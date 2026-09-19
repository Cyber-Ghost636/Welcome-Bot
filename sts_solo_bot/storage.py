import json
from typing import Any

from .config import CONFIG_FILE


def load_config() -> dict[str, dict[str, Any]]:
    if not CONFIG_FILE.exists():
        return {}
    try:
        data = json.loads(CONFIG_FILE.read_text())
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return {}


def save_config(config: dict[str, dict[str, Any]]) -> None:
    CONFIG_FILE.write_text(json.dumps(config, indent=2))


def get_guild_config(config: dict[str, dict[str, Any]], guild_id: int) -> dict[str, Any]:
    key = str(guild_id)
    guild_config = config.get(key, {})
    config[key] = guild_config
    return guild_config


def parse_bungie_timestamp(value: str | None):
    if not value:
        return None
    try:
        from datetime import datetime

        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
