#This Bot Is Created By Shivam, Thanks To Shivam For Providing Repo
"""Notifications sent when a tracked episode is uploaded."""

import logging

import database as db
from config import OWNER_ID

logger = logging.getLogger(__name__)


async def notify_episode_uploaded(bot, anime_name, episode, language, quality, channel_id):
    recipients = {int(OWNER_ID)} if OWNER_ID else set()
    for admin in db.list_admins():
        try:
            recipients.add(int(admin["user_id"]))
        except (KeyError, TypeError, ValueError):
            continue

    text = (
        "🔔 Tracked episode uploaded\n"
        f"Anime: {anime_name}\n"
        f"Episode: S{episode.season:02d}E{episode.episode:02d}\n"
        f"Language: {language}\n"
        f"Quality: {quality}\n"
        f"Channel: {channel_id}"
    )
    for user_id in recipients:
        try:
            await bot.send_message(user_id, text)
        except Exception as exc:
            logger.warning("Could not notify admin %s about %s: %s", user_id, anime_name, exc)