using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Net.Http;
using System.Runtime.Serialization;
using System.Runtime.Serialization.Json;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using System.Windows.Forms;

[DataContract]
internal sealed class LauncherConfig
{
    [DataMember]
    public string ControllerPath { get; set; }
    [DataMember]
    public string PowerShellPath { get; set; }
    [DataMember]
    public string ChromePath { get; set; }
}

[DataContract]
internal sealed class ReadyResponse
{
    [DataMember(Name = "ok")]
    public bool Ok { get; set; }
    [DataMember(Name = "status")]
    public string Status { get; set; }
    [DataMember(Name = "backend")]
    public string Backend { get; set; }
}

[DataContract]
internal sealed class ControllerResponse
{
    [DataMember(Name = "version")]
    public string Version { get; set; }
    [DataMember(Name = "status")]
    public string Status { get; set; }
    [DataMember(Name = "state")]
    public string State { get; set; }
}

internal static class Launcher
{
    internal const string PageUrl = "http://localhost:3000/";
    internal static readonly TimeSpan StartupWaitTimeout = TimeSpan.FromMinutes(15);

    private static T Deserialize<T>(string text) where T : class
    {
        using (var stream = new MemoryStream(Encoding.UTF8.GetBytes(text)))
            return (T)new DataContractJsonSerializer(typeof(T)).ReadObject(stream);
    }

    [STAThread]
    private static void Main(string[] args)
    {
        Application.EnableVisualStyles();
        Application.SetCompatibleTextRenderingDefault(false);
        bool created;
        using (var mutex = new Mutex(true, "Local\\TERUISI.DesktopLauncher.v1", out created))
        {
            if (!created) return;
            try
            {
                var config = Deserialize<LauncherConfig>(File.ReadAllText(
                    Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "launcher.json"), Encoding.UTF8));
                foreach (var path in new[] { config.ControllerPath, config.PowerShellPath, config.ChromePath })
                    if (!File.Exists(path)) throw new IOException("启动所需文件不存在，请重新安装桌面入口。");
                using (var form = new LauncherForm(config))
                {
                    if (args.Length == 2 && args[0] == "--preview")
                    {
                        form.RenderPreview(args[1]);
                        return;
                    }
                    Application.Run(form);
                }
            }
            catch (Exception ex)
            {
                MessageBox.Show(ex.Message, "运营管理系统", MessageBoxButtons.OK, MessageBoxIcon.Error);
            }
        }
    }

    internal static bool IsReady(string text)
    {
        try
        {
            var value = Deserialize<ReadyResponse>(text);
            return value != null && value.Ok
                && object.Equals(value.Status, "ready")
                && object.Equals(value.Backend, "django-postgresql");
        }
        catch { return false; }
    }

    internal static async Task<bool> ProbeReady()
    {
        try
        {
            using (var handler = new HttpClientHandler { UseProxy = false, AllowAutoRedirect = false })
            using (var client = new HttpClient(handler) { Timeout = TimeSpan.FromSeconds(8) })
            {
                client.DefaultRequestHeaders.Add("x-teruisi-local-health", "1");
                using (var response = await client.GetAsync("http://127.0.0.1:3000/_teruisi/local/health/ready"))
                {
                    if (!response.IsSuccessStatusCode || !IsReady(await response.Content.ReadAsStringAsync()))
                        return false;
                }
                using (var response = await client.GetAsync(PageUrl, HttpCompletionOption.ResponseHeadersRead))
                    return response.StatusCode == System.Net.HttpStatusCode.OK;
            }
        }
        catch { return false; }
    }

    internal static string PsLiteral(string value) { return "'" + value.Replace("'", "''") + "'"; }

    internal static async Task<bool> WaitForReady(Func<Task<bool>> probe, TimeSpan timeout, Func<bool> cancelled)
    {
        var elapsed = Stopwatch.StartNew();
        while (elapsed.Elapsed < timeout && !cancelled())
        {
            if (await probe()) return !cancelled();
            if (cancelled()) return false;
            var remaining = timeout - elapsed.Elapsed;
            if (remaining <= TimeSpan.Zero) break;
            await Task.Delay((int)Math.Min(2000, Math.Ceiling(remaining.TotalMilliseconds)));
        }
        return false;
    }

    // The controller redirects JSON to a file which can remain open briefly (or be
    // inherited by a durable descendant) after the direct controller exits. Read
    // with sharing enabled so a successful start cannot be reported as a launcher
    // failure merely because another process still has the receipt open.
    private static string ReadSharedText(string path)
    {
        using (var stream = new FileStream(path, FileMode.Open, FileAccess.Read,
            FileShare.ReadWrite | FileShare.Delete))
        using (var reader = new StreamReader(stream, Encoding.UTF8, true))
            return reader.ReadToEnd();
    }

    private static ControllerResponse ReadControllerReceipt(string path)
    {
        Exception last = null;
        for (var attempt = 0; attempt < 20; attempt++)
        {
            try
            {
                var text = ReadSharedText(path);
                if (!string.IsNullOrWhiteSpace(text))
                {
                    var receipt = Deserialize<ControllerResponse>(text);
                    if (receipt != null) return receipt;
                }
            }
            catch (Exception ex) { last = ex; }
            Thread.Sleep(250);
        }
        throw new InvalidOperationException("启动器没有收到有效的总控结果，请查看日志。", last);
    }

    // Capture to files and wait only for the direct controller process. Its durable
    // service descendants must never keep a redirected parent pipe alive.
    internal static async Task<Dictionary<string, object>> StartController(LauncherConfig config, string logDirectory)
    {
        Directory.CreateDirectory(logDirectory);
        var output = Path.Combine(logDirectory, "controller.json");
        var error = Path.Combine(logDirectory, "controller.stderr.log");
        var command = "$ErrorActionPreference='Stop'; $p=Start-Process -FilePath " + PsLiteral(config.PowerShellPath)
            + " -ArgumentList @('-NoProfile','-ExecutionPolicy','Bypass','-File',"
            + PsLiteral("\"" + config.ControllerPath + "\"") + ",'-Action','Start','-Open','-Json')"
            + " -WorkingDirectory " + PsLiteral(Path.GetDirectoryName(config.ControllerPath))
            + " -WindowStyle Hidden -RedirectStandardOutput " + PsLiteral(output)
            + " -RedirectStandardError " + PsLiteral(error)
            + " -PassThru; $p.WaitForExit(); exit $p.ExitCode";
        var info = new ProcessStartInfo(config.PowerShellPath,
            "-NoProfile -NonInteractive -WindowStyle Hidden -EncodedCommand "
            + Convert.ToBase64String(Encoding.Unicode.GetBytes(command)))
        {
            UseShellExecute = false, CreateNoWindow = true, WindowStyle = ProcessWindowStyle.Hidden
        };
        using (var process = Process.Start(info))
        {
            await Task.Run(() => process.WaitForExit());
            if (process.ExitCode != 0)
                throw new InvalidOperationException("受控启动未通过，请查看本次日志中的原因。");
        }
        var receipt = ReadControllerReceipt(output);
        if (receipt == null
            || !object.Equals(receipt.Version, "teruisi-operations-system-control-v2")
            || string.IsNullOrEmpty(receipt.Status) || string.IsNullOrEmpty(receipt.State))
            throw new InvalidOperationException("启动器没有收到有效的总控结果，请查看日志。");
        var status = receipt.Status;
        var state = receipt.State;
        if (!((status == "started" || status == "already_running") && state == "Running")
            && !(status == "started_degraded" && state == "BackendDegraded")
            && !(status == "start_in_progress" && state == "Starting"))
            throw new InvalidOperationException("总控尚未确认系统可用，请查看本次启动日志。");
        return new Dictionary<string, object> {
            { "version", receipt.Version }, { "status", status }, { "state", state }
        };
    }
}

internal sealed class LauncherForm : Form
{
    private readonly LauncherConfig config;
    private readonly Label status = new Label();
    private readonly Label detail = new Label();
    private readonly Label elapsed = new Label();
    private readonly ProgressBar progress = new ProgressBar();
    private readonly Button retry = new Button();
    private readonly Button logs = new Button();
    private readonly System.Windows.Forms.Timer timer = new System.Windows.Forms.Timer();
    private DateTime started;
    private string logDirectory;
    private bool busy;
    private bool previewOnly;

    internal LauncherForm(LauncherConfig value)
    {
        config = value;
        Text = "运营管理系统";
        ClientSize = new Size(540, 354);
        StartPosition = FormStartPosition.CenterScreen;
        FormBorderStyle = FormBorderStyle.FixedSingle;
        MaximizeBox = false;
        BackColor = Color.FromArgb(248, 250, 252);
        Font = new Font("Microsoft YaHei UI", 10);
        var iconPath = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "launcher.ico");
        if (File.Exists(iconPath)) Icon = new Icon(iconPath);
        var brand = new Label { Text = "TERUISI  /  本地工作台", Location = new Point(32, 24),
            Size = new Size(465, 26), ForeColor = Color.FromArgb(13, 130, 117),
            Font = new Font("Microsoft YaHei UI", 10, FontStyle.Bold) };
        var title = new Label { Text = "运营管理系统", Location = new Point(28, 58),
            Size = new Size(480, 45), Font = new Font("Microsoft YaHei UI", 24, FontStyle.Bold) };
        status.Text = "正在为你打开系统…";
        status.SetBounds(32, 127, 476, 30);
        status.Font = new Font("Microsoft YaHei UI", 13, FontStyle.Bold);
        detail.Text = "服务已运行时直接打开；首次启动请稍等片刻。";
        detail.SetBounds(32, 166, 476, 48);
        detail.ForeColor = Color.FromArgb(83, 98, 116);
        progress.SetBounds(32, 224, 476, 5);
        progress.Style = ProgressBarStyle.Marquee;
        elapsed.SetBounds(32, 242, 476, 24);
        elapsed.ForeColor = detail.ForeColor;
        elapsed.Text = "准备连接本地服务器";
        retry.Text = "重新打开";
        retry.SetBounds(32, 288, 230, 40);
        retry.BackColor = Color.FromArgb(13, 130, 117);
        retry.ForeColor = Color.White;
        retry.FlatStyle = FlatStyle.Flat;
        retry.FlatAppearance.BorderSize = 0;
        retry.Enabled = false;
        logs.Text = "查看本次日志";
        logs.SetBounds(278, 288, 230, 40);
        logs.Enabled = false;
        Controls.AddRange(new Control[] { brand, title, status, detail, progress, elapsed, retry, logs });
        timer.Interval = 500;
        timer.Tick += delegate { elapsed.Text = "已等待 " + (int)(DateTime.UtcNow - started).TotalSeconds
            + " 秒 · 关闭此窗口不影响服务器运行"; };
        retry.Click += async delegate { await OpenSystem(); };
        logs.Click += delegate { if (Directory.Exists(logDirectory)) Process.Start("explorer.exe", "\"" + logDirectory + "\""); };
        Shown += async delegate { if (!previewOnly) await OpenSystem(); };
        FormClosed += delegate { timer.Stop(); timer.Dispose(); };
    }

    internal void RenderPreview(string path)
    {
        previewOnly = true;
        Show();
        Application.DoEvents();
        using (var bitmap = new Bitmap(Width, Height))
        {
            DrawToBitmap(bitmap, new Rectangle(0, 0, Width, Height));
            bitmap.Save(path, System.Drawing.Imaging.ImageFormat.Png);
        }
        Hide();
    }

    private void Record(string value)
    {
        File.AppendAllText(Path.Combine(logDirectory, "launcher.log"), DateTime.Now.ToString("s")
            + " " + value + Environment.NewLine, Encoding.UTF8);
    }

    private async Task OpenSystem()
    {
        if (busy) return;
        busy = true;
        retry.Enabled = false;
        started = DateTime.UtcNow;
        progress.Visible = true;
        status.Text = "正在检查本地服务器…";
        detail.Text = "服务已运行时将直接打开 Chrome。";
        timer.Start();
        try
        {
            logDirectory = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                "TERUISI", "Launcher", "logs", DateTime.Now.ToString("yyyyMMdd-HHmmss") + "-" + Guid.NewGuid().ToString("N").Substring(0, 8));
            Directory.CreateDirectory(logDirectory);
            logs.Enabled = true;
            Record("Checking local readiness");
            if (await Launcher.ProbeReady())
            {
                if (IsDisposed) return;
                Process.Start(new ProcessStartInfo(config.ChromePath, Launcher.PageUrl) { UseShellExecute = true });
                Record("Opened Chrome: existing server ready; no lifecycle action");
            }
            else
            {
                if (IsDisposed) return;
                status.Text = "正在启动本地服务器…";
                detail.Text = "正在通过系统总控检查并启动服务，完成后自动打开。\n开机后可能需要几分钟，请保持此窗口打开，无需重复点击。";
                Record("Delegating Start -Open to existing controller");
                var result = await Launcher.StartController(config, logDirectory);
                if (IsDisposed) return;
                var outcome = (string)result["status"];
                if (outcome == "start_in_progress")
                {
                    status.Text = "已有启动任务，正在等待…";
                    Record("Waiting for existing startup readiness");
                    if (await Launcher.WaitForReady(Launcher.ProbeReady, Launcher.StartupWaitTimeout, () => IsDisposed))
                    {
                        if (IsDisposed) return;
                        Process.Start(config.ChromePath, Launcher.PageUrl);
                        outcome = "already_running";
                    }
                    if (IsDisposed) return;
                }
                if (outcome != "started" && outcome != "already_running" && outcome != "started_degraded")
                    throw new InvalidOperationException("系统尚未确认就绪，请查看启动日志后再试。");
                Record("Controller result: " + outcome);
                if (outcome == "started_degraded")
                {
                    status.Text = "页面已打开，部分服务待就绪";
                    detail.Text = "可继续使用正常页面；本次总控结果保存在日志中。";
                    return;
                }
            }
            status.Text = "系统已打开";
            detail.Text = "以后双击桌面的「运营管理系统」即可进入。";
            timer.Stop();
            elapsed.Text = "完成 · 窗口即将自动关闭";
            progress.Visible = false;
            await Task.Delay(1800);
            if (!IsDisposed) Close();
        }
        catch (Exception ex)
        {
            if (!IsDisposed)
            {
                status.Text = "暂时未能打开系统";
                detail.Text = ex.Message;
                if (Directory.Exists(logDirectory)) Record("Failed: " + ex.GetType().Name);
            }
        }
        finally
        {
            busy = false;
            if (!IsDisposed)
            {
                timer.Stop();
                progress.Visible = false;
                retry.Enabled = true;
                elapsed.Text = "关闭此窗口不影响服务器运行";
            }
        }
    }
}
