from pathlib import Path
from datetime import datetime,timezone,timedelta
import hashlib,json,shutil,zipfile,os
from PIL import Image,ImageDraw

ROOT=Path(__file__).resolve().parents[1]
OUT=Path(r'C:\Users\uiwcv\Documents\Codex\2026-11-02\ui-task-spec-for-gpt-md\outputs')
OUT.mkdir(exist_ok=True,parents=True)
work=ROOT/'work'
apk=json.loads((work/'apk-download-verification.json').read_text())
assert apk['head_status']==200 and apk['sha256']==apk['download_sha256']
assert hashlib.sha256((ROOT/'dist/proactive-agent.apk').read_bytes()).hexdigest()==apk['sha256']
assert hashlib.sha256((ROOT/'android/app/build/outputs/apk/release/app-release.apk').read_bytes()).hexdigest()==apk['sha256']
assert '12 passed' in (ROOT/'work-server-tests.log').read_text('utf-8',errors='replace')
assert 'OK (1 test)' in (work/'android-instrumentation.txt').read_text('utf-8')
assert 'BUILD SUCCESSFUL' in (ROOT/'work-android-build.log').read_text('utf-8',errors='replace')
assert 'foreground_guard_refused":true' in (work/'virtual-display-runtime.txt').read_text()
deploy=json.loads((work/'deployment-verification.json').read_text());assert all(deploy.values())
harness=json.loads((work/'harness-runtime.json').read_text());assert all(harness.values())
before=(work/'protected-services.before.txt').read_text().strip()
after=(work/'protected-services.after.txt').read_text().strip()
assert before==after,'Protected service state/PID changed'
head=(work/'public-head-us.txt').read_text();assert '200 OK' in head
signature=(work/'apk-signature.txt').read_text();assert 'Verifies' in signature
token=(ROOT/'private/device-token').read_text().strip()
with zipfile.ZipFile(ROOT/'dist/proactive-agent.apk') as package:
    assert all(token.encode() not in package.read(name) for name in package.namelist())
    assert not any(name.lower().endswith(('.jks','.keystore','.pem')) for name in package.namelist())

photos=work/'android-evidence/files'
if not photos.exists():photos=work/'android-evidence'
files=sorted(photos.glob('*.png'))
canvas=Image.new('RGB',(1200,1760),'#0b0d12');draw=ImageDraw.Draw(canvas)
for i,path in enumerate(files[:6]):
    canvas.paste(Image.open(path).convert('RGB').resize((380,824)),((i%3)*400+10,(i//3)*880+32))
    draw.text(((i%3)*400+12,(i//3)*880+10),path.stem,fill='#b8a4ff')
canvas.save(OUT/'ui-preview.png')
shutil.copy2(work/'proactive-virtual-display-after.png',OUT/'virtual-display-proof.png')
shutil.copy2(ROOT/'dist/proactive-agent.apk',OUT/'proactive-agent.apk')
shutil.copy2(ROOT/'dist/SHA256SUMS.txt',OUT/'SHA256SUMS.txt')

now=datetime.now(timezone(timedelta(hours=8),'Asia/Shanghai')).strftime('%Y-%m-%d %H:%M:%S %Z')
report=f'''# 共生 Pulse · 交付验收报告

交付时间：{now}。本报告包含私人设备配对码，仅保存在本机，**不要公开转发**。

## 直接使用

- **公网 APK**：[点击下载共生 Pulse]({apk['url']})
- 服务地址：[日本节点](http://216.23.82.196/proactive-agent/)
- 默认 Server URL：`http://216.23.82.196/proactive-agent`
- 本地 APK：`D:\\bubuki\\proactive-personal-agent\\dist\\proactive-agent.apk`
- Android 版本要求：Android 12 / API 31 及以上；版本 1.0.0，包名 `dev.bubuki.proactive`。
- 项目全部源码：`D:\\bubuki\\proactive-personal-agent`。

**私人设备配对码**（在 App「双脑同步」中粘贴）：

```text
{token}
```

配对码未写进公开 APK。然后填入小脑/大脑 API Base URL、Key、Model，以及需要使用的 SMTP，点击 **「⚡ 保存并同步至云端启动 Harness」**。

## 已验证结果

| 项目 | 验收结果与证据 |
|---|---|
| 编译 / Lint | Release 编译成功，Lint 0 个错误；保留非阻断的平台/依赖提示 |
| APK 签名 | `apksigner verify` 通过，RSA 3072，APK Signature Scheme v2 |
| 服务端 | 12 项测试全部通过，覆盖配置密钥遮蔽/保留、聊天、记忆库、路径逃逸、成果确认幂等、三级状态机、加密/重放、WebSocket、免打扰重连、SMTP 等待中已读、视觉坐标验证和工具循环 |
| 签名 Release 实测 | Android 15 / API 35 隔离模拟器，**1 项端到端场景通过**：公网加密配对、配置同步、聊天、Markdown 预览/编辑保存、真实快照成果、后台加密 WebSocket、Stage 1 系统通知、Stage 3 原生全局悬浮窗 |
| 真正的虚拟屏 | 同一最终 Release 的 helper 在 Android shell UID 2000 下，实际创建独立 displayId、拒绝搬动 display 0 正在使用的同包 App、启动独立测试 Activity、截图、点击 `(270,570)`，截图中按钮变为“点击已到达独立虚拟屏 ✓”，最后释放显示器 |
| Harness 运行 | 日本机实际 write/edit/read、Bash 和分段输出回收通过；Landlock 拒绝工作区外读取、写服务端代码与 TCP 连接；公开网页抓取通过、环回 URL 被拒绝 |
| 日本部署 | 独立 venv + `proactive-agent.service`，active/enabled，**仅监听 `127.0.0.1:18190`** |
| nginx | 独立 `/proactive-agent/` HTTP/WebSocket 路径；对原有配置只新增 include。自动比对确认原有 location、上游与其他非空配置行一致 |
| 受保护服务 | Claude/BB/浏览器/Docker/sing-box 的服务状态、MainPID、启动时间字段前后完全相同；未执行 Docker 操作，未改防火墙、路由或代理 |
| 数据权限 | 服务器 device-token、传输私钥、config.json、state.json 均为 0600；App 使用 Keystore 加密本地设置 |
| 公网下载 | 美国节点 `curl -I` 返回 **200 OK**；本机经既有代理完整下载，并比较字节数及 SHA-256 |

最终 APK 大小：**{apk['local_bytes']:,} 字节**。HEAD Content-Length、本地文件和公网全量下载三者一致。

```text
SHA-256: {apk['sha256']}
签名证书 SHA-256: 879d8a5eb7e55e054925d1030f350b3441276e46bef549214001b2bfc4672329
```

## 尚需你的配置 / 真机授权

- **没有借用或寻找你现有网关的私人 Key，也没有消耗付费模型或 GPU。** 小脑、大脑、VLM 的真实推理与结果质量需你填入可用 API 后验收。工具循环和坐标失败分支已用受控模型响应测试，但这不等于真实模型质量验收。
- **SMTP 未配置。** 已验证缺少配置时返回 `sent: false` 与具体原因，以及状态机升级；没有声称真实邮件发送/投递成功。填好邮箱授权码后，使用「发送测试催单邮件」。
- 未在你的真实手机安装、授权或操作应用。Root 的 `su` 授权链与 Shizuku 的 UserService 授权/绑定仍需你的设备；本次在同一 Release 上实际验证了 shell UID 2000 的虚拟屏、截图和指定 displayId 点击。OEM / Android 版本兼容性不能由这一台模拟器代替全部真机验证。
- 本机对日本 IP 的直接路由超时；**通过既有本地代理和美国公网节点验证可达**。未修改 Windows 或日本机的代理/网络配置。手机若同样直连不通，需要使用你已有的可达网络。
- 按任务要求使用 HTTP 公网地址。App API/WS 载荷有固定公钥加密；APK 下载本身应核对上述 SHA-256 与签名。

## 界面与运行证据

四页及 Stage 3 均来自实际运行的签名 APK，未使用设计稿代替运行结果：

![四页与悬浮窗](C:/Users/uiwcv/Documents/Codex/2026-11-02/ui-task-spec-for-gpt-md/outputs/ui-preview.png)

![独立虚拟屏点击结果](C:/Users/uiwcv/Documents/Codex/2026-11-02/ui-task-spec-for-gpt-md/outputs/virtual-display-proof.png)

本地详细证据在项目 `work/`：`android-instrumentation.txt`、`virtual-display-runtime.txt`、`harness-runtime.json`、`deployment-verification.json`、`apk-download-verification.json`、`apk-signature.txt`、`public-head-us.txt`、`protected-services.before.txt` / `after.txt`。服务端测试日志与 Gradle 构建日志在项目根目录 `work-server-tests.log` / `work-android-build.log`。

测试发现并修复了：Windows AIDL 生成注释的 Unicode 转义、虚拟屏启动 Intent 参数、前台任务识别应读取 displays、Bash 流输出回收、免打扰重连避免重新霸屏、SMTP 等待中已读取消升级。最终使用清理自身免打扰状态的验收场景通过；不会把测试隔离问题掩盖成产品验收成功。

## 电脑安静模式

已查到 LLT 的“接电游戏 → Performance”规则；现有四条电源/游戏规则已全部改为 Quiet。命令行已设定并回读 quiet，当前用户的 `Codex-Keep-Legion-Quiet` 守护任务正在运行，每 10 秒纠正一次其他档位。未改风扇曲线、散热保护、GPU 模式等设置。

**当前 LLT 进程缓存的旧规则仍能瞬时切档**（守护日志已记录并纠正）。磁盘配置下次正常启动生效；若要现在立刻处理当前缓存，需要先手动关闭 Windows 提权提示。自动化没有代点这个权限窗口。安静模式详细检查、原配置备份与撤销说明见旁边的 `quiet-mode-report.md`。

检查时 GPU 为 0%、约 1.48 W，未找到正在挖矿/远控的证据；这次有界检查不能保证不存在恶意软件，没有进行影响睡觉的全盘扫描。
'''
(ROOT/'DELIVERY_REPORT.md').write_text(report,encoding='utf-8')
(OUT/'DELIVERY_REPORT.md').write_text(report,encoding='utf-8')
shutil.copy2(ROOT/'README.md',OUT/'README.md')

with zipfile.ZipFile(OUT/'proactive-personal-agent-source.zip','w',zipfile.ZIP_DEFLATED) as package:
    for fixed in ['README.md','TASK_SPEC_FOR_GPT.md','.gitignore']:
        package.write(ROOT/fixed,fixed)
    for name in ['server','android','scripts']:
        for folder,dirs,names in os.walk(ROOT/name):
            dirs[:]=[d for d in dirs if d not in ('.venv','.gradle','build','__pycache__','.pytest_cache','data','work')]
            for filename in names:
                file=Path(folder)/filename
                if filename=='local.properties' or filename.endswith(('.pyc','.jks','.keystore')):continue
                package.write(file,file.relative_to(ROOT).as_posix())
print('DELIVERY_READY',apk['local_bytes'],apk['sha256'])
