import pandas as pd
import json
import os
from config import TRADING_MODE, POSITIONS_LOG_FILE  # добавляем импорт
import time
# Кеш свечей для каждого символа
# Формат: {"SYMBOL": pd.DataFrame с колонками ["Open", "High", "Low", "Close", "Volume"]}
klines_cache = {}

# Пользовательские данные (позиции, баланс и т.д.)
# Структура:
# {
#   "positions": {
#       "BTCUSDT": {
#           "side": "BUY" / "SELL",
#           "qty": float,
#           "entry": float,
#           "tp": float,
#           "sl": float,
#           "trail_percent": float,
#           "trailing": bool,
#           "trail_pending": bool
#       },
#       ...
#   },
#   "real_positions": []  # для хранения реальных позиций с Binance
# }
user_data_cache = {
    "positions": {},
    "real_positions": []  # добавляем поле для реальных позиций
}

def load_positions_from_file():
    """Загружает позиции из файла при запуске"""
    global user_data_cache
    
    try:
        if os.path.exists(POSITIONS_LOG_FILE):
            with open(POSITIONS_LOG_FILE, 'r') as f:
                data = json.load(f)
                
                # Загружаем только закрытые позиции для истории
                if "closed_positions" in data:
                    user_data_cache["closed_positions"] = data["closed_positions"]
                    
                print(f"✅ Загружены позиции из {POSITIONS_LOG_FILE}")
                return True
    except Exception as e:
        print(f"❌ Ошибка загрузки позиций из файла: {e}")
    
    return False

def save_positions_to_file():
    """Сохраняет позиции в файл"""
    try:
        # Сохраняем только историю закрытых позиций
        data_to_save = {
            "trading_mode": TRADING_MODE,
            "closed_positions": user_data_cache.get("closed_positions", []),
            "last_update": pd.Timestamp.now().isoformat()
        }
        
        with open(POSITIONS_LOG_FILE, 'w') as f:
            json.dump(data_to_save, f, indent=2, default=str)
            
        return True
    except Exception as e:
        print(f"❌ Ошибка сохранения позиций: {e}")
        return False

def sync_real_positions(binance_positions):
    """Синхронизация позиций с Binance"""
    try:
        from data_store import user_data_cache
        
        positions_dict = user_data_cache.get("positions", {})
        
        # Очищаем старые реальные позиции
        keys_to_remove = []
        for key, pos in positions_dict.items():
            if pos.get('source') == 'binance_real':
                keys_to_remove.append(key)
        
        for key in keys_to_remove:
            positions_dict.pop(key, None)
        
        # Добавляем актуальные позиции
        for pos in binance_positions:
            position_amt = float(pos.get('positionAmt', 0))
            
            if position_amt != 0:
                symbol = pos.get('symbol')
                if not symbol.endswith('USDT'):
                    symbol = symbol + 'USDT'
                
                positions_dict[symbol] = {
                    'symbol': symbol,
                    'side': 'BUY' if position_amt > 0 else 'SELL',
                    'qty': abs(position_amt),
                    'entry': float(pos.get('entryPrice', 0)),
                    'current_price': float(pos.get('markPrice', 0)),
                    'unrealized_pnl': float(pos.get('unRealizedProfit', 0)),
                    'realized_pnl': float(pos.get('realizedProfit', 0)),
                    'leverage': float(pos.get('leverage', 1)),
                    'source': 'binance_real',
                    'timestamp': time.time()
                }
        
        user_data_cache["positions"] = positions_dict
        print(f"✅ Синхронизировано позиций: {len([p for p in positions_dict.values() if p.get('source') == 'binance_real'])}")
        
    except Exception as e:
        print(f"❌ Ошибка синхронизации позиций: {e}")

def get_all_positions():
    """Возвращает все позиции (виртуальные и реальные)"""
    positions = []
    
    # Виртуальные позиции (для dryrun)
    for symbol, pos in user_data_cache.get("positions", {}).items():
        positions.append({
            'symbol': symbol,
            'source': 'virtual',
            **pos
        })
    
    # Реальные позиции (для real mode)
    positions.extend(user_data_cache.get("real_positions", []))
    
    return positions

# Вспомогательная функция для инициализации свечей (только для dryrun)
def load_sample_klines(symbol: str, n=100):
    """Создает тестовые свечи только для DRY_RUN режима"""
    if TRADING_MODE == 'real':
        print(f"⚠️  Режим real: тестовые свечи не создаются для {symbol}")
        return None
    
    import numpy as np
    close = 100 + np.cumsum(np.random.randn(n))
    high = close + np.random.rand(n) * 2
    low = close - np.random.rand(n) * 2
    open_ = close + np.random.randn(n)
    volume = np.random.randint(100, 1000, size=n)
    df = pd.DataFrame({"Open": open_, "High": high, "Low": low, "Close": close, "Volume": volume})
    klines_cache[symbol] = df
    return df

# Автоматическая загрузка позиций при импорте модуля
if __name__ != "__main__":
    try:
        load_positions_from_file()
        print(f"✅ DataStore инициализирован (Режим: {TRADING_MODE})")
    except Exception as e:
        print(f"⚠️  Ошибка загрузки DataStore: {e}")
        print(f"   Продолжаем с пустыми данными")