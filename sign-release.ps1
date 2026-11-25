param(
    [string]$Directory = "$PSScriptRoot\dist\installer",
    [switch]$InitializeKey
)
$ErrorActionPreference = 'Stop'
$privatePath = Join-Path $PSScriptRoot '.release-secrets\release-signing-key.xml'
$publicPath = Join-Path $PSScriptRoot 'native\Varya.Desktop\Assets\UpdatePublicKey.xml'
$rsa = New-Object System.Security.Cryptography.RSACryptoServiceProvider(3072)
$rsa.PersistKeyInCsp = $false
try {
    if ($InitializeKey) {
        if ((Test-Path -LiteralPath $privatePath) -or (Test-Path -LiteralPath $publicPath)) { throw 'Ключ уже существует. Автоматическая замена запрещена.' }
        New-Item -ItemType Directory -Force (Split-Path $privatePath) | Out-Null
        [IO.File]::WriteAllText($privatePath, $rsa.ToXmlString($true))
        [IO.File]::WriteAllText($publicPath, $rsa.ToXmlString($false))
        Write-Host 'Создан локальный ключ релизов. Приватный файл в .release-secrets нельзя публиковать.'
        return
    }
    if (!(Test-Path -LiteralPath $privatePath)) { throw 'Нет приватного ключа релизов. Восстановите ключ из личной резервной копии.' }
    $rsa.FromXmlString([IO.File]::ReadAllText($privatePath))
    if ($rsa.ToXmlString($false) -ne [IO.File]::ReadAllText($publicPath)) { throw 'Ключ подписи не совпадает со встроенным открытым ключом.' }
    $version = '2.1.1'
    $assets = @(Get-ChildItem -LiteralPath $Directory -File | Where-Object { $_.Extension -in '.msi','.cab' } | Sort-Object Name | ForEach-Object {
        @{ name = $_.Name; size = $_.Length; sha256 = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant(); url = "https://github.com/TvoiaMechta228/varya/releases/download/v$version/$($_.Name)" }
    })
    if (!($assets | Where-Object { $_.name -eq "Varya-$version-x64.msi" })) { throw 'Не найден MSI текущей версии.' }
    $manifest = @{ version = $version; assets = $assets } | ConvertTo-Json -Depth 5
    $bytes = [Text.Encoding]::UTF8.GetBytes($manifest)
    [IO.File]::WriteAllBytes((Join-Path $Directory 'varya-update.json'), $bytes)
    [IO.File]::WriteAllBytes((Join-Path $Directory 'varya-update.sig'), $rsa.SignData($bytes, 'SHA256'))
    Write-Host 'Метаданные подписаны. Это подпись обновлений Вари, а не сертификат издателя Windows.'
} finally { $rsa.Dispose() }
