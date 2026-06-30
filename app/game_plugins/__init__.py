from __future__ import annotations

import importlib
import pkgutil
from typing import Any, Protocol


class GamePlugin(Protocol):
    slug: str
    label: str

    def fields(self) -> list[dict[str, str]]: ...

    def filter_config(self) -> dict[str, dict[str, Any]]: ...

    def enrich(self, api_name: str, current: dict[str, Any]) -> dict[str, Any]: ...

    def enrich_all(self, achievements: list[dict[str, Any]]) -> dict[str, dict[str, Any]]: ...


def _discover_slugs() -> list[str]:
    import app.game_plugins as _pkg
    return [name for _, name, is_pkg in pkgutil.iter_modules(_pkg.__path__) if not is_pkg]


def available_plugins() -> list[dict[str, str]]:
    plugins = []
    for slug in _discover_slugs():
        plugin = load_plugin(slug)
        if plugin is not None:
            plugins.append({"slug": slug, "label": getattr(plugin, "label", slug)})
    return plugins


def load_plugin(slug: str) -> GamePlugin | None:
    if not slug:
        return None
    try:
        return importlib.import_module(f"app.game_plugins.{slug}")  # type: ignore[return-value]
    except ModuleNotFoundError:
        return None
