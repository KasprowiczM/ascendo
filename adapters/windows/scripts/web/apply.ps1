<#
.SYNOPSIS
    Web (third-party app) apply phase. Supports Tier-A (download + install)
    and Tier-B (trigger-only) per app, gated by the `tier_a_apply` flag in
    web_apps.toml.

.DESCRIPTION
    For every installed app whose candidate is strictly newer than its
    installed version, this phase dispatches to one of two paths:

    * Tier-A (handler=github_release or release_feed, tier_a_apply=true):
        Invoke-<handler>ApplyReal -- downloads the matching installer
        asset, verifies Authenticode signature (publisher subject check
        when expected_publisher set), runs silently (NSIS /S or msiexec
        /qn /norestart by default; override via silent_args), then reads
        DisplayVersion back from the Uninstall registry to confirm the
        install landed. Emits `success` / `failed` / `skipped` (UAC
        cancel) per item.

    * Tier-B (handler=builtin, OR Tier-A handler with tier_a_apply=false):
        Invoke-<handler>Apply -- opens the vendor's release/download
        page in the default browser. The operator clicks through the
        vendor's own install flow. Emits `triggered`.

    Status semantics:
      * success   : Tier-A apply landed; readback DisplayVersion >= candidate.
      * triggered : Tier-B successfully opened the vendor URL.
      * skipped   : the app isn't installed on this host (no item emitted),
                    OR UAC was cancelled.
      * up_to_date: installed == candidate (no action taken).
      * failed    : Tier-A install or readback failed; Start-Process threw
                    on Tier-B.

    Dry-run mode emits items with status='planned' (no network/install call).
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
        -Text ("Web apply: Tier-A apps (tier_a_apply=true) download + verify Authenticode " +
               "+ run installer silently + readback DisplayVersion. Tier-B apps (default + " +
               "all builtin) open vendor URL for manual install.")

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

        # Decide Tier-A vs Tier-B for this app. Tier-A requires:
        #   * handler in {github_release, release_feed}  (NOT builtin)
        #   * tier_a_apply = true on the WebAppV1 entry
        # When either condition is false, fall through to the existing
        # Tier-B trigger-only behaviour.
        $tierA = $false
        $tierAProp = $appConfig.PSObject.Properties['tier_a_apply']
        if ($null -ne $tierAProp -and $tierAProp.Value) {
            $tierA = [bool]$tierAProp.Value
        }
        if ($handler -eq 'builtin') { $tierA = $false }

        if ($tierA) {
            # ── Tier-A: real download + verify + install + readback ──
            $result = $null
            try {
                switch ($handler) {
                    'github_release' {
                        $result = Invoke-GitHubReleaseApplyReal -Slug $slug -Config $appConfig
                    }
                    'release_feed' {
                        $result = Invoke-ReleaseFeedApplyReal -Slug $slug -Config $appConfig
                    }
                }
            } catch {
                $result = @{
                    Success          = $false
                    InstalledVersion = $null
                    ExitCode         = -1
                    ErrorMessage     = "handler ApplyReal threw: $($_.Exception.Message)"
                    RebootRequired   = $false
                    DownloadPath     = $null
                    UacCancelled     = $false
                }
            }

            if ($null -eq $result) {
                Add-SidecarMessage -Sidecar $sidecar -Level 'error' `
                    -Text ("{0}: ApplyReal returned null" -f $slug)
                $itemArgs = @{
                    Sidecar        = $sidecar
                    Id             = "web:$slug"
                    Name           = $displayName
                    Category       = 'web'
                    SourceType     = 'web'
                    Status         = 'failed'
                    CurrentVersion = $installed
                    TargetVersion  = $targetVer
                }
                [void](Add-SidecarItem @itemArgs)
                continue
            }

            $uacCancelled = $false
            if ($result.PSObject.Properties['UacCancelled']) {
                $uacCancelled = [bool]$result.UacCancelled
            }

            $finalStatus = 'failed'
            if ($result.Success) {
                $finalStatus = 'success'
            } elseif ($uacCancelled) {
                $finalStatus = 'skipped'
            }

            $reportedTarget = $targetVer
            if ($result.PSObject.Properties['InstalledVersion'] -and
                $result.InstalledVersion) {
                $reportedTarget = [string]$result.InstalledVersion
            }
            $reportedExit = 0
            if ($result.PSObject.Properties['ExitCode']) {
                $reportedExit = [int]$result.ExitCode
            }
            $rebootRequired = $false
            if ($result.PSObject.Properties['RebootRequired']) {
                $rebootRequired = [bool]$result.RebootRequired
            }

            if (-not $result.Success -and $result.ErrorMessage) {
                Add-SidecarMessage -Sidecar $sidecar -Level 'error' `
                    -Text ("{0}: {1}" -f $slug, $result.ErrorMessage)
            } elseif ($uacCancelled) {
                Add-SidecarMessage -Sidecar $sidecar -Level 'warn' `
                    -Text ("{0}: operator cancelled UAC prompt" -f $slug)
            } elseif ($rebootRequired) {
                Add-SidecarMessage -Sidecar $sidecar -Level 'info' `
                    -Text ("{0}: installer succeeded; reboot required (exit 3010)" -f $slug)
            }

            $itemArgs = @{
                Sidecar        = $sidecar
                Id             = "web:$slug"
                Name           = $displayName
                Category       = 'web'
                SourceType     = 'web'
                Status         = $finalStatus
                CurrentVersion = $installed
                TargetVersion  = $reportedTarget
                ExitCode       = $reportedExit
            }
            [void](Add-SidecarItem @itemArgs)

            if ($rebootRequired) {
                # Best-effort: AscendoJson exposes Set-SidecarNeedsReboot
                # in newer builds; ignore if absent (compatibility shim).
                try {
                    if (Get-Command -Name 'Set-SidecarNeedsReboot' -ErrorAction SilentlyContinue) {
                        Set-SidecarNeedsReboot -Sidecar $sidecar -Value $true
                    } else {
                        $sidecar | Add-Member -NotePropertyName 'needs_reboot' -NotePropertyValue $true -Force
                    }
                } catch {}
            }
            continue
        }

        # ── Tier-B: trigger-only (open vendor URL) ──
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
