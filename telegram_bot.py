"""
Telegram менеджер для биржевого бота
Красивые сообщения в канал + полное управление через личного бота
"""

import threading
import time
import requests
import json
from datetime import datetime
from typing import Dict, List, Optional
import logging

# Импорт конфигурации
try:
    from config import (
        TELEGRAM_BOT_TOKEN,
        TELEGRAM_MY_CHAT_ID,    # Ваш личный ID для управления
        TELEGRAM_CHANNEL_ID,    # Канал для красивых уведомлений
        SEND_TO_CHANNEL,        # Отправлять ли в канал
        SEND_TO_ME,             # Отправлять ли вам лично
        TRADING_MODE,
        TIMEFRAME,
        LEVERAGE,
        RISK_FRACTION,
        INITIAL_CASH
    )
    # Импортируем Binance клиента для получения реальных данных
    from binance_client import binance_client
except ImportError as e:
    print(f"Warning: Could not import config: {e}")
    TELEGRAM_BOT_TOKEN = ""
    TELEGRAM_MY_CHAT_ID = ""
    TELEGRAM_CHANNEL_ID = ""
    SEND_TO_CHANNEL = True
    SEND_TO_ME = True
    TRADING_MODE = "test"
    binance_client = None

log = logging.getLogger(__name__)

# ========== ГЛОБАЛЬНЫЕ СОСТОЯНИЯ ==========
trading_paused = False
bot_active = True
auto_trading = True
emergency_stop = False

# ========== КЛАСС ДЛЯ УПРАВЛЕНИЯ ==========
class TradingControl:
    """Класс для управления торговлей через Telegram"""
    
    def __init__(self):
        self.start_time = time.time()
        self.authorized_users = set()
        
        # Добавляем вас как авторизованного пользователя
        if TELEGRAM_MY_CHAT_ID:
            self.authorized_users.add(str(TELEGRAM_MY_CHAT_ID))
            print(f"✅ Авторизован пользователь: {TELEGRAM_MY_CHAT_ID}")
    
    def is_authorized(self, chat_id: str) -> bool:
        """Проверка авторизации пользователя"""
        return str(chat_id) in self.authorized_users

# Создаем экземпляр управления
control = TradingControl()

# ========== ФУНКЦИИ ДЛЯ ПОЛУЧЕНИЯ РЕАЛЬНЫХ ДАННЫХ ==========
def get_real_balance() -> float:
    """Получение реального баланса с Binance"""
    if TRADING_MODE != 'real' or not binance_client:
        return INITIAL_CASH
    
    try:
        # Пробуем получить реальный баланс
        balance = binance_client.get_balance('USDT')
        return balance
    except Exception as e:
        print(f"❌ Не удалось получить реальный баланс: {e}")
        return INITIAL_CASH

def get_real_positions() -> List[Dict]:
    """Получение реальных позиций с Binance"""
    if TRADING_MODE != 'real' or not binance_client:
        return []
    
    try:
        positions = binance_client.get_positions()
        return positions
    except Exception as e:
        print(f"❌ Не удалось получить реальные позиции: {e}")
        return []

def get_real_pnl() -> Dict:
    """Получение реального PnL"""
    if TRADING_MODE != 'real':
        return {
            'realized': 0,
            'unrealized': 0,
            'total': 0,
            'balance': INITIAL_CASH
        }
    
    try:
        # Используем реальные данные если доступны
        from pnl_utils import get_total_pnl
        pnl_data = get_total_pnl()
        
        # Получаем реальный баланс
        real_balance = get_real_balance()
        pnl_data['balance'] = real_balance
        
        return pnl_data
    except Exception as e:
        print(f"❌ Не удалось получить реальный PnL: {e}")
        return {
            'realized': 0,
            'unrealized': 0,
            'total': 0,
            'balance': INITIAL_CASH
        }

# ========== КРАСИВЫЕ СООБЩЕНИЯ ДЛЯ КАНАЛА ==========
def create_channel_message(message_type: str, **kwargs) -> str:
    """Создание красивых сообщений для канала с реальными данными"""
    
    # Получаем актуальные данные
    current_balance = get_real_balance()
    positions = get_real_positions()
    open_positions_count = len(positions)
    
    if message_type == "startup":
        if TRADING_MODE == 'real':
            return f"""
🎯 <b>ТОРГОВЫЙ БОТ АКТИВИРОВАН</b>
━━━━━━━━━━━━━━━━━━━━
📊 <b>Режим:</b> <code>РЕАЛЬНАЯ ТОРГОВЛЯ</code> 🚨
⚡ <b>Статус:</b> <code>АКТИВЕН</code> ✅

📈 <b>Настройки стратегии:</b>
• Таймфрейм: <code>{TIMEFRAME}</code>
• Плечо: <code>{LEVERAGE}x</code>
• Риск на сделку: <code>{RISK_FRACTION*100}%</code>
• Текущий баланс: <code>{current_balance:.2f} USDT</code>

⏰ <b>Запуск:</b> <code>{datetime.now().strftime('%H:%M:%S')}</code>
📅 <b>Дата:</b> <code>{datetime.now().strftime('%d.%m.%Y')}</code>

⚠️ <i>Бот работает с реальными средствами.
Все сделки будут публиковаться здесь.</i>
"""
        else:
            return f"""
🧪 <b>ТОРГОВЫЙ БОТ АКТИВИРОВАН</b>
━━━━━━━━━━━━━━━━━━━━
📊 <b>Режим:</b> <code>ТЕСТОВАЯ ТОРГОВЛЯ</code>
⚡ <b>Статус:</b> <code>АКТИВЕН</code> ✅

📈 <b>Настройки стратегии:</b>
• Таймфрейм: <code>{TIMEFRAME}</code>
• Плечо: <code>{LEVERAGE}x</code>
• Риск на сделку: <code>{RISK_FRACTION*100}%</code>
• Капитал: <code>{INITIAL_CASH} USDT</code>

⏰ <b>Запуск:</b> <code>{datetime.now().strftime('%H:%M:%S')}</code>
📅 <b>Дата:</b> <code>{datetime.now().strftime('%d.%m.%Y')}</code>

📊 <i>Тестовый режим - без риска для средств.
Идеально для тестирования стратегий.</i>
"""
    
    elif message_type == "signal":
        symbol = kwargs.get('symbol', 'Unknown')
        side = kwargs.get('side', 'Unknown')
        price = kwargs.get('price', 0)
        
        if side == "BUY":
            return f"""
📈 <b>ТОРГОВЫЙ СИГНАЛ ОБНАРУЖЕН</b>
━━━━━━━━━━━━━━━━━━━━
🎯 <b>Символ:</b> <code>{symbol}</code>
📊 <b>Сигнал:</b> <code>ПОКУПКА</code> 🟢
💰 <b>Цена:</b> <code>{price:.4f}</code>
💵 <b>Доступно:</b> <code>{current_balance:.2f} USDT</code>

🎲 <b>Вероятность:</b> <code>Высокая</code> 🔥
⏰ <b>Время:</b> <code>{datetime.now().strftime('%H:%M:%S')}</code>

⚡ <i>Сигнал основан на анализе индикаторов
и рыночных условий. Готовимся к входу.</i>
"""
        else:  # SELL
            return f"""
📉 <b>ТОРГОВЫЙ СИГНАЛ ОБНАРУЖЕН</b>
━━━━━━━━━━━━━━━━━━━━
🎯 <b>Символ:</b> <code>{symbol}</code>
📊 <b>Сигнал:</b> <code>ПРОДАЖА</code> 🔴
💰 <b>Цена:</b> <code>{price:.4f}</code>
💵 <b>Доступно:</b> <code>{current_balance:.2f} USDT</code>

🎲 <b>Вероятность:</b> <code>Высокая</code> 🔥
⏰ <b>Время:</b> <code>{datetime.now().strftime('%H:%M:%S')}</code>

⚡ <i>Сигнал основан на анализе индикаторов
и рыночных условий. Готовимся к выходу.</i>
"""
    
    elif message_type == "trade_open":
        symbol = kwargs.get('symbol', 'Unknown')
        side = kwargs.get('side', 'Unknown')
        price = kwargs.get('price', 0)
        quantity = kwargs.get('quantity', 0)
        notional = price * quantity
        
        # Получаем актуальный баланс после открытия сделки
        current_balance_after = get_real_balance()
        
        if side == "BUY":
            return f"""
🚀 <b>СДЕЛКА ОТКРЫТА: ПОКУПКА</b>
━━━━━━━━━━━━━━━━━━━━
🎯 <b>Символ:</b> <code>{symbol}</code>
📊 <b>Направление:</b> <code>LONG</code> 🟢
💰 <b>Цена входа:</b> <code>{price:.4f}</code>
📦 <b>Количество:</b> <code>{quantity:.4f}</code>
💵 <b>Номинал:</b> <code>{notional:.2f} USDT</code>
🏦 <b>Баланс после:</b> <code>{current_balance_after:.2f} USDT</code>

⚡ <b>Плечо:</b> <code>{LEVERAGE}x</code>
🎯 <b>Риск:</b> <code>{RISK_FRACTION*100}%</code>
⏰ <b>Время:</b> <code>{datetime.now().strftime('%H:%M:%S')}</code>

✅ <i>Позиция успешно открыта.
Ожидаем движение цены в нашу сторону.</i>
"""
        else:  # SELL
            return f"""
🚀 <b>СДЕЛКА ОТКРЫТА: ПРОДАЖА</b>
━━━━━━━━━━━━━━━━━━━━
🎯 <b>Символ:</b> <code>{symbol}</code>
📊 <b>Направление:</b> <code>SHORT</code> 🔴
💰 <b>Цена входа:</b> <code>{price:.4f}</code>
📦 <b>Количество:</b> <code>{quantity:.4f}</code>
💵 <b>Номинал:</b> <code>{notional:.2f} USDT</code>
🏦 <b>Баланс после:</b> <code>{current_balance_after:.2f} USDT</code>

⚡ <b>Плечо:</b> <code>{LEVERAGE}x</code>
🎯 <b>Риск:</b> <code>{RISK_FRACTION*100}%</code>
⏰ <b>Время:</b> <code>{datetime.now().strftime('%H:%M:%S')}</code>

✅ <i>Позиция успешно открыта.
Ожидаем движение цены в нашу сторону.</i>
"""
    
    elif message_type == "trade_close":
        symbol = kwargs.get('symbol', 'Unknown')
        side = kwargs.get('side', 'Unknown')
        entry_price = kwargs.get('entry_price', 0)
        exit_price = kwargs.get('exit_price', 0)
        quantity = kwargs.get('quantity', 0)
        pnl = kwargs.get('pnl', 0)
        reason = kwargs.get('reason', 'Не указана')
        
        pnl_percent = (pnl / (entry_price * quantity)) * 100 if entry_price * quantity > 0 else 0
        
        # Получаем актуальный баланс после закрытия
        current_balance_after = get_real_balance()
        
        if pnl > 0:
            pnl_emoji = "💰"
            pnl_text = "ПРИБЫЛЬ"
        else:
            pnl_emoji = "📉"
            pnl_text = "УБЫТОК"
        
        return f"""
🔒 <b>СДЕЛКА ЗАКРЫТА</b>
━━━━━━━━━━━━━━━━━━━━
🎯 <b>Символ:</b> <code>{symbol}</code>
📊 <b>Направление:</b> <code>{'LONG' if side == 'BUY' else 'SHORT'}</code>
💰 <b>Вход:</b> <code>{entry_price:.4f}</code>
🎯 <b>Выход:</b> <code>{exit_price:.4f}</code>
📦 <b>Количество:</b> <code>{quantity:.4f}</code>
🏦 <b>Баланс после:</b> <code>{current_balance_after:.2f} USDT</code>

{pnl_emoji} <b>Результат:</b> <code>{pnl:+.2f} USDT</code>
📈 <b>Процент:</b> <code>{pnl_percent:+.2f}%</code>
📝 <b>Причина:</b> <code>{reason}</code>
⏰ <b>Время:</b> <code>{datetime.now().strftime('%H:%M:%S')}</code>

💡 <i>Сделка завершена. {pnl_text} фиксируется.
Анализируем результат и готовимся к следующим сделкам.</i>
"""
    
    elif message_type == "error":
        error = kwargs.get('error', 'Неизвестная ошибка')
        return f"""
❌ <b>ОШИБКА СИСТЕМЫ</b>
━━━━━━━━━━━━━━━━━━━━
⚠️ <b>Тип:</b> <code>Критическая ошибка</code>
📝 <b>Описание:</b> <code>{error[:100]}...</code>
⏰ <b>Время:</b> <code>{datetime.now().strftime('%H:%M:%S')}</code>

🔧 <i>Система пытается восстановить работу.
Техническая команда уведомлена.</i>
"""
    
    elif message_type == "status_update":
        # Получаем реальные данные PnL
        pnl_data = get_real_pnl()
        
        return f"""
📊 <b>СТАТУС БОТА</b>
━━━━━━━━━━━━━━━━━━━━
💰 <b>Баланс:</b> <code>{pnl_data['balance']:.2f} USDT</code>
📈 <b>Позиций:</b> <code>{open_positions_count}</code>
💵 <b>Реализованный PnL:</b> <code>{pnl_data['realized']:+.2f} USDT</code>
📉 <b>Незакрытый PnL:</b> <code>{pnl_data['unrealized']:+.2f} USDT</code>
📊 <b>Общий PnL:</b> <code>{pnl_data['total']:+.2f} USDT</code>

📊 <b>Режим:</b> <code>{TRADING_MODE.upper()}</code>
⚡ <b>Торговля:</b> <code>{'АКТИВНА' if not trading_paused else 'НА ПАУЗЕ'}</code>
🤖 <b>Автоторговля:</b> <code>{'ВКЛ' if auto_trading else 'ВЫКЛ'}</code>

⏰ <b>Время отчета:</b> <code>{datetime.now().strftime('%H:%M:%S')}</code>
📅 <b>Дата:</b> <code>{datetime.now().strftime('%d.%m.%Y')}</code>

📈 <i>Регулярный отчет о состоянии торгового бота.
Все показатели в реальном времени.</i>
"""
    
    else:
        return f"📢 {kwargs.get('text', 'Уведомление')}"

# ========== ФУНКЦИИ ОТПРАВКИ ==========
def send_to_channel(message: str, parse_mode: str = 'HTML') -> bool:
    """Отправить красивое сообщение в канал"""
    if not SEND_TO_CHANNEL or not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHANNEL_ID:
        return False
    
    # Для тестирования упрощаем HTML
    if parse_mode == 'HTML':
        # Заменяем HTML теги на безопасные
        message = message.replace('<b>', '*').replace('</b>', '*')
        message = message.replace('<code>', '`').replace('</code>', '`')
        message = message.replace('<i>', '_').replace('</i>', '_')
        parse_mode = 'Markdown'
    
    return _send_message(TELEGRAM_CHANNEL_ID, message, parse_mode)

def send_to_me(message: str, parse_mode: str = 'Markdown') -> bool:
    """Отправить сообщение вам лично (для управления)"""
    if not SEND_TO_ME or not TELEGRAM_BOT_TOKEN or not TELEGRAM_MY_CHAT_ID:
        return False
    
    return _send_message(TELEGRAM_MY_CHAT_ID, message, parse_mode)

def _send_message(chat_id: str, text: str, parse_mode: str = 'Markdown') -> bool:
    """Базовая функция отправки сообщения"""
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        data = {
            "chat_id": chat_id,
            "text": text[:4096],
            "parse_mode": parse_mode,
            "disable_notification": False,
            "disable_web_page_preview": True
        }
        
        # Отладка
        print(f"📤 Отправка в Telegram (chat_id: {chat_id}, символов: {len(text)})")
        
        response = requests.post(url, json=data, timeout=10)
        
        if response.status_code == 200:
            destination = "канал" if "@" in str(chat_id) or "-100" in str(chat_id) else "вам"
            print(f"✅ Сообщение отправлено в {destination}")
            return True
        else:
            print(f"❌ Ошибка отправки: {response.status_code}")
            print(f"   Ответ Telegram: {response.text[:200]}")
            
            # Если ошибка из-за parse_mode, пробуем без него
            if "parse mode" in response.text.lower():
                print("⚠️  Пробуем отправить без форматирования...")
                data.pop("parse_mode", None)
                response2 = requests.post(url, json=data, timeout=10)
                if response2.status_code == 200:
                    print("✅ Сообщение отправлено без форматирования")
                    return True
                else:
                    print(f"❌ Снова ошибка: {response2.status_code}")
                    print(f"   Ответ: {response2.text[:200]}")
            
            return False
            
    except Exception as e:
        print(f"❌ Ошибка отправки: {e}")
        return False

# ========== ИНТЕГРАЦИОННЫЕ ФУНКЦИИ ==========
def send_startup_message(custom_message=None):
    """Отправка красивого сообщения о запуске"""
    if custom_message:
        # Если передано кастомное сообщение, отправляем его
        if SEND_TO_CHANNEL and TELEGRAM_CHANNEL_ID:
            send_to_channel(custom_message)
        if SEND_TO_ME and TELEGRAM_MY_CHAT_ID:
            send_to_me(custom_message)
        return
    
    # Получаем реальный баланс для сообщения
    current_balance = get_real_balance()
    
    # В канал - красивое сообщение
    channel_msg = create_channel_message("startup")
    send_to_channel(channel_msg)
    
    # Вам лично - информация для управления с реальными данными
    personal_msg = f"""
🤖 *ТОРГОВЫЙ БОТ ЗАПУЩЕН*

📊 *Режим:* {TRADING_MODE.upper()}
⏰ *Таймфрейм:* {TIMEFRAME}
⚖️  *Плечо:* {LEVERAGE}x
🎯 *Риск на сделку:* {RISK_FRACTION*100}%
💰 *Текущий баланс:* {current_balance:.2f} USDT

🕐 *Время запуска:* {datetime.now().strftime('%H:%M:%S')}

*Команды управления:*
/status - Статус бота
/pause - Пауза торговли  
/resume - Возобновить
/auto_on - Автоторговля ВКЛ
/auto_off - Автоторговля ВЫКЛ
/help - Все команды

⚠️ *Сообщения о сделках будут публиковаться в канале*
"""
    send_to_me(personal_msg)

def send_signal_alert(symbol: str, side: str, price: float):
    """Отправка алерта о сигнале"""
    # Получаем реальный баланс
    current_balance = get_real_balance()
    
    # В канал - красивое сообщение
    channel_msg = create_channel_message("signal", 
                                       symbol=symbol, 
                                       side=side, 
                                       price=price)
    send_to_channel(channel_msg)
    
    # Вам лично - техническая информация с реальным балансом
    personal_msg = f"""
📈 *СИГНАЛ ОБНАРУЖЕН*

• Символ: {symbol}
• Сигнал: {side}
• Цена: {price:.4f}
• Баланс: {current_balance:.2f} USDT
• Время: {datetime.now().strftime('%H:%M:%S')}

*Статус торговли:*
• Автоторговля: {'✅ ВКЛ' if auto_trading else '⏸ ВЫКЛ'}
• Пауза: {'⏸ ДА' if trading_paused else '✅ НЕТ'}
• Режим: {TRADING_MODE.upper()}
"""
    send_to_me(personal_msg)

def send_trade_opened(symbol: str, side: str, price: float, quantity: float):
    """Отправка алерта об открытии сделки"""
    notional = price * quantity
    current_balance = get_real_balance()
    
    # В канал - красивое сообщение
    channel_msg = create_channel_message("trade_open",
                                       symbol=symbol,
                                       side=side,
                                       price=price,
                                       quantity=quantity)
    send_to_channel(channel_msg)
    
    # Вам лично - детали
    personal_msg = f"""
{'🚨' if TRADING_MODE == 'real' else '💰'} *СДЕЛКА ОТКРЫТА*

• Символ: {symbol}
• Сторона: {side}
• Цена: {price:.4f}
• Количество: {quantity:.4f}
• Номинал: {notional:.2f} USDT
• Баланс: {current_balance:.2f} USDT
• Время: {datetime.now().strftime('%H:%M:%S')}

📊 *Расчеты:*
• Рик на сделку: {notional/current_balance*100:.1f}% (от баланса)
• Плечо: {LEVERAGE}x
• Режим: {TRADING_MODE.upper()}
"""
    send_to_me(personal_msg)

def send_trade_closed(symbol: str, side: str, entry_price: float, 
                     exit_price: float, quantity: float, reason: str):
    """Отправка алерта о закрытии сделки"""
    if side == "BUY":
        pnl = (exit_price - entry_price) * quantity
    else:
        pnl = (entry_price - exit_price) * quantity
    
    current_balance = get_real_balance()
    
    # В канал - красивое сообщение
    channel_msg = create_channel_message("trade_close",
                                       symbol=symbol,
                                       side=side,
                                       entry_price=entry_price,
                                       exit_price=exit_price,
                                       quantity=quantity,
                                       pnl=pnl,
                                       reason=reason)
    send_to_channel(channel_msg)
    
    # Вам лично - детали
    pnl_percent = (pnl / (entry_price * quantity)) * 100 if entry_price * quantity > 0 else 0
    
    personal_msg = f"""
🔒 *СДЕЛКА ЗАКРЫТА*

• Символ: {symbol}
• Сторона: {side}
• Вход: {entry_price:.4f}
• Выход: {exit_price:.4f}
• Количество: {quantity:.4f}
• Баланс: {current_balance:.2f} USDT
• PnL: {pnl:+.2f} USDT
• Процент: {pnl_percent:+.2f}%
• Причина: {reason}
• Время: {datetime.now().strftime('%H:%M:%S')}
"""
    send_to_me(personal_msg)

def send_status_update():
    """Отправка периодического отчета"""
    # Получаем реальные данные
    pnl_data = get_real_pnl()
    positions = get_real_positions()
    
    # В канал - красивый отчет
    channel_msg = create_channel_message("status_update")
    send_to_channel(channel_msg)
    
    # Вам лично - детальный отчет
    personal_msg = f"""
📊 *ОТЧЕТ О РАБОТЕ*

💰 *Баланс:* {pnl_data['balance']:.2f} USDT
📈 *Открытых позиций:* {len(positions)}
💵 *Реализованный PnL:* {pnl_data['realized']:+.2f} USDT
📉 *Незакрытый PnL:* {pnl_data['unrealized']:+.2f} USDT
📊 *Общий PnL:* {pnl_data['total']:+.2f} USDT
📊 *Режим:* {TRADING_MODE.upper()}

⚡ *Статус торговли:*
• Активна: {'✅ ДА' if not trading_paused else '⏸ НЕТ'}
• Автоторговля: {'🤖 ВКЛ' if auto_trading else '👤 ВЫКЛ'}
• Аварийная остановка: {'🚨 АКТИВНА' if emergency_stop else '✅ НЕТ'}

⏰ *Время отчета:* {datetime.now().strftime('%H:%M:%S')}
"""
    send_to_me(personal_msg)

def send_error(error: str):
    """Отправка ошибки"""
    current_balance = get_real_balance()
    
    # В канал - красивое сообщение об ошибке
    channel_msg = create_channel_message("error", error=error)
    send_to_channel(channel_msg)
    
    # Вам лично - технические детали
    personal_msg = f"""
❌ *КРИТИЧЕСКАЯ ОШИБКА*

{error}

• Баланс: {current_balance:.2f} USDT
• Время: {datetime.now().strftime('%H:%M:%S')}
• Режим: {TRADING_MODE.upper()}
"""
    send_to_me(personal_msg)

# ========== ПРОСЛУШИВАНИЕ КОМАНД ==========
def listen_commands():
    """Прослушивание команд только от вас"""
    print("🎮 Telegram управление запущено (только для вас)")
    
    offset = None
    
    while True:
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
            params = {"timeout": 30}
            if offset:
                params["offset"] = offset
            
            response = requests.get(url, params=params, timeout=35)
            
            if response.status_code == 200:
                data = response.json()
                if data.get("ok"):
                    updates = data.get("result", [])
                    
                    for update in updates:
                        offset = update["update_id"] + 1
                        
                        if "message" in update:
                            message = update["message"]
                            chat_id = str(message["chat"]["id"])
                            text = message.get("text", "")
                            
                            # Обрабатываем только ваши команды
                            if chat_id == TELEGRAM_MY_CHAT_ID:
                                print(f"📩 Ваша команда: {text}")
                                _process_command(chat_id, text)
            
            time.sleep(1)
            
        except Exception as e:
            print(f"❌ Ошибка в listen_commands: {e}")
            time.sleep(5)

def _process_command(chat_id: str, command: str):
    """Обработка команд"""
    global trading_paused, auto_trading, emergency_stop
    
    cmd = command.lower().strip()
    
    if cmd == '/start':
        # Получаем реальные данные для стартового сообщения
        current_balance = get_real_balance()
        positions = get_real_positions()
        
        send_to_me(f"""
🤖 *ТОРГОВЫЙ БОТ BINANCE*

📊 *Режим:* {TRADING_MODE.upper()}
💰 *Баланс:* {current_balance:.2f} USDT
📈 *Позиций:* {len(positions)}

Доступные команды:

*Управление:*
/status - Статус бота
/pause  - Пауза торговли
/resume - Возобновить
/auto_on  - Автоторговля ВКЛ
/auto_off - Автоторговля ВЫКЛ
/emergency - Аварийная остановка
/reset - Сброс аварии

*Информация:*
/stats - Статистика
/settings - Настройки

*Для просмотра сделок подпишитесь на канал*
""")
    
    elif cmd == '/status':
        # Получаем реальные данные для статуса
        current_balance = get_real_balance()
        positions = get_real_positions()
        pnl_data = get_real_pnl()
        
        status_msg = f"""
📊 *СТАТУС БОТА*

• Режим: {TRADING_MODE.upper()}
• Торговля: {'▶️ АКТИВНА' if not trading_paused else '⏸ НА ПАУЗЕ'}
• Автоторговля: {'🤖 ВКЛ' if auto_trading else '👤 ВЫКЛ'}
• Аварийная остановка: {'🚨 АКТИВНА' if emergency_stop else '✅ НЕТ'}

💰 *Финансы:*
• Баланс: {current_balance:.2f} USDT
• Позиций: {len(positions)}
• Реализованный PnL: {pnl_data['realized']:+.2f} USDT
• Незакрытый PnL: {pnl_data['unrealized']:+.2f} USDT
• Общий PnL: {pnl_data['total']:+.2f} USDT

⚙️ *Настройки:*
• Таймфрейм: {TIMEFRAME}
• Плечо: {LEVERAGE}x
• Рик: {RISK_FRACTION*100}%

🕐 *Время:* {datetime.now().strftime('%H:%M:%S')}
"""
        send_to_me(status_msg)
    
    elif cmd == '/pause':
        trading_paused = True
        send_to_me("✅ Торговля приостановлена")
    
    elif cmd == '/resume':
        trading_paused = False
        send_to_me("✅ Торговля возобновлена")
    
    elif cmd == '/auto_on':
        auto_trading = True
        send_to_me("🤖 Автоторговля ВКЛЮЧЕНА")
    
    elif cmd == '/auto_off':
        auto_trading = False
        send_to_me("👤 Ручной режим ВКЛЮЧЕН")
    
    elif cmd == '/emergency':
        emergency_stop = True
        trading_paused = True
        send_to_me("🚨 АВАРИЙНАЯ ОСТАНОВКА АКТИВИРОВАНА!")
    
    elif cmd == '/reset':
        emergency_stop = False
        send_to_me("✅ Аварийная остановка отключена")
    
    elif cmd == '/stats':
        # Получаем подробную статистику
        pnl_data = get_real_pnl()
        positions = get_real_positions()
        current_balance = get_real_balance()
        
        stats_msg = f"""
📈 *СТАТИСТИКА ТОРГОВЛИ*

💰 *Баланс:* {current_balance:.2f} USDT
📊 *Позиций:* {len(positions)}

💵 *PnL:*
• Реализованный: {pnl_data['realized']:+.2f} USDT
• Незакрытый: {pnl_data['unrealized']:+.2f} USDT
• Общий: {pnl_data['total']:+.2f} USDT

📊 *Режим:* {TRADING_MODE.upper()}
⚡ *Статус:* {'АКТИВЕН' if not trading_paused else 'НА ПАУЗЕ'}
🤖 *Автоторговля:* {'ВКЛ' if auto_trading else 'ВЫКЛ'}

🕐 *Отчет:* {datetime.now().strftime('%H:%M:%S')}
"""
        send_to_me(stats_msg)
    
    elif cmd == '/settings':
        current_balance = get_real_balance()
        
        settings_msg = f"""
⚙️ *НАСТРОЙКИ БОТА*

📊 *Основные:*
• Режим: {TRADING_MODE.upper()}
• Таймфрейм: {TIMEFRAME}
• Плечо: {LEVERAGE}x
• Риск на сделку: {RISK_FRACTION*100}%
• Начальный капитал: {INITIAL_CASH} USDT
• Текущий баланс: {current_balance:.2f} USDT

⚡ *Управление:*
• Торговля: {'АКТИВНА' if not trading_paused else 'НА ПАУЗЕ'}
• Автоторговля: {'ВКЛ' if auto_trading else 'ВЫКЛ'}
• Аварийный стоп: {'АКТИВЕН' if emergency_stop else 'ВЫКЛ'}

🕐 *Обновлено:* {datetime.now().strftime('%H:%M:%S')}
"""
        send_to_me(settings_msg)
    
    elif cmd == '/help':
        send_to_me("""
📋 *ВСЕ КОМАНДЫ*

/start - Начало работы
/status - Статус бота с реальными данными
/pause - Пауза торговли
/resume - Возобновить торговлю
/auto_on - Включить автоторговлю
/auto_off - Выключить автоторговлю
/emergency - Аварийная остановка
/reset - Сброс аварии
/stats - Статистика торговли
/settings - Настройки бота
/help - Эта справка

⚠️ *Только вы можете управлять ботом*
""")
    
    else:
        send_to_me("❓ Неизвестная команда. Используйте /help")

# ========== ИНТЕГРАЦИОННЫЕ ФУНКЦИИ ==========
def should_trade() -> bool:
    """Проверка, можно ли торговать"""
    return not trading_paused and not emergency_stop and auto_trading

def get_trading_status() -> Dict:
    """Получение статуса торговли"""
    return {
        "paused": trading_paused,
        "active": not emergency_stop,
        "auto_trading": auto_trading,
        "emergency_stop": emergency_stop,
        "mode": TRADING_MODE
    }

# ========== ЗАПУСК ==========
def start_telegram_manager():
    """Запуск менеджера Telegram"""
    print("🤖 Инициализация Telegram менеджера...")
    
    # Проверяем конфигурацию
    if not TELEGRAM_BOT_TOKEN:
        print("❌ TELEGRAM_BOT_TOKEN не установлен")
        return False
    
    if not TELEGRAM_MY_CHAT_ID:
        print("❌ TELEGRAM_MY_CHAT_ID не установлен (ваш личный ID)")
        return False
    
    if not TELEGRAM_CHANNEL_ID:
        print("⚠️  TELEGRAM_CHANNEL_ID не установлен (красивые сообщения в канал отключены)")
    
    # Отправляем стартовые сообщения
    send_startup_message()
    
    # Запускаем прослушивание команд (только для вас)
    command_thread = threading.Thread(target=listen_commands, daemon=True)
    command_thread.start()
    
    print("✅ Telegram менеджер запущен")
    print(f"   👑 Ваше управление: ID {TELEGRAM_MY_CHAT_ID}")
    print(f"   📢 Канал с красивыми сообщениями: {'✅ ВКЛ' if SEND_TO_CHANNEL and TELEGRAM_CHANNEL_ID else '❌ ВЫКЛ'}")
    
    return True

# ========== ТЕСТИРОВАНИЕ ==========
if __name__ == "__main__":
    """Тестирование сообщений"""
    print("=== Тестирование красивых сообщений ===")
    
    # Тестируем все типы сообщений
    test_messages = [
        ("startup", {}),
        ("signal", {"symbol": "BTCUSDT", "side": "BUY", "price": 50000}),
        ("trade_open", {"symbol": "ETHUSDT", "side": "SELL", "price": 2500, "quantity": 0.1}),
        ("trade_close", {"symbol": "BNBUSDT", "side": "BUY", "entry_price": 300, 
                        "exit_price": 320, "quantity": 1, "pnl": 20, "reason": "Take Profit"}),
        ("status_update", {}),
    ]
    
    for msg_type, params in test_messages:
        print(f"\n📨 Тестируем: {msg_type}")
        message = create_channel_message(msg_type, **params)
        print(message[:200] + "...")
        print("-" * 50)