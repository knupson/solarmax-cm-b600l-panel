#Requires -Version 5.1
<#
.SYNOPSIS
    Installs the Solarmax CM-B600L panel driver and leaves its icon in the tray.

.DESCRIPTION
    Checks Python, installs the Python dependencies, runs the diagnostic, renders a
    test frame, registers the logon task and starts the tray.

.PARAMETER Check
    Runs the checks and stops. Registers nothing, installs nothing, starts nothing.

.PARAMETER ProfilePath
    The layout to install. Defaults to Apex. (Named ProfilePath and not Profile
    because $Profile is an automatic PowerShell variable.)

.PARAMETER NoAutostart
    Installs the dependencies but does not register the logon task.

.PARAMETER Yes
    Answers yes to the confirmation. For unattended runs.

.EXAMPLE
    .\install.ps1
    .\install.ps1 -Check
    .\install.ps1 -ProfilePath vmaxpanel\profiles\embers.json
#>
[CmdletBinding()]
param(
    [switch]$Check,
    [string]$ProfilePath = "vmaxpanel\profiles\apex.json",
    [switch]$NoAutostart,
    [switch]$Yes
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$paso = 0
function Paso([string]$texto) {
    $script:paso++
    Write-Host ""
    Write-Host "[$script:paso] $texto" -ForegroundColor Cyan
}
function Ok([string]$texto)    { Write-Host "    $texto" -ForegroundColor Green }
function Aviso([string]$texto) { Write-Host "    $texto" -ForegroundColor Yellow }
function Fatal([string]$texto) {
    Write-Host ""
    Write-Host "  $texto" -ForegroundColor Red
    Write-Host ""
    exit 1
}

Write-Host ""
Write-Host "  Solarmax CM-B600L panel driver" -ForegroundColor White
Write-Host "  ------------------------------"

# --- Windows -----------------------------------------------------------------

if (-not ($IsWindows -or $env:OS -eq "Windows_NT")) {
    Fatal "This panel is driven through Windows WMI and a Windows serial port. Windows only."
}

# --- Python ------------------------------------------------------------------

Paso "Looking for Python"

# The interpreter is invoked by its FULL PATH, never by the name on PATH: a
# function named after the executable shadows it, and `& "py"` would call the
# wrapper below instead of Python, recursing until the stack gives out.
$pythonExe = $null
$pythonArgs = @()

foreach ($candidato in @(
    @{ nombre = "py";     args = @("-3") },
    @{ nombre = "python"; args = @() }
)) {
    $cmd = Get-Command $candidato.nombre -CommandType Application -ErrorAction SilentlyContinue |
           Select-Object -First 1
    if (-not $cmd) { continue }
    try {
        $v = & $cmd.Source @($candidato.args + @(
            "-c", "import sys; print('%d.%d' % sys.version_info[:2])")) 2>$null
    } catch { continue }
    if ($LASTEXITCODE -ne 0 -or -not $v) { continue }
    $partes = "$v".Trim().Split(".")
    if ([int]$partes[0] -eq 3 -and [int]$partes[1] -ge 10) {
        $pythonExe = $cmd.Source
        $pythonArgs = $candidato.args
        Ok "Python $("$v".Trim()) ($($cmd.Source))"
        break
    }
    Aviso "Python $("$v".Trim()) found at $($cmd.Source): too old, 3.10 or newer is needed"
}

if (-not $pythonExe) {
    Write-Host ""
    Write-Host "  Python 3.10 or newer is not installed." -ForegroundColor Red
    Write-Host ""
    Write-Host "  Install it and run this script again:"
    Write-Host "      winget install Python.Python.3.13" -ForegroundColor White
    Write-Host ""
    Write-Host "  or download it from https://www.python.org/downloads/"
    Write-Host "  Tick 'Add python.exe to PATH' in the installer."
    Write-Host ""
    exit 1
}

function Invoke-Py {
    & $pythonExe @($pythonArgs + $args)
}

# --- dependencies ------------------------------------------------------------

Paso "Installing the Python dependencies"

if (-not (Test-Path "requirements.txt")) {
    Fatal "requirements.txt is not here. Run this script from the folder it came in."
}

Invoke-Py -m pip install --quiet --upgrade pip
Invoke-Py -m pip install --quiet -r requirements.txt
if ($LASTEXITCODE -ne 0) { Fatal "pip could not install the dependencies (see the output above)." }
Ok "psutil, pyserial and pillow are in place"

# --- diagnostic --------------------------------------------------------------

Paso "Checking the hardware and the sensors"

if (-not (Test-Path $ProfilePath)) {
    Fatal "The layout $ProfilePath does not exist."
}

# The layout being installed, not the default one: without --profile the
# diagnostic reports on a different file from the one about to be registered.
Invoke-Py -m vmaxpanel --profile $ProfilePath --diagnose
$diagnostico = $LASTEXITCODE

Write-Host ""
if ($diagnostico -ne 0) {
    Aviso "Something marked MISSING above blocks installation. Fix it and run this again."
    Aviso "Anything marked optional is safe to ignore: the panel runs without it."
    if (-not $Check) { exit 1 }
} else {
    Ok "Everything needed is present"
}

# --- test frame --------------------------------------------------------------

Paso "Rendering a test frame"

$png = Join-Path $env:TEMP "vmaxpanel-install-preview.png"
Invoke-Py -m vmaxpanel --profile $ProfilePath --save $png
if ($LASTEXITCODE -ne 0) { Fatal "The layout could not be rendered (see the output above)." }
Ok "Written to $png"
Ok "Open it to see exactly what the panel will show."

if ($Check) {
    Write-Host ""
    Write-Host "  Checks done. Nothing was installed (-Check)." -ForegroundColor White
    Write-Host ""
    exit 0
}

# --- autostart ---------------------------------------------------------------

if ($NoAutostart) {
    Write-Host ""
    Write-Host "  Dependencies installed. The logon task was not registered (-NoAutostart)." -ForegroundColor White
    Write-Host ""
    exit 0
}

Paso "Registering it to start with Windows"

$esAdmin = ([Security.Principal.WindowsPrincipal] `
            [Security.Principal.WindowsIdentity]::GetCurrent() `
           ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

if (-not $esAdmin) {
    Write-Host ""
    Write-Host "  This step needs an administrator console." -ForegroundColor Yellow
    Write-Host "  The panel reads temperatures and disk health, and those need elevation."
    Write-Host ""
    Write-Host "  Right-click PowerShell, choose 'Run as administrator', then:"
    Write-Host "      cd $PSScriptRoot" -ForegroundColor White
    Write-Host "      .\install.ps1" -ForegroundColor White
    Write-Host ""
    exit 1
}

if (-not $Yes) {
    Write-Host ""
    Write-Host "  This registers a scheduled task named PanelVitals that starts the panel"
    Write-Host "  every time you log in, with administrator rights."
    Write-Host ""
    $r = Read-Host "  Register it? [Y/n]"
    if ($r -and $r -notmatch '^(y|yes|s|si)$') {
        Write-Host ""
        Write-Host "  Nothing was registered. Start it by hand whenever you want:" -ForegroundColor White
        Write-Host "      .\install.ps1 -NoAutostart" -ForegroundColor White
        Write-Host ""
        exit 0
    }
}

Invoke-Py -m vmaxpanel --profile $ProfilePath --install
if ($LASTEXITCODE -ne 0) { Fatal "The task could not be registered (see the output above)." }
Ok "Task PanelVitals registered"

# --- start it ----------------------------------------------------------------

Paso "Starting it"

Start-ScheduledTask -TaskName "PanelVitals"
Start-Sleep -Seconds 4

Invoke-Py -m vmaxpanel --status
$estado = $LASTEXITCODE

Write-Host ""
if ($estado -eq 0) {
    Write-Host "  Done. The panel is drawing." -ForegroundColor Green
} else {
    Write-Host "  Installed, but it is not drawing yet." -ForegroundColor Yellow
    Write-Host "  If the panel is unplugged or LCD Control is holding the port, it keeps retrying."
}

Write-Host ""
Write-Host "  The icon is in your tray, next to the clock." -ForegroundColor White
Write-Host "  Right-click it for the menu: pause, change the layout, open the editor."
Write-Host "  Windows hides new tray icons by default - click the arrow to see it,"
Write-Host "  and drag it out to keep it visible."
Write-Host ""
