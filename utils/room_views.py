"""Панель управления личной голосовой комнатой (Room Creator).

Панель живёт в текстовом канале самой комнаты, поэтому комната всегда
определяется по interaction.channel_id — все custom_id статические,
как у звёзд рейтинга тикетов. View регистрируется в боте ровно один раз
(cogs/rooms.py, cog_load) и переживает рестарты.

Временные компоненты (селекты выбора участника, подтверждения, запросы
из прихожей) уходят ephemeral-сообщениями и сознательно НЕ регистрируются
постоянно: после рестарта они просто перестают быть нажимаемыми.
"""

import time

import discord

import db
from utils import room_ops
from utils.views import BasePersistentView


# === МОДАЛКИ =================================================================

class RoomNameModal(discord.ui.Modal):
    def __init__(self, current_name: str):
        super().__init__(title="Название комнаты")
        self.field = discord.ui.TextInput(
            label="Как назвать канал?",
            placeholder="Например: Играемся с друзьями",
            default=current_name[:100],
            max_length=100,
            required=True,
        )
        self.add_item(self.field)

    async def on_submit(self, interaction: discord.Interaction):
        room = room_ops.resolve_room(interaction.channel)
        if room is None:
            await interaction.response.send_message("Комната больше не существует.", ephemeral=True)
            return
        try:
            await room_ops.set_name(interaction.guild, room, str(self.field))
        except discord.HTTPException:
            await interaction.response.send_message(
                "⛔ Discord не дал переименовать канал.", ephemeral=True
            )
            return
        await interaction.response.send_message("✅ Название обновлено.", ephemeral=True)
        await room_ops.refresh_panel(interaction.guild, db.get_room(room["voice_channel_id"]) or room)


class RoomLimitModal(discord.ui.Modal):
    def __init__(self, current_limit: int):
        super().__init__(title="Лимит участников")
        self.field = discord.ui.TextInput(
            label="Максимум людей в канале (0 — без лимита)",
            placeholder="Например: 5",
            default=str(current_limit),
            max_length=2,
            required=True,
        )
        self.add_item(self.field)

    async def on_submit(self, interaction: discord.Interaction):
        raw = str(self.field).strip()
        if not raw.isdigit() or int(raw) > 99:
            await interaction.response.send_message(
                "Введите число от 0 до 99.", ephemeral=True
            )
            return
        room = room_ops.resolve_room(interaction.channel)
        if room is None:
            await interaction.response.send_message("Комната больше не существует.", ephemeral=True)
            return
        try:
            await room_ops.set_limit(interaction.guild, room, int(raw))
        except discord.HTTPException:
            await interaction.response.send_message("⛔ Не удалось изменить лимит.", ephemeral=True)
            return
        word = "лимит снят" if int(raw) == 0 else f"лимит: {int(raw)}"
        await interaction.response.send_message(f"✅ {word.capitalize()}.", ephemeral=True)
        await room_ops.refresh_panel(interaction.guild, db.get_room(room["voice_channel_id"]) or room)


# === ВРЕМЕННЫЕ ВИДЫ (ephemeral) ==============================================

class UserPickView(BasePersistentView):
    """Выбор участника для действий «доверять/не доверять», «пригласить» и т.д."""

    def __init__(self, action: str):
        super().__init__(timeout=180)
        self.action = action
        self.pick = discord.ui.UserSelect(
            placeholder="Кого выбираем?",
            min_values=1,
            max_values=1,
            custom_id=f"vc_pick_{action}_{time.time_ns()}",
        )
        self.pick.callback = self._picked
        self.add_item(self.pick)

    async def _picked(self, interaction: discord.Interaction):
        room = room_ops.resolve_room(interaction.channel)
        if room is None:
            await interaction.response.edit_message(content="Комната больше не существует.", view=None)
            return
        target = self.pick.values[0]
        if not isinstance(target, discord.Member):
            # Селект позволяет выбрать пользователя не с сервера: с ним нельзя
            # работать set_permissions (только Member/Role — см. правило №2).
            await interaction.response.edit_message(
                content="Выберите участника этого сервера.", view=None
            )
            return
        guild = interaction.guild
        voice = room_ops.room_voice(guild, room)
        result = ""

        if self.action == "trust":
            await room_ops.apply_trust(guild, room, target, True)
            result = f"✅ {target.mention} теперь может говорить и стримить."
        elif self.action == "distrust":
            await room_ops.apply_trust(guild, room, target, False)
            result = f"🔇 {target.mention} заглушен(а) в этой комнате."
        elif self.action == "invite":
            try:
                await voice.set_permissions(target, connect=True)
            except discord.HTTPException:
                pass
            if (
                room.get("lobby_channel_id")
                and target.voice
                and target.voice.channel
                and target.voice.channel.id == room["lobby_channel_id"]
            ):
                try:
                    await target.move_to(voice)
                except discord.HTTPException:
                    pass
            result = f"📨 {target.mention} приглашён(а) в комнату."
        elif self.action == "kick":
            moved = await room_ops.kick_member(room, target)
            result = (
                f"📤 {target.mention} выгнан(а) из комнаты." if moved
                else f"{target.mention} сейчас не в вашей комнате."
            )
        elif self.action == "ban":
            if target.id == room["owner_id"]:
                result = "⛔ Себя забанить нельзя."
            else:
                await room_ops.ban_member(guild, room, target)
                result = f"⛔ {target.mention} забанен(а) и выгнан(а) из комнаты."
        elif self.action == "unban":
            await room_ops.unban_member(guild, room, target)
            result = f"🔓 {target.mention} снова может зайти в комнату."
        elif self.action == "transfer":
            if target.id == room["owner_id"]:
                result = "Вы уже владелец этой комнаты."
            elif not (target.voice and target.voice.channel and target.voice.channel.id == voice.id):
                result = "⛔ Передавать можно только тому, кто сейчас в комнате."
            else:
                await room_ops.transfer_ownership(guild, room, target)
                result = f"👑 Комната передана {target.mention}."

        await interaction.response.edit_message(content=result, view=None)


class RegionSelectView(BasePersistentView):
    def __init__(self):
        super().__init__(timeout=120)
        options = [
            discord.SelectOption(label=label, value=value or "auto")
            for label, value in room_ops.REGIONS
        ]
        select = discord.ui.Select(
            placeholder="Регион сервера голосового канала",
            options=options,
            custom_id=f"vc_region_{time.time_ns()}",
        )
        select.callback = self._picked
        self.add_item(select)

    async def _picked(self, interaction: discord.Interaction):
        room = room_ops.resolve_room(interaction.channel)
        if room is None:
            await interaction.response.edit_message(content="Комната больше не существует.", view=None)
            return
        chosen = interaction.data["values"][0]
        region = None if chosen == "auto" else chosen
        error = await room_ops.set_region(interaction.guild, room, region)
        if error:
            await interaction.response.edit_message(content=error, view=None)
            return
        label = next(lbl for lbl, val in room_ops.REGIONS if (val or "auto") == chosen)
        await interaction.response.edit_message(content=f"🌍 Регион: **{label}**.", view=None)
        await room_ops.refresh_panel(interaction.guild, db.get_room(room["voice_channel_id"]) or room)


class RoomDeleteConfirmView(BasePersistentView):
    def __init__(self):
        super().__init__(timeout=60)

    @discord.ui.button(label="Удалить", emoji="🗑️", style=discord.ButtonStyle.danger,
                       custom_id="vc_delete_confirm_tmp")
    async def confirm(self, interaction: discord.Interaction, _: discord.ui.Button):
        room = room_ops.resolve_room(interaction.channel)
        if room is None:
            await interaction.response.edit_message(content="Комната уже удалена.", view=None)
            return
        cfg = db.get_config(interaction.guild_id)
        member = await room_ops.ensure_manager(interaction, room, cfg)
        if member is None:
            return
        await interaction.response.edit_message(content="💥 Удаляю комнату…", view=None)
        await room_ops.delete_room_everything(interaction.guild, room)

    @discord.ui.button(label="Отмена", emoji="↩️", style=discord.ButtonStyle.secondary,
                       custom_id="vc_delete_cancel_tmp")
    async def cancel(self, interaction: discord.Interaction, _: discord.ui.Button):
        await interaction.response.edit_message(content="Удаление отменено.", view=None)


class LobbyRequestView(BasePersistentView):
    """Запрос из прихожей: владелец принимает или отклоняет."""

    def __init__(self, room_voice_id: int, requester_id: int):
        super().__init__(timeout=3600)
        self.room_voice_id = room_voice_id
        self.requester_id = requester_id
        stamp = time.time_ns()
        yes = discord.ui.Button(
            label="Впустить", emoji="✅", style=discord.ButtonStyle.success,
            custom_id=f"vc_req_yes_{requester_id}_{stamp}",
        )
        no = discord.ui.Button(
            label="Отклонить", emoji="❌", style=discord.ButtonStyle.danger,
            custom_id=f"vc_req_no_{requester_id}_{stamp}",
        )
        yes.callback = self._accept
        no.callback = self._decline
        self.add_item(yes)
        self.add_item(no)

    def _requester(self, guild: discord.Guild):
        member = guild.get_member(self.requester_id)
        lobby = guild.get_channel(
            (db.get_room(self.room_voice_id) or {}).get("lobby_channel_id") or 0
        )
        sitting_in_lobby = bool(
            member and member.voice and member.voice.channel and lobby
            and member.voice.channel.id == lobby.id
        )
        return member, sitting_in_lobby

    async def _accept(self, interaction: discord.Interaction):
        room = db.get_room(self.room_voice_id)
        if room is None or interaction.guild.get_channel(self.room_voice_id) is None:
            await interaction.response.edit_message(content="Комната уже распалась.", view=None)
            return
        cfg = db.get_config(interaction.guild_id)
        member = await room_ops.ensure_manager(interaction, room, cfg)
        if member is None:
            return
        requester, in_lobby = self._requester(interaction.guild)
        if requester is None:
            await interaction.response.edit_message(content="Просивший уже ушёл с сервера.", view=None)
            return
        voice = room_ops.room_voice(interaction.guild, room)
        try:
            await voice.set_permissions(requester, connect=True)
        except discord.HTTPException:
            pass
        if in_lobby:
            try:
                await requester.move_to(voice)
            except discord.HTTPException:
                pass
        await interaction.response.edit_message(
            content=f"✅ {requester.mention} впущен(а) ({member.display_name}).", view=None
        )

    async def _decline(self, interaction: discord.Interaction):
        room = db.get_room(self.room_voice_id)
        if room is None:
            await interaction.response.edit_message(content="Комната уже распалась.", view=None)
            return
        cfg = db.get_config(interaction.guild_id)
        member = await room_ops.ensure_manager(interaction, room, cfg)
        if member is None:
            return
        requester, in_lobby = self._requester(interaction.guild)
        if requester is not None and in_lobby:
            try:
                await requester.move_to(None)
            except discord.HTTPException:
                pass
        who = requester.mention if requester else f"<@{self.requester_id}>"
        await interaction.response.edit_message(
            content=f"❌ {who} — отказано ({member.display_name}).", view=None
        )


# === ОСНОВНАЯ ПАНЕЛЬ =========================================================

class RoomControlView(BasePersistentView):
    """15 кнопок как на референсе: 3 ряда по 5. Живёт под embed-панелью комнаты."""

    def __init__(self):
        super().__init__(timeout=None)

    async def _prep(self, interaction: discord.Interaction):
        """Общий вход: найти комнату по каналу панели и проверить права."""
        room = room_ops.resolve_room(interaction.channel)
        if room is None:
            await interaction.response.send_message(
                "Это не комната — панель здесь не работает.", ephemeral=True
            )
            return None
        cfg = db.get_config(interaction.guild_id)
        member = await room_ops.ensure_manager(interaction, room, cfg)
        if member is None:
            return None
        return room, cfg, member

    # --- Ряд 1 -------------------------------------------------------------

    @discord.ui.button(label="НАЗВАНИЕ", emoji="📝", style=discord.ButtonStyle.secondary,
                       row=0, custom_id="vc_name")
    async def btn_name(self, interaction: discord.Interaction, _: discord.ui.Button):
        prep = await self._prep(interaction)
        if prep is None:
            return
        room, _, _ = prep
        voice = room_ops.room_voice(interaction.guild, room)
        await interaction.response.send_modal(RoomNameModal(voice.name if voice else ""))

    @discord.ui.button(label="ЛИМИТ", emoji="👥", style=discord.ButtonStyle.secondary,
                       row=0, custom_id="vc_limit")
    async def btn_limit(self, interaction: discord.Interaction, _: discord.ui.Button):
        prep = await self._prep(interaction)
        if prep is None:
            return
        room, _, _ = prep
        voice = room_ops.room_voice(interaction.guild, room)
        await interaction.response.send_modal(RoomLimitModal(voice.user_limit if voice else 0))

    @discord.ui.button(label="ДОСТУП", emoji="🔒", style=discord.ButtonStyle.primary,
                       row=0, custom_id="vc_access")
    async def btn_access(self, interaction: discord.Interaction, _: discord.ui.Button):
        prep = await self._prep(interaction)
        if prep is None:
            return
        room, _, _ = prep
        new_private = not bool(room["is_private"])
        await interaction.response.defer()
        await room_ops.set_access(interaction.guild, room, new_private)
        state = "🔒 комната закрыта — зайти могут только приглашённые" \
            if new_private else "🌍 комната открыта для всех"
        await interaction.followup.send(state, ephemeral=True)
        await room_ops.refresh_panel(interaction.guild, db.get_room(room["voice_channel_id"]) or room)

    @discord.ui.button(label="ПРИХОЖАЯ", emoji="⏳", style=discord.ButtonStyle.secondary,
                       row=0, custom_id="vc_lobby")
    async def btn_lobby(self, interaction: discord.Interaction, _: discord.ui.Button):
        prep = await self._prep(interaction)
        if prep is None:
            return
        room, _, _ = prep
        enable = not bool(room["lobby_enabled"])
        await interaction.response.defer()
        ok = await room_ops.set_lobby(interaction.guild, room, enable)
        if not ok:
            await interaction.followup.send(
                "⛔ Не удалось создать канал прихожей.", ephemeral=True
            )
            return
        state = "⏳ Прихожая включена: гости ждут в ней вашего одобрения." \
            if enable else "⏳ Прихожая выключена и удалена."
        await interaction.followup.send(state, ephemeral=True)
        await room_ops.refresh_panel(interaction.guild, db.get_room(room["voice_channel_id"]) or room)

    @discord.ui.button(label="ЧАТ", emoji="💬", style=discord.ButtonStyle.secondary,
                       row=0, custom_id="vc_chat")
    async def btn_chat(self, interaction: discord.Interaction, _: discord.ui.Button):
        prep = await self._prep(interaction)
        if prep is None:
            return
        room, _, _ = prep
        hide = not bool(room["chat_hidden"])
        await interaction.response.defer()
        await room_ops.toggle_chat(interaction.guild, room, hide)
        state = "💬 Чат комнаты открыт всем участникам." \
            if not hide else "💬 Чат снова видят только владелец и стафф."
        await interaction.followup.send(state, ephemeral=True)
        await room_ops.refresh_panel(interaction.guild, db.get_room(room["voice_channel_id"]) or room)

    # --- Ряд 2 -------------------------------------------------------------

    @discord.ui.button(label="ДОВЕРЯТЬ", emoji="✅", style=discord.ButtonStyle.success,
                       row=1, custom_id="vc_trust")
    async def btn_trust(self, interaction: discord.Interaction, _: discord.ui.Button):
        await self._pick_action(interaction, "trust")

    @discord.ui.button(label="НЕ ДОВЕРЯТЬ", emoji="❌", style=discord.ButtonStyle.danger,
                       row=1, custom_id="vc_distrust")
    async def btn_distrust(self, interaction: discord.Interaction, _: discord.ui.Button):
        await self._pick_action(interaction, "distrust")

    @discord.ui.button(label="ПРИГЛАСИТЬ", emoji="📨", style=discord.ButtonStyle.success,
                       row=1, custom_id="vc_invite")
    async def btn_invite(self, interaction: discord.Interaction, _: discord.ui.Button):
        await self._pick_action(interaction, "invite")

    @discord.ui.button(label="ВЫГНАТЬ", emoji="📤", style=discord.ButtonStyle.danger,
                       row=1, custom_id="vc_kick")
    async def btn_kick(self, interaction: discord.Interaction, _: discord.ui.Button):
        await self._pick_action(interaction, "kick")

    @discord.ui.button(label="РЕГИОН", emoji="🌍", style=discord.ButtonStyle.secondary,
                       row=1, custom_id="vc_region")
    async def btn_region(self, interaction: discord.Interaction, _: discord.ui.Button):
        prep = await self._prep(interaction)
        if prep is None:
            return
        await interaction.response.send_message(
            "Выберите регион:", view=RegionSelectView(), ephemeral=True
        )

    # --- Ряд 3 -------------------------------------------------------------

    @discord.ui.button(label="ЗАБАНИТЬ", emoji="⛔", style=discord.ButtonStyle.danger,
                       row=2, custom_id="vc_ban")
    async def btn_ban(self, interaction: discord.Interaction, _: discord.ui.Button):
        await self._pick_action(interaction, "ban")

    @discord.ui.button(label="РАЗБАНИТЬ", emoji="🔓", style=discord.ButtonStyle.success,
                       row=2, custom_id="vc_unban")
    async def btn_unban(self, interaction: discord.Interaction, _: discord.ui.Button):
        await self._pick_action(interaction, "unban")

    @discord.ui.button(label="ЗАБРАТЬ", emoji="👑", style=discord.ButtonStyle.primary,
                       row=2, custom_id="vc_claim")
    async def btn_claim(self, interaction: discord.Interaction, _: discord.ui.Button):
        prep = await self._prep(interaction)
        if prep is None:
            return
        room, _, member = prep
        if member.id == room["owner_id"]:
            await interaction.response.send_message("Вы уже владелец этой комнаты.", ephemeral=True)
            return
        voice = room_ops.room_voice(interaction.guild, room)
        occupants = [m.id for m in room_ops.voice_members(voice)]
        if room["owner_id"] in occupants:
            await interaction.response.send_message(
                "⛔ Владелец ещё в комнате — забрать нельзя.", ephemeral=True
            )
            return
        await interaction.response.defer()
        await room_ops.transfer_ownership(interaction.guild, room, member)
        await interaction.followup.send(f"👑 Комната теперь ваша, {member.display_name}.", ephemeral=True)

    @discord.ui.button(label="ПЕРЕДАТЬ", emoji="🔁", style=discord.ButtonStyle.primary,
                       row=2, custom_id="vc_transfer")
    async def btn_transfer(self, interaction: discord.Interaction, _: discord.ui.Button):
        await self._pick_action(interaction, "transfer")

    @discord.ui.button(label="УДАЛИТЬ", emoji="🗑️", style=discord.ButtonStyle.danger,
                       row=2, custom_id="vc_delete")
    async def btn_delete(self, interaction: discord.Interaction, _: discord.ui.Button):
        prep = await self._prep(interaction)
        if prep is None:
            return
        await interaction.response.send_message(
            "Удалить комнату вместе с чатом?", view=RoomDeleteConfirmView(), ephemeral=True
        )

    # --- Общий выбор участника ----------------------------------------------

    async def _pick_action(self, interaction: discord.Interaction, action: str):
        prep = await self._prep(interaction)
        if prep is None:
            return
        hints = {
            "trust": "кому разрешить говорить и стримить?",
            "distrust": "кого заглушить?",
            "invite": "кого пригласить?",
            "kick": "кого выгнать из канала?",
            "ban": "кого забанить?",
            "unban": "кого разбанить?",
            "transfer": "кому передать владение?",
        }
        await interaction.response.send_message(hints[action], view=UserPickView(action), ephemeral=True)
