# Scheduling without model overhead

Use one or more modes as needed. Sync is idempotent, so a Stop hook and a daily catch-up run can coexist. Do not schedule Codex itself merely to copy messages when a local command is sufficient.

## Windows Task Scheduler

Install the standalone skill, then run the provided `scripts/register-schedule.ps1` helper with `-Mode Hourly` or `-Mode Daily -At 23:00`. It uses the current user's interactive account, limited privileges, no stored password, a no-overlap policy, a 20-minute timeout and catch-up after missed starts. An existing task with the same name is never overwritten implicitly.

The task runs while the account is logged in. Sleeping or logged-out computers cannot guarantee execution at the requested wall-clock time. Check Task Scheduler's Last Run Result and `recall.py status`; a zero result means the command completed without parser warnings. If the Python executable is not discoverable, supply `-Python` with its absolute path.

To remove an installed schedule:

```powershell
powershell -NoProfile -File scripts/register-schedule.ps1 -Mode Hourly -Remove
powershell -NoProfile -File scripts/register-schedule.ps1 -Mode Daily -Remove
```

## Linux and macOS

The same CLI works with cron, systemd timers or launchd. Configure the Python executable and script using absolute paths. For example, a cron entry shape is:

```cron
0 * * * * /absolute/path/to/python3 /absolute/path/to/recall.py sync
0 23 * * * /absolute/path/to/python3 /absolute/path/to/recall.py end-day
```

Replace the paths with the actual installation paths. Set `CODEX_HOME` and `CODEX_RECALL_HOME` explicitly in the scheduler if you use custom locations. Keep stderr or the scheduler's failure reporting visible so a corrupt transcript does not become an invisible stale archive. This repository does not install a Unix scheduler automatically.

## Codex scheduled task

If you prefer Codex's scheduler, use its normal automation UI or tools to create a schedule with this prompt, substituting the absolute script path:

> Run the installed Codex Recall helper with `end-day`. Do not read or summarize transcript contents and do not run AI search. Stay quiet on success. Report only sync errors or parser warnings requiring attention.

This method has the cost of waking the selected Codex model. The command itself remains model-free. There is no reason to choose a stronger model for this maintenance operation; use the least costly model your scheduler supports and validate its tool execution once.

If the user has not specified a schedule, provide the supported options without creating a recurring task on their behalf.
