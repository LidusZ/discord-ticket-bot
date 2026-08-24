"""Бизнес-логика трекера приглашений: снимок инвайтов, определение автора,
embed-сообщения для логов и еженедельного топа.

Discord не сообщает, по какому именно приглашению зашёл участник, поэтому
используется разница счётчиков: бот помнит «код -> использования» и при
входе ищет приглашение, у которого число выросло.
"""

from typing import Optional

import discord

import db

# Окно еженедельного топа (7 дней) в секундах.
WEEK_SECONDS = 7 * 24 * 3600


async def fetch_fresh_invites(guild: discord.Guild) -> tuple[Optional[dict[str, tuple]], Optional[str]]:
    """Текущие приглашения сервера: код -> (id автора или None, использования).

    Второе значение — примечание об ошибке для лога: без права «Управление
    сервером» список приглашений Discord не отдаёт вовсе.
    """
    try:
        invites = await guild.invites()
    except discord.Forbidden:
        return None, "нужно право «Управление сервером» (Manage Server)"
    except discord.HTTPException:
        return None, "Discord не отдал список приглашений"

    fresh: dict[str, tuple] = {
        inv.code: (inv.inviter.id if inv.inviter else None, inv.uses) for inv in invites
    }
    # Ванити-ссылка сервера (discord.gg/слаг) в guild.invites() не входит.
    if guild.vanity_url_code:
        try:
            vanity = await guild.vanity_invite()
            fresh[vanity.code] = (None, vanity.uses)
        except (discord.Forbidden, discord.NotFound, discord.HTTPException):
            pass
    return fresh, None


async def refresh_cache(guild: discord.Guild) -> bool:
    """Пересобирает снимок приглашений в базе. False — сервер недоступен."""
    fresh, _note = await fetch_fresh_invites(guild)
    if fresh is None:
        return False
    db.replace_invite_cache(guild.id, [(code, inviter_id, uses) for code, (inviter_id, uses) in fresh.items()])
    return True


async def attribute_join(guild: discord.Guild) -> tuple[Optional[dict], Optional[str]]:
    """Определяет, по какому приглашению зашёл участник.

    Возвращает ({inviter_id, code}, примечание об ошибке). Информация равна
    None, если автора определить не удалось; снимок в базе обновляется в любом
    случае, чтобы следующее сравнение было честным.
    """
    cached = db.get_invite_cache(guild.id)
    fresh, note = await fetch_fresh_invites(guild)
    if fresh is None:
        return None, note
    db.replace_invite_cache(guild.id, [(code, inviter_id, uses) for code, (inviter_id, uses) in fresh.items()])

    if not cached:
        return None, None  # самый первый снимок — пока не с чем сравнивать

    best_code: Optional[str] = None
    best_gain = 0
    for code, (_inviter_id, uses) in fresh.items():
        old = cached.get(code)
        gain = uses - (old[1] if old else 0)
        if gain > best_gain:
            best_code, best_gain = code, gain

    if best_code is None:
        return None, None
    inviter_id = fresh[best_code][0]
    return {"inviter_id": inviter_id, "code": best_code}, None


def resolve_log_channel(guild: discord.Guild, cfg: dict) -> Optional[discord.TextChannel]:
    """Канал логов входов/выходов: своя настройка трекера, иначе общий лог-канал тикетов."""
    channel_id = cfg.get("inv_log_channel_id") or cfg.get("log_channel_id")
    channel = guild.get_channel(channel_id) if channel_id else None
    return channel if isinstance(channel, discord.TextChannel) else None


def join_embed(member: discord.Member, info: Optional[dict], note: Optional[str]) -> discord.Embed:
    """Лог входа: кто и по чьему приглашению зашёл."""
    if info is None:
        description = f"📥 {member.mention} зашёл на сервер — по какому приглашению, точно определить не удалось."
    elif info["inviter_id"] is None:
        description = (
            f"📥 {member.mention} зашёл по ванити-ссылке сервера"
            f" `discord.gg/{member.guild.vanity_url_code}`."
        )
    else:
        total = db.invite_counts(member.guild.id, info["inviter_id"])["total"]
        inviter = member.guild.get_member(info["inviter_id"])
        who = inviter.mention if inviter else f"<@{info['inviter_id']}>"
        description = f"📥 {member.mention} зашёл по приглашению {who} — теперь у него **{total}**."

    embed = discord.Embed(description=description, color=discord.Color.green())
    if info and info.get("code"):
        embed.set_footer(text=f"код приглашения: {info['code']}")
    elif note:
        embed.set_footer(text=note)
    return embed


def leave_embed(member: discord.Member, counted: bool) -> discord.Embed:
    """Лог выхода. counted=True — выход уменьшил «живые» приглашения автора."""
    description = f"📤 {member.mention} покинул сервер."
    if counted:
        description += "\nОдно из его приглашений больше не считается «живым»."
    return discord.Embed(description=description, color=discord.Color.dark_grey())


def top_embed(guild: discord.Guild, rows: list[dict], title: str) -> discord.Embed:
    """Таблица топа для /invitetop и еженедельной автопубликации."""
    medals = ["🥇", "🥈", "🥉"]
    lines = []
    for place, row in enumerate(rows, start=1):
        medal = medals[place - 1] if place <= len(medals) else f"**{place}.**"
        stayed = row["stayed"] or 0
        lines.append(
            f"{medal} <@{row['inviter_id']}> — **{row['total']}**"
            + (f" (из них осталось: {stayed})" if stayed != row["total"] else "")
        )
    description = "\n".join(lines) if lines else "За эту неделю новых приглашений никто не принёс."
    return discord.Embed(title=title, description=description, color=discord.Color.gold())
