// ===== КОНФИГ =====
const GITHUB_RAW = 'https://raw.githubusercontent.com/GrifMcPo/WhoisBotDisVk/main/data';

// ===== ПЕРЕМЕННЫЕ =====
let currentKey = null;
let sessionActive = false;

// ===== ВХОД =====
function login() {
    const key = document.getElementById('keyInput').value.trim();
    const errorEl = document.getElementById('loginError');
    
    if (!key) {
        errorEl.textContent = '❌ Введите ключ';
        return;
    }
    
    if (key.startsWith('ADMIN_') && key.length === 10) {
        sessionActive = true;
        currentKey = key;
        document.getElementById('loginPage').style.display = 'none';
        document.getElementById('adminPage').style.display = 'block';
        loadLogs();
        loadUsers();
        loadBans();
        loadTechStatus();
        errorEl.textContent = '';
    } else {
        errorEl.textContent = '❌ Неверный ключ';
    }
}

function logout() {
    sessionActive = false;
    currentKey = null;
    document.getElementById('adminPage').style.display = 'none';
    document.getElementById('loginPage').style.display = 'block';
    document.getElementById('keyInput').value = '';
}

// ===== ВКЛАДКИ =====
function showTab(tab) {
    document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
    document.querySelector(`.tab[data-tab="${tab}"]`).classList.add('active');
    document.querySelectorAll('.tab-content').forEach(c => c.style.display = 'none');
    const target = document.getElementById(`${tab}Tab`);
    if (target) target.style.display = 'block';
}

// ===== ЗАГРУЗКА ЛОГОВ =====
async function loadLogs() {
    try {
        const res = await fetch(`${GITHUB_RAW}/logs.json?_=${Date.now()}`, {
            headers: { 'Cache-Control': 'no-cache' }
        });
        if (!res.ok) throw new Error('HTTP ' + res.status);
        const data = await res.json();
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
    } catch (e) {
        console.error('Logs error:', e);
        const container = document.getElementById('logsList');
        if (container) container.innerHTML = '<p style="color:#ff4455;">❌ Ошибка загрузки логов</p>';
    }
}

// ===== ЗАГРУЗКА ПОЛЬЗОВАТЕЛЕЙ =====
async function loadUsers() {
    try {
        const res = await fetch(`${GITHUB_RAW}/idlist.json?_=${Date.now()}`, {
            headers: { 'Cache-Control': 'no-cache' }
        });
        if (!res.ok) throw new Error('HTTP ' + res.status);
        const data = await res.json();
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
    } catch (e) {
        console.error('Users error:', e);
        const container = document.getElementById('usersList');
        if (container) container.innerHTML = '<p style="color:#ff4455;">❌ Ошибка загрузки пользователей</p>';
    }
}

// ===== ЗАГРУЗКА БАНОВ =====
async function loadBans() {
    try {
        const res = await fetch(`${GITHUB_RAW}/banlist.json?_=${Date.now()}`, {
            headers: { 'Cache-Control': 'no-cache' }
        });
        if (!res.ok) throw new Error('HTTP ' + res.status);
        const data = await res.json();
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
    } catch (e) {
        console.error('Bans error:', e);
        const container = document.getElementById('bansList');
        if (container) container.innerHTML = '<p style="color:#ff4455;">❌ Ошибка загрузки банов</p>';
    }
}

// ===== ТЕХРАБОТЫ =====
async function loadTechStatus() {
    try {
        const res = await fetch(`${GITHUB_RAW}/tech.json?_=${Date.now()}`, {
            headers: { 'Cache-Control': 'no-cache' }
        });
        if (!res.ok) throw new Error('HTTP ' + res.status);
        const data = await res.json();
        const status = data.active ? 'включены' : 'выключены';
        const btn = document.getElementById('techBtn');
        const statusEl = document.getElementById('techStatus');
        if (btn) btn.textContent = data.active ? '❌ Выключить техработы' : '🛠️ Включить техработы';
        if (statusEl) statusEl.textContent = `Техработы: ${status}`;
    } catch (e) {
        console.error('Tech error:', e);
        const statusEl = document.getElementById('techStatus');
        if (statusEl) statusEl.textContent = 'Техработы: неизвестно';
    }
}

async function toggleTech() {
    alert('Для управления техработами используй бот-команды:\n.tex on [время]\n.tex off');
}

// ===== АВТО-ОБНОВЛЕНИЕ =====
setInterval(() => {
    if (sessionActive) {
        loadLogs();
        loadUsers();
        loadBans();
    }
}, 30000);

// ===== ENTER =====
document.addEventListener('DOMContentLoaded', function() {
    const keyInput = document.getElementById('keyInput');
    if (keyInput) {
        keyInput.addEventListener('keydown', function(e) {
            if (e.key === 'Enter') login();
        });
    }
});

console.log('🚀 Whois Admin loaded');
