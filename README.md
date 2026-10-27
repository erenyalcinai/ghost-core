# Ghost — a one-person autonomous institution, running in production

Ghost is a personal AI infrastructure that runs 24/7 for one owner. It is not a chat
window and not a single coding session. It is a small institution: a brain on a VPS,
a Mac it uses as a worker, an iPhone it physically controls, a Telegram bridge, a
native iOS/macOS cockpit app, a voice agent, a persistent memory, a governance layer
with approval gates, and a blind second-eye verifier that can veto its own reports.

For its owner it is the thing that reads the mail, searches the last ten years of messages,
notes, photos and calls when asked, writes reminders into the phone, drives the Mac, ships
the apps, and reports every morning. For a studio it is the invisible half of the team.

This repository is the **open core** of that system. It was built by one engineer and
the agent itself, in production since 11 August 2026, with real failures. The lessons those
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
| Tests | ~580, run in ~20 s with `python -m unittest`, no network, CI on every push |
| Scheduling units | 8 launchd agents (Mac) + 3 systemd units (VPS) in this tree; the bridge and self-check units themselves live in the private deploy |
| Lessons | 170 operational lessons, auto-extracted from commit messages, curated in [LESSONS.md](LESSONS.md) |
| History | Started 11 August 2026; this open-core snapshot is from day 78 (880+ commits in the private tree, most authored by the agent under the owner's direction) |

## What it does, day to day

Mechanisms are below; this is the outcome side. Numbers are from the private tree on 2026-10-15,
day 65. Anything marked *(private)* is code that stayed in the private tree: the owner's business
scripts and two readers. Everything else named here is in this repository.

### For one person

- **Morning report.** What it did overnight, what it is waiting on you for, how the business is
  doing. On Telegram and on the Pulse page of the cockpit. 589 briefing lines so far.
- **Reads your mail.** Your inbox is forwarded to its own address and spooled into a digest; it
  reads your Gmail over IMAP with Gmail's own query language, full history. *(private)*
- **Writes into your phone.** Real iOS reminders and calendar events, queued through the cockpit,
  with the alarm living on the phone so it fires with the server off. Geofences you set from where
  you are standing wake the agent when you arrive.
- **Knows your context.** Your location, today's calendar and your battery are one line in every
  turn. Stale data is dropped rather than guessed.
- **Searches your past.** Apple Notes, Photos, iCloud Drive, WhatsApp (live and from backup),
  SMS/iMessage history, call log, contacts, Safari history, all from an unencrypted iPhone backup
  it takes itself every night, plus 2,623 ChatGPT conversations imported and full-text indexed.
  Every read goes through one localhost process with a token and a log: `ops/mac_veri_okuyucu.py`,
  14 endpoints, public. Only the Gmail IMAP reader and the ChatGPT importer are *(private)*.
- **Uses your machines.** Screenshots, clicks, typing and app launches on the Mac in ~0.3 s; the
  same on an iPhone over Wi-Fi. The cockpit shows you the live screen.
- **Talks.** A voice agent that answers from tools and forwards real work instead of pretending.
- **Shows instead of telling.** Tables, diffs, charts and approval cards drawn by the server inside
  the native app; a shared Desk of cards you can add notes and files to. 80 images and files sent
  into the app so far.
- **Remembers.** 164 memories; the three most relevant are injected into every turn, the rest
  are a full-text search away. An entity graph you can query (`/varlik`), standing goals it works
  on when idle, and an encrypted backup of the memory database to a git branch with a restore drill.

### For a studio or a team

- **Ships apps** *(private scripts)*. Builds, signs and uploads iOS apps; wrote the store listing in two languages,
  took the screenshots, answered App Review and filed the in-app purchase. App B went live on
  the App Store on 2026-10-04 this way, with the owner doing two things: logging in and saying go.
- **Deploys sites** *(private scripts)*. Cloudflare Pages and Workers, DNS, redirects via Page Rules when the modern
  API is closed to it. 220 deployments of its own diary site alone.
- **Watches.** Uptime, open ports, SSH logins, tunnel health and PostHog traffic with the owner
  and bots filtered out (public sentinels); Sentry errors, App Store review status and subscriber
  counts read straight from the D1 database *(private)*. A self-check every few minutes; 172
  alerts raised in 65 days, most of them about its own stale code.
- **Reports on the business** *(private)*. A daily business digest (42 snapshots) and a daily
  intelligence briefing; the weekly synthesis and the weekly transparency report (what it did
  on its own, what was approved, what was refused) are public.
- **Fixes itself.** Restarts stale services without killing the reply in flight, restores its own
  git hooks, updates its CLI daily, notices when the model alias map has gone stale.
- **Keeps books.** 1,934 autonomous actions in the audit log; 1,071 webhook jobs and 17,435 chat
  turns in the event log; 710 commits, 172 lessons.
- **Works while you sleep.** A job queue for the Mac that runs when it wakes; cron and systemd
  units for the rest; a physical key that can take the Mac away from it.

### Data it can reach

| Source | How | In this tree |
|---|---|---|
| Telegram, native iOS/macOS app, voice | bridge, cockpit API, ElevenLabs Agents webhooks | yes |
| Mac: shell, GUI, screen | reverse tunnel fast lane + queue, computer-use driver | yes |
| iPhone: screen, taps, apps | WebDriverAgent over Wi-Fi relay | yes |
| Private Mac data: Notes, Photos, Files, WhatsApp, SMS/iMessage, calls, contacts, Safari, Mac Mail, nightly iPhone backup | single Full-Disk-Access reader, localhost + token + log | yes |
| Mail: own inbox, forwarded inbox, Gmail IMAP | spool + digest, IMAP reader | private |
| Anything you share from any app, your camera, your voice | Share Extension, camera in chat, on-device speech-to-text, Siri shortcut | yes |
| ChatGPT history | export import + FTS | private |
| GitHub, Sentry, Cloudflare, App Store Connect, PostHog, D1 | MCP servers and REST | wiring pattern yes, business scripts private |
| Owner context: location, calendar, battery | cockpit senses | yes |

### Examples, as they actually happen

Reconstructed from the private log and shortened; each one happened. The owner types on a phone; the agent
runs on a server and reaches into a Mac and an iPhone.

**Search your life, then act.** *"When did I last talk to the packaging supplier, and what did we agree?"*
The agent searches WhatsApp (live and backup), the SMS history, the call log and Apple Notes
without being told where to look, quotes the message with its date, and offers to put the agreed
date into the calendar. The policy is explicit: it only touches personal data when the question
is about the owner's own history, never in code or business work, and every read is logged.

**Say it, get an alarm.** *"Remind me tomorrow at 10 to call the factory."* The intent layer turns
the sentence into a reminder proposal with an approve button; on approval a real iOS reminder is
written on the phone, so the alarm fires even if the server is down. *"Add a zone around here, 300 m"*
makes a geofence from the phone's current position; arriving there wakes the agent, which decides
what, if anything, to say.

**Mail without opening mail.** *"Did a bank statement come this week?"* It reads the forwarded
inbox spool and runs a Gmail query (`from:bank newer_than:7d`), answers with sender and date, and
can hand back the attachment. It cannot send mail; it says so instead of promising.

**Ship a release.** *"Put App B 1.0.1 out."* On the Mac it builds, signs and uploads the build,
writes the release notes in two languages, and shows an approval card in the cockpit with the
diff of what will change on the store page. Nothing permanent happens before the card is answered.
When Apple asked for a demo on a real device, the agent recorded the app on its own iPhone and sent it.

**Explain a number.** *"Why does example.com get no views?"* It queries PostHog with HogQL, filters
the owner's own devices and the bots out, checks Search Console, and publishes a report card to the
shared Desk with the table instead of pasting it into chat.

**Change its own code.** *"The chat jumps when I scroll."* It reads the app's diagnostics channel,
patches the Swift view, builds and installs on the phone through the Mac, commits with a `Ders:`
block explaining the root cause, and the lesson lands in `LESSONS.md` by itself. Pushes pass a
test gate; the bridge reloads its own new code only when idle, so the reply that triggered the
change is not lost. About 83% of the private tree's commits were made this way. A `FREEZE` file
or the pulled YubiKey stops all of it.

**Fan out when one head is not enough.** `/fleet audit the backend for security` splits the job,
runs sub-agents in parallel, has them critique each other, and returns one synthesis. Every
ordinary job is first routed to one of five departments (engineering, intelligence, finance,
revenue, operations), each with its own persona and tool permissions.

**Work at night, report in the morning.** Standing goals are picked up when the chat is idle; the
morning report lists what was done, with the audit entries, and what needs the owner. If the server
dies, the Mac takes over the Telegram listener (baton policy) so the owner at least hears why.

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

**It builds and corrects itself, through itself.** The agent edits its own repository from
inside its own chat session: it patches `ghost/` and the Swift app, writes the tests, commits,
and the push goes through the same test gate as any human push (`ops/githooks/pre-push`). The
running bridge then replaces itself with the new code only when idle (`ops/self_restart.sh`);
the self-check notices when a process is still on stale code and renews it; the Mac worker
reloads new code within 30 s on its own. The loop closes on knowledge, not just code: every
root cause is written as a `Ders:` block in the commit, extracted into the lessons file
automatically, and the rules that must change behaviour go into the agent's own operating
context (`CLAUDE.md`), which it is instructed to keep correct: a stale line there misleads
every future session, so it fixes the line when it sees it. Its memory directory is written
the same way. The publishing script that produced this repository, the scrubber, the CI
workflow and this README were written by the agent. About 83% of the private tree's commits
are its own. What it may not do on its own: anything classed permanent or spend, anything
while `FREEZE` exists, and anything on the Mac while the key is out.

**Your data, and who may read it.** The owner's private data (Notes, Photos, iCloud files,
WhatsApp, SMS/iMessage, calls, contacts, Safari, Mac Mail, a nightly unencrypted iPhone backup
the agent takes itself) is readable by exactly one process on the Mac: a Full-Disk-Access
reader bound to localhost, token-gated, with every read written to a log the owner can open
(`ops/mac_veri_okuyucu.py`, 14 endpoints). The fast lane the agent uses for everything else
deliberately has no such access. A YubiKey in the Mac is the physical switch: pull it and the
reader answers "locked". The rule the agent follows is written, not implied: it searches
personal history only when the question is about the owner's own life, never for code,
infrastructure or business work, and it says "not found" only after it has looked everywhere
(`ops/mac_veri.py` documents the sources). Backup, chat history and mail readers that import
third-party data (Gmail IMAP, ChatGPT export) stayed private; the gate is public.

**It has hands, not just an API.**
- **Mac as a worker**: a fast lane (reverse SSH tunnel, ~0.3 s round trip) for commands,
  screenshots and GUI actions; a queue for when the Mac sleeps; a Full-Disk-Access reader
  that is the *only* process allowed to touch private data, bound to localhost with a token
  and a log.
- **iPhone as a limb**: WebDriverAgent over Wi-Fi through a relay, so the phone needs USB
  only for charging. Coordinate spaces (points vs pixels) are handled explicitly because
  getting them wrong taps outside the screen.
- **Cockpit, a full iOS and macOS citizen**: a native app with a chat (SSE stream, camera,
  on-device Turkish speech-to-text, multi-attachment tray), a server-drawn "Stage" (tables,
  diffs, charts, live Mac/phone screens, approval cards), a "Pulse" page that answers three
  questions (what did Ghost do today, what is it waiting on me for, how is the business), a
  "Decisions" page with swipe-to-approve, and a shared persistent "Desk" of cards. Around it:
  a Siri shortcut and Action-button intents for the lock screen, a Share Extension ("send to
  Ghost" from any app), a home-screen widget, Live Activities for running jobs, push via APNs,
  a Mac menu-bar app from the same source, QR pairing with HMAC+nonce device auth. The app
  writes real iOS reminders and calendar events on the owner's phone, and geofences wake the agent.

**It remembers.** SQLite + FTS5 memory injected into every turn, an entity graph, standing
goals the agent works on when idle, and continuity hooks that notice when a session is
about to end without writing anything down.

**It is multi-engine.** Claude is primary; Codex, Gemini and a local Ollama model are detected
and used as fallback, on an API outage *or* when the quota is hit, and the owner is told which
engine answered. Model aliases resolve to the newest model the API *and* the CLI both know,
because a stale CLI silently rejects new model ids. Quota is shown as a percentage, and one
persistent `claude` process per chat keeps the prompt cache warm.

**It survives its own server.** If the VPS goes dark, a baton policy lets the Mac take over the
Telegram listener, so the owner is told why instead of hearing silence. The memory database is
backed up encrypted to a git branch, and a restore drill script proves the backup is readable.

**It is an institution, literally.** Every job is routed to one of five departments defined in a
YAML file (persona, tools, keywords); `/fleet` runs parallel sub-agents with cross-critique; a
weekly transparency report turns the audit log into "what I did on my own, what you approved,
what you refused". Continuity hooks flag a session that is about to end without writing anything
down; idle time goes to standing goals.

**It is tested like it will be run.** ~580 tests in 20 s with every path redirected and the
network cut (`tests/common.py`, after a test once leaked into production), a pre-push gate that
runs them, and CI that runs the same command on the same file set.

## How you talk to it

Plain language, in Turkish or English, on Telegram or in the app; the agent answers in your
language. About 50 slash commands exist for the operating side. A sampler:

```
/durum  /saglik  /kota  /motorlar       health, quota, which engine answered
/nerede  /bugun  /duyular              where you are, your day, what the agent currently senses
/hatirlat  /gorev  /izle  /bolge         schedule in natural language, watch a URL, add a geofence
/gorevler  /istekler                   what is queued for your phone, what awaits your approval
/sahne  /masa  /rapor                   draw on the Stage, publish to the Desk
/hafiza  /varlik  /ogren  /hedef        memory, entities, teach it a fact, set a standing goal
/fleet  /departmanlar  /job  /jobs      multi-agent mode, routing, background jobs
/model  /kanal  /sesli                  pick a model alias, primary channel, spoken replies
/guard  /freeze  /unfreeze  /tazele     governance mode, stop all autonomy, refresh context
```

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
python3 -m unittest discover -s tests        # ~580 tests, ~20 s, no network
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
  agent runs, it runs as the owner's user. Treat it as such. What stands between untrusted
  text and the secrets is a root-owned tool guard (`ops/guvenlik_kapisi.py`, QUICKSTART 4b):
  heuristic, it stops plain one-step exfiltration, not a determined attacker.
- **Not finished.** Automated inputs (monitors, scheduler, goals, webhooks) flow through the
  event bus, chat input still bypasses it; outbound e-mail exists but is unwired (no key);
  the economy module from the private tree is intentionally excluded; several modules carry
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

Built and operated by [Eren Yalçın](https://github.com/erenyalcinai) with Ghost itself. Questions about the code:
**eren@yalcin.ai**. MIT, see [LICENSE](LICENSE).
