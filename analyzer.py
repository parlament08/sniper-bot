import os
import time
import json
import uuid
import requests
import pandas as pd
import google.generativeai as genai
from html import escape
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
from core.journal import write_scan_record
from core.liquidity import build_liquidity_map
from core.premium_discount import evaluate_premium_discount
from core.risk import calculate_setup_score, format_setup_direction, resolve_session_decision, select_best_setup
from core.risk_plan import build_risk_plan
from core.session import DEFAULT_TIMEZONE, evaluate_session, next_quarter_close
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
SEND_DIAGNOSTIC_OUTSIDE_KZ = os.environ.get("SEND_DIAGNOSTIC_OUTSIDE_KZ", "false").lower() == "true"
TELEGRAM_REPORT_DETAIL = os.environ.get("TELEGRAM_REPORT_DETAIL", "compact").lower()
SCAN_JOURNAL_ENABLED = os.environ.get("SCAN_JOURNAL_ENABLED", "true").lower() == "true"

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
            logger.warning("Dashboard block exceeds Telegram safe length; splitting it by lines.")
            for chunk in _split_oversized_block(header, block, max_length):
                send_telegram_alert(chunk)
            continue

        current_blocks.append(block)

    flush()


def _split_oversized_block(header, block, max_length):
    chunks = []
    current_lines = []
    for line in block.splitlines():
        projected = "\n".join([header] + current_lines + [line])
        if current_lines and len(projected) > max_length:
            chunks.append("\n".join([header] + current_lines))
            current_lines = [line]
            continue

        if not current_lines and len(projected) > max_length:
            available = max(200, max_length - len(header) - 32)
            for start in range(0, len(line), available):
                chunks.append("\n".join([header, line[start:start + available]]))
            current_lines = []
            continue

        current_lines.append(line)

    if current_lines:
        chunks.append("\n".join([header] + current_lines))
    return chunks


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
    return f"{level_type} {float(price):.4f} Q{int(strength)} D{float(distance_atr):.2f}ATR T{touches} {state}"


def _format_liquidity_map(liquidity_map):
    if liquidity_map is None:
        return '0'

    nearest_buy = _level_value(liquidity_map, 'nearest_buy_side')
    nearest_sell = _level_value(liquidity_map, 'nearest_sell_side')

    return (
        f"BSL: {_format_liquidity_level(nearest_buy)} | "
        f"SSL: {_format_liquidity_level(nearest_sell)}"
    )


NO_TRADE_REASON_LABELS = {
    "neutral_htf": "Neutral HTF",
    "pd_block": "P/D block",
    "countertrend": "Countertrend",
    "fvg_invalid": "FVG invalid",
    "missing_structure": "Missing structure",
    "context_only": "Context only",
    "waiting_for_confirmation": "Waiting confirmation",
    "missing_sweep_or_poi": "Missing sweep/POI",
    "shallow_pd_zone": "Shallow P/D",
    "incomplete_scenario": "Incomplete scenario",
    "state_machine_block": "Scenario gate",
    "risk_plan_block": "Risk plan",
    "low_score": "Low score",
}


def _format_no_trade_reason(score_result):
    reason = score_result.get('no_trade_reason') or score_result.get('diagnostics', {}).get('no_trade_reason')
    return NO_TRADE_REASON_LABELS.get(reason, str(reason).replace('_', ' ').title() if reason else '')


def _gate(value, pass_text='PASS', fail_text='FAIL'):
    return pass_text if value else fail_text


def _build_gates_summary(score_result, analysis_data, in_kz):
    diagnostics = score_result.get('diagnostics', {})
    state_text = str(score_result.get('breakdown', {}).get('state_machine', '0'))
    state_ok = 'signal_ready' in state_text
    macro_text = str(score_result.get('breakdown', {}).get('macro', '0'))
    macro_state = 'PASS' if macro_text.startswith('+') else 'MIXED'
    risk_plan = analysis_data.get('risk_plan')
    risk_ok = bool(risk_plan and risk_plan.get('valid', False))
    risk_state = 'PASS' if risk_ok else 'WAIT' if risk_plan is None else 'FAIL'

    return (
        f"KZ {_gate(in_kz)} | "
        f"P/D {_gate(diagnostics.get('pd_valid', True))} | "
        f"Sweep {_gate(diagnostics.get('sfp_present', False))} | "
        f"Trigger {_gate(diagnostics.get('trigger_structure_aligned', False))} | "
        f"FVG {_gate(diagnostics.get('fvg_test_present', False))} | "
        f"SM {'PASS' if state_ok else 'WAIT'} | "
        f"Risk {risk_state} | "
        f"Macro {macro_state}"
    )


def _compact_state_text(state_text):
    if not state_text or state_text == '0':
        return '0'
    return state_text.replace('waiting_for_', 'wait_').replace('liquidity_sweep', 'sweep')


def _build_dashboard_block(coin, score_result, analysis_data, decision, in_kz):
    total_score = score_result.get('total_score', 0)
    breakdown = score_result.get('breakdown', {})
    direction = analysis_data['direction']
    setup_direction_text, setup_emoji = format_setup_direction(direction, total_score, decision)
    reason_label = _format_no_trade_reason(score_result)
    reason_suffix = f" — {reason_label}" if setup_direction_text == 'NO TRADE' and reason_label else ""

    header = (
        f"💎 <b>{_html_text(coin)}</b> | "
        f"<b>{_html_text(setup_direction_text + reason_suffix)} {setup_emoji}</b> | "
        f"<b>{total_score}/100</b> | {_html_text(decision)}"
    )

    trend_data = analysis_data.get('trend_data')
    market_structure = analysis_data.get('market_structure')
    bias_line = (
        f"📊 4H: {_html_text(_format_bias(trend_data))} | "
        f"{_html_text(breakdown.get('trend', '0'))} | "
        f"{_html_text(breakdown.get('adx', _format_adx(trend_data)))}"
    )
    htf_structure_line = f"🧱 HTF: {_html_text(breakdown.get('htf_structure', _format_market_structure(market_structure)))}"
    structure_line = f"⚙️ Structure: {_html_text(breakdown.get('structure', '0'))}"
    liquidity_line = f"💧 Sweep/SFP: {_html_text(breakdown.get('liquidity', '0'))}"
    liquidity_map_line = f"🗺 Liq: {_html_text(breakdown.get('liquidity_map', '0'))}"
    fvg_line = f"🎯 FVG: {_html_text(breakdown.get('fvg', '0'))}"
    volume_line = f"📈 Volume: {_html_text(breakdown.get('volume', '0'))}"
    premium_discount_line = f"⚖️ P/D: {_html_text(breakdown.get('premium_discount', '0'))}"
    risk_plan_line = f"🛡 Risk: {_html_text(breakdown.get('risk_plan', '0'))}"
    state_machine_line = f"🧭 Scenario: {_html_text(_compact_state_text(breakdown.get('state_machine', '0')))}"
    gates_line = f"🚧 Gates: {_html_text(_build_gates_summary(score_result, analysis_data, in_kz))}"
    macro_line = f"🌍 Macro: {_html_text(breakdown.get('macro', '0'))}"
    separator = "──────────────────"

    lines = [
        header,
        bias_line,
        structure_line,
        liquidity_line,
        fvg_line,
        premium_discount_line,
        risk_plan_line,
        state_machine_line,
        gates_line,
        macro_line,
    ]

    if TELEGRAM_REPORT_DETAIL == "audit":
        lines.insert(2, htf_structure_line)
        lines.insert(5, liquidity_map_line)
        lines.insert(7, volume_line)
    else:
        lines.insert(5, volume_line)

    lines.append(separator)
    return "\n".join(lines)


def _format_risk_plan(risk_plan):
    if not risk_plan:
        return '0'
    validity = 'OK' if risk_plan.get('valid') else 'BLOCK'
    target_2 = risk_plan.get('target_2')
    rr2_text = f" / T2 {risk_plan.get('rr_to_target_2'):.2f}R" if target_2 is not None and risk_plan.get('rr_to_target_2') is not None else ""
    return (
        f"{validity} ({risk_plan.get('entry_model')} -> {risk_plan.get('target_model')}, "
        f"T1 {risk_plan.get('rr_to_target_1'):.2f}R{rr2_text}, "
        f"SL {risk_plan.get('stop_distance_percent'):.2f}%, "
        f"{risk_plan.get('reason')})"
    )


def _event_snapshot(event):
    if not event:
        return None
    return {
        'type': event.get('type'),
        'index': str(event.get('index')) if event.get('index') is not None else None,
        'level': event.get('level'),
        'quality_score': event.get('quality_score'),
        'confidence': event.get('confidence'),
        'displacement_ratio': event.get('displacement_ratio'),
        'body_ratio': event.get('body_ratio'),
        'close_position': event.get('close_position'),
        'rvol': event.get('rvol'),
        'level_type': event.get('level_type'),
        'level_strength': event.get('level_strength'),
        'liquidity_depth': event.get('liquidity_depth'),
        'rejection_strength': event.get('rejection_strength'),
        'volume_confirmed': event.get('volume_confirmed'),
        'absorption_warning': event.get('absorption_warning'),
    }


def _liquidity_level_snapshot(level):
    if not level:
        return None
    return {
        'type': _level_value(level, 'type'),
        'price': _level_value(level, 'price'),
        'strength': _level_value(level, 'strength'),
        'touches': _level_value(level, 'touches'),
        'age_bars': _level_value(level, 'age_bars'),
        'distance_atr': _level_value(level, 'distance_atr'),
        'swept': _level_value(level, 'swept'),
    }


def _build_scan_journal_record(run_id, timestamp, symbol, session, score_result, analysis_data, macro):
    trend_data = analysis_data.get('trend_data') or {}
    market_structure = analysis_data.get('market_structure')
    liquidity_map = analysis_data.get('liquidity_map')
    premium_discount = analysis_data.get('premium_discount_data')
    risk_plan = analysis_data.get('risk_plan')
    last_15m = analysis_data.get('last_closed_15m')

    return {
        'run_id': run_id,
        'timestamp': timestamp,
        'symbol': symbol,
        'timeframes': {
            '15m_last_closed': str(last_15m.name) if last_15m is not None else None,
        },
        'session': session.to_dict() if hasattr(session, 'to_dict') else session,
        'decision': score_result.get('decision'),
        'score': score_result.get('total_score'),
        'raw_score': score_result.get('raw_score'),
        'direction': analysis_data.get('direction'),
        'no_trade_reason': score_result.get('no_trade_reason'),
        'features': {
            'trend_4h': {
                'is_bullish': trend_data.get('is_bullish'),
                'strength': trend_data.get('strength'),
                'adx': trend_data.get('adx_value'),
                'p_di': trend_data.get('p_di'),
                'n_di': trend_data.get('n_di'),
            },
            'market_structure_4h': market_structure.to_dict() if hasattr(market_structure, 'to_dict') else market_structure,
            'context_1h': _event_snapshot(analysis_data.get('context_break_1h')),
            'trigger_15m': _event_snapshot(analysis_data.get('trigger_break_15m')),
            'sfp': _event_snapshot(analysis_data.get('sfp_data')),
            'premium_discount': premium_discount.to_dict() if hasattr(premium_discount, 'to_dict') else premium_discount,
            'liquidity_map': {
                'nearest_buy_side': _liquidity_level_snapshot(_level_value(liquidity_map, 'nearest_buy_side')),
                'nearest_sell_side': _liquidity_level_snapshot(_level_value(liquidity_map, 'nearest_sell_side')),
                'strongest_buy_side': _liquidity_level_snapshot(_level_value(liquidity_map, 'strongest_buy_side')),
                'strongest_sell_side': _liquidity_level_snapshot(_level_value(liquidity_map, 'strongest_sell_side')),
            },
            'risk_plan': risk_plan.to_dict() if hasattr(risk_plan, 'to_dict') else risk_plan,
        },
        'diagnostics': score_result.get('diagnostics', {}),
        'breakdown': score_result.get('breakdown', {}),
        'macro': macro,
    }


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


def _macro_price_text(value, suffix=''):
    if value is None:
        return 'N/A'
    return f"{value}{suffix}"


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

    return analyze_symbol_snapshot(coin, df_4h, df_1h, df_15m, macro_context)


def analyze_symbol_snapshot(coin, df_4h, df_1h, df_15m, macro_context):
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
            'raw_score': 0,
            'total_score': 0,
            'decision': 'Ignore',
            'no_trade_reason': 'neutral_htf',
            'diagnostics': {
                'pd_valid': False,
                'pd_shallow': False,
                'with_trend': False,
                'context_structure_aligned': False,
                'trigger_structure_aligned': False,
                'trigger_confirmed': False,
                'sfp_present': bool(sfp_data_in_window),
                'fvg_test_present': False,
                'scenario_valid': False,
                'no_trade_reason': 'neutral_htf',
            },
            'breakdown': {
                'trend': f"0 (Neutral market: {market_structure.reason})",
                'structure': '0 (Neutral market state)',
                'liquidity': '0',
                'fvg': '0',
                'volume': '0',
                'macro': '0',
                'premium_discount': '0',
                'risk_plan': '0',
                'liquidity_map': _format_liquidity_map(liquidity_map),
                'state_machine': '0',
                'htf_structure': _format_market_structure(market_structure),
                'adx': _format_adx(trend_data),
            },
        }, {
            'trend_data': trend_data,
            'market_structure': market_structure,
            'structure_data': None,
            'context_break_1h': context_break_1h,
            'trigger_break_15m': trigger_break_15m,
            'sfp_data': sfp_data_in_window,
            'fvg_candidates': [],
            'active_fvg': None,
            'premium_discount_data': None,
            'liquidity_map': liquidity_map,
            'risk_plan': None,
            'state_machine': None,
            'session': None,
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
                'raw_score': 0,
                'total_score': 0,
                'decision': 'Ignore',
                'no_trade_reason': 'neutral_htf',
                'diagnostics': {
                    'pd_valid': False,
                    'pd_shallow': False,
                    'with_trend': False,
                    'context_structure_aligned': False,
                    'trigger_structure_aligned': False,
                    'trigger_confirmed': False,
                    'sfp_present': bool(sfp_data_in_window),
                    'fvg_test_present': False,
                    'scenario_valid': False,
                    'no_trade_reason': 'neutral_htf',
                },
                'breakdown': {
                    'trend': f"0 (Neutral market: {market_structure.reason})",
                    'structure': '0 (Low ADX override direction mismatch)',
                    'liquidity': '0',
                    'fvg': '0',
                    'volume': '0',
                    'macro': '0',
                    'premium_discount': '0',
                    'risk_plan': '0',
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
        final_score_result['no_trade_reason'] = 'state_machine_block'
        final_score_result.setdefault('diagnostics', {})['no_trade_reason'] = 'state_machine_block'
        final_score_result['diagnostics']['state_machine_allowed'] = False
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
    final_score_result.setdefault('diagnostics', {})['state_machine_allowed'] = (
        state_machine_result.signal_allowed if state_machine_result is not None else False
    )

    risk_plan = build_risk_plan(
        direction=direction,
        current_price=current_price,
        atr=float(last_closed_15m.get('atr', 0.0)),
        liquidity_map=liquidity_map,
        fvg_data=all_fvgs,
        fvg_test_data=selected_fvg_test_data,
        sfp_data=sfp_data_in_window,
        structure_data=context_break_1h or trigger_break_15m,
    ) if direction in ('LONG', 'SHORT') else None
    if risk_plan:
        final_score_result['breakdown']['risk_plan'] = _format_risk_plan(risk_plan)
        final_score_result.setdefault('diagnostics', {})['risk_plan_valid'] = risk_plan.valid
        if final_score_result.get('total_score', 0) >= 70 and not risk_plan.valid:
            raw_before_risk = final_score_result.get('raw_score', final_score_result.get('total_score', 0))
            final_score_result['raw_score'] = raw_before_risk
            final_score_result['total_score'] = 69
            final_score_result['decision'] = 'Watchlist'
            final_score_result['no_trade_reason'] = 'risk_plan_block'
            final_score_result['diagnostics']['no_trade_reason'] = 'risk_plan_block'
            final_score_result['breakdown']['risk_plan'] = (
                f"WATCHLIST ({risk_plan.reason}, score {raw_before_risk}->69, "
                f"T1 {risk_plan.rr_to_target_1:.2f}R)"
            )
    else:
        final_score_result['breakdown']['risk_plan'] = '0'
        final_score_result.setdefault('diagnostics', {})['risk_plan_valid'] = False

    analysis_data = {
        "trend_data": trend_data,
        "market_structure": market_structure,
        # Для А+ сетапа нам нужен уровень от 1H структуры
        "structure_data": context_break_1h or trigger_break_15m,
        "context_break_1h": context_break_1h,
        "trigger_break_15m": trigger_break_15m,
        "sfp_data": sfp_data_in_window,
        "fvg_candidates": all_fvgs,
        "active_fvg": selected_fvg_test_data,
        "premium_discount_data": premium_discount_data,
        "liquidity_map": liquidity_map,
        "risk_plan": risk_plan,
        "state_machine": state_machine_status,
        "session": None,
        "direction": direction,
        "last_closed_15m": last_closed_15m,
    }

    return final_score_result, analysis_data

def market_scan(report_mode="HUNT"):
    session = evaluate_session()
    current_time_str = session.local_time
    in_kz = session.in_kill_zone
    session_status = (
        f"{session.session_name} KZ ({session.minutes_to_session_end}m left)"
        if in_kz
        else f"ВНЕ KILL ZONE (next {session.minutes_to_next_session}m)"
    )
    
    macro = get_macro_context()
    macro_str = (
        f"DXY: {macro.get('DXY', {}).get('trend')} | "
        f"SPX: {macro.get('SPX', {}).get('trend')} | "
        f"BTC.D: {_macro_price_text(macro.get('BTC.D', {}).get('price'), '%')}"
    )
    logger.info(f"[{current_time_str}] Запуск сканирования | Режим: {report_mode} | Сессия: {session_status}")

    dashboard_lines = []
    run_id = str(uuid.uuid4())

    for coin in COINS_LIST:
        try:
            score_result, analysis_data = prepare_and_analyze(coin, macro)
            if not score_result or not analysis_data:
                dashboard_lines.append(f"• <b>{coin}</b>: Н/Д (ошибка данных)")
                continue
            analysis_data['session'] = session

            total_score = score_result.get('total_score', 0)
            is_high_score_setup = total_score >= 85
            #is_high_score_setup = total_score >= 0
            decision = resolve_session_decision(score_result, in_kz)
            if SCAN_JOURNAL_ENABLED:
                try:
                    journal_path = write_scan_record(
                        _build_scan_journal_record(
                            run_id,
                            pd.Timestamp.now(tz=DEFAULT_TIMEZONE).isoformat(),
                            coin,
                            session,
                            score_result,
                            analysis_data,
                            macro,
                        )
                    )
                    logger.info(f"Scan journal записан: {journal_path}")
                except Exception as journal_error:
                    logger.error(f"Не удалось записать scan journal для {coin}: {journal_error}")

            # Отправка А+ сетапа
            if is_high_score_setup and in_kz:
                direction = analysis_data['direction']
                risk_plan = analysis_data.get('risk_plan')
                entry_price = risk_plan.get('entry') if risk_plan and risk_plan.get('valid') else None

                if entry_price and (time.time() - last_alert_time.get(coin, 0) > 7200):
                    entry_price = float(entry_price)
                    stop_loss = float(risk_plan.get('stop_loss'))
                    take_profit = float(risk_plan.get('target_1'))

                    setup_details_json = {
                        "coin": coin, 
                        "direction": direction,
                        "entry_price": f"{entry_price:.4f}",
                        "stop_loss": f"{stop_loss:.4f}",
                        "take_profit": f"{take_profit:.4f}",
                        "target_2": f"{risk_plan.get('target_2'):.4f}" if risk_plan.get('target_2') is not None else None,
                        "score": total_score,
                        "entry_model": risk_plan.get('entry_model'),
                        "stop_model": risk_plan.get('stop_model'),
                        "target_model": risk_plan.get('target_model'),
                        "rr_to_t1": risk_plan.get('rr_to_target_1'),
                        "rr_to_t2": risk_plan.get('rr_to_target_2'),
                        "invalidation_level": f"{risk_plan.get('invalidation_level'):.4f}",
                        "late_entry": risk_plan.get('late_entry'),
                        "risk_reason": risk_plan.get('reason'),
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
                dashboard_lines.append(_build_dashboard_block(coin, score_result, analysis_data, decision, in_kz))
            else:
                dashboard_lines.append(
                    f"• <b>{_html_text(coin)}</b>: {total_score} баллов | "
                    f"{_html_text(decision)} | Тренд: {_html_text(trend_strength)}"
                )

        except Exception as e:
            logger.error(f"Критическая ошибка при анализе {coin}: {e}", exc_info=True)
            dashboard_lines.append(f"• <b>{coin}</b>: ОШИБКА АНАЛИЗА")

    # Отправка единого дашборда
    should_send_dashboard = report_mode == "FULL" or in_kz or SEND_DIAGNOSTIC_OUTSIDE_KZ
    if dashboard_lines and should_send_dashboard:
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
        next_run_time = next_quarter_close(timezone=DEFAULT_TIMEZONE)
        logger.info(f"💤 Ожидаю закрытия свечи. Следующий запуск в: {next_run_time.strftime('%H:%M:%S')}")
        
        time.sleep(seconds_to_wait)
        
        # Автоматический выбор режима отчета
        next_session = evaluate_session()
        next_hour, next_minute = map(int, next_session.local_time.split(":"))
        current_mode = "FULL" if (next_minute == 0 and next_hour in [9, 15]) else "HUNT"
            
        try:
            market_scan(report_mode=current_mode)
        except Exception as e:
            logger.error(f"Критическая ошибка в главном цикле: {e}", exc_info=True)
