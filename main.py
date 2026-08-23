"""Точка входа тикет-бота: запуск, загрузка когов, синхронизация команд
и keep-alive HTTP-сервер для бесплатного тарифа Render."""

import asyncio
import logging
import os
import sys
import traceback
from pathlib import Path
from threading import Thread
from http.server import HTTPServer, BaseHTTPRequestHandler

# Локальный запуск читает .env рядом с проектом; на Render переменная задаётся в панели.
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import discord
from discord import app_commands
from discord.ext import commands

import db

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
        db.init_db()
        await self.load_cogs()

        # Мгновенное появление команд на известных серверах + глобальная синхронизация
        # для новых серверов (там команды подтянутся в течение часа или при заходе бота).
        await self.tree.sync()
        for guild_id in db.all_guild_ids():
            guild = discord.Object(id=guild_id)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        log.info("Слэш-команды синхронизированы")

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
    async with bot:
        await bot.start(TOKEN)


if __name__ == "__main__":
    asyncio.run(main())
