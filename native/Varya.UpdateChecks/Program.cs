using System.Net;
using System.Security.Cryptography;
using System.Text.Json;
using Varya_Desktop;

if (args.Length == 1 && args[0] == "--jobs")
{
    JobChecks.Run();
    return;
}

if (args.Length == 3)
{
    var release = UpdateService.Verify(await File.ReadAllBytesAsync(args[0]),
        await File.ReadAllBytesAsync(args[1]), await File.ReadAllTextAsync(args[2]));
    Console.WriteLine($"Подпись релиза {release.Version} проверена. Файлов: {release.Assets.Length}.");
    return;
}

using var rsa = RSA.Create(2048);
var content = new byte[] { 1, 2, 3, 4 };
var asset = new UpdateAsset("Varya-2.2.0-x64.msi", content.Length,
    Convert.ToHexString(SHA256.HashData(content)),
    "https://github.com/TvoiaMechta228/varya/releases/download/v2.2.0/Varya-2.2.0-x64.msi");
var manifest = new UpdateManifest("2.2.0", [asset]);
var publicKey = rsa.ToXmlString(false);
UpdateManifest Verify(UpdateManifest value)
{
    var bytes = JsonSerializer.SerializeToUtf8Bytes(value);
    return UpdateService.Verify(bytes, rsa.SignData(bytes, HashAlgorithmName.SHA256, RSASignaturePadding.Pkcs1), publicKey);
}
void Reject(Action action)
{
    try { action(); }
    catch (InvalidDataException) { return; }
    throw new Exception("Недопустимые метаданные приняты");
}
if (Verify(manifest).Version != "2.2.0") throw new Exception("Проверка правильной подписи не прошла");
Reject(() => UpdateService.Verify(JsonSerializer.SerializeToUtf8Bytes(manifest), new byte[256], publicKey));
Reject(() => Verify(manifest with { Assets = [asset with { Name = "../outside.msi" }] }));
Reject(() => Verify(manifest with { Assets = [asset with { Url = "https://example.com/file.msi" }] }));
Reject(() => Verify(manifest with { Assets = [asset, asset] }));
Reject(() => Verify(manifest with { Assets = [asset with { Size = -1 }] }));
var directory = Path.Combine(Path.GetTempPath(), "varya-update-check-" + Guid.NewGuid().ToString("N"));
Directory.CreateDirectory(directory);
try
{
    using var http = new HttpClient(new FakeDownload(content));
    var service = new UpdateService(http);
    var path = await service.Download(Verify(manifest), directory, new Progress<double>(), CancellationToken.None);
    if (!(await File.ReadAllBytesAsync(path)).SequenceEqual(content)) throw new Exception("Файл загрузки повреждён");
    File.Delete(path);
    try
    {
        await service.Download(Verify(manifest with { Assets = [asset with { Sha256 = new string('0', 64) }] }), directory, new Progress<double>(), CancellationToken.None);
        throw new Exception("Повреждённая загрузка принята");
    }
    catch (InvalidDataException) { }
    if (Directory.GetFiles(directory, "*", SearchOption.AllDirectories).Length != 0) throw new Exception("Остались непроверенные файлы");
}
finally { Directory.Delete(directory, true); }
Console.WriteLine("8 проверок обновления прошли: подпись, пути, адрес, дубли, размер, загрузка, SHA-256 и очистка.");

sealed class FakeDownload(byte[] bytes) : HttpMessageHandler
{
    protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken cancel)
        => Task.FromResult(new HttpResponseMessage(HttpStatusCode.OK) { Content = new ByteArrayContent(bytes) });
}
