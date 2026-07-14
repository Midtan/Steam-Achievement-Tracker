from __future__ import annotations

import json
import urllib.parse
import urllib.request
import urllib.error
from typing import Any

from .config import STEAM_API_KEY


class SteamError(RuntimeError):
    pass


class SteamPrivateProfileError(SteamError):
    """Raised when Steam profile or game details are private."""
    pass


def _require_key() -> str:
    if not STEAM_API_KEY:
        raise SteamError("Set STEAM_API_KEY before using Steam API actions.")
    return STEAM_API_KEY


def _get_json(url: str, params: dict[str, Any]) -> dict[str, Any]:
    query = urllib.parse.urlencode(params)
    request = urllib.request.Request(f"{url}?{query}", headers={"User-Agent": "achievement-tracker/0.1"})
    try:
        with urllib.request.urlopen(request, timeout=25) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 403:
            raise SteamPrivateProfileError(
                "Steam profile or game details are private. "
                "Please ensure your Steam profile and game details are set to public. "
                "Go to Steam -> Profile -> Edit Profile -> My Privacy Settings -> "
                "Set 'Game details' and 'Profile' to 'Public'."
            ) from exc
        raise SteamError(f"Steam request failed with HTTP {exc.code}: {exc.reason}") from exc
    except Exception as exc:
        raise SteamError(f"Steam request failed: {exc}") from exc


def resolve_steam_id(identifier: str) -> tuple[str, str]:
    value = identifier.strip()
    if value.isdigit() and len(value) >= 16:
        return value, ""

    data = _get_json(
        "https://api.steampowered.com/ISteamUser/ResolveVanityURL/v1/",
        {"key": _require_key(), "vanityurl": value},
    )
    response = data.get("response", {})
    if response.get("success") != 1:
        raise SteamError(f"Could not resolve Steam vanity name '{value}'.")
    return str(response["steamid"]), value


def fetch_player_summaries(steam_ids: list[str]) -> dict[str, dict[str, str]]:
    """Fetch player summaries including avatar URLs.
    Returns dict mapping steam_id to dict with 'name' and 'avatar' keys.
    """
    if not steam_ids:
        return {}
    data = _get_json(
        "https://api.steampowered.com/ISteamUser/GetPlayerSummaries/v2/",
        {"key": _require_key(), "steamids": ",".join(steam_ids)},
    )
    players = data.get("response", {}).get("players", [])
    return {
        str(player["steamid"]): {
            "name": player.get("personaname", str(player["steamid"])),
            "avatar": player.get("avatarfull") or player.get("avatarmedium") or player.get("avatar") or "",
        }
        for player in players
    }


def fetch_game_schema(app_id: int) -> list[dict[str, Any]]:
    data = _get_json(
        "https://api.steampowered.com/ISteamUserStats/GetSchemaForGame/v2/",
        {"key": _require_key(), "appid": app_id, "l": "english"},
    )
    achievements = data.get("game", {}).get("availableGameStats", {}).get("achievements", [])
    if not isinstance(achievements, list):
        raise SteamError("Steam schema response did not contain achievements.")
    return achievements


def fetch_player_achievements(app_id: int, steam_id: str) -> list[dict[str, Any]]:
    data = _get_json(
        "https://api.steampowered.com/ISteamUserStats/GetPlayerAchievements/v0001/",
        {"key": _require_key(), "appid": app_id, "steamid": steam_id, "l": "english"},
    )
    if not data.get("playerstats", {}).get("success", True):
        raise SteamError(f"Steam did not return achievement stats for {steam_id}.")
    achievements = data.get("playerstats", {}).get("achievements", [])
    if not isinstance(achievements, list):
        raise SteamError("Steam player achievement response did not contain achievements.")
    return achievements


def fetch_player_stats(app_id: int, steam_id: str) -> dict[str, Any]:
    """Raw GetUserStatsForGame response (numeric stat counters, e.g. kill counts)."""
    return _get_json(
        "https://api.steampowered.com/ISteamUserStats/GetUserStatsForGame/v0002/",
        {"key": _require_key(), "appid": app_id, "steamid": steam_id},
    )

