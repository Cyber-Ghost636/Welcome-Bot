import os
import datetime

import discord
import pytz
from discord import app_commands
from discord.ext import commands, tasks


class IndependentClock(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.allowed_guild_id = str(os.getenv("GOLDEN_OLDIES_GUILD_ID") or "1334068759656398899")

        # Fetch the channel ID configuration safely from .env
        env_channel_id = os.getenv("DISCORD_CLOCK_CHANNEL_ID")
        self.CHANNEL_ID = int(env_channel_id) if env_channel_id else None
        self.MESSAGE_ID = None

        self.live_clock.start()

    def cog_unload(self):
        self.live_clock.cancel()

    def update_env_file(self, new_channel_id):
        """Updates or appends the channel ID inside the .env file safely."""
        env_file = ".env"
        key = "DISCORD_CLOCK_CHANNEL_ID"
        lines = []
        updated = False

        if os.path.exists(env_file):
            with open(env_file, "r", encoding="utf-8") as f:
                lines = f.readlines()

        for i, line in enumerate(lines):
            if line.strip().startswith(key):
                lines[i] = f"{key}={new_channel_id}\n"
                updated = True
                break

        if not updated:
            lines.append(f"\n{key}={new_channel_id}\n")

        with open(env_file, "w", encoding="utf-8") as f:
            f.writelines(lines)

    @app_commands.command(name="setup_clock", description="Sets up the live timezone clock in the current channel.")
    @app_commands.checks.has_permissions(administrator=True)
    async def setup_clock(self, interaction: discord.Interaction):
        if str(interaction.guild_id or "") != self.allowed_guild_id:
            await interaction.response.send_message(
                "This command is only available in Golden Oldies.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)

        old_channel_id = self.CHANNEL_ID
        new_channel_id = interaction.channel_id

        # Clean up the old message if changing channels.
        if old_channel_id and self.MESSAGE_ID:
            try:
                old_channel = self.bot.get_channel(old_channel_id)
                if old_channel:
                    old_msg = await old_channel.fetch_message(self.MESSAGE_ID)
                    await old_msg.delete()
            except Exception:
                pass

        # Update memory state and the .env file.
        self.CHANNEL_ID = new_channel_id
        self.MESSAGE_ID = None
        self.update_env_file(new_channel_id)

        # Trigger a quick update cycle instantly.
        await self.live_clock()

        await interaction.followup.send(
            f"✅ Timezone clock successfully moved and bound to {interaction.channel.mention}!",
            ephemeral=True,
        )

    @tasks.loop(minutes=1)
    async def live_clock(self):
        if not self.CHANNEL_ID:
            return

        channel = self.bot.get_channel(self.CHANNEL_ID)
        if not channel:
            return
        if str(getattr(getattr(channel, "guild", None), "id", "")) != self.allowed_guild_id:
            return

        # Fetch accurate times across different global zones.
        utc_now = datetime.datetime.now(pytz.utc)
        uk_now = datetime.datetime.now(pytz.timezone("Europe/London"))
        eu_now = datetime.datetime.now(pytz.timezone("Europe/Paris"))
        ist_now = datetime.datetime.now(pytz.timezone("Asia/Kolkata"))
        jst_now = datetime.datetime.now(pytz.timezone("Asia/Tokyo"))
        sgt_now = datetime.datetime.now(pytz.timezone("Asia/Singapore"))
        aest_now = datetime.datetime.now(pytz.timezone("Australia/Sydney"))
        nzst_now = datetime.datetime.now(pytz.timezone("Pacific/Auckland"))
        gst_now = datetime.datetime.now(pytz.timezone("Asia/Dubai"))
        sast_now = datetime.datetime.now(pytz.timezone("Africa/Johannesburg"))
        brt_now = datetime.datetime.now(pytz.timezone("America/Sao_Paulo"))
        est_now = datetime.datetime.now(pytz.timezone("America/New_York"))
        cst_now = datetime.datetime.now(pytz.timezone("America/Chicago"))
        mst_now = datetime.datetime.now(pytz.timezone("America/Denver"))
        pst_now = datetime.datetime.now(pytz.timezone("America/Los_Angeles"))

        # Construct a clean, elongated vertical list.
        embed = discord.Embed(
            title="🌐 Global Live Time Zones",
            color=discord.Color.blue(),
            timestamp=utc_now,
        )

        embed.description = "\n".join(
            [
                f"🇬🇧 United Kingdom: `{uk_now.strftime('%H:%M')}`",
                f"🇪🇺 Central Europe: `{eu_now.strftime('%H:%M')}`",
                f"🇮🇳 India (IST): `{ist_now.strftime('%H:%M')}`",
                f"🇯🇵 Japan (JST): `{jst_now.strftime('%H:%M')}`",
                f"🇸🇬 Singapore (SGT): `{sgt_now.strftime('%H:%M')}`",
                f"🇦🇪 Dubai (GST): `{gst_now.strftime('%H:%M')}`",
                "",
                f"🇺🇸 US Eastern (EST): `{est_now.strftime('%H:%M')}`",
                f"🇺🇸 US Central (CST): `{cst_now.strftime('%H:%M')}`",
                f"🇺🇸 US Mountain (MST): `{mst_now.strftime('%H:%M')}`",
                f"🇺🇸 US Pacific (PST): `{pst_now.strftime('%H:%M')}`",
                f"🇨🇦 Canada Eastern: `{est_now.strftime('%H:%M')}`",
                f"🇨🇦 Canada Central: `{cst_now.strftime('%H:%M')}`",
                f"🇨🇦 Canada Mountain: `{mst_now.strftime('%H:%M')}`",
                f"🇨🇦 Canada Pacific: `{pst_now.strftime('%H:%M')}`",
                "",
                f"🇧🇷 Brazil (BRT): `{brt_now.strftime('%H:%M')}`",
                f"🇿🇦 South Africa (SAST): `{sast_now.strftime('%H:%M')}`",
                f"🇦🇺 Australia Sydney (AEST/AEDT): `{aest_now.strftime('%H:%M')}`",
                f"🇳🇿 New Zealand (NZST/NZDT): `{nzst_now.strftime('%H:%M')}`",
            ]
        )

        embed.set_footer(text="Updates every minute")

        try:
            # If we have a bound message, try to edit it first.
            if self.MESSAGE_ID is not None:
                try:
                    msg = await channel.fetch_message(int(self.MESSAGE_ID))
                    await msg.edit(embed=embed)
                    return
                except (discord.NotFound, discord.Forbidden):
                    self.MESSAGE_ID = None

            # Self-healing backup check: recover a matching clock message and
            # clean up duplicates so only one static clock message remains.
            if self.MESSAGE_ID is None:
                primary_message = None
                duplicate_messages = []
                async for message in channel.history(limit=25):
                    if message.author != self.bot.user or not message.embeds:
                        continue
                    first_embed = message.embeds[0]
                    if str(getattr(first_embed, "title", "") or "").strip() != "🌐 Global Live Time Zones":
                        continue
                    if primary_message is None:
                        primary_message = message
                    else:
                        duplicate_messages.append(message)

                if primary_message is not None:
                    self.MESSAGE_ID = primary_message.id
                    await primary_message.edit(embed=embed)
                    for duplicate in duplicate_messages:
                        try:
                            await duplicate.delete()
                        except Exception:
                            pass
                    return

            # No existing clock message was found; create one.
            msg = await channel.send(embed=embed)
            self.MESSAGE_ID = msg.id

        except Exception as e:
            print(f"Error handling clock loop: {e}")
            self.MESSAGE_ID = None

    @live_clock.before_loop
    async def before_live_clock(self):
        await self.bot.wait_until_ready()


async def setup(bot):
    await bot.add_cog(IndependentClock(bot))
