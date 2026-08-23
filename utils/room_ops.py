"""Бизнес-логика Room Creator: создание, настройка и удаление
временных голосовых комнат. Аналог utils/ticket_ops.py — функции
не отвечают на взаимодействия сами, это делает вызывающий код.

Панель управления живёт в текстовом канале самой комнаты, поэтому
комната всегда определяется по interaction.channel_id (см. room_views).
"""

import re
import traceback
from typing import Optional

import discord

import db
from utils import checks

# Максимум каналов в категории комнат — защита от переполнения (лимит Discord — 50).
MAX_CHANNELS_IN_CATEGORY = 45

# Регионы для кнопки «Регион». Пустое значение = «Автоматически» (rtc_region=None).
REGIONS: list[tuple[str, Optional[str]]] = [
    ("Автоматически", None),
    ("Россия", "russia"),
    ("Европа", "europe"),
    ("США (восток)", "us-east"),
    ("США (центр)", "us-central"),
    ("Бразилия", "brazil"),
    ("Япония", "japan"),
    ("Сингапур", "singapore"),
    ("Индия", "india"),
    ("Австралия", "sydney"),
    ("ЮАР", "southafrica"),
]


def resolve_room(channel) -> Optional[dict]:
    """Комната по текстовому каналу панели (или по самому голосовому)."""
    if isinstance(channel, discord.TextChannel):
        return db.find_room_by_text(channel.id)
    if isinstance(channel, discord.VoiceChannel):
        return db.get_room(channel.id)
    return None


def room_display_name(guild: discord.Guild, room: dict) -> str:
    member = guild.get_member(room["owner_id"])
    return member.display_name if member else f"id{room['owner_id']}"


def _render_room_name(template: str, member: discord.Member, guild: discord.Guild) -> str:
    try:
        name = template.format(user=member.display_name, server=guild.name)
    except (KeyError, IndexError, ValueError):
        name = template.replace("{user}", member.display_name).replace("{server}", guild.name)
    name = re.sub(r"[\n@`]", " ", name).strip() or member.display_name
    return name[:100]


# === ПРОВЕРКА ПРАВ ===========================================================

async def ensure_manager(
    interaction: discord.Interaction, room: dict, cfg: dict
) -> Optional[discord.Member]:
    """Member, если нажавший — владелец комнаты или стафф; иначе ephemeral-отказ."""
    member = await checks.ensure_guild(interaction)
    if member is None:
        return None
    if member.id == room["owner_id"] or checks.is_staff(member, cfg["staff_role_ids"]):
        return member
    await interaction.response.send_message(
        "⛔ Управлять комнатой может только её владелец.", ephemeral=True
    )
    return None


# === СОЗДАНИЕ / УДАЛЕНИЕ =====================================================

async def spawn_room_for_member(member: discord.Member, trigger: discord.VoiceChannel) -> None:
    """Зашёл в триггер → создать пару (войс + текстовый канал панели) и переместить."""
    guild = member.guild
    cfg = db.get_config(guild.id)

    category = guild.get_channel(cfg["voice_category_id"]) if cfg["voice_category_id"] else None
    if not isinstance(category, discord.CategoryChannel):
        category = trigger.category
    if category is None:
        _try_dm(member, "❌ Комната не создана: для голосовых комнат не задана категория "
                        "(`/config voice category`).")
        return

    existing = db.find_room_of_owner(guild.id, member.id)
    if existing:
        alive = guild.get_channel(existing["voice_channel_id"])
        if isinstance(alive, discord.VoiceChannel):
            try:
                await member.move_to(alive)
            except discord.HTTPException:
                pass
            return
        # Голосовой канал снесли вручную: подчищаем остатки (кабинет, прихожую),
        # чтобы они не висели сиротами, и создаём комнату заново.
        await delete_room_everything(guild, existing)

    if len(category.channels) >= MAX_CHANNELS_IN_CATEGORY:
        _try_dm(member, "❌ В категории комнат закончилось место — попробуйте позже.")
        return

    template = cfg.get("voice_name_template") or "{user}"
    room_name = _render_room_name(template, member, guild)

    overwrites: dict = {
        guild.default_role: discord.PermissionOverwrite(read_messages=False),
        member: discord.PermissionOverwrite(
            read_messages=True, send_messages=True, connect=True
        ),
        guild.me: discord.PermissionOverwrite(
            read_messages=True, send_messages=True, connect=True, manage_channels=True
        ),
    }
    for role_id in cfg["staff_role_ids"]:
        role = guild.get_role(role_id)
        if role:
            overwrites[role] = discord.PermissionOverwrite(read_messages=True, send_messages=True)

    try:
        voice = await category.create_voice_channel(
            name=room_name,
            user_limit=cfg.get("voice_user_limit") or None,
            overwrites={
                guild.me: overwrites[guild.me],
                member: discord.PermissionOverwrite(connect=True),
            },
            reason=f"Room Creator: комната {member}",
        )
    except discord.Forbidden:
        _try_dm(member, "❌ У бота нет прав создавать голосовые каналы в категории комнат.")
        return

    try:
        text = await category.create_text_channel(
            name=f"кабинет-{member.display_name}"[:90].lower(),
            overwrites=overwrites,
            reason=f"Room Creator: панель комнаты {member}",
        )
    except discord.Forbidden:
        _try_dm(member, "❌ У бота нет прав создавать текстовые каналы — комната удалена.")
        try:
            await voice.delete(reason="Room Creator: не удалось создать панель")
        except discord.HTTPException:
            pass
        return

    db.create_room(voice.id, guild.id, text.id, member.id, chat_hidden=True)

    from utils.room_views import RoomControlView  # лениво: избегаем цикла импортов
    try:
        panel = await text.send(embed=build_room_embed(guild, db.get_room(voice.id)),
                                view=RoomControlView())
        db.update_room(voice.id, panel_message_id=panel.id)
    except discord.HTTPException:
        traceback.print_exc()

    try:
        await text.send(
            f"{member.mention}, ваша комната готова: **{voice.name}**. "
            f"Управляйте ею кнопками выше. Чтобы позвать друзей — включите «Чат» "
            f"или пригласите их напрямую."
        )
    except discord.HTTPException:
        pass

    try:
        await member.move_to(voice)
    except discord.HTTPException:
        _try_dm(member, f"✅ Комната **{voice.name}** создана, но переместить вас не удалось "
                        f"— зайдите в неё вручную.")


def _try_dm(member: discord.Member, text: str) -> None:
    try:
        member.send(text)
    except (discord.Forbidden, discord.HTTPException):
        pass


async def delete_room_everything(guild: discord.Guild, room: dict) -> None:
    """Удаляет каналы комнаты (если ещё живы) и запись из базы."""
    for key in ("lobby_channel_id", "text_channel_id", "voice_channel_id"):
        channel_id = room.get(key)
        channel = guild.get_channel(channel_id) if channel_id else None
        if channel is not None:
            try:
                await channel.delete(reason="Room Creator: комната закрыта")
            except discord.HTTPException:
                traceback.print_exc()
    db.delete_room(room["voice_channel_id"])


# === ПАНЕЛЬ ==================================================================

def build_room_embed(guild: discord.Guild, room: dict) -> discord.Embed:
    voice = guild.get_channel(room["voice_channel_id"])
    access = "🔒 приватная" if room["is_private"] else "🌍 публичная"
    limit = str(voice.user_limit) if voice and voice.user_limit else "без лимита"
    region = (voice.rtc_region.name if voice and voice.rtc_region else "Автоматически") \
        if voice else "—"
    members = len([m for m in voice.members if not m.bot]) if voice else 0
    embed = discord.Embed(
        title="🎛️ Панель управления комнатой",
        description=(
            "Этот интерфейс используется для управления личным каналом.\n"
            "Кнопки работают у владельца комнаты (и у стаффа)."
        ),
        color=discord.Color.blurple(),
    )
    embed.add_field(name="Владелец", value=f"<@{room['owner_id']}>")
    embed.add_field(name="Доступ", value=access)
    embed.add_field(name="Участников", value=str(members))
    embed.add_field(name="Лимит", value=limit)
    embed.add_field(name="Регион", value=region)
    embed.add_field(
        name="Прихожая",
        value="⏳ включена" if room["lobby_enabled"] else "выключена",
    )
    embed.add_field(
        name="Чат комнаты",
        value="💬 открыт всем" if not room["chat_hidden"] else "только владелец и стафф",
        inline=False,
    )
    return embed


async def refresh_panel(guild: discord.Guild, room: dict) -> None:
    """Перерисовывает embed панели. Молча пропускает, если сообщение удалено."""
    panel_id = room.get("panel_message_id")
    text = guild.get_channel(room.get("text_channel_id") or 0)
    if not panel_id or not isinstance(text, discord.TextChannel):
        return
    from utils.room_views import RoomControlView
    try:
        await text.get_partial_message(panel_id).edit(
            embed=build_room_embed(guild, db.get_room(room["voice_channel_id"]) or room),
            view=RoomControlView(),
        )
    except (discord.NotFound, discord.HTTPException):
        pass


# === НАСТРОЙКИ КОМНАТЫ =======================================================

async def set_access(guild: discord.Guild, room: dict, private: bool) -> None:
    voice = guild.get_channel(room["voice_channel_id"])
    if isinstance(voice, discord.VoiceChannel):
        try:
            if private:
                await voice.set_permissions(
                    guild.default_role, connect=False, reason="Комната стала приватной"
                )
            else:
                await _neutralize_overwrite(voice, guild.default_role, "connect")
        except discord.HTTPException:
            traceback.print_exc()
    db.update_room(room["voice_channel_id"], is_private=int(private))


async def set_name(guild: discord.Guild, room: dict, name: str) -> None:
    voice = guild.get_channel(room["voice_channel_id"])
    if isinstance(voice, discord.VoiceChannel):
        await voice.edit(name=name[:100])


async def set_limit(guild: discord.Guild, room: dict, limit: int) -> None:
    voice = guild.get_channel(room["voice_channel_id"])
    if isinstance(voice, discord.VoiceChannel):
        await voice.edit(user_limit=limit or None)


async def set_region(guild: discord.Guild, room: dict, region: Optional[str]) -> str:
    """Возвращает None при успехе или текст ошибки (регион недоступен)."""
    voice = guild.get_channel(room["voice_channel_id"])
    if not isinstance(voice, discord.VoiceChannel):
        return "Голосовой канал комнаты не найден."
    try:
        await voice.edit(rtc_region=region)
        return ""
    except discord.HTTPException:
        return "⛔ Discord не дал сменить регион — для этого сервера он недоступен."


async def toggle_chat(guild: discord.Guild, room: dict, hidden: bool) -> None:
    text = guild.get_channel(room.get("text_channel_id") or 0)
    if isinstance(text, discord.TextChannel):
        try:
            if hidden:
                await text.set_permissions(guild.default_role, read_messages=False)
            else:
                await text.set_permissions(
                    guild.default_role, read_messages=True, send_messages=True
                )
        except discord.HTTPException:
            traceback.print_exc()
    db.update_room(room["voice_channel_id"], chat_hidden=int(hidden))


async def apply_trust(guild: discord.Guild, room: dict, target: discord.Member, trust: bool) -> None:
    """Доверять — разрешить говорить/стримить; не доверять — заглушить на уровне канала."""
    voice = guild.get_channel(room["voice_channel_id"])
    if not isinstance(voice, discord.VoiceChannel):
        return
    flags = ("speak", "stream", "use_voice_activation")
    try:
        if trust:
            await _neutralize_overwrite(voice, target, *flags)
        else:
            await voice.set_permissions(
                target, speak=False, stream=False, use_voice_activation=False
            )
    except discord.HTTPException:
        traceback.print_exc()


async def ban_member(guild: discord.Guild, room: dict, target: discord.Member) -> None:
    voice = guild.get_channel(room["voice_channel_id"])
    if not isinstance(voice, discord.VoiceChannel):
        return
    try:
        await voice.set_permissions(target, connect=False)
    except discord.HTTPException:
        traceback.print_exc()
        return
    if target.voice and target.voice.channel and target.voice.channel.id == voice.id:
        await _disconnect(target)


async def unban_member(guild: discord.Guild, room: dict, target: discord.Member) -> None:
    voice = guild.get_channel(room["voice_channel_id"])
    if isinstance(voice, discord.VoiceChannel):
        await _neutralize_overwrite(voice, target, "connect")


async def kick_member(room: dict, target: discord.Member) -> bool:
    voice = room["voice_channel_id"]
    if target.voice and target.voice.channel and target.voice.channel.id == voice:
        await _disconnect(target)
        return True
    return False


async def _disconnect(member: discord.Member) -> None:
    try:
        await member.move_to(None)
    except discord.HTTPException:
        pass


async def _neutralize_overwrite(
    channel: discord.abc.GuildChannel, target, *flags: str
) -> None:
    """Снимает указанные флаги персонального оверрайда (делает их нейтральными),
    а если запись опустела — удаляет её целиком."""
    over = channel.overwrites_for(target)
    for flag in flags:
        setattr(over, flag, None)
    await channel.set_permissions(
        target, overwrite=None if over.is_empty() else over
    )


# === ПЕРЕДАЧА / ЗАБРАТЬ ======================================================

async def transfer_ownership(guild: discord.Guild, room: dict, new_owner: discord.Member) -> None:
    old_owner_id = room["owner_id"]
    db.update_room(room["voice_channel_id"], owner_id=new_owner.id)
    text = guild.get_channel(room.get("text_channel_id") or 0)
    if isinstance(text, discord.TextChannel):
        # Кабинет видят только владелец и стафф: выдаём его новому владельцу,
        # иначе он получит комнату, кнопки которой ему не видны. У старого
        # персональный доступ забираем (доступ по ролям стаффа не трогаем).
        old_owner = guild.get_member(old_owner_id)
        if old_owner is not None and old_owner.id != new_owner.id:
            try:
                await _neutralize_overwrite(
                    text, old_owner,
                    "read_messages", "send_messages", "attach_files", "embed_links",
                )
            except discord.HTTPException:
                traceback.print_exc()
        try:
            await text.set_permissions(new_owner, read_messages=True, send_messages=True)
        except discord.HTTPException:
            traceback.print_exc()
        try:
            await text.send(f"👑 {new_owner.mention} — новый владелец комнаты.")
        except discord.HTTPException:
            pass
    await refresh_panel(guild, db.get_room(room["voice_channel_id"]) or room)


def voice_members(voice: Optional[discord.VoiceChannel]) -> list[discord.Member]:
    if voice is None:
        return []
    return [m for m in voice.members if not m.bot]


# === ПРИХОЖАЯ ================================================================

async def set_lobby(guild: discord.Guild, room: dict, enabled: bool) -> bool:
    """Включает/выключает прихожую. False — не удалось (нет прав на канал)."""
    voice = guild.get_channel(room["voice_channel_id"])
    if not isinstance(voice, discord.VoiceChannel):
        return False
    if enabled:
        overwrites: dict = {
            guild.default_role: discord.PermissionOverwrite(connect=True, read_messages=True),
            guild.me: discord.PermissionOverwrite(connect=True, read_messages=True, move_members=True),
        }
        owner_member = guild.get_member(room["owner_id"])
        if owner_member is not None:
            overwrites[owner_member] = discord.PermissionOverwrite(
                connect=True, read_messages=True, move_members=True
            )
        try:
            lobby = await voice.category.create_voice_channel(
                name=f"⏳ прихожая · {voice.name}"[:100],
                overwrites=overwrites,
                position=voice.position,
                reason="Room Creator: прихожая",
            )
        except discord.HTTPException:
            traceback.print_exc()
            return False
        db.update_room(room["voice_channel_id"], lobby_channel_id=lobby.id, lobby_enabled=1)
    else:
        lobby = guild.get_channel(room.get("lobby_channel_id") or 0)
        if lobby is not None:
            try:
                await lobby.delete(reason="Room Creator: прихожая выключена")
            except discord.HTTPException:
                pass
        db.update_room(room["voice_channel_id"], lobby_channel_id=None, lobby_enabled=0)
    return True


async def post_join_request(guild: discord.Guild, room: dict, requester: discord.Member) -> None:
    """Кто-то зашёл в прихожую → запрос владельцу в текстовый канал комнаты."""
    text = guild.get_channel(room.get("text_channel_id") or 0)
    if not isinstance(text, discord.TextChannel):
        return
    from utils.room_views import LobbyRequestView
    try:
        await text.send(
            f"🚪 {requester.mention} ждёт в прихожей и просит впустить его в комнату.",
            view=LobbyRequestView(room["voice_channel_id"], requester.id),
        )
    except discord.HTTPException:
        traceback.print_exc()


# === ЖИЗНЕННЫЙ ЦИКЛ ==========================================================

async def handle_owner_left(guild: discord.Guild, room: dict) -> None:
    """Владелец вышел, но в комнате остались люди → передать первому оставшемуся."""
    voice = guild.get_channel(room["voice_channel_id"])
    remaining = voice_members(voice)
    if remaining:
        await transfer_ownership(guild, room, remaining[0])
    else:
        await delete_room_everything(guild, room)


def room_voice(guild: discord.Guild, room: dict) -> Optional[discord.VoiceChannel]:
    channel = guild.get_channel(room["voice_channel_id"])
    return channel if isinstance(channel, discord.VoiceChannel) else None
