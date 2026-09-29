"""Load built-in and user code maps."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from homeassistant.core import HomeAssistant

from .codec import CodeMap, CodeMapError
from .const import DOMAIN, USER_MAPS_DIR

_LOGGER = logging.getLogger(__name__)

BUILTIN_DIR = Path(__file__).parent / "maps"


def _load_dir(directory: Path, prefix: str) -> dict[str, CodeMap]:
    maps: dict[str, CodeMap] = {}
    if not directory.is_dir():
        return maps
    for path in sorted(directory.glob("*.json")):
        map_id = f"{prefix}{path.stem}"
        try:
            maps[map_id] = CodeMap.from_dict(map_id, json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError, CodeMapError) as err:
            _LOGGER.error("Skipping IR code map %s: %s", path, err)
    return maps


def _load_all(config_dir: str) -> dict[str, CodeMap]:
    maps = _load_dir(BUILTIN_DIR, "")
    # User maps are prefixed so they can never shadow a built-in one.
    maps.update(_load_dir(Path(config_dir) / USER_MAPS_DIR, "user_"))
    return maps


async def async_get_code_maps(hass: HomeAssistant, reload: bool = False) -> dict[str, CodeMap]:
    """Return all code maps, loading them from disk on first use (or when reload=True)."""
    cache = hass.data.setdefault(DOMAIN, {})
    if reload or "maps" not in cache:
        cache["maps"] = await hass.async_add_executor_job(_load_all, hass.config.config_dir)
    return cache["maps"]
