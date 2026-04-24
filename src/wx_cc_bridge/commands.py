"""Slash-command handlers: /new /clear /pwd /cd /history /resume /help.

Dispatcher returns str reply if the message is a command, None otherwise.
"""
from __future__ import annotations

import datetime
import json
import re
from pathlib import Path
from typing import Callable

from .session_store import SessionStore

HELP = (
    "/new         新对话（忘掉当前 session）\n"
    "/clear       同 /new\n"
    "/pwd         当前工作目录\n"
    "/cd <path>   切目录（持久化，开新对话）\n"
    "/history     最近 5 轮历史对话\n"
    "/resume <n>  切回第 n 个历史对话\n"
    "/help        本帮助"
)


def _encode_cwd(cwd: str) -> str:
    """Claude Code's project dir naming: non-[A-Za-z0-9-] chars → '-'.

    Observed: `_` and `.` also get replaced, not just `/`.
    """
    return re.sub(r"[^A-Za-z0-9-]", "-", cwd)


def _first_user_text(jsonl_path: Path) -> str:
    """Read first user message from a session jsonl for summary."""
    try:
        for line in jsonl_path.read_text(errors="replace").splitlines():
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if obj.get("type") != "user":
                continue
            msg = obj.get("message") or {}
            content = msg.get("content")
            if isinstance(content, str):
                return content.strip()
            if isinstance(content, list):
                for c in content:
                    if isinstance(c, dict) and c.get("type") == "text":
                        return (c.get("text") or "").strip()
    except Exception:
        pass
    return "(无摘要)"


def list_history(cwd: str, limit: int = 5) -> list[dict]:
    """List recent sessions for a given cwd, newest first.

    Reads ~/.claude/projects/<encoded-cwd>/*.jsonl.
    """
    proj_dir = Path.home() / ".claude" / "projects" / _encode_cwd(cwd)
    if not proj_dir.exists():
        return []
    files = sorted(
        proj_dir.glob("*.jsonl"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )[:limit]
    out = []
    for f in files:
        summary = _first_user_text(f)
        if len(summary) > 40:
            summary = summary[:40] + "…"
        out.append(
            {
                "session_id": f.stem,
                "summary": summary,
                "mtime": f.stat().st_mtime,
            }
        )
    return out


async def handle(
    text: str,
    chat_id: str,
    store: SessionStore,
    default_cwd_fn: Callable[[str], str],
) -> str | None:
    """Return reply text if `text` is a /command, else None."""
    if not text.startswith("/"):
        return None

    parts = text.strip().split(maxsplit=1)
    cmd = parts[0]
    arg = parts[1].strip() if len(parts) > 1 else ""

    state = store.get(chat_id)
    cwd = state.get("cwd") or default_cwd_fn(chat_id)

    if cmd in ("/new", "/clear"):
        store.clear_session(chat_id)
        return "✓ 已开启新对话"

    if cmd == "/pwd":
        return cwd

    if cmd == "/cd":
        if not arg:
            return "用法：/cd <路径>"
        try:
            new_cwd = str(Path(arg).expanduser().resolve())
        except Exception as e:
            return f"路径无效：{e}"
        Path(new_cwd).mkdir(parents=True, exist_ok=True)
        store.set_cwd(chat_id, new_cwd)
        store.clear_session(chat_id)
        return f"✓ 已切到 {new_cwd}\n（新对话开始）"

    if cmd == "/history":
        items = list_history(cwd)
        if not items:
            return f"({cwd} 下暂无历史对话)"
        lines = [f"最近 {len(items)} 轮对话（{cwd}）："]
        for i, it in enumerate(items, 1):
            dt = datetime.datetime.fromtimestamp(it["mtime"]).strftime("%m-%d %H:%M")
            lines.append(f"{i}. [{dt}] {it['summary']}")
        lines.append("\n用 /resume <n> 切回")
        return "\n".join(lines)

    if cmd == "/resume":
        if not arg.isdigit():
            return "用法：/resume <序号>"
        idx = int(arg) - 1
        items = list_history(cwd)
        if idx < 0 or idx >= len(items):
            return f"序号超范围（当前 1-{len(items)}）"
        chosen = items[idx]
        store.set_session(chat_id, chosen["session_id"])
        return f"✓ 已切回对话：{chosen['summary']}"

    if cmd == "/help":
        return HELP

    return f"未知命令 {cmd}。/help 查看所有命令"
