import pandas as pd
from backtesting import Strategy
from utils import ema200, bol_h, bol_l, rsi
from pos_manager import calculate_qty
from config import RISK_FRACTION, TRADING_MODE, LEVERAGE, INITIAL_CASH
from binance_client import BinanceClient

# Клиент для реальной торговли
binance_client = None

def init_binance_client():
    """Инициализация клиента для реальной торговли"""
    global binance_client
    if TRADING_MODE == 'real' and binance_client is None:
        try:
            binance_client = BinanceClient()
            return True
        except Exception as e:
            print(f"❌ Ошибка инициализации Binance клиента: {e}")
            return False
    return TRADING_MODE == 'dryrun'

def calculate_qty_for_backtest(price: float, equity: float = None, risk_fraction: float = RISK_FRACTION) -> float:
    """Расчет количества для бэктеста"""
    if equity is None:
        equity = INITIAL_CASH
    
    qty = max(1e-8, (equity * risk_fraction * LEVERAGE) / price)
    return qty

def calculate_qty_for_realtime(price: float, symbol: str, risk_fraction: float = RISK_FRACTION) -> float:
    """Расчет количества для реальной торговли"""
    if TRADING_MODE == 'dryrun':
        return calculate_qty_for_backtest(price, INITIAL_CASH, risk_fraction)
    
    if not init_binance_client():
        print(f"❌ Не удалось инициализировать клиент для расчета количества")
        return 0.0
    
    try:
        equity = binance_client.get_balance('USDT')
        qty = max(1e-8, (equity * risk_fraction * LEVERAGE) / price)
        
        symbol_info = binance_client.get_symbol_info(symbol)
        if symbol_info:
            min_qty = float(symbol_info.get('minQty', 0.001))
            max_qty = float(symbol_info.get('maxQty', 1000))
            qty = max(min_qty, min(qty, max_qty))
        
        return qty
    except Exception as e:
        print(f"❌ Ошибка расчета количества для реальной торговли: {e}")
        return 0.0

def adjust_size_for_backtest(size):
    """Корректировка размера для бэктеста"""
    return size if size < 1 else max(1, int(size))

class BBRSI_EMA_Strategy(Strategy):
    bol_period = 40
    bol_dev = 2
    rsi_period = 14

    def init(self):
        self.bol_h = self.I(bol_h, self.data.Close, self.bol_period, self.bol_dev)
        self.bol_l = self.I(bol_l, self.data.Close, self.bol_period, self.bol_dev)
        self.rsi = self.I(rsi, self.data.Close, self.rsi_period)
        self.ema200 = self.I(ema200, self.data.Close)

    def next(self):
        price = float(self.data.Close[-1])
        size = adjust_size_for_backtest(calculate_qty_for_backtest(price, self.equity, RISK_FRACTION))
        
        if price > self.ema200[-1]:
            if self.data.Close[-3] > self.bol_l[-3] and self.data.Close[-2] < self.bol_l[-2] and self.rsi[-1] < 30:
                if not self.position:
                    self.buy(size=size)
                elif self.position.is_short:
                    self.position.close()
                    self.buy(size=size)
        elif price < self.ema200[-1]:
            if self.data.Close[-3] < self.bol_h[-3] and self.data.Close[-2] > self.bol_h[-2] and self.rsi[-1] > 70:
                if not self.position:
                    self.sell(size=size)
                elif self.position.is_long:
                    self.position.close()
                    self.sell(size=size)

class Breakout_Strategy(Strategy):
    period = 20

    def init(self):
        self.highest = self.I(lambda x: pd.Series(x).rolling(self.period).max(), self.data.High)
        self.lowest = self.I(lambda x: pd.Series(x).rolling(self.period).min(), self.data.Low)

    def next(self):
        price = float(self.data.Close[-1])
        size = adjust_size_for_backtest(calculate_qty_for_backtest(price, self.equity, RISK_FRACTION))
        
        if price > self.highest[-2]:
            if not self.position or self.position.is_short:
                if self.position:
                    self.position.close()
                self.buy(size=size)
        elif price < self.lowest[-2]:
            if not self.position or self.position.is_long:
                if self.position:
                    self.position.close()
                self.sell(size=size)

# Функции для реальной торговли - ИСПРАВЛЕННЫЕ
def safe_get_value(data, index):
    """Безопасное получение значения из Series или array"""
    try:
        if hasattr(data, 'iloc'):
            return data.iloc[index]
        elif hasattr(data, '__getitem__'):
            return data[index]
        elif hasattr(data, 'values'):
            return data.values[index]
        return None
    except (IndexError, KeyError):
        return None

def generate_bb_rsi_signal(df, bol_period=40, bol_dev=2, rsi_period=14):
    """ИСПРАВЛЕННАЯ генерация сигнала BB+RSI для реальной торговли"""
    if df is None or len(df) < bol_period:
        return None
    
    try:
        lower = bol_l(df["Close"], bol_period, bol_dev)
        upper = bol_h(df["Close"], bol_period, bol_dev)
        rsi_val = rsi(df["Close"], rsi_period)
        
        # Преобразуем всё к pandas Series для единообразия
        if not isinstance(lower, pd.Series):
            lower = pd.Series(lower)
        if not isinstance(upper, pd.Series):
            upper = pd.Series(upper)
        if not isinstance(rsi_val, pd.Series):
            rsi_val = pd.Series(rsi_val)
        
        if len(df) > 2 and len(lower) >= 3 and len(upper) >= 3 and len(rsi_val) >= 1:
            # Используем безопасное получение значений
            close_m3 = safe_get_value(df["Close"], -3)
            close_m2 = safe_get_value(df["Close"], -2)
            lower_m3 = safe_get_value(lower, -3)
            lower_m2 = safe_get_value(lower, -2)
            upper_m3 = safe_get_value(upper, -3)
            upper_m2 = safe_get_value(upper, -2)
            rsi_last = safe_get_value(rsi_val, -1)
            
            if (close_m3 is not None and close_m2 is not None and 
                lower_m3 is not None and lower_m2 is not None and
                upper_m3 is not None and upper_m2 is not None and
                rsi_last is not None):
                
                if close_m3 > lower_m3 and close_m2 < lower_m2 and rsi_last < 30:
                    return "BUY"
                elif close_m3 < upper_m3 and close_m2 > upper_m2 and rsi_last > 70:
                    return "SELL"
                    
    except Exception as e:
        print(f"❌ Ошибка в generate_bb_rsi_signal: {e}")
    
    return None

def generate_breakout_signal(df, period=20):
    """Генерация сигнала пробоя для реальной торговли"""
    if df is None or len(df) < period + 2:
        return None
    
    try:
        highest = df["High"].iloc[-period-1:-1].max()
        lowest = df["Low"].iloc[-period-1:-1].min()
        price_last = df["Close"].iloc[-1]
        
        if price_last > highest:
            return "BUY"
        elif price_last < lowest:
            return "SELL"
    except Exception as e:
        print(f"❌ Ошибка в generate_breakout_signal: {e}")
    
    return None

def get_trading_signal(symbol, df, strategy="bb_rsi"):
    """Получение торгового сигнала для реальной торговли"""
    if df is None or len(df) < 100:
        return None
    
    if strategy == "bb_rsi":
        return generate_bb_rsi_signal(df)
    elif strategy == "breakout":
        return generate_breakout_signal(df)
    else:
        return generate_bb_rsi_signal(df)