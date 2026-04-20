import json
import logging
from pathlib import Path
from typing import Optional

import discord
from discord import app_commands
from redbot.core import commands
from redbot.core.data_manager import cog_data_path

import TagScriptEngine as tse

log = logging.getLogger("red.sablinova.sabbyslashtags")


class SabbySlashTags(commands.Cog):
    """Global tag system with TagScript support via /c slash command."""

    def __init__(self, bot):
        self.bot = bot
        self.data_path = cog_data_path(self) / "tags.json"
        self.data = {"tags": {}, "whitelist": {"users": [], "roles": []}}
        self.engine = None

    async def cog_load(self):
        self._load_data()
        blocks = [
            tse.MathBlock(),
            tse.RandomBlock(),
            tse.RangeBlock(),
            tse.AnyBlock(),
            tse.IfBlock(),
            tse.AllBlock(),
            tse.BreakBlock(),
            tse.StrfBlock(),
            tse.StopBlock(),
            tse.AssignmentBlock(),
            tse.FiftyFiftyBlock(),
            tse.LooseVariableGetterBlock(),
            tse.SubstringBlock(),
            tse.EmbedBlock(),
            tse.ReplaceBlock(),
            tse.URLEncodeBlock(),
        ]
        self.engine = tse.Interpreter(blocks)

    def _load_data(self):
        if self.data_path.exists():
            with open(self.data_path, "r") as f:
                self.data = json.load(f)
        else:
            self._save_data()

    def _save_data(self):
        self.data_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.data_path, "w") as f:
            json.dump(self.data, f, indent=2)

    async def _can_manage(self, ctx) -> bool:
        """Bot owner OR whitelisted user OR has a whitelisted role."""
        if await self.bot.is_owner(ctx.author):
            return True
        if ctx.author.id in self.data["whitelist"]["users"]:
            return True
        if ctx.guild:
            author_role_ids = {r.id for r in ctx.author.roles}
            if author_role_ids & set(self.data["whitelist"]["roles"]):
                return True
        return False

    def _process_tag(self, ctx, content: str, args: str) -> str:
        seed = {
            "user": tse.MemberAdapter(ctx.author),
            "channel": tse.ChannelAdapter(ctx.channel),
            "args": tse.StringAdapter(args or ""),
        }
        if ctx.guild:
            seed["server"] = tse.GuildAdapter(ctx.guild)
        output = self.engine.process(content, seed)
        return output.body or ""

    @app_commands.command(name="c", description="Invoke a tag")
    @app_commands.describe(
        tagname="The tag to invoke", args="Arguments to pass to the tag"
    )
    async def slash_c(
        self, interaction: discord.Interaction, tagname: str, args: Optional[str] = None
    ):
        """Invoke a tag via slash command."""
        tagname = tagname.lower()
        if tagname not in self.data["tags"]:
            await interaction.response.send_message(
                f"Tag `{tagname}` not found.", ephemeral=True
            )
            return
        seed = {
            "user": tse.MemberAdapter(interaction.user),
            "channel": tse.ChannelAdapter(interaction.channel),
            "args": tse.StringAdapter(args or ""),
        }
        if interaction.guild:
            seed["server"] = tse.GuildAdapter(interaction.guild)
        tag = self.data["tags"][tagname]
        output = self.engine.process(tag["content"], seed)
        result = output.body or ""
        if not result:
            await interaction.response.send_message(
                "Tag produced no output.", ephemeral=True
            )
            return
        await interaction.response.send_message(
            result, allowed_mentions=discord.AllowedMentions.none()
        )

    @slash_c.autocomplete("tagname")
    async def tagname_autocomplete(
        self, interaction: discord.Interaction, current: str
    ):
        tags = list(self.data["tags"].keys())
        return [
            app_commands.Choice(name=t, value=t)
            for t in tags
            if current.lower() in t.lower()
        ][:25]

    @commands.group(name="sabbytags", aliases=["c"])
    async def sabbytags(self, ctx):
        """Manage SabbySlashTags."""
        pass

    @sabbytags.command(name="add")
    async def sabbytags_add(self, ctx, name: str, *, content: str):
        """Add a new tag."""
        if not await self._can_manage(ctx):
            await ctx.send("You do not have permission to manage tags.")
            return
        name = name.lower()
        if name in self.data["tags"]:
            await ctx.send(f"Tag `{name}` already exists. Use `edit` to modify it.")
            return
        self.data["tags"][name] = {"content": content, "author": ctx.author.id}
        self._save_data()
        await ctx.send(f"Tag `{name}` created.")

    @sabbytags.command(name="edit")
    async def sabbytags_edit(self, ctx, name: str, *, content: str):
        """Edit an existing tag."""
        if not await self._can_manage(ctx):
            await ctx.send("You do not have permission to manage tags.")
            return
        name = name.lower()
        if name not in self.data["tags"]:
            await ctx.send(f"Tag `{name}` does not exist.")
            return
        self.data["tags"][name]["content"] = content
        self._save_data()
        await ctx.send(f"Tag `{name}` updated.")

    @sabbytags.command(name="delete")
    async def sabbytags_delete(self, ctx, name: str):
        """Delete a tag."""
        if not await self._can_manage(ctx):
            await ctx.send("You do not have permission to manage tags.")
            return
        name = name.lower()
        if name not in self.data["tags"]:
            await ctx.send(f"Tag `{name}` does not exist.")
            return
        del self.data["tags"][name]
        self._save_data()
        await ctx.send(f"Tag `{name}` deleted.")

    @sabbytags.command(name="list")
    async def sabbytags_list(self, ctx):
        """List all tags (paginated)."""
        tags = sorted(self.data["tags"].keys())
        if not tags:
            await ctx.send("No tags exist.")
            return
        pages = []
        per_page = 15
        for i in range(0, len(tags), per_page):
            chunk = tags[i : i + per_page]
            page_text = "\n".join(f"`{t}`" for t in chunk)
            pages.append(
                discord.Embed(
                    title="Tags",
                    description=page_text,
                    color=discord.Color.blurple(),
                ).set_footer(
                    text=f"Page {i // per_page + 1}/{(len(tags) - 1) // per_page + 1} | {len(tags)} tags total"
                )
            )
        if len(pages) == 1:
            await ctx.send(embed=pages[0])
        else:
            from redbot.core.utils.menus import SimpleMenu

            await SimpleMenu(pages).start(ctx)

    @sabbytags.command(name="info")
    async def sabbytags_info(self, ctx, name: str):
        """Show raw content of a tag."""
        name = name.lower()
        if name not in self.data["tags"]:
            await ctx.send(f"Tag `{name}` does not exist.")
            return
        tag = self.data["tags"][name]
        author = self.bot.get_user(tag["author"])
        author_str = str(author) if author else str(tag["author"])
        await ctx.send(
            f"**Tag:** `{name}`\n**Author:** {author_str}\n```\n{tag['content']}\n```"
        )

    @sabbytags.group(name="whitelist")
    @commands.is_owner()
    async def sabbytags_whitelist(self, ctx):
        """Manage the tag management whitelist (bot owner only)."""
        if ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)

    @sabbytags_whitelist.command(name="add")
    async def whitelist_add(self, ctx, target: discord.Member | discord.Role):
        """Add a user or role to the whitelist."""
        if isinstance(target, discord.Role):
            if target.id not in self.data["whitelist"]["roles"]:
                self.data["whitelist"]["roles"].append(target.id)
                self._save_data()
            await ctx.send(f"Role {target.name} added to whitelist.")
        else:
            if target.id not in self.data["whitelist"]["users"]:
                self.data["whitelist"]["users"].append(target.id)
                self._save_data()
            await ctx.send(f"User {target.display_name} added to whitelist.")

    @sabbytags_whitelist.command(name="remove")
    async def whitelist_remove(self, ctx, target: discord.Member | discord.Role):
        """Remove a user or role from the whitelist."""
        if isinstance(target, discord.Role):
            if target.id in self.data["whitelist"]["roles"]:
                self.data["whitelist"]["roles"].remove(target.id)
                self._save_data()
            await ctx.send(f"Role {target.name} removed from whitelist.")
        else:
            if target.id in self.data["whitelist"]["users"]:
                self.data["whitelist"]["users"].remove(target.id)
                self._save_data()
            await ctx.send(f"User {target.display_name} removed from whitelist.")

    @sabbytags_whitelist.command(name="list")
    async def whitelist_list(self, ctx):
        """Show current whitelist."""
        users = self.data["whitelist"]["users"]
        roles = self.data["whitelist"]["roles"]
        lines = []
        if users:
            user_strs = [str(self.bot.get_user(uid) or uid) for uid in users]
            lines.append(f"**Users:** {', '.join(user_strs)}")
        if roles:
            if ctx.guild:
                role_strs = [str(ctx.guild.get_role(rid) or rid) for rid in roles]
            else:
                role_strs = [str(rid) for rid in roles]
            lines.append(f"**Roles:** {', '.join(role_strs)}")
        if not lines:
            await ctx.send("Whitelist is empty.")
            return
        await ctx.send("\n".join(lines))
