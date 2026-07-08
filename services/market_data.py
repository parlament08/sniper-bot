import requests
import pandas as pd

def fetch_candles(symbol, timeframe, limit=5):
    """
    Прямой и сверхбыстрый запрос к Binance Futures API без тяжелых библиотек.
    """
    # fapi - это эндпоинт именно USDT-M фьючерсов
    url = "https://fapi.binance.com/fapi/v1/klines"
    
    # Формируем тикер в формате Binance (без слешей), например BTCUSDT
    binance_symbol = f"{symbol}USDT"
    
    params = {
        "symbol": binance_symbol,
        "interval": timeframe,
        "limit": limit
    }
    
    try:
        # Тайм-аут 5 секунд, чтобы не висеть вечно при сбоях сети
        response = requests.get(url, params=params, timeout=5)
        response.raise_for_status() 
        data = response.json()
        
        # Binance возвращает массив массивов, забираем только нужное
        df = pd.DataFrame(data, columns=[
            'timestamp', 'open', 'high', 'low', 'close', 'volume', 
            'close_time', 'qav', 'num_trades', 'tbbav', 'tbqav', 'ignore'
        ])
        
        # Оставляем только классический OHLCV
        df = df[['timestamp', 'open', 'high', 'low', 'close', 'volume']]
        
        # Конвертируем тиковые миллисекунды в нормальное время UTC
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        
        # Устанавливаем timestamp как индекс, это необходимо для resample()
        df.set_index('timestamp', inplace=True)
        
        return df
        
    except Exception as e:
        print(f"❌ Ошибка прямого API Binance для {symbol} ({timeframe}): {e}")
        return None

if __name__ == "__main__":
    # Быстрый тест
    print("⚡ Тест прямого подключения к Binance Futures...")
    df = fetch_candles("BTC", "4h")
    if df is not None:
        print(df.tail(1).to_string(index=False))