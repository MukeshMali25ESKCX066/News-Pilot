import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "data" / "newspilot.db"
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

APP_NAME = os.getenv("APP_NAME", "NewsPilot AI")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")
WORDPRESS_SITE_URL = os.getenv("WORDPRESS_SITE_URL", "").rstrip("/")
WORDPRESS_CONNECTOR_TOKEN = os.getenv("WORDPRESS_CONNECTOR_TOKEN", "")
SCHEDULER_INTERVAL_MINUTES = int(os.getenv("SCHEDULER_INTERVAL_MINUTES", "5"))
