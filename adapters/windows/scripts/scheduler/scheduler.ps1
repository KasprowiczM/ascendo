<#
.SYNOPSIS
  Windows Task Scheduler driver. Manages Ascendo's own scheduled tasks
  under the Task Scheduler folder \Ascendo\.

.DESCRIPTION
  Actions:
    -Action install   : register or overwrite a scheduled task. Reads the
                        ScheduleSpec from -PayloadPath as JSON.
    -Action uninstall : remove a scheduled task by name.
    -Action list      : enumerate Ascendo-owned tasks; emit JSON array.
    -Action trigger   : run a registered task immediately.

  ScheduleSpec.expression syntax (best-effort parser):
    "DAILY HH:MM"             -> /SC DAILY /ST HH:MM
    "WEEKLY <DAY> HH:MM"      -> /SC WEEKLY /D <DAY> /ST HH:MM
    "HOURLY HH:MM"            -> /SC HOURLY /MO 1 /ST HH:MM
    Anything else passed through verbatim as schtasks args (advanced).
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)] [ValidateSet('install','uninstall','list','trigger')] [string] $Action,
    [Parameter(Mandatory = $true)] [string] $OutputPath,
    [string] $PayloadPath
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$AscendoFolder = '\Ascendo\'   # All our tasks live under \Ascendo\<name>

function Read-Payload {
    if (-not $PayloadPath) { return $null }
    if (-not (Test-Path $PayloadPath)) { return $null }
    $raw = Get-Content -LiteralPath $PayloadPath -Raw -Encoding UTF8
    if (-not $raw) { return $null }
    return ($raw | ConvertFrom-Json)
}

function Write-Output-Json {
    param($Object)
    $outputDir = Split-Path -Parent $OutputPath
    if ($outputDir -and -not (Test-Path $outputDir)) {
        $null = New-Item -ItemType Directory -Force -Path $outputDir
    }
    $json = $Object | ConvertTo-Json -Depth 6
    [System.IO.File]::WriteAllText($OutputPath, $json, [System.Text.UTF8Encoding]::new($false))
}

function Resolve-AscendoExe {
    # Prefer the installed CLI shim; fall back to ``python -m ascendo``.
    $cli = Get-Command 'ascendo' -ErrorAction SilentlyContinue
    if ($cli) { return @($cli.Source) }
    $py = Get-Command 'python' -ErrorAction SilentlyContinue
    if (-not $py) { $py = Get-Command 'pwsh' -ErrorAction SilentlyContinue }  # absurd fallback
    if (-not $py) { throw "no ``ascendo`` on PATH and no ``python`` either" }
    return @($py.Source, '-m', 'ascendo')
}

function Convert-ExpressionToArgs {
    param([string] $Expression)
    if (-not $Expression) { return @('/SC', 'WEEKLY', '/D', 'SUN', '/ST', '03:00') }
    $parts = $Expression.Trim() -split '\s+'
    switch -Wildcard ($parts[0].ToUpperInvariant()) {
        'DAILY'   {
            $time = if ($parts.Count -ge 2) { $parts[1] } else { '03:00' }
            return @('/SC', 'DAILY', '/ST', $time)
        }
        'WEEKLY'  {
            $day  = if ($parts.Count -ge 2) { $parts[1].ToUpperInvariant() } else { 'SUN' }
            $time = if ($parts.Count -ge 3) { $parts[2] } else { '03:00' }
            return @('/SC', 'WEEKLY', '/D', $day, '/ST', $time)
        }
        'MONTHLY' {
            $time = if ($parts.Count -ge 2) { $parts[1] } else { '03:00' }
            return @('/SC', 'MONTHLY', '/ST', $time)
        }
        'HOURLY'  {
            $time = if ($parts.Count -ge 2) { $parts[1] } else { '00:00' }
            return @('/SC', 'HOURLY', '/MO', '1', '/ST', $time)
        }
        'MINUTE'  {
            return @('/SC', 'MINUTE', '/MO', if ($parts.Count -ge 2) { $parts[1] } else { '60' })
        }
        default {
            # Pass through (advanced users supply full schtasks args).
            return ($parts)
        }
    }
}

function Invoke-Install {
    param($Spec)
    if (-not $Spec) { throw "install requires payload" }
    $name    = [string] $Spec.name
    $expr    = [string] $Spec.expression
    $profile = if ($Spec.profile) { [string] $Spec.profile } else { 'full' }
    $enabled = if ($Spec.PSObject.Properties['enabled']) { [bool] $Spec.enabled } else { $true }
    $desc    = if ($Spec.description) { [string] $Spec.description } else { "Ascendo scheduled run ($profile)" }

    $taskPath = "$AscendoFolder$name"
    $exeArgv  = Resolve-AscendoExe
    # Quote the action so schtasks treats the entire command as one TR.
    $tr = '"' + ($exeArgv -join '" "') + '"' + " run --profile $profile"

    $schArgs = @('/Create', '/F', '/RL', 'HIGHEST', '/TN', $taskPath, '/TR', $tr) + (Convert-ExpressionToArgs -Expression $expr)
    & schtasks.exe @schArgs | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "schtasks /Create failed (exit $LASTEXITCODE)" }

    if (-not $enabled) {
        & schtasks.exe '/Change' '/TN' $taskPath '/DISABLE' | Out-Null
    }

    Write-Output-Json -Object @{ name = $name; expression = $expr; profile = $profile; enabled = $enabled; description = $desc }
}

function Invoke-Uninstall {
    param($Spec)
    if (-not $Spec -or -not $Spec.name) { throw "uninstall requires .name" }
    $taskPath = "$AscendoFolder$([string] $Spec.name)"
    & schtasks.exe '/Delete' '/F' '/TN' $taskPath 2>$null | Out-Null
    Write-Output-Json -Object @{ ok = $true; name = $Spec.name }
}

function Invoke-List {
    $items = @()
    try {
        $tasks = Get-ScheduledTask -TaskPath $AscendoFolder -ErrorAction Stop
    } catch {
        $tasks = @()
    }
    foreach ($t in $tasks) {
        $action = $t.Actions | Select-Object -First 1
        $tr     = if ($action) { "$($action.Execute) $($action.Arguments)" } else { '' }
        # Profile heuristic: pull it out of the TR.
        $profile = if ($tr -match '--profile\s+(\S+)') { $matches[1] } else { 'full' }
        # Expression: reconstruct from triggers (best effort).
        $trig = $t.Triggers | Select-Object -First 1
        $expr = ''
        if ($trig) {
            switch ($trig.PSObject.TypeNames[0]) {
                'Microsoft.Management.Infrastructure.CimInstance#MSFT_TaskWeeklyTrigger' {
                    $day = ($trig.DaysOfWeek -as [string[]])[0]
                    $time = if ($trig.StartBoundary) { ([DateTime]$trig.StartBoundary).ToString('HH:mm') } else { '03:00' }
                    $expr = "WEEKLY $day $time"
                }
                'Microsoft.Management.Infrastructure.CimInstance#MSFT_TaskDailyTrigger' {
                    $time = if ($trig.StartBoundary) { ([DateTime]$trig.StartBoundary).ToString('HH:mm') } else { '03:00' }
                    $expr = "DAILY $time"
                }
                default { $expr = '' }
            }
        }
        $items += [pscustomobject]@{
            name        = $t.TaskName
            expression  = $expr
            profile     = $profile
            enabled     = ($t.State -ne 'Disabled')
            description = $t.Description
        }
    }
    Write-Output-Json -Object $items
}

function Invoke-Trigger {
    param($Spec)
    if (-not $Spec -or -not $Spec.name) { throw "trigger requires .name" }
    $taskPath = "$AscendoFolder$([string] $Spec.name)"
    & schtasks.exe '/Run' '/TN' $taskPath | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "schtasks /Run failed (exit $LASTEXITCODE)" }
    Write-Output-Json -Object @{ ok = $true; name = $Spec.name }
}

# ── Dispatch ──────────────────────────────────────────────────────────────
$payload = Read-Payload
switch ($Action) {
    'install'   { Invoke-Install   -Spec $payload }
    'uninstall' { Invoke-Uninstall -Spec $payload }
    'list'      { Invoke-List }
    'trigger'   { Invoke-Trigger   -Spec $payload }
}
exit 0
