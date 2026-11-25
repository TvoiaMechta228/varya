param([switch]$Installer)
$ErrorActionPreference = 'Stop'
Push-Location $PSScriptRoot
try {
    $pythonExe = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
    $distRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot 'dist'))
    $outputRoot = Join-Path $distRoot 'Varya'
    $installerRoot = Join-Path $distRoot 'installer'
    & $pythonExe prepare_models.py
    if ($LASTEXITCODE -ne 0) { throw 'Модели не подготовлены' }
    & $pythonExe -m pytest -q
    if ($LASTEXITCODE -ne 0) { throw 'Тесты не прошли' }
    & dotnet run --project native/Varya.UpdateChecks -c Release
    if ($LASTEXITCODE -ne 0) { throw 'Проверки обновлений не прошли' }
    & dotnet run --project native/Varya.UpdateChecks -c Release -- --jobs
    if ($LASTEXITCODE -ne 0) { throw 'Проверка жизненного цикла процессов не прошла' }
    foreach ($targetPath in @($outputRoot, $installerRoot)) {
        if ((Split-Path ([IO.Path]::GetFullPath($targetPath))) -ne $distRoot) { throw 'Недопустимый путь сборки' }
        $inUse = Get-CimInstance Win32_Process | Where-Object {
            $_.ExecutablePath -and $_.ExecutablePath.StartsWith($targetPath + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)
        }
        if ($inUse) { throw "Папка используется запущенным приложением: $targetPath" }
        if (Test-Path -LiteralPath $targetPath) {
            if ((Get-Item -LiteralPath $targetPath -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Выходная папка является ссылкой' }
            if (Get-ChildItem -LiteralPath $targetPath -Recurse -Force -Attributes ReparsePoint) { throw 'В выходной папке найдены ссылки' }
            Remove-Item -LiteralPath $targetPath -Recurse -Force
        }
    }
    & dotnet publish native/Varya.Desktop -c Release -p:Platform=x64 -o $outputRoot
    if ($LASTEXITCODE -ne 0) { throw 'Ошибка сборки WinUI' }
    & $pythonExe -m PyInstaller --noconfirm --distpath dist/native-engine --workpath build/native-engine Varya.Engine.spec
    if ($LASTEXITCODE -ne 0) { throw 'Ошибка сборки движка' }
    New-Item -ItemType Directory -Force "$outputRoot\engine","$outputRoot\models","$outputRoot\tools\llama","$outputRoot\tools\browser" | Out-Null
    Copy-Item -Path dist/native-engine/Varya.Engine/* -Destination "$outputRoot\engine" -Recurse
    Copy-Item -LiteralPath models/whisper-small,models/vosk-model-small-ru-0.22 -Destination "$outputRoot\models" -Recurse
    Copy-Item -Path models/ru_RU-irina-medium.onnx,models/ru_RU-irina-medium.onnx.json,models/qwen2.5-1.5b-instruct-q4_k_m.gguf,models/VOICE_MODEL_CARD -Destination "$outputRoot\models"
    Copy-Item -Path tools/llama/*.exe,tools/llama/*.dll -Destination "$outputRoot\tools\llama"
    Get-ChildItem tools/browser -Directory | Where-Object { $_.Name -ne '.links' } | Copy-Item -Destination "$outputRoot\tools\browser" -Recurse
    Copy-Item README.md,THIRD_PARTY.md -Destination $outputRoot
    if ($Installer) {
        & $pythonExe build_installer.py --source $outputRoot --output $installerRoot
        if ($LASTEXITCODE -ne 0) { throw 'Ошибка сборки MSI' }
        Write-Host "Установщик: $installerRoot"
    }
    Write-Host "Готово: $outputRoot\Varya.exe. Переносите всю папку приложения."
} finally {
    Pop-Location
}
