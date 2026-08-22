import discord


def check_dj(interaction: discord.Interaction) -> bool:
    perms = getattr(interaction.user, "guild_permissions", None)
    if perms and (
        getattr(perms, "manage_guild", False) or getattr(perms, "administrator", False)
    ):
        return True

    cog = interaction.client.get_cog("MusicCog")
    custom_role_id = None
    custom_role_name = None
    if cog and interaction.guild:
        guild_settings = cog.settings.get(str(interaction.guild.id), {})
        custom_role_id = guild_settings.get("dj_role_id")
        custom_role_name = guild_settings.get("dj_role_name")

    if hasattr(interaction.user, "roles"):
        for role in interaction.user.roles:
            if custom_role_id and role.id == custom_role_id:
                return True
            if custom_role_name and role.name.lower() == custom_role_name.lower():
                return True
            if role.name.lower() == "dj":
                return True
    return False


async def check_dj_permission(
    interaction: discord.Interaction,
    message: str = "❌ You need the 'DJ' role or Manage Server permissions to perform this action.",
) -> bool:
    if not check_dj(interaction):
        if not interaction.response.is_done():
            await interaction.response.send_message(message, ephemeral=True)
        return False
    return True
