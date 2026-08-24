"""Настройка бота командами /config — без правки кода и перезапуска."""

from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

import db


class ServerConfigCog(commands.Cog):
    config_group = app_commands.Group(
        name="config",
        description="Настройка тикет-бота",
        default_permissions=discord.Permissions(administrator=True),
        guild_only=True,
    )
    set_group = app_commands.Group(
        name="set", parent=config_group, description="Основные каналы"
    )
    staff_group = app_commands.Group(
        name="staff", parent=config_group, description="Роли персонала поддержки"
    )
    categories_group = app_commands.Group(
        name="categories", parent=config_group, description="Категории на панели тикетов"
    )
    voice_group = app_commands.Group(
        name="voice", parent=config_group, description="Голосовые комнаты (Room Creator)"
    )
    members_group = app_commands.Group(
        name="members", parent=config_group, description="Счётчики участников сервера"
    )
    invites_group = app_commands.Group(
        name="invites", parent=config_group, description="Трекер приглашений"
    )

    # --- Основные каналы ---

    @set_group.command(name="category", description="Категория, в которой создаются тикеты")
    async def set_category(self, interaction: discord.Interaction, channel: discord.CategoryChannel):
        db.set_category(interaction.guild_id, channel.id)
        await interaction.response.send_message(
            f"✅ Тикеты будут создаваться в категории {channel.mention}.", ephemeral=True
        )

    @set_group.command(name="logs", description="Канал для транскриптов и логов закрытий")
    async def set_logs(self, interaction: discord.Interaction, channel: discord.TextChannel):
        db.set_log_channel(interaction.guild_id, channel.id)
        await interaction.response.send_message(
            f"✅ Логи будут отправляться в {channel.mention}.", ephemeral=True
        )

    # --- Персонал ---

    @staff_group.command(name="add", description="Добавить роль персонала")
    async def staff_add(self, interaction: discord.Interaction, role: discord.Role):
        if role.is_default() or role == interaction.guild.default_role:
            await interaction.response.send_message("⛔ @everyone добавить нельзя.", ephemeral=True)
            return
        added = db.staff_add(interaction.guild_id, role.id)
        if added:
            await interaction.response.send_message(f"✅ Роль {role.mention} — теперь персонал.", ephemeral=True)
        else:
            await interaction.response.send_message("Эта роль уже в списке.", ephemeral=True)

    @staff_group.command(name="remove", description="Убрать роль из персонала")
    async def staff_remove(self, interaction: discord.Interaction, role: discord.Role):
        removed = db.staff_remove(interaction.guild_id, role.id)
        if removed:
            await interaction.response.send_message(f"✅ Роль {role.mention} убрана из персонала.", ephemeral=True)
        else:
            await interaction.response.send_message("Этой роли нет в списке.", ephemeral=True)

    # --- Категории панели ---

    @categories_group.command(name="add", description="Добавить категорию на панель (до 25)")
    async def category_add(
        self,
        interaction: discord.Interaction,
        label: app_commands.Range[str, 1, 80],
        emoji: Optional[str] = "",
        description: Optional[app_commands.Range[str, 0, 100]] = "",
    ):
        result = db.category_add(interaction.guild_id, label.strip(), emoji.strip()[:64], description.strip())
        if result == "ok":
            await interaction.response.send_message(
                f"✅ Категория «{label}» добавлена. Опубликуйте панель заново командой `/setup`.",
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(f"⛔ {result}", ephemeral=True)

    @categories_group.command(name="remove", description="Удалить категорию с панели")
    async def category_remove(self, interaction: discord.Interaction, label: str):
        result = db.category_remove(interaction.guild_id, label.strip())
        if result == "ok":
            await interaction.response.send_message(
                f"✅ Категория «{label}» удалена. Обновите панель командой `/setup`.", ephemeral=True
            )
        else:
            await interaction.response.send_message(f"⛔ {result}", ephemeral=True)

    @category_remove.autocomplete("label")
    async def category_remove_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        cfg = db.get_config(interaction.guild_id)
        current = current.lower()
        return [
            app_commands.Choice(name=c["label"], value=c["label"])
            for c in cfg["ticket_categories"]
            if current in c["label"].lower()
        ][:25]

    # --- Голосовые комнаты (Room Creator) ---

    @voice_group.command(name="trigger", description="Триггер-канал «Создать комнату»")
    async def voice_trigger(self, interaction: discord.Interaction, channel: discord.VoiceChannel):
        db.set_voice_trigger(interaction.guild_id, channel.id)
        await interaction.response.send_message(
            f"✅ {channel.mention} теперь триггер: зашёл в него — получил личную комнату.",
            ephemeral=True,
        )

    @voice_group.command(name="category", description="Категория, в которой создаются комнаты")
    async def voice_category(self, interaction: discord.Interaction, channel: discord.CategoryChannel):
        db.set_voice_category(interaction.guild_id, channel.id)
        await interaction.response.send_message(
            f"✅ Комнаты будут создаваться в категории {channel.mention}.", ephemeral=True
        )

    @voice_group.command(name="defaults", description="Шаблон имени и лимит новых комнат")
    async def voice_defaults(
        self,
        interaction: discord.Interaction,
        name: str = "{user}",
        user_limit: app_commands.Range[int, 0, 99] = 0,
    ):
        """Плейсхолдеры: {user} — имя создателя, {server} — название сервера."""
        db.set_voice_defaults(interaction.guild_id, name.strip(), user_limit)
        limit_text = "без лимита" if user_limit == 0 else f"лимит {user_limit}"
        await interaction.response.send_message(
            f"✅ Новые комнаты: имя «{name.strip()}», {limit_text}.", ephemeral=True
        )

    @voice_group.command(name="empty_delete", description="Через сколько минут удалять пустую комнату")
    async def voice_empty(self, interaction: discord.Interaction, minutes: app_commands.Range[int, 1, 120]):
        db.set_room_empty_minutes(interaction.guild_id, minutes)
        await interaction.response.send_message(
            f"✅ Пустая комната будет удалена через **{minutes} мин**.", ephemeral=True
        )

    # --- Счётчики участников ---

    @members_group.command(name="enable", description="Создать каналы All Members / Members / Bots")
    async def members_enable(self, interaction: discord.Interaction, category: Optional[discord.CategoryChannel] = None):
        """Без аргумента счётчики попадут в категорию текущего канала (или прошлую настройку)."""
        await interaction.response.defer(ephemeral=True, thinking=True)
        from utils import member_stats_ops

        cfg = db.get_config(interaction.guild_id)
        if category is not None:
            category_id = category.id
        elif cfg.get("ms_category_id"):
            category_id = cfg["ms_category_id"]
        else:
            category_id = getattr(interaction.channel, "category_id", None)

        await member_stats_ops.delete_counters(interaction.guild, cfg)
        try:
            ids = await member_stats_ops.create_counters(interaction.guild, category_id)
        except discord.Forbidden:
            await interaction.followup.send(
                "❌ У бота нет права создавать голосовые каналы. Выдайте Manage Channels.",
                ephemeral=True,
            )
            return
        db.set_memberstats_category(interaction.guild_id, category_id)
        db.set_memberstats_channels(interaction.guild_id, ids["ms_ch_all"], ids["ms_ch_humans"], ids["ms_ch_bots"])
        db.set_memberstats_enabled(interaction.guild_id, True)

        where = f"в категории <#{category_id}>" if category_id else "без категории"
        await interaction.followup.send(
            f"✅ Счётчики созданы {where}. Обновляются сами: по входам/выходам и "
            f"каждые 5 минут (лимит Discord на переименование каналов).",
            ephemeral=True,
        )

    @members_group.command(name="disable", description="Выключить и удалить каналы-счётчики")
    async def members_disable(self, interaction: discord.Interaction):
        from utils import member_stats_ops

        cfg = db.get_config(interaction.guild_id)
        if not cfg.get("ms_enabled") and not cfg.get("ms_ch_all"):
            await interaction.response.send_message(
                "Счётчики и так выключены.", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True)
        await member_stats_ops.delete_counters(interaction.guild, cfg)
        db.set_memberstats_channels(interaction.guild_id, None, None, None)
        db.set_memberstats_enabled(interaction.guild_id, False)
        await interaction.followup.send("✅ Счётчики удалены. Настройки подписей сохранены.", ephemeral=True)

    @members_group.command(name="labels", description="Свои подписи счётчиков, {count} — число")
    async def members_labels(
        self,
        interaction: discord.Interaction,
        all_label: app_commands.Range[str, 1, 90],
        humans_label: app_commands.Range[str, 1, 90],
        bots_label: app_commands.Range[str, 1, 90],
    ):
        """Пример: `Участники всего: {count}` / `Люди: {count}` / `Боты: {count}`."""
        labels = {"all": all_label.strip(), "humans": humans_label.strip(), "bots": bots_label.strip()}
        db.set_memberstat_labels(interaction.guild_id, labels)
        await interaction.response.defer(ephemeral=True)
        cfg = db.get_config(interaction.guild_id)
        if cfg.get("ms_enabled"):
            from utils import member_stats_ops
            await member_stats_ops.refresh_guild(interaction.guild, cfg)
        await interaction.followup.send("✅ Подписи сохранены и применены.", ephemeral=True)

    @members_group.command(name="show", description="Текущее состояние счётчиков участников")
    async def members_show(self, interaction: discord.Interaction):
        from utils import member_stats_ops

        cfg = db.get_config(interaction.guild_id)
        labels = db.get_memberstat_labels(interaction.guild_id)
        counts = member_stats_ops.compute_counts(interaction.guild)
        enabled = bool(cfg.get("ms_enabled"))
        lines = [
            f"{labels[key]} — сейчас **{counts[key]}**"
            for key in ("all", "humans", "bots")
        ]
        embed = discord.Embed(title="👥 Счётчики участников", color=discord.Color.blurple())
        embed.add_field(
            name="Статус",
            value=("🟢 включены\n" + "\n".join(lines)) if enabled else "🔴 выключены (`/config members enable`)",
            inline=False,
        )
        embed.add_field(
            name="Категория",
            value=(f"<#{cfg['ms_category_id']}>" if cfg.get("ms_category_id") else "—"),
        )
        embed.add_field(
            name="Обновление",
            value="по входу/выходу участников + каждые 5 минут",
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # --- Трекер приглашений ---

    @invites_group.command(name="log", description="Канал логов входов/выходов по приглашениям")
    async def invites_log(
        self,
        interaction: discord.Interaction,
        channel: Optional[discord.TextChannel] = None,
    ):
        """Без аргумента логи приглашений пойдут в общий канал логов тикетов."""
        db.set_invite_log_channel(interaction.guild_id, channel.id if channel else None)
        if channel:
            text = f"✅ Логи приглашений будут отправляться в {channel.mention}."
        else:
            text = "✅ Отдельный канал сброшен — логи приглашений пойдут в общий канал логов тикетов."
        await interaction.response.send_message(text, ephemeral=True)

    @invites_group.command(name="weekly", description="Топ инвайтеров за 7 дней раз в неделю")
    async def invites_weekly(
        self,
        interaction: discord.Interaction,
        channel: Optional[discord.TextChannel] = None,
    ):
        """Укажите канал — включить автопубликацию; вызвать без канала — выключить."""
        if channel is None:
            db.set_invite_weekly_channel(interaction.guild_id, None)
            await interaction.response.send_message("🔴 Еженедельный топ инвайтеров выключен.", ephemeral=True)
            return
        import time

        db.set_invite_weekly_channel(interaction.guild_id, channel.id)
        # Неделя отсчитывается с момента включения, чтобы дайджесты были ровно раз в 7 дней.
        db.set_invite_weekly_last(interaction.guild_id, int(time.time()))
        await interaction.response.send_message(
            f"✅ Раз в неделю в {channel.mention} будет публиковаться топ инвайтеров "
            "за последние 7 дней. Первый — через неделю.",
            ephemeral=True,
        )

    @invites_group.command(name="show", description="Состояние трекера приглашений")
    async def invites_show(self, interaction: discord.Interaction):
        cfg = db.get_config(interaction.guild_id)
        guild = interaction.guild
        log_ch = guild.get_channel(cfg["inv_log_channel_id"]) if cfg.get("inv_log_channel_id") else None
        weekly_ch = guild.get_channel(cfg["inv_weekly_channel_id"]) if cfg.get("inv_weekly_channel_id") else None
        last = cfg.get("inv_weekly_last_ts")

        embed = discord.Embed(title="📨 Трекер приглашений", color=discord.Color.gold())
        embed.add_field(
            name="Канал логов",
            value=log_ch.mention if log_ch else "не задан — используется общий канал логов тикетов",
        )
        if weekly_ch:
            when = (
                f"неделя отсчитывается от <t:{last}:d> <t:{last}:t>" if last else "первый дайджест в течение часа"
            )
            weekly_text = f"🟢 включён → {weekly_ch.mention}\n{when}"
        else:
            weekly_text = "🔴 выключен (`/config invites weekly #канал`)"
        embed.add_field(name="Еженедельный топ за 7 дней", value=weekly_text)
        embed.add_field(
            name="Ручной показ",
            value="`/invitetop` — только для администраторов",
            inline=False,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # --- Лимиты и автозакрытие ---

    @config_group.command(name="limits", description="Лимиты создания тикетов на пользователя")
    async def limits(
        self,
        interaction: discord.Interaction,
        max_open: app_commands.Range[int, 1, 5],
        cooldown_seconds: app_commands.Range[int, 0, 3600] = 60,
    ):
        db.set_limits(interaction.guild_id, max_open, cooldown_seconds)
        await interaction.response.send_message(
            f"✅ Максимум открытых тикетов: **{max_open}**, кулдаун: **{cooldown_seconds} сек**.",
            ephemeral=True,
        )

    @config_group.command(name="autoclose", description="Автозакрытие неактивных тикетов")
    async def autoclose(
        self,
        interaction: discord.Interaction,
        warn_hours: app_commands.Range[int, 0, 168],
        close_hours: app_commands.Range[int, 0, 336] = 48,
    ):
        """warn_hours = 0 полностью выключает автозакрытие."""
        if warn_hours > 0 and close_hours <= warn_hours:
            await interaction.response.send_message(
                "⛔ Время закрытия должно быть больше времени предупреждения "
                "(или 0, чтобы только предупреждать).",
                ephemeral=True,
            )
            return
        effective_close = close_hours if warn_hours > 0 else 0
        db.set_autoclose(interaction.guild_id, warn_hours, effective_close)
        if warn_hours == 0:
            text = "✅ Автозакрытие выключено."
        elif effective_close == 0:
            text = f"✅ Предупреждение через **{warn_hours} ч** неактивности; автозакрытие отключено."
        else:
            text = (
                f"✅ Предупреждение через **{warn_hours} ч** неактивности, "
                f"закрытие через **{close_hours} ч**."
            )
        await interaction.response.send_message(text, ephemeral=True)

    # --- Просмотр ---

    @config_group.command(name="show", description="Показать текущие настройки")
    async def show(self, interaction: discord.Interaction):
        cfg = db.get_config(interaction.guild_id)
        guild = interaction.guild

        category = guild.get_channel(cfg["category_id"]) if cfg["category_id"] else None
        logs = guild.get_channel(cfg["log_channel_id"]) if cfg["log_channel_id"] else None
        roles = ", ".join(
            f"<@&{rid}>" for rid in cfg["staff_role_ids"] if guild.get_role(rid)
        ) or "—"
        cats = "\n".join(
            f"{c.get('emoji') or '🎫'} **{c['label']}**"
            + (f" — {c['description']}" if c.get("description") else "")
            for c in cfg["ticket_categories"]
        )
        if cfg["autoclose_warn_hours"]:
            autoclose = f"предупреждение через {cfg['autoclose_warn_hours']} ч"
            autoclose += (
                f", закрытие через {cfg['autoclose_hours']} ч"
                if cfg["autoclose_hours"]
                else ", без автозакрытия"
            )
        else:
            autoclose = "выключено"

        embed = discord.Embed(title="⚙️ Настройки тикет-бота", color=discord.Color.blurple())
        embed.add_field(name="Категория тикетов", value=category.mention if category else "❌ не задана")
        embed.add_field(name="Канал логов", value=logs.mention if logs else "❌ не задан")
        embed.add_field(name="Персонал", value=roles, inline=False)
        embed.add_field(name=f"Категории панели ({len(cfg['ticket_categories'])})", value=cats, inline=False)
        embed.add_field(
            name="Лимиты",
            value=f"макс. открытых: {cfg['max_open_per_user']}, кулдаун: {cfg['cooldown_seconds']} сек",
        )
        embed.add_field(name="Автозакрытие", value=autoclose)
        embed.add_field(name="Всего тикетов создано", value=str(cfg["counter"]))

        trigger = guild.get_channel(cfg.get("voice_trigger_id") or 0) if cfg.get("voice_trigger_id") else None
        vcategory = guild.get_channel(cfg.get("voice_category_id") or 0) if cfg.get("voice_category_id") else None
        template = cfg.get("voice_name_template") or "{user}"
        embed.add_field(
            name="Room Creator",
            value=(
                f"триггер: {trigger.mention if trigger else '❌ не задан'}\n"
                f"категория: {vcategory.mention if vcategory else '❌ не задана'}\n"
                f"имя: «{template}», лимит: {cfg.get('voice_user_limit') or 'нет'}\n"
                f"удаление пустой: через {cfg.get('room_empty_minutes') or 10} мин\n"
                f"комнат сейчас: {len(db.guild_rooms(guild.id))}"
            ),
            inline=False,
        )

        if cfg.get("ms_enabled"):
            ms_text = (
                f"🟢 включены, категория: "
                + (f"<#{cfg['ms_category_id']}>" if cfg.get("ms_category_id") else "без категории")
            )
        else:
            ms_text = "🔴 выключены (`/config members enable`)"
        embed.add_field(name="Счётчики участников", value=ms_text)

        inv_log = guild.get_channel(cfg["inv_log_channel_id"]) if cfg.get("inv_log_channel_id") else None
        inv_weekly = guild.get_channel(cfg["inv_weekly_channel_id"]) if cfg.get("inv_weekly_channel_id") else None
        invites_text = (
            f"логи: {inv_log.mention if inv_log else 'общий канал логов'}\n"
            f"еженедельный топ: {inv_weekly.mention if inv_weekly else 'выключен'}"
        )
        embed.add_field(name="Трекер приглашений", value=invites_text)
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot):
    await bot.add_cog(ServerConfigCog(bot))
