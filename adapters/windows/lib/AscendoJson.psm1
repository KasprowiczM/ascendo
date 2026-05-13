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
$script:VALID_ITEM_STATUS = @('success','up_to_date','failed','skipped','planned','partial','missing','triggered')
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

function Append-AscendoJsonLine {
    <#
    .SYNOPSIS
        Append a single object as one JSONL line to a sidecar bufdir file.
    .DESCRIPTION
        Internal helper for the bufdir-salvage path (mirror of Ubuntu
        adapter Sesja 56). Each call adds exactly one ConvertTo-Json
        line (Depth=100) to the target file. Best-effort: failures are
        silently swallowed so a flaky disk can't break the phase script.

        Caller usually passes $Sidecar['_bufdir'] as the bufdir; this
        helper handles the no-op case (empty / unset / nonexistent dir).
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [AllowEmptyString()] [string] $BufDir,
        [Parameter(Mandatory)] [string]    $FileName,
        [Parameter(Mandatory)] [hashtable] $Payload
    )

    if (-not $BufDir) { return }
    if (-not (Test-Path -LiteralPath $BufDir)) { return }

    try {
        $line = ($Payload | ConvertTo-Json -Depth 100 -Compress)
        $path = Join-Path -Path $BufDir -ChildPath $FileName
        # Append in UTF-8 NO BOM. .NET 4.5+ has AppendAllText which
        # honours encoding; PS 5.1 + 7.x both inherit that.
        $utf8NoBom = [System.Text.UTF8Encoding]::new($false)
        [System.IO.File]::AppendAllText($path, ($line + "`n"), $utf8NoBom)
    } catch {
        # Bufdir failures must NEVER break the phase. The Python side
        # will detect missing bufdir contents on salvage and proceed
        # with whatever survived (or skip salvage entirely).
        Write-Verbose ("Append-AscendoJsonLine: $_")
    }
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
    .PARAMETER BufDir
        Optional absolute path pre-allocated by the Python manager for
        sidecar salvage (mirror of Ubuntu adapter's Sesja 56 pattern).
        When set, every Add-Sidecar* call appends a JSONL line to a
        sibling file in this directory IN ADDITION to the in-memory
        ArrayList, so the Python side can reconstruct a partial sidecar
        if the script dies before Save-Sidecar fires. When unset, all
        bufdir paths are no-ops (backwards-compatible with phase
        scripts that haven't been updated yet).
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
        [Parameter()]          [string] $Invocation,
        [Parameter()]          [string] $BufDir
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
        # Salvage bufdir path (mirror of Ubuntu Sesja 56). Stashed on
        # the sidecar object under a leading-underscore key so it
        # survives all the way to Save-Sidecar but never lands in the
        # emitted JSON (we strip it before ConvertTo-Json).
        '_bufdir'      = $null
    }

    if ($PSBoundParameters.ContainsKey('BufDir') -and $BufDir) {
        try {
            if (-not (Test-Path -LiteralPath $BufDir)) {
                New-Item -ItemType Directory -Path $BufDir -Force | Out-Null
            }

            # Write meta.json with the stable identity block so the
            # Python salvage path can reconstruct run/host/tool even if
            # the script dies before adding a single item.
            $metaPayload = [ordered]@{
                'schema'      = $script:SCHEMA_ID
                'run'         = $run
                'host'        = $hostInfo
                'tool'        = $tool
                'phase'       = $Phase
                'category'    = $Category
                'started_at'  = $startedAt
            }
            $metaJson = $metaPayload | ConvertTo-Json -Depth 100
            $metaPath = Join-Path -Path $BufDir -ChildPath 'meta.json'
            Write-AscendoUtf8NoBom -Path $metaPath -Text $metaJson

            # Pre-create empty JSONL files so subsequent Add-* helpers
            # can blindly Add-Content without first checking existence.
            foreach ($name in @('items.jsonl', 'messages.jsonl', 'diags.jsonl')) {
                $jsonlPath = Join-Path -Path $BufDir -ChildPath $name
                if (-not (Test-Path -LiteralPath $jsonlPath)) {
                    Write-AscendoUtf8NoBom -Path $jsonlPath -Text ''
                }
            }

            $sidecar['_bufdir'] = $BufDir
        } catch {
            # Best-effort: a bufdir IO failure must not abort the run.
            # The Python side simply won't find anything to salvage if
            # the script later crashes — degrading gracefully.
            Write-Verbose ("New-Sidecar: bufdir setup failed for '$BufDir': $_")
        }
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

    # PowerShell auto-unwraps [Nullable[T]] params to T-or-$null at the
    # callee, so $ExitCode/$DurationMs are plain int-or-$null here -- the
    # .HasValue / .Value API doesn't apply. Treat $null as "not provided".
    if ($PSBoundParameters.ContainsKey('DurationMs') -and $null -ne $DurationMs -and [int]$DurationMs -lt 0) {
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
        'exit_code'        = $(if ($PSBoundParameters.ContainsKey('ExitCode')   -and $null -ne $ExitCode)   { [int]$ExitCode }   else { $null })
        'duration_ms'      = $(if ($PSBoundParameters.ContainsKey('DurationMs') -and $null -ne $DurationMs) { [int]$DurationMs } else { $null })
        'evidence'         = $evidenceBlock
        'rollback'         = $rollbackBlock
        'messages'         = $msgList
    }

    [void]$Sidecar['items'].Add($item)

    # Bufdir mirror (Sesja 56 salvage path): append to items.jsonl so
    # the Python side can rebuild the items list even if the script
    # dies before Save-Sidecar fires. No-op when -BufDir wasn't passed
    # to New-Sidecar.
    $bufdir = $null
    if ($Sidecar.Contains('_bufdir')) {
        $bufdir = [string]$Sidecar['_bufdir']
    }
    Append-AscendoJsonLine -BufDir $bufdir -FileName 'items.jsonl' -Payload $item

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

    # Bufdir mirror (Sesja 56 salvage path): append to messages.jsonl
    # so the Python side can rebuild the phase-level messages list
    # even if the script dies before Save-Sidecar fires. No-op when
    # -BufDir wasn't passed to New-Sidecar.
    $bufdir = $null
    if ($Sidecar.Contains('_bufdir')) {
        $bufdir = [string]$Sidecar['_bufdir']
    }
    $msgPayload = [ordered]@{
        'level'     = $entry['level']
        'text'      = $entry['text']
        'timestamp' = $entry['timestamp']
    }
    Append-AscendoJsonLine -BufDir $bufdir -FileName 'messages.jsonl' -Payload $msgPayload
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

    # Strip the internal _bufdir key before serialising — it's a
    # Python-side salvage breadcrumb, not part of the v1 contract.
    $bufdirToCleanup = $null
    $serialisable = $Sidecar
    if ($Sidecar.Contains('_bufdir')) {
        $bufdirToCleanup = [string]$Sidecar['_bufdir']
        # Build a sibling ordered hashtable without the _bufdir key so
        # ConvertTo-Json emits the canonical shape. ConvertTo-Json
        # doesn't have a per-key exclude flag, so we copy in order.
        $serialisable = [ordered]@{}
        foreach ($k in $Sidecar.Keys) {
            if ($k -eq '_bufdir') { continue }
            $serialisable[$k] = $Sidecar[$k]
        }
    }

    $json = $serialisable | ConvertTo-Json -Depth 100

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

    # Canonical sidecar landed on disk — remove the salvage bufdir
    # since it's strictly redundant now. ASCENDO_KEEP_BUFDIR=1 keeps
    # it for forensic debugging.
    if ($bufdirToCleanup -and (Test-Path -LiteralPath $bufdirToCleanup)) {
        $keep = $env:ASCENDO_KEEP_BUFDIR
        if (-not ($keep -eq '1' -or $keep -eq 'true' -or $keep -eq 'True')) {
            try {
                Remove-Item -LiteralPath $bufdirToCleanup -Recurse -Force `
                    -ErrorAction SilentlyContinue
            } catch {
                # Best-effort cleanup; Python side will mop up.
            }
        }
    }

    return (Get-Item -LiteralPath $finalPath)
}

# -----------------------------------------------------------------------------
# Stream-log helpers (parity with macOS adapters/macos/lib/ascendo_json.sh)
# -----------------------------------------------------------------------------
# When the orchestrator sets $env:ASCENDO_STREAM_LOG, every apply script can
# mirror its mutating output through these helpers so the dashboard's SSE
# endpoint streams every line live to Run Center. When the env var is unset
# (CLI runs), these are no-ops.

function Write-AscendoStreamLine {
    <#
    .SYNOPSIS
        Append a single line (or block of lines) to $env:ASCENDO_STREAM_LOG
        when set. No-op otherwise. Best-effort — failures (disk full,
        rotated log, locked file) are swallowed.

    .PARAMETER Text
        Multi-line string to append. Each call adds a trailing newline if
        the input doesn't already end with one.

    .EXAMPLE
        Write-AscendoStreamLine -Text ">>> winget upgrade $id"
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [AllowEmptyString()] [string] $Text
    )
    $log = $env:ASCENDO_STREAM_LOG
    if (-not $log) { return }
    try {
        $payload = if ($Text.EndsWith("`n")) { $Text } else { $Text + "`n" }
        # Append-only; tolerate failures so the apply pipeline never breaks
        # because of a stream-log issue.
        Add-Content -LiteralPath $log -Value $payload `
            -Encoding utf8 -ErrorAction SilentlyContinue
    } catch {
        # Swallow — stream-log is best-effort.
    }
}

function Write-AscendoStreamFile {
    <#
    .SYNOPSIS
        Append the contents of a file to $env:ASCENDO_STREAM_LOG. Used by
        apply scripts that capture child-process stdout/stderr to tempfiles
        and want to mirror the captures into Run Center's live feed.
    .PARAMETER Path
        Path to the file whose contents should be appended.
    #>
    [CmdletBinding()]
    param([Parameter(Mandatory)] [string] $Path)
    $log = $env:ASCENDO_STREAM_LOG
    if (-not $log) { return }
    if (-not (Test-Path -LiteralPath $Path)) { return }
    try {
        $body = Get-Content -LiteralPath $Path -Raw -ErrorAction SilentlyContinue
        if ($body) {
            Add-Content -LiteralPath $log -Value $body `
                -Encoding utf8 -ErrorAction SilentlyContinue
        }
    } catch {
        # Best-effort.
    }
}

# -----------------------------------------------------------------------------
# Watchdog heartbeat
# -----------------------------------------------------------------------------
#
# Mirror of the Ubuntu adapter's Sesja 55 heartbeat (lib/json.sh +
# lib/orchestrator.sh background process emitting `>>> still running (Ns)`
# every 10s during silent phases). The dashboard's SSE log_line stream
# forwards Console.Error.WriteLine output to the SPA's Run Center
# terminal box, so this keeps the UI visibly alive during long-running
# `winget upgrade`, `Install-WindowsUpdate`, or msi uninstall calls that
# would otherwise produce zero output for minutes at a time.
#
# Implementation: PowerShell runspace (NOT a child process). Runspaces
# share stdio with the parent so [Console]::Error.WriteLine lines flow
# straight through to the captured SSE stream — a child pwsh.exe process
# would have its own console handles and we'd lose the lines.

function Start-AscendoHeartbeat {
    <#
    .SYNOPSIS
        Start a background heartbeat that prints `>>> still running (Ns)` to
        stderr at a fixed interval.

    .DESCRIPTION
        Spawns a PowerShell runspace (NOT a child process — runspaces share
        stdio with the parent so the SSE stream picks up the lines). Returns
        a token (PSCustomObject) you pass to Stop-AscendoHeartbeat. Safe to
        call from any phase script before a long-running invocation. Pair
        with Stop-AscendoHeartbeat in a try/finally so the runspace cannot
        leak on exception.

    .PARAMETER IntervalSeconds
        Seconds between heartbeats. Default 10. Sleep is chunked internally
        (200ms ticks) so Stop-AscendoHeartbeat returns promptly.

    .PARAMETER Label
        Optional context label, e.g. "winget upgrade (3 package(s))".
        Appended to the heartbeat line so operators know what's running.

    .EXAMPLE
        $hb = Start-AscendoHeartbeat -IntervalSeconds 10 -Label "winget upgrade"
        try {
            foreach ($pkg in $candidates) { winget upgrade --id $pkg.Id }
        } finally {
            Stop-AscendoHeartbeat $hb
        }
    #>
    [CmdletBinding()]
    [OutputType([pscustomobject])]
    param(
        [int]    $IntervalSeconds = 10,
        [string] $Label = ''
    )

    if ($IntervalSeconds -lt 1) { $IntervalSeconds = 1 }

    $stopFlag = [ref]$false
    $start    = [datetime]::UtcNow
    # Capture the stream-log path on the parent's env so the runspace
    # (which gets its own session-state) can mirror heartbeat lines into
    # the file the dashboard's SSE consumer tails. Without this, the
    # heartbeat went only to [Console]::Error which subprocess.run
    # captures but doesn't forward, so the SPA Run Center showed zero
    # liveness during long Install-WindowsUpdate / winget upgrade hangs.
    $streamLog = $env:ASCENDO_STREAM_LOG

    $script = {
        param($intervalSeconds, $label, $start, $stopFlag, $streamLog)
        while (-not $stopFlag.Value) {
            $elapsed = [int]([datetime]::UtcNow - $start).TotalSeconds
            if ($label) {
                $msg = ">>> still running ${elapsed}s ($label)"
            } else {
                $msg = ">>> still running ${elapsed}s"
            }
            try { [Console]::Error.WriteLine($msg) } catch { }
            if ($streamLog) {
                try {
                    # Direct append from the runspace — Write-AscendoStreamLine
                    # isn't necessarily imported into the new session, and
                    # Add-Content with -ErrorAction SilentlyContinue is safe
                    # to call concurrently (file is opened/closed per
                    # append; collisions just lose a tick which is fine).
                    Add-Content -LiteralPath $streamLog -Value ($msg + "`n") `
                        -Encoding utf8 -ErrorAction SilentlyContinue
                } catch { }
            }
            # Sleep in 200ms chunks so Stop-AscendoHeartbeat returns
            # promptly when StopFlag is flipped.
            $remainingMs = $intervalSeconds * 1000
            while ($remainingMs -gt 0 -and -not $stopFlag.Value) {
                Start-Sleep -Milliseconds 200
                $remainingMs -= 200
            }
        }
    }

    $rs = $null
    $ps = $null
    $handle = $null
    try {
        $rs = [runspacefactory]::CreateRunspace()
        $rs.Open()
        $ps = [powershell]::Create().
            AddScript($script).
            AddArgument($IntervalSeconds).
            AddArgument($Label).
            AddArgument($start).
            AddArgument($stopFlag).
            AddArgument($streamLog)
        $ps.Runspace = $rs
        $handle = $ps.BeginInvoke()
    } catch {
        # If we can't spin up the runspace, return a benign null-shaped
        # token. Stop-AscendoHeartbeat is null-safe so callers don't need
        # to special-case the failure.
        try { if ($ps -ne $null) { $ps.Dispose() } } catch { }
        try { if ($rs -ne $null) { $rs.Dispose() } } catch { }
        return $null
    }

    return [pscustomobject]@{
        Runspace   = $rs
        PowerShell = $ps
        Handle     = $handle
        StopFlag   = $stopFlag
        Label      = $Label
        StartTime  = $start
    }
}

function Stop-AscendoHeartbeat {
    <#
    .SYNOPSIS
        Stop a heartbeat started by Start-AscendoHeartbeat. Safe to call
        with $null (no-op) — callers can stop unconditionally inside
        finally blocks without null-guarding.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory=$false, Position=0)]
        $Heartbeat
    )

    if ($null -eq $Heartbeat) { return }

    try { $Heartbeat.StopFlag.Value = $true } catch { }

    # Give the runspace up to ~1s to drain (its loop wakes every 200ms).
    try {
        if ($Heartbeat.PowerShell -and $Heartbeat.Handle) {
            [void]$Heartbeat.PowerShell.EndInvoke($Heartbeat.Handle)
        }
    } catch { }

    try { if ($Heartbeat.PowerShell) { $Heartbeat.PowerShell.Dispose() } } catch { }
    try { if ($Heartbeat.Runspace)   { $Heartbeat.Runspace.Dispose()   } } catch { }
}

# -----------------------------------------------------------------------------
# Exports
# -----------------------------------------------------------------------------

Export-ModuleMember -Function @(
    'New-Sidecar',
    'Add-SidecarItem',
    'Add-SidecarMessage',
    'Save-Sidecar',
    'Get-AscendoHostInfo',
    'Write-AscendoStreamLine',
    'Write-AscendoStreamFile',
    'Start-AscendoHeartbeat',
    'Stop-AscendoHeartbeat'
)
