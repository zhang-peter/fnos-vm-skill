# Standalone PowerShell launcher for the fnos-vm skill.
$ErrorActionPreference = "Stop"
$script = Join-Path $PSScriptRoot "fnos-vm.py"

$python = $env:FNOS_VM_PYTHON
if (-not $python) {
    $bundled = Join-Path $env:USERPROFILE ".dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe"
    if (Test-Path $bundled) {
        $python = $bundled
    }
}
if (-not $python) {
    $py = Get-Command py -ErrorAction SilentlyContinue
    if ($py) {
        & $py.Source -3 $script @args
        exit $LASTEXITCODE
    }
    $cmd = Get-Command python3 -ErrorAction SilentlyContinue
    if ($cmd) { $python = $cmd.Source }
}
if (-not $python) {
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($cmd) { $python = $cmd.Source }
}
if (-not $python) {
    [Console]::Error.WriteLine("fnos-vm: Python 3.9+ was not found. Set FNOS_VM_PYTHON to python.exe.")
    exit 2
}

& $python $script @args
exit $LASTEXITCODE
