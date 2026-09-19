# Validation for 0.2.0

40 synthetic tests passed locally on Windows with Python 3.14, including optional file indexing, cache reuse, stale-summary invalidation, opt-in checks, task associations, filename and fuzzy matching, size limits and format extraction. Skill, plugin and package validators passed. File indexing remains off until explicitly enabled and scoped. AI summaries are implemented as a consent-based Codex workflow, not an automatic model service.

# Validation for 0.1.0

Executed locally on Windows with Python 3.14 on 2026-09-19.

- 25 synthetic unit and integration tests passed.
- Codex skill and plugin validators passed.
- Package structure, Python syntax, private-data exclusions and punctuation checks passed.
- Windows scheduling helper passed PowerShell parsing. No real recurring schedule was created as part of validation.
- The actual installed Windows hook command was executed with a real archived transcript path. It returned exit code 0, `{}` on stdout and no stderr in 0.82 seconds, including PowerShell startup.
- Live Codex automatic hook dispatch still requires the user's hook trust and a new session. Direct command validation is not claimed as proof that this approval has occurred.

## Local history smoke test

The import examined 1,399 local log files and retained 613 user-facing tasks with 23,793 request/final-answer messages. Internal subagent sessions were excluded. No parser warnings or pending exports remained. After resolving displayed task names, the Markdown archive occupied about 19.6 MB. These are machine-specific observations, not a storage guarantee.

The initial import took 166 seconds. An unchanged full sync took 0.83 seconds and consumed zero new transcript-body bytes. Metadata enumeration and checkpoint checks still perform small reads; this is not a claim of zero filesystem I/O.

Warm in-process quick searches took approximately 7 to 13 milliseconds. This excludes Python process startup and Codex tool overhead. A deliberately misspelled query found the intended archived task. A normal task query found the intended active task with its displayed name and project.

Task links were separately opened in Codex desktop and confirmed by the user for one active and one archived task. The source package contains no private task IDs or transcript examples.

## Covered failure cases

Tests cover incomplete last records, malformed JSON, repeated message IDs, duplicate log files, canonical log selection, metadata renames, desktop project assignments, archived moves, source truncation, response-only warnings, excluded tool/reasoning/commentary text, subagent exclusion, concurrent sync processes, missing exports, recovery after export failure, bounded context, hook failures, hook preservation, path quoting and external transcript rejection.

AI-assisted search is implemented as the Codex skill's explicit, bounded query-expansion workflow. It is not a deterministic semantic-search model with a measured recall score. Broader retrieval evaluation and the remote cross-platform CI matrix remain release follow-up work.
