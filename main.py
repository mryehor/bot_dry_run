"""
Главный файл для запуска торгового бота Binance с Telegram управлением
Автоматические сделки без подтверждения
"""

import asyncio
import time
import traceback
import threading
import sys
from datetime import datetime

# Импорт модулей
from websocket_handler import get_liquid_tickers, fetch_historical_klines, start_websockets
from binance_client import binance_client
from config import (
    TIMEFRAME, CHECK_INTERVAL, TOP_N_TICKERS, MIN_PRICE, MIN_VOLUME,
    MAX_SPREAD_PERCENT, TRADING_MODE, USE_BBRSI, USE_BREAKOUT,
    BBRSI_PARAM_GRID, BREAKOUT_PARAM_GRID, INITIAL_CASH,
    LEVERAGE, RISK_FRACTION, LOG_FILE,
    TELEGRAM_BOT_TOKEN, TELEGRAM_MY_CHAT_ID, TELEGRAM_CHANNEL_ID
)
from strategies import BBRSI_EMA_Strategy, Breakout_Strategy, get_trading_signal
from pos_manager import (
    get_open_position, open_position, close_position, init_binance_client
)
from backtesting.lib import FractionalBacktest
from utils import bol_h, bol_l, rsi, validate_trade_params
from pnl_utils import simulate_realtime_pnl, get_total_pnl, format_pnl_message
from data_store import load_positions_from_file, save_positions_to_file, klines_cache

# Импорт Telegram бота
from telegram_bot import (
    start_telegram_manager,
    should_trade,
    get_trading_status,
    send_startup_message,
    send_signal_alert,
    send_trade_opened,
    send_trade_closed,
    send_status_update,
    send_error,
    send_to_me  # Добавляем для простых сообщений
)

# ========== ОПТИМИЗАЦИЯ ==========
def optimize_params_ws(symbol, strategy_class, param_grid):
    df = klines_cache.get(symbol)
    if df is None or len(df) < 150:
        return None

    best_eq = -float("inf")
    best_params = {}

    for params in param_grid:
        class TempStrategy(strategy_class):
            pass
        for k, v in params.items():
            setattr(TempStrategy, k, v)
        try:
            bt = FractionalBacktest(df, TempStrategy, cash=INITIAL_CASH, margin=1, commission=0.005, finalize_trades=True)
            stats = bt.run()
            eq_final = stats.get("Equity Final [$]", None)
            if eq_final is not None and eq_final > best_eq:
                best_eq = eq_final
                best_params = params
        except Exception:
            continue
    return best_params

def optimize_and_select_top_ws(symbols):
    """Оптимизация и выбор топ-5 символов"""
    results = []
    for symbol in symbols:
        total_equity = 0.0
        df = klines_cache.get(symbol)
        if df is None or df.empty:
            print(f"[WARN] Нет данных по {symbol}")
            continue

        # BBRSI
        if USE_BBRSI:
            try:
                params = optimize_params_ws(symbol, BBRSI_EMA_Strategy, BBRSI_PARAM_GRID)
                if params:
                    BBRSI_EMA_Strategy.bol_period = params["bol_period"]
                    BBRSI_EMA_Strategy.bol_dev = params["bol_dev"]
                    BBRSI_EMA_Strategy.rsi_period = params["rsi_period"]
                bt = FractionalBacktest(df, BBRSI_EMA_Strategy, cash=INITIAL_CASH, margin=1, commission=0.005, finalize_trades=True)
                stats = bt.run()
                equity = stats.get("Equity Final [$]", 0.0)
                total_equity += equity
                print(f"[INFO] {symbol} BBRSI equity: {equity}")
            except Exception as e:
                print(f"[ERROR] BBRSI бэктест {symbol} упал:", e)

        # BREAKOUT
        if USE_BREAKOUT:
            try:
                params_b = optimize_params_ws(symbol, Breakout_Strategy, BREAKOUT_PARAM_GRID)
                if params_b:
                    Breakout_Strategy.period = params_b["period"]
                bt2 = FractionalBacktest(df, Breakout_Strategy, cash=INITIAL_CASH, margin=1, commission=0.005, finalize_trades=True)
                stats2 = bt2.run()
                equity2 = stats2.get("Equity Final [$]", 0.0)
                total_equity += equity2
                print(f"[INFO] {symbol} BREAKOUT equity: {equity2}")
            except Exception as e:
                print(f"[ERROR] BREAKOUT бэктест {symbol} упал:", e)

        results.append((symbol, total_equity))

    # сортируем по equity и выбираем топ-5
    if not results:
        print("[WARN] Нет результатов оптимизации, берём первые 5 символов")
        return symbols[:5]

    results.sort(key=lambda x: x[1], reverse=True)
    top5 = results[:5]
    print("[INFO] Top5 монет:", top5)
    return top5
    
# ========== ЦИКЛ МОНИТОРИНГА TP/SL ==========
async def tp_sl_monitor_loop():
    """Цикл мониторинга и закрытия позиций по TP/SL"""
    print("🎯 Запуск цикла мониторинга TP/SL...")
    
    from pos_manager import auto_close_positions
    from telegram_bot import send_to_me
    
    check_interval = 10  # Проверять каждые 10 секунд
    last_report_time = time.time()
    report_interval = 300  # Отчет каждые 5 минут
    
    while True:
        try:
            # Проверяем статус торговли
            from telegram_bot import should_trade
            if not should_trade():
                await asyncio.sleep(30)
                continue
            
            # Автоматически закрываем позиции по TP/SL
            closed_positions = auto_close_positions()
            
            # Отправляем уведомления о закрытых позициях
            if closed_positions:
                for pos in closed_positions:
                    msg = f"""
✅ ПОЗИЦИЯ ЗАКРЫТА АВТОМАТИЧЕСКИ:

Символ: {pos['symbol']}
Причина: {pos['reason']}
PnL: {pos['pnl']:+.2f} ({pos['pnl_percent']:+.2f}%)
Время: {datetime.now().strftime('%H:%M:%S')}
"""
                    try:
                        send_to_me(msg)
                    except:
                        print(f"⚠️  Не удалось отправить уведомление о закрытии")
            
            # Периодический отчет
            current_time = time.time()
            if current_time - last_report_time > report_interval:
                from data_store import user_data_cache
                positions_dict = user_data_cache.get("positions", {})
                open_positions = [p for p in positions_dict.values() if p.get('status') == 'OPEN']
                
                if open_positions:
                    report = f"📊 ОТКРЫТЫЕ ПОЗИЦИИ ({len(open_positions)}):\n"
                    
                    for pos in open_positions[:5]:  # Первые 5 позиций
                        side = pos.get('side', 'BUY')
                        entry = pos.get('entry', 0)
                        current = pos.get('current_price', entry)
                        tp = pos.get('tp_price', 0)
                        sl = pos.get('sl_price', 0)
                        pnl = pos.get('unrealized_pnl', 0)
                        
                        # Расчет расстояния до TP/SL в %
                        if side == 'BUY':
                            to_tp = ((tp - current) / current) * 100 if tp > 0 else 0
                            to_sl = ((current - sl) / current) * 100 if sl > 0 else 0
                        else:
                            to_tp = ((current - tp) / current) * 100 if tp > 0 else 0
                            to_sl = ((sl - current) / current) * 100 if sl > 0 else 0
                        
                        report += f"   {pos['symbol']} {side}: "
                        report += f"PnL={pnl:+.2f}, "
                        report += f"До TP: {to_tp:.1f}%, "
                        report += f"До SL: {to_sl:.1f}%\n"
                    
                    try:
                        send_to_me(report)
                    except:
                        print("⚠️  Не удалось отправить отчет")
                
                last_report_time = current_time
            
            await asyncio.sleep(check_interval)
            
        except Exception as e:
            print(f"❌ Ошибка в цикле мониторинга TP/SL: {e}")
            await asyncio.sleep(30)


# ========== ТОРГОВЫЙ ЦИКЛ ==========
async def trade_symbol_loop(symbol):
    """Основной торговый цикл для символа"""
    
    print(f"📈 Запущен торговый цикл для {symbol} (Режим: {TRADING_MODE})")
    
    # Переменные для отслеживания
    last_position_check = 0
    position_check_interval = 10  # Проверять позицию каждые 10 секунд
    
    while True:
        try:
            current_time = time.time()
            
            # Проверяем, можно ли торговать
            if not should_trade():
                await asyncio.sleep(CHECK_INTERVAL)
                continue
            
            df = klines_cache.get(symbol)
            if df is None or len(df) < 20:
                await asyncio.sleep(CHECK_INTERVAL)
                continue

            # УЛУЧШЕННАЯ ПРОВЕРКА ПОЗИЦИИ
            if current_time - last_position_check > position_check_interval:
                # 1. Проверяем позицию через стандартную функцию
                pos = get_open_position(symbol)
                
                # 2. Для реальной торговли дополнительно проверяем Binance
                if TRADING_MODE == 'real' and (not pos or pos.get('source') != 'binance_real'):
                    try:
                        from binance_client import binance_client
                        if binance_client:
                            # Получаем реальные позиции с Binance
                            positions = binance_client.get_positions()
                            found_real = False
                            
                            for binance_pos in positions:
                                # Приводим символы к одному формату
                                binance_symbol = binance_pos.get('symbol', '')
                                search_symbol = symbol.replace('USDT', '')
                                
                                if binance_symbol == search_symbol:
                                    position_amt = float(binance_pos.get('positionAmt', 0))
                                    if abs(position_amt) > 0:
                                        print(f"✅ Найдена реальная позиция на Binance: {symbol}")
                                        print(f"   Количество: {position_amt}")
                                        print(f"   Цена входа: {binance_pos.get('entryPrice', 'unknown')}")
                                        
                                        # Создаем объект позиции
                                        pos = {
                                            "symbol": symbol,
                                            "side": "BUY" if position_amt > 0 else "SELL",
                                            "qty": abs(position_amt),
                                            "entry": float(binance_pos.get('entryPrice', 0)),
                                            "current_price": float(df["Close"].iloc[-1]),
                                            "source": "binance_real",
                                            "status": "OPEN",
                                            "timestamp": current_time
                                        }
                                        
                                        # Сохраняем в кэш
                                        from data_store import user_data_cache
                                        positions_dict = user_data_cache.get("positions", {})
                                        positions_dict[symbol] = pos
                                        user_data_cache["positions"] = positions_dict
                                        
                                        found_real = True
                                        break
                            
                            if not found_real:
                                print(f"ℹ️  Нет реальной позиции на Binance для {symbol}")
                    
                    except Exception as e:
                        print(f"❌ Ошибка проверки реальной позиции {symbol}: {e}")
                
                last_position_check = current_time
            
            # Проверяем позицию снова (возможно обновилась)
            pos = get_open_position(symbol)
            
            if pos:
                price_last = float(df["Close"].iloc[-1])
                entry = pos.get("entry", price_last)
                qty = pos.get("qty", 0)
                
                if qty > 0:
                    # Расчет PnL
                    if pos.get('side') == "BUY":
                        pnl = (price_last - entry) * qty
                    else:
                        pnl = (entry - price_last) * qty
                    
                    pnl_percent = (pnl / (entry * qty)) * 100 if entry > 0 and qty > 0 else 0
                    
                    # Логируем статус позиции
                    print(f"⏳ {symbol} {pos.get('side')}: "
                          f"entry={entry:.4f}, current={price_last:.4f}, "
                          f"qty={qty:.4f}, PnL={pnl:+.2f} ({pnl_percent:+.2f}%)")
                    
                    # Для реальных позиций периодически проверяем статус
                    if TRADING_MODE == 'real' and pos.get('source') == 'binance_real':
                        try:
                            from binance_client import binance_client
                            positions = binance_client.get_positions()
                            
                            still_open = False
                            for binance_pos in positions:
                                if binance_pos.get('symbol') == symbol.replace('USDT', ''):
                                    position_amt = float(binance_pos.get('positionAmt', 0))
                                    if abs(position_amt) > 0:
                                        still_open = True
                                        
                                        # Обновляем количество если изменилось
                                        if abs(position_amt) != qty:
                                            print(f"⚠️  Количество изменилось: было {qty}, стало {abs(position_amt)}")
                                            pos['qty'] = abs(position_amt)
                                            
                                            # Обновляем в кэше
                                            from data_store import user_data_cache
                                            if symbol in user_data_cache.get("positions", {}):
                                                user_data_cache["positions"][symbol]['qty'] = abs(position_amt)
                                        break
                            
                            if not still_open:
                                print(f"⚠️  Позиция {symbol} закрыта на Binance, удаляю из кэша")
                                # Очищаем из кэша
                                from data_store import user_data_cache
                                if symbol in user_data_cache.get("positions", {}):
                                    user_data_cache["positions"].pop(symbol)
                        
                        except Exception as e:
                            print(f"❌ Ошибка проверки статуса реальной позиции: {e}")
                
                await asyncio.sleep(CHECK_INTERVAL)
                continue

            # ПРОВЕРКА ОЖИДАЮЩИХ ОРДЕРОВ
            if TRADING_MODE == 'real':
                from data_store import user_data_cache
                positions_dict = user_data_cache.get("positions", {})
                cached_pos = positions_dict.get(symbol)
                
                if cached_pos and cached_pos.get('order_id'):
                    print(f"⚠️  Для {symbol} есть ожидающий ордер: {cached_pos.get('order_id')}")
                    print(f"   Пропускаю торговый цикл пока ордер не исполнится")
                    
                    # Проверяем статус ордера
                    try:
                        from binance_client import binance_client
                        from pos_manager import check_order_status
                        
                        order_status = check_order_status(cached_pos['order_id'], symbol)
                        
                        if order_status.get('status') == 'FILLED':
                            print(f"✅ Ордер {cached_pos['order_id']} исполнен!")
                            # Обновляем статус позиции
                            cached_pos['status'] = 'OPEN'
                            positions_dict[symbol] = cached_pos
                            user_data_cache["positions"] = positions_dict
                        elif order_status.get('status') in ['CANCELED', 'REJECTED', 'EXPIRED']:
                            print(f"❌ Ордер {cached_pos['order_id']} отменен/отклонен")
                            # Удаляем из кэша
                            positions_dict.pop(symbol, None)
                            user_data_cache["positions"] = positions_dict
                    
                    except Exception as e:
                        print(f"❌ Ошибка проверки ордера: {e}")
                    
                    await asyncio.sleep(CHECK_INTERVAL)
                    continue

            # ТОЛЬКО ЕСЛИ НЕТ ПОЗИЦИИ И НЕТ ОЖИДАЮЩИХ ОРДЕРОВ - ПРОВЕРЯЕМ СИГНАЛЫ
            signal = get_trading_signal(symbol, df, strategy="bb_rsi")
            
            if not signal and USE_BREAKOUT:
                signal = get_trading_signal(symbol, df, strategy="breakout")
            
            if signal:
                price_last = float(df["Close"].iloc[-1])
                msg = f"⚡ Сигнал для {symbol}: {signal} | Цена: {price_last:.4f}"
                print(msg)
                
                # Отправляем уведомление в Telegram (ИСПРАВЛЕНО)
                try:
                    # Отправляем простое сообщение вместо send_signal_alert
                    send_to_me(f"⚡ СИГНАЛ: {symbol} {signal} @ {price_last:.4f}")
                except:
                    print("⚠️  Не удалось отправить в Telegram")
                
                # Дополнительная проверка для реальной торговли
                if TRADING_MODE == 'real':
                    # Проверяем баланс
                    try:
                        from binance_client import binance_client
                        balance = binance_client.get_balance('USDT')
                        if balance < 20:  # Минимальный номинал
                            print(f"❌ Недостаточно баланса: {balance:.2f} USDT < 20 USDT")
                            await asyncio.sleep(CHECK_INTERVAL)
                            continue
                    except Exception as e:
                        print(f"❌ Не удалось проверить баланс: {e}")
                        await asyncio.sleep(CHECK_INTERVAL)
                        continue
                
                # Открываем позицию (АВТОМАТИЧЕСКИ - без подтверждения)
                side = signal  # "BUY" или "SELL"
                
                if TRADING_MODE == 'real':
                    print(f"🚨 РЕАЛЬНАЯ СДЕЛКА (АВТО): {side} {symbol} @ {price_last:.4f}")
                    # Автоматически подтверждаем сделку для реальной торговли
                    print("✅ Подтверждение автоматическое - открываем позицию")
                
                try:
                    # Используем параметр risk_fraction из config
                    pos_data = open_position(symbol, side, risk_fraction=RISK_FRACTION)
                    
                    if pos_data:
                        success_msg = f"✅ Позиция открыта: {side} для {symbol} @ {price_last:.4f}"
                        print(success_msg)
                        
                        quantity = pos_data.get('qty', 0)
                        notional = price_last * quantity
                        
                        # Отправляем алерты (ИСПРАВЛЕНО)
                        try:
                            if TRADING_MODE == 'real':
                                send_to_me(f"🚨 РЕАЛЬНАЯ СДЕЛКА: {success_msg}")
                            else:
                                send_to_me(f"💰 ТЕСТОВАЯ СДЕЛКА: {success_msg}")
                        except:
                            print("⚠️  Не удалось отправить уведомление о сделке")
                        
                        # После открытия позиции ждем дольше перед следующей проверкой
                        await asyncio.sleep(CHECK_INTERVAL * 2)
                    else:
                        error_msg = f"❌ Не удалось открыть позицию для {symbol}"
                        print(error_msg)
                        try:
                            send_error(error_msg)
                        except:
                            pass
                        
                        # При ошибке ждем немного дольше
                        await asyncio.sleep(CHECK_INTERVAL * 3)
                        
                except Exception as e:
                    error_msg = f"❌ Ошибка открытия позиции для {symbol}: {e}"
                    print(error_msg)
                    try:
                        send_error(error_msg)
                    except:
                        pass
                    traceback.print_exc()
                    
                    # При ошибке ждем дольше
                    await asyncio.sleep(CHECK_INTERVAL * 5)

        except Exception as e:
            error_msg = f"❌ Критическая ошибка в торговом цикле {symbol}: {e}"
            print(error_msg)
            
            try:
                send_error(error_msg)
            except:
                pass
            traceback.print_exc()
            
            # При критической ошибке ждем дольше
            await asyncio.sleep(CHECK_INTERVAL * 10)

        await asyncio.sleep(CHECK_INTERVAL)

# ========== ЦИКЛ МОНИТОРИНГА ==========
async def monitoring_loop():
    """Цикл мониторинга системы"""
    
    print("📊 Запущен цикл мониторинга системы")
    
    last_pnl_report = time.time()
    last_status_report = time.time()
    last_cache_refresh = time.time()
    last_cleanup = time.time()
    
    pnl_report_interval = 300  # 5 минут
    status_report_interval = 3600  # 1 час
    cache_refresh_interval = 30  # 30 секунд
    cleanup_interval = 300  # 5 минут
    
    while True:
        try:
            current_time = time.time()
            
            # 1. ОБНОВЛЕНИЕ КЭША ПОЗИЦИЙ (каждые 30 секунд)
            if current_time - last_cache_refresh > cache_refresh_interval:
                try:
                    print(f"🔄 Обновление кэша позиций...")
                    
                    if TRADING_MODE == 'real':
                        # Получаем реальные позиции с Binance
                        from binance_client import binance_client
                        from data_store import user_data_cache
                        
                        try:
                            positions = binance_client.get_positions()
                            positions_dict = user_data_cache.get("positions", {})
                            
                            # Очищаем старые реальные позиции из кэша
                            keys_to_remove = []
                            for key, pos in positions_dict.items():
                                if pos.get('source') == 'binance_real':
                                    keys_to_remove.append(key)
                            
                            for key in keys_to_remove:
                                positions_dict.pop(key, None)
                            
                            # Добавляем актуальные позиции с Binance
                            for pos in positions:
                                position_amt = float(pos.get('positionAmt', 0))
                                
                                if abs(position_amt) > 0:
                                    symbol = pos.get('symbol')
                                    if not symbol.endswith('USDT'):
                                        symbol = symbol + 'USDT'
                                    
                                    # Получаем текущую цену из кэша свечей
                                    current_price = 0
                                    try:
                                        df = klines_cache.get(symbol)
                                        if df is not None and not df.empty:
                                            current_price = float(df["Close"].iloc[-1])
                                    except:
                                        current_price = float(pos.get('markPrice', 0))
                                    
                                    positions_dict[symbol] = {
                                        "symbol": symbol,
                                        "side": "BUY" if position_amt > 0 else "SELL",
                                        "qty": abs(position_amt),
                                        "entry": float(pos.get('entryPrice', 0)),
                                        "current_price": current_price,
                                        "unrealized_pnl": float(pos.get('unRealizedProfit', 0)),
                                        "leverage": float(pos.get('leverage', 1)),
                                        "source": "binance_real",
                                        "status": "OPEN",
                                        "last_updated": current_time
                                    }
                            
                            user_data_cache["positions"] = positions_dict
                            print(f"✅ Кэш обновлен: {len([p for p in positions_dict.values() if p.get('source') == 'binance_real'])} реальных позиций")
                            
                        except Exception as e:
                            print(f"❌ Ошибка обновления кэша с Binance: {e}")
                    
                    last_cache_refresh = current_time
                except Exception as e:
                    print(f"❌ Ошибка при обновлении кэша: {e}")
            
            # 2. ОЧИСТКА УСТАРЕВШИХ ПОЗИЦИЙ (каждые 5 минут)
            if current_time - last_cleanup > cleanup_interval:
                try:
                    print(f"🧹 Очистка устаревших записей...")
                    
                    from data_store import user_data_cache
                    positions_dict = user_data_cache.get("positions", {})
                    
                    if positions_dict:
                        removed_count = 0
                        keys_to_remove = []
                        
                        for symbol, pos in positions_dict.items():
                            last_updated = pos.get('last_updated', 0)
                            
                            # Удаляем записи старше 1 часа
                            if current_time - last_updated > 3600:
                                keys_to_remove.append(symbol)
                                removed_count += 1
                            
                            # Удаляем позиции с нулевым количеством
                            elif pos.get('qty', 0) <= 0:
                                keys_to_remove.append(symbol)
                                removed_count += 1
                        
                        for key in keys_to_remove:
                            positions_dict.pop(key, None)
                        
                        if removed_count > 0:
                            user_data_cache["positions"] = positions_dict
                            print(f"🗑️  Удалено {removed_count} устаревших позиций")
                    
                    last_cleanup = current_time
                except Exception as e:
                    print(f"❌ Ошибка очистки: {e}")
            
            # 3. Отчет о PnL каждые 5 минут
            if current_time - last_pnl_report > pnl_report_interval:
                try:
                    pnl_data = get_total_pnl()
                    pnl_message = format_pnl_message(pnl_data)
                    
                    # Отправляем отчет в Telegram (ИСПРАВЛЕНО)
                    try:
                        send_to_me(f"📊 ОТЧЕТ PnL:\n{pnl_message}")
                    except:
                        print("⚠️  Не удалось отправить отчет PnL")
                    
                    # Выводим в консоль
                    print(f"\n📊 Отчет PnL ({TRADING_MODE}):")
                    print(f"   Закрытый PnL: {pnl_data['realized']:.2f}")
                    print(f"   Открытый PnL: {pnl_data['unrealized']:.2f}")
                    print(f"   Общий PnL: {pnl_data['total']:.2f}")
                    
                    # Дополнительная информация о позициях
                    try:
                        from data_store import user_data_cache
                        positions_dict = user_data_cache.get("positions", {})
                        open_positions = [p for p in positions_dict.values() if p.get('status') == 'OPEN']
                        
                        if open_positions:
                            print(f"   📈 Открытых позиций: {len(open_positions)}")
                            for pos in open_positions[:3]:  # Показываем первые 3
                                pnl_pos = pos.get('unrealized_pnl', 0)
                                if pnl_pos != 0:
                                    print(f"     {pos['symbol']}: {pnl_pos:+.2f}")
                    except:
                        pass
                    
                    last_pnl_report = current_time
                except Exception as e:
                    print(f"❌ Ошибка при получении PnL: {e}")
            
            # 4. Отчет о статусе каждые час
            if current_time - last_status_report > status_report_interval:
                try:
                    status = get_trading_status()
                    if not status.get("paused", False):
                        # Отправляем обновление статуса
                        try:
                            status_msg = f"""
📊 СТАТУС БОТА:

Режим: {TRADING_MODE.upper()}
Торговля: {'✅ АКТИВНА' if not status['paused'] else '⏸ НА ПАУЗЕ'}
Автоторговля: {'🤖 ВКЛ' if status['auto_trading'] else '👤 ВЫКЛ'}
Аварийный стоп: {'🚨 ВКЛ' if status['emergency_stop'] else '✅ ВЫКЛ'}

Время: {datetime.now().strftime('%H:%M:%S')}
"""
                            send_to_me(status_msg)
                        except:
                            print("⚠️  Не удалось отправить статус")
                    
                    last_status_report = current_time
                except Exception as e:
                    print(f"❌ Ошибка при отправке статуса: {e}")
            
            # 5. Проверка статуса системы
            status = get_trading_status()
            if status["paused"] and TRADING_MODE == 'real':
                print("⚠️  Торговля на паузе в РЕАЛЬНОМ режиме!")
            
            # 6. ПРОВЕРКА КРИТИЧЕСКИХ ОШИБОК
            try:
                from data_store import user_data_cache
                error_count = user_data_cache.get("error_count", 0)
                if error_count > 10:
                    print(f"🚨 Критическое количество ошибок: {error_count}")
                    try:
                        send_to_me(f"🚨 Критическое количество ошибок: {error_count}")
                    except:
                        pass
                    # Сбрасываем счетчик
                    user_data_cache["error_count"] = 0
            except:
                pass
            
            # 7. Сохраняем позиции (если есть изменения)
            try:
                save_positions_to_file()
            except Exception as e:
                print(f"❌ Ошибка сохранения позиций: {e}")
            
            # 8. ПРОВЕРКА БАЛАНСА (для реальной торговли)
            if TRADING_MODE == 'real' and current_time - last_pnl_report > 600:  # Каждые 10 минут
                try:
                    from binance_client import binance_client
                    balance = binance_client.get_balance('USDT')
                    
                    if balance < 50:  # Предупреждение при низком балансе
                        warning_msg = f"⚠️  Низкий баланс: {balance:.2f} USDT"
                        print(warning_msg)
                        if current_time - last_pnl_report > 1800:  # Отправляем раз в 30 минут
                            try:
                                send_to_me(warning_msg)
                            except:
                                pass
                except:
                    pass
            
            await asyncio.sleep(30)  # Проверка каждые 30 секунд (было 60)
            
        except Exception as e:
            print(f"❌ Ошибка в цикле мониторинга: {e}")
            
            # Увеличиваем счетчик ошибок
            try:
                from data_store import user_data_cache
                user_data_cache["error_count"] = user_data_cache.get("error_count", 0) + 1
            except:
                pass
            
            await asyncio.sleep(60)
            
# ========== СИСТЕМНЫЙ ЦИКЛ ==========
async def system_health_loop():
    """Цикл проверки здоровья системы"""
    print("❤️  Запущен цикл проверки здоровья системы")
    
    while True:
        try:
            # Проверяем соединение с Binance
            if TRADING_MODE == 'real' and binance_client:
                try:
                    balance = binance_client.get_balance('USDT')
                    print(f"💰 Баланс Binance: {balance:.2f} USDT")
                except:
                    print("⚠️  Нет связи с Binance")
            
            # Проверяем кеш данных
            cache_size = len(klines_cache)
            print(f"📊 Размер кеша данных: {cache_size} символов")
            
            await asyncio.sleep(300)  # Проверка каждые 5 минут
            
        except Exception as e:
            print(f"❌ Ошибка в цикле здоровья: {e}")
            await asyncio.sleep(60)

# ========== ОСНОВНАЯ АСИНХРОННАЯ ФУНКЦИЯ ==========
async def main_async():
    """Основная асинхронная функция"""
    
    print("=" * 60)
    print("🚀 ИНИЦИАЛИЗАЦИЯ ТОРГОВОГО БОТА")
    print("=" * 60)
    
    # Отправляем сообщение о запуске в Telegram
    try:
        send_startup_message()
    except Exception as e:
        print(f"⚠️  Не удалось отправить стартовое сообщение: {e}")
        send_to_me("🤖 Бот запущен (упрощенное сообщение)")
    
    # Инициализация клиента для реальной торговли
    if TRADING_MODE == 'real':
        print("\n🔐 Инициализация реального торгового режима...")
        
        if binance_client and hasattr(binance_client, 'is_connected'):
            if binance_client.is_connected():
                print("✅ Binance клиент готов")
                
                # Проверяем баланс
                try:
                    balance = binance_client.get_balance('USDT')
                    print(f"💰 Текущий баланс: {balance:.2f} USDT")
                    
                    if balance == 0:
                        warning_msg = "⚠️  ВНИМАНИЕ: Баланс 0.00 USDT\n   Пополните счет для реальной торговли"
                        print(warning_msg)
                        send_to_me(warning_msg)
                except Exception as e:
                    print(f"⚠️  Не удалось проверить баланс: {e}")
            else:
                warning_msg = "⚠️  Binance клиент не подключен\n   Работаем в тестовом режиме даже при TRADING_MODE=real"
                print(warning_msg)
                send_to_me(warning_msg)
    
    # Загружаем сохраненные позиции
    print("\n📂 Загрузка сохраненных позиций...")
    load_positions_from_file()
    
    # Выводим информацию о настройках
    print(f"\n📊 КОНФИГУРАЦИЯ БОТА:")
    print(f"   • Режим: {TRADING_MODE.upper()}")
    print(f"   • Таймфрейм: {TIMEFRAME}")
    print(f"   • Плечо: {LEVERAGE}x")
    print(f"   • Риск на сделку: {RISK_FRACTION*100}%")
    print(f"   • Начальный капитал: {INITIAL_CASH} USDT")
    print(f"   • Стратегии: BBRSI={'ВКЛ' if USE_BBRSI else 'ВЫКЛ'}, BREAKOUT={'ВКЛ' if USE_BREAKOUT else 'ВЫКЛ'}")
    
    if TRADING_MODE == 'real':
        warning_msg = """
🚨 ВНИМАНИЕ: РЕАЛЬНАЯ ТОРГОВЛЯ С РЕАЛЬНЫМИ ДЕНЬГАМИ!
   Убедитесь, что понимаете все риски!
   Начните с маленьких сумм для тестирования!
"""
        print(warning_msg)
        send_to_me(warning_msg)
    
    # Получаем ликвидные тикеры
    print(f"\n🔍 Поиск ликвидных тикеров...")
    symbols = await get_liquid_tickers(
        top_n=TOP_N_TICKERS,
        min_price=MIN_PRICE,
        min_volume=MIN_VOLUME,
        max_spread_percent=MAX_SPREAD_PERCENT
    )
    
    if not symbols:
        print("❌ Не получили ликвидные тикеры, используем BTCUSDT")
        symbols = ["BTCUSDT"]
    
    print(f"📈 Найдено ликвидных тикеров: {len(symbols)}")
    print(f"📋 Символы: {symbols[:10]}{'...' if len(symbols) > 10 else ''}")
    
    # Загрузка исторических свечей
    print("\n📥 Загружаем исторические свечи...")
    loaded_symbols = []
    
    for s in symbols:
        df = await fetch_historical_klines(s, interval=TIMEFRAME, limit=500)
        if not df.empty:
            klines_cache[s] = df
            loaded_symbols.append(s)
            print(f"   ✅ {s}: {len(df)} свечей")
        else:
            print(f"   ❌ {s}: не удалось загрузить")
    
    if not loaded_symbols:
        error_msg = "❌ Не удалось загрузить данные ни по одному символу!"
        print(error_msg)
        send_to_me(error_msg)
        return
    
    symbols = loaded_symbols
    
    # Запуск WebSocket
    print(f"\n📡 Запуск WebSocket для {len(symbols)} символов...")
    await start_websockets(symbols, interval=TIMEFRAME)
    
    # Оптимизация и выбор топ-5
    print("\n🧮 Оптимизация и выбор топ-5 символов...")
    top5 = optimize_and_select_top_ws(symbols)
    top_symbols = [s for s, _ in top5] if top5 else symbols[:5]
    
    print(f"🎯 Топ-5 символов для торговли: {top_symbols}")
    
    # Отправляем информацию в Telegram
    telegram_msg = f"""
🎯 Выбраны топ-5 символов для торговли:
{', '.join(top_symbols)}

📊 Режим: {TRADING_MODE.upper()}
⏰ Таймфрейм: {TIMEFRAME}
⚖️  Плечо: {LEVERAGE}x
🎯 Риск на сделку: {RISK_FRACTION*100}%
"""
    send_to_me(telegram_msg)
    
    # Запуск торговых циклов
    print(f"\n🔄 Запуск торговых циклов...")
    trade_tasks = [asyncio.create_task(trade_symbol_loop(sym)) for sym in top_symbols]
    
    # Запуск цикла мониторинга
    print("👁️  Запуск цикла мониторинга...")
    monitor_task = asyncio.create_task(monitoring_loop())
    
    # Запуск цикла здоровья системы
    print("❤️  Запуск цикла проверки здоровья...")
    health_task = asyncio.create_task(system_health_loop())

    print("🎯 Запуск цикла мониторинга TP/SL...")
    tp_sl_task = asyncio.create_task(tp_sl_monitor_loop())
    
    print(f"\n✅ Бот успешно запущен! Торговля: {'АКТИВНА' if not get_trading_status()['paused'] else 'НА ПАУЗЕ'}")
    print("   Используйте Telegram для управления ботом")
    
    # Ожидание завершения всех задач
    await asyncio.gather(*trade_tasks, monitor_task, health_task, tp_sl_task)

# ========== ЗАПУСК ПАНЕЛИ УПРАВЛЕНИЯ ==========
def start_control_panel():
    """Запуск панели управления Telegram"""
    print("🎮 Запуск Telegram панели управления...")
    
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_MY_CHAT_ID:
        print("⚠️  Telegram токен или chat_id не установлены")
        print("   Установите TELEGRAM_BOT_TOKEN и TELEGRAM_MY_CHAT_ID в config.py")
        print("   Бот будет работать без Telegram управления")
        return
    
    # Запускаем Telegram бот
    start_telegram_manager()

# ========== ТОЧКА ВХОДА ==========
if __name__ == "__main__":
    # Запуск панели управления в отдельном потоке
    start_control_panel()
    
    # Настройки перезапуска
    RESTART_DELAY = 10  # секунд
    restart_count = 0
    max_restarts = 10
    
    while restart_count < max_restarts:
        try:
            print(f"\n{'='*60}")
            print(f"🔄 ЗАПУСК #{restart_count + 1}")
            print(f"{'='*60}")
            
            # Запуск основной асинхронной функции
            asyncio.run(main_async())
            
        except KeyboardInterrupt:
            print("\n\n⏹️  Остановлено пользователем")
            print("Остановлено пользователем")
            sys.exit(0)
            
        except Exception as e:
            restart_count += 1
            error_msg = f"❌ Критическая ошибка #{restart_count}! Перезапуск через {RESTART_DELAY} секунд"
            print(f"\n{error_msg}")
            print(f"Ошибка: {e}")
            
            # Отправляем уведомление в Telegram
            try:
                send_to_me(f"⚠️  {error_msg}\nОшибка: {str(e)[:100]}...")
            except:
                print("⚠️  Не удалось отправить ошибку в Telegram")
            
            traceback.print_exc()
            
            if restart_count >= max_restarts:
                fatal_msg = f"❌ Достигнут лимит перезапусков ({max_restarts})"
                print(fatal_msg)
                print(f"Достигнут лимит перезапусков ({max_restarts})")
                sys.exit(1)
            

            time.sleep(RESTART_DELAY)
