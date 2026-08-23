"""Счётчики участников сервера: голосовые каналы с числом всех участников,
людей и ботов. Включаются командой /config members enable.

Обновление — раз в 5 минут и по пачкам входов/выходов (с задержкой-дебаунсом),
потому что Discord разрешает только ~2 переименования канала за 10 минут.
"""

import asyncio
import traceback

import discord
from discord.ext import commands, tasks

import db
from utils import member_stats_ops

UPDATE_MINUTES = 5
DEBOUNCE_SECONDS = 60


class MembersCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        # Отдельный флаг дебаунса на каждый сервер: общий глотал бы события
        # второго сервера, пока «спит» первый.
        self._refresh_pending: set[int] = set()

    async def cog_load(self):
        self._update_loop.start()

    def cog_unload(self):
        self._update_loop.cancel()

    @tasks.loop(minutes=UPDATE_MINUTES)
    async def _update_loop(self):
        try:
            for guild in list(self.bot.guilds):
                cfg = db.get_config(guild.id)
                if cfg.get("ms_enabled"):
                    await member_stats_ops.refresh_guild(guild, cfg)
        except Exception:
            traceback.print_exc()

    @_update_loop.before_loop
    async def _wait_ready(self):
        await self.bot.wait_until_ready()

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        await self._schedule_refresh(member.guild)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        await self._schedule_refresh(member.guild)

    async def _schedule_refresh(self, guild: discord.Guild) -> None:
        """Не даём рейду входов/выходов заспамить переименования: одна волна
        обновления в минуту на сервер, числа всё равно считаются свежими."""
        if guild.id in self._refresh_pending:
            return
        self._refresh_pending.add(guild.id)
        try:
            await asyncio.sleep(DEBOUNCE_SECONDS)
            if not self.bot.is_closed():
                cfg = db.get_config(guild.id)
                if cfg.get("ms_enabled"):
                    await member_stats_ops.refresh_guild(guild, cfg)
        except Exception:
            traceback.print_exc()
        finally:
            self._refresh_pending.discard(guild.id)


async def setup(bot):
    await bot.add_cog(MembersCog(bot))
