import os
import time
import json
import uuid
import hashlib
import subprocess
import traceback as traceback_module
import requests
import pandas as pd
import google.generativeai as genai
from contextlib import contextmanager
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
from core.payload import payload_to_dict
from core.premium_discount import evaluate_premium_discount
from core.risk import calculate_setup_score, format_setup_direction, resolve_session_decision, select_best_setup
from core.risk_plan import RiskPlan, RiskPlanConfig, build_risk_plan
from core.session import DEFAULT_TIMEZONE, evaluate_session, next_quarter_close
from core.scenario_scanner import ScenarioEvent, scan_scenarios
from core.state_machine import SniperEvent, SniperStateMachine
from core.trigger_scanner import scan_post_anchor_trigger

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
SEND_A_PLUS_OUTSIDE_KZ = os.environ.get("SEND_A_PLUS_OUTSIDE_KZ", "true").lower() == "true"
TELEGRAM_REPORT_DETAIL = os.environ.get("TELEGRAM_REPORT_DETAIL", "compact").lower()
SCAN_JOURNAL_ENABLED = os.environ.get("SCAN_JOURNAL_ENABLED", "true").lower() == "true"
ENABLE_SCENARIO_RESEARCH_TRACE = os.environ.get("ENABLE_SCENARIO_RESEARCH_TRACE", "false").lower() == "true"
SCENARIO_RESEARCH_TRACE_MAX_FVGS = int(os.environ.get("SCENARIO_RESEARCH_TRACE_MAX_FVGS", "12"))
SCENARIO_RESEARCH_TRACE_MAX_CANDLES = int(os.environ.get("SCENARIO_RESEARCH_TRACE_MAX_CANDLES", "64"))
APP_VERSION = os.environ.get("APP_VERSION", "v1.0.0-rc2")
GIT_COMMIT = os.environ.get("GIT_COMMIT")
BUILD_TIME = os.environ.get("BUILD_TIME") or pd.Timestamp.now(tz=DEFAULT_TIMEZONE).isoformat()
CODE_HASH_FILES = (
    "analyzer.py",
    "core/state_machine.py",
    "core/scenario_scanner.py",
    "core/trigger_scanner.py",
    "core/risk_plan.py",
)
A_PLUS_DELIVERY_THRESHOLD = int(os.environ.get("A_PLUS_DELIVERY_THRESHOLD", "85"))
MIN_SCENARIO_FVG_QUALITY = 70
MAX_SCENARIO_FVG_AGE = 64
MAX_SCENARIO_FVG_RETESTS = 3
MIN_TRIGGER_QUALITY = 70
MIN_EARLY_TRIGGER_QUALITY = 55
MIN_EARLY_TRIGGER_BODY_RATIO = 0.45
MIN_EARLY_TRIGGER_DISPLACEMENT_ATR = 0.5
MIN_EARLY_TRIGGER_RVOL = 1.2
TRIGGER_LINK_WINDOW_BARS = 5
MAX_TRIGGER_BARS_AFTER_SFP = 24
MAX_TRIGGER_BARS_AFTER_POI = 24
if not GIT_COMMIT or GIT_COMMIT == "unknown":
    logger.warning("GIT_COMMIT is not configured")

A_PLUS_NARRATOR_INSTRUCTION = """
Ты — профессиональный финансовый диктор. Оформи этот JSON с А+ сетапом в красивый HTML для Telegram с тегами <b> и <code>. Ничего не выдумывай от себя.
"""

model = genai.GenerativeModel(model_name='models/gemini-3.1-flash-lite')
# COINS_LIST = ['BTC', 'ETH', 'SOL', 'XRP', 'ADA', 'LINK', 'INJ', 'HYPE', 'LTC', 'DOT']
COINS_LIST = [
    'BTC', 'ETH', 'SOL', 'XRP', 'BNB',
    'DOGE', 'ADA', 'AVAX', 'LINK', 'DOT',
    'TRX', 'LTC', 'BCH', 'UNI', 'SUI',
    'NEAR', 'APT', 'ICP', 'FIL', 'ETC',
    'ATOM', 'ARB', 'OP', 'INJ', 'TIA',
    'SEI', 'AAVE', 'MKR', 'RUNE', 'LDO',
    'ORDI', 'WIF', 'FET',
    'RENDER', 'GRT', 'JUP', 'PYTH', 'ENA',
    'HYPE', 'TON', 'WLD', 'ALGO', 'SAND',
    'MANA', 'APE', 'DYDX', 'IMX', 'STX',
]
last_alert_time = {coin: 0 for coin in COINS_LIST}
_SCENARIO_TRANSITION_STATE = {}
_SCENARIO_RUNTIME_STATE = {}
EARLY_TRIGGER_WINDOW_EXPIRED_REASON = "early_trigger_window_expired"


def export_scenario_runtime_state():
    return {
        "schema_version": 1,
        "scenario_runtime_state": [
            {
                "symbol": symbol,
                "candidate_id": candidate_id,
                "state": _json_state_value(state),
            }
            for (symbol, candidate_id), state in sorted(
                _SCENARIO_RUNTIME_STATE.items(),
                key=lambda item: (str(item[0][0]), str(item[0][1])),
            )
        ],
        "scenario_transition_state": [
            {
                "symbol": symbol,
                "candidate_id": candidate_id,
                "state": _json_state_value(state),
            }
            for (symbol, candidate_id), state in sorted(
                _SCENARIO_TRANSITION_STATE.items(),
                key=lambda item: (str(item[0][0]), str(item[0][1])),
            )
        ],
    }


def import_scenario_runtime_state(snapshot, *, replace=True):
    if not isinstance(snapshot, dict):
        raise TypeError("runtime state snapshot must be a dict")
    if snapshot.get("schema_version") != 1:
        raise ValueError("unsupported runtime state schema_version")
    runtime_entries = snapshot.get("scenario_runtime_state", [])
    transition_entries = snapshot.get("scenario_transition_state", [])
    if not isinstance(runtime_entries, list) or not isinstance(transition_entries, list):
        raise TypeError("runtime state containers must be lists")
    if replace:
        reset_scenario_runtime_state()
    for entry in runtime_entries:
        symbol, candidate_id, state = _runtime_state_entry(entry)
        _SCENARIO_RUNTIME_STATE[(symbol, candidate_id)] = state
    for entry in transition_entries:
        symbol, candidate_id, state = _runtime_state_entry(entry)
        _SCENARIO_TRANSITION_STATE[(symbol, candidate_id)] = state


def reset_scenario_runtime_state():
    _SCENARIO_RUNTIME_STATE.clear()
    _SCENARIO_TRANSITION_STATE.clear()


def scenario_runtime_state_hash(snapshot=None):
    payload = snapshot if snapshot is not None else export_scenario_runtime_state()
    text = json.dumps(_json_state_value(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@contextmanager
def scenario_runtime_state(snapshot=None, *, persist=False):
    previous = export_scenario_runtime_state()
    try:
        import_scenario_runtime_state(snapshot or _empty_scenario_runtime_state(), replace=True)
        yield
    finally:
        if not persist:
            import_scenario_runtime_state(previous, replace=True)


def _empty_scenario_runtime_state():
    return {"schema_version": 1, "scenario_runtime_state": [], "scenario_transition_state": []}


def _runtime_state_entry(entry):
    if not isinstance(entry, dict):
        raise TypeError("runtime state entry must be a dict")
    symbol = entry.get("symbol")
    candidate_id = entry.get("candidate_id")
    if symbol is None or candidate_id is None:
        raise ValueError("runtime state entry requires symbol and candidate_id")
    return str(symbol), str(candidate_id), _restore_state_value(entry.get("state"))


def _json_state_value(value):
    if isinstance(value, dict):
        return {str(key): _json_state_value(val) for key, val in sorted(value.items(), key=lambda item: str(item[0]))}
    if isinstance(value, (list, tuple)):
        return [_json_state_value(item) for item in value]
    if isinstance(value, pd.Timestamp):
        ts = value.tz_localize(DEFAULT_TIMEZONE) if value.tzinfo is None else value
        ts = ts.tz_convert("UTC")
        return ts.isoformat().replace("+00:00", "Z")
    if hasattr(value, "item"):
        try:
            return value.item()
        except Exception:
            pass
    if hasattr(value, "isoformat") and not isinstance(value, str):
        try:
            ts = pd.Timestamp(value)
            ts = ts.tz_localize(DEFAULT_TIMEZONE) if ts.tzinfo is None else ts
            return ts.tz_convert("UTC").isoformat().replace("+00:00", "Z")
        except Exception:
            pass
    return value


def _restore_state_value(value):
    if isinstance(value, dict):
        return {str(key): _restore_state_value(val) for key, val in value.items()}
    if isinstance(value, list):
        return [_restore_state_value(item) for item in value]
    if isinstance(value, str):
        try:
            if "T" in value and (value.endswith("Z") or "+" in value):
                return pd.Timestamp(value)
        except Exception:
            return value
    return value

def send_telegram_alert(text, *, run_id=None, message_type="UNKNOWN", delivery_context=None):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"}
    sent = False
    error = None
    status_code = None
    message_id = None
    try:
        res = requests.post(url, data=payload, timeout=10)
        status_code = res.status_code
        if res.status_code != 200:
            error = res.text
            logger.error(f"Ошибка Telegram API: {res.text}")
        else:
            sent = True
            try:
                message_id = (res.json().get("result") or {}).get("message_id")
            except Exception:
                message_id = None
    except Exception as e:
        error = str(e)
        logger.error(f"Не удалось отправить пуш в Telegram: {e}")
    _record_telegram_delivery(
        run_id=run_id,
        message_type=message_type,
        attempted=True,
        sent=sent,
        error=error,
        status_code=status_code,
        message_length=len(text or ""),
        telegram_message_id=message_id if sent else None,
        **(delivery_context or {}),
    )
    return {"attempted": True, "sent": sent, "error": error, "status_code": status_code}


def send_telegram_blocks(header_lines, body_blocks, max_length=TELEGRAM_MAX_MESSAGE_LENGTH, *, run_id=None, message_type="DASHBOARD"):
    header = "\n".join(header_lines)
    current_blocks = []

    def flush():
        if not current_blocks:
            return
        message = "\n".join([header] + current_blocks)
        if run_id is None and message_type == "DASHBOARD":
            send_telegram_alert(message)
        else:
            send_telegram_alert(message, run_id=run_id, message_type=message_type)
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
    trigger_scan_line = f"🔎 Trigger Scan: {_html_text(breakdown.get('trigger_scan', breakdown.get('trigger_debug', '0')))}"
    scenario_scan_line = f"🧭 Scenario Scan: {_html_text(breakdown.get('scenario_scan', '0'))}"
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
        trigger_scan_line,
        scenario_scan_line,
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
    if risk_plan.get('risk_plan_status') == 'not_available':
        return f"WAIT ({risk_plan.get('reason') or 'entry model not formed'})"
    validity = 'OK' if risk_plan.get('valid') else 'BLOCK'
    target_2 = risk_plan.get('target_2')
    rr2_text = f" / T2 {risk_plan.get('rr_to_target_2'):.2f}R" if target_2 is not None and risk_plan.get('rr_to_target_2') is not None else ""
    return (
        f"{validity} ({risk_plan.get('entry_model')} -> {risk_plan.get('target_model')}, "
        f"T1 {risk_plan.get('rr_to_target_1'):.2f}R{rr2_text}, "
        f"SL {risk_plan.get('stop_distance_percent'):.2f}%, "
        f"{risk_plan.get('reason')})"
    )


def _event_timing_fields(event):
    event_index = event.get('index') if event else None
    detected_at = event.get('detected_at') or event.get('created_at') or event.get('scan_time')
    event_time = event.get('event_time') or event_index
    delay_seconds = None
    if event_time is not None and detected_at is not None:
        try:
            event_ts = pd.Timestamp(event_time)
            detected_ts = pd.Timestamp(detected_at)
            if event_ts.tzinfo is not None and detected_ts.tzinfo is None:
                detected_ts = detected_ts.tz_localize(event_ts.tzinfo)
            elif event_ts.tzinfo is None and detected_ts.tzinfo is not None:
                event_ts = event_ts.tz_localize(detected_ts.tzinfo)
            delay_seconds = max(0.0, (detected_ts - event_ts).total_seconds())
        except Exception:
            delay_seconds = None
    return {
        'event_time': str(event_time) if event_time is not None else None,
        'detected_at': str(detected_at) if detected_at is not None else None,
        'detection_delay_seconds': delay_seconds,
        'is_reconstructed': bool(event.get('is_reconstructed') or event.get('historical_only')),
    }


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
        'trigger_stage': event.get('trigger_stage'),
        'is_early': event.get('is_early'),
        'is_confirmed': event.get('is_confirmed'),
        'reason': event.get('reason'),
        **_event_timing_fields(event),
    }


def _trigger_candidate_snapshot(event, rejection_reason=None):
    snapshot = _event_snapshot(event)
    if not snapshot:
        return None
    snapshot['direction'] = _event_direction(event)
    snapshot['rejection_reason'] = rejection_reason
    return snapshot


def _trigger_scan_snapshot(trigger_scan):
    if not trigger_scan:
        return None
    data = trigger_scan.to_dict() if hasattr(trigger_scan, 'to_dict') else dict(trigger_scan)
    return {
        'expected_direction': data.get('expected_direction'),
        'selected_trigger': _trigger_candidate_snapshot(data.get('selected_trigger')),
        'confirmed_trigger': _trigger_candidate_snapshot(data.get('confirmed_trigger')),
        'early_trigger': _trigger_candidate_snapshot(data.get('early_trigger')),
        'pre_sfp_trigger': _trigger_candidate_snapshot(data.get('pre_sfp_trigger')),
        'post_sfp_trigger': _trigger_candidate_snapshot(data.get('post_sfp_trigger')),
        'pre_poi_trigger': _trigger_candidate_snapshot(data.get('pre_poi_trigger')),
        'post_poi_trigger': _trigger_candidate_snapshot(data.get('post_poi_trigger')),
        'candidate_trigger': _trigger_candidate_snapshot(data.get('candidate_trigger')),
        'opposite_trigger': _trigger_candidate_snapshot(data.get('opposite_trigger')),
        'sfp_index': str(data.get('sfp_index')) if data.get('sfp_index') is not None else None,
        'poi_index': str(data.get('poi_index')) if data.get('poi_index') is not None else None,
        'pd_location_index': str(data.get('pd_location_index')) if data.get('pd_location_index') is not None else None,
        'anchor_index': str(data.get('anchor_index')) if data.get('anchor_index') is not None else None,
        'trigger_index': str(data.get('trigger_index')) if data.get('trigger_index') is not None else None,
        'trigger_confirmed': data.get('trigger_confirmed'),
        'early_trigger_confirmed': data.get('early_trigger_confirmed'),
        'rejected_reason': data.get('rejected_reason'),
        'waiting_for': data.get('waiting_for'),
        'confirmed_trigger_debug': data.get('confirmed_trigger_debug'),
    }


def _object_field(value, field, default=None):
    if value is None:
        return default
    if isinstance(value, dict):
        return value.get(field, default)
    return getattr(value, field, default)


def _json_safe(value):
    if hasattr(value, "to_dict"):
        value = value.to_dict()
    elif hasattr(value, "_asdict"):
        value = value._asdict()
    elif hasattr(value, "__dict__") and not isinstance(value, type):
        value = vars(value)
    return _json_state_value(value)


def _fvg_diagnostic_id(fvg):
    if not fvg:
        return None
    fvg_type = fvg.get("type") or fvg.get("direction")
    created = fvg.get("end_index") or fvg.get("created_index")
    top = fvg.get("top")
    bottom = fvg.get("bottom")
    raw = f"{fvg_type}:{created}:{top}:{bottom}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _fvg_diagnostic_snapshot(fvg):
    if not fvg:
        return None
    data = _json_safe(fvg) if not isinstance(fvg, dict) else _json_safe(dict(fvg))
    if not isinstance(data, dict):
        return None
    top = _safe_float(data.get("top"))
    bottom = _safe_float(data.get("bottom"))
    gap_size = round(top - bottom, 10) if top is not None and bottom is not None else None
    invalidated = bool(data.get("invalidated"))
    return {
        "fvg_id": data.get("fvg_id") or _fvg_diagnostic_id(data),
        "direction": data.get("direction") or data.get("type"),
        "created_at": data.get("created_at") or data.get("end_index") or data.get("created_index"),
        "created_index": data.get("created_index") or data.get("end_index"),
        "left_index": data.get("start_index"),
        "middle_index": data.get("middle_index"),
        "right_index": data.get("right_index") or data.get("end_index"),
        "lower": bottom,
        "upper": top,
        "gap_size": gap_size,
        "gap_size_atr": data.get("gap_size_atr") if data.get("gap_size_atr") is not None else data.get("size_atr_ratio"),
        "quality_score": data.get("quality_score"),
        "detected": data.get("detected"),
        "valid": data.get("valid") if data.get("valid") is not None else bool(data.get("detected") and not invalidated),
        "invalidated": invalidated,
        "invalidation_reason": data.get("invalidation_reason"),
        "invalidated_at": data.get("invalidated_at"),
        "invalidation_candle_index": data.get("invalidation_candle_index") or data.get("invalidated_at"),
        "invalidation_price": data.get("invalidation_price"),
        "invalidation_boundary": data.get("invalidation_boundary"),
        "invalidation_operator": data.get("invalidation_operator"),
        "filled_percentage": data.get("filled_percentage") if data.get("filled_percentage") is not None else data.get("overlap_percent"),
        "historical_only": data.get("historical_only"),
        "is_reconstructed": data.get("is_reconstructed"),
        "source_confirmed_trigger_id": data.get("source_confirmed_trigger_id"),
        "source_confirmed_trigger_index": data.get("source_confirmed_trigger_index") or data.get("confirmed_trigger_index"),
        "candidate_id": data.get("candidate_id") or data.get("source_candidate_id"),
    }


def _selected_scenario_for_trace(analysis_data):
    scenario_scan = (analysis_data or {}).get("scenario_scan")
    return getattr(scenario_scan, "selected_scenario", None)


def _selected_scenario_direction(selected, analysis_data=None):
    direction = _object_field(selected, "direction")
    if direction:
        normalized = str(direction).lower()
        if normalized == "long":
            return "bullish"
        if normalized == "short":
            return "bearish"
        return direction
    scenario_scan = (analysis_data or {}).get("scenario_scan")
    direction = getattr(scenario_scan, "selected_direction", None) or (analysis_data or {}).get("direction")
    normalized = str(direction).lower() if direction is not None else None
    if normalized == "long":
        return "bullish"
    if normalized == "short":
        return "bearish"
    return direction


def _scenario_event_payload_by_type(selected, event_type):
    for event in _object_field(selected, "events_used", []) or []:
        if _scenario_event_field(event, "event_type") != event_type:
            continue
        payload = _scenario_event_payload(event)
        if payload:
            return payload
        return {
            "index": _scenario_event_field(event, "index"),
            "event_id": _scenario_event_field(event, "event_id"),
        }
    return None


def _scenario_confirmed_trigger(selected):
    trigger_scan = _object_field(selected, "trigger_scan", {}) or {}
    trigger = trigger_scan.get("confirmed_trigger") if isinstance(trigger_scan, dict) else None
    return trigger or _scenario_event_payload_by_type(selected, "CONFIRMED_TRIGGER_CONFIRMED")


def _scenario_early_trigger(selected):
    trigger_scan = _object_field(selected, "trigger_scan", {}) or {}
    trigger = trigger_scan.get("early_trigger") if isinstance(trigger_scan, dict) else None
    return trigger or _scenario_event_payload_by_type(selected, "EARLY_TRIGGER_CONFIRMED")


def _normalized_invalidation_reason(reason):
    if reason in {"opposite_confirmed_bos", "opposite_confirmed_choch"}:
        return "opposite_confirmed_trigger"
    return reason


def _trigger_research_diagnostics(selected, analysis_data):
    if not selected:
        return None
    trigger = _scenario_confirmed_trigger(selected)
    trigger_type = trigger.get("type") if isinstance(trigger, dict) else None
    trigger_direction = _event_direction(trigger) if trigger else None
    scenario_direction = _selected_scenario_direction(selected, analysis_data)
    return {
        "scenario_direction": scenario_direction,
        "trigger_direction": trigger_direction,
        "same_direction": None if not trigger_direction or not scenario_direction else trigger_direction == scenario_direction,
        "candidate_invalidated_reason": _object_field(selected, "invalidated_reason"),
        "candidate_invalidated_reason_normalized": _normalized_invalidation_reason(_object_field(selected, "invalidated_reason")),
        "last_invalidated_component": _object_field(selected, "last_invalidated_component"),
        "opposite_trigger_invalidation": _normalized_invalidation_reason(_object_field(selected, "invalidated_reason")) == "opposite_confirmed_trigger",
        "confirmed_trigger": {
            "trigger_id": _structure_event_id(trigger) if trigger else None,
            "type": trigger_type,
            "direction": trigger_direction,
            "timestamp": str(trigger.get("index")) if isinstance(trigger, dict) and trigger.get("index") is not None else None,
            "index": str(trigger.get("index")) if isinstance(trigger, dict) and trigger.get("index") is not None else None,
            "quality_score": trigger.get("quality_score") if isinstance(trigger, dict) else None,
            "body_ratio": trigger.get("body_ratio") if isinstance(trigger, dict) else None,
            "displacement_ratio": trigger.get("displacement_ratio") if isinstance(trigger, dict) else None,
            "close_position": trigger.get("close_position") if isinstance(trigger, dict) else None,
            "rvol": trigger.get("rvol") if isinstance(trigger, dict) else None,
            "opposite_wick_ratio": trigger.get("opposite_wick_ratio") if isinstance(trigger, dict) else None,
            "hold_confirmed": trigger.get("hold_confirmed") if isinstance(trigger, dict) else None,
            "failed_conditions": trigger.get("failed_conditions") if isinstance(trigger, dict) else None,
        },
    }


def _research_candles_for_candidate(df_15m_closed, selected_candidate, max_candles=None):
    if df_15m_closed is None or df_15m_closed.empty or not selected_candidate:
        return []
    early = _scenario_early_trigger(selected_candidate)
    if not early or early.get("index") is None:
        return []
    max_candles = max_candles or SCENARIO_RESEARCH_TRACE_MAX_CANDLES
    confirmed = _scenario_confirmed_trigger(selected_candidate)
    early_key = _event_sort_key(early.get("index"))
    confirmed_key = _event_sort_key(confirmed.get("index")) if confirmed and confirmed.get("index") is not None else None
    positions = list(range(len(df_15m_closed.index)))
    early_pos = min(positions, key=lambda pos: abs(_event_sort_key(df_15m_closed.index[pos]) - early_key))
    if confirmed_key is not None:
        confirmed_pos = min(positions, key=lambda pos: abs(_event_sort_key(df_15m_closed.index[pos]) - confirmed_key))
        start = max(0, early_pos - 10)
        end = min(len(df_15m_closed), confirmed_pos + 21)
    else:
        start = max(0, early_pos - 10)
        end = min(len(df_15m_closed), early_pos + 21)
    if end - start > max_candles:
        end = start + max_candles
    candles = []
    for index_position, (idx, row) in enumerate(df_15m_closed.iloc[start:end].iterrows(), start=start):
        candles.append({
            "timestamp": str(idx),
            "open": _safe_float(row.get("open")),
            "high": _safe_float(row.get("high")),
            "low": _safe_float(row.get("low")),
            "close": _safe_float(row.get("close")),
            "volume": _safe_float(row.get("volume")),
            "atr": _safe_float(row.get("atr")),
            "rvol": _safe_float(row.get("rvol")),
            "closed": True,
            "index": str(idx),
            "position": index_position,
        })
    return candles


def _scenario_lifecycle_events(selected, analysis_time, selected_fvg=None):
    if not selected:
        return []
    candidate_id = _object_field(selected, "candidate_id")
    events = []
    previous_stage = None
    type_map = {
        "HTF_CONTEXT_CONFIRMED": "candidate_created",
        "SFP_CONFIRMED": "sfp_confirmed",
        "LIQUIDITY_SWEEP_CONFIRMED": "sfp_confirmed",
        "EARLY_TRIGGER_CONFIRMED": "early_trigger_confirmed",
        "CONFIRMED_TRIGGER_CONFIRMED": "confirmed_trigger_confirmed",
        "FVG_CREATED": "fvg_detected",
        "FVG_RETESTED": "fvg_matched",
        "RISK_VALID": "risk_plan_created",
    }
    for event in _object_field(selected, "events_used", []) or []:
        event_type = _scenario_event_field(event, "event_type")
        mapped_type = type_map.get(event_type, str(event_type).lower() if event_type else None)
        payload = _scenario_event_payload(event)
        events.append({
            "candidate_id": candidate_id,
            "event_type": mapped_type,
            "event_time": str(_scenario_event_field(event, "index")) if _scenario_event_field(event, "index") is not None else None,
            "analysis_time": str(analysis_time) if analysis_time is not None else None,
            "previous_stage": previous_stage,
            "new_stage": event_type,
            "reason": payload.get("reason"),
            "related_trigger_id": payload.get("event_id") or _structure_event_id(payload),
            "related_fvg_id": None,
        })
        previous_stage = event_type
    if selected_fvg and selected_fvg.get("invalidated"):
        events.append({
            "candidate_id": candidate_id,
            "event_type": "fvg_invalidated",
            "event_time": selected_fvg.get("invalidated_at") or selected_fvg.get("invalidation_candle_index"),
            "analysis_time": str(analysis_time) if analysis_time is not None else None,
            "previous_stage": previous_stage,
            "new_stage": previous_stage,
            "reason": selected_fvg.get("invalidation_reason") or "fvg_invalidated",
            "related_trigger_id": None,
            "related_fvg_id": selected_fvg.get("fvg_id"),
        })
    if _object_field(selected, "status") == "invalidated":
        events.append({
            "candidate_id": candidate_id,
            "event_type": "candidate_invalidated",
            "event_time": str(_object_field(selected, "last_event_index")) if _object_field(selected, "last_event_index") is not None else None,
            "analysis_time": str(analysis_time) if analysis_time is not None else None,
            "previous_stage": previous_stage,
            "new_stage": "INVALIDATED",
            "reason": _object_field(selected, "invalidated_reason"),
            "related_trigger_id": None,
            "related_fvg_id": selected_fvg.get("fvg_id") if selected_fvg else None,
        })
    return events


def _build_scenario_research_trace(symbol, timestamp, score_result, analysis_data):
    selected = _selected_scenario_for_trace(analysis_data)
    if not selected:
        return None
    stage = _object_field(selected, "current_step") or _object_field(selected, "status")
    events = _object_field(selected, "events_used", []) or []
    stage_names = {_scenario_event_field(event, "event_type") for event in events}
    if "SFP_CONFIRMED" not in stage_names and "LIQUIDITY_SWEEP_CONFIRMED" not in stage_names and "EARLY_TRIGGER_CONFIRMED" not in stage_names:
        return None

    candidate_id = _object_field(selected, "candidate_id")
    direction = _selected_scenario_direction(selected, analysis_data)
    confirmed_trigger = _scenario_confirmed_trigger(selected)
    confirmed_trigger_index = _event_sort_key(confirmed_trigger.get("index")) if confirmed_trigger and confirmed_trigger.get("index") is not None else None
    confirmed_trigger_id = _structure_event_id(confirmed_trigger) if confirmed_trigger else None
    fvg_candidates = list((analysis_data or {}).get("fvg_candidates") or [])[:SCENARIO_RESEARCH_TRACE_MAX_FVGS]
    candidate_fvgs = [_fvg_diagnostic_snapshot(fvg) for fvg in fvg_candidates]
    candidate_fvgs = [item for item in candidate_fvgs if item]
    selected_fvg = _fvg_diagnostic_snapshot((analysis_data or {}).get("active_fvg"))
    match_trace = []
    for raw_fvg, snapshot in zip(fvg_candidates, candidate_fvgs):
        diagnostics = _fvg_matches_state_machine_scenario(
            raw_fvg,
            direction,
            expected_candidate_id=candidate_id,
            confirmed_trigger_index=confirmed_trigger_index,
            confirmed_trigger_id=confirmed_trigger_id,
            return_diagnostics=True,
        )
        match_trace.append({
            "fvg_id": snapshot.get("fvg_id"),
            **diagnostics["checks"],
            "rejection_reasons": diagnostics["rejection_reasons"],
        })
    candle_trace = list((analysis_data or {}).get("research_15m_candles") or [])[:SCENARIO_RESEARCH_TRACE_MAX_CANDLES]
    return {
        "enabled": True,
        "symbol": symbol,
        "analysis_time": str(timestamp) if timestamp is not None else None,
        "candidate_id": candidate_id,
        "candidate_stage": stage,
        "fvg_diagnostics": {
            "candidates": candidate_fvgs,
            "selected_fvg": selected_fvg,
            "candidate_fvg_created": any(item.get("accepted") for item in match_trace),
            "candidate_fvg_retested": bool(selected_fvg and selected_fvg.get("filled_percentage")),
        },
        "fvg_match_trace": match_trace,
        "trigger_diagnostics": _trigger_research_diagnostics(selected, analysis_data),
        "lifecycle_events": _scenario_lifecycle_events(selected, timestamp, selected_fvg),
        "candle_trace": {
            "candles": candle_trace,
            "count": len(candle_trace),
            "truncated": len((analysis_data or {}).get("research_15m_candles") or []) > SCENARIO_RESEARCH_TRACE_MAX_CANDLES,
        },
    }


def _scenario_scan_snapshot(scenario_output):
    if not scenario_output:
        return None
    if hasattr(scenario_output, 'to_dict'):
        return scenario_output.to_dict()
    return dict(scenario_output)


def _scenario_identity(selected_scenario):
    if selected_scenario is None:
        return None
    scenario_key = getattr(selected_scenario, "scenario_key", None)
    if scenario_key is not None:
        if isinstance(scenario_key, (list, tuple)):
            return "|".join(str(item) for item in scenario_key)
        return str(scenario_key)
    return getattr(selected_scenario, "candidate_id", None)


def _candidate_scoped_trigger_scan(scenario_output, expected_direction=None):
    snapshot = _scenario_scan_snapshot(scenario_output)
    if not snapshot:
        return None
    selected = snapshot.get('selected_scenario')
    if selected:
        trigger_scan = selected.get('trigger_scan')
        if trigger_scan:
            return trigger_scan
    return {
        'expected_direction': expected_direction or snapshot.get('selected_direction') or 'NEUTRAL',
        'selected_trigger': None,
        'confirmed_trigger': None,
        'early_trigger': None,
        'candidate_trigger': None,
        'opposite_trigger': None,
        'sfp_index': None,
        'poi_index': None,
        'anchor_index': None,
        'trigger_index': None,
        'early_trigger_confirmed': False,
        'trigger_confirmed': False,
        'rejected_reason': snapshot.get('reason') or 'no_valid_scenario',
        'waiting_for': None,
    }


def _git_commit_short():
    if GIT_COMMIT:
        return GIT_COMMIT
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=os.path.dirname(os.path.abspath(__file__)),
            check=False,
            capture_output=True,
            text=True,
            timeout=2,
        )
        commit = result.stdout.strip()
        return commit or None
    except Exception:
        return None


def _config_hash():
    payload = {
        "a_plus_delivery_threshold": A_PLUS_DELIVERY_THRESHOLD,
        "min_scenario_fvg_quality": MIN_SCENARIO_FVG_QUALITY,
        "max_scenario_fvg_age": MAX_SCENARIO_FVG_AGE,
        "max_scenario_fvg_retests": MAX_SCENARIO_FVG_RETESTS,
        "min_trigger_quality": MIN_TRIGGER_QUALITY,
        "min_early_trigger_quality": MIN_EARLY_TRIGGER_QUALITY,
        "min_early_trigger_body_ratio": MIN_EARLY_TRIGGER_BODY_RATIO,
        "min_early_trigger_displacement_atr": MIN_EARLY_TRIGGER_DISPLACEMENT_ATR,
        "min_early_trigger_rvol": MIN_EARLY_TRIGGER_RVOL,
        "trigger_link_window_bars": TRIGGER_LINK_WINDOW_BARS,
        "max_trigger_bars_after_sfp": MAX_TRIGGER_BARS_AFTER_SFP,
        "max_trigger_bars_after_poi": MAX_TRIGGER_BARS_AFTER_POI,
        "telegram_report_detail": TELEGRAM_REPORT_DETAIL,
        "send_diagnostic_outside_kz": SEND_DIAGNOSTIC_OUTSIDE_KZ,
        "send_a_plus_outside_kz": SEND_A_PLUS_OUTSIDE_KZ,
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:16]


def _code_hash():
    override = os.environ.get("CODE_HASH")
    if override:
        return override
    root = os.path.dirname(os.path.abspath(__file__))
    digest = hashlib.sha256()
    for relative_path in CODE_HASH_FILES:
        path = os.path.join(root, relative_path)
        try:
            with open(path, "rb") as fh:
                digest.update(relative_path.encode("utf-8"))
                digest.update(b"\0")
                digest.update(fh.read())
                digest.update(b"\0")
        except OSError:
            return None
    return digest.hexdigest()[:16]


def _build_metadata():
    return {
        "app_version": APP_VERSION,
        "git_commit": _git_commit_short(),
        "config_hash": _config_hash(),
        "code_hash": _code_hash(),
        "build_time": BUILD_TIME,
    }


def _build_telegram_delivery_record(
    *,
    run_id,
    message_type,
    attempted,
    sent,
    error,
    status_code=None,
    message_length=None,
    telegram_message_id=None,
    symbol=None,
    candidate_id=None,
    scenario_id=None,
    delivery_gate_result=None,
    in_kill_zone=None,
    outside_kz_delivery_enabled=None,
    kill_zone_bypassed=None,
):
    record = {
        **_build_metadata(),
        "record_type": "telegram_delivery",
        "run_id": run_id,
        "timestamp": pd.Timestamp.now(tz=DEFAULT_TIMEZONE).isoformat(),
        "message_type": message_type,
        "symbol": symbol,
        "candidate_id": candidate_id,
        "scenario_id": scenario_id,
        "attempted": bool(attempted),
        "sent": bool(sent),
        "error": error,
        "status_code": status_code,
        "message_length": message_length,
        "telegram_message_id": telegram_message_id,
        "delivery_gate_result": delivery_gate_result,
    }
    if message_type == "A_PLUS":
        record.update({
            "in_kill_zone": in_kill_zone,
            "outside_kz_delivery_enabled": outside_kz_delivery_enabled,
            "kill_zone_bypassed": kill_zone_bypassed,
        })
    return record


def _record_telegram_delivery(**kwargs):
    if not SCAN_JOURNAL_ENABLED:
        return None
    try:
        return write_scan_record(_build_telegram_delivery_record(**kwargs))
    except Exception as journal_error:
        logger.error(f"Не удалось записать telegram delivery journal: {journal_error}")
        return None


def _analysis_error(symbol, stage, exc, run_id):
    if isinstance(exc, BaseException):
        return {
            "symbol": symbol,
            "stage": stage,
            "exception_type": type(exc).__name__,
            "exception_message": str(exc),
            "traceback": "".join(traceback_module.format_exception(type(exc), exc, exc.__traceback__)),
            "run_id": run_id,
        }
    return {
        "symbol": symbol,
        "stage": stage,
        "exception_type": str(exc),
        "exception_message": str(exc),
        "traceback": None,
        "run_id": run_id,
    }


def _event_type_name(event):
    if event is None:
        return None
    if isinstance(event, dict):
        return str(event.get("event_type") or event.get("type") or "").upper()
    return str(getattr(event, "event_type", "") or "").upper()


def _selected_scenario_snapshot(analysis_data):
    scenario_scan = (analysis_data or {}).get("scenario_scan")
    snapshot = _scenario_scan_snapshot(scenario_scan)
    if not snapshot:
        return None
    return snapshot.get("selected_scenario")


def _selected_scenario_event_types(analysis_data):
    selected = _selected_scenario_snapshot(analysis_data) or {}
    return {
        _event_type_name(event)
        for event in selected.get("events_used") or []
        if _event_type_name(event)
    }


def _a_plus_delivery_gate(score_result, analysis_data, in_kill_zone):
    diagnostics = (score_result or {}).setdefault("diagnostics", {})
    selected = _selected_scenario_snapshot(analysis_data) or {}
    event_types = _selected_scenario_event_types(analysis_data)
    kill_zone_gate = bool(in_kill_zone) or SEND_A_PLUS_OUTSIDE_KZ
    gates = {
        "in_kill_zone": bool(in_kill_zone),
        "outside_kz_delivery_enabled": SEND_A_PLUS_OUTSIDE_KZ,
        "kill_zone_gate": kill_zone_gate,
        "score_threshold": float((score_result or {}).get("total_score", 0) or 0) >= A_PLUS_DELIVERY_THRESHOLD,
        "scenario_valid": bool(diagnostics.get("scenario_scan_valid") or selected.get("scenario_valid")),
        "signal_allowed": bool(diagnostics.get("scenario_scan_signal_allowed") or selected.get("signal_allowed")),
        "trigger_confirmed": bool(diagnostics.get("trigger_confirmed")),
        "fvg_created": "FVG_CREATED" in event_types,
        "fvg_retested": "FVG_RETESTED" in event_types,
        "displacement_confirmed": "DISPLACEMENT_CONFIRMED" in event_types,
        "scenario_risk_valid": bool(diagnostics.get("scenario_risk_valid") or selected.get("risk_valid")),
    }
    required_gate_names = [
        "kill_zone_gate",
        "score_threshold",
        "scenario_valid",
        "signal_allowed",
        "trigger_confirmed",
        "fvg_created",
        "fvg_retested",
        "displacement_confirmed",
        "scenario_risk_valid",
    ]
    return {
        "allowed": all(gates[name] for name in required_gate_names),
        "threshold": A_PLUS_DELIVERY_THRESHOLD,
        "kill_zone_bypassed": bool(not in_kill_zone and SEND_A_PLUS_OUTSIDE_KZ and kill_zone_gate),
        "gates": gates,
        "failed_gates": [name for name in required_gate_names if not gates[name]],
    }


def _annotate_a_plus_delivery_gate(score_result, analysis_data, in_kill_zone):
    gate = _a_plus_delivery_gate(score_result, analysis_data, in_kill_zone)
    diagnostics = score_result.setdefault("diagnostics", {})
    diagnostics["a_plus_delivery_allowed"] = gate["allowed"]
    diagnostics["a_plus_delivery_gate"] = gate
    return gate


def is_a_plus_delivery_allowed(score_result, analysis_data, in_kill_zone):
    return _a_plus_delivery_gate(score_result, analysis_data, in_kill_zone)["allowed"]


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
    selected_scenario = getattr(analysis_data.get('scenario_scan'), "selected_scenario", None)
    selected_candidate_id = getattr(selected_scenario, "candidate_id", None)
    scenario_id = _scenario_identity(selected_scenario)
    risk_snapshot = risk_plan.to_dict() if hasattr(risk_plan, 'to_dict') else risk_plan if isinstance(risk_plan, dict) else {}
    risk_snapshot = risk_snapshot or {}
    risk_config = RiskPlanConfig()
    market_data_timestamps = analysis_data.get('market_data_timestamps') or {}
    scan_interval = analysis_data.get('scan_interval')
    htf_context = analysis_data.get('htf_context')
    shadow_candidate = analysis_data.get('shadow_candidate') or {}
    trigger_diagnostics = _build_trigger_diagnostics(score_result, analysis_data, session)
    shadow_outcome = _empty_shadow_outcome()
    production_a_plus_allowed = score_result.get('diagnostics', {}).get('a_plus_delivery_allowed')
    delivery_gate = score_result.get('diagnostics', {}).get('a_plus_delivery_gate') or {}
    shadow_rejection_reasons = list(shadow_candidate.get('shadow_rejection_reasons') or [])
    for failed_gate in delivery_gate.get("failed_gates") or []:
        reason = f"production_gate_{failed_gate}"
        if reason not in shadow_rejection_reasons:
            shadow_rejection_reasons.append(reason)
    journal_shadow_tier = (
        "A+" if shadow_candidate and production_a_plus_allowed is True else shadow_candidate.get('shadow_tier')
    )

    record = {
        **_build_metadata(),
        'record_type': 'symbol_scan',
        'run_id': run_id,
        'timestamp': timestamp,
        'symbol': symbol,
        'scan_interval': scan_interval,
        'market_data_timestamp_15m': market_data_timestamps.get('15m'),
        'market_data_timestamp_1h': market_data_timestamps.get('1h'),
        'market_data_timestamp_4h': market_data_timestamps.get('4h'),
        'market_open_15m': _safe_float(last_15m.get('open')) if last_15m is not None else None,
        'market_high_15m': _safe_float(last_15m.get('high')) if last_15m is not None else None,
        'market_low_15m': _safe_float(last_15m.get('low')) if last_15m is not None else None,
        'market_close_15m': _safe_float(last_15m.get('close')) if last_15m is not None else None,
        'atr': analysis_data.get('atr'),
        'poi_price': risk_snapshot.get('poi_price'),
        'planned_entry': risk_snapshot.get('entry'),
        'current_price': analysis_data.get('current_price'),
        'planned_entry_distance_from_poi_atr': risk_snapshot.get('entry_distance_from_poi_atr'),
        'current_price_distance_from_poi_atr': risk_snapshot.get('current_price_distance_from_poi_atr'),
        'max_entry_distance_from_poi_atr': risk_snapshot.get('max_entry_distance_from_poi_atr', risk_config.max_entry_distance_from_poi_atr),
        'minimum_stop_distance_percent': risk_snapshot.get('minimum_stop_distance_percent'),
        'minimum_stop_distance_atr': risk_snapshot.get('minimum_stop_distance_atr', risk_config.min_stop_distance_atr),
        'candidate_id': selected_candidate_id,
        'scenario_id': scenario_id,
        'state_machine_allowed': score_result.get('diagnostics', {}).get('state_machine_allowed'),
        'scenario_scan_signal_allowed': score_result.get('diagnostics', {}).get('scenario_scan_signal_allowed'),
        'a_plus_delivery_allowed': score_result.get('diagnostics', {}).get('a_plus_delivery_allowed'),
        'delivery_gate_checks': score_result.get('diagnostics', {}).get('a_plus_delivery_gate'),
        'htf_context': htf_context,
        'trigger_diagnostics': trigger_diagnostics,
        'shadow_tier': journal_shadow_tier,
        'shadow_candidate_id': shadow_candidate.get('shadow_candidate_id'),
        'shadow_direction': shadow_candidate.get('shadow_direction'),
        'shadow_created_at': shadow_candidate.get('shadow_created_at'),
        'shadow_rejection_reasons': shadow_rejection_reasons if shadow_candidate else [],
        'htf_context_class': shadow_candidate.get('htf_context_class'),
        'production_a_plus_allowed': production_a_plus_allowed,
        **shadow_outcome,
        'timeframes': {
            '15m_last_closed': str(last_15m.name) if last_15m is not None else None,
            '1h_last_closed': market_data_timestamps.get('1h'),
            '4h_last_closed': market_data_timestamps.get('4h'),
        },
        'session': session.to_dict() if hasattr(session, 'to_dict') else session,
        'decision': score_result.get('decision'),
        'context_decision': score_result.get('context_decision'),
        'scenario_status': score_result.get('scenario_status'),
        'execution_status': score_result.get('execution_status'),
        'final_decision': score_result.get('final_decision') or score_result.get('decision'),
        'score': score_result.get('total_score'),
        'raw_score': score_result.get('raw_score'),
        'score_components': score_result.get('score_components'),
        'score_components_total': score_result.get('score_components_total'),
        'score_consistent': score_result.get('score_consistent'),
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
            'htf_context': htf_context,
            'trigger_diagnostics': trigger_diagnostics,
            'context_1h': _event_snapshot(analysis_data.get('context_break_1h')),
            'trigger_15m': _event_snapshot(analysis_data.get('trigger_break_15m')),
            'scenario_trigger_15m': _event_snapshot(analysis_data.get('scenario_trigger_15m')),
            'long_trigger_candidate': _trigger_candidate_snapshot(analysis_data.get('long_trigger_candidate')),
            'short_trigger_candidate': _trigger_candidate_snapshot(analysis_data.get('short_trigger_candidate')),
            'sfp': _event_snapshot(analysis_data.get('sfp_data')),
            'premium_discount': premium_discount.to_dict() if hasattr(premium_discount, 'to_dict') else premium_discount,
            'liquidity_map': {
                'nearest_buy_side': _liquidity_level_snapshot(_level_value(liquidity_map, 'nearest_buy_side')),
                'nearest_sell_side': _liquidity_level_snapshot(_level_value(liquidity_map, 'nearest_sell_side')),
                'strongest_buy_side': _liquidity_level_snapshot(_level_value(liquidity_map, 'strongest_buy_side')),
                'strongest_sell_side': _liquidity_level_snapshot(_level_value(liquidity_map, 'strongest_sell_side')),
            },
            'risk_plan': risk_snapshot or None,
            'shadow_candidate': shadow_candidate or None,
            'trigger_debug': analysis_data.get('trigger_debug'),
            'trigger_scan': _trigger_scan_snapshot(analysis_data.get('trigger_scan')),
            'scenario_scan': _scenario_scan_snapshot(analysis_data.get('scenario_scan')),
        },
        'diagnostics': score_result.get('diagnostics', {}),
        'breakdown': score_result.get('breakdown', {}),
        'macro': macro,
    }
    if ENABLE_SCENARIO_RESEARCH_TRACE:
        trace = _build_scenario_research_trace(symbol, timestamp, score_result, analysis_data)
        if trace:
            record["scenario_research_trace"] = trace
    return record


def _scenario_transition_state(candidate):
    if not candidate:
        return None
    status = getattr(candidate, "status", None)
    if status == "invalidated":
        return "INVALIDATED"
    if status == "complete":
        return "SIGNAL_ALLOWED"
    next_step = getattr(candidate, "next_expected_step", None)
    if next_step:
        return f"WAITING_FOR_{str(next_step).upper()}"
    current_step = getattr(candidate, "current_step", None)
    return str(current_step or status or "UNKNOWN").upper()


def _last_scenario_event(candidate):
    events = list(getattr(candidate, "events_used", None) or [])
    return events[-1] if events else None


def _scenario_event_field(event, field, default=None):
    if event is None:
        return default
    if isinstance(event, dict):
        return event.get(field, default)
    return getattr(event, field, default)


def _scenario_event_payload(event):
    payload = _scenario_event_field(event, "payload", None)
    return payload if isinstance(payload, dict) else {}


def _build_scenario_transition_records(run_id, timestamp, symbol, scenario_scan, *, detected_at=None):
    if scenario_scan is None:
        return []
    detected_at = detected_at or timestamp
    candidates = []
    for item in [
        getattr(scenario_scan, "selected_scenario", None),
        *(getattr(scenario_scan, "top_candidates", None) or []),
        *(getattr(scenario_scan, "long_candidates", None) or []),
        *(getattr(scenario_scan, "short_candidates", None) or []),
    ]:
        if item is not None:
            candidates.append(item)

    records = []
    seen = set()
    for candidate in candidates:
        candidate_id = getattr(candidate, "candidate_id", None)
        if not candidate_id or candidate_id in seen:
            continue
        seen.add(candidate_id)
        to_state = _scenario_transition_state(candidate)
        cache_key = (symbol, candidate_id)
        from_state = _SCENARIO_TRANSITION_STATE.get(cache_key)
        if from_state == to_state:
            continue
        _SCENARIO_TRANSITION_STATE[cache_key] = to_state

        event = _last_scenario_event(candidate)
        payload = _scenario_event_payload(event)
        event_time = _scenario_event_field(event, "index", getattr(candidate, "last_event_index", None))
        event_detected_at = payload.get("detected_at") or detected_at
        records.append({
            **_build_metadata(),
            "record_type": "scenario_transition",
            "run_id": run_id,
            "timestamp": timestamp,
            "symbol": symbol,
            "candidate_id": candidate_id,
            "from_state": from_state,
            "to_state": to_state,
            "event_type": _scenario_event_field(event, "event_type", None),
            "event_time": str(event_time) if event_time is not None else None,
            "detected_at": str(event_detected_at) if event_detected_at is not None else None,
            "is_reconstructed": bool(payload.get("is_reconstructed") or payload.get("historical_only")),
            "invalidation_component": getattr(candidate, "last_invalidated_component", None),
            "invalidated_reason": getattr(candidate, "invalidated_reason", None),
        })
    return records


def _apply_runtime_update_counts(symbol, scenario_scan, analysis_time=None):
    if scenario_scan is None:
        return scenario_scan
    analysis_index = _normalize_lifetime_candle_index(analysis_time) if analysis_time is not None else None
    candidates = []
    for item in [
        getattr(scenario_scan, "selected_scenario", None),
        *(getattr(scenario_scan, "top_candidates", None) or []),
        *(getattr(scenario_scan, "long_candidates", None) or []),
        *(getattr(scenario_scan, "short_candidates", None) or []),
    ]:
        if item is not None:
            candidates.append(item)

    seen = set()
    for candidate in candidates:
        candidate_id = getattr(candidate, "candidate_id", None)
        if not candidate_id or candidate_id in seen:
            continue
        seen.add(candidate_id)
        cache_key = (symbol, candidate_id)
        lifecycle = _SCENARIO_RUNTIME_STATE.get(cache_key)
        candidate_anchor_index = _candidate_lifetime_anchor_index(candidate)
        candidate_last_index = _candidate_lifetime_last_index(candidate, analysis_index)
        if lifecycle is None:
            runtime_update_count = int(getattr(candidate, "runtime_update_count", 0) or 0)
            first_index = (
                getattr(candidate, "anchor_first_touch_index", None)
                or getattr(candidate, "candidate_created_at", None)
                or candidate_anchor_index
            )
        else:
            previous_scan_index = lifecycle.get("last_scan_index")
            scan_index_changed = (
                candidate_last_index is not None
                and _event_sort_key(candidate_last_index) != _event_sort_key(previous_scan_index)
            )
            runtime_update_count = int(lifecycle.get("runtime_update_count", 0) or 0) + (1 if scan_index_changed else 0)
            first_index = lifecycle.get("first_index") or candidate_anchor_index
        last_index = candidate_last_index
        market_age_bars = max(
            int(getattr(candidate, "market_age_bars", 0) or 0),
            int(lifecycle.get("market_age_bars", 0) or 0) if lifecycle else 0,
            _market_age_bars_between(first_index, last_index),
        )
        max_wait_bars = _candidate_max_wait_bars(candidate)
        expiration_reason = lifecycle.get("expiration_reason") if lifecycle else None
        candidate_expired = bool(lifecycle.get("candidate_expired")) if lifecycle else False
        if candidate_expired and expiration_reason:
            _expire_scenario_candidate(candidate, expiration_reason)
        if _candidate_should_expire_waiting_for_early_trigger(candidate, market_age_bars, max_wait_bars):
            candidate_expired = True
            expiration_reason = EARLY_TRIGGER_WINDOW_EXPIRED_REASON
            _expire_scenario_candidate(candidate, expiration_reason)
        _SCENARIO_RUNTIME_STATE[cache_key] = {
            "runtime_update_count": runtime_update_count,
            "first_index": first_index,
            "last_index": last_index,
            "last_scan_index": last_index,
            "market_age_bars": market_age_bars,
            "max_wait_bars": max_wait_bars,
            "candidate_expired": candidate_expired,
            "expiration_reason": expiration_reason,
        }
        candidate.runtime_update_count = runtime_update_count
        candidate.market_age_bars = market_age_bars
        candidate.age_bars = market_age_bars
        if isinstance(getattr(candidate, "trigger_scan", None), dict):
            anchor_time = _candidate_lifetime_anchor_index(candidate)
            candidate.trigger_scan["runtime_update_count"] = runtime_update_count
            candidate.trigger_scan["market_age_bars"] = market_age_bars
            candidate.trigger_scan["age_bars"] = market_age_bars
            candidate.trigger_scan["candidate_anchor_time"] = str(anchor_time) if anchor_time is not None else None
            candidate.trigger_scan["candidate_anchor_index"] = str(anchor_time) if anchor_time is not None else None
            candidate.trigger_scan["analysis_time"] = str(last_index) if last_index is not None else None
            candidate.trigger_scan["bars_waiting"] = market_age_bars
            candidate.trigger_scan["max_wait_bars"] = max_wait_bars
            candidate.trigger_scan["bars_remaining"] = max(max_wait_bars - market_age_bars, 0)
            candidate.trigger_scan["scans_waiting"] = runtime_update_count
            candidate.trigger_scan["candidate_expired"] = candidate_expired
            candidate.trigger_scan["expiration_reason"] = expiration_reason
            if candidate_expired:
                candidate.trigger_scan["rejected_reason"] = expiration_reason
                candidate.trigger_scan["waiting_for"] = None
                candidate.trigger_scan["early_trigger_confirmed"] = False
                candidate.trigger_scan["trigger_confirmed"] = False
                candidate.trigger_scan["selected_trigger"] = None
                candidate.trigger_scan["confirmed_trigger"] = None
                candidate.trigger_scan["early_trigger"] = None
    _refresh_scenario_selection_after_lifetime(scenario_scan)
    for cache_key in list(_SCENARIO_RUNTIME_STATE.keys()):
        if cache_key[0] == symbol and cache_key[1] not in seen:
            del _SCENARIO_RUNTIME_STATE[cache_key]
    return scenario_scan


def _candidate_lifetime_anchor_index(candidate):
    trigger_scan = getattr(candidate, "trigger_scan", None)
    if isinstance(trigger_scan, dict):
        for key in ("sfp_index", "anchor_index", "candidate_anchor_index"):
            if trigger_scan.get(key) is not None:
                return trigger_scan.get(key)
    return getattr(candidate, "anchor_index", None)


def _normalize_lifetime_candle_index(value):
    if value is None:
        return None
    try:
        ts = pd.Timestamp(value)
    except Exception:
        return value
    if ts.tzinfo is not None:
        return ts.tz_convert(None)
    return ts


def _candidate_lifetime_last_index(candidate, analysis_index):
    if analysis_index is not None:
        return analysis_index
    return (
        getattr(candidate, "last_event_index", None)
        or getattr(candidate, "anchor_last_touch_index", None)
        or _candidate_lifetime_anchor_index(candidate)
    )


def _candidate_max_wait_bars(candidate):
    trigger_scan = getattr(candidate, "trigger_scan", None)
    if isinstance(trigger_scan, dict) and trigger_scan.get("max_wait_bars") is not None:
        try:
            return int(trigger_scan.get("max_wait_bars"))
        except (TypeError, ValueError):
            pass
    return int(MAX_TRIGGER_BARS_AFTER_SFP)


def _candidate_has_early_or_confirmed_trigger(candidate):
    trigger_scan = getattr(candidate, "trigger_scan", None)
    if isinstance(trigger_scan, dict):
        if trigger_scan.get("early_trigger_confirmed") or trigger_scan.get("trigger_confirmed"):
            return True
        if trigger_scan.get("early_trigger") or trigger_scan.get("confirmed_trigger"):
            return True
    for event in getattr(candidate, "events_used", None) or []:
        event_type = _scenario_event_field(event, "event_type")
        if event_type in {"EARLY_TRIGGER_CONFIRMED", "CONFIRMED_TRIGGER_CONFIRMED", "CHOCH_CONFIRMED", "BOS_CONFIRMED"}:
            return True
    return False


def _candidate_should_expire_waiting_for_early_trigger(candidate, market_age_bars, max_wait_bars):
    if getattr(candidate, "status", None) == "invalidated":
        return False
    if _candidate_has_early_or_confirmed_trigger(candidate):
        return False
    next_step = getattr(candidate, "next_expected_step", None)
    waiting_for = getattr(candidate, "waiting_for", None)
    trigger_scan = getattr(candidate, "trigger_scan", None)
    scan_waiting_for = trigger_scan.get("waiting_for") if isinstance(trigger_scan, dict) else None
    is_waiting_early = (
        next_step == "EARLY_TRIGGER_CONFIRMED"
        or waiting_for in {"bullish CHOCH/BOS after SFP", "bearish CHOCH/BOS after SFP"}
        or scan_waiting_for in {"bullish CHOCH/BOS after SFP", "bearish CHOCH/BOS after SFP"}
        or (isinstance(trigger_scan, dict) and str(trigger_scan.get("rejected_reason") or "").startswith("no_") and "trigger_after_sfp" in str(trigger_scan.get("rejected_reason")))
    )
    return bool(is_waiting_early and int(market_age_bars or 0) > int(max_wait_bars))


def _expire_scenario_candidate(candidate, reason):
    if getattr(candidate, "status", None) == "invalidated" and getattr(candidate, "invalidated_reason", None) == reason:
        return
    previous_stage = getattr(candidate, "current_step", None)
    candidate.status = "invalidated"
    candidate.signal_allowed = False
    candidate.scenario_valid = False
    candidate.invalidated_reason = reason
    candidate.last_invalidated_component = "early_trigger"
    candidate.candidate_invalidated = True
    candidate.waiting_for = None
    observation = {
        "event_type": "candidate_expired",
        "previous_stage": previous_stage,
        "new_stage": "INVALIDATED",
        "reason": reason,
    }
    pending = list(getattr(candidate, "pending_observations", None) or [])
    if observation not in pending:
        pending.append(observation)
    candidate.pending_observations = pending
    diagnostics = list(getattr(candidate, "event_diagnostics", None) or [])
    if not any(item.get("reason") == reason for item in diagnostics if isinstance(item, dict)):
        diagnostics.append({
            "event_type": "candidate_expired",
            "failed_conditions": [reason],
            "reason": reason,
        })
    candidate.event_diagnostics = diagnostics


def _refresh_scenario_selection_after_lifetime(scenario_scan):
    selected = getattr(scenario_scan, "selected_scenario", None)
    if selected is not None and getattr(selected, "status", None) != "invalidated":
        return
    candidates = []
    for item in [
        *(getattr(scenario_scan, "top_candidates", None) or []),
        *(getattr(scenario_scan, "long_candidates", None) or []),
        *(getattr(scenario_scan, "short_candidates", None) or []),
    ]:
        if item is not None and getattr(item, "status", None) != "invalidated":
            candidates.append(item)
    seen = set()
    unique = []
    for candidate in candidates:
        candidate_id = getattr(candidate, "candidate_id", None)
        if candidate_id in seen:
            continue
        seen.add(candidate_id)
        unique.append(candidate)
    if unique:
        replacement = max(unique, key=lambda item: (getattr(item, "selection_eligible", False), getattr(item, "quality_score", 0), -int(getattr(item, "rank", 999) or 999)))
        scenario_scan.selected_scenario = replacement
        scenario_scan.selected_scenario_id = getattr(replacement, "candidate_id", None)
        scenario_scan.selected_direction = getattr(replacement, "direction", None)
        scenario_scan.signal_allowed = bool(getattr(replacement, "signal_allowed", False))
        scenario_scan.scenario_valid = bool(getattr(replacement, "scenario_valid", False))
        scenario_scan.reason = getattr(replacement, "invalidated_reason", None) or getattr(replacement, "waiting_for", None) or scenario_scan.reason
        return
    scenario_scan.selected_scenario = None
    scenario_scan.selected_scenario_id = None
    scenario_scan.selected_direction = None
    scenario_scan.signal_allowed = False
    scenario_scan.scenario_valid = False
    if selected is not None and getattr(selected, "invalidated_reason", None):
        scenario_scan.reason = getattr(selected, "invalidated_reason")


def _market_age_bars_between(first_index, last_index):
    if first_index is None or last_index is None:
        return 0
    if isinstance(first_index, (int, float)) and isinstance(last_index, (int, float)):
        return max(0, int(float(last_index) - float(first_index)))
    try:
        first_ts = pd.Timestamp(first_index)
        last_ts = pd.Timestamp(last_index)
    except Exception:
        return 0
    if first_ts.tzinfo is not None and last_ts.tzinfo is None:
        first_ts = first_ts.tz_convert(None)
    elif first_ts.tzinfo is None and last_ts.tzinfo is not None:
        last_ts = last_ts.tz_convert(None)
    elif first_ts.tzinfo is not None and last_ts.tzinfo is not None:
        first_ts = first_ts.tz_convert("UTC")
        last_ts = last_ts.tz_convert("UTC")
    if last_ts < first_ts:
        return 0
    return max(0, int((last_ts - first_ts) / pd.Timedelta(minutes=15)))


def _build_run_summary_record(
    *,
    run_id,
    started_at,
    finished_at,
    duration_seconds,
    report_mode,
    session,
    symbol_results,
    errors,
):
    successful = [item for item in symbol_results if item.get("success")]
    universe_total = len(COINS_LIST)
    symbols_success = len(successful)
    symbols_failed = len(errors)
    coverage_percent = round((symbols_success / universe_total) * 100, 2) if universe_total else 0.0
    if symbols_success == universe_total and symbols_failed == 0:
        run_status = "SUCCESS"
    elif symbols_success > 0:
        run_status = "PARTIAL_SUCCESS"
    else:
        run_status = "FAILED"
    audit_eligible = run_status == "SUCCESS" and all(
        (item.get("error") or {}).get("exception_type") != "NoAnalysisData"
        for item in symbol_results
    )
    decisions = [item.get("final_decision") or item.get("decision") for item in successful]
    context_decisions = [item.get("context_decision") for item in successful]
    diagnostics = [item.get("diagnostics") or {} for item in successful]
    analysis_items = [item.get("analysis_data") or {} for item in successful]
    scenario_scans = [
        _scenario_scan_snapshot(item.get("scenario_scan"))
        for item in analysis_items
        if item.get("scenario_scan") is not None
    ]
    top_candidates = [
        candidate
        for scan in scenario_scans
        for candidate in (scan or {}).get("top_candidates", [])
    ]
    global_trigger_scans = [
        _trigger_scan_snapshot(item.get("global_trigger_scan")) or {}
        for item in analysis_items
    ]
    selected_event_types = [_selected_scenario_event_types(item) for item in analysis_items]

    return {
        **_build_metadata(),
        "record_type": "run_summary",
        "run_id": run_id,
        "started_at": started_at,
        "finished_at": finished_at,
        "duration_seconds": round(float(duration_seconds or 0.0), 2),
        "report_mode": report_mode,
        "session": session.to_dict() if hasattr(session, "to_dict") else session,
        "symbols_total": universe_total,
        "symbols_success": symbols_success,
        "symbols_failed": symbols_failed,
        "run_status": run_status,
        "coverage_percent": coverage_percent,
        "audit_eligible": audit_eligible,
        "ignore_count": sum(1 for decision in decisions if decision == "Ignore"),
        "watchlist_count": sum(1 for decision in decisions if decision == "Watchlist"),
        "context_watchlist_count": sum(1 for decision in context_decisions if decision in ("Watchlist", "A+ WATCH ONLY")),
        "a_plus_count": sum(1 for decision in decisions if decision == "A+"),
        "sfp_present_count": sum(1 for diag in diagnostics if diag.get("sfp_present")),
        "early_trigger_count": sum(1 for diag in diagnostics if diag.get("early_trigger_confirmed")),
        "active_confirmed_trigger_count": sum(1 for diag in diagnostics if diag.get("trigger_confirmed")),
        "confirmed_trigger_count": sum(1 for diag in diagnostics if diag.get("trigger_confirmed")),
        "historical_confirmed_trigger_count": sum(1 for scan in global_trigger_scans if scan.get("trigger_confirmed")),
        "scenario_valid_count": sum(1 for diag in diagnostics if diag.get("scenario_scan_valid")),
        "signal_allowed_count": sum(1 for diag in diagnostics if diag.get("scenario_scan_signal_allowed")),
        "risk_geometry_valid_count": sum(1 for diag in diagnostics if diag.get("risk_geometry_valid")),
        "scenario_risk_valid_count": sum(1 for diag in diagnostics if diag.get("scenario_risk_valid")),
        "fvg_created_count": sum(1 for types in selected_event_types if "FVG_CREATED" in types),
        "fvg_retested_count": sum(1 for types in selected_event_types if "FVG_RETESTED" in types),
        "displacement_confirmed_count": sum(1 for types in selected_event_types if "DISPLACEMENT_CONFIRMED" in types),
        "invalidated_scenario_count": sum(1 for candidate in top_candidates if candidate.get("status") == "invalidated"),
        "selection_ineligible_count": sum(1 for candidate in top_candidates if candidate.get("selection_eligible") is False),
        "a_plus_delivery_allowed_count": sum(1 for diag in diagnostics if diag.get("a_plus_delivery_allowed")),
        "errors": list(errors or []),
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


def _detect_early_trigger_candidates(df_15m_closed, sfp_data, poi_data, max_bars=24):
    anchor_index = _early_anchor_index(sfp_data, poi_data)
    if anchor_index is None or df_15m_closed is None or df_15m_closed.empty:
        return []

    micro_highs, micro_lows = find_swings(df_15m_closed, left_bars=2, right_bars=1)
    anchor_sort = _event_sort_key(anchor_index)
    window = df_15m_closed[_event_sort_key_index(df_15m_closed.index) > anchor_sort].head(max_bars)
    candidates = []
    for index, candle in window.iterrows():
        bullish = _build_early_trigger_candidate("bullish", candle, index, micro_highs, micro_lows, anchor_sort)
        bearish = _build_early_trigger_candidate("bearish", candle, index, micro_highs, micro_lows, anchor_sort)
        if bullish:
            candidates.append(bullish)
        if bearish:
            candidates.append(bearish)
    return candidates


def _event_sort_key_index(index):
    return pd.Index([_event_sort_key(item) for item in index])


def _early_anchor_index(sfp_data, poi_data):
    sfp_index = sfp_data.get('index') if sfp_data else None
    if sfp_index is not None:
        return sfp_index
    return poi_data.get('index') if poi_data else None


def _build_early_trigger_candidate(direction, candle, index, micro_highs, micro_lows, anchor_sort):
    candle_range = float(candle.get('high', 0.0) or 0.0) - float(candle.get('low', 0.0) or 0.0)
    if candle_range <= 0:
        return None
    open_price = float(candle.get('open', 0.0) or 0.0)
    close_price = float(candle.get('close', 0.0) or 0.0)
    high_price = float(candle.get('high', 0.0) or 0.0)
    low_price = float(candle.get('low', 0.0) or 0.0)
    body_ratio = abs(close_price - open_price) / candle_range
    atr = float(candle.get('atr', 0.0) or 0.0)
    displacement_ratio = abs(close_price - open_price) / atr if atr > 0 else 0.0
    close_position = (close_price - low_price) / candle_range
    rvol = float(candle.get('rvol', 0.0) or 0.0)
    absorption_warning = bool(rvol >= 1.8 and body_ratio < 0.35)

    if body_ratio < MIN_EARLY_TRIGGER_BODY_RATIO:
        return None
    if displacement_ratio < MIN_EARLY_TRIGGER_DISPLACEMENT_ATR:
        return None
    if absorption_warning:
        return None

    if direction == "bullish":
        level = _latest_micro_level(micro_highs, "high", index, anchor_sort)
        if level is None or close_price <= level or close_position < 0.6:
            return None
        trigger_type = "bullish_early_choch"
        micro_break_confirmed = True
    else:
        level = _latest_micro_level(micro_lows, "low", index, anchor_sort)
        if level is None or close_price >= level or close_position > 0.4:
            return None
        trigger_type = "bearish_early_choch"
        micro_break_confirmed = True

    if rvol < MIN_EARLY_TRIGGER_RVOL and not micro_break_confirmed:
        return None

    quality = _early_trigger_quality(body_ratio, displacement_ratio, rvol, close_position, direction)
    if quality < MIN_EARLY_TRIGGER_QUALITY:
        return None

    return {
        "type": trigger_type,
        "direction": direction,
        "index": index,
        "level": round(float(level), 8),
        "quality_score": quality,
        "body_ratio": round(body_ratio, 4),
        "displacement_ratio": round(displacement_ratio, 4),
        "close_position": round(close_position, 4),
        "rvol": round(rvol, 4),
        "absorption_warning": absorption_warning,
        "micro_break_confirmed": micro_break_confirmed,
        "trigger_stage": "early",
        "is_early": True,
        "is_confirmed": False,
        "reason": f"micro swing {'high' if direction == 'bullish' else 'low'} break after SFP/POI",
    }


def _latest_micro_level(swings, column, index, anchor_sort):
    if swings is None or swings.empty:
        return None
    eligible = swings[
        (_event_sort_key_index(swings.index) > anchor_sort)
        & (_event_sort_key_index(swings.index) < _event_sort_key(index))
    ]
    if eligible.empty:
        return None
    return float(eligible.iloc[-1][column])


def _early_trigger_quality(body_ratio, displacement_ratio, rvol, close_position, direction):
    quality = 40
    quality += min(body_ratio * 20, 20)
    quality += min(displacement_ratio * 15, 20)
    quality += min(rvol * 5, 10)
    if direction == "bullish":
        quality += max(0.0, close_position - 0.5) * 20
    else:
        quality += max(0.0, 0.5 - close_position) * 20
    return int(round(max(0, min(100, quality))))


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


def _as_dict(item):
    if item is None:
        return None
    if hasattr(item, 'to_dict'):
        return item.to_dict()
    return dict(item)


def _scenario_fvg_reject_reason(fvg):
    if not fvg:
        return 'no_fvg_candidate'
    if fvg.get('invalidated', False):
        return 'fvg_invalidated'
    if int(fvg.get('quality_score', 0) or 0) < MIN_SCENARIO_FVG_QUALITY:
        return 'fvg_quality_below_min'
    if int(fvg.get('age_bars', 0) or 0) > MAX_SCENARIO_FVG_AGE:
        return 'fvg_too_old'
    if int(fvg.get('retest_count', 0) or 0) > MAX_SCENARIO_FVG_RETESTS:
        return 'fvg_too_many_retests'
    return None


def _annotate_scenario_fvgs(fvg_data):
    annotated = []
    for item in fvg_data or []:
        fvg = _as_dict(item)
        reject_reason = _scenario_fvg_reject_reason(fvg)
        fvg['scenario_valid'] = reject_reason is None
        fvg['scenario_reject_reason'] = reject_reason
        annotated.append(fvg)
    return annotated


def _directional_fvgs(direction, fvg_data):
    state_direction = _direction_to_state_direction(direction)
    if state_direction is None:
        return []
    target_type = 'bullish' if state_direction == 'bullish' else 'bearish'
    return [
        fvg for fvg in (fvg_data or [])
        if fvg.get('type') == target_type
    ]


def _select_scenario_fvg(direction, fvg_test_data, fvg_data):
    candidates = [
        fvg for fvg in _directional_fvgs(direction, fvg_data)
        if fvg.get('scenario_valid', _scenario_fvg_reject_reason(fvg) is None)
        and (fvg.get('tested', False) or bool(fvg_test_data))
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda item: _event_sort_key(item.get('end_index')))


def _select_candidate_scenario_fvg(direction, fvg_test_data, fvg_data, expected_candidate_id=None):
    candidates = []
    for fvg in _directional_fvgs(direction, fvg_data):
        if not fvg.get('scenario_valid', _scenario_fvg_reject_reason(fvg) is None):
            continue
        source_candidate_id = fvg.get('source_candidate_id') or fvg.get('candidate_id')
        if expected_candidate_id is not None and source_candidate_id is not None and str(source_candidate_id) != str(expected_candidate_id):
            continue
        candidates.append(fvg)
    if not candidates:
        return None
    return max(candidates, key=lambda item: _event_sort_key(item.get('end_index')))


def _fvg_for_state_machine(direction, fvg_test_data, fvg_data, current_price, expected_candidate_id=None):
    state_direction = _direction_to_state_direction(direction)
    if state_direction is None:
        return None

    fvg = _select_candidate_scenario_fvg(direction, fvg_test_data, fvg_data, expected_candidate_id)
    if fvg:
        return {
            'detected': True,
            'direction': state_direction,
            'type': fvg.get('type'),
            'tested': bool(fvg.get('tested', False) or fvg_test_data),
            'invalidated': False,
            'scenario_valid': True,
            'quality_score': fvg.get('quality_score'),
            'age_bars': fvg.get('age_bars'),
            'retest_count': fvg.get('retest_count'),
            'end_index': fvg.get('end_index'),
            'created_index': fvg.get('created_index') or fvg.get('end_index'),
            'test_index': fvg_test_data.get('index') if fvg_test_data else None,
            'source_candidate_id': fvg.get('source_candidate_id') or fvg.get('candidate_id'),
            'source_confirmed_trigger_id': fvg.get('source_confirmed_trigger_id'),
            'source_confirmed_trigger_index': fvg.get('source_confirmed_trigger_index') or fvg.get('confirmed_trigger_index'),
            'historical_only': fvg.get('historical_only', False),
            'is_reconstructed': fvg.get('is_reconstructed', False),
            'displacement_index': fvg.get('displacement_index') or (fvg_test_data or {}).get('displacement_index'),
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


def _event_within_15m_window(later_index, earlier_index, bars=5):
    if later_index is None or earlier_index is None:
        return False
    try:
        if later_index <= earlier_index:
            return False
        delta = later_index - earlier_index
        if isinstance(later_index, (int, float)):
            if later_index > 1e11:
                return delta <= bars * 15 * 60 * 1000
            if later_index > 1e8:
                return delta <= bars * 15 * 60
            return delta <= bars
        return delta <= pd.Timedelta(minutes=bars * 15)
    except (TypeError, ValueError):
        return _event_sort_key(later_index) > _event_sort_key(earlier_index)


def _find_latest_directional_fvg(direction, fvg_data):
    candidates = _directional_fvgs(direction, fvg_data)
    if not candidates:
        return None
    return max(candidates, key=lambda item: _event_sort_key(item.get('end_index')))


def _direction_label(direction):
    if direction == 'bullish':
        return 'LONG'
    if direction == 'bearish':
        return 'SHORT'
    return 'NEUTRAL'


def _format_trigger_name(trigger):
    if not trigger:
        return 'none'
    trigger_type = str(trigger.get('type', 'trigger'))
    parts = trigger_type.split('_')
    if len(parts) >= 3 and parts[1] == 'early':
        name = f"{parts[0]} {parts[2].upper()}"
    elif len(parts) >= 3 and parts[1] == 'micro':
        name = f"{parts[0]} MICRO"
    elif len(parts) >= 2:
        name = f"{parts[0]} {parts[1].upper()}"
    else:
        name = trigger_type
    quality = trigger.get('quality_score')
    return f"{name} Q{int(quality)}" if quality is not None else name


def _candidate_for_direction(direction, long_trigger_candidate, short_trigger_candidate):
    if direction == 'bullish':
        return long_trigger_candidate
    if direction == 'bearish':
        return short_trigger_candidate
    return None


def _opposite_candidate_for_direction(direction, long_trigger_candidate, short_trigger_candidate):
    if direction == 'bullish':
        return short_trigger_candidate
    if direction == 'bearish':
        return long_trigger_candidate
    return None


def _missing_trigger_reason(direction, sfp_data, fvg_test_data):
    if direction == 'bullish':
        return 'no_bullish_trigger_after_sfp_or_poi' if (sfp_data or fvg_test_data) else 'no_15m_trigger_found'
    if direction == 'bearish':
        return 'no_bearish_trigger_after_sfp_or_poi' if (sfp_data or fvg_test_data) else 'no_15m_trigger_found'
    return 'no_15m_trigger_found'


def _build_trigger_debug(
    direction,
    trigger_structure,
    sfp_data,
    fvg_test_data,
    fvg_data,
    premium_discount_data,
    long_trigger_candidate=None,
    short_trigger_candidate=None,
    trigger_scan=None,
):
    state_direction = _direction_to_state_direction(direction)
    expected_direction = state_direction or 'neutral'
    latest_fvg = _find_latest_directional_fvg(direction, fvg_data)
    scenario_fvg = _select_scenario_fvg(direction, fvg_test_data, fvg_data)
    scan_long_candidate = long_trigger_candidate
    scan_short_candidate = short_trigger_candidate
    trigger_structure_direction = _event_direction(trigger_structure)
    if trigger_structure_direction == 'bullish' and scan_long_candidate is None:
        scan_long_candidate = trigger_structure
    elif trigger_structure_direction == 'bearish' and scan_short_candidate is None:
        scan_short_candidate = trigger_structure
    if trigger_scan is None:
        trigger_scan = scan_post_anchor_trigger(
            expected_direction=_direction_label(state_direction),
            sfp=sfp_data,
            poi=fvg_test_data,
            long_trigger_candidate=scan_long_candidate,
            short_trigger_candidate=scan_short_candidate,
            max_bars_after_sfp=MAX_TRIGGER_BARS_AFTER_SFP,
            max_bars_after_poi=MAX_TRIGGER_BARS_AFTER_POI,
            min_trigger_quality=MIN_TRIGGER_QUALITY,
        )
    trigger_scan_data = trigger_scan.to_dict() if hasattr(trigger_scan, 'to_dict') else dict(trigger_scan)
    selected_candidate = trigger_scan_data.get('selected_trigger')
    confirmed_candidate = trigger_scan_data.get('confirmed_trigger')
    early_candidate = trigger_scan_data.get('early_trigger')
    candidate_trigger = trigger_scan_data.get('candidate_trigger')
    opposite_candidate = trigger_scan_data.get('opposite_trigger')

    if selected_candidate is None and trigger_structure and _event_direction(trigger_structure) == state_direction:
        # Compatibility fallback only for non-scenario calls with no explicit scan result.
        selected_candidate = trigger_structure if trigger_scan_data.get('trigger_confirmed') else None
    if state_direction is not None and opposite_candidate is None and trigger_structure and _event_direction(trigger_structure) not in (None, state_direction):
        opposite_candidate = trigger_structure
    if state_direction is None:
        opposite_candidate = None
    debug_candidate = selected_candidate or candidate_trigger

    debug = {
        'expected_direction': _direction_label(state_direction),
        'selected_trigger': _trigger_candidate_snapshot(selected_candidate) if trigger_scan_data.get('trigger_confirmed') else None,
        'confirmed_trigger': _trigger_candidate_snapshot(confirmed_candidate),
        'early_trigger': _trigger_candidate_snapshot(early_candidate),
        'candidate_trigger': _trigger_candidate_snapshot(candidate_trigger),
        'long_trigger_candidate': _trigger_candidate_snapshot(long_trigger_candidate),
        'short_trigger_candidate': _trigger_candidate_snapshot(short_trigger_candidate),
        'opposite_trigger': _trigger_candidate_snapshot(opposite_candidate),
        'trigger_candidate_type': debug_candidate.get('type') if debug_candidate else None,
        'trigger_candidate_direction': _event_direction(debug_candidate),
        'trigger_candidate_quality': debug_candidate.get('quality_score') if debug_candidate else None,
        'trigger_rejected_reason': trigger_scan_data.get('rejected_reason'),
        'trigger_index': trigger_scan_data.get('trigger_index') or (debug_candidate.get('index') if debug_candidate else None),
        'sfp_index': trigger_scan_data.get('sfp_index'),
        'anchor_index': trigger_scan_data.get('anchor_index'),
        'fvg_index': (scenario_fvg or latest_fvg or {}).get('end_index') if (scenario_fvg or latest_fvg) else None,
        'poi_index': trigger_scan_data.get('poi_index'),
        'expected_state_direction': expected_direction,
        'fvg_scenario_valid': bool(scenario_fvg),
        'fvg_rejected_reason': _scenario_fvg_reject_reason(latest_fvg) if latest_fvg else None,
        'trigger_confirmed': bool(trigger_scan_data.get('trigger_confirmed')),
        'early_trigger_confirmed': bool(trigger_scan_data.get('early_trigger_confirmed')),
        'trigger_scan': _trigger_scan_snapshot(trigger_scan),
    }

    if state_direction is None:
        return debug
    if not trigger_scan_data.get('trigger_confirmed'):
        debug['trigger_rejected_reason'] = trigger_scan_data.get('rejected_reason') or _missing_trigger_reason(state_direction, sfp_data, fvg_test_data)
        return debug
    if _event_direction(selected_candidate) != state_direction:
        debug['trigger_rejected_reason'] = 'trigger_direction_conflict'
        debug['selected_trigger'] = None
        debug['trigger_confirmed'] = False
        return debug
    if int(selected_candidate.get('quality_score', 0) or 0) < MIN_TRIGGER_QUALITY:
        debug['trigger_rejected_reason'] = 'trigger_quality_below_min'
        debug['selected_trigger'] = None
        debug['trigger_confirmed'] = False
        return debug

    if fvg_test_data:
        if not scenario_fvg:
            debug['trigger_rejected_reason'] = debug['fvg_rejected_reason'] or 'fvg_quality_below_min'
            debug['selected_trigger'] = None
            debug['trigger_confirmed'] = False
            return debug

    debug['trigger_rejected_reason'] = None
    debug['trigger_confirmed'] = True
    return debug


def _format_trigger_debug(trigger_debug):
    if not trigger_debug:
        return '0'
    reason = trigger_debug.get('trigger_rejected_reason') or 'unknown'
    expected = trigger_debug.get('expected_direction') or 'NEUTRAL'
    selected = trigger_debug.get('selected_trigger')
    early = trigger_debug.get('early_trigger')
    opposite = trigger_debug.get('opposite_trigger')
    fvg_reason = trigger_debug.get('fvg_rejected_reason')
    opposite_text = f" | opposite: {_format_trigger_name(opposite)}" if opposite and expected != 'NEUTRAL' else ""
    fvg_text = f" | fvg: {fvg_reason}" if fvg_reason else ""

    if expected == 'NEUTRAL':
        candidate = trigger_debug.get('candidate_trigger')
        candidate_text = f" | candidate: {_format_trigger_name(candidate)}" if candidate else ""
        return f"skipped — no trade direction because HTF is Neutral{candidate_text}"

    if selected and trigger_debug.get('trigger_confirmed'):
        return f"{_format_trigger_name(selected)} confirmed after SFP/POI{opposite_text}"

    if early and trigger_debug.get('early_trigger_confirmed'):
        return f"early {_format_trigger_name(early)} after SFP/POI — waiting for confirmed BOS{opposite_text}{fvg_text}"

    if reason in ('no_bullish_trigger_after_sfp_or_poi', 'no_bearish_trigger_after_sfp_or_poi'):
        direction_word = 'bullish' if expected == 'LONG' else 'bearish'
        return f"waiting — no {direction_word} CHOCH/BOS after SFP/POI{opposite_text}{fvg_text}"

    return f"rejected — {reason} for {expected}{opposite_text}{fvg_text}"


def _format_trigger_scan(trigger_scan):
    snapshot = _trigger_scan_snapshot(trigger_scan)
    if not snapshot:
        return '0'
    expected = snapshot.get('expected_direction') or 'NEUTRAL'
    selected = snapshot.get('selected_trigger')
    early = snapshot.get('early_trigger')
    reason = snapshot.get('rejected_reason')
    waiting_for = snapshot.get('waiting_for')
    pre_sfp = snapshot.get('pre_sfp_trigger')
    pre_poi = snapshot.get('pre_poi_trigger')
    opposite = snapshot.get('opposite_trigger')
    opposite_text = f" | opposite: {_format_trigger_name(opposite)}" if opposite and expected != 'NEUTRAL' else ""

    if expected == 'NEUTRAL':
        candidate = snapshot.get('candidate_trigger')
        candidate_text = f" | candidate: {_format_trigger_name(candidate)}" if candidate else ""
        return f"skipped — no trade direction because HTF is Neutral{candidate_text}"

    if selected and snapshot.get('trigger_confirmed'):
        if early:
            return f"confirmed {_format_trigger_name(selected)} after early CHOCH"
        anchor = 'SFP' if snapshot.get('sfp_index') is not None else 'POI'
        return f"confirmed — {_format_trigger_name(selected)} after {anchor}"

    if early and snapshot.get('early_trigger_confirmed'):
        anchor = 'SFP' if snapshot.get('sfp_index') is not None else 'POI'
        debug_suffix = _format_confirmed_trigger_debug_suffix(snapshot.get('confirmed_trigger_debug'))
        return f"early {_format_trigger_name(early)} after {anchor} — waiting for confirmed BOS{debug_suffix}"

    if reason in ('trigger_before_sfp', 'trigger_before_poi'):
        trigger = pre_sfp if reason == 'trigger_before_sfp' else pre_poi
        anchor = 'SFP' if reason == 'trigger_before_sfp' else 'POI'
        return f"rejected — {_format_trigger_name(trigger)} was before {anchor}"

    if reason in ('no_bullish_trigger_after_sfp_or_poi', 'no_bearish_trigger_after_sfp_or_poi'):
        direction_word = 'bullish' if expected == 'LONG' else 'bearish'
        return f"waiting — no {direction_word} CHOCH/BOS after SFP/POI{opposite_text}"

    if reason == 'no_sfp_or_poi_anchor':
        return f"waiting — {waiting_for or 'trigger anchor'}"

    if waiting_for:
        return f"waiting — {waiting_for}"

    return f"rejected — {reason or 'unknown'}"


def _format_confirmed_trigger_debug_suffix(debug):
    if not debug:
        return ""
    candidate_count = int(debug.get('candidate_bos_count') or 0) + int(debug.get('candidate_choch_count') or 0)
    final_reason = debug.get('final_reason')
    if candidate_count <= 0:
        if final_reason:
            return f" | {_humanize_confirmed_debug_reason(final_reason)}"
        return " | no confirmed BOS after early trigger"
    if not final_reason:
        return f" | candidates {candidate_count}"
    return f" | candidates {candidate_count} rejected: {_humanize_confirmed_debug_reason(final_reason)}"


def _humanize_confirmed_debug_reason(reason):
    mapping = {
        'quality_below_min': 'quality below min',
        'before_early_trigger': 'before early trigger',
        'direction_conflict': 'direction conflict',
        'candidate_scope_mismatch': 'candidate scope mismatch',
        'outside_confirmation_window': 'outside window',
        'confirmed_bos_found': 'confirmed BOS found',
        'no_confirmed_bos_after_early_trigger': 'no confirmed BOS after early trigger',
        'not_enough_candles_after_early_trigger': 'not enough candles after early trigger',
        'no_break_level_available': 'no break level after early trigger',
        'no_confirmed_break_level_after_early_trigger': 'no break level after early trigger',
        'no_candle_closed_beyond_break_level': 'no candle closed beyond break level',
        'opposite_structure_invalidated_candidate': 'opposite structure invalidated candidate',
    }
    return mapping.get(reason, reason or 'unknown')


def _format_scenario_scan(scenario_output):
    snapshot = _scenario_scan_snapshot(scenario_output)
    if not snapshot:
        return '0'
    selected = snapshot.get('selected_scenario')
    reason = snapshot.get('reason')
    candidate_suffix = _format_candidate_count_suffix(snapshot)
    if not selected:
        return f"no valid scenario — {_humanize_scenario_reason(reason)}{candidate_suffix}"

    status = selected.get('status')
    direction = selected.get('direction')
    completed = selected.get('completed_steps', 0)
    total = selected.get('total_steps', 10)
    selected_label = _format_selected_candidate_label(selected, snapshot)
    if status == 'complete':
        return f"complete {direction} scenario | {completed}/{total} steps | A+ allowed{candidate_suffix}"
    if status == 'invalidated':
        prefix = f"{selected_label} — " if selected_label else ""
        return f"{prefix}invalidated — {_humanize_scenario_reason(selected.get('invalidated_reason') or reason)}{candidate_suffix}"
    waiting_for = _humanize_scenario_waiting(selected.get('waiting_for') or selected.get('next_expected_step') or reason)
    prefix = f"{selected_label} — " if selected_label else ""
    return f"{prefix}waiting for {waiting_for} | {completed}/{total} steps{candidate_suffix}"


def _format_selected_candidate_label(selected, snapshot):
    if not selected.get('candidate_id') and not snapshot.get('candidate_counts'):
        return None
    direction = selected.get('direction') or snapshot.get('selected_direction') or 'scenario'
    ordinal = _candidate_ordinal(selected)
    if ordinal is None:
        return f"selected {direction}"
    return f"selected {direction} #{ordinal}"


def _candidate_ordinal(selected):
    candidate_id = str(selected.get('candidate_id') or '')
    tail = candidate_id.rsplit('_', 1)[-1]
    if tail.isdigit():
        return int(tail)
    rank = selected.get('rank')
    return rank if isinstance(rank, int) else None


def _format_candidate_count_suffix(snapshot):
    counts = snapshot.get('candidate_counts') or {}
    long_total = counts.get('long_total')
    short_total = counts.get('short_total')
    if long_total is None or short_total is None:
        return ''
    return f" | candidates {long_total}L/{short_total}S"


def _humanize_scenario_reason(reason):
    mapping = {
        'htf_neutral_no_scenario': 'HTF neutral',
        'htf_direction_conflict': 'HTF direction conflict',
        'pd_invalid_for_direction': 'premium/discount direction conflict',
        'waiting_for_liquidity_sweep': 'liquidity sweep / SFP',
        'waiting_for_bullish_choch_or_bos': 'bullish CHOCH/BOS after SFP',
        'waiting_for_bearish_choch_or_bos': 'bearish CHOCH/BOS after SFP',
        'waiting_for_confirmed_bullish_bos': 'confirmed bullish BOS',
        'waiting_for_confirmed_bearish_bos': 'confirmed bearish BOS',
        'waiting_for_bullish_fvg_after_confirmed_bos': 'bullish FVG',
        'waiting_for_bearish_fvg_after_confirmed_bos': 'bearish FVG',
        'waiting_for_bullish_bos': 'bullish BOS',
        'waiting_for_bearish_bos': 'bearish BOS',
        'valid_risk_plan': 'valid risk plan',
    }
    return mapping.get(reason, reason or 'unknown')


def _humanize_scenario_waiting(waiting_for):
    mapping = {
        'waiting_for_poi': 'POI touch',
        'waiting_for_liquidity_sweep': 'liquidity sweep / SFP',
        'confirmed bullish BOS after early CHOCH': 'confirmed bullish BOS',
        'confirmed bearish BOS after early CHOCH': 'confirmed bearish BOS',
        'bullish FVG after confirmed BOS': 'bullish FVG',
        'bearish FVG after confirmed BOS': 'bearish FVG',
        'SFP_CONFIRMED': 'liquidity sweep / SFP',
        'CHOCH_CONFIRMED': 'CHOCH/BOS after SFP',
        'BOS_CONFIRMED': 'BOS',
        'EARLY_TRIGGER_CONFIRMED': 'CHoCH/BOS after SFP',
        'CONFIRMED_TRIGGER_CONFIRMED': 'confirmed BOS',
        'FVG_CREATED': 'valid FVG',
        'FVG_RETESTED': 'FVG retest',
        'DISPLACEMENT_CONFIRMED': 'displacement',
        'RISK_VALID': 'valid risk plan',
    }
    return mapping.get(waiting_for, _humanize_scenario_reason(waiting_for))


def _scenario_event(event_type, direction, index, quality_score=None, source=None, payload=None):
    return ScenarioEvent(
        event_type=event_type,
        direction=direction,
        index=index,
        quality_score=quality_score,
        source=source,
        payload=payload,
    )


def _structure_event_id(event):
    if not event:
        return None
    return event.get('event_id') or event.get('id') or f"{str(event.get('type') or 'structure').upper()}:{event.get('index')}"


def _fvg_pipeline_payload(fvg, selected_trigger, selected_fvg_test_data, fvg_direction, selected_direction, *, test_index=None, displacement_index=None):
    payload = payload_to_dict(fvg)
    payload.setdefault('created_index', payload.get('end_index'))
    payload.setdefault('retested_index', test_index)
    payload.setdefault('invalidated_index', payload.get('invalidated_index'))
    if fvg_direction == selected_direction and selected_trigger:
        payload.setdefault('source_candidate_id', selected_trigger.get('candidate_id'))
        payload.setdefault('source_confirmed_trigger_id', _structure_event_id(selected_trigger))
        payload.setdefault('source_confirmed_trigger_index', selected_trigger.get('index'))
    elif fvg_direction == selected_direction and selected_fvg_test_data:
        payload.setdefault('source_candidate_id', selected_fvg_test_data.get('source_candidate_id'))
        payload.setdefault('source_confirmed_trigger_id', selected_fvg_test_data.get('source_confirmed_trigger_id'))
        payload.setdefault('source_confirmed_trigger_index', selected_fvg_test_data.get('source_confirmed_trigger_index'))
    if displacement_index is not None:
        if test_index is not None and _event_sort_key(displacement_index) > _event_sort_key(test_index):
            payload.setdefault('displacement_stage', 'post_retest')
        else:
            payload.setdefault('displacement_stage', 'bos_displacement')
    return payload


def _build_scenario_events(
    direction,
    market_structure,
    premium_discount_data,
    sfp_data,
    trigger_scan,
    context_structure,
    fvg_data,
    selected_fvg_test_data,
    risk_plan,
    last_closed_15m,
):
    events = []
    htf_trend = market_structure.get('trend') if market_structure else None
    if htf_trend:
        events.append(_scenario_event(
            'HTF_CONTEXT_CONFIRMED',
            htf_trend,
            -2,
            quality_score=market_structure.get('confidence') if market_structure else None,
            source='htf_structure',
            payload=market_structure.to_dict() if hasattr(market_structure, 'to_dict') else market_structure,
        ))

    if premium_discount_data:
        pd_payload = premium_discount_data.to_dict() if hasattr(premium_discount_data, 'to_dict') else premium_discount_data
        pd_index = _poi_event_index(selected_fvg_test_data, last_closed_15m)
        pd_payload = payload_to_dict(pd_payload)
        pd_payload.setdefault('source', 'premium_discount')
        pd_payload.setdefault('pd_location_id', _pd_location_id(pd_payload))
        pd_payload.setdefault('zone_depth_initial', pd_payload.get('zone_depth'))
        pd_payload['zone_depth_current'] = pd_payload.get('zone_depth')
        pd_payload['zone_strength_current'] = pd_payload.get('zone_strength')
        if pd_index is not None and premium_discount_data.get('valid_for_buy', False):
            events.append(_scenario_event('PD_LOCATION_VALID', 'bullish', pd_index, premium_discount_data.get('zone_strength'), 'premium_discount', pd_payload))
        if pd_index is not None and premium_discount_data.get('valid_for_sell', False):
            events.append(_scenario_event('PD_LOCATION_VALID', 'bearish', pd_index, premium_discount_data.get('zone_strength'), 'premium_discount', pd_payload))

    if sfp_data:
        sfp_direction = _event_direction(sfp_data)
        events.append(_scenario_event(
            'SFP_CONFIRMED',
            sfp_direction,
            sfp_data.get('index'),
            sfp_data.get('quality_score'),
            'sfp',
            sfp_data,
        ))

    trigger_data = trigger_scan.to_dict() if hasattr(trigger_scan, 'to_dict') else dict(trigger_scan or {})
    trigger_sfp_index = trigger_data.get('sfp_index')
    state_direction = _direction_to_state_direction(direction)
    if state_direction and trigger_sfp_index is not None and (
        trigger_data.get('early_trigger') or trigger_data.get('confirmed_trigger') or trigger_data.get('selected_trigger')
    ):
        trigger_anchor_payload = {
            'type': f'{state_direction}_sfp',
            'index': trigger_sfp_index,
            'quality_score': (sfp_data or {}).get('quality_score'),
            'source_sfp': sfp_data,
            'source': 'trigger_scan',
            'reason': 'scenario-scoped SFP anchor from trigger scan chain',
        }
        events.append(_scenario_event(
            'SFP_CONFIRMED',
            state_direction,
            trigger_sfp_index,
            (sfp_data or {}).get('quality_score'),
            'trigger_scan',
            trigger_anchor_payload,
        ))

    early_trigger = trigger_data.get('early_trigger')
    if early_trigger:
        early_trigger_payload = dict(early_trigger)
        if trigger_data.get('confirmed_trigger_debug') is not None:
            early_trigger_payload['confirmed_trigger_debug'] = trigger_data.get('confirmed_trigger_debug')
        events.append(_scenario_event(
            'EARLY_TRIGGER_CONFIRMED',
            _event_direction(early_trigger),
            early_trigger.get('index'),
            early_trigger.get('quality_score'),
            'trigger_scan',
            early_trigger_payload,
        ))
    selected_trigger = trigger_data.get('selected_trigger')
    for structure in (context_structure, selected_trigger):
        if not structure:
            continue
        structure_direction = _event_direction(structure)
        structure_type = str(structure.get('type', ''))
        if 'choch' in structure_type:
            events.append(_scenario_event('CONFIRMED_TRIGGER_CONFIRMED', structure_direction, structure.get('index'), structure.get('quality_score'), 'structure', structure))
        elif 'bos' in structure_type:
            events.append(_scenario_event('CONFIRMED_TRIGGER_CONFIRMED', structure_direction, structure.get('index'), structure.get('quality_score'), 'structure', structure))

    for fvg in _latest_fvgs_by_type(fvg_data):
        fvg_direction = 'bullish' if fvg.get('type') == 'bullish' else 'bearish' if fvg.get('type') == 'bearish' else None
        created_index = fvg.get('end_index')
        selected_direction = _direction_to_state_direction(direction)
        if created_index is not None:
            events.append(_scenario_event(
                'FVG_CREATED',
                fvg_direction,
                created_index,
                fvg.get('quality_score'),
                'fvg',
                _fvg_pipeline_payload(fvg, selected_trigger, selected_fvg_test_data, fvg_direction, selected_direction),
            ))
        test_index = fvg.get('test_index')
        if test_index is None and selected_fvg_test_data and fvg_direction == selected_direction:
            test_index = selected_fvg_test_data.get('index')
        if test_index is not None and (fvg.get('tested', False) or selected_fvg_test_data):
            events.append(_scenario_event(
                'FVG_RETESTED',
                fvg_direction,
                test_index,
                fvg.get('quality_score'),
                'fvg',
                _fvg_pipeline_payload(fvg, selected_trigger, selected_fvg_test_data, fvg_direction, selected_direction, test_index=test_index),
            ))
        displacement_index = fvg.get('displacement_index') or ((selected_fvg_test_data or {}).get('displacement_index') if fvg_direction == selected_direction else None)
        if displacement_index is not None:
            events.append(_scenario_event(
                'DISPLACEMENT_CONFIRMED',
                fvg_direction,
                displacement_index,
                fvg.get('quality_score'),
                'fvg',
                _fvg_pipeline_payload(
                    fvg,
                    selected_trigger,
                    selected_fvg_test_data,
                    fvg_direction,
                    selected_direction,
                    test_index=test_index,
                    displacement_index=displacement_index,
                ),
            ))

    if risk_plan:
        risk_direction = 'bullish' if risk_plan.direction == 'LONG' else 'bearish'
        risk_index = last_closed_15m.name if last_closed_15m is not None else None
        risk_event_type = 'RISK_VALID' if risk_plan.valid else 'RISK_INVALID'
        events.append(_scenario_event(
            risk_event_type,
            risk_direction,
            risk_index,
            None,
            'risk_plan',
            risk_plan.to_dict() if hasattr(risk_plan, 'to_dict') else risk_plan,
        ))
    return events


def _candidate_risk_inputs(selected_scenario, fvg_data=None, selected_fvg_test_data=None, direction=None):
    if selected_scenario is None:
        return [], None, False
    fvg_event = _candidate_event(selected_scenario, "FVG_CREATED")
    retest_event = _candidate_event(selected_scenario, "FVG_RETESTED")
    displacement_event = _candidate_event(selected_scenario, "DISPLACEMENT_CONFIRMED")
    candidate_id = getattr(selected_scenario, "candidate_id", None)
    if fvg_event is None:
        inferred_fvg = _infer_candidate_fvg(selected_scenario, fvg_data, direction)
        if inferred_fvg is None:
            return [], None, False
        inferred_fvg["source_candidate_id"] = candidate_id
        inferred_fvg["candidate_id"] = candidate_id
        inferred_fvg["tested"] = False
        return [inferred_fvg], None, False
    fvg_payload = payload_to_dict(getattr(fvg_event, "payload", None))
    fvg_payload["source_candidate_id"] = candidate_id
    fvg_payload["candidate_id"] = candidate_id
    fvg_payload["tested"] = retest_event is not None
    fvg_test_payload = None
    if retest_event is not None:
        fvg_test_payload = payload_to_dict(getattr(retest_event, "payload", None))
        fvg_test_payload["source_candidate_id"] = candidate_id
        fvg_test_payload.setdefault("index", getattr(retest_event, "index", None))
    elif selected_fvg_test_data and _event_sort_key(selected_fvg_test_data.get("index")) > _event_sort_key(fvg_payload.get("created_index") or fvg_payload.get("end_index")):
        fvg_test_payload = payload_to_dict(selected_fvg_test_data)
        fvg_test_payload["source_candidate_id"] = candidate_id
        fvg_payload["tested"] = True
    return [fvg_payload], fvg_test_payload, displacement_event is not None


def _build_candidate_risk_plan(
    *,
    selected_scenario,
    direction,
    current_price,
    atr,
    liquidity_map,
    fvg_data,
    selected_fvg_test_data,
    sfp_data,
    structure_data,
):
    if direction not in ('LONG', 'SHORT') or selected_scenario is None:
        return None
    selected_candidate_id = getattr(selected_scenario, 'candidate_id', None)
    candidate_fvg_data, candidate_fvg_test_data, candidate_displacement_confirmed = _candidate_risk_inputs(
        selected_scenario,
        fvg_data,
        selected_fvg_test_data,
        direction,
    )
    candidate_structure_data = _candidate_confirmed_trigger(selected_scenario) or structure_data
    candidate_sfp_data = _candidate_sfp(selected_scenario) or sfp_data
    risk_plan = build_risk_plan(
        direction=direction,
        current_price=current_price,
        atr=atr,
        liquidity_map=liquidity_map,
        fvg_data=candidate_fvg_data,
        fvg_test_data=candidate_fvg_test_data,
        sfp_data=candidate_sfp_data,
        structure_data=candidate_structure_data,
        source_candidate_id=selected_candidate_id,
        candidate_fvg_created=bool(candidate_fvg_data),
        candidate_fvg_retested=bool(candidate_fvg_test_data),
        post_retest_displacement_confirmed=bool(candidate_displacement_confirmed),
    )
    return _ensure_risk_source_candidate(risk_plan, selected_candidate_id)


def _not_available_risk_plan(direction, reason, source_candidate_id=None):
    if direction not in ('LONG', 'SHORT'):
        return None
    return RiskPlan(
        direction=direction,
        entry=None,
        stop_loss=None,
        invalidation_level=None,
        target_1=None,
        target_2=None,
        risk_per_unit=None,
        rr_to_target_1=None,
        rr_to_target_2=None,
        stop_distance_percent=None,
        entry_distance_from_poi_atr=None,
        valid=False,
        reason=reason,
        entry_model="entry_not_available",
        stop_model="none",
        target_model="none",
        risk_plan_status="not_available",
        source_candidate_id=source_candidate_id,
        risk_geometry="not_available",
    )


def _risk_plan_for_selected_candidate(risk_plan, selected_scenario, direction):
    selected_candidate_id = getattr(selected_scenario, 'candidate_id', None) if selected_scenario else None
    if risk_plan is None or not selected_candidate_id:
        return risk_plan
    source_candidate_id = risk_plan.get("source_candidate_id") if isinstance(risk_plan, dict) else getattr(risk_plan, "source_candidate_id", None)
    if source_candidate_id is None:
        return _ensure_risk_source_candidate(risk_plan, selected_candidate_id)
    if str(source_candidate_id) != str(selected_candidate_id):
        logger.warning(
            "Risk plan provenance mismatch: selected_candidate_id=%s risk_source_candidate_id=%s",
            selected_candidate_id,
            source_candidate_id,
        )
        return _not_available_risk_plan(direction, "candidate_provenance_mismatch", selected_candidate_id)
    return risk_plan


def _infer_candidate_fvg(selected_scenario, fvg_data, direction):
    confirmed = _candidate_confirmed_trigger(selected_scenario)
    if not confirmed:
        return None
    selected_direction = _direction_to_state_direction(direction)
    confirmed_index = confirmed.get("index")
    candidates = []
    for fvg in fvg_data or []:
        payload = payload_to_dict(fvg)
        if payload.get("type") != selected_direction:
            continue
        created_index = payload.get("end_index") or payload.get("created_index")
        if created_index is None or _event_sort_key(created_index) <= _event_sort_key(confirmed_index):
            continue
        if payload.get("invalidated"):
            continue
        payload.setdefault("created_index", created_index)
        candidates.append(payload)
    if not candidates:
        return None
    return max(candidates, key=lambda item: _event_sort_key(item.get("created_index") or item.get("end_index")))


def _ensure_risk_source_candidate(risk_plan, candidate_id):
    if risk_plan is None or not candidate_id:
        return risk_plan
    if isinstance(risk_plan, dict):
        risk_plan.setdefault("source_candidate_id", candidate_id)
        return risk_plan
    if getattr(risk_plan, "source_candidate_id", None) is None:
        object.__setattr__(risk_plan, "source_candidate_id", candidate_id)
    return risk_plan


def _candidate_confirmed_trigger(selected_scenario):
    return _scenario_event_payload_by_type(selected_scenario, "CONFIRMED_TRIGGER_CONFIRMED")


def _candidate_sfp(selected_scenario):
    return _scenario_event_payload_by_type(selected_scenario, "SFP_CONFIRMED")


def _candidate_event(selected_scenario, event_type):
    wanted = str(event_type or "").upper()
    aliases = {
        "SFP_CONFIRMED": {"SFP_CONFIRMED", "LIQUIDITY_SWEEP_CONFIRMED"},
        "CONFIRMED_TRIGGER_CONFIRMED": {"CONFIRMED_TRIGGER_CONFIRMED", "CHOCH_CONFIRMED", "BOS_CONFIRMED"},
    }.get(wanted, {wanted})
    for event in getattr(selected_scenario, "events_used", []) or []:
        if str(getattr(event, "event_type", "") or "").upper() in aliases:
            return event
    return None


def _poi_event_index(selected_fvg_test_data, last_closed_15m):
    if selected_fvg_test_data and selected_fvg_test_data.get('index') is not None:
        return selected_fvg_test_data.get('index')
    if last_closed_15m is not None and getattr(last_closed_15m, 'name', None) is not None:
        return last_closed_15m.name
    return None


def _scan_interval_minutes(df):
    if df is None or len(df.index) < 2:
        return None
    try:
        delta = pd.Timestamp(df.index[-1]) - pd.Timestamp(df.index[-2])
        return round(delta.total_seconds() / 60.0, 4)
    except Exception:
        return None


def _build_htf_context_explain(
    *,
    market_structure,
    trend_data,
    df_4h_closed,
    df_1h_closed,
    swing_highs_4h,
    swing_lows_4h,
    recent_structure_events,
    market_data_timestamps,
    analysis_time=None,
):
    config = MarketStructureConfig()
    recent_highs = swing_highs_4h.sort_index().tail(config.swing_lookback) if swing_highs_4h is not None else None
    recent_lows = swing_lows_4h.sort_index().tail(config.swing_lookback) if swing_lows_4h is not None else None
    sequence_points = _htf_swing_sequence(recent_highs, recent_lows)
    sequence_labels = [point["label"] for point in sequence_points if point.get("label") not in ("H", "L")]

    high_diff = recent_highs['high'].astype(float).diff().dropna() if recent_highs is not None and not recent_highs.empty and 'high' in recent_highs else pd.Series(dtype=float)
    low_diff = recent_lows['low'].astype(float).diff().dropna() if recent_lows is not None and not recent_lows.empty and 'low' in recent_lows else pd.Series(dtype=float)
    latest_high_change = float(high_diff.iloc[-1]) if not high_diff.empty else 0.0
    latest_low_change = float(low_diff.iloc[-1]) if not low_diff.empty else 0.0
    latest_hh = latest_high_change > 0
    latest_lh = latest_high_change < 0
    latest_hl = latest_low_change > 0
    latest_ll = latest_low_change < 0

    adx = float(trend_data.get('adx_value')) if trend_data and trend_data.get('adx_value') is not None else None
    reason = getattr(market_structure, "reason", None)
    reason_flags = []
    if adx is not None and adx < config.adx_neutral_threshold:
        reason_flags.append("adx_below_threshold")
    if reason == "Conflicting swing structure" or (latest_hh and latest_ll):
        reason_flags.append("conflicting_swings")
    if reason == "Compressed swing structure" or (latest_lh and latest_hl and not (latest_hh or latest_ll)):
        reason_flags.append("compressed_swings")
    if reason == "No confirmed swing structure":
        reason_flags.append("insufficient_confirmed_swings")
    if reason == "Range too narrow":
        reason_flags.append("range_too_narrow")
    if reason == "Conflicting recent BOS":
        reason_flags.append("conflicting_recent_bos")
    if reason == "No confirmed directional structure":
        reason_flags.append("no_confirmed_directional_structure")

    last_break = _latest_structure_event(recent_structure_events)
    protected_high = _last_swing_value(recent_highs, "high")
    protected_low = _last_swing_value(recent_lows, "low")
    bull_score = int(latest_hh) + int(latest_hl) + int(bool(trend_data and trend_data.get('is_bullish') is True))
    bear_score = int(latest_lh) + int(latest_ll) + int(bool(trend_data and trend_data.get('is_bullish') is False))
    neutral_score = len(reason_flags)

    last_closed_4h = (market_data_timestamps or {}).get("4h")
    market_data_age_seconds = _age_seconds(last_closed_4h, analysis_time=analysis_time)

    return {
        "direction": getattr(market_structure, "trend", None),
        "reason": reason,
        "adx": adx,
        "adx_threshold": config.adx_neutral_threshold,
        "last_closed_1h": (market_data_timestamps or {}).get("1h"),
        "last_closed_4h": last_closed_4h,
        "market_data_age_seconds": market_data_age_seconds,
        "protected_high": protected_high,
        "protected_low": protected_low,
        "last_break_type": last_break.get("break_type"),
        "last_break_direction": last_break.get("direction"),
        "last_break_index": last_break.get("index"),
        "swing_sequence": sequence_labels,
        "swing_points": sequence_points,
        "bull_score": bull_score,
        "bear_score": bear_score,
        "neutral_score": neutral_score,
        "conflicting_structure": bool("conflicting_swings" in reason_flags or "conflicting_recent_bos" in reason_flags),
        "reason_flags": reason_flags,
    }


def _htf_swing_sequence(swing_highs, swing_lows):
    points = []
    previous_high = None
    if swing_highs is not None:
        for idx, row in swing_highs.sort_index().iterrows():
            price = _safe_float(row.get("high"))
            if previous_high is None:
                label = "H"
            elif price is not None and previous_high is not None and price > previous_high:
                label = "HH"
            elif price is not None and previous_high is not None and price < previous_high:
                label = "LH"
            else:
                label = "EH"
            points.append({"kind": "high", "label": label, "price": price, "index": str(idx)})
            previous_high = price
    previous_low = None
    if swing_lows is not None:
        for idx, row in swing_lows.sort_index().iterrows():
            price = _safe_float(row.get("low"))
            if previous_low is None:
                label = "L"
            elif price is not None and previous_low is not None and price > previous_low:
                label = "HL"
            elif price is not None and previous_low is not None and price < previous_low:
                label = "LL"
            else:
                label = "EL"
            points.append({"kind": "low", "label": label, "price": price, "index": str(idx)})
            previous_low = price
    return sorted(points, key=lambda item: item["index"])


def _safe_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _last_swing_value(swings, column):
    if swings is None or swings.empty or column not in swings:
        return None
    return _safe_float(swings.sort_index().iloc[-1].get(column))


def _latest_structure_event(events):
    if not events:
        return {}
    event = events[-1]
    event_type = str(event.get("type") or "")
    direction = "bullish" if "bullish" in event_type else "bearish" if "bearish" in event_type else None
    break_type = "BOS" if "bos" in event_type.lower() else "CHOCH" if "choch" in event_type.lower() else None
    return {"break_type": break_type, "direction": direction, "index": str(event.get("index")) if event.get("index") is not None else None}


def _opposite_state_direction(direction):
    if direction == "bullish":
        return "bearish"
    if direction == "bearish":
        return "bullish"
    return None


def _shadow_target_level(direction, liquidity_map):
    if direction == "bullish":
        return _level_value(_level_value(liquidity_map, "nearest_buy_side"), "price")
    if direction == "bearish":
        return _level_value(_level_value(liquidity_map, "nearest_sell_side"), "price")
    return None


def _shadow_invalidation_level(direction, liquidity_map, current_price, atr, sfp_data=None):
    sfp_level = _safe_float((sfp_data or {}).get("level"))
    if sfp_level is not None:
        return sfp_level
    if direction == "bullish":
        return (
            _safe_float(_level_value(_level_value(liquidity_map, "nearest_sell_side"), "price"))
            or (float(current_price) - (2 * float(atr or 0.0)))
        )
    if direction == "bearish":
        return (
            _safe_float(_level_value(_level_value(liquidity_map, "nearest_buy_side"), "price"))
            or (float(current_price) + (2 * float(atr or 0.0)))
        )
    return None


def _shadow_rr(direction, entry, stop, target):
    entry = _safe_float(entry)
    stop = _safe_float(stop)
    target = _safe_float(target)
    if entry is None or stop is None or target is None:
        return None
    risk = abs(entry - stop)
    if risk <= 0:
        return None
    reward = (target - entry) if direction == "bullish" else (entry - target)
    if reward <= 0:
        return None
    return round(reward / risk, 4)


def _shadow_candidate_id(symbol, direction, anchor_type, anchor_index, entry, stop, target):
    payload = "|".join(
        str(item)
        for item in (
            symbol,
            direction,
            anchor_type,
            anchor_index,
            round(float(entry), 8) if entry is not None else None,
            round(float(stop), 8) if stop is not None else None,
            round(float(target), 8) if target is not None else None,
        )
    )
    digest = hashlib.sha1(payload.encode("utf-8")).hexdigest()[:12]
    return f"SHADOW_{str(symbol).upper()}_{str(direction).upper()}_{digest}"


def _shadow_event_for_direction(direction, context_break_1h, long_trigger_candidate, short_trigger_candidate, trigger_break_15m):
    directional_context = context_break_1h if _event_direction(context_break_1h) == direction else None
    directional_trigger = _candidate_for_direction(direction, long_trigger_candidate, short_trigger_candidate)
    if not directional_trigger and _event_direction(trigger_break_15m) == direction:
        directional_trigger = trigger_break_15m
    return directional_context or directional_trigger, directional_context, directional_trigger


def _build_shadow_candidate(
    *,
    symbol,
    htf_context,
    market_structure,
    context_break_1h,
    trigger_break_15m,
    long_trigger_candidate,
    short_trigger_candidate,
    sfp_data,
    premium_discount_data,
    liquidity_map,
    current_price,
    atr,
    risk_plan=None,
    scenario_scan=None,
    created_at=None,
):
    current_price = _safe_float(current_price)
    atr = _safe_float(atr) or 0.0
    if current_price is None:
        return None

    direction_scores = []
    for direction in ("bullish", "bearish"):
        anchor, context_event, trigger_event = _shadow_event_for_direction(
            direction,
            context_break_1h,
            long_trigger_candidate,
            short_trigger_candidate,
            trigger_break_15m,
        )
        if not anchor:
            continue
        quality = float(anchor.get("quality_score") or 0.0)
        context_bonus = 10 if context_event else 0
        trigger_bonus = 5 if trigger_event else 0
        direction_scores.append((quality + context_bonus + trigger_bonus, direction, anchor, context_event, trigger_event))

    if not direction_scores:
        return None

    _, direction, anchor, context_event, trigger_event = max(direction_scores, key=lambda item: (item[0], _event_sort_key(item[2].get("index"))))
    target = _safe_float(_shadow_target_level(direction, liquidity_map))
    stop = _safe_float(_shadow_invalidation_level(direction, liquidity_map, current_price, atr, sfp_data=sfp_data))
    entry = _safe_float((trigger_event or anchor).get("level")) or current_price
    rr = _shadow_rr(direction, entry, stop, target)
    htf_direction = (htf_context or {}).get("direction") or (market_structure.get("trend") if market_structure else None)
    htf_supportive = htf_direction == direction
    has_poi = bool(premium_discount_data)
    has_sweep = bool(sfp_data)
    has_trigger = bool(trigger_event)
    preliminary_risk_valid = rr is not None and rr >= RiskPlanConfig().min_rr_for_watchlist
    confirmed_trigger = bool(trigger_event and float(trigger_event.get("quality_score") or 0.0) >= MIN_TRIGGER_QUALITY)
    tier = "C"
    rejection_reasons = []
    if target is None:
        rejection_reasons.append("target_not_available")
    if stop is None:
        rejection_reasons.append("invalidation_not_available")
    if rr is None:
        rejection_reasons.append("risk_reward_not_available")
    if not has_sweep and not has_poi:
        rejection_reasons.append("no_poi_or_sweep")
    if not has_trigger:
        rejection_reasons.append("no_trigger_candidate")
    if not preliminary_risk_valid:
        rejection_reasons.append("preliminary_risk_invalid")
    if (has_sweep or has_poi) and has_trigger and preliminary_risk_valid:
        tier = "B"
    if not htf_supportive:
        rejection_reasons.append("htf_not_directionally_supportive")
    if not confirmed_trigger:
        rejection_reasons.append("confirmed_trigger_missing")
    if tier == "B" and htf_supportive and confirmed_trigger and rr is not None and rr >= RiskPlanConfig().min_rr_for_a_plus:
        tier = "A"

    anchor_type = str(anchor.get("type") or "structure")
    anchor_index = anchor.get("index")
    created_at = created_at or anchor_index
    shadow_id = _shadow_candidate_id(symbol, direction, anchor_type, anchor_index, entry, stop, target)
    return {
        "shadow_tier": tier,
        "shadow_candidate_id": shadow_id,
        "shadow_direction": "LONG" if direction == "bullish" else "SHORT",
        "shadow_created_at": str(created_at) if created_at is not None else None,
        "shadow_rejection_reasons": sorted(set(rejection_reasons)),
        "htf_context_class": "directional" if htf_direction in ("bullish", "bearish") else "neutral",
        "entry": entry,
        "stop_loss": stop,
        "target_1": target,
        "risk_per_unit": round(abs(entry - stop), 8) if entry is not None and stop is not None else None,
        "rr_to_target_1": rr,
        "anchor_type": anchor_type,
        "anchor_index": str(anchor_index) if anchor_index is not None else None,
        "context_1h": _event_snapshot(context_event),
        "trigger_15m": _event_snapshot(trigger_event),
        "sfp": _event_snapshot(sfp_data),
    }


def _empty_shadow_outcome():
    return {
        "entry_filled": None,
        "entry_filled_at": None,
        "max_favorable_excursion_r": None,
        "max_adverse_excursion_r": None,
        "reached_1r": None,
        "reached_2r": None,
        "target_1_hit": None,
        "stop_hit": None,
        "expired": None,
        "outcome_at": None,
    }


def _component_result(
    *,
    detected=False,
    direction=None,
    timestamp=None,
    quality_score=None,
    configured_threshold=None,
    passed=None,
    rejection_reason=None,
    condition_value=None,
    condition_threshold=None,
):
    if passed is None:
        if configured_threshold is not None and quality_score is not None:
            passed = float(quality_score or 0.0) >= float(configured_threshold)
        else:
            passed = bool(detected)
    return {
        "detected": bool(detected),
        "direction": direction,
        "timestamp": str(timestamp) if timestamp is not None else None,
        "quality_score": quality_score,
        "configured_threshold": configured_threshold,
        "passed": bool(passed),
        "rejection_reason": rejection_reason,
        "condition_value": condition_value if condition_value is not None else quality_score,
        "condition_threshold": condition_threshold if condition_threshold is not None else configured_threshold,
    }


def _component_from_event(event, threshold=None, rejection_reason=None):
    payload = payload_to_dict(event)
    return _component_result(
        detected=bool(payload),
        direction=_event_direction(payload),
        timestamp=payload.get("index") or payload.get("event_time"),
        quality_score=payload.get("quality_score"),
        configured_threshold=threshold,
        rejection_reason=rejection_reason,
    )


def _scenario_event_payload_by_type(selected_scenario, wanted):
    event = _candidate_event(selected_scenario, wanted)
    if event is None:
        return None
    payload = payload_to_dict(getattr(event, "payload", None))
    payload.setdefault("index", getattr(event, "index", None))
    payload.setdefault("quality_score", getattr(event, "quality_score", None))
    payload.setdefault("direction", _direction_to_state_direction(getattr(event, "direction", None)))
    return payload


def _latest_checked_candle_metric(confirmed_debug, key):
    values = [
        _safe_float(candle.get(key))
        for candle in (confirmed_debug or {}).get("checked_candles") or []
        if _safe_float(candle.get(key)) is not None
    ]
    return max(values) if values else None


def _best_confirmed_candidate_debug(confirmed_debug):
    candidates = list((confirmed_debug or {}).get("rejected_candidates") or [])
    if not candidates:
        return None
    return max(candidates, key=lambda item: float(item.get("quality_score") or 0.0))


def _close_distance_from_break_atr(confirmed_debug):
    break_level = _safe_float((confirmed_debug or {}).get("break_level"))
    if break_level is None:
        return None
    best = None
    for candle in (confirmed_debug or {}).get("checked_candles") or []:
        close = _safe_float(candle.get("close"))
        if close is None:
            continue
        distance = abs(break_level - close)
        if best is None or distance < best:
            best = distance
    return best


def _fvg_distance_from_current_atr(fvg_data, current_price, atr):
    current_price = _safe_float(current_price)
    atr = _safe_float(atr)
    if current_price is None or not atr or atr <= 0:
        return None
    distances = []
    for fvg in fvg_data or []:
        top = _safe_float(fvg.get("top"))
        bottom = _safe_float(fvg.get("bottom"))
        if top is None or bottom is None:
            continue
        if bottom <= current_price <= top:
            distances.append(0.0)
        else:
            distances.append(min(abs(current_price - top), abs(current_price - bottom)) / atr)
    return round(min(distances), 4) if distances else None


def _retest_progress_toward_fvg(fvg_data, current_price):
    current_price = _safe_float(current_price)
    if current_price is None:
        return None
    candidates = []
    for fvg in fvg_data or []:
        top = _safe_float(fvg.get("top"))
        bottom = _safe_float(fvg.get("bottom"))
        if top is None or bottom is None or top == bottom:
            continue
        if bottom <= current_price <= top:
            candidates.append(1.0)
        else:
            width = abs(top - bottom)
            distance = min(abs(current_price - top), abs(current_price - bottom))
            candidates.append(max(0.0, min(1.0, 1.0 - (distance / width))))
    return round(max(candidates), 4) if candidates else None


def _bars_since_index(index, last_closed_15m, scan_interval_minutes):
    if index is None or last_closed_15m is None or getattr(last_closed_15m, "name", None) is None:
        return None
    try:
        minutes = (pd.Timestamp(last_closed_15m.name) - pd.Timestamp(index)).total_seconds() / 60.0
        interval = float(scan_interval_minutes or 15.0)
        return max(0, int(minutes // interval))
    except Exception:
        return None


def _near_miss_from_components(components):
    candidates = []
    for name, component in (components or {}).items():
        value = _safe_float(component.get("condition_value"))
        threshold = _safe_float(component.get("condition_threshold"))
        if value is None or threshold is None or threshold <= 0:
            continue
        ratio = value / threshold
        if ratio < 1.0:
            candidates.append((ratio, name, value, threshold))
    if not candidates:
        return None, None, None, None
    ratio, name, value, threshold = max(candidates, key=lambda item: item[0])
    return name, round(value, 4), round(threshold, 4), round(ratio, 4)


def _trigger_stage_from_required(required_next_event):
    text = str(required_next_event or "").upper()
    if "EARLY_TRIGGER" in text or "CHOCH" in text:
        return "waiting_for_early_trigger"
    if "CONFIRMED_TRIGGER" in text or "BOS" in text:
        return "waiting_for_confirmed_trigger"
    if "FVG_CREATED" in text:
        return "waiting_for_fvg_creation"
    if "FVG_RETESTED" in text:
        return "waiting_for_fvg_retest"
    if "DISPLACEMENT" in text:
        return "waiting_for_displacement"
    return "not_waiting_for_trigger"


def _build_trigger_diagnostics(score_result, analysis_data, session):
    scenario_scan = analysis_data.get("scenario_scan") if analysis_data else None
    selected = getattr(scenario_scan, "selected_scenario", None) if scenario_scan else None
    if selected is None:
        return None

    selected_snapshot = selected.to_dict() if hasattr(selected, "to_dict") else dict(selected)
    candidate_id_value = selected_snapshot.get("candidate_id")
    required_next = selected_snapshot.get("next_expected_step") or selected_snapshot.get("waiting_for")
    trigger_stage = _trigger_stage_from_required(required_next)
    trigger_scan = _candidate_scoped_trigger_scan(scenario_scan, selected_snapshot.get("direction"))
    trigger_scan_data = trigger_scan.to_dict() if hasattr(trigger_scan, "to_dict") else dict(trigger_scan or {})
    confirmed_debug = trigger_scan_data.get("confirmed_trigger_debug") or {}
    direction = selected_snapshot.get("direction")
    state_direction = _direction_to_state_direction(direction)
    opposite_direction = _opposite_state_direction(state_direction)
    event_types_used = _selected_scenario_event_types(analysis_data)

    sfp_payload = _scenario_event_payload_by_type(selected, "SFP_CONFIRMED") or analysis_data.get("sfp_data")
    early_trigger = trigger_scan_data.get("early_trigger")
    confirmed_trigger = trigger_scan_data.get("confirmed_trigger") or trigger_scan_data.get("selected_trigger")
    candidate_trigger = trigger_scan_data.get("candidate_trigger") or analysis_data.get("trigger_break_15m")
    best_rejected_confirmed = _best_confirmed_candidate_debug(confirmed_debug)
    fvg_created = "FVG_CREATED" in event_types_used
    fvg_retested = "FVG_RETESTED" in event_types_used
    displacement_confirmed = "DISPLACEMENT_CONFIRMED" in event_types_used
    last_closed = analysis_data.get("last_closed_15m")
    scan_interval = analysis_data.get("scan_interval") or 15.0

    choch_candidate = None
    bos_candidate = None
    for item in [early_trigger, confirmed_trigger, candidate_trigger, best_rejected_confirmed]:
        item_type = str((item or {}).get("type") or "").lower()
        if "choch" in item_type and choch_candidate is None:
            choch_candidate = item
        if "bos" in item_type and bos_candidate is None:
            bos_candidate = item

    if choch_candidate is None and early_trigger and "early" in str(early_trigger.get("type", "")).lower():
        choch_candidate = early_trigger

    displacement_value = (
        _safe_float((confirmed_trigger or {}).get("displacement_ratio"))
        or _safe_float((early_trigger or {}).get("displacement_ratio"))
        or _latest_checked_candle_metric(confirmed_debug, "displacement_ratio")
        or _safe_float((candidate_trigger or {}).get("displacement_ratio"))
    )
    close_position_value = (
        _safe_float((confirmed_trigger or {}).get("close_position"))
        or _safe_float((early_trigger or {}).get("close_position"))
        or _latest_checked_candle_metric(confirmed_debug, "close_position")
        or _safe_float((candidate_trigger or {}).get("close_position"))
    )
    impulse_value = (
        _safe_float((confirmed_trigger or {}).get("rvol"))
        or _safe_float((early_trigger or {}).get("rvol"))
        or _latest_checked_candle_metric(confirmed_debug, "rvol")
        or _safe_float((candidate_trigger or {}).get("rvol"))
    )
    confirmed_bos_quality = _safe_float((confirmed_trigger or {}).get("quality_score"))
    if confirmed_bos_quality is None and best_rejected_confirmed:
        confirmed_bos_quality = _safe_float(best_rejected_confirmed.get("quality_score"))

    components = {
        "SFP": _component_from_event(sfp_payload),
        "CHoCH": _component_from_event(
            choch_candidate,
            threshold=MIN_EARLY_TRIGGER_QUALITY,
            rejection_reason=trigger_scan_data.get("rejected_reason") if not early_trigger else None,
        ),
        "BOS": _component_result(
            detected=bool(confirmed_trigger or best_rejected_confirmed),
            direction=_event_direction(confirmed_trigger or best_rejected_confirmed),
            timestamp=(confirmed_trigger or best_rejected_confirmed or {}).get("index"),
            quality_score=confirmed_bos_quality,
            configured_threshold=MIN_TRIGGER_QUALITY,
            rejection_reason=(best_rejected_confirmed or {}).get("rejected_reason") or confirmed_debug.get("final_reason"),
        ),
        "displacement": _component_result(
            detected=displacement_value is not None,
            direction=state_direction,
            timestamp=(confirmed_trigger or early_trigger or candidate_trigger or {}).get("index"),
            quality_score=displacement_value,
            configured_threshold=MIN_EARLY_TRIGGER_DISPLACEMENT_ATR,
            rejection_reason="displacement_quality_below_threshold" if displacement_value is not None and displacement_value < MIN_EARLY_TRIGGER_DISPLACEMENT_ATR else None,
        ),
        "FVG creation": _component_result(detected=fvg_created, direction=state_direction, passed=fvg_created),
        "FVG retest": _component_result(detected=fvg_retested, direction=state_direction, passed=fvg_retested),
        "close beyond structure": _component_result(
            detected=any(bool(candle.get("breaks_level")) for candle in confirmed_debug.get("checked_candles") or []),
            direction=state_direction,
            condition_value=close_position_value,
            condition_threshold=BOSConfig().min_close_position,
            configured_threshold=BOSConfig().min_close_position,
            passed=any(bool(candle.get("breaks_level")) for candle in confirmed_debug.get("checked_candles") or []),
            rejection_reason=confirmed_debug.get("final_reason") if confirmed_debug else None,
        ),
        "volume/impulse confirmation": _component_result(
            detected=impulse_value is not None,
            direction=state_direction,
            quality_score=impulse_value,
            configured_threshold=MIN_EARLY_TRIGGER_RVOL,
            condition_value=impulse_value,
            condition_threshold=MIN_EARLY_TRIGGER_RVOL,
            rejection_reason="volume_impulse_below_threshold" if impulse_value is not None and impulse_value < MIN_EARLY_TRIGGER_RVOL else None,
        ),
        "session/kill-zone condition": _component_result(
            detected=session is not None,
            direction=None,
            passed=bool(getattr(session, "in_kill_zone", False)) or SEND_A_PLUS_OUTSIDE_KZ,
            rejection_reason=None if (bool(getattr(session, "in_kill_zone", False)) or SEND_A_PLUS_OUTSIDE_KZ) else "outside_kill_zone",
        ),
    }

    missing_conditions = []
    if not components["SFP"]["detected"]:
        missing_conditions.append("sfp_not_detected")
    if trigger_stage == "waiting_for_early_trigger":
        if not early_trigger:
            missing_conditions.append("early_trigger_not_detected")
        if not choch_candidate:
            missing_conditions.append("choch_not_detected")
        if not bos_candidate:
            missing_conditions.append("bos_not_detected")
        if displacement_value is not None and displacement_value < MIN_EARLY_TRIGGER_DISPLACEMENT_ATR:
            missing_conditions.append("displacement_quality_below_threshold")
    if trigger_stage == "waiting_for_confirmed_trigger":
        if not early_trigger:
            missing_conditions.append("early_trigger_not_detected")
        if not confirmed_trigger:
            missing_conditions.append("confirmation_bos_not_detected")
        if confirmed_bos_quality is not None and confirmed_bos_quality < MIN_TRIGGER_QUALITY:
            missing_conditions.append("bos_quality_below_threshold")
        if not components["close beyond structure"]["passed"]:
            missing_conditions.append("close_beyond_structure_missing")
    if not fvg_created:
        missing_conditions.append("fvg_not_created")
    if fvg_created and not fvg_retested:
        missing_conditions.append("fvg_not_retested")
    if fvg_retested and not displacement_confirmed:
        missing_conditions.append("post_retest_displacement_missing")
    if impulse_value is not None and impulse_value < MIN_EARLY_TRIGGER_RVOL:
        missing_conditions.append("volume_impulse_below_threshold")

    closest, value, threshold, ratio = _near_miss_from_components(components)
    early_index = (early_trigger or {}).get("index")
    diagnostics = {
        "candidate_id": candidate_id_value,
        "scenario_id": _scenario_identity(selected),
        "trigger_stage": trigger_stage,
        "required_next_event": required_next,
        "bars_waiting": selected_snapshot.get("market_age_bars") or selected_snapshot.get("age_bars"),
        "scans_waiting": selected_snapshot.get("runtime_update_count") or selected_snapshot.get("update_count"),
        "early_trigger_detected": bool(early_trigger),
        "confirmed_trigger_detected": bool(confirmed_trigger),
        "components": components,
        "missing_conditions": sorted(set(missing_conditions)),
        "near_miss": {
            "closest_failed_condition": closest,
            "condition_value": value,
            "condition_threshold": threshold,
            "near_miss_ratio": ratio,
        },
        "near_miss_metrics": {
            "displacement_quality": displacement_value,
            "required_displacement_quality": MIN_EARLY_TRIGGER_DISPLACEMENT_ATR,
            "bos_quality": confirmed_bos_quality,
            "required_bos_quality": MIN_TRIGGER_QUALITY,
            "close_distance_from_confirmation_level_atr": _close_distance_from_break_atr(confirmed_debug),
            "fvg_distance_from_current_price_atr": _fvg_distance_from_current_atr(
                analysis_data.get("fvg_candidates"),
                analysis_data.get("current_price"),
                analysis_data.get("atr"),
            ),
            "retracement_progress_toward_fvg": _retest_progress_toward_fvg(
                analysis_data.get("fvg_candidates"),
                analysis_data.get("current_price"),
            ),
            "bars_since_early_trigger": _bars_since_index(early_index, last_closed, scan_interval),
        },
        "last_observed_events": [
            event.get("event_type") or event.get("type")
            for event in selected_snapshot.get("events_used") or []
            if isinstance(event, dict) and (event.get("event_type") or event.get("type"))
        ],
        "opposite_direction": opposite_direction,
    }
    return diagnostics


def _normalize_analysis_time(analysis_time=None):
    if analysis_time is None:
        return pd.Timestamp.now(tz=DEFAULT_TIMEZONE)
    ts = pd.Timestamp(analysis_time)
    if ts.tzinfo is None:
        return ts.tz_localize(DEFAULT_TIMEZONE)
    return ts.tz_convert(DEFAULT_TIMEZONE)


def _age_seconds(timestamp, analysis_time=None):
    if timestamp is None:
        return None
    try:
        ts = pd.Timestamp(timestamp)
        if ts.tzinfo is None:
            ts = ts.tz_localize(DEFAULT_TIMEZONE)
        else:
            ts = ts.tz_convert(DEFAULT_TIMEZONE)
        as_of = _normalize_analysis_time(analysis_time)
        return round((as_of - ts).total_seconds(), 4)
    except Exception:
        return None


def _pd_location_id(payload):
    return "|".join(str(payload.get(key)) for key in ("zone", "range_timeframe", "range_low", "range_high"))


def _latest_fvgs_by_type(fvg_data):
    result = []
    for fvg_type in ('bullish', 'bearish'):
        candidates = [fvg for fvg in (fvg_data or []) if fvg.get('type') == fvg_type]
        if candidates:
            result.append(max(candidates, key=lambda item: _event_sort_key(item.get('end_index'))))
    return result


def _scenario_events_for_state_machine(
    direction,
    market_structure,
    premium_discount_data,
    sfp_data,
    context_structure,
    trigger_structure,
    fvg_result,
    fvg_test_data,
    expected_candidate_id=None,
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

    if sfp_data:
        raw_sfp_direction = sfp_data.get('direction')
        normalized_sfp_direction = raw_sfp_direction if raw_sfp_direction in ('bullish', 'bearish') else _direction_to_state_direction(raw_sfp_direction)
        sfp_direction = _event_direction(sfp_data) or normalized_sfp_direction
        sfp_type = str(sfp_data.get('type', ''))
        if sfp_direction == state_direction or state_direction in sfp_type:
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

    confirmed_trigger_index = _event_sort_key(trigger_structure.get('index')) if trigger_structure else None
    confirmed_trigger_id = _structure_event_id(trigger_structure) if trigger_structure else None
    if fvg_result and _fvg_matches_state_machine_scenario(
        fvg_result,
        state_direction,
        expected_candidate_id=expected_candidate_id,
        confirmed_trigger_index=confirmed_trigger_index,
        confirmed_trigger_id=confirmed_trigger_id,
    ):
        created_index = fvg_result.get('end_index')
        if created_index is not None:
            events.append((_event_sort_key(created_index), SniperEvent.FVG_CREATED))
        test_index = fvg_result.get('test_index') or (fvg_test_data or {}).get('index')
        if test_index is not None:
            events.append((_event_sort_key(test_index), SniperEvent.FVG_RETESTED))

        displacement_index = None
        if fvg_test_data:
            displacement_index = fvg_test_data.get('displacement_index')
        if displacement_index is None:
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


def _fvg_matches_state_machine_scenario(
    fvg_result,
    state_direction,
    *,
    expected_candidate_id=None,
    confirmed_trigger_index,
    confirmed_trigger_id,
    return_diagnostics=False,
):
    checks = {
        "same_direction": False,
        "after_confirmed_trigger": False,
        "candidate_id_match": None,
        "source_trigger_id_match": None,
        "source_trigger_index_match": None,
        "historical_allowed": False,
        "reconstructed_allowed": False,
        "not_invalidated": False,
        "accepted": False,
    }
    rejection_reasons = []

    if not fvg_result:
        rejection_reasons.append("missing_fvg")
        result = {"matched": False, "checks": checks, "rejection_reasons": rejection_reasons}
        return result if return_diagnostics else False

    direction_value = fvg_result.get('direction')
    fvg_type = fvg_result.get('type')
    checks["same_direction"] = direction_value in (None, state_direction) and fvg_type in (None, state_direction)
    if not checks["same_direction"]:
        rejection_reasons.append("direction_mismatch")

    checks["not_invalidated"] = not bool(fvg_result.get('invalidated', False))
    if not checks["not_invalidated"]:
        rejection_reasons.append(fvg_result.get("invalidation_reason") or "fvg_invalidated")

    checks["historical_allowed"] = not bool(fvg_result.get('historical_only', False))
    if not checks["historical_allowed"]:
        rejection_reasons.append("historical_only")

    checks["reconstructed_allowed"] = not bool(fvg_result.get('is_reconstructed', False))
    if not checks["reconstructed_allowed"]:
        rejection_reasons.append("is_reconstructed")

    created_index = fvg_result.get('end_index') or fvg_result.get('created_index')
    if confirmed_trigger_index is None:
        rejection_reasons.append("missing_confirmed_trigger")
    else:
        checks["after_confirmed_trigger"] = created_index is not None and _event_sort_key(created_index) > confirmed_trigger_index
        if not checks["after_confirmed_trigger"]:
            rejection_reasons.append("created_before_or_at_confirmed_trigger")

    source_trigger_id = fvg_result.get('source_confirmed_trigger_id')
    if source_trigger_id is not None and confirmed_trigger_id is not None and str(source_trigger_id) != str(confirmed_trigger_id):
        checks["source_trigger_id_match"] = False
        rejection_reasons.append("source_trigger_id_mismatch")
    elif source_trigger_id is not None and confirmed_trigger_id is not None:
        checks["source_trigger_id_match"] = True

    source_candidate_id = fvg_result.get('source_candidate_id') or fvg_result.get('candidate_id')
    if expected_candidate_id is not None and source_candidate_id is not None and str(source_candidate_id) != str(expected_candidate_id):
        checks["candidate_id_match"] = False
        rejection_reasons.append("candidate_id_mismatch")
    elif expected_candidate_id is not None and source_candidate_id is not None:
        checks["candidate_id_match"] = True

    source_trigger_index = fvg_result.get('source_confirmed_trigger_index') or fvg_result.get('confirmed_trigger_index')
    if source_trigger_index is not None and _event_sort_key(source_trigger_index) != confirmed_trigger_index:
        checks["source_trigger_index_match"] = False
        rejection_reasons.append("source_trigger_index_mismatch")
    elif source_trigger_index is not None:
        checks["source_trigger_index_match"] = True

    checks["accepted"] = not rejection_reasons
    result = {"matched": checks["accepted"], "checks": checks, "rejection_reasons": rejection_reasons}
    return result if return_diagnostics else checks["accepted"]


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
    expected_candidate_id=None,
):
    state_direction = _direction_to_state_direction(direction)
    if state_direction is None:
        return '0', None

    structure_result = _structure_for_state_machine(direction, market_structure, context_structure, trigger_structure)
    fvg_result = _fvg_for_state_machine(direction, fvg_test_data, fvg_data, current_price, expected_candidate_id)
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
        expected_candidate_id=expected_candidate_id,
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


def prepare_and_analyze(coin, macro_context, analysis_time=None, runtime_state=None, persist_runtime_state=True):
    df_4h = fetch_candles(coin, '4h', limit=300)
    df_1h = fetch_candles(coin, '1h', limit=300)
    df_15m = fetch_candles(coin, '15m', limit=300)

    return analyze_symbol_snapshot(
        coin,
        df_4h,
        df_1h,
        df_15m,
        macro_context,
        analysis_time=analysis_time,
        runtime_state=runtime_state,
        persist_runtime_state=persist_runtime_state,
    )


def analyze_symbol_snapshot(
    coin,
    df_4h,
    df_1h,
    df_15m,
    macro_context,
    analysis_time=None,
    runtime_state=None,
    persist_runtime_state=True,
):
    if runtime_state is not None or not persist_runtime_state:
        with scenario_runtime_state(runtime_state, persist=persist_runtime_state):
            return analyze_symbol_snapshot(
                coin,
                df_4h,
                df_1h,
                df_15m,
                macro_context,
                analysis_time=analysis_time,
            )
    if df_4h is None or df_1h is None or df_15m is None or len(df_4h) < 100 or len(df_1h) < 100 or len(df_15m) < 100:
        logger.warning(f"Недостаточно данных для {coin}.")
        return None, None

    analysis_time = _normalize_analysis_time(analysis_time)
    df_4h = df_4h.copy(deep=True)
    df_1h = df_1h.copy(deep=True)
    df_15m = df_15m.copy(deep=True)

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
    scan_interval_minutes = _scan_interval_minutes(df_15m_closed)
    market_data_timestamps = {
        "15m": str(df_15m_closed.index[-1]) if not df_15m_closed.empty else None,
        "1h": str(df_1h_closed.index[-1]) if not df_1h_closed.empty else None,
        "4h": str(df_4h_closed.index[-1]) if not df_4h_closed.empty else None,
    }
    window_15m = df_15m_closed.tail(100)
    window_1h = df_1h_closed.tail(100)
    
    # ❗️ ВАЖНО: Тренд оценивается по 4H данным для глобального контекста
    trend_data = evaluate_trend(df_4h_closed)

    # --- Анализ в окне памяти (20 свечей) ---
    sfp_data_in_window = None
    context_break_1h = None
    trigger_break_15m = None
    long_trigger_candidate = None
    short_trigger_candidate = None

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
    recent_15m_structure_events = _detect_recent_structure_events(
        df_15m_closed,
        swing_highs_15m,
        swing_lows_15m,
        timeframe_minutes=15,
        right_bars=3,
        config=bos_config,
        lookback=MAX_TRIGGER_BARS_AFTER_SFP + MAX_TRIGGER_BARS_AFTER_POI,
        limit=12,
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
        if not trigger_break_15m or not long_trigger_candidate or not short_trigger_candidate:
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
                if not trigger_break_15m:
                    trigger_break_15m = structure_break
                trigger_direction = _event_direction(structure_break)
                if trigger_direction == 'bullish' and not long_trigger_candidate:
                    long_trigger_candidate = structure_break
                elif trigger_direction == 'bearish' and not short_trigger_candidate:
                    short_trigger_candidate = structure_break

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
    htf_context = _build_htf_context_explain(
        market_structure=market_structure,
        trend_data=trend_data,
        df_4h_closed=df_4h_closed,
        df_1h_closed=df_1h_closed,
        swing_highs_4h=swing_highs_4h,
        swing_lows_4h=swing_lows_4h,
        recent_structure_events=recent_4h_structure_events,
        market_data_timestamps=market_data_timestamps,
        analysis_time=analysis_time,
    )
    low_adx_override_direction = None
    if market_structure.trend == 'neutral' and market_structure.reason == 'ADX below neutral threshold':
        low_adx_override_direction = _has_strong_reversal_context(
            sfp_data_in_window,
            context_break_1h,
            trigger_break_15m,
        )

    if market_structure.trend == 'neutral' and not low_adx_override_direction:
        neutral_trigger_scan = scan_post_anchor_trigger(
            expected_direction='NEUTRAL',
            sfp=sfp_data_in_window,
            poi=None,
            long_trigger_candidate=long_trigger_candidate,
            short_trigger_candidate=short_trigger_candidate,
            max_bars_after_sfp=MAX_TRIGGER_BARS_AFTER_SFP,
            max_bars_after_poi=MAX_TRIGGER_BARS_AFTER_POI,
            min_trigger_quality=MIN_TRIGGER_QUALITY,
        )
        neutral_trigger_debug = _build_trigger_debug(
            'NEUTRAL',
            trigger_break_15m,
            sfp_data_in_window,
            None,
            [],
            None,
            long_trigger_candidate=long_trigger_candidate,
            short_trigger_candidate=short_trigger_candidate,
            trigger_scan=neutral_trigger_scan,
        )
        neutral_scenario_scan = scan_scenarios(
            events=[ScenarioEvent('HTF_CONTEXT_CONFIRMED', 'neutral', -2, market_structure.confidence, 'htf_structure')],
            expected_direction='NEUTRAL',
            htf_structure=market_structure,
            premium_discount=None,
            risk_plan=None,
        )
        shadow_candidate = _build_shadow_candidate(
            symbol=coin,
            htf_context=htf_context,
            market_structure=market_structure,
            context_break_1h=context_break_1h,
            trigger_break_15m=trigger_break_15m,
            long_trigger_candidate=long_trigger_candidate,
            short_trigger_candidate=short_trigger_candidate,
            sfp_data=sfp_data_in_window,
            premium_discount_data=None,
            liquidity_map=liquidity_map,
            current_price=current_price,
            atr=float(last_closed_15m.get('atr', 0.0)),
            risk_plan=None,
            scenario_scan=neutral_scenario_scan,
            created_at=market_data_timestamps.get("15m"),
        )
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
                'trigger_scan_confirmed': neutral_trigger_scan.trigger_confirmed,
                'trigger_scan_rejected_reason': neutral_trigger_scan.rejected_reason,
                'scenario_scan_signal_allowed': neutral_scenario_scan.signal_allowed,
                'scenario_scan_valid': neutral_scenario_scan.scenario_valid,
                'scenario_scan_reason': neutral_scenario_scan.reason,
                'scenario_scan': _scenario_scan_snapshot(neutral_scenario_scan),
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
                'trigger_debug': _format_trigger_debug(neutral_trigger_debug),
                'trigger_scan': _format_trigger_scan(neutral_trigger_scan),
                'scenario_scan': _format_scenario_scan(neutral_scenario_scan),
                'htf_structure': _format_market_structure(market_structure),
                'adx': _format_adx(trend_data),
            },
        }, {
            'trend_data': trend_data,
            'market_structure': market_structure,
            'structure_data': None,
            'context_break_1h': context_break_1h,
            'trigger_break_15m': trigger_break_15m,
            'scenario_trigger_15m': None,
            'long_trigger_candidate': long_trigger_candidate,
            'short_trigger_candidate': short_trigger_candidate,
            'sfp_data': sfp_data_in_window,
            'fvg_candidates': [],
            'active_fvg': None,
            'premium_discount_data': None,
            'liquidity_map': liquidity_map,
            'risk_plan': None,
            'trigger_debug': neutral_trigger_debug,
            'trigger_scan': neutral_trigger_scan,
            'scenario_scan': neutral_scenario_scan,
            'state_machine': None,
            'htf_context': htf_context,
            'session': None,
            'direction': 'NEUTRAL',
            'last_closed_15m': last_closed_15m,
            'market_data_timestamps': market_data_timestamps,
            'scan_interval': scan_interval_minutes,
            'current_price': current_price,
            'atr': float(last_closed_15m.get('atr', 0.0)),
            'shadow_candidate': shadow_candidate,
            'analysis_time': analysis_time,
        }
        
    # Находим самый последний тест FVG в окне памяти
    all_fvgs = find_fvg(
        df_15m_closed,
        atr_series=df_15m_closed['atr'],
        rvol_series=df_15m_closed['rvol'],
        min_size_atr_ratio=0.5,
    )
    all_fvgs = _annotate_scenario_fvgs(all_fvgs)
    
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
    final_score_result['context_decision'] = final_score_result.get('decision')
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
    final_score_result.setdefault('context_decision', final_score_result.get('decision', 'Ignore'))
    selected_fvg_test_data = long_fvg_test_data if direction == 'LONG' else short_fvg_test_data if direction == 'SHORT' else None
    early_trigger_candidates = _detect_early_trigger_candidates(
        df_15m_closed,
        sfp_data_in_window,
        selected_fvg_test_data,
        max_bars=max(MAX_TRIGGER_BARS_AFTER_SFP, MAX_TRIGGER_BARS_AFTER_POI),
    )
    trigger_candidates = list(recent_15m_structure_events) + list(early_trigger_candidates)
    trigger_scan = scan_post_anchor_trigger(
        expected_direction=direction,
        sfp=sfp_data_in_window,
        poi=selected_fvg_test_data,
        long_trigger_candidate=long_trigger_candidate,
        short_trigger_candidate=short_trigger_candidate,
        trigger_candidates=trigger_candidates,
        df_15m_closed=df_15m_closed,
        max_bars_after_sfp=MAX_TRIGGER_BARS_AFTER_SFP,
        max_bars_after_poi=MAX_TRIGGER_BARS_AFTER_POI,
        min_trigger_quality=MIN_TRIGGER_QUALITY,
        min_early_trigger_quality=MIN_EARLY_TRIGGER_QUALITY,
    )
    scenario_trigger_15m = trigger_scan.selected_trigger
    trigger_debug = _build_trigger_debug(
        direction,
        trigger_break_15m,
        sfp_data_in_window,
        selected_fvg_test_data,
        all_fvgs,
        premium_discount_data,
        long_trigger_candidate=long_trigger_candidate,
        short_trigger_candidate=short_trigger_candidate,
        trigger_scan=trigger_scan,
    )
    state_machine_status, state_machine_result = _state_machine_diagnostic(
        direction,
        market_structure,
        premium_discount_data,
        liquidity_map,
        sfp_data_in_window,
        context_break_1h,
        scenario_trigger_15m,
        selected_fvg_test_data,
        all_fvgs,
        current_price,
        len(df_15m_closed) - 1,
    )
    final_score_result['breakdown']['liquidity_map'] = _format_liquidity_map(liquidity_map)
    final_score_result['breakdown']['state_machine'] = state_machine_status
    final_score_result['breakdown']['trigger_debug'] = _format_trigger_debug(trigger_debug)
    final_score_result['breakdown']['trigger_scan'] = _format_trigger_scan(trigger_scan)
    final_score_result['breakdown']['htf_structure'] = _format_market_structure(market_structure)
    final_score_result['breakdown']['adx'] = _format_adx(trend_data)
    final_score_result.setdefault('diagnostics', {})['state_machine_allowed'] = (
        state_machine_result.signal_allowed if state_machine_result is not None else False
    )
    final_score_result['diagnostics']['trigger_rejected_reason'] = trigger_debug.get('trigger_rejected_reason')
    final_score_result['diagnostics']['trigger_scan_rejected_reason'] = trigger_scan.rejected_reason
    final_score_result['diagnostics']['trigger_scan_confirmed'] = trigger_scan.trigger_confirmed
    final_score_result['diagnostics']['early_trigger_confirmed'] = trigger_scan.early_trigger_confirmed
    final_score_result['diagnostics']['trigger_confirmed'] = trigger_scan.trigger_confirmed
    final_score_result['diagnostics']['trigger_structure_aligned'] = bool(scenario_trigger_15m)
    final_score_result['diagnostics']['fvg_scenario_valid'] = trigger_debug.get('fvg_scenario_valid')
    final_score_result['diagnostics']['fvg_rejected_reason'] = trigger_debug.get('fvg_rejected_reason')

    scenario_events = _build_scenario_events(
        direction,
        market_structure,
        premium_discount_data,
        sfp_data_in_window,
        trigger_scan,
        context_break_1h,
        all_fvgs,
        selected_fvg_test_data,
        None,
        last_closed_15m,
    )
    scenario_scan = scan_scenarios(
        events=scenario_events,
        expected_direction=direction,
        htf_structure=market_structure,
        premium_discount=premium_discount_data,
        risk_plan=None,
    )
    scenario_scan = _apply_runtime_update_counts(coin, scenario_scan, analysis_time=last_closed_15m.name)
    selected_scenario = scenario_scan.selected_scenario
    risk_plan = _build_candidate_risk_plan(
        selected_scenario=selected_scenario,
        direction=direction,
        current_price=current_price,
        atr=float(last_closed_15m.get('atr', 0.0)),
        liquidity_map=liquidity_map,
        fvg_data=all_fvgs,
        selected_fvg_test_data=selected_fvg_test_data,
        sfp_data=sfp_data_in_window,
        structure_data=context_break_1h,
    )
    candidate_fvg_data, candidate_fvg_test_data, _ = _candidate_risk_inputs(
        selected_scenario,
        all_fvgs,
        selected_fvg_test_data,
        direction,
    )
    if selected_scenario is not None:
        candidate_sfp_data = _candidate_sfp(selected_scenario) or sfp_data_in_window
        state_machine_status, state_machine_result = _state_machine_diagnostic(
            direction,
            market_structure,
            premium_discount_data,
            liquidity_map,
            candidate_sfp_data,
            context_break_1h,
            _candidate_confirmed_trigger(selected_scenario) or scenario_trigger_15m,
            candidate_fvg_test_data,
            candidate_fvg_data,
            current_price,
            len(df_15m_closed) - 1,
            expected_candidate_id=getattr(selected_scenario, 'candidate_id', None),
        )
        final_score_result['breakdown']['state_machine'] = state_machine_status
        final_score_result['diagnostics']['state_machine_allowed'] = (
            state_machine_result.signal_allowed if state_machine_result is not None else False
        )
    if risk_plan and risk_plan.risk_plan_status != "not_available":
        assert risk_plan.source_candidate_id is not None
    if risk_plan:
        scenario_events = _build_scenario_events(
            direction,
            market_structure,
            premium_discount_data,
            _candidate_sfp(selected_scenario) or sfp_data_in_window,
            _candidate_scoped_trigger_scan(scenario_scan, direction),
            context_break_1h,
            candidate_fvg_data,
            candidate_fvg_test_data,
            risk_plan,
            last_closed_15m,
        )
        scenario_scan = scan_scenarios(
            events=scenario_events,
            expected_direction=direction,
            htf_structure=market_structure,
            premium_discount=premium_discount_data,
            risk_plan=risk_plan,
        )
        final_selected_scenario = scenario_scan.selected_scenario
        if (
            final_selected_scenario is not None
            and getattr(final_selected_scenario, 'candidate_id', None) != getattr(selected_scenario, 'candidate_id', None)
        ):
            risk_plan = _build_candidate_risk_plan(
                selected_scenario=final_selected_scenario,
                direction=direction,
                current_price=current_price,
                atr=float(last_closed_15m.get('atr', 0.0)),
                liquidity_map=liquidity_map,
                fvg_data=all_fvgs,
                selected_fvg_test_data=selected_fvg_test_data,
                sfp_data=_candidate_sfp(final_selected_scenario) or sfp_data_in_window,
                structure_data=context_break_1h,
            )
            risk_plan = _risk_plan_for_selected_candidate(risk_plan, final_selected_scenario, direction)
            candidate_fvg_data, candidate_fvg_test_data, _ = _candidate_risk_inputs(
                final_selected_scenario,
                all_fvgs,
                selected_fvg_test_data,
                direction,
            )
            scenario_events = _build_scenario_events(
                direction,
                market_structure,
                premium_discount_data,
                _candidate_sfp(final_selected_scenario) or sfp_data_in_window,
                _candidate_scoped_trigger_scan(scenario_scan, direction),
                context_break_1h,
                candidate_fvg_data,
                candidate_fvg_test_data,
                risk_plan,
                last_closed_15m,
            )
            scenario_scan = scan_scenarios(
                events=scenario_events,
                expected_direction=direction,
                htf_structure=market_structure,
                premium_discount=premium_discount_data,
                risk_plan=risk_plan,
            )
        risk_plan = _risk_plan_for_selected_candidate(risk_plan, scenario_scan.selected_scenario, direction)
    scenario_scan = _apply_runtime_update_counts(coin, scenario_scan, analysis_time=last_closed_15m.name)
    if risk_plan:
        final_score_result['breakdown']['risk_plan'] = _format_risk_plan(risk_plan)
        final_score_result.setdefault('diagnostics', {})['risk_plan_valid'] = risk_plan.valid
        final_score_result['diagnostics']['risk_geometry_valid'] = risk_plan.valid
        if final_score_result.get('total_score', 0) >= 70 and not risk_plan.valid:
            raw_before_risk = final_score_result.get('raw_score', final_score_result.get('total_score', 0))
            final_score_result['raw_score'] = raw_before_risk
            final_score_result['total_score'] = 69
            final_score_result['decision'] = 'Watchlist'
            final_score_result['no_trade_reason'] = 'risk_plan_block'
            final_score_result['diagnostics']['no_trade_reason'] = 'risk_plan_block'
            rr_text = f"T1 {risk_plan.rr_to_target_1:.2f}R" if risk_plan.rr_to_target_1 is not None else "entry not available"
            final_score_result['breakdown']['risk_plan'] = (
                f"WATCHLIST ({risk_plan.reason}, score {raw_before_risk}->69, "
                f"{rr_text})"
            )
    else:
        final_score_result['breakdown']['risk_plan'] = '0'
        final_score_result.setdefault('diagnostics', {})['risk_plan_valid'] = False
        final_score_result['diagnostics']['risk_geometry_valid'] = False
    final_score_result['breakdown']['scenario_scan'] = _format_scenario_scan(scenario_scan)
    final_score_result['diagnostics']['scenario_scan_signal_allowed'] = scenario_scan.signal_allowed
    final_score_result['diagnostics']['scenario_scan_valid'] = scenario_scan.scenario_valid
    final_score_result['diagnostics']['scenario_scan_reason'] = scenario_scan.reason
    final_score_result['diagnostics']['scenario_scan'] = _scenario_scan_snapshot(scenario_scan)
    selected_scenario = scenario_scan.selected_scenario
    scoped_trigger_scan = _candidate_scoped_trigger_scan(scenario_scan, direction)
    scoped_trigger_snapshot = _trigger_scan_snapshot(scoped_trigger_scan) or {}
    final_score_result['breakdown']['trigger_scan'] = _format_trigger_scan(scoped_trigger_scan)
    final_score_result['diagnostics']['trigger_scan_rejected_reason'] = scoped_trigger_snapshot.get('rejected_reason')
    final_score_result['diagnostics']['trigger_scan_confirmed'] = bool(scoped_trigger_snapshot.get('trigger_confirmed'))
    final_score_result['diagnostics']['early_trigger_confirmed'] = bool(scoped_trigger_snapshot.get('early_trigger_confirmed'))
    final_score_result['diagnostics']['trigger_confirmed'] = bool(scoped_trigger_snapshot.get('trigger_confirmed'))
    final_score_result['diagnostics']['scenario_scan_status'] = selected_scenario.status if selected_scenario else None
    final_score_result['diagnostics']['scenario_scan_direction'] = scenario_scan.selected_direction
    final_score_result['diagnostics']['scenario_risk_valid'] = bool(selected_scenario and selected_scenario.risk_valid)
    final_score_result['scenario_status'] = _decision_scenario_status(scenario_scan)
    final_score_result['execution_status'] = _decision_execution_status(scenario_scan, risk_plan)

    raw_or_score = final_score_result.get('raw_score', final_score_result.get('total_score', 0))
    scenario_complete = bool(
        scenario_scan.signal_allowed
        and scenario_scan.scenario_valid
        and selected_scenario is not None
        and selected_scenario.status == 'complete'
        and risk_plan is not None
        and risk_plan.valid
    )
    if raw_or_score >= 70 and not scenario_complete:
        final_score_result['raw_score'] = raw_or_score
        final_score_result['total_score'] = min(final_score_result.get('total_score', raw_or_score), 69)
        final_score_result['decision'] = 'Watchlist'
        final_score_result['no_trade_reason'] = scenario_scan.reason
        final_score_result['diagnostics']['no_trade_reason'] = scenario_scan.reason
        final_score_result['breakdown']['scenario'] = (
            f"WATCHLIST (Scenario Scan gate: {scenario_scan.reason}, "
            f"score {raw_or_score}->69)"
        )

    analysis_data = {
        "trend_data": trend_data,
        "market_structure": market_structure,
        # Для А+ сетапа нам нужен уровень от 1H структуры
        "structure_data": context_break_1h or scenario_trigger_15m,
        "context_break_1h": context_break_1h,
        "trigger_break_15m": trigger_break_15m,
        "scenario_trigger_15m": scenario_trigger_15m,
        "long_trigger_candidate": long_trigger_candidate,
        "short_trigger_candidate": short_trigger_candidate,
        "recent_15m_structure_events": recent_15m_structure_events,
        "early_trigger_candidates": early_trigger_candidates,
        "sfp_data": sfp_data_in_window,
        "fvg_candidates": all_fvgs,
        "active_fvg": selected_fvg_test_data,
        "premium_discount_data": premium_discount_data,
        "liquidity_map": liquidity_map,
        "risk_plan": risk_plan,
        "trigger_debug": trigger_debug,
        "trigger_scan": scoped_trigger_scan,
        "global_trigger_scan": trigger_scan,
        "scenario_scan": scenario_scan,
        "scenario_events": scenario_events,
        "state_machine": state_machine_status,
        "htf_context": htf_context,
        "session": None,
        "direction": direction,
        "last_closed_15m": last_closed_15m,
        "market_data_timestamps": market_data_timestamps,
        "scan_interval": scan_interval_minutes,
        "current_price": current_price,
        "atr": float(last_closed_15m.get('atr', 0.0)),
        "analysis_time": analysis_time,
    }
    if ENABLE_SCENARIO_RESEARCH_TRACE:
        analysis_data["research_15m_candles"] = _research_candles_for_candidate(
            df_15m_closed,
            scenario_scan.selected_scenario,
            max_candles=SCENARIO_RESEARCH_TRACE_MAX_CANDLES,
        )
    analysis_data["shadow_candidate"] = _build_shadow_candidate(
        symbol=coin,
        htf_context=htf_context,
        market_structure=market_structure,
        context_break_1h=context_break_1h,
        trigger_break_15m=trigger_break_15m,
        long_trigger_candidate=long_trigger_candidate,
        short_trigger_candidate=short_trigger_candidate,
        sfp_data=sfp_data_in_window,
        premium_discount_data=premium_discount_data,
        liquidity_map=liquidity_map,
        current_price=current_price,
        atr=float(last_closed_15m.get('atr', 0.0)),
        risk_plan=risk_plan,
        scenario_scan=scenario_scan,
        created_at=market_data_timestamps.get("15m"),
    )

    return final_score_result, analysis_data


def _decision_scenario_status(scenario_scan):
    selected = getattr(scenario_scan, "selected_scenario", None) if scenario_scan else None
    if selected is None:
        return "none"
    if selected.status == "invalidated":
        return "invalidated"
    if selected.status == "complete":
        return "complete"
    next_step = selected.next_expected_step
    mapping = {
        "POI_TOUCHED": "waiting_for_anchor",
        "SFP_CONFIRMED": "waiting_for_anchor",
        "EARLY_TRIGGER_CONFIRMED": "waiting_for_early_trigger",
        "CONFIRMED_TRIGGER_CONFIRMED": "waiting_for_confirmed_trigger",
        "FVG_CREATED": "waiting_for_fvg",
        "FVG_RETESTED": "waiting_for_retest",
        "DISPLACEMENT_CONFIRMED": "waiting_for_displacement",
        "RISK_VALID": "waiting_for_risk",
    }
    return mapping.get(next_step, selected.status)


def _decision_execution_status(scenario_scan, risk_plan):
    selected = getattr(scenario_scan, "selected_scenario", None) if scenario_scan else None
    if not selected or selected.status != "complete":
        return "not_ready"
    risk_valid = risk_plan.get("valid", False) if isinstance(risk_plan, dict) else getattr(risk_plan, "valid", False)
    if risk_plan is None or not risk_valid:
        return "not_ready"
    return "ready"


def _normalize_final_decision(decision):
    if decision == "A+":
        return "A+"
    if decision in ("Watchlist", "A+ WATCH ONLY"):
        return "Watchlist"
    return "Ignore"

def market_scan(report_mode="HUNT", analysis_time=None):
    decision_time = _normalize_analysis_time(analysis_time)
    started_at_ts = time.time()
    started_at = pd.Timestamp.now(tz=DEFAULT_TIMEZONE).isoformat()
    session = evaluate_session(now=decision_time)
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
    logger.info(f"run_started | [{current_time_str}] Запуск сканирования | Режим: {report_mode} | Сессия: {session_status}")

    dashboard_lines = []
    run_id = str(uuid.uuid4())
    symbol_results = []
    errors = []

    for coin in COINS_LIST:
        try:
            score_result, analysis_data = prepare_and_analyze(coin, macro, analysis_time=decision_time)
            if not score_result or not analysis_data:
                dashboard_lines.append(f"• <b>{coin}</b>: Н/Д (ошибка данных)")
                error = _analysis_error(coin, "prepare_and_analyze", "NoAnalysisData", run_id)
                errors.append(error)
                symbol_results.append({"symbol": coin, "success": False, "error": error})
                continue
            analysis_data['session'] = session

            total_score = score_result.get('total_score', 0)
            decision = resolve_session_decision(score_result, in_kz)
            final_decision = _normalize_final_decision(decision)
            score_result['session_decision'] = decision
            score_result['final_decision'] = final_decision
            score_result['decision'] = final_decision
            delivery_gate = _annotate_a_plus_delivery_gate(score_result, analysis_data, in_kz)
            symbol_results.append({
                "symbol": coin,
                "success": True,
                "decision": final_decision,
                "context_decision": score_result.get("context_decision"),
                "scenario_status": score_result.get("scenario_status"),
                "execution_status": score_result.get("execution_status"),
                "final_decision": final_decision,
                "score_result": score_result,
                "diagnostics": score_result.get("diagnostics", {}),
                "analysis_data": analysis_data,
                "scenario_scan": analysis_data.get("scenario_scan"),
            })
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
                    transition_timestamp = pd.Timestamp.now(tz=DEFAULT_TIMEZONE).isoformat()
                    for transition_record in _build_scenario_transition_records(
                        run_id,
                        transition_timestamp,
                        coin,
                        analysis_data.get("scenario_scan"),
                        detected_at=transition_timestamp,
                    ):
                        write_scan_record(transition_record)
                except Exception as journal_error:
                    logger.error(f"Не удалось записать scan journal для {coin}: {journal_error}")

            # Отправка А+ сетапа
            if delivery_gate["allowed"]:
                direction = analysis_data['direction']
                risk_plan = analysis_data.get('risk_plan')
                entry_price = risk_plan.get('entry') if risk_plan and risk_plan.get('valid') else None
                selected_delivery_scenario = getattr(analysis_data.get('scenario_scan'), "selected_scenario", None)
                delivery_candidate_id = getattr(selected_delivery_scenario, "candidate_id", None)
                delivery_scenario_id = _scenario_identity(selected_delivery_scenario)

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
                        send_telegram_alert(
                            f"🚨 <b>СНАЙПЕР ОБНАРУЖИЛ СЕТАП! ({total_score}/100)</b> 🚨\n\n{ai_response}",
                            run_id=run_id,
                            message_type="A_PLUS",
                            delivery_context={
                                "symbol": coin,
                                "candidate_id": delivery_candidate_id,
                                "scenario_id": delivery_scenario_id,
                                "delivery_gate_result": delivery_gate,
                                "in_kill_zone": bool(in_kz),
                                "outside_kz_delivery_enabled": SEND_A_PLUS_OUTSIDE_KZ,
                                "kill_zone_bypassed": bool(delivery_gate.get("kill_zone_bypassed")),
                            },
                        )
                        last_alert_time[coin] = time.time()
                    except Exception as e:
                        logger.error(f"Ошибка Gemini для A+ сетапа {coin}: {e}")
                        _record_telegram_delivery(
                            run_id=run_id,
                            message_type="A_PLUS",
                            attempted=True,
                            sent=False,
                            error=f"gemini_generation_failed: {e}",
                            status_code=None,
                            message_length=None,
                            symbol=coin,
                            candidate_id=delivery_candidate_id,
                            scenario_id=delivery_scenario_id,
                            delivery_gate_result=delivery_gate,
                            in_kill_zone=bool(in_kz),
                            outside_kz_delivery_enabled=SEND_A_PLUS_OUTSIDE_KZ,
                            kill_zone_bypassed=bool(delivery_gate.get("kill_zone_bypassed")),
                        )

            trend_data = analysis_data.get('trend_data')
            trend_strength = trend_data.get('strength', 'Н/Д') if trend_data else "Н/Д"
            
            # Добавляем строку в дашборд
            if coin in ['BTC', 'ETH', 'SOL']:
            # if coin in COINS_LIST:
                dashboard_lines.append(_build_dashboard_block(coin, score_result, analysis_data, final_decision, in_kz))
            else:
                dashboard_lines.append(
                    f"• <b>{_html_text(coin)}</b>: {total_score} баллов | "
                    f"{_html_text(final_decision)} | Тренд: {_html_text(trend_strength)}"
                )

        except Exception as e:
            logger.error(f"Критическая ошибка при анализе {coin}: {e}", exc_info=True)
            dashboard_lines.append(f"• <b>{coin}</b>: ОШИБКА АНАЛИЗА")
            error = _analysis_error(coin, "symbol_analysis", e, run_id)
            errors.append(error)
            symbol_results.append({"symbol": coin, "success": False, "error": error})

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
        send_telegram_blocks(summary_header, dashboard_lines, run_id=run_id, message_type="DASHBOARD")
    elif report_mode == "HUNT" and not in_kz:
        logger.info(f"[{current_time_str}] Вне Kill Zone. Дашборд скрыт для экономии эфира.")

    finished_at = pd.Timestamp.now(tz=DEFAULT_TIMEZONE).isoformat()
    duration_seconds = time.time() - started_at_ts
    if SCAN_JOURNAL_ENABLED:
        try:
            summary_path = write_scan_record(
                _build_run_summary_record(
                    run_id=run_id,
                    started_at=started_at,
                    finished_at=finished_at,
                    duration_seconds=duration_seconds,
                    report_mode=report_mode,
                    session=session,
                    symbol_results=symbol_results,
                    errors=errors,
                )
            )
            logger.info(f"Run summary записан: {summary_path}")
        except Exception as journal_error:
            logger.error(f"run_failed | Не удалось записать run_summary: {journal_error}")
    logger.info(
        f"run_completed | run_id={run_id} | symbols_success={len([item for item in symbol_results if item.get('success')])} "
        f"| symbols_failed={len(errors)} | duration_seconds={duration_seconds:.2f}"
    )

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
