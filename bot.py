import os
import json
import re
import unicodedata
import asyncio
import io
from pathlib import Path
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse
import aiohttp
import discord
from discord.ext import commands
from discord.ext import tasks
from discord import app_commands
from dotenv import load_dotenv
from discord import ui, Interaction
from bungie_api import BungieAPI
from guardian_rank import GuardianRankCommands
from raid_roles import RaidRoleManager
from dungeon_team import TeamDungeonTracker
from raid_team import TeamRaidTracker
from pinnacle_ops import (
    SPECIAL_SOLO_ACTIVITY_NAMES,
    activity_name_matches_special_solo_activity,
    normalize_special_solo_activity_display_name,
)


# Use the real discord.py app_commands API directly; do not assume a legacy discord.app namespace.
load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')
BUNGIE_API_KEY = os.getenv('BUNGIE_API_KEY')
BUNGIE_CLAN_ID = os.getenv('BUNGIE_CLAN_ID')
BASE_DIR = Path(__file__).resolve().parent
CONFIG_FILE = BASE_DIR / "server_config.json"
LEGACY_CONFIG_FILE = BASE_DIR / "rules_config.json"

intents = discord.Intents.default()
intents.members = True
intents.message_content = True
bot = commands.Bot(command_prefix="/", intents=intents)

async def main():
    await bot.load_extension("GuardianRankCommands")
    await bot.start(TOKEN)

bungie_api = None
SOLO_CHECK_INTERVAL_MINUTES = int(os.getenv("SOLO_CHECK_INTERVAL_MINUTES", "5"))
TEAM_ACTIVITY_CHECK_INTERVAL_MINUTES = int(os.getenv("TEAM_ACTIVITY_CHECK_INTERVAL_MINUTES", "5"))
SOLO_POST_DELAY_MINUTES = max(0, int(os.getenv("SOLO_POST_DELAY_MINUTES", "3")))
TEAM_DUNGEON_POST_DELAY_MINUTES = max(0, int(os.getenv("TEAM_DUNGEON_POST_DELAY_MINUTES", "3")))
TEAM_RAID_POST_DELAY_MINUTES = max(0, int(os.getenv("TEAM_RAID_POST_DELAY_MINUTES", "3")))
DUNGEON_HISTORY_COUNT = int(os.getenv("DUNGEON_HISTORY_COUNT", "50"))
SOLO_HISTORY_MAX_PAGES = max(1, int(os.getenv("SOLO_HISTORY_MAX_PAGES", "3")))
RAID_HISTORY_COUNT = int(os.getenv("RAID_HISTORY_COUNT", str(DUNGEON_HISTORY_COUNT)))
DUNGEON_FULL_CLEAR_MIN_SECONDS = 15 * 60
# Keep dungeon mode, plus optional extra modes for special solo content (e.g. Presage).
SOLO_ACTIVITY_MODES = [82, 84, 63, 88, 7, 0]
# Allow a longer historic catch-up window for missed solo clears.
INITIAL_BASELINE_POST_WINDOW_SECONDS = 6 * 60 * 60
SOLO_DEBUG_LOGS = os.getenv("SOLO_DEBUG_LOGS", "0").strip() in {"1", "true", "yes", "on"}
SOLO_POST_CHECKPOINTS = os.getenv("SOLO_POST_CHECKPOINTS", "0").strip().casefold() in {"1", "true", "yes", "on"}
SOLO_MAX_POSTS_PER_CYCLE = max(1, int(os.getenv("SOLO_MAX_POSTS_PER_CYCLE", "3")))
BOT_NICKNAME = os.getenv("BOT_NICKNAME", "Wheelie Bin").strip()
BOT_USERNAME = os.getenv("BOT_USERNAME", "Wheelie Bin").strip()
startup_solo_scan_done = False
startup_solo_scan_task = None


def solo_debug(message: str):
    if SOLO_DEBUG_LOGS:
        print(f"[SOLO_DEBUG] {message}")


def format_duration(seconds_value):
    try:
        total = int(seconds_value)
    except (TypeError, ValueError):
        return None
    if total < 0:
        return None
    minutes, seconds = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours:d}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:d}:{seconds:02d}"


def format_int_stat(value):
    try:
        return str(int(value))
    except (TypeError, ValueError):
        return None


def should_hide_status_for_short_exotic_run(activity_name: str | None, duration_seconds) -> bool:
    name_text = str(activity_name or "").casefold().strip()
    if not name_text.startswith("exotic mission -"):
        return False
    try:
        duration_value = int(duration_seconds)
    except (TypeError, ValueError):
        return False
    return duration_value < 10 * 60


def load_rules_config():
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text())
        except Exception as e:
            print(f"Failed to load server config: {e}")

    if LEGACY_CONFIG_FILE.exists():
        try:
            legacy_data = json.loads(LEGACY_CONFIG_FILE.read_text())
            save_rules_config(legacy_data)
            print("Migrated legacy rules_config.json to server_config.json")
            return legacy_data
        except Exception as e:
            print(f"Failed to migrate legacy config: {e}")

    return {}


def save_rules_config(config):
    try:
        CONFIG_FILE.write_text(json.dumps(config, indent=2))
    except Exception as e:
        print(f"Failed to save server config: {e}")


rules_config = load_rules_config()
raid_role_manager = RaidRoleManager(bot, rules_config, save_rules_config)


RULES_TEXT = "Please read the Rules and tap here to show you have agreed to them"
RULES_EMOJI = "📜"


WELCOME_GUILD_OVERRIDES = {
    "1546957591068934294": {
        "welcome_title": "Welcome to Seal Team Six Gaming™",
        "welcome_message": (
            "Welcome, {member}, to Seal Team Six Gaming™.\n"
            "Expect chaos, cracked plays, and questionable life choices. Grab a drink, squad up, and dive in."
        ),
        "welcome_bottom_logo_url": "https://i.postimg.cc/bwnpKqhr/Seal-Team-Six-Gaming.jpg",
    },
    "738885128385855498": {
        "welcome_title": "Welcome to Seal Team Six Gaming™",
        "welcome_message": (
            "Welcome, {member}, to Seal Team Six Gaming™.\n"
            "Expect chaos, cracked plays, and questionable life choices. Grab a drink, squad up, and dive in."
        ),
        "welcome_bottom_logo_url": "https://i.postimg.cc/bwnpKqhr/Seal-Team-Six-Gaming.jpg",
    },
}

SEAL_TEAM_SIX_MAIN_GUILD_ID = "738885128385855498"
SEAL_TEAM_SIX_TEST_GUILD_ID = "1546957591068934294"
GOLDEN_OLDIES_GUILD_ID = "1334068759656398899"
SEAL_TEAM_SIX_GUILD_IDS = {
    SEAL_TEAM_SIX_MAIN_GUILD_ID,
    SEAL_TEAM_SIX_TEST_GUILD_ID,
    GOLDEN_OLDIES_GUILD_ID,
}
SHARED_WELCOME_FIELD_KEYS = (
    "welcome_title",
    "welcome_message",
    "welcome_image",
    "welcome_bottom_logo_url",
    "welcome_footer_logo_url",
)

SEAL_TEAM_SIX_TEST_WELCOME_CHANNEL_ID = "1546958740689911938"


def get_effective_welcome_settings(
    guild_id: int | None,
    guild_config,
    guild_name: str | None = None,
    current_channel_id: int | None = None,
):
    guild_id_str = str(guild_id or "")
    effective_config = dict(guild_config or {})

    # Keep Seal Team Six test server in sync with the main Seal Team Six setup.
    if guild_id_str == SEAL_TEAM_SIX_TEST_GUILD_ID:
        source_config = rules_config.get(SEAL_TEAM_SIX_MAIN_GUILD_ID, {})
        if isinstance(source_config, dict):
            for key in SHARED_WELCOME_FIELD_KEYS:
                value = source_config.get(key)
                if value:
                    effective_config[key] = value

    override = WELCOME_GUILD_OVERRIDES.get(guild_id_str)
    if override is None and guild_name:
        normalized_name = str(guild_name).casefold()
        if (
            "seal team six" in normalized_name
            or "team six" in normalized_name
            or "team 6" in normalized_name
        ):
            override = WELCOME_GUILD_OVERRIDES.get("1546957591068934294")
    if override is None:
        configured_welcome_channel_id = str(effective_config.get("welcome_channel_id") or "").strip()
        if configured_welcome_channel_id == SEAL_TEAM_SIX_TEST_WELCOME_CHANNEL_ID:
            override = WELCOME_GUILD_OVERRIDES.get("1546957591068934294")
    if override is None and current_channel_id is not None:
        if str(current_channel_id) == SEAL_TEAM_SIX_TEST_WELCOME_CHANNEL_ID:
            override = WELCOME_GUILD_OVERRIDES.get("1546957591068934294")
    if override:
        # Override map is fallback only; explicit config values stay authoritative.
        for key, value in override.items():
            effective_config.setdefault(key, value)

    return effective_config


async def build_embed_image_attachment(image_url: str | None, fallback_name: str = "welcome-image"):
    if not image_url:
        return None, None

    parsed = urlparse(str(image_url))
    extension = Path(parsed.path).suffix or ".png"
    filename = f"{fallback_name}{extension}"

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(str(image_url)) as response:
                if response.status != 200:
                    return None, None
                data = await response.read()
    except Exception:
        return None, None

    return discord.File(io.BytesIO(data), filename=filename), filename


def is_seal_team_six_welcome_context(
    guild_id: int | None,
    guild_name: str | None,
    guild_config,
    current_channel_id: int | None = None,
):
    guild_id_str = str(guild_id or "")
    if guild_id_str in {SEAL_TEAM_SIX_MAIN_GUILD_ID, SEAL_TEAM_SIX_TEST_GUILD_ID}:
        return True

    normalized_name = str(guild_name or "").casefold()
    if (
        "seal team six" in normalized_name
        or "team six" in normalized_name
        or "team 6" in normalized_name
    ):
        return True

    configured_welcome_channel_id = str((guild_config or {}).get("welcome_channel_id") or "").strip()
    if configured_welcome_channel_id == SEAL_TEAM_SIX_TEST_WELCOME_CHANNEL_ID:
        return True

    if current_channel_id is not None and str(current_channel_id) == SEAL_TEAM_SIX_TEST_WELCOME_CHANNEL_ID:
        return True

    return False


DUNGEON_NAMES = {
    # Multiple activity hashes can map to the same dungeon title
    # (normal/master variants, rotations, or backend activity versions).
    # Repeated names here are intentional for canonical naming.
    # Shattered Throne
    1375089621: "Shattered Throne",
    2032534090: "Shattered Throne",

    # Pit of Heresy
    1042180643: "Pit of Heresy",
    
    2582501063: "Pit of Heresy",

    # Prophecy
    2296816009: "Prophecy",
    3637651331: "Prophecy",
    1077850348: "Prophecy",

    # Grasp of Avarice
    4078656646: "Grasp of Avarice",

    # Duality
    3132816111: "Duality",
    3635748805: "Duality",  # Normal Mode
    1826478593: "Duality",  # Master Mode
    -659218491: "Duality",  # Signed 32-bit Normal Mode
    
    # Spire of the Watcher
    422102671: "Spire of the Watcher",
    1225969316: "Spire of the Watcher",
    1262462921: "Spire of the Watcher",

    # Ghosts of the Deep
    1451777341: "Ghosts of the Deep",
    1094262727: "Ghosts of the Deep",
    313828469: "Ghosts of the Deep",

    # Warlord's Ruin
    3732368189: "Warlord's Ruin",
    2004855007: "Warlord's Ruin",
    -562599107: "Warlord's Ruin",  # FIXED: Signed 32-bit variation for 3732368189

    # Vesper's Host
    3138284691: "Vesper's Host",
    300092127: "Vesper's Host",
    -1156682605: "Vesper's Host", # FIXED: Signed 32-bit variation for 313828469

    # Sundered Doctrine
    247869137: "Sundered Doctrine",
}


# Automatic default thresholds for classifying full clears vs checkpoint clears.
# These are intentionally conservative so fast legit clears are less likely to
# be labeled as checkpoint clears.
DUNGEON_FULL_CLEAR_MIN_SECONDS_BY_NAME = {
    "Shattered Throne": 600,
    "Pit of Heresy": 600,
    "Prophecy": 540,
    "Grasp of Avarice": 660,
    "Duality": 660,
    "Spire of the Watcher": 660,
    "Ghosts of the Deep": 780,
    "Warlord's Ruin": 660,
    "Vesper's Host": 660,
    "Sundered Doctrine": 660,
    "Presage": 900,
}

def get_dungeon_name(reference_id, guild_config=None):
    custom_names = (guild_config or {}).get("custom_dungeon_names", {})
    ref_str = str(reference_id) if reference_id is not None else ""
    if ref_str and ref_str in custom_names:
        return custom_names[ref_str]

    try:
        activity_hash = int(reference_id)
    except (TypeError, ValueError):
        return "Dungeon"
    if activity_hash in SPECIAL_SOLO_ACTIVITY_NAMES:
        return normalize_special_solo_activity_display_name(SPECIAL_SOLO_ACTIVITY_NAMES[activity_hash])
    return DUNGEON_NAMES.get(activity_hash)


def get_full_clear_min_seconds(activity_hash, guild_config=None, dungeon_name=None):
    guild_config = guild_config or {}

    # Allow one global guild override if set.
    global_override = guild_config.get("dungeon_full_clear_min_seconds")
    if global_override is not None:
        try:
            return int(global_override)
        except (TypeError, ValueError):
            pass

    # Allow per-dungeon guild overrides by name if provided.
    per_dungeon_overrides = guild_config.get("dungeon_full_clear_min_seconds_by_name", {})
    if dungeon_name and dungeon_name in per_dungeon_overrides:
        try:
            return int(per_dungeon_overrides[dungeon_name])
        except (TypeError, ValueError):
            pass

    if not dungeon_name:
        dungeon_name = get_dungeon_name(activity_hash, guild_config=guild_config)

    if dungeon_name in DUNGEON_FULL_CLEAR_MIN_SECONDS_BY_NAME:
        return DUNGEON_FULL_CLEAR_MIN_SECONDS_BY_NAME[dungeon_name]

    return DUNGEON_FULL_CLEAR_MIN_SECONDS


async def resolve_activity_name(reference_id, guild_config=None):
    known_name = get_dungeon_name(reference_id, guild_config=guild_config)
    if known_name:
        return known_name

    try:
        activity_hash = int(reference_id)
    except (TypeError, ValueError):
        return "Dungeon"

    if bungie_api is None:
        return f"Dungeon ({activity_hash})"

    try:
        definition = await bungie_api.get_activity_definition(activity_hash)
    except Exception:
        return f"Dungeon ({activity_hash})"

    display_name = (definition.get("displayProperties") or {}).get("name")
    if display_name:
        if activity_name_matches_special_solo_activity(display_name):
            return normalize_special_solo_activity_display_name(display_name)
        return display_name
    return f"Dungeon ({activity_hash})"


CLASS_NAMES = {
    0: "Titan",
    1: "Hunter",
    2: "Warlock",
}


def get_class_name(class_type):
    try:
        return CLASS_NAMES.get(int(class_type))
    except (TypeError, ValueError):
        return None


def get_character_info_from_pgcr_entry(entry):
    player = entry.get("player", {}) or {}
    class_name = get_class_name(player.get("characterClass") or entry.get("characterClass"))

    subclass_name = None
    for key in (
        "characterSubclassName",
        "subclassName",
        "subclass",
        "characterSubclass",
        "characterSubclassHash",
        "subclassHash",
    ):
        value = player.get(key) or entry.get(key)
        if value is not None:
            candidate = str(value).strip()
            if candidate:
                subclass_name = candidate
                break

    return class_name, subclass_name


def get_guild_config(guild_id: int):
    guild_key = str(guild_id)
    guild_config = rules_config.get(guild_key, {})
    rules_config[guild_key] = guild_config
    return guild_config


def prune_seen_instances(guild_config):
    seen = guild_config.get("dungeon_seen_instance_ids", [])
    if len(seen) > 500:
        guild_config["dungeon_seen_instance_ids"] = seen[-500:]


def parse_bungie_timestamp(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def normalize_member_name(name: str):
    if not name:
        return None
    normalized = unicodedata.normalize("NFKC", str(name)).strip()
    if not normalized:
        return None
    return normalized.casefold()


def normalize_bungie_name_input(name: str):
    if not name:
        return ""

    normalized = unicodedata.normalize("NFKC", str(name)).strip()
    if not normalized:
        return ""

    # Strip Discord mention wrappers and common punctuation users paste with names.
    normalized = normalized.replace("<", "").replace(">", "")
    normalized = normalized.strip("`'\" ")
    normalized = normalized.strip(",.;:")

    # Remove leading clan tags like [GOLD] WH33LS#8650
    if normalized.startswith("[") and "]" in normalized:
        normalized = normalized.split("]", 1)[1].strip()

    if "#" in normalized:
        base, code = normalized.split("#", 1)
        base = base.strip().strip(",.;:")
        code = code.strip().split()[0].strip(",.;:")
        if base and code:
            return f"{base}#{code}"
        return base or normalized

    return normalized


def _bungie_name_variants(name: str):
    cleaned = normalize_bungie_name_input(name)
    if not cleaned:
        return "", ""
    base = cleaned.split("#", 1)[0].strip()
    return cleaned.casefold(), base.casefold()


def _bungie_name_key(name: str):
    _, base = _bungie_name_variants(name)
    if not base:
        return ""
    return "".join(ch for ch in base if ch.isalnum())


def bungie_name_matches(input_name: str, candidate_name: str):
    input_full, input_base = _bungie_name_variants(input_name)
    cand_full, cand_base = _bungie_name_variants(candidate_name)
    if not input_full or not cand_full:
        return False
    if input_full == cand_full:
        return True
    return bool(input_base and cand_base and input_base == cand_base)


def pick_best_roster_match(input_name: str, roster_players: list[dict]):
    _, input_base = _bungie_name_variants(input_name)
    input_key = _bungie_name_key(input_name)
    if not input_base:
        return None

    exact = []
    starts = []
    contains = []

    for player in roster_players:
        candidate_name = (
            player.get("global_name")
            or player.get("displayName")
            or ""
        )
        if not candidate_name:
            continue

        _, candidate_base = _bungie_name_variants(candidate_name)
        candidate_key = _bungie_name_key(candidate_name)
        if not candidate_base:
            continue

        if candidate_base == input_base or (input_key and candidate_key and candidate_key == input_key):
            exact.append(player)
        elif candidate_base.startswith(input_base) or (input_key and candidate_key and candidate_key.startswith(input_key)):
            starts.append(player)
        elif input_base in candidate_base or (input_key and candidate_key and input_key in candidate_key):
            contains.append(player)

    if exact:
        return exact[0]
    if len(starts) == 1:
        return starts[0]
    if len(contains) == 1:
        return contains[0]
    return None


def parse_bungie_profile_input(value: str):
    if not value:
        return None

    text = str(value).strip()
    # Matches URLs like:
    # https://www.bungie.net/7/en/User/Profile/1/4611686018462077131?bgn=WH33LS
    match = re.search(r"/User/Profile/(\d+)/(\d+)", text, flags=re.IGNORECASE)
    if not match:
        return None

    try:
        membership_type = int(match.group(1))
    except (TypeError, ValueError):
        return None

    membership_id = str(match.group(2)).strip()
    if not membership_id:
        return None

    return {
        "membership_type": membership_type,
        "membership_id": membership_id,
    }


def find_member_avatar_url(guild: discord.Guild, player_name: str):
    if guild is None or not player_name:
        return None

    # Bungie names usually include a discriminator (#1234). Discord usernames do not
    # reliably map to those, so avoid using a Discord avatar unless the player_name
    # does not include a discriminator.
    if "#" in player_name:
        return None

    base_name = normalize_member_name(player_name)
    if not base_name:
        return None

    matches = []
    for member in guild.members:
        if normalize_member_name(member.display_name) == base_name:
            matches.append(member)
            continue
        if normalize_member_name(member.name) == base_name:
            matches.append(member)
            continue
        if member.nick and normalize_member_name(member.nick) == base_name:
            matches.append(member)

    if len(matches) == 1:
        return matches[0].display_avatar.url
    return None


def is_member_in_guild(guild: discord.Guild, player_name: str):
    if guild is None or not player_name:
        return False

    base_name = normalize_member_name(player_name.split("#", 1)[0])
    if not base_name:
        return False

    for member in guild.members:
        if normalize_member_name(member.display_name) == base_name:
            return True
        if normalize_member_name(member.name) == base_name:
            return True
        if member.nick and normalize_member_name(member.nick) == base_name:
            return True
    return False


def normalize_bungie_image_url(path_value):
    if not path_value:
        return None
    if path_value.startswith("http://") or path_value.startswith("https://"):
        return path_value
    return f"https://www.bungie.net{path_value}"


def resolve_raid_thumbnail_by_name(raid_name: str, guild_config):
    if not raid_name:
        return None

    raid_map = (guild_config or {}).get("team_raid_thumbnail_by_name") or {}
    normalized_target = str(raid_name).casefold().strip()
    if not normalized_target:
        return None

    for key, value in raid_map.items():
        if not key or not value:
            continue
        if str(key).casefold().strip() == normalized_target:
            return str(value)
    return None


def resolve_dungeon_image_by_name(dungeon_name: str, guild_config):
    if not dungeon_name:
        return None

    def _normalize_lookup_key(value: str):
        text = str(value or "").casefold().strip()
        if not text:
            return ""
        text = re.sub(r"^exotic\s+mission\s*-\s*", "", text)
        text = re.sub(r"\s*:\s*(customize|standard)\b", "", text)
        text = re.sub(r"\s*\((normal|master|legend|expert|customize|standard)\)\s*$", "", text)
        text = re.sub(r"[^a-z0-9]+", "", text)
        return text

    config = guild_config or {}
    # Prefer explicit solo map, then shared dungeon maps, then legacy global image.
    custom_map = (
        config.get("solo_dungeon_image_by_name")
        or config.get("team_dungeon_thumbnail_by_name")
        or config.get("dungeon_thumbnail_by_name")
        or {}
    )
    normalized_target = str(dungeon_name).casefold().strip()
    normalized_target_compact = _normalize_lookup_key(dungeon_name)
    if normalized_target:
        for key, value in custom_map.items():
            if not key or not value:
                continue
            normalized_key = str(key).casefold().strip()
            if normalized_key == normalized_target:
                return str(value)
            if normalized_target_compact and _normalize_lookup_key(normalized_key) == normalized_target_compact:
                return str(value)

    fallback = config.get("dungeon_embed_image")
    return str(fallback) if fallback else None


def resolve_text_channel_from_input(guild: discord.Guild, channel_input: str | None):
    if guild is None or channel_input is None:
        return None

    raw = str(channel_input).strip()
    if not raw:
        return None

    mention_match = re.fullmatch(r"<#(\d+)>", raw)
    if mention_match:
        raw = mention_match.group(1)

    if raw.isdigit():
        resolved = guild.get_channel(int(raw))
        return resolved if isinstance(resolved, discord.TextChannel) else None

    normalized = raw.casefold()
    for text_channel in guild.text_channels:
        if text_channel.name.casefold() == normalized:
            return text_channel

    for text_channel in guild.text_channels:
        if normalized in text_channel.name.casefold():
            return text_channel

    return None


team_dungeon_tracker = TeamDungeonTracker(
    bot=bot,
    base_dir=BASE_DIR,
    rules_config=rules_config,
    save_rules_config=save_rules_config,
    get_bungie_api=lambda: bungie_api,
    get_guild_config=get_guild_config,
    resolve_activity_name=resolve_activity_name,
    parse_bungie_timestamp=parse_bungie_timestamp,
    format_duration=format_duration,
    format_int_stat=format_int_stat,
    debug_log=solo_debug,
    dungeon_history_count=DUNGEON_HISTORY_COUNT,
    initial_baseline_post_window_seconds=INITIAL_BASELINE_POST_WINDOW_SECONDS,
    post_delay_minutes=TEAM_DUNGEON_POST_DELAY_MINUTES,
    min_completions=int(os.getenv("TEAM_DUNGEON_MIN_COMPLETIONS", "2")),
)

team_raid_tracker = TeamRaidTracker(
    bot=bot,
    base_dir=BASE_DIR,
    rules_config=rules_config,
    save_rules_config=save_rules_config,
    get_bungie_api=lambda: bungie_api,
    get_guild_config=get_guild_config,
    resolve_activity_name=resolve_activity_name,
    parse_bungie_timestamp=parse_bungie_timestamp,
    format_duration=format_duration,
    format_int_stat=format_int_stat,
    debug_log=solo_debug,
    raid_history_count=RAID_HISTORY_COUNT,
    initial_baseline_post_window_seconds=INITIAL_BASELINE_POST_WINDOW_SECONDS,
    post_delay_minutes=TEAM_RAID_POST_DELAY_MINUTES,
    min_completions=int(os.getenv("TEAM_RAID_MIN_CLAN_COMPLETIONS", "3")),
    min_total_completions=int(os.getenv("TEAM_RAID_MIN_TOTAL_COMPLETIONS", "3")),
)

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")
    


guardian_rank_commands = GuardianRankCommands(
    bot,
    get_bungie_api=lambda: bungie_api,
    resolve_player_fn=lambda name: resolve_player(name),
    get_guild_config=get_guild_config,
    rules_config=rules_config,
    save_rules_config=save_rules_config,
    clan_id=BUNGIE_CLAN_ID,
    check_interval_minutes=SOLO_CHECK_INTERVAL_MINUTES,
)
guardian_rank_commands.register_commands()




def build_dungeon_embed(
    player_name: str,
    player_avatar: str | None,
    dungeon_name: str,
    is_flawless: bool,
    is_solo: bool,
    was_started_from_beginning: bool,
    guild_config,
    thumbnail_url: str | None = None,
):
    is_exotic_mission = str(dungeon_name or "").casefold().startswith("exotic mission -")
    embed_color = discord.Color.gold() if is_flawless else discord.Color.orange()
    if is_solo and is_flawless:
        description = f"Flawless solo by {player_name}! No deaths, no mercy."
    elif is_solo:
        if is_exotic_mission:
            description = f"{player_name} finished the exotic mission with some scars, but got it done."
        else:
            description = f"{player_name} solo cleared the dungeon with some scars, but got it done."
    else:
        return None  # Don't show embeds for team clears

    embed = discord.Embed(
        title=dungeon_name,
        description=description,
        color=embed_color,
    )
    if player_avatar:
        embed.set_author(name=player_name, icon_url=player_avatar)
    else:
        embed.set_author(name=player_name)

    if thumbnail_url:
        embed.set_thumbnail(url=thumbnail_url)

    return embed



async def resolve_player_from_membership(membership_id, membership_type, display_name="Guardian"):
    if bungie_api is None:
        return None

    profile = await bungie_api.get_profile(membership_type, membership_id)
    characters = profile.get("characters", {}).get("data", {})
    character_ids = list(characters.keys())

    return {
        "global_name": display_name,
        "membership_id": str(membership_id),
        "membership_type": int(membership_type),
        "character_ids": character_ids,
        "avatar_url": None,
    }


async def resolve_player(global_name: str):
    if bungie_api is None:
        return None

    profile_ref = parse_bungie_profile_input(global_name)
    if profile_ref is not None:
        try:
            return await resolve_player_from_membership(
                profile_ref["membership_id"],
                profile_ref["membership_type"],
                display_name="Guardian",
            )
        except Exception:
            # Fall through to name-based resolution if profile lookup fails.
            pass

    cleaned_name = normalize_bungie_name_input(global_name)
    if not cleaned_name:
        return None

    # Prefer clan-roster membership resolution first when available, matching
    # how automated checks resolve and fetch player data.
    if BUNGIE_CLAN_ID:
        try:
            roster = await get_auto_watch_players_for_clan()
            roster_player = pick_best_roster_match(cleaned_name, roster)
            if roster_player:
                roster_name = roster_player.get("global_name") or ""
                membership_id = roster_player.get("membership_id")
                membership_type = roster_player.get("membership_type")
                if membership_id and membership_type is not None:
                    return await resolve_player_from_membership(
                        membership_id,
                        membership_type,
                        display_name=roster_name or cleaned_name,
                    )
        except Exception as e:
            print(f"Clan roster pre-lookup failed for '{cleaned_name}': {e}")

    memberships = await bungie_api.search_global_name(cleaned_name)
    if not memberships:
        # Fallback for clan members when Bungie global-name search misses.
        if BUNGIE_CLAN_ID:
            try:
                roster = await bungie_api.get_clan_roster(BUNGIE_CLAN_ID)
                roster_member = pick_best_roster_match(cleaned_name, roster)
                if roster_member:
                    roster_name = (roster_member.get("displayName") or "").strip()
                    membership_id = roster_member.get("membershipId")
                    membership_type = roster_member.get("membershipType")
                    if membership_id and membership_type is not None:
                        return await resolve_player_from_membership(
                            membership_id,
                            membership_type,
                            display_name=roster_name or cleaned_name,
                        )
            except Exception as e:
                print(f"Clan roster fallback failed for '{cleaned_name}': {e}")
        return None

    fallback_candidate = None
    for membership in memberships:
        membership_id = membership.get("membershipId")
        membership_type = membership.get("membershipType")
        if not membership_id or membership_type is None:
            continue

        display_name = membership.get("bungieGlobalDisplayName") or membership.get("displayName") or cleaned_name
        display_code = membership.get("bungieGlobalDisplayNameCode")
        avatar_url = normalize_bungie_image_url(
            membership.get("iconPath") or membership.get("profilePicturePath")
        )
        if display_code is not None:
            try:
                display_name = f"{display_name}#{int(display_code):04d}"
            except (TypeError, ValueError):
                display_name = f"{display_name}#{display_code}"

        try:
            profile = await bungie_api.get_profile(membership_type, membership_id)
        except Exception:
            continue

        characters = profile.get("characters", {}).get("data", {})
        character_ids = list(characters.keys())
        candidate = {
            "global_name": display_name,
            "membership_id": str(membership_id),
            "membership_type": int(membership_type),
            "character_ids": character_ids,
            "avatar_url": avatar_url,
        }
        if character_ids:
            return candidate
        if fallback_candidate is None:
            fallback_candidate = candidate

    return fallback_candidate


async def detect_new_solo_dungeon_completions(player, seen_instance_ids, min_period_dt=None):
    completions = []
    if bungie_api is None:
        return completions

    membership_id = player.get("membership_id")
    membership_type = player.get("membership_type")
    character_ids = player.get("character_ids", [])
    if not character_ids:
        profile = await bungie_api.get_profile(membership_type, membership_id)
        characters = profile.get("characters", {}).get("data", {})
        character_ids = list(characters.keys())
        player["character_ids"] = character_ids

    activity_name_cache = {}
    scanned_instance_ids = set()

    for character_id in character_ids:
        activity_mode_queries = list(SOLO_ACTIVITY_MODES)
        activity_mode_queries.append(None)

        for mode in activity_mode_queries:
            for page in range(SOLO_HISTORY_MAX_PAGES):
                history = await bungie_api.get_activity_history(
                    membership_type=membership_type,
                    membership_id=membership_id,
                    character_id=character_id,
                    mode=mode,
                    count=DUNGEON_HISTORY_COUNT,
                    page=page,
                )
                activities = history.get("activities", [])
                if not activities:
                    break

                for activity in activities:
                    details = activity.get("activityDetails", {})
                    instance_id = str(details.get("instanceId", ""))
                    if (
                        not instance_id
                        or instance_id in seen_instance_ids
                        or instance_id in scanned_instance_ids
                    ):
                        if instance_id in seen_instance_ids:
                            solo_debug(f"Skipping seen instance {instance_id}")
                        continue

                    scanned_instance_ids.add(instance_id)
                    activity_hash = details.get("referenceId")
                    period = activity.get("period")
                    completion_dt = parse_bungie_timestamp(period)

                    # Skip old history entries before PGCR calls to keep large
                    # clan scans fast enough to finish each cycle.
                    if min_period_dt is not None and completion_dt is not None and completion_dt < min_period_dt:
                        continue

                    if mode is None:
                        activity_hash_int = None
                        try:
                            activity_hash_int = int(activity_hash)
                        except (TypeError, ValueError):
                            pass

                        activity_name = activity_name_cache.get(activity_hash_int)
                        if activity_name is None and activity_hash_int is not None:
                            try:
                                definition = await bungie_api.get_activity_definition(activity_hash_int)
                                activity_name = (definition.get("displayProperties") or {}).get("name")
                            except Exception:
                                activity_name = ""
                            activity_name_cache[activity_hash_int] = activity_name or ""

                        if not activity_name_matches_special_solo_activity(activity_name):
                            continue
                    elif mode != 82:
                        activity_hash_int = None
                        try:
                            activity_hash_int = int(activity_hash)
                        except (TypeError, ValueError):
                            pass

                        activity_name = activity_name_cache.get(activity_hash_int)
                        if activity_name is None and activity_hash_int is not None:
                            try:
                                definition = await bungie_api.get_activity_definition(activity_hash_int)
                                activity_name = (definition.get("displayProperties") or {}).get("name")
                            except Exception:
                                activity_name = ""
                            activity_name_cache[activity_hash_int] = activity_name or ""

                        if not activity_name_matches_special_solo_activity(activity_name):
                            continue

                    values = activity.get("values", {})
                    completed = values.get("completed", {}).get("basic", {}).get("value", 0) == 1
                    if not completed:
                        solo_debug(f"Skipping non-complete instance {instance_id}")
                        continue

                    deaths_value = values.get("deaths", {}).get("basic", {}).get("value")
                    pgcr = await bungie_api.get_pgcr(instance_id)
                    entries = pgcr.get("entries", [])
                    was_started_from_beginning = pgcr.get("activityWasStartedFromBeginning", True)
                    player_membership_id = str(membership_id)

                    def entry_completed(entry):
                        try:
                            return int(
                                entry.get("values", {})
                                .get("completed", {})
                                .get("basic", {})
                                .get("value", 0)
                            ) == 1
                        except (TypeError, ValueError):
                            return False

                    completed_entries = [entry for entry in entries if entry_completed(entry)]
                    completed_membership_ids = {
                        str(
                            (entry.get("player", {})
                            .get("destinyUserInfo", {})
                            .get("membershipId", ""))
                        ).strip()
                        for entry in completed_entries
                        if entry.get("player", {})
                        .get("destinyUserInfo", {})
                        .get("membershipId")
                    }

                    participant_membership_ids = {
                        str(
                            (entry.get("player", {})
                            .get("destinyUserInfo", {})
                            .get("membershipId", ""))
                        ).strip()
                        for entry in entries
                        if entry.get("player", {})
                        .get("destinyUserInfo", {})
                        .get("membershipId")
                    }

                    if participant_membership_ids:
                        is_solo = participant_membership_ids == {player_membership_id}
                    else:
                        is_solo = len(entries) == 1 or completed_membership_ids == {player_membership_id}
                    if not is_solo:
                        solo_debug(
                            f"Skipping non-solo instance {instance_id} (entries={len(entries)} "
                            f"completed_members={sorted(completed_membership_ids)})"
                        )
                        continue

                    class_name = None
                    subclass_name = None
                    if entries:
                        class_name, subclass_name = get_character_info_from_pgcr_entry(entries[0])

                    if deaths_value is None and entries:
                        deaths_value = entries[0].get("values", {}).get("deaths", {}).get("basic", {}).get("value")

                    kills_value = values.get("kills", {}).get("basic", {}).get("value")
                    if kills_value is None and entries:
                        kills_value = entries[0].get("values", {}).get("kills", {}).get("basic", {}).get("value")

                    opponents_defeated_value = values.get("opponentsDefeated", {}).get("basic", {}).get("value")
                    if opponents_defeated_value is None and entries:
                        opponents_defeated_value = entries[0].get("values", {}).get("opponentsDefeated", {}).get("basic", {}).get("value")

                    assists_value = values.get("assists", {}).get("basic", {}).get("value")
                    if assists_value is None and entries:
                        assists_value = entries[0].get("values", {}).get("assists", {}).get("basic", {}).get("value")

                    duration_value = values.get("activityDurationSeconds", {}).get("basic", {}).get("value")
                    if duration_value is None and entries:
                        duration_value = entries[0].get("values", {}).get("activityDurationSeconds", {}).get("basic", {}).get("value")

                    duration_seconds = None
                    try:
                        if duration_value is not None:
                            duration_seconds = int(duration_value)
                    except (TypeError, ValueError):
                        duration_seconds = None

                    is_flawless = False
                    try:
                        is_flawless = int(deaths_value) == 0
                    except (TypeError, ValueError):
                        is_flawless = False

                    completions.append(
                        {
                            "instance_id": instance_id,
                            "period": period,
                            "activity_hash": details.get("referenceId"),
                            "flawless": is_flawless,
                            "solo": is_solo,
                            "deaths": deaths_value,
                            "kills": kills_value,
                            "opponents_defeated": opponents_defeated_value,
                            "assists": assists_value,
                            "duration_seconds": duration_seconds,
                            "class_name": class_name,
                            "subclass_name": subclass_name,
                            "was_started_from_beginning": was_started_from_beginning,
                        }
                    )
                    solo_debug(
                        f"Detected solo completion instance={instance_id} hash={details.get('referenceId')} "
                        f"mode={mode} page={page} flawless={is_flawless} duration={duration_seconds}"
                    )

    completions.sort(key=lambda item: item.get("period", ""))
    return completions


async def build_latest_solo_completion_from_player(player):
    if player is None:
        return None

    completions = await detect_new_solo_dungeon_completions(player, set())
    if not completions:
        return None

    latest_completion = None
    latest_dt = None
    for completion in completions:
        completion_dt = parse_bungie_timestamp(completion.get("period"))
        if completion_dt is None:
            continue
        if latest_dt is None or completion_dt > latest_dt:
            latest_dt = completion_dt
            latest_completion = completion

    return latest_completion or completions[-1]


async def get_auto_watch_players_for_clan():
    if bungie_api is None or not BUNGIE_CLAN_ID:
        return []

    roster = await bungie_api.get_clan_roster(BUNGIE_CLAN_ID)
    players = []
    for member in roster:
        membership_id = member.get("membershipId")
        membership_type = member.get("membershipType")
        display_name = member.get("displayName") or "Guardian"
        if not membership_id or membership_type is None:
            continue
        players.append(
            {
                "global_name": display_name,
                "membership_id": str(membership_id),
                "membership_type": int(membership_type),
                # Character IDs are resolved lazily during checks.
                "character_ids": [],
                "avatar_url": member.get("avatarUrl"),
            }
        )
    return players


def get_solo_watch_scope(guild_config):
    configured = str((guild_config or {}).get("solo_watch_scope", "")).strip().casefold()
    if configured in {"server", "clan", "manual"}:
        return configured
    return "clan" if BUNGIE_CLAN_ID else "manual"


async def get_server_watch_players_for_guild(guild: discord.Guild):
    if guild is None:
        return []

    players = []
    seen_memberships = set()
    seen_names = set()
    for member in guild.members:
        if member.bot:
            continue

        candidate_names = [member.display_name, member.name]
        resolved_player = None
        for candidate_name in candidate_names:
            normalized_candidate = normalize_member_name(candidate_name)
            if not normalized_candidate or normalized_candidate in seen_names:
                continue
            seen_names.add(normalized_candidate)
            try:
                resolved_player = await resolve_player(candidate_name)
            except Exception:
                resolved_player = None
            if resolved_player:
                break

        if not resolved_player:
            continue

        membership_key = (
            str(resolved_player.get("membership_type")),
            str(resolved_player.get("membership_id")),
        )
        if membership_key in seen_memberships:
            continue
        seen_memberships.add(membership_key)
        players.append(resolved_player)

    return players


async def get_team_watched_players_for_guild(guild_config, guild: discord.Guild | None = None):
    scope = get_solo_watch_scope(guild_config)
    if scope == "server":
        return await get_server_watch_players_for_guild(guild)

    if scope == "clan" and BUNGIE_CLAN_ID:
        try:
            return await get_auto_watch_players_for_clan()
        except Exception as e:
            print(f"Failed to fetch clan roster for watched players: {e}")
            return []

    return list((guild_config or {}).get("dungeon_watch", []) or [])


async def post_solo_completion(guild: discord.Guild, channel_id: str, player, completion):
    try:
        channel = await bot.fetch_channel(int(channel_id))
    except Exception as e:
        print(f"Failed to fetch dungeon channel for guild {guild.id}: {e}")
        return

    if getattr(channel, "guild", None) is None or channel.guild.id != guild.id:
        print(f"Dungeon channel mismatch for guild {guild.id}")
        return

    guild_config = get_guild_config(guild.id)
    player_name = player.get("global_name", "Guardian")
    if (not player_name or str(player_name).strip().casefold() == "guardian") and bungie_api is not None:
        membership_id = player.get("membership_id")
        membership_type = player.get("membership_type")
        if membership_id and membership_type is not None:
            try:
                profile = await bungie_api.get_profile(membership_type, membership_id)
                user_info = (profile.get("profile", {}) or {}).get("data", {}).get("userInfo", {}) or {}
                display_name = user_info.get("bungieGlobalDisplayName") or user_info.get("displayName")
                display_code = user_info.get("bungieGlobalDisplayNameCode")
                if display_name:
                    if display_code is not None:
                        try:
                            player_name = f"{display_name}#{int(display_code):04d}"
                        except (TypeError, ValueError):
                            player_name = f"{display_name}#{display_code}"
                    else:
                        player_name = str(display_name)
            except Exception:
                pass

    player_avatar = normalize_bungie_image_url(player.get("avatar_url"))
    dungeon_name = await resolve_activity_name(completion.get("activity_hash"), guild_config=guild_config)
    thumbnail_url = resolve_dungeon_image_by_name(dungeon_name, guild_config)
    is_flawless = completion.get("flawless", False)
    is_solo = completion.get("solo", False)
    duration_seconds = completion.get("duration_seconds")

    # Use Bungie's exact checkpoint field instead of time heuristic
    was_started_from_beginning = completion.get("was_started_from_beginning", True)
    is_checkpoint = not was_started_from_beginning
    
    # Only post embeds for solo clears
    if not is_solo:
        return

    embed = build_dungeon_embed(
        player_name,
        player_avatar,
        dungeon_name,
        is_flawless,
        is_solo,
        was_started_from_beginning,
        guild_config,
        thumbnail_url=thumbnail_url,
    )
    
    if embed is None:
        return

    kills_value = completion.get("kills")
    opponents_defeated_value = completion.get("opponents_defeated")
    deaths_value = completion.get("deaths")
    duration_text = format_duration(duration_seconds)

    status = "Full" if was_started_from_beginning else "Checkpoint"
    kills_text = format_int_stat(kills_value) or "—"
    deaths_text = format_int_stat(deaths_value) or "—"
    time_text = duration_text or "—"
    hide_status = should_hide_status_for_short_exotic_run(dungeon_name, duration_seconds)
    embed.add_field(name="Kills", value=kills_text, inline=True)
    embed.add_field(name="Deaths", value=deaths_text, inline=True)
    embed.add_field(name="Time", value=time_text, inline=True)
    if not hide_status:
        embed.add_field(name="Status", value=status, inline=True)

    await channel.send(embed=embed)
    solo_debug(
        f"Posted completion guild={guild.id} player={player_name} "
        f"dungeon={dungeon_name} flawless={is_flawless} solo={is_solo} checkpoint={is_checkpoint}"
    )


async def run_solo_check_for_guild(guild: discord.Guild):
    # Solo completion posts are intentionally restricted to Golden Oldies.
    if str(guild.id) != GOLDEN_OLDIES_GUILD_ID:
        return

    guild_config = get_guild_config(guild.id)
    channel_id = guild_config.get("dungeon_channel_id")
    watched = await get_team_watched_players_for_guild(guild_config, guild=guild)

    if not watched:
        return

    if not channel_id:
        return

    last_checked_dt = parse_bungie_timestamp(guild_config.get("dungeon_last_checked_at"))
    check_started_at = datetime.now(timezone.utc)
    seen_instance_ids = set(guild_config.get("dungeon_seen_instance_ids", []))
    posted_instance_ids = set(guild_config.get("dungeon_posted_instance_ids", []))
    new_seen = list(guild_config.get("dungeon_seen_instance_ids", []))
    new_posted = list(guild_config.get("dungeon_posted_instance_ids", []))
    mark_seen_ids = set()
    mark_posted_ids = set()
    processed_instance_ids = set()
    any_changes = False
    recent_historic_candidates = []
    posted_this_cycle = 0
    threshold_dt = check_started_at - timedelta(seconds=INITIAL_BASELINE_POST_WINDOW_SECONDS)
    post_not_before_dt = check_started_at - timedelta(minutes=SOLO_POST_DELAY_MINUTES)

    def persist_seen_instance(instance_id: str):
        if not instance_id:
            return
        if instance_id not in seen_instance_ids:
            seen_instance_ids.add(instance_id)
            new_seen.append(instance_id)
        guild_config["dungeon_seen_instance_ids"] = new_seen
        prune_seen_instances(guild_config)
        rules_config[str(guild.id)] = guild_config
        save_rules_config(rules_config)

    def persist_posted_instance(instance_id: str):
        if not instance_id:
            return
        if instance_id not in posted_instance_ids:
            posted_instance_ids.add(instance_id)
            new_posted.append(instance_id)
        guild_config["dungeon_posted_instance_ids"] = new_posted[-500:]
        rules_config[str(guild.id)] = guild_config
        save_rules_config(rules_config)

    for player in watched:
        player_name = player.get("global_name", "Guardian")
        try:
            completions = await detect_new_solo_dungeon_completions(
                player,
                posted_instance_ids,
                min_period_dt=threshold_dt,
            )
        except Exception as e:
            print(f"Solo check failed for {player_name} in guild {guild.id}: {e}")
            continue

        for completion in completions:
            instance_id = completion.get("instance_id")
            if not instance_id or instance_id in posted_instance_ids or instance_id in processed_instance_ids:
                continue

            processed_instance_ids.add(instance_id)

            completion_dt = parse_bungie_timestamp(completion.get("period"))
            if completion_dt is None:
                solo_debug(
                    f"Skipping old/invalid completion instance={instance_id} "
                    f"completion_dt={completion_dt}"
                )
                mark_seen_ids.add(instance_id)
                continue

            if completion_dt < threshold_dt:
                solo_debug(
                    f"Skipping stale completion instance={instance_id} "
                    f"completion_dt={completion_dt} threshold_dt={threshold_dt}"
                )
                mark_seen_ids.add(instance_id)
                continue

            if completion_dt > post_not_before_dt:
                solo_debug(
                    f"Deferring fresh completion instance={instance_id} completion_dt={completion_dt} "
                    f"post_not_before_dt={post_not_before_dt}"
                )
                continue

            if not SOLO_POST_CHECKPOINTS and not completion.get("was_started_from_beginning", True):
                solo_debug(f"Skipping checkpoint completion instance={instance_id} due to SOLO_POST_CHECKPOINTS=0")
                mark_seen_ids.add(instance_id)
                persist_seen_instance(instance_id)
                continue

            if last_checked_dt is None:
                solo_debug(
                    f"Startup candidate instance={instance_id} "
                    f"completion_dt={completion_dt} player={player_name}"
                )
                recent_historic_candidates.append((completion_dt, player, completion))
                continue

            if completion_dt <= last_checked_dt:
                if completion_dt >= threshold_dt:
                    solo_debug(
                        f"Recent historic candidate instance={instance_id} "
                        f"completion_dt={completion_dt} player={player_name}"
                    )
                    if posted_this_cycle >= SOLO_MAX_POSTS_PER_CYCLE:
                        solo_debug(
                            f"Deferring instance={instance_id} due to SOLO_MAX_POSTS_PER_CYCLE="
                            f"{SOLO_MAX_POSTS_PER_CYCLE}"
                        )
                        continue
                    try:
                        await post_solo_completion(guild, channel_id, player, completion)
                        posted_this_cycle += 1
                        mark_seen_ids.add(instance_id)
                        mark_posted_ids.add(instance_id)
                        persist_seen_instance(instance_id)
                        persist_posted_instance(instance_id)
                    except Exception as e:
                        print(
                            f"Failed to post recent historic solo completion for {player_name} "
                            f"(instance {instance_id}) in guild {guild.id}: {e}"
                        )
                else:
                    solo_debug(
                        f"Skipping old/invalid completion instance={instance_id} "
                        f"completion_dt={completion_dt} last_checked_dt={last_checked_dt}"
                    )
                    mark_seen_ids.add(instance_id)
                continue

            if posted_this_cycle >= SOLO_MAX_POSTS_PER_CYCLE:
                solo_debug(
                    f"Deferring instance={instance_id} due to SOLO_MAX_POSTS_PER_CYCLE="
                    f"{SOLO_MAX_POSTS_PER_CYCLE}"
                )
                continue

            try:
                await post_solo_completion(guild, channel_id, player, completion)
                posted_this_cycle += 1
                mark_seen_ids.add(instance_id)
                mark_posted_ids.add(instance_id)
                persist_seen_instance(instance_id)
                persist_posted_instance(instance_id)
            except Exception as e:
                print(
                    f"Failed to post solo completion for {player_name} "
                    f"(instance {instance_id}) in guild {guild.id}: {e}"
                )
                continue
        # Persist resolved character IDs for manually watched players.
        if guild_config.get("dungeon_watch"):
            any_changes = True

    if recent_historic_candidates:
        recent_historic_candidates.sort(key=lambda item: item[0] or datetime.min)
        posted_historic = 0
        for candidate_dt, candidate_player, candidate_completion in recent_historic_candidates:
            candidate_instance_id = candidate_completion.get("instance_id")
            if candidate_dt is None or candidate_dt < threshold_dt:
                if candidate_instance_id:
                    mark_seen_ids.add(candidate_instance_id)
                continue
            if candidate_dt > post_not_before_dt:
                continue
            if not SOLO_POST_CHECKPOINTS and not candidate_completion.get("was_started_from_beginning", True):
                if candidate_instance_id:
                    mark_seen_ids.add(candidate_instance_id)
                    persist_seen_instance(candidate_instance_id)
                continue
            if posted_this_cycle >= SOLO_MAX_POSTS_PER_CYCLE:
                continue
            try:
                await post_solo_completion(guild, channel_id, candidate_player, candidate_completion)
                posted_historic += 1
                posted_this_cycle += 1
                if candidate_instance_id:
                    mark_seen_ids.add(candidate_instance_id)
                    mark_posted_ids.add(candidate_instance_id)
                    persist_seen_instance(candidate_instance_id)
                    persist_posted_instance(candidate_instance_id)
            except Exception as e:
                print(
                    f"Failed to post historic solo completion for {candidate_player.get('global_name','Guardian')} "
                    f"(instance {candidate_completion.get('instance_id')}) in guild {guild.id}: {e}"
                )
        if posted_historic == 0:
            newest_dt = max((item[0] for item in recent_historic_candidates if item[0] is not None), default=None)
            solo_debug(
                f"No recent historic completion to post; newest candidate dt={newest_dt} "
                f"is older than {threshold_dt}"
            )

    guild_config["dungeon_last_checked_at"] = check_started_at.isoformat()
    if mark_seen_ids:
        any_changes = True
        for instance_id in mark_seen_ids:
            if instance_id not in seen_instance_ids:
                seen_instance_ids.add(instance_id)
                new_seen.append(instance_id)

    if mark_posted_ids:
        any_changes = True
        for instance_id in mark_posted_ids:
            if instance_id not in posted_instance_ids:
                posted_instance_ids.add(instance_id)
                new_posted.append(instance_id)

    guild_config["dungeon_seen_instance_ids"] = new_seen
    guild_config["dungeon_posted_instance_ids"] = new_posted[-500:]
    prune_seen_instances(guild_config)
    rules_config[str(guild.id)] = guild_config
    save_rules_config(rules_config)



async def run_team_activity_check_for_guild(guild: discord.Guild):
    guild_config = get_guild_config(guild.id)
    team_channel_id = guild_config.get("team_dungeon_channel_id")
    team_raid_channel_id = guild_config.get("team_raid_channel_id")

    # If no team channels are configured, skip team activity work entirely.
    if not team_channel_id and not team_raid_channel_id:
        return

    watched = await get_team_watched_players_for_guild(guild_config, guild=guild)

    if not watched:
        return

    if team_channel_id:
        await team_dungeon_tracker.run_team_check_for_guild(
            guild=guild,
            channel_id=team_channel_id,
            watched=watched,
        )

    if team_raid_channel_id:
        await team_raid_tracker.run_team_raid_check_for_guild(
            guild=guild,
            channel_id=team_raid_channel_id,
            watched=watched,
        )


async def run_startup_solo_scan_once():
    global startup_solo_scan_done
    if startup_solo_scan_done:
        return

    for guild in bot.guilds:
        try:
            await run_solo_check_for_guild(guild)
        except Exception as e:
            print(f"Startup solo check failed for guild {guild.id}: {e}")

    startup_solo_scan_done = True


@tasks.loop(minutes=SOLO_CHECK_INTERVAL_MINUTES)
async def solo_dungeon_check_loop():
    if bungie_api is None:
        return
    for guild in bot.guilds:
        await run_solo_check_for_guild(guild)


@solo_dungeon_check_loop.before_loop
async def before_solo_dungeon_check_loop():
    await bot.wait_until_ready()


@tasks.loop(minutes=TEAM_ACTIVITY_CHECK_INTERVAL_MINUTES)
async def team_activity_check_loop():
    if bungie_api is None:
        return
    for guild in bot.guilds:
        await run_team_activity_check_for_guild(guild)


@team_activity_check_loop.before_loop
async def before_team_activity_check_loop():
    await bot.wait_until_ready()


STATUSES = [
    discord.CustomActivity(name="...Goldies"),
    discord.CustomActivity(name="Welcome"),
]
_status_index = 0


@tasks.loop(seconds=8)
async def rotate_status():
    global _status_index
    await bot.change_presence(activity=STATUSES[_status_index])
    _status_index = (_status_index + 1) % len(STATUSES)


@rotate_status.before_loop
async def before_rotate_status():
    await bot.wait_until_ready()


def get_rules_role_settings(guild_config):
    configured_role_id = guild_config.get("rules_role_id") or os.getenv('RULES_ROLE_ID')
    configured_role_name = guild_config.get("rules_role_name") or os.getenv('RULES_ROLE_NAME', 'Verified')
    return configured_role_id, configured_role_name


def find_verified_role(guild: discord.Guild, guild_config):
    role_id, configured_role_name = get_rules_role_settings(guild_config)
    if role_id:
        try:
            role = guild.get_role(int(role_id))
            if role:
                return role
        except ValueError:
            pass

    role = discord.utils.get(guild.roles, name=configured_role_name)
    if role:
        return role

    lowered = configured_role_name.casefold()
    for candidate in guild.roles:
        if candidate.name.casefold() == lowered:
            return candidate
    return None


def is_admin_or_founder(member: discord.Member):
    if member.guild_permissions.administrator:
        return True
    for role in member.roles:
        if role.name.casefold() == "founder":
            return True
    return False


async def ensure_rules_message(guild_id: int, channel_id: int):
    guild_key = str(guild_id)
    guild_config = rules_config.get(guild_key, {})
    try:
        channel = await bot.fetch_channel(channel_id)
    except Exception as e:
        print(f"Failed to fetch rules channel for guild {guild_id}: {e}")
        return

    # Guard against using a channel that belongs to a different guild.
    if getattr(channel, "guild", None) is None or channel.guild.id != guild_id:
        print(
            f"Skipping rules check for guild {guild_id}: "
            f"channel {channel_id} belongs to a different guild."
        )
        return

    # First, try stored message ID.
    message_id = guild_config.get("rules_message_id")
    if message_id:
        try:
            existing = await channel.fetch_message(int(message_id))
            if existing.author == bot.user:
                return
        except Exception:
            # Stored message no longer exists or is inaccessible; fall through to scan/post.
            pass

    # Fallback scan in case message exists but wasn't stored.
    try:
        async for message in channel.history(limit=100):
            if (
                message.author == bot.user
                and message.content.strip().startswith("Please read the Rules")
                and any(reaction.emoji == RULES_EMOJI for reaction in message.reactions)
            ):
                guild_config["rules_message_id"] = str(message.id)
                rules_config[guild_key] = guild_config
                save_rules_config(rules_config)
                print(f"Rules message already exists in guild {guild_id}. Skipping.")
                return

        msg = await channel.send(RULES_TEXT)
        await msg.add_reaction(RULES_EMOJI)
        guild_config["rules_message_id"] = str(msg.id)
        rules_config[guild_key] = guild_config
        save_rules_config(rules_config)
    except Exception as e:
        print(f"Failed to ensure rules message in guild {guild_id}: {e}")



@bot.event
async def on_ready():
    global bungie_api
    global startup_solo_scan_done
    global startup_solo_scan_task
    print(f'Logged in as {bot.user}!', flush=True)

    if BOT_USERNAME and bot.user and bot.user.name != BOT_USERNAME:
        try:
            await bot.user.edit(username=BOT_NICKNAME)
            print(f"Updated global bot username to {BOT_NICKNAME}", flush=True)
        except Exception as e:
            print(f"Failed to update global bot username: {e}", flush=True)

    if BOT_NICKNAME:
        for guild in bot.guilds:
            me = guild.me or guild.get_member(bot.user.id)
            if me is None:
                try:
                    me = await guild.fetch_member(bot.user.id)
                except Exception as e:
                    print(f"Failed to fetch bot member in guild {guild.id}: {e}", flush=True)
                    continue

            current_nick = me.nick or bot.user.name
            if me.nick == BOT_NICKNAME:
                print(f"Bot nickname already set to {BOT_NICKNAME} in guild {guild.id}", flush=True)
                continue

            try:
                await me.edit(nick=BOT_NICKNAME, reason="Sync bot display name")
                print(
                    f"Set bot nickname from {current_nick} to {BOT_NICKNAME} in guild {guild.id}",
                    flush=True,
                )
            except discord.Forbidden:
                print(f"Missing permission to set nickname in guild {guild.id}", flush=True)
            except Exception as e:
                print(f"Failed to set nickname in guild {guild.id}: {e}", flush=True)

    if BUNGIE_API_KEY and bungie_api is None:
        bungie_api = BungieAPI(BUNGIE_API_KEY)
    if not BUNGIE_API_KEY:
        print("BUNGIE_API_KEY is missing. Solo dungeon tracking is disabled.")
    else:
        if not BUNGIE_CLAN_ID:
            print("BUNGIE_CLAN_ID is missing. Set it for automatic clan-wide solo tracking.")
        if not solo_dungeon_check_loop.is_running():
            solo_dungeon_check_loop.start()
        if not team_activity_check_loop.is_running():
            team_activity_check_loop.start()
        guardian_rank_commands.start()

        if not startup_solo_scan_done and (startup_solo_scan_task is None or startup_solo_scan_task.done()):
            startup_solo_scan_task = asyncio.create_task(run_startup_solo_scan_once())

    try:
        synced = await asyncio.wait_for(bot.tree.sync(), timeout=20)
        print(f'Synced {len(synced)} global command(s)')

        # Remove any old guild-scoped copies so Discord only shows one command set.
        for guild in bot.guilds:
            bot.tree.clear_commands(guild=guild)
            guild_synced = await asyncio.wait_for(bot.tree.sync(guild=guild), timeout=20)
            print(
                f'Cleared guild-specific command copies for {guild.name} '
                f'({guild.id}); now {len(guild_synced)} guild command(s)'
            )
    except Exception as e:
        print(f'Failed to sync global commands: {e}')

    fallback_channel_id = os.getenv('RULES_CHANNEL_ID')
    fallback_guild_id = None
    if fallback_channel_id:
        try:
            fallback_channel = await bot.fetch_channel(int(fallback_channel_id))
            if getattr(fallback_channel, "guild", None) is not None:
                fallback_guild_id = fallback_channel.guild.id
        except Exception as e:
            print(f"Failed to resolve RULES_CHANNEL_ID: {e}")

    for guild in bot.guilds:
        guild_key = str(guild.id)
        guild_config = rules_config.get(guild_key, {})
        channel_id = guild_config.get("rules_channel_id")
        if not channel_id and fallback_channel_id and fallback_guild_id == guild.id:
            # Migrate one-time env fallback into per-guild config.
            channel_id = str(fallback_channel_id)
            guild_config["rules_channel_id"] = str(fallback_channel_id)
            rules_config[guild_key] = guild_config
            save_rules_config(rules_config)
        if not channel_id:
            continue
        await ensure_rules_message(guild.id, int(channel_id))

        raid_roles_channel_id = guild_config.get("raid_roles_channel_id")
        if raid_roles_channel_id:
            await raid_role_manager.ensure_raid_roles_message(guild.id, int(raid_roles_channel_id))

    if not rotate_status.is_running():
        await bot.change_presence(activity=STATUSES[0])
        rotate_status.start()
    


@bot.event
async def on_close():
    if bungie_api is not None:
        await bungie_api.close()

@bot.event
async def on_member_join(member):
    guild_config = rules_config.get(str(member.guild.id), {})
    guild_config = get_effective_welcome_settings(member.guild.id, guild_config, member.guild.name)
    if guild_config.get("welcome_enabled", True) is False:
        return
    channel_id = guild_config.get("welcome_channel_id")
    channel = None
    if channel_id:
        try:
            channel = await bot.fetch_channel(int(channel_id))
        except Exception as e:
            print(f"Failed to fetch welcome channel for guild {member.guild.id}: {e}")
    if channel is None:
        channel = member.guild.system_channel
    if channel:
        welcome_title = guild_config.get("welcome_title", "Welcome to the server!")
        welcome_message = guild_config.get(
            "welcome_message",
            "{member} has joined the server!\n\nSay hi, and introduce yourself in General Chat!"
        )
        welcome_image = guild_config.get("welcome_image")
        welcome_bottom_logo_url = guild_config.get("welcome_bottom_logo_url")
        welcome_footer_logo_url = guild_config.get("welcome_footer_logo_url")
        if not welcome_bottom_logo_url and welcome_footer_logo_url:
            welcome_bottom_logo_url = welcome_footer_logo_url
        prefer_bottom_logo = is_seal_team_six_welcome_context(
            member.guild.id,
            member.guild.name,
            guild_config,
            getattr(channel, "id", None),
        )
        embed = discord.Embed(
            title=welcome_title,
            description=welcome_message.replace("{member}", member.mention),
            color=discord.Color.gold()
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        image_url = None
        if prefer_bottom_logo and welcome_bottom_logo_url:
            image_url = str(welcome_bottom_logo_url)
        elif welcome_image:
            image_url = str(welcome_image)
        elif welcome_bottom_logo_url:
            image_url = str(welcome_bottom_logo_url)

        attachment, filename = await build_embed_image_attachment(image_url, fallback_name="welcome-logo")
        if attachment and filename:
            embed.set_image(url=f"attachment://{filename}")
            await channel.send(embed=embed, file=attachment)
        else:
            if image_url:
                embed.set_image(url=image_url)
            await channel.send(embed=embed)

# Reaction-based rules agreement
@bot.event
async def on_raw_reaction_add(payload):
    if payload.guild_id is None:
        return

    guild_rules = rules_config.get(str(payload.guild_id), {})
    handled = await raid_role_manager.handle_raid_reaction_add(payload, guild_rules)
    if handled:
        return

    channel_id = guild_rules.get('rules_channel_id') or os.getenv('RULES_CHANNEL_ID')
    emoji = RULES_EMOJI
    if not channel_id or str(payload.channel_id) != str(channel_id):
        return

    # If we track a specific rules message, only accept reactions on that message.
    tracked_message_id = guild_rules.get("rules_message_id")
    if tracked_message_id and str(payload.message_id) != str(tracked_message_id):
        return

    # Only respond to the scroll emoji
    if str(payload.emoji) != emoji:
        return

    guild = bot.get_guild(payload.guild_id)
    if not guild:
        return

    member = guild.get_member(payload.user_id)
    if member is None:
        try:
            member = await guild.fetch_member(payload.user_id)
        except Exception:
            return

    if member.bot:
        return

    role = find_verified_role(guild, guild_rules)
    if not role:
        _, configured_role_name = get_rules_role_settings(guild_rules)
        print(
            f"Verified role not found in guild {guild.id}. "
            f"Expected role id {guild_rules.get('rules_role_id') or os.getenv('RULES_ROLE_ID')} "
            f"or role name: {configured_role_name}"
        )
        return
    if role in member.roles:
        return
    try:
        await member.add_roles(role, reason="Agreed to rules via reaction role")
    except Exception as e:
        print(f"Failed to assign role: {e}")


@bot.event
async def on_raw_reaction_remove(payload):
    if payload.guild_id is None:
        return

    guild_rules = rules_config.get(str(payload.guild_id), {})
    await raid_role_manager.handle_raid_reaction_remove(payload, guild_rules)


@bot.tree.command(name="test_welcome", description="Test the welcome embed in the current channel.")
@app_commands.checks.has_permissions(administrator=True)
async def test_welcome(interaction: discord.Interaction):
    member = interaction.user
    guild_config = rules_config.get(str(interaction.guild_id), {})
    guild_config = get_effective_welcome_settings(
        interaction.guild_id,
        guild_config,
        interaction.guild.name if interaction.guild else None,
        interaction.channel_id,
    )
    embed = discord.Embed(
        title=guild_config.get("welcome_title", "Welcome to the server!"),
        description=guild_config.get(
            "welcome_message",
            "{member} has joined the server!\n\nSay hi, and introduce yourself in General Chat!"
        ).replace("{member}", member.mention),
        color=discord.Color.gold()
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    welcome_image = guild_config.get("welcome_image")
    welcome_bottom_logo_url = guild_config.get("welcome_bottom_logo_url")
    welcome_footer_logo_url = guild_config.get("welcome_footer_logo_url")
    if not welcome_bottom_logo_url and welcome_footer_logo_url:
        welcome_bottom_logo_url = welcome_footer_logo_url
    prefer_bottom_logo = is_seal_team_six_welcome_context(
        interaction.guild_id,
        interaction.guild.name if interaction.guild else None,
        guild_config,
        interaction.channel_id,
    )
    image_url = None
    if prefer_bottom_logo and welcome_bottom_logo_url:
        image_url = str(welcome_bottom_logo_url)
    elif welcome_image:
        image_url = str(welcome_image)
    elif welcome_bottom_logo_url:
        image_url = str(welcome_bottom_logo_url)

    attachment, filename = await build_embed_image_attachment(image_url, fallback_name="welcome-logo")
    if attachment and filename:
        embed.set_image(url=f"attachment://{filename}")
        await interaction.response.send_message(embed=embed, file=attachment)
    else:
        if image_url:
            embed.set_image(url=image_url)
        await interaction.response.send_message(embed=embed)


@bot.tree.command(name="set_rules_channel", description="Set the rules channel for this server.")
@app_commands.checks.has_permissions(administrator=True)
@app_commands.describe(channel="The channel where the rules message should be posted")
async def set_rules_channel(interaction: discord.Interaction, channel: discord.TextChannel):
    guild_id = str(interaction.guild_id)
    guild_config = rules_config.get(guild_id, {})
    guild_config["rules_channel_id"] = str(channel.id)
    # Reset tracked message because channel changed.
    guild_config.pop("rules_message_id", None)
    rules_config[guild_id] = guild_config
    save_rules_config(rules_config)
    await ensure_rules_message(interaction.guild_id, channel.id)
    await interaction.response.send_message(
        f"Rules channel set to {channel.mention}. I checked and ensured one rules message is present.",
        ephemeral=True,
    )


@bot.tree.command(name="set_rules_role", description="Set the role to grant when members agree to the rules.")
@app_commands.checks.has_permissions(administrator=True)
@app_commands.describe(role="Role to grant after a member reacts to the rules message")
async def set_rules_role(interaction: discord.Interaction, role: discord.Role):
    guild_id = str(interaction.guild_id)
    guild_config = rules_config.get(guild_id, {})
    guild_config["rules_role_id"] = str(role.id)
    guild_config["rules_role_name"] = role.name
    rules_config[guild_id] = guild_config
    save_rules_config(rules_config)
    await interaction.response.send_message(
        f"Rules agreement role set to {role.mention}.",
        ephemeral=True,
    )


@bot.tree.command(name="set_welcome_channel", description="Set the welcome channel for this server.")
@app_commands.checks.has_permissions(administrator=True)
@app_commands.describe(channel="The channel where welcome messages should be posted")
async def set_welcome_channel(interaction: discord.Interaction, channel: discord.TextChannel):
    guild_id = str(interaction.guild_id)
    guild_config = rules_config.get(guild_id, {})
    guild_config["welcome_channel_id"] = str(channel.id)
    guild_config["welcome_enabled"] = True
    rules_config[guild_id] = guild_config
    save_rules_config(rules_config)
    await interaction.response.send_message(
        f"Welcome channel set to {channel.mention}.",
        ephemeral=True,
    )


@bot.tree.command(name="unset_welcome_channel", description="Disable welcome messages for this server.")
@app_commands.checks.has_permissions(administrator=True)
async def unset_welcome_channel(interaction: discord.Interaction):
    guild_id = str(interaction.guild_id)
    guild_config = rules_config.get(guild_id, {})
    guild_config["welcome_enabled"] = False
    rules_config[guild_id] = guild_config
    save_rules_config(rules_config)
    await interaction.response.send_message(
        "Welcome messages have been disabled for this server.",
        ephemeral=True,
    )


@bot.tree.command(name="set_welcome_logo", description="Set or clear the welcome embed bottom logo image.")
@app_commands.checks.has_permissions(administrator=True)
@app_commands.describe(url="Direct image URL (leave blank to clear, or use clear/off/unset)")
async def set_welcome_logo(interaction: discord.Interaction, url: str | None = None):
    guild_id = str(interaction.guild_id)
    guild_config = rules_config.get(guild_id, {})
    raw = (url or "").strip()

    if not raw or raw.casefold() in {"clear", "off", "unset", "none"}:
        guild_config.pop("welcome_bottom_logo_url", None)
        guild_config.pop("welcome_footer_logo_url", None)
        rules_config[guild_id] = guild_config
        save_rules_config(rules_config)
        await interaction.response.send_message(
            "Welcome bottom logo has been cleared.",
            ephemeral=True,
        )
        return

    guild_config["welcome_bottom_logo_url"] = raw
    # Keep backward compatibility key aligned for older code paths/tools.
    guild_config["welcome_footer_logo_url"] = raw
    rules_config[guild_id] = guild_config
    save_rules_config(rules_config)
    await interaction.response.send_message(
        "Welcome bottom logo URL saved. Run /test_welcome to preview.",
        ephemeral=True,
    )


@bot.tree.command(name="set_raid_titles", description="Set raid title reaction panel to the channel you run this command in.")
async def set_raid_titles(interaction: discord.Interaction):
    if interaction.guild_id is None:
        await interaction.response.send_message("This command must be used in a server.", ephemeral=True)
        return

    member = interaction.user
    if not isinstance(member, discord.Member) or not is_admin_or_founder(member):
        await interaction.response.send_message(
            "Only a Founder or Administrator can use this command.",
            ephemeral=True,
        )
        return

    channel = interaction.channel
    if not isinstance(channel, discord.TextChannel):
        await interaction.response.send_message(
            "Please run this command in a text channel.",
            ephemeral=True,
        )
        return

    await raid_role_manager.set_raid_roles_channel(interaction.guild_id, channel.id)
    await interaction.response.send_message(
        f"Raid title panel set to {channel.mention} and posted.",
        ephemeral=True,
    )


@bot.tree.command(
    name="set_dungeon_channel",
    description="Set the channel for solo dungeon announcements!"
)
@app_commands.checks.has_permissions(administrator=True)
@app_commands.describe(channel="Optional channel ID, #mention, or name. Leave empty to use this channel.")
async def set_dungeon_channel(
    interaction: discord.Interaction,
    channel: str | None = None,
):
    if interaction.guild_id is None:
        await interaction.response.send_message("This command must be used in a server.", ephemeral=True)
        return

    guild = interaction.guild
    if guild is None:
        await interaction.response.send_message("Could not determine the server context.", ephemeral=True)
        return

    target_channel = resolve_text_channel_from_input(guild, channel)
    if target_channel is None:
        current_channel = interaction.channel
        if isinstance(current_channel, discord.TextChannel) and (channel is None or not str(channel).strip()):
            target_channel = current_channel

    if target_channel is None or target_channel.guild.id != interaction.guild_id:
        await interaction.response.send_message(
            "Could not find that text channel. Use a channel ID, #mention, exact name, or run the command in the target channel with no argument.",
            ephemeral=True,
        )
        return

    guild_config = get_guild_config(interaction.guild_id)
    guild_config["dungeon_channel_id"] = str(target_channel.id)
    rules_config[str(interaction.guild_id)] = guild_config
    save_rules_config(rules_config)
    await interaction.response.send_message(
        f"Solo dungeon announcements channel set to {target_channel.mention}.",
        ephemeral=True,
    )


@bot.tree.command(
    name="unset_dungeon_channel",
    description="Unset the channel for solo dungeon announcements.",
)
@app_commands.checks.has_permissions(administrator=True)
async def unset_dungeon_channel(interaction: discord.Interaction):
    if interaction.guild_id is None:
        await interaction.response.send_message("This command must be used in a server.", ephemeral=True)
        return

    guild_config = get_guild_config(interaction.guild_id)
    guild_config.pop("dungeon_channel_id", None)
    rules_config[str(interaction.guild_id)] = guild_config
    save_rules_config(rules_config)
    await interaction.response.send_message(
        "Solo dungeon announcements channel has been unset.",
        ephemeral=True,
    )


@bot.tree.command(
    name="set_team_dungeon_channel",
    description="Set the channel for team dungeon completion announcements.",
)
@app_commands.checks.has_permissions(administrator=True)
@app_commands.describe(channel="Optional channel ID, #mention, or name. Leave empty to use this channel.")
async def set_team_dungeon_channel(
    interaction: discord.Interaction,
    channel: str | None = None,
):
    if interaction.guild_id is None:
        await interaction.response.send_message("This command must be used in a server.", ephemeral=True)
        return

    guild = interaction.guild
    if guild is None:
        await interaction.response.send_message("Could not determine the server context.", ephemeral=True)
        return

    target_channel = resolve_text_channel_from_input(guild, channel)
    if target_channel is None:
        current_channel = interaction.channel
        if isinstance(current_channel, discord.TextChannel) and (channel is None or not str(channel).strip()):
            target_channel = current_channel

    if target_channel is None or target_channel.guild.id != interaction.guild_id:
        await interaction.response.send_message(
            "Could not find that text channel. Use a channel ID, #mention, exact name, or run the command in the target channel with no argument.",
            ephemeral=True,
        )
        return

    guild_config = get_guild_config(interaction.guild_id)
    guild_config["team_dungeon_channel_id"] = str(target_channel.id)
    rules_config[str(interaction.guild_id)] = guild_config
    save_rules_config(rules_config)
    await interaction.response.send_message(
        f"Team dungeon announcements channel set to {target_channel.mention}.",
        ephemeral=True,
    )


@bot.tree.command(
    name="unset_team_dungeon_channel",
    description="Unset the channel for team dungeon completion announcements.",
)
@app_commands.checks.has_permissions(administrator=True)
async def unset_team_dungeon_channel(interaction: discord.Interaction):
    if interaction.guild_id is None:
        await interaction.response.send_message("This command must be used in a server.", ephemeral=True)
        return

    guild_config = get_guild_config(interaction.guild_id)
    if "team_dungeon_channel_id" in guild_config:
        guild_config.pop("team_dungeon_channel_id", None)
        rules_config[str(interaction.guild_id)] = guild_config
        save_rules_config(rules_config)

    await interaction.response.send_message(
        "Team dungeon announcements channel has been unset.",
        ephemeral=True,
    )


@bot.tree.command(
    name="set_team_raid_channel",
    description="Set the channel for team raid completion announcements.",
)
@app_commands.checks.has_permissions(administrator=True)
@app_commands.describe(channel="Optional channel ID, #mention, or name. Leave empty to use this channel.")
async def set_team_raid_channel(
    interaction: discord.Interaction,
    channel: str | None = None,
):
    if interaction.guild_id is None:
        await interaction.response.send_message("This command must be used in a server.", ephemeral=True)
        return

    guild = interaction.guild
    if guild is None:
        await interaction.response.send_message("Could not determine the server context.", ephemeral=True)
        return

    target_channel = resolve_text_channel_from_input(guild, channel)
    if target_channel is None:
        current_channel = interaction.channel
        if isinstance(current_channel, discord.TextChannel) and (channel is None or not str(channel).strip()):
            target_channel = current_channel

    if target_channel is None or target_channel.guild.id != interaction.guild_id:
        await interaction.response.send_message(
            "Could not find that text channel. Use a channel ID, #mention, exact name, or run the command in the target channel with no argument.",
            ephemeral=True,
        )
        return

    guild_config = get_guild_config(interaction.guild_id)
    guild_config["team_raid_channel_id"] = str(target_channel.id)
    rules_config[str(interaction.guild_id)] = guild_config
    save_rules_config(rules_config)
    await interaction.response.send_message(
        f"Team raid announcements channel set to {target_channel.mention}.",
        ephemeral=True,
    )


@bot.tree.command(
    name="unset_team_raid_channel",
    description="Unset the channel for team raid completion announcements.",
)
@app_commands.checks.has_permissions(administrator=True)
async def unset_team_raid_channel(interaction: discord.Interaction):
    if interaction.guild_id is None:
        await interaction.response.send_message("This command must be used in a server.", ephemeral=True)
        return

    guild_config = get_guild_config(interaction.guild_id)
    if "team_raid_channel_id" in guild_config:
        guild_config.pop("team_raid_channel_id", None)
        rules_config[str(interaction.guild_id)] = guild_config
        save_rules_config(rules_config)

    await interaction.response.send_message(
        "Team raid announcements channel has been unset.",
        ephemeral=True,
    )


@bot.tree.command(
    name="set_dungeon_full_clear_seconds",
    description="Set the minimum runtime (seconds) treated as a full clear; lower is treated as checkpoint clear.")
@app_commands.checks.has_permissions(administrator=True)
@app_commands.describe(seconds="Minimum duration in seconds for a full clear (example: 900)")
async def set_dungeon_full_clear_seconds(interaction: discord.Interaction, seconds: app_commands.Range[int, 60, 21600]):
    if interaction.guild_id is None:
        await interaction.response.send_message("This command must be used in a server.", ephemeral=True)
        return

    guild_config = get_guild_config(interaction.guild_id)
    guild_config["dungeon_full_clear_min_seconds"] = int(seconds)
    rules_config[str(interaction.guild_id)] = guild_config
    save_rules_config(rules_config)
    await interaction.response.send_message(
        f"Set full-clear threshold to {seconds} seconds. Runs shorter than this will be labeled checkpoint clears.",
        ephemeral=True,
    )


@bot.tree.command(
    name="set_solo_watch_scope",
    description="Set whether solo/team tracking watches clan roster or only members in this server.",
)
@app_commands.checks.has_permissions(administrator=True)
@app_commands.describe(scope="Choose clan (all clan roster) or server (only Discord members in this server)")
@app_commands.choices(
    scope=[
        app_commands.Choice(name="Clan roster", value="clan"),
        app_commands.Choice(name="Server members only", value="server"),
    ]
)
async def set_solo_watch_scope(
    interaction: discord.Interaction,
    scope: app_commands.Choice[str],
):
    if interaction.guild_id is None:
        await interaction.response.send_message("This command must be used in a server.", ephemeral=True)
        return

    guild_config = get_guild_config(interaction.guild_id)
    guild_config["solo_watch_scope"] = scope.value
    rules_config[str(interaction.guild_id)] = guild_config
    save_rules_config(rules_config)

    if scope.value == "server":
        detail = "Tracking will include only players who are members of this Discord server."
    else:
        detail = "Tracking will use the configured Bungie clan roster."

    await interaction.response.send_message(
        f"Solo watch scope set to **{scope.name}**. {detail}",
        ephemeral=True,
    )


async def post_latest_solo_embed(
    interaction: discord.Interaction,
    gamertag: str,
    channel: discord.TextChannel | None = None,
    *,
    acknowledgement_text: str,
):
    if interaction.guild_id is None:
        await interaction.response.send_message("This command must be used in a server.", ephemeral=True)
        return

    guild_config = get_guild_config(interaction.guild_id)
    target_channel = channel or interaction.channel
    if not isinstance(target_channel, discord.TextChannel):
        await interaction.response.send_message(
            "Please use this command in a text channel or provide a text channel.",
            ephemeral=True,
        )
        return

    if target_channel.guild.id != interaction.guild_id:
        await interaction.response.send_message(
            "The selected channel must be in this server.",
            ephemeral=True,
        )
        return

    resolved_player = await resolve_player(str(gamertag).strip())
    if resolved_player is None:
        await interaction.response.send_message(
            "Could not find that Bungie player. Try WH33LS or WH33LS#8650.",
            ephemeral=True,
        )
        return

    completion = await build_latest_solo_completion_from_player(resolved_player)
    if completion is None:
        await interaction.response.send_message(
            f"No solo dungeon completion found for {resolved_player.get('global_name', str(gamertag).strip())}.",
            ephemeral=True,
        )
        return

    dungeon_name = await resolve_activity_name(completion.get("activity_hash"), guild_config=guild_config)
    flawless = bool(completion.get("flawless", False))
    checkpoint = not bool(completion.get("was_started_from_beginning", True))
    player_name = resolved_player.get("global_name") or str(gamertag).strip()
    player_avatar = normalize_bungie_image_url(resolved_player.get("avatar_url"))

    embed = build_dungeon_embed(
        player_name,
        player_avatar,
        dungeon_name,
        flawless,
        True,
        not checkpoint,
        guild_config,
    )
    kills_text = format_int_stat(completion.get("kills")) or "—"
    deaths_text = format_int_stat(completion.get("deaths")) or "—"
    time_text = format_duration(completion.get("duration_seconds")) or "—"
    status_text = "Checkpoint" if checkpoint else "Full"
    hide_status = should_hide_status_for_short_exotic_run(dungeon_name, completion.get("duration_seconds"))
    embed.add_field(name="Kills", value=kills_text, inline=True)
    embed.add_field(name="Deaths", value=deaths_text, inline=True)
    embed.add_field(name="Time", value=time_text, inline=True)
    if not hide_status:
        embed.add_field(name="Status", value=status_text, inline=True)

    gold_cup_path = BASE_DIR / "Gold-Cup.png"
    if gold_cup_path.exists():
        with gold_cup_path.open("rb") as f:
            await target_channel.send(embed=embed, file=discord.File(f, filename="Gold-Cup.png"))
    else:
        await target_channel.send(embed=embed)

    await interaction.response.send_message(
        acknowledgement_text.format(player_name=player_name, target_channel=target_channel.mention),
        ephemeral=True,
    )


@bot.tree.command(name="test_dungeon_embed", description="Post a real solo dungeon embed from a Bungie gamertag.")
@app_commands.checks.has_permissions(administrator=True)
@app_commands.describe(
    gamertag="Bungie name like WH33LS or WH33LS#8650",
    channel="Optional channel for the test post (defaults to this channel)",
)
async def test_dungeon_embed(
    interaction: discord.Interaction,
    gamertag: str,
    channel: discord.TextChannel | None = None,
):
    await post_latest_solo_embed(
        interaction,
        gamertag,
        channel,
        acknowledgement_text="Posted latest solo dungeon for {player_name} to {target_channel}.",
    )


@bot.tree.command(name="post_latest_solo", description="Post your latest real solo dungeon embed like a normal announcement.")
@app_commands.checks.has_permissions(administrator=True)
@app_commands.describe(
    gamertag="Bungie name like WH33LS or WH33LS#8650",
    channel="Optional channel for the post (defaults to this channel)",
)
async def post_latest_solo(
    interaction: discord.Interaction,
    gamertag: str,
    channel: discord.TextChannel | None = None,
):
    await post_latest_solo_embed(
        interaction,
        gamertag,
        channel,
        acknowledgement_text="Posted latest solo completion for {player_name} to {target_channel}.",
    )


@bot.tree.command(name="test_raid_embed", description="Post a test raid embed preview with your configured raid image.")
@app_commands.checks.has_permissions(administrator=True)
@app_commands.describe(
    raid_name="Raid name to preview (must match your raid image mapping name)",
)
async def test_raid_embed(
    interaction: discord.Interaction,
    raid_name: str,
):
    if interaction.guild_id is None:
        await interaction.response.send_message("This command must be used in a server.", ephemeral=True)
        return

    guild_config = get_guild_config(interaction.guild_id)
    thumbnail_url = resolve_raid_thumbnail_by_name(raid_name, guild_config)
    if not thumbnail_url:
        await interaction.response.send_message(
            "No raid image found for that name in team_raid_thumbnail_by_name.",
            ephemeral=True,
        )
        return

    embed = discord.Embed(
        description=f"**{raid_name}**\nRaid thumbnail preview",
        color=discord.Color.gold(),
    )
    embed.set_thumbnail(url=thumbnail_url)
    await interaction.response.send_message(embed=embed)


@bot.tree.command(
    name="test_team_dungeon_post",
    description="Post a real team dungeon completion message from a Bungie gamertag.",
)
@app_commands.checks.has_permissions(administrator=True)
@app_commands.describe(
    gamertag="Bungie name like WH33LS or WH33LS#8650",
    channel="Optional channel for the test post (defaults to this channel)",
)
async def test_team_dungeon_post(
    interaction: discord.Interaction,
    gamertag: str,
    channel: discord.TextChannel | None = None,
):
    if interaction.guild_id is None:
        await interaction.response.send_message("This command must be used in a server.", ephemeral=True)
        return

    guild_config = get_guild_config(interaction.guild_id)
    target_channel = channel
    if target_channel is None:
        configured_channel_id = guild_config.get("team_dungeon_channel_id") or guild_config.get("dungeon_channel_id")
        if configured_channel_id:
            try:
                fetched_channel = await bot.fetch_channel(int(configured_channel_id))
                if isinstance(fetched_channel, discord.TextChannel):
                    target_channel = fetched_channel
            except Exception:
                target_channel = None

    if target_channel is None:
        target_channel = interaction.channel

    if not isinstance(target_channel, discord.TextChannel):
        await interaction.response.send_message(
            "Please use this command in a text channel or provide a text channel.",
            ephemeral=True,
        )
        return

    if target_channel.guild.id != interaction.guild_id:
        await interaction.response.send_message(
            "The selected channel must be in this server.",
            ephemeral=True,
        )
        return

    player = await resolve_player(str(gamertag).strip())
    if player is None:
        await interaction.response.send_message(
            "Could not find that Bungie player. Try WH33LS or WH33LS#8650.",
            ephemeral=True,
        )
        return

    try:
        completion = await team_dungeon_tracker.build_latest_completion_from_player(
            player=player,
            watched_membership_map=None,
            min_completions=2,
        )
    except Exception as e:
        await interaction.response.send_message(
            f"Failed to load latest dungeon completion for that player: {e}",
            ephemeral=True,
        )
        return

    if completion is None:
        await interaction.response.send_message(
            f"No team dungeon completion with 2+ players found for {player.get('global_name', 'that player')}.",
            ephemeral=True,
        )
        return

    dungeon_name = await resolve_activity_name(completion.get("activity_hash"), guild_config=guild_config)
    completion["difficulty"] = await team_dungeon_tracker.resolve_dungeon_difficulty_label(
        completion.get("activity_hash"),
        dungeon_name,
    )
    embed, file = await team_dungeon_tracker.build_team_message_payload(dungeon_name, completion, guild_config)
    if file:
        await target_channel.send(embed=embed, file=file)
    else:
        await target_channel.send(embed=embed)
    await interaction.response.send_message(
        f"Posted real team dungeon data for {player.get('global_name', str(gamertag).strip())} to {target_channel.mention}.",
        ephemeral=True,
    )


@bot.tree.command(
    name="test_team_raid_post",
    description="Post a real team raid completion message from a Bungie gamertag.",
)
@app_commands.checks.has_permissions(administrator=True)
@app_commands.describe(
    gamertag="Bungie name like WH33LS or WH33LS#8650",
    channel="Optional channel for the test post (defaults to this channel)",
)
async def test_team_raid_post(
    interaction: discord.Interaction,
    gamertag: str,
    channel: discord.TextChannel | None = None,
):
    if interaction.guild_id is None:
        await interaction.response.send_message("This command must be used in a server.", ephemeral=True)
        return

    guild_config = get_guild_config(interaction.guild_id)
    target_channel = channel
    if target_channel is None:
        configured_channel_id = (
            guild_config.get("team_raid_channel_id")
            or guild_config.get("team_dungeon_channel_id")
            or guild_config.get("dungeon_channel_id")
        )
        if configured_channel_id:
            try:
                fetched_channel = await bot.fetch_channel(int(configured_channel_id))
                if isinstance(fetched_channel, discord.TextChannel):
                    target_channel = fetched_channel
            except Exception:
                target_channel = None

    if target_channel is None:
        target_channel = interaction.channel

    if not isinstance(target_channel, discord.TextChannel):
        await interaction.response.send_message(
            "Please use this command in a text channel or provide a text channel.",
            ephemeral=True,
        )
        return

    if target_channel.guild.id != interaction.guild_id:
        await interaction.response.send_message(
            "The selected channel must be in this server.",
            ephemeral=True,
        )
        return

    player = await resolve_player(str(gamertag).strip())
    if player is None:
        await interaction.response.send_message(
            "Could not find that Bungie player. Try WH33LS or WH33LS#8650.",
            ephemeral=True,
        )
        return

    try:
        completion = await team_raid_tracker.build_latest_completion_from_player(
            player=player,
            watched_membership_map=None,
            min_completions=int(os.getenv("TEAM_RAID_MIN_CLAN_COMPLETIONS", "3")),
        )
    except Exception as e:
        await interaction.response.send_message(
            f"Failed to load latest raid completion for that player: {e}",
            ephemeral=True,
        )
        return

    if completion is None:
        await interaction.response.send_message(
            f"No team raid completion with 3+ clan members found for {player.get('global_name', 'that player')}.",
            ephemeral=True,
        )
        return

    raid_name = await resolve_activity_name(completion.get("activity_hash"), guild_config=guild_config)
    completion["difficulty"] = await team_raid_tracker.resolve_raid_difficulty_label(
        completion.get("activity_hash"),
        raid_name,
    )
    embed, file = await team_raid_tracker.build_team_raid_message_payload(raid_name, completion, guild_config)
    if file:
        await target_channel.send(embed=embed, file=file)
    else:
        await target_channel.send(embed=embed)
    await interaction.response.send_message(
        f"Posted real team raid data for {player.get('global_name', str(gamertag).strip())} to {target_channel.mention}.",
        ephemeral=True,
    )


@bot.tree.command(
    name="post_dungeon_embed",
    description="Post a dungeon embed to the configured dungeon channel without leaving a public command response.")
@app_commands.checks.has_permissions(administrator=True)
@app_commands.describe(
    player_name="Name to show in the embed (defaults to your Discord display name)",
    dungeon_name="Dungeon name to show in the embed",
    flawless="Whether this should be a flawless solo embed",
    is_solo="Whether this should be treated as a solo run",
    full_clear="Whether this should be treated as a full clear",
)
async def post_dungeon_embed(
    interaction: discord.Interaction,
    dungeon_name: str,
    flawless: bool = False,
    is_solo: bool = True,
    full_clear: bool = True,
    player_name: str | None = None,
):
    if interaction.guild_id is None:
        await interaction.response.send_message("This command must be used in a server.", ephemeral=True)
        return

    guild_config = get_guild_config(interaction.guild_id)
    channel_id = guild_config.get("dungeon_channel_id")
    if not channel_id:
        await interaction.response.send_message(
            "Dungeon channel is not configured for this server.",
            ephemeral=True,
        )
        return

    try:
        channel = await bot.fetch_channel(int(channel_id))
    except Exception as e:
        await interaction.response.send_message(
            f"Failed to resolve configured dungeon channel: {e}",
            ephemeral=True,
        )
        return

    if getattr(channel, "guild", None) is None or channel.guild.id != interaction.guild_id:
        await interaction.response.send_message(
            "Configured dungeon channel does not belong to this guild.",
            ephemeral=True,
        )
        return

    player_name = player_name or interaction.user.display_name
    player_avatar = interaction.user.display_avatar.url if interaction.user.display_avatar else None
    embed = build_dungeon_embed(
        player_name,
        player_avatar,
        dungeon_name,
        flawless,
        is_solo,
        full_clear,
        guild_config,
    )

    await channel.send(embed=embed)
    await interaction.response.send_message(
        f"Posted a dungeon embed to {channel.mention}.",
        ephemeral=True,
    )


@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.errors.MissingPermissions):
        message = "You need the 'Administrator' permission to use this command."
    else:
        message = "Something went wrong while running that command. Please try again."
        print(f"App command error: {error}")

    try:
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)
    except discord.NotFound:
        # Interaction token expired (typically >3s before first response).
        print("App command interaction expired before error response could be sent.")


bot.run(TOKEN)

