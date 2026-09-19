# Architecture and reliability

## Data flow

Local Codex logs -> deterministic event parser -> private SQLite index -> per-task Markdown files.

Quick search queries SQLite. AI-assisted search is a bounded workflow in the skill: the user's current Codex model expands the query, searches locally and reads selected evidence. There is no separate API client. The preference setting controls the skill's behavior, not an automatic background model.

## Supported inputs

- `session_meta` establishes identity, working directory and source.
- Modern `event_msg/item_completed` records with `UserMessage` and `AgentMessage` items are preferred. Case variants are accepted. Final, commentary and unspecified visible answer phases are retained; analysis is excluded.
- Legacy `event_msg/user_message` and `event_msg/agent_message` are the next choice.
- `response_item/message` is a last resort. Known injected user-context wrappers are removed, and a warning marks this less reliable schema. Developer/system roles, tools and reasoning never enter the archive.
- One schema family is selected per file to avoid duplicate representations of the same turn. An upgrade to a stronger family triggers a rebuild of that task.
- Sessions identified as subagents are excluded. Forks are separate tasks, so inherited conversation text can legitimately occur in more than one task.

Codex metadata databases are opened read-only. The parser discovers the latest numbered state database, checks columns and falls back to the session index and session metadata when needed. It does not modify Codex internals. Project assignment changes are applied to the task header; the archive is not a historical record of every project reassignment.

## Incremental protocol

A per-task checkpoint contains source byte offset, observed size, modification time and a hash of the final 256 bytes before the checkpoint. Complete records only are consumed. An incomplete final line is retried when the source changes. Invalid JSON stops at its byte position and reports a warning rather than skipping unknown data.

Source truncation, changed checkpoint bytes, or a same-sized rewrite triggers a task rebuild. This detects typical append/truncate/replace operations; it is not a full-file tamper detector. A rewrite earlier in the file combined with appended bytes and an identical checkpoint tail can evade the shortcut. To force a full reimport into a fresh archive, pass a new `--archive` path. Source logs remain the source of truth.

An OS lock serializes writers and releases automatically on process exit. SQLite commits message and checkpoint changes together. The dirty flag is committed before export, and is cleared only after an atomic Markdown replacement. A subsequent sync repairs interrupted exports. Repeated completed item IDs update existing messages rather than duplicating them.

The tool retains exports for sources no longer present. A missing file can mean deletion, an offline volume or a different host. It is not treated as permission to erase the archive.

## Search boundaries

FTS5 ranks title, project and message text. All query words are tried first, then bounded dictionary-based typo alternatives, then a clearly labeled partial match. Queries are tokenized and quoted instead of being accepted as raw FTS expressions. Filters are parameterized. Results are grouped by task and bounded.

Quick search has no understanding of meaning. The AI option improves paraphrase handling through query expansion and evidence review, but is not exhaustive semantic search. It never uploads the whole archive or computes embeddings. Retrieved content is untrusted evidence, not instructions.

## Hooks and schedules

The installer writes an absolute Python command and a PowerShell-specific command override into the user's Stop hook. It does not modify hook trust records. Background hook success produces `{}`. Failures produce a short advisory warning, never exit code 2 or a blocking decision that could restart the model.

Use the initial full sync to backfill. A missed hook is repaired by the next manual or scheduled full sync. The task database and Markdown timestamps can lag until sync runs. Daily reports are indexes of messages by the computer's local date, not AI summaries. Scheduling after midnight requires an explicit previous date if that is the intended reporting day.

## Known scope limits

Only locally available transcripts are covered. Cloud-only chats, other computers, binary attachments and removed source files cannot be recovered by this tool. The transcript format is an internal Codex format and can change. Unknown schema changes need a parser update and fresh validation. Task links require a compatible Codex desktop client and an existing accessible task ID.
