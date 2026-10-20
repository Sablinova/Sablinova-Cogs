"""Static taxonomy, category dictionaries, and metadata for FilterCheck providers."""

from __future__ import annotations

# FortiGuard categories allowed by default on typical educational/standard policies
FORTIGUARD_SAFE_CATEGORIES = {
    0, 9, 28, 29, 30, 31, 33, 34, 35, 36, 39, 40, 41, 42, 43, 44,
    46, 47, 49, 50, 51, 52, 53, 63, 75, 76, 77, 78, 79, 80, 81, 82, 84, 92,
}

FORTIGUARD_CATEGORIES = {
    26: "Malicious Websites",
    28: "Phishing",
    30: "Proxy Avoidance",
    51: "Spam",
    52: "Dynamic DNS",
    53: "Meaningless Content",
    63: "Peer-to-Peer",
    84: "Malware",
}

# Lightspeed category mappings
LIGHTSPEED_CATEGORIES = {
    0: ("Unknown", True),
    1: ("Search Engines", False),
    2: ("General Information", False),
    3: ("Education", False),
    4: ("Reference", False),
    5: ("News", False),
    6: ("Entertainment", True),
    7: ("Games", True),
    8: ("Social Networking", True),
    9: ("Audio / Video Streaming", True),
    10: ("Security - Malware", True),
    11: ("Security - Phishing", True),
    12: ("Security - Domain Sharing", True),
    13: ("Proxy Avoidance", True),
    14: ("Explicit Content", True),
    15: ("Shopping", True),
    16: ("Technology", False),
    17: ("Business", False),
    18: ("Forums / Message Boards", True),
    19: ("Webmail", False),
    20: ("File Sharing", True),
}

# Blocksi Web categories
BLOCKSI_WEB_CATEGORIES = {
    100: ("Security Risk", "Malicious content", True),
    101: ("Security Risk", "Phishing", True),
    102: ("Security Risk", "Proxy Avoidance", True),
    103: ("Security Risk", "Spam", True),
    201: ("General", "Education", False),
    202: ("General", "Reference", False),
    203: ("General", "News", False),
    204: ("General", "Technology", False),
    301: ("Entertainment", "Games", True),
    302: ("Entertainment", "Social Networking", True),
    303: ("Entertainment", "Streaming Media", True),
}

# Cisco blocked categories
CISCO_BLOCKED_CATEGORIES = {
    "Malicious Sites",
    "Phishing",
    "Spam",
    "Dynamic DNS",
    "Anonymizers",
    "Botnet",
    "Cryptomining",
    "Adult Content",
    "Pornography",
    "Gambling",
    "Peer to Peer",
    "Hate Speech",
    "Illegal Activities",
    "Terrorism",
}

# Linewize blocked signatures
LINEWIZE_BLOCKED_CATEGORIES = {
    "animeandmanga",
    "dynamicdns",
    "trackers",
    "celebrities",
    "entertainment",
    "humouranddistractions",
    "livestreaming",
    "media",
    "socialmediaandcommunication",
    "marijuana",
    "crypto",
    "gamestorespublishers",
    "videogames",
    "blocklist.proxies",
    "aitools",
    "blocklist.dating",
    "blocklist.piracy",
    "whatsmyipservices",
    "p2p",
    "unsafesearchengines",
    "porn",
    "extreme",
    "malware",
    "matureandexplicit",
    "gamingresources",
    "offensive",
}

# Country labels and flags
COUNTRY_PROBES = {
    "CN": ("🇨🇳", "China", "Great Firewall (GFW) / Domestic DNS"),
    "RU": ("🇷🇺", "Russia", "Roskomnadzor (RKN) / Yandex Safe"),
    "IN": ("🇮🇳", "India", "Department of Telecom (DoT) / Jio / Airtel"),
    "ID": ("🇮🇩", "Indonesia", "Kominfo / TrustPositif"),
    "TR": ("🇹🇷", "Turkey", "BTK National Registry"),
    "GB": ("🇬🇧", "United Kingdom", "Cleanfeed / High Court Blocklist"),
    "IR": ("🇮🇷", "Iran", "Telecommunication Infrastructure Co (TIC)"),
    "ES": ("🇪🇸", "Spain", "LaLiga / Copyright Court Blocklist"),
    "IT": ("🇮🇹", "Italy", "AGCOM Piracy Shield"),
    "AU": ("🇦🇺", "Australia", "Federal Court Copyright Blocklist"),
}

# Major DNS resolvers to query
DNS_RESOLVERS = [
    {
        "name": "Cloudflare Security",
        "detail": "1.1.1.2 • Malware & Phishing",
        "ip": "1.1.1.2",
        "category": "Malware Block",
    },
    {
        "name": "Cloudflare Family",
        "detail": "1.1.1.3 • Malware & Adult",
        "ip": "1.1.1.3",
        "category": "Family Filter",
    },
    {
        "name": "Quad9 Threat Block",
        "detail": "9.9.9.9 • Global Threat Intel",
        "ip": "9.9.9.9",
        "category": "Threat Protection",
    },
    {
        "name": "AdGuard Default",
        "detail": "94.140.14.14 • Ad & Malware",
        "ip": "94.140.14.14",
        "category": "Ad & Threat Block",
    },
    {
        "name": "AdGuard Family",
        "detail": "94.140.14.15 • Family Safe",
        "ip": "94.140.14.15",
        "category": "Family Filter",
    },
    {
        "name": "CleanBrowsing Security",
        "detail": "185.228.168.9 • Phishing & Malware",
        "ip": "185.228.168.9",
        "category": "Security Filter",
    },
    {
        "name": "CleanBrowsing Adult",
        "detail": "185.228.168.168 • Adult & Safe",
        "ip": "185.228.168.168",
        "category": "Adult Filter",
    },
    {
        "name": "OpenDNS FamilyShield",
        "detail": "208.67.222.123 • Cisco Adult Filter",
        "ip": "208.67.222.123",
        "category": "Cisco FamilyShield",
    },
    {
        "name": "Control D Malware",
        "detail": "76.76.2.1 • Malicious Filter",
        "ip": "76.76.2.1",
        "category": "Malware Filter",
    },
    {
        "name": "Comodo Secure",
        "detail": "8.26.56.26 • Comodo Threat DNS",
        "ip": "8.26.56.26",
        "category": "Threat Filter",
    },
    {
        "name": "Google Public DNS",
        "detail": "8.8.8.8 • Unfiltered Baseline",
        "ip": "8.8.8.8",
        "category": "Unfiltered Baseline",
    },
]
