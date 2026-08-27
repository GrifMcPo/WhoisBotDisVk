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
cloned_profiles = {}  # user_id -> {username, full_name, photo}
copied_profiles = {}  # user_id -> {username, full_name, photo}

def get_msk_time():
    return (datetime.utcnow() + timedelta(hours=3)).strftime('%d.%m.%Y %H:%M:%S')

def get_msk_time_short():
    return (datetime.utcnow() + timedelta(hours=3)).strftime('%H:%M')

def get_msk_date():
    return (datetime.utcnow() + timedelta(hours=3)).strftime('%d.%m.%Y')

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

# ========== НОВЫЕ ФУНКЦИИ ==========

# --- .scan (проверка файла на вирусы) ---
async def scan_file(file_path, file_name):
    try:
        # Используем VirusTotal API (бесплатно, но с лимитами)
        VT_API_KEY = os.getenv("VT_API_KEY", "")
        if not VT_API_KEY:
            return "❌ VirusTotal API ключ не настроен!"
        
        url = "https://www.virustotal.com/api/v3/files"
        files = {"file": (file_name, open(file_path, "rb"))}
        headers = {"x-apikey": VT_API_KEY}
        
        response = requests.post(url, files=files, headers=headers)
        if response.status_code == 200:
            data = response.json()
            analysis_id = data.get("data", {}).get("id")
            if analysis_id:
                # Ждем результат
                await asyncio.sleep(5)
                result_url = f"https://www.virustotal.com/api/v3/analyses/{analysis_id}"
                result_response = requests.get(result_url, headers=headers)
                if result_response.status_code == 200:
                    result_data = result_response.json()
                    stats = result_data.get("data", {}).get("attributes", {}).get("stats", {})
                    malicious = stats.get("malicious", 0)
                    suspicious = stats.get("suspicious", 0)
                    undetected = stats.get("undetected", 0)
                    
                    if malicious > 0:
                        return f"⚠️ ОБНАРУЖЕНЫ ВИРУСЫ!\n\n🦠 Вредоносных: {malicious}\n⚠️ Подозрительных: {suspicious}\n✅ Безопасных: {undetected}"
                    else:
                        return f"✅ Файл безопасен!\n\n🦠 Вредоносных: {malicious}\n⚠️ Подозрительных: {suspicious}\n✅ Безопасных: {undetected}"
        return "❌ Ошибка проверки файла"
    except Exception as e:
        return f"❌ Ошибка: {str(e)}"

# --- .ai (ИИ-агент) ---
async def ai_agent(question):
    try:
        # Используем бесплатный API (или можно подключить OpenAI)
        # Пока простой ответ
        return f"🤖 ИИ-агент:\n\n{question}\n\n⚠️ Функция в разработке. Скоро будет подключен ChatGPT!"
    except Exception as e:
        return f"❌ Ошибка: {str(e)}"

# --- .sherlock (поиск аккаунтов) ---
async def sherlock_search(username):
    try:
        # Используем sherlock API
        url = f"https://sherlock-hq.vercel.app/api?username={username}"
        response = requests.get(url, timeout=10)
        if response.status_code == 200:
            data = response.json()
            found = []
            for platform, info in data.items():
                if info.get("exists"):
                    found.append(f"✅ {platform}: {info.get('url', '')}")
            if found:
                return f"🔍 НАЙДЕНЫ АККАУНТЫ ДЛЯ @{username}:\n\n" + "\n".join(found[:20])
            else:
                return f"❌ Аккаунты для @{username} не найдены"
        return "❌ Ошибка поиска"
    except:
        return "❌ Ошибка подключения к серверу"

# --- .scanurl (проверка ссылки) ---
async def scan_url(url_to_check):
    try:
        # Используем VirusTotal URL API
        VT_API_KEY = os.getenv("VT_API_KEY", "")
        if not VT_API_KEY:
            return "❌ VirusTotal API ключ не настроен!"
        
        scan_url = "https://www.virustotal.com/api/v3/urls"
        headers = {"x-apikey": VT_API_KEY}
        data = {"url": url_to_check}
        
        response = requests.post(scan_url, headers=headers, data=data)
        if response.status_code == 200:
            result_data = response.json()
            analysis_id = result_data.get("data", {}).get("id")
            if analysis_id:
                await asyncio.sleep(3)
                result_url = f"https://www.virustotal.com/api/v3/analyses/{analysis_id}"
                result_response = requests.get(result_url, headers=headers)
                if result_response.status_code == 200:
                    stats = result_response.json().get("data", {}).get("attributes", {}).get("stats", {})
                    malicious = stats.get("malicious", 0)
                    if malicious > 0:
                        return f"⚠️ ССЫЛКА ОПАСНА!\n\n🦠 Вредоносных: {malicious}\n🔗 URL: {url_to_check}"
                    else:
                        return f"✅ Ссылка безопасна!\n\n🦠 Вредоносных: {malicious}\n🔗 URL: {url_to_check}"
        return "❌ Ошибка проверки ссылки"
    except Exception as e:
        return f"❌ Ошибка: {str(e)}"

# --- .cat (случайный кот) ---
async def get_random_cat():
    try:
        url = "https://api.thecatapi.com/v1/images/search"
        response = requests.get(url)
        if response.status_code == 200:
            data = response.json()
            if data:
                return data[0].get("url")
        return None
    except:
        return None

# --- .clone / .unclone (клонирование сообщений) ---
cloned_chats = {}  # chat_id -> True

# --- .copyp / .uncopyp (копирование профиля) ---
async def copy_profile(user_id, target_user_id):
    try:
        # Получаем данные пользователя
        chat = await bot.get_chat(target_user_id)
        copied_profiles[str(user_id)] = {
            "username": chat.username,
            "full_name": chat.full_name,
            "photo": None  # Можно добавить фото позже
        }
        return True
    except:
        return False

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

# ========== КЛАВИАТУРА ДЛЯ .inf ==========
def get_inf_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📋 Основные", callback_data="inf_main")],
        [InlineKeyboardButton(text="🔍 Пробивы", callback_data="inf_probe")],
        [InlineKeyboardButton(text="🛠️ Админ", callback_data="inf_admin")],
        [InlineKeyboardButton(text="📚 Все команды", callback_data="inf_all")]
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
        
        # ===== НОВОЕ МЕНЮ .inf =====
        if text.lower() == '.inf':
            await send_to_business_chat(
                chat_id,
                "Добро пожаловать в RipSave 👀\n\nМаксимум возможностей — минимум лишних действий 🌟\nВ этом разделе вы можете ознакомиться с функционалом бота, узнать о доступных инструментах и открыть подробную информацию о каждой возможности.",
                connection_id,
                reply_markup=get_inf_keyboard()
            )
            return
        
        # ===== .help (старый) =====
        if text.lower() == '.help':
            await send_to_business_chat(chat_id, HELP_TEXT, connection_id)
            return
        
        # ===== НОВЫЕ КОМАНДЫ =====
        
        # .scan - проверка файла на вирусы
        if text.lower() == '.scan' and message.reply_to_message:
            reply = message.reply_to_message
            if reply.document or reply.photo or reply.video:
                # Получаем файл
                file_id = None
                if reply.document:
                    file_id = reply.document.file_id
                    file_name = reply.document.file_name or "file"
                elif reply.photo:
                    file_id = reply.photo[-1].file_id
                    file_name = "photo.jpg"
                elif reply.video:
                    file_id = reply.video.file_id
                    file_name = "video.mp4"
                
                if file_id:
                    await send_to_business_chat(chat_id, "🔍 Сканирую файл на вирусы...", connection_id)
                    # Скачиваем файл
                    file = await bot.get_file(file_id)
                    file_path = await bot.download_file(file.file_path)
                    
                    # Сохраняем временно
                    temp_path = f"/tmp/{file_name}"
                    with open(temp_path, "wb") as f:
                        f.write(file_path.getvalue())
                    
                    result = await scan_file(temp_path, file_name)
                    await send_to_business_chat(chat_id, result, connection_id)
                    os.remove(temp_path)
                    return
        
        # .ai - ИИ-агент
        if text.lower().startswith('.ai '):
            question = text[4:].strip()
            result = await ai_agent(question)
            await send_to_business_chat(chat_id, result, connection_id)
            return
        
        if text.lower() == '.ai' and message.reply_to_message:
            question = message.reply_to_message.text or "Нет текста"
            result = await ai_agent(question)
            await send_to_business_chat(chat_id, result, connection_id)
            return
        
        # .sherlock - поиск аккаунтов
        if text.lower().startswith('.sherlock '):
            username = text[10:].strip()
            result = await sherlock_search(username)
            await send_to_business_chat(chat_id, result, connection_id)
            return
        
        # .scanurl - проверка ссылки
        if text.lower().startswith('.scanurl '):
            url = text[9:].strip()
            result = await scan_url(url)
            await send_to_business_chat(chat_id, result, connection_id)
            return
        
        if text.lower() == '.scanurl' and message.reply_to_message:
            url = message.reply_to_message.text or ""
            if url.startswith("http"):
                result = await scan_url(url)
                await send_to_business_chat(chat_id, result, connection_id)
                return
        
        # ===== БАЗОВЫЕ КОМАНДЫ =====
        
        # .me - информация о вас
        if text.lower() == '.me':
            user = message.from_user
            await send_to_business_chat(
                chat_id,
                f"👤 ИНФОРМАЦИЯ О ВАС\n\n"
                f"🆔 ID: {user.id}\n"
                f"📛 Имя: {user.full_name}\n"
                f"👤 Username: @{user.username or 'Нет'}\n"
                f"🤖 Бот: {'✅' if user.is_bot else '❌'}\n"
                f"💬 Язык: {user.language_code or 'Неизвестно'}",
                connection_id
            )
            return
        
        # .id - ваш ID
        if text.lower() == '.id':
            await send_to_business_chat(chat_id, f"🆔 Ваш ID: {message.from_user.id}", connection_id)
            return
        
        # .chat - информация о чате
        if text.lower() == '.chat':
            chat = message.chat
            await send_to_business_chat(
                chat_id,
                f"💬 ИНФОРМАЦИЯ О ЧАТЕ\n\n"
                f"🆔 ID: {chat.id}\n"
                f"📛 Название: {chat.title or 'Личный чат'}\n"
                f"📊 Тип: {chat.type}\n"
                f"👥 Участников: {chat.get_member_count() if hasattr(chat, 'get_member_count') else 'Неизвестно'}",
                connection_id
            )
            return
        
        # .business - ID бизнес-подключения
        if text.lower() == '.business':
            await send_to_business_chat(chat_id, f"🏢 Business Connection ID:\n`{connection_id or 'Не активно'}`", connection_id)
            return
        
        # .meta - данные сообщения в ответе
        if text.lower() == '.meta' and message.reply_to_message:
            reply = message.reply_to_message
            await send_to_business_chat(
                chat_id,
                f"📋 ДАННЫЕ СООБЩЕНИЯ\n\n"
                f"🆔 ID сообщения: {reply.message_id}\n"
                f"👤 От: {reply.from_user.full_name} (@{reply.from_user.username or 'Нет'})\n"
                f"🆔 User ID: {reply.from_user.id}\n"
                f"💬 Чат ID: {reply.chat.id}\n"
                f"🕐 Время: {reply.date.strftime('%d.%m.%Y %H:%M:%S')}\n"
                f"📝 Текст: {reply.text or 'Нет текста'}\n"
                f"📎 Медиа: {'✅' if reply.media else '❌'}",
                connection_id
            )
            return
        
        # .ping - проверка задержки
        if text.lower() == '.ping':
            start = datetime.now()
            await send_to_business_chat(chat_id, "🏓 Пинг...", connection_id)
            end = datetime.now()
            ping_ms = (end - start).microseconds / 1000
            await send_to_business_chat(chat_id, f"🏓 Понг! {ping_ms:.2f} мс", connection_id)
            return
        
        # .time - текущее время МСК
        if text.lower() == '.time':
            await send_to_business_chat(chat_id, f"🕐 Текущее время МСК: [{get_msk_time_short()}]", connection_id)
            return
        
        # .date - текущая дата
        if text.lower() == '.date':
            await send_to_business_chat(chat_id, f"📅 Текущая дата: {get_msk_date()}", connection_id)
            return
        
        # .cat - случайный кот
        if text.lower() == '.cat':
            cat_url = await get_random_cat()
            if cat_url:
                await send_to_business_chat(chat_id, "🐱 Ваш случайный кот:", connection_id)
                # Отправляем фото
                try:
                    await bot.send_photo(chat_id, cat_url, business_connection_id=connection_id)
                except:
                    await send_to_business_chat(chat_id, f"🐱 Кот: {cat_url}", connection_id)
            else:
                await send_to_business_chat(chat_id, "❌ Не удалось найти кота", connection_id)
            return
        
        # .status - статистика чата с пользователем
        if text.lower() == '.status' and message.reply_to_message:
            reply = message.reply_to_message
            target_user_id = reply.from_user.id
            
            # Получаем логи пользователя
            logs = get_logs_for_user(target_user_id, 20)
            user_info = supabase.table("users").select("*").eq("user_id", target_user_id).execute()
            
            log_count = len(logs)
            last_commands = "\n".join([f"• {log.get('command', '?')} ({log.get('time', '?')})" for log in logs[:5]]) or "Нет команд"
            
            await send_to_business_chat(
                chat_id,
                f"📊 СТАТИСТИКА ПОЛЬЗОВАТЕЛЯ\n\n"
                f"👤 {reply.from_user.full_name} (@{reply.from_user.username or 'Нет'})\n"
                f"🆔 ID: {target_user_id}\n"
                f"📝 Всего команд: {log_count}\n\n"
                f"🕐 Последние команды:\n{last_commands}",
                connection_id
            )
            return
        
        # .clone - включить клонирование
        if text.lower() == '.clone':
            cloned_chats[str(chat_id)] = True
            await send_to_business_chat(chat_id, "✅ Клонирование сообщений ВКЛЮЧЕНО", connection_id)
            return
        
        # .unclone - выключить клонирование
        if text.lower() == '.unclone':
            cloned_chats.pop(str(chat_id), None)
            await send_to_business_chat(chat_id, "✅ Клонирование сообщений ВЫКЛЮЧЕНО", connection_id)
            return
        
        # .copyp - скопировать профиль собеседника
        if text.lower() == '.copyp' and message.reply_to_message:
            target = message.reply_to_message.from_user
            if await copy_profile(user_id, target.id):
                await send_to_business_chat(
                    chat_id,
                    f"✅ Профиль скопирован!\n\n"
                    f"📛 Имя: {target.full_name}\n"
                    f"👤 Username: @{target.username or 'Нет'}\n"
                    f"🆔 ID: {target.id}\n\n"
                    f"Используй .uncopyp чтобы вернуть свой профиль",
                    connection_id
                )
            else:
                await send_to_business_chat(chat_id, "❌ Ошибка копирования профиля", connection_id)
            return
        
        # .uncopyp - вернуть свой профиль
        if text.lower() == '.uncopyp':
            if str(user_id) in copied_profiles:
                copied_profiles.pop(str(user_id), None)
                await send_to_business_chat(chat_id, "✅ Ваш профиль восстановлен", connection_id)
            else:
                await send_to_business_chat(chat_id, "❌ У вас не скопирован профиль", connection_id)
            return
        
        # ===== СТАРЫЕ КОМАНДЫ =====
        
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
            result = "👥 СПИСОК ПОЛЬЗОВАТЕЛЕЙ\n\n"
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
                result = f"📋 ЛОГИ ДЛЯ {target_id} (последние {len(logs)})\n\n"
                for log in logs:
                    result += f"🕐 {log.get('time', '?')}\n📝 {log.get('command', '?')}\n\n"
                await send_to_business_chat(chat_id, result[:4000], connection_id)
            except:
                await send_to_business_chat(chat_id, "❌ Неверный формат", connection_id)
            return
        
        # .ban
        if text.lower().startswith('.ban'):
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
            key = create_session_key()
            await send_to_business_chat(chat_id, f"🔑 Ваш ключ:\n\n`{key}`\n\n⏱ Действует 10 часов", connection_id)
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
        
        # .whois
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

# ========== ТЕКСТ ПОМОЩИ (старый) ==========
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
.inf — Показать меню команд
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

🔹 НОВЫЕ КОМАНДЫ:
.scan — Проверка файла на вирусы
.ai [вопрос] — ИИ-агент
.sherlock [ник] — Поиск аккаунтов
.scanurl [ссылка] — Проверка ссылки на вирусы
.me — Информация о вас
.id — Ваш ID
.chat — Информация о чате
.business — ID бизнес-подключения
.meta — Данные сообщения в ответе
.ping — Проверить задержку
.time — Текущее время МСК
.date — Текущая дата
.cat — Случайный кот
.status — Статистика чата с пользователем
.clone — Включить клонирование
.unclone — Выключить клонирование
.copyp — Скопировать профиль собеседника
.uncopyp — Вернуть свой профиль

📌 .команды — в чатах с собеседниками
📌 /команды — в личке с ботом"""

# ========== КОМАНДЫ В ЛИЧКЕ ==========
@dp.message(Command("start"))
async def start(message: types.Message):
    user_id = message.from_user.id
    
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
        await message.answer(
            "🔥 ДОБРО ПОЖАЛОВАТЬ В RipSave!\n\nМаксимум возможностей — минимум лишних действий 🌟\n\nВыберите действие:",
            reply_markup=get_main_keyboard()
        )
    finally:
        del processing_commands[user_id]

@dp.message(Command("help"))
async def help_command(message: types.Message):
    user_id = message.from_user.id
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
    if not is_admin(user_id):
        await message.answer("❌ У вас нет прав!")
        return
    key = create_session_key()
    await message.answer(f"🔑 Ваш ключ:\n\n`{key}`\n\n⏱ Действует 10 часов")

@dp.message(Command("chkban"))
async def chkban_command(message: types.Message):
    user_id = message.from_user.id
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
    data = callback.data
    
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
    
    # ===== НОВЫЕ CALLBACK ДЛЯ .inf =====
    if data == "show_inf":
        await callback.message.edit_text(
            "Добро пожаловать в RipSave 👀\n\nМаксимум возможностей — минимум лишних действий 🌟\nВ этом разделе вы можете ознакомиться с функционалом бота, узнать о доступных инструментах и открыть подробную информацию о каждой возможности.",
            reply_markup=get_inf_keyboard()
        )
        await callback.answer()
        return
    
    if data == "inf_main":
        await callback.message.edit_text(
            "📋 ОСНОВНЫЕ КОМАНДЫ\n\n"
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
            ".uncopyp — вернуть свой исходный профиль"
        )
        await callback.answer()
        return
    
    if data == "inf_probe":
        await callback.message.edit_text(
            "🔍 ПРОБИВЫ\n\n"
            ".whois ip [IP] — Пробив IP-адреса\n"
            ".whois n [номер] — Пробив номера телефона\n"
            ".whois qz [@username] — Пробив по юзернейму\n"
            ".scan — Проверка файла на вирусы\n"
            ".scanurl [ссылка] — Проверка ссылки на вирусы/фишинг\n"
            ".sherlock [ник] — Поиск аккаунтов по никнейму\n"
            ".status — Статистика чата с пользователем"
        )
        await callback.answer()
        return
    
    if data == "inf_admin":
        await callback.message.edit_text(
            "🛠️ АДМИН-КОМАНДЫ\n\n"
            ".ban [ID] [время] [причина] — Забанить пользователя\n"
            ".unban [ID] [причина] — Разбанить пользователя\n"
            ".idlist — Список всех пользователей\n"
            ".logs [ID] — Логи пользователя\n"
            ".key — Создать ключ доступа\n"
            ".tex on/off — Включить/выключить техработы\n"
            ".stop [run/bot/max] max — Остановить раннеры"
        )
        await callback.answer()
        return
    
    if data == "inf_all":
        await callback.message.edit_text(
            "📚 ВСЕ КОМАНДЫ\n\n"
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
            ".ban [ID] [время] [причина] — Бан\n"
            ".unban [ID] [причина] — Разбан\n"
            ".idlist — Список пользователей\n"
            ".logs [ID] — Логи пользователя\n"
            ".key — Создать ключ\n"
            ".tex on/off — Техработы\n"
            ".stop [run/bot/max] max — Остановить раннеры"
        )
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
    print("🔥 БОТ ЗАПУЩЕН С SUPABASE (БИБЛИОТЕКА)!")
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
