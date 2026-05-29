<#
.SYNOPSIS
    pip apply phase. For each non-skipped package, run
    `pip install --user --upgrade <name>` and capture stderr tail on failure.

    NO sudo / UAC: pip on Windows installs to the user site-packages.
    Test-AscendoPipShouldSkip rejects pip/setuptools/wheel (system-
    managed packages that often fail to self-upgrade with
    "cannot uninstall, RECORD file not found").
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)] [string] $RunId,
    [Parameter(Mandatory)]
        [ValidateSet('cli','scheduler','dashboard','plugin','unknown')]
        [string] $Trigger,
    [Parameter(Mandatory)]
        [Alias('Profile')]
        [string] $ProfileName,
    [Parameter(Mandatory)] [string] $OutputDir,
    [Parameter()] [switch] $DryRun,
    [Parameter()] [string] $ItemFilter = ''
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$AdapterDir = Split-Path -Parent (Split-Path -Parent $ScriptDir)
$LibDir = Join-Path $AdapterDir 'lib'
Import-Module (Join-Path $LibDir 'AscendoJson.psm1') -Force -DisableNameChecking
Import-Module (Join-Path $LibDir 'AscendoPip.psm1')  -Force -DisableNameChecking

$ManifestPath = Join-Path (Join-Path $AdapterDir 'config') 'pip_global_clis.txt'

function _Compare-Version {
    param([string]$Installed, [string]$Latest)
    if (-not $Installed -or -not $Latest) { return 0 }
    try {
        $a = $Installed.TrimStart('v','V'); $b = $Latest.TrimStart('v','V')
        $aClean = ($a -split '[^0-9.]')[0]; $bClean = ($b -split '[^0-9.]')[0]
        if (-not $aClean -or -not $bClean) { return 0 }
        return ([version]$aClean).CompareTo([version]$bClean)
    } catch { return 0 }
}

function _Tail-Lines {
    param([string]$Text, [int]$Lines = 12, [int]$Chars = 1500)
    if (-not $Text) { return '' }
    $arr = $Text -split "`r?`n"
    $kept = $arr | Where-Object { $_.Trim() -ne '' } | Select-Object -Last $Lines
    $joined = $kept -join "`n"
    if ($joined.Length -gt $Chars) {
        $joined = $joined.Substring($joined.Length - $Chars)
    }
    return $joined
}

$sidecar = $null
try {
    $pipCmd = Get-AscendoPipCommand
    $pipBin = Get-AscendoPipBin
    $newSidecarArgs = @{
        RunId = $RunId; Trigger = $Trigger; ProfileName = $ProfileName
        DryRun = [bool]$DryRun; Phase = 'apply'; Category = 'pip'
        ToolName = 'pip'; ToolVersion = 'unknown'
    }
    if ($pipBin) { $newSidecarArgs['ToolBinaryPath'] = $pipBin }
    $sidecar = New-Sidecar @newSidecarArgs

    if (-not $pipCmd -or $pipCmd.Count -lt 1) {
        Add-SidecarMessage -Sidecar $sidecar -Level 'error' `
            -Text "pip not found on PATH; nothing to apply."
        Add-SidecarItem -Sidecar $sidecar -Id '__no_pip__' -Name 'pip missing' `
            -Category 'pip' -SourceType 'pip' -Status 'failed' | Out-Null
        [void](Save-Sidecar -Sidecar $sidecar -OutputDir $OutputDir)
        exit 1
    }

    $pipExe = [string]$pipCmd[0]
    $pipLeadingArgs = @($pipCmd[1])

    $itemFilterArray = $null
    if ($ItemFilter -and $ItemFilter.Trim()) {
        $itemFilterArray = @(
            $ItemFilter -split ',' | ForEach-Object { $_.Trim() } | Where-Object { $_ }
        )
        if ($itemFilterArray.Count -eq 0) { $itemFilterArray = $null }
    }

    # ── Read DEDUPLICATION_TASKS.json for uninstalls ───────────────
    $uninstallFilterArray = $null
    $dedupFile = Join-Path $OutputDir "$RunId\DEDUPLICATION_TASKS.json"
    if (Test-Path -LiteralPath $dedupFile) {
        try {
            $dedupJson = Get-Content -LiteralPath $dedupFile -Raw | ConvertFrom-Json
            if ($null -ne $dedupJson.pip) {
                $uninstallFilterArray = @($dedupJson.pip)
            }
        } catch {}
    }
    
    if ($null -ne $uninstallFilterArray) {
        foreach ($pkg in $uninstallFilterArray) {
            if ($DryRun) {
                Add-SidecarItem -Sidecar $sidecar -Id $pkg -Name $pkg `
                    -Category 'pip' -SourceType 'pip' -Status 'planned' `
                    -TargetVersion 'ABSENT' | Out-Null
                continue
            }
            $uninstallArgs = @()
            foreach ($a in $pipLeadingArgs) { if ($null -ne $a) { $uninstallArgs += [string]$a } }
            $uninstallArgs += @('uninstall', '-y', $pkg)
            $proc = Start-Process -FilePath $pipExe -ArgumentList $uninstallArgs -NoNewWindow -Wait -PassThru
            if ($proc.ExitCode -eq 0) {
                Add-SidecarItem -Sidecar $sidecar -Id $pkg -Name $pkg `
                    -Category 'pip' -SourceType 'pip' -Status 'success' `
                    -TargetVersion 'ABSENT' -ResolvedVersion 'ABSENT' -ExitCode $proc.ExitCode | Out-Null
            } else {
                Add-SidecarItem -Sidecar $sidecar -Id $pkg -Name $pkg `
                    -Category 'pip' -SourceType 'pip' -Status 'failed' `
                    -ExitCode $proc.ExitCode | Out-Null
            }
        }
    }

    $manifest = Read-AscendoPipManifest -Path $ManifestPath
    foreach ($pkg in $manifest) {
        if ($null -ne $itemFilterArray -and ($itemFilterArray -notcontains $pkg)) { continue }
        if (Test-AscendoPipShouldSkip -PackageName $pkg) {
            Add-SidecarItem -Sidecar $sidecar -Id $pkg -Name $pkg `
                -Category 'pip' -SourceType 'pip' -Status 'skipped' | Out-Null
            continue
        }

        $installed = Get-AscendoPipInstalledVersion -PackageName $pkg
        $latest    = Get-AscendoPipLatestVersion    -PackageName $pkg

        if ($installed -and $latest -and (_Compare-Version $installed $latest) -ge 0) {
            $upArgs = @{
                Sidecar = $sidecar; Id = $pkg; Name = $pkg
                Category = 'pip'; SourceType = 'pip'; Status = 'up_to_date'
                CurrentVersion = $installed; TargetVersion = $installed
            }
            Add-SidecarItem @upArgs | Out-Null
            continue
        }

        if ($DryRun) {
            $dryArgs = @{
                Sidecar = $sidecar; Id = $pkg; Name = $pkg
                Category = 'pip'; SourceType = 'pip'; Status = 'planned'
            }
            if ($installed) { $dryArgs['CurrentVersion'] = $installed }
            if ($latest)    { $dryArgs['TargetVersion']  = $latest }
            Add-SidecarItem @dryArgs | Out-Null
            continue
        }

        # Real install. Compose argv: <leading> install --user --upgrade <pkg>
        $installArgs = @()
        foreach ($a in $pipLeadingArgs) { if ($null -ne $a) { $installArgs += [string]$a } }
        $installArgs += @('install', '--user', '--upgrade', '--disable-pip-version-check', $pkg)

        $logTmp = [System.IO.Path]::GetTempFileName()
        $rc = 0
        try {
            $proc = Start-Process -FilePath $pipExe `
                -ArgumentList $installArgs `
                -NoNewWindow -Wait -PassThru `
                -RedirectStandardOutput $logTmp `
                -RedirectStandardError "$logTmp.err"
            $rc = $proc.ExitCode
            if (Test-Path "$logTmp.err") {
                $errBody = Get-Content "$logTmp.err" -Raw -ErrorAction SilentlyContinue
                if ($errBody) { Add-Content -LiteralPath $logTmp -Value $errBody }
                Remove-Item "$logTmp.err" -Force -ErrorAction SilentlyContinue
            }
        } catch {
            $rc = -1
            Add-Content -LiteralPath $logTmp -Value ("EXCEPTION: $_") -ErrorAction SilentlyContinue
        }

        $body = ''
        if (Test-Path $logTmp) {
            $body = Get-Content $logTmp -Raw -ErrorAction SilentlyContinue
            Remove-Item $logTmp -Force -ErrorAction SilentlyContinue
        }

        $finalArgs = @{
            Sidecar = $sidecar; Id = $pkg; Name = $pkg
            Category = 'pip'; SourceType = 'pip'
            ExitCode = $rc
        }
        if ($installed) { $finalArgs['CurrentVersion'] = $installed }
        if ($latest)    { $finalArgs['TargetVersion']  = $latest }

        if ($rc -eq 0) {
            $finalArgs['Status'] = 'success'
            if ($latest) { $finalArgs['ResolvedVersion'] = $latest }
            Add-SidecarItem @finalArgs | Out-Null
        } else {
            $finalArgs['Status'] = 'failed'
            $tail = _Tail-Lines -Text $body
            # Add-SidecarItem -Messages validator requires [hashtable]
            # entries specifically (rejects [ordered] = OrderedDictionary).
            # See adapters/windows/lib/AscendoJson.psm1:538.
            $finalArgs['Messages'] = @(
                @{
                    'level' = 'error'
                    'text'  = if ($tail) { $tail } else { "pip install failed with exit $rc" }
                    'timestamp' = $null
                }
            )
            Add-SidecarItem @finalArgs | Out-Null
        }
    }

    [void](Save-Sidecar -Sidecar $sidecar -OutputDir $OutputDir)
    exit 0
} catch {
    $errMsg = $_.Exception.Message
    if ($null -ne $sidecar) {
        try { Add-SidecarMessage -Sidecar $sidecar -Level 'error' -Text ("Phase failed: {0}" -f $errMsg) } catch {}
        try {
            Add-SidecarItem -Sidecar $sidecar -Id '__phase_error__' `
                -Name 'apply phase error' -Category 'pip' -SourceType 'pip' `
                -Status 'failed' | Out-Null
        } catch {}
        try { [void](Save-Sidecar -Sidecar $sidecar -OutputDir $OutputDir) } catch {}
    }
    [Console]::Error.WriteLine("pip/apply.ps1 FAILED: $errMsg")
    exit 1
}
