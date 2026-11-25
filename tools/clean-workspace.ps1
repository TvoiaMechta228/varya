param([switch]$DryRun)
$ErrorActionPreference = 'Stop'
$workspaceRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$keyPath = Join-Path $workspaceRoot '.release-secrets\release-signing-key.xml'
if (!(Test-Path -LiteralPath $keyPath)) { throw 'Перед очисткой сохраните ключ выпуска в .release-secrets.' }
$app = Join-Path $workspaceRoot 'dist\Varya\Varya.exe'
$versionLine = Select-String -LiteralPath (Join-Path $workspaceRoot 'config.py') -Pattern '^ASSISTANT_VERSION = "([0-9]+\.[0-9]+\.[0-9]+)"$'
if (!$versionLine) { throw 'Не удалось определить версию новой сборки.' }
$version = $versionLine.Matches[0].Groups[1].Value
$installer = Join-Path $workspaceRoot "dist\installer\Varya-$version-x64.msi"
if (!(Test-Path -LiteralPath $app) -or !(Test-Path -LiteralPath $installer)) { throw 'Новая сборка и установщик ещё не готовы. Старые файлы сохранены.' }
$processes = @(Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath })
$removed = [Collections.Generic.List[object]]::new()
$skipped = [Collections.Generic.List[object]]::new()
function Remove-GeneratedPath([string]$relative) {
    $absolute = [IO.Path]::GetFullPath((Join-Path $workspaceRoot $relative))
    if (!$absolute.StartsWith($workspaceRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) { throw "Путь вне проекта: $relative" }
    if (!(Test-Path -LiteralPath $absolute)) { return }
    $resolved = (Resolve-Path -LiteralPath $absolute).Path
    if ($resolved -ne $absolute) { throw "Неожиданный путь: $relative" }
    $item = Get-Item -LiteralPath $absolute -Force
    if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { $skipped.Add(@{path=$relative;reason='ссылка'}); return }
    $running = @($processes | Where-Object { $_.ExecutablePath -eq $absolute -or $_.ExecutablePath.StartsWith($absolute + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase) })
    if ($running) { $skipped.Add(@{path=$relative;reason='используется запущенным приложением'}); return }
    if ($item.PSIsContainer -and (Get-ChildItem -LiteralPath $absolute -Recurse -Force -Attributes ReparsePoint)) { $skipped.Add(@{path=$relative;reason='содержит ссылки'}); return }
    $bytes = if ($item.PSIsContainer) { (Get-ChildItem -LiteralPath $absolute -Recurse -Force -File | Measure-Object Length -Sum).Sum } else { $item.Length }
    if (!$DryRun) { Remove-Item -LiteralPath $absolute -Recurse -Force }
    $removed.Add(@{path=$relative;bytes=[long]$bytes})
}
Push-Location $workspaceRoot
try {
    foreach ($relative in @('build','logs','.pytest_cache','.ruff_cache','__pycache__','tests\__pycache__','legacy\__pycache__','tools\__pycache__','tools\wix','tools\inno','native\Varya.Desktop\bin','native\Varya.Desktop\obj','native\Varya.UpdateChecks\bin','native\Varya.UpdateChecks\obj')) { Remove-GeneratedPath $relative }
    Get-ChildItem -LiteralPath (Join-Path $workspaceRoot 'dist') -Force | Where-Object { $_.Name -notin @('Varya','installer') } | ForEach-Object { Remove-GeneratedPath ('dist\' + $_.Name) }
    foreach ($name in @('Varya.wxs','Varya.wixobj',"Varya-$version-x64.wixpdb")) { Remove-GeneratedPath ('dist\installer\' + $name) }
    $report = @{dry_run=[bool]$DryRun;removed=@($removed.ToArray());skipped=@($skipped.ToArray());bytes=($removed | Measure-Object bytes -Sum).Sum}
    $report | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $PSScriptRoot 'cleanup-report.json') -Encoding utf8
    $report | ConvertTo-Json -Depth 5
} finally { Pop-Location }
