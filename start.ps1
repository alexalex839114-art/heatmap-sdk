param(
    [int]$Port = 8000
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$VenvPath = Join-Path $ProjectRoot ".venv"
$PythonExe = Join-Path $VenvPath "Scripts\python.exe"
$RequirementsPath = Join-Path $ProjectRoot "requirements.txt"
$StampPath = Join-Path $VenvPath ".requirements-installed"

Set-Location $ProjectRoot

function Test-PortAvailable {
    param(
        [int]$CandidatePort
    )

    $Listener = $null
    try {
        $Address = [System.Net.IPAddress]::Parse("127.0.0.1")
        $Listener = [System.Net.Sockets.TcpListener]::new($Address, $CandidatePort)
        $Listener.Start()
        return $true
    }
    catch {
        return $false
    }
    finally {
        if ($null -ne $Listener) {
            $Listener.Stop()
        }
    }
}

function Get-FreePort {
    param(
        [int]$StartPort
    )

    for ($Candidate = $StartPort; $Candidate -lt ($StartPort + 50); $Candidate++) {
        if (Test-PortAvailable -CandidatePort $Candidate) {
            return $Candidate
        }
    }
    throw "No free localhost port found in range $StartPort-$($StartPort + 49)"
}

if (-not (Test-Path $VenvPath)) {
    Write-Host "Creating virtual environment..."
    python -m venv $VenvPath
}

if (-not (Test-Path $PythonExe)) {
    throw "Virtual environment Python not found at $PythonExe"
}

if (-not (Test-Path $StampPath) -or ((Get-Item $RequirementsPath).LastWriteTimeUtc -gt (Get-Item $StampPath -ErrorAction SilentlyContinue).LastWriteTimeUtc)) {
    Write-Host "Installing dependencies..."
    & $PythonExe -m pip install --upgrade pip
    & $PythonExe -m pip install -r $RequirementsPath
    New-Item -ItemType File -Path $StampPath -Force | Out-Null
}

$SelectedPort = Get-FreePort -StartPort $Port
if ($SelectedPort -ne $Port) {
    Write-Host "Port $Port is busy; using $SelectedPort instead."
}

Write-Host "Starting server at http://127.0.0.1:$SelectedPort"
& $PythonExe -m uvicorn app.main:app --host 127.0.0.1 --port $SelectedPort --reload
