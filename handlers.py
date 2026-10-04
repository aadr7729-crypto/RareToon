#This Bot Is Created By Shivam, Thanks To Shivam For Providing Repo
import os
import re
import asyncio
import io
import logging
import time
import threading
import uuid
from datetime import datetime, timezone
from telegram import Update, Bot, InputFile
from telegram.constants import ChatAction, ParseMode
from telegram.ext import (
    Application, CommandHandler, ContextTypes,
    CallbackQueryHandler, MessageHandler, filters, ApplicationHandlerStop,
)

import database as db
import scraper
import video_downloader
from telegram_uploader import upload_video
from telegram_uploader import format_upload_filename
from notifications import notify_episode_uploaded
from keyboards import (
    search_results_keyboard, status_keyboard, language_keyboard,
    episode_keyboard, quality_keyboard, ongoing_action_keyboard,
    tracked_anime_keyboard, admin_management_keyboard,
    channel_settings_keyboard, quality_preference_keyboard,
    main_admin_keyboard, upload_ongoing_confirmation_keyboard,
    premium_duration_keyboard,
)
from config import DOWNLOAD_DIR, DEFAULT_QUALITY, MAX_FILESIZE
from PIL import Image, ImageOps

logger = logging.getLogger(__name__)

user_state = {}
active_tasks = {}
user_task_ids = {}
recent_task_ids = {}

PREMIUM_REQUIRED_MESSAGE = (
    "You Not Have Premium Buy Premium First\n\n"
    "7 Days Premium - 20rs\n"
    "15 Days Premium - 40rs\n"
    "1 Month Premium - 80rs\n\n"
    "After paying, send the payment screenshot by DM to @shivam_anime."
)
QUALITY_POLL_SECONDS = 60


class TaskStopped(Exception):
    """Internal signal used to stop a download/upload task cleanly."""


def _is_language_partial_release(status, site_episode_count, available_episode_count):
    """Treat a completed title as ongoing when the chosen language is behind."""
    if status != "completed":
        return False
    try:
        site_count = int(site_episode_count or 0)
        available_count = int(available_episode_count or 0)
    except (TypeError, ValueError):
        return False
    return site_count > 0 and 0 <= available_count < site_count


class TaskControl:
    def __init__(self, user_id):
        self.user_id = user_id
        self.task_id = uuid.uuid4().hex[:8]
        self.stop_event = threading.Event()
        self.task = None
        self.cancel_announced = False

    def cancel(self):
        self.stop_event.set()

    def is_cancelled(self):
        return self.stop_event.is_set()

    def check(self):
        if self.is_cancelled():
            raise TaskStopped


def _launch_task(context, uid, factory):
    """Start a cancellable task without affecting the user's other tasks."""
    control = TaskControl(uid)

    async def run_task():
        try:
            await factory(control)
        except (TaskStopped, video_downloader.DownloadCancelled):
            control.cancel()
        except asyncio.CancelledError:
            control.cancel()
            raise
        except Exception:
            logger.exception("Task %s failed", control.task_id)
            try:
                await context.bot.send_message(
                    uid,
                    f"❌ Task `{control.task_id}` failed unexpectedly. Check the bot log for details.",
                    parse_mode=ParseMode.MARKDOWN,
                )
            except Exception:
                logger.debug("Could not send task failure notice for %s", control.task_id)
        finally:
            if control.is_cancelled() and not control.cancel_announced:
                try:
                    await context.bot.send_message(
                        uid,
                        f"🛑 Task `{control.task_id}` stopped.",
                        parse_mode=ParseMode.MARKDOWN,
                    )
                    control.cancel_announced = True
                except Exception:
                    logger.debug("Could not send cancellation notice for %s", control.task_id)

    task = context.application.create_task(run_task())
    control.task = task
    active_tasks[control.task_id] = control
    user_task_ids.setdefault(uid, set()).add(control.task_id)

    def cleanup(done_task):
        active_tasks.pop(control.task_id, None)
        recent_task_ids[control.task_id] = {
            "user_id": uid,
            "status": "cancelled" if control.is_cancelled() else "completed",
            "finished_at": time.monotonic(),
        }
        cutoff = time.monotonic() - 3600
        for old_id, item in list(recent_task_ids.items()):
            if item["finished_at"] < cutoff:
                recent_task_ids.pop(old_id, None)
        owned_ids = user_task_ids.get(uid)
        if owned_ids:
            owned_ids.discard(control.task_id)
            if not owned_ids:
                user_task_ids.pop(uid, None)
        if not done_task.cancelled():
            try:
                done_task.exception()
            except Exception:
                pass

    task.add_done_callback(cleanup)
    return control


def _cancel_tasks(uid, task_id=None):
    if task_id:
        control = active_tasks.get(task_id)
        if not control or control.user_id != uid:
            return False
        task_ids = [task_id]
    else:
        task_ids = list(user_task_ids.get(uid, set()))
        if not task_ids:
            return False

    cancelled = False
    for current_id in task_ids:
        control = active_tasks.get(current_id)
        if not control or control.user_id != uid:
            continue
        control.cancel()
        cancelled = True
    return cancelled


def _recent_task(uid, task_id):
    item = recent_task_ids.get(task_id)
    return item if item and item["user_id"] == uid else None


def _format_bytes(value):
    value = float(value or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.1f} {unit}"
        value /= 1024


def _format_eta(seconds):
    if not seconds or seconds == float("inf"):
        return "calculating..."
    seconds = max(0, int(seconds))
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes}m {seconds}s" if hours else f"{minutes}m {seconds}s"


async def _edit_progress_message(message, text, **kwargs):
    if getattr(message, "photo", None):
        return await message.edit_caption(caption=text, **kwargs)
    return await message.edit_text(text, **kwargs)


class _ProgressReporter:
    """Throttle Telegram message edits while a file is downloading/uploading."""

    def __init__(self, message, label, episode_index, episode_total, quality, control=None):
        self.message = message
        self.label = label
        self.episode_index = episode_index
        self.episode_total = episode_total
        self.quality = quality
        self.control = control
        self.task_id = control.task_id if control else uuid.uuid4().hex[:8]
        self.loop = asyncio.get_running_loop()
        self._state_lock = threading.Lock()
        self._edit_lock = asyncio.Lock()
        self._last_sent = {"download": -1.0, "upload": -1.0}
        self._last_at = {"download": 0.0, "upload": 0.0}
        self._percent = {"download": 0.0, "upload": 0.0}
        self._phase_started = {"download": time.monotonic(), "upload": time.monotonic()}
        self._last_bytes = {"download": 0, "upload": 0}
        self._total_bytes = {"download": 0, "upload": 0}

    @staticmethod
    def _bar(percent, width=14):
        percent = max(0.0, min(100.0, percent))
        filled = round(width * percent / 100)
        return f"{'▰' * filled}{'▱' * (width - filled)}"

    def _accept(self, phase, percent, completed=None, total=None, force=False):
        now = time.monotonic()
        with self._state_lock:
            percent = max(0.0, min(100.0, float(percent or 0)))
            if force and percent == 0:
                self._last_bytes[phase] = 0
                self._total_bytes[phase] = 0
                self._percent[phase] = 0.0
                self._last_sent[phase] = -1.0
                self._last_at[phase] = 0.0
                self._phase_started[phase] = now
            else:
                percent = max(self._percent[phase], percent)
                self._percent[phase] = percent
                if completed is not None:
                    self._last_bytes[phase] = max(
                        self._last_bytes[phase], int(completed)
                    )
                if total is not None:
                    self._total_bytes[phase] = max(0, int(total))
            if (
                not force
                and percent < 100
                and percent - self._last_sent[phase] < 5
                and now - self._last_at[phase] < 3
            ):
                return None
            self._last_sent[phase] = percent
            self._last_at[phase] = now
            return percent

    def _text(self, phase, percent, completed_bytes=None, total_bytes=None):
        icon = "📥" if phase == "download" else "📤"
        label = "Downloading" if phase == "download" else "Uploading"
        with self._state_lock:
            completed_bytes = (
                self._last_bytes[phase] if completed_bytes is None else completed_bytes
            )
            total_bytes = (
                self._total_bytes[phase] if total_bytes is None else total_bytes
            )
            percent = max(percent, self._percent[phase])
            phase_started = self._phase_started[phase]
        elapsed = max(time.monotonic() - phase_started, 0.001)
        speed = completed_bytes / elapsed
        eta = (total_bytes - completed_bytes) / speed if speed > 0 and total_bytes else 0
        try:
            import psutil
            cpu = psutil.cpu_percent(interval=None)
            ram = psutil.virtual_memory().percent
        except Exception:
            cpu, ram = 0.0, 0.0
        return (
            f"{icon} {label} Progress...\n\n"
            f"{self._bar(percent)} {percent:.2f}%\n\n"
            f"╭───────────────╮\n"
            f"📦 {_format_bytes(completed_bytes)} / {_format_bytes(total_bytes)}\n"
            f"🚀 {_format_bytes(speed)}/s\n"
            f"⏳ ETA : {_format_eta(eta)}\n"
            f"🕐 Elapsed : {int(elapsed)} Seconds\n"
            f"╰───────────────╯\n\n"
            f"🖥️ CPU: {cpu:.1f}%  💾 RAM: {ram:.1f}%\n"
            f"🆔 Task ID: {self.task_id}  _(use /cancel {self.task_id} to stop)_"
        )

    async def _edit(self, phase):
        async with self._edit_lock:
            try:
                with self._state_lock:
                    percent = self._percent[phase]
                    completed_bytes = self._last_bytes[phase]
                    total_bytes = self._total_bytes[phase]
                text = self._text(
                    phase, percent, completed_bytes, total_bytes,
                )
                if getattr(self.message, "photo", None):
                    await self.message.edit_caption(caption=text)
                else:
                    await self.message.edit_text(text)
            except Exception as exc:
                logger.debug("Progress update failed: %s", exc)

    async def update(self, phase, percent, force=False):
        accepted = self._accept(phase, percent, force=force)
        if accepted is not None:
            await self._edit(phase)

    def _schedule_edit(self, phase):
        try:
            self.loop.call_soon_threadsafe(
                lambda: self.loop.create_task(self._edit(phase))
            )
        except RuntimeError:
            logger.debug("Progress update ignored after task shutdown")

    def report_percent(self, phase, percent, *_):
        """Callback for the downloader thread."""
        if self.control and self.control.is_cancelled():
            raise video_downloader.DownloadCancelled
        completed = _[0] if len(_) >= 2 else None
        total = _[1] if len(_) >= 2 else None
        accepted = self._accept(phase, percent, completed, total)
        if accepted is None:
            return
        self._schedule_edit(phase)

    def report_bytes(self, phase, sent, total):
        if self.control and self.control.is_cancelled():
            raise video_downloader.DownloadCancelled
        percent = (sent / total * 100) if total else 0
        accepted = self._accept(phase, percent, sent, total)
        if accepted is None:
            return
        self._schedule_edit(phase)


def _state(user_id):
    if user_id not in user_state:
        user_state[user_id] = {}
    return user_state[user_id]


def _clear(user_id):
    user_state.pop(user_id, None)


def _authorized(user_id):
    return _admin_authorized(user_id) or db.has_premium(user_id)


def _admin_authorized(user_id):
    return db.is_owner(user_id)


async def premium_access_gate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Require owner/admin or active premium before processing bot interactions."""
    user = update.effective_user
    if user is None or _authorized(user.id):
        return

    message = update.effective_message
    text = getattr(message, "text", "") or ""
    query = update.callback_query
    command = text.split(maxsplit=1)[0].split("@", 1)[0] if text else ""
    if command == "/cancel":
        return
    if query and query.data == "cancel":
        return

    if query:
        if message:
            await query.answer()
        else:
            await query.answer(PREMIUM_REQUIRED_MESSAGE, show_alert=True)
            raise ApplicationHandlerStop
    if message:
        await message.reply_text(PREMIUM_REQUIRED_MESSAGE)
    raise ApplicationHandlerStop


def _format_premium_expiry(timestamp):
    return datetime.fromtimestamp(float(timestamp), timezone.utc).strftime(
        "%Y-%m-%d %H:%M UTC"
    )


async def _notify_premium_user(context, user_id, days, expiry, extended=False):
    action = (
        f"has been extended by {days} days"
        if extended else f"is active for {days} days"
    )
    try:
        await context.bot.send_message(
            user_id,
            f"✅ Premium access {action}, until "
            f"{_format_premium_expiry(expiry)}.",
        )
    except Exception as exc:
        logger.info(
            "Could not DM premium activation to user %s: %s", user_id, exc
        )


# ======================= Command Handlers =======================

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    name = update.effective_user.full_name

    if not _authorized(uid):
        await update.message.reply_text(
            f"👋 Hello {name}!\n\n{PREMIUM_REQUIRED_MESSAGE}"
        )
        return

    await update.message.reply_text(
        f"👋 Welcome {name}!\n\n"
        "🤖 *RareAnimes Download Bot*\n\n"
        "Commands:\n"
        "/upload <anime> - Download & upload anime\n"
        "/track <anime> - Track ongoing anime\n"
        "/untrack <anime> - Stop tracking\n"
        "/listtracked - List tracked anime\n"
        "/channel - Channel settings\n"
        "/quality - Show upload quality behavior\n"
        "/setmetadata @tag - Set your video filename format\n"
        "/remmetadata - Remove your filename format\n"
        "/setthumb - Reply to a photo with this command to set your thumbnail\n"
        "/remthumb - Remove your saved thumbnail\n"
        "/admins - Manage admins\n"
        "/stats - Bot statistics\n"
        "/cancel [task_id] - Stop a running task\n"
        "/help - This help",
        parse_mode=ParseMode.MARKDOWN,
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update.effective_user.id):
        await update.message.reply_text("Not authorized.")
        return
    await update.message.reply_text(
        "🤖 *RareAnimes Bot Help*\n\n"
        "/upload <anime> - Search, select ongoing/completed, download & upload\n"
        "/track <anime> - Track ongoing anime for auto new-episode upload\n"
        "/untrack <anime> - Stop tracking\n"
        "/listtracked - List tracked anime\n"
        "/channel - Set upload channel\n"
        "/setchannel @channel - Set channel by username\n"
        "/quality - Show upload quality behavior (360P/480P/720P/1080P when available)\n"
        "/setmetadata @tag - Set your personal filename prefix\n"
        "/remmetadata - Remove your personal filename prefix\n"
        "/setthumb - Reply to a photo with this command to set your thumbnail\n"
        "/remthumb - Remove your saved thumbnail\n"
        "/admins - Manage admins (owner)\n"
        "/stats - Bot statistics\n"
        "/help - This message\n"
        "/cancel - Cancel operation\n\n"
        "*Workflow:*\n"
        "1. /upload <name> → search results\n"
        "2. Select anime → Ongoing/Completed buttons\n"
        "3. Select language (if multiple)\n"
        "4. Bot downloads videos & uploads to channel",
        parse_mode=ParseMode.MARKDOWN,
    )


async def cmd_setmetadata(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text(
            "Usage: /setmetadata @your_tag\n"
            "The bot adds the anime, season, episode, and quality to each "
            "uploaded filename automatically."
        )
        return
    prefix = context.args[0].strip()
    if not re.fullmatch(r"@?[A-Za-z0-9_.-]{1,48}", prefix):
        await update.message.reply_text(
            "Use one tag with letters, numbers, dots, underscores, or hyphens."
        )
        return
    if not prefix.startswith("@"):
        prefix = "@" + prefix
    db.set_user_metadata_prefix(update.effective_user.id, prefix)
    shown_prefix = prefix if prefix.casefold().endswith("s") else f"{prefix}s"
    await update.message.reply_text(
        "✅ Your filename format is set. Example: "
        f"{shown_prefix}_Solo_Leveling_S01_EP01_480p.mp4\n"
        "Only uploads started by you use this format."
    )


async def cmd_remmetadata(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db.clear_user_metadata_prefix(update.effective_user.id)
    await update.message.reply_text("✅ Your custom filename format was removed.")


def _prepare_telegram_thumbnail(image_bytes):
    with Image.open(io.BytesIO(image_bytes)) as source:
        image = ImageOps.exif_transpose(source).convert("RGB")
    image.thumbnail((320, 320), Image.Resampling.LANCZOS)
    for quality in (85, 75, 65, 55, 45, 35):
        output = io.BytesIO()
        image.save(output, format="JPEG", quality=quality, optimize=True)
        result = output.getvalue()
        if len(result) <= 190_000:
            return result
    raise ValueError("The image could not be compressed to Telegram's thumbnail size.")


async def cmd_setthumb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    replied = message.reply_to_message
    photo = replied.photo[-1] if replied and replied.photo else None
    document = getattr(replied, "document", None) if replied else None
    image_file = photo
    if image_file is None and document and (document.mime_type or "").startswith("image/"):
        image_file = document
    if image_file is None:
        await message.reply_text(
            "Reply to a photo or image file with /setthumb to save it as your thumbnail."
        )
        return

    try:
        telegram_file = await context.bot.get_file(image_file.file_id)
        if telegram_file.file_size and telegram_file.file_size > 20 * 1024 * 1024:
            await message.reply_text("That image is too large. Send an image under 20 MB.")
            return
        image_bytes = bytes(await telegram_file.download_as_bytearray())
        thumbnail = _prepare_telegram_thumbnail(image_bytes)
    except Exception as exc:
        logger.warning("Could not process thumbnail for user %s: %s", update.effective_user.id, exc)
        await message.reply_text("❌ Could not use that image. Please reply to a valid photo.")
        return

    db.set_user_thumbnail(update.effective_user.id, thumbnail)
    await message.reply_text(
        "✅ Your thumbnail is saved and will be used only for uploads started by you."
    )


async def cmd_remthumb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db.clear_user_thumbnail(update.effective_user.id)
    await update.message.reply_text(
        "✅ Your saved thumbnail was removed. Telegram will generate previews for your videos."
    )


async def cmd_upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not _authorized(uid):
        await update.message.reply_text("Not authorized.")
        return

    args = context.args
    if not args:
        await update.message.reply_text("Usage: /upload <anime_name>\nExample: /upload naruto")
        return

    query = " ".join(args).strip()
    if len(query) < 2:
        await update.message.reply_text("Please provide a valid anime name.")
        return

    st = _state(uid)
    st["query"] = query
    st["track_mode"] = False

    msg = await update.message.reply_text(f"🔍 Searching for '{query}' on RareAnimes...")
    results = await asyncio.to_thread(scraper.search_anime, query)

    if not results:
        await msg.edit_text(f"❌ No results found for '{query}'\nTry a different name.")
        return

    st["results"] = [r.to_dict() for r in results]
    st["page"] = 0
    await msg.edit_text(
        f"✅ Found {len(results)} results. Select an anime:",
        reply_markup=search_results_keyboard(results, 0),
    )


async def cmd_track(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not _authorized(uid):
        await update.message.reply_text("Not authorized.")
        return

    args = context.args
    if not args:
        await update.message.reply_text("Usage: /track <anime_name>")
        return

    query = " ".join(args).strip()
    st = _state(uid)
    st["query"] = query
    st["track_mode"] = True

    msg = await update.message.reply_text(f"🔍 Searching for '{query}' to track...")
    results = await asyncio.to_thread(scraper.search_anime, query)
    if not results:
        await msg.edit_text(f"❌ No results found for '{query}'")
        return

    st["results"] = [r.to_dict() for r in results]
    st["page"] = 0
    await msg.edit_text(
        f"✅ Found {len(results)} results. Select an anime to track:",
        reply_markup=search_results_keyboard(results, 0),
    )


async def cmd_untrack(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not _authorized(uid):
        await update.message.reply_text("Not authorized.")
        return

    args = context.args
    if not args:
        await update.message.reply_text("Usage: /untrack <anime_name>")
        return

    query = " ".join(args).strip()
    tracked = db.list_tracked()
    found = False
    for t in tracked:
        if query.lower() in t["anime_name"].lower():
            db.remove_tracked(t["anime_name"], t["language"])
            await update.message.reply_text(f"✅ Stopped tracking: {t['anime_name']} [{t['language']}]")
            found = True
    if not found:
        await update.message.reply_text(f"❌ No tracked anime matching '{query}' found.")


async def cmd_listtracked(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not _authorized(uid):
        await update.message.reply_text("Not authorized.")
        return

    tracked = db.list_tracked()
    if not tracked:
        await update.message.reply_text("📭 No anime is being tracked.")
        return

    lines = ["📋 *Tracked Anime:*", ""]
    for t in tracked:
        lines.append(f"🎬 `{t['anime_name']}` [{t['language']}] - Last: S{t['last_season']}E{t['last_episode']}")
    lines.append("")
    lines.append("Use /untrack <name> to stop tracking.")

    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)
    await update.message.reply_text(
        "Click a tracked anime for more:",
        reply_markup=tracked_anime_keyboard([dict(t) for t in tracked]),
    )


async def cmd_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not _admin_authorized(uid):
        await update.message.reply_text("Owner/admin only.")
        return

    ch = db.get_config("channel_id")
    if ch:
        text = f"📺 *Current channel:* `{ch}`\n\nForward a message from target channel to change."
    else:
        text = (
            "📺 *No channel set.*\n\n"
            "1. Add this bot to your channel as admin\n"
            "2. Forward a message from your channel here,\n"
            "3. Or use /setchannel @mychannel"
        )
    await update.message.reply_text(
        text,
        reply_markup=channel_settings_keyboard(),
        parse_mode=ParseMode.MARKDOWN,
    )


async def cmd_setchannel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not _admin_authorized(uid):
        await update.message.reply_text("Owner/admin only.")
        return

    args = context.args
    if not args:
        await update.message.reply_text(
            "Usage: /setchannel <channel_id_or_username>\n"
            "Example: /setchannel @mychannel\n"
            "Or forward a message from your channel."
        )
        _state(uid)["awaiting_channel"] = True
        return

    channel = " ".join(args).strip()
    try:
        chat = await context.bot.get_chat(channel)
        db.set_config("channel_id", str(chat.id))
        db.set_config("channel_title", chat.title or chat.username or channel)
        await update.message.reply_text(
            f"✅ Channel set to: {chat.title or chat.username}\nID: {chat.id}"
        )
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}\nMake sure the bot is admin of the channel.")


async def cmd_quality(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not _admin_authorized(uid):
        await update.message.reply_text("Owner/admin only.")
        return
    await update.message.reply_text(
        "🎞️ Upload quality:\n"
        "The bot uploads 360P, 480P, 720P and 1080P versions when the source provides them.\n"
        "There is no single default quality.",
    )


async def cmd_admins(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from config import OWNER_ID
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("Owner only.")
        return
    admins = db.list_admins()
    names = "\n".join(f"  • {a['user_id']} (since {a['granted_at'][:10]})" for a in admins) or "  (none)"
    await update.message.reply_text(
        f"👥 Bot Admins:\n{names}\n\n"
        f"Add: /addadmin <user_id>\nRemove: buttons below",
        reply_markup=admin_management_keyboard(admins),
    )


async def cmd_addadmin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from config import OWNER_ID
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("Owner only.")
        return
    args = context.args
    if not args:
        await update.message.reply_text("Usage: /addadmin <user_id>")
        return
    try:
        nid = int(args[0])
    except ValueError:
        await update.message.reply_text("Invalid ID.")
        return
    db.add_admin(nid, update.effective_user.id)
    await update.message.reply_text(f"✅ User {nid} added as admin.")


async def cmd_removeadmin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from config import OWNER_ID
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("Owner only.")
        return
    args = context.args
    if not args:
        await update.message.reply_text("Usage: /removeadmin <user_id>")
        return
    try:
        rid = int(args[0])
    except ValueError:
        await update.message.reply_text("Invalid ID.")
        return
    db.remove_admin(rid)
    await update.message.reply_text(f"✅ User {rid} removed from admins.")


async def _grant_premium(context, admin_id, user_id, days):
    was_active = db.has_premium(user_id)
    expiry = db.grant_premium(user_id, days, admin_id)
    await _notify_premium_user(
        context, user_id, days, expiry, extended=was_active
    )
    return expiry


async def cmd_addpremium(update: Update, context: ContextTypes.DEFAULT_TYPE):
    admin_id = update.effective_user.id
    if not _admin_authorized(admin_id):
        await update.message.reply_text("Owner/admin only.")
        return
    if not context.args:
        await update.message.reply_text("Usage: /addpremium <user_id>")
        return
    try:
        user_id = int(context.args[0])
        if user_id <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("Please provide a valid numeric Telegram user ID.")
        return

    state = _state(admin_id)
    state["pending_premium_target"] = user_id
    state.pop("awaiting_custom_premium_days", None)
    await update.message.reply_text(
        f"Choose a premium duration for user {user_id}:",
        reply_markup=premium_duration_keyboard(user_id),
    )


async def cb_premium_duration(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    admin_id = query.from_user.id
    if not _admin_authorized(admin_id):
        await query.message.edit_text("Owner/admin only.")
        return

    parts = query.data.split(":")
    try:
        user_id = int(parts[2])
        duration = parts[3]
    except (IndexError, ValueError):
        await query.message.edit_text("❌ Invalid premium grant selection.")
        return

    state = _state(admin_id)
    if state.get("pending_premium_target") != user_id:
        await query.message.edit_text(
            "This premium request has expired. Run /addpremium again."
        )
        return

    if duration == "custom":
        state["awaiting_custom_premium_days"] = True
        await query.message.edit_text(
            f"Send the custom duration for user {user_id} as a whole number of days "
            "(1–3650)."
        )
        return

    if duration not in {"7", "15", "30"}:
        await query.message.edit_text("❌ Invalid premium duration.")
        return

    days = int(duration)
    expiry = await _grant_premium(context, admin_id, user_id, days)
    state.pop("pending_premium_target", None)
    await query.message.edit_text(
        f"✅ Premium granted to user {user_id} for {days} days.\n"
        f"Expires: {_format_premium_expiry(expiry)}"
    )


async def cmd_rempremium(update: Update, context: ContextTypes.DEFAULT_TYPE):
    admin_id = update.effective_user.id
    if not _admin_authorized(admin_id):
        await update.message.reply_text("Owner/admin only.")
        return
    if not context.args:
        await update.message.reply_text("Usage: /rempremium <user_id>")
        return
    try:
        user_id = int(context.args[0])
        if user_id <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("Please provide a valid numeric Telegram user ID.")
        return

    if not db.remove_premium(user_id):
        await update.message.reply_text(f"User {user_id} has no premium record.")
        return
    await update.message.reply_text(f"✅ Premium removed for user {user_id}.")
    try:
        await context.bot.send_message(
            user_id, "Your RareAnimes premium access has been removed."
        )
    except Exception as exc:
        logger.info("Could not DM premium removal to user %s: %s", user_id, exc)


async def cmd_listpremium(update: Update, context: ContextTypes.DEFAULT_TYPE):
    admin_id = update.effective_user.id
    if not _admin_authorized(admin_id):
        await update.message.reply_text("Owner/admin only.")
        return
    premiums = db.list_premiums(active_only=True)
    if not premiums:
        await update.message.reply_text("There are no active premium users.")
        return

    lines = ["⭐ Active premium users:", ""]
    for premium in premiums:
        lines.append(
            f"• {premium['user_id']} — until "
            f"{_format_premium_expiry(premium['expires_at'])}"
        )
    await update.message.reply_text("\n".join(lines))


async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not _admin_authorized(uid):
        await update.message.reply_text("Owner/admin only.")
        return
    tracked = db.list_tracked()
    admins = db.list_admins()
    ch = db.get_config("channel_id", "Not set")
    q = db.get_config("default_quality", DEFAULT_QUALITY)
    text = (
        f"📊 *Bot Statistics*\n\n"
        f"Tracked Anime: {len(tracked)}\n"
        f"Admins: {len(admins) + 1}\n"
        f"Channel: `{ch}`\n"
        f"Default Quality: {q}\n"
    )
    if tracked:
        text += "\n*Tracked:*\n"
        for t in tracked:
            text += f"  • {t['anime_name']} [{t['language']}] - S{t['last_season']}E{t['last_episode']}\n"
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    task_id = context.args[0].strip() if context.args else None
    cancelled = _cancel_tasks(update.effective_user.id, task_id)
    _clear(update.effective_user.id)
    if task_id and not cancelled:
        recent = _recent_task(update.effective_user.id, task_id)
        if recent:
            status = "cancelled" if recent["status"] == "cancelled" else "already completed"
            await update.message.reply_text(
                f"ℹ️ Task `{task_id}` has {status}; there is nothing left to stop.",
                parse_mode=ParseMode.MARKDOWN,
            )
        else:
            await update.message.reply_text(
                f"❌ No active task matches `{task_id}`. Check the task ID or use `/cancel`.",
                parse_mode=ParseMode.MARKDOWN,
            )
    elif cancelled:
        await update.message.reply_text(
            f"✅ Cancellation requested for `{task_id or 'active task(s)'}`. "
            "The bot will stop cleanly after the current transfer step.",
            parse_mode=ParseMode.MARKDOWN,
        )
    else:
        await update.message.reply_text("ℹ️ There is no active task. Current selection cleared.")


# ======================= Message Handlers =======================

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = getattr(update, "effective_user", None)
    message = getattr(update, "effective_message", None) or getattr(
        update, "message", None
    )
    if user is None or message is None:
        logger.debug("Ignoring a message update without an associated user.")
        return
    uid = user.id
    if not _authorized(uid):
        await update.message.reply_text(PREMIUM_REQUIRED_MESSAGE)
        return
    from autoupload_handlers import handle_pending_text
    if await handle_pending_text(update, context):
        return

    st = _state(uid)
    if st.get("awaiting_custom_premium_days"):
        if not _admin_authorized(uid):
            st.pop("awaiting_custom_premium_days", None)
            st.pop("pending_premium_target", None)
            await update.message.reply_text("Owner/admin only.")
            return
        try:
            days = int((update.message.text or "").strip())
        except ValueError:
            await update.message.reply_text(
                "Send a whole number of days from 1 to 3650."
            )
            return
        if not 1 <= days <= 3650:
            await update.message.reply_text(
                "Custom premium duration must be from 1 to 3650 days."
            )
            return
        user_id = st.get("pending_premium_target")
        if not user_id:
            st.pop("awaiting_custom_premium_days", None)
            await update.message.reply_text(
                "The premium request expired. Run /addpremium again."
            )
            return
        expiry = await _grant_premium(context, uid, user_id, days)
        st.pop("awaiting_custom_premium_days", None)
        st.pop("pending_premium_target", None)
        await update.message.reply_text(
            f"✅ Premium granted to user {user_id} for {days} days.\n"
            f"Expires: {_format_premium_expiry(expiry)}"
        )
        return

    if update.message.forward_from_chat:
        if st.get("awaiting_channel"):
            if not _admin_authorized(uid):
                st.pop("awaiting_channel", None)
                await update.message.reply_text("Owner/admin only.")
                return
            chat = update.message.forward_from_chat
            if chat.type in ("channel", "supergroup"):
                db.set_config("channel_id", str(chat.id))
                db.set_config("channel_title", chat.title or chat.username)
                await update.message.reply_text(
                    f"✅ Channel set to: {chat.title or chat.username}\nID: {chat.id}"
                )
                st.pop("awaiting_channel", None)
    elif update.message.text and st.get("awaiting_admin"):
        from config import OWNER_ID
        if uid != OWNER_ID:
            st.pop("awaiting_admin", None)
            await update.message.reply_text("Owner only.")
            return
        try:
            admin_id = int(update.message.text.strip())
        except ValueError:
            await update.message.reply_text("Please send a numeric Telegram user ID.")
            return
        db.add_admin(admin_id, uid)
        _state(uid).pop("awaiting_admin", None)
        await update.message.reply_text(f"✅ User {admin_id} added as admin.")


# ======================= Callback Handlers =======================

async def cb_anime_select(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """User selected an anime from search results."""
    query = update.callback_query
    await query.answer()

    uid = query.from_user.id
    data = query.data
    # Format: anime_select:INDEX
    parts = data.split(":")
    try:
        index = int(parts[1])
    except (IndexError, ValueError):
        await query.message.edit_text("❌ Invalid selection.")
        return

    st = _state(uid)
    results_list = st.get("results", [])
    if index >= len(results_list) or index < 0:
        await query.message.edit_text("❌ Selection out of range.")
        return

    anime_url = results_list[index]["url"]
    st["selected_url"] = anime_url

    await query.message.edit_text("🔍 Analyzing anime page...")

    info = await asyncio.to_thread(scraper.get_anime_page_info, anime_url)

    if isinstance(info, dict) and info.get("error"):
        await query.message.edit_text(f"❌ Error: {info['error']}")
        _clear(uid)
        return

    st["anime_info"] = info

    title = info.get("title", "")
    status = info.get("status", "unknown")
    categories = info.get("categories", [])
    languages = info.get("languages", [])

    status_str = "🔔 Ongoing" if status == "ongoing" else "✅ Completed" if status == "completed" else "🔔/✅"
    lang_str = ", ".join(languages) if languages else "Hindi"

    text = (
        f"🎬 *{title}*\n\n"
        f"Status: {status_str}\n"
        f"Languages: {lang_str}\n"
        f"Categories: {', '.join(categories)}\n"
        f"Episodes: {info.get('episode_count', '?')}\n\n"
        f"Select action:"
    )

    show_ongoing = status in ("ongoing", "unknown")
    show_completed = status in ("completed", "unknown", "ongoing")
    st["languages"] = languages if languages else ["hindi"]

    await query.message.edit_text(
        text,
        reply_markup=status_keyboard(anime_url, show_ongoing, show_completed),
        parse_mode=ParseMode.MARKDOWN,
    )


async def cb_search_page(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    uid = query.from_user.id
    data = query.data
    page = int(data.split(":")[1])

    st = _state(uid)
    results = st.get("results", [])
    if not results:
        await query.message.edit_text("No results. Search again.")
        return

    st["page"] = page
    from scraper import AnimeResult
    ar = [AnimeResult(**r) for r in results]
    await query.message.edit_text(
        f"✅ Select an anime (page {page + 1}):",
        reply_markup=search_results_keyboard(ar, page),
    )


async def _show_status_selection(message, st):
    info = st.get("anime_info", {})
    title = info.get("title", "")
    status = info.get("status", "unknown")
    categories = info.get("categories", [])
    languages = st.get("languages", ["hindi"])
    status_str = (
        "🔔 Ongoing" if status == "ongoing"
        else "✅ Completed" if status == "completed" else "🔔/✅"
    )
    text = (
        f"🎬 *{title}*\n\n"
        f"Status: {status_str}\n"
        f"Languages: {', '.join(languages) if languages else 'Hindi'}\n"
        f"Categories: {', '.join(categories)}\n"
        f"Episodes: {info.get('episode_count', '?')}\n\n"
        "Select action:"
    )
    await message.edit_text(
        text,
        reply_markup=status_keyboard(
            st.get("selected_url", ""),
            status in ("ongoing", "unknown"),
            status in ("completed", "unknown", "ongoing"),
        ),
        parse_mode=ParseMode.MARKDOWN,
    )


async def cb_upload_back(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Restore the prior screen in the /upload selection flow."""
    query = update.callback_query
    uid = query.from_user.id
    st = _state(uid)
    action = query.data.removeprefix("upload:back_")
    await query.answer()

    if action == "search":
        results = st.get("results", [])
        if not results:
            await query.message.edit_text("Search results expired. Start /upload again.")
            return
        from scraper import AnimeResult
        page = max(0, int(st.get("page", 0) or 0))
        ar = [AnimeResult(**result) for result in results]
        page = min(page, max(0, (len(ar) - 1) // 8))
        st["page"] = page
        await query.message.edit_text(
            f"✅ Found {len(ar)} results. Select an anime:",
            reply_markup=search_results_keyboard(ar, page),
        )
        return

    if action == "status":
        await _show_status_selection(query.message, st)
        return

    if action == "previous":
        languages = st.get("languages", ["hindi"])
        if st.get("language_selection_shown") and len(languages) > 1:
            status = st.get("selected_status", "completed")
            await query.message.edit_text(
                "🌐 *Select Language*\n\nChoose download language:",
                reply_markup=language_keyboard(languages, status),
                parse_mode=ParseMode.MARKDOWN,
            )
        else:
            await _show_status_selection(query.message, st)
        return

    await query.message.edit_text("This selection has expired. Start /upload again.")


async def cb_status_select(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """User selected Ongoing or Completed."""
    query = update.callback_query
    await query.answer()

    uid = query.from_user.id
    data = query.data
    # Format: status:ongoing  or  status:completed
    parts = data.split(":")
    status = parts[1] if len(parts) > 1 else ""

    st = _state(uid)
    anime_url = st.get("selected_url", "")
    st["selected_status"] = status

    languages = st.get("languages", ["hindi"])

    if len(languages) > 1:
        st["language_selection_shown"] = True
        text = "🌐 *Select Language*\n\nChoose download language:"
        await query.message.edit_text(
            text,
            reply_markup=language_keyboard(languages, f"{status}"),
            parse_mode=ParseMode.MARKDOWN,
        )
    else:
        st["language_selection_shown"] = False
        lang = languages[0] if languages else "hindi"
        await _proceed_status(query, context, uid, st, anime_url, status, lang)


async def cb_language_select(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """User selected a language."""
    query = update.callback_query
    await query.answer()

    uid = query.from_user.id
    data = query.data
    # Format: lang:LANGUAGE:status
    parts = data.split(":")
    language = parts[1] if len(parts) > 1 else "hindi"
    status = parts[2] if len(parts) > 2 else "completed"

    st = _state(uid)
    anime_url = st.get("selected_url", "")

    await _proceed_status(query, context, uid, st, anime_url, status, language)


async def _show_episode_selector(
    query, uid, anime_url, language, title, episodes=None
):
    """Load episodes and show one button per episode before starting a job."""
    if episodes is None:
        season_data = await asyncio.to_thread(
            scraper.get_season_episodes, anime_url, language
        )
        if isinstance(season_data, dict) and season_data.get("error"):
            await query.message.edit_text(f"❌ Error: {season_data['error']}")
            _clear(uid)
            return
        episodes = (
            season_data.get("episodes", [])
            if isinstance(season_data, dict) else []
        )

    if not episodes:
        await query.message.edit_text("❌ No episodes found.")
        _clear(uid)
        return

    st = _state(uid)
    st["episodes"] = [episode.to_dict() for episode in episodes]
    st["selected_episodes"] = set()
    st["episode_page"] = 0
    st["selected_language"] = language
    st["selected_status"] = "completed"
    st["selected_url"] = anime_url
    st["anime_title"] = title
    await query.message.edit_text(
        f"✅ *{title}*\n\n"
        f"Select the episodes to download, or choose *All Episodes*.\n"
        f"Found: {len(episodes)}",
        reply_markup=episode_keyboard(episodes, page=0),
        parse_mode=ParseMode.MARKDOWN,
    )


def _episodes_from_state(st):
    return [
        scraper.EpisodeInfo(
            item["season"], item["episode"], item["download_url"],
            item.get("title", ""), item.get("language", "hindi"),
            item.get("episode_type", "episode"),
            item.get("audio_variant", ""),
        )
        for item in st.get("episodes", [])
    ]


async def cb_episode_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = query.from_user.id
    st = _state(uid)
    episodes = _episodes_from_state(st)
    try:
        index = int(query.data.split(":")[2])
    except (IndexError, ValueError):
        await query.answer("Invalid episode.", show_alert=True)
        return
    if index < 0 or index >= len(episodes):
        await query.answer("Episode no longer exists.", show_alert=True)
        return
    await query.answer()
    selected = st.setdefault("selected_episodes", set())
    key = f"{episodes[index].season}_{episodes[index].episode}"
    if key in selected:
        selected.remove(key)
    else:
        selected.add(key)
    await query.message.edit_reply_markup(
        reply_markup=episode_keyboard(
            episodes, selected, page=st.get("episode_page", 0)
        )
    )


async def cb_episode_page(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = query.from_user.id
    st = _state(uid)
    episodes = _episodes_from_state(st)
    try:
        page = int(query.data.split(":")[2])
    except (IndexError, ValueError):
        await query.answer("Invalid page.", show_alert=True)
        return
    page_count = max(1, (len(episodes) + 7) // 8)
    if not episodes or page < 0 or page >= page_count:
        await query.answer("That episode page is no longer available.", show_alert=True)
        return
    st["episode_page"] = page
    await query.answer()
    await query.message.edit_reply_markup(
        reply_markup=episode_keyboard(
            episodes,
            st.get("selected_episodes", set()),
            page=page,
        )
    )


async def _start_completed_download(context, query, uid, episodes):
    st = _state(uid)
    if st.get("episode_download_started"):
        await query.answer("This download has already started.", show_alert=True)
        return
    st["episode_download_started"] = True
    title = st.get("anime_title") or st.get("anime_info", {}).get("title", "Anime")
    language = st.get("selected_language", "hindi")
    anime_url = st.get("selected_url", "")
    destination_id = db.get_config("channel_id") or str(uid)
    await query.answer()
    try:
        try:
            await query.message.edit_reply_markup(reply_markup=None)
        except Exception:
            logger.debug("Could not disable episode selection buttons")
        progress_message = await context.bot.send_message(
            chat_id=query.message.chat_id,
            text=(
                f"📥 Starting download for {len(episodes)} episode(s)...\n"
                "Use /cancel to request a clean stop."
            ),
        )
        _launch_task(
            context,
            uid,
            lambda control: _process_completed(
                context, uid, query, anime_url, language, title,
                destination_id, episodes=episodes, control=control,
                progress_message=progress_message,
            ),
        )
    except Exception:
        st.pop("episode_download_started", None)
        logger.exception("Could not start the selected-episode download")
        try:
            await query.message.reply_text(
                "❌ Could not start the download. Please try the button again."
            )
        except Exception:
            pass


async def cb_episode_download(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = query.from_user.id
    st = _state(uid)
    episodes = _episodes_from_state(st)
    selected_keys = st.get("selected_episodes", set())
    selected = [
        episode for episode in episodes
        if f"{episode.season}_{episode.episode}" in selected_keys
    ]
    if not selected:
        await query.answer("Select at least one episode first.", show_alert=True)
        return
    await _start_completed_download(context, query, uid, selected)


async def cb_episode_download_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = query.from_user.id
    st = _state(uid)
    episodes = _episodes_from_state(st)
    if not episodes:
        await query.answer()
        await query.message.edit_text("❌ Episode selection expired. Use /upload again.")
        return
    await _start_completed_download(context, query, uid, episodes)


async def _proceed_status(query, context, uid, st, anime_url, status, language):
    """Proceed based on selected status and language."""
    info = st.get("anime_info", {})
    title = info.get("title", "")

    # Use the configured channel when available; otherwise deliver to the
    # requesting user's private chat.
    channel_id = db.get_config("channel_id") or str(uid)
    destination_note = (
        "configured channel"
        if db.get_config("channel_id")
        else "your private chat (no upload channel is configured)"
    )

    st["selected_language"] = language
    st["selected_status"] = status
    st["selected_url"] = anime_url

    season_data = await asyncio.to_thread(
        scraper.get_season_episodes, anime_url, language
    )
    if isinstance(season_data, dict) and season_data.get("error"):
        await query.message.edit_text(f"❌ Error: {season_data['error']}")
        _clear(uid)
        return
    episodes = (
        season_data.get("episodes", [])
        if isinstance(season_data, dict) else []
    )
    st["episodes"] = [episode.to_dict() for episode in episodes]
    st["selected_episodes"] = set()
    st["episode_page"] = 0
    st["available_episode_count"] = len(episodes)

    site_episode_count = info.get("episode_count")
    language_is_partial = _is_language_partial_release(
        status, site_episode_count, len(episodes)
    )

    if status == "ongoing" or language_is_partial:
        availability = (
            f"Available in {language}: {len(episodes)} of "
            f"{site_episode_count} site episodes.\n\n"
            if language_is_partial else
            f"Available in {language}: {len(episodes)} episode(s).\n\n"
        )
        explanation = (
            "The site marks this title completed, but this language has "
            "fewer episodes released."
            if language_is_partial else
            "Choose whether to backfill current episodes or wait for future releases."
        )
        await query.message.edit_text(
            f"🔔 *Ongoing Tracking*\n\n"
            f"Anime: {title}\n"
            f"Language: {language}\n\n"
            f"{explanation}\n"
            f"{availability}"
            f"Destination: {destination_note}\n\n"
            f"Choose how to start:",
            reply_markup=upload_ongoing_confirmation_keyboard(title, language, anime_url),
            parse_mode=ParseMode.MARKDOWN,
        )
    elif status == "completed":
        await _show_episode_selector(
            query, uid, anime_url, language, title, episodes=episodes
        )


async def cb_ongoing_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Select backfill episodes or start tracking future releases only."""
    query = update.callback_query
    uid = query.from_user.id
    st = _state(uid)

    anime_url = st.get("selected_url", "")
    info = st.get("anime_info", {})
    title = info.get("title", "")
    language = st.get("selected_language", "hindi")
    action = query.data.removeprefix("ongoing:")

    if action == "backfill":
        episodes = _episodes_from_state(st)
        if not episodes:
            await query.answer("No previous episodes are available.", show_alert=True)
            return
        st["ongoing_selected_episodes"] = set()
        st["ongoing_episode_page"] = 0
        await query.answer()
        await _show_ongoing_episode_selection(query.message, st, edit=True)
        return

    if action == "back_status":
        await query.answer()
        await _show_ongoing_confirmation(query.message, st)
        return

    if action.startswith("toggle:"):
        episodes = _episodes_from_state(st)
        try:
            index = int(action.split(":", 1)[1])
        except ValueError:
            await query.answer("Invalid episode.", show_alert=True)
            return
        if index < 0 or index >= len(episodes):
            await query.answer("Episode no longer exists.", show_alert=True)
            return
        selected = st.setdefault("ongoing_selected_episodes", set())
        if index in selected:
            selected.remove(index)
        else:
            selected.add(index)
        await query.answer()
        await _show_ongoing_episode_selection(query.message, st, edit=True)
        return

    if action.startswith("page:"):
        episodes = _episodes_from_state(st)
        try:
            page = int(action.split(":", 1)[1])
        except ValueError:
            await query.answer("Invalid page.", show_alert=True)
            return
        page_count = max(1, (len(episodes) + 7) // 8)
        if not episodes or page < 0 or page >= page_count:
            await query.answer("That episode page is no longer available.", show_alert=True)
            return
        st["ongoing_episode_page"] = page
        await query.answer()
        await _show_ongoing_episode_selection(query.message, st, edit=True)
        return

    if action == "selected":
        episodes = _episodes_from_state(st)
        indices = st.get("ongoing_selected_episodes", set())
        selected_episodes = [
            episode for index, episode in enumerate(episodes) if index in indices
        ]
        if not selected_episodes:
            await query.answer("Select at least one episode first.", show_alert=True)
            return
        await query.answer()
        await _launch_ongoing_tracking(
            query, context, uid, st, selected_episodes=selected_episodes
        )
        return

    if action == "all":
        episodes = _episodes_from_state(st)
        if not episodes:
            await query.answer("No previous episodes are available.", show_alert=True)
            return
        await query.answer()
        await _launch_ongoing_tracking(
            query, context, uid, st, selected_episodes=episodes
        )
        return

    if action == "future":
        await query.answer()
        await _launch_ongoing_tracking(
            query, context, uid, st, upload_existing=False
        )
        return

    await query.answer("That action is not available.", show_alert=True)


def _ongoing_episode_selection_text(st):
    episodes = st.get("episodes", [])
    page_count = max(1, (len(episodes) + 7) // 8)
    page = min(st.get("ongoing_episode_page", 0), page_count - 1)
    return (
        f"🔔 {st.get('anime_info', {}).get('title', 'Anime')} — choose previous episodes\n"
        f"Page {page + 1}/{page_count} • "
        f"Selected: {len(st.get('ongoing_selected_episodes', set()))}\n"
        "Tap episodes to select them, then choose Download Selected."
    )


async def _show_ongoing_episode_selection(message, st, edit=False):
    episodes = _episodes_from_state(st)
    selected_indices = st.get("ongoing_selected_episodes", set())
    selected_keys = {
        f"{episodes[index].season}_{episodes[index].episode}"
        for index in selected_indices
        if 0 <= index < len(episodes)
    }
    markup = episode_keyboard(
        episodes,
        selected_keys,
        page=st.get("ongoing_episode_page", 0),
        callback_prefix="ongoing",
        back_callback="ongoing:back_status",
    )
    text = _ongoing_episode_selection_text(st)
    if edit:
        await message.edit_text(text, reply_markup=markup)
    else:
        await message.reply_text(text, reply_markup=markup)


async def _show_ongoing_confirmation(message, st):
    info = st.get("anime_info", {})
    title = info.get("title", "")
    language = st.get("selected_language", "hindi")
    configured_channel = db.get_config("channel_id")
    destination_note = (
        "configured channel"
        if configured_channel else "your private chat (no upload channel is configured)"
    )
    await message.edit_text(
        f"🔔 *Ongoing Tracking*\n\n"
        f"Anime: {title}\n"
        f"Language: {language}\n\n"
        "Choose whether to upload previous episodes or only future releases.\n"
        f"Destination: {destination_note}",
        reply_markup=upload_ongoing_confirmation_keyboard(
            title, language, st.get("selected_url", "")
        ),
        parse_mode=ParseMode.MARKDOWN,
    )


async def _launch_ongoing_tracking(
    query, context, uid, st, upload_existing=True, selected_episodes=None,
):
    anime_url = st.get("selected_url", "")
    title = st.get("anime_info", {}).get("title", "")
    language = st.get("selected_language", "hindi")
    channel_id = db.get_config("channel_id") or str(uid)
    if upload_existing:
        count = len(selected_episodes or [])
        status_text = (
            f"Backfilling {count} selected episode(s), then monitoring new releases..."
        )
    else:
        status_text = "Skipping current episodes and monitoring future releases..."
    await query.message.edit_text(
        f"🔔 Tracking started for '{title}'\n"
        f"{status_text}\n"
        f"Destination: {'configured channel' if db.get_config('channel_id') else 'your private chat'}"
    )
    _launch_task(
        context,
        uid,
        lambda control: _start_ongoing(
            context, uid, query, anime_url, title, language, channel_id,
            control, upload_existing=upload_existing,
            selected_episodes=selected_episodes,
        ),
    )


async def _process_completed(
    context, uid, query, anime_url, language, title, destination_id,
    episodes=None, control=None, progress_message=None,
):
    """Download and upload every available target quality for every episode."""
    bot = context.bot
    progress_message = progress_message or query.message
    uploaded_count = 0
    skipped_count = 0
    failures = []

    if episodes is None:
        season_data = await asyncio.to_thread(scraper.get_season_episodes, anime_url, language)
        if isinstance(season_data, dict) and season_data.get("error"):
            await _edit_progress_message(progress_message, f"❌ Error: {season_data['error']}")
            _clear(uid)
            return
        episodes = season_data.get("episodes", [])
    if not episodes:
        await _edit_progress_message(progress_message, "❌ No episodes found.")
        _clear(uid)
        return

    total = len(episodes)
    await _edit_progress_message(
        progress_message,
        f"📥 Found {total} episodes.\n"
        "Uploading 360P, 720P and 1080P when available..."
    )

    for i, ep in enumerate(episodes):
        if control:
            control.check()
        s, e = ep.season, ep.episode
        episode_label = f"S{s:02d}E{e:02d}"
        await _edit_progress_message(
            progress_message,
            f"🔎 Finding qualities for {episode_label}\n"
            f"Episode {i + 1}/{total}"
        )
        links, ad = await _wait_for_target_quality(
            ep.download_url, progress_message, episode_label, control
        )

        if not links or isinstance(links, dict):
            err = links.get("error", "Unknown") if isinstance(links, dict) else "No links"
            failures.append(f"{episode_label}: {err}")
            continue

        selected_qualities = _select_qualities(links)
        if not selected_qualities:
            avail = ", ".join(link.quality for link in links)
            failures.append(f"{episode_label}: target qualities unavailable ({avail})")
            continue

        for sel in selected_qualities:
            if not db.claim_episode_upload(
                title, s, e, language, sel.quality, destination_id
            ):
                skipped_count += 1
                continue

            fname = (
                f"{title.replace('/', '_').replace(chr(92), '_').replace(' ', '_')}_"
                f"S{s:02d}E{e:02d}_{sel.quality.replace(' ', '_')}.mp4"
            )
            fpath = os.path.join(DOWNLOAD_DIR, fname)
            progress = _ProgressReporter(
                progress_message,
                episode_label,
                i + 1,
                total,
                sel.quality,
                control,
            )

            await progress.update("download", 0, force=True)
            try:
                dl = await _download_quality_until_ready(
                    ep.download_url, sel, fpath, progress, episode_label, control
                )
            except (video_downloader.DownloadCancelled, TaskStopped):
                db.release_episode_claim(
                    title, s, e, language, sel.quality, destination_id
                )
                video_downloader.cleanup_file(fpath)
                if control:
                    control.cancel()
                await _edit_progress_message(
                    progress_message, f"🛑 Task `{progress.task_id}` cancelled."
                )
                if control:
                    control.cancel_announced = True
                _clear(uid)
                return
            except Exception as ex:
                db.release_episode_claim(
                    title, s, e, language, sel.quality, destination_id
                )
                video_downloader.cleanup_file(fpath)
                failures.append(
                    f"{episode_label} {sel.quality}: download failed ({str(ex)[:180]})"
                )
                continue
            if not dl:
                db.release_episode_claim(
                    title, s, e, language, sel.quality, destination_id
                )
                failures.append(f"{episode_label} {sel.quality}: video download failed")
                video_downloader.cleanup_file(fpath)
                continue
            await progress.update("download", 100, force=True)

            await progress.update("upload", 0, force=True)
            try:
                if control:
                    control.check()
                preferences = db.get_user_preferences(uid)
                upload_filename = format_upload_filename(
                    fname, preferences.get("metadata_prefix"),
                    title, s, e, sel.quality,
                )
                caption = (
                    f"🎬 {title}\n📺 {episode_label}\n"
                    f"🎞️ {sel.quality} ({sel.size})\n🌐 {language}"
                )
                receipt = await upload_video(
                    bot,
                    destination_id,
                    fpath,
                    caption,
                    upload_filename,
                    progress_callback=lambda sent, total_bytes: progress.report_bytes(
                        "upload", sent, total_bytes
                    ),
                    thumbnail=preferences.get("thumbnail"),
                )
                await progress.update("upload", 100, force=True)
                file_hash = video_downloader.get_file_hash(fpath)
                db.record_upload(
                    title,
                    s,
                    e,
                    language,
                    sel.quality,
                    file_hash,
                    receipt.message_id,
                    destination_id,
                )
                uploaded_count += 1
            except Exception as ex:
                if isinstance(ex, (video_downloader.DownloadCancelled, TaskStopped)):
                    video_downloader.cleanup_file(fpath)
                    await _edit_progress_message(
                        progress_message,
                        f"🛑 Task `{progress.task_id}` cancelled."
                    )
                    if control:
                        control.cancel_announced = True
                    _clear(uid)
                    return
                error_text = str(ex).strip() or ex.__class__.__name__
                failures.append(
                    f"{episode_label} {sel.quality}: upload failed ({error_text[:180]})"
                )
            finally:
                video_downloader.cleanup_file(fpath)
                db.release_episode_claim(
                    title, s, e, language, sel.quality, destination_id
                )

    if failures:
        details = "\n".join(f"• {failure}" for failure in failures[:3])
        if len(failures) > 3:
            details += f"\n• …and {len(failures) - 3} more"
        summary = (
            f"⚠️ Finished with errors for {title}.\n"
            f"Uploaded files: {uploaded_count}\n"
            f"Already uploaded: {skipped_count}\n"
            f"Failed files: {len(failures)}\n\n"
            f"{details}"
        )
    else:
        summary = (
            f"✅ Done! Uploaded {uploaded_count} files for {title}."
            + (f"\nAlready uploaded: {skipped_count}" if skipped_count else "")
        )
    await _edit_progress_message(progress_message, summary)
    _clear(uid)


async def _start_ongoing(
    context, uid, query, anime_url, title, language, channel_id, control=None,
    upload_existing=True, selected_episodes=None,
):
    """Start tracking and optionally upload all or selected existing episodes."""
    bot = context.bot
    uploaded_count = 0
    skipped_count = 0
    failed_count = 0

    season_data = await asyncio.to_thread(scraper.get_season_episodes, anime_url, language)
    episodes = []
    if isinstance(season_data, dict) and season_data.get("error"):
        await bot.send_message(
            uid, f"❌ Could not load episodes for {title}: {season_data['error']}"
        )
        _clear(uid)
        return
    if isinstance(season_data, dict):
        episodes = season_data.get("episodes", [])

    if not episodes and language.lower() != "hindi":
        info = await asyncio.to_thread(scraper.get_anime_page_info, anime_url)
        if isinstance(info, dict) and info.get("error"):
            await bot.send_message(
                uid, f"❌ Could not load episodes for {title}: {info['error']}"
            )
            _clear(uid)
            return
        mq = info.get("watch_multiquality_url")
        if mq:
            episodes = await asyncio.to_thread(scraper.get_multiquality_episodes, mq)

    if not episodes:
        db.add_tracked(
            title, anime_url, None, language, "0", 1, channel_id,
            owner_id=uid,
        )
        await bot.send_message(
            uid,
            f"🔔 Tracking {title} [{language}] from episode 1. "
            "No episodes in this language are available yet; future releases "
            "will be checked automatically.",
        )
        _clear(uid)
        return

    episodes.sort(
        key=lambda episode: (int(episode.season or 0), int(episode.episode or 0))
    )
    latest = episodes[-1]
    selected_keys = None
    if selected_episodes is not None:
        selected_keys = {
            (int(ep.season or 0), int(ep.episode or 0))
            for ep in selected_episodes
        }
    baseline_episodes = []
    if not upload_existing or selected_keys is not None:
        baseline_episodes = [
            f"{int(ep.season or 0)}:{int(ep.episode or 0)}"
            for ep in episodes
            if not upload_existing
            or (int(ep.season or 0), int(ep.episode or 0)) not in selected_keys
        ]
    db.add_tracked(
        title, anime_url, latest.download_url, language,
        str(latest.episode), latest.season, channel_id, owner_id=uid,
        baseline_episodes=baseline_episodes,
    )

    await bot.send_message(uid, f"🔔 Tracking: {title} [{language}]\nLatest: S{latest.season:02d}E{latest.episode:02d}")
    if not upload_existing:
        await bot.send_message(
            uid,
            f"⏭️ Skipped the {len(episodes)} episode(s) already available in "
            f"{language}. Only later releases will be uploaded.",
        )
        _clear(uid)
        return

    episodes_to_upload = (
        [
            ep for ep in episodes
            if (int(ep.season or 0), int(ep.episode or 0)) in selected_keys
        ]
        if selected_keys is not None else episodes
    )

    # Process existing episodes in every available target quality.
    for i, ep in enumerate(episodes_to_upload):
        if control:
            control.check()
        episode_label = f"S{ep.season:02d}E{ep.episode:02d}"
        progress_message = await bot.send_message(
            uid,
            f"🔎 Finding qualities for {episode_label}\n"
            f"Episode {i + 1}/{len(episodes)}",
        )
        links, ad = await _wait_for_target_quality(
            ep.download_url, progress_message, episode_label, control
        )
        if not links or isinstance(links, dict):
            failed_count += 1
            continue

        selected_qualities = _select_qualities(links)
        if not selected_qualities:
            failed_count += 1
            continue

        for sel in selected_qualities:
            if not db.claim_episode_upload(
                title, ep.season, ep.episode, language, sel.quality, channel_id
            ):
                skipped_count += 1
                continue

            fname = (
                f"{title.replace('/', '_').replace(chr(92), '_').replace(' ', '_')}_"
                f"{episode_label}_{sel.quality.replace(' ', '_')}.mp4"
            )
            fpath = os.path.join(DOWNLOAD_DIR, fname)
            progress = _ProgressReporter(
                progress_message,
                episode_label,
                i + 1,
                len(episodes),
                sel.quality,
                control,
            )
            await progress.update("download", 0, force=True)
            try:
                dl = await _download_quality_until_ready(
                    ep.download_url, sel, fpath, progress, episode_label, control
                )
            except (video_downloader.DownloadCancelled, TaskStopped):
                db.release_episode_claim(
                    title, ep.season, ep.episode, language, sel.quality, channel_id
                )
                video_downloader.cleanup_file(fpath)
                if control:
                    control.cancel()
                await bot.send_message(uid, f"🛑 Task `{progress.task_id}` cancelled.")
                if control:
                    control.cancel_announced = True
                _clear(uid)
                return
            except Exception as ex:
                db.release_episode_claim(
                    title, ep.season, ep.episode, language, sel.quality, channel_id
                )
                video_downloader.cleanup_file(fpath)
                failed_count += 1
                logger.exception("Download failed for %s %s", title, episode_label)
                continue
            if not dl:
                db.release_episode_claim(
                    title, ep.season, ep.episode, language, sel.quality, channel_id
                )
                failed_count += 1
                video_downloader.cleanup_file(fpath)
                continue
            await progress.update("download", 100, force=True)

            await progress.update("upload", 0, force=True)
            try:
                if control:
                    control.check()
                preferences = db.get_user_preferences(uid)
                upload_filename = format_upload_filename(
                    fname, preferences.get("metadata_prefix"),
                    title, ep.season, ep.episode, sel.quality,
                )
                caption = (
                    f"🎬 {title}\n📺 {episode_label}\n"
                    f"🎞️ {sel.quality} ({sel.size})\n🌐 {language}\n🔔 NEW EPISODE"
                )
                receipt = await upload_video(
                    bot,
                    channel_id,
                    fpath,
                    caption,
                    upload_filename,
                    progress_callback=lambda sent, total_bytes: progress.report_bytes(
                        "upload", sent, total_bytes
                    ),
                    thumbnail=preferences.get("thumbnail"),
                )
                await progress.update("upload", 100, force=True)
                file_hash = video_downloader.get_file_hash(fpath)
                db.record_upload(
                    title,
                    ep.season,
                    ep.episode,
                    language,
                    sel.quality,
                    file_hash,
                    receipt.message_id,
                    channel_id,
                )
                uploaded_count += 1
                await notify_episode_uploaded(
                    bot, title, ep, language, sel.quality, channel_id
                )
            except Exception as ex:
                if isinstance(ex, (video_downloader.DownloadCancelled, TaskStopped)):
                    video_downloader.cleanup_file(fpath)
                    await bot.send_message(uid, f"🛑 Task `{progress.task_id}` cancelled.")
                    if control:
                        control.cancel_announced = True
                    _clear(uid)
                    return
                failed_count += 1
                await bot.send_message(uid, f"❌ {episode_label} {sel.quality} upload failed: {ex}")
            finally:
                video_downloader.cleanup_file(fpath)
                db.release_episode_claim(
                    title, ep.season, ep.episode, language, sel.quality, channel_id
                )

    await bot.send_message(
        uid,
        f"🔔 Tracking active!\n"
        f"Initial upload: {uploaded_count} uploaded, {failed_count} failed, "
        f"{skipped_count} already uploaded.\n"
        f"Monitoring for new episodes of {title}.",
    )


async def cb_ongoing_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    st = _state(query.from_user.id)
    info = st.get("anime_info", {})
    title = info.get("title", "")
    language = st.get("selected_language", "hindi")
    await query.message.edit_text(f"📋 Episodes of {title} [{language}]:\n\nUse /listtracked to see track status.")


async def cb_ongoing_stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    st = _state(query.from_user.id)
    info = st.get("anime_info", {})
    title = info.get("title", "")
    language = st.get("selected_language", "hindi")
    db.remove_tracked(title, language)
    _clear(query.from_user.id)
    await query.message.edit_text(f"✅ Stopped tracking: {title} [{language}]", reply_markup=None)


async def cb_channel_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not _admin_authorized(query.from_user.id):
        await query.message.edit_text("Owner/admin only.")
        return
    action = query.data.split(":", 1)[1]
    uid = query.from_user.id
    if action == "set":
        _state(uid)["awaiting_channel"] = True
        await query.message.edit_text(
            "📺 Send a forwarded message from the target channel, "
            "or use /setchannel @channel."
        )
    elif action == "remove":
        db.set_config("channel_id", "")
        db.set_config("channel_title", "")
        await query.message.edit_text("✅ Upload channel removed.")
    elif action == "test":
        channel_id = db.get_config("channel_id")
        if not channel_id:
            await query.message.edit_text("❌ No upload channel is configured.")
            return
        try:
            await context.bot.send_message(channel_id, "✅ RareAnimes bot channel test.")
            await query.message.edit_text("✅ Test message sent to the configured channel.")
        except Exception as exc:
            await query.message.edit_text(f"❌ Channel test failed: {exc}")


async def cb_admin_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    from config import OWNER_ID
    if query.from_user.id != OWNER_ID:
        await query.message.edit_text("Owner only.")
        return
    parts = query.data.split(":")
    if parts[1] == "add":
        _state(query.from_user.id)["awaiting_admin"] = True
        await query.message.edit_text("Send the Telegram user ID to add as an admin.")
        return
    try:
        admin_id = int(parts[2])
    except (IndexError, ValueError):
        await query.message.edit_text("❌ Invalid admin ID.")
        return
    db.remove_admin(admin_id)
    await query.message.edit_text(f"✅ User {admin_id} removed as admin.")


async def cb_tracked_select(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    parts = query.data.split(":")
    if len(parts) < 4:
        await query.message.edit_text("❌ Invalid tracked anime.")
        return
    title, language = parts[2], parts[3]
    await query.message.edit_text(
        f"🎬 {title}\n🌐 {language}\n\n"
        "Use /untrack with the anime name to stop monitoring."
    )


async def cb_search_page_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await cb_search_page(update, context)


async def cb_set_quality(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not _admin_authorized(query.from_user.id):
        await query.message.edit_text("Owner/admin only.")
        return
    data = query.data
    quality = data.split(":")[1]
    db.set_config("default_quality", quality)
    await query.message.edit_text(f"✅ Default quality set to: {quality}")


async def cb_admin_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not _admin_authorized(query.from_user.id):
        await query.message.edit_text("Owner/admin only.")
        return
    ch = db.get_config("channel_id")
    if ch:
        await query.message.edit_text(
            f"📺 Channel: `{ch}`\nUse /setchannel to change.",
            parse_mode=ParseMode.MARKDOWN,
        )
    else:
        await query.message.edit_text("📺 No channel.\nUse /setchannel to configure.")


async def cb_admin_quality(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not _admin_authorized(query.from_user.id):
        await query.message.edit_text("Owner/admin only.")
        return
    cur = db.get_config("default_quality", DEFAULT_QUALITY)
    await query.message.edit_text(f"🎞️ Current: {cur}\nSelect:", reply_markup=quality_preference_keyboard())


async def cb_admin_management(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    from config import OWNER_ID
    if query.from_user.id != OWNER_ID:
        await query.message.edit_text("Owner only.")
        return
    admins = db.list_admins()
    await query.message.edit_text("👥 Admin management:\nAdd: /addadmin <id>\nRemove: buttons below", reply_markup=admin_management_keyboard(admins))


async def cb_admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not _admin_authorized(query.from_user.id):
        await query.message.edit_text("Owner/admin only.")
        return
    tracked = db.list_tracked()
    admins = db.list_admins()
    ch = db.get_config("channel_id", "Not set")
    q = db.get_config("default_quality", DEFAULT_QUALITY)
    text = f"📊 Stats:\n\nTracked: {len(tracked)}\nAdmins: {len(admins)+1}\nChannel: `{ch}`\nQuality: {q}"
    await query.message.edit_text(text, parse_mode=ParseMode.MARKDOWN)


async def cb_admin_main(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not _admin_authorized(query.from_user.id):
        await query.message.edit_text("Owner/admin only.")
        return
    ch = db.get_config("channel_id", "Not set")
    q = db.get_config("default_quality", DEFAULT_QUALITY)
    tracked = db.list_tracked()
    text = (
        f"⚙️ Admin Panel\n\n"
        f"📺 Channel: `{ch}`\n"
        f"🎞️ Quality: {q}\n"
        f"🔔 Tracked: {len(tracked)}\n\n"
        f"Select option:"
    )
    await query.message.edit_text(
        text,
        reply_markup=main_admin_keyboard(),
        parse_mode=ParseMode.MARKDOWN,
    )


async def cb_noop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()


async def cb_legacy_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Acknowledge callbacks kept for compatibility with older keyboards."""
    await update.callback_query.answer(
        "Please start a new /upload flow to choose an episode and quality.",
        show_alert=True,
    )


async def cb_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    cancelled = _cancel_tasks(query.from_user.id)
    _clear(query.from_user.id)
    try:
        message = (
            "✅ Cancellation requested. The bot will stop safely after the current transfer."
            if cancelled
            else "✅ Selection closed."
        )
        await query.message.edit_text(message, reply_markup=None)
    except Exception:
        pass


# ======================= Utility =======================

def _select_qualities(links):
    """Return each supported quality once, in ascending resolution order."""
    selected = []
    seen = set()
    for wanted in ("360P", "720P", "1080P"):
        for link in links:
            if db.normalize_quality_label(link.quality) == wanted and wanted not in seen:
                selected.append(link)
                seen.add(wanted)
                break
    return selected


async def _wait_for_target_quality(download_url, progress_message, episode_label, control=None):
    """Refresh the source until at least one supported video quality is published."""
    while True:
        if control:
            control.check()
        try:
            links, metadata = await asyncio.to_thread(
                scraper.get_all_download_urls, download_url
            )
        except Exception:
            logger.exception("Could not refresh download qualities for %s", episode_label)
            links, metadata = [], {}

        if links and not isinstance(links, dict) and _select_qualities(links):
            return links, metadata

        await _edit_progress_message(
            progress_message,
            f"⏳ No supported quality is posted for {episode_label} yet.\n"
            f"Refreshing the download page every {QUALITY_POLL_SECONDS} seconds. "
            "Use /cancel to stop.",
        )
        for _ in range(QUALITY_POLL_SECONDS):
            if control:
                control.check()
            await asyncio.sleep(1)


def _quality_resolution(label):
    match = re.search(r"(?<!\d)(360|480|720|1080)\s*p?", str(label), re.IGNORECASE)
    return match.group(1) if match else None


async def _download_quality_until_ready(
    download_url, selected_link, output_path, progress, episode_label, control=None
):
    """Retry a selected quality after refreshing its link until its file is ready."""
    current_link = selected_link
    wanted_resolution = _quality_resolution(selected_link.quality)
    while True:
        if control:
            control.check()

        if current_link is None:
            try:
                links, _ = await asyncio.to_thread(
                    scraper.get_all_download_urls, download_url
                )
            except Exception:
                logger.exception(
                    "Could not refresh %s quality for %s",
                    wanted_resolution or selected_link.quality,
                    episode_label,
                )
                links = []
            current_link = next(
                (
                    link for link in links
                    if not isinstance(links, dict)
                    and _quality_resolution(link.quality) == wanted_resolution
                ),
                None,
            ) if links else None

        if current_link is not None:
            try:
                downloaded = await asyncio.to_thread(
                    video_downloader.download_video,
                    current_link.url,
                    output_path,
                    lambda percent, transferred, total_bytes: progress.report_percent(
                        "download", percent, transferred, total_bytes
                    ),
                    cancel_check=control.is_cancelled if control else None,
                )
            except (video_downloader.DownloadCancelled, TaskStopped):
                raise
            except Exception:
                logger.exception(
                    "Download is not ready for %s %s",
                    episode_label, selected_link.quality,
                )
                downloaded = None
            if downloaded:
                return downloaded
            video_downloader.cleanup_file(output_path)
            current_link = None

        await _edit_progress_message(
            progress.message,
            f"⏳ {selected_link.quality} for {episode_label} is not ready yet.\n"
            f"Refreshing the download page every {QUALITY_POLL_SECONDS} seconds. "
            "Use /cancel to stop.",
        )
        for _ in range(QUALITY_POLL_SECONDS):
            if control:
                control.check()
            await asyncio.sleep(1)
