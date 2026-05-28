$errors = $null
$tokens = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile('D:\Dev_Env\ascendo\bin\validate-windows.ps1', [ref]$tokens, [ref]$errors)

$errToken = $null
foreach ($err in $errors) {
    if ($err.Extent.StartLineNumber -eq 85) {
        $errToken = $err
        break
    }
}

if ($errToken) {
    $nearbyTokens = $tokens | Where-Object { $_.Extent.StartLineNumber -ge 86 -and $_.Extent.StartLineNumber -le 90 }
    foreach ($t in $nearbyTokens) {
        Write-Host "$($t.Kind): $($t.Text) (Line $($t.Extent.StartLineNumber))"
    }
}
