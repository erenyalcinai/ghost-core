# Architecture

This document describes what is actually in the tree, module by module, including what is
unfinished. Aspirational text lives in [VISION.md](VISION.md); this file does not.

## 1. Topology: one brain, one worker, one limb

| Machine | Role | Always on? | What runs there |
|---|---|---|---|
| VPS (Linux) | **Brain** | yes | Telegram bridge, Cockpit API, sentinels (selfcheck, uptime, ports, logins, tunnel), memory DB, scheduler |
| Mac | **Worker** | no (sleeps) | Xcode builds, local repos, GUI automation, Full-Disk-Access reader, WDA relay to the phone, YubiKey gate |
| iPhone | **Limb** | Wi-Fi | WebDriverAgent runner; driven from the VPS through the Mac relay |

The VPS reaches the Mac through a reverse SSH tunnel the Mac keeps open (`ops/launchd/ai.jarvis.tunnel.plist`).
Two lanes exist on purpose:

- **Fast lane** (`ops/mac.py` ↔ `ops/mac_agent.py`): ~0.3 s round trip, single-threaded. Commands,
  screenshots, click/type/scroll (through a computer-use driver). A hung command blocks the lane,
  so long jobs go elsewhere.
- **Queue** (`ops/mac_cmd.py` ↔ `ops/mac_worker.py`): file-based job queue the worker drains every
  few seconds when awake. Survives sleep; 6-60 s latency.

A **baton** (`ghost/baton_policy.py`) decides which machine listens to Telegram. Rule: a gap is safe,
a collision is not; on any doubt the VPS listens.

## 2. Core package `ghost/` (46 modules, stdlib only)

Grouped by concern. Line counts are from this tree.

**Input and dispatch**
- `bus.py` (111) — event bus on SQLite; every input is one event type. *Status: written to, no central consumer yet; the real flow is still the bridge's own job queue.*
- `komutlar.py` (692) — command registry: slash commands and their handlers, moved out of the bridge.
- `cc_komutlar.py` (124) — discovers Claude Code skills/commands so the chat engine can use them.
- `intent.py` (102) — catches record-creating intents in free text.
- `webhook.py` (279) — inbound webhook receiver (token-gated, rate-limited) so the world can wake Ghost.
- `channels.py` (158) — output channels (Telegram, Cockpit, ntfy fallback) with a primary-channel switch.
- `notify.py` (73) — owner notification used by core modules.

**Memory and world model**
- `memory.py` (151) — SQLite (+FTS5) persistent memory; relevant entries are injected into every turn.
- `memsync.py` (47) — Mac↔VPS memory sync.
- `entities.py` (201) — entity graph (people, sites, apps, devices) with relations; seeded from the owner profile.
- `goals.py` (132) — standing goals the agent works on when idle.
- `continuity.py` (189) — notices a session about to end without writing memory, leaves a flag for the next one.
- `sahip.py` (128) — **owner profile loader**. All personal constants (name, sites, root cards, seed entities, ASR vocabulary, voice prompt facts, SSH fingerprints) come from `ops/sahip.json`; a broken file degrades to the example profile and raises a selfcheck warning instead of crashing imports.

**Governance and verification**
- `governance.py` (100) — action classes `read < reversible < permanent < spend`, regex classifier, audit log, `FREEZE` file.
- `verify.py` (196) — blind second-eye reviewer with veto. Triggered by rule (`needs_review`), not by the agent.
- `sandbox.py` (95) — time/process-group limited subprocess with scrubbed env. **No FS/network jail.**
- `transparency.py` (73) — weekly "what did Ghost do" report.

**Engines and quota**
- `engines.py` (107) — engine plurality: Claude primary, local Ollama fallback.
- `modeller.py` (271) — resolves an alias (opus/sonnet/haiku) to the newest id both the API and the installed CLI accept; warns when the CLI is stale.
- `kalici_oturum.py` (281) — one long-lived `claude` process across many turns (prompt-cache friendly).
- `chat_policy.py` (239) — when a chat session may be refreshed (never silently; owner-driven).
- `kesik_mesaj.py` (88) — re-queues a message that was interrupted by a bridge restart, exactly once.
- `kota.py` (244) — subscription quota as a percentage, read from the owner's own account.

**Cockpit (server side of the app)**
- `api.py` (593) — HTTP surface the app talks to (pairing, chat, stage, desk, senses, tasks, media, SSE).
- `kokpit.py` (661) — glue: outbox, devices, nonces, media.
- `apns.py` (236) — push notifications to the app.
- `sahne.py` (490) — **Stage**: server-drawn UI (tables, diffs, charts, forms, approval cards, live screens).
- `masa.py` (782) — **Desk**: shared persistent cards (notes, reports, files, sub-cards) with server-side permissions; the owner's items cannot be edited by the agent.
- `akis.py` (343) — timeline / chat event stream.
- `canli.py` (173) — live screen sources (Mac, iPhone) for the Stage.
- `gun.py` (166), `isnabzi.py` (94), `trafik.py` (159), `health.py` (314) — the Pulse page: what happened today, what is pending on the owner, how the business and the sites are doing, machine-readable health.

**Senses and hands**
- `duyular.py` (317) — permitted context from the owner's phone (location, calendar, battery), one line per turn, stale data dropped.
- `bolgeler.py` (196) — geofences: entering a region wakes the agent to *think*, not to print a canned line.
- `gorevler.py` (237) — queue of real iOS reminders/calendar events the agent writes to the owner's phone, with delivery confirmation ("queued" ≠ "written").
- `schedule.py` (214), `monitors.py` (165) — natural-language scheduling (Turkish patterns) and "watch this, tell me when it changes".

**Voice**
- `voice.py` (207) — spoken replies.
- `ses_ajani.py` (661) — the voice agent "Echo" (ElevenLabs Agents): answers from tools, forwards work to Ghost, never claims work is done; prompt facts come from the owner profile.

**Other**
- `email.py` (43) — inbound e-mail via a Cloudflare worker; outbound is stubbed (no key, refuses politely).
- `dersler.py` (82) — lessons extraction (see §5).
- `agents/` — five departments as YAML (engineering, intelligence, finance, earnings, operations); a department is a persona + tool set + routing keywords, not code. The earnings department points at the economy module, which is not in this tree; its two commands say so.
- `tools/registry.py` (111) — capability inventory.
- `config.py` (52) — single source for paths and `.env`/`secrets.env` loading.

## 3. Organs `ops/` (what runs unattended)

| Organ | Where | What it does |
|---|---|---|
| `telegram_bridge.py` (2,344) | VPS/Mac | async job manager: chat lane + 3-job parallel pool + approval gate; spawns `claude` as a child |
| `selfcheck.py` | both, every 15 min | is the bridge alive, is a job stuck, is the code on disk newer than the running process (renew when idle), is the CLI stale, is the owner profile intact; revives the bridge |
| `heartbeat.py` | both | every sentinel stamps a heartbeat; "silence = health" has a blind spot and this closes it |
| `uptime_watch.py` | VPS, 10 min | sites from the profile; strict-200 for pages where 404 means "content gone"; GET-only for endpoints that 501 on HEAD |
| `port_watch.py`, `giris_nobeti.py`, `tunnel_watch.py` | VPS | exposed ports, SSH logins from new places, half-open (zombie) tunnel sessions |
| `iphone_watch.py`, `wda_relay.py` | Mac | two silent ways the phone lane dies; Wi-Fi relay so USB is only for charging |
| `mac_agent.py`, `mac_worker.py`, `mac.py`, `mac_cmd.py` | Mac/VPS | fast lane and queue (see §1) |
| `mac_veri_okuyucu.py` (1,336), `mac_veri.py` | Mac/VPS | the one Full-Disk-Access process: notes, photos, messages, files, backups; localhost + token + log |
| `mac_anahtar.py` | Mac | YubiKey presence gate for all Mac access |
| `self_restart.sh`, `reload_bridge.sh`, `reboot_vps.sh`, `reboot_sonrasi.sh` | VPS | restart only when idle; after reboot, count the organs and report |
| `ders_yaz.py`, `weekly_synth.py` | VPS | lessons extraction; weekly synthesis job |
| `backup.py`, `mem_backup.py` | VPS | state backup; encrypted memory snapshot to a git branch |
| `launchd/*.plist`, `systemd/*` | — | the units that keep all of this running |

## 4. Governance state machine

```
read ──► reversible-write ──► permanent-write ──► spend
(auto)   (auto + audit)       (approval)          (approval + vault)
```

`/freeze` stops every level at once. Every autonomous action lands in `ops/audit.jsonl`. Weekly
transparency report goes to the owner. Autonomous (unattended) jobs run with an extra
`AUTONOMOUS_GUARD` restriction: nothing irreversible, nothing that spends.

The verifier (§2) sits *after* the work and *before* the report. Its three properties are the
whole point: triggered by rule, blind to the agent's reasoning, able to veto.

## 5. Knowledge flow

- Commit messages may carry a `Ders:` (lesson) block. `ops/ders_yaz.py` moves it into the lessons
  playbook automatically. The public, curated, English version is [LESSONS.md](LESSONS.md).
- The private tree also carries dossiers and daily intelligence briefings; those are personal and
  are not in this repository.
- The owner profile `ops/sahip.json` is the single place personal facts live. `ops/sahip.example.json`
  is the same file passed through the publishing scrubber.

## 6. Cockpit app (`kokpit/ios`)

One Swift source, two targets (`GhostKokpit` iOS, `GhostKokpitMac`), plus a share extension and
a widget. Generated with `xcodegen` from `project.yml`; secrets come from `Secrets.xcconfig`
(an `.example` is provided). Builds for the simulator and macOS without a team id. Views: chat with
structured turns and attachments, Stage, Desk, Pulse, sites, commands, decisions, camera, live
screens, settings, pairing, voice ("Konuş"). The HTTP protocol is documented in the `ghost/api.py` and `ghost/sahne.py` docstrings; the private tree's long cockpit worklog is not shipped.

## 7. Known limitations (kept honest)

- Sandbox has no filesystem or network isolation.
- Event bus has no central consumer; dispatch still lives in the bridge.
- Single owner, single Telegram id, Turkish UI.
- The Mac fast lane is single-threaded; one hung command blocks it (there is a sentinel for that).
- Outbound e-mail is stubbed.
- The economy/bounty module exists in the private tree and is excluded here; two commands say so when invoked.
