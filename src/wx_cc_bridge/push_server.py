"""HTTP push endpoint: lets local services (e.g. ailei timers) ask the bridge
to send a WeChat message to a known chat_id.

Binds 127.0.0.1 only. Bearer token auth via ``WX_BRIDGE_PUSH_TOKEN``.

  POST /push   {"chat_id": "<wxid>", "text": "..."}   → 200 {"ok": true}
  GET  /health                                        → 200 {"ok": true} (no auth)

Sending requires a cached ``context_token`` from a prior incoming message
in that chat. If none is cached, returns 412 with a hint.

Implemented with stdlib asyncio (no extra deps). HTTP/1.0, connection: close,
single request per connection — sufficient for an internal control plane.
"""
from __future__ import annotations

import asyncio
import json
from typing import Awaitable, Callable

from .ilink.client import ILinkClient
from .session_store import SessionStore

PushHandler = Callable[[str, str], Awaitable[dict]]


def _resp(status: int, body: dict) -> bytes:
    payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
    head = (
        f"HTTP/1.0 {status} {_reason(status)}\r\n"
        f"Content-Type: application/json; charset=utf-8\r\n"
        f"Content-Length: {len(payload)}\r\n"
        f"Connection: close\r\n\r\n"
    ).encode("ascii")
    return head + payload


def _reason(status: int) -> str:
    return {
        200: "OK",
        400: "Bad Request",
        401: "Unauthorized",
        404: "Not Found",
        405: "Method Not Allowed",
        412: "Precondition Failed",
        500: "Internal Server Error",
    }.get(status, "OK")


async def _read_request(reader: asyncio.StreamReader) -> tuple[str, str, dict, bytes]:
    """Returns (method, path, headers, body). Raises on malformed input."""
    request_line = await asyncio.wait_for(reader.readline(), timeout=5)
    if not request_line:
        raise ValueError("empty request")
    parts = request_line.decode("iso-8859-1").rstrip("\r\n").split(" ")
    if len(parts) < 2:
        raise ValueError("bad request line")
    method, path = parts[0], parts[1]

    headers: dict[str, str] = {}
    while True:
        line = await asyncio.wait_for(reader.readline(), timeout=5)
        if line in (b"\r\n", b"\n", b""):
            break
        k, _, v = line.decode("iso-8859-1").rstrip("\r\n").partition(":")
        headers[k.strip().lower()] = v.strip()

    length = int(headers.get("content-length", "0") or 0)
    body = b""
    if length > 0:
        body = await asyncio.wait_for(reader.readexactly(length), timeout=10)
    return method, path, headers, body


def make_handler(
    client: ILinkClient,
    store: SessionStore,
    push_token: str,
) -> Callable[[asyncio.StreamReader, asyncio.StreamWriter], Awaitable[None]]:
    async def handle(
        reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            try:
                method, path, headers, body = await _read_request(reader)
            except Exception as e:
                writer.write(_resp(400, {"error": f"bad request: {e!r}"}))
                await writer.drain()
                return

            if path == "/health" and method == "GET":
                writer.write(_resp(200, {"ok": True}))
                await writer.drain()
                return

            auth = headers.get("authorization", "")
            if not auth.startswith("Bearer ") or auth[7:] != push_token:
                writer.write(_resp(401, {"error": "unauthorized"}))
                await writer.drain()
                return

            if path != "/push":
                writer.write(_resp(404, {"error": "not found"}))
                await writer.drain()
                return
            if method != "POST":
                writer.write(_resp(405, {"error": "POST only"}))
                await writer.drain()
                return

            try:
                payload = json.loads(body.decode("utf-8"))
            except Exception:
                writer.write(_resp(400, {"error": "invalid json"}))
                await writer.drain()
                return

            chat_id = (payload.get("chat_id") or "").strip()
            text = payload.get("text") or ""
            if not chat_id or not text:
                writer.write(_resp(400, {"error": "chat_id and text required"}))
                await writer.drain()
                return

            ctx_token = store.get_ctx_token(chat_id)
            if not ctx_token:
                writer.write(
                    _resp(
                        412,
                        {
                            "error": "no cached context_token; this chat must "
                            "ping the bot first so we can capture a token"
                        },
                    )
                )
                await writer.drain()
                return

            try:
                resp = await client.send_text(chat_id, ctx_token, text)
            except Exception as e:
                print(f"[push] send error: {e!r}")
                writer.write(_resp(500, {"error": f"send failed: {e!r}"}))
                await writer.drain()
                return

            print(f"[push→] {chat_id} ({len(text)} chars) resp={resp}")
            writer.write(_resp(200, {"ok": True}))
            await writer.drain()
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    return handle


async def serve(
    client: ILinkClient,
    store: SessionStore,
    push_token: str,
    host: str = "127.0.0.1",
    port: int = 8787,
) -> asyncio.AbstractServer:
    handler = make_handler(client, store, push_token)
    server = await asyncio.start_server(handler, host, port)
    print(f"[push] listening on {host}:{port}")
    return server
