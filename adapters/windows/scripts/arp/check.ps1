<#
.SYNOPSIS
  ARP (Add/Remove Programs) check phase. Read-only enumeration of MSI / EXE
  installs surfaced through the Uninstall registry tree. Emits ascendo/v1
  sidecar.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)] [string] $RunId,
    [Parameter(Mandatory)]
        [ValidateSet('cli','scheduler','dashboard','plugin','unknown')]
        [string] $Trigger,
    [Parameter(Mandatory)]
        [Alias('Profile')]
        [string] $ProfileName,
    [Parameter(Mandatory)] [string] $OutputDir,
    [Parameter()] [switch] $DryRun,
    [Parameter()] [string] $ItemFilter = ''
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$LibDir    = Join-Path (Split-Path -Parent (Split-Path -Parent $ScriptDir)) 'lib'
Import-Module (Join-Path $LibDir 'AscendoJson.psm1') -Force -DisableNameChecking

$RegistryRoots = @(
    'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall',
    'HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall',
    'HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall'
)

function _Get-RegProp {
    # Strict-mode-safe property access on a registry item. Returns $null if
    # the property doesn't exist on the object, otherwise its value.
    param($Obj, [string] $Name)
    if ($null -eq $Obj) { return $null }
    if (-not $Obj.PSObject.Properties[$Name]) { return $null }
    return $Obj.$Name
}

function Get-ArpEntries {
    param([string[]] $Roots)
    $out = New-Object System.Collections.Generic.List[object]
    foreach ($root in $Roots) {
        if (-not (Test-Path $root)) { continue }
        try { $children = Get-ChildItem -Path $root -ErrorAction SilentlyContinue } catch { continue }
        foreach ($key in $children) {
            try { $props = Get-ItemProperty -Path $key.PSPath -ErrorAction SilentlyContinue } catch { continue }
            if (-not $props) { continue }

            $displayName = _Get-RegProp $props 'DisplayName'
            if (-not $displayName) { continue }

            $sysComp = _Get-RegProp $props 'SystemComponent'
            if ($null -ne $sysComp -and $sysComp -eq 1) { continue }

            $parentKey = _Get-RegProp $props 'ParentKeyName'
            if ($parentKey) { continue }

            $uninstall      = _Get-RegProp $props 'UninstallString'
            $quietUninstall = _Get-RegProp $props 'QuietUninstallString'
            if (-not $uninstall -and -not $quietUninstall) { continue }

            $version   = _Get-RegProp $props 'DisplayVersion'
            $publisher = _Get-RegProp $props 'Publisher'

            $hive = $null
            if ($key.PSObject.Properties['PSDrive'] -and $key.PSDrive -and $key.PSDrive.PSObject.Properties['Name']) {
                $hive = [string]$key.PSDrive.Name
            }

            $out.Add([pscustomobject]@{
                Id        = [string]$key.PSChildName
                Name      = [string]$displayName
                Version   = if ($version)   { [string]$version }   else { $null }
                Publisher = if ($publisher) { [string]$publisher } else { $null }
                Hive      = $hive
            })
        }
    }
    return ,$out
}

$sidecar = $null
try {
    $newSidecarArgs = @{
        RunId       = $RunId
        Trigger     = $Trigger
        ProfileName = $ProfileName
        DryRun      = [bool]$DryRun
        Phase       = 'check'
        Category    = 'registry_arp'
        ToolName    = 'registry'
        ToolVersion = 'n/a'
    }
    $sidecar = New-Sidecar @newSidecarArgs

    $itemFilterArray = $null
    if ($null -ne $ItemFilter -and $ItemFilter.Trim() -ne '') {
        $itemFilterArray = @(
            $ItemFilter -split ',' |
                ForEach-Object { $_.Trim() } |
                Where-Object   { $_ -ne '' }
        )
        if ($itemFilterArray.Count -eq 0) { $itemFilterArray = $null }
    }

    $entries = Get-ArpEntries -Roots $RegistryRoots
    Add-SidecarMessage -Sidecar $sidecar -Level 'info' `
        -Text ("arp: {0} ARP entries discovered" -f $entries.Count)

    foreach ($e in $entries) {
        if ($null -ne $itemFilterArray) {
            if (($itemFilterArray -notcontains $e.Id) -and ($itemFilterArray -notcontains $e.Name)) { continue }
        }
        $itemArgs = @{
            Sidecar    = $sidecar
            Id         = $e.Id
            Name       = $e.Name
            Category   = 'registry_arp'
            SourceType = 'registry_arp'
            SourceFeed = $e.Hive
            Status     = 'up_to_date'
        }
        if ($e.Version) { $itemArgs['CurrentVersion'] = $e.Version }
        if ($e.Version -or $e.Publisher) {
            $ev = @{}
            if ($e.Version)   { $ev['registry_version'] = $e.Version }
            if ($e.Publisher) { $ev['note'] = "publisher: $($e.Publisher)" }
            $itemArgs['Evidence'] = $ev
        }
        [void](Add-SidecarItem @itemArgs)
    }

    [void](Save-Sidecar -Sidecar $sidecar -OutputDir $OutputDir)
    exit 0
}
catch {
    if ($null -ne $sidecar) {
        try {
            Add-SidecarMessage -Sidecar $sidecar -Level 'error' `
                -Text ("arp check infrastructure failure: {0}" -f $_.Exception.Message)
            [void](Add-SidecarItem -Sidecar $sidecar `
                -Id '<infrastructure>' -Name 'arp enumeration' `
                -Category 'registry_arp' -SourceType 'registry_arp' `
                -Status 'failed')
            [void](Save-Sidecar -Sidecar $sidecar -OutputDir $OutputDir)
        } catch { }
    }
    exit 1
}
