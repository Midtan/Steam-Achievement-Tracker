from __future__ import annotations

import time
from datetime import datetime
from typing import Any

from . import steam
from .config import PLAYER_REFRESH_TTL_SECONDS
from .db import connect, json_dump, json_load, row_to_dict, utc_now
from .game_plugins import load_plugin


def list_games() -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute("SELECT * FROM games ORDER BY name").fetchall()
    return [row_to_dict(row) for row in rows]


def add_game(app_id: int, name: str, plugin: str = "") -> dict[str, Any]:
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO games(app_id, name, plugin)
            VALUES (?, ?, ?)
            ON CONFLICT(app_id) DO UPDATE SET name = excluded.name, plugin = excluded.plugin
            """,
            (app_id, name.strip(), plugin.strip()),
        )
        row = conn.execute("SELECT * FROM games WHERE app_id = ?", (app_id,)).fetchone()
    return row_to_dict(row)


def delete_game(game_id: int) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM games WHERE id = ?", (game_id,))


def list_players() -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute("SELECT * FROM players ORDER BY display_name").fetchall()
    return [row_to_dict(row) for row in rows]


def add_player(identifier: str, display_name: str = "") -> dict[str, Any]:
    steam_id, vanity = steam.resolve_steam_id(identifier)
    summary = steam.fetch_player_summaries([steam_id]).get(steam_id, {})
    resolved_name = display_name.strip() or summary.get("name", steam_id)
    avatar_url = summary.get("avatar", "")

    with connect() as conn:
        conn.execute(
            """
            INSERT INTO players(steam_id, display_name, vanity_name, avatar_url)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(steam_id) DO UPDATE SET
                display_name = excluded.display_name,
                vanity_name = excluded.vanity_name,
                avatar_url = excluded.avatar_url
            """,
            (steam_id, resolved_name, vanity, avatar_url),
        )
        row = conn.execute("SELECT * FROM players WHERE steam_id = ?", (steam_id,)).fetchone()
    return row_to_dict(row)


def delete_player(player_id: int) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM players WHERE id = ?", (player_id,))


def refresh_game_schema(game_id: int) -> dict[str, Any]:
    with connect() as conn:
        game = conn.execute("SELECT * FROM games WHERE id = ?", (game_id,)).fetchone()
    if not game:
        raise ValueError("Game not found.")

    plugin = load_plugin(game["plugin"])
    achievements = steam.fetch_game_schema(int(game["app_id"]))
    plugin_metadata: dict[str, dict[str, Any]] = {}
    plugin_error = ""
    if plugin and hasattr(plugin, "enrich_all"):
        try:
            plugin_metadata = plugin.enrich_all(achievements)
        except Exception as exc:
            plugin_error = str(exc)
    now = utc_now()
    with connect() as conn:
        for index, ach in enumerate(achievements):
            api_name = ach.get("name", "")
            if not api_name:
                continue
            metadata = plugin_metadata.get(api_name, {})
            if plugin:
                metadata = plugin.enrich(api_name, metadata)
            conn.execute(
                """
                INSERT INTO achievements(
                    game_id, api_name, display_name, description, icon, icon_gray, hidden, sort_order, metadata
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(game_id, api_name) DO UPDATE SET
                    display_name = excluded.display_name,
                    description = excluded.description,
                    icon = excluded.icon,
                    icon_gray = excluded.icon_gray,
                    hidden = excluded.hidden,
                    sort_order = excluded.sort_order,
                    metadata = excluded.metadata
                """,
                (
                    game_id,
                    api_name,
                    ach.get("displayName", api_name),
                    ach.get("description", ""),
                    ach.get("icon", ""),
                    ach.get("icongray", ""),
                    1 if ach.get("hidden") else 0,
                    index,
                    json_dump(metadata),
                ),
            )
        conn.execute("UPDATE games SET last_schema_refresh = ? WHERE id = ?", (now, game_id))

    return {
        "updated": len(achievements),
        "plugin_metadata": len(plugin_metadata),
        "plugin_error": plugin_error,
        "last_schema_refresh": now,
    }


def refresh_player_state(game_id: int, player_id: int) -> dict[str, Any]:
    with connect() as conn:
        game = conn.execute("SELECT * FROM games WHERE id = ?", (game_id,)).fetchone()
        player = conn.execute("SELECT * FROM players WHERE id = ?", (player_id,)).fetchone()
    if not game or not player:
        raise ValueError("Game or player not found.")

    try:
        achievements = steam.fetch_player_achievements(int(game["app_id"]), str(player["steam_id"]))
    except steam.SteamPrivateProfileError as exc:
        raise ValueError(
            f"Cannot fetch achievements for player '{player['display_name']}': "
            f"Steam profile or game details are private. "
            f"Please ensure the Steam profile and game details are set to public in Steam privacy settings."
        ) from exc
    now = utc_now()
    with connect() as conn:
        for ach in achievements:
            api_name = ach.get("apiname") or ach.get("name")
            if not api_name:
                continue
            conn.execute(
                """
                INSERT INTO player_achievements(game_id, player_id, api_name, achieved, unlock_time, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(game_id, player_id, api_name) DO UPDATE SET
                    achieved = excluded.achieved,
                    unlock_time = excluded.unlock_time,
                    updated_at = excluded.updated_at
                """,
                (
                    game_id,
                    player_id,
                    api_name,
                    1 if ach.get("achieved") else 0,
                    int(ach.get("unlocktime") or 0),
                    now,
                ),
            )
        conn.execute("UPDATE players SET last_refresh = ? WHERE id = ?", (now, player_id))
    return {"updated": len(achievements), "last_refresh": now}


def refresh_all_players(game_id: int, force: bool = False) -> dict[str, Any]:
    refreshed = []
    with connect() as conn:
        players = conn.execute("SELECT * FROM players ORDER BY display_name").fetchall()
    for player in players:
        if not force and not _is_stale(player["last_refresh"]):
            continue
        refreshed.append(refresh_player_state(game_id, int(player["id"])))
    return {"refreshed": len(refreshed)}


def dashboard(game_id: int, refresh_stale: bool = True) -> dict[str, Any]:
    if refresh_stale:
        try:
            refresh_all_players(game_id, force=False)
        except steam.SteamError:
            pass

    with connect() as conn:
        game = conn.execute("SELECT * FROM games WHERE id = ?", (game_id,)).fetchone()
        players = conn.execute("SELECT * FROM players ORDER BY display_name").fetchall()
        achievements = conn.execute(
            "SELECT * FROM achievements WHERE game_id = ? ORDER BY sort_order, display_name",
            (game_id,),
        ).fetchall()
        states = conn.execute(
            "SELECT * FROM player_achievements WHERE game_id = ?",
            (game_id,),
        ).fetchall()
    if not game:
        raise ValueError("Game not found.")

    player_list = [row_to_dict(row) for row in players]
    state_map = {(row["player_id"], row["api_name"]): row for row in states}
    items = []
    for row in achievements:
        achievement = row_to_dict(row)
        achievement["metadata"] = json_load(achievement["metadata"])
        per_player = []
        achieved_count = 0
        for player in player_list:
            state = state_map.get((player["id"], achievement["api_name"]))
            achieved = bool(state["achieved"]) if state else False
            achieved_count += 1 if achieved else 0
            per_player.append(
                {
                    "player_id": player["id"],
                    "display_name": player["display_name"],
                    "avatar_url": player.get("avatar_url", ""),
                    "achieved": achieved,
                    "unlock_time": state["unlock_time"] if state else 0,
                }
            )
        achievement["players"] = per_player
        achievement["achieved_count"] = achieved_count
        achievement["missing_count"] = max(len(player_list) - achieved_count, 0)
        items.append(achievement)

    plugin = load_plugin(game["plugin"])
    return {
        "game": row_to_dict(game),
        "players": player_list,
        "achievements": items,
        "plugin_fields": plugin.fields() if plugin else [],
        "plugin_filter_config": plugin.filter_config() if plugin else {},
    }


def _is_stale(value: str | None) -> bool:
    if not value:
        return True
    try:
        then = datetime.fromisoformat(value).timestamp()
    except ValueError:
        return True
    return time.time() - then > PLAYER_REFRESH_TTL_SECONDS
