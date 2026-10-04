#This Bot Is Created By Shivam, Thanks To Shivam For Providing Repo
import asyncio
import io
import logging
import os
import re
from dataclasses import dataclass

from telegram import InputFile
from wzgram import Client as WzgramClient
from wzgram.enums import ParseMode

from config import BOT_TOKEN
from video_metadata import get_video_metadata


@dataclass
class UploadReceipt:
    message_id: int


_client = None
_client_lock = asyncio.Lock()
_client_started = False
BOT_API_UPLOAD_LIMIT = 50 * 1024 * 1024
logger = logging.getLogger(__name__)


def _mtproto_credentials():
    api_id_raw = os.getenv("TELEGRAM_API_ID", "").strip()
    api_hash = os.getenv("TELEGRAM_API_HASH", "").strip()
    if not api_id_raw and not api_hash:
        return None
    if not api_id_raw or not api_hash:
        raise RuntimeError(
            "Both TELEGRAM_API_ID and TELEGRAM_API_HASH are required for MTProto uploads."
        )
    try:
        api_id = int(api_id_raw)
    except ValueError as exc:
        raise RuntimeError("TELEGRAM_API_ID must be a number.") from exc
    return api_id, api_hash


async def _get_mtproto_client():
    global _client, _client_started
    credentials = _mtproto_credentials()
    if credentials is None:
        return None

    async with _client_lock:
        if _client is None:
            api_id, api_hash = credentials
            _client = WzgramClient(
                "raretoon-wzgram-uploader",
                api_id=api_id,
                api_hash=api_hash,
                bot_token=BOT_TOKEN,
                in_memory=True,
                no_updates=True,
            )

        if not _client_started:
            await _client.start()
            _client_started = True
    return _client


class UploadDestinationError(RuntimeError):
    """The WZGram bot session cannot address the requested destination."""


async def _resolve_mtproto_destination(client, chat_id, bot=None):
    destination = _chat_reference(chat_id)
    try:
        chat = await client.get_chat(destination)
        return chat.id
    except Exception as original_error:
        # Resolve through the Bot API first when a public username is available,
        # then warm WZGram's peer cache before uploading the video.
        if bot is not None:
            try:
                chat = await bot.get_chat(destination)
                username = getattr(chat, "username", None)
                if username:
                    resolved = await client.get_chat(f"@{username}")
                    return resolved.id
            except Exception:
                logger.debug(
                    "Could not resolve channel %s by its public username.",
                    chat_id,
                    exc_info=True,
                )
        raise UploadDestinationError(
            "The WZGram bot session cannot resolve this channel. The Bot API "
            "uploader will be used instead; private channels may be limited "
            "to 50 MB per video."
        ) from original_error


async def prepare_upload_destination(chat_id, bot=None):
    """Resolve a destination before downloading; False means use Bot API."""
    client = await _get_mtproto_client()
    if client is None:
        return False
    try:
        return await _resolve_mtproto_destination(client, chat_id, bot=bot)
    except UploadDestinationError:
        logger.info(
            "Using the Bot API for channel %s; WZGram could not resolve the peer.",
            chat_id,
        )
        return False


async def _send_mtproto_file(client, chat_id, file_path, **kwargs):
    for attempt in range(2):
        try:
            destination = await _resolve_mtproto_destination(client, chat_id)
            return await client.send_video(
                chat_id=destination,
                video=file_path,
                **kwargs,
            )
        except ConnectionError:
            if attempt:
                raise
            global _client_started
            async with _client_lock:
                if _client is not None and _client_started:
                    await _client.stop()
                    _client_started = False
            client = await _get_mtproto_client()
            if client is None:
                raise RuntimeError("The WZGram upload client is unavailable.")
    raise ConnectionError("WZGram could not send the video after reconnecting.")


def _chat_reference(chat_id):
    value = str(chat_id).strip()
    try:
        return int(value)
    except ValueError:
        return value


def format_upload_filename(
    default_filename, metadata_prefix, anime_name, season, episode, quality
):
    """Apply a user's private filename prefix while keeping generated fields safe."""
    if not metadata_prefix:
        return default_filename

    def safe_component(value):
        return re.sub(r"[^\w@.-]+", "_", str(value), flags=re.UNICODE).strip("._")

    prefix = safe_component(metadata_prefix).rstrip("_")
    if not prefix:
        return default_filename
    if not prefix.casefold().endswith("s"):
        prefix += "s"

    try:
        episode_label = f"S{int(season):02d}_EP{int(episode):02d}"
    except (TypeError, ValueError):
        episode_label = f"S{safe_component(season)}_EP{safe_component(episode)}"
    title = safe_component(anime_name)
    quality_label = safe_component(quality).lower()
    return f"{prefix}_{title}_{episode_label}_{quality_label}.mp4"


async def upload_video(
    bot, chat_id, file_path, caption, filename, progress_callback=None,
    thumbnail=None,
):
    """Upload with WZGram MTProto, falling back to the Bot API if unavailable."""
    metadata = get_video_metadata(file_path)
    duration = metadata["duration"]
    width = metadata["width"]
    height = metadata["height"]
    if duration <= 0 or width <= 0 or height <= 0:
        raise ValueError(
            "Could not read this video's duration and dimensions. "
            "Make sure ffprobe is installed and the downloaded file is a valid video."
        )
    client = await _get_mtproto_client()
    if client is not None:
        try:
            await _resolve_mtproto_destination(client, chat_id, bot=bot)
        except UploadDestinationError:
            logger.info(
                "Using the Bot API for channel %s; WZGram could not resolve the peer.",
                chat_id,
            )
            client = None
    if client is not None:
        if progress_callback:
            progress_callback(0, os.path.getsize(file_path))
        thumb_file = io.BytesIO(thumbnail) if thumbnail else None
        if thumb_file is not None:
            thumb_file.name = "thumbnail.jpg"
        message = await _send_mtproto_file(
            client,
            chat_id,
            file_path,
            caption=caption,
            parse_mode=ParseMode.DISABLED,
            file_name=filename,
            duration=duration,
            width=width,
            height=height,
            thumb=thumb_file,
            progress=progress_callback,
            supports_streaming=True,
        )
        if progress_callback:
            file_size = os.path.getsize(file_path)
            progress_callback(file_size, file_size)
        return UploadReceipt(message_id=message.id)

    file_size = os.path.getsize(file_path)
    if file_size > BOT_API_UPLOAD_LIMIT:
        raise UploadDestinationError(
            "This video is larger than the Bot API's 50 MB upload limit. "
            "For larger uploads, configure TELEGRAM_API_ID and "
            "TELEGRAM_API_HASH and make sure the WZGram bot can resolve the destination."
        )
    if progress_callback:
        progress_callback(0, file_size)
    video_options = {"duration": duration} if duration > 0 else {}
    if width > 0 and height > 0:
        video_options.update(width=width, height=height)
    if thumbnail:
        video_options["thumbnail"] = InputFile(
            thumbnail, filename="thumbnail.jpg"
        )
    with open(file_path, "rb") as video:
        message = await bot.send_video(
            chat_id=chat_id,
            video=video,
            caption=caption,
            filename=filename,
            supports_streaming=True,
            **video_options,
        )
    if progress_callback:
        progress_callback(file_size, file_size)
    return UploadReceipt(message_id=message.message_id)