import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("STS_DISCORD_TOKEN") or os.getenv("DISCORD_TOKEN")
BUNGIE_API_KEY = os.getenv("BUNGIE_API_KEY")
BUNGIE_CLAN_ID = os.getenv("STS_BUNGIE_CLAN_ID") or os.getenv("BUNGIE_CLAN_ID")

BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_FILE = BASE_DIR / "sts_server_config.json"
DEFAULT_DUNGEON_THUMBNAIL_URL = os.getenv("STS_DUNGEON_THUMBNAIL_URL", "").strip()

SOLO_CHECK_INTERVAL_MINUTES = int(os.getenv("STS_SOLO_CHECK_INTERVAL_MINUTES", "5"))
SOLO_POST_DELAY_MINUTES = max(0, int(os.getenv("STS_SOLO_POST_DELAY_MINUTES", "3")))
DUNGEON_HISTORY_COUNT = int(os.getenv("STS_DUNGEON_HISTORY_COUNT", "50"))
SOLO_HISTORY_MAX_PAGES = max(1, int(os.getenv("STS_SOLO_HISTORY_MAX_PAGES", "3")))
INITIAL_BASELINE_POST_WINDOW_SECONDS = int(
    os.getenv("STS_INITIAL_BASELINE_POST_WINDOW_SECONDS", str(12 * 60 * 60))
)

# Keep dungeon mode plus additional modes used by exotic/pinnacle solo activities.
SOLO_ACTIVITY_MODES = [82, 84, 63, 88, 7, 0]

# Restrict this bot to Seal Team Six guild(s) only.
ALLOWED_GUILD_IDS = {
    "738885128385855498",
}


def is_allowed_guild(guild_id: int | str | None) -> bool:
    return str(guild_id or "") in ALLOWED_GUILD_IDS
