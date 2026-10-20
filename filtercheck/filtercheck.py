"""Red-DiscordBot Cog for multi-provider web filter, DNS security, and ISP censorship checks."""

from __future__ import annotations

import logging
import discord
from discord import app_commands
from redbot.core import Config, commands
from redbot.core.bot import Red

from .checker import FilterCheckerEngine, sanitize_domain
from .views import (
    FilterCheckView,
    build_country_embed,
    build_dns_embed,
    build_overview_embed,
    build_school_embed,
)

log = logging.getLogger("red.sablinova.filtercheck")


class FilterCheck(commands.Cog):
    """Check domains against 20 school filters, 11 secure DNS resolvers, and national ISP blocks."""

    __version__ = "1.0.0"
    __author__ = "Sablinova"

    def __init__(self, bot: Red) -> None:
        self.bot = bot
        self.config = Config.get_conf(self, identifier=948271038174019283, force_registration=True)
        default_global = {
            "default_ephemeral": False,
        }
        self.config.register_global(**default_global)
        self.engine = FilterCheckerEngine()

    async def cog_load(self) -> None:
        try:
            self.bot.register_rpc_handler(self._rpc_sync_slash)
            self.bot.register_rpc_handler(self._rpc_check_domain)
        except Exception as exc:
            log.debug("Could not register filtercheck RPC handlers: %s", exc)

    async def cog_unload(self) -> None:
        try:
            self.bot.unregister_rpc_handler(self._rpc_sync_slash)
            self.bot.unregister_rpc_handler(self._rpc_check_domain)
        except Exception:
            pass
        await self.engine.close()

    async def _rpc_sync_slash(self, guild_id: int | None = None) -> dict:
        """RPC method to sync slash command tree to Discord."""
        try:
            await self.bot.enable_app_command("check", discord.AppCommandType.chat_input)
            await self.bot.tree.red_check_enabled()
        except Exception:
            pass
        guild = self.bot.get_guild(guild_id) if guild_id else None
        synced = await self.bot.tree.sync(guild=guild)
        return {
            "status": "success",
            "synced_count": len(synced),
            "command_names": [cmd.name for cmd in synced],
            "guild_id": guild_id,
        }

    async def _rpc_check_domain(self, domain: str) -> dict:
        """RPC method to run a check on a domain directly."""
        clean = sanitize_domain(domain)
        report = await self.engine.check_all(clean)
        return {
            "domain": report.domain,
            "elapsed_seconds": round(report.elapsed_seconds, 3),
            "school_summary": {
                "blocked": report.school_blocked_count,
                "allowed": report.school_allowed_count,
            },
            "dns_summary": {
                "blocked": report.dns_blocked_count,
                "allowed": report.dns_allowed_count,
            },
            "country_summary": {
                "blocked": report.country_blocked_count,
                "allowed": report.country_allowed_count,
            },
            "school_filters": [
                {
                    "provider": f.provider,
                    "category": f.category,
                    "blocked": f.blocked,
                    "detail": f.detail,
                }
                for f in report.school_filters
            ],
            "dns_resolvers": [
                {
                    "provider": d.provider,
                    "category": d.category,
                    "blocked": d.blocked,
                    "detail": d.detail,
                }
                for d in report.dns_resolvers
            ],
            "country_blocks": [
                {
                    "country_code": c.country_code,
                    "country_name": c.country_name,
                    "flag": c.flag,
                    "blocked": c.blocked,
                    "status_text": c.status_text,
                    "probe_source": c.probe_source,
                }
                for c in report.country_blocks
            ],
        }

    def format_help_for_context(self, ctx: commands.Context) -> str:
        pre_processed = super().format_help_for_context(ctx)
        return f"{pre_processed}\n\nCog Version: {self.__version__}\nAuthor: {self.__author__}"

    @app_commands.command(
        name="check",
        description="Check a domain against school filters, DNS resolvers, and ISP blocks",
        extras={"red_force_enable": True},
    )
    @app_commands.describe(
        domain="Domain or URL to check (e.g. pubslounge.xyz or google.com)",
        ephemeral="Set to True if you want the results visible only to you (default: False)",
    )
    async def slash_check(
        self,
        interaction: discord.Interaction,
        domain: str,
        ephemeral: bool = False,
    ) -> None:
        """Slash command to inspect any domain or URL."""
        clean = sanitize_domain(domain)
        if not clean or "." not in clean:
            await interaction.response.send_message(
                "❌ Please enter a valid domain name (e.g. `pubslounge.xyz` or `https://google.com`).",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=ephemeral)

        try:
            report = await self.engine.check_all(clean)
            embed = build_overview_embed(report)
            view = FilterCheckView(report=report, author_id=interaction.user.id, current_page="overview")
            await interaction.followup.send(embed=embed, view=view, ephemeral=ephemeral)
        except Exception as exc:
            log.exception("Error executing /check domain: %s", exc)
            await interaction.followup.send(
                f"❌ An error occurred while inspecting `{clean}`: {exc}",
                ephemeral=True,
            )

    @commands.command(name="check", aliases=["filtercheck", "webfilter", "wf"])
    async def prefix_check(self, ctx: commands.Context, domain: str) -> None:
        """Check a domain against 20 school filters, 11 DNS resolvers, and ISP blocks.

        Usage:
        `[p]check pubslounge.xyz`
        `[p]check https://discord.com`
        """
        clean = sanitize_domain(domain)
        if not clean or "." not in clean:
            await ctx.send("❌ Please enter a valid domain name (e.g. `pubslounge.xyz`).")
            return

        async with ctx.typing():
            try:
                report = await self.engine.check_all(clean)
                embed = build_overview_embed(report)
                view = FilterCheckView(report=report, author_id=ctx.author.id, current_page="overview")
                await ctx.send(embed=embed, view=view)
            except Exception as exc:
                log.exception("Error executing prefix check domain: %s", exc)
                await ctx.send(f"❌ An error occurred while inspecting `{clean}`: {exc}")


async def setup(bot: Red) -> None:
    cog = FilterCheck(bot)
    await bot.add_cog(cog)
