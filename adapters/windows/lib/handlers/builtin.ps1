# =============================================================================
# adapters/windows/lib/handlers/builtin.ps1
# =============================================================================
#
# Per-handler functions for the builtin WebManager handler (Tier-B / manual).
#
# Public surface:
#   Invoke-BuiltinCheck    - returns $null (no candidate version known).
#                            Caller emits a 'skipped' / 'triggered' item.
#   Invoke-BuiltinApply    - opens the configured vendor URL so the operator
#                            can run the vendor's own update flow.
#                            Returns $true on success, $false on failure.
#
# Compatibility: PowerShell 5.1 + 7.x. Set-StrictMode safe.
# =============================================================================

Set-StrictMode -Version Latest

function Invoke-BuiltinCheck {
    [CmdletBinding()]
    [OutputType([string])]
    param(
        [Parameter(Mandatory)] [string] $Slug,
        [Parameter(Mandatory)] [object] $Config
    )
    # By design, builtin has no probe path -- we can't know the candidate
    # version without a real upstream feed. The caller (check.ps1) treats
    # $null as "skipped: manual_required" and emits a sidecar item that
    # surfaces the vendor URL to the operator.
    return $null
}

function Invoke-BuiltinApply {
    [CmdletBinding()]
    [OutputType([bool])]
    param(
        [Parameter(Mandatory)] [string] $Slug,
        [Parameter(Mandatory)] [object] $Config
    )
    $sub = $Config.PSObject.Properties['builtin']
    if ($null -eq $sub -or $null -eq $sub.Value) {
        Write-Verbose "$Slug : builtin sub-table missing on config; cannot open URL"
        return $false
    }
    $urlProp = $sub.Value.PSObject.Properties['url']
    if ($null -eq $urlProp -or -not $urlProp.Value) {
        Write-Verbose "$Slug : builtin.url missing; cannot open URL"
        return $false
    }
    $url = [string]$urlProp.Value
    try {
        Start-Process -FilePath $url -ErrorAction Stop | Out-Null
        return $true
    } catch {
        Write-Verbose "$Slug : Start-Process $url failed: $_"
        return $false
    }
}

# Functions are auto-exported when this file is dot-sourced or
# loaded via Import-Module (PS5.1 errors on explicit Export-ModuleMember
# from inside a transient .ps1 module -- Sesja 58 fix).
