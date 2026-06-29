from __future__ import annotations

import importlib
from typing import Any, Protocol


class GamePlugin(Protocol):
    slug: str
    label: str

    def fields(self) -> list[dict[str, str]]: ...

    def filter_config(self) -> dict[str, dict[str, Any]]: ...

    def enrich(self, api_name: str, current: dict[str, Any]) -> dict[str, Any]: ...

    def enrich_all(self, achievements: list[dict[str, Any]]) -> dict[str, dict[str, Any]]: ...


def available_plugins() -> list[dict[str, str]]:
    plugins = []
    for name in ("payday2",):
        plugin = load_plugin(name)
        plugins.append({"slug": name, "label": getattr(plugin, "label", name)})
    return plugins


def load_plugin(slug: str) -> GamePlugin | None:
    if not slug:
        return None
    try:
        return importlib.import_module(f"app.game_plugins.{slug}")  # type: ignore[return-value]
    except ModuleNotFoundError:
        return None
