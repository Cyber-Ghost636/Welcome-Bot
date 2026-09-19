import discord
from discord import app_commands


RAID_ROLE_DEFAULTS = [
    {"key": "last_wish", "emoji": "🐉", "raid_name": "Last Wish", "title_name": "Rivensbane"},
    {"key": "garden_of_salvation", "emoji": "🌿", "raid_name": "Garden of Salvation", "title_name": "Enlightened"},
    {"key": "deep_stone_crypt", "emoji": "❄️", "raid_name": "Deep Stone Crypt", "title_name": "Descendant"},
    {"key": "vault_of_glass", "emoji": "🏛️", "raid_name": "Vault of Glass", "title_name": "Fatebreaker"},
    {"key": "vow_of_the_disciple", "emoji": "🔺", "raid_name": "Vow of the Disciple", "title_name": "Disciple-Slayer"},
    {"key": "kings_fall", "emoji": "👑", "raid_name": "King's Fall", "title_name": "Kingslayer"},
    {"key": "root_of_nightmares", "emoji": "🌙", "raid_name": "Root of Nightmares", "title_name": "Dream Warrior"},
    {"key": "crotas_end", "emoji": "⚔️", "raid_name": "Crota's End", "title_name": "Swordbearer"},
    {"key": "pantheon", "emoji": "🏆", "raid_name": "Pantheon", "title_name": "Godsbane"},
    {"key": "the_desert_perpetual", "emoji": "🏜️", "raid_name": "The Desert Perpetual", "title_name": "The Desert Perpetual"},
    {"key": "salvations_edge", "emoji": "🧊", "raid_name": "Salvation's Edge", "title_name": "Iconoclast"},
]


RAID_APP_COMMAND_CHOICES = [
    app_commands.Choice(name=raid["raid_name"], value=raid["key"])
    for raid in RAID_ROLE_DEFAULTS
]


class RaidRoleManager:
    def __init__(self, bot, rules_config: dict, save_config_fn):
        self.bot = bot
        self.rules_config = rules_config
        self.save_config_fn = save_config_fn

    def build_default_raid_roles_map(self):
        data = {}
        for raid in RAID_ROLE_DEFAULTS:
            data[raid["key"]] = {
                "emoji": raid["emoji"],
                "raid_name": raid["raid_name"],
                "title_name": raid.get("title_name") or raid["raid_name"],
                "role_name": raid.get("title_name") or raid["raid_name"],
            }
        return data

    def get_raid_role_entries(self, guild_config):
        config_map = guild_config.get("raid_roles")
        if not isinstance(config_map, dict):
            config_map = {}

        entries = []
        for raid in RAID_ROLE_DEFAULTS:
            key = raid["key"]
            existing = config_map.get(key, {}) if isinstance(config_map.get(key, {}), dict) else {}
            stored_role_name = existing.get("role_name")
            default_title_name = raid.get("title_name") or raid["raid_name"]
            legacy_learn_name = f"Learn {raid['raid_name']}"
            if stored_role_name == legacy_learn_name:
                stored_role_name = default_title_name

            entry = {
                "key": key,
                "emoji": existing.get("emoji") or raid["emoji"],
                "raid_name": existing.get("raid_name") or raid["raid_name"],
                "title_name": existing.get("title_name") or default_title_name,
                "role_id": existing.get("role_id"),
                "role_name": stored_role_name or default_title_name,
            }
            entries.append(entry)
        return entries

    def find_raid_role(self, guild: discord.Guild, raid_entry):
        role_id = raid_entry.get("role_id")
        if role_id:
            try:
                role = guild.get_role(int(role_id))
                if role:
                    return role
            except (TypeError, ValueError):
                pass

        role_name = raid_entry.get("role_name")
        if role_name:
            role = discord.utils.get(guild.roles, name=role_name)
            if role:
                return role

            lowered = str(role_name).casefold()
            for candidate in guild.roles:
                if candidate.name.casefold() == lowered:
                    return candidate
        return None

    def build_raid_roles_text(self, guild: discord.Guild, guild_config):
        max_members = guild_config.get("raid_roles_max_members", 5)
        try:
            max_members = int(max_members)
        except (TypeError, ValueError):
            max_members = 5

        lines = [
            "React to the raid title you'd like to try for.",
            "",
        ]

        for entry in self.get_raid_role_entries(guild_config):
            role = self.find_raid_role(guild, entry)
            title_text = entry.get("title_name") or entry["role_name"]
            raid_name = entry["raid_name"]
            if str(title_text).casefold() == str(raid_name).casefold():
                lines.append(f"{entry['emoji']} {raid_name}")
            else:
                lines.append(f"{entry['emoji']} {raid_name} - {title_text}")

        return "\n".join(lines)

    async def ensure_raid_roles_message(self, guild_id: int, channel_id: int):
        guild_key = str(guild_id)
        guild_config = self.rules_config.get(guild_key, {})
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            return

        try:
            channel = await self.bot.fetch_channel(channel_id)
        except Exception as e:
            print(f"Failed to fetch raid roles channel for guild {guild_id}: {e}")
            return

        if getattr(channel, "guild", None) is None or channel.guild.id != guild_id:
            print(
                f"Skipping raid role panel for guild {guild_id}: "
                f"channel {channel_id} belongs to a different guild."
            )
            return

        panel_text = self.build_raid_roles_text(guild, guild_config)
        raid_entries = self.get_raid_role_entries(guild_config)

        message_id = guild_config.get("raid_roles_message_id")
        message = None
        if message_id:
            try:
                message = await channel.fetch_message(int(message_id))
            except Exception:
                message = None

        if message is None:
            try:
                message = await channel.send(panel_text)
                guild_config["raid_roles_message_id"] = str(message.id)
                self.rules_config[guild_key] = guild_config
                self.save_config_fn(self.rules_config)
            except Exception as e:
                print(f"Failed to create raid roles panel in guild {guild_id}: {e}")
                return
        else:
            try:
                await message.edit(content=panel_text)
            except Exception as e:
                print(f"Failed to update raid roles panel in guild {guild_id}: {e}")

        for entry in raid_entries:
            try:
                await message.add_reaction(entry["emoji"])
            except Exception as e:
                print(
                    f"Failed to add raid reaction {entry['emoji']} "
                    f"for guild {guild_id}: {e}"
                )

    async def handle_raid_reaction_add(self, payload, guild_config):
        panel_channel_id = guild_config.get("raid_roles_channel_id")
        panel_message_id = guild_config.get("raid_roles_message_id")
        if not panel_channel_id or not panel_message_id:
            return False

        if str(payload.channel_id) != str(panel_channel_id) or str(payload.message_id) != str(panel_message_id):
            return False

        guild = self.bot.get_guild(payload.guild_id)
        if guild is None:
            return True

        member = guild.get_member(payload.user_id)
        if member is None:
            try:
                member = await guild.fetch_member(payload.user_id)
            except Exception:
                return True

        if member.bot:
            return True

        emoji = str(payload.emoji)
        entry = next((item for item in self.get_raid_role_entries(guild_config) if str(item.get("emoji")) == emoji), None)
        if entry is None:
            return True

        role = self.find_raid_role(guild, entry)
        if role is None:
            print(f"Raid role not found for {entry['raid_name']} in guild {guild.id}")
            return True

        if role in member.roles:
            return True

        max_members = guild_config.get("raid_roles_max_members", 5)
        try:
            max_members = int(max_members)
        except (TypeError, ValueError):
            max_members = 5

        current_members = sum(1 for m in role.members if not m.bot)
        if current_members >= max_members:
            try:
                channel = self.bot.get_channel(payload.channel_id) or await self.bot.fetch_channel(payload.channel_id)
                message = await channel.fetch_message(payload.message_id)
                await message.remove_reaction(payload.emoji, member)
            except Exception as e:
                print(f"Failed to remove full raid reaction in guild {guild.id}: {e}")

            try:
                await member.send(
                    f"The role for {entry['raid_name']} is currently full "
                    f"({max_members} members)."
                )
            except Exception:
                pass
            return True

        try:
            await member.add_roles(role, reason=f"Selected raid title role for {entry['raid_name']}")
        except Exception as e:
            print(f"Failed to add raid role in guild {guild.id}: {e}")
        return True

    async def handle_raid_reaction_remove(self, payload, guild_config):
        panel_channel_id = guild_config.get("raid_roles_channel_id")
        panel_message_id = guild_config.get("raid_roles_message_id")
        if not panel_channel_id or not panel_message_id:
            return False

        if str(payload.channel_id) != str(panel_channel_id) or str(payload.message_id) != str(panel_message_id):
            return False

        guild = self.bot.get_guild(payload.guild_id)
        if guild is None:
            return True

        member = guild.get_member(payload.user_id)
        if member is None:
            try:
                member = await guild.fetch_member(payload.user_id)
            except Exception:
                return True

        if member.bot:
            return True

        emoji = str(payload.emoji)
        entry = next((item for item in self.get_raid_role_entries(guild_config) if str(item.get("emoji")) == emoji), None)
        if entry is None:
            return True

        role = self.find_raid_role(guild, entry)
        if role is None:
            return True

        if role not in member.roles:
            return True

        try:
            await member.remove_roles(role, reason=f"Removed raid title role for {entry['raid_name']}")
        except Exception as e:
            print(f"Failed to remove raid role in guild {guild.id}: {e}")
        return True

    async def set_raid_roles_channel(self, guild_id: int, channel_id: int):
        guild_key = str(guild_id)
        guild_config = self.rules_config.get(guild_key, {})
        guild_config["raid_roles_channel_id"] = str(channel_id)
        guild_config["raid_roles"] = guild_config.get("raid_roles") or self.build_default_raid_roles_map()
        guild_config["raid_roles_max_members"] = int(guild_config.get("raid_roles_max_members", 5))
        guild_config.pop("raid_roles_message_id", None)
        self.rules_config[guild_key] = guild_config
        self.save_config_fn(self.rules_config)
        await self.ensure_raid_roles_message(guild_id, channel_id)

    async def set_raid_learn_role(self, guild_id: int, raid_key: str, role: discord.Role):
        guild_key = str(guild_id)
        guild_config = self.rules_config.get(guild_key, {})
        raid_roles = guild_config.get("raid_roles") or self.build_default_raid_roles_map()
        raid_entry = raid_roles.get(raid_key) or {}

        default_data = next((item for item in RAID_ROLE_DEFAULTS if item["key"] == raid_key), None)
        if default_data is None:
            return None

        raid_entry["emoji"] = raid_entry.get("emoji") or default_data["emoji"]
        raid_entry["raid_name"] = raid_entry.get("raid_name") or default_data["raid_name"]
        raid_entry["title_name"] = raid_entry.get("title_name") or default_data.get("title_name") or default_data["raid_name"]
        raid_entry["role_id"] = str(role.id)
        raid_entry["role_name"] = role.name
        raid_roles[raid_key] = raid_entry

        guild_config["raid_roles"] = raid_roles
        self.rules_config[guild_key] = guild_config
        self.save_config_fn(self.rules_config)

        raid_channel_id = guild_config.get("raid_roles_channel_id")
        if raid_channel_id:
            await self.ensure_raid_roles_message(guild_id, int(raid_channel_id))

        return raid_entry

    async def set_raid_learn_cap(self, guild_id: int, limit: int):
        guild_key = str(guild_id)
        guild_config = self.rules_config.get(guild_key, {})
        guild_config["raid_roles_max_members"] = int(limit)
        guild_config["raid_roles"] = guild_config.get("raid_roles") or self.build_default_raid_roles_map()
        self.rules_config[guild_key] = guild_config
        self.save_config_fn(self.rules_config)

        raid_channel_id = guild_config.get("raid_roles_channel_id")
        if raid_channel_id:
            await self.ensure_raid_roles_message(guild_id, int(raid_channel_id))
