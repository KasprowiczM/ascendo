<#
.SYNOPSIS
    npm verify phase. Reads sibling apply__npm.json (when present),
    re-queries the installed version of each item the apply touched,
    asserts it matches the target. Soft no-op when no apply sidecar
    exists.
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

function _Compare-Version {
    param([string]$A, [string]$B)
    if (-not $A -or -not $B) { return 0 }
    try {
        $a = $A.TrimStart('v','V'); $b = $B.TrimStart('v','V')
        $aClean = ($a -split '[^0-9.]')[0]; $bClean = ($b -split '[^0-9.]')[0]
        if (-not $aClean -or -not $bClean) { return 0 }
        return ([version]$aClean).CompareTo([version]$bClean)
    } catch { return 0 }
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
        DryRun = [bool]$DryRun; Phase = 'verify'; Category = 'npm'
        ToolName = 'npm'; ToolVersion = $toolVersion
    }
    if ($toolBinaryPath) { $newSidecarArgs['ToolBinaryPath'] = $toolBinaryPath }
    $sidecar = New-Sidecar @newSidecarArgs

    # Locate sibling apply sidecar.
    $parsed = [Guid]::Empty
    if ([Guid]::TryParse($RunId, [ref]$parsed)) {
        $runIdNorm = $parsed.ToString()
    } else {
        $runIdNorm = $RunId
    }
    # Sibling-sidecar lookup with canonical-run-dir fallback (each
    # phase script runs in its own tempdir; apply's sidecar isn't
    # co-located by default).
    $applySidecarPath = Find-AscendoSiblingSidecar `
        -OutputDir $OutputDir `
        -RunId     $runIdNorm `
        -Filename  'apply__npm.json'

    if (-not $applySidecarPath) {
        # Soft no-op: verify ran without a prior apply (e.g. check-only flow).
        $primaryPath = Join-Path (Join-Path $OutputDir $runIdNorm) 'apply__npm.json'
        Add-SidecarMessage -Sidecar $sidecar -Level 'info' `
            -Text "No apply sidecar found at $primaryPath; verify is a no-op."
        [void](Save-Sidecar -Sidecar $sidecar -OutputDir $OutputDir)
        exit 0
    }

    try {
        $apply = Get-Content -LiteralPath $applySidecarPath -Raw -ErrorAction Stop |
                 ConvertFrom-Json -ErrorAction Stop
    } catch {
        Add-SidecarMessage -Sidecar $sidecar -Level 'warn' `
            -Text "Could not read apply sidecar: $_"
        [void](Save-Sidecar -Sidecar $sidecar -OutputDir $OutputDir)
        exit 0
    }

    # Re-prime caches (verify runs in a fresh PowerShell, so caches are empty).
    foreach ($it in @($apply.items)) {
        if (-not $it -or -not $it.PSObject.Properties['id']) { continue }
        $pkg = [string]$it.id
        if (-not $pkg -or $pkg -eq '__phase_error__' -or $pkg.StartsWith('__')) { continue }
        # Only verify items that apply considered (status in success/planned/etc.)
        $applyStatus = ''
        if ($it.PSObject.Properties['status']) { $applyStatus = [string]$it.status }
        # Don't verify failed/skipped -- they didn't change anything.
        if ($applyStatus -in @('failed','skipped','up_to_date')) {
            # Pass-through for visibility.
            $passArgs = @{
                Sidecar = $sidecar; Id = $pkg; Name = $pkg
                Category = 'npm'; SourceType = 'npm'; Status = $applyStatus
            }
            if ($it.PSObject.Properties['current_version'] -and $it.current_version) {
                $passArgs['CurrentVersion'] = [string]$it.current_version
            }
            if ($it.PSObject.Properties['target_version'] -and $it.target_version) {
                $passArgs['TargetVersion'] = [string]$it.target_version
            }
            Add-SidecarItem @passArgs | Out-Null
            continue
        }

        $target = $null
        if ($it.PSObject.Properties['target_version'] -and $it.target_version) {
            $target = [string]$it.target_version
        }
        $current = Get-AscendoNpmInstalledVersion -PackageName $pkg

        $verifyArgs = @{
            Sidecar = $sidecar; Id = $pkg; Name = $pkg
            Category = 'npm'; SourceType = 'npm'
        }
        if ($current) { $verifyArgs['CurrentVersion'] = $current }
        if ($target)  { $verifyArgs['TargetVersion']  = $target }

        if (-not $current) {
            $verifyArgs['Status'] = 'failed'
            $verifyArgs['Messages'] = @(
                [ordered]@{ 'level' = 'error'; 'text' = "Package $pkg not installed post-apply."; 'timestamp' = $null }
            )
        } elseif ($target -and (_Compare-Version $current $target) -lt 0) {
            $verifyArgs['Status'] = 'failed'
            $verifyArgs['Messages'] = @(
                [ordered]@{ 'level' = 'error'; 'text' = "Version mismatch: installed=$current expected $target."; 'timestamp' = $null }
            )
        } else {
            $verifyArgs['Status'] = 'success'
        }
        Add-SidecarItem @verifyArgs | Out-Null
    }

    [void](Save-Sidecar -Sidecar $sidecar -OutputDir $OutputDir)
    exit 0
} catch {
    $errMsg = $_.Exception.Message
    if ($null -ne $sidecar) {
        try { Add-SidecarMessage -Sidecar $sidecar -Level 'error' -Text ("Phase failed: {0}" -f $errMsg) } catch {}
        try {
            Add-SidecarItem -Sidecar $sidecar -Id '__phase_error__' `
                -Name 'verify phase error' -Category 'npm' -SourceType 'npm' `
                -Status 'failed' | Out-Null
        } catch {}
        try { [void](Save-Sidecar -Sidecar $sidecar -OutputDir $OutputDir) } catch {}
    }
    [Console]::Error.WriteLine("npm/verify.ps1 FAILED: $errMsg")
    exit 1
}
