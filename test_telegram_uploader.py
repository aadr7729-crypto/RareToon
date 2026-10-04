import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import telegram_uploader as uploader


class FakeClient:
    def __init__(self, started=False):
        self.started = started
        self.start_calls = 0
        self.stop_calls = 0
        self.get_chat = AsyncMock(
            return_value=SimpleNamespace(id=-100222, username="anime_channel")
        )
        self.send_video = AsyncMock(return_value=SimpleNamespace(id=91))

    async def start(self):
        self.start_calls += 1
        self.started = True
        return self

    async def stop(self):
        self.stop_calls += 1
        self.started = False
        return self


class TelegramUploaderTests(unittest.IsolatedAsyncioTestCase):
    async def test_new_wzgram_client_uses_bot_auth_and_memory_session(self):
        client = FakeClient()
        with (
            patch.object(uploader, "_client", None),
            patch.object(uploader, "_client_started", False),
            patch.object(
                uploader, "_mtproto_credentials", return_value=(123, "hash")
            ),
            patch.object(uploader, "WzgramClient", return_value=client) as factory,
            patch.object(uploader, "BOT_TOKEN", "test-token"),
        ):
            result = await uploader._get_mtproto_client()

        self.assertIs(result, client)
        self.assertEqual(client.start_calls, 1)
        factory.assert_called_once_with(
            "raretoon-wzgram-uploader",
            api_id=123,
            api_hash="hash",
            bot_token="test-token",
            in_memory=True,
            no_updates=True,
        )

    async def test_resolves_uncached_public_channel_by_username(self):
        client = FakeClient()
        client.get_chat = AsyncMock(
            side_effect=[
                ValueError("not cached"),
                SimpleNamespace(id=-100222),
            ]
        )
        bot = SimpleNamespace(
            get_chat=AsyncMock(
                return_value=SimpleNamespace(username="anime_channel")
            )
        )
        with patch.object(
            uploader, "_get_mtproto_client", new=AsyncMock(return_value=client)
        ):
            destination = await uploader.prepare_upload_destination(
                "-100222", bot=bot
            )

        self.assertEqual(destination, -100222)
        bot.get_chat.assert_awaited_once_with(-100222)
        client.get_chat.assert_has_awaits([
            unittest.mock.call(-100222),
            unittest.mock.call("@anime_channel"),
        ])

    async def test_unresolved_private_channel_falls_back_to_bot_api(self):
        client = FakeClient()
        client.get_chat = AsyncMock(side_effect=ValueError("unknown peer"))
        bot = SimpleNamespace(
            get_chat=AsyncMock(return_value=SimpleNamespace(username=None)),
            send_video=AsyncMock(return_value=SimpleNamespace(message_id=92)),
        )
        with tempfile.NamedTemporaryFile() as video:
            video.write(b"video")
            video.flush()
            with (
                patch.object(
                    uploader, "_get_mtproto_client",
                    new=AsyncMock(return_value=client),
                ),
                patch.object(
                    uploader, "get_video_metadata",
                    return_value={"duration": 1, "width": 320, "height": 240},
                ),
            ):
                receipt = await uploader.upload_video(
                    bot, "-100222", video.name, "caption", "episode.mp4"
                )

        self.assertEqual(receipt.message_id, 92)
        client.send_video.assert_not_awaited()
        bot.send_video.assert_awaited_once()

    async def test_bot_api_fallback_rejects_videos_over_50_mb(self):
        client = FakeClient()
        client.get_chat = AsyncMock(side_effect=ValueError("unknown peer"))
        bot = SimpleNamespace(
            get_chat=AsyncMock(return_value=SimpleNamespace(username=None)),
            send_video=AsyncMock(),
        )
        with tempfile.NamedTemporaryFile() as video:
            video.write(b"video")
            video.flush()
            with (
                patch.object(
                    uploader, "_get_mtproto_client",
                    new=AsyncMock(return_value=client),
                ),
                patch.object(
                    uploader, "get_video_metadata",
                    return_value={"duration": 1, "width": 320, "height": 240},
                ),
                patch.object(
                    uploader.os.path, "getsize",
                    return_value=uploader.BOT_API_UPLOAD_LIMIT + 1,
                ),
            ):
                with self.assertRaisesRegex(
                    uploader.UploadDestinationError, "50 MB upload limit"
                ):
                    await uploader.upload_video(
                        bot, "-100222", video.name, "caption", "episode.mp4"
                    )

        client.send_video.assert_not_awaited()
        bot.send_video.assert_not_awaited()

    async def test_wzgram_sends_video_with_duration_dimensions_and_progress(self):
        client = FakeClient()
        callback = unittest.mock.Mock()
        bot = SimpleNamespace()
        with tempfile.NamedTemporaryFile() as video:
            video.write(b"video")
            video.flush()
            with (
                patch.object(
                    uploader, "_get_mtproto_client",
                    new=AsyncMock(return_value=client),
                ),
                patch.object(
                    uploader, "get_video_metadata",
                    return_value={"duration": 1421, "width": 1920, "height": 1080},
                ),
            ):
                receipt = await uploader.upload_video(
                    bot,
                    "-100222",
                    video.name,
                    "caption",
                    "episode.mp4",
                    progress_callback=callback,
                )

        self.assertEqual(receipt.message_id, 91)
        client.send_video.assert_awaited_once()
        kwargs = client.send_video.await_args.kwargs
        self.assertEqual(kwargs["chat_id"], -100222)
        self.assertEqual(kwargs["video"], video.name)
        self.assertEqual(kwargs["duration"], 1421)
        self.assertEqual((kwargs["width"], kwargs["height"]), (1920, 1080))
        self.assertIs(kwargs["parse_mode"], uploader.ParseMode.DISABLED)
        self.assertTrue(kwargs["supports_streaming"])
        self.assertIs(kwargs["progress"], callback)

    async def test_restarts_client_once_after_connection_drop(self):
        client = FakeClient(started=True)
        attempts = 0

        async def send_video(**kwargs):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise ConnectionError("disconnected")
            return SimpleNamespace(id=92)

        client.send_video = AsyncMock(side_effect=send_video)
        with tempfile.NamedTemporaryFile() as video:
            video.write(b"video")
            video.flush()
            with (
                patch.object(uploader, "_client", client),
                patch.object(uploader, "_client_started", True),
                patch.object(
                    uploader, "_mtproto_credentials", return_value=(123, "hash")
                ),
                patch.object(
                    uploader, "get_video_metadata",
                    return_value={"duration": 1, "width": 320, "height": 240},
                ),
            ):
                receipt = await uploader.upload_video(
                    SimpleNamespace(),
                    "-100222",
                    video.name,
                    "caption",
                    "episode.mp4",
                )

        self.assertEqual(receipt.message_id, 92)
        self.assertEqual(attempts, 2)
        self.assertEqual(client.stop_calls, 1)
        self.assertEqual(client.start_calls, 1)

    async def test_upload_fails_instead_of_sending_zero_duration_metadata(self):
        with tempfile.NamedTemporaryFile() as video:
            with (
                patch.object(
                    uploader, "get_video_metadata",
                    return_value={"duration": 0, "width": 1920, "height": 1080},
                ),
                patch.object(uploader, "_get_mtproto_client") as get_client,
            ):
                with self.assertRaisesRegex(ValueError, "Could not read"):
                    await uploader.upload_video(
                        SimpleNamespace(), "-100222", video.name,
                        "caption", "episode.mp4",
                    )

        get_client.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()