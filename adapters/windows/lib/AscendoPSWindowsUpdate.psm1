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

function Expand-WUAggregatedRow {
    <#
    .SYNOPSIS
        Split a PSWindowsUpdate result row whose per-property values are
        Object[] (one value per KB) into N per-KB rows with scalar values.
    .DESCRIPTION
        Some PSWindowsUpdate configurations return a single result row
        whose properties are parallel arrays:

            KB             = [KB5087051, KB5089549]
            Title          = ["Cumulative Update ...", "Defender ..."]
            Result         = ["Installed", "Installed"]
            HResult        = [0, 0]
            RebootRequired = [True, True]

        The naive dedup-by-KB downstream key-on-array stringification
        ("KB5087051 KB5089549") which produces ONE merged item with
        Result="Installed Installed" -- which Convert-WUResultToItemStatus
        then classifies as 'failed' (anchored regex ^Installed$ doesn't
        match the joined form). Operator observation on DP5520WMK (run
        f3f9d20f, 2026-05-13): 2 KBs actually installed, reboot pending,
        but apply.ps1 reported items=1 failed=1 success=0.

        This helper walks all PSObject properties, finds the maximum
        array length across them (treating scalars as length 1), and
        emits N output rows. For each output row index i, each
        property's value is either Object[i] (when the source value is
        Object[] of sufficient length) or the source scalar (broadcast).

        Non-aggregated rows (no Object[] property) return the original
        row unchanged inside a 1-element array, so callers can blindly
        iterate the result.
    .PARAMETER Row
        A PSCustomObject from Install-WindowsUpdate. May have scalar
        or Object[] property values.
    .OUTPUTS
        [pscustomobject[]] - 1 or more rows; N if the input was an
        aggregated row of N KBs; 1 otherwise.
    #>
    [CmdletBinding()]
    [OutputType([pscustomobject[]])]
    param(
        [Parameter(Mandatory)] [AllowNull()] $Row
    )

    if ($null -eq $Row) { return ,@() }

    # Determine the fan-out width by scanning all properties for the
    # longest Object[]. Strings count as scalar (length 1) -- strings
    # ARE IEnumerable, but we deliberately treat them as opaque values.
    $maxLen = 1
    $arrayProps = @{}
    foreach ($p in $Row.PSObject.Properties) {
        $v = $p.Value
        if ($v -is [string]) { continue }
        if ($v -is [System.Array]) {
            $len = $v.Length
            if ($len -gt $maxLen) { $maxLen = $len }
            $arrayProps[$p.Name] = $v
        }
    }

    # No fan-out needed -- return the row unchanged inside a 1-element
    # array so callers can iterate uniformly.
    if ($maxLen -le 1 -or $arrayProps.Count -eq 0) {
        return ,@($Row)
    }

    $expanded = New-Object System.Collections.Generic.List[object]
    for ($i = 0; $i -lt $maxLen; $i++) {
        $fields = [ordered]@{}
        foreach ($p in $Row.PSObject.Properties) {
            $v = $p.Value
            # Array property: take element i when in-range, else null.
            # Scalar (incl. string): broadcast to every output row.
            if ($arrayProps.ContainsKey($p.Name)) {
                $arr = $arrayProps[$p.Name]
                if ($i -lt $arr.Length) { $fields[$p.Name] = $arr[$i] } else { $fields[$p.Name] = $null }
            } else {
                $fields[$p.Name] = $v
            }
        }
        $expanded.Add([pscustomobject]$fields) | Out-Null
    }
    return ,$expanded.ToArray()
}

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
        return  # was `return ,@()` — see Stop-PackageProcesses comment
    }

    try {
        Import-Module PSWindowsUpdate -Force -ErrorAction Stop | Out-Null
    } catch {
        Write-Verbose "Get-PendingWindowsUpdates: Import-Module failed: $_"
        return  # was `return ,@()` — see Stop-PackageProcesses comment
    }

    $raw = $null
    try {
        # -AcceptAll suppresses the per-EULA prompt that older WU updates
        # pop up; pairs with -Confirm:$false. -ErrorAction Stop so we can
        # actually catch transient WUA errors below.
        $raw = @(Get-WindowsUpdate -AcceptAll -Confirm:$false -ErrorAction Stop 2>$null)
    } catch {
        Write-Verbose "Get-PendingWindowsUpdates: scan failed: $_"
        return  # was `return ,@()` — see Stop-PackageProcesses comment
    }

    if (-not $raw -or $raw.Count -eq 0) {
        return  # was `return ,@()` — see Stop-PackageProcesses comment
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
        return  # was `return ,@()` — see Stop-PackageProcesses comment
    }

    try {
        Import-Module PSWindowsUpdate -Force -ErrorAction Stop | Out-Null
    } catch {
        Write-Verbose "Install-WindowsUpdateBatch: Import-Module failed: $_"
        return  # was `return ,@()` — see Stop-PackageProcesses comment
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

    # Capture stderr (PowerShell error stream) into a variable so the
    # caller can surface install failures in the sidecar instead of
    # silently dropping them on the floor. Mirrors the macOS apply.sh
    # Sesja 34 stderr-tail pattern. The `-ErrorVariable` parameter
    # binds to the cmdlet's error-stream output; `2>&1` would mingle
    # results into $raw which breaks the dedup-by-KB logic below.
    $raw = $null
    $installErrors = @()
    try {
        $raw = @(Install-WindowsUpdate @installArgs -ErrorVariable installErrors 2>$null)
    } catch {
        # Catch terminating errors (ErrorAction=Stop). Stash on the
        # script-level $WUInstallStderr so the caller can surface it.
        $script:WUInstallStderr = ('Install-WindowsUpdate threw: {0}' -f $_.Exception.Message)
        Write-Verbose "Install-WindowsUpdateBatch: install failed: $_"
        return  # was `return ,@()` — see Stop-PackageProcesses comment
    }

    # Stash non-terminating errors (e.g. "no updates pending", "agent
    # busy", per-KB download failures) for the caller. Format last 12
    # error records, capped at 1500 chars total.
    if ($installErrors -and $installErrors.Count -gt 0) {
        $tailLines = @($installErrors |
                       ForEach-Object { [string]$_ } |
                       Where-Object { $_ -and $_.Trim() } |
                       Select-Object -Last 12)
        $joined = ($tailLines -join "`n")
        if ($joined.Length -gt 1500) {
            $joined = $joined.Substring($joined.Length - 1500)
        }
        $script:WUInstallStderr = $joined
    } else {
        $script:WUInstallStderr = ''
    }

    if (-not $raw -or $raw.Count -eq 0) {
        return  # was `return ,@()` — see Stop-PackageProcesses comment
    }

    # Some PSWindowsUpdate configurations (especially when -AcceptAll
    # and -IgnoreReboot are both set, as we do) collapse multiple
    # updates into ONE result row where the per-property values are
    # Object[] in parallel: KB=[KB1,KB2], Title=[T1,T2], Result=
    # [Installed,Installed], HResult=[0,0], RebootRequired=[True,True].
    # Without expansion, the dedup below sees the joined string
    # ("KB1 KB2") as a single key and emits one bogus item with
    # id="KB1 KB2", Result="Installed Installed" — which the regex in
    # Convert-WUResultToItemStatus then classifies as 'failed' (because
    # ^Installed$ doesn't match "Installed Installed"). Operator
    # observation on DP5520WMK (run f3f9d20f, 2026-05-13 09:09 UTC):
    # 2 KBs actually installed (system asked for reboot) but the apply
    # phase reported `failed items=1 failed=1 success=0`.
    #
    # Expand-WUAggregatedRow splits an aggregated row into N per-KB
    # rows by walking each Object[] property in parallel. Scalar
    # properties get broadcast (same value on every output row).
    $expanded = New-Object System.Collections.Generic.List[object]
    foreach ($r in $raw) {
        if ($null -eq $r) { continue }
        $exp = Expand-WUAggregatedRow -Row $r
        foreach ($e in $exp) { $expanded.Add($e) | Out-Null }
    }

    # Deduplicate by KB id; PSWindowsUpdate fires the result callback once
    # per stage (Downloading, Installing, Installed). Keep the last result.
    $seen = @{}
    foreach ($r in $expanded) {
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

function Get-WUInstallStderr {
    <#
    .SYNOPSIS
        Returns the captured error-stream tail from the most recent
        Install-WindowsUpdateBatch invocation, or '' if none.
    .DESCRIPTION
        Parity with the macOS apply.sh Sesja 34 stderr-tail pattern.
        PSWindowsUpdate emits non-terminating errors for transient
        problems (agent busy, KB download failed, certificate not
        trusted, etc.) that we'd otherwise swallow with `2>$null`.
        This accessor lets the apply phase surface them in the sidecar.
    .OUTPUTS
        [string] - last 12 non-empty error lines, capped at 1500 chars.
    #>
    [CmdletBinding()] [OutputType([string])] param()
    if ($script:WUInstallStderr) { return [string]$script:WUInstallStderr }
    return ''
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
    'Get-WUInstallStderr'
    'Convert-WUResultToItemStatus'
    'Expand-WUAggregatedRow'
)
