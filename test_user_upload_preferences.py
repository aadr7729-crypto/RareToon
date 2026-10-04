import asyncio
import io
import os
import sqlite3
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import database
import handlers
import video_downloader
from PIL import Image
from telegram_uploader import format_upload_filename, upload_video


class UserPreferenceDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.mongo_patch = patch.object(database, "_mongo", return_value=None)
        self.path_patch = patch.object(
            database, "DATABASE_PATH", os.path.join(self.temp_dir.name, "prefs.db")
        )
        self.mongo_patch.start()
        self.path_patch.start()
        database.init_db()

    def tearDown(self):
        self.path_patch.stop()
        self.mongo_patch.stop()
        self.temp_dir.cleanup()

    def test_preferences_are_isolated_and_can_be_cleared_independently(self):
        database.set_user_metadata_prefix(101, "@shivam_anime")
        database.set_user_thumbnail(101, b"jpeg-data")
        database.set_user_metadata_prefix(202, "@another_user")

        self.assertEqual(
            database.get_user_preferences(101),
            {"metadata_prefix": "@shivam_anime", "thumbnail": b"jpeg-data"},
        )
        self.assertEqual(
            database.get_user_preferences(202),
            {"metadata_prefix": "@another_user", "thumbnail": None},
        )

        database.clear_user_metadata_prefix(101)
        self.assertIsNone(
            database.get_user_preferences(101)["metadata_prefix"]
        )
        self.assertEqual(
            database.get_user_preferences(101)["thumbnail"], b"jpeg-data"
        )

    def test_tracked_anime_records_the_user_who_started_it(self):
        database.add_tracked(
            "Solo Leveling", "season-url", "episode-url", "hindi",
            "1", 1, "-100123", owner_id=101,
        )
        self.assertEqual(database.list_tracked()[0]["owner_id"], 101)

    def test_legacy_private_tracks_are_migrated_without_breaking_upload_migration(self):
        legacy_path = os.path.join(self.temp_dir.name, "legacy.db")
        conn = sqlite3.connect(legacy_path)
        conn.executescript(
            """
            CREATE TABLE tracked_anime (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                anime_name TEXT, season_page_url TEXT, multiquality_url TEXT,
                language TEXT, last_episode TEXT, last_season INTEGER,
                status TEXT DEFAULT 'ongoing', channel_id TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(anime_name, language)
            );
            INSERT INTO tracked_anime (anime_name, language, channel_id)
            VALUES ('Solo Leveling', 'hindi', '101');
            CREATE TABLE uploaded_episodes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                anime_name TEXT, season_num INTEGER, episode_num INTEGER,
                language TEXT, quality TEXT, file_hash TEXT, message_id INTEGER,
                uploaded_at TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(anime_name, season_num, episode_num, language, quality)
            );
            """
        )
        conn.commit()
        conn.close()

        with patch.object(database, "DATABASE_PATH", legacy_path):
            database.init_db()
            tracked = database.list_tracked()

        self.assertEqual(tracked[0]["owner_id"], 101)


class UserUploadPreferenceTests(unittest.IsolatedAsyncioTestCase):
    def test_metadata_filename_matches_requested_format(self):
        self.assertEqual(
            format_upload_filename(
                "Solo_Leveling_S01E01_480P.mp4",
                "@shivam_anime",
                "Solo Leveling",
                1,
                1,
                "480P",
            ),
            "@shivam_animes_Solo_Leveling_S01_EP01_480p.mp4",
        )
        self.assertEqual(
            format_upload_filename(
                "Solo_Leveling_S01E01_480P.mp4", None,
                "Solo Leveling", 1, 1, "480P",
            ),
            "Solo_Leveling_S01E01_480P.mp4",
        )

    def test_thumbnail_is_compressed_to_telegram_dimensions_and_jpeg(self):
        source = io.BytesIO()
        Image.new("RGB", (1200, 800), color="purple").save(source, format="PNG")
        thumbnail = handlers._prepare_telegram_thumbnail(source.getvalue())

        with Image.open(io.BytesIO(thumbnail)) as image:
            self.assertEqual(image.format, "JPEG")
            self.assertLessEqual(max(image.size), 320)
        self.assertLessEqual(len(thumbnail), 190_000)

    async def test_quality_fetch_refreshes_until_a_supported_quality_is_found(self):
        progress = SimpleNamespace()
        link = SimpleNamespace(quality="480P")
        control = SimpleNamespace(check=lambda: None)
        with (
            patch.object(
                handlers.scraper, "get_all_download_urls",
                side_effect=[([], {}), ([link], {"episode_title": "episode"})],
            ) as fetch,
            patch.object(handlers, "_edit_progress_message", new=AsyncMock()),
            patch.object(handlers, "QUALITY_POLL_SECONDS", 0),
        ):
            links, metadata = await handlers._wait_for_target_quality(
                "episode-url", progress, "S01E01", control
            )

        self.assertEqual(links, [link])
        self.assertEqual(metadata["episode_title"], "episode")
        self.assertEqual(fetch.call_count, 2)

    async def test_failed_quality_download_refreshes_its_source_link(self):
        progress_message = SimpleNamespace()
        progress = SimpleNamespace(
            message=progress_message, report_percent=Mock()
        )
        initial_link = SimpleNamespace(quality="720P HD", url="old-url")
        refreshed_link = SimpleNamespace(quality="720P", url="new-url")
        control = SimpleNamespace(check=lambda: None, is_cancelled=lambda: False)
        with (
            patch.object(
                handlers.scraper, "get_all_download_urls",
                return_value=([refreshed_link], {}),
            ) as fetch,
            patch.object(
                handlers.video_downloader, "download_video",
                side_effect=[None, "/tmp/ready.mp4"],
            ) as download,
            patch.object(handlers.video_downloader, "cleanup_file"),
            patch.object(handlers, "_edit_progress_message", new=AsyncMock()),
            patch.object(handlers, "QUALITY_POLL_SECONDS", 0),
        ):
            result = await handlers._download_quality_until_ready(
                "episode-url", initial_link, "/tmp/episode.mp4",
                progress, "S01E01", control,
            )

        self.assertEqual(result, "/tmp/ready.mp4")
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(download.call_args_list[0].args[0], "old-url")
        self.assertEqual(download.call_args_list[1].args[0], "new-url")

    async def test_bot_api_receives_user_thumbnail_when_configured(self):
        with tempfile.NamedTemporaryFile() as video:
            video.write(b"video")
            video.flush()
            bot = SimpleNamespace(send_video=AsyncMock(
                return_value=SimpleNamespace(message_id=55)
            ))
            with (
                patch("telegram_uploader._get_mtproto_client", new=AsyncMock(return_value=None)),
                patch("telegram_uploader.get_video_metadata", return_value={
                    "duration": 1, "width": 320, "height": 240,
                }),
            ):
                receipt = await upload_video(
                    bot, 123, video.name, "caption", "custom.mp4",
                    thumbnail=b"jpeg-thumbnail",
                )

        self.assertEqual(receipt.message_id, 55)
        sent_thumbnail = bot.send_video.await_args.kwargs["thumbnail"]
        self.assertEqual(sent_thumbnail.filename, "thumbnail.jpg")

    async def test_mtproto_receives_user_thumbnail_and_custom_filename(self):
        with tempfile.NamedTemporaryFile() as video:
            video.write(b"video")
            video.flush()
            client = SimpleNamespace(
                get_chat=AsyncMock(return_value=SimpleNamespace(id=123)),
                send_video=AsyncMock(return_value=SimpleNamespace(id=77)),
            )
            bot = SimpleNamespace()
            with (
                patch("telegram_uploader._get_mtproto_client", new=AsyncMock(return_value=client)),
                patch("telegram_uploader.get_video_metadata", return_value={
                    "duration": 1421, "width": 1920, "height": 1080,
                }),
            ):
                receipt = await upload_video(
                    bot, 123, video.name, "caption", "custom.mp4",
                    thumbnail=b"jpeg-thumbnail",
                )

        self.assertEqual(receipt.message_id, 77)
        call = client.send_video.await_args
        self.assertEqual(call.kwargs["thumb"].read(), b"jpeg-thumbnail")
        self.assertEqual(call.kwargs["file_name"], "custom.mp4")
        self.assertEqual(call.kwargs["duration"], 1421)
        self.assertEqual((call.kwargs["width"], call.kwargs["height"]), (1920, 1080))

    def test_html_error_page_is_not_treated_as_a_video_download(self):
        response = SimpleNamespace(
            status_code=200,
            headers={"content-type": "text/html"},
            close=Mock(),
        )
        session = SimpleNamespace(
            headers=SimpleNamespace(update=Mock()),
            get=Mock(return_value=response),
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = os.path.join(temp_dir, "episode.mp4")
            with patch.object(video_downloader.requests, "Session", return_value=session):
                result = video_downloader.download_video("episode-url", output_path)

        self.assertIsNone(result)
        self.assertFalse(os.path.exists(output_path))
        response.close.assert_called_once()