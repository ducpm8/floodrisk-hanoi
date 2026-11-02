@(8000, 3000) | ForEach-Object {
    $p = (Get-NetTCPConnection -LocalPort $_ -State Listen -ErrorAction SilentlyContinue).OwningProcess
    if ($p) { Stop-Process -Id $p -Force; Write-Host "Killed PID $p on port $_" } else { Write-Host "Port $_ is free" }
}
