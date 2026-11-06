param([switch]$PrepareOnly, [switch]$CheckOnly)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
try {
    $runtime = Join-Path $PSScriptRoot '.venv/Scripts/python.exe'
    if (-not (Test-Path -LiteralPath $runtime)) {
        if ($CheckOnly) { throw 'Python environment is not installed. Run Start.cmd first.' }
        Write-Host 'Preparing Python environment (first launch)...'
        $candidates = @(
            (Join-Path (Split-Path $PSScriptRoot -Parent) 'Botopros/.venv/Scripts/python.exe'),
            (Join-Path $env:LOCALAPPDATA 'Python/pythoncore-3.14-64/python.exe')
        )
        $bootstrap = $candidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
        if ($bootstrap) { & $bootstrap -m venv (Join-Path $PSScriptRoot '.venv') }
        else {
            $launcher = Get-Command py -ErrorAction SilentlyContinue
            if (-not $launcher) { throw 'Install Python 3.12 or newer from python.org.' }
            & $launcher.Source -m venv (Join-Path $PSScriptRoot '.venv')
        }
        if ($LASTEXITCODE -ne 0) { throw 'Could not create the Python environment.' }
    }
    $requirements = Join-Path $PSScriptRoot 'requirements.txt'
    $stamp = Join-Path $PSScriptRoot '.venv/camp-requirements.sha256'
    $hash = (Get-FileHash -LiteralPath $requirements -Algorithm SHA256).Hash
    if (-not $CheckOnly -and ((-not (Test-Path -LiteralPath $stamp)) -or ((Get-Content -LiteralPath $stamp -Raw).Trim() -ne $hash))) {
        Write-Host 'Installing dependencies (internet needed on first launch)...'
        & $runtime -m pip install --disable-pip-version-check -r $requirements
        if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed. Check the internet connection and try again.' }
        Set-Content -LiteralPath $stamp -Value $hash -Encoding ascii
    }
    & $runtime -m bot.check_config
    if ($LASTEXITCODE -ne 0) { throw 'Check the local .env settings. Never share your bot token.' }
    if ($PrepareOnly -or $CheckOnly) { exit 0 }
    Write-Host 'Starting camp bot. Keep this window open. Press Ctrl+C to stop.'
    & $runtime -m bot.main
    if ($LASTEXITCODE -ne 0) { throw 'Bot stopped with an error. See the message above.' }
} catch {
    Write-Host $_.Exception.Message -ForegroundColor Red
    exit 1
}
