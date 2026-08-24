"""Сохранение базы в закрытый канал на сервере Discord и восстановление после рестарта.

У файловой системы Render Free нет постоянного диска: каждый рестарт или деплой
начинается с чистого контейнера, и data/bot.db пропадает вместе со всеми /config.
Внешние сервисы не нужны: бот хранит свежий снимок базы одним вложением в своём
закрытом канале «бэкап-базы» (виден только боту и стаффу), а перед стартом
скачивает его обратно. Канал живёт в Discord, поэтому переживает любые рестарты.

Выгрузка происходит вскоре после любой записи в базу (настройки, тикеты,
комнаты — потеря статуса тикета при рестарте ломает кнопки), раз в час
при активности и при остановке бота (Render присылает SIGTERM перед
выключением деплоя). Каких-либо настроек не требуется — используется
обычный токен бота.
"""

import logging
import sqlite3
import tempfile
from pathlib import Path
from typing import Optional

import discord

import db

log = logging.getLogger("tickets.persist")

DB_PATH = Path(__file__).parent.parent / "data" / "bot.db"
# Канал ищем по подстроке в имени, сообщение-хранилище — по подстроке в тексте.
CHANNEL_MARKER = "бэкап-базы"
MESSAGE_MARKER = "Автоматический бэкап базы"
BACKUP_FILENAME = "bot.db"

# Плановая копия — не чаще раза в час; изменение настроек выгружается уже через пару минут.
REGULAR_INTERVAL_SECONDS = 3600
IMPORTANT_DEBOUNCE_SECONDS = 120

_last_backup_message_id: Optional[int] = None


def is_enabled() -> bool:
    # Хранилище в Discord доступно всегда: отдельные токены не нужны.
    return True


def snapshot_to(dest_path: Path) -> None:
    """Консистентная копия живой базы через sqlite3 backup API (не чтение файла под нагрузкой)."""
    src = db.connect()
    dst = sqlite3.connect(dest_path)
    with dst:
        src.backup(dst)
    dst.close()


def _backup_guild(bot) -> Optional[discord.Guild]:
    """Сервер-хранитель: основной (из констант БД), иначе первый известный."""
    return bot.get_guild(db.LEGACY_GUILD_ID) or (bot.guilds[0] if bot.guilds else None)


async def find_or_create_channel(guild: discord.Guild) -> Optional[discord.TextChannel]:
    """Закрытый канал для бэкапов: ищем по имени среди существующих, создаём если нет."""
    for ch in guild.text_channels:
        if CHANNEL_MARKER in ch.name:
            return ch
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        guild.me: discord.PermissionOverwrite(
            view_channel=True, send_messages=True,
            attach_files=True, read_message_history=True,
        ),
    }
    try:
        return await guild.create_text_channel(
            f"🔒 {CHANNEL_MARKER}",
            overwrites=overwrites,
            reason="Хранилище автоматических бэкапов базы",
        )
    except discord.HTTPException:
        log.warning("Не удалось создать канал для бэкапов — проверьте права бота на сервере")
        return None


async def restore_if_missing(bot) -> None:
    """Скачивает последний снимок базы из Discord, если локальной ещё нет —
    ровно этот случай наступает при каждом старте контейнера Render.

    Вызывается из setup_hook до подключения к шлюзу, когда кэша серверов ещё нет,
    поэтому работает напрямую через REST и основной сервер из констант БД.
    """
    global _last_backup_message_id
    if DB_PATH.exists():
        log.info("Локальная база на месте — восстановление из Discord не требуется")
        return

    import aiohttp

    try:
        raw_channels = await bot.http.get_guild_channels(db.LEGACY_GUILD_ID)
    except Exception:
        log.warning("Не получил каналы основного сервера — начинаю с пустой базы", exc_info=True)
        return
    channel_id = next(
        (
            c["id"] for c in raw_channels
            if c.get("type") == discord.ChannelType.text.value
            and CHANNEL_MARKER in (c.get("name") or "")
        ),
        None,
    )
    if channel_id is None:
        log.info("Канала бэкапов ещё нет — начинаю с пустой базы")
        return

    try:
        messages = await bot.http.get_channel_messages(channel_id, limit=50)
    except Exception:
        log.warning("Не прочитал канал бэкапов — начинаю с пустой базы", exc_info=True)
        return

    for msg in messages:  # от новых к старым
        if MESSAGE_MARKER not in (msg.get("content") or "") or not msg.get("attachments"):
            continue
        url = msg["attachments"][0].get("url")
        if not url:
            continue
        try:
            async with aiohttp.ClientSession(total=30) as session:
                async with session.get(url) as resp:
                    if resp.status != 200:
                        continue
                    content = await resp.read()
        except Exception:
            log.warning("Не скачал копию базы из Discord", exc_info=True)
            return
        if not content.startswith(b"SQLite format 3"):
            log.warning("Вложение в канале бэкапов не похоже на SQLite-базу — пропускаю")
            continue
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        DB_PATH.write_bytes(content)
        _last_backup_message_id = int(msg["id"])
        log.info(
            "База восстановлена из Discord (%d байт) — настройки и тикеты на месте",
            len(content),
        )
        return
    log.info("Копий базы в канале бэкапов не нашлось — начинаю с пустой")


async def upload(bot, reason: str) -> bool:
    """Выгружает свежий снимок базы в канал бэкапов. Одно сообщение редактируется
    на месте, чтобы канал не засорялся. False — не удалось."""
    global _last_backup_message_id
    guild = _backup_guild(bot)
    if guild is None:
        return False
    channel = await find_or_create_channel(guild)
    if channel is None:
        return False

    try:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir) / BACKUP_FILENAME
            snapshot_to(tmp_path)
            payload_file = discord.File(tmp_path, filename=BACKUP_FILENAME)

            message = None
            if _last_backup_message_id:
                try:
                    message = await channel.fetch_message(_last_backup_message_id)
                except discord.NotFound:
                    message = None
            if message is None:
                async for old in channel.history(limit=20):
                    if MESSAGE_MARKER in old.content and old.attachments:
                        message = old
                        break

            text = f"🗄️ {MESSAGE_MARKER} ({reason})"
            if message is not None:
                await message.edit(content=text, attachments=[payload_file])
            else:
                message = await channel.send(content=text, file=payload_file)
            _last_backup_message_id = message.id
        log.info("База выгружена в канал бэкапов (%s)", reason)
        return True
    except Exception:
        log.warning("Не удалось выгрузить бэкап в Discord (%s)", reason, exc_info=True)
        return False
