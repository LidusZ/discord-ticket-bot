"""Проверки прав и работа с метками тикета в теме канала.

База данных — основной источник правды о тикете, но тема канала дублирует
ключевые поля: если база потеряется (например, при переезде), бот всё равно
понимает, кому принадлежит канал.
"""

import re
from typing import Optional

import discord

OWNER_TOPIC_PREFIX = "TICKET_OWNER:"
OWNER_RE = re.compile(r"TICKET_OWNER:(\d+)")
CLAIMED_RE = re.compile(r"CLAIMED:(\d+)")


def build_topic(owner_id: int, category_label: str, claimed_by: Optional[int] = None) -> str:
    return f"{OWNER_TOPIC_PREFIX}{owner_id} | CAT:{category_label} | CLAIMED:{claimed_by or 0}"


def owner_from_topic(channel: discord.TextChannel) -> Optional[int]:
    if not channel.topic:
        return None
    match = OWNER_RE.search(channel.topic)
    return int(match.group(1)) if match else None


def claimed_from_topic(channel: discord.TextChannel) -> Optional[int]:
    if not channel.topic:
        return None
    match = CLAIMED_RE.search(channel.topic)
    return int(match.group(1)) or None


def is_staff(member: discord.Member, staff_role_ids: list[int]) -> bool:
    if member.guild_permissions.administrator:
        return True
    return any(role.id in staff_role_ids for role in member.roles)


async def ensure_guild(interaction: discord.Interaction) -> Optional[discord.Member]:
    """Возвращает Member, если взаимодействие пришло с сервера; иначе отвечает отказом."""
    member = interaction.user
    if interaction.guild is not None and isinstance(member, discord.Member):
        return member
    if not interaction.response.is_done():
        await interaction.response.send_message("Доступно только на сервере.", ephemeral=True)
    return None


async def ensure_staff(interaction: discord.Interaction, staff_role_ids: list[int]) -> Optional[discord.Member]:
    """Member, если нажавший — стафф; иначе ephemeral-отказ и None."""
    member = await ensure_guild(interaction)
    if member is None:
        return None
    if is_staff(member, staff_role_ids):
        return member
    if not interaction.response.is_done():
        await interaction.response.send_message("⛔ Кнопка доступна только стаффу.", ephemeral=True)
    return None


async def ensure_access(
    interaction: discord.Interaction, staff_role_ids: list[int], owner_id: Optional[int]
) -> Optional[discord.Member]:
    """Member, если нажавший — стафф или владелец тикета; иначе отказ и None."""
    member = await ensure_guild(interaction)
    if member is None:
        return None
    if is_staff(member, staff_role_ids) or member.id == owner_id:
        return member
    if not interaction.response.is_done():
        await interaction.response.send_message(
            "⛔ Только стафф или владелец тикета могут это делать.", ephemeral=True
        )
    return None
