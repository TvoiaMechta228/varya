using System.Diagnostics;
using Varya_Desktop;

/// <summary>Закрытие Вари завершает рабочие процессы и сохраняет отдельный браузер.</summary>
static class JobChecks
{
    public static void Run()
    {
        var info = new ProcessStartInfo(Path.GetFullPath(".venv/Scripts/python.exe"))
        {
            UseShellExecute = false, CreateNoWindow = true,
            RedirectStandardInput = true, RedirectStandardOutput = true
        };
        info.ArgumentList.Add("-u");
        info.ArgumentList.Add("-c");
        info.ArgumentList.Add("import subprocess,sys; input(); a=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)']); b=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)'],creationflags=subprocess.CREATE_BREAKAWAY_FROM_JOB); print(str(a.pid)+','+str(b.pid),flush=True); input()");
        using var parent = Process.Start(info) ?? throw new Exception("Не запустился процесс проверки");
        using var job = new NativeJob(parent);
        Process? external = null;
        try
        {
            parent.StandardInput.WriteLine("start");
            parent.StandardInput.Flush();
            var line = parent.StandardOutput.ReadLineAsync().WaitAsync(TimeSpan.FromSeconds(8)).GetAwaiter().GetResult()
                ?? throw new Exception("Не получены идентификаторы проверки");
            var ids = line.Split(',').Select(int.Parse).ToArray();
            external = Process.GetProcessById(ids[1]);
            job.Dispose();
            if (!parent.WaitForExit(3000)) throw new Exception("Задание не завершило движок");
            try
            {
                using var owned = Process.GetProcessById(ids[0]);
                if (!owned.WaitForExit(3000)) throw new Exception("Рабочий процесс остался запущенным");
            }
            catch (ArgumentException) { }
            external.Refresh();
            if (external.HasExited) throw new Exception("Закрытие задания завершило пользовательский браузер");
            Console.WriteLine("Проверка процессов пройдена: движок и рабочий процесс завершены, отдельный браузер сохранён.");
        }
        finally
        {
            if (external != null) { if (!external.HasExited) external.Kill(); external.Dispose(); }
        }
    }
}
