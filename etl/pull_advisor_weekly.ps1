<#
.SYNOPSIS
  Weekly: find the Soybean & Corn Advisor report in Outlook and load its estimates into the WASDE dashboard.

.DESCRIPTION
  Looks in the Inbox for the newest message in the last $LookbackDays whose subject matches
  "Soybean & Corn Report" and that carries a .docx attachment (it arrives forwarded, e.g. from Brent Baker),
  saves the attachment to a temp folder, runs etl\load_advisor_report.py on it, then deletes the copy.
  The loader replaces that report date's rows, so running it several times a week is harmless.
  Internal subscription material: nothing is kept on disk after the run.

  Runs as the signed-in user through Outlook COM (same approach as morning_wire\fetch_wire.ps1), so classic
  Outlook has to be able to open; COM only sees classic Outlook's synced mailbox.

.EXAMPLE
  powershell -File etl\pull_advisor_weekly.ps1 -DryRun     # parse and print, write nothing
#>
param(
    [int]$LookbackDays = 8,
    [switch]$DryRun
)
$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$logDir = Join-Path $env:LOCALAPPDATA 'advisor_weekly'
$tmp = Join-Path $env:TEMP 'advisor_weekly'
New-Item -ItemType Directory -Force $logDir, $tmp | Out-Null
$log = Join-Path $logDir 'advisor_weekly.log'

function Log([string]$m) { "$(Get-Date -Format 's')  $m" | Tee-Object -FilePath $log -Append | Write-Host }

try {
    $ol = New-Object -ComObject Outlook.Application
    $inbox = $ol.GetNamespace('MAPI').GetDefaultFolder(6)
    $since = (Get-Date).AddDays(-$LookbackDays).ToString('MM/dd/yyyy hh:mm tt', [Globalization.CultureInfo]::InvariantCulture)
    $best = $null
    foreach ($m in $inbox.Items.Restrict("[ReceivedTime] >= '$since'")) {
        if ($m.Subject -notmatch 'Soybean\s*&\s*Corn\s+Report') { continue }
        $has = $false
        foreach ($a in $m.Attachments) { if ($a.FileName -match '\.docx') { $has = $true } }
        if ($has -and ($null -eq $best -or $m.ReceivedTime -gt $best.ReceivedTime)) { $best = $m }
    }
    if ($null -eq $best) { Log "No Soybean & Corn Report with a .docx in the last $LookbackDays days"; exit 0 }

    $docx = $null
    foreach ($a in $best.Attachments) {
        if ($a.FileName -match '\.docx') {
            $docx = Join-Path $tmp 'advisor_report.docx'
            $a.SaveAsFile($docx)
            break
        }
    }
    Log "Found: '$($best.Subject)' received $($best.ReceivedTime)"
    $args = @((Join-Path $repo 'etl\load_advisor_report.py'), $docx)
    if ($DryRun) { $args += '--dry-run' }
    $out = & python @args 2>&1 | Out-String
    Log ($out.Trim())
    if ($LASTEXITCODE -ne 0) { throw "loader exited with $LASTEXITCODE" }
}
catch {
    Log "FAILED: $($_.Exception.Message)"
    exit 1
}
finally {
    Remove-Item (Join-Path $tmp '*') -Force -ErrorAction SilentlyContinue   # don't keep the subscription file
}
