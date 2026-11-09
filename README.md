# AI-DLC Pulse

A live, read-only browser dashboard for AI-DLC v2 runs. See stage and unit-of-work progress, which agents are working, what's waiting for your approval, and where the time went.

**AI-DLC Pulse** shows the heartbeat of an AI-Driven Development Lifecycle run. It reads the files AI-DLC already writes (stage state, the audit trail, unit plans and artifacts) and turns them into a live dashboard. You can see progress across the stages and units of work, which agents are active, approval gates and paused units waiting on you, and stalled stages and errors, with optional desktop alerts. A timeline shows how time splits between agent work and human review. It needs only Python, never writes to your project, and runs on your own machine.

> **Note:** This is sample code, for non-production usage. You should work with your security and legal teams to meet your organizational security, regulatory and compliance requirements before deployment.

## Demo

A short walkthrough of a running AI-DLC record: the Overview and Needs you panel, the animated Workflow and units of work, the Timeline, Agents, the Audit trail, and the commands cheatsheet.

https://github.com/user-attachments/assets/386ecce2-da84-4102-a230-7dcc3a4e3b4d

## Why

An AI-DLC run moves through up to 33 stages and 14 agents, and it pauses at approval gates that need a person. The engine records all of this in files: `aidlc-state.md`, the audit trail, and stage artifacts. Those files are hard to follow while a run is in progress. People miss open gates, can't tell whether a quiet session is working or stuck, and find it hard to explain progress to stakeholders.

## What it does

- **Overview:** shows progress, the current and next stage, and a lifecycle board coloured by status. Click a stage to see its lead and reviewer agents, timings and artifacts.
- **Needs you:** lists open approval gates (with their questions file), pending AI-DLC questions, stalled stages and new errors. It can also send desktop notifications and puts a count in the tab title.
- **Workflow:** shows an animated flow of the run with a replay mode that steps through its history. It also draws the units-of-work dependency graph, from the Inception gate through the units to build and deploy.
- **Timeline:** a Gantt chart that splits each stage into agent work, waiting on you, and rework, plus time per phase and a rough estimate of time left.
- **Agents:** shows which agents are working now, have worked, are coming up, or aren't used in this run.
- **Audit trail:** the full event log, with search, filters by event, stage and agent, and JSON export.
- **Artifacts:** formatted markdown and JSON viewer for every file a stage produced.

## Who it's for

Developers running AI-DLC day to day, workshop facilitators watching a session, and leads who want a clear view of where an AI-assisted delivery stands.

## Quick start

Requires Python 3.11 or later. It uses only the standard library, so there's nothing to install.

```bash
git clone https://github.com/aws-samples/sample-aidlc-pulse.git
cd sample-aidlc-pulse
python3 server.py --project /path/to/your-aidlc-project
# open http://127.0.0.1:8765/
```

| Option | Default | Meaning |
|---|---|---|
| `--project` | (required) | AI-DLC project root, the folder that contains `.kiro/` (or another harness folder) and `aidlc/` |
| `--port` | `8765` | Port to listen on |
| `--host` | `127.0.0.1` | Loopback address to bind. Any other address is refused because the dashboard has no authentication |
| `--stall-minutes` | `15` | How long a working stage can go without audit activity before it's flagged as possibly stalled (1–1440) |

The page checks for changes every 5 seconds (every 15 seconds in a background tab) and only redraws when something changed.

Deep links: `/?record=<space>/<intent>&stage=<slug>`. Add `#workflow`, `#timeline`, `#agents` or `#audit` to open a tab.

## The tabs

### Overview

- **Record picker:** every intent under `aidlc/spaces/<space>/intents/<intent>/`, plus a legacy flat `aidlc-docs/` record if one exists. The newest is selected by default.
- **Summary cards:** percent of in-scope stages done, workflow status, current and next stage, gates awaiting approval, scope, depth and test strategy, and the agents working now.
- **Lifecycle board:** all 33 stages by phase, coloured by status: completed, in progress, awaiting approval, revising, pending or not in scope.
- **Stage detail** (click a stage): lead, support and reviewer agents, execution and condition from the stage frontmatter, start and finish times, duration, gate and approval counts, agents seen in the audit trail, and the stage's artifact files.
- **Recent audit events**, an **Errors logged** card, and **Sessions**: active space, current session, bound intent and recent prompts.

### Needs you

A panel at the top of the Overview tab lists everything waiting on you:

- **Approval needed:** an open stage gate, how long it has been open, and a button to read the stage's `*-questions.md`. You still approve in the Kiro chat. The dashboard never writes.
- **AI-DLC is asking:** pending engine questions from `aidlc/.aidlc-sessions/questions/` raised since this run started.
- **Possibly stalled:** a stage is in progress but the audit trail has been silent for longer than `--stall-minutes`. Waiting at a gate doesn't count as a stall.
- **Errors in the current stage:** `ERROR_LOGGED` events. Every error is also listed in the Errors logged card.

When nothing needs you, the panel turns green and says so. The browser tab title shows the count, for example `(2) AI-DLC Pulse`.

Click **🔔 Enable alerts** once to get desktop notifications when a new gate opens, a stage stalls, a question arrives or an error is logged. Items that already existed when you opened the page don't notify.

### Workflow (animated)

- **Lifecycle flow:** the run's in-scope stages as a flow diagram across the five phases. Tick "show out-of-scope stages" to see all 33. Links between finished stages are solid green. The link into the running stage is a moving dashed line with a dot travelling along it. The running stage pulses blue, and an open approval gate pulses amber. Click a stage to open its detail.
- **Replay run:** steps through the stage start and finish times from the audit trail, so you can watch the run unfold. Play or pause, drag the slider, or click "Live view" to return.
- **Units of work:** the dependency graph from `inception/units-generation/unit-of-work-dependency.md`, laid out by dependency depth from the Inception gate through the units to Build & Test and the CI/deploy tail. Links already implied by a longer path are hidden. Unit status comes from `UNIT_*` audit events when the run logs them. Otherwise every unit takes the status of the code-generation stage.

Animations stop when the operating system's "reduce motion" setting is on.

### Units of work

When a run plans units (stage 2.7 units-generation), a **Units of work** card appears on the Overview and Workflow tabs:

- **Progress:** "N of M done", a progress bar, counts of units in progress, paused and not started, and the unit and stage being worked on now.
- **Unit × stage grid:** one row per unit and one column per per-unit Construction stage in scope (functional design, NFR, infrastructure, code generation). Each cell shows done ✓, in progress ●, paused ⏸, skipped –, or files written but no receipt ◐.
- **Paused units:** the reason and next step AI-DLC recorded. These also appear under Needs you and trigger an alert, because AI-DLC won't start other work until a paused unit is resumed.
- **Time per unit** on the Timeline tab, and unit receipts are included in the Workflow tab's replay.

How it's worked out:

| Question | Source |
|---|---|
| Which units are planned | The fenced `yaml` edge block in `inception/units-generation/unit-of-work-dependency.md`, then `runtime-graph.json` (`bolt_dag.units`), then the older "Unit N (Name)" dependency table |
| A unit's progress at each stage | `UNIT_STARTED`, `UNIT_PAUSED`, `UNIT_RESUMED`, `UNIT_COMPLETED` and `UNIT_SKIPPED` audit events, keyed by stage and unit. A stage gate rejected for the unit reopens it |
| When a unit is done | Approved at its `construction-unit` checkpoint and not rejected since, which is how AI-DLC counts it. Without checkpoints: every per-unit stage completed or skipped |
| Runs with no unit events | Each cell takes the overall status of its stage |

### Timeline

- **Totals:** agent working time, time waiting on you, rework after a change request, and the longest stage.
- **Estimated time left:** the median duration of this run's finished stages multiplied by the in-scope stages left. It's a rough figure and needs at least 2 finished stages.
- **Time by phase** bars, and a **stage Gantt chart**. Each stage's bar is split into agent work (blue), waiting on you (amber) and rework (red). Segments still running pulse. Click a bar to see that stage's audit events.

Time comes from the stage events in the audit trail:

| From | To | Counted as |
|---|---|---|
| `STAGE_STARTED` | `STAGE_AWAITING_APPROVAL` | Agent work |
| `STAGE_AWAITING_APPROVAL` | `GATE_APPROVED` / `GATE_REJECTED` | Waiting on you |
| `GATE_REJECTED` / `STAGE_REVISING` | the next `STAGE_AWAITING_APPROVAL` | Rework |

### Agents

Every installed agent (`<harness>/agents/aidlc-*.json`) plus the orchestrator, grouped by what it's doing in the selected run:

| Group | Meaning |
|---|---|
| Working now | Leads a stage that is in progress or revising, or was dispatched (`SUBAGENT_DISPATCHED` / `REVIEW_REQUESTED`) with no matching completion yet |
| Waiting for approval | Leads a stage whose gate is open |
| Has worked | The audit trail shows it starting a stage, being dispatched, or reviewing |
| Upcoming | Assigned as lead, support or reviewer to in-scope stages that haven't run |
| Assigned, no activity recorded | Named as support or reviewer on finished stages, but there's no audit event for it. Support agents only appear in the audit trail when they're dispatched as subagents |
| Not in this run | Installed, but no in-scope stage uses it |

Each card shows the stages the agent ran, leads, supports and reviews, its model tier, dispatch counts, and a link to its audit events.

### Audit trail

The run's full audit trail (up to the 5,000 newest events), with:

- search across all fields
- filters by event type, stage and agent
- rows that expand to show every field
- a JSON download

Events with no `Stage` field (artifacts, subagent dispatches, human turns) are attributed to the stage running at the time and shown in grey italics. Phase, session and workflow events stay unattributed.

### Artifacts

Artifacts open in a viewer with formatted markdown (headings, tables, lists, task checkboxes, code blocks and quotes) and pretty-printed JSON. **Raw** shows the source, and links to other artifacts open in the same viewer. Esc closes it.

## How it works

A small Python server reads the project's harness folder and its `aidlc/` record on each request and serves a single-page dashboard.

| Data | File |
|---|---|
| Stage order and ids | `<harness>/tools/data/scope-grid.json` |
| Stage agents, reviewer, condition, outputs | `<harness>/aidlc-common/stages/<phase>/<slug>.md` frontmatter |
| Agent roster and model tiers | `<harness>/agents/aidlc-*.json`, `<harness>/tools/data/agent-tiers.json` |
| Stage status (`[ ] [-] [?] [R] [x] [S]`) | `<record>/aidlc-state.md` |
| Timing, gates, agents, errors | `<record>/audit/*.md` (or `<record>/audit.md`) |
| Artifacts | `<record>/<phase>/<slug>/` |
| Units of work | `<record>/inception/units-generation/unit-of-work-dependency.md`, `<record>/runtime-graph.json` |
| Sessions and engine questions | `aidlc/.aidlc-sessions/` |

`<harness>` is the first of `.kiro`, `.claude`, `.codex`, `.cursor` or `.opencode` found in the project.

| File | Role |
|---|---|
| `server.py` | HTTP server, command-line options, security headers |
| `reader.py` | Reads and parses the AI-DLC files into one status model |
| `insights.py` | Needs-you items, stall and error checks, time accounting and the time-left estimate |
| `units.py` | Planned units and each unit's progress through the per-unit stages |
| `index.html` | The dashboard page and styles (plain HTML and CSS, no build step) |
| `app.js` | The dashboard script, served from the same origin so the Content-Security-Policy can disallow inline scripts |
| `cheatsheet.html`, `cheatsheet.js` | AI-DLC commands cheatsheet (every `/aidlc` chat command and `aidlc` terminal command), served at `/cheatsheet.html` and linked from the dashboard header. It also opens directly from disk. |

## Safety

- **Read-only:** it never writes to the project. You still approve gates and answer questions in the chat.
- **Local only:** it binds to `127.0.0.1` and rejects requests whose `Host` header isn't a loopback name, which guards against DNS rebinding.
- **Contained file access:** the artifact endpoint only serves text files inside the selected run's folder. It rejects `..`, absolute paths and symlinks that point outside.
- **No markup injection:** file content is always rendered as text. The markdown viewer builds page elements directly and never inserts HTML, and a strict Content-Security-Policy is sent.

## Limitations

- **Stall warnings are a hint.** A long code-generation step can also be quiet in the audit trail.
- **Support agents may be missing from the audit trail.** Older runs didn't log support agents unless they were dispatched as subagents.
- **Unit status may be inferred.** Runs without `UNIT_*` events show each unit with its stages' overall status. Team-owned runs (`Unit Ownership: team`) are shown from the same receipts; the dashboard doesn't read the owner column of the engine's Unit Progress table.
- **The time-left estimate is rough.** It assumes the remaining stages take as long as a typical finished one.

## Security

See [SECURITY](SECURITY.md) for the security model and how to report a vulnerability, and [CONTRIBUTING](CONTRIBUTING.md#security-issue-notifications) for more information.

## License

This library is licensed under the MIT-0 License. See the [LICENSE](LICENSE) file.
