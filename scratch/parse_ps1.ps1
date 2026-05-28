$errors = $null
$tokens = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile('D:\Dev_Env\ascendo\bin\validate-windows.ps1', [ref]$tokens, [ref]$errors)
foreach ($err in $errors) {
    Write-Host "Error at line $($err.Extent.StartLineNumber): $($err.Message)"
}
