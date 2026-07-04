from typing import Dict, List, Optional

def calculate_setup_score(
    trade_direction: str,
    trend_data: Optional[Dict],
    structure_data: Optional[Dict],
    sfp_data: Optional[Dict],
    fvg_data: List[Dict],
    volume_data: Optional[Dict],
    macro_data: Optional[Dict]
) -> Dict:
    """
    Рассчитывает скоринговую оценку для торгового сетапа на основе набора технических факторов.

    Args:
        trade_direction (str): Направление сделки ('long' или 'short').
        trend_data (Optional[Dict]): Данные о тренде из `evaluate_trend`.
            Пример: {'is_bullish': True, 'strength': 'strong', 'adx_value': 32.5}
        structure_data (Optional[Dict]): Данные о сломе структуры из `detect_structure_break`.
            Пример: {'type': 'bullish_break', 'level': 65000.0}
        sfp_data (Optional[Dict]): Данные о захвате ликвидности из `detect_sfp`.
            Пример: {'type': 'bullish_sfp', 'level': 64000.0}
        fvg_data (List[Dict]): Список FVG из `find_fvg`.
            Пример: [{'type': 'bullish', 'top': 61700, 'bottom': 61500, ...}]
        volume_data (Optional[Dict]): Данные об объеме.
            Пример: {'rvol': 1.8}
        macro_data (Optional[Dict]): Данные о макро-контексте (заглушка).
            Пример: {'confirms': True}

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

    # 1. Тренд (+25 баллов)
    if trend_data:
        is_with_trend = (trade_direction == 'long' and trend_data.get('is_bullish')) or \
                        (trade_direction == 'short' and not trend_data.get('is_bullish'))
        
        if is_with_trend:
            if trend_data.get('strength') == 'strong':
                score += 25
                breakdown['trend'] = '+25 (Сильный тренд по ADX)'
            else:  # 'flat'
                score += 10
                breakdown['trend'] = '+10 (Цена по тренду, но ADX во флэте)'

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
    if sfp_data:
        # SFP - разворотный паттерн. Bullish SFP (снизу) для лонга, Bearish SFP (сверху) для шорта.
        is_sfp_aligned = (trade_direction == 'long' and 'bullish' in sfp_data.get('type', '')) or \
                         (trade_direction == 'short' and 'bearish' in sfp_data.get('type', ''))
        
        if is_sfp_aligned:
            score += 20
            breakdown['liquidity'] = '+20 (SFP - захват ликвидности)'

    # 4. FVG (+15 баллов)
    if fvg_data:
        is_fvg_aligned = any(
            (trade_direction == 'long' and fvg.get('type') == 'bullish') or \
            (trade_direction == 'short' and fvg.get('type') == 'bearish')
            for fvg in fvg_data
        )
        if is_fvg_aligned:
            score += 15
            breakdown['fvg'] = '+15 (Найден релевантный FVG)'

    # 5. Объем (+10 баллов)
    if volume_data and volume_data.get('rvol', 0) > 1.5:
        score += 10
        breakdown['volume'] = '+10 (Подтверждение объемом RVOL > 1.5)'

    # 6. Макро-контекст (+10 баллов) - Заглушка
    if macro_data and macro_data.get('confirms', False):
        score += 10
        breakdown['macro'] = '+10 (Макро-фон подтверждает)'

    # Определение итогового решения
    decision = "Ignore"
    if score >= 70:
        decision = "A+"
    elif score >= 40:
        decision = "Watchlist"

    return {
        'total_score': score,
        'decision': decision,
        'breakdown': breakdown
    }