"""Per-chat state persistence: session_id, cwd.

Shape of sessions.json:
{
  "<chat_id>": {
    "session_id": "abc-123",
    "cwd": "/Users/andy/cc-wx-sessions/xxx",
    "updated_at": "2026-04-24T12:00:00"
  }
}
"""
from __future__ import annotations

import datetime
import json
from pathlib import Path


class SessionStore:
    def __init__(self, path: Path):
        self.path = path
        self._data: dict[str, dict] = {}
        if path.exists():
            try:
                self._data = json.loads(path.read_text())
            except Exception:
                self._data = {}

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self._data, ensure_ascii=False, indent=2))

    def _touch(self, chat_id: str) -> dict:
        rec = self._data.setdefault(chat_id, {})
        rec["updated_at"] = datetime.datetime.now().isoformat(timespec="seconds")
        return rec

    def get(self, chat_id: str) -> dict:
        return self._data.get(chat_id, {})

    def set_session(self, chat_id: str, session_id: str) -> None:
        self._touch(chat_id)["session_id"] = session_id
        self._save()

    def clear_session(self, chat_id: str) -> None:
        rec = self._data.get(chat_id)
        if rec and "session_id" in rec:
            rec.pop("session_id")
            self._touch(chat_id)
            self._save()

    def set_cwd(self, chat_id: str, cwd: str) -> None:
        self._touch(chat_id)["cwd"] = cwd
        self._save()

    def set_ctx_token(self, chat_id: str, ctx_token: str) -> None:
        self._touch(chat_id)["ctx_token"] = ctx_token
        self._save()

    def get_ctx_token(self, chat_id: str) -> str | None:
        return self._data.get(chat_id, {}).get("ctx_token")
