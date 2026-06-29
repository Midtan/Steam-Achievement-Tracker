from __future__ import annotations

import json
import mimetypes
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import service
from .config import ADMIN_SECRET, PUBLIC_DIR
from .db import init_db
from .game_plugins import available_plugins
from .steam import SteamError


class Handler(BaseHTTPRequestHandler):
    server_version = "AchievementTracker/0.1"

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)
        try:
            if path == "/api/health":
                self.json({"ok": True, "admin_secret_is_default": ADMIN_SECRET == "change-me"})
            elif path == "/api/games":
                self.json({"games": service.list_games()})
            elif path == "/api/players":
                self.json({"players": service.list_players()})
            elif path == "/api/plugins":
                self.json({"plugins": available_plugins()})
            elif path.startswith("/api/games/") and path.endswith("/dashboard"):
                game_id = int(path.split("/")[3])
                refresh = query.get("refresh", ["true"])[0] != "false"
                self.json(service.dashboard(game_id, refresh_stale=refresh))
            else:
                self.static(path)
        except Exception as exc:
            self.error(exc)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        try:
            if path == "/api/admin/verify":
                self.require_admin()
                self.json({"ok": True})
            elif path == "/api/games":
                self.require_admin()
                body = self.body()
                self.json({"game": service.add_game(int(body["app_id"]), body["name"], body.get("plugin", ""))})
            elif path == "/api/players":
                self.require_admin()
                body = self.body()
                self.json({"player": service.add_player(body["identifier"], body.get("display_name", ""))})
            elif path.startswith("/api/games/") and path.endswith("/refresh-schema"):
                self.require_admin()
                self.json(service.refresh_game_schema(int(path.split("/")[3])))
            elif path.startswith("/api/games/") and path.endswith("/refresh-players"):
                self.json(service.refresh_all_players(int(path.split("/")[3]), force=True))
            else:
                self.send_error(HTTPStatus.NOT_FOUND)
        except Exception as exc:
            self.error(exc)

    def do_DELETE(self) -> None:
        path = urlparse(self.path).path
        try:
            self.require_admin()
            parts = path.strip("/").split("/")
            if len(parts) == 3 and parts[:2] == ["api", "games"]:
                service.delete_game(int(parts[2]))
                self.json({"ok": True})
            elif len(parts) == 3 and parts[:2] == ["api", "players"]:
                service.delete_player(int(parts[2]))
                self.json({"ok": True})
            else:
                self.send_error(HTTPStatus.NOT_FOUND)
        except Exception as exc:
            self.error(exc)

    def body(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if length == 0:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def require_admin(self) -> None:
        if self.headers.get("X-Admin-Secret") != ADMIN_SECRET:
            raise PermissionError("Admin secret is missing or incorrect.")

    def json(self, value: object, status: HTTPStatus = HTTPStatus.OK) -> None:
        payload = json.dumps(value).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def static(self, path: str) -> None:
        if path in ("", "/"):
            path = "/index.html"
        candidate = (PUBLIC_DIR / path.lstrip("/")).resolve()
        if PUBLIC_DIR.resolve() not in [candidate, *candidate.parents] or not candidate.is_file():
            candidate = PUBLIC_DIR / "index.html"
        content_type = mimetypes.guess_type(str(candidate))[0] or "application/octet-stream"
        data = candidate.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def error(self, exc: Exception) -> None:
        if isinstance(exc, PermissionError):
            status = HTTPStatus.UNAUTHORIZED
        elif isinstance(exc, (KeyError, ValueError, SteamError)):
            status = HTTPStatus.BAD_REQUEST
        else:
            status = HTTPStatus.INTERNAL_SERVER_ERROR
        self.json({"error": str(exc)}, status)

    def log_message(self, format: str, *args: object) -> None:
        print(f"{self.address_string()} - {format % args}")


def main() -> None:
    init_db()
    address = ("127.0.0.1", 8765)
    httpd = ThreadingHTTPServer(address, Handler)
    print(f"Achievement Tracker running at http://{address[0]}:{address[1]}")
    if ADMIN_SECRET == "change-me":
        print("ADMIN_SECRET is using the default value 'change-me'. Set it before sharing the server.")
    httpd.serve_forever()


if __name__ == "__main__":
    main()
