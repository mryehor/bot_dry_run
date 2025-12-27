from data_store import klines_cache, user_data_cache
from config import TRADING_MODE

def get_real_positions_pnl():
    """Получение PnL реальных позиций с Binance"""
    global binance_client
    
    if TRADING_MODE == 'dryrun' or binance_client is None:
        return 0.0
    
    try:
        if binance_client is None:
            from binance_client import BinanceClient
            binance_client = BinanceClient()
        
        # Получаем позиции с Binance
        # (нужно реализовать этот метод в binance_client.py)
        positions = binance_client.get_positions()
        
        total_pnl = 0.0
        for pos in positions:
            if float(pos.get('positionAmt', 0)) != 0:
                unrealized_pnl = float(pos.get('unRealizedProfit', 0))
                total_pnl += unrealized_pnl
        
        return total_pnl
    except Exception as e:
        print(f"❌ Ошибка получения реального PnL: {e}")
        return 0.0
    

def simulate_realtime_pnl(symbol: str):
    pos = user_data_cache.get("positions", {}).get(symbol)
    if not pos:
        return None

    df = klines_cache.get(symbol)
    if df is None or df.empty:
        return None

    prices = df["Close"].to_numpy()
    entry = pos["entry"]
    qty = pos["qty"]
    side = pos["side"]
    tp = pos.get("tp", entry * (1.01 if side == "BUY" else 0.99))
    sl = pos.get("sl", entry * (0.98 if side == "BUY" else 1.02))
    trail_percent = pos.get("trail_percent", 0.5) / 100.0
    trail_activation = 0.002
    trailing_active = False

    if side == "BUY":
        max_price = entry
        for price in prices:
            max_price = max(max_price, price)
            if not trailing_active and price >= entry * (1 + trail_activation):
                trailing_active = True
            if tp is not None and price >= tp:
                return (tp - entry) * qty
            if sl is not None and price <= sl:
                return (sl - entry) * qty
            if trailing_active and price < max_price * (1 - trail_percent):
                pnl = (price - entry) * qty
                if pnl > 0:
                    return pnl
        return (prices[-1] - entry) * qty
    else:  # SELL
        min_price = entry
        for price in prices:
            min_price = min(min_price, price)
            if not trailing_active and price <= entry * (1 - trail_activation):
                trailing_active = True
            if tp is not None and tp <= price:
                return (entry - tp) * qty
            if sl is not None and price >= sl:
                return (entry - sl) * qty
            if trailing_active and price > min_price * (1 + trail_percent):
                pnl = (entry - price) * qty
                if pnl > 0:
                    return pnl
        return (entry - prices[-1]) * qty

def get_total_pnl():
    """Получение общего PnL (учитывая режим)"""
    if TRADING_MODE == 'real':
        # Для реального режима
        from logger import realized_total_pnl
        
        # Получаем unrealized PnL с Binance
        unrealized_pnl = get_real_positions_pnl()
        
        # Общий PnL = закрытый + открытый
        total_pnl = realized_total_pnl + unrealized_pnl
        
        return {
            "realized": realized_total_pnl,
            "unrealized": unrealized_pnl,
            "total": total_pnl,
            "mode": "real"
        }
    else:
        # Для dryrun режима
        from logger import realized_total_pnl
        
        unrealized = 0.0
        for symbol in user_data_cache.get("positions", {}).keys():
            pnl = simulate_realtime_pnl(symbol)
            if pnl is not None:
                unrealized += pnl
        
        total_pnl = realized_total_pnl + unrealized
        
        return {
            "realized": realized_total_pnl,
            "unrealized": unrealized,
            "total": total_pnl,
            "mode": "dryrun"
        }

def format_pnl_message(pnl_data):
    """Форматирование сообщения о PnL"""
    mode = pnl_data["mode"]
    mode_emoji = "🚨" if mode == "real" else "🧪"
    
    message = f"{mode_emoji} *{mode.upper()} PnL Статистика*\n\n"
    message += f"💰 Закрытый PnL: `{pnl_data['realized']:.2f}`\n"
    message += f"📈 Открытый PnL: `{pnl_data['unrealized']:.2f}`\n"
    message += f"💵 Общий PnL: `{pnl_data['total']:.2f}`\n"
    
    if mode == "real":
        message += "\n⚠️ *РЕАЛЬНЫЕ ДЕНЬГИ* ⚠️"
    
    return message