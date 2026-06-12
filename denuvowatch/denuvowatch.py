import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

import aiohttp
import discord
from bs4 import BeautifulSoup
from discord.ext import tasks
from redbot.core import Config, commands
from redbot.core.bot import Red

log = logging.getLogger("red.sabby.denuvowatch")

MAX_GAMES = 50
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
}

DEFAULT_GLOBALS = {
    "games": {},          # appid_str -> {name, denuvo, build_id, build_time, header}
    "notify_channel": None,
    "notify_user": None,
    "interval_minutes": 15,
}


class DenuvoWatch(commands.Cog):
    """Watch Steam games for Denuvo and build changes.

    Monitors a single global watchlist and alerts a configured channel when a
    game's Denuvo anti-tamper status flips or when its public build is updated.
    """

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(
            self, identifier=0xDE0040A7C8, force_registration=True
        )
        self.config.register_global(**DEFAULT_GLOBALS)
        self.session = aiohttp.ClientSession(headers=HEADERS)
        self._check_lock = asyncio.Lock()
        self.check_games.start()

    def cog_unload(self):
        self.check_games.cancel()
        asyncio.create_task(self.session.close())

    # ─── Steam helpers ──────────────────────────────────────────────────────

    async def fetch_app_details(self, appid: int) -> dict:
        try:
            async with self.session.get(
                "https://store.steampowered.com/api/appdetails",
                params={"appids": appid, "cc": "us", "l": "en"},
                timeout=aiohttp.ClientTimeout(total=15),
            ) as r:
                r.raise_for_status()
                payload = await r.json()
            result = payload.get(str(appid), {})
            return result.get("data", {}) if result.get("success") else {}
        except Exception:
            return {}

    async def fetch_build_id(self, appid: int):
        """Return (build_id, build_time) for the public branch via SteamCMD API."""
        try:
            async with self.session.get(
                f"https://api.steamcmd.net/v1/info/{appid}",
                timeout=aiohttp.ClientTimeout(total=15),
            ) as r:
                data = await r.json()
            depots = (
                data.get("data", {}).get(str(appid), {}).get("depots", {})
            )
            public_branch = depots.get("branches", {}).get("public", {})
            build_id = public_branch.get("buildid")
            timeupdated = public_branch.get("timeupdated")
            return (
                str(build_id) if build_id else None,
                int(timeupdated) if timeupdated else None,
            )
        except Exception:
            return None, None

    @staticmethod
    def _check_denuvo_api(data: dict) -> bool:
        return "denuvo" in data.get("drm_notice", "").lower()

    async def _check_denuvo_scrape(self, appid: int) -> bool:
        try:
            async with self.session.get(
                f"https://store.steampowered.com/app/{appid}/",
                cookies={"birthtime": "0", "mature_content": "1"},
                timeout=aiohttp.ClientTimeout(total=15),
            ) as r:
                text = await r.text()
            soup = BeautifulSoup(text, "html.parser")
            return "denuvo" in soup.get_text().lower()
        except Exception:
            return False

    async def has_denuvo(self, appid: int, data: dict) -> bool:
        if self._check_denuvo_api(data):
            return True
        return await self._check_denuvo_scrape(appid)

    async def search_steam(self, query: str) -> list:
        try:
            async with self.session.get(
                "https://store.steampowered.com/api/storesearch/",
                params={"term": query, "cc": "us", "l": "en"},
                timeout=aiohttp.ClientTimeout(total=15),
            ) as r:
                payload = await r.json()
            return [
                {"appid": i["id"], "name": i["name"]}
                for i in payload.get("items", [])
            ]
        except Exception:
            return []

    async def get_game_snapshot(self, appid: int) -> Optional[dict]:
        data = await self.fetch_app_details(appid)
        if not data:
            return None
        denuvo = await self.has_denuvo(appid, data)
        build_id, build_time = await self.fetch_build_id(appid)
        return {
            "name": data.get("name", f"AppID {appid}"),
            "denuvo": denuvo,
            "header": data.get("header_image", ""),
            "build_id": build_id,
            "build_time": build_time,
        }

    # ─── Embed builders ─────────────────────────────────────────────────────

    @staticmethod
    def build_denuvo_embed(appid: int, change_type: str, old: dict, new: dict) -> discord.Embed:
        name = new.get("name", old.get("name", f"AppID {appid}"))
        url = f"https://store.steampowered.com/app/{appid}/"
        if change_type == "denuvo_removed":
            embed = discord.Embed(
                title="🎉 Denuvo Removed!",
                description=f"**[{name}]({url})** no longer has Denuvo anti-tamper.",
                color=discord.Color.green(),
            )
            embed.add_field(name="Before", value="⚠️ Had Denuvo", inline=True)
            embed.add_field(name="After", value="✅ Denuvo-free", inline=True)
        else:
            embed = discord.Embed(
                title="⚠️ Denuvo Added",
                description=f"**[{name}]({url})** now has Denuvo anti-tamper.",
                color=discord.Color.red(),
            )
            embed.add_field(name="Before", value="✅ Denuvo-free", inline=True)
            embed.add_field(name="After", value="⚠️ Has Denuvo", inline=True)
        if new.get("header"):
            embed.set_thumbnail(url=new["header"])
        embed.set_footer(text=f"AppID {appid} • DenuvoWatch")
        embed.timestamp = datetime.now(timezone.utc)
        return embed

    @staticmethod
    def build_depot_embed(appid: int, old_build: str, new_build: str, new: dict) -> discord.Embed:
        name = new.get("name", f"AppID {appid}")
        url = f"https://store.steampowered.com/app/{appid}/"
        embed = discord.Embed(
            title="🔧 Build Updated",
            description=f"**[{name}]({url})** received a new build.",
            color=discord.Color.blue(),
        )
        embed.add_field(name="Old Build ID", value=f"`{old_build}`", inline=True)
        embed.add_field(name="New Build ID", value=f"`{new_build}`", inline=True)
        if new.get("build_time"):
            embed.add_field(name="Build Pushed", value=f"<t:{new['build_time']}:R>", inline=True)
        if new.get("header"):
            embed.set_thumbnail(url=new["header"])
        embed.set_footer(text=f"AppID {appid} • DenuvoWatch")
        embed.timestamp = datetime.now(timezone.utc)
        return embed

    # ─── Background check ────────────────────────────────────────────────────

    async def check_games_internal(self) -> bool:
        """Scan the whole watchlist. Returns True if any change was detected."""
        if self._check_lock.locked():
            return False
        async with self._check_lock:
            changes = False
            try:
                channel_id = await self.config.notify_channel()
                channel = self.bot.get_channel(channel_id) if channel_id else None
                if channel is None:
                    log.warning("Notify channel not set or not found (%s).", channel_id)
                    return False

                games = await self.config.games()
                if not games:
                    return False

                notify_user = await self.config.notify_user()
                log.info("Checking %d games…", len(games))

                async def check_single(appid_str):
                    await asyncio.sleep(0.5)
                    snap = await self.get_game_snapshot(int(appid_str))
                    return appid_str, snap

                results = await asyncio.gather(
                    *[check_single(a) for a in games.keys()]
                )

                for appid_str, new in results:
                    if new is None:
                        continue
                    old = games.get(appid_str, {})
                    appid = int(appid_str)

                    if old.get("denuvo") and not new["denuvo"]:
                        await channel.send(
                            embed=self.build_denuvo_embed(appid, "denuvo_removed", old, new)
                        )
                        changes = True
                    elif not old.get("denuvo") and new["denuvo"]:
                        await channel.send(
                            embed=self.build_denuvo_embed(appid, "denuvo_added", old, new)
                        )
                        changes = True

                    old_build = old.get("build_id")
                    new_build = new.get("build_id")
                    if old_build and new_build and old_build != new_build:
                        content = f"<@{notify_user}>" if notify_user else None
                        await channel.send(
                            content=content,
                            embed=self.build_depot_embed(appid, old_build, new_build, new),
                        )
                        changes = True

                    games[appid_str] = {
                        "name": new["name"],
                        "denuvo": new["denuvo"],
                        "build_id": new["build_id"],
                        "build_time": new.get("build_time"),
                        "header": new.get("header", old.get("header", "")),
                    }

                await self.config.games.set(games)
                log.info("Check complete.")
            except Exception:
                log.exception("check_games crashed")
            return changes

    @tasks.loop(minutes=15)
    async def check_games(self):
        await self.check_games_internal()

    @check_games.before_loop
    async def before_check_games(self):
        await self.bot.wait_until_red_ready()
        # Sync the loop interval with the stored config value.
        minutes = await self.config.interval_minutes()
        if minutes and minutes != self.check_games.minutes:
            self.check_games.change_interval(minutes=minutes)

    # ─── Watchlist commands (hybrid: slash + prefix) ─────────────────────────

    async def _resolve_appid(self, query: str, games: dict) -> Optional[int]:
        if query.isdigit():
            return int(query)
        for appid_str, info in games.items():
            if query.lower() in info.get("name", "").lower():
                return int(appid_str)
        results = await self.search_steam(query)
        return results[0]["appid"] if results else None

    @commands.hybrid_command(name="dadd", description="Add a Steam game to the watchlist by name or AppID")
    @commands.is_owner()
    async def dadd(self, ctx: commands.Context, *, query: str):
        """Add a game to the watchlist (searches Steam if you pass a name)."""
        await ctx.defer()
        games = await self.config.games()

        if query.isdigit():
            appid = int(query)
            snapshot = await self.get_game_snapshot(appid)
            if snapshot is None:
                await ctx.send(f"❌ Couldn't find a game with AppID `{appid}`.")
                return
            candidates = [{"appid": appid, "name": snapshot["name"]}]
        else:
            candidates = (await self.search_steam(query))[:5]
            if not candidates:
                await ctx.send("❌ No results found on Steam.")
                return

        if len(candidates) == 1:
            await self._add_appid(ctx, candidates[0]["appid"])
            return

        options = [
            discord.SelectOption(label=c["name"][:100], value=str(c["appid"]))
            for c in candidates
        ]
        select = discord.ui.Select(placeholder="Choose a game…", options=options)

        async def select_callback(inter: discord.Interaction):
            await inter.response.defer()
            await self._add_appid(ctx, int(select.values[0]), interaction=inter)

        select.callback = select_callback
        view = discord.ui.View(timeout=60)
        view.add_item(select)
        await ctx.send("Multiple results found — pick one:", view=view)

    async def _add_appid(self, ctx: commands.Context, appid: int, interaction: discord.Interaction = None):
        async def send(*args, **kwargs):
            if interaction is not None:
                return await interaction.followup.send(*args, **kwargs)
            return await ctx.send(*args, **kwargs)

        games = await self.config.games()

        if str(appid) in games:
            await send(f"ℹ️ **{games[str(appid)]['name']}** is already on the watchlist.")
            return

        if len(games) >= MAX_GAMES:
            await send(f"❌ Watchlist is full ({MAX_GAMES} games max).")
            return

        snapshot = await self.get_game_snapshot(appid)
        if snapshot is None:
            await send(f"❌ Couldn't fetch data for AppID `{appid}`.")
            return

        games[str(appid)] = {
            "name": snapshot["name"],
            "denuvo": snapshot["denuvo"],
            "build_id": snapshot["build_id"],
            "build_time": snapshot.get("build_time"),
            "header": snapshot.get("header", ""),
        }
        await self.config.games.set(games)

        embed = discord.Embed(
            title="✅ Added to Watchlist",
            description=f"**[{snapshot['name']}](https://store.steampowered.com/app/{appid}/)**",
            color=discord.Color.blurple(),
        )
        embed.add_field(name="Denuvo", value="⚠️ Yes" if snapshot["denuvo"] else "✅ No", inline=True)
        embed.add_field(name="Build ID", value=f"`{snapshot['build_id']}`" if snapshot["build_id"] else "Unknown", inline=True)
        embed.add_field(name="Watchlist", value=f"{len(games)}/{MAX_GAMES} games", inline=True)
        if snapshot.get("header"):
            embed.set_thumbnail(url=snapshot["header"])
        embed.set_footer(text=f"AppID {appid}")
        await send(embed=embed)

    @commands.hybrid_command(name="dremove", description="Remove a game from the watchlist")
    @commands.is_owner()
    async def dremove(self, ctx: commands.Context, *, query: str):
        """Remove a game from the watchlist."""
        await ctx.defer()
        games = await self.config.games()
        if not games:
            await ctx.send("📭 Watchlist is empty.")
            return

        matches = []
        for appid_str, info in games.items():
            if query.isdigit() and appid_str == query:
                matches = [(appid_str, info)]
                break
            elif query.lower() in info.get("name", "").lower():
                matches.append((appid_str, info))

        if not matches:
            await ctx.send(f"❌ No game matching `{query}` on the watchlist.")
            return

        if len(matches) == 1:
            appid_str, info = matches[0]
            del games[appid_str]
            await self.config.games.set(games)
            await ctx.send(f"🗑️ Removed **{info['name']}** from the watchlist.")
            return

        options = [
            discord.SelectOption(label=info["name"][:100], value=appid_str)
            for appid_str, info in matches[:25]
        ]
        select = discord.ui.Select(placeholder="Which game to remove?", options=options)

        async def cb(inter: discord.Interaction):
            chosen_id = select.values[0]
            current = await self.config.games()
            name = current.get(chosen_id, {}).get("name", chosen_id)
            current.pop(chosen_id, None)
            await self.config.games.set(current)
            await inter.response.send_message(f"🗑️ Removed **{name}** from the watchlist.")

        select.callback = cb
        view = discord.ui.View(timeout=60)
        view.add_item(select)
        await ctx.send("Multiple matches — choose one:", view=view)

    @commands.hybrid_command(name="dlist", description="Show all watched games and their status")
    async def dlist(self, ctx: commands.Context):
        """Show all watched games with their current Denuvo/build status."""
        await ctx.defer()
        games = await self.config.games()
        if not games:
            await ctx.send("📭 Watchlist is empty. Use `/dadd` to add games.")
            return

        ordered = sorted(games.items(), key=lambda x: x[1].get("name", "").lower())
        lines = []
        for appid_str, info in ordered:
            icon = "⚠️" if info.get("denuvo") else "✅"
            build = f" • build `{info['build_id']}`" if info.get("build_id") else ""
            lines.append(f"{icon} **{info.get('name', appid_str)}** `{appid_str}`{build}")

        embed = discord.Embed(
            title=f"🎮 Steam Watchlist ({len(games)}/{MAX_GAMES})",
            color=discord.Color.blurple(),
        )
        embed.description = "\n".join(lines)[:4000]
        embed.set_footer(text="⚠️ = has Denuvo   ✅ = no Denuvo")
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="dcheck", description="Instantly check a game's current status")
    async def dcheck(self, ctx: commands.Context, *, query: str):
        """Instantly check any game's current Denuvo/build status."""
        await ctx.defer()
        games = await self.config.games()
        appid = await self._resolve_appid(query, games)
        if appid is None:
            await ctx.send(f"❌ Couldn't resolve `{query}` to a Steam game.")
            return

        snapshot = await self.get_game_snapshot(appid)
        if snapshot is None:
            await ctx.send(f"❌ Couldn't fetch data for AppID `{appid}`.")
            return

        in_watchlist = str(appid) in games
        embed = discord.Embed(
            title=f"🔍 {snapshot['name']}",
            url=f"https://store.steampowered.com/app/{appid}/",
            color=discord.Color.blurple(),
        )
        embed.add_field(name="Denuvo", value="⚠️ Yes" if snapshot["denuvo"] else "✅ No", inline=True)
        embed.add_field(name="Build ID", value=f"`{snapshot['build_id']}`" if snapshot["build_id"] else "Unknown", inline=True)
        embed.add_field(name="Watchlist", value="👁️ Watching" if in_watchlist else "➕ Use /dadd", inline=True)
        if snapshot.get("build_time"):
            embed.add_field(name="Build Pushed", value=f"<t:{snapshot['build_time']}:R>", inline=True)
        if snapshot.get("header"):
            embed.set_thumbnail(url=snapshot["header"])
        embed.set_footer(text=f"AppID {appid} • Checked now")
        embed.timestamp = datetime.now(timezone.utc)
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="dforcecheck", description="Manually trigger a full watchlist scan (owner only)")
    @commands.is_owner()
    async def dforcecheck(self, ctx: commands.Context):
        """Manually trigger a full watchlist scan now."""
        await ctx.send("🔄 Running full watchlist check now…")
        changes = await self.check_games_internal()
        if not changes:
            await ctx.send("✅ Check complete — no changes detected.")

    @commands.hybrid_command(name="dstatus", description="Show bot status and next scheduled check time")
    async def dstatus(self, ctx: commands.Context):
        """Show how many games are watched and when the next check runs."""
        games = await self.config.games()
        interval = await self.config.interval_minutes()
        channel_id = await self.config.notify_channel()
        next_iter = self.check_games.next_iteration
        next_str = f"<t:{int(next_iter.timestamp())}:R>" if next_iter else "Starting soon"

        embed = discord.Embed(title="🤖 DenuvoWatch", color=discord.Color.blurple())
        embed.add_field(name="Watching", value=f"{len(games)}/{MAX_GAMES} games", inline=True)
        embed.add_field(name="Check every", value=f"{interval} minutes", inline=True)
        embed.add_field(name="Next check", value=next_str, inline=True)
        embed.add_field(
            name="Alert channel",
            value=f"<#{channel_id}>" if channel_id else "❌ not set",
            inline=True,
        )
        await ctx.send(embed=embed)

    # ─── Config commands (owner only) ────────────────────────────────────────

    @commands.group(name="denuvowatch", aliases=["dwatch"])
    @commands.is_owner()
    async def denuvowatch(self, ctx: commands.Context):
        """DenuvoWatch configuration."""

    @denuvowatch.command(name="channel")
    async def dw_channel(self, ctx: commands.Context, channel: discord.TextChannel):
        """Set the channel where alerts are posted."""
        await self.config.notify_channel.set(channel.id)
        await ctx.send(f"✅ Alerts will be posted in {channel.mention}.")

    @denuvowatch.command(name="pinguser")
    async def dw_pinguser(self, ctx: commands.Context, user: discord.User = None):
        """Set (or clear) the user pinged on build updates. Omit to clear."""
        await self.config.notify_user.set(user.id if user else None)
        if user:
            await ctx.send(f"✅ Build updates will ping {user.mention}.")
        else:
            await ctx.send("✅ Build-update ping cleared.")

    @denuvowatch.command(name="interval")
    async def dw_interval(self, ctx: commands.Context, minutes: int):
        """Set how often (in minutes) the watchlist is scanned."""
        if minutes < 5:
            await ctx.send("❌ Interval must be at least 5 minutes to respect Steam rate limits.")
            return
        await self.config.interval_minutes.set(minutes)
        self.check_games.change_interval(minutes=minutes)
        await ctx.send(f"✅ Check interval set to **{minutes}** minutes.")

    @denuvowatch.command(name="show")
    async def dw_show(self, ctx: commands.Context):
        """Show the current configuration."""
        channel_id = await self.config.notify_channel()
        user_id = await self.config.notify_user()
        interval = await self.config.interval_minutes()
        games = await self.config.games()
        embed = discord.Embed(title="⚙️ DenuvoWatch Config", color=discord.Color.blurple())
        embed.add_field(name="Alert channel", value=f"<#{channel_id}>" if channel_id else "❌ not set", inline=False)
        embed.add_field(name="Ping user", value=f"<@{user_id}>" if user_id else "none", inline=False)
        embed.add_field(name="Interval", value=f"{interval} minutes", inline=False)
        embed.add_field(name="Watchlist size", value=f"{len(games)}/{MAX_GAMES}", inline=False)
        await ctx.send(embed=embed)

    @denuvowatch.command(name="clear")
    async def dw_clear(self, ctx: commands.Context):
        """Clear the entire watchlist."""
        await self.config.games.set({})
        await ctx.send("🗑️ Watchlist cleared.")
