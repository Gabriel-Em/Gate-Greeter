import os
import json

def ensure_json_file(filepath: str) -> None:
    """Creates an empty JSON object file if it does not already exist."""
    if not os.path.exists(filepath):
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump({}, f)

def load_json_data(filepath: str) -> dict:
    """Safely loads and returns JSON data from a file."""
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, FileNotFoundError):
        return {}

def save_json_data(filepath: str, data: dict) -> None:
    """Saves dictionary data to a JSON file with formatting."""
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4)

def get_env_int(key: str, default: int = 0) -> int:
    """Retrieves an integer from environment variables safely."""
    val = os.getenv(key)
    if val is not None and val.strip().isdigit():
        return int(val.strip())
    return default

def get_env_float(key: str, default: float = 0.0) -> float:
    """Retrieves a float from environment variables safely."""
    val = os.getenv(key)
    if val is not None:
        try:
            return float(val.strip())
        except ValueError:
            return default
    return default

def get_env_int_list(key: str) -> list[int]:
    """Parses a comma-separated string of integers from .env into a list of ints."""
    val = os.getenv(key, "")
    if not val.strip():
        return []
    return [int(x.strip()) for x in val.split(",") if x.strip().isdigit()]

def is_env_enabled(key: str, default: bool = True) -> bool:
    """Checks if a boolean environment variable toggle is enabled."""
    default_str = "True" if default else "False"
    return os.getenv(key, default_str).strip().lower() in ("true", "1", "t")
