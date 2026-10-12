# scripts/get_tunnel_url.ps1
# Extracts and prints active trycloudflare.com URL from logs/tunnel.log
param (
    [int]$WaitSeconds = 10
)

$LogPath = Join-Path $PSScriptRoot "..\logs\tunnel.log"
$Regex = 'https://[a-zA-Z0-9-]+\.trycloudflare\.com'
$Deadline = (Get-Date).AddSeconds($WaitSeconds)

while ((Get-Date) -le $Deadline) {
    if (Test-Path $LogPath) {
        $Content = Get-Content $LogPath -Raw -ErrorAction SilentlyContinue
        if ($Content -match $Regex) {
            $Matches = [regex]::Matches($Content, $Regex)
            if ($Matches.Count -gt 0) {
                $Url = $Matches[$Matches.Count - 1].Value
                Write-Output $Url
                exit 0
            }
        }
    }
    Start-Sleep -Seconds 1
}

Write-Error "No active trycloudflare.com tunnel URL found in logs/tunnel.log"
exit 1
