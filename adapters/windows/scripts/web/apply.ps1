<#
.SYNOPSIS
    Web (third-party app) apply phase. v1 scope: Tier-B trigger-only.

.DESCRIPTION
    For every installed app whose candidate is strictly newer than its
    installed version, this phase invokes Invoke-<handler>Apply (opens
    the vendor's release/download page in the default browser) and emits
    a 'triggered' sidecar item.

    v1 scope: we do NOT download/run .exe installers from Ascendo on
    Windows yet -- that requires Authenticode signature verification +
    UAC handoff + per-installer flag handling we'll add in a follow-up.
    Apply opens the page; the operator clicks through the vendor's own
    install flow.

    Status semantics (matching macOS Tier-B handlers):
      * triggered : we successfully opened the vendor URL.
      * skipped   : the app isn't installed on this host (no item emitted).
      * up_to_date: installed == candidate (no action taken).
      * failed    : Start-Process threw (rare; e.g. no default browser).

    Dry-run mode emits items with status='planned' (no Start-Process call).
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
        Phase       = 'apply'
        Category    = 'web'
        ToolName    = 'ascendo-web'
        ToolVersion = '0.1.0'
    }
    $sidecar = New-Sidecar @newSidecarArgs

    Add-SidecarMessage -Sidecar $sidecar -Level 'info' `
        -Text ("Web apply is Tier-B trigger-only in v1: opens vendor download/release page; " +
               "no .exe install performed by Ascendo. Operator clicks through vendor flow.")

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
        if (-not $installed) { continue }   # not installed on this host

        # Probe candidate first so we can short-circuit up_to_date apps.
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

        # builtin handler always triggers (no candidate version) -- operator
        # explicitly opted in to the vendor's manual update flow. For
        # Tier-A handlers (github_release / release_feed), we only trigger
        # when candidate is strictly newer than installed.
        $shouldTrigger = $false
        $statusBase = 'up_to_date'
        $targetVer = $installed
        if ($handler -eq 'builtin') {
            $shouldTrigger = $true
            $statusBase = 'triggered'
            $targetVer = $installed   # bidirectional from/to (no real candidate)
        } elseif ([string]::IsNullOrWhiteSpace($candidate)) {
            # Tier-A probe failed (rate-limit / 404 / regex no-match).
            $itemArgs = @{
                Sidecar        = $sidecar
                Id             = "web:$slug"
                Name           = $displayName
                Category       = 'web'
                SourceType     = 'web'
                Status         = 'skipped'
                CurrentVersion = $installed
                TargetVersion  = $installed
            }
            [void](Add-SidecarItem @itemArgs)
            Add-SidecarMessage -Sidecar $sidecar -Level 'warn' `
                -Text ("{0}: candidate probe unavailable; nothing triggered" -f $slug)
            continue
        } elseif (Compare-WebVersion -Installed $installed -Candidate $candidate) {
            $shouldTrigger = $true
            $statusBase = 'triggered'
            $targetVer = $candidate
        } else {
            $shouldTrigger = $false
            $statusBase = 'up_to_date'
            $targetVer = $candidate
        }

        if (-not $shouldTrigger) {
            $itemArgs = @{
                Sidecar        = $sidecar
                Id             = "web:$slug"
                Name           = $displayName
                Category       = 'web'
                SourceType     = 'web'
                Status         = 'up_to_date'
                CurrentVersion = $installed
                TargetVersion  = $targetVer
            }
            [void](Add-SidecarItem @itemArgs)
            continue
        }

        # DryRun: emit 'planned' without invoking the handler apply.
        if ($DryRun) {
            $itemArgs = @{
                Sidecar        = $sidecar
                Id             = "web:$slug"
                Name           = $displayName
                Category       = 'web'
                SourceType     = 'web'
                Status         = 'planned'
                CurrentVersion = $installed
                TargetVersion  = $targetVer
            }
            [void](Add-SidecarItem @itemArgs)
            continue
        }

        # Live apply: invoke handler.
        $opened = $false
        try {
            switch ($handler) {
                'github_release' { $opened = Invoke-GitHubReleaseApply -Slug $slug -Config $appConfig }
                'release_feed'   { $opened = Invoke-ReleaseFeedApply   -Slug $slug -Config $appConfig }
                'builtin'        { $opened = Invoke-BuiltinApply        -Slug $slug -Config $appConfig }
            }
        } catch {
            $opened = $false
            Add-SidecarMessage -Sidecar $sidecar -Level 'warn' `
                -Text ("{0}: handler apply threw: {1}" -f $slug, $_.Exception.Message)
        }

        $finalStatus = if ($opened) { 'triggered' } else { 'failed' }
        $itemArgs = @{
            Sidecar        = $sidecar
            Id             = "web:$slug"
            Name           = $displayName
            Category       = 'web'
            SourceType     = 'web'
            Status         = $finalStatus
            CurrentVersion = $installed
            TargetVersion  = $targetVer
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
                -Id '__phase_error__' -Name 'web apply error' `
                -Category 'web' -SourceType 'web' -Status 'failed' | Out-Null
            [void](Save-Sidecar -Sidecar $sidecar -OutputDir $OutputDir)
        } catch {}
    }
    [Console]::Error.WriteLine("apply__web.ps1 FAILED: $errMsg")
    exit 1
}
