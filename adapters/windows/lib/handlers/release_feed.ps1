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

    # Tier-B trigger-only path: open the feed URL so the operator can
    # navigate to the actual download. Default for apps without
    # tier_a_apply=true.
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

# -----------------------------------------------------------------------------
# Tier-A apply (uses Invoke-WebInstallerDownload / Invoke-WebInstallerVerify /
# Invoke-WebInstallerRun / Get-WebReinstalledVersion from github_release.ps1
# which the apply.ps1 dispatcher always imports first).
# -----------------------------------------------------------------------------

function _RF_ResolveDownloadUrl {
    <#
    .SYNOPSIS
        Resolve the absolute installer URL from a release_feed config.

        Priority order:
          1. download_url        - static absolute URL pinned in TOML
          2. download_path       - dotted JSON walk on the same body as
                                   the version probe; resolved against
                                   the feed URL if it comes back as a
                                   relative path
          3. (no download_url + no download_path + format=text)
                                  - fall back to the feed URL itself
                                   (e.g. when the body IS the installer
                                   URL on one line). Reject if it doesn't
                                   look like an installer URL.

    .OUTPUTS
        [string] absolute URL or $null on failure.
    #>
    param(
        [Parameter(Mandatory)] [object] $Cfg,
        [Parameter(Mandatory)] [string] $Version,
        [Parameter()] [int] $TimeoutSec = 8
    )

    $duProp = $Cfg.PSObject.Properties['download_url']
    if ($null -ne $duProp -and $duProp.Value) {
        return [string]$duProp.Value
    }

    $dpProp = $Cfg.PSObject.Properties['download_path']
    if ($null -eq $dpProp -or -not $dpProp.Value) {
        return $null
    }
    $downloadPath = [string]$dpProp.Value

    $feedUrl = [string]$Cfg.PSObject.Properties['url'].Value
    if (-not $feedUrl) { return $null }

    # Fetch the feed JSON, walk the download_path.
    $override = $env:ASCENDO_WEB_RELEASE_FEED_OVERRIDE
    $body = $null
    if ($override -and (Test-Path -LiteralPath $override)) {
        try {
            $body = Get-Content -LiteralPath $override -Raw -Encoding UTF8
        } catch { return $null }
    } else {
        try {
            $body = Invoke-WebRequest -Uri $feedUrl `
                -Headers @{ 'User-Agent' = 'Ascendo/0.0.7' } `
                -TimeoutSec $TimeoutSec `
                -UseBasicParsing `
                -ErrorAction Stop |
                Select-Object -ExpandProperty Content
        } catch { return $null }
    }
    if (-not $body) { return $null }

    $parsed = $null
    try { $parsed = $body | ConvertFrom-Json } catch { return $null }
    if ($null -eq $parsed) { return $null }

    $raw = _RF-WalkJsonPath -Parsed $parsed -Path $downloadPath
    if ($null -eq $raw) { return $null }
    $rawUrl = [string]$raw
    if (-not $rawUrl) { return $null }

    # Absolute URL already? Use as-is.
    if ($rawUrl -match '^https?://') {
        return $rawUrl
    }
    # Resolve relative path against feed URL's parent directory.
    try {
        $baseUri = [System.Uri]::new($feedUrl)
        $resolved = [System.Uri]::new($baseUri, $rawUrl)
        return $resolved.AbsoluteUri
    } catch {
        return $null
    }
}

function Invoke-ReleaseFeedApplyReal {
    <#
    .SYNOPSIS
        Tier-A apply: probe candidate version, resolve installer URL,
        download, verify Authenticode, execute, read DisplayVersion back.
    .OUTPUTS
        Hashtable: Success (bool), InstalledVersion (string|$null),
                   ExitCode (int), ErrorMessage (string|$null),
                   RebootRequired (bool), DownloadPath (string|$null),
                   UacCancelled (bool)
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [string] $Slug,
        [Parameter(Mandatory)] [object] $Config
    )

    $cfg = _RF-GetSub -Config $Config
    if ($null -eq $cfg) {
        return @{
            Success          = $false
            InstalledVersion = $null
            ExitCode         = -1
            ErrorMessage     = 'release_feed sub-table missing on config'
            RebootRequired   = $false
            DownloadPath     = $null
            UacCancelled     = $false
        }
    }

    # We need a candidate version both as the cache prefix AND as the value
    # we report back. Probe via the public Check function.
    $candidate = Invoke-ReleaseFeedCheck -Slug $Slug -Config $Config
    if ([string]::IsNullOrWhiteSpace($candidate)) {
        return @{
            Success          = $false
            InstalledVersion = $null
            ExitCode         = -1
            ErrorMessage     = "candidate probe unavailable (rate-limited / 404 / regex no-match)"
            RebootRequired   = $false
            DownloadPath     = $null
            UacCancelled     = $false
        }
    }

    $timeout = 8
    $tProp = $cfg.PSObject.Properties['http_timeout_s']
    if ($null -ne $tProp -and $tProp.Value) { $timeout = [int]$tProp.Value }

    $downloadUrl = _RF_ResolveDownloadUrl -Cfg $cfg -Version $candidate -TimeoutSec $timeout
    if (-not $downloadUrl) {
        return @{
            Success          = $false
            InstalledVersion = $null
            ExitCode         = -1
            ErrorMessage     = ("could not resolve download URL " +
                                "(neither download_url nor download_path set, " +
                                "or feed walk failed)")
            RebootRequired   = $false
            DownloadPath     = $null
            UacCancelled     = $false
        }
    }

    # Optional process kill before install.
    $killList = @()
    $killProp = $cfg.PSObject.Properties['kill_processes']
    if ($null -ne $killProp -and $null -ne $killProp.Value) {
        $killList = @($killProp.Value)
    }
    foreach ($procName in $killList) {
        try {
            $running = @(Get-Process -Name $procName -ErrorAction SilentlyContinue)
            foreach ($proc in $running) {
                try {
                    Stop-Process -Id $proc.Id -Force -ErrorAction Stop
                } catch {
                    Write-Verbose "$Slug : Stop-Process $procName failed: $_"
                }
            }
        } catch {
            Write-Verbose "$Slug : Get-Process $procName threw: $_"
        }
    }

    # 1) Download
    $assetName = ($downloadUrl -split '/')[-1]
    $downloadPath = Invoke-WebInstallerDownload -Slug $Slug -Url $downloadUrl `
        -Version $candidate -AssetName $assetName
    if (-not $downloadPath) {
        return @{
            Success          = $false
            InstalledVersion = $null
            ExitCode         = -1
            ErrorMessage     = "installer download failed for $downloadUrl"
            RebootRequired   = $false
            DownloadPath     = $null
            UacCancelled     = $false
        }
    }

    # 2) Verify Authenticode
    $expectedPub = ''
    $pubProp = $cfg.PSObject.Properties['expected_publisher']
    if ($null -ne $pubProp -and $pubProp.Value) { $expectedPub = [string]$pubProp.Value }
    $verifyResult = Invoke-WebInstallerVerify -InstallerPath $downloadPath `
        -ExpectedPublisher $expectedPub
    if (-not $verifyResult.Verified) {
        return @{
            Success          = $false
            InstalledVersion = $null
            ExitCode         = -1
            ErrorMessage     = ("Authenticode verification failed: " + $verifyResult.ErrorMessage)
            RebootRequired   = $false
            DownloadPath     = $downloadPath
            UacCancelled     = $false
        }
    }

    # 3) Run installer
    $kind = $null
    $kindProp = $cfg.PSObject.Properties['installer_kind']
    if ($null -ne $kindProp -and $kindProp.Value) { $kind = [string]$kindProp.Value }
    $silent = $null
    $silentProp = $cfg.PSObject.Properties['silent_args']
    if ($null -ne $silentProp -and $null -ne $silentProp.Value) {
        $silent = @($silentProp.Value)
    }
    $runResult = Invoke-WebInstallerRun -InstallerPath $downloadPath `
        -InstallerKind $kind -SilentArgs $silent
    $uacCancelled = $false
    if ($runResult.PSObject.Properties['UacCancelled']) {
        $uacCancelled = [bool]$runResult.UacCancelled
    }
    if (-not $runResult.Started) {
        return @{
            Success          = $false
            InstalledVersion = $null
            ExitCode         = $runResult.ExitCode
            ErrorMessage     = $runResult.ErrorMessage
            RebootRequired   = $false
            DownloadPath     = $downloadPath
            UacCancelled     = $uacCancelled
        }
    }
    if ($runResult.ExitCode -ne 0 -and -not $runResult.RebootRequired) {
        return @{
            Success          = $false
            InstalledVersion = $null
            ExitCode         = $runResult.ExitCode
            ErrorMessage     = $runResult.ErrorMessage
            RebootRequired   = $false
            DownloadPath     = $downloadPath
            UacCancelled     = $uacCancelled
        }
    }

    # 4) Readback installed version
    $uninstallKey = ''
    $ukProp = $Config.PSObject.Properties['windows_uninstall_key']
    if ($null -ne $ukProp -and $ukProp.Value) { $uninstallKey = [string]$ukProp.Value }
    $dnp = $null
    $dnpProp = $cfg.PSObject.Properties['display_name_pattern']
    if ($null -ne $dnpProp -and $dnpProp.Value) { $dnp = [string]$dnpProp.Value }
    $newVersion = Get-WebReinstalledVersion -UninstallKey $uninstallKey `
        -DisplayNamePattern $dnp -Slug $Slug

    return @{
        Success          = $true
        InstalledVersion = $newVersion
        ExitCode         = $runResult.ExitCode
        ErrorMessage     = $null
        RebootRequired   = $runResult.RebootRequired
        DownloadPath     = $downloadPath
        UacCancelled     = $false
    }
}

# Functions are auto-exported when this file is dot-sourced or
# loaded via Import-Module (PS5.1 errors on explicit Export-ModuleMember
# from inside a transient .ps1 module -- Sesja 58 fix).
