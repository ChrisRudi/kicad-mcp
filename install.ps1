# One-shot installer for the KiCad MCP server (Windows / PowerShell).
# - Verifies KiCad 10 is reachable
# - Registers the server with Claude Code (if `claude` CLI is available)
# - Prints ready-to-paste snippets for all other MCP clients

$ErrorActionPreference = "Stop"

# UTF-8 console so a non-ASCII install path (C:\Users\üser\…) prints and
# round-trips cleanly through the pip log instead of becoming "Sch?ler".
try {
    [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
    $OutputEncoding = [System.Text.UTF8Encoding]::new()
} catch {}

$ScriptDir = Split-Path -Parent $PSCommandPath
$Launcher  = Join-Path $ScriptDir "start_mcp.bat"

# --- 1. Verify KiCad 10 ---
Write-Host ">> Checking KiCad 10 installation..."
$KicadPy = $null
$candidates = @(
    $env:KICAD_PYTHON_PATH,
    "C:\Program Files\KiCad\10.0\bin\python.exe",
    "C:\Program Files\KiCad\9.0\bin\python.exe",
    "D:\Program Files\KiCad\10.0\bin\python.exe"
)
foreach ($c in $candidates) {
    if ($c -and (Test-Path $c)) { $KicadPy = $c; break }
}

if (-not $KicadPy) {
    Write-Error "KiCad 10 nicht gefunden. Bitte installieren oder KICAD_PYTHON_PATH setzen."
    exit 1
}
Write-Host "   OK: $KicadPy"

# --- 2. Install server + deps into a local _deps dir (umlaut-safe) ---
# NOT `pip --user` (fragile under KiCad's bundled Python) and NOT a cmd/batch
# round-trip (folds a non-ASCII path to "Sch?ler"). PowerShell's & passes the
# args to python via CreateProcessW (Unicode-safe); `-X utf8` forces UTF-8
# mode. Deps land in <repo>\_deps, which main.py injects into sys.path (KiCad's
# Python ignores PYTHONPATH), so no env-var dance and no user-site .pth.
Write-Host ">> Installing server + dependencies into _deps (UTF-8, no --user)..."
$DepsDir = Join-Path $ScriptDir "_deps"
$pipLog  = Join-Path $env:TEMP "kicad-mcp-pip.log"
& $KicadPy -X utf8 -m pip install --upgrade --target $DepsDir $ScriptDir *> $pipLog
if ($LASTEXITCODE -ne 0) {
    Write-Host "   FEHLER beim pip-install. Siehe $pipLog" -ForegroundColor Red
    Get-Content $pipLog -Tail 20
    exit 1
}
Write-Host "   OK ($DepsDir)"

# --- 3. Register with Claude Code ---
$claudeCmd = Get-Command claude -ErrorAction SilentlyContinue
if ($claudeCmd) {
    Write-Host ">> Registering with Claude Code (user scope)..."
    & claude mcp remove kicad -s user 2>$null | Out-Null
    & claude mcp add kicad -s user -- cmd /c $Launcher
    Write-Host "   OK"
} else {
    Write-Host ">> Claude Code CLI (``claude``) not found - skipping auto-register."
}

# --- 4. Snippets for other clients ---
$launcherJson = $Launcher -replace '\\','\\'

$snippetStd = @"
{
  "mcpServers": {
    "kicad": {
      "command": "cmd",
      "args": ["/c", "$launcherJson"]
    }
  }
}
"@

$snippetVscode = @"
{
  "servers": {
    "kicad": {
      "type": "stdio",
      "command": "cmd",
      "args": ["/c", "$launcherJson"]
    }
  }
}
"@

Write-Host ""
Write-Host "========================================================================="
Write-Host " For other MCP clients, paste into the respective config file:"
Write-Host " (see docs\MCP_CLIENTS.md for file locations)"
Write-Host "========================================================================="
Write-Host ""
Write-Host "--- Claude Desktop / Cursor / Windsurf / Claude Code (project-scope) ---"
Write-Host $snippetStd
Write-Host ""
Write-Host "--- VS Code (.vscode\mcp.json) ---"
Write-Host $snippetVscode
Write-Host ""
Write-Host "Done."
