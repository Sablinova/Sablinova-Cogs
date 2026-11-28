import asyncio
import logging
from typing import Optional

import discord
from redbot.core import Config, commands
from redbot.core.bot import Red

log = logging.getLogger("red.sablinova.policy")

DEFAULT_POLICY_URL = "https://github.com/Sablinova/privacy-policy"


class Policy(commands.Cog):
    """View Project Sablinova's Privacy Policy and data usage summary."""

    def __init__(self, bot: Red) -> None:
        self.bot = bot
        self.config = Config.get_conf(self, identifier=7391042856, force_registration=True)
        self.config.register_global(policy_url=DEFAULT_POLICY_URL)
        self._sync_task: Optional[asyncio.Task] = None

    async def cog_load(self) -> None:
        """Ensure the /policy slash command is enabled and synced if missing."""
        self._sync_task = asyncio.create_task(self._ensure_slash_synced())

    async def cog_unload(self) -> None:
        """Cancel pending slash-sync task on unload."""
        if self._sync_task and not self._sync_task.done():
            self._sync_task.cancel()

    async def _ensure_slash_synced(self) -> None:
        """Enable and sync /policy onto the global command tree if not yet registered."""
        try:
            await self.bot.wait_until_ready()
            if self.policy.app_command is not None:
                self.policy.app_command.extras["red_force_enable"] = True
            await self.bot.tree.red_check_enabled()
            remote_commands = await self.bot.tree.fetch_commands()
            if "policy" not in {cmd.name for cmd in remote_commands}:
                await self.bot.tree.sync()
                log.info("Synced /policy slash command globally.")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("Could not verify/sync /policy slash command: %s", exc)

    def _build_policy_embed(self, policy_url: str) -> discord.Embed:
        """Build the summary embed returned by `-policy` and `/policy`."""
        embed = discord.Embed(
            title="Project Sablinova — Privacy Policy",
            description=(
                "Here is a quick overview of what data **Project Sablinova** processes "
                "and how you can opt out or request deletion."
            ),
            color=discord.Color.blurple(),
            url=policy_url,
        )
        embed.add_field(
            name="What Is Processed",
            value=(
                "- Discord IDs (users, servers, channels, roles) and join metadata for moderation & server features.\n"
                "- Transient message content for enabled server protection (spam/phishing/invite filtering), "
                "staff delete/edit logs, broken link fixing, sticky messages, and user-invoked commands."
            ),
            inline=False,
        )
        embed.add_field(
            name="Storage, AI & Third Parties",
            value=(
                "- Audit logs (joins/leaves, deleted/edited messages) stay inside private Discord staff channels.\n"
                "- User-invoked AI/lookup prompts are sent transiently to configured inference/lookup APIs solely to reply.\n"
                "- Data is **never sold** and messages are **never used to train AI/ML models**."
            ),
            inline=False,
        )
        embed.add_field(
            name="Opt-Out & Deletion",
            value=(
                "- Server admins can set ignored channels/roles or turn off logging modules anytime.\n"
                f"- Need data removed? Open a request via [GitHub Issues]({policy_url}/issues) or contact `Sablinova`."
            ),
            inline=False,
        )
        embed.set_footer(text=f"Full policy: {policy_url}")
        return embed

    @commands.hybrid_command(
        name="policy",
        aliases=["privacy", "privacypolicy"],
        description="View Project Sablinova's Privacy Policy and data usage summary.",
    )
    async def policy(self, ctx: commands.Context) -> None:
        """Show Project Sablinova's Privacy Policy link and data usage summary."""
        policy_url = await self.config.policy_url()
        embed = self._build_policy_embed(policy_url)
        view = discord.ui.View(timeout=None)
        view.add_item(
            discord.ui.Button(
                label="Read Full Privacy Policy",
                style=discord.ButtonStyle.link,
                url=policy_url,
            )
        )
        await ctx.send(embed=embed, view=view)

    if policy.app_command is not None:
        policy.app_command.extras["red_force_enable"] = True

    @commands.command(name="setpolicyurl", hidden=True)
    @commands.is_owner()
    async def set_policy_url(self, ctx: commands.Context, url: str) -> None:
        """Update the Privacy Policy URL displayed by `[p]policy` and `/policy`."""
        cleaned = url.strip()
        if not cleaned.startswith(("https://", "http://")):
            await ctx.send("Please provide a full `https://` or `http://` URL.")
            return
        await self.config.policy_url.set(cleaned)
        await ctx.send(f"Updated Privacy Policy URL to `{cleaned}`.")
