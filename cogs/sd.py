"""Периодическая отправка «.Дм» в текстовый чат голосового канала.

Включается командой /startsd, выключается /stopsd. Интервал 9 минут
(«раз в 9–10 минут»). Состояние хранится в базе — переживает рестарт,
но после рестарта первое сообщение уйдёт только в ближайший цикл.
"""

import traceback
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks

import db

# Войс-канал по умолчанию (основной сервер) и текст сообщения.
DEFAULT_SD_CHANNEL_ID = 1541057269859483708
SD_MESSAGE = ".Дм"
INTERVAL_MINUTES = 9


class SdCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        self._send_loop.start()

    def cog_unload(self):
        self._send_loop.cancel()

    @tasks.loop(minutes=INTERVAL_MINUTES)
    async def _send_loop(self):
        try:
            for guild in list(self.bot.guilds):
                cfg = db.get_config(guild.id)
                if not cfg.get("sd_enabled"):
                    continue
                channel = guild.get_channel(cfg.get("sd_channel_id") or 0)
                if not isinstance(channel, discord.VoiceChannel):
                    continue
                try:
                    await channel.send(SD_MESSAGE)
                except discord.HTTPException:
                    traceback.print_exc()
        except Exception:
            traceback.print_exc()

    @_send_loop.before_loop
    async def _wait_ready(self):
        await self.bot.wait_until_ready()

    @app_commands.command(name="startsd", description="Отправлять «.Дм» в голосовой канал каждые 9 минут")
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    async def startsd(self, interaction: discord.Interaction, channel: Optional[discord.VoiceChannel] = None):
        """Без аргумента используется стандартный канал основного сервера."""
        channel_id = channel.id if channel else DEFAULT_SD_CHANNEL_ID
        db.set_sd_channel(interaction.guild_id, channel_id)
        db.set_sd_enabled(interaction.guild_id, True)

        target = interaction.guild.get_channel(channel_id)
        note = "первое уйдёт в ближайший цикл"
        if isinstance(target, discord.VoiceChannel):
            try:
                await target.send(SD_MESSAGE)
                note = f"первое уже отправлено в {target.mention}"
            except discord.HTTPException:
                note = "но первое сообщение не ушло — проверьте права бота в канале"
        else:
            note = "но указанный канал не найден на этом сервере"
        await interaction.response.send_message(
            f"✅ Отправка «{SD_MESSAGE}» включена (раз в {INTERVAL_MINUTES} мин), {note}.",
            ephemeral=True,
        )

    @app_commands.command(name="stopsd", description="Выключить отправку «.Дм»")
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    async def stopsd(self, interaction: discord.Interaction):
        cfg = db.get_config(interaction.guild_id)
        if not cfg.get("sd_enabled"):
            await interaction.response.send_message("Отправка и так выключена.", ephemeral=True)
            return
        db.set_sd_enabled(interaction.guild_id, False)
        await interaction.response.send_message("✅ Отправка выключена.", ephemeral=True)


async def setup(bot):
    await bot.add_cog(SdCog(bot))
