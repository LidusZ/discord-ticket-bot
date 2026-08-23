"""HTML-транскрипты тикетов. Чистый Python без внешних зависимостей."""

import html
import io
from datetime import datetime, timezone
from typing import Any

import discord

MAX_MESSAGES = 1000

_STYLE = """
body { margin: 0; font-family: 'Segoe UI', Arial, sans-serif; background: #1e1f22; color: #dbdee1; }
.wrap { max-width: 900px; margin: 0 auto; padding: 24px 16px 48px; }
h1 { font-size: 20px; color: #fff; }
.meta { background: #2b2d31; border-radius: 8px; padding: 12px 16px; margin: 12px 0 24px; font-size: 14px; line-height: 1.7; }
.msg { display: flex; gap: 12px; padding: 10px 8px; border-radius: 6px; }
.msg:hover { background: #2b2d31; }
.avatar { flex: 0 0 40px; height: 40px; border-radius: 50%; color: #fff; font-weight: 700;
          display: flex; align-items: center; justify-content: center; font-size: 18px; }
.body { min-width: 0; }
.head { font-size: 14px; }
.author { color: #fff; font-weight: 600; }
.time { color: #949ba4; font-size: 12px; margin-left: 8px; }
.text { font-size: 15px; line-height: 1.45; white-space: pre-wrap; overflow-wrap: anywhere; }
.attach a { color: #00a8fc; font-size: 13px; text-decoration: none; }
.embed { border-left: 4px solid #4e5058; background: #2b2d31; border-radius: 4px;
         padding: 8px 12px; margin-top: 6px; font-size: 14px; }
.embed .etitle { color: #fff; font-weight: 600; }
.embed .efield-name { color: #fff; font-weight: 600; margin-top: 6px; }
.bot-tag { background: #5865f2; color: #fff; font-size: 10px; font-weight: 600;
           border-radius: 3px; padding: 1px 4px; vertical-align: middle; margin-left: 4px; }
.empty { color: #949ba4; font-style: italic; }
"""


def _initial_color(name: str) -> str:
    hue = sum(ord(ch) for ch in name) % 360
    return f"hsl({hue}, 65%, 45%)"


def _render_embed(embed: Any) -> str:
    parts = []
    if embed.title:
        parts.append(f'<div class="etitle">{html.escape(embed.title)}</div>')
    if embed.description:
        parts.append(f'<div>{html.escape(embed.description)}</div>')
    for field in embed.fields:
        parts.append(f'<div class="efield-name">{html.escape(field.name)}</div>'
                     f'<div>{html.escape(field.value)}</div>')
    if embed.footer and embed.footer.text:
        parts.append(f'<div class="time">{html.escape(embed.footer.text)}</div>')
    return '<div class="embed">' + "".join(parts) + "</div>" if parts else ""


def render_transcript(channel_name: str, meta: dict[str, str], messages: list[Any]) -> bytes:
    """Собирает HTML-файл. meta — пары «заголовок → значение» для шапки.

    messages — объекты discord.Message в хронологическом порядке.
    """
    rows = []
    for msg in messages:
        author = msg.author.display_name
        avatar = f'<div class="avatar" style="background:{_initial_color(author)}">{html.escape(author[:1].upper())}</div>'
        bot_tag = '<span class="bot-tag">БОТ</span>' if msg.author.bot else ""
        time_str = msg.created_at.strftime("%d.%m.%Y %H:%M:%S")
        head = (f'<div class="head"><span class="author">{html.escape(author)}</span>'
                f'{bot_tag}<span class="time">{time_str}</span></div>')

        chunks = []
        if msg.content:
            chunks.append(f'<div class="text">{html.escape(msg.content)}</div>')
        for att in msg.attachments:
            chunks.append(f'<div class="attach"><a href="{html.escape(att.url)}">'
                          f'📎 {html.escape(att.filename)}</a></div>')
        for embed in msg.embeds:
            rendered = _render_embed(embed)
            if rendered:
                chunks.append(rendered)
        if not chunks:
            continue
        rows.append(f'<div class="msg">{avatar}<div class="body">{head}{"".join(chunks)}</div></div>')

    body = "\n".join(rows) or '<p class="empty">В тикете нет сообщений.</p>'
    meta_rows = "".join(
        f"<div><b>{html.escape(key)}:</b> {value}</div>" for key, value in meta.items()
    )
    page = f"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<title>Транскрипт — {html.escape(channel_name)}</title>
<style>{_STYLE}</style>
</head>
<body>
<div class="wrap">
<h1>📄 Транскрипт тикета «{html.escape(channel_name)}»</h1>
<div class="meta">{meta_rows}</div>
{body}
</div>
</body>
</html>"""
    return page.encode("utf-8")


async def build_transcript_file(channel: discord.TextChannel, meta: dict[str, str]) -> discord.File:
    """Собирает транскрипт канала и возвращает готовый discord.File."""
    messages = [msg async for msg in channel.history(limit=MAX_MESSAGES)]
    messages.reverse()
    data = render_transcript(channel.name, meta, messages)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return discord.File(io.BytesIO(data), filename=f"transcript-{channel.name}-{stamp}.html")
