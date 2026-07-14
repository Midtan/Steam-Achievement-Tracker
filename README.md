# Steam Achievement Tracker

Local co-op achievement planning tool. Tracks which players in a group have or are missing each Steam achievement, with per-game filtering powered by a plugin system.

No external Python dependencies — stdlib only. Data stored in SQLite.

## What it does

- Tracks multiple Steam games.                                                                                                                                                                   
- Tracks any number of players by SteamID64 or vanity profile name.                                                                                                                              
- Pulls and caches the achievement list per game.                                                                                                                                                
- Refreshes player completion state without re-pulling the achievement schema.                                                                                                                   
- Shows who has each achievement, who is missing it, and group completion.                                                                                                                       
- Filters by completion state, specific missing players, search text, and plugin-provided fields.                                                                                                
- Keeps game/player configuration and achievement-list refreshes behind an admin secret.                                                                                                         
- Supports per-game plugins in `app/game_plugins`.

---

## Requirements

- Python 3.10+
- A Steam Web API key — get one at https://steamcommunity.com/dev/apikey
- Steam profiles for all tracked players must have **Profile** and **Game details** set to **Public** (Steam → Edit Profile → Privacy Settings)

---

## Setup

### Linux / macOS

```bash
cp .env.example .env
# edit .env with your values
python -m app.server
```

### Windows (PowerShell)

```powershell
Copy-Item .env.example .env
notepad .env
python -m app.server
```

Open `http://127.0.0.1:8765` in a browser.

---

## Docker

> Docker deployment is not yet implemented. This section is a placeholder.

---

## Configuration

All config lives in `.env` (auto-loaded on startup). Only `.env.example` is committed — never commit `.env`.

| Variable | Default | Description |
|---|---|---|
| `STEAM_API_KEY` | *(required)* | Steam Web API key. Needed for schema refreshes and player lookups. |
| `ADMIN_SECRET` | `change-me` | Secret for all write operations. The UI warns if it is still the default. |
| `HOST` | `127.0.0.1` | Bind address. Set to `0.0.0.0` to listen on all interfaces (e.g. for Docker or LAN access). |
| `PORT` | `8765` | Port the server listens on. |
| `PLAYER_REFRESH_TTL_SECONDS` | `600` | How old cached player data can be before a dashboard load triggers a background refresh. |

---

## Usage

### Admin access

Admin operations (adding/removing games and players, refreshing schemas) require the `ADMIN_SECRET`. Enter it in the Admin panel in the UI — it is stored in `sessionStorage` for one hour.

### Adding a game

1. Open the Admin panel, enter the Steam App ID and a display name.
2. Optionally select a plugin (see below).
3. After adding the game, click **Refresh Schema** to pull achievements from Steam.

### Adding players

Enter a SteamID64 (a 17-digit number) or a Steam vanity URL name. The display name is resolved automatically from Steam; you can override it.

### Dashboard

Select a game from the dropdown. The dashboard shows every achievement with:

- Who has it / who is missing it
- Group completion percentage
- Plugin-provided filter fields (heist, difficulty, etc.) when a plugin is attached

Filters: completion state, missing-player filter, text search, and any fields the plugin declares.

Player data refreshes automatically when stale (controlled by `PLAYER_REFRESH_TTL_SECONDS`). Force a refresh via **Refresh Players** in the Admin panel.

---

## Data & Storage

SQLite database at `data/achievement_tracker.sqlite3` — created automatically on first run. The `data/` directory is gitignored.

Schema tables:

| Table | Contents |
|---|---|
| `games` | Tracked games (app ID, name, plugin slug) |
| `players` | Tracked players (Steam ID, display name, avatar) |
| `achievements` | Achievement schema per game (pulled from Steam, enriched by plugin) |
| `player_achievements` | Per-player unlock state, cached locally |

---

## API Reference

All endpoints return JSON. Admin endpoints require the header `X-Admin-Secret: <your secret>`.

| Method | Path | Auth | Description |
|---|---|---|---|
| GET | `/api/health` | — | Server status, warns if admin secret is default |
| GET | `/api/games` | — | List tracked games |
| GET | `/api/players` | — | List tracked players |
| GET | `/api/plugins` | — | List available plugins |
| GET | `/api/games/<id>/dashboard` | — | Achievement dashboard for a game (`?refresh=false` to skip auto-refresh) |
| POST | `/api/games` | admin | Add a game (`app_id`, `name`, optional `plugin`) |
| POST | `/api/players` | admin | Add a player (`identifier`, optional `display_name`) |
| POST | `/api/games/<id>/refresh-schema` | admin | Re-pull achievement schema from Steam + run plugin enrichment |
| POST | `/api/games/<id>/refresh-players` | — | Force-refresh all player states for a game |
| DELETE | `/api/games/<id>` | admin | Remove a game and all its achievement data |
| DELETE | `/api/players/<id>` | admin | Remove a player |

---

## Plugin System

Plugins live in `app/game_plugins/<slug>.py`. Any `.py` file in that directory (that isn't `__init__.py`) is automatically discovered and available in the UI when adding a game.

A plugin is a plain Python module that exports the following:

```python
# Required module-level attributes
slug: str   # must match the filename without .py
label: str  # human-readable name shown in the UI

# Required functions
def fields() -> list[dict[str, str]]:
    """
    Declares which metadata fields this plugin provides.
    Each entry: {"key": "heist", "label": "Heist"}
    These keys appear as filter columns in the dashboard.
    """

def filter_config() -> dict[str, dict]:
    """
    Controls how each field is filtered in the UI.
    Keys match those returned by fields().

    Each value is a dict with:
      "type":  "exact"     — filter must match exactly
               "inclusive" — show achievements at or below the selected level
                             (useful for difficulty: selecting "Hard" shows Normal + Hard)
               "multi"     — achievement must match at least one selected value
                             (useful for heist lists where one achievement covers multiple)
      "order": list        — explicit sort order for filter values in the UI
               "alpha"     — sort alphabetically
    """

def enrich(api_name: str, current: dict) -> dict:
    """
    Called per achievement after enrich_all completes.
    Merge/override metadata. Return the updated dict.
    Ensure all declared field keys exist (set defaults here).
    """

def enrich_all(achievements: list[dict]) -> dict[str, dict]:
    """
    Called once during a schema refresh, receives the raw Steam achievement list.
    Return a dict mapping api_name → metadata dict.
    Use this for bulk operations: fetching an external source, parsing a local file, etc.
    enrich() is still called per-achievement after this.
    """
```

### Writing a plugin

1. Create `app/game_plugins/<your_slug>.py` implementing all four functions above.
2. The plugin appears automatically in the game-add UI on the next server start.
3. Assign the plugin to a game via the Admin panel (or update an existing game).
4. Click **Refresh Schema** on the game — this triggers `enrich_all` then `enrich` for every achievement.

Metadata returned by the plugin is stored as JSON in the `achievements.metadata` column and sent to the frontend via `/api/games/<id>/dashboard` under each achievement's `metadata` key.

The frontend renders one tag per field declared by `fields()` (using that field's `label`), pulling the value from `metadata[key]`. Metadata keys not declared in `fields()` are never shown — a plugin can freely stash extra working data in the metadata dict (e.g. a raw scraped description used only to derive other fields) without it leaking into the UI.

One key is given special generic handling, usable by any plugin that pulls data from an external source — it does not need to be declared in `fields()`:

- `source_label` — rendered as a tag reading `Source: <source_label>`, linking to `source_url` if also present (plain text tag otherwise).
- `source_url` — the URL the `source_label` tag links to.

### Manual metadata files

Plugins can load supporting data from `data/`. Any file in `data/` is gitignored by default — copy from a `.sample.*` if one exists. Manual data can serve as an override layer on top of auto-fetched data (see the Payday 2 plugin for an example).

### Plugin key-value store (`plugin_kv` table)

The database includes a general-purpose `plugin_kv` table for plugins that need to persist non-achievement-specific data between runs — for example, a list fetched from an external source during schema refresh that should be available on subsequent dashboard loads without re-fetching.

Schema: `(plugin_slug TEXT, key TEXT, value TEXT, updated_at TEXT)` — primary key on `(plugin_slug, key)`.

```python
from app.db import connect, utc_now

# Write
with connect() as conn:
    conn.execute(
        """
        INSERT INTO plugin_kv(plugin_slug, key, value, updated_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(plugin_slug, key) DO UPDATE SET
            value = excluded.value, updated_at = excluded.updated_at
        """,
        ("myplugin", "my_key", json.dumps(data), utc_now()),
    )

# Read
with connect() as conn:
    row = conn.execute(
        "SELECT value FROM plugin_kv WHERE plugin_slug = ? AND key = ?",
        ("myplugin", "my_key"),
    ).fetchone()
data = json.loads(row["value"]) if row else None
```

Store whatever the plugin needs during `enrich_all` (schema refresh) and read it back in `filter_config`, `fields`, or `enrich` on subsequent requests. Per-achievement data still goes into the `metadata` dict returned by `enrich_all`/`enrich`.

---

## Payday 2 Plugin

**App ID:** `218620`  
**Plugin slug:** `payday2`

### What it does

During a schema refresh the plugin:

1. Scans each Steam achievement's display name and description for heist names from `data/payday2_heists.json` (simple substring match, case-insensitive, tolerates leading "the").
2. Fetches approach (Stealth / Loud) and difficulty from the Payday 2 Fandom Wiki achievement descriptions.
3. Applies manual overrides from `data/payday2_metadata.json` (wins over wiki data).

This adds three filter fields to the dashboard: **Heists**, **Approach**, and **Difficulty**.

### Filter behaviour

| Field | Type | Behaviour |
|---|---|---|
| Heists | `multi` | Achievement shown if any of its heists match the selected filter; "Not heist specific" shows achievements with no heist |
| Approach | `exact` | Exact match (Stealth or Loud) |
| Difficulty | `inclusive` | Selecting a difficulty shows achievements at that level or below |

### Heist whitelist (`data/payday2_heists.json`)

Flat JSON array of heist names. The plugin matches each name as a substring of the Steam achievement text (case-insensitive, also tries without leading "the"). Update this file when new heists are added to the game.

```json
["The Big Bank", "Jewelry Store", "Crime Spree"]
```

### Manual metadata (`data/payday2_metadata.json`)

Copy `data/payday2_metadata.sample.json` to `data/payday2_metadata.json`. Keys are Steam achievement API names. Values override anything extracted from the wiki.

```json
{
  "bigbank_2": {
    "heist": ["The Big Bank"],
    "approach": "Stealth",
    "difficulty": "Death Wish"
  }
}
```

---

## Development

No build step. The frontend is vanilla JS/HTML/CSS in `public/`. The backend is pure Python stdlib in `app/`.

### Project layout

```
app/
  config.py          — env loading, paths, constants
  db.py              — SQLite schema, connection context manager
  steam.py           — Steam Web API calls
  service.py         — business logic (add/refresh/dashboard)
  server.py          — HTTP server, routing
  game_plugins/
    __init__.py      — plugin discovery and loading
    payday2.py       — Payday 2 plugin (wiki scrape + manual metadata)
data/
  achievement_tracker.sqlite3   — auto-created, gitignored
  payday2_metadata.json         — gitignored, copy from .sample.json
  payday2_heists.json           — whitelist for heist name validation
public/
  index.html / app.js / styles.css
```

### Running locally

```bash
python -m app.server
```

No install step, no virtual environment required (stdlib only).

### Adding a new game plugin

See [Plugin System](#plugin-system) above. Short version:

1. Create `app/game_plugins/<slug>.py` with `slug`, `label`, `fields()`, `filter_config()`, `enrich()`, `enrich_all()`.
2. Restart the server — the plugin is auto-discovered.
3. Assign to a game in the Admin panel, then refresh its schema.

All game-specific logic (heist categorisation, difficulty tiers, approach detection, external data fetches) belongs in the plugin file. The core app is game-agnostic.
