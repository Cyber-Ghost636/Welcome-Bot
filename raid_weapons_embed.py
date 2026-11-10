from __future__ import annotations

import discord


RAID_WEAPON_TABLE = {
    "deep_stone_crypt": {
        "display_name": "Deep Stone Crypt",
        "thumbnail_url": "https://www.bungie.net/common/destiny2_content/icons/4cf7f7f8fb1ff84709fddf4b2f6fcf34.jpg",
        "encounters": [
            {
                "name": "Crypt Security",
                "weapons": ["Trustee (Scout Rifle)", "Heritage (Shotgun)"],
            },
            {
                "name": "Atraks-1, Fallen Exo",
                "weapons": ["Succession (Sniper Rifle)"],
            },
            {
                "name": "Descent",
                "weapons": ["Posterity (Hand Cannon)", "Bequest (Sword)"],
            },
            {
                "name": "Taniks, the Abomination",
                "weapons": ["Commemoration (Machine Gun)", "Eyes of Tomorrow (Exotic Rocket Launcher)"],
            },
        ],
    },
    "root_of_nightmares": {
        "display_name": "Root of Nightmares",
        "encounters": [
            {
                "name": "Cataclysm",
                "weapons": [
                    "Nessa's Oblation (Shotgun)",
                    "Briar's Contempt (Linear Fusion Rifle)",
                    "Koraxis's Distress (Grenade Launcher)",
                ],
            },
            {
                "name": "Scission",
                "weapons": [
                    "Nessa's Oblation (Shotgun)",
                    "Acasia's Dejection (Trace Rifle)",
                    "Mykel's Reverence (Sidearm)",
                    "Koraxis's Distress (Grenade Launcher)",
                ],
            },
            {
                "name": "Macrocosm",
                "weapons": [
                    "Rufus's Fury (Auto Rifle)",
                    "Acasia's Dejection (Trace Rifle)",
                    "Mykel's Reverence (Sidearm)",
                    "Koraxis's Distress (Grenade Launcher)",
                ],
            },
            {
                "name": "Nezarec",
                "weapons": [
                    "Nessa's Oblation (Shotgun)",
                    "Briar's Contempt (Linear Fusion Rifle)",
                    "Koraxis's Distress (Grenade Launcher)",
                    "Acasia's Dejection (Trace Rifle)",
                    "Mykel's Reverence (Sidearm)",
                    "Conditional Finality (Exotic Shotgun)",
                ],
            },
        ],
    },
    "crotas_end": {
        "display_name": "Crota's End",
        "encounters": [
            {
                "name": "The Abyss",
                "weapons": [
                    "Song of Ir Yût (Machine Gun)",
                    "Abyss Defiant (Auto Rifle)",
                    "Fang of Ir Yût (Scout Rifle)",
                ],
            },
            {
                "name": "The Bridge",
                "weapons": [
                    "Swordbreaker (Shotgun)",
                    "Oversoul Edict (Pulse Rifle)",
                    "Fang of Ir Yût (Scout Rifle)",
                ],
            },
            {
                "name": "Ir Yût, the Deathsinger",
                "weapons": [
                    "Song of Ir Yût (Machine Gun)",
                    "Oversoul Edict (Pulse Rifle)",
                    "Word of Crota (Hand Cannon)",
                ],
            },
            {
                "name": "Crota, Son of Oryx",
                "weapons": [
                    "Swordbreaker (Shotgun)",
                    "Abyss Defiant (Auto Rifle)",
                    "Word of Crota (Hand Cannon)",
                    "Necrochasm (Exotic Auto Rifle)",
                ],
            },
        ],
    },
    "vault_of_glass": {
        "display_name": "Vault of Glass",
        "encounters": [
            {
                "name": "Confluxes",
                "weapons": [
                    "Found Verdict (Shotgun)",
                    "Vision of Confluence (Scout Rifle)",
                    "Corrective Measure (Machine Gun)",
                ],
            },
            {
                "name": "Oracles",
                "weapons": [
                    "Found Verdict (Shotgun)",
                    "Vision of Confluence (Scout Rifle)",
                    "Praedyth's Revenge (Sniper Rifle)",
                ],
            },
            {
                "name": "Templar",
                "weapons": [
                    "Fatebringer (Hand Cannon)",
                    "Vision of Confluence (Scout Rifle)",
                    "Corrective Measure (Machine Gun)",
                ],
            },
            {
                "name": "Gatekeepers",
                "weapons": [
                    "Found Verdict (Shotgun)",
                    "Fatebringer (Hand Cannon)",
                    "Hezen Vengeance (Rocket Launcher)",
                ],
            },
            {
                "name": "Atheon",
                "weapons": [
                    "Praedyth's Revenge (Sniper Rifle)",
                    "Corrective Measure (Machine Gun)",
                    "Hezen Vengeance (Rocket Launcher)",
                    "Vex Mythoclast (Exotic Fusion Rifle)",
                ],
            },
        ],
    },
    "salvations_edge": {
        "display_name": "Salvation's Edge",
        "encounters": [
            {
                "name": "Substratum",
                "weapons": [
                    "Imminence (Submachine Gun)",
                    "Non-Denouement (Combat Bow)",
                    "Nullify (Pulse Rifle)",
                ],
            },
            {
                "name": "Herald of Finality",
                "weapons": [
                    "Imminence (Submachine Gun)",
                    "Forthcoming Deviance (Glaive)",
                    "Non-Denouement (Combat Bow)",
                ],
            },
            {
                "name": "Repository",
                "weapons": [
                    "Critical Anomaly (Sniper Rifle)",
                    "Forthcoming Deviance (Glaive)",
                    "Nullify (Pulse Rifle)",
                ],
            },
            {
                "name": "Verity",
                "weapons": [
                    "Imminence (Submachine Gun)",
                    "Summum Bonum (Sword)",
                    "Non-Denouement (Combat Bow)",
                ],
            },
            {
                "name": "The Witness",
                "weapons": [
                    "Critical Anomaly (Sniper Rifle)",
                    "Summum Bonum (Sword)",
                    "Nullify (Pulse Rifle)",
                    "Euphony (Exotic Linear Fusion Rifle)",
                ],
            },
        ],
    },
    "desert_perpetual": {
        "display_name": "Desert Perpetual",
        "encounters": [
            {
                "name": "Substratum",
                "weapons": [
                    "Imminence (Submachine Gun)",
                    "Non-Denouement (Combat Bow)",
                    "Nullify (Pulse Rifle)",
                ],
            },
            {
                "name": "Herald of Finality",
                "weapons": [
                    "Imminence (Submachine Gun)",
                    "Forthcoming Deviance (Glaive)",
                    "Non-Denouement (Combat Bow)",
                ],
            },
            {
                "name": "Repository",
                "weapons": [
                    "Critical Anomaly (Sniper Rifle)",
                    "Forthcoming Deviance (Glaive)",
                    "Nullify (Pulse Rifle)",
                ],
            },
            {
                "name": "Verity",
                "weapons": [
                    "Imminence (Submachine Gun)",
                    "Summum Bonum (Sword)",
                    "Non-Denouement (Combat Bow)",
                ],
            },
            {
                "name": "The Witness",
                "weapons": [
                    "Critical Anomaly (Sniper Rifle)",
                    "Summum Bonum (Sword)",
                    "Nullify (Pulse Rifle)",
                    "Euphony (Exotic Linear Fusion Rifle)",
                ],
            },
        ],
    },
    "vow_of_the_disciple": {
        "display_name": "Vow of the Disciple",
        "encounters": [
            {
                "name": "Acquisition",
                "weapons": [
                    "Submission (Submachine Gun)",
                    "Deliverance (Fusion Rifle)",
                    "Cataclysmic (Linear Fusion Rifle)",
                ],
            },
            {
                "name": "The Caretaker",
                "weapons": [
                    "Submission (Submachine Gun)",
                    "Insidious (Pulse Rifle)",
                    "Cataclysmic (Linear Fusion Rifle)",
                    "Forbearance (Grenade Launcher)",
                ],
            },
            {
                "name": "Exhibition",
                "weapons": [
                    "Submission (Submachine Gun)",
                    "Deliverance (Fusion Rifle)",
                ],
            },
            {
                "name": "Rhulk",
                "weapons": [
                    "Insidious (Pulse Rifle)",
                    "Forbearance (Grenade Launcher)",
                    "Lubrae's Ruin (Glaive)",
                    "Collective Obligation (Exotic Pulse Rifle)",
                ],
            },
        ],
    },
    "last_wish": {
        "display_name": "Last Wish",
        "encounters": [
            {
                "name": "Shared Raid Pool",
                "weapons": [
                    "Nation of Beasts (Hand Cannon)",
                    "The Supremacy (Sniper Rifle)",
                    "Apex Predator (Rocket Launcher)",
                    "Chattering Bone (Pulse Rifle)",
                    "Transfiguration (Scout Rifle)",
                    "Tyranny of Heaven (Combat Bow)",
                    "Age-Old Bond (Auto Rifle)",
                    "Techeun Force (Fusion Rifle)",
                    "One Thousand Voices (Exotic Fusion Rifle)",
                ],
            },
        ],
    },
    "kings_fall": {
        "display_name": "King's Fall",
        "encounters": [
            {
                "name": "Gate",
                "weapons": [
                    "Doom of Chelchis (Scout Rifle)",
                    "Qullim's Terminus (Machine Gun)",
                ],
            },
            {
                "name": "Basilica",
                "weapons": [
                    "Doom of Chelchis (Scout Rifle)",
                    "Qullim's Terminus (Machine Gun)",
                ],
            },
            {
                "name": "Warpriest",
                "weapons": [
                    "Smite of Merain (Pulse Rifle)",
                    "Defiance of Yasmin (Sniper Rifle)",
                    "Qullim's Terminus (Machine Gun)",
                ],
            },
            {
                "name": "Golgoroth",
                "weapons": [
                    "Doom of Chelchis (Scout Rifle)",
                    "Zaouli's Bane (Hand Cannon)",
                    "Midha's Reckoning (Fusion Rifle)",
                    "Qullim's Terminus (Machine Gun)",
                ],
            },
            {
                "name": "The Daughters",
                "weapons": [
                    "Doom of Chelchis (Scout Rifle)",
                    "Smite of Merain (Pulse Rifle)",
                    "Zaouli's Bane (Hand Cannon)",
                    "Defiance of Yasmin (Sniper Rifle)",
                ],
            },
            {
                "name": "Oryx, the Taken King",
                "weapons": [
                    "Doom of Chelchis (Scout Rifle)",
                    "Qullim's Terminus (Machine Gun)",
                    "Smite of Merain (Pulse Rifle)",
                    "Defiance of Yasmin (Sniper Rifle)",
                    "Zaouli's Bane (Hand Cannon)",
                    "Touch of Malice (Exotic Scout Rifle)",
                ],
            },
        ],
    },
    "garden_of_salvation": {
        "display_name": "Garden of Salvation",
        "encounters": [
            {
                "name": "Evade the Consecrated Mind",
                "weapons": [
                    "Zealot's Reward (Fusion Rifle)",
                    "Accrued Redemption (Combat Bow)",
                ],
            },
            {
                "name": "Summon the Consecrated Mind",
                "weapons": [
                    "Prophet of Doom (Shotgun)",
                    "Reckless Oracle (Auto Rifle)",
                ],
            },
            {
                "name": "Defeat the Consecrated Mind",
                "weapons": [
                    "Ancient Gospel (Hand Cannon)",
                    "Sacred Provenance (Pulse Rifle)",
                ],
            },
            {
                "name": "Defeat the Sanctified Mind",
                "weapons": [
                    "Omniscient Eye (Sniper Rifle)",
                    "Divinity (Exotic Trace Rifle)",
                ],
            },
        ],
    },
}

RAID_KEY_ALIASES = {
    "desert-perpetual": "desert_perpetual",
    "desert perpetual": "desert_perpetual",
    "salvation's edge": "salvations_edge",
}


def get_raid_weapon_choices():
    choices = [(data["display_name"], raid_key) for raid_key, data in RAID_WEAPON_TABLE.items()]
    choices.sort(key=lambda item: item[0].casefold())
    return choices


def build_raid_weapons_embed(raid_key: str):
    normalized_key = str(raid_key or "").strip()
    canonical_key = RAID_KEY_ALIASES.get(normalized_key.casefold(), normalized_key)
    raid_data = RAID_WEAPON_TABLE.get(canonical_key)
    if not raid_data:
        return None

    embed = discord.Embed(
        title=f"{raid_data['display_name']} Weapon Drops",
        description="Weapons by encounter",
        color=discord.Color.gold(),
    )

    thumbnail_url = raid_data.get("thumbnail_url")
    if thumbnail_url:
        embed.set_thumbnail(url=thumbnail_url)

    for encounter in raid_data.get("encounters", []):
        encounter_name = encounter.get("name") or "Encounter"
        weapons = encounter.get("weapons") or []
        weapon_lines = "\n".join(f"• {weapon}" for weapon in weapons) if weapons else "• No weapon data yet"
        embed.add_field(name=encounter_name, value=weapon_lines, inline=False)

    embed.set_footer(text="via Wheelie Bin")
    return embed
