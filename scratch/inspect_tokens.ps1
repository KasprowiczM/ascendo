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
    Write-Host "Error token offset: $($errToken.Extent.StartOffset) - $($errToken.Extent.EndOffset)"
    $nearbyTokens = $tokens | Where-Object { $_.Extent.StartOffset -ge ($errToken.Extent.StartOffset - 100) -and $_.Extent.EndOffset -le ($errToken.Extent.EndOffset + 100) }
    foreach ($t in $nearbyTokens) {
        Write-Host "$($t.Kind): $($t.Text) (Line $($t.Extent.StartLineNumber))"
    }
}
