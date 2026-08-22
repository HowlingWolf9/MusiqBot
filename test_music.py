import os
import json
import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
import discord
from discord import app_commands

from music_cog import MusicCog, Song, LoopMode, check_dj, create_progress_bar, YTDLSource
from music_bot import MusicBot


@pytest.fixture
def bot():
    mock_bot = AsyncMock(spec=MusicBot)
    mock_bot.loop = MagicMock()
    async def dummy_run_in_executor(executor, func, *args, **kwargs):
        return func(*args, **kwargs)
    mock_bot.loop.run_in_executor = AsyncMock(side_effect=dummy_run_in_executor)

    def dummy_create_task(coro):
        coro.close()
        return MagicMock()

    mock_bot.loop.create_task = MagicMock(side_effect=dummy_create_task)
    mock_bot.wait_until_ready = AsyncMock()
    mock_bot.get_cog = MagicMock(return_value=None)
    return mock_bot



@pytest.fixture
def cog(bot):
    c = MusicCog(bot)
    bot.get_cog.return_value = c
    return c


@pytest.fixture
def interaction(bot):
    mock_interaction = AsyncMock(spec=discord.Interaction)
    mock_interaction.guild = MagicMock(spec=discord.Guild)
    mock_interaction.guild.id = 12345
    mock_interaction.guild.name = "Test Guild"
    mock_interaction.channel = AsyncMock(spec=discord.TextChannel)
    mock_interaction.user = MagicMock(spec=discord.Member)
    mock_interaction.user.voice = MagicMock(spec=discord.VoiceState)
    mock_interaction.user.voice.channel = MagicMock(spec=discord.VoiceChannel)
    mock_interaction.response = AsyncMock()
    mock_interaction.response.is_done = MagicMock(return_value=False)
    mock_interaction.followup = AsyncMock()
    mock_interaction.client = bot
    
    mock_vc = AsyncMock(spec=discord.VoiceClient)
    mock_vc.channel = mock_interaction.user.voice.channel
    mock_interaction.guild.voice_client = mock_vc
    mock_interaction.is_expired = MagicMock(return_value=False)
    
    return mock_interaction


@pytest.mark.asyncio
async def test_queue_append_shuffle_remove(cog, interaction):
    player = cog.get_player(interaction)
    
    song1 = Song({"title": "Track 1", "webpage_url": "http://1"}, interaction.user)
    song2 = Song({"title": "Track 2", "webpage_url": "http://2"}, interaction.user)
    
    await player.queue.put(song1)
    await player.queue.put(song2)
    
    assert player.queue.qsize() == 2
    
    player.shuffle()
    assert player.queue.qsize() == 2
    
    removed = player.remove(1)
    assert player.queue.qsize() == 1
    assert removed in [song1, song2]


@pytest.mark.asyncio
async def test_move_and_clear_queue(cog, interaction):
    player = cog.get_player(interaction)
    
    song1 = Song({"title": "Track 1", "webpage_url": "http://1"}, interaction.user)
    song2 = Song({"title": "Track 2", "webpage_url": "http://2"}, interaction.user)
    song3 = Song({"title": "Track 3", "webpage_url": "http://3"}, interaction.user)
    
    await player.queue.put(song1)
    await player.queue.put(song2)
    await player.queue.put(song3)
    
    # Move song 3 (index 2) to position 0
    moved = player.move(2, 0)
    assert moved == song3
    assert player.queue._queue[0] == song3
    
    # Clear queue
    player.clear_queue()
    assert player.queue.empty()


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
    mock_role.id = 999
    interaction.user.roles = [mock_role]
    interaction.user.guild_permissions.manage_guild = False
    interaction.user.guild_permissions.administrator = False
    
    await cog.stop.callback(cog, interaction)
    
    if interaction.response.send_message.call_args and interaction.response.send_message.call_args[0][0] == "⏹️ Stopped the music and cleared the queue.":
        pytest.fail("Missing permission check: Standard user was able to execute DJ-restricted command (stop).")


@pytest.mark.asyncio
async def test_custom_dj_role(cog, interaction):
    custom_role = MagicMock(name="Custom DJ")
    custom_role.name = "MusicMaster"
    custom_role.id = 777
    
    cog.settings[str(interaction.guild.id)] = {"dj_role_id": 777, "dj_role_name": "MusicMaster"}
    
    interaction.user.roles = [custom_role]
    interaction.user.guild_permissions.manage_guild = False
    interaction.user.guild_permissions.administrator = False
    
    assert check_dj(interaction) is True


@pytest.mark.asyncio
async def test_idle_auto_disconnect_timer(cog, interaction):
    player = cog.get_player(interaction)
    player.bot.is_closed = MagicMock(return_value=False)
    
    async def mock_wait_for(fut, timeout):
        if hasattr(fut, "close"):
            fut.close()
        raise asyncio.TimeoutError

    with patch("asyncio.wait_for", side_effect=mock_wait_for):
        with patch.object(player, 'destroy', new_callable=MagicMock) as mock_destroy:
            await player.player_loop()
            mock_destroy.assert_called_once_with(interaction.guild)


@pytest.mark.asyncio
async def test_invalid_url_error_handling(cog, interaction):
    with patch("music_cog.YTDLSource.extract_info", side_effect=Exception("Invalid URL or format")):
        await cog.play.callback(cog, interaction, url="http://invalid.url")
        
    interaction.followup.send.assert_called_with("❌ An error occurred: Invalid URL or format", ephemeral=True)


@pytest.mark.asyncio
async def test_ytsearch_default_prefix(cog, interaction):
    with patch("music_cog.ytdl.extract_info", return_value={"entries": [{"title": "Test Song", "webpage_url": "http://yt.com"}]}) as mock_ytdl:
        data = await YTDLSource.extract_info("shape of you")
        assert data["title"] == "Test Song"
        mock_ytdl.assert_called_once_with("ytsearch5:shape of you", download=False)

    with patch("music_cog.ytdl.extract_info", return_value={"entries": [{"title": "Test Song", "webpage_url": "http://yt.com"}]}) as mock_ytdl:
        data2 = await YTDLSource.extract_info("ytsearch:shape of you")
        assert data2["title"] == "Test Song"
        mock_ytdl.assert_called_once_with("ytsearch5:shape of you", download=False)





def test_progress_bar_calculation():
    bar_0 = create_progress_bar(0, 100, length=10)
    assert "🔘" in bar_0
    
    bar_50 = create_progress_bar(50, 100, length=10)
    assert bar_50.startswith("━━━━━🔘")


@pytest.mark.asyncio
async def test_loop_mode_toggle(cog, interaction):
    player = cog.get_player(interaction)
    assert player.loop_mode == LoopMode.OFF
    
    player.loop_mode = LoopMode.TRACK
    assert player.loop_mode == LoopMode.TRACK
    
    player.loop_mode = LoopMode.QUEUE
    assert player.loop_mode == LoopMode.QUEUE


@pytest.mark.asyncio
async def test_autocomplete_expired_interaction(cog, interaction):
    interaction.is_expired = MagicMock(return_value=True)
    res = await cog.song_autocomplete(interaction, "test query")
    assert res == []


def test_autocomplete_log_filter():
    from music_bot import AutocompleteErrorFilter
    import logging

    flt = AutocompleteErrorFilter()

    # Log record with 10062 (Unknown interaction) NotFound error
    exc = discord.NotFound(MagicMock(status=404, reason="Not Found"), {"code": 10062, "message": "Unknown interaction"})
    record = logging.LogRecord("discord.app_commands.tree", logging.ERROR, "", 0, "Error msg", (), (type(exc), exc, None))
    assert flt.filter(record) is False

    # Standard error record should be allowed through
    std_record = logging.LogRecord("discord.app_commands.tree", logging.ERROR, "", 0, "Normal error", (), None)
    assert flt.filter(std_record) is True


def test_select_best_search_entry():
    from music_cog import select_best_search_entry

    entries = [
        {"title": "Song (Official Music Video)", "uploader": "Artist Channel"},
        {"title": "Song (Official Audio)", "uploader": "Artist Channel"},
        {"title": "Song", "uploader": "Artist - Topic"},
    ]

    best = select_best_search_entry(entries)
    assert best["uploader"] == "Artist - Topic"


@pytest.mark.asyncio
async def test_concurrent_play_and_search(cog, interaction):
    interaction2 = AsyncMock(spec=discord.Interaction)
    interaction2.guild = interaction.guild
    interaction2.channel = interaction.channel
    interaction2.user = MagicMock(spec=discord.Member)
    interaction2.user.voice = interaction.user.voice
    interaction2.response = AsyncMock()
    interaction2.followup = AsyncMock()
    interaction2.client = interaction.client

    with patch("music_cog.YTDLSource.extract_info") as mock_extract:
        mock_extract.side_effect = [
            {"title": "Song User 1", "webpage_url": "http://yt.com/1"},
            {"title": "Song User 2", "webpage_url": "http://yt.com/2"},
        ]
        
        await asyncio.gather(
            cog.play.callback(cog, interaction, url="http://yt.com/1"),
            cog.play.callback(cog, interaction2, url="http://yt.com/2"),
        )
        
        player = cog.get_player(interaction)
        assert player.queue.qsize() == 2


@pytest.mark.asyncio
async def test_seek_valid_and_invalid_timestamps(cog, interaction):
    player = cog.get_player(interaction)
    player.current = Song({"title": "Test Track", "webpage_url": "http://yt.com"}, interaction.user)
    
    # Seeking when voice client is playing
    await cog.seek.callback(cog, interaction, timestamp="1:30")
    assert player.seek_position == 90
    interaction.response.send_message.assert_called_with("⏩ Seeked to `1:30`.")
        
    # Invalid timestamp format (e.g., negative or too many parts)
    interaction.response.send_message.reset_mock()
    await cog.seek.callback(cog, interaction, timestamp="-10")
    interaction.response.send_message.assert_called_with("❌ Invalid timestamp format. Use `mm:ss` or seconds.", ephemeral=True)

    interaction.response.send_message.reset_mock()
    await cog.seek.callback(cog, interaction, timestamp="1:2:3:4")
    interaction.response.send_message.assert_called_with("❌ Invalid timestamp format. Use `mm:ss` or seconds.", ephemeral=True)

    interaction.response.send_message.reset_mock()
    await cog.seek.callback(cog, interaction, timestamp="abc")
    interaction.response.send_message.assert_called_with("❌ Invalid timestamp format. Use `mm:ss` or seconds.", ephemeral=True)


@pytest.mark.asyncio
async def test_seek_and_replay_nothing_playing(cog, interaction):
    player = cog.get_player(interaction)
    player.current = None

    await cog.seek.callback(cog, interaction, timestamp="30")
    interaction.response.send_message.assert_called_with("Nothing is currently playing to seek.", ephemeral=True)

    interaction.response.send_message.reset_mock()
    await cog.replay.callback(cog, interaction)
    interaction.response.send_message.assert_called_with("Nothing is currently playing to replay.", ephemeral=True)


@pytest.mark.asyncio
async def test_volume_command_bounds(cog, interaction):
    # Invalid volume < 1
    await cog.volume.callback(cog, interaction, vol=0)
    interaction.response.send_message.assert_called_with("Please enter a value between 1 and 100.", ephemeral=True)

    # Invalid volume > 100
    interaction.response.send_message.reset_mock()
    await cog.volume.callback(cog, interaction, vol=150)
    interaction.response.send_message.assert_called_with("Please enter a value between 1 and 100.", ephemeral=True)

    # Valid volume
    interaction.response.send_message.reset_mock()
    await cog.volume.callback(cog, interaction, vol=75)
    interaction.response.send_message.assert_called_with("🔊 Changed volume to 75%")
    assert cog.get_player(interaction).volume == 0.75


@pytest.mark.asyncio
async def test_verify_voice_edge_cases(cog, interaction):
    # User not in a guild (Direct Message)
    interaction.guild = None
    res = await cog.verify_voice(interaction)
    assert res is False
    interaction.response.send_message.assert_called_with("🔴 This command can only be used in a server.", ephemeral=True)

    # Restore guild, user not in voice channel
    interaction.guild = MagicMock(spec=discord.Guild)
    interaction.user.voice = None
    interaction.response.send_message.reset_mock()
    res = await cog.verify_voice(interaction)
    assert res is False
    interaction.response.send_message.assert_called_with("🔴 You need to join a voice channel first!", ephemeral=True)

    # User in a different voice channel than bot
    user_vc = MagicMock()
    bot_vc = MagicMock()
    interaction.user.voice = MagicMock(channel=user_vc)
    interaction.guild.voice_client = MagicMock(channel=bot_vc)
    interaction.response.send_message.reset_mock()
    res = await cog.verify_voice(interaction)
    assert res is False
    interaction.response.send_message.assert_called_with("🔴 You must be in the same voice channel as the bot to use this.", ephemeral=True)


@pytest.mark.asyncio
async def test_remove_and_move_out_of_bounds(cog, interaction):
    player = cog.get_player(interaction)
    song1 = Song({"title": "Song 1", "webpage_url": "http://1"}, interaction.user)
    await player.queue.put(song1)

    # Out of bounds remove
    await cog.remove.callback(cog, interaction, index=5)
    interaction.response.send_message.assert_called_with("❌ Invalid queue index. Check the queue for valid numbers.", ephemeral=True)

    # Out of bounds move
    interaction.response.send_message.reset_mock()
    await cog.move.callback(cog, interaction, from_index=1, to_index=10)
    interaction.response.send_message.assert_called_with("❌ Invalid queue index positions.", ephemeral=True)


@pytest.mark.asyncio
async def test_resolve_spotify_error_handling(cog):
    # Network failure or non-200 Spotify oembed response
    with patch("music_cog.MusicCog.get_session") as mock_session_fn:
        mock_session = MagicMock()
        mock_resp = AsyncMock()
        mock_resp.status = 500
        mock_cm = MagicMock()
        mock_cm.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_cm.__aexit__ = AsyncMock(return_value=None)
        mock_session.get.return_value = mock_cm
        mock_session_fn.return_value = mock_session

        res = await cog.resolve_spotify("https://open.spotify.com/track/invalid")
        assert res is None


def test_progress_bar_edge_cases():
    # Elapsed exceeds total
    bar_overflow = create_progress_bar(150, 100, length=10)
    assert "🔘" in bar_overflow
    assert bar_overflow.startswith("━━━━━━━━━━🔘")

    # Total zero
    bar_zero = create_progress_bar(0, 0, length=10)
    assert bar_zero.startswith("🔘")

    # Elapsed negative
    bar_neg = create_progress_bar(-10, 100, length=10)
    assert bar_neg.startswith("🔘")


@pytest.mark.asyncio
async def test_voice_state_update_empty_channel_timer(cog):
    guild = MagicMock()
    guild.id = 123
    voice_client = MagicMock()
    guild.voice_client = voice_client
    bot_member = MagicMock(bot=True)
    voice_client.channel.members = [bot_member]

    user_member = MagicMock(bot=False, guild=guild)
    before_state = MagicMock(channel=voice_client.channel)
    after_state = MagicMock(channel=None)

    # Member leaves channel -> timer created
    await cog.on_voice_state_update(user_member, before_state, after_state)
    assert guild.id in cog.empty_channel_timers

    # Member rejoins -> timer cancelled
    before_state.channel = None
    after_state.channel = voice_client.channel
    await cog.on_voice_state_update(user_member, before_state, after_state)
    assert guild.id not in cog.empty_channel_timers


def test_select_best_search_entry_with_none_and_invalids():
    from music_cog import select_best_search_entry

    entries = [
        None,
        "invalid string entry",
        {"title": "Track 1", "uploader": "Random Channel"},
        {"title": "Track 2 (Official Audio)", "uploader": "Artist - Topic"},
    ]
    best = select_best_search_entry(entries)
    assert best["title"] == "Track 2 (Official Audio)"


@pytest.mark.asyncio
async def test_queue_view_page_clamping(cog, interaction):
    from music_cog import QueueView
    player = cog.get_player(interaction)

    song1 = Song({"title": "Track 1", "webpage_url": "http://1"}, interaction.user)
    await player.queue.put(song1)

    view = QueueView(player, interaction.user)
    view.current_page = 5  # Out of range page

    embed = view.build_embed()
    # current_page should be clamped to max_pages - 1 (0 in this case)
    assert view.current_page == 0
    assert "Track 1" in embed.description


@pytest.mark.asyncio
async def test_autocomplete_malformed_api_response(cog, interaction):
    if hasattr(cog, "autocomplete_cache"):
        cog.autocomplete_cache.clear()
    with patch("music_cog.MusicCog.get_session") as mock_session_fn:
        mock_session = MagicMock()
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value={"unexpected": "payload"})
        mock_cm = MagicMock()
        mock_cm.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_cm.__aexit__ = AsyncMock(return_value=None)
        mock_session.get.return_value = mock_cm
        mock_session_fn.return_value = mock_session

        res = await cog.song_autocomplete(interaction, "query")
        assert len(res) == 1
        assert res[0].name == "query"


@pytest.mark.asyncio
async def test_player_loop_source_creation_failure_notifies_channel(cog, interaction):
    player = cog.get_player(interaction)
    player.channel = AsyncMock()
    song = Song({"title": "Broken Track", "webpage_url": "http://invalid"}, interaction.user)
    await player.queue.put(song)

    with patch("music_cog.YTDLSource.create_source", side_effect=Exception("Extraction Failed")):
        player.bot.is_closed = MagicMock(side_effect=[False, True])
        await player.player_loop()
        player.channel.send.assert_called_with("❌ Failed to play **Broken Track**: Track unavailable or restricted.")


@pytest.mark.asyncio
async def test_bot_forced_disconnect_cleans_up_player(cog, interaction):
    cog.get_player(interaction)
    bot_user = MagicMock()
    bot_user.guild = interaction.guild
    cog.bot.user = bot_user

    before_state = MagicMock(channel=MagicMock())
    after_state = MagicMock(channel=None)

    with patch.object(cog, 'cleanup', new_callable=AsyncMock) as mock_cleanup:
        await cog.on_voice_state_update(bot_user, before_state, after_state)
        mock_cleanup.assert_called_once_with(interaction.guild)


@pytest.mark.asyncio
async def test_get_related_video_youtube_shorts_regex(cog, interaction):
    player = cog.get_player(interaction)

    with patch("music_cog.yt_dlp.YoutubeDL") as mock_ytdl_class:
        mock_instance = MagicMock()
        mock_instance.extract_info.return_value = {"entries": [{"url": "http://yt.com/watch?v=123"}]}
        mock_ytdl_class.return_value.__enter__.return_value = mock_instance
        res = await player.get_related_video("https://www.youtube.com/shorts/abc123XYZ")
        assert res == "http://yt.com/watch?v=123"


def test_search_select_handles_none_title_and_invalid_entry(cog):
    from music_cog import SearchSelect

    entries = [
        None,
        {"title": None, "duration": 120},
        {"title": "Valid Track", "duration": 180},
    ]

    select = SearchSelect(entries, cog)
    assert len(select.options) == 2
    assert select.options[0].label == "2. Unknown Title"
    assert select.options[1].label == "3. Valid Track"


@pytest.mark.asyncio
async def test_ensure_voice_client_connect_failure_returns_none(cog, interaction):
    interaction.guild.voice_client = None
    target_channel = interaction.user.voice.channel
    target_channel.connect.side_effect = Exception("Voice Server Connection Timeout")

    vc = await cog.ensure_voice_client(interaction)
    assert vc is None


@pytest.mark.asyncio
async def test_process_playlist_filters_invalid_and_idless_entries(cog, interaction):
    player = cog.get_player(interaction)
    playlist_url = "http://youtube.com/playlist?list=123"

    raw_data = {
        "entries": [
            None,
            "corrupted_entry_str",
            {"title": "Track without URL or ID"},
            {"title": "Track 1", "id": "validid123"},
        ]
    }

    with patch("music_cog.yt_dlp.YoutubeDL") as mock_ytdl_class:
        mock_instance = MagicMock()
        mock_instance.extract_info.return_value = raw_data
        mock_ytdl_class.return_value.__enter__.return_value = mock_instance

        await cog.process_playlist(interaction, playlist_url, player)
        assert player.queue.qsize() == 1
        queued_song = await player.queue.get()
        assert queued_song.title == "Track 1"
        assert queued_song.url == "https://www.youtube.com/watch?v=validid123"


@pytest.mark.asyncio
async def test_is_busy_concurrency_state_tracking(cog, interaction):
    player = cog.get_player(interaction)
    assert player.is_busy is False

    player.is_loading = True
    assert player.is_busy is True

    player.is_loading = False
    assert player.is_busy is False

    player.current = MagicMock()
    assert player.is_busy is True


def test_ffmpeg_low_latency_options():
    from music_cog import ffmpeg_options
    before = ffmpeg_options.get("before_options", "")
    assert "-nostdin" in before
    assert "-reconnect 1" in before
    assert "-reconnect_streamed 1" in before
    assert "-reconnect_delay_max" in before


@pytest.mark.asyncio
async def test_loop_track_skip_advances_queue(cog, interaction):
    player = cog.get_player(interaction)
    player.loop_mode = LoopMode.TRACK

    song1 = Song({"title": "Track 1", "webpage_url": "http://1"}, interaction.user)
    song2 = Song({"title": "Track 2", "webpage_url": "http://2"}, interaction.user)
    player.current = song1
    await player.queue.put(song2)

    # Calling skip sets manual_skip
    await cog.skip.callback(cog, interaction)
    assert player.manual_skip is True


@pytest.mark.asyncio
async def test_stop_in_loop_modes_clears_queue_and_sets_manual_stop(cog, interaction):
    player = cog.get_player(interaction)
    player.loop_mode = LoopMode.TRACK

    song1 = Song({"title": "Track 1", "webpage_url": "http://1"}, interaction.user)
    player.current = song1

    await cog.stop.callback(cog, interaction)
    assert player.manual_stop is True
    assert player.queue.empty()


@pytest.mark.asyncio
async def test_autoplay_fallback_search_when_mix_empty(cog, interaction):
    player = cog.get_player(interaction)
    player.current = Song({"title": "Bohemian Rhapsody", "webpage_url": "http://yt.com/123"}, interaction.user)
    player.history = ["http://yt.com/123"]

    with patch("music_cog.yt_dlp.YoutubeDL") as mock_ydl:
        # Mock empty mix entries
        mock_instance = MagicMock()
        mock_instance.extract_info.return_value = {"entries": []}
        mock_ydl.return_value.__enter__.return_value = mock_instance

        with patch("music_cog.YTDLSource.extract_info", return_value={"title": "Related Song", "webpage_url": "http://yt.com/fallback"}) as mock_search:
            res = await player.get_related_video("http://yt.com/123")
            assert res == "http://yt.com/fallback"
            mock_search.assert_called_once_with("ytsearch5:Bohemian Rhapsody audio", loop=cog.bot.loop)


@pytest.mark.asyncio
async def test_dj_restricted_volume_and_autoplay(cog, interaction):
    mock_role = MagicMock(name="Regular Role")
    mock_role.name = "Regular Role"
    mock_role.id = 999
    interaction.user.roles = [mock_role]
    interaction.user.guild_permissions.manage_guild = False
    interaction.user.guild_permissions.administrator = False

    # Volume restricted
    await cog.volume.callback(cog, interaction, vol=50)
    interaction.response.send_message.assert_called_with(
        "❌ You need the 'DJ' role or Manage Server permissions to use this command.", ephemeral=True
    )

    interaction.response.send_message.reset_mock()
    # Autoplay restricted
    await cog.autoplay.callback(cog, interaction)
    interaction.response.send_message.assert_called_with(
        "❌ You need the 'DJ' role or Manage Server permissions to use this command.", ephemeral=True
    )


def test_atomic_settings_persistence(cog, tmp_path):
    temp_settings_file = str(tmp_path / "settings_test.json")
    cog.settings_file = temp_settings_file
    cog.settings = {"123": {"volume": 0.8, "autoplay": True}}

    cog.save_settings()

    assert os.path.exists(temp_settings_file)
    with open(temp_settings_file, "r") as f:
        data = json.load(f)
    assert data["123"]["volume"] == 0.8


@pytest.mark.asyncio
async def test_resolve_spotify_cleans_metadata(cog):
    with patch("music_cog.MusicCog.get_session") as mock_session_fn:
        mock_session = MagicMock()
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value={"title": "Blinding Lights - song by The Weeknd | Spotify"})
        mock_cm = MagicMock()
        mock_cm.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_cm.__aexit__ = AsyncMock(return_value=None)
        mock_session.get.return_value = mock_cm
        mock_session_fn.return_value = mock_session

        res = await cog.resolve_spotify("https://open.spotify.com/track/0VjIjW4GlUZAMYd2vXMi3b")
        assert res == "ytsearch:Blinding Lights"


@pytest.mark.asyncio
async def test_queue_view_interaction_check(cog, interaction):
    from music_cog import QueueView
    player = cog.get_player(interaction)
    view = QueueView(player, interaction.user)

    # User in voice channel
    assert await view.interaction_check(interaction) is True

    # User not in voice channel
    interaction.user.voice = None
    assert await view.interaction_check(interaction) is False


@pytest.mark.asyncio
async def test_loop_command_updates_np_msg_live(cog, interaction):
    player = cog.get_player(interaction)
    player.current = Song({"title": "Test Song", "webpage_url": "http://yt.com"}, interaction.user)
    mock_np = AsyncMock()
    player.np_msg = mock_np

    mode = MagicMock()
    mode.name = "Track"
    mode.value = "track"

    await cog.loop.callback(cog, interaction, mode=mode)
    assert player.loop_mode == LoopMode.TRACK
    mock_np.edit.assert_called_once()


@pytest.mark.asyncio
async def test_cog_app_command_error_missing_permissions(cog, interaction):
    err = app_commands.MissingPermissions(["manage_guild"])
    await cog.cog_app_command_error(interaction, err)
    interaction.response.send_message.assert_called_with(
        "❌ You need the following permission(s) to use this command: `manage_guild`", ephemeral=True
    )


@pytest.mark.asyncio
async def test_player_loop_voice_client_play_exception_handling(cog, interaction):
    player = cog.get_player(interaction)
    player.channel = AsyncMock()
    song = Song({"title": "Test Song", "webpage_url": "http://yt.com"}, interaction.user)
    await player.queue.put(song)

    interaction.guild.voice_client.is_connected = MagicMock(return_value=True)
    interaction.guild.voice_client.play = MagicMock(side_effect=discord.ClientException("Voice disconnected"))

    with patch("music_cog.YTDLSource.create_source", return_value=MagicMock()):
        player.bot.is_closed = MagicMock(side_effect=[False, True])
        await player.player_loop()
        player.channel.send.assert_called_with("❌ Failed to play **Test Song**: Voice playback error.")
        assert player.current is None


@pytest.mark.asyncio
async def test_clear_messages_bulk_limit_14_days(cog, interaction):
    interaction.user.guild_permissions.manage_messages = True
    interaction.channel.permissions_for = MagicMock(return_value=MagicMock(manage_messages=True))
    interaction.channel.purge = AsyncMock(return_value=[MagicMock()])

    # Test within 14 days -> can_bulk = True
    await cog.clear_messages.callback(cog, interaction, amount=50, days=5)
    _, kwargs = interaction.channel.purge.call_args
    assert kwargs.get("bulk") is True

    # Test older than 14 days -> can_bulk = False
    interaction.channel.purge.reset_mock()
    await cog.clear_messages.callback(cog, interaction, amount=50, days=15)
    _, kwargs = interaction.channel.purge.call_args
    assert kwargs.get("bulk") is False


@pytest.mark.asyncio
async def test_cleanup_removes_voice_connect_locks(cog, interaction):
    cog.voice_connect_locks[interaction.guild.id] = asyncio.Lock()
    assert interaction.guild.id in cog.voice_connect_locks

    await cog.cleanup(interaction.guild)
    assert interaction.guild.id not in cog.voice_connect_locks


@pytest.mark.asyncio
async def test_song_autocomplete_cache_and_https(cog, interaction):
    assert hasattr(cog, "autocomplete_cache")
    assert isinstance(cog.autocomplete_cache, dict)

    with patch("music_cog.MusicCog.get_session") as mock_session_fn:
        mock_session = MagicMock()
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value=["query", ["Result 1", "Result 2"]])
        mock_cm = MagicMock()
        mock_cm.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_cm.__aexit__ = AsyncMock(return_value=None)
        mock_session.get.return_value = mock_cm
        mock_session_fn.return_value = mock_session

        res = await cog.song_autocomplete(interaction, "test query")
        assert len(res) == 2
        assert res[0].name == "Result 1"
        mock_session.get.assert_called_once()
        url_called = mock_session.get.call_args[0][0]
        assert url_called.startswith("https://suggestqueries.google.com")


@pytest.mark.asyncio
async def test_user_song_priority_over_autoqueue(cog, interaction):
    player = cog.get_player(interaction)
    
    # Add an autoplay song first
    auto_song = Song({"title": "Autoplay Song", "webpage_url": "http://auto1"}, cog.bot.user, is_autoplay=True)
    await player.queue.put(auto_song)
    
    # Now user adds a song
    user_song = Song({"title": "User Song", "webpage_url": "http://user1"}, interaction.user, is_autoplay=False)
    await player.queue.put(user_song)
    
    items = player.get_queue_items()
    assert len(items) == 2
    # User song must be first in line before autoqueue song
    assert items[0] == user_song
    assert items[1] == auto_song


@pytest.mark.asyncio
async def test_multiple_user_songs_fifo_before_autoqueue(cog, interaction):
    player = cog.get_player(interaction)
    
    auto_song1 = Song({"title": "Auto 1", "webpage_url": "http://auto1"}, cog.bot.user, is_autoplay=True)
    auto_song2 = Song({"title": "Auto 2", "webpage_url": "http://auto2"}, cog.bot.user, is_autoplay=True)
    await player.queue.put(auto_song1)
    await player.queue.put(auto_song2)
    
    user_song1 = Song({"title": "User 1", "webpage_url": "http://u1"}, interaction.user, is_autoplay=False)
    user_song2 = Song({"title": "User 2", "webpage_url": "http://u2"}, interaction.user, is_autoplay=False)
    
    # User 1 adds song, then User 2 adds song
    await player.queue.put(user_song1)
    await player.queue.put(user_song2)
    
    items = player.get_queue_items()
    assert len(items) == 4
    # User songs must maintain FIFO order among themselves and precede autoqueue songs
    assert items[0] == user_song1
    assert items[1] == user_song2
    assert items[2] == auto_song1
    assert items[3] == auto_song2


@pytest.mark.asyncio
async def test_autoplay_playing_not_interrupted_by_user_request(cog, interaction):
    player = cog.get_player(interaction)
    
    # Autoplay song is currently playing
    auto_playing = Song({"title": "Current Auto", "webpage_url": "http://auto_curr"}, cog.bot.user, is_autoplay=True)
    player.current = auto_playing
    
    # User requests a song while autoplay is actively playing
    user_song = Song({"title": "User Request", "webpage_url": "http://user_req"}, interaction.user, is_autoplay=False)
    await player.queue.put(user_song)
    
    # Currently playing song remains uninterrupted
    assert player.current == auto_playing
    # Next up in queue is the user's requested song
    assert player.peek_next() == user_song


@pytest.mark.asyncio
async def test_manual_skip_during_autoplay_transitions_to_user_song(cog, interaction):
    player = cog.get_player(interaction)
    player.channel = AsyncMock()
    
    auto_playing = Song({"title": "Autoplay Song", "webpage_url": "http://auto"}, cog.bot.user, is_autoplay=True)
    user_song = Song({"title": "User Song", "webpage_url": "http://user"}, interaction.user, is_autoplay=False)
    
    await player.queue.put(user_song)
    player.current = auto_playing
    player.manual_skip = True
    
    # Next song peeked should be user song
    assert player.peek_next() == user_song


@pytest.mark.asyncio
async def test_shuffle_preserves_user_priority(cog, interaction):
    player = cog.get_player(interaction)
    
    u1 = Song({"title": "U1", "webpage_url": "http://u1"}, interaction.user, is_autoplay=False)
    u2 = Song({"title": "U2", "webpage_url": "http://u2"}, interaction.user, is_autoplay=False)
    u3 = Song({"title": "U3", "webpage_url": "http://u3"}, interaction.user, is_autoplay=False)
    a1 = Song({"title": "A1", "webpage_url": "http://a1"}, cog.bot.user, is_autoplay=True)
    a2 = Song({"title": "A2", "webpage_url": "http://a2"}, cog.bot.user, is_autoplay=True)
    
    for s in [u1, u2, u3, a1, a2]:
        await player.queue.put(s)
        
    player.shuffle()
    
    items = player.get_queue_items()
    assert len(items) == 5
    # The first 3 items must all be user songs, and the last 2 must be auto songs
    assert all(not item.is_autoplay for item in items[:3])
    assert all(item.is_autoplay for item in items[3:])


@pytest.mark.asyncio
async def test_loop_queue_respects_user_and_autoplay_priority(cog, interaction):
    player = cog.get_player(interaction)
    player.loop_mode = LoopMode.QUEUE
    
    auto_song = Song({"title": "A1", "webpage_url": "http://a1"}, cog.bot.user, is_autoplay=True)
    await player.queue.put(auto_song)
    
    # Suppose a user song finishes in LoopMode.QUEUE
    user_curr = Song({"title": "U1", "webpage_url": "http://u1"}, interaction.user, is_autoplay=False)
    new_song = Song(user_curr.data, user_curr.requester, is_autoplay=user_curr.is_autoplay)
    await player.queue.put(new_song)
    
    items = player.get_queue_items()
    # The re-queued user song should be placed before the autoplay song
    assert items[0] == new_song
    assert items[1] == auto_song


def test_ytdl_format_options_priority():
    from music_cog import ytdl_format_options
    assert "bestaudio/best" in ytdl_format_options["format"]
    player_clients = (
        ytdl_format_options.get("extractor_args", {}).get("youtube", {}).get("player_client", [])
    )
    assert "android" in player_clients
    assert "web" in player_clients


def test_is_valid_music_entry():
    from music_cog import is_valid_music_entry

    # Valid music entries
    assert is_valid_music_entry({"title": "Adele - Easy On Me (Official Video)", "duration": 215}) is True
    assert is_valid_music_entry({"title": "Queen - Bohemian Rhapsody (Official Audio)", "duration": 354}) is True
    assert is_valid_music_entry({"title": "Artist - Track (Lyrics)", "duration": 180}) is True

    # Non-music keyword rejections
    assert is_valid_music_entry({"title": "Reacting to Adele - Easy On Me! (She Cried)", "duration": 600}) is False
    assert is_valid_music_entry({"title": "Top 10 Best Songs Tier List Review", "duration": 400}) is False
    assert is_valid_music_entry({"title": "How To Play Piano Tutorial for Beginners", "duration": 500}) is False
    assert is_valid_music_entry({"title": "Podcast Episode 42: Music Discussion", "duration": 700}) is False
    assert is_valid_music_entry({"title": "Epic Gameplay Walkthrough Part 1", "duration": 800}) is False
    assert is_valid_music_entry({"title": "10 Hours Loop of Chill Beats", "duration": 36000}) is False
    assert is_valid_music_entry({"title": "Behind The Scenes of Music Video", "duration": 300}) is False

    # Duration boundary rejections
    assert is_valid_music_entry({"title": "Short Meme Clip", "duration": 30}) is False
    assert is_valid_music_entry({"title": "Full Album Compilation Live", "duration": 1200}) is False


def test_extract_video_id_and_normalize_title():
    from music_cog import extract_video_id, normalize_title

    assert extract_video_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    assert extract_video_id("https://youtu.be/dQw4w9WgXcQ?t=42") == "dQw4w9WgXcQ"
    assert extract_video_id("https://www.youtube.com/shorts/dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    assert extract_video_id("https://www.youtube.com/embed/dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    assert extract_video_id("dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    assert extract_video_id(None) is None

    assert normalize_title("Rick Astley - Never Gonna Give You Up (Official Video) [4K]") == "rick astley never gonna give you up"
    assert normalize_title("Shakira - Waka Waka (This Time for Africa) [HQ]") == "shakira waka waka"


@pytest.mark.asyncio
async def test_get_related_video_filters_non_music_and_history(cog, interaction):
    player = cog.get_player(interaction)
    player.history = ["https://www.youtube.com/watch?v=song1111111"]
    player.current = Song({"title": "Current Seed Song", "webpage_url": "https://www.youtube.com/watch?v=seed1111111"}, interaction.user)

    queued_song = Song({"title": "Queued Song", "webpage_url": "https://www.youtube.com/watch?v=queued11111"}, interaction.user)
    await player.queue.put(queued_song)

    mock_entries = [
        {"id": "seed1111111", "title": "Current Seed Song", "url": "https://www.youtube.com/watch?v=seed1111111", "duration": 200},
        {"id": "song1111111", "title": "Already Played Song", "url": "https://www.youtube.com/watch?v=song1111111", "duration": 200},
        {"id": "queued11111", "title": "Queued Song", "url": "https://www.youtube.com/watch?v=queued11111", "duration": 200},
        {"id": "reaction111", "title": "Reaction To Seed Song!!", "url": "https://www.youtube.com/watch?v=reaction111", "duration": 400},
        {"id": "valid_song1", "title": "Valid Music Track 1", "url": "https://www.youtube.com/watch?v=valid_song1", "duration": 210},
        {"id": "valid_song2", "title": "Valid Music Track 2", "url": "https://www.youtube.com/watch?v=valid_song2", "duration": 195},
    ]

    with patch("music_cog.yt_dlp.YoutubeDL") as mock_ydl_class:
        mock_instance = MagicMock()
        mock_instance.extract_info.return_value = {"entries": mock_entries}
        mock_ydl_class.return_value.__enter__.return_value = mock_instance

        # Test recommendations over multiple trials
        for _ in range(5):
            res = await player.get_related_video("https://www.youtube.com/watch?v=seed1111111")
            assert res in ("https://www.youtube.com/watch?v=valid_song1", "https://www.youtube.com/watch?v=valid_song2")
            assert "seed1111111" not in res
            assert "song1111111" not in res
            assert "queued11111" not in res
            assert "reaction111" not in res


def test_float_duration_song_builds_np_embed(cog, interaction):
    player = cog.get_player(interaction)
    player.current = Song({"title": "Float Track", "webpage_url": "http://yt.com", "duration": 215.7}, interaction.user)
    player.current_start_time = None

    # Must not raise ValueError: Unknown format code 'd' for object of type 'float'
    embed = player.build_np_embed()
    assert "00:00 / 03:35" in embed.description
    assert player.current.duration == 215


def test_float_duration_search_select_options():
    from music_cog import SearchSelect

    entries = [{"title": "Float Track", "duration": 199.9}]
    select = SearchSelect(entries, MagicMock())
    assert select.options[0].description == "3:19"


@pytest.mark.asyncio
async def test_autoplay_toggle_idle_spawns_prefetch_task(cog, interaction):
    player = cog.get_player(interaction)
    player.autoplay = False
    player.history = ["http://yt.com/previous"]

    with patch.object(cog.bot.loop, "create_task", side_effect=lambda coro: coro.close() or MagicMock()) as mock_ct:
        await cog.autoplay.callback(cog, interaction)
        assert player.autoplay is True
        mock_ct.assert_called_once()

    # The created task must be prefetch_autoplay (wakes idle loop via queue.put),
    # not a no-op next.set()
    assert interaction.response.send_message.call_args[0][0] == "📻 Autoplay is now **enabled**."


@pytest.mark.asyncio
async def test_autocomplete_cache_hit_filters_non_string_entries(cog, interaction):
    cog.autocomplete_cache.clear()
    cog.autocomplete_cache["bad query"] = ["Good Suggestion", {"not": "a string"}, 12345]

    res = await cog.song_autocomplete(interaction, "bad query")
    assert len(res) == 1
    assert res[0].name == "Good Suggestion"


@pytest.mark.asyncio
async def test_autocomplete_cache_stores_filtered_suggestions(cog, interaction):
    cog.autocomplete_cache.clear()
    with patch("music_cog.MusicCog.get_session") as mock_session_fn:
        mock_session = MagicMock()
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value=["q", [{"junk": 1}, "Real Result"]])
        mock_cm = MagicMock()
        mock_cm.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_cm.__aexit__ = AsyncMock(return_value=None)
        mock_session.get.return_value = mock_cm
        mock_session_fn.return_value = mock_session

        res = await cog.song_autocomplete(interaction, "filtered query")
        assert len(res) == 1
        assert res[0].name == "Real Result"
        assert cog.autocomplete_cache["filtered query"] == ["Real Result"]


@pytest.mark.asyncio
async def test_unexpected_app_command_error_responds_to_user(cog, interaction):
    err = app_commands.AppCommandError(RuntimeError("boom"))
    await cog.cog_app_command_error(interaction, err)
    interaction.response.send_message.assert_called_with(
        "❌ An unexpected error occurred. Please try again later.", ephemeral=True
    )


@pytest.mark.asyncio
async def test_unexpected_error_after_response_uses_followup(cog, interaction):
    interaction.response.is_done = MagicMock(return_value=True)
    err = app_commands.AppCommandError(RuntimeError("boom"))
    await cog.cog_app_command_error(interaction, err)
    interaction.followup.send.assert_called_with(
        "❌ An unexpected error occurred. Please try again later.", ephemeral=True
    )


def test_check_dj_user_without_guild_permissions(interaction):
    class FakeUser:
        roles = []

    interaction.user = FakeUser()
    assert check_dj(interaction) is False


def test_coerce_duration_variants():
    from music_cog import coerce_duration

    assert coerce_duration(215.7) == 215
    assert coerce_duration("199") == 199
    assert coerce_duration("garbage") == 0
    assert coerce_duration(None) == 0
    assert coerce_duration(0) == 0
    assert coerce_duration(float("nan")) == 0


def test_search_select_garbage_duration_string():
    from music_cog import SearchSelect

    entries = [{"title": "Weird Track", "duration": "3:19"}]
    select = SearchSelect(entries, MagicMock())
    assert select.options[0].description == "Unknown duration"


def test_silence_primer_prepends_zero_frames_then_passes_through():
    from music.audio import SilencePrimer

    class FakeSource(discord.AudioSource):
        def __init__(self):
            self.calls = 0

        def read(self):
            self.calls += 1
            return b"\x01" * 10

        def cleanup(self):
            self.cleaned = True

    fake = FakeSource()
    primer = SilencePrimer(fake, frames=3)

    assert primer.is_opus() is False
    for _ in range(3):
        frame = primer.read()
        assert frame == b"\x00" * 3840
    assert fake.calls == 0

    assert primer.read() == b"\x01" * 10
    assert fake.calls == 1

    primer.cleanup()
    assert fake.cleaned


@pytest.mark.asyncio
async def test_create_source_priming_wraps_inside_transformer():
    from music.audio import YTDLSource, SilencePrimer

    class DummyFFmpeg(discord.AudioSource):
        def read(self):
            return b"\x00" * 3840

    data = {"url": "http://stream.example/audio", "title": "Primed Track"}

    with patch("music.audio.discord.FFmpegPCMAudio", return_value=DummyFFmpeg()):
        src = await YTDLSource.create_source(data, prime_frames=25)
        assert isinstance(src.original, SilencePrimer)
        assert src.original.remaining == 25
        # PCMVolumeTransformer stays outermost so /volume keeps working
        assert src.volume == 0.5

        plain = await YTDLSource.create_source(data)
        assert not isinstance(plain.original, SilencePrimer)


@pytest.mark.asyncio
async def test_create_source_passes_user_agent_to_ffmpeg():
    from music.audio import YTDLSource

    class DummyFFmpeg(discord.AudioSource):
        def read(self):
            return b"\x00" * 3840

    captured = {}

    def fake_ffmpeg(url, **kwargs):
        captured.update(kwargs)
        return DummyFFmpeg()

    with patch("music.audio.discord.FFmpegPCMAudio", side_effect=fake_ffmpeg):
        await YTDLSource.create_source(
            {"url": "http://stream.example/a", "title": "T",
             "http_headers": {"User-Agent": "TestAgent/1.0"}}
        )
        assert "-user_agent 'TestAgent/1.0'" in captured["before_options"]

        captured.clear()
        await YTDLSource.create_source({"url": "http://stream.example/b", "title": "T"})
        assert "-user_agent" not in captured["before_options"]

        captured.clear()
        await YTDLSource.create_source(
            {"url": "http://stream.example/c", "title": "T",
             "http_headers": {"User-Agent": "bad'agent\\weird"}}
        )
        assert "-user_agent 'badagentweird'" in captured["before_options"]

        captured.clear()
        await YTDLSource.create_source(
            {"url": "http://stream.example/d", "title": "T",
             "http_headers": {"User-Agent": "'"}}
        )
        assert "-user_agent" not in captured["before_options"]


def test_is_instant_fail_matrix(cog, interaction):
    import time as _time

    player = cog.get_player(interaction)

    # No playback started / no current track -> not a failure
    assert player._is_instant_fail() is False

    player.current = Song({"title": "Long Track", "webpage_url": "http://x", "duration": 200}, interaction.user)

    # Recent start + long known duration -> instant fail
    player.last_play_start = _time.time()
    assert player._is_instant_fail() is True

    # Stale start (played longer than threshold) -> normal completion
    player.last_play_start = _time.time() - 5
    assert player._is_instant_fail() is False

    # Known ultra-short track that finished fast -> legitimate, not a failure
    player.last_play_start = _time.time()
    player.current = Song({"title": "Short Clip", "webpage_url": "http://x", "duration": 1}, interaction.user)
    assert player._is_instant_fail() is False

    # Unknown duration ended instantly -> treated as failure
    player.current = Song({"title": "Unknown Length", "webpage_url": "http://x"}, interaction.user)
    assert player._is_instant_fail() is True


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", [LoopMode.TRACK, LoopMode.QUEUE])
async def test_instant_fail_bypasses_loop_requeue(cog, interaction, mode):
    player = cog.get_player(interaction)
    player.channel = AsyncMock()
    player.loop_mode = mode
    song = Song({"title": "Dead Track", "webpage_url": "http://yt.com/dead", "duration": 200}, interaction.user)
    await player.queue.put(song)

    interaction.guild.voice_client.is_connected = MagicMock(return_value=True)

    def fake_play(source, after=None):
        # Simulate ffmpeg dying instantly: EOF fires the after-callback
        player.next.set()

    interaction.guild.voice_client.play = MagicMock(side_effect=fake_play)

    with patch("music_cog.YTDLSource.create_source", return_value=MagicMock()):
        player.bot.is_closed = MagicMock(side_effect=[False, True])
        await player.player_loop()

    # Dead track must NOT be re-queued by either loop mode
    assert player.queue.empty()
    assert player.current is None
    player.channel.send.assert_called_with(
        "⚠️ **Dead Track** is unavailable — skipping."
    )


@pytest.mark.asyncio
async def test_empty_channel_timer_repeat_events_keep_countdown(cog):
    guild = MagicMock()
    guild.id = 424242
    voice_client = MagicMock()
    guild.voice_client = voice_client
    voice_client.channel.members = [MagicMock(bot=True)]

    member = MagicMock(bot=False, guild=guild)
    before = MagicMock(channel=voice_client.channel)
    after = MagicMock(channel=None)

    await cog.on_voice_state_update(member, before, after)
    first_timer = cog.empty_channel_timers[guild.id]

    # Another user leaves -> event fires again but must NOT reset the clock
    await cog.on_voice_state_update(member, before, after)
    assert cog.empty_channel_timers[guild.id] is first_timer


@pytest.mark.asyncio
async def test_empty_channel_sweep_starts_and_skips_timers(cog):
    alone_guild = MagicMock()
    alone_guild.id = 111
    vc_alone = MagicMock()
    vc_alone.is_connected.return_value = True
    vc_alone.channel.members = [MagicMock(bot=True)]
    alone_guild.voice_client = vc_alone

    busy_guild = MagicMock()
    busy_guild.id = 222
    vc_busy = MagicMock()
    vc_busy.is_connected.return_value = True
    vc_busy.channel.members = [MagicMock(bot=True), MagicMock(bot=False)]
    busy_guild.voice_client = vc_busy

    cog.bot.guilds = [alone_guild, busy_guild]

    await cog._sweep_empty_channels()

    assert alone_guild.id in cog.empty_channel_timers
    assert busy_guild.id not in cog.empty_channel_timers


@pytest.mark.asyncio
async def test_countdown_cleanup_fires_and_pop_guard(cog):
    real_loop = asyncio.get_running_loop()
    cog.bot.loop = real_loop
    cog.cleanup = AsyncMock()

    with patch("asyncio.sleep", new=AsyncMock(return_value=None)):
        # Fire-through: countdown completes -> cleanup once, own entry removed
        g1 = MagicMock()
        g1.id = 700
        g1.voice_client = MagicMock()
        g1.voice_client.channel.members = [MagicMock(bot=True)]
        cog._start_empty_channel_timer(g1)
        t1 = cog.empty_channel_timers[g1.id]
        await t1
        cog.cleanup.assert_awaited_once_with(g1)
        assert g1.id not in cog.empty_channel_timers

        # Eviction guard: a cancelled/superseded task must not evict its replacement
        g2 = MagicMock()
        g2.id = 701
        g2.voice_client = MagicMock()
        g2.voice_client.channel.members = [MagicMock(bot=True)]
        cog._start_empty_channel_timer(g2)
        stale = cog.empty_channel_timers[g2.id]
        sentinel = object()
        cog.empty_channel_timers[g2.id] = sentinel  # simulate replacement
        await stale  # stale finishes; its finally must keep the sentinel
        assert cog.empty_channel_timers[g2.id] is sentinel


@pytest.mark.asyncio
async def test_shutdown_cleanup_cleans_every_guild_player(cog, interaction):
    cog.get_player(interaction)  # guild 12345

    other_guild = MagicMock()
    other_guild.id = 9999
    cog.players[other_guild.id] = MagicMock(guild=other_guild)

    cog.cleanup = AsyncMock()
    await cog.shutdown_cleanup()

    assert cog.cleanup.await_count == 2
    cog.cleanup.assert_any_await(interaction.guild)
    cog.cleanup.assert_any_await(other_guild)


@pytest.mark.asyncio
async def test_play_clears_thinking_placeholder(cog, interaction):
    with patch(
        "music_cog.YTDLSource.extract_info",
        return_value={"title": "T", "webpage_url": "http://yt.com/x"},
    ):
        await cog.play.callback(cog, interaction, url="http://yt.com/x")
    interaction.delete_original_response.assert_awaited_once()


@pytest.mark.asyncio
async def test_orphan_sweep_deletes_leftovers_and_updates_index(cog):
    # Channel must be a MagicMock: get_partial_message() is synchronous.
    # An AsyncMock would turn it into a coroutine and break delete_message_safe.
    channel = MagicMock()
    partial = AsyncMock()
    channel.get_partial_message.return_value = partial

    stale = {"channel_id": 42, "message_id": 9001}
    keep = {"channel_id": 42, "message_id": 9002}
    cog.orphan_index = {"12345": [stale, dict(keep)]}
    cog._save_orphan_index = MagicMock()
    cog.bot.get_channel = MagicMock(return_value=channel)

    # First delete succeeds, second fails -> stays indexed
    partial.delete.side_effect = [None, RuntimeError("boom")]

    await cog._sweep_orphans()

    assert stale not in cog.orphan_index["12345"]
    assert keep in cog.orphan_index["12345"]
    assert channel.get_partial_message.call_count == 2


def test_track_and_untrack_message_roundtrip(cog, interaction):
    msg = MagicMock()
    msg.channel.id = 777
    msg.id = 888

    cog.track_message(interaction.guild.id, msg)
    assert {"channel_id": 777, "message_id": 888} in cog.orphan_index[str(interaction.guild.id)]

    cog.untrack_message(interaction.guild.id, msg)
    assert str(interaction.guild.id) not in cog.orphan_index


def test_should_prime_gating(cog, interaction):
    import time as _time

    player = cog.get_player(interaction)

    # Fresh player (never played) counts as idle -> prime
    assert player.last_active_ts == 0.0
    assert player._should_prime() is True

    # Recent activity -> no priming needed
    player.last_active_ts = _time.time()
    assert player._should_prime() is False

    # Stale activity beyond threshold -> prime
    player.last_active_ts = _time.time() - 5
    assert player._should_prime() is True









