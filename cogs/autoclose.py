"""Фоновая проверка открытых тикетов: предупреждение о неактивности
и автозакрытие. Интервалы настраиваются командой /config autoclose."""

import time
import traceback

import discord
from discord.ext import commands, tasks

import db
from utils import ticket_ops


class AutoCloseCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        self._sweep_loop.start()

    def cog_unload(self):
        self._sweep_loop.cancel()

    async def _last_activity_ts(self, channel: discord.TextChannel) -> float:
        if channel.last_message_id:
            return discord.utils.snowflake_time(channel.last_message_id).timestamp()
        return discord.utils.snowflake_time(channel.id).timestamp()

    @tasks.loop(minutes=10)
    async def _sweep_loop(self):
        try:
            await self._sweep()
        except Exception:
            traceback.print_exc()

    @_sweep_loop.before_loop
    async def _wait_ready(self):
        await self.bot.wait_until_ready()

    async def _sweep(self):
        now = time.time()
        for guild in list(self.bot.guilds):
            cfg = db.get_config(guild.id)
            warn_hours = cfg["autoclose_warn_hours"]
            if warn_hours <= 0:
                continue
            close_hours = cfg["autoclose_hours"]

            for ticket in db.open_tickets(guild.id):
                channel = guild.get_channel(ticket["channel_id"])
                if not isinstance(channel, discord.TextChannel):
                    continue
                if channel.name.startswith("closed-"):
                    # Закрытие потерялось при рестарте (база восстановлена старее
                    # Discord) — чиним запись, транскрипт/оценку второй раз не шлём.
                    db.mark_closed(channel.id)
                    continue
                idle_hours = (now - await self._last_activity_ts(channel)) / 3600

                if close_hours > 0 and idle_hours >= close_hours:
                    try:
                        await ticket_ops.perform_close(
                            channel, ticket, cfg, closed_by=None, auto=True
                        )
                    except Exception:
                        traceback.print_exc()
                    continue

                if idle_hours >= warn_hours:
                    if not ticket["warn_sent_at"]:
                        owner = guild.get_member(ticket["owner_id"])
                        mention = owner.mention if owner else f"<@{ticket['owner_id']}>"
                        closing_note = (
                            f" Если активности не будет ещё {close_hours} ч — тикет закроется автоматически."
                            if close_hours > 0 else ""
                        )
                        try:
                            await channel.send(
                                f"⏰ {mention}, тикет без активности уже {int(idle_hours)} ч."
                                f" Напишите что-нибудь, чтобы он остался открытым.{closing_note}"
                            )
                            db.set_warned(channel.id, True)
                        except discord.HTTPException:
                            pass
                elif ticket["warn_sent_at"]:
                    # Кто-то написал после предупреждения — снимаем флаг.
                    db.set_warned(channel.id, False)


async def setup(bot):
    await bot.add_cog(AutoCloseCog(bot))
