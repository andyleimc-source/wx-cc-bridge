# wx-cc-bridge

WeChat ↔ Claude Code CLI bridge. Talk to Claude Code from your personal WeChat account through the official ClawBot / iLink protocol, using your Claude Max subscription (no API billing).

```
WeChat user → iLink long-poll (ilinkai.weixin.qq.com)
           → this bridge (Python / FastAPI-free, asyncio)
           → subprocess: claude -p "…" --resume <session_id>
           → reply back via sendmessage
```

## Why

- **No API key required** — runs the `claude` CLI against your Max subscription.
- **Per-chat isolation** — each WeChat contact gets its own `cwd` and Claude session.
- **Slash commands** — `/new`, `/history`, `/resume`, `/cd`, `/pwd`, `/help`.

## Status

- **M1**: iLink integration (login, long-poll, send) — working
- **M2**: Claude CLI integration + slash commands — working
- **M3**: Hardening (token auth, bash-tool denylist, reply chunking) — not yet

## Requirements

- macOS (launchd deployment uses Apple-specific bits; core Python works anywhere)
- Python 3.11+
- [`uv`](https://github.com/astral-sh/uv)
- `claude` CLI installed and logged in with a Max subscription — **must not** be configured to hit the Anthropic API
- A WeChat account that can register as a ClawBot (this is Tencent-side; not every account can)

## Install

```bash
git clone https://github.com/<you>/wx-cc-bridge.git
cd wx-cc-bridge
make sync                    # uv-based deps + editable install
```

## Run (foreground, for first-time login)

```bash
make run
```

A QR code prints in the terminal. Scan it with the WeChat account you want to use as the bot (that account will act as the bridge endpoint). Login persists in `~/.wx-cc-bridge/token.json`.

Send any message from a second WeChat account to the bot account and you should see `[msg] …` in the log and a Claude reply back in WeChat.

## Run as a background service (macOS launchd)

```bash
make install-service         # one-time
make status                  # PID / exit code
make logs                    # tail stdout+stderr
make restart-service
make uninstall-service
```

Logs: `~/Library/Logs/wx-cc-bridge/`. Auto-restarts on crash; survives logout/login; **does not** survive Mac sleep/shutdown.

## Commands (in WeChat)

| Command        | Effect                                      |
| -------------- | ------------------------------------------- |
| `/help`        | List commands                               |
| `/pwd`         | Show current workspace for this chat        |
| `/cd <path>`   | Switch workspace (persists, opens new session) |
| `/new`, `/clear` | Start a fresh Claude session              |
| `/history`     | List recent Claude sessions in this workspace |
| `/resume <n>`  | Resume session by number from `/history`    |

Anything that doesn't start with `/` is forwarded to `claude -p` in the chat's current workspace, resuming its session.

## Layout

```
src/wx_cc_bridge/
  ilink/client.py     # QR login, long-poll, send — iLink protocol
  claude_runner.py    # async subprocess wrapper around `claude -p`
  session_store.py    # per-chat state (session_id, cwd) in JSON
  commands.py         # slash-command dispatcher
  bridge.py           # main loop: poll → dispatch → reply
  echo.py             # M1-era echo bot, handy for iLink protocol debugging
scripts/
  wx-cc-bridge.plist.template  # launchd service template
```

Runtime state:

```
~/.wx-cc-bridge/
  token.json       # iLink bot_token + baseurl (sensitive — keep off GitHub)
  cursor.txt       # long-poll cursor
  sessions.json    # chat_id → {session_id, cwd}
~/cc-wx-sessions/{safe_chat_id}/  # per-chat workspace (Claude's cwd)
```

## Protocol gotchas (learned the hard way)

None of the following are in the public `weixin-bot-api.md` — reverse-engineered from [x1ah/wechat-ilink-demo](https://github.com/x1ah/wechat-ilink-demo)'s `bot.mjs`:

1. **QR code encoding** — the `qrcode` field in `get_bot_qrcode` response is an opaque id (scanning it opens a landing page). The actual login URL is in `qrcode_img_content` (despite its name, it's a URL string, not image bytes). Render *that* as a QR.
2. **`AuthorizationType: ilink_bot_token` header** — required on every request after login. Without it the server returns `{errcode: -14, errmsg: "session timeout"}` on every `getupdates` call.
3. **`X-WECHAT-UIN` encoding** — `base64(str(random_uint32).encode())`, i.e. the uint32 rendered as a decimal string, then base64. *Not* `base64(4_random_bytes)`.
4. **`sendmessage` needs `from_user_id: ""` and `client_id: <uuid>`** — the server silently accepts messages missing these and returns `{}`, but never delivers them if the reply takes more than ~1s. Quick echoes work, Claude replies don't.
5. **Claude Code's project dir encoding** at `~/.claude/projects/` replaces all non-`[A-Za-z0-9-]` characters with `-`, not just `/`. So `/Users/foo/_bar` becomes `-Users-foo--bar`.

## Security

The default workspace for each chat is `~/cc-wx-sessions/{safe_chat_id}/`, *not* `$HOME`, to avoid exposing dotfiles (`.ssh`, `.aws`, etc.) to `/cd` accidents or a compromised WeChat account.

Claude runs with `--permission-mode bypassPermissions`. Once you `/cd` out of the sandbox, Claude has read/write/delete on whatever you point it at. Don't point it at `~` unless you understand that implication. A future milestone adds a `--disallowedTools` baseline (no `Bash(rm*)` etc.).

Nothing here exposes a public port — this bridge only *makes* outbound long-poll requests.

## License

MIT — see `LICENSE`.
