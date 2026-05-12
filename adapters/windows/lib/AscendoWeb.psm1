# =============================================================================
# AscendoWeb.psm1 - shared utilities for the WebManager phase scripts
# =============================================================================
#
# Public surface:
#   Get-WebInstalledVersion    - scan HKLM + HKCU Uninstall registry hives for
#                                 a given windows_uninstall_key and return its
#                                 DisplayVersion value, or $null if absent.
#   Invoke-WebRegistry         - call the Python web_registry.py CLI shim and
#                                 parse its JSON output. Returns a hashtable
#                                 (one app) or array (--list-slugs).
#   Compare-WebVersion          - simple "is candidate newer than installed"
#                                 comparator (semver-ish: dot-separated nums).
#
# Compatibility: PowerShell 5.1 + 7.x.
# =============================================================================

Set-StrictMode -Version Latest

# -----------------------------------------------------------------------------
# Get-WebInstalledVersion
# -----------------------------------------------------------------------------

function Get-WebInstalledVersion {
    <#
    .SYNOPSIS
        Look up the installed version of a Windows app by Uninstall-registry
        key name. Returns $null when not installed on this host.
    .DESCRIPTION
        Scans both:
          HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\<key>
          HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\<key>
          HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\<key>
        and returns the first DisplayVersion value found. The MSI product
        ID form ({GUID}) and the human-readable installer name (Inno / NSIS)
        are both supported -- caller passes whichever shape matches the
        target installer's registry footprint.
    .PARAMETER UninstallKey
        Exact name of the Uninstall registry sub-key (e.g. 'Discord',
        'OBS Studio', '{12345678-1234-...}').
    .OUTPUTS
        [string] - the DisplayVersion value, or $null if the key is absent
                   or has no DisplayVersion.
    #>
    [CmdletBinding()]
    [OutputType([string])]
    param(
        [Parameter(Mandatory)] [string] $UninstallKey
    )

    if ([string]::IsNullOrWhiteSpace($UninstallKey)) {
        return $null
    }

    $roots = @(
        'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall',
        'HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall',
        'HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall'
    )

    foreach ($root in $roots) {
        $path = Join-Path $root $UninstallKey
        if (-not (Test-Path -LiteralPath $path)) { continue }
        try {
            $props = Get-ItemProperty -LiteralPath $path -ErrorAction SilentlyContinue
        } catch {
            continue
        }
        if ($null -eq $props) { continue }
        # PSObject.Properties safe access (StrictMode + missing prop = no throw).
        $versionProp = $props.PSObject.Properties['DisplayVersion']
        if ($null -ne $versionProp -and $versionProp.Value) {
            return [string]$versionProp.Value
        }
    }
    return $null
}

# -----------------------------------------------------------------------------
# Invoke-WebRegistry
# -----------------------------------------------------------------------------

function Invoke-WebRegistry {
    <#
    .SYNOPSIS
        Invoke the Python web_registry.py CLI shim and return parsed output.
    .PARAMETER ConfigDir
        Path to adapters/windows/config/ (containing web_apps.toml).
    .PARAMETER LibDir
        Path to adapters/windows/lib/ (containing web_registry.py).
    .PARAMETER Action
        'list-slugs' | 'get-app' | 'validate'.
    .PARAMETER Slug
        Required when Action='get-app'.
    .PARAMETER UserOverride
        Optional path to user override TOML.
    .OUTPUTS
        For 'list-slugs': [string[]] of slugs.
        For 'get-app':    [hashtable] with the app fields.
        For 'validate':   [bool] $true on success.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [string] $ConfigDir,
        [Parameter(Mandatory)] [string] $LibDir,
        [Parameter(Mandatory)]
            [ValidateSet('list-slugs','get-app','validate')]
            [string] $Action,
        [Parameter()] [string] $Slug,
        [Parameter()] [string] $UserOverride
    )

    $shim = Join-Path $LibDir 'web_registry.py'
    $shipped = Join-Path $ConfigDir 'web_apps.toml'

    if (-not (Test-Path -LiteralPath $shim)) {
        throw "web_registry.py shim not found at $shim"
    }
    if (-not (Test-Path -LiteralPath $shipped)) {
        throw "web_apps.toml registry not found at $shipped"
    }

    # Discovery order matters on Windows. `python` is often a Microsoft Store
    # shim or an older 3.x install that lacks pydantic / ascendo_windows. The
    # `py` launcher picks the highest-installed Python (where Ascendo lives).
    # See web_registry.py: it also self-bootstraps sys.path so any 3.11+ with
    # pydantic available will work.
    $python = $null
    foreach ($cand in @('py', 'python3', 'python')) {
        $cmd = Get-Command -Name $cand -ErrorAction SilentlyContinue
        if ($null -ne $cmd) { $python = $cmd.Source; break }
    }
    if ($null -eq $python) {
        throw 'no Python interpreter on PATH (looked for py, python3, python)'
    }

    $pyArgs = @($shim, '--shipped', $shipped)
    if ($UserOverride -and (Test-Path -LiteralPath $UserOverride)) {
        $pyArgs += @('--user-override', $UserOverride)
    }

    switch ($Action) {
        'list-slugs' {
            $pyArgs += '--list-slugs'
            $output = & $python @pyArgs 2>&1
            if ($LASTEXITCODE -ne 0) {
                throw "web_registry.py --list-slugs failed (exit $LASTEXITCODE): $output"
            }
            # Split into non-empty lines.
            return @($output -split "`r?`n" | Where-Object { $_ -and $_.Trim() })
        }
        'get-app' {
            if (-not $Slug) { throw "Action='get-app' requires -Slug" }
            $pyArgs += @('--get-app', $Slug)
            $output = & $python @pyArgs 2>&1
            if ($LASTEXITCODE -ne 0) {
                throw "web_registry.py --get-app $Slug failed (exit $LASTEXITCODE): $output"
            }
            $line = (($output -split "`r?`n") | Where-Object { $_ -and $_.Trim() } | Select-Object -First 1)
            if (-not $line) {
                throw "web_registry.py --get-app $Slug returned empty output"
            }
            # Parse the JSON; ConvertFrom-Json returns a PSCustomObject on PS 5/7.
            return ($line | ConvertFrom-Json)
        }
        'validate' {
            $pyArgs += '--validate'
            # Capture output so callers can see WHY validation failed
            # (previously routed to *> $null, hiding ImportError / etc.).
            $output = & $python @pyArgs 2>&1
            if ($LASTEXITCODE -ne 0) {
                throw "web_registry.py --validate failed (exit $LASTEXITCODE): $output"
            }
            return $true
        }
    }
}

# -----------------------------------------------------------------------------
# Compare-WebVersion
# -----------------------------------------------------------------------------

function Compare-WebVersion {
    <#
    .SYNOPSIS
        Return $true when Candidate is strictly newer than Installed, $false
        otherwise (including the equal case).
    .DESCRIPTION
        Splits both strings on '.' and compares component-wise as integers
        when both sides parse, otherwise lexicographically. Trailing missing
        components are treated as 0 (so '1.2' == '1.2.0'). Non-numeric
        suffixes (e.g. '1.2.3-rc1') are compared lexicographically after
        the matching numeric prefix. This is "semver-lite" -- sufficient for
        the apps in our registry; we don't pretend to fully parse PEP 440
        or semver-2.0.
    .PARAMETER Installed
        The installed version string (DisplayVersion from registry).
    .PARAMETER Candidate
        The candidate version string from the upstream probe.
    .OUTPUTS
        [bool]
    #>
    [CmdletBinding()]
    [OutputType([bool])]
    param(
        [Parameter(Mandatory)] [AllowEmptyString()] [string] $Installed,
        [Parameter(Mandatory)] [AllowEmptyString()] [string] $Candidate
    )

    if ([string]::IsNullOrWhiteSpace($Installed)) { return $true }
    if ([string]::IsNullOrWhiteSpace($Candidate)) { return $false }
    if ($Installed -eq $Candidate) { return $false }

    $iParts = $Installed.Split('.')
    $cParts = $Candidate.Split('.')
    $max = [Math]::Max($iParts.Count, $cParts.Count)

    for ($idx = 0; $idx -lt $max; $idx++) {
        $iv = if ($idx -lt $iParts.Count) { $iParts[$idx] } else { '0' }
        $cv = if ($idx -lt $cParts.Count) { $cParts[$idx] } else { '0' }
        if ($iv -eq $cv) { continue }

        $iInt = 0; $cInt = 0
        $iParse = [int]::TryParse($iv, [ref]$iInt)
        $cParse = [int]::TryParse($cv, [ref]$cInt)

        if ($iParse -and $cParse) {
            if ($cInt -gt $iInt) { return $true }
            if ($cInt -lt $iInt) { return $false }
            continue
        }
        # Fall back to lexicographic when either component isn't a clean int.
        $cmp = [string]::Compare($cv, $iv, [System.StringComparison]::Ordinal)
        if ($cmp -gt 0) { return $true }
        if ($cmp -lt 0) { return $false }
    }
    return $false
}

Export-ModuleMember -Function @(
    'Get-WebInstalledVersion',
    'Invoke-WebRegistry',
    'Compare-WebVersion'
)
