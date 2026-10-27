import os
from pathlib import Path

BOT_TOKEN = os.environ["BOT_TOKEN"]
OWNER_ID = int(os.environ["OWNER_ID"])
FP_CHROMIUM = os.environ["FP_CHROMIUM"]
FP_HEADLESS = os.environ.get("FP_HEADLESS") == "1"
TELEGRAM_API_URL = os.environ.get("TELEGRAM_API_URL")
DATA_DIR = Path(os.environ.get("DATA_DIR") or Path.cwd() / "data")

DB_PATH = DATA_DIR / "data.db"
LOGINS_DIR = DATA_DIR / "logins"
