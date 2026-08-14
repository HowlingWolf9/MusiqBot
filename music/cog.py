import asyncio
import datetime
import functools
import json
import logging
import os
import aiohttp
import discord
from discord import app_commands
from discord.ext import commands
import yt_dlp

from music.audio import YTDLSource
from music.config import ytdl
from music.models import LoopMode, Song
from music.permissions import check_dj_permission
from music.player import MusicPlayer
from music.services.autocomplete import fetch_song_autocomplete
from music.services.spotify import resolve_spotify as resolve_spotify_service
from music.views.player_view import PlayerView
from music.views.queue_view import QueueView
from music.views.search_view import SearchView

logger = logging.getLogger(__name__)


class MusicCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.players = {}
        self.settings_file = "music_settings.json"
        self.settings = self.load_settings()
        self.empty_channel_timers = {}
        self.voice_connect_locks = {}
        self.autocomplete_cache = {}
        self.session = None

    async def get_session(self) -> aiohttp.ClientSession:
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession()
        return self.session

    async def cog_unload(self):
        if self.session and not self.session.closed:
            await self.session.close()

    async def cog_app_command_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ):
        if isinstance(error, app_commands.MissingPermissions):
            perms = ", ".join(error.missing_permissions)
            msg = f"❌ You need the following permission(s) to use this command: `{perms}`"
            if not interaction.response.is_done():
                await interaction.response.send_message(msg, ephemeral=True)
            else:
                await interaction.followup.send(msg, ephemeral=True)
            return
        if isinstance(error, app_commands.BotMissingPermissions):
            perms = ", ".join(error.missing_permissions)
            msg = f"❌ I need the following permission(s) to perform this action: `{perms}`"
            if not interaction.response.is_done():
                await interaction.response.send_message(msg, ephemeral=True)
            else:
                await interaction.followup.send(msg, ephemeral=True)
            return
        logger.error(f"App command error in {interaction.command}: {error}", exc_info=True)

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        if member == self.bot.user:
            if after.channel is None:
                if member.guild.id in self.players:
                    await self.cleanup(member.guild)
                return
            else:
                # Bot moved to another voice channel
                voice_client = member.guild.voice_client
                if voice_client and voice_client.channel:
                    non_bot_members = [m for m in voice_client.channel.members if not m.bot]
                    if len(non_bot_members) == 0:
                        if member.guild.id in self.empty_channel_timers:
                            self.empty_channel_timers[member.guild.id].cancel()

                        async def disconnect_after_timeout():
                            try:
                                await asyncio.sleep(300)
                                vc = member.guild.voice_client
                                if vc and vc.channel:
                                    members = [m for m in vc.channel.members if not m.bot]
                                    if len(members) == 0:
                                        await self.cleanup(member.guild)
                            except asyncio.CancelledError:
                                pass
                            finally:
                                self.empty_channel_timers.pop(member.guild.id, None)

                        self.empty_channel_timers[member.guild.id] = (
                            self.bot.loop.create_task(disconnect_after_timeout())
                        )
                return

        if member.bot:
            return

        voice_client = member.guild.voice_client
        if not voice_client:
            return

        if after.channel == voice_client.channel:
            if member.guild.id in self.empty_channel_timers:
                self.empty_channel_timers[member.guild.id].cancel()
                del self.empty_channel_timers[member.guild.id]

        if (
            before.channel == voice_client.channel
            and after.channel != voice_client.channel
        ):
            non_bot_members = [m for m in voice_client.channel.members if not m.bot]
            if len(non_bot_members) == 0:
                if member.guild.id in self.empty_channel_timers:
                    self.empty_channel_timers[member.guild.id].cancel()

                async def disconnect_after_timeout():
                    try:
                        await asyncio.sleep(300)
                        vc = member.guild.voice_client
                        if vc and vc.channel:
                            members = [m for m in vc.channel.members if not m.bot]
                            if len(members) == 0:
                                await self.cleanup(member.guild)
                    except asyncio.CancelledError:
                        pass
                    finally:
                        self.empty_channel_timers.pop(member.guild.id, None)

                self.empty_channel_timers[member.guild.id] = (
                    self.bot.loop.create_task(disconnect_after_timeout())
                )

    def load_settings(self):
        if os.path.exists(self.settings_file):
            try:
                with open(self.settings_file, "r") as f:
                    return json.load(f)
            except Exception:
                return {}
        return {}

    def save_settings(self):
        try:
            temp_file = f"{self.settings_file}.tmp"
            with open(temp_file, "w") as f:
                json.dump(self.settings, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temp_file, self.settings_file)
        except Exception as e:
            logger.error(f"Failed to save settings: {e}", exc_info=True)

    async def save_settings_async(self):
        try:
            loop = self.bot.loop or asyncio.get_running_loop()
        except RuntimeError:
            loop = self.bot.loop or asyncio.get_event_loop()
        await loop.run_in_executor(None, functools.partial(self.save_settings))

    def get_player(self, interaction):
        try:
            player = self.players[interaction.guild.id]
        except KeyError:
            player = MusicPlayer(interaction, self)
            guild_id = str(interaction.guild.id)
            if guild_id in self.settings:
                player.autoplay = self.settings[guild_id].get("autoplay", False)
                player.volume = self.settings[guild_id].get("volume", 0.5)
            self.players[interaction.guild.id] = player
        return player

    async def cleanup(self, guild):
        try:
            if guild.id in self.empty_channel_timers:
                self.empty_channel_timers[guild.id].cancel()
                del self.empty_channel_timers[guild.id]
        except Exception:
            pass
        self.voice_connect_locks.pop(guild.id, None)
        try:
            await guild.voice_client.disconnect()
        except AttributeError:
            pass
        try:
            player = self.players.pop(guild.id)
            await player.delete_message_safe(player.np_msg)
            player.np_msg = None
            await player.delete_message_safe(player.queue_msg)
            player.queue_msg = None
            player.clear_queue()
            player.player_task.cancel()
        except KeyError:
            pass

    async def ensure_voice_client(
        self, interaction: discord.Interaction
    ) -> discord.VoiceClient | None:
        if not interaction.guild or not getattr(interaction.user, "voice", None):
            return None

        guild_id = interaction.guild.id
        if guild_id not in self.voice_connect_locks:
            self.voice_connect_locks[guild_id] = asyncio.Lock()

        async with self.voice_connect_locks[guild_id]:
            target_channel = interaction.user.voice.channel
            vc = interaction.guild.voice_client

            if vc is not None:
                if vc.channel == target_channel and vc.is_connected():
                    return vc

                if vc.is_connected():
                    try:
                        await vc.move_to(target_channel)
                        return vc
                    except Exception as e:
                        logger.error(f"Error moving voice channel: {e}", exc_info=True)
                        return None
                else:
                    try:
                        await vc.disconnect(force=True)
                    except Exception:
                        pass

            try:
                return await target_channel.connect(timeout=15, reconnect=True)
            except Exception as e:
                logger.error(f"Error connecting to voice channel: {e}", exc_info=True)
                return None

    async def verify_voice(self, interaction: discord.Interaction) -> bool:
        if not interaction.guild:
            await interaction.response.send_message(
                "🔴 This command can only be used in a server.", ephemeral=True
            )
            return False
        if not getattr(interaction.user, "voice", None):
            await interaction.response.send_message(
                "🔴 You need to join a voice channel first!", ephemeral=True
            )
            return False
        if (
            interaction.guild.voice_client
            and interaction.guild.voice_client.channel != interaction.user.voice.channel
        ):
            await interaction.response.send_message(
                "🔴 You must be in the same voice channel as the bot to use this.",
                ephemeral=True,
            )
            return False
        return True

    async def song_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        session = await self.get_session()
        return await fetch_song_autocomplete(
            interaction, current, session, self.autocomplete_cache
        )

    async def resolve_spotify(self, url: str) -> str | None:
        session = await self.get_session()
        return await resolve_spotify_service(session, url)

    async def process_playlist(
        self, interaction: discord.Interaction, url: str, player: MusicPlayer
    ):
        ydl_opts = {
            "extract_flat": "in_playlist",
            "quiet": True,
            "skip_download": True,
        }
        loop = self.bot.loop or asyncio.get_event_loop()

        def extract():
            with yt_dlp.YoutubeDL(ydl_opts) as ydl_inst:
                return ydl_inst.extract_info(url, download=False)

        try:
            data = await loop.run_in_executor(None, extract)
        except Exception as e:
            try:
                return await interaction.followup.send(
                    f"❌ Failed to extract playlist: {e}", ephemeral=True
                )
            except Exception:
                return

        entries = data.get("entries") or []
        if not entries:
            try:
                return await interaction.followup.send(
                    "❌ Playlist is empty or unavailable.", ephemeral=True
                )
            except Exception:
                return

        count = 0
        for entry in entries[:100]:
            if not isinstance(entry, dict):
                continue
            title = entry.get("title") or "Unknown Track"
            webpage_url = (
                entry.get("url")
                or entry.get("webpage_url")
                or (f"https://www.youtube.com/watch?v={entry.get('id')}" if entry.get("id") else None)
            )
            if not webpage_url:
                continue
            duration = entry.get("duration")
            thumbnail = entry.get("thumbnail")
            entry_data = {
                "title": title,
                "webpage_url": webpage_url,
                "duration": duration,
                "thumbnail": thumbnail,
            }
            song = Song(entry_data, interaction.user)
            await player.queue.put(song)
            count += 1

        try:
            await interaction.followup.send(
                f"🎶 Added **{count}** tracks from playlist to queue!", ephemeral=True
            )
        except Exception:
            pass

    @app_commands.command(
        name="play", description="Play a song, playlist, or add it to the queue"
    )
    @app_commands.autocomplete(url=song_autocomplete)
    async def play(self, interaction: discord.Interaction, url: str):
        if not await self.verify_voice(interaction):
            return

        try:
            await interaction.response.defer(ephemeral=True)
        except Exception:
            pass

        voice_client = await self.ensure_voice_client(interaction)
        if not voice_client:
            try:
                return await interaction.followup.send(
                    "❌ Failed to connect to voice channel.",
                    ephemeral=True,
                )
            except Exception:
                return

        player = self.get_player(interaction)
        player.channel = interaction.channel

        is_busy = player.is_busy
        player.is_loading = True

        try:
            if "spotify.com" in url:
                resolved_url = await self.resolve_spotify(url)
                if resolved_url:
                    url = resolved_url
                else:
                    try:
                        return await interaction.followup.send(
                            "❌ Could not resolve Spotify track metadata.",
                            ephemeral=True,
                        )
                    except Exception:
                        return

            if "list=" in url and ("youtube.com" in url or "music.youtube.com" in url):
                return await self.process_playlist(interaction, url, player)

            try:
                data = await YTDLSource.extract_info(url, loop=self.bot.loop)
            except Exception as e:
                try:
                    return await interaction.followup.send(
                        f"❌ An error occurred: {str(e)}", ephemeral=True
                    )
                except Exception:
                    return

            song = Song(data, interaction.user)

            if is_busy:
                embed = discord.Embed(
                    title="Added to Queue",
                    description=f"[{song.title}]({song.url})",
                    color=discord.Color.blue(),
                )
                embed.set_footer(
                    text=f"Added by {song.requester.display_name}",
                    icon_url=song.requester.display_avatar.url,
                )
                msg = await interaction.channel.send(embed=embed)
                song.added_msg = msg
                try:
                    await interaction.followup.send(
                        f"✅ Added **{song.title}** to the queue.", ephemeral=True
                    )
                except Exception:
                    pass
            else:
                msg = await interaction.channel.send(
                    f"⏳ Loading track: **{song.title}**..."
                )
                song.added_msg = msg
                try:
                    await interaction.followup.send(
                        f"⏳ Loading track: **{song.title}**...", ephemeral=True
                    )
                except Exception:
                    pass

            await player.queue.put(song)
        finally:
            player.is_loading = False

    @app_commands.command(
        name="search", description="Search YouTube Music and choose from results"
    )
    @app_commands.autocomplete(query=song_autocomplete)
    async def search(self, interaction: discord.Interaction, query: str):
        if not await self.verify_voice(interaction):
            return

        try:
            await interaction.response.defer(ephemeral=True)
        except Exception:
            pass

        voice_client = await self.ensure_voice_client(interaction)
        if not voice_client:
            try:
                return await interaction.followup.send(
                    "❌ Failed to connect to voice channel.", ephemeral=True
                )
            except Exception:
                return

        try:
            url = f"ytsearch5:{query}"
            loop = self.bot.loop or asyncio.get_event_loop()
            data = await loop.run_in_executor(
                None, lambda: ytdl.extract_info(url, download=False)
            )
        except Exception as e:
            return await interaction.followup.send(
                f"❌ An error occurred: {str(e)}", ephemeral=True
            )

        if "entries" not in data or not data["entries"]:
            return await interaction.followup.send(
                "❌ No results found.", ephemeral=True
            )

        entries = data["entries"][:5]
        view = SearchView(entries, interaction.user, self)

        self.get_player(interaction)

        msg = await interaction.followup.send(
            "🔎 **Select a song to play:**", view=view, ephemeral=True
        )
        view.message = msg

    @app_commands.command(name="skip", description="Skip the currently playing song")
    async def skip(self, interaction: discord.Interaction):
        if not await self.verify_voice(interaction):
            return
        if not await check_dj_permission(
            interaction,
            "❌ You need the 'DJ' role or Manage Server permissions to use this command.",
        ):
            return

        voice_client = interaction.guild.voice_client
        if not voice_client or (
            not voice_client.is_playing() and not voice_client.is_paused()
        ):
            return await interaction.response.send_message(
                "There is nothing playing to skip.", ephemeral=True
            )

        player = self.get_player(interaction)
        player.manual_skip = True
        voice_client.stop()
        await interaction.response.send_message(
            "⏭️ Skipped the current song.", ephemeral=True
        )

    @app_commands.command(name="stop", description="Stop the music and clear the queue")
    async def stop(self, interaction: discord.Interaction):
        if not await self.verify_voice(interaction):
            return
        if not await check_dj_permission(
            interaction,
            "❌ You need the 'DJ' role or Manage Server permissions to use this command.",
        ):
            return

        player = self.get_player(interaction)
        player.manual_stop = True
        player.clear_queue()
        player.history.clear()

        voice_client = interaction.guild.voice_client
        if voice_client and (voice_client.is_playing() or voice_client.is_paused()):
            voice_client.stop()

        await interaction.response.send_message(
            "⏹️ Stopped the music and cleared the queue.", ephemeral=True
        )

    @app_commands.command(name="pause", description="Pause the music")
    async def pause(self, interaction: discord.Interaction):
        if not await self.verify_voice(interaction):
            return
        if not await check_dj_permission(
            interaction,
            "❌ You need the 'DJ' role or Manage Server permissions to use this command.",
        ):
            return

        if (
            interaction.guild.voice_client
            and interaction.guild.voice_client.is_playing()
        ):
            interaction.guild.voice_client.pause()
            await interaction.response.send_message(
                "⏸️ Paused the music.", ephemeral=True
            )
        else:
            await interaction.response.send_message(
                "Nothing is currently playing.", ephemeral=True
            )

    @app_commands.command(name="resume", description="Resume the music")
    async def resume(self, interaction: discord.Interaction):
        if not await self.verify_voice(interaction):
            return
        if not await check_dj_permission(
            interaction,
            "❌ You need the 'DJ' role or Manage Server permissions to use this command.",
        ):
            return

        if (
            interaction.guild.voice_client
            and interaction.guild.voice_client.is_paused()
        ):
            interaction.guild.voice_client.resume()
            await interaction.response.send_message(
                "▶️ Resumed the music.", ephemeral=True
            )
        else:
            await interaction.response.send_message(
                "The music is not paused.", ephemeral=True
            )

    @app_commands.command(name="queue", description="Show the current music queue")
    async def queue(self, interaction: discord.Interaction):
        if not interaction.guild:
            return await interaction.response.send_message(
                "This command can only be used in a server.", ephemeral=True
            )
        player = self.get_player(interaction)

        await player.delete_message_safe(player.queue_msg)
        view = QueueView(player, interaction.user)
        embed = view.build_embed()

        await interaction.response.defer(ephemeral=True)
        msg = await interaction.channel.send(embed=embed, view=view)
        view.message = msg
        player.queue_msg = msg
        await interaction.followup.send("🎶 Queue updated below.", ephemeral=True)

    @app_commands.command(
        name="nowplaying", description="Show the currently playing song"
    )
    async def nowplaying(self, interaction: discord.Interaction):
        if not interaction.guild:
            return await interaction.response.send_message(
                "This command can only be used in a server.", ephemeral=True
            )
        player = self.players.get(interaction.guild.id)
        if not player or not player.current:
            return await interaction.response.send_message(
                "There is no music playing right now.", ephemeral=True
            )

        await player.delete_message_safe(player.np_msg)
        embed = player.build_np_embed()
        view = PlayerView(self, player)
        player.np_msg = await interaction.channel.send(embed=embed, view=view)
        await interaction.response.send_message(
            "🎶 Displaying Now Playing.", ephemeral=True
        )

    @app_commands.command(
        name="volume", description="Set the volume of the bot (1-100)"
    )
    async def volume(self, interaction: discord.Interaction, vol: int):
        if not await self.verify_voice(interaction):
            return
        if not await check_dj_permission(
            interaction,
            "❌ You need the 'DJ' role or Manage Server permissions to use this command.",
        ):
            return

        if not 1 <= vol <= 100:
            return await interaction.response.send_message(
                "Please enter a value between 1 and 100.", ephemeral=True
            )

        player = self.get_player(interaction)
        new_vol = vol / 100.0
        if interaction.guild.voice_client and getattr(
            interaction.guild.voice_client, "source", None
        ):
            interaction.guild.voice_client.source.volume = new_vol

        player.volume = new_vol

        guild_id = str(interaction.guild.id)
        if guild_id not in self.settings:
            self.settings[guild_id] = {}
        self.settings[guild_id]["volume"] = new_vol
        await self.save_settings_async()

        await interaction.response.send_message(
            f"🔊 Changed volume to {vol}%", ephemeral=True
        )

    @app_commands.command(
        name="leave", description="Clear the queue and leave the voice channel"
    )
    async def leave(self, interaction: discord.Interaction):
        if not await self.verify_voice(interaction):
            return
        if not await check_dj_permission(
            interaction,
            "❌ You need the 'DJ' role or Manage Server permissions to use this command.",
        ):
            return

        if interaction.guild.voice_client:
            await self.cleanup(interaction.guild)
            await interaction.response.send_message(
                "🛑 Cleared the queue and disconnected.", ephemeral=True
            )
        else:
            await interaction.response.send_message(
                "I am not connected to a voice channel.", ephemeral=True
            )

    @app_commands.command(
        name="autoplay",
        description="Toggle autoplay (automatically queues related songs when empty)",
    )
    async def autoplay(self, interaction: discord.Interaction):
        if not await self.verify_voice(interaction):
            return
        if not await check_dj_permission(
            interaction,
            "❌ You need the 'DJ' role or Manage Server permissions to use this command.",
        ):
            return

        player = self.get_player(interaction)
        player.autoplay = not player.autoplay

        guild_id = str(interaction.guild.id)
        if guild_id not in self.settings:
            self.settings[guild_id] = {}
        self.settings[guild_id]["autoplay"] = player.autoplay
        await self.save_settings_async()

        status = "enabled" if player.autoplay else "disabled"
        await interaction.response.send_message(
            f"📻 Autoplay is now **{status}**.", ephemeral=True
        )

        if player.autoplay and player.queue.empty() and player.history:
            if not player.current:
                self.bot.loop.call_soon_threadsafe(player.next.set)
            else:
                self.bot.loop.create_task(player.prefetch_autoplay())

    @app_commands.command(name="loop", description="Set loop mode (off, track, queue)")
    @app_commands.choices(
        mode=[
            app_commands.Choice(name="Off", value="off"),
            app_commands.Choice(name="Track", value="track"),
            app_commands.Choice(name="Queue", value="queue"),
        ]
    )
    async def loop(
        self, interaction: discord.Interaction, mode: app_commands.Choice[str]
    ):
        if not await self.verify_voice(interaction):
            return
        if not await check_dj_permission(
            interaction,
            "❌ You need the 'DJ' role or Manage Server permissions to use this command.",
        ):
            return

        player = self.get_player(interaction)
        player.loop_mode = LoopMode(mode.value)
        if player.np_msg and player.current:
            try:
                view = PlayerView(self, player)
                await player.np_msg.edit(embed=player.build_np_embed(), view=view)
            except Exception:
                pass
        await interaction.response.send_message(
            f"🔁 Loop mode set to **{mode.name}**.", ephemeral=True
        )

    @app_commands.command(name="shuffle", description="Shuffle the current queue")
    async def shuffle(self, interaction: discord.Interaction):
        if not await self.verify_voice(interaction):
            return
        if not await check_dj_permission(
            interaction,
            "❌ You need the 'DJ' role or Manage Server permissions to use this command.",
        ):
            return

        player = self.get_player(interaction)
        if player.queue.empty():
            return await interaction.response.send_message(
                "The queue is empty.", ephemeral=True
            )

        player.shuffle()
        await interaction.response.send_message(
            "🔀 Shuffled the queue.", ephemeral=True
        )

    @app_commands.command(
        name="remove", description="Remove a song from the queue by its index"
    )
    async def remove(self, interaction: discord.Interaction, index: int):
        if not await self.verify_voice(interaction):
            return
        if not await check_dj_permission(
            interaction,
            "❌ You need the 'DJ' role or Manage Server permissions to use this command.",
        ):
            return

        player = self.get_player(interaction)
        try:
            song = player.remove(index - 1)
            await interaction.response.send_message(
                f"🗑️ Removed **{song.title}** from the queue.", ephemeral=True
            )
        except IndexError:
            await interaction.response.send_message(
                "❌ Invalid queue index. Check the queue for valid numbers.",
                ephemeral=True,
            )

    @app_commands.command(
        name="clear", description="Clear all upcoming tracks from the queue"
    )
    async def clear(self, interaction: discord.Interaction):
        if not await self.verify_voice(interaction):
            return
        if not await check_dj_permission(
            interaction,
            "❌ You need the 'DJ' role or Manage Server permissions to use this command.",
        ):
            return

        player = self.get_player(interaction)
        player.clear_queue()
        await interaction.response.send_message(
            "🧹 Cleared the queue.", ephemeral=True
        )

    @app_commands.command(
        name="move", description="Move a track from one position in queue to another"
    )
    async def move(
        self, interaction: discord.Interaction, from_index: int, to_index: int
    ):
        if not await self.verify_voice(interaction):
            return
        if not await check_dj_permission(
            interaction,
            "❌ You need the 'DJ' role or Manage Server permissions to use this command.",
        ):
            return

        player = self.get_player(interaction)
        try:
            song = player.move(from_index - 1, to_index - 1)
            await interaction.response.send_message(
                f"↕️ Moved **{song.title}** to position {to_index}.", ephemeral=True
            )
        except IndexError:
            await interaction.response.send_message(
                "❌ Invalid queue index positions.", ephemeral=True
            )

    @app_commands.command(
        name="seek", description="Seek to a timestamp (e.g. 1:30 or 90 seconds)"
    )
    async def seek(self, interaction: discord.Interaction, timestamp: str):
        if not await self.verify_voice(interaction):
            return
        if not await check_dj_permission(
            interaction,
            "❌ You need the 'DJ' role or Manage Server permissions to use this command.",
        ):
            return

        player = self.get_player(interaction)
        if not player.current or not interaction.guild.voice_client:
            return await interaction.response.send_message(
                "Nothing is currently playing to seek.", ephemeral=True
            )

        seconds = 0
        parts = timestamp.split(":")
        try:
            if len(parts) == 1:
                seconds = int(parts[0])
            elif len(parts) == 2:
                seconds = int(parts[0]) * 60 + int(parts[1])
            elif len(parts) == 3:
                seconds = int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
            else:
                raise ValueError
            if seconds < 0 or any(int(p) < 0 for p in parts):
                raise ValueError
        except ValueError:
            return await interaction.response.send_message(
                "❌ Invalid timestamp format. Use `mm:ss` or seconds.", ephemeral=True
            )

        try:
            player.seek_position = seconds
            player.is_seeking = True
            interaction.guild.voice_client.stop()
            await interaction.response.send_message(
                f"⏩ Seeked to `{timestamp}`.", ephemeral=True
            )
        except Exception as e:
            player.seek_position = None
            player.is_seeking = False
            await interaction.response.send_message(
                f"❌ Failed to seek: {e}", ephemeral=True
            )

    @app_commands.command(
        name="replay", description="Restart playback of the current song"
    )
    async def replay(self, interaction: discord.Interaction):
        if not await self.verify_voice(interaction):
            return
        if not await check_dj_permission(
            interaction,
            "❌ You need the 'DJ' role or Manage Server permissions to use this command.",
        ):
            return

        player = self.get_player(interaction)
        if not player.current or not interaction.guild.voice_client:
            return await interaction.response.send_message(
                "Nothing is currently playing to replay.", ephemeral=True
            )

        try:
            player.seek_position = 0
            player.is_seeking = True
            interaction.guild.voice_client.stop()
            await interaction.response.send_message(
                "🔄 Replaying current song.", ephemeral=True
            )
        except Exception as e:
            player.seek_position = None
            player.is_seeking = False
            await interaction.response.send_message(
                f"❌ Failed to replay: {e}", ephemeral=True
            )

    @app_commands.command(
        name="setdjrole", description="Set a custom DJ role for this server"
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def setdjrole(self, interaction: discord.Interaction, role: discord.Role):
        if not interaction.guild:
            return await interaction.response.send_message(
                "This command can only be used in a server.", ephemeral=True
            )

        guild_id = str(interaction.guild.id)
        if guild_id not in self.settings:
            self.settings[guild_id] = {}
        self.settings[guild_id]["dj_role_id"] = role.id
        self.settings[guild_id]["dj_role_name"] = role.name
        await self.save_settings_async()

        await interaction.response.send_message(
            f"🎧 Set DJ role to **{role.name}**.", ephemeral=True
        )

    @app_commands.command(
        name="settings", description="View active server settings for MuhazBot"
    )
    async def settings_cmd(self, interaction: discord.Interaction):
        if not interaction.guild:
            return await interaction.response.send_message(
                "This command can only be used in a server.", ephemeral=True
            )

        player = self.get_player(interaction)
        guild_id = str(interaction.guild.id)
        guild_settings = self.settings.get(guild_id, {})

        dj_role = (
            f"<@&{guild_settings.get('dj_role_id')}>"
            if guild_settings.get("dj_role_id")
            else "Role named 'DJ' or Manage Server"
        )
        vol_pct = int(player.volume * 100)
        autoplay_str = "Enabled" if player.autoplay else "Disabled"
        loop_str = player.loop_mode.value.capitalize()

        embed = discord.Embed(
            title=f"⚙️ Settings for {interaction.guild.name}",
            color=discord.Color.blue(),
        )
        embed.add_field(name="Volume", value=f"`{vol_pct}%`", inline=True)
        embed.add_field(name="Autoplay", value=f"`{autoplay_str}`", inline=True)
        embed.add_field(name="Loop Mode", value=f"`{loop_str}`", inline=True)
        embed.add_field(name="DJ Role", value=dj_role, inline=False)

        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(
        name="clear_messages",
        description="Delete the bot's past messages in the channel based on a time scale.",
    )
    @app_commands.describe(
        amount="Maximum number of messages to check/delete (default: 100)",
        minutes="Delete messages from the last X minutes",
        hours="Delete messages from the last X hours",
        days="Delete messages from the last X days",
    )
    async def clear_messages(
        self,
        interaction: discord.Interaction,
        amount: int = 100,
        minutes: int = 0,
        hours: int = 0,
        days: int = 0,
    ):
        if not getattr(interaction.user, "guild_permissions", None) or not interaction.user.guild_permissions.manage_messages:
            return await interaction.response.send_message(
                "❌ You need 'Manage Messages' permission to use this command.",
                ephemeral=True,
            )

        if minutes == 0 and hours == 0 and days == 0:
            return await interaction.response.send_message(
                "❌ Please specify a time scale (minutes, hours, or days) to clear.",
                ephemeral=True,
            )

        await interaction.response.defer(ephemeral=True)

        try:
            delta = datetime.timedelta(minutes=minutes, hours=hours, days=days)
            after_time = discord.utils.utcnow() - delta

            def is_me(m):
                return m.author == self.bot.user

            can_manage = (
                getattr(interaction.channel.permissions_for(interaction.guild.me), "manage_messages", False)
                if interaction.guild and interaction.guild.me
                else False
            )
            # Discord API rejects bulk deletion of messages older than 14 days with HTTP 400
            can_bulk = can_manage and delta.days < 14

            deleted = await interaction.channel.purge(limit=amount, after=after_time, check=is_me, bulk=can_bulk)

            time_str = []
            if days:
                time_str.append(f"{days} days")
            if hours:
                time_str.append(f"{hours} hours")
            if minutes:
                time_str.append(f"{minutes} minutes")

            await interaction.followup.send(
                f"✅ Successfully deleted **{len(deleted)}** of my messages from the last {' '.join(time_str)}.",
                ephemeral=True
            )
        except discord.Forbidden:
            await interaction.followup.send(
                "❌ I don't have permission to delete messages here.", ephemeral=True
            )
        except discord.HTTPException as e:
            await interaction.followup.send(
                f"❌ Failed to delete messages: {e}", ephemeral=True
            )


async def setup(bot):
    await bot.add_cog(MusicCog(bot))
