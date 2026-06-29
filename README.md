# Achievement Tracker

A local co-op achievement planning tool with a Python backend, SQLite storage, a browser UI, Steam integration, and per-game plugin hooks.

## Run

Copy the example environment file and fill in your real values:

```powershell
Copy-Item .env.example .env
notepad .env
python -m app.server
```

Open `http://127.0.0.1:8765`.

Steam player achievement data depends on the player's Steam privacy settings. Achievement schema refreshes require `STEAM_API_KEY`. Keep real secrets in `.env`; only `.env.example` should be committed.

## What it does

- Tracks multiple Steam games.
- Tracks any number of players by SteamID64 or vanity profile name.
- Pulls and caches the achievement list per game.
- Refreshes player completion state without re-pulling the achievement schema.
- Shows who has each achievement, who is missing it, and group completion.
- Filters by completion state, specific missing players, search text, and plugin-provided fields.
- Keeps game/player configuration and achievement-list refreshes behind an admin secret.
- Supports per-game plugins in `app/game_plugins`.

## Payday 2 plugin

Payday 2 uses Steam app id `218620`. The included plugin can add filters for `heist`, `approach`, and `difficulty` if metadata exists in:

```text
data/payday2_metadata.json
```

Use `data/payday2_metadata.sample.json` as the shape. The keys are Steam achievement API names.
