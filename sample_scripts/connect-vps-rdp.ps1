param(
    [string]$VpsUser = "myuser",
    [string]$VpsHost = "vps.example.com",
    [int]$LocalPort = 3389,
    [string]$RemoteHost = "127.0.0.1",
    [int]$RemotePort = 3389,
    [string]$RdpFile = "$PSScriptRoot\mail-custodian-via-ssh-tunnel.rdp",
    [switch]$Tunnel,
    [switch]$Rdp
)

if (-not $Tunnel -and -not $Rdp) {
    $Tunnel = $true
    $Rdp = $true
}

if ($Tunnel) {
    Start-Process -FilePath "ssh.exe" -ArgumentList @(
        "$VpsUser@$VpsHost",
        "-L", "$LocalPort`:$RemoteHost`:$RemotePort"
    )
}

if ($Rdp) {
    if ($Tunnel) {
        Start-Sleep -Seconds 4
    }

    if (Test-Path $RdpFile) {
        Start-Process -FilePath "mstsc.exe" -ArgumentList @($RdpFile)
    } else {
        Start-Process -FilePath "mstsc.exe" -ArgumentList @("/v:127.0.0.1:$LocalPort")
    }
}
