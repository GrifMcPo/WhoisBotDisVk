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

# ===== ГЛОБАЛЬНЫЕ ПЕРЕМЕННЫЕ =====
business_connections = {}
blocked_notified = {}
processing_commands = {}
muted_chats = {}
antispam_settings = {}

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

def format_response(title, content):
    """Форматирует ответ: жирный заголовок + цитата (как в Telegram)"""
    lines = content.split('\n')
    quoted = '\n'.join([f"│ {line}" for line in lines])
    return f"*{title}*\n\n{quoted}"

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

# ========== ОСТАНОВКА РАННЕРОВ ==========
async def stop_runners(target, user_id=None, username=None):
    GH_TOKEN = os.getenv("GH_TOKEN", "")
    if not GH_TOKEN:
        return "❌ GH_TOKEN не настроен!"
    
    REPO = "GrifMcPo/WhoisBotDisVk"
    
    try:
        url = f"https://api.github.com/repos/{REPO}/actions/runs"
        headers = {
            "Authorization": f"token {GH_TOKEN}",
            "Accept": "application/vnd.github.v3+json"
        }
        
        response = requests.get(url, headers=headers)
        if response.status_code != 200:
            return f"❌ Ошибка получения раннеров: {response.status_code}"
        
        runs = response.json().get("workflow_runs", [])
        running_runs = [r for r in runs if r["status"] in ["queued", "in_progress"]]
        
        if not running_runs:
            return "📊 Нет активных раннеров"
        
        stopped_count = 0
        skipped_count = 0
        
        for run in running_runs:
            run_id = run["id"]
            run_name = run.get("name", "unknown")
            
            should_stop = False
            
            if target == "max":
                should_stop = True
            elif target == "bot":
                if "bot" in run_name.lower() or "telegram" in run_name.lower():
                    should_stop = True
            elif target == "run":
                if "bot" not in run_name.lower() and "telegram" not in run_name.lower():
                    should_stop = True
            
            if should_stop:
                cancel_url = f"https://api.github.com/repos/{REPO}/actions/runs/{run_id}/cancel"
                cancel_response = requests.post(cancel_url, headers=headers)
                if cancel_response.status_code in [200, 202]:
                    stopped_count += 1
                else:
                    skipped_count += 1
            else:
                skipped_count += 1
        
        result = f"✅ Остановлено: {stopped_count} раннеров\n"
        result += f"⏭️ Пропущено: {skipped_count} раннеров\n"
        result += f"🎯 Цель: {target}\n"
        result += f"🕐 Время: {get_msk_time()}"
        
        await save_log_async({
            "command": f".stop {target} max",
            "user_id": user_id or ADMIN_ID,
            "username": username or "RCON",
            "stopped": stopped_count,
            "skipped": skipped_count,
            "time": get_msk_time()
        })
        
        return result
        
    except Exception as e:
        return f"❌ Ошибка: {str(e)}"

# ========== ПРОБИВ IP ==========
async def probe_ip(ip: str):
    results = []
    success_count = 0
    
    sources = [
        {"name": "Сервер #1", "url": "http://ip-api.com/json/{}?fields=status,message,country,countryCode,region,regionName,city,zip,lat,lon,isp,org,as,asname,timezone,query"},
        {"name": "Сервер #2", "url": "https://ipinfo.io/{}/json"},
        {"name": "Сервер #3", "url": "http://ipwhois.io/json/{}"},
        {"name": "Сервер #4", "url": "https://freegeoip.app/json/{}"},
        {"name": "Сервер #5", "url": "https://ipapi.co/{}/json"},
    ]
    
    async with aiohttp.ClientSession() as session:
        for source in sources:
            try:
                url = source["url"].format(ip)
                async with session.get(url, timeout=5) as response:
                    if response.status == 200:
                        data = await response.json()
                        success_count += 1
                        results.append({"source": source["name"], "data": data})
            except Exception as e:
                logger.warning(f"⚠️ Ошибка {source['name']}: {e}")
                pass
    
    return results, success_count

# ========== ПРОБИВ НОМЕРА ==========
async def probe_phone(phone: str):
    results = []
    success_count = 0
    local_data = None
    
    phone_clean = phone.replace('+', '').replace('-', '').replace('(', '').replace(')', '').replace(' ', '')
    
    try:
        parsed = phonenumbers.parse(phone_clean, None)
        
        if not phonenumbers.is_valid_number(parsed):
            return [], 0, {"error": "❌ Номер не существует или введен неверно"}
        
        operator = carrier.name_for_number(parsed, "ru") or "Не определен"
        region = geocoder.description_for_number(parsed, "ru") or "Не определен"
        timezone_info = timezone.time_zones_for_number(parsed)
        phone_type = phonenumbers.number_type(parsed)
        formatted = phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.INTERNATIONAL)
        national = phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.NATIONAL)
        country_code = parsed.country_code
        
        type_names = {
            0: "Неизвестный",
            1: "Стационарный",
            2: "Мобильный",
            3: "Стационарный (набор)",
            4: "VoIP",
            5: "Личный номер",
            6: "Универсальный",
            7: "Pager"
        }
        
        local_data = {
            "formatted": formatted,
            "national": national,
            "operator": operator,
            "region": region,
            "timezone": ', '.join(timezone_info) if timezone_info else "Не определен",
            "type": type_names.get(phone_type, "Неизвестный"),
            "country_code": f"+{country_code}",
            "valid": True
        }
        results.append(local_data)
        success_count += 1
        
    except phonenumbers.NumberParseException:
        return [], 0, {"error": "❌ Некорректный формат номера\nПример: 89001234567 или +79001234567"}
    except Exception as e:
        logger.error(f"❌ Ошибка парсинга номера: {e}")
        return [], 0, {"error": f"❌ Ошибка: {str(e)}"}
    
    return results, success_count, local_data

async def probe_username(username: str):
    return {
        "username": username,
        "id": 123456789,
        "name": "Пользователь",
        "status": "Активен"
    }

def analyze_ip_results(results):
    final = {"country": "Не определено", "region": "Не определено", "city": "Не определено", 
             "isp": "Не определено", "org": "Не определено", "as": "Не определено", "timezone": "Не определено"}
    
    field_map = {
        "country": ["country", "country_name", "countryCode"],
        "region": ["region", "regionName", "region_name"],
        "city": ["city", "city_name"],
        "isp": ["isp", "org"],
        "org": ["org", "organization"],
        "as": ["as", "asn"],
        "timezone": ["timezone", "time_zone"]
    }
    
    values = {key: [] for key in final.keys()}
    
    for result in results:
        data = result.get("data", {})
        for field, aliases in field_map.items():
            for alias in aliases:
                if alias in data and data[alias]:
                    values[field].append(data[alias])
                    break
    
    from collections import Counter
    for field, vals in values.items():
        if vals:
            final[field] = Counter(vals).most_common(1)[0][0]
    
    return final

def analyze_phone_results(results, local_data):
    final = {
        "formatted": "Не определено",
        "national": "Не определено",
        "operator": "Не определено",
        "region": "Не определено",
        "timezone": "Не определено",
        "type": "Не определено",
        "country_code": "Не определено"
    }
    
    if local_data:
        for key in final.keys():
            if key in local_data:
                final[key] = local_data[key]
    
    return final

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
            reply_markup=reply_markup,
            parse_mode="Markdown"
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
            "business_connection_id": connection_id,
            "parse_mode": "Markdown"
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
            text=text,
            parse_mode="Markdown"
        )
        return True
    except:
        return False

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

# ========== КЛАВИАТУРЫ ==========

def get_inf_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📋 Основные", callback_data="inf_main"),
            InlineKeyboardButton(text="🔍 Пробивы", callback_data="inf_probe"),
            InlineKeyboardButton(text="🛠️ Админ", callback_data="inf_admin")
        ],
        [
            InlineKeyboardButton(text="🌟 Развлечения", callback_data="inf_fun"),
            InlineKeyboardButton(text="🛠️ Утилиты", callback_data="inf_utils"),
            InlineKeyboardButton(text="📚 Все", callback_data="inf_all")
        ],
        [
            InlineKeyboardButton(text="❌ Закрыть", callback_data="close_menu")
        ]
    ])

def get_fun_keyboard(page=1):
    if page == 1:
        return InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="⬅️ Назад", callback_data="show_inf"),
                InlineKeyboardButton(text="➡️ Еще", callback_data="fun_page_2"),
                InlineKeyboardButton(text="❌ Закрыть", callback_data="close_menu")
            ]
        ])
    else:
        return InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="⬅️ Назад", callback_data="fun_page_1"),
                InlineKeyboardButton(text="📋 Меню", callback_data="show_inf"),
                InlineKeyboardButton(text="❌ Закрыть", callback_data="close_menu")
            ]
        ])

def get_utils_keyboard(page=1):
    if page == 1:
        return InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="⬅️ Назад", callback_data="show_inf"),
                InlineKeyboardButton(text="➡️ Еще", callback_data="utils_page_2"),
                InlineKeyboardButton(text="❌ Закрыть", callback_data="close_menu")
            ]
        ])
    else:
        return InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="⬅️ Назад", callback_data="utils_page_1"),
                InlineKeyboardButton(text="📋 Меню", callback_data="show_inf"),
                InlineKeyboardButton(text="❌ Закрыть", callback_data="close_menu")
            ]
        ])

def get_back_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Назад в меню", callback_data="show_inf")]
    ])

def get_main_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🌐 Пробив IP", callback_data="probe_ip"),
            InlineKeyboardButton(text="📱 Пробив номера", callback_data="probe_phone"),
            InlineKeyboardButton(text="👤 Пробив юзера", callback_data="probe_user")
        ],
        [
            InlineKeyboardButton(text="📋 Меню команд", callback_data="show_inf")
        ]
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
        
        # ===== .inf - главное меню =====
        if text.lower() == '.inf':
            await send_to_business_chat(
                chat_id,
                "*Добро пожаловать в RipSave 👀*\n\n"
                "Максимум возможностей — минимум лишних действий 🌟\n"
                "В этом разделе вы можете ознакомиться с функционалом бота, узнать о доступных инструментах и открыть подробную информацию о каждой возможности.",
                connection_id,
                reply_markup=get_inf_keyboard()
            )
            return
        
        # ===== ОСНОВНЫЕ КОМАНДЫ =====
        
        # .me
        if text.lower() == '.me':
            user = message.from_user
            await send_to_business_chat(
                chat_id,
                format_response(
                    "Ваш профиль",
                    f"ID: {user.id}\nНик: @{user.username or 'Нет'}\nРоль: {'админ' if is_admin(user.id) else 'пользователь'}"
                ),
                connection_id
            )
            return
        
        # .id
        if text.lower() == '.id':
            await send_to_business_chat(
                chat_id,
                format_response(
                    "Идентификаторы",
                    f"Ваш Telegram ID: {message.from_user.id}\nID чата: {chat_id}"
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
                    "Информация о чате",
                    f"ID: {chat.id}\nНазвание: {chat.title or 'личный чат'}"
                ),
                connection_id
            )
            return
        
        # .business
        if text.lower() == '.business':
            await send_to_business_chat(
                chat_id,
                format_response(
                    "Business connection",
                    f"{connection_id or 'Не активно'}"
                ),
                connection_id
            )
            return
        
        # .time
        if text.lower() == '.time':
            await send_to_business_chat(
                chat_id,
                format_response(
                    "Текущее время (МСК)",
                    f"[{get_msk_time_short()}]"
                ),
                connection_id
            )
            return
        
        # .date
        if text.lower() == '.date':
            await send_to_business_chat(
                chat_id,
                format_response(
                    "Текущая дата",
                    f"{get_msk_date_full()}\nTZ: Europe/Moscow"
                ),
                connection_id
            )
            return
        
        # .ping
        if text.lower() == '.ping':
            start = datetime.now()
            await send_to_business_chat(chat_id, "*🏓 Pong*", connection_id)
            end = datetime.now()
            ping_ms = (end - start).microseconds / 1000
            await send_to_business_chat(
                chat_id,
                format_response(
                    "🏓 Pong",
                    f"API: {ping_ms:.0f} мс\nTCP: {ping_ms * 0.5:.0f} мс\nБот: @{bot.username}\nСтатус: ✅ онлайн"
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
                    "Статистика пользователя",
                    f"{reply.from_user.full_name} (@{reply.from_user.username or 'Нет'})\nID: {target_user_id}\nВсего команд: {log_count}\n\nПоследние команды:\n{last_commands}"
                ),
                connection_id
            )
            return
        
        # .clone / .unclone
        if text.lower() == '.clone':
            muted_chats[str(chat_id)] = True
            await send_to_business_chat(chat_id, "✅ Клонирование сообщений ВКЛЮЧЕНО", connection_id)
            return
        
        if text.lower() == '.unclone':
            muted_chats.pop(str(chat_id), None)
            await send_to_business_chat(chat_id, "✅ Клонирование сообщений ВЫКЛЮЧЕНО", connection_id)
            return
        
        # .copyp / .uncopyp
        if text.lower() == '.copyp' and message.reply_to_message:
            target = message.reply_to_message.from_user
            await send_to_business_chat(
                chat_id,
                f"✅ Профиль скопирован!\n\nИмя: {target.full_name}\nUsername: @{target.username or 'Нет'}\nID: {target.id}\n\nИспользуй .uncopyp чтобы вернуть свой профиль",
                connection_id
            )
            return
        
        if text.lower() == '.uncopyp':
            await send_to_business_chat(chat_id, "✅ Ваш профиль восстановлен", connection_id)
            return
        
        # ===== РАЗВЛЕКАТЕЛЬНЫЕ КОМАНДЫ =====
        
        # .send
        if text.lower() == '.send':
            await send_to_business_chat(chat_id, "📝 Создаю чек...", connection_id)
            await asyncio.sleep(0.5)
            await send_to_business_chat(chat_id, "✅ Чек готов!\n\n💰 Сумма: 1000 RUB", connection_id)
            return
        
        # .xrocket
        if text.lower().startswith('.xrocket '):
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
        
        # .ban_anim
        if text.lower() == '.ban':
            await run_animation(chat_id, 'ban', connection_id)
            return
        
        # .spam
        if text.lower().startswith('.spam '):
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
        
        # .random
        if text.lower().startswith('.random '):
            parts = text.split()
            if len(parts) >= 3:
                try:
                    min_val = int(parts[1])
                    max_val = int(parts[2])
                    result = random.randint(min_val, max_val)
                    await send_to_business_chat(chat_id, f"🎲 Случайное число: *{result}*", connection_id)
                except:
                    await send_to_business_chat(chat_id, "❌ .random [мин] [макс]", connection_id)
            return
        
        # .flip
        if text.lower() == '.flip':
            result = random.choice(['Орёл', 'Решка'])
            await send_to_business_chat(chat_id, f"🪙 *{result}*!", connection_id)
            return
        
        # .coin
        if text.lower() == '.coin':
            result = random.choice(['Орёл', 'Решка'])
            await send_to_business_chat(chat_id, f"🪙 *{result}*!", connection_id)
            return
        
        # .magic8
        if text.lower().startswith('.magic8 '):
            answers = [
                "Да", "Нет", "Возможно", "Спроси позже",
                "Определённо да", "Определённо нет", "Всё зависит от тебя",
                "Шансы хорошие", "Шансы невелики", "Пока неясно"
            ]
            result = random.choice(answers)
            await send_to_business_chat(chat_id, f"🎱 *{result}*", connection_id)
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
                f"🤖 Мой выбор: *{bot_choice}*\n\nТвой выбор: *{choice}*",
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
                f"🎰 *{result[0]} {result[1]} {result[2]}*\n\n{'🎉 ПОБЕДА!' if is_win else '😔 Попробуй ещё'}",
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
            await send_to_business_chat(chat_id, f"🎯 Я выбираю: *{choice}*", connection_id)
            return
        
        # .luck
        if text.lower() == '.luck':
            luck = random.randint(0, 100)
            await send_to_business_chat(chat_id, f"🍀 Ваша удача: *{luck}%*", connection_id)
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
            leet_map = {'а': '4', 'е': '3', 'и': '1', 'о': '0', 'с': '5', 'т': '7'}
            leet_text = ''.join([leet_map.get(c.lower(), c) for c in text[6:]])
            await send_to_business_chat(chat_id, f"`{leet_text}`", connection_id)
            return
        
        # .zalgo
        if text.lower().startswith('.zalgo '):
            zalgo_chars = ['̖', '̗', '̘', '̙', '̜', '̝', '̞', '̟', '̠', '̤', '̥', '̦', '̩', '̪', '̫', '̬']
            zalgo_text = ''.join([c + ''.join(random.choices(zalgo_chars, k=random.randint(1, 3))) for c in text[7:]])
            await send_to_business_chat(chat_id, f"`{zalgo_text}`", connection_id)
            return
        
        # .mute / .unmute
        if text.lower().startswith('.mute '):
            try:
                minutes = int(text[6:])
                muted_chats[str(chat_id)] = minutes
                await send_to_business_chat(
                    chat_id,
                    f"🔇 Чат замучен на {minutes} минут",
                    connection_id
                )
            except:
                await send_to_business_chat(chat_id, "❌ .mute [N]", connection_id)
            return
        
        if text.lower() == '.unmute':
            muted_chats.pop(str(chat_id), None)
            await send_to_business_chat(chat_id, "🔊 Чат размучен", connection_id)
            return
        
        # .antispam
        if text.lower() == '.antispam on':
            antispam_settings[str(chat_id)] = True
            await send_to_business_chat(chat_id, "✅ Антиспам включён", connection_id)
            return
        
        if text.lower() == '.antispam off':
            antispam_settings.pop(str(chat_id), None)
            await send_to_business_chat(chat_id, "✅ Антиспам выключён", connection_id)
            return
        
        # .proxies
        if text.lower() == '.proxies':
            try:
                url = "https://api.proxyscrape.com/v2/?request=displayproxies&protocol=http&timeout=10000&country=all&ssl=all&anonymity=all"
                response = requests.get(url, timeout=10)
                if response.status_code == 200:
                    proxies = response.text.strip().split('\n')[:10]
                    result = "🌐 Свежие HTTP-прокси:\n\n" + "\n".join(proxies)
                    await send_to_business_chat(chat_id, result, connection_id)
                else:
                    await send_to_business_chat(chat_id, "❌ Ошибка получения прокси", connection_id)
            except:
                await send_to_business_chat(chat_id, "❌ Ошибка подключения", connection_id)
            return
        
        # .tempmail
        if text.lower() == '.tempmail':
            try:
                response = requests.get("https://api.temp-mail.org/request/domains/format/json")
                if response.status_code == 200:
                    domains = response.json()
                    domain = domains[0] if domains else "temp-mail.org"
                    random_part = ''.join(random.choices(string.ascii_lowercase + string.digits, k=10))
                    email = f"{random_part}@{domain}"
                    await send_to_business_chat(
                        chat_id,
                        f"📧 Временная почта создана:\n`{email}`\n\nИспользуйте .inbox чтобы проверить почту",
                        connection_id
                    )
                else:
                    await send_to_business_chat(chat_id, "❌ Ошибка создания почты", connection_id)
            except:
                await send_to_business_chat(chat_id, "❌ Ошибка подключения", connection_id)
            return
        
        # .scam
        if text.lower().startswith('.scam '):
            scam_text = text[6:]
            suspicious = ['http', 'bit.ly', 't.me', 'ххх', 'бесплатно', 'выиграл']
            is_scam = any(word in scam_text.lower() for word in suspicious)
            await send_to_business_chat(
                chat_id,
                f"*🔍 Проверка на скам/фишинг*\n\nТекст: {scam_text}\nСтатус: {'⚠️ ПОДОЗРИТЕЛЬНО' if is_scam else '✅ БЕЗОПАСНО'}",
                connection_id
            )
            return
        
        # .export
        if text.lower() == '.export':
            await send_to_business_chat(chat_id, "📋 Копирую переписку...", connection_id)
            await asyncio.sleep(0.5)
            await send_to_business_chat(chat_id, "📄 Переписка скопирована, создаю файл переписки...", connection_id)
            await asyncio.sleep(0.5)
            await send_to_business_chat(chat_id, "📁 Файл готов, отправляю в чат...", connection_id)
            await asyncio.sleep(0.5)
            await send_to_business_chat(chat_id, "✅ Экспорт завершён!", connection_id)
            return
        
        # ===== АДМИН-КОМАНДЫ =====
        
        # .ban
        if text.lower().startswith('.ban '):
            parts = text.split()
            if len(parts) < 4:
                await send_to_business_chat(
                    chat_id,
                    "❌ .ban [ID] [время] [причина] [-s] [-g]\nПример: .ban 123456 1h Спам -s -g",
                    connection_id
                )
                return
            
            target_id = parts[1]
            time_str = parts[2]
            reason = " ".join(parts[3:])
            
            is_global = '-g' in parts
            silent = '-s' in parts
            reason = re.sub(r'\s*-[gs]\s*', ' ', reason).strip()
            
            minutes, time_display = parse_time(time_str)
            
            if add_ban(target_id, reason, user_id, minutes, is_global, silent):
                if silent:
                    await delete_business_message(chat_id, message_id, connection_id)
                    await bot.send_message(
                        user_id,
                        format_response(
                            "Пользователь успешно заблокирован!",
                            f"ID: {target_id}\nПричина: {reason}\nСервер: {'глобальный' if is_global else 'локальный'}"
                        ),
                        parse_mode="Markdown"
                    )
                else:
                    await send_to_business_chat(
                        chat_id,
                        format_response(
                            "Пользователь успешно заблокирован!",
                            f"ID: {target_id}\nПричина: {reason}\nСервер: {'глобальный' if is_global else 'локальный'}"
                        ),
                        connection_id
                    )
                
                try:
                    ban_msg = format_response(
                        "Вас заблокировали в боте!",
                        f"Причина: {reason}\nВремя: {time_display}\nСервер: {'глобальный' if is_global else 'локальный'}"
                    )
                    if not minutes:
                        ban_msg += "\n\n⚠️ Блокировка выдана навсегда"
                    await bot.send_message(chat_id=int(target_id), text=ban_msg, parse_mode="Markdown")
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
                    format_response(
                        "Пользователь разбанен!",
                        f"ID: {target_id}\nПричина: {reason}\nСервер: {'глобальный' if is_global else 'локальный'}"
                    ),
                    connection_id
                )
                
                try:
                    await bot.send_message(
                        chat_id=int(target_id),
                        text=format_response(
                            "Вас разблокировали!",
                            f"Причина: {reason}"
                        ),
                        parse_mode="Markdown"
                    )
                except:
                    pass
            else:
                await send_to_business_chat(chat_id, f"❌ Пользователь {target_id} не найден в черном списке", connection_id)
            return
        
        # .stop
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
        
        # .idlist
        if text.lower() == '.idlist':
            users = get_all_users()
            if not users:
                await send_to_business_chat(chat_id, "📊 Список пользователей пуст", connection_id)
                return
            result = "*👥 СПИСОК ПОЛЬЗОВАТЕЛЕЙ*\n\n"
            for user in users:
                result += f"🆔 {user.get('user_id', '?')} → @{user.get('username', 'Нет')}\n"
            await send_to_business_chat(chat_id, result[:4000], connection_id)
            return
        
        # .logs
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
                result = f"*📋 ЛОГИ ДЛЯ {target_id}* (последние {len(logs)})\n\n"
                for log in logs:
                    result += f"🕐 {log.get('time', '?')}\n📝 {log.get('command', '?')}\n\n"
                await send_to_business_chat(chat_id, result[:4000], connection_id)
            except:
                await send_to_business_chat(chat_id, "❌ Неверный формат", connection_id)
            return
        
        # .tex on/off
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
                
                result_text = f"✅ РЕЗУЛЬТАТ ПРОБИВА IP\n\n━━━━━━━━━━━━━━━━━━━━━━━━━━\nIP-адрес: {target}\nСтрана: {final['country']}\nРегион: {final['region']}\nГород: {final['city']}\nПровайдер: {final['isp']}\nОрганизация: {final['org']}\nAS: {final['as']}\nЧасовой пояс: {final['timezone']}\n━━━━━━━━━━━━━━━━━━━━━━━━━━\nОбработано: {success_count}/5 серверов"
                await edit_business_message(chat_id, loading.message_id, result_text, connection_id)
                await save_log_async({"command": f".whois ip {target}", "user_id": user_id, "username": message.from_user.username or "Нет", "target": target, "time": get_msk_time()})
                
            elif command_type == "n":
                results, success_count, local_data = await probe_phone(target)
                if local_data and "error" in local_data:
                    await edit_business_message(chat_id, loading.message_id, f"❌ {local_data['error']}", connection_id)
                    return
                final = analyze_phone_results(results, local_data)
                result_text = f"✅ РЕЗУЛЬТАТ ПРОБИВА НОМЕРА\n\n━━━━━━━━━━━━━━━━━━━━━━━━━━\nНомер: {final['formatted']}\nОператор: {final['operator']}\nРегион: {final['region']}\nЧасовой пояс: {final['timezone']}\nТип: {final['type']}\nКод страны: {final['country_code']}\n━━━━━━━━━━━━━━━━━━━━━━━━━━\nОбработано: {success_count} серверов"
                await edit_business_message(chat_id, loading.message_id, result_text, connection_id)
                await save_log_async({"command": f".whois n {target}", "user_id": user_id, "username": message.from_user.username or "Нет", "target": target, "time": get_msk_time()})
                
            elif command_type == "qz":
                data = await probe_username(target)
                result_text = f"✅ РЕЗУЛЬТАТ ПРОБИВА USERNAME\n\n━━━━━━━━━━━━━━━━━━━━━━━━━━\nUsername: {data['username']}\nID: {data['id']}\nИмя: {data['name']}\nСтатус: {data['status']}\n━━━━━━━━━━━━━━━━━━━━━━━━━━"
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
    
    try:
        # ===== .inf - при нажатии удаляем старое и присылаем новое =====
        if data == "show_inf":
            try:
                await callback.message.delete()
            except:
                pass
            await callback.message.answer(
                "*Добро пожаловать в RipSave 👀*\n\n"
                "Максимум возможностей — минимум лишних действий 🌟\n"
                "В этом разделе вы можете ознакомиться с функционалом бота, узнать о доступных инструментах и открыть подробную информацию о каждой возможности.",
                reply_markup=get_inf_keyboard(),
                parse_mode="Markdown"
            )
            await callback.answer()
            return
        
        if data == "inf_main":
            try:
                await callback.message.delete()
            except:
                pass
            await callback.message.answer(
                "*📋 ОСНОВНЫЕ КОМАНДЫ*\n\n"
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
                reply_markup=get_back_keyboard(),
                parse_mode="Markdown"
            )
            await callback.answer()
            return
        
        if data == "inf_probe":
            try:
                await callback.message.delete()
            except:
                pass
            await callback.message.answer(
                "*🔍 ПРОБИВЫ*\n\n"
                ".whois ip [IP] — Пробив IP-адреса\n"
                ".whois n [номер] — Пробив номера телефона\n"
                ".whois qz [@username] — Пробив по юзернейму\n"
                ".scan — Проверка файла на вирусы\n"
                ".scanurl [ссылка] — Проверка ссылки на вирусы/фишинг\n"
                ".sherlock [ник] — Поиск аккаунтов по никнейму\n"
                ".status — Статистика чата с пользователем",
                reply_markup=get_back_keyboard(),
                parse_mode="Markdown"
            )
            await callback.answer()
            return
        
        if data == "inf_admin":
            try:
                await callback.message.delete()
            except:
                pass
            await callback.message.answer(
                "*🛠️ АДМИН-КОМАНДЫ*\n\n"
                ".ban [ID] [время] [причина] [-s] [-g] — Забанить пользователя\n"
                ".unban [ID] [причина] [-g] — Разбанить пользователя\n"
                ".idlist — Список всех пользователей\n"
                ".logs [ID] — Логи пользователя\n"
                ".tex on/off — Включить/выключить техработы\n"
                ".stop [run/bot/max] max — Остановить раннеры",
                reply_markup=get_back_keyboard(),
                parse_mode="Markdown"
            )
            await callback.answer()
            return
        
        if data == "inf_fun":
            try:
                await callback.message.delete()
            except:
                pass
            await callback.message.answer(
                "*🌟 РАЗВЛЕКАТЕЛЬНЫЕ (1/4)*\n\n"
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
                reply_markup=get_fun_keyboard(1),
                parse_mode="Markdown"
            )
            await callback.answer()
            return
        
        if data == "fun_page_2":
            try:
                await callback.message.delete()
            except:
                pass
            await callback.message.answer(
                "*🌟 РАЗВЛЕКАТЕЛЬНЫЕ (2/4)*\n\n"
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
                reply_markup=get_fun_keyboard(2),
                parse_mode="Markdown"
            )
            await callback.answer()
            return
        
        if data == "inf_utils":
            try:
                await callback.message.delete()
            except:
                pass
            await callback.message.answer(
                "*🛠️ УТИЛИТЫ (1/3)*\n\n"
                ".mute [N] — мут (N мин или до .unmute)\n"
                ".unmute — выключить мут\n"
                ".nomute — обход мута (в чате даже под мутом; ЛС: /nomute)\n"
                ".unnomute — выключить обход мута\n"
                ".warn N — автомут после N сообщений\n"
                ".unwarn — снять warn\n"
                ".antispam on/off — антиспам\n"
                ".a_troll / .aut_troll / .stop_troll — троллинг",
                reply_markup=get_utils_keyboard(1),
                parse_mode="Markdown"
            )
            await callback.answer()
            return
        
        if data == "utils_page_2":
            try:
                await callback.message.delete()
            except:
                pass
            await callback.message.answer(
                "*🛠️ УТИЛИТЫ (2/3)*\n\n"
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
                    [
                        InlineKeyboardButton(text="⬅️ Назад", callback_data="utils_page_1"),
                        InlineKeyboardButton(text="📋 Меню", callback_data="show_inf"),
                        InlineKeyboardButton(text="❌ Закрыть", callback_data="close_menu")
                    ]
                ]),
                parse_mode="Markdown"
            )
            await callback.answer()
            return
        
        if data == "inf_all":
            try:
                await callback.message.delete()
            except:
                pass
            await callback.message.answer(
                "*📚 ВСЕ КОМАНДЫ*\n\n"
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
                reply_markup=get_back_keyboard(),
                parse_mode="Markdown"
            )
            await callback.answer()
            return
        
        if data == "close_menu":
            try:
                await callback.message.delete()
            except:
                pass
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
        
    except Exception as e:
        logger.error(f"❌ Ошибка callback: {e}")
        try:
            await callback.answer("❌ Ошибка", show_alert=True)
        except:
            pass

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
        
        result_text = f"✅ РЕЗУЛЬТАТ ПРОБИВА IP\n\n━━━━━━━━━━━━━━━━━━━━━━━━━━\nIP-адрес: {text}\nСтрана: {final['country']}\nРегион: {final['region']}\nГород: {final['city']}\nПровайдер: {final['isp']}\nОрганизация: {final['org']}\nAS: {final['as']}\nЧасовой пояс: {final['timezone']}\n━━━━━━━━━━━━━━━━━━━━━━━━━━\nОбработано: {success_count}/5 серверов"
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
        result_text = f"✅ РЕЗУЛЬТАТ ПРОБИВА НОМЕРА\n\n━━━━━━━━━━━━━━━━━━━━━━━━━━\nНомер: {final['formatted']}\nОператор: {final['operator']}\nРегион: {final['region']}\nЧасовой пояс: {final['timezone']}\nТип: {final['type']}\nКод страны: {final['country_code']}\n━━━━━━━━━━━━━━━━━━━━━━━━━━\nОбработано: {success_count} серверов"
        await edit_normal_message(message.chat.id, loading.message_id, result_text)
        await save_log_async({"command": f"Номер {text}", "user_id": user_id, "username": message.from_user.username or "Нет", "target": text, "time": get_msk_time()})
        return
    
    if text.startswith('@'):
        loading = await show_animation(message)
        data = await probe_username(text)
        result_text = f"✅ РЕЗУЛЬТАТ ПРОБИВА USERNAME\n\n━━━━━━━━━━━━━━━━━━━━━━━━━━\nUsername: {data['username']}\nID: {data['id']}\nИмя: {data['name']}\nСтатус: {data['status']}\n━━━━━━━━━━━━━━━━━━━━━━━━━━"
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
