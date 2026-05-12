# =============================================================================
# adapters/windows/lib/AscendoNpm.psm1 -- npm helper functions (Windows)
# =============================================================================
# Sourced by adapters/windows/scripts/npm/*.ps1. PowerShell 5.1 + 7.x compatible.
#
# Provides:
#   Get-AscendoNpmBin             returns absolute path to npm (npm.cmd preferred)
#   Get-AscendoNpmInstalledVersion -PackageName <n>  reads from cached npm list
#   Get-AscendoNpmLatestVersion   -PackageName <n>  npm view <name> version
#   Read-AscendoNpmManifest       -Path <p>          parse manifest -> string[]
#   Test-AscendoNpmShouldSkip     -PackageName <n>   skip-list check (currently stub)
# =============================================================================

Set-StrictMode -Version Latest

# -- module-level caches ----------------------------------------------------
# npm list --depth=0 --json is expensive; cache once per script run.
$script:_NpmInstalledCache = $null
$script:_NpmLatestCache = @{}

# -----------------------------------------------------------------------------
# Get-AscendoNpmBin
# -----------------------------------------------------------------------------
function Get-AscendoNpmBin {
    <#
    .SYNOPSIS
        Return the absolute path to npm on Windows, or empty string if missing.

    .DESCRIPTION
        Resolution order:
          1. $env:ASCENDO_NPM_BIN_OVERRIDE if set + executable
          2. npm.cmd on PATH (the canonical Windows form from the Node installer)
          3. npm on PATH (rare, but possible via Git Bash / nvm-windows shims)
        Returns an empty string when nothing resolves.
    #>
    [CmdletBinding()]
    [OutputType([string])]
    param()

    if ($env:ASCENDO_NPM_BIN_OVERRIDE -and (Test-Path -LiteralPath $env:ASCENDO_NPM_BIN_OVERRIDE)) {
        return [string]$env:ASCENDO_NPM_BIN_OVERRIDE
    }

    foreach ($candidate in @('npm.cmd', 'npm')) {
        $cmd = Get-Command -Name $candidate -ErrorAction SilentlyContinue
        if ($null -ne $cmd -and $cmd.Source) {
            return [string]$cmd.Source
        }
    }
    return ''
}

# -----------------------------------------------------------------------------
# _Invoke-Npm  (private helper)
# -----------------------------------------------------------------------------
function _Invoke-Npm {
    <#
    .SYNOPSIS
        Spawn npm with stdout/stderr captured, return [string] stdout body.
        Return empty string on failure (no exceptions for empty output).
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [string]   $NpmBin,
        [Parameter(Mandatory)] [string[]] $Arguments
    )
    if (-not $NpmBin -or -not (Test-Path -LiteralPath $NpmBin)) { return '' }
    try {
        # Capture stdout, suppress stderr (npm outdated emits a banner there).
        $out = & $NpmBin @Arguments 2>$null | Out-String
        if ($null -eq $out) { return '' }
        return [string]$out
    } catch {
        Write-Verbose "_Invoke-Npm failed: $_"
        return ''
    }
}

# -----------------------------------------------------------------------------
# Get-AscendoNpmInstalledVersion
# -----------------------------------------------------------------------------
function Get-AscendoNpmInstalledVersion {
    <#
    .SYNOPSIS
        Return installed version of a globally-installed npm package.

    .DESCRIPTION
        Reads from a module-level cache populated lazily by calling
        ``npm list -g --depth=0 --json`` once per script run. Cache is
        cleared by re-importing the module (-Force).

        Returns the version string (e.g. "1.0.119") or $null when the
        package is not installed.
    #>
    [CmdletBinding()]
    [OutputType([string])]
    param(
        [Parameter(Mandatory)] [string] $PackageName
    )
    if (-not $PackageName) { return $null }

    if ($null -eq $script:_NpmInstalledCache) {
        $npm = Get-AscendoNpmBin
        if (-not $npm) {
            $script:_NpmInstalledCache = @{}
            return $null
        }
        $raw = _Invoke-Npm -NpmBin $npm `
            -Arguments @('list', '-g', '--depth=0', '--json')
        $cache = @{}
        if ($raw) {
            try {
                $obj = $raw | ConvertFrom-Json -ErrorAction Stop
                if ($null -ne $obj -and $obj.PSObject.Properties['dependencies']) {
                    $deps = $obj.dependencies
                    if ($null -ne $deps) {
                        foreach ($prop in $deps.PSObject.Properties) {
                            if ($null -ne $prop.Value -and $prop.Value.PSObject.Properties['version']) {
                                $cache[$prop.Name] = [string]$prop.Value.version
                            }
                        }
                    }
                }
            } catch {
                Write-Verbose "Get-AscendoNpmInstalledVersion: parse failed: $_"
            }
        }
        $script:_NpmInstalledCache = $cache
    }

    if ($script:_NpmInstalledCache.ContainsKey($PackageName)) {
        return [string]$script:_NpmInstalledCache[$PackageName]
    }
    return $null
}

# -----------------------------------------------------------------------------
# Get-AscendoNpmLatestVersion
# -----------------------------------------------------------------------------
function Get-AscendoNpmLatestVersion {
    <#
    .SYNOPSIS
        Return the latest published version of a package via ``npm view``.

    .DESCRIPTION
        Cached per-package for the duration of the script run. Returns
        $null on network failure / unknown package.
    #>
    [CmdletBinding()]
    [OutputType([string])]
    param(
        [Parameter(Mandatory)] [string] $PackageName
    )
    if (-not $PackageName) { return $null }
    if ($script:_NpmLatestCache.ContainsKey($PackageName)) {
        return [string]$script:_NpmLatestCache[$PackageName]
    }

    $npm = Get-AscendoNpmBin
    if (-not $npm) {
        $script:_NpmLatestCache[$PackageName] = $null
        return $null
    }
    $raw = _Invoke-Npm -NpmBin $npm `
        -Arguments @('view', $PackageName, 'version')
    if (-not $raw) {
        $script:_NpmLatestCache[$PackageName] = $null
        return $null
    }
    $trimmed = $raw.Trim()
    if (-not $trimmed) {
        $script:_NpmLatestCache[$PackageName] = $null
        return $null
    }
    # ``npm view <pkg> version`` prints just the version on one line.
    # If multi-line (warnings spill into stdout on some npm versions),
    # take the last non-empty line.
    $lines = $trimmed -split "`r?`n" | Where-Object { $_.Trim() -ne '' }
    if ($lines.Count -eq 0) {
        $script:_NpmLatestCache[$PackageName] = $null
        return $null
    }
    $version = ([string]$lines[-1]).Trim()
    $script:_NpmLatestCache[$PackageName] = $version
    return $version
}

# -----------------------------------------------------------------------------
# Read-AscendoNpmManifest
# -----------------------------------------------------------------------------
function Read-AscendoNpmManifest {
    <#
    .SYNOPSIS
        Parse an npm package manifest (one package per line).

    .DESCRIPTION
        File format: one package name per line, lines starting with # and
        blank lines are skipped. Trailing inline-comment text (after `#`)
        is stripped. Returns string[] of package names.

        Example file content:
            # comment
            eslint
            prettier  # always-latest
        Returns: @('eslint', 'prettier')
    #>
    [CmdletBinding()]
    [OutputType([string[]])]
    param(
        [Parameter(Mandatory)] [string] $Path
    )
    if (-not (Test-Path -LiteralPath $Path)) {
        return @()
    }
    $names = New-Object 'System.Collections.Generic.List[string]'
    Get-Content -LiteralPath $Path -Encoding UTF8 | ForEach-Object {
        $line = [string]$_
        # Strip trailing comment (everything after # including leading spaces).
        $idx = $line.IndexOf('#')
        if ($idx -ge 0) {
            $line = $line.Substring(0, $idx)
        }
        $trimmed = $line.Trim()
        if ($trimmed) {
            $names.Add($trimmed)
        }
    }
    return $names.ToArray()
}

# -----------------------------------------------------------------------------
# Test-AscendoNpmShouldSkip
# -----------------------------------------------------------------------------
function Test-AscendoNpmShouldSkip {
    <#
    .SYNOPSIS
        Should this package be skipped on apply? (currently always false).

    .DESCRIPTION
        Hook for future skip rules (e.g. ``npm`` self-update issues on
        certain Node versions). Today this is a stub returning $false
        for every package. Calling code is written defensively so adding
        skip rules later is a one-line change here.
    #>
    [CmdletBinding()]
    [OutputType([bool])]
    param(
        [Parameter(Mandatory)] [string] $PackageName
    )
    return $false
}

# -----------------------------------------------------------------------------
# Exports
# -----------------------------------------------------------------------------
Export-ModuleMember -Function `
    Get-AscendoNpmBin, `
    Get-AscendoNpmInstalledVersion, `
    Get-AscendoNpmLatestVersion, `
    Read-AscendoNpmManifest, `
    Test-AscendoNpmShouldSkip
