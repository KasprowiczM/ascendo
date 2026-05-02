<#
.SYNOPSIS
  VSS snapshot driver. Two actions:
    -Action create  : create a System Restore checkpoint (preferred — it
                      bundles a VSS shadow copy on each protected volume
                      and is the closest Windows analogue of timeshift).
                      Persists the operator-provided label + notes into a
                      JSON registry under %PROGRAMDATA%\Ascendo\snapshots.
    -Action list    : enumerate VSS shadow copies via Get-CimInstance and
                      merge in any persisted label/notes from the registry.

  Output: a single JSON file at -OutputPath. For create: a single object.
  For list: a JSON array.

.NOTES
  Restore is intentionally NOT implemented here. System Restore rollback
  is a destructive, reboot-required operation; it lives behind an explicit
  user gesture in the dashboard / CLI ``ascendo snapshot restore``.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)] [ValidateSet('create','list')] [string] $Action,
    [Parameter(Mandatory = $true)] [string] $OutputPath,
    [string] $Label,
    [string] $Notes
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

# Persisted label/notes registry — System Restore checkpoints have a
# Description field (used as label) but not a free-form notes field. We
# round-trip both via a JSON file managed by Ascendo itself. ProgramData
# is admin-write but world-read, which matches our threat model.
$RegistryDir  = Join-Path $env:ProgramData 'Ascendo\snapshots'
$RegistryFile = Join-Path $RegistryDir 'registry.json'

function Read-Registry {
    if (-not (Test-Path $RegistryFile)) { return @{} }
    try {
        $raw = Get-Content -LiteralPath $RegistryFile -Raw -Encoding UTF8
        if (-not $raw) { return @{} }
        $obj = $raw | ConvertFrom-Json
        $h = @{}
        $obj.PSObject.Properties | ForEach-Object { $h[$_.Name] = $_.Value }
        return $h
    } catch { return @{} }
}

function Write-Registry {
    param([hashtable] $Map)
    if (-not (Test-Path $RegistryDir)) {
        $null = New-Item -ItemType Directory -Force -Path $RegistryDir
    }
    $json = ($Map | ConvertTo-Json -Depth 4)
    [System.IO.File]::WriteAllText($RegistryFile, $json, [System.Text.UTF8Encoding]::new($false))
}

function Get-VssShadowCopies {
    try {
        $copies = Get-CimInstance -ClassName Win32_ShadowCopy -ErrorAction Stop
    } catch {
        return @()
    }
    $registry = Read-Registry
    $out = @()
    foreach ($c in $copies) {
        $id = $c.ID
        $entry = if ($registry.ContainsKey($id)) { $registry[$id] } else { $null }
        $createdAt = $null
        try {
            # InstallDate is CIM_DATETIME; cast via [DateTime] is reliable.
            $createdAt = [DateTime]$c.InstallDate
            if ($createdAt) {
                $createdAt = $createdAt.ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
            }
        } catch { $createdAt = $null }
        # AllocatedSpace is not present on every Windows build / shadow copy
        # provider; under Set-StrictMode -Version Latest plain dot access
        # throws. Guard via PSObject.Properties.
        $sizeBytes = $null
        if ($c.PSObject.Properties['AllocatedSpace']) {
            $sizeBytes = $c.PSObject.Properties['AllocatedSpace'].Value
        }
        $out += [pscustomobject]@{
            id          = $id
            created_at  = $createdAt
            label       = if ($entry) { $entry.label } else { $null }
            backend     = 'vss'
            size_bytes  = $sizeBytes
            notes       = if ($entry) { $entry.notes } else { $null }
        }
    }
    return ,$out
}

function Invoke-CreateSnapshot {
    param([string] $LabelText, [string] $NotesText)
    # Use Checkpoint-Computer (System Restore) — a single call wraps a
    # VSS shadow copy for every protected volume and is the safest unit.
    # Description field gets the label; notes go to the JSON registry.
    if (-not $LabelText) { $LabelText = "Ascendo $(Get-Date -Format 'yyyy-MM-ddTHH:mm:ssZ')" }
    try {
        # MODIFY_SETTINGS = 12 is the documented event-type for general
        # checkpoints from third-party software per Microsoft's docs.
        Checkpoint-Computer -Description $LabelText -RestorePointType 'MODIFY_SETTINGS' -ErrorAction Stop
    } catch {
        throw "Checkpoint-Computer failed: $($_.Exception.Message)"
    }
    # Find the just-created checkpoint by description match (newest first).
    $rp = Get-ComputerRestorePoint -ErrorAction Stop |
          Where-Object { $_.Description -eq $LabelText } |
          Sort-Object SequenceNumber -Descending |
          Select-Object -First 1
    if (-not $rp) {
        # Fall back to grabbing the newest VSS shadow copy regardless of label.
        $copy = Get-CimInstance -ClassName Win32_ShadowCopy |
                Sort-Object InstallDate -Descending | Select-Object -First 1
        if (-not $copy) { throw "snapshot created but could not be located" }
        $id = $copy.ID
    } else {
        $id = "RP$($rp.SequenceNumber)"
    }

    # Persist label + notes for round-trip.
    $registry = Read-Registry
    $registry[$id] = @{ label = $LabelText; notes = $NotesText }
    Write-Registry -Map $registry

    return [pscustomobject]@{
        id          = $id
        created_at  = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
        label       = $LabelText
        backend     = 'vss'
        size_bytes  = $null
        notes       = $NotesText
    }
}

# ── Dispatch ──────────────────────────────────────────────────────────────
$result = $null
$resultIsList = $false
switch ($Action) {
    'create' { $result = Invoke-CreateSnapshot -LabelText $Label -NotesText $Notes; $resultIsList = $false }
    'list'   { $result = Get-VssShadowCopies; $resultIsList = $true }
}

# Always emit a JSON file (creates parent dirs as needed).
$outputDir = Split-Path -Parent $OutputPath
if ($outputDir -and -not (Test-Path $outputDir)) {
    $null = New-Item -ItemType Directory -Force -Path $outputDir
}

# ConvertTo-Json on an empty array / $null produces an empty string, which
# makes downstream JSON parsers choke. For 'list' actions we always emit a
# JSON array, even when empty. Single-element arrays are also unwrapped to
# objects by ConvertTo-Json without ``-AsArray``, so we wrap them.
if ($resultIsList) {
    $arr = @($result)
    if ($arr.Count -eq 0) {
        $json = '[]'
    } else {
        $body = ($arr | ForEach-Object { ConvertTo-Json -InputObject $_ -Depth 6 }) -join ','
        $json = '[' + $body + ']'
    }
} elseif ($null -eq $result) {
    $json = 'null'
} else {
    $json = $result | ConvertTo-Json -Depth 6
}
[System.IO.File]::WriteAllText($OutputPath, $json, [System.Text.UTF8Encoding]::new($false))
exit 0
