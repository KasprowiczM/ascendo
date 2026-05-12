# =============================================================================
# adapters/windows/lib/handlers/github_release.ps1
# =============================================================================
#
# Per-handler probe + apply functions for the github_release WebManager handler.
#
# Public surface:
#   Invoke-GitHubReleaseCheck    - returns candidate version string or $null
#                                  on rate-limit / 404 / no-matching-asset.
#                                  Caller should treat $null as "skipped:
#                                  probe_unavailable".
#   Invoke-GitHubReleaseApply    - v1 scope: Tier-B trigger-only. Opens the
#                                  release's HTML page in the default browser
#                                  so the operator can manually run the .exe.
#                                  Returns $true on success (page opened),
#                                  $false on failure.
#
# Both functions take a -Slug (string) + -Config (PSCustomObject from the
# Python registry shim's --get-app output).
#
# Compatibility: PowerShell 5.1 + 7.x. Set-StrictMode safe.
# =============================================================================

Set-StrictMode -Version Latest

function Invoke-GitHubReleaseCheck {
    [CmdletBinding()]
    [OutputType([string])]
    param(
        [Parameter(Mandatory)] [string] $Slug,
        [Parameter(Mandatory)] [object] $Config
    )

    # Pluck nested github_release config (PSCustomObject from ConvertFrom-Json).
    $sub = $Config.PSObject.Properties['github_release']
    if ($null -eq $sub -or $null -eq $sub.Value) {
        Write-Verbose "$Slug : github_release sub-table missing on config; treating as probe_unavailable"
        return $null
    }
    $cfg = $sub.Value

    $repoProp = $cfg.PSObject.Properties['repo']
    $patternProp = $cfg.PSObject.Properties['asset_pattern']
    if ($null -eq $repoProp -or $null -eq $patternProp) {
        Write-Verbose "$Slug : github_release config missing repo/asset_pattern"
        return $null
    }
    $repo = [string]$repoProp.Value
    $pattern = [string]$patternProp.Value
    $timeout = 8
    $timeoutProp = $cfg.PSObject.Properties['http_timeout_s']
    if ($null -ne $timeoutProp -and $timeoutProp.Value) {
        $timeout = [int]$timeoutProp.Value
    }
    $prerelease = $false
    $preProp = $cfg.PSObject.Properties['prerelease']
    if ($null -ne $preProp -and $preProp.Value) {
        $prerelease = [bool]$preProp.Value
    }

    $url = if ($prerelease) {
        "https://api.github.com/repos/$repo/releases"
    } else {
        "https://api.github.com/repos/$repo/releases/latest"
    }

    # Allow tests to substitute a canned response via env var.
    $override = $env:ASCENDO_WEB_GH_RELEASE_OVERRIDE
    $rawJson = $null
    if ($override -and (Test-Path -LiteralPath $override)) {
        try {
            $rawJson = Get-Content -LiteralPath $override -Raw -Encoding UTF8
        } catch {
            Write-Verbose "$Slug : failed to read GH release override: $_"
            return $null
        }
    } else {
        try {
            # Invoke-RestMethod with a UA (GitHub requires one). TLS 1.2+
            # forced via [Net.ServicePointManager]::SecurityProtocol below.
            $rawJson = Invoke-RestMethod -Uri $url `
                -Headers @{ 'User-Agent' = 'Ascendo/0.0.7 (+https://github.com/KasprowiczM/ascendo)' } `
                -TimeoutSec $timeout `
                -ErrorAction Stop |
                ConvertTo-Json -Depth 100
        } catch {
            $msg = $_.Exception.Message
            Write-Verbose "$Slug : github_release fetch failed: $msg"
            return $null
        }
    }
    if (-not $rawJson) { return $null }

    $parsed = $null
    try {
        $parsed = $rawJson | ConvertFrom-Json
    } catch {
        Write-Verbose "$Slug : github_release response is not valid JSON"
        return $null
    }
    if ($null -eq $parsed) { return $null }

    # /releases (prerelease=true) returns an array; /releases/latest returns a single object.
    $candidates = if ($parsed -is [System.Collections.IEnumerable] -and -not ($parsed -is [string])) {
        @($parsed)
    } else {
        @($parsed)
    }

    foreach ($rel in $candidates) {
        if ($null -eq $rel) { continue }
        $tagProp = $rel.PSObject.Properties['tag_name']
        $assetsProp = $rel.PSObject.Properties['assets']
        if ($null -eq $tagProp) { continue }
        $tag = [string]$tagProp.Value

        # Filter assets by the regex.
        $assets = @()
        if ($null -ne $assetsProp -and $null -ne $assetsProp.Value) {
            $assets = @($assetsProp.Value)
        }
        $matched = $false
        foreach ($a in $assets) {
            $nameProp = $a.PSObject.Properties['name']
            if ($null -eq $nameProp) { continue }
            $name = [string]$nameProp.Value
            if ($name -match $pattern) { $matched = $true; break }
        }
        if (-not $matched) { continue }

        # Strip leading 'v' (Obsidian tags 'v1.12.7' but DisplayVersion '1.12.7').
        if ($tag.StartsWith('v') -or $tag.StartsWith('V')) {
            $tag = $tag.Substring(1)
        }
        return $tag
    }

    Write-Verbose "$Slug : no release contained an asset matching pattern $pattern"
    return $null
}

function Invoke-GitHubReleaseApply {
    [CmdletBinding()]
    [OutputType([bool])]
    param(
        [Parameter(Mandatory)] [string] $Slug,
        [Parameter(Mandatory)] [object] $Config
    )

    # v1 scope: Tier-B trigger-only. We don't download + run the .exe yet
    # (needs Authenticode + UAC + per-installer flag handling we'll add in
    # a follow-up). Open the release page so the operator can do the
    # manual download/install themselves.
    $sub = $Config.PSObject.Properties['github_release']
    if ($null -eq $sub -or $null -eq $sub.Value) {
        return $false
    }
    $repo = $sub.Value.repo
    if (-not $repo) { return $false }
    $url = "https://github.com/$repo/releases/latest"
    try {
        Start-Process -FilePath $url -ErrorAction Stop | Out-Null
        return $true
    } catch {
        Write-Verbose "$Slug : Start-Process $url failed: $_"
        return $false
    }
}

Export-ModuleMember -Function @(
    'Invoke-GitHubReleaseCheck',
    'Invoke-GitHubReleaseApply'
)
