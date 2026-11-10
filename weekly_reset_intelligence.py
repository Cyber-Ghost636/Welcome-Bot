from __future__ import annotations

from datetime import datetime, timedelta, timezone
from html import unescape
import json
from pathlib import Path
import re
from zoneinfo import ZoneInfo

import aiohttp
import discord
from discord import app_commands
from discord.ext import tasks


RESET_WEEKDAY = 1  # Tuesday
RESET_HOUR_UTC = 17
POST_TIMEZONE = ZoneInfo("Europe/London")
POST_HOUR_LOCAL = 17
POST_MINUTE_LOCAL = 5
D2RAD_URL = "https://d2rad.com/"
ROTATION_THUMBNAIL_URL = "https://i.postimg.cc/rsVZrxM9/Dark-Skull.gif"
WEEKLY_RESET_EMBED_FORMAT_VERSION = "v7_nightmare_hunts_manual"
NIGHTMARE_HUNTS_FILENAME = "nightmareHunts.json"

KNOWN_RAID_NAMES = {
    "last wish",
    "garden of salvation",
    "deep stone crypt",
    "vault of glass",
    "vow of the disciple",
    "king's fall",
    "root of nightmares",
    "crota's end",
    "salvation's edge",
}

KNOWN_DUNGEON_NAMES = {
    "shattered throne",
    "pit of heresy",
    "prophecy",
    "grasp of avarice",
    "duality",
    "spire of the watcher",
    "ghosts of the deep",
    "warlord's ruin",
    "vesper's host",
    "sundered doctrine",
}

WEEKLY_WORLD_CYCLE = [
    {
        "week": 1,
        "hunts": "Insanity | Anguish | Despair",
        "dreaming_city": "Curse Week 1\nBay of Drowned Wishes",
    },
    {
        "week": 2,
        "hunts": "Fear | Isolation | Rage",
        "dreaming_city": "Curse Week 2\nChamber of Starlight",
    },
    {
        "week": 3,
        "hunts": "Anguish | Pride | Servitude",
        "dreaming_city": "Curse Week 3\nAphelions Rest",
    },
]

# 2026-09-29 reset is treated as Week 1 so 2026-10-06 aligns to Week 2.
WORLD_CYCLE_ANCHOR_RESET_UTC = datetime(2026, 9, 29, 17, 0, tzinfo=timezone.utc)
# Tuesday 20 Oct 2026 17:00 UTC (Week 3 / index 2 in the external matrix)
NIGHTMARE_HUNTS_ANCHOR_RESET_UTC = datetime(2026, 10, 20, 17, 0, tzinfo=timezone.utc)
NIGHTMARE_HUNTS_STARTING_INDEX = 2


class WeeklyResetIntelligence:
    def __init__(
        self,
        *,
        bot,
        get_bungie_api,
        get_guild_config,
        rules_config,
        save_rules_config,
        golden_oldies_guild_id: str,
    ):
        self.bot = bot
        self.get_bungie_api = get_bungie_api
        self.get_guild_config = get_guild_config
        self.rules_config = rules_config
        self.save_rules_config = save_rules_config
        self.golden_oldies_guild_id = str(golden_oldies_guild_id or "").strip()

        self._commands_registered = False
        self._started = False
        self._activity_name_cache = {}
        self._milestone_name_cache = {}
        self._base_dir = Path(__file__).resolve().parent

    async def _run_startup_post_check(self) -> None:
        for guild in self.bot.guilds:
            if not self._is_allowed_guild(guild.id):
                continue
            try:
                posted, message = await self._post_for_guild(guild, force=False)
                if posted:
                    print(f"[WEEKLY_RESET_INTEL] Startup check posted for guild {guild.id}", flush=True)
                else:
                    print(f"[WEEKLY_RESET_INTEL] Startup check skipped for guild {guild.id}: {message}", flush=True)
            except Exception as e:
                print(f"[WEEKLY_RESET_INTEL] Startup check failed for guild {guild.id}: {e}", flush=True)

    @staticmethod
    def _parse_list_input(value: str | None) -> list[str]:
        text = str(value or "").strip()
        if not text:
            return []
        items = [part.strip() for part in text.split(",")]
        output = []
        seen = set()
        for item in items:
            if not item:
                continue
            key = item.casefold()
            if key in seen:
                continue
            seen.add(key)
            output.append(item)
        return output

    def _is_allowed_guild(self, guild_id: int | str | None) -> bool:
        return str(guild_id or "") == self.golden_oldies_guild_id

    def _current_reset_anchor(self, now: datetime | None = None) -> datetime:
        now_utc = now or datetime.now(timezone.utc)
        candidate = now_utc.replace(hour=RESET_HOUR_UTC, minute=0, second=0, microsecond=0)
        delta_days = (candidate.weekday() - RESET_WEEKDAY) % 7
        anchor = candidate - timedelta(days=delta_days)
        if now_utc < anchor:
            anchor -= timedelta(days=7)
        return anchor

    def _reset_key(self, now: datetime | None = None) -> str:
        anchor = self._current_reset_anchor(now)
        return anchor.strftime("%Y-%m-%dT%H:%M:%SZ")

    def _current_post_gate_utc(self, now: datetime | None = None) -> datetime:
        """Return the most recent Tuesday post gate in Europe/London converted to UTC."""
        now_utc = now or datetime.now(timezone.utc)
        now_local = now_utc.astimezone(POST_TIMEZONE)
        candidate_local = now_local.replace(
            hour=POST_HOUR_LOCAL,
            minute=POST_MINUTE_LOCAL,
            second=0,
            microsecond=0,
        )
        delta_days = (candidate_local.weekday() - RESET_WEEKDAY) % 7
        anchor_local = candidate_local - timedelta(days=delta_days)
        if now_local < anchor_local:
            anchor_local -= timedelta(days=7)
        return anchor_local.astimezone(timezone.utc)

    def _get_world_cycle(self, reset_dt: datetime | None) -> dict:
        if reset_dt is None:
            return WEEKLY_WORLD_CYCLE[0]

        delta_days = (reset_dt - WORLD_CYCLE_ANCHOR_RESET_UTC).days
        weeks_since_anchor = delta_days // 7
        cycle_index = weeks_since_anchor % len(WEEKLY_WORLD_CYCLE)
        return WEEKLY_WORLD_CYCLE[cycle_index]

    def _get_manual_nightmare_hunts_for_reset(self, reset_key: str, guild_config: dict | None) -> str | None:
        if not isinstance(guild_config, dict):
            return None

        mapping = guild_config.get("weekly_nightmare_hunts_by_reset")
        if not isinstance(mapping, dict):
            return None

        entry = mapping.get(reset_key)
        if entry is None:
            return None

        if isinstance(entry, list):
            formatted = " | ".join(str(item).strip() for item in entry if str(item).strip())
            return formatted or "TBD"

        if isinstance(entry, str):
            normalized = str(entry).strip()
            return normalized or "TBD"

        return None

    def _get_active_nightmare_hunts(
        self,
        reset_dt: datetime | None,
        fallback_hunts: str,
        *,
        reset_key: str | None = None,
        guild_config: dict | None = None,
    ) -> str:
        if reset_key:
            manual_for_reset = self._get_manual_nightmare_hunts_for_reset(reset_key, guild_config)
            if manual_for_reset is not None:
                return manual_for_reset

        if reset_dt is None:
            return fallback_hunts

        file_path = self._base_dir / NIGHTMARE_HUNTS_FILENAME
        if not file_path.exists():
            return fallback_hunts

        try:
            with file_path.open("r", encoding="utf-8") as f:
                rotation_data = json.load(f)
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            return fallback_hunts

        if not isinstance(rotation_data, list) or not rotation_data:
            return fallback_hunts

        weeks_elapsed = (reset_dt - NIGHTMARE_HUNTS_ANCHOR_RESET_UTC).days // 7
        target_index = (NIGHTMARE_HUNTS_STARTING_INDEX + weeks_elapsed) % len(rotation_data)
        active = rotation_data[target_index]
        if not isinstance(active, dict):
            return fallback_hunts

        hunts = active.get("hunts")
        if isinstance(hunts, list):
            formatted = " | ".join(str(hunt).strip() for hunt in hunts if str(hunt).strip())
            return formatted or "TBD"
        if isinstance(hunts, str) and hunts.strip():
            return hunts.strip()

        return "TBD"

    async def _fetch_d2rad_weekly_rotation(self) -> dict | None:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/129.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-GB,en;q=0.9",
        }
        try:
            timeout = aiohttp.ClientTimeout(total=15)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(D2RAD_URL, headers=headers) as response:
                    if response.status != 200:
                        return None
                    html = await response.text()
        except Exception:
            return None

        # Prefer current timeline extraction because d2rad page structure is dynamic.
        timeline_current = self._parse_d2rad_current_timeline_from_html(html)
        if timeline_current is not None:
            raids = list(timeline_current.get("raids") or [])
            dungeons = list(timeline_current.get("dungeons") or [])
            if raids or dungeons:
                return {
                    "raids": raids,
                    "dungeons": dungeons,
                }

        # Extract readable text from page HTML.
        text = re.sub(r"<script[\\s\\S]*?</script>", "", html, flags=re.IGNORECASE)
        text = re.sub(r"<style[\\s\\S]*?</style>", "", text, flags=re.IGNORECASE)
        text = re.sub(r"<[^>]+>", "\n", text)
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if not lines:
            return self._parse_d2rad_this_week_from_html(html)

        upper_lines = [line.upper() for line in lines]
        try:
            this_week_idx = upper_lines.index("THIS WEEK")
        except ValueError:
            return self._parse_d2rad_this_week_from_html(html)

        end_idx = len(lines)
        for marker in ("RESET INFORMATION", "NEXT WEEK"):
            try:
                marker_idx = upper_lines.index(marker, this_week_idx + 1)
                end_idx = min(end_idx, marker_idx)
            except ValueError:
                pass

        window = lines[this_week_idx:end_idx]
        upper_window = [line.upper() for line in window]

        try:
            raids_idx = upper_window.index("RAIDS")
            dungeons_idx = upper_window.index("DUNGEONS")
        except ValueError:
            return self._parse_d2rad_this_week_from_html(html)

        def _collect_names(start_idx: int, stop_idx: int) -> list[str]:
            output = []
            seen = set()
            for line in window[start_idx + 1:stop_idx]:
                match = re.search(r"View\\s+(.+?)\\s+loot", line, flags=re.IGNORECASE)
                if not match:
                    continue
                name = match.group(1).strip()
                if name.casefold().startswith("the "):
                    name = name[4:].strip()
                key = name.casefold()
                if not key or key in seen:
                    continue
                seen.add(key)
                output.append(name)
                if len(output) >= 2:
                    break
            return output

        raids = _collect_names(raids_idx, dungeons_idx)
        dungeons = _collect_names(dungeons_idx, len(window))

        if not raids and not dungeons:
            return self._parse_d2rad_this_week_from_html(html)

        return {
            "raids": raids,
            "dungeons": dungeons,
        }

    def _parse_d2rad_current_timeline_from_html(self, html: str) -> dict | None:
        """Extract current-week raids/dungeons from d2rad timeline HTML."""
        if not html:
            return None

        current_idx = html.find("Current ·")
        if current_idx == -1:
            return None

        next_item_idx = html.find("Predicted", current_idx + 1)
        if next_item_idx == -1:
            next_item_idx = min(len(html), current_idx + 20000)
        segment = html[current_idx:next_item_idx]

        raid_matches = re.findall(
            r'<a href="/raids/[^"]+"[^>]*>[\s\S]*?<span class="loot-table-trigger-label">([^<]+)</span>',
            segment,
            flags=re.IGNORECASE,
        )
        dungeon_matches = re.findall(
            r'<a href="/dungeons/[^"]+"[^>]*>[\s\S]*?<span class="loot-table-trigger-label">([^<]+)</span>',
            segment,
            flags=re.IGNORECASE,
        )

        def _dedupe(values: list[str]) -> list[str]:
            out: list[str] = []
            seen: set[str] = set()
            for raw in values:
                name = unescape(str(raw or "").strip())
                if not name:
                    continue
                if name.casefold().startswith("the "):
                    name = name[4:].strip()
                key = name.casefold()
                if not key or key in seen:
                    continue
                seen.add(key)
                out.append(name)
                if len(out) >= 2:
                    break
            return out

        raids = _dedupe(raid_matches)
        dungeons = _dedupe(dungeon_matches)
        if not raids and not dungeons:
            return None
        return {"raids": raids, "dungeons": dungeons}

    def _parse_d2rad_this_week_from_html(self, html: str) -> dict | None:
        """Fallback parser for d2rad THIS WEEK section using raw HTML patterns."""
        if not html:
            return None

        upper_html = html.upper()
        this_week_pos = upper_html.find("THIS WEEK")
        if this_week_pos == -1:
            return None

        next_week_pos = upper_html.find("NEXT WEEK", this_week_pos + 1)
        if next_week_pos == -1:
            next_week_pos = len(html)

        section = html[this_week_pos:next_week_pos]
        # Remove tags we don't need and normalize whitespace.
        cleaned = re.sub(r"<script[\\s\\S]*?</script>", "", section, flags=re.IGNORECASE)
        cleaned = re.sub(r"<style[\\s\\S]*?</style>", "", cleaned, flags=re.IGNORECASE)

        raid_matches = re.findall(
            r"/raids/[^\"'>]+[\"'][^>]*>\s*View\s+(.+?)\s+loot\s*<",
            cleaned,
            flags=re.IGNORECASE,
        )
        dungeon_matches = re.findall(
            r"/dungeons/[^\"'>]+[\"'][^>]*>\s*View\s+(.+?)\s+loot\s*<",
            cleaned,
            flags=re.IGNORECASE,
        )

        def _dedupe(values: list[str]) -> list[str]:
            out: list[str] = []
            seen: set[str] = set()
            for raw in values:
                name = str(raw or "").strip()
                if not name:
                    continue
                if name.casefold().startswith("the "):
                    name = name[4:].strip()
                key = name.casefold()
                if not key or key in seen:
                    continue
                seen.add(key)
                out.append(name)
                if len(out) >= 2:
                    break
            return out

        raids = _dedupe(raid_matches)
        dungeons = _dedupe(dungeon_matches)
        if raids or dungeons:
            return {
                "raids": raids,
                "dungeons": dungeons,
            }

        # Secondary fallback for d2rad React payload format.
        current_pos = html.find("Current ·")
        if current_pos != -1:
            # Slice a bounded window that should contain the current timeline item.
            payload_window = html[current_pos: current_pos + 30000]
            link_matches = re.findall(
                r"href\\\":\\\"/(raids|dungeons)/[^\\\"]+\\\",\\\"aria-label\\\":\\\"View\\s+(.+?)\\s+loot\\\"",
                payload_window,
                flags=re.IGNORECASE,
            )

            raid_values: list[str] = []
            dungeon_values: list[str] = []
            for kind, name in link_matches:
                clean_name = str(name or "").strip()
                if not clean_name:
                    continue
                if str(kind).casefold() == "raids":
                    raid_values.append(clean_name)
                elif str(kind).casefold() == "dungeons":
                    dungeon_values.append(clean_name)

            raids = _dedupe(raid_values)
            dungeons = _dedupe(dungeon_values)

        if not raids and not dungeons:
            return None
        return {
            "raids": raids,
            "dungeons": dungeons,
        }

    async def _get_activity_name(self, activity_hash) -> str:
        try:
            key = int(activity_hash)
        except (TypeError, ValueError):
            return ""

        if key in self._activity_name_cache:
            return self._activity_name_cache[key]

        bungie_api = self.get_bungie_api()
        if bungie_api is None:
            return ""

        try:
            definition = await bungie_api.get_activity_definition(key)
        except Exception:
            return ""

        name = str((definition.get("displayProperties") or {}).get("name") or "").strip()
        self._activity_name_cache[key] = name
        return name

    async def _get_milestone_name(self, milestone_hash) -> str:
        try:
            key = int(milestone_hash)
        except (TypeError, ValueError):
            return ""

        if key in self._milestone_name_cache:
            return self._milestone_name_cache[key]

        bungie_api = self.get_bungie_api()
        if bungie_api is None:
            return ""

        try:
            definition = await bungie_api.get_milestone_definition(key)
        except Exception:
            return ""

        name = str((definition.get("displayProperties") or {}).get("name") or "").strip()
        self._milestone_name_cache[key] = name
        return name

    def _score_candidate(self, *, activity_name: str, milestone_name: str, kind: str) -> int:
        text_a = activity_name.casefold()
        text_m = milestone_name.casefold()
        score = 0

        if kind == "raid":
            if text_a in KNOWN_RAID_NAMES:
                score += 8
            if "raid" in text_m:
                score += 6
        if kind == "dungeon":
            if text_a in KNOWN_DUNGEON_NAMES:
                score += 8
            if "dungeon" in text_m:
                score += 6

        for token in ("featured", "weekly", "rotator", "challenge"):
            if token in text_m:
                score += 2

        if "legacy" in text_m:
            score += 1

        return score

    async def _resolve_featured_activities(self) -> dict:
        bungie_api = self.get_bungie_api()
        if bungie_api is None:
            return {"raid": None, "dungeon": None}

        milestones = await bungie_api.get_milestones()
        if not isinstance(milestones, dict):
            return {"raid": None, "dungeon": None}

        raid_candidates = []
        dungeon_candidates = []

        for milestone_hash, milestone_data in milestones.items():
            milestone_name = await self._get_milestone_name(milestone_hash)
            activities = milestone_data.get("activities") or []
            for activity in activities:
                activity_hash = (activity.get("activityHash") if isinstance(activity, dict) else None)
                if activity_hash is None:
                    continue

                activity_name = await self._get_activity_name(activity_hash)
                if not activity_name:
                    continue

                lowered = activity_name.casefold()
                if lowered in KNOWN_RAID_NAMES or "raid" in milestone_name.casefold():
                    raid_candidates.append(
                        (
                            self._score_candidate(
                                activity_name=activity_name,
                                milestone_name=milestone_name,
                                kind="raid",
                            ),
                            activity_name,
                            milestone_name,
                            str(activity_hash),
                        )
                    )

                if lowered in KNOWN_DUNGEON_NAMES or "dungeon" in milestone_name.casefold():
                    dungeon_candidates.append(
                        (
                            self._score_candidate(
                                activity_name=activity_name,
                                milestone_name=milestone_name,
                                kind="dungeon",
                            ),
                            activity_name,
                            milestone_name,
                            str(activity_hash),
                        )
                    )

        # Deterministic pick: best score, then alphabetic activity name.
        raid_candidates.sort(key=lambda item: (-item[0], item[1]))
        dungeon_candidates.sort(key=lambda item: (-item[0], item[1]))

        # Keep top two unique raid names because weekly reset often exposes two raid tracks
        # (featured rotator + pinnacle/current).
        raid_names = []
        seen_raid = set()
        for _, raid_name, _, _ in raid_candidates:
            key = str(raid_name).casefold().strip()
            if not key or key in seen_raid:
                continue
            seen_raid.add(key)
            raid_names.append(raid_name)
            if len(raid_names) >= 2:
                break

        featured_raid = raid_names[0] if raid_names else None
        featured_raid_secondary = raid_names[1] if len(raid_names) > 1 else None

        dungeon_names = []
        seen_dungeon = set()
        for _, dungeon_name, _, _ in dungeon_candidates:
            key = str(dungeon_name).casefold().strip()
            if not key or key in seen_dungeon:
                continue
            seen_dungeon.add(key)
            dungeon_names.append(dungeon_name)
            if len(dungeon_names) >= 2:
                break

        featured_dungeon = dungeon_names[0] if dungeon_names else None
        featured_dungeon_secondary = dungeon_names[1] if len(dungeon_names) > 1 else None

        return {
            "raid": featured_raid,
            "raid_secondary": featured_raid_secondary,
            "dungeon": featured_dungeon,
            "dungeon_secondary": featured_dungeon_secondary,
            "raid_source": raid_candidates[0][2] if raid_candidates else None,
            "dungeon_source": dungeon_candidates[0][2] if dungeon_candidates else None,
        }

    def _build_embed(self, *, reset_key: str, current: dict) -> discord.Embed:
        try:
            reset_dt = datetime.strptime(reset_key, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        except ValueError:
            reset_dt = None

        reset_local = reset_dt.astimezone(POST_TIMEZONE) if reset_dt is not None else None
        cycle = self._get_world_cycle(reset_dt)
        active_hunts = str(current.get("nightmare_hunts") or cycle["hunts"])

        next_reset_dt = reset_dt + timedelta(days=7) if reset_dt is not None else None
        reset_line = "Reset soon."
        if next_reset_dt is not None:
            reset_line = f"Next reset <t:{int(next_reset_dt.timestamp())}:R>."

        header_lines = []
        if reset_local is not None:
            header_lines.append(f"Tuesday {reset_local.strftime('%d/%m/%Y')}")
        header_lines.append(f"WEEK {cycle['week']}")
        header_lines.append("")
        header_lines.append(reset_line)

        embed = discord.Embed(
            title="Weekly Rotations",
            description="\n".join(header_lines),
            color=discord.Color.gold(),
        )
        embed.set_thumbnail(url=ROTATION_THUMBNAIL_URL)

        raids = list(current.get("raids") or [])
        dungeons = list(current.get("dungeons") or [])

        if not raids:
            primary_raid = current.get("raid")
            secondary_raid = current.get("raid_secondary")
            if primary_raid:
                raids.append(primary_raid)
            if secondary_raid:
                raids.append(secondary_raid)

        if not dungeons:
            primary_dungeon = current.get("dungeon")
            secondary_dungeon = current.get("dungeon_secondary")
            if primary_dungeon:
                dungeons.append(primary_dungeon)
            if secondary_dungeon:
                dungeons.append(secondary_dungeon)

        raid_1 = raids[0] if len(raids) > 0 else "Unknown"
        raid_2 = raids[1] if len(raids) > 1 else "-"
        dungeon_1 = dungeons[0] if len(dungeons) > 0 else "Unknown"
        dungeon_2 = dungeons[1] if len(dungeons) > 1 else "-"

        raids_text = f"{raid_1}\n{raid_2}"
        dungeons_text = f"{dungeon_1}\n{dungeon_2}"
        raid_challenges_text = (
            "Summoning Ritual | Leftovers | Wait For It… | Red Rover | "
            "Swift Destruction | The Grass Is Always Greener | "
            "Illuminated Torment Challenge | Conservation of Energy"
        )

        embed.add_field(name="Weekly Raids", value=raids_text, inline=True)
        embed.add_field(name="Hunts", value=active_hunts, inline=True)
        embed.add_field(name="Dreaming City", value=cycle["dreaming_city"], inline=True)
        embed.add_field(name="Weekly Dungeons", value=dungeons_text, inline=False)
        embed.add_field(name="Raid Challenges", value=raid_challenges_text, inline=False)
        embed.set_footer(text="via Wheelie Bin")
        return embed

    async def _compute_snapshot(self) -> dict:
        reset_key = self._reset_key()
        guild_config = {}
        if self.golden_oldies_guild_id.isdigit():
            guild_config = self.get_guild_config(int(self.golden_oldies_guild_id))

        manual = guild_config.get("weekly_reset_manual") if isinstance(guild_config, dict) else None
        try:
            reset_dt = datetime.strptime(reset_key, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        except ValueError:
            reset_dt = None
        active_hunts = self._get_active_nightmare_hunts(
            reset_dt,
            self._get_world_cycle(reset_dt)["hunts"] if reset_dt is not None else WEEKLY_WORLD_CYCLE[0]["hunts"],
            reset_key=reset_key,
            guild_config=guild_config,
        )

        if isinstance(manual, dict):
            manual_reset_key = str(manual.get("reset_key") or "").strip()
            # Manual overrides are week-scoped; legacy overrides without a key are ignored
            # so stale values cannot persist into future resets.
            if manual_reset_key == reset_key:
                manual_raids = self._parse_list_input(",".join(str(v) for v in (manual.get("raids") or [])))
                manual_dungeons = self._parse_list_input(",".join(str(v) for v in (manual.get("dungeons") or [])))
                if manual_raids or manual_dungeons:
                    return {
                        "reset_key": reset_key,
                        "raids": manual_raids,
                        "dungeons": manual_dungeons,
                        "raid": manual_raids[0] if manual_raids else None,
                        "raid_secondary": manual_raids[1] if len(manual_raids) > 1 else None,
                        "dungeon": manual_dungeons[0] if manual_dungeons else None,
                        "dungeon_secondary": manual_dungeons[1] if len(manual_dungeons) > 1 else None,
                        "updated_at": datetime.now(timezone.utc).isoformat(),
                        "source": "manual",
                        "nightmare_hunts": active_hunts,
                    }
            elif not manual_reset_key and self.golden_oldies_guild_id.isdigit():
                # One-time migration cleanup for legacy manual overrides without reset_key.
                guild_id = int(self.golden_oldies_guild_id)
                live_guild_config = self.get_guild_config(guild_id)
                if isinstance(live_guild_config, dict) and "weekly_reset_manual" in live_guild_config:
                    live_guild_config.pop("weekly_reset_manual", None)
                    self.rules_config[str(guild_id)] = live_guild_config
                    self.save_rules_config(self.rules_config)
                    print(f"[WEEKLY_RESET_INTEL] Cleared legacy manual override for guild {guild_id}")

        # Primary auto source: d2rad "THIS WEEK" verified values.
        d2rad = await self._fetch_d2rad_weekly_rotation()
        if isinstance(d2rad, dict) and (d2rad.get("raids") or d2rad.get("dungeons")):
            raids = list(d2rad.get("raids") or [])
            dungeons = list(d2rad.get("dungeons") or [])
            return {
                "reset_key": reset_key,
                "raids": raids,
                "dungeons": dungeons,
                "raid": raids[0] if raids else None,
                "raid_secondary": raids[1] if len(raids) > 1 else None,
                "dungeon": dungeons[0] if dungeons else None,
                "dungeon_secondary": dungeons[1] if len(dungeons) > 1 else None,
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "source": "d2rad",
                "nightmare_hunts": active_hunts,
            }

        # Fallback auto source: Bungie milestones.
        resolved = await self._resolve_featured_activities()
        api_raids = [name for name in [resolved.get("raid"), resolved.get("raid_secondary")] if name]
        api_dungeons = [name for name in [resolved.get("dungeon"), resolved.get("dungeon_secondary")] if name]
        return {
            "reset_key": reset_key,
            "raid": resolved.get("raid"),
            "raid_secondary": resolved.get("raid_secondary"),
            "dungeon": resolved.get("dungeon"),
            "dungeon_secondary": resolved.get("dungeon_secondary"),
            "raids": api_raids,
            "dungeons": api_dungeons,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "source": "api",
            "nightmare_hunts": active_hunts,
        }

    async def _post_for_guild(self, guild: discord.Guild, *, force: bool = False) -> tuple[bool, str]:
        guild_config = self.get_guild_config(guild.id)
        channel_id = guild_config.get("weekly_reset_channel_id")
        if not channel_id:
            return False, "No weekly reset channel configured."

        try:
            channel = await self.bot.fetch_channel(int(channel_id))
        except Exception:
            return False, "Configured channel is not accessible."

        if getattr(channel, "guild", None) is None or channel.guild.id != guild.id:
            return False, "Configured channel does not belong to this guild."

        if not force:
            now_utc = datetime.now(timezone.utc)
            post_gate_utc = self._current_post_gate_utc(now_utc)
            if now_utc < post_gate_utc:
                return False, "Waiting for Tuesday 17:05 (Europe/London) post window."

        current = await self._compute_snapshot()
        current_raids = list(current.get("raids") or [])
        current_dungeons = list(current.get("dungeons") or [])
        if not current_raids or not current_dungeons:
            return False, "Weekly data incomplete (raids/dungeons). Waiting for full this-week values."

        already_posted = guild_config.get("weekly_reset_last_posted_key") == current["reset_key"]
        previous_snapshot = guild_config.get("weekly_reset_snapshot") if isinstance(guild_config, dict) else None
        previous_source = str((previous_snapshot or {}).get("source") or "")
        previous_raids = list((previous_snapshot or {}).get("raids") or [])
        previous_dungeons = list((previous_snapshot or {}).get("dungeons") or [])
        previous_hunts = str((previous_snapshot or {}).get("nightmare_hunts") or "")
        current_source = str(current.get("source") or "")
        current_hunts = str(current.get("nightmare_hunts") or "")

        unchanged_snapshot = (
            previous_source == current_source
            and previous_raids == current_raids
            and previous_dungeons == current_dungeons
            and previous_hunts == current_hunts
        )

        current_embed_format = str(guild_config.get("weekly_reset_embed_format_version") or "")
        embed_is_current = current_embed_format == WEEKLY_RESET_EMBED_FORMAT_VERSION

        if already_posted and not force and unchanged_snapshot and embed_is_current:
            return False, "Reset for this week already posted."

        embed = self._build_embed(reset_key=current["reset_key"], current=current)

        message = None
        if not force:
            message_id = guild_config.get("weekly_reset_message_id")
            if message_id:
                try:
                    message = await channel.fetch_message(int(message_id))
                except Exception:
                    message = None

        if message is None:
            message = await channel.send(embed=embed)
            guild_config["weekly_reset_message_id"] = str(message.id)
        else:
            await message.edit(embed=embed)

        guild_config["weekly_reset_snapshot"] = current
        guild_config["weekly_reset_last_posted_key"] = current["reset_key"]
        guild_config["weekly_reset_embed_format_version"] = WEEKLY_RESET_EMBED_FORMAT_VERSION
        self.rules_config[str(guild.id)] = guild_config
        self.save_rules_config(self.rules_config)
        return True, "Posted weekly reset intelligence."

    @tasks.loop(minutes=5)
    async def weekly_reset_loop(self):
        for guild in self.bot.guilds:
            if not self._is_allowed_guild(guild.id):
                continue
            try:
                await self._post_for_guild(guild, force=False)
            except Exception as e:
                print(f"[WEEKLY_RESET_INTEL] guild {guild.id} failed: {e}")

    @weekly_reset_loop.before_loop
    async def before_weekly_reset_loop(self):
        await self.bot.wait_until_ready()
        # Run once at startup so weekly reset doesn't wait for the next 15-minute tick.
        for guild in self.bot.guilds:
            if not self._is_allowed_guild(guild.id):
                continue
            try:
                posted, message = await self._post_for_guild(guild, force=False)
                if posted:
                    print(f"[WEEKLY_RESET_INTEL] Startup catch-up posted for guild {guild.id}")
                else:
                    print(f"[WEEKLY_RESET_INTEL] Startup catch-up skipped for guild {guild.id}: {message}")
            except Exception as e:
                print(f"[WEEKLY_RESET_INTEL] Startup catch-up failed for guild {guild.id}: {e}")

    def start(self):
        if self._started:
            print("[WEEKLY_RESET_INTEL] start() skipped: already started", flush=True)
            return
        print("[WEEKLY_RESET_INTEL] start() initializing", flush=True)
        if not self.weekly_reset_loop.is_running():
            self.weekly_reset_loop.start()
        self.bot.loop.create_task(self._run_startup_post_check())
        self._started = True
        print("[WEEKLY_RESET_INTEL] start() initialized", flush=True)

    def register_commands(self):
        if self._commands_registered:
            return

        @self.bot.tree.command(
            name="set_weekly_reset_channel",
            description="Golden Oldies: set channel for Weekly Reset Intelligence posts.",
        )
        @app_commands.checks.has_permissions(administrator=True)
        @app_commands.describe(channel="Optional target channel. Leave blank to use this channel.")
        async def set_weekly_reset_channel(
            interaction: discord.Interaction,
            channel: discord.TextChannel | None = None,
        ):
            if interaction.guild_id is None or interaction.guild is None:
                await interaction.response.send_message("This command must be used in a server.", ephemeral=True)
                return

            if not self._is_allowed_guild(interaction.guild_id):
                await interaction.response.send_message("This command is only available in Golden Oldies.", ephemeral=True)
                return

            target_channel = channel or interaction.channel
            if not isinstance(target_channel, discord.TextChannel):
                await interaction.response.send_message("Use this in a text channel.", ephemeral=True)
                return

            if target_channel.guild.id != interaction.guild_id:
                await interaction.response.send_message("The selected channel must be in this server.", ephemeral=True)
                return

            guild_config = self.get_guild_config(interaction.guild_id)
            guild_config["weekly_reset_channel_id"] = str(target_channel.id)
            self.rules_config[str(interaction.guild_id)] = guild_config
            self.save_rules_config(self.rules_config)
            await interaction.response.send_message(
                f"Weekly Reset Intelligence channel set to {target_channel.mention}.",
                ephemeral=True,
            )

        @self.bot.tree.command(
            name="weekly_reset_intel",
            description="Golden Oldies: post current Weekly Reset Intelligence now.",
        )
        async def weekly_reset_intel(interaction: discord.Interaction):
            if interaction.guild_id is None or interaction.guild is None:
                await interaction.response.send_message("This command must be used in a server.", ephemeral=True)
                return

            if not self._is_allowed_guild(interaction.guild_id):
                await interaction.response.send_message("This command is only available in Golden Oldies.", ephemeral=True)
                return

            await interaction.response.defer(ephemeral=True)
            posted, message = await self._post_for_guild(interaction.guild, force=True)
            if posted:
                try:
                    await interaction.delete_original_response()
                except Exception:
                    pass
            else:
                await interaction.followup.send(message, ephemeral=True)

        @self.bot.tree.command(
            name="set_weekly_reset_manual",
            description="Golden Oldies: manually set weekly raids/dungeons (comma-separated).",
        )
        @app_commands.checks.has_permissions(administrator=True)
        @app_commands.describe(
            raids="Comma-separated raids, e.g. Crota's End, Deep Stone Crypt",
            dungeons="Comma-separated dungeons, e.g. Shattered Throne, Ghosts of the Deep",
        )
        async def set_weekly_reset_manual(interaction: discord.Interaction, raids: str, dungeons: str):
            if interaction.guild_id is None or interaction.guild is None:
                await interaction.response.send_message("This command must be used in a server.", ephemeral=True)
                return

            if not self._is_allowed_guild(interaction.guild_id):
                await interaction.response.send_message("This command is only available in Golden Oldies.", ephemeral=True)
                return

            raid_list = self._parse_list_input(raids)
            dungeon_list = self._parse_list_input(dungeons)
            if not raid_list and not dungeon_list:
                await interaction.response.send_message("Provide at least one raid or dungeon value.", ephemeral=True)
                return

            guild_config = self.get_guild_config(interaction.guild_id)
            guild_config["weekly_reset_manual"] = {
                "reset_key": self._reset_key(),
                "raids": raid_list,
                "dungeons": dungeon_list,
            }
            self.rules_config[str(interaction.guild_id)] = guild_config
            self.save_rules_config(self.rules_config)
            await interaction.response.send_message(
                "Weekly reset manual values saved for the current reset week.",
                ephemeral=True,
            )

        @self.bot.tree.command(
            name="clear_weekly_reset_manual",
            description="Golden Oldies: clear manual weekly reset overrides and use API again.",
        )
        @app_commands.checks.has_permissions(administrator=True)
        async def clear_weekly_reset_manual(interaction: discord.Interaction):
            if interaction.guild_id is None or interaction.guild is None:
                await interaction.response.send_message("This command must be used in a server.", ephemeral=True)
                return

            if not self._is_allowed_guild(interaction.guild_id):
                await interaction.response.send_message("This command is only available in Golden Oldies.", ephemeral=True)
                return

            guild_config = self.get_guild_config(interaction.guild_id)
            guild_config.pop("weekly_reset_manual", None)
            self.rules_config[str(interaction.guild_id)] = guild_config
            self.save_rules_config(self.rules_config)
            await interaction.response.send_message("Weekly reset manual overrides cleared.", ephemeral=True)

        @self.bot.tree.command(
            name="set_weekly_nightmare_hunts",
            description="Golden Oldies: set this reset week's Nightmare Hunts.",
        )
        @app_commands.checks.has_permissions(administrator=True)
        @app_commands.describe(
            hunts="Comma-separated hunts, e.g. Despair, Fear, Rage",
        )
        async def set_weekly_nightmare_hunts(interaction: discord.Interaction, hunts: str):
            if interaction.guild_id is None or interaction.guild is None:
                await interaction.response.send_message("This command must be used in a server.", ephemeral=True)
                return

            if not self._is_allowed_guild(interaction.guild_id):
                await interaction.response.send_message("This command is only available in Golden Oldies.", ephemeral=True)
                return

            hunt_list = self._parse_list_input(hunts)
            if not hunt_list:
                await interaction.response.send_message("Provide at least one hunt value.", ephemeral=True)
                return

            guild_config = self.get_guild_config(interaction.guild_id)
            by_reset = guild_config.get("weekly_nightmare_hunts_by_reset")
            if not isinstance(by_reset, dict):
                by_reset = {}
            current_reset_key = self._reset_key()
            by_reset[current_reset_key] = hunt_list
            guild_config["weekly_nightmare_hunts_by_reset"] = by_reset
            self.rules_config[str(interaction.guild_id)] = guild_config
            self.save_rules_config(self.rules_config)
            await interaction.response.send_message(
                f"Nightmare Hunts saved for this reset week: {' | '.join(hunt_list)}",
                ephemeral=True,
            )

        @self.bot.tree.command(
            name="clear_weekly_nightmare_hunts",
            description="Golden Oldies: clear this reset week's manual Nightmare Hunts override.",
        )
        @app_commands.checks.has_permissions(administrator=True)
        async def clear_weekly_nightmare_hunts(interaction: discord.Interaction):
            if interaction.guild_id is None or interaction.guild is None:
                await interaction.response.send_message("This command must be used in a server.", ephemeral=True)
                return

            if not self._is_allowed_guild(interaction.guild_id):
                await interaction.response.send_message("This command is only available in Golden Oldies.", ephemeral=True)
                return

            guild_config = self.get_guild_config(interaction.guild_id)
            by_reset = guild_config.get("weekly_nightmare_hunts_by_reset")
            if not isinstance(by_reset, dict):
                await interaction.response.send_message("No weekly Nightmare Hunts overrides are set.", ephemeral=True)
                return

            current_reset_key = self._reset_key()
            removed = by_reset.pop(current_reset_key, None)
            if removed is None:
                await interaction.response.send_message("No Nightmare Hunts override exists for this reset week.", ephemeral=True)
                return

            guild_config["weekly_nightmare_hunts_by_reset"] = by_reset
            self.rules_config[str(interaction.guild_id)] = guild_config
            self.save_rules_config(self.rules_config)
            await interaction.response.send_message("Cleared Nightmare Hunts override for this reset week.", ephemeral=True)

        self._commands_registered = True
