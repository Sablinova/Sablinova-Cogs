"""Denuvoauto — manual activation support wizard for offline/no-internet users.

This cog is a TRIAGE and ROUTING tool. It does NOT automate DRM bypass,
token application, anti-tamper handling, or any unsafe automation. All
sensitive steps are MANUAL STAFF GATES that pause the wizard and ping
staff for human review.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

import discord
from discord import app_commands
from redbot.core import Config, commands
from redbot.core.bot import Red

log = logging.getLogger("red.sablinova.denuvoauto")


class N:
    START = "START"
    ERROR_SELECT = "ERROR_SELECT"
    STEAM_CHECK = "STEAM_CHECK"
    STEAM_INSTALL_STEPS = "STEAM_INSTALL_STEPS"
    RUN_GAME = "RUN_GAME"
    BITDEFENDER_CHECK = "BITDEFENDER_CHECK"
    MINUS_BITDEFENDER = "MINUS_BITDEFENDER"
    COLDCLIENT_CHECK = "COLDCLIENT_CHECK"
    MINUS_PCL = "MINUS_PCL"
    UE_GAME_CHECK = "UE_GAME_CHECK"
    UE_GUIDE = "UE_GUIDE"
    NORMAL_GUIDE = "NORMAL_GUIDE"
    WAIT_FOR_TXTS = "WAIT_FOR_TXTS"
    TXT_PARSED = "TXT_PARSED"
    GPU_QUESTION = "GPU_QUESTION"
    CPU_AVX2_QUESTION = "CPU_AVX2_QUESTION"
    VERIFY_GAME = "VERIFY_GAME"
    APPLY_TOKEN = "APPLY_TOKEN"
    ERROR_AFTER_TOKEN = "ERROR_AFTER_TOKEN"
    SCREENSHOT_ERROR = "SCREENSHOT_ERROR"
    ANTI_TAMPER_SUBCODE = "ANTI_TAMPER_SUBCODE"
    HYPERVISOR = "HYPERVISOR"
    UE_CRASH = "UE_CRASH"
    NO_LICENSE = "NO_LICENSE"
    STEAMAPI = "STEAMAPI"
    ISTEAMUSER = "ISTEAMUSER"
    CIRNO_SANC = "CIRNO_SANC"
    UNABLE_TO_IDENTIFY = "UNABLE_TO_IDENTIFY"
    MISC_BRANCH = "MISC_BRANCH"
    SAVE_GONE = "SAVE_GONE"
    AUTO_SAVE = "AUTO_SAVE"
    DESCRIBE_DIFF = "DESCRIBE_DIFF"
    STAFF_HANDOFF = "STAFF_HANDOFF"
    FINISHED = "FINISHED"


MANUAL_GATES = {N.COLDCLIENT_CHECK, N.APPLY_TOKEN, N.ANTI_TAMPER_SUBCODE, N.HYPERVISOR}

DEFAULT_TAGS: dict[str, str] = {
    "steam_install_steps": "Please make sure Steam is installed, then try launching the game again.",
    "run_game": "Have you tried running the game and reached the current error state?",
    "minus_bitdefender": "Noted. Continue after temporarily excluding the relevant game path if staff already instructed that step.",
    "coldclient_check": (
        "🛑 **Manual staff step.**\n"
        "ColdClient-related review requires a staff member. Please wait — "
        "staff will assist you here. Do not attempt unsafe workarounds."
    ),
    "minus_pcl": "PCL/launch context noted. Moving to the next compatibility check.",
    "ue_guide": "UE branch selected. If available, upload `pub_dep.txt` and `pub_crash.txt` now.",
    "normal_guide": "Standard branch selected. If available, upload `pub_dep.txt` and `pub_crash.txt` now.",
    "wait_for_txts": "Upload `pub_dep.txt` and/or `pub_crash.txt`, then press **I uploaded the txts**. If you do not have them, press **Skip / no logs**.",
    "verify_game": "Have you already verified/rechecked the game files in the intended launcher context?",
    "apply_token": (
        "🛑 **Manual staff step.**\n"
        "Token application requires a staff member. Please wait — "
        "staff will assist you here. Do not attempt unsafe workarounds."
    ),
    "anti_tamper_prompt": "Choose the anti-tamper subcode shown, if any.",
    "anti_tamper_wait": (
        "🛑 **Manual staff step.**\n"
        "Anti-tamper code handling is reviewed by staff only. Please wait — "
        "staff will assist you here without unsafe automation."
    ),
    "hypervisor": (
        "🛑 **Manual staff step.**\n"
        "Hypervisor-related troubleshooting needs human staff review. Please wait — "
        "staff will assist you here. Do not try risky bypass steps."
    ),
    "save_gone": "Save-loss issues should be reviewed by staff with your context preserved.",
    "auto_save": "Autosave issues should be reviewed by staff with the rest of your session details.",
    "describe_diff": "Thanks. Staff will review the session report and ask for any extra detail they need.",
}

MANUAL_GATE_TAG_KEYS: set[str] = {"coldclient_check", "apply_token", "anti_tamper_wait", "hypervisor"}

TAG_USED_IN: dict[str, str] = {
    "steam_install_steps": "Shown after the Steam-installed=No branch.",
    "run_game": "YesNo prompt before bitdefender check.",
    "minus_bitdefender": "Bitdefender exclusion follow-up.",
    "coldclient_check": "ColdClient manual staff gate message.",
    "minus_pcl": "PCL/launch follow-up before UE check.",
    "ue_guide": "UE branch — invites pub_dep/pub_crash uploads.",
    "normal_guide": "Standard branch — invites pub_dep/pub_crash uploads.",
    "wait_for_txts": "Wait-for-attachment prompt with upload/skip buttons.",
    "verify_game": "YesNo prompt before the token gate.",
    "apply_token": "Token application manual staff gate message.",
    "anti_tamper_prompt": "Subcode select prompt (non-gate).",
    "anti_tamper_wait": "Anti-tamper manual staff gate message after subcode chosen.",
    "hypervisor": "Hypervisor manual staff gate message.",
    "save_gone": "Save-loss branch handoff intro.",
    "auto_save": "Autosave-issue branch handoff intro.",
    "describe_diff": "Generic 'describe difference' staff handoff intro.",
}


@dataclass
class Session:
    guild_id: int
    channel_id: int
    user_id: int
    node: str = N.START
    answers: dict[str, Any] = field(default_factory=dict)
    parsed_dep: Optional[dict[str, Any]] = None
    parsed_crash: Optional[dict[str, Any]] = None


def parse_pub_dep(text: str) -> dict[str, Any]:
    try:
        installed: list[str] = []
        missing: list[str] = []
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith("+"):
                token = stripped[1:].strip().split()[0] if stripped[1:].strip() else "item"
                installed.append(token)
            elif stripped.startswith("-"):
                token = stripped[1:].strip().split()[0] if stripped[1:].strip() else "item"
                missing.append(token)
        lines = [line.strip() for line in text.splitlines() if line.strip()][:30]
        summary = "\n".join(lines)[:1500]
        return {
            "installed": installed,
            "missing": missing,
            "steam_present": "steam" in text.lower(),
            "raw_summary": summary,
        }
    except Exception:
        log.exception("Failed to parse pub_dep text")
        return {"installed": [], "missing": [], "steam_present": False, "raw_summary": ""}


def parse_pub_crash(text: str) -> dict[str, Any]:
    try:
        mem_match = re.search(r"(?i)(?:memory|ram)[^\n]*?(\d+(?:\.\d+)?)\s*GB", text)
        memory_gb = float(mem_match.group(1)) if mem_match else None
        lines = [line.strip() for line in text.splitlines() if line.strip()][:30]
        summary = "\n".join(lines)[:1500]
        return {
            "pubcrashlogger": "pubcrashlogger" in text.lower(),
            "memory_gb": memory_gb,
            "memory_low": memory_gb is not None and memory_gb <= 8.0,
            "raw_summary": summary,
        }
    except Exception:
        log.exception("Failed to parse pub_crash text")
        return {
            "pubcrashlogger": False,
            "memory_gb": None,
            "memory_low": False,
            "raw_summary": "",
        }


class _Target:
    def __init__(self, channel: discord.abc.Messageable, responder: Optional[Any] = None) -> None:
        self.channel = channel
        self._responder = responder

    async def send(self, content: Optional[str] = None, **kwargs: Any) -> Optional[discord.Message]:
        try:
            if self._responder is not None and not self._responder.is_done():
                await self._responder.send_message(content=content, ephemeral=False, **kwargs)
                return None
        except Exception:
            log.exception("Initial interaction response failed")
        try:
            return await self.channel.send(content=content, **kwargs)
        except Exception:
            log.exception("Channel send failed")
            return None


class BaseWizardView(discord.ui.View):
    def __init__(self, cog: "Denuvoauto", session: Session) -> None:
        super().__init__(timeout=900)
        self.cog = cog
        self.session = session
        self.message: Optional[discord.Message] = None

    async def on_timeout(self) -> None:
        try:
            if self.message is not None:
                await self.message.edit(content="Wizard timed out — run /denuvoauto again.", view=None)
        except Exception:
            log.exception("Failed to edit timed out wizard view")


class YesNoView(BaseWizardView):
    def __init__(self, cog: "Denuvoauto", session: Session, yes_node: str, no_node: str) -> None:
        super().__init__(cog, session)
        self.yes_node = yes_node
        self.no_node = no_node

    async def _go(self, interaction: discord.Interaction, next_node: str, label: str) -> None:
        self.session.answers[f"answer_{self.session.node}"] = label
        self.session.node = next_node
        await interaction.response.edit_message(content=f"Selected: {label}", view=None)
        await self.cog._advance(self.session, channel=interaction.channel)

    @discord.ui.button(label="Yes", style=discord.ButtonStyle.success)
    async def yes(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self._go(interaction, self.yes_node, "yes")

    @discord.ui.button(label="No", style=discord.ButtonStyle.secondary)
    async def no(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self._go(interaction, self.no_node, "no")


class ChoiceView(BaseWizardView):
    def __init__(self, cog: "Denuvoauto", session: Session, choices: list[tuple[str, str]]) -> None:
        super().__init__(cog, session)
        for index, (label, next_node) in enumerate(choices):
            self.add_item(ChoiceButton(label=label, next_node=next_node, row=index // 5))


class ChoiceButton(discord.ui.Button[ChoiceView]):
    def __init__(self, label: str, next_node: str, row: int = 0) -> None:
        super().__init__(label=label, style=discord.ButtonStyle.primary, row=row)
        self.next_node = next_node

    async def callback(self, interaction: discord.Interaction) -> None:
        assert self.view is not None
        self.view.session.answers[f"choice_{self.view.session.node}"] = self.label
        self.view.session.node = self.next_node
        await interaction.response.edit_message(content=f"Selected: {self.label}", view=None)
        await self.view.cog._advance(self.view.session, channel=interaction.channel)


class ErrorSelect(discord.ui.Select["ErrorSelectView"]):
    def __init__(self) -> None:
        options = [
            discord.SelectOption(label="UE crash", value=N.UE_CRASH),
            discord.SelectOption(label="No license", value=N.NO_LICENSE),
            discord.SelectOption(label="SteamAPI", value=N.STEAMAPI),
            discord.SelectOption(label="ISteamUser", value=N.ISTEAMUSER),
            discord.SelectOption(label="Anti tamper", value=N.ANTI_TAMPER_SUBCODE),
            discord.SelectOption(label="Cirno / Sanc", value=N.CIRNO_SANC),
            discord.SelectOption(label="Hypervisor", value=N.HYPERVISOR),
            discord.SelectOption(label="Something else / unable to identify", value=N.UNABLE_TO_IDENTIFY),
            discord.SelectOption(label="No error / misc", value=N.MISC_BRANCH),
        ]
        super().__init__(placeholder="Pick the closest error match", options=options)

    async def callback(self, interaction: discord.Interaction) -> None:
        assert self.view is not None
        self.view.session.answers["error_select"] = self.values[0]
        self.view.session.node = self.values[0]
        await interaction.response.edit_message(content=f"Selected branch: {self.values[0]}", view=None)
        await self.view.cog._advance(self.view.session, channel=interaction.channel)


class ErrorSelectView(BaseWizardView):
    def __init__(self, cog: "Denuvoauto", session: Session) -> None:
        super().__init__(cog, session)
        self.add_item(ErrorSelect())


class AntiTamperSelect(discord.ui.Select["AntiTamperSelectView"]):
    def __init__(self) -> None:
        options = [
            discord.SelectOption(label="88500000", value="88500000"),
            discord.SelectOption(label="88500005", value="88500005"),
            discord.SelectOption(label="88500006", value="88500006"),
            discord.SelectOption(label="Unable to identify", value="unable_to_identify"),
        ]
        super().__init__(placeholder="Choose the anti-tamper subcode", options=options)

    async def callback(self, interaction: discord.Interaction) -> None:
        assert self.view is not None
        self.view.session.answers["anti_tamper_subcode"] = self.values[0]
        self.view.session.node = N.ANTI_TAMPER_SUBCODE
        await interaction.response.edit_message(content=f"Subcode noted: {self.values[0]}", view=None)
        await self.view.cog._advance(self.view.session, channel=interaction.channel)


class AntiTamperSelectView(BaseWizardView):
    def __init__(self, cog: "Denuvoauto", session: Session) -> None:
        super().__init__(cog, session)
        self.add_item(AntiTamperSelect())


class UploadedView(BaseWizardView):
    @discord.ui.button(label="I uploaded the txts", style=discord.ButtonStyle.success)
    async def uploaded(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        assert interaction.channel is not None
        note = await self.cog._scan_recent_uploads(self.session, interaction.channel)
        self.session.answers["upload_scan"] = note
        self.session.node = N.TXT_PARSED
        await interaction.response.edit_message(content=f"Upload scan complete: {note}", view=None)
        await self.cog._advance(self.session, channel=interaction.channel)

    @discord.ui.button(label="Skip / no logs", style=discord.ButtonStyle.secondary)
    async def skip(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        self.session.answers["upload_scan"] = "User skipped log upload."
        self.session.node = N.TXT_PARSED
        await interaction.response.edit_message(content="Proceeding without logs.", view=None)
        await self.cog._advance(self.session, channel=interaction.channel)


class SetupWizardView(discord.ui.View):
    def __init__(self, cog: "Denuvoauto", guild: discord.Guild, owner_id: int, keys: list[str]) -> None:
        super().__init__(timeout=900)
        self.cog = cog
        self.guild = guild
        self.owner_id = owner_id
        self.keys = keys
        self.index = 0
        self.changes: dict[str, str] = {}
        self.message: Optional[discord.Message] = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user is None or interaction.user.id != self.owner_id:
            try:
                await interaction.response.send_message(
                    "This setup wizard is owned by someone else.", ephemeral=True
                )
            except Exception:
                log.exception("setup wizard ownership reply failed")
            return False
        return True

    async def on_timeout(self) -> None:
        try:
            if self.message is not None:
                await self.message.edit(
                    content="Setup wizard timed out — run `[p]denuvoautosetup` again to continue.",
                    embed=None,
                    view=None,
                )
        except Exception:
            log.exception("Failed to edit timed out setup wizard view")

    async def build_step_embed(self) -> discord.Embed:
        key = self.keys[self.index]
        default = DEFAULT_TAGS.get(key, "")
        tags = await self.cog.config.guild(self.guild).tags()
        current = tags.get(key) if isinstance(tags, dict) else None
        is_gate = key in MANUAL_GATE_TAG_KEYS
        embed = discord.Embed(title=f"Setup [{self.index + 1}/{len(self.keys)}]: {key}")
        if is_gate:
            embed.description = (
                "⚠️ This is a MANUAL STAFF GATE message. The text only changes what the user sees while waiting — "
                "the wait-and-handoff behavior cannot be disabled."
            )
        embed.add_field(name="Used in", value=TAG_USED_IN.get(key, "—"), inline=False)
        embed.add_field(name="Default", value=f"```\n{default[:1000]}\n```", inline=False)
        embed.add_field(
            name="Current override",
            value=(f"```\n{current[:1000]}\n```" if current else "(unset — using default)"),
            inline=False,
        )
        return embed

    async def _update_message(
        self,
        interaction: discord.Interaction,
        *,
        embed: discord.Embed,
        view: Optional[discord.ui.View],
    ) -> None:
        try:
            if not interaction.response.is_done():
                await interaction.response.edit_message(embed=embed, view=view)
                return
        except Exception:
            log.exception("setup wizard inline edit failed")
        try:
            if self.message is not None:
                await self.message.edit(embed=embed, view=view)
        except Exception:
            log.exception("setup wizard fallback edit failed")

    async def advance_step(self, interaction: discord.Interaction) -> None:
        self.index += 1
        if self.index >= len(self.keys):
            summary = discord.Embed(
                title="Setup complete",
                description="\n".join(
                    f"`{k}`: {'updated' if k in self.changes else 'unchanged'}" for k in self.keys
                )[:4000],
            )
            await self._update_message(interaction, embed=summary, view=None)
            self.stop()
            return
        embed = await self.build_step_embed()
        await self._update_message(interaction, embed=embed, view=self)

    @discord.ui.button(label="Set", style=discord.ButtonStyle.primary)
    async def set_button(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        key = self.keys[self.index]
        tags = await self.cog.config.guild(self.guild).tags()
        current = tags.get(key) if isinstance(tags, dict) else None
        modal = TagModal(self, key, current or DEFAULT_TAGS.get(key, ""))
        try:
            await interaction.response.send_modal(modal)
        except Exception:
            log.exception("setup wizard modal send failed")

    @discord.ui.button(label="Keep current", style=discord.ButtonStyle.secondary)
    async def keep_button(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self.advance_step(interaction)

    @discord.ui.button(label="Skip / unset", style=discord.ButtonStyle.danger)
    async def skip_button(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        key = self.keys[self.index]
        try:
            async with self.cog.config.guild(self.guild).tags() as tags:
                tags.pop(key, None)
            self.changes[key] = "(unset)"
        except Exception:
            log.exception("setup wizard unset failed for %s", key)
        await self.advance_step(interaction)


class TagModal(discord.ui.Modal):
    def __init__(self, parent: SetupWizardView, key: str, prefill: str) -> None:
        super().__init__(title=f"Edit tag: {key}"[:45])
        self.parent = parent
        self.key = key
        self.input = discord.ui.TextInput(
            label="Tag content",
            style=discord.TextStyle.paragraph,
            required=True,
            max_length=2000,
            default=prefill[:2000] if prefill else None,
        )
        self.add_item(self.input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        value = str(self.input.value or "").strip()
        if not value:
            try:
                await interaction.response.send_message("Empty value — keeping current.", ephemeral=True)
            except Exception:
                log.exception("modal empty reply failed")
            await self.parent.advance_step(interaction)
            return
        try:
            async with self.parent.cog.config.guild(self.parent.guild).tags() as tags:
                tags[self.key] = value
            self.parent.changes[self.key] = "updated"
        except Exception:
            log.exception("modal write failed for %s", self.key)
        await self.parent.advance_step(interaction)


def _editor_check():
    async def predicate(ctx: commands.Context) -> bool:
        cog: Denuvoauto = ctx.cog  # type: ignore[assignment]
        if cog is None or ctx.guild is None or not isinstance(ctx.author, discord.Member):
            return False
        return await cog._is_editor(ctx.author)

    return commands.check(predicate)


class Denuvoauto(commands.Cog):
    """Manual-activation support wizard for Denuvo offline/no-internet users.

    Slash: /denuvoauto launches the guided triage wizard.
    Prefix admin group: [p]denuvoautoset for staff role/channel config.

    NOTE: All token/DRM-sensitive steps are MANUAL STAFF GATES. This cog
    only collects context, parses optional pub_dep.txt/pub_crash.txt
    diagnostic logs the user uploads, and routes to staff. It never
    instructs or automates DRM circumvention.
    """

    def __init__(self, bot: Red) -> None:
        self.bot = bot
        self.config = Config.get_conf(self, identifier=948273615, force_registration=True)
        self.config.register_guild(
            staff_role_id=None,
            staff_channel_id=None,
            log_channel_id=None,
            tags={},
            editor_role_ids=[],
        )
        self.sessions: dict[tuple[int, int, int], Session] = {}
        self._nodes: dict[str, Callable[[Session, _Target], Any]] = {
            N.START: self._node_start,
            N.ERROR_SELECT: self._node_error_select,
            N.STEAM_CHECK: self._node_steam_check,
            N.STEAM_INSTALL_STEPS: self._node_steam_install_steps,
            N.RUN_GAME: self._node_run_game,
            N.BITDEFENDER_CHECK: self._node_bitdefender_check,
            N.MINUS_BITDEFENDER: self._node_minus_bitdefender,
            N.COLDCLIENT_CHECK: self._node_coldclient_check,
            N.MINUS_PCL: self._node_minus_pcl,
            N.UE_GAME_CHECK: self._node_ue_game_check,
            N.UE_GUIDE: self._node_ue_guide,
            N.NORMAL_GUIDE: self._node_normal_guide,
            N.WAIT_FOR_TXTS: self._node_wait_for_txts,
            N.TXT_PARSED: self._node_txt_parsed,
            N.GPU_QUESTION: self._node_gpu_question,
            N.CPU_AVX2_QUESTION: self._node_cpu_avx2_question,
            N.VERIFY_GAME: self._node_verify_game,
            N.APPLY_TOKEN: self._node_apply_token,
            N.ERROR_AFTER_TOKEN: self._node_error_after_token,
            N.SCREENSHOT_ERROR: self._node_screenshot_error,
            N.ANTI_TAMPER_SUBCODE: self._node_anti_tamper_subcode,
            N.HYPERVISOR: self._node_hypervisor,
            N.UE_CRASH: self._node_ue_crash,
            N.NO_LICENSE: self._node_no_license,
            N.STEAMAPI: self._node_steamapi,
            N.ISTEAMUSER: self._node_isteamuser,
            N.CIRNO_SANC: self._node_cirno_sanc,
            N.UNABLE_TO_IDENTIFY: self._node_unable_to_identify,
            N.MISC_BRANCH: self._node_misc_branch,
            N.SAVE_GONE: self._node_save_gone,
            N.AUTO_SAVE: self._node_auto_save,
            N.DESCRIBE_DIFF: self._node_describe_diff,
            N.STAFF_HANDOFF: self._node_staff_handoff,
            N.FINISHED: self._node_finished,
        }

    def _session_key(self, interaction_or_message: Any) -> Optional[tuple[int, int, int]]:
        guild = getattr(interaction_or_message, "guild", None)
        channel = getattr(interaction_or_message, "channel", None)
        user = getattr(interaction_or_message, "user", None) or getattr(interaction_or_message, "author", None)
        if guild is None or channel is None or user is None:
            return None
        return guild.id, channel.id, user.id

    def _get_session(self, guild_id: int, channel_id: int, user_id: int) -> Optional[Session]:
        return self.sessions.get((guild_id, channel_id, user_id))

    def _set_session(self, session: Session) -> None:
        self.sessions[(session.guild_id, session.channel_id, session.user_id)] = session

    def _clear_session(self, guild_id: int, channel_id: int, user_id: int) -> None:
        self.sessions.pop((guild_id, channel_id, user_id), None)

    async def _is_editor(self, member: discord.Member) -> bool:
        try:
            if member is None:
                return False
            if getattr(member.guild_permissions, "manage_guild", False):
                return True
            try:
                if await self.bot.is_owner(member):
                    return True
            except Exception:
                log.exception("is_owner check failed")
            if member.guild is None:
                return False
            editor_ids = await self.config.guild(member.guild).editor_role_ids()
            editor_set = set(editor_ids or [])
            return any(role.id in editor_set for role in member.roles)
        except Exception:
            log.exception("_is_editor failed")
            return False

    async def _render_tag(self, guild: Optional[discord.Guild], key: str) -> str:
        default = DEFAULT_TAGS.get(key, "")
        if guild is None:
            return default
        try:
            tags = await self.config.guild(guild).tags()
            if isinstance(tags, dict):
                value = tags.get(key)
                if value:
                    return value
            return default
        except Exception:
            log.exception("tag render failed for %s", key)
            return default

    def _session_guild(self, session: Session) -> Optional[discord.Guild]:
        return self.bot.get_guild(session.guild_id)

    async def _advance(
        self,
        session: Session,
        interaction: Optional[discord.Interaction] = None,
        channel: Optional[discord.abc.Messageable] = None,
    ) -> None:
        target_channel = channel or interaction.channel if interaction else channel
        if target_channel is None:
            return
        handler = self._nodes.get(session.node)
        if handler is None:
            log.warning("No handler for node %s", session.node)
            return
        await handler(session, _Target(target_channel, interaction.response if interaction else None))

    async def _send_with_view(
        self, target: _Target, content: str, view: Optional[BaseWizardView] = None, embed: Optional[discord.Embed] = None
    ) -> None:
        message = await target.send(content=content, embed=embed, view=view)
        if view is not None and message is not None:
            view.message = message

    async def _scan_recent_uploads(self, session: Session, channel: discord.abc.Messageable) -> str:
        found: list[str] = []
        if not isinstance(channel, discord.TextChannel):
            return "Channel does not support history scan."
        try:
            async for message in channel.history(limit=25):
                for attachment in message.attachments:
                    name = attachment.filename.lower()
                    try:
                        data = await attachment.read()
                        text = data.decode("utf-8", errors="replace")
                    except Exception:
                        log.exception("Failed reading uploaded attachment during scan")
                        continue
                    if "pub_dep" in name and name.endswith(".txt"):
                        session.parsed_dep = parse_pub_dep(text)
                        found.append("pub_dep.txt")
                    if "pub_crash" in name and name.endswith(".txt"):
                        session.parsed_crash = parse_pub_crash(text)
                        found.append("pub_crash.txt")
        except Exception:
            log.exception("Failed scanning channel history for uploads")
            return "History scan failed; proceeding with any data already captured."
        return f"Found: {', '.join(found)}" if found else "No matching txt files found; continuing anyway."

    @app_commands.command(name="denuvoauto", description="Start the Denuvo manual-activation support wizard.")
    async def denuvoauto_slash(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None or interaction.channel is None or interaction.user is None:
            if not interaction.response.is_done():
                await interaction.response.send_message("This command must be used in a server channel.", ephemeral=True)
            return
        session = Session(guild_id=interaction.guild.id, channel_id=interaction.channel.id, user_id=interaction.user.id)
        self._set_session(session)
        await self._advance(session, interaction=interaction)

    @commands.Cog.listener("on_message")
    async def on_message(self, message: discord.Message) -> None:
        if message.guild is None or message.author.bot or not message.attachments:
            return
        session = self._get_session(message.guild.id, message.channel.id, message.author.id)
        if session is None or session.node != N.WAIT_FOR_TXTS:
            return
        for attachment in message.attachments:
            name = attachment.filename.lower()
            try:
                data = await attachment.read()
                text = data.decode("utf-8", errors="replace")
            except Exception:
                log.exception("Failed to read attachment from user upload")
                continue
            if "pub_dep" in name and name.endswith(".txt"):
                session.parsed_dep = parse_pub_dep(text)
                session.answers["pub_dep_uploaded"] = True
            elif "pub_crash" in name and name.endswith(".txt"):
                session.parsed_crash = parse_pub_crash(text)
                session.answers["pub_crash_uploaded"] = True
        if session.parsed_dep is not None and session.parsed_crash is not None:
            session.node = N.TXT_PARSED
            await self._advance(session, channel=message.channel)

    @commands.group(name="denuvoautoset")
    @commands.admin_or_permissions(manage_guild=True)
    @commands.guild_only()
    async def denuvoautoset(self, ctx: commands.Context) -> None:
        if ctx.invoked_subcommand is None:
            await ctx.send("Use a subcommand: staffrole, staffchannel, logchannel, show, reset.")

    @denuvoautoset.command(name="staffrole")
    async def denuvoautoset_staffrole(self, ctx: commands.Context, role: discord.Role) -> None:
        await self.config.guild(ctx.guild).staff_role_id.set(role.id)
        await ctx.send(f"Staff role set to {role.mention}.")

    @denuvoautoset.command(name="staffchannel")
    async def denuvoautoset_staffchannel(self, ctx: commands.Context, channel: discord.TextChannel) -> None:
        await self.config.guild(ctx.guild).staff_channel_id.set(channel.id)
        await ctx.send(f"Staff channel set to {channel.mention}.")

    @denuvoautoset.command(name="logchannel")
    async def denuvoautoset_logchannel(self, ctx: commands.Context, channel: discord.TextChannel) -> None:
        await self.config.guild(ctx.guild).log_channel_id.set(channel.id)
        await ctx.send(f"Log channel set to {channel.mention}.")

    @denuvoautoset.command(name="show")
    async def denuvoautoset_show(self, ctx: commands.Context) -> None:
        conf = await self.config.guild(ctx.guild).all()
        embed = discord.Embed(title="Denuvoauto settings")
        embed.add_field(name="staff_role_id", value=str(conf["staff_role_id"]), inline=False)
        embed.add_field(name="staff_channel_id", value=str(conf["staff_channel_id"]), inline=False)
        embed.add_field(name="log_channel_id", value=str(conf["log_channel_id"]), inline=False)
        await ctx.send(embed=embed)

    @denuvoautoset.command(name="reset")
    async def denuvoautoset_reset(self, ctx: commands.Context) -> None:
        self.sessions = {k: v for k, v in self.sessions.items() if k[0] != ctx.guild.id}
        await ctx.send("Cleared active Denuvoauto sessions for this guild.")

    @denuvoautoset.group(name="tags")
    @_editor_check()
    @commands.guild_only()
    async def denuvoautoset_tags(self, ctx: commands.Context) -> None:
        if ctx.invoked_subcommand is None:
            await ctx.send("Use a subcommand: set, get, clear, list, reset.")

    @denuvoautoset_tags.command(name="set")
    async def denuvoautoset_tags_set(self, ctx: commands.Context, key: str, *, value: str) -> None:
        if key not in DEFAULT_TAGS:
            await ctx.send(f"Unknown tag key: `{key}`. Use `{ctx.clean_prefix}denuvoautoset tags list`.")
            return
        if len(value) > 2000:
            await ctx.send("Tag value too long (max 2000 characters).")
            return
        async with self.config.guild(ctx.guild).tags() as tags:
            tags[key] = value
        await ctx.send(f"Tag `{key}` updated.")

    @denuvoautoset_tags.command(name="get")
    async def denuvoautoset_tags_get(self, ctx: commands.Context, key: str) -> None:
        if key not in DEFAULT_TAGS:
            await ctx.send(f"Unknown tag key: `{key}`.")
            return
        tags = await self.config.guild(ctx.guild).tags()
        value = tags.get(key) if isinstance(tags, dict) else None
        if value:
            await ctx.send(f"`{key}` override:\n```\n{value[:1800]}\n```")
        else:
            await ctx.send(f"`{key}` is unset; falls back to default:\n```\n{DEFAULT_TAGS[key][:1800]}\n```")

    @denuvoautoset_tags.command(name="clear")
    async def denuvoautoset_tags_clear(self, ctx: commands.Context, key: str) -> None:
        if key not in DEFAULT_TAGS:
            await ctx.send(f"Unknown tag key: `{key}`.")
            return
        async with self.config.guild(ctx.guild).tags() as tags:
            tags.pop(key, None)
        await ctx.send(f"Tag `{key}` cleared. Default text will be used.")

    @denuvoautoset_tags.command(name="list")
    async def denuvoautoset_tags_list(self, ctx: commands.Context) -> None:
        tags = await self.config.guild(ctx.guild).tags() or {}
        embed = discord.Embed(title="Denuvoauto tag overrides")
        lines = []
        for key in DEFAULT_TAGS:
            gate = " (MANUAL GATE)" if key in MANUAL_GATE_TAG_KEYS else ""
            status = "set" if (isinstance(tags, dict) and tags.get(key)) else "default"
            lines.append(f"`{key}`{gate} — {status}")
        embed.description = "\n".join(lines)[:4000]
        await ctx.send(embed=embed)

    @denuvoautoset_tags.command(name="reset")
    async def denuvoautoset_tags_reset(self, ctx: commands.Context) -> None:
        await self.config.guild(ctx.guild).tags.set({})
        await ctx.send("All tag overrides cleared.")

    @commands.group(name="denuvoautoowner")
    @commands.is_owner()
    @commands.guild_only()
    async def denuvoautoowner(self, ctx: commands.Context) -> None:
        if ctx.invoked_subcommand is None:
            await ctx.send("Use a subcommand: addeditor, removeeditor, showeditors.")

    @denuvoautoowner.command(name="addeditor")
    async def denuvoautoowner_addeditor(self, ctx: commands.Context, role: discord.Role) -> None:
        async with self.config.guild(ctx.guild).editor_role_ids() as ids:
            if role.id not in ids:
                ids.append(role.id)
        await ctx.send(
            f"Added {role.mention} to Denuvoauto editor roles.",
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @denuvoautoowner.command(name="removeeditor")
    async def denuvoautoowner_removeeditor(self, ctx: commands.Context, role: discord.Role) -> None:
        async with self.config.guild(ctx.guild).editor_role_ids() as ids:
            try:
                ids.remove(role.id)
            except ValueError:
                pass
        await ctx.send(
            f"Removed {role.mention} from Denuvoauto editor roles.",
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @denuvoautoowner.command(name="showeditors")
    async def denuvoautoowner_showeditors(self, ctx: commands.Context) -> None:
        ids = await self.config.guild(ctx.guild).editor_role_ids()
        if not ids:
            await ctx.send(
                "No editor roles configured. Only admins (manage_guild) and the bot owner can edit tags."
            )
            return
        mentions = []
        for rid in ids:
            role = ctx.guild.get_role(rid)
            mentions.append(role.mention if role else f"`{rid}` (missing)")
        await ctx.send(
            "Editor roles: " + ", ".join(mentions),
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @commands.command(name="denuvoautosetup")
    @_editor_check()
    @commands.guild_only()
    async def denuvoautosetup(self, ctx: commands.Context) -> None:
        keys = list(DEFAULT_TAGS.keys())
        view = SetupWizardView(self, ctx.guild, ctx.author.id, keys)
        embed = await view.build_step_embed()
        msg = await ctx.send(embed=embed, view=view)
        view.message = msg

    async def _node_start(self, session: Session, target: _Target) -> None:
        session.node = N.ERROR_SELECT
        await self._advance(session, channel=target.channel)

    async def _node_error_select(self, session: Session, target: _Target) -> None:
        view = ErrorSelectView(self, session)
        await self._send_with_view(target, "What best matches the current issue?", view=view)

    async def _node_steam_check(self, session: Session, target: _Target) -> None:
        view = YesNoView(self, session, N.RUN_GAME, N.STEAM_INSTALL_STEPS)
        await self._send_with_view(target, "Is Steam installed and available on this system?", view=view)

    async def _node_steam_install_steps(self, session: Session, target: _Target) -> None:
        session.answers["steam_steps"] = "Asked user to ensure Steam is installed before retrying."
        session.node = N.RUN_GAME
        text = await self._render_tag(self._session_guild(session), "steam_install_steps")
        await target.send(content=text)
        await self._advance(session, channel=target.channel)

    async def _node_run_game(self, session: Session, target: _Target) -> None:
        view = YesNoView(self, session, N.BITDEFENDER_CHECK, N.SCREENSHOT_ERROR)
        text = await self._render_tag(self._session_guild(session), "run_game")
        await self._send_with_view(target, text, view=view)

    async def _node_bitdefender_check(self, session: Session, target: _Target) -> None:
        view = YesNoView(self, session, N.MINUS_BITDEFENDER, N.COLDCLIENT_CHECK)
        await self._send_with_view(target, "Is Bitdefender involved on this machine?", view=view)

    async def _node_minus_bitdefender(self, session: Session, target: _Target) -> None:
        session.answers["bitdefender"] = "present"
        session.node = N.COLDCLIENT_CHECK
        text = await self._render_tag(self._session_guild(session), "minus_bitdefender")
        await target.send(content=text)
        await self._advance(session, channel=target.channel)

    async def _node_coldclient_check(self, session: Session, target: _Target) -> None:
        # MANUAL STAFF GATE — do not automate DRM/token steps
        msg = await self._render_tag(self._session_guild(session), "coldclient_check")
        await target.send(content=msg)
        session.node = N.STAFF_HANDOFF
        await self._advance(session, channel=target.channel)

    async def _node_minus_pcl(self, session: Session, target: _Target) -> None:
        session.node = N.UE_GAME_CHECK
        text = await self._render_tag(self._session_guild(session), "minus_pcl")
        await target.send(content=text)
        await self._advance(session, channel=target.channel)

    async def _node_ue_game_check(self, session: Session, target: _Target) -> None:
        view = YesNoView(self, session, N.UE_GUIDE, N.NORMAL_GUIDE)
        await self._send_with_view(target, "Is this an Unreal Engine game / UE-style crash context?", view=view)

    async def _node_ue_guide(self, session: Session, target: _Target) -> None:
        session.node = N.WAIT_FOR_TXTS
        text = await self._render_tag(self._session_guild(session), "ue_guide")
        await target.send(content=text)
        await self._advance(session, channel=target.channel)

    async def _node_normal_guide(self, session: Session, target: _Target) -> None:
        session.node = N.WAIT_FOR_TXTS
        text = await self._render_tag(self._session_guild(session), "normal_guide")
        await target.send(content=text)
        await self._advance(session, channel=target.channel)

    async def _node_wait_for_txts(self, session: Session, target: _Target) -> None:
        view = UploadedView(self, session)
        text = await self._render_tag(self._session_guild(session), "wait_for_txts")
        await self._send_with_view(
            target,
            text,
            view=view,
        )

    async def _node_txt_parsed(self, session: Session, target: _Target) -> None:
        dep_missing = bool(session.parsed_dep and session.parsed_dep.get("missing"))
        crash_low = bool(session.parsed_crash and session.parsed_crash.get("memory_low"))
        next_node = N.VERIFY_GAME if dep_missing else N.GPU_QUESTION
        if crash_low:
            next_node = N.CPU_AVX2_QUESTION
        session.node = next_node
        await target.send(content="Thanks — I parsed what I could from the text files. Continuing with follow-up checks.")
        await self._advance(session, channel=target.channel)

    async def _node_gpu_question(self, session: Session, target: _Target) -> None:
        view = YesNoView(self, session, N.CPU_AVX2_QUESTION, N.VERIFY_GAME)
        await self._send_with_view(target, "Are you using the expected GPU / graphics path for this game?", view=view)

    async def _node_cpu_avx2_question(self, session: Session, target: _Target) -> None:
        view = YesNoView(self, session, N.VERIFY_GAME, N.STAFF_HANDOFF)
        await self._send_with_view(target, "Does the CPU support AVX2, as far as you know?", view=view)

    async def _node_verify_game(self, session: Session, target: _Target) -> None:
        view = YesNoView(self, session, N.APPLY_TOKEN, N.DESCRIBE_DIFF)
        text = await self._render_tag(self._session_guild(session), "verify_game")
        await self._send_with_view(target, text, view=view)

    async def _node_apply_token(self, session: Session, target: _Target) -> None:
        # MANUAL STAFF GATE — do not automate DRM/token steps
        msg = await self._render_tag(self._session_guild(session), "apply_token")
        await target.send(content=msg)
        session.node = N.STAFF_HANDOFF
        await self._advance(session, channel=target.channel)

    async def _node_error_after_token(self, session: Session, target: _Target) -> None:
        session.node = N.SCREENSHOT_ERROR
        await target.send(content="If an error appears after staff review, please capture the exact message or closest match.")
        await self._advance(session, channel=target.channel)

    async def _node_screenshot_error(self, session: Session, target: _Target) -> None:
        session.node = N.ERROR_SELECT
        await target.send(content="Please identify the visible error from the selector so staff get the right context.")
        await self._advance(session, channel=target.channel)

    async def _node_anti_tamper_subcode(self, session: Session, target: _Target) -> None:
        guild = self._session_guild(session)
        if "anti_tamper_subcode" not in session.answers:
            view = AntiTamperSelectView(self, session)
            prompt = await self._render_tag(guild, "anti_tamper_prompt")
            await self._send_with_view(target, prompt, view=view)
            return
        # MANUAL STAFF GATE — do not automate DRM/token steps
        msg = await self._render_tag(guild, "anti_tamper_wait")
        await target.send(content=msg)
        session.node = N.STAFF_HANDOFF
        await self._advance(session, channel=target.channel)

    async def _node_hypervisor(self, session: Session, target: _Target) -> None:
        # MANUAL STAFF GATE — do not automate DRM/token steps
        msg = await self._render_tag(self._session_guild(session), "hypervisor")
        await target.send(content=msg)
        session.node = N.STAFF_HANDOFF
        await self._advance(session, channel=target.channel)

    async def _node_ue_crash(self, session: Session, target: _Target) -> None:
        session.node = N.UE_GAME_CHECK
        await target.send(content="UE crash branch selected. I’ll ask a couple of engine-related questions next.")
        await self._advance(session, channel=target.channel)

    async def _node_no_license(self, session: Session, target: _Target) -> None:
        session.node = N.STEAM_CHECK
        await target.send(content="No-license branch selected. Let’s confirm Steam and launch context first.")
        await self._advance(session, channel=target.channel)

    async def _node_steamapi(self, session: Session, target: _Target) -> None:
        session.node = N.STEAM_CHECK
        await target.send(content="SteamAPI branch selected. We’ll confirm Steam presence and then continue.")
        await self._advance(session, channel=target.channel)

    async def _node_isteamuser(self, session: Session, target: _Target) -> None:
        session.node = N.STEAM_CHECK
        await target.send(content="ISteamUser branch selected. We’ll confirm Steam context first.")
        await self._advance(session, channel=target.channel)

    async def _node_cirno_sanc(self, session: Session, target: _Target) -> None:
        choices = [("Save is gone", N.SAVE_GONE), ("Autosave issue", N.AUTO_SAVE), ("Something else", N.DESCRIBE_DIFF)]
        view = ChoiceView(self, session, choices)
        await self._send_with_view(target, "Cirno/Sanc branch selected. What best matches the issue?", view=view)

    async def _node_unable_to_identify(self, session: Session, target: _Target) -> None:
        session.node = N.DESCRIBE_DIFF
        await target.send(content="No problem — give staff the clearest difference you can describe.")
        await self._advance(session, channel=target.channel)

    async def _node_misc_branch(self, session: Session, target: _Target) -> None:
        session.node = N.STEAM_CHECK
        await target.send(content="Starting with the general branch. We’ll check Steam and launch flow first.")
        await self._advance(session, channel=target.channel)

    async def _node_save_gone(self, session: Session, target: _Target) -> None:
        session.answers["save_issue"] = "save_gone"
        session.node = N.STAFF_HANDOFF
        text = await self._render_tag(self._session_guild(session), "save_gone")
        await target.send(content=text)
        await self._advance(session, channel=target.channel)

    async def _node_auto_save(self, session: Session, target: _Target) -> None:
        session.answers["save_issue"] = "auto_save"
        session.node = N.STAFF_HANDOFF
        text = await self._render_tag(self._session_guild(session), "auto_save")
        await target.send(content=text)
        await self._advance(session, channel=target.channel)

    async def _node_describe_diff(self, session: Session, target: _Target) -> None:
        session.answers.setdefault("describe_diff", "User needs manual follow-up for details.")
        session.node = N.STAFF_HANDOFF
        text = await self._render_tag(self._session_guild(session), "describe_diff")
        await target.send(content=text)
        await self._advance(session, channel=target.channel)

    async def _node_staff_handoff(self, session: Session, target: _Target) -> None:
        guild = self.bot.get_guild(session.guild_id)
        channel = guild.get_channel(session.channel_id) if guild else target.channel
        embed = self._build_report_embed(session)
        if guild is not None and channel is not None:
            await self._ping_staff(guild, channel, embed)
        session.node = N.FINISHED
        await self._advance(session, channel=target.channel)

    async def _node_finished(self, session: Session, target: _Target) -> None:
        await target.send(content="Wizard complete — staff have been notified if configuration is set.")
        self._clear_session(session.guild_id, session.channel_id, session.user_id)

    def _build_report_embed(self, session: Session) -> discord.Embed:
        embed = discord.Embed(title="Denuvoauto session report")
        user_mention = f"<@{session.user_id}>"
        jump = f"https://discord.com/channels/{session.guild_id}/{session.channel_id}"
        embed.add_field(name="User", value=f"{user_mention} (`{session.user_id}`)\n[Channel jump]({jump})", inline=False)
        embed.add_field(name="Final node", value=session.node, inline=False)
        answers = "\n".join(f"{k}={v}" for k, v in session.answers.items())[:1000] or "None"
        embed.add_field(name="Collected answers", value=answers, inline=False)
        dep = session.parsed_dep or {}
        crash = session.parsed_crash or {}
        embed.add_field(
            name="pub_dep summary",
            value=(
                f"installed={len(dep.get('installed', []))}\n"
                f"missing={len(dep.get('missing', []))}\n"
                f"steam_present={dep.get('steam_present', False)}"
            ),
            inline=False,
        )
        embed.add_field(
            name="pub_crash summary",
            value=(
                f"pubcrashlogger={crash.get('pubcrashlogger', False)}\n"
                f"memory_gb={crash.get('memory_gb')}\n"
                f"memory_low={crash.get('memory_low', False)}"
            ),
            inline=False,
        )
        dep_raw = (dep.get("raw_summary") or "")[:400]
        crash_raw = (crash.get("raw_summary") or "")[:400]
        if dep_raw:
            embed.add_field(name="pub_dep excerpt", value=f"```\n{dep_raw}\n```"[:1024], inline=False)
        if crash_raw:
            embed.add_field(name="pub_crash excerpt", value=f"```\n{crash_raw}\n```"[:1024], inline=False)
        return embed

    async def _ping_staff(
        self,
        guild: discord.Guild,
        fallback_channel: discord.abc.Messageable,
        embed: discord.Embed,
    ) -> None:
        conf = await self.config.guild(guild).all()
        staff_channel = guild.get_channel(conf["staff_channel_id"]) if conf["staff_channel_id"] else fallback_channel
        log_channel = guild.get_channel(conf["log_channel_id"]) if conf["log_channel_id"] else None
        role = guild.get_role(conf["staff_role_id"]) if conf["staff_role_id"] else None
        mention = role.mention if role is not None else "Staff review needed"
        allowed = discord.AllowedMentions(roles=True)
        try:
            await staff_channel.send(content=mention, embed=embed, allowed_mentions=allowed)
        except Exception:
            log.exception("Failed to send staff handoff report")
        if log_channel is not None and getattr(staff_channel, "id", None) != log_channel.id:
            try:
                await log_channel.send(content="Denuvoauto log copy", embed=embed)
            except Exception:
                log.exception("Failed to send log channel report")
