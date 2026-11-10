import json
import os
from pathlib import Path
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands, tasks

from utils import extract_unvaulting_percentage, generate_bar_text


BASE_DIR = Path(__file__).resolve().parent
CONFIG_FILE = BASE_DIR / "bungie_tracker_config.json"
SOURCE_URL = os.getenv("BUNGIE_PROGRESS_SOURCE_URL", "https://www.bungie.net/7/en/News")
TRACKER_MARKER = "Bungie Dev Unvaulting Tracker"
TRACKER_MARKERS = (TRACKER_MARKER, "DESTINY 2 · UNVAULTING")
DEFAULT_BANNER_URL = "https://i.postimg.cc/yNm98xv3/PRINCE.webp"
MAX_ARTICLES_TO_SCAN = 20


class _NewsArticleLinkParser(HTMLParser):
    def __init__(self, base_url: str):
        super().__init__()
        self.base_url = base_url
        self.article_urls = []

    def handle_starttag(self, tag, attrs):
        if tag != "a":
            return
        href = dict(attrs).get("href")
        if not href:
            return
        url = urljoin(self.base_url, href)
        if "/7/en/News/Article/" in url and url not in self.article_urls:
            self.article_urls.append(url)


class BungieTracker(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.config = self._load_config()
        self.update_loop.start()

    def cog_unload(self):
        self.update_loop.cancel()

    def _load_config(self) -> dict:
        if not CONFIG_FILE.exists():
            return {}
        try:
            payload = json.loads(CONFIG_FILE.read_text())
            if isinstance(payload, dict):
                return payload
        except (OSError, json.JSONDecodeError):
            pass
        return {}

    def _save_config(self) -> None:
        try:
            CONFIG_FILE.write_text(json.dumps(self.config, indent=2))
        except OSError as error:
            print(f"[BUNGIE_TRACKER] Failed to save config: {error}")

    def _get_guild_settings(self, guild_id: int) -> dict:
        settings = self.config.get(str(guild_id), {})
        return settings if isinstance(settings, dict) else {}

    async def _get_channel(self, guild: discord.Guild, channel_id: int):
        channel = self.bot.get_channel(channel_id)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(channel_id)
            except Exception as error:
                print(f"[BUNGIE_TRACKER] Could not fetch channel {channel_id}: {error}")
                return None

        if not isinstance(channel, discord.TextChannel) or channel.guild.id != guild.id:
            print(f"[BUNGIE_TRACKER] Channel {channel_id} is not a text channel in guild {guild.id}")
            return None
        return channel

    async def force_channel_update(self, guild: discord.Guild) -> bool:
        settings = self._get_guild_settings(guild.id)
        channel_id = settings.get("channel_id")
        if not channel_id:
            return False

        channel = await self._get_channel(guild, int(channel_id))
        if channel is None:
            return False

        percentage = max(0, min(100, int(settings.get("current_percent", 0))))
        new_content = generate_bar_text(percentage)
        embed = discord.Embed(
            description=new_content,
            color=discord.Color.from_rgb(89, 170, 224),
        )
        image_url = settings.get("banner_url") or DEFAULT_BANNER_URL
        if image_url:
            embed.set_image(url=image_url)
        message = None
        message_id = settings.get("message_id")

        if message_id:
            try:
                message = await channel.fetch_message(int(message_id))
                if message.author != self.bot.user:
                    message = None
            except Exception:
                message = None

        if message is None:
            async for candidate in channel.history(limit=20):
                content_matches = any(marker in candidate.content for marker in TRACKER_MARKERS)
                embed_matches = any(
                    any(marker in (part or "") for marker in TRACKER_MARKERS)
                    for existing_embed in candidate.embeds
                    for part in (existing_embed.title, existing_embed.description)
                )
                if candidate.author == self.bot.user and (content_matches or embed_matches):
                    message = candidate
                    break

        try:
            if message is not None:
                if message.content or not message.embeds or message.embeds[0].to_dict() != embed.to_dict():
                    await message.edit(content=None, embed=embed)
            else:
                message = await channel.send(embed=embed)
            settings["message_id"] = str(message.id)
            settings["current_percent"] = percentage
            self.config[str(guild.id)] = settings
            self._save_config()
            return True
        except Exception as error:
            print(f"[BUNGIE_TRACKER] Failed to update guild {guild.id}: {error}")
            return False

    async def _fetch_progress(self) -> int | None:
        try:
            timeout = aiohttp.ClientTimeout(total=20)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(SOURCE_URL) as response:
                    if response.status != 200:
                        print(f"[BUNGIE_TRACKER] News feed returned HTTP {response.status}")
                        return None
                    news_html = await response.text()

                parser = _NewsArticleLinkParser(SOURCE_URL)
                parser.feed(news_html)
                for article_url in parser.article_urls[:MAX_ARTICLES_TO_SCAN]:
                    async with session.get(article_url) as response:
                        if response.status != 200:
                            continue
                        percentage = extract_unvaulting_percentage(await response.text())
                    if percentage is not None:
                        return percentage

                print("[BUNGIE_TRACKER] No percentage found in recent News articles")
                return None
        except Exception as error:
            print(f"[BUNGIE_TRACKER] News fetch failed: {error}")
            return None

    @commands.command(name="setprogress")
    @commands.guild_only()
    @commands.has_permissions(administrator=True)
    async def set_progress(self, ctx: commands.Context, percentage: int):
        """Set the tracker percentage and update the configured channel message."""
        percentage = max(0, min(100, percentage))
        settings = self._get_guild_settings(ctx.guild.id)
        settings["current_percent"] = percentage
        self.config[str(ctx.guild.id)] = settings
        self._save_config()

        if not await self.force_channel_update(ctx.guild):
            await ctx.send("Set a progress channel first with `/setprogresschannel`.")
            return
        await ctx.send(f"Progress bar updated to **{percentage}%**.")

    @app_commands.command(name="setprogress", description="Set the Bungie progress bar percentage.")
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(administrator=True)
    async def set_progress_slash(self, interaction: discord.Interaction, percentage: int):
        percentage = max(0, min(100, percentage))
        settings = self._get_guild_settings(interaction.guild_id)
        settings["current_percent"] = percentage
        self.config[str(interaction.guild_id)] = settings
        self._save_config()

        if not await self.force_channel_update(interaction.guild):
            await interaction.response.send_message(
                "Set a progress channel first with `/setprogresschannel`.",
                ephemeral=True,
            )
            return
        await interaction.response.send_message(
            f"Progress bar updated to **{percentage}%**.",
            ephemeral=True,
        )

    @app_commands.command(name="setprogresschannel", description="Use this channel for the Bungie progress bar.")
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(administrator=True)
    async def set_progress_channel(self, interaction: discord.Interaction):
        guild = interaction.guild
        channel = interaction.channel
        if guild is None or not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message("Run this command in a server text channel.", ephemeral=True)
            return

        guild_key = str(guild.id)
        settings = self._get_guild_settings(guild.id)
        settings["channel_id"] = str(channel.id)
        settings.pop("message_id", None)
        self.config[guild_key] = settings
        self._save_config()

        updated = await self.force_channel_update(guild)
        response = f"Progress bar channel set to {channel.mention}."
        if not updated:
            response += " I couldn't post there; check my View Channel, Read Message History, and Send Messages permissions."
        await interaction.response.send_message(response, ephemeral=True)

    @app_commands.command(name="setprogressthumbnail", description="Set or reset the progress bar image.")
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(administrator=True)
    async def set_progress_thumbnail(self, interaction: discord.Interaction, image_url: str | None = None):
        if interaction.guild is None:
            await interaction.response.send_message("Use this command in a server.", ephemeral=True)
            return

        settings = self._get_guild_settings(interaction.guild.id)
        image_url = str(image_url or "").strip()
        if image_url:
            parsed_url = urlparse(image_url)
            if parsed_url.scheme != "https" or not parsed_url.netloc:
                await interaction.response.send_message(
                    "Provide a valid HTTPS image URL.",
                    ephemeral=True,
                )
                return
            settings["banner_url"] = image_url
            result_text = "Progress bar image updated."
        else:
            settings.pop("banner_url", None)
            result_text = "Progress bar image reset to the default."

        self.config[str(interaction.guild.id)] = settings
        self._save_config()
        updated = await self.force_channel_update(interaction.guild)
        if not updated:
            result_text += " Set the progress channel first with `/setprogresschannel`."
        await interaction.response.send_message(result_text, ephemeral=True)

    @tasks.loop(hours=6)
    async def update_loop(self):
        percentage = await self._fetch_progress()
        if percentage is None:
            return

        percentage = max(0, min(100, percentage))
        for guild in self.bot.guilds:
            settings = self._get_guild_settings(guild.id)
            if settings.get("channel_id"):
                settings["current_percent"] = percentage
                self.config[str(guild.id)] = settings
                await self.force_channel_update(guild)

    @update_loop.before_loop
    async def before_update_loop(self):
        await self.bot.wait_until_ready()


async def setup(bot):
    await bot.add_cog(BungieTracker(bot))