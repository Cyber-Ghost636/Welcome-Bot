import discord
import random


SOLO_DUNGEON_TEXT_LINES = {
    "dungeon_flawless": [
        "Not a single death. Dungeon audited and closed.",
        "Flawless and fearless. The dungeon had no answers.",
        "No wipes, no mercy, no survivors.",
        "Deathless run. Controller still in one piece.",
        "Perfect run. The wipe screen stayed unemployed.",
        "Solo flawless secured. Loot acquired, dignity intact.",
        "No deaths, no drama, just immaculate mechanics.",
        "Flawless clear. The dungeon filed a complaint.",
        "That wasn't a clear, that was a public execution.",
        "Fireteam size: 1. Respect demanded: maximum.",
        "The wipe screen filed for unemployment.",
        "A flawless so rude it should be bannable.",
        "One Guardian, zero sympathy.",
        "No fireteam, no fear, no negotiations.",
        "Encounter difficulty downgraded to spectator mode.",
        "The mission timeline now reads: before you / after you.",
        "Solo clear complete. Enemy morale unavailable.",
        "Another encounter added to your tax bracket.",
    ],
    "dungeon_non_flawless": [
        "A little scuffed, a lot legendary.",
        "A few bruises, still a solo victory.",
        "Wobbled once, finished strong.",
        "A few setbacks, still a great solo clear.",
        "Took the scenic route to victory.",
        "Messy in places, legendary in all the right ones.",
        "A couple of oops moments, still a huge clear.",
        "Not flawless, still a highlight reel ending.",
        "Not flawless? Still a one-Guardian war crime.",
        "Respawn counter got farmed, boss still got humbled.",
        "Mechanics explained. Consequences delivered.",
        "Difficulty selected: yes.",
        "The encounter started confident and ended as a lesson.",
        "Wipe screen watched from the bench all game.",
        "Boss got outplayed, outpaced, and out of excuses.",
        "That clear had villain energy.",
        "Another boss learned that health bars are suggestions.",
        "The boss brought phases. You brought deadlines.",
    ],
    "exotic_flawless": [
        "Deathless exotic run. Someone tell the Vanguard.",
        "Flawless exotic clear. No notes.",
        "Zero deaths, maximum flex.",
        "Clean exotic mission. Bosses taking emotional damage.",
        "Exotic flawless achieved. The Traveler is watching.",
        "No respawns needed. Mission status: dominated.",
        "One take, no mistakes, full exotic glory.",
        "Deathless exotic clear. Clipboard signed and stamped.",
        "Loot dropped out of fear.",
        "They brought mechanics. You brought violence.",
        "Solo run so clean the enemies reported suspicious activity.",
        "Boss got solo'd and sent to orbit emotionally.",
        "Boss mechanics met a one-Guardian reality check.",
        "You didn't run the activity, you repossessed it.",
        "Solo execution complete. Loot released under pressure.",
        "Bungie designed it; you redesigned its confidence.",
    ],
    "exotic_non_flawless": [
        "Scuffed the route, still got the clear.",
        "Not flawless, still cinematic.",
        "A little chaos, mission still complete.",
        "Respawns happened. Victory happened too.",
        "Some turbulence, smooth landing at the chest.",
        "Not perfect execution, perfect commitment.",
        "Wipes occurred. Adaptation occurred. Clear achieved.",
        "Bit of chaos, lot of clutch.",
        "The mission had a plan. Then you happened.",
        "Raid boss behavior, solo player budget.",
        "That chest opened out of pure survival instinct.",
        "Another boss added to the talked-too-much list.",
        "Solo clear so disrespectful it should need a warning label.",
        "They built an arena. You built a highlight reel.",
        "Enemy AI currently filing emotional damage reports.",
        "The chest opened like it owed you money.",
        "You turned a raid mechanic into a personal side quest.",
        "You didn't beat the encounter — you corrected it.",
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
