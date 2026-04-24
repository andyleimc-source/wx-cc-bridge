"""M2 bridge: WeChat ↔ Claude Code CLI.

收到消息 → /命令走 commands.handle → 否则 subprocess claude -p，带 session_id。
每个 chat_id 串行化；不同 chat 并发。
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

from . import claude_runner, commands
from .ilink.client import ILinkClient, extract_meta, extract_text, login
from .session_store import SessionStore

STATE_DIR = Path(os.environ.get("WX_CC_STATE", Path.home() / ".wx-cc-bridge"))
TOKEN_PATH = STATE_DIR / "token.json"
CURSOR_PATH = STATE_DIR / "cursor.txt"
SESSIONS_PATH = STATE_DIR / "sessions.json"
DEFAULT_WS_ROOT = Path(
    os.environ.get("WX_CC_WS_ROOT", Path.home() / "cc-wx-sessions")
)


def default_cwd_for(chat_id: str) -> str:
    safe = chat_id.replace("@", "_at_").replace("/", "_").replace(" ", "_")
    return str(DEFAULT_WS_ROOT / safe)


def _load_cursor() -> str:
    return CURSOR_PATH.read_text().strip() if CURSOR_PATH.exists() else ""


def _save_cursor(c: str) -> None:
    CURSOR_PATH.parent.mkdir(parents=True, exist_ok=True)
    CURSOR_PATH.write_text(c)


async def handle_message(
    chat_id: str,
    ctx_token: str,
    text: str,
    client: ILinkClient,
    store: SessionStore,
    locks: dict[str, asyncio.Lock],
) -> None:
    cmd_reply = await commands.handle(text, chat_id, store, default_cwd_for)
    if cmd_reply is not None:
        try:
            await client.send_text(chat_id, ctx_token, cmd_reply)
        except Exception as e:
            print(f"[send cmd-reply] error: {e!r}")
        return

    lock = locks.setdefault(chat_id, asyncio.Lock())
    async with lock:
        state = store.get(chat_id)
        cwd = Path(state.get("cwd") or default_cwd_for(chat_id))
        session_id = state.get("session_id")

        print(f"[claude→] {chat_id} cwd={cwd} sid={session_id}")
        t0 = asyncio.get_event_loop().time()
        result = await claude_runner.ask(text, cwd=cwd, session_id=session_id)
        dt = asyncio.get_event_loop().time() - t0
        print(
            f"[claude←] {dt:.1f}s "
            f"sid={result.session_id} err={bool(result.error)} "
            f"text_len={len(result.text)}"
        )

        if result.error:
            reply = f"[Claude 出错] {result.error[:800]}"
            print(f"[claude ERR] {result.error[:500]}")
        else:
            reply = result.text or "(Claude 回了空)"
            if result.session_id and result.session_id != session_id:
                store.set_session(chat_id, result.session_id)

        try:
            resp = await client.send_text(chat_id, ctx_token, reply)
            print(f"[send←] resp={resp} ({len(reply)} chars sent)")
        except Exception as e:
            print(f"[send EXC] {e!r}")


async def main() -> None:
    client = ILinkClient()
    await login(client, TOKEN_PATH)

    store = SessionStore(SESSIONS_PATH)
    locks: dict[str, asyncio.Lock] = {}
    cursor = _load_cursor()
    print(f"[bridge] start, cursor={cursor!r}, ws_root={DEFAULT_WS_ROOT}")

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
            sender, ctx_token = extract_meta(msg)
            text = extract_text(msg)
            if not (sender and ctx_token and text):
                print(f"[msg] skipped: {json.dumps(msg, ensure_ascii=False)[:400]}")
                continue
            print(f"[msg] {sender}: {text[:120]}")
            # dispatch without blocking the poll loop; per-chat lock serializes
            asyncio.create_task(
                handle_message(sender, ctx_token, text, client, store, locks)
            )


def run() -> None:
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[exit]")


if __name__ == "__main__":
    run()
