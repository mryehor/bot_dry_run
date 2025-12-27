# run_simple.py - Простой запуск
import asyncio
from websocket_handler import get_liquid_tickers, fetch_historical_klines, start_websockets
from data_store import klines_cache
from config import TRADING_MODE, TIMEFRAME, CHECK_INTERVAL
from strategies import get_trading_signal
from pos_manager import get_open_position, open_position

print(f"🤖 Binance Trading Bot - Режим: {TRADING_MODE.upper()}")

async def trade(symbol):
    print(f"📈 Начинаем торговлю {symbol}")
    
    while True:
        try:
            # Получаем данные
            df = klines_cache.get(symbol)
            if df is None or len(df) < 50:
                await asyncio.sleep(5)
                continue
            
            # Проверяем позицию
            pos = get_open_position(symbol)
            if pos:
                print(f"📊 {symbol}: позиция {pos.get('side', '?')}")
                await asyncio.sleep(CHECK_INTERVAL)
                continue
            
            # Получаем сигнал
            signal = get_trading_signal(symbol, df)
            
            if signal:
                price = float(df["Close"].iloc[-1])
                print(f"⚡ Сигнал: {signal} {symbol} @ {price:.2f}")
                
                if TRADING_MODE == 'real':
                    print("🚨 РЕАЛЬНАЯ ТОРГОВЛЯ - требуется подтверждение")
                    confirm = input(f"Открыть {signal} {symbol}? (yes/no): ")
                    if confirm.lower() != 'yes':
                        print("❌ Отменено")
                        await asyncio.sleep(CHECK_INTERVAL)
                        continue
                
                # Открываем позицию
                try:
                    open_position(symbol, signal)
                    print(f"✅ Позиция открыта")
                except Exception as e:
                    print(f"❌ Ошибка: {e}")
            
        except Exception as e:
            print(f"❌ Ошибка в {symbol}: {e}")
        
        await asyncio.sleep(CHECK_INTERVAL)

async def main():
    # Получаем тикеры
    symbols = await get_liquid_tickers(top_n=3)
    if not symbols:
        symbols = ["BTCUSDT"]
    
    print(f"🎯 Торгуем: {symbols}")
    
    # Загружаем данные
    for symbol in symbols:
        df = await fetch_historical_klines(symbol, TIMEFRAME, 200)
        if not df.empty:
            klines_cache[symbol] = df
            print(f"✅ {symbol}: {len(df)} свечей")
    
    # Запускаем WebSocket
    await start_websockets(symbols, TIMEFRAME)
    
    # Запускаем торговлю
    tasks = [asyncio.create_task(trade(sym)) for sym in symbols]
    await asyncio.gather(*tasks)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n👋 Остановлено")
    except Exception as e:
        print(f"💥 Ошибка: {e}")