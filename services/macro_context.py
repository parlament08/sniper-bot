import requests
import yfinance as yf
import pandas as pd


DXY_NEUTRAL_THRESHOLD_PERCENT = 0.12
SPX_NEUTRAL_THRESHOLD_PERCENT = 0.25
MACRO_MAX_DATA_AGE_HOURS = 36
BTC_D_HIGH_RISK_THRESHOLD = 57.5

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
        return None


def _data_age_hours(index_value) -> float:
    try:
        timestamp = pd.Timestamp(index_value)
        if timestamp.tzinfo is None:
            timestamp = timestamp.tz_localize("UTC")
        now = pd.Timestamp.utcnow()
        return round(max((now - timestamp).total_seconds() / 3600, 0.0), 2)
    except Exception:
        return float("inf")


def _tradfi_trend(hist, asset_name: str) -> dict:
    if hist is None or hist.empty or len(hist) < 3:
        return {
            "price": None,
            "trend": "Unavailable ⚪",
            "bias": "unavailable",
            "change_pct": 0.0,
            "data_age_hours": None,
            "stale": True,
        }

    closes = hist["Close"].dropna()
    if len(closes) < 3:
        return {
            "price": None,
            "trend": "Unavailable ⚪",
            "bias": "unavailable",
            "change_pct": 0.0,
            "data_age_hours": None,
            "stale": True,
        }

    current_price = float(closes.iloc[-1])
    start_price = float(closes.iloc[0])
    ema_span = min(5, len(closes))
    ema_value = float(closes.ewm(span=ema_span, adjust=False).mean().iloc[-1])
    change_pct = ((current_price - start_price) / start_price) * 100 if start_price else 0.0
    threshold = DXY_NEUTRAL_THRESHOLD_PERCENT if asset_name == "DXY" else SPX_NEUTRAL_THRESHOLD_PERCENT
    age_hours = _data_age_hours(closes.index[-1])
    stale = age_hours > MACRO_MAX_DATA_AGE_HOURS

    if abs(change_pct) < threshold:
        bias = "neutral"
    elif current_price > ema_value and change_pct > 0:
        bias = "bullish"
    elif current_price < ema_value and change_pct < 0:
        bias = "bearish"
    else:
        bias = "neutral"

    trend_map = {
        "bullish": "Bullish 🟢",
        "bearish": "Bearish 🔴",
        "neutral": "Neutral 🟡",
    }
    if stale:
        trend = f"{trend_map[bias]} (stale)"
    else:
        trend = trend_map[bias]

    return {
        "price": round(current_price, 2),
        "trend": trend,
        "bias": bias,
        "change_pct": round(change_pct, 4),
        "data_age_hours": age_hours,
        "stale": stale,
    }

def get_macro_context():
    """
    Собирает данные по S&P500 и DXY.
    Интегрирует доминацию BTC с откалиброванным порогом для API CoinGecko.
    """
    tickers = {"DXY": "DX-Y.NYB", "SPX": "^GSPC"}
    macro_data = {}
    
    # 1. Получаем традиционные финансы (DXY, SPX)
    for name, symbol in tickers.items():
        try:
            # Отключаем вывод предупреждений yfinance
            ticker = yf.Ticker(symbol)
            hist = ticker.history(period="5d")
            macro_data[name] = _tradfi_trend(hist, name)
        except Exception as e:
            macro_data[name] = {
                "price": None,
                "trend": "Unavailable ⚪",
                "bias": "unavailable",
                "change_pct": 0.0,
                "data_age_hours": None,
                "stale": True,
                "error": str(e),
            }
            
    # 2. Интегрируем доминацию Биткоина
    btc_d = get_btc_dominance()
    
    if btc_d is None:
        macro_data["BTC.D"] = {
            "price": None,
            "trend": "Unavailable ⚪",
            "bias": "unavailable",
            "stale": True,
        }
        return macro_data
    
    # Сдвиг порога: 57.5% на CoinGecko примерно равно 60% на TradingView
    btc_status = "High Risk (Alts) 🔴" if btc_d > BTC_D_HIGH_RISK_THRESHOLD else "Neutral 🟡"
    btc_bias = "high_risk_alts" if btc_d > BTC_D_HIGH_RISK_THRESHOLD else "neutral"
    
    macro_data["BTC.D"] = {
        "price": btc_d,
        "trend": btc_status,
        "bias": btc_bias,
        "stale": False,
    }
            
    return macro_data

def evaluate_macro_score(trade_direction: str, macro_data: dict, is_altcoin: bool = True) -> tuple:
    """
    Оценивает макро-фон и возвращает кортеж: (баллы, текстовое_описание).
    Вместо блокировки (False) возвращает 0 баллов (Neutral) при рассинхроне.
    """
    if not macro_data:
        return 0, "Нет данных"
        
    dxy_data = macro_data.get("DXY", {})
    spx_data = macro_data.get("SPX", {})
    btc_d_data = macro_data.get("BTC.D", {})
    dxy_bias = dxy_data.get("bias") or _bias_from_trend(dxy_data.get("trend", ""))
    spx_bias = spx_data.get("bias") or _bias_from_trend(spx_data.get("trend", ""))
    btc_d_bias = btc_d_data.get("bias") or ("high_risk_alts" if "High Risk" in btc_d_data.get("trend", "") else "neutral")

    if dxy_data.get("stale") or spx_data.get("stale"):
        return 0, "Макро-данные DXY/SPX устарели или недоступны"
    if dxy_bias == "unavailable" or spx_bias == "unavailable":
        return 0, "DXY/SPX unavailable"
    
    # Определяем идеальную синхронизацию
    is_tradfi_bullish = spx_bias == "bullish" and dxy_bias == "bearish"
    is_tradfi_bearish = spx_bias == "bearish" and dxy_bias == "bullish"
    long_partial = (spx_bias == "bullish" and dxy_bias == "neutral") or (spx_bias == "neutral" and dxy_bias == "bearish")
    short_partial = (spx_bias == "bearish" and dxy_bias == "neutral") or (spx_bias == "neutral" and dxy_bias == "bullish")
    
    if trade_direction == 'long':
        if is_altcoin and btc_d_bias == "unavailable":
            return 0, "BTC.D unavailable"
        if is_altcoin and btc_d_bias == "high_risk_alts":
            return 0, "BTC.D высокий: риск оттока ликвидности из альтов"
            
        if is_tradfi_bullish:
            return 10, "Макро-фон подтверждает лонг"
        if long_partial:
            return 5, "Частичная поддержка лонга: один из DXY/SPX нейтрален"
        if dxy_bias == "bearish" and spx_bias == "bearish":
            return 0, "Смешанный фон: DXY поддерживает риск, SPX давит на риск"
        if dxy_bias == "bullish" and spx_bias == "bullish":
            return 0, "Смешанный фон: SPX поддерживает риск, DXY давит на риск"
        else:
            return 0, "Смешанный или нейтральный фон DXY/SPX"
            
    elif trade_direction == 'short':
        if is_tradfi_bearish:
            return 10, "Макро-фон подтверждает шорт"
        if short_partial:
            return 5, "Частичная поддержка шорта: один из DXY/SPX нейтрален"
        if dxy_bias == "bearish" and spx_bias == "bearish":
            return 0, "Смешанный фон: SPX давит на риск, DXY против шорта"
        if dxy_bias == "bullish" and spx_bias == "bullish":
            return 0, "Смешанный фон: DXY за шорт, SPX поддерживает риск"
        else:
            return 0, "Смешанный или нейтральный фон DXY/SPX"
            
    return 0, "Неизвестное направление"


def _bias_from_trend(trend: str) -> str:
    if "Bullish" in trend:
        return "bullish"
    if "Bearish" in trend:
        return "bearish"
    if "Neutral" in trend:
        return "neutral"
    return "unavailable"


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
