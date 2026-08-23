"""Все постоянные UI-компоненты бота: панель создания тикета,
кнопки управления внутри тикета и окно оценки поддержки.

Каждый класс регистрируется в боте ровно один раз (bot.add_view) —
благодаря фиксированным custom_id кнопки продолжают работать
после перезапуска процесса. Логика кнопок вынесена в функции
уровня модуля, чтобы её переиспользовали и новые, и легаси-кнопки.
"""

import traceback

import discord

import db
from utils import checks

PANEL_BUTTON_ID = "ticket_panel_create"
PANEL_SELECT_ID = "ticket_panel_select"


class BasePersistentView(discord.ui.View):
    """Постоянный view, который сообщает об ошибках пользователю,
    а не молча игнорирует нажатие."""

    async def on_error(self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item):
        traceback.print_exception(type(error), error, error.__traceback__)
        message = "❌ Не удалось обработать нажатие. Попробуйте ещё раз — если повторится, сообщите администратору."
        try:
            if interaction.response.is_done():
                await interaction.followup.send(message, ephemeral=True)
            else:
                await interaction.response.send_message(message, ephemeral=True)
        except Exception:
            pass


# === ОБЩАЯ ЛОГИКА КНОПОК =====================================================

async def open_panel_modal(interaction: discord.Interaction) -> None:
    """Кнопка панели при одной категории: модалка для первой категории."""
    from utils import ticket_ops
    cfg = db.get_config(interaction.guild_id)
    first = cfg["ticket_categories"][0]["label"]
    await ticket_ops.open_modal_for_category(interaction, first)


async def begin_close(interaction: discord.Interaction) -> None:
    from utils import ticket_ops
    ticket = ticket_ops.resolve_ticket(interaction.channel)
    if ticket is None:
        await interaction.response.send_message(
            "Здесь нельзя это делать — канал не является тикетом.", ephemeral=True
        )
        return
    cfg = db.get_config(interaction.guild_id)
    if not await checks.ensure_access(interaction, cfg["staff_role_ids"], ticket["owner_id"]):
        return
    if ticket["status"] == "closed":
        await interaction.response.send_message("Тикет уже закрыт.", ephemeral=True)
        return
    await interaction.response.send_message(
        "Закрыть этот **тикет**?", view=ConfirmCloseView(), ephemeral=True
    )


async def confirm_close(interaction: discord.Interaction) -> None:
    from utils import ticket_ops
    ticket = ticket_ops.resolve_ticket(interaction.channel)
    if ticket is None:
        await interaction.response.edit_message(content="Это не тикет.", view=None)
        return
    cfg = db.get_config(interaction.guild_id)
    closer = await checks.ensure_access(interaction, cfg["staff_role_ids"], ticket["owner_id"])
    if closer is None:
        return
    if ticket["status"] == "closed":
        await interaction.response.edit_message(content="Тикет уже закрыт.", view=None)
        return
    await interaction.response.edit_message(content="🔒 Закрываю тикет…", view=None)
    is_owner = closer.id == ticket["owner_id"]
    await ticket_ops.perform_close(
        interaction.channel, ticket, cfg, closed_by=None if is_owner else closer,
        rating_in_channel=is_owner,
    )


async def cancel_close(interaction: discord.Interaction) -> None:
    await interaction.response.edit_message(content="Закрытие отменено.", view=None)


async def claim_ticket(interaction: discord.Interaction) -> None:
    from utils import ticket_ops
    ticket = ticket_ops.resolve_ticket(interaction.channel)
    if ticket is None:
        await interaction.response.send_message("Это не тикет.", ephemeral=True)
        return
    cfg = db.get_config(interaction.guild_id)
    staff = await checks.ensure_staff(interaction, cfg["staff_role_ids"])
    if staff is None:
        return
    await interaction.response.defer()
    await ticket_ops.claim_ticket(interaction.channel, ticket, staff)


async def save_transcript(interaction: discord.Interaction) -> None:
    from utils import ticket_ops
    ticket = ticket_ops.resolve_ticket(interaction.channel)
    if ticket is None:
        await interaction.response.send_message("Это не тикет.", ephemeral=True)
        return
    cfg = db.get_config(interaction.guild_id)
    if not await checks.ensure_staff(interaction, cfg["staff_role_ids"]):
        return
    await interaction.response.defer(ephemeral=True)
    sent = await ticket_ops.send_transcript_to_logs(
        interaction.channel, ticket, f"Запросил: {interaction.user.display_name}"
    )
    if sent:
        await interaction.followup.send("✅ Транскрипт отправлен в лог-канал.", ephemeral=True)
    else:
        file = await ticket_ops.build_transcript(
            interaction.channel, ticket, f"Запросил: {interaction.user.display_name}"
        )
        await interaction.followup.send(content="(Лог-канал не настроен)", file=file, ephemeral=True)


async def reopen_ticket(interaction: discord.Interaction) -> None:
    from utils import ticket_ops
    ticket = ticket_ops.resolve_ticket(interaction.channel)
    if ticket is None:
        await interaction.response.send_message("Это не тикет.", ephemeral=True)
        return
    if ticket["status"] != "closed":
        await interaction.response.send_message("Тикет ещё открыт.", ephemeral=True)
        return
    cfg = db.get_config(interaction.guild_id)
    if not await checks.ensure_staff(interaction, cfg["staff_role_ids"]):
        return
    await interaction.response.defer()
    notice = await ticket_ops.perform_reopen(interaction.channel, ticket)
    await interaction.followup.send(notice)
    try:
        await interaction.message.delete()
    except discord.HTTPException:
        pass


async def delete_ticket(interaction: discord.Interaction) -> None:
    from utils import ticket_ops
    ticket = ticket_ops.resolve_ticket(interaction.channel)
    if ticket is None:
        await interaction.response.send_message("Это не тикет.", ephemeral=True)
        return
    if ticket["status"] != "closed":
        await interaction.response.send_message(
            "⛔ Удалять можно только закрытый тикет. Сначала закройте его.", ephemeral=True
        )
        return
    cfg = db.get_config(interaction.guild_id)
    if not await checks.ensure_staff(interaction, cfg["staff_role_ids"]):
        return
    await ticket_ops.perform_delete(interaction)


# === ПАНЕЛЬ СОЗДАНИЯ ТИКЕТА ==================================================

class TicketCategorySelect(discord.ui.Select):
    def __init__(self, categories: list[dict]):
        options = [
            discord.SelectOption(
                label=c["label"][:100],
                value=c["label"],
                emoji=c.get("emoji") or None,
                description=(c.get("description") or "")[:100] or None,
            )
            for c in categories
        ]
        super().__init__(
            placeholder="Выберите причину обращения…",
            options=options,
            custom_id=PANEL_SELECT_ID,
        )

    async def callback(self, interaction: discord.Interaction):
        from utils import ticket_ops
        await ticket_ops.open_modal_for_category(interaction, self.values[0])


class TicketPanelView(BasePersistentView):
    """Сообщение-панель в канале поддержки. Одна категория — кнопка, несколько — меню."""

    def __init__(self, categories: list[dict] | None = None):
        super().__init__(timeout=None)
        cats = categories if categories else db.DEFAULT_CATEGORIES
        if len(cats) > 1:
            self.add_item(TicketCategorySelect(cats))
        else:
            button = discord.ui.Button(
                label="Создать обращение",
                emoji=cats[0].get("emoji") or "🎫",
                style=discord.ButtonStyle.primary,
                custom_id=PANEL_BUTTON_ID,
            )
            button.callback = lambda interaction: open_panel_modal(interaction)
            self.add_item(button)


class PanelDispatchView(BasePersistentView):
    """Регистрационный view панели: держит обработчики и кнопки, и меню,
    чтобы работали уже опубликованные панели любого вида после рестарта.
    Сам этот view никуда не отправляется (как LegacyCompatView)."""

    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(TicketCategorySelect(db.DEFAULT_CATEGORIES))
        button = discord.ui.Button(
            label="Создать обращение",
            emoji="🎫",
            style=discord.ButtonStyle.primary,
            custom_id=PANEL_BUTTON_ID,
        )
        button.callback = lambda interaction: open_panel_modal(interaction)
        self.add_item(button)


class TicketReasonModal(discord.ui.Modal):
    def __init__(self, category_label: str):
        super().__init__(title=f"Обращение: {category_label}"[:45])
        self.category_label = category_label
        self.reason = discord.ui.TextInput(
            label="Опишите вашу проблему",
            placeholder="Что случилось? Чем помочь?",
            style=discord.TextStyle.paragraph,
            required=True,
            max_length=1000,
        )
        self.add_item(self.reason)

    async def on_submit(self, interaction: discord.Interaction):
        from utils import ticket_ops
        await ticket_ops.spawn_ticket(interaction, self.category_label, str(self.reason))


# === КНОПКИ ВНУТРИ ОТКРЫТОГО ТИКЕТА ==========================================

class OpenControlsView(BasePersistentView):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Закрыть", emoji="🔒", style=discord.ButtonStyle.secondary, custom_id="ticket_btn_close")
    async def close(self, interaction: discord.Interaction, _: discord.ui.Button):
        await begin_close(interaction)

    @discord.ui.button(label="Взять в работу", emoji="🙋", style=discord.ButtonStyle.success, custom_id="ticket_btn_claim")
    async def claim(self, interaction: discord.Interaction, _: discord.ui.Button):
        await claim_ticket(interaction)

    @discord.ui.button(label="Транскрипт", emoji="📄", style=discord.ButtonStyle.secondary, custom_id="ticket_btn_transcript")
    async def transcript(self, interaction: discord.Interaction, _: discord.ui.Button):
        await save_transcript(interaction)


# === ПОДТВЕРЖДЕНИЕ ЗАКРЫТИЯ ==================================================

class ConfirmCloseView(BasePersistentView):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Да, закрыть", emoji="✅", style=discord.ButtonStyle.danger, custom_id="ticket_btn_confirm_close")
    async def confirm(self, interaction: discord.Interaction, _: discord.ui.Button):
        await confirm_close(interaction)

    @discord.ui.button(label="Отмена", emoji="↩️", style=discord.ButtonStyle.secondary, custom_id="ticket_btn_cancel_close")
    async def cancel(self, interaction: discord.Interaction, _: discord.ui.Button):
        await cancel_close(interaction)


# === КНОПКИ В ЗАКРЫТОМ ТИКЕТЕ ================================================

class ClosedControlsView(BasePersistentView):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Переоткрыть", emoji="🔓", style=discord.ButtonStyle.primary, custom_id="ticket_btn_reopen")
    async def reopen(self, interaction: discord.Interaction, _: discord.ui.Button):
        await reopen_ticket(interaction)

    @discord.ui.button(label="Транскрипт", emoji="📄", style=discord.ButtonStyle.secondary, custom_id="ticket_btn_transcript_closed")
    async def transcript(self, interaction: discord.Interaction, _: discord.ui.Button):
        await save_transcript(interaction)

    @discord.ui.button(label="Удалить тикет", emoji="🗑️", style=discord.ButtonStyle.danger, custom_id="ticket_btn_delete")
    async def delete(self, interaction: discord.Interaction, _: discord.ui.Button):
        await delete_ticket(interaction)


# === КНОПКИ ОТ СТАРОЙ ВЕРСИИ БОТА ============================================
# Сообщения, опубликованные до переписывания, хранят старые custom_id.
# Регистрируем те же идентификаторы, чтобы старая панель и старые тикеты
# продолжали работать под новым кодом. Сам этот view никуда не отправляется.

class LegacyCompatView(BasePersistentView):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Создать запрос (стар.)", emoji="👍", custom_id="create_ticket_panel_btn")
    async def legacy_panel(self, interaction: discord.Interaction, _: discord.ui.Button):
        await open_panel_modal(interaction)

    @discord.ui.button(label="Закрыть (стар.)", emoji="🔒", custom_id="close_ticket_init_btn")
    async def legacy_close_init(self, interaction: discord.Interaction, _: discord.ui.Button):
        await begin_close(interaction)

    @discord.ui.button(label="Да (стар.)", emoji="✅", custom_id="confirm_close_btn")
    async def legacy_confirm(self, interaction: discord.Interaction, _: discord.ui.Button):
        await confirm_close(interaction)

    @discord.ui.button(label="Отменить (стар.)", emoji="↩️", custom_id="cancel_close_btn")
    async def legacy_cancel(self, interaction: discord.Interaction, _: discord.ui.Button):
        await cancel_close(interaction)

    @discord.ui.button(label="Сохранить диалог (стар.)", emoji="📄", custom_id="save_transcript_btn")
    async def legacy_save(self, interaction: discord.Interaction, _: discord.ui.Button):
        await save_transcript(interaction)

    @discord.ui.button(label="Переоткрыть (стар.)", emoji="🔓", custom_id="reopen_ticket_btn")
    async def legacy_reopen(self, interaction: discord.Interaction, _: discord.ui.Button):
        await reopen_ticket(interaction)

    @discord.ui.button(label="Удалить тикет (стар.)", emoji="⛔", custom_id="delete_ticket_btn")
    async def legacy_delete(self, interaction: discord.Interaction, _: discord.ui.Button):
        await delete_ticket(interaction)


# === ОЦЕНКА ПОДДЕРЖКИ ========================================================

class RatingStarsView(BasePersistentView):
    """Пять звёзд. Работает и в канале тикета, и в ЛС пользователя."""

    def __init__(self):
        super().__init__(timeout=None)
        for stars in range(1, 6):
            button = discord.ui.Button(
                label="⭐" * stars, custom_id=f"ticket_rate_{stars}", row=0
            )
            button.callback = self._make_callback(stars)
            self.add_item(button)

    def _make_callback(self, stars: int):
        async def callback(interaction: discord.Interaction):
            user_id = interaction.user.id
            if interaction.guild is not None:
                from utils import ticket_ops
                ticket = ticket_ops.resolve_ticket(interaction.channel)
                if ticket is None or ticket["owner_id"] != user_id:
                    await interaction.response.send_message(
                        "Оценить поддержку может только владелец тикета.", ephemeral=True
                    )
                    return
                channel_id, guild_id = interaction.channel.id, interaction.guild.id
            else:
                ticket = db.latest_closed_unrated(user_id)
                if ticket is None:
                    await interaction.response.send_message(
                        "Не нашёл ваш закрытый тикет без оценки.", ephemeral=True
                    )
                    return
                channel_id, guild_id = ticket["channel_id"], ticket["guild_id"]

            if db.add_rating(channel_id, guild_id, user_id, stars):
                word = {1: "жаль", 5: "круто!"}.get(stars, "спасибо!")
                await interaction.response.send_message(f"Оценка принята — {word} 💛", ephemeral=True)
            else:
                await interaction.response.send_message("Вы уже оценили этот тикет.", ephemeral=True)

        return callback


def build_rating_embed() -> discord.Embed:
    return discord.Embed(
        title="⭐ Оцените работу поддержки",
        description="Насколько помогла наша команда? Ваша оценка анонимна для модераторов.",
        color=discord.Color.blurple(),
    )


ALL_PERSISTENT_VIEWS = (
    PanelDispatchView,
    OpenControlsView,
    ConfirmCloseView,
    ClosedControlsView,
    LegacyCompatView,
    RatingStarsView,
)
