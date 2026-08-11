# Arranca el daemon del panel. Idempotente: si ya corre, no arranca otro.
param(
    [double]$Fps = 1.0,
    [int]$Brightness = 100,
    [ValidateSet(0, 90, 180, 270)][int]$Rotate = 180,
    [switch]$Force
)
$ErrorActionPreference = 'Stop'
$here = $PSScriptRoot
$pidFile = Join-Path $here 'panel.pid'

if ($Force) { & (Join-Path $here 'stop.ps1') | Out-Null }

if (Test-Path $pidFile) {
    $old = [int](Get-Content $pidFile | Select-Object -First 1)
    if (Get-Process -Id $old -ErrorAction SilentlyContinue) {
        "ya corre (pid $old). Usar -Force para reiniciar."
        return
    }
    Remove-Item $pidFile -Force
}

$py = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $py) { throw 'no encuentro python en el PATH' }
$pyw = $py -replace 'python\.exe$', 'pythonw.exe'
if (-not (Test-Path $pyw)) { $pyw = $py }   # fallback: python.exe con ventana oculta

$args = @('-u', 'panel.py', '--fps', $Fps, '--brightness', $Brightness,
          '--rotate', $Rotate, '--log', 'panel.log')
$p = Start-Process -FilePath $pyw -ArgumentList $args -WorkingDirectory $here `
                   -WindowStyle Hidden -PassThru
$p.Id | Set-Content $pidFile
"daemon arrancado, pid $($p.Id)  (log: $(Join-Path $here 'panel.log'))"
