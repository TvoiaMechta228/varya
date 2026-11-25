using System.Diagnostics;
using System.Text.Json;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Input;
using Microsoft.UI.Xaml.Media;
using Windows.System;

namespace Varya_Desktop;

/// <summary>Окно повседневного диалога с отдельным процессом исполнителей.</summary>
public sealed partial class MainPage : Page
{
    private EngineClient? engine;
    private bool initialized, ready, busy, listening;
    private int choiceVersion;
    private TextBlock? draft;
    private readonly DispatcherTimer resourceTimer = new() { Interval = TimeSpan.FromSeconds(5) };
    private readonly Dictionary<string, (Border Card, TextBlock Time)> timerCards = new();
    private int? selectedMicrophone;
    private long engineMemory;
    private readonly UpdateService updater = new();
    private UpdateManifest? update;
    private CancellationTokenSource? updateCancel;
    private bool checkedUpdates;

    public MainPage()
    {
        InitializeComponent();
        Input.AddHandler(UIElement.KeyDownEvent, new KeyEventHandler(InputKeyDown), true);
        Loaded += (_, _) =>
        {
            if (initialized) return;
            initialized = true;
            StartEngine();
            resourceTimer.Tick += (_, _) =>
            {
                using var process = Process.GetCurrentProcess();
                ResourceText.Text = $"Приложение и движок · {(process.WorkingSet64 + (engineMemory > 0 ? engineMemory : engine?.MemoryBytes() ?? 0)) / 1048576} МиБ";
            };
            resourceTimer.Start();
        };
    }

    private void StartEngine()
    {
        ready = false;
        SetBusy(true);
        try
        {
            engine = new EngineClient();
            var owner = engine;
            owner.Received += (kind, value) => DispatcherQueue.TryEnqueue(() =>
            {
                if (engine == owner) Receive(kind, value);
            });
            engine.Start();
        }
        catch (Exception error)
        {
            ShowNotice(error.Message, true);
            SetBusy(false);
        }
    }

    public void Shutdown()
    {
        updateCancel?.Cancel();
        resourceTimer.Stop();
        engine?.Dispose();
        engine = null;
    }

    private async Task Request(object command)
    {
        try
        {
            if (engine is null) throw new IOException("Движок ещё не запущен");
            await engine.SendAsync(command);
        }
        catch (Exception error) when (error is IOException or InvalidOperationException)
        {
            SetBusy(false);
            ShowNotice(error.Message, true);
        }
    }

    private void SetBusy(bool value)
    {
        busy = value;
        SendButton.IsEnabled = ready && !busy;
        MicButton.IsEnabled = ready && (!busy || listening);
        StopButton.IsEnabled = ready && (busy || listening);
        SaveSettingsButton.IsEnabled = ready && !busy;
        PreviewButton.IsEnabled = ready && !busy;
        SaveNoteButton.IsEnabled = ready && !busy;
        InstallUpdateButton.IsEnabled = update != null && !busy && updateCancel == null;
        SaveMailButton.IsEnabled = ready && !busy;
        DevicesButton.IsEnabled = ready && !busy;
        RestartButton.IsEnabled = !busy || !ready;
        foreach (var child in Choices.Children.OfType<Button>()) child.IsEnabled = !busy;
    }

    private void ShowNotice(string text, bool error = false)
    {
        Notice.Message = text;
        Notice.Severity = error ? InfoBarSeverity.Error : InfoBarSeverity.Informational;
        Notice.IsOpen = true;
    }

    private TextBlock Turn(string role, string text)
    {
        Welcome.Visibility = Visibility.Collapsed;
        var content = new StackPanel { Spacing = 6 };
        content.Children.Add(new TextBlock
        {
            Text = role == "user" ? "Вы" : "Варя",
            FontSize = 12, Foreground = new SolidColorBrush(role == "user"
                ? Windows.UI.Color.FromArgb(255, 160, 177, 185) : Windows.UI.Color.FromArgb(255, 130, 225, 196))
        });
        var block = new TextBlock { Text = text, TextWrapping = TextWrapping.Wrap, FontSize = 16, IsTextSelectionEnabled = true };
        content.Children.Add(block);
        Messages.Children.Add(new Border
        {
            Child = content, Padding = new Thickness(18),
            CornerRadius = new CornerRadius(12),
            Background = new SolidColorBrush(Windows.UI.Color.FromArgb(255, 27, 36, 41))
        });
        while (Messages.Children.Count > 100) Messages.Children.RemoveAt(1);
        ScrollToEnd();
        return block;
    }

    private void ScrollToEnd()
    {
        DispatcherQueue.TryEnqueue(() => ChatScroll.ChangeView(null, ChatScroll.ScrollableHeight, null, true));
    }

    private void Receive(string kind, JsonElement value)
    {
        switch (kind)
        {
            case "resources":
                engineMemory = value.GetInt64();
                break;
            case "ready":
                ready = true;
                SetBusy(false);
                Status.Text = "Готова · микрофон выключен";
                break;
            case "busy":
                SetBusy(true);
                Status.Text = "Выполняю просьбу…";
                break;
            case "user":
                draft = null;
                Turn("user", value.GetString() ?? "");
                Choices.Children.Clear();
                break;
            case "assistant":
            case "private_text":
                if (draft != null) { draft.Text = value.GetString() ?? ""; draft = null; }
                else Turn("assistant", value.GetString() ?? "");
                ScrollToEnd();
                break;
            case "delta":
                draft ??= Turn("assistant", "");
                draft.Text += value.GetString();
                ScrollToEnd();
                break;
            case "end_draft":
                break;
            case "state":
            case "mission":
                Status.Text = value.GetString() ?? "";
                break;
            case "partial":
                Partial.Text = value.GetString() ?? "";
                break;
            case "notice":
                ShowNotice(value.GetString() ?? "");
                break;
            case "idle":
                listening = value.GetProperty("listening").GetBoolean();
                SetBusy(false);
                MicButton.Content = listening ? "Выключить микрофон" : "Включить микрофон";
                Status.Text = listening ? "Слушаю · локальное распознавание" : "Готова · микрофон выключен";
                break;
            case "listening":
                listening = value.GetBoolean();
                SetBusy(false);
                MicButton.Content = "Выключить микрофон";
                StopButton.IsEnabled = true;
                break;
            case "mic_unavailable":
                listening = false;
                MicButton.Content = "Включить микрофон";
                ShowNotice("Микрофон недоступен. Выбери устройство в настройках. Текстовый ввод работает.", true);
                break;
            case "choices":
                choiceVersion = value.GetProperty("version").GetInt32();
                Choices.Children.Clear();
                foreach (var item in value.GetProperty("items").EnumerateArray())
                {
                    var text = item.GetString() ?? "";
                    var version = choiceVersion;
                    var button = new Button { Content = text };
                    button.Click += async (_, _) =>
                    {
                        if (busy) return;
                        SetBusy(true);
                        await Request(new { op = "choice", text, version });
                    };
                    Choices.Children.Add(button);
                }
                break;
            case "settings":
                ApplySettings(value);
                break;
            case "mail_account":
                MailAddress.Text = value.GetProperty("address").GetString() ?? "";
                ImapHost.Text = value.GetProperty("imap").GetString() ?? "";
                SmtpHost.Text = value.GetProperty("smtp").GetString() ?? "";
                ImapPort.Value = value.GetProperty("imap_port").GetInt32();
                SmtpPort.Value = value.GetProperty("smtp_port").GetInt32();
                MailPassword.Password = "";
                break;
            case "devices":
                Microphones.Items.Clear();
                Microphones.Items.Add(new ComboBoxItem { Content = "Устройство Windows по умолчанию", Tag = -1 });
                var index = 0;
                foreach (var name in value.EnumerateArray())
                {
                    Microphones.Items.Add(new ComboBoxItem { Content = $"{index} · {name.GetString()}", Tag = index });
                    index++;
                }
                Microphones.SelectedIndex = selectedMicrophone is >= 0 && selectedMicrophone < index ? selectedMicrophone.Value + 1 : 0;
                break;
            case "timers":
                UpdateTimers(value);
                break;
            case "plans":
                Plans.Children.Clear();
                foreach (var plan in value.EnumerateArray())
                {
                    var identifier = plan.GetProperty("id").GetString() ?? "";
                    var panel = new StackPanel { Spacing = 8 };
                    panel.Children.Add(new TextBlock { Text = plan.GetProperty("request").GetString(), TextWrapping = TextWrapping.Wrap });
                    var position = plan.GetProperty("position").GetInt32() + 1;
                    var interrupted = plan.GetProperty("status").GetString() == "running";
                    panel.Children.Add(new TextBlock { Text = $"План {identifier} · шаг {position}" + (interrupted ? " требует проверки результата" : " ожидает продолжения"), TextWrapping = TextWrapping.Wrap });
                    if (interrupted)
                    {
                        foreach (var completed in new[] { true, false })
                        {
                            var choice = completed;
                            var recover = new Button { Content = choice ? "Я проверил — шаг выполнен" : "Я проверил — повторить шаг" };
                            recover.Click += async (_, _) => await Submit($"продолжи план {identifier} последний шаг " + (choice ? "выполнен" : "не выполнен"));
                            panel.Children.Add(recover);
                        }
                    }
                    else
                    {
                        var resume = new Button { Content = "Просмотреть и продолжить" };
                        resume.Click += async (_, _) => await Submit("продолжи план " + identifier);
                        panel.Children.Add(resume);
                    }
                    Plans.Children.Add(new Border { Child = panel, Padding = new Thickness(14), CornerRadius = new CornerRadius(10), Background = new SolidColorBrush(Windows.UI.Color.FromArgb(255, 27, 36, 41)) });
                }
                if (Plans.Children.Count == 0) Plans.Children.Add(new TextBlock { Text = "Незавершённых поручений нет." });
                break;
            case "notes":
                Notes.Children.Clear();
                foreach (var note in value.EnumerateArray().Reverse())
                    Notes.Children.Add(new TextBlock { Text = note.GetProperty("text").GetString(), TextWrapping = TextWrapping.Wrap, IsTextSelectionEnabled = true });
                if (Notes.Children.Count == 0) Notes.Children.Add(new TextBlock { Text = "Здесь появятся сохранённые заметки." });
                break;
            case "history":
                HistoryText.Text = string.Join("\n\n", value.EnumerateArray().Select(row =>
                {
                    var cells = row.EnumerateArray().ToArray();
                    return $"{cells[0].GetString()} · {cells[1].GetString()}\n{cells[2].GetString()}";
                }));
                if (string.IsNullOrEmpty(HistoryText.Text)) HistoryText.Text = "Журнал пока пуст.";
                break;
            case "artifact_card":
            case "screenshot_card":
                AddArtifact(value.GetString() ?? "");
                break;
            case "timer":
                ShowNotice("Время вышло · " + value.GetString());
                if (App.Main != null) App.Main.Restore();
                break;
            case "tray":
                TrayButton.IsEnabled = value.GetBoolean();
                break;
            case "restore":
            case "wake":
                App.Main?.Restore();
                _ = Request(new { op = "background", value = false });
                break;
            case "exit":
                App.Main?.Close();
                break;
            case "fatal":
                ready = false;
                listening = false;
                SetBusy(false);
                ShowNotice(value.GetString() ?? "Движок недоступен", true);
                Status.Text = "Требуется перезапуск движка";
                break;
        }
    }

    private void UpdateTimers(JsonElement value)
    {
        var names = new HashSet<string>();
        foreach (var timer in value.EnumerateArray())
        {
            var name = timer.GetProperty("name").GetString() ?? "";
            names.Add(name);
            if (!timerCards.TryGetValue(name, out var row))
            {
                var panel = new StackPanel { Spacing = 8 };
                panel.Children.Add(new TextBlock { Text = name, FontSize = 17 });
                var remaining = new TextBlock();
                panel.Children.Add(remaining);
                var cancel = new Button { Content = "Отменить таймер" };
                cancel.Click += async (_, _) => await Submit("отмени таймер " + name, false);
                panel.Children.Add(cancel);
                var card = new Border { Child = panel, Padding = new Thickness(16), CornerRadius = new CornerRadius(10),
                    Background = new SolidColorBrush(Windows.UI.Color.FromArgb(255, 27, 36, 41)) };
                Timers.Children.Add(card);
                row = (card, remaining);
                timerCards[name] = row;
            }
            row.Time.Text = TimeSpan.FromSeconds(timer.GetProperty("seconds").GetInt32()).ToString(@"hh\:mm\:ss");
        }
        foreach (var old in timerCards.Keys.Where(name => !names.Contains(name)).ToArray())
        {
            Timers.Children.Remove(timerCards[old].Card);
            timerCards.Remove(old);
        }
    }

    private void AddArtifact(string path)
    {
        if (!File.Exists(path) && !Directory.Exists(path)) return;
        var button = new Button { Content = "Открыть результат · " + Path.GetFileName(path) };
        button.Click += (_, _) =>
        {
            try { Process.Start(new ProcessStartInfo(path) { UseShellExecute = true }); }
            catch (Exception error) { ShowNotice(error.Message, true); }
        };
        Messages.Children.Add(button);
        ScrollToEnd();
    }

    private void ApplySettings(JsonElement value)
    {
        selectedMicrophone = value.GetProperty("microphone").ValueKind == JsonValueKind.Number ? value.GetProperty("microphone").GetInt32() : null;
        Microphones.Items.Clear();
        Microphones.Items.Add(new ComboBoxItem { Content = "Устройство Windows по умолчанию", Tag = -1 });
        if (selectedMicrophone != null) Microphones.Items.Add(new ComboBoxItem { Content = $"Микрофон №{selectedMicrophone}", Tag = selectedMicrophone.Value });
        Microphones.SelectedIndex = selectedMicrophone == null ? 0 : 1;
        Voice.SelectedIndex = value.GetProperty("voice").GetString() == "sapi" ? 1 : 0;
        Rate.Value = value.GetProperty("rate").GetInt32();
        Volume.Value = value.GetProperty("volume").GetInt32();
        SpeakTyped.IsOn = value.GetProperty("speak_typed").GetBoolean();
        ScheduledReminders.IsOn = value.TryGetProperty("scheduled_reminders", out var reminders) && reminders.GetBoolean();
        AutoUpdates.IsOn = value.TryGetProperty("check_updates", out var auto) && auto.GetBoolean();
        SaveHistory.IsOn = value.GetProperty("save_history").GetBoolean();
        Wake.IsOn = value.GetProperty("wake_word").GetBoolean();
        Installed.IsOn = value.TryGetProperty("installed_apps", out var installed) && installed.GetBoolean();
        Mode.SelectedItem = value.TryGetProperty("mode", out var mode) ? mode.GetString() : "Помощник";
        Project.Text = value.TryGetProperty("project", out var project) ? project.GetString() ?? "" : "";
        MusicRoot.Text = value.TryGetProperty("music_root", out var music) ? music.GetString() ?? "" : "";
        WorkApps.Text = value.TryGetProperty("work_apps", out var apps) ? string.Join(", ", apps.EnumerateArray().Select(x => x.GetString())) : "блокнот";
        if (AutoUpdates.IsOn && !checkedUpdates) _ = CheckForUpdate(true);
    }

    private async Task Submit(string text, bool showChat = true)
    {
        text = text.Trim();
        if (!ready || busy || string.IsNullOrEmpty(text)) return;
        if (showChat) Navigation.SelectedItem = Navigation.MenuItems[0];
        SetBusy(true);
        Input.Text = "";
        await Request(new { op = "typed", text });
    }

    private async void Send(object sender, RoutedEventArgs e) => await Submit(Input.Text);
    private async void Suggestion(object sender, RoutedEventArgs e) => await Submit((sender as Button)?.Tag?.ToString() ?? "");
    private async void SaveNote(object sender, RoutedEventArgs e)
    {
        if (string.IsNullOrWhiteSpace(NoteInput.Text)) return;
        var text = NoteInput.Text;
        await Submit("запомни " + text, false);
        NoteInput.Text = "";
    }
    private async void InputKeyDown(object sender, KeyRoutedEventArgs e)
    {
        var shift = Microsoft.UI.Input.InputKeyboardSource.GetKeyStateForCurrentThread(VirtualKey.Shift);
        if (e.Key == VirtualKey.Enter && !shift.HasFlag(Windows.UI.Core.CoreVirtualKeyStates.Down))
        { e.Handled = true; await Submit(Input.Text); }
        if (e.Key == VirtualKey.Escape && (busy || listening)) await Request(new { op = "stop" });
    }
    private async void Microphone(object sender, RoutedEventArgs e)
    {
        if (!ready) return;
        if (listening) { await Request(new { op = "stop" }); return; }
        SetBusy(true);
        await Request(new { op = "start" });
    }
    private async void Stop(object sender, RoutedEventArgs e)
    {
        Status.Text = "Останавливаю…";
        StopButton.IsEnabled = false;
        await Request(new { op = "stop" });
    }
    private async void GetDevices(object sender, RoutedEventArgs e)
    { SetBusy(true); await Request(new { op = "devices" }); }
    private async void SaveSettings(object sender, RoutedEventArgs e)
    {
        if (busy) return;
        var path = Project.Text.Trim();
        if (path.Length > 0 && !Directory.Exists(path)) { ShowNotice("Папка проекта не существует", true); return; }
        var device = (Microphones.SelectedItem as ComboBoxItem)?.Tag as int? ?? -1;
        var values = new Dictionary<string, object?>
        {
            ["microphone"] = device < 0 ? null : device,
            ["voice"] = (Voice.SelectedItem as ComboBoxItem)?.Tag?.ToString() ?? "piper",
            ["rate"] = (int)Rate.Value, ["volume"] = (int)Volume.Value,
            ["speak_typed"] = SpeakTyped.IsOn, ["save_history"] = SaveHistory.IsOn,
            ["scheduled_reminders"] = ScheduledReminders.IsOn,
            ["check_updates"] = AutoUpdates.IsOn,
            ["wake_word"] = Wake.IsOn, ["installed_apps"] = Installed.IsOn,
            ["mode"] = Mode.SelectedItem?.ToString() ?? "Помощник", ["project"] = path,
            ["music_root"] = MusicRoot.Text.Trim(),
            ["work_apps"] = WorkApps.Text.Split(',', StringSplitOptions.TrimEntries | StringSplitOptions.RemoveEmptyEntries)
        };
        SetBusy(true);
        await Request(new { op = "settings", values });
    }
    private async void Preview(object sender, RoutedEventArgs e)
    { SetBusy(true); await Request(new { op = "preview" }); }
    private async void SaveMail(object sender, RoutedEventArgs e)
    {
        if (busy) return;
        SetBusy(true);
        await Request(new { op = "mail_config", values = new {
            address = MailAddress.Text.Trim(), imap = ImapHost.Text.Trim(),
            smtp = SmtpHost.Text.Trim(), smtp_port = (int)SmtpPort.Value,
            imap_port = (int)ImapPort.Value,
            password = MailPassword.Password
        }});
    }
    private async void Restart(object sender, RoutedEventArgs e)
    {
        ready = false;
        var previous = engine;
        engine = null;
        await Task.Run(() => previous?.Dispose());
        Status.Text = "Перезапускаю…";
        StartEngine();
    }
    private async void CheckUpdates(object sender, RoutedEventArgs e) => await CheckForUpdate(false);
    private async Task CheckForUpdate(bool automatic)
    {
        if (updateCancel != null) return;
        checkedUpdates = true;
        updateCancel = new CancellationTokenSource(TimeSpan.FromSeconds(40));
        CheckUpdateButton.IsEnabled = false;
        InstallUpdateButton.IsEnabled = false;
        UpdateStatus.Text = "Проверяю опубликованные версии…";
        try
        {
            update = await updater.Check(updateCancel.Token);
            UpdateStatus.Text = update is null ? "Новых подписанных выпусков пока нет."
                : $"Доступна версия {update.Version} · {update.Assets.Sum(a => a.Size) / 1073741824.0:F1} ГиБ";
            if (automatic && update != null) ShowNotice($"Доступна Варя {update.Version}. Установить можно в настройках.");
        }
        catch (OperationCanceledException) { UpdateStatus.Text = "Проверка прервана по времени или отменена."; }
        catch (Exception error) when (error is HttpRequestException or IOException or System.Security.Cryptography.CryptographicException or JsonException or InvalidOperationException)
        { update = null; UpdateStatus.Text = "Проверка не завершена. " + error.Message; }
        finally
        {
            updateCancel.Dispose(); updateCancel = null;
            CheckUpdateButton.IsEnabled = true;
            InstallUpdateButton.IsEnabled = update != null && !busy;
        }
    }
    private async void InstallUpdate(object sender, RoutedEventArgs e)
    {
        if (update is null || busy || updateCancel != null) return;
        var candidate = update;
        var dialog = new ContentDialog
        {
            XamlRoot = XamlRoot, Title = $"Установить Варю {candidate.Version}?",
            Content = $"Загрузка займёт {candidate.Assets.Sum(a => a.Size) / 1073741824.0:F1} ГиБ. После проверки файлов Варя закроется и откроется установщик Windows. Личные данные сохранятся.",
            PrimaryButtonText = "Загрузить и установить", CloseButtonText = "Отмена", DefaultButton = ContentDialogButton.Close
        };
        if (await dialog.ShowAsync() != ContentDialogResult.Primary) return;
        updateCancel = new CancellationTokenSource(TimeSpan.FromHours(2));
        InstallUpdateButton.IsEnabled = false;
        CheckUpdateButton.IsEnabled = false;
        CancelUpdateButton.Visibility = UpdateProgress.Visibility = Visibility.Visible;
        UpdateProgress.Value = 0;
        try
        {
            var data = engine?.DataPath ?? Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "Varya");
            var progress = new Progress<double>(number => { UpdateProgress.Value = number; UpdateStatus.Text = $"Загружаю и проверяю обновление · {number:F0}%"; });
            var installer = await updater.Download(candidate, data, progress, updateCancel.Token);
            var info = new ProcessStartInfo(Path.Combine(Environment.SystemDirectory, "msiexec.exe")) { UseShellExecute = false };
            info.ArgumentList.Add("/i"); info.ArgumentList.Add(installer);
            _ = Process.Start(info) ?? throw new IOException("Windows Installer не запустился.");
            App.Main?.Close();
        }
        catch (OperationCanceledException) { UpdateStatus.Text = "Загрузка отменена. Текущая версия продолжает работать."; }
        catch (Exception error) when (error is IOException or HttpRequestException or System.ComponentModel.Win32Exception)
        { UpdateStatus.Text = "Обновление не установлено. " + error.Message; }
        finally
        {
            updateCancel?.Dispose(); updateCancel = null;
            CheckUpdateButton.IsEnabled = true;
            InstallUpdateButton.IsEnabled = update != null && !busy;
            CancelUpdateButton.Visibility = UpdateProgress.Visibility = Visibility.Collapsed;
        }
    }
    private void CancelUpdate(object sender, RoutedEventArgs e) => updateCancel?.Cancel();
    private void OpenData(object sender, RoutedEventArgs e)
    {
        if (engine is null) return;
        try { Directory.CreateDirectory(engine.DataPath); Process.Start(new ProcessStartInfo(engine.DataPath) { UseShellExecute = true }); }
        catch (Exception error) { ShowNotice(error.Message, true); }
    }
    private async void HideToTray(object sender, RoutedEventArgs e)
    { await Request(new { op = "background", value = true }); App.Main?.AppWindow.Hide(); }
    private async void Navigate(NavigationView sender, NavigationViewSelectionChangedEventArgs args)
    {
        if (ChatPage is null) return;
        var tag = args.IsSettingsSelected ? "settings" : (args.SelectedItem as NavigationViewItem)?.Tag?.ToString();
        ChatPage.Visibility = tag == "chat" ? Visibility.Visible : Visibility.Collapsed;
        TasksPage.Visibility = tag == "tasks" ? Visibility.Visible : Visibility.Collapsed;
        SettingsPage.Visibility = tag == "settings" ? Visibility.Visible : Visibility.Collapsed;
        HistoryPage.Visibility = tag == "history" ? Visibility.Visible : Visibility.Collapsed;
        if (tag == "history" && ready) await Request(new { op = "history" });
    }
}
