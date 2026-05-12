<#
.SYNOPSIS
    Web (third-party app) check phase. Read-only enumeration of installed
    web apps from web_apps.toml + per-handler candidate-version probes.

.DESCRIPTION
    For each enabled entry in adapters/windows/config/web_apps.toml whose
    windows_uninstall_key matches an installed registry key:

      * read installed DisplayVersion from HKLM/HKCU Uninstall hives;
      * dispatch to Invoke-<handler>Check (github_release / release_feed /
        builtin);
      * classify the result into planned / up_to_date / skipped (builtin or
        probe_unavailable) and emit one ascendo/v1 sidecar item.

    Apps NOT installed on this host produce no item (registry key absent).

    v1 scope: check + plan probe real candidate versions for github_release
    and release_feed handlers; builtin entries always emit a `skipped` item
    with the vendor URL surfaced. Apply is Tier-B trigger-only across the
    board — we do not download/install .exe on Windows yet (needs
    Authenticode + UAC + per-installer flag handling).

.PARAMETER RunId
    UUID from the orchestrator (round-trips through [Guid]).

.PARAMETER Trigger
    cli | scheduler | dashboard | plugin | unknown.

.PARAMETER ProfileName
    Profile slug. Aliased to 'Profile' for the Python caller (-Profile <slug>).

.PARAMETER OutputDir
    Base dir; sidecar lands at <OutputDir>/<RunId>/check__web.json.

.PARAMETER DryRun
    Switch; ignored for check (read-only) beyond stamping run.dry_run.

.PARAMETER ItemFilter
    Comma-separated slugs (e.g. 'obsidian,brave'). Empty = no filter.

.PARAMETER ConfigDir
    Path to adapters/windows/config/. Defaults to the sibling config/ dir.

.NOTES
    PowerShell 5.1 + 7.x compatible.
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

# -----------------------------------------------------------------------------
# Locate sibling lib/ and config/
# -----------------------------------------------------------------------------
$ScriptDir = Split-Path -Parent $PSCommandPath
$AdapterDir = Split-Path -Parent (Split-Path -Parent $ScriptDir)
$LibDir = Join-Path $AdapterDir 'lib'
if (-not $ConfigDir) { $ConfigDir = Join-Path $AdapterDir 'config' }

Import-Module (Join-Path $LibDir 'AscendoJson.psm1') -Force -DisableNameChecking
Import-Module (Join-Path $LibDir 'AscendoWeb.psm1')  -Force -DisableNameChecking
Import-Module (Join-Path $LibDir 'handlers/github_release.ps1') -Force -DisableNameChecking
Import-Module (Join-Path $LibDir 'handlers/release_feed.ps1')   -Force -DisableNameChecking
Import-Module (Join-Path $LibDir 'handlers/builtin.ps1')        -Force -DisableNameChecking

# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------
$sidecar = $null

try {
    $newSidecarArgs = @{
        RunId       = $RunId
        Trigger     = $Trigger
        ProfileName = $ProfileName
        DryRun      = [bool]$DryRun
        Phase       = 'check'
        Category    = 'web'
        ToolName    = 'ascendo-web'
        ToolVersion = '0.1.0'
    }
    $sidecar = New-Sidecar @newSidecarArgs

    # Item filter parsing (comma-separated slugs).
    $itemFilterArray = $null
    if ($ItemFilter -and $ItemFilter.Trim()) {
        $itemFilterArray = @(
            $ItemFilter -split ',' |
                ForEach-Object { $_.Trim() } |
                Where-Object   { $_ -ne '' }
        )
        if ($itemFilterArray.Count -eq 0) { $itemFilterArray = $null }
    }

    # Validate registry parses up-front so a malformed TOML doesn't surface
    # as N per-app failures.
    $registryOk = $false
    try {
        $registryOk = Invoke-WebRegistry -ConfigDir $ConfigDir -LibDir $LibDir -Action 'validate'
    } catch {
        Add-SidecarMessage -Sidecar $sidecar -Level 'error' `
            -Text ("web_apps.toml registry could not be validated: {0}" -f $_.Exception.Message)
    }
    if (-not $registryOk) {
        # No items emitted; save and exit with the error in messages.
        Add-SidecarItem -Sidecar $sidecar `
            -Id '__phase_error__' -Name 'web registry invalid' `
            -Category 'web' -SourceType 'web' -Status 'failed' | Out-Null
        [void](Save-Sidecar -Sidecar $sidecar -OutputDir $OutputDir)
        exit 1
    }

    # Enumerate slugs.
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

        # Load full app config via the shim.
        $appConfig = $null
        try {
            $appConfig = Invoke-WebRegistry -ConfigDir $ConfigDir -LibDir $LibDir `
                -Action 'get-app' -Slug $slug
        } catch {
            Add-SidecarMessage -Sidecar $sidecar -Level 'warn' `
                -Text ("{0}: failed to load app config: {1}" -f $slug, $_.Exception.Message)
            continue
        }
        if ($null -eq $appConfig) { continue }

        $handler = [string]$appConfig.handler
        $displayName = [string]$appConfig.display_name

        # Ownership detection: skip apps not installed on this host (no
        # windows_uninstall_key match in HKLM/HKCU).
        $uninstallKey = ''
        $ukProp = $appConfig.PSObject.Properties['windows_uninstall_key']
        if ($null -ne $ukProp -and $ukProp.Value) {
            $uninstallKey = [string]$ukProp.Value
        }
        $installed = $null
        if ($uninstallKey) {
            $installed = Get-WebInstalledVersion -UninstallKey $uninstallKey
        }
        if (-not $installed) {
            # Not installed on this host — skip silently. (We don't emit a
            # "missing" item; the operator hasn't asked us to install net-new
            # apps. Web detection is opt-in: install first via your usual
            # channel, then Ascendo tracks updates.)
            continue
        }

        # Per-handler probe.
        $candidate = $null
        try {
            switch ($handler) {
                'github_release' { $candidate = Invoke-GitHubReleaseCheck -Slug $slug -Config $appConfig }
                'release_feed'   { $candidate = Invoke-ReleaseFeedCheck   -Slug $slug -Config $appConfig }
                'builtin'        { $candidate = Invoke-BuiltinCheck        -Slug $slug -Config $appConfig }
                default {
                    Add-SidecarMessage -Sidecar $sidecar -Level 'warn' `
                        -Text ("{0}: unknown handler '{1}'" -f $slug, $handler)
                    $candidate = $null
                }
            }
        } catch {
            Add-SidecarMessage -Sidecar $sidecar -Level 'warn' `
                -Text ("{0}: handler probe threw: {1}" -f $slug, $_.Exception.Message)
            $candidate = $null
        }

        # Classify + emit. Three explicit branches so the bidirectional
        # from=/to= invariant is readable + scanner-friendly: every
        # $itemArgs hashtable sets BOTH CurrentVersion and TargetVersion
        # (TargetVersion == CurrentVersion when up_to_date or skipped —
        # the SPA overlay expects candidate to equal installed when the
        # row is "current" rather than reading candidate=None and painting
        # "candidate unknown").
        if ([string]::IsNullOrWhiteSpace($candidate)) {
            # No candidate detected: builtin handler (no probe), or Tier-A
            # probe failed (rate-limit / 404 / regex no-match).
            $itemArgs = @{
                Sidecar         = $sidecar
                Id              = "web:$slug"
                Name            = $displayName
                Category        = 'web'
                SourceType      = 'web'
                Status          = 'skipped'
                CurrentVersion  = $installed
                TargetVersion   = $installed
            }
            [void](Add-SidecarItem @itemArgs)
            $status = 'skipped'
        } elseif (Compare-WebVersion -Installed $installed -Candidate $candidate) {
            # Tier-A probe says candidate > installed. Emit 'planned'.
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
            $status = 'planned'
        } else {
            # Tier-A probe reports candidate equal-or-older than installed.
            # Emit 'up_to_date' with TargetVersion == CurrentVersion so the
            # SPA overlay does not paint the row as "candidate unknown".
            $itemArgs = @{
                Sidecar         = $sidecar
                Id              = "web:$slug"
                Name            = $displayName
                Category        = 'web'
                SourceType      = 'web'
                Status          = 'up_to_date'
                CurrentVersion  = $installed
                TargetVersion   = $candidate
            }
            [void](Add-SidecarItem @itemArgs)
            $status = 'up_to_date'
        }

        if ($status -eq 'skipped' -and $handler -eq 'builtin') {
            $vendorUrl = ''
            try { $vendorUrl = [string]$appConfig.builtin.url } catch { $vendorUrl = '' }
            $msg = if ($vendorUrl) {
                "{0}: builtin handler — vendor auto-updates internally or manual download at {1}" -f $slug, $vendorUrl
            } else {
                "{0}: builtin handler — no probe URL configured" -f $slug
            }
            Add-SidecarMessage -Sidecar $sidecar -Level 'info' -Text $msg
        } elseif ($status -eq 'skipped') {
            Add-SidecarMessage -Sidecar $sidecar -Level 'warn' `
                -Text ("{0}: {1} probe returned empty (rate-limit / 404 / regex no-match?)" -f $slug, $handler)
        }
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
                -Id '__phase_error__' -Name 'web check error' `
                -Category 'web' -SourceType 'web' -Status 'failed' | Out-Null
            [void](Save-Sidecar -Sidecar $sidecar -OutputDir $OutputDir)
        } catch {
            # Best-effort; fall through.
        }
    }
    [Console]::Error.WriteLine("check__web.ps1 FAILED: $errMsg")
    exit 1
}
