import os
import re
import json
import math

_INT_PATTERN = re.compile(r"-?[0-9]+")


def save_json_data(filepath: str, data: dict) -> None:
    """Saves dictionary data to a JSON file with formatting."""
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4)

def _parse_int(raw: str) -> int | None:
    """Parses a base-10 integer (optionally negative), returning None if malformed."""
    raw = raw.strip()
    return int(raw) if _INT_PATTERN.fullmatch(raw) else None

def get_env_int(key: str, default: int = 0) -> int:
    """Retrieves an integer from environment variables safely."""
    val = os.getenv(key)
    if val is None:
        return default
    parsed = _parse_int(val)
    return default if parsed is None else parsed

def get_env_float(key: str, default: float = 0.0) -> float:
    """Retrieves a finite float from environment variables safely."""
    val = os.getenv(key)
    if val is not None:
        try:
            parsed = float(val.strip())
        except ValueError:
            return default
        return parsed if math.isfinite(parsed) else default
    return default

def get_env_int_list(key: str) -> list[int]:
    """Parses a comma-separated string of integers from .env into a list of ints."""
    val = os.getenv(key, "")
    parsed = (_parse_int(x) for x in val.split(","))
    return [x for x in parsed if x is not None]

def is_env_enabled(key: str, default: bool = True) -> bool:
    """Checks if a boolean environment variable toggle is enabled."""
    default_str = "True" if default else "False"
    return os.getenv(key, default_str).strip().lower() in ("true", "1", "t")
