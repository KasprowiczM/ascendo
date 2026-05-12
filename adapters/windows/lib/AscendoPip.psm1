# =============================================================================
# adapters/windows/lib/AscendoPip.psm1 -- pip helper functions (Windows)
# =============================================================================
# Sourced by adapters/windows/scripts/pip/*.ps1. PowerShell 5.1 + 7.x compatible.
#
# Provides:
#   Get-AscendoPipCommand          returns @($exe, @($args)) tuple for invoking pip
#   Get-AscendoPipInstalledVersion -PackageName <n>  cached from pip list --format=json
#   Get-AscendoPipLatestVersion    -PackageName <n>  pypi.org/pypi/<n>/json
#   Read-AscendoPipManifest        -Path <p>          parse manifest -> string[]
#   Test-AscendoPipShouldSkip      -PackageName <n>   skip pip/setuptools/wheel
# =============================================================================

Set-StrictMode -Version Latest

# -- module-level caches ----------------------------------------------------
$script:_PipInstalledCache = $null
$script:_PipLatestCache = @{}
# Tuple ([string]$exe, [string[]]$leadingArgs). Cached so repeat lookups
# don't re-probe PATH.
$script:_PipCommand = $null

# -- enable TLS 1.2 ----------------------------------------------------------
# Windows PowerShell 5.1 defaults to TLS 1.0 which PyPI rejects.
try {
    [Net.ServicePointManager]::SecurityProtocol = `
        [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
} catch {
    # Best-effort; the user may not be calling our network functions at all.
}

# -----------------------------------------------------------------------------
# Get-AscendoPipCommand
# -----------------------------------------------------------------------------
function Get-AscendoPipCommand {
    <#
    .SYNOPSIS
        Return a 2-element array @($exe, [string[]]$leadingArgs) for
        invoking pip. Empty array when nothing resolves.

    .DESCRIPTION
        Resolution order:
          1. $env:ASCENDO_PIP_BIN_OVERRIDE if set + executable -> ($override, @())
          2. pip3 on PATH                                       -> ($exe, @())
          3. pip on PATH                                        -> ($exe, @())
          4. py launcher on PATH                                -> ($exe, @('-m','pip'))
        The leading args (typically empty, or ``-m pip`` for the py
        launcher) are prepended to whatever the caller wants to run.
    #>
    [CmdletBinding()]
    [OutputType([object[]])]
    param()

    if ($null -ne $script:_PipCommand) { return $script:_PipCommand }

    if ($env:ASCENDO_PIP_BIN_OVERRIDE -and (Test-Path -LiteralPath $env:ASCENDO_PIP_BIN_OVERRIDE)) {
        $script:_PipCommand = @([string]$env:ASCENDO_PIP_BIN_OVERRIDE, @())
        return $script:_PipCommand
    }

    foreach ($candidate in @('pip3', 'pip')) {
        $cmd = Get-Command -Name $candidate -ErrorAction SilentlyContinue
        if ($null -ne $cmd -and $cmd.Source) {
            $script:_PipCommand = @([string]$cmd.Source, @())
            return $script:_PipCommand
        }
    }

    $py = Get-Command -Name 'py' -ErrorAction SilentlyContinue
    if ($null -ne $py -and $py.Source) {
        $script:_PipCommand = @([string]$py.Source, @('-m', 'pip'))
        return $script:_PipCommand
    }

    $script:_PipCommand = @()
    return $script:_PipCommand
}

# -----------------------------------------------------------------------------
# Get-AscendoPipBin (compatibility alias - simple path or $null)
# -----------------------------------------------------------------------------
function Get-AscendoPipBin {
    <#
    .SYNOPSIS
        Return the absolute path to the pip launcher, or empty string.

    .DESCRIPTION
        Convenience wrapper around Get-AscendoPipCommand for tests /
        callers that just want a presence-check string. For real
        invocations callers should use Get-AscendoPipCommand to get the
        leading args too.
    #>
    [CmdletBinding()]
    [OutputType([string])]
    param()
    $cmd = Get-AscendoPipCommand
    if ($null -eq $cmd -or $cmd.Count -lt 1) { return '' }
    return [string]$cmd[0]
}

# -----------------------------------------------------------------------------
# _Invoke-Pip  (private helper)
# -----------------------------------------------------------------------------
function _Invoke-Pip {
    <#
    .SYNOPSIS
        Spawn pip with stdout captured, return [string] body. Empty on failure.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [string[]] $Arguments
    )
    $cmd = Get-AscendoPipCommand
    if ($null -eq $cmd -or $cmd.Count -lt 1) { return '' }
    $exe = [string]$cmd[0]
    $leading = @($cmd[1])
    $full = @()
    foreach ($a in $leading) { if ($null -ne $a) { $full += [string]$a } }
    foreach ($a in $Arguments) { $full += [string]$a }
    try {
        $out = & $exe @full 2>$null | Out-String
        if ($null -eq $out) { return '' }
        return [string]$out
    } catch {
        Write-Verbose "_Invoke-Pip failed: $_"
        return ''
    }
}

# -----------------------------------------------------------------------------
# Get-AscendoPipInstalledVersion
# -----------------------------------------------------------------------------
function Get-AscendoPipInstalledVersion {
    <#
    .SYNOPSIS
        Return installed version of a pip package (case-insensitive).

    .DESCRIPTION
        Reads from a module-level cache populated lazily by calling
        ``pip list --format=json`` once per script run.
    #>
    [CmdletBinding()]
    [OutputType([string])]
    param(
        [Parameter(Mandatory)] [string] $PackageName
    )
    if (-not $PackageName) { return $null }

    if ($null -eq $script:_PipInstalledCache) {
        $raw = _Invoke-Pip -Arguments @('list', '--format=json', '--disable-pip-version-check')
        $cache = @{}
        if ($raw) {
            try {
                $obj = $raw | ConvertFrom-Json -ErrorAction Stop
                if ($null -ne $obj) {
                    foreach ($entry in $obj) {
                        if ($null -ne $entry -and $entry.PSObject.Properties['name']) {
                            $name = [string]$entry.name
                            $ver = ''
                            if ($entry.PSObject.Properties['version']) {
                                $ver = [string]$entry.version
                            }
                            # Lower-cased key for case-insensitive lookup
                            # (PyPI normalises names per PEP 503).
                            $cache[$name.ToLowerInvariant()] = $ver
                        }
                    }
                }
            } catch {
                Write-Verbose "Get-AscendoPipInstalledVersion: parse failed: $_"
            }
        }
        $script:_PipInstalledCache = $cache
    }

    $key = $PackageName.ToLowerInvariant()
    if ($script:_PipInstalledCache.ContainsKey($key)) {
        $v = [string]$script:_PipInstalledCache[$key]
        if ($v) { return $v }
    }
    return $null
}

# -----------------------------------------------------------------------------
# Get-AscendoPipLatestVersion
# -----------------------------------------------------------------------------
function Get-AscendoPipLatestVersion {
    <#
    .SYNOPSIS
        Return the latest published version of a package via PyPI JSON API.

    .DESCRIPTION
        Hits ``https://pypi.org/pypi/<name>/json`` with a 5s timeout and
        extracts ``.info.version``. Cached per-package for the script run.
        Returns $null on network failure / unknown package.
    #>
    [CmdletBinding()]
    [OutputType([string])]
    param(
        [Parameter(Mandatory)] [string] $PackageName
    )
    if (-not $PackageName) { return $null }
    if ($script:_PipLatestCache.ContainsKey($PackageName)) {
        return [string]$script:_PipLatestCache[$PackageName]
    }

    $url = "https://pypi.org/pypi/$PackageName/json"
    try {
        $resp = Invoke-WebRequest -Uri $url -UseBasicParsing `
            -TimeoutSec 5 -ErrorAction Stop
        if ($null -eq $resp -or -not $resp.Content) {
            $script:_PipLatestCache[$PackageName] = $null
            return $null
        }
        $obj = $resp.Content | ConvertFrom-Json -ErrorAction Stop
        if ($null -ne $obj -and $obj.PSObject.Properties['info'] `
                -and $null -ne $obj.info -and $obj.info.PSObject.Properties['version']) {
            $v = [string]$obj.info.version
            $script:_PipLatestCache[$PackageName] = $v
            return $v
        }
    } catch {
        Write-Verbose "Get-AscendoPipLatestVersion($PackageName) failed: $_"
    }
    $script:_PipLatestCache[$PackageName] = $null
    return $null
}

# -----------------------------------------------------------------------------
# Read-AscendoPipManifest
# -----------------------------------------------------------------------------
function Read-AscendoPipManifest {
    <#
    .SYNOPSIS
        Parse a pip package manifest (one package per line).

    .DESCRIPTION
        File format: one package name per line, lines starting with # and
        blank lines are skipped. Trailing inline-comment text (after `#`)
        is stripped. Returns string[] of package names.
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
# Test-AscendoPipShouldSkip
# -----------------------------------------------------------------------------
function Test-AscendoPipShouldSkip {
    <#
    .SYNOPSIS
        Return $true for packages that should be skipped during apply.

    .DESCRIPTION
        Skip-list: pip, setuptools, wheel. These are the standard "infra"
        packages that come with every Python distribution. Trying to
        ``pip install -U`` them via pip itself on a system-managed
        Python often fails with "cannot uninstall, RECORD file not
        found" - the analog of the macOS brew-Python-self-skip rule.
        Mirrors adapters/macos/lib/ascendo_pip.sh::ascendo_pip_brew_owns
        intent.

        Case-insensitive match.
    #>
    [CmdletBinding()]
    [OutputType([bool])]
    param(
        [Parameter(Mandatory)] [string] $PackageName
    )
    if (-not $PackageName) { return $false }
    $lc = $PackageName.ToLowerInvariant()
    return ($lc -eq 'pip' -or $lc -eq 'setuptools' -or $lc -eq 'wheel')
}

# -----------------------------------------------------------------------------
# Exports
# -----------------------------------------------------------------------------
Export-ModuleMember -Function `
    Get-AscendoPipCommand, `
    Get-AscendoPipBin, `
    Get-AscendoPipInstalledVersion, `
    Get-AscendoPipLatestVersion, `
    Read-AscendoPipManifest, `
    Test-AscendoPipShouldSkip
