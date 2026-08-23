import io
import re
import asyncio
import traceback
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

# === НАСТРОЙКИ МОДУЛЯ ===
TICKET_CATEGORY_ID = 1540863516163186829  # ID категории для тикетов
LOG_CHANNEL_ID = 1540863605455462460     # ID канала для логов (транскриптов)
STAFF_ROLE_IDS = [                      # ID ролей админов/модеров
    1537536849298460784,
    1537547996890271775,
    1537536849290199077,
    1537536849290199075,
    1537536849290199076,
]

OWNER_TOPIC_PREFIX = "TICKET_OWNER:"  # метка владельца тикета в теме канала
GUILD_ID = 0  # ID твоего сервера: если указан, слэш-команды появляются мгновенно; 0 = глобально (может занять до часа)


# === ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ===
def is_staff(member: discord.Member) -> bool:
    if member.guild_permissions.administrator:
        return True
    return any(role.id in STAFF_ROLE_IDS for role in member.roles)


def get_ticket_owner_id(channel) -> Optional[int]:
    if not isinstance(channel, discord.TextChannel):
        return None
    if channel.topic:
        match = re.match(rf"{OWNER_TOPIC_PREFIX}(\d+)", channel.topic)
        if match:
            return int(match.group(1))
    # Фолбэк для старых тикетов, созданных до появления метки в теме канала
    for target, overwrite in channel.overwrites.items():
        if (
            isinstance(target, discord.Member)
            and not target.bot
            and overwrite.read_messages
            and not is_staff(target)
        ):
            return target.id
    return None


def find_open_ticket(guild: discord.Guild, user_id: int) -> Optional[discord.TextChannel]:
    category = guild.get_channel(TICKET_CATEGORY_ID)
    if not isinstance(category, discord.CategoryChannel):
        return None
    for channel in category.text_channels:
        if channel.topic and channel.topic[len(OWNER_TOPIC_PREFIX):] == str(user_id):
            return channel
    return None


async def ensure_staff(interaction: discord.Interaction) -> bool:
    """True, если нажавший — стафф; иначе отправляет отказ и возвращает False."""
    member = interaction.user
    if interaction.guild is None or not isinstance(member, discord.Member):
        await interaction.response.send_message("Доступно только на сервере.", ephemeral=True)
        return False
    if is_staff(member):
        return True
    await interaction.response.send_message("⛔ Кнопка доступна только стаффу.", ephemeral=True)
    return False


async def ensure_access(interaction: discord.Interaction) -> bool:
    """True, если нажавший — стафф или владелец тикета; иначе отказ и False."""
    member = interaction.user
    if interaction.guild is None or not isinstance(member, discord.Member):
        await interaction.response.send_message("Доступно только на сервере.", ephemeral=True)
        return False
    owner_id = get_ticket_owner_id(interaction.channel)
    if is_staff(member) or member.id == owner_id:
        return True
    await interaction.response.send_message(
        "⛔ Только стафф или владелец тикета могут это делать.", ephemeral=True
    )
    return False


async def generate_transcript(channel: discord.TextChannel):
    messages = []
    async for msg in channel.history(limit=500, oldest_first=True):
        time_str = msg.created_at.strftime("%Y-%m-%d %H:%M:%S")
        if msg.content:
            messages.append(f"[{time_str}] {msg.author}: {msg.content}")
        for att in msg.attachments:
            messages.append(f"[{time_str}] {msg.author}: [Вложение: {att.url}]")
        for embed in msg.embeds:
            parts = [p for p in (embed.title, embed.description) if p]
            parts += [f"{f.name}: {f.value}" for f in embed.fields]
            if parts:
                messages.append(f"[{time_str}] {msg.author}: [Embed] " + " | ".join(parts))

    content = "\n".join(messages) or "Диалог пуст."
    file_bytes = io.BytesIO(content.encode("utf-8"))
    return discord.File(file_bytes, filename=f"transcript-{channel.name}.txt")


class TicketControlsView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Сохранить диалог (ВСЕГДА)", style=discord.ButtonStyle.secondary, emoji="📄", custom_id="save_transcript_btn")
    async def save_transcript(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await ensure_staff(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        transcript_file = await generate_transcript(interaction.channel)
        log_channel = interaction.guild.get_channel(LOG_CHANNEL_ID)

        if log_channel:
            await log_channel.send(content=f"📑 Транскрипт тикета `{interaction.channel.name}` (Сохранил: {interaction.user.mention})", file=transcript_file)
            await interaction.followup.send("✅ Диалог сохранён и отправлен в лог-канал!", ephemeral=True)
        else:
            await interaction.followup.send(content="✅ Диалог сохранён! (лог-канал не найден)", file=transcript_file, ephemeral=True)

    @discord.ui.button(label="Переоткрыть", style=discord.ButtonStyle.secondary, emoji="🔓", custom_id="reopen_ticket_btn")
    async def reopen_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await ensure_staff(interaction):
            return
        await interaction.response.defer()
        owner_id = get_ticket_owner_id(interaction.channel)
        owner = interaction.guild.get_member(owner_id) if owner_id else None
        if owner:
            await interaction.channel.set_permissions(owner, read_messages=True, send_messages=True)
            notice = f"🔓 Тикет снова открыт ({owner.mention})!"
        else:
            notice = "🔓 Тикет снова открыт! (владелец тикета не найден на сервере)"

        new_name = interaction.channel.name
        if new_name.startswith("closed-"):
            new_name = "ticket-" + new_name[len("closed-"):]
            try:
                await interaction.channel.edit(name=new_name[:100])
            except discord.HTTPException:
                pass

        await interaction.followup.send(notice)
        try:
            await interaction.message.delete()
        except discord.HTTPException:
            pass

    @discord.ui.button(label="Удалить тикет (НЕ ЖМИ СРАЗУ)", style=discord.ButtonStyle.danger, emoji="⛔", custom_id="delete_ticket_btn")
    async def delete_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await ensure_staff(interaction):
            return
        await interaction.response.send_message("💥 Удаление тикета через 5 секунд...")
        await asyncio.sleep(5)
        try:
            await interaction.channel.delete()
        except discord.NotFound:
            pass


class ConfirmCloseView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Да", style=discord.ButtonStyle.success, emoji="✅", custom_id="confirm_close_btn")
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await ensure_access(interaction):
            return
        await interaction.response.defer()

        owner_id = get_ticket_owner_id(interaction.channel)
        owner = interaction.guild.get_member(owner_id) if owner_id else None
        if owner:
            await interaction.channel.set_permissions(owner, read_messages=False)

        new_name = interaction.channel.name
        if not new_name.startswith("closed-"):
            try:
                await interaction.channel.edit(name=f"closed-{new_name}"[:100])
            except discord.HTTPException:
                pass

        # Убираем кнопки с сообщения подтверждения, чтобы не нажали повторно
        try:
            await interaction.edit_original_response(view=None)
        except discord.HTTPException:
            pass

        embed = discord.Embed(description=f"Тикет закрыт {interaction.user.mention}", color=discord.Color.gold())
        await interaction.followup.send(embed=embed, view=TicketControlsView())

    @discord.ui.button(label="Отменить", style=discord.ButtonStyle.secondary, emoji="🍑", custom_id="cancel_close_btn")
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="Закрытие отменено.", view=None)


class CloseButtonView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Закрыть", style=discord.ButtonStyle.secondary, emoji="🔒", custom_id="close_ticket_init_btn")
    async def close_init(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await ensure_access(interaction):
            return
        await interaction.response.send_message("Вы точно хотите закрыть данный **тикет**?", view=ConfirmCloseView(), ephemeral=True)


class TicketReasonModal(discord.ui.Modal, title="Поддержка"):
    reason = discord.ui.TextInput(
        label="Причина подачи тикета.",
        placeholder="Информация:",
        style=discord.TextStyle.paragraph,
        required=True,
        max_length=1000,
    )

    async def on_submit(self, interaction: discord.Interaction):
        # Сначала подтверждаем взаимодействие: создание канала может занять больше 3 секунд
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            guild = interaction.guild
            category = guild.get_channel(TICKET_CATEGORY_ID)
            if not isinstance(category, discord.CategoryChannel):
                await interaction.followup.send("❌ Категория для тикетов не найдена. Сообщите администратору.", ephemeral=True)
                return

            existing = find_open_ticket(guild, interaction.user.id)
            if existing:
                await interaction.followup.send(
                    f"❌ У вас уже есть тикет: {existing.mention}. Сначала дождитесь его закрытия или удаления.",
                    ephemeral=True,
                )
                return

            staff_pings = " ".join(f"<@&{r_id}>" for r_id in STAFF_ROLE_IDS if guild.get_role(r_id))

            overwrites = {
                guild.default_role: discord.PermissionOverwrite(read_messages=False),
                interaction.user: discord.PermissionOverwrite(read_messages=True, send_messages=True, attach_files=True),
                guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True)
            }
            for r_id in STAFF_ROLE_IDS:
                role = guild.get_role(r_id)
                if role:
                    overwrites[role] = discord.PermissionOverwrite(read_messages=True, send_messages=True)

            ticket_channel = await category.create_text_channel(
                name=f"ticket-{interaction.user.name}".lower(),
                topic=f"{OWNER_TOPIC_PREFIX}{interaction.user.id}",
                overwrites=overwrites,
            )

            await ticket_channel.send(content=f"{interaction.user.mention} **Здравствуйте.** *Уважаемые администраторы скоро помогут, подождите.* {staff_pings}")

            embed_help = discord.Embed(title="Поддержка скоро поможет вам.", color=discord.Color.green())
            embed_reason = discord.Embed(title="Причина подачи тикета.", description=f"```{self.reason.value}```", color=discord.Color.dark_grey())

            await ticket_channel.send(embed=embed_help)
            await ticket_channel.send(embed=embed_reason, view=CloseButtonView())
            await interaction.followup.send(f"✅ Тикет создан: {ticket_channel.mention}", ephemeral=True)
        except discord.Forbidden:
            await interaction.followup.send("❌ У бота нет прав создавать каналы в категории тикетов.", ephemeral=True)
        except Exception:
            traceback.print_exc()
            await interaction.followup.send("❌ Произошла ошибка при создании тикета. Сообщите администратору.", ephemeral=True)


class TicketLauncherPanel(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Создать запрос", style=discord.ButtonStyle.success, emoji="👍", custom_id="create_ticket_panel_btn")
    async def create_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(TicketReasonModal())


# --- Класс модуля (Cog) ---
class TicketsCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        # Регистрируем постоянные view — без этого кнопки на старых сообщениях
        # перестают работать после перезапуска бота
        for view in (TicketLauncherPanel(), CloseButtonView(), ConfirmCloseView(), TicketControlsView()):
            self.bot.add_view(view)

    @commands.Cog.listener()
    async def on_ready(self):
        # Синхронизируем слэш-команды один раз после подключения к Discord
        if getattr(self.bot, "_slash_synced", False):
            return
        self.bot._slash_synced = True
        try:
            if GUILD_ID:
                guild = discord.Object(id=GUILD_ID)
                self.bot.tree.copy_global_to(guild=guild)
                await self.bot.tree.sync(guild=guild)
            else:
                await self.bot.tree.sync()
            print("[+] Слэш-команды синхронизированы")
        except Exception:
            print("[-] Не удалось синхронизировать слэш-команды")
            traceback.print_exc()

    @discord.app_commands.command(name="ticket", description="Написать в поддержку (создать тикет)")
    @discord.app_commands.guild_only()
    async def ticket(self, interaction: discord.Interaction):
        await interaction.response.send_modal(TicketReasonModal())

    @commands.hybrid_command(name="setup_tickets", help="Публикует панель создания тикетов.")
    @commands.has_permissions(administrator=True)
    @discord.app_commands.default_permissions(administrator=True)
    async def setup_tickets(self, ctx):
        try:
            await ctx.message.delete()
        except discord.HTTPException:
            pass
        embed = discord.Embed(
            title="🎧 Поддержка",
            description="Нажми кнопку **«Создать запрос»** ниже, чтобы обратиться в поддержку.",
            color=discord.Color.green(),
        )
        await ctx.send(embed=embed, view=TicketLauncherPanel())

    @commands.hybrid_command(name="close", help="Закрыть тикет в текущем канале.")
    async def close(self, ctx):
        channel = ctx.channel
        if getattr(channel, "category_id", None) != TICKET_CATEGORY_ID and not channel.name.startswith("ticket-"):
            return
        if not isinstance(ctx.author, discord.Member):
            return
        owner_id = get_ticket_owner_id(channel)
        if not (is_staff(ctx.author) or ctx.author.id == owner_id):
            await ctx.send("⛔ Только стафф или владелец тикета могут его закрыть.", delete_after=10)
            return
        await ctx.send("Вы точно хотите закрыть данный **тикет**?", view=ConfirmCloseView())

    async def cog_command_error(self, ctx, error):
        if isinstance(error, commands.MissingPermissions):
            await ctx.send("⛔ Нужны права администратора.", delete_after=10)
        else:
            raise error


async def setup(bot):
    await bot.add_cog(TicketsCog(bot))
