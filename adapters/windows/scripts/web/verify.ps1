<#
.SYNOPSIS
    Web (third-party app) verify phase. Re-reads installed DisplayVersion
    from the registry after apply to confirm what actually changed.

.DESCRIPTION
    v1 apply is Tier-B trigger-only: we open the vendor's release/download
    page and the operator runs the installer manually. After apply, the
    operator returns to Ascendo and clicks Verify. We re-read every
    registered app's installed DisplayVersion and emit:

      * success    : installed != prior installed (the operator did upgrade,
                     even if not all the way to the candidate the probe
                     reported);
      * up_to_date : installed == candidate (or no apply sidecar to compare
                     against);
      * failed     : prior apply marked this slug as failed.

    If a sibling apply__web.json sidecar exists in the same run dir we use
    its items to compute the "prior installed" version per slug. Without it
    verify is a soft no-op (every installed app reports up_to_date).
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
    [Parameter()] [string] $ItemFilter = '',
    [Parameter()] [string] $ConfigDir = ''
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$ScriptDir = Split-Path -Parent $PSCommandPath
$AdapterDir = Split-Path -Parent (Split-Path -Parent $ScriptDir)
$LibDir = Join-Path $AdapterDir 'lib'
if (-not $ConfigDir) { $ConfigDir = Join-Path $AdapterDir 'config' }

Import-Module (Join-Path $LibDir 'AscendoJson.psm1') -Force -DisableNameChecking
Import-Module (Join-Path $LibDir 'AscendoWeb.psm1')  -Force -DisableNameChecking

$sidecar = $null

try {
    $newSidecarArgs = @{
        RunId       = $RunId
        Trigger     = $Trigger
        ProfileName = $ProfileName
        DryRun      = [bool]$DryRun
        Phase       = 'verify'
        Category    = 'web'
        ToolName    = 'ascendo-web'
        ToolVersion = '0.1.0'
    }
    $sidecar = New-Sidecar @newSidecarArgs

    # Load sibling apply sidecar if present. Sibling-sidecar lookup
    # with canonical-run-dir fallback (each phase script runs in its
    # own tempdir so apply's sidecar isn't co-located by default).
    $applySidecarPath = Find-AscendoSiblingSidecar `
        -OutputDir $OutputDir `
        -RunId     $RunId `
        -Filename  'apply__web.json'
    if (-not $applySidecarPath) {
        $applySidecarPath = Join-Path (Join-Path $OutputDir $RunId) 'apply__web.json'
    }
    $priorInstalled = @{}      # slug -> prior installed_version (string)
    $priorTarget    = @{}      # slug -> apply.target_version (string) -- preserves
                               #         the candidate version reported by check
                               #         so verify can keep the "outdated" signal
                               #         when apply was Tier-B triggered but the
                               #         operator never actually ran the installer.
    $applyStatus = @{}         # slug -> apply status (string)
    if (Test-Path -LiteralPath $applySidecarPath) {
        try {
            $applyDoc = Get-Content -LiteralPath $applySidecarPath -Raw -Encoding UTF8 | ConvertFrom-Json
            if ($null -ne $applyDoc -and $applyDoc.PSObject.Properties['items']) {
                foreach ($it in @($applyDoc.items)) {
                    $idProp = $it.PSObject.Properties['id']
                    if ($null -eq $idProp) { continue }
                    $id = [string]$idProp.Value
                    if (-not $id.StartsWith('web:')) { continue }
                    $slug = $id.Substring(4)
                    $curProp = $it.PSObject.Properties['current_version']
                    if ($null -ne $curProp -and $curProp.Value) {
                        $priorInstalled[$slug] = [string]$curProp.Value
                    }
                    $tgtProp = $it.PSObject.Properties['target_version']
                    if ($null -ne $tgtProp -and $tgtProp.Value) {
                        $priorTarget[$slug] = [string]$tgtProp.Value
                    }
                    $stProp = $it.PSObject.Properties['status']
                    if ($null -ne $stProp -and $stProp.Value) {
                        $applyStatus[$slug] = [string]$stProp.Value
                    }
                }
            }
        } catch {
            Add-SidecarMessage -Sidecar $sidecar -Level 'warn' `
                -Text ("Failed to read sibling apply__web.json: {0}" -f $_.Exception.Message)
        }
    } else {
        Add-SidecarMessage -Sidecar $sidecar -Level 'info' `
            -Text "No sibling apply__web.json; verify is a soft no-op (reports up_to_date for every installed app)."
    }

    $itemFilterArray = $null
    if ($ItemFilter -and $ItemFilter.Trim()) {
        $itemFilterArray = @(
            $ItemFilter -split ',' |
                ForEach-Object { $_.Trim() } |
                Where-Object   { $_ -ne '' }
        )
        if ($itemFilterArray.Count -eq 0) { $itemFilterArray = $null }
    }

    $slugs = @()
    try {
        $slugs = @(Invoke-WebRegistry -ConfigDir $ConfigDir -LibDir $LibDir -Action 'list-slugs')
    } catch {
        Add-SidecarMessage -Sidecar $sidecar -Level 'warn' `
            -Text ("Failed to list registered web apps: {0}" -f $_.Exception.Message)
    }

    foreach ($slug in $slugs) {
        if ($null -ne $itemFilterArray -and ($itemFilterArray -notcontains $slug)) {
            continue
        }
        $appConfig = $null
        try {
            $appConfig = Invoke-WebRegistry -ConfigDir $ConfigDir -LibDir $LibDir `
                -Action 'get-app' -Slug $slug
        } catch {
            continue
        }
        if ($null -eq $appConfig) { continue }

        $displayName = [string]$appConfig.display_name

        $uninstallKey = ''
        $ukProp = $appConfig.PSObject.Properties['windows_uninstall_key']
        if ($null -ne $ukProp -and $ukProp.Value) {
            $uninstallKey = [string]$ukProp.Value
        }
        $installed = $null
        if ($uninstallKey) {
            $installed = Get-WebInstalledVersion -UninstallKey $uninstallKey
        }
        if (-not $installed) { continue }

        $prior = if ($priorInstalled.ContainsKey($slug)) { $priorInstalled[$slug] } else { $null }
        $priorCand = if ($priorTarget.ContainsKey($slug)) { $priorTarget[$slug] } else { $null }
        $applyResult = if ($applyStatus.ContainsKey($slug)) { $applyStatus[$slug] } else { $null }

        # Decide the verify status + target version.
        #
        # The matrix:
        #
        #   apply.status | installed-vs-prior | verify.status | verify.target
        #   --------------+--------------------+----------------+------------------
        #   failed       | (any)              | failed         | installed (no candidate cleared)
        #   success      | changed            | success        | installed
        #   success      | unchanged          | failed         | priorCand (install lied)
        #   triggered    | changed            | success        | installed
        #   triggered    | unchanged          | skipped        | priorCand (apply opened
        #                                                       vendor page; operator
        #                                                       hasn't run installer yet
        #                                                       -- we MUST preserve the
        #                                                       candidate so the row
        #                                                       keeps showing as outdated)
        #   up_to_date   | unchanged          | up_to_date     | installed
        #   (no row)     | (any)              | up_to_date     | installed
        #
        # Operator observation on DP5520WMK 2026-05-13 (run 6149fbba):
        # apply triggered vscode-user (Tier-B URL open), installed
        # didn't change, verify flipped to up_to_date with
        # candidate=installed -- erasing the outdated signal. This
        # block restores the candidate so the SPA keeps painting
        # "1.119.1 -> 1.120.0" until the operator actually upgrades
        # (or until a future check phase reads the feed and confirms
        # both versions match).
        $changed = ($prior -and $installed -ne $prior)
        $status = 'up_to_date'
        $reportedTarget = $installed
        if ($applyResult -eq 'failed') {
            $status = 'failed'
            if ($priorCand -and -not $changed) { $reportedTarget = $priorCand }
        } elseif ($applyResult -eq 'success' -and $changed) {
            $status = 'success'
        } elseif ($applyResult -eq 'success' -and -not $changed) {
            $status = 'failed'
            if ($priorCand) { $reportedTarget = $priorCand }
            Add-SidecarMessage -Sidecar $sidecar -Level 'warn' `
                -Text ("{0}: apply reported success but DisplayVersion is still {1}; check post-install state" -f $slug, $installed)
        } elseif ($applyResult -eq 'triggered' -and $changed) {
            $status = 'success'
        } elseif ($applyResult -eq 'triggered' -and -not $changed) {
            $status = 'skipped'
            if ($priorCand) { $reportedTarget = $priorCand }
        }

        $itemArgs = @{
            Sidecar        = $sidecar
            Id             = "web:$slug"
            Name           = $displayName
            Category       = 'web'
            SourceType     = 'web'
            Status         = $status
            CurrentVersion = $installed
            TargetVersion  = $reportedTarget
        }
        [void](Add-SidecarItem @itemArgs)
    }

    [void](Save-Sidecar -Sidecar $sidecar -OutputDir $OutputDir)
    exit 0

} catch {
    $errMsg = $_.Exception.Message
    if ($null -ne $sidecar) {
        try {
            Add-SidecarMessage -Sidecar $sidecar -Level 'error' `
                -Text ("Phase failed: {0}" -f $errMsg)
            Add-SidecarItem -Sidecar $sidecar `
                -Id '__phase_error__' -Name 'web verify error' `
                -Category 'web' -SourceType 'web' -Status 'failed' | Out-Null
            [void](Save-Sidecar -Sidecar $sidecar -OutputDir $OutputDir)
        } catch {}
    }
    [Console]::Error.WriteLine("verify__web.ps1 FAILED: $errMsg")
    exit 1
}
