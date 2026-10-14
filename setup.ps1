<#
  One-time setup for Windows (PowerShell).

    .\setup.ps1                 # venv + CUDA PyTorch + all packages + Ollama models + build data/indexes
    .\setup.ps1 -CpuTorch       # CPU-only PyTorch for the embedder (smaller download, keeps VRAM free)
    .\setup.ps1 -NoTorch        # no PyTorch at all: embeddings via Ollama 'all-minilm' (same model)

  If scripts are blocked:  Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
  Uses "python -m pip" everywhere (works where pip.exe inside a venv is blocked by Application Control).
#>
param(
  [switch]$CpuTorch,
  [switch]$NoTorch,
  [string]$PythonVersion = "3.12",
  [string]$TorchVersion = "2.11.0",
  [string]$CudaIndex = "https://download.pytorch.org/whl/cu128"
)
$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

function Step($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }

# ---------------------------------------------------------------- venv
Step "Creating virtual environment (.venv) with Python $PythonVersion"
if (-not (Test-Path ".venv\Scripts\python.exe")) {
  if (Get-Command py -ErrorAction SilentlyContinue) { py -$PythonVersion -m venv .venv }
  else { python -m venv .venv }
}
$py = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
& $py --version
& $py -m pip install --upgrade pip

# ---------------------------------------------------------------- packages
Step "Installing core packages"
& $py -m pip install -r requirements.txt -r requirements-dev.txt

if ($NoTorch) {
  Step "Skipping PyTorch / sentence-transformers (embeddings will use Ollama all-minilm)"
} else {
  if ($CpuTorch) {
    Step "Installing PyTorch $TorchVersion (CPU)"
    & $py -m pip install "torch==$TorchVersion" --index-url https://download.pytorch.org/whl/cpu
  } else {
    Step "Installing PyTorch $TorchVersion (CUDA 12.8 build - works on RTX 50-series / Blackwell)"
    & $py -m pip install "torch==$TorchVersion" --index-url $CudaIndex
  }
  Step "Installing sentence-transformers"
  & $py -m pip install -r requirements-embeddings.txt
}

# ---------------------------------------------------------------- .env
if (-not (Test-Path ".env")) {
  Copy-Item ".env.example" ".env"
  if ($NoTorch) { (Get-Content ".env") -replace '^EMBED_BACKEND=.*', 'EMBED_BACKEND=ollama' | Set-Content ".env" }
  Write-Host "Created .env from .env.example"
}

# ---------------------------------------------------------------- Ollama
Step "Pulling Ollama models"
if (Get-Command ollama -ErrorAction SilentlyContinue) {
  ollama pull qwen3:8b-q4_K_M
  ollama pull all-minilm      # fallback embedder (tiny, 46 MB)
} else {
  Write-Warning "Ollama not found. Install it from https://ollama.com/download, then run: ollama pull qwen3:8b-q4_K_M ; ollama pull all-minilm"
}

# ---------------------------------------------------------------- data + indexes
Step "Building DuckDB database and FAISS indexes"
& $py scripts\ingest.py
if ($LASTEXITCODE -ne 0) {
  Write-Warning "Ingest did not finish. If PyTorch is blocked on this PC, re-run with:  .\setup.ps1 -NoTorch"
}

Step "Checking setup"
& $py scripts\check_setup.py

Write-Host "`nAll set. Start the chatbot with:  .\run.ps1   then open http://127.0.0.1:8000" -ForegroundColor Green
