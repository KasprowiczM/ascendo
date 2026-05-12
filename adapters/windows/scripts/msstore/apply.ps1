<#
.SYNOPSIS
  Microsoft Store apply phase. Runs `winget upgrade --source msstore` per
  package id and records each result in the sidecar.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)] [string] $RunId,
    [Parameter(Mandatory)] [ValidateSet('cli','scheduler','dashboard','plugin','unknown')] [string] $Trigger,
    [Parameter(Mandatory)] [Alias('Profile')] [string] $ProfileName,
    [Parameter(Mandatory)] [string] $OutputDir,
    [Parameter()] [switch] $DryRun,
    [Parameter()] [string] $ItemFilter = ''
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$LibDir    = Join-Path (Split-Path -Parent (Split-Path -Parent $ScriptDir)) 'lib'
Import-Module (Join-Path $LibDir 'AscendoJson.psm1')   -Force -DisableNameChecking
Import-Module (Join-Path $LibDir 'AscendoWinget.psm1') -Force -DisableNameChecking

# Local helpers (these aren't exported from AscendoWinget.psm1; each script
# that needs them defines its own minimal version).
function _Get-WingetVersionString {
    try {
        $v = (& winget --version 2>$null) -as [string]
        if ($v -and $v.Trim()) { return $v.Trim() }
    } catch {}
    return 'unknown'
}
function _Get-WingetBinaryPathString {
    try {
        $cmd = Get-Command winget -ErrorAction SilentlyContinue
        if ($cmd -and $cmd.Source) { return [string]$cmd.Source }
    } catch {}
    return $null
}

function _Get-StderrTailExcerpt {
    # Parity with macOS apply.sh (Sesja 34): last 12 non-empty stderr
    # lines, capped at 1500 chars, surfaced as a sidecar message so
    # operators see the actual failure reason (registry 404, EACCES,
    # MAU/Store auth, etc.) instead of bare winget exit codes.
    [CmdletBinding()] [OutputType([string])]
    param([Parameter(Mandatory)] [string] $StderrFile,
          [int] $MaxLines = 12, [int] $MaxChars = 1500)
    if (-not (Test-Path -LiteralPath $StderrFile)) { return '' }
    try {
        $lines = Get-Content -LiteralPath $StderrFile -ErrorAction SilentlyContinue |
                 Where-Object { $_ -and $_.Trim() } |
                 Select-Object -Last $MaxLines
        if (-not $lines) { return '' }
        $joined = ($lines -join "`n")
        if ($joined.Length -gt $MaxChars) {
            $joined = $joined.Substring($joined.Length - $MaxChars)
        }
        return $joined
    } catch { return '' }
}

function _Invoke-MsStoreUpgrade {
    # Synchronous winget upgrade --source msstore wrapper that captures
    # stdout + stderr to separate temp files via Start-Process. Returns
    # an object with ExitCode + StderrExcerpt.
    [CmdletBinding()] [OutputType([pscustomobject])]
    param([Parameter(Mandatory)] [string] $PackageId)

    $argv = @(
        'upgrade','--source','msstore','--id',$PackageId,'--exact',
        '--accept-source-agreements','--accept-package-agreements',
        '--silent','--disable-interactivity'
    )

    $wingetTarget = 'winget'
    $wingetCmd = Get-Command -Name 'winget' -ErrorAction SilentlyContinue
    if ($null -ne $wingetCmd -and $wingetCmd.Source) { $wingetTarget = [string]$wingetCmd.Source }

    $stdoutFile = $null; $stderrFile = $null
    $code = -1; $excerpt = ''
    try {
        $stdoutFile = [System.IO.Path]::GetTempFileName()
        $stderrFile = [System.IO.Path]::GetTempFileName()
        Write-AscendoStreamLine -Text (">>> winget {0}" -f ($argv -join ' '))
        $proc = Start-Process -FilePath $wingetTarget -ArgumentList $argv `
                              -RedirectStandardOutput $stdoutFile `
                              -RedirectStandardError  $stderrFile `
                              -NoNewWindow -Wait -PassThru -ErrorAction Stop
        $code = [int]$proc.ExitCode
        # Mirror combined stdout+stderr into Run Center's live SSE feed
        # so the operator sees msstore output as it happens (parity with
        # macOS web/apply.sh _stream_tee infrastructure).
        Write-AscendoStreamFile -Path $stdoutFile
        Write-AscendoStreamFile -Path $stderrFile
        if ($code -ne 0) { $excerpt = _Get-StderrTailExcerpt -StderrFile $stderrFile }
    } catch {
        $code = -1
        $excerpt = $_.Exception.Message
        Write-AscendoStreamLine -Text ("[error] msstore apply spawn threw: {0}" -f $excerpt)
    }
    finally {
        foreach ($p in @($stdoutFile, $stderrFile)) {
            if ($p -and (Test-Path -LiteralPath $p)) {
                Remove-Item -LiteralPath $p -ErrorAction SilentlyContinue
            }
        }
    }

    return [pscustomobject]@{ ExitCode = $code; StderrExcerpt = $excerpt }
}


$sidecar = $null
$prevEnc = $null
try {
    $_sidecarArgs = @{
        RunId = $RunId; Trigger = $Trigger; ProfileName = $ProfileName
        DryRun = [bool]$DryRun; Phase = 'apply'; Category = 'msstore'
        ToolName = 'winget'; ToolVersion = (_Get-WingetVersionString)
    }
    $sidecar = New-Sidecar @_sidecarArgs
    $prevEnc = Initialize-WingetEnvironment

    $filterIds = @()
    if ($ItemFilter -and $ItemFilter.Trim() -ne '') {
        $filterIds = @($ItemFilter -split ',' | ForEach-Object { $_.Trim() } | Where-Object { $_ })
    }

    $upgradable = @()
    try { $_all = @(Get-WingetUpgradable -ErrorAction Stop); $upgradable = @($_all | Where-Object { $_.PSObject.Properties['Source'] -and $_.Source -and ($_.Source -ieq 'msstore') }) } catch {
        Add-SidecarMessage -Sidecar $sidecar -Level 'warn' `
            -Text ("apply enumeration failed: {0}" -f $_.Exception.Message)
    }

    # Watchdog heartbeat — mirror Ubuntu Sesja 55 work. Skipped on
    # dry-run (no real winget upgrade -> no silent window). See
    # Start-AscendoHeartbeat in AscendoJson.psm1.
    $hb = $null
    try {
        if (-not $DryRun) {
            $hb = Start-AscendoHeartbeat -IntervalSeconds 10 `
                -Label ("msstore upgrade ({0} package(s))" -f $upgradable.Count)
        }

    foreach ($u in $upgradable) {
        if (-not $u.Id) { continue }
        if ($filterIds.Count -gt 0 -and ($filterIds -notcontains $u.Id)) { continue }

        $curVersion = $null
        if ($u.PSObject.Properties['Version']  -and $u.Version)  { $curVersion = [string]$u.Version }
        $tgtVersion = $null
        if ($u.PSObject.Properties['Available'] -and $u.Available) { $tgtVersion = [string]$u.Available }

        $itemArgs = @{
            Sidecar = $sidecar; Id = [string]$u.Id; Name = [string]$u.Name
            Category = 'msstore'; SourceType = 'msstore'; SourceFeed = 'msstore'
        }
        if ($curVersion) { $itemArgs['CurrentVersion'] = $curVersion }
        if ($tgtVersion) { $itemArgs['TargetVersion']  = $tgtVersion }

        if ($DryRun) {
            $itemArgs['Status'] = 'planned'
            [void](Add-SidecarItem @itemArgs)
            continue
        }

        # Pre-dispatch up_to_date guard (parity with macOS web/apply.sh
        # Sesja 40). Get-WingetUpgradable is the only feed we trust, so
        # current==target is rare here, but we honour it defensively.
        if ($curVersion -and $tgtVersion -and ($curVersion -eq $tgtVersion)) {
            $itemArgs['Status']          = 'up_to_date'
            $itemArgs['ExitCode']        = 0
            $itemArgs['ResolvedVersion'] = $curVersion
            $itemArgs['Messages']        = @( @{ level='info'; text='already at latest version' } )
            [void](Add-SidecarItem @itemArgs)
            continue
        }

        $result = _Invoke-MsStoreUpgrade -PackageId ([string]$u.Id)
        $exit   = [int]$result.ExitCode
        $statusFromCode = Convert-WingetExitCode -ExitCode $exit
        $itemArgs['Status']    = if ($statusFromCode -in 'success','reboot_required','up_to_date') { 'success' } else { 'failed' }
        $itemArgs['ExitCode']  = [int]$exit
        if ($tgtVersion) { $itemArgs['ResolvedVersion'] = $tgtVersion }
        $itemArgs['Rollback'] = @{ available = $true; method = 'reinstall_previous' }

        # Surface stderr tail on non-zero exit (parity with macOS Sesja 34).
        if ($exit -ne 0 -and $result.StderrExcerpt) {
            $itemArgs['Messages'] = @( @{
                level = 'error'
                text  = ("stderr (last 12 lines): {0}" -f $result.StderrExcerpt)
            } )
        }

        [void](Add-SidecarItem @itemArgs)
    }
    } finally {
        Stop-AscendoHeartbeat $hb
    }

    Add-SidecarMessage -Sidecar $sidecar -Level 'info' `
        -Text ("msstore apply: processed {0} package(s)" -f $upgradable.Count)
    [void](Save-Sidecar -Sidecar $sidecar -OutputDir $OutputDir)
    exit 0
}
catch {
    if ($null -ne $sidecar) {
        try {
            Add-SidecarMessage -Sidecar $sidecar -Level 'error' `
                -Text ("msstore apply infrastructure failure: {0}" -f $_.Exception.Message)
            [void](Add-SidecarItem -Sidecar $sidecar `
                -Id '<infrastructure>' -Name 'msstore apply' `
                -Category 'msstore' -SourceType 'msstore' -SourceFeed 'msstore' `
                -Status 'failed')
            [void](Save-Sidecar -Sidecar $sidecar -OutputDir $OutputDir)
        } catch {}
    }
    exit 1
}
finally {
    if ($null -ne $prevEnc) { try { Restore-WingetEnvironment -Previous $prevEnc } catch {} }
}
                                                                                                                                                                