// ===== ВЕРСИЯ 14.0 — КЛЮЧИ В HTML, ДАННЫЕ ИЗ ФАЙЛОВ =====
console.log('🚀 RCON Admin v14.0');

// ===== КОНФИГ =====
const DATA_PATH = './data';

// ===== ПЕРЕМЕННЫЕ =====
let updateInterval = null;

// ===== ФУНКЦИЯ ЧТЕНИЯ ФАЙЛА =====
async function readFile(fileName) {
    try {
        const url = `${DATA_PATH}/${fileName}?_=${Date.now()}`;
        const res = await fetch(url, {
            cache: 'no-cache',
            headers: {
                'Cache-Control': 'no-cache, no-store, must-revalidate',
                'Pragma': 'no-cache'
            }
        });
        if (!res.ok) {
            if (res.status === 404) {
                console.warn(`⚠️ Файл ${fileName} не найден`);
                return null;
            }
            throw new Error(`HTTP ${res.status}`);
        }
        return await res.json();
    } catch (e) {
        console.error(`❌ Ошибка чтения ${fileName}:`, e.message);
        return null;
    }
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

// ===== ЗАГРУЗКА ЛОГОВ (logs.json) =====
async function loadLogs() {
    const data = await readFile('logs.json');
    const container = document.getElementById('logsList');
    if (!container) return;
    if (!data || data.length === 0) {
        container.innerHTML = '<div style="color:#667799;">📭 Логов пока нет</div>';
        return;
    }
    container.innerHTML = data.slice().reverse().slice(0, 50).map(log => `
        <div class="log-item">
            <span><span class="time">[${log.time || '—'}]</span> ${log.command || '—'}</span>
            <span>${log.username || log.user_id || '—'}</span>
        </div>
    `).join('');
}

// ===== ЗАГРУЗКА ПОЛЬЗОВАТЕЛЕЙ (idlist.json) =====
async function loadUsers() {
    const data = await readFile('idlist.json');
    const container = document.getElementById('usersList');
    if (!container) return;
    if (!data || data.length === 0) {
        container.innerHTML = '<div style="color:#667799;">📭 Пользователей пока нет</div>';
        return;
    }
    container.innerHTML = data.map(user => `
        <div class="user-item">
            <span>🆔 ${user.id || '?'}</span>
            <span>👤 ${user.username ? '@' + user.username : 'Нет'}</span>
        </div>
    `).join('');
}

// ===== ЗАГРУЗКА БАНОВ (banlist.json) =====
async function loadBans() {
    const data = await readFile('banlist.json');
    const container = document.getElementById('bansList');
    if (!container) return;
    const entries = Object.entries(data || {});
    if (entries.length === 0) {
        container.innerHTML = '<div style="color:#667799;">📭 Банов нет</div>';
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

// ===== ЗАГРУЗКА ТЕХРАБОТ (tech.json) =====
async function loadTechStatus() {
    const data = await readFile('tech.json');
    const statusEl = document.getElementById('techStatus');
    const btn = document.getElementById('techBtn');
    if (!statusEl) return;
    const isActive = data && data.active;
    statusEl.textContent = `🛠️ Техработы: ${isActive ? 'ВКЛЮЧЕНЫ 🔴' : 'выключены 🟢'}`;
    if (btn) {
        btn.textContent = isActive ? '❌ Выключить техработы' : '🛠️ Включить техработы';
        btn.style.background = isActive ? '#ff4455' : '#1a6aff';
    }
}

// ===== ВКЛАДКИ =====
function showTab(tab) {
    document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
    document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
    const btn = document.querySelector(`.tab-btn[data-tab="${tab}"]`);
    if (btn) btn.classList.add('active');
    const content = document.getElementById(`tab-${tab}`);
    if (content) content.classList.add('active');
    
    if (tab === 'logs') loadLogs();
    if (tab === 'users') loadUsers();
    if (tab === 'bans') loadBans();
}

// ===== ТЕХРАБОТЫ (только просмотр) =====
function toggleTech() {
    alert('📌 Управление техработами через бота:\n.tex on [время] — включить\n.tex off — выключить');
}

// ===== ИНИЦИАЛИЗАЦИЯ =====
document.addEventListener('DOMContentLoaded', function() {
    console.log('🔧 Инициализация RCON Admin v14.0');
    
    // Если уже на админ-панели — загружаем данные
    if (document.getElementById('adminPage').style.display !== 'none') {
        loadAllData();
        if (updateInterval) clearInterval(updateInterval);
        updateInterval = setInterval(loadAllData, 15000);
    }
    
    // Подключаем кнопку техработ
    const techBtn = document.getElementById('techBtn');
    if (techBtn) {
        techBtn.addEventListener('click', toggleTech);
    }
});

console.log('✅ RCON Admin v14.0 загружен');
console.log(`📁 DATA_PATH: ${DATA_PATH}`);
console.log('📂 Файлы: logs.json, idlist.json, banlist.json, tech.json');
