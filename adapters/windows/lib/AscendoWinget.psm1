# =============================================================================
# AscendoWinget.psm1 - winget output parsing + exit-code helpers
# =============================================================================
#
# Extracts the hidden gems from Ascendo/3_Update-Programs.ps1
# (the Windows pre-merge codebase that took ~6 patch iterations to stabilise).
#
# Public surface:
#   Initialize-WingetEnvironment      - set UTF-8, return previous encoding
#   Restore-WingetEnvironment         - restore previous encoding
#   Get-WingetUpgradable              - parse winget upgrade output
#   Get-WingetInstalled               - parse winget list output
#   Convert-WingetExitCode            - map winget exit codes to status
#   Resolve-WingetId                  - strip embedded version from Id field
#
# Compatibility: PowerShell 5.1 + 7.x.
# See ../scripts/check__winget.ps1 for the consumer.
# =============================================================================

Set-StrictMode -Version Latest

# -----------------------------------------------------------------------------
# Exit-code constants
# -----------------------------------------------------------------------------
# Source: Ascendo/CLAUDE.md > Key Design Decisions item 4
# Confirmed in 3_Update-Programs.ps1 (search for -1978335190, -1978335212, 3010).
$script:WINGET_EXIT_SUCCESS         = 0
$script:WINGET_EXIT_UP_TO_DATE_A    = -1978335190    # 0x8A15002A (Config error)
$script:WINGET_EXIT_UP_TO_DATE_B    = -1978335189    # 0x8A15002B (CLI error)
$script:WINGET_EXIT_ID_NOT_FOUND    = -1978335212    # 0x8A150014
$script:WINGET_EXIT_REBOOT_REQUIRED = 3010

# -----------------------------------------------------------------------------
# PRIVATE HELPERS
# -----------------------------------------------------------------------------
# IMPORTANT: every helper must be defined BEFORE the public function that calls
# it. PowerShell parses files top-to-bottom; a helper defined AFTER its first
# use silently returns $null (this is exactly the bug fixed on 2026-03-24 in
# 3_Update-Programs.ps1, where Get-ColValue lived at line ~1394 but was called
# at line ~383 — the new-app detection scan returned 0 IDs every run).
# -----------------------------------------------------------------------------

function Get-WingetColValue {
    <#
    .SYNOPSIS
        Extract a column value from a winget table row using a pre-detected
        column-start map.
    .DESCRIPTION
        Guards against:
          * $start -lt 0 (column header missing -> IndexOf returned -1)
          * $start -ge $src.Length (row shorter than column start)
          * $len -le 0 (no characters between this column and the next)
        Trims whitespace from the result and returns ''.
        Ported from 3_Update-Programs.ps1 line ~1394 (Get-ColValue) and
        4_Generate-ProgramsList.ps1 line ~61 (Get-WingetColValue).
        The $start -lt 0 guard was added 2026-04-09 to fix a Substring(-1, n)
        exception when a column was absent from the header.
    .PARAMETER Source
        The data row to extract from.
    .PARAMETER Positions
        Array of column-start indices (one entry per column, in order).
    .PARAMETER Index
        Zero-based column index to extract.
    .OUTPUTS
        [string] - the trimmed column value, or '' if the column is missing.
    #>
    [CmdletBinding()]
    [OutputType([string])]
    param(
        [Parameter(Mandatory)] [AllowEmptyString()] [string]$Source,
        [Parameter(Mandatory)] [int[]]$Positions,
        [Parameter(Mandatory)] [int]$Index
    )

    if ($Index -ge $Positions.Count) { return '' }
    $start = $Positions[$Index]
    # CRITICAL: -1 means IndexOf() did not find the column in the header.
    # Without this guard, Substring(-1, n) throws ArgumentOutOfRangeException.
    if ($start -lt 0)             { return '' }
    if ($start -ge $Source.Length) { return '' }

    # Find the next column to the RIGHT of this one. Cannot just use
    # $Positions[$Index + 1] because columns may be reported out-of-order
    # (rare, but happens when a header column is absent and IndexOf returns
    # a value smaller than the previous column's start index).
    $end = $Source.Length
    for ($i = $Index + 1; $i -lt $Positions.Count; $i++) {
        if ($Positions[$i] -gt $start) {
            $end = $Positions[$i]
            break
        }
    }

    $len = [Math]::Min($end - $start, $Source.Length - $start)
    if ($len -le 0) { return '' }
    return $Source.Substring($start, $len).Trim()
}

function Get-WingetColumnStarts {
    <#
    .SYNOPSIS
        Parse a winget header line into an ordered list of column-start
        indices, plus a layout flag.
    .DESCRIPTION
        Detects which of the 4 known winget table layouts is in play:
          * upgrade-5col : Name | Id | Version | Available | Source
          * upgrade-4col : Name | Id | Available | Source
          * list-4col    : Name | Id | Version | Source         (winget list)
          * list-3col    : Name | Id | Source                   (rare; ARP-only)
        IMPORTANT: header detection uses the LITERAL \b word-boundary
        metacharacter, NOT the \x08 backspace byte. The 2026-03-24 bug in
        3_Update-Programs.ps1 was caused by editor corruption replacing \b
        with the backspace control character — the regex never matched and
        the parser silently returned 0 rows. Be paranoid about this.
    .PARAMETER Header
        The detected header line (the line immediately BEFORE a ^-{3,}
        separator row in winget output).
    .OUTPUTS
        [pscustomobject] with properties:
          Layout    : 'upgrade-5col' | 'upgrade-4col' | 'list-4col' | 'list-3col' | 'unknown'
          Positions : [int[]]  (column-start indices in column order)
          NumCols   : [int]    (count of columns)
          Columns   : [string[]] (column names matching Positions)
    .NOTES
        Ported from 3_Update-Programs.ps1 lines ~1560-1577 (upgrade table) and
        4_Generate-ProgramsList.ps1 lines ~225-249 (list table).
    #>
    [CmdletBinding()]
    [OutputType([pscustomobject])]
    param(
        [Parameter(Mandatory)] [AllowEmptyString()] [string]$Header
    )

    $hasName      = [regex]::IsMatch($Header, '\bName\b')
    $hasId        = [regex]::IsMatch($Header, '\bId\b')
    $hasVersion   = [regex]::IsMatch($Header, '\bVersion\b')
    $hasAvailable = [regex]::IsMatch($Header, '\bAvailable\b')
    $hasSource    = [regex]::IsMatch($Header, '\bSource\b')

    if (-not $hasId) {
        return [pscustomobject]@{
            Layout    = 'unknown'
            Positions = @()
            NumCols   = 0
            Columns   = @()
        }
    }

    # IndexOf returns -1 if the column is absent. Get-WingetColValue's
    # $start -lt 0 guard handles that case downstream (returns '').
    $idxName      = if ($hasName) { $Header.IndexOf('Name') } else { 0 }
    if ($idxName -lt 0) { $idxName = 0 }
    $idxId        = $Header.IndexOf('Id')
    $idxVersion   = if ($hasVersion)   { $Header.IndexOf('Version')   } else { -1 }
    $idxAvailable = if ($hasAvailable) { $Header.IndexOf('Available') } else { -1 }
    $idxSource    = if ($hasSource)    { $Header.IndexOf('Source')    } else { -1 }

    if ($hasVersion -and $hasAvailable) {
        # winget upgrade, full 5-column format
        return [pscustomobject]@{
            Layout    = 'upgrade-5col'
            Positions = @($idxName, $idxId, $idxVersion, $idxAvailable, $idxSource)
            NumCols   = 5
            Columns   = @('Name', 'Id', 'Version', 'Available', 'Source')
        }
    }
    if ($hasAvailable -and -not $hasVersion) {
        # winget upgrade, 4-column (no current version, ARP-detected packages)
        return [pscustomobject]@{
            Layout    = 'upgrade-4col'
            Positions = @($idxName, $idxId, $idxAvailable, $idxSource)
            NumCols   = 4
            Columns   = @('Name', 'Id', 'Available', 'Source')
        }
    }
    if ($hasVersion -and -not $hasAvailable) {
        # winget list, has Version (no Available column - listing not upgrades)
        return [pscustomobject]@{
            Layout    = 'list-4col'
            Positions = @($idxName, $idxId, $idxVersion, $idxSource)
            NumCols   = 4
            Columns   = @('Name', 'Id', 'Version', 'Source')
        }
    }
    # winget list, no Version (very rare)
    return [pscustomobject]@{
        Layout    = 'list-3col'
        Positions = @($idxName, $idxId, $idxSource)
        NumCols   = 3
        Columns   = @('Name', 'Id', 'Source')
    }
}

function Read-WingetTabularOutput {
    <#
    .SYNOPSIS
        Parse a raw winget tabular-output blob into PSCustomObject rows.
    .DESCRIPTION
        Shared parser used by Get-WingetUpgradable + Get-WingetInstalled.

        Algorithm (separator-before-header detection):
          1. Iterate lines, tracking the PREVIOUS line.
          2. When a ^-{3,} separator row is seen, the previous line WAS the
             column header. Capture column-start positions from it.
          3. For every subsequent non-noise line, extract columns by
             Substring() at the captured positions.
        This is more robust than regex-matching the header directly because
        it is immune to:
          * banner text (winget version banner above the table)
          * locale changes (column names translated in non-English locales:
            we still detect the separator and read whatever columns the
            preceding line declares)
          * spinner / CR-overwrite artefacts ("\r" characters in the output)
          * leading whitespace
        Bug history: 3_Update-Programs.ps1 originally regex-matched the
        header directly, but the regex used \x08 (backspace) instead of \b
        (word boundary) due to editor corruption. Fixed 2026-03-24 by
        switching to separator-before-header detection.
    .PARAMETER RawOutput
        The combined stdout+stderr from a winget invocation (typically
        captured via "winget ... 2>&1 | Out-String").
    .OUTPUTS
        [pscustomobject[]] with one entry per data row. Property set depends
        on the detected layout (Name, Id, Version, Available, Source).
        Available + Version are only present if the layout has those columns.
    .NOTES
        Filtered noise:
          * empty lines
          * separator lines (^-{3,})
          * "X package(s) have version numbers that cannot be determined"
          * "X upgrades available"
          * trailing notes ("The following packages have an upgrade...")
        These were the false-positive packages reported in the 2026-03-24
        bug ("X package(s) have... cannot be determined" was being parsed
        as a row).
    #>
    [CmdletBinding()]
    [OutputType([pscustomobject[]])]
    param(
        [Parameter(Mandatory)] [AllowEmptyString()] [string]$RawOutput
    )

    $rows      = New-Object System.Collections.Generic.List[object]
    $layout    = $null
    $inTable   = $false
    $prevLine  = ''

    foreach ($rawLine in ($RawOutput -split "`n")) {
        # Strip trailing \r (winget uses \r-overwrite for spinner progress)
        $line = $rawLine.TrimEnd("`r")

        if (-not $inTable) {
            if ($line -match '^-{3,}\s*$') {
                # Previous line was the header - capture column positions
                $layout  = Get-WingetColumnStarts -Header $prevLine
                $inTable = $true
                Write-Verbose "Read-WingetTabularOutput: detected layout '$($layout.Layout)' (cols: $($layout.Columns -join ', '))"
                continue
            }
            $prevLine = $line
            continue
        }

        # Inside the data table now
        $line    = $line.TrimEnd()
        $trimmed = $line.Trim()

        # Filter noise rows
        if ($trimmed -eq '')                                              { continue }
        if ($trimmed -match '^-{3,}\s*$')                                 { continue }
        if ($trimmed -match '^\d+ upgrades? available')                   { continue }
        if ($trimmed -match '^\d+ package')                               { continue }
        if ($trimmed -match 'cannot be determined')                       { continue }
        if ($trimmed -match '^The following packages')                    { continue }
        if ($trimmed -match '^No installed package found')                { continue }

        if (-not $layout -or $layout.Layout -eq 'unknown' -or $layout.Positions.Count -lt 2) {
            # Header detection failed - skip data rows. The caller can fall
            # back to whitespace-split if it cares; we only emit positionally
            # parsed rows from this function.
            continue
        }

        $name = Get-WingetColValue -Source $line -Positions $layout.Positions -Index 0
        $id   = Resolve-WingetId  -RawId   (Get-WingetColValue -Source $line -Positions $layout.Positions -Index 1)
        if (-not $name -or -not $id) { continue }

        # Defensive sanity check — drop rows that look like parser-merged
        # output. Real winget IDs are dot-separated tokens of alphanumerics,
        # hyphens, and underscores; they never contain internal whitespace
        # and rarely exceed 80 characters. Lengths over 256 or any internal
        # whitespace indicate adjacent rows were collapsed into one (the
        # AppX/MSIX continuation-line case observed on DP5520WMK with
        # AutoHotkey). We skip these rows and emit a verbose-level warning
        # rather than poisoning the items[] with a synthetic super-row.
        if ($id.Length -gt 256 -or $id -match '\s') {
            $preview = if ($id.Length -gt 80) { $id.Substring(0, 80) + '...' } else { $id }
            Write-Verbose "Read-WingetTabularOutput: skipping suspected merged row (id length=$($id.Length), has-whitespace=$([bool]($id -match '\s'))): $preview"
            continue
        }

        switch ($layout.Layout) {
            'upgrade-5col' {
                $rows.Add([pscustomobject]@{
                    Name      = $name
                    Id        = $id
                    Version   = Get-WingetColValue -Source $line -Positions $layout.Positions -Index 2
                    Available = Get-WingetColValue -Source $line -Positions $layout.Positions -Index 3
                    Source    = Get-WingetColValue -Source $line -Positions $layout.Positions -Index 4
                })
            }
            'upgrade-4col' {
                $rows.Add([pscustomobject]@{
                    Name      = $name
                    Id        = $id
                    Version   = 'Unknown'
                    Available = Get-WingetColValue -Source $line -Positions $layout.Positions -Index 2
                    Source    = Get-WingetColValue -Source $line -Positions $layout.Positions -Index 3
                })
            }
            'list-4col' {
                $rows.Add([pscustomobject]@{
                    Name    = $name
                    Id      = $id
                    Version = Get-WingetColValue -Source $line -Positions $layout.Positions -Index 2
                    Source  = Get-WingetColValue -Source $line -Positions $layout.Positions -Index 3
                })
            }
            'list-3col' {
                $rows.Add([pscustomobject]@{
                    Name    = $name
                    Id      = $id
                    Version = 'Unknown'
                    Source  = Get-WingetColValue -Source $line -Positions $layout.Positions -Index 2
                })
            }
            default {
                # Unknown layout - skip silently
                continue
            }
        }
    }

    # Return the array directly. The leading comma form ``,$rows.ToArray()``
    # was an over-wrap: it emitted a 1-element pipeline object containing
    # the entire inner array, which downstream callers using ``@(...)``
    # collected as a single super-element. PowerShell's implicit array→
    # string coercion then joined every package's ``.Id`` (and ``.Name``,
    # ``.Version`` …) with spaces, producing inventories that surfaced as
    # one giant row whose name was a space-mash of every installed app.
    # Without the comma, the array is enumerated through the pipeline as
    # one object per row, which ``@(...)`` collects correctly. Empty case:
    # ``ToArray()`` returns ``Object[0]`` and the pipeline emits nothing —
    # callers see ``Count == 0``.
    return $rows.ToArray()
}

# -----------------------------------------------------------------------------
# PUBLIC FUNCTIONS
# -----------------------------------------------------------------------------

function Initialize-WingetEnvironment {
    <#
    .SYNOPSIS
        Set up the console environment for reliable winget output parsing.
    .DESCRIPTION
        Sets [Console]::OutputEncoding to UTF-8 and (optionally) sets
        $env:WINGET_DISABLE_INTERACTIVITY=1.

        WHY THIS MATTERS: winget truncates long Name and Id columns with the
        UTF-8 ellipsis character '...' (U+2026, encoded as 3 bytes in UTF-8:
        0xE2 0x80 0xA6). With the default OEM/ANSI console encoding on
        Windows (cp437/cp1252), each of those 3 bytes is decoded as a
        separate ASCII character — meaning every truncated row is 2
        characters longer than the parser expects, shifting all subsequent
        column offsets right by 2. The result is garbled IDs in PROGRAMS.md
        (the bug that motivated the 2026-03-24 fix in 4_Generate-ProgramsList.ps1).

        Setting OutputEncoding to UTF-8 makes .NET decode '...' as a single
        character, keeping column offsets accurate.
    .PARAMETER DisableInteractivityEnv
        When $true, sets $env:WINGET_DISABLE_INTERACTIVITY=1 in addition to
        the per-call --disable-interactivity flag. This is a belt-and-braces
        measure for unattended runs.
    .OUTPUTS
        [System.Text.Encoding] - the previous OutputEncoding. Pass this to
        Restore-WingetEnvironment when done.
    .EXAMPLE
        $prev = Initialize-WingetEnvironment
        try {
            Get-WingetUpgradable
        } finally {
            Restore-WingetEnvironment -PreviousEncoding $prev
        }
    .NOTES
        Ported from 3_Update-Programs.ps1 line 35 (top-of-script encoding
        setup) and 4_Generate-ProgramsList.ps1 (same pattern, applied
        2026-03-24 as part of the MSIX-ID column-shift fix).
    #>
    [CmdletBinding()]
    [OutputType([System.Text.Encoding])]
    param(
        [switch]$DisableInteractivityEnv
    )

    $previous = [Console]::OutputEncoding
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
    Write-Verbose "Initialize-WingetEnvironment: OutputEncoding $($previous.WebName) -> utf-8"

    if ($DisableInteractivityEnv) {
        $env:WINGET_DISABLE_INTERACTIVITY = '1'
        Write-Verbose 'Initialize-WingetEnvironment: WINGET_DISABLE_INTERACTIVITY=1'
    }

    return $previous
}

function Restore-WingetEnvironment {
    <#
    .SYNOPSIS
        Restore the previous console OutputEncoding.
    .DESCRIPTION
        Pair with Initialize-WingetEnvironment in a try/finally block.
        If $PreviousEncoding is $null (e.g. caller never initialised) this
        is a silent no-op.
    .PARAMETER PreviousEncoding
        The encoding returned by Initialize-WingetEnvironment.
    .EXAMPLE
        Restore-WingetEnvironment -PreviousEncoding $prev
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [AllowNull()] [System.Text.Encoding]$PreviousEncoding
    )

    if ($null -eq $PreviousEncoding) {
        Write-Verbose 'Restore-WingetEnvironment: no previous encoding supplied; no-op'
        return
    }
    [Console]::OutputEncoding = $PreviousEncoding
    Write-Verbose "Restore-WingetEnvironment: OutputEncoding -> $($PreviousEncoding.WebName)"
}

function Resolve-WingetId {
    <#
    .SYNOPSIS
        Strip an embedded version suffix from a parsed winget Id field.
    .DESCRIPTION
        When the Id column is too narrow, winget sometimes merges a trailing
        " <version>" into the Id field. Example raw Id from the output:

            "Microsoft.WindowsTerminal 1.18.10301.0"

        A real winget Id contains only word chars, dots and hyphens (no
        spaces). This function strips a trailing "<digits>.<digits>..."
        suffix introduced by column-merge. Inputs without that suffix are
        returned unchanged.
    .PARAMETER RawId
        The string captured from the Id column.
    .OUTPUTS
        [string] - cleaned Id (or the original input if no version suffix).
    .NOTES
        Ported from 3_Update-Programs.ps1 line ~1524-1531. The regex must
        match the WHOLE line (`^...$`) so a legitimate version inside an Id
        (impossible per winget rules, but be defensive) is not stripped
        from a partial match.
    .EXAMPLE
        Resolve-WingetId -RawId 'Mozilla.Firefox 122.0.1'
        # returns 'Mozilla.Firefox'
    .EXAMPLE
        Resolve-WingetId -RawId 'Mozilla.Firefox'
        # returns 'Mozilla.Firefox'
    #>
    [CmdletBinding()]
    [OutputType([string])]
    param(
        [Parameter(Mandatory)] [AllowEmptyString()] [string]$RawId
    )

    if ([string]::IsNullOrWhiteSpace($RawId)) { return '' }

    # Pattern: a proper Id (Vendor.App or Vendor.App.SubId) followed by
    # whitespace then a version literal (digits + dots/hyphens) and
    # optional trailing junk.
    $pattern = '^([A-Za-z][A-Za-z0-9_-]*(?:\.[A-Za-z0-9_-]+)+)\s+[\d][\d\.\-]+.*$'
    $m = [regex]::Match($RawId, $pattern)
    if ($m.Success) {
        return $m.Groups[1].Value
    }
    return $RawId
}

function Convert-WingetExitCode {
    <#
    .SYNOPSIS
        Map a winget exit code to a structured success/failure result.
    .DESCRIPTION
        Recognised codes:
          0           SUCCESS                    Standard success
          -1978335190 UP_TO_DATE                 0x8A15002A -- "no applicable update found"
          -1978335212 ID_NOT_FOUND               0x8A150014 -- "no installed package found
                                                  matching input criteria" (try name fallback)
          3010        REBOOT_REQUIRED            Installer succeeded but requires restart
          *           FAILED                     Anything else - log with code for triage
    .PARAMETER ExitCode
        The integer exit code from $LASTEXITCODE after a winget call.
    .OUTPUTS
        [pscustomobject] with:
          Status      : 'success' | 'up_to_date' | 'failed' | 'reboot_required' | 'id_not_found'
          IsSuccess   : [bool] (true for success / up_to_date / reboot_required)
          Description : Human-readable string for logs / UI
          ExitCode    : The original exit code (passed through)
    .NOTES
        Ported from 3_Update-Programs.ps1; see the exit-code switch around
        the per-package upgrade loop (search "1978335190").
        Status code conventions chosen here:
          - 'id_not_found' is FAILED-class (IsSuccess = $false) because the
            caller usually wants to attempt a name-based fallback. It is a
            recoverable failure, not a success.
          - 'reboot_required' is SUCCESS-class (IsSuccess = $true) because
            the install actually completed; the caller should surface the
            restart-needed signal but not retry the upgrade.
    .EXAMPLE
        $r = Convert-WingetExitCode -ExitCode -1978335190
        if ($r.IsSuccess) { Write-Verbose "$($r.Description)" }
    #>
    [CmdletBinding()]
    [OutputType([pscustomobject])]
    param(
        [Parameter(Mandatory)] [int]$ExitCode
    )

    switch ($ExitCode) {
        $script:WINGET_EXIT_SUCCESS {
            return [pscustomobject]@{
                Status      = 'success'
                IsSuccess   = $true
                Description = 'Operation completed successfully.'
                ExitCode    = $ExitCode
            }
        }
        $script:WINGET_EXIT_UP_TO_DATE_A {
            return [pscustomobject]@{
                Status      = 'up_to_date'
                IsSuccess   = $true
                Description = 'Package is already up to date (no applicable update found).'
                ExitCode    = $ExitCode
            }
        }
        $script:WINGET_EXIT_UP_TO_DATE_B {
            return [pscustomobject]@{
                Status      = 'up_to_date'
                IsSuccess   = $true
                Description = 'Package is already up to date (no applicable update found).'
                ExitCode    = $ExitCode
            }
        }
        $script:WINGET_EXIT_ID_NOT_FOUND {
            return [pscustomobject]@{
                Status      = 'id_not_found'
                IsSuccess   = $false
                Description = 'No installed package matched the requested Id (try name-based fallback).'
                ExitCode    = $ExitCode
            }
        }
        $script:WINGET_EXIT_REBOOT_REQUIRED {
            return [pscustomobject]@{
                Status      = 'reboot_required'
                IsSuccess   = $true
                Description = 'Installer succeeded but a system restart is required to complete the update.'
                ExitCode    = $ExitCode
            }
        }
        default {
            return [pscustomobject]@{
                Status      = 'failed'
                IsSuccess   = $false
                Description = "winget returned non-zero exit code $ExitCode (see logs for details)."
                ExitCode    = $ExitCode
            }
        }
    }
}

function Get-WingetUpgradable {
    <#
    .SYNOPSIS
        Run `winget upgrade` and parse the column-tabular output into objects.
    .DESCRIPTION
        Invokes `winget upgrade --accept-source-agreements --include-unknown
        --disable-interactivity` and parses the result with the column-
        position parser.

        Handles 5-column (Name/Id/Version/Available/Source) and 4-column
        (Name/Id/Available/Source) output formats. The 4-column format is
        emitted when winget detected a package via ARP only and cannot
        determine a current version - in that case Version is reported as
        'Unknown'.

        Skips banner text, info messages ("X package(s) have version
        numbers that cannot be determined"), and trailing notes.
    .PARAMETER IncludePinned
        When set, passes --include-pinned so pinned packages also appear in
        the result. Default is false (matches winget default behaviour).
    .OUTPUTS
        [pscustomobject[]] - each with properties Name, Id, Version,
        Available, Source. Version may be 'Unknown'.
    .EXAMPLE
        $prev = Initialize-WingetEnvironment
        try {
            Get-WingetUpgradable | Where-Object { $_.Available -ne '' }
        } finally {
            Restore-WingetEnvironment -PreviousEncoding $prev
        }
    .NOTES
        Ported from 3_Update-Programs.ps1 lines 1533-1610 (the discovery
        block before the per-package upgrade loop). The --disable-interactivity
        flag is mandatory for unattended operation.
    #>
    [CmdletBinding()]
    [OutputType([pscustomobject[]])]
    param(
        [switch]$IncludePinned
    )

    $argsList = @(
        'upgrade'
        '--accept-source-agreements'
        '--include-unknown'
        '--disable-interactivity'
    )
    if ($IncludePinned) { $argsList += '--include-pinned' }

    Write-Verbose "Get-WingetUpgradable: invoking 'winget $($argsList -join ' ')'"
    $raw = & winget @argsList 2>&1 | Out-String

    return Read-WingetTabularOutput -RawOutput $raw
}

function Get-WingetInstalled {
    <#
    .SYNOPSIS
        Run `winget list` and parse the column-tabular output.
    .DESCRIPTION
        Invokes `winget list --accept-source-agreements --disable-interactivity`
        and parses the result with the column-position parser.
        Returns one PSCustomObject per installed package winget can see
        (this includes ARP and MSIX synthetic IDs in addition to true
        winget-source packages).
    .OUTPUTS
        [pscustomobject[]] - Name, Id, Version, Source. Version is 'Unknown'
        when winget cannot determine it (rare; usually for ARP-only
        detected packages). Source may be empty for ARP-detected packages.
    .EXAMPLE
        $prev = Initialize-WingetEnvironment
        try {
            $apps = Get-WingetInstalled
            $wingetIds = $apps | Where-Object { $_.Id -match '^[A-Za-z][A-Za-z0-9._-]+\.[A-Za-z0-9._-]+$' }
        } finally {
            Restore-WingetEnvironment -PreviousEncoding $prev
        }
    .NOTES
        Ported from 3_Update-Programs.ps1 lines 1434-1466 (the new-app
        detection scan) and 4_Generate-ProgramsList.ps1 lines 213-283 (the
        winget inventory pass). Note: `winget list` does not have an
        Available column - this function returns 4 columns at most
        (Name/Id/Version/Source).
    #>
    [CmdletBinding()]
    [OutputType([pscustomobject[]])]
    param()

    $argsList = @(
        'list'
        '--accept-source-agreements'
        '--disable-interactivity'
    )

    Write-Verbose "Get-WingetInstalled: invoking 'winget $($argsList -join ' ')'"
    $raw = & winget @argsList 2>&1 | Out-String

    return Read-WingetTabularOutput -RawOutput $raw
}

# -----------------------------------------------------------------------------
# Unknown-version suppression state file
# -----------------------------------------------------------------------------
# Some winget packages have no usable installed version: their Inno Setup
# uninstall registry entry lacks DisplayVersion, and ``winget list`` reports
# Version='Unknown' both before AND after a successful upgrade. The classic
# example on DP5520WMK (2026-05-13) is ``SoftSea.IMGtoISO``:
#
#   Name       Id               Version Available Source
#   IMG to ISO SoftSea.IMGtoISO Unknown 1.0       winget
#
# Without state, every check phase re-classifies it as ``planned`` (Available
# differs from "Unknown") and every apply phase re-runs the installer -- even
# when the operator just installed version 1.0 minutes ago.
#
# The fix: persist a per-id mark whenever apply succeeds with a known target.
# On subsequent check phases, if winget still reports Unknown AND the
# Available matches the marked target, classify as ``up_to_date`` and
# surface the marked version as installed.
#
# State file: $env:ASCENDO_STATE_DIR/winget_apply_marks.json, defaulting to
# $env:USERPROFILE/.ascendo/state/winget_apply_marks.json. JSON shape:
#
#   {
#     "SoftSea.IMGtoISO": {"target": "1.0", "appliedAt": "2026-05-13T12:36Z"},
#     "Nomacs.nomacs":    {"target": "3.20.0", ...}
#   }
#
# Operator override / reset: delete the file (or remove a single id) to
# force re-detection. The mark is read-only metadata; nothing else in the
# pipeline depends on it.
# -----------------------------------------------------------------------------

function _Get-AscendoApplyMarksPath {
    $base = if ($env:ASCENDO_STATE_DIR) {
        [string]$env:ASCENDO_STATE_DIR
    } elseif ($env:USERPROFILE) {
        Join-Path $env:USERPROFILE '.ascendo\state'
    } else {
        # Tests / containers without USERPROFILE: fall back to TEMP.
        Join-Path $env:TEMP 'ascendo-state'
    }
    return (Join-Path $base 'winget_apply_marks.json')
}

function Get-AscendoApplyMark {
    <#
    .SYNOPSIS
        Return the persisted apply-mark for a winget package id, or $null
        when no mark exists.

    .DESCRIPTION
        The mark records the last version we successfully applied for a
        given id. Check phase consults it to suppress the
        "Version=Unknown but a known Available exists" false-positive
        outdated state.

    .PARAMETER Id
        Winget package id (e.g. ``SoftSea.IMGtoISO``).

    .OUTPUTS
        [pscustomobject] with ``target`` (string) + ``appliedAt`` (string),
        or $null when the state file is missing / unreadable / has no
        entry for this id.
    #>
    [CmdletBinding()]
    [OutputType([pscustomobject])]
    param(
        [Parameter(Mandatory)] [string] $Id
    )

    $path = _Get-AscendoApplyMarksPath
    if (-not (Test-Path -LiteralPath $path)) { return $null }
    try {
        $raw = Get-Content -LiteralPath $path -Raw -Encoding UTF8
    } catch { return $null }
    if ([string]::IsNullOrWhiteSpace($raw)) { return $null }

    # Parse defensively; corrupt state shouldn't crash the phase.
    $data = $null
    try {
        if ($PSVersionTable.PSVersion.Major -ge 6) {
            $data = $raw | ConvertFrom-Json -AsHashtable
        } else {
            $data = $raw | ConvertFrom-Json
        }
    } catch { return $null }
    if ($null -eq $data) { return $null }

    if ($data -is [System.Collections.IDictionary]) {
        if (-not $data.ContainsKey($Id)) { return $null }
        $entry = $data[$Id]
    } else {
        $prop = $data.PSObject.Properties[$Id]
        if (-not $prop) { return $null }
        $entry = $prop.Value
    }
    if ($null -eq $entry) { return $null }

    # Normalise to a PSCustomObject so callers don't need to switch on type.
    $targetVal = $null
    $appliedVal = $null
    if ($entry -is [System.Collections.IDictionary]) {
        if ($entry.Contains('target'))    { $targetVal  = [string]$entry['target'] }
        if ($entry.Contains('appliedAt')) { $appliedVal = [string]$entry['appliedAt'] }
    } else {
        $tp = $entry.PSObject.Properties['target']
        if ($tp -and $tp.Value) { $targetVal = [string]$tp.Value }
        $ap = $entry.PSObject.Properties['appliedAt']
        if ($ap -and $ap.Value) { $appliedVal = [string]$ap.Value }
    }
    if (-not $targetVal) { return $null }
    return [pscustomobject]@{
        target    = $targetVal
        appliedAt = $appliedVal
    }
}

function Set-AscendoApplyMark {
    <#
    .SYNOPSIS
        Persist the version we just applied for a winget package id, so
        future check phases can suppress the unknown-version false-positive.

    .PARAMETER Id
        Winget package id.

    .PARAMETER Target
        The version that was just applied (typically pkg.Available from
        the upgrade output). Must be non-empty and not ``'Unknown'`` --
        marking with Unknown defeats the purpose.

    .OUTPUTS
        None. Failure to write is logged via Write-Verbose and swallowed
        (state-file write must never abort a successful apply).
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [string] $Id,
        [Parameter(Mandatory)] [string] $Target
    )

    if ([string]::IsNullOrWhiteSpace($Id))      { return }
    if ([string]::IsNullOrWhiteSpace($Target))  { return }
    if ($Target -eq 'Unknown')                  { return }

    $path = _Get-AscendoApplyMarksPath
    $dir = Split-Path -Parent $path
    try {
        if (-not (Test-Path -LiteralPath $dir)) {
            New-Item -ItemType Directory -Path $dir -Force | Out-Null
        }
    } catch {
        Write-Verbose ("Set-AscendoApplyMark: failed to create state dir {0}: {1}" -f $dir, $_)
        return
    }

    # Read existing state (best-effort).
    $data = [ordered]@{}
    if (Test-Path -LiteralPath $path) {
        try {
            $raw = Get-Content -LiteralPath $path -Raw -Encoding UTF8
            if (-not [string]::IsNullOrWhiteSpace($raw)) {
                $parsed = if ($PSVersionTable.PSVersion.Major -ge 6) {
                    $raw | ConvertFrom-Json -AsHashtable
                } else {
                    $raw | ConvertFrom-Json
                }
                if ($parsed -is [System.Collections.IDictionary]) {
                    foreach ($k in $parsed.Keys) { $data[$k] = $parsed[$k] }
                } elseif ($parsed) {
                    foreach ($p in $parsed.PSObject.Properties) {
                        $data[$p.Name] = $p.Value
                    }
                }
            }
        } catch {
            Write-Verbose ("Set-AscendoApplyMark: corrupt state at {0}, will overwrite: {1}" -f $path, $_)
            $data = [ordered]@{}
        }
    }

    $data[$Id] = [ordered]@{
        target    = [string]$Target
        appliedAt = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
    }

    try {
        $json = ConvertTo-Json $data -Depth 4
        # Atomic-ish write: write to .tmp + rename. PS doesn't expose a
        # transactional FS API; this minimises the window for a partial
        # file if the process is killed mid-write.
        $tmp = "$path.tmp"
        [System.IO.File]::WriteAllText($tmp, $json, [System.Text.UTF8Encoding]::new($false))
        Move-Item -LiteralPath $tmp -Destination $path -Force
    } catch {
        Write-Verbose ("Set-AscendoApplyMark: write failed for {0}: {1}" -f $path, $_)
    }
}

# -----------------------------------------------------------------------------
# Module export
# -----------------------------------------------------------------------------
Export-ModuleMember -Function @(
    'Initialize-WingetEnvironment'
    'Restore-WingetEnvironment'
    'Get-WingetUpgradable'
    'Get-WingetInstalled'
    'Convert-WingetExitCode'
    'Resolve-WingetId'
    'Get-AscendoApplyMark'
    'Set-AscendoApplyMark'
    'Read-WingetTabularOutput'
)

# =============================================================================
# TEST FIXTURES + MANUAL TRACE
# =============================================================================
# The following blocks are NOT executed at module load (they live in a here-
# string assigned to nothing). They are reference fixtures for a Windows-side
# reviewer. To run them interactively:
#
#   Import-Module .\AscendoWinget.psm1 -Force
#   $prev = Initialize-WingetEnvironment
#   $blob = @"
#   <paste fixture below>
#   "@
#   Read-WingetTabularOutput -RawOutput $blob
#   Restore-WingetEnvironment -PreviousEncoding $prev
#
# (Read-WingetTabularOutput is private; use `& (Get-Module AscendoWinget) {
# Read-WingetTabularOutput ... }` or temporarily export it for testing.)

$null = @'
================================================================================
FIXTURE 1 - Standard 5-column `winget upgrade` output
================================================================================
Name                                    Id                            Version       Available     Source
-------------------------------------------------------------------------------------------------------
Mozilla Firefox (x64 en-US)             Mozilla.Firefox               122.0         122.0.1       winget
Visual Studio Code                      Microsoft.VisualStudioCode    1.86.0        1.86.1        winget
PowerShell 7-x64                        Microsoft.PowerShell          7.4.0.0       7.4.1.0       winget
3 upgrades available.
'@

$null = @'
================================================================================
FIXTURE 2 - 4-column output (no Version) -- e.g. ARP-detected packages
================================================================================
Name                                    Id                            Available     Source
-------------------------------------------------------------------------------------------
Some ARP-only Tool                      ARP\Machine\X86\SomeTool      2.5.0         winget
1 package(s) have version numbers that cannot be determined.
'@

$null = @'
================================================================================
FIXTURE 3 - The bug case: embedded version in Name field (THE 2026-03-20 BUG)
================================================================================
Name                                                          Id                                 Version    Available    Source
--------------------------------------------------------------------------------------------------------------------------------
Microsoft Windows Desktop Runtime 10.0.4 (x64)                Microsoft.DotNet.DesktopRuntime.10 10.0.3     10.0.4       winget

NAIVE PARSE (`-split "\s{2,}"`) PRODUCES (WRONG):
  cols[0] = "Microsoft Windows Desktop Runtime 10.0.4 (x64)"
  cols[1] = "Microsoft.DotNet.DesktopRuntime.10"
  cols[2] = "10.0.3"
  cols[3] = "10.0.4"
  cols[4] = "winget"
  -- LOOKS OK. But examine the raw line: ONE space between "Runtime" and
  "10.0.4" is NOT >=2 spaces, so the whitespace splitter glues them. If the
  vendor instead writes "Runtime  10.0.4" (TWO spaces) the splitter creates
  a phantom column and Id slides into cols[2], Version into cols[3], etc.

POSITION PARSE (this module):
  Header line (with column index numbers as 10s/units below):
    Name                                                          Id                                 Version    Available    Source
    0         1         2         3         4         5         6         7         8         9
    0123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345

  Get-WingetColumnStarts returns Positions = [0, 62, 97, 108, 121]:
    Header.IndexOf("Name")      -> 0
    Header.IndexOf("Id")        -> 62
    Header.IndexOf("Version")   -> 97
    Header.IndexOf("Available") -> 108
    Header.IndexOf("Source")    -> 121

  Data row substring extraction at those positions:
    [0..61]    "Microsoft Windows Desktop Runtime 10.0.4 (x64)               "
                trimmed -> "Microsoft Windows Desktop Runtime 10.0.4 (x64)"     <- Name
    [62..96]   "Microsoft.DotNet.DesktopRuntime.10 "
                trimmed -> "Microsoft.DotNet.DesktopRuntime.10"                  <- Id
    [97..107]  "10.0.3     "
                trimmed -> "10.0.3"                                              <- Version
    [108..120] "10.0.4       "
                trimmed -> "10.0.4"                                              <- Available
    [121..]    "winget"                                                          <- Source

  RESULT: Id correctly extracted as "Microsoft.DotNet.DesktopRuntime.10",
  immune to the spaces inside Name.

  If Resolve-WingetId still ran (defensive), it would NOT match the regex
  because there is no trailing version suffix on the captured Id - returned
  unchanged. Good.

================================================================================
TRACE OF FIXTURE 3 THROUGH Read-WingetTabularOutput:

  iter 1: rawLine = ""                                           prev=""    inTable=false
  iter 2: rawLine = "Name ... Source"                            prev set
  iter 3: rawLine = "----..."                                    matches ^-{3,}
                    -> Get-WingetColumnStarts on prev returns
                       Layout='upgrade-5col', Positions=[0,62,97,108,121]
                    -> inTable = true
  iter 4: rawLine = "Microsoft Windows Desktop Runtime ..."      data row
                    name = "Microsoft Windows Desktop Runtime 10.0.4 (x64)"
                    id   = "Microsoft.DotNet.DesktopRuntime.10" (Resolve-WingetId no-op)
                    Layout='upgrade-5col' branch:
                      Version="10.0.3", Available="10.0.4", Source="winget"
                    -> emit pscustomobject

  Final result: 1 row, properties exactly as expected.

================================================================================
PS 5.1 COMPATIBILITY NOTES (avoided features):
  - No && / ||                  -- used `if (-not $X) { ... }` instead
  - No ?? / ?.                  -- used `if ($null -eq $x) { ... }` instead
  - No `-AsHashtable` on        -- not used here (private state is hashtable
    ConvertFrom-Json                 only via @{} literals)
  - No `[ordered]@{}` ranges    -- not needed; objects emitted via pscustomobject
  - No `${PSStyle}`             -- only Write-Verbose used
  - No `-Parallel` ForEach      -- single-threaded loop
  - No `using namespace`        -- fully qualified type names in casts
  - `[System.Collections.Generic.List[object]]` is fine on 5.1.
================================================================================
'@
