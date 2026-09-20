
import unicodedata
import re
from datetime import datetime, timedelta, timezone

import discord
from discord import app_commands
from discord.ext import commands, tasks

from bungie_api import BungieAPI, normalize_bungie_image_url
from pinnacle_ops import (
    SPECIAL_SOLO_ACTIVITY_NAMES,
    activity_name_matches_special_solo_activity,
    normalize_special_solo_activity_display_name,
)
from .config import (
    BUNGIE_API_KEY,
    BUNGIE_CLAN_ID,
    DEFAULT_DUNGEON_THUMBNAIL_URL,
    DUNGEON_HISTORY_COUNT,
    GOLD_CUP_PATH,
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


bot = commands.Bot(command_prefix="/", intents=discord.Intents(guilds=True, members=True))
rules_config = load_config()
bungie_api = None
startup_solo_scan_done = False


DUNGEON_NAMES = {
    1375089621: "Shattered Throne",
    2032534090: "Shattered Throne",
    1042180643: "Pit of Heresy",
    2582501063: "Pit of Heresy",
    2296816009: "Prophecy",
    3637651331: "Prophecy",
    1077850348: "Prophecy",
    4078656646: "Grasp of Avarice",
    3132816111: "Duality",
    3635748805: "Duality",  # Added Normal Difficulty
    1826478593: "Duality",  # Added Master Difficulty
    -659218491: "Duality",  # Signed 32-bit Normal Difficulty
    422102671: "Spire of the Watcher",
    1225969316: "Spire of the Watcher",
    1262462921: "Spire of the Watcher",
    1451777341: "Ghosts of the Deep",
    1094262727: "Ghosts of the Deep",
    313828469: "Ghosts of the Deep",
    3732368189: "Warlord's Ruin",
    2004855007: "Warlord's Ruin",
    3138284691: "Vesper's Host",
    300092127: "Vesper's Host",
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

    fallback = config.get("dungeon_embed_image") or DEFAULT_DUNGEON_THUMBNAIL_URL
    fallback_text = str(fallback or "").strip()
    return fallback_text or None


def should_hide_status_for_short_exotic_run(activity_name: str | None, duration_seconds) -> bool:
    name_text = str(activity_name or "").casefold().strip()
    if not name_text.startswith("exotic mission -"):
        return False
    try:
        duration_value = int(duration_seconds)
    except (TypeError, ValueError):
        return False
    return duration_value < 10 * 60


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
                    if mode is None or mode != 82:
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
                            "was_started_from_beginning": pgcr.get("activityWasStartedFromBeginning", True),
                        }
                    )

    completions.sort(key=lambda item: item.get("period", ""))
    return completions


async def post_solo_completion(guild: discord.Guild, channel_id: str, player, completion):
    channel = await bot.fetch_channel(int(channel_id))
    if getattr(channel, "guild", None) is None or channel.guild.id != guild.id:
        return

    player_name = player.get("global_name", "Guardian")
    dungeon_name = get_dungeon_name(completion.get("activity_hash"))
    guild_config = get_guild_config(rules_config, guild.id)
    thumbnail_url = resolve_dungeon_image_by_name(dungeon_name, guild_config)
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

    await channel.send(embed=embed)


async def run_solo_check_for_guild(guild: discord.Guild):
    if not is_allowed_guild(guild.id):
        return

    guild_config = get_guild_config(rules_config, guild.id)
    channel_id = guild_config.get("dungeon_channel_id")
    if not channel_id:
        return

    # STS mode: clan roster only (no server-member scanning).
    watched = await get_clan_watch_players()
    if not watched:
        return

    now = datetime.now(timezone.utc)
    threshold_dt = now - timedelta(seconds=INITIAL_BASELINE_POST_WINDOW_SECONDS)
    post_not_before_dt = now - timedelta(minutes=SOLO_POST_DELAY_MINUTES)

    seen_instance_ids = set(guild_config.get("dungeon_seen_instance_ids", []))
    posted_instance_ids = set(guild_config.get("dungeon_posted_instance_ids", []))
    mark_seen_ids = set()
    mark_posted_ids = set()

    for player in watched:
        completions = await detect_new_solo_dungeon_completions(player, posted_instance_ids)
        for completion in completions:
            instance_id = completion.get("instance_id")
            if not instance_id or instance_id in posted_instance_ids:
                continue

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
    for guild in bot.guilds:
        await run_solo_check_for_guild(guild)


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
        for guild in bot.guilds:
            try:
                await run_solo_check_for_guild(guild)
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
