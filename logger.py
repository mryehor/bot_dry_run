import json
import pandas as pd
from config import POSITIONS_LOG_FILE, INITIAL_CASH, TRADING_MODE  # меняем DRY_RUN на TRADING_MODE
from data_store import user_data_cache
from pnl_utils import simulate_realtime_pnl
from binance_client import BinanceClient  # добавляем для реального баланса

realized_total_pnl = 0.0
opened_positions = set()  # (symbol, side, entry_price) для отслеживания открытых позиций

# Клиент для получения реального баланса
binance_client = None

def _write_log_entry(entry: dict):
    """Запись лога в файл"""
    with open(POSITIONS_LOG_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")

def escape_markdown(text):
    """Экранирование для Markdown"""
    if text is None:
        return "N/A"
    text = str(text)
    for ch in "_*[]()~`>#+-=|{}.!":
        text = text.replace(ch, f"\\{ch}")
    return text

def get_real_balance():
    """Получение реального баланса с Binance"""
    global binance_client
    
    if TRADING_MODE == 'dryrun':
        return None
    
    try:
        if binance_client is None:
            from binance_client import BinanceClient
            binance_client = BinanceClient()
        
        # Получаем реальный баланс USDT
        balance = binance_client.get_balance('USDT')
        return float(balance)
    except Exception as e:
        print(f"❌ Ошибка получения реального баланса: {e}")
        return None

def log_position(action, symbol, side, price, qty, pnl=0.0,
                 reason="DRY_RUN", exit_reason=None, tp=None, sl=None):
    global realized_total_pnl, opened_positions
    from telegram_bot import send_startup_message as send_telegram_message

    # Проверяем режим для сообщения
    if TRADING_MODE == 'real':
        reason = reason.replace("DRY_RUN", "REAL_TRADE")
    
    key = (symbol, side, price,)
    
    if action.upper() == "OPEN":
        if key in opened_positions:
            # Уже открыта — пропускаем
            return
        opened_positions.add(key)

    if action.upper() == "CLOSE":
        # Удаляем из открытых при закрытии
        opened_positions.discard(key)
        realized_total_pnl += pnl

    # unrealized PnL (только для dryrun)
    unrealized = 0.0
    if TRADING_MODE == 'dryrun':
        for s, p in user_data_cache.get("positions", {}).items():
            u = simulate_realtime_pnl(s)
            if u is not None:
                unrealized += u

    # Баланс аккаунта
    if TRADING_MODE == 'dryrun':
        total_equity = INITIAL_CASH + realized_total_pnl + unrealized
        account_balance = total_equity
    else:
        # Для реальной торговли получаем баланс с Binance
        real_balance = get_real_balance()
        if real_balance is not None:
            account_balance = real_balance
            total_equity = real_balance
        else:
            account_balance = INITIAL_CASH + realized_total_pnl
            total_equity = account_balance

    # лог всегда создаётся
    log_entry = {
        "timestamp": pd.Timestamp.now().isoformat(),
        "action": action,
        "symbol": symbol,
        "side": side,
        "price": price,
        "qty": qty,
        "pnl": pnl,
        "total_equity": total_equity,
        "account_balance": account_balance,
        "trading_mode": TRADING_MODE,  # добавляем режим торговли
        "reason": reason,
        "exit_reason": exit_reason,
        "tp": tp,
        "sl": sl
    }

    # Вывод в консоль с указанием режима
    mode_indicator = "🟢 REAL" if TRADING_MODE == 'real' else "🟡 DRY"
    print(f"[{log_entry['timestamp']}] {mode_indicator} {action} {side} {symbol} @ {price:.4f} "
          f"QTY={qty:.4f} PnL={pnl:.4f} TotalEquity={total_equity:.4f}")

    _write_log_entry(log_entry)

    # отправка в Telegram
    try:
        side_emoji = "🟢 LONG" if side.upper() == "BUY" else "🔴 SHORT"
        action_emoji = "📌" if action.upper() == "OPEN" else "✅"
        mode_emoji = "🚨" if TRADING_MODE == 'real' else "🧪"
        
        # Заголовок с указанием режима
        if TRADING_MODE == 'real':
            title = f"{mode_emoji} *РЕАЛЬНАЯ СДЕЛКА* {action_emoji}"
        else:
            title = f"{mode_emoji} *ТЕСТОВАЯ СДЕЛКА* {action_emoji}"
        
        text = (
            f"{title} *{escape_markdown(action)}* {side_emoji} *{escape_markdown(symbol)}*\n"
            f"💰 Цена: `{price:.4f}`\n"
            f"📊 Количество: `{qty:.4f}`\n"
            f"💵 PnL: `{pnl:.4f}`\n"
            f"💹 Общий баланс: `{total_equity:.4f}`\n"
            f"🏦 Баланс счёта: `{account_balance:.4f}`\n"
            f"📝 Причина: {escape_markdown(reason)}"
        )

        # добавляем TP/SL, если заданы
        if tp is not None:
            text += f"\n🎯 TP: `{tp:.4f}`"
        if sl is not None:
            text += f"\n🛑 SL: `{sl:.4f}`"

        if exit_reason:
            text += f"\n⚡ Причина выхода: {escape_markdown(exit_reason)}"
            
        # Добавляем предупреждение для реальных сделок
        if TRADING_MODE == 'real':
            text += f"\n\n⚠️ *ВНИМАНИЕ: РЕАЛЬНАЯ СДЕЛКА* ⚠️"

        send_telegram_message(text)
    except Exception as e:
        print("Ошибка отправки лога в Telegram:", e)

def get_recent_logs(limit=50):
    """Получение последних логов"""
    logs = []
    try:
        with open(POSITIONS_LOG_FILE, "r", encoding="utf-8") as f:
            lines = f.readlines()[-limit:]
            for line in lines:
                logs.append(json.loads(line))
    except Exception as e:
        print("Ошибка чтения логов:", e)
    return logs

def get_trading_summary():
    """Получение сводки по торговле"""
    logs = get_recent_logs(1000)
    
    if not logs:
        return {"total_trades": 0, "win_rate": 0, "total_pnl": 0}
    
    closed_trades = [log for log in logs if log.get("action") == "CLOSE"]
    
    if not closed_trades:
        return {"total_trades": 0, "win_rate": 0, "total_pnl": 0}
    
    winning_trades = sum(1 for trade in closed_trades if trade.get("pnl", 0) > 0)
    total_trades = len(closed_trades)
    win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 0
    total_pnl = sum(trade.get("pnl", 0) for trade in closed_trades)
    
    return {
        "total_trades": total_trades,
        "winning_trades": winning_trades,
        "losing_trades": total_trades - winning_trades,
        "win_rate": win_rate,
        "total_pnl": total_pnl,
        "trading_mode": TRADING_MODE,
        "initial_cash": INITIAL_CASH,
        "current_balance": realized_total_pnl + INITIAL_CASH
    }