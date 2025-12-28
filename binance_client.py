"""
Binance Client для торгового бота
Поддерживает режимы: real (реальная торговля) и dryrun (тестовая сеть)
"""
import time
from datetime import datetime
from binance.client import Client
from binance.exceptions import BinanceAPIException
from binance.enums import *
import config


class BinanceClient:
    """Клиент для работы с Binance Futures API"""
    
    def __init__(self):
        """Инициализация клиента Binance"""
        self.client = None
        self.initialized = False
        self.last_api_call = time.time()
        self.api_call_count = 0
        self.last_reset_time = time.time()
        self.testnet = config.TRADING_MODE != 'real'
        
        print(f"{'='*60}")
        print(f"🚀 Инициализация BinanceClient")
        print(f"📊 Режим: {'🔴 РЕАЛЬНАЯ ТОРГОВЛЯ' if not self.testnet else '🟡 ТЕСТОВАЯ СЕТЬ'}")
        print(f"{'='*60}")
        
        try:
            if self.initialize_client():
                print("✅ Клиент готов к работе")
            else:
                print("⚠️  Клиент инициализирован с ограничениями")
                print("   Некоторые функции могут быть недоступны")
        except Exception as e:
            print(f"⚠️  Ошибка при инициализации: {e}")
            print("   Работаем в минимальном режиме")

    def _rate_limit(self):
        """Ограничение частоты запросов к API"""
        current_time = time.time()
    
        # Сбрасываем счетчик каждую минуту
        if current_time - self.last_reset_time > 60:
            if self.api_call_count > 0:
                print(f"📊 API запросов за последнюю минуту: {self.api_call_count}")
            self.api_call_count = 0
            self.last_reset_time = current_time
    
        # Лимит: не более 10 запросов в секунду (600 в минуту)
        if current_time - self.last_api_call < 0.1:
            time.sleep(0.1)
    
        self.last_api_call = current_time
        self.api_call_count += 1
    
        # Предупреждение при достижении лимита
        if self.api_call_count > 500:
            print(f"⚠️  Внимание: {self.api_call_count}/600 запросов к API за минуту")
    
        # Если достигли лимита Binance (1200/мин), ждем
        if self.api_call_count > 1100:  # Безопасный порог
            sleep_time = 60 - (current_time - self.last_reset_time)
            if sleep_time > 0:
                print(f"🚨 Лимит API! Ждем {sleep_time:.1f} секунд...")
                time.sleep(sleep_time)
   
    def initialize_client(self):
        """Инициализация клиента Binance"""
        try:
            # Проверяем наличие API ключей
            if not config.API_KEY or not config.API_SECRET:
               #raise ValueError("API_KEY или API_SECRET не установлены в config.py")
                print("⚠️  API ключи не установлены")
                return False
            # Создаем клиент
            if self.testnet:
                print("🟡 Подключение к ТЕСТОВОЙ сети Binance Futures...")
                self.client = Client(
                    api_key=config.API_KEY,
                    api_secret=config.API_SECRET,
                    testnet=True
                )
            else:
                print("🔴 Подключение к РЕАЛЬНОЙ торговле Binance Futures...")
                self.client = Client(
                    api_key=config.API_KEY,
                    api_secret=config.API_SECRET
                )
            
            # Синхронизируем время
            if not self.sync_time():
                print("⚠️  Внимание: время не синхронизировано!")
            
            # Тестируем подключение
            if not self.test_connection():
                raise ConnectionError("Не удалось подключиться к Binance")
            
            # Получаем информацию об аккаунте
            self.account_info = self.get_account_info()
            
            self.initialized = True
            print("✅ BinanceClient успешно инициализирован")
            
            # Выводим информацию о балансе
            if self.account_info:
                print(f"💰 Баланс USDT: {self.get_balance('USDT'):.2f}")
                print(f"📈 Общий баланс: {float(self.account_info['totalWalletBalance']):.2f}")
            
            return True
            
        except BinanceAPIException as e:
            print(f"❌ Ошибка Binance API: {e.code} - {e.message}")
            self._handle_api_error(e)
            return False
            
        except Exception as e:
            print(f"❌ Критическая ошибка инициализации: {e}")
            import traceback
            traceback.print_exc()
            return False
    
    def _handle_api_error(self, error):
        """Обработка ошибок API"""
        error_codes = {
            -1001: "Внутренняя ошибка Binance. Попробуйте позже.",
            -1021: "Неверная временная метка. Проверьте системное время.",
            -2010: "Недостаточно средств для выполнения ордера.",
            -2011: "Ордер отклонен.",
            -2013: "Неверный API ключ, IP или разрешения.",
            -2014: "Подпись недействительна.",
            -2015: "Неверный API ключ, IP или разрешения для действия.",
            -1013: "Нарушение фильтра: недопустимое количество.",
            -1111: "Слишком много десятичных знаков для количества.",
            -1121: "Неверный символ.",
        }
        
        if error.code in error_codes:
            print(f"⚠️  Подробности: {error_codes[error.code]}")
        
        # Рекомендации по устранению
        if error.code == -1021:
            print("💡 Решение: Синхронизируйте время системы или добавьте задержку.")
        elif error.code == -2015:
            print("💡 Решение: Проверьте разрешения API ключа в личном кабинете Binance.")
            print("   Нужно включить 'Enable Futures' в настройках API.")
    
    def sync_time(self):
        """Синхронизация времени с сервером Binance с коррекцией"""
        self._rate_limit()
    
        try:
            # Получаем время сервера несколько раз для точности
            time_diffs = []
            for _ in range(5):
                server_time = self.client.get_server_time()
                client_time = int(time.time() * 1000)
                time_diff = server_time['serverTime'] - client_time
                time_diffs.append(time_diff)
                time.sleep(0.1)
        
            # Берем медианное значение
            time_diffs.sort()
            median_diff = time_diffs[len(time_diffs)//2]
        
            print(f"📊 Расхождение времени: {median_diff}ms")
        
            # Если расхождение больше 1000ms, показываем предупреждение
            if abs(median_diff) > 1000:
                print(f"⚠️  ВНИМАНИЕ: Большое расхождение времени!")
                print(f"   Серверное время отличается на {median_diff}ms")
                print(f"   Рекомендуется синхронизировать системное время")
            
                # Для Windows предлагаем решение
                import platform
                if platform.system() == 'Windows':
                    print(f"   Запустите от администратора: w32tm /resync")
            
                # Не блокируем работу, но предупреждаем
                return False
            elif abs(median_diff) > 100:
                print(f"⚠️  Небольшое расхождение: {median_diff}ms")
                return True
            else:
                print(f"✅ Время синхронизировано (расхождение: {median_diff}ms)")
                return True
            
        except Exception as e:
            print(f"⚠️  Ошибка синхронизации времени: {e}")
            return False
    
    def test_connection(self):
        """Тестирование подключения к Binance"""
        self._rate_limit()
        
        try:
            # Проверяем доступ к API
            exchange_info = self.client.futures_exchange_info()
            print(f"✅ Подключение к Binance успешно")
            print(f"   Доступно символов: {len(exchange_info['symbols'])}")
            return True
            
        except BinanceAPIException as e:
            print(f"❌ Ошибка подключения к Binance: {e.code} - {e.message}")
            self._handle_api_error(e)
            return False
            
        except Exception as e:
            print(f"❌ Общая ошибка подключения: {e}")
            return False
    
    def get_account_info(self):
        """Получение информации об аккаунте"""
        self._rate_limit()
        
        try:
            account_info = self.client.futures_account()
            return account_info
            
        except BinanceAPIException as e:
            print(f"❌ Ошибка получения информации об аккаунте: {e.code}")
            return None
            
        except Exception as e:
            print(f"❌ Ошибка при запросе аккаунта: {e}")
            return None
    
    def get_balance(self, asset='USDT'):
        """Получение баланса"""
        if not self.initialized:
            print("⚠️  Клиент не инициализирован")
            return 0.0
        
        self._rate_limit()
        
        try:
            if asset.upper() == 'USDT':
                # Для USDT используем доступный баланс фьючерсов
                account = self.client.futures_account()
                balance = float(account['availableBalance'])
            else:
                # Для других активов ищем в списке
                account = self.client.futures_account()
                balances = account['assets']
                
                for bal in balances:
                    if bal['asset'] == asset.upper():
                        balance = float(bal['availableBalance'])
                        break
                else:
                    balance = 0.0
            
            return balance
            
        except Exception as e:
            print(f"❌ Ошибка получения баланса {asset}: {e}")
            return 0.0
    
    def get_positions(self):
        """Получение текущих позиций"""
        if not self.initialized:
            print("⚠️  Клиент не инициализирован")
            return []
        
        self._rate_limit()
        
        try:
            positions = self.client.futures_position_information()
            
            # Фильтруем только открытые позиции
            open_positions = []
            for pos in positions:
                if float(pos['positionAmt']) != 0:
                    open_positions.append({
                        'symbol': pos['symbol'],
                        'side': 'BUY' if float(pos['positionAmt']) > 0 else 'SELL',
                        'quantity': abs(float(pos['positionAmt'])),
                        'entry_price': float(pos['entryPrice']),
                        'mark_price': float(pos['markPrice']),
                        'unrealized_pnl': float(pos['unRealizedProfit']),
                        'leverage': int(float(pos['leverage']))
                    })
            
            return open_positions
            
        except Exception as e:
            print(f"❌ Ошибка получения позиций: {e}")
            return []
    
    def get_symbol_info(self, symbol):
        """Получение информации о символе"""
        self._rate_limit()
        
        try:
            exchange_info = self.client.futures_exchange_info()
            
            for sym_info in exchange_info['symbols']:
                if sym_info['symbol'] == symbol:
                    info = {
                        'symbol': symbol,
                        'status': sym_info['status'],
                        'baseAsset': sym_info['baseAsset'],
                        'quoteAsset': sym_info['quoteAsset'],
                        'filters': {}
                    }
                    
                    # Извлекаем фильтры
                    for filt in sym_info['filters']:
                        filter_type = filt['filterType']
                        info['filters'][filter_type] = filt
                    
                    # Упрощенные поля для удобства
                    lot_size = info['filters'].get('LOT_SIZE', {})
                    price_filter = info['filters'].get('PRICE_FILTER', {})
                    min_notional = info['filters'].get('MIN_NOTIONAL', {})
                    
                    info['min_qty'] = float(lot_size.get('minQty', 0))
                    info['max_qty'] = float(lot_size.get('maxQty', 0))
                    info['step_size'] = float(lot_size.get('stepSize', 0.001))
                    
                    info['min_price'] = float(price_filter.get('minPrice', 0))
                    info['max_price'] = float(price_filter.get('maxPrice', 0))
                    info['tick_size'] = float(price_filter.get('tickSize', 0.01))
                    
                    info['min_notional'] = float(min_notional.get('minNotional', 5))
                    
                    return info
            
            print(f"⚠️  Символ {symbol} не найден")
            return None
            
        except Exception as e:
            print(f"❌ Ошибка получения информации о символе {symbol}: {e}")
            return None
    
    def place_order(self, side, quantity, symbol, order_type=ORDER_TYPE_MARKET, price=None):
        """Размещение ордера"""
        if not self.initialized:
            raise Exception("Клиент не инициализирован")
        
        self._rate_limit()
        
        try:
            print(f"\n{'='*40}")
            print(f"🚨 РАЗМЕЩЕНИЕ ОРДЕРА")
            print(f"{'='*40}")
            print(f"Символ: {symbol}")
            print(f"Сторона: {side}")
            print(f"Тип: {order_type}")
            print(f"Количество: {quantity}")
            
            if price:
                print(f"Цена: {price}")
            print(f"Режим: {'РЕАЛЬНЫЙ' if not self.testnet else 'ТЕСТОВЫЙ'}")
            
            # Параметры ордера
            order_params = {
                'symbol': symbol,
                'side': side,
                'type': order_type,
                'quantity': quantity
            }
            
            # Для лимитных ордеров добавляем цену
            if order_type == ORDER_TYPE_LIMIT and price:
                order_params['price'] = price
                order_params['timeInForce'] = TIME_IN_FORCE_GTC
            
            # Размещаем ордер
            order = self.client.futures_create_order(**order_params)
            
            print(f"\n✅ Ордер успешно размещен!")
            print(f"ID ордера: {order['orderId']}")
            print(f"Статус: {order['status']}")
            print(f"Исполнено: {order['executedQty']}")
            
            if 'avgPrice' in order and order['avgPrice']:
                print(f"Средняя цена: {order['avgPrice']}")
            
            return order
            
        except BinanceAPIException as e:
            print(f"\n❌ Ошибка API при размещении ордера: {e.code} - {e.message}")
            
            # Обработка ошибки минимального номинала
            if e.code == -4164:  # Order's notional must be no smaller than...
                print(f"⚠️  Решение: увеличьте количество или торгуйте с большим балансом")
                print(f"   Минимальный номинал для {symbol}: 100 USDT")
            
            self._handle_api_error(e)
            raise
            
        except Exception as e:
            print(f"\n❌ Общая ошибка при размещении ордера: {e}")
            raise

    def close_position(self, symbol, side, quantity):
        """Закрытие позиции"""
        if not self.initialized:
            raise Exception("Клиент не инициализирован")
    
        self._rate_limit()
    
        try:    
            # Определяем сторону для закрытия (противоположная)
            close_side = 'SELL' if side.upper() == 'BUY' else 'BUY'
        
            print(f"\n{'='*40}")
            print(f"🚨 ЗАКРЫТИЕ ПОЗИЦИИ")
            print(f"{'='*40}")
            print(f"Символ: {symbol}")
            print(f"Открытая сторона: {side}")
            print(f"Сторона закрытия: {close_side}")
            print(f"Количество: {quantity}")
            print(f"Режим: {'РЕАЛЬНЫЙ' if not self.testnet else 'ТЕСТОВЫЙ'}")
        
            # УБЕРИТЕ input() ДЛЯ АВТОМАТИЧЕСКОЙ ТОРГОВЛИ!
            # Вместо этого просто логируем
            if not self.testnet:
                print(f"⚠️  ВНИМАНИЕ: Закрытие РЕАЛЬНОЙ позиции!")
                print(f"   Символ: {symbol}, Сторона: {close_side}, Количество: {quantity}")
                # Для автоматической торговли подтверждение не нужно
                # Если хотите подтверждение, используйте Telegram команду
            
            # Размещаем ордер на закрытие
            order = self.client.futures_create_order(
                symbol=symbol,
                side=close_side,
                type=ORDER_TYPE_MARKET,
                quantity=quantity,
                reduceOnly=True  # Только уменьшение позиции
            )
            
            print(f"\n✅ Позиция успешно закрыта!")
            print(f"ID ордера: {order['orderId']}")
            print(f"Статус: {order['status']}")
            
            return order
            
        except BinanceAPIException as e:
            print(f"\n❌ Ошибка API при закрытии позиции: {e.code} - {e.message}")
            
            # Обработка специфических ошибок
            if e.code == -4164:  # Order's notional must be no smaller than...
                print(f"⚠️  Номинал ордера меньше минимального. Пробую без reduceOnly...")
                try:
                    # Пробуем без reduceOnly
                    order = self.client.futures_create_order(
                        symbol=symbol,
                        side=close_side,
                        type=ORDER_TYPE_MARKET,
                        quantity=quantity
                        # Без reduceOnly
                    )
                    print(f"✅ Закрыто без reduceOnly")
                    return order
                except Exception as e2:
                    print(f"❌ Вторая попытка тоже не удалась: {e2}")
            
            self._handle_api_error(e)
            raise
            
        except Exception as e:
            print(f"\n❌ Общая ошибка при закрытии позиции: {e}")
            raise
    
    def get_klines(self, symbol, interval='5m', limit=500):
        """Получение исторических свечей"""
        self._rate_limit()
        
        try:
            klines = self.client.futures_klines(
                symbol=symbol,
                interval=interval,
                limit=limit
            )
            return klines
            
        except Exception as e:
            print(f"❌ Ошибка получения свечей для {symbol}: {e}")
            return []
    
    def get_ticker_price(self, symbol):
        """Получение текущей цены"""
        self._rate_limit()
        
        try:
            ticker = self.client.futures_symbol_ticker(symbol=symbol)
            return float(ticker['price'])
            
        except Exception as e:
            print(f"❌ Ошибка получения цены для {symbol}: {e}")
            return 0.0
    
    def get_order_status(self, symbol, order_id):
        """Получение статуса ордера"""
        self._rate_limit()
        
        try:
            order = self.client.futures_get_order(
                symbol=symbol,
                orderId=order_id
            )
            return order
            
        except Exception as e:
            print(f"❌ Ошибка получения статуса ордера {order_id}: {e}")
            return None
    
    def cancel_order(self, symbol, order_id):
        """Отмена ордера"""
        self._rate_limit()
        
        try:
            result = self.client.futures_cancel_order(
                symbol=symbol,
                orderId=order_id
            )
            print(f"✅ Ордер {order_id} отменен")
            return result
            
        except Exception as e:
            print(f"❌ Ошибка отмены ордера {order_id}: {e}")
            return None
    
    def get_income_history(self, symbol=None, limit=100):
        """Получение истории доходов (комиссии, финансирование)"""
        self._rate_limit()
        
        try:
            params = {'limit': limit}
            if symbol:
                params['symbol'] = symbol
            
            history = self.client.futures_income_history(**params)
            return history
            
        except Exception as e:
            print(f"❌ Ошибка получения истории доходов: {e}")
            return []
    
    def get_funding_rate(self, symbol):
        """Получение текущей ставки финансирования"""
        self._rate_limit()
        
        try:
            funding = self.client.futures_funding_rate(symbol=symbol, limit=1)
            if funding:
                return float(funding[0]['fundingRate'])
            return 0.0
            
        except Exception as e:
            print(f"❌ Ошибка получения ставки финансирования для {symbol}: {e}")
            return 0.0
    
    def is_connected(self):
        """Проверка подключения"""
        return self.initialized and self.client is not None
    
    def get_mode(self):
        """Получение режима работы"""
        return 'TESTNET' if self.testnet else 'REAL'


# Глобальный экземпляр клиента для использования во всем проекте
binance_client = BinanceClient()


def get_client():
    """Получение глобального экземпляра клиента"""
    return binance_client


if __name__ == "__main__":
    # Тестирование клиента
    print("\n🧪 Тестирование BinanceClient...")
    
    if binance_client.is_connected():
        print("✅ Клиент успешно подключен")
        
        # Тест получения баланса
        balance = binance_client.get_balance('USDT')
        print(f"💰 Баланс USDT: {balance:.2f}")
        
        # Тест получения позиций
        positions = binance_client.get_positions()
        print(f"📊 Открытых позиций: {len(positions)}")
        
        # Тест получения информации о символе
        btc_info = binance_client.get_symbol_info('BTCUSDT')
        if btc_info:
            print(f"📈 Информация о BTCUSDT:")
            print(f"   Min Qty: {btc_info['min_qty']}")
            print(f"   Step Size: {btc_info['step_size']}")
            print(f"   Min Notional: {btc_info['min_notional']}")
        
        # Тест получения цены
        price = binance_client.get_ticker_price('BTCUSDT')
        print(f"💵 Текущая цена BTCUSDT: {price:.2f}")
        
    else:
        print("❌ Не удалось подключиться к Binance")
