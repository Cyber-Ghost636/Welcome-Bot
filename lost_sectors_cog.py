import asyncio
import datetime
import json
import os
from pathlib import Path
from zoneinfo import ZoneInfo

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands, tasks

import data


BASE_DIR = Path(__file__).resolve().parent
CONFIG_FILE = BASE_DIR / "server_config.json"
DEFAULT_LOST_SECTOR_ROTATION = [
    "Thrilladrome",
    "The Broken Deep",
    "The Blooming Deep",
    "Bunker E15",
    "Perdition",
    "Veles Labyrinth",
    "Exodus Garden 2A",
    "Chamber of Starlight",
    "Aphelion's Rest",
    "Bay of Drowned Wishes",
    "The Conflux",
    "The Orrery",
    "Ancient's Haunt",
]
DEFAULT_AUTO_ANCHOR_NAME = "Thrilladrome"
DEFAULT_AUTO_ANCHOR_DATE = "2025-07-01"
LOST_SECTOR_POST_TIMEZONE = os.getenv("LOST_SECTOR_POST_TIMEZONE", "Europe/London")
LOST_SECTOR_POST_HOUR_LOCAL = int(os.getenv("LOST_SECTOR_POST_HOUR_LOCAL", os.getenv("LOST_SECTOR_POST_HOUR_UTC", "18")))
LOST_SECTOR_POST_MINUTE_LOCAL = int(os.getenv("LOST_SECTOR_POST_MINUTE_LOCAL", os.getenv("LOST_SECTOR_POST_MINUTE_UTC", "0")))
LOST_SECTOR_RETRY_WINDOW_MINUTES = max(0, int(os.getenv("LOST_SECTOR_RETRY_WINDOW_MINUTES", "360")))
LOST_SECTOR_RETRY_INTERVAL_MINUTES = max(1, int(os.getenv("LOST_SECTOR_RETRY_INTERVAL_MINUTES", "1")))
LOST_SECTOR_HTTP_TIMEOUT_SECONDS = max(3, int(os.getenv("LOST_SECTOR_HTTP_TIMEOUT_SECONDS", "8")))
LOST_SECTOR_PREVIEW_TIMEOUT_SECONDS = max(1, int(os.getenv("LOST_SECTOR_PREVIEW_TIMEOUT_SECONDS", "3")))
try:
    LOST_SECTOR_POST_TZ = ZoneInfo(LOST_SECTOR_POST_TIMEZONE)
except Exception:
    LOST_SECTOR_POST_TZ = datetime.timezone.utc
LOST_SECTOR_FALLBACK_MAX_ENTRIES = max(1, int(os.getenv("LOST_SECTOR_FALLBACK_MAX_ENTRIES", "9")))
LOST_SECTOR_EMBED_FORMAT_VERSION = "destination_v4_all_today"


class LostSectors(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.daily_reset_announcer.start()
        self.daily_retry_announcer.start()

    def cog_unload(self):
        self.daily_reset_announcer.cancel()
        self.daily_retry_announcer.cancel()

    def _load_config(self) -> dict:
        if not CONFIG_FILE.exists():
            return {}
        try:
            payload = json.loads(CONFIG_FILE.read_text())
            if isinstance(payload, dict):
                return payload
        except Exception:
            pass
        return {}

    def _save_config(self, config: dict) -> None:
        try:
            CONFIG_FILE.write_text(json.dumps(config, indent=2))
        except Exception as e:
            print(f"[LOST_SECTORS] Failed to save server config: {e}")

    def _get_guild_config(self, guild_id: int) -> dict:
        config = self._load_config()
        guild_key = str(guild_id)
        guild_config = config.get(guild_key, {})
        if not isinstance(guild_config, dict):
            guild_config = {}
        config[guild_key] = guild_config
        self._save_config(config)
        return guild_config

    def _set_lost_sector_channel_for_guild(self, guild_id: int, channel_id: int) -> None:
        config = self._load_config()
        guild_key = str(guild_id)
        guild_config = config.get(guild_key, {})
        if not isinstance(guild_config, dict):
            guild_config = {}
        guild_config["lost_sector_channel_id"] = str(channel_id)
        # Reset tracked message when channel changes.
        guild_config.pop("lost_sector_message_id", None)
        config[guild_key] = guild_config
        self._save_config(config)

    def _get_lost_sector_message_id_for_guild(self, guild_id: int) -> int | None:
        config = self._load_config()
        guild_config = config.get(str(guild_id), {})
        if not isinstance(guild_config, dict):
            return None
        raw = guild_config.get("lost_sector_message_id")
        if raw is None:
            return None
        try:
            return int(raw)
        except (TypeError, ValueError):
            return None

    def _set_lost_sector_message_id_for_guild(self, guild_id: int, message_id: int | None) -> None:
        config = self._load_config()
        guild_key = str(guild_id)
        guild_config = config.get(guild_key, {})
        if not isinstance(guild_config, dict):
            guild_config = {}
        if message_id is None:
            guild_config.pop("lost_sector_message_id", None)
        else:
            guild_config["lost_sector_message_id"] = str(message_id)
        config[guild_key] = guild_config
        self._save_config(config)

    def _get_lost_sector_last_announced_date_for_guild(self, guild_id: int) -> str | None:
        config = self._load_config()
        guild_config = config.get(str(guild_id), {})
        if not isinstance(guild_config, dict):
            return None
        value = str(guild_config.get("lost_sector_last_announced_date") or "").strip()
        return value or None

    def _set_lost_sector_last_announced_date_for_guild(self, guild_id: int, date_text: str) -> None:
        config = self._load_config()
        guild_key = str(guild_id)
        guild_config = config.get(guild_key, {})
        if not isinstance(guild_config, dict):
            guild_config = {}
        guild_config["lost_sector_last_announced_date"] = str(date_text).strip()
        config[guild_key] = guild_config
        self._save_config(config)

    def _get_lost_sector_embed_format_version_for_guild(self, guild_id: int) -> str | None:
        config = self._load_config()
        guild_config = config.get(str(guild_id), {})
        if not isinstance(guild_config, dict):
            return None
        value = str(guild_config.get("lost_sector_embed_format_version") or "").strip()
        return value or None

    def _set_lost_sector_embed_format_version_for_guild(self, guild_id: int) -> None:
        config = self._load_config()
        guild_key = str(guild_id)
        guild_config = config.get(guild_key, {})
        if not isinstance(guild_config, dict):
            guild_config = {}
        guild_config["lost_sector_embed_format_version"] = LOST_SECTOR_EMBED_FORMAT_VERSION
        config[guild_key] = guild_config
        self._save_config(config)

    def _set_manual_lost_sector_for_guild(self, guild_id: int, sector_name: str) -> None:
        config = self._load_config()
        guild_key = str(guild_id)
        guild_config = config.get(guild_key, {})
        if not isinstance(guild_config, dict):
            guild_config = {}
        value = str(sector_name).strip()
        guild_config["lost_sector_manual_name"] = value
        guild_config["lost_sector_legend_manual_name"] = value
        guild_config["lost_sector_master_manual_name"] = value
        config[guild_key] = guild_config
        self._save_config(config)

    def _clear_manual_lost_sector_for_guild(self, guild_id: int) -> bool:
        config = self._load_config()
        guild_key = str(guild_id)
        guild_config = config.get(guild_key, {})
        if not isinstance(guild_config, dict):
            guild_config = {}
        existed = any(
            key in guild_config
            for key in (
                "lost_sector_manual_name",
                "lost_sector_legend_manual_name",
                "lost_sector_master_manual_name",
            )
        )
        guild_config.pop("lost_sector_manual_name", None)
        guild_config.pop("lost_sector_legend_manual_name", None)
        guild_config.pop("lost_sector_master_manual_name", None)
        config[guild_key] = guild_config
        self._save_config(config)
        return existed

    def _get_manual_lost_sector_for_guild(self, guild_id: int) -> str | None:
        config = self._load_config()
        guild_config = config.get(str(guild_id), {})
        if not isinstance(guild_config, dict):
            return None
        raw = str(guild_config.get("lost_sector_manual_name") or "").strip()
        return raw or None

    def _set_manual_lost_sector_difficulty_for_guild(self, guild_id: int, difficulty: str, sector_name: str) -> None:
        key_map = {
            "legend": "lost_sector_legend_manual_name",
            "master": "lost_sector_master_manual_name",
        }
        config_key = key_map.get(str(difficulty).strip().casefold())
        if config_key is None:
            return

        config = self._load_config()
        guild_key = str(guild_id)
        guild_config = config.get(guild_key, {})
        if not isinstance(guild_config, dict):
            guild_config = {}
        guild_config[config_key] = str(sector_name).strip()
        config[guild_key] = guild_config
        self._save_config(config)

    def _clear_manual_lost_sector_difficulty_for_guild(self, guild_id: int, difficulty: str) -> bool:
        key_map = {
            "legend": "lost_sector_legend_manual_name",
            "master": "lost_sector_master_manual_name",
        }
        config_key = key_map.get(str(difficulty).strip().casefold())
        if config_key is None:
            return False

        config = self._load_config()
        guild_key = str(guild_id)
        guild_config = config.get(guild_key, {})
        if not isinstance(guild_config, dict):
            guild_config = {}
        existed = config_key in guild_config
        guild_config.pop(config_key, None)
        config[guild_key] = guild_config
        self._save_config(config)
        return existed

    def _get_manual_lost_sector_pair_for_guild(self, guild_id: int) -> tuple[str | None, str | None]:
        config = self._load_config()
        guild_config = config.get(str(guild_id), {})
        if not isinstance(guild_config, dict):
            return None, None

        legend = str(guild_config.get("lost_sector_legend_manual_name") or "").strip() or None
        master = str(guild_config.get("lost_sector_master_manual_name") or "").strip() or None

        if legend is None and master is None:
            legacy = str(guild_config.get("lost_sector_manual_name") or "").strip() or None
            if legacy:
                legend = legacy
                master = legacy
        return legend, master

    def _get_lost_sector_channel_id_for_guild(self, guild_id: int) -> int | None:
        config = self._load_config()
        guild_config = config.get(str(guild_id), {})
        if not isinstance(guild_config, dict):
            return None
        raw = guild_config.get("lost_sector_channel_id")
        if raw is None:
            return None
        try:
            return int(raw)
        except (TypeError, ValueError):
            return None

    def _get_cached_daily_d2lostsector_payload_for_guild(self, guild_id: int) -> tuple[list[str], list[str]]:
        config = self._load_config()
        guild_config = config.get(str(guild_id), {})
        if not isinstance(guild_config, dict):
            return [], []

        cache_format = str(guild_config.get("lost_sector_cache_format") or "").strip()
        if cache_format != "text_v3":
            return [], []

        cache_date = str(guild_config.get("lost_sector_cache_date") or "").strip()
        today = datetime.datetime.now(datetime.timezone.utc).date().isoformat()
        if cache_date != today:
            return [], []

        cached_lines = guild_config.get("lost_sector_cache_lines")
        if isinstance(cached_lines, list):
            lines = [str(item).strip() for item in cached_lines if str(item).strip()]
            # Guard against stale oversized cached payloads from older formats.
            if lines and len("\n".join(lines)) > 950:
                return [], []
            # d2lostsector fallback should be a tiny daily list; larger payloads are usually parsed sections,
            # not today's active rotation.
            if len(lines) > LOST_SECTOR_FALLBACK_MAX_ENTRIES:
                return [], []
            if lines:
                return lines, []

        cached_sectors = guild_config.get("lost_sector_cache_sectors")
        cached_champions = guild_config.get("lost_sector_cache_champions")

        sectors = [str(item).strip() for item in cached_sectors if str(item).strip()] if isinstance(cached_sectors, list) else []
        champions = [str(item).strip().casefold() for item in cached_champions if str(item).strip()] if isinstance(cached_champions, list) else []
        return sectors, champions

    def _set_cached_daily_d2lostsector_payload_for_guild(self, guild_id: int, sectors: list[str], champions: list[str]) -> None:
        config = self._load_config()
        guild_key = str(guild_id)
        guild_config = config.get(guild_key, {})
        if not isinstance(guild_config, dict):
            guild_config = {}

        today = datetime.datetime.now(datetime.timezone.utc).date().isoformat()
        guild_config["lost_sector_cache_format"] = "text_v3"
        guild_config["lost_sector_cache_date"] = today
        guild_config["lost_sector_cache_lines"] = [str(item).strip() for item in sectors if str(item).strip()]
        guild_config["lost_sector_cache_sectors"] = [str(item).strip() for item in sectors if str(item).strip()]
        guild_config["lost_sector_cache_champions"] = [str(item).strip().casefold() for item in champions if str(item).strip()]
        config[guild_key] = guild_config
        self._save_config(config)

    def _get_auto_rotation_sector_for_guild(self, guild_id: int) -> str | None:
        config = self._load_config()
        guild_config = config.get(str(guild_id), {})
        if not isinstance(guild_config, dict):
            guild_config = {}

        anchor_name = str(guild_config.get("lost_sector_auto_anchor_name") or "").strip() or DEFAULT_AUTO_ANCHOR_NAME
        anchor_date_raw = str(guild_config.get("lost_sector_auto_anchor_date") or "").strip() or DEFAULT_AUTO_ANCHOR_DATE

        try:
            anchor_date = datetime.date.fromisoformat(anchor_date_raw)
        except ValueError:
            anchor_date = datetime.date.fromisoformat(DEFAULT_AUTO_ANCHOR_DATE)
            anchor_name = DEFAULT_AUTO_ANCHOR_NAME

        custom_rotation = guild_config.get("lost_sector_auto_rotation")
        if isinstance(custom_rotation, list):
            rotation = [str(item).strip() for item in custom_rotation if str(item).strip()]
        else:
            rotation = list(DEFAULT_LOST_SECTOR_ROTATION)

        if not rotation:
            rotation = list(DEFAULT_LOST_SECTOR_ROTATION)

        anchor_index = None
        normalized_anchor = anchor_name.casefold()
        for idx, name in enumerate(rotation):
            if name.casefold() == normalized_anchor:
                anchor_index = idx
                break
        if anchor_index is None:
            anchor_index = 0

        today = datetime.datetime.now(datetime.timezone.utc).date()
        day_offset = (today - anchor_date).days
        current_index = (anchor_index + day_offset) % len(rotation)
        return rotation[current_index]

    async def fetch_active_sectors(self):
        if not data.BUNGIE_API_KEY:
            return None

        url = "https://www.bungie.net/Platform/Destiny2/Milestones/"
        headers = {"X-API-Key": data.BUNGIE_API_KEY}

        try:
            timeout = aiohttp.ClientTimeout(total=LOST_SECTOR_HTTP_TIMEOUT_SECONDS)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url, headers=headers) as response:
                    if response.status != 200:
                        return None
                    payload = await response.json()
        except Exception:
            return None

        milestones = payload.get("Response", {})
        found = []
        unresolved_hashes: set[int] = set()
        for milestone_data in milestones.values():
            activities = milestone_data.get("activities") or []
            for activity in activities:
                activity_hash = activity.get("activityHash") if isinstance(activity, dict) else None
                if activity_hash in data.LOST_SECTOR_MAP:
                    found.append(data.LOST_SECTOR_MAP[activity_hash])
                elif isinstance(activity_hash, int):
                    unresolved_hashes.add(activity_hash)

        if not found and unresolved_hashes:
            for activity_hash in sorted(unresolved_hashes):
                resolved = await self._resolve_sector_name_from_activity_hash(activity_hash, headers=headers)
                if resolved:
                    found.append(resolved)

        return sorted(set(found))

    def _build_known_sector_name_index(self) -> dict[str, str]:
        """Map normalized base names to canonical display names from LOST_SECTOR_MAP."""
        index: dict[str, str] = {}
        for mapped_name in data.LOST_SECTOR_MAP.values():
            raw = str(mapped_name or "").strip()
            if not raw:
                continue
            base = raw.split("(", 1)[0].strip().casefold()
            if base and base not in index:
                index[base] = raw
        return index

    def _build_known_sector_location_index(self) -> list[tuple[str, str, str]]:
        """Return sector names and locations, longest names first for safe prefix matching."""
        index: list[tuple[str, str, str]] = []
        for mapped_name in data.LOST_SECTOR_MAP.values():
            raw = str(mapped_name or "").strip()
            if not raw:
                continue
            if "(" in raw and raw.endswith(")"):
                base, location = raw.rsplit("(", 1)
                display_name = base.strip()
                location = location[:-1].strip()
                if display_name and location:
                    index.append((display_name.casefold(), display_name, location))
        return sorted(index, key=lambda entry: len(entry[0]), reverse=True)

    def _extract_display_location_and_name(self, raw_line: str) -> tuple[str | None, str]:
        text = str(raw_line or "").strip()
        if not text:
            return None, "Unknown Lost Sector"

        if text.lower().startswith("legend/master:"):
            text = text.split(":", 1)[1].strip()

        # Normalize known sectors, including site labels that append the destination inline.
        lowered = text.casefold()
        for normalized_name, display_name, location in self._build_known_sector_location_index():
            if lowered.startswith(normalized_name):
                raw_remainder = text[len(display_name):]
                remainder = raw_remainder.strip()
                if remainder and remainder.startswith("(") and ")" in remainder:
                    inline_label, trailing = remainder[1:].split(")", 1)
                    if inline_label.strip().casefold() == location.casefold():
                        remainder = trailing.strip()
                elif remainder.casefold().startswith(location.casefold()):
                    remainder = remainder[len(location):].strip()

                if not raw_remainder or raw_remainder[0].isspace() or raw_remainder[0] == "(":
                    display_name = f"{display_name} {remainder}".strip() if remainder else display_name
                    return location, display_name

        # Keep explicit destinations for custom names that are not in the Bungie map.
        if "(" in text and text.endswith(")"):
            base, maybe_location = text.rsplit("(", 1)
            location = maybe_location[:-1].strip()
            name = base.strip()
            if name and location:
                return location, name

        return None, text

    def _format_embed_sector_lines(self, sectors: list[str]) -> list[str]:
        output: list[str] = []
        for raw in sectors:
            location, display_name = self._extract_display_location_and_name(raw)
            if location:
                output.append(f"**{location}**")
            output.append(f"- {display_name}")
        return output

    async def _resolve_sector_name_from_activity_hash(self, activity_hash: int, headers: dict[str, str]) -> str | None:
        url = f"https://www.bungie.net/Platform/Destiny2/Manifest/DestinyActivityDefinition/{activity_hash}/"
        try:
            timeout = aiohttp.ClientTimeout(total=LOST_SECTOR_HTTP_TIMEOUT_SECONDS)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url, headers=headers) as response:
                    if response.status != 200:
                        return None
                    payload = await response.json()
        except Exception:
            return None

        definition = payload.get("Response") or {}
        raw_name = ((definition.get("displayProperties") or {}).get("name") or "").strip()
        if not raw_name:
            return None

        index = self._build_known_sector_name_index()
        return index.get(raw_name.casefold())

    async def fetch_milestone_activity_hashes(self) -> tuple[str, list[int], list[int]]:
        """Return status plus mapped and unmapped milestone activity hashes."""
        if not data.BUNGIE_API_KEY:
            return "missing_api_key", [], []

        url = "https://www.bungie.net/Platform/Destiny2/Milestones/"
        headers = {"X-API-Key": data.BUNGIE_API_KEY}

        try:
            timeout = aiohttp.ClientTimeout(total=LOST_SECTOR_HTTP_TIMEOUT_SECONDS)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url, headers=headers) as response:
                    if response.status != 200:
                        return "api_error", [], []
                    payload = await response.json()
        except Exception:
            return "api_error", [], []

        milestones = payload.get("Response", {})
        all_hashes: set[int] = set()
        for milestone_data in milestones.values():
            activities = milestone_data.get("activities") or []
            for activity in activities:
                activity_hash = activity.get("activityHash") if isinstance(activity, dict) else None
                if isinstance(activity_hash, int):
                    all_hashes.add(activity_hash)

        mapped = sorted([h for h in all_hashes if h in data.LOST_SECTOR_MAP])
        unmapped = sorted([h for h in all_hashes if h not in data.LOST_SECTOR_MAP])
        return "ok", mapped, unmapped

    async def fetch_active_sector_status(self) -> tuple[str, list[str]]:
        """Return a status code and mapped sectors.

        Status values:
        - missing_api_key
        - api_error
        - no_mapped_sectors
        - ok
        """
        if not data.BUNGIE_API_KEY:
            return "missing_api_key", []

        sectors = await self.fetch_active_sectors()
        if sectors is None:
            return "api_error", []
        if not sectors:
            return "no_mapped_sectors", []
        return "ok", sectors

    async def fetch_active_sector_status_for_guild(self, guild_id: int) -> tuple[str, list[str]]:
        legend_manual, master_manual = self._get_manual_lost_sector_pair_for_guild(guild_id)
        if legend_manual or master_manual:
            if legend_manual and master_manual and legend_manual.casefold() == master_manual.casefold():
                lines = [f"Legend/Master: {legend_manual}"]
            else:
                lines = []
                if legend_manual:
                    lines.append(f"Legend: {legend_manual}")
                if master_manual:
                    lines.append(f"Master: {master_manual}")
            return "manual", lines

        manual = self._get_manual_lost_sector_for_guild(guild_id)
        if manual:
            return "manual", [f"Legend/Master: {manual}"]

        # Primary source for daily updates: Bungie API milestone/live data.
        # Start fallback fetch in parallel so slow API responses don't delay posting.
        site_task = asyncio.create_task(self._fetch_d2lostsector_site_lines())
        live_status, live_sectors = await self.fetch_active_sector_status()
        if live_status == "ok" and live_sectors:
            site_task.cancel()
            return live_status, live_sectors

        # Fallback source: d2lostsector.report.
        try:
            site_lines = await site_task
        except asyncio.CancelledError:
            site_lines = []
        except Exception:
            site_lines = []
        if len(site_lines) > LOST_SECTOR_FALLBACK_MAX_ENTRIES:
            site_lines = site_lines[:LOST_SECTOR_FALLBACK_MAX_ENTRIES]
        if site_lines:
            self._set_cached_daily_d2lostsector_payload_for_guild(guild_id, site_lines, [])
            return "d2lostsector", site_lines

        cached_sectors, cached_champions = self._get_cached_daily_d2lostsector_payload_for_guild(guild_id)
        if cached_sectors:
            # New cache format stores ready-to-display lines.
            if not cached_champions:
                return "d2lostsector_cached", cached_sectors

            # Backward compatibility with older cache format.
            lines = [f"Legend/Master: {name}" for name in cached_sectors]
            champion_line = self._format_champion_line(cached_champions)
            if champion_line:
                lines.append(champion_line)
            return "d2lostsector_cached", lines

        auto_sector = self._get_auto_rotation_sector_for_guild(guild_id)
        if auto_sector:
            return "auto_rotation", [f"Legend/Master: {auto_sector}"]

        return live_status, live_sectors

    def _build_embed(self, sectors: list[str]) -> discord.Embed:
        embed = discord.Embed(
            title="Daily Legend/Master Lost Sector",
            color=discord.Color.orange(),
            timestamp=datetime.datetime.now(datetime.timezone.utc),
        )
        if sectors:
            lines = self._format_embed_sector_lines(sectors)
            value = "\n".join(lines)
            if len(value) > 1024:
                clipped = []
                total = 0
                for line in lines:
                    extra = len(line) + (1 if clipped else 0)
                    if total + extra > 1000:
                        break
                    clipped.append(line)
                    total += extra
                value = "\n".join(clipped) + "\n- ..."
            embed.description = value
        embed.set_footer(text="via Wheelie Bin")
        return embed

    async def _fetch_upcoming_preview_lines(self, days: int = 2) -> list[str]:
        """Return short upcoming-day preview lines from d2lostsector calendar pages."""
        output: list[str] = []
        base_date = datetime.datetime.now(datetime.timezone.utc).date() + datetime.timedelta(days=1)
        for offset in range(max(0, int(days))):
            target_date = base_date + datetime.timedelta(days=offset)
            lines = await self._fetch_d2lostsector_site_lines_for_date(target_date)
            if not lines:
                continue
            first = lines[0].replace("Legend/Master:", "").strip()
            output.append(f"{target_date.isoformat()}: {first}")
        return output

    def _format_champion_suffix(self, champions: list[str]) -> str:
        ordered = ["barrier", "overload", "unstoppable"]
        parts: list[str] = []
        for key in ordered:
            if key in champions:
                parts.append(key)
        return " ".join(parts).strip()

    async def _fetch_d2lostsector_site_lines(self) -> list[str]:
        """Parse d2lostsector.report and return sector lines with text champions."""
        url = "https://d2lostsector.report/"
        try:
            timeout = aiohttp.ClientTimeout(total=LOST_SECTOR_HTTP_TIMEOUT_SECONDS)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url) as response:
                    if response.status != 200:
                        return []
                    html = await response.text()
        except Exception:
            return []

        marker = "Today&#x27;s World Lost Sectors"
        start = html.find(marker)
        if start == -1:
            marker = "Today's World Lost Sectors"
            start = html.find(marker)
        if start == -1:
            return []

        section_end = html.find("</section>", start)
        if section_end == -1:
            section_end = min(len(html), start + 80000)
        segment = html[start:section_end]

        try:
            import re

            article_blocks = re.findall(r"<article\b.*?</article>", segment, flags=re.IGNORECASE | re.DOTALL)
        except Exception:
            return []

        lines: list[str] = []
        seen_names: set[str] = set()
        for block in article_blocks:
            name_match = re.search(r"<h2[^>]*>([^<]+)</h2>", block, flags=re.IGNORECASE)
            if not name_match:
                continue
            name = str(name_match.group(1) or "").strip()
            if not name:
                continue

            key = name.casefold()
            if key in seen_names:
                continue
            seen_names.add(key)

            champion_matches = re.findall(r"Champion type:\s*(barrier|overload|unstoppable)", block, flags=re.IGNORECASE)
            champions: list[str] = []
            seen_champions: set[str] = set()
            for champ in champion_matches:
                champ_key = str(champ or "").strip().casefold()
                if champ_key and champ_key not in seen_champions:
                    seen_champions.add(champ_key)
                    champions.append(champ_key)

            suffix = self._format_champion_suffix(champions)
            if suffix:
                lines.append(f"Legend/Master: {name} ({suffix.replace(' ', '/')})")
            else:
                lines.append(f"Legend/Master: {name}")

        return lines

    async def _fetch_d2lostsector_site_lines_for_date(self, target_date: datetime.date) -> list[str]:
        """Parse d2lostsector.report calendar day view for a specific date."""
        url = f"https://d2lostsector.report/calendar?view={target_date.isoformat()}"
        try:
            timeout = aiohttp.ClientTimeout(total=LOST_SECTOR_HTTP_TIMEOUT_SECONDS)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url) as response:
                    if response.status != 200:
                        return []
                    html = await response.text()
        except Exception:
            return []

        try:
            import re

            article_blocks = re.findall(r"<article\\b.*?</article>", html, flags=re.IGNORECASE | re.DOTALL)
        except Exception:
            return []

        lines: list[str] = []
        seen_names: set[str] = set()
        for block in article_blocks:
            name_match = re.search(r"<h2[^>]*>([^<]+)</h2>", block, flags=re.IGNORECASE)
            if not name_match:
                continue
            name = str(name_match.group(1) or "").strip()
            if not name:
                continue

            key = name.casefold()
            if key in seen_names:
                continue
            seen_names.add(key)

            champion_matches = re.findall(
                r"Champion type:\s*(barrier|overload|unstoppable)",
                block,
                flags=re.IGNORECASE,
            )
            champions: list[str] = []
            seen_champions: set[str] = set()
            for champ in champion_matches:
                champ_key = str(champ or "").strip().casefold()
                if champ_key and champ_key not in seen_champions:
                    seen_champions.add(champ_key)
                    champions.append(champ_key)

            suffix = self._format_champion_suffix(champions)
            if suffix:
                lines.append(f"Legend/Master: {name} ({suffix.replace(' ', '/')})")
            else:
                lines.append(f"Legend/Master: {name}")

        if len(lines) > LOST_SECTOR_FALLBACK_MAX_ENTRIES:
            return lines[:LOST_SECTOR_FALLBACK_MAX_ENTRIES]
        return lines

    async def _fetch_d2lostsector_site_payload(self) -> tuple[list[str], list[str]]:
        """Parse d2lostsector.report homepage for today's sectors and champions."""
        url = "https://d2lostsector.report/"
        try:
            timeout = aiohttp.ClientTimeout(total=LOST_SECTOR_HTTP_TIMEOUT_SECONDS)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url) as response:
                    if response.status != 200:
                        return [], []
                    html = await response.text()
        except Exception:
            return [], []

        marker = "Today&#x27;s World Lost Sectors"
        start = html.find(marker)
        if start == -1:
            marker = "Today's World Lost Sectors"
            start = html.find(marker)
        if start == -1:
            return [], []

        section_end = html.find("</section>", start)
        if section_end == -1:
            section_end = min(len(html), start + 80000)

        segment = html[start:section_end]
        try:
            import re

            matches = re.findall(r"<h2[^>]*>([^<]+)</h2>", segment, flags=re.IGNORECASE)
            champion_matches = re.findall(r"Champion type:\s*(barrier|overload|unstoppable)", segment, flags=re.IGNORECASE)
        except Exception:
            return [], []

        cleaned: list[str] = []
        seen: set[str] = set()
        for item in matches:
            name = str(item or "").strip()
            if not name:
                continue
            key = name.casefold()
            if key in seen:
                continue
            seen.add(key)
            cleaned.append(name)

        champions: list[str] = []
        seen_champions: set[str] = set()
        for champ in champion_matches:
            champ_key = str(champ or "").strip().casefold()
            if champ_key in seen_champions:
                continue
            seen_champions.add(champ_key)
            champions.append(champ_key)

        return cleaned, champions

    def _format_champion_line(self, champions: list[str]) -> str | None:
        ordered = ["barrier", "overload", "unstoppable"]
        parts: list[str] = []
        for key in ordered:
            if key in champions:
                parts.append(key)
        if not parts:
            return None
        return "Champions: " + "/".join(parts)

    async def _announce_for_guild(self, guild: discord.Guild, today_key: str) -> None:
        tracked_message_id = self._get_lost_sector_message_id_for_guild(guild.id)
        already_announced = self._get_lost_sector_last_announced_date_for_guild(guild.id) == today_key
        format_is_current = (
            self._get_lost_sector_embed_format_version_for_guild(guild.id)
            == LOST_SECTOR_EMBED_FORMAT_VERSION
        )
        if already_announced and format_is_current and tracked_message_id is not None:
            print(f"[LOST_SECTORS] Guild {guild.id} already announced for {today_key}")
            return

        status, sectors = await self.fetch_active_sector_status_for_guild(guild.id)
        if status not in {"ok", "manual", "auto_rotation", "d2lostsector", "d2lostsector_cached"} or not sectors:
            print(f"[LOST_SECTORS] Skipping guild {guild.id}: status={status}")
            return

        guild_config = self._get_guild_config(guild.id)
        channel_id = self._get_lost_sector_channel_id_for_guild(guild.id)
        if not channel_id:
            weekly_channel_raw = guild_config.get("weekly_reset_channel_id") if isinstance(guild_config, dict) else None
            try:
                channel_id = int(weekly_channel_raw) if weekly_channel_raw is not None else None
            except (TypeError, ValueError):
                channel_id = None
        if not channel_id:
            channel_id = data.ANNOUNCEMENT_CHANNEL_ID
        if not channel_id:
            return

        channel = self.bot.get_channel(channel_id)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(channel_id)
            except Exception:
                print(f"[LOST_SECTORS] Could not find channel ID {channel_id} for guild {guild.id}")
                return

        if getattr(channel, "guild", None) is None or channel.guild.id != guild.id:
            print(f"[LOST_SECTORS] Channel {channel_id} does not belong to guild {guild.id}")
            return

        embed = self._build_embed(sectors)
        try:
            upcoming_lines = await asyncio.wait_for(
                self._fetch_upcoming_preview_lines(days=2),
                timeout=LOST_SECTOR_PREVIEW_TIMEOUT_SECONDS,
            )
        except Exception:
            upcoming_lines = []
        if upcoming_lines:
            embed.add_field(
                name="Coming Days",
                value="\n".join(f"- {line}" for line in upcoming_lines),
                inline=False,
            )
        if tracked_message_id:
            try:
                existing_message = await channel.fetch_message(tracked_message_id)
                await existing_message.edit(embed=embed)
                self._set_lost_sector_last_announced_date_for_guild(guild.id, today_key)
                self._set_lost_sector_embed_format_version_for_guild(guild.id)
                print(f"[LOST_SECTORS] Updated tracked message {tracked_message_id} for guild {guild.id} ({today_key})")
                return
            except Exception as e:
                print(
                    f"[LOST_SECTORS] Could not edit tracked message {tracked_message_id} "
                    f"for guild {guild.id}: {e}",
                    flush=True,
                )
                self._set_lost_sector_message_id_for_guild(guild.id, None)

        # Recover from missing/stale tracked message ID by reusing the latest matching bot embed.
        try:
            async for message in channel.history(limit=30):
                if message.author != self.bot.user or not message.embeds:
                    continue
                first = message.embeds[0]
                if str(getattr(first, "title", "") or "").strip() != "Daily Legend/Master Lost Sector":
                    continue
                await message.edit(embed=embed)
                self._set_lost_sector_message_id_for_guild(guild.id, message.id)
                self._set_lost_sector_last_announced_date_for_guild(guild.id, today_key)
                self._set_lost_sector_embed_format_version_for_guild(guild.id)
                print(f"[LOST_SECTORS] Recovered and updated message {message.id} for guild {guild.id} ({today_key})")
                return
        except Exception as e:
            print(f"[LOST_SECTORS] History recovery failed for guild {guild.id}: {e}", flush=True)

        try:
            posted = await channel.send(embed=embed)
            self._set_lost_sector_message_id_for_guild(guild.id, posted.id)
            self._set_lost_sector_last_announced_date_for_guild(guild.id, today_key)
            self._set_lost_sector_embed_format_version_for_guild(guild.id)
            print(f"[LOST_SECTORS] Posted message {posted.id} for guild {guild.id} ({today_key})")
        except Exception as e:
            print(f"[LOST_SECTORS] Failed to post/update message for guild {guild.id}: {e}")

    async def _run_startup_catchup_if_needed(self) -> None:
        now_local = datetime.datetime.now(LOST_SECTOR_POST_TZ)
        post_hour = max(0, min(23, LOST_SECTOR_POST_HOUR_LOCAL))
        post_minute = max(0, min(59, LOST_SECTOR_POST_MINUTE_LOCAL))
        post_anchor = now_local.replace(hour=post_hour, minute=post_minute, second=0, microsecond=0)
        if now_local < post_anchor:
            print(
                "[LOST_SECTORS] Startup catch-up skipped: "
                f"now_local={now_local.isoformat()} before schedule={post_anchor.isoformat()}"
            )
            return

        today_key = now_local.date().isoformat()
        print(
            "[LOST_SECTORS] Startup catch-up running: "
            f"now_local={now_local.isoformat()} today_key={today_key}"
        )
        for guild in self.bot.guilds:
            await self._announce_for_guild(guild, today_key)

    def _is_within_retry_window(self, now_local: datetime.datetime) -> bool:
        post_hour = max(0, min(23, LOST_SECTOR_POST_HOUR_LOCAL))
        post_minute = max(0, min(59, LOST_SECTOR_POST_MINUTE_LOCAL))
        anchor = now_local.replace(hour=post_hour, minute=post_minute, second=0, microsecond=0)
        if now_local < anchor:
            return False
        if LOST_SECTOR_RETRY_WINDOW_MINUTES <= 0:
            return True
        elapsed_seconds = (now_local - anchor).total_seconds()
        return elapsed_seconds <= LOST_SECTOR_RETRY_WINDOW_MINUTES * 60

    @tasks.loop(
        time=datetime.time(
            hour=max(0, min(23, LOST_SECTOR_POST_HOUR_LOCAL)),
            minute=max(0, min(59, LOST_SECTOR_POST_MINUTE_LOCAL)),
            tzinfo=LOST_SECTOR_POST_TZ,
        )
    )
    async def daily_reset_announcer(self):
        today_key = datetime.datetime.now(LOST_SECTOR_POST_TZ).date().isoformat()
        for guild in self.bot.guilds:
            await self._announce_for_guild(guild, today_key)

    @tasks.loop(minutes=LOST_SECTOR_RETRY_INTERVAL_MINUTES)
    async def daily_retry_announcer(self):
        now_local = datetime.datetime.now(LOST_SECTOR_POST_TZ)
        if not self._is_within_retry_window(now_local):
            return

        today_key = now_local.date().isoformat()
        for guild in self.bot.guilds:
            already_announced = self._get_lost_sector_last_announced_date_for_guild(guild.id) == today_key
            if already_announced:
                continue
            print(f"[LOST_SECTORS] Retry announce for guild {guild.id} ({today_key})")
            await self._announce_for_guild(guild, today_key)

    @daily_reset_announcer.before_loop
    async def before_daily_reset_announcer(self):
        await self.bot.wait_until_ready()
        print(
            "[LOST_SECTORS] Announcer ready. "
            f"schedule_local={max(0, min(23, LOST_SECTOR_POST_HOUR_LOCAL)):02d}:"
            f"{max(0, min(59, LOST_SECTOR_POST_MINUTE_LOCAL)):02d} "
            f"tz={LOST_SECTOR_POST_TIMEZONE}",
            flush=True,
        )
        await self._run_startup_catchup_if_needed()

    @daily_retry_announcer.before_loop
    async def before_daily_retry_announcer(self):
        await self.bot.wait_until_ready()

    @app_commands.command(name="lostsector", description="Show currently active lost sector(s).")
    async def lost_sector_slash(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        if interaction.guild_id is None:
            await interaction.followup.send("This command must be used in a server.", ephemeral=True)
            return

        status, sectors = await self.fetch_active_sector_status_for_guild(interaction.guild_id)
        if status in {"ok", "manual", "auto_rotation", "d2lostsector", "d2lostsector_cached"}:
            text = "Current daily Legend/Master Lost Sector:\n" + "\n".join([f"- {name}" for name in sectors])
            if status == "manual":
                text += "\n\n(using manual override)"
            elif status == "auto_rotation":
                text += "\n\n(using automatic rotation fallback)"
            elif status == "d2lostsector":
                text += "\n\n(using d2lostsector.report fallback)"
            elif status == "d2lostsector_cached":
                text += "\n\n(using cached daily source)"
            await interaction.followup.send(text, ephemeral=True)
        else:
            if status == "missing_api_key":
                await interaction.followup.send("BUNGIE_API_KEY is not configured for this bot.", ephemeral=True)
            elif status == "api_error":
                await interaction.followup.send("Bungie API is unavailable right now. Please try again shortly.", ephemeral=True)
            else:
                await interaction.followup.send("Bungie API responded, but no known lost-sector hashes matched the current data set.", ephemeral=True)

    @app_commands.command(name="testlostsector", description="Post a test Legend/Master lost sector embed in this channel.")
    @app_commands.checks.has_permissions(administrator=True)
    async def test_lost_sector_embed(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        if interaction.guild_id is None:
            await interaction.followup.send("This command must be used in a server.", ephemeral=True)
            return

        status, sectors = await self.fetch_active_sector_status_for_guild(interaction.guild_id)

        target_channel = interaction.channel
        if not isinstance(target_channel, discord.TextChannel):
            await interaction.followup.send("Please run this command in a text channel.", ephemeral=True)
            return

        if status == "ok":
            await target_channel.send(embed=self._build_embed(sectors))
        elif status == "manual":
            embed = self._build_embed(sectors)
            embed.set_footer(text="via Wheelie Bin (manual override)")
            await target_channel.send(embed=embed)
        elif status == "auto_rotation":
            embed = self._build_embed(sectors)
            embed.set_footer(text="via Wheelie Bin (automatic rotation fallback)")
            await target_channel.send(embed=embed)
        elif status == "d2lostsector":
            await target_channel.send(embed=self._build_embed(sectors))
        elif status == "d2lostsector_cached":
            await target_channel.send(embed=self._build_embed(sectors))
        else:
            fallback = ["Example Lost Sector (preview)"]
            preview_embed = self._build_embed(fallback)
            preview_embed.title = "Daily Legend/Master Lost Sector (Preview)"
            preview_embed.set_footer(text="Preview only - live data unavailable or unmapped")
            await target_channel.send(embed=preview_embed)

        try:
            await interaction.delete_original_response()
        except Exception:
            pass

    @app_commands.command(name="lostsector_hashes", description="Admin debug: show mapped and unmapped milestone activity hashes.")
    @app_commands.checks.has_permissions(administrator=True)
    async def lost_sector_hashes(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        status, mapped, unmapped = await self.fetch_milestone_activity_hashes()
        if status == "missing_api_key":
            await interaction.followup.send("BUNGIE_API_KEY is not configured for this bot.", ephemeral=True)
            return
        if status == "api_error":
            await interaction.followup.send("Bungie API is unavailable right now. Please try again shortly.", ephemeral=True)
            return

        lines = [
            f"Milestone activity hashes seen: {len(mapped) + len(unmapped)}",
            f"Mapped in LOST_SECTOR_MAP: {len(mapped)}",
            f"Unmapped: {len(unmapped)}",
        ]

        if mapped:
            lines.append("")
            lines.append("Mapped hashes:")
            lines.extend([f"- {activity_hash}" for activity_hash in mapped[:25]])

        if unmapped:
            lines.append("")
            lines.append("Unmapped hashes (add these if they are Lost Sectors):")
            lines.extend([f"- {activity_hash}" for activity_hash in unmapped[:50]])

        if len(lines) > 1:
            await interaction.followup.send("\n".join(lines), ephemeral=True)
        else:
            await interaction.followup.send("No milestone activity hashes found.", ephemeral=True)

    @app_commands.command(name="set_lost_sector_channel", description="Set the channel for daily lost sector posts.")
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.describe(channel="Optional text channel. Leave empty to use this channel.")
    async def set_lost_sector_channel(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel | None = None,
    ):
        if interaction.guild_id is None:
            await interaction.response.send_message("This command must be used in a server.", ephemeral=True)
            return

        target_channel = channel
        if target_channel is None:
            # Allow command use from a thread by falling back to its parent text channel.
            if isinstance(interaction.channel, discord.Thread):
                parent = interaction.channel.parent
                if isinstance(parent, discord.TextChannel):
                    target_channel = parent
            elif isinstance(interaction.channel, discord.TextChannel):
                target_channel = interaction.channel

        if target_channel is None:
            await interaction.response.send_message(
                "Please run this command in a text channel or provide a text channel.",
                ephemeral=True,
            )
            return

        if target_channel.guild.id != interaction.guild_id:
            await interaction.response.send_message("The selected channel must be in this server.", ephemeral=True)
            return

        self._set_lost_sector_channel_for_guild(interaction.guild_id, target_channel.id)
        await interaction.response.send_message(
            f"Lost sector announcements channel set to {target_channel.mention}.",
            ephemeral=True,
        )

    @set_lost_sector_channel.error
    async def set_lost_sector_channel_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ):
        if isinstance(error, app_commands.MissingPermissions):
            responder = interaction.followup.send if interaction.response.is_done() else interaction.response.send_message
            await responder(
                "You need the Manage Server permission to use this command.",
                ephemeral=True,
            )
            return
        raise error

    @app_commands.command(name="set_lost_sector_manual", description="Set a manual lost sector name override for this server.")
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(sector_name="Example: K1 Logistics (The Moon)")
    async def set_lost_sector_manual(self, interaction: discord.Interaction, sector_name: str):
        if interaction.guild_id is None:
            await interaction.response.send_message("This command must be used in a server.", ephemeral=True)
            return

        value = str(sector_name or "").strip()
        if not value:
            await interaction.response.send_message("Please provide a non-empty lost sector name.", ephemeral=True)
            return

        self._set_manual_lost_sector_for_guild(interaction.guild_id, value)
        await interaction.response.send_message(
            f"Manual lost sector override set to: {value}",
            ephemeral=True,
        )

    @app_commands.command(name="set_legend_lost_sector", description="Set manual daily Legend lost sector name for this server.")
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(sector_name="Example: K1 Logistics")
    async def set_legend_lost_sector(self, interaction: discord.Interaction, sector_name: str):
        if interaction.guild_id is None:
            await interaction.response.send_message("This command must be used in a server.", ephemeral=True)
            return

        value = str(sector_name or "").strip()
        if not value:
            await interaction.response.send_message("Please provide a non-empty lost sector name.", ephemeral=True)
            return

        self._set_manual_lost_sector_difficulty_for_guild(interaction.guild_id, "legend", value)
        await interaction.response.send_message(f"Manual Legend lost sector set to: {value}", ephemeral=True)

    @app_commands.command(name="set_master_lost_sector", description="Set manual daily Master lost sector name for this server.")
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(sector_name="Example: K1 Logistics")
    async def set_master_lost_sector(self, interaction: discord.Interaction, sector_name: str):
        if interaction.guild_id is None:
            await interaction.response.send_message("This command must be used in a server.", ephemeral=True)
            return

        value = str(sector_name or "").strip()
        if not value:
            await interaction.response.send_message("Please provide a non-empty lost sector name.", ephemeral=True)
            return

        self._set_manual_lost_sector_difficulty_for_guild(interaction.guild_id, "master", value)
        await interaction.response.send_message(f"Manual Master lost sector set to: {value}", ephemeral=True)

    @app_commands.command(name="clear_lost_sector_manual", description="Clear the manual lost sector override for this server.")
    @app_commands.checks.has_permissions(administrator=True)
    async def clear_lost_sector_manual(self, interaction: discord.Interaction):
        if interaction.guild_id is None:
            await interaction.response.send_message("This command must be used in a server.", ephemeral=True)
            return

        removed = self._clear_manual_lost_sector_for_guild(interaction.guild_id)
        if removed:
            await interaction.response.send_message("Manual lost sector override cleared.", ephemeral=True)
        else:
            await interaction.response.send_message("No manual lost sector override was set.", ephemeral=True)

    @app_commands.command(name="clear_legend_lost_sector", description="Clear manual daily Legend lost sector name for this server.")
    @app_commands.checks.has_permissions(administrator=True)
    async def clear_legend_lost_sector(self, interaction: discord.Interaction):
        if interaction.guild_id is None:
            await interaction.response.send_message("This command must be used in a server.", ephemeral=True)
            return

        removed = self._clear_manual_lost_sector_difficulty_for_guild(interaction.guild_id, "legend")
        if removed:
            await interaction.response.send_message("Manual Legend lost sector cleared.", ephemeral=True)
        else:
            await interaction.response.send_message("No manual Legend lost sector was set.", ephemeral=True)

    @app_commands.command(name="clear_master_lost_sector", description="Clear manual daily Master lost sector name for this server.")
    @app_commands.checks.has_permissions(administrator=True)
    async def clear_master_lost_sector(self, interaction: discord.Interaction):
        if interaction.guild_id is None:
            await interaction.response.send_message("This command must be used in a server.", ephemeral=True)
            return

        removed = self._clear_manual_lost_sector_difficulty_for_guild(interaction.guild_id, "master")
        if removed:
            await interaction.response.send_message("Manual Master lost sector cleared.", ephemeral=True)
        else:
            await interaction.response.send_message("No manual Master lost sector was set.", ephemeral=True)

    @app_commands.command(name="set_lost_sector_auto_anchor", description="Enable automatic daily rotation fallback from an anchor day.")
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(
        sector_name="Lost sector name for the anchor date (example: Thrilladrome)",
        anchor_date="UTC date in YYYY-MM-DD format (leave empty for today)",
    )
    async def set_lost_sector_auto_anchor(
        self,
        interaction: discord.Interaction,
        sector_name: str,
        anchor_date: str | None = None,
    ):
        if interaction.guild_id is None:
            await interaction.response.send_message("This command must be used in a server.", ephemeral=True)
            return

        name_value = str(sector_name or "").strip()
        if not name_value:
            await interaction.response.send_message("Please provide a valid lost sector name.", ephemeral=True)
            return

        if anchor_date:
            date_text = str(anchor_date).strip()
            try:
                datetime.date.fromisoformat(date_text)
            except ValueError:
                await interaction.response.send_message("anchor_date must be YYYY-MM-DD.", ephemeral=True)
                return
        else:
            date_text = datetime.datetime.now(datetime.timezone.utc).date().isoformat()

        config = self._load_config()
        guild_key = str(interaction.guild_id)
        guild_config = config.get(guild_key, {})
        if not isinstance(guild_config, dict):
            guild_config = {}

        rotation = guild_config.get("lost_sector_auto_rotation")
        if not isinstance(rotation, list) or not rotation:
            rotation = list(DEFAULT_LOST_SECTOR_ROTATION)

        normalized = [str(item).strip() for item in rotation if str(item).strip()]
        if name_value.casefold() not in {item.casefold() for item in normalized}:
            normalized.append(name_value)

        guild_config["lost_sector_auto_rotation"] = normalized
        guild_config["lost_sector_auto_anchor_name"] = name_value
        guild_config["lost_sector_auto_anchor_date"] = date_text
        config[guild_key] = guild_config
        self._save_config(config)

        await interaction.response.send_message(
            f"Automatic lost sector rotation fallback enabled with anchor {name_value} on {date_text} UTC.",
            ephemeral=True,
        )

    @app_commands.command(name="disable_lost_sector_auto", description="Disable automatic rotation fallback for lost sectors.")
    @app_commands.checks.has_permissions(administrator=True)
    async def disable_lost_sector_auto(self, interaction: discord.Interaction):
        if interaction.guild_id is None:
            await interaction.response.send_message("This command must be used in a server.", ephemeral=True)
            return

        config = self._load_config()
        guild_key = str(interaction.guild_id)
        guild_config = config.get(guild_key, {})
        if not isinstance(guild_config, dict):
            guild_config = {}

        existed = any(
            key in guild_config
            for key in ("lost_sector_auto_anchor_name", "lost_sector_auto_anchor_date", "lost_sector_auto_rotation")
        )
        guild_config.pop("lost_sector_auto_anchor_name", None)
        guild_config.pop("lost_sector_auto_anchor_date", None)
        guild_config.pop("lost_sector_auto_rotation", None)
        config[guild_key] = guild_config
        self._save_config(config)

        if existed:
            await interaction.response.send_message("Automatic lost sector rotation fallback disabled.", ephemeral=True)
        else:
            await interaction.response.send_message("Automatic lost sector rotation fallback was not enabled.", ephemeral=True)


async def setup(bot):
    await bot.add_cog(LostSectors(bot))
