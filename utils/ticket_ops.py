"""Бизнес-логика тикетов без привязки к конкретным кнопкам.

Функции здесь используются и когом tickets (команды/кнопки),
и когом autoclose (фоновое закрытие), поэтому они не отправляют
ответы на взаимодействия сами — это делает вызывающий код.
"""

import asyncio
import re
import traceback
from datetime import datetime, timezone
from typing import Optional

import discord

import db
from utils import checks
from utils.transcripts import build_transcript_file
from utils.views import OpenControlsView, RatingStarsView, TicketReasonModal, build_rating_embed

# Защита от двойного клика по панели: пока заявка создаётся, повторные
# сабмиты того же пользователя отклоняются.
_creating: set[int] = set()


# === ОПРЕДЕЛЕНИЕ ТИКЕТА ======================================================

def resolve_ticket(channel) -> Optional[dict]:
    """Ищет тикет в базе; для каналов старой версии собирает запись по теме канала."""
    if not isinstance(channel, discord.TextChannel):
        return None
    row = db.get_ticket(channel.id)
    if row:
        return row
    owner_id = checks.owner_from_topic(channel)
    if owner_id is None:
        return None
    return {
        "channel_id": channel.id,
        "guild_id": channel.guild.id,
        "owner_id": owner_id,
        "category": "",
        "number": 0,
        "claimed_by": checks.claimed_from_topic(channel),
        "status": "closed" if channel.name.startswith("closed-") else "open",
        "created_at": int(discord.utils.snowflake_time(channel.id).timestamp()),
        "closed_at": None,
        "warn_sent_at": None,
    }


# === СОЗДАНИЕ ================================================================

async def open_modal_for_category(interaction: discord.Interaction, category_label: str) -> None:
    cfg = db.get_config(interaction.guild_id)
    known = any(c["label"] == category_label for c in cfg["ticket_categories"])
    if not known:
        await interaction.response.send_message(
            "Эта категория больше не существует. Попросите администратора обновить панель командой `/setup`.",
            ephemeral=True,
        )
        return
    await interaction.response.send_modal(TicketReasonModal(category_label))


def _channel_display_name(guild: discord.Guild, user_id: int) -> str:
    member = guild.get_member(user_id)
    return member.display_name if member else f"id{user_id}"


def format_duration(seconds: float) -> str:
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds} сек"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} мин"
    hours = minutes // 60
    return f"{hours} ч {minutes % 60} мин"


def _ts(ts: Optional[int]) -> str:
    if ts is None:
        return "—"
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%d.%m.%Y %H:%M UTC")


async def spawn_ticket(interaction: discord.Interaction, category_label: str, reason: str) -> None:
    """Создаёт канал тикета по сабмиту модалки. Полностью ведёт ответы взаимодействия."""
    assert interaction.guild is not None
    guild = interaction.guild
    user = interaction.user
    cfg = db.get_config(guild.id)

    await interaction.response.defer(ephemeral=True, thinking=True)

    category = guild.get_channel(cfg["category_id"]) if cfg["category_id"] else None
    if not isinstance(category, discord.CategoryChannel):
        await interaction.followup.send(
            "❌ Категория для тикетов не настроена. Администратор может задать её командой `/config set`.",
            ephemeral=True,
        )
        return

    if user.id in _creating:
        await interaction.followup.send("⏳ Ваш прошлый тикет ещё создаётся, секунду…", ephemeral=True)
        return

    existing = db.find_open_ticket(guild.id, user.id)
    if existing:
        old_channel = guild.get_channel(existing["channel_id"])
        where = old_channel.mention if old_channel else "#удалённый-канал"
        await interaction.followup.send(
            f"❌ У вас уже есть открытый тикет: {where}. Дождитесь его закрытия.", ephemeral=True
        )
        return

    cooldown_left = db.seconds_since_last_ticket(guild.id, user.id)
    if cooldown_left is not None and cooldown_left < cfg["cooldown_seconds"]:
        wait = format_duration(cfg["cooldown_seconds"] - cooldown_left)
        await interaction.followup.send(f"⏳ Слишком часто. Попробуйте снова через {wait}.", ephemeral=True)
        return

    _creating.add(user.id)
    try:
        number = db.next_ticket_number(guild.id)
        safe_name = re.sub(r"[^a-zа-яё0-9_-]", "", user.name.lower()) or "user"
        channel_name = f"ticket-{number:04d}-{safe_name}"[:95]

        overwrites: dict = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            user: discord.PermissionOverwrite(
                read_messages=True, send_messages=True, attach_files=True, embed_links=True
            ),
            guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True),
        }
        for role_id in cfg["staff_role_ids"]:
            role = guild.get_role(role_id)
            if role:
                overwrites[role] = discord.PermissionOverwrite(
                    read_messages=True, send_messages=True
                )

        channel = await category.create_text_channel(
            name=channel_name,
            topic=checks.build_topic(user.id, category_label),
            overwrites=overwrites,
            reason=f"Тикет #{number:04d} ({category_label})",
        )
        db.create_ticket(channel.id, guild.id, user.id, category_label, number)

        staff_pings = " ".join(f"<@&{rid}>" for rid in cfg["staff_role_ids"] if guild.get_role(rid))
        await channel.send(
            content=(
                f"{user.mention}, здравствуйте! Опишите проблему подробнее, "
                f"если хотите уточнить её. {staff_pings}"
            ),
            embed=discord.Embed(
                title=f"🎧 Тикет #{number:04d} · {category_label}",
                description="Персонал скоро ответит. Закрыть обращение можно кнопкой ниже.",
                color=discord.Color.green(),
            ),
        )
        await channel.send(
            embed=discord.Embed(
                title="Причина обращения",
                description=f"```{reason[:900]}```",
                color=discord.Color.dark_grey(),
            ),
            view=OpenControlsView(),
        )
        await interaction.followup.send(f"✅ Тикет создан: {channel.mention}", ephemeral=True)
    except discord.Forbidden:
        await interaction.followup.send(
            "❌ У бота нет прав создавать каналы в категории тикетов.", ephemeral=True
        )
    except Exception:
        traceback.print_exc()
        await interaction.followup.send(
            "❌ Не удалось создать тикет. Сообщите администратору.", ephemeral=True
        )
    finally:
        _creating.discard(user.id)


# === РАБОТА В РАМКАХ ТИКЕТА ==================================================

async def claim_ticket(channel: discord.TextChannel, ticket: dict, staff: discord.Member) -> None:
    if ticket["status"] != "open":
        await channel.send("ℹ️ Тикет уже закрыт — взять в работу нельзя.")
        return
    if ticket["claimed_by"]:
        who = channel.guild.get_member(ticket["claimed_by"])
        name = who.display_name if who else f"<@{ticket['claimed_by']}>"
        await channel.send(f"ℹ️ Тикет уже взял в работу **{name}**.")
        return
    db.set_claimed(channel.id, staff.id)
    try:
        await channel.edit(topic=checks.build_topic(ticket["owner_id"], ticket["category"], staff.id))
    except discord.HTTPException:
        pass
    await channel.send(f"🙋 {staff.mention} взял тикет в работу.")


def _transcript_meta(channel: discord.TextChannel, ticket: dict, note: str) -> dict[str, str]:
    guild = channel.guild
    meta = {
        "Номер": f"#{ticket['number']:04d}" if ticket["number"] else "—",
        "Категория": ticket["category"] or "—",
        "Владелец": _channel_display_name(guild, ticket["owner_id"]),
        "Создан": _ts(ticket["created_at"]),
    }
    if ticket["claimed_by"]:
        meta["Взял в работу"] = _channel_display_name(guild, ticket["claimed_by"])
    if ticket["status"] == "closed" and ticket["closed_at"]:
        meta["Закрыт"] = _ts(ticket["closed_at"])
        meta["Время обработки"] = format_duration(ticket["closed_at"] - ticket["created_at"])
    meta["Примечание"] = note
    return meta


async def build_transcript(channel: discord.TextChannel, ticket: dict, note: str) -> discord.File:
    return await build_transcript_file(channel, _transcript_meta(channel, ticket, note))


async def send_transcript_to_logs(channel: discord.TextChannel, ticket: dict, note: str) -> bool:
    """Отправляет транскрипт в лог-канал. False, если лог-канал не настроен."""
    cfg = db.get_config(channel.guild.id)
    log_channel = channel.guild.get_channel(cfg["log_channel_id"]) if cfg["log_channel_id"] else None
    if not isinstance(log_channel, discord.TextChannel):
        return False
    file = await build_transcript(channel, ticket, note)
    try:
        await log_channel.send(content=f"📑 Транскрипт `{channel.name}`", file=file)
        return True
    except discord.HTTPException:
        traceback.print_exc()
        return False


# === ЗАКРЫТИЕ / ПЕРЕОТКРЫТИЕ / УДАЛЕНИЕ ======================================

async def perform_close(
    channel: discord.TextChannel,
    ticket: dict,
    cfg: dict,
    *,
    closed_by: Optional[discord.Member],
    rating_in_channel: bool = False,
    auto: bool = False,
) -> None:
    """Полное закрытие: права, переименование, база, транскрипт, оценка, уведомление."""
    guild = channel.guild
    owner = guild.get_member(ticket["owner_id"])

    # Просьба об оценке — ДО отъёма прав у владельца.
    if rating_in_channel:
        try:
            await channel.send(embed=build_rating_embed(), view=RatingStarsView())
        except discord.HTTPException:
            pass

    if owner is not None:
        # set_permissions принимает только Member/Role — если владелец покинул сервер,
        # править пермишены нечем и не для кого.
        try:
            await channel.set_permissions(owner, read_messages=False, reason="Закрытие тикета")
        except discord.HTTPException:
            pass

    base_name = channel.name[len("closed-"):] if channel.name.startswith("closed-") else channel.name
    target_name = f"closed-{base_name}"[:100]
    # Повторное закрытие не должно жечь лимит переименований канала (~2/10 мин):
    # лишний edit заставил бы следующее настоящее переименование висеть минутами.
    if channel.name != target_name:
        try:
            await channel.edit(name=target_name)
        except discord.HTTPException:
            pass

    db.mark_closed(channel.id)
    ticket = db.get_ticket(channel.id) or {**ticket, "status": "closed"}

    closer_label = "автоматически (неактивность)" if auto else (
        closed_by.mention if closed_by else "владельцем тикета"
    )
    await send_transcript_to_logs(channel, ticket, f"Закрыл: {closer_label}")

    # Если закрывал стафф — просим оценку в ЛС.
    if not rating_in_channel and owner is not None:
        try:
            await owner.send(embed=build_rating_embed(), view=RatingStarsView())
        except (discord.Forbidden, discord.HTTPException):
            pass

    embed = discord.Embed(
        description=f"🔒 Тикет закрыт ({closer_label}).", color=discord.Color.gold()
    )
    from utils.views import ClosedControlsView
    await channel.send(embed=embed, view=ClosedControlsView())


async def perform_reopen(channel: discord.TextChannel, ticket: dict) -> str:
    guild = channel.guild
    owner = guild.get_member(ticket["owner_id"])
    if owner is not None:
        try:
            await channel.set_permissions(
                owner,
                read_messages=True,
                send_messages=True,
                attach_files=True,
                embed_links=True,
                reason="Переоткрытие тикета",
            )
        except discord.HTTPException:
            pass

    new_name = channel.name
    if new_name.startswith("closed-"):
        stripped = new_name[len("closed-"):]
        # Префикс «ticket-» уже сидит в stripped; дописываем его только
        # старым каналам без него, иначе получается ticket-ticket-NNNN.
        new_name = stripped if stripped.startswith("ticket-") else f"ticket-{stripped}"
        try:
            await channel.edit(name=new_name[:100])
        except discord.HTTPException:
            pass

    db.mark_reopened(channel.id)
    who = owner.mention if owner else f"<@{ticket['owner_id']}>"
    return f"🔓 Тикет снова открыт ({who})!"


async def perform_delete(interaction: discord.Interaction) -> None:
    channel = interaction.channel
    await interaction.response.send_message("💥 Тикет будет удалён через 5 секунд…")
    await asyncio.sleep(5)
    db.delete_ticket(channel.id)
    try:
        await channel.delete(reason="Удаление тикета стаффом")
    except discord.NotFound:
        pass
