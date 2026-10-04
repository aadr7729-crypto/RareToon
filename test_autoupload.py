import asyncio
import json
import os
import sqlite3
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import database
import handlers
import scraper
import bot as bot_module
import autoupload_handlers as autoupload_ui
import autoupload_service as autoupload
import scheduler as scheduler_module
from autoupload_service import _sync_channel_unlocked


class AutoUploadDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.mongo_patch = patch.object(database, "_mongo", return_value=None)
        self.path_patch = patch.object(
            database, "DATABASE_PATH", os.path.join(self.temp_dir.name, "auto.db")
        )
        self.mongo_patch.start()
        self.path_patch.start()
        database.init_db()

    def tearDown(self):
        self.path_patch.stop()
        self.mongo_patch.stop()
        self.temp_dir.cleanup()

    def test_auto_settings_channels_and_upload_ledger_are_isolated(self):
        database.set_config("default_quality", "480P")
        database.add_tracked(
            "Existing Series", "source", None, "hindi", "1", 1, "-100111"
        )
        database.set_autoupload_setting("quality", "1080P")
        database.set_autoupload_setting("languages", json.dumps(["hindi", "tamil"]))
        database.add_autoupload_channel(
            "-100222", "Example [Hindi]", "Example", "hindi",
            ["https://example.test/season"], genres=["Action"],
        )

        database.record_autoupload_episode(
            "-100222", "Example", 1, 4, "hindi", "1080P", 99
        )

        self.assertEqual(database.get_config("default_quality"), "480P")
        self.assertEqual(database.get_autoupload_setting("quality"), "1080P")
        self.assertEqual(
            database.get_autoupload_setting("languages"),
            '["hindi", "tamil"]',
        )
        self.assertEqual(
            [item["anime_name"] for item in database.list_tracked()],
            ["Existing Series"],
        )
        self.assertEqual(
            [item["anime_name"] for item in database.list_autoupload_channels()],
            ["Example"],
        )
        self.assertTrue(
            database.is_autoupload_episode_uploaded(
                "-100222", 1, 4, "hindi", "1080P"
            )
        )
        self.assertFalse(
            database.is_episode_uploaded(
                "Example", 1, 4, "hindi", "1080P", "-100222"
            )
        )

    def test_manual_thumbnail_survives_mapping_refresh(self):
        database.add_autoupload_channel(
            "-100222", "Example", "Example", "hindi",
            ["https://example.test/season"],
        )
        database.set_autoupload_channel_thumbnail("-100222", b"image-bytes", "file-id")
        database.add_autoupload_channel(
            "-100222", "Example", "Example", "hindi",
            ["https://example.test/season-2"],
        )
        channel = database.get_autoupload_channel("-100222")
        self.assertEqual(channel["thumbnail"], b"image-bytes")
        self.assertEqual(channel["thumbnail_file_id"], "file-id")
        self.assertEqual(channel["season_page_urls"], ["https://example.test/season-2"])

    def test_main_post_image_is_separate_from_video_thumbnail(self):
        database.add_autoupload_channel(
            "-100222", "Example", "Example", "hindi",
            ["https://example.test/season"],
        )
        database.set_autoupload_channel_thumbnail(
            "-100222", b"video-thumbnail", "video-thumbnail-id"
        )
        database.set_autoupload_channel_main_post_image(
            "-100222", b"main-poster", "main-poster-id"
        )

        channel = database.get_autoupload_channel("-100222")

        self.assertEqual(channel["thumbnail"], b"video-thumbnail")
        self.assertEqual(channel["thumbnail_file_id"], "video-thumbnail-id")
        self.assertEqual(channel["main_post_image"], b"main-poster")
        self.assertEqual(channel["main_post_image_file_id"], "main-poster-id")

    def test_existing_database_gets_main_post_image_columns(self):
        legacy_path = os.path.join(self.temp_dir.name, "legacy.db")
        connection = sqlite3.connect(legacy_path)
        connection.execute(
            """CREATE TABLE autoupload_channels (
                channel_id TEXT PRIMARY KEY,
                channel_title TEXT,
                anime_name TEXT NOT NULL,
                language TEXT NOT NULL,
                season_page_urls TEXT NOT NULL DEFAULT '[]',
                thumbnail BLOB,
                thumbnail_file_id TEXT,
                genres TEXT NOT NULL DEFAULT '[]',
                invite_link TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )"""
        )
        connection.execute(
            """INSERT INTO autoupload_channels
               (channel_id, channel_title, anime_name, language)
               VALUES (?, ?, ?, ?)""",
            ("-100222", "Example", "Example", "hindi"),
        )
        connection.commit()
        connection.close()

        with (
            patch.object(database, "DATABASE_PATH", legacy_path),
            patch.object(database, "_mongo", return_value=None),
        ):
            database.init_db()
            channel = database.get_autoupload_channel("-100222")
            self.assertIsNone(channel["main_post_image"])
            self.assertIsNone(channel["main_post_image_file_id"])
            self.assertTrue(database.set_autoupload_channel_main_post_image(
                "-100222", b"poster", "poster-id"
            ))
            channel = database.get_autoupload_channel("-100222")

        self.assertEqual(channel["main_post_image"], b"poster")
        self.assertEqual(channel["main_post_image_file_id"], "poster-id")

    def test_initially_skipped_episodes_are_saved_per_channel(self):
        database.set_autoupload_ignored_episodes(
            "-100222", {(1, 1), (1, 2), (2, 1)}
        )
        self.assertEqual(
            database.get_autoupload_ignored_episodes("-100222"),
            {(1, 1), (1, 2), (2, 1)},
        )

    def test_legacy_quality_labels_still_match_uploaded_episode_ledgers(self):
        connection = database._get()
        connection.execute(
            """INSERT INTO autoupload_uploaded_episodes
               (channel_id, anime_name, season_num, episode_num, language, quality)
               VALUES (?, ?, ?, ?, ?, ?)""",
            ("-100222", "Example", 1, 4, "hindi", "720p HD"),
        )
        connection.execute(
            """INSERT INTO uploaded_episodes
               (anime_name, season_num, episode_num, language, quality, channel_id)
               VALUES (?, ?, ?, ?, ?, ?)""",
            ("Tracked", 1, 4, "hindi", "360p BluRay", "-100222"),
        )
        connection.commit()
        connection.close()

        self.assertTrue(
            database.is_autoupload_episode_uploaded(
                "-100222", 1, 4, "hindi", "720P"
            )
        )
        self.assertTrue(
            database.is_episode_uploaded(
                "Tracked", 1, 4, "hindi", "360P", "-100222"
            )
        )


class AutoUploadParsingTests(unittest.TestCase):
    def test_destination_is_checked_before_fetching_episode_pages(self):
        async def run():
            bot = Mock()
            progress = Mock(status=AsyncMock(), close=AsyncMock())
            with (
                patch.object(
                    autoupload._OwnerProgress,
                    "start",
                    new=AsyncMock(return_value=progress),
                ),
                patch(
                    "autoupload_service.validate_channel",
                    new=AsyncMock(return_value=SimpleNamespace(id="-100222")),
                ) as validate,
                patch(
                    "autoupload_service.prepare_upload_destination",
                    new=AsyncMock(side_effect=RuntimeError("unavailable")),
                ) as prepare,
                patch(
                    "autoupload_service.scraper.get_season_episodes",
                ) as read_pages,
            ):
                with self.assertRaisesRegex(RuntimeError, "unavailable"):
                    await _sync_channel_unlocked(
                        bot,
                        {
                            "channel_id": "-100222",
                            "anime_name": "Example",
                            "language": "hindi",
                        },
                    )

            validate.assert_awaited_once_with(bot, "-100222")
            prepare.assert_awaited_once_with("-100222", bot=bot)
            read_pages.assert_not_called()

        asyncio.run(run())

    def test_lost_channel_access_pauses_tracking_without_repeated_errors(self):
        async def run():
            bot = Mock()
            progress = Mock(status=AsyncMock(), close=AsyncMock())
            with (
                patch.object(
                    autoupload._OwnerProgress,
                    "start",
                    new=AsyncMock(return_value=progress),
                ),
                patch(
                    "autoupload_service.validate_channel",
                    new=AsyncMock(
                        side_effect=autoupload.ChannelAccessError(
                            "The bot was removed from the channel."
                        )
                    ),
                ),
                patch(
                    "autoupload_service.prepare_upload_destination",
                    new=AsyncMock(),
                ) as prepare,
                patch.object(autoupload.db, "set_autoupload_setting") as save_status,
            ):
                result = await _sync_channel_unlocked(
                    bot,
                    {
                        "channel_id": "-100222",
                        "anime_name": "Example",
                        "language": "hindi",
                    },
                )

            self.assertEqual(result, 0)
            save_status.assert_called_once_with(
                "status:-100222", "paused_access"
            )
            prepare.assert_not_awaited()

        asyncio.run(run())

    def test_anime_catalog_only_includes_hindi_dub_episodes(self):
        async def run():
            async def fake_to_thread(function, *args):
                if function is scraper.get_anime_page_info:
                    return {
                        "title": "Overgear",
                        "status": "ongoing",
                        "languages": ["hindi"],
                        "categories": ["Anime", "Ongoing"],
                        "all_season_links": [{
                            "url": "https://example.test/season-2",
                        }],
                    }
                if args[0].endswith("season-2"):
                    return {"episodes": [
                        scraper.EpisodeInfo(
                            2, 1, "https://example.test/ep2",
                            audio_variant="hindi_dub",
                        ),
                    ], "all_seasons": []}
                return {"episodes": [
                    scraper.EpisodeInfo(
                        1, 1, "https://example.test/ep1",
                        audio_variant="hindi_dub",
                    ),
                    scraper.EpisodeInfo(
                        1, 2, "https://example.test/sub",
                        audio_variant="hindi_sub",
                    ),
                ], "all_seasons": []}

            with patch.object(autoupload.asyncio, "to_thread", new=fake_to_thread):
                catalog = await autoupload.load_anime_catalog({
                    "title": "Overgear",
                    "url": "https://example.test/season-1",
                })

            self.assertEqual(catalog["anime_name"], "Overgear")
            self.assertEqual(
                [(item["season"], item["episode"]) for item in catalog["episodes"]],
                [(1, 1), (2, 1)],
            )
            self.assertEqual(len(catalog["season_page_urls"]), 2)

        asyncio.run(run())

    def test_ongoing_episode_menu_has_backfill_and_future_only_actions(self):
        pending = {
            "catalog": {
                "episodes": [{"season": 1, "episode": number} for number in range(1, 3)],
            },
            "status": "ongoing",
            "selected": set(),
        }
        markup = __import__("autoupload_handlers")._episode_keyboard(pending, 0)
        callbacks = [
            button.callback_data
            for row in markup.inline_keyboard
            for button in row
        ]
        self.assertIn("autoupload:download_selected", callbacks)
        self.assertIn("autoupload:backfill_track", callbacks)
        self.assertIn("autoupload:skip_track", callbacks)
        self.assertIn("autoupload:back_status", callbacks)

    def test_auto_upload_always_attempts_the_fixed_quality_set(self):
        async def run():
            channel = {"channel_id": "-100222", "anime_name": "Example"}
            episode = {
                "season": 1,
                "episode": 2,
                "download_url": "https://example.test/episode",
            }
            progress = Mock(
                status=AsyncMock(),
                is_cancelled=Mock(return_value=False),
            )
            with (
                patch.object(
                    autoupload.asyncio, "to_thread",
                    new=AsyncMock(return_value=([
                        SimpleNamespace(quality=quality, url=quality)
                        for quality in autoupload.AUTUPLOAD_QUALITIES
                    ], {})),
                ),
                patch.object(
                    autoupload.db, "is_autoupload_episode_uploaded",
                    return_value=False,
                ),
                patch.object(
                    autoupload, "_upload_one",
                    new=AsyncMock(return_value=True),
                ) as upload_one,
            ):
                result = await autoupload._upload_batch(
                    Mock(), channel, [episode], progress
                )

            self.assertEqual(result, (3, 0, 0))
            self.assertEqual(
                [call.args[4] for call in upload_one.await_args_list],
                ["360P", "720P", "1080P"],
            )

        asyncio.run(run())

    def test_auto_upload_waits_for_each_missing_quality(self):
        async def run():
            channel = {"channel_id": "-100222", "anime_name": "Example"}
            episode = {
                "season": 2,
                "episode": 1,
                "download_url": "https://example.test/episode",
            }
            quality_sets = [
                ["360P"],
                ["360P", "720P"],
                ["360P", "720P", "1080P"],
            ]
            responses = [
                (
                    [SimpleNamespace(quality=q, url=q) for q in qualities],
                    {},
                )
                for qualities in quality_sets
            ]
            progress = Mock(
                status=AsyncMock(),
                is_cancelled=Mock(return_value=False),
            )
            with (
                patch.object(
                    autoupload.asyncio, "to_thread",
                    new=AsyncMock(side_effect=responses),
                ) as fetch,
                patch.object(
                    autoupload.asyncio, "sleep", new=AsyncMock()
                ) as sleep,
                patch.object(
                    autoupload.db, "is_autoupload_episode_uploaded",
                    return_value=False,
                ),
                patch.object(
                    autoupload, "_upload_one",
                    new=AsyncMock(return_value=True),
                ) as upload_one,
            ):
                result = await autoupload._upload_batch(
                    Mock(), channel, [episode], progress
                )

            self.assertEqual(result, (3, 0, 0))
            self.assertEqual(fetch.await_count, 3)
            self.assertEqual(sleep.await_args_list, [
                unittest.mock.call(autoupload.AUTO_QUALITY_POLL_SECONDS),
                unittest.mock.call(autoupload.AUTO_QUALITY_POLL_SECONDS),
            ])
            self.assertEqual(
                [call.args[4] for call in upload_one.await_args_list],
                ["360P", "720P", "1080P"],
            )
            self.assertEqual(
                [
                    [link.quality for link in call.kwargs["links"]]
                    for call in upload_one.await_args_list
                ],
                [["360P"], ["360P", "720P"], ["360P", "720P", "1080P"]],
            )
            status_text = "\n".join(
                call.args[0] for call in progress.status.await_args_list
            )
            self.assertIn("waiting for 720P, 1080P", status_text)
            self.assertIn("waiting for 1080P", status_text)

        asyncio.run(run())

    def test_auto_upload_can_be_cancelled_while_waiting_for_quality(self):
        async def run():
            cancelled = False

            async def stop_during_wait(_seconds):
                nonlocal cancelled
                cancelled = True

            progress = Mock(
                status=AsyncMock(),
                is_cancelled=Mock(side_effect=lambda: cancelled),
            )
            with (
                patch.object(
                    autoupload.asyncio, "to_thread",
                    new=AsyncMock(return_value=([
                        SimpleNamespace(quality="360P", url="360P"),
                    ], {})),
                ) as fetch,
                patch.object(
                    autoupload.asyncio, "sleep",
                    new=AsyncMock(side_effect=stop_during_wait),
                ),
                patch.object(
                    autoupload.db, "is_autoupload_episode_uploaded",
                    return_value=False,
                ),
                patch.object(
                    autoupload, "_upload_one", new=AsyncMock(return_value=True)
                ),
            ):
                with self.assertRaises(asyncio.CancelledError):
                    await autoupload._upload_batch(
                        Mock(),
                        {"channel_id": "-100222", "anime_name": "Example"},
                        [{
                            "season": 1,
                            "episode": 1,
                            "download_url": "https://example.test/episode",
                        }],
                        progress,
                    )

            fetch.assert_awaited_once()

        asyncio.run(run())

    def test_auto_upload_quality_selection_does_not_substitute_another_quality(self):
        links = [SimpleNamespace(quality="480P", url="480")]
        self.assertIsNone(autoupload._select_quality(links, "720P"))

    def test_main_announcement_uses_the_saved_anime_image(self):
        async def run():
            bot = Mock(send_photo=AsyncMock(
                return_value=SimpleNamespace(message_id=99)
            ))
            channel = {
                "channel_id": "-100222",
                "anime_name": "Example",
                "thumbnail_file_id": "video-thumbnail",
                "thumbnail": b"video-thumbnail",
                "main_post_image_file_id": "saved-main-poster",
                "main_post_image": b"main-poster",
                "episode_count": 12,
                "genres": ["Drama", "Romance"],
                "site_info": {
                    "ott_name": "Crunchyroll",
                    "ott_url": "https://www.crunchyroll.com/search?q=Example",
                },
            }
            episode = {"season": 2, "episode": 5}
            with (
                patch.object(
                    autoupload.db, "get_autoupload_setting",
                    side_effect=lambda key, default=None: (
                        "-100999" if key == "main_channel_id" else default
                    ),
                ),
                patch.object(
                    autoupload.db, "claim_autoupload_announcement",
                    return_value=True,
                ),
                patch.object(
                    autoupload, "_channel_episode_link",
                    new=AsyncMock(return_value="https://t.me/example/99"),
                ),
                patch.object(autoupload.db, "record_autoupload_announcement") as record,
            ):
                await autoupload._announce_episode(
                    bot, channel, episode, "720P", 42
                )

            bot.send_photo.assert_awaited_once()
            kwargs = bot.send_photo.await_args.kwargs
            self.assertEqual(kwargs["photo"], "saved-main-poster")
            self.assertIn("Example", kwargs["caption"])
            self.assertIn("Episodes :- 12", kwargs["caption"])
            self.assertIn("Genre :- Drama, Romance", kwargs["caption"])
            self.assertIn("Episode 5", kwargs["caption"])
            self.assertEqual(
                [entity.url for entity in kwargs["caption_entities"]],
                [
                    "https://www.crunchyroll.com/search?q=Example",
                    "https://t.me/example/99",
                ],
            )
            record.assert_called_once()

        asyncio.run(run())

    def test_default_post_caption_matches_the_requested_layout(self):
        with patch.object(
            autoupload.db, "get_autoupload_setting",
            return_value=autoupload.DEFAULT_ANNOUNCEMENT_CAPTION,
        ):
            caption = autoupload._format_announcement_caption(
                {
                    "anime_name": "Ranma ½",
                    "episode_count": 12,
                    "genres": ["Drama", "Romance", "Comedy", "Slice_Of_Life"],
                    "site_info": {
                        "status": "ongoing",
                        "episode_count": "Ongoing",
                        "quality_labels": "480P, 720P, 1080P [DUAL]",
                        "genres": ["Drama", "Romance", "Comedy", "Slice_Of_Life"],
                        "ott_name": "Crunchyroll",
                        "ott_url": "https://www.crunchyroll.com/search?q=Ranma",
                    },
                },
                {"season": 3, "episode": 1},
                "720P",
                "https://t.me/example/42",
                qualities="480P, 720P, 1080P, 2160P",
            )

        self.assertIn("Ranma ½", caption)
        self.assertIn("├Season :- 03", caption)
        self.assertIn("├Episodes :- Ongoing", caption)
        self.assertIn("├Quality :- 480P, 720P, 1080P [DUAL]", caption)
        self.assertIn("├Language :- Hindi Dub [#OFFICIAL]", caption)
        self.assertIn("├Genre :- Drama, Romance, Comedy, Slice_Of_Life", caption)
        self.assertIn("[ Credit - Crunchyroll ] Entertainment", caption)
        self.assertIn("Episode 1", caption)

    def test_default_caption_prompts_for_a_missing_main_post_image(self):
        async def run():
            user_id = 123
            message = SimpleNamespace(reply_text=AsyncMock())
            query = SimpleNamespace(
                data="autoupload:reset_announcement_caption",
                from_user=SimpleNamespace(id=user_id),
                message=message,
                answer=AsyncMock(),
            )
            update = SimpleNamespace(callback_query=query)
            with (
                patch.object(autoupload_ui, "_pending_inputs", {}) as pending,
                patch.object(autoupload_ui, "_is_admin", return_value=True),
                patch.object(autoupload_ui.db, "set_autoupload_setting") as save,
                patch.object(
                    autoupload_ui.db,
                    "list_autoupload_channels",
                    return_value=[{
                        "channel_id": "-100222",
                        "anime_name": "Example",
                        "main_post_image": None,
                        "main_post_image_file_id": None,
                    }],
                ),
            ):
                await autoupload_ui.cb_autoupload(update, Mock())

            save.assert_called_once_with(
                "announcement_caption_template",
                autoupload.DEFAULT_ANNOUNCEMENT_CAPTION,
            )
            self.assertEqual(pending[user_id]["kind"], "main_post_image")
            self.assertIn(
                "separate from its video thumbnail",
                message.reply_text.await_args.args[0],
            )

        asyncio.run(run())

    def test_default_caption_does_not_request_an_already_saved_image_again(self):
        async def run():
            user_id = 123
            message = SimpleNamespace(reply_text=AsyncMock())
            query = SimpleNamespace(
                data="autoupload:reset_announcement_caption",
                from_user=SimpleNamespace(id=user_id),
                message=message,
                answer=AsyncMock(),
            )
            update = SimpleNamespace(callback_query=query)
            channels = [{
                "channel_id": "-100222",
                "anime_name": "Example",
                "main_post_image": b"poster",
                "main_post_image_file_id": "saved-poster",
            }]
            with (
                patch.object(autoupload_ui, "_pending_inputs", {}) as pending,
                patch.object(autoupload_ui, "_is_admin", return_value=True),
                patch.object(autoupload_ui.db, "set_autoupload_setting"),
                patch.object(
                    autoupload_ui.db, "list_autoupload_channels",
                    return_value=channels,
                ),
            ):
                await autoupload_ui.cb_autoupload(update, Mock())

            self.assertNotIn(user_id, pending)
            self.assertIn(
                "No image is needed again",
                message.reply_text.await_args.args[0],
            )

        asyncio.run(run())

    def test_main_post_photo_is_saved_without_replacing_video_thumbnail(self):
        async def run():
            user_id = 123
            pending_data = {
                "kind": "main_post_image",
                "channel_id": "-100222",
                "anime_name": "Example",
                "setup_missing": False,
            }
            photo = SimpleNamespace(file_id="main-poster-file")
            message = SimpleNamespace(
                photo=[photo],
                reply_text=AsyncMock(),
            )
            update = SimpleNamespace(
                effective_user=SimpleNamespace(id=user_id),
                effective_message=message,
            )
            telegram_file = SimpleNamespace(
                download_as_bytearray=AsyncMock(return_value=b"main-poster-bytes")
            )
            context = SimpleNamespace(
                bot=SimpleNamespace(get_file=AsyncMock(return_value=telegram_file))
            )
            with (
                patch.object(autoupload_ui, "_pending_inputs", {user_id: pending_data}),
                patch.object(autoupload_ui, "_is_admin", return_value=True),
                patch.object(
                    autoupload_ui.db,
                    "get_autoupload_channel",
                    return_value={"channel_id": "-100222", "anime_name": "Example"},
                ),
                patch.object(
                    autoupload_ui.db,
                    "set_autoupload_channel_main_post_image",
                    return_value=True,
                ) as save_main_image,
                patch.object(autoupload_ui.db, "set_autoupload_channel_thumbnail") as save_thumb,
            ):
                await autoupload_ui.handle_autoupload_photo(update, context)

            save_main_image.assert_called_once_with(
                "-100222", b"main-poster-bytes", "main-poster-file"
            )
            save_thumb.assert_not_called()
            context.bot.get_file.assert_awaited_once_with("main-poster-file")

        asyncio.run(run())

    def test_owner_progress_has_a_cancel_button(self):
        async def run():
            message = Mock(edit_text=AsyncMock())
            bot = Mock(send_message=AsyncMock(return_value=message))
            with patch.object(autoupload, "OWNER_ID", 123):
                progress = await autoupload._OwnerProgress(bot).start("Starting")
                markup = bot.send_message.await_args.kwargs["reply_markup"]
                callback_data = markup.inline_keyboard[0][0].callback_data
                await progress.transfer_start("Example", "S01E01", "360P", "download")
                self.assertTrue(callback_data.startswith("autoupload:stop:"))
                self.assertTrue(progress.request_cancel())
                self.assertTrue(progress.is_cancelled())
                await progress.close()
                self.assertNotIn(progress.task_id, autoupload._owner_tasks)

        asyncio.run(run())

    def test_skip_action_uses_callback_message_and_starts_tracking(self):
        async def run():
            user_id = 123
            message = SimpleNamespace(
                reply_text=AsyncMock(),
                edit_text=AsyncMock(),
            )
            query = SimpleNamespace(
                from_user=SimpleNamespace(id=user_id),
                data="autoupload:skip_track",
                answer=AsyncMock(),
                message=message,
            )
            update = SimpleNamespace(callback_query=query)
            context = SimpleNamespace(bot=Mock(), application=Mock())
            channel = {
                "channel_id": "-100222",
                "anime_name": "Example",
                "thumbnail": b"stored-thumbnail",
            }
            pending = {
                "kind": "episode_selection",
                "channel_id": "-100222",
                "status": "ongoing",
                "catalog": {
                    "anime_name": "Example",
                    "episodes": [{"season": 1, "episode": 1}],
                },
                "selected": set(),
            }
            with (
                patch.object(autoupload_ui, "_pending_inputs", {user_id: pending}),
                patch.object(autoupload_ui, "_is_admin", return_value=True),
                patch.object(
                    autoupload,
                    "register_channel",
                    new=AsyncMock(return_value=channel),
                ),
                patch.object(
                    autoupload_ui.db, "set_autoupload_ignored_episodes"
                ) as save_ignored,
                patch.object(
                    autoupload_ui,
                    "_start_upload_task",
                    new=AsyncMock(),
                ) as start,
            ):
                await autoupload_ui.cb_autoupload(update, context)

            query.answer.assert_awaited_once()
            save_ignored.assert_called_once_with("-100222", {(1, 1)})
            start.assert_awaited_once_with(
                message, context, channel, [], "skip"
            )

        asyncio.run(run())

    def test_site_partial_language_release_is_classified_as_ongoing(self):
        self.assertTrue(
            handlers._is_language_partial_release("completed", "50", 10)
        )
        self.assertTrue(
            handlers._is_language_partial_release("completed", "50", 0)
        )
        self.assertFalse(
            handlers._is_language_partial_release("completed", 10, 10)
        )
        self.assertFalse(
            handlers._is_language_partial_release("completed", "unknown", 10)
        )
        self.assertFalse(
            handlers._is_language_partial_release("ongoing", 50, 10)
        )

    def test_completed_title_with_partial_language_release_shows_tracking_choices(self):
        class FakeMessage:
            text = None

            async def edit_text(self, text, **kwargs):
                self.text = text
                self.reply_markup = kwargs.get("reply_markup")

        class FakeQuery:
            def __init__(self):
                self.message = FakeMessage()

        query = FakeQuery()
        state = {
            "anime_info": {"title": "Tomb Raider King", "episode_count": 50}
        }

        async def load_episodes(*args, **kwargs):
            class Episode:
                def to_dict(self):
                    return {}

            return {"episodes": [Episode() for _ in range(10)]}

        with (
            patch.object(handlers.asyncio, "to_thread", new=load_episodes),
            patch.object(handlers.db, "get_config", return_value=""),
        ):
            asyncio.run(handlers._proceed_status(
                query, None, 123, state, "https://example.test/anime",
                "completed", "hindi",
            ))

        self.assertIn("10 of 50 site episodes", query.message.text)
        callbacks = [
            button.callback_data
            for row in query.message.reply_markup.inline_keyboard
            for button in row
        ]
        self.assertEqual(callbacks[:2], ["ongoing:backfill", "ongoing:future"])

    def test_ongoing_choice_buttons_are_routed_to_confirmation_handler(self):
        app = Mock()
        bot_module.register_handlers(app)
        registered_handlers = [
            call.args[0] for call in app.add_handler.call_args_list
        ]
        ongoing_handler = next(
            handler for handler in registered_handlers
            if getattr(handler, "callback", None) is handlers.cb_ongoing_confirm
        )

        self.assertRegex("ongoing:backfill", ongoing_handler.pattern)
        self.assertRegex("ongoing:future", ongoing_handler.pattern)

    def test_ongoing_backfill_has_episode_selection_pagination_and_back(self):
        async def run():
            user_id = 456
            state = {
                "anime_info": {"title": "Example"},
                "selected_language": "hindi",
                "selected_url": "https://example.test/anime",
                "episodes": [
                    {
                        "season": 1,
                        "episode": episode,
                        "download_url": f"https://example.test/{episode}",
                        "title": f"Episode {episode}",
                        "language": "hindi",
                        "episode_type": "episode",
                        "audio_variant": "",
                    }
                    for episode in range(1, 11)
                ],
            }
            message = SimpleNamespace(edit_text=AsyncMock())
            query = SimpleNamespace(
                from_user=SimpleNamespace(id=user_id),
                data="ongoing:backfill",
                answer=AsyncMock(),
                message=message,
            )
            with patch.object(handlers, "user_state", {user_id: state}):
                await handlers.cb_ongoing_confirm(
                    SimpleNamespace(callback_query=query), SimpleNamespace()
                )

            query.answer.assert_awaited_once()
            markup = message.edit_text.await_args.kwargs["reply_markup"]
            callbacks = [
                button.callback_data
                for row in markup.inline_keyboard
                for button in row
            ]
            self.assertIn("ongoing:toggle:0", callbacks)
            self.assertIn("ongoing:page:1", callbacks)
            self.assertIn("ongoing:selected", callbacks)
            self.assertIn("ongoing:all", callbacks)
            self.assertIn("ongoing:back_status", callbacks)

        asyncio.run(run())

    def test_ongoing_backfill_downloads_only_selected_episodes(self):
        async def run():
            user_id = 789
            state = {
                "anime_info": {"title": "Example"},
                "selected_language": "hindi",
                "selected_url": "https://example.test/anime",
                "ongoing_selected_episodes": {1},
                "episodes": [
                    {
                        "season": 1,
                        "episode": episode,
                        "download_url": f"https://example.test/{episode}",
                        "title": f"Episode {episode}",
                        "language": "hindi",
                        "episode_type": "episode",
                        "audio_variant": "",
                    }
                    for episode in (1, 2, 3)
                ],
            }
            query = SimpleNamespace(
                from_user=SimpleNamespace(id=user_id),
                data="ongoing:selected",
                answer=AsyncMock(),
                message=SimpleNamespace(),
            )
            start_tracking = AsyncMock()
            with (
                patch.object(handlers, "user_state", {user_id: state}),
                patch.object(
                    handlers, "_launch_ongoing_tracking", new=start_tracking
                ),
            ):
                await handlers.cb_ongoing_confirm(
                    SimpleNamespace(callback_query=query), SimpleNamespace()
                )

            query.answer.assert_awaited_once()
            selected = start_tracking.await_args.kwargs["selected_episodes"]
            self.assertEqual([episode.episode for episode in selected], [2])

        asyncio.run(run())

    def test_unselected_ongoing_episodes_are_saved_as_tracking_baseline(self):
        async def run():
            episodes = [
                SimpleNamespace(
                    season=1,
                    episode=episode,
                    download_url=f"https://example.test/{episode}",
                )
                for episode in (1, 2, 3)
            ]
            context = SimpleNamespace(
                bot=SimpleNamespace(send_message=AsyncMock())
            )

            async def load_episodes(*args, **kwargs):
                return {"episodes": episodes}

            with (
                patch.object(handlers.asyncio, "to_thread", new=load_episodes),
                patch.object(handlers.db, "add_tracked") as add_tracked,
                patch.object(
                    handlers,
                    "_wait_for_target_quality",
                    new=AsyncMock(return_value=({}, None)),
                ),
            ):
                await handlers._start_ongoing(
                    context,
                    987,
                    None,
                    "https://example.test/anime",
                    "Example",
                    "hindi",
                    "-100222",
                    selected_episodes=[episodes[1]],
                )

            add_tracked.assert_called_once()
            self.assertEqual(
                set(add_tracked.call_args.kwargs["baseline_episodes"]),
                {"1:1", "1:3"},
            )

        asyncio.run(run())

    def test_upload_selection_screens_include_back_buttons(self):
        episode = SimpleNamespace(
            season=1,
            episode=1,
            episode_type="episode",
            title="Episode 1",
        )

        def callbacks(markup):
            return [
                button.callback_data
                for row in markup.inline_keyboard
                for button in row
            ]

        self.assertIn(
            "upload:back_search",
            callbacks(handlers.status_keyboard("https://example.test/anime")),
        )
        self.assertIn(
            "upload:back_status",
            callbacks(handlers.language_keyboard(["hindi", "tamil"], "completed")),
        )
        self.assertIn(
            "upload:back_previous",
            callbacks(handlers.episode_keyboard([episode])),
        )
        self.assertIn(
            "upload:back_previous",
            callbacks(handlers.upload_ongoing_confirmation_keyboard(
                "Example", "hindi", "https://example.test/anime"
            )),
        )
        self.assertIn(
            "ongoing:back_status",
            callbacks(handlers.episode_keyboard(
                [episode], callback_prefix="ongoing",
                back_callback="ongoing:back_status",
            )),
        )

    def test_ongoing_catalog_parser_only_returns_post_articles(self):
        html = """
        <article>
          <h2><a href="https://www.rareanimes.mov/hindi/example-season-1/">
            Example Season 1 Hindi Dubbed Episodes Download HD
          </a></h2>
        </article>
        <article><h2><a href="/hindi/category/animes/">Animes</a></h2></article>
        """
        response = type("Response", (), {
            "status_code": 200,
            "text": html,
        })()
        session = type("Session", (), {
            "get": lambda self, *args, **kwargs: response,
        })()
        with patch.object(scraper, "_session", return_value=session), patch.object(
            database, "cache_get", return_value=None
        ), patch.object(database, "cache_set", return_value=None):
            entries = scraper.get_ongoing_anime_catalog("hindi", max_pages=1)

        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["title"].strip(), "Example Season 1 Hindi Dubbed Episodes Download HD")


class ScraperMetadataParsingTests(unittest.TestCase):
    def test_series_details_and_streaming_provider_are_extracted_from_post(self):
        page = scraper.BeautifulSoup(
            """
            <div class="entry-content">
              <p>✅ Download Overgeared Season 1 Crunchyroll Dual Audio
              WEB-DL HD in 480p &amp; 720p &amp; 1080p. It is a Crunchyroll
              Series and based on Animation, Sci-Fi &amp; Fantasy, and Action
              &amp; Adventure.</p>
              <p>🍂Season: <span>01</span></p>
              <p>🎞Episodes: <span>12</span></p>
              <p>🎭 Genre: <span>Animation, Sci-Fi &amp; Fantasy, and Action
              &amp; Adventure</span></p>
              <p>🎬Quality: <span>(480p, 720p, 1080p)</span></p>
            </div>
            """,
            "lxml",
        )

        info = scraper._extract_series_metadata(page)

        self.assertEqual(info["season_number"], 1)
        self.assertEqual(info["episode_count"], 12)
        self.assertEqual(info["quality_labels"], "480P, 720P, 1080P [DUAL]")
        self.assertEqual(
            info["genres"],
            ["Animation", "Sci-Fi & Fantasy", "Action & Adventure"],
        )
        self.assertEqual(info["ott_name"], "Crunchyroll")
        self.assertIn("crunchyroll.com/search", info["ott_url"])
        self.assertIn("Overgeared", info["ott_url"])

    def test_private_episode_link_uses_the_channel_invite_link(self):
        async def run():
            bot = Mock(
                get_chat=AsyncMock(
                    return_value=SimpleNamespace(
                        username=None,
                        invite_link="https://t.me/+example-invite",
                    )
                )
            )

            link = await autoupload._channel_episode_link(bot, "-100222", 42)

            self.assertEqual(link, "https://t.me/+example-invite")

        asyncio.run(run())

    def test_tracked_episode_selection_is_limited_to_three_default_qualities(self):
        links = [
            SimpleNamespace(quality=quality)
            for quality in ("480P", "360P Web-DL", "720p", "1080P HD")
        ]

        self.assertEqual(
            [link.quality for link in handlers._select_qualities(links)],
            ["360P Web-DL", "720p", "1080P HD"],
        )


class TrackedSchedulerTests(unittest.TestCase):
    def test_scheduled_track_uploads_each_supported_quality_and_records_it(self):
        async def run():
            episode = SimpleNamespace(
                season=1,
                episode=2,
                audio_variant="hindi_dub",
                download_url="https://example.test/episode-2",
            )
            links = [
                SimpleNamespace(quality="360p WEB-DL", size="80 MB", url="360"),
                SimpleNamespace(quality="480P", size="100 MB", url="480"),
                SimpleNamespace(quality="720p HDRip", size="150 MB", url="720"),
                SimpleNamespace(quality="1080P WEB-DL", size="250 MB", url="1080"),
            ]
            to_thread = AsyncMock(side_effect=[
                {"episodes": [episode]},
                (links, {}),
                True,
                True,
                True,
            ])
            uploads = AsyncMock(side_effect=[
                SimpleNamespace(message_id=101),
                SimpleNamespace(message_id=102),
                SimpleNamespace(message_id=103),
            ])
            bot = Mock(send_chat_action=AsyncMock())

            with (
                patch.object(scheduler_module.asyncio, "to_thread", new=to_thread),
                patch.object(
                    scheduler_module.db, "is_episode_uploaded", return_value=False
                ) as is_uploaded,
                patch.object(
                    scheduler_module.db, "claim_episode_upload", return_value=True
                ) as claim,
                patch.object(scheduler_module.db, "record_upload") as record,
                patch.object(scheduler_module.db, "release_episode_claim") as release,
                patch.object(scheduler_module.db, "get_user_preferences", return_value={}),
                patch.object(scheduler_module.db, "update_tracked_episode"),
                patch.object(
                    scheduler_module.video_downloader, "get_file_hash",
                    return_value="hash",
                ),
                patch.object(scheduler_module.video_downloader, "cleanup_file"),
                patch.object(scheduler_module, "upload_video", new=uploads),
                patch.object(
                    scheduler_module,
                    "notify_episode_uploaded",
                    new=AsyncMock(),
                ),
            ):
                await scheduler_module._check_anime_for_update(
                    bot,
                    {
                        "anime_name": "Example",
                        "language": "hindi",
                        "channel_id": "-100222",
                        "season_page_url": "https://example.test/season",
                        "multiquality_url": None,
                    },
                )

            expected = ["360P", "720P", "1080P"]
            self.assertEqual(
                [call.args[4] for call in is_uploaded.call_args_list], expected
            )
            self.assertEqual(
                [call.args[4] for call in claim.call_args_list], expected
            )
            self.assertEqual(
                [call.args[4] for call in record.call_args_list], expected
            )
            self.assertEqual(uploads.await_count, 3)
            self.assertEqual(release.call_count, 3)

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()