# pos_manager.py
from data_store import klines_cache, user_data_cache, sync_real_positions
from config import LEVERAGE, INITIAL_CASH, RISK_FRACTION, TRADING_MODE
from utils import _quantize_to_step
from logger import log_position
import time
from typing import Dict, List, Optional, Any
# Импортируем глобальный клиент
from binance_client import binance_client as global_client
def refresh_positions_cache():
    """Обновление кэша позиций с Binance"""
    try:
        if TRADING_MODE != 'real' or not global_client:
            return
        
        from data_store import user_data_cache
        
        # Получаем позиции с Binance
        positions = global_client.get_positions()
        positions_dict = user_data_cache.get("positions", {})
        
        # Очищаем старые реальные позиции
        symbols_to_remove = []
        for symbol, pos in positions_dict.items():
            if pos.get('source') == 'binance_real':
                symbols_to_remove.append(symbol)
        
        for symbol in symbols_to_remove:
            positions_dict.pop(symbol, None)
        
        # Добавляем актуальные позиции
        for pos in positions:
            position_amt = float(pos.get('positionAmt', 0))
            
            if abs(position_amt) > 0:
                symbol = pos.get('symbol')
                if not symbol.endswith('USDT'):
                    symbol = symbol + 'USDT'
                
                # Получаем текущую цену
                try:
                    from data_store import klines_cache
                    df = klines_cache.get(symbol)
                    current_price = float(df["Close"].iloc[-1]) if df is not None else float(pos.get('markPrice', 0))
                except:
                    current_price = float(pos.get('markPrice', 0))
                
                positions_dict[symbol] = {
                    "symbol": symbol,
                    "side": "BUY" if position_amt > 0 else "SELL",
                    "qty": abs(position_amt),
                    "entry": float(pos.get('entryPrice', 0)),
                    "current_price": current_price,
                    "source": "binance_real",
                    "status": "OPEN",
                    "timestamp": time.time()
                }
        
        user_data_cache["positions"] = positions_dict
        
        if positions_dict:
            print(f"✅ Кэш позиций обновлен: {len(positions_dict)} позиций")
        
    except Exception as e:
        print(f"❌ Ошибка обновления кэша позиций: {e}")

# Используем глобальный клиент
binance_client = global_client
def check_order_status(order_id: str, symbol: str) -> Dict:
    """Проверка статуса ордера"""
    try:
        if TRADING_MODE == 'real' and global_client:
            order = global_client.get_order(symbol=symbol, orderId=order_id)
            
            if order:
                status = order.get('status')
                executed_qty = float(order.get('executedQty', 0))
                avg_price = float(order.get('avgPrice', 0))
                
                print(f"📊 Статус ордера {order_id}: {status}")
                print(f"   Исполнено: {executed_qty}")
                print(f"   Средняя цена: {avg_price}")
                
                if status == 'FILLED' and executed_qty > 0:
                    print(f"✅ Ордер {order_id} полностью исполнен")
                    return {
                        'status': 'FILLED',
                        'executed_qty': executed_qty,
                        'avg_price': avg_price,
                        'order': order
                    }
                elif status == 'PARTIALLY_FILLED':
                    print(f"⚠️  Ордер {order_id} частично исполнен: {executed_qty}")
                    return {
                        'status': 'PARTIALLY_FILLED',
                        'executed_qty': executed_qty,
                        'avg_price': avg_price
                    }
                elif status in ['NEW', 'PENDING']:
                    print(f"⏳ Ордер {order_id} ожидает исполнения")
                    return {'status': 'PENDING'}
                else:
                    print(f"❌ Ордер {order_id} в статусе: {status}")
                    return {'status': status}
        
        return {'status': 'UNKNOWN'}
        
    except Exception as e:
        print(f"❌ Ошибка проверки ордера {order_id}: {e}")
        return {'status': 'ERROR', 'error': str(e)}
    
def init_binance_client():
    """Инициализация клиента для реальной торговли"""
    print(f"DEBUG: init_binance_client вызван, TRADING_MODE={TRADING_MODE}")
    
    if TRADING_MODE == 'real':
        # Проверяем, инициализирован ли глобальный клиент
        if global_client and global_client.is_connected():
            print(f"✅ Используем глобальный Binance клиент")
            return True
        else:
            print(f"❌ Глобальный клиент не подключен")
            return False
    
    # Для dryrun всегда возвращаем True
    return TRADING_MODE == 'dryrun'

def get_open_position(symbol: str):
    """Получение конкретной позиции"""
    try:
        if TRADING_MODE == 'real':
            # Для реальной торговли ищем в позициях Binance
            if not global_client or not global_client.is_connected():
                print(f"❌ Глобальный клиент не подключен для {symbol}")
                return None
            
            try:
                positions = global_client.get_positions()
            

                for pos in positions:
                    # Приводим символы к одному формату (USDT может быть с суффиксом или без)
                    pos_symbol = pos.get('symbol')
                    search_symbol = symbol
                    
                    # Нормализуем символы
                    if not pos_symbol.endswith('USDT') and search_symbol.endswith('USDT'):
                        search_symbol = search_symbol.replace('USDT', '')
                    elif pos_symbol.endswith('USDT') and not search_symbol.endswith('USDT'):
                        search_symbol = search_symbol + 'USDT'
                    
                    if pos_symbol == search_symbol:
                        print(f"✅ Найдена реальная позиция: {symbol} {pos.get('side')} {pos.get('qty')}")
                        return pos
                
                # Если не нашли в реальных позициях, проверяем кэш
                from data_store import user_data_cache
                cached_pos = user_data_cache.get("positions", {}).get(symbol)
                if cached_pos and cached_pos.get('source') == 'binance_real':
                    print(f"⚠️  Позиция {symbol} есть в кэше, но нет на Binance. Удаляю из кэша.")
                    # Удаляем из кэша
                    positions_dict = user_data_cache.get("positions", {})
                    positions_dict.pop(symbol, None)
                    user_data_cache["positions"] = positions_dict
                
                    return None
            except Exception as e:
                print(f"❌ Ошибка получения позиций: {e}")
                return None
        else:
                    # Для dryrun
                    return user_data_cache.get("positions", {}).get(symbol)

    except Exception as e:
        print(f"❌ Ошибка в get_open_position для {symbol}: {e}")
        return None
    
def calculate_qty(price: float, equity: float = None, risk_fraction: float = RISK_FRACTION) -> float:
    """Расчет количества для сделки"""
    if equity is None:
        equity = INITIAL_CASH
    
    # Для реальной торговли получаем реальный баланс
    if TRADING_MODE == 'real' and global_client and global_client.is_connected():
        try:
            equity = global_client.get_balance('USDT')
            print(f"💰 Используем реальный баланс: {equity:.2f} USDT")
        except Exception as e:
            print(f"⚠️  Не удалось получить реальный баланс: {e}")
            print(f"   Используем виртуальный баланс: {INITIAL_CASH} USDT")
            equity = INITIAL_CASH
    
    # Базовая формула расчета
    qty = max(1e-8, (equity * risk_fraction * LEVERAGE) / price)

    if TRADING_MODE == 'real':
        min_notional = 20.0  # Минимальный номинал Binance
        initial_notional = price * qty
        
        if initial_notional < min_notional:
            print(f"⚠️  Корректирую количество под минимальный номинал {min_notional} USDT")
            print(f"   Было: {initial_notional:.2f} USDT")
            
            # Увеличиваем с запасом 5% чтобы хватило после округления
            qty = (min_notional * 1.05) / price
            new_notional = price * qty
            
            print(f"   Стало: {new_notional:.2f} USDT (+5% запас)")

            # Пересчитываем с минимальным номиналом
            required_risk = min_notional / LEVERAGE
            adjusted_risk_fraction = required_risk / equity
            
            if adjusted_risk_fraction > 0.5:  # Не более 50% риска
                print(f"⚠️  ВНИМАНИЕ: Для минимального номинала нужен риск {adjusted_risk_fraction*100:.1f}%")
                print(f"   Это больше максимального безопасного значения")
                print(f"   Рекомендую выбрать другой символ с меньшей ценой")
                return qty  # Возвращаем исходное, open_position обработает ошибку
            
            # Пересчитываем с минимальным номиналом
            qty = min_notional / price
            print(f"⚠️  Корректирую количество под минимальный номинал {min_notional} USDT")
            print(f"   Было: {initial_notional:.2f} USDT")
            print(f"   Стало: {price * qty:.2f} USDT")
            print(f"   Новый риск: {adjusted_risk_fraction*100:.1f}%")
    
    return qty

def open_position(symbol: str, side: str):
    """Автоматическое открытие позиции - ВСЁ берется с Binance"""
    print(f"🤖 АВТОМАТИЧЕСКОЕ ОТКРЫТИЕ: {symbol} {side}")
    
    # 1. Проверяем режим
    if TRADING_MODE != 'real':
        print(f"❌ Только для реальной торговли!")
        return None
    
    # 2. Проверяем клиент
    if not global_client or not global_client.is_connected():
        print(f"❌ Клиент Binance не подключен")
        return None
    
    try:
        print(f"🔍 Получаю данные с Binance...")
        
        # 3. Получаем информацию о символе ПРАВИЛЬНО
        # Вместо get_symbol_info используем futures_exchange_info
        try:
            exchange_info = global_client.client.futures_exchange_info()
            symbol_info = None
            
            # Ищем нужный символ в списке
            for sym_info in exchange_info['symbols']:
                if sym_info['symbol'] == symbol:
                    symbol_info = sym_info
                    break
            
            if not symbol_info:
                print(f"❌ Символ {symbol} не найден на Binance")
                return None
                
        except Exception as e:
            print(f"❌ Ошибка получения информации о символе: {e}")
            return None
        
        # 4. Извлекаем фильтры ПРАВИЛЬНО
        step_size = 0.001
        min_qty = 0.001
        
        if 'filters' in symbol_info:
            for filt in symbol_info['filters']:
                if filt.get('filterType') == 'LOT_SIZE':
                    step_size = float(filt.get('stepSize', 0.001))
                    min_qty = float(filt.get('minQty', 0.001))
                    print(f"✅ Параметры с Binance: step={step_size}, min={min_qty}")
                    break
        
        # 5. Получаем текущую цену
        current_price = global_client.get_ticker_price(symbol)
        print(f"💰 Цена с Binance: {current_price}")
        
        # 6. Получаем баланс
        balance = global_client.get_balance('USDT')
        print(f"🏦 Баланс с Binance: {balance:.2f} USDT")
        
        if balance < 10:
            print(f"❌ Недостаточно баланса: {balance:.2f} USDT")
            return None
        
        # 7. Проверяем, нет ли уже открытой позиции
        positions = global_client.get_positions()
        for pos in positions:
            if pos.get('symbol') == symbol.replace('USDT', ''):
                position_amt = float(pos.get('positionAmt', 0))
                if abs(position_amt) > 0:
                    print(f"⚠️  Позиция {symbol} уже открыта на Binance!")
                    print(f"   Количество: {abs(position_amt)}")
                    print(f"   Сторона: {'BUY' if position_amt > 0 else 'SELL'}")
                    return None
        
        # 8. Автоматический расчет количества
        MIN_NOTIONAL = 5.0
        
        # Рассчитываем минимальное количество для 5 USDT
        min_qty_for_5usdt = MIN_NOTIONAL / current_price
        
        # Округляем до step_size ВВЕРХ
        if step_size > 0:
            min_qty_for_5usdt = ((min_qty_for_5usdt // step_size) + 1) * step_size
        
        # Берем большее из: минимального количества символа и минимального для 5 USDT
        quantity = max(min_qty, min_qty_for_5usdt)
        
        # Проверяем номинал
        notional = quantity * current_price
        print(f"📊 Рассчитано: qty={quantity}, notional={notional:.2f} USDT")
        
        # Если все еще меньше 5 USDT, добавляем еще один шаг
        if notional < MIN_NOTIONAL:
            print(f"⚠️  Номинал {notional:.2f} < {MIN_NOTIONAL}, увеличиваю...")
            quantity += step_size
            notional = quantity * current_price
        
        # Проверяем, не превышает ли 20% от баланса
        if notional > balance * 0.2:
            print(f"⚠️  Превышает 20% баланса, уменьшаю...")
            # Максимум 20% от баланса
            max_qty = (balance * 0.2) / current_price
            # Округляем ВНИЗ до step_size
            if step_size > 0:
                max_qty = (max_qty // step_size) * step_size
            quantity = max(min_qty, max_qty)
            notional = quantity * current_price
        
        # Финальная проверка
        if notional < MIN_NOTIONAL:
            print(f"❌ Не удалось достичь минимального номинала {MIN_NOTIONAL} USDT")
            return None
        
        print(f"📊 ФИНАЛЬНЫЕ ПАРАМЕТРЫ:")
        print(f"   Количество: {quantity}")
        print(f"   Цена: {current_price}")
        print(f"   Номинал: {notional:.2f} USDT")
        print(f"   % от баланса: {(notional/balance*100):.1f}%")
        
        # 9. Определяем точность для форматирования
        # Смотрим сколько знаков после запятой в step_size
        step_str = str(step_size)
        if '.' in step_str:
            # Убираем лишние нули в конце
            precision = len(step_str.rstrip('0').split('.')[1])
        else:
            precision = 0
        
        # Форматируем количество
        if precision == 0:
            qty_str = str(int(quantity))
        else:
            qty_str = format(quantity, f'.{precision}f')
        
        print(f"🔢 Количество для API ({precision} знаков): {qty_str}")
        
        # 10. Открываем ордер на Binance
        print(f"🚀 Открываю ордер на Binance...")
        
        order = global_client.place_order(
            side=side.upper(),
            quantity=qty_str,
            symbol=symbol,
            order_type='MARKET'
        )
        
        if not order or 'orderId' not in order:
            print(f"❌ Ошибка размещения ордера")
            return None
        
        print(f"✅✅✅ ОРДЕР РАЗМЕЩЕН!")
        print(f"📋 ID: {order['orderId']}")
        
        # 11. Ждем и проверяем позицию
        time.sleep(3)
        
        # Получаем обновленные позиции
        positions = global_client.get_positions()
        opened_position = None
        
        for pos in positions:
            if pos.get('symbol') == symbol:
                position_amt = float(pos.get('positionAmt', 0))
                if abs(position_amt) > 0:
                    opened_position = pos
                    break
        
        if opened_position:
            print(f"✅ ПОЗИЦИЯ ОТКРЫТА НА BINANCE!")
            
            # Создаем данные позиции
            pos_data = {
                "symbol": symbol,
                "side": 'BUY' if position_amt > 0 else 'SELL',
                "qty": abs(position_amt),
                "entry": float(opened_position.get('entryPrice', current_price)),
                "current_price": float(opened_position.get('markPrice', current_price)),
                "unrealized_pnl": float(opened_position.get('unRealizedProfit', 0)),
                "leverage": float(opened_position.get('leverage', LEVERAGE)),
                "status": "OPEN",
                "source": "binance_real",
                "order_id": order['orderId'],
                "timestamp": time.time()
            }
            
            print(f"📊 Данные с Binance:")
            print(f"   Количество: {pos_data['qty']}")
            print(f"   Цена входа: {pos_data['entry']}")
            print(f"   Текущая цена: {pos_data['current_price']}")
            print(f"   PnL: {pos_data['unrealized_pnl']:+.2f}")
            
            # Сохраняем в кэш
            from data_store import user_data_cache
            if "positions" not in user_data_cache:
                user_data_cache["positions"] = {}
            user_data_cache["positions"][symbol] = pos_data
            try:
                from telegram_bot import send_trade_opened
                
                # Формируем данные для Telegram (без current_price, так как позиция еще не открыта)
                trade_data = {
                    'symbol': symbol,
                    'side': side.upper(),
                    'qty': quantity,
                    'entry_price': current_price,
                    'current_price': current_price,  # дублируем entry_price
                    'order_id': order['orderId'],
                    'leverage': LEVERAGE,
                    'notional': quantity * current_price,
                    'mode': 'REAL',
                    'status': 'PENDING'
                }
                
                send_trade_opened(trade_data)
                print(f"✅ Уведомление об ордере отправлено в Telegram")
                
            except Exception as tg_error:
                print(f"⚠️  Ошибка отправки в Telegram: {tg_error}")
                import traceback
                traceback.print_exc()

            return pos_data
        else:
            print(f"⚠️  Ордер размещен, но позиция еще не появилась")
            
            # Создаем временные данные
            pos_data = {
                "symbol": symbol,
                "side": side.upper(),
                "qty": quantity,
                "entry": current_price,
                "status": "PENDING",
                "source": "binance_real_pending",
                "order_id": order['orderId'],
                "timestamp": time.time()
            }
            
            # Сохраняем в кэш
            from data_store import user_data_cache
            if "positions" not in user_data_cache:
                user_data_cache["positions"] = {}
            user_data_cache["positions"][symbol] = pos_data
            
            return pos_data
            
    except Exception as e:
        print(f"❌ ОШИБКА: {e}")
        import traceback
        traceback.print_exc()
        return None
                
def check_position(symbol: str, price: float):
    """Проверка позиции (только для dryrun)"""
    if TRADING_MODE == 'real':
        # Для реальной торговли управление SL/TP на стороне Binance
        return
    
    # Только для dryrun
    pos = user_data_cache.get("positions", {}).get(symbol)
    if not pos or pos["status"] != "OPEN":
        return

    side = pos["side"]
    sl = pos["sl"]
    tp = pos["tp"]
    trail = pos["trail_percent"]

    reason = None

    # --- трейлинг стоп ---
    if side == "BUY":
        new_sl = price * (1 - trail / 100)
        if new_sl > sl:  # подтягиваем стоп
            pos["sl"] = new_sl
            print(f"[TRAIL] {symbol} stop moved to {new_sl:.2f}")
    else:  # SELL
        new_sl = price * (1 + trail / 100)
        if new_sl < sl:
            pos["sl"] = new_sl
            print(f"[TRAIL] {symbol} stop moved to {new_sl:.2f}")

    # --- TP / SL ---
    if side == "BUY":
        if tp is not None and price >= tp:
            reason = "TP"
        elif sl is not None and price <= sl:
            reason = "SL"
    else:  # SELL
        if tp is not None and price <= tp:
            reason = "TP"
        elif sl is not None and price >= sl:
            reason = "SL"

    if reason:
        pos["status"] = "CLOSED"
        print(f"[DRY RUN] CLOSE {symbol} {side} @ {price} by {reason}")

def close_position(symbol: str, exit_price: float, exit_reason=None):
    """Закрытие позиции"""
    # Инициализируем клиент если нужно
    if TRADING_MODE == 'real':
        if not init_binance_client():
            print(f"❌ Не удалось инициализировать клиент для закрытия позиции")
            return False
    
    if TRADING_MODE == 'dryrun':
        # DRY RUN - виртуальное закрытие
        pos = get_open_position(symbol)
        if not pos:
            return False

        qty = pos["qty"]
        side = pos["side"]
        entry = pos["entry"]

        # Расчёт PnL
        if side == "BUY":  # Лонг
            pnl = (exit_price - entry) * qty
        elif side in ("SELL", "SHORT"):  # Шорт
            pnl = (entry - exit_price) * qty
        else:
            pnl = 0

        # Обновляем exit_reason и зануляем TP/SL
        pos["exit_reason"] = exit_reason or "MANUAL"
        pos["tp"] = None
        pos["sl"] = None

        # Логируем закрытие
        log_position(
            action="CLOSE",
            symbol=symbol,
            side=side,
            price=exit_price,
            qty=qty,
            pnl=pnl,
            exit_reason=pos["exit_reason"]
        )

        # Удаляем из кэша
        user_data_cache["positions"].pop(symbol, None)
        return True
    else:
        # РЕАЛЬНАЯ ТОРГОВЛЯ - закрытие на Binance
        try:
            # Получаем текущую позицию
            positions = get_open_positions()
            pos = None
            for p in positions:
                if p.get('symbol') == symbol:
                    pos = p
                    break
            
            if not pos:
                print(f"❌ Позиция {symbol} не найдена для закрытия")
                return False
            
            # Определяем сторону для закрытия (противоположная открытой)
            close_side = "SELL" if pos["side"] == "BUY" else "BUY"
            qty = pos["qty"]
            
            print(f"🚨 РЕАЛЬНОЕ ЗАКРЫТИЕ: {close_side} {qty:.4f} {symbol}")
            
            # Размещаем ордер на закрытие
            order = global_client.place_order(
                side=close_side,
                quantity=qty,
                symbol=symbol,
                order_type='MARKET'
            )
            
            # Логируем закрытие
            pnl = pos.get('unrealized_pnl', 0)
            log_position(
                action="CLOSE",
                symbol=symbol,
                side=pos["side"],
                price=exit_price,
                qty=qty,
                pnl=pnl,
                exit_reason=exit_reason or "REAL_TRADE_CLOSE"
            )
            
            print(f"✅ Реальная позиция закрыта: {symbol}")
            return True
            
        except Exception as e:
            print(f"❌ Ошибка закрытия реальной позиции: {e}")
            return False