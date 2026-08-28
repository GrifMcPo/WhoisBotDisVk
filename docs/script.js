// =====================================================
// RIPSAVE — ВЕБ-КОНСОЛЬ ДЛЯ ТЕЛЕГРАМ БОТА
// Подключение через Supabase
// =====================================================

// ===== КОНФИГУРАЦИЯ =====
const SUPABASE_URL = 'https://doidpainkowqiquvrzpg.supabase.co';
const SUPABASE_KEY = 'sb_publishable_AvtE4QKUwNVPnFL4kRltjA_lVCLGMAB'; // anon key

// ===== ГЛОБАЛЬНЫЕ ПЕРЕМЕННЫЕ =====
let appKey = localStorage.getItem('app_key') || '';
let userId = null;
let isConnected = false;

// ===== DOM ЭЛЕМЕНТЫ =====
const keyInput = document.getElementById('keyInput');
const connectBtn = document.getElementById('connectBtn');
const status = document.getElementById('status');

const logContainer = document.getElementById('logContainer');
const commandInput = document.getElementById('commandInput');
const sendBtn = document.getElementById('sendBtn');
const clearBtn = document.getElementById('clearBtn');
const logoutBtn = document.getElementById('logoutBtn');
const connectionStatus = document.getElementById('connectionStatus');

// ===== ПОМОЩНИКИ =====

function addLog(text, className = '') {
    if (!logContainer) return;
    const entry = document.createElement('div');
    entry.className = `log-entry ${className}`;
    entry.textContent = text;
    logContainer.appendChild(entry);
    logContainer.scrollTop = logContainer.scrollHeight;
}

function setStatus(text, type = '') {
    if (!status) return;
    status.textContent = text;
    status.className = `status ${type}`;
}

function setConnectionStatus(online) {
    if (!connectionStatus) return;
    connectionStatus.textContent = online ? '✅ Online' : '❌ Offline';
    connectionStatus.className = `status-badge ${online ? '' : 'offline'}`;
    isConnected = online;
}

// ===== РАБОТА С SUPABASE =====

async function checkKey(key) {
    try {
        // Проверяем ключ в таблице keys
        const response = await fetch(`${SUPABASE_URL}/rest/v1/keys?key=eq.${key}`, {
            headers: {
                'apikey': SUPABASE_KEY,
                'Authorization': `Bearer ${SUPABASE_KEY}`
            }
        });
        
        if (!response.ok) throw new Error('Ошибка проверки ключа');
        
        const data = await response.json();
        
        if (data.length === 0) {
            return { success: false, error: 'Неверный ключ' };
        }
        
        const keyData = data[0];
        
        // Проверяем срок действия
        if (keyData.expires_at) {
            const expires = new Date(keyData.expires_at);
            if (expires < new Date()) {
                return { success: false, error: 'Ключ истёк' };
            }
        }
        
        userId = keyData.user_id;
        return { success: true, userId: keyData.user_id };
        
    } catch (error) {
        return { success: false, error: error.message };
    }
}

async function executeCommand(command) {
    try {
        // Сохраняем команду в logs (как команду от пользователя)
        await fetch(`${SUPABASE_URL}/rest/v1/logs`, {
            method: 'POST',
            headers: {
                'apikey': SUPABASE_KEY,
                'Authorization': `Bearer ${SUPABASE_KEY}`,
                'Content-Type': 'application/json',
                'Prefer': 'return=representation'
            },
            body: JSON.stringify({
                user_id: userId,
                command: command,
                target: '',
                username: 'web_console',
                time: new Date().toLocaleString('ru-RU')
            })
        });
        
        // Здесь можно добавить логику ожидания ответа
        // Например, через polling или WebSocket
        
        return { success: true, result: `✅ Команда "${command}" отправлена` };
        
    } catch (error) {
        return { success: false, error: error.message };
    }
}

// ===== ОБРАБОТЧИКИ =====

// Вход
if (connectBtn) {
    connectBtn.addEventListener('click', async () => {
        const key = keyInput.value.trim();
        if (!key) {
            setStatus('Введите ключ доступа', 'error');
            return;
        }
        
        setStatus('⏳ Проверка ключа...', '');
        
        const result = await checkKey(key);
        if (result.success) {
            appKey = key;
            localStorage.setItem('app_key', key);
            setStatus('✅ Ключ принят!', 'success');
            setTimeout(() => {
                window.location.href = 'console.html';
            }, 500);
        } else {
            setStatus(`❌ ${result.error}`, 'error');
        }
    });
}

// Вход по Enter
if (keyInput) {
    keyInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') connectBtn.click();
    });
}

// Консоль — отправка команды
async function handleSendCommand() {
    if (!commandInput) return;
    const command = commandInput.value.trim();
    if (!command) return;
    
    addLog(`> ${command}`, 'command');
    commandInput.value = '';
    
    const result = await executeCommand(command);
    if (result.success) {
        addLog(result.result, 'result');
    } else {
        addLog(`❌ ${result.error}`, 'error');
    }
}

if (sendBtn) {
    sendBtn.addEventListener('click', handleSendCommand);
}

if (commandInput) {
    commandInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') sendBtn.click();
    });
}

// Быстрые команды
document.querySelectorAll('.quick-btn').forEach(btn => {
    btn.addEventListener('click', () => {
        const cmd = btn.dataset.cmd;
        if (commandInput) {
            commandInput.value = cmd;
        }
        sendBtn.click();
    });
});

// Очистка логов
if (clearBtn) {
    clearBtn.addEventListener('click', () => {
        if (logContainer) {
            logContainer.innerHTML = '';
            addLog('━━━ ЛОГИ ОЧИЩЕНЫ ━━━', 'system');
        }
    });
}

// Выход
if (logoutBtn) {
    logoutBtn.addEventListener('click', () => {
        localStorage.removeItem('app_key');
        window.location.href = 'index.html';
    });
}

// ===== АВТО-ВХОД НА СТРАНИЦЕ КОНСОЛИ =====
if (window.location.pathname.includes('console.html')) {
    const savedKey = localStorage.getItem('app_key');
    if (!savedKey) {
        window.location.href = 'index.html';
    } else {
        // Проверяем ключ
        checkKey(savedKey).then(result => {
            if (!result.success) {
                localStorage.removeItem('app_key');
                window.location.href = 'index.html';
            } else {
                userId = result.userId;
                setConnectionStatus(true);
                addLog('✅ Подключено к боту', 'system');
                addLog(`🔑 Ключ: ${savedKey.substring(0, 10)}...`, 'system');
                addLog('📡 Введите команду или выберите быструю', 'system');
                addLog('', '');
            }
        });
    }
}

// ===== ПЕРИОДИЧЕСКАЯ ПРОВЕРКА СТАТУСА =====
if (window.location.pathname.includes('console.html')) {
    setInterval(async () => {
        if (!isConnected) return;
        try {
            const response = await fetch(`${SUPABASE_URL}/rest/v1/tech?select=active&limit=1`, {
                headers: {
                    'apikey': SUPABASE_KEY,
                    'Authorization': `Bearer ${SUPABASE_KEY}`
                }
            });
            if (!response.ok) throw new Error();
            const data = await response.json();
            if (data.length > 0 && data[0].active) {
                addLog('🛠️ Технические работы включены', 'system');
            }
        } catch (e) {
            // игнорируем
        }
    }, 30000);
}
