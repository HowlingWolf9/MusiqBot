import discord


class QueueView(discord.ui.View):
    def __init__(self, player, user):
        super().__init__(timeout=180)
        self.player = player
        self.user = user
        self.current_page = 0
        self.per_page = 10
        self.message = None
        self.update_buttons()

    def get_max_pages(self):
        items = self.player.get_queue_items()
        return max(1, (len(items) + self.per_page - 1) // self.per_page)

    def update_buttons(self):
        max_pages = self.get_max_pages()
        self.children[0].disabled = self.current_page == 0
        self.children[1].disabled = self.current_page >= max_pages - 1

    def build_embed(self) -> discord.Embed:
        items = self.player.get_queue_items()
        max_pages = self.get_max_pages()
        if self.current_page >= max_pages:
            self.current_page = max(0, max_pages - 1)
        if self.current_page < 0:
            self.current_page = 0
        start = self.current_page * self.per_page
        end = start + self.per_page
        page_items = items[start:end]

        embed = discord.Embed(
            title=f"🎶 Queue for {self.player.guild.name}", color=discord.Color.blue()
        )

        if self.player.current:
            cur_req = self.player.current.requester.display_name
            if getattr(self.player.current, "is_autoplay", False):
                cur_req += " (Autoplay)"
            embed.add_field(
                name="Currently Playing",
                value=f"[{self.player.current.title}]({self.player.current.url}) | `{cur_req}`",
                inline=False,
            )

        if not items:
            embed.description = "The queue is currently empty."
        else:
            fmt = "\n".join(
                f"`{start + i + 1}.` **[{song.title}]({song.url})** | `{song.requester.display_name}{' (Autoplay)' if getattr(song, 'is_autoplay', False) else ''}`"
                for i, song in enumerate(page_items)
            )
            embed.description = f"**Up Next:**\n{fmt}"

        total_duration = sum(s.duration for s in items if s.duration)
        mins, secs = divmod(total_duration, 60)
        hours, mins = divmod(mins, 60)
        dur_str = f"{hours}h {mins}m" if hours else f"{mins}m {secs}s"

        loop_str = self.player.loop_mode.value.capitalize()
        embed.set_footer(
            text=f"Page {self.current_page + 1} of {max_pages} | {len(items)} tracks | Queue Duration: {dur_str} | Loop: {loop_str}"
        )
        return embed

    @discord.ui.button(emoji="⏮️", style=discord.ButtonStyle.secondary)
    async def prev_page(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        self.current_page = max(0, self.current_page - 1)
        self.update_buttons()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(emoji="⏭️", style=discord.ButtonStyle.secondary)
    async def next_page(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        max_pages = self.get_max_pages()
        self.current_page = min(max_pages - 1, self.current_page + 1)
        self.update_buttons()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

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

    async def on_timeout(self):
        if self.message:
            try:
                await self.message.delete()
            except Exception:
                pass
            if self.player and self.player.queue_msg == self.message:
                self.player.queue_msg = None
