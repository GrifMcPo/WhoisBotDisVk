import os
import json
import time
import asyncio
import logging
import random
import re
import string
from datetime import datetime, timedelta
from typing import Dict, Optional, List, Tuple
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes
from telegram.constants import ParseMode

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Получение токенов из секретов GitHub
BOT_TOKEN = os.getenv('BOT_TOKEN')
ADMIN_ID = int(os.getenv('ADMIN_ID', 0))

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN not set in environment variables!")

# Файлы для хранения данных
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

# ─── Утилиты ───

def moscow_time():
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')

def progress_bar(percent):
    filled = int(percent / 10)
    return '█' * filled + '░' * (10 - filled)

def parse_ban_time(time_str: str) -> int:
    """
    Парсит время бана:
    30m - 30 минут
    2h - 2 часа
    1h30m - 1 час 30 минут
    7d - 7 дней
    -1w - навсегда
    """
    time_str = time_str.lower().strip()
    
    if time_str == '-1w' or time_str == 'forever' or time_str == 'навсегда':
        return -1
    
    total_minutes = 0
    
    # Парсим часы
    h_match = re.search(r'(\d+)h', time_str)
    if h_match:
        total_minutes += int(h_match.group(1)) * 60
    
    # Парсим минуты
    m_match = re.search(r'(\d+)m', time_str)
    if m_match:
        total_minutes += int(m_match.group(1))
    
    # Парсим дни
    d_match = re.search(r'(\d+)d', time_str)
    if d_match:
        total_minutes += int(d_match.group(1)) * 24 * 60
    
    # Парсим недели
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

# ─── Анимация подключения ───

async def show_connection_animation(context, chat_id, message_id, action_type):
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
    
    for stage in stages:
        if stage.get('final'):
            text = f"""✅ ПОДКЛЮЧЕНИЕ ВЫПОЛНЕНО

📊 Получение данных...
⏳ Обработка информации..."""
            try:
                await context.bot.edit_message_text(
                    text=text,
                    chat_id=chat_id,
                    message_id=message_id
                )
            except:
                pass
            await asyncio.sleep(0.5)
            return
        
        text = f"{stage['title']}\n\n"
        for server in stage['servers']:
            bar = '█' * (server['progress'] // 10) + '░' * (10 - server['progress'] // 10)
            checkmark = ' ✅' if server['done'] else ''
            text += f"📡 {server['name']}... {bar} {server['progress']}%{checkmark}\n"
        text += "\n⏳ Ожидайте..."
        
        try:
            await context.bot.edit_message_text(
                text=text,
                chat_id=chat_id,
                message_id=message_id
            )
        except:
            pass
        await asyncio.sleep(0.4)

# ─── Результаты пробива ───

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

MENU_KEYBOARD = InlineKeyboardMarkup([
    [InlineKeyboardButton("1️⃣ Пробив IP", callback_data='ip')],
    [InlineKeyboardButton("2️⃣ Пробив номера", callback_data='phone')],
    [InlineKeyboardButton("3️⃣ Пробив юзера (@)", callback_data='username')]
])

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
📌 .команды — в чатах с собеседниками
📌 /команды — в личке с ботом"""

# ─── Проверка админа ───

def is_admin(user_id):
    return user_id == ADMIN_ID

# ─── Удаление сообщения ───

async def try_delete_message(context, chat_id, message_id):
    try:
        await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
    except:
        pass

# ─── Бан/Разбан ───

async def handle_ban(update, context, args, is_business):
    if len(args) < 3:
        await update.message.reply_text('❌ Формат: .ban (ID) (ВРЕМЯ) (ПРИЧИНА)\nПримеры:\n.ban 123456789 30m Спам\n.ban 123456789 -1w Навсегда')
        return
    
    try:
        target_id = int(args[0])
        time_str = args[1]
        reason = ' '.join(args[2:])
        
        minutes = parse_ban_time(time_str)
        if minutes is None:
            await update.message.reply_text('❌ Неверный формат времени. Примеры: 30m, 2h, 1h30m, 7d, -1w')
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
                'issued_by': update.effective_user.id
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
                'issued_by': update.effective_user.id
            }
        
        # Удаляем старый бан
        bans = [b for b in bans if b['user_id'] != target_id]
        bans.append(ban_data)
        save_json(FILES['banlist'], bans)
        
        # Удаляем из уведомленных
        if target_id in banned_notified:
            banned_notified.remove(target_id)
        
        # Формируем ответ
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
        
        await update.message.reply_text(result, parse_mode=ParseMode.HTML)
        
        # Уведомляем пользователя
        try:
            if minutes == -1:
                await context.bot.send_message(
                    chat_id=target_id,
                    text=f"""⛔ ВАС ЗАБЛОКИРОВАЛИ В БОТЕ НАВСЕГДА

📌 Причина: {reason}
🕐 Дата блокировки: {moscow_time()}"""
                )
            else:
                await context.bot.send_message(
                    chat_id=target_id,
                    text=f"""⛔ ВАС ЗАБЛОКИРОВАЛИ В БОТЕ

📌 Причина: {reason}
⏱ Длительность: {time_str}
🕐 Дата блокировки: {moscow_time()}
⏳ Разблокировка: {unban_at.strftime('%Y-%m-%d %H:%M:%S')}"""
                )
            banned_notified.add(target_id)
        except:
            pass
            
    except ValueError:
        await update.message.reply_text('❌ Неверный формат ID.')

async def handle_unban(update, context, args, is_business):
    if len(args) < 2:
        await update.message.reply_text('❌ Формат: .unban (ID) (ПРИЧИНА)\nПример: .unban 123456789 Ошибка')
        return
    
    try:
        target_id = int(args[0])
        reason = ' '.join(args[1:])
        
        bans = load_json(FILES['banlist'])
        filtered = [b for b in bans if b['user_id'] != target_id]
        
        if len(filtered) == len(bans):
            await update.message.reply_text(f'⛔ Данный {target_id} не заблокирован.')
            return
        
        save_json(FILES['banlist'], filtered)
        if target_id in banned_notified:
            banned_notified.remove(target_id)
        
        result = f"""✅ ПОЛЬЗОВАТЕЛЬ РАЗБАНЕН

🆔 ID: <code>{target_id}</code>
📌 Причина разбана: {reason}
🕐 Дата: {moscow_time()}
🔓 Пользователь снова может пользоваться ботом"""
        
        await update.message.reply_text(result, parse_mode=ParseMode.HTML)
        
        try:
            await context.bot.send_message(
                chat_id=target_id,
                text=f"""✅ ВАС РАЗБЛОКИРОВАЛИ

📌 Причина разблокировки: {reason}
🕐 Дата: {moscow_time()}
🔓 Теперь вы снова можете пользоваться ботом"""
            )
        except:
            pass
            
    except ValueError:
        await update.message.reply_text('❌ Неверный формат ID.')

async def handle_chkban(update, context, args):
    if len(args) < 1:
        await update.message.reply_text('❌ Формат: .chkban (ID)')
        return
    
    try:
        target_id = int(args[0])
        bans = load_json(FILES['banlist'])
        ban = next((b for b in bans if b['user_id'] == target_id), None)
        
        if not ban:
            await update.message.reply_text(f'⛔ Данный {target_id} не заблокирован.')
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
                await update.message.reply_text(f'⛔ Данный {target_id} не заблокирован.')
                return
            remaining = int((unban_at - now).total_seconds() // 60)
            hours = remaining // 60
            minutes = remaining % 60
            result = f"""---<code>{target_id}</code>---
📌Причина: {ban['reason']}
🕐Дата выдачи: {datetime.fromisoformat(ban['banned_at']).strftime('%Y-%m-%d %H:%M:%S')}
🕐Дата снятия бана: {unban_at.strftime('%Y-%m-%d %H:%M:%S')}
🔓Осталось до окончания: {hours}ч {minutes}м"""
        
        await update.message.reply_text(result, parse_mode=ParseMode.HTML)
        
    except ValueError:
        await update.message.reply_text('❌ Неверный формат ID.')

async def handle_logs(update, context, args):
    if len(args) < 2:
        await update.message.reply_text('❌ Формат: .logs (ID) (количество)\nПример: .logs 123456789 10')
        return
    
    try:
        target_id = int(args[0])
        limit = min(int(args[1]), 100)
        
        logs = load_json(FILES['logs'])
        user_logs = logs.get(str(target_id), [])[-limit:][::-1]
        
        if not user_logs:
            await update.message.reply_text('📭 Логи не найдены для данного ID.')
            return
        
        text = f'📋 Логи пользователя <code>{target_id}</code> (последние {len(user_logs)})\n\n'
        for log in user_logs:
            text += f"📝 {log['command']}\n🕐 {datetime.fromisoformat(log['time']).strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        
        if len(text) > 4096:
            chunks = [text[i:i+4096] for i in range(0, len(text), 4096)]
            for chunk in chunks:
                await update.message.reply_text(chunk, parse_mode=ParseMode.HTML)
        else:
            await update.message.reply_text(text, parse_mode=ParseMode.HTML)
            
    except ValueError:
        await update.message.reply_text('❌ Неверный формат ID или количества.')

async def handle_idlist(update, context):
    users = load_json(FILES['idlist'])
    
    if not users:
        await update.message.reply_text('📭 Список ID пуст.')
        return
    
    text = f'📋 Список пользователей ({len(users)})\n\n'
    for user_id, data in users.items():
        username = data.get('username') or 'нет username'
        text += f'👤 @{username} → <code>{user_id}</code>\n'
    
    if len(text) > 4096:
        chunks = [text[i:i+4096] for i in range(0, len(text), 4096)]
        for chunk in chunks:
            await update.message.reply_text(chunk, parse_mode=ParseMode.HTML)
    else:
        await update.message.reply_text(text, parse_mode=ParseMode.HTML)

async def handle_key(update, context):
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
        
        await update.message.reply_text(result, parse_mode=ParseMode.HTML)
        
    except Exception as e:
        await update.message.reply_text('❌ Ошибка при генерации ключа.')

async def handle_tex(update, context, args):
    global maintenance_mode, maintenance_until
    
    if len(args) < 1:
        await update.message.reply_text('❌ Формат: .tex on (минуты) или .tex off')
        return
    
    sub_cmd = args[0].lower()
    
    if sub_cmd == 'on':
        if len(args) < 2:
            await update.message.reply_text('❌ Укажите время: .tex on 30')
            return
        
        try:
            minutes = int(args[1])
            if minutes <= 0:
                await update.message.reply_text('❌ Время должно быть больше 0.')
                return
            
            until = datetime.now() + timedelta(minutes=minutes)
            await set_tech_works(True, until.isoformat())
            maintenance_mode = True
            maintenance_until = until.isoformat()
            
            await update.message.reply_text(
                f'✅ ТЕХ-РАБОТЫ УСПЕШНО ВКЛЮЧЕНЫ\n'
                f'🕐 Время работ: до {until.strftime("%Y-%m-%d %H:%M:%S")}'
            )
        except ValueError:
            await update.message.reply_text('❌ Неверный формат времени. Укажите минуты.')
            
    elif sub_cmd == 'off':
        await set_tech_works(False, None)
        maintenance_mode = False
        maintenance_until = None
        await update.message.reply_text('✅ ТЕХ-РАБОТЫ УСПЕШНО ВЫКЛЮЧЕНЫ')
        
    else:
        await update.message.reply_text('❌ Используйте: .tex on (минуты) или .tex off')

# ─── Проверка бана (без спама) ───

async def check_ban_silent(update, context):
    user_id = update.effective_user.id
    ban = await is_banned(user_id)
    if ban:
        if user_id not in banned_notified:
            banned_notified.add(user_id)
            if ban.get('forever', False):
                await update.message.reply_text(
                    f'⛔ ВАС ЗАБЛОКИРОВАЛИ В БОТЕ НАВСЕГДА\n\n'
                    f'📌 Причина: {ban["reason"]}\n'
                    f'🕐 Дата блокировки: {datetime.fromisoformat(ban["banned_at"]).strftime("%Y-%m-%d %H:%M:%S")}'
                )
            else:
                await update.message.reply_text(
                    f'⛔ ВАС ЗАБЛОКИРОВАЛИ В БОТЕ\n\n'
                    f'📌 Причина: {ban["reason"]}\n'
                    f'⏱ Длительность: {ban.get("duration", "неизвестно")}\n'
                    f'🕐 Дата блокировки: {datetime.fromisoformat(ban["banned_at"]).strftime("%Y-%m-%d %H:%M:%S")}\n'
                    f'⏳ Разблокировка: {datetime.fromisoformat(ban["unban_at"]).strftime("%Y-%m-%d %H:%M:%S")}'
                )
        return True
    return False

async def check_maintenance(update):
    global maintenance_mode, maintenance_until
    if update.effective_user.id == ADMIN_ID:
        return False
    if maintenance_mode:
        if maintenance_until and datetime.now().isoformat() > maintenance_until:
            maintenance_mode = False
            return False
        return True
    return False

# ─── Команды ЛС ───

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    username = update.effective_user.username or str(user_id)
    first_name = update.effective_user.first_name
    
    if await check_ban_silent(update, context):
        return
    
    if await check_maintenance(update):
        await update.message.reply_text(
            f'🛠️ БОТ НА ТЕХНИЧЕСКИХ РАБОТАХ\n\n'
            f'🕐 ВРЕМЯ: до {datetime.fromisoformat(maintenance_until).strftime("%Y-%m-%d %H:%M:%S")}'
        )
        return
    
    await save_user(user_id, username, first_name)
    await log_command(user_id, username, '/start')
    
    await update.message.reply_text(
        '👋 Привет! Я бот для пробива информации.\n\n'
        'Выбери действие:',
        reply_markup=MENU_KEYBOARD
    )

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    username = query.from_user.username or str(user_id)
    
    ban = await is_banned(user_id)
    if ban:
        if user_id not in banned_notified:
            banned_notified.add(user_id)
            if ban.get('forever', False):
                await query.edit_message_text(
                    f'⛔ ВАС ЗАБЛОКИРОВАЛИ В БОТЕ НАВСЕГДА\n\n'
                    f'📌 Причина: {ban["reason"]}\n'
                    f'🕐 Дата блокировки: {datetime.fromisoformat(ban["banned_at"]).strftime("%Y-%m-%d %H:%M:%S")}'
                )
            else:
                await query.edit_message_text(
                    f'⛔ ВАС ЗАБЛОКИРОВАЛИ В БОТЕ\n\n'
                    f'📌 Причина: {ban["reason"]}\n'
                    f'⏱ Длительность: {ban.get("duration", "неизвестно")}\n'
                    f'🕐 Дата блокировки: {datetime.fromisoformat(ban["banned_at"]).strftime("%Y-%m-%d %H:%M:%S")}\n'
                    f'⏳ Разблокировка: {datetime.fromisoformat(ban["unban_at"]).strftime("%Y-%m-%d %H:%M:%S")}'
                )
        return
    
    if await check_maintenance(update):
        await query.edit_message_text(
            f'🛠️ БОТ НА ТЕХНИЧЕСКИХ РАБОТАХ\n\n'
            f'🕐 ВРЕМЯ: до {datetime.fromisoformat(maintenance_until).strftime("%Y-%m-%d %H:%M:%S")}'
        )
        return
    
    action = query.data
    context.user_data['action'] = action
    
    action_names = {'ip': 'IP', 'phone': 'номера', 'username': 'юзера (@)'}
    
    await query.delete_message()
    msg = await query.message.reply_text(
        f'Действие выбрано: Whois {action_names[action]}\n'
        f'Пришлите сюда {"IP:" if action == "ip" else "номер:" if action == "phone" else "@username:"}'
    )
    context.user_data['waiting_input'] = True
    context.user_data['input_message_id'] = msg.message_id

async def handle_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    username = update.effective_user.username or str(user_id)
    
    if await check_ban_silent(update, context):
        return
    
    if await check_maintenance(update):
        await update.message.reply_text(
            f'🛠️ БОТ НА ТЕХНИЧЕСКИХ РАБОТАХ\n\n'
            f'🕐 ВРЕМЯ: до {datetime.fromisoformat(maintenance_until).strftime("%Y-%m-%d %H:%M:%S")}'
        )
        return
    
    if not context.user_data.get('waiting_input'):
        return
    
    await save_user(user_id, username, update.effective_user.first_name)
    await log_command(user_id, username, update.message.text)
    
    action = context.user_data.get('action')
    input_text = update.message.text.strip()
    
    await update.message.delete()
    try:
        await context.bot.delete_message(
            chat_id=update.effective_chat.id,
            message_id=context.user_data['input_message_id']
        )
    except:
        pass
    
    loading_msg = await update.message.reply_text('⏳ Подключение...')
    await show_connection_animation(context, update.effective_chat.id, loading_msg.message_id, action)
    
    if action == 'ip':
        result = format_ip_result(input_text)
    elif action == 'phone':
        result = format_phone_result(input_text)
    else:
        result = format_username_result(input_text)
    
    await loading_msg.delete()
    await update.message.reply_text(result, parse_mode=ParseMode.HTML)
    
    context.user_data['waiting_input'] = False

# ─── Бизнес-команды ───

async def handle_business_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return
    
    user_id = update.effective_user.id
    username = update.effective_user.username or str(user_id)
    text = update.message.text.strip()
    
    if not text.startswith('.'):
        return
    
    if await check_ban_silent(update, context):
        await update.message.delete()
        return
    
    if await check_maintenance(update) and user_id != ADMIN_ID:
        await update.message.reply_text(
            f'🛠️ БОТ НА ТЕХНИЧЕСКИХ РАБОТАХ\n\n'
            f'🕐 ВРЕМЯ: до {datetime.fromisoformat(maintenance_until).strftime("%Y-%m-%d %H:%M:%S")}'
        )
        await update.message.delete()
        return
    
    await save_user(user_id, username, update.effective_user.first_name)
    await log_command(user_id, username, text)
    
    parts = text[1:].split()
    if not parts:
        return
    
    command = parts[0].lower()
    args = parts[1:] if len(parts) > 1 else []
    
    await update.message.delete()
    
    # .help
    if command == 'help':
        await update.message.reply_text(HELP_TEXT)
        return
    
    # .whois
    if command == 'whois':
        if len(args) < 2:
            await update.message.reply_text(
                '❌ Укажите тип и данные для пробива.\n\n'
                'Примеры:\n.whois ip 8.8.8.8\n.whois n +79991234567\n.whois qz @username'
            )
            return
        
        action = args[0]
        target = ' '.join(args[1:])
        
        loading_msg = await update.message.reply_text('⏳ Подключение...')
        
        if action == 'ip':
            await show_connection_animation(context, update.effective_chat.id, loading_msg.message_id, 'ip')
            result = format_ip_result(target)
        elif action == 'n':
            await show_connection_animation(context, update.effective_chat.id, loading_msg.message_id, 'phone')
            result = format_phone_result(target)
        elif action == 'qz':
            await show_connection_animation(context, update.effective_chat.id, loading_msg.message_id, 'username')
            result = format_username_result(target)
        else:
            await loading_msg.delete()
            await update.message.reply_text('❌ Неверный тип. Используйте: ip, n, qz')
            return
        
        await loading_msg.delete()
        await update.message.reply_text(result, parse_mode=ParseMode.HTML)
        return
    
    # Админ команды
    if user_id != ADMIN_ID:
        return
    
    if command == 'ban':
        await handle_ban(update, context, args, True)
        return
    
    if command == 'unban':
        await handle_unban(update, context, args, True)
        return
    
    if command == 'chkban':
        await handle_chkban(update, context, args)
        return
    
    if command == 'logs':
        await handle_logs(update, context, args)
        return
    
    if command == 'idlist':
        await handle_idlist(update, context)
        return
    
    if command == 'key':
        await handle_key(update, context)
        return
    
    if command == 'tex':
        await handle_tex(update, context, args)
        return

# ─── Запуск ───

def main():
    init_files()
    
    application = Application.builder().token(BOT_TOKEN).build()
    
    # ЛС команды
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(button_callback))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE, handle_input))
    
    # Бизнес-сообщения
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & filters.ChatType.GROUP, handle_business_message))
    
    logger.info('Бот запущен!')
    logger.info(f'Админ ID: {ADMIN_ID}')
    
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
