param([ValidateRange(1024, 65535)][int]$Port = 8001)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $projectRoot 'backend/.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw 'Install the backend environment first; see MCP_ACCESS.md.'
}
$previousEnabled = $env:MCP_ENABLED
$previousHosts = $env:MCP_ALLOWED_HOSTS
try {
    $env:MCP_ENABLED = 'true'
    if (-not $env:MCP_ALLOWED_HOSTS) {
        $env:MCP_ALLOWED_HOSTS = "127.0.0.1:$Port,localhost:$Port"
    }
    Push-Location (Join-Path $projectRoot 'backend')
    try {
        & $pythonPath -m uvicorn app.mcp_server:app --host 127.0.0.1 --port $Port
    } finally {
        Pop-Location
    }
} finally {
    $env:MCP_ENABLED = $previousEnabled
    $env:MCP_ALLOWED_HOSTS = $previousHosts
}
