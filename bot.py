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

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ===== СЕКРЕТЫ =====
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", 0))

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

business_connections = {}
blocked_notified = {}

def get_msk_time():
    return (datetime.utcnow() + timedelta(hours=3)).strftime('%d.%m.%Y %H:%M:%S')

def parse_time(time_str):
    """Парсит время типа 1h, 2h30m, -1w (навсегда)"""
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

# ========== БАНЛИСТ ==========
def load_banlist():
    try:
        if os.path.exists(BANLIST_FILE):
            with open(BANLIST_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        return {}
    except:
        return {}

def save_banlist(banlist):
    try:
        with open(BANLIST_FILE, 'w', encoding='utf-8') as f:
            json.dump(banlist, f, indent=2, ensure_ascii=False)
        return True
    except:
        return False

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

def is_tech_mode():
    try:
        if os.path.exists(TECH_FILE):
            with open(TECH_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                if data.get("active", False):
                    expires = datetime.fromisoformat(data.get("expires_at"))
                    if datetime.now() > expires:
                        data["active"] = False
                        with open(TECH_FILE, 'w', encoding='utf-8') as f2:
                            json.dump(data, f2)
                        return False
                    return True
        return False
    except:
        return False

def get_tech_info():
    try:
        if os.path.exists(TECH_FILE):
            with open(TECH_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
    except:
        pass
    return {"active": False, "expires_at": None}

def set_tech_mode(active, expires_at=None):
    os.makedirs(os.path.dirname(TECH_FILE), exist_ok=True)
    data = {"active": active, "expires_at": expires_at}
    with open(TECH_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f)

# ========== ЛОГИ ==========
def save_log(log_entry):
    try:
        os.makedirs(os.path.dirname(LOGS_FILE), exist_ok=True)
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
        
        save_idlist(log_entry.get("user_id"), log_entry.get("username"))
        return True
    except Exception as e:
        print(f"❌ Ошибка сохранения лога: {e}")
        return False

def save_idlist(user_id, username):
    try:
        os.makedirs(os.path.dirname(IDLIST_FILE), exist_ok=True)
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

# ========== КЛЮЧИ ==========
def load_keys():
    try:
        if os.path.exists(KEYS_FILE):
            with open(KEYS_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        return {}
    except:
        return {}

def save_keys(keys):
    try:
        with open(KEYS_FILE, 'w', encoding='utf-8') as f:
            json.dump(keys, f, indent=2, ensure_ascii=False)
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
        "created_at": get_msk_time(),
        "expires_at": (datetime.now() + timedelta(hours=10)).isoformat()
    }
    save_keys(keys)
    return key

def verify_key(key):
    keys = load_keys()
    if key not in keys:
        return False
    
    data = keys[key]
    expires = datetime.fromisoformat(data["expires_at"])
    if datetime.now() > expires:
        del keys[key]
        save_keys(keys)
        return False
    
    return True

# ========== УДАЛЕНИЕ В БИЗНЕС-ЧАТЕ ==========
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

# ========== ОТПРАВКА В БИЗНЕС-ЧАТ ==========
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

# ========== РЕДАКТИРОВАНИЕ В БИЗНЕС-ЧАТЕ ==========
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

# ========== РЕДАКТИРОВАНИЕ В ОБЫЧНОМ ЧАТЕ ==========
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
async def show_animation(target, connection_id=None, type_name="IP"):
    stages = [
        f"🔄 ПОДКЛЮЧЕНИЕ К СЕРВЕРАМ\n\n"
        f"📡 Сервер #1... ████░░░░░░ 40%\n"
        f"📡 Сервер #2... ░░░░░░░░░░ 0%\n"
        f"📡 Сервер #3... ░░░░░░░░░░ 0%\n"
        f"📡 Сервер #4... ░░░░░░░░░░ 0%\n"
        f"📡 Сервер #5... ░░░░░░░░░░ 0%\n\n"
        f"⏳ Ожидайте...",
        
        f"🔄 ПОДКЛЮЧЕНИЕ К СЕРВЕРАМ\n\n"
        f"📡 Сервер #1... ████████░░ 80%\n"
        f"📡 Сервер #2... ██████░░░░ 60%\n"
        f"📡 Сервер #3... ████░░░░░░ 40%\n"
        f"📡 Сервер #4... ██░░░░░░░░ 20%\n"
        f"📡 Сервер #5... ░░░░░░░░░░ 0%\n\n"
        f"⏳ Ожидайте...",
        
        f"🔄 ПОДКЛЮЧЕНИЕ К СЕРВЕРАМ\n\n"
        f"📡 Сервер #1... ██████████ 100% ✅\n"
        f"📡 Сервер #2... ██████████ 100% ✅\n"
        f"📡 Сервер #3... ████████░░ 80%\n"
        f"📡 Сервер #4... ██████░░░░ 60%\n"
        f"📡 Сервер #5... ████░░░░░░ 40%\n\n"
        f"⏳ Ожидайте...",
        
        f"✅ ПОДКЛЮЧЕНИЕ ВЫПОЛНЕНО\n\n"
        f"📊 Получение данных...\n"
        f"⏳ Обработка информации..."
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

# ========== БИЗНЕС CONNECTION ==========
@dp.business_connection()
async def handle_business_connection(connection: BusinessConnection):
    if connection.user:
        user_id = connection.user.id
        connection_id = connection.id
        username = connection.user.username or "Нет юзернейма"
        
        if not is_admin(user_id):
            await bot.send_message(
                chat_id=user_id,
                text="❌ У вас нет прав на подключение бизнес-бота!\nТолько администратор может использовать эту функцию."
            )
            return
        
        business_connections[str(user_id)] = connection_id
        
        logger.info(f"🔗 BUSINESS CONNECTION: @{username} (ID: {user_id})")
        
        await bot.send_message(
            chat_id=user_id,
            text=f"✅ БОТ ПОДКЛЮЧЕН К БИЗНЕС-АККАУНТУ!\n\n"
                 f"🆔 ID: {user_id}\n"
                 f"📌 Команды работают в чатах с собеседниками!\n"
                 f"🔥 Введите .help для списка команд"
        )

# ========== БИЗНЕС MESSAGE ==========
@dp.business_message()
async def handle_business_message(message: types.Message):
    try:
        user_id = message.from_user.id
        chat_id = message.chat.id
        message_id = message.message_id
        connection_id = message.business_connection_id
        
        if not is_admin(user_id):
            await delete_business_message(chat_id, message_id, connection_id)
            return
        
        if not connection_id:
            connection_id = business_connections.get(str(user_id))
        
        if is_banned(user_id):
            if str(user_id) not in blocked_notified:
                ban_info = get_ban_info(user_id)
                reason = ban_info.get("reason", "Не указана") if ban_info else "Не указана"
                await delete_business_message(chat_id, message_id, connection_id)
                await send_to_business_chat(
                    chat_id,
                    f"⛔ ВАС ЗАБЛОКИРОВАЛИ В БОТЕ!\n\n📌 Причина: {reason}",
                    connection_id
                )
                blocked_notified[str(user_id)] = True
            else:
                await delete_business_message(chat_id, message_id, connection_id)
            return
        
        if is_tech_mode():
            tech_info = get_tech_info()
            await delete_business_message(chat_id, message_id, connection_id)
            await send_to_business_chat(
                chat_id,
                f"🛠️ БОТ НА ТЕХНИЧЕСКИХ РАБОТАХ\n\n🕐 ВРЕМЯ: {tech_info.get('expires_at', 'Неизвестно')}",
                connection_id
            )
            return
        
        if not message.text:
            return
        
        text = message.text.strip()
        await delete_business_message(chat_id, message_id, connection_id)
        
        # .help
        if text.lower() == '.help':
            await send_to_business_chat(
                chat_id,
                "📚 ДОСТУПНЫЕ КОМАНДЫ\n\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                "🔍 ПРОБИВ\n\n"
                ".whois ip [IP] — пробив IP-адреса\n"
                ".whois n [номер] — пробив номера телефона\n"
                ".whois qz [@username] — пробив Telegram-юзернейма\n\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                "⚡ ДОПОЛНИТЕЛЬНО\n\n"
                ".help — справка\n\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                "🛡️ НАКАЗАНИЯ (PLUS)\n\n"
                ".ban (I) (T) (R) — Выдать бан\n"
                ".unban (I) (R) — Снять блокировку\n"
                ".chkban (I) — Проверить бан\n\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                "📌 .команды — в чатах с собеседниками\n"
                "📌 /команды — в личке с ботом",
                connection_id
            )
            return
        
        # .ban
        if text.lower().startswith('.ban'):
            parts = text.split(maxsplit=3)
            if len(parts) < 3:
                await send_to_business_chat(chat_id, "❌ .ban [ID] [время] [причина]\nПример: .ban 123456789 1h Спам", connection_id)
                return
            
            target_id = parts[1]
            time_str = parts[2]
            reason = parts[3] if len(parts) > 3 else "Без причины"
            
            minutes, time_display = parse_time(time_str)
            
            add_ban(target_id, reason, user_id, minutes)
            
            await send_to_business_chat(
                chat_id,
                f"✅ ПОЛЬЗОВАТЕЛЬ ЗАБАНЕН\n\n"
                f"🆔 ID: {target_id}\n"
                f"📌 Причина: {reason}\n"
                f"⏱ Время: {time_display}\n"
                f"🕐 Дата: {get_msk_time()}",
                connection_id
            )
            
            try:
                ban_msg = f"⛔ ВАС ЗАБЛОКИРОВАЛИ В БОТЕ\n\n📌 Причина: {reason}\n⏱ Длительность: {time_display}\n🕐 Дата блокировки: {get_msk_time()}"
                if minutes:
                    ban_msg += f"\n⏳ Разблокировка: {(datetime.now() + timedelta(minutes=minutes)).strftime('%d.%m.%Y %H:%M')}"
                await bot.send_message(chat_id=int(target_id), text=ban_msg)
            except:
                pass
            
            save_log({
                "command": f".ban {target_id}",
                "user_id": user_id,
                "username": message.from_user.username or "Нет",
                "target": target_id,
                "reason": reason,
                "time": get_msk_time()
            })
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
                await send_to_business_chat(
                    chat_id,
                    f"✅ ПОЛЬЗОВАТЕЛЬ РАЗБАНЕН\n\n"
                    f"🆔 ID: {target_id}\n"
                    f"📌 Причина разбана: {reason}\n"
                    f"🕐 Дата: {get_msk_time()}\n"
                    f"🔓 Пользователь снова может пользоваться ботом",
                    connection_id
                )
                try:
                    await bot.send_message(
                        chat_id=int(target_id),
                        text=f"✅ ВАС РАЗБЛОКИРОВАЛИ\n\n📌 Причина: {reason}\n🕐 Дата: {get_msk_time()}\n🔓 Теперь вы снова можете пользоваться ботом"
                    )
                except:
                    pass
            else:
                await send_to_business_chat(chat_id, f"❌ Пользователь {target_id} не найден в черном списке", connection_id)
            return
        
        # .chkban
        if text.lower().startswith('.chkban'):
            parts = text.split(maxsplit=1)
            if len(parts) < 2:
                await send_to_business_chat(chat_id, "❌ .chkban [ID]", connection_id)
                return
            
            target_id = parts[1]
            ban_info = get_ban_info(target_id)
            
            if ban_info:
                await send_to_business_chat(
                    chat_id,
                    f"---{target_id}---\n"
                    f"📌 Причина: {ban_info.get('reason', 'Не указана')}\n"
                    f"🕐 Дата выдачи: {ban_info.get('added_at', 'Неизвестно')}\n"
                    f"🕐 Дата снятия: {ban_info.get('expires_at', 'НАВСЕГДА')}\n"
                    f"🔓 Осталось: {ban_info.get('expires_at', 'Навсегда')}",
                    connection_id
                )
            else:
                await send_to_business_chat(chat_id, f"⛔ Данный {target_id} не заблокирован.", connection_id)
            return
        
        # .key
        if text.lower() == '.key':
            key = create_session_key()
            await send_to_business_chat(
                chat_id,
                f"🔑 Ваш ключ для сайта:\n\n`{key}`\n\n⏱ Действует 10 часов\n🌐 Сайт: https://grifmcpo.github.io/WhoisBotDisVk/",
                connection_id
            )
            return
        
        # .tex on
        if text.lower().startswith('.tex on'):
            parts = text.split(maxsplit=2)
            if len(parts) < 3:
                await send_to_business_chat(chat_id, "❌ .tex on [время]\nПример: .tex on 1h", connection_id)
                return
            
            time_str = parts[2]
            minutes, time_display = parse_time(time_str)
            expires_at = (datetime.now() + timedelta(minutes=minutes)).isoformat()
            
            set_tech_mode(True, expires_at)
            
            await send_to_business_chat(
                chat_id,
                f"✅ ТЕХ-РАБОТЫ УСПЕШНО ВКЛЮЧЕНЫ\n🕐 Время работ: {get_msk_time()}\n⏳ Окончание: {(datetime.now() + timedelta(minutes=minutes)).strftime('%d.%m.%Y %H:%M')}",
                connection_id
            )
            return
        
        # .tex off
        if text.lower() == '.tex off':
            set_tech_mode(False, None)
            await send_to_business_chat(
                chat_id,
                "✅ ТЕХ-РАБОТЫ УСПЕШНО ВЫКЛЮЧЕНЫ",
                connection_id
            )
            return
        
        # .whois
        if text.lower().startswith('.whois'):
            parts = text.split(maxsplit=2)
            if len(parts) < 3:
                await send_to_business_chat(chat_id, "❌ .whois ip [IP] или .whois n [номер] или .whois qz [@username]", connection_id)
                return
            
            command_type = parts[1].lower()
            target = parts[2]
            
            loading = await show_animation(chat_id, connection_id, "IP")
            await asyncio.sleep(1)
            await edit_business_message(
                chat_id,
                loading.message_id,
                f"✅ РЕЗУЛЬТАТ ПРОБИВА\n\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"🌐 {command_type.upper()}: {target}\n"
                f"🌍 Статус: Успешно обработано\n"
                f"📊 Использовано серверов: 20/20\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━",
                connection_id
            )
            
            save_log({
                "command": f".whois {command_type} {target}",
                "user_id": user_id,
                "username": message.from_user.username or "Нет",
                "target": target,
                "time": get_msk_time()
            })
            return
        
    except Exception as e:
        logger.error(f"❌ Ошибка бизнес-сообщения: {e}")

# ========== КОМАНДЫ В ЛИЧКЕ ==========
@dp.message(Command("start"))
async def start(message: types.Message):
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
    
    save_log({
        "command": "/start",
        "user_id": user_id,
        "username": message.from_user.username or "Нет",
        "time": get_msk_time()
    })
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🌐 ПРОБИВ IP", callback_data="probe_ip")],
        [InlineKeyboardButton(text="📱 ПРОБИВ НОМЕРА", callback_data="probe_phone")],
        [InlineKeyboardButton(text="👤 ПРОБИВ ЮЗЕРА", callback_data="probe_user")],
    ])
    
    await message.answer(
        "🔥 ДОБРО ПОЖАЛОВАТЬ В СИСТЕМУ!\n\n"
        "Выберите действие:",
        reply_markup=keyboard
    )

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
    
    await message.answer(
        "📚 СПИСОК КОМАНД\n\n"
        "🔹 В ЛИЧКЕ БОТА (с /):\n"
        "/start — Главное меню\n"
        "/help — Справка\n"
        "/whois — Пробив\n"
        "/ban — Бан (админ)\n"
        "/unban — Разбан (админ)\n"
        "/key — Получить ключ для сайта\n\n"
        "🔹 В ЧАТАХ (с .):\n"
        ".help — Справка\n"
        ".whois ip [IP] — Пробив IP\n"
        ".whois n [номер] — Пробив номера\n"
        ".whois qz [@username] — Пробив юзера\n"
        ".ban [ID] [время] [причина] — Бан\n"
        ".unban [ID] [причина] — Разбан\n"
        ".chkban [ID] — Проверить бан\n"
        ".key — Получить ключ\n"
        ".tex on [время] — Включить техработы\n"
        ".tex off — Выключить техработы"
    )

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
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🌐 ПРОБИВ IP", callback_data="probe_ip")],
        [InlineKeyboardButton(text="📱 ПРОБИВ НОМЕРА", callback_data="probe_phone")],
        [InlineKeyboardButton(text="👤 ПРОБИВ ЮЗЕРА", callback_data="probe_user")],
    ])
    
    await message.answer(
        "🔍 Выберите тип пробива:",
        reply_markup=keyboard
    )

@dp.message(Command("ban"))
async def ban_command(message: types.Message):
    user_id = message.from_user.id
    
    if not is_admin(user_id):
        await message.answer("❌ У вас нет прав на бан!")
        return
    
    args = message.text.split(maxsplit=3)
    if len(args) < 3:
        await message.answer("❌ /ban [ID] [время] [причина]\nПример: /ban 123456789 1h Спам")
        return
    
    target_id = args[1]
    time_str = args[2]
    reason = args[3] if len(args) > 3 else "Без причины"
    
    minutes, time_display = parse_time(time_str)
    
    add_ban(target_id, reason, user_id, minutes)
    
    await message.answer(
        f"✅ ПОЛЬЗОВАТЕЛЬ ЗАБАНЕН\n\n"
        f"🆔 ID: {target_id}\n"
        f"📌 Причина: {reason}\n"
        f"⏱ Время: {time_display}\n"
        f"🕐 Дата: {get_msk_time()}"
    )
    
    try:
        ban_msg = f"⛔ ВАС ЗАБЛОКИРОВАЛИ В БОТЕ\n\n📌 Причина: {reason}\n⏱ Длительность: {time_display}\n🕐 Дата блокировки: {get_msk_time()}"
        if minutes:
            ban_msg += f"\n⏳ Разблокировка: {(datetime.now() + timedelta(minutes=minutes)).strftime('%d.%m.%Y %H:%M')}"
        await bot.send_message(chat_id=int(target_id), text=ban_msg)
    except:
        pass
    
    save_log({
        "command": f"/ban {target_id}",
        "user_id": user_id,
        "username": message.from_user.username or "Нет",
        "target": target_id,
        "reason": reason,
        "time": get_msk_time()
    })

@dp.message(Command("unban"))
async def unban_command(message: types.Message):
    user_id = message.from_user.id
    
    if not is_admin(user_id):
        await message.answer("❌ У вас нет прав на разбан!")
        return
    
    args = message.text.split(maxsplit=2)
    if len(args) < 2:
        await message.answer("❌ /unban [ID] [причина]")
        return
    
    target_id = args[1]
    reason = args[2] if len(args) > 2 else "Без причины"
    
    if remove_ban(target_id):
        await message.answer(
            f"✅ ПОЛЬЗОВАТЕЛЬ РАЗБАНЕН\n\n"
            f"🆔 ID: {target_id}\n"
            f"📌 Причина разбана: {reason}\n"
            f"🕐 Дата: {get_msk_time()}\n"
            f"🔓 Пользователь снова может пользоваться ботом"
        )
        try:
            await bot.send_message(
                chat_id=int(target_id),
                text=f"✅ ВАС РАЗБЛОКИРОВАЛИ\n\n📌 Причина: {reason}\n🕐 Дата: {get_msk_time()}\n🔓 Теперь вы снова можете пользоваться ботом"
            )
        except:
            pass
    else:
        await message.answer(f"❌ Пользователь {target_id} не найден в черном списке")

@dp.message(Command("key"))
async def key_command(message: types.Message):
    user_id = message.from_user.id
    
    if not is_admin(user_id):
        await message.answer("❌ У вас нет прав на получение ключа!")
        return
    
    key = create_session_key()
    await message.answer(
        f"🔑 Ваш ключ для сайта:\n\n`{key}`\n\n⏱ Действует 10 часов\n🌐 Сайт: https://grifmcpo.github.io/WhoisBotDisVk/"
    )

# ========== CALLBACK ==========
@dp.callback_query()
async def handle_callback(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    
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
        await callback.answer()
    elif data == "probe_phone":
        await callback.message.answer("📱 ВВЕДИТЕ НОМЕР\n📌 Пример: 89001234567")
        await callback.answer()
    elif data == "probe_user":
        await callback.message.answer("👤 ВВЕДИТЕ @USERNAME\n📌 Пример: @username")
        await callback.answer()

@dp.message()
async def handle_private_message(message: types.Message):
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
        await message.answer(
            "❌ Команды с . работают только в чатах с собеседниками!\n"
            "📌 В личке используй команды с / (например /help)"
        )
        return
    
    # Пробив по IP
    if re.match(r'^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$', text):
        loading = await show_animation(message)
        await asyncio.sleep(1)
        await edit_normal_message(
            message.chat.id,
            loading.message_id,
            f"✅ РЕЗУЛЬТАТ ПРОБИВА IP\n\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🌐 IP-адрес: {text}\n"
            f"🌍 Город: Москва (пример)\n"
            f"📡 Оператор: Пример\n"
            f"🛡️ Доверенность: 95%\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━"
        )
        save_log({
            "command": f"IP {text}",
            "user_id": user_id,
            "username": message.from_user.username or "Нет",
            "target": text,
            "time": get_msk_time()
        })
        return
    
    # Пробив по номеру
    if re.match(r'^\+?\d{10,15}$', text):
        loading = await show_animation(message)
        await asyncio.sleep(1)
        await edit_normal_message(
            message.chat.id,
            loading.message_id,
            f"✅ РЕЗУЛЬТАТ ПРОБИВА НОМЕРА\n\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📱 Номер: {text}\n"
            f"📡 Оператор: Пример\n"
            f"🌍 Регион: Москва (пример)\n"
            f"🛡️ Доверенность: 92%\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━"
        )
        save_log({
            "command": f"Номер {text}",
            "user_id": user_id,
            "username": message.from_user.username or "Нет",
            "target": text,
            "time": get_msk_time()
        })
        return
    
    # Пробив по юзеру
    if text.startswith('@'):
        loading = await show_animation(message)
        await asyncio.sleep(1)
        await edit_normal_message(
            message.chat.id,
            loading.message_id,
            f"✅ РЕЗУЛЬТАТ ПРОБИВА USERNAME\n\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"👤 Username: {text}\n"
            f"🆔 ID: 123456789 (пример)\n"
            f"📛 Имя: Пример\n"
            f"🛡️ Доверенность: 88%\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━"
        )
        save_log({
            "command": f"Юзер {text}",
            "user_id": user_id,
            "username": message.from_user.username or "Нет",
            "target": text,
            "time": get_msk_time()
        })
        return
    
    await message.answer(
        "❓ Неизвестная команда\n\n"
        "📌 Введи /help для списка команд\n"
        "📌 В чатах с собеседниками используй .команды"
    )

# ========== ЗАПУСК ==========
async def main():
    print("=" * 60)
    print("🔥 БОТ ЗАПУЩЕН!")
    print(f"👤 АДМИН: {ADMIN_ID}")
    print("📌 Команды с / — в личке бота")
    print("📌 Команды с . — в чатах с собеседниками")
    print("=" * 60)
    
    os.makedirs('data', exist_ok=True)
    
    for file in [LOGS_FILE, BANLIST_FILE, IDLIST_FILE, KEYS_FILE, TECH_FILE]:
        if not os.path.exists(file):
            with open(file, 'w', encoding='utf-8') as f:
                if file == TECH_FILE:
                    json.dump({"active": False, "expires_at": None}, f)
                elif file == IDLIST_FILE:
                    json.dump([], f)
                elif file in [BANLIST_FILE, KEYS_FILE]:
                    json.dump({}, f)
                else:
                    json.dump([], f)
            print(f"✅ Создан файл: {file}")
    
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
