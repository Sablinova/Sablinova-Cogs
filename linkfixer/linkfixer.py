import logging
import re
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set, Tuple, Union

import discord
from redbot.core import Config, commands
from redbot.core.bot import Red

log = logging.getLogger("red.sablinova.linkfixer")

Span = Tuple[int, int]


@dataclass(frozen=True)
class Link:
    name: str
    pattern: re.Pattern
    fixed: str


GENERIC_LINK = re.compile(r"(?<!<)(https?://[^\s|)>\]]+)")
BLOCK_OR_DELIMITER = re.compile(r"```.*?```|`[^`]*?`|\\.|\|\|", re.DOTALL)

ALL_LINKS = [
    Link(
        "fxtwitter",
        re.compile(r"(?<!<)(https?://(?:www\.|m\.)?(?:x|twitter)\.com/([^\s]+status/[^\s|)>\]]+))", re.IGNORECASE),
        "https://fxtwitter.com/",
    ),
    Link(
        "vxtwitter",
        re.compile(r"(?<!<)(https?://(?:www\.|m\.)?(?:x|twitter)\.com/([^\s]+status/[^\s|)>\]]+))", re.IGNORECASE),
        "https://vxtwitter.com/",
    ),
    Link(
        "oginstagram",
        re.compile(r"(?<!<)(https?://(?:www\.)?instagram\.com/([^\s/]+/[^\s|)>\]]+))", re.IGNORECASE),
        "https://www.d.oginstagram.com/",
    ),
    Link(
        "vxreddit",
        re.compile(r"(?<!<)(https?://(?:www\.|old\.)?reddit\.com/(r/[^\s/]+/[^\s|)>\]]+))", re.IGNORECASE),
        "https://vxreddit.com/",
    ),
    Link(
        "redditez",
        re.compile(r"(?<!<)(https?://(?:www\.|old\.)?reddit\.com/(r/[^\s/]+/[^\s|)>\]]+))", re.IGNORECASE),
        "https://redditez.com/",
    ),
    Link(
        "phixiv",
        re.compile(r"(?<!<)(https?://(?:www\.)?pixiv\.net/([^\s|)>\]]+))", re.IGNORECASE),
        "https://phixiv.net/",
    ),
    Link(
        "viewthreads",
        re.compile(r"(?<!<)(https?://(?:www\.)?threads\.com/(@[^\s/]+/[^\s|)>\]]+))", re.IGNORECASE),
        "https://viewthreads.com/",
    ),
    Link(
        "tnktok",
        re.compile(r"(?<!<)(https?://(?:www\.)?tiktok\.com/([^\s/]+/[^/]+/[^\s|)>\]]+))", re.IGNORECASE),
        "https://tnktok.com/",
    ),
    Link(
        "vm.tnktok",
        re.compile(r"(?<!<)(https?://vm\.tiktok\.com/([^\s|)>\]]+))", re.IGNORECASE),
        "https://vm.tnktok.com/",
    ),
    Link(
        "tiktokez",
        re.compile(r"(?<!<)(https?://(?:www\.)?tiktok\.com/([^\s/]+/[^/]+/[^\s|)>\]]+))", re.IGNORECASE),
        "https://tiktokez.com/",
    ),
    Link(
        "vm.tiktokez",
        re.compile(r"(?<!<)(https?://vm\.tiktok\.com/([^\s|)>\]]+))", re.IGNORECASE),
        "https://vm.tiktokez.com/",
    ),
    Link(
        "fixembed",
        re.compile(
            r"(?<![<=])(https?:\/\/(?:(?:www|\w\w?)\.)?(?:twitter\.com|x\.com|instagram\.com|reddit\.com|redd\.it|threads\.(?:net|com)|pixiv\.net|bilibili\.com|b23\.tv|youtube\.com\/post|pinterest\.com\/pin|pin\.it|tiktok\.com|sta\.sh)\/[^\s|)>\]]+)",
            re.IGNORECASE,
        ),
        "https://fixembed.app/embed?url=",
    ),
]


def get_code_and_spoiler_spans(content: str) -> Tuple[List[Span], List[Span]]:
    """Returns lists of code blocks and spoiler blocks."""
    code_spans: List[Span] = []
    spoiler_spans: List[Span] = []
    spoiler_start = None
    for m in BLOCK_OR_DELIMITER.finditer(content):
        token = m.group(0)
        if token == "||":
            if spoiler_start is None:
                spoiler_start = m.end()
            else:
                spoiler_spans.append((spoiler_start, m.start()))
                spoiler_start = None
        elif token.startswith("`"):
            code_spans.append((m.start(), m.end()))
    return code_spans, spoiler_spans


def is_in_span(spans: List[Span], pos: int) -> bool:
    return any(start <= pos < end for start, end in spans)


class LinkFixer(commands.Cog):
    """Sends modified links to embed content from popular social media sites with per-channel toggles."""

    def __init__(self, bot: Red, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.bot = bot
        self.config = Config.get_conf(self, identifier=44141349)
        self.config.register_guild(
            enabled=False,
            enabled_channels=[],
            disabled_channels=[],
            disabled_links=["vxtwitter", "redditez", "tiktokez", "vm.tiktokez"],
            language=None,
        )
        self.enabled_guilds: List[int] = []
        self.disabled_links: Dict[int, List[str]] = {}
        self.enabled_channels: Dict[int, Set[int]] = defaultdict(set)
        self.disabled_channels: Dict[int, Set[int]] = defaultdict(set)

    async def cog_load(self):
        all_guilds = await self.config.all_guilds()
        for gid, config in all_guilds.items():
            g_int = int(gid)
            if config.get("enabled", False):
                self.enabled_guilds.append(g_int)
            self.disabled_links[g_int] = config.get("disabled_links", [])
            self.enabled_channels[g_int] = set(config.get("enabled_channels", []))
            self.disabled_channels[g_int] = set(config.get("disabled_channels", []))

    async def red_delete_data_for_user(self, *args, **kwargs):
        """Nothing to delete."""
        pass

    def is_channel_enabled(
        self,
        guild_id: int,
        channel: Union[discord.TextChannel, discord.VoiceChannel, discord.Thread, Any],
    ) -> bool:
        """Determines whether LinkFixer is active in the given channel or thread."""
        channel_id = channel.id
        parent_id = getattr(channel, "parent_id", None)
        if parent_id is None and hasattr(channel, "parent") and channel.parent:
            parent_id = getattr(channel.parent, "id", None)

        disabled = self.disabled_channels.get(guild_id, set())
        enabled = self.enabled_channels.get(guild_id, set())

        # Explicit channel or parent disable check
        if channel_id in disabled or (parent_id and parent_id in disabled):
            return False

        # Explicit channel or parent enable check
        if channel_id in enabled or (parent_id and parent_id in enabled):
            return True

        # Fallback to server-wide status
        return guild_id in self.enabled_guilds

    @commands.Cog.listener()
    async def on_message_without_command(self, message: discord.Message):
        if not message.guild or message.author == message.guild.me or message.author.bot:
            return

        if not self.is_channel_enabled(message.guild.id, message.channel):
            return

        perms = message.channel.permissions_for(message.guild.me)
        if not perms.send_messages or not perms.embed_links:
            return

        if not await self.is_valid_red_message(message):
            return

        code_spans, spoiler_spans = get_code_and_spoiler_spans(message.content)
        spoiler_edge_case = any("||" in message.content[start:end] for start, end in code_spans)

        matched_links: List[str] = []
        for match in GENERIC_LINK.finditer(message.content):
            if is_in_span(code_spans, match.start()):
                continue
            link = match.group(0)
            spoilered_link = f"|| {link} ||"
            should_spoiler = is_in_span(spoiler_spans, match.start()) or spoiler_edge_case
            if link in matched_links or spoilered_link in matched_links:
                if should_spoiler and link in matched_links:
                    matched_links[matched_links.index(link)] = spoilered_link
                continue
            matched_links.append(spoilered_link if should_spoiler else link)

        if not matched_links:
            return

        language = await self.config.guild(message.guild).language()
        any_fixed = False
        link_types = [link for link in ALL_LINKS if link.name not in self.disabled_links.get(message.guild.id, [])]
        for i in range(len(matched_links)):
            link = matched_links[i]
            for link_type in link_types:
                if match := link_type.pattern.search(link):
                    any_fixed = True
                    tail = [g for g in match.groups() if g][-1].split("?")[0]
                    if language and "fxtwitter" in link_type.fixed:
                        tail = tail.rstrip("/") + "/en"
                    matched_links[i] = link.replace(match.group(0), f"{link_type.fixed}{tail}")
                    break

        if not any_fixed:
            return

        matched_links.insert(0, f"-# {message.author.mention} I fixed the links so the content embeds better.")
        await message.channel.send("\n".join(matched_links), allowed_mentions=discord.AllowedMentions.none())
        if message.channel.permissions_for(message.guild.me).manage_messages:
            await message.edit(suppress=True)

    async def is_valid_red_message(self, message: discord.Message) -> bool:
        return (
            isinstance(message.author, discord.Member)
            and await self.bot.allowed_by_whitelist_blacklist(message.author)
            and await self.bot.ignored_channel_or_guild(message)
            and not await self.bot.cog_disabled_in_guild(self, message.guild)
        )

    @commands.group(name="linkfixer", aliases=["linkfix"], invoke_without_command=True)
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def command_linkfixer(self, ctx: commands.Context):
        """Configure the LinkFixer cog."""
        await ctx.send_help()

    @command_linkfixer.command(name="enable")
    async def command_linkfixer_enable(self, ctx: commands.Context):
        """Enable LinkFixer across the entire server."""
        assert ctx.guild
        await self.config.guild(ctx.guild).enabled.set(True)
        if ctx.guild.id not in self.enabled_guilds:
            self.enabled_guilds.append(ctx.guild.id)
        await ctx.reply(f"LinkFixer is now **enabled server-wide** in {ctx.guild.name}.")

    @command_linkfixer.command(name="disable")
    async def command_linkfixer_disable(self, ctx: commands.Context):
        """Disable LinkFixer across the server (unless overridden per channel)."""
        assert ctx.guild
        await self.config.guild(ctx.guild).enabled.set(False)
        if ctx.guild.id in self.enabled_guilds:
            self.enabled_guilds.remove(ctx.guild.id)
        await ctx.reply(f"LinkFixer is now **disabled server-wide** in {ctx.guild.name}.")

    # =========================================================================
    # PER-CHANNEL CONFIGURATION
    # =========================================================================

    @command_linkfixer.group(name="channel", aliases=["channels", "chan"], invoke_without_command=True)
    async def command_linkfixer_channel(self, ctx: commands.Context):
        """Configure per-channel enable and disable overrides."""
        await ctx.send_help()

    @command_linkfixer_channel.command(name="enable", aliases=["on", "add"])
    async def command_linkfixer_channel_enable(
        self, ctx: commands.Context, channel: Optional[discord.TextChannel] = None
    ):
        """Enable LinkFixer in a specific channel (even if disabled server-wide)."""
        target = channel or ctx.channel
        gid = ctx.guild.id

        self.disabled_channels[gid].discard(target.id)
        self.enabled_channels[gid].add(target.id)

        await self.config.guild(ctx.guild).disabled_channels.set(list(self.disabled_channels[gid]))
        await self.config.guild(ctx.guild).enabled_channels.set(list(self.enabled_channels[gid]))

        await ctx.reply(f"LinkFixer is now **explicitly enabled** in {target.mention}.")

    @command_linkfixer_channel.command(name="disable", aliases=["off", "remove", "ignore"])
    async def command_linkfixer_channel_disable(
        self, ctx: commands.Context, channel: Optional[discord.TextChannel] = None
    ):
        """Disable LinkFixer in a specific channel (even if enabled server-wide)."""
        target = channel or ctx.channel
        gid = ctx.guild.id

        self.enabled_channels[gid].discard(target.id)
        self.disabled_channels[gid].add(target.id)

        await self.config.guild(ctx.guild).enabled_channels.set(list(self.enabled_channels[gid]))
        await self.config.guild(ctx.guild).disabled_channels.set(list(self.disabled_channels[gid]))

        await ctx.reply(f"LinkFixer is now **explicitly disabled** in {target.mention}.")

    @command_linkfixer_channel.command(name="reset", aliases=["clear", "default"])
    async def command_linkfixer_channel_reset(
        self, ctx: commands.Context, channel: Optional[discord.TextChannel] = None
    ):
        """Reset a channel override so it follows the server-wide setting."""
        target = channel or ctx.channel
        gid = ctx.guild.id

        self.enabled_channels[gid].discard(target.id)
        self.disabled_channels[gid].discard(target.id)

        await self.config.guild(ctx.guild).enabled_channels.set(list(self.enabled_channels[gid]))
        await self.config.guild(ctx.guild).disabled_channels.set(list(self.disabled_channels[gid]))

        status = "enabled" if gid in self.enabled_guilds else "disabled"
        await ctx.reply(f"Removed override for {target.mention}. It now follows the server setting ({status}).")

    @command_linkfixer_channel.command(name="list")
    async def command_linkfixer_channel_list(self, ctx: commands.Context):
        """List all channel overrides in this server."""
        gid = ctx.guild.id
        enabled_cids = self.enabled_channels.get(gid, set())
        disabled_cids = self.disabled_channels.get(gid, set())

        server_state = "Enabled" if gid in self.enabled_guilds else "Disabled"
        lines = [f"**Server Default:** {server_state}"]

        if enabled_cids:
            en_mentions = [f"<#{cid}>" for cid in sorted(enabled_cids)]
            lines.append(f"**Explicitly Enabled Channels:**\n" + ", ".join(en_mentions))
        else:
            lines.append("**Explicitly Enabled Channels:** None")

        if disabled_cids:
            dis_mentions = [f"<#{cid}>" for cid in sorted(disabled_cids)]
            lines.append(f"**Explicitly Disabled Channels:**\n" + ", ".join(dis_mentions))
        else:
            lines.append("**Explicitly Disabled Channels:** None")

        embed = discord.Embed(
            title=f"LinkFixer Channel Overrides for {ctx.guild.name}",
            description="\n\n".join(lines),
            color=await ctx.embed_color(),
        )
        await ctx.send(embed=embed)

    # =========================================================================
    # TRANSLATION & LINK TYPE TOGGLES
    # =========================================================================

    @command_linkfixer.group(name="translate", aliases=["language", "english"], invoke_without_command=True)
    async def command_linkfixer_translate(self, ctx: commands.Context):
        """Controls automatic embed translations."""
        await ctx.send_help()

    @command_linkfixer_translate.command(name="enable", aliases=["english", "on", "yes", "true"])
    async def command_linkfixer_translate_enable(self, ctx: commands.Context):
        """Enables compatible links to be translated to English."""
        assert ctx.guild
        await self.config.guild(ctx.guild).language.set("en")
        await ctx.reply("Compatible links will be translated to English (such as fxtwitter).")

    @command_linkfixer_translate.command(name="disable", aliases=["off", "no", "false"])
    async def command_linkfixer_translate_disable(self, ctx: commands.Context):
        """Disables compatible links from being translated to English."""
        assert ctx.guild
        await self.config.guild(ctx.guild).language.set(None)
        await ctx.reply("Embeds will not be translated.")

    @command_linkfixer.group(name="links", aliases=["link", "fix"], invoke_without_command=True)
    async def command_linkfixer_links(self, ctx: commands.Context):
        """List or toggle available links for the fixer."""
        await ctx.send_help()

    @command_linkfixer_links.command(name="list")
    async def command_linkfixer_links_list(self, ctx: commands.Context):
        """List all available links for the fixer."""
        assert ctx.guild
        disabled_links = await self.config.guild(ctx.guild).disabled_links()
        links = []
        for link in ALL_LINKS:
            links.append(f" `{'⛔' if link.name in disabled_links else '✅'} {link.name}`")
        await ctx.send("-# (Links will be fixed with the first matching fixer in the list)\n>>> " + "\n".join(links))

    @command_linkfixer_links.command(name="enable", aliases=["add"])
    async def command_linkfixer_links_enable(self, ctx: commands.Context, *link_names: str):
        """Enables one or more link fixes."""
        assert ctx.guild
        if ctx.guild.id not in self.enabled_guilds:
            return await ctx.reply(f"LinkFixer is not enabled in {ctx.guild.name}")
        if not link_names:
            return await ctx.send_help()
        all_links = set(link.name for link in ALL_LINKS)
        if len(link_names) == 1 and link_names[0].lower() == "all":
            link_names = list(all_links)
        disabled_links = await self.config.guild(ctx.guild).disabled_links()
        disabled_links = list(set(disabled_links) - set(link_names))
        await self.config.guild(ctx.guild).disabled_links.set(disabled_links)
        self.disabled_links[ctx.guild.id] = disabled_links
        invalid_links = list(set(link_names) - all_links)
        if invalid_links:
            await ctx.send("Invalid options: " + ", ".join([f"`{link}`" for link in invalid_links]))
        else:
            await ctx.tick(message="Done")
        await self.command_linkfixer_links_list(ctx)

    @command_linkfixer_links.command(name="disable", aliases=["remove"])
    async def command_linkfixer_links_disable(self, ctx: commands.Context, *link_names: str):
        """Disables one or more link fixes."""
        assert ctx.guild
        if ctx.guild.id not in self.enabled_guilds:
            return await ctx.reply(f"LinkFixer is not enabled in {ctx.guild.name}")
        if not link_names:
            return await ctx.send_help()
        all_links = set(link.name for link in ALL_LINKS)
        if len(link_names) == 1 and link_names[0].lower() == "all":
            link_names = list(all_links)
        disabled_links = await self.config.guild(ctx.guild).disabled_links()
        disabled_links = list(all_links & (set(disabled_links) | set(link_names)))
        await self.config.guild(ctx.guild).disabled_links.set(disabled_links)
        self.disabled_links[ctx.guild.id] = disabled_links
        invalid_links = list(set(link_names) - all_links)
        if invalid_links:
            await ctx.send("Invalid options: " + ", ".join([f"`{link}`" for link in invalid_links]))
        else:
            await ctx.tick(message="Done")
        await self.command_linkfixer_links_list(ctx)
