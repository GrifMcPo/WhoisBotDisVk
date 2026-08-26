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
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram import F
import aiohttp

# ===== НАСТРОЙКА ЛОГИРОВАНИЯ =====
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ===== СЕКРЕТЫ =====
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", 0))

# ===== SUPABASE (HTTP КЛИЕНТ) =====
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

# ===== HTTP КЛИЕНТ ДЛЯ SUPABASE =====
class SupabaseClient:
    def __init__(self, url, key):
        self.url = url.rstrip('/')
        self.key = key
        self.headers = {
            "apikey": key,
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Prefer": "return=representation"
        }
    
    def table(self, name):
        return SupabaseTable(self, name)

class SupabaseTable:
    def __init__(self, client, name):
        self.client = client
        self.name = name
        self._select = "*"
        self._filters = []
        self._eq_filters = {}
        self._order = None
        self._limit = None
    
    def select(self, columns):
        self._select = columns
        return self
    
    def eq(self, column, value):
        self._eq_filters[column] = value
        return self
    
    def neq(self, column, value):
        self._filters.append(f"{column}=neq.{value}")
        return self
    
    def order(self, column, desc=False):
        self._order = f"{column}.{'desc' if desc else 'asc'}"
        return self
    
    def limit(self, count):
        self._limit = count
        return self
    
    def execute(self):
        url = f"{self.client.url}/rest/v1/{self.name}"
        params = {"select": self._select}
        
        for col, val in self._eq_filters.items():
            params[col] = f"eq.{val}"
        
        if self._filters:
            params["and"] = ",".join(self._filters)
        if self._order:
            params["order"] = self._order
        if self._limit:
            params["limit"] = self._limit
        
        response = requests.get(url, headers=self.client.headers, params=params)
        return type('obj', (object,), {'data': response.json()})()

supabase = SupabaseClient(SUPABASE_URL, SUPABASE_KEY)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

business_connections = {}
blocked_notified = {}
processing_commands = {}

def get_msk_time():
    return (datetime.utcnow() + timedelta(hours=3)).strftime('%d.%m.%Y %H:%M:%S')

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

# ========== SUPABASE ФУНКЦИИ ==========

# --- ЛОГИ ---
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

# --- БАНЫ ---
def add_ban(user_id, reason, admin_id, time_minutes=None):
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
            "expires_at": expires_at
        }).execute()
        if str(user_id) in blocked_notified:
            del blocked_notified[str(user_id)]
        return True
    except:
        return False

def remove_ban(user_id):
    try:
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

# --- КЛЮЧИ ---
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

# --- ТЕХРАБОТЫ ---
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

# ========== АНИМАЦИЯ ==========
async def show_animation(target, connection_id=None):
    stages = [
        "🔄 ПОДКЛЮЧЕНИЕ К СЕРВЕРАМ\n\n📡 Сервер #1... ████░░░░░░ 40%\n📡 Сервер #2... ░░░░░░░░░░ 0%\n📡 Сервер #3... ░░░░░░░░░░ 0%\n📡 Сервер #4... ░░░░░░░░░░ 0%\n📡 Сервер #5... ░░░░░░░░░░ 0%\n\n⏳ Ожидайте...",
        "🔄 ПОДКЛЮЧЕНИЕ К СЕРВЕРАМ\n\n📡 Сервер #1... ████████░░ 80%\n📡 Сервер #2... ██████░░░░ 60%\n📡 Сервер #3... ████░░░░░░ 40%\n📡 Сервер #4... ██░░░░░░░░ 20%\n📡 Сервер #5... ░░░░░░░░░░ 0%\n\n⏳ Ожидайте...",
        "🔄 ПОДКЛЮЧЕНИЕ К СЕРВЕРАМ\n\n📡 Сервер #1... ██████████ 100% ✅\n📡 Сервер #2... ██████████ 100% ✅\n📡 Сервер #3... ████████░░ 80%\n📡 Сервер #4... ██████░░░░ 60%\n📡 Сервер #5... ████░░░░░░ 40%\n\n⏳ Ожидайте...",
        "✅ ПОДКЛЮЧЕНИЕ ВЫПОЛНЕНО\n\n📊 Получение данных...\n⏳ Обработка информации..."
    ]
    
    if connection_id:
        msg = await send_to_business_chat(target, stages[0], connection_id)
        for stage in stages[1:]:
            await asyncio.sleep(0.4)
            await edit_business_message(target, msg.message_id, stage, connection_id)
        return msg
    else:
        msg = await target.answer(stages[0])
        for stage in stages[1:]:
            await asyncio.sleep(0.4)
            await edit_normal_message(target, msg.message_id, stage)
        return msg

# ========== КЛАВИАТУРА ==========
def get_main_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🌐 ПРОБИВ IP", callback_data="probe_ip")],
        [InlineKeyboardButton(text="📱 ПРОБИВ НОМЕРА", callback_data="probe_phone")],
        [InlineKeyboardButton(text="👤 ПРОБИВ ЮЗЕРА", callback_data="probe_user")],
    ])

# ========== ОБРАБОТЧИК ВСЕХ СООБЩЕНИЙ (ДИАГНОСТИКА) ==========
@dp.message()
async def catch_all_messages(message: types.Message):
    """Ловит все сообщения для диагностики"""
    user_id = message.from_user.id
    chat_id = message.chat.id
    text = message.text or "[НЕТ ТЕКСТА]"
    connection_id = message.business_connection_id
    
    # ПРОВЕРЯЕМ BUSINESS CONNECTION ID
    has_business = "✅" if connection_id else "❌"
    
    logger.info(f"📩 ВСЕ СООБЩЕНИЕ: user={user_id}, chat={chat_id}, has_business={has_business}, text={text[:50]}")
    
    # Если это бизнес-сообщение — логируем отдельно
    if connection_id:
        logger.info(f"🏢 БИЗНЕС СООБЩЕНИЕ: connection={connection_id}, text={text}")
        
        # Проверяем, зарегистрировано ли подключение
        if str(user_id) in business_connections:
            logger.info(f"✅ Бизнес-подключение найдено в словаре для user={user_id}")
        else:
            logger.info(f"❌ Бизнес-подключение НЕ найдено в словаре для user={user_id}")
            
            # Добавляем в словарь если админ
            if is_admin(user_id):
                business_connections[str(user_id)] = connection_id
                logger.info(f"✅ Добавил бизнес-подключение в словарь для user={user_id}")
                await bot.send_message(
                    chat_id=user_id,
                    text=f"✅ БОТ ПОДКЛЮЧЕН К БИЗНЕС-АККАУНТУ!\n\n🆔 ID: {user_id}\n📌 Команды работают в чатах с собеседниками!\n🔥 Введите .help для списка команд"
                )
    
    # Пропускаем команды / (они обрабатываются отдельно)
    if text and text.startswith('/'):
        logger.info(f"⏭️ Команда / пропущена (обрабатывается отдельно)")
        return
    
    # Если сообщение с бизнес-коннектом и начинается с .
    if connection_id and text and text.startswith('.'):
        logger.info(f"🎯 ОБНАРУЖЕНА БИЗНЕС-КОМАНДА: {text}")
        
        # Проверяем админа
        if not is_admin(user_id):
            logger.info(f"⛔ Не админ: {user_id}")
            await send_to_business_chat(chat_id, "❌ У вас нет прав!", connection_id)
            return
        
        # Обрабатываем команду
        await handle_business_command(message, text, connection_id)
        return
    
    # Если сообщение без бизнес-коннекта (личка) и не начинается с /
    if not connection_id and text and not text.startswith('/'):
        logger.info(f"💬 ЛИЧНОЕ СООБЩЕНИЕ: {text[:50]}")
        # Обрабатываем как личное сообщение
        await handle_private_message(message)
        return

# ========== ОБРАБОТЧИК БИЗНЕС-КОМАНД ==========
async def handle_business_command(message: types.Message, text: str, connection_id: str):
    """Обрабатывает бизнес-команды"""
    user_id = message.from_user.id
    chat_id = message.chat.id
    
    logger.info(f"⚙️ Обработка бизнес-команды: {text}")
    
    try:
        await delete_business_message(chat_id, message.message_id, connection_id)
        
        # .help
        if text.lower() == '.help':
            logger.info("📚 .help команда")
            await send_to_business_chat(chat_id, HELP_TEXT, connection_id)
            return
        
        # .stop
        if text.lower().startswith('.stop'):
            logger.info("🛑 .stop команда")
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
            logger.info("👥 .idlist команда")
            users = get_all_users()
            if not users:
                await send_to_business_chat(chat_id, "📊 Список пользователей пуст", connection_id)
                return
            result = "👥 СПИСОК ПОЛЬЗОВАТЕЛЕЙ\n\n"
            for user in users:
                result += f"🆔 {user.get('user_id', '?')} → @{user.get('username', 'Нет')}\n"
            await send_to_business_chat(chat_id, result[:4000], connection_id)
            return
        
        # .logs
        if text.lower().startswith('.logs'):
            logger.info("📋 .logs команда")
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
        
        # .ban
        if text.lower().startswith('.ban'):
            logger.info("🔨 .ban команда")
            parts = text.split(maxsplit=3)
            if len(parts) < 3:
                await send_to_business_chat(chat_id, "❌ .ban [ID] [время] [причина]", connection_id)
                return
            target_id = parts[1]
            time_str = parts[2]
            reason = parts[3] if len(parts) > 3 else "Без причины"
            minutes, time_display = parse_time(time_str)
            add_ban(target_id, reason, user_id, minutes)
            await send_to_business_chat(chat_id, f"✅ ПОЛЬЗОВАТЕЛЬ ЗАБАНЕН\n\n🆔 ID: {target_id}\n📌 Причина: {reason}\n⏱ Время: {time_display}\n🕐 Дата: {get_msk_time()}", connection_id)
            try:
                ban_msg = f"⛔ ВАС ЗАБЛОКИРОВАЛИ В БОТЕ\n\n📌 Причина: {reason}\n⏱ Длительность: {time_display}\n🕐 Дата блокировки: {get_msk_time()}"
                if minutes:
                    ban_msg += f"\n⏳ Разблокировка: {(datetime.now() + timedelta(minutes=minutes)).strftime('%d.%m.%Y %H:%M')}"
                await bot.send_message(chat_id=int(target_id), text=ban_msg)
            except:
                pass
            await save_log_async({"command": f".ban {target_id}", "user_id": user_id, "username": message.from_user.username or "Нет", "target": target_id, "reason": reason, "time": get_msk_time()})
            return
        
        # .unban
        if text.lower().startswith('.unban'):
            logger.info("🔓 .unban команда")
            parts = text.split(maxsplit=2)
            if len(parts) < 2:
                await send_to_business_chat(chat_id, "❌ .unban [ID] [причина]", connection_id)
                return
            target_id = parts[1]
            reason = parts[2] if len(parts) > 2 else "Без причины"
            if remove_ban(target_id):
                await send_to_business_chat(chat_id, f"✅ ПОЛЬЗОВАТЕЛЬ РАЗБАНЕН\n\n🆔 ID: {target_id}\n📌 Причина: {reason}\n🕐 Дата: {get_msk_time()}", connection_id)
                try:
                    await bot.send_message(chat_id=int(target_id), text=f"✅ ВАС РАЗБЛОКИРОВАЛИ\n\n📌 Причина: {reason}\n🕐 Дата: {get_msk_time()}\n🔓 Теперь вы снова можете пользоваться ботом")
                except:
                    pass
            else:
                await send_to_business_chat(chat_id, f"❌ Пользователь {target_id} не найден в черном списке", connection_id)
            return
        
        # .key
        if text.lower() == '.key':
            logger.info("🔑 .key команда")
            key = create_session_key()
            await send_to_business_chat(chat_id, f"🔑 Ваш ключ:\n\n`{key}`\n\n⏱ Действует 10 часов", connection_id)
            return
        
        # .tex on
        if text.lower().startswith('.tex on'):
            logger.info("🛠️ .tex on команда")
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
            logger.info("🛠️ .tex off команда")
            set_tech_mode(False, None)
            await send_to_business_chat(chat_id, "✅ ТЕХ-РАБОТЫ ВЫКЛЮЧЕНЫ", connection_id)
            return
        
        # .whois
        if text.lower().startswith('.whois'):
            logger.info("🔍 .whois команда")
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
        
        # Если команда не распознана
        logger.info(f"❓ Неизвестная бизнес-команда: {text}")
        await send_to_business_chat(chat_id, f"❓ Неизвестная команда\n\n📌 Введи .help для списка команд", connection_id)
        
    except Exception as e:
        logger.error(f"❌ Ошибка обработки бизнес-команды: {e}")
        await send_to_business_chat(chat_id, f"❌ Ошибка: {e}", connection_id)

# ========== ТЕКСТ ПОМОЩИ ==========
HELP_TEXT = """📚 СПИСОК КОМАНД

🔹 В ЛИЧКЕ (с /):
/start — Главное меню
/help — Справка
/whois — Пробив
/idlist — Пользователи (админ)
/logs (ID) — Логи (админ)
/ban — Бан (админ)
/unban — Разбан (админ)
/key — Ключ (админ)
/stop — Остановить раннеры (админ)

🔹 В ЧАТАХ (с .):
.help — Справка
.idlist — Пользователи
.logs (ID) — Логи
.whois ip [IP] — Пробив IP
.whois n [номер] — Пробив номера
.whois qz [@username] — Пробив юзера
.ban [ID] [время] [причина] — Бан
.unban [ID] [причина] — Разбан
.key — Ключ
.tex on/off — Техработы
.stop run max — Остановить все раннеры (кроме бота)
.stop bot max — Остановить все раннеры бота
.stop max max — Остановить ВСЕ раннеры

📌 .команды — в чатах с собеседниками
📌 /команды — в личке с ботом"""

# ========== КОМАНДЫ В ЛИЧКЕ ==========
@dp.message(Command("start"))
async def start(message: types.Message):
    user_id = message.from_user.id
    logger.info(f"📩 /start от {user_id}")
    
    if user_id in processing_commands and processing_commands[user_id] == "start":
        return
    processing_commands[user_id] = "start"
    
    try:
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
        
        await save_log_async({"command": "/start", "user_id": user_id, "username": message.from_user.username or "Нет", "time": get_msk_time()})
        await message.answer("🔥 ДОБРО ПОЖАЛОВАТЬ!\n\nВыберите действие:", reply_markup=get_main_keyboard())
    finally:
        del processing_commands[user_id]

@dp.message(Command("help"))
async def help_command(message: types.Message):
    user_id = message.from_user.id
    logger.info(f"📩 /help от {user_id}")
    if is_banned(user_id):
        if str(user_id) not in blocked_notified:
            ban_info = get_ban_info(user_id)
            reason = ban_info.get("reason", "Не указана") if ban_info else "Не указана"
            await message.answer(f"⛔ ВАС ЗАБЛОКИРОВАЛИ В БОТЕ!\n\n📌 Причина: {reason}")
            blocked_notified[str(user_id)] = True
        return
    await message.answer(HELP_TEXT)

@dp.message(Command("stop"))
async def stop_command(message: types.Message):
    user_id = message.from_user.id
    logger.info(f"📩 /stop от {user_id}")
    if not is_admin(user_id):
        await message.answer("❌ У вас нет прав!")
        return
    
    args = message.text.split(maxsplit=2)
    if len(args) < 3:
        await message.answer("❌ /stop [run/bot/max] [max]")
        return
    
    target = args[1].lower()
    action = args[2].lower()
    
    if target not in ['run', 'bot', 'max'] or action != 'max':
        await message.answer("❌ /stop [run/bot/max] [max]")
        return
    
    result = await stop_runners(target, user_id, message.from_user.username)
    await message.answer(result)

@dp.message(Command("whois"))
async def whois_command(message: types.Message):
    user_id = message.from_user.id
    logger.info(f"📩 /whois от {user_id}")
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
    await message.answer("🔍 Выберите тип пробива:", reply_markup=get_main_keyboard())

@dp.message(Command("idlist"))
async def idlist_command(message: types.Message):
    user_id = message.from_user.id
    logger.info(f"📩 /idlist от {user_id}")
    if not is_admin(user_id):
        await message.answer("❌ У вас нет прав!")
        return
    users = get_all_users()
    if not users:
        await message.answer("📊 Список пользователей пуст")
        return
    result = "👥 СПИСОК ПОЛЬЗОВАТЕЛЕЙ\n\n"
    for user in users:
        result += f"🆔 {user.get('user_id', '?')} → @{user.get('username', 'Нет')}\n"
    await message.answer(result[:4000])

@dp.message(Command("logs"))
async def logs_command(message: types.Message):
    user_id = message.from_user.id
    logger.info(f"📩 /logs от {user_id}")
    if not is_admin(user_id):
        await message.answer("❌ У вас нет прав!")
        return
    args = message.text.split(maxsplit=2)
    if len(args) < 2:
        await message.answer("❌ /logs [ID] [кол-во]")
        return
    try:
        target_id = int(args[1])
        count = min(int(args[2]) if len(args) > 2 else 10, 50)
        logs = get_logs_for_user(target_id, count)
        if not logs:
            await message.answer(f"📊 Логов для {target_id} нет")
            return
        result = f"📋 ЛОГИ ДЛЯ {target_id} (последние {len(logs)})\n\n"
        for log in logs:
            result += f"🕐 {log.get('time', '?')}\n📝 {log.get('command', '?')}\n\n"
        await message.answer(result[:4000])
    except:
        await message.answer("❌ Неверный формат")

@dp.message(Command("ban"))
async def ban_command(message: types.Message):
    user_id = message.from_user.id
    logger.info(f"📩 /ban от {user_id}")
    if not is_admin(user_id):
        await message.answer("❌ У вас нет прав!")
        return
    args = message.text.split(maxsplit=3)
    if len(args) < 3:
        await message.answer("❌ /ban [ID] [время] [причина]")
        return
    target_id = args[1]
    time_str = args[2]
    reason = args[3] if len(args) > 3 else "Без причины"
    minutes, time_display = parse_time(time_str)
    add_ban(target_id, reason, user_id, minutes)
    await message.answer(f"✅ ПОЛЬЗОВАТЕЛЬ ЗАБАНЕН\n\n🆔 ID: {target_id}\n📌 Причина: {reason}\n⏱ Время: {time_display}\n🕐 Дата: {get_msk_time()}")
    try:
        ban_msg = f"⛔ ВАС ЗАБЛОКИРОВАЛИ В БОТЕ\n\n📌 Причина: {reason}\n⏱ Длительность: {time_display}\n🕐 Дата блокировки: {get_msk_time()}"
        if minutes:
            ban_msg += f"\n⏳ Разблокировка: {(datetime.now() + timedelta(minutes=minutes)).strftime('%d.%m.%Y %H:%M')}"
        await bot.send_message(chat_id=int(target_id), text=ban_msg)
    except:
        pass
    await save_log_async({"command": f"/ban {target_id}", "user_id": user_id, "username": message.from_user.username or "Нет", "target": target_id, "reason": reason, "time": get_msk_time()})

@dp.message(Command("unban"))
async def unban_command(message: types.Message):
    user_id = message.from_user.id
    logger.info(f"📩 /unban от {user_id}")
    if not is_admin(user_id):
        await message.answer("❌ У вас нет прав!")
        return
    args = message.text.split(maxsplit=2)
    if len(args) < 2:
        await message.answer("❌ /unban [ID] [причина]")
        return
    target_id = args[1]
    reason = args[2] if len(args) > 2 else "Без причины"
    if remove_ban(target_id):
        await message.answer(f"✅ ПОЛЬЗОВАТЕЛЬ РАЗБАНЕН\n\n🆔 ID: {target_id}\n📌 Причина: {reason}\n🕐 Дата: {get_msk_time()}")
        try:
            await bot.send_message(chat_id=int(target_id), text=f"✅ ВАС РАЗБЛОКИРОВАЛИ\n\n📌 Причина: {reason}\n🕐 Дата: {get_msk_time()}\n🔓 Теперь вы снова можете пользоваться ботом")
        except:
            pass
    else:
        await message.answer(f"❌ Пользователь {target_id} не найден в черном списке")

@dp.message(Command("key"))
async def key_command(message: types.Message):
    user_id = message.from_user.id
    logger.info(f"📩 /key от {user_id}")
    if not is_admin(user_id):
        await message.answer("❌ У вас нет прав!")
        return
    key = create_session_key()
    await message.answer(f"🔑 Ваш ключ:\n\n`{key}`\n\n⏱ Действует 10 часов")

@dp.message(Command("chkban"))
async def chkban_command(message: types.Message):
    user_id = message.from_user.id
    logger.info(f"📩 /chkban от {user_id}")
    if not is_admin(user_id):
        await message.answer("❌ У вас нет прав!")
        return
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.answer("❌ /chkban [ID]")
        return
    target_id = args[1]
    ban_info = get_ban_info(target_id)
    if ban_info:
        await message.answer(f"---{target_id}---\n📌 Причина: {ban_info.get('reason', 'Не указана')}\n🕐 Дата выдачи: {ban_info.get('added_at', 'Неизвестно')}\n🕐 Дата снятия: {ban_info.get('expires_at', 'НАВСЕГДА')}")
    else:
        await message.answer(f"⛔ {target_id} не заблокирован.")

@dp.message(Command("tex"))
async def tex_command(message: types.Message):
    user_id = message.from_user.id
    logger.info(f"📩 /tex от {user_id}")
    if not is_admin(user_id):
        await message.answer("❌ У вас нет прав!")
        return
    args = message.text.split(maxsplit=2)
    if len(args) < 2:
        await message.answer("❌ /tex on [время] или /tex off")
        return
    action = args[1].lower()
    if action == "on":
        if len(args) < 3:
            await message.answer("❌ /tex on [время]")
            return
        minutes, _ = parse_time(args[2])
        expires_at = (datetime.now() + timedelta(minutes=minutes)).isoformat()
        set_tech_mode(True, expires_at)
        await message.answer(f"✅ ТЕХ-РАБОТЫ ВКЛЮЧЕНЫ\n🕐 Окончание: {(datetime.now() + timedelta(minutes=minutes)).strftime('%d.%m.%Y %H:%M')}")
    elif action == "off":
        set_tech_mode(False, None)
        await message.answer("✅ ТЕХ-РАБОТЫ ВЫКЛЮЧЕНЫ")
    else:
        await message.answer("❌ /tex on [время] или /tex off")

# ========== CALLBACK ==========
@dp.callback_query()
async def handle_callback(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    logger.info(f"📩 Callback от {user_id}: {callback.data}")
    
    if is_banned(user_id):
        if str(user_id) not in blocked_notified:
            ban_info = get_ban_info(user_id)
            reason = ban_info.get("reason", "Не указана") if ban_info else "Не указана"
            await callback.message.answer(f"⛔ ВАС ЗАБЛОКИРОВАЛИ В БОТЕ!\n\n📌 Причина: {reason}")
            blocked_notified[str(user_id)] = True
        await callback.answer()
        return
    
    if is_tech_mode() and not is_admin(user_id):
        tech_info = get_tech_info()
        await callback.message.answer(f"🛠️ БОТ НА ТЕХНИЧЕСКИХ РАБОТАХ\n\n🕐 ВРЕМЯ: {tech_info.get('expires_at', 'Неизвестно')}")
        await callback.answer()
        return
    
    data = callback.data
    if data == "probe_ip":
        await callback.message.answer("🌐 ВВЕДИТЕ IP\n📌 Пример: 8.8.8.8")
    elif data == "probe_phone":
        await callback.message.answer("📱 ВВЕДИТЕ НОМЕР\n📌 Пример: 89001234567")
    elif data == "probe_user":
        await callback.message.answer("👤 ВВЕДИТЕ @USERNAME\n📌 Пример: @username")
    await callback.answer()

# ========== ЛИЧНЫЕ СООБЩЕНИЯ ==========
async def handle_private_message(message: types.Message):
    """Обрабатывает личные сообщения (без бизнес-коннекта)"""
    user_id = message.from_user.id
    
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
    print("🔥 БОТ ЗАПУЩЕН С SUPABASE!")
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
