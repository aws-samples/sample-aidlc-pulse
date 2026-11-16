# Changelog

All notable changes to AI-DLC Pulse. Releases are published to [aws-samples/sample-aidlc-pulse](https://github.com/aws-samples/sample-aidlc-pulse). The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow [Semantic Versioning](https://semver.org/). The version is set in `version.py` and shown in the dashboard header.

## [1.1.0] - 2026-11-11

Tested against AI-DLC 2.11 (release 2.11.3). Changes since the 1.0.0 sample release.

### Added

- **Decisions tab:** the run's decision record, read from every stage's `*-questions.md`, including per-unit ones. Each question shows who asked it, the options (with the recommended one marked), the chosen option, the answer text and how it was given. Unanswered questions are highlighted and counted on the tab, and the "Approval needed" card links to them.
- **AI-DLC version check:** the header shows the project's AI-DLC release (from `<harness>/tools/data/aidlc-stamp.json`). A warning appears when it isn't a release Pulse is tested against.
- **`--harness` option** to choose which harness folder to read. A warning appears when several are installed, because AI-DLC records don't say which harness ran them.
- **Setup warnings banner** on every tab, also shown when the stage order had to be guessed.
- **Space-level audit events** (`aidlc/spaces/<space>/intents/audit/`, written when no single intent is selected) appear in the Audit trail with a `space` tag and count as activity for stall detection.
- **Per-unit artifacts** under `construction/<unit>/<stage>/` are listed with their stage and tagged with their unit.
- **Project layout:** Python modules moved to `src/` and the browser files to `src/static/`, next to `tests/`. The start command is unchanged (`python3 server.py --project …`); the root `server.py` is now a small launcher and the HTTP server is `src/http_server.py`.
- **Project folder prompt:** `python3 server.py` without `--project` asks for the AI-DLC project folder (paste or drag it in; Enter uses the current folder if it's a project). `python3 server.py <folder>` also works.
- **Version number:** Pulse's own version in the dashboard header and the HTTP `Server` header.
- **Tests and CI:** 60+ standard-library `unittest` tests on synthetic projects (reader, timing, units, decisions, server, strict-CSP checks), and CI on Python 3.11 and 3.13 (GitHub Actions). A CI job checks that every audit event name Pulse matches still exists in the tested AI-DLC release, and a weekly job checks the newest release.

### Changed

- **Stage order, numbers and names** come from `<harness>/tools/data/stage-graph.json`, so stage ids match AI-DLC's own (for example code-generation is 3.5, not 3.2). Older installs fall back to `scope-grid.json`, then stage-file frontmatter.
- **Stage attribution** for events with no `Stage` field uses, in order: the `Stage slug` field, the `File` path inside the run (which also gives the unit), the event's `Unit`, then the single running stage. When parallel units are in different stages, the event is left unattributed ("one of N running") instead of being credited to the stage that started last.
- **Look:** the AWS 2026 brand palette. A Squid Ink header and tabs with the brand accent bar, brand blue, purple, pink and orange accents, and heavier headings. Status colours are deeper shades of the brand hues, so text passes WCAG AA (4.5:1) on its background and bars pass 3:1; "skipped" and "not in scope" labels went from 2.4:1 to 5.1:1. Amazon Ember and Amazon Ember Mono are used when installed; no font files are shipped.
- **Caching:** files are cached by modification time and size (128 MiB cap), so each poll only re-reads what changed.
- **Event names** Pulse depends on are declared in one place (`src/events.py`).

### Fixed

- **Revision count was always 0.** It matched substrings that no AI-DLC event contains. It now counts `GATE_REJECTED`.
- **Audit events were sorted as text,** which mis-ordered timestamps with offsets or fractional seconds. They're now sorted by parsed time.
- **Artifact files with spaces or non-ASCII letters** in their names couldn't be opened.
- Scanner findings: unreachable code in the timing module, an implicit string concatenation, a backreferencing regex and a dynamic method lookup.

### Security

- **Content-Security-Policy is now `style-src 'self'`** with no `unsafe-inline` anywhere. Styles moved to `app.css` and `cheatsheet.css`, and element styles are set through the CSSOM. Tests enforce no inline styles, scripts, event handlers or `innerHTML`.
- **Artifact path checks** now allow spaces and non-ASCII letters but refuse hidden (dot) segments, control characters and separators, as well as `..`, absolute paths and symlinks that leave the run. Covered by tests.
- The horizontal-rule check in the markdown viewer no longer uses a backreferencing regex, so it's plainly linear in the line length.
- The markdown viewer's HTML-comment removal could leave a comment behind when one was nested inside a broken opener ("incomplete multi-character sanitization"). It's now a linear scan that re-checks its output after every character, and an unclosed comment runs to the end of the text, as in HTML. The viewer never inserts HTML, so the leftover could only ever appear as text.
- The query-string limit was raised from 512 to 2,048 characters, to fit percent-encoded non-ASCII artifact paths.

## [1.0.0] - 2026-11-09

First release, published as [sample-aidlc-pulse `dfd2786`](https://github.com/aws-samples/sample-aidlc-pulse/commit/dfd2786). Tested against AI-DLC 2.11 (release 2.11.3).

### Added

- **Overview:** progress, current and next stage, and a lifecycle board of all 33 stages coloured by status. Stage detail shows lead, support and reviewer agents, timings and artifacts.
- **Needs you:** open approval gates (with their questions file), pending AI-DLC questions, paused units, stalled stages and new errors. Optional desktop notifications and a count in the tab title.
- **Workflow:** an animated lifecycle flow with a replay mode, and the units-of-work dependency graph.
- **Units of work:** progress, a unit × stage grid and paused units, from `UNIT_*` audit receipts and `construction-unit` checkpoints.
- **Timeline:** a Gantt chart splitting each stage into agent work, waiting on you and rework, with time per phase, time per unit and a rough estimate of time left. Per-unit Construction work is timed per unit, and on an idle run open agent and rework segments stop at the last audit event instead of running on with the clock. A paused unit stops counting when it paused.
- **Agents:** which agents are working, have worked, are coming up, or aren't used in the run.
- **Audit trail:** the full event log with search, filters and JSON export.
- **Artifacts:** a markdown and JSON viewer for every file a stage produced.
- **Commands cheatsheet** for AI-DLC 2.11.3 at `/cheatsheet.html`.
- Read-only, loopback-only server with Host-header checks, a Content-Security-Policy and contained artifact access. Python standard library only.
- `SECURITY.md` with the security model and how to report a vulnerability.
- A demo walkthrough video in the README.
