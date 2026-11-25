using Microsoft.UI.Xaml;
using Windows.Graphics;

namespace Varya_Desktop;

/// <summary>Нативное окно и завершение дерева процессов помощницы.</summary>
public sealed partial class MainWindow : Window
{
    public MainWindow()
    {
        InitializeComponent();
        Title = "Варя · Личный ассистент";
        ExtendsContentIntoTitleBar = true;
        SetTitleBar(AppTitleBar);
        AppWindow.TitleBar.ButtonForegroundColor = Windows.UI.Color.FromArgb(255, 230, 237, 240);
        AppWindow.TitleBar.ButtonInactiveForegroundColor = Windows.UI.Color.FromArgb(255, 165, 181, 188);
        AppWindow.TitleBar.ButtonBackgroundColor = Windows.UI.Color.FromArgb(0, 0, 0, 0);
        AppWindow.TitleBar.ButtonHoverBackgroundColor = Windows.UI.Color.FromArgb(255, 53, 64, 71);
        AppWindow.Resize(new SizeInt32(1160, 820));
        var icon = Path.Combine(AppContext.BaseDirectory, "Assets", "AppIcon.ico");
        if (File.Exists(icon)) AppWindow.SetIcon(icon);
        RootFrame.Navigate(typeof(MainPage));
        Closed += (_, _) => (RootFrame.Content as MainPage)?.Shutdown();
    }

    public void Restore()
    {
        AppWindow.Show();
        if (AppWindow.Presenter is Microsoft.UI.Windowing.OverlappedPresenter presenter) presenter.Restore();
        Activate();
    }
}
