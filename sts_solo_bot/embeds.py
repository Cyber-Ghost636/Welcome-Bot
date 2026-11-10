import discord
import random


SOLO_DUNGEON_TEXT_LINES = {
    "dungeon_flawless": [
        "Not a single death. Dungeon audited and closed.",
        "Flawless and fearless. The dungeon had no answers.",
        "No wipes, no mercy, no survivors.",
        "Deathless run. Controller still in one piece.",
    ],
    "dungeon_non_flawless": [
        "A little scuffed, a lot legendary.",
        "A few bruises, still a solo victory.",
        "Wobbled once, finished strong.",
        "A few setbacks, still a great solo clear.",
    ],
    "exotic_flawless": [
        "Deathless exotic run. Someone tell the Vanguard.",
        "Flawless exotic clear. No notes.",
        "Zero deaths, maximum flex.",
        "Clean exotic mission. Bosses taking emotional damage.",
    ],
    "exotic_non_flawless": [
        "Scuffed the route, still got the clear.",
        "Not flawless, still cinematic.",
        "A little chaos, mission still complete.",
        "Respawns happened. Victory happened too.",
    ],
}


def build_solo_activity_announcement_line(is_exotic_mission: bool, is_flawless: bool):
    if is_exotic_mission:
        key = "exotic_flawless" if is_flawless else "exotic_non_flawless"
    else:
        key = "dungeon_flawless" if is_flawless else "dungeon_non_flawless"

    options = [str(line).strip() for line in SOLO_DUNGEON_TEXT_LINES.get(key, []) if str(line).strip()]
    if not options:
        return None
    return random.choice(options)


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


def build_solo_embed(
    player_name: str,
    player_avatar: str | None,
    dungeon_name: str,
    is_flawless: bool,
    thumbnail_url: str | None = None,
) -> discord.Embed:
    is_exotic_mission = str(dungeon_name or "").casefold().startswith("exotic mission -")
    color = discord.Color.gold() if is_flawless else discord.Color.orange()
    announcement_line = build_solo_activity_announcement_line(is_exotic_mission, is_flawless)
    if is_flawless:
        description = f"Solo flawless by {player_name}."
        if announcement_line:
            description = f"{description} {announcement_line}"
    else:
        description = f"Solo clear by {player_name}."
        if announcement_line:
            description = f"{description} {announcement_line}"

    embed = discord.Embed(
        title=dungeon_name,
        description=description,
        color=color,
    )
    if player_avatar:
        embed.set_author(name=player_name, icon_url=player_avatar)
    else:
        embed.set_author(name=player_name)
    if thumbnail_url:
        embed.set_thumbnail(url=thumbnail_url)
    embed.set_footer(text="#via D2 Solo")
    return embed
