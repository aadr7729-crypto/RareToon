#This Bot Is Created By Shivam, Thanks To Shivam For Providing Repo
import sqlite3
import os
import time
import json
import re

from config import DATABASE_PATH, MONGODB_URI, MONGO_DB_NAME

try:
    from pymongo import MongoClient, ReturnDocument
except ImportError:  # pragma: no cover
    MongoClient = None
    ReturnDocument = None

_schema = """
CREATE TABLE IF NOT EXISTS admins (
    user_id INTEGER PRIMARY KEY,
    granted_by INTEGER,
    granted_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS premiums (
    user_id INTEGER PRIMARY KEY,
    granted_by INTEGER NOT NULL,
    granted_at REAL NOT NULL,
    expires_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS config (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS tracked_anime (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    anime_name TEXT,
    season_page_url TEXT,
    multiquality_url TEXT,
    language TEXT,
    last_episode TEXT,
    last_season INTEGER,
    status TEXT DEFAULT 'ongoing',
    channel_id TEXT,
    owner_id INTEGER,
    baseline_episodes TEXT DEFAULT '[]',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(anime_name, language)
);

CREATE TABLE IF NOT EXISTS user_preferences (
    user_id INTEGER PRIMARY KEY,
    metadata_prefix TEXT,
    thumbnail BLOB
);

CREATE TABLE IF NOT EXISTS uploaded_episodes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    anime_name TEXT,
    season_num INTEGER,
    episode_num INTEGER,
    language TEXT,
    quality TEXT,
    file_hash TEXT,
    message_id INTEGER,
    channel_id TEXT,
    uploaded_at TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(anime_name, season_num, episode_num, language, quality, channel_id)
);

CREATE TABLE IF NOT EXISTS upload_claims (
    anime_name TEXT,
    season_num INTEGER,
    episode_num INTEGER,
    language TEXT,
    quality TEXT,
    channel_id TEXT,
    claimed_at REAL NOT NULL,
    expires_at REAL NOT NULL,
    UNIQUE(anime_name, season_num, episode_num, language, quality, channel_id)
);

CREATE TABLE IF NOT EXISTS anime_cache (
    key TEXT PRIMARY KEY,
    value TEXT,
    expires_at REAL
);

CREATE TABLE IF NOT EXISTS autoupload_channels (
    channel_id TEXT PRIMARY KEY,
    channel_title TEXT,
    anime_name TEXT NOT NULL,
    language TEXT NOT NULL,
    season_page_urls TEXT NOT NULL DEFAULT '[]',
    thumbnail BLOB,
    thumbnail_file_id TEXT,
    main_post_image BLOB,
    main_post_image_file_id TEXT,
    genres TEXT NOT NULL DEFAULT '[]',
    site_info TEXT NOT NULL DEFAULT '{}',
    invite_link TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS autoupload_uploaded_episodes (
    channel_id TEXT NOT NULL,
    anime_name TEXT NOT NULL,
    season_num INTEGER NOT NULL,
    episode_num INTEGER NOT NULL,
    language TEXT NOT NULL,
    quality TEXT NOT NULL,
    message_id INTEGER,
    uploaded_at TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(channel_id, season_num, episode_num, language, quality)
);

CREATE TABLE IF NOT EXISTS autoupload_announcements (
    channel_id TEXT NOT NULL,
    season_num INTEGER NOT NULL,
    episode_num INTEGER NOT NULL,
    language TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'sending',
    claimed_at REAL NOT NULL,
    message_id INTEGER,
    sent_at REAL,
    UNIQUE(channel_id, season_num, episode_num, language)
);

CREATE TABLE IF NOT EXISTS autoupload_discovery_requests (
    request_id TEXT PRIMARY KEY,
    anime_name TEXT NOT NULL,
    language TEXT NOT NULL,
    entry_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    channel_id TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
"""

_mongo_client = None
_mongo_db = None


def _mongo():
    """Return the configured MongoDB database, or None for legacy SQLite mode."""
    global _mongo_client, _mongo_db
    if not MONGODB_URI:
        return None
    if MongoClient is None:
        raise RuntimeError("MONGODB_URI is set, but pymongo is not installed.")
    if _mongo_db is None:
        _mongo_client = MongoClient(MONGODB_URI, serverSelectionTimeoutMS=5000)
        _mongo_client.admin.command("ping")
        _mongo_db = _mongo_client[MONGO_DB_NAME]
    return _mongo_db


def _get():
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    mongo = _mongo()
    if mongo is not None:
        try:
            mongo.admins.create_index("user_id", unique=True)
            mongo.premiums.create_index("user_id", unique=True)
            mongo.config.create_index("key", unique=True)
            mongo.user_preferences.create_index("user_id", unique=True)
            mongo.tracked_anime.create_index(
                [("anime_name", 1), ("language", 1)], unique=True
            )
            for tracked in mongo.tracked_anime.find(
                {
                    "owner_id": {"$exists": False},
                    "channel_id": {"$regex": "^[1-9][0-9]*$"},
                },
                {"channel_id": 1},
            ):
                mongo.tracked_anime.update_one(
                    {"_id": tracked["_id"], "owner_id": {"$exists": False}},
                    {"$set": {"owner_id": int(tracked["channel_id"])}},
                )
            legacy_upload_key = [
                ("anime_name", 1),
                ("season_num", 1),
                ("episode_num", 1),
                ("language", 1),
                ("quality", 1),
            ]
            for index in mongo.uploaded_episodes.list_indexes():
                if index.get("unique") and list(index["key"].items()) == legacy_upload_key:
                    mongo.uploaded_episodes.drop_index(index["name"])
            mongo.uploaded_episodes.create_index(
                [
                    ("anime_name", 1),
                    ("season_num", 1),
                    ("episode_num", 1),
                    ("language", 1),
                    ("quality", 1),
                    ("channel_id", 1),
                ],
                unique=True,
                name="episode_channel_upload_unique",
            )
            claim_key = [
                ("anime_name", 1),
                ("season_num", 1),
                ("episode_num", 1),
                ("language", 1),
                ("quality", 1),
                ("channel_id", 1),
            ]
            mongo.upload_claims.create_index(
                claim_key, unique=True, name="episode_channel_claim_unique"
            )
            mongo.upload_claims.create_index("expires_at", expireAfterSeconds=0)
            mongo.anime_cache.create_index("key", unique=True)
            mongo.autoupload_channels.create_index("channel_id", unique=True)
            mongo.autoupload_channels.create_index(
                [("anime_name", 1), ("language", 1)]
            )
            mongo.autoupload_uploaded_episodes.create_index(
                [
                    ("channel_id", 1),
                    ("season_num", 1),
                    ("episode_num", 1),
                    ("language", 1),
                    ("quality", 1),
                ],
                unique=True,
                name="autoupload_episode_channel_unique",
            )
            mongo.autoupload_announcements.create_index(
                [
                    ("channel_id", 1),
                    ("season_num", 1),
                    ("episode_num", 1),
                    ("language", 1),
                ],
                unique=True,
                name="autoupload_announcement_episode_unique",
            )
            mongo.autoupload_discovery_requests.create_index(
                "request_id", unique=True, name="autoupload_discovery_request_unique"
            )
        except Exception as exc:
            raise RuntimeError(
                f"MongoDB database '{MONGO_DB_NAME}' could not be initialized. "
                "If Atlas reports the 100-database limit, remove an unused "
                "database or use a cluster with capacity for this database."
            ) from exc
        return
    conn = _get()
    conn.executescript(_schema)
    autoupload_columns = {
        column["name"]
        for column in conn.execute("PRAGMA table_info(autoupload_channels)")
    }
    if "main_post_image" not in autoupload_columns:
        conn.execute("ALTER TABLE autoupload_channels ADD COLUMN main_post_image BLOB")
    if "main_post_image_file_id" not in autoupload_columns:
        conn.execute(
            "ALTER TABLE autoupload_channels ADD COLUMN main_post_image_file_id TEXT"
        )
    if "site_info" not in autoupload_columns:
        conn.execute(
            "ALTER TABLE autoupload_channels ADD COLUMN site_info TEXT NOT NULL DEFAULT '{}'"
        )
    tracked_columns = {
        column["name"] for column in conn.execute("PRAGMA table_info(tracked_anime)")
    }
    if "owner_id" not in tracked_columns:
        conn.execute("ALTER TABLE tracked_anime ADD COLUMN owner_id INTEGER")
    if "baseline_episodes" not in tracked_columns:
        conn.execute(
            "ALTER TABLE tracked_anime ADD COLUMN baseline_episodes TEXT DEFAULT '[]'"
        )
    conn.execute(
        """UPDATE tracked_anime SET owner_id=CAST(channel_id AS INTEGER)
           WHERE owner_id IS NULL
             AND channel_id GLOB '[0-9]*'
             AND channel_id NOT GLOB '*[^0-9]*'
             AND CAST(channel_id AS INTEGER)>0"""
    )
    conn.commit()
    _migrate_sqlite_upload_uniqueness(conn)
    conn.commit()
    conn.close()


def _migrate_sqlite_upload_uniqueness(conn):
    """Allow the same episode in different channels while keeping old records."""
    upload_columns = {
        column["name"]
        for column in conn.execute("PRAGMA table_info(uploaded_episodes)")
    }
    unique_indexes = []
    for index in conn.execute("PRAGMA index_list(uploaded_episodes)"):
        if not index["unique"]:
            continue
        index_name = index["name"].replace('"', '""')
        columns = conn.execute(
            f'PRAGMA index_info("{index_name}")'
        ).fetchall()
        unique_indexes.append(tuple(column["name"] for column in columns))

    required = (
        "anime_name", "season_num", "episode_num", "language", "quality", "channel_id"
    )
    if required in unique_indexes:
        return

    channel_expression = (
        "COALESCE(channel_id, '')" if "channel_id" in upload_columns else "''"
    )
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute(
            """CREATE TABLE uploaded_episodes_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                anime_name TEXT,
                season_num INTEGER,
                episode_num INTEGER,
                language TEXT,
                quality TEXT,
                file_hash TEXT,
                message_id INTEGER,
                channel_id TEXT,
                uploaded_at TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(anime_name, season_num, episode_num, language, quality, channel_id)
            )"""
        )
        conn.execute(
            f"""INSERT OR IGNORE INTO uploaded_episodes_new
               (id, anime_name, season_num, episode_num, language, quality,
                file_hash, message_id, channel_id, uploaded_at)
               SELECT id, anime_name, season_num, episode_num, language, quality,
                       file_hash, message_id, {channel_expression}, uploaded_at
               FROM uploaded_episodes"""
        )
        conn.execute("DROP TABLE uploaded_episodes")
        conn.execute(
            "ALTER TABLE uploaded_episodes_new RENAME TO uploaded_episodes"
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def get_config(key, default=None):
    mongo = _mongo()
    if mongo is not None:
        row = mongo.config.find_one({"key": key})
        return row["value"] if row else default
    conn = _get()
    row = conn.execute("SELECT value FROM config WHERE key=?", (key,)).fetchone()
    conn.close()
    return row["value"] if row else default


def set_config(key, value):
    mongo = _mongo()
    if mongo is not None:
        mongo.config.update_one(
            {"key": key}, {"$set": {"key": key, "value": str(value)}}, upsert=True
        )
        return
    conn = _get()
    conn.execute(
        "INSERT INTO config (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, str(value)),
    )
    conn.commit()
    conn.close()


def get_autoupload_setting(key, default=None):
    """Read a setting reserved for the separate /autoupload feature."""
    return get_config(f"autoupload:{key}", default)


def set_autoupload_setting(key, value):
    """Write a setting reserved for the separate /autoupload feature."""
    set_config(f"autoupload:{key}", value)


def get_autoupload_ignored_episodes(channel_id):
    """Return episode keys intentionally skipped during initial channel setup."""
    raw = get_autoupload_setting(f"ignored_episodes:{channel_id}", "[]")
    try:
        items = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        return set()
    return {
        (int(item[0]), int(item[1]))
        for item in items or []
        if isinstance(item, (list, tuple)) and len(item) == 2
    }


def set_autoupload_ignored_episodes(channel_id, episode_keys):
    """Persist episodes that predate tracking and must not be backfilled."""
    normalized = sorted({
        (int(season or 0), int(episode or 0))
        for season, episode in (episode_keys or [])
    })
    set_autoupload_setting(
        f"ignored_episodes:{channel_id}",
        json.dumps(normalized),
    )


def add_autoupload_channel(
    channel_id, channel_title, anime_name, language, season_page_urls,
    thumbnail=None, thumbnail_file_id=None, genres=None, invite_link=None,
    site_info=None,
):
    """Create or update an anime-to-channel mapping without touching manual tracks."""
    channel_id = str(channel_id)
    season_page_urls = list(dict.fromkeys(season_page_urls or []))
    genres = list(genres or [])
    fields = {
        "channel_id": channel_id,
        "channel_title": channel_title,
        "anime_name": anime_name,
        "language": str(language).lower(),
        "season_page_urls": season_page_urls,
        "genres": genres,
        "site_info": site_info or {},
        "invite_link": invite_link,
    }
    if thumbnail is not None:
        fields["thumbnail"] = thumbnail
    if thumbnail_file_id is not None:
        fields["thumbnail_file_id"] = thumbnail_file_id

    mongo = _mongo()
    if mongo is not None:
        mongo.autoupload_channels.update_one(
            {"channel_id": channel_id},
            {"$set": fields, "$setOnInsert": {
                "created_at": time.strftime("%Y-%m-%d %H:%M:%S")
            }},
            upsert=True,
        )
        return

    conn = _get()
    existing = conn.execute(
        "SELECT thumbnail, thumbnail_file_id FROM autoupload_channels WHERE channel_id=?",
        (channel_id,),
    ).fetchone()
    if existing and thumbnail is None:
        fields["thumbnail"] = existing["thumbnail"]
    if existing and thumbnail_file_id is None:
        fields["thumbnail_file_id"] = existing["thumbnail_file_id"]
    conn.execute(
        """INSERT INTO autoupload_channels
           (channel_id, channel_title, anime_name, language, season_page_urls,
            thumbnail, thumbnail_file_id, genres, site_info, invite_link)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(channel_id) DO UPDATE SET
             channel_title=excluded.channel_title,
             anime_name=excluded.anime_name,
             language=excluded.language,
             season_page_urls=excluded.season_page_urls,
             thumbnail=excluded.thumbnail,
             thumbnail_file_id=excluded.thumbnail_file_id,
             genres=excluded.genres,
             site_info=excluded.site_info,
             invite_link=excluded.invite_link""",
        (
            channel_id, channel_title, anime_name, str(language).lower(),
            json.dumps(season_page_urls), fields.get("thumbnail"),
            fields.get("thumbnail_file_id"), json.dumps(genres),
            json.dumps(fields["site_info"]), invite_link,
        ),
    )
    conn.commit()
    conn.close()


def get_autoupload_channel(channel_id):
    """Return one automation mapping, decoding its JSON fields."""
    channel_id = str(channel_id)
    mongo = _mongo()
    if mongo is not None:
        row = mongo.autoupload_channels.find_one(
            {"channel_id": channel_id}, {"_id": 0}
        )
        if row:
            row["season_page_urls"] = row.get("season_page_urls") or []
            row["genres"] = row.get("genres") or []
            row["site_info"] = row.get("site_info") or {}
        return row

    conn = _get()
    row = conn.execute(
        "SELECT * FROM autoupload_channels WHERE channel_id=?", (channel_id,)
    ).fetchone()
    conn.close()
    if not row:
        return None
    item = dict(row)
    for field in ("season_page_urls", "genres", "site_info"):
        try:
            item[field] = json.loads(
                item.get(field) or ("{}" if field == "site_info" else "[]")
            )
        except (TypeError, ValueError):
            item[field] = {} if field == "site_info" else []
    return item


def list_autoupload_channels():
    """List managed channels without changing or exposing manual tracking state."""
    mongo = _mongo()
    if mongo is not None:
        return list(
            mongo.autoupload_channels.find({}, {"_id": 0}).sort("created_at", 1)
        )
    conn = _get()
    rows = conn.execute(
        "SELECT * FROM autoupload_channels ORDER BY created_at"
    ).fetchall()
    conn.close()
    result = []
    for row in rows:
        item = dict(row)
        for field in ("season_page_urls", "genres", "site_info"):
            try:
                item[field] = json.loads(
                    item.get(field) or ("{}" if field == "site_info" else "[]")
                )
            except (TypeError, ValueError):
                item[field] = {} if field == "site_info" else []
        result.append(item)
    return result


def remove_autoupload_channel(channel_id):
    """Remove a channel from automation but keep its upload history for deduping."""
    channel_id = str(channel_id)
    mongo = _mongo()
    if mongo is not None:
        return mongo.autoupload_channels.delete_one({"channel_id": channel_id}).deleted_count > 0
    conn = _get()
    changed = conn.execute(
        "DELETE FROM autoupload_channels WHERE channel_id=?", (channel_id,)
    ).rowcount
    conn.commit()
    conn.close()
    return changed > 0


def set_autoupload_channel_thumbnail(channel_id, image_data, file_id=None):
    """Save an automation-only per-anime thumbnail."""
    channel_id = str(channel_id)
    mongo = _mongo()
    if mongo is not None:
        result = mongo.autoupload_channels.update_one(
            {"channel_id": channel_id},
            {"$set": {"thumbnail": image_data, "thumbnail_file_id": file_id}},
        )
        return result.matched_count > 0
    conn = _get()
    changed = conn.execute(
        """UPDATE autoupload_channels
           SET thumbnail=?, thumbnail_file_id=? WHERE channel_id=?""",
        (image_data, file_id, channel_id),
    ).rowcount
    conn.commit()
    conn.close()
    return changed > 0


def set_autoupload_channel_main_post_image(channel_id, image_data, file_id=None):
    """Save the per-anime image used only in main-channel announcements."""
    channel_id = str(channel_id)
    mongo = _mongo()
    if mongo is not None:
        result = mongo.autoupload_channels.update_one(
            {"channel_id": channel_id},
            {"$set": {
                "main_post_image": image_data,
                "main_post_image_file_id": file_id,
            }},
        )
        return result.matched_count > 0
    conn = _get()
    changed = conn.execute(
        """UPDATE autoupload_channels
           SET main_post_image=?, main_post_image_file_id=? WHERE channel_id=?""",
        (image_data, file_id, channel_id),
    ).rowcount
    conn.commit()
    conn.close()
    return changed > 0


def set_autoupload_channel_site_info(channel_id, site_info):
    """Store source-page metadata used in the channel's announcement caption."""
    channel_id = str(channel_id)
    site_info = site_info or {}
    mongo = _mongo()
    if mongo is not None:
        result = mongo.autoupload_channels.update_one(
            {"channel_id": channel_id},
            {"$set": {"site_info": site_info}},
        )
        return result.matched_count > 0
    conn = _get()
    changed = conn.execute(
        "UPDATE autoupload_channels SET site_info=? WHERE channel_id=?",
        (json.dumps(site_info), channel_id),
    ).rowcount
    conn.commit()
    conn.close()
    return changed > 0


def is_autoupload_episode_uploaded(
    channel_id, season_num, episode_num, language, quality
):
    """Check the isolated automation upload ledger."""
    identity = {
        "channel_id": str(channel_id),
        "season_num": int(season_num or 0),
        "episode_num": int(episode_num or 0),
        "language": str(language).lower(),
        "quality": normalize_quality_label(quality),
    }
    mongo = _mongo()
    if mongo is not None:
        records = mongo.autoupload_uploaded_episodes.find(
            {key: value for key, value in identity.items() if key != "quality"},
            {"quality": 1, "_id": 0},
        )
        return any(
            normalize_quality_label(row.get("quality")) == identity["quality"]
            for row in records
        )
    conn = _get()
    row = conn.execute(
        """SELECT quality FROM autoupload_uploaded_episodes
           WHERE channel_id=? AND season_num=? AND episode_num=?
             AND language=?""",
        (
            identity["channel_id"], identity["season_num"],
            identity["episode_num"], identity["language"],
        ),
    ).fetchall()
    conn.close()
    return any(
        normalize_quality_label(item["quality"]) == identity["quality"]
        for item in row
    )


def record_autoupload_episode(
    channel_id, anime_name, season_num, episode_num, language, quality, message_id
):
    """Record an auto-upload without affecting the ordinary upload history."""
    row = {
        "channel_id": str(channel_id),
        "anime_name": anime_name,
        "season_num": int(season_num or 0),
        "episode_num": int(episode_num or 0),
        "language": str(language).lower(),
        "quality": normalize_quality_label(quality),
        "message_id": message_id,
    }
    mongo = _mongo()
    if mongo is not None:
        mongo.autoupload_uploaded_episodes.update_one(
            {
                "channel_id": row["channel_id"],
                "season_num": row["season_num"],
                "episode_num": row["episode_num"],
                "language": row["language"],
                "quality": row["quality"],
            },
            {"$setOnInsert": {
                **row, "uploaded_at": time.strftime("%Y-%m-%d %H:%M:%S")
            }},
            upsert=True,
        )
        return
    conn = _get()
    conn.execute(
        """INSERT OR IGNORE INTO autoupload_uploaded_episodes
           (channel_id, anime_name, season_num, episode_num, language, quality, message_id)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            row["channel_id"], row["anime_name"], row["season_num"],
            row["episode_num"], row["language"], row["quality"],
            row["message_id"],
        ),
    )
    conn.commit()
    conn.close()


def get_autoupload_episode_record(
    channel_id, season_num, episode_num, language, quality
):
    """Return the ledger row for one auto-uploaded quality, if present."""
    identity = {
        "channel_id": str(channel_id),
        "season_num": int(season_num or 0),
        "episode_num": int(episode_num or 0),
        "language": str(language).lower(),
        "quality": normalize_quality_label(quality),
    }
    mongo = _mongo()
    if mongo is not None:
        return mongo.autoupload_uploaded_episodes.find_one(identity, {"_id": 0})
    conn = _get()
    row = conn.execute(
        """SELECT * FROM autoupload_uploaded_episodes
           WHERE channel_id=? AND season_num=? AND episode_num=?
             AND language=? AND quality=?""",
        (
            identity["channel_id"], identity["season_num"],
            identity["episode_num"], identity["language"], identity["quality"],
        ),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def claim_autoupload_announcement(
    channel_id, season_num, episode_num, language, lease_seconds=900
):
    """Atomically reserve the one main-channel post for an episode."""
    identity = {
        "channel_id": str(channel_id),
        "season_num": int(season_num or 0),
        "episode_num": int(episode_num or 0),
        "language": str(language).lower(),
    }
    now = time.time()
    mongo = _mongo()
    if mongo is not None:
        record = {**identity, "status": "sending", "claimed_at": now}
        try:
            mongo.autoupload_announcements.insert_one(record)
            return True
        except Exception:
            existing = mongo.autoupload_announcements.find_one(identity)
            if not existing:
                raise
            if existing.get("status") == "sent":
                return False
            claimed_at = float(existing.get("claimed_at", 0) or 0)
            if now - claimed_at < lease_seconds:
                return False
            result = mongo.autoupload_announcements.update_one(
                {**identity, "status": "sending", "claimed_at": existing.get("claimed_at")},
                {"$set": {"claimed_at": now}},
            )
            return result.modified_count > 0

    conn = _get()
    inserted = conn.execute(
        """INSERT OR IGNORE INTO autoupload_announcements
           (channel_id, season_num, episode_num, language, status, claimed_at)
           VALUES (?, ?, ?, ?, 'sending', ?)""",
        (
            identity["channel_id"], identity["season_num"],
            identity["episode_num"], identity["language"], now,
        ),
    ).rowcount
    if inserted:
        conn.commit()
        conn.close()
        return True
    existing = conn.execute(
        """SELECT status, claimed_at FROM autoupload_announcements
           WHERE channel_id=? AND season_num=? AND episode_num=? AND language=?""",
        (
            identity["channel_id"], identity["season_num"],
            identity["episode_num"], identity["language"],
        ),
    ).fetchone()
    if not existing or existing["status"] == "sent":
        conn.close()
        return False
    if now - float(existing["claimed_at"] or 0) < lease_seconds:
        conn.close()
        return False
    changed = conn.execute(
        """UPDATE autoupload_announcements SET claimed_at=?
           WHERE channel_id=? AND season_num=? AND episode_num=? AND language=?
             AND status='sending' AND claimed_at=?""",
        (
            now, identity["channel_id"], identity["season_num"],
            identity["episode_num"], identity["language"], existing["claimed_at"],
        ),
    ).rowcount
    conn.commit()
    conn.close()
    return changed > 0


def record_autoupload_announcement(
    channel_id, season_num, episode_num, language, message_id
):
    identity = {
        "channel_id": str(channel_id),
        "season_num": int(season_num or 0),
        "episode_num": int(episode_num or 0),
        "language": str(language).lower(),
    }
    now = time.time()
    mongo = _mongo()
    if mongo is not None:
        mongo.autoupload_announcements.update_one(
            identity,
            {"$set": {
                "status": "sent", "message_id": message_id, "sent_at": now,
            }, "$setOnInsert": {"claimed_at": now}},
            upsert=True,
        )
        return
    conn = _get()
    conn.execute(
        """INSERT INTO autoupload_announcements
           (channel_id, season_num, episode_num, language, status, claimed_at,
            message_id, sent_at)
           VALUES (?, ?, ?, ?, 'sent', ?, ?, ?)
           ON CONFLICT(channel_id, season_num, episode_num, language)
           DO UPDATE SET status='sent', message_id=excluded.message_id,
                         sent_at=excluded.sent_at""",
        (
            identity["channel_id"], identity["season_num"],
            identity["episode_num"], identity["language"], now, message_id, now,
        ),
    )
    conn.commit()
    conn.close()


def release_autoupload_announcement(
    channel_id, season_num, episode_num, language
):
    identity = {
        "channel_id": str(channel_id),
        "season_num": int(season_num or 0),
        "episode_num": int(episode_num or 0),
        "language": str(language).lower(),
    }
    mongo = _mongo()
    if mongo is not None:
        mongo.autoupload_announcements.delete_one({**identity, "status": "sending"})
        return
    conn = _get()
    conn.execute(
        """DELETE FROM autoupload_announcements
           WHERE channel_id=? AND season_num=? AND episode_num=?
             AND language=? AND status='sending'""",
        (
            identity["channel_id"], identity["season_num"],
            identity["episode_num"], identity["language"],
        ),
    )
    conn.commit()
    conn.close()


def add_autoupload_discovery_request(
    request_id, anime_name, language, entry
):
    """Persist a discovery candidate so it cannot create a channel without approval."""
    now = time.time()
    row = {
        "request_id": str(request_id),
        "anime_name": str(anime_name),
        "language": str(language).lower(),
        "entry": entry,
    }
    mongo = _mongo()
    if mongo is not None:
        existing = mongo.autoupload_discovery_requests.find_one(
            {"request_id": row["request_id"]}, {"_id": 0}
        )
        if existing:
            if existing.get("status") == "pending":
                mongo.autoupload_discovery_requests.update_one(
                    {"request_id": row["request_id"], "status": "pending"},
                    {"$set": {"anime_name": row["anime_name"],
                              "language": row["language"], "entry": entry,
                              "updated_at": now}},
                )
                existing = mongo.autoupload_discovery_requests.find_one(
                    {"request_id": row["request_id"]}, {"_id": 0}
                )
            return existing, False
        try:
            mongo.autoupload_discovery_requests.insert_one({
                **row, "status": "pending", "created_at": now, "updated_at": now,
            })
            return {
                **row, "status": "pending", "created_at": now, "updated_at": now,
            }, True
        except Exception:
            existing = mongo.autoupload_discovery_requests.find_one(
                {"request_id": row["request_id"]}, {"_id": 0}
            )
            if not existing:
                raise
            return existing, False

    conn = _get()
    inserted = conn.execute(
        """INSERT OR IGNORE INTO autoupload_discovery_requests
           (request_id, anime_name, language, entry_json, status, created_at, updated_at)
           VALUES (?, ?, ?, ?, 'pending', ?, ?)""",
        (
            row["request_id"], row["anime_name"], row["language"],
            json.dumps(entry), now, now,
        ),
    ).rowcount
    if not inserted:
        conn.execute(
            """UPDATE autoupload_discovery_requests
               SET anime_name=?, language=?, entry_json=?, updated_at=?
               WHERE request_id=? AND status='pending'""",
            (
                row["anime_name"], row["language"], json.dumps(entry),
                now, row["request_id"],
            ),
        )
    conn.commit()
    conn.close()
    return get_autoupload_discovery_request(row["request_id"]), bool(inserted)


def get_autoupload_discovery_request(request_id):
    mongo = _mongo()
    if mongo is not None:
        return mongo.autoupload_discovery_requests.find_one(
            {"request_id": str(request_id)}, {"_id": 0}
        )
    conn = _get()
    row = conn.execute(
        "SELECT * FROM autoupload_discovery_requests WHERE request_id=?",
        (str(request_id),),
    ).fetchone()
    conn.close()
    if not row:
        return None
    item = dict(row)
    try:
        item["entry"] = json.loads(item.pop("entry_json"))
    except (TypeError, ValueError):
        item["entry"] = {}
    return item


def claim_autoupload_discovery_request(request_id):
    """Move a pending or failed approval to creating, preventing duplicate channels."""
    mongo = _mongo()
    if mongo is not None:
        result = mongo.autoupload_discovery_requests.update_one(
            {"request_id": str(request_id), "status": {"$in": ["pending", "failed"]}},
            {"$set": {"status": "creating", "updated_at": time.time()}},
        )
        return result.modified_count > 0
    conn = _get()
    changed = conn.execute(
        """UPDATE autoupload_discovery_requests
           SET status='creating', updated_at=?
           WHERE request_id=? AND status IN ('pending', 'failed')""",
        (time.time(), str(request_id)),
    ).rowcount
    conn.commit()
    conn.close()
    return changed > 0


def set_autoupload_discovery_status(request_id, status, channel_id=None):
    mongo = _mongo()
    fields = {"status": str(status), "updated_at": time.time()}
    if channel_id is not None:
        fields["channel_id"] = str(channel_id)
    if mongo is not None:
        mongo.autoupload_discovery_requests.update_one(
            {"request_id": str(request_id)}, {"$set": fields}
        )
        return
    assignments = ", ".join(f"{key}=?" for key in fields)
    conn = _get()
    conn.execute(
        f"UPDATE autoupload_discovery_requests SET {assignments} WHERE request_id=?",
        (*fields.values(), str(request_id)),
    )
    conn.commit()
    conn.close()


def get_user_preferences(user_id):
    """Return per-user upload settings without exposing another user's values."""
    mongo = _mongo()
    if mongo is not None:
        return mongo.user_preferences.find_one(
            {"user_id": int(user_id)}, {"_id": 0}
        ) or {}
    conn = _get()
    row = conn.execute(
        "SELECT metadata_prefix, thumbnail FROM user_preferences WHERE user_id=?",
        (int(user_id),),
    ).fetchone()
    conn.close()
    return dict(row) if row else {}


def _set_user_preference(user_id, key, value):
    if key not in {"metadata_prefix", "thumbnail"}:
        raise ValueError("Unsupported user preference.")
    user_id = int(user_id)
    mongo = _mongo()
    if mongo is not None:
        mongo.user_preferences.update_one(
            {"user_id": user_id},
            {"$set": {"user_id": user_id, key: value}},
            upsert=True,
        )
        return
    conn = _get()
    conn.execute(
        f"""INSERT INTO user_preferences (user_id, {key}) VALUES (?, ?)
            ON CONFLICT(user_id) DO UPDATE SET {key}=excluded.{key}""",
        (user_id, value),
    )
    conn.commit()
    conn.close()


def set_user_metadata_prefix(user_id, prefix):
    _set_user_preference(user_id, "metadata_prefix", str(prefix))


def clear_user_metadata_prefix(user_id):
    _set_user_preference(user_id, "metadata_prefix", None)


def set_user_thumbnail(user_id, thumbnail):
    _set_user_preference(user_id, "thumbnail", bytes(thumbnail))


def clear_user_thumbnail(user_id):
    _set_user_preference(user_id, "thumbnail", None)


def is_admin(user_id):
    mongo = _mongo()
    if mongo is not None:
        return mongo.admins.find_one({"user_id": int(user_id)}, {"_id": 1}) is not None
    conn = _get()
    row = conn.execute("SELECT 1 FROM admins WHERE user_id=?", (user_id,)).fetchone()
    conn.close()
    return row is not None


def is_owner(user_id):
    from config import OWNER_ID
    return user_id == OWNER_ID or is_admin(user_id)


def add_admin(user_id, granted_by):
    mongo = _mongo()
    if mongo is not None:
        mongo.admins.update_one(
            {"user_id": int(user_id)},
            {
                "$setOnInsert": {
                    "user_id": int(user_id),
                    "granted_by": int(granted_by),
                    "granted_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                }
            },
            upsert=True,
        )
        return
    conn = _get()
    conn.execute(
        "INSERT OR IGNORE INTO admins (user_id, granted_by) VALUES (?, ?)",
        (user_id, granted_by),
    )
    conn.commit()
    conn.close()


def remove_admin(user_id):
    mongo = _mongo()
    if mongo is not None:
        mongo.admins.delete_one({"user_id": int(user_id)})
        return
    conn = _get()
    conn.execute("DELETE FROM admins WHERE user_id=?", (user_id,))
    conn.commit()
    conn.close()


def list_admins():
    mongo = _mongo()
    if mongo is not None:
        return list(mongo.admins.find({}, {"_id": 0}).sort("granted_at", 1))
    conn = _get()
    rows = conn.execute(
        "SELECT user_id, granted_by, granted_at FROM admins ORDER BY granted_at"
    ).fetchall()
    conn.close()
    return rows


def has_premium(user_id):
    """Return whether a user has an unexpired premium grant."""
    user_id = int(user_id)
    now = time.time()
    mongo = _mongo()
    if mongo is not None:
        return mongo.premiums.find_one(
            {"user_id": user_id, "expires_at": {"$gt": now}},
            {"_id": 1},
        ) is not None
    conn = _get()
    row = conn.execute(
        "SELECT 1 FROM premiums WHERE user_id=? AND expires_at>?",
        (user_id, now),
    ).fetchone()
    conn.close()
    return row is not None


def grant_premium(user_id, days, granted_by):
    """Grant premium, extending an existing active term instead of replacing it."""
    user_id, days, granted_by = int(user_id), int(days), int(granted_by)
    if user_id <= 0 or days <= 0 or days > 3650:
        raise ValueError("User ID and days must be positive; days cannot exceed 3650.")

    now = time.time()
    duration = days * 24 * 60 * 60
    mongo = _mongo()
    if mongo is not None:
        current = mongo.premiums.find_one({"user_id": user_id})
        current_expiry = float(current.get("expires_at", 0)) if current else 0
        expires_at = max(now, current_expiry) + duration
        mongo.premiums.update_one(
            {"user_id": user_id},
            {
                "$set": {
                    "user_id": user_id,
                    "granted_by": granted_by,
                    "granted_at": now,
                    "expires_at": expires_at,
                }
            },
            upsert=True,
        )
        return expires_at

    conn = _get()
    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute(
            "SELECT expires_at FROM premiums WHERE user_id=?", (user_id,)
        ).fetchone()
        current_expiry = float(row["expires_at"]) if row else 0
        expires_at = max(now, current_expiry) + duration
        conn.execute(
            """INSERT INTO premiums (user_id, granted_by, granted_at, expires_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(user_id) DO UPDATE SET
                   granted_by=excluded.granted_by,
                   granted_at=excluded.granted_at,
                   expires_at=excluded.expires_at""",
            (user_id, granted_by, now, expires_at),
        )
        conn.commit()
        return expires_at
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def remove_premium(user_id):
    """Revoke a user's premium record; return whether a record was removed."""
    user_id = int(user_id)
    mongo = _mongo()
    if mongo is not None:
        return mongo.premiums.delete_one({"user_id": user_id}).deleted_count > 0
    conn = _get()
    cursor = conn.execute("DELETE FROM premiums WHERE user_id=?", (user_id,))
    conn.commit()
    removed = cursor.rowcount > 0
    conn.close()
    return removed


def list_premiums(active_only=True):
    """List premium grants, excluding expired records by default."""
    now = time.time()
    mongo = _mongo()
    if mongo is not None:
        query = {"expires_at": {"$gt": now}} if active_only else {}
        return list(
            mongo.premiums.find(query, {"_id": 0}).sort("expires_at", 1)
        )
    conn = _get()
    if active_only:
        rows = conn.execute(
            """SELECT user_id, granted_by, granted_at, expires_at
               FROM premiums WHERE expires_at>? ORDER BY expires_at""",
            (now,),
        ).fetchall()
    else:
        rows = conn.execute(
            """SELECT user_id, granted_by, granted_at, expires_at
               FROM premiums ORDER BY expires_at"""
        ).fetchall()
    conn.close()
    return rows


def add_tracked(
    anime_name, season_page_url, multiquality_url, language, last_episode,
    last_season, channel_id, owner_id=None, baseline_episodes=None,
):
    baseline_episodes = [
        str(item) for item in (baseline_episodes or [])
    ]
    mongo = _mongo()
    if mongo is not None:
        mongo.tracked_anime.update_one(
            {"anime_name": anime_name, "language": language},
            {
                "$set": {
                    "anime_name": anime_name,
                    "season_page_url": season_page_url,
                    "multiquality_url": multiquality_url,
                    "language": language,
                    "last_episode": last_episode,
                    "last_season": last_season,
                    "status": "ongoing",
                    "channel_id": channel_id,
                    "baseline_episodes": baseline_episodes,
                    **({"owner_id": int(owner_id)} if owner_id is not None else {}),
                },
                "$setOnInsert": {"created_at": time.strftime("%Y-%m-%d %H:%M:%S")},
            },
            upsert=True,
        )
        return
    conn = _get()
    conn.execute(
        """INSERT OR REPLACE INTO tracked_anime
           (anime_name, season_page_url, multiquality_url, language, last_episode,
            last_season, status, channel_id, owner_id, baseline_episodes)
           VALUES (?, ?, ?, ?, ?, ?, 'ongoing', ?, ?, ?)""",
        (
            anime_name, season_page_url, multiquality_url, language, last_episode,
            last_season, channel_id, int(owner_id) if owner_id is not None else None,
            json.dumps(baseline_episodes),
        ),
    )
    conn.commit()
    conn.close()


def remove_tracked(anime_name, language=None):
    mongo = _mongo()
    if mongo is not None:
        query = {"anime_name": anime_name}
        if language:
            query["language"] = language
        mongo.tracked_anime.delete_many(query)
        return
    conn = _get()
    if language:
        conn.execute(
            "DELETE FROM tracked_anime WHERE anime_name=? AND language=?",
            (anime_name, language),
        )
    else:
        conn.execute("DELETE FROM tracked_anime WHERE anime_name=?", (anime_name,))
    conn.commit()
    conn.close()


def update_tracked_episode(anime_name, language, episode, season=None):
    mongo = _mongo()
    if mongo is not None:
        update = {"last_episode": episode}
        if season is not None:
            update["last_season"] = season
        mongo.tracked_anime.update_one(
            {"anime_name": anime_name, "language": language}, {"$set": update}
        )
        return
    conn = _get()
    conn.execute(
        "UPDATE tracked_anime SET last_episode=? WHERE anime_name=? AND language=?",
        (episode, anime_name, language),
    )
    if season is not None:
        conn.execute(
            "UPDATE tracked_anime SET last_season=? WHERE anime_name=? AND language=?",
            (season, anime_name, language),
        )
    conn.commit()
    conn.close()


def list_tracked():
    mongo = _mongo()
    if mongo is not None:
        return list(mongo.tracked_anime.find({}, {"_id": 0}).sort("created_at", 1))
    conn = _get()
    rows = conn.execute(
        "SELECT * FROM tracked_anime ORDER BY created_at"
    ).fetchall()
    conn.close()
    return rows


def is_episode_uploaded(
    anime_name, season_num, episode_num, language, quality, channel_id=None
):
    expected_quality = normalize_quality_label(quality)
    mongo = _mongo()
    if mongo is not None:
        query = {
            "anime_name": anime_name,
            "season_num": int(season_num),
            "episode_num": int(episode_num),
            "language": language,
        }
        if channel_id is not None:
            query["channel_id"] = str(channel_id)
        records = mongo.uploaded_episodes.find(query, {"quality": 1, "_id": 0})
        return any(
            normalize_quality_label(row.get("quality")) == expected_quality
            for row in records
        )
    conn = _get()
    query = """SELECT 1 FROM uploaded_episodes
               WHERE anime_name=? AND season_num=? AND episode_num=?
               AND language=?"""
    params = [anime_name, season_num, episode_num, language]
    query = query.replace("SELECT 1", "SELECT quality")
    if channel_id is not None:
        query += " AND channel_id=?"
        params.append(str(channel_id))
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return any(
        normalize_quality_label(row["quality"]) == expected_quality
        for row in rows
    )


def normalize_quality_label(value):
    """Normalize source quality labels so suffixes/casing cannot defeat deduping."""
    match = re.search(
        r"(?<!\d)(360|480|720|1080|1440|2160)\s*P?\b",
        str(value or ""),
        re.IGNORECASE,
    )
    return f"{match.group(1)}P" if match else str(value or "").strip().upper()


def _upload_identity(anime_name, season_num, episode_num, language, quality, channel_id):
    return {
        "anime_name": anime_name,
        "season_num": int(season_num),
        "episode_num": int(episode_num),
        "language": language,
        "quality": normalize_quality_label(quality),
        "channel_id": str(channel_id),
    }


def claim_episode_upload(
    anime_name, season_num, episode_num, language, quality, channel_id,
    lease_seconds=21600,
):
    """Atomically reserve one channel/episode/quality while it is being uploaded."""
    identity = _upload_identity(
        anime_name, season_num, episode_num, language, quality, channel_id
    )
    if is_episode_uploaded(
        anime_name, season_num, episode_num, language, quality, channel_id
    ):
        return False

    now = time.time()
    mongo = _mongo()
    if mongo is not None:
        from pymongo.errors import DuplicateKeyError

        try:
            mongo.upload_claims.update_one(
                {
                    **identity,
                    "$or": [
                        {"expires_at": {"$lte": now}},
                        {"expires_at": {"$exists": False}},
                    ],
                },
                {
                    "$set": {
                        **identity,
                        "claimed_at": now,
                        "expires_at": now + lease_seconds,
                    }
                },
                upsert=True,
            )
            return True
        except DuplicateKeyError:
            return False

    conn = _get()
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("DELETE FROM upload_claims WHERE expires_at<=?", (now,))
        cursor = conn.execute(
            """INSERT OR IGNORE INTO upload_claims
               (anime_name, season_num, episode_num, language, quality,
                channel_id, claimed_at, expires_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                identity["anime_name"], identity["season_num"],
                identity["episode_num"], identity["language"],
                identity["quality"], identity["channel_id"],
                now, now + lease_seconds,
            ),
        )
        conn.commit()
        return cursor.rowcount == 1
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def release_episode_claim(
    anime_name, season_num, episode_num, language, quality, channel_id
):
    """Release a failed/cancelled in-flight upload so it can be retried."""
    identity = _upload_identity(
        anime_name, season_num, episode_num, language, quality, channel_id
    )
    mongo = _mongo()
    if mongo is not None:
        mongo.upload_claims.delete_one(identity)
        return
    conn = _get()
    conn.execute(
        """DELETE FROM upload_claims
           WHERE anime_name=? AND season_num=? AND episode_num=?
             AND language=? AND quality=? AND channel_id=?""",
        (
            identity["anime_name"], identity["season_num"],
            identity["episode_num"], identity["language"],
            identity["quality"], identity["channel_id"],
        ),
    )
    conn.commit()
    conn.close()


def record_upload(anime_name, season_num, episode_num, language, quality, file_hash, message_id, channel_id):
    quality = normalize_quality_label(quality)
    mongo = _mongo()
    channel_id = str(channel_id)
    if mongo is not None:
        mongo.uploaded_episodes.update_one(
            {
                "anime_name": anime_name,
                "season_num": int(season_num),
                "episode_num": int(episode_num),
                "language": language,
                "quality": quality,
                "channel_id": channel_id,
            },
            {
                "$set": {
                    "anime_name": anime_name,
                    "season_num": int(season_num),
                    "episode_num": int(episode_num),
                    "language": language,
                    "quality": quality,
                    "file_hash": file_hash,
                    "message_id": int(message_id),
                    "channel_id": channel_id,
                    "uploaded_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                }
            },
            upsert=True,
        )
        mongo.upload_claims.delete_one(
            _upload_identity(
                anime_name, season_num, episode_num, language, quality, channel_id
            )
        )
        return
    conn = _get()
    conn.execute(
        """INSERT OR IGNORE INTO uploaded_episodes
           (anime_name, season_num, episode_num, language, quality, file_hash, message_id, channel_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
         (anime_name, season_num, episode_num, language, quality, file_hash, message_id, channel_id),
    )
    conn.execute(
        """DELETE FROM upload_claims
           WHERE anime_name=? AND season_num=? AND episode_num=?
             AND language=? AND quality=? AND channel_id=?""",
        (anime_name, season_num, episode_num, language, quality, channel_id),
    )
    conn.commit()
    conn.close()


def cache_set(key, value, ttl=3600):
    mongo = _mongo()
    if mongo is not None:
        mongo.anime_cache.update_one(
            {"key": key},
            {
                "$set": {
                    "key": key,
                    "value": str(value),
                    "expires_at": time.time() + ttl,
                }
            },
            upsert=True,
        )
        return
    conn = _get()
    expires = time.time() + ttl
    conn.execute(
        "INSERT OR REPLACE INTO anime_cache (key, value, expires_at) VALUES (?, ?, ?)",
        (key, str(value), expires),
    )
    conn.commit()
    conn.close()


def cache_get(key):
    mongo = _mongo()
    if mongo is not None:
        row = mongo.anime_cache.find_one({"key": key})
        if row and row.get("expires_at", 0) > time.time():
            return row.get("value")
        if row:
            mongo.anime_cache.delete_one({"_id": row["_id"]})
        return None
    conn = _get()
    row = conn.execute("SELECT value, expires_at FROM anime_cache WHERE key=?", (key,)).fetchone()
    conn.close()
    if row and row["expires_at"] > time.time():
        return row["value"]
    return None


def cache_delete(key):
    mongo = _mongo()
    if mongo is not None:
        mongo.anime_cache.delete_one({"key": key})
        return
    conn = _get()
    conn.execute("DELETE FROM anime_cache WHERE key=?", (key,))
    conn.close()
