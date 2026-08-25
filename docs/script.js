// Симуляция API
class AdminAPI {
    static async validateKey(key) {
        if (key && key.startsWith('ADMIN_') && key.length === 10) {
            return { valid: true, expires: 'через 10 часов' };
        }
        return { valid: false };
    }
    
    static async getLogs() {
        return {
            data: [
                { time: '2024-01-15 12:30:45', user: 'user1', command: '/whois' },
                { time: '2024-01-15 12:31:20', user: 'user2', command: '.help' },
                { time: '2024-01-15 12:32:10', user: 'user3', command: '.whois ip 8.8.8.8' }
            ]
        };
    }
    
    static async getUsers() {
        return {
            data: [
                { id: 123456789, username: 'user1' },
                { id: 987654321, username: 'user2' },
                { id: 456789123, username: 'user3' }
            ]
        };
    }
    
    static async getBans() {
        return {
            data: [
                { id: 123456789, reason: 'Спам', until: '2024-01-20 15:00:00' },
                { id: 456789123, reason: 'Нарушение', until: 'Навсегда' }
            ]
        };
    }
}

document.addEventListener('DOMContentLoaded', function() {
    const keyInput = document.getElementById('keyInput');
    const keySubmit = document.getElementById('keySubmitBtn');
    const keyStatus = document.getElementById('keyStatus');
    const loginBox = document.getElementById('loginBox');
    const adminPanel = document.getElementById('adminPanel');
    
    keySubmit.addEventListener('click', async function() {
        const key = keyInput.value.trim();
        if (!key) {
            keyStatus.textContent = '❌ Введите ключ';
            keyStatus.className = 'status-message error';
            return;
        }
        
        keySubmit.textContent = '⏳ Проверка...';
        keySubmit.disabled = true;
        
        try {
            const result = await AdminAPI.validateKey(key);
            if (result.valid) {
                keyStatus.textContent = '✅ Ключ верный! Доступ разрешен';
                keyStatus.className = 'status-message success';
                setTimeout(() => {
                    loginBox.style.display = 'none';
                    adminPanel.style.display = 'block';
                }, 500);
            } else {
                keyStatus.textContent = '❌ Неверный ключ или сессия истекла';
                keyStatus.className = 'status-message error';
            }
        } catch (error) {
            keyStatus.textContent = '❌ Ошибка проверки ключа';
            keyStatus.className = 'status-message error';
        }
        
        keySubmit.textContent = 'Проверить';
        keySubmit.disabled = false;
    });
    
    keyInput.addEventListener('keypress', function(e) {
        if (e.key === 'Enter') keySubmit.click();
    });
    
    window.showLogs = async function() {
        const content = document.getElementById('contentArea');
        content.innerHTML = '<p style="text-align:center;color:rgba(255,255,255,0.4);">⏳ Загрузка...</p>';
        try {
            const result = await AdminAPI.getLogs();
            let html = '<h3>📊 Логи команд</h3>';
            result.data.forEach(log => {
                html += `
                    <div style="padding:8px 12px;margin-bottom:6px;background:rgba(255,255,255,0.03);border-radius:8px;border-left:3px solid #4285f4;">
                        <span style="color:rgba(255,255,255,0.4);font-size:12px;">🕐 ${log.time}</span>
                        <span style="color:#34a853;font-weight:500;">👤 ${log.user}</span>
                        <div style="margin-top:4px;font-family:monospace;color:rgba(255,255,255,0.7);">📝 ${log.command}</div>
                    </div>
                `;
            });
            content.innerHTML = html;
        } catch (error) {
            content.innerHTML = '<p style="color:#ea4335;text-align:center;">❌ Ошибка загрузки</p>';
        }
    };
    
    window.showUsers = async function() {
        const content = document.getElementById('contentArea');
        content.innerHTML = '<p style="text-align:center;color:rgba(255,255,255,0.4);">⏳ Загрузка...</p>';
        try {
            const result = await AdminAPI.getUsers();
            let html = '<h3>👥 Список пользователей</h3><pre>';
            result.data.forEach(user => {
                html += `🆔 ${user.id}\n👤 @${user.username}\n\n`;
            });
            html += '</pre>';
            content.innerHTML = html;
        } catch (error) {
            content.innerHTML = '<p style="color:#ea4335;text-align:center;">❌ Ошибка загрузки</p>';
        }
    };
    
    window.showBans = async function() {
        const content = document.getElementById('contentArea');
        content.innerHTML = '<p style="text-align:center;color:rgba(255,255,255,0.4);">⏳ Загрузка...</p>';
        try {
            const result = await AdminAPI.getBans();
            let html = '<h3>⛔ Забаненные пользователи</h3><pre>';
            if (result.data.length === 0) {
                html += '📭 Нет забаненных пользователей';
            } else {
                result.data.forEach(ban => {
                    html += `🆔 ${ban.id}\n📌 ${ban.reason}\n⏳ ${ban.until}\n\n`;
                });
            }
            html += '</pre>';
            content.innerHTML = html;
        } catch (error) {
            content.innerHTML = '<p style="color:#ea4335;text-align:center;">❌ Ошибка загрузки</p>';
        }
    };
    
    window.showSettings = function() {
        const content = document.getElementById('contentArea');
        content.innerHTML = `
            <h3>⚙️ Настройки бота</h3>
            <div style="margin-top:20px;">
                <p style="margin-bottom:15px;color:rgba(255,255,255,0.7);">Технические работы:</p>
                <div class="settings-row">
                    <button class="btn-danger" onclick="toggleMaintenance('on')">🔧 Включить</button>
                    <button class="btn-success" onclick="toggleMaintenance('off')">✅ Выключить</button>
                </div>
                <div class="settings-status" id="maintenanceStatus">
                    <span style="color:rgba(255,255,255,0.5);">Статус: технические работы отключены</span>
                </div>
            </div>
        `;
    };
    
    window.toggleMaintenance = function(action) {
        const statusDiv = document.getElementById('maintenanceStatus');
        if (action === 'on') {
            statusDiv.innerHTML = `
                <span style="color:#ea4335;">🔴 ТЕХНИЧЕСКИЕ РАБОТЫ ВКЛЮЧЕНЫ</span>
                <div style="margin-top:8px;color:rgba(255,255,255,0.4);font-size:13px;">
                    Время: ${new Date().toLocaleString('ru-RU')}
                </div>
            `;
        } else {
            statusDiv.innerHTML = `
                <span style="color:#34a853;">🟢 ТЕХНИЧЕСКИЕ РАБОТЫ ВЫКЛЮЧЕНЫ</span>
            `;
        }
    };
});

// RGB анимация фона
let hue = 0;
setInterval(() => {
    hue = (hue + 0.5) % 360;
    document.body.style.background = `linear-gradient(135deg, 
        hsl(${hue}, 80%, 8%), 
        hsl(${hue + 40}, 80%, 15%)
    )`;
}, 100);
