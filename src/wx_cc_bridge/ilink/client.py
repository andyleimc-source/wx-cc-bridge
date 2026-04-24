"""iLink (ClawBot) protocol client.

Protocol ref: https://github.com/hao-ji-xing/openclaw-weixin/blob/main/weixin-bot-api.md
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import struct
import uuid
from pathlib import Path
from typing import Any

import httpx

BASE_URL_DEFAULT = "https://ilinkai.weixin.qq.com"
CHANNEL_VERSION = "1.0.2"
LONGPOLL_TIMEOUT = 40.0  # server holds 35s, give HTTP a bit more


def _uin_header() -> str:
    # random uint32 → decimal string → base64 (matches openclaw-weixin 1.0.2 source)
    n = struct.unpack(">I", os.urandom(4))[0]
    return base64.b64encode(str(n).encode()).decode()


class ILinkClient:
    def __init__(
        self,
        bot_token: str | None = None,
        base_url: str = BASE_URL_DEFAULT,
    ):
        self.bot_token = bot_token
        self.base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(timeout=LONGPOLL_TIMEOUT)

    async def aclose(self) -> None:
        await self._client.aclose()

    def _headers(self) -> dict[str, str]:
        h = {
            "Content-Type": "application/json",
            "AuthorizationType": "ilink_bot_token",
            "X-WECHAT-UIN": _uin_header(),
        }
        if self.bot_token:
            h["Authorization"] = f"Bearer {self.bot_token}"
        return h

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> dict:
        r = await self._client.get(
            f"{self.base_url}/{path}", params=params, headers=self._headers()
        )
        r.raise_for_status()
        return r.json()

    async def _post(self, path: str, body: dict[str, Any]) -> dict:
        r = await self._client.post(
            f"{self.base_url}/{path}", json=body, headers=self._headers()
        )
        r.raise_for_status()
        return r.json()

    async def get_qrcode(self, bot_type: int = 3) -> dict:
        return await self._get("ilink/bot/get_bot_qrcode", {"bot_type": bot_type})

    async def get_qrcode_status(self, qrcode: str) -> dict:
        return await self._get("ilink/bot/get_qrcode_status", {"qrcode": qrcode})

    async def getupdates(self, cursor: str = "") -> dict:
        return await self._post(
            "ilink/bot/getupdates",
            {
                "get_updates_buf": cursor,
                "base_info": {"channel_version": CHANNEL_VERSION},
            },
        )

    async def send_text(self, to_user_id: str, context_token: str, text: str) -> dict:
        # Match x1ah/wechat-ilink-demo buildSendTextBody exactly.
        # from_user_id="" and client_id appear required for actual delivery —
        # without client_id the server silently accepts but doesn't deliver.
        return await self._post(
            "ilink/bot/sendmessage",
            {
                "msg": {
                    "from_user_id": "",
                    "to_user_id": to_user_id,
                    "client_id": f"wx-cc-bridge-{uuid.uuid4()}",
                    "message_type": 2,
                    "message_state": 2,
                    "context_token": context_token,
                    "item_list": [{"type": 1, "text_item": {"text": text}}],
                },
                "base_info": {"channel_version": CHANNEL_VERSION},
            },
        )


async def login(client: ILinkClient, token_path: Path) -> None:
    """Load cached token or run QR-code login flow."""
    if token_path.exists():
        data = json.loads(token_path.read_text())
        client.bot_token = data["bot_token"]
        if data.get("baseurl"):
            client.base_url = data["baseurl"].rstrip("/")
        print(f"[login] reuse token from {token_path}")
        return

    qr = await client.get_qrcode()
    qrcode_id = qr.get("qrcode") or qr.get("qrcode_str")
    login_url = qr.get("qrcode_img_content") or qr.get("qrcode_img")
    if not qrcode_id:
        raise RuntimeError(f"no qrcode in response: {qr}")
    # qrcode_img_content 实际是登录 URL (liteapp.weixin.qq.com/q/...)，不是 PNG。
    # 微信扫这个 URL 才能触发 ClawBot 登录握手；直接扫 qrcode (纯 hex id) 会打开落地页。
    qr_content = login_url or qrcode_id

    import qrcode as qrlib
    q = qrlib.QRCode(border=1)
    q.add_data(qr_content)
    q.make()
    q.print_ascii(invert=True)

    print(f"[login] 请用微信扫上方二维码登录 ClawBot")
    print(f"[login] (URL: {qr_content})")
    print(f"[login] (qrcode id: {qrcode_id})")

    last_dump = None
    while True:
        status = await client.get_qrcode_status(qrcode_id)
        st = status.get("status")
        # dump 每次状态变化（或前 3 次都打，便于观察）
        dump = json.dumps(status, ensure_ascii=False)
        if dump != last_dump:
            print(f"[login] status → {dump}")
            last_dump = dump
        if st == "confirmed" and status.get("bot_token"):
            client.bot_token = status["bot_token"]
            if status.get("baseurl"):
                client.base_url = status["baseurl"].rstrip("/")
            token_path.parent.mkdir(parents=True, exist_ok=True)
            token_path.write_text(
                json.dumps(
                    {"bot_token": client.bot_token, "baseurl": client.base_url},
                    ensure_ascii=False,
                )
            )
            print(f"[login] 登录成功，token 已保存到 {token_path}")
            return
        await asyncio.sleep(2)


def extract_text(msg: dict) -> str | None:
    """Best-effort text extraction; wrap may be flat or under 'msg'."""
    body = msg.get("msg") if isinstance(msg.get("msg"), dict) else msg
    for item in body.get("item_list") or []:
        if item.get("type") == 1:
            return (item.get("text_item") or {}).get("text")
    return None


def extract_meta(msg: dict) -> tuple[str | None, str | None]:
    """Return (from_user_id, context_token)."""
    body = msg.get("msg") if isinstance(msg.get("msg"), dict) else msg
    return body.get("from_user_id"), body.get("context_token")
