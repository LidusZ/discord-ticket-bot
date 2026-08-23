"""Статистика работы поддержки: /stats по серверу и /ticketinfo в коге tickets."""

import time

import discord
from discord import app_commands
from discord.ext import commands

import db
from utils import checks, ticket_ops


class StatsCog(commands.Cog):
    @app_commands.command(name="stats", description="Статистика поддержки за период")
    @app_commands.guild_only()
    async def stats(
        self,
        interaction: discord.Interaction,
        days: app_commands.Range[int, 1, 365] = 30,
    ):
        cfg = db.get_config(interaction.guild_id)
        member = interaction.user
        if not isinstance(member, discord.Member) or not checks.is_staff(member, cfg["staff_role_ids"]):
            await interaction.response.send_message("⛔ Доступно только персоналу.", ephemeral=True)
            return

        since = int(time.time()) - days * 86400
        s = db.stats_overview(interaction.guild_id, since)

        avg_close = (
            ticket_ops.format_duration(s["avg_close_seconds"])
            if s["avg_close_seconds"] is not None
            else "—"
        )
        rating = (
            f"{round(s['avg_stars'], 2)} / 5 ⭐ (голосов: {s['votes']})"
            if s["votes"]
            else "пока нет оценок"
        )

        embed = discord.Embed(
            title=f"📊 Статистика поддержки за {days} дн.",
            color=discord.Color.blurple(),
        )
        embed.add_field(name="Открыто тикетов", value=str(s["opened"]))
        embed.add_field(name="Закрыто", value=str(s["closed"]))
        embed.add_field(name="Открыто сейчас", value=str(s["open_now"]))
        embed.add_field(name="Среднее время обработки", value=avg_close)
        embed.add_field(name="Оценка поддержки", value=rating, inline=False)

        if s["top_claims"]:
            top_lines = [
                f"<@{user_id}> — {count} тик." for user_id, count in s["top_claims"]
            ]
            embed.add_field(
                name="Топ по взятым тикетам",
                value="\n".join(top_lines),
                inline=False,
            )
        await interaction.response.send_message(embed=embed)


async def setup(bot):
    await bot.add_cog(StatsCog(bot))
