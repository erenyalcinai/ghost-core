# Lessons

170 operational lessons from 65 days of running an autonomous personal AI institution in
production. Each one was written into a commit message (`Ders:` block) at the moment the
mistake was understood, and extracted into this file automatically. Dates are kept; commit
hashes, product names and personal details are not. Product references are generalised
("the iOS app", "the owner") on purpose — the lesson is the point, not the portfolio.

They are in chronological order. Patterns that recur: the difference between "the capability
exists" and "the capability is wired to the thing that runs"; restarting a process that is the
parent of your own answer; coordinate spaces; verifying before claiming; and the owner's
decision standing once stated.

---

### 2026-08-21 — A test gate only exists where the thing that runs it is bound to it
Having tests does not make a gate. A gate exists only where it is wired into the process that executes the code; otherwise it is merely good intentions.

### 2026-08-21 — A safeguard must self-heal when the setting that enables it is not versioned
Versioning a safeguard is not enough if the setting that switches it on (a git hooks path) is not versioned too. A protection that cannot travel dies in the first fresh clone.

### 2026-08-21 — A backup channel must be wired into every sender
Having a fallback channel in the code is not enough; it must be attached to EVERY sender. If one sender has no fallback, the single-channel risk remains, and the system is described as "redundant" but goes silent during an outage.

### 2026-08-21 — "I added the setting" is not "the setting is read"
When a system reads configuration in two separate places, adding a value is not the same as the value being honored. For every new secret, verify LIVE on the reading side; looking at the file is not enough.

### 2026-08-21 — Lessons buried in commit bodies die there
The place where knowledge is produced is not the place where it is looked at. Writing it down is not enough; unless something carries it to where it is read, it is stored, not recorded.

### 2026-08-21 — Before calling a capability "present", ask who triggers it
Backup and CI were both ready in code, but both depended on one particular machine being on. That made them a property of coincidence, not of the system.

### 2026-08-21 — A missing permission shows up at the first job that needs it, not at the first try
Before calling a tool "working", make it perform the most privileged thing it will ever do, once.

### 2026-08-21 — Mentioning a tool's marker triggers it
A "skip CI" marker is honored anywhere in a commit message, including when you merely discuss it. When documenting or mentioning a control marker, assume the text is also read by machines.

### 2026-08-21 — A missing permission is sometimes the wrong identity, not a missing grant
If a second identity can do the same job, compare the identities you already hold before asking for more permission.

### 2026-08-21 — A secret's expiry date is the expiry date of every rail bound to it
When binding to an expiring credential, also write down the list of things that will silently stop on that day.

### 2026-08-21 — A stale self-description file produces a wrong start every session
When the file that introduces the system goes stale, the error repeats in every session and its source becomes invisible: it yields not wrong output but a wrong starting point. Verify context files against the source, not from memory.

### 2026-08-21 — Describing the same fact in two files guarantees one goes stale
One of them will rot, and it becomes unclear which is right. Point to the canonical file instead of copying.

### 2026-08-21 — Rules go in the always-loaded file, recipes go in skills
Behavior that must hold in every session does not belong in a skill, which loads on demand; a rule belongs in the always-on context file. The right split: rule in the context file, recipe in the skill.

### 2026-08-21 — Before moving a procedure into a new mechanism, find out who reads it
If automation reads a file by path, moving it silently breaks that automation. The right move is to leave the content in place and put a trigger on top.

### 2026-08-21 — Choose the next task from evidence, not from the plan
Twice in one session I deviated from the plan, and the cause was the same: the plan was built on assumptions, the correction on evidence. Hold plans loosely until evidence arrives; let the most recent observed failure set the order.

### 2026-08-21 — A rejected plan is neither deleted nor silently left alone
Both get misread later. Put the decision and its rejection reason at the top of the file; an archive is only an archive if it says why it is one.

### 2026-08-21 — Harmless constant noise blinds the audit surface
If something is always in the list "anyway", nobody looks at the list when a real problem lands there. Silence the noise so the list carries meaning again.

### 2026-08-21 — When you say "verified from the source", count which lines actually reached the source
In a bulk refresh, a line skipped one by one hides even deeper behind the refreshed file's credibility. Also: a product's canonical truth lives in the product's own repo; an inventory file should point, not copy.

### 2026-08-22 — Write the reason and the reversal condition with a reversible decision
Recording a reversible decision as if it were permanent does as much harm as losing the decision itself.

### 2026-08-22 — Promotional copy is where fabrication is easiest
Every sentence sounds right. Run every capability you claim before you publish it.

### 2026-08-22 — The reflex answer to "what can you do" is a spec list; show one concrete thing instead
When writing for a general audience, do not enumerate capabilities; show one specific thing you did. "I touch a phone remotely" beats "302 tests".

### 2026-08-22 — Do not define the thing by the owner's needs
When introducing something, the easy slip is to explain it through its owner's needs. It shifts the subject and shrinks what is being described.

### 2026-08-22 — Read a bug list's label before its contents
If the version stamp is frozen, the list tells you only that something is broken, not what; triage cannot go further than that. (Seven open Sentry issues turned out to be five of one pattern plus one measurement blind spot.)

### 2026-08-23 — If an organ has two lanes, the context file must say which is the default
I sent a command to the Mac over the 56-second queue lane while the 0.3-second fast lane had been idle for months and the context file never mentioned it. The slowness was in routing, not infrastructure.

### 2026-08-23 — Never swallow a remote tool's schema error silently
A call returning invalid_arguments was counted as "worked"; only a screenshot exposed the lie.

### 2026-08-24 — "Disabled this turn" and "broken" are different things
If you do not tell Ghost the state of its own harness, it will mistake a limitation for a fault and send you chasing a nonexistent problem. The same holds for failover: hitting a limit is not failing; when compensating automatically, do not take the decision out of the owner's hands.

### 2026-08-24 — "Service is up" and "service runs the RIGHT CODE" are different claims
If you do not measure the second, you have never measured deploy success. In a script, `set -e` means silent death, not error handling: the rest of the job (the restart) never ran and nobody noticed.

### 2026-08-24 — A green test on your own machine does not mean green in CI
Any module that unconditionally reads a gitignored file at import time makes every test importing it machine-dependent. If the gate and CI do not test the same environment, the gate protects nothing.

### 2026-08-24 — The gate must run in the same environment, on the same file set, as what it protects
If a gate runs in a different environment than the one it guards, it only tests itself. Telling it to "run the same tests" is not enough; it must run on the SAME FILE SET, otherwise every untracked local file becomes a dependency the gate cannot see.

### 2026-08-25 — Convert a generated draft into house style before publishing
The job that produces the draft labels the lesson one way, but the site renders a single paragraph because line breaks collapse in HTML. A sentinel's output format is not the publication format.

### 2026-08-26 — A plan that lives only in memory does not exist
Memory is tied to a session and may not be recalled; decided work is written into the repo, and memory merely points to it.

### 2026-08-26 — On a decision within the owner's own domain, state your objection once, with reasons
The owner's statement about their own business outranks my outside scan. After the objection is made once, the repeated decision is the decision and goes on record.

### 2026-08-26 — Before measuring local search results, ask where the measurement point is
A ranking observed from the wrong country is noise, not data.

### 2026-08-26 — When deleting rejected work, first verify where it spread
Deleting locally is easy; a copy that escaped keeps living silently.

### 2026-08-27 — The second eye: a blind verifier audits work before it is called "done"
If the decision to call the auditor is left to the party being audited, there is no audit. The trigger must be independent of the work, which is why the pre-push gate works.

### 2026-08-28 — Do not verify analytics with a headless browser
The analytics library's bot filter sees navigator.webdriver and drops the event. The script loads with 200 and no CSP error, but no event ever fires and the setup looks like it works. Real evidence comes from a real device.

### 2026-08-28 — A tool saying "not installed" does not mean it is not installed
The same pattern appeared twice in a day: a Dart binary hung under macOS Gatekeeper quarantine, and CocoaPods sat on the PATH of the wrong ruby. In both cases the right move was to look for where it was, not to install it.

### 2026-08-28 — Do not call a new background job done because "tests passed"
When a garbage-collection sentinel went live it had run on no shard; only the tests knew it was correct. Worker logs were not persistent, so evidence cannot come from logs; the durable trace was a horizon value the GC writes first on every round. The sentinel reads DATA, not logs: if the horizon exists, GC ran; if tombstones older than it remain, deletion did not stick. It goes quiet after the first report.

### 2026-08-28 — Do not lump different failure classes under one error flag
"Timed out" and "session could not be established" landed in the same bucket, so recovery chose the wrong path: it redid the work from scratch (double silence for the user) and then substituted the weaker engine's answer for the stronger one's. The failover itself was right; its trigger was wrong. Before falling back, ask "which error?", not "an error?".

### 2026-08-29 — Do not hang a classification decision on keywords
Keyword routing sat in the most frequently run place as a silent error source. It looked "working" for ages because choosing the wrong department had no visible cost. Had the same logic been wired to a handoff decision, the cost would have been immediately visible: chat going to the worker. A cheap model turn is the price of the right decision.

### 2026-08-29 — Keyword routing was replaced by a single comprehension turn
A routing rule can seem to "work" merely because its mistakes have no visible cost. Mechanisms that produce invisible errors are more dangerous than ones that produce visible errors, because nobody fixes them.

### 2026-08-29 — A decision mechanism must see the world it creates
The handoff decision opened jobs but could not see the jobs it had opened. A system whose output is not an input to its next decision eats its own queue. Same job must not be opened twice.

### 2026-08-29 — When delegating authority, ask what the delegate's power can reach
I considered whether the handoff was CORRECT but not what authority it carried; a wrong classification thus became a process able to push to production. A wrong decision alone does no damage; a wrong decision in the hands of granted authority does. Narrow the authority and classification errors stay cheap.

### 2026-08-29 — When you restrict an actor, build the counterpart channel too
Telling a worker "do not do irreversible things" just left jobs half-finished, because what was missing was not permission but a CHANNEL. If you forbid something, give the actor a legitimate way to request it, or it will either break the rule or become useless.

### 2026-08-29 — Measure a capability's cost before taking it live
Adding a layer without measuring means making the user pay for an unproven benefit. It was the owner, not I, who asked "what does this cost", and noticed it as a user. Measure the latency a new layer adds against the time it saves, simultaneously.

### 2026-08-29 — "Each test mocks its own side effects" is a wish, not a rule
One forgotten test is enough, and it shows on the production side the day it is forgotten. Put the gate on the OUTPUT itself. When you find one isolation gap, hunt for its siblings: file, network, process, clock.

### 2026-08-29 — A limb's single path can break regardless of whether the limb is up
The phone and WDA were fine; only the carrier between them (USB) broke. If a limb is reachable by several paths (USB and LAN), do not bind the rail to one carrier: add a relay that verifies the target and picks the route at runtime.

### 2026-08-29 — A test must set up its own preconditions
Otherwise it measures "is there a .env on this machine?" rather than the gate, and you learn the difference between environments on the day you trust the gate most.

### 2026-08-29 — Derive number formatting from the data source, not the device locale
If one of two side-by-side numbers comes from outside (StoreKit or an API), derive the format from that text, not from the device language; otherwise the same screen shows two formats whenever locale and data source diverge.

### 2026-08-30 — Do not trust a CLI's side fields
Fields like modelUsage can silently come back empty after a version change; the fallback default used when measurement is absent should itself be resolved per model, not be a single constant.

### 2026-08-30 — Measure the pipe before loosening discipline
In phone driving, slowness came from the cost of the pipe, not verification discipline; a single screenshot took 9 seconds. Moving to an MJPEG stream took frames from ~9s to under 1s.

### 2026-08-31 — Keep runtime state files out of the repo
When committing a sentinel's first live run, do not drag its state file into the repo along with it: put the proof in the commit message and the file in .gitignore.

### 2026-08-31 — Verify an approval gate stands at the last reversible point
If automation has moved behind the gate, the rule is theater, not protection. A timer pushed a commit to GitHub at 18:10 and I asked for approval at 18:12. The gate is the commit, not the push.

### 2026-09-01 — "Port shows LISTEN" is not proof of liveness
The local sshd holds the socket while the far end may be dead. The distinguishing measure is data flow, not connect: connect succeeded in 0.00s and I misdiagnosed "Mac is asleep" from it. Also: the sentinel reported "healthy" throughout the outage; do not read the silence of a rail you do not monitor as health. A further lesson: do not leave staged files and move on, because a 15-minute sync timer sweeps them into an automatic commit and the rationale you meant to write is lost. Commit first, then continue.

### 2026-09-01 — Cancel a timed rollback by killing the process, not by leaving an approval file
The safety process sat asleep, and when the cleanup deleted the file it would have woken up and reverted the change. Cancelling means killing the process and then verifying it is really dead. When verifying, `pgrep -f` counted its own command line; use `ps -eo pid,cmd | grep revert[.]sh` for an exact count. Separately: a permission boundary that exists on one machine but not on the other is not a boundary.

### 2026-09-01 — Do not look for a sentinel's cost in CPU
CPU and tokens were near zero; the real cost was 1,440 journal lines a day. When adding a periodic job, ask "what does it write, and how often?", not "how much CPU does it use?".

### 2026-09-01 — On Cloudflare Pages without a 404.html, every path returns 200
This silently produces soft-404s, and a "site opens" test cannot see it; you must deliberately request a nonexistent path.

### 2026-09-01 — A "can't do that" claim is an assertion, not a measurement
A "can't" statement is a claim, not a measurement. If its source is another session's note, verify it with the cheapest possible test before repeating it (a password field turned out to accept typed input after all).

### 2026-09-02 — Ask what your data access actually rests on
Saying "we have access to this data source" is not enough; ask what the access depends on. If it depends on a human's browser session, you have no access, only a borrowed moment. Switching Search Console reads to a service account fixed this.

### 2026-09-02 — A sentinel's value is measured by when it stays quiet
A sentinel that speaks on every single network blip stops being read when a real failure arrives. Source fluctuation should not generate notifications.

### 2026-09-02 — Target the job, not the door
A permission being closed does not mean the job cannot be done. Cloudflare has three products that can do the same redirect; two were closed and the third was open.

### 2026-09-02 — Check you have the permission before you offer the fix
Before proposing a fix, verify you have the authority to perform it. Asking "shall I add this?" commits to the person that it is possible; an unperformable change was offered for three rounds. The capability check comes before the offer, not after.

### 2026-09-02 — Verify agent reports against the source
Two findings in an agent's report (no sshd keepalive, ufw unclear) were wrong, misled by a commented-out line and an unreadable root-only file. Always check such claims at the source.

### 2026-09-02 — Cloudflare blocks default Python user agents; test with the real client
Cloudflare's Browser Integrity Check rejects the Python-urllib User-Agent with error 1010. API clients should send their own UA, and "curl works" does not mean it works; test with the real client's UA. Also: stream-json does not mix with `communicate()`; use a line-reading thread plus `proc.wait(timeout)`, sharing the parsing path with the json mode's result object. Finally, Xcode automatic signing signs a capability-less app with a wildcard App ID, so push needs an explicit App ID, which can be enabled via the App Store Connect API without visiting the portal.

### 2026-09-02 — Check module names before creating one; a pipe is not a test gate
Before writing a new module, check whether the name exists: a new notify module briefly overwrote an existing one and broke 10 tests, so the new code moved to its own file. Also, `tests | tail && commit` is not a test gate because tail always returns 0; use pipefail or a separate step (the pre-push gate caught it this time).

### 2026-09-02 — Read settings from the loader's returned dict
A config loader that returns a dict does not modify the environment. Module-level code that reads `os.environ` will see nothing under a service manager, so wrap access in a helper (the push module reported "no key" for this reason).

### 2026-09-03 — Migrate a monolith through a registry, not in one move
Do not move a large monolith in a single step. A registry plus a "registry first, then the chain" pattern lets commands migrate one at a time while the test gate preserves behavior at each step.

### 2026-09-03 — A second channel is not "equal" until you count all of the first channel's exits
The app mirror was a copy of `send()`, but Ghost's images, videos, documents, voice output and sentinel alarms went out through separate scripts and a notify path, bypassing it. A second channel cannot be called equivalent until every output door of the first is accounted for.

### 2026-09-03 — Reply on the channel the question came from
"Primary channel" alone is not a push rule: if the owner asks from the app and the answer goes to Telegram, the app stays silent and looks like no notification. The question channel is the answer channel.

### 2026-09-03 — Fix a system reflex at its source, not with a delay
A 0.15 s "I'll correct it later" compensation showed the user exactly the jump it was trying to hide. Close the cause at the source.

### 2026-09-03 — A tab that is 95% empty should be a signal, not a tab
If a tab hosts a state that is empty 95% of the time, it has not earned its place. Make that state visible with a signal that appears when it occurs (badge, strip, notification) rather than a permanent tab. Occupying space is not the same as being visible.

### 2026-09-03 — Revisit the message list when a channel's capabilities change
In a system writing to two channels, "every message to every channel" silently becomes wrong: once one channel can show information better, the other channel's text becomes noise. The "still working" notice went to Telegram only because the app has a live equivalent.

### 2026-09-03 — When deferring a capability, record the question, not the feature
Deferring a capability for lack of a concrete scenario is right, but a deferred item sitting on a plan list means nobody looks for the scenario. Record the question: "which of my answers would change with this data?" Here the answer was clear (suggest the Mac when the owner is away from home, shorter jobs on a busy day), and it determined the whole design: staleness gate, location coarsening, field filter.

### 2026-09-03 — Classify the data a new organ produces
When building a new organ, classify the data it emits. `git add -A` silently staged a sensing file with live location data, which was about to enter a public repository; reading `git status` before commit caught it, not a reflex. Rule: if a module writes a file under ops/, the .gitignore line goes in the same commit.

### 2026-09-03 — Cleanup code that relies on "the process will clean up" is a lie if it lives in the dying process
Code that resets a state must hang off a lifecycle other than that of the process producing the state; here the right hook was startup, where "no live work exists" is provable. Second lesson: after writing the rule, look at LIVE data; one source of stale state only showed up in the production table, since test data never produced it.

### 2026-09-03 — Read the canonical source before calling a field "empty"
Prices were reported empty although filled in, and store text was drafted without reading the site's legal pages; the owner corrected both. The same root error recurred in diagnosis: a paywall card missing in one environment (a US-storefront simulator) was generalized to a storefront difference, while the real cause was an A/B arm that the PostHog event already recorded exactly. Do not generalize from a single measurement of a behavioral difference; verify in a second environment or in the event log.

### 2026-09-04 — Verify which tree you build from
The Mac build tree was an rsync copy, not the git clone, which had been behind and dirty for days. Before building, compare hashes of key files on both sides.

### 2026-09-04 — Slash commands only work as the first line of a prompt
Claude Code recognizes a slash command only on the first line of the prompt. Any layer that prepends text (tool note, sensing, persona, handoff) silently turns it into plain text, so put the command first, notes after, and prove it with a test.

### 2026-09-04 — When a session limit cuts work off, the working tree is the truth
After an interrupted session, a new session should first read the git diff from the source to determine what finished and what did not (build, docs, commit). A "doing it" sentence in the transcript does not mean the work was done.

### 2026-09-05 — Make long-running agents write their output to files
When a session limit hits mid-review, background agents' results are lost. For long reviews, have agents write their output to a file instead of trusting the session record.

### 2026-09-06 — Interrogate the filter when a sentinel keeps "fixing" the wrong process
The reverse tunnel's sshd session is a plain `sshd-session` entry; `@notty` is the Mac worker's ControlMaster. A sentinel filtering on `@notty` never found the tunnel. If a "dropped but the path did not return" line repeats, you are probably killing the wrong process.

### 2026-09-06 — For "connection drops" complaints, look at origin versus edge first
Check the Cloudflare origin/edge split and gaps in the API log: origin 200 plus a client gap means the client network; origin 5xx/502 means our side. The app should still do its part: show a freshness indicator and not wipe old data.

### 2026-09-06 — A commit is not an installed build
For the Kokpit, commit, rsync, build and the version installed on the phone must each be verified separately. "I committed it" is not the same as "it is on the phone."

### 2026-09-07 — The Stage should carry only real work output
A hand-pushed snapshot (a Pulse photo) does not go stale on its own and hangs for days; the owner read it as a "demo". Anything pushed for verification must be cleared when the job is done.

### 2026-09-07 — A 401 must mean only "who are you"
iOS URLSession re-sends the same request (same nonce) after a dropped connection. The server collapsed this into the same 401 as an auth error, so the app fell to the pairing screen every time, each pairing created a new device record, and one APNs token spread over 5 records made every notification ring 5 times. Separate the reason for rejection in the response and log. Second lesson: nobody refreshed the bridge after commits on the VPS, so "I committed" did not mean "it's running"; the sentinel now refreshes an idle bridge itself.

### 2026-09-07 — Multi-machine sentinels must label their machine
The same sentinel ran on two machines and printed the same text, so the alarm's origin was unknown; I fixed the VPS and declared done while the Mac kept shouting. Multi-machine sentinel messages should name the machine, and fixes must be verified on all copies.

### 2026-09-07 — To save cache, keep the process alive, not the transcript
Opening a process per turn does not share the cache even when it resumes the same transcript: the system prompt is regenerated per process, and the prefix shifts when git status changes. Keep one long-lived process.

### 2026-09-07 — Separate processes go stale separately
When a field exists on the server but not in the app, first check which process runs the code that produces it. The bridge and the API are separate processes; refreshing one does not refresh the other.

### 2026-09-08 — Write intermediate state to a file during long setups
In a long setup job, write what is done and what remains to a file; when quota runs out the session is lost and a new session otherwise has to dig through transcripts.

### 2026-09-08 — If a door is closed to one IP, try another; never silently skip missing coverage
The Mac is not just an Xcode box but a second network egress, which got past an IP-based block. Silently skipping a missing module makes coverage loss invisible: count and report before writing `continue`.

### 2026-09-08 — A 200 on every path is a soft 404
"The site is live and returns 200" can be bad news for SEO: if unknown paths also return 200, that is a soft 404. Before calling a new static site live, request a path that does not exist and expect 404.

### 2026-09-08 — When automating access, carry "who can see it" with you
A Search Console property opened via API is owned by the API identity, not the human, so make the add step register the human owner as well.

### 2026-09-09 — Put fallbacks in shared infrastructure, and make proxies faithful
Do not bury a fallback path in the first module that needs it; when a second module needs it, nobody knows it exists. A proxy must carry the semantics of the client it replaces (redirects, headers, encoding), or failures do not disappear but come back in an unrecognizable disguise.

### 2026-09-09 — A queue that is never approved is a signal that it is not needed
Ten drafts accumulated over nine days and none was approved. An automation that asks for approval daily turns into noise when no answer comes; the unapproved backlog says "not needed".

### 2026-09-11 — Write the teardown inventory at setup time
Teardown of a short-lived experiment came from the setup notes: the dossier listed every Publish/Measure item (hosting, DNS, timer, Search Console, indexing ping), so removal took ten minutes. Every new setup should record a "what did I touch" list alongside it.

### 2026-09-19 — A copied binary under launchd can die on code signing; wrap the target as a child
A copied /bin/bash under launchd dies with OS_REASON_CODESIGNING. If TCC needs a distinct identity, write a small ad-hoc-signed C wrapper and run the target as a child rather than exec: the wrapper stays the responsible process and the child inherits the permission.

### 2026-09-19 — Apple Notes and iMessage storage formats
Apple Notes body ZICNOTEDATA.ZDATA is gzip(protobuf) with the text path Document(2)->Note(3)->note_text(2); a 30-line dependency-free varint walker suffices. In iMessage, message.text is often NULL and the text is in attributedBody, 5 bytes after the NSString marker (0x81 means a 2-byte length).

### 2026-09-19 — Filter journals at the source and close test DB connections
Filter journalctl output with -g instead of loading it in Python: 30 days of SSH logs were 1.2 GB as JSON. Also, do not leave a sqlite connection opened in a test without commit and close; every later test hangs 30 s on the lock and the error message misleads.

### 2026-09-19 — journalctl -g returns exit code 1 when nothing matches
`journalctl -g` returns rc=1 when it finds no lines, so a oneshot sentinel was failing on every quiet interval. Empty stdout and stderr with rc=1 means "no new lines", not an error.

### 2026-09-20 — mix-blend-mode isolates inside animated containers
If a child using mix-blend-mode sits inside a container animating opacity, the blend stays isolated in the container's group and the image's own background shows. Apply the fade to the children, not the container.

### 2026-09-20 — Use a real alpha channel, not a blend mode, for transparent logos
A white-background JPG made "transparent" with mix-blend-mode shows as a box inside a container with backdrop-filter or opacity animation. The lasting fix is a real alpha channel.

### 2026-09-20 — On a single-branch Pages project, preview and production are the same branch
After going live, a plain deploy pushes the draft ribbon to production. A publish flag was added to the script and a warning written in the dossier.

### 2026-09-20 — Cut logos at the thinnest column, measured with an alpha threshold
When a logo has two objects side by side, looking for an empty column is not enough; measure column fill with an alpha threshold and cut at the thinnest column (for deriving a favicon from the logo mark).

### 2026-09-20 — Always ship a 404 page; set email DNS to match actual mail use
On Cloudflare Pages, without a 404.html every path returns 200 with the home page (soft 404), so a static site always gets a 404.html. For a domain that sends no mail, use SPF -all, DMARC reject and a null MX, and note in the dossier that these must change if mail is set up later.

### 2026-09-20 — Search Console owner added by API sees "no access" until verified in the UI
A property added via the Site Verification API shows the human owner "you don't have access" until they click "verify ownership", which opens it instantly. Indexing requests cannot be made through the API; they are clicked in the UI.

### 2026-09-23 — A question card must close itself when answered
A Stage card that asks a question should close when the answer arrives; if closing is left to the chat turn that handles the answer, that turn forgets and the screen goes stale. A single funnel across Stage, chat and Telegram resolves it.

### 2026-09-25 — A hardware key can be a physical switch
A hardware key can serve two roles, passkey store and USB presence signal, without conflict. A physical switch is not identity: protection is zero while the key is plugged in, a deliberate choice. Build the gate fail-open first, then wire it, so it can ship safely even while the Mac sleeps.

### 2026-09-26 — Put a shared security gate on every path that consumes it
If one security variable (the gate) is consumed through several paths, put the check on each path separately: locking the Mac agents while leaving the phone relay open is a silent hole. Put the check on the machine that holds the hardware, not on the remote client.

### 2026-09-26 — Port to macOS from the same source, with a single bridge file
When porting an iOS SwiftUI app to the Mac, do not scatter `#if` through the views; collect platform differences in one bridge file and exclude iOS-only organs from the target. A 31-file app compiled on the first try because the network and model layers had been written Foundation-pure from the start.

### 2026-09-26 — In a persistent process, "one result per prompt" breaks
In a long-lived process the assumption of one result per prompt is broken by the harness's own turns. Treat output arriving while idle as an answer too, rather than leaving it queued.

### 2026-09-26 — A mid-turn restart kills the reply; persist the in-flight message
"No reply" is not one lost message but a window problem: whatever triggers the restart (apt, a self-refresh, OOM), the child in the process tree dies with the reply. The fix is not to ban restarts but to write the in-flight message to disk and re-queue it once on startup; the ban applies only to the external trigger (needrestart).

### 2026-09-26 — Fire self-terminating actions only after delivery, and when idle
A job that triggers its own death (reboot, restart) combined with a "re-queue the message" safety net creates a loop. The trigger must always come after delivery and while idle.

### 2026-09-26 — When you disable an automatic restart, hand its work to another path
Closing off an automatic restart also removes what it did (fresh libraries). Delegate that work elsewhere, or the ban opens a door to silent rot; here a self-check now sees library updates as "stale" and refreshes the bridge when idle.

### 2026-09-26 — Keep counters and timers on the server, not in the client
Do not hold state like elapsed time in the client: SwiftUI views and tasks are recreated on tab or scene changes. Put the source of truth on the server and let the client only render it.

### 2026-09-26 — Key login alerts to identity, not to username or IP
Tie a security alert to the key fingerprint rather than the user name or IP. If your own automation uses the same account, the alert turns into noise and real alarms become invisible.

### 2026-09-26 — Give the live row under a chat list a fixed height
A live line below a chat list (typing indicator, counter) must have a fixed height. `defaultScrollAnchor(.bottom)` also fires on size changes in iOS 17; restrict it with `for: .initialOffset` on iOS 18.

### 2026-09-26 — Never put a live indicator inside a scrolling list
Any item whose height changes breaks the scroll anchor, so put live indicators (typing, counters) in a fixed strip outside the list. Decide when to snap to the bottom with a conditional size-change anchor, not with `onChange`.

### 2026-09-26 — Derive "user is at the bottom" from user gestures, not geometry
Containers such as the keyboard or a status strip shift the scroll geometry without changing what the user intends. Derive "at the bottom" from user movement, not from offsets.

### 2026-09-26 — The meaning of scroll geometry differs per platform
The "bottom edge" threshold did not account for the top safe area on iOS, so the phone never saw the bottom edge. Verify such thresholds with on-device logs, not assumptions.

### 2026-09-26 — Do not blindly retry non-idempotent generation APIs
An image/video generation API had a balance separate from the web subscription, a nonstandard `Key ID:SECRET` auth scheme, and no idempotency. After an ambiguous timeout, do not blindly retry the POST.

### 2026-09-26 — Do not use LazyVStack for bottom-anchored lists like chat
A lazy stack's estimated row heights send `scrollTo(edge:)`/`initialOffset` to the wrong place and the offset jumps on first scroll. Content height must be exact: use an eager VStack with pagination, Equatable bubbles and cached images. The symptoms "teleporting", "not at the bottom on open" and "black area past the end" share one root cause, and a fluctuating content value in the log is the diagnostic.

### 2026-09-26 — macOS has no setsid
On macOS, background jobs must be started with `(nohup ... &)`. `setsid` silently fails to start the job while still returning rc=0.

### 2026-09-26 — Third-party API schemas are not canonical
A community OpenAPI file listed 2K|4K resolutions for a model, but the live API required 720p|1080p. On the first real call, read the body of the 422 response instead of trusting an unofficial schema.

### 2026-09-26 — Do not assume an SDK's success contract
The official client's `subscribe()` does not raise on failed/nsfw/cancelled; it fetches and returns the response. Capture the final state through the queue-update callback and treat only Completed as success; read the source rather than assuming.

### 2026-09-26 — Grep the code before claiming a capability gap
A note saying "Ghost cannot reply by voice (no TTS)" was stale, because a TTS module and command had existed for weeks. Grep the code before asserting a missing capability.

### 2026-09-26 — Swift package binary downloads can hang behind a VPN tunnel
Xcode's SwiftPM binary-package (xcframework zip) download hung at 0 bytes over a VPN tunnel while curl fetched the same URL fine. Download the zip manually, verify its sha256, and place it in the SwiftPM artifacts cache under the mangled URL name so resolve copies from cache. To diagnose a hung xcodebuild, check with lsof/nettop: no open zip/tmp file means it is not downloading. Also, macOS has no setsid; use `(nohup ... &)`.

### 2026-09-26 — A component that refers to the main agent in third person should not share its name
When the owner said "voice chat is not you", the voice agent needed its own distinct name (in English). Options were presented through a Stage question and "Echo" was chosen.

### 2026-09-26 — Two audio stacks in one process corrupt the shared audio session
Speech dictation and WebRTC share an AVAudioSession and break each other, producing a microphone error. Do not call it "working" after the first stack works; test the second and treat state as sequential.

### 2026-09-26 — Voice agents: distrust the transcript, state capability limits, and count functions after slicing rewrites
A voice agent's "errors" are mostly STT errors, so it should not trust the transcript and should verify meaning before turning it into a task. If you do not write capability limits into the prompt, the agent invents them ("sends mail"). When rewriting a module in slices, neighboring functions between boundary indices get deleted; after writing, count with `grep ^def`.

### 2026-09-26 — Request counts are not traffic
Cloudflare request counts include probers and bots. Use a filtered PostHog count for real visits and the Search Console query dimension for demand.

### 2026-09-27 — Add long-press support when testing context menus on a device
Verifying tile context menus on a device needed a long-press command; tap and swipe were not enough. A W3C pointer pause with a 0.9 s default works.

### 2026-09-28 — Cloudflare redirects: Page Rules work without Redirect Rules, but need a proxied dummy record
Even without Redirect Rules permission, a Page Rule `forwarding_url` sets up a 301. A proxied dummy A record is mandatory, or the rule never reaches the edge.

### 2026-09-28 — An image model may ignore the image-count parameter
The `num_images` parameter was ignored and always returned a single output. For many frames, make parallel calls.

### 2026-09-28 — Generative video: generate the easy direction and reverse it
Video models cannot do "gather/shrink" motion (three failed attempts). Generating a spread-out motion from a clean scene and reversing it with `ffmpeg -vf reverse` works. Image-to-video models invent faces and pastel colors, so write "no faces" and the palette explicitly. Data-URI images are accepted with a 2 MB body cap.

### 2026-09-28 — Compose product videos from generated motion plus real screenshots
The owner wanted a single continuous scene with sound and the product appearing on screen; silent partial clips do not meet that. Generative models cannot do forward "absorb into screen" motion and invent screen content, so generate a "flung off the screen" shot from a clean frame and reverse it. Add sound separately and forward with a sound-generation model, and montage the real screenshot as the product screen.

### 2026-09-28 — Identify devices by browser version and viewport, not by OS version or screen size
Safari 26's user agent freezes the iOS version at "18_7", and private-tab fingerprint protection reports a fake 414x896 screen while viewport width (402) stays real. Base the device signature on browser version plus viewport width. When you see a "foreign visitor with the same signature", first check whether it is the owner's private tab.

### 2026-09-28 — An unrouted media type is silently swallowed
In the Telegram bridge, a media type missing from the dispatch list is dropped silently: the user says "I can't send it" and no error is logged. When a new type appears, check the dispatcher list first, then state the Bot API limits (20 MB getFile) to the user explicitly.

### 2026-09-28 — Check the owner's actual status before asserting a structural blocker
A claim "no company, so Apple payments are blocked" was built without verification, though the relevant information already sat in memory, unlinked. Ask or look up existing status before asserting a structural blocker.

### 2026-09-28 — When widening a broad-privilege reader, carry old limits to the new endpoint
When expanding a process with wide permissions (full disk access), apply earlier promises (Mail/Safari restrictions) to the new endpoint too. A blanket "fully" approval does not remove the previous boundary; write the intersection explicitly.

### 2026-09-28 — Normalize timestamp scale before sorting or filtering
A Core Data date field can arrive at two scales (seconds and nanoseconds) in the same table; "filter out small values" is not enough. Normalize the scale first, then sort and filter.

### 2026-09-28 — Derive display fields from the source table, not cache columns
The same schema's column type can change between iOS and Mac versions (text to blob). Derive previews and other computed fields from the source table rather than trusting a cache column.

### 2026-09-28 — macOS base64 does not accept GNU flags
`base64 -d file` errors out and the `>` target gets truncated. Use `base64 -D -i src -o dst` and verify with md5.

### 2026-09-28 — idevicebackup2 buffers stdout outside a tty
Its log looks empty in the first minutes. If the process is alive in `ps` and the Info.plist was written, wait; Manifest.db comes last.

### 2026-09-28 — Open device-originated SQLite files with immutable=1
A SQLite file from a device can carry a WAL header but arrive without -wal/-shm. `mode=ro` alone gives "unable to open database file"; `mode=ro&immutable=1` opens it.

### 2026-09-28 — Data synced to iCloud may be missing from device backups
An iOS 26 backup does not include iCloud-synced data (call history, Safari history), but the same data lives in the Mac's own databases. Check the Mac copy before saying "not in the backup". Also, idevicebackup2 does not resume after a cable drop (-4); it resends the same image from scratch, so keep the phone connected for the first full backup.

### 2026-09-28 — "Passes locally" is not enough while CI is red
A test that assumed a directory or binary absent on the runner stayed red for five days. State such assumptions explicitly in the test, and read the spool/source before saying "X does not exist".

### 2026-09-28 — "Access granted" is not the same as "data exists"
Permission for the Mail directory existed but no account was in it. Before writing an endpoint, verify the source is actually populated, and ship a diagnostic endpoint with the first deploy.

### 2026-09-28 — Gmail IMAP mailbox names depend on account locale
Special mailboxes are found by their \All \Junk \Sent flags, not by name. An unquoted X-GM-RAW query makes Gmail answer "Could not parse".

### 2026-09-28 — Do not trust raw hrefs in email-driven automation
Tracking links from mail providers carry the real URL as base64 JSON in a `p=` parameter. Decode it rather than following the raw href.

### 2026-09-29 — A sentinel that says "down" must record why
A boolean status is not enough for diagnosis; log the HTTP code, exception type, duration and edge request ID, or you will have to scrape provider logs at the next incident.

### 2026-09-30 — Large export archives can break standard unzip tools
ChatGPT exports since 2026-09 are split into conversations-NNN.json parts in a zip64 archive. macOS unzip 6.0 and Python 3.9 zipfile fail on it ("corrupt", negative header offset); bsdtar opens it fine. Keep the large zip on the Mac and move only the JSON files via tgz+scp, using a dedicated non-default SSH key. iCloud Drive sync did not arrive in 10 minutes while AirDrop landed instantly, so suggest AirDrop for large files.

### 2026-09-30 — "Dataless" iCloud files look normal but cannot be opened
A file can appear in `ls` while its content is only in the cloud; `open()` fails with "Resource deadlock avoided" (errno 11). That means content not downloaded, not a broken reader; fetch it with `brctl download` and re-read. It differs from a `.X.icloud` placeholder, so handle both.

### 2026-09-30 — Do not infer identity from device class
An iOS 26 + Safari 26 + viewport 402 signature identifies a phone class, not a person, and geo-IP and missing IP data cannot separate them either. For a certain split, mark the owner's own browser on the site (opt-out).

### 2026-10-01 — Hardware flashing quirks: unified images and power switch
The firmware repo moved to a new organization (301). The Cardputer image is unified (bootloader, table and app in one bin), so esptool writes it at 0x0, not 0x10000; verify with the first byte 0xe9 and 0xaa50 at 0x8000. The Cardputer ADV serial port does not appear while the side power switch is ON: switch OFF, then plug in USB-C.

### 2026-10-01 — Use the device's own shell to prove a live boot
The firmware's USB-serial/JTAG interface exposes a prompted shell at 115200; `help` dumps all commands and confirms the version, which is live boot proof separate from the flash hash. Sub-GHz/IR transmit and speech commands generate RF or sound, so they are not read-only.

### 2026-10-01 — Model aliases are frozen at the CLI build date
In the Claude CLI the "opus" alias is a string fixed at build time, not resolved live; a stale CLI neither finds the new model by alias nor accepts its full ID (unrecognized_model, 0 tokens). The only gate for new models is updating the CLI. If you resolve the mapping yourself, "the API offers it" is not enough: also require "the CLI recognizes it" and take the intersection.

### 2026-10-02 — App Store Connect build lists lag after upload
After an upload, the builds list returns empty for about 15 minutes; do not assume it was not uploaded, since Apple's warning mail lands in the spool earlier.

### 2026-10-02 — Check size before copying a Flutter repo between machines
Run `du` first: Flutter build, .dart_tool and tool directories add up to about 5 GB. macOS rsync does not support `--chown`, and macOS has no setsid.

### 2026-10-03 — Know the regulatory thresholds before choosing income channels
An exemption covered only some income channels. For the others, learn the registration threshold before building, not after.

### 2026-10-04 — A pending Cloudflare zone can be pre-configured; registry propagation may lag
While a zone is "pending", Page Rules and DNS can be set up in advance, and the edge returns 409. Even if WHOIS shows changed nameservers, the TLD registry's authoritative answer can lag; before concluding "the owner entered it wrong", check the registry with `dig +norecurse` against the TLD nameservers.

### 2026-10-04 — Validate duplicated surfaces with usage data
A question-card "reflex" duplicating chat was added to animate the Stage. A 30-day log showed the owner always answered from chat, so the copy only went stale. Measure a surface's value by how often answers come from it, not how often it was shown; if the second number is near zero, remove the copy.

### 2026-10-04 — Design shared spaces so the data model prevents breakage
Embed "who can break what" in the data model: separate the canonical file from the published copy, keep permissions in one function, and enforce them on the server rather than the client. The answer to "don't let me break things" is architectural separation, not user restriction. Second lesson: an `.alert`/`.sheet` attached to Menu/contextMenu content disappears when the menu closes and never opens; keep dialog state in the model and present it at the root.

### 2026-10-04 — Compile every target when adding a case to a shared enum
Adding a case to a shared enum requires also building the second target's switches (macOS); a single-target build does not reveal that the other is broken. The Mac build had been broken for days.

### 2026-10-04 — Add new storage directories to the backup list
A "persistent" shared area was designed, but its uploaded file copies were not on the backup list and were single copies. When a new storage directory is created, add it to the backup list too.

### 2026-10-04 — Recurring swallowed errors mean a feature never worked
Calls wrapped in try/except in the bridge fail silently. A repeated log line such as "activity start error" means a feature is not working at all, not that it is noise: an undefined local variable made every job without a department raise UnboundLocalError.

### 2026-10-05 — Backup-restore alarms can be environment faults
A restore-test failure was a tmpfs user quota ("Disk quota exceeded") on a 54 MB archive, not a bad backup. Open the test on disk rather than /tmp and read errno first.

### 2026-10-09 — Always set an explicit LIMIT in HogQL
Without a LIMIT, HogQL returns 100 rows without error, which halved a 30-day report. Put an explicit LIMIT on every GROUP BY query and treat exactly 100 rows as truncated.

### 2026-10-12 — Close the matching "open" line in docs in the same turn
When something is approved in App Store Connect, close the corresponding "OPEN" line in the dossier in the same pass; it stayed stale for eight days.

### 2026-10-14 — Open-sourcing a personal codebase needs a repeatable redaction pipeline
A "secrets scan" is not enough: identity leaks into code comments, seed data, test fixtures and device identifiers. Keep substitutions in a fixed mapping table, the gate as a separate deny list, and both in one re-runnable script; a hand-cleaned tree gets re-polluted on the second release.

### 2026-10-14 — Move personal constants out of code into a profile file
Hard-coded personal constants make code inseparable from its owner; moving them to a profile file and generating the sample from the same redaction mapping keeps both trees on one source. Also, a `git ls-files` filter does not see a new file until it is added, so an ImportError in the clean tree was caused by staging, not code.

### 2026-10-27 — Text the model sees is not approval
Approval used to be a chat line ("[action: id] approve") with a timestamp id; any mail, page or tool output could contain that line. Irreversible actions now check an approval record written only at the owner's real entry points (signed app request, owner-verified non-forwarded chat message), single-use and time-limited, with random ids.

### 2026-10-27 — Runs that read untrusted text get no tools and no secrets
The morning brief read web pages while allowed to Read and Write files; the mail digest was "tool-less" only in its docstring. Both now run in an empty temp directory with an explicit tool list (`--tools ""` or web-only) and only the auth token in the environment, and the shell, not the model, writes the output file.

### 2026-10-27 — Replay real history before installing a security hook
A PreToolUse hook that blocks reading secret files, dumping the environment and sending secrets or personal data to unknown hosts first blocked about 10% of three weeks of real tool calls (ssh -i key paths, sourcing the env file, URLs inside request bodies). Three narrowing passes brought it to about 1%, almost all genuine "print the secret" commands. The hook protects its own file and settings, so later changes go through the owner's approval card; re-test that write path after every narrowing.

### 2026-10-27 — A guard the agent's own user can write is a suggestion
The tool guard, its settings file and the approval record all belonged to the same Unix user as the agent. An independent review got 14 of 18 crafted commands past it, including rewriting the settings file with an obfuscated path so later sessions would start without the hook. Rules about the guard inside the guard cannot fix that. It now lives in root-owned managed settings, runs under `python -I` so a planted `usercustomize` cannot load, and checks approvals without importing any module the agent can edit. A user-level `disableAllHooks` was tested against it and did not turn it off.

### 2026-10-27 — One step from root is root
The VPS user had a single sudo line, but the agent could run commands on the Mac, and the Mac held an unrestricted root key for the VPS. The boundary was on the wrong machine. The key is now pinned with `command=` to reboot and service restarts; full root needs a hardware key and a physical touch.

### 2026-10-27 — If the verifier crashes, the job is unverified, not done
The second-eye check returned no verdict when it threw, and "no verdict" did not block the green tick. A crashed verifier now yields "unverified", which blocks permanent and spending work and is stored in memory under a separate kind, so later sessions do not read it as fact.

### 2026-10-27 — A scrubbing regex rewrote code it did not understand
The publishing script replaced a product name inside identifiers: `desktop` became something else, and every Mac screenshot and typing call in the public tree pointed at a tool that does not exist. All tests passed because no test exercised that path. Scrub rules now use word boundaries and the publish gate scans for corrupted identifiers.
