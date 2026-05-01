# =============================================================================
# AscendoJson.psm1 - JSON v1 sidecar emitter for native Windows scripts
# =============================================================================
#
# Companion to lib/_json_emit.py (Linux/macOS bash equivalent).
# Emits sidecars conforming to the ascendo/v1 schema defined in
# core/ascendo/models/sidecar.py.
#
# Usage:
#   Import-Module .\adapters\windows\lib\AscendoJson.psm1
#   $side = New-Sidecar -RunId $rid -Trigger cli -ProfileName full ...
#   Add-SidecarItem -Sidecar $side -Id 'Microsoft.PowerShell' ...
#   Save-Sidecar -Sidecar $side -OutputDir $env:TEMP\ascendo-runs
#
# Compatibility: PowerShell 5.1 + 7.x. No external module dependencies.
# =============================================================================

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# -----------------------------------------------------------------------------
# Module-private constants
# -----------------------------------------------------------------------------

$script:SCHEMA_ID = 'ascendo/v1'

$script:VALID_TRIGGERS    = @('cli','scheduler','dashboard','plugin','unknown')
$script:VALID_PHASES      = @('check','plan','apply','verify','cleanup')
$script:VALID_PHASE_STATUS= @('success','partial','failed','skipped')
$script:VALID_ITEM_STATUS = @('success','up_to_date','failed','skipped','planned','partial')
$script:VALID_MSG_LEVELS  = @('debug','info','warn','error')
$script:VALID_ELEVATION   = @('none','sudo','pkexec','doas','uac','launchd_root','unknown')
$script:VALID_SOURCE_TYPE = @(
    'apt','snap','flatpak','brew_formula','brew_cask','winget','msstore',
    'appx','registry_arp','windows_update','mac_app_store','npm','pip','pipx','web',
    'plugin','unknown'
)

# -----------------------------------------------------------------------------
# Private helpers
# -----------------------------------------------------------------------------

function Get-AscendoUtcNowIso {
    <#
    .SYNOPSIS
        UTC ISO-8601 timestamp with microsecond precision and 'Z' suffix.
    .DESCRIPTION
        PowerShell's "o" format yields 7 fractional digits (100ns ticks).
        Pydantic + the Python emitter prefer microsecond precision (6 digits)
        with a 'Z' suffix.
    #>
    [CmdletBinding()]
    param()

    $now = [DateTime]::UtcNow
    return $now.ToString('yyyy-MM-ddTHH:mm:ss.ffffffZ', [System.Globalization.CultureInfo]::InvariantCulture)
}

function Test-AscendoEnumValue {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [string]   $Value,
        [Parameter(Mandatory)] [string[]] $Allowed,
        [Parameter(Mandatory)] [string]   $FieldName
    )
    if ($Allowed -notcontains $Value) {
        $allowedStr = ($Allowed -join ', ')
        throw "Invalid $FieldName '$Value'. Must be one of: $allowedStr"
    }
}

function Get-AscendoArch {
    <#
    .SYNOPSIS
        Map $env:PROCESSOR_ARCHITECTURE to the lowercase canonical names
        used in the schema.
    #>
    [CmdletBinding()]
    param()

    $raw = $env:PROCESSOR_ARCHITECTURE
    if (-not $raw) { return 'unknown' }
    switch ($raw.ToUpperInvariant()) {
        'AMD64' { return 'x86_64' }
        'ARM64' { return 'arm64' }
        'X86'   { return 'i386' }
        'IA64'  { return 'ia64' }
        default { return $raw.ToLowerInvariant() }
    }
}

function Get-AscendoOsVersion {
    <#
    .SYNOPSIS
        Compose a free-form Windows version string like "11 Pro 26200".
    .DESCRIPTION
        Pulls the marketing edition from Win32_OperatingSystem.Caption
        (e.g. "Microsoft Windows 11 Pro") and the build number from the
        environment. Strips the "Microsoft Windows " prefix and appends
        the build to match the example in the Pydantic model.
    #>
    [CmdletBinding()]
    param()

    $caption = $null
    try {
        $os = Get-CimInstance -ClassName Win32_OperatingSystem -ErrorAction Stop
        if ($os -and $os.Caption) { $caption = [string]$os.Caption }
    } catch {
        Write-Verbose "Get-CimInstance Win32_OperatingSystem failed: $_"
    }

    $build = [System.Environment]::OSVersion.Version.Build

    if ($caption) {
        $edition = $caption -replace '^Microsoft\s+Windows\s+', ''
        $edition = $edition.Trim()
        if ($edition) {
            return ('{0} {1}' -f $edition, $build)
        }
    }
    return ('{0}.{1} {2}' -f
        [System.Environment]::OSVersion.Version.Major,
        [System.Environment]::OSVersion.Version.Minor,
        $build)
}

function Get-AscendoIsElevated {
    <#
    .SYNOPSIS
        True if the current process is running as Administrator.
    #>
    [CmdletBinding()]
    param()

    try {
        $identity  = [System.Security.Principal.WindowsIdentity]::GetCurrent()
        $principal = [System.Security.Principal.WindowsPrincipal]::new($identity)
        return [bool]$principal.IsInRole(
            [System.Security.Principal.WindowsBuiltInRole]::Administrator
        )
    } catch {
        Write-Verbose "Elevation check failed: $_"
        return $false
    }
}

function Get-AscendoLocale {
    <#
    .SYNOPSIS
        BCP-47 locale tag (e.g. 'pl-PL', 'en-US'). Returns $null if unknown.
    #>
    [CmdletBinding()]
    param()

    try {
        $name = (Get-Culture).Name
        if ($name) { return $name } else { return $null }
    } catch {
        return $null
    }
}

function Get-AscendoHostInfo {
    <#
    .SYNOPSIS
        Build a HostInfo hashtable for the current machine.
    .DESCRIPTION
        Returns a hashtable matching the HostInfo Pydantic model:
            hostname, os, os_version, arch, user, is_elevated,
            elevation_method, locale.
        All keys are snake_case and serialise directly to JSON.
    #>
    [CmdletBinding()]
    param()

    $isElevated = Get-AscendoIsElevated
    $hostname   = $env:COMPUTERNAME
    if (-not $hostname) {
        try { $hostname = [System.Net.Dns]::GetHostName() } catch { $hostname = 'unknown' }
    }
    $user = $env:USERNAME
    if (-not $user) { $user = 'unknown' }

    return [ordered]@{
        'hostname'         = $hostname
        'os'               = 'windows'
        'os_version'       = Get-AscendoOsVersion
        'arch'             = Get-AscendoArch
        'user'             = $user
        'is_elevated'      = [bool]$isElevated
        'elevation_method' = $(if ($isElevated) { 'uac' } else { 'none' })
        'locale'           = Get-AscendoLocale
    }
}

function Get-AscendoSafeFilenameFragment {
    <#
    .SYNOPSIS
        Sanitise a string for use in a filename (replaces FS-unsafe chars).
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [string] $Value
    )
    $cleaned = $Value -replace '/', '_'
    $cleaned = $cleaned -replace '[\\:*?"<>|]', '_'
    return $cleaned
}

function Write-AscendoUtf8NoBom {
    <#
    .SYNOPSIS
        Write text to a file as UTF-8 without a BOM.
    .DESCRIPTION
        PowerShell 5.1's `Out-File -Encoding utf8` writes a BOM. We bypass
        cmdlets and use the .NET API with UTF8Encoding($false) to guarantee
        no BOM on both 5.1 and 7.x.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [string] $Path,
        [Parameter(Mandatory)] [string] $Text
    )
    $utf8NoBom = [System.Text.UTF8Encoding]::new($false)
    [System.IO.File]::WriteAllText($Path, $Text, $utf8NoBom)
}

# -----------------------------------------------------------------------------
# Public API
# -----------------------------------------------------------------------------

function New-Sidecar {
    <#
    .SYNOPSIS
        Initialize an in-memory sidecar object.
    .DESCRIPTION
        Returns a hashtable that can be passed to Add-SidecarItem,
        Add-SidecarMessage, and Save-Sidecar. Captures host info at
        the moment of the call and stamps started_at = UTC now.
    .PARAMETER RunId
        UUID string from the orchestrator. Must round-trip through [Guid].
    .PARAMETER Trigger
        'cli' | 'scheduler' | 'dashboard' | 'plugin' | 'unknown'.
    .PARAMETER ProfileName
        Profile slug (e.g. 'full', 'safe', 'quick'). Renamed from -Profile
        because PowerShell's CommonParameters reserve some neighbouring
        names; ProfileName avoids any conflict in PS 5.1 + 7.x.
    .PARAMETER DryRun
        Boolean. Default $false.
    .PARAMETER Phase
        'check' | 'plan' | 'apply' | 'verify' | 'cleanup'.
    .PARAMETER Category
        SourceType slug (e.g. 'winget', 'msstore', 'registry_arp').
    .PARAMETER ToolName
        Tool slug (e.g. 'winget'). 1-64 chars.
    .PARAMETER ToolVersion
        Self-reported version (e.g. 'v1.28.240').
    .PARAMETER ToolBinaryPath
        Optional absolute path to the tool binary.
    .PARAMETER Invocation
        Optional original command-line string for audit.
    .OUTPUTS
        [Hashtable] - mutable state object passed to subsequent Add-* calls.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [string] $RunId,
        [Parameter(Mandatory)] [string] $Trigger,
        [Parameter(Mandatory)] [string] $ProfileName,
        [Parameter()]          [bool]   $DryRun = $false,
        [Parameter(Mandatory)] [string] $Phase,
        [Parameter(Mandatory)] [string] $Category,
        [Parameter(Mandatory)] [string] $ToolName,
        [Parameter(Mandatory)] [string] $ToolVersion,
        [Parameter()]          [string] $ToolBinaryPath,
        [Parameter()]          [string] $Invocation
    )

    Test-AscendoEnumValue -Value $Trigger  -Allowed $script:VALID_TRIGGERS    -FieldName 'Trigger'
    Test-AscendoEnumValue -Value $Phase    -Allowed $script:VALID_PHASES      -FieldName 'Phase'
    Test-AscendoEnumValue -Value $Category -Allowed $script:VALID_SOURCE_TYPE -FieldName 'Category'

    $parsedGuid = [Guid]::Empty
    if (-not [Guid]::TryParse($RunId, [ref]$parsedGuid)) {
        throw "Invalid RunId '$RunId' (not a UUID)."
    }
    $runIdNorm = $parsedGuid.ToString()

    if ($ProfileName -notmatch '^[a-zA-Z0-9_\-]+$') {
        throw "Invalid ProfileName '$ProfileName' (must match [A-Za-z0-9_-]+)."
    }
    if ($ProfileName.Length -gt 64) {
        throw "ProfileName too long (max 64 chars)."
    }

    $toolNameTrim = $ToolName.Trim()
    if (-not $toolNameTrim) { throw 'ToolName is empty after trimming.' }
    if ($toolNameTrim.Length -gt 64) { throw "ToolName too long (max 64 chars)." }

    $startedAt = Get-AscendoUtcNowIso
    $hostInfo  = Get-AscendoHostInfo

    $tool = [ordered]@{
        'name'        = $toolNameTrim
        'version'     = $ToolVersion
        'binary_path' = $(if ($PSBoundParameters.ContainsKey('ToolBinaryPath') -and $ToolBinaryPath) { $ToolBinaryPath } else { $null })
    }

    $run = [ordered]@{
        'id'          = $runIdNorm
        'trigger'     = $Trigger
        'profile'     = $ProfileName
        'dry_run'     = [bool]$DryRun
        'started_at'  = $startedAt
        'finished_at' = $null
        'invocation'  = $(if ($PSBoundParameters.ContainsKey('Invocation') -and $Invocation) { $Invocation } else { $null })
    }

    $sidecar = [ordered]@{
        'schema'       = $script:SCHEMA_ID
        'run'          = $run
        'host'         = $hostInfo
        'tool'         = $tool
        'phase'        = $Phase
        'category'     = $Category
        'started_at'   = $startedAt
        'finished_at'  = $null
        'status'       = $null
        'items'        = [System.Collections.ArrayList]::new()
        'summary'      = $null
        'messages'     = [System.Collections.ArrayList]::new()
    }
    return $sidecar
}

function Add-SidecarItem {
    <#
    .SYNOPSIS
        Append a per-package item record to the sidecar.
    .PARAMETER Sidecar
        The hashtable from New-Sidecar.
    .PARAMETER Id
        Package id (e.g. 'Microsoft.PowerShell').
    .PARAMETER Name
        Display name.
    .PARAMETER Category
        SourceType slug for the item's category.
    .PARAMETER SourceType
        ItemSource type slug.
    .PARAMETER SourceFeed
        Optional feed name.
    .PARAMETER SourceUrl
        Optional URL associated with this source.
    .PARAMETER CurrentVersion
        Optional version installed before phase.
    .PARAMETER TargetVersion
        Optional version planned.
    .PARAMETER ResolvedVersion
        Optional version actually installed.
    .PARAMETER Status
        'success' | 'up_to_date' | 'failed' | 'skipped' | 'planned' | 'partial'.
    .PARAMETER ExitCode
        Optional integer.
    .PARAMETER DurationMs
        Optional non-negative integer.
    .PARAMETER Evidence
        Optional [Hashtable]: appx_version / registry_version / dpkg_version /
        binary_version / binary_path / binary_sha256 / note.
    .PARAMETER Rollback
        Optional [Hashtable]: available (bool) / method / snapshot_id /
        instructions_path.
    .PARAMETER Messages
        Optional array of [Hashtable] with level + text (and optional timestamp).
    .OUTPUTS
        [Hashtable] - the item that was added (for caller convenience).
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [hashtable] $Sidecar,
        [Parameter(Mandatory)] [string]    $Id,
        [Parameter(Mandatory)] [string]    $Name,
        [Parameter(Mandatory)] [string]    $Category,
        [Parameter(Mandatory)] [string]    $SourceType,
        [Parameter()]          [string]    $SourceFeed,
        [Parameter()]          [string]    $SourceUrl,
        [Parameter()]          [string]    $CurrentVersion,
        [Parameter()]          [string]    $TargetVersion,
        [Parameter()]          [string]    $ResolvedVersion,
        [Parameter(Mandatory)] [string]    $Status,
        [Parameter()]          [Nullable[int]] $ExitCode,
        [Parameter()]          [Nullable[int]] $DurationMs,
        [Parameter()]          [hashtable] $Evidence,
        [Parameter()]          [hashtable] $Rollback,
        [Parameter()]          [array]     $Messages
    )

    Test-AscendoEnumValue -Value $Category   -Allowed $script:VALID_SOURCE_TYPE -FieldName 'item.Category'
    Test-AscendoEnumValue -Value $SourceType -Allowed $script:VALID_SOURCE_TYPE -FieldName 'item.SourceType'
    Test-AscendoEnumValue -Value $Status     -Allowed $script:VALID_ITEM_STATUS -FieldName 'item.Status'

    if ($PSBoundParameters.ContainsKey('DurationMs') -and $DurationMs.HasValue -and $DurationMs.Value -lt 0) {
        throw "DurationMs must be non-negative."
    }

    $source = [ordered]@{
        'type' = $SourceType
        'feed' = $(if ($PSBoundParameters.ContainsKey('SourceFeed') -and $SourceFeed) { $SourceFeed } else { $null })
        'url'  = $(if ($PSBoundParameters.ContainsKey('SourceUrl')  -and $SourceUrl)  { $SourceUrl }  else { $null })
    }

    $evidenceBlock = $null
    if ($Evidence) {
        $evidenceBlock = [ordered]@{
            'appx_version'     = $(if ($Evidence.Contains('appx_version'))     { $Evidence['appx_version'] }     else { $null })
            'registry_version' = $(if ($Evidence.Contains('registry_version')) { $Evidence['registry_version'] } else { $null })
            'dpkg_version'     = $(if ($Evidence.Contains('dpkg_version'))     { $Evidence['dpkg_version'] }     else { $null })
            'binary_version'   = $(if ($Evidence.Contains('binary_version'))   { $Evidence['binary_version'] }   else { $null })
            'binary_path'      = $(if ($Evidence.Contains('binary_path'))      { $Evidence['binary_path'] }      else { $null })
            'binary_sha256'    = $(if ($Evidence.Contains('binary_sha256'))    { $Evidence['binary_sha256'] }    else { $null })
            'note'             = $(if ($Evidence.Contains('note'))             { $Evidence['note'] }             else { $null })
        }
    }

    $rollbackBlock = $null
    if ($Rollback) {
        if (-not $Rollback.Contains('available')) {
            throw "Rollback hashtable must contain an 'available' boolean key."
        }
        $rollbackBlock = [ordered]@{
            'available'         = [bool]$Rollback['available']
            'method'            = $(if ($Rollback.Contains('method'))            { $Rollback['method'] }            else { $null })
            'snapshot_id'       = $(if ($Rollback.Contains('snapshot_id'))       { $Rollback['snapshot_id'] }       else { $null })
            'instructions_path' = $(if ($Rollback.Contains('instructions_path')) { $Rollback['instructions_path'] } else { $null })
        }
    }

    $msgList = [System.Collections.ArrayList]::new()
    if ($Messages) {
        foreach ($m in $Messages) {
            if (-not ($m -is [hashtable])) {
                throw "Each entry in -Messages must be a hashtable with 'level' and 'text'."
            }
            if (-not $m.Contains('level') -or -not $m.Contains('text')) {
                throw "Each message hashtable requires 'level' and 'text' keys."
            }
            Test-AscendoEnumValue -Value ([string]$m['level']) -Allowed $script:VALID_MSG_LEVELS -FieldName 'item.message.level'
            $entry = [ordered]@{
                'level'     = [string]$m['level']
                'text'      = [string]$m['text']
                'timestamp' = $(if ($m.Contains('timestamp')) { $m['timestamp'] } else { $null })
            }
            [void]$msgList.Add($entry)
        }
    }

    $item = [ordered]@{
        'id'               = $Id
        'name'             = $Name
        'category'         = $Category
        'source'           = $source
        'current_version'  = $(if ($PSBoundParameters.ContainsKey('CurrentVersion')  -and $CurrentVersion)  { $CurrentVersion }  else { $null })
        'target_version'   = $(if ($PSBoundParameters.ContainsKey('TargetVersion')   -and $TargetVersion)   { $TargetVersion }   else { $null })
        'resolved_version' = $(if ($PSBoundParameters.ContainsKey('ResolvedVersion') -and $ResolvedVersion) { $ResolvedVersion } else { $null })
        'status'           = $Status
        'exit_code'        = $(if ($PSBoundParameters.ContainsKey('ExitCode')   -and $ExitCode.HasValue)   { [int]$ExitCode.Value }   else { $null })
        'duration_ms'      = $(if ($PSBoundParameters.ContainsKey('DurationMs') -and $DurationMs.HasValue) { [int]$DurationMs.Value } else { $null })
        'evidence'         = $evidenceBlock
        'rollback'         = $rollbackBlock
        'messages'         = $msgList
    }

    [void]$Sidecar['items'].Add($item)
    return $item
}

function Add-SidecarMessage {
    <#
    .SYNOPSIS
        Add a phase-level message (not bound to a specific item).
    .PARAMETER Sidecar
        The hashtable from New-Sidecar.
    .PARAMETER Level
        'debug' | 'info' | 'warn' | 'error'.
    .PARAMETER Text
        Message text (max 4096 chars per schema).
    .PARAMETER Timestamp
        Optional ISO-8601 timestamp string.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [hashtable] $Sidecar,
        [Parameter(Mandatory)] [string]    $Level,
        [Parameter(Mandatory)] [string]    $Text,
        [Parameter()]          [string]    $Timestamp
    )

    Test-AscendoEnumValue -Value $Level -Allowed $script:VALID_MSG_LEVELS -FieldName 'message.Level'

    if (-not $Text -or $Text.Length -lt 1) {
        throw "message.Text must be non-empty."
    }
    if ($Text.Length -gt 4096) {
        throw "message.Text exceeds 4096 chars."
    }

    $entry = [ordered]@{
        'level'     = $Level
        'text'      = $Text
        'timestamp' = $(if ($PSBoundParameters.ContainsKey('Timestamp') -and $Timestamp) { $Timestamp } else { $null })
    }
    [void]$Sidecar['messages'].Add($entry)
}

function Save-Sidecar {
    <#
    .SYNOPSIS
        Compute summary, set finished_at + status, write atomically to disk.
    .DESCRIPTION
        File goes to "$OutputDir\$RunId\<phase>__<category>.json".
        Phase + category are sanitised for filesystem safety.

        Atomic write: writes to a temp file in the same directory, then
        Move-Item -Force to the final name. UTF-8 NO BOM via .NET API.

        Phase status rules:
          - failed > 0 + success == 0 -> 'failed'
          - failed > 0 + success > 0  -> 'partial'
          - skipped == total (total>0)-> 'skipped'
          - else                      -> 'success'

        finished_at = now UTC ISO-8601.
    .PARAMETER Sidecar
        The hashtable from New-Sidecar (mutated in place).
    .PARAMETER OutputDir
        Base directory. Run subdirectory is created automatically.
    .OUTPUTS
        [System.IO.FileInfo] - the written file.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [hashtable] $Sidecar,
        [Parameter(Mandatory)] [string]    $OutputDir
    )

    $items = @($Sidecar['items'])
    $total      = $items.Count
    $successCnt = @($items | Where-Object { $_['status'] -eq 'success' }).Count
    $upToDate   = @($items | Where-Object { $_['status'] -eq 'up_to_date' }).Count
    $failedCnt  = @($items | Where-Object { $_['status'] -eq 'failed' }).Count
    $skippedCnt = @($items | Where-Object { $_['status'] -eq 'skipped' }).Count
    $plannedCnt = @($items | Where-Object { $_['status'] -eq 'planned' }).Count
    $partialCnt = @($items | Where-Object { $_['status'] -eq 'partial' }).Count

    $exitCode = $(if ($failedCnt -gt 0) { 1 } else { 0 })

    $durations = $items |
        ForEach-Object { $_['duration_ms'] } |
        Where-Object   { $_ -ne $null }
    $durationTotal = $null
    if ($durations) {
        $sum = 0
        foreach ($d in $durations) { $sum += [int]$d }
        $durationTotal = $sum
    }

    $Sidecar['summary'] = [ordered]@{
        'total'       = $total
        'success'     = $successCnt
        'up_to_date'  = $upToDate
        'failed'      = $failedCnt
        'skipped'     = $skippedCnt
        'planned'     = $plannedCnt
        'partial'     = $partialCnt
        'duration_ms' = $durationTotal
        'exit_code'   = $exitCode
    }

    $phaseStatus =
        if     ($failedCnt -gt 0 -and $successCnt -eq 0) { 'failed' }
        elseif ($failedCnt -gt 0 -and $successCnt -gt 0) { 'partial' }
        elseif ($total -gt 0 -and $skippedCnt -eq $total){ 'skipped' }
        else                                             { 'success' }

    $Sidecar['status']      = $phaseStatus
    $Sidecar['finished_at'] = Get-AscendoUtcNowIso

    $runId       = [string]$Sidecar['run']['id']
    $phaseSlug   = Get-AscendoSafeFilenameFragment -Value ([string]$Sidecar['phase'])
    $catSlug     = Get-AscendoSafeFilenameFragment -Value ([string]$Sidecar['category'])
    $runDir      = Join-Path -Path $OutputDir -ChildPath $runId
    if (-not (Test-Path -LiteralPath $runDir)) {
        New-Item -ItemType Directory -Path $runDir -Force | Out-Null
    }

    $finalName = ('{0}__{1}.json' -f $phaseSlug, $catSlug)
    $finalPath = Join-Path -Path $runDir -ChildPath $finalName

    $json = $Sidecar | ConvertTo-Json -Depth 100

    $tmpName = ('.{0}__{1}.{2}.tmp' -f $phaseSlug, $catSlug, [System.IO.Path]::GetRandomFileName())
    $tmpPath = Join-Path -Path $runDir -ChildPath $tmpName
    try {
        Write-AscendoUtf8NoBom -Path $tmpPath -Text $json
        Move-Item -LiteralPath $tmpPath -Destination $finalPath -Force
    } catch {
        if (Test-Path -LiteralPath $tmpPath) {
            Remove-Item -LiteralPath $tmpPath -Force -ErrorAction SilentlyContinue
        }
        throw
    }

    return (Get-Item -LiteralPath $finalPath)
}

# -----------------------------------------------------------------------------
# Exports
# -----------------------------------------------------------------------------

Export-ModuleMember -Function @(
    'New-Sidecar',
    'Add-SidecarItem',
    'Add-SidecarMessage',
    'Save-Sidecar',
    'Get-AscendoHostInfo'
)
