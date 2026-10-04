#This Bot Is Created By Shivam, Thanks To Shivam For Providing Repo
from telegram import InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from colored_buttons import Button


def search_results_keyboard(results, page=0, per_page=8):
    """Create inline keyboard with search results."""
    kb = []
    start = page * per_page
    end = start + per_page
    page_results = results[start:end]
    
    for i, result in enumerate(page_results):
        callback_data = f"anime_select:{start + i}"
        kb.append([Button(result.title, callback_data=callback_data)])
    
    nav_buttons = []
    if page > 0:
        nav_buttons.append(Button("⬅️ Previous", callback_data=f"search_page:{page - 1}"))
    if end < len(results):
        nav_buttons.append(Button("Next ➡️", callback_data=f"search_page:{page + 1}"))
    
    if nav_buttons:
        kb.append(nav_buttons)
    
    kb.append([Button("❌ Cancel", callback_data="cancel")])
    return InlineKeyboardMarkup(kb)


def status_keyboard(anime_url, is_ongoing=True, is_completed=True):
    """Create ongoing/completed status buttons."""
    kb = []
    if is_ongoing:
        kb.append([Button("🔔 Ongoing", callback_data="status:ongoing")])
    if is_completed:
        kb.append([Button("✅ Completed", callback_data="status:completed")])
    
    if not kb:
        kb.append([Button("🔔 Ongoing", callback_data="status:ongoing")])
        kb.append([Button("✅ Completed", callback_data="status:completed")])
    
    kb.append([Button("⬅️ Back to search results", callback_data="upload:back_search")])
    kb.append([Button("❌ Cancel", callback_data="cancel")])
    return InlineKeyboardMarkup(kb)


def language_keyboard(languages, context=""):
    """Create language selection buttons."""
    lang_labels = {
        "hindi": "🇮🇳 Hindi",
        "tamil": "🇮🇳 Tamil",
        "telugu": "🇮🇳 Telugu",
        "bengali": "🇮🇳 Bengali",
        "malayalam": "🇮🇳 Malayalam",
        "english": "🇬🇧 English",
    }
    
    kb = []
    for lang in languages:
        label = lang_labels.get(lang.lower(), lang.capitalize())
        kb.append([Button(label, callback_data=f"lang:{lang}:{context}")])
    
    kb.append([Button("⬅️ Back", callback_data="upload:back_status")])
    kb.append([Button("❌ Cancel", callback_data="cancel")])
    return InlineKeyboardMarkup(kb)


def episode_keyboard(
    episodes, selected=None, page=0, callback_prefix="ep",
    back_callback="upload:back_previous", per_page=8,
):
    """Create a paginated episode picker for completed or ongoing anime."""
    if selected is None:
        selected = set()
    
    kb = []
    page_count = max(1, (len(episodes) + per_page - 1) // per_page)
    page = max(0, min(page, page_count - 1))
    start = page * per_page
    end = min(start + per_page, len(episodes))
    for index in range(start, end):
        ep = episodes[index]
        label = f"EPISODE {ep.episode}"
        if ep.season:
            label = f"S{ep.season:02d} • {label}"
        if ep.episode_type == "movie":
            label = "Movie"
        if ep.title:
            label += f" - {ep.title}"
        label = label[:60]
        
        key = f"{ep.season}_{ep.episode}"
        prefix = "✅ " if key in selected else ""
        
        kb.append([
            Button(f"{prefix}{label}", callback_data=f"{callback_prefix}:toggle:{index}")
        ])
    
    navigation = []
    if page > 0:
        navigation.append(Button("⬅️ Previous", callback_data=f"{callback_prefix}:page:{page - 1}"))
    if end < len(episodes):
        navigation.append(Button("Next ➡️", callback_data=f"{callback_prefix}:page:{page + 1}"))
    if navigation:
        kb.append(navigation)

    if callback_prefix == "ongoing":
        kb.append([Button("📥 Download Selected", callback_data="ongoing:selected")])
        kb.append([Button("✅ Download All Previous Episodes", callback_data="ongoing:all")])
        kb.append([Button("⬅️ Back to ongoing options", callback_data=back_callback)])
    else:
        kb.append([Button("📥 Download Selected", callback_data="ep:download")])
        kb.append([Button("✅ All Episodes", callback_data="ep:download_all")])
        kb.append([Button("⬅️ Back", callback_data=back_callback)])
    kb.append([Button("❌ Cancel", callback_data="cancel")])
    
    return InlineKeyboardMarkup(kb)


def quality_keyboard(download_links):
    """Create quality selection buttons."""
    kb = []
    for link in download_links:
        label = f"{link.quality} ({link.size})"
        callback = f"quality:{link.quality}:{link.url}"
        kb.append([Button(label, callback_data=callback)])
    
    kb.append([Button("❌ Cancel", callback_data="cancel")])
    return InlineKeyboardMarkup(kb)


def ongoing_action_keyboard(anime_name, language):
    """Buttons shown after ongoing tracking starts."""
    kb = [
        [Button("📋 List Episodes", callback_data="ongoing:list")],
        [Button("⏹️ Stop Tracking", callback_data="ongoing:stop")],
    ]
    return InlineKeyboardMarkup(kb)


def tracked_anime_keyboard(tracked_list):
    """Create keyboard for listing tracked anime."""
    kb = []
    for item in tracked_list:
        label = f"{item['anime_name']} [{item['language']}]"
        cb = f"tracked:select:{item['anime_name']}:{item['language']}"
        kb.append([Button(label, callback_data=cb)])
    
    if not tracked_list:
        kb.append([Button("No tracked anime", callback_data="noop:empty")])
    
    kb.append([Button("❌ Close", callback_data="cancel")])
    return InlineKeyboardMarkup(kb)


def admin_management_keyboard(admins):
    """Create admin management keyboard."""
    kb = [
        [Button("➕ Add Admin (Reply with ID)", callback_data="admin:add")],
    ]
    for admin in admins:
        kb.append([Button(
            f"🗑️ Remove {admin['user_id']}",
            callback_data=f"admin:remove:{admin['user_id']}"
        )])
    
    kb.append([Button("❌ Close", callback_data="cancel")])
    return InlineKeyboardMarkup(kb)


def channel_settings_keyboard():
    """Create channel settings keyboard."""
    kb = [
        [Button("📺 Set Channel", callback_data="channel:set")],
        [Button("📺 Remove Channel", callback_data="channel:remove")],
        [Button("📋 Test Channel", callback_data="channel:test")],
        [Button("❌ Close", callback_data="cancel")],
    ]
    return InlineKeyboardMarkup(kb)


def quality_preference_keyboard():
    """Create quality preference settings."""
    kb = [
        [Button("🎞️ 360P", callback_data="setquality:360P")],
        [Button("🎞️ 480P", callback_data="setquality:480P")],
        [Button("🎞️ 720P", callback_data="setquality:720P")],
        [Button("🎞️ 1080P", callback_data="setquality:1080P")],
        [Button("❌ Cancel", callback_data="cancel")],
    ]
    return InlineKeyboardMarkup(kb)


def main_admin_keyboard():
    """Main admin settings keyboard."""
    kb = [
        [Button("📺 Channel Settings", callback_data="admin:channel")],
        [Button("🎞️ Default Quality", callback_data="admin:quality")],
        [Button("👥 Admin Management", callback_data="admin:management")],
        [Button("📊 Bot Stats", callback_data="admin:stats")],
        [Button("❌ Close", callback_data="cancel")],
    ]
    return InlineKeyboardMarkup(kb)


def premium_duration_keyboard(user_id):
    """Select the premium term to grant to a Telegram user."""
    user_id = int(user_id)
    return InlineKeyboardMarkup([
        [Button("7 Days", callback_data=f"premium:duration:{user_id}:7")],
        [Button("15 Days", callback_data=f"premium:duration:{user_id}:15")],
        [Button("30 Days", callback_data=f"premium:duration:{user_id}:30")],
        [Button("Custom", callback_data=f"premium:duration:{user_id}:custom")],
    ])


def upload_ongoing_confirmation_keyboard(anime_name, language, season_page_url):
    """Choose whether to backfill currently available episodes before tracking."""
    kb = [
        [Button(
            "📥 Upload Previous Episodes First",
            callback_data="ongoing:backfill",
        )],
        [Button(
            "⏭️ Upload Ongoing Episodes Only",
            callback_data="ongoing:future",
        )],
        [Button("⬅️ Back", callback_data="upload:back_previous")],
        [Button("❌ Cancel", callback_data="cancel")],
    ]
    return InlineKeyboardMarkup(kb)


def episode_download_keyboard(episode_key, quality_url, quality):
    """Keyboard for downloading a specific episode quality."""
    kb = [
        [Button(f"⬇️ Download {quality}", callback_data=f"ep_dl:{quality_url}")],
    ]
    return InlineKeyboardMarkup(kb)
