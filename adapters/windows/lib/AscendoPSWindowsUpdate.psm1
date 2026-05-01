# =============================================================================
# AscendoPSWindowsUpdate.psm1 - PSWindowsUpdate cmdlet wrapper
# =============================================================================
#
# Thin wrappers around the PSWindowsUpdate module's Get-WindowsUpdate /
# Install-WindowsUpdate cmdlets. Used by adapters/windows/scripts/windows_update/*
# to enumerate and install pending Windows OS updates (KBs).
#
# Public surface:
#   Test-PSWindowsUpdateAvailable     - $true if module installed on host
#   Get-PendingWindowsUpdates         - parse Get-WindowsUpdate into PSCustomObjects
#   Install-WindowsUpdateBatch        - wrap Install-WindowsUpdate (no auto-reboot)
#   Convert-WUResultToItemStatus      - map WU result to ascendo/v1 ItemStatus
#
# Compatibility: PowerShell 5.1 + 7.x.
#
# NOTE: This module does NOT auto-install PSWindowsUpdate. That requires
# administrator + internet access + PSGallery trust. The expected install
# path is documented for the operator:
#
#   Install-Module PSWindowsUpdate -Scope CurrentUser -Force
#
# All public functions degrade gracefully if PSWindowsUpdate is missing:
# Get-PendingWindowsUpdates returns @(); Install-WindowsUpdateBatch returns @().
# The script side surfaces this as a [warn]-level phase message.
#
# See Aktualizacje-W11-Dell5520\1_Update-Windows.ps1 for the original real-
# world usage on DP5520WMK that this module is ported from.
# =============================================================================

Set-StrictMode -Version Latest

# -----------------------------------------------------------------------------
# PRIVATE HELPERS
# -----------------------------------------------------------------------------

function Get-WUKBId {
    <#
    .SYNOPSIS
        Extract a canonical KB id (e.g. "KB5034441") from a PSWindowsUpdate
        result object.
    .DESCRIPTION
        PSWindowsUpdate populates `KBArticleIDs` as an array of strings -
        sometimes ('KB5034441'), sometimes ('5034441'). Some non-OS results
        (driver updates, defender definitions) carry an empty array. We
        normalise to 'KB<digits>' when possible, otherwise fall back to the
        update Title which is always non-empty.
    .OUTPUTS
        [string] - canonical KB id, or the update Title if no KB available.
    #>
    [CmdletBinding()]
    [OutputType([string])]
    param(
        [Parameter(Mandatory)] [AllowNull()] $Update
    )

    if ($null -eq $Update) { return '' }

    $kbList = $null
    if ($Update.PSObject.Properties['KBArticleIDs']) {
        $kbList = $Update.KBArticleIDs
    }
    if ($null -ne $kbList -and $kbList.Count -gt 0) {
        $first = [string]$kbList[0]
        if (-not [string]::IsNullOrWhiteSpace($first)) {
            if ($first -match '^KB\d+$') { return $first }
            if ($first -match '^\d+$')   { return ('KB{0}' -f $first) }
            return $first
        }
    }

    if ($Update.PSObject.Properties['Title'] -and $Update.Title) {
        return [string]$Update.Title
    }
    return ''
}

function Format-WUSize {
    <#
    .SYNOPSIS
        Render `MaxDownloadSize` as a human-readable string ("105.3 MB").
    .OUTPUTS
        [string] - empty when size is 0 / null.
    #>
    [CmdletBinding()]
    [OutputType([string])]
    param(
        [Parameter(Mandatory)] [AllowNull()] $Update
    )

    if ($null -eq $Update) { return '' }
    if (-not $Update.PSObject.Properties['MaxDownloadSize']) { return '' }
    $size = $Update.MaxDownloadSize
    if ($null -eq $size -or $size -le 0) { return '' }

    if ($size -ge 1GB) { return ('{0:N1} GB' -f ($size / 1GB)) }
    if ($size -ge 1MB) { return ('{0:N1} MB' -f ($size / 1MB)) }
    if ($size -ge 1KB) { return ('{0:N1} KB' -f ($size / 1KB)) }
    return ('{0} B' -f $size)
}

function Get-WUSeverity {
    <#
    .SYNOPSIS
        Extract the MSRC severity ("Critical" / "Important" / etc.).
    .OUTPUTS
        [string] - the severity, or '' when unspecified.
    #>
    [CmdletBinding()]
    [OutputType([string])]
    param(
        [Parameter(Mandatory)] [AllowNull()] $Update
    )

    if ($null -eq $Update) { return '' }
    foreach ($prop in @('MsrcSeverity','Severity')) {
        if ($Update.PSObject.Properties[$prop] -and $Update.$prop) {
            return [string]$Update.$prop
        }
    }
    return ''
}

# -----------------------------------------------------------------------------
# PUBLIC FUNCTIONS
# -----------------------------------------------------------------------------

function Test-PSWindowsUpdateAvailable {
    <#
    .SYNOPSIS
        Returns $true if the PSWindowsUpdate module is installed on the host.
    .DESCRIPTION
        Uses Get-Module -ListAvailable, which is fast (~30 ms typical) and
        does NOT load the module. The script side calls this once at the
        start of every phase to decide whether to fall back to a no-op
        sidecar with a [warn] message.
    .OUTPUTS
        [bool] - true when at least one PSWindowsUpdate manifest exists in
        any of the $env:PSModulePath roots.
    #>
    [CmdletBinding()]
    [OutputType([bool])]
    param()

    try {
        $mod = Get-Module -ListAvailable -Name PSWindowsUpdate -ErrorAction SilentlyContinue
        return [bool]$mod
    } catch {
        Write-Verbose "Test-PSWindowsUpdateAvailable: error: $_"
        return $false
    }
}

function Get-PendingWindowsUpdates {
    <#
    .SYNOPSIS
        Wraps Get-WindowsUpdate to enumerate pending OS updates.
    .DESCRIPTION
        Imports PSWindowsUpdate (if available), runs Get-WindowsUpdate
        -AcceptAll, and projects each result into a small PSCustomObject
        shape that's stable across PSWindowsUpdate versions. The cmdlet
        itself returns rich .NET interop objects; we keep only the
        properties the sidecar emitter cares about.
    .OUTPUTS
        [PSCustomObject[]] with properties:
          KB              - canonical KB id (e.g. 'KB5034441') or Title fallback
          Title           - human-readable update title
          Size            - formatted size string ('105.3 MB') or ''
          Severity        - MSRC severity ('Critical', 'Important', '') or ''
          IsDownloaded    - [bool]
          IsRebootRequired- [bool]
          Raw             - the original PSWindowsUpdate result object
    .NOTES
        * On hosts without PSWindowsUpdate installed, returns @() with a
          Write-Verbose breadcrumb. Caller is expected to gate on
          Test-PSWindowsUpdateAvailable; this function does not throw.
        * Get-WindowsUpdate may emit nothing when WUA hasn't refreshed
          recently. PSWindowsUpdate handles the refresh internally; we
          simply pass through whatever the cmdlet returns.
    #>
    [CmdletBinding()]
    [OutputType([pscustomobject[]])]
    param()

    if (-not (Test-PSWindowsUpdateAvailable)) {
        Write-Verbose 'Get-PendingWindowsUpdates: PSWindowsUpdate not installed; returning @()'
        return ,@()
    }

    try {
        Import-Module PSWindowsUpdate -Force -ErrorAction Stop | Out-Null
    } catch {
        Write-Verbose "Get-PendingWindowsUpdates: Import-Module failed: $_"
        return ,@()
    }

    $raw = $null
    try {
        # -AcceptAll suppresses the per-EULA prompt that older WU updates
        # pop up; pairs with -Confirm:$false. -ErrorAction Stop so we can
        # actually catch transient WUA errors below.
        $raw = @(Get-WindowsUpdate -AcceptAll -Confirm:$false -ErrorAction Stop 2>$null)
    } catch {
        Write-Verbose "Get-PendingWindowsUpdates: scan failed: $_"
        return ,@()
    }

    if (-not $raw -or $raw.Count -eq 0) {
        return ,@()
    }

    $rows = New-Object System.Collections.Generic.List[object]
    foreach ($u in $raw) {
        if ($null -eq $u) { continue }

        $kb       = Get-WUKBId      -Update $u
        $title    = if ($u.PSObject.Properties['Title']) { [string]$u.Title } else { '' }
        $size     = Format-WUSize   -Update $u
        $severity = Get-WUSeverity  -Update $u

        $isDownloaded = $false
        if ($u.PSObject.Properties['IsDownloaded']) {
            $isDownloaded = [bool]$u.IsDownloaded
        }
        $rebootReq = $false
        if ($u.PSObject.Properties['RebootRequired']) {
            $rebootReq = [bool]$u.RebootRequired
        }

        $rows.Add([pscustomobject]@{
            KB               = $kb
            Title            = $title
            Size             = $size
            Severity         = $severity
            IsDownloaded     = $isDownloaded
            IsRebootRequired = $rebootReq
            Raw              = $u
        })
    }

    return ,$rows.ToArray()
}

function Install-WindowsUpdateBatch {
    <#
    .SYNOPSIS
        Wraps Install-WindowsUpdate to actually install pending updates.
    .DESCRIPTION
        Calls PSWindowsUpdate's Install-WindowsUpdate cmdlet with
        -AutoReboot:$false (CRITICAL: we never auto-reboot from this layer;
        the caller surfaces the reboot-required signal in a phase warning).
        Optionally restricts the install set to a specific KB filter.
    .PARAMETER Filter
        Optional array of KB filters, e.g. @('KB5034441','KB5037997'). When
        omitted/empty, installs ALL pending updates. PSWindowsUpdate's
        -KBArticleID accepts the bare numeric form ("5034441") or the
        canonical "KB" form; we normalise both.
    .PARAMETER AcceptAll
        If $true (default), passes -AcceptAll to suppress per-update prompts.
    .PARAMETER AutoReboot
        Default $false. Set $true ONLY in unit tests; production callers
        should always leave this $false.
    .OUTPUTS
        [PSCustomObject[]] with properties:
          KB             - canonical KB id (or Title fallback)
          Title          - update title
          Result         - 'Installed' / 'Failed' / 'Downloaded' / other raw status
          HResult        - [int?] result HRESULT (0 = success)
          RebootRequired - [bool]
        Returns @() when PSWindowsUpdate is unavailable, when there's
        nothing to install, or when the cmdlet itself throws.
    .NOTES
        * PSWindowsUpdate fires its result callback multiple times per KB
          (Downloading, Installing, Installed). We deduplicate by KB id and
          keep the last seen result. Mirrors 1_Update-Windows.ps1 line ~220.
    #>
    [CmdletBinding()]
    [OutputType([pscustomobject[]])]
    param(
        [Parameter()] [string[]]$Filter,
        [Parameter()] [bool]$AcceptAll = $true,
        [Parameter()] [bool]$AutoReboot = $false
    )

    if (-not (Test-PSWindowsUpdateAvailable)) {
        Write-Verbose 'Install-WindowsUpdateBatch: PSWindowsUpdate not installed; returning @()'
        return ,@()
    }

    try {
        Import-Module PSWindowsUpdate -Force -ErrorAction Stop | Out-Null
    } catch {
        Write-Verbose "Install-WindowsUpdateBatch: Import-Module failed: $_"
        return ,@()
    }

    # Normalise KB filter: 'KB5034441' -> '5034441' (PSWindowsUpdate's
    # -KBArticleID parameter expects the bare numeric form).
    $kbNumbers = $null
    if ($Filter -and $Filter.Count -gt 0) {
        $kbNumbers = @(
            $Filter |
                ForEach-Object {
                    $s = [string]$_
                    if ($s -match '^KB(\d+)$') { $matches[1] }
                    elseif ($s -match '^\d+$') { $s }
                    else { $s }
                } |
                Where-Object { -not [string]::IsNullOrWhiteSpace($_) }
        )
    }

    $installArgs = @{
        Confirm     = $false
        ErrorAction = 'Stop'
    }
    if ($AcceptAll)        { $installArgs['AcceptAll']    = $true }
    if ($AutoReboot)       { $installArgs['AutoReboot']   = $true }
    else                   { $installArgs['IgnoreReboot'] = $true }
    if ($kbNumbers -and $kbNumbers.Count -gt 0) {
        $installArgs['KBArticleID'] = $kbNumbers
    }

    $raw = $null
    try {
        $raw = @(Install-WindowsUpdate @installArgs 2>$null)
    } catch {
        Write-Verbose "Install-WindowsUpdateBatch: install failed: $_"
        return ,@()
    }

    if (-not $raw -or $raw.Count -eq 0) {
        return ,@()
    }

    # Deduplicate by KB id; PSWindowsUpdate fires the result callback once
    # per stage (Downloading, Installing, Installed). Keep the last result.
    $seen = @{}
    foreach ($r in $raw) {
        if ($null -eq $r) { continue }
        $key = Get-WUKBId -Update $r
        if (-not $key) { $key = ('?row{0}' -f $seen.Count) }
        $seen[$key] = $r
    }

    $rows = New-Object System.Collections.Generic.List[object]
    foreach ($key in $seen.Keys) {
        $r = $seen[$key]

        $kb = Get-WUKBId -Update $r
        $title = if ($r.PSObject.Properties['Title']) { [string]$r.Title } else { '' }

        $resultRaw = ''
        if ($r.PSObject.Properties['Result'] -and $r.Result) {
            $resultRaw = [string]$r.Result
        } elseif ($r.PSObject.Properties['Status'] -and $r.Status) {
            $resultRaw = [string]$r.Status
        }

        $hresult = $null
        if ($r.PSObject.Properties['HResult']) {
            try { $hresult = [int]$r.HResult } catch { $hresult = $null }
        }

        $rebootReq = $false
        if ($r.PSObject.Properties['RebootRequired']) {
            $rebootReq = [bool]$r.RebootRequired
        }

        # Normalise the WU "encoded" status (e.g. "[ADI----]" where
        # A=Accepted D=Downloaded I=Installed) into a friendly word for
        # downstream Convert-WUResultToItemStatus.
        $resultNorm = $resultRaw
        if ($resultRaw -match '\[A?D?I' -or $resultRaw -match '\bADI\b') {
            $resultNorm = 'Installed'
        } elseif ($resultRaw -match 'Installed') {
            $resultNorm = 'Installed'
        } elseif ($resultRaw -match 'Downloaded') {
            $resultNorm = 'Downloaded'
        } elseif ($resultRaw -match 'Failed|Error') {
            $resultNorm = 'Failed'
        }

        $rows.Add([pscustomobject]@{
            KB             = $kb
            Title          = $title
            Result         = $resultNorm
            HResult        = $hresult
            RebootRequired = $rebootReq
        })
    }

    return ,$rows.ToArray()
}

function Convert-WUResultToItemStatus {
    <#
    .SYNOPSIS
        Map a PSWindowsUpdate Result string onto an ascendo/v1 ItemStatus.
    .DESCRIPTION
        Mapping:
          'Installed'  -> 'success'
          'Downloaded' -> 'partial' (downloaded but not yet installed)
          'Failed'     -> 'failed'
          anything else -> 'failed'
        ItemStatus has no 'reboot_required' value; the caller is responsible
        for separately surfacing the reboot signal (see Install-WindowsUpdateBatch
        result's RebootRequired field) into a phase-level warn message.
    .PARAMETER Result
        The Result string from Install-WindowsUpdateBatch's PSCustomObject.
    .OUTPUTS
        [string] - one of 'success', 'partial', 'failed'.
    #>
    [CmdletBinding()]
    [OutputType([string])]
    param(
        [Parameter(Mandatory)] [AllowEmptyString()] [AllowNull()] [string]$Result
    )

    if ([string]::IsNullOrWhiteSpace($Result)) { return 'failed' }

    switch -Regex ($Result) {
        '^Installed$'  { return 'success' }
        '^Downloaded$' { return 'partial' }
        '^Failed$'     { return 'failed' }
        default        { return 'failed' }
    }
}

# -----------------------------------------------------------------------------
# Module export
# -----------------------------------------------------------------------------
Export-ModuleMember -Function @(
    'Test-PSWindowsUpdateAvailable'
    'Get-PendingWindowsUpdates'
    'Install-WindowsUpdateBatch'
    'Convert-WUResultToItemStatus'
)
