"""Счётчики участников: голосовые каналы «All Members / Members / Bots»,
которые бот держит в актуальном состоянии (как у популярных stat-ботов).

Используется когом cogs/members.py (фоновое обновление) и config_cog
(команды включения/настройки). Discord разрешает ~2 переименования канала
за 10 минут, поэтому обновление — не чаще раза в 5 минут и только при
реальном изменении числа.
"""

import re
import traceback
from typing import Optional

import discord

import db

# Порядок каналов в категории: все → люди → боты.
FIELDS = (
    ("ms_ch_all", "all"),
    ("ms_ch_humans", "humans"),
    ("ms_ch_bots", "bots"),
)


def compute_counts(guild: discord.Guild) -> dict[str, int]:
    members = guild.members
    bots = sum(1 for m in members if m.bot)
    total = guild.member_count if guild.member_count is not None else len(members)
    return {"all": total, "humans": len(members) - bots, "bots": bots}


def render_label(template: str, count: int) -> str:
    try:
        return template.format(count=count)[:100]
    except (KeyError, IndexError, ValueError):
        # Пользователь ввёл кривые фигурные скобки: {count} подставляем вручную,
        # остальные {...} плейсхолдеры вычищаем, чтобы не мусорить в названии.
        text = template.replace("{count}", str(count))
        return re.sub(r"\{[^{}]*\}", "", text).strip()[:100]


async def create_counters(guild: discord.Guild, category_id: Optional[int]) -> dict[str, int]:
    """Создаёт три канала-счётчика. Возвращает {поле_бд: id канала}.
    При сбое посреди создания подчищает уже созданные, чтобы повторный
    /config members enable не наделал дублей без записи в базе."""
    counts = compute_counts(guild)
    labels = db.get_memberstat_labels(guild.id)
    category = guild.get_channel(category_id or 0)
    target = category if isinstance(category, discord.CategoryChannel) else guild
    ids: dict[str, int] = {}
    try:
        for field, key in FIELDS:
            channel = await target.create_voice_channel(
                render_label(labels[key], counts[key]), reason="Счётчики участников"
            )
            ids[field] = channel.id
    except Exception:
        for channel_id in ids.values():
            leftover = guild.get_channel(channel_id)
            if leftover is not None:
                try:
                    await leftover.delete(reason="Счётчики участников: откат после сбоя")
                except discord.HTTPException:
                    traceback.print_exc()
        raise
    return ids


async def delete_counters(guild: discord.Guild, cfg: dict) -> None:
    """Сносит каналы-счётчики, если ещё живы (перед пересозданием или выключением)."""
    for field, _ in FIELDS:
        channel = guild.get_channel(cfg.get(field) or 0)
        if channel is None:
            continue
        try:
            await channel.delete(reason="Счётчики участников выключены")
        except discord.HTTPException:
            traceback.print_exc()


async def refresh_guild(guild: discord.Guild, cfg: dict) -> None:
    """Приводит каналы к текущим числам; пересоздаёт снесённые руками."""
    counts = compute_counts(guild)
    labels = db.get_memberstat_labels(guild.id)
    category = guild.get_channel(cfg.get("ms_category_id") or 0)
    target = category if isinstance(category, discord.CategoryChannel) else guild

    ids = {field: cfg.get(field) for field, _ in FIELDS}
    for field, key in FIELDS:
        desired = render_label(labels[key], counts[key])
        channel = guild.get_channel(ids[field] or 0)
        try:
            if channel is None:
                channel = await target.create_voice_channel(
                    desired, reason="Счётчики участников: канал пересоздан"
                )
                ids[field] = channel.id
            elif channel.name != desired:
                await channel.edit(name=desired, reason="Счётчики участников")
        except discord.HTTPException:
            traceback.print_exc()

    if ids != {field: cfg.get(field) for field, _ in FIELDS}:
        db.set_memberstats_channels(guild.id, ids["ms_ch_all"], ids["ms_ch_humans"], ids["ms_ch_bots"])
