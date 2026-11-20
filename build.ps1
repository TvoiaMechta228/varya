$ErrorActionPreference = 'Stop'
Push-Location $PSScriptRoot
try {
    $pythonExe = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $pythonExe)) {
        throw 'Сначала создайте .venv и установите requirements-build.txt'
    }
    & $pythonExe -m pytest -q
    if ($LASTEXITCODE -ne 0) { throw 'Тесты не прошли' }
    & $pythonExe -m PyInstaller --noconfirm --clean Varya.spec
    if ($LASTEXITCODE -ne 0) { throw 'Ошибка сборки EXE' }
    Write-Host 'Готово: dist\Varya.exe'
} finally {
    Pop-Location
}
