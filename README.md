# Raretoon

## Run the Telegram bot

The project runs as the `Raretoon Telegram Bot` workflow with:

```bash
python bot.py
```

Environment settings:

- `BOT_TOKEN` — required Telegram bot token stored as a Replit Secret.
- `OWNER_ID` — required numeric Telegram user ID for the bot owner.
- `TELEGRAM_API_ID` and `TELEGRAM_API_HASH` — optional Telegram application credentials
  required by WZGram for direct MTProto video uploads. This avoids the Bot API's
  50 MB upload limit and reduces upload overhead. Without them, the bot falls
  back to the Bot API; unresolved private destinations remain limited to 50 MB.
- `MONGODB_URI` — MongoDB connection string stored as a Replit Secret. When set,
  the bot uses database `Rareteen` for configuration, tracking, uploads, and cache.

Without `MONGODB_URI`, the imported SQLite database remains available as a local
fallback; set the secret before deployment to keep all persistent data in MongoDB.
The bot writes runtime logs to `bot.log`. The polling process must have only one
active instance for the configured bot token. Never commit a local `.env` file.

Inline buttons use Telegram's semantic button style values through the existing
python-telegram-bot transport. Completed-anime uploads first show paginated episode
buttons and support `Download Selected` or `All Episodes`. Ongoing backfill also
lets users choose specific previous episodes before tracking starts. Active
transfers show a task ID in the progress message and can be stopped with
`/cancel <task_id>`. Episode upload history is kept per destination channel, and
tracked releases are checked every 30 minutes. Automatic checks wait one interval
after startup, so a fresh bot restart does not immediately restart previously
queued tracking uploads.
The separate `/track` flow also uploads 360P, 720P, and 1080P when those files
are available. Scheduled scans and **Check Tracked Channels Now** share the
per-quality upload history, so later scans skip qualities already sent.

## Ongoing anime auto-upload

Run `/autoupload` as the owner or an admin. Choose **Set Ongoing Anime Channel**,
send an existing channel ID where the bot is an administrator, and then type the
anime name to search the website. Select a result, choose **Ongoing** or
**Complete**, and select individual Hindi-dub episodes or use the backfill
options. Ongoing titles can either upload the listed episodes before tracking
new releases or skip the current list and upload new releases only.

The bot asks for a channel thumbnail only when one has not already been stored
for that channel. It saves the image in the configured database and uses it for
episode uploads. Download and upload progress, tracking checks, and completion
statuses are sent to the owner's Telegram DM. Ongoing channels are checked every
30 minutes. This flow uses the bot account only: it does not inspect channel
history, create channels, or require a Telegram user session.

`/autoupload` has separate settings for filename metadata and episode captions,
the thumbnail archive channel, and the optional main announcement channel.
Auto-upload always uploads 360P, 720P, and 1080P; this set cannot be changed.
If the site publishes those qualities at different times, available files upload
first and the bot refreshes the episode page every 60 seconds until the rest are
posted. Use the owner-DM Cancel button to stop a task while it is waiting.
Use `/authupload` to configure the main-channel announcement caption and channel.
Choosing the default caption restores the sample-style layout and prompts once
for a separate main-post image for each tracked anime that does not have one.
These images are stored independently from video thumbnails and reused for later
posts. Owner-DM transfer progress includes a Cancel button; cancellation stops
the current transfer and leaves its episode eligible for a later retry.
Auto-upload settings and upload history are separate from `/upload`, `/track`,
`/quality`, and the shared upload channel. Removing a channel from auto-upload
does not delete its Telegram channel or messages.

## Personal upload settings

- `/setmetadata @your_tag` saves a filename prefix for your own uploads. The bot
  fills in the anime, season, episode, and quality automatically; for example,
  `@shivam_anime` produces `@shivam_animes_Solo_Leveling_S01_EP01_480p.mp4`.
  `/remmetadata` restores the normal filename.
- Reply to a photo with `/setthumb` to use it as your thumbnail. The image is
  resized and saved for your account only. `/remthumb` removes it; without a
  saved thumbnail, Telegram generates the video preview.
- If a download page has not published a supported quality yet, the active
  upload refreshes the page every 60 seconds until it is ready. Use `/cancel` to
  stop the wait.

## Ubuntu VPS notes

For Ubuntu 22.04, install Python 3.11 or newer and FFmpeg (`sudo apt install
ffmpeg`) before starting the bot. `ffprobe` attaches real duration and dimensions
to Telegram videos. Install the packages in `requirements.txt`, set environment
values using protected systemd credentials or a private environment file, and run
only one bot process per token. The episode scanner uploads sequentially to avoid
overlapping transfers on a 4-core VPS.
