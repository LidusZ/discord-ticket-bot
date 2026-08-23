"""Ядро бота: панель создания тикетов, команды управления и регистрация
постоянных кнопок (чтобы они работали после перезапуска)."""

from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

import db
from utils import checks, ticket_ops
from utils.ticket_ops import format_duration
from utils.views import (
    ALL_PERSISTENT_VIEWS,
    ConfirmCloseView,
    TicketPanelView,
)


class TicketsCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        for view_cls in ALL_PERSISTENT_VIEWS:
            self.bot.add_view(view_cls())

    # --- Панель ---

    @app_commands.command(name="setup", description="Опубликовать панель создания тикетов")
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    async def setup_panel(
        self,
        interaction: discord.Interaction,
        title: Optional[str] = None,
        description: Optional[str] = None,
    ):
        cfg = db.get_config(interaction.guild_id)
        if not cfg["category_id"]:
            await interaction.response.send_message(
                "⛔ Сначала задайте категорию тикетов: `/config set category`.", ephemeral=True
            )
            return

        cats = "\n".join(
            f"{c.get('emoji') or '🎫'} **{c['label']}**"
            + (f" — {c['description']}" if c.get("description") else "")
            for c in cfg["ticket_categories"]
        )
        embed = discord.Embed(
            title=title or "🎧 Поддержка",
            description=(
                description
                or "Нужна помощь? Создайте обращение — персонал ответит в приватном канале.\n\n"
                   f"{cats}"
            ),
            color=discord.Color.green(),
        )
        await interaction.channel.send(embed=embed, view=TicketPanelView(cfg["ticket_categories"]))
        await interaction.response.send_message("✅ Панель опубликована.", ephemeral=True)

    # --- Управление тикетом командами ---

    async def _current_ticket(self, interaction: discord.Interaction) -> Optional[dict]:
        ticket = ticket_ops.resolve_ticket(interaction.channel)
        if ticket is None:
            await interaction.response.send_message(
                "Эта команда работает только внутри канала-тикетa.", ephemeral=True
            )
        return ticket

    @app_commands.command(name="close", description="Закрыть тикет в текущем канале")
    @app_commands.guild_only()
    async def close(self, interaction: discord.Interaction):
        ticket = await self._current_ticket(interaction)
        if ticket is None:
            return
        if ticket["status"] == "closed":
            await interaction.response.send_message("Тикет уже закрыт.", ephemeral=True)
            return
        cfg = db.get_config(interaction.guild_id)
        if not await checks.ensure_access(interaction, cfg["staff_role_ids"], ticket["owner_id"]):
            return
        await interaction.response.send_message(
            "Закрыть этот **тикет**?", view=ConfirmCloseView(), ephemeral=True
        )

    @app_commands.command(name="claim", description="Взять тикет в работу")
    @app_commands.guild_only()
    async def claim(self, interaction: discord.Interaction):
        ticket = await self._current_ticket(interaction)
        if ticket is None:
            return
        cfg = db.get_config(interaction.guild_id)
        staff = await checks.ensure_staff(interaction, cfg["staff_role_ids"])
        if staff is None:
            return
        await interaction.response.defer()
        await ticket_ops.claim_ticket(interaction.channel, ticket, staff)

    @app_commands.command(name="add", description="Добавить пользователя в тикет")
    @app_commands.guild_only()
    async def add(self, interaction: discord.Interaction, user: discord.Member):
        ticket = await self._current_ticket(interaction)
        if ticket is None:
            return
        cfg = db.get_config(interaction.guild_id)
        if not await checks.ensure_access(interaction, cfg["staff_role_ids"], ticket["owner_id"]):
            return
        await interaction.channel.set_permissions(
            user, read_messages=True, send_messages=True, attach_files=True
        )
        await interaction.response.send_message(
            f"➕ {user.mention} добавлен(а) в тикет {interaction.user.mention}."
        )

    @app_commands.command(name="remove", description="Убрать пользователя из тикета")
    @app_commands.guild_only()
    async def remove(self, interaction: discord.Interaction, user: discord.Member):
        ticket = await self._current_ticket(interaction)
        if ticket is None:
            return
        cfg = db.get_config(interaction.guild_id)
        if not await checks.ensure_staff(interaction, cfg["staff_role_ids"]):
            return
        if user.id == ticket["owner_id"]:
            await interaction.response.send_message(
                "⛔ Владельца тикета убрать нельзя — закройте сам тикет.", ephemeral=True
            )
            return
        over = discord.PermissionOverwrite()
        over.read_messages = None
        over.send_messages = None
        await interaction.channel.set_permissions(user, overwrite=over)
        await interaction.response.send_message(f"➖ {user.mention} убран(а) из тикета.")

    @app_commands.command(name="ticketinfo", description="Информация о текущем тикете")
    @app_commands.guild_only()
    async def ticketinfo(self, interaction: discord.Interaction):
        ticket = await self._current_ticket(interaction)
        if ticket is None:
            return
        number = f"#{ticket['number']:04d}" if ticket["number"] else "старый формат"
        claimed = (
            f"<@{ticket['claimed_by']}>" if ticket["claimed_by"] else "не взят"
        )
        stars = db.ticket_rating(ticket["channel_id"])
        rating = "⭐" * stars if stars else "нет оценки"

        embed = discord.Embed(title=f"🎫 Тикет {number}", color=discord.Color.blurple())
        embed.add_field(name="Статус", value="🟢 открыт" if ticket["status"] == "open" else "🔒 закрыт")
        embed.add_field(name="Категория", value=ticket["category"] or "—")
        embed.add_field(name="Владелец", value=f"<@{ticket['owner_id']}>")
        embed.add_field(name="Взят в работу", value=claimed)
        embed.add_field(name="Создан", value=ticket_ops._ts(ticket["created_at"]))
        if ticket["closed_at"]:
            embed.add_field(name="Закрыт", value=ticket_ops._ts(ticket["closed_at"]))
            embed.add_field(
                name="Время обработки",
                value=format_duration(ticket["closed_at"] - ticket["created_at"]),
            )
        embed.add_field(name="Оценка поддержки", value=rating)
        await interaction.response.send_message(embed=embed)


async def setup(bot):
    await bot.add_cog(TicketsCog(bot))
