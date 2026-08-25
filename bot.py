import asyncio
import os
import sys
import json
import logging
import re
import requests
import ipaddress
import phonenumbers
import base64
from phonenumbers import carrier, geocoder, timezone, number_type
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, BusinessConnection
from aiogram import F
import aiohttp
import asyncio

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ===== СЕКРЕТЫ =====
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", 0))
GH_TOKEN = os.getenv("GH_TOKEN", "")

if not BOT_TOKEN:
    print("❌ Токен не найден!")
    sys.exit(1)

if not ADMIN_ID:
    print("❌ ADMIN_ID не найден!")
    sys.exit(1)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# ===== ФАЙЛЫ =====
LOGS_FILE = "data/logs.json"
BANLIST_FILE = "data/banlist.json"
IDLIST_FILE = "data/idlist.json"
KEYS_FILE = "data/keys.json"
TECH_FILE = "data/tech.json"

# ===== GITHUB API =====
REPO = "GrifMcPo/WhoisBotDisVk"
BRANCH = "main"

business_connections = {}
blocked_notified = {}
processing_commands = {}

# ===== ОЧЕРЕДЬ ДЛЯ ЛОГОВ (ФОНОВАЯ) =====
log_queue = asyncio.Queue()

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

# ========== ФОНОВОЕ СОХРАНЕНИЕ ЛОГОВ ==========
async def log_worker():
    """Фоновый процесс для сохранения логов без блокировки бота"""
    while True:
        try:
            log_entry = await log_queue.get()
            await asyncio.to_thread(save_log_sync, log_entry)
            log_queue.task_done()
        except Exception as e:
            print(f"❌ Ошибка фонового лога: {e}")

def save_log_sync(log_entry):
    """Синхронное сохранение лога (для фонового потока)"""
    try:
        logs = []
        if os.path.exists(LOGS_FILE):
            try:
                with open(LOGS_FILE, 'r', encoding='utf-8') as f:
                    content = f.read().strip()
                    if content:
                        logs = json.loads(content)
                        if not isinstance(logs, list):
                            logs = []
            except:
                logs = []
        
        logs.append(log_entry)
        
        with open(LOGS_FILE, 'w', encoding='utf-8') as f:
            json.dump(logs, f, indent=2, ensure_ascii=False)
        
        # Сохраняем idlist
        save_idlist_sync(log_entry.get("user_id"), log_entry.get("username"))
        return True
    except Exception as e:
        print(f"❌ Ошибка сохранения лога: {e}")
        return False

def save_idlist_sync(user_id, username):
    try:
        idlist = []
        if os.path.exists(IDLIST_FILE):
            try:
                with open(IDLIST_FILE, 'r', encoding='utf-8') as f:
                    idlist = json.load(f)
                    if not isinstance(idlist, list):
                        idlist = []
            except:
                idlist = []
        
        for item in idlist:
            if item.get("id") == user_id:
                item["username"] = username
                break
        else:
            idlist.append({"id": user_id, "username": username})
        
        with open(IDLIST_FILE, 'w', encoding='utf-8') as f:
            json.dump(idlist, f, indent=2, ensure_ascii=False)
    except:
        pass

async def save_log_async(log_entry):
    """Асинхронное сохранение лога (ставит в очередь)"""
    await log_queue.put(log_entry)

# ========== GITHUB API (АСИНХРОННЫЙ) ==========
async def write_to_github_async(file_path, content, message="Update file"):
    if not GH_TOKEN:
        return await asyncio.to_thread(save_local_file, file_path, content)
    
    try:
        url = f"https://api.github.com/repos/{REPO}/contents/{file_path}"
        headers = {
            "Authorization": f"token {GH_TOKEN}",
            "Accept": "application/vnd.github.v3+json"
        }
        
        sha = None
        try:
            response = requests.get(url, headers=headers)
            if response.status_code == 200:
                sha = response.json().get("sha")
        except:
            pass
        
        if isinstance(content, dict) or isinstance(content, list):
            content_str = json.dumps(content, indent=2, ensure_ascii=False)
        else:
            content_str = content
        
        content_base64 = base64.b64encode(content_str.encode('utf-8')).decode('utf-8')
        
        data = {
            "message": message,
            "content": content_base64,
            "branch": BRANCH
        }
        if sha:
            data["sha"] = sha
        
        response = requests.put(url, headers=headers, json=data)
        
        if response.status_code in [200, 201]:
            print(f"✅ Файл {file_path} записан в GitHub")
            return True
        else:
            return await asyncio.to_thread(save_local_file, file_path, content)
    except Exception as e:
        print(f"❌ Ошибка записи в GitHub: {e}")
        return await asyncio.to_thread(save_local_file, file_path, content)

def save_local_file(file_path, content):
    try:
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        if isinstance(content, dict) or isinstance(content, list):
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(content, f, indent=2, ensure_ascii=False)
        else:
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(content)
        return True
    except:
        return False

def load_from_github(file_path):
    if not GH_TOKEN:
        return None
    
    try:
        url = f"https://api.github.com/repos/{REPO}/contents/{file_path}"
        headers = {
            "Authorization": f"token {GH_TOKEN}",
            "Accept": "application/vnd.github.v3+json"
        }
        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            data = response.json()
            content = base64.b64decode(data["content"]).decode('utf-8')
            return json.loads(content)
        return None
    except:
        return None

# ========== БАНЛИСТ ==========
def load_banlist():
    try:
        data = load_from_github(BANLIST_FILE)
        if data is not None:
            return data
    except:
        pass
    
    try:
        if os.path.exists(BANLIST_FILE):
            with open(BANLIST_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        return {}
    except:
        return {}

def save_banlist(banlist):
    if write_to_github_async(BANLIST_FILE, banlist, "Update banlist"):
        return True
    return save_local_file(BANLIST_FILE, banlist)

def add_ban(user_id, reason, admin_id, time_minutes=None):
    banlist = load_banlist()
    expires_at = None
    if time_minutes:
        expires_at = (datetime.now() + timedelta(minutes=time_minutes)).isoformat()
    
    banlist[str(user_id)] = {
        "reason": reason,
        "added_by": admin_id,
        "added_at": get_msk_time(),
        "expires_at": expires_at
    }
    save_banlist(banlist)
    if str(user_id) in blocked_notified:
        del blocked_notified[str(user_id)]
    return True

def remove_ban(user_id):
    banlist = load_banlist()
    if str(user_id) in banlist:
        del banlist[str(user_id)]
        save_banlist(banlist)
        if str(user_id) in blocked_notified:
            del blocked_notified[str(user_id)]
        return True
    return False

def is_banned(user_id):
    banlist = load_banlist()
    if str(user_id) not in banlist:
        return False
    
    data = banlist[str(user_id)]
    if data.get("expires_at"):
        expires = datetime.fromisoformat(data["expires_at"])
        if datetime.now() > expires:
            del banlist[str(user_id)]
            save_banlist(banlist)
            if str(user_id) in blocked_notified:
                del blocked_notified[str(user_id)]
            return False
    
    return True

def get_ban_info(user_id):
    banlist = load_banlist()
    data = banlist.get(str(user_id), {})
    if not data:
        return None
    
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

def is_admin(user_id):
    return user_id == ADMIN_ID

# ========== ТЕХРАБОТЫ ==========
def load_tech():
    try:
        data = load_from_github(TECH_FILE)
        if data is not None:
            return data
    except:
        pass
    
    try:
        if os.path.exists(TECH_FILE):
            with open(TECH_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        return {"active": False, "expires_at": None}
    except:
        return {"active": False, "expires_at": None}

def save_tech(data):
    if write_to_github_async(TECH_FILE, data, "Update tech mode"):
        return True
    return save_local_file(TECH_FILE, data)

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

# ========== КЛЮЧИ ==========
def load_keys():
    try:
        data = load_from_github(KEYS_FILE)
        if data is not None:
            return data
    except:
        pass
    
    try:
        if os.path.exists(KEYS_FILE):
            with open(KEYS_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        return {}
    except:
        return {}

def save_keys(keys):
    if write_to_github_async(KEYS_FILE, keys, "Update keys"):
        return True
    return save_local_file(KEYS_FILE, keys)

def generate_key():
    import string, secrets
    chars = string.ascii_uppercase + string.digits
    random_part = ''.join(secrets.choice(chars) for _ in range(5))
    return f"ADMIN_{random_part}"

def create_session_key():
    keys = load_keys()
    key = generate_key()
    keys[key] = {
        "created_at": get_msk_time(),
        "expires_at": (datetime.now() + timedelta(hours=10)).isoformat()
    }
    save_keys(keys)
    return key

# ========== ЛОГИ ==========
def get_logs_for_user(user_id, count=10):
    try:
        logs = []
        if os.path.exists(LOGS_FILE):
            with open(LOGS_FILE, 'r', encoding='utf-8') as f:
                logs = json.load(f)
        
        filtered = [log for log in logs if log.get("user_id") == user_id]
        return filtered[-count:] if count else filtered
    except:
        return []

# ========== ОСТАНОВКА РАННЕРОВ ==========
async def stop_runners(target, user_id=None, username=None):
    if not GH_TOKEN:
        return "❌ GH_TOKEN не настроен!"
    
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

# ========== ПРОБИВ IP (АСИНХРОННЫЙ) ==========
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

# ========== ПРОБИВ USERNAME ==========
async def probe_username(username: str):
    # Простой пробив по username (имитация)
    return {
        "username": username,
        "id": 123456789,
        "name": "Пользователь",
        "status": "Активен"
    }

# ========== АНАЛИЗ РЕЗУЛЬТАТОВ IP ==========
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
            text=f"✅ БОТ ПОДКЛЮЧЕН К БИЗНЕС-АККАУНТУ!\n\n🆔 ID: {user_id}\n📌 Команды работают в чатах с собеседниками!\n🔥 Введите .help для списка команд"
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
        
        # .help
        if text.lower() == '.help':
            await send_to_business_chat(chat_id, HELP_TEXT, connection_id)
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
            try:
                idlist = load_from_github(IDLIST_FILE) or []
                if not idlist:
                    await send_to_business_chat(chat_id, "📊 Список пользователей пуст", connection_id)
                    return
                result = "👥 СПИСОК ПОЛЬЗОВАТЕЛЕЙ\n\n"
                for item in idlist:
                    result += f"🆔 {item.get('id', '?')} → @{item.get('username', 'Нет')}\n"
                await send_to_business_chat(chat_id, result[:4000], connection_id)
            except Exception as e:
                await send_to_business_chat(chat_id, f"❌ Ошибка: {e}", connection_id)
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
            await send_to_business_ch
