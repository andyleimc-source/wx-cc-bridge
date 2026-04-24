"""Wrap `claude -p` CLI as an async function.

Goes through the subscribed CLI binary — must NOT use the Anthropic API.
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path

CLAUDE_BIN = "claude"


@dataclass
class ClaudeResult:
    text: str
    session_id: str
    error: str | None = None


async def ask(
    prompt: str,
    cwd: Path,
    session_id: str | None = None,
    timeout: float = 300.0,
) -> ClaudeResult:
    """Invoke `claude -p` in the given cwd, optionally resuming a session.

    Returns the final text + the (possibly new) session_id.
    """
    cwd.mkdir(parents=True, exist_ok=True)

    args = [
        CLAUDE_BIN,
        "-p",
        prompt,
        "--output-format",
        "json",
        "--permission-mode",
        "bypassPermissions",
    ]
    if session_id:
        args += ["--resume", session_id]

    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            cwd=str(cwd),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        return ClaudeResult(
            "", session_id or "", error=f"未找到 `{CLAUDE_BIN}` 命令，检查 PATH"
        )

    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        return ClaudeResult("", session_id or "", error=f"超时 {timeout}s")

    if proc.returncode != 0:
        return ClaudeResult(
            "",
            session_id or "",
            error=f"exit={proc.returncode}: {stderr.decode(errors='replace')[:1500]}",
        )

    raw = stdout.decode(errors="replace").strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        return ClaudeResult(
            "", session_id or "", error=f"JSON parse: {e}; raw head: {raw[:400]!r}"
        )

    if isinstance(data, dict) and data.get("is_error"):
        return ClaudeResult(
            "", data.get("session_id") or session_id or "", error=data.get("result", raw[:500])
        )

    return ClaudeResult(
        text=(data.get("result") or "").strip(),
        session_id=data.get("session_id") or session_id or "",
    )
