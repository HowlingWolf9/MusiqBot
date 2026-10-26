import discord

from music.models import LoopMode
from music.permissions import check_dj_permission


class PlayerView(discord.ui.View):
    def __init__(self, cog, player):
        super().__init__(timeout=None)
        self.cog = cog
        self.player = player
        self.update_buttons()

    def update_buttons(self):
        self.update_loop_button()
        for child in self.children:
            if getattr(child, "custom_id", None) == "autoplay_button":
                if self.player.autoplay:
                    child.style = discord.ButtonStyle.success
                else:
                    child.style = discord.ButtonStyle.secondary
            elif getattr(child, "emoji", None) and str(child.emoji) == "⏯️":
                if self.player.paused_at is not None:
                    child.style = discord.ButtonStyle.secondary
                else:
                    child.style = discord.ButtonStyle.primary

    def update_loop_button(self):
        for child in self.children:
            if getattr(child, "custom_id", None) == "loop_button":
                if self.player.loop_mode == LoopMode.OFF:
                    child.style = discord.ButtonStyle.secondary
                    child.label = "Loop Off"
                elif self.player.loop_mode == LoopMode.TRACK:
                    child.style = discord.ButtonStyle.success
                    child.label = "Loop Track"
                elif self.player.loop_mode == LoopMode.QUEUE:
                    child.style = discord.ButtonStyle.primary
                    child.label = "Loop Queue"

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not interaction.user.voice:
            await interaction.response.send_message(
                "🔴 You need to join a voice channel first!", ephemeral=True
            )
            return False
        if (
            interaction.guild.voice_client
            and interaction.guild.voice_client.channel != interaction.user.voice.channel
        ):
            await interaction.response.send_message(
                "🔴 You must be in the same voice channel as the bot.", ephemeral=True
            )
            return False
        return True

    @discord.ui.button(style=discord.ButtonStyle.primary, emoji="⏯️")
    async def pause_resume(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        if not await check_dj_permission(
            interaction,
            "❌ You need the 'DJ' role or Manage Server permissions to do this.",
        ):
            return
        vc = interaction.guild.voice_client
        if not vc:
            return await interaction.response.send_message(
                "Nothing is playing.", ephemeral=True
            )
        if vc.is_playing():
            vc.pause()
            self.player.mark_paused()
            self.update_buttons()
            await interaction.response.send_message(
                "⏸️ Paused the music."
            )
            if self.player.np_msg:
                try:
                    await self.player.np_msg.edit(
                        embed=self.player.build_np_embed(), view=self
                    )
                except Exception:
                    pass
        elif vc.is_paused():
            vc.resume()
            self.player.mark_resumed()
            self.update_buttons()
            await interaction.response.send_message(
                "▶️ Resumed the music."
            )
            if self.player.np_msg:
                try:
                    await self.player.np_msg.edit(
                        embed=self.player.build_np_embed(), view=self
                    )
                except Exception:
                    pass
        else:
            await interaction.response.send_message(
                "Nothing is playing.", ephemeral=True
            )

    @discord.ui.button(style=discord.ButtonStyle.secondary, emoji="⏭️")
    async def skip(self, interaction: discord.Interaction, button: discord.ui.Button):
        # DJs skip instantly; other listeners in the same voice channel
        # register a vote (shared logic with the /skip command).
        await self.cog.handle_skip_request(interaction)

    @discord.ui.button(style=discord.ButtonStyle.danger, emoji="⏹️")
    async def stop(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await check_dj_permission(
            interaction,
            "❌ You need the 'DJ' role or Manage Server permissions to do this.",
        ):
            return
        await self.player.stop_playback()
        await interaction.response.send_message(
            "⏹️ Stopped music and cleared the queue."
        )

    @discord.ui.button(
        style=discord.ButtonStyle.secondary, emoji="🔁", custom_id="loop_button"
    )
    async def loop_toggle(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        if not await check_dj_permission(
            interaction,
            "❌ You need the 'DJ' role or Manage Server permissions to do this.",
        ):
            return

        if self.player.loop_mode == LoopMode.OFF:
            self.player.loop_mode = LoopMode.TRACK
        elif self.player.loop_mode == LoopMode.TRACK:
            self.player.loop_mode = LoopMode.QUEUE
        else:
            self.player.loop_mode = LoopMode.OFF

        self.update_buttons()
        try:
            await interaction.response.edit_message(
                embed=self.player.build_np_embed(), view=self
            )
        except Exception:
            await interaction.response.send_message(
                f"🔁 Loop mode set to **{self.player.loop_mode.value.capitalize()}**."
            )

    @discord.ui.button(style=discord.ButtonStyle.secondary, emoji="🔀")
    async def shuffle_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        if not await check_dj_permission(
            interaction,
            "❌ You need the 'DJ' role or Manage Server permissions to do this.",
        ):
            return
        if self.player.queue.empty():
            return await interaction.response.send_message(
                "The queue is empty.", ephemeral=True
            )
        self.player.shuffle()
        await interaction.response.send_message(
            "🔀 Shuffled the queue."
        )

    @discord.ui.button(
        style=discord.ButtonStyle.secondary, emoji="📻", row=1, custom_id="autoplay_button"
    )
    async def autoplay_toggle(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        if not await check_dj_permission(
            interaction,
            "❌ You need the 'DJ' role or Manage Server permissions to do this.",
        ):
            return
        self.player.autoplay = not self.player.autoplay
        guild_id = str(interaction.guild.id)
        if guild_id not in self.cog.settings:
            self.cog.settings[guild_id] = {}
        self.cog.settings[guild_id]["autoplay"] = self.player.autoplay
        await self.cog.save_settings_async()

        if not self.player.autoplay:
            if self.player._prefetch_task and not self.player._prefetch_task.done():
                self.player._prefetch_task.cancel()
            self.player._prefetch_task = None
            self.player._prefetching = False
            self.player.remove_autoplay_songs()
        else:
            self.player.stopped_by_user = False
            has_auto = any(
                getattr(s, "is_autoplay", False) for s in self.player.queue._queue
            )
            if not has_auto and (self.player.current or self.player.history):
                self.player._prefetch_task = self.cog.spawn_background(
                    self.player.prefetch_autoplay()
                )

        self.update_buttons()
        status = "enabled" if self.player.autoplay else "disabled"
        try:
            await interaction.response.edit_message(
                embed=self.player.build_np_embed(), view=self
            )
        except Exception:
            await interaction.response.send_message(
                f"📻 Autoplay is now **{status}**."
            )

    @discord.ui.button(style=discord.ButtonStyle.secondary, emoji="🔄", row=1)
    async def replay_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        if not await check_dj_permission(
            interaction,
            "❌ You need the 'DJ' role or Manage Server permissions to do this.",
        ):
            return
        if not self.player.current or not interaction.guild.voice_client:
            return await interaction.response.send_message(
                "Nothing is playing to replay.", ephemeral=True
            )
        self.player.seek_position = 0
        self.player.is_seeking = True
        self.player.paused_at = None
        self.player.total_paused = 0.0
        interaction.guild.voice_client.stop()
        await interaction.response.send_message("🔄 Restarting the current song.")
