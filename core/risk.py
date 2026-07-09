from typing import Dict, List, Optional
import pandas as pd


def _structure_label(structure_data: Optional[Dict]) -> str:
    if not structure_data:
        return ''

    struct_type = 'CHoCH' if 'choch' in structure_data.get('type', '') else 'BOS'
    quality_score = structure_data.get('quality_score')

    if quality_score is None:
        return struct_type

    displacement_ratio = structure_data.get('displacement_ratio')
    body_ratio = structure_data.get('body_ratio')
    confidence = structure_data.get('confidence')
    metrics = [f"{struct_type} Q{quality_score}"]

    if displacement_ratio is not None:
        metrics.append(f"DR{float(displacement_ratio):.2f}")
    if body_ratio is not None:
        metrics.append(f"BR{float(body_ratio):.2f}")
    if confidence is not None:
        metrics.append(f"C{int(confidence)}")

    return " ".join(metrics)


def _sfp_label(sfp_data: Optional[Dict]) -> str:
    if not sfp_data:
        return 'SFP'

    quality_score = sfp_data.get('quality_score')
    if quality_score is None:
        return 'SFP'

    liquidity_depth = sfp_data.get('liquidity_depth')
    rejection_strength = sfp_data.get('rejection_strength')
    metrics = [f"SFP Q{quality_score}"]

    if liquidity_depth is not None:
        metrics.append(f"D{float(liquidity_depth):.2f}")
    if rejection_strength is not None:
        metrics.append(f"R{int(rejection_strength)}")

    return " ".join(metrics)


def _sfp_quality_tier(sfp_data: Optional[Dict]) -> str:
    if not sfp_data:
        return 'none'

    quality_score = sfp_data.get('quality_score')
    liquidity_depth = sfp_data.get('liquidity_depth')
    rejection_strength = sfp_data.get('rejection_strength')

    if quality_score is None or liquidity_depth is None or rejection_strength is None:
        return 'legacy_strong' if sfp_data.get('rvol', 0) > 1.5 else 'legacy'

    quality_score = int(quality_score)
    liquidity_depth = float(liquidity_depth)
    rejection_strength = int(rejection_strength)

    if liquidity_depth < 0.15 or rejection_strength < 60:
        return 'weak'
    if quality_score >= 80 and rejection_strength >= 75:
        return 'strong'
    if quality_score >= 70:
        return 'medium'
    return 'weak'


def _sfp_liquidity_score(sfp_data: Optional[Dict]) -> int:
    tier = _sfp_quality_tier(sfp_data)
    if tier in ('strong', 'legacy_strong'):
        return 20
    if tier == 'medium':
        return 10
    if tier == 'weak':
        return 5
    return 0


def _structure_volume_score(structure_data: Optional[Dict], confirmation_reason: Optional[str] = None) -> int:
    if not structure_data or structure_data.get('rvol', 0) <= 1.5:
        return 0

    if confirmation_reason:
        return 10

    return 5 if structure_data.get('quality_score', 0) >= 90 else 0


def _pd_get(premium_discount_data, key: str, default=None):
    if not premium_discount_data:
        return default
    if hasattr(premium_discount_data, 'get'):
        return premium_discount_data.get(key, default)
    return getattr(premium_discount_data, key, default)


def _premium_discount_label(premium_discount_data) -> str:
    zone = _pd_get(premium_discount_data, 'zone')
    distance = _pd_get(premium_discount_data, 'distance_from_equilibrium_percent')
    range_distance = _pd_get(premium_discount_data, 'distance_from_equilibrium_range_percent')

    if zone is None:
        return '0'
    if distance is None:
        return f"{zone}"
    label = f"{zone} ({float(distance):+.2f}% от EQ"
    if range_distance is not None:
        label += f", {float(range_distance):.2f}% range"
    return f"{label})"


def select_best_setup(long_score: Dict, short_score: Dict) -> tuple:
    long_total = long_score.get('total_score', 0)
    short_total = short_score.get('total_score', 0)

    if long_total <= 0 and short_total <= 0:
        return long_score, 'NEUTRAL'

    if long_total >= short_total:
        return long_score, 'LONG'

    return short_score, 'SHORT'


def format_setup_direction(direction: str, total_score: int, decision: str, no_trade_threshold: int = 40) -> tuple:
    if decision == 'Ignore' and total_score < no_trade_threshold:
        return 'NO TRADE', '⚪'

    setup_emoji = '🟢' if direction == 'LONG' else '🔴' if direction == 'SHORT' else '⚪'
    return direction, setup_emoji


def resolve_session_decision(score_result: Dict, in_kill_zone: bool, watch_only_threshold: int = 85) -> str:
    decision = score_result.get('decision', 'Ignore')
    total_score = score_result.get('total_score', 0)

    if in_kill_zone:
        return decision
    if total_score >= watch_only_threshold:
        return 'A+ WATCH ONLY'
    return 'Ignore'


def calculate_setup_score(
    trade_direction: str,
    current_price: float,
    trend_data: Optional[Dict],
    context_structure_data: Optional[Dict], # 1H Structure
    trigger_structure_data: Optional[Dict], # 15m Structure
    sfp_data_in_window: Optional[Dict],
    fvg_test_data: Optional[Dict],
    fvg_data: List[Dict],
    macro_data: Optional[Dict],
    premium_discount_data: Optional[Dict] = None,
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
        'macro': '0',
        'premium_discount': '0',
    }

    pd_valid = True
    if premium_discount_data:
        pd_valid = (
            _pd_get(premium_discount_data, 'valid_for_buy', False)
            if trade_direction == 'long'
            else _pd_get(premium_discount_data, 'valid_for_sell', False)
        )
        breakdown['premium_discount'] = (
            f"OK ({_premium_discount_label(premium_discount_data)})"
            if pd_valid
            else f"BLOCK ({_premium_discount_label(premium_discount_data)})"
        )

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
    context_struct_label = ''
    if context_structure_data:
        if (trade_direction == 'long' and 'bullish' in context_structure_data.get('type', '')) or \
           (trade_direction == 'short' and 'bearish' in context_structure_data.get('type', '')):
            is_context_aligned = True
            context_struct_label = _structure_label(context_structure_data)

    is_trigger_aligned = False
    trigger_struct_label = ''
    if trigger_structure_data:
        if (trade_direction == 'long' and 'bullish' in trigger_structure_data.get('type', '')) or \
           (trade_direction == 'short' and 'bearish' in trigger_structure_data.get('type', '')):
            is_trigger_aligned = True
            trigger_struct_label = _structure_label(trigger_structure_data)
            
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
            structure_desc = f"+30 (1H {context_struct_label} & 15m {trigger_struct_label}, {confirmation_reason})"
        else:
            # A+: Только подтвержденный 15m триггер
            score_structure = 20
            structure_desc = f"+20 (15m {trigger_struct_label}, {confirmation_reason})"
    elif is_context_aligned:
        # 2. Средний приоритет: есть только 1H контекст
        score_structure = 10
        structure_desc = f"+10 (1H {context_struct_label} only)"
    elif is_trigger_aligned: # and not confirmation_reason
        # 3. Низкий приоритет: есть неподтвержденный 15m триггер
        score_structure = 5
        structure_desc = f"+5 (15m {trigger_struct_label}, без POI/SFP confirmation)"

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
            sfp_score = _sfp_liquidity_score(sfp_data_in_window)
            if sfp_score > 0:
                score += sfp_score
                breakdown['liquidity'] = f"+{sfp_score} ({_sfp_label(sfp_data_in_window)} на 1H свинге)"
            else:
                breakdown['liquidity'] = f"0 ({_sfp_label(sfp_data_in_window)} слабый захват)"

    # 4. FVG (+15 баллов) - Память + Инвалидация пробоем
    breakdown['fvg'] = '0 (Зона не тестировалась)'
    if fvg_test_data:
        is_fvg_zone_valid = False
        active_fvg_quality = None
        # Проверяем, что хотя бы одна релевантная зона не пробита
        for fvg in fvg_data:
            if fvg.get('invalidated', False):
                continue

            if trade_direction == 'long' and fvg.get('type') == 'bullish':
                # Зона валидна, если цена находится ВНУТРИ или КАСАЕТСЯ ее границ
                if fvg['bottom'] <= current_price <= fvg['top']:
                    is_fvg_zone_valid = True
                    active_fvg_quality = fvg.get('quality_score')
                    break
            elif trade_direction == 'short' and fvg.get('type') == 'bearish':
                # Зона валидна, если цена находится ВНУТРИ или КАСАЕТСЯ ее границ
                if fvg['bottom'] <= current_price <= fvg['top']:
                    is_fvg_zone_valid = True
                    active_fvg_quality = fvg.get('quality_score')
                    break
        
        if is_fvg_zone_valid:
            score += 15
            quality_text = f" Q{active_fvg_quality}" if active_fvg_quality is not None else ""
            breakdown['fvg'] = f"+15 (Тест FVG{quality_text} в окне 5 часов, зона удержана)"
        else:
            breakdown['fvg'] = '0 (Зона пробита после теста)'

    # 5. Объем (+10 баллов) - Привязан к триггерным паттернам (SFP/BOS)
    breakdown['volume'] = '0 (Нет подтверждения аномальным объемом)'
    
    # Приоритет 1: Объем на свече SFP (сбор ликвидности)
    is_sfp_aligned = sfp_data_in_window and (
        (trade_direction == 'long' and 'bullish' in sfp_data_in_window.get('type', '')) or
        (trade_direction == 'short' and 'bearish' in sfp_data_in_window.get('type', ''))
    )
    if (
        is_sfp_aligned
        and _sfp_quality_tier(sfp_data_in_window) in ('strong', 'legacy_strong')
        and sfp_data_in_window.get('volume_confirmed', sfp_data_in_window.get('rvol', 0) > 1.5)
    ):
        score += 10
        breakdown['volume'] = '+10 (Подтверждение объемом на сильном SFP)'
    elif is_sfp_aligned and sfp_data_in_window.get('volume_confirmed', sfp_data_in_window.get('rvol', 0) > 1.5):
        breakdown['volume'] = '0 (Объем есть, но SFP не strong-tier)'
    
    # Приоритет 2: Объем на свече слома структуры (BOS/CHoCH)
    # Сначала проверяем более важный 15m триггер
    is_trigger_aligned = trigger_structure_data and (
        (trade_direction == 'long' and 'bullish' in trigger_structure_data.get('type', '')) or
        (trade_direction == 'short' and 'bearish' in trigger_structure_data.get('type', ''))
    )
    is_context_aligned = context_structure_data and (
        (trade_direction == 'long' and 'bullish' in context_structure_data.get('type', '')) or
        (trade_direction == 'short' and 'bearish' in context_structure_data.get('type', ''))
    )
    should_prioritize_context_volume = bool(is_context_aligned and not confirmation_reason)

    context_volume_score = _structure_volume_score(context_structure_data)
    trigger_volume_score = _structure_volume_score(trigger_structure_data, confirmation_reason) if is_trigger_aligned else 0

    if not is_sfp_aligned and should_prioritize_context_volume and context_volume_score > 0:
        score += context_volume_score
        breakdown['volume'] = f"+{context_volume_score} (Объем на 1H сломе с Q>=90)"
    elif (
        not is_sfp_aligned
        and should_prioritize_context_volume
        and context_structure_data.get('rvol', 0) > 1.5
    ):
        breakdown['volume'] = '0 (Объем есть, но 1H структура Q<90)'
    elif not is_sfp_aligned and is_trigger_aligned and trigger_volume_score > 0:
        score += trigger_volume_score
        if trigger_volume_score == 10:
            breakdown['volume'] = '+10 (Объем на 15m триггере с POI/SFP confirmation)'
        else:
            breakdown['volume'] = '+5 (Объем на экстремальном 15m BOS без POI/SFP)'
    elif (
        not is_sfp_aligned
        and is_trigger_aligned
        and trigger_structure_data.get('rvol', 0) > 1.5
    ):
        breakdown['volume'] = '0 (Объем есть, но 15m структура без POI/SFP и Q<90)'
    # Если на 15м нет, проверяем 1H контекст
    elif not is_sfp_aligned:
        if is_context_aligned and context_volume_score > 0:
            score += context_volume_score
            breakdown['volume'] = f"+{context_volume_score} (Объем на 1H сломе с Q>=90)"
        elif is_context_aligned and context_structure_data.get('rvol', 0) > 1.5:
            breakdown['volume'] = '0 (Объем есть, но 1H структура Q<90)'

    # 6. Макро-контекст (+10 баллов)
    if macro_data:
        m_score = macro_data.get('score', 0)
        m_reason = macro_data.get('reason', 'Нет данных')
        score += m_score
        breakdown['macro'] = f"+{m_score} ({m_reason})" if m_score > 0 else f"0 ({m_reason})"

    raw_score = score

    if not pd_valid:
        breakdown['premium_discount'] = (
            f"BLOCK ({_premium_discount_label(premium_discount_data)}, score {raw_score}->0)"
        )
        score = 0

    # Определение итогового решения на основе суммы баллов
    decision = "Ignore"
    
    if score >= 70:
        decision = "A+"
    elif score >= 40:
        decision = "Watchlist"
    else:
        decision = "Ignore"

    return {
        'raw_score': raw_score,
        'total_score': score,
        'decision': decision,
        'breakdown': breakdown
    }
