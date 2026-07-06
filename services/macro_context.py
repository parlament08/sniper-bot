import requests
import yfinance as yf

def get_btc_dominance():
    """
    Получает текущую доминацию BTC (в процентах) через API CoinGecko.
    """
    try:
        url = "https://api.coingecko.com/api/v3/global"
        # timeout=5 не даст скрипту зависнуть, если API упадет
        response = requests.get(url, timeout=5).json()
        btc_dominance = response['data']['market_cap_percentage']['btc']
        return round(btc_dominance, 2)
    except Exception as e:
        print(f"Ошибка получения BTC.D: {e}")
        return 55.0 # Среднее значение по умолчанию, чтобы ИИ мог продолжить работу

def get_macro_context():
    """
    Собирает данные по S&P500 и DXY для синхронизации рынка.
    Добавляет актуальную доминацию BTC.
    Определяет локальный тренд по закрытию вчерашнего/сегодняшнего дня.
    """
    tickers = {"DXY": "DX-Y.NYB", "SPX": "^GSPC"}
    macro_data = {}
    
    print("🔄 Запрос межрыночного фона...")
    
    # 1. Получаем традиционные финансы (DXY, SPX)
    for name, symbol in tickers.items():
        try:
            ticker = yf.Ticker(symbol)
            hist = ticker.history(period="5d")
            
            if len(hist) >= 2:
                current_price = hist['Close'].iloc[-1]
                prev_price = hist['Close'].iloc[-2]
                
                trend = "Bullish 🟢" if current_price > prev_price else "Bearish 🔴"
                
                macro_data[name] = {
                    "price": round(current_price, 2),
                    "trend": trend
                }
            else:
                macro_data[name] = {"price": None, "trend": "Unknown"}
        except Exception as e:
            print(f"Ошибка получения данных для {name}: {e}")
            macro_data[name] = {"price": None, "trend": "Unknown"}
            
    # 2. Интегрируем доминацию Биткоина
    btc_d = get_btc_dominance()
    # Размечаем статус доминации по твоему алгоритму
    btc_status = "High Risk (Alts) 🔴" if btc_d > 60 else "Neutral 🟡"
    
    macro_data["BTC.D"] = {
        "price": btc_d,
        "trend": btc_status
    }
            
    return macro_data

def check_macro_confirmation(trade_direction: str, macro_data: dict, is_altcoin: bool = True) -> bool:
    """
    Анализирует макро-данные (DXY, SPX) и доминацию Биткоина (BTC.D).
    """
    if not macro_data:
        return False
        
    dxy_trend = macro_data.get("DXY", {}).get("trend", "")
    spx_trend = macro_data.get("SPX", {}).get("trend", "")
    btc_d_status = macro_data.get("BTC.D", {}).get("trend", "")
    
    # Базовая проверка TradFi (Глобальная ликвидность)
    is_tradfi_bullish = "Bullish" in spx_trend and "Bearish" in dxy_trend
    is_tradfi_bearish = "Bearish" in spx_trend and "Bullish" in dxy_trend
    
    if trade_direction == 'long':
        # Для лонга: TradFi должен быть Risk-On
        if not is_tradfi_bullish:
            return False
            
        # Если торгуем альткоин, проверяем доминацию битка
        if is_altcoin and "High Risk" in btc_d_status:
            return False # Ликвидность уходит в биток, макро не подтверждает лонг альтов
            
        return True
        
    elif trade_direction == 'short':
        # Для шорта: TradFi должен быть Risk-Off (падаем)
        if not is_tradfi_bearish:
            return False
            
        # При шорте альткоинов высокая доминация битка - это даже плюс (альта слабеет быстрее),
        # поэтому здесь мы не блокируем шорт по BTC.D.
        return True
        
    return False

if __name__ == "__main__":
    context = get_macro_context()
    
    # Форматируем вывод
    print("\n🌍 ТЕКУЩИЙ МЕЖРЫНОЧНЫЙ ФОН:")
    for asset, data in context.items():
        if asset == "BTC.D":
            print(f"[{asset}]: Значение {data['price']}% | Статус: {data['trend']}")
        else:
            print(f"[{asset}]: Цена {data['price']} | Тренд (1D): {data['trend']}")
    
    # Срабатывание триггеров защиты из твоего промпта
    if context.get("DXY", {}).get("trend") == "Bullish 🟢" and context.get("SPX", {}).get("trend") == "Bearish 🔴":
        print("\n⚠️ ВНИМАНИЕ: DXY растет, Фонда падает. Режим RISK-OFF (Шорт приоритет, Лонги заблокированы).")
        
    if context.get("BTC.D", {}).get("price", 0) > 60:
        print("⚠️ ВНИМАНИЕ: Доминация BTC выше 60%. Лонги по альткоинам требуют двойного подтверждения.")