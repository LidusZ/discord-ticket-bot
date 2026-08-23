"""Room Creator: временные голосовые комнаты по кнопке-триггеру.

Зашёл в триггер-канал → бот создаёт пару «войс + текстовый канал панели»
и перемещает пользователя. Комната удаляется, когда владелец ушёл и
передать её некому, или когда она пустеет дольше настроенного времени.
"""

import time
import traceback

import discord
from discord.ext import commands, tasks

import db
from utils import room_ops
from utils.room_views import RoomControlView

# Повторные запросы из прихожей от одного человека глушим на минуту,
# чтобы прыжки в/из прихожей не спамили панель.
_REQUEST_COOLDOWN_SECONDS = 60


class RoomsCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._last_requests: dict[tuple[int, int], float] = {}

    async def cog_load(self):
        # Ровно один регистрационный экземпляр на все комнаты (правило №1).
        self.bot.add_view(RoomControlView())
        self._cleanup_loop.start()

    def cog_unload(self):
        self._cleanup_loop.cancel()

    @tasks.loop(seconds=60)
    async def _cleanup_loop(self):
        try:
            await self._sweep()
        except Exception:
            traceback.print_exc()

    @_cleanup_loop.before_loop
    async def _wait_ready(self):
        await self.bot.wait_until_ready()

    async def _sweep(self):
        """Удаляет комнаты, пустующие дольше лимита, и подчищает записи
        о комнатах, чьи каналы снесли вручную."""
        now = int(time.time())
        for room in db.all_rooms():
            guild = self.bot.get_guild(room["guild_id"])
            if guild is None:
                continue
            voice = room_ops.room_voice(guild, room)
            if voice is None:
                await room_ops.delete_room_everything(guild, room)
                continue
            occupied = bool(room_ops.voice_members(voice))
            if occupied:
                if room["empty_since"]:
                    db.update_room(voice.id, empty_since=None)
                continue
            if not room["empty_since"]:
                # Событие выхода могло потеряться (рестарт/даунтайм Render):
                # запускаем таймер пустоты сейчас, иначе комната зависла бы навсегда.
                db.update_room(voice.id, empty_since=now)
                continue
            minutes = db.get_config(guild.id).get("room_empty_minutes") or 10
            if now - room["empty_since"] >= minutes * 60:
                await room_ops.delete_room_everything(guild, room)

    @commands.Cog.listener()
    async def on_voice_state_update(
        self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState
    ):
        if member.bot:
            return
        # Мут, стрим, видео и прочие флаги не меняют канал — такие события игнорируем,
        # иначе каждый заход в мут считался бы выходом из комнаты.
        if before.channel is not None and after.channel is not None \
                and before.channel.id == after.channel.id:
            return
        guild = member.guild
        cfg = db.get_config(guild.id)
        trigger_id = cfg.get("voice_trigger_id")

        # 1) Зашёл в триггер — создать комнату.
        if trigger_id and after.channel and after.channel.id == trigger_id:
            await room_ops.spawn_room_for_member(member, after.channel)
            return

        # 2) Зашёл в прихожую — отправить запрос владельцу.
        if after.channel:
            lobby_room = db.find_room_by_lobby(after.channel.id)
            if lobby_room is not None:
                self._post_request_throttled(guild, lobby_room, member)

        # 3) Вошёл в комнату — снять метку пустоты.
        if after.channel:
            joined = db.get_room(after.channel.id)
            if joined is not None and joined["empty_since"]:
                db.update_room(joined["voice_channel_id"], empty_since=None)

        # 4) Вышел из комнаты — передача владельца или таймер пустоты.
        if before.channel is None:
            return
        left = db.get_room(before.channel.id)
        if left is None:
            return
        voice = room_ops.room_voice(guild, left)
        remaining = room_ops.voice_members(voice)
        was_owner = member.id == left["owner_id"]

        if not remaining:
            if was_owner:
                # Владелец вышел последним и передавать некому — комната больше не нужна.
                await room_ops.delete_room_everything(guild, left)
            elif not left["empty_since"]:
                db.update_room(left["voice_channel_id"], empty_since=int(time.time()))
        elif was_owner:
            await room_ops.handle_owner_left(guild, left)

    def _post_request_throttled(
        self, guild: discord.Guild, room: dict, requester: discord.Member
    ) -> None:
        key = (room["voice_channel_id"], requester.id)
        last = self._last_requests.get(key)
        now = time.monotonic()
        if last is not None and now - last < _REQUEST_COOLDOWN_SECONDS:
            return
        self._last_requests[key] = now
        # Чистим остывшие записи, чтобы словарь не рос бесконечно.
        stale = [k for k, ts in self._last_requests.items() if now - ts > 3600]
        for k in stale:
            self._last_requests.pop(k, None)
        try:
            room_ops.post_join_request(guild, room, requester)
        except Exception:
            traceback.print_exc()


async def setup(bot):
    await bot.add_cog(RoomsCog(bot))
