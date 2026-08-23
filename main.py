import asyncio
import os
import sys
import traceback
from pathlib import Path
from threading import Thread
from http.server import HTTPServer, BaseHTTPRequestHandler

import discord
from discord.ext import commands

# --- Веб-сервер для бесплатного тарифа Render ---
# Внимание: сам по себе он не мешает Render засыпать — нужен внешний пингер (UptimeRobot и т.п.)
class SimpleHTTPRequestHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot status: OK")

def run_web_server():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(('0.0.0.0', port), SimpleHTTPRequestHandler)
    server.serve_forever()

TOKEN = os.environ.get("DISCORD_TOKEN")
if not TOKEN:
    sys.exit("[!] Переменная окружения DISCORD_TOKEN не задана — запуск невозможен.")

Thread(target=run_web_server, daemon=True).start()

# --- Основной код бота ---
intents = discord.Intents.default()
# Оба интента ниже должны быть включены в Developer Portal -> Bot -> Privileged Gateway Intents,
# иначе бот упадёт при старте с PrivilegedIntentsRequired
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    print("====================================")
    print(f"[+] Бот успешно запущен: {bot.user}")
    print("====================================")

async def load_extensions():
    cogs_dir = Path(__file__).parent / 'cogs'
    for file in sorted(cogs_dir.glob('*.py')):
        if file.name.startswith('_'):
            continue
        try:
            await bot.load_extension(f'cogs.{file.stem}')
            print(f"[+] Модуль {file.name} успешно загружен")
        except Exception:
            print(f"[-] Не удалось загрузить модуль {file.name}, продолжаю без него")
            traceback.print_exc()

async def main():
    async with bot:
        await load_extensions()
        await bot.start(TOKEN)

if __name__ == "__main__":
    asyncio.run(main())
