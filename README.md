# Discord Ticket Bot

Тикет-бот для поддержки на сервере Discord. Python + discord.py, база — SQLite.
Всё работает бесплатно: код открыт, хостинг — Render Free, анти-сон — UptimeRobot.

## Возможности

- **Панель создания тикетов** (`/setup`): кнопка при одной категории или выпадающее меню до 25 категорий («Поддержка», «Жалоба на игрока», «Апелляция»…).
- **Приватный канал** `ticket-0001-имя`: видят только автор, персонал и бот; причина обращения — в модальном окне при создании.
- **Защита от спама**: лимит открытых тикетов на пользователя и кулдаун между созданием (настраивается).
- **Кнопки в тикете**: 🔒 Закрыть (с подтверждением), 🙋 Взять в работу (claim), 📄 Транскрипт; в закрытом — 🔓 Переоткрыть и 🗑️ Удалить.
- **Команды**: `/close`, `/claim`, `/add`, `/remove`, `/ticketinfo`.
- **HTML-транскрипты** при каждом закрытии уходят в лог-канал: авторы, время, вложения, embeds.
- **Автозакрытие неактивных**: предупреждение через N часов, закрытие через M часов (фоновая проверка каждые 10 минут).
- **Оценка поддержки** ⭐1–5 после закрытия (в канале или в ЛС) и статистика `/stats`: открыто/закрыто, среднее время обработки, топ модераторов, средняя оценка.
- **Вся настройка командами** `/config ...` — без правки кода и перезапуска. Настройки старой версии подхватываются автоматически при первом запуске.

## Локальный запуск

Нужен Python 3.11+.

```bash
pip install -r requirements.txt
copy .env.example .env        # Windows (на Linux/macOS: cp)
# впишите токен в .env
python main.py
```

Токен: [Discord Developer Portal](https://discord.com/developers/applications) → New Application → Bot → Reset Token.
Там же включите оба Privileged Gateway Intents: **SERVER MEMBERS INTENT** и **MESSAGE CONTENT INTENT** — без них бот не стартует.

Приглашение на сервер: Portal → OAuth2 → URL Generator → scopes `bot` + `applications.commands`,
права минимум: View Channels, Send Messages, Embed Links, Attach Files, Read Message History, Manage Channels, Manage Roles (проще — Administrator).

## Настройка на сервере (команды администратора)

| Команда | Что делает |
|---|---|
| `/config set category` | категория, где создаются тикеты |
| `/config set logs` | канал для транскриптов и логов |
| `/config staff add/remove` | роли персонала |
| `/config categories add/remove` | категории на панели (название, эмодзи, описание) |
| `/config limits` | макс. открытых тикетов и кулдаун |
| `/config autoclose` | часы неактивности до предупреждения и закрытия (0 — выключить) |
| `/setup` | опубликовать панель создания тикетов |

## Деплой 24/7 бесплатно

### 1. Render

1. Запушьте репозиторий на GitHub.
2. [render.com](https://render.com) → **New → Web Service** → подключите репозиторий.
3. Runtime: **Python 3**; Build command: `pip install -r requirements.txt`; Start command: `python main.py`.
4. Environment → добавьте `DISCORD_TOKEN` = токен бота. Instance type: **Free**.

На бесплатном тарифе сервис засыпает без трафика — решается шагом 2.

### 2. UptimeRobot

1. Зарегистрируйтесь на [uptimerobot.com](https://uptimerobot.com) (бесплатно).
2. Add New Monitor → HTTP(s), URL: `https://ВАШ-СЕРВИС.onrender.com/`, интервал 5 минут.

Бот теперь онлайн ~24/7: пингер будит сервис, а встроенный HTTP-сервер отвечает на пинги.

## Структура проекта

```
main.py              вход: бот, загрузка когов, синхронизация команд, keep-alive сервер
db.py                SQLite: настройки серверов, тикеты, оценки (+ миграция старых настроек)
utils/checks.py      проверки прав, метки тикета в теме канала
utils/transcripts.py генератор HTML-транскриптов
utils/views.py       кнопки/панели/модалки (постоянные, переживают рестарт)
utils/ticket_ops.py  создание/закрытие/claim/reopen/удаление тикета
cogs/config_cog.py   /config …
cogs/tickets.py      /setup, /close, /claim, /add, /remove, /ticketinfo
cogs/autoclose.py    фоновое автозакрытие
cogs/stats.py        /stats
data/bot.db          база (в .gitignore, создаётся сама)
```
