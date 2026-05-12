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

    # Load sibling apply sidecar if present.
    $applySidecarPath = Join-Path (Join-Path $OutputDir $RunId) 'apply__web.json'
    $priorInstalled = @{}      # slug -> prior installed_version (string)
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
        $applyResult = if ($applyStatus.ContainsKey($slug)) { $applyStatus[$slug] } else { $null }

        $status = 'up_to_date'
        if ($applyResult -eq 'failed') {
            $status = 'failed'
        } elseif ($prior -and $installed -ne $prior) {
            # Operator actually upgraded.
            $status = 'success'
        }

        $itemArgs = @{
            Sidecar        = $sidecar
            Id             = "web:$slug"
            Name           = $displayName
            Category       = 'web'
            SourceType     = 'web'
            Status         = $status
            CurrentVersion = $installed
            TargetVersion  = $installed   # post-apply: installed is the new candidate-equivalent
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
