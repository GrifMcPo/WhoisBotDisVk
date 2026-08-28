import asyncio
import os
import sys
import json
import logging
import re
import requests
import ipaddress
import phonenumbers
from phonenumbers import carrier, geocoder, timezone, number_type
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, BusinessConnection
from aiogram import F
from supabase import create_client, Client
import aiohttp
import random
import string
import hashlib
from textwrap import dedent
import html

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ===== СЕКРЕТЫ =====
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", 0))

# ===== SUPABASE =====
SUPABASE_URL = os.getenv("SUPABASE_URL", "https://doidpainkowqiquvrzpg.supabase.co")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "")

if not BOT_TOKEN:
    print("❌ Токен не найден!")
    sys.exit(1)

if not SUPABASE_KEY:
    print("❌ SUPABASE_SERVICE_KEY не найден в секретах!")
    sys.exit(1)

print("=" * 60)
print("✅ SUPABASE_SERVICE_KEY найден!")
print(f"🔗 Supabase URL: {SUPABASE_URL}")
print("=" * 60)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

business_connections = {}
blocked_notified = {}
processing_commands = {}
cloned_profiles = {}
copied_profiles = {}
muted_chats = {}  # chat_id -> True
antispam_settings = {}  # chat_id -> True
warn_settings = {}  # chat_id -> warn_count

# ===== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ =====

def get_msk_time():
    return (datetime.utcnow() + timedelta(hours=3)).strftime('%d.%m.%Y %H:%M:%S')

def get_msk_time_short():
    return (datetime.utcnow() + timedelta(hours=3)).strftime('%H:%M')

def get_msk_date_full():
    msk = datetime.utcnow() + timedelta(hours=3)
    days = ['понедельник', 'вторник', 'среда', 'четверг', 'пятница', 'суббота', 'воскресенье']
    months = ['января', 'февраля', 'марта', 'апреля', 'мая', 'июня', 'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря']
    return f"{days[msk.weekday()]}, {msk.day} {months[msk.month-1]} {msk.year} г."

def parse_time(time_str):
    if time_str == "-1w":
        return None, "НАВСЕГДА"
    
    total_minutes = 0
    hours = re.findall(r'(\d+)h', time_str)
    minutes = re.findall(r'(\d+)m', time_str)
    
    if hours:
        total_minutes += int(hours[0]) * 60
    if minutes:
        total_minutes += int(minutes[0])
    
    if total_minutes == 0:
        total_minutes = 60
    
    return total_minutes, f"{total_minutes} минут"

def format_response(title, content, centered=False):
    """Форматирует ответ бота: жирный заголовок, остальное цитатой"""
    result = f"**{title}**"
    if content:
        if centered:
            result += f"\n\n```\n{content}\n```"
        else:
            result += f"\n\n{content}"
    return result

def format_centered(text):
    """Центрирует текст в блоке"""
    return f"```\n{text}\n```"

def is_global_banned(user_id):
    """Проверяет глобальный бан (по IP/устройству)"""
    try:
        # В реальном приложении здесь проверка по IP/device_id
        # Пока просто проверяем есть ли запись в bans с флагом global
        result = supabase.table("bans").select("*").eq("user_id", int(user_id)).eq("is_global", True).execute()
        return len(result.data) > 0
    except:
        return False

# ========== SUPABASE ФУНКЦИИ ==========

async def save_log_async(log_entry):
    try:
        user_id = log_entry.get("user_id")
        username = log_entry.get("username", "Нет")
        full_name = log_entry.get("full_name", "Нет")
        
        existing = supabase.table("users").select("user_id").eq("user_id", user_id).execute()
        if not existing.data:
            supabase.table("users").insert({
                "user_id": user_id,
                "username": username,
                "full_name": full_name,
                "role": "user"
            }).execute()
        
        supabase.table("logs").insert({
            "user_id": user_id,
            "command": log_entry.get("command", ""),
            "target": log_entry.get("target", ""),
            "username": username,
            "time": log_entry.get("time", get_msk_time())
        }).execute()
        return True
    except Exception as e:
        print(f"❌ Ошибка сохранения лога: {e}")
        return False

def get_logs_for_user(user_id, count=10):
    try:
        result = supabase.table("logs").select("*").eq("user_id", user_id).order("id", desc=True).limit(count).execute()
        return result.data
    except:
        return []

def get_all_users():
    try:
        result = supabase.table("users").select("user_id, username, full_name, role").order("id", desc=True).execute()
        return result.data
    except:
        return []

def add_ban(user_id, reason, admin_id, time_minutes=None, is_global=False, silent=False):
    expires_at = None
    if time_minutes:
        expires_at = (datetime.now() + timedelta(minutes=time_minutes)).isoformat()
    
    try:
        supabase.table("bans").delete().eq("user_id", int(user_id)).execute()
        supabase.table("bans").insert({
            "user_id": int(user_id),
            "reason": reason,
            "added_by": admin_id,
            "added_at": get_msk_time(),
            "expires_at": expires_at,
            "is_global": is_global,
            "silent": silent
        }).execute()
        if str(user_id) in blocked_notified:
            del blocked_notified[str(user_id)]
        return True
    except:
        return False

def remove_ban(user_id, is_global=False):
    try:
        if is_global:
            supabase.table("bans").delete().eq("user_id", int(user_id)).eq("is_global", True).execute()
        else:
            supabase.table("bans").delete().eq("user_id", int(user_id)).execute()
        if str(user_id) in blocked_notified:
            del blocked_notified[str(user_id)]
        return True
    except:
        return False

def is_banned(user_id):
    try:
        result = supabase.table("bans").select("*").eq("user_id", int(user_id)).execute()
        if not result.data:
            return False
        
        data = result.data[0]
        if data.get("expires_at"):
            expires = datetime.fromisoformat(data["expires_at"])
            if datetime.now() > expires:
                supabase.table("bans").delete().eq("user_id", int(user_id)).execute()
                if str(user_id) in blocked_notified:
                    del blocked_notified[str(user_id)]
                return False
        return True
    except:
        return False

def get_ban_info(user_id):
    try:
        result = supabase.table("bans").select("*").eq("user_id", int(user_id)).execute()
        if not result.data:
            return None
        
        data = result.data[0]
        reason = data.get("reason", "Не указана")
        expires = data.get("expires_at")
        if expires:
            expires_dt = datetime.fromisoformat(expires)
            time_left = expires_dt - datetime.now()
            hours = time_left.seconds // 3600
            minutes = (time_left.seconds % 3600) // 60
            if time_left.days > 0:
                time_str = f"{time_left.days}д {hours}ч"
            elif hours > 0:
                time_str = f"{hours}ч {minutes}м"
            else:
                time_str = f"{minutes}м"
            reason += f" (осталось: {time_str})"
        else:
            reason += " (НАВСЕГДА)"
        
        return data
    except:
        return None

def load_keys():
    try:
        result = supabase.table("keys").select("*").execute()
        keys = {}
        for item in result.data:
            keys[item["key"]] = {
                "user_id": item["user_id"],
                "created_at": item["created_at"],
                "expires_at": item["expires_at"]
            }
        return keys
    except:
        return {}

def save_keys(keys):
    try:
        supabase.table("keys").delete().neq("id", 0).execute()
        for key, data in keys.items():
            supabase.table("keys").insert({
                "key": key,
                "user_id": data["user_id"],
                "created_at": data["created_at"],
                "expires_at": data["expires_at"]
            }).execute()
        return True
    except:
        return False

def generate_key():
    import string, secrets
    chars = string.ascii_uppercase + string.digits
    random_part = ''.join(secrets.choice(chars) for _ in range(5))
    return f"ADMIN_{random_part}"

def create_session_key():
    keys = load_keys()
    key = generate_key()
    keys[key] = {
        "user_id": ADMIN_ID,
        "created_at": get_msk_time(),
        "expires_at": (datetime.now() + timedelta(hours=10)).isoformat()
    }
    save_keys(keys)
    return key

def load_tech():
    try:
        result = supabase.table("tech").select("*").limit(1).execute()
        if result.data:
            return result.data[0]
        return {"active": False, "expires_at": None}
    except:
        return {"active": False, "expires_at": None}

def save_tech(data):
    try:
        supabase.table("tech").delete().neq("id", 0).execute()
        supabase.table("tech").insert({
            "active": data.get("active", False),
            "expires_at": data.get("expires_at")
        }).execute()
        return True
    except:
        return False

def is_tech_mode():
    data = load_tech()
    if data.get("active", False):
        expires = data.get("expires_at")
        if expires:
            try:
                expires_dt = datetime.fromisoformat(expires)
                if datetime.now() > expires_dt:
                    data["active"] = False
                    save_tech(data)
                    return False
            except:
                pass
        return True
    return False

def get_tech_info():
    return load_tech()

def set_tech_mode(active, expires_at=None):
    data = {"active": active, "expires_at": expires_at}
    save_tech(data)

def is_admin(user_id):
    try:
        result = supabase.table("users").select("role").eq("user_id", user_id).execute()
        if result.data and result.data[0].get("role") == "admin":
            return True
    except:
        pass
    return user_id == ADMIN_ID

# ========== НОВЫЕ ФУНКЦИИ ==========

# --- .mute / .unmute ---
muted_users = {}  # user_id -> True

def mute_user(user_id):
    muted_users[str(user_id)] = True

def unmute_user(user_id):
    muted_users.pop(str(user_id), None)

def is_muted(user_id):
    return str(user_id) in muted_users

# --- .nomute / .unnomute ---
nomute_users = {}  # user_id -> True

def set_nomute(user_id):
    nomute_users[str(user_id)] = True

def unset_nomute(user_id):
    nomute_users.pop(str(user_id), None)

def has_nomute(user_id):
    return str(user_id) in nomute_users

# --- .warn ---
warn_counts = {}  # user_id -> count

def add_warn(user_id):
    warn_counts[str(user_id)] = warn_counts.get(str(user_id), 0) + 1
    return warn_counts[str(user_id)]

def reset_warn(user_id):
    warn_counts.pop(str(user_id), None)

def get_warn_count(user_id):
    return warn_counts.get(str(user_id), 0)

# --- .antispam ---
antispam_enabled = {}  # chat_id -> True

def toggle_antispam(chat_id):
    antispam_enabled[str(chat_id)] = not antispam_enabled.get(str(chat_id), False)
    return antispam_enabled[str(chat_id)]

def is_antispam_enabled(chat_id):
    return antispam_enabled.get(str(chat_id), False)

# --- .gift (Stars) ---
async def send_stars(user_id, amount):
    try:
        # Отправка звезд через Telegram API
        # В реальном приложении используйте метод createInvoiceLink
        return True
    except:
        return False

# --- .proxies ---
def get_proxies():
    try:
        url = "https://api.proxyscrape.com/v2/?request=displayproxies&protocol=http&timeout=10000&country=all&ssl=all&anonymity=all"
        response = requests.get(url, timeout=10)
        if response.status_code == 200:
            proxies = response.text.strip().split('\n')
            return proxies[:20]
        return ["Не удалось получить прокси"]
    except:
        return ["Ошибка получения прокси"]

# --- .tempmail ---
tempmail_inboxes = {}  # email -> messages

async def create_tempmail():
    try:
        # Используем API tempmail
        response = requests.get("https://api.temp-mail.org/request/domains/format/json")
        if response.status_code == 200:
            domains = response.json()
            domain = domains[0] if domains else "temp-mail.org"
            random_part = ''.join(random.choices(string.ascii_lowercase + string.digits, k=10))
            email = f"{random_part}@{domain}"
            tempmail_inboxes[email] = []
            return email
        return "Ошибка создания почты"
    except:
        return "Ошибка создания почты"

async def get_tempmail_messages(email):
    try:
        # Получаем сообщения
        response = requests.get(f"https://api.temp-mail.org/request/mail/id/{email}/format/json")
        if response.status_code == 200:
            messages = response.json()
            return messages
        return []
    except:
        return []

# --- .leaks ---
async def check_leaks(query):
    try:
        # Проверка по базам утечек
        # Используем haveibeenpwned API
        response = requests.get(f"https://haveibeenpwned.com/api/v3/breachedaccount/{query}")
        if response.status_code == 200:
            breaches = response.json()
            return f"Найдено утечек: {len(breaches)}"
        elif response.status_code == 404:
            return "Утечек не найдено"
        return "Ошибка проверки"
    except:
        return "Ошибка проверки"

# --- .export ---
async def export_chat(chat_id, user_id):
    try:
        # Получаем историю чата
        # В реальном приложении нужно собирать сообщения из истории
        messages = [
            {"user": "user1", "text": "Привет!", "time": "12:00"},
            {"user": "user2", "text": "Здравствуй!", "time": "12:01"},
        ]
        
        # Формируем файл
        content = "===== ЭКСПОРТ ПЕРЕПИСКИ =====\n"
        content += f"Дата: {get_msk_date_full()}\n"
        content += "=" * 40 + "\n\n"
        
        for msg in messages:
            content += f"[{msg['time']}] ({msg['user']}): {msg['text']}\n"
        
        # Сохраняем в файл
        filename = f"chat_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        with open(filename, 'w', encoding='utf-8') as f:
            f.write(content)
        
        return filename
    except:
        return None

# ========== АНИМАЦИИ ==========

ANIMATIONS = {
    'send': ['📝 Создаю чек...', '✅ Чек готов!', '💰 Сумма: 1000 RUB'],
    'xrocket': ['🚀 Создаю чек xRocket...', '✅ Готово!', '💵 USDT: 500'],
    'dox': ['⚠️ СБОР ДАННЫХ...', '📡 Анализ...', '👤 ДАННЫЕ ПОЛУЧЕНЫ!'],
    'snos': ['☢️ АКТИВАЦИЯ...', '🔋 ЗАРЯД 100%', '💥 ОБЪЕКТ УНИЧТОЖЕН'],
    'hack': ['💻 ВЗЛОМ...', '🔓 ДОСТУП ПОЛУЧЕН', '🟢 СИСТЕМА ВЗЛОМАНА'],
    'ddos': ['🌐 DDOS АТАКА...', '📡 ОТПРАВКА ПАКЕТОВ', '⚠️ СЕРВЕР НЕ ОТВЕЧАЕТ'],
    'ban': ['🔨 БАН...', '⛔ ПОЛЬЗОВАТЕЛЬ ЗАБАНЕН'],
    'love': ['💕', '❤️', '💖', '💗', '❤️', '💕'],
    'ghoul': ['1000-7', '993', '986', '979', '972', '965', '958', '951', '944', '937', '930', '923', '916', '909', '902', '895']
}

async def run_animation(chat_id, animation_name, connection_id=None):
    anim = ANIMATIONS.get(animation_name, ['🔄'])
    for stage in anim:
        if connection_id:
            await send_to_business_chat(chat_id, stage, connection_id)
        else:
            await bot.send_message(chat_id, stage)
        await asyncio.sleep(0.5)
    return True

# ========== БИЗНЕС API ==========

async def delete_business_message(chat_id: int, message_id: int, connection_id: str):
    try:
        url = f'https://api.telegram.org/bot{BOT_TOKEN}/deleteBusinessMessages'
        payload = {
            "business_connection_id": connection_id,
            "message_ids": [message_id]
        }
        response = requests.post(url, json=payload, timeout=10)
        return response.json().get('ok', False)
    except:
        return False

async def send_to_business_chat(chat_id: int, text: str, connection_id: str, reply_markup=None):
    try:
        return await bot.send_message(
            chat_id=chat_id,
            text=text,
            business_connection_id=connection_id,
            reply_markup=reply_markup
        )
    except Exception as e:
        logger.error(f"❌ Ошибка отправки: {e}")
        return None

async def edit_business_message(chat_id: int, message_id: int, text: str, connection_id: str):
    try:
        url = f'https://api.telegram.org/bot{BOT_TOKEN}/editMessageText'
        payload = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
            "business_connection_id": connection_id
        }
        response = requests.post(url, json=payload, timeout=10)
        return response.json().get('ok', False)
    except:
        return False

async def edit_normal_message(chat_id: int, message_id: int, text: str):
    try:
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=text
        )
        return True
    except:
        return False

# ========== КЛАВИАТУРЫ ==========

def get_inf_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📋 Основные", callback_data="inf_main")],
        [InlineKeyboardButton(text="🔍 Пробивы", callback_data="inf_probe")],
        [InlineKeyboardButton(text="🛠️ Админ", callback_data="inf_admin")],
        [InlineKeyboardButton(text="🌟 Развлекательные", callback_data="inf_fun")],
        [InlineKeyboardButton(text="🛠️ Утилиты", callback_data="inf_utils")],
        [InlineKeyboardButton(text="📚 Все команды", callback_data="inf_all")]
    ])

def get_fun_keyboard(page=1):
    if page == 1:
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Назад в меню", callback_data="show_inf")],
            [InlineKeyboardButton(text="➡️ Еще", callback_data="fun_page_2")],
            [InlineKeyboardButton(text="❌ Закрыть меню", callback_data="close_menu")]
        ])
    else:
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="fun_page_1")],
            [InlineKeyboardButton(text="📋 Меню", callback_data="show_inf")],
            [InlineKeyboardButton(text="❌ Закрыть меню", callback_data="close_menu")]
        ])

def get_utils_keyboard(page=1):
    if page == 1:
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Назад в меню", callback_data="show_inf")],
            [InlineKeyboardButton(text="➡️ Еще", callback_data="utils_page_2")],
            [InlineKeyboardButton(text="❌ Закрыть меню", callback_data="close_menu")]
        ])
    else:
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="utils_page_1")],
            [InlineKeyboardButton(text="📋 Меню", callback_data="show_inf")],
            [InlineKeyboardButton(text="❌ Закрыть меню", callback_data="close_menu")]
        ])

def get_main_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🌐 ПРОБИВ IP", callback_data="probe_ip")],
        [InlineKeyboardButton(text="📱 ПРОБИВ НОМЕРА", callback_data="probe_phone")],
        [InlineKeyboardButton(text="👤 ПРОБИВ ЮЗЕРА", callback_data="probe_user")],
        [InlineKeyboardButton(text="📋 Меню команд", callback_data="show_inf")]
    ])

# ========== BUSINESS CONNECTION ==========

@dp.business_connection()
async def handle_business_connection(connection: BusinessConnection):
    if connection.user:
        user_id = connection.user.id
        connection_id = connection.id
        username = connection.user.username or "Нет юзернейма"
        
        if not is_admin(user_id):
            return
        
        business_connections[str(user_id)] = connection_id
        
        logger.info(f"🔗 BUSINESS CONNECTION: @{username} (ID: {user_id})")
        
        # Отправляем приветственное сообщение
        await bot.send_message(
            chat_id=user_id,
            text=f"✅ БОТ ПОДКЛЮЧЕН К БИЗНЕС-АККАУНТУ!\n\n🆔 ID: {user_id}\n📌 Команды работают в чатах с собеседниками!\n🔥 Введите .inf для списка команд"
        )

# ========== BUSINESS MESSAGE ==========

@dp.business_message()
async def handle_business_message(message: types.Message):
    try:
        user_id = message.from_user.id
        chat_id = message.chat.id
        message_id = message.message_id
        connection_id = message.business_connection_id
        
        if not is_admin(user_id):
            return
        
        if not connection_id:
            connection_id = business_connections.get(str(user_id))
        
        # Проверка бана
        if is_banned(user_id):
            if str(user_id) not in blocked_notified:
                ban_info = get_ban_info(user_id)
                reason = ban_info.get("reason", "Не указана") if ban_info else "Не указана"
                await send_to_business_chat(
                    chat_id,
                    f"⛔ ВАС ЗАБЛОКИРОВАЛИ В БОТЕ!\n\n📌 Причина: {reason}",
                    connection_id
                )
                blocked_notified[str(user_id)] = True
            return
        
        # Проверка глобального бана
        if is_global_banned(user_id):
            await send_to_business_chat(
                chat_id,
                "⛔ ВАС ЗАБЛОКИРОВАЛИ ГЛОБАЛЬНО!\n\nОбратитесь к администратору.",
                connection_id
            )
            return
        
        # Проверка техработ
        if is_tech_mode():
            tech_info = get_tech_info()
            await send_to_business_chat(
                chat_id,
                f"🛠️ БОТ НА ТЕХНИЧЕСКИХ РАБОТАХ\n\n🕐 ВРЕМЯ: {tech_info.get('expires_at', 'Неизвестно')}",
                connection_id
            )
            return
        
        if not message.text:
            return
        
        text = message.text.strip()
        
        if not text.startswith('.'):
            return
        
        await delete_business_message(chat_id, message_id, connection_id)
        
        # ===== ОБРАБОТКА КОМАНД =====
        
        # .inf - главное меню
        if text.lower() == '.inf':
            await send_to_business_chat(
                chat_id,
                "**Добро пожаловать в RipSave 👀**\n\n"
                "Максимум возможностей — минимум лишних действий 🌟\n"
                "В этом разделе вы можете ознакомиться с функционалом бота, узнать о доступных инструментах и открыть подробную информацию о каждой возможности.",
                connection_id,
                reply_markup=get_inf_keyboard()
            )
            return
        
        # .help (старый)
        if text.lower() == '.help':
            await send_to_business_chat(chat_id, HELP_TEXT, connection_id)
            return
        
        # ===== ОСНОВНЫЕ КОМАНДЫ =====
        
        # .me
        if text.lower() == '.me':
            user = message.from_user
            await send_to_business_chat(
                chat_id,
                format_response(
                    "👤 Ваш профиль",
                    f"🆔 ID: {user.id}\n"
                    f"👤 Ник: @{user.username or 'Нет'}\n"
                    f"📛 Роль: {'admin' if is_admin(user.id) else 'user'}"
                ),
                connection_id
            )
            return
        
        # .id
        if text.lower() == '.id':
            await send_to_business_chat(
                chat_id,
                format_response(
                    "🆔 Идентификаторы",
                    f"Ваш Telegram ID: {message.from_user.id}\n"
                    f"ID чата: {chat_id}"
                ),
                connection_id
            )
            return
        
        # .chat
        if text.lower() == '.chat':
            chat = message.chat
            await send_to_business_chat(
                chat_id,
                format_response(
                    "💬 Информация о чате",
                    f"🆔 ID: {chat.id}\n"
                    f"📛 Название: {chat.title or 'личный чат'}"
                ),
                connection_id
            )
            return
        
        # .business
        if text.lower() == '.business':
            await send_to_business_chat(
                chat_id,
                format_response(
                    "🔗 Business connection",
                    f"{connection_id or 'Не активно'}"
                ),
                connection_id
            )
            return
        
        # .ping
        if text.lower() == '.ping':
            start = datetime.now()
            await send_to_business_chat(chat_id, "🏓 **Pong**", connection_id)
            end = datetime.now()
            ping_ms = (end - start).microseconds / 1000
            await send_to_business_chat(
                chat_id,
                f"**🏓 Pong**\n\n"
                f"API: {ping_ms:.0f} ms\n"
                f"TCP: {ping_ms * 0.5:.0f} ms\n"
                f"Bot: @{bot.username}\n"
                f"Status: ✅ online",
                connection_id
            )
            return
        
        # .time
        if text.lower() == '.time':
            await send_to_business_chat(
                chat_id,
                format_response(
                    "🕒 Текущее время (МСК)",
                    f"**[{get_msk_time_short()}]**"
                ),
                connection_id
            )
            return
        
        # .date
        if text.lower() == '.date':
            await send_to_business_chat(
                chat_id,
                format_response(
                    "📅 Текущая дата",
                    f"{get_msk_date_full()}\n"
                    f"TZ: Europe/Moscow"
                ),
                connection_id
            )
            return
        
        # .ping
        if text.lower() == '.ping':
            start = datetime.now()
            await send_to_business_chat(chat_id, "🏓 **Pong**", connection_id)
            end = datetime.now()
            ping_ms = (end - start).microseconds / 1000
            await send_to_business_chat(
                chat_id,
                f"**🏓 Pong**\n\n"
                f"API: {ping_ms:.0f} ms\n"
                f"TCP: {ping_ms * 0.5:.0f} ms\n"
                f"Bot: @RipSave_bot\n"
                f"Status: ✅ online",
                connection_id
            )
            return
        
        # .time
        if text.lower() == '.time':
            await send_to_business_chat(
                chat_id,
                format_response(
                    "🕒 Текущее время (МСК)",
                    f"**[{get_msk_time_short()}]**"
                ),
                connection_id
            )
            return
        
        # .date
        if text.lower() == '.date':
            await send_to_business_chat(
                chat_id,
                format_response(
                    "📅 Текущая дата",
                    f"{get_msk_date_full()}\n"
                    f"TZ: Europe/Moscow"
                ),
                connection_id
            )
            return
        
        # .cat
        if text.lower() == '.cat':
            cat_urls = [
                "https://cataas.com/cat",
                "https://cataas.com/cat?type=square",
                "https://cataas.com/cat?type=square&position=center"
            ]
            url = random.choice(cat_urls)
            try:
                await bot.send_photo(chat_id, url, business_connection_id=connection_id, caption="🐱 Ваш случайный кот")
            except:
                await send_to_business_chat(chat_id, f"🐱 Кот: {url}", connection_id)
            return
        
        # .status
        if text.lower() == '.status' and message.reply_to_message:
            reply = message.reply_to_message
            target_user_id = reply.from_user.id
            
            logs = get_logs_for_user(target_user_id, 20)
            log_count = len(logs)
            last_commands = "\n".join([f"• {log.get('command', '?')} ({log.get('time', '?')})" for log in logs[:5]]) or "Нет команд"
            
            await send_to_business_chat(
                chat_id,
                format_response(
                    "📊 Статистика пользователя",
                    f"👤 {reply.from_user.full_name} (@{reply.from_user.username or 'Нет'})\n"
                    f"🆔 ID: {target_user_id}\n"
                    f"📝 Всего команд: {log_count}\n\n"
                    f"🕐 Последние команды:\n{last_commands}"
                ),
                connection_id
            )
            return
        
        # .clone / .unclone
        if text.lower() == '.clone':
            cloned_chats[str(chat_id)] = True
            await send_to_business_chat(chat_id, "✅ Клонирование сообщений ВКЛЮЧЕНО", connection_id)
            return
        
        if text.lower() == '.unclone':
            cloned_chats.pop(str(chat_id), None)
            await send_to_business_chat(chat_id, "✅ Клонирование сообщений ВЫКЛЮЧЕНО", connection_id)
            return
        
        # .copyp / .uncopyp
        if text.lower() == '.copyp' and message.reply_to_message:
            target = message.reply_to_message.from_user
            copied_profiles[str(user_id)] = {
                "username": target.username,
                "full_name": target.full_name
            }
            await send_to_business_chat(
                chat_id,
                f"✅ Профиль скопирован!\n\n"
                f"📛 Имя: {target.full_name}\n"
                f"👤 Username: @{target.username or 'Нет'}\n"
                f"🆔 ID: {target.id}\n\n"
                f"Используй .uncopyp чтобы вернуть свой профиль",
                connection_id
            )
            return
        
        if text.lower() == '.uncopyp':
            copied_profiles.pop(str(user_id), None)
            await send_to_business_chat(chat_id, "✅ Ваш профиль восстановлен", connection_id)
            return
        
        # ===== РАЗВЛЕКАТЕЛЬНЫЕ КОМАНДЫ =====
        
        # .send - фейковый чек
        if text.lower() == '.send':
            await send_to_business_chat(chat_id, "📝 Создаю чек...", connection_id)
            await asyncio.sleep(0.5)
            await send_to_business_chat(chat_id, "✅ Чек готов!\n\n💰 Сумма: 1000 RUB", connection_id)
            return
        
        # .xrocket
        if text.lower().startswith('.xrocket'):
            parts = text.split()
            amount = parts[1] if len(parts) > 1 else "100"
            await send_to_business_chat(chat_id, f"🚀 Создаю чек xRocket на {amount} USDT...", connection_id)
            await asyncio.sleep(0.5)
            await send_to_business_chat(chat_id, f"✅ Готово!\n\n💵 USDT: {amount}", connection_id)
            return
        
        # .dox
        if text.lower() == '.dox':
            await run_animation(chat_id, 'dox', connection_id)
            return
        
        # .snos
        if text.lower() == '.snos':
            await run_animation(chat_id, 'snos', connection_id)
            return
        
        # .hack
        if text.lower() == '.hack':
            await run_animation(chat_id, 'hack', connection_id)
            return
        
        # .ddos
        if text.lower() == '.ddos':
            await run_animation(chat_id, 'ddos', connection_id)
            return
        
        # .ban_anim (пугающая анимация бана)
        if text.lower() == '.ban':
            await run_animation(chat_id, 'ban', connection_id)
            return
        
        # .spam
        if text.lower().startswith('.spam'):
            parts = text.split(maxsplit=2)
            if len(parts) < 3:
                await send_to_business_chat(chat_id, "❌ .spam [количество] [текст]", connection_id)
                return
            try:
                count = int(parts[1])
                spam_text = parts[2]
                for i in range(min(count, 20)):
                    await send_to_business_chat(chat_id, spam_text, connection_id)
                    await asyncio.sleep(0.2)
            except:
                await send_to_business_chat(chat_id, "❌ Неверный формат", connection_id)
            return
        
        # .love
        if text.lower() == '.love':
            await run_animation(chat_id, 'love', connection_id)
            return
        
        # .ghoul
        if text.lower() == '.ghoul':
            await run_animation(chat_id, 'ghoul', connection_id)
            return
        
        # .arg - анимация появления текста
        if text.lower().startswith('.arg '):
            arg_text = text[5:]
            chars = list(arg_text)
            result = ""
            for char in chars:
                result += char
                await send_to_business_chat(chat_id, f"```\n{result}\n```", connection_id)
                await asyncio.sleep(0.05)
            return
        
        # .scrl - анимация прокрутки
        if text.lower().startswith('.scrl '):
            scrl_text = text[6:]
            for i in range(len(scrl_text)):
                await send_to_business_chat(chat_id, f"```\n{scrl_text[:i+1]}\n```", connection_id)
                await asyncio.sleep(0.05)
            return
        
        # .print - эффект печати
        if text.lower().startswith('.print '):
            print_text = text[7:]
            for char in print_text:
                await send_to_business_chat(chat_id, char, connection_id)
                await asyncio.sleep(0.03)
            return
        
        # .glit - глитч эффект
        if text.lower().startswith('.glit '):
            glit_text = text[6:]
            import random as rnd
            for _ in range(5):
                glitched = ''.join([c if rnd.random() > 0.3 else rnd.choice(string.ascii_letters + string.digits) for c in glit_text])
                await send_to_business_chat(chat_id, f"```\n{glitched}\n```", connection_id)
                await asyncio.sleep(0.1)
            await send_to_business_chat(chat_id, f"```\n{glit_text}\n```", connection_id)
            return
        
        # .stairs - каждое слово отдельным сообщением
        if text.lower().startswith('.stairs '):
            words = text[8:].split()
            for word in words:
                await send_to_business_chat(chat_id, word, connection_id)
                await asyncio.sleep(0.2)
            return
        
        # .wave - нарастающая лесенка
        if text.lower().startswith('.wave '):
            wave_text = text[6:]
            for i in range(1, len(wave_text) + 1):
                await send_to_business_chat(chat_id, f"```\n{' ' * (len(wave_text)-i)}{wave_text[:i]}\n```", connection_id)
                await asyncio.sleep(0.1)
            return
        
        # .letters - каждая буква отдельным сообщением
        if text.lower().startswith('.letters '):
            letters_text = text[9:]
            for char in letters_text:
                await send_to_business_chat(chat_id, char, connection_id)
                await asyncio.sleep(0.1)
            return
        
        # .random
        if text.lower().startswith('.random '):
            parts = text.split()
            if len(parts) >= 3:
                try:
                    min_val = int(parts[1])
                    max_val = int(parts[2])
                    result = random.randint(min_val, max_val)
                    await send_to_business_chat(chat_id, f"🎲 Случайное число: **{result}**", connection_id)
                except:
                    await send_to_business_chat(chat_id, "❌ .random [мин] [макс]", connection_id)
            return
        
        # .flip
        if text.lower() == '.flip':
            result = random.choice(['Орёл', 'Решка'])
            await send_to_business_chat(chat_id, f"🪙 **{result}**!", connection_id)
            return
        
        # .coin
        if text.lower() == '.coin':
            result = random.choice(['Орёл', 'Решка'])
            await send_to_business_chat(chat_id, f"🪙 **{result}**!", connection_id)
            return
        
        # .magic8
        if text.lower().startswith('.magic8 '):
            answers = [
                "Да", "Нет", "Возможно", "Спроси позже",
                "Определённо да", "Определённо нет", "Всё зависит от тебя",
                "Шансы хорошие", "Шансы невелики", "Пока неясно"
            ]
            result = random.choice(answers)
            await send_to_business_chat(chat_id, f"🎱 **{result}**", connection_id)
            return
        
        # .quote
        if text.lower() == '.quote':
            quotes = [
                "Жизнь — это то, что с тобой происходит, пока ты строишь планы.",
                "Не бойся медленно двигаться — бойся стоять на месте.",
                "Успех — это умение двигаться от неудачи к неудаче, не теряя энтузиазма.",
                "Единственный способ сделать что-то отлично — любить то, что ты делаешь.",
                "Не важно, насколько медленно ты идёшь, главное — не останавливаться."
            ]
            quote = random.choice(quotes)
            await send_to_business_chat(chat_id, f"💡 *{quote}*", connection_id)
            return
        
        # .joke
        if text.lower() == '.joke':
            jokes = [
                "Почему программисты не путают Хэллоуин и Рождество? Потому что 31 окт = 25 дек.",
                "Сколько программистов нужно, чтобы поменять лампочку? Ни одного, это аппаратная проблема.",
                "В чём разница между программистом и обычным человеком? Программист видит баги, а обычный человек — особенности."
            ]
            joke = random.choice(jokes)
            await send_to_business_chat(chat_id, f"😂 {joke}", connection_id)
            return
        
        # .rps
        if text.lower().startswith('.rps '):
            choice = text[5:].lower()
            options = ['камень', 'ножницы', 'бумага']
            if choice not in options:
                await send_to_business_chat(chat_id, "❌ Выбери: камень, ножницы или бумага", connection_id)
                return
            bot_choice = random.choice(options)
            await send_to_business_chat(
                chat_id,
                f"🤖 Мой выбор: **{bot_choice}**\n\n"
                f"Твой выбор: **{choice}**",
                connection_id
            )
            return
        
        # .slot
        if text.lower() == '.slot':
            symbols = ['🍒', '🍋', '🍊', '🍇', '💎', '7️⃣']
            result = [random.choice(symbols) for _ in range(3)]
            is_win = len(set(result)) == 1
            await send_to_business_chat(
                chat_id,
                f"🎰 **{result[0]} {result[1]} {result[2]}**\n\n"
                f"{'🎉 ПОБЕДА!' if is_win else '😔 Попробуй ещё'}",
                connection_id
            )
            return
        
        # .choose
        if text.lower().startswith('.choose '):
            choices = text[8:].split('|')
            if len(choices) < 2:
                await send_to_business_chat(chat_id, "❌ .choose [вариант1 | вариант2 | ...]", connection_id)
                return
            choice = random.choice(choices).strip()
            await send_to_business_chat(chat_id, f"🎯 Я выбираю: **{choice}**", connection_id)
            return
        
        # .luck
        if text.lower() == '.luck':
            luck = random.randint(0, 100)
            await send_to_business_chat(chat_id, f"🍀 Ваша удача: **{luck}%**", connection_id)
            return
        
        # .fate
        if text.lower() == '.fate':
            fates = [
                "📜 Сегодня тебя ждёт удача!",
                "📜 Будь осторожен в решениях.",
                "📜 Жди приятного сюрприза.",
                "📜 Твой день будет полон неожиданностей.",
                "📜 Звёзды говорят: действуй!"
            ]
            await send_to_business_chat(chat_id, random.choice(fates), connection_id)
            return
        
        # .meme
        if text.lower() == '.meme':
            await send_to_business_chat(chat_id, "🖼️ Скоро здесь будут мемы!", connection_id)
            return
        
        # .memer
        if text.lower().startswith('.memer '):
            meme_text = text[7:]
            await send_to_business_chat(chat_id, f"🖼️ Мем с текстом: {meme_text}\n\n(Функция в разработке)", connection_id)
            return
        
        # ===== УТИЛИТЫ =====
        
        # .password
        if text.lower().startswith('.password '):
            try:
                length = int(text[10:])
                chars = string.ascii_letters + string.digits + "!@#$%^&*"
                password = ''.join(random.choices(chars, k=min(length, 50)))
                await send_to_business_chat(chat_id, f"🔑 Пароль:\n`{password}`", connection_id)
            except:
                await send_to_business_chat(chat_id, "❌ .password [длина]", connection_id)
            return
        
        # .nickname
        if text.lower() == '.nickname':
            prefixes = ['Cool', 'Super', 'Mega', 'Ultra', 'Pro', 'Shadow', 'Dark', 'Light']
            suffixes = ['Cat', 'Dog', 'Wolf', 'Fox', 'Bear', 'Hawk', 'Dragon', 'Phoenix']
            nickname = f"{random.choice(prefixes)}{random.choice(suffixes)}{random.randint(10, 99)}"
            await send_to_business_chat(chat_id, f"👤 Случайный ник:\n`{nickname}`", connection_id)
            return
        
        # .upper / .lower / .title
        if text.lower().startswith('.upper '):
            await send_to_business_chat(chat_id, text[7:].upper(), connection_id)
            return
        
        if text.lower().startswith('.lower '):
            await send_to_business_chat(chat_id, text[7:].lower(), connection_id)
            return
        
        if text.lower().startswith('.title '):
            await send_to_business_chat(chat_id, text[7:].title(), connection_id)
            return
        
        # .reverse
        if text.lower().startswith('.reverse '):
            await send_to_business_chat(chat_id, text[9:][::-1], connection_id)
            return
        
        # .leet
        if text.lower().startswith('.leet '):
            leet_map = {'a': '4', 'e': '3', 'i': '1', 'o': '0', 's': '5', 't': '7'}
            leet_text = ''.join([leet_map.get(c.lower(), c) for c in text[6:]])
            await send_to_business_chat(chat_id, f"`{leet_text}`", connection_id)
            return
        
        # .zalgo
        if text.lower().startswith('.zalgo '):
            zalgo_chars = ['̖', '̗', '̘', '̙', '̜', '̝', '̞', '̟', '̠', '̤', '̥', '̦', '̩', '̪', '̫', '̬']
            zalgo_text = ''.join([c + ''.join(random.choices(zalgo_chars, k=random.randint(1, 3))) for c in text[7:]])
            await send_to_business_chat(chat_id, f"`{zalgo_text}`", connection_id)
            return
        
        # .mute
        if text.lower().startswith('.mute '):
            try:
                minutes = int(text[6:])
                mute_user(chat_id)
                await send_to_business_chat(
                    chat_id,
                    f"🔇 Чат замучен на {minutes} минут",
                    connection_id
                )
                # Автоматический размут через N минут
                asyncio.create_task(auto_unmute(chat_id, minutes))
            except:
                await send_to_business_chat(chat_id, "❌ .mute [N]", connection_id)
            return
        
        # .unmute
        if text.lower() == '.unmute':
            unmute_user(chat_id)
            await send_to_business_chat(chat_id, "🔊 Чат размучен", connection_id)
            return
        
        # .nomute
        if text.lower() == '.nomute':
            set_nomute(user_id)
            await send_to_business_chat(chat_id, "✅ Обход мута включён", connection_id)
            return
        
        # .unnomute
        if text.lower() == '.unnomute':
            unset_nomute(user_id)
            await send_to_business_chat(chat_id, "✅ Обход мута выключён", connection_id)
            return
        
        # .warn
        if text.lower().startswith('.warn '):
            try:
                count = int(text[6:])
                warn_counts[str(chat_id)] = count
                await send_to_business_chat(
                    chat_id,
                    f"⚠️ Установлен авто-мут после {count} сообщений",
                    connection_id
                )
            except:
                await send_to_business_chat(chat_id, "❌ .warn [N]", connection_id)
            return
        
        # .unwarn
        if text.lower() == '.unwarn':
            warn_counts.pop(str(chat_id), None)
            await send_to_business_chat(chat_id, "✅ Авто-мут отключён", connection_id)
            return
        
        # .antispam
        if text.lower() == '.antispam on':
            antispam_enabled[str(chat_id)] = True
            await send_to_business_chat(chat_id, "✅ Антиспам включён", connection_id)
            return
        
        if text.lower() == '.antispam off':
            antispam_enabled[str(chat_id)] = False
            await send_to_business_chat(chat_id, "✅ Антиспам выключён", connection_id)
            return
        
        # .gift
        if text.lower() == '.gift':
            await send_to_business_chat(
                chat_id,
                "🎁 Отправка подарков за Stars\n\n"
                "Функция в разработке",
                connection_id
            )
            return
        
        # .proxies
        if text.lower() == '.proxies':
            proxies = get_proxies()
            result = "🌐 Свежие HTTP-прокси:\n\n" + "\n".join(proxies[:10])
            await send_to_business_chat(chat_id, result, connection_id)
            return
        
        # .tempmail
        if text.lower() == '.tempmail':
            email = await create_tempmail()
            await send_to_business_chat(
                chat_id,
                f"📧 Временная почта создана:\n`{email}`\n\n"
                f"Используйте .inbox чтобы проверить почту",
                connection_id
            )
            return
        
        # .scam
        if text.lower().startswith('.scam '):
            scam_text = text[6:]
            # Простая проверка на фишинг
            suspicious = ['http', 'bit.ly', 't.me', 'ххх', 'бесплатно', 'выиграл']
            is_scam = any(word in scam_text.lower() for word in suspicious)
            await send_to_business_chat(
                chat_id,
                f"🔍 Проверка на скам/фишинг\n\n"
                f"Текст: {scam_text}\n"
                f"Статус: {'⚠️ ПОДОЗРИТЕЛЬНО' if is_scam else '✅ БЕЗОПАСНО'}",
                connection_id
            )
            return
        
        # .export
        if text.lower() == '.export':
            # Анимация экспорта
            await send_to_business_chat(chat_id, "📋 Копирую переписку...", connection_id)
            await asyncio.sleep(0.5)
            await send_to_business_chat(chat_id, "📄 Переписка скопирована, создаю файл переписки...", connection_id)
            await asyncio.sleep(0.5)
            await send_to_business_chat(chat_id, "📁 Файл готов, отправляю в чат...", connection_id)
            await asyncio.sleep(0.5)
            
            filename = await export_chat(chat_id, user_id)
            if filename:
                await send_to_business_chat(chat_id, f"✅ Файл создан: {filename}", connection_id)
            else:
                await send_to_business_chat(chat_id, "❌ Ошибка экспорта", connection_id)
            return
        
        # .ban (админ-команда с флагами)
        if text.lower().startswith('.ban '):
            parts = text.split()
            if len(parts) < 4:
                await send_to_business_chat(
                    chat_id,
                    "❌ .ban [ID] [время] [причина] [-s] [-g]\n"
                    "Пример: .ban 123456 1h Спам -s -g",
                    connection_id
                )
                return
            
            target_id = parts[1]
            time_str = parts[2]
            reason = " ".join(parts[3:])
            
            # Проверяем флаги
            is_global = '-g' in parts
            silent = '-s' in parts
            reason = re.sub(r'\s*-[gs]\s*', ' ', reason).strip()
            
            minutes, time_display = parse_time(time_str)
            
            if add_ban(target_id, reason, user_id, minutes, is_global, silent):
                # Если silent - удаляем сообщение и отправляем в ЛС
                if silent:
                    await delete_business_message(chat_id, message_id, connection_id)
                    await bot.send_message(
                        user_id,
                        f"✅ Пользователь успешно заблокирован!\n\n"
                        f"🆔 ID: {target_id}\n"
                        f"📌 Reason: {reason}\n"
                        f"🖥️ Server: {'глобальный' if is_global else 'локальный'}"
                    )
                else:
                    await send_to_business_chat(
                        chat_id,
                        f"**✅ Пользователь успешно заблокирован!**\n\n"
                        f"🆔 ID: {target_id}\n"
                        f"📌 Reason: {reason}\n"
                        f"🖥️ Server: {'глобальный' if is_global else 'локальный'}",
                        connection_id
                    )
                
                # Уведомляем забаненного
                try:
                    ban_msg = f"**Вас заблокировали в боте!**\n\n"
                    ban_msg += f"📌 Reason: {reason}\n"
                    ban_msg += f"⏱ Time: {time_display}\n"
                    ban_msg += f"🖥️ Server: {'глобальный' if is_global else 'локальный'}"
                    if not minutes:
                        ban_msg += "\n\n⚠️ Блокировка выдана навсегда"
                    await bot.send_message(chat_id=int(target_id), text=ban_msg)
                except:
                    pass
                
                await save_log_async({
                    "command": f".ban {target_id}",
                    "user_id": user_id,
                    "username": message.from_user.username or "Нет",
                    "target": target_id,
                    "reason": reason,
                    "time": get_msk_time()
                })
            return
        
        # .unban
        if text.lower().startswith('.unban '):
            parts = text.split()
            if len(parts) < 2:
                await send_to_business_chat(chat_id, "❌ .unban [ID] [причина] [-g]", connection_id)
                return
            
            target_id = parts[1]
            is_global = '-g' in parts
            reason = " ".join(parts[2:])
            reason = re.sub(r'\s*-g\s*', ' ', reason).strip()
            
            if remove_ban(target_id, is_global):
                await send_to_business_chat(
                    chat_id,
                    f"**✅ Пользователь разбанен!**\n\n"
                    f"🆔 ID: {target_id}\n"
                    f"📌 Reason: {reason}\n"
                    f"🖥️ Server: {'глобальный' if is_global else 'локальный'}",
                    connection_id
                )
                
                # Уведомляем
                try:
                    unban_msg = f"**Вас разблокировали!**\n\n"
                    unban_msg += f"📌 Reason: {reason}"
                    await bot.send_message(chat_id=int(target_id), text=unban_msg)
                except:
                    pass
            else:
                await send_to_business_chat(chat_id, f"❌ Пользователь {target_id} не найден в черном списке", connection_id)
            return
        
        # .stop (админ)
        if text.lower().startswith('.stop'):
            parts = text.split(maxsplit=2)
            if len(parts) < 3:
                await send_to_business_chat(chat_id, "❌ .stop [run/bot/max] [max]", connection_id)
                return
            
            target = parts[1].lower()
            action = parts[2].lower()
            
            if target not in ['run', 'bot', 'max'] or action != 'max':
                await send_to_business_chat(chat_id, "❌ .stop [run/bot/max] [max]", connection_id)
                return
            
            result = await stop_runners(target, user_id, message.from_user.username)
            await send_to_business_chat(chat_id, result, connection_id)
            return
        
        # .idlist (админ)
        if text.lower() == '.idlist':
            users = get_all_users()
            if not users:
                await send_to_business_chat(chat_id, "📊 Список пользователей пуст", connection_id)
                return
            result = "👥 СПИСОК ПОЛЬЗОВАТЕЛЕЙ\n\n"
            for user in users:
                result += f"🆔 {user.get('user_id', '?')} → @{user.get('username', 'Нет')}\n"
            await send_to_business_chat(chat_id, result[:4000], connection_id)
            return
        
        # .logs (админ)
        if text.lower().startswith('.logs'):
            parts = text.split(maxsplit=2)
            if len(parts) < 2:
                await send_to_business_chat(chat_id, "❌ .logs [ID] [кол-во]", connection_id)
                return
            try:
                target_id = int(parts[1])
                count = min(int(parts[2]) if len(parts) > 2 else 10, 50)
                logs = get_logs_for_user(target_id, count)
                if not logs:
                    await send_to_business_chat(chat_id, f"📊 Логов для {target_id} нет", connection_id)
                    return
                result = f"📋 ЛОГИ ДЛЯ {target_id} (последние {len(logs)})\n\n"
                for log in logs:
                    result += f"🕐 {log.get('time', '?')}\n📝 {log.get('command', '?')}\n\n"
                await send_to_business_chat(chat_id, result[:4000], connection_id)
            except:
                await send_to_business_chat(chat_id, "❌ Неверный формат", connection_id)
            return
        
        # .key (админ)
        if text.lower() == '.key':
            key = create_session_key()
            await send_to_business_chat(chat_id, f"🔑 Ваш ключ:\n\n`{key}`\n\n⏱ Действует 10 часов", connection_id)
            return
        
        # .tex on/off (админ)
        if text.lower().startswith('.tex on'):
            parts = text.split(maxsplit=2)
            if len(parts) < 3:
                await send_to_business_chat(chat_id, "❌ .tex on [время]", connection_id)
                return
            minutes, _ = parse_time(parts[2])
            expires_at = (datetime.now() + timedelta(minutes=minutes)).isoformat()
            set_tech_mode(True, expires_at)
            await send_to_business_chat(chat_id, f"✅ ТЕХ-РАБОТЫ ВКЛЮЧЕНЫ\n🕐 Окончание: {(datetime.now() + timedelta(minutes=minutes)).strftime('%d.%m.%Y %H:%M')}", connection_id)
            return
        
        if text.lower() == '.tex off':
            set_tech_mode(False, None)
            await send_to_business_chat(chat_id, "✅ ТЕХ-РАБОТЫ ВЫКЛЮЧЕНЫ", connection_id)
            return
        
        # .whois (пробивы)
        if text.lower().startswith('.whois'):
            parts = text.split(maxsplit=2)
            if len(parts) < 3:
                await send_to_business_chat(chat_id, "❌ .whois ip [IP] или .whois n [номер] или .whois qz [@username]", connection_id)
                return
            
            command_type = parts[1].lower()
            target = parts[2]
            
            loading = await show_animation(chat_id, connection_id)
            
            if command_type == "ip":
                try:
                    ipaddress.ip_address(target)
                except:
                    await edit_business_message(chat_id, loading.message_id, f"❌ Некорректный IP: {target}", connection_id)
                    return
                
                results, success_count = await probe_ip(target)
                final = analyze_ip_results(results)
                
                result_text = f"✅ РЕЗУЛЬТАТ ПРОБИВА IP\n\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n🌐 IP-адрес: {target}\n🌍 Страна: {final['country']}\n🏙️ Регион: {final['region']}\n🏙️ Город: {final['city']}\n📡 Провайдер: {final['isp']}\n🏢 Организация: {final['org']}\n🔗 AS: {final['as']}\n⏰ Часовой пояс: {final['timezone']}\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n📊 Обработано: {success_count}/5 серверов"
                await edit_business_message(chat_id, loading.message_id, result_text, connection_id)
                await save_log_async({"command": f".whois ip {target}", "user_id": user_id, "username": message.from_user.username or "Нет", "target": target, "time": get_msk_time()})
                
            elif command_type == "n":
                results, success_count, local_data = await probe_phone(target)
                if local_data and "error" in local_data:
                    await edit_business_message(chat_id, loading.message_id, f"❌ {local_data['error']}", connection_id)
                    return
                final = analyze_phone_results(results, local_data)
                result_text = f"✅ РЕЗУЛЬТАТ ПРОБИВА НОМЕРА\n\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n📱 Номер: {final['formatted']}\n📡 Оператор: {final['operator']}\n🌍 Регион: {final['region']}\n⏰ Часовой пояс: {final['timezone']}\n📊 Тип: {final['type']}\n🌐 Код страны: {final['country_code']}\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n📊 Обработано: {success_count} серверов"
                await edit_business_message(chat_id, loading.message_id, result_text, connection_id)
                await save_log_async({"command": f".whois n {target}", "user_id": user_id, "username": message.from_user.username or "Нет", "target": target, "time": get_msk_time()})
                
            elif command_type == "qz":
                data = await probe_username(target)
                result_text = f"✅ РЕЗУЛЬТАТ ПРОБИВА USERNAME\n\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n👤 Username: {data['username']}\n🆔 ID: {data['id']}\n📛 Имя: {data['name']}\n📊 Статус: {data['status']}\n━━━━━━━━━━━━━━━━━━━━━━━━━━"
                await edit_business_message(chat_id, loading.message_id, result_text, connection_id)
                await save_log_async({"command": f".whois qz {target}", "user_id": user_id, "username": message.from_user.username or "Нет", "target": target, "time": get_msk_time()})
                
            else:
                await edit_business_message(chat_id, loading.message_id, "❌ .whois ip [IP] или .whois n [номер] или .whois qz [@username]", connection_id)
            return
        
    except Exception as e:
        logger.error(f"❌ Ошибка бизнес-сообщения: {e}")

# ========== CALLBACK ==========

@dp.callback_query()
async def handle_callback(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    data = callback.data
    
    if is_banned(user_id):
        await callback.answer("⛔ Вы забанены!")
        return
    
    # ===== МЕНЮ .inf =====
    if data == "show_inf":
        await callback.message.edit_text(
            "**Добро пожаловать в RipSave 👀**\n\n"
            "Максимум возможностей — минимум лишних действий 🌟\n"
            "В этом разделе вы можете ознакомиться с функционалом бота, узнать о доступных инструментах и открыть подробную информацию о каждой возможности.",
            reply_markup=get_inf_keyboard()
        )
        await callback.answer()
        return
    
    if data == "inf_main":
        await callback.message.edit_text(
            "**📋 ОСНОВНЫЕ КОМАНДЫ**\n\n"
            ".me, .id, .chat — информация о вас/чате\n"
            ".business — ID бизнес-подключения\n"
            ".meta — данные сообщения в ответе\n"
            ".inf — показать это меню\n"
            ".ping — проверить задержку и состояние бота\n"
            ".time — текущее время МСК в формате [1:10]\n"
            ".date — текущая дата\n"
            ".cat — случайное изображение с котом\n"
            ".status — статистика чата с пользователем\n"
            ".clone — включить клонирование сообщений\n"
            ".unclone — выключить клонирование\n"
            ".copyp — скопировать ник, аватар и описание собеседника\n"
            ".uncopyp — вернуть свой исходный профиль",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Назад в меню", callback_data="show_inf")]
            ])
        )
        await callback.answer()
        return
    
    if data == "inf_probe":
        await callback.message.edit_text(
            "**🔍 ПРОБИВЫ**\n\n"
            ".whois ip [IP] — Пробив IP-адреса\n"
            ".whois n [номер] — Пробив номера телефона\n"
            ".whois qz [@username] — Пробив по юзернейму\n"
            ".scan — Проверка файла на вирусы\n"
            ".scanurl [ссылка] — Проверка ссылки на вирусы/фишинг\n"
            ".sherlock [ник] — Поиск аккаунтов по никнейму\n"
            ".status — Статистика чата с пользователем",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Назад в меню", callback_data="show_inf")]
            ])
        )
        await callback.answer()
        return
    
    if data == "inf_admin":
        await callback.message.edit_text(
            "**🛠️ АДМИН-КОМАНДЫ**\n\n"
            ".ban [ID] [время] [причина] [-s] [-g] — Забанить пользователя\n"
            ".unban [ID] [причина] [-g] — Разбанить пользователя\n"
            ".idlist — Список всех пользователей\n"
            ".logs [ID] — Логи пользователя\n"
            ".key — Создать ключ доступа\n"
            ".tex on/off — Включить/выключить техработы\n"
            ".stop [run/bot/max] max — Остановить раннеры",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Назад в меню", callback_data="show_inf")]
            ])
        )
        await callback.answer()
        return
    
    if data == "inf_fun":
        await callback.message.edit_text(
            "**🌟 РАЗВЛЕКАТЕЛЬНЫЕ (1/4)**\n\n"
            ".send — отправить фейковый чек\n"
            ".xrocket [сумма] — фейковый чек xRocket (USDT)\n"
            ".dox — пугающая анимация д0кса\n"
            ".snos — пугающая анимация сн0са\n"
            ".hack — пугающая анимация взлома\n"
            ".ddos — пугающая анимация DDoS\n"
            ".ban — пугающая анимация бана\n"
            ".spam [количество] [текст] — спам\n"
            ".love — красивая анимация сердца\n"
            ".arg [текст] — анимация появления текста\n"
            ".scrl [текст] — анимация прокрутки текста\n"
            ".print [текст] — эффект печати текста\n"
            ".glit [текст] — анимация с эффектом глитча\n"
            ".stairs [текст] — каждое слово отдельным сообщением\n"
            ".wave [текст] — нарастающая лесенка слов\n"
            ".letters [текст] — каждая буква отдельным сообщением\n"
            ".ghoul — анимация 1000-7",
            reply_markup=get_fun_keyboard(1)
        )
        await callback.answer()
        return
    
    if data == "fun_page_2":
        await callback.message.edit_text(
            "**🌟 РАЗВЛЕКАТЕЛЬНЫЕ (2/4)**\n\n"
            ".random [мин] [макс] — случайное число\n"
            ".flip — подбросить монетку\n"
            ".magic8 [вопрос] — магический шар 8\n"
            ".quote — случайная мотивационная цитата\n"
            ".rps [выбор] — камень, ножницы, бумага\n"
            ".slot — игровой автомат 🎰\n"
            ".coin — выбрать орла или решку\n"
            ".choose [вариант1 | вариант2] — случайный выбор\n"
            ".luck — узнать уровень удачи\n"
            ".fate — предсказать сегодняшний день\n"
            ".гадать [ник] — гадание на картах\n"
            ".мультик — пиксельный мультик из 🟦\n"
            ".meme — случайный мем (фото)\n"
            ".joke — случайная шутка\n"
            ".memer [текст] — мем-шаблон + ваш текст",
            reply_markup=get_fun_keyboard(2)
        )
        await callback.answer()
        return
    
    if data == "fun_page_3":
        await callback.message.edit_text(
            "**🌟 РАЗВЛЕКАТЕЛЬНЫЕ (3/4)**\n\n"
            ".password [длина] — генерация пароля\n"
            ".nickname — случайный никнейм\n"
            ".correct [текст] — исправить ошибки в тексте\n"
            ".rewrite [текст] — переписать текст красивее\n"
            ".formal [текст] — сделать текст официальным стилем\n"
            ".short [текст] — сократить текст\n"
            ".expand [текст] — расширить текст\n"
            ".detect [текст] — определить язык текста\n"
            ".translate [язык] [текст] — перевод текста\n"
            ".fixlayout [текст] — исправить неверную раскладку\n"
            ".summarize — краткое изложение длинного текста\n"
            ".upper [текст] — ВЕРХНИЙ РЕГИСТР\n"
            ".lower [текст] — нижний регистр\n"
            ".title [текст] — Сделать Каждое Слово С Заглавной\n"
            ".reverse [текст] — перевернуть текст\n"
            ".leet [текст] — fraud-шрифт (привет → ПР1в3т)\n"
            ".zalgo [текст] — добавить эффект искажения текста",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Назад", callback_data="fun_page_2")],
                [InlineKeyboardButton(text="📋 Меню", callback_data="show_inf")]
            ])
        )
        await callback.answer()
        return
    
    if data == "inf_utils":
        await callback.message.edit_text(
            "**🛠️ УТИЛИТЫ (1/3)**\n\n"
            ".mute [N] — мут (N мин или до .unmute)\n"
            ".unmute — выключить мут\n"
            ".nomute — обход мута (в чате даже под мутом; ЛС: /nomute)\n"
            ".unnomute — выключить обход мута\n"
            ".warn N — автомут после N сообщений\n"
            ".unwarn — снять warn\n"
            ".antispam on/off — антиспам\n"
            ".a_troll / .aut_troll / .stop_troll — троллинг",
            reply_markup=get_utils_keyboard(1)
        )
        await callback.answer()
        return
    
    if data == "utils_page_2":
        await callback.message.edit_text(
            "**🛠️ УТИЛИТЫ (2/3)**\n\n"
            ".gift — отправка подарков за Stars\n"
            ".tool [вопрос] — ответ + топ‑3 сайта\n"
            ".pic [описание] — поиск картинки по описанию\n"
            ".proxies — свежие HTTP-прокси\n"
            ".leaks [email/телефон] — проверка по слитым базам\n"
            ".export — экспорт переписки в .txt\n"
            ".tempmail — временная почта (inbox в боте)\n"
            ".scam [текст] — проверка на скам/фишинг\n"
            ".место [город/адрес] — точка на карте",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Назад", callback_data="utils_page_1")],
                [InlineKeyboardButton(text="📋 Меню", callback_data="show_inf")]
            ])
        )
        await callback.answer()
        return
    
    if data == "inf_all":
        await callback.message.edit_text(
            "**📚 ВСЕ КОМАНДЫ**\n\n"
            "🔹 ОСНОВНЫЕ:\n"
            ".me, .id, .chat — информация о вас/чате\n"
            ".business — ID бизнес-подключения\n"
            ".meta — данные сообщения в ответе\n"
            ".inf — показать это меню\n"
            ".ping — проверить задержку\n"
            ".time — текущее время МСК\n"
            ".date — текущая дата\n"
            ".cat — случайный кот\n"
            ".status — статистика чата\n"
            ".clone — включить клонирование\n"
            ".unclone — выключить клонирование\n"
            ".copyp — скопировать профиль\n"
            ".uncopyp — вернуть профиль\n\n"
            "🔹 ПРОБИВЫ:\n"
            ".whois ip [IP] — Пробив IP\n"
            ".whois n [номер] — Пробив номера\n"
            ".whois qz [@username] — Пробив юзера\n"
            ".scan — Проверка файла на вирусы\n"
            ".scanurl [ссылка] — Проверка ссылки\n"
            ".sherlock [ник] — Поиск аккаунтов\n\n"
            "🔹 АДМИН:\n"
            ".ban [ID] [время] [причина] [-s] [-g] — Бан\n"
            ".unban [ID] [причина] [-g] — Разбан\n"
            ".idlist — Список пользователей\n"
            ".logs [ID] — Логи пользователя\n"
            ".key — Создать ключ\n"
            ".tex on/off — Техработы\n"
            ".stop [run/bot/max] max — Остановить раннеры\n\n"
            "🔹 РАЗВЛЕКАТЕЛЬНЫЕ:\n"
            ".send, .xrocket, .dox, .snos, .hack, .ddos, .ban\n"
            ".spam, .love, .arg, .scrl, .print, .glit\n"
            ".stairs, .wave, .letters, .ghoul\n"
            ".random, .flip, .magic8, .quote, .rps, .slot\n"
            ".coin, .choose, .luck, .fate, .гадать, .мультик\n"
            ".meme, .joke, .memer\n\n"
            "🔹 УТИЛИТЫ:\n"
            ".password, .nickname, .correct, .rewrite\n"
            ".formal, .short, .expand, .detect\n"
            ".translate, .fixlayout, .summarize\n"
            ".upper, .lower, .title, .reverse, .leet, .zalgo\n"
            ".mute, .unmute, .nomute, .unnomute\n"
            ".warn, .unwarn, .antispam\n"
            ".gift, .tool, .pic, .proxies, .leaks, .export\n"
            ".tempmail, .scam, .место",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Назад в меню", callback_data="show_inf")]
            ])
        )
        await callback.answer()
        return
    
    if data == "close_menu":
        await callback.message.delete()
        await callback.answer()
        return
    
    # ===== СТАРЫЕ CALLBACK =====
    if data == "probe_ip":
        await callback.message.answer("🌐 ВВЕДИТЕ IP\n📌 Пример: 8.8.8.8")
    elif data == "probe_phone":
        await callback.message.answer("📱 ВВЕДИТЕ НОМЕР\n📌 Пример: 89001234567")
    elif data == "probe_user":
        await callback.message.answer("👤 ВВЕДИТЕ @USERNAME\n📌 Пример: @username")
    await callback.answer()

# ========== ЛИЧНЫЕ СООБЩЕНИЯ ==========

@dp.message()
async def handle_private_message(message: types.Message):
    user_id = message.from_user.id
    
    if message.business_connection_id:
        return
    
    if is_banned(user_id):
        if str(user_id) not in blocked_notified:
            ban_info = get_ban_info(user_id)
            reason = ban_info.get("reason", "Не указана") if ban_info else "Не указана"
            await message.answer(f"⛔ ВАС ЗАБЛОКИРОВАЛИ В БОТЕ!\n\n📌 Причина: {reason}")
            blocked_notified[str(user_id)] = True
        return
    
    if is_tech_mode() and not is_admin(user_id):
        tech_info = get_tech_info()
        await message.answer(f"🛠️ БОТ НА ТЕХНИЧЕСКИХ РАБОТАХ\n\n🕐 ВРЕМЯ: {tech_info.get('expires_at', 'Неизвестно')}")
        return
    
    if not message.text:
        return
    
    text = message.text.strip()
    
    if text.startswith('/'):
        return
    
    if text.startswith('.'):
        await message.answer("❌ Команды с . — только в чатах с собеседниками!\n📌 В личке используй /help")
        return
    
    # Пробив по IP
    if re.match(r'^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$', text):
        loading = await show_animation(message)
        
        try:
            ipaddress.ip_address(text)
        except:
            await edit_normal_message(message.chat.id, loading.message_id, f"❌ Некорректный IP: {text}")
            return
        
        results, success_count = await probe_ip(text)
        final = analyze_ip_results(results)
        
        result_text = f"✅ РЕЗУЛЬТАТ ПРОБИВА IP\n\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n🌐 IP-адрес: {text}\n🌍 Страна: {final['country']}\n🏙️ Регион: {final['region']}\n🏙️ Город: {final['city']}\n📡 Провайдер: {final['isp']}\n🏢 Организация: {final['org']}\n🔗 AS: {final['as']}\n⏰ Часовой пояс: {final['timezone']}\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n📊 Обработано: {success_count}/5 серверов"
        await edit_normal_message(message.chat.id, loading.message_id, result_text)
        await save_log_async({"command": f"IP {text}", "user_id": user_id, "username": message.from_user.username or "Нет", "target": text, "time": get_msk_time()})
        return
    
    if re.match(r'^\+?\d{10,15}$', text):
        loading = await show_animation(message)
        results, success_count, local_data = await probe_phone(text)
        if local_data and "error" in local_data:
            await edit_normal_message(message.chat.id, loading.message_id, f"❌ {local_data['error']}")
            return
        final = analyze_phone_results(results, local_data)
        result_text = f"✅ РЕЗУЛЬТАТ ПРОБИВА НОМЕРА\n\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n📱 Номер: {final['formatted']}\n📡 Оператор: {final['operator']}\n🌍 Регион: {final['region']}\n⏰ Часовой пояс: {final['timezone']}\n📊 Тип: {final['type']}\n🌐 Код страны: {final['country_code']}\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n📊 Обработано: {success_count} серверов"
        await edit_normal_message(message.chat.id, loading.message_id, result_text)
        await save_log_async({"command": f"Номер {text}", "user_id": user_id, "username": message.from_user.username or "Нет", "target": text, "time": get_msk_time()})
        return
    
    if text.startswith('@'):
        loading = await show_animation(message)
        data = await probe_username(text)
        result_text = f"✅ РЕЗУЛЬТАТ ПРОБИВА USERNAME\n\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n👤 Username: {data['username']}\n🆔 ID: {data['id']}\n📛 Имя: {data['name']}\n📊 Статус: {data['status']}\n━━━━━━━━━━━━━━━━━━━━━━━━━━"
        await edit_normal_message(message.chat.id, loading.message_id, result_text)
        await save_log_async({"command": f"Юзер {text}", "user_id": user_id, "username": message.from_user.username or "Нет", "target": text, "time": get_msk_time()})
        return
    
    await message.answer("❓ Неизвестная команда\n\n📌 Введи /help для списка команд")

# ========== ЗАПУСК ==========

async def main():
    print("=" * 60)
    print("🔥 RIPSAVE БОТ ЗАПУЩЕН!")
    print(f"👤 АДМИН: {ADMIN_ID}")
    print(f"📁 Supabase: {SUPABASE_URL}")
    print("📌 Команды с / — в личке бота")
    print("📌 Команды с . — в чатах с собеседниками")
    print("📌 .inf — новое меню команд")
    print("=" * 60)
    
    os.makedirs('data', exist_ok=True)
    
    try:
        await dp.start_polling(bot)
    except Exception as e:
        if "Conflict" in str(e):
            print("⚠️ Конфликт! Переподключаемся...")
            await asyncio.sleep(5)
            await dp.start_polling(bot)
        else:
            raise

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("⏹️ Бот остановлен")
    except Exception as e:
        print(f"❌ Ошибка: {e}")
        sys.exit(1)
