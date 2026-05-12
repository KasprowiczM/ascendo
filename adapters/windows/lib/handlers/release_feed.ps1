# =============================================================================
# adapters/windows/lib/handlers/release_feed.ps1
# =============================================================================
#
# Per-handler probe + apply functions for the release_feed WebManager handler.
#
# Public surface:
#   Invoke-ReleaseFeedCheck    - returns candidate version string or $null
#                                on transport failure / regex no-match (text
#                                mode) / path-walk failure (json mode).
#                                Caller should treat $null as "skipped:
#                                probe_unavailable".
#   Invoke-ReleaseFeedApply    - v1 scope: Tier-B trigger-only. Opens the
#                                feed URL in the default browser so the
#                                operator can manually run the installer.
#
# JSON mode walks a dotted version_path (supports [N] list indices).
# Text mode runs the version_regex against the raw body.
# version_regex + version_replace XOR pair: if both set, run a single
# regex replace on the extracted/raw value before reporting.
#
# Compatibility: PowerShell 5.1 + 7.x. Set-StrictMode safe.
# =============================================================================

Set-StrictMode -Version Latest

function _RF-GetSub {
    param([object] $Config)
    $sub = $Config.PSObject.Properties['release_feed']
    if ($null -eq $sub -or $null -eq $sub.Value) { return $null }
    return $sub.Value
}

function _RF-WalkJsonPath {
    <#
    .SYNOPSIS
        Walk a dotted JSON path (with [N] index syntax) through a parsed
        object. Returns $null on any missing segment.
    #>
    param(
        [Parameter(Mandatory)] $Parsed,
        [Parameter(Mandatory)] [string] $Path
    )
    if ([string]::IsNullOrWhiteSpace($Path)) { return $null }
    $current = $Parsed
    # Split on '.' first, then expand [N] suffixes.
    foreach ($segment in $Path.Split('.')) {
        if ($null -eq $current) { return $null }
        # A segment may be "foo", "foo[0]", or "[0]".
        $name = $segment
        $indices = @()
        $m = [System.Text.RegularExpressions.Regex]::Matches(
            $segment, '\[(\d+)\]')
        if ($m.Count -gt 0) {
            $bracketStart = $segment.IndexOf('[')
            $name = $segment.Substring(0, $bracketStart)
            foreach ($mm in $m) { $indices += [int]$mm.Groups[1].Value }
        }
        if ($name) {
            if ($current -is [System.Collections.IDictionary]) {
                if (-not $current.Contains($name)) { return $null }
                $current = $current[$name]
            } else {
                $prop = $current.PSObject.Properties[$name]
                if ($null -eq $prop) { return $null }
                $current = $prop.Value
            }
        }
        foreach ($idx in $indices) {
            if ($null -eq $current) { return $null }
            if ($current -is [System.Collections.IEnumerable] -and -not ($current -is [string])) {
                $arr = @($current)
                if ($idx -ge $arr.Count) { return $null }
                $current = $arr[$idx]
            } else {
                return $null
            }
        }
    }
    return $current
}

function _RF-ApplyRegexTransform {
    <#
    .SYNOPSIS
        Apply (version_regex, version_replace) to the raw value. Falls back
        to raw on no match (graceful degradation per macOS M5.7.4 spec).
    #>
    param(
        [Parameter(Mandatory)] [AllowEmptyString()] [string] $Raw,
        [Parameter()] [string] $Pattern,
        [Parameter()] [string] $Replacement
    )
    if (-not $Pattern) { return $Raw }
    if ([string]::IsNullOrEmpty($Raw)) { return $Raw }
    try {
        $rx = [System.Text.RegularExpressions.Regex]::new($Pattern)
    } catch {
        Write-Verbose "release_feed regex invalid: $_"
        return $Raw
    }
    if (-not $rx.IsMatch($Raw)) {
        Write-Verbose "release_feed regex didn't match raw value '$Raw'; falling back"
        return $Raw
    }
    return $rx.Replace($Raw, $Replacement, 1)
}

function Invoke-ReleaseFeedCheck {
    [CmdletBinding()]
    [OutputType([string])]
    param(
        [Parameter(Mandatory)] [string] $Slug,
        [Parameter(Mandatory)] [object] $Config
    )

    $cfg = _RF-GetSub -Config $Config
    if ($null -eq $cfg) {
        Write-Verbose "$Slug : release_feed sub-table missing on config; treating as probe_unavailable"
        return $null
    }

    $urlProp = $cfg.PSObject.Properties['url']
    if ($null -eq $urlProp) { return $null }
    $url = [string]$urlProp.Value
    if (-not $url) { return $null }

    $fmt = 'json'
    $fmtProp = $cfg.PSObject.Properties['format']
    if ($null -ne $fmtProp -and $fmtProp.Value) { $fmt = [string]$fmtProp.Value }

    $timeout = 8
    $timeoutProp = $cfg.PSObject.Properties['http_timeout_s']
    if ($null -ne $timeoutProp -and $timeoutProp.Value) {
        $timeout = [int]$timeoutProp.Value
    }

    $pattern = ''
    $replace = ''
    $rxProp = $cfg.PSObject.Properties['version_regex']
    if ($null -ne $rxProp -and $rxProp.Value) {
        $pattern = [string]$rxProp.Value
    }
    $repProp = $cfg.PSObject.Properties['version_replace']
    if ($null -ne $repProp -and $repProp.Value) {
        $replace = [string]$repProp.Value
    }

    # Test-mode override: ASCENDO_WEB_RELEASE_FEED_OVERRIDE points at a file
    # holding the body verbatim.
    $override = $env:ASCENDO_WEB_RELEASE_FEED_OVERRIDE
    $body = $null
    if ($override -and (Test-Path -LiteralPath $override)) {
        try {
            $body = Get-Content -LiteralPath $override -Raw -Encoding UTF8
        } catch {
            Write-Verbose "$Slug : failed to read release_feed override: $_"
            return $null
        }
    } else {
        try {
            $body = Invoke-WebRequest -Uri $url `
                -Headers @{ 'User-Agent' = 'Ascendo/0.0.7' } `
                -TimeoutSec $timeout `
                -UseBasicParsing `
                -ErrorAction Stop |
                Select-Object -ExpandProperty Content
        } catch {
            Write-Verbose "$Slug : release_feed fetch failed: $_"
            return $null
        }
    }

    if ($null -eq $body) { return $null }

    if ($fmt -eq 'text') {
        return _RF-ApplyRegexTransform -Raw $body -Pattern $pattern -Replacement $replace
    }

    # JSON path
    $parsed = $null
    try {
        $parsed = $body | ConvertFrom-Json
    } catch {
        Write-Verbose "$Slug : release_feed body is not valid JSON"
        return $null
    }

    $pathProp = $cfg.PSObject.Properties['version_path']
    if ($null -eq $pathProp -or -not $pathProp.Value) {
        Write-Verbose "$Slug : release_feed json mode requires version_path"
        return $null
    }
    $raw = _RF-WalkJsonPath -Parsed $parsed -Path ([string]$pathProp.Value)
    if ($null -eq $raw) { return $null }
    $rawStr = [string]$raw
    if (-not $rawStr) { return $null }

    return _RF-ApplyRegexTransform -Raw $rawStr -Pattern $pattern -Replacement $replace
}

function Invoke-ReleaseFeedApply {
    [CmdletBinding()]
    [OutputType([bool])]
    param(
        [Parameter(Mandatory)] [string] $Slug,
        [Parameter(Mandatory)] [object] $Config
    )

    # v1 scope: Tier-B trigger-only. We don't download + run installers from
    # release_feed yet (needs Authenticode + UAC + per-installer flag handling
    # we'll add in a follow-up). Open the feed URL so the operator can
    # navigate to the actual download.
    $cfg = _RF-GetSub -Config $Config
    if ($null -eq $cfg) { return $false }
    $url = [string]$cfg.url
    if (-not $url) { return $false }
    try {
        Start-Process -FilePath $url -ErrorAction Stop | Out-Null
        return $true
    } catch {
        Write-Verbose "$Slug : Start-Process $url failed: $_"
        return $false
    }
}

Export-ModuleMember -Function @(
    'Invoke-ReleaseFeedCheck',
    'Invoke-ReleaseFeedApply'
)
