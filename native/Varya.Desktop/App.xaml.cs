using Microsoft.UI.Xaml;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text;

namespace Varya_Desktop;

/// <summary>Жизненный цикл единственного экземпляра приложения.</summary>
public partial class App : Application
{
    public static MainWindow? Main { get; private set; }
    private Mutex? mutex;
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] private static extern IntPtr FindWindow(string? className, string title);
    [DllImport("user32.dll")] private static extern bool ShowWindow(IntPtr window, int command);
    [DllImport("user32.dll")] private static extern bool SetForegroundWindow(IntPtr window);

    public App()
    {
        InitializeComponent();
        UnhandledException += (_, e) =>
        {
            var folder = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "Varya", "logs");
            try
            {
                Directory.CreateDirectory(folder);
                File.AppendAllText(Path.Combine(folder, "native-crash.log"), DateTime.Now + "\n" + e.Exception + "\n");
            }
            catch (IOException) { }
        };
    }

    protected override void OnLaunched(LaunchActivatedEventArgs args)
    {
        var data = Environment.GetEnvironmentVariable("LOCALAPPDATA") ?? Environment.UserName;
        var identity = Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(data)))[..16];
        mutex = new Mutex(true, "Local\\VaryaNative-" + identity, out bool created);
        if (!created)
        {
            var existing = FindWindow(null, "Варя · Личный ассистент");
            if (existing != IntPtr.Zero) { ShowWindow(existing, 9); SetForegroundWindow(existing); }
            mutex.Dispose();
            Exit();
            return;
        }
        Main = new MainWindow();
        Main.Activate();
    }
}
