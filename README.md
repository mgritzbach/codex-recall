# Codex Recall

Find the conversation you remember, even when you cannot remember its title.

Codex Recall keeps a local Markdown archive of your requests and Codex's visible answers. Search it with a fast Python word search, or let Codex interpret a small set of matches when you choose AI-assisted search. Results include links that open the original task, including archived tasks on supported Codex desktop builds.

**No API key. No model needed for recording. Core features use only the Python standard library. No automatic uploads.**

## Choose your search budget

| Mode | What happens | Model usage |
| --- | --- | --- |
| Quick | SQLite full-text ranking, case/accent normalization, typo suggestions and partial-word fallback | None in the search engine |
| AI-assisted | Codex tries up to three related queries and examines a few bounded excerpts | Uses your current Codex conversation's model |

The skill asks which mode you prefer. If quick search is not satisfactory, it asks before escalating. You can save a continuing preference or choose each time. AI-assisted search is query expansion and evidence review, not a precomputed semantic embedding index. It may still miss a discussion with no useful textual anchor.

Running the Python commands yourself uses zero model tokens. Asking Codex to run them has the normal cost of that conversation. This plugin does not silently start another model, assume a cheap model is available, or charge a separate API account.

## Install

Requirements: Python 3.10 or newer with SQLite FTS5, local Codex JSONL history, and disk space for a text archive and search index. Windows, macOS and Linux code paths are included. See the validation notes before claiming platform coverage.

Download this repository, open a terminal in its directory, and run:

```text
python scripts/install.py
python skills/codex-recall/scripts/recall.py sync
```

On systems where Python is named `python3`, use that command instead. The installer copies the standalone skill to `$CODEX_HOME/skills/codex-recall` (normally `~/.codex/skills/codex-recall`). Start a new Codex conversation or restart the app to discover it. Ask:

```text
Use $codex-recall to find the conversation about email formatting.
```

The repository also contains `.codex-plugin/plugin.json` and `skills/` for native plugin distribution. Install either the standalone skill or the plugin, not both. Hook installation is an explicit setup step so the correct Python executable and absolute paths can be recorded on each computer. No shell-variable substitution in a plugin cache path is required.

## Three ways to record

### A. After every request

```text
python scripts/install.py --enable-hook
```

This configures a background `Stop` command hook in `$CODEX_HOME/hooks.json`. It preserves unrelated hooks and makes a backup before changing the file. The hook runs after the answer and syncs only that transcript. Success returns empty JSON, not extra model context. A failure reports a short warning and never asks Codex to continue generating.

Codex requires new hooks to be reviewed and trusted. Review the hook in Codex's hooks controls (`/hooks` in the CLI), then start a fresh session. The installer does not bypass that trust mechanism. Run the initial full sync before enabling the hook on very large histories. Manual or scheduled sync catches up if a hook was missed, a session was interrupted, or the app exited before the hook completed.

### B. Scheduled updates

Have the operating system run this command with absolute paths:

```text
python /absolute/path/to/recall.py sync
```

This launches Python only, not Codex. The Windows helper supports hourly updates:

```powershell
powershell -NoProfile -File scripts/register-schedule.ps1 -Mode Hourly
```

Scheduling is opt-in. Downloading or installing the skill does not create a scheduled task. A Codex scheduled task can also invoke the same command, but waking a Codex agent has model overhead, so an OS schedule is the cheaper choice. More details are in `docs/scheduling.md`.

### C. End of day

```text
python skills/codex-recall/scripts/recall.py end-day
```

This syncs and writes a small index of that local calendar day's tasks with links and message counts. It does not ask a model to summarize or duplicate the conversation text. Use `--date YYYY-MM-DD` for a specific day. A daily Windows schedule defaults to 23:00 local time:

```powershell
powershell -NoProfile -File scripts/register-schedule.ps1 -Mode Daily -At 23:00
```

## Quick search from a terminal

```text
python skills/codex-recall/scripts/recall.py search "email formating"
python skills/codex-recall/scripts/recall.py search "refresh tokens" --project "Website"
python skills/codex-recall/scripts/recall.py search "tailoring" --archived yes
python skills/codex-recall/scripts/recall.py search "Germany" --since 2026-09-01
python skills/codex-recall/scripts/recall.py prefer quick
python skills/codex-recall/scripts/recall.py prefer ai
python skills/codex-recall/scripts/recall.py prefer ask
python skills/codex-recall/scripts/recall.py status
```

Search returns JSON with a task title, project, timestamp, excerpt, archive status, message ID, Markdown path and `codex://threads/<task-id>` URL. A normal or archived task link was verified in Codex desktop during development. These are local app links, not public share links. Browser, CLI, other-host and deleted-task behavior can differ. Search includes archives automatically; opening a result does not unarchive it.

Search does not silently refresh the archive. Check `last_sync`, run `sync` if needed, or keep the hook/scheduler enabled. The skill handles freshness before searching. `--since` compares UTC message dates; daily indexes use the computer's local calendar date.

## Exactly what is recorded

Each `tasks/<task-id>.md` contains:

- The task name, project, task ID, archived status and original task link.
- Your request text and Codex's visible answer text, preserving wording and Markdown.
- The recorded timestamp with timezone and weekday for each message. Unknown timestamps are labeled.

It excludes tool calls/results, hidden reasoning, system/developer prompts, binary attachments, image payloads and subagent tasks. Text inside an attached document is not extracted by the chat recorder; use the optional file index for selected documents. A text-only request accompanying an image is retained. Project names come from Codex metadata; missing names are labeled instead of inferred from directory names.

Modern display events are preferred because model-input records can contain injected context. Older user/agent events are supported. A response-only fallback is supported with a visible coverage warning and conservative filtering. Unsupported or corrupt records are reported; the tool does not pretend an incomplete export is complete. Read `docs/architecture.md` for boundaries.

Version 0.2 includes visible intermediate answers, including progress text, so substantive findings are not lost. If upgrading from 0.1, run `sync --rebuild` once to backfill them.

## Advanced: selected file contents

File indexing is **off by default**, with a separate opt-in for AI summaries. You choose the files or folder scope. Nothing scans your disk automatically.

```text
python skills/codex-recall/scripts/recall.py files enable
python skills/codex-recall/scripts/recall.py files add "/selected/report.md" --task TASK_ID
python skills/codex-recall/scripts/recall.py files add "/selected/folder" --recursive
python skills/codex-recall/scripts/recall.py search "observatory"
python skills/codex-recall/scripts/recall.py files status
```

Replace the path and optional task ID with actual values. Basic mode extracts text, headings, frequent keywords and a labeled extractive lead summary locally. Supported formats are UTF-8 TXT, Markdown, RST, CSV, JSON, YAML, HTML, DOCX and PPTX. PDF text extraction requires the optional `pypdf` package; it is never installed automatically. Scanned PDFs and images need OCR, which is not included. XLSX and legacy binary Office formats are not supported in this release.

Normal `sync`, the after-request hook and `end-day` refresh registered files when indexing is enabled. Unchanged files are skipped using size and modification time; changed files are hashed. Matching content in the same extraction format is reused across paths and tasks. A deliberately edited file that preserves both size and timestamp can evade that shortcut; remove and re-add its registration to force another read. Folder registration selects existing files, not future files.

AI summaries use your current Codex conversation, with your consent. `files ai-on` permits the skill to summarize a bounded excerpt and save a cached summary and keywords. It does not launch an AI service. Background maintenance never uses a model. Changed file content invalidates its old summary. The skill's detailed procedure is in `skills/codex-recall/references/files.md`.

Limits: 5 MB per file, 200 supported files per folder registration, 20 MB of expanded Office XML, 300 PDF pages and 200,000 extracted characters per file. Large text is marked truncated. Search returns task links only for explicit task associations, and unassociated files are still searchable globally. File results support project/archive filters through those associations; `--since` currently limits results to conversations.

```text
python skills/codex-recall/scripts/recall.py files disable
python skills/codex-recall/scripts/recall.py files ai-off
python skills/codex-recall/scripts/recall.py files remove "/selected/report.md"
```

Disabling retains the private cache but hides file results. Removing a registration purges its extracted content and summary when no other file references that content. Original files are never modified. File caches live alongside the private chat archive, outside this repository.

## Storage and efficiency

Private data defaults to `$CODEX_HOME/chat-recall`, outside the repository. Set `CODEX_RECALL_HOME` or pass `--archive` before the subcommand to use another location. Set `CODEX_HOME` or `--home` for a different local Codex profile.

- The initial sync reads the local history once.
- Later runs read only new complete JSONL records, using stored offsets and a short integrity check.
- Unchanged Markdown files are not rewritten. Changed tasks are atomically replaced.
- Task titles and project assignments are refreshed without rereading conversation bodies.
- SQLite transactions, an OS writer lock and persistent export flags protect against concurrent runs and crashes.
- No service, GPU, embedding model, vector database or model session is kept running.
- Search defaults to five task matches; AI-assisted retrieval is deliberately bounded.

The Markdown archive and SQLite index both store text, so total disk use is greater than Markdown alone. Code makes no network requests. The archive is not encrypted and faithfully preserves any sensitive text in messages. Deleting a source conversation does not automatically delete its existing export; this is a retained archive. Keep the private archive out of Git and cloud sync unless that is your intention.

## Development and sharing

```text
python -m unittest discover -s tests -v
python scripts/check_package.py
```

The repository includes a native Codex manifest, a focused skill, Python helpers, synthetic tests, GitHub Actions, MIT license, contribution guidance, security notes and a changelog. Share the source repository or its release ZIP, never your local `chat-recall` directory. There is no hosted service or publisher account embedded in this package.

All 40 tests passed locally on Windows with Python 3.14 and in GitHub Actions on Windows, Linux and macOS with Python 3.10 and 3.13. The README is not a claim of marketplace approval.

Design references: [OpenAI Plugins](https://github.com/openai/plugins), [Superpowers](https://github.com/obra/superpowers), and the [Codex hooks documentation](https://learn.chatgpt.com/docs/hooks). The deprecated [OpenAI skills catalog](https://github.com/openai/skills) points to the plugins repository. These informed packaging and documentation; this project does not copy their agent workflows or claim their endorsement.

## Remove or update

Run `python scripts/install.py` from a newer release to update the managed standalone skill. Re-run with `--enable-hook` if paths or Python changed, then review the changed hook in Codex. To remove just the hook:

```text
python scripts/install.py --remove-hook
```

Remove an installed Windows schedule with the same scheduling helper and `-Remove`. Remove the standalone skill directory to uninstall it. Your private archive is retained until you delete it deliberately.
