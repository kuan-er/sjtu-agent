[CmdletBinding()]
param(
    [string]$Python,
    [switch]$SkipPlaywright,
    [switch]$NoSetup,
    [switch]$ForceRecreateVenv,
    [switch]$Help
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Show-Help {
    @"
SJTU Agent installer (PowerShell)

Usage:
  .\install.ps1 [options]

Options:
  -Python <cmd>          Specify the Python executable (default: auto-detect py / python / python3)
  -SkipPlaywright        Skip Playwright Chromium installation
  -NoSetup               Do not run sjtu-agent setup after installation
  -ForceRecreateVenv     Force recreate the .venv virtual environment
  -Help                  Show this help message
"@
}

function Write-Log {
    param([string]$Message)

    Write-Host ""
    Write-Host "[sjtu-agent-install] $Message"
}

function Resolve-PythonCommand {
    param([string]$RequestedPython)

    if ($RequestedPython) {
        if (-not (Get-Command $RequestedPython -ErrorAction SilentlyContinue)) {
            throw "Python executable not found: $RequestedPython"
        }
        return [pscustomobject]@{
            Executable = $RequestedPython
            PrefixArgs = @()
        }
    }

    $candidates = @(
        @{ Executable = "py";      PrefixArgs = @("-3") },
        @{ Executable = "python";  PrefixArgs = @() },
        @{ Executable = "python3"; PrefixArgs = @() }
    )

    foreach ($candidate in $candidates) {
        if (-not (Get-Command $candidate.Executable -ErrorAction SilentlyContinue)) {
            continue
        }

        try {
            & $candidate.Executable @($candidate.PrefixArgs + @("--version")) *> $null
            if ($LASTEXITCODE -eq 0) {
                return [pscustomobject]@{
                    Executable = $candidate.Executable
                    PrefixArgs = $candidate.PrefixArgs
                }
            }
        }
        catch {
        }
    }

    throw "No usable Python found. Please install Python 3.11+ or specify one with -Python."
}

function Invoke-PythonCommand {
    param(
        [Parameter(Mandatory = $true)]
        [pscustomobject]$PythonCommand,
        [Parameter(Mandatory = $true)]
        [string[]]$Arguments,
        [Parameter(Mandatory = $true)]
        [string]$ErrorMessage
    )

    & $PythonCommand.Executable @($PythonCommand.PrefixArgs + $Arguments)
    if ($LASTEXITCODE -ne 0) {
        throw $ErrorMessage
    }
}

function Invoke-ExternalCommand {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Executable,
        [Parameter(Mandatory = $true)]
        [string[]]$Arguments,
        [Parameter(Mandatory = $true)]
        [string]$ErrorMessage
    )

    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw $ErrorMessage
    }
}

if ($Help) {
    Show-Help
    exit 0
}

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
# 脚本在 install\ 下，项目根目录是它的父目录
$ProjectDir = Split-Path -Parent $ScriptDir

if (-not (Test-Path (Join-Path $ProjectDir "pyproject.toml"))) {
    throw "Please run this script from the repository root directory."
}

$PythonCommand = Resolve-PythonCommand -RequestedPython $Python

Invoke-PythonCommand -PythonCommand $PythonCommand -Arguments @(
    "-c",
    "import sys; sys.exit('Python 3.11 or higher is required (browser-use needs 3.11+).') if sys.version_info < (3, 11) else None"
) -ErrorMessage "Python version check failed."

# ── 确保 uv 可用（未装则自动安装，2026 标准，比 pip 快一个数量级）──────────
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Log "未检测到 uv，正在安装…"
    try {
        winget install --id astral-sh.uv -e --accept-source-agreements --accept-package-agreements 2>$null
    } catch {
    }
    if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
        # fallback: 官方 PowerShell 安装脚本
        try { Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression } catch {}
    }
    if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
        throw "uv 安装失败，请手动安装: winget install astral-sh.uv"
    }
}

$VenvDir = Join-Path $ProjectDir ".venv"
$VenvPython = Join-Path $VenvDir "Scripts\python.exe"

if ($ForceRecreateVenv -and (Test-Path $VenvDir)) {
    Write-Log "Recreating virtual environment: $VenvDir"
    Remove-Item -Recurse -Force $VenvDir
}

if ((Test-Path $VenvDir) -and -not (Test-Path $VenvPython)) {
    Write-Log "Detected broken virtual environment, recreating: $VenvDir"
    Remove-Item -Recurse -Force $VenvDir
}

if (-not (Test-Path $VenvDir)) {
    Write-Log "Creating virtual environment (uv): $VenvDir"
    $PythonPath = (Get-Command $PythonCommand.Executable).Source
    uv venv $VenvDir --python $PythonPath
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to create virtual environment with uv."
    }
}

Write-Log "Installing SJTU Agent (uv)"
uv pip install -e $ProjectDir --python $VenvPython
if ($LASTEXITCODE -ne 0) {
    throw "Failed to install SJTU Agent with uv."
}

# Windows 上 editable install 的 .pth 文件偶尔会失效或丢失，
# 手动写一个兜底 .pth 确保 sjtu_agent 包始终可被找到
Write-Log "Writing path file for editable install (Windows fallback)"
try {
    $SitePackages = & $VenvPython -c "import site; print(site.getsitepackages()[0])" 2>$null
    if ($SitePackages) {
        $PthFile = Join-Path $SitePackages "sjtu_agent_editable_path.pth"
        $ProjectDir | Set-Content -Path $PthFile -Encoding UTF8
        Write-Host "  Wrote $PthFile"
    }
} catch {
    Write-Host "  (Could not write .pth file, this is non-fatal)"
}

if (-not $SkipPlaywright) {
    Write-Log "Installing Playwright Chromium"
    Invoke-ExternalCommand -Executable $VenvPython -Arguments @("-m", "playwright", "install", "chromium") -ErrorMessage "Failed to install Playwright Chromium."
}

# Add .venv\Scripts to the current user's PATH (persistent, user scope)
$VenvScripts = Join-Path $VenvDir "Scripts"
$CurrentUserPath = [System.Environment]::GetEnvironmentVariable("PATH", "User")
if ($CurrentUserPath -notlike "*$VenvScripts*") {
    Write-Log "Adding $VenvScripts to user PATH"
    [System.Environment]::SetEnvironmentVariable(
        "PATH",
        "$VenvScripts;$CurrentUserPath",
        "User"
    )
    # Also update PATH for the current session so sjtu-agent works immediately
    $env:PATH = "$VenvScripts;$env:PATH"
    Write-Host ""
    Write-Host "Added to PATH: $VenvScripts"
    Write-Host "(Effective immediately in this session; new terminals will also pick it up.)"
} else {
    Write-Host ""
    Write-Host "$VenvScripts is already in PATH."
}

if (-not $NoSetup) {
    Write-Log "Launching sjtu-agent setup"
    & $VenvPython -m sjtu_agent setup
    $SetupExit = $LASTEXITCODE
    Write-Host ""
    Write-Host "=========================================="
    Write-Host "If 'sjtu-agent' is not recognized in a new terminal,"
    Write-Host "please close and reopen PowerShell so the PATH update takes effect."
    Write-Host "You can also run directly without PATH:"
    Write-Host "  $VenvPython -m sjtu_agent"
    Write-Host "  $VenvPython -m sjtu_agent web"
    Write-Host "=========================================="
    exit $SetupExit
}

Write-Host ""
Write-Host "Installation complete."
Write-Host ""
Write-Host "IMPORTANT: If 'sjtu-agent' is not recognized, close and reopen PowerShell."
Write-Host "You can also run directly without PATH:"
Write-Host "  $VenvPython -m sjtu_agent"
Write-Host "  $VenvPython -m sjtu_agent web"
Write-Host ""
Write-Host "Or after reopening a terminal:"
Write-Host "  sjtu-agent"
Write-Host "  sjtu-agent setup"
Write-Host "  sjtu-agent update"
