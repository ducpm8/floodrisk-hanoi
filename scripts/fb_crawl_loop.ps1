param(
    [string]$Url = "https://www.facebook.com/profile/100069422482221/search?q=ng%E1%BA%ADp",
    [int]$MaxBatches = 8,
    [int]$PerBatch = 10,
    [int]$PauseMinSec = 45,
    [int]$PauseMaxSec = 90
)

$endpoint = "http://localhost:8000/api/admin/facebook-crawl"
$body = "{`"facebook_url`":`"$Url`",`"max_posts`":$PerBatch}"

$totalStaged = 0
$totalSkipped = 0
$emptyStreak = 0

for ($i = 1; $i -le $MaxBatches; $i++) {
    Write-Host ("=== BATCH {0} / {1}  start {2} ===" -f $i, $MaxBatches, (Get-Date -Format HH:mm:ss))
    $staged = 0
    $skipped = 0
    try {
        $r = Invoke-WebRequest $endpoint -Method POST -ContentType "application/json" -Body $body -UseBasicParsing -TimeoutSec 600
        $lines = $r.Content -split "`n" | Where-Object { $_ -match "^data:" -and $_ -notmatch "heartbeat" }
        $lines | Select-Object -Last 12 | ForEach-Object { Write-Host ("  {0}" -f $_) }
        $doneLine = $lines | Where-Object { $_ -match '"type":\s*"done"' } | Select-Object -Last 1
        if ($doneLine) {
            $sm = [regex]::Match($doneLine, '"staged":\s*(\d+)')
            $km = [regex]::Match($doneLine, '"skipped":\s*(\d+)')
            if ($sm.Success) { $staged = [int]$sm.Groups[1].Value }
            if ($km.Success) { $skipped = [int]$km.Groups[1].Value }
        }
        $totalStaged += $staged
        $totalSkipped += $skipped
        Write-Host ("  ==> batch {0}: staged={1} skipped={2} | total staged={3}" -f $i, $staged, $skipped, $totalStaged)
    } catch {
        Write-Host ("  ERROR: {0}" -f $_.Exception.Message)
    }
    Write-Host ("=== BATCH {0} end {1} ===" -f $i, (Get-Date -Format HH:mm:ss))

    if ($staged -eq 0) {
        $emptyStreak++
        if ($emptyStreak -ge 2) {
            Write-Host (">>> 2 consecutive empty batches. STOP. Total staged: {0}" -f $totalStaged)
            break
        }
        Write-Host (">>> empty batch ({0}/2) - retry immediately, no pause" -f $emptyStreak)
        continue
    }
    $emptyStreak = 0
    $pause = Get-Random -Minimum $PauseMinSec -Maximum $PauseMaxSec
    Write-Host (">>> staged {0} > 0 - pause {1}s before next batch" -f $staged, $pause)
    Start-Sleep -Seconds $pause
}

Write-Host ""
Write-Host "================================================"
Write-Host ("FINAL: staged={0}  skipped={1}" -f $totalStaged, $totalSkipped)
Write-Host "================================================"
