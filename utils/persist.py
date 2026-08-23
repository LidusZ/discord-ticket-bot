"""Сохранение базы в приватный репозиторий GitHub и восстановление после рестарта.

У файловой системы Render Free нет постоянного диска: каждый рестарт или деплой
начинается с чистого контейнера, и data/bot.db пропадает вместе со всеми /config.
Модуль решает это без сторонних сервисов: перед стартом база скачивается из
резервного репозитория (если локальной ещё нет), а дальше выгружается обратно —
вскоре после изменения настроек, раз в час при любой активности и при остановке
бота (Render присылает SIGTERM перед выключением).

Настройка (один раз, Render → Environment):
  GITHUB_TOKEN  — fine-grained токен с правом «Contents: Read and write»
                  только на резервный репозиторий;
  BACKUP_REPO   — репозиторий для копий в формате «owner/name»
                  (например LidusZ/discord-ticket-bot-backup, лучше приватный).

Без этих переменных модуль просто выключен — локальная разработка не страдает.
"""

import base64
import logging
import os
import sqlite3
import tempfile
from pathlib import Path

import db

log = logging.getLogger("tickets.persist")

DB_PATH = Path(__file__).parent.parent / "data" / "bot.db"
BACKUP_FILE = "bot.db"
BRANCH = os.environ.get("BACKUP_BRANCH", "main")
CONTENTS_URL = "https://api.github.com/repos/{repo}/contents/" + BACKUP_FILE

# Плановая копия — не чаще раза в час; изменение настроек выгружается уже через пару минут.
REGULAR_INTERVAL_SECONDS = 3600
IMPORTANT_DEBOUNCE_SECONDS = 120

_TOKEN = os.environ.get("GITHUB_TOKEN")
_REPO = os.environ.get("BACKUP_REPO")


def is_enabled() -> bool:
    return bool(_TOKEN and _REPO)


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {_TOKEN}",
        "Accept": "application/vnd.github+json",
    }


def snapshot_to(dest_path: Path) -> None:
    """Консистентная копия живой базы через sqlite3 backup API (не чтение файла под нагрузкой)."""
    src = db.connect()
    dst = sqlite3.connect(dest_path)
    with dst:
        src.backup(dst)
    dst.close()


async def restore_if_missing() -> None:
    """Скачивает базу из резервного репозитория, если локальной ещё нет —
    ровно этот случай наступает при каждом старте контейнера Render."""
    if not is_enabled():
        log.info(
            "Бэкап в GitHub выключен (задайте BACKUP_REPO и GITHUB_TOKEN) — "
            "настройки будут пропадать при рестартах"
        )
        return
    if DB_PATH.exists():
        log.info("Локальная база на месте — восстановление из GitHub не требуется")
        return

    import aiohttp

    try:
        async with aiohttp.ClientSession(total=30) as session:
            async with session.get(
                CONTENTS_URL.format(repo=_REPO), params={"ref": BRANCH}, headers=_headers()
            ) as resp:
                if resp.status == 404:
                    log.info("В резервном репозитории ещё нет копии базы — начинаю с пустой")
                    return
                resp.raise_for_status()
                payload = await resp.json()
    except Exception:
        log.warning("Не удалось скачать бэкап из GitHub — продолжаю с пустой базой", exc_info=True)
        return

    content = base64.b64decode(payload.get("content") or "")
    if not content.startswith(b"SQLite format 3"):
        log.warning("Файл в резервном репозитории не похож на SQLite-базу — не восстанавливаю")
        return
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    DB_PATH.write_bytes(content)
    log.info("База восстановлена из GitHub (%d байт) — настройки и тикеты на месте", len(content))


async def upload(reason: str) -> bool:
    """Выгружает свежий снимок базы в резервный репозиторий. False — не удалось или выключено."""
    if not is_enabled():
        return False

    import aiohttp

    try:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir) / BACKUP_FILE
            snapshot_to(tmp_path)
            encoded = base64.b64encode(tmp_path.read_bytes()).decode("ascii")
    except Exception:
        log.warning("Не удалось собрать снимок базы для бэкапа", exc_info=True)
        return False

    body = {"message": f"Бэкап базы: {reason}", "content": encoded, "branch": BRANCH}
    try:
        async with aiohttp.ClientSession(total=30) as session:
            # sha текущего файла нужен, чтобы обновить существующую копию (создать — можно без него).
            async with session.get(
                CONTENTS_URL.format(repo=_REPO), params={"ref": BRANCH}, headers=_headers()
            ) as resp:
                if resp.status == 200:
                    body["sha"] = (await resp.json()).get("sha")
                elif resp.status != 404:
                    text = await resp.text()
                    log.warning("GitHub не отдал состояние бэкапа (%s): %s", resp.status, text[:300])
                    return False
            async with session.put(
                CONTENTS_URL.format(repo=_REPO),
                headers={**_headers(), "Content-Type": "application/json"},
                json=body,
            ) as resp:
                if resp.status in (200, 201):
                    log.info("База выгружена в GitHub (%s)", reason)
                    return True
                text = await resp.text()
                log.warning("GitHub не принял бэкап (%s): %s", resp.status, text[:300])
                return False
    except Exception:
        log.warning("Ошибка выгрузки бэкапа в GitHub", exc_info=True)
        return False
