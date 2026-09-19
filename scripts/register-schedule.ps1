param(
    [ValidateSet('Hourly', 'Daily')][string]$Mode = 'Daily',
    [string]$At = '23:00',
    [string]$Python = (Get-Command python -ErrorAction Stop).Source,
    [string]$CodexHome = $(if ($env:CODEX_HOME) { $env:CODEX_HOME } else { Join-Path $env:USERPROFILE '.codex' }),
    [switch]$Remove
)
$ErrorActionPreference = 'Stop'
$taskName = "Codex Recall $Mode"
if ($Remove) {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
    exit
}
$recallScript = Join-Path $CodexHome 'skills/codex-recall/scripts/recall.py'
if (-not (Test-Path -LiteralPath $recallScript)) { throw 'Install the standalone skill first using scripts/install.py.' }
if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) { throw "Task already exists: $taskName. Remove it explicitly before replacing it." }
if ($Mode -eq 'Daily') {
    $parsedTime = [DateTime]::ParseExact($At, 'HH:mm', [Globalization.CultureInfo]::InvariantCulture)
    $trigger = New-ScheduledTaskTrigger -Daily -At $parsedTime
    $operation = 'end-day'
} else {
    $trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Hours 1)
    $operation = 'sync'
}
$argsText = '"{0}" --home "{1}" {2}' -f $recallScript, $CodexHome, $operation
$action = New-ScheduledTaskAction -Execute $Python -Argument $argsText
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 20)
$identity = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$principal = New-ScheduledTaskPrincipal -UserId $identity -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Description 'Incremental local Codex text archive. No model or API calls.'
