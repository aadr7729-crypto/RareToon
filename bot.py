#This Bot Is Created By Shivam, Thanks To Shivam For Providing Repo
import os
import logging

from telegram import BotCommand, Update
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, MessageHandler,
    TypeHandler, filters,
)

import database as db
from config import BOT_TOKEN, OWNER_ID
from handlers import (
    cmd_start, cmd_help, cmd_upload, cmd_track, cmd_untrack,
    cmd_listtracked, cmd_channel, cmd_setchannel, cmd_quality,
    cmd_admins, cmd_addadmin, cmd_removeadmin, cmd_stats,
    cmd_addpremium, cmd_rempremium, cmd_listpremium, cb_premium_duration,
    cmd_cancel, handle_message,
    premium_access_gate,
    cb_anime_select, cb_status_select, cb_language_select,
    cb_ongoing_confirm, cb_ongoing_list, cb_ongoing_stop, cb_upload_back,
    cb_search_page, cb_set_quality,
    cb_admin_channel, cb_admin_quality, cb_admin_management,
    cb_admin_stats, cb_admin_main, cb_noop, cb_cancel,
    cb_episode_toggle, cb_episode_page, cb_episode_download,
    cb_episode_download_all,
    cb_channel_action, cb_admin_action, cb_tracked_select,
    cb_legacy_action,
)
from scheduler import periodic_check_job

from handlers import (
    cmd_remmetadata, cmd_remthumb, cmd_setmetadata, cmd_setthumb,
)
from autoupload_handlers import (
    cmd_autoupload, cmd_authupload, cb_autoupload,
    cb_authupload_cancel_task, handle_autoupload_photo,
)
from autoupload_service import periodic_autoupload_job


logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
    handlers=[
        logging.FileHandler("bot.log"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)


def register_handlers(app: Application):
    app.add_handler(TypeHandler(Update, premium_access_gate), group=-1)
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("upload", cmd_upload))
    app.add_handler(CommandHandler("track", cmd_track))
    app.add_handler(CommandHandler("untrack", cmd_untrack))
    app.add_handler(CommandHandler("listtracked", cmd_listtracked))
    app.add_handler(CommandHandler("channel", cmd_channel))
    app.add_handler(CommandHandler("setchannel", cmd_setchannel))
    app.add_handler(CommandHandler("quality", cmd_quality))
    app.add_handler(CommandHandler("admins", cmd_admins))
    app.add_handler(CommandHandler("addadmin", cmd_addadmin))
    app.add_handler(CommandHandler("removeadmin", cmd_removeadmin))
    app.add_handler(CommandHandler("addpremium", cmd_addpremium))
    app.add_handler(CommandHandler("rempremium", cmd_rempremium))
    app.add_handler(CommandHandler("listpremium", cmd_listpremium))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CommandHandler("autoupload", cmd_autoupload))
    app.add_handler(CommandHandler("authupload", cmd_authupload))
    app.add_handler(CommandHandler("cancel", cmd_cancel))
    app.add_handler(CommandHandler("admin", cmd_stats))
    app.add_handler(CommandHandler("setmetadata", cmd_setmetadata))
    app.add_handler(CommandHandler("remmetadata", cmd_remmetadata))
    app.add_handler(CommandHandler("setthumb", cmd_setthumb))
    app.add_handler(CommandHandler(
        ["remthumb", "remthumbnail", "remthumnail"], cmd_remthumb
    ))

    app.add_handler(MessageHandler(filters.FORWARDED & filters.ChatType.PRIVATE, handle_message))
    app.add_handler(MessageHandler(
        filters.PHOTO & filters.ChatType.PRIVATE, handle_autoupload_photo
    ))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    app.add_handler(CallbackQueryHandler(cb_anime_select, pattern=r"^anime_select:"))
    app.add_handler(CallbackQueryHandler(cb_search_page, pattern=r"^search_page:"))
    app.add_handler(CallbackQueryHandler(cb_status_select, pattern=r"^status:"))
    app.add_handler(CallbackQueryHandler(cb_language_select, pattern=r"^lang:"))
    app.add_handler(CallbackQueryHandler(
        cb_ongoing_confirm,
        pattern=r"^ongoing:(?:backfill|future|selected|all|toggle:\d+|page:\d+|back_status)$",
    ))
    app.add_handler(CallbackQueryHandler(
        cb_upload_back, pattern=r"^upload:back_"
    ))
    app.add_handler(CallbackQueryHandler(cb_ongoing_list, pattern=r"^ongoing:list"))
    app.add_handler(CallbackQueryHandler(cb_ongoing_stop, pattern=r"^ongoing:stop"))
    app.add_handler(CallbackQueryHandler(cb_episode_toggle, pattern=r"^ep:toggle:"))
    app.add_handler(CallbackQueryHandler(cb_episode_page, pattern=r"^ep:page:"))
    app.add_handler(CallbackQueryHandler(cb_episode_download, pattern=r"^ep:download$"))
    app.add_handler(CallbackQueryHandler(cb_episode_download_all, pattern=r"^ep:download_all$"))
    app.add_handler(CallbackQueryHandler(cb_legacy_action, pattern=r"^(quality|ep_dl):"))
    app.add_handler(CallbackQueryHandler(cb_set_quality, pattern=r"^setquality:"))
    app.add_handler(CallbackQueryHandler(cb_channel_action, pattern=r"^channel:"))
    app.add_handler(CallbackQueryHandler(cb_admin_channel, pattern=r"^admin:channel"))
    app.add_handler(CallbackQueryHandler(cb_admin_quality, pattern=r"^admin:quality"))
    app.add_handler(CallbackQueryHandler(cb_admin_management, pattern=r"^admin:management"))
    app.add_handler(CallbackQueryHandler(cb_admin_stats, pattern=r"^admin:stats"))
    app.add_handler(CallbackQueryHandler(cb_admin_main, pattern=r"^admin:main"))
    app.add_handler(CallbackQueryHandler(cb_admin_action, pattern=r"^admin:(add|remove)(:|$)"))
    app.add_handler(CallbackQueryHandler(cb_premium_duration, pattern=r"^premium:duration:"))
    app.add_handler(CallbackQueryHandler(cb_tracked_select, pattern=r"^tracked:select:"))
    app.add_handler(CallbackQueryHandler(cb_noop, pattern=r"^noop"))
    app.add_handler(CallbackQueryHandler(cb_cancel, pattern=r"^cancel$"))
    app.add_handler(CallbackQueryHandler(
        cb_authupload_cancel_task, pattern=r"^autoupload:stop:"
    ))
    app.add_handler(CallbackQueryHandler(cb_autoupload, pattern=r"^autoupload:"))


async def configure_bot_menu(app: Application):
    """Expose every supported command in Telegram's bot menu."""
    await app.bot.set_my_commands([
        BotCommand("start", "Start the bot"),
        BotCommand("help", "Show help"),
        BotCommand("upload", "Search and download anime"),
        BotCommand("track", "Track an ongoing anime"),
        BotCommand("untrack", "Stop tracking an anime"),
        BotCommand("listtracked", "List tracked anime"),
        BotCommand("channel", "Configure upload channel"),
        BotCommand("setchannel", "Set an upload channel"),
        BotCommand("quality", "Set default quality"),
        BotCommand("setmetadata", "Set your video filename format"),
        BotCommand("remmetadata", "Remove your filename format"),
        BotCommand("setthumb", "Set your video thumbnail by replying to a photo"),
        BotCommand("remthumb", "Remove your saved video thumbnail"),
        BotCommand("admins", "Manage bot admins"),
        BotCommand("addpremium", "Grant premium (admin only)"),
        BotCommand("rempremium", "Remove premium (admin only)"),
        BotCommand("listpremium", "List premium users (admin only)"),
        BotCommand("stats", "Show bot statistics"),
        BotCommand("autoupload", "Configure ongoing anime auto-upload (admin only)"),
        BotCommand("authupload", "Configure main-channel episode posts (admin only)"),
        BotCommand("admin", "Open admin panel"),
        BotCommand("cancel", "Cancel active task"),
    ])


def main():
    db.init_db()

    token = BOT_TOKEN
    if not token:
        logger.error(
            "BOT_TOKEN is missing. Add it as a Replit Secret before starting the bot."
        )
        raise SystemExit(1)

    if OWNER_ID == 0:
        logger.error(
            "OWNER_ID is missing or invalid. Set it to the owner's numeric Telegram ID."
        )
        raise SystemExit(1)

    app = Application.builder().token(token).post_init(configure_bot_menu).build()
    register_handlers(app)

    app.job_queue.run_repeating(
        periodic_check_job,
        interval=1800,
        first=1800,
        job_kwargs={"max_instances": 1, "coalesce": True},
    )
    app.job_queue.run_repeating(
        periodic_autoupload_job,
        interval=1800,
        first=1800,
        job_kwargs={"max_instances": 1, "coalesce": True},
    )

    logger.info("Bot started")
    app.run_polling()


if __name__ == "__main__":
    main()
