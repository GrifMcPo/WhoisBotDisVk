// ===== ВЕРСИЯ 5.0 =====
console.log('🚀 Whois Admin v5.0');

// ===== КОНФИГ =====
const GITHUB_RAW = 'https://raw.githubusercontent.com/GrifMcPo/WhoisBotDisVk/main/data';

// ===== ПЕРЕМЕННЫЕ =====
let sessionActive = false;
let updateInterval = null;

// ===== ФУНКЦИЯ ЧТЕНИЯ ФАЙЛА ЧЕРЕЗ RAW =====
async function readFile(fileName) {
    try {
        const url = `${GITHUB_RAW}/${fileName}?_=${Date.now()}`;
        const res = await fetch(url, {
            cache: 'no-cache',
            headers: {
                'Cache-Control': 'no-cache, no-store, must-revalidate',
                'Pragma': 'no-cache'
            }
        });
        if (!res.ok) {
            if (res.status === 404) {
                console.warn(`Файл ${fileName} не найден`);
                return null;
            }
            throw new Error(`HTTP ${res.status}`);
        }
        return await res.json();
    } catch (e) {
        console.error(`Ошибка чтения ${fileName}:`, e);
        return null;
    }
}

// ===== ФУНКЦИЯ ЗАПИСИ (ПОКА НЕТ) =====
// Для записи используется бот через GitHub API

// ===== ВХОД (ЧИТАЕТ keys.json) =====
async function login() {
    const key = document.getElementById('keyInput').value.trim();
    const errorEl = document.getElementById('loginError');
    const loadingEl = document.getElementById('loginLoading');
    
    if (!key) {
        errorEl.textContent = '❌ Введите ключ';
        return;
    }
    
    errorEl.textContent = '';
    loadingEl.style.display = 'block';
    
    try {
        const keys = await readFile('keys.json');
        
        if (!keys) {
            errorEl.textContent = '❌ Файл keys.json не найден. Получите ключ через /key в боте';
            loadingEl.style.display = 'none';
            return;
        }
        
        if (keys[key]) {
            const expires = new Date(keys[key].expires_at);
            const now = new Date();
            
            if (expires > now) {
                sessionActive = true;
                document.getElementById('loginPage').style.display = 'none';
                document.getElementById('adminPage').style.display = 'block';
                
                // Загружаем все данные
                await loadAllData();
                
                // Обновление каждые 10 секунд
                if (updateInterval) clearInterval(updateInterval);
                updateInterval = setInterval(() => {
                    if (sessionActive) {
                        loadAllData();
                    }
                }, 10000);
                
                errorEl.textContent = '';
                loadingEl.style.display = 'none';
                return;
            } else {
                errorEl.textContent = '❌ Ключ истёк. Получите новый через /key в боте';
            }
        } else {
            errorEl.textContent = '❌ Неверный ключ';
        }
    } catch (e) {
        errorEl.textContent = '❌ Ошибка проверки ключа. Проверьте интернет-соединение.';
        console.error('Login error:', e);
    }
    
    loadingEl.style.display = 'none';
}

function logout() {
    sessionActive = false;
    if (updateInterval) {
        clearInterval(updateInterval);
        updateInterval = null;
    }
    document.getElementById('adminPage').style.display = 'none';
    document.getElementById('loginPage').style.display = 'block';
    document.getElementById('keyInput').value = '';
}

// ===== ЗАГРУЗКА ВСЕХ ДАННЫХ =====
async function loadAllData() {
    await Promise.all([
        loadLogs(),
        loadUsers(),
        loadBans(),
        loadTechStatus()
    ]);
}

// ===== ВКЛАДКИ =====
function showTab(tab) {
    document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
    document.querySelector(`.tab[data-tab="${tab}"]`).classList.add('active');
    document.querySelectorAll('.tab-content').forEach(c => c.style.display = 'none');
    const target = document.getElementById(`${tab}Tab`);
    if (target) target.style.display = 'block';
    
    // При переключении вкладки обновляем данные
    if (tab === 'logs') loadLogs();
    if (tab === 'users') loadUsers();
    if (tab === 'bans') loadBans();
}

// ===== ЗАГРУЗКА ЛОГОВ (читает logs.json) =====
async function loadLogs() {
    const data = await readFile('logs.json');
    const container = document.getElementById('logsList');
    if (!container) return;
    if (!data || data.length === 0) {
        container.innerHTML = '<p style="color:#667799;">📭 Логов пока нет</p>';
        return;
    }
    container.innerHTML = data.slice().reverse().slice(0, 50).map(log => `
        <div class="log-item">
            <span><span class="time">[${log.time || '—'}]</span> <span class="cmd">${log.command || '—'}</span></span>
            <span class="user">${log.username || log.user_id || '—'}</span>
        </div>
    `).join('');
}

// ===== ЗАГРУЗКА ПОЛЬЗОВАТЕЛЕЙ (читает idlist.json) =====
async function loadUsers() {
    const data = await readFile('idlist.json');
    const container = document.getElementById('usersList');
    if (!container) return;
    if (!data || data.length === 0) {
        container.innerHTML = '<p style="color:#667799;">📭 Пользователей пока нет</p>';
        return;
    }
    container.innerHTML = data.map(user => `
        <div class="user-item">
            <span>🆔 ${user.id || '?'}</span>
            <span>👤 ${user.username ? '@' + user.username : 'Нет'}</span>
        </div>
    `).join('');
}

// ===== ЗАГРУЗКА БАНОВ (читает banlist.json) =====
async function loadBans() {
    const data = await readFile('banlist.json');
    const container = document.getElementById('bansList');
    if (!container) return;
    const entries = Object.entries(data || {});
    if (entries.length === 0) {
        container.innerHTML = '<p style="color:#667799;">📭 Банов нет</p>';
        return;
    }
    container.innerHTML = entries.map(([id, info]) => `
        <div class="ban-item">
            <span>🆔 ${id}</span>
            <span>📌 ${info.reason || '—'}</span>
            <span>🕐 ${info.added_at || '—'}</span>
        </div>
    `).join('');
}

// ===== ТЕХРАБОТЫ (читает tech.json) =====
async function loadTechStatus() {
    const data = await readFile('tech.json');
    const status = data && data.active ? 'включены' : 'выключены';
    const btn = document.getElementById('techBtn');
    const statusEl = document.getElementById('techStatus');
    if (btn) btn.textContent = data && data.active ? '❌ Выключить техработы' : '🛠️ Включить техработы';
    if (statusEl) statusEl.textContent = `Техработы: ${status}`;
}

async function toggleTech() {
    alert('Для управления техработами используй бот-команды:\n.tex on [время]\n.tex off');
}

// ===== ENTER =====
document.addEventListener('DOMContentLoaded', function() {
    const keyInput = document.getElementById('keyInput');
    if (keyInput) {
        keyInput.addEventListener('keydown', function(e) {
            if (e.key === 'Enter') login();
        });
    }
});

console.log('🚀 Whois Admin v5.0 loaded');
console.log('📁 GITHUB_RAW:', GITHUB_RAW);
console.log('📂 Файлы: logs.json, idlist.json, banlist.json, keys.json, tech.json');
console.log('🔄 Обновление данных каждые 10 секунд');
