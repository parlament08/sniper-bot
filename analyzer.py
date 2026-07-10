import os
import time
import json
import requests
import pandas as pd
import google.generativeai as genai
from html import escape
from datetime import time as dt_time
from dotenv import load_dotenv

from core.logger import logger
from services.market_data import fetch_candles
#from services.macro_context import get_macro_context, check_macro_confirmation
from services.macro_context import get_macro_context, evaluate_macro_score
from core.structure import (
    BOSConfig,
    MarketStructureConfig,
    SFPConfig,
    detect_sfp_against_liquidity_levels,
    detect_sfp,
    detect_structure_break,
    evaluate_market_structure,
    find_fvg,
    find_swings,
)
from core.indicators import calculate_ema, calculate_atr, calculate_rvol, calculate_adx, evaluate_trend
from core.liquidity import build_liquidity_map
from core.premium_discount import evaluate_premium_discount
from core.risk import calculate_setup_score, format_setup_direction, resolve_session_decision, select_best_setup
from core.state_machine import SniperEvent, SniperStateMachine

# Загружаем переменные окружения из .env файла
load_dotenv()

# Инициализация API Gemini
api_key = os.environ.get("GEMINI_API_KEY")
if not api_key:
    raise ValueError("❌ Не найден GEMINI_API_KEY. Выполни в терминале: export GEMINI_API_KEY='твой_ключ'")

genai.configure(api_key=api_key)

# Настройки Telegram
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
TELEGRAM_MAX_MESSAGE_LENGTH = 3900

A_PLUS_NARRATOR_INSTRUCTION = """
Ты — профессиональный финансовый диктор. Оформи этот JSON с А+ сетапом в красивый HTML для Telegram с тегами <b> и <code>. Ничего не выдумывай от себя.
"""

model = genai.GenerativeModel(model_name='models/gemini-3.1-flash-lite')
COINS_LIST = ['BTC', 'ETH', 'SOL', 'XRP', 'ADA', 'LINK', 'INJ', 'HYPE', 'LTC', 'DOT']
last_alert_time = {coin: 0 for coin in COINS_LIST}

def send_telegram_alert(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"}
    try:
        res = requests.post(url, data=payload, timeout=10)
        if res.status_code != 200:
            logger.error(f"Ошибка Telegram API: {res.text}")
    except Exception as e:
        logger.error(f"Не удалось отправить пуш в Telegram: {e}")


def send_telegram_blocks(header_lines, body_blocks, max_length=TELEGRAM_MAX_MESSAGE_LENGTH):
    header = "\n".join(header_lines)
    current_blocks = []

    def flush():
        if not current_blocks:
            return
        send_telegram_alert("\n".join([header] + current_blocks))
        current_blocks.clear()

    for block in body_blocks:
        projected_blocks = current_blocks + [block]
        projected_length = len("\n".join([header] + projected_blocks))

        if current_blocks and projected_length > max_length:
            flush()
            projected_blocks = [block]
            projected_length = len("\n".join([header] + projected_blocks))

        if projected_length > max_length:
            logger.warning("Dashboard block exceeds Telegram safe length; sending it as a standalone message.")
            send_telegram_alert("\n".join([header, block]))
            continue

        current_blocks.append(block)

    flush()


def _html_text(value):
    return escape(str(value), quote=False)


def _resolve_premium_discount(current_price, range_candidates):
    for candidate in range_candidates:
        if len(candidate) == 3:
            timeframe, swing_highs, swing_lows = candidate
        else:
            swing_highs, swing_lows = candidate
            timeframe = 'unknown'
        if swing_highs.empty or swing_lows.empty:
            continue
        try:
            return evaluate_premium_discount(
                current_price,
                swing_highs,
                swing_lows,
                range_timeframe=timeframe,
                range_type='last_swing',
            )
        except ValueError:
            continue
    return None


def _level_value(level, key, default=None):
    if level is None:
        return default
    if hasattr(level, 'get'):
        return level.get(key, default)
    return getattr(level, key, default)


def _format_liquidity_level(level):
    if level is None:
        return 'none'

    level_type = _level_value(level, 'type', 'unknown')
    price = _level_value(level, 'price', 0.0)
    strength = _level_value(level, 'strength', 0.0)
    distance_atr = _level_value(level, 'distance_atr', 0.0)
    touches = _level_value(level, 'touches', 0)
    swept = _level_value(level, 'swept', False)
    state = 'swept' if swept else 'fresh'
    return f"{level_type} {float(price):.4f} S{int(strength)} D{float(distance_atr):.2f}ATR T{touches} {state}"


def _format_liquidity_map(liquidity_map):
    if liquidity_map is None:
        return '0'

    nearest_buy = _level_value(liquidity_map, 'nearest_buy_side')
    nearest_sell = _level_value(liquidity_map, 'nearest_sell_side')
    strongest_buy = _level_value(liquidity_map, 'strongest_buy_side')
    strongest_sell = _level_value(liquidity_map, 'strongest_sell_side')

    return (
        f"Buy: {_format_liquidity_level(nearest_buy)} | "
        f"Sell: {_format_liquidity_level(nearest_sell)} | "
        f"Strongest B/S: {_format_liquidity_level(strongest_buy)} / {_format_liquidity_level(strongest_sell)}"
    )


def _build_liquidity_map_before_candle(
    candle_index,
    candle_close,
    df_1h_closed,
    swing_highs_1h,
    swing_lows_1h,
):
    history_1h = df_1h_closed[df_1h_closed.index < candle_index]
    if history_1h.empty:
        return None

    highs_before = swing_highs_1h[swing_highs_1h.index < candle_index]
    lows_before = swing_lows_1h[swing_lows_1h.index < candle_index]
    if highs_before.empty and lows_before.empty:
        return None

    return build_liquidity_map(
        history_1h,
        highs_before,
        lows_before,
        atr_series=history_1h['atr'] if 'atr' in history_1h.columns else None,
        current_price=float(candle_close),
    )


def _direction_to_state_direction(direction):
    if direction == 'LONG':
        return 'bullish'
    if direction == 'SHORT':
        return 'bearish'
    return None


def _format_bias(trend_data):
    if not trend_data or 'is_bullish' not in trend_data:
        return 'Н/Д'
    return 'ВВЕРХ ↗️ по EMA99' if trend_data['is_bullish'] else 'ВНИЗ ↘️ по EMA99'


def _format_market_structure(market_structure):
    if market_structure is None:
        return 'Н/Д'
    trend = market_structure.get('trend', 'neutral')
    confidence = market_structure.get('confidence', 0)
    reason = market_structure.get('reason', '')
    return f"{str(trend).upper()} C{confidence} ({reason})"


def _format_adx(trend_data):
    if not trend_data or trend_data.get('adx_value') is None:
        return 'Н/Д'
    adx_value = float(trend_data.get('adx_value', 0))
    p_di = trend_data.get('p_di')
    n_di = trend_data.get('n_di')
    mode = 'strong' if trend_data.get('strength') == 'strong' else 'weak/neutral'
    di_text = ''
    if p_di is not None and n_di is not None:
        di_text = f" +DI {float(p_di):.2f} / -DI {float(n_di):.2f}"
    return f"ADX {adx_value:.2f}{di_text} | {mode}"


def _event_direction(event):
    event_type = str(event.get('type', '')) if event else ''
    if 'bullish' in event_type:
        return 'bullish'
    if 'bearish' in event_type:
        return 'bearish'
    return None


def _event_kind(event):
    event_type = str(event.get('type', '')) if event else ''
    if 'choch' in event_type:
        return 'choch'
    if 'bos' in event_type:
        return 'bos'
    return None


def _event_is_strong(event, min_quality=70, min_displacement=0.8):
    if not event:
        return False
    return (
        float(event.get('quality_score', 0)) >= min_quality
        and float(event.get('displacement_ratio', 0)) >= min_displacement
    )


def _sfp_is_strong(sfp_data, direction):
    if not sfp_data:
        return False
    sfp_direction = _event_direction(sfp_data)
    return (
        sfp_direction == direction
        and int(sfp_data.get('quality_score', 0)) >= 80
        and float(sfp_data.get('liquidity_depth', 0)) >= 0.15
        and int(sfp_data.get('rejection_strength', 0)) >= 75
    )


def _has_strong_reversal_context(sfp_data, context_structure, trigger_structure):
    events = [event for event in (context_structure, trigger_structure) if event]
    for direction in ('bullish', 'bearish'):
        has_sfp = _sfp_is_strong(sfp_data, direction)
        has_choch = any(
            _event_direction(event) == direction and _event_kind(event) == 'choch' and _event_is_strong(event)
            for event in events
        )
        has_bos = any(
            _event_direction(event) == direction and _event_kind(event) == 'bos' and _event_is_strong(event)
            for event in events
        )
        if has_sfp and has_choch and has_bos:
            return direction
    return None


def _cap_low_adx_override(score_result, override_direction):
    if not override_direction:
        return score_result

    score_result['breakdown']['trend'] = (
        f"{score_result['breakdown'].get('trend', '0')} "
        f"(Low ADX override: strong {override_direction} reversal, A+ blocked)"
    )
    if score_result.get('total_score', 0) >= 70:
        score_result['raw_score'] = score_result.get('raw_score', score_result.get('total_score', 0))
        score_result['total_score'] = 69
        score_result['decision'] = 'Watchlist'
    return score_result


def _detect_recent_structure_events(df, swing_highs, swing_lows, timeframe_minutes, right_bars, config, lookback=24, limit=4):
    events = []
    window = df.tail(lookback)
    for index, candle in window.iterrows():
        future_candles = window[window.index > index]
        highs_before = swing_highs[swing_highs.index < index]
        lows_before = swing_lows[swing_lows.index < index]
        event = detect_structure_break(
            candle,
            highs_before,
            lows_before,
            right_bars=right_bars,
            timeframe_minutes=timeframe_minutes,
            config=config,
            future_candles=future_candles,
        )
        if event:
            events.append(event.to_dict() if hasattr(event, 'to_dict') else dict(event))
    return events[-limit:]


def _structure_for_state_machine(direction, market_structure, context_structure, trigger_structure):
    state_direction = _direction_to_state_direction(direction)
    if state_direction is None:
        return market_structure

    selected_structure = None
    for candidate in (trigger_structure, context_structure):
        if candidate and state_direction in str(candidate.get('type', '')):
            selected_structure = candidate
            break

    return {
        'trend': market_structure.get('trend') if market_structure else 'neutral',
        'neutral': bool(market_structure and market_structure.get('trend') == 'neutral'),
        'direction': state_direction,
        'type': selected_structure.get('type') if selected_structure else '',
        'detected': bool(selected_structure),
        'bos_detected': bool(selected_structure and 'bos' in str(selected_structure.get('type', ''))),
        'choch_detected': bool(selected_structure and 'choch' in str(selected_structure.get('type', ''))),
    }


def _fvg_for_state_machine(direction, fvg_test_data, fvg_data, current_price):
    state_direction = _direction_to_state_direction(direction)
    if state_direction is None or not fvg_test_data:
        return None

    target_type = 'bullish' if state_direction == 'bullish' else 'bearish'
    for fvg in fvg_data:
        if fvg.get('type') != target_type or fvg.get('invalidated', False):
            continue
        if fvg.get('tested', False) or fvg_test_data:
            return {
                'detected': True,
                'direction': state_direction,
                'type': target_type,
                'tested': True,
                'invalidated': False,
                'end_index': fvg.get('end_index'),
                'test_index': fvg_test_data.get('index') if fvg_test_data else None,
            }
    return None


def _event_sort_key(index):
    if index is None:
        return 0.0
    if isinstance(index, (int, float)):
        return float(index)
    try:
        return float(pd.Timestamp(index).value)
    except (TypeError, ValueError):
        return 0.0


def _scenario_events_for_state_machine(
    direction,
    market_structure,
    premium_discount_data,
    sfp_data,
    context_structure,
    trigger_structure,
    fvg_result,
    fvg_test_data,
):
    state_direction = _direction_to_state_direction(direction)
    if state_direction is None:
        return []

    events = []
    pd_valid = (
        premium_discount_data
        and (
            premium_discount_data.get('valid_for_buy', False)
            if state_direction == 'bullish'
            else premium_discount_data.get('valid_for_sell', False)
        )
    )
    htf_ok = bool(market_structure and market_structure.get('trend') == state_direction) or bool(pd_valid)
    if htf_ok:
        events.append((-1.0, SniperEvent.HTF_CONTEXT_CONFIRMED))
    if pd_valid:
        events.append((-0.5, SniperEvent.POI_TOUCHED))

    if sfp_data and state_direction in str(sfp_data.get('type', '')):
        events.append((_event_sort_key(sfp_data.get('index')), SniperEvent.LIQUIDITY_SWEEP_CONFIRMED))

    for structure in (context_structure, trigger_structure):
        if not structure or state_direction not in str(structure.get('type', '')):
            continue
        event_index = _event_sort_key(structure.get('index'))
        struct_type = str(structure.get('type', ''))
        if 'choch' in struct_type:
            events.append((event_index, SniperEvent.CHOCH_CONFIRMED))
        if structure is trigger_structure and 'bos' in struct_type:
            events.append((event_index, SniperEvent.BOS_CONFIRMED))

    if fvg_result:
        created_index = fvg_result.get('end_index')
        if created_index is not None:
            events.append((_event_sort_key(created_index), SniperEvent.FVG_CREATED))
        test_index = fvg_result.get('test_index') or (fvg_test_data or {}).get('index')
        if test_index is not None:
            events.append((_event_sort_key(test_index), SniperEvent.FVG_RETESTED))

    displacement_index = None
    if fvg_test_data:
        displacement_index = fvg_test_data.get('displacement_index')
    if displacement_index is None and fvg_result:
        displacement_index = fvg_result.get('displacement_index')
    if displacement_index is not None:
        events.append((_event_sort_key(displacement_index), SniperEvent.DISPLACEMENT_CONFIRMED))

    ordered = sorted(events, key=lambda item: item[0])
    deduped = []
    seen = set()
    for _, event in ordered:
        if event in seen:
            continue
        seen.add(event)
        deduped.append(event)
    return deduped


def _state_machine_diagnostic(
    direction,
    market_structure,
    premium_discount_data,
    liquidity_map,
    sfp_data,
    context_structure,
    trigger_structure,
    fvg_test_data,
    fvg_data,
    current_price,
    current_bar,
):
    state_direction = _direction_to_state_direction(direction)
    if state_direction is None:
        return '0', None

    structure_result = _structure_for_state_machine(direction, market_structure, context_structure, trigger_structure)
    fvg_result = _fvg_for_state_machine(direction, fvg_test_data, fvg_data, current_price)
    displacement_result = None

    selected_structure = trigger_structure or context_structure
    if selected_structure and state_direction in str(selected_structure.get('type', '')):
        displacement_result = {
            'valid': selected_structure.get('quality_score', 0) >= 70,
            'direction': state_direction,
        }

    machine = SniperStateMachine(state_direction)
    scenario_events = _scenario_events_for_state_machine(
        direction,
        market_structure,
        premium_discount_data,
        sfp_data,
        context_structure,
        trigger_structure,
        fvg_result,
        fvg_test_data,
    )
    result = machine.update(
        events=scenario_events,
        current_bar=current_bar,
        structure_result=structure_result,
        premium_discount_result=premium_discount_data,
    )

    if result.invalidation_reason:
        return f"{result.state.value} C{int(result.confidence)} ({result.invalidation_reason})", result
    completed = len(result.completed_steps)
    missing_next = result.missing_steps[0] if result.missing_steps else 'ready'
    return f"{result.state.value} C{int(result.confidence)} ({completed}/8, next: {missing_next})", result


def prepare_and_analyze(coin, macro_context):
    df_4h = fetch_candles(coin, '4h', limit=300)
    df_1h = fetch_candles(coin, '1h', limit=300)
    df_15m = fetch_candles(coin, '15m', limit=300)

    if df_4h is None or df_1h is None or df_15m is None or len(df_4h) < 100 or len(df_1h) < 100 or len(df_15m) < 100:
        logger.warning(f"Недостаточно данных для {coin}.")
        return None, None

    numeric_cols = ['open', 'high', 'low', 'close', 'volume']
    for col in numeric_cols:
        df_4h[col] = df_4h[col].astype(float)
        df_1h[col] = df_1h[col].astype(float)
        df_15m[col] = df_15m[col].astype(float)

    df_4h['ema99'] = calculate_ema(df_4h, 99)
    adx_df = calculate_adx(df_4h, 14)
    if adx_df is not None:
        df_4h = df_4h.join(adx_df)

    df_1h['atr'] = calculate_atr(df_1h, 14)
    df_1h['rvol'] = calculate_rvol(df_1h, 20)
    df_15m['atr'] = calculate_atr(df_15m, 14)
    df_15m['rvol'] = calculate_rvol(df_15m, 20)

    df_4h.dropna(inplace=True)
    df_1h.dropna(inplace=True)
    df_15m.dropna(inplace=True)

    if df_4h.empty or df_1h.empty or df_15m.empty:
        logger.warning(f"Недостаточно данных для {coin} после расчета индикаторов.")
        return None, None

    df_4h_closed = df_4h.iloc[:-1].copy()
    df_1h_closed = df_1h.iloc[:-1].copy()
    df_15m_closed = df_15m.iloc[:-1].copy()
    last_closed_15m = df_15m_closed.iloc[-1]
    window_15m = df_15m_closed.tail(100)
    window_1h = df_1h_closed.tail(100)
    
    # ❗️ ВАЖНО: Тренд оценивается по 4H данным для глобального контекста
    trend_data = evaluate_trend(df_4h_closed)

    # --- Анализ в окне памяти (20 свечей) ---
    sfp_data_in_window = None
    context_break_1h = None
    trigger_break_15m = None

    # 1. Ищем свинги на старшем и рабочих таймфреймах.
    # 1H берется напрямую с биржи, а не строится из 15m, чтобы контекст совпадал с биржевыми свечами.
    swing_highs_4h, swing_lows_4h = find_swings(df_4h_closed, left_bars=3, right_bars=2)
    swing_highs_1h, swing_lows_1h = find_swings(df_1h_closed, left_bars=3, right_bars=2)
    swing_highs_15m, swing_lows_15m = find_swings(df_15m_closed, left_bars=5, right_bars=3)
    current_price = float(last_closed_15m['close'])
    liquidity_map = build_liquidity_map(
        df_1h_closed,
        swing_highs_1h,
        swing_lows_1h,
        atr_series=df_1h_closed['atr'],
        current_price=current_price,
    )
    bos_config = BOSConfig(hold_confirmation_bars=1)
    sfp_config = SFPConfig(hold_confirmation_bars=1)
    recent_4h_structure_events = _detect_recent_structure_events(
        df_4h_closed,
        swing_highs_4h,
        swing_lows_4h,
        timeframe_minutes=240,
        right_bars=2,
        config=bos_config,
    )

    # Итерируемся по окну С КОНЦА, чтобы найти ПОСЛЕДНИЕ (самые релевантные) события SFP и BOS
    for index, candle in window_15m.iloc[::-1].iterrows():
        future_candles = window_15m[window_15m.index > index]
        # SFP ищем по старшим свингам (1H)
        swings_before_candle_h_1h = swing_highs_1h[swing_highs_1h.index < index]
        swings_before_candle_l_1h = swing_lows_1h[swing_lows_1h.index < index]

        # Ищем SFP (только если еще не нашли)
        if not sfp_data_in_window:
            historical_liquidity_map = _build_liquidity_map_before_candle(
                index,
                candle.get('close'),
                df_1h_closed,
                swing_highs_1h,
                swing_lows_1h,
            )
            sfp = detect_sfp_against_liquidity_levels(
                candle,
                _level_value(historical_liquidity_map, 'levels', []),
                config=sfp_config,
                future_candles=future_candles,
            )
            if not sfp:
                sfp = detect_sfp(
                    candle,
                    swings_before_candle_h_1h,
                    swings_before_candle_l_1h,
                    right_bars=2,
                    timeframe_minutes=60,
                    config=sfp_config,
                    future_candles=future_candles,
                )
            if sfp:
                sfp['rvol'] = candle.get('rvol', 0)
                sfp_data_in_window = sfp

        # Ищем 15m CHoCH (Триггер)
        if not trigger_break_15m:
            swings_before_candle_h_15m = swing_highs_15m[swing_highs_15m.index < index]
            swings_before_candle_l_15m = swing_lows_15m[swing_lows_15m.index < index]
            structure_break = detect_structure_break(
                candle,
                swings_before_candle_h_15m,
                swings_before_candle_l_15m,
                right_bars=3,
                timeframe_minutes=15,
                config=bos_config,
                future_candles=future_candles,
            )
            if structure_break:
                trigger_break_15m = structure_break

    for index, candle in window_1h.iloc[::-1].iterrows():
        if context_break_1h:
            break
        future_candles = window_1h[window_1h.index > index]
        swings_before_candle_h_1h = swing_highs_1h[swing_highs_1h.index < index]
        swings_before_candle_l_1h = swing_lows_1h[swing_lows_1h.index < index]
        structure_break = detect_structure_break(
            candle,
            swings_before_candle_h_1h,
            swings_before_candle_l_1h,
            right_bars=2,
            timeframe_minutes=60,
            config=bos_config,
            future_candles=future_candles,
        )
        if structure_break:
            context_break_1h = structure_break

    market_structure = evaluate_market_structure(
        df_4h_closed,
        swing_highs_4h,
        swing_lows_4h,
        trend_data=trend_data,
        recent_structure_events=recent_4h_structure_events,
        config=MarketStructureConfig(),
    )
    low_adx_override_direction = None
    if market_structure.trend == 'neutral' and market_structure.reason == 'ADX below neutral threshold':
        low_adx_override_direction = _has_strong_reversal_context(
            sfp_data_in_window,
            context_break_1h,
            trigger_break_15m,
        )

    if market_structure.trend == 'neutral' and not low_adx_override_direction:
        return {
            'total_score': 0,
            'decision': 'Ignore',
            'breakdown': {
                'trend': f"0 (Neutral market: {market_structure.reason})",
                'structure': '0 (Neutral market state)',
                'liquidity': '0',
                'fvg': '0',
                'volume': '0',
                'macro': '0',
                'premium_discount': '0',
                'liquidity_map': _format_liquidity_map(liquidity_map),
                'state_machine': '0',
                'htf_structure': _format_market_structure(market_structure),
                'adx': _format_adx(trend_data),
            },
        }, {
            'trend_data': trend_data,
            'market_structure': market_structure,
            'structure_data': None,
            'liquidity_map': liquidity_map,
            'state_machine': None,
            'direction': 'NEUTRAL',
            'last_closed_15m': last_closed_15m,
        }
        
    # Находим самый последний тест FVG в окне памяти
    all_fvgs = find_fvg(
        df_15m_closed,
        atr_series=df_15m_closed['atr'],
        rvol_series=df_15m_closed['rvol'],
        min_size_atr_ratio=0.5,
    )
    
    bullish_fvg_test_indices = []
    for fvg in (f for f in all_fvgs if f['type'] == 'bullish' and not f.get('invalidated', False)):
        test_candles = window_15m[(window_15m['low'] <= fvg['top']) & (window_15m['high'] >= fvg['bottom'])]
        if not test_candles.empty:
            bullish_fvg_test_indices.append(test_candles.index[-1])
    
    bearish_fvg_test_indices = []
    for fvg in (f for f in all_fvgs if f['type'] == 'bearish' and not f.get('invalidated', False)):
        test_candles = window_15m[(window_15m['low'] <= fvg['top']) & (window_15m['high'] >= fvg['bottom'])]
        if not test_candles.empty:
            bearish_fvg_test_indices.append(test_candles.index[-1])

    bullish_fvg_test_index = max(bullish_fvg_test_indices) if bullish_fvg_test_indices else None
    bearish_fvg_test_index = max(bearish_fvg_test_indices) if bearish_fvg_test_indices else None

    long_fvg_test_data = {'index': bullish_fvg_test_index} if bullish_fvg_test_index else None
    short_fvg_test_data = {'index': bearish_fvg_test_index} if bearish_fvg_test_index else None

    premium_discount_data = _resolve_premium_discount(
        current_price,
        (
            ('4H', swing_highs_4h, swing_lows_4h),
            ('1H', swing_highs_1h, swing_lows_1h),
            ('15m', swing_highs_15m, swing_lows_15m),
        ),
    )

    is_altcoin = coin != "BTC"
    
    # ✅ НОВАЯ ЛОГИКА МАКРО
    long_macro_score, long_macro_reason = evaluate_macro_score('long', macro_context, is_altcoin=is_altcoin)
    short_macro_score, short_macro_reason = evaluate_macro_score('short', macro_context, is_altcoin=is_altcoin)

    long_macro_data = {'score': long_macro_score, 'reason': long_macro_reason}
    short_macro_data = {'score': short_macro_score, 'reason': short_macro_reason}
    
    # ✅ ВОТ ЭТИ ДВЕ СТРОКИ НУЖНО ВЕРНУТЬ (Вызов калькулятора баллов):
    long_score = calculate_setup_score('long', current_price, trend_data, context_break_1h, trigger_break_15m, sfp_data_in_window, long_fvg_test_data, all_fvgs, long_macro_data, premium_discount_data)
    short_score = calculate_setup_score('short', current_price, trend_data, context_break_1h, trigger_break_15m, sfp_data_in_window, short_fvg_test_data, all_fvgs, short_macro_data, premium_discount_data)

    final_score_result, direction = select_best_setup(long_score, short_score)
    if low_adx_override_direction:
        selected_state_direction = _direction_to_state_direction(direction)
        if selected_state_direction == low_adx_override_direction:
            final_score_result = _cap_low_adx_override(final_score_result, low_adx_override_direction)
        else:
            final_score_result = {
                'total_score': 0,
                'decision': 'Ignore',
                'breakdown': {
                    'trend': f"0 (Neutral market: {market_structure.reason})",
                    'structure': '0 (Low ADX override direction mismatch)',
                    'liquidity': '0',
                    'fvg': '0',
                    'volume': '0',
                    'macro': '0',
                    'premium_discount': '0',
                },
            }
            direction = 'NEUTRAL'
    selected_fvg_test_data = long_fvg_test_data if direction == 'LONG' else short_fvg_test_data if direction == 'SHORT' else None
    state_machine_status, state_machine_result = _state_machine_diagnostic(
        direction,
        market_structure,
        premium_discount_data,
        liquidity_map,
        sfp_data_in_window,
        context_break_1h,
        trigger_break_15m,
        selected_fvg_test_data,
        all_fvgs,
        current_price,
        len(df_15m_closed) - 1,
    )
    if (
        final_score_result.get('total_score', 0) >= 70
        and state_machine_result is not None
        and not state_machine_result.signal_allowed
    ):
        raw_before_state = final_score_result.get('raw_score', final_score_result.get('total_score', 0))
        final_score_result['raw_score'] = raw_before_state
        final_score_result['total_score'] = 69
        final_score_result['decision'] = 'Watchlist'
        final_score_result.setdefault('breakdown', {})
        final_score_result['breakdown']['scenario'] = (
            f"WATCHLIST (State Machine gate: {state_machine_result.state.value}, "
            f"next: {state_machine_result.missing_steps[0] if state_machine_result.missing_steps else 'ready'}, "
            f"score {raw_before_state}->69)"
        )
    final_score_result['breakdown']['liquidity_map'] = _format_liquidity_map(liquidity_map)
    final_score_result['breakdown']['state_machine'] = state_machine_status
    final_score_result['breakdown']['htf_structure'] = _format_market_structure(market_structure)
    final_score_result['breakdown']['adx'] = _format_adx(trend_data)

    analysis_data = {
        "trend_data": trend_data,
        "market_structure": market_structure,
        # Для А+ сетапа нам нужен уровень от 1H структуры
        "structure_data": context_break_1h or trigger_break_15m,
        "premium_discount_data": premium_discount_data,
        "liquidity_map": liquidity_map,
        "state_machine": state_machine_status,
        "direction": direction,
        "last_closed_15m": last_closed_15m,
    }

    return final_score_result, analysis_data

def market_scan(report_mode="HUNT"):
    # Логика времени (Кишинев UTC+3)
    t = time.time() + 10800
    local_struct = time.gmtime(t)
    current_time_str = f"{local_struct.tm_hour:02d}:{local_struct.tm_min:02d}"
    curr_t = dt_time(local_struct.tm_hour, local_struct.tm_min)
    
    in_kz = (dt_time(10, 0) <= curr_t <= dt_time(12, 0)) or \
            (dt_time(15, 30) <= curr_t <= dt_time(18, 0))
    session_status = "В KILL ZONE" if in_kz else "ВНЕ KILL ZONE"
    
    macro = get_macro_context()
    macro_str = f"DXY: {macro.get('DXY', {}).get('trend')} | SPX: {macro.get('SPX', {}).get('trend')} | BTC.D: {macro.get('BTC.D', {}).get('price')}%"
    logger.info(f"[{current_time_str}] Запуск сканирования | Режим: {report_mode} | Сессия: {session_status}")

    dashboard_lines = []

    for coin in COINS_LIST:
        try:
            score_result, analysis_data = prepare_and_analyze(coin, macro)
            if not score_result or not analysis_data:
                dashboard_lines.append(f"• <b>{coin}</b>: Н/Д (ошибка данных)")
                continue

            total_score = score_result.get('total_score', 0)
            is_high_score_setup = total_score >= 85
            #is_high_score_setup = total_score >= 0
            decision = resolve_session_decision(score_result, in_kz)

            # Отправка А+ сетапа
            if is_high_score_setup and in_kz:
                direction = analysis_data['direction']
                atr = analysis_data['last_closed_15m']['atr']
                entry_price = analysis_data.get('structure_data', {}).get('level')

                if entry_price and (time.time() - last_alert_time.get(coin, 0) > 7200):
                    entry_price = float(entry_price)
                    if direction == 'LONG':
                        stop_loss = entry_price - 2 * atr
                        take_profit = entry_price + 3 * (entry_price - stop_loss)
                    else:
                        stop_loss = entry_price + 2 * atr
                        take_profit = entry_price - 3 * (stop_loss - entry_price)

                    setup_details_json = {
                        "coin": coin, 
                        "direction": direction,
                        "entry_price": f"{entry_price:.4f}",
                        "stop_loss": f"{stop_loss:.4f}",
                        "take_profit": f"{take_profit:.4f}",
                        "score": total_score
                    }
                    
                    prompt = f"{A_PLUS_NARRATOR_INSTRUCTION}\n\nJSON ДАННЫЕ:\n{json.dumps(setup_details_json, indent=2, ensure_ascii=False)}"
                    try:
                        ai_response = model.generate_content(prompt).text
                        send_telegram_alert(f"🚨 <b>СНАЙПЕР ОБНАРУЖИЛ СЕТАП! ({total_score}/100)</b> 🚨\n\n{ai_response}")
                        last_alert_time[coin] = time.time()
                    except Exception as e:
                        logger.error(f"Ошибка Gemini для A+ сетапа {coin}: {e}")

            trend_data = analysis_data.get('trend_data')
            trend_strength = trend_data.get('strength', 'Н/Д') if trend_data else "Н/Д"
            
            # Добавляем строку в дашборд
            # if coin in ['BTC', 'ETH', 'SOL']:
            if coin in COINS_LIST:
                breakdown = score_result.get('breakdown', {})
                direction = analysis_data['direction']
                
                # 1. Форматируем заголовок с направлением сетапа (15m)
                setup_direction_text, setup_emoji = format_setup_direction(direction, total_score, decision)
                header = (
                    f"💎 <b>{_html_text(coin)}</b> | "
                    f"Сетап: <b>{_html_text(setup_direction_text)} {setup_emoji}</b> | "
                    f"Score: <b>{total_score}/100</b> | {_html_text(decision)}"
                )

                # 2. Форматируем HTF-контекст отдельно: EMA bias, swing-structure и ADX.
                trend_data = analysis_data.get('trend_data')
                market_structure = analysis_data.get('market_structure')
                
                bias_line = f"📊 4H Bias: {_html_text(_format_bias(trend_data))} | {_html_text(breakdown.get('trend', '0'))}"
                htf_structure_line = f"🧱 4H Structure: {_html_text(breakdown.get('htf_structure', _format_market_structure(market_structure)))}"
                adx_line = f"💪 ADX: {_html_text(breakdown.get('adx', _format_adx(trend_data)))}"
                structure_line = f"⚙️ Структура: {_html_text(breakdown.get('structure', '0'))}"
                liquidity_line = f"💧 Ликвидность: {_html_text(breakdown.get('liquidity', '0'))}"
                liquidity_map_line = f"🗺 Liquidity Map: {_html_text(breakdown.get('liquidity_map', '0'))}"
                fvg_line = f"🎯 FVG: {_html_text(breakdown.get('fvg', '0'))}"
                volume_line = f"📈 Объем: {_html_text(breakdown.get('volume', '0'))}"
                premium_discount_line = f"⚖️ P/D: {_html_text(breakdown.get('premium_discount', '0'))}"
                state_machine_line = f"🧭 State: {_html_text(breakdown.get('state_machine', '0'))}"
                scenario_line = f"🧩 Scenario: {_html_text(breakdown.get('scenario', '0'))}"
                macro_line = f"🌍 Макро: {_html_text(breakdown.get('macro', '0'))}"
                separator = "──────────────────"
                
                detailed_report = "\n".join([
                    header,
                    bias_line,
                    htf_structure_line,
                    adx_line,
                    structure_line,
                    liquidity_line,
                    liquidity_map_line,
                    fvg_line,
                    volume_line,
                    premium_discount_line,
                    state_machine_line,
                    scenario_line,
                    macro_line,
                    separator,
                ])
                dashboard_lines.append(detailed_report)
            else:
                dashboard_lines.append(
                    f"• <b>{_html_text(coin)}</b>: {total_score} баллов | "
                    f"{_html_text(decision)} | Тренд: {_html_text(trend_strength)}"
                )

        except Exception as e:
            logger.error(f"Критическая ошибка при анализе {coin}: {e}", exc_info=True)
            dashboard_lines.append(f"• <b>{coin}</b>: ОШИБКА АНАЛИЗА")

    # Отправка единого дашборда
    if dashboard_lines and (report_mode == "FULL" or in_kz):
        header_text = "РЫНОЧНЫЙ БРИФИНГ" if report_mode == "FULL" else "СНАЙПЕР ОНЛАЙН"
        summary_header = [
            f"📡 <b>{_html_text(header_text)} | {_html_text(current_time_str)}</b>",
            f"⚡️ Сессия: <code>{_html_text(session_status)}</code>",
            f"🌍 Макро: <code>{_html_text(macro_str)}</code>",
            "────────────────"
        ]
        send_telegram_blocks(summary_header, dashboard_lines)
    elif report_mode == "HUNT" and not in_kz:
        logger.info(f"[{current_time_str}] Вне Kill Zone. Дашборд скрыт для экономии эфира.")

if __name__ == "__main__":
    logger.info("🚀 Радар «СНАЙПЕР» онлайн. Версия 11.0 [Orchestrator Deterministic] запущена.")
    send_telegram_alert("👋 <b>СИСТЕМА ОНЛАЙН [V.11.0]</b>\nRadar запущен. Включен новый детерминированный Score Engine.")
    
    # Первый запуск при старте
    market_scan(report_mode="FULL")
    
    while True:
        t_now = time.time()
        seconds_past_quarter = int(t_now) % 900
        seconds_to_wait = 900 - seconds_past_quarter + 5  # 5 секунд буфера на закрытие свечи биржи
        
        next_run_time = time.gmtime(t_now + seconds_to_wait + 10800)
        logger.info(f"💤 Ожидаю закрытия свечи. Следующий запуск в: {next_run_time.tm_hour:02d}:{next_run_time.tm_min:02d}:05")
        
        time.sleep(seconds_to_wait)
        
        # Автоматический выбор режима отчета
        next_run_struct = time.gmtime(time.time() + 10800)
        current_mode = "FULL" if (next_run_struct.tm_min == 0 and next_run_struct.tm_hour in [9, 15]) else "HUNT"
            
        try:
            market_scan(report_mode=current_mode)
        except Exception as e:
            logger.error(f"Критическая ошибка в главном цикле: {e}", exc_info=True)
