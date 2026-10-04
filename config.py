#This Bot Is Created By Shivam, Thanks To Shivam For Providing Repo
import os
from dotenv import load_dotenv

# Meow Gop GOp GOp
# imported local .env file.
load_dotenv(override=False)

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
DOWNLOAD_DIR = os.getenv("DOWNLOAD_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "downloads"))
DATABASE_PATH = os.getenv("DATABASE_PATH", os.path.join(os.path.dirname(os.path.abspath(__file__)), "bot.db"))
MONGODB_URI = os.getenv("MONGODB_URI", "").strip()
MONGO_DB_NAME = os.getenv("MONGO_DB_NAME", "Raretoon1").strip() or "Raretoon1"

BASE_URL = "https://www.rareanimes.mov"
CODEDEW_BASE = "https://codedew.com"

OWNER_ID = int(os.getenv("OWNER_ID", "0"))

DEFAULT_QUALITY = os.getenv("DEFAULT_QUALITY", "720P")
MAX_FILESIZE = int(os.getenv("MAX_FILESIZE_MB", "2000")) * 1024 * 1024

START_IMAGES = [
    "https://4kwallpapers.com/images/walls/thumbs_3t/15718.jpg",
    "https://4kwallpapers.com/images/wallpapers/firefly-honkai-star--21360.jpg",
    "https://4kwallpapers.com/images/walls/thumbs_3t/16213.jpg",
]

os.makedirs(DOWNLOAD_DIR, exist_ok=True)
