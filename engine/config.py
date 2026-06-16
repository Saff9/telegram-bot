import os
from dotenv import load_dotenv

load_dotenv()

# --- Configuration & Constants ---
API_ID = os.getenv("API_ID")
if API_ID:
    API_ID = API_ID.strip().strip('"').strip("'")
    if API_ID.isdigit():
        API_ID = int(API_ID)

API_HASH = os.getenv("API_HASH")
if API_HASH:
    API_HASH = API_HASH.strip().strip('"').strip("'")

BOT_TOKEN = os.getenv("BOT_TOKEN")
if BOT_TOKEN:
    BOT_TOKEN = BOT_TOKEN.strip().strip('"').strip("'")

CHANNEL_ID = os.getenv("CHANNEL_ID")
if CHANNEL_ID:
    CHANNEL_ID = CHANNEL_ID.strip().strip('"').strip("'")
    if CHANNEL_ID.startswith("-") and CHANNEL_ID[1:].isdigit():
        CHANNEL_ID = int(CHANNEL_ID)
    elif CHANNEL_ID.isdigit():
        CHANNEL_ID = int(CHANNEL_ID)

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
DOWNLOAD_DIR = "downloads"
DB_PATH = "bot_data.db"
MAX_CONCURRENT_TASKS = int(os.getenv("MAX_CONCURRENT_TASKS", "2" if os.getenv("PORT") else "5"))
MIN_DISK_GB = 2

