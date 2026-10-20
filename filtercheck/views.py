"""Interactive Discord UI views and embed generators for FilterCheck."""

from __future__ import annotations

import discord
from redbot.core import commands

from .checker import DomainCheckReport, FilterResult


def build_school_embed(report: DomainCheckReport) -> discord.Embed:
    """Build the exact 3-column school filter embed matching the reference design."""
    embed = discord.Embed(
        title="🔍 Web Filter Results",
        color=0x2B2D42 if report.school_blocked_count > 0 else 0x2ECC71,
    )

    # Header description with counts
    header = (
        f"**Domain:** `{report.domain}`\n\n"
        f"🚫 **Blocked by:** {report.school_blocked_count}\n"
        f"✅ **Allowed by:** {report.school_allowed_count}"
    )
    embed.description = header

    # Split 20 filters across 3 inline columns (7, 7, 6)
    filters = report.school_filters
    col1 = filters[:7]
    col2 = filters[7:14]
    col3 = filters[14:]

    def format_col(items: list[FilterResult]) -> str:
        lines = []
        for f in items:
            lines.append(f"{f.icon} **{f.provider}**\n{f.category}")
        return "\n\n".join(lines) if lines else "—"

    embed.add_field(name="\u200b", value=format_col(col1), inline=True)
    embed.add_field(name="\u200b", value=format_col(col2), inline=True)
    embed.add_field(name="\u200b", value=format_col(col3), inline=True)

    embed.set_footer(
        text=f"credit to @regentstew • Checked 20 school filters in {report.elapsed_seconds:.2f}s"
    )
    return embed


def build_dns_embed(report: DomainCheckReport) -> discord.Embed:
    """Build embed detailing verdicts from major secure and family DNS resolvers."""
    embed = discord.Embed(
        title="🛡️ DNS Security & Family Resolvers",
        description=(
            f"**Domain:** `{report.domain}`\n\n"
            f"🚫 **Blocked by:** {report.dns_blocked_count} resolvers\n"
            f"✅ **Allowed by:** {report.dns_allowed_count} resolvers\n\n"
            "Evaluates threat sinkholing, adult filters, and DNS poisoning."
        ),
        color=0x3498DB,
    )

    lines = []
    for r in report.dns_resolvers:
        detail_note = f" — *{r.detail}*" if r.detail else ""
        lines.append(f"{r.icon} **{r.provider}** (`{r.category}`){detail_note}")

    chunks = []
    chunk = ""
    for line in lines:
        if len(chunk) + len(line) > 950:
            chunks.append(chunk)
            chunk = line + "\n"
        else:
            chunk += line + "\n"
    if chunk:
        chunks.append(chunk)

    for i, c in enumerate(chunks):
        name = "Resolver Status" if i == 0 else "Resolvers (cont.)"
        embed.add_field(name=name, value=c, inline=False)

    embed.set_footer(
        text=f"Checked {len(report.dns_resolvers)} global DNS resolvers in {report.elapsed_seconds:.2f}s"
    )
    return embed


def build_country_embed(report: DomainCheckReport) -> discord.Embed:
    """Build embed detailing country-level and ISP censorship measurements."""
    embed = discord.Embed(
        title="🌍 Country & ISP Censorship Checks",
        description=(
            f"**Domain:** `{report.domain}`\n\n"
            f"🚫 **Restricted in:** {report.country_blocked_count} regions\n"
            f"✅ **Accessible in:** {report.country_allowed_count} regions\n\n"
            "Live ISP measurements via OONI probes & national regulatory blocklists."
        ),
        color=0xE67E22 if report.country_blocked_count > 0 else 0x2ECC71,
    )

    lines = []
    for c in report.country_blocks:
        lines.append(
            f"{c.icon} {c.flag} **{c.country_name}** ({c.country_code})\n"
            f"└ *{c.status_text}* • `{c.probe_source}`"
        )

    chunks = []
    chunk = ""
    for line in lines:
        if len(chunk) + len(line) > 950:
            chunks.append(chunk)
            chunk = line + "\n\n"
        else:
            chunk += line + "\n\n"
    if chunk:
        chunks.append(chunk)

    for i, ch in enumerate(chunks):
        name = "Regional ISP Status" if i == 0 else "Regional Status (cont.)"
        embed.add_field(name=name, value=ch, inline=False)

    embed.set_footer(
        text=f"OONI & National Registry Data • Checked in {report.elapsed_seconds:.2f}s"
    )
    return embed


def build_overview_embed(report: DomainCheckReport) -> discord.Embed:
    """Build high-level executive summary across school, DNS, and ISP layers."""
    total_checks = (
        len(report.school_filters) + len(report.dns_resolvers) + len(report.country_blocks)
    )
    total_blocked = (
        report.school_blocked_count + report.dns_blocked_count + report.country_blocked_count
    )
    total_allowed = (
        report.school_allowed_count + report.dns_allowed_count + report.country_allowed_count
    )

    embed = discord.Embed(
        title="📊 Complete Domain Inspection Overview",
        description=f"Full multi-tier analysis for **`{report.domain}`**",
        color=0xE74C3C if total_blocked > 5 else 0x2ECC71,
    )

    embed.add_field(
        name="🏫 School & Enterprise Filters",
        value=(
            f"• **Blocked:** {report.school_blocked_count} / {len(report.school_filters)}\n"
            f"• **Allowed:** {report.school_allowed_count} / {len(report.school_filters)}\n"
            f"• Primary Verdict: {'🚫 High Risk / Restricted' if report.school_blocked_count >= 10 else '✅ Mostly Allowed'}"
        ),
        inline=False,
    )

    embed.add_field(
        name="🛡️ DNS Security & Family Resolvers",
        value=(
            f"• **Blocked:** {report.dns_blocked_count} / {len(report.dns_resolvers)}\n"
            f"• **Allowed:** {report.dns_allowed_count} / {len(report.dns_resolvers)}\n"
            f"• Primary Verdict: {'🚫 Threat / Sinkhole Detected' if report.dns_blocked_count > 0 else '✅ Clean Resolution'}"
        ),
        inline=False,
    )

    embed.add_field(
        name="🌍 National ISP Censorship",
        value=(
            f"• **Blocked:** {report.country_blocked_count} / {len(report.country_blocks)}\n"
            f"• **Accessible:** {report.country_allowed_count} / {len(report.country_blocks)}\n"
            f"• Primary Verdict: {'🚫 Government / ISP Blocked' if report.country_blocked_count > 0 else '✅ Globally Accessible'}"
        ),
        inline=False,
    )

    embed.add_field(
        name="📈 Combined Metrics",
        value=f"**{total_blocked}** Blocked • **{total_allowed}** Allowed • Tested **{total_checks}** engines in **{report.elapsed_seconds:.2f}s**",
        inline=False,
    )

    embed.set_footer(
        text="Click the buttons below to browse detailed reports per category."
    )
    return embed


class FilterCheckView(discord.ui.View):
    """Interactive navigation view toggling between School, DNS, Country, and Overview embeds."""

    def __init__(self, report: DomainCheckReport, author_id: int, timeout: float = 180.0) -> None:
        super().__init__(timeout=timeout)
        self.report = report
        self.author_id = author_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "You can run your own check with `/check domain:`!", ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="School Filters", style=discord.ButtonStyle.primary, emoji="🏫", row=0)
    async def btn_school(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        embed = build_school_embed(self.report)
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="DNS Resolvers", style=discord.ButtonStyle.secondary, emoji="🛡️", row=0)
    async def btn_dns(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        embed = build_dns_embed(self.report)
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Country & ISP", style=discord.ButtonStyle.secondary, emoji="🌍", row=0)
    async def btn_country(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        embed = build_country_embed(self.report)
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Overview", style=discord.ButtonStyle.success, emoji="📊", row=0)
    async def btn_overview(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        embed = build_overview_embed(self.report)
        await interaction.response.edit_message(embed=embed, view=self)
