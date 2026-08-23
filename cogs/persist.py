"""Периодическая выгрузка базы в закрытый Discord-канал бэкапов (utils/persist.py).

Render Free не хранит файлы между рестартами, поэтому база выгружается:
вскоре после любой записи в базу (настройки, тикеты, комнаты), раз в час
при активности — а при остановке бота последнюю копию отправляет main.py
в close() по SIGTERM.
"""

import time
import traceback

from discord.ext import commands, tasks

import db
from utils import persist


class PersistCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._last_upload_ts = 0.0

    async def cog_load(self):
        self._backup_loop.start()

    def cog_unload(self):
        self._backup_loop.cancel()

    @tasks.loop(minutes=5)
    async def _backup_loop(self):
        try:
            now = time.time()
            # Выгружаем только если после прошлой выгрузки база менялась.
            if db.last_write_ts <= self._last_upload_ts:
                return
            due_regular = now - self._last_upload_ts >= persist.REGULAR_INTERVAL_SECONDS
            # Любая запись важна наравне с настройками: потерянный при рестарте
            # статус тикета ломает кнопки закрытия/переоткрытия/удаления.
            due_write = now - db.last_write_ts >= persist.IMPORTANT_DEBOUNCE_SECONDS
            if not (due_regular or due_write):
                return
            reason = "изменение данных" if due_write else "плановая копия"
            if await persist.upload(self.bot, reason):
                self._last_upload_ts = now
        except Exception:
            traceback.print_exc()

    @_backup_loop.before_loop
    async def _wait_ready(self):
        await self.bot.wait_until_ready()


async def setup(bot):
    await bot.add_cog(PersistCog(bot))
