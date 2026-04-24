"""M1 echo bot — verifies iLink login + long-poll + send loop end-to-end."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

from .ilink.client import ILinkClient, extract_meta, extract_text, login

STATE_DIR = Path(os.environ.get("WX_CC_STATE", Path.home() / ".wx-cc-bridge"))
TOKEN_PATH = STATE_DIR / "token.json"
CURSOR_PATH = STATE_DIR / "cursor.txt"


def _load_cursor() -> str:
    if CURSOR_PATH.exists():
        return CURSOR_PATH.read_text().strip()
    return ""


def _save_cursor(cursor: str) -> None:
    CURSOR_PATH.parent.mkdir(parents=True, exist_ok=True)
    CURSOR_PATH.write_text(cursor)


async def main() -> None:
    client = ILinkClient()
    await login(client, TOKEN_PATH)

    cursor = _load_cursor()
    print(f"[poll] start, cursor={cursor!r}")

    while True:
        try:
            data = await client.getupdates(cursor)
        except Exception as e:
            print(f"[poll] error: {e!r}; retry in 2s")
            await asyncio.sleep(2)
            continue

        if "errcode" in data or "errmsg" in data:
            print(f"[poll] server error: {data}; retry in 2s")
            await asyncio.sleep(2)
            continue

        new_cursor = data.get("get_updates_buf")
        if new_cursor and new_cursor != cursor:
            cursor = new_cursor
            _save_cursor(cursor)

        for msg in data.get("msgs") or []:
            sender, ctx = extract_meta(msg)
            text = extract_text(msg)
            if not (sender and ctx and text):
                print(f"[msg] skipped (unparsed): {json.dumps(msg, ensure_ascii=False)[:500]}")
                continue
            print(f"[msg] {sender}: {text}")
            try:
                await client.send_text(sender, ctx, f"echo: {text}")
            except Exception as e:
                print(f"[send] error: {e!r}")


def run() -> None:
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[exit]")


if __name__ == "__main__":
    run()
