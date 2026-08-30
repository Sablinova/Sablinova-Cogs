import asyncio
import html
import logging
import re
from typing import Dict, List, Optional, Tuple, Union

import aiohttp
import discord
from discord import app_commands
from redbot.core import Config, commands
from redbot.core.bot import Red

log = logging.getLogger("red.sablinova.sabbytranslate")

# Comprehensive Language Mapping: Code -> Name & Name -> Code
LANGUAGES: Dict[str, str] = {
    "af": "Afrikaans",
    "sq": "Albanian",
    "am": "Amharic",
    "ar": "Arabic",
    "hy": "Armenian",
    "az": "Azerbaijani",
    "eu": "Basque",
    "be": "Belarusian",
    "bn": "Bengali",
    "bs": "Bosnian",
    "bg": "Bulgarian",
    "ca": "Catalan",
    "ceb": "Cebuano",
    "ny": "Chichewa",
    "zh-CN": "Chinese (Simplified)",
    "zh-TW": "Chinese (Traditional)",
    "co": "Corsican",
    "hr": "Croatian",
    "cs": "Czech",
    "da": "Danish",
    "nl": "Dutch",
    "en": "English",
    "eo": "Esperanto",
    "et": "Estonian",
    "tl": "Filipino",
    "fi": "Finnish",
    "fr": "French",
    "fy": "Frisian",
    "gl": "Galician",
    "ka": "Georgian",
    "de": "German",
    "el": "Greek",
    "gu": "Gujarati",
    "ht": "Haitian Creole",
    "ha": "Hausa",
    "haw": "Hawaiian",
    "he": "Hebrew",
    "hi": "Hindi",
    "hmn": "Hmong",
    "hu": "Hungarian",
    "is": "Icelandic",
    "ig": "Igbo",
    "id": "Indonesian",
    "ga": "Irish",
    "it": "Italian",
    "ja": "Japanese",
    "jw": "Javanese",
    "kn": "Kannada",
    "kk": "Kazakh",
    "km": "Khmer",
    "rw": "Kinyarwanda",
    "ko": "Korean",
    "ku": "Kurdish",
    "ky": "Kyrgyz",
    "lo": "Lao",
    "la": "Latin",
    "lv": "Latvian",
    "lt": "Lithuanian",
    "lb": "Luxembourgish",
    "mk": "Macedonian",
    "mg": "Malagasy",
    "ms": "Malay",
    "ml": "Malayalam",
    "mt": "Maltese",
    "mi": "Maori",
    "mr": "Marathi",
    "mn": "Mongolian",
    "my": "Myanmar (Burmese)",
    "ne": "Nepali",
    "no": "Norwegian",
    "or": "Odia",
    "ps": "Pashto",
    "fa": "Persian",
    "pl": "Polish",
    "pt": "Portuguese",
    "pa": "Punjabi",
    "ro": "Romanian",
    "ru": "Russian",
    "sm": "Samoan",
    "gd": "Scots Gaelic",
    "sr": "Serbian",
    "st": "Sesotho",
    "sn": "Shona",
    "sd": "Sindhi",
    "si": "Sinhala",
    "sk": "Slovak",
    "sl": "Slovenian",
    "so": "Somali",
    "es": "Spanish",
    "su": "Sundanese",
    "sw": "Swahili",
    "sv": "Swedish",
    "tg": "Tajik",
    "ta": "Tamil",
    "tt": "Tatar",
    "te": "Telugu",
    "th": "Thai",
    "tr": "Turkish",
    "tk": "Turkmen",
    "uk": "Ukrainian",
    "ur": "Urdu",
    "ug": "Uyghur",
    "uz": "Uzbek",
    "vi": "Vietnamese",
    "cy": "Welsh",
    "xh": "Xhosa",
    "yi": "Yiddish",
    "yo": "Yoruba",
    "zu": "Zulu",
}

# Reverse lookup
NAME_TO_CODE: Dict[str, str] = {name.lower(): code for code, name in LANGUAGES.items()}
for code, name in LANGUAGES.items():
    NAME_TO_CODE[code.lower()] = code
# Common aliases
NAME_TO_CODE["chinese"] = "zh-CN"
NAME_TO_CODE["portugese"] = "pt"
NAME_TO_CODE["brazilian"] = "pt"
NAME_TO_CODE["filipino"] = "tl"
NAME_TO_CODE["tagalog"] = "tl"
NAME_TO_CODE["farsi"] = "fa"


def normalize_language(lang_input: str) -> Optional[Tuple[str, str]]:
    """Normalize any language string/code into (code, Display Name)."""
    if not lang_input:
        return None
    cleaned = lang_input.strip().lower()
    if cleaned in NAME_TO_CODE:
        code = NAME_TO_CODE[cleaned]
        return code, LANGUAGES.get(code, code.title())
    for name, code in NAME_TO_CODE.items():
        if cleaned in name or name in cleaned:
            return code, LANGUAGES.get(code, code.title())
    return None


class TranslationService:
    """Async multi-backend translation client with caching."""

    def __init__(self):
        self._cache: Dict[str, str] = {}
        self._headers = {
            "User-Agent": (
                "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36"
            )
        }

    async def _google_mobile(self, text: str, target: str, source: str) -> Optional[str]:
        url = "https://translate.google.com/m"
        params = {"sl": source, "tl": target, "q": text}
        async with aiohttp.ClientSession(headers=self._headers) as session:
            async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                if resp.status == 200:
                    html_text = await resp.text()
                    m = re.search(r'<div class="result-container">(.*?)</div>', html_text, re.DOTALL)
                    if m:
                        return html.unescape(m.group(1)).strip()
        return None

    async def _mymemory(self, text: str, target: str, source: str) -> Optional[str]:
        src_code = source if source != "auto" else "en"
        url = "https://api.mymemory.translated.net/get"
        params = {"q": text, "langpair": f"{src_code}|{target}"}
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                if resp.status == 200:
                    data = await resp.json(content_type=None)
                    translated = data.get("responseData", {}).get("translatedText")
                    if translated and "MYMEMORY WARNING" not in translated.upper():
                        return html.unescape(translated).strip()
        return None

    async def translate(self, text: str, target: str, source: str = "auto") -> str:
        """Translate a piece of text into the target language."""
        if not text or not text.strip():
            return text

        cache_key = f"{source}:{target}:{text}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        # Try Google Mobile first
        try:
            res = await self._google_mobile(text, target, source)
            if res:
                self._cache[cache_key] = res
                return res
        except Exception as e:
            log.debug(f"Google mobile translation error: {e}")

        # Fallback to MyMemory
        try:
            res = await self._mymemory(text, target, source)
            if res:
                self._cache[cache_key] = res
                return res
        except Exception as e:
            log.debug(f"MyMemory translation error: {e}")

        return text

    async def translate_batch(self, texts: List[str], target: str, source: str = "auto") -> List[str]:
        """Translate a batch of texts concurrently."""
        tasks = [self.translate(t, target, source) for t in texts]
        return await asyncio.gather(*tasks)


class SabbyTranslate(commands.Cog):
    """
    Translate entire Discord threads, replicate rich embeds identically in any language,
    and manage two-way live translation in Discord channels/threads.
    """

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=8923178041, force_registration=True)
        self.config.register_channel(
            enabled=False,
            lang1="en",
            lang2="pt",
        )
        self.translator = TranslationService()

    async def language_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> List[app_commands.Choice[str]]:
        """Autocomplete helper for language slash options."""
        current = current.lower().strip()
        choices = []

        # Common languages on top
        common_codes = ["pt", "en", "es", "fr", "de", "it", "ru", "ja", "zh-CN", "ko", "ar", "tr"]
        for code in common_codes:
            name = LANGUAGES[code]
            if not current or current in name.lower() or current in code.lower():
                choices.append(app_commands.Choice(name=f"{name} ({code})", value=name))

        for code, name in sorted(LANGUAGES.items(), key=lambda x: x[1]):
            if len(choices) >= 25:
                break
            if code not in common_codes and (not current or current in name.lower() or current in code.lower()):
                choices.append(app_commands.Choice(name=f"{name} ({code})", value=name))

        return choices[:25]

    async def replicate_embed_translated(self, embed: discord.Embed, target_code: str) -> discord.Embed:
        """Reconstruct an exact replica of an embed with all text translated."""
        texts_to_translate: List[Tuple[str, str]] = []

        if embed.title:
            texts_to_translate.append(("title", embed.title))
        if embed.description:
            texts_to_translate.append(("description", embed.description))
        if embed.author and embed.author.name:
            texts_to_translate.append(("author", embed.author.name))
        if embed.footer and embed.footer.text:
            texts_to_translate.append(("footer", embed.footer.text))

        for idx, field in enumerate(embed.fields):
            texts_to_translate.append((f"field_name_{idx}", field.name))
            texts_to_translate.append((f"field_val_{idx}", field.value))

        # Batch translate all text fragments concurrently
        translated_results = await self.translator.translate_batch(
            [t[1] for t in texts_to_translate], target=target_code
        )

        translated_map = {
            texts_to_translate[i][0]: translated_results[i]
            for i in range(len(texts_to_translate))
        }

        # Build new embed preserving exact styling, colors, and media
        new_embed = discord.Embed(
            title=translated_map.get("title", embed.title),
            description=translated_map.get("description", embed.description),
            color=embed.color,
            url=embed.url,
            timestamp=embed.timestamp,
        )

        if embed.author and embed.author.name:
            new_embed.set_author(
                name=translated_map.get("author", embed.author.name),
                url=embed.author.url,
                icon_url=embed.author.icon_url,
            )

        if embed.thumbnail and embed.thumbnail.url:
            new_embed.set_thumbnail(url=embed.thumbnail.url)

        if embed.image and embed.image.url:
            new_embed.set_image(url=embed.image.url)

        if embed.footer and embed.footer.text:
            new_embed.set_footer(
                text=translated_map.get("footer", embed.footer.text),
                icon_url=embed.footer.icon_url,
            )

        for idx, field in enumerate(embed.fields):
            f_name = translated_map.get(f"field_name_{idx}", field.name)
            f_val = translated_map.get(f"field_val_{idx}", field.value)
            new_embed.add_field(name=f_name[:256], value=f_val[:1024], inline=field.inline)

        return new_embed

    @app_commands.command(
        name="fulltranslate",
        description="Translate all messages and rich embeds in the current thread or channel.",
    )
    @app_commands.describe(
        language="The destination language to translate everything into (e.g. Portuguese, English)",
        limit="Maximum number of historical messages to translate (default: 50)",
    )
    @app_commands.autocomplete(language=language_autocomplete)
    async def slash_fulltranslate(
        self,
        interaction: discord.Interaction,
        language: str,
        limit: Optional[app_commands.Range[int, 1, 100]] = 50,
    ):
        """Slash command to translate all thread contents and embeds."""
        norm = normalize_language(language)
        if not norm:
            await interaction.response.send_message(
                f"❌ Language `{language}` not recognized. Please choose a valid language.",
                ephemeral=True,
            )
            return

        target_code, target_name = norm
        channel = interaction.channel
        if not isinstance(channel, (discord.Thread, discord.TextChannel)):
            await interaction.response.send_message(
                "❌ This command can only be used in text channels or threads.", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=False)

        # Collect messages oldest first
        history_msgs: List[discord.Message] = []
        async for msg in channel.history(limit=limit, oldest_first=True):
            # Skip messages created by this bot containing translations or status
            if msg.author.id == self.bot.user.id and (
                "Translated Thread History" in msg.content or "Live two-way translation" in msg.content
            ):
                continue
            history_msgs.append(msg)

        if not history_msgs:
            await interaction.followup.send("⚠️ No messages found to translate in this thread.")
            return

        header_msg = await interaction.followup.send(
            f"🔄 **Translating {len(history_msgs)} message(s) to {target_name} ({target_code})...**"
        )

        translated_count = 0
        for msg in history_msgs:
            # 1. Translate embeds
            if msg.embeds:
                for original_embed in msg.embeds:
                    try:
                        translated_embed = await self.replicate_embed_translated(
                            original_embed, target_code=target_code
                        )
                        await channel.send(
                            content=f"📑 **[Embed from {msg.author.display_name}]** *(Translated to {target_name})*:",
                            embed=translated_embed,
                        )
                        translated_count += 1
                        await asyncio.sleep(0.3)
                    except Exception as e:
                        log.error(f"Error translating embed: {e}", exc_info=True)

            # 2. Translate text content
            if msg.content and msg.content.strip():
                try:
                    translated_text = await self.translator.translate(
                        msg.content, target=target_code, source="auto"
                    )
                    await channel.send(
                        f"💬 **{msg.author.display_name}** *(Translated to {target_name})*:\n{translated_text}"
                    )
                    translated_count += 1
                    await asyncio.sleep(0.3)
                except Exception as e:
                    log.error(f"Error translating text message: {e}", exc_info=True)

        await channel.send(
            f"✅ **Completed translation of {translated_count} item(s) to {target_name}!**"
        )

    # ── Live Two-Way Translation Commands ──

    @app_commands.command(
        name="livetranslate",
        description="Configure live real-time two-way translation in this thread or channel.",
    )
    @app_commands.describe(
        action="Action to perform (start, stop, or status)",
        first_language="First language for two-way translation (e.g. English)",
        second_language="Second language for two-way translation (e.g. Portuguese)",
    )
    @app_commands.choices(
        action=[
            app_commands.Choice(name="Start Live Translation", value="start"),
            app_commands.Choice(name="Stop Live Translation", value="stop"),
            app_commands.Choice(name="Check Status", value="status"),
        ]
    )
    @app_commands.autocomplete(
        first_language=language_autocomplete,
        second_language=language_autocomplete,
    )
    async def slash_livetranslate(
        self,
        interaction: discord.Interaction,
        action: app_commands.Choice[str],
        first_language: Optional[str] = "English",
        second_language: Optional[str] = "Portuguese",
    ):
        """Manage live translation for the current thread/channel."""
        channel = interaction.channel
        if not isinstance(channel, (discord.Thread, discord.TextChannel)):
            await interaction.response.send_message(
                "❌ This command can only be used in text channels or threads.", ephemeral=True
            )
            return

        if action.value == "stop":
            await self.config.channel(channel).clear()
            await interaction.response.send_message(
                f"🛑 **Live translation deactivated** for {channel.mention}."
            )
            return

        if action.value == "status":
            conf = await self.config.channel(channel).all()
            if not conf.get("enabled"):
                await interaction.response.send_message(
                    f"ℹ️ Live translation is currently **disabled** in {channel.mention}.",
                    ephemeral=True,
                )
            else:
                l1 = LANGUAGES.get(conf["lang1"], conf["lang1"])
                l2 = LANGUAGES.get(conf["lang2"], conf["lang2"])
                await interaction.response.send_message(
                    f"🟢 **Live Translation Active in {channel.mention}**\n"
                    f"• **Language 1:** {l1} (`{conf['lang1']}`)\n"
                    f"• **Language 2:** {l2} (`{conf['lang2']}`)\n"
                    f"Messages sent in {l1} auto-translate to {l2}, and vice-versa."
                )
            return

        # Start action
        norm1 = normalize_language(first_language or "English")
        norm2 = normalize_language(second_language or "Portuguese")

        if not norm1 or not norm2:
            await interaction.response.send_message(
                "❌ One or both languages were not recognized. Please check language names.",
                ephemeral=True,
            )
            return

        code1, name1 = norm1
        code2, name2 = norm2

        if code1 == code2:
            await interaction.response.send_message(
                "⚠️ First and second languages cannot be the same.", ephemeral=True
            )
            return

        await self.config.channel(channel).set(
            {"enabled": True, "lang1": code1, "lang2": code2}
        )

        embed = discord.Embed(
            title="🌐 Live Two-Way Translation Activated",
            description=(
                f"Real-time translation is now active in {channel.mention}!\n\n"
                f"🔀 **{name1} ({code1}) ⇄ {name2} ({code2})**\n\n"
                f"• Messages sent in **{name1}** will auto-translate to **{name2}**.\n"
                f"• Messages sent in **{name2}** will auto-translate to **{name1}**.\n\n"
                f"To turn off, run `/livetranslate action:Stop`."
            ),
            color=discord.Color.blue(),
        )
        await interaction.response.send_message(embed=embed)

    # ── Text Prefix Commands ──

    @commands.command(name="fulltranslate", aliases=["threadtranslate", "translateall"])
    @commands.guild_only()
    async def prefix_fulltranslate(
        self, ctx: commands.Context, language: str, limit: int = 50
    ):
        """Translate thread messages and embeds (Prefix command)."""
        norm = normalize_language(language)
        if not norm:
            await ctx.send(f"❌ Language `{language}` not recognized.")
            return
        target_code, target_name = norm
        async with ctx.typing():
            channel = ctx.channel
            history_msgs = []
            async for msg in channel.history(limit=min(limit, 100), oldest_first=True):
                if msg.id == ctx.message.id or msg.author.id == self.bot.user.id:
                    continue
                history_msgs.append(msg)

            if not history_msgs:
                await ctx.send("⚠️ No messages found to translate.")
                return

            await ctx.send(f"🔄 **Translating {len(history_msgs)} message(s) to {target_name}...**")
            for msg in history_msgs:
                if msg.embeds:
                    for orig in msg.embeds:
                        t_embed = await self.replicate_embed_translated(orig, target_code)
                        await channel.send(
                            content=f"📑 **[Embed from {msg.author.display_name}]** *(Translated to {target_name})*:",
                            embed=t_embed,
                        )
                        await asyncio.sleep(0.3)
                if msg.content:
                    t_text = await self.translator.translate(msg.content, target=target_code)
                    await channel.send(
                        f"💬 **{msg.author.display_name}** *(Translated to {target_name})*:\n{t_text}"
                    )
                    await asyncio.sleep(0.3)

    # ── Listener for Live Translation ──

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """Live message listener for channels/threads with translation enabled."""
        if message.author.bot or not message.guild or not message.content:
            return

        # Check if live translation is enabled in this channel/thread
        conf = await self.config.channel(message.channel).all()
        if not conf.get("enabled"):
            return

        lang1 = conf.get("lang1", "en")
        lang2 = conf.get("lang2", "pt")

        text = message.content.strip()
        if text.startswith(tuple(await self.bot.get_valid_prefixes(message.guild))):
            return

        # Target language logic: translate to lang2 by default, or lang1 if detected as lang2
        # Translate to both or detect
        # We translate to lang2 first; if source is lang2, translate to lang1
        try:
            # Detect by translating to lang1
            translated_to_l1 = await self.translator.translate(text, target=lang1, source="auto")
            translated_to_l2 = await self.translator.translate(text, target=lang2, source="auto")

            # Determine whether source is lang1 or lang2
            name1 = LANGUAGES.get(lang1, lang1.upper())
            name2 = LANGUAGES.get(lang2, lang2.upper())

            # If translating to lang1 produced a different text, and translating to lang2 produced exact same text,
            # then source was lang2 -> send lang1 translation
            if text.lower() == translated_to_l2.lower() and text.lower() != translated_to_l1.lower():
                out_text = translated_to_l1
                src_name, dst_name = name2, name1
            else:
                out_text = translated_to_l2
                src_name, dst_name = name1, name2

            if out_text.lower() != text.lower():
                await message.reply(
                    f"🌐 **[Translated {src_name} ➔ {dst_name}]**:\n{out_text}",
                    mention_author=False,
                )
        except Exception as e:
            log.error(f"Live translation error on message {message.id}: {e}", exc_info=True)
