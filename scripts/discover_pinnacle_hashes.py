#!/usr/bin/env python3
import os
import json
import asyncio
from collections import defaultdict

from bungie_api import BungieAPI

TARGET_KEYWORDS = (
    "operation: seraph's shield",
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
    "pinnacle",
    "exotic mission",
)


async def main():
    api_key = os.getenv("BUNGIE_API_KEY")
    clan_id = os.getenv("BUNGIE_CLAN_ID")
    if not api_key or not clan_id:
        raise RuntimeError("BUNGIE_API_KEY and BUNGIE_CLAN_ID are required")

    api = BungieAPI(api_key)
    out = defaultdict(set)
    seen_hashes = set()

    try:
        roster = await api.get_clan_roster(clan_id)
        for member in roster[:120]:
            membership_type = member.get("membershipType")
            membership_id = member.get("membershipId")
            if not membership_type or not membership_id:
                continue

            try:
                profile = await api.get_profile(membership_type, membership_id)
            except Exception:
                continue

            character_ids = list((profile.get("characters", {}).get("data", {}) or {}).keys())
            for character_id in character_ids:
                for mode in (82, 84, None):
                    try:
                        history = await api.get_activity_history(
                            membership_type=membership_type,
                            membership_id=membership_id,
                            character_id=character_id,
                            mode=mode,
                            count=50,
                            page=0,
                        )
                    except Exception:
                        continue

                    for activity in history.get("activities", []):
                        reference_id = (activity.get("activityDetails") or {}).get("referenceId")
                        try:
                            activity_hash = int(reference_id)
                        except Exception:
                            continue

                        if activity_hash in seen_hashes:
                            continue
                        seen_hashes.add(activity_hash)

                        try:
                            definition = await api.get_activity_definition(activity_hash)
                        except Exception:
                            continue

                        name = ((definition.get("displayProperties") or {}).get("name") or "").strip()
                        if not name:
                            continue

                        lowered = name.casefold()
                        if any(keyword in lowered for keyword in TARGET_KEYWORDS):
                            out[name].add(activity_hash)
    finally:
        await api.close()

    result = {name: sorted(hashes) for name, hashes in sorted(out.items())}
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
