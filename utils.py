import pandas as pd
import numpy as np
from decimal import Decimal, ROUND_DOWN, ROUND_UP
from config import TRADING_MODE  # добавляем импорт режима

def ema200(arr):
    """Экспоненциальная скользящая средняя 200"""
    s = pd.Series(arr)
    return s.ewm(span=200, adjust=False).mean().to_numpy()

def bol_h(arr, period=40, dev=2):
    """Верхняя полоса Боллинджера"""
    s = pd.Series(arr) if not isinstance(arr, pd.Series) else arr
    bb = ta.volatility.BollingerBands(s, window=period, window_dev=dev)
    return bb.bollinger_hband()

def bol_l(arr, period=40, dev=2):
    """Нижняя полоса Боллинджера"""
    s = pd.Series(arr) if not isinstance(arr, pd.Series) else arr
    bb = ta.volatility.BollingerBands(s, window=period, window_dev=dev)
    return bb.bollinger_lband()

def rsi(arr, period=14):
    """Индекс относительной силы (RSI)"""
    s = pd.Series(arr) if not isinstance(arr, pd.Series) else arr
    return ta.momentum.RSIIndicator(s, window=period).rsi()

def atr(arr_high, arr_low, arr_close, period=14):
    """Average True Range (ATR)"""
    df = pd.DataFrame({"high": arr_high, "low": arr_low, "close": arr_close})
    return ta.volatility.AverageTrueRange(df["high"], df["low"], df["close"], window=period).average_true_range().to_numpy()

def _quantize_to_step(value: float, step: float) -> float:
    """Округление количества до шага торговли"""
    d_val = Decimal(str(value))
    d_step = Decimal(str(step))
    steps = (d_val / d_step).to_integral_value(rounding=ROUND_DOWN)
    return float(steps * d_step)

def _quantize_to_step_up(value: float, step: float) -> float:
    """Округление вверх до шага торговли"""
    d_val = Decimal(str(value))
    d_step = Decimal(str(step))
    steps = (d_val / d_step).to_integral_value(rounding=ROUND_UP)
    return float(steps * d_step)

def get_symbol_step_size(symbol: str):
    """Получение шага размера для символа (для реальной торговли)"""
    if TRADING_MODE == 'dryrun':
        # Для тестового режима используем стандартный шаг
        return 0.001
    
    try:
        # Пытаемся получить информацию о символе из Binance
        from binance_client import BinanceClient
        client = BinanceClient()
        symbol_info = client.get_symbol_info(symbol)
        
        if symbol_info and 'stepSize' in symbol_info:
            return float(symbol_info['stepSize'])
    except Exception as e:
        print(f"⚠️  Не удалось получить шаг для {symbol}: {e}")
    
    # Возвращаем значение по умолчанию
    return 0.001

def calculate_position_size(price: float, risk_amount: float, stop_loss_pct: float = 0.02):
    """
    Расчет размера позиции на основе риска
    Args:
        price: текущая цена
        risk_amount: сумма риска в USDT
        stop_loss_pct: процент стоп-лосса (например, 0.02 для 2%)
    Returns:
        количество для торговли
    """
    risk_per_coin = price * stop_loss_pct
    if risk_per_coin <= 0:
        return 0.0
    
    qty = risk_amount / risk_per_coin
    return qty

def calculate_risk_based_sl_tp(entry_price: float, side: str, atr_value: float = None, 
                              risk_reward_ratio: float = 1.5):
    """
    Расчет SL и TP на основе ATR
    Args:
        entry_price: цена входа
        side: 'BUY' или 'SELL'
        atr_value: значение ATR
        risk_reward_ratio: соотношение риск/прибыль
    Returns:
        (stop_loss, take_profit)
    """
    if atr_value is None:
        # По умолчанию 2% от цены
        atr_multiplier = 0.02
    else:
        # Используем 1.5 ATR
        atr_multiplier = 1.5 * atr_value / entry_price
    
    if side.upper() == "BUY":
        stop_loss = entry_price * (1 - atr_multiplier)
        take_profit = entry_price * (1 + atr_multiplier * risk_reward_ratio)
    else:  # SELL
        stop_loss = entry_price * (1 + atr_multiplier)
        take_profit = entry_price * (1 - atr_multiplier * risk_reward_ratio)
    
    return stop_loss, take_profit

def clean_klines(df: pd.DataFrame) -> pd.DataFrame:
    """Очистка данных свечей"""
    if df is None or df.empty:
        return pd.DataFrame()
    
    df = df.dropna(subset=["Open", "High", "Low", "Close", "Volume"])
    df[["Open", "High", "Low", "Close", "Volume"]] = df[["Open", "High", "Low", "Close", "Volume"]].astype(float)
    df = df[(df["Open"] > 0) & (df["High"] > 0) & (df["Low"] > 0) & (df["Close"] > 0)]
    
    # Проверка на аномалии (для реальной торговли)
    if TRADING_MODE == 'real' and len(df) > 10:
        # Проверяем на пропущенные свечи
        time_diff = df.index.to_series().diff().dt.total_seconds()
        if (time_diff > 600).any():  # больше 10 минут
            print(f"⚠️  Обнаружены пропущенные свечи в данных")
        
        # Проверяем на аномальные значения
        price_change = df["Close"].pct_change().abs()
        if (price_change > 0.1).any():  # больше 10% изменения
            print(f"⚠️  Обнаружены аномальные изменения цены")
    
    return df

def validate_trade_params(symbol: str, price: float, qty: float) -> bool:
    """Валидация параметров сделки (для реальной торговли)"""
    if TRADING_MODE == 'dryrun':
        return True
    
    try:
        from binance_client import BinanceClient
        client = BinanceClient()
        symbol_info = client.get_symbol_info(symbol)
        
        if not symbol_info:
            print(f"❌ Не удалось получить информацию о символе {symbol}")
            return False
        
        # Проверка минимального количества
        min_qty = float(symbol_info.get('minQty', 0))
        if qty < min_qty:
            print(f"❌ Количество {qty} меньше минимального {min_qty} для {symbol}")
            return False
        
        # Проверка шага количества
        step_size = float(symbol_info.get('stepSize', 0.001))
        if not _is_valid_step(qty, step_size):
            print(f"❌ Количество {qty} не соответствует шагу {step_size} для {symbol}")
            return False
        
        # Проверка минимальной стоимости ордера
        min_notional = float(symbol_info.get('minNotional', 10))
        order_value = price * qty
        if order_value < min_notional:
            print(f"❌ Стоимость ордера {order_value} меньше минимальной {min_notional} для {symbol}")
            return False
        
        return True
        
    except Exception as e:
        print(f"❌ Ошибка валидации параметров сделки: {e}")
        return False

def _is_valid_step(value: float, step: float) -> bool:
    """Проверка соответствия значения шагу"""
    if step <= 0:
        return True
    
    d_val = Decimal(str(value))
    d_step = Decimal(str(step))
    remainder = d_val % d_step
    
    # Допускаем небольшую погрешность из-за float
    return float(remainder) < 1e-10

def calculate_commission(qty: float, price: float, is_maker: bool = False) -> float:
    """Расчет комиссии за сделку"""
    # Комиссия Binance Futures: 0.02% для тейкера, 0.01% для мейкера
    commission_rate = 0.0002 if not is_maker else 0.0001
    
    if TRADING_MODE == 'real':
        # Для реальной торговли может быть скидка за использование BNB
        # Здесь можно добавить логику проверки скидки
        pass
    
    commission = qty * price * commission_rate
    return commission

def format_price(price: float, symbol: str = "USDT") -> str:
    """Форматирование цены для отображения"""
    if price >= 1000:
        return f"{price:,.0f} {symbol}"
    elif price >= 1:
        return f"{price:,.2f} {symbol}"
    else:

        return f"{price:.6f} {symbol}"
