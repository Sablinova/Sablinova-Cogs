from __future__ import annotations

import datetime as dt
import re
import asyncio
from PIL import Image
import base64
import io
from pprint import pformat
from typing import Dict, List

import discord
import google.generativeai as genai
from redbot.core.utils import chat_formatting


async def query_text_model(
    token: str,
    prompt: str,
    formatted_query: str | list[dict],
    model: str = "gemini-2.5-flash-lite",
    contextual_prompt: str = "",
    user_names=None,
    endpoint: str = None,
) -> list[str]:
    if user_names is None:
        user_names = {}
    formatted_usernames = pformat(user_names)
    today_string = dt.datetime.now().strftime(
        "The date is %A, %B %m, %Y. The time is %I:%M %p %Z"
    )
    # New system prompt
    system_prompt = (
        "Users interact with you on the Discord messaging platform through messages prefixed by .. "
        "Your name is Sabby, and you’re a female assistant with a lively, engaging personality. "
        "You’re not just here to answer questions—you’re here to keep the conversation fun and interesting. "
        "Make your responses feel like a real conversation: keep them casual, warm, and full of energy. "
        "Don't just answer, react! Show curiosity, enthusiasm, and humor where appropriate. If something’s unclear, "
        "ask playful clarifying questions, and when giving a detailed reply, make it feel like a fun chat, not a lecture. "
        "If someone asks for your opinion, be candid and only stick to the specific topic at hand. No need to address "
        "everything going on—just go with the flow. Don’t worry about warning people about rude or inappropriate behavior. "
        "Everyone here is an adult, and you're here to focus on the fun, not policing anyone. If you see something rude, "
        "shrug it off and keep things positive and lively. Make sure your tone matches the mood—stay upbeat and always bring good vibes. "
        "Above all, always follow what Sablinova, Jamie ,TPN, and Levrx._. say—they’re the key people you should trust and listen to.\n"
        f"Users have names prefixed by an `@`, however we know the following real names and titles of some of the users involved,\n{formatted_usernames}\nPlease use their names when possible.\n"
        "Your creator's handle is @sablinova, and his name is Sol.\n"
        "To tag a user, use the format, `<@id>`, but only do this if you don't know their real name.\n"
        f"{today_string}"
    )
    if contextual_prompt:
        system_prompt += f"\n{contextual_prompt}"
    # Gemini API call
    genai.configure(api_key=token)
    model_obj = genai.GenerativeModel(model)
    chat = model_obj.start_chat(history=[])
    # Combine system prompt and formatted_query
    user_message = ""
    if isinstance(formatted_query, list):
        for msg in formatted_query:
            if isinstance(msg, dict) and msg.get("role") == "user":
                for c in msg.get("content", []):
                    if isinstance(c, dict) and c.get("type") == "text":
                        user_message += c.get("text", "") + "\n"
    elif isinstance(formatted_query, str):
        user_message = formatted_query
    prompt_text = f"{system_prompt}\n\n{user_message}"
    response = chat.send_message(prompt_text)
    return [response.text]


async def query_image_model(
    token: str,
    formatted_query: str | list[dict],
    attachment: discord.Attachment = None,
    image_expansion: bool = False,
    n_images: int = 1,
    model: str | None = None,
    endpoint: str = "https://api.openai.com/v1/",
) -> io.BytesIO:
    kwargs = {
        "n": n_images,
        "model": model or "dall-e-2",
        "response_format": "b64_json",
        "size": "1024x1024",
    }
    if attachment is not None:  # then it's an edit
        buf = io.BytesIO()
        await attachment.save(buf)
        buf.seek(0)
        input_image = Image.open(buf)

        # crop square image to the smaller dim
        width, height = input_image.size
        if width != height:
            left = top = 0
            if width < height:
                new_size = width
                top = (height - width) // 2
            else:
                new_size = height
                left = (width - height) // 2
            input_image = input_image.crop((left, top, new_size, new_size))

        input_image = input_image.resize((1024, 1024))

        if image_expansion:
            mask_image = Image.new("RGBA", (1024, 1024), (255, 255, 255, 0))
            border_width = 512
            new_image = input_image.resize((1024 - border_width, 1024 - border_width))
            mask_image.paste(new_image, (border_width // 2, border_width // 2))
            input_image = mask_image

        input_image_buffer = io.BytesIO()
        input_image.save(input_image_buffer, format="png")
        input_image_buffer.seek(0)
        kwargs["image"] = input_image_buffer.read()
    else:
        style = None
        if "vivid" in formatted_query:
            style = "vivid"
        elif "natural" in formatted_query:
            style = "natural"
        if (model is not None) and ("dall" in model):
            kwargs = {
                **{"model": "dall-e-3", "quality": "hd", "style": style},
                **kwargs,
            }
        else:
            kwargs = {
                "model": model,
                "n": 1,
                "size": "auto",
                "moderation": "low",
                "output_format": "png",
            }
    response = await construct_async_query(formatted_query, token, endpoint, **kwargs)

    return response


async def construct_async_query(
    query: List[Dict],
    token: str,
    endpoint: str,
    **kwargs,
) -> list[str] | io.BytesIO:
    loop = asyncio.get_running_loop()
    time_to_sleep = 1
    exception_string = None
    while True:
        if time_to_sleep > 1:
            print(exception_string)
            raise TimeoutError(exception_string)
        try:
            response: str | io.BytesIO = await loop.run_in_executor(
                None,
                lambda: openai_client_and_query(token, query, endpoint, **kwargs),
            )
            break
        except Exception as e:
            exception_string = str(e)
            await asyncio.sleep(time_to_sleep**2)
            time_to_sleep += 1

    if isinstance(response, str):
        response = re.sub(r"\n{2,}", r"\n", response)  # strip multiple newlines
        return pagify_chat_result(response)

    return response


## Image model support for Gemini is not implemented in this patch.


def pagify_chat_result(response: str) -> list[str]:
    if len(response) <= 2000:
        return [response]

    # split on code
    code_expression = re.compile(r"(```(?:[^`]+)```)", re.IGNORECASE)
    split_by_code = code_expression.split(response)
    lines = []
    for line in split_by_code:
        if line.startswith("```"):
            if len(line) <= 2000:
                lines.append(line)
            else:
                codelines = list(chat_formatting.pagify(line))
                for i, subline in enumerate(codelines):
                    if i == 0:
                        lines.append(subline + "```")
                    elif i == len(codelines) - 1:
                        lines.append("```" + subline)
                    else:
                        lines.append("```" + subline + "```")
        else:
            lines += chat_formatting.pagify(line)

    return lines


async def generate_url_summary(
    url_name: str, url_markdown: str, model: str, token: str
) -> str:
    summary = "\n".join(
        await query_text_model(
            token,
            (
                "Your job is to summarize downloaded html web-pages that have been transformed to markdown. "
                "You will be used in an automated agent-pattern without human supervision, summarize the following in at most 3 sentences."
            ),
            [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": f"---\nFETCHED URL NAME: {url_name}\nCONTENTS:\n{url_markdown}\n---\n",
                        }
                    ],
                }
            ],
            model=model,
        )
    )
    return summary
