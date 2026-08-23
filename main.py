"""Точка входа тикет-бота: запуск, загрузка когов, синхронизация команд
и keep-alive HTTP-сервер для бесплатного тарифа Render."""

import asyncio
import logging
import os
import signal
import sys
import traceback
from pathlib import Path
from threading import Thread
from http.server import HTTPServer, BaseHTTPRequestHandler

import aiohttp

# Локальный запуск читает .env рядом с проектом; на Render переменная задаётся в панели.
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import discord
from discord import app_commands
from discord.ext import commands, tasks

import db
from utils import persist

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
)
log = logging.getLogger("tickets")

TOKEN = os.environ.get("DISCORD_TOKEN")
if not TOKEN:
    sys.exit("[!] Переменная окружения DISCORD_TOKEN не задана — запуск невозможен.")


# --- Веб-сервер для Render Free (пингуется UptimeRobot'ом, чтобы сервис не засыпал) ---
class StatusHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot status: OK")

    def log_message(self, *args):  # не засоряем логи пингами
        pass


def run_web_server():
    port = int(os.environ.get("PORT", 8080))
    try:
        server = HTTPServer(("0.0.0.0", port), StatusHandler)
        server.serve_forever()
    except OSError:
        log.warning("Порт %s занят — keep-alive сервер не запущен (нормально для локального теста)", port)


Thread(target=run_web_server, daemon=True).start()


class TicketBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        # Оба интента должны быть включены в Developer Portal -> Bot -> Privileged Gateway Intents.
        intents.message_content = True
        intents.members = True
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        # Файловая система Render Free пустая после каждого рестарта:
        # сначала возвращаем базу из Discord-канала бэкапов, потом открываем её.
        await persist.restore_if_missing(self)
        db.init_db()
        await self.load_cogs()
        keep_self_awake.start()

        # Мгновенное появление команд на известных серверах + глобальная синхронизация
        # для новых серверов (там команды подтянутся в течение часа или при заходе бота).
        await self.tree.sync()
        for guild_id in db.all_guild_ids():
            guild = discord.Object(id=guild_id)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        log.info("Слэш-команды синхронизированы")

    async def close(self):
        # Render присылает SIGTERM перед остановкой/деплоем — успеваем выгрузить
        # свежую базу (тикеты, комнаты, настройки), чтобы ничего не потерять.
        try:
            await persist.upload(self, "остановка бота")
        except Exception:
            pass
        await super().close()

    async def load_cogs(self):
        cogs_dir = Path(__file__).parent / "cogs"
        for file in sorted(cogs_dir.glob("*.py")):
            if file.name.startswith("_"):
                continue
            try:
                await self.load_extension(f"cogs.{file.stem}")
                log.info("Модуль %s загружен", file.name)
            except Exception:
                log.error("Не удалось загрузить модуль %s, продолжаю без него", file.name)
                traceback.print_exc()


bot = TicketBot()


# --- Самопинг: бесплатный Render засыпает без входящего трафика за 15 минут. ---
# Бот сам дёргает свой публичный адрес (Render выдаёт его в RENDER_EXTERNAL_URL),
# поэтому внешний пингер вроде UptimeRobot больше не обязателен.
@tasks.loop(minutes=10)
async def keep_self_awake():
    url = os.environ.get("RENDER_EXTERNAL_URL")
    if not url:
        return  # локальный запуск — пинговать некуда
    try:
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=20)
        ) as session:
            async with session.get(url) as resp:
                if resp.status != 200:
                    log.warning("Самопинг %s вернул %s", url, resp.status)
    except Exception:
        pass  # разовый сбой сети не важен — следующий тик через 10 минут


@keep_self_awake.before_loop
async def _keepalive_wait_ready():
    await bot.wait_until_ready()


@bot.event
async def on_ready():
    log.info("============================")
    log.info("Бот запущен: %s", bot.user)
    log.info("Серверов: %d", len(bot.guilds))
    log.info("============================")


@bot.event
async def on_guild_join(guild: discord.Guild):
    db.ensure_guild(guild.id)
    copy_to = discord.Object(id=guild.id)
    bot.tree.copy_global_to(guild=copy_to)
    await bot.tree.sync(guild=copy_to)
    log.info("Бота добавили на сервер %s (%s) — настройки созданы", guild.name, guild.id)


@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    log.error("Ошибка команды /%s: %s", interaction.command.qualified_name if interaction.command else "?", error)
    traceback.print_exception(type(error), error, error.__traceback__)
    message = "❌ Произошла ошибка. Если повторится — сообщите администратору."
    try:
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)
    except discord.HTTPException:
        pass


async def main():
    # По умолчанию SIGTERM убивает процесс мгновенно и close() с финальной
    # выгрузкой базы не вызывается — без этой обработки деплой Render теряет
    # всё записанное в базе с момента прошлой копии.
    try:
        asyncio.get_running_loop().add_signal_handler(
            signal.SIGTERM, lambda: asyncio.ensure_future(bot.close())
        )
    except NotImplementedError:
        pass  # локальный запуск на Windows — сигналов нет, остановка через Ctrl+C
    async with bot:
        await bot.start(TOKEN)


if __name__ == "__main__":
    asyncio.run(main())
