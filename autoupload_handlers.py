"""Owner/admin controls for the owner-selected /autoupload workflow."""

import io
import json
import string

from telegram import InlineKeyboardMarkup, InputFile, Update
from telegram.ext import ContextTypes

import autoupload_service as service
import database as db
from colored_buttons import Button

_pending_inputs = {}
_SEARCH_PAGE_SIZE = 8
_EPISODE_PAGE_SIZE = 20
_TEMPLATE_FIELDS = {
    "anime", "episode", "episode_number", "season", "language", "quality",
}
_ANNOUNCEMENT_TEMPLATE_FIELDS = _TEMPLATE_FIELDS | {
    "link", "episode_count", "qualities", "genres",
}


def _is_admin(user_id):
    return db.is_owner(user_id)


def _settings_keyboard():
    channel_count = len(db.list_autoupload_channels())
    summary = (
        f"Tracked channels: {channel_count}\n"
        f"Language: Hindi Dub only\n"
        "Fixed qualities: 360P, 720P, and 1080P\n"
        "New episodes are checked every 30 minutes."
    )
    rows = [
        [Button("➕ Set Ongoing Anime Channel", callback_data="autoupload:set_channel")],
        [Button("➖ Remove Ongoing Anime Channel", callback_data="autoupload:remove_channel")],
        [Button("📋 Ongoing Anime Channels", callback_data="autoupload:list_channels")],
        [Button("🖼️ Set Channel Thumbnail", callback_data="autoupload:thumbnail_list")],
        [Button("🗄️ Set Thumbnail Archive Channel", callback_data="autoupload:set_database")],
        [Button("🗑️ Remove Thumbnail Archive Channel", callback_data="autoupload:remove_database")],
        [Button("📄 Set Filename Metadata", callback_data="autoupload:set_metadata")],
        [Button("💬 Set Episode Caption", callback_data="autoupload:set_caption")],
        [Button("♻️ Reset Filename Metadata", callback_data="autoupload:remove_metadata")],
        [Button("♻️ Reset Episode Caption", callback_data="autoupload:remove_caption")],
        [Button("📣 Set Main Announcement Channel", callback_data="autoupload:set_main")],
        [Button("🗑️ Remove Main Announcement Channel", callback_data="autoupload:remove_main")],
        [Button("📢 Main Channel Post Settings", callback_data="autoupload:auth_menu")],
        [Button("🔄 Check Tracked Channels Now", callback_data="autoupload:run")],
        [Button("❌ Close", callback_data="autoupload:close")],
    ]
    return InlineKeyboardMarkup(rows), summary


async def cmd_autoupload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not _is_admin(user_id):
        await update.effective_message.reply_text("Owner/admin only.")
        return
    keyboard, summary = _settings_keyboard()
    await update.effective_message.reply_text(
        "⚙️ Auto-upload settings\n\n"
        f"{summary}\n\n"
        "Choose an existing channel the bot can post to. You will search for "
        "the anime yourself; no channels are created automatically.",
        reply_markup=keyboard,
    )


def _authupload_keyboard():
    return InlineKeyboardMarkup([
        [Button("📣 Set Main Channel", callback_data="autoupload:set_main")],
        [Button("💬 Set Main Post Caption", callback_data="autoupload:set_announcement_caption")],
        [Button("♻️ Default Caption + Set Images", callback_data="autoupload:reset_announcement_caption")],
        [Button("🖼️ Set/Replace Anime Post Image", callback_data="autoupload:main_image_list")],
        [Button("🗑️ Remove Main Channel", callback_data="autoupload:remove_main")],
        [Button("⬅️ Back to Auto-upload", callback_data="autoupload:menu")],
    ])


async def cmd_authupload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update.effective_user.id):
        await update.effective_message.reply_text("Owner/admin only.")
        return
    await update.effective_message.reply_text(
        "📢 Main-channel episode post settings\n\n"
        "Each anime has a separate saved main-post image, independent of its "
        "video thumbnail. The default caption asks for missing images once and "
        "reuses saved images for future episode posts.",
        reply_markup=_authupload_keyboard(),
    )


async def _prompt_main_post_image(
    message, user_id, *, missing_only=False, default_caption_saved=False
):
    channels = db.list_autoupload_channels()
    if missing_only:
        channels = [
            channel for channel in channels
            if not channel.get("main_post_image_file_id")
            and not channel.get("main_post_image")
        ]
    if not channels:
        if missing_only and db.list_autoupload_channels():
            text = (
                "The default caption is saved, and every tracked anime already "
                "has a separate main-post image. No image is needed again."
            )
        elif missing_only:
            text = (
                "The default caption is saved. Set up an anime with /autoupload "
                "first, then use Set/Replace Anime Post Image."
            )
        else:
            text = "Set up an anime with /autoupload before adding its post image."
        await message.reply_text(text, reply_markup=_authupload_keyboard())
        return

    if len(channels) == 1:
        channel = channels[0]
        _pending_inputs[user_id] = {
            "kind": "main_post_image",
            "channel_id": str(channel["channel_id"]),
            "anime_name": channel["anime_name"],
            "setup_missing": missing_only,
        }
        prefix = "Default caption saved. " if default_caption_saved else ""
        await message.reply_text(
            f"{prefix}Send the main-channel poster image for {channel['anime_name']}. "
            "This is separate from its video thumbnail and is saved for future "
            "episode posts; it will not be requested again.",
            reply_markup=InlineKeyboardMarkup([[
                Button("Cancel", callback_data="autoupload:main_image_cancel")
            ]]),
        )
        return

    prefix = (
        "Default caption saved. Choose an anime that still needs a main-post image:"
        if missing_only else "Choose the anime whose main-channel post image to set or replace:"
    )
    action = "main_image_missing" if missing_only else "main_image"
    await message.reply_text(
        prefix,
        reply_markup=InlineKeyboardMarkup([
            [Button(
                channel["anime_name"],
                callback_data=f"autoupload:{action}:{channel['channel_id']}",
            )]
            for channel in channels
        ] + [[Button("Cancel", callback_data="autoupload:main_image_cancel")]]),
    )


async def cb_authupload_cancel_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await service.cancel_owner_task(update, context)


def _search_keyboard(results, page):
    start = page * _SEARCH_PAGE_SIZE
    rows = [
        [Button(
            str(item.get("title") or "Anime")[:60],
            callback_data=f"autoupload:search:{index}",
        )]
        for index, item in enumerate(results[start:start + _SEARCH_PAGE_SIZE], start)
    ]
    navigation = []
    if page > 0:
        navigation.append(Button("⬅️ Previous", callback_data=f"autoupload:search_page:{page - 1}"))
    if start + _SEARCH_PAGE_SIZE < len(results):
        navigation.append(Button("Next ➡️", callback_data=f"autoupload:search_page:{page + 1}"))
    if navigation:
        rows.append(navigation)
    rows.append([Button("⬅️ Back to channel setup", callback_data="autoupload:back_channel")])
    rows.append([Button("Cancel", callback_data="autoupload:cancel")])
    return InlineKeyboardMarkup(rows)


async def _show_search_results(message, user_id, page=0, edit=False):
    pending = _pending_inputs.get(user_id, {})
    results = pending.get("results") or []
    if not results:
        pending["kind"] = "anime_query"
        text = "No matching anime were found. Send another title to search again."
        if edit:
            await message.edit_text(text)
        else:
            await message.reply_text(text)
        return
    page_count = max(1, (len(results) + _SEARCH_PAGE_SIZE - 1) // _SEARCH_PAGE_SIZE)
    page = max(0, min(page, page_count - 1))
    pending["page"] = page
    text = (
        f"Search results for “{pending.get('query', '')}” "
        f"({len(results)} found). Choose the matching anime:"
    )
    markup = _search_keyboard(results, page)
    if edit:
        await message.edit_text(text, reply_markup=markup)
    else:
        await message.reply_text(text, reply_markup=markup)


def _episode_keyboard(pending, page):
    episodes = pending["catalog"]["episodes"]
    page_count = max(1, (len(episodes) + _EPISODE_PAGE_SIZE - 1) // _EPISODE_PAGE_SIZE)
    page = max(0, min(page, page_count - 1))
    pending["episode_page"] = page
    selected = pending.setdefault("selected", set())
    start = page * _EPISODE_PAGE_SIZE
    rows = []
    for index, episode in enumerate(
        episodes[start:start + _EPISODE_PAGE_SIZE], start
    ):
        marker = "✅" if index in selected else "▫️"
        label = (
            f"{marker} S{int(episode['season']):02d}E{int(episode['episode']):02d}"
            " • Hindi Dub"
        )
        rows.append([Button(label, callback_data=f"autoupload:episode:{index}")])
    navigation = []
    if page > 0:
        navigation.append(Button("⬅️ Previous", callback_data=f"autoupload:episode_page:{page - 1}"))
    if start + _EPISODE_PAGE_SIZE < len(episodes):
        navigation.append(Button("Next ➡️", callback_data=f"autoupload:episode_page:{page + 1}"))
    if navigation:
        rows.append(navigation)
    rows.append([
        Button("⬅️ Back to anime status", callback_data="autoupload:back_status"),
    ])
    rows.append([
        Button("⬇️ Download Selected", callback_data="autoupload:download_selected"),
    ])
    if pending.get("status") == "ongoing":
        rows.extend([
            [Button(
                "⬇️ Download Previous + Track New",
                callback_data="autoupload:backfill_track",
            )],
            [Button(
                "⏭ Skip Previous + Track New Only",
                callback_data="autoupload:skip_track",
            )],
        ])
    else:
        rows.append([
            Button("⬇️ Download All Episodes", callback_data="autoupload:download_all"),
        ])
    rows.append([Button("Cancel", callback_data="autoupload:cancel")])
    return InlineKeyboardMarkup(rows)


def _episode_selection_text(pending):
    catalog = pending["catalog"]
    episodes = catalog["episodes"]
    status_label = "Ongoing — future episodes will be tracked" if pending["status"] == "ongoing" else "Complete — no future tracking"
    selected_count = len(pending.get("selected", set()))
    if episodes:
        page_count = max(1, (len(episodes) + _EPISODE_PAGE_SIZE - 1) // _EPISODE_PAGE_SIZE)
        page = pending.get("episode_page", 0) + 1
        list_summary = (
            f"Available Hindi Dub episodes: {len(episodes)}\n"
            f"Page {page}/{page_count} • Selected: {selected_count}\n"
            "Tap an episode to select or remove it."
        )
    else:
        list_summary = (
            "No Hindi-dubbed episodes were found on the anime page. "
            "Check another search result or try again later."
        )
    return (
        f"🎬 {catalog['anime_name']}\n"
        f"Status: {status_label}\n"
        f"Website status: {catalog['site_info'].get('status', 'unknown')}\n"
        f"Source: {catalog['source_url']}\n\n"
        f"{list_summary}"
    )


async def _show_episode_selection(message, user_id, page=None, edit=False):
    pending = _pending_inputs[user_id]
    if pending["kind"] != "episode_selection":
        return
    if page is None:
        page = pending.get("episode_page", 0)
    markup = _episode_keyboard(pending, page)
    text = _episode_selection_text(pending)
    if edit:
        await message.edit_text(text, reply_markup=markup, disable_web_page_preview=True)
    else:
        await message.reply_text(text, reply_markup=markup, disable_web_page_preview=True)


async def _show_anime_status(message, user_id):
    pending = _pending_inputs.get(user_id, {})
    catalog = pending.get("catalog")
    if not catalog:
        await message.reply_text("That anime selection has expired. Start /autoupload again.")
        return
    await message.reply_text(
        f"🎬 {catalog['anime_name']}\n"
        f"Site status: {catalog['site_info'].get('status', 'unknown')}\n"
        f"Categories: {', '.join(catalog['site_info'].get('categories') or []) or 'not listed'}\n"
        f"Available Hindi Dub episodes: {len(catalog['episodes'])}\n"
        f"Source: {catalog['source_url']}\n\n"
        "Choose whether to track this anime as ongoing or completed.",
        reply_markup=InlineKeyboardMarkup([
            [Button("🔔 Ongoing — track new episodes", callback_data="autoupload:anime_status:ongoing")],
            [Button("✅ Complete — no future tracking", callback_data="autoupload:anime_status:completed")],
            [Button("⬅️ Back to search results", callback_data="autoupload:back_results")],
            [Button("Cancel", callback_data="autoupload:cancel")],
        ]),
        disable_web_page_preview=True,
    )


async def _save_and_start(query, context, user_id, mode):
    pending = _pending_inputs.get(user_id)
    if not pending or pending.get("kind") != "episode_selection":
        await query.message.reply_text(
            "This anime selection has expired. Start /autoupload again."
        )
        return
    catalog = pending["catalog"]
    all_episodes = catalog["episodes"]
    status = pending["status"]
    selected_indices = pending.get("selected", set())

    if mode == "selected":
        episodes = [
            episode for index, episode in enumerate(all_episodes)
            if index in selected_indices
        ]
        if not episodes:
            await query.message.reply_text("Select at least one episode first.")
            return
    elif mode == "all":
        episodes = list(all_episodes)
    elif mode == "skip":
        episodes = []
    else:
        episodes = list(all_episodes)

    try:
        channel = await service.register_channel(
            context.bot, pending["channel_id"], catalog, status
        )
    except Exception as exc:
        await query.message.reply_text(f"❌ Could not save this channel: {exc}")
        return
    channel["episode_count"] = len(all_episodes)
    channel["episode_counts_by_season"] = service._episode_counts_by_season(all_episodes)

    all_keys = {
        (int(item["season"] or 0), int(item["episode"] or 0))
        for item in all_episodes
    }
    selected_keys = {
        (int(item["season"] or 0), int(item["episode"] or 0))
        for item in episodes
    }
    if mode == "skip":
        ignored = all_keys
    elif mode == "selected" and status == "ongoing":
        ignored = all_keys - selected_keys
    else:
        ignored = set()
    db.set_autoupload_ignored_episodes(channel["channel_id"], ignored)

    if not channel.get("thumbnail"):
        db.set_autoupload_setting(
            f"status:{channel['channel_id']}", "awaiting_thumbnail"
        )
        _pending_inputs[user_id] = {
            "kind": "thumbnail",
            "channel_id": str(channel["channel_id"]),
            "anime_name": channel["anime_name"],
            "episodes": episodes,
            "episode_count": len(all_episodes),
            "episode_counts_by_season": service._episode_counts_by_season(all_episodes),
            "mode": mode,
            "tracking_status": status,
            "start_after_save": True,
        }
        await query.message.reply_text(
            f"Send a thumbnail photo for {channel['anime_name']}. "
            "It will be stored in the database and used on its episode uploads. "
            "Or skip the thumbnail and start.",
            reply_markup=InlineKeyboardMarkup([[
                Button("Skip thumbnail and start", callback_data="autoupload:thumbnail_skip"),
            ]]),
        )
        return

    _pending_inputs.pop(user_id, None)
    await _start_upload_task(
        query.message, context, channel, episodes, mode
    )


async def _start_upload_task(message, context, channel, episodes, mode):
    if episodes:
        await message.reply_text(
            f"✅ Auto-upload is starting for {channel['anime_name']}. "
            "Download and upload progress will appear in the owner DM."
        )
        context.application.create_task(
            service.upload_selected_episodes(context.bot, channel, episodes)
        )
    elif mode == "skip":
        await message.reply_text(
            f"✅ Previous episodes were skipped. {channel['anime_name']} is now "
            "tracked for new Hindi Dub episodes; tracking updates go to the owner DM."
        )
        context.application.create_task(service.notify_owner(
            context.bot,
            f"Tracking • {channel['anime_name']}\n"
            "Earlier listed episodes were skipped. New Hindi Dub episodes will be uploaded.",
        ))
    else:
        await message.reply_text(
            f"No episodes were available to upload for {channel['anime_name']}."
        )


async def cb_autoupload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    if not _is_admin(user_id):
        await query.answer("Owner/admin only.", show_alert=True)
        return
    await query.answer()
    action = query.data.removeprefix("autoupload:")

    if action == "close":
        await query.message.edit_text("Auto-upload settings closed.")
        return
    if action == "menu":
        keyboard, summary = _settings_keyboard()
        await query.message.edit_text(f"⚙️ Auto-upload settings\n\n{summary}", reply_markup=keyboard)
        return
    if action == "auth_menu":
        await query.message.edit_text(
            "📢 Main-channel episode post settings",
            reply_markup=_authupload_keyboard(),
        )
        return
    if action == "back_auth_menu":
        _pending_inputs.pop(user_id, None)
        await query.message.edit_text(
            "📢 Main-channel episode post settings",
            reply_markup=_authupload_keyboard(),
        )
        return
    if action == "back_channel":
        pending = _pending_inputs.get(user_id, {})
        channel_id = pending.get("channel_id")
        _pending_inputs[user_id] = {"kind": "ongoing_channel"}
        prompt = "Send the numeric ID of an existing Telegram channel. The bot must already be an administrator there."
        if channel_id:
            prompt += f"\nPrevious channel ID: {channel_id}"
        await query.message.reply_text(
            prompt,
            reply_markup=InlineKeyboardMarkup([[
                Button("⬅️ Back to /autoupload", callback_data="autoupload:menu")
            ]]),
        )
        return
    if action == "cancel":
        _pending_inputs.pop(user_id, None)
        await query.message.edit_text("Auto-upload setup cancelled.")
        return

    if action == "set_channel":
        _pending_inputs[user_id] = {"kind": "ongoing_channel"}
        await query.message.reply_text(
            "Send the numeric ID of an existing Telegram channel. The bot must "
            "already be an administrator there.",
            reply_markup=InlineKeyboardMarkup([[
                Button("⬅️ Back to /autoupload", callback_data="autoupload:menu")
            ]]),
        )
        return

    if action in ("set_database", "set_main"):
        _pending_inputs[user_id] = {
            "kind": "database_channel" if action == "set_database" else "main_channel"
        }
        await query.message.reply_text(
            "Send the numeric channel ID. The bot must be an administrator there.",
            reply_markup=InlineKeyboardMarkup([[
                Button(
                    "⬅️ Back",
                    callback_data=(
                        "autoupload:back_auth_menu"
                        if action == "set_main" else "autoupload:menu"
                    ),
                )
            ]]),
        )
        return
    if action == "remove_database":
        db.set_autoupload_setting("database_channel_id", "")
        await query.message.reply_text("The thumbnail archive channel was removed.")
        return
    if action == "remove_main":
        db.set_autoupload_setting("main_channel_id", "")
        await query.message.reply_text("The main announcement channel was removed.")
        return
    if action == "set_announcement_caption":
        _pending_inputs[user_id] = {"kind": "announcement_caption"}
        await query.message.reply_text(
            "Send a main-channel post caption template (up to 1,000 characters). "
            "Placeholders: {anime}, {episode}, {episode_number}, {season}, "
            "{episode_count}, {qualities}, {genres}, {language}, {quality}, {link}.",
            reply_markup=InlineKeyboardMarkup([[
                Button("⬅️ Back", callback_data="autoupload:back_auth_menu")
            ]]),
        )
        return
    if action == "reset_announcement_caption":
        db.set_autoupload_setting(
            "announcement_caption_template", service.DEFAULT_ANNOUNCEMENT_CAPTION
        )
        await _prompt_main_post_image(
            query.message,
            user_id,
            missing_only=True,
            default_caption_saved=True,
        )
        return
    if action == "main_image_list":
        await _prompt_main_post_image(query.message, user_id)
        return
    if action == "main_image_cancel":
        pending = _pending_inputs.get(user_id, {})
        if pending.get("kind") == "main_post_image":
            _pending_inputs.pop(user_id, None)
        await query.message.reply_text(
            "Main-channel image setup cancelled.",
            reply_markup=_authupload_keyboard(),
        )
        return
    if action.startswith(("main_image:", "main_image_missing:")):
        missing_only = action.startswith("main_image_missing:")
        channel_id = action.split(":", 1)[1]
        channel = db.get_autoupload_channel(channel_id)
        if not channel:
            await query.message.reply_text("That anime channel is no longer configured.")
            return
        if missing_only and (
            channel.get("main_post_image_file_id") or channel.get("main_post_image")
        ):
            await query.message.reply_text(
                f"A main-post image is already saved for {channel['anime_name']}."
            )
            return
        _pending_inputs[user_id] = {
            "kind": "main_post_image",
            "channel_id": str(channel_id),
            "anime_name": channel["anime_name"],
            "setup_missing": missing_only,
        }
        await query.message.reply_text(
            f"Send the main-channel poster image for {channel['anime_name']}. "
            "It is stored separately from the video thumbnail and reused for "
            "future episode posts.",
            reply_markup=InlineKeyboardMarkup([[
                Button("Cancel", callback_data="autoupload:main_image_cancel")
            ]]),
        )
        return
    if action == "back_results":
        pending = _pending_inputs.get(user_id)
        if not pending or not pending.get("results"):
            await query.message.reply_text(
                "Those search results have expired. Start /autoupload again."
            )
            return
        pending["kind"] = "anime_results"
        await _show_search_results(query.message, user_id)
        return
    if action == "back_status":
        pending = _pending_inputs.get(user_id)
        if not pending or pending.get("kind") != "episode_selection":
            await query.message.reply_text(
                "That anime selection has expired. Start /autoupload again."
            )
            return
        pending["kind"] = "anime_status"
        await _show_anime_status(query.message, user_id)
        return

    if action == "remove_channel":
        channels = db.list_autoupload_channels()
        if not channels:
            await query.message.reply_text("No auto-upload channels are configured.")
            return
        await query.message.reply_text(
            "Choose a channel to remove from tracking. This does not delete its "
            "Telegram channel or messages.",
            reply_markup=InlineKeyboardMarkup([
                [Button(
                    f"{row['anime_name']} [Hindi Dub]",
                    callback_data=f"autoupload:remove:{row['channel_id']}",
                )]
                for row in channels
            ]),
        )
        return
    if action.startswith("remove:"):
        channel_id = action.split(":", 1)[1]
        removed = db.remove_autoupload_channel(channel_id)
        await query.message.reply_text(
            "✅ Removed from auto-upload." if removed else "That channel was not configured."
        )
        return
    if action == "list_channels":
        channels = db.list_autoupload_channels()
        if not channels:
            await query.message.reply_text("No auto-upload channels are configured.")
            return
        lines = [
            f"• {row['anime_name']} — {row.get('channel_title') or row['channel_id']} "
            f"({row['channel_id']})"
            for row in channels
        ]
        await query.message.reply_text("\n".join(lines))
        return

    if action == "thumbnail_list":
        channels = db.list_autoupload_channels()
        if not channels:
            await query.message.reply_text("Set up an auto-upload channel first.")
            return
        await query.message.reply_text(
            "Choose the channel whose stored thumbnail you want to replace:",
            reply_markup=InlineKeyboardMarkup([
                [Button(
                    row["anime_name"],
                    callback_data=f"autoupload:thumbnail:{row['channel_id']}",
                )]
                for row in channels
            ]),
        )
        return
    if action.startswith("thumbnail:"):
        channel_id = action.split(":", 1)[1]
        channel = db.get_autoupload_channel(channel_id)
        if not channel:
            await query.message.reply_text("That channel is no longer configured.")
            return
        _pending_inputs[user_id] = {
            "kind": "thumbnail",
            "channel_id": str(channel_id),
            "anime_name": channel["anime_name"],
            "episodes": [],
            "start_after_save": False,
        }
        await query.message.reply_text(
            f"Send a photo to replace the stored thumbnail for {channel['anime_name']}."
        )
        return
    if action == "thumbnail_skip":
        pending = _pending_inputs.get(user_id)
        if not pending or pending.get("kind") != "thumbnail":
            await query.message.reply_text("There is no thumbnail step to skip.")
            return
        channel = db.get_autoupload_channel(pending["channel_id"])
        if not channel:
            _pending_inputs.pop(user_id, None)
            await query.message.reply_text("That channel is no longer configured.")
            return
        channel["episode_count"] = pending.get("episode_count")
        channel["episode_counts_by_season"] = pending.get(
            "episode_counts_by_season", {}
        )
        if pending.get("tracking_status"):
            db.set_autoupload_setting(
                f"status:{channel['channel_id']}", pending["tracking_status"]
            )
        _pending_inputs.pop(user_id, None)
        await _start_upload_task(
            query.message,
            context,
            channel,
            pending.get("episodes") or [],
            pending.get("mode", "selected"),
        )
        return

    if action == "set_caption":
        _pending_inputs[user_id] = {"kind": "caption"}
        await query.message.reply_text(
            "Send the episode caption template (1–900 characters). Placeholders: "
            "{anime}, {episode}, {episode_number}, {season}, {language}, {quality}."
        )
        return
    if action == "set_metadata":
        _pending_inputs[user_id] = {"kind": "metadata"}
        await query.message.reply_text(
            "Send the video filename template (1–200 characters). Placeholders: "
            "{anime}, {season}, {episode}, {episode_number}, {language}, {quality}.\n"
            "Example: {anime}_S{season}_EP{episode}_{quality}"
        )
        return
    if action == "remove_caption":
        db.set_autoupload_setting("caption_template", service.DEFAULT_AUTUPLOAD_CAPTION)
        await query.message.reply_text("The default auto-upload episode caption is restored.")
        return
    if action == "remove_metadata":
        db.set_autoupload_setting("filename_template", service.DEFAULT_AUTUPLOAD_FILENAME)
        await query.message.reply_text("The default auto-upload filename metadata is restored.")
        return
    if action.startswith("setquality:"):
        await query.message.reply_text(
            "Auto-upload always uses 360P, 720P, and 1080P. This setting cannot be changed."
        )
        return
    if action == "run":
        await query.message.reply_text(
            "Checking all owner-configured ongoing-anime channels now. "
            "Progress will appear in the owner DM."
        )
        context.application.create_task(service.run_autoupload_cycle(context.bot))
        return

    if action.startswith("search_page:"):
        try:
            page = int(action.split(":", 1)[1])
        except ValueError:
            page = 0
        await _show_search_results(query.message, user_id, page, edit=True)
        return
    if action.startswith("search:"):
        pending = _pending_inputs.get(user_id)
        try:
            index = int(action.split(":", 1)[1])
            result = pending["results"][index]
        except (KeyError, IndexError, TypeError, ValueError):
            await query.message.reply_text("That search choice has expired. Search again.")
            return
        await query.message.reply_text("Loading anime details and Hindi Dub episode list…")
        try:
            catalog = await service.load_anime_catalog(result)
        except Exception as exc:
            await query.message.reply_text(f"❌ Could not load this anime: {exc}")
            return
        _pending_inputs[user_id] = {
            "kind": "anime_status",
            "channel_id": pending["channel_id"],
            "catalog": catalog,
            "results": pending.get("results"),
            "query": pending.get("query"),
            "page": pending.get("page", 0),
        }
        await _show_anime_status(query.message, user_id)
        return
    if action.startswith("anime_status:"):
        pending = _pending_inputs.get(user_id)
        status = action.split(":", 1)[1]
        if not pending or pending.get("kind") != "anime_status" or status not in {"ongoing", "completed"}:
            await query.message.reply_text("That anime selection has expired. Start /autoupload again.")
            return
        pending.update(kind="episode_selection", status=status, selected=set(), episode_page=0)
        await _show_episode_selection(query.message, user_id)
        return
    if action.startswith("episode_page:"):
        try:
            page = int(action.split(":", 1)[1])
        except ValueError:
            page = 0
        await _show_episode_selection(query.message, user_id, page, edit=True)
        return
    if action.startswith("episode:"):
        pending = _pending_inputs.get(user_id)
        try:
            index = int(action.split(":", 1)[1])
            if pending["kind"] != "episode_selection":
                raise KeyError
            if not 0 <= index < len(pending["catalog"]["episodes"]):
                raise IndexError
        except (KeyError, IndexError, TypeError, ValueError):
            await query.message.reply_text("That episode selection has expired.")
            return
        selected = pending.setdefault("selected", set())
        if index in selected:
            selected.remove(index)
        else:
            selected.add(index)
        await _show_episode_selection(
            query.message, user_id, pending.get("episode_page", 0), edit=True
        )
        return
    if action in {"download_selected", "download_all", "backfill_track", "skip_track"}:
        mode = {
            "download_selected": "selected",
            "download_all": "all",
            "backfill_track": "all",
            "skip_track": "skip",
        }[action]
        await _save_and_start(query, context, user_id, mode)
        return


def _validated_template(text, max_length, allowed_fields=None):
    if not text or len(text) > max_length:
        return False
    try:
        placeholders = {
            field_name
            for _, field_name, _, _ in string.Formatter().parse(text)
            if field_name
        }
    except ValueError:
        return False
    return placeholders.issubset(allowed_fields or _TEMPLATE_FIELDS)


async def handle_pending_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle text replies belonging to an owner/admin auto-upload prompt."""
    user_id = update.effective_user.id
    pending = _pending_inputs.get(user_id)
    if not pending:
        return False
    if not _is_admin(user_id):
        _pending_inputs.pop(user_id, None)
        await update.effective_message.reply_text("Owner/admin only.")
        return True

    text = (update.effective_message.text or "").strip()
    kind = pending["kind"]
    if kind in {"caption", "metadata", "announcement_caption"}:
        maximum = 1000 if kind == "announcement_caption" else (
            900 if kind == "caption" else 200
        )
        allowed_fields = (
            _ANNOUNCEMENT_TEMPLATE_FIELDS
            if kind == "announcement_caption" else _TEMPLATE_FIELDS
        )
        if not _validated_template(text, maximum, allowed_fields):
            await update.effective_message.reply_text(
                "That template is invalid. Use supported placeholders such as "
                "{anime}, {episode}, {episode_number}, {season}, {episode_count}, "
                "{qualities}, {genres}, {language}, {quality}, and {link}; check "
                "that all braces match."
            )
            return True
        setting = {
            "caption": "caption_template",
            "metadata": "filename_template",
            "announcement_caption": "announcement_caption_template",
        }[kind]
        db.set_autoupload_setting(setting, text)
        _pending_inputs.pop(user_id, None)
        label = {
            "caption": "episode caption",
            "metadata": "filename metadata",
            "announcement_caption": "main-channel post caption",
        }[kind]
        await update.effective_message.reply_text(f"✅ Auto-upload {label} saved.")
        return True

    if kind == "anime_query":
        if not text or len(text) > 100:
            await update.effective_message.reply_text("Send an anime name up to 100 characters.")
            return True
        await update.effective_message.reply_text(f"Searching the anime site for “{text}”…")
        try:
            results = await service.search_anime(text)
        except Exception:
            results = []
        pending.update(kind="anime_results", query=text, results=results, page=0)
        await _show_search_results(update.effective_message, user_id)
        return True

    if kind in {"thumbnail", "main_post_image"}:
        if kind == "main_post_image":
            await update.effective_message.reply_text(
                "Send a photo for the main-channel anime image."
            )
            return True
        await update.effective_message.reply_text(
            "Send a photo for the thumbnail, or use Skip thumbnail and start."
        )
        return True

    try:
        channel_id = int(text)
    except ValueError:
        await update.effective_message.reply_text(
            "Send a numeric Telegram channel ID, such as -1001234567890."
        )
        return True

    if kind == "ongoing_channel":
        try:
            chat = await service.validate_channel(context.bot, channel_id)
        except Exception as exc:
            await update.effective_message.reply_text(f"❌ {exc}")
            return True
        pending.update(kind="anime_query", channel_id=str(chat.id))
        await update.effective_message.reply_text(
            f"✅ Channel verified: {chat.title} ({chat.id}).\n"
            "Now send the anime name to search for, for example: Overgear."
        )
        return True

    if kind in ("database_channel", "main_channel"):
        try:
            chat = await service.validate_channel(context.bot, channel_id)
        except Exception as exc:
            await update.effective_message.reply_text(f"❌ {exc}")
            return True
        setting = "database_channel_id" if kind == "database_channel" else "main_channel_id"
        db.set_autoupload_setting(setting, str(chat.id))
        _pending_inputs.pop(user_id, None)
        label = "thumbnail archive" if kind == "database_channel" else "main announcement"
        await update.effective_message.reply_text(
            f"✅ Auto-upload {label} channel set to {chat.title} ({chat.id})."
        )
        return True
    return False


async def handle_autoupload_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Store a video thumbnail or a separate main-channel anime image."""
    user_id = update.effective_user.id
    pending = _pending_inputs.get(user_id)
    if not pending or pending.get("kind") not in {"thumbnail", "main_post_image"}:
        return
    if not _is_admin(user_id):
        _pending_inputs.pop(user_id, None)
        await update.effective_message.reply_text("Owner/admin only.")
        return

    photo = update.effective_message.photo[-1]
    telegram_file = await context.bot.get_file(photo.file_id)
    photo_bytes = bytes(await telegram_file.download_as_bytearray())
    channel_id = pending["channel_id"]
    channel = db.get_autoupload_channel(channel_id)
    if not channel:
        _pending_inputs.pop(user_id, None)
        await update.effective_message.reply_text("That channel is no longer configured.")
        return

    if pending["kind"] == "main_post_image":
        if not db.set_autoupload_channel_main_post_image(
            channel_id, photo_bytes, photo.file_id
        ):
            _pending_inputs.pop(user_id, None)
            await update.effective_message.reply_text(
                "Could not save that main-channel image. Please try again."
            )
            return
        _pending_inputs.pop(user_id, None)
        await update.effective_message.reply_text(
            f"✅ Main-channel post image saved for {channel['anime_name']}. "
            "It is separate from the video thumbnail and will be reused for "
            "future posts."
        )
        if pending.get("setup_missing"):
            await _prompt_main_post_image(
                update.effective_message,
                user_id,
                missing_only=True,
                default_caption_saved=True,
            )
        return

    channel["episode_count"] = pending.get("episode_count")
    channel["episode_counts_by_season"] = pending.get(
        "episode_counts_by_season", {}
    )
    database_file_id = None
    archive_channel = db.get_autoupload_setting("database_channel_id")
    if archive_channel:
        try:
            archived = await context.bot.send_photo(
                archive_channel,
                photo=InputFile(io.BytesIO(photo_bytes), filename="anime-thumbnail.jpg"),
                caption=f"{channel['anime_name']} [Hindi Dub]",
            )
            database_file_id = archived.photo[-1].file_id
        except Exception:
            await update.effective_message.reply_text(
                "The thumbnail will be saved in the app database, but could not "
                "be copied to the archive channel."
            )

    if not db.set_autoupload_channel_thumbnail(
        channel_id, photo_bytes, database_file_id
    ):
        _pending_inputs.pop(user_id, None)
        await update.effective_message.reply_text("Could not save that thumbnail.")
        return

    channel = db.get_autoupload_channel(channel_id)
    episodes = pending.get("episodes") or []
    _pending_inputs.pop(user_id, None)
    await update.effective_message.reply_text(
        f"✅ Thumbnail saved for {channel['anime_name']}."
    )
    if pending.get("tracking_status"):
        db.set_autoupload_setting(
            f"status:{channel['channel_id']}", pending["tracking_status"]
        )
    if pending.get("start_after_save"):
        await _start_upload_task(
            update.effective_message,
            context,
            channel,
            episodes,
            pending.get("mode", "selected"),
        )