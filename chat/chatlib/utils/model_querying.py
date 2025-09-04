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
) -> list[str] | io.BytesIO:
    if user_names is None:
        user_names = {}
    formatted_usernames = pformat(user_names)

    today_string = dt.datetime.now().strftime(
        "The date is %A, %B %m, %Y. The time is %I:%M %p %Z"
    )

    system_prefix = [
        {
            "role": "system",
            "content": [
                {
                    "type": "text",
                    "text": prompt,
                },
                {
                    "type": "text",
                    "text": (
                        "Users have names prefixed by an `@`, however we know the following real names and titles of "
                        f"some of the users involved,\n{formatted_usernames}\nPlease use their names when possible.\n"
                        "Your creator's handle is @erisaurus, and her name is Zoe.\n"
                        "To tag a user, use the format, `<@id>`, but only do this if you don't know their real name.\n"
                        f"{today_string}"
                    ),
                },
            ],
        },
    ]
    if contextual_prompt != "":
        system_prefix[0]["content"].append({"type": "text", "text": contextual_prompt})
    
    response = await construct_async_query(
        system_prefix + formatted_query,
        token,
        model,
    )
    return response


async def query_image_model(
    token: str,
    formatted_query: str | list[dict],
    attachment: discord.Attachment = None,
    image_expansion: bool = False,
    n_images: int = 1,
    model: str | None = None,
    endpoint: str = None,
) -> io.BytesIO:
    # Note: Gemini doesn't have image generation capabilities like DALL-E
    # This would need to be handled differently or use a different service
    raise NotImplementedError("Image generation not available with Gemini. Consider using a different service for this feature.")


async def construct_async_query(
    query: List[Dict],
    token: str,
    model: str = "gemini-1.5-flash",
) -> list[str]:
    loop = asyncio.get_running_loop()
    time_to_sleep = 1
    exception_string = None
    while True:
        if time_to_sleep > 1:
            print(exception_string)
            raise TimeoutError(exception_string)
        try:
            response: str = await loop.run_in_executor(
                None,
                lambda: gemini_client_and_query(token, query, model),
            )
            break
        except Exception as e:
            exception_string = str(e)
            await asyncio.sleep(time_to_sleep**2)
            time_to_sleep += 1

    response = re.sub(r"\n{2,}", r"\n", response)  # strip multiple newlines
    return pagify_chat_result(response)


def gemini_client_and_query(
    token: str,
    messages: list[dict],
    model: str = "gemini-1.5-flash",
) -> str:
    genai.configure(api_key=token)
    model_instance = genai.GenerativeModel(model)
    
    # Convert messages to Gemini format
    conversation_parts = []
    for message in messages:
        if message["role"] == "system":
            # Add system message as context
            for content in message["content"]:
                if content["type"] == "text":
                    conversation_parts.append(content["text"])
        elif message["role"] == "user":
            user_text = ""
            if "name" in message:
                user_text += f"{message['name']}: "
            for content in message["content"]:
                if content["type"] == "text":
                    user_text += content["text"]
            conversation_parts.append(user_text)
        elif message["role"] == "assistant":
            assistant_text = ""
            if "name" in message:
                assistant_text += f"{message['name']}: "
            for content in message["content"]:
                if content["type"] == "text":
                    assistant_text += content["text"]
            conversation_parts.append(assistant_text)
    
    # Join all parts for the prompt
    prompt = "\n\n".join(conversation_parts)
    
    response = model_instance.generate_content(prompt)
    return response.text


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