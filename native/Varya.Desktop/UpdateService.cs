using System.Net;
using System.Net.Http.Headers;
using System.Security.Cryptography;
using System.Text.Json;
using System.Text.RegularExpressions;

namespace Varya_Desktop;

public sealed record UpdateAsset(string Name, long Size, string Sha256, string Url);
public sealed record UpdateManifest(string Version, UpdateAsset[] Assets);

/// <summary>Проверенные подписью пакеты GitHub Releases, установка через Windows Installer.</summary>
public sealed class UpdateService
{
    public const string Repository = "TvoiaMechta228/varya";
    private static readonly JsonSerializerOptions Json = new() { PropertyNameCaseInsensitive = true };
    private readonly HttpClient client;

    public UpdateService(HttpClient? http = null)
    {
        client = http ?? new HttpClient { Timeout = Timeout.InfiniteTimeSpan };
        client.DefaultRequestHeaders.UserAgent.Add(new ProductInfoHeaderValue("Varya", "2.1"));
    }

    public static UpdateManifest Verify(byte[] payload, byte[] signature, string publicKey)
    {
        using var rsa = RSA.Create();
        rsa.FromXmlString(publicKey);
        if (!rsa.VerifyData(payload, signature, HashAlgorithmName.SHA256, RSASignaturePadding.Pkcs1))
            throw new InvalidDataException("Не совпала подпись обновления.");
        var manifest = JsonSerializer.Deserialize<UpdateManifest>(payload, Json)
            ?? throw new InvalidDataException("Пустое описание обновления.");
        if (!Regex.IsMatch(manifest.Version ?? "", @"^\d{1,3}\.\d{1,3}\.\d{1,5}$")
            || !System.Version.TryParse(manifest.Version, out _)
            || manifest.Assets is null || manifest.Assets.Length is < 1 or > 10)
            throw new InvalidDataException("Некорректное описание обновления.");
        var seen = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        foreach (var asset in manifest.Assets)
        {
            if (asset is null || !Regex.IsMatch(asset.Name ?? "", @"^(Varya-[0-9.]+-x64\.msi|varya[0-9]+\.cab)$")
                || !seen.Add(asset.Name!) || asset.Size is <= 0 or > 8_000_000_000
                || !Regex.IsMatch(asset.Sha256 ?? "", "^[a-fA-F0-9]{64}$")
                || asset.Url != $"https://github.com/{Repository}/releases/download/v{manifest.Version}/{asset.Name}")
                throw new InvalidDataException("Некорректный файл обновления.");
        }
        if (manifest.Assets.Count(a => a.Name.EndsWith(".msi", StringComparison.Ordinal)) != 1
            || !manifest.Assets.Any(a => a.Name == $"Varya-{manifest.Version}-x64.msi")
            || manifest.Assets.Sum(a => a.Size) > 16_000_000_000)
            throw new InvalidDataException("Отсутствует установщик нужной версии.");
        return manifest;
    }

    private async Task<byte[]> SmallFile(string url, int limit, CancellationToken cancel)
    {
        using var response = await client.GetAsync(url, HttpCompletionOption.ResponseHeadersRead, cancel);
        response.EnsureSuccessStatusCode();
        await using var stream = await response.Content.ReadAsStreamAsync(cancel);
        using var output = new MemoryStream();
        var bytes = new byte[8192];
        int read;
        while ((read = await stream.ReadAsync(bytes, cancel)) > 0)
        {
            if (output.Length + read > limit) throw new InvalidDataException("Ответ сервера превысил допустимый размер.");
            output.Write(bytes, 0, read);
        }
        return output.ToArray();
    }

    public async Task<UpdateManifest?> Check(CancellationToken cancel)
    {
        var url = $"https://api.github.com/repos/{Repository}/releases/latest";
        byte[] payload;
        try { payload = await SmallFile(url, 500_000, cancel); }
        catch (HttpRequestException error) when (error.StatusCode == HttpStatusCode.NotFound) { return null; }
        using var release = JsonDocument.Parse(payload);
        var root = release.RootElement;
        if (root.GetProperty("draft").GetBoolean() || root.GetProperty("prerelease").GetBoolean()) return null;
        var tag = root.GetProperty("tag_name").GetString();
        if (tag is null || !Regex.IsMatch(tag, @"^v\d{1,3}\.\d{1,3}\.\d{1,5}$")) return null;
        var assets = root.GetProperty("assets").EnumerateArray().ToArray();
        string AssetUrl(string name)
        {
            var asset = assets.SingleOrDefault(a => a.GetProperty("name").GetString() == name);
            if (asset.ValueKind == JsonValueKind.Undefined) throw new InvalidDataException("Релиз ещё не содержит подписанных файлов обновления.");
            var expected = $"https://github.com/{Repository}/releases/download/{tag}/{name}";
            if (asset.GetProperty("browser_download_url").GetString() != expected)
                throw new InvalidDataException("Некорректный адрес обновления.");
            return expected;
        }
        var metadata = await SmallFile(AssetUrl("varya-update.json"), 100_000, cancel);
        var signature = await SmallFile(AssetUrl("varya-update.sig"), 1024, cancel);
        using var resource = typeof(UpdateService).Assembly.GetManifestResourceStream("VaryaUpdateKey")
            ?? throw new InvalidDataException("В сборке отсутствует ключ обновления.");
        using var reader = new StreamReader(resource);
        var manifest = Verify(metadata, signature, await reader.ReadToEndAsync(cancel));
        if ("v" + manifest.Version != tag) throw new InvalidDataException("Версия подписи не соответствует релизу.");
        var current = typeof(UpdateService).Assembly.GetName().Version ?? new Version(2, 1, 0);
        return new Version(manifest.Version) > current ? manifest : null;
    }

    public async Task<string> Download(UpdateManifest manifest, string dataPath, IProgress<double> progress, CancellationToken cancel)
    {
        var directory = Path.Combine(dataPath, "updates", manifest.Version);
        Directory.CreateDirectory(directory);
        var total = manifest.Assets.Sum(a => a.Size);
        if (new DriveInfo(Path.GetPathRoot(Path.GetFullPath(directory))!).AvailableFreeSpace < total * 2)
            throw new IOException("Недостаточно места для загрузки и установки обновления.");
        long completed = 0;
        foreach (var asset in manifest.Assets)
        {
            var final = Path.Combine(directory, asset.Name);
            var temporary = final + ".partial-" + Guid.NewGuid().ToString("N");
            try
            {
                using var response = await client.GetAsync(asset.Url, HttpCompletionOption.ResponseHeadersRead, cancel);
                response.EnsureSuccessStatusCode();
                await using var input = await response.Content.ReadAsStreamAsync(cancel);
                using var hash = IncrementalHash.CreateHash(HashAlgorithmName.SHA256);
                long size = 0;
                await using (var output = new FileStream(temporary, FileMode.CreateNew, FileAccess.Write, FileShare.None, 131072, true))
                {
                    var buffer = new byte[131072];
                    int read;
                    while ((read = await input.ReadAsync(buffer, cancel)) > 0)
                    {
                        size += read;
                        if (size > asset.Size) throw new InvalidDataException("Размер файла обновления изменился.");
                        hash.AppendData(buffer, 0, read);
                        await output.WriteAsync(buffer.AsMemory(0, read), cancel);
                        progress.Report((completed + size) * 100.0 / total);
                    }
                }
                if (size != asset.Size || !Convert.ToHexString(hash.GetHashAndReset()).Equals(asset.Sha256, StringComparison.OrdinalIgnoreCase))
                    throw new InvalidDataException("Контрольная сумма обновления не совпала.");
                cancel.ThrowIfCancellationRequested();
                File.Move(temporary, final, true);
                completed += size;
            }
            finally { if (File.Exists(temporary)) File.Delete(temporary); }
        }
        return Path.Combine(directory, manifest.Assets.Single(a => a.Name.EndsWith(".msi", StringComparison.Ordinal)).Name);
    }
}
