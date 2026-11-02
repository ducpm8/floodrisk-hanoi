$port = 8000
$conn = Get-NetTCPConnection -LocalPort $port -ErrorAction SilentlyContinue
if ($conn) {
    Stop-Process -Id $conn.OwningProcess -Force -ErrorAction SilentlyContinue
    Write-Host "Killed PID $($conn.OwningProcess) on :$port"
} else {
    Write-Host "Nothing on :$port"
}

Start-Sleep -Milliseconds 500

$python = "C:\Program Files\PyManager\python.exe"
Start-Process -FilePath $python -ArgumentList "-m uvicorn api.main:app --host 127.0.0.1 --port 8000 --log-level info" -WorkingDirectory "C:\firstproject" -WindowStyle Normal

Write-Host "Waiting for API..."
$ready = $false
for ($i = 0; $i -lt 15; $i++) {
    Start-Sleep -Seconds 1
    try {
        $r = Invoke-WebRequest -Uri "http://127.0.0.1:8000/health" -UseBasicParsing -TimeoutSec 1 -ErrorAction Stop
        if ($r.StatusCode -eq 200) { $ready = $true; break }
    } catch {}
}

if ($ready) { Write-Host "API up at http://127.0.0.1:8000" }
else { Write-Host "API did not respond after 15s - check the new window for errors" }
