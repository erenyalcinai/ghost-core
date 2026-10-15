# Quickstart

Goal: the core running on a Linux box in about 20 minutes — Telegram bridge, Claude as the
engine, memory, governance, sentinels. The Mac/iPhone limbs and the Cockpit app are separate
optional steps at the end.

## 0. Requirements

- Linux (Ubuntu 22.04+ tested), Python **3.12+**, `git`. No `pip install` is needed: the core
  uses only the standard library.
- [Claude Code CLI](https://docs.claude.com/en/docs/claude-code) installed and logged in
  (`claude --version` works). The bridge spawns it as a child process; a Claude subscription
  is what pays for the thinking.
- A Telegram bot token from @BotFather and your own numeric Telegram user id.

## 1. Clone and test

Clone into `~/jarvis`: the launchd/systemd units and a few scripts assume that checkout path
(it is the historical name of the private tree).

```bash
git clone https://github.com/erenyalcinai/ghost-core ~/jarvis && cd ~/jarvis
python3 -m unittest discover -s tests      # 508 tests, ~18 s, no network, no secrets
```

If this is green, the core is intact on your machine.

## 2. Owner profile

```bash
cp ops/sahip.example.json ops/sahip.json
```

Edit it: your name, the sites you want watched (`izle`), which of them must return 200
(`strict200`), root cards for the Desk, seed entities. Everything personal lives here and
only here; the code reads it through `ghost/sahip.py`. A broken file will not crash the
bridge — selfcheck will tell you it fell back to the example.

## 3. Secrets

Create `.env` in the repository root (it is git-ignored):

```
TELEGRAM_BOT_TOKEN=123456:ABC...
TELEGRAM_OWNER_ID=12345678
CLAUDE_BIN=/home/you/.local/bin/claude
```

Only the owner id can talk to the bot; everyone else is ignored. Optional keys (all off when
absent): `NTFY_TOPIC` (fallback channel), `GHOST_API_PORT` (Cockpit API, default 8790),
`POSTHOG_*`, `CLOUDFLARE_*`, `ELEVENLABS_*` for the respective organs.

## 4. Run the bridge

```bash
python3 ops/telegram_bridge.py
```

You should see `köprü(v3.0/vps) başladı` in `ops/bridge.log`, and the bot answers `/help`,
`/durum`, `/kota`. Send it a sentence; it runs as a Claude Code session with the repository
as its working directory, so `CLAUDE.md` is its operating context — start from
`CLAUDE.md.example`.

To keep it running, install it as a user service:

```bash
mkdir -p ~/.config/systemd/user
sed "s|/home/ghost/jarvis|$PWD|g" ops/systemd/ghost-api.service > ~/.config/systemd/user/ghost-api.service
# write a ghost-bridge.service the same way pointing at ops/telegram_bridge.py
systemctl --user daemon-reload && systemctl --user enable --now ghost-bridge ghost-api
```

Add `ops/selfcheck.py` on a 15-minute timer; it revives the bridge, renews it when the code
on disk is newer *and the bridge is idle*, and warns about stuck jobs, stale CLI and a broken
profile. **Never restart the bridge synchronously from inside a chat turn** — the session is a
child of the bridge and the answer dies with it. Use `ops/self_restart.sh`, which waits for idle.

## 5. What you have now

- Chat and jobs over Telegram, 3 parallel background jobs, approval gate for permanent/spend actions,
  `/freeze`.
- Memory (`ops/ghost.db`), entity graph, goals, scheduler, monitors, webhooks (behind nginx — see
  `ops/nginx/ghost-hooks`).
- Sentinels: selfcheck, heartbeat, uptime, ports, SSH logins.
- Lessons extraction from commit messages (`ops/ders_yaz.py`, hook it to `post-commit` or a timer).

## 6. Optional: Mac worker and iPhone limb

Requires your own Mac (Apple Silicon tested), Xcode, and an iPhone you own.

1. On the Mac, clone the repo to `~/jarvis` and copy the same `.env`.
2. Install the launchd agents from `ops/launchd/` (`launchctl bootstrap gui/$(id -u) <plist>`):
   `tunnel` (reverse SSH to the VPS, keeps the fast lane open), `macbridge`/`macworker` (queue),
   `maclink` (fast-lane agent), `selfcheck`.
3. Full-Disk-Access reader: build `ops/mac_ghost_veri.c`, grant it FDA in System Settings, and run it
   from a launchd agent of your own (label it `ai.jarvis.veri`; no plist is shipped for it). It binds to
   `127.0.0.1:8766` with a token and logs every read.
4. YubiKey gate (optional but recommended): `ops/mac_anahtar.py` — write your key's serial to
   `~/.ghost/anahtar_seri`; when the key is absent every Mac path returns 423 "locked".
5. iPhone: install WebDriverAgent with Xcode, enable wireless pairing, run `ops/wda_relay.py` as
   `ai.jarvis.wdarelay`. From the VPS, `python3 ops/phone.py durum` should answer.

## 7. Optional: Cockpit app

```bash
cd kokpit/ios
cp Secrets.xcconfig.example Secrets.xcconfig     # API base URL + pairing secret
xcodegen generate
xcodebuild -project GhostKokpit.xcodeproj -scheme GhostKokpit \
  -destination 'generic/platform=iOS Simulator' CODE_SIGNING_ALLOWED=NO build
xcodebuild -project GhostKokpit.xcodeproj -scheme GhostKokpitMac \
  -destination 'platform=macOS,arch=arm64' CODE_SIGNING_ALLOWED=NO build
```

Both build without a team id. To install on a device, set `DEVELOPMENT_TEAM` in `project.yml`, change
the keychain access group prefix in `GhostKokpit/Net/Keychain.swift` to your team id, set `KOKPIT_UDID`,
and use `./build_install.sh`. Pair the app with the server once (QR/secret), then the Stage,
Desk, Pulse and chat work; push notifications need your own APNs key.

## 8. Not shipped

The private tree also has a daily briefing script, product health checks, mail and chat-history
readers, an economy module and the Claude Code skills the `/arastir`, `/denetim`, `/istihbarat`
commands route to. Those commands answer with a short "not in this installation" message.
The `kazanc` department in `ghost/agents/departments.yaml` refers to the same excluded module.

## 9. Language

The chat, commands and UI are Turkish. `/help` lists the commands; `playbooks/komutlar.md` explains
them. The agent itself answers in whatever language you write to it.
