<#
.SYNOPSIS
    npm apply phase. For each non-skipped package, run
    `npm install -g <name>@latest` and capture stderr tail on failure.

    NO sudo / UAC: npm globals install to %APPDATA%\npm which is
    user-owned.
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
Import-Module (Join-Path $LibDir 'AscendoNpm.psm1')  -Force -DisableNameChecking

$ManifestPath = Join-Path (Join-Path $AdapterDir 'config') 'npm_global_clis.txt'

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
    $npmBin = Get-AscendoNpmBin
    $toolVersion = 'unknown'
    $toolBinaryPath = $null
    if ($npmBin) {
        $toolBinaryPath = $npmBin
        try {
            $v = & $npmBin --version 2>$null | Out-String
            if ($v) { $toolVersion = $v.Trim() }
        } catch {}
    }

    $newSidecarArgs = @{
        RunId = $RunId; Trigger = $Trigger; ProfileName = $ProfileName
        DryRun = [bool]$DryRun; Phase = 'apply'; Category = 'npm'
        ToolName = 'npm'; ToolVersion = $toolVersion
    }
    if ($toolBinaryPath) { $newSidecarArgs['ToolBinaryPath'] = $toolBinaryPath }
    $sidecar = New-Sidecar @newSidecarArgs

    if (-not $npmBin) {
        Add-SidecarMessage -Sidecar $sidecar -Level 'error' `
            -Text "npm not found on PATH; nothing to apply."
        Add-SidecarItem -Sidecar $sidecar -Id '__no_npm__' -Name 'npm missing' `
            -Category 'npm' -SourceType 'npm' -Status 'failed' | Out-Null
        [void](Save-Sidecar -Sidecar $sidecar -OutputDir $OutputDir)
        exit 1
    }

    $itemFilterArray = $null
    if ($ItemFilter -and $ItemFilter.Trim()) {
        $itemFilterArray = @(
            $ItemFilter -split ',' | ForEach-Object { $_.Trim() } | Where-Object { $_ }
        )
        if ($itemFilterArray.Count -eq 0) { $itemFilterArray = $null }
    }

    $manifest = Read-AscendoNpmManifest -Path $ManifestPath
    foreach ($pkg in $manifest) {
        if ($null -ne $itemFilterArray -and ($itemFilterArray -notcontains $pkg)) { continue }
        if (Test-AscendoNpmShouldSkip -PackageName $pkg) {
            Add-SidecarItem -Sidecar $sidecar -Id $pkg -Name $pkg `
                -Category 'npm' -SourceType 'npm' -Status 'skipped' | Out-Null
            continue
        }

        $installed = Get-AscendoNpmInstalledVersion -PackageName $pkg
        $latest    = Get-AscendoNpmLatestVersion    -PackageName $pkg

        # Skip if already up_to_date (mirror winget apply guard).
        if ($installed -and $latest -and (_Compare-Version $installed $latest) -ge 0) {
            $upArgs = @{
                Sidecar = $sidecar; Id = $pkg; Name = $pkg
                Category = 'npm'; SourceType = 'npm'; Status = 'up_to_date'
                CurrentVersion = $installed; TargetVersion = $installed
            }
            Add-SidecarItem @upArgs | Out-Null
            continue
        }

        if ($DryRun) {
            $dryArgs = @{
                Sidecar = $sidecar; Id = $pkg; Name = $pkg
                Category = 'npm'; SourceType = 'npm'; Status = 'planned'
            }
            if ($installed) { $dryArgs['CurrentVersion'] = $installed }
            if ($latest)    { $dryArgs['TargetVersion']  = $latest }
            Add-SidecarItem @dryArgs | Out-Null
            continue
        }

        # Real install: tee combined output to a temp file so we can show
        # the failure tail in the sidecar messages on non-zero exit.
        $logTmp = [System.IO.Path]::GetTempFileName()
        $rc = 0
        try {
            $proc = Start-Process -FilePath $npmBin `
                -ArgumentList @('install', '-g', "$pkg@latest") `
                -NoNewWindow -Wait -PassThru `
                -RedirectStandardOutput $logTmp `
                -RedirectStandardError "$logTmp.err"
            $rc = $proc.ExitCode
            # Merge stderr into the same buffer for tail extraction.
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
            Category = 'npm'; SourceType = 'npm'
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
            $messages = @(
                @{
                    'level' = 'error'
                    'text'  = if ($tail) { $tail } else { "npm install failed with exit $rc" }
                    'timestamp' = $null
                }
            )
            $finalArgs['Messages'] = $messages
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
                -Name 'apply phase error' -Category 'npm' -SourceType 'npm' `
                -Status 'failed' | Out-Null
        } catch {}
        try { [void](Save-Sidecar -Sidecar $sidecar -OutputDir $OutputDir) } catch {}
    }
    [Console]::Error.WriteLine("npm/apply.ps1 FAILED: $errMsg")
    exit 1
}
