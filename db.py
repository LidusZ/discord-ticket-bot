"""SQLite-хранилище бота: настройки серверов, тикеты, оценки.

Все вызовы синхронные и выполняются в цикле событий discord.py.
Это осознанно: операции точечные (по индексу) и занимают доли миллисекунды,
а бот рассчитан на один-несколько серверов. Соединение одно и живёт всё
время работы процесса, поэтому гонок между потоками нет.
"""

import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Optional

DB_PATH = Path(__file__).parent / "data" / "bot.db"

# --- Настройки старой версии бота (были захардкожены в cogs/ticket.py). ---
# Импортируются в базу один раз при первом запуске новой версии,
# чтобы переезд не потребовал ручной перенастройки.
LEGACY_GUILD_ID = 1537536848673374319
LEGACY_CATEGORY_ID = 1540863516163186829
LEGACY_LOG_CHANNEL_ID = 1540863605455462460
LEGACY_STAFF_ROLE_IDS = [
    1537536849298460784,
    1537547996890271775,
    1537536849290199077,
    1537536849290199075,
    1537536849290199076,
]

# Категории тикетов по умолчанию для нового сервера.
DEFAULT_CATEGORIES = [
    {"label": "Поддержка", "emoji": "🎧", "description": "Общие вопросы и помощь"},
]

# Подписи каналов-счётчиков участников по умолчанию ({count} — число).
MS_DEFAULT_LABELS = {
    "all": "👥 All Members: {count}",
    "humans": "👤 Members: {count}",
    "bots": "🤖 Bots: {count}",
}

_conn: Optional[sqlite3.Connection] = None

# Метки времени последней записи в базу и последнего изменения настроек —
# их читает utils/persist.py, чтобы решать, когда выгружать копию базы в GitHub.
last_write_ts = 0.0
config_write_ts = 0.0


class _TrackingConnection(sqlite3.Connection):
    """Соединение, отмечающее время каждой фиксации. Все записи в базе идут
    через commit(), поэтому обёртки здесь достаточно, чтобы заметить любую
    из них без правки каждого отдельного вызова."""

    def commit(self):
        super().commit()
        global last_write_ts
        last_write_ts = time.time()


def connect() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(DB_PATH, factory=_TrackingConnection)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA foreign_keys = ON")
    return _conn


def init_db() -> None:
    """Создаёт таблицы и переносит настройки старой версии (один раз)."""
    conn = connect()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS guild_config (
            guild_id            INTEGER PRIMARY KEY,
            category_id         INTEGER,
            log_channel_id      INTEGER,
            staff_role_ids      TEXT NOT NULL DEFAULT '[]',
            ticket_categories   TEXT NOT NULL DEFAULT '[]',
            max_open_per_user   INTEGER NOT NULL DEFAULT 1,
            cooldown_seconds    INTEGER NOT NULL DEFAULT 60,
            autoclose_warn_hours INTEGER NOT NULL DEFAULT 24,
            autoclose_hours     INTEGER NOT NULL DEFAULT 48,
            counter             INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS tickets (
            channel_id   INTEGER PRIMARY KEY,
            guild_id     INTEGER NOT NULL,
            owner_id     INTEGER NOT NULL,
            category     TEXT NOT NULL DEFAULT '',
            number       INTEGER NOT NULL,
            claimed_by   INTEGER,
            status       TEXT NOT NULL DEFAULT 'open',
            created_at   INTEGER NOT NULL,
            closed_at    INTEGER,
            warn_sent_at INTEGER
        );
        CREATE INDEX IF NOT EXISTS idx_tickets_guild_status ON tickets(guild_id, status);
        CREATE INDEX IF NOT EXISTS idx_tickets_owner ON tickets(guild_id, owner_id, status);

        CREATE TABLE IF NOT EXISTS ratings (
            channel_id INTEGER PRIMARY KEY,
            guild_id   INTEGER NOT NULL,
            user_id    INTEGER NOT NULL,
            stars      INTEGER NOT NULL CHECK (stars BETWEEN 1 AND 5),
            rated_at   INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS rooms (
            voice_channel_id INTEGER PRIMARY KEY,
            guild_id         INTEGER NOT NULL,
            text_channel_id  INTEGER UNIQUE,
            lobby_channel_id INTEGER UNIQUE,
            panel_message_id INTEGER,
            owner_id         INTEGER NOT NULL,
            is_private       INTEGER NOT NULL DEFAULT 0,
            lobby_enabled    INTEGER NOT NULL DEFAULT 0,
            chat_hidden      INTEGER NOT NULL DEFAULT 1,
            created_at       INTEGER NOT NULL,
            empty_since      INTEGER
        );
        CREATE INDEX IF NOT EXISTS idx_rooms_guild ON rooms(guild_id);
        """
    )

    # Миграция настроек Room Creator: CREATE TABLE IF NOT EXISTS не добавляет
    # колонки в уже существующую guild_config, поэтому добавляем их точечно.
    for ddl in (
        "ALTER TABLE guild_config ADD COLUMN voice_trigger_id INTEGER",
        "ALTER TABLE guild_config ADD COLUMN voice_category_id INTEGER",
        "ALTER TABLE guild_config ADD COLUMN voice_name_template TEXT",
        "ALTER TABLE guild_config ADD COLUMN voice_user_limit INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE guild_config ADD COLUMN room_empty_minutes INTEGER NOT NULL DEFAULT 10",
        # Счётчики участников (голосовые каналы All Members / Members / Bots).
        "ALTER TABLE guild_config ADD COLUMN ms_enabled INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE guild_config ADD COLUMN ms_category_id INTEGER",
        "ALTER TABLE guild_config ADD COLUMN ms_ch_all INTEGER",
        "ALTER TABLE guild_config ADD COLUMN ms_ch_humans INTEGER",
        "ALTER TABLE guild_config ADD COLUMN ms_ch_bots INTEGER",
        "ALTER TABLE guild_config ADD COLUMN ms_labels TEXT",
        # Периодическая отправка «.Дм» в голосовой канал (/startsd, /stopsd).
        "ALTER TABLE guild_config ADD COLUMN sd_enabled INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE guild_config ADD COLUMN sd_channel_id INTEGER",
    ):
        try:
            conn.execute(ddl)
        except sqlite3.OperationalError:
            pass  # колонка уже существует

    if not conn.execute("SELECT 1 FROM guild_config LIMIT 1").fetchone():
        if LEGACY_GUILD_ID and LEGACY_CATEGORY_ID:
            conn.execute(
                "INSERT INTO guild_config (guild_id, category_id, log_channel_id, staff_role_ids, ticket_categories)"
                " VALUES (?, ?, ?, ?, ?)",
                (
                    LEGACY_GUILD_ID,
                    LEGACY_CATEGORY_ID,
                    LEGACY_LOG_CHANNEL_ID,
                    json.dumps(LEGACY_STAFF_ROLE_IDS),
                    json.dumps(DEFAULT_CATEGORIES, ensure_ascii=False),
                ),
            )
            print(f"[db] Настройки старой версии импортированы для сервера {LEGACY_GUILD_ID}")

    conn.commit()
    print(f"[db] База готова: {DB_PATH}")


# === НАСТРОЙКИ СЕРВЕРА =======================================================

def ensure_guild(guild_id: int) -> None:
    conn = connect()
    conn.execute("INSERT OR IGNORE INTO guild_config (guild_id, ticket_categories) VALUES (?, ?)",
                 (guild_id, json.dumps(DEFAULT_CATEGORIES, ensure_ascii=False)))
    conn.commit()


def get_config(guild_id: int) -> dict[str, Any]:
    row = connect().execute("SELECT * FROM guild_config WHERE guild_id = ?", (guild_id,)).fetchone()
    if row is None:
        ensure_guild(guild_id)
        row = connect().execute("SELECT * FROM guild_config WHERE guild_id = ?", (guild_id,)).fetchone()
    cfg = dict(row)
    cfg["staff_role_ids"] = json.loads(cfg["staff_role_ids"])
    categories = json.loads(cfg["ticket_categories"])
    cfg["ticket_categories"] = categories or DEFAULT_CATEGORIES
    return cfg


def set_category(guild_id: int, channel_id: int) -> None:
    _set(guild_id, "category_id", channel_id)


def set_log_channel(guild_id: int, channel_id: int) -> None:
    _set(guild_id, "log_channel_id", channel_id)


def set_limits(guild_id: int, max_open: int, cooldown_seconds: int) -> None:
    _set(guild_id, "max_open_per_user", max_open)
    _set(guild_id, "cooldown_seconds", cooldown_seconds)


def set_autoclose(guild_id: int, warn_hours: int, close_hours: int) -> None:
    _set(guild_id, "autoclose_warn_hours", warn_hours)
    _set(guild_id, "autoclose_hours", close_hours)


def staff_add(guild_id: int, role_id: int) -> bool:
    """Добавляет роль персонала. False, если роль уже есть."""
    roles = get_config(guild_id)["staff_role_ids"]
    if role_id in roles:
        return False
    roles.append(role_id)
    _set(guild_id, "staff_role_ids", json.dumps(roles))
    return True


def staff_remove(guild_id: int, role_id: int) -> bool:
    roles = get_config(guild_id)["staff_role_ids"]
    if role_id not in roles:
        return False
    roles.remove(role_id)
    _set(guild_id, "staff_role_ids", json.dumps(roles))
    return True


def category_add(guild_id: int, label: str, emoji: str, description: str) -> str:
    """Добавляет категорию панели. Возвращает 'ok' или текст причины отказа."""
    cats = get_config(guild_id)["ticket_categories"]
    if len(cats) >= 25:
        return "Достигнут лимит Discord — не больше 25 категорий на панели."
    if any(c["label"].lower() == label.lower() for c in cats):
        return f"Категория «{label}» уже есть."
    cats.append({"label": label, "emoji": emoji or "🎫", "description": description})
    _set(guild_id, "ticket_categories", json.dumps(cats, ensure_ascii=False))
    return "ok"


def category_remove(guild_id: int, label: str) -> str:
    cats = get_config(guild_id)["ticket_categories"]
    found = next((c for c in cats if c["label"].lower() == label.lower()), None)
    if found is None:
        return f"Категория «{label}» не найдена."
    if len(cats) == 1:
        return "Нельзя удалить последнюю категорию."
    cats.remove(found)
    _set(guild_id, "ticket_categories", json.dumps(cats, ensure_ascii=False))
    return "ok"


def all_guild_ids() -> list[int]:
    rows = connect().execute("SELECT guild_id FROM guild_config").fetchall()
    return [r["guild_id"] for r in rows]


def _set(guild_id: int, field: str, value: Any) -> None:
    ensure_guild(guild_id)
    conn = connect()
    conn.execute(f"UPDATE guild_config SET {field} = ? WHERE guild_id = ?", (value, guild_id))
    conn.commit()
    global config_write_ts
    config_write_ts = time.time()


def next_ticket_number(guild_id: int) -> int:
    conn = connect()
    cur = conn.execute(
        "UPDATE guild_config SET counter = counter + 1 WHERE guild_id = ? RETURNING counter",
        (guild_id,),
    )
    value = cur.fetchone()[0]
    conn.commit()
    return value


# === ТИКЕТЫ ==================================================================

def create_ticket(channel_id: int, guild_id: int, owner_id: int, category: str, number: int) -> None:
    conn = connect()
    conn.execute(
        "INSERT INTO tickets (channel_id, guild_id, owner_id, category, number, status, created_at)"
        " VALUES (?, ?, ?, ?, ?, 'open', ?)",
        (channel_id, guild_id, owner_id, category, number, int(time.time())),
    )
    conn.commit()


def get_ticket(channel_id: int) -> Optional[dict]:
    row = connect().execute("SELECT * FROM tickets WHERE channel_id = ?", (channel_id,)).fetchone()
    return dict(row) if row else None


def find_open_ticket(guild_id: int, user_id: int) -> Optional[dict]:
    row = connect().execute(
        "SELECT * FROM tickets WHERE guild_id = ? AND owner_id = ? AND status = 'open' ORDER BY created_at DESC",
        (guild_id, user_id),
    ).fetchone()
    return dict(row) if row else None


def count_open_tickets(guild_id: int, user_id: int) -> int:
    row = connect().execute(
        "SELECT COUNT(*) FROM tickets WHERE guild_id = ? AND owner_id = ? AND status = 'open'",
        (guild_id, user_id),
    ).fetchone()
    return row[0]


def seconds_since_last_ticket(guild_id: int, user_id: int) -> Optional[int]:
    row = connect().execute(
        "SELECT MAX(created_at) FROM tickets WHERE guild_id = ? AND owner_id = ?",
        (guild_id, user_id),
    ).fetchone()
    if row[0] is None:
        return None
    return int(time.time()) - row[0]


def set_claimed(channel_id: int, staff_id: Optional[int]) -> None:
    _update_ticket(channel_id, claimed_by=staff_id)


def mark_closed(channel_id: int) -> None:
    _update_ticket(channel_id, status="closed", closed_at=int(time.time()), warn_sent_at=None)


def mark_reopened(channel_id: int) -> None:
    _update_ticket(channel_id, status="open", closed_at=None, warn_sent_at=None)


def set_warned(channel_id: int, warned: bool) -> None:
    _update_ticket(channel_id, warn_sent_at=int(time.time()) if warned else None)


def delete_ticket(channel_id: int) -> None:
    conn = connect()
    conn.execute("DELETE FROM ratings WHERE channel_id = ?", (channel_id,))
    conn.execute("DELETE FROM tickets WHERE channel_id = ?", (channel_id,))
    conn.commit()


def open_tickets(guild_id: int) -> list[dict]:
    rows = connect().execute(
        "SELECT * FROM tickets WHERE guild_id = ? AND status = 'open'", (guild_id,)
    ).fetchall()
    return [dict(r) for r in rows]


# === ОЦЕНКИ И СТАТИСТИКА =====================================================

def add_rating(channel_id: int, guild_id: int, user_id: int, stars: int) -> bool:
    """Сохраняет оценку. False — за этот тикет уже голосовали."""
    conn = connect()
    exists = conn.execute("SELECT 1 FROM ratings WHERE channel_id = ?", (channel_id,)).fetchone()
    if exists:
        return False
    conn.execute(
        "INSERT INTO ratings (channel_id, guild_id, user_id, stars, rated_at) VALUES (?, ?, ?, ?, ?)",
        (channel_id, guild_id, user_id, stars, int(time.time())),
    )
    conn.commit()
    return True


def ticket_rating(channel_id: int) -> Optional[int]:
    row = connect().execute("SELECT stars FROM ratings WHERE channel_id = ?", (channel_id,)).fetchone()
    return row[0] if row else None


def latest_closed_unrated(user_id: int) -> Optional[dict]:
    """Последний закрытый тикет пользователя, за который он ещё не голосовал."""
    row = connect().execute(
        "SELECT * FROM tickets"
        " WHERE owner_id = ? AND status = 'closed'"
        "   AND NOT EXISTS (SELECT 1 FROM ratings r WHERE r.channel_id = tickets.channel_id)"
        " ORDER BY closed_at DESC LIMIT 1",
        (user_id,),
    ).fetchone()
    return dict(row) if row else None


def stats_overview(guild_id: int, since_ts: int) -> dict:
    conn = connect()
    opened, = conn.execute(
        "SELECT COUNT(*) FROM tickets WHERE guild_id = ? AND created_at >= ?",
        (guild_id, since_ts),
    ).fetchone()
    closed, avg_close_seconds = conn.execute(
        "SELECT COUNT(*), AVG(closed_at - created_at) FROM tickets"
        " WHERE guild_id = ? AND status = 'closed' AND closed_at >= ?",
        (guild_id, since_ts),
    ).fetchone()
    open_now, = conn.execute(
        "SELECT COUNT(*) FROM tickets WHERE guild_id = ? AND status = 'open'",
        (guild_id,),
    ).fetchone()
    avg_stars, votes = conn.execute(
        "SELECT AVG(stars), COUNT(*) FROM ratings WHERE guild_id = ? AND rated_at >= ?",
        (guild_id, since_ts),
    ).fetchone()
    top_claims = conn.execute(
        "SELECT claimed_by, COUNT(*) AS cnt FROM tickets"
        " WHERE guild_id = ? AND claimed_by IS NOT NULL AND created_at >= ?"
        " GROUP BY claimed_by ORDER BY cnt DESC LIMIT 5",
        (guild_id, since_ts),
    ).fetchall()
    return {
        "opened": opened,
        "closed": closed,
        "open_now": open_now,
        "avg_close_seconds": avg_close_seconds,
        "avg_stars": avg_stars,
        "votes": votes,
        "top_claims": [(r["claimed_by"], r["cnt"]) for r in top_claims],
    }


def _update_ticket(channel_id: int, **fields: Any) -> None:
    keys = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [channel_id]
    conn = connect()
    conn.execute(f"UPDATE tickets SET {keys} WHERE channel_id = ?", values)
    conn.commit()


# === ГОЛОСОВЫЕ КОМНАТЫ (ROOM CREATOR) ========================================

def set_voice_trigger(guild_id: int, channel_id: int) -> None:
    _set(guild_id, "voice_trigger_id", channel_id)


def set_voice_category(guild_id: int, channel_id: int) -> None:
    _set(guild_id, "voice_category_id", channel_id)


def set_voice_defaults(guild_id: int, name_template: str, user_limit: int) -> None:
    _set(guild_id, "voice_name_template", name_template)
    _set(guild_id, "voice_user_limit", user_limit)


def set_room_empty_minutes(guild_id: int, minutes: int) -> None:
    _set(guild_id, "room_empty_minutes", minutes)


def create_room(
    voice_channel_id: int,
    guild_id: int,
    text_channel_id: int,
    owner_id: int,
    is_private: bool = False,
    chat_hidden: bool = True,
) -> None:
    conn = connect()
    conn.execute(
        "INSERT INTO rooms (voice_channel_id, guild_id, text_channel_id, owner_id,"
        " is_private, chat_hidden, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (voice_channel_id, guild_id, text_channel_id, owner_id,
         int(is_private), int(chat_hidden), int(time.time())),
    )
    conn.commit()


def get_room(voice_channel_id: int) -> Optional[dict]:
    row = connect().execute("SELECT * FROM rooms WHERE voice_channel_id = ?", (voice_channel_id,)).fetchone()
    return dict(row) if row else None


def find_room_by_text(channel_id: int) -> Optional[dict]:
    row = connect().execute("SELECT * FROM rooms WHERE text_channel_id = ?", (channel_id,)).fetchone()
    return dict(row) if row else None


def find_room_by_lobby(channel_id: int) -> Optional[dict]:
    row = connect().execute("SELECT * FROM rooms WHERE lobby_channel_id = ?", (channel_id,)).fetchone()
    return dict(row) if row else None


def find_room_of_owner(guild_id: int, user_id: int) -> Optional[dict]:
    row = connect().execute(
        "SELECT * FROM rooms WHERE guild_id = ? AND owner_id = ?", (guild_id, user_id)
    ).fetchone()
    return dict(row) if row else None


def update_room(voice_channel_id: int, **fields: Any) -> None:
    if not fields:
        return
    keys = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [voice_channel_id]
    conn = connect()
    conn.execute(f"UPDATE rooms SET {keys} WHERE voice_channel_id = ?", values)
    conn.commit()


def delete_room(voice_channel_id: int) -> None:
    conn = connect()
    conn.execute("DELETE FROM rooms WHERE voice_channel_id = ?", (voice_channel_id,))
    conn.commit()


def guild_rooms(guild_id: int) -> list[dict]:
    rows = connect().execute("SELECT * FROM rooms WHERE guild_id = ?", (guild_id,)).fetchall()
    return [dict(r) for r in rows]


def all_rooms() -> list[dict]:
    rows = connect().execute("SELECT * FROM rooms").fetchall()
    return [dict(r) for r in rows]


# === СЧЁТЧИКИ УЧАСТНИКОВ =====================================================

def set_memberstats_enabled(guild_id: int, enabled: bool) -> None:
    _set(guild_id, "ms_enabled", int(enabled))


def set_memberstats_category(guild_id: int, category_id: Optional[int]) -> None:
    _set(guild_id, "ms_category_id", category_id)


def set_memberstats_channels(guild_id: int, ch_all: Optional[int], humans: Optional[int], bots: Optional[int]) -> None:
    _set(guild_id, "ms_ch_all", ch_all)
    _set(guild_id, "ms_ch_humans", humans)
    _set(guild_id, "ms_ch_bots", bots)


def set_memberstat_labels(guild_id: int, labels: dict) -> None:
    _set(guild_id, "ms_labels", json.dumps(labels, ensure_ascii=False))


def get_memberstat_labels(guild_id: int) -> dict:
    """Подписи каналов-счётчиков; отсутствующие ключи берутся из дефолта."""
    cfg = get_config(guild_id)
    raw = cfg.get("ms_labels")
    labels = dict(MS_DEFAULT_LABELS)
    if raw:
        try:
            stored = json.loads(raw)
            if isinstance(stored, dict):
                labels.update({k: v for k, v in stored.items() if v})
        except (ValueError, TypeError):
            pass
    return labels


# === ПЕРИОДИЧЕСКОЕ СООБЩЕНИЕ В ГОЛОСОВОЙ КАНАЛ (SD) ==========================

def set_sd_enabled(guild_id: int, enabled: bool) -> None:
    _set(guild_id, "sd_enabled", int(enabled))


def set_sd_channel(guild_id: int, channel_id: Optional[int]) -> None:
    _set(guild_id, "sd_channel_id", channel_id)
