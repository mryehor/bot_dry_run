# websocket_handler.py
import asyncio
import pandas as pd
import time
from typing import List
from binance import AsyncClient, BinanceSocketManager
from config import API_KEY, API_SECRET, TIMEFRAME, TRADING_MODE
from data_store import klines_cache
from utils import bol_h, bol_l, rsi
from pos_manager import get_open_position, open_position, close_position
from telegram_bot import send_error as send_telegram_message
from logger import log_position

# ---------- fetch_historical_klines ----------
async def fetch_historical_klines(symbol: str, interval="5m", limit=500):
    if TRADING_MODE == "dryrun":
        # Возвращаем фиктивные данные для dry run
        df = pd.DataFrame([{"Open": 0, "High": 0, "Low": 0, "Close": 0, "Volume": 0}] * limit)
        df.index = pd.date_range(end=pd.Timestamp.now(), periods=limit, freq=interval)
        return df

    client = await AsyncClient.create(API_KEY, API_SECRET)
    try:
        raw = await client.futures_klines(symbol=symbol, interval=interval, limit=limit)
        df = pd.DataFrame(raw, columns=[
            "Open time", "Open", "High", "Low", "Close", "Volume",
            "Close time", "Quote asset volume", "Number of trades",
            "Taker buy base asset volume", "Taker buy quote asset volume", "Ignore"
        ])
        df["Open time"] = pd.to_datetime(df["Open time"], unit="ms")
        df["Close time"] = pd.to_datetime(df["Close time"], unit="ms")
        for col in ["Open", "High", "Low", "Close", "Volume"]:
            df[col] = df[col].astype(float)
        df.set_index("Close time", inplace=True)
        return df
    except Exception as e:
        print(f"❌ Ошибка загрузки {symbol}: {e}")
        return pd.DataFrame()
    finally:
        await client.close_connection()

# ---------- WebSocket handler ----------
async def handle_kline(msg):
    try:
        print(f"🔍 DEBUG: handle_kline вызван для символа: {msg.get('s', 'unknown')}")
        k = msg["k"]
        symbol = msg["s"]
        row = {
            "Open": float(k["o"]),
            "High": float(k["h"]),
            "Low": float(k["l"]),
            "Close": float(k["c"]),
            "Volume": float(k["v"]),
        }
        idx = pd.to_datetime(k["t"], unit="ms")

        # Обновляем кэш свечей
        df = klines_cache.get(symbol)
        if df is None or df.empty:
            df = pd.DataFrame([row], index=[idx])
        else:
            if idx in df.index:
                df.loc[idx] = row
            else:
                df = pd.concat([df, pd.DataFrame([row], index=[idx])])
                df = df.tail(500)
        klines_cache[symbol] = df

        # Проверка открытой позиции
        pos = get_open_position(symbol)
        price_last = row["Close"]
        signal = None

        # --- сигналы по индикаторам ---
        if len(df) > 2:
            lower = bol_l(df["Close"])[-1]
            upper = bol_h(df["Close"])[-1]
            rsi_val = rsi(df["Close"])[-1]
            if df["Close"].iloc[-2] > lower and df["Close"].iloc[-1] < lower and rsi_val < 30:
                signal = "BUY"
            elif df["Close"].iloc[-2] < upper and df["Close"].iloc[-1] > upper and rsi_val > 70:
                signal = "SELL"

        # --- сигналы по пробою ---
        period = 20
        if len(df) > period + 2:
            highest = df["High"].iloc[-period-1:-1].max()
            lowest = df["Low"].iloc[-period-1:-1].min()
            if price_last > highest:
                signal = "BUY"
            elif price_last < lowest:
                signal = "SELL"

        # --- если есть открытая позиция ---
        if pos:
            # ИСПРАВЛЯЕМ КЛЮЧИ!
            side = pos.get("side", "BUY")
            
            # Используем правильные ключи из get_positions()
            entry = pos.get("entry_price", pos.get("entry", 0))  # entry_price из get_positions()
            quantity = pos.get("quantity", pos.get("qty", 0))
            
            # TP и SL должны быть рассчитаны, т.к. Binance их не предоставляет
            # Рассчитываем их если нет в данных
            if "tp" not in pos or pos["tp"] is None:
                if side == "BUY":
                    tp = entry * 1.02  # +2%
                else:
                    tp = entry * 0.98  # -2%
            else:
                tp = pos.get("tp")
                
            if "sl" not in pos or pos["sl"] is None:
                if side == "BUY":
                    sl = entry * 0.98  # -2%
                else:
                    sl = entry * 1.02  # +2%
            else:
                sl = pos.get("sl")

            # проверка TP / SL и обратного сигнала через logger
            close_reason = None
            
            if side == "BUY":
                if signal == "SELL":
                    close_reason = "Обратный сигнал SELL"
                elif sl is not None and price_last <= sl:
                    close_reason = "Stop Loss достигнут"
                elif tp is not None and price_last >= tp:
                    close_reason = "Take Profit достигнут"
                    
            elif side == "SELL":
                if signal == "BUY":
                    close_reason = "Обратный сигнал BUY"
                elif sl is not None and price_last >= sl:
                    close_reason = "Stop Loss достигнут"
                elif tp is not None and price_last <= tp:
                    close_reason = "Take Profit достигнут"
            
            # Если есть причина для закрытия
            if close_reason:
                print(f"🚨 Закрытие позиции {symbol}: {close_reason}")
                print(f"   Entry: {entry}, Last: {price_last}, TP: {tp}, SL: {sl}")
                
                # Рассчитываем PnL
                if side == "BUY":
                    pnl = (price_last - entry) * quantity
                else:
                    pnl = (entry - price_last) * quantity
                
                # Закрываем позицию
                result = close_position(symbol, price_last, reason=close_reason)
                
                if result:
                    log_position("CLOSE", symbol, side, price_last, quantity, 
                                 pnl=pnl, tp=tp, sl=sl, exit_reason=close_reason)
                else:
                    print(f"❌ Ошибка при закрытии позиции {symbol}")
            else:
                # Показываем текущее состояние
                current_pnl = 0
                if side == "BUY":
                    current_pnl = (price_last - entry) * quantity
                else:
                    current_pnl = (entry - price_last) * quantity
                    
                print(f"⏳ Ожидаем: {symbol} {side}")
                print(f"   Entry: {entry}, Last: {price_last}")
                print(f"   TP: {tp:.2f}, SL: {sl:.2f}")
                print(f"   PnL: {current_pnl:+.2f} ({((price_last/entry - 1)*100):+.2f}%)")

        # --- если позиции нет и появился сигнал ---
        elif signal:
            print(f"🚀 Сигнал на открытие: {symbol} {signal}")
            
            # Открываем позицию
            pos_data = open_position(symbol, signal)
            
            if pos_data:
                # Получаем TP/SL из данных позиции или рассчитываем
                tp = pos_data.get("tp")
                sl = pos_data.get("sl")
                entry = pos_data.get("entry", price_last)
                quantity = pos_data.get("qty", 0)
                
                # Если нет TP/SL в данных, рассчитываем
                if tp is None:
                    if signal == "BUY":
                        tp = entry * 1.02
                    else:
                        tp = entry * 0.98
                        
                if sl is None:
                    if signal == "BUY":
                        sl = entry * 0.98
                    else:
                        sl = entry * 1.02
                
                log_position("OPEN", symbol, signal, entry, quantity, 
                             tp=tp, sl=sl, reason=f"Сигнал {signal}")

    except Exception as e:
        print("Ошибка в обработчике kline:", e)
        import traceback
        traceback.print_exc()
        
# ---------- start websockets ----------
async def start_websockets(symbols: List[str], interval: str = TIMEFRAME):
    if TRADING_MODE == 'dryrun':
        print("[DRY_RUN] WebSockets не запущены")
        return

    client = await AsyncClient.create(API_KEY, API_SECRET)
    bm = BinanceSocketManager(client)
    sockets = [bm.kline_socket(symbol=s, interval=interval) for s in symbols]

    async def listen(sock):
        async with sock as stream:
            while True:
                msg = await stream.recv()
                await handle_kline(msg)

    tasks = [asyncio.create_task(listen(sock)) for sock in sockets]
    
    mode_indicator = "🔴 РЕАЛЬНАЯ" if TRADING_MODE == 'real' else "🟡 ТЕСТОВАЯ"
    print(f"✅ WebSockets запущены ({mode_indicator}):", symbols)
    
    # Добавляем предупреждение для реальной торговли
    if TRADING_MODE == 'real':
        print("🚨 ВНИМАНИЕ: Бот подключен к реальной торговле!")
    
    await asyncio.gather(*tasks)

# ---------- get_liquid_tickers ----------
_liquid_tickers_cache = {"timestamp": 0, "tickers": []}

async def get_liquid_tickers(top_n=10, min_price=0.1, min_volume=1_000_000, max_spread_percent=5.0):
    global _liquid_tickers_cache
    
    if TRADING_MODE == 'dryrun':
        if not _liquid_tickers_cache["tickers"]:
            _liquid_tickers_cache["tickers"] = ["BTCUSDT", "ETHUSDT", "BNBUSDT"]
        return _liquid_tickers_cache["tickers"]

    client = await AsyncClient.create(API_KEY, API_SECRET)
    now = time.time()
    if now - _liquid_tickers_cache["timestamp"] < 3600:
        await client.close_connection()
        return _liquid_tickers_cache["tickers"]

    try:
        tickers = await client.futures_ticker()
        filtered = []
        for t in tickers:
            symbol = t.get("symbol")
            if not symbol or "USDT" not in symbol:
                continue
            try:
                price = float(t.get("lastPrice", 0))
                volume = float(t.get("quoteVolume", 0))
                high = float(t.get("highPrice", 0))
                low = float(t.get("lowPrice", 0))
                spread_percent = ((high - low) / price) * 100 if price else 100
                if price >= min_price and volume >= min_volume and spread_percent <= max_spread_percent:
                    filtered.append({"symbol": symbol, "volume": volume})
            except Exception:
                continue

        filtered.sort(key=lambda x: x["volume"], reverse=True)
        top_symbols = [x["symbol"] for x in filtered[:top_n]]
        _liquid_tickers_cache = {"timestamp": now, "tickers": top_symbols}
        
        # Логируем найденные тикеры
        print(f"📊 Найдено ликвидных тикеров: {len(top_symbols)}")
        if TRADING_MODE == 'real' and top_symbols:
            print(f"🔍 Торгуем в реальном режиме: {top_symbols[:3]}...")
        
        return top_symbols
    finally:
        await client.close_connection()