import asyncio
import html
import logging
import re
from typing import Dict, List, Optional, Set, Tuple

import aiohttp
import discord
from discord import app_commands
import langdetect
from langdetect import DetectorFactory
from redbot.core import Config, commands
from redbot.core.bot import Red

DetectorFactory.seed = 0

log = logging.getLogger("red.sablinova.sabbytranslate")

CUSTOM_EMOJI_RE = re.compile(r"<a?:\w+:\d+>")
MENTION_RE = re.compile(r"<@!?[0-9]+>|<@&[0-9]+>|<#[0-9]+>")
URL_RE = re.compile(r"https?://\S+")
DISCORD_TIMESTAMP_RE = re.compile(r"<t:\d+(:[tTdDfFR])?>")

LANGUAGES: Dict[str, str] = {
    "af": "Afrikaans", "sq": "Albanian", "am": "Amharic", "ar": "Arabic", "hy": "Armenian",
    "az": "Azerbaijani", "eu": "Basque", "be": "Belarusian", "bn": "Bengali", "bs": "Bosnian",
    "bg": "Bulgarian", "ca": "Catalan", "ceb": "Cebuano", "ny": "Chichewa",
    "zh-CN": "Chinese (Simplified)", "zh-TW": "Chinese (Traditional)", "co": "Corsican",
    "hr": "Croatian", "cs": "Czech", "da": "Danish", "nl": "Dutch", "en": "English",
    "eo": "Esperanto", "et": "Estonian", "tl": "Filipino", "fi": "Finnish", "fr": "French",
    "fy": "Frisian", "gl": "Galician", "ka": "Georgian", "de": "German", "el": "Greek",
    "gu": "Gujarati", "ht": "Haitian Creole", "ha": "Hausa", "haw": "Hawaiian", "he": "Hebrew",
    "hi": "Hindi", "hmn": "Hmong", "hu": "Hungarian", "is": "Icelandic", "ig": "Igbo",
    "id": "Indonesian", "ga": "Irish", "it": "Italian", "ja": "Japanese", "jw": "Javanese",
    "kn": "Kannada", "kk": "Kazakh", "km": "Khmer", "rw": "Kinyarwanda", "ko": "Korean",
    "ku": "Kurdish", "ky": "Kyrgyz", "lo": "Lao", "la": "Latin", "lv": "Latvian",
    "lt": "Lithuanian", "lb": "Luxembourgish", "mk": "Macedonian", "mg": "Malagasy",
    "ms": "Malay", "ml": "Malayalam", "mt": "Maltese", "mi": "Maori", "mr": "Marathi",
    "mn": "Mongolian", "my": "Myanmar (Burmese)", "ne": "Nepali", "no": "Norwegian",
    "or": "Odia", "ps": "Pashto", "fa": "Persian", "pl": "Polish", "pt": "Portuguese",
    "pa": "Punjabi", "ro": "Romanian", "ru": "Russian", "sm": "Samoan", "gd": "Scots Gaelic",
    "sr": "Serbian", "st": "Sesotho", "sn": "Shona", "sd": "Sindhi", "si": "Sinhala",
    "sk": "Slovak", "sl": "Slovenian", "so": "Somali", "es": "Spanish", "su": "Sundanese",
    "sw": "Swahili", "sv": "Swedish", "tg": "Tajik", "ta": "Tamil", "tt": "Tatar",
    "te": "Telugu", "th": "Thai", "tr": "Turkish", "tk": "Turkmen", "uk": "Ukrainian",
    "ur": "Urdu", "ug": "Uyghur", "uz": "Uzbek", "vi": "Vietnamese", "cy": "Welsh",
    "xh": "Xhosa", "yi": "Yiddish", "yo": "Yoruba", "zu": "Zulu",
    "ary": "Moroccan Arabic (Darija)",
}

NAME_TO_CODE: Dict[str, str] = {name.lower(): code for code, name in LANGUAGES.items()}
for code, name in LANGUAGES.items():
    NAME_TO_CODE[code.lower()] = code
NAME_TO_CODE["chinese"] = "zh-CN"
NAME_TO_CODE["zh"] = "zh-CN"
NAME_TO_CODE["portugese"] = "pt"
NAME_TO_CODE["brazilian"] = "pt"
NAME_TO_CODE["filipino"] = "tl"
NAME_TO_CODE["tagalog"] = "tl"
NAME_TO_CODE["farsi"] = "fa"
NAME_TO_CODE["jp"] = "ja"
NAME_TO_CODE["kr"] = "ko"
NAME_TO_CODE["darija"] = "ary"
NAME_TO_CODE["moroccan"] = "ary"
NAME_TO_CODE["moroccan arabic"] = "ary"
NAME_TO_CODE["maghrebi"] = "ary"
NAME_TO_CODE["ar-ma"] = "ary"


def normalize_language(lang_input: str) -> Optional[Tuple[str, str]]:
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


def clean_text_for_detection(text: str) -> str:
    t = re.sub(r"```[\s\S]*?```", "", text)
    t = re.sub(r"`[^`]*`", "", t)
    t = CUSTOM_EMOJI_RE.sub("", t)
    t = MENTION_RE.sub("", t)
    t = URL_RE.sub("", t)
    t = DISCORD_TIMESTAMP_RE.sub("", t)
    return t.strip()


def detect_script_heuristic(text: str) -> Optional[str]:
    has_arabic = any("\u0600" <= ch <= "\u06FF" or "\u0750" <= ch <= "\u077F" for ch in text)
    if has_arabic:
        return "ar"
    has_hangul = any("\uAC00" <= ch <= "\uD7AF" or "\u1100" <= ch <= "\u11FF" for ch in text)
    if has_hangul:
        return "ko"
    has_hiragana_katakana = any("\u3040" <= ch <= "\u30FF" for ch in text)
    if has_hiragana_katakana:
        return "ja"
    has_cyrillic = any("\u0400" <= ch <= "\u04FF" for ch in text)
    if has_cyrillic:
        return "ru"
    has_hebrew = any("\u0590" <= ch <= "\u05FF" for ch in text)
    if has_hebrew:
        return "he"
    has_devanagari = any("\u0900" <= ch <= "\u097F" for ch in text)
    if has_devanagari:
        return "hi"
    has_thai = any("\u0E00" <= ch <= "\u0E7F" for ch in text)
    if has_thai:
        return "th"
    has_greek = any("\u0370" <= ch <= "\u03FF" for ch in text)
    if has_greek:
        return "el"
    return None


def detect_language(text: str) -> Optional[str]:
    cleaned = clean_text_for_detection(text)
    if not cleaned:
        return None

    script_code = detect_script_heuristic(cleaned)
    if script_code:
        return script_code

    if len(cleaned.split()) <= 1 and len(cleaned) < 4:
        return None

    try:
        lang = langdetect.detect(cleaned)
        if lang == "zh-cn":
            return "zh-CN"
        if lang == "zh-tw":
            return "zh-TW"
        return lang
    except Exception:
        return None


class TranslationService:
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
        async with aiohttp.ClientSession(headers=self._headers) as session:
            async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                if resp.status == 200:
                    data = await resp.json(content_type=None)
                    translated = data.get("responseData", {}).get("translatedText")
                    if translated and "MYMEMORY WARNING" not in translated.upper():
                        return html.unescape(translated).strip()
        return None

    async def translate(self, text: str, target: str, source: str = "auto") -> str:
        if not text or not text.strip():
            return text

        cache_key = f"{source}:{target}:{text}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        if target == "ary" or source == "ary":
            try:
                res = await self._mymemory(text, target, source)
                if res and res != text:
                    self._cache[cache_key] = res
                    return res
            except Exception as e:
                log.debug(f"MyMemory Darija translation error: {e}")

        try:
            res = await self._google_mobile(text, target, source)
            if res:
                self._cache[cache_key] = res
                return res
        except Exception as e:
            log.debug(f"Google mobile translation error: {e}")

        try:
            res = await self._mymemory(text, target, source)
            if res:
                self._cache[cache_key] = res
                return res
        except Exception as e:
            log.debug(f"MyMemory translation error: {e}")

        return text

    async def translate_batch(self, texts: List[str], target: str, source: str = "auto") -> List[str]:
        tasks = [self.translate(t, target, source) for t in texts]
        return await asyncio.gather(*tasks)


class LanguageSelect(discord.ui.Select):
    def __init__(self, cog, user_id: int):
        self.cog = cog
        self.user_id = user_id
        options = [
            discord.SelectOption(label="English", value="en", emoji="🇺🇸"),
            discord.SelectOption(label="Portuguese", value="pt", emoji="🇧🇷"),
            discord.SelectOption(label="Arabic", value="ar", emoji="🇸🇦"),
            discord.SelectOption(label="Darija (Moroccan)", value="ary", emoji="🇲🇦"),
            discord.SelectOption(label="Korean", value="ko", emoji="🇰🇷"),
            discord.SelectOption(label="Spanish", value="es", emoji="🇪🇸"),
            discord.SelectOption(label="French", value="fr", emoji="🇫🇷"),
            discord.SelectOption(label="German", value="de", emoji="🇩🇪"),
            discord.SelectOption(label="Russian", value="ru", emoji="🇷🇺"),
            discord.SelectOption(label="Japanese", value="ja", emoji="🇯🇵"),
            discord.SelectOption(label="Chinese", value="zh-CN", emoji="🇨🇳"),
            discord.SelectOption(label="Italian", value="it", emoji="🇮🇹"),
            discord.SelectOption(label="Turkish", value="tr", emoji="🇹🇷"),
            discord.SelectOption(label="Vietnamese", value="vi", emoji="🇻🇳"),
            discord.SelectOption(label="Hindi", value="hi", emoji="🇮🇳"),
            discord.SelectOption(label="Indonesian", value="id", emoji="🇮🇩"),
        ]
        super().__init__(placeholder="🌐 Choose your preferred language...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("❌ This language prompt is for another user.", ephemeral=True)
            return

        chosen_code = self.values[0]
        chosen_name = LANGUAGES.get(chosen_code, chosen_code.title())
        await self.cog.config.user(interaction.user).preferred_language.set(chosen_code)
        self.cog._user_last_lang[interaction.user.id] = chosen_code

        for item in self.view.children:
            item.disabled = True
        await interaction.response.edit_message(
            content=f"✅ {interaction.user.mention} saved preferred language as **{chosen_name}** (`{chosen_code}`)!\nReplies to you in live translation threads will automatically translate into **{chosen_name}**.",
            view=self.view,
        )


class LanguagePromptView(discord.ui.View):
    def __init__(self, cog, user_id: int, detected_code: str, detected_name: str, timeout: float = 120.0):
        super().__init__(timeout=timeout)
        self.cog = cog
        self.user_id = user_id
        self.detected_code = detected_code
        self.detected_name = detected_name

        btn_detected = discord.ui.Button(
            label=f"Set to {detected_name} ({detected_code})",
            style=discord.ButtonStyle.primary,
            emoji="✅",
            custom_id=f"set_detected_{detected_code}"
        )
        btn_detected.callback = self.set_detected_callback
        self.add_item(btn_detected)

        self.add_item(LanguageSelect(cog, user_id))

        btn_dismiss = discord.ui.Button(
            label="Dismiss",
            style=discord.ButtonStyle.secondary,
            emoji="✖️",
            custom_id="dismiss_prompt"
        )
        btn_dismiss.callback = self.dismiss_callback
        self.add_item(btn_dismiss)

    async def set_detected_callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("❌ This language prompt is for another user.", ephemeral=True)
            return

        await self.cog.config.user(interaction.user).preferred_language.set(self.detected_code)
        self.cog._user_last_lang[interaction.user.id] = self.detected_code

        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(
            content=f"✅ {interaction.user.mention} saved preferred language as **{self.detected_name}** (`{self.detected_code}`)!\nReplies to you in live translation threads will automatically translate into **{self.detected_name}**.",
            view=self,
        )

    async def dismiss_callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("❌ This language prompt is for another user.", ephemeral=True)
            return

        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(
            content=f"ℹ️ {interaction.user.mention} dismissed language setup.",
            view=self,
        )

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True


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
            first_lang="en",
            second_lang="pt",
            reply_translate=True,
            prompt_new_users=True,
        )
        self.config.register_user(
            preferred_language=None,
        )
        self.translator = TranslationService()
        self._user_last_lang: Dict[int, str] = {}
        self._prompted_users: Set[int] = set()

    async def language_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> List[app_commands.Choice[str]]:
        current = current.lower().strip()
        choices = []

        common_codes = ["en", "pt", "es", "fr", "de", "it", "ru", "ar", "ko", "ja", "zh-CN", "tr"]
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

        translated_results = await self.translator.translate_batch(
            [t[1] for t in texts_to_translate], target=target_code
        )

        translated_map = {
            texts_to_translate[i][0]: translated_results[i]
            for i in range(len(texts_to_translate))
        }

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

        history_msgs: List[discord.Message] = []
        async for msg in channel.history(limit=limit, oldest_first=True):
            if msg.author.id == self.bot.user.id and (
                "Translated Thread History" in msg.content or "Live Two-Way Translation" in msg.content or "Translated" in msg.content
            ):
                continue
            history_msgs.append(msg)

        if not history_msgs:
            await interaction.followup.send("⚠️ No messages found to translate in this thread.")
            return

        await interaction.followup.send(
            f"🔄 **Translating {len(history_msgs)} message(s) to {target_name} ({target_code})...**"
        )

        translated_count = 0
        for msg in history_msgs:
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

    # ── Live Two-Way Translation ──

    @app_commands.command(
        name="livetranslate",
        description="Configure two-way live translation in this thread or channel.",
    )
    @app_commands.describe(
        action="Action to perform (start, stop, or status)",
        first_language="First language (e.g. English)",
        second_language="Second language (e.g. Portuguese)",
    )
    @app_commands.choices(
        action=[
            app_commands.Choice(name="Start Live Translation", value="start"),
            app_commands.Choice(name="Stop Live Translation", value="stop"),
            app_commands.Choice(name="Check Status", value="status"),
        ],
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
        channel = interaction.channel
        if not isinstance(channel, (discord.Thread, discord.TextChannel)):
            await interaction.response.send_message(
                "❌ This command can only be used in text channels or threads.", ephemeral=True
            )
            return

        action_val = str(getattr(action, "value", action)).strip().lower()
        if action_val == "stop":
            await self.config.channel(channel).clear()
            await interaction.response.send_message(
                f"🛑 **Live translation deactivated** for {channel.mention}."
            )
            return

        if action_val == "status":
            conf = await self.config.channel(channel).all()
            if not conf.get("enabled"):
                await interaction.response.send_message(
                    f"ℹ️ Live translation is currently **disabled** in {channel.mention}.",
                    ephemeral=True,
                )
            else:
                first_l = LANGUAGES.get(conf["first_lang"], conf["first_lang"].upper())
                sec_l = LANGUAGES.get(conf["second_lang"], conf["second_lang"].upper())
                await interaction.response.send_message(
                    f"🟢 **Live Two-Way Translation Active in {channel.mention}**\n"
                    f"• **Language 1:** {first_l} (`{conf['first_lang']}`)\n"
                    f"• **Language 2:** {sec_l} (`{conf['second_lang']}`)\n"
                    f"• Messages sent in **{first_l}** auto-translate to **{sec_l}**.\n"
                    f"• Messages sent in **{sec_l}** auto-translate to **{first_l}**.\n"
                    f"• Messages in any other language auto-translate to **{first_l}**.\n"
                    f"• **Smart Reply Translation:** `Enabled`"
                )
            return

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

        await self.config.channel(channel).set({
            "enabled": True,
            "first_lang": code1,
            "second_lang": code2,
            "reply_translate": True,
            "prompt_new_users": True,
        })

        embed = discord.Embed(
            title="🌐 Live Two-Way Translation Activated",
            description=(
                f"Two-way translation is now active in {channel.mention}!\n\n"
                f"🔀 **{name1} ({code1}) ⇄ {name2} ({code2})**\n\n"
                f"• Messages in **{name1}** will auto-translate to **{name2}**.\n"
                f"• Messages in **{name2}** will auto-translate to **{name1}**.\n"
                f"• Messages in any other language will auto-translate to **{name1}**.\n"
                f"• **Smart Reply Translation**: Replies to foreign users auto-translate back into their language.\n\n"
                f"To turn off, run `/livetranslate action:Stop`."
            ),
            color=discord.Color.blue(),
        )
        await interaction.response.send_message(embed=embed)

    @app_commands.command(
        name="mytranslate",
        description="Set your preferred personal language for smart translation & replies.",
    )
    @app_commands.describe(
        language="Your native/preferred language (e.g. Portuguese, Korean, Arabic)",
    )
    @app_commands.autocomplete(language=language_autocomplete)
    async def slash_mytranslate(
        self,
        interaction: discord.Interaction,
        language: Optional[str] = None,
    ):
        if not language:
            curr = await self.config.user(interaction.user).preferred_language()
            if curr:
                lang_name = LANGUAGES.get(curr, curr.upper())
                await interaction.response.send_message(
                    f"ℹ️ Your preferred language is set to **{lang_name}** (`{curr}`).\n"
                    f"To change it, run `/mytranslate language:<new_language>`.",
                    ephemeral=True,
                )
            else:
                view = discord.ui.View(timeout=120.0)
                view.add_item(LanguageSelect(self, interaction.user.id))
                await interaction.response.send_message(
                    "🌐 **Select your native / preferred language below:**",
                    view=view,
                    ephemeral=True,
                )
            return

        norm = normalize_language(language)
        if not norm:
            await interaction.response.send_message(
                f"❌ Language `{language}` not recognized.", ephemeral=True
            )
            return

        code, name = norm
        await self.config.user(interaction.user).preferred_language.set(code)
        self._user_last_lang[interaction.user.id] = code

        await interaction.response.send_message(
            f"✅ Your preferred language is now saved as **{name}** (`{code}`)!\n"
            f"When people reply to you in live translation channels, their messages will automatically translate into **{name}**.",
            ephemeral=True,
        )

    @commands.command(name="fulltranslate", aliases=["threadtranslate", "translateall"])
    @commands.guild_only()
    async def prefix_fulltranslate(
        self, ctx: commands.Context, language: str, limit: int = 50
    ):
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

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild or not message.content:
            return

        conf = await self.config.channel(message.channel).all()
        if not conf.get("enabled"):
            return

        text = message.content.strip()
        prefixes = await self.bot.get_valid_prefixes(message.guild)
        if text.startswith(tuple(prefixes)):
            return

        first_lang = conf.get("first_lang", "en")
        second_lang = conf.get("second_lang", "pt")
        reply_enabled = conf.get("reply_translate", True)
        prompt_enabled = conf.get("prompt_new_users", True)

        # 1. Check & prompt unregistered foreign speakers
        detected_lang = detect_language(text)
        if detected_lang:
            self._user_last_lang[message.author.id] = detected_lang

            if prompt_enabled and message.author.id not in self._prompted_users:
                user_pref = await self.config.user(message.author).preferred_language()
                if not user_pref and detected_lang not in (first_lang, second_lang):
                    self._prompted_users.add(message.author.id)
                    det_name = LANGUAGES.get(detected_lang, detected_lang.upper())
                    view = LanguagePromptView(self, message.author.id, detected_lang, det_name)
                    asyncio.create_task(
                        message.channel.send(
                            f"👋 Hello {message.author.mention}! I noticed you are speaking **{det_name}**.\n"
                            f"Would you like to save **{det_name}** as your preferred language so replies to you are automatically translated?",
                            view=view,
                        )
                    )

        # 2. Smart Reply Context
        if reply_enabled and message.reference and message.reference.message_id:
            try:
                ref_msg = message.reference.resolved or await message.channel.fetch_message(
                    message.reference.message_id
                )
                if ref_msg and isinstance(ref_msg, discord.Message) and not ref_msg.author.bot and ref_msg.author.id != message.author.id:
                    user_pref = await self.config.user(ref_msg.author).preferred_language()
                    recipient_lang = user_pref or self._user_last_lang.get(ref_msg.author.id)

                    if not recipient_lang and ref_msg.content:
                        recipient_lang = detect_language(ref_msg.content)

                    sender_lang = detected_lang or first_lang

                    if recipient_lang and recipient_lang != sender_lang:
                        recip_name = LANGUAGES.get(recipient_lang, recipient_lang.upper())
                        send_name = LANGUAGES.get(sender_lang, sender_lang.upper())
                        translated = await self.translator.translate(text, target=recipient_lang, source=sender_lang)

                        if translated.lower().strip() != text.lower().strip():
                            await message.reply(
                                f"🌐 **[Translated {send_name} ➔ {recip_name} for {ref_msg.author.mention}]**:\n{translated}",
                                mention_author=False,
                            )
                            return
            except Exception as e:
                log.debug(f"Reply context translation error: {e}")

        # 3. Two-Way Channel Translation
        if not detected_lang:
            return

        if detected_lang == first_lang:
            target_lang = second_lang
        elif detected_lang == second_lang:
            target_lang = first_lang
        else:
            # Any other language translates to first_lang
            target_lang = first_lang

        if not target_lang or target_lang == detected_lang:
            return

        try:
            translated = await self.translator.translate(text, target=target_lang, source=detected_lang)
            if translated.lower().strip() == text.lower().strip():
                return

            src_name = LANGUAGES.get(detected_lang, detected_lang.upper())
            dst_name = LANGUAGES.get(target_lang, target_lang.upper())

            await message.reply(
                f"🌐 **[Translated {src_name} ➔ {dst_name}]**:\n{translated}",
                mention_author=False,
            )
        except Exception as e:
            log.error(f"Live translation error on message {message.id}: {e}", exc_info=True)
