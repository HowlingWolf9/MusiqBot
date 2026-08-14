import discord
from music.models import LoopMode
from music.permissions import check_dj_permission


class PlayerView(discord.ui.View):
    def __init__(self, cog, player):
        super().__init__(timeout=None)
        self.cog = cog
        self.player = player
        self.update_loop_button()

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
            await interaction.response.send_message(
                "⏸️ Paused the music.", ephemeral=True
            )
        elif vc.is_paused():
            vc.resume()
            await interaction.response.send_message(
                "▶️ Resumed the music.", ephemeral=True
            )
        else:
            await interaction.response.send_message(
                "Nothing is playing.", ephemeral=True
            )

    @discord.ui.button(style=discord.ButtonStyle.secondary, emoji="⏭️")
    async def skip(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await check_dj_permission(
            interaction,
            "❌ You need the 'DJ' role or Manage Server permissions to do this.",
        ):
            return
        vc = interaction.guild.voice_client
        if vc and (vc.is_playing() or vc.is_paused()):
            self.player.manual_skip = True
            vc.stop()
            await interaction.response.send_message(
                "⏭️ Skipped the song.", ephemeral=True
            )
        else:
            await interaction.response.send_message("Nothing to skip.", ephemeral=True)

    @discord.ui.button(style=discord.ButtonStyle.danger, emoji="⏹️")
    async def stop(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await check_dj_permission(
            interaction,
            "❌ You need the 'DJ' role or Manage Server permissions to do this.",
        ):
            return
        self.player.manual_stop = True
        self.player.clear_queue()
        self.player.history.clear()
        vc = interaction.guild.voice_client
        if vc and (vc.is_playing() or vc.is_paused()):
            vc.stop()
        await interaction.response.send_message(
            "⏹️ Stopped music and cleared the queue.", ephemeral=True
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

        self.update_loop_button()
        try:
            await interaction.response.edit_message(
                embed=self.player.build_np_embed(), view=self
            )
        except Exception:
            await interaction.response.send_message(
                f"🔁 Loop mode set to **{self.player.loop_mode.value.capitalize()}**.",
                ephemeral=True,
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
            "🔀 Shuffled the queue.", ephemeral=True
        )
