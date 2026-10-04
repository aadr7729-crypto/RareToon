"""Owner-configured Hindi-dub episode downloads and channel tracking."""

import asyncio
import json
import io
import logging
import os
import re
import threading
import time
import uuid
from difflib import SequenceMatcher

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InputFile, MessageEntity
from telegram.constants import ChatAction
from telegram.error import BadRequest, Forbidden

import database as db
import scraper
import video_downloader
from config import DOWNLOAD_DIR, OWNER_ID
from telegram_uploader import (
    BOT_API_UPLOAD_LIMIT,
    UploadDestinationError,
    prepare_upload_destination,
    upload_video,
)

logger = logging.getLogger(__name__)

LANGUAGE_NAMES = {"hindi": "Hindi Dub"}
AUTUPLOAD_QUALITIES = ("360P", "720P", "1080P")
AUTO_QUALITY_POLL_SECONDS = 60
DEFAULT_AUTUPLOAD_CAPTION = (
    "Episode :- {episode}\n"
    "🗣 Language :- {language}\n"
    "🟡 Quality :- {quality}"
)
DEFAULT_ANNOUNCEMENT_CAPTION = (
    "❝ {anime} ❞\n\n"
    "━━━━━━━━━━━━━━━━━━━━\n"
    "├Season :- {season}\n"
    "├Episodes :- {episode_count}\n"
    "├Quality :- {qualities}\n"
    "├Language :- {language} [#OFFICIAL]\n"
    "├Genre :- {genres}\n"
    "━━━━━━━━━━━━━━━━━━━━\n"
    "▸ [ Credit - {ott} ] Entertainment\n"
    "━━━━━━━━━━━━━━━━━━━━\n"
    "╭──────────────────────────╮\n"
    "       Episode {episode_number}\n"
    "╰──────────────────────────╯"
)
DEFAULT_AUTUPLOAD_FILENAME = "{anime}_S{season}_EP{episode}_{quality}"
_channel_sync_locks = {}
_owner_tasks = {}


class ChannelAccessError(RuntimeError):
    """The bot is no longer able to post to a configured destination."""


class _OwnerProgress:
    """Edit one progress message in the owner's DM for each upload task."""

    def __init__(self, bot):
        self.bot = bot
        self.message = None
        self.loop = asyncio.get_running_loop()
        self._edit_lock = asyncio.Lock()
        self._state_lock = threading.Lock()
        self._title = "Auto-upload is starting."
        self._phase = ""
        self._percent = 0.0
        self._completed = 0
        self._total = 0
        self._last_sent_percent = -1.0
        self._last_sent_at = 0.0
        self.task_id = uuid.uuid4().hex[:10]
        self.task = asyncio.current_task()
        self.cancel_event = threading.Event()
        self.cancel_requested = False
        self.phase = ""
        self.closed = False

    def _cancel_markup(self):
        return InlineKeyboardMarkup([[
            InlineKeyboardButton(
                "⏹ Cancel",
                callback_data=f"autoupload:stop:{self.task_id}",
            )
        ]])

    async def start(self, title):
        self._title = str(title)
        _owner_tasks[self.task_id] = self
        if self.task:
            self.task.add_done_callback(self._task_finished)
        if OWNER_ID <= 0:
            logger.warning("Auto-upload owner-DM updates are unavailable: OWNER_ID is unset.")
            return self
        try:
            self.message = await self.bot.send_message(
                OWNER_ID, self._title, reply_markup=self._cancel_markup()
            )
        except Exception:
            logger.warning("Could not open an auto-upload progress message in the owner DM.")
        return self

    def _task_finished(self, task):
        if self.closed:
            return
        self.closed = True
        self._title = (
            "Auto-upload stopped."
            if task.cancelled() else "Auto-upload task finished."
        )
        self._phase = ""
        self.phase = ""
        _owner_tasks.pop(self.task_id, None)
        if self.loop.is_closed():
            return
        try:
            self.loop.create_task(self._edit())
        except RuntimeError:
            logger.debug("Could not remove the cancel button after task shutdown.")

    def is_cancelled(self):
        return self.cancel_event.is_set()

    def request_cancel(self):
        if self.closed or self.cancel_requested:
            return False
        self.cancel_requested = True
        self.cancel_event.set()
        if self.phase != "download" and self.task and not self.task.done():
            self.task.cancel()
        return True

    async def close(self):
        self.closed = True
        _owner_tasks.pop(self.task_id, None)
        await self._edit()

    @staticmethod
    def _bar(percent, width=14):
        percent = max(0.0, min(100.0, float(percent or 0)))
        filled = round(width * percent / 100)
        return f"{'▰' * filled}{'▱' * (width - filled)}"

    @staticmethod
    def _size(value):
        value = float(max(0, value or 0))
        for unit in ("B", "KB", "MB", "GB"):
            if value < 1024 or unit == "GB":
                return f"{value:.1f} {unit}"
            value /= 1024

    def _render(self):
        with self._state_lock:
            title = self._title
            phase = self._phase
            percent = self._percent
            completed = self._completed
            total = self._total
        if not phase:
            return title
        label = "Downloading" if phase == "download" else "Uploading"
        return (
            f"{title}\n\n"
            f"{'📥' if phase == 'download' else '📤'} {label}\n"
            f"{self._bar(percent)} {percent:.1f}%\n"
            f"{self._size(completed)} / {self._size(total)}"
        )

    async def _edit(self):
        if self.message is None:
            return
        async with self._edit_lock:
            try:
                markup = (
                    self._cancel_markup()
                    if not self.closed and not self.cancel_requested
                    else None
                )
                await self.message.edit_text(self._render(), reply_markup=markup)
            except Exception:
                logger.debug("Owner-DM auto-upload progress edit failed.", exc_info=True)

    async def status(self, text):
        with self._state_lock:
            self._title = str(text)
            self._phase = ""
            self.phase = ""
            self._percent = 0.0
            self._completed = 0
            self._total = 0
            self._last_sent_percent = -1.0
        await self._edit()

    async def transfer_start(self, anime_name, episode_label, quality, phase):
        with self._state_lock:
            self._title = f"Auto-upload • {anime_name}\n{episode_label} • {quality}"
            self._phase = phase
            self.phase = phase
            self._percent = 0.0
            self._completed = 0
            self._total = 0
            self._last_sent_percent = -1.0
            self._last_sent_at = 0.0
        await self._edit()

    def _report_transfer(self, phase, percent, completed, total):
        now = time.monotonic()
        with self._state_lock:
            percent = max(self._percent, min(100.0, max(0.0, float(percent or 0))))
            self._percent = percent
            self._completed = max(0, int(completed or 0))
            self._total = max(0, int(total or 0))
            if (
                percent < 100
                and percent - self._last_sent_percent < 5
                and now - self._last_sent_at < 3
            ):
                return
            self._phase = phase
            self._last_sent_percent = percent
            self._last_sent_at = now
        try:
            self.loop.call_soon_threadsafe(lambda: self.loop.create_task(self._edit()))
        except RuntimeError:
            logger.debug("Ignoring auto-upload progress after loop shutdown.")

    def report_download(self, percent, completed=0, total=0):
        self._report_transfer("download", percent, completed, total)

    def report_upload(self, completed, total):
        percent = completed / total * 100 if total else 0
        self._report_transfer("upload", percent, completed, total)


async def cancel_owner_task(update, context):
    """Stop only the active upload shown in the owner's progress message."""
    query = update.callback_query
    if query.from_user.id != OWNER_ID:
        await query.answer("Owner only.", show_alert=True)
        return
    task_id = query.data.rsplit(":", 1)[-1]
    progress = _owner_tasks.get(task_id)
    if not progress or not progress.request_cancel():
        await query.answer("This task is no longer active.", show_alert=True)
        return
    await query.answer("Cancellation requested.")
    await progress.status("Cancellation requested; stopping the current transfer…")


def clean_anime_title(value):
    text = re.sub(r"<[^>]+>", " ", value or "")
    text = re.split(r"\s*[|｜]\s*", text, maxsplit=1)[0]
    text = re.sub(
        r"\b(?:hindi\s+)?(?:dubbed|episodes?\s+download|download\s+hd)\b.*$",
        "",
        text,
        flags=re.IGNORECASE,
    )
    return " ".join(text.split()).strip(" -–—:|")


def _episode_key(episode):
    return int(episode.get("season") or 0), int(episode.get("episode") or 0)


def _episode_counts_by_season(episodes):
    counts = {}
    for episode in episodes or []:
        season = str(_episode_key(episode)[0])
        counts[season] = counts.get(season, 0) + 1
    return counts


async def search_anime(query):
    """Search RareAnimes and return serializable choices for the owner."""
    results = await asyncio.to_thread(scraper.search_anime, query, "hindi")
    return [result.to_dict() for result in (results or [])]


async def load_anime_catalog(result):
    """Load title details and only Hindi-dub episodes for the chosen result."""
    source_url = result.get("url")
    if not source_url:
        raise ValueError("The selected anime result has no source page.")
    info = await asyncio.to_thread(scraper.get_anime_page_info, source_url)
    if not isinstance(info, dict) or info.get("error"):
        raise RuntimeError("Could not load the anime details page.")

    season_urls = [source_url]
    season_urls.extend(
        item.get("url")
        for item in info.get("all_season_links", [])
        if isinstance(item, dict) and item.get("url")
    )
    season_urls = list(dict.fromkeys(season_urls))
    episodes = []
    seen = set()
    page_errors = []
    for page_url in season_urls:
        page = await asyncio.to_thread(scraper.get_season_episodes, page_url, "hindi")
        if isinstance(page, dict) and page.get("error"):
            page_errors.append(page["error"])
            continue
        page_episodes = page.get("episodes", []) if isinstance(page, dict) else page or []
        for episode in page_episodes:
            if getattr(episode, "audio_variant", "") != "hindi_dub":
                continue
            identity = (int(episode.season or 0), int(episode.episode or 0))
            if identity in seen or not getattr(episode, "download_url", ""):
                continue
            seen.add(identity)
            episodes.append({
                "season": identity[0],
                "episode": identity[1],
                "download_url": episode.download_url,
                "title": getattr(episode, "title", "") or "Hindi Dub",
                "episode_type": getattr(episode, "episode_type", "episode"),
            })
    episodes.sort(key=_episode_key)
    if not episodes and page_errors:
        raise RuntimeError("Could not read episode pages from the anime site.")
    return {
        "anime_name": clean_anime_title(result.get("title") or info.get("title"))
        or result.get("title", "Anime"),
        "source_url": source_url,
        "season_page_urls": season_urls,
        "site_info": {
            "title": info.get("title") or result.get("title", ""),
            "status": info.get("status") or "unknown",
            "languages": info.get("languages") or [],
            "categories": info.get("categories") or [],
            **(info.get("series_metadata") or {}),
        },
        "episodes": episodes,
    }


async def validate_channel(bot, channel_id):
    """Ensure the target is a channel where the bot can post."""
    try:
        chat = await bot.get_chat(int(channel_id))
    except (BadRequest, Forbidden) as exc:
        raise ChannelAccessError(
            "The bot cannot access this channel. Add it back as an administrator and retry."
        ) from exc
    chat_type = getattr(chat.type, "value", str(chat.type)).lower()
    if chat_type != "channel":
        raise ChannelAccessError("That ID no longer points to a Telegram channel.")
    try:
        member = await bot.get_chat_member(chat.id, (await bot.get_me()).id)
    except (BadRequest, Forbidden) as exc:
        raise ChannelAccessError(
            "The bot cannot verify its channel access. Make it an administrator and retry."
        ) from exc
    status = getattr(member.status, "value", str(member.status)).lower()
    if status not in ("administrator", "creator"):
        raise ChannelAccessError(
            "The bot is no longer an administrator. Add it back as an administrator and retry."
        )
    return chat


async def register_channel(bot, channel_id, catalog, status):
    chat = await validate_channel(bot, channel_id)
    excluded_categories = {
        "anime", "completed", "hindi", "hindi dub", "hindi dubbed", "ongoing",
    }
    genres = [
        str(category).strip()
        for category in catalog.get("site_info", {}).get("categories", [])
        if str(category).strip()
        and str(category).strip().casefold() not in excluded_categories
    ]
    site_info = dict(catalog.get("site_info") or {})
    if not site_info.get("genres"):
        site_info["genres"] = genres
    db.add_autoupload_channel(
        chat.id,
        chat.title,
        catalog["anime_name"],
        "hindi",
        catalog["season_page_urls"],
        genres=site_info.get("genres") or genres,
        site_info=site_info,
        invite_link=getattr(chat, "invite_link", None),
    )
    db.set_autoupload_setting(f"status:{chat.id}", status)
    return db.get_autoupload_channel(chat.id)


def _quality_number(value):
    match = re.search(r"(?<!\d)(360|480|720|1080|1440|2160)\s*P?\b", str(value or "").upper())
    return int(match.group(1)) if match else 0


def _select_quality(links, desired):
    links = [item for item in (links or []) if getattr(item, "url", "")]
    if not links:
        return None
    wanted = _quality_number(desired)
    return next((item for item in links if _quality_number(item.quality) == wanted), None)


def _download_size_bytes(value):
    match = re.search(
        r"(\d+(?:\.\d+)?)\s*(B|KB|KIB|MB|MIB|GB|GIB)\b",
        str(value or ""),
        flags=re.IGNORECASE,
    )
    if not match:
        return None
    unit = match.group(2).upper()
    exponent = {"B": 0, "KB": 1, "KIB": 1, "MB": 2, "MIB": 2, "GB": 3, "GIB": 3}[unit]
    return int(float(match.group(1)) * (1024 ** exponent))


def _format_caption(channel, episode, quality):
    template = db.get_autoupload_setting("caption_template", DEFAULT_AUTUPLOAD_CAPTION)
    values = {
        "anime": channel["anime_name"],
        "episode": f"S{int(episode.get('season') or 0):02d}E{int(episode.get('episode') or 0):02d}",
        "episode_number": int(episode.get("episode") or 0),
        "season": int(episode.get("season") or 0),
        "language": "Hindi Dub",
        "quality": quality,
    }
    try:
        return str(template).format(**values)
    except (KeyError, ValueError, IndexError):
        logger.warning("Invalid /autoupload caption template; using the default.")
        return DEFAULT_AUTUPLOAD_CAPTION.format(**values)


def _available_quality_labels(links):
    qualities = {
        _quality_number(getattr(item, "quality", ""))
        for item in (links or [])
    }
    return ", ".join(
        f"{quality}P" for quality in sorted(qualities) if quality
    ) or "Not listed"


def _format_announcement_caption(channel, episode, quality, link, qualities=None):
    template = db.get_autoupload_setting(
        "announcement_caption_template", DEFAULT_ANNOUNCEMENT_CAPTION
    )
    site_info = channel.get("site_info") or {}
    genres = site_info.get("genres") or channel.get("genres") or []
    if isinstance(genres, str):
        try:
            genres = json.loads(genres)
        except (TypeError, ValueError):
            genres = [genres]
    genre_text = ", ".join(
        str(genre).strip() for genre in genres if str(genre).strip()
    ) or "Not listed"
    season = int(episode.get("season") or 0)
    season_counts = channel.get("episode_counts_by_season") or {}
    if str(site_info.get("status") or "").casefold() == "ongoing":
        episode_count = "Ongoing"
    else:
        episode_count = (
            site_info.get("episode_count")
            or season_counts.get(str(season))
            or season_counts.get(season)
            or channel.get("episode_count")
            or episode.get("episode_count")
            or int(episode.get("episode") or 0)
        )
    source_qualities = site_info.get("quality_labels")
    values = {
        "anime": channel["anime_name"],
        "episode": f"S{int(episode.get('season') or 0):02d}E{int(episode.get('episode') or 0):02d}",
        "episode_number": int(episode.get("episode") or 0),
        "season": f"{season:02d}",
        "episode_count": episode_count,
        "language": "Hindi Dub",
        "quality": quality,
        "qualities": source_qualities or qualities or quality,
        "genres": genre_text,
        "link": link or "",
        "ott": site_info.get("ott_name") or "Not listed",
        "ott_link": site_info.get("ott_url") or "",
    }
    try:
        return str(template).format(**values)[:1024]
    except (KeyError, ValueError, IndexError):
        logger.warning("Invalid /authupload caption template; using the default.")
        return DEFAULT_ANNOUNCEMENT_CAPTION.format(**values)[:1024]


def _utf16_offset(text, index):
    return len(text[:index].encode("utf-16-le")) // 2


def _announcement_link_entities(text, channel, episode, link):
    """Link the provider credit and uploaded episode label without showing URLs."""
    site_info = channel.get("site_info") or {}
    candidates = []
    ott_name = str(site_info.get("ott_name") or "").strip()
    ott_url = str(site_info.get("ott_url") or "").strip()
    if ott_name and ott_url:
        credit_start = text.find("Credit - ")
        provider_start = text.find(ott_name, credit_start + len("Credit - "))
        if credit_start >= 0 and provider_start >= 0:
            candidates.append((provider_start, len(ott_name), ott_url))

    episode_label = f"Episode {int(episode.get('episode') or 0)}"
    episode_start = text.rfind(episode_label)
    if link and episode_start >= 0:
        candidates.append((episode_start, len(episode_label), link))

    return [
        MessageEntity(
            type=MessageEntity.TEXT_LINK,
            offset=_utf16_offset(text, start),
            length=_utf16_offset(text, start + length) - _utf16_offset(text, start),
            url=url,
        )
        for start, length, url in candidates
    ]


def _format_filename(channel, episode, quality):
    values = {
        "anime": channel["anime_name"],
        "episode": int(episode.get("episode") or 0),
        "season": int(episode.get("season") or 0),
        "episode_number": int(episode.get("episode") or 0),
        "language": "Hindi Dub",
        "quality": quality,
    }
    template = db.get_autoupload_setting(
        "filename_template", DEFAULT_AUTUPLOAD_FILENAME
    )
    try:
        name = str(template).format(**values)
    except (KeyError, ValueError, IndexError):
        logger.warning("Invalid /autoupload filename template; using the default.")
        name = DEFAULT_AUTUPLOAD_FILENAME.format(**values)
    name = re.sub(r"[^\w@.-]+", "_", name, flags=re.UNICODE).strip("._")
    return (name or "anime_episode") + ".mp4"


async def _channel_episode_link(bot, channel_id, message_id, invite_link=None):
    try:
        chat = await bot.get_chat(channel_id)
        if chat.username:
            return f"https://t.me/{chat.username}/{message_id}"
        if getattr(chat, "invite_link", None):
            return chat.invite_link
    except Exception:
        pass
    if invite_link:
        return invite_link
    digits = str(channel_id).lstrip("-")
    if digits.startswith("100"):
        digits = digits[3:]
    return f"https://t.me/c/{digits}/{message_id}" if digits else None


async def _announce_episode(bot, channel, episode, quality, message_id, qualities=None):
    main_channel = db.get_autoupload_setting("main_channel_id")
    if not main_channel:
        return
    season, number = _episode_key(episode)
    if not db.claim_autoupload_announcement(
        channel["channel_id"], season, number, "hindi"
    ):
        return
    try:
        site_info = channel.get("site_info") or {}
        if not site_info:
            page_urls = channel.get("season_page_urls") or []
            if isinstance(page_urls, str):
                try:
                    page_urls = json.loads(page_urls)
                except (TypeError, ValueError):
                    page_urls = [page_urls]
            if page_urls:
                page_info = await asyncio.to_thread(
                    scraper.get_anime_page_info, page_urls[0]
                )
                if isinstance(page_info, dict) and not page_info.get("error"):
                    site_info = dict(page_info.get("series_metadata") or {})
                    site_info["status"] = page_info.get("status", "unknown")
                    channel["site_info"] = site_info
                    channel["genres"] = site_info.get("genres") or channel.get("genres") or []
                    db.set_autoupload_channel_site_info(
                        channel["channel_id"], site_info
                    )
        link = await _channel_episode_link(
            bot, channel["channel_id"], message_id, channel.get("invite_link")
        )
        text = _format_announcement_caption(
            channel, episode, quality, link, qualities=qualities
        )
        entities = _announcement_link_entities(text, channel, episode, link)
        image = channel.get("main_post_image_file_id")
        if not image and channel.get("main_post_image"):
            image = InputFile(
                io.BytesIO(bytes(channel["main_post_image"])),
                filename="main-channel-poster.jpg",
            )
        if image:
            try:
                announcement = await bot.send_photo(
                    main_channel, photo=image, caption=text,
                    caption_entities=entities or None,
                )
            except BadRequest:
                if (
                    not channel.get("main_post_image_file_id")
                    or not channel.get("main_post_image")
                ):
                    raise
                announcement = await bot.send_photo(
                    main_channel,
                    photo=InputFile(
                        io.BytesIO(bytes(channel["main_post_image"])),
                        filename="main-channel-poster.jpg",
                    ),
                    caption=text,
                    caption_entities=entities or None,
                )
        else:
            announcement = await bot.send_message(
                main_channel, text, entities=entities or None
            )
        db.record_autoupload_announcement(
            channel["channel_id"], season, number, "hindi",
            announcement.message_id,
        )
    except Forbidden:
        db.release_autoupload_announcement(
            channel["channel_id"], season, number, "hindi"
        )
        logger.warning(
            "Could not announce %s: the bot lacks permission in the main channel.",
            channel["anime_name"],
        )
    except Exception:
        db.release_autoupload_announcement(
            channel["channel_id"], season, number, "hindi"
        )
        logger.exception(
            "Could not send the auto-upload announcement for %s.",
            channel["anime_name"],
        )


async def _upload_one(
    bot, channel, episode, progress, quality, links=None, mtproto_available=True
):
    if progress.is_cancelled():
        raise asyncio.CancelledError
    season, number = _episode_key(episode)
    episode_label = f"S{season:02d}E{number:02d}"
    if links is None:
        try:
            links, _ = await asyncio.to_thread(
                scraper.get_all_download_urls, episode["download_url"]
            )
        except Exception:
            logger.exception("Could not resolve %s %s.", channel["anime_name"], episode_label)
            return False
    if isinstance(links, dict):
        return False
    selected = _select_quality(links, quality)
    if not selected:
        return False
    if not mtproto_available:
        expected_size = _download_size_bytes(getattr(selected, "size", None))
        if expected_size and expected_size > BOT_API_UPLOAD_LIMIT:
            await progress.status(
                f"Auto-upload skipped • {channel['anime_name']} • {episode_label}\n"
                f"{getattr(selected, 'size', 'Video')} exceeds the Bot API's "
                "50 MB limit. The channel must be public for the MTProto bot "
                "to resolve it."
            )
            logger.info(
                "Skipping %s %s before download because the private-channel "
                "Bot API fallback cannot upload %s.",
                channel["anime_name"],
                episode_label,
                getattr(selected, "size", "an oversized video"),
            )
            return False

    filename = _format_filename(channel, episode, selected.quality)
    filepath = os.path.join(DOWNLOAD_DIR, f"{uuid.uuid4().hex}_{filename}")
    await progress.transfer_start(channel["anime_name"], episode_label, selected.quality, "download")
    try:
        downloaded = await asyncio.to_thread(
            video_downloader.download_video,
            selected.url,
            filepath,
            progress.report_download,
            cancel_check=progress.is_cancelled,
        )
        if progress.is_cancelled():
            raise asyncio.CancelledError
        if not downloaded:
            return False
        try:
            await bot.send_chat_action(channel["channel_id"], ChatAction.UPLOAD_VIDEO)
        except Exception:
            logger.debug("Could not send upload chat action for %s.", channel["channel_id"])
        await progress.transfer_start(
            channel["anime_name"], episode_label, selected.quality, "upload"
        )
        receipt = await upload_video(
            bot,
            channel["channel_id"],
            filepath,
            _format_caption(channel, episode, selected.quality),
            filename,
            progress_callback=progress.report_upload,
            thumbnail=bytes(channel["thumbnail"]) if channel.get("thumbnail") else None,
        )
        db.record_autoupload_episode(
            channel["channel_id"], channel["anime_name"], season, number,
            "hindi", selected.quality, receipt.message_id,
        )
        progress.phase = "post"
        await _announce_episode(
            bot,
            channel,
            episode,
            selected.quality,
            receipt.message_id,
            qualities=_available_quality_labels(links),
        )
        return True
    except video_downloader.DownloadCancelled as exc:
        raise asyncio.CancelledError from exc
    except asyncio.CancelledError:
        raise
    except ChannelAccessError:
        raise
    except Forbidden as exc:
        raise ChannelAccessError(
            "The bot lost permission to post in this channel. Add it back as an administrator."
        ) from exc
    except UploadDestinationError as exc:
        logger.info(
            "Could not upload %s %s: %s.",
            channel["anime_name"],
            episode_label,
            exc,
        )
        await progress.status(
            f"Auto-upload skipped • {channel['anime_name']} • {episode_label}\n{exc}"
        )
        return False
    except Exception:
        logger.exception("Auto-upload failed for %s %s.", channel["anime_name"], episode_label)
        return False
    finally:
        video_downloader.cleanup_file(filepath)


async def _upload_batch(bot, channel, episodes, progress, mtproto_available=True):
    total = len(episodes)
    uploaded = 0
    failed = 0
    skipped = 0

    async def resolve_links(episode, episode_label):
        try:
            links, _ = await asyncio.to_thread(
                scraper.get_all_download_urls, episode["download_url"]
            )
        except Exception:
            logger.exception(
                "Could not resolve download qualities for %s %s.",
                channel["anime_name"], episode_label,
            )
            return []
        return [] if isinstance(links, dict) else (links or [])

    for index, episode in enumerate(episodes, start=1):
        if progress.is_cancelled():
            raise asyncio.CancelledError
        await progress.status(
            f"Auto-upload • {channel['anime_name']}\n"
            f"Episode {index}/{total} • preparing transfer"
        )
        season, number = _episode_key(episode)
        episode_label = f"S{season:02d}E{number:02d}"
        pending = []
        for quality in AUTUPLOAD_QUALITIES:
            if progress.is_cancelled():
                raise asyncio.CancelledError
            if db.is_autoupload_episode_uploaded(
                channel["channel_id"], season, number, "hindi", quality
            ):
                skipped += 1
            else:
                pending.append(quality)

        if not pending:
            continue

        links = await resolve_links(episode, episode_label)
        while pending:
            if progress.is_cancelled():
                raise asyncio.CancelledError
            for quality in list(pending):
                if not _select_quality(links, quality):
                    continue
                if await _upload_one(
                    bot, channel, episode, progress, quality, links=links,
                    mtproto_available=mtproto_available,
                ):
                    uploaded += 1
                else:
                    failed += 1
                pending.remove(quality)
                if progress.is_cancelled():
                    raise asyncio.CancelledError

            if not pending:
                break

            waiting_for = ", ".join(pending)
            await progress.status(
                f"Auto-upload • {channel['anime_name']}\n"
                f"{episode_label} • waiting for {waiting_for}\n"
                f"Refreshing the episode page every "
                f"{AUTO_QUALITY_POLL_SECONDS} seconds. "
                "Newly posted qualities will upload automatically; use Cancel to stop."
            )
            await asyncio.sleep(AUTO_QUALITY_POLL_SECONDS)
            if progress.is_cancelled():
                raise asyncio.CancelledError
            links = await resolve_links(episode, episode_label)
    return uploaded, failed, skipped


async def upload_selected_episodes(bot, channel, episodes):
    """Upload an owner-selected batch and report all transfer progress by DM."""
    channel_id = str(channel["channel_id"])
    lock = _channel_sync_locks.setdefault(channel_id, asyncio.Lock())
    progress = await _OwnerProgress(bot).start(
        f"Auto-upload • {channel['anime_name']}\nPreparing {len(episodes)} episode(s)."
    )
    async with lock:
        try:
            await validate_channel(bot, channel_id)
            destination = await prepare_upload_destination(channel_id, bot=bot)
            uploaded, failed, skipped = await _upload_batch(
                bot,
                channel,
                episodes,
                progress,
                mtproto_available=bool(destination),
            )
            await progress.status(
                f"Auto-upload finished • {channel['anime_name']}\n"
                f"Uploaded: {uploaded} quality file(s)"
                + (f" • Failed or unavailable: {failed}" if failed else "")
                + (f" • Already uploaded: {skipped}" if skipped else "")
            )
            return uploaded
        except asyncio.CancelledError:
            await progress.status(f"Auto-upload stopped • {channel['anime_name']}")
            raise
        except ChannelAccessError as exc:
            db.set_autoupload_setting(f"status:{channel_id}", "paused_access")
            logger.warning(
                "Paused auto-upload for %s because channel access was lost: %s",
                channel_id,
                exc,
            )
            await progress.status(
                f"Auto-upload paused • {channel['anime_name']}\n{exc}"
            )
            return 0
        except Exception as exc:
            logger.exception("Could not upload selected episodes for %s.", channel["anime_name"])
            await progress.status(f"Auto-upload error • {channel['anime_name']}\n{exc}")
            raise
        finally:
            await progress.close()


async def _episodes_for_channel(channel):
    page_urls = channel.get("season_page_urls") or []
    if isinstance(page_urls, str):
        try:
            page_urls = json.loads(page_urls)
        except (TypeError, ValueError):
            page_urls = [page_urls]
    episodes = []
    seen = set()
    errors = []
    for page_url in page_urls:
        result = await asyncio.to_thread(
            scraper.get_season_episodes, page_url, channel.get("language", "hindi")
        )
        if isinstance(result, dict) and result.get("error"):
            errors.append(result["error"])
            continue
        items = result.get("episodes", []) if isinstance(result, dict) else result or []
        for item in items:
            if getattr(item, "audio_variant", "") != "hindi_dub":
                continue
            key = (int(item.season or 0), int(item.episode or 0))
            if key in seen or not item.download_url:
                continue
            seen.add(key)
            episodes.append({
                "season": key[0],
                "episode": key[1],
                "download_url": item.download_url,
                "title": getattr(item, "title", "") or "Hindi Dub",
            })
    if not episodes and errors:
        raise RuntimeError("Could not read the anime's episode pages.")
    return sorted(episodes, key=_episode_key)


async def _sync_channel_unlocked(bot, channel):
    progress = await _OwnerProgress(bot).start(
        f"Tracking • {channel['anime_name']}\n"
        "Checking the website for new Hindi Dub episodes."
    )
    try:
        await validate_channel(bot, channel["channel_id"])
        destination = await prepare_upload_destination(
            channel["channel_id"], bot=bot
        )
        episodes = await _episodes_for_channel(channel)
        channel["episode_count"] = len(episodes)
        channel["episode_counts_by_season"] = _episode_counts_by_season(episodes)
        ignored = db.get_autoupload_ignored_episodes(channel["channel_id"])
        queue = [
            episode for episode in episodes
            if _episode_key(episode) not in ignored
            and any(
                not db.is_autoupload_episode_uploaded(
                    channel["channel_id"], *_episode_key(episode), "hindi", quality
                )
                for quality in AUTUPLOAD_QUALITIES
            )
        ]
    except asyncio.CancelledError:
        await progress.status(f"Tracking stopped • {channel['anime_name']}")
        await progress.close()
        raise
    except ChannelAccessError as exc:
        db.set_autoupload_setting(
            f"status:{channel['channel_id']}", "paused_access"
        )
        logger.warning(
            "Paused auto-upload for %s because channel access was lost: %s",
            channel["channel_id"],
            exc,
        )
        await progress.status(f"Tracking paused • {channel['anime_name']}\n{exc}")
        await progress.close()
        return 0
    except Exception as exc:
        await progress.status(f"Tracking error • {channel['anime_name']}\n{exc}")
        await progress.close()
        raise
    if not queue:
        await progress.status(
            f"Tracking • {channel['anime_name']}\n"
            f"Checked {len(episodes)} Hindi Dub episode(s); nothing new to upload."
        )
        await progress.close()
        return 0
    try:
        uploaded, failed, skipped = await _upload_batch(
            bot, channel, queue, progress, mtproto_available=bool(destination)
        )
    except asyncio.CancelledError:
        await progress.status(f"Tracking stopped • {channel['anime_name']}")
        await progress.close()
        raise
    except ChannelAccessError as exc:
        db.set_autoupload_setting(
            f"status:{channel['channel_id']}", "paused_access"
        )
        logger.warning(
            "Paused auto-upload for %s because channel access was lost: %s",
            channel["channel_id"],
            exc,
        )
        await progress.status(f"Tracking paused • {channel['anime_name']}\n{exc}")
        await progress.close()
        return 0
    await progress.status(
        f"Tracking update • {channel['anime_name']}\n"
        f"Uploaded: {uploaded} quality file(s)"
        + (f" • Failed or unavailable: {failed}" if failed else "")
        + (f" • Already uploaded: {skipped}" if skipped else "")
    )
    await progress.close()
    return uploaded


async def sync_channel(bot, channel):
    """Serialize the scheduler and owner-started jobs for a target channel."""
    channel_key = str(channel["channel_id"])
    lock = _channel_sync_locks.setdefault(channel_key, asyncio.Lock())
    async with lock:
        return await _sync_channel_unlocked(bot, channel)


async def run_autoupload_cycle(bot):
    """Check only channels selected and registered by the owner."""
    for channel in db.list_autoupload_channels():
        status = db.get_autoupload_setting(
            f"status:{channel['channel_id']}", "ongoing"
        )
        if status != "ongoing":
            continue
        try:
            await sync_channel(bot, channel)
        except asyncio.CancelledError:
            raise
        except ChannelAccessError as exc:
            db.set_autoupload_setting(
                f"status:{channel['channel_id']}", "paused_access"
            )
            logger.warning(
                "Paused auto-upload for %s because channel access was lost: %s",
                channel["channel_id"],
                exc,
            )
        except Exception:
            logger.exception(
                "Could not track /autoupload channel %s.", channel.get("channel_id")
            )


async def notify_owner(bot, text):
    """Send a one-off auto-upload status to the owner."""
    if OWNER_ID <= 0:
        logger.warning("Could not notify the owner: OWNER_ID is unset.")
        return
    try:
        await bot.send_message(OWNER_ID, text)
    except Exception:
        logger.warning("Could not send an auto-upload status to the owner DM.")


async def periodic_autoupload_job(context):
    await run_autoupload_cycle(context.bot)