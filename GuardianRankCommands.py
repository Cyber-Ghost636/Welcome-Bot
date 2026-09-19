import os
import discord
from discord.ext import commands, tasks
from datetime import datetime
from bungie_api import resolve_player, get_recent_activities, get_pgcr, get_guardian_rank

GUARDIAN_RANK_INFO = {
    1: {"name": "New Light", "description": "New to the light — learning the ropes of the Last City."},
    2: {"name": "Explorer", "description": "Finding their footing — starting to explore Destiny's systems."},
    3: {"name": "Initiate", "description": "A steady Guardian — completing activities and earning experience."},
    4: {"name": "Scout", "description": "Skilled and dependable — often leading small fireteams."},
    5: {"name": "Adventurer", "description": "Seasoned adventurer — comfortable with a range of activities."},
    6: {"name": "Veteran", "description": "A respected Veteran — consistent contributor to clan goals."},
    7: {"name": "Elite", "description": "Elite Guardian — high skill and achievement in endgame content."},
    8: {"name": "Justiciar", "description": "Justiciar — a guardian of standards, often mentoring others."},
    9: {"name": "Vanquisher", "description": "Vanquisher — renowned for PvE or PvP prowess and accomplishments."},
    10: {"name": "Exemplar", "description": "Exemplar — an example to the Light; exceptional performance."},
    11: {"name": "Paragon", "description": "Paragon — among the highest echelon of Guardians, legendary status."},
}

GUARDIAN_RANK_SYMBOL_URL = "https://i.postimg.cc/L86yLFs7/CROWN.png"

ANNOUNCE_CHANNEL_ID = int(os.getenv("GUARDIAN_RANK_CHANNEL_ID", "1443142601988636852"))

class GuardianRankCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.pgcr_cache = {}       # membershipId → last activityId

        self.scan_discord_members.start()
        self.pgcr_watcher.start()

    # ---------------------------------------------------------
    # GOLD GUARDIAN RANK EMBED
    # ---------------------------------------------------------
    async def send_guardian_rank_embed(self, bungie_name: str, guardian_rank: int):
        rank_info = GUARDIAN_RANK_INFO.get(guardian_rank)
        if not rank_info:
            return

        rank_name = rank_info["name"]
        rank_description = rank_info["description"]

        channel = self.bot.get_channel(ANNOUNCE_CHANNEL_ID)
        if not channel:
            return

        embed = discord.Embed(
            title="Guardian Rank Achievement",
            colour=discord.Colour.gold()   # GOLD text for GOLDEN OLDIES
        )

        # GOLD TEXT: Guardian name
        embed.add_field(
            name=bungie_name,              # GOLD
            value=f"Has achieved Guardian Rank {guardian_rank}",
            inline=False
        )

        # GOLD TEXT: Rank name
        embed.add_field(
            name=rank_name,                # GOLD
            value=rank_description,
            inline=False
        )

        # Crown image
        embed.set_thumbnail(url=GUARDIAN_RANK_SYMBOL_URL)

        embed.set_footer(text="via Wheelie Bin")

        await channel.send(embed=embed)

    # ---------------------------------------------------------
    # AUTO-SCAN DISCORD MEMBERS (NO GUARDIAN RANK TRACKING)
    # ---------------------------------------------------------
    @tasks.loop(seconds=30)
    async def scan_discord_members(self):
        await self.bot.wait_until_ready()

        guild = self.bot.guilds[0]
        for member in guild.members:
            if member.bot:
                continue

            bungie_name = member.name

            try:
                profile = await resolve_player(bungie_name)
            except Exception:
                continue

            if not profile:
                continue

            membership_id = profile["membershipId"]

            # Initialize PGCR cache only
            recent = await get_recent_activities(membership_id)
            if recent:
                self.pgcr_cache[membership_id] = recent[0]["activityDetails"]["instanceId"]

    # ---------------------------------------------------------
    # PGCR WATCHER — CHECKS EVERY 45 SECONDS
    # ---------------------------------------------------------
    @tasks.loop(seconds=45)
    async def pgcr_watcher(self):
        await self.bot.wait_until_ready()

        for membership_id in list(self.pgcr_cache.keys()):
            try:
                recent = await get_recent_activities(membership_id)
            except Exception:
                continue

            if not recent:
                continue

            latest_instance = recent[0]["activityDetails"]["instanceId"]

            # Skip if already processed
            if self.pgcr_cache.get(membership_id) == latest_instance:
                continue

            # Update cache
            self.pgcr_cache[membership_id] = latest_instance

            # Fetch PGCR
            try:
                pgcr = await get_pgcr(latest_instance)
            except Exception:
                continue

            # ---------------------------------------------------------
            # CHECK GUARDIAN RANK + SEND GOLD EMBED
            # ---------------------------------------------------------
            try:
                guardian_rank = await get_guardian_rank(membership_id)
            except Exception:
                guardian_rank = None

            if guardian_rank:
                # Bungie name from PGCR
                bungie_name = pgcr["entries"][0]["player"]["destinyUserInfo"]["displayName"]
                await self.send_guardian_rank_embed(bungie_name, guardian_rank)

    # ---------------------------------------------------------
    # SETUP
    # ---------------------------------------------------------
    async def setup(bot):
        await bot.add_cog(GuardianRankCommands(bot))