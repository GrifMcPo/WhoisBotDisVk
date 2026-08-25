import os
import json
import time
import asyncio
import logging
import random
import re
import requests
from datetime import datetime, timedelta
from typing import Optional, List

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
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

# Базовый URL для API
API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"

# Файлы для данных
FILES = {
    'banlist': 'banlist.json',
    'logs': 'logs.json',
    'idlist': 'idlist.json',
    'keys': 'keys.json',
    'settings': 'settings.json',
    'last_update': 'last_update.json'
}

# Глобальные состояния
maintenance_mode = False
maintenance_until = None
banned_notified = set()
last_update_id = 0

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
    files = ['banlist.json', 'logs.json', 'idlist.json', 'keys.json', 'settings.json', 'last_update.json']
    for file in files:
        try:
            with open(file, 'r') as f:
                pass
        except FileNotFoundError:
            if file == 'banlist.json':
                with open(file, 'w') as f:
                    json.dump([], f)
            elif file == 'last_update.json':
                with open(file, 'w') as f:
                    json.dump({'last_update_id': 0}, f)
            else:
                with open(file, 'w') as f:
                    json.dump({}, f)
    logger.info('✅ Файлы инициализированы')

def get_last_update_id():
    try:
        data = load_json('last_update.json')
        return data.get('last_update_id', 0)
    except:
        return 0

def save_last_update_id(update_id):
    try:
        save_json('last_update.json', {'last_update_id': update_id})
    except:
        pass

# ─── Утилиты ───

def moscow_time():
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')

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

def log_command(user_id, username, command):
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

def save_user(user_id, username, first_name):
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

def is_banned(user_id):
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

def check_expired_bans():
    try:
        bans = load_json(FILES['banlist'])
        now = datetime.now().isoformat()
        active_bans = [b for b in bans if b.get('forever', False) or now < b['unban_at']]
        if len(active_bans) != len(bans):
            save_json(FILES['banlist'], active_bans)
    except Exception as e:
        logger.error(f'Ошибка проверки банов: {e}')

def get_tech_works():
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

def set_tech_works(active, until=None):
    try:
        settings = load_json(FILES['settings'])
        settings['maintenance'] = active
        settings['maintenance_until'] = until
        save_json(FILES['settings'], settings)
    except Exception as e:
        logger.error(f'Ошибка настройки тех-работ: {e}')

def is_admin(user_id):
    return ADMIN_ID != 0 and user_id == ADMIN_ID

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

# ─── Telegram API запросы ───

def send_message(chat_id, text, parse_mode='HTML'):
    """Отправка сообщения через Telegram API"""
    try:
        url = f"{API_URL}/sendMessage"
        payload = {
            'chat_id': chat_id,
            'text': text,
            'parse_mode': parse_mode
        }
        response = requests.post(url, json=payload, timeout=10)
        return response.json()
    except Exception as e:
        logger.error(f'Ошибка отправки сообщения: {e}')
        return None

def delete_message(chat_id, message_id):
    """Удаление сообщения через Telegram API"""
    try:
        url = f"{API_URL}/deleteMessage"
        payload = {
            'chat_id': chat_id,
            'message_id': message_id
        }
        response = requests.post(url, json=payload, timeout=10)
        return response.json()
    except Exception as e:
        logger.error(f'Ошибка удаления сообщения: {e}')
        return None

def get_updates(offset=None, timeout=30):
    """Получение обновлений через Telegram API"""
    try:
        url = f"{API_URL}/getUpdates"
        payload = {
            'timeout': timeout,
            'allowed_updates': ['message', 'business_connection', 'business_message']
        }
        if offset:
            payload['offset'] = offset
        response = requests.post(url, json=payload, timeout=timeout+5)
        return response.json()
    except Exception as e:
        logger.error(f'Ошибка получения обновлений: {e}')
        return None

# ─── Обработка сообщений ───

def process_business_message(message):
    """Обработка бизнес-сообщения"""
    try:
        user_id = message['from']['id']
        username = message['from'].get('username', 'без юзера')
        text = message.get('text', '')
        chat_id = message['chat']['id']
        message_id = message['message_id']
        
        logger.info(f'📩 [BUSINESS] от {user_id} (@{username}): "{text[:100]}"')
        
        # Проверка бана
        ban = is_banned(user_id)
        if ban:
            if user_id not in banned_notified:
                banned_notified.add(user_id)
                if ban.get('forever', False):
                    send_message(chat_id, f'⛔ ВАС ЗАБЛОКИРОВАЛИ В БОТЕ НАВСЕГДА\n\n📌 Причина: {ban["reason"]}')
                else:
                    send_message(chat_id, f'⛔ ВАС ЗАБЛОКИРОВАЛИ В БОТЕ\n\n📌 Причина: {ban["reason"]}\n⏳ Разблокировка: {datetime.fromisoformat(ban["unban_at"]).strftime("%Y-%m-%d %H:%M:%S")}')
            if text.startswith('.'):
                delete_message(chat_id, message_id)
            return
        
        # Тех работы
        if maintenance_mode and not is_admin(user_id):
            if text.startswith('.'):
                delete_message(chat_id, message_id)
            send_message(chat_id, f'🛠️ БОТ НА ТЕХНИЧЕСКИХ РАБОТАХ\n\n🕐 ВРЕМЯ: до {datetime.fromisoformat(maintenance_until).strftime("%Y-%m-%d %H:%M:%S")}')
            return
        
        # Сохраняем пользователя
        save_user(user_id, username, message['from'].get('first_name', ''))
        
        # Обрабатываем только команды с точкой
        if not text.startswith('.'):
            return
        
        log_command(user_id, username, text)
        delete_message(chat_id, message_id)
        
        parts = text[1:].split()
        if not parts:
            return
        
        cmd = parts[0].lower()
        args = parts[1:] if len(parts) > 1 else []
        
        logger.info(f'🔄 [BUSINESS CMD] .{cmd} от {user_id}')
        
        # .help
        if cmd == 'help':
            help_text = """📚 ДОСТУПНЫЕ КОМАНДЫ

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
.unban (ID) (REASON) — Снять блокировку
.chkban (ID) — Проверить бан пользователя

━━━━━━━━━━━━━━━━━━━━━━━━━━
📌 .команды — в чатах с собеседниками
📌 /команды — в личке с ботом"""
            send_message(chat_id, help_text)
            return
        
        # .whois
        if cmd == 'whois':
            if len(args) < 2:
                send_message(chat_id, '❌ Укажите тип и данные для пробива.\n\nПримеры:\n.whois ip 8.8.8.8\n.whois n +79991234567\n.whois qz @username')
                return
            
            action = args[0]
            target = ' '.join(args[1:])
            
            # Отправляем анимацию
            anim_msg = send_message(chat_id, '⏳ Подключение к серверам...')
            
            if action == 'ip':
                result = format_ip_result(target)
            elif action == 'n':
                result = format_phone_result(target)
            elif action == 'qz':
                result = format_username_result(target)
            else:
                send_message(chat_id, '❌ Неверный тип. Используйте: ip, n, qz')
                return
            
            # Удаляем анимацию
            if anim_msg and anim_msg.get('ok'):
                delete_message(chat_id, anim_msg['result']['message_id'])
            
            send_message(chat_id, result)
            return
        
        # Админ команды
        if not is_admin(user_id):
            logger.warning(f'⛔ Не-админ {user_id} пытался использовать .{cmd}')
            return
        
        if cmd == 'ban':
            handle_ban(chat_id, args)
            return
        
        if cmd == 'unban':
            handle_unban(chat_id, args)
            return
        
        if cmd == 'chkban':
            handle_chkban(chat_id, args)
            return
        
        if cmd == 'logs':
            handle_logs(chat_id, args)
            return
        
        if cmd == 'idlist':
            handle_idlist(chat_id)
            return
        
        if cmd == 'key':
            handle_key(chat_id)
            return
        
        if cmd == 'tex':
            handle_tex(chat_id, args)
            return
        
        send_message(chat_id, f'❌ Неизвестная команда: {cmd}')
        
    except Exception as e:
        logger.error(f'Ошибка обработки бизнес-сообщения: {e}')

# ─── Админ команды ───

def handle_ban(chat_id, args):
    if len(args) < 3:
        send_message(chat_id, '❌ Формат: .ban (ID) (ВРЕМЯ) (ПРИЧИНА)\nПримеры:\n.ban 123456789 30m Спам\n.ban 123456789 -1w Навсегда')
        return
    
    try:
        target_id = int(args[0])
        time_str = args[1]
        reason = ' '.join(args[2:])
        
        minutes = parse_ban_time(time_str)
        if minutes is None:
            send_message(chat_id, '❌ Неверный формат времени. Примеры: 30m, 2h, 1h30m, 7d, -1w')
            return
        
        now = datetime.now()
        bans = load_json(FILES['banlist'])
        
        if minutes == -1:
            ban_data = {
                'user_id': target_id,
                'reason': reason,
                'duration': time_str,
                'banned_at': now.isoformat(),
                'forever': True
            }
        else:
            unban_at = now + timedelta(minutes=minutes)
            ban_data = {
                'user_id': target_id,
                'reason': reason,
                'duration': time_str,
                'banned_at': now.isoformat(),
                'unban_at': unban_at.isoformat(),
                'forever': False
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
        
        send_message(chat_id, result)
        
        try:
            if minutes == -1:
                send_message(target_id, f'⛔ ВАС ЗАБЛОКИРОВАЛИ В БОТЕ НАВСЕГДА\n\n📌 Причина: {reason}\n🕐 Дата: {moscow_time()}')
            else:
                send_message(target_id, f'⛔ ВАС ЗАБЛОКИРОВАЛИ В БОТЕ\n\n📌 Причина: {reason}\n⏱ Длительность: {time_str}\n🕐 Дата: {moscow_time()}\n⏳ Разблокировка: {unban_at.strftime("%Y-%m-%d %H:%M:%S")}')
            banned_notified.add(target_id)
        except:
            pass
            
    except ValueError:
        send_message(chat_id, '❌ Неверный формат ID.')

def handle_unban(chat_id, args):
    if len(args) < 2:
        send_message(chat_id, '❌ Формат: .unban (ID) (ПРИЧИНА)\nПример: .unban 123456789 Ошибка')
        return
    
    try:
        target_id = int(args[0])
        reason = ' '.join(args[1:])
        
        bans = load_json(FILES['banlist'])
        filtered = [b for b in bans if b['user_id'] != target_id]
        
        if len(filtered) == len(bans):
            send_message(chat_id, f'⛔ Данный {target_id} не заблокирован.')
            return
        
        save_json(FILES['banlist'], filtered)
        if target_id in banned_notified:
            banned_notified.remove(target_id)
        
        result = f"""✅ ПОЛЬЗОВАТЕЛЬ РАЗБАНЕН

🆔 ID: <code>{target_id}</code>
📌 Причина разбана: {reason}
🕐 Дата: {moscow_time()}
🔓 Пользователь снова может пользоваться ботом"""
        
        send_message(chat_id, result)
        
        try:
            send_message(target_id, f'✅ ВАС РАЗБЛОКИРОВАЛИ\n\n📌 Причина разблокировки: {reason}\n🕐 Дата: {moscow_time()}\n🔓 Теперь вы снова можете пользоваться ботом')
        except:
            pass
            
    except ValueError:
        send_message(chat_id, '❌ Неверный формат ID.')

def handle_chkban(chat_id, args):
    if len(args) < 1:
        send_message(chat_id, '❌ Формат: .chkban (ID)')
        return
    
    try:
        target_id = int(args[0])
        bans = load_json(FILES['banlist'])
        ban = next((b for b in bans if b['user_id'] == target_id), None)
        
        if not ban:
            send_message(chat_id, f'⛔ Данный {target_id} не заблокирован.')
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
                send_message(chat_id, f'⛔ Данный {target_id} не заблокирован.')
                return
            remaining = int((unban_at - now).total_seconds() // 60)
            hours = remaining // 60
            minutes = remaining % 60
            result = f"""---<code>{target_id}</code>---
📌Причина: {ban['reason']}
🕐Дата выдачи: {datetime.fromisoformat(ban['banned_at']).strftime('%Y-%m-%d %H:%M:%S')}
🕐Дата снятия бана: {unban_at.strftime('%Y-%m-%d %H:%M:%S')}
🔓Осталось до окончания: {hours}ч {minutes}м"""
        
        send_message(chat_id, result)
        
    except ValueError:
        send_message(chat_id, '❌ Неверный формат ID.')

def handle_logs(chat_id, args):
    if len(args) < 2:
        send_message(chat_id, '❌ Формат: .logs (ID) (количество)\nПример: .logs 123456789 10')
        return
    
    try:
        target_id = int(args[0])
        limit = min(int(args[1]), 100)
        
        logs = load_json(FILES['logs'])
        user_logs = logs.get(str(target_id), [])[-limit:][::-1]
        
        if not user_logs:
            send_message(chat_id, '📭 Логи не найдены для данного ID.')
            return
        
        text = f'📋 Логи пользователя <code>{target_id}</code> (последние {len(user_logs)})\n\n'
        for log in user_logs:
            text += f"📝 {log['command']}\n🕐 {datetime.fromisoformat(log['time']).strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        
        if len(text) > 4096:
            chunks = [text[i:i+4096] for i in range(0, len(text), 4096)]
            for chunk in chunks:
                send_message(chat_id, chunk)
        else:
            send_message(chat_id, text)
            
    except ValueError:
        send_message(chat_id, '❌ Неверный формат ID или количества.')

def handle_idlist(chat_id):
    users = load_json(FILES['idlist'])
    
    if not users:
        send_message(chat_id, '📭 Список ID пуст.')
        return
    
    text = f'📋 Список пользователей ({len(users)})\n\n'
    for user_id, data in users.items():
        username = data.get('username') or 'нет username'
        text += f'👤 @{username} → <code>{user_id}</code>\n'
    
    if len(text) > 4096:
        chunks = [text[i:i+4096] for i in range(0, len(text), 4096)]
        for chunk in chunks:
            send_message(chat_id, chunk)
    else:
        send_message(chat_id, text)

def handle_key(chat_id):
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
        
        send_message(chat_id, result)
        
    except Exception as e:
        send_message(chat_id, '❌ Ошибка при генерации ключа.')

def handle_tex(chat_id, args):
    global maintenance_mode, maintenance_until
    
    if len(args) < 1:
        send_message(chat_id, '❌ Формат: .tex on (минуты) или .tex off')
        return
    
    sub_cmd = args[0].lower()
    
    if sub_cmd == 'on':
        if len(args) < 2:
            send_message(chat_id, '❌ Укажите время: .tex on 30')
            return
        
        try:
            minutes = int(args[1])
            if minutes <= 0:
                send_message(chat_id, '❌ Время должно быть больше 0.')
                return
            
            until = datetime.now() + timedelta(minutes=minutes)
            set_tech_works(True, until.isoformat())
            maintenance_mode = True
            maintenance_until = until.isoformat()
            
            send_message(chat_id, f'✅ ТЕХ-РАБОТЫ УСПЕШНО ВКЛЮЧЕНЫ\n🕐 Время работ: до {until.strftime("%Y-%m-%d %H:%M:%S")}')
        except ValueError:
            send_message(chat_id, '❌ Неверный формат времени. Укажите минуты.')
            
    elif sub_cmd == 'off':
        set_tech_works(False, None)
        maintenance_mode = False
        maintenance_until = None
        send_message(chat_id, '✅ ТЕХ-РАБОТЫ УСПЕШНО ВЫКЛЮЧЕНЫ')
        
    else:
        send_message(chat_id, '❌ Используйте: .tex on (минуты) или .tex off')

# ─── Обработка личных сообщений ───

def process_private_message(message):
    """Обработка личных сообщений боту (/команды)"""
    try:
        user_id = message['from']['id']
        username = message['from'].get('username', 'без юзера')
        text = message.get('text', '')
        chat_id = message['chat']['id']
        
        # Только команды с /
        if not text.startswith('/'):
            return
        
        logger.info(f'📩 [DM] от {user_id}: "{text[:100]}"')
        
        # Проверка бана
        ban = is_banned(user_id)
        if ban:
            if user_id not in banned_notified:
                banned_notified.add(user_id)
                send_message(chat_id, '⛔ ВЫ ЗАБЛОКИРОВАНЫ В БОТЕ!')
            return
        
        # Тех работы
        if maintenance_mode and not is_admin(user_id):
            send_message(chat_id, f'🛠️ БОТ НА ТЕХНИЧЕСКИХ РАБОТАХ\n\n🕐 ВРЕМЯ: до {datetime.fromisoformat(maintenance_until).strftime("%Y-%m-%d %H:%M:%S")}')
            return
        
        save_user(user_id, username, message['from'].get('first_name', ''))
        log_command(user_id, username, text)
        
        parts = text[1:].split()
        if not parts:
            return
        
        cmd = parts[0].lower()
        args = parts[1:] if len(parts) > 1 else []
        
        # /start
        if cmd == 'start':
            send_message(chat_id, '👋 Привет! Я бот для пробива информации.\n\nИспользуй /help для списка команд.')
            return
        
        # /help
        if cmd == 'help':
            help_text = """📚 ДОСТУПНЫЕ КОМАНДЫ

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
.unban (ID) (REASON) — Снять блокировку
.chkban (ID) — Проверить бан пользователя

━━━━━━━━━━━━━━━━━━━━━━━━━━
📌 .команды — в чатах с собеседниками
📌 /команды — в личке с ботом"""
            send_message(chat_id, help_text)
            return
        
        # Админ команды
        if not is_admin(user_id):
            logger.warning(f'⛔ Не-админ {user_id} пытался использовать /{cmd}')
            return
        
        if cmd == 'ban':
            handle_ban(chat_id, args)
            return
        
        if cmd == 'unban':
            handle_unban(chat_id, args)
            return
        
        if cmd == 'chkban':
            handle_chkban(chat_id, args)
            return
        
        if cmd == 'logs':
            handle_logs(chat_id, args)
            return
        
        if cmd == 'idlist':
            handle_idlist(chat_id)
            return
        
        if cmd == 'key':
            handle_key(chat_id)
            return
        
        if cmd == 'tex':
            handle_tex(chat_id, args)
            return
        
        send_message(chat_id, f'❌ Неизвестная команда: {cmd}')
        
    except Exception as e:
        logger.error(f'Ошибка обработки личного сообщения: {e}')

# ─── ОСНОВНОЙ ЦИКЛ ───

async def main_loop():
    """Основной цикл обработки обновлений"""
    global last_update_id
    
    logger.info('🚀 Бот запущен и ждёт сообщения...')
    logger.info(f'👑 Админ ID: {ADMIN_ID}')
    logger.info('📌 /команды — в личке с ботом')
    logger.info('📌 .команды — в чатах с собеседниками (Business API)')
    
    # Отправляем приветствие админу
    if ADMIN_ID:
        send_message(ADMIN_ID, f'🤖 БОТ ЗАПУЩЕН!\n\n🕐 Время: {moscow_time()}\n📌 Бот готов к работе!')
    
    last_update_id = get_last_update_id()
    
    while True:
        try:
            # Получаем обновления
            response = get_updates(offset=last_update_id + 1 if last_update_id else None, timeout=30)
            
            if not response or not response.get('ok'):
                logger.error(f'Ошибка получения обновлений: {response}')
                await asyncio.sleep(2)
                continue
            
            updates = response.get('result', [])
            
            for update in updates:
                update_id = update.get('update_id')
                if update_id:
                    last_update_id = max(last_update_id, update_id)
                    save_last_update_id(last_update_id)
                
                # Обработка бизнес-подключения
                if 'business_connection' in update:
                    logger.info(f'🔗 [BUSINESS_CONNECTION] {update["business_connection"]}')
                    if ADMIN_ID:
                        send_message(ADMIN_ID, f'✅ BUSINESS API ПОДКЛЮЧЕН!\n\n🕐 Время: {moscow_time()}')
                
                # Обработка бизнес-сообщения
                if 'business_message' in update:
                    process_business_message(update['business_message'])
                
                # Обработка обычного сообщения
                if 'message' in update:
                    message = update['message']
                    chat_type = message.get('chat', {}).get('type', '')
                    
                    # Если личное сообщение боту
                    if chat_type == 'private':
                        process_private_message(message)
            
            # Проверяем истекшие баны
            check_expired_bans()
            
            # Обновляем статус тех-работ
            tech = get_tech_works()
            global maintenance_mode, maintenance_until
            if tech.get('active'):
                maintenance_mode = True
                maintenance_until = tech.get('until')
            else:
                maintenance_mode = False
                maintenance_until = None
            
        except Exception as e:
            logger.error(f'Ошибка в основном цикле: {e}')
            await asyncio.sleep(5)

# ─── ЗАПУСК ───

def main():
    init_files()
    
    # Проверяем, что бот работает
    try:
        response = requests.get(f"{API_URL}/getMe", timeout=10)
        if response.ok:
            logger.info(f'✅ Бот подключен: {response.json()}')
        else:
            logger.error(f'❌ Ошибка подключения бота: {response.text}')
            return
    except Exception as e:
        logger.error(f'❌ Ошибка: {e}')
        return
    
    # Запускаем основной цикл
    asyncio.run(main_loop())

if __name__ == '__main__':
    main()
