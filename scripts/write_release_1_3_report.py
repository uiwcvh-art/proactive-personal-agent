"""Write the delivery only from accepted, read-back artifacts. Never print the pairing code."""
from pathlib import Path
import datetime,hashlib,json,shutil,zipfile
from PIL import Image,ImageDraw,ImageFont
ROOT=Path(__file__).resolve().parents[1]
OUT=Path(r'C:\Users\uiwcv\Documents\Codex\2026-11-02\ui-task-spec-for-gpt-md\outputs');OUT.mkdir(exist_ok=True)
WORK=ROOT/'work'
def read(name):return json.loads((WORK/name).read_text('utf-8'))
gate=read('final-release-gate.json');pub=read('release-publication.json');download=read('apk-download-verification.json')
desktop=read('cloud-computer/acceptance.json');shell=read('cloud-computer/workspace-acceptance.json');runtime=read('cloud-computer/runtime-proof.json');public=read('cloud-computer/public-api.json')
source=read('source-delivery.json');deploy=read('redesign-deployment.json');connection=read('cloud-computer/connection.json')
mika=json.loads((WORK/'mika-acceptance/files/mika-acceptance.json').read_text('utf-8'))
live=json.loads((WORK/'public-computer-acceptance/files/public-computer-acceptance.json').read_text('utf-8'))
apk=ROOT/'dist/proactive-agent.apk';digest=hashlib.sha256(apk.read_bytes()).hexdigest()
assert digest==gate['apk_sha256']==pub['apk_sha256']==download['sha256']==download['download_sha256']
assert pub['curl_head_200'] and len(gate['checks'])==4 and all(x['passed'] for x in gate['checks'])
assert '29 passed' in (WORK/'server-final-tests.txt').read_text('utf-8')
assert desktop['real_harness_navigation'] and desktop['real_harness_stop'] and shell['actual_bash']
assert live['native_media_store_download_bytes']==768034 and len(live['native_secondary_pages'])==6
assert mika['coordinate_tap'] and mika['real_local_llm_loop'] and mika['encrypted_file_upload_download_bytes']==791321
assert deploy['protected_services_unchanged'] and connection['protected_services_unchanged']
assert '879d8a5eb7e55e054925d1030f350b3441276e46bef549214001b2bfc4672329' in (WORK/'apk-1.3-signature.txt').read_text('utf-8')
assert (WORK/'quiet-mode-final.txt').read_text('utf-8-sig').strip()=='quiet'
secrets=[(ROOT/'private/device-token').read_bytes().strip(),(ROOT/'private/keystore-password').read_bytes().strip()]
with zipfile.ZipFile(apk) as archive:
    for item in archive.infolist():
        data=archive.read(item)
        if any(secret in data for secret in secrets):raise RuntimeError('Private credential found in release APK entry')
screens=OUT/'screenshots-1.3';screens.mkdir(exist_ok=True)
for folder in [WORK/'android-v2-evidence/files',WORK/'mika-acceptance/files',WORK/'public-computer-acceptance/files']:
    for p in folder.glob('*.png'):
        if p.name.startswith(('v2-','mika-','public-')):shutil.copy2(p,screens/p.name)
for source_path,name in [(WORK/'motion-rebuild/evidence/motion-acceptance.mp4','motion-1.3.mp4'),(WORK/'mika-acceptance/motion-acceptance.mp4','mika-local-1.3.mp4'),(WORK/'android-v2-evidence/motion-acceptance.mp4','ui-1.3.mp4')]:shutil.copy2(source_path,OUT/name)
for source_path,name in [(WORK/'cloud-computer/final-frame.jpg','cloud-computer-1.3.jpg'),(WORK/'cloud-computer/input-proof.jpg','cloud-input-proof.jpg'),(WORK/'cloud-computer/browser-proof.jpg','cloud-browser-proof.jpg')]:shutil.copy2(source_path,OUT/name)
for src,name in [(WORK/'cloud-computer/acceptance.json','cloud-harness-acceptance.json'),(WORK/'cloud-computer/workspace-acceptance.json','cloud-workspace-acceptance.json'),(WORK/'mika-acceptance/files/mika-acceptance.json','mika-acceptance.json'),(WORK/'public-computer-acceptance/files/public-computer-acceptance.json','public-computer-acceptance.json'),(WORK/'motion-rebuild/evidence/files/motion-audit.json','motion-1.3-audit.json'),(WORK/'final-release-gate.json','release-1.3-acceptance.json')]:shutil.copy2(src,OUT/name)
shutil.copy2(apk,OUT/'proactive-agent.apk');shutil.copy2(ROOT/'dist/proactive-personal-agent-source.zip',OUT/'proactive-personal-agent-source.zip')
items=[('public-01-home.png','助手与两种执行入口'),('public-02-cloud-computer.png','真实云电脑'),('public-07-mika-models.png','Mika 原生模型设置')]
font=ImageFont.truetype(r'C:\Windows\Fonts\msyh.ttc',18)
canvas=Image.new('RGB',(1032,792),'#161820');draw=ImageDraw.Draw(canvas)
for i,(name,label) in enumerate(items):
    im=Image.open(screens/name).convert('RGB');im.thumbnail((324,702));x=12+i*344;canvas.paste(im,(x,48));draw.text((x+5,13),label,font=font,fill='#ededf3')
canvas.save(OUT/'ui-1.3-preview.png')
motion=json.loads((WORK/'motion-rebuild/evidence/files/motion-audit.json').read_text('utf-8'))
motion_rows='\n'.join(f"| {x['name']} | {x['distinct_positions']} |" for x in motion if 'distinct_positions' in x)
date=datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).isoformat(timespec='seconds')
initial=json.loads((WORK/'cloud-computer/protected-before.json').read_text());final=json.loads((WORK/'cloud-computer/protected-final.json').read_text())
common=f'''# Pulse Bot 1.3.0 交付与验收

验收时间：{date}（北京时间）。

APK：[公网下载](http://216.23.82.196/proactive-agent/download/proactive-agent.apk)。Android 12 / API 31 及以上；包名 `dev.bubuki.proactive`，版本码 4，可覆盖同证书旧版。

## 文件与签名

| 项目 | 实测 |
|---|---|
| APK 字节数 | {apk.stat().st_size:,} |
| APK SHA-256 | `{digest}` |
| 签名证书 SHA-256 | `879d8a5eb7e55e054925d1030f350b3441276e46bef549214001b2bfc4672329` |
| 公网下载 | `curl -I` 为 `200 OK`；完整 GET 的长度与 SHA-256 均等于本地 APK |
| 源码 ZIP | {source['bytes']:,} 字节；`{source['sha256']}` |
| 原生组件对应源码 | {pub['gpl_source_bytes']:,} 字节；`{pub['gpl_source_sha256']}` |

本地 APK：`D:\\bubuki\\proactive-personal-agent\\dist\\proactive-agent.apk`。
本地源码：`D:\\bubuki\\proactive-personal-agent\\dist\\proactive-personal-agent-source.zip`。
[原生组件对应源码下载](http://216.23.82.196/proactive-agent/download/mika-corresponding-source.tar.gz) 的 HEAD 返回 200、长度完整，上传前后摘要一致。该源码包未做第二次完整公网下载，勿与 APK 完整 GET 验证混同。

## 已实现

- 首页可直接进入云电脑或此手机；对话也可选择对话、文件执行、云电脑或手机。助手、角色、工具、例行任务、Markdown 记忆、邮箱、节奏、外观和权限均保留二级入口。
- Mika Apache-2.0 原生应用与核心直接集成为两个源码模块，固定上游 `e9daf53d6834b236e713c6a62c3a095338eb7f95`，参考官方 v0.1.33。模型、手机连接、记忆/技能、浏览器、计划、设置均为实际原生页面。原有账号、工具及设置实现随源码保留，界面不是空链接。
- 模型仅在用户明确点击时从本机已有配置复制；本机凭据、任务草稿和外观均有实际存储路径。Mika 接受任务后才清除对应草稿。默认没有常驻悬浮胶囊。
- 云电脑复用 Browser Use 0.13.14：可查看真实桌面、接管、中文输入、导航、补充任务、交还、停止、下载文件；Agent 可使用浏览器和受限 Bash。资料库支持带摘要的分块上传，以及原生 Android 下载目录保存。无模型时可人工接管；AI 任务要先填写自己的执行模型。
- 原 Small Brain、Pi-style Harness、Markdown Vault、确认固化、例行任务、三级通知/邮件/悬浮卡与加密 WebSocket 保留。

## 部署边界

- 日本宿主机 `/opt/proactive-agent/`，原 `proactive-agent.service` 与 venv，主 API `127.0.0.1:18190`。
- 新云电脑专用账号 `proactive-desktop`、`/opt/proactive-agent/cloud-computer/desktop-venv`、`proactive-desktop.service`。Xvfb `:91`、新建 Chromium 与专用 profile；未复用现有浏览器资料。
- 私有控制 18193、专属网页代理 18194、CDP 都限回环。已有 nginx `/proactive-agent/` 负责公网 HTTP / WebSocket，本轮未修改 nginx、既有代理、路由、防火墙、Docker 网络或容器。
- Chromium 沙箱实际保留，并有 Landlock、独立临时目录、其他用户进程隐藏和资源限额。Bash 仅能访问任务工作区，实测不能读取这台新浏览器的 profile，也不能连接宿主 API。
- 受保护服务本轮部署前后快照逐项一致，共 {deploy['protected_services_checked']} 项；首次云电脑部署至最终快照一致性：{str(initial==final).lower()}。只重启了本项目自己的服务。
- Windows 未执行任何性能档位修改；最终回读 `quiet`。任务创建的安卓模拟器与本地预览服务器已停止，未关闭用户其他应用。

## 实测证据

1. 后端：29 项通过，只有依赖原有弃用提示。
2. 同一签名 APK 的固定 SHA-256 验收：页面/催单、动效、Mika、本机 APK 至真实公网电脑四项均通过。没有在验收期间改换 APK。
3. 页面回归包括 24 个页面与状态、记忆编辑回读、工具输出、角色保存、例行任务和原生三级提示确认。
4. Mika：助手签名交接为 uid 2000；创建独立 displayId、取得帧并实际注入点击；真实 Mika 模型循环连接免费隔离 HTTP 桩；791,321 字节经加密上传、原生下载后逐字节相等。
5. 日本机真实进程：坐标点击、中文及 emoji 输入、公开网页导航、768,034 字节浏览器下载回读摘要一致；Browser Use 真实循环调用免费模型桩并收到截图，暂停时等待、接受补充要求、交还后继续、停止后不再执行。
6. 云电脑上传 727,900 字节通过摘要校验；Agent 实际调用 Bash 处理该文件，并验证 profile 与 TCP 隔离。
7. Android 至公网：配对加密状态/截图/文件接口 200，未配对电脑接口 401；真实接管、768,034 字节原生 MediaStore 下载摘要一致；六个 Mika 原生二级页面实际打开并验证跟随所选深色。
8. 测试没有消费付费模型额度、发送真实 SMTP 邮件、操作用户真机或改写生产模型配置。

动效实测为独立位置数量，不等于 FPS：

| 动作 | 可辨识位置 |
|---|---:|
{motion_rows}

验收还检查手势取消/提交、面板拖动、后台中断、键盘、旧消息节点保留、工具详情和减弱动画。冷启动首个切页仍有停顿记录，不能宣称恒定 60fps。

## 查看实际运行结果

- `outputs/ui-1.3-preview.png`：当前界面预览。
- `outputs/ui-1.3.mp4`、`outputs/motion-1.3.mp4`、`outputs/mika-local-1.3.mp4`：安卓真实录屏。
- `outputs/screenshots-1.3/`：云端、手机原生及原有页面截图。
- `outputs/*acceptance.json`：对应原始验收摘要。

这些文件位于 `C:\\Users\\uiwcv\\Documents\\Codex\\2026-11-02\\ui-task-spec-for-gpt-md` 下。

## 当前边界与未完成目标

- **不宣称已经完成 Grok Bot 的全部逐像素复刻。** 原版 1.19.0 已下载、校验并安装，但在本模拟器的 `libpairipcore.so` 启动崩溃；没有绕过保护，也未能逐页观察登录后所有页面。前端依据官方公开移动端资料重建，Mika 则直接复用了源码。完整 Grok 逐页/动效对齐目标仍保留为未完成。
- 已证明真实引擎和工具路径，不能把免费模型桩的输出当作真实模型的任务质量验收。当前生产 Small / Big Brain 都尚未配置模型 Key；需在 App 填写后启动自己的 AI 工作。
- x86_64 模拟器验证了 Mika Java/Kotlin 助手、独立屏和聊天；ARM64 Linux 沙箱二进制与上游输入逐字节核对、对应源码齐备，尚未在物理 ARM64 手机上完整运行验证。OEM、OAuth 登录、邮箱实际投递需用户自己的设备和账号条件。
- 云电脑 AI 本轮是 Browser Use 浏览器与 Bash 引擎；人工控制覆盖这台新桌面。未声称任意原生 Linux GUI 应用都有现成自动化策略。
'''
token=(ROOT/'private/device-token').read_text('utf-8').strip()
private=common+'\n## 私人配对信息（请勿公开此报告）\n\nServer URL：`http://216.23.82.196/proactive-agent`\n\n设备配对码：\n\n```text\n'+token+'\n```\n\n安装后点左上头像 → 云端节点，填写配对码。模型、邮箱密钥请在 App 自行填写；公开 APK 没有内置这些秘密。\n'
(ROOT/'DELIVERY_REPORT.md').write_text(private,'utf-8');(OUT/'DELIVERY_REPORT.md').write_text(private,'utf-8');(OUT/'RELEASE_1_3.md').write_text(common,'utf-8')
sums=[]
for file in [ROOT/'dist/proactive-agent.apk',ROOT/'dist/proactive-personal-agent-source.zip',ROOT/'dist/mika-corresponding-source.tar.gz']:
    with file.open('rb') as f:value=hashlib.file_digest(f,'sha256').hexdigest()
    sums.append(value+'  '+file.name)
(ROOT/'dist/SHA256SUMS.txt').write_text('\n'.join(sums)+'\n');shutil.copy2(ROOT/'dist/SHA256SUMS.txt',OUT/'SHA256SUMS.txt')
print(json.dumps({'apk_sha256':digest,'report':str(ROOT/'DELIVERY_REPORT.md'),'preview':str(OUT/'ui-1.3-preview.png'),'pairing_code_printed':False,'goal_full_grok_clone_complete':False},indent=2))
