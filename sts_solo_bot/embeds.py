import discord


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
    if is_flawless:
        description = f"Flawless solo by {player_name}! Stuffed the boss in a locker and walked out flawless."
    else:
        if is_exotic_mission:
            description = f"{player_name} finished the exotic mission with some scars, but got it done."
        else:
            description = f"{player_name} solo cleared the dungeon with some scars, but got it done."

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
    return embed
