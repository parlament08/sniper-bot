import yfinance as yf

def get_macro_context():
    """
    Собирает данные по S&P500 и DXY для синхронизации рынка.
    Определяет локальный тренд по закрытию вчерашнего/сегодняшнего дня.
    """
    # Тикеры в Yahoo Finance: Индекс доллара и S&P 500
    tickers = {"DXY": "DX-Y.NYB", "SPX": "^GSPC"}
    macro_data = {}
    
    print("🔄 Запрос межрыночного фона...")
    
    for name, symbol in tickers.items():
        try:
            ticker = yf.Ticker(symbol)
            # Берем историю за последние 5 дней (дневки)
            hist = ticker.history(period="5d")
            
            if len(hist) >= 2:
                # Текущая и предыдущая цена закрытия
                current_price = hist['Close'].iloc[-1]
                prev_price = hist['Close'].iloc[-2]
                
                # Примитивная логика тренда (для 1D). Потом можем усложнить до RSI или MA.
                trend = "Bullish 🟢" if current_price > prev_price else "Bearish 🔴"
                
                macro_data[name] = {
                    "price": round(current_price, 2),
                    "trend": trend
                }
        except Exception as e:
            print(f"Ошибка получения данных для {name}: {e}")
            macro_data[name] = {"price": None, "trend": "Unknown"}
            
    return macro_data

if __name__ == "__main__":
    context = get_macro_context()
    
    # Форматируем вывод так, как мы будем отдавать его в Gemini
    print("\n🌍 ТЕКУЩИЙ МЕЖРЫНОЧНЫЙ ФОН:")
    for asset, data in context.items():
        print(f"[{asset}]: Цена {data['price']} | Тренд (1D): {data['trend']}")
    
    # Простейший фильтр из твоей инструкции (Синхронизация)
    if context.get("DXY", {}).get("trend") == "Bullish 🟢" and context.get("SPX", {}).get("trend") == "Bearish 🔴":
        print("\n⚠️ ВНИМАНИЕ: DXY растет, Фонда падает. Режим RISK-OFF (Шорт приоритет, Лонги по крипте заблокированы).")