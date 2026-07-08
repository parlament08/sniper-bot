from typing import Dict, List, Optional
import pandas as pd

def calculate_setup_score(
    trade_direction: str,
    current_price: float,
    trend_data: Optional[Dict],
    context_structure_data: Optional[Dict], # 1H Structure
    trigger_structure_data: Optional[Dict], # 15m Structure
    sfp_data_in_window: Optional[Dict],
    fvg_test_data: Optional[Dict],
    fvg_data: List[Dict],
    macro_data: Optional[Dict]
) -> Dict:
    """
    Рассчитывает скоринговую оценку для торгового сетапа на основе набора технических факторов.
    Использует концепцию "Окна памяти" (Lookback Window) для SFP и FVG.
    Реализует логику "Double Confirmation" (1H Context + 15m Trigger).

    Args:
        trade_direction (str): Направление сделки ('long' или 'short').
        current_price (float): Текущая цена закрытия для проверки инвалидации.
        trend_data (Optional[Dict]): Данные о тренде из `evaluate_trend`.
        context_structure_data (Optional[Dict]): Данные о сломе 1H структуры (Контекст).
        trigger_structure_data (Optional[Dict]): Данные о сломе 15m структуры (Триггер).
        sfp_data_in_window (Optional[Dict]): Данные о захвате ликвидности (SFP) в ОКНЕ ПАМЯТИ.
        fvg_test_data (Optional[Dict]): Данные о последнем тесте FVG (включая индекс).
        fvg_data (List[Dict]): Список всех FVG для проверки на инвалидацию.
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

    # 2. Структура (Double Confirmation: 1H Context + 15m Trigger) - Иерархическая оценка
    score_structure = 0
    structure_desc = '0 (Нет валидной структуры)'
    confirmation_reason = None # Будет использоваться для проверки в секции ликвидности

    # Определяем наличие и тип структурных событий на обоих ТФ
    is_context_aligned = False
    context_struct_type = ''
    if context_structure_data:
        if (trade_direction == 'long' and 'bullish' in context_structure_data.get('type', '')) or \
           (trade_direction == 'short' and 'bearish' in context_structure_data.get('type', '')):
            is_context_aligned = True
            context_struct_type = 'CHoCH' if 'choch' in context_structure_data.get('type', '') else 'BOS'

    is_trigger_aligned = False
    trigger_struct_type = ''
    if trigger_structure_data:
        if (trade_direction == 'long' and 'bullish' in trigger_structure_data.get('type', '')) or \
           (trade_direction == 'short' and 'bearish' in trigger_structure_data.get('type', '')):
            is_trigger_aligned = True
            trigger_struct_type = 'CHoCH' if 'choch' in trigger_structure_data.get('type', '') else 'BOS'
            
            # --- Логика поиска подтверждения для триггера ---
            if fvg_test_data:
                trigger_time = trigger_structure_data['index']
                fvg_test_time = fvg_test_data['index']
                
                # Триггер (CHoCH) должен произойти ПОСЛЕ теста FVG и в пределах небольшого окна
                if trigger_time > fvg_test_time:
                    time_delta = trigger_time - fvg_test_time
                    is_within_window = False
                    if isinstance(trigger_time, (int, float)):
                        if trigger_time > 1e11: # ms
                            if time_delta <= 5 * 15 * 60 * 1000: is_within_window = True
                        elif trigger_time > 1e8: # s
                            if time_delta <= 5 * 15 * 60: is_within_window = True
                        else: # RangeIndex
                            if time_delta <= 5: is_within_window = True
                    else: # DatetimeIndex
                        if time_delta <= pd.Timedelta(minutes=75): # 5 * 15
                            is_within_window = True
                    
                    if is_within_window:
                        confirmation_reason = "in POI"
            
            if not confirmation_reason and sfp_data_in_window:
                is_sfp_aligned_for_trigger = (trade_direction == 'long' and 'bullish' in sfp_data_in_window.get('type', '')) or \
                                             (trade_direction == 'short' and 'bearish' in sfp_data_in_window.get('type', ''))
                if is_sfp_aligned_for_trigger:
                    trigger_time = trigger_structure_data['index']
                    sfp_time = sfp_data_in_window['index']
                    if trigger_time > sfp_time:
                        time_delta = trigger_time - sfp_time
                        is_within_window = False
                        if isinstance(trigger_time, (int, float)):
                            if trigger_time > 1e11: # ms
                                if time_delta <= 5 * 15 * 60 * 1000: is_within_window = True
                            elif trigger_time > 1e8: # s
                                if time_delta <= 5 * 15 * 60: is_within_window = True
                            else: # RangeIndex
                                if time_delta <= 5: is_within_window = True
                        else: # DatetimeIndex
                            if time_delta <= pd.Timedelta(minutes=75): is_within_window = True
                        if is_within_window:
                            confirmation_reason = "after SFP"

    # Иерархическая логика начисления баллов
    if is_trigger_aligned and confirmation_reason:
        # 1. Высший приоритет: есть подтвержденный 15m триггер
        if is_context_aligned:
            # A++: Двойное подтверждение
            score_structure = 30
            structure_desc = f"+30 (1H {context_struct_type} & 15m {trigger_struct_type} ({confirmation_reason}))"
        else:
            # A+: Только подтвержденный 15m триггер
            score_structure = 20
            structure_desc = f"+20 (15m {trigger_struct_type} ({confirmation_reason}))"
    elif is_context_aligned:
        # 2. Средний приоритет: есть только 1H контекст
        score_structure = 10
        structure_desc = f"+10 (1H {context_struct_type} only)"
    elif is_trigger_aligned: # and not confirmation_reason
        # 3. Низкий приоритет: есть неподтвержденный 15m триггер
        score_structure = 5
        structure_desc = f"+5 (15m {trigger_struct_type} - No Confirmation)"

    score += score_structure
    breakdown['structure'] = structure_desc

    # 3. Ликвидность (+20 баллов)
    # Баллы за SFP начисляются только если он НЕ был использован для подтверждения CHoCH,
    # чтобы избежать двойного скоринга одного и того же события.
    sfp_used_for_confirmation = (confirmation_reason == "after SFP")
    if sfp_data_in_window and not sfp_used_for_confirmation:
        is_sfp_aligned = (trade_direction == 'long' and 'bullish' in sfp_data_in_window.get('type', '')) or \
                         (trade_direction == 'short' and 'bearish' in sfp_data_in_window.get('type', ''))
        
        if is_sfp_aligned:
            score += 20
            breakdown['liquidity'] = '+20 (SFP на 1H свинге)'

    # 4. FVG (+15 баллов) - Память + Инвалидация пробоем
    breakdown['fvg'] = '0 (Зона не тестировалась)'
    if fvg_test_data:
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
    # Сначала проверяем более важный 15m триггер
    is_trigger_aligned = trigger_structure_data and (
        (trade_direction == 'long' and 'bullish' in trigger_structure_data.get('type', '')) or
        (trade_direction == 'short' and 'bearish' in trigger_structure_data.get('type', ''))
    )
    if is_trigger_aligned and trigger_structure_data.get('rvol', 0) > 1.5:
        score += 10
        breakdown['volume'] = '+10 (Подтверждение объемом на 15m триггере)'
    # Если на 15м нет, проверяем 1H контекст
    else:
        is_context_aligned = context_structure_data and (
            (trade_direction == 'long' and 'bullish' in context_structure_data.get('type', '')) or
            (trade_direction == 'short' and 'bearish' in context_structure_data.get('type', ''))
        )
        if is_context_aligned and context_structure_data.get('rvol', 0) > 1.5:
            score += 10
            breakdown['volume'] = '+10 (Подтверждение объемом на 1H сломе)'

    # 6. Макро-контекст (+10 баллов)
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