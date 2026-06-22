"""
Guide - Slash command cog for Red-DiscordBot

Sends a video/guide link based on the ticket channel name.
Mirrors the /saveinst game matching system.
"""

import logging
from pathlib import Path

import discord
from discord import app_commands
from redbot.core import Config, commands
from redbot.core.bot import Red

log = logging.getLogger("red.sablinova.guide")


class Guide(commands.Cog):
    """Sends game-specific guide links based on ticket channel name."""

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(
            self, identifier=7743901265, force_registration=True
        )
        self.config.register_global(
            guides={},  # keyword -> {"name": str, "url": str}
        )

    # ── Admin prefix commands ────────────────────────────────────────────────

    @commands.group(name="guide")
    @commands.admin_or_permissions(manage_guild=True)
    async def guide_group(self, ctx: commands.Context) -> None:
        """Manage guide links for the /guide slash command."""
        pass

    @guide_group.command(name="add")
    async def guide_add(
        self, ctx: commands.Context, keyword: str, name: str, *, url: str
    ) -> None:
        """Add or update a guide link.

        **Usage:**
        `[p]guide add <keyword> <name> <url>`

        **Examples:**
        `[p]guide add re9 "Resident Evil 9" https://youtu.be/abc123`
        `[p]guide add "monster hunter wilds" "Monster Hunter Wilds" https://youtu.be/xyz`

        The keyword is matched against the ticket channel name.
        Wrap multi-word keywords or names in quotes.
        """
        keyword = keyword.lower().strip()
        async with self.config.guides() as guides:
            guides[keyword] = {"name": name.strip(), "url": url.strip()}

        await ctx.send(
            embed=discord.Embed(
                description=f"✅ Guide added for **{name}**\nKeyword: `{keyword}`\nURL: {url.strip()}",
                color=discord.Color.green(),
            )
        )

    @guide_group.command(name="edit")
    async def guide_edit(
        self, ctx: commands.Context, keyword: str, name: str = None, *, url: str = None
    ) -> None:
        """Edit an existing guide link's name or URL.

        Leave a parameter as `None` or skip it if you don't want to change it.

        **Usage:**
        `[p]guide edit <keyword> [new_name] [new_url]`

        **Examples:**
        `[p]guide edit re9 None https://youtu.be/new-link` (Updates only URL)
        `[p]guide edit re9 "Resident Evil IX" None` (Updates only Name)
        `[p]guide edit re9 "Resident Evil IX" https://youtu.be/new-link` (Updates both)
        """
        keyword = keyword.lower().strip()
        
        async with self.config.guides() as guides:
            if keyword not in guides:
                await ctx.send(f"❌ No guide found with the keyword `{keyword}`. Use `[p]guide add` instead.")
                return

            current_data = guides[keyword]
            old_name = current_data["name"]
            old_url = current_data["url"]

            # Parse and clean updates, fall back to old values if specified as 'None' or omitted
            new_name = name.strip() if (name and name.lower() != "none") else old_name
            new_url = url.strip() if (url and url.lower() != "none") else old_url

            # Update the configuration
            guides[keyword] = {"name": new_name, "url": new_url}

        # Build a clean changes summary description
        changes = []
        if new_name != old_name:
            changes.append(f"• **Name:** `{old_name}` ➔ **{new_name}**")
        if new_url != old_url:
            changes.append(f"• **URL:** {old_url} ➔ {new_url}")

        if not changes:
            await ctx.send("ℹ️ No changes were made.")
            return

        description = f"✅ Updated guide for keyword: `{keyword}`\n\n" + "\n".join(changes)
        
        await ctx.send(
            embed=discord.Embed(
                description=description,
                color=discord.Color.orange(),
            )
        )
        
    @guide_group.command(name="remove")
    async def guide_remove(self, ctx: commands.Context, *, keyword: str) -> None:
        """Remove a guide link by keyword or name.

        **Usage:**
        `[p]guide remove <keyword or name>`
        """
        keyword = keyword.lower().strip()
        async with self.config.guides() as guides:
            # Exact keyword match
            if keyword in guides:
                name = guides[keyword]["name"]
                del guides[keyword]
                await ctx.send(f"✅ Removed guide for **{name}** (`{keyword}`).")
                return

            # Match by name
            matched = None
            for k, data in guides.items():
                if keyword == data["name"].lower():
                    matched = k
                    break

            if matched:
                name = guides[matched]["name"]
                del guides[matched]
                await ctx.send(f"✅ Removed guide for **{name}** (`{matched}`).")
            else:
                await ctx.send(f"❌ No guide found matching `{keyword}`.")

    @guide_group.command(name="list")
    async def guide_list(self, ctx: commands.Context) -> None:
        """List all configured guide links."""
        guides = await self.config.guides()

        if not guides:
            await ctx.send("No guides configured. Use `[p]guide add` to add one.")
            return

        embed = discord.Embed(
            title="📖 Configured Guides",
            color=discord.Color.blue(),
        )

        lines = []
        for keyword, data in sorted(guides.items(), key=lambda x: x[1]["name"]):
            lines.append(f"**{data['name']}** — kw: `{keyword}`\n{data['url']}")

        # Discord embed field value limit is 1024 chars; chunk if needed
        chunk = []
        chunk_len = 0
        field_num = 1
        for line in lines:
            if chunk_len + len(line) + 1 > 1000:
                embed.add_field(
                    name=f"Games ({field_num})",
                    value="\n\n".join(chunk),
                    inline=False,
                )
                chunk = []
                chunk_len = 0
                field_num += 1
            chunk.append(line)
            chunk_len += len(line) + 1

        if chunk:
            embed.add_field(
                name=f"Games ({field_num})" if field_num > 1 else "Games",
                value="\n\n".join(chunk),
                inline=False,
            )

        embed.set_footer(text=f"{len(guides)} guide(s) total")
        await ctx.send(embed=embed)

    @guide_group.command(name="test")
    async def guide_test(self, ctx: commands.Context, *, keyword: str) -> None:
        """Test what the /guide command would send for a given keyword.

        **Usage:**
        `[p]guide test <keyword or game name>`
        """
        guides = await self.config.guides()
        match = self._find_guide(keyword.lower(), guides)

        if match:
            keyword_key, data = match
            await ctx.send(
                embed=discord.Embed(
                    description=(
                        f"**Match:** {data['name']} (`{keyword_key}`)\n"
                        f"**URL:** {data['url']}"
                    ),
                    color=discord.Color.green(),
                )
            )
        else:
            await ctx.send(f"❌ No guide found matching `{keyword}`.")

    # ── Matching logic ───────────────────────────────────────────────────────

    def _find_guide(
        self, game_name: str, guides: dict
    ) -> tuple[str, dict] | None:
        """Find the best matching guide for a game name string.

        Uses the same three-phase matching as /saveinst:
        Phase 1 — exact match on keyword or name
        Phase 2 — substring match (longest first)
        Phase 3 — word overlap scoring (min score 1)
        """
        if not guides:
            return None

        targets = [
            {
                "key": k,
                "name": data["name"].lower(),
                "data": data,
            }
            for k, data in guides.items()
        ]

        # Phase 1: Exact match
        for t in targets:
            if game_name == t["key"] or game_name == t["name"]:
                return t["key"], t["data"]

        # Phase 2: Substring match — longest key/name first to avoid
        # short entries stealing matches from longer ones
        targets_by_len = sorted(
            targets,
            key=lambda x: max(len(x["key"]), len(x["name"])),
            reverse=True,
        )
        for t in targets_by_len:
            if t["key"] in game_name or t["name"] in game_name:
                return t["key"], t["data"]
            if len(game_name) > 4 and (
                game_name in t["key"] or game_name in t["name"]
            ):
                return t["key"], t["data"]

        # Phase 3: Word overlap scoring — require at least 1 match
        game_words = {
            w
            for w in game_name.replace(":", " ").replace("-", " ").split()
            if len(w) >= 3
        }
        best_score = 0
        best_match = None
        for t in targets:
            t_words = {
                w
                for w in t["key"].replace(":", " ").replace("-", " ").split()
                if len(w) >= 3
            } | {
                w
                for w in t["name"].replace(":", " ").replace("-", " ").split()
                if len(w) >= 3
            }
            overlap = len(game_words & t_words)
            if overlap > best_score:
                best_score = overlap
                best_match = t

        if best_score >= 1 and best_match:
            return best_match["key"], best_match["data"]

        return None

    def _game_name_from_channel(self, channel_name: str) -> str:
        """Derive a game name string from a Discord channel name."""
        if "-" in channel_name:
            game_name = (
                channel_name.split("-", 1)[1].strip().lower().replace("-", " ")
            )
        elif "|" in channel_name:
            game_name = channel_name.split("|", 1)[1].strip().lower()
        else:
            game_name = channel_name.strip().lower()
        return game_name

    # ── Slash command autocomplete ───────────────────────────────────────────

    async def guide_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        """Autocomplete for the game parameter on /guide."""
        guides = await self.config.guides()
        current_lower = current.strip().lower()

        if current_lower:
            filtered = [
                (k, data["name"])
                for k, data in guides.items()
                if current_lower in k or current_lower in data["name"].lower()
            ]
        else:
            filtered = [(k, data["name"]) for k, data in guides.items()]

        filtered.sort(key=lambda x: x[1])
        return [
            app_commands.Choice(name=name, value=key)
            for key, name in filtered[:25]
        ]

    # ── Slash command ────────────────────────────────────────────────────────

    @app_commands.command(
        name="guide",
        description="Send a guide link for the game in this ticket",
    )
    @app_commands.describe(game="Optional: manually specify the game")
    @app_commands.autocomplete(game=guide_autocomplete)
    async def guide_slash(
        self, interaction: discord.Interaction, game: str = None
    ) -> None:
        """Send a guide link based on the ticket channel name."""
        guides = await self.config.guides()

        if not guides:
            await interaction.response.send_message(
                "❌ No guides have been configured yet. Ask an admin to add some with `[p]guide add`.",
                ephemeral=True,
            )
            return

        if game:
            game_name = game.strip().lower()
        else:
            game_name = self._game_name_from_channel(interaction.channel.name)

        match = self._find_guide(game_name, guides)

        if not match:
            await interaction.response.send_message(
                f"❌ No guide found matching: **{game_name}**.",
                ephemeral=True,
            )
            return

        matched_key, data = match
        await interaction.response.send_message(
            f"📖 **{data['name']} Guide**\n{data['url']}"
        )