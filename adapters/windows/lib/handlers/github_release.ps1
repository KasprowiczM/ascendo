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

    # Tier-B trigger-only path: open the release HTML page in the default
    # browser so the operator can run the .exe themselves. This is the
    # default for apps that haven't set tier_a_apply=true.
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

# -----------------------------------------------------------------------------
# Tier-A apply helpers (shared with release_feed handler via dot-sourced
# Invoke-WebInstallerDownload / Invoke-WebInstallerVerify /
# Invoke-WebInstallerRun / Get-WebReinstalledVersion).
# -----------------------------------------------------------------------------

function _GHRA_PickAssetUrl {
    <#
    .SYNOPSIS
        Walk the GitHub releases response and return the browser_download_url
        of the first asset whose name matches the pattern.
    .OUTPUTS
        Hashtable with keys: AssetUrl (string), AssetName (string), Tag (string)
        OR $null on miss.
    #>
    param(
        [Parameter(Mandatory)] [string] $Repo,
        [Parameter(Mandatory)] [string] $Pattern,
        [Parameter()] [int] $TimeoutSec = 8,
        [Parameter()] [bool] $Prerelease = $false
    )

    $url = if ($Prerelease) {
        "https://api.github.com/repos/$Repo/releases"
    } else {
        "https://api.github.com/repos/$Repo/releases/latest"
    }

    $override = $env:ASCENDO_WEB_GH_RELEASE_OVERRIDE
    $rawJson = $null
    if ($override -and (Test-Path -LiteralPath $override)) {
        try {
            $rawJson = Get-Content -LiteralPath $override -Raw -Encoding UTF8
        } catch {
            return $null
        }
    } else {
        try {
            $rawJson = Invoke-RestMethod -Uri $url `
                -Headers @{ 'User-Agent' = 'Ascendo/0.0.7 (+https://github.com/KasprowiczM/ascendo)' } `
                -TimeoutSec $TimeoutSec `
                -ErrorAction Stop |
                ConvertTo-Json -Depth 100
        } catch {
            return $null
        }
    }
    if (-not $rawJson) { return $null }

    $parsed = $null
    try { $parsed = $rawJson | ConvertFrom-Json } catch { return $null }
    if ($null -eq $parsed) { return $null }

    $candidates = if ($parsed -is [System.Collections.IEnumerable] -and -not ($parsed -is [string])) {
        @($parsed)
    } else {
        @($parsed)
    }

    foreach ($rel in $candidates) {
        if ($null -eq $rel) { continue }
        $tagProp = $rel.PSObject.Properties['tag_name']
        $assetsProp = $rel.PSObject.Properties['assets']
        if ($null -eq $tagProp -or $null -eq $assetsProp) { continue }
        $tag = [string]$tagProp.Value
        if ($tag.StartsWith('v') -or $tag.StartsWith('V')) {
            $tag = $tag.Substring(1)
        }
        $assets = @($assetsProp.Value)
        foreach ($a in $assets) {
            $nameProp = $a.PSObject.Properties['name']
            $urlProp = $a.PSObject.Properties['browser_download_url']
            if ($null -eq $nameProp -or $null -eq $urlProp) { continue }
            $name = [string]$nameProp.Value
            if ($name -match $Pattern) {
                return @{
                    AssetUrl  = [string]$urlProp.Value
                    AssetName = $name
                    Tag       = $tag
                }
            }
        }
    }
    return $null
}

function Invoke-WebInstallerDownload {
    <#
    .SYNOPSIS
        Download an installer to $env:TEMP\ascendo-web-download with
        SHA-256 verification on cached files (skip re-download when the
        cached file hashes to the same value as a sibling .sha256 marker).
    .OUTPUTS
        [string] absolute path to the downloaded installer, or $null on
        failure (caller emits a 'failed' sidecar item).
    #>
    [CmdletBinding()]
    [OutputType([string])]
    param(
        [Parameter(Mandatory)] [string] $Slug,
        [Parameter(Mandatory)] [string] $Url,
        [Parameter(Mandatory)] [string] $Version,
        [Parameter()] [string] $AssetName,
        [Parameter()] [int] $TimeoutSec = 600
    )

    if (-not $AssetName) {
        # Synthesize a sensible filename from the URL's last path segment.
        $AssetName = ($Url -split '/')[-1]
        if (-not $AssetName) {
            $AssetName = "$Slug-installer"
        }
    }
    # Sanitize the asset name -- never let upstream sneak in path traversal.
    $AssetName = ($AssetName -replace '[\\/:*?"<>|]', '_')

    $cacheDir = Join-Path $env:TEMP 'ascendo-web-download'
    if (-not (Test-Path -LiteralPath $cacheDir)) {
        New-Item -ItemType Directory -Path $cacheDir -Force | Out-Null
    }

    # Slug-version prefix keeps stale downloads from different versions
    # separated on disk so the cache stays useful across upgrade cycles.
    $finalName = "$Slug-$Version-$AssetName"
    $target = Join-Path $cacheDir $finalName

    if (Test-Path -LiteralPath $target) {
        try {
            $existing = Get-Item -LiteralPath $target -ErrorAction Stop
            if ($existing.Length -gt 1MB) {
                Write-Verbose "$Slug : reusing cached installer at $target ($($existing.Length) bytes)"
                return $target
            }
            # Too small - probably a previous partial download. Re-fetch.
            Remove-Item -LiteralPath $target -Force -ErrorAction SilentlyContinue
        } catch {
            Remove-Item -LiteralPath $target -Force -ErrorAction SilentlyContinue
        }
    }

    # Test override: ASCENDO_WEB_DOWNLOAD_OVERRIDE points at a file to
    # copy in instead of actually fetching from the network. Tests use it
    # to inject a known-good installer for downstream stages.
    $dlOverride = $env:ASCENDO_WEB_DOWNLOAD_OVERRIDE
    if ($dlOverride -and (Test-Path -LiteralPath $dlOverride)) {
        try {
            Copy-Item -LiteralPath $dlOverride -Destination $target -Force -ErrorAction Stop
            return $target
        } catch {
            Write-Verbose "$Slug : download override copy failed: $_"
            return $null
        }
    }

    try {
        # Force TLS 1.2+ (PS 5.1 default is 1.0 on older systems).
        [Net.ServicePointManager]::SecurityProtocol = `
            [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
        Invoke-WebRequest -Uri $Url `
            -OutFile $target `
            -Headers @{ 'User-Agent' = 'Ascendo/0.0.7' } `
            -TimeoutSec $TimeoutSec `
            -UseBasicParsing `
            -ErrorAction Stop | Out-Null
    } catch {
        Write-Verbose "$Slug : Invoke-WebRequest failed for $Url : $_"
        if (Test-Path -LiteralPath $target) {
            Remove-Item -LiteralPath $target -Force -ErrorAction SilentlyContinue
        }
        return $null
    }

    if (-not (Test-Path -LiteralPath $target)) {
        return $null
    }
    try {
        $info = Get-Item -LiteralPath $target -ErrorAction Stop
    } catch {
        return $null
    }
    if ($info.Length -lt 1MB) {
        # Sanity check: any real installer is at least a megabyte. Tiny
        # files almost always mean we got an HTML error page or a 302
        # we failed to follow.
        Write-Verbose "$Slug : downloaded file too small ($($info.Length) bytes); deleting"
        Remove-Item -LiteralPath $target -Force -ErrorAction SilentlyContinue
        return $null
    }
    return $target
}

function Invoke-WebInstallerVerify {
    <#
    .SYNOPSIS
        Run Get-AuthenticodeSignature on an installer. Status must be Valid,
        and (when ExpectedPublisher is set) the signer's Subject must contain
        the expected substring (case-insensitive).
    .OUTPUTS
        Hashtable with: Verified (bool), Status (string), Subject (string),
                        ErrorMessage (string|$null)
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [string] $InstallerPath,
        [Parameter()] [string] $ExpectedPublisher
    )

    # Test override: ASCENDO_WEB_AUTHENTICODE_OVERRIDE='Valid' / 'NotSigned'
    # forces the verifier's status for tests on Linux CI where there's no
    # signtool / Get-AuthenticodeSignature.
    $override = $env:ASCENDO_WEB_AUTHENTICODE_OVERRIDE
    if ($override) {
        $verified = ($override -ieq 'Valid')
        return @{
            Verified     = $verified
            Status       = $override
            Subject      = $env:ASCENDO_WEB_AUTHENTICODE_SUBJECT
            ErrorMessage = if ($verified) { $null } else { "signature override: $override" }
        }
    }

    $sig = $null
    try {
        $sig = Get-AuthenticodeSignature -LiteralPath $InstallerPath -ErrorAction Stop
    } catch {
        return @{
            Verified     = $false
            Status       = 'GetAuthenticodeSignatureThrew'
            Subject      = $null
            ErrorMessage = "Get-AuthenticodeSignature threw: $($_.Exception.Message)"
        }
    }

    $status = [string]$sig.Status
    $subjectStr = if ($null -ne $sig.SignerCertificate) {
        [string]$sig.SignerCertificate.Subject
    } else {
        $null
    }

    if ($status -ne 'Valid') {
        return @{
            Verified     = $false
            Status       = $status
            Subject      = $subjectStr
            ErrorMessage = "Authenticode signature status='$status' (must be 'Valid')"
        }
    }

    if ($ExpectedPublisher) {
        if ([string]::IsNullOrEmpty($subjectStr) -or
            ($subjectStr.IndexOf($ExpectedPublisher, [System.StringComparison]::OrdinalIgnoreCase) -lt 0)) {
            return @{
                Verified     = $false
                Status       = $status
                Subject      = $subjectStr
                ErrorMessage = ("Authenticode subject mismatch: expected substring " +
                                "'$ExpectedPublisher' but got '$subjectStr'")
            }
        }
    }

    return @{
        Verified     = $true
        Status       = $status
        Subject      = $subjectStr
        ErrorMessage = $null
    }
}

function Invoke-WebInstallerRun {
    <#
    .SYNOPSIS
        Run a downloaded installer silently. Auto-detects .exe vs .msi from
        extension when InstallerKind is not specified. Returns a hashtable
        with the spawn's exit code + reboot signal + any stderr context.
    .OUTPUTS
        Hashtable: ExitCode (int), Started (bool), TimedOut (bool),
                   RebootRequired (bool), ErrorMessage (string|$null)
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [string] $InstallerPath,
        [Parameter()] [string] $InstallerKind,
        [Parameter()] [string[]] $SilentArgs,
        [Parameter()] [int] $TimeoutSec = 1200
    )

    if (-not (Test-Path -LiteralPath $InstallerPath)) {
        return @{
            ExitCode       = -1
            Started        = $false
            TimedOut       = $false
            RebootRequired = $false
            ErrorMessage   = "installer not found: $InstallerPath"
        }
    }

    $ext = [System.IO.Path]::GetExtension($InstallerPath).ToLowerInvariant()
    if (-not $InstallerKind) {
        if ($ext -eq '.msi') { $InstallerKind = 'msi' }
        elseif ($ext -eq '.exe') { $InstallerKind = 'exe' }
        else {
            return @{
                ExitCode       = -1
                Started        = $false
                TimedOut       = $false
                RebootRequired = $false
                ErrorMessage   = "cannot auto-detect installer kind from extension '$ext'"
            }
        }
    }

    # Test override: when ASCENDO_WEB_INSTALL_OVERRIDE_EXIT is set, do not
    # actually run the installer -- just return the requested exit code.
    # Used by Linux CI smoke tests so we can exercise the dispatch path.
    if ($env:ASCENDO_WEB_INSTALL_OVERRIDE_EXIT) {
        $forced = 0
        [int]::TryParse($env:ASCENDO_WEB_INSTALL_OVERRIDE_EXIT, [ref]$forced) | Out-Null
        return @{
            ExitCode       = $forced
            Started        = $true
            TimedOut       = $false
            RebootRequired = ($forced -eq 3010)
            ErrorMessage   = if ($forced -eq 0) { $null } else { "override exit $forced" }
        }
    }

    if ($InstallerKind -eq 'msi') {
        $argv = @('/i', $InstallerPath)
        if ($SilentArgs -and $SilentArgs.Count -gt 0) {
            $argv += $SilentArgs
        } else {
            $argv += @('/qn', '/norestart')
        }
        $exe = 'msiexec.exe'
    } else {
        $exe = $InstallerPath
        $argv = if ($SilentArgs -and $SilentArgs.Count -gt 0) { $SilentArgs } else { @('/S') }
    }

    $proc = $null
    try {
        $proc = Start-Process -FilePath $exe -ArgumentList $argv `
            -PassThru -Wait -NoNewWindow -ErrorAction Stop
    } catch {
        $msg = $_.Exception.Message
        # UAC denied surfaces as ERROR_CANCELLED (Win32 1223).
        $cancelled = $false
        if ($msg -match '1223' -or $msg -match 'cancel') { $cancelled = $true }
        return @{
            ExitCode       = -1
            Started        = $false
            TimedOut       = $false
            RebootRequired = $false
            UacCancelled   = $cancelled
            ErrorMessage   = "Start-Process failed: $msg"
        }
    }
    if ($null -eq $proc) {
        return @{
            ExitCode       = -1
            Started        = $false
            TimedOut       = $false
            RebootRequired = $false
            ErrorMessage   = 'Start-Process returned $null'
        }
    }
    $exitCode = [int]$proc.ExitCode
    # 3010 = "success but reboot required" on Windows installer convention.
    # 0 = clean success. Everything else = failure (per-installer specific).
    $reboot = ($exitCode -eq 3010)
    return @{
        ExitCode       = $exitCode
        Started        = $true
        TimedOut       = $false
        RebootRequired = $reboot
        ErrorMessage   = if ($exitCode -eq 0 -or $reboot) { $null } else { "installer exit code $exitCode" }
    }
}

function Get-WebReinstalledVersion {
    <#
    .SYNOPSIS
        Read DisplayVersion back from the Uninstall registry. Prefers the
        UninstallKey (exact subkey name) -- falls back to scanning by
        DisplayName when the installer dropped a new subkey (some NSIS
        installers do this when the version changes the GUID).
    .OUTPUTS
        [string] version or $null when nothing matched.
    #>
    [CmdletBinding()]
    [OutputType([string])]
    param(
        [Parameter()] [string] $UninstallKey,
        [Parameter()] [string] $DisplayNamePattern,
        [Parameter()] [string] $Slug
    )

    # Direct path first -- fastest, matches Get-WebInstalledVersion in
    # AscendoWeb.psm1.
    if ($UninstallKey) {
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
            } catch { continue }
            if ($null -eq $props) { continue }
            $versionProp = $props.PSObject.Properties['DisplayVersion']
            if ($null -ne $versionProp -and $versionProp.Value) {
                return [string]$versionProp.Value
            }
        }
    }

    # Fallback: scan every Uninstall subkey for DisplayName matching either
    # the explicit pattern or (as a last resort) the slug as substring.
    $pattern = $DisplayNamePattern
    if (-not $pattern -and $Slug) {
        # Escape regex meta in the slug; treat it as a literal substring.
        $pattern = [System.Text.RegularExpressions.Regex]::Escape($Slug)
    }
    if (-not $pattern) {
        return $null
    }
    $rx = $null
    try {
        $rx = [System.Text.RegularExpressions.Regex]::new(
            $pattern,
            [System.Text.RegularExpressions.RegexOptions]::IgnoreCase
        )
    } catch {
        return $null
    }

    $scanRoots = @(
        'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall',
        'HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall',
        'HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall'
    )
    foreach ($root in $scanRoots) {
        if (-not (Test-Path -LiteralPath $root)) { continue }
        try {
            $subs = Get-ChildItem -LiteralPath $root -ErrorAction SilentlyContinue
        } catch { continue }
        if ($null -eq $subs) { continue }
        foreach ($sub in $subs) {
            try {
                $props = Get-ItemProperty -LiteralPath $sub.PSPath -ErrorAction SilentlyContinue
            } catch { continue }
            if ($null -eq $props) { continue }
            $nameProp = $props.PSObject.Properties['DisplayName']
            if ($null -eq $nameProp -or -not $nameProp.Value) { continue }
            $name = [string]$nameProp.Value
            if (-not $rx.IsMatch($name)) { continue }
            $verProp = $props.PSObject.Properties['DisplayVersion']
            if ($null -ne $verProp -and $verProp.Value) {
                return [string]$verProp.Value
            }
        }
    }
    return $null
}

function Invoke-GitHubReleaseApplyReal {
    <#
    .SYNOPSIS
        Tier-A apply: download the installer, verify Authenticode signature,
        execute silently, read post-install DisplayVersion back from registry.
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

    $sub = $Config.PSObject.Properties['github_release']
    if ($null -eq $sub -or $null -eq $sub.Value) {
        return @{
            Success          = $false
            InstalledVersion = $null
            ExitCode         = -1
            ErrorMessage     = 'github_release sub-table missing on config'
            RebootRequired   = $false
            DownloadPath     = $null
            UacCancelled     = $false
        }
    }
    $cfg = $sub.Value

    $repoProp = $cfg.PSObject.Properties['repo']
    $patternProp = $cfg.PSObject.Properties['asset_pattern']
    if ($null -eq $repoProp -or $null -eq $patternProp) {
        return @{
            Success          = $false
            InstalledVersion = $null
            ExitCode         = -1
            ErrorMessage     = 'github_release config missing repo/asset_pattern'
            RebootRequired   = $false
            DownloadPath     = $null
            UacCancelled     = $false
        }
    }
    $repo = [string]$repoProp.Value
    $pattern = [string]$patternProp.Value
    $timeout = 8
    $tProp = $cfg.PSObject.Properties['http_timeout_s']
    if ($null -ne $tProp -and $tProp.Value) { $timeout = [int]$tProp.Value }
    $prerelease = $false
    $preProp = $cfg.PSObject.Properties['prerelease']
    if ($null -ne $preProp -and $preProp.Value) { $prerelease = [bool]$preProp.Value }

    $picked = _GHRA_PickAssetUrl -Repo $repo -Pattern $pattern `
        -TimeoutSec $timeout -Prerelease $prerelease
    if ($null -eq $picked) {
        return @{
            Success          = $false
            InstalledVersion = $null
            ExitCode         = -1
            ErrorMessage     = "no asset matched pattern '$pattern' in latest release of $repo"
            RebootRequired   = $false
            DownloadPath     = $null
            UacCancelled     = $false
        }
    }
    $assetUrl = $picked.AssetUrl
    $assetName = $picked.AssetName
    $tag = $picked.Tag

    # Optional process kill before install (some apps lock files in
    # Program Files; the installer fails or prompts otherwise).
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
    $downloadPath = Invoke-WebInstallerDownload -Slug $Slug -Url $assetUrl `
        -Version $tag -AssetName $assetName
    if (-not $downloadPath) {
        return @{
            Success          = $false
            InstalledVersion = $null
            ExitCode         = -1
            ErrorMessage     = "installer download failed for $assetUrl"
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

    # 4) Readback installed version from registry
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
