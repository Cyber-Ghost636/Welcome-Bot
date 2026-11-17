from __future__ import annotations

import asyncio
import io
import os
import re
from urllib.parse import urlparse
from datetime import datetime, timedelta, timezone
from pathlib import Path

import aiohttp
import discord


class TeamRaidTracker:
    def __init__(
        self,
        *,
        bot,
        base_dir: Path,
        rules_config,
        save_rules_config,
        get_bungie_api,
        get_guild_config,
        resolve_activity_name,
        parse_bungie_timestamp,
        format_duration,
        format_int_stat,
        debug_log,
        raid_history_count: int,
        initial_baseline_post_window_seconds: int,
        post_delay_minutes: int = 3,
        min_completions: int = 3,
        min_total_completions: int = 3,
    ):
        self.bot = bot
        self.base_dir = base_dir
        self.rules_config = rules_config
        self.save_rules_config = save_rules_config
        self.get_bungie_api = get_bungie_api
        self.get_guild_config = get_guild_config
        self.resolve_activity_name = resolve_activity_name
        self.parse_bungie_timestamp = parse_bungie_timestamp
        self.format_duration = format_duration
        self.format_int_stat = format_int_stat
        self.debug_log = debug_log
        self.raid_history_count = int(raid_history_count)
        self.initial_baseline_post_window_seconds = int(initial_baseline_post_window_seconds)
        self.post_delay_seconds = max(0, int(post_delay_minutes) * 60)
        self.min_completions = max(3, int(min_completions))
        self.player_fetch_concurrency = max(1, int(os.getenv("TEAM_PLAYER_CONCURRENCY", "4")))
        self.activity_icon_cache = {}
        self.modifier_name_cache = {}
        self.class_emoji_by_type = {
            0: ":titan:",
            1: ":hunter:",
            2: ":warlock:",
        }
        self.class_name_by_type = {
            0: "Titan",
            1: "Hunter",
            2: "Warlock",
        }
        self.min_total_completed_players = max(1, int(min_total_completions))

    def _safe_int(self, value):
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def _format_duration_compact(self, duration_seconds):
        duration = self._safe_int(duration_seconds)
        if duration is None or duration < 0:
            return "Unknown"

        minutes, seconds = divmod(duration, 60)
        hours, minutes = divmod(minutes, 60)
        if hours:
            return f"{hours}h {minutes}m {seconds}s"
        return f"{minutes}m {seconds}s"

    def _kd_text(self, kills, deaths):
        kills_int = self._safe_int(kills) or 0
        deaths_int = self._safe_int(deaths) or 0
        if deaths_int <= 0:
            return "N/A"
        return f"{kills_int / deaths_int:.2f}"

    def _display_name_from_destiny_user(self, destiny_user):
        base_name = destiny_user.get("bungieGlobalDisplayName") or destiny_user.get("displayName") or "Guardian"
        code = destiny_user.get("bungieGlobalDisplayNameCode")
        if code is None:
            return str(base_name)
        code_int = self._safe_int(code)
        if code_int is None:
            return f"{base_name}#{code}"
        return f"{base_name}#{code_int:04d}"

    def _compact_player_name(self, player_name):
        if not player_name:
            return "Guardian"
        return re.sub(r"#\d{1,6}$", "", str(player_name)).strip() or "Guardian"

    def _is_placeholder_player_name(self, player_name):
        if player_name is None:
            return True
        return str(player_name).strip().casefold() in {"", "guardian"}

    def _display_name_from_profile(self, profile):
        profile_data = (profile.get("profile", {}) or {}).get("data", {}) or {}
        user_info = profile_data.get("userInfo") or profile_data.get("userInfoCard") or {}
        display_name = user_info.get("bungieGlobalDisplayName") or user_info.get("displayName")
        if not display_name:
            return None

        display_code = user_info.get("bungieGlobalDisplayNameCode")
        if display_code is None:
            return str(display_name)

        code_int = self._safe_int(display_code)
        if code_int is None:
            return f"{display_name}#{display_code}"
        return f"{display_name}#{code_int:04d}"

    def _resolve_class_icon(self, player, guild_config):
        guild_config = guild_config or {}
        icon_map = (
            guild_config.get("team_raid_class_icon_by_name")
            or guild_config.get("team_dungeon_class_icon_by_name")
            or {}
        )

        class_name = str(player.get("class_name") or "Guardian").strip()
        class_key = class_name.casefold()
        class_hint = str(player.get("class_emoji") or "").strip().strip(":").casefold()

        icon_map_folded = {
            str(key).strip().casefold(): str(value)
            for key, value in icon_map.items()
            if key and value
        }

        configured_icon = (
            icon_map.get(class_name)
            or icon_map.get(class_name.title())
            or icon_map.get(class_key)
            or icon_map_folded.get(class_key)
            or icon_map_folded.get(class_hint)
        )
        if configured_icon:
            return str(configured_icon)

        return player.get("class_emoji") or ":guardian:"

    def _entry_stat_value(self, entry, stat_name):
        return (
            (entry.get("values", {}) or {})
            .get(stat_name, {})
            .get("basic", {})
            .get("value")
        )

    def _entry_extended_stat_value(self, entry, stat_name):
        return (
            (entry.get("extended", {}) or {})
            .get("values", {})
            .get(stat_name, {})
            .get("basic", {})
            .get("value")
        )

    def _normalize_bungie_image_url(self, path_value):
        if not path_value:
            return None
        if str(path_value).startswith("http://") or str(path_value).startswith("https://"):
            return str(path_value)
        return f"https://www.bungie.net{path_value}"

    def _normalize_raid_name(self, raid_name: str):
        if not raid_name:
            return "Raid"

        cleaned = str(raid_name).strip()
        # Remove common difficulty suffix wrappers and separators.
        cleaned = re.sub(r"\s*[:\-]\s*(master|standard)\s*$", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*[\(\[]\s*(master|standard)\s*[\)\]]\s*$", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()

        # Normalize leading article variations (for map matching only).
        if cleaned.casefold().startswith("the "):
            cleaned = cleaned[4:].strip()

        return cleaned

    def _canonical_raid_key(self, raid_name: str):
        normalized = self._normalize_raid_name(raid_name)
        # Canonical form for resilient comparisons across punctuation/casing.
        return "".join(ch for ch in normalized.casefold() if ch.isalnum())

    def _thumbnail_lookup_key(self, value: str):
        text = str(value or "").casefold().strip()
        if not text:
            return ""
        text = text.replace("resplendant", "resplendent")
        text = text.replace("insurrectiopn", "insurrection")
        return re.sub(r"[^a-z0-9]+", "", text)

    def _is_pantheon_activity_name(self, value: str | None):
        lowered = str(value or "").casefold().strip()
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

    async def _resolve_feat_names_from_pgcr(self, pgcr):
        selected_hashes = list((pgcr or {}).get("selectedSkullHashes") or [])
        if not selected_hashes:
            return []

        bungie_api = self.get_bungie_api()
        if bungie_api is None:
            return []

        feat_names = []
        seen = set()
        for raw_hash in selected_hashes:
            modifier_hash = self._safe_int(raw_hash)
            if modifier_hash is None:
                continue

            if modifier_hash in self.modifier_name_cache:
                resolved_name = self.modifier_name_cache[modifier_hash]
            else:
                try:
                    definition = await bungie_api.get_activity_modifier_definition(modifier_hash)
                except Exception:
                    definition = {}
                resolved_name = str((definition.get("displayProperties") or {}).get("name") or "").strip()
                self.modifier_name_cache[modifier_hash] = resolved_name

            lowered = resolved_name.casefold()
            if not lowered or lowered in seen:
                continue
            seen.add(lowered)
            feat_names.append(resolved_name)

        return feat_names

    def _pantheon_thumbnail_candidates(self, value: str):
        lowered = str(value or "").casefold().strip()
        if "pantheon" not in lowered:
            return []

        candidates = ["The Pantheon", "Pantheon"]
        calus_resplendent_tokens = {
            "calus resplendent",
            "argos",
            "gahlran",
            "emperor calus",
        }
        morgeth_surpassing_tokens = {
            "morgeth surpassing",
            "warpriest",
            "consecrated mind",
            "morgeth",
        }
        insurrection_revolutionary_tokens = {
            "insurrection prime revolutionary",
            "insurrection prime",
        }

        if any(token in lowered for token in calus_resplendent_tokens):
            candidates.append("Calus Resplendent")
        if any(token in lowered for token in morgeth_surpassing_tokens):
            candidates.append("Morgeth Surpassing")
        if any(token in lowered for token in insurrection_revolutionary_tokens):
            candidates.append("Insurrection Prime Revolutionary")

        return candidates

    async def resolve_raid_difficulty_label(self, activity_hash, raid_name: str | None = None):
        activity_hash_int = self._safe_int(activity_hash)
        if activity_hash_int is None:
            return "Standard"

        bungie_api = self.get_bungie_api()
        if bungie_api is None:
            return "Standard"

        try:
            definition = await bungie_api.get_activity_definition(activity_hash_int)
        except Exception:
            return "Standard"

        display = (definition.get("displayProperties") or {}).get("name") or ""
        display_lower = display.casefold()

        if "master" in display_lower:
            return "Master"
        if "standard" in display_lower:
            return "Standard"

        if raid_name and ": master" in str(raid_name).casefold():
            return "Master"
        return "Standard"

    async def resolve_raid_name(self, activity_hash, guild_config=None):
        activity_hash_int = self._safe_int(activity_hash)
        if activity_hash_int is None:
            return "Raid"

        bungie_api = self.get_bungie_api()
        if bungie_api is None:
            return "Raid"

        candidate_hashes = {activity_hash_int}
        if activity_hash_int < 0:
            candidate_hashes.add(activity_hash_int + (1 << 32))
        elif activity_hash_int > 0x7FFFFFFF:
            candidate_hashes.add(activity_hash_int - (1 << 32))

        for candidate_hash in candidate_hashes:
            try:
                definition = await bungie_api.get_activity_definition(candidate_hash)
            except Exception:
                continue

            display_name = (definition.get("displayProperties") or {}).get("name")
            if display_name:
                return str(display_name)

        resolved_name = await self.resolve_activity_name(activity_hash, guild_config=guild_config)
        if not resolved_name:
            return "Raid"

        resolved_text = str(resolved_name).strip()
        if resolved_text.casefold().startswith("dungeon (") and resolved_text.endswith(")"):
            return "Raid"
        return resolved_text

    async def resolve_raid_thumbnail_url(self, raid_name: str, activity_hash, guild_config):
        guild_config = guild_config or {}
        normalized_name = self._normalize_raid_name(raid_name)
        thumbnail_lookup_names = [normalized_name, *self._pantheon_thumbnail_candidates(normalized_name)]

        # Keep raid mappings prioritized for raid posts.
        custom_maps = [
            guild_config.get("team_raid_thumbnail_by_name"),
            guild_config.get("raid_thumbnail_by_name"),
            guild_config.get("team_dungeon_thumbnail_by_name"),
        ]

        def _find_thumbnail_in_map(name_map, lookup_name):
            if not isinstance(name_map, dict):
                return None

            lookup_normalized_name = self._normalize_raid_name(lookup_name)
            lookup_canonical = self._canonical_raid_key(lookup_normalized_name)
            lookup_compact = self._thumbnail_lookup_key(lookup_normalized_name)

            if (
                lookup_normalized_name
                and lookup_normalized_name in name_map
                and name_map[lookup_normalized_name]
            ):
                return str(name_map[lookup_normalized_name])

            normalized_lookup = str(lookup_normalized_name).casefold().strip()
            for key, value in name_map.items():
                if not key or not value:
                    continue
                key_normalized = self._normalize_raid_name(str(key)).casefold().strip()
                key_canonical = self._canonical_raid_key(str(key))
                key_compact = self._thumbnail_lookup_key(str(key))
                if key_normalized == normalized_lookup:
                    return str(value)
                if lookup_canonical and key_canonical == lookup_canonical:
                    return str(value)
                if lookup_compact and key_compact == lookup_compact:
                    return str(value)
            return None

        for custom_map in custom_maps:
            for lookup_name in thumbnail_lookup_names:
                matched = _find_thumbnail_in_map(custom_map, lookup_name)
                if matched:
                    return matched

        # Auto-resolve thumbnail from Bungie activity definition.
        activity_hash_int = self._safe_int(activity_hash)
        if activity_hash_int is not None:
            candidate_hashes = {activity_hash_int}
            if activity_hash_int < 0:
                candidate_hashes.add(activity_hash_int + (1 << 32))
            elif activity_hash_int > 0x7FFFFFFF:
                candidate_hashes.add(activity_hash_int - (1 << 32))

            for candidate_hash in candidate_hashes:
                if candidate_hash in self.activity_icon_cache:
                    return self.activity_icon_cache[candidate_hash]

            bungie_api = self.get_bungie_api()
            if bungie_api is not None:
                for candidate_hash in candidate_hashes:
                    try:
                        definition = await bungie_api.get_activity_definition(candidate_hash)
                    except Exception:
                        continue

                    display = definition.get("displayProperties") or {}
                    display_name = display.get("name")
                    if display_name:
                        display_lookup_names = [display_name, *self._pantheon_thumbnail_candidates(display_name)]
                        for custom_map in custom_maps:
                            for lookup_name in display_lookup_names:
                                mapped_from_definition = _find_thumbnail_in_map(custom_map, lookup_name)
                                if mapped_from_definition:
                                    return mapped_from_definition

                    icon_path = display.get("icon")
                    icon_url = self._normalize_bungie_image_url(icon_path)
                    if icon_url:
                        self.activity_icon_cache[candidate_hash] = icon_url
                        return icon_url

        # Fallback to global team/single-activity thumbnail settings.
        return (
            guild_config.get("team_raid_thumbnail_url")
            or guild_config.get("raid_embed_image")
            or guild_config.get("team_dungeon_thumbnail_url")
            or guild_config.get("dungeon_embed_image")
        )

    def _entry_completed(self, entry):
        try:
            return int(
                entry.get("values", {})
                .get("completed", {})
                .get("basic", {})
                .get("value", 0)
            ) == 1
        except (TypeError, ValueError):
            return False

    def _build_completion_from_pgcr(
        self,
        *,
        pgcr,
        activity_hash,
        period,
        watched_membership_map,
        min_completions,
        min_total_completions=None,
    ):
        entries = pgcr.get("entries", [])
        completed_entries = [entry for entry in entries if self._entry_completed(entry)]
        required_total = self.min_total_completed_players if min_total_completions is None else max(1, int(min_total_completions))
        if len(completed_entries) < required_total:
            return None

        all_completed_membership_ids = {
            str(
                (entry.get("player", {})
                .get("destinyUserInfo", {})
                .get("membershipId", ""))
            ).strip()
            for entry in completed_entries
            if entry.get("player", {})
            .get("destinyUserInfo", {})
            .get("membershipId")
        }
        all_completed_ids_ordered = []
        for entry in completed_entries:
            membership_id = str(
                (entry.get("player", {})
                .get("destinyUserInfo", {})
                .get("membershipId", ""))
            ).strip()
            if membership_id and membership_id not in all_completed_ids_ordered:
                all_completed_ids_ordered.append(membership_id)

        if watched_membership_map:
            selected_completed_ids = sorted(
                membership for membership in all_completed_membership_ids
                if membership in watched_membership_map
            )
        else:
            selected_completed_ids = sorted(all_completed_membership_ids)

        required_count = max(3, int(min_completions))
        if len(selected_completed_ids) < required_count:
            return None

        selected_players = []
        all_players = []
        for entry in completed_entries:
            entry_player = entry.get("player", {}) or {}
            destiny_user = (entry_player.get("destinyUserInfo", {}) or {})
            entry_membership_id = str(destiny_user.get("membershipId", "")).strip()
            if not entry_membership_id:
                continue

            entry_membership_type = self._safe_int(
                destiny_user.get("membershipType")
                or entry_player.get("membershipType")
                or entry.get("membershipType")
            )
            entry_character_id = str(
                entry_player.get("characterId")
                or entry.get("characterId")
                or ""
            ).strip()

            class_type = self._safe_int(
                entry_player.get("characterClass")
                or entry.get("characterClass")
                or self._entry_stat_value(entry, "classType")
                or self._entry_extended_stat_value(entry, "classType")
            )
            class_emoji = self.class_emoji_by_type.get(class_type, ":guardian:")
            power = (
                (entry.get("player", {}) or {}).get("lightLevel")
                or (entry.get("player", {}) or {}).get("characterPowerLevel")
                or (entry.get("player", {}) or {}).get("powerLevel")
                or self._entry_stat_value(entry, "lightLevel")
                or self._entry_stat_value(entry, "powerLevel")
            )
            kills = self._entry_stat_value(entry, "kills")
            deaths = self._entry_stat_value(entry, "deaths")
            assists = self._entry_stat_value(entry, "assists")

            player_payload = {
                "membership_id": entry_membership_id,
                "membership_type": entry_membership_type,
                "character_id": entry_character_id,
                "name": self._display_name_from_destiny_user(destiny_user),
                "class_emoji": class_emoji,
                "class_name": self.class_name_by_type.get(class_type, "Guardian"),
                "power": power,
                "kills": kills,
                "deaths": deaths,
                "assists": assists,
            }
            all_players.append(player_payload)
            if entry_membership_id in selected_completed_ids:
                selected_players.append(player_payload)

        players_by_id = {
            player_data.get("membership_id"): player_data
            for player_data in selected_players
            if player_data.get("membership_id")
        }
        ordered_players = []
        for membership in selected_completed_ids:
            ordered_player = players_by_id.get(membership)
            if ordered_player:
                ordered_players.append(ordered_player)
            else:
                fallback_name = watched_membership_map.get(membership) if watched_membership_map else "Guardian"
                ordered_players.append(
                    {
                        "membership_id": membership,
                        "membership_type": None,
                        "character_id": "",
                        "name": fallback_name,
                        "class_emoji": ":guardian:",
                        "class_name": "Guardian",
                        "power": None,
                        "kills": 0,
                        "deaths": 0,
                        "assists": 0,
                    }
                )

        all_players_by_id = {
            player_data.get("membership_id"): player_data
            for player_data in all_players
            if player_data.get("membership_id")
        }
        ordered_all_players = []
        for membership in all_completed_ids_ordered:
            ordered_player = all_players_by_id.get(membership)
            if ordered_player:
                ordered_all_players.append(ordered_player)
            else:
                fallback_name = watched_membership_map.get(membership) if watched_membership_map else "Guardian"
                ordered_all_players.append(
                    {
                        "membership_id": membership,
                        "membership_type": None,
                        "character_id": "",
                        "name": fallback_name,
                        "class_emoji": ":guardian:",
                        "class_name": "Guardian",
                        "power": None,
                        "kills": 0,
                        "deaths": 0,
                        "assists": 0,
                    }
                )

        duration_seconds = None
        for entry in completed_entries:
            duration_value = self._entry_stat_value(entry, "activityDurationSeconds")
            duration_seconds = self._safe_int(duration_value)
            if duration_seconds is not None:
                break

        return {
            "instance_id": str((pgcr.get("activityDetails", {}) or {}).get("instanceId") or ""),
            "period": period,
            "activity_hash": activity_hash,
            "watched_completed_count": len(selected_completed_ids),
            "watched_completed_names": [
                player_data.get("name") or "Guardian"
                for player_data in ordered_players
            ],
            "watched_players": ordered_players,
            "players": ordered_all_players,
            "duration_seconds": duration_seconds,
            "was_started_from_beginning": pgcr.get("activityWasStartedFromBeginning", True),
        }

    async def _enrich_players_with_profile_class(self, completion):
        bungie_api = self.get_bungie_api()
        if bungie_api is None or not completion:
            return

        watched_players = completion.get("watched_players", []) or []
        all_players = completion.get("players", []) or []

        players = []
        seen_targets = set()
        for player in [*watched_players, *all_players]:
            membership_id = str(player.get("membership_id") or "").strip()
            character_id = str(player.get("character_id") or "").strip()
            target_key = (membership_id, character_id)
            if target_key in seen_targets:
                continue
            seen_targets.add(target_key)
            players.append(player)

        if not players:
            return

        profile_cache = {}
        history_character_cache = {}
        instance_id = str(completion.get("instance_id") or "").strip()

        async def resolve_character_from_instance_history(membership_type, membership_id, profile):
            if membership_type is None or not membership_id or not instance_id:
                return None, "none"

            cache_key = (int(membership_type), str(membership_id), instance_id)
            if cache_key in history_character_cache:
                cached = history_character_cache[cache_key]
                return cached, "instance_history_exact" if cached is not None else "none"

            characters = ((profile.get("characters", {}) or {}).get("data", {}) or {})
            if not characters:
                history_character_cache[cache_key] = None
                return None, "none"

            max_count = max(25, int(self.raid_history_count))
            for candidate_character_id in characters.keys():
                for page in range(3):
                    try:
                        history = await bungie_api.get_activity_history(
                            membership_type=membership_type,
                            membership_id=membership_id,
                            character_id=candidate_character_id,
                            mode=4,
                            count=max_count,
                            page=page,
                        )
                    except Exception:
                        break

                    activities = history.get("activities", [])
                    if not activities:
                        break

                    for activity in activities:
                        details = activity.get("activityDetails", {}) or {}
                        activity_instance_id = str(details.get("instanceId") or "").strip()
                        if activity_instance_id and activity_instance_id == instance_id:
                            matched = characters.get(str(candidate_character_id))
                            history_character_cache[cache_key] = matched
                            return matched, "instance_history_exact"

            history_character_cache[cache_key] = None
            return None, "none"

        for player in players:
            membership_id = str(player.get("membership_id") or "").strip()
            membership_type = self._safe_int(player.get("membership_type"))
            character_id = str(player.get("character_id") or "").strip()
            if not membership_id:
                continue

            membership_types_to_try = [membership_type] if membership_type is not None else [1, 2, 3, 4, 5, 6]

            profile = None
            for candidate_type in membership_types_to_try:
                if candidate_type is None:
                    continue
                cache_key = (candidate_type, membership_id)
                profile = profile_cache.get(cache_key)
                if profile is None:
                    try:
                        profile = await bungie_api.get_profile(candidate_type, membership_id)
                    except Exception:
                        continue
                    profile_cache[cache_key] = profile
                if profile is not None:
                    membership_type = candidate_type
                    break

            if profile is None:
                continue

            if self._is_placeholder_player_name(player.get("name")):
                resolved_name = self._display_name_from_profile(profile)
                if resolved_name:
                    player["name"] = resolved_name

            characters = ((profile.get("characters", {}) or {}).get("data", {}) or {})
            character = None
            resolution_source = "none"
            if character_id:
                character = characters.get(character_id)
                if character is None:
                    character = characters.get(str(character_id))
                if character is not None:
                    resolution_source = "character_id"

            if character is None:
                character, resolution_source = await resolve_character_from_instance_history(
                    membership_type,
                    membership_id,
                    profile,
                )

            if character is None:
                continue

            class_type = self._safe_int(character.get("classType"))
            if class_type is None:
                continue
            if class_type not in self.class_name_by_type:
                continue

            existing_class_name = str(player.get("class_name") or "Guardian").strip()
            existing_known = existing_class_name.casefold() in {"titan", "hunter", "warlock"}
            # Preserve an existing known class unless new data comes from exact run evidence.
            if existing_known and resolution_source not in {"character_id", "instance_history_exact"}:
                continue

            player["class_name"] = self.class_name_by_type.get(class_type, "Guardian")
            player["class_emoji"] = self.class_emoji_by_type.get(class_type, ":guardian:")

    async def build_completion_from_instance(
        self,
        *,
        instance_id,
        watched_membership_map=None,
        min_completions=None,
    ):
        bungie_api = self.get_bungie_api()
        if bungie_api is None:
            return None

        pgcr = await bungie_api.get_pgcr(instance_id)
        activity_hash = (pgcr.get("activityDetails", {}) or {}).get("referenceId")
        period = pgcr.get("period")
        completion = self._build_completion_from_pgcr(
            pgcr=pgcr,
            activity_hash=activity_hash,
            period=period,
            watched_membership_map=watched_membership_map,
            min_completions=self.min_completions if min_completions is None else min_completions,
            min_total_completions=self.min_total_completed_players,
        )
        completion["feat_names"] = await self._resolve_feat_names_from_pgcr(pgcr)
        await self._enrich_players_with_profile_class(completion)
        return completion

    async def build_latest_completion_from_player(
        self,
        *,
        player,
        watched_membership_map=None,
        min_completions=None,
    ):
        bungie_api = self.get_bungie_api()
        if bungie_api is None:
            return None

        membership_id = player.get("membership_id")
        membership_type = player.get("membership_type")
        character_ids = player.get("character_ids", [])
        if not character_ids:
            profile = await bungie_api.get_profile(membership_type, membership_id)
            characters = profile.get("characters", {}).get("data", {})
            character_ids = list(characters.keys())
            player["character_ids"] = character_ids

        if not character_ids:
            return None

        seen_instances = set()
        candidates = []
        required_count = self.min_completions if min_completions is None else min_completions
        history_pages = 3

        for character_id in character_ids:
            for page in range(history_pages):
                history = await bungie_api.get_activity_history(
                    membership_type=membership_type,
                    membership_id=membership_id,
                    character_id=character_id,
                    mode=4,
                    count=self.raid_history_count,
                    page=page,
                )
                activities = history.get("activities", [])
                if not activities:
                    break

                for activity in activities:
                    values = activity.get("values", {})
                    completed = values.get("completed", {}).get("basic", {}).get("value", 0) == 1
                    if not completed:
                        continue

                    details = activity.get("activityDetails", {})
                    instance_id = str(details.get("instanceId", ""))
                    if not instance_id or instance_id in seen_instances:
                        continue
                    seen_instances.add(instance_id)

                    completion = await self.build_completion_from_instance(
                        instance_id=instance_id,
                        watched_membership_map=watched_membership_map,
                        min_completions=required_count,
                    )
                    if completion is None:
                        continue

                    completion["instance_id"] = instance_id
                    if completion.get("activity_hash") is None:
                        completion["activity_hash"] = details.get("referenceId")
                    if completion.get("period") is None:
                        completion["period"] = activity.get("period")

                    completion_dt = self.parse_bungie_timestamp(completion.get("period"))
                    candidates.append((completion_dt, completion))

        if not candidates:
            return None

        candidates.sort(
            key=lambda item: item[0] or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )
        return candidates[0][1]

    def build_team_raid_text(self, raid_name: str, completion):
        duration_text = self._format_duration_compact(completion.get("duration_seconds"))
        title_name = self._normalize_raid_name(raid_name)
        difficulty = completion.get("difficulty") or "Standard"
        lines = [f"{title_name} - {difficulty}"]
        feat_names = [str(name).strip() for name in (completion.get("feat_names") or []) if str(name).strip()]
        if self._is_pantheon_activity_name(title_name) and feat_names:
            lines[0] = f"{lines[0]} | Feats: {' | '.join(feat_names)}"

        lines.append("")

        display_players = completion.get("players") or completion.get("watched_players", [])
        for index, player in enumerate(display_players):
            player_name = player.get("name") or "Player"
            class_emoji = player.get("class_emoji") or ":guardian:"
            power = self._safe_int(player.get("power"))
            power_text = str(power) if power is not None else "?"
            kills = self._safe_int(player.get("kills")) or 0
            deaths = self._safe_int(player.get("deaths")) or 0
            assists = self._safe_int(player.get("assists")) or 0
            kd_text = self._kd_text(kills, deaths)

            lines.append(f"{player_name}:{class_emoji} | :power:{power_text}")
            lines.append(f"K: {kills} | D: {deaths} | A: {assists} | K/D: {kd_text}")

        completed_dt = self.parse_bungie_timestamp(completion.get("period"))
        if completed_dt is not None:
            lines.append("")
            lines.append(f"<t:{int(completed_dt.timestamp())}:R>")

        status_text = "Full" if completion.get("was_started_from_beginning", True) else "Checkpoint"
        lines.append(f"Time: {duration_text} | Status: {status_text}")

        return "\n".join(lines).strip()

    def build_team_raid_embed(self, raid_name: str, completion, guild_config, thumbnail_url=None):
        duration_text = self._format_duration_compact(completion.get("duration_seconds"))
        title_name = self._normalize_raid_name(raid_name)
        difficulty = completion.get("difficulty") or "Standard"
        completed_dt = self.parse_bungie_timestamp(completion.get("period"))
        feat_names = [str(name).strip() for name in (completion.get("feat_names") or []) if str(name).strip()]

        description_lines = [f"**{title_name}: {difficulty} - {duration_text}**"]
        if self._is_pantheon_activity_name(title_name) and feat_names:
            description_lines.append(f"Feats: **{' | '.join(feat_names)}**")
        if completed_dt is not None:
            description_lines.append(f"<t:{int(completed_dt.timestamp())}:R>")

        embed = discord.Embed(
            description="\n".join(description_lines).strip(),
            color=discord.Color.gold(),
        )

        display_players = completion.get("players") or completion.get("watched_players", [])
        display_players = sorted(
            display_players,
            key=lambda player: self._safe_int(player.get("kills")) or 0,
            reverse=True,
        )
        for player in display_players[:6]:
            player_name = self._compact_player_name(player.get("name"))
            class_icon = self._resolve_class_icon(player, guild_config)
            power = self._safe_int(player.get("power"))
            power_text = str(power) if power is not None else "?"
            kills = self._safe_int(player.get("kills")) or 0
            deaths = self._safe_int(player.get("deaths")) or 0
            assists = self._safe_int(player.get("assists")) or 0
            kd_text = self._kd_text(kills, deaths)

            embed.add_field(
                name=f"{player_name} {class_icon} :power:{power_text}",
                value=f"K: {kills} | D: {deaths} | A: {assists} | K/D: {kd_text}",
                inline=True,
            )

        embed.set_footer(text="via Wheelie Bin")

        return embed

    async def build_thumbnail_attachment(self, thumbnail_url: str | None, fallback_name: str):
        if not thumbnail_url:
            return None, None

        parsed = urlparse(thumbnail_url)
        extension = Path(parsed.path).suffix or ".png"
        safe_name = "".join(ch for ch in fallback_name.lower().replace(" ", "-") if ch.isalnum() or ch in {"-", "_"})
        filename = f"{safe_name}-thumbnail{extension}"

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(thumbnail_url) as response:
                    if response.status != 200:
                        return None, None
                    data = await response.read()
        except Exception:
            return None, None

        return discord.File(io.BytesIO(data), filename=filename), filename

    async def build_team_raid_message_payload(self, raid_name: str, completion, guild_config):
        thumbnail_url = await self.resolve_raid_thumbnail_url(
            raid_name,
            completion.get("activity_hash"),
            guild_config,
        )
        embed = self.build_team_raid_embed(raid_name, completion, guild_config, thumbnail_url=thumbnail_url)
        file, filename = await self.build_thumbnail_attachment(thumbnail_url, raid_name)
        if file and filename:
            embed.set_thumbnail(url=f"attachment://{filename}")
        elif thumbnail_url:
            embed.set_thumbnail(url=str(thumbnail_url))
        return embed, file

    async def detect_new_team_raid_completions(
        self,
        player,
        seen_instance_ids,
        watched_membership_map,
        processed_instance_ids,
        earliest_relevant_dt=None,
    ):
        completions = []
        bungie_api = self.get_bungie_api()
        if bungie_api is None:
            return completions

        membership_id = player.get("membership_id")
        membership_type = player.get("membership_type")
        character_ids = player.get("character_ids", [])
        if not character_ids:
            profile = await bungie_api.get_profile(membership_type, membership_id)
            characters = profile.get("characters", {}).get("data", {})
            character_ids = list(characters.keys())
            player["character_ids"] = character_ids

        for character_id in character_ids:
            history = await bungie_api.get_activity_history(
                membership_type=membership_type,
                membership_id=membership_id,
                character_id=character_id,
                mode=4,
                count=self.raid_history_count,
                page=0,
            )
            activities = history.get("activities", [])
            for activity in activities:
                activity_period = self.parse_bungie_timestamp(activity.get("period"))
                if (
                    earliest_relevant_dt is not None
                    and activity_period is not None
                    and activity_period < earliest_relevant_dt
                ):
                    break

                details = activity.get("activityDetails", {})
                instance_id = str(details.get("instanceId", ""))
                if not instance_id or instance_id in seen_instance_ids or instance_id in processed_instance_ids:
                    continue
                processed_instance_ids.add(instance_id)

                values = activity.get("values", {})
                completed = values.get("completed", {}).get("basic", {}).get("value", 0) == 1
                if not completed:
                    continue

                pgcr = await bungie_api.get_pgcr(instance_id)
                entries = pgcr.get("entries", [])
                was_started_from_beginning = pgcr.get("activityWasStartedFromBeginning", True)
                player_membership_id = str(membership_id)

                completed_entries = [entry for entry in entries if self._entry_completed(entry)]
                completed_membership_ids = {
                    str(
                        (entry.get("player", {})
                        .get("destinyUserInfo", {})
                        .get("membershipId", ""))
                    ).strip()
                    for entry in completed_entries
                    if entry.get("player", {})
                    .get("destinyUserInfo", {})
                    .get("membershipId")
                }

                participant_membership_ids = {
                    str(
                        (entry.get("player", {})
                        .get("destinyUserInfo", {})
                        .get("membershipId", ""))
                    ).strip()
                    for entry in entries
                    if entry.get("player", {})
                    .get("destinyUserInfo", {})
                    .get("membershipId")
                }

                if participant_membership_ids:
                    is_solo = participant_membership_ids == {player_membership_id}
                else:
                    is_solo = len(entries) == 1 or completed_membership_ids == {player_membership_id}
                if is_solo:
                    continue

                completion = self._build_completion_from_pgcr(
                    pgcr=pgcr,
                    activity_hash=details.get("referenceId"),
                    period=activity.get("period"),
                    watched_membership_map=watched_membership_map,
                    min_completions=self.min_completions,
                    min_total_completions=self.min_total_completed_players,
                )
                if completion is None:
                    continue
                completion["feat_names"] = await self._resolve_feat_names_from_pgcr(pgcr)
                await self._enrich_players_with_profile_class(completion)

                # Keep the instance id from activity history for consistency.
                completion["instance_id"] = instance_id
                completions.append(completion)

        completions.sort(key=lambda item: item.get("period", ""))
        return completions

    async def post_team_raid_completion(self, guild: discord.Guild, channel_id: str, completion):
        try:
            channel = await self.bot.fetch_channel(int(channel_id))
        except Exception as e:
            print(f"Failed to fetch raid channel for guild {guild.id}: {e}")
            return

        if getattr(channel, "guild", None) is None or channel.guild.id != guild.id:
            print(f"Raid channel mismatch for guild {guild.id}")
            return

        guild_config = self.get_guild_config(guild.id)

        raid_name = None
        activity_hash_int = self._safe_int(completion.get("activity_hash"))
        if activity_hash_int is not None:
            bungie_api = self.get_bungie_api()
            if bungie_api is not None:
                try:
                    definition = await bungie_api.get_activity_definition(activity_hash_int)
                    raid_name = (definition.get("displayProperties") or {}).get("name")
                except Exception:
                    raid_name = None

        if not raid_name:
            raid_name = await self.resolve_raid_name(completion.get("activity_hash"), guild_config=guild_config)

        completion["difficulty"] = await self.resolve_raid_difficulty_label(
            completion.get("activity_hash"),
            raid_name,
        )
        embed, file = await self.build_team_raid_message_payload(raid_name, completion, guild_config)
        if file:
            await channel.send(embed=embed, file=file)
        else:
            await channel.send(embed=embed)

        self.debug_log(
            f"Posted team raid completion guild={guild.id} raid={raid_name} "
            f"watched_count={completion.get('watched_completed_count')}"
        )

    async def run_team_raid_check_for_guild(self, guild: discord.Guild, channel_id: str, watched):
        if not channel_id or not watched:
            return

        guild_config = self.get_guild_config(guild.id)
        check_started_at = datetime.now(timezone.utc)
        threshold_dt = check_started_at - timedelta(seconds=self.initial_baseline_post_window_seconds)
        post_not_before_dt = check_started_at - timedelta(seconds=self.post_delay_seconds)

        watched_membership_map = {
            str(player.get("membership_id")): player.get("global_name", "Guardian")
            for player in watched
            if player.get("membership_id")
        }

        team_last_checked_dt = self.parse_bungie_timestamp(guild_config.get("team_raid_last_checked_at"))
        team_seen_instance_ids = set(guild_config.get("team_raid_seen_instance_ids", []))
        new_team_seen = list(guild_config.get("team_raid_seen_instance_ids", []))
        posted_instance_ids = set(guild_config.get("team_raid_posted_instance_ids", []))
        new_posted_instance_ids = list(guild_config.get("team_raid_posted_instance_ids", []))
        team_mark_seen_ids = set()
        team_processed_instance_ids = set()
        team_recent_historic_candidates = []

        fetch_semaphore = asyncio.Semaphore(self.player_fetch_concurrency)

        async def fetch_player_completions(player: dict):
            player_name = player.get("global_name", "Guardian")
            async with fetch_semaphore:
                try:
                    completions = await self.detect_new_team_raid_completions(
                        player,
                        team_seen_instance_ids,
                        watched_membership_map,
                        team_processed_instance_ids,
                        earliest_relevant_dt=threshold_dt,
                    )
                    return player, completions
                except Exception as e:
                    print(f"Team raid check failed for {player_name} in guild {guild.id}: {e}")
                    return player, []

        player_completion_results = await asyncio.gather(*(fetch_player_completions(player) for player in watched))

        for player, completions in player_completion_results:

            for completion in completions:
                instance_id = completion.get("instance_id")
                if not instance_id or instance_id in team_seen_instance_ids:
                    continue
                if instance_id in posted_instance_ids:
                    team_mark_seen_ids.add(instance_id)
                    continue

                completion_dt = self.parse_bungie_timestamp(completion.get("period"))
                if completion_dt is None:
                    team_mark_seen_ids.add(instance_id)
                    continue

                if completion_dt < threshold_dt:
                    team_mark_seen_ids.add(instance_id)
                    continue

                if completion_dt > post_not_before_dt:
                    continue

                if team_last_checked_dt is None:
                    team_recent_historic_candidates.append((completion_dt, completion))
                    continue

                if completion_dt <= team_last_checked_dt:
                    if completion_dt >= threshold_dt:
                        reserved_for_post = False
                        if instance_id not in posted_instance_ids:
                            posted_instance_ids.add(instance_id)
                            new_posted_instance_ids.append(instance_id)
                            reserved_for_post = True
                        try:
                            await self.post_team_raid_completion(guild, channel_id, completion)
                            team_mark_seen_ids.add(instance_id)
                        except Exception as e:
                            if reserved_for_post:
                                posted_instance_ids.discard(instance_id)
                                if instance_id in new_posted_instance_ids:
                                    new_posted_instance_ids.remove(instance_id)
                            print(
                                f"Failed to post recent historic team raid completion (instance {instance_id}) "
                                f"in guild {guild.id}: {e}"
                            )
                    else:
                        team_mark_seen_ids.add(instance_id)
                    continue

                try:
                    reserved_for_post = False
                    if instance_id not in posted_instance_ids:
                        posted_instance_ids.add(instance_id)
                        new_posted_instance_ids.append(instance_id)
                        reserved_for_post = True
                    await self.post_team_raid_completion(guild, channel_id, completion)
                    team_mark_seen_ids.add(instance_id)
                except Exception as e:
                    if reserved_for_post:
                        posted_instance_ids.discard(instance_id)
                        if instance_id in new_posted_instance_ids:
                            new_posted_instance_ids.remove(instance_id)
                    print(
                        f"Failed to post team raid completion (instance {instance_id}) "
                        f"in guild {guild.id}: {e}"
                    )
                    continue

        if team_recent_historic_candidates:
            team_recent_historic_candidates.sort(key=lambda item: item[0] or datetime.min.replace(tzinfo=timezone.utc))
            historic_processed_ids = set()
            for candidate_dt, candidate_completion in team_recent_historic_candidates:
                candidate_instance_id = candidate_completion.get("instance_id")
                if not candidate_instance_id or candidate_instance_id in historic_processed_ids:
                    continue
                if candidate_instance_id in posted_instance_ids:
                    team_mark_seen_ids.add(candidate_instance_id)
                    continue
                if candidate_dt is None or candidate_dt < threshold_dt:
                    team_mark_seen_ids.add(candidate_instance_id)
                    continue
                if candidate_dt > post_not_before_dt:
                    continue
                try:
                    reserved_for_post = False
                    if candidate_instance_id not in posted_instance_ids:
                        posted_instance_ids.add(candidate_instance_id)
                        new_posted_instance_ids.append(candidate_instance_id)
                        reserved_for_post = True
                    await self.post_team_raid_completion(guild, channel_id, candidate_completion)
                    historic_processed_ids.add(candidate_instance_id)
                    team_mark_seen_ids.add(candidate_instance_id)
                except Exception as e:
                    if reserved_for_post:
                        posted_instance_ids.discard(candidate_instance_id)
                        if candidate_instance_id in new_posted_instance_ids:
                            new_posted_instance_ids.remove(candidate_instance_id)
                    print(
                        f"Failed to post historic team raid completion (instance {candidate_instance_id}) "
                        f"in guild {guild.id}: {e}"
                    )

        guild_config["team_raid_last_checked_at"] = check_started_at.isoformat()
        if team_mark_seen_ids:
            for instance_id in team_mark_seen_ids:
                if instance_id not in team_seen_instance_ids:
                    team_seen_instance_ids.add(instance_id)
                    new_team_seen.append(instance_id)

        guild_config["team_raid_seen_instance_ids"] = new_team_seen[-500:]
        guild_config["team_raid_posted_instance_ids"] = new_posted_instance_ids[-5000:]
        self.rules_config[str(guild.id)] = guild_config
        self.save_rules_config(self.rules_config)
