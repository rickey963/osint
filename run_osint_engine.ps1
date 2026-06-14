# run_osint_engine.ps1 - Automated OSINT Engine Runner

$loop_interval = 120 # 2 minutes in seconds

Write-Host "Starting OSINT Engine automation loop..." -ForegroundColor Green
Write-scale "[!] Press Ctrl+C to stop the engine." -ForegroundColor Yellow

while ($true) {
    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Write-Host "[$timestamp] Starting data fetch (python fetch_data.py)..." -ForegroundColor Cyan

    try {
        # Execute the python script
        python fetch_data.py

        if ($?) {
            Write-Host "[$timestamp] Data update successful!" -ForegroundColor Green
        } else {
            Write-Error "[$timestamp] Data update failed! Check Python errors."
        }
    }
    catch {
        Write-Error "[$timestamp] An error occurred while running the engine: $_"
    }

    Write-Host "Waiting for $loop_interval seconds until next update..." -ForegroundColor Gray
    Start-Sleep -Seconds $loop_interval
}