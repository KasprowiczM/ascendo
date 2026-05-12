<#
.SYNOPSIS
    Web (third-party app) plan phase. Side-effect-free enumeration of
    apps the apply phase WOULD trigger an update for.

.DESCRIPTION
    Same probes as check.ps1 but the sidecar emits items ONLY for
    candidates strictly newer than installed (status='planned').
    up_to_date / skipped (builtin) apps are not emitted -- plan is
    the apply-preview phase, not the inventory phase.

    v1 scope: probes are real (github_release + release_feed); apply
    remains Tier-B trigger-only (we don't yet download/install .exe).
    Plan items still carry target_version so the operator can see
    what each app will be bumped to.
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
Import-Module (Join-Path $LibDir 'handlers/github_release.ps1') -Force -DisableNameChecking
Import-Module (Join-Path $LibDir 'handlers/release_feed.ps1')   -Force -DisableNameChecking
Import-Module (Join-Path $LibDir 'handlers/builtin.ps1')        -Force -DisableNameChecking

$sidecar = $null

try {
    $newSidecarArgs = @{
        RunId       = $RunId
        Trigger     = $Trigger
        ProfileName = $ProfileName
        DryRun      = [bool]$DryRun
        Phase       = 'plan'
        Category    = 'web'
        ToolName    = 'ascendo-web'
        ToolVersion = '0.1.0'
    }
    $sidecar = New-Sidecar @newSidecarArgs

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

        $handler = [string]$appConfig.handler
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

        $candidate = $null
        try {
            switch ($handler) {
                'github_release' { $candidate = Invoke-GitHubReleaseCheck -Slug $slug -Config $appConfig }
                'release_feed'   { $candidate = Invoke-ReleaseFeedCheck   -Slug $slug -Config $appConfig }
                'builtin'        { $candidate = Invoke-BuiltinCheck        -Slug $slug -Config $appConfig }
            }
        } catch {
            $candidate = $null
        }
        if ([string]::IsNullOrWhiteSpace($candidate)) { continue }
        if (-not (Compare-WebVersion -Installed $installed -Candidate $candidate)) { continue }

        $itemArgs = @{
            Sidecar         = $sidecar
            Id              = "web:$slug"
            Name            = $displayName
            Category        = 'web'
            SourceType      = 'web'
            Status          = 'planned'
            CurrentVersion  = $installed
            TargetVersion   = $candidate
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
                -Id '__phase_error__' -Name 'web plan error' `
                -Category 'web' -SourceType 'web' -Status 'failed' | Out-Null
            [void](Save-Sidecar -Sidecar $sidecar -OutputDir $OutputDir)
        } catch {}
    }
    [Console]::Error.WriteLine("plan__web.ps1 FAILED: $errMsg")
    exit 1
}
