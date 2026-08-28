package com.ripmax.app

import android.content.Intent
import android.os.Bundle
import android.widget.*
import androidx.appcompat.app.AppCompatActivity

class MainActivity : AppCompatActivity() {
    private lateinit var etKey: EditText
    private lateinit var btnConnect: Button
    private lateinit var tvStatus: TextView
    
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)
        
        etKey = findViewById(R.id.etKey)
        btnConnect = findViewById(R.id.btnConnect)
        tvStatus = findViewById(R.id.tvStatus)
        
        val savedKey = getSharedPreferences("app_prefs", MODE_PRIVATE)
            .getString("app_key", null)
        
        if (!savedKey.isNullOrEmpty()) {
            navigateToConsole(savedKey)
            return
        }
        
        btnConnect.setOnClickListener {
            val key = etKey.text.toString().trim()
            if (key.isNotEmpty()) {
                tvStatus.text = "⏳ Проверка ключа..."
                tvStatus.setTextColor(resources.getColor(android.R.color.holo_orange_dark))
                checkKey(key)
            } else {
                Toast.makeText(this, "Введите ключ доступа", Toast.LENGTH_SHORT).show()
            }
        }
    }
    
    private fun checkKey(key: String) {
        // Простая проверка (можно расширить)
        if (key.startsWith("ADMIN_") && key.length > 10) {
            tvStatus.text = "✅ Ключ принят!"
            tvStatus.setTextColor(resources.getColor(android.R.color.holo_green_dark))
            saveKey(key)
            navigateToConsole(key)
        } else {
            tvStatus.text = "❌ Неверный ключ!"
            tvStatus.setTextColor(resources.getColor(android.R.color.holo_red_dark))
            Toast.makeText(this, "Неверный ключ доступа", Toast.LENGTH_SHORT).show()
        }
    }
    
    private fun saveKey(key: String) {
        getSharedPreferences("app_prefs", MODE_PRIVATE)
            .edit()
            .putString("app_key", key)
            .apply()
    }
    
    private fun navigateToConsole(key: String) {
        val intent = Intent(this, ConsoleActivity::class.java)
        intent.putExtra("app_key", key)
        startActivity(intent)
        finish()
    }
}
