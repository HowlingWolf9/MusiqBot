import discord
from music.models import Song
from music.utils import delete_message_safe


class SearchSelect(discord.ui.Select):
    def __init__(self, entries, cog):
        self.entries = entries
        self.cog = cog
        options = []
        for i, entry in enumerate(entries):
            if not isinstance(entry, dict):
                continue
            title = entry.get("title") or "Unknown Title"
            if len(title) > 90:
                title = title[:90] + "..."
            duration = entry.get("duration")
            if duration:
                mins, secs = divmod(duration, 60)
                desc = f"{mins}:{secs:02d}"
            else:
                desc = "Unknown duration"
            options.append(
                discord.SelectOption(
                    label=f"{i + 1}. {title}", description=desc, value=str(i)
                )
            )

        super().__init__(
            placeholder="Choose a song to play...",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        if not interaction.user.voice:
            return await interaction.response.send_message(
                "🔴 You need to join a voice channel first!", ephemeral=True
            )

        await interaction.response.defer(ephemeral=True)

        voice_client = await self.cog.ensure_voice_client(interaction)
        if not voice_client:
            try:
                return await interaction.followup.send(
                    "❌ Failed to connect to voice channel.", ephemeral=True
                )
            except Exception:
                return

        player = self.cog.get_player(interaction)
        player.channel = interaction.channel

        is_busy = player.is_busy
        player.is_loading = True

        try:
            index = int(self.values[0])
            entry = self.entries[index]

            song = Song(entry, interaction.user)

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

        if self.view and getattr(self.view, "message", None):
            await delete_message_safe(self.view.message)


class SearchView(discord.ui.View):
    def __init__(self, entries, user, cog):
        super().__init__(timeout=300)
        self.user = user
        self.add_item(SearchSelect(entries, cog))
        self.message = None

    async def on_timeout(self):
        if self.message:
            await delete_message_safe(self.message)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user != self.user:
            await interaction.response.send_message(
                "This menu is not for you.", ephemeral=True
            )
            return False
        return True
