import json
import os
from dataclasses import asdict, is_dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

import pandas as pd


DEFAULT_JOURNAL_DIR = Path("data/journal")


def write_scan_record(record: Dict[str, Any], journal_dir: Optional[Path] = None) -> Path:
    target_dir = Path(journal_dir or os.environ.get("SCAN_JOURNAL_DIR", DEFAULT_JOURNAL_DIR))
    target_dir.mkdir(parents=True, exist_ok=True)

    timestamp = pd.Timestamp(record.get("timestamp", datetime.utcnow()))
    date_part = timestamp.strftime("%Y-%m-%d")
    target_path = target_dir / f"scans_{date_part}.jsonl"

    with target_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(_json_safe(record), ensure_ascii=False, sort_keys=True))
        fh.write("\n")

    return target_path


def _json_safe(value: Any) -> Any:
    if is_dataclass(value):
        return _json_safe(asdict(value))
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    if isinstance(value, pd.Series):
        return _json_safe(value.to_dict())
    if isinstance(value, pd.DataFrame):
        return _json_safe(value.to_dict(orient="records"))
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if hasattr(value, "isoformat"):
        try:
            return value.isoformat()
        except TypeError:
            pass
    if hasattr(value, "to_dict"):
        try:
            return _json_safe(value.to_dict())
        except TypeError:
            pass
    if hasattr(value, "item"):
        try:
            return value.item()
        except (TypeError, ValueError):
            pass
    return value
