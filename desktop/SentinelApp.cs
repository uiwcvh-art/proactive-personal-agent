using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.IO;
using System.Net;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using System.Windows.Forms;
using Microsoft.Win32;

[assembly: AssemblyTitle("DSB · 桌面连接与守护中心")]
[assembly: AssemblyProduct("DeepSeek Claw Sentinel v3.0")]
[assembly: AssemblyVersion("3.0.0.0")]

namespace DeepSeekClawSentinel
{
    internal static class NativeMethods
    {
        [DllImport("user32.dll")]
        public static extern bool SetProcessDPIAware();

        [DllImport("user32.dll")]
        public static extern bool DestroyIcon(IntPtr handle);

        [DllImport("user32.dll")]
        public static extern bool SetForegroundWindow(IntPtr hWnd);
    }

    public sealed class SentinelForm : Form
    {
        private const string RunRegPath = @"Software\Microsoft\Windows\CurrentVersion\Run";
        private const string RunRegName = "DeepSeekClawSentinel";
        private const int GatewayPort = 18195;
        private const int ControlPort = 18197;

        private readonly string pluginDir;
        private readonly string sentinelScriptPath;
        private readonly string logFilePath;
        private readonly EventWaitHandle showEvent;
        private readonly JavaScriptSerializer json = new JavaScriptSerializer();

        private bool suppressInitialShow;
        private NotifyIcon trayIcon;
        private System.Windows.Forms.Timer pollTimer;
        private Panel headerBadgePanel;
        private Label lblStatusDot;
        private Label lblStatusText;
        private Label lblGatewayRow;
        private Label lblCloudRow;
        private Label lblPeersRow;
        private Label lblTrafficRow;
        private Label lblErrorHint;
        private Button btnReconnect;
        private Button btnRestartCore;
        private Button btnWakeDsh;
        private Button btnOpenLog;
        private Button btnToggleAutostart;
        private Button btnHideToTray;

        private Icon iconOnline;
        private Icon iconWarn;
        private Icon iconOffline;
        private int currentIconState = -1;

        private bool allowRealExit = false;
        private bool pollBusy = false;
        private int consecutiveGatewayFailures = 0;
        private int consecutiveCloudOfflineTicks = 0;
        private DateTime lastSpawnAttemptUtc = DateTime.MinValue;
        private DateTime lastAutoReconnectUtc = DateTime.MinValue;
        private int lastKnownSentinelPid = 0;

        public SentinelForm(bool minimized, EventWaitHandle wakeEvent)
        {
            this.suppressInitialShow = minimized;
            this.showEvent = wakeEvent;
            this.pluginDir = Path.GetDirectoryName(Application.ExecutablePath) ?? @"D:\dsh-baike-plugin";
            this.sentinelScriptPath = Path.Combine(this.pluginDir, "sentinel.mjs");
            this.logFilePath = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), ".dsh", "sentinel.log");

            this.iconOnline = CreateDsbStatusIcon(Color.FromArgb(48, 209, 88));
            this.iconWarn = CreateDsbStatusIcon(Color.FromArgb(255, 196, 46));
            this.iconOffline = CreateDsbStatusIcon(Color.FromArgb(255, 69, 58));

            BuildUi();
            SetupTray();
            EnsureAutostartSynced();
            UpdateAutostartButtonText();

            // Force HWND creation immediately so BeginInvoke & WinForms Timer run reliably even when hidden in tray!
            IntPtr dummy = this.Handle;
            RegisterWakeListener();
            Task.Run(new Action(InitialBootAndRefresh));

            this.pollTimer = new System.Windows.Forms.Timer();
            this.pollTimer.Interval = 2000;
            this.pollTimer.Tick += delegate { TriggerRefreshAsync(false); };
            this.pollTimer.Start();
        }

        protected override void SetVisibleCore(bool value)
        {
            if (this.suppressInitialShow)
            {
                value = false;
                if (!this.IsHandleCreated) CreateHandle();
            }
            base.SetVisibleCore(value);
        }

        private void InitialBootAndRefresh()
        {
            EnsureNodeGatewayHealthy(true);
            Thread.Sleep(450);
            RefreshStatusCore();
        }

        private void RegisterWakeListener()
        {
            if (this.showEvent == null) return;
            Thread t = new Thread(delegate()
            {
                while (!this.IsDisposed)
                {
                    try
                    {
                        if (this.showEvent.WaitOne(1000))
                        {
                            if (this.IsHandleCreated && !this.IsDisposed)
                            {
                                this.BeginInvoke(new Action(ShowFromTray));
                            }
                        }
                    }
                    catch
                    {
                        break;
                    }
                }
            });
            t.IsBackground = true;
            t.Start();
        }

        private void BuildUi()
        {
            this.Text = "DSB · 桌面连接与常驻守护中心 v3.0";
            this.Font = new Font("Microsoft YaHei UI", 9.2f, FontStyle.Regular);
            this.ClientSize = new Size(490, 368);
            this.FormBorderStyle = FormBorderStyle.FixedSingle;
            this.MaximizeBox = false;
            this.StartPosition = FormStartPosition.CenterScreen;
            this.BackColor = Color.FromArgb(19, 21, 26);
            this.ForeColor = Color.FromArgb(242, 244, 248);
            this.Icon = this.iconWarn;
            if (this.suppressInitialShow)
            {
                this.ShowInTaskbar = false;
                this.WindowState = FormWindowState.Minimized;
            }

            Label lblTitle = new Label();
            lblTitle.Text = "DSB · 桌面连接守护中心";
            lblTitle.Font = new Font("Microsoft YaHei UI", 13.5f, FontStyle.Bold);
            lblTitle.ForeColor = Color.FromArgb(248, 250, 252);
            lblTitle.AutoSize = true;
            lblTitle.Location = new Point(20, 16);
            this.Controls.Add(lblTitle);

            Label lblSub = new Label();
            lblSub.Text = "常驻独占网关 127.0.0.1:18195 · 直连/代理双路自动自愈 · 零黑框托盘驻留";
            lblSub.Font = new Font("Microsoft YaHei UI", 8.5f, FontStyle.Regular);
            lblSub.ForeColor = Color.FromArgb(148, 163, 184);
            lblSub.AutoSize = true;
            lblSub.Location = new Point(22, 45);
            this.Controls.Add(lblSub);

            this.headerBadgePanel = new Panel();
            this.headerBadgePanel.Location = new Point(20, 72);
            this.headerBadgePanel.Size = new Size(450, 36);
            this.headerBadgePanel.BackColor = Color.FromArgb(30, 34, 43);

            this.lblStatusDot = new Label();
            this.lblStatusDot.Text = "●";
            this.lblStatusDot.Font = new Font("Microsoft YaHei UI", 11f, FontStyle.Bold);
            this.lblStatusDot.ForeColor = Color.FromArgb(255, 196, 46);
            this.lblStatusDot.AutoSize = true;
            this.lblStatusDot.Location = new Point(12, 7);
            this.headerBadgePanel.Controls.Add(this.lblStatusDot);

            this.lblStatusText = new Label();
            this.lblStatusText.Text = "正在握手本机常驻网关与云端中枢…";
            this.lblStatusText.Font = new Font("Microsoft YaHei UI", 9.5f, FontStyle.Bold);
            this.lblStatusText.ForeColor = Color.FromArgb(248, 250, 252);
            this.lblStatusText.AutoSize = false;
            this.lblStatusText.Size = new Size(406, 24);
            this.lblStatusText.Location = new Point(34, 8);
            this.headerBadgePanel.Controls.Add(this.lblStatusText);
            this.Controls.Add(this.headerBadgePanel);

            Panel card = new Panel();
            card.Location = new Point(20, 116);
            card.Size = new Size(450, 146);
            card.BackColor = Color.FromArgb(26, 29, 36);

            this.lblGatewayRow = CreateMetricLabel(14, 12, "本机网关：正在检测 127.0.0.1:18195…");
            this.lblCloudRow = CreateMetricLabel(14, 42, "云端链路：正在检测云端连通性与路由…");
            this.lblPeersRow = CreateMetricLabel(14, 72, "跨端协同：手机端检测中 · DSH 宿主检测中");
            this.lblTrafficRow = CreateMetricLabel(14, 102, "会话流量：DSH 会话统计中 · 累计心跳 0 次");
            this.lblErrorHint = new Label();
            this.lblErrorHint.Location = new Point(14, 125);
            this.lblErrorHint.Size = new Size(422, 18);
            this.lblErrorHint.Font = new Font("Microsoft YaHei UI", 8.0f, FontStyle.Regular);
            this.lblErrorHint.ForeColor = Color.FromArgb(248, 113, 113);
            this.lblErrorHint.Text = "";

            card.Controls.Add(this.lblGatewayRow);
            card.Controls.Add(this.lblCloudRow);
            card.Controls.Add(this.lblPeersRow);
            card.Controls.Add(this.lblTrafficRow);
            card.Controls.Add(this.lblErrorHint);
            this.Controls.Add(card);

            this.btnReconnect = CreateActionButton("⚡ 立即重连", 20, 274, 106, Color.FromArgb(79, 70, 229), Color.White);
            this.btnReconnect.Click += delegate { ManualReconnectClicked(); };

            this.btnRestartCore = CreateActionButton("↻ 重启内核", 134, 274, 106, Color.FromArgb(39, 44, 56), Color.FromArgb(226, 232, 240));
            this.btnRestartCore.Click += delegate { ManualRestartCoreClicked(); };

            this.btnWakeDsh = CreateActionButton("🚀 唤醒 DSH", 248, 274, 110, Color.FromArgb(39, 44, 56), Color.FromArgb(226, 232, 240));
            this.btnWakeDsh.Click += delegate { LaunchDshGui(); };

            this.btnOpenLog = CreateActionButton("📄 查看日志", 366, 274, 104, Color.FromArgb(39, 44, 56), Color.FromArgb(226, 232, 240));
            this.btnOpenLog.Click += delegate { OpenLogFile(); };

            this.btnToggleAutostart = CreateActionButton("开机自启：检测中", 20, 318, 286, Color.FromArgb(28, 32, 40), Color.FromArgb(165, 180, 252));
            this.btnToggleAutostart.Click += delegate { ToggleAutostart(); };

            this.btnHideToTray = CreateActionButton("最小化到托盘", 314, 318, 156, Color.FromArgb(28, 32, 40), Color.FromArgb(148, 163, 184));
            this.btnHideToTray.Click += delegate { HideToTray(); };

            this.Controls.Add(this.btnReconnect);
            this.Controls.Add(this.btnRestartCore);
            this.Controls.Add(this.btnWakeDsh);
            this.Controls.Add(this.btnOpenLog);
            this.Controls.Add(this.btnToggleAutostart);
            this.Controls.Add(this.btnHideToTray);
        }

        private Label CreateMetricLabel(int x, int y, string text)
        {
            Label lbl = new Label();
            lbl.Location = new Point(x, y);
            lbl.Size = new Size(422, 22);
            lbl.Font = new Font("Microsoft YaHei UI", 9.0f, FontStyle.Regular);
            lbl.ForeColor = Color.FromArgb(226, 232, 240);
            lbl.Text = text;
            return lbl;
        }

        private Button CreateActionButton(string text, int x, int y, int width, Color bg, Color fg)
        {
            Button b = new Button();
            b.Text = text;
            b.Location = new Point(x, y);
            b.Size = new Size(width, 34);
            b.FlatStyle = FlatStyle.Flat;
            b.FlatAppearance.BorderSize = 1;
            b.FlatAppearance.BorderColor = Color.FromArgb(52, 58, 72);
            b.BackColor = bg;
            b.ForeColor = fg;
            b.Cursor = Cursors.Hand;
            return b;
        }

        private void SetupTray()
        {
            ContextMenu menu = new ContextMenu();
            menu.MenuItems.Add("打开连接守护面板", delegate { ShowFromTray(); });
            menu.MenuItems.Add("⚡ 立即重连云端", delegate { ManualReconnectClicked(); });
            menu.MenuItems.Add("↻ 重启桥接内核 (18195)", delegate { ManualRestartCoreClicked(); });
            menu.MenuItems.Add("-");
            menu.MenuItems.Add("启动 DeepSeek Harness 主界面", delegate { LaunchDshGui(); });
            menu.MenuItems.Add("查看运行日志 (sentinel.log)", delegate { OpenLogFile(); });
            menu.MenuItems.Add("-");
            menu.MenuItems.Add("彻底退出守护 (断开电脑端在线)", delegate
            {
                this.allowRealExit = true;
                ShutdownNodeSentinelGracefully();
                this.trayIcon.Visible = false;
                Application.Exit();
            });

            this.trayIcon = new NotifyIcon();
            this.trayIcon.Icon = this.iconWarn;
            this.trayIcon.Text = "DSB · 桌面连接守护中心";
            this.trayIcon.ContextMenu = menu;
            this.trayIcon.Visible = true;
            this.trayIcon.DoubleClick += delegate { ShowFromTray(); };
        }

        private Icon CreateDsbStatusIcon(Color badgeColor)
        {
            using (Bitmap bmp = new Bitmap(32, 32))
            {
                using (Graphics g = Graphics.FromImage(bmp))
                {
                    g.SmoothingMode = SmoothingMode.AntiAlias;
                    g.Clear(Color.Transparent);

                    using (SolidBrush bgBrush = new SolidBrush(Color.FromArgb(20, 26, 38)))
                    {
                        g.FillEllipse(bgBrush, 1, 1, 28, 28);
                    }
                    using (Pen ringPen = new Pen(Color.FromArgb(46, 196, 182), 2.1f))
                    {
                        g.DrawEllipse(ringPen, 2, 2, 26, 26);
                    }
                    PointF[] pulseWave = new PointF[]
                    {
                        new PointF(6.5f, 16.0f),
                        new PointF(10.5f, 16.0f),
                        new PointF(13.0f, 10.0f),
                        new PointF(16.2f, 21.0f),
                        new PointF(19.0f, 13.5f),
                        new PointF(22.5f, 16.0f)
                    };
                    using (Pen wavePen = new Pen(Color.FromArgb(240, 253, 250), 2.3f))
                    {
                        wavePen.StartCap = LineCap.Round;
                        wavePen.EndCap = LineCap.Round;
                        wavePen.LineJoin = LineJoin.Round;
                        g.DrawLines(wavePen, pulseWave);
                    }
                    using (SolidBrush borderBrush = new SolidBrush(Color.FromArgb(19, 21, 26)))
                    {
                        g.FillEllipse(borderBrush, 19, 19, 12, 12);
                    }
                    using (SolidBrush dotBrush = new SolidBrush(badgeColor))
                    {
                        g.FillEllipse(dotBrush, 21, 21, 8, 8);
                    }
                }
                IntPtr hIcon = bmp.GetHicon();
                Icon cloned = (Icon)Icon.FromHandle(hIcon).Clone();
                NativeMethods.DestroyIcon(hIcon);
                return cloned;
            }
        }

        private void SetIconState(int stateCode, string tooltip)
        {
            string safeTip = tooltip.Length > 60 ? tooltip.Substring(0, 60) : tooltip;
            if (this.trayIcon != null)
            {
                try { this.trayIcon.Text = safeTip; } catch { }
                if (this.currentIconState != stateCode)
                {
                    this.currentIconState = stateCode;
                    Icon target = stateCode == 2 ? this.iconOnline : (stateCode == 1 ? this.iconWarn : this.iconOffline);
                    this.trayIcon.Icon = target;
                    this.Icon = target;
                }
            }
        }

        protected override void OnFormClosing(FormClosingEventArgs e)
        {
            if (!this.allowRealExit && e.CloseReason == CloseReason.UserClosing)
            {
                e.Cancel = true;
                HideToTray();
                return;
            }
            base.OnFormClosing(e);
        }

        private void HideToTray()
        {
            this.suppressInitialShow = true;
            this.ShowInTaskbar = false;
            this.WindowState = FormWindowState.Minimized;
            this.Hide();
        }

        private void ShowFromTray()
        {
            this.suppressInitialShow = false;
            this.ShowInTaskbar = true;
            this.Show();
            this.WindowState = FormWindowState.Normal;
            this.Activate();
            NativeMethods.SetForegroundWindow(this.Handle);
            TriggerRefreshAsync(false);
        }

        private string HttpRequestLocal(string url, string method, int timeoutMs)
        {
            HttpWebRequest req = (HttpWebRequest)WebRequest.Create(url);
            req.Method = method;
            req.Timeout = timeoutMs;
            req.ReadWriteTimeout = timeoutMs;
            req.KeepAlive = false;
            req.Proxy = null;
            if (method == "POST")
            {
                req.ContentLength = 0;
            }
            using (HttpWebResponse resp = (HttpWebResponse)req.GetResponse())
            using (Stream s = resp.GetResponseStream())
            using (StreamReader sr = new StreamReader(s, Encoding.UTF8))
            {
                return sr.ReadToEnd();
            }
        }

        private string ResolveNodeExePath()
        {
            string[] candidates = new string[]
            {
                @"C:\Program Files\nodejs\node.exe",
                Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles), "nodejs", "node.exe"),
                "node.exe"
            };
            foreach (string c in candidates)
            {
                if (c == "node.exe" || File.Exists(c)) return c;
            }
            return "node.exe";
        }

        private void KillWedgedSentinelNode()
        {
            try
            {
                string ctrlRaw = HttpRequestLocal(string.Format("http://127.0.0.1:{0}/", ControlPort), "GET", 800);
                Dictionary<string, object> ctrlObj = this.json.Deserialize<Dictionary<string, object>>(ctrlRaw);
                if (ctrlObj != null && ctrlObj.ContainsKey("pid"))
                {
                    int pid = Convert.ToInt32(ctrlObj["pid"]);
                    if (pid > 0)
                    {
                        try { Process.GetProcessById(pid).Kill(); } catch { }
                    }
                }
            }
            catch { }

            if (this.lastKnownSentinelPid > 0)
            {
                try { Process.GetProcessById(this.lastKnownSentinelPid).Kill(); } catch { }
            }
        }

        private void ShutdownNodeSentinelGracefully()
        {
            try
            {
                HttpRequestLocal(string.Format("http://127.0.0.1:{0}/shutdown", ControlPort), "POST", 800);
            }
            catch
            {
                KillWedgedSentinelNode();
            }
        }

        private void SpawnNodeSentinelProcess()
        {
            if (!File.Exists(this.sentinelScriptPath)) return;
            if ((DateTime.UtcNow - this.lastSpawnAttemptUtc).TotalSeconds < 2.5) return;
            this.lastSpawnAttemptUtc = DateTime.UtcNow;

            ProcessStartInfo psi = new ProcessStartInfo();
            psi.FileName = ResolveNodeExePath();
            psi.Arguments = "\"" + this.sentinelScriptPath + "\"";
            psi.WorkingDirectory = this.pluginDir;
            psi.CreateNoWindow = true;
            psi.UseShellExecute = false;
            psi.WindowStyle = ProcessWindowStyle.Hidden;
            try
            {
                Process p = Process.Start(psi);
                if (p != null) this.lastKnownSentinelPid = p.Id;
            }
            catch { }
        }

        private bool EnsureNodeGatewayHealthy(bool forceSpawnIfDown)
        {
            try
            {
                string raw = HttpRequestLocal(string.Format("http://127.0.0.1:{0}/status", GatewayPort), "GET", 1200);
                Dictionary<string, object> data = this.json.Deserialize<Dictionary<string, object>>(raw);
                if (data != null && GetBool(data, "ok") && GetBool(data, "bridge_running"))
                {
                    this.consecutiveGatewayFailures = 0;
                    int pid = GetInt(data, "pid");
                    if (pid > 0) this.lastKnownSentinelPid = pid;
                    return true;
                }
            }
            catch { }

            this.consecutiveGatewayFailures++;
            if (forceSpawnIfDown || this.consecutiveGatewayFailures >= 2)
            {
                if (this.consecutiveGatewayFailures >= 2)
                {
                    KillWedgedSentinelNode();
                    Thread.Sleep(150);
                }
                SpawnNodeSentinelProcess();
                this.consecutiveGatewayFailures = 0;
            }
            return false;
        }

        private void TriggerRefreshAsync(bool forceSpawn)
        {
            if (this.pollBusy) return;
            this.pollBusy = true;
            Task.Run(delegate
            {
                try
                {
                    if (forceSpawn) EnsureNodeGatewayHealthy(true);
                    RefreshStatusCore();
                }
                finally
                {
                    this.pollBusy = false;
                }
            });
        }

        private void RefreshStatusCore()
        {
            Dictionary<string, object> st = null;
            try
            {
                string raw = HttpRequestLocal(string.Format("http://127.0.0.1:{0}/status", GatewayPort), "GET", 1400);
                st = this.json.Deserialize<Dictionary<string, object>>(raw);
            }
            catch
            {
                st = null;
            }

            if (st == null || !GetBool(st, "ok"))
            {
                EnsureNodeGatewayHealthy(false);
                UpdateUiSafe(delegate
                {
                    this.lblStatusDot.ForeColor = Color.FromArgb(255, 69, 58);
                    this.lblStatusText.Text = "本机网关未响应 · 守护狗正在自动拉起恢复…";
                    this.lblGatewayRow.Text = "本机网关：127.0.0.1:18195 正在自动拉起恢复中…";
                    this.lblCloudRow.Text = "云端链路：等待本机网关就绪";
                    this.lblErrorHint.Text = "提示：若持续未恢复可点击下方「重启内核」或「查看日志」";
                    SetIconState(0, "DSB · 桌面守护 (正在重启本机网关…)");
                });
                return;
            }

            this.consecutiveGatewayFailures = 0;
            int pid = GetInt(st, "pid");
            if (pid > 0) this.lastKnownSentinelPid = pid;
            bool vpsOnline = GetBool(st, "vps_online");
            bool phoneOnline = GetBool(st, "phone_online");
            bool dshAlive = GetBool(st, "dsh_host_alive") || IsDshProcessRunning();
            string route = GetStr(st, "active_route", "direct");
            int rtt = GetInt(st, "last_rtt_ms");
            int hbCount = GetInt(st, "heartbeat_count");
            string lastHb = GetStr(st, "last_heartbeat_at", "--");
            string lastErr = GetStr(st, "last_error", "");
            int sessions = GetInt(st, "sessions_count");
            int runningSessions = GetInt(st, "running_sessions_count");
            long totalBytes = GetLong(st, "bytes_sent") + GetLong(st, "bytes_received");

            if (!vpsOnline)
            {
                this.consecutiveCloudOfflineTicks++;
                if (this.consecutiveCloudOfflineTicks >= 12 && (DateTime.UtcNow - this.lastAutoReconnectUtc).TotalSeconds > 30)
                {
                    this.lastAutoReconnectUtc = DateTime.UtcNow;
                    try { HttpRequestLocal(string.Format("http://127.0.0.1:{0}/local/reconnect", GatewayPort), "POST", 1200); } catch { }
                }
            }
            else
            {
                this.consecutiveCloudOfflineTicks = 0;
            }

            UpdateUiSafe(delegate
            {
                string routeLabel = route.StartsWith("proxy") ? "本地代理通道 (" + route.Replace("proxy ", "") + ")" : "高速直连通道 (Direct)";
                if (vpsOnline)
                {
                    this.lblStatusDot.ForeColor = Color.FromArgb(48, 209, 88);
                    this.lblStatusText.Text = string.Format("云端在线守护中 · {0} · RTT {1}ms", routeLabel, rtt);
                    SetIconState(2, string.Format("DSB · 电脑端在线 ({0}, {1}ms)", route.StartsWith("proxy") ? "代理" : "直连", rtt));
                }
                else
                {
                    this.lblStatusDot.ForeColor = Color.FromArgb(255, 196, 46);
                    this.lblStatusText.Text = "本机网关正常 · 正在连接/重连云端服务器…";
                    SetIconState(1, "DSB · 本机网关就绪 (正在重连云端…)");
                }

                this.lblGatewayRow.Text = string.Format("本机网关：127.0.0.1:{0} 常驻独占正常 (PID {1})", GatewayPort, pid);
                this.lblCloudRow.Text = string.Format("云端链路：{0} · 最近心跳 {1} (累计 #{2})", routeLabel, lastHb, hbCount);
                this.lblPeersRow.Text = string.Format(
                    "跨端协同：手机端 {0}  |  DeepSeek Harness 桌面端：{1}",
                    phoneOnline ? "🟢 在线" : "⚪ 离线/待机",
                    dshAlive ? "🟢 运行中" : "⚪ 未打开 (后台仍保持在线)"
                );
                this.lblTrafficRow.Text = string.Format(
                    "会话流量：本机 DSH 会话 {0} 个 ({1} 运行中) · 累计收发 {2:0.0} KB",
                    sessions,
                    runningSessions,
                    totalBytes / 1024.0
                );
                this.lblErrorHint.Text = (!vpsOnline && !string.IsNullOrEmpty(lastErr))
                    ? ("最近网络诊断：" + (lastErr.Length > 58 ? lastErr.Substring(0, 58) + "…" : lastErr))
                    : "";
            });
        }

        private void ManualReconnectClicked()
        {
            this.lblStatusText.Text = "正在立即重新探测直连/代理路由并握手云端…";
            Task.Run(delegate
            {
                try
                {
                    HttpRequestLocal(string.Format("http://127.0.0.1:{0}/local/reconnect", GatewayPort), "POST", 1500);
                }
                catch
                {
                    EnsureNodeGatewayHealthy(true);
                }
                Thread.Sleep(600);
                RefreshStatusCore();
            });
        }

        private void ManualRestartCoreClicked()
        {
            this.lblStatusText.Text = "正在重启常驻网关内核 (sentinel.mjs)…";
            Task.Run(delegate
            {
                ShutdownNodeSentinelGracefully();
                Thread.Sleep(350);
                KillWedgedSentinelNode();
                this.lastSpawnAttemptUtc = DateTime.MinValue;
                SpawnNodeSentinelProcess();
                Thread.Sleep(900);
                RefreshStatusCore();
            });
        }

        private bool IsDshProcessRunning()
        {
            try
            {
                return Process.GetProcessesByName("DeepSeek Harness").Length > 0;
            }
            catch
            {
                return false;
            }
        }

        private void LaunchDshGui()
        {
            string[] candidates = new string[]
            {
                @"D:\dsh\DeepSeek Harness.exe",
                Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "Programs", "DeepSeek Harness", "DeepSeek Harness.exe")
            };
            foreach (string exe in candidates)
            {
                if (File.Exists(exe))
                {
                    try
                    {
                        ProcessStartInfo psi = new ProcessStartInfo(exe);
                        psi.WorkingDirectory = Path.GetDirectoryName(exe);
                        psi.UseShellExecute = true;
                        Process.Start(psi);
                        return;
                    }
                    catch { }
                }
            }
            MessageBox.Show("未找到 D:\\dsh\\DeepSeek Harness.exe，请检查安装目录。", "提示", MessageBoxButtons.OK, MessageBoxIcon.Information);
        }

        private void OpenLogFile()
        {
            try
            {
                Directory.CreateDirectory(Path.GetDirectoryName(this.logFilePath));
                if (!File.Exists(this.logFilePath))
                {
                    File.WriteAllText(this.logFilePath, "", Encoding.UTF8);
                }
                Process.Start("notepad.exe", "\"" + this.logFilePath + "\"");
            }
            catch { }
        }

        private bool IsAutostartEnabled()
        {
            try
            {
                using (RegistryKey k = Registry.CurrentUser.OpenSubKey(RunRegPath, false))
                {
                    if (k == null) return false;
                    object val = k.GetValue(RunRegName);
                    return val != null && val.ToString().IndexOf("DeepSeekClawSentinel", StringComparison.OrdinalIgnoreCase) >= 0;
                }
            }
            catch
            {
                return false;
            }
        }

        private void EnsureAutostartSynced()
        {
            try
            {
                if (IsAutostartEnabled())
                {
                    using (RegistryKey k = Registry.CurrentUser.OpenSubKey(RunRegPath, true))
                    {
                        if (k != null)
                        {
                            k.SetValue(RunRegName, "\"" + Application.ExecutablePath + "\" --minimized");
                        }
                    }
                }
            }
            catch { }
        }

        private void ToggleAutostart()
        {
            try
            {
                bool currentlyOn = IsAutostartEnabled();
                using (RegistryKey k = Registry.CurrentUser.OpenSubKey(RunRegPath, true))
                {
                    if (k != null)
                    {
                        if (currentlyOn) k.DeleteValue(RunRegName, false);
                        else k.SetValue(RunRegName, "\"" + Application.ExecutablePath + "\" --minimized");
                    }
                }
            }
            catch { }
            UpdateAutostartButtonText();
        }

        private void UpdateAutostartButtonText()
        {
            bool on = IsAutostartEnabled();
            this.btnToggleAutostart.Text = on
                ? "✅ Windows 开机后台自启：已开启 (点击关闭)"
                : "⚪ Windows 开机后台自启：已关闭 (点击开启)";
            this.btnToggleAutostart.ForeColor = on
                ? Color.FromArgb(134, 239, 172)
                : Color.FromArgb(148, 163, 184);
        }

        private void UpdateUiSafe(Action action)
        {
            if (this.IsDisposed) return;
            try
            {
                if (this.InvokeRequired) this.BeginInvoke(action);
                else action();
            }
            catch { }
        }

        private static bool GetBool(Dictionary<string, object> d, string k)
        {
            object v;
            if (d == null || !d.TryGetValue(k, out v) || v == null) return false;
            if (v is bool) return (bool)v;
            return string.Equals(v.ToString(), "true", StringComparison.OrdinalIgnoreCase);
        }

        private static int GetInt(Dictionary<string, object> d, string k)
        {
            object v;
            if (d == null || !d.TryGetValue(k, out v) || v == null) return 0;
            try { return Convert.ToInt32(v); } catch { return 0; }
        }

        private static long GetLong(Dictionary<string, object> d, string k)
        {
            object v;
            if (d == null || !d.TryGetValue(k, out v) || v == null) return 0L;
            try { return Convert.ToInt64(v); } catch { return 0L; }
        }

        private static string GetStr(Dictionary<string, object> d, string k, string defVal)
        {
            object v;
            if (d == null || !d.TryGetValue(k, out v) || v == null) return defVal;
            return v.ToString();
        }
    }

    internal static class Program
    {
        [STAThread]
        private static void Main(string[] args)
        {
            try { NativeMethods.SetProcessDPIAware(); } catch { }

            bool minimized = false;
            if (args != null)
            {
                foreach (string a in args)
                {
                    if (string.Equals(a, "--minimized", StringComparison.OrdinalIgnoreCase) ||
                        string.Equals(a, "-m", StringComparison.OrdinalIgnoreCase))
                    {
                        minimized = true;
                    }
                }
            }

            bool createdNew = false;
            EventWaitHandle wakeEvent = new EventWaitHandle(false, EventResetMode.AutoReset, @"Local\DeepSeekClawSentinel_ShowEvent_v3");
            using (Mutex singleMutex = new Mutex(true, @"Local\DeepSeekClawSentinel_Mutex_v3", out createdNew))
            {
                if (!createdNew)
                {
                    if (!minimized)
                    {
                        try { wakeEvent.Set(); } catch { }
                    }
                    return;
                }

                ServicePointManager.Expect100Continue = false;
                ServicePointManager.DefaultConnectionLimit = 16;
                Application.EnableVisualStyles();
                Application.SetCompatibleTextRenderingDefault(false);
                Application.Run(new SentinelForm(minimized, wakeEvent));
            }
        }
    }
}
