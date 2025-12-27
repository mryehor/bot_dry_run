# Создайте test_telegram.py
import sys
sys.path.append('.')
from telegram_bot import check_telegram_config, send_telegram_message

if __name__ == "__main__":
    print("🔧 Тестирование Telegram...")
    
    if check_telegram_config():
        print("✅ Конфигурация OK")
        
        # Пробуем отправить тестовое сообщение
        success = send_telegram_message(
            "Тестовое сообщение от бота",
            with_keyboard=False
        )
        
        if success:
            print("✅ Тестовое сообщение отправлено")
        else:
            print("❌ Не удалось отправить тестовое сообщение")
    else:
        print("❌ Конфигурация неверная")