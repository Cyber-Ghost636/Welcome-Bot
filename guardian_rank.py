import discord
from discord.ext import tasks


GUARDIAN_RANK_INFO = {
    1: {
        "name": "New Light",
        "description": "New to the light — learning the ropes of the Last City.",
    },
    2: {
        "name": "Explorer",
        "description": "Finding their footing — starting to explore Destiny's systems.",
    },
    3: {
        "name": "Initiate",
        "description": "A steady Guardian — completing activities and earning experience.",
    },
    4: {
        "name": "Scout",
        "description": "Skilled and dependable — often leading small fireteams.",
    },
    5: {
        "name": "Adventurer",
        "description": "Seasoned adventurer — comfortable with a range of activities.",
    },
    6: {
        "name": "Veteran",
        "description": "A respected Veteran — consistent contributor to clan goals.",
    },
    7: {
        "name": "Elite",
        "description": "Elite Guardian — high skill and achievement in endgame content.",
    },
    8: {
        "name": "Justiciar",
        "description": "Justiciar — a guardian of standards, often mentoring others.",
    },
    9: {
        "name": "Vanquisher",
        "description": "Vanquisher — renowned for PvE or PvP prowess and accomplishments.",
    },
    10: {
        "name": "Exemplar",
        "description": "Exemplar — an example to the Light; exceptional performance.",
    },
    11: {
        "name": "Paragon",
        "description": "Paragon — among the highest echelon of Guardians, legendary status.",
    },
}

GUARDIAN_RANK_SYMBOL_URL = "https://i.postimg.cc/L86yLFs7/CROWN.png"


class GuardianRankCommands:
    def __init__(
        self,
        bot,
        get_bungie_api,
        resolve_player_fn,
        get_guild_config,
        rules_config,
        save_rules_config,
        clan_id,
        check_interval_minutes=30,
    ):
        self.bot = bot
        self.get_bungie_api = get_bungie_api
        self.resolve_player = resolve_player_fn
        self.get_guild_config = get_guild_config
        self.rules_config = rules_config
        self.save_rules_config = save_rules_config
        self.clan_id = str(clan_id).strip() if clan_id else ""
        self.check_interval_minutes = int(check_interval_minutes)
        self._commands_registered = False
        self._tracker_started = False
        self._rank_icon_cache = {}
        # Cache of seen PGCR instance IDs so we don't process the same activity twice
        self._seen_pgcr_ids = set()

    def _get_rank_name(self, rank_number: int):
        try:
            info = GUARDIAN_RANK_INFO.get(int(rank_number))
            return info["name"] if info else f"Rank {rank_number}"
        except (TypeError, ValueError):
            return "Unknown Rank"

    def _get_rank_description(self, rank_number: int):
        try:
            info = GUARDIAN_RANK_INFO.get(int(rank_number))
            return info["description"] if info else "A Guardian on their journey."
        except (TypeError, ValueError):
            return "A Guardian on their journey."

    async def _get_rank_icon_url(self, rank_number: int):
        try:
            key = int(rank_number)
        except (TypeError, ValueError):
            return GUARDIAN_RANK_SYMBOL_URL

        if key not in self._rank_icon_cache:
            self._rank_icon_cache[key] = GUARDIAN_RANK_SYMBOL_URL
        return self._rank_icon_cache[key]

    def _is_member_in_guild(self, guild: discord.Guild, player_name: str | None):
        if not player_name:
            return False
        normalized_name = player_name.casefold()
        for member in guild.members:
            if member.display_name.casefold() == normalized_name or member.name.casefold() == normalized_name:
                return True
        return False

    async def _resolve_watch_player(self, player_entry):
        if not isinstance(player_entry, dict):
            return None

        membership_id = player_entry.get("membership_id")
        membership_type = player_entry.get("membership_type")
        global_name = player_entry.get("global_name") or player_entry.get("bungie_name")
        avatar_url = player_entry.get("avatar_url")

        if membership_id and membership_type is not None:
            try:
                return {
                    "global_name": global_name or "Guardian",
                    "membership_id": str(membership_id),
                    "membership_type": int(membership_type),
                    "avatar_url": avatar_url,
                }
            except (TypeError, ValueError):
                return None

        if global_name and self.resolve_player:
            player = await self.resolve_player(global_name)
            if player:
                return player

        return None

    async def _get_auto_watch_players_for_clan(self):
        bungie_api = self.get_bungie_api()
        if bungie_api is None or not self.clan_id:
            return []

        roster = await bungie_api.get_clan_roster(self.clan_id)
        players = []
        for member in roster:
            membership_id = member.get("membershipId")
            membership_type = member.get("membershipType")
            display_name = member.get("displayName") or "Guardian"
            if not membership_id or membership_type is None:
                continue
            players.append(
                {
                    "global_name": display_name,
                    "membership_id": str(membership_id),
                    "membership_type": int(membership_type),
                    "avatar_url": member.get("avatarUrl"),
                }
            )
        return players

    async def _get_watch_players_for_guild(self, guild: discord.Guild):
        guild_config = self.get_guild_config(guild.id)
        watch_entries = guild_config.get("guardian_rank_watch", [])
        if watch_entries:
            players = []
            for entry in watch_entries:
                player = await self._resolve_watch_player(entry)
                if player:
                    players.append(player)
            return players, False

        players = []
        seen_names = set()
        for member in guild.members:
            if member.bot:
                continue
            member_name = (member.display_name or member.name or "").strip()
            if not member_name:
                continue
            normalized_name = member_name.casefold()
            if normalized_name in seen_names:
                continue
            seen_names.add(normalized_name)

            if not self.resolve_player:
                continue
            player = await self.resolve_player(member_name)
            if player:
                players.append(player)

        if players:
            return players, True

        if self.clan_id:
            players = []
            try:
                players = await self._get_auto_watch_players_for_clan()
            except Exception as e:
                print(f"Failed to fetch clan roster for guardian rank tracking in guild {guild.id}: {e}")
                return [], False
            return players, True

        return [], False

    def _build_rank_embed(
        self,
        player_name: str,
        new_rank: int,
        avatar_url: str | None = None,
        rank_icon_url: str | None = None,
    ):
        rank_name = self._get_rank_name(new_rank)
        rank_desc = self._get_rank_description(new_rank)
        embed = discord.Embed(
            title=f"Has achieved Guardian Rank {new_rank}",
            description=f"**{rank_name}**\n{rank_desc}",
            color=discord.Color.gold(),
        )
        if avatar_url:
            embed.set_author(name=player_name, icon_url=avatar_url)
        else:
            embed.set_author(name=player_name)

        if rank_icon_url:
            embed.set_thumbnail(url=rank_icon_url)

        embed.set_footer(text="via Wheelie Bin")
        return embed

    async def _post_rank_change(self, guild: discord.Guild, channel_id: str, player_name: str, old_rank: int, new_rank: int):
        try:
            channel = await self.bot.fetch_channel(int(channel_id))
        except Exception as e:
            print(f"Failed to fetch guardian rank channel for guild {guild.id}: {e}")
            return

        if getattr(channel, "guild", None) is None or channel.guild.id != guild.id:
            print(f"Guardian rank channel mismatch for guild {guild.id}")
            return

        avatar_url = None
        for member in guild.members:
            if member.display_name.casefold() == player_name.casefold() or member.name.casefold() == player_name.casefold():
                avatar_url = member.display_avatar.url
                break

        rank_icon_url = await self._get_rank_icon_url(new_rank)
        embed = self._build_rank_embed(
            player_name,
            new_rank,
            avatar_url=avatar_url,
            rank_icon_url=rank_icon_url,
        )
        await channel.send(embed=embed)

    async def _get_primary_character_id(self, bungie_api, membership_type, membership_id):
        """
        Use profile (components=200) to get characters and pick one.
        For simplicity, we just pick the first character.
        """
        try:
            profile = await bungie_api.get_profile(membership_type, membership_id)
        except Exception as e:
            print(f"[PGCR] Failed to fetch profile for {membership_type}:{membership_id}: {e}")
            return None

        characters = profile.get("characters", {}).get("data", {})
        if not characters:
            return None

        # Pick the first character ID
        character_id = next(iter(characters.keys()), None)
        return character_id

    async def _check_guardian_ranks_for_guild(self, guild: discord.Guild):
        bungie_api = self.get_bungie_api()
        if bungie_api is None:
            return

        guild_config = self.get_guild_config(guild.id)
        channel_id = guild_config.get("guardian_rank_channel_id")
        if not channel_id:
            return

        players, auto_watched = await self._get_watch_players_for_guild(guild)
        if not players:
            return

        previous_ranks = guild_config.get("guardian_rank_last_seen", {})
        if not isinstance(previous_ranks, dict):
            previous_ranks = {}

        updated_ranks = dict(previous_ranks)
        any_changes = False

        for member in players:
            player_name = member.get("global_name", "Guardian")
            if auto_watched and not self._is_member_in_guild(guild, player_name):
                continue

            membership_id = member.get("membership_id")
            membership_type = member.get("membership_type")
            if not membership_id or membership_type is None:
                continue

            try:
                profile_data = await bungie_api.get_guardian_ranks(membership_type, membership_id)
            except Exception as e:
                print(f"Guardian rank fetch failed for {player_name}: {e}")
                continue

            current_rank = (
                profile_data.get("profile", {})
                .get("data", {})
                .get("currentGuardianRank")
            )
            if current_rank is None:
                continue

            key = f"{membership_type}:{membership_id}"
            old_rank = previous_ranks.get(key)
            updated_ranks[key] = int(current_rank)

            if old_rank is None:
                any_changes = True
                continue

            try:
                old_rank_int = int(old_rank)
                new_rank_int = int(current_rank)
            except (TypeError, ValueError):
                any_changes = True
                continue

            if new_rank_int > old_rank_int:
                try:
                    await self._post_rank_change(guild, channel_id, player_name, old_rank_int, new_rank_int)
                except Exception as e:
                    print(f"Failed to post guardian rank update for {player_name}: {e}")
                any_changes = True
            elif new_rank_int != old_rank_int:
                any_changes = True

        if any_changes or not previous_ranks:
            guild_config["guardian_rank_last_seen"] = updated_ranks
            self.rules_config[str(guild.id)] = guild_config
            self.save_rules_config(self.rules_config)

    @tasks.loop(minutes=30)
    async def guardian_rank_check_loop(self):
        bungie_api = self.get_bungie_api()
        if bungie_api is None:
            return
        for guild in self.bot.guilds:
            await self._check_guardian_ranks_for_guild(guild)

    @guardian_rank_check_loop.before_loop
    async def before_guardian_rank_check_loop(self):
        await self.bot.wait_until_ready()

    @tasks.loop(seconds=45)
    async def pgcr_watch_loop(self):
        """
        PGCR watcher:
        - For each watched player, find a character
        - Fetch recent activity history
        - For new instance IDs, trigger a rank check
        """
        bungie_api = self.get_bungie_api()
        if bungie_api is None:
            return

        for guild in self.bot.guilds:
            guild_config = self.get_guild_config(guild.id)
            channel_id = guild_config.get("guardian_rank_channel_id")
            if not channel_id:
                continue

            players, auto_watched = await self._get_watch_players_for_guild(guild)
            if not players:
                continue

            for member in players:
                player_name = member.get("global_name", "Guardian")
                membership_id = member.get("membership_id")
                membership_type = member.get("membership_type")

                if not membership_id or membership_type is None:
                    continue

                if auto_watched and not self._is_member_in_guild(guild, player_name):
                    continue

                character_id = await self._get_primary_character_id(
                    bungie_api, membership_type, membership_id
                )
                if not character_id:
                    continue

                try:
                    history = await bungie_api.get_activity_history(
                        membership_type,
                        membership_id,
                        character_id,
                        mode=82,
                        count=10,
                        page=0,
                    )
                except Exception as e:
                    print(f"[PGCR] Failed to fetch activity history for {player_name}: {e}")
                    continue

                activities = history.get("activities", [])
                if not activities:
                    continue

                for activity in activities:
                    instance_id = activity.get("activityDetails", {}).get("instanceId")
                    if not instance_id:
                        continue

                    if instance_id in self._seen_pgcr_ids:
                        continue

                    self._seen_pgcr_ids.add(instance_id)

                    # Trigger immediate rank check based on this activity
                    await self._trigger_rank_check_from_pgcr(
                        guild, channel_id, member
                    )

    @pgcr_watch_loop.before_loop
    async def before_pgcr_watch_loop(self):
        await self.bot.wait_until_ready()

    async def _trigger_rank_check_from_pgcr(self, guild, channel_id, member):
        """
        When a new PGCR (activity) is seen, fetch profile guardian rank
        and announce if it increased.
        """
        bungie_api = self.get_bungie_api()
        if bungie_api is None:
            return

        player_name = member.get("global_name", "Guardian")
        membership_id = member.get("membership_id")
        membership_type = member.get("membership_type")

        if not membership_id or membership_type is None:
            return

        try:
            profile_data = await bungie_api.get_guardian_ranks(membership_type, membership_id)
        except Exception as e:
            print(f"[PGCR] Profile fetch failed for {player_name}: {e}")
            return

        current_rank = (
            profile_data.get("profile", {})
            .get("data", {})
            .get("currentGuardianRank")
        )
        if current_rank is None:
            return

        guild_config = self.get_guild_config(guild.id)
        previous_ranks = guild_config.get("guardian_rank_last_seen", {})
        if not isinstance(previous_ranks, dict):
            previous_ranks = {}

        key = f"{membership_type}:{membership_id}"
        old_rank = previous_ranks.get(key)

        previous_ranks[key] = int(current_rank)
        guild_config["guardian_rank_last_seen"] = previous_ranks
        self.rules_config[str(guild.id)] = guild_config
        self.save_rules_config(self.rules_config)

        if old_rank is not None:
            try:
                old_rank_int = int(old_rank)
                new_rank_int = int(current_rank)
            except (TypeError, ValueError):
                return

            if new_rank_int > old_rank_int:
                await self._post_rank_change(
                    guild, channel_id, player_name, old_rank_int, new_rank_int
                )

    def start(self):
        if self._tracker_started:
            return
        if not self.guardian_rank_check_loop.is_running():
            self.guardian_rank_check_loop.change_interval(minutes=self.check_interval_minutes)
            self.guardian_rank_check_loop.start()
        if not self.pgcr_watch_loop.is_running():
            self.pgcr_watch_loop.start()
        self._tracker_started = True

    def register_commands(self):
        if self._commands_registered:
            return

        @self.bot.tree.command(
            name="guardian_rank",
            description="Look up a Guardian's current, season-start, and lifetime highest rank.",
        )
        @discord.app_commands.describe(bungie_name="Bungie name, e.g. WH33LS#8650")
        async def guardian_rank(interaction: discord.Interaction, bungie_name: str):
            await interaction.response.defer()

            bungie_api = self.get_bungie_api()
            if bungie_api is None:
                await interaction.followup.send("Bungie API is not configured.", ephemeral=True)
                return

            player = await self.resolve_player(bungie_name)
            if not player:
                await interaction.followup.send(
                    f"Could not find a Destiny 2 player named **{bungie_name}**.", ephemeral=True
                )
                return

            try:
                profile_data = await bungie_api.get_guardian_ranks(
                    player["membership_type"], player["membership_id"]
                )
            except Exception as e:
                print(f"Guardian rank lookup failed: {e}")
                await interaction.followup.send(
                    "Failed to fetch Guardian rank data from Bungie.", ephemeral=True
                )
                return

            profile_info = profile_data.get("profile", {}).get("data", {})
            current_rank = profile_info.get("currentGuardianRank")
            renewed_rank = profile_info.get("renewedGuardianRank")
            lifetime_rank = profile_info.get("lifetimeHighestGuardianRank")

            if current_rank is None:
                await interaction.followup.send(
                    f"No Guardian Rank data found for **{player['global_name']}**.", ephemeral=True
                )
                return

            display_name = player["global_name"]
            avatar_url = player.get("avatar_url")
            current_rank_name = self._get_rank_name(int(current_rank))
            current_desc = self._get_rank_description(int(current_rank))

            embed = discord.Embed(
                title=f"Has achieved Guardian Rank {current_rank}",
                description=f"**{current_rank_name}**\n{current_desc}",
                color=discord.Color.gold(),
            )
            if avatar_url:
                embed.set_author(name=display_name, icon_url=avatar_url)
            else:
                embed.set_author(name=display_name)

            icon_url = await self._get_rank_icon_url(int(current_rank))
            if icon_url:
                embed.set_thumbnail(url=icon_url)
            embed.set_footer(text="via Wheelie Bin")
            if renewed_rank is not None:
                renewed_rank_name = self._get_rank_name(int(renewed_rank))
                embed.add_field(
                    name="Season Start Rank",
                    value=f"{renewed_rank} - {renewed_rank_name}",
                    inline=True,
                )
            if lifetime_rank is not None:
                lifetime_rank_name = self._get_rank_name(int(lifetime_rank))
                embed.add_field(
                    name="Lifetime Highest",
                    value=f"{lifetime_rank} - {lifetime_rank_name}",
                    inline=True,
                )

            await interaction.followup.send(embed=embed)

        @self.bot.tree.command(
            name="set_guardian_rank_channel",
            description="Set the channel for Guardian Rank up announcements.",
        )
        @discord.app_commands.checks.has_permissions(administrator=True)
        @discord.app_commands.describe(
            channel="Optional channel mention, ID, or name. Leave empty to use this channel"
        )
        async def set_guardian_rank_channel(
            interaction: discord.Interaction,
            channel: str | None = None,
        ):
            if interaction.guild is None:
                await interaction.response.send_message(
                    "This command must be used in a server.",
                    ephemeral=True,
                )
                return

            target_channel = None
            channel_input = (channel or "").strip()
            if not channel_input:
                target_channel = interaction.channel
            else:
                if channel_input.casefold() in {"unset", "clear", "none", "off", "disable"}:
                    guild_config = self.get_guild_config(interaction.guild_id)
                    guild_config.pop("guardian_rank_channel_id", None)
                    guild_config.pop("guardian_rank_last_seen", None)
                    self.rules_config[str(interaction.guild_id)] = guild_config
                    self.save_rules_config(self.rules_config)
                    await interaction.response.send_message(
                        "Guardian Rank announcements have been disabled for this server.",
                        ephemeral=True,
                    )
                    return

                if channel_input.startswith("<#") and channel_input.endswith(">"):
                    channel_input = channel_input[2:-1]

                if channel_input.isdigit():
                    target_channel = interaction.guild.get_channel(int(channel_input))
                else:
                    lookup_name = channel_input.casefold().lstrip("#")
                    for candidate in interaction.guild.text_channels:
                        if candidate.name.casefold() == lookup_name:
                            target_channel = candidate
                            break

            if target_channel is None or getattr(target_channel, "guild", None) is None:
                await interaction.response.send_message(
                    "I couldn't find that channel. Use a channel mention, numeric ID, exact channel name, or leave it blank.",
                    ephemeral=True,
                )
                return

            guild_config = self.get_guild_config(interaction.guild_id)
            guild_config["guardian_rank_channel_id"] = str(target_channel.id)
            guild_config.pop("guardian_rank_last_seen", None)
            self.rules_config[str(interaction.guild_id)] = guild_config
            self.save_rules_config(self.rules_config)
            await interaction.response.send_message(
                f"Guardian Rank announcements channel set to {target_channel.mention}.",
                ephemeral=True,
            )

        @self.bot.tree.command(
            name="clear_guardian_rank_channel",
            description="Disable Guardian Rank announcements for this server.",
        )
        @discord.app_commands.checks.has_permissions(administrator=True)
        async def clear_guardian_rank_channel(interaction: discord.Interaction):
            if interaction.guild is None:
                await interaction.response.send_message(
                    "This command must be used in a server.",
                    ephemeral=True,
                )
                return

            guild_config = self.get_guild_config(interaction.guild_id)
            guild_config.pop("guardian_rank_channel_id", None)
            guild_config.pop("guardian_rank_last_seen", None)
            self.rules_config[str(interaction.guild_id)] = guild_config
            self.save_rules_config(self.rules_config)
            await interaction.response.send_message(
                "Guardian Rank announcements have been disabled for this server.",
                ephemeral=True,
            )

        @self.bot.tree.command(
            name="unset_guardian_rank_channel",
            description="Disable Guardian Rank announcements for this server.",
        )
        @discord.app_commands.checks.has_permissions(administrator=True)
        async def unset_guardian_rank_channel(interaction: discord.Interaction):
            if interaction.guild is None:
                await interaction.response.send_message(
                    "This command must be used in a server.",
                    ephemeral=True,
                )
                return

            guild_config = self.get_guild_config(interaction.guild_id)
            guild_config.pop("guardian_rank_channel_id", None)
            guild_config.pop("guardian_rank_last_seen", None)
            self.rules_config[str(interaction.guild_id)] = guild_config
            self.save_rules_config(self.rules_config)
            await interaction.response.send_message(
                "Guardian Rank announcements have been disabled for this server.",
                ephemeral=True,
            )

        @self.bot.tree.command(
            name="guardian_rank_watch_add",
            description="Watch a Discord member's Destiny 2 Guardian rank in this server.",
        )
        @discord.app_commands.checks.has_permissions(administrator=True)
        @discord.app_commands.describe(bungie_name="Bungie name, e.g. WH33LS#8650")
        async def guardian_rank_watch_add(interaction: discord.Interaction, bungie_name: str):
            if interaction.guild is None:
                await interaction.response.send_message(
                    "This command must be used in a server.",
                    ephemeral=True,
                )
                return

            await interaction.response.defer(ephemeral=True)
            player = await self.resolve_player(bungie_name)
            if not player:
                await interaction.followup.send(
                    f"Could not resolve Destiny player **{bungie_name}**.",
                    ephemeral=True,
                )
                return

            guild_config = self.get_guild_config(interaction.guild_id)
            watch_list = guild_config.get("guardian_rank_watch", [])
            membership_key = f"{player['membership_type']}:{player['membership_id']}"
            if any(
                str(entry.get("membership_type")) == str(player["membership_type"]) and
                str(entry.get("membership_id")) == str(player["membership_id"])
                for entry in watch_list
                if isinstance(entry, dict)
            ):
                await interaction.followup.send(
                    f"{player['global_name']} is already on the watch list.",
                    ephemeral=True,
                )
                return

            watch_list.append({
                "global_name": player["global_name"],
                "membership_id": str(player["membership_id"]),
                "membership_type": int(player["membership_type"]),
                "avatar_url": player.get("avatar_url"),
            })
            guild_config["guardian_rank_watch"] = watch_list
            self.rules_config[str(interaction.guild_id)] = guild_config
            self.save_rules_config(self.rules_config)
            await interaction.followup.send(
                f"Added {player['global_name']} to the Guardian Rank watch list.",
                ephemeral=True,
            )

        @self.bot.tree.command(
            name="guardian_rank_watch_remove",
            description="Remove a player from this server's Guardian Rank watch list.",
        )
        @discord.app_commands.checks.has_permissions(administrator=True)
        @discord.app_commands.describe(bungie_name="Bungie name, e.g. WH33LS#8650")
        async def guardian_rank_watch_remove(interaction: discord.Interaction, bungie_name: str):
            if interaction.guild is None:
                await interaction.response.send_message(
                    "This command must be used in a server.",
                    ephemeral=True,
                )
                return

            await interaction.response.defer(ephemeral=True)
            player = await self.resolve_player(bungie_name)
            if not player:
                await interaction.followup.send(
                    f"Could not resolve Destiny player **{bungie_name}**.",
                    ephemeral=True,
                )
                return

            guild_config = self.get_guild_config(interaction.guild_id)
            watch_list = guild_config.get("guardian_rank_watch", [])
            remaining = [
                entry
                for entry in watch_list
                if not (
                    isinstance(entry, dict)
                    and str(entry.get("membership_type")) == str(player["membership_type"])
                    and str(entry.get("membership_id")) == str(player["membership_id"])
                )
            ]
            if len(remaining) == len(watch_list):
                await interaction.followup.send(
                    f"{player['global_name']} is not on the Guardian Rank watch list.",
                    ephemeral=True,
                )
                return

            guild_config["guardian_rank_watch"] = remaining
            last_seen = guild_config.get("guardian_rank_last_seen", {})
            removed_key = f"{player['membership_type']}:{player['membership_id']}"
            if removed_key in last_seen:
                last_seen.pop(removed_key, None)
                guild_config["guardian_rank_last_seen"] = last_seen
            self.rules_config[str(interaction.guild_id)] = guild_config
            self.save_rules_config(self.rules_config)
            await interaction.followup.send(
                f"Removed {player['global_name']} from the Guardian Rank watch list.",
                ephemeral=True,
            )

        @self.bot.tree.command(
            name="guardian_rank_watch_list",
            description="List the players currently watched for Guardian Rank updates.",
        )
        @discord.app_commands.checks.has_permissions(administrator=True)
        async def guardian_rank_watch_list(interaction: discord.Interaction):
            if interaction.guild is None:
                await interaction.response.send_message(
                    "This command must be used in a server.",
                    ephemeral=True,
                )
                return

            guild_config = self.get_guild_config(interaction.guild_id)
            watch_list = guild_config.get("guardian_rank_watch", [])
            if not watch_list:
                await interaction.response.send_message(
                    "No Guardian Rank watch entries are configured for this server.",
                    ephemeral=True,
                )
                return

            lines = []
            for entry in watch_list:
                if isinstance(entry, dict):
                    lines.append(
                        f"{entry.get('global_name', 'Unknown')} - {entry.get('membership_type')}:{entry.get('membership_id')}"
                    )
                else:
                    lines.append(str(entry))

            await interaction.response.send_message(
                "Guardian Rank watch list:\n" + "\n".join(lines),
                ephemeral=True,
            )

        @self.bot.tree.command(
            name="test_guardian_rank",
            description="Post a test Guardian Rank embed in this channel.",
        )
        @discord.app_commands.checks.has_permissions(administrator=True)
        @discord.app_commands.describe(rank="Rank number to preview, from 1 to 11")
        async def test_guardian_rank(interaction: discord.Interaction, rank: int = 11):
            if interaction.guild is None:
                await interaction.response.send_message(
                    "This command must be used in a server.",
                    ephemeral=True,
                )
                return

            if rank < 1 or rank > 11:
                await interaction.response.send_message(
                    "Rank must be between 1 and 11.",
                    ephemeral=True,
                )
                return

            display_name = interaction.user.display_name
            avatar_url = interaction.user.display_avatar.url if interaction.user.display_avatar else None
            icon_url = await self._get_rank_icon_url(rank)

            embed = discord.Embed(
                title=f"Has achieved Guardian Rank {rank}",
                description=f"**{self._get_rank_name(rank)}**\n{self._get_rank_description(rank)}",
                color=discord.Color.gold(),
            )
            if avatar_url:
                embed.set_author(name=display_name, icon_url=avatar_url)
            else:
                embed.set_author(name=display_name)
            if icon_url:
                embed.set_thumbnail(url=icon_url)
            embed.set_footer(text="via Wheelie Bin")

            await interaction.response.send_message(embed=embed)

        self._commands_registered = True
