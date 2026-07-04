import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
import discord

from music_cog import MusicCog, Song
from music_bot import MusicBot

@pytest.fixture
def bot():
    mock_bot = AsyncMock(spec=MusicBot)
    mock_bot.loop = MagicMock()
    mock_bot.wait_until_ready = AsyncMock()
    return mock_bot

@pytest.fixture
def cog(bot):
    return MusicCog(bot)

@pytest.fixture
def interaction(bot):
    mock_interaction = AsyncMock(spec=discord.Interaction)
    mock_interaction.guild = MagicMock(spec=discord.Guild)
    mock_interaction.guild.id = 12345
    mock_interaction.channel = MagicMock(spec=discord.TextChannel)
    mock_interaction.user = MagicMock(spec=discord.Member)
    mock_interaction.user.voice = MagicMock(spec=discord.VoiceState)
    mock_interaction.user.voice.channel = MagicMock(spec=discord.VoiceChannel)
    mock_interaction.response = AsyncMock()
    mock_interaction.followup = AsyncMock()
    mock_interaction.client = MagicMock()
    mock_interaction.client.wait_until_ready = AsyncMock()
    mock_interaction.client.loop = bot.loop
    
    mock_vc = AsyncMock(spec=discord.VoiceClient)
    mock_vc.channel = mock_interaction.user.voice.channel
    mock_interaction.guild.voice_client = mock_vc
    
    return mock_interaction

@pytest.mark.asyncio
async def test_queue_append_shuffle_remove(cog, interaction):
    player = cog.get_player(interaction)
    
    song1 = Song({"title": "Track 1", "webpage_url": "http://1"}, interaction.user)
    song2 = Song({"title": "Track 2", "webpage_url": "http://2"}, interaction.user)
    
    await player.queue.put(song1)
    await player.queue.put(song2)
    
    assert player.queue.qsize() == 2
    
    try:
        player.shuffle()
    except AttributeError as e:
        pytest.fail(f"Shuffle method missing or failed: {e}")
        
    try:
        player.remove(1)
    except AttributeError as e:
        pytest.fail(f"Remove method missing or failed: {e}")
    except IndexError as e:
        pytest.fail(f"Out of bounds exception on remove: {e}")

@pytest.mark.asyncio
async def test_play_not_in_voice_channel(cog, interaction):
    interaction.user.voice = None
    
    await cog.play.callback(cog, interaction, url="http://youtube.com/watch?v=123")
    
    interaction.response.send_message.assert_called_with(
        "🔴 You need to join a voice channel first!", ephemeral=True
    )

@pytest.mark.asyncio
async def test_dj_restricted_command_stop(cog, interaction):
    mock_role = MagicMock(name="Regular Role")
    mock_role.name = "Regular Role"
    interaction.user.roles = [mock_role]
    interaction.user.guild_permissions.manage_guild = False
    
    await cog.stop.callback(cog, interaction)
    
    if interaction.response.send_message.call_args and interaction.response.send_message.call_args[0][0] == "⏹️ Stopped the music and cleared the queue.":
        pytest.fail("Missing permission check: Standard user was able to execute DJ-restricted command (stop).")

@pytest.mark.asyncio
async def test_idle_auto_disconnect_timer(cog, interaction):
    player = cog.get_player(interaction)
    player.bot.is_closed = MagicMock(return_value=False)
    
    with patch("asyncio.wait_for", side_effect=asyncio.TimeoutError):
        with patch.object(player, 'destroy', new_callable=MagicMock) as mock_destroy:
            await player.player_loop()
            mock_destroy.assert_called_once_with(interaction.guild)

@pytest.mark.asyncio
async def test_invalid_url_error_handling(cog, interaction):
    with patch("music_cog.YTDLSource.extract_info", side_effect=Exception("Invalid URL or format")):
        await cog.play.callback(cog, interaction, url="http://invalid.url")
        
    interaction.followup.send.assert_called_with("❌ An error occurred: Invalid URL or format")
