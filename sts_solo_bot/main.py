
import unicodedata
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import discord
from discord import app_commands
from discord.ext import commands, tasks

from bungie_api import BungieAPI, normalize_bungie_image_url
from pinnacle_ops import (
    SPECIAL_SOLO_ACTIVITY_NAMES,
    activity_name_matches_special_solo_activity,
    normalize_special_solo_activity_display_name,
)
from raid_weapons_embed import build_raid_weapons_embed, get_raid_weapon_choices
from .config import (
    BUNGIE_API_KEY,
    BUNGIE_CLAN_ID,
    DEFAULT_DUNGEON_THUMBNAIL_URL,
    DUNGEON_HISTORY_COUNT,
    INITIAL_BASELINE_POST_WINDOW_SECONDS,
    SOLO_ACTIVITY_MODES,
    SOLO_CHECK_INTERVAL_MINUTES,
    SOLO_HISTORY_MAX_PAGES,
    SOLO_POST_DELAY_MINUTES,
    TOKEN,
    is_allowed_guild,
)
from .embeds import build_solo_embed, format_duration, format_int_stat
from .storage import get_guild_config, load_config, parse_bungie_timestamp, save_config


intents = discord.Intents(guilds=True, members=True)
intents.message_content = True
bot = commands.Bot(command_prefix="/", intents=intents)
rules_config = load_config()
bungie_api = None
startup_solo_scan_done = False
pantheon_modifier_name_cache = {}


def sanitize_thumbnail_url(raw_url) -> str | None:
    raw = str(raw_url or "").strip().strip("<>")
    if not raw:
        return None

    if raw.startswith("//"):
        raw = f"https:{raw}"

    normalized = normalize_bungie_image_url(raw)
    if not normalized:
        return None

    parsed = urlparse(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None

    return normalized


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

def normalize_member_name(name: str):
    if not name:
        return None
    normalized = unicodedata.normalize("NFKC", str(name)).strip()
    return normalized.casefold() if normalized else None


def normalize_bungie_name_input(name: str):
    if not name:
        return ""
    normalized = unicodedata.normalize("NFKC", str(name)).strip()
    if not normalized:
        return ""
    if normalized.startswith("[") and "]" in normalized:
        normalized = normalized.split("]", 1)[1].strip()
    normalized = normalized.strip("`'\" ").strip(",.;:")
    if "#" in normalized:
        base, code = normalized.split("#", 1)
        base = base.strip().strip(",.;:")
        code = code.strip().split()[0].strip(",.;:")
        if base and code:
            return f"{base}#{code}"
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


def pick_best_roster_match(input_name: str, roster_players: list[dict]):
    _, input_base = _bungie_name_variants(input_name)
    input_key = _bungie_name_key(input_name)
    if not input_base:
        return None

    exact = []
    starts = []
    contains = []

    for player in roster_players:
        candidate_name = (player.get("displayName") or "").strip()
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


def get_dungeon_name(reference_id):
    try:
        activity_hash = int(reference_id)
    except (TypeError, ValueError):
        return "Dungeon"

    if activity_hash in SPECIAL_SOLO_ACTIVITY_NAMES:
        return normalize_special_solo_activity_display_name(SPECIAL_SOLO_ACTIVITY_NAMES[activity_hash])
    if activity_hash in DUNGEON_NAMES:
        return DUNGEON_NAMES[activity_hash]

    # Bungie activity hashes can appear in signed or unsigned 32-bit form.
    if activity_hash < 0:
        unsigned_hash = activity_hash + (1 << 32)
        return DUNGEON_NAMES.get(unsigned_hash, "Dungeon")

    if activity_hash > 0x7FFFFFFF:
        signed_hash = activity_hash - (1 << 32)
        return DUNGEON_NAMES.get(signed_hash, "Dungeon")

    return "Dungeon"


async def resolve_activity_name(reference_id):
    known_name = get_dungeon_name(reference_id)
    if known_name and known_name != "Dungeon":
        return known_name

    try:
        activity_hash = int(reference_id)
    except (TypeError, ValueError):
        return "Dungeon"

    if bungie_api is None:
        return "Dungeon"

    try:
        definition = await bungie_api.get_activity_definition(activity_hash)
    except Exception:
        return "Dungeon"

    display_name = (definition.get("displayProperties") or {}).get("name")
    if not display_name:
        return "Dungeon"

    if activity_name_matches_special_solo_activity(display_name):
        return normalize_special_solo_activity_display_name(display_name)
    return str(display_name)


def resolve_dungeon_image_by_name(dungeon_name: str, guild_config):
    if not dungeon_name:
        return None

    def _normalize_lookup_key(value: str):
        text = str(value or "").casefold().strip()
        if not text:
            return ""
        text = re.sub(r"^exotic\s+mission\s*-\s*", "", text)
        text = re.sub(r"\s*:\s*(customize|standard|normal|master|legend|expert)\b", "", text)
        text = re.sub(r"\s*\((normal|master|legend|expert|customize|standard)\)\s*$", "", text)
        text = re.sub(r"^the\s+", "", text)
        token_set = {token for token in re.findall(r"[a-z0-9]+", text) if token}
        if {"pit", "heresy"}.issubset(token_set):
            return "pitofheresy"
        if {"sundered", "doctrine"}.issubset(token_set):
            return "sundereddoctrine"
        text = re.sub(r"[^a-z0-9]+", "", text)
        return text

    config = guild_config or {}
    custom_maps = [
        config.get("solo_dungeon_image_by_name") or {},
        config.get("team_dungeon_thumbnail_by_name") or {},
        config.get("dungeon_thumbnail_by_name") or {},
    ]
    normalized_target = str(dungeon_name).casefold().strip()
    normalized_target_compact = _normalize_lookup_key(dungeon_name)
    if normalized_target:
        for custom_map in custom_maps:
            for key, value in custom_map.items():
                if not key or not value:
                    continue
                normalized_key = str(key).casefold().strip()
                if normalized_key == normalized_target:
                    return str(value)
                if normalized_target_compact and _normalize_lookup_key(normalized_key) == normalized_target_compact:
                    return str(value)

    # Fallback: some activity names include extra prefixes/qualifiers
    # (e.g. "Grandmaster Conquest - Heliostat"), so match compact keys by containment.
    if normalized_target_compact:
        candidates = []
        for custom_map in custom_maps:
            for key, value in custom_map.items():
                if not key or not value:
                    continue
                compact_key = _normalize_lookup_key(key)
                if not compact_key:
                    continue
                if compact_key in normalized_target_compact or normalized_target_compact in compact_key:
                    candidates.append((len(compact_key), str(value)))
        if candidates:
            # Prefer the most specific (longest) compact key.
            candidates.sort(key=lambda item: item[0], reverse=True)
            return candidates[0][1]

    return None


async def resolve_solo_thumbnail_url(dungeon_name: str, guild_config, activity_hash=None):
    thumbnail_url = sanitize_thumbnail_url(resolve_dungeon_image_by_name(dungeon_name, guild_config))
    if thumbnail_url:
        return thumbnail_url

    activity_hash_int = None
    try:
        if activity_hash is not None:
            activity_hash_int = int(activity_hash)
    except (TypeError, ValueError):
        activity_hash_int = None

    # Retry map lookup via canonical hash mapping when title variants don't match.
    if activity_hash_int is not None:
        known_name = get_dungeon_name(activity_hash_int)
        if known_name and known_name != "Dungeon":
            mapped_thumbnail = sanitize_thumbnail_url(resolve_dungeon_image_by_name(known_name, guild_config))
            if mapped_thumbnail:
                return mapped_thumbnail

    if activity_hash_int is not None and bungie_api is not None:
        try:
            definition = await bungie_api.get_activity_definition(activity_hash_int)
            display_name = (definition.get("displayProperties") or {}).get("name")
            if display_name:
                if activity_name_matches_special_solo_activity(display_name):
                    display_name = normalize_special_solo_activity_display_name(display_name)
                display_name_thumbnail = sanitize_thumbnail_url(
                    resolve_dungeon_image_by_name(display_name, guild_config)
                )
                if display_name_thumbnail:
                    return display_name_thumbnail
            icon_path = (definition.get("displayProperties") or {}).get("icon")
            icon_url = sanitize_thumbnail_url(icon_path)
            if icon_url:
                return icon_url
        except Exception:
            pass

    config_fallback = sanitize_thumbnail_url((guild_config or {}).get("dungeon_embed_image"))
    if config_fallback:
        print(
            f"[STS_SOLO_THUMBNAIL] Using guild fallback image "
            f"activity_hash={activity_hash} dungeon={dungeon_name}"
        )
        return config_fallback

    default_fallback = sanitize_thumbnail_url(DEFAULT_DUNGEON_THUMBNAIL_URL) or "https://i.postimg.cc/vBbCWrts/Dark-Skull.gif"
    print(
        f"[STS_SOLO_THUMBNAIL] Using global fallback image "
        f"activity_hash={activity_hash} dungeon={dungeon_name}"
    )
    return default_fallback


def should_hide_status_for_short_exotic_run(activity_name: str | None, duration_seconds) -> bool:
    name_text = str(activity_name or "").casefold().strip()
    return name_text.startswith("exotic mission -")


def is_pantheon_activity_name(activity_name: str | None):
    lowered = str(activity_name or "").casefold().strip()
    if not lowered:
        return False
    pantheon_tokens = {
        "pantheon",
        "calus resplendent",
        "morgeth surpassing",
        "insurrection prime revolutionary",
        "insurrection prime",
    }
    return any(token in lowered for token in pantheon_tokens)


async def resolve_feat_names_from_pgcr(pgcr):
    selected_hashes = list((pgcr or {}).get("selectedSkullHashes") or [])
    if not selected_hashes or bungie_api is None:
        return []

    feat_names = []
    seen = set()
    for raw_hash in selected_hashes:
        try:
            modifier_hash = int(raw_hash)
        except (TypeError, ValueError):
            continue

        if modifier_hash in pantheon_modifier_name_cache:
            resolved_name = pantheon_modifier_name_cache[modifier_hash]
        else:
            try:
                definition = await bungie_api.get_activity_modifier_definition(modifier_hash)
            except Exception:
                definition = {}
            resolved_name = str((definition.get("displayProperties") or {}).get("name") or "").strip()
            pantheon_modifier_name_cache[modifier_hash] = resolved_name

        lowered = resolved_name.casefold()
        if not lowered or lowered in seen:
            continue
        seen.add(lowered)
        feat_names.append(resolved_name)

    return feat_names


def parse_bungie_profile_input(value: str):
    if not value:
        return None
    text = str(value).strip()
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


async def resolve_player(global_name: str):
    if bungie_api is None:
        return None

    memberships = await bungie_api.search_global_name(global_name.strip())
    if not memberships:
        return None

    for membership in memberships:
        membership_id = membership.get("membershipId")
        membership_type = membership.get("membershipType")
        if not membership_id or membership_type is None:
            continue

        try:
            profile = await bungie_api.get_profile(membership_type, membership_id)
        except Exception:
            continue

        characters = profile.get("characters", {}).get("data", {})
        character_ids = list(characters.keys())
        if not character_ids:
            continue

        display_name = membership.get("bungieGlobalDisplayName") or membership.get("displayName") or global_name
        display_code = membership.get("bungieGlobalDisplayNameCode")
        if display_code is not None:
            try:
                display_name = f"{display_name}#{int(display_code):04d}"
            except (TypeError, ValueError):
                display_name = f"{display_name}#{display_code}"

        return {
            "global_name": display_name,
            "membership_id": str(membership_id),
            "membership_type": int(membership_type),
            "character_ids": character_ids,
            "avatar_url": normalize_bungie_image_url(
                membership.get("iconPath") or membership.get("profilePicturePath")
            ),
        }
    return None


async def resolve_player_from_membership(membership_id, membership_type, display_name="Guardian", avatar_url=None):
    if bungie_api is None:
        return None

    profile = await bungie_api.get_profile(membership_type, membership_id)
    characters = profile.get("characters", {}).get("data", {})
    character_ids = list(characters.keys())
    if not character_ids:
        return None

    return {
        "global_name": display_name,
        "membership_id": str(membership_id),
        "membership_type": int(membership_type),
        "character_ids": character_ids,
        "avatar_url": normalize_bungie_image_url(avatar_url),
    }


async def get_clan_roster_players():
    if bungie_api is None or not BUNGIE_CLAN_ID:
        return []
    try:
        return await bungie_api.get_clan_roster(BUNGIE_CLAN_ID)
    except Exception:
        return []


async def get_clan_watch_players():
    roster = await get_clan_roster_players()
    players = []
    for member in roster:
        membership_id = member.get("membershipId")
        membership_type = member.get("membershipType")
        if not membership_id or membership_type is None:
            continue
        try:
            resolved = await resolve_player_from_membership(
                membership_id=membership_id,
                membership_type=membership_type,
                display_name=member.get("displayName") or "Guardian",
                avatar_url=member.get("avatarUrl"),
            )
        except Exception:
            resolved = None
        if resolved:
            players.append(resolved)
    return players


async def get_server_watch_players_for_guild(guild: discord.Guild):
    players = []
    seen = set()
    clan_roster = await get_clan_roster_players()

    for member in guild.members:
        if member.bot:
            continue

        resolved = None
        for name in [member.display_name, member.name]:
            if not normalize_member_name(name):
                continue
            try:
                resolved = await resolve_player(name)
            except Exception:
                resolved = None
            if resolved:
                break

        if not resolved and clan_roster:
            for name in [member.display_name, member.name]:
                if not normalize_member_name(name):
                    continue
                roster_match = pick_best_roster_match(name, clan_roster)
                if not roster_match:
                    continue
                membership_id = roster_match.get("membershipId")
                membership_type = roster_match.get("membershipType")
                if not membership_id or membership_type is None:
                    continue
                try:
                    resolved = await resolve_player_from_membership(
                        membership_id,
                        membership_type,
                        display_name=roster_match.get("displayName") or name,
                        avatar_url=roster_match.get("avatarUrl"),
                    )
                except Exception:
                    resolved = None
                if resolved:
                    break

        if not resolved:
            continue

        key = (str(resolved.get("membership_type")), str(resolved.get("membership_id")))
        if key in seen:
            continue
        seen.add(key)
        players.append(resolved)

    return players


async def get_profile_watch_players_for_guild(guild_config):
    players = []
    for item in list((guild_config or {}).get("dungeon_watch_profiles", []) or []):
        profile_ref = parse_bungie_profile_input(str(item))
        if profile_ref is None:
            continue
        try:
            resolved = await resolve_player_from_membership(
                membership_id=profile_ref["membership_id"],
                membership_type=profile_ref["membership_type"],
                display_name="Guardian",
            )
        except Exception:
            resolved = None
        if resolved:
            players.append(resolved)
    return players


def merge_unique_players(*player_groups):
    merged = []
    seen = set()
    for group in player_groups:
        for player in group or []:
            key = (str(player.get("membership_type")), str(player.get("membership_id")))
            if key in seen:
                continue
            seen.add(key)
            merged.append(player)
    return merged


async def detect_new_solo_dungeon_completions(player, seen_instance_ids):
    completions = []
    membership_id = player.get("membership_id")
    membership_type = player.get("membership_type")
    activity_name_cache = {}
    scanned_instance_ids = set()

    for character_id in player.get("character_ids", []):
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
                        continue
                    scanned_instance_ids.add(instance_id)

                    activity_hash = details.get("referenceId")
                    activity_hash_int = None
                    try:
                        activity_hash_int = int(activity_hash)
                    except (TypeError, ValueError):
                        activity_hash_int = None

                    is_special_by_hash = False
                    if activity_hash_int is not None:
                        candidate_hashes = {activity_hash_int}
                        if activity_hash_int < 0:
                            candidate_hashes.add(activity_hash_int + (1 << 32))
                        elif activity_hash_int > 0x7FFFFFFF:
                            candidate_hashes.add(activity_hash_int - (1 << 32))
                        is_special_by_hash = any(
                            candidate_hash in SPECIAL_SOLO_ACTIVITY_NAMES
                            for candidate_hash in candidate_hashes
                        )

                    if mode is None or mode != 82:
                        activity_name = activity_name_cache.get(activity_hash_int)
                        if activity_name is None and activity_hash_int is not None:
                            try:
                                definition = await bungie_api.get_activity_definition(activity_hash_int)
                                activity_name = (definition.get("displayProperties") or {}).get("name")
                            except Exception:
                                activity_name = ""
                            activity_name_cache[activity_hash_int] = activity_name or ""

                        if not is_special_by_hash and not activity_name_matches_special_solo_activity(activity_name):
                            continue

                    values = activity.get("values", {})
                    completed = values.get("completed", {}).get("basic", {}).get("value", 0) == 1
                    if not completed:
                        continue

                    pgcr = await bungie_api.get_pgcr(instance_id)
                    entries = pgcr.get("entries", [])
                    player_membership_id = str(membership_id)
                    participant_ids = {
                        str((entry.get("player", {}).get("destinyUserInfo", {}).get("membershipId", ""))).strip()
                        for entry in entries
                        if entry.get("player", {}).get("destinyUserInfo", {}).get("membershipId")
                    }
                    is_solo = participant_ids == {player_membership_id} if participant_ids else len(entries) == 1
                    if not is_solo:
                        continue

                    deaths_value = values.get("deaths", {}).get("basic", {}).get("value")
                    kills_value = values.get("kills", {}).get("basic", {}).get("value")
                    duration_value = values.get("activityDurationSeconds", {}).get("basic", {}).get("value")

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
                        pass

                    completions.append(
                        {
                            "instance_id": instance_id,
                            "period": activity.get("period"),
                            "activity_hash": activity_hash,
                            "flawless": is_flawless,
                            "solo": True,
                            "deaths": deaths_value,
                            "kills": kills_value,
                            "duration_seconds": duration_seconds,
                            "feat_names": await resolve_feat_names_from_pgcr(pgcr),
                            "was_started_from_beginning": pgcr.get("activityWasStartedFromBeginning", True),
                        }
                    )

    completions.sort(key=lambda item: item.get("period", ""))
    return completions


async def post_solo_completion(guild: discord.Guild, channel_id: str, player, completion):
    channel = await bot.fetch_channel(int(channel_id))
    if getattr(channel, "guild", None) is None or channel.guild.id != guild.id:
        return

    instance_id = str(completion.get("instance_id") or "").strip()
    if instance_id:
        latest_guild_config = get_guild_config(rules_config, guild.id)
        latest_posted_ids = set(latest_guild_config.get("dungeon_posted_instance_ids", []))
        if instance_id in latest_posted_ids:
            print(f"[STS_SOLO] Skipping duplicate solo completion instance_id={instance_id} guild={guild.id}")
            return

    player_name = player.get("global_name", "Guardian")
    dungeon_name = await resolve_activity_name(completion.get("activity_hash"))
    guild_config = get_guild_config(rules_config, guild.id)
    thumbnail_url = await resolve_solo_thumbnail_url(
        dungeon_name,
        guild_config,
        completion.get("activity_hash"),
    )
    embed = build_solo_embed(
        player_name,
        normalize_bungie_image_url(player.get("avatar_url")),
        dungeon_name,
        bool(completion.get("flawless", False)),
        thumbnail_url=thumbnail_url,
    )
    embed.add_field(name="Kills", value=format_int_stat(completion.get("kills")) or "-", inline=True)
    embed.add_field(name="Deaths", value=format_int_stat(completion.get("deaths")) or "-", inline=True)
    embed.add_field(name="Time", value=format_duration(completion.get("duration_seconds")) or "-", inline=True)
    status = "Full" if completion.get("was_started_from_beginning", True) else "Checkpoint"
    hide_status = should_hide_status_for_short_exotic_run(dungeon_name, completion.get("duration_seconds"))
    if not hide_status:
        embed.add_field(name="Status", value=status, inline=True)
    feat_names = [str(name).strip() for name in (completion.get("feat_names") or []) if str(name).strip()]
    if is_pantheon_activity_name(dungeon_name) and feat_names:
        embed.add_field(name="Feats", value=" | ".join(feat_names), inline=True)

    await channel.send(embed=embed)


async def run_solo_check_for_guild(guild: discord.Guild):
    return await run_solo_check_for_guild_with_cache(guild)


async def run_solo_check_for_guild_with_cache(
    guild: discord.Guild,
    watched_override=None,
    completions_cache=None,
    cache_seen_instance_ids=None,
):
    if not is_allowed_guild(guild.id):
        return

    guild_config = get_guild_config(rules_config, guild.id)
    channel_id = guild_config.get("dungeon_channel_id")
    if not channel_id:
        return

    # STS mode: clan roster only (no server-member scanning).
    watched = watched_override if watched_override is not None else await get_clan_watch_players()
    if not watched:
        return

    now = datetime.now(timezone.utc)
    threshold_dt = now - timedelta(seconds=INITIAL_BASELINE_POST_WINDOW_SECONDS)
    post_not_before_dt = now - timedelta(minutes=SOLO_POST_DELAY_MINUTES)

    seen_instance_ids = set(guild_config.get("dungeon_seen_instance_ids", []))
    posted_instance_ids = set(guild_config.get("dungeon_posted_instance_ids", []))
    processed_instance_ids = set()
    mark_seen_ids = set()
    mark_posted_ids = set()

    for player in watched:
        player_key = (
            str(player.get("membership_type") or ""),
            str(player.get("membership_id") or ""),
        )
        completions = None
        if completions_cache is not None and player_key in completions_cache:
            completions = completions_cache[player_key]
        if completions is None:
            detection_seen_ids = posted_instance_ids
            if cache_seen_instance_ids is not None:
                detection_seen_ids = cache_seen_instance_ids
            completions = await detect_new_solo_dungeon_completions(player, detection_seen_ids)
            if completions_cache is not None:
                completions_cache[player_key] = completions

        for completion in completions:
            instance_id = completion.get("instance_id")
            if (
                not instance_id
                or instance_id in posted_instance_ids
                or instance_id in processed_instance_ids
            ):
                continue
            processed_instance_ids.add(instance_id)

            completion_dt = parse_bungie_timestamp(completion.get("period"))
            if completion_dt is None or completion_dt < threshold_dt:
                mark_seen_ids.add(instance_id)
                seen_instance_ids.add(instance_id)
                continue
            if completion_dt > post_not_before_dt:
                continue

            await post_solo_completion(guild, channel_id, player, completion)
            mark_seen_ids.add(instance_id)
            mark_posted_ids.add(instance_id)
            seen_instance_ids.add(instance_id)
            posted_instance_ids.add(instance_id)

    guild_config["dungeon_last_checked_at"] = now.isoformat()
    if mark_seen_ids:
        history = list(guild_config.get("dungeon_seen_instance_ids", []))
        history_set = set(history)
        for instance_id in mark_seen_ids:
            if instance_id not in history_set:
                history.append(instance_id)
                history_set.add(instance_id)
        guild_config["dungeon_seen_instance_ids"] = history[-500:]

    if mark_posted_ids:
        posted_history = list(guild_config.get("dungeon_posted_instance_ids", []))
        posted_history_set = set(posted_history)
        for instance_id in mark_posted_ids:
            if instance_id not in posted_history_set:
                posted_history.append(instance_id)
                posted_history_set.add(instance_id)
        guild_config["dungeon_posted_instance_ids"] = posted_history[-500:]

    rules_config[str(guild.id)] = guild_config
    save_config(rules_config)


@tasks.loop(minutes=SOLO_CHECK_INTERVAL_MINUTES)
async def solo_loop():
    if bungie_api is None:
        return

    allowed_guilds = [guild for guild in bot.guilds if is_allowed_guild(guild.id)]
    print(f"[STS_SOLO_LOOP] Starting cycle for {len(allowed_guilds)} guild(s)")
    if not allowed_guilds:
        print("[STS_SOLO_LOOP] No allowed guilds in this bot session")
        return

    watched = await get_clan_watch_players()
    if not watched:
        print("[STS_SOLO_LOOP] No clan watch players resolved for this cycle")
        return

    posted_union = set()
    for guild in allowed_guilds:
        guild_config = get_guild_config(rules_config, guild.id)
        posted_union.update(guild_config.get("dungeon_posted_instance_ids", []))

    completions_cache = {}
    for guild in allowed_guilds:
        try:
            await run_solo_check_for_guild_with_cache(
                guild,
                watched_override=watched,
                completions_cache=completions_cache,
                cache_seen_instance_ids=posted_union,
            )
        except Exception as e:
            print(f"[STS_SOLO_LOOP] Guild {guild.id} failed: {e}")
    print("[STS_SOLO_LOOP] Cycle complete")


@solo_loop.before_loop
async def before_solo_loop():
    await bot.wait_until_ready()


async def resolve_text_channel_from_input(guild: discord.Guild, channel_input: str):
    raw = str(channel_input or "").strip()
    if not raw:
        return None

    if raw.startswith("<#") and raw.endswith(">"):
        raw = raw[2:-1].strip()

    if raw.isdigit():
        channel_id = int(raw)
        cached = guild.get_channel(channel_id)
        if isinstance(cached, discord.TextChannel):
            return cached
        try:
            fetched = await bot.fetch_channel(channel_id)
            if isinstance(fetched, discord.TextChannel) and fetched.guild.id == guild.id:
                return fetched
        except Exception:
            return None

    lowered = raw.casefold()
    for text_channel in guild.text_channels:
        if text_channel.name.casefold() == lowered:
            return text_channel

    for text_channel in guild.text_channels:
        if lowered in text_channel.name.casefold():
            return text_channel

    return None


@bot.event
async def on_ready():
    global bungie_api
    global startup_solo_scan_done
    print(f"D2 Solo bot logged in as {bot.user}")

    if TOKEN is None:
        print("Missing token. Set STS_DISCORD_TOKEN or DISCORD_TOKEN.")
        return

    if bungie_api is None and BUNGIE_API_KEY:
        bungie_api = BungieAPI(BUNGIE_API_KEY)

    # Syncs your command tree (including the new /testembed command)
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} D2 Solo command(s)")
        for cmd in synced:
            print(f" -> Registered command: /{cmd.name}")
    except Exception as e:
        print(f"Failed to sync command tree: {e}")

    if not startup_solo_scan_done:
        allowed_guilds = [guild for guild in bot.guilds if is_allowed_guild(guild.id)]
        watched = await get_clan_watch_players()
        posted_union = set()
        for guild in allowed_guilds:
            guild_config = get_guild_config(rules_config, guild.id)
            posted_union.update(guild_config.get("dungeon_posted_instance_ids", []))
        completions_cache = {}

        for guild in allowed_guilds:
            try:
                await run_solo_check_for_guild_with_cache(
                    guild,
                    watched_override=watched,
                    completions_cache=completions_cache,
                    cache_seen_instance_ids=posted_union,
                )
            except Exception as e:
                print(f"Startup solo check failed for guild {guild.id}: {e}")
        startup_solo_scan_done = True

    if not solo_loop.is_running():
        solo_loop.start()


@bot.tree.command(name="set_dungeon_channel", description="Set solo/flawless announcements to this channel.")
async def set_dungeon_channel(interaction: discord.Interaction):
    if interaction.guild_id is None or interaction.guild is None:
        await interaction.response.send_message("This command must be used in a server.", ephemeral=True)
        return

    if not is_allowed_guild(interaction.guild_id):
        await interaction.response.send_message("This bot is only enabled for Seal Team Six servers.", ephemeral=True)
        return

    target_channel = interaction.channel if isinstance(interaction.channel, discord.TextChannel) else None

    if target_channel is None:
        await interaction.response.send_message("Could not resolve a text channel.", ephemeral=True)
        return

    guild_config = get_guild_config(rules_config, interaction.guild_id)
    guild_config["dungeon_channel_id"] = str(target_channel.id)
    rules_config[str(interaction.guild_id)] = guild_config
    save_config(rules_config)
    await interaction.response.send_message(
        f"Solo/flawless dungeon channel set to {target_channel.mention}.",
        ephemeral=True,
    )


@bot.tree.command(name="set_dungeon_channel_by_id", description="Set the solo/flawless channel by ID, mention, or channel name.")
@app_commands.describe(channel="Channel ID, #mention, or channel name")
async def set_dungeon_channel_by_id(interaction: discord.Interaction, channel: str):
    if interaction.guild_id is None or interaction.guild is None:
        await interaction.response.send_message("This command must be used in a server.", ephemeral=True)
        return

    if not is_allowed_guild(interaction.guild_id):
        await interaction.response.send_message("This bot is only enabled for Seal Team Six servers.", ephemeral=True)
        return

    target_channel = await resolve_text_channel_from_input(interaction.guild, channel)
    if target_channel is None:
        await interaction.response.send_message(
            "Could not find that text channel. Use a channel ID, #mention, or channel name.",
            ephemeral=True,
        )
        return

    guild_config = get_guild_config(rules_config, interaction.guild_id)
    guild_config["dungeon_channel_id"] = str(target_channel.id)
    rules_config[str(interaction.guild_id)] = guild_config
    save_config(rules_config)
    await interaction.response.send_message(
        f"Solo/flawless dungeon channel set to {target_channel.mention}.",
        ephemeral=True,
    )


@bot.tree.command(name="unset_dungeon_channel", description="Unset the solo/flawless dungeon channel.")
async def unset_dungeon_channel(interaction: discord.Interaction):
    if interaction.guild_id is None:
        await interaction.response.send_message("This command must be used in a server.", ephemeral=True)
        return

    if not is_allowed_guild(interaction.guild_id):
        await interaction.response.send_message("This bot is only enabled for Seal Team Six servers.", ephemeral=True)
        return

    guild_config = get_guild_config(rules_config, interaction.guild_id)
    guild_config.pop("dungeon_channel_id", None)
    rules_config[str(interaction.guild_id)] = guild_config
    save_config(rules_config)
    await interaction.response.send_message("Solo/flawless dungeon channel has been unset.", ephemeral=True)


@bot.tree.command(
    name="set_raid_weapon_channel",
    description="Set the default channel for /raid_weapons posts.",
)
@app_commands.checks.has_permissions(manage_channels=True)
@app_commands.describe(channel="Optional channel ID, #mention, or name. Leave empty to use this channel.")
async def set_raid_weapon_channel(
    interaction: discord.Interaction,
    channel: str | None = None,
):
    if interaction.guild_id is None or interaction.guild is None:
        await interaction.response.send_message("This command must be used in a server.", ephemeral=True)
        return

    if not is_allowed_guild(interaction.guild_id):
        await interaction.response.send_message("This bot is only enabled for Seal Team Six servers.", ephemeral=True)
        return

    target_channel = await resolve_text_channel_from_input(interaction.guild, channel)
    if target_channel is None:
        current_channel = interaction.channel
        if isinstance(current_channel, discord.TextChannel) and (channel is None or not str(channel).strip()):
            target_channel = current_channel

    if target_channel is None or target_channel.guild.id != interaction.guild_id:
        await interaction.response.send_message(
            "Could not find that text channel. Use a channel ID, #mention, or channel name.",
            ephemeral=True,
        )
        return

    guild_config = get_guild_config(rules_config, interaction.guild_id)
    guild_config["raid_weapon_channel_id"] = str(target_channel.id)
    rules_config[str(interaction.guild_id)] = guild_config
    save_config(rules_config)
    await interaction.response.send_message(
        f"Raid weapon channel set to {target_channel.mention}.",
        ephemeral=True,
    )


@bot.tree.command(
    name="unset_raid_weapon_channel",
    description="Unset the default channel for /raid_weapons posts.",
)
@app_commands.checks.has_permissions(manage_channels=True)
async def unset_raid_weapon_channel(interaction: discord.Interaction):
    if interaction.guild_id is None:
        await interaction.response.send_message("This command must be used in a server.", ephemeral=True)
        return

    if not is_allowed_guild(interaction.guild_id):
        await interaction.response.send_message("This bot is only enabled for Seal Team Six servers.", ephemeral=True)
        return

    guild_config = get_guild_config(rules_config, interaction.guild_id)
    if "raid_weapon_channel_id" in guild_config:
        guild_config.pop("raid_weapon_channel_id", None)
        rules_config[str(interaction.guild_id)] = guild_config
        save_config(rules_config)

    await interaction.response.send_message("Raid weapon channel has been unset.", ephemeral=True)


@bot.tree.command(
    name="raid_weapons",
    description="Show raid weapons and where they drop by encounter.",
)
@app_commands.describe(
    raid="Raid to show weapon drops for",
)
@app_commands.choices(
    raid=[
        app_commands.Choice(name=choice_name, value=choice_value)
        for choice_name, choice_value in get_raid_weapon_choices()
    ]
)
async def raid_weapons(
    interaction: discord.Interaction,
    raid: app_commands.Choice[str],
):
    if interaction.guild_id is None:
        await interaction.response.send_message("This command must be used in a server.", ephemeral=True)
        return

    if not is_allowed_guild(interaction.guild_id):
        await interaction.response.send_message("This bot is only enabled for Seal Team Six servers.", ephemeral=True)
        return

    target_channel = interaction.channel
    if not isinstance(target_channel, discord.TextChannel):
        await interaction.response.send_message(
            "Please use this command in a text channel.",
            ephemeral=True,
        )
        return

    if target_channel.guild.id != interaction.guild_id:
        await interaction.response.send_message(
            "The selected channel must be in this server.",
            ephemeral=True,
        )
        return

    embed = build_raid_weapons_embed(raid.value)
    if embed is None:
        await interaction.response.send_message(
            "No raid weapon table found for that raid yet.",
            ephemeral=True,
        )
        return

    await interaction.response.send_message(embed=embed)


@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingPermissions):
        message = "You need **Manage Channels** permission to use that command."
    elif isinstance(error, app_commands.CheckFailure):
        message = "You don’t have permission to use that command."
    else:
        print(f"[STS_SOLO] App command error: {error}")
        message = "Something went wrong while running that command. Please try again."

    try:
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)
    except Exception as followup_error:
        print(f"[STS_SOLO] Failed to send app command error response: {followup_error}")


@bot.event
async def on_close():
    if bungie_api is not None:
        await bungie_api.close()


def run():
    if not TOKEN:
        raise RuntimeError("Missing STS_DISCORD_TOKEN (or DISCORD_TOKEN).")
    bot.run(TOKEN)


if __name__ == "__main__":
    run()
