"""Comprehensive unit and integration tests for FilterCheck cog."""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from filtercheck.checker import (
    CountryBlockResult,
    DomainCheckReport,
    FilterCheckerEngine,
    FilterResult,
    sanitize_domain,
)
from filtercheck.views import (
    FilterCheckView,
    build_country_embed,
    build_dns_embed,
    build_overview_embed,
    build_school_embed,
)


def test_sanitize_domain():
    assert sanitize_domain("pubslounge.xyz") == "pubslounge.xyz"
    assert sanitize_domain("https://pubslounge.xyz") == "pubslounge.xyz"
    assert sanitize_domain("http://pubslounge.xyz/api/test?arg=1#top") == "pubslounge.xyz"
    assert sanitize_domain("  HTTP://GOOGLE.COM:8080/path/  ") == "google.com"
    assert sanitize_domain("discord.gg") == "discord.gg"
    assert sanitize_domain("") == ""


def test_domain_check_report_counts():
    report = DomainCheckReport(
        domain="example.com",
        school_filters=[
            FilterResult("FortiGuard", "Malicious", True),
            FilterResult("Lightspeed", "Unknown", True),
            FilterResult("Linewize", "Reference", False),
            FilterResult("LanSchool", "Allowed", False),
        ],
        dns_resolvers=[
            FilterResult("Cloudflare Sec", "Malware", False),
            FilterResult("Quad9", "Threat", True),
        ],
        country_blocks=[
            CountryBlockResult("CN", "China", "🇨🇳", True, "Blocked", "GFW"),
            CountryBlockResult("GB", "UK", "🇬🇧", False, "Allowed", "Cleanfeed"),
        ],
        elapsed_seconds=1.25,
    )

    assert report.school_blocked_count == 2
    assert report.school_allowed_count == 2
    assert report.dns_blocked_count == 1
    assert report.dns_allowed_count == 1
    assert report.country_blocked_count == 1
    assert report.country_allowed_count == 1


def test_build_school_embed():
    report = DomainCheckReport(
        domain="pubslounge.xyz",
        school_filters=[
            FilterResult("FortiGuard", "Malicious Websites", True),
            FilterResult("Lightspeed", "Unknown", True),
            FilterResult("Linewize", "Reference", False),
            FilterResult("LanSchool", "Allowed", False),
            FilterResult("NextDNS", "NextDNS", False),
        ],
        elapsed_seconds=0.88,
    )
    embed = build_school_embed(report)

    assert embed.title == "🔍 Web Filter Results"
    assert "pubslounge.xyz" in embed.description
    assert "**Blocked by:** 2" in embed.description
    assert "**Allowed by:** 3" in embed.description
    assert len(embed.fields) == 3
    assert "@regentstew" in embed.footer.text


def test_build_dns_embed():
    report = DomainCheckReport(
        domain="pubslounge.xyz",
        dns_resolvers=[
            FilterResult("Cloudflare Security", "Malware Block", False, detail="Resolved (172.64.80.1)"),
            FilterResult("Quad9", "Threat Block", False, detail="Resolved (172.64.80.1)"),
        ],
        elapsed_seconds=0.45,
    )
    embed = build_dns_embed(report)

    assert "DNS Security" in embed.title
    assert "pubslounge.xyz" in embed.description
    assert len(embed.fields) >= 1


def test_build_country_embed():
    report = DomainCheckReport(
        domain="pubslounge.xyz",
        country_blocks=[
            CountryBlockResult("CN", "China", "🇨🇳", True, "GFW DNS Blocked", "Great Firewall"),
            CountryBlockResult("RU", "Russia", "🇷🇺", False, "No block", "RKN"),
        ],
        elapsed_seconds=0.62,
    )
    embed = build_country_embed(report)

    assert "Country & ISP Censorship" in embed.title
    assert "**Restricted in:** 1" in embed.description
    assert "**Accessible in:** 1" in embed.description
    assert len(embed.fields) >= 1


def test_build_overview_embed():
    report = DomainCheckReport(
        domain="pubslounge.xyz",
        school_filters=[FilterResult("FortiGuard", "Malicious", True)],
        dns_resolvers=[FilterResult("Cloudflare", "Clean", False)],
        country_blocks=[CountryBlockResult("CN", "China", "🇨🇳", False, "Clean", "GFW")],
        elapsed_seconds=1.12,
    )
    embed = build_overview_embed(report)

    assert "Complete Domain Inspection Overview" in embed.title
    assert "pubslounge.xyz" in embed.description
    assert len(embed.fields) == 4


@pytest.mark.asyncio
async def test_view_interaction_check():
    report = DomainCheckReport(domain="test.com")
    view = FilterCheckView(report=report, author_id=12345)

    interaction_author = MagicMock()
    interaction_author.user.id = 12345
    assert await view.interaction_check(interaction_author) is True

    interaction_other = MagicMock()
    interaction_other.user.id = 99999
    interaction_other.response.send_message = AsyncMock()
    assert await view.interaction_check(interaction_other) is False
    interaction_other.response.send_message.assert_called_once()


@pytest.mark.asyncio
async def test_engine_live_run():
    engine = FilterCheckerEngine()
    try:
        report = await engine.check_all("pubslounge.xyz")
        assert report.domain == "pubslounge.xyz"
        assert len(report.school_filters) == 20
        assert report.school_blocked_count >= 10
        assert report.school_allowed_count >= 1
        assert len(report.dns_resolvers) >= 10
        assert len(report.country_blocks) >= 5
        assert report.elapsed_seconds > 0
    finally:
        await engine.close()
