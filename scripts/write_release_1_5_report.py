"""Publish the client-only delivery report from immutable acceptance and public readback."""
from pathlib import Path
import datetime,hashlib,json,shutil,zipfile
from PIL import Image,ImageDraw,ImageFont

ROOT=Path(__file__).resolve().parents[1];WORK=ROOT/'work'
OUT=Path(r'C:\Users\uiwcv\Documents\Codex\2026-11-02\ui-task-spec-for-gpt-md\outputs');OUT.mkdir(exist_ok=True)
def read(name):return json.loads((WORK/name).read_text('utf-8'))
gate=read('final-release-gate.json');pub=read('release-1.5-publication.json');download=read('apk-download-verification.json')
source=read('source-delivery.json');motion=read('motion-summary-1.5.json');server=read('server-unchanged-1.5.json')
pixels=read('recorded-motion-comparison-1.5.json')['comparison']
local=read('mika-acceptance/files/mika-acceptance.json');live=read('public-computer-acceptance/files/public-computer-acceptance.json')
apk=ROOT/'dist/proactive-agent.apk';digest=hashlib.sha256(apk.read_bytes()).hexdigest()
assert gate['version']=='1.5.0' and digest==gate['apk_sha256']==pub['apk_sha256']==download['download_sha256']==motion['apk_sha256']
assert len(gate['checks'])==5 and all(c['passed'] for c in gate['checks'])
assert local['coordinate_tap'] and local['real_local_llm_loop'] and live['native_media_store_download_bytes']==768034
assert server['unchanged'] and all(hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==sha for name,sha in server['server_files'].items())
assert '30 passed' in (WORK/'server-1.4-final-tests.txt').read_text('utf-8')
assert '879d8a5eb7e55e054925d1030f350b3441276e46bef549214001b2bfc4672329' in (WORK/'apk-1.5-signature.txt').read_text('utf-8')
assert (WORK/'quiet-mode-1.5.txt').read_text('utf-8-sig').strip()=='quiet'
secrets=[(ROOT/'private/device-token').read_bytes().strip(),(ROOT/'private/keystore-password').read_bytes().strip()]
with zipfile.ZipFile(apk) as archive:
    for entry in archive.infolist():
        data=archive.read(entry)
        assert not any(secret in data for secret in secrets),'Private credential in APK'

screens=OUT/'screenshots-1.5';screens.mkdir(exist_ok=True)
for folder,prefix in [(WORK/'android-v2-evidence/files','v2-'),(WORK/'bot-management-acceptance/files','bots-'),(WORK/'mika-acceptance/files','mika-'),(WORK/'public-computer-acceptance/files','public-')]:
    for p in folder.glob(prefix+'*.png'):shutil.copy2(p,screens/p.name)
for old,name in [('motion-rebuild/evidence/motion-acceptance.mp4','motion-1.5.mp4'),('android-v2-evidence/motion-acceptance.mp4','ui-1.5.mp4'),('mika-acceptance/motion-acceptance.mp4','mika-local-1.5.mp4'),('bot-management-acceptance/motion-acceptance.mp4','bots-1.5.mp4')]:shutil.copy2(WORK/old,OUT/name)
for old,name in [('final-release-gate.json','release-1.5-acceptance.json'),('motion-summary-1.5.json','motion-summary-1.5.json'),('mika-acceptance/files/mika-acceptance.json','mika-1.5-acceptance.json'),('public-computer-acceptance/files/public-computer-acceptance.json','public-1.5-acceptance.json'),('rendering-study-1.4.json','rendering-study-1.4.json')]:shutil.copy2(WORK/old,OUT/name)
shutil.copy2(WORK/'recorded-motion-comparison-1.5.json',OUT/'recorded-motion-comparison-1.5.json')
shutil.copy2(WORK/'motion-1.5-preview.gif',OUT/'motion-1.5-preview.gif')
shutil.copy2(apk,OUT/'proactive-agent.apk');shutil.copy2(ROOT/'dist/proactive-personal-agent-source.zip',OUT/'proactive-personal-agent-source.zip')
font=ImageFont.truetype(r'C:\Windows\Fonts\msyh.ttc',18)
canvas=Image.new('RGB',(1032,792),'#161820');draw=ImageDraw.Draw(canvas)
for i,(name,label) in enumerate([('public-01-home.png','助手与云电脑'),('v2-23-chat-dark.png','深色对话'),('public-07-mika-models.png','Mika 原生模型')]):
    picture=Image.open(screens/name).convert('RGB');picture.thumbnail((324,702));x=12+i*344;canvas.paste(picture,(x,48));draw.text((x+5,13),label,font=font,fill='#ededf3')
canvas.save(OUT/'ui-1.5-preview.png')
date=datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).isoformat(timespec='seconds')
before=motion['baseline']['first-push']['max_total_ms'];after=motion['current']['first-push']['max_total_ms'];warm=motion['current']['warm-push']['max_total_ms']
checks='\n'.join(f"| {c['check']} | 通过 | {c['seconds']:.2f} s |" for c in gate['checks'])
common=f'''# Pulse Bot 1.5.0 交付与验收

时间：{date}

APK：<http://216.23.82.196/proactive-agent/download/proactive-agent.apk>

## 本次改动

- 全屏侧滑去掉大面积模糊阴影，用 1px 边缘分界保留前后层次；卡片和按钮的小阴影保留。
- 页面侧滑使用零初速度曲线，正向 380 ms、返回 330 ms 均不加长。正常返回的边缘跟随前景页面；手势返回、取消、预测返回、面板拖动和减弱动画保留。
- 同一 APK 内 Mika 原生模块的版本字段统一为 1.5.0 / code 6，避免原生设置及更新判断停留在 1.3。
- 本次为客户端更新。日本节点主服务仍是 1.4.0，云电脑实现保持原版本；后端 {len(server['server_files'])} 个文件与已验收 1.4 源码逐字节一致，沿用其 30 项后端测试，没有重新部署或重启服务。

## 动画证据及边界

原版 1.4 的 Perfetto 完整跟踪显示 WebViewFunctor::drawGl 约 142 ms 阻塞，同期 Chrome GPU 线程创建着色器/纹理。第二份跟踪没有缓冲区覆盖；在相同 1.4 APK 内逐项禁用整屏阴影，首次峰值从约 141 ms 降至约 90 ms。关闭全部阴影仍约 80 ms，说明单一阴影不是全部冷启动成本。持久合成层、渐变边缘和 View Transitions 的实验没有稳定消除长帧，未用于产品。

最终 1.5 APK 无录屏对照：首次 Window 总帧时峰值 {after} ms，热切页 {warm} ms；1.4 同一基准方法首次为 {before} ms。原始记录在 `motion-summary-1.5.json` 与 `rendering-study-1.4.json`。这些是本机双核 Android 模拟器的 Window 帧指标，不能当作实体手机稳定 60 fps 的证明；是否完整流畅还需结合实际录屏和实体设备。

最终包冷帧峰值没有稳定下降，因此不宣称冷启动卡顿已修复。实际原生录屏的返回箭头像素追踪显示，图标首次可匹配时，侧滑进度从 1.4 的 {pixels['1.4']['glyph_progress_at_first_match_pct']}% 变为 1.5 的 {pixels['1.5']['glyph_progress_at_first_match_pct']}%，能看到更早的一段位移；这不是 FPS 指标，也不等于每一帧都连续。`motion-1.5-preview.gif` 是最终包首次侧滑与返回的原始录屏节选。

原始系统跟踪在项目 `work/cold-trace-2/cold-start.pftrace`，SQL 在同目录 `focus.sql`；采集工具为官方 Perfetto v59.1 Windows 二进制，下载和 SHA-256 记录在 `work/tools/perfetto/receipt.json`。FrameTimeline 解释参照 [Perfetto 官方文档](https://perfetto.dev/docs/data-sources/frametimeline)。

## 签名与公网回读

| 项目 | 实测 |
| --- | --- |
| 客户端 | 1.5.0 / versionCode 6 |
| APK 字节数 | {apk.stat().st_size:,} |
| APK SHA-256 | `{digest}` |
| 签名证书 SHA-256 | `879d8a5eb7e55e054925d1030f350b3441276e46bef549214001b2bfc4672329` |
| curl HEAD | 200 OK |
| 完整公网 GET | {download['download_bytes']:,} 字节，哈希与本地一致 |

## 功能验收

同一不可变签名 APK 的五项安卓流程：

| 流程 | 结果 | 用时 |
| --- | --- | --- |
{checks}

- 名册：长按、置顶、隐藏全部、空状态、恢复、设置入口、草稿隔离与 Activity 重建。
- UI：实际触摸一级/二级页面、对话、工具、角色权限、Markdown 编辑回读、例行任务、WebSocket 通知与催单回执。键盘检查要求真实 IME 可见、动画结束且视图高度稳定，再采样发送位置；避免输入法尚在弹出时点击旧坐标。
- Motion：侧滑、快速返回、边缘手势成功/取消、面板拖动/重开、键盘跟随、旧消息节点保留、减弱动画。
- Mika：签名助手交接 uid 2000，独立 VirtualDisplay、实际坐标点击和截图；真实本地模型循环连接免费隔离桩；791,321 字节加密上传和原生下载相等。
- 公网客户端：连接日本节点真实云电脑、接管、768,034 字节原生下载逐字节核对；六个 Mika 原生二级页面跟随门户深色设置。
- 没有付费模型调用、真实 SMTP 邮件发送或真实手机操作。APK 与源码包均扫描排除实际配对码、私钥和签名密码。

## 云电脑与使用

服务器：`http://216.23.82.196/proactive-agent`。安装后在设置中填私人设备配对码，然后配置模型；「云电脑」可查看画面、接管、输入点击、交还、停止和传输文件。手动操作可不配模型；AI 执行需要用户自己的 API Key、图片输入及结构化输出模型。

云端复用 Browser Use 0.13.14 / Playwright 1.56，位于日本机宿主 `/opt/proactive-agent/`，独立用户、venv、systemd、Xvfb :91、Openbox 和 Chromium profile。主 API 为 `127.0.0.1:18190`，桌面控制 18193、网页代理 18194 和 CDP 均仅回环。复用既有 nginx `/proactive-agent/` HTTP/WebSocket 路由。本轮没有 Docker 操作，也没有触碰既有 Claude/BB/浏览器/代理配置。

「此手机」直接使用集成的 Mika 原生应用。模型、手机连接、记忆/技能、浏览器、计划和设置页面保留；可明确选择复制已保存在本机的云端模型配置。默认无常驻悬浮球。

## 交付文件

- `outputs/ui-1.5-preview.png`：实际截图预览。
- `outputs/motion-1.5.mp4`、`ui-1.5.mp4`、`mika-local-1.5.mp4`、`bots-1.5.mp4`：原始录屏。
- `outputs/screenshots-1.5/`、`release-1.5-acceptance.json`、`mika-1.5-acceptance.json`、`public-1.5-acceptance.json`：验收证据。
- 源码 ZIP {source['bytes']:,} 字节，SHA-256 `{source['sha256']}`，本机 `dist/proactive-personal-agent-source.zip` 与 outputs 副本一致。
- Mika 固定上游 `e9daf53d6834b236e713c6a62c3a095338eb7f95`，Apache-2.0；原生对应源码仍为 <http://216.23.82.196/proactive-agent/download/mika-corresponding-source.tar.gz>，{pub['gpl_source_bytes']:,} 字节，SHA-256 `{pub['gpl_source_sha256']}`。

## 完整目标仍在进行

尚未完成主助手替换、身份 Title/图片头像、删除/重置、群组/线程、完整技能/例行任务管理、连接器、语音等 Grok 产品能力；视觉和动效尚未逐屏与登录后的原版对齐。原版 1.19 在本机 ARM 转译模拟器 libpairipcore 崩溃，未进入登录后页面。Mika 原生 ARM Linux 运行时、Root/Shizuku 控制尚未在 ARM64 实体手机上验收。当前成绩不等于“完全照抄已完成”。

电脑回读为 `quiet`；测试后关闭了本次启动的模拟器。
'''
assert not any(secret.decode() in common for secret in secrets)
private=common+'\n## 本机私人配对\n\n以下内容不进入公开报告或源码包。\n\n设备配对码：`'+secrets[0].decode()+'`\n\nApp → 设置 → 云端节点，URL 保持 `http://216.23.82.196/proactive-agent`。\n'
for p in [ROOT/'DELIVERY_REPORT.md',OUT/'DELIVERY_REPORT.md']:
    if p.exists() and '# Pulse Bot 1.4.0 ' in p.read_text('utf-8'):shutil.copy2(p,p.with_name('DELIVERY_REPORT_1_4.md'))
    p.write_text(private,'utf-8')
(OUT/'RELEASE_1_5.md').write_text(common,'utf-8')
items=['proactive-agent.apk','proactive-personal-agent-source.zip','ui-1.5-preview.png','motion-1.5-preview.gif','motion-1.5.mp4','ui-1.5.mp4','mika-local-1.5.mp4','bots-1.5.mp4']
manifest=''.join(hashlib.sha256((OUT/name).read_bytes()).hexdigest()+'  '+name+'\n' for name in items)
for name in ['SHA256SUMS_1_5.txt','SHA256SUMS.txt']:(OUT/name).write_text(manifest,'utf-8')
print(json.dumps({'version':'1.5.0','apk_sha256':digest,'report':str(ROOT/'DELIVERY_REPORT.md'),'private_pairing_printed':False,'full_grok_goal_complete':False},indent=2))
