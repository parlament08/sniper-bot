from typing import Dict, List, Optional

def calculate_setup_score(
    trade_direction: str,
    current_price: float,
    trend_data: Optional[Dict],
    structure_data: Optional[Dict],
    sfp_data_in_window: Optional[Dict],
    fvg_tested_in_window: bool,
    fvg_data: List[Dict],
    volume_data: Optional[Dict],
    macro_data: Optional[Dict]
) -> Dict:
    """
    Рассчитывает скоринговую оценку для торгового сетапа на основе набора технических факторов.
    Использует концепцию "Окна памяти" (Lookback Window) для SFP и FVG.

    Args:
        trade_direction (str): Направление сделки ('long' или 'short').
        current_price (float): Текущая цена закрытия для проверки инвалидации.
        trend_data (Optional[Dict]): Данные о тренде из `evaluate_trend`.
        structure_data (Optional[Dict]): Данные о сломе структуры (BOS/CHoCH) на ПОСЛЕДНЕЙ свече.
        sfp_data_in_window (Optional[Dict]): Данные о захвате ликвидности (SFP) в ОКНЕ ПАМЯТИ.
        fvg_tested_in_window (bool): Флаг, был ли протестирован релевантный FVG в ОКНЕ ПАМЯТИ.
        fvg_data (List[Dict]): Список всех FVG для проверки на инвалидацию.
        volume_data (Optional[Dict]): Данные об объеме на ПОСЛЕДНЕЙ свече.
        macro_data (Optional[Dict]): Данные о макро-контексте.

    Returns:
        Dict: Словарь с итоговым счетом, решением и детализацией начисленных баллов.
    """
    score = 0
    breakdown = {
        'trend': '0',
        'structure': '0',
        'liquidity': '0',
        'fvg': '0',
        'volume': '0',
        'macro': '0'
    }

    # 1. Тренд (+25 / +10 баллов)
    # ФИКС: Если тренд глобально направлен ПРОТИВ нашей сделки,
    # бот не должен начислять никаких баллов, независимо от ADX.
    
    if trend_data:
        is_with_trend = (trade_direction == 'long' and trend_data.get('is_bullish')) or \
                        (trade_direction == 'short' and not trend_data.get('is_bullish'))
        
        if is_with_trend:
            if trend_data.get('strength') == 'strong':
                score += 25
                breakdown['trend'] = '+25 (Сильный тренд, совпадает с направлением)'
            else:  # 'flat'
                score += 10
                breakdown['trend'] = '+10 (Цена по тренду, слабый импульс/откат)'
        else:
            # ЖЕСТКИЙ ФИЛЬТР:
            score += 0
            breakdown['trend'] = '0 (Контртренд - торговля против 4H EMA99)'

    # 2. Структура (+20 баллов)
    if structure_data:
        is_structure_aligned = (trade_direction == 'long' and 'bullish' in structure_data.get('type', '')) or \
                               (trade_direction == 'short' and 'bearish' in structure_data.get('type', ''))
        
        if is_structure_aligned:
            # Упрощенная логика: слом против тренда = CHoCH, слом по тренду = BOS.
            is_with_trend = (trade_direction == 'long' and trend_data.get('is_bullish', False)) or \
                            (trade_direction == 'short' and not trend_data.get('is_bullish', True))
            
            if not is_with_trend:  # Слом против глобального тренда
                score += 20
                breakdown['structure'] = '+20 (CHoCH - смена характера)'
            else:  # Слом по тренду
                score += 15
                breakdown['structure'] = '+15 (BOS - продолжение тренда)'

    # 3. Ликвидность (+20 баллов)
    if sfp_data_in_window:
        # SFP - разворотный паттерн. Bullish SFP (снизу) для лонга, Bearish SFP (сверху) для шорта.
        is_sfp_aligned = (trade_direction == 'long' and 'bullish' in sfp_data_in_window.get('type', '')) or \
                         (trade_direction == 'short' and 'bearish' in sfp_data_in_window.get('type', ''))
        
        if is_sfp_aligned:
            score += 20
            breakdown['liquidity'] = '+20 (SFP в окне 5 часов)'

    # 4. FVG (+15 баллов) - Память + Инвалидация пробоем
    breakdown['fvg'] = '0 (Зона не тестировалась)'
    if fvg_tested_in_window:
        is_fvg_zone_valid = False
        # Проверяем, что хотя бы одна релевантная зона не пробита
        for fvg in fvg_data:
            if trade_direction == 'long' and fvg.get('type') == 'bullish':
                # Инвалидация: текущая цена НЕ должна быть ниже нижней границы FVG
                if current_price >= fvg['bottom']:
                    is_fvg_zone_valid = True
                    break
            elif trade_direction == 'short' and fvg.get('type') == 'bearish':
                # Инвалидация: текущая цена НЕ должна быть выше верхней границы FVG
                if current_price <= fvg['top']:
                    is_fvg_zone_valid = True
                    break
        
        if is_fvg_zone_valid:
            score += 15
            breakdown['fvg'] = '+15 (Тест FVG в окне 5 часов, зона удержана)'
        else:
            breakdown['fvg'] = '0 (Зона пробита после теста)'

    # 5. Объем (+10 баллов) - Привязан к триггерным паттернам (SFP/BOS)
    breakdown['volume'] = '0 (Нет подтверждения аномальным объемом)'
    
    # Приоритет 1: Объем на свече SFP (сбор ликвидности)
    is_sfp_aligned = sfp_data_in_window and (
        (trade_direction == 'long' and 'bullish' in sfp_data_in_window.get('type', '')) or
        (trade_direction == 'short' and 'bearish' in sfp_data_in_window.get('type', ''))
    )
    if is_sfp_aligned and sfp_data_in_window.get('rvol', 0) > 1.5:
        score += 10
        breakdown['volume'] = '+10 (Подтверждение объемом на свече SFP)'
    
    # Приоритет 2: Объем на свече слома структуры (BOS/CHoCH)
    else:
        is_structure_aligned = structure_data and (
            (trade_direction == 'long' and 'bullish' in structure_data.get('type', '')) or
            (trade_direction == 'short' and 'bearish' in structure_data.get('type', ''))
        )
        if is_structure_aligned and structure_data.get('rvol', 0) > 1.5:
            score += 10
            breakdown['volume'] = '+10 (Подтверждение объемом на свече BOS/CHoCH)'

    # 6. Макро-контекст (+10 / 0 баллов)
    if macro_data:
        m_score = macro_data.get('score', 0)
        m_reason = macro_data.get('reason', 'Нет данных')
        score += m_score
        breakdown['macro'] = f"+{m_score} ({m_reason})" if m_score > 0 else f"0 ({m_reason})"

    # Определение итогового решения на основе суммы баллов
    decision = "Ignore"
    
    if score >= 70:
        decision = "A+"
    elif score >= 40:
        decision = "Watchlist"
    else:
        decision = "Ignore"

    return {
        'total_score': score,
        'decision': decision,
        'breakdown': breakdown
    }