# Ghost — a one-person autonomous institution, running in production

Ghost is a personal AI infrastructure that runs 24/7 for one owner. It is not a chat
window and not a single coding session. It is a small institution: a brain on a VPS,
a Mac it uses as a worker, an iPhone it physically controls, a Telegram bridge, a
native iOS/macOS cockpit app, a voice agent, a persistent memory, a governance layer
with approval gates, and a blind second-eye verifier that can veto its own reports.

This repository is the **open core** of that system. It was built by one engineer and
the agent itself over 65 days, in production, with real failures. The lessons those
failures produced are in [LESSONS.md](LESSONS.md); that file is the most honest thing
here and probably the most useful.

> **Status:** reference implementation, runnable, single-owner. Not a product, no
> support. See [What this is not](#what-this-is-not).

## Numbers

| | |
|---|---|
| Core Python (`ghost/`) | ~10,800 lines, 46 modules, **zero third-party dependencies** (two optional: PyJWT for APNs, websockets for the voice test client) |
| Organs (`ops/`) | ~9,600 lines: bridge, sentinels, Mac and iPhone limbs |
| Cockpit app (`kokpit/ios`) | ~7,800 lines Swift, iOS + macOS targets, one source |
| Tests | 518, run in ~20 s with `python -m unittest`, CI on every push |
| Scheduling units | 9 launchd agents (Mac) + 3 systemd units (VPS) in this tree |
| Lessons | 170 operational lessons, auto-extracted from commit messages, curated in [LESSONS.md](LESSONS.md) |
| History | 65 days, 680+ commits in the private tree, ~83% authored by the agent under the owner's direction |

## What makes it different

**It keeps itself safe in production, not in a demo.**
- **Action classes** (`ghost/governance.py`): every action is classified read / reversible /
  permanent / spend. Permanent and spend actions require explicit approval; everything
  autonomous is written to an audit log; a `FREEZE` file stops all autonomy instantly.
- **Blind second eye** (`ghost/verify.py`): when a job claims "done", a separate reviewer
  that *cannot see the agent's reasoning* reads only the owner's order and the raw result.
  A "missing" or "wrong" verdict vetoes the ✅. The agent does not decide when it is reviewed.
- **Physical kill switch** (`ops/mac_anahtar.py`): a YubiKey plugged into the Mac is a hardware
  button for the agent's access to the Mac. Pull it and every Mac path returns "locked".
- **Self-renewal that does not kill the reply** (`ops/self_restart.sh`, `ghost/kesik_mesaj.py`):
  the chat session is a child of the bridge process, so a naive restart kills the answer
  mid-flight. Restarts wait for idle; an interrupted message is re-queued exactly once.
- **Lessons pipeline** (`ops/ders_yaz.py`): a `Ders:` block in a commit message is extracted
  into a playbook automatically. Knowledge is written where it is produced and moved to
  where it is read, without anyone remembering to do it.

**It has hands, not just an API.**
- **Mac as a worker**: a fast lane (reverse SSH tunnel, ~0.3 s round trip) for commands,
  screenshots and GUI actions; a queue for when the Mac sleeps; a Full-Disk-Access reader
  that is the *only* process allowed to touch private data, bound to localhost with a token
  and a log.
- **iPhone as a limb**: WebDriverAgent over Wi-Fi through a relay, so the phone needs USB
  only for charging. Coordinate spaces (points vs pixels) are handled explicitly because
  getting them wrong taps outside the screen.
- **Cockpit**: a native iOS + macOS app with a chat, a server-drawn "Stage" (tables, diffs,
  charts, live screens, approval cards), a "Pulse" status page that answers three questions
  (what did Ghost do today, what is it waiting on me for, how is the business), and a shared
  persistent "Desk" of cards. The app can also write real iOS reminders and calendar events
  on the owner's phone, and geofences can wake the agent.

**It remembers.** SQLite + FTS5 memory injected into every turn, an entity graph, standing
goals the agent works on when idle, and continuity hooks that notice when a session is
about to end without writing anything down.

**It is multi-engine.** Claude is primary; a local Ollama model is the fallback so the chat
survives an API outage. Model aliases resolve to the newest model the API *and* the CLI both
know, because a stale CLI silently rejects new model ids.

## Architecture in one picture

```
  CHANNELS      Telegram · Cockpit app (iOS/macOS) · voice agent · webhooks · e-mail (in)
      │
  CORE  ghost/  event bus · command registry · governance + audit + freeze · blind verifier
                memory (SQLite+FTS) · entities · goals · scheduler · monitors · engines
                stage / desk / pulse (server-drawn UI) · senses (location, calendar, battery)
      │
  ORGANS ops/   telegram bridge · selfcheck · heartbeat · uptime · port · login sentinels
                Mac fast lane + queue + FDA reader · iPhone WDA relay · YubiKey gate
      │
  LIMBS         VPS (brain, 24/7) · Mac (worker, sleeps) · iPhone (limb, Wi-Fi)
```

Details, module by module: [ARCHITECTURE.md](ARCHITECTURE.md). The north star it was built
toward: [VISION.md](VISION.md).

## Run it

[QUICKSTART.md](QUICKSTART.md) gets the core (bridge + Telegram + Claude CLI) running in
about 20 minutes on a Linux box. The Mac and iPhone limbs and the Cockpit app are documented
but optional; they need your own hardware and Apple developer setup.

```bash
git clone https://github.com/erenyalcinai/ghost-core && cd ghost-core
python3 -m unittest discover -s tests        # 518 tests, ~20 s, no network
cp ops/sahip.example.json ops/sahip.json     # owner profile: sites, products, root cards
printf 'TELEGRAM_BOT_TOKEN=...\nTELEGRAM_OWNER_ID=...\nCLAUDE_BIN=/path/to/claude\n' > .env
python3 ops/telegram_bridge.py
```

## Built in Turkish

Identifiers, comments, commands and the chat itself are Turkish. This was a deliberate
choice for a single Turkish-speaking owner, and it is kept here as-is: the code is what ran,
not a translation. Documentation in this repository (README, architecture, quickstart,
lessons) is English. If you read the code, a few words carry most of the weight:
*köprü* bridge · *nöbetçi* sentinel · *uzuv* limb · *hızlı hat* fast lane · *sahip* owner ·
*sahne* stage · *masa* desk · *nabız* pulse · *ders* lesson · *onay* approval.

## What this is not

- **Not a product.** No installer, no support, no roadmap for other users. It is a
  reference for how one person can build and *operate* an autonomous institution.
- **Not multi-tenant.** One owner, one Telegram id, one profile file. Making it serve many
  people is a rewrite, not a config change.
- **Not sandboxed for untrusted code.** `ghost/sandbox.py` limits time and process group
  and scrubs the environment, but there is no filesystem or network jail. Everything the
  agent runs, it runs as the owner's user. Treat it as such.
- **Not finished.** The event bus is written to but has no central consumer yet; the
  economy module from the private tree is intentionally excluded; several modules carry
  a `NOT:` comment admitting what they don't do. Those comments are kept on purpose.

## Provenance and privacy

The author's name is public by choice. Everything else that identifies people, devices,
accounts, locations or live infrastructure is not: this tree is generated from the private
working repository by a publishing script (kept private) that copies a fixed allow-list of
files, rewrites such identifiers to fixed placeholders, and then runs a deny-list gate over
the result; if any forbidden pattern survives, nothing is published. Three independent
blind reviews were run over the tree before the first release. The owner's real profile
(`ops/sahip.json`) never enters this tree; `ops/sahip.example.json` is a simplified
fictional profile with the same shape. Private worklogs, dossiers, raw lesson logs and
the economy experiments are excluded on purpose; a few scripts mention them and say so.
The project's own public domain (`ghostinprod.com`) is Ghost's brand and stays.

## Author and license

Built and operated by [Eren Yalçın](https://github.com/erenyalcinai) with Ghost itself. Work enquiries: **eren@yalcin.ai**
(part-time, remote). MIT, see [LICENSE](LICENSE).
