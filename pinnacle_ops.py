"""Static config for non-dungeon solo activities (Pinnacle/Exotic style)."""

import re

# Hash->label mappings for non-dungeon solo activities.
# Keep labels explicit for variant clarity (Normal/Master/Ultimate/etc).
#
# Template examples (replace hash values with real referenceIds):
# 1234567890: "Exotic Mission - Presage (Normal)",
# 2345678901: "Exotic Mission - Presage (Master)",
#
# Suggested starter labels from your list:
# 0: "Operation: Seraph's Shield (Normal)",
# 0: "Operation: Seraph's Shield (Master)",
# 0: "The Coil (Normal)",
# 0: "The Coil (Ultimate)",
# 0: "Widow's Court",
# 0: "Exotic Mission - Heliostat (Normal)",
# 0: "Exotic Mission - Heliostat (Master)",
# 0: "Exotic Mission - Derealize (Normal)",
# 0: "Exotic Mission - Derealize (Master)",
# 0: "Exotic Mission - Kells Fall (Normal)",
# 0: "Exotic Mission - Kells Fall (Master)",
# 0: "Exotic Mission - Encore (Normal)",
# 0: "Exotic Mission - Encore (Master)",
# 0: "Exotic Mission - Starcrossed (Normal)",
# 0: "Exotic Mission - Starcrossed (Master)",
# 0: "Exotic Mission - //node.ovrd.AVALON// (Normal)",
# 0: "Exotic Mission - //node.ovrd.AVALON// (Legend)",
# 0: "Exotic Mission - Vox Obscura (Normal)",
# 0: "Exotic Mission - Vox Obscura (Master)",
# 0: "Exotic Mission - Presage (Normal)",
# 0: "Exotic Mission - Presage (Master)",
# 0: "Exotic Mission - Zero Hour (Normal)",
# 0: "Exotic Mission - Zero Hour (Legend)",
# 0: "Exotic Mission - The Whisper (Normal)",
# 0: "Exotic Mission - The Whisper (Legend)",
# 0: "Crawl of Elders (Normal)",
# 0: "Crawl of Elders (Master)",
SPECIAL_SOLO_ACTIVITY_NAMES = {
    # Auto-discovered from Bungie activity definitions via clan history scan.
    # Presage
    935938264: "Exotic Mission - Presage (Customize)",
    3883295757: "Exotic Mission - Presage (Standard)",

    # Kell's Fall
    2872322301: "Exotic Mission - Kell's Fall (Customize)",
    3651375932: "Exotic Mission - Kell's Fall (Expert - Customize)",

    # Zero Hour
    1749077519: "Exotic Mission - Zero Hour (Customize)",
    3361746271: "Exotic Mission - Zero Hour (Standard)",
    1848771417: "Exotic Mission - Zero Hour (Expert)",

    # The Whisper
    576782083: "Exotic Mission - The Whisper (Customize)",
    3743446313: "Exotic Mission - The Whisper (Standard)",
    3871520787: "Exotic Mission - The Whisper (Expert)",

    # //node.ovrd.AVALON//
    775989808: "Exotic Mission - //node.ovrd.AVALON// (Customize)",
    3755529435: "Exotic Mission - //node.ovrd.AVALON//",
    3083261666: "Exotic Mission - //node.ovrd.AVALON// (Legend)",

    # Vox Obscura
    429161651: "Exotic Mission - Vox Obscura (Customize)",

    # Starcrossed
    1768099736: "Exotic Mission - Starcrossed (Customize)",
    896748846: "Exotic Mission - Starcrossed (Normal)",
    1013336498: "Exotic Mission - Starcrossed (Legend)",

    # Operation: Seraph's Shield
    2258770894: "Operation: Seraph's Shield (Customize)",
    1221538367: "Operation: Seraph's Shield (Standard)",
    995051012: "Operation: Seraph's Shield (Legend)",
    2919809209: "Operation: Seraph's Shield (Expert)",
    3645820853: "Operation: Seraph's Shield (Customize)",

    # The Coil
    962366573: "The Coil (Customize)",
    3698225057: "The Coil (Matchmade)",
    443581538: "The Coil (Matchmade Variant)",
    4233082192: "The Coil (Private)",

    # Heliostat / Derealize / Encore
    4129522203: "Exotic Mission - Heliostat (Customize)",
    2384839795: "Grandmaster Conquest - Heliostat (Customize)",
    1402729521: "Exotic Mission - Derealize (Customize)",
    2883193556: "Master Conquest - Derealize (Customize)",
    3120544689: "Exotic Mission - Encore (Customize)",

    # Widow's Court / related
    427041827: "Widow's Court",
    1448047125: "Widow's Court",
    2399854375: "Widow's Court",
    2428036886: "Widow's Court",
    3651554949: "Widow's Court",
    1598363326: "Widow's Court (Matchmade)",
}


SPECIAL_SOLO_ACTIVITY_KEYWORDS = (
    "pinnacle",
    "exotic mission",
    "operation: seraph's shield",
    "seraph's shield",
    "the coil",
    "widow's court",
    "heliostat",
    "derealize",
    "kells fall",
    "encore",
    "starcrossed",
    "avalon",
    "vox obscura",
    "presage",
    "zero hour",
    "the whisper",
    "crawl of elders",
)


EXOTIC_MISSION_NAME_KEYWORDS = (
    "heliostat",
    "derealize",
    "kells fall",
    "kell's fall",
    "encore",
    "starcrossed",
    "avalon",
    "vox obscura",
    "presage",
    "zero hour",
    "the whisper",
)


def activity_name_matches_special_solo_activity(name: str | None):
    if not name:
        return False
    normalized = str(name).casefold().strip()
    normalized_compact = re.sub(r"[^a-z0-9]+", " ", normalized).strip()
    normalized_dense = re.sub(r"[^a-z0-9]+", "", normalized)
    for keyword in SPECIAL_SOLO_ACTIVITY_KEYWORDS:
        keyword_text = str(keyword).casefold().strip()
        keyword_compact = re.sub(r"[^a-z0-9]+", " ", keyword_text).strip()
        keyword_dense = re.sub(r"[^a-z0-9]+", "", keyword_text)
        if keyword_text and keyword_text in normalized:
            return True
        if keyword_compact and keyword_compact in normalized_compact:
            return True
        if keyword_dense and keyword_dense in normalized_dense:
            return True
    return False


def normalize_special_solo_activity_display_name(name: str | None):
    if not name:
        return name

    value = str(name).strip()
    if not value:
        return value

    # Normalize Bungie variant labels to a cleaner public naming scheme.
    # Standard/Normal/Customize -> Customize, Expert/Legend -> Master.
    value = re.sub(r":\s*Customize\b", " (Customize)", value, flags=re.IGNORECASE)
    value = re.sub(r":\s*Standard\b", " (Customize)", value, flags=re.IGNORECASE)
    value = re.sub(r"\(Standard\)", "(Customize)", value, flags=re.IGNORECASE)
    value = re.sub(r"\(Customize\)", "(Customize)", value, flags=re.IGNORECASE)
    value = re.sub(r"\(Normal\)", "(Customize)", value, flags=re.IGNORECASE)
    value = re.sub(r"\(Legend\)", "(Master)", value, flags=re.IGNORECASE)
    value = re.sub(r"\(Expert\)", "(Master)", value, flags=re.IGNORECASE)

    # Keep labels consistent when Bungie omits an explicit variant for exotic missions.
    if value.casefold().startswith("exotic mission - ") and "(" not in value:
        value = f"{value} (Customize)"

    # Some manifest labels omit the "Exotic Mission -" prefix entirely
    # (for example "Kell's Fall: Customize"). Keep embed titles consistent.
    value_casefold = value.casefold().strip()
    if not value_casefold.startswith("exotic mission - "):
        compact_value = re.sub(r"[^a-z0-9]+", " ", value_casefold).strip()
        for keyword in EXOTIC_MISSION_NAME_KEYWORDS:
            keyword_compact = re.sub(r"[^a-z0-9]+", " ", str(keyword).casefold()).strip()
            if keyword_compact and keyword_compact in compact_value:
                value = f"Exotic Mission - {value}"
                break

    return value
