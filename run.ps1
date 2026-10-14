# Start the chatbot (Windows PowerShell):  .\run.ps1
$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot
$py = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { Write-Error "No .venv found - run .\setup.ps1 first."; exit 1 }

# Make sure Ollama is up (starts it in the background if it is installed but not running).
try { Invoke-RestMethod -Uri "http://127.0.0.1:11434/api/version" -TimeoutSec 2 | Out-Null }
catch {
  if (Get-Command ollama -ErrorAction SilentlyContinue) {
    Write-Host "Starting Ollama..." -ForegroundColor Yellow
    Start-Process -WindowStyle Hidden ollama -ArgumentList "serve"
    Start-Sleep -Seconds 3
  } else { Write-Warning "Ollama is not running - answers will fail until it is started." }
}

Start-Job { Start-Sleep 4; Start-Process "http://127.0.0.1:8000" } | Out-Null
& $py run.py
