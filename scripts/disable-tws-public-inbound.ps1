#Requires -RunAsAdministrator
$ErrorActionPreference = 'Stop'

$rules = Get-NetFirewallRule | Where-Object {
    $_.DisplayName -eq 'tws.exe' -and
    $_.Direction -eq 'Inbound' -and
    $_.Action -eq 'Allow'
}

if (-not $rules) {
    Write-Host 'No inbound allow rules for tws.exe were found.'
    exit 0
}

$rules | Disable-NetFirewallRule
$remaining = Get-NetFirewallRule | Where-Object {
    $_.DisplayName -eq 'tws.exe' -and
    $_.Direction -eq 'Inbound' -and
    $_.Action -eq 'Allow' -and
    $_.Enabled -eq 'True'
}

if ($remaining) {
    throw 'One or more tws.exe inbound allow rules are still enabled.'
}

Write-Host 'Disabled all tws.exe inbound allow rules. Localhost API access remains available.'
