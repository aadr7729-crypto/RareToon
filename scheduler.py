#This Bot Is Created By Shivam, Thanks To Shivam For Providing Repo
import os
import asyncio
import logging
import json

from telegram.constants import ChatAction

import database as db
import scraper
import video_downloader
from config import DOWNLOAD_DIR
from telegram_uploader import format_upload_filename, upload_video
from notifications import notify_episode_uploaded

logger = logging.getLogger(__name__)
TRACKED_UPLOAD_QUALITIES = ("360P", "720P", "1080P")


async def check_all_tracked(bot):
    """Check all tracked ongoing anime for new episodes."""
    tracked = db.list_tracked()
    for anime in tracked:
        try:
            await _check_anime_for_update(bot, anime)
        except Exception as e:
            try:
                anime_name = anime["anime_name"]
            except (KeyError, TypeError):
                anime_name = "?"
            logger.exception("Error checking %s: %s", anime_name, e)


async def _check_anime_for_update(bot, anime):
    """Upload every published-but-not-yet-uploaded episode for a tracked anime."""
    anime_name = anime["anime_name"]
    multiquality_url = anime["multiquality_url"]
    language = anime["language"]
    channel_id = anime["channel_id"]
    try:
        owner_id = anime["owner_id"]
    except (KeyError, IndexError, TypeError):
        owner_id = None
    try:
        season_page_url = anime["season_page_url"]
    except (KeyError, IndexError, TypeError):
        season_page_url = None

    if not season_page_url and not multiquality_url:
        return

    try:
        if season_page_url:
            season_data = await asyncio.to_thread(
                scraper.get_season_episodes, season_page_url, language
            )
            episodes = (
                season_data.get("episodes", [])
                if isinstance(season_data, dict) else []
            )
        else:
            episodes = await asyncio.to_thread(
                scraper.get_multiquality_episodes, multiquality_url
            )
    except Exception as e:
        logger.error("Error getting episodes for %s: %s", anime_name, e)
        return

    # Older tracked records may only have a multiquality URL. Keep their Hindi
    # tracking strict: ambiguous and Hindi-sub tracks are never eligible.
    if language.lower() == "hindi":
        episodes = [
            episode for episode in episodes
            if episode.audio_variant == "hindi_dub"
        ]

    if not episodes:
        return

    episodes = sorted(
        episodes,
        key=lambda episode: (int(episode.season or 0), int(episode.episode or 0)),
    )
    baseline = anime.get("baseline_episodes", [])
    if isinstance(baseline, str):
        try:
            baseline = json.loads(baseline)
        except (TypeError, ValueError):
            baseline = []
    baseline = {str(item) for item in baseline or []}
    latest = episodes[-1]
    latest_season, latest_number = int(latest.season or 0), int(latest.episode or 0)

    for episode in episodes:
        if f"{int(episode.season or 0)}:{int(episode.episode or 0)}" in baseline:
            continue
        label = f"S{episode.season:02d}E{episode.episode:02d}"
        try:
            links, _ = await asyncio.to_thread(
                scraper.get_all_download_urls, episode.download_url
            )
        except Exception:
            logger.exception("Could not resolve %s %s", anime_name, label)
            continue

        if not links or isinstance(links, dict):
            logger.error("No download links for %s %s", anime_name, label)
            continue

        for target_quality in TRACKED_UPLOAD_QUALITIES:
            selected = next(
                (
                    link for link in links
                    if db.normalize_quality_label(link.quality) == target_quality
                ),
                None,
            )
            if not selected:
                continue
            if db.is_episode_uploaded(
                anime_name, episode.season, episode.episode,
                language, target_quality, channel_id,
            ):
                continue

            safe_title = anime_name.replace("/", "_").replace("\\", "_").replace(" ", "_")
            filename = f"{safe_title}_{label}_{target_quality}.mp4"
            filepath = os.path.join(DOWNLOAD_DIR, filename)
            if not db.claim_episode_upload(
                anime_name, episode.season, episode.episode,
                language, target_quality, channel_id,
            ):
                continue
            try:
                downloaded = await asyncio.to_thread(
                    video_downloader.download_video, selected.url, filepath
                )
                if not downloaded:
                    logger.error(
                        "Download failed for %s %s %s",
                        anime_name, label, target_quality,
                    )
                    continue

                await bot.send_chat_action(
                    chat_id=channel_id, action=ChatAction.UPLOAD_VIDEO
                )
                caption = (
                    f"🎬 {anime_name}\n"
                    f"📺 {label}\n"
                    f"🎞️ {target_quality} ({selected.size})\n"
                    f"🌐 {language}\n"
                    "🔔 NEW EPISODE"
                )
                preferences = (
                    db.get_user_preferences(owner_id) if owner_id is not None else {}
                )
                upload_filename = format_upload_filename(
                    filename, preferences.get("metadata_prefix"),
                    anime_name, episode.season, episode.episode, target_quality,
                )
                receipt = await upload_video(
                    bot, channel_id, filepath, caption, upload_filename,
                    thumbnail=preferences.get("thumbnail"),
                )
                file_hash = video_downloader.get_file_hash(filepath)
                db.record_upload(
                    anime_name, episode.season, episode.episode, language,
                    target_quality, file_hash, receipt.message_id, channel_id
                )
                db.update_tracked_episode(
                    anime_name, language, str(episode.episode), episode.season
                )
                await notify_episode_uploaded(
                    bot, anime_name, episode, language, target_quality, channel_id
                )
                logger.info(
                    "Uploaded tracked episode %s %s at %s",
                    anime_name, label, target_quality,
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception(
                    "Error uploading %s %s at %s",
                    anime_name, label, target_quality,
                )
            finally:
                video_downloader.cleanup_file(filepath)
                db.release_episode_claim(
                    anime_name, episode.season, episode.episode,
                    language, target_quality, channel_id,
                )

    # The database cursor is informational. Every published episode is scanned
    # above, so a temporary failure is retried on the next scheduler run.
    db.update_tracked_episode(
        anime_name, language, str(latest_number), latest_season
    )


async def periodic_check_job(context):
    """JobQueue callback for periodic checks."""
    await check_all_tracked(context.bot)
