import os
import json
import asyncio
import logging
import random
import re
from datetime import datetime, timedelta
from typing import Optional, List

from aiogram import Bot, Dispatcher, types, F
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, Message, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.client.default import DefaultBotProperties

# МОЩНОЕ ЛОГИРОВАНИЕ
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Получение токенов
BOT_TOKEN = os.getenv('BOT_TOKEN')
ADMIN_ID = int(os.getenv('ADMIN_ID', '0') or '0')

if not BOT_TOKEN:
    logger.error('❌ BOT_TOKEN не найден!')
    raise ValueError("BOT_TOKEN not set!")

logger.info(f'🤖 Бот запускается')
logger.info(f'👑 ADMIN_ID: {ADMIN_ID}')

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

# Файлы для данных
FILES = {
    'banlist': 'banlist.json',
    'logs': 'logs.json',
    'idlist': 'idlist.json',
    'keys': 'keys.json',
    'settings': 'settings.json'
}

# Глобальные состояния
maintenance_mode = False
maintenance_until = None
banned_notified = set()
user_states = {}
account_connected = False  # Флаг подключения аккаунта

# ─── Работа с файлами ───

def load_json(filename):
    try:
        with open(filename, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        if filename == 'banlist.json':
            return []
        return {}

def save_json(filename, data):
    with open(filename, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def init_files():
    files = ['banlist.json', 'logs.json', 'idlist.json', 'keys.json', 'settings.json']
    for file in files:
        try:
            with open(file, 'r') as f:
                pass
        except FileNotFoundError:
            if file == 'banlist.json':
                with open(file, 'w') as f:
                    json.dump([], f)
            else:
                with open(file, 'w') as f:
                    json.dump({}, f)
    logger.info('✅ Файлы инициализированы')

# ─── Утилиты ───

def moscow_time():
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')

def progress_bar(percent):
    filled = int(percent / 10)
    return '█' * filled + '░' * (10 - filled)

def parse_ban_time(time_str: str) -> int:
    time_str = time_str.lower().strip()
    
    if time_str in ['-1w', 'forever', 'навсегда']:
        return -1
    
    total_minutes = 0
    
    h_match = re.search(r'(\d+)h', time_str)
    if h_match:
        total_minutes += int(h_match.group(1)) * 60
    
    m_match = re.search(r'(\d+)m', time_str)
    if m_match:
        total_minutes += int(m_match.group(1))
    
    d_match = re.search(r'(\d+)d', time_str)
    if d_match:
        total_minutes += int(d_match.group(1)) * 24 * 60
    
    w_match = re.search(r'(\d+)w', time_str)
    if w_match:
        total_minutes += int(w_match.group(1)) * 7 * 24 * 60
    
    if total_minutes == 0:
        try:
            total_minutes = int(time_str)
        except ValueError:
            return None
    
    return total_minutes

# ─── Работа с данными ───

async def log_command(user_id, username, command):
    try:
        logs = load_json(FILES['logs'])
        user_id_str = str(user_id)
        if user_id_str not in logs:
            logs[user_id_str] = []
        logs[user_id_str].append({
            'time': datetime.now().isoformat(),
            'command': command[:500]
        })
        if len(logs[user_id_str]) > 1000:
            logs[user_id_str] = logs[user_id_str][-1000:]
        save_json(FILES['logs'], logs)
    except Exception as e:
        logger.error(f'Ошибка логирования: {e}')

async def save_user(user_id, username, first_name):
    try:
        users = load_json(FILES['idlist'])
        user_id_str = str(user_id)
        if user_id_str not in users:
            users[user_id_str] = {
                'username': username or None,
                'first_name': first_name or None,
                'last_seen': datetime.now().isoformat()
            }
            save_json(FILES['idlist'], users)
    except Exception as e:
        logger.error(f'Ошибка сохранения юзера: {e}')

async def is_banned(user_id):
    try:
        bans = load_json(FILES['banlist'])
        now = datetime.now().isoformat()
        for ban in bans:
            if ban['user_id'] == user_id:
                if ban.get('forever', False):
                    return ban
                if now < ban['unban_at']:
                    return ban
        return None
    except Exception as e:
        return None

async def check_expired_bans():
    try:
        bans = load_json(FILES['banlist'])
        now = datetime.now().isoformat()
        active_bans = [b for b in bans if b.get('forever', False) or now < b['unban_at']]
        if len(active_bans) != len(bans):
            save_json(FILES['banlist'], active_bans)
    except Exception as e:
        logger.error(f'Ошибка проверки банов: {e}')

async def get_tech_works():
    try:
        settings = load_json(FILES['settings'])
        if settings.get('maintenance', False) and settings.get('maintenance_until'):
            if datetime.now().isoformat() < settings['maintenance_until']:
                return {'active': True, 'until': settings['maintenance_until']}
            else:
                settings['maintenance'] = False
                settings['maintenance_until'] = None
                save_json(FILES['settings'], settings)
        return {'active': False}
    except:
        return {'active': False}

async def set_tech_works(active, until=None):
    try:
        settings = load_json(FILES['settings'])
        settings['maintenance'] = active
        settings['maintenance_until'] = until
        save_json(FILES['settings'], settings)
    except Exception as e:
        logger.error(f'Ошибка настройки тех-работ: {e}')

def is_admin(user_id):
    return ADMIN_ID != 0 and user_id == ADMIN_ID

# ─── Анимация ───

async def show_connection_animation(target, action_type: str):
    stages = [
        {
            'title': '🔄 ПОДКЛЮЧЕНИЕ К СЕРВЕРАМ',
            'servers': [
                {'name': 'Сервер #1', 'progress': 40, 'done': False},
                {'name': 'Сервер #2', 'progress': 0, 'done': False},
                {'name': 'Сервер #3', 'progress': 0, 'done': False},
                {'name': 'Сервер #4', 'progress': 0, 'done': False},
                {'name': 'Сервер #5', 'progress': 0, 'done': False},
            ]
        },
        {
            'title': '🔄 ПОДКЛЮЧЕНИЕ К СЕРВЕРАМ',
            'servers': [
                {'name': 'Сервер #1', 'progress': 80, 'done': False},
                {'name': 'Сервер #2', 'progress': 60, 'done': False},
                {'name': 'Сервер #3', 'progress': 40, 'done': False},
                {'name': 'Сервер #4', 'progress': 20, 'done': False},
                {'name': 'Сервер #5', 'progress': 0, 'done': False},
            ]
        },
        {
            'title': '🔄 ПОДКЛЮЧЕНИЕ К СЕРВЕРАМ',
            'servers': [
                {'name': 'Сервер #1', 'progress': 100, 'done': True},
                {'name': 'Сервер #2', 'progress': 100, 'done': True},
                {'name': 'Сервер #3', 'progress': 80, 'done': False},
                {'name': 'Сервер #4', 'progress': 60, 'done': False},
                {'name': 'Сервер #5', 'progress': 40, 'done': False},
            ]
        },
        {
            'title': '✅ ПОДКЛЮЧЕНИЕ ВЫПОЛНЕНО',
            'servers': None,
            'final': True
        }
    ]
    
    msg = await target.answer("⏳ Подключение...")
    
    for stage in stages:
        if stage.get('final'):
            text = f"""✅ ПОДКЛЮЧЕНИЕ ВЫПОЛНЕНО

📊 Получение данных...
⏳ Обработка информации..."""
            try:
                await msg.edit_text(text)
            except:
                pass
            await asyncio.sleep(0.5)
            return msg
        
        text = f"{stage['title']}\n\n"
        for server in stage['servers']:
            bar = '█' * (server['progress'] // 10) + '░' * (10 - server['progress'] // 10)
            checkmark = ' ✅' if server['done'] else ''
            text += f"📡 {server['name']}... {bar} {server['progress']}%{checkmark}\n"
        text += "\n⏳ Ожидайте..."
        
        try:
            await msg.edit_text(text)
        except:
            pass
        await asyncio.sleep(0.4)
    
    return msg

# ─── Результаты ───

def format_ip_result(data):
    return f"""✅ РЕЗУЛЬТАТ ПРОБИВА IP

━━━━━━━━━━━━━━━━━━━━━━━━━━
🌐 IP-адрес: <code>{data or '185.234.xx.xx'}</code>
🌍 Город: Москва
🏙️ Область: Московская область
🇷🇺 Страна: Россия
📍 Координаты: 55.7558, 37.6173
🏠 Адрес: ул. Тверская, д. 1
📡 Оператор: ООО «Ростелеком»
🕒 Часовой пояс: Europe/Moscow
━━━━━━━━━━━━━━━━━━━━━━━━━━
🛡️ БЕЗОПАСНОСТЬ

⚠️ IP в чёрном списке: ❌ НЕТ
🚫 IP в базе мошенников: ❌ НЕТ
🕵️ IP в базе скамеров: ❌ НЕТ
✅ Доверенность IP: 95%
📊 Использовано серверов: 20/20
━━━━━━━━━━━━━━━━━━━━━━━━━━"""

def format_phone_result(data):
    return f"""✅ РЕЗУЛЬТАТ ПРОБИВА НОМЕРА

━━━━━━━━━━━━━━━━━━━━━━━━━━
📱 Номер: <code>{data or '+7 999 123-45-67'}</code>
📡 Оператор: МТС
🌍 Регион: Московская область
🏙️ Город: Москва
📊 Тип номера: Мобильный
🕒 Часовой пояс: Europe/Moscow
🇷🇺 Страна: Россия
━━━━━━━━━━━━━━━━━━━━━━━━━━
🛡️ БЕЗОПАСНОСТЬ

⚠️ Номер в чёрном списке: ❌ НЕТ
🚫 Номер в базе мошенников: ❌ НЕТ
🕵️ Номер в базе скамеров: ❌ НЕТ
✅ Доверенность номера: 88%
📊 Использовано серверов: 20/20
━━━━━━━━━━━━━━━━━━━━━━━━━━"""

def format_username_result(data):
    return f"""✅ РЕЗУЛЬТАТ ПРОБИВА USERNAME

━━━━━━━━━━━━━━━━━━━━━━━━━━
👤 Username: <code>{data or '@example_user'}</code>
🆔 ID: 123456789
📛 Имя: Алексей Смирнов
📅 Дата регистрации: 12.05.2020
🌍 Язык интерфейса: Русский
🔍 Активность: высокая
📱 Привязан к номеру: +7 999 123-45-67
━━━━━━━━━━━━━━━━━━━━━━━━━━
🛡️ БЕЗОПАСНОСТЬ

⚠️ Username в чёрном списке: ❌ НЕТ
🚫 Аккаунт в базе мошенников: ❌ НЕТ
🕵️ Аккаунт в базе скамеров: ❌ НЕТ
✅ Доверенность аккаунта: 82%
📊 Использовано серверов: 20/20
━━━━━━━━━━━━━━━━━━━━━━━━━━"""

# ─── Клавиатуры ───

def get_menu_keyboard():
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="1️⃣ Пробив IP", callback_data="ip"),
        InlineKeyboardButton(text="2️⃣ Пробив номера", callback_data="phone"),
    )
    builder.row(
        InlineKeyboardButton(text="3️⃣ Пробив юзера (@)", callback_data="username"),
    )
    return builder.as_markup()

HELP_TEXT = """📚 ДОСТУПНЫЕ КОМАНДЫ

━━━━━━━━━━━━━━━━━━━━━━━━━━
🔍 ПРОБИВ

.whois ip [IP] — пробив IP-адреса
.whois n [номер] — пробив номера телефона
.whois qz [@username] — пробив Telegram-юзернейма

━━━━━━━━━━━━━━━━━━━━━━━━━━
⚡ ДОПОЛНИТЕЛЬНО

.help — справка

━━━━━━━━━━━━━━━━━━━━━━━━━━
🛡️ НАКАЗАНИЯ (PLUS)

.ban (ID) (TIME) (REASON) — Выдать бан
  TIME: 30m, 2h, 1h30m, 7d, -1w (навсегда)
.unban (ID) (REASON) — Снять блокировку
.chkban (ID) — Проверить бан пользователя

━━━━━━━━━━━━━━━━━━━━━━━━━━
📌 .команды — в чатах с собеседниками (Business API)
📌 /команды — в личке с ботом"""

# ─── Удаление сообщения ───

async def delete_message(target, message_id):
    try:
        await bot.delete_message(chat_id=target.chat.id, message_id=message_id)
        logger.info(f'🗑️ Удалено сообщение {message_id}')
    except Exception as e:
        logger.error(f'❌ Не удалось удалить: {e}')

# ─── ОБРАБОТЧИК ПОДКЛЮЧЕНИЯ АККАУНТА ───

@dp.chat_member()
async def handle_chat_member(chat_member_update: types.ChatMemberUpdated):
    """
    Обработчик подключения бота к аккаунту
    Срабатывает когда бота добавляют в чат или подключают к аккаунту
    """
    user_id = chat_member_update.from_user.id
    chat_id = chat_member_update.chat.id
    new_status = chat_member_update.new_chat_member.status
    
    logger.info(f'🔗 [CHAT_MEMBER] Пользователь {user_id}, чат {chat_id}, статус: {new_status}')
    
    # Если бота добавили в чат или подключили к аккаунту
    if new_status in ['member', 'administrator', 'creator']:
        # Отправляем уведомление админу
        if ADMIN_ID:
            try:
                await bot.send_message(
                    ADMIN_ID,
                    f'✅ АККАУНТ ПОДКЛЮЧЕН!\n\n'
                    f'📌 Бот успешно подключен к аккаунту\n'
                    f'🕐 Время: {moscow_time()}\n'
                    f'📊 Chat ID: {chat_id}\n'
                    f'👤 User ID: {user_id}\n\n'
                    f'Теперь бот будет видеть сообщения в чатах с собеседниками!'
                )
                logger.info(f'📤 Уведомление отправлено админу {ADMIN_ID}')
            except Exception as e:
                logger.error(f'❌ Ошибка отправки уведомления: {e}')

# ─── ОБРАБОТЧИК BUSINESS CONNECTION ───

@dp.business_connection()
async def handle_business_connection(connection: types.BusinessConnection):
    """
    Обработчик подключения Business API
    Срабатывает когда бот подключается к бизнес-аккаунту
    """
    user_id = connection.user_id
    chat_id = connection.user_id  # В бизнес-подключении user_id = chat_id
    
    logger.info(f'🔗 [BUSINESS_CONNECTION] Подключение от {user_id}')
    
    global account_connected
    account_connected = True
    
    # Отправляем уведомление админу
    if ADMIN_ID:
        try:
            await bot.send_message(
                ADMIN_ID,
                f'✅ BUSINESS API ПОДКЛЮЧЕН!\n\n'
                f'📌 Бот подключен к Business API\n'
                f'🕐 Время: {moscow_time()}\n'
                f'👤 User ID: {user_id}\n'
                f'🔗 Connection ID: {connection.id}\n\n'
                f'Теперь бот видит сообщения в чатах с собеседниками!\n'
                f'Проверь: отправь .help в чат с собеседником.'
            )
            logger.info(f'📤 Уведомление Business API отправлено админу {ADMIN_ID}')
        except Exception as e:
            logger.error(f'❌ Ошибка отправки уведомления: {e}')

# ─── УНИВЕРСАЛЬНЫЙ ОБРАБОТЧИК СООБЩЕНИЙ ───

@dp.message()
async def handle_all_messages(message: Message):
    """
    УНИВЕРСАЛЬНЫЙ ОБРАБОТЧИК ВСЕХ СООБЩЕНИЙ
    Определяет тип чата и обрабатывает соответственно
    """
    user_id = message.from_user.id
    username = message.from_user.username or "без юзера"
    text = message.text or ""
    chat_id = message.chat.id
    chat_type = message.chat.type
    message_id = message.message_id
    
    # Проверяем, является ли сообщение бизнес-сообщением
    is_business = hasattr(message, 'business_connection_id') and message.business_connection_id is not None
    
    # МОЩНЫЙ ЛОГ - ВИДНО ВСЕ СООБЩЕНИЯ!
    logger.info(f'📩 [ВХОДЯЩЕЕ] от {user_id} (@{username}) в чат {chat_id} ({chat_type}){" [BUSINESS]" if is_business else ""}: "{text[:100]}"')
    
    # Если это бизнес-сообщение - отправляем дополнительный лог админу
    if is_business and ADMIN_ID:
        try:
            await bot.send_message(
                ADMIN_ID,
                f'📩 [BUSINESS] Сообщение получено!\n\n'
                f'👤 От: {username} (ID: {user_id})\n'
                f'💬 Текст: {text[:200]}\n'
                f'🕐 Время: {moscow_time()}'
            )
        except:
            pass
    
    # Проверка на бан
    ban = await is_banned(user_id)
    if ban:
        if user_id not in banned_notified:
            banned_notified.add(user_id)
            if ban.get('forever', False):
                await message.answer(
                    f'⛔ ВАС ЗАБЛОКИРОВАЛИ В БОТЕ НАВСЕГДА\n\n'
                    f'📌 Причина: {ban["reason"]}\n'
                    f'🕐 Дата блокировки: {datetime.fromisoformat(ban["banned_at"]).strftime("%Y-%m-%d %H:%M:%S")}'
                )
            else:
                await message.answer(
                    f'⛔ ВАС ЗАБЛОКИРОВАЛИ В БОТЕ\n\n'
                    f'📌 Причина: {ban["reason"]}\n'
                    f'⏱ Длительность: {ban.get("duration", "неизвестно")}\n'
                    f'🕐 Дата блокировки: {datetime.fromisoformat(ban["banned_at"]).strftime("%Y-%m-%d %H:%M:%S")}\n'
                    f'⏳ Разблокировка: {datetime.fromisoformat(ban["unban_at"]).strftime("%Y-%m-%d %H:%M:%S")}'
                )
        if text.startswith('.') or text.startswith('/'):
            await delete_message(message, message_id)
        return
    
    # Тех работы
    global maintenance_mode, maintenance_until
    if maintenance_mode and not is_admin(user_id):
        if maintenance_until and datetime.now().isoformat() > maintenance_until:
            maintenance_mode = False
        else:
            if text.startswith('.') or text.startswith('/'):
                await delete_message(message, message_id)
            await message.answer(
                f'🛠️ БОТ НА ТЕХНИЧЕСКИХ РАБОТАХ\n\n'
                f'🕐 ВРЕМЯ: до {datetime.fromisoformat(maintenance_until).strftime("%Y-%m-%d %H:%M:%S")}'
            )
            return
    
    # Сохраняем пользователя
    await save_user(user_id, username, message.from_user.first_name)
    
    # Если это ввод данных (не команда)
    if user_id in user_states and user_states[user_id].get('waiting_input'):
        if not text.startswith('.') and not text.startswith('/'):
            await handle_user_input(message)
            return
    
    # ─── ОБРАБОТКА КОМАНД ───
    
    # Бизнес-команды (с точкой) - работают в ЛЮБЫХ чатах
    if text.startswith('.'):
        await handle_business_command(message, text)
        return
    
    # ЛС команды (с /) - только в личке с ботом
    if text.startswith('/') and chat_type == 'private':
        await handle_dm_command(message, text)
        return

# ─── ОБРАБОТЧИК ЛС КОМАНД ───

async def handle_dm_command(message: Message, text: str):
    """Обработка команд в личке (/команды)"""
    user_id = message.from_user.id
    username = message.from_user.username
    
    logger.info(f'🔄 [DM CMD] /{text.split()[0]} от {user_id}')
    
    await log_command(user_id, username, text)
    
    parts = text[1:].split()
    if not parts:
        return
    
    cmd = parts[0].lower()
    args = parts[1:] if len(parts) > 1 else []
    
    # /start
    if cmd == 'start':
        await message.answer(
            '👋 Привет! Я бот для пробива информации.\n\n'
            'Выбери действие:',
            reply_markup=get_menu_keyboard()
        )
        return
    
    # /menu
    if cmd == 'menu':
        await message.answer(
            '📋 МЕНЮ БОТА\n\nВыберите действие:',
            reply_markup=get_menu_keyboard()
        )
        return
    
    # /help
    if cmd == 'help':
        await message.answer(HELP_TEXT)
        return
    
    # Админ команды
    if not is_admin(user_id):
        logger.warning(f'⛔ Не-админ {user_id} пытался использовать /{cmd}')
        return
    
    if cmd == 'ban':
        await handle_ban(message, args)
        return
    
    if cmd == 'unban':
        await handle_unban(message, args)
        return
    
    if cmd == 'chkban':
        await handle_chkban(message, args)
        return
    
    if cmd == 'logs':
        await handle_logs(message, args)
        return
    
    if cmd == 'idlist':
        await handle_idlist(message)
        return
    
    if cmd == 'key':
        await handle_key(message)
        return
    
    if cmd == 'tex':
        await handle_tex(message, args)
        return
    
    await message.answer(f'❌ Неизвестная команда: {cmd}')

# ─── ОБРАБОТЧИК БИЗНЕС-КОМАНД ───

async def handle_business_command(message: Message, text: str):
    """Обработка бизнес-команд (.команды) - работают в ЛЮБЫХ чатах!"""
    user_id = message.from_user.id
    username = message.from_user.username
    chat_id = message.chat.id
    message_id = message.message_id
    
    logger.info(f'🔄 [BUSINESS CMD] .{text.split()[0]} от {user_id} в чате {chat_id}')
    
    # Удаляем команду
    await delete_message(message, message_id)
    
    await log_command(user_id, username, text)
    
    parts = text[1:].split()
    if not parts:
        return
    
    cmd = parts[0].lower()
    args = parts[1:] if len(parts) > 1 else []
    
    # .help
    if cmd == 'help':
        await message.answer(HELP_TEXT)
        return
    
    # .whois
    if cmd == 'whois':
        if len(args) < 2:
            await message.answer(
                '❌ Укажите тип и данные для пробива.\n\n'
                'Примеры:\n.whois ip 8.8.8.8\n.whois n +79991234567\n.whois qz @username'
            )
            return
        
        action = args[0]
        target = ' '.join(args[1:])
        
        await show_connection_animation(message, action)
        
        if action == 'ip':
            result = format_ip_result(target)
        elif action == 'n':
            result = format_phone_result(target)
        elif action == 'qz':
            result = format_username_result(target)
        else:
            await message.answer('❌ Неверный тип. Используйте: ip, n, qz')
            return
        
        await message.answer(result)
        return
    
    # Админ команды
    if not is_admin(user_id):
        logger.warning(f'⛔ Не-админ {user_id} пытался использовать .{cmd}')
        return
    
    if cmd == 'ban':
        await handle_ban(message, args)
        return
    
    if cmd == 'unban':
        await handle_unban(message, args)
        return
    
    if cmd == 'chkban':
        await handle_chkban(message, args)
        return
    
    if cmd == 'logs':
        await handle_logs(message, args)
        return
    
    if cmd == 'idlist':
        await handle_idlist(message)
        return
    
    if cmd == 'key':
        await handle_key(message)
        return
    
    if cmd == 'tex':
        await handle_tex(message, args)
        return
    
    await message.answer(f'❌ Неизвестная команда: {cmd}')

# ─── ОБРАБОТЧИК ВВОДА ДАННЫХ ───

async def handle_user_input(message: Message):
    user_id = message.from_user.id
    state = user_states.get(user_id, {})
    
    if not state.get('waiting_input'):
        return
    
    logger.info(f'🔄 [INPUT] {message.text[:50]} от {user_id}')
    
    action = state.get('action')
    await delete_message(message, message.message_id)
    
    await log_command(user_id, message.from_user.username, f'[ВВОД] {message.text}')
    
    await show_connection_animation(message, action)
    
    if action == 'ip':
        result = format_ip_result(message.text)
    elif action == 'phone':
        result = format_phone_result(message.text)
    else:
        result = format_username_result(message.text)
    
    await message.answer(result)
    user_states[user_id] = {}

# ─── ОБРАБОТЧИК КНОПОК ───

@dp.callback_query()
async def handle_callback(callback: CallbackQuery):
    user_id = callback.from_user.id
    
    logger.info(f'🔘 [КНОПКА] {callback.data} от {user_id}')
    
    await callback.answer()
    
    ban = await is_banned(user_id)
    if ban:
        await callback.message.edit_text('⛔ Вы заблокированы в боте!')
        return
    
    action = callback.data
    action_names = {'ip': 'IP', 'phone': 'номера', 'username': 'юзера (@)'}
    
    await callback.message.delete()
    
    msg = await callback.message.answer(
        f'Действие выбрано: Whois {action_names[action]}\n'
        f'Пришлите сюда {"IP:" if action == "ip" else "номер:" if action == "phone" else "@username:"}'
    )
    
    user_states[user_id] = {
        'waiting_input': True,
        'action': action,
        'prompt_id': msg.message_id
    }

# ─── АДМИН КОМАНДЫ ───

async def handle_ban(message, args: List[str]):
    if len(args) < 3:
        await message.answer('❌ Формат: .ban (ID) (ВРЕМЯ) (ПРИЧИНА)\nПримеры:\n.ban 123456789 30m Спам\n.ban 123456789 -1w Навсегда')
        return
    
    try:
        target_id = int(args[0])
        time_str = args[1]
        reason = ' '.join(args[2:])
        
        minutes = parse_ban_time(time_str)
        if minutes is None:
            await message.answer('❌ Неверный формат времени. Примеры: 30m, 2h, 1h30m, 7d, -1w')
            return
        
        now = datetime.now()
        bans = load_json(FILES['banlist'])
        
        if minutes == -1:
            ban_data = {
                'user_id': target_id,
                'reason': reason,
                'duration': time_str,
                'banned_at': now.isoformat(),
                'forever': True,
                'issued_by': message.from_user.id
            }
        else:
            unban_at = now + timedelta(minutes=minutes)
            ban_data = {
                'user_id': target_id,
                'reason': reason,
                'duration': time_str,
                'banned_at': now.isoformat(),
                'unban_at': unban_at.isoformat(),
                'forever': False,
                'issued_by': message.from_user.id
            }
        
        bans = [b for b in bans if b['user_id'] != target_id]
        bans.append(ban_data)
        save_json(FILES['banlist'], bans)
        
        if target_id in banned_notified:
            banned_notified.remove(target_id)
        
        if minutes == -1:
            result = f"""✅ ПОЛЬЗОВАТЕЛЬ ЗАБАНЕН НАВСЕГДА

🆔 ID: <code>{target_id}</code>
📌 Причина: {reason}
🕐 Дата: {moscow_time()}
⏳ БАН БЕССРОЧНЫЙ"""
        else:
            result = f"""✅ ПОЛЬЗОВАТЕЛЬ ЗАБАНЕН

🆔 ID: <code>{target_id}</code>
📌 Причина: {reason}
⏱ Время: {time_str}
🕐 Дата: {moscow_time()}
⏳ Бан активен до: {unban_at.strftime('%Y-%m-%d %H:%M:%S')}"""
        
        await message.answer(result)
        
        try:
            if minutes == -1:
                await bot.send_message(
                    target_id,
                    f"""⛔ ВАС ЗАБЛОКИРОВАЛИ В БОТЕ НАВСЕГДА

📌 Причина: {reason}
🕐 Дата блокировки: {moscow_time()}"""
                )
            else:
                await bot.send_message(
                    target_id,
                    f"""⛔ ВАС ЗАБЛОКИРОВАЛИ В БОТЕ

📌 Причина: {reason}
⏱ Длительность: {time_str}
🕐 Дата блокировки: {moscow_time()}
⏳ Разблокировка: {unban_at.strftime('%Y-%m-%d %H:%M:%S')}"""
                )
            banned_notified.add(target_id)
        except:
            pass
            
    except ValueError:
        await message.answer('❌ Неверный формат ID.')

async def handle_unban(message, args: List[str]):
    if len(args) < 2:
        await message.answer('❌ Формат: .unban (ID) (ПРИЧИНА)\nПример: .unban 123456789 Ошибка')
        return
    
    try:
        target_id = int(args[0])
        reason = ' '.join(args[1:])
        
        bans = load_json(FILES['banlist'])
        filtered = [b for b in bans if b['user_id'] != target_id]
        
        if len(filtered) == len(bans):
            await message.answer(f'⛔ Данный {target_id} не заблокирован.')
            return
        
        save_json(FILES['banlist'], filtered)
        if target_id in banned_notified:
            banned_notified.remove(target_id)
        
        result = f"""✅ ПОЛЬЗОВАТЕЛЬ РАЗБАНЕН

🆔 ID: <code>{target_id}</code>
📌 Причина разбана: {reason}
🕐 Дата: {moscow_time()}
🔓 Пользователь снова может пользоваться ботом"""
        
        await message.answer(result)
        
        try:
            await bot.send_message(
                target_id,
                f"""✅ ВАС РАЗБЛОКИРОВАЛИ

📌 Причина разблокировки: {reason}
🕐 Дата: {moscow_time()}
🔓 Теперь вы снова можете пользоваться ботом"""
            )
        except:
            pass
            
    except ValueError:
        await message.answer('❌ Неверный формат ID.')

async def handle_chkban(message, args: List[str]):
    if len(args) < 1:
        await message.answer('❌ Формат: .chkban (ID)')
        return
    
    try:
        target_id = int(args[0])
        bans = load_json(FILES['banlist'])
        ban = next((b for b in bans if b['user_id'] == target_id), None)
        
        if not ban:
            await message.answer(f'⛔ Данный {target_id} не заблокирован.')
            return
        
        if ban.get('forever', False):
            result = f"""---<code>{target_id}</code>---
📌Причина: {ban['reason']}
🕐Дата выдачи: {datetime.fromisoformat(ban['banned_at']).strftime('%Y-%m-%d %H:%M:%S')}
⏳ БАН НАВСЕГДА"""
        else:
            now = datetime.now()
            unban_at = datetime.fromisoformat(ban['unban_at'])
            if unban_at < now:
                await message.answer(f'⛔ Данный {target_id} не заблокирован.')
                return
            remaining = int((unban_at - now).total_seconds() // 60)
            hours = remaining // 60
            minutes = remaining % 60
            result = f"""---<code>{target_id}</code>---
📌Причина: {ban['reason']}
🕐Дата выдачи: {datetime.fromisoformat(ban['banned_at']).strftime('%Y-%m-%d %H:%M:%S')}
🕐Дата снятия бана: {unban_at.strftime('%Y-%m-%d %H:%M:%S')}
🔓Осталось до окончания: {hours}ч {minutes}м"""
        
        await message.answer(result)
        
    except ValueError:
        await message.answer('❌ Неверный формат ID.')

async def handle_logs(message, args: List[str]):
    if len(args) < 2:
        await message.answer('❌ Формат: .logs (ID) (количество)\nПример: .logs 123456789 10')
        return
    
    try:
        target_id = int(args[0])
        limit = min(int(args[1]), 100)
        
        logs = load_json(FILES['logs'])
        user_logs = logs.get(str(target_id), [])[-limit:][::-1]
        
        if not user_logs:
            await message.answer('📭 Логи не найдены для данного ID.')
            return
        
        text = f'📋 Логи пользователя <code>{target_id}</code> (последние {len(user_logs)})\n\n'
        for log in user_logs:
            text += f"📝 {log['command']}\n🕐 {datetime.fromisoformat(log['time']).strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        
        if len(text) > 4096:
            chunks = [text[i:i+4096] for i in range(0, len(text), 4096)]
            for chunk in chunks:
                await message.answer(chunk)
        else:
            await message.answer(text)
            
    except ValueError:
        await message.answer('❌ Неверный формат ID или количества.')

async def handle_idlist(message):
    users = load_json(FILES['idlist'])
    
    if not users:
        await message.answer('📭 Список ID пуст.')
        return
    
    text = f'📋 Список пользователей ({len(users)})\n\n'
    for user_id, data in users.items():
        username = data.get('username') or 'нет username'
        text += f'👤 @{username} → <code>{user_id}</code>\n'
    
    if len(text) > 4096:
        chunks = [text[i:i+4096] for i in range(0, len(text), 4096)]
        for chunk in chunks:
            await message.answer(chunk)
    else:
        await message.answer(text)

async def handle_key(message):
    try:
        chars = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789'
        suffix = ''.join(random.choices(chars, k=5))
        key = f'ADMIN_{suffix}'
        
        expires_at = datetime.now() + timedelta(hours=10)
        
        keys = load_json(FILES['keys'])
        keys[key] = {
            'created_at': datetime.now().isoformat(),
            'expires_at': expires_at.isoformat(),
            'active': True
        }
        save_json(FILES['keys'], keys)
        
        result = f"""🔑 Ключ доступа сгенерирован

━━━━━━━━━━━━━━━━━━━━━━━━━━
🔐 Ключ: <code>{key}</code>
⏱ Действует: 10 часов
🕐 До: {expires_at.strftime('%Y-%m-%d %H:%M:%S')}
━━━━━━━━━━━━━━━━━━━━━━━━━━
📌 Используйте этот ключ для входа в админ-панель сайта"""
        
        await message.answer(result)
        
    except Exception as e:
        await message.answer('❌ Ошибка при генерации ключа.')

async def handle_tex(message, args: List[str]):
    global maintenance_mode, maintenance_until
    
    if len(args) < 1:
        await message.answer('❌ Формат: .tex on (минуты) или .tex off')
        return
    
    sub_cmd = args[0].lower()
    
    if sub_cmd == 'on':
        if len(args) < 2:
            await message.answer('❌ Укажите время: .tex on 30')
            return
        
        try:
            minutes = int(args[1])
            if minutes <= 0:
                await message.answer('❌ Время должно быть больше 0.')
                return
            
            until = datetime.now() + timedelta(minutes=minutes)
            await set_tech_works(True, until.isoformat())
            maintenance_mode = True
            maintenance_until = until.isoformat()
            
            await message.answer(
                f'✅ ТЕХ-РАБОТЫ УСПЕШНО ВКЛЮЧЕНЫ\n'
                f'🕐 Время работ: до {until.strftime("%Y-%m-%d %H:%M:%S")}'
            )
        except ValueError:
            await message.answer('❌ Неверный формат времени. Укажите минуты.')
            
    elif sub_cmd == 'off':
        await set_tech_works(False, None)
        maintenance_mode = False
        maintenance_until = None
        await message.answer('✅ ТЕХ-РАБОТЫ УСПЕШНО ВЫКЛЮЧЕНЫ')
        
    else:
        await message.answer('❌ Используйте: .tex on (минуты) или .tex off')

# ─── ЗАПУСК ───

async def main():
    init_files()
    logger.info('🚀 Бот запускается...')
    logger.info(f'👑 Админ ID: {ADMIN_ID}')
    logger.info('📌 /команды — в личке с ботом')
    logger.info('📌 .команды — в чатах с собеседниками (Business API)')
    logger.info('📌 Пример: .whois ip 8.8.8.8')
    
    # Отправляем приветствие админу
    if ADMIN_ID:
        try:
            await bot.send_message(
                ADMIN_ID,
                f'🤖 БОТ ЗАПУЩЕН!\n\n'
                f'🕐 Время: {moscow_time()}\n'
                f'📌 Для проверки Business API:\n'
                f'1. Включи Secretary Mode у бота в @BotFather\n'
                f'2. Подключи бота в настройках Telegram\n'
                f'3. Отправь .help в чат с собеседником\n\n'
                f'Когда бот подключится к аккаунту, я пришлю уведомление!'
            )
            logger.info('📤 Приветствие отправлено админу')
        except Exception as e:
            logger.error(f'❌ Ошибка отправки приветствия: {e}')
    
    await dp.start_polling(bot)

if __name__ == '__main__':
    asyncio.run(main())
