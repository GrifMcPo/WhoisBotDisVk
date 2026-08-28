package com.ripmax.app

import android.os.Bundle
import android.view.View
import android.widget.*
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import androidx.recyclerview.widget.LinearLayoutManager
import androidx.recyclerview.widget.RecyclerView
import androidx.swiperefreshlayout.widget.SwipeRefreshLayout
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

class ConsoleActivity : AppCompatActivity() {
    private lateinit var rvLogs: RecyclerView
    private lateinit var etCommand: EditText
    private lateinit var btnSend: Button
    private lateinit var swipeRefresh: SwipeRefreshLayout
    private lateinit var tvStatus: TextView
    private lateinit var btnClear: Button
    private var appKey: String = ""
    
    private val logItems = mutableListOf<LogItem>()
    
    data class LogItem(val text: String, val color: Int = android.R.color.white)
    
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_console)
        
        appKey = intent.getStringExtra("app_key") ?: ""
        
        rvLogs = findViewById(R.id.rvLogs)
        etCommand = findViewById(R.id.etCommand)
        btnSend = findViewById(R.id.btnSend)
        swipeRefresh = findViewById(R.id.swipeRefresh)
        tvStatus = findViewById(R.id.tvStatus)
        btnClear = findViewById(R.id.btnClear)
        
        setupLogs()
        setupQuickCommands()
        
        tvStatus.text = "✅ Подключено"
        tvStatus.setTextColor(resources.getColor(android.R.color.holo_green_dark))
        
        addLog("━━━ RIPSAVE CONSOLE ━━━", android.R.color.holo_blue_light)
        addLog("🔑 Ключ: ${appKey.take(10)}...", android.R.color.white)
        addLog("📡 Ожидание команд...", android.R.color.white)
        addLog("", android.R.color.white)
        
        btnSend.setOnClickListener {
            val text = etCommand.text.toString().trim()
            if (text.isNotEmpty()) {
                executeCommand(text)
                etCommand.text?.clear()
            }
        }
        
        swipeRefresh.setOnRefreshListener {
            loadData()
        }
        
        btnClear.setOnClickListener {
            logItems.clear()
            setupLogs()
            addLog("━━━ ЛОГИ ОЧИЩЕНЫ ━━━", android.R.color.holo_orange_dark)
        }
    }
    
    private fun setupLogs() {
        val adapter = object : RecyclerView.Adapter<RecyclerView.ViewHolder>() {
            override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): RecyclerView.ViewHolder {
                val tv = TextView(parent.context).apply {
                    setTextColor(resources.getColor(android.R.color.white))
                    textSize = 13f
                    setPadding(16, 8, 16, 8)
                }
                return object : RecyclerView.ViewHolder(tv) {}
            }
            
            override fun onBindViewHolder(holder: RecyclerView.ViewHolder, position: Int) {
                val item = logItems[position]
                (holder.itemView as TextView).apply {
                    text = item.text
                    setTextColor(resources.getColor(item.color))
                }
            }
            
            override fun getItemCount() = logItems.size
        }
        
        rvLogs.layoutManager = LinearLayoutManager(this)
        rvLogs.adapter = adapter
    }
    
    private fun addLog(text: String, color: Int = android.R.color.white) {
        logItems.add(LogItem(text, color))
        rvLogs.adapter?.notifyItemInserted(logItems.size - 1)
        rvLogs.scrollToPosition(logItems.size - 1)
    }
    
    private fun setupQuickCommands() {
        val commands = listOf(
            ".help", ".idlist", ".key", ".tex on", ".tex off"
        )
        val llQuick = findViewById<LinearLayout>(R.id.llQuick)
        
        commands.forEach { cmd ->
            val btn = Button(this).apply {
                text = cmd
                textSize = 11f
                setPadding(20, 10, 20, 10)
                setTextColor(resources.getColor(android.R.color.white))
                setBackgroundColor(resources.getColor(android.R.color.darker_gray))
                layoutParams = LinearLayout.LayoutParams(
                    LinearLayout.LayoutParams.WRAP_CONTENT,
                    LinearLayout.LayoutParams.WRAP_CONTENT
                ).apply { marginEnd = 8 }
                setOnClickListener {
                    etCommand.setText(cmd)
                    executeCommand(cmd)
                }
            }
            llQuick.addView(btn)
        }
    }
    
    private fun executeCommand(command: String) {
        addLog("> $command", android.R.color.holo_green_light)
        
        lifecycleScope.launch {
            try {
                val result = withContext(Dispatchers.IO) {
                    sendCommandToBot(command)
                }
                addLog(result, android.R.color.white)
                addLog("", android.R.color.white)
            } catch (e: Exception) {
                addLog("❌ Ошибка: ${e.message}", android.R.color.holo_red_dark)
            }
        }
    }
    
    private suspend fun sendCommandToBot(command: String): String {
        // Здесь отправка команды в Supabase и получение ответа
        delay(500)
        return when {
            command == ".help" -> """
                📚 ДОСТУПНЫЕ КОМАНДЫ
                
                .ban [ID] [время] [причина] — Бан
                .unban [ID] — Разбан
                .idlist — Список пользователей
                .logs [ID] — Логи
                .key — Создать ключ
                .tex on/off — Техработы
                .stop [run/bot/max] max — Остановить раннеры
            """.trimIndent()
            command == ".idlist" -> "👥 СПИСОК ПОЛЬЗОВАТЕЛЕЙ\n\n🆔 123456 → @user1\n🆔 789012 → @user2"
            command.startsWith(".ban") -> "✅ Пользователь забанен"
            command.startsWith(".unban") -> "✅ Пользователь разбанен"
            command == ".key" -> "🔑 Ключ: ADMIN_ABCDE"
            command == ".tex on" -> "✅ Техработы включены"
            command == ".tex off" -> "✅ Техработы выключены"
            else -> "❌ Неизвестная команда: $command"
        }
    }
    
    private fun loadData() {
        lifecycleScope.launch {
            delay(1000)
            swipeRefresh.isRefreshing = false
            addLog("🔄 Данные обновлены", android.R.color.holo_blue_light)
        }
    }
}
