using System.Diagnostics;
using System.Text;
using System.Text.Json;

namespace Varya_Desktop;

/// <summary>Неблокирующий канал к отдельному процессу голосовых исполнителей.</summary>
public sealed class EngineClient : IDisposable
{
    private Process? process;
    private readonly SemaphoreSlim inputLock = new(1, 1);
    private bool disposed;
    private NativeJob? job;
    public event Action<string, JsonElement>? Received;
    public string Home { get; }
    public string DataPath { get; private set; } = Path.Combine(Environment.GetEnvironmentVariable("LOCALAPPDATA") ?? Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "Varya");

    public EngineClient()
    {
        Home = Environment.GetEnvironmentVariable("VARYA_HOME") ?? AppContext.BaseDirectory;
    }

    public void Start()
    {
        var packaged = Path.Combine(Home, "engine", "Varya.Engine.exe");
        if (!File.Exists(packaged) && !File.Exists(Path.Combine(Home, ".venv", "Scripts", "python.exe")))
            throw new IOException("В папке приложения отсутствует движок. Распакуй всю папку Вари, включая engine и models.");
        var info = new ProcessStartInfo
        {
            FileName = File.Exists(packaged) ? packaged : Path.Combine(Home, ".venv", "Scripts", "python.exe"),
            WorkingDirectory = Home, UseShellExecute = false, CreateNoWindow = true,
            RedirectStandardInput = true, RedirectStandardOutput = true, RedirectStandardError = true,
            StandardInputEncoding = new UTF8Encoding(false), StandardOutputEncoding = Encoding.UTF8,
            StandardErrorEncoding = Encoding.UTF8
        };
        if (!File.Exists(packaged)) { info.ArgumentList.Add("-u"); info.ArgumentList.Add("engine_host.py"); }
        info.Environment["VARYA_HOME"] = Home;
        info.Environment["PYTHONUTF8"] = "1";
        info.Environment["OMP_NUM_THREADS"] = "2";
        process = Process.Start(info) ?? throw new IOException("Не удалось запустить движок");
        job = new NativeJob(process);
        _ = ReadEvents(process);
        _ = ReadErrors(process);
    }

    private async Task ReadEvents(Process owner)
    {
        try
        {
            while (await owner.StandardOutput.ReadLineAsync() is { } line)
            {
                if (line.Length > 200000) continue;
                try
                {
                    using var document = JsonDocument.Parse(line);
                    var root = document.RootElement;
                    var kind = root.GetProperty("kind").GetString() ?? "";
                    var value = root.GetProperty("value").Clone();
                    if (kind == "ready" && value.TryGetProperty("data", out var path)) DataPath = path.GetString() ?? DataPath;
                    Received?.Invoke(kind, value);
                }
                catch (JsonException) { }
            }
            await owner.WaitForExitAsync();
            if (!disposed) Received?.Invoke("fatal", JsonSerializer.SerializeToElement("Движок остановился. Его можно перезапустить в настройках."));
        }
        catch (Exception error) when (error is IOException or InvalidOperationException or ObjectDisposedException)
        {
            if (!disposed) Received?.Invoke("fatal", JsonSerializer.SerializeToElement("Потеряна связь с движком. Открой настройки для перезапуска."));
        }
    }

    private async Task ReadErrors(Process owner)
    {
        try
        {
            Directory.CreateDirectory(Path.Combine(DataPath, "logs"));
            var path = Path.Combine(DataPath, "logs", "native-engine.log");
            if (File.Exists(path) && new FileInfo(path).Length > 1000000) File.Move(path, path + ".old", true);
            await using var log = new StreamWriter(path, true, Encoding.UTF8) { AutoFlush = true };
            while (await owner.StandardError.ReadLineAsync() is { } line) await log.WriteLineAsync(line);
        }
        catch (Exception error) when (error is IOException or ObjectDisposedException or InvalidOperationException) { }
    }

    public async Task SendAsync(object command)
    {
        await inputLock.WaitAsync();
        try
        {
            if (disposed || process is null || process.HasExited) throw new IOException("Движок недоступен. Перезапусти его в настройках.");
            await process.StandardInput.WriteLineAsync(JsonSerializer.Serialize(command));
            await process.StandardInput.FlushAsync();
        }
        finally { inputLock.Release(); }
    }

    public long MemoryBytes()
    {
        try { process?.Refresh(); return process?.HasExited == false ? process.WorkingSet64 : 0; }
        catch (InvalidOperationException) { return 0; }
    }

    public void Dispose()
    {
        if (disposed) return;
        disposed = true;
        if (process != null)
        {
            try
            {
                if (!process.HasExited)
                {
                    process.StandardInput.WriteLine("{\"op\":\"close\"}");
                    process.StandardInput.Flush();
                    if (!process.WaitForExit(2000)) process.Kill(true);
                }
            }
            catch (Exception error) when (error is InvalidOperationException or IOException or System.ComponentModel.Win32Exception) { }
            process.Dispose();
        }
        job?.Dispose();
    }
}
