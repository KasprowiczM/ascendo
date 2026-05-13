# =============================================================================
# AscendoWebDiscovery.psm1 -- Windows registry-based web-app auto-discovery
# =============================================================================
#
# Windows-side mirror of adapters/macos/lib/web_discovery.sh. Walks the three
# Add-or-Remove-Programs (ARP) registry hives and emits one PSCustomObject per
# discovered app, fingerprinted with ownership (winget / msstore / curated /
# arp / unknown) and eligibility (eligible / ineligible / owned).
#
# Public surface:
#   Invoke-AscendoWebDiscovery   -- main entry. Returns PSCustomObject[].
#
# Default behaviour skips ARP entries that are:
#   * Microsoft system components (Windows updates, VC++ runtimes, KB-only)
#   * Driver / vendor uninstall stubs
#   * Already owned by winget / msstore / the curated web_apps.toml registry
#   * Caller-supplied ineligibility patterns
#
# Pass -IncludeUninstalled to also emit curated-registry entries that aren't
# installed on this host (so the SPA can show "potentially available" rows).
#
# Caller-extensible exclusion: $env:ASCENDO_WEB_INELIGIBLE_PATTERNS is a
# comma-separated list of regex patterns matched against DisplayName.
#
# Performance: registry walk is < 5s on a typical Win11 box. `winget list`
# results are cached per-process so successive Invoke-AscendoWebDiscovery
# calls (e.g. for unit tests) reuse the same lookup. The `winget list` call
# is gated by Test-Path + Get-Command + a 10s timeout; on hosts without
# winget (or where it hangs) the ownership probe degrades to empty rather
# than blocking the whole discovery.
#
# Compatibility: PowerShell 5.1 + 7.x. Set-StrictMode -Version Latest safe.
# =============================================================================

Set-StrictMode -Version Latest

# Registry roots scanned for ARP entries.
$script:_AscendoArpRoots = @(
    'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall',
    'HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall',
    'HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall'
)

# Hard-coded ineligibility patterns (DisplayName regex). Mirrors the
# macOS web_discovery.sh `_owned_by` ineligible cases plus Windows-specific
# system / driver shapes.
$script:_AscendoIneligibleNamePatterns = @(
    # Windows-shipped components
    '^Windows\s+(SDK|Driver|App|Runtime|Software|Subsystem|Assessment).*',
    '^Windows\s+Driver\s+Package.*',
    '^Windows\s+SDK\s+.*',
    # Microsoft developer / build runtime redistributables
    '^Microsoft\s+Visual\s+C\+\+\s+.*Redistributable.*',
    '^Microsoft\s+\.NET\s+(Runtime|SDK|Host|Targeting Pack|AppHost Pack|Framework|Core).*',
    '^Microsoft\s+ASP\.NET.*',
    '^Microsoft\s+Edge\s+(Update|WebView2\s+Runtime).*',
    # KB / update payloads
    '^KB[0-9]+',
    '^Security\s+Update\s+for\s+Microsoft.*',
    '^Update\s+for\s+Microsoft.*',
    '^Hotfix\s+for\s+Microsoft.*',
    # Driver packages
    '.*\s+Driver$',
    '.*\s+Drivers$',
    # Vendor uninstall stubs (e.g. "Uninstall HP Support Assistant")
    '^Uninstall\s+'
)

# Hard-coded ineligibility patterns matched against the bundle-id (registry
# key name). Catches MSI product GUIDs that look like update / hotfix
# bundles per Windows Installer conventions ({GUID}_isN suffix is Inno
# Setup uninstaller key for an update bundle).
$script:_AscendoIneligibleKeyPatterns = @(
    # Inno Setup update bundles use a numeric suffix; the original install
    # registers as `{GUID}_is1`, updates as `{GUID}_is2`, etc. The base
    # (_is1) is the "real" app; later suffixes are update bundles.
    '^\{[0-9A-Fa-f-]{30,38}\}_is[2-9][0-9]*$'
)

# Module-level caches. Cleared by Clear-AscendoWebDiscoveryCache (test hook).
$script:_AscendoWingetIdCache = $null     # @{ id = $true }
$script:_AscendoMsstoreIdCache = $null    # @{ id = $true }
$script:_AscendoCuratedNameCache = $null  # @{ displayName (lowercased) = slug }
$script:_AscendoCuratedKeyCache = $null   # @{ uninstall_key (lowercased) = slug }


# -----------------------------------------------------------------------------
# Clear-AscendoWebDiscoveryCache
# -----------------------------------------------------------------------------
function Clear-AscendoWebDiscoveryCache {
    <#
    .SYNOPSIS
        Reset module-level ownership caches. Intended for tests that want a
        clean lookup state between scenarios.
    #>
    [CmdletBinding()]
    param()

    $script:_AscendoWingetIdCache = $null
    $script:_AscendoMsstoreIdCache = $null
    $script:_AscendoCuratedNameCache = $null
    $script:_AscendoCuratedKeyCache = $null
}


# -----------------------------------------------------------------------------
# _Get-RegPropSafe (internal helper, StrictMode-safe)
# -----------------------------------------------------------------------------
function _Get-RegPropSafe {
    param($Obj, [string] $Name)
    if ($null -eq $Obj) { return $null }
    if (-not $Obj.PSObject.Properties[$Name]) { return $null }
    return $Obj.$Name
}


# -----------------------------------------------------------------------------
# _Get-AscendoWingetIds (internal; cached per-process)
# -----------------------------------------------------------------------------
function _Get-AscendoWingetIds {
    <#
    .SYNOPSIS
        Return two hashtables: winget-source IDs and msstore-source IDs.
    .DESCRIPTION
        Calls `winget list --source winget` and `winget list --source msstore`,
        parses the column-tabular output, and returns lookup hashtables keyed
        on PackageId (lowercased). Cached per-process; honour the
        ASCENDO_WEB_DISCOVERY_NO_WINGET env var to skip the probe entirely
        (useful for tests + situations where winget hangs).

        Returns @{ winget = @{}; msstore = @{} } on failure rather than
        throwing -- ownership detection is advisory; absence means
        Eligibility=eligible rather than Eligibility=owned.
    #>
    [CmdletBinding()]
    [OutputType([hashtable])]
    param()

    # Returning early respects test mocking: tests that pre-populate the
    # caches via SetAscendoWebDiscoveryWingetCache stay unmodified.
    if ($null -ne $script:_AscendoWingetIdCache -and $null -ne $script:_AscendoMsstoreIdCache) {
        return @{
            winget  = $script:_AscendoWingetIdCache
            msstore = $script:_AscendoMsstoreIdCache
        }
    }

    $script:_AscendoWingetIdCache = @{}
    $script:_AscendoMsstoreIdCache = @{}

    if ($env:ASCENDO_WEB_DISCOVERY_NO_WINGET) {
        return @{
            winget  = $script:_AscendoWingetIdCache
            msstore = $script:_AscendoMsstoreIdCache
        }
    }

    $wingetCmd = Get-Command -Name 'winget' -ErrorAction SilentlyContinue
    if ($null -eq $wingetCmd) {
        return @{
            winget  = $script:_AscendoWingetIdCache
            msstore = $script:_AscendoMsstoreIdCache
        }
    }

    # Prefer the AscendoWinget parser if it's been imported by the caller
    # (typical for scripts that already use Get-WingetInstalled). Lookup is
    # by command name to avoid hard-coupling discovery to the winget module.
    $hasParser = $null -ne (Get-Command -Name 'Get-WingetInstalled' -ErrorAction SilentlyContinue)
    if (-not $hasParser) {
        # No parser available -- discovery returns empty ownership maps and
        # relies on the registry walk alone. The macOS analogue degrades
        # similarly when brew is not on PATH.
        return @{
            winget  = $script:_AscendoWingetIdCache
            msstore = $script:_AscendoMsstoreIdCache
        }
    }

    try {
        $rows = @(Get-WingetInstalled -ErrorAction Stop)
    } catch {
        Write-Verbose "AscendoWebDiscovery: Get-WingetInstalled failed: $_"
        return @{
            winget  = $script:_AscendoWingetIdCache
            msstore = $script:_AscendoMsstoreIdCache
        }
    }

    foreach ($row in $rows) {
        if (-not $row.PSObject.Properties['Id']) { continue }
        $id = [string]$row.Id
        if ([string]::IsNullOrWhiteSpace($id)) { continue }
        $key = $id.Trim().ToLowerInvariant()

        $source = ''
        if ($row.PSObject.Properties['Source']) {
            $source = [string]$row.Source
        }
        $sourceLower = $source.Trim().ToLowerInvariant()

        if ($sourceLower -eq 'msstore') {
            $script:_AscendoMsstoreIdCache[$key] = $true
        } elseif ($sourceLower -eq 'winget') {
            $script:_AscendoWingetIdCache[$key] = $true
        } elseif ($id -like 'MSIX\*') {
            $script:_AscendoMsstoreIdCache[$key] = $true
        } else {
            # ARP rows from winget surface here too; we don't mark them as
            # owned because that's what the WebManager actually wants to see.
        }
    }

    return @{
        winget  = $script:_AscendoWingetIdCache
        msstore = $script:_AscendoMsstoreIdCache
    }
}


# -----------------------------------------------------------------------------
# _Get-AscendoCuratedOwnership (internal; cached per-process)
# -----------------------------------------------------------------------------
function _Get-AscendoCuratedOwnership {
    <#
    .SYNOPSIS
        Return two hashtables describing curated web_apps.toml entries:
        one keyed by lowercased DisplayName, one by lowercased
        windows_uninstall_key. Both map to the slug.
    .DESCRIPTION
        Reads the registry via the Python web_registry.py shim (the same
        path check.ps1 uses). On failure (no shim, no Python, malformed
        TOML) the caches stay empty and curated-ownership detection is
        skipped -- discovery falls back to winget/msstore detection only.
    #>
    [CmdletBinding()]
    [OutputType([hashtable])]
    param(
        [Parameter(Mandatory)] [string] $ConfigDir,
        [Parameter(Mandatory)] [string] $LibDir,
        [Parameter()] [string] $UserOverride = ''
    )

    if ($null -ne $script:_AscendoCuratedNameCache -and $null -ne $script:_AscendoCuratedKeyCache) {
        return @{
            byName = $script:_AscendoCuratedNameCache
            byKey  = $script:_AscendoCuratedKeyCache
        }
    }

    $script:_AscendoCuratedNameCache = @{}
    $script:_AscendoCuratedKeyCache = @{}

    # Use the existing AscendoWeb module if it has been imported. Avoids
    # importing it from within Discovery (which would create a circular
    # dependency in the test harness).
    $invoke = Get-Command -Name 'Invoke-WebRegistry' -ErrorAction SilentlyContinue
    if ($null -eq $invoke) {
        return @{
            byName = $script:_AscendoCuratedNameCache
            byKey  = $script:_AscendoCuratedKeyCache
        }
    }

    $slugs = @()
    try {
        $listArgs = @{
            ConfigDir = $ConfigDir
            LibDir    = $LibDir
            Action    = 'list-slugs'
        }
        if ($UserOverride) { $listArgs['UserOverride'] = $UserOverride }
        $slugs = @(Invoke-WebRegistry @listArgs)
    } catch {
        Write-Verbose "AscendoWebDiscovery: Invoke-WebRegistry list-slugs failed: $_"
        return @{
            byName = $script:_AscendoCuratedNameCache
            byKey  = $script:_AscendoCuratedKeyCache
        }
    }

    foreach ($slug in $slugs) {
        if ([string]::IsNullOrWhiteSpace($slug)) { continue }
        $cfg = $null
        try {
            $getArgs = @{
                ConfigDir = $ConfigDir
                LibDir    = $LibDir
                Action    = 'get-app'
                Slug      = $slug
            }
            if ($UserOverride) { $getArgs['UserOverride'] = $UserOverride }
            $cfg = Invoke-WebRegistry @getArgs
        } catch {
            Write-Verbose "AscendoWebDiscovery: get-app $slug failed: $_"
            continue
        }
        if ($null -eq $cfg) { continue }

        $dnProp = $cfg.PSObject.Properties['display_name']
        if ($null -ne $dnProp -and $dnProp.Value) {
            $script:_AscendoCuratedNameCache[([string]$dnProp.Value).Trim().ToLowerInvariant()] = $slug
        }
        $ukProp = $cfg.PSObject.Properties['windows_uninstall_key']
        if ($null -ne $ukProp -and $ukProp.Value) {
            $script:_AscendoCuratedKeyCache[([string]$ukProp.Value).Trim().ToLowerInvariant()] = $slug
        }
    }

    return @{
        byName = $script:_AscendoCuratedNameCache
        byKey  = $script:_AscendoCuratedKeyCache
    }
}


# -----------------------------------------------------------------------------
# _ConvertTo-AscendoSlug (internal)
# -----------------------------------------------------------------------------
function _ConvertTo-AscendoSlug {
    param([string] $Text)
    if ([string]::IsNullOrWhiteSpace($Text)) { return 'unknown' }
    $lower = $Text.Trim().ToLowerInvariant()
    # Replace any run of non-alnum chars with a single hyphen.
    $slug = [System.Text.RegularExpressions.Regex]::Replace($lower, '[^a-z0-9]+', '-')
    $slug = $slug.Trim('-')
    if (-not $slug) { return 'unknown' }
    if ($slug.Length -gt 64) { $slug = $slug.Substring(0, 64).Trim('-') }
    if (-not $slug) { return 'unknown' }
    return $slug
}


# -----------------------------------------------------------------------------
# _Test-AscendoNamePattern (internal)
# -----------------------------------------------------------------------------
function _Test-AscendoNamePattern {
    param(
        [string] $Value,
        [string[]] $Patterns
    )
    if ([string]::IsNullOrWhiteSpace($Value)) { return $false }
    if (-not $Patterns) { return $false }
    foreach ($pat in $Patterns) {
        if (-not $pat) { continue }
        try {
            if ($Value -match $pat) { return $true }
        } catch {
            # Skip patterns that don't compile cleanly so a single bad
            # extra-pattern from env doesn't blow up discovery.
            Write-Verbose "AscendoWebDiscovery: skipping malformed regex pattern: $pat"
        }
    }
    return $false
}


# -----------------------------------------------------------------------------
# Invoke-AscendoWebDiscovery
# -----------------------------------------------------------------------------
function Invoke-AscendoWebDiscovery {
    <#
    .SYNOPSIS
        Walk the Add/Remove Programs registry, fingerprint each entry, and
        emit one PSCustomObject per discovered installed app.
    .DESCRIPTION
        Mirror of adapters/macos/lib/web_discovery.sh for Windows. Each
        emitted object has:
          Slug             slug derived from DisplayName
          BundleId         registry KeyName (Inno / NSIS / MSI GUID)
          DisplayName      from registry DisplayName
          Version          DisplayVersion (may be empty)
          Publisher        Publisher (may be empty)
          InstallLocation  InstallLocation (may be empty)
          UninstallString  UninstallString (or QuietUninstallString)
          Hive             'HKLM' / 'HKCU'
          Source           winget / msstore / curated / arp / unknown
          Eligibility      eligible / ineligible / owned
          Handler          builtin (always; rich classification deferred)
          Reason           short string explaining ineligible/owned

        Default behaviour skips ineligible and owned rows. Pass
        -IncludeOwned / -IncludeIneligible to see them anyway (mostly
        for diagnostics). Pass -IncludeUninstalled to also append rows
        for curated apps that aren't installed (so the SPA can surface
        them as "potentially available").
    .PARAMETER ExcludePatterns
        Optional caller-supplied DisplayName regex patterns. Mark matches
        as Eligibility=ineligible with Reason='excluded'.
    .PARAMETER ConfigDir
        Path to adapters/windows/config/. Defaults to the sibling config/
        relative to this module's directory. Used only for curated
        ownership detection.
    .PARAMETER LibDir
        Path to adapters/windows/lib/. Defaults to this module's directory.
    .PARAMETER UserOverride
        Optional user override TOML path for the curated registry.
    .PARAMETER IncludeUninstalled
        When set, append rows for curated apps that aren't installed on
        this host. Eligibility=eligible, Source=curated, Version=''.
    .PARAMETER IncludeOwned
        When set, also emit rows marked Eligibility=owned (would otherwise
        be filtered out). Useful for `validate-windows.ps1` audits.
    .PARAMETER IncludeIneligible
        When set, also emit rows marked Eligibility=ineligible.
    #>
    [CmdletBinding()]
    [OutputType([pscustomobject[]])]
    param(
        [Parameter()] [string[]] $ExcludePatterns = @(),
        [Parameter()] [string] $ConfigDir = '',
        [Parameter()] [string] $LibDir = '',
        [Parameter()] [string] $UserOverride = '',
        [Parameter()] [switch] $IncludeUninstalled,
        [Parameter()] [switch] $IncludeOwned,
        [Parameter()] [switch] $IncludeIneligible
    )

    # Resolve default ConfigDir / LibDir relative to this module.
    if (-not $LibDir) {
        $LibDir = Split-Path -Parent $PSCommandPath
    }
    if (-not $ConfigDir) {
        $AdapterDir = Split-Path -Parent $LibDir
        $ConfigDir = Join-Path $AdapterDir 'config'
    }

    # Compose ineligibility patterns: hard-coded + caller-supplied + env-var.
    $allNamePatterns = @($script:_AscendoIneligibleNamePatterns)
    if ($ExcludePatterns) {
        foreach ($p in $ExcludePatterns) {
            if ($p) { $allNamePatterns += $p }
        }
    }
    if ($env:ASCENDO_WEB_INELIGIBLE_PATTERNS) {
        foreach ($p in ($env:ASCENDO_WEB_INELIGIBLE_PATTERNS -split ',')) {
            $trimmed = $p.Trim()
            if ($trimmed) { $allNamePatterns += $trimmed }
        }
    }

    # Load ownership signals.
    $owns = _Get-AscendoWingetIds
    $curated = _Get-AscendoCuratedOwnership -ConfigDir $ConfigDir -LibDir $LibDir -UserOverride $UserOverride

    $results = New-Object 'System.Collections.Generic.List[object]'
    $seenInstalledKeys = @{}

    foreach ($root in $script:_AscendoArpRoots) {
        if (-not (Test-Path -LiteralPath $root)) { continue }
        try {
            $children = Get-ChildItem -Path $root -ErrorAction SilentlyContinue
        } catch {
            Write-Verbose "AscendoWebDiscovery: Get-ChildItem $root failed: $_"
            continue
        }
        foreach ($key in $children) {
            try {
                $props = Get-ItemProperty -Path $key.PSPath -ErrorAction SilentlyContinue
            } catch {
                continue
            }
            if (-not $props) { continue }

            $displayName = _Get-RegPropSafe $props 'DisplayName'
            if (-not $displayName) { continue }

            # Skip ARP filtering basics (mirrors scripts/arp/check.ps1).
            $sysComp = _Get-RegPropSafe $props 'SystemComponent'
            if ($null -ne $sysComp -and $sysComp -eq 1) { continue }

            $parentKey = _Get-RegPropSafe $props 'ParentKeyName'
            if ($parentKey) { continue }

            $releaseType = _Get-RegPropSafe $props 'ReleaseType'
            if ($releaseType) {
                # Patch / Hotfix / Update bundles are not standalone apps.
                $rt = ([string]$releaseType).Trim().ToLowerInvariant()
                if ($rt -in @('hotfix','update','security update','servicepack','updaterollup','patch')) {
                    continue
                }
            }

            $uninstall = _Get-RegPropSafe $props 'UninstallString'
            $quietUninstall = _Get-RegPropSafe $props 'QuietUninstallString'
            $hasUninstall = ($uninstall -or $quietUninstall)

            $keyName = [string]$key.PSChildName
            $version = _Get-RegPropSafe $props 'DisplayVersion'
            $publisher = _Get-RegPropSafe $props 'Publisher'
            $installLoc = _Get-RegPropSafe $props 'InstallLocation'

            $hive = 'unknown'
            if ($key.PSObject.Properties['PSDrive'] -and $key.PSDrive -and $key.PSDrive.PSObject.Properties['Name']) {
                $hive = [string]$key.PSDrive.Name
            }

            # Skip entries with no uninstaller AND no DisplayVersion -- they
            # are typically pseudo-entries (e.g. some Windows feature
            # placeholders).
            if (-not $hasUninstall -and -not $version) { continue }

            # ---- Eligibility classification ----

            $eligibility = 'eligible'
            $source = 'arp'
            $reason = ''

            # 1. Microsoft system components: Publisher matches Microsoft +
            #    DisplayName matches a system-component pattern.
            $publisherLower = ''
            if ($publisher) { $publisherLower = ([string]$publisher).Trim().ToLowerInvariant() }
            $looksMicrosoft = ($publisherLower -match '^microsoft(\s+(corp|corporation))?\.?$')

            if ($looksMicrosoft) {
                if (_Test-AscendoNamePattern -Value ([string]$displayName) -Patterns $allNamePatterns) {
                    $eligibility = 'ineligible'
                    $reason = 'microsoft_system_component'
                }
            } else {
                # 2. Generic name-pattern check (driver / stub / KB) applies
                #    to any publisher.
                if (_Test-AscendoNamePattern -Value ([string]$displayName) -Patterns $allNamePatterns) {
                    $eligibility = 'ineligible'
                    $reason = 'name_pattern'
                }
            }

            # 3. Inno-setup update bundle key (`{GUID}_isN` for N >= 2) is an
            #    update payload, not the base app.
            if ($eligibility -eq 'eligible' -and
                (_Test-AscendoNamePattern -Value $keyName -Patterns $script:_AscendoIneligibleKeyPatterns)) {
                $eligibility = 'ineligible'
                $reason = 'inno_update_bundle'
            }

            # 4. Owned-by-winget / -msstore (by KeyName).
            if ($eligibility -eq 'eligible') {
                $keyNameLower = $keyName.Trim().ToLowerInvariant()
                if ($owns.winget.ContainsKey($keyNameLower)) {
                    $eligibility = 'owned'
                    $source = 'winget'
                    $reason = 'owned_by_winget'
                } elseif ($owns.msstore.ContainsKey($keyNameLower)) {
                    $eligibility = 'owned'
                    $source = 'msstore'
                    $reason = 'owned_by_msstore'
                }
            }

            # 5. Curated registry match by name OR uninstall key.
            if ($eligibility -eq 'eligible') {
                $nameKey = ([string]$displayName).Trim().ToLowerInvariant()
                $keyLow = $keyName.Trim().ToLowerInvariant()
                $curatedSlug = ''
                if ($curated.byKey.ContainsKey($keyLow)) {
                    $curatedSlug = [string]$curated.byKey[$keyLow]
                } elseif ($curated.byName.ContainsKey($nameKey)) {
                    $curatedSlug = [string]$curated.byName[$nameKey]
                }
                if ($curatedSlug) {
                    $eligibility = 'owned'
                    $source = 'curated'
                    $reason = "curated:$curatedSlug"
                    # Track so -IncludeUninstalled doesn't re-emit this.
                    $seenInstalledKeys[$curatedSlug] = $true
                }
            }

            # Emit (or skip based on caller flags).
            if ($eligibility -eq 'ineligible' -and -not $IncludeIneligible) { continue }
            if ($eligibility -eq 'owned' -and -not $IncludeOwned) { continue }

            $slug = _ConvertTo-AscendoSlug -Text ([string]$displayName)

            $uninstallStr = if ($quietUninstall) { [string]$quietUninstall } elseif ($uninstall) { [string]$uninstall } else { '' }

            $obj = [pscustomobject]@{
                Slug            = $slug
                BundleId        = $keyName
                DisplayName     = [string]$displayName
                Version         = if ($version) { [string]$version } else { '' }
                Publisher       = if ($publisher) { [string]$publisher } else { '' }
                InstallLocation = if ($installLoc) { [string]$installLoc } else { '' }
                UninstallString = $uninstallStr
                Hive            = $hive
                Source          = $source
                Eligibility     = $eligibility
                Handler         = 'builtin'
                Reason          = $reason
            }
            $results.Add($obj)
        }
    }

    # -IncludeUninstalled: append curated entries that didn't surface in the
    # ARP walk so the SPA can show them as "available but not installed".
    if ($IncludeUninstalled) {
        foreach ($entry in $curated.byKey.GetEnumerator()) {
            $slug = [string]$entry.Value
            if ($seenInstalledKeys.ContainsKey($slug)) { continue }
            $obj = [pscustomobject]@{
                Slug            = $slug
                BundleId        = [string]$entry.Key
                DisplayName     = $slug
                Version         = ''
                Publisher       = ''
                InstallLocation = ''
                UninstallString = ''
                Hive            = ''
                Source          = 'curated'
                Eligibility     = 'eligible'
                Handler         = 'builtin'
                Reason          = 'curated_not_installed'
            }
            $results.Add($obj)
        }
    }

    # DO NOT use ``return ,$results.ToArray()`` here. The comma operator
    # wraps the array in a 1-element outer array; combined with the typical
    # caller idiom ``@(Invoke-AscendoWebDiscovery ...)`` PowerShell sees a
    # single-element array containing the N PSCustomObjects (and then
    # ``$d[0]`` is the inner array, member-access maps over every element
    # via space-joined strings, etc.). Bug observed on DP5520WMK
    # 2026-05-13: web check sidecar collapsed ~90 apps into one row with
    # name = "AutoHotkey CCleaner Dell ...". Return the array via standard
    # PowerShell enumeration; ``@()`` wrappers at the call site collect
    # the N items correctly.
    return $results.ToArray()
}


# -----------------------------------------------------------------------------
# Exports
# -----------------------------------------------------------------------------
Export-ModuleMember -Function `
    Invoke-AscendoWebDiscovery, `
    Clear-AscendoWebDiscoveryCache
