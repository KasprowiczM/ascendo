<#
.SYNOPSIS
  ARP apply phase. Removes packages explicitly listed via -ItemFilter via
  each entry's UninstallString. Without -ItemFilter this is a no-op.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)] [string] $RunId,
    [Parameter(Mandatory)] [ValidateSet('cli','scheduler','dashboard','plugin','unknown')] [string] $Trigger,
    [Parameter(Mandatory)] [Alias('Profile')] [string] $ProfileName,
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

function Find-ArpEntry {
    param([string] $TargetId)
    foreach ($root in $RegistryRoots) {
        if (-not (Test-Path $root)) { continue }
        $key = Join-Path $root $TargetId
        if (Test-Path $key) {
            try { return Get-ItemProperty -Path $key -ErrorAction Stop } catch { continue }
        }
    }
    return $null
}

$sidecar = $null
try {
    $_sidecarArgs = @{
        RunId = $RunId; Trigger = $Trigger; ProfileName = $ProfileName
        DryRun = [bool]$DryRun; Phase = 'apply'; Category = 'registry_arp'
        ToolName = 'registry'; ToolVersion = 'n/a'
    }
    $sidecar = New-Sidecar @_sidecarArgs

    if (-not $ItemFilter -or $ItemFilter.Trim() -eq '') {
        Add-SidecarMessage -Sidecar $sidecar -Level 'info' `
            -Text 'arp apply: nothing to do (use -ItemFilter to remove specific entries)'
        [void](Save-Sidecar -Sidecar $sidecar -OutputDir $OutputDir)
        exit 0
    }

    $filterIds = @($ItemFilter -split ',' | ForEach-Object { $_.Trim() } | Where-Object { $_ })

    foreach ($id in $filterIds) {
        $entry = Find-ArpEntry -TargetId $id
        if (-not $entry) {
            [void](Add-SidecarItem -Sidecar $sidecar `
                -Id $id -Name $id `
                -Category 'registry_arp' -SourceType 'registry_arp' `
                -Status 'failed')
            Add-SidecarMessage -Sidecar $sidecar -Level 'warn' `
                -Text ("arp apply: {0} not found in registry" -f $id)
            continue
        }
        # Strict-mode-safe property reads.
        $_p = { param($o,$n) if ($null -eq $o) { return $null }; if (-not $o.PSObject.Properties[$n]) { return $null }; return $o.$n }
        $dn        = & $_p $entry 'DisplayName'
        $name      = if ($dn) { [string]$dn } else { $id }
        $dv        = & $_p $entry 'DisplayVersion'
        $version   = if ($dv) { [string]$dv } else { $null }
        $qus       = & $_p $entry 'QuietUninstallString'
        $us        = & $_p $entry 'UninstallString'
        $uninstall = if ($qus) { [string]$qus } elseif ($us) { [string]$us } else { $null }

        if ($DryRun) {
            $dryArgs = @{
                Sidecar = $sidecar; Id = $id; Name = $name
                Category = 'registry_arp'; SourceType = 'registry_arp'
                Status = 'planned'
            }
            if ($version) { $dryArgs['CurrentVersion'] = $version }
            [void](Add-SidecarItem @dryArgs)
            continue
        }
        if (-not $uninstall) {
            $failArgs = @{
                Sidecar = $sidecar; Id = $id; Name = $name
                Category = 'registry_arp'; SourceType = 'registry_arp'
                Status = 'failed'
            }
            if ($version) { $failArgs['CurrentVersion'] = $version }
            [void](Add-SidecarItem @failArgs)
            Add-SidecarMessage -Sidecar $sidecar -Level 'error' `
                -Text ("arp apply: {0} has no UninstallString" -f $id)
            continue
        }

        $exitCode = -1
        try {
            $proc = Start-Process -FilePath 'cmd.exe' -ArgumentList @('/c', $uninstall) `
                                  -Wait -NoNewWindow -PassThru
            $exitCode = $proc.ExitCode
        } catch {
            Add-SidecarMessage -Sidecar $sidecar -Level 'error' `
                -Text ("arp apply: {0} uninstall threw: {1}" -f $id, $_.Exception.Message)
        }
        $status = if ($exitCode -eq 0 -or $exitCode -eq 3010) { 'success' } else { 'failed' }
        $okArgs = @{
            Sidecar = $sidecar; Id = $id; Name = $name
            Category = 'registry_arp'; SourceType = 'registry_arp'
            Status = $status; ExitCode = [int]$exitCode
            Rollback = @{ available = $false; method = 'reinstall_required' }
        }
        if ($version) { $okArgs['CurrentVersion'] = $version }
        [void](Add-SidecarItem @okArgs)
    }

    [void](Save-Sidecar -Sidecar $sidecar -OutputDir $OutputDir)
    exit 0
}
catch {
    if ($null -ne $sidecar) {
        try {
             Add-SidecarMessage -Sidecar $sidecar -Level 'error' `
                -Text ("arp apply infrastructure failure: {0}" -f $_.Exception.Message)
            [void](Save-Sidecar -Sidecar $sidecar -OutputDir $OutputDir)
        } catch {}
    }
    exit 1
}
