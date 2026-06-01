# =============================================================================
# adapters/windows/tests/ps/ReleaseFeed.Regex.Tests.ps1
# =============================================================================
#
# W2 (audit ASCENDO_ULTRA_REVIEW_2 sec.4) — fail-loud on a broken version
# probe. When a release_feed handler has a configured version_regex that does
# NOT match the fetched value, the handler must NOT silently report the raw
# value as the candidate version (that paints a wrong "planned"/"up_to_date").
# It must signal probe_broken — on Windows that is a $null return, which
# scripts/web/check.ps1 classifies as `skipped` (probe unavailable) instead of
# fabricating a version. Mirrors the macOS release_feed rc=28 contract.
#
# Contract asserted on _RF-ApplyRegexTransform:
#   * empty pattern            -> passthrough (returns raw)
#   * pattern + match          -> transform (extract/replace)
#   * pattern + NO match       -> $null (probe_broken)
#   * invalid pattern          -> $null (probe_broken; broken config)
#
# No Pester dependency; plain assertions; exit 0 pass / 1 fail.
# Compatibility: PowerShell 5.1 + 7.x.
# =============================================================================

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$Handler = Resolve-Path (Join-Path $PSScriptRoot '..\..\lib\handlers\release_feed.ps1')
. $Handler   # dot-source so _RF-ApplyRegexTransform is defined in this scope

$script:Failures = 0
function Assert-Value {
    param($Expected, $Actual, [string] $Label)
    # Normalise $null and '' distinctly: $null is the probe_broken sentinel.
    $eNull = ($null -eq $Expected)
    $aNull = ($null -eq $Actual)
    if ($eNull -ne $aNull -or (-not $eNull -and ([string]$Expected -ne [string]$Actual))) {
        $eShow = if ($eNull) { '<null>' } else { [string]$Expected }
        $aShow = if ($aNull) { '<null>' } else { [string]$Actual }
        Write-Host ("[FAIL] {0}: expected [{1}] got [{2}]" -f $Label, $eShow, $aShow)
        $script:Failures++
    } else {
        Write-Host ("[ OK ] {0}" -f $Label)
    }
}

# empty pattern -> passthrough
$r1 = _RF-ApplyRegexTransform -Raw '1.2.3' -Pattern '' -Replacement ''
Assert-Value -Expected '1.2.3' -Actual $r1 -Label 'empty pattern => passthrough raw'

# pattern + match -> transform (whole-string extract; Replace keeps unmatched
# text, so an extraction pattern must span the whole value).
$r2 = _RF-ApplyRegexTransform -Raw 'release v9.4.1 (stable)' -Pattern '^.*?(\d+\.\d+\.\d+).*$' -Replacement '$1'
Assert-Value -Expected '9.4.1' -Actual $r2 -Label 'pattern match => transform'

# pattern + NO match -> probe_broken ($null)
$r3 = _RF-ApplyRegexTransform -Raw 'no version string in this body' -Pattern 'v(\d+\.\d+\.\d+)' -Replacement '$1'
Assert-Value -Expected $null -Actual $r3 -Label 'pattern no-match => probe_broken ($null), NOT raw'

# invalid pattern -> probe_broken ($null)
$r4 = _RF-ApplyRegexTransform -Raw '1.2.3' -Pattern '(' -Replacement '$1'
Assert-Value -Expected $null -Actual $r4 -Label 'invalid pattern => probe_broken ($null)'

if ($script:Failures -gt 0) {
    Write-Host ("RELEASE_FEED REGEX TESTS FAILED ({0})" -f $script:Failures)
    exit 1
}
Write-Host 'RELEASE_FEED REGEX TESTS PASSED'
exit 0
