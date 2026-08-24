"""Трекер приглашений: кто кого привёл, логи входов/выходов в канал
и топ инвайтеров за последние 7 дней (/invitetop + еженедельная автопубликация).

Список приглашений сервера требует у бота права «Управление сервером».
"""

import time
import traceback

import discord
from discord import app_commands
from discord.ext import commands, tasks

import db
from utils import invite_ops


class InvitesCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        self._resync_loop.start()
        self._weekly_loop.start()

    def cog_unload(self):
        self._resync_loop.cancel()
        self._weekly_loop.cancel()

    # --- Фоновые циклы -----------------------------------------------------

    @tasks.loop(minutes=10)
    async def _resync_loop(self):
        # Самолечение: инвайт могли использовать или удалить, пока бот был
        # оффлайн либо событие create/delete не долетело.
        for guild in list(self.bot.guilds):
            try:
                await invite_ops.refresh_cache(guild)
            except Exception:
                traceback.print_exc()

    @_resync_loop.before_loop
    async def _wait_ready_resync(self):
        await self.bot.wait_until_ready()

    @tasks.loop(hours=1)
    async def _weekly_loop(self):
        try:
            await self._post_due_weekly_tops()
        except Exception:
            traceback.print_exc()

    @_weekly_loop.before_loop
    async def _wait_ready_weekly(self):
        await self.bot.wait_until_ready()

    async def _post_due_weekly_tops(self):
        now = int(time.time())
        for guild in list(self.bot.guilds):
            cfg = db.get_config(guild.id)
            channel_id = cfg.get("inv_weekly_channel_id")
            if not channel_id:
                continue
            last = cfg.get("inv_weekly_last_ts")
            if not last:
                # Неделя отсчитывается с момента включения.
                db.set_invite_weekly_last(guild.id, now)
                continue
            if now - last < invite_ops.WEEK_SECONDS:
                continue
            channel = guild.get_channel(channel_id)
            if not isinstance(channel, discord.TextChannel):
                continue
            rows = db.invite_top(guild.id, now - invite_ops.WEEK_SECONDS)
            embed = invite_ops.top_embed(guild, rows, "🏆 Топ инвайтеров за последние 7 дней")
            try:
                await channel.send(embed=embed)
            except discord.HTTPException:
                traceback.print_exc()
                continue
            db.set_invite_weekly_last(guild.id, now)

    # --- События Discord ----------------------------------------------------

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        if member.bot:
            return
        guild = member.guild
        info, note = await invite_ops.attribute_join(guild)
        if info is not None:
            db.add_invite_join(guild.id, info["inviter_id"], member.id, info["code"])
        cfg = db.get_config(guild.id)
        channel = invite_ops.resolve_log_channel(guild, cfg)
        if channel is None:
            return
        try:
            await channel.send(embed=invite_ops.join_embed(member, info, note))
        except discord.HTTPException:
            pass

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        if member.bot:
            return
        counted = db.mark_invite_left(member.guild.id, member.id)
        cfg = db.get_config(member.guild.id)
        channel = invite_ops.resolve_log_channel(member.guild, cfg)
        if channel is None:
            return
        try:
            await channel.send(embed=invite_ops.leave_embed(member, counted))
        except discord.HTTPException:
            pass

    @commands.Cog.listener()
    async def on_invite_create(self, invite: discord.Invite):
        if invite.guild and invite.code:
            inviter_id = invite.inviter.id if invite.inviter else None
            db.upsert_invite_cache_entry(invite.guild.id, invite.code, inviter_id, invite.uses or 0)

    @commands.Cog.listener()
    async def on_invite_delete(self, invite: discord.Invite):
        if invite.guild and invite.code:
            db.delete_invite_cache_entry(invite.guild.id, invite.code)

    # --- Команды -------------------------------------------------------------

    @app_commands.command(name="invitetop", description="Топ инвайтеров за последние 7 дней")
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    async def invitetop(self, interaction: discord.Interaction):
        """Разово показать топ; автоматом раз в неделю публикуется через /config invites weekly."""
        now = int(time.time())
        rows = db.invite_top(interaction.guild_id, now - invite_ops.WEEK_SECONDS)
        embed = invite_ops.top_embed(interaction.guild, rows, "🏆 Топ инвайтеров за последние 7 дней")
        await interaction.response.send_message(embed=embed)


async def setup(bot):
    await bot.add_cog(InvitesCog(bot))
