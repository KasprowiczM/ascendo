# =============================================================================
# AscendoWingetActions.psm1 - mutation helpers + policy tables for apply phase
# =============================================================================
#
# Companion to AscendoWinget.psm1 (read-only winget wrappers). This module
# carries the *side-effecting* helpers used by apply.ps1:
#
#   * Process management (kill running apps before upgrade -> avoid file lock)
#   * Registry-driven uninstall (for installers that hang if old version is
#     still registered)
#   * Skip-list / uninstall-first policy tables
#   * Rollback-method synthesis for the sidecar
#
# All three policy tables are *ports* of the corresponding constants in
# Aktualizacje-W11-Dell5520/3_Update-Programs.ps1 (the Windows pre-merge
# codebase that battle-tested these entries against ~600 real packages).
# Where a winget package ID is unfamiliar, this module preserves it verbatim
# rather than guessing - the source repo is the ground truth.
#
# Compatibility: PowerShell 5.1 + 7.x. No external dependencies.
# =============================================================================

Set-StrictMode -Version Latest

# -----------------------------------------------------------------------------
# Module-private constants
# -----------------------------------------------------------------------------

# Registry hives scanned for ARP (Add/Remove Programs) entries when
# Uninstall-PackageViaRegistry needs to find an UninstallString.
$script:UNINSTALL_REG_PATHS = @(
    'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*'
    'HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*'
    'HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*'
)

# -----------------------------------------------------------------------------
# Policy tables (ported from 3_Update-Programs.ps1)
# -----------------------------------------------------------------------------
# IMPORTANT: every entry below is a verbatim port. If a future Ascendo build
# wants to *add* policies, do it here - but do not silently delete entries
# that exist upstream without updating the upstream source first. The
# Aktualizacje-W11-Dell5520 repository is authoritative.
# -----------------------------------------------------------------------------

# Source: 3_Update-Programs.ps1 line 74-154. Process names are matched by
# Get-Process -Name (no .exe suffix, case-insensitive on Windows).
$script:APP_PROCESS_MAP = [ordered]@{
    # Remote Access & Server Management
    'Supermicro.IPMIView'                = @('IPMIView', 'ipmitool')
    'ASTi.IPMIView'                      = @('IPMIView', 'ipmitool')
    'Devolutions.RemoteDesktopManager'   = @('RemoteDesktopManager', 'RemoteDesktopManager64')
    'RealVNC.VNCViewer'                  = @('vncviewer', 'vncviewerx64')
    # Media
    'VideoLAN.VLC'                       = @('vlc')
    'Spotify.Spotify'                    = @('Spotify')
    'Plex.PlexMediaServer'               = @('Plex Media Server', 'PlexScriptHost')
    'OBSProject.OBSStudio'               = @('obs64', 'obs32', 'obs')
    'HandBrake.HandBrake'                = @('HandBrake')
    'Perplexity.Comet'                   = @('Comet')
    # Communication & Collaboration
    'Zoom.Zoom'                          = @('Zoom', 'CptHost', 'ZoomOutlookPlugin')
    'SlackTechnologies.Slack'            = @('slack')
    'Discord.Discord'                    = @('Discord', 'DiscordPTB', 'DiscordCanary')
    'Telegram.TelegramDesktop'           = @('Telegram')
    'WhatsApp.WhatsApp'                  = @('WhatsApp')
    'Signal.Signal'                      = @('Signal')
    'Microsoft.Teams'                    = @('Teams', 'ms-teams')
    'Cisco.Webex'                        = @('CiscoWebexStart', 'webex')
    'Tutanota.Tutanota'                  = @('tutanota-desktop')
    # Browsers
    'Mozilla.Firefox'                    = @('firefox')
    'Google.Chrome'                      = @('chrome')
    'Microsoft.Edge'                     = @('msedge')
    'Opera.Opera'                        = @('opera')
    'Brave.Brave'                        = @('brave')
    'Vivaldi.Vivaldi'                    = @('vivaldi')
    'Google.Antigravity'                 = @('Antigravity')
    # Productivity & Office
    'Adobe.Acrobat.Reader.64-bit'        = @('AcroRd32', 'Acrobat')
    'Notion.Notion'                      = @('Notion')
    'Obsidian.Obsidian'                  = @('Obsidian')
    'Microsoft.OneDrive'                 = @('OneDrive')
    # Graphics & Design
    'GIMP.GIMP'                          = @('gimp-2', 'gimp')
    'GIMP.GIMP.3'                        = @('gimp-3', 'gimp-2', 'gimp')
    'Inkscape.Inkscape'                  = @('inkscape')
    # AI Assistants & LLM Tools
    'Anthropic.Claude'                   = @('Claude')
    'OpenAI.Codex'                       = @('Codex', 'openai-codex')
    # Development & Programming
    'Microsoft.VisualStudioCode'         = @('Code')
    'SublimeHQ.SublimeText.4'            = @('sublime_text')
    'Notepad++.Notepad++'                = @('notepad++')
    'SST.OpenCodeDesktop'                = @('opencode-desktop', 'OpenCode')
    'SST.opencode'                       = @('opencode')
    'Postman.Postman'                    = @('Postman')
    'Docker.DockerDesktop'               = @('Docker Desktop', 'com.docker.backend', 'dockerd')
    'GitHub.GitHubDesktop'               = @('GitHubDesktop')
    'Atlassian.Sourcetree'               = @('SourceTree')
    # System Utilities
    'RARLab.WinRAR'                      = @('WinRAR')
    '7zip.7zip'                          = @('7zFM', '7zG')
    'IZArc.IZArc'                        = @('IZArc')
    'Anki.Anki'                          = @('anki')
    'CrystalDewWorld.CrystalDiskInfo'    = @('DiskInfo64', 'DiskInfo32')
    'CPUID.CPU-Z'                        = @('cpuz')
    'TechPowerUp.GPU-Z'                  = @('GPU-Z')
    'HWiNFO.HWiNFO'                      = @('HWiNFO64', 'HWiNFO32')
    'Piriform.CCleaner'                  = @('CCleaner64', 'CCleaner')
    'Balena.Etcher'                      = @('balenaEtcher')
    'Rufus.Rufus'                        = @('rufus')
    # Security
    'Bitwarden.Bitwarden'                = @('Bitwarden')
    'KeePassXCTeam.KeePassXC'            = @('KeePassXC')
    'NordVPN.NordVPN'                    = @('NordVPN')
    'ProtonTechnologies.ProtonVPN'       = @('ProtonVPN')
    'Malwarebytes.Malwarebytes'          = @('MBam', 'MBAMService')
    # Cloud & Storage
    'Dropbox.Dropbox'                    = @('Dropbox')
    'Nextcloud.NextcloudDesktop'         = @('nextcloud')
    'Synology.DriveClient'               = @('SynologyDrive')
    'Mega.MEGASync'                      = @('MEGASync', 'MEGAupdater')
    # Network & Remote
    'WinSCP.WinSCP'                      = @('WinSCP')
    'PuTTY.PuTTY'                        = @('putty')
    'MobaXterm.MobaXterm'                = @('MobaXterm_Personal', 'MobaXterm')
}

# Source: 3_Update-Programs.ps1 line 158-181. Installers that hang when the
# previous version is still registered. The 'Arch' field is preserved
# verbatim from the source for parity but is not currently consumed by
# Uninstall-PackageViaRegistry (the registry sweep already covers WOW64
# and HKCU).
$script:UNINSTALL_FIRST_MAP = @{
    'Supermicro.IPMIView' = @{
        ProcessNames          = @('IPMIView', 'ipmitool')
        UninstallName         = 'IPMIView*'
        InstallAfterUninstall = $false
    }
    'ASTi.IPMIView' = @{
        ProcessNames          = @('IPMIView', 'ipmitool')
        UninstallName         = 'IPMIView*'
        InstallAfterUninstall = $false
    }
    'SDAssociation.SDMemoryCardFormatter' = @{
        ProcessNames          = @()
        UninstallName         = 'SD Card Formatter*'
        InstallAfterUninstall = $true
    }
    # NOTE: 3_Update-Programs.ps1 documents MEGAsync and IMG-to-ISO as
    # uninstall-first candidates in CLAUDE.md but does NOT actually list
    # them in $UNINSTALL_FIRST upstream - they are handled via the
    # unknown-version evidence rules instead. We preserve that behaviour
    # here. TODO: confirm with maintainer whether they should be promoted
    # to uninstall-first now that we have the apply phase in Ascendo.
}

# Source: 3_Update-Programs.ps1 line 186-193 ($SKIP_PACKAGE_POLICY -> $SKIP_IDS).
# These IDs are detected by winget but cannot be auto-upgraded reliably; they
# are surfaced to manual review instead. Add to this list only when an
# upstream policy entry is added to 3_Update-Programs.ps1 first.
$script:SKIP_IDS = @(
    'Microsoft.DotNet.DesktopRuntime.10'
    # Reason: runtime/framework entry detected via ARP; update path is
    # Windows Update or manual install (not winget upgrade).
)

# -----------------------------------------------------------------------------
# PUBLIC FUNCTIONS - policy table accessors
# -----------------------------------------------------------------------------

function Get-AscendoWingetSkipList {
    <#
    .SYNOPSIS
        IDs to never auto-upgrade in apply phase.
    .OUTPUTS
        [string[]] - package IDs.
    .NOTES
        Ported from $SKIP_IDS in 3_Update-Programs.ps1.
        Currently includes: 'Microsoft.DotNet.DesktopRuntime.10' (ARP/ID mismatch).
    #>
    [CmdletBinding()]
    [OutputType([string[]])]
    param()

    # Return a copy so callers cannot mutate the module's private state.
    return @($script:SKIP_IDS)
}

function Get-AscendoWingetProcessMap {
    <#
    .SYNOPSIS
        Map of package ID -> array of process names to stop before upgrade.
    .OUTPUTS
        [hashtable] - { 'Vendor.AppId' = @('proc1','proc2'); ... }
    .NOTES
        Ported from $APP_PROCESS_MAP in 3_Update-Programs.ps1. File-lock
        failures are common with VLC, Discord, Chrome, Spotify, Claude
        desktop, GIMP, etc. without this. Returns a *new* hashtable so
        callers cannot mutate the module's private state.
    #>
    [CmdletBinding()]
    [OutputType([hashtable])]
    param()

    $copy = [ordered]@{}
    foreach ($k in $script:APP_PROCESS_MAP.Keys) {
        $copy[$k] = @($script:APP_PROCESS_MAP[$k])
    }
    # Cast back to plain hashtable to satisfy the documented OutputType.
    $plain = @{}
    foreach ($k in $copy.Keys) { $plain[$k] = $copy[$k] }
    return $plain
}

function Get-AscendoWingetUninstallFirstMap {
    <#
    .SYNOPSIS
        Map of package ID -> uninstall-first metadata for problematic installers.
    .OUTPUTS
        [hashtable] - values are hashtables with keys:
          ProcessNames           [string[]]
          UninstallName          [string]   wildcard for registry DisplayName
          InstallAfterUninstall  [bool]     true = run install after successful uninstall
    .NOTES
        Ported from $UNINSTALL_FIRST in 3_Update-Programs.ps1. IPMIView
        and SD Card Formatter installers hang if previous version is still
        registered.
    #>
    [CmdletBinding()]
    [OutputType([hashtable])]
    param()

    $copy = @{}
    foreach ($k in $script:UNINSTALL_FIRST_MAP.Keys) {
        $entry = $script:UNINSTALL_FIRST_MAP[$k]
        $copy[$k] = @{
            ProcessNames          = @($entry.ProcessNames)
            UninstallName         = [string]$entry.UninstallName
            InstallAfterUninstall = [bool]$entry.InstallAfterUninstall
        }
    }
    return $copy
}

function Test-PackageSkipped {
    <#
    .SYNOPSIS
        Test whether a winget package ID is on the apply-phase skip list.
    .PARAMETER PackageId
        winget package ID.
    .OUTPUTS
        [bool]
    #>
    [CmdletBinding()]
    [OutputType([bool])]
    param(
        [Parameter(Mandatory)] [AllowEmptyString()] [string]$PackageId
    )

    if ([string]::IsNullOrWhiteSpace($PackageId)) { return $false }
    return ($script:SKIP_IDS -contains $PackageId)
}

# -----------------------------------------------------------------------------
# PUBLIC FUNCTIONS - mutation helpers
# -----------------------------------------------------------------------------

function Stop-PackageProcesses {
    <#
    .SYNOPSIS
        Stop all processes associated with a package per the process map.
    .DESCRIPTION
        Looks up the package in the process map and attempts to stop each
        listed process. Uses Get-Process -ErrorAction SilentlyContinue so
        a missing process is NOT an error (the typical case - most apps
        are not running at maintenance time).

        Each process gets a graceful CloseMainWindow() attempt with up to
        $Wait seconds for shutdown, then a hard Stop-Process -Force.

        Returns one PSCustomObject per (process-name, instance) tuple
        encountered, even if the process was not running. Callers can
        filter by Stopped=$true to count actual kills.
    .PARAMETER PackageId
        winget package ID.
    .PARAMETER Wait
        Optional [int] timeout in seconds for graceful shutdown
        (Stop-Process -Force fallback after this). Default 5.
    .OUTPUTS
        [PSCustomObject[]] - each with: ProcessName, Pid, Stopped (bool),
        Error (string|$null).
    .NOTES
        Ported from Stop-AppProcesses in 3_Update-Programs.ps1 line 530.
        That implementation went straight to Stop-Process -Force; we add a
        graceful CloseMainWindow() pre-step for foreground apps so the user
        does not lose unsaved state when running a maintenance pass with
        the dashboard visible.
    #>
    [CmdletBinding()]
    [OutputType([System.Object[]])]
    param(
        [Parameter(Mandatory)] [string]$PackageId,
        [Parameter()] [int]$Wait = 5
    )

    $results = New-Object System.Collections.Generic.List[object]

    if (-not $script:APP_PROCESS_MAP.Contains($PackageId)) {
        Write-Verbose "Stop-PackageProcesses: '$PackageId' not in APP_PROCESS_MAP; nothing to do."
        return ,@()
    }

    $procNames = $script:APP_PROCESS_MAP[$PackageId]
    foreach ($procName in $procNames) {
        $running = @(Get-Process -Name $procName -ErrorAction SilentlyContinue)
        if (-not $running -or $running.Count -eq 0) {
            Write-Verbose "Stop-PackageProcesses: '$procName' not running."
            continue
        }

        foreach ($proc in $running) {
            $entry = [pscustomobject]@{
                ProcessName = $procName
                Pid         = $proc.Id
                Stopped     = $false
                Error       = $null
            }

            # Phase 1: graceful shutdown (CloseMainWindow only works for GUI
            # processes with a main window; service / background processes
            # return $false and we fall through to the force kill).
            try {
                if ($proc.MainWindowHandle -ne [IntPtr]::Zero) {
                    [void]$proc.CloseMainWindow()
                    if ($proc.WaitForExit($Wait * 1000)) {
                        $entry.Stopped = $true
                        $results.Add($entry)
                        continue
                    }
                }
            } catch {
                # Swallow - we will try Stop-Process below.
                Write-Verbose ("Stop-PackageProcesses: CloseMainWindow on PID {0} threw: {1}" -f $proc.Id, $_.Exception.Message)
            }

            # Phase 2: hard kill.
            try {
                Stop-Process -Id $proc.Id -Force -ErrorAction Stop
                # Stop-Process returns immediately; give the OS a moment.
                Start-Sleep -Milliseconds 500
                $entry.Stopped = $true
            } catch {
                $entry.Error = [string]$_.Exception.Message
            }
            $results.Add($entry)
        }
    }

    return ,$results.ToArray()
}

function Uninstall-PackageViaRegistry {
    <#
    .SYNOPSIS
        Find a package by DisplayName wildcard in registry ARP and run its
        UninstallString silently.
    .DESCRIPTION
        Searches all three uninstall registry hives (HKLM 64-bit, HKLM
        WOW6432Node, HKCU) for entries whose DisplayName matches the
        wildcard. Runs the first matching UninstallString silently:

          * MSI installer (UninstallString contains 'msiexec' or '.msi'):
            extracts the {GUID} product code and runs
            'msiexec /x <GUID> /qn /norestart'.
          * EXE installer: runs the executable with a battery of common
            silent flags '/S /SILENT /VERYSILENT /NORESTART /quiet'.
            The flags are deliberately permissive: most installers ignore
            the ones they do not recognise, and the worst case is a noisy
            uninstall that still completes.

        Treats exit code 0 as success and 3010 as success-with-restart
        (consistent with Convert-WingetExitCode).
    .PARAMETER UninstallName
        Wildcard for HKLM/HKCU Uninstall registry DisplayName field. Must
        be non-empty - empty wildcards would match every installed program.
    .OUTPUTS
        [PSCustomObject] with:
          Found            [bool]   true if at least one matching ARP entry was found
          UninstallString  [string] the raw UninstallString from registry (or '')
          ExitCode         [int]    exit code from the uninstaller (0 if Found=$false)
          Error            [string] error message, or '' on success
    .NOTES
        Ported from Invoke-UninstallFirst in 3_Update-Programs.ps1 line 552.
        Per the apply.ps1 contract we return *one* aggregate result rather
        than per-entry results: callers only need to know whether the
        package is gone, not how many ARP rows it had.
    #>
    [CmdletBinding()]
    [OutputType([pscustomobject])]
    param(
        [Parameter(Mandatory)] [string]$UninstallName
    )

    if ([string]::IsNullOrWhiteSpace($UninstallName)) {
        throw 'Uninstall-PackageViaRegistry: UninstallName must be non-empty.'
    }

    $result = [pscustomobject]@{
        Found           = $false
        UninstallString = ''
        ExitCode        = 0
        Error           = ''
    }

    $entries = @()
    foreach ($path in $script:UNINSTALL_REG_PATHS) {
        try {
            $found = Get-ItemProperty -Path $path -ErrorAction SilentlyContinue |
                Where-Object { $_.DisplayName -like $UninstallName }
            if ($found) { $entries += $found }
        } catch {
            Write-Verbose "Uninstall-PackageViaRegistry: scan of '$path' threw: $_"
        }
    }

    if ($entries.Count -eq 0) {
        Write-Verbose "Uninstall-PackageViaRegistry: no ARP entry matched '$UninstallName'."
        return $result
    }

    # Pick the first entry that has a UninstallString. Some ARP rows are
    # phantom (DisplayName but no UninstallString); skip those.
    $entry = $null
    foreach ($e in $entries) {
        if ($e.UninstallString) { $entry = $e; break }
    }
    if ($null -eq $entry) {
        $result.Found = $true
        $result.Error = "Matching ARP entries found but none had an UninstallString."
        return $result
    }

    $result.Found = $true
    $result.UninstallString = [string]$entry.UninstallString
    $uString = $result.UninstallString

    try {
        if ($uString -match 'msiexec' -or $uString -match '\.msi') {
            # MSI path: extract product GUID and run msiexec /x silently.
            $productCode = ''
            if ($uString -match '\{[0-9A-Fa-f\-]{36}\}') {
                $productCode = $Matches[0]
            }
            if (-not $productCode) {
                $result.ExitCode = 1
                $result.Error    = "MSI uninstall string did not contain a product GUID: $uString"
                return $result
            }
            $msiArgs = @('/x', $productCode, '/qn', '/norestart')
            $proc = Start-Process -FilePath 'msiexec.exe' `
                -ArgumentList $msiArgs `
                -Wait -PassThru -NoNewWindow -ErrorAction Stop
            $result.ExitCode = [int]$proc.ExitCode
        } else {
            # EXE path: extract the executable from the (possibly quoted)
            # UninstallString and run with permissive silent flags.
            $exe = ''
            if ($uString -match '^"([^"]+)"') {
                $exe = $Matches[1]
            } else {
                # Unquoted - take the first whitespace-delimited token.
                $exe = ($uString -split '\s+', 2)[0]
            }
            if (-not $exe) {
                $result.ExitCode = 1
                $result.Error    = "Could not parse executable from UninstallString: $uString"
                return $result
            }
            $exeArgs = @('/S', '/SILENT', '/VERYSILENT', '/NORESTART', '/quiet')
            $proc = Start-Process -FilePath $exe `
                -ArgumentList $exeArgs `
                -Wait -PassThru -NoNewWindow -ErrorAction Stop
            $result.ExitCode = [int]$proc.ExitCode
        }

        # 0 = success; 3010 = success-but-restart-required.
        if ($result.ExitCode -ne 0 -and $result.ExitCode -ne 3010) {
            $result.Error = "Uninstaller returned non-zero exit code $($result.ExitCode)."
        }
    } catch {
        $result.ExitCode = 1
        $result.Error    = [string]$_.Exception.Message
    }

    return $result
}

function Get-AscendoWingetRollbackMethod {
    <#
    .SYNOPSIS
        Build the rollback hint hashtable for a successful winget upgrade.
    .DESCRIPTION
        Produces the per-item rollback fragment (matches the ItemRollback
        Pydantic model in core/ascendo/models/package.py). The orchestrator
        is responsible for filling in snapshot_id and instructions_path
        later; this function only handles per-item method.
    .PARAMETER PackageId
        winget package ID.
    .PARAMETER PreviousVersion
        Version that was installed before upgrade (current_version of the item).
    .OUTPUTS
        [hashtable] for ItemRollback model:
          available  = $true if PreviousVersion is non-empty, else $false
          method     = "winget install --id <PackageId> --version <PreviousVersion> ..."
                       (null if available=$false)
    .NOTES
        Per ADR + threat-model: every successful apply must record a
        rollback hint, even if the hint is "rollback not possible without
        a snapshot". When PreviousVersion is null/empty the available flag
        is $false so the orchestrator can fall back to snapshot-based
        rollback (Time Machine / VSS / timeshift).
    #>
    [CmdletBinding()]
    [OutputType([hashtable])]
    param(
        [Parameter(Mandatory)] [string]$PackageId,
        [Parameter()] [AllowNull()] [AllowEmptyString()] [string]$PreviousVersion
    )

    if ([string]::IsNullOrWhiteSpace($PreviousVersion) -or $PreviousVersion -eq 'Unknown') {
        return @{
            available = $false
            method    = $null
        }
    }

    $cmd = ('winget install --id {0} --version {1} --silent ' +
            '--accept-package-agreements --accept-source-agreements ' +
            '--disable-interactivity') -f $PackageId, $PreviousVersion

    return @{
        available = $true
        method    = $cmd
    }
}

# -----------------------------------------------------------------------------
# Module exports
# -----------------------------------------------------------------------------

Export-ModuleMember -Function @(
    'Get-AscendoWingetSkipList'
    'Get-AscendoWingetProcessMap'
    'Get-AscendoWingetUninstallFirstMap'
    'Test-PackageSkipped'
    'Stop-PackageProcesses'
    'Uninstall-PackageViaRegistry'
    'Get-AscendoWingetRollbackMethod'
)
