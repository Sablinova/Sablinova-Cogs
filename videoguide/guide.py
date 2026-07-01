"""
Guide - Slash command cog for Red-DiscordBot

Sends a video/guide link based on the ticket channel name.
Mirrors the /saveinst game matching system.
"""

import logging
import asyncio
import json
import io

import discord
from discord import app_commands
from redbot.core import Config, commands
from redbot.core.bot import Red

log = logging.getLogger("red.sablinova.guide")


class GuideListView(discord.ui.View):
    def __init__(self, pages: list, make_embed, total_pages: int):
        super().__init__(timeout=60)
        self.pages = pages
        self.make_embed = make_embed
        self.total_pages = total_pages
        self.current_page = 0
        self.message = None
        self._update_buttons()

    def _update_buttons(self):
        self.prev_button.disabled = self.current_page == 0
        self.next_button.disabled = self.current_page == self.total_pages - 1

    @discord.ui.button(label="◀", style=discord.ButtonStyle.secondary)
    async def prev_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page -= 1
        self._update_buttons()
        await interaction.response.edit_message(embed=self.make_embed(self.current_page), view=self)

    @discord.ui.button(label="▶", style=discord.ButtonStyle.secondary)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page += 1
        self._update_buttons()
        await interaction.response.edit_message(embed=self.make_embed(self.current_page), view=self)

    async def on_timeout(self):
        if self.message:
            await self.message.edit(view=None)


class Guide(commands.Cog):
    """Sends game-specific guide links based on ticket channel name."""

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(
            self, identifier=7743901265, force_registration=True
        )
        self.config.register_global(
            guides={},  # keyword -> {"name": str, "url": str, "aliases": [str]}
        )
        self._guides_cache: dict | None = None

    # ── Cache helpers ────────────────────────────────────────────────────────

    async def _get_guides(self) -> dict:
        if self._guides_cache is None:
            self._guides_cache = await self.config.guides()
        return self._guides_cache

    async def _save_guides(self, guides: dict) -> None:
        self._guides_cache = guides
        await self.config.set("guides", guides)

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
        guides = await self._get_guides()
        guides[keyword] = {"name": name.strip(), "url": url.strip(), "aliases": []}
        await self._save_guides(guides)

        await ctx.send(
            embed=discord.Embed(
                description=f"✅ Guide added for **{name}**\nKeyword: `{keyword}`\nURL: {url.strip()}",
                color=discord.Color.green(),
            )
        )

    @guide_group.command(name="edit")
    async def guide_edit(self, ctx: commands.Context, *, keyword: str) -> None:
        """Interactive wizard to edit an existing guide link.

        **Usage:**
        `[p]guide edit <keyword or name>`
        """
        keyword = keyword.lower().strip()
        guides = await self._get_guides()

        # Match by keyword, alias, or name
        matched_key = self._resolve_key(keyword, guides)

        if not matched_key:
            await ctx.send(f"❌ No guide found matching `{keyword}`. Use `[p]guide add` instead.")
            return

        data = guides[matched_key]
        current_keyword = matched_key

        def check(m):
            return m.author == ctx.author and m.channel == ctx.channel

        while True:
            aliases = data.get("aliases", [])
            alias_display = ", ".join(f"`{a}`" for a in aliases) if aliases else "None"

            embed = discord.Embed(
                title=f"Editing Guide: {data['name']}",
                description="Type the **number** of the field you want to edit, or type `cancel` to exit and save.",
                color=discord.Color.blurple(),
            )
            embed.add_field(name="1. Keyword", value=f"`{current_keyword}`", inline=False)
            embed.add_field(name="2. Display Name", value=f"**{data['name']}**", inline=False)
            embed.add_field(name="3. URL", value=data["url"], inline=False)
            embed.add_field(name="4. Aliases", value=alias_display, inline=False)
            await ctx.send(embed=embed)

            try:
                msg = await ctx.bot.wait_for("message", check=check, timeout=120)
                choice = msg.content.strip().lower()

                if choice == "cancel":
                    await ctx.send(f"✅ Exited editor for **{data['name']}**.")
                    break

                elif choice == "1":
                    await ctx.send("Enter the new **Keyword**:")
                    kw_msg = await ctx.bot.wait_for("message", check=check, timeout=120)
                    if kw_msg.content.lower() == "cancel":
                        continue
                    new_kw = kw_msg.content.strip().lower()
                    if new_kw in guides and new_kw != current_keyword:
                        await ctx.send(f"❌ A guide with keyword `{new_kw}` already exists.")
                        continue
                    del guides[current_keyword]
                    guides[new_kw] = data
                    current_keyword = new_kw
                    await self._save_guides(guides)
                    await ctx.send(f"✅ Keyword updated to `{current_keyword}`.")

                elif choice == "2":
                    await ctx.send("Enter the new **Display Name**:")
                    name_msg = await ctx.bot.wait_for("message", check=check, timeout=120)
                    if name_msg.content.lower() == "cancel":
                        continue
                    data["name"] = name_msg.content.strip()
                    guides[current_keyword] = data
                    await self._save_guides(guides)
                    await ctx.send(f"✅ Display Name updated to **{data['name']}**.")

                elif choice == "3":
                    await ctx.send("Enter the new **URL**:")
                    url_msg = await ctx.bot.wait_for("message", check=check, timeout=120)
                    if url_msg.content.lower() == "cancel":
                        continue
                    data["url"] = url_msg.content.strip()
                    guides[current_keyword] = data
                    await self._save_guides(guides)
                    await ctx.send(f"✅ URL updated.")

                elif choice == "4":
                    current_aliases = data.get("aliases", [])
                    alias_display = ", ".join(f"`{a}`" for a in current_aliases) if current_aliases else "None"
                    await ctx.send(
                        f"Current aliases: {alias_display}\n\n"
                        "`add <alias>` — add an alias\n"
                        "`remove <alias>` — remove an alias\n"
                        "`cancel` — go back"
                    )
                    alias_msg = await ctx.bot.wait_for("message", check=check, timeout=120)
                    alias_input = alias_msg.content.strip().lower()

                    if alias_input == "cancel":
                        continue
                    elif alias_input.startswith("add "):
                        new_alias = alias_input[4:].strip()
                        # Check alias isn't already a keyword or alias elsewhere
                        conflict = self._resolve_key(new_alias, guides)
                        if conflict and conflict != current_keyword:
                            await ctx.send(f"❌ `{new_alias}` is already used by **{guides[conflict]['name']}**.")
                        elif new_alias in current_aliases:
                            await ctx.send(f"❌ `{new_alias}` is already an alias for this guide.")
                        else:
                            current_aliases.append(new_alias)
                            data["aliases"] = current_aliases
                            guides[current_keyword] = data
                            await self._save_guides(guides)
                            await ctx.send(f"✅ Alias `{new_alias}` added.")
                    elif alias_input.startswith("remove "):
                        rem_alias = alias_input[7:].strip()
                        if rem_alias not in current_aliases:
                            await ctx.send(f"❌ `{rem_alias}` is not an alias for this guide.")
                        else:
                            current_aliases.remove(rem_alias)
                            data["aliases"] = current_aliases
                            guides[current_keyword] = data
                            await self._save_guides(guides)
                            await ctx.send(f"✅ Alias `{rem_alias}` removed.")
                    else:
                        await ctx.send("Invalid input. Use `add <alias>`, `remove <alias>`, or `cancel`.")

                else:
                    await ctx.send("Invalid choice. Type 1, 2, 3, 4, or `cancel`.")

            except asyncio.TimeoutError:
                await ctx.send("Editor timed out. Any changes made before this were saved.")
                break

    @guide_group.command(name="remove")
    async def guide_remove(self, ctx: commands.Context, *, keyword: str) -> None:
        """Remove a guide link by keyword, alias, or name.

        **Usage:**
        `[p]guide remove <keyword, alias, or name>`
        """
        keyword = keyword.lower().strip()
        guides = await self._get_guides()

        matched_key = self._resolve_key(keyword, guides)

        if matched_key:
            name = guides[matched_key]["name"]
            del guides[matched_key]
            await self._save_guides(guides)
            await ctx.send(f"✅ Removed guide for **{name}** (`{matched_key}`).")
        else:
            await ctx.send(f"❌ No guide found matching `{keyword}`.")

    @guide_group.command(name="list")
    async def guide_list(self, ctx: commands.Context) -> None:
        """List all configured guide links."""
        guides = await self._get_guides()

        if not guides:
            await ctx.send("No guides configured. Use `[p]guide add` to add one.")
            return

        lines = []
        for keyword, data in sorted(guides.items(), key=lambda x: x[1]["name"]):
            aliases = data.get("aliases", [])
            alias_str = f" • aliases: {', '.join(f'`{a}`' for a in aliases)}" if aliases else ""
            lines.append(f"**{data['name']}** — kw: `{keyword}`{alias_str}\n{data['url']}")

        # Build pages
        pages = []
        chunk = []
        for line in lines:
            projected = "\n\n".join(chunk + [line])
            if chunk and len(projected) > 1024:
                pages.append("\n\n".join(chunk))
                chunk = []
            chunk.append(line)
        if chunk:
            pages.append("\n\n".join(chunk))

        total_pages = len(pages)

        def make_embed(page_index: int) -> discord.Embed:
            embed = discord.Embed(
                title="📖 Configured Guides",
                description=pages[page_index],
                color=discord.Color.blue(),
            )
            embed.set_footer(text=f"Page {page_index + 1}/{total_pages} • {len(guides)} guide(s) total")
            return embed

        if total_pages == 1:
            await ctx.send(embed=make_embed(0))
            return

        view = GuideListView(pages, make_embed, total_pages)
        view.message = await ctx.send(embed=make_embed(0), view=view)

    @guide_group.command(name="export")
    async def guide_export(self, ctx: commands.Context) -> None:
        """Export all guides to a JSON file.

        **Usage:**
        `[p]guide export`
        """
        guides = await self._get_guides()

        if not guides:
            await ctx.send("No guides configured to export.")
            return

        data = json.dumps(guides, indent=2, ensure_ascii=False)
        file = discord.File(
            fp=io.BytesIO(data.encode("utf-8")),
            filename="guides_export.json",
        )
        await ctx.send("📦 Here's your guides export:", file=file)

    @guide_group.command(name="import")
    async def guide_import(self, ctx: commands.Context, merge: bool = False) -> None:
        """Import guides from a JSON file attachment.

        Attach a JSON file exported via `[p]guide export`.
        By default this **replaces** all guides. Pass `merge` to merge instead.

        **Usage:**
        `[p]guide import` — replaces all existing guides
        `[p]guide import merge` — merges with existing guides (imported entries overwrite on conflict)
        """
        if not ctx.message.attachments:
            await ctx.send("❌ Please attach a JSON file to import.")
            return

        attachment = ctx.message.attachments[0]
        if not attachment.filename.endswith(".json"):
            await ctx.send("❌ Attachment must be a `.json` file.")
            return

        try:
            raw = await attachment.read()
            imported = json.loads(raw.decode("utf-8"))
        except Exception as e:
            await ctx.send(f"❌ Failed to parse JSON: {e}")
            return

        if not isinstance(imported, dict):
            await ctx.send("❌ Invalid format. Expected a JSON object.")
            return

        # Validate entries
        invalid = []
        for k, v in imported.items():
            if not isinstance(v, dict) or "name" not in v or "url" not in v:
                invalid.append(k)

        if invalid:
            await ctx.send(f"❌ Invalid entries found: {', '.join(f'`{k}`' for k in invalid)}\nAborted.")
            return

        # Ensure aliases key exists on all imported entries
        for k in imported:
            imported[k].setdefault("aliases", [])

        if merge:
            guides = await self._get_guides()
            guides.update(imported)
            await self._save_guides(guides)
            await ctx.send(f"✅ Merged **{len(imported)}** guide(s) into existing config.")
        else:
            await self._save_guides(imported)
            await ctx.send(f"✅ Imported **{len(imported)}** guide(s). All previous guides replaced.")

    # ── Matching logic ───────────────────────────────────────────────────────

    def _resolve_key(self, query: str, guides: dict) -> str | None:
        """Resolve a query (keyword, alias, or name) to a guide key."""
        # Exact keyword match
        if query in guides:
            return query
        # Alias match
        for k, data in guides.items():
            if query in data.get("aliases", []):
                return k
        # Name match
        for k, data in guides.items():
            if query == data["name"].lower():
                return k
        return None

    def _find_guide(
        self, game_name: str, guides: dict
    ) -> tuple[str, dict] | None:
        """Find the best matching guide for a game name string.

        Uses the same three-phase matching as /saveinst:
        Phase 1 — exact match on keyword, alias, or name
        Phase 2 — substring match (longest first)
        Phase 3 — word overlap scoring (min score 1)
        """
        if not guides:
            return None

        targets = []
        for k, data in guides.items():
            targets.append({
                "key": k,
                "name": data["name"].lower(),
                "aliases": [a.lower() for a in data.get("aliases", [])],
                "data": data,
            })

        # Phase 1: Exact match on keyword, alias, or name
        for t in targets:
            if game_name == t["key"] or game_name == t["name"] or game_name in t["aliases"]:
                return t["key"], t["data"]

        # Phase 2: Substring match — longest key/name/alias first
        targets_by_len = sorted(
            targets,
            key=lambda x: max(len(x["key"]), len(x["name"]), max((len(a) for a in x["aliases"]), default=0)),
            reverse=True,
        )
        for t in targets_by_len:
            candidates = [t["key"], t["name"]] + t["aliases"]
            for c in candidates:
                if c in game_name or (len(game_name) > 4 and game_name in c):
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
            all_text = f"{t['key']} {t['name']} {' '.join(t['aliases'])}"
            t_words = {
                w
                for w in all_text.replace(":", " ").replace("-", " ").split()
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
        guides = await self._get_guides()
        current_lower = current.strip().lower()

        if current_lower:
            filtered = [
                (k, data["name"])
                for k, data in guides.items()
                if current_lower in k
                or current_lower in data["name"].lower()
                or any(current_lower in a for a in data.get("aliases", []))
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
        guides = await self._get_guides()

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