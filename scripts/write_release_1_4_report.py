"""Write a private handoff and a credential-free report from the accepted release."""
from pathlib import Path
import datetime,hashlib,json,shutil,zipfile
from PIL import Image,ImageDraw,ImageFont

ROOT=Path(__file__).resolve().parents[1];WORK=ROOT/'work'
OUT=Path(r'C:\Users\uiwcv\Documents\Codex\2026-11-02\ui-task-spec-for-gpt-md\outputs')
OUT.mkdir(exist_ok=True)
def read(name):return json.loads((WORK/name).read_text('utf-8'))
gate=read('final-release-gate.json');pub=read('release-1.4-publication.json');download=read('apk-download-verification.json')
deploy=read('redesign-deployment.json');public=read('cloud-computer/public-api-1.4.json');source=read('source-delivery.json')
bots=read('bot-management-acceptance/files/bot-management-audit.json')
perf=read('startup-frame-summary-1.4.json')
mika=read('mika-acceptance/files/mika-acceptance.json');live=read('public-computer-acceptance/files/public-computer-acceptance.json')
apk=ROOT/'dist/proactive-agent.apk';digest=hashlib.sha256(apk.read_bytes()).hexdigest()
assert gate['version']=='1.4.0' and digest==gate['apk_sha256']==pub['apk_sha256']==download['sha256']==download['download_sha256']
assert len(gate['checks'])==5 and all(c['passed'] for c in gate['checks']) and bots['passed']
assert '30 passed' in (WORK/'server-1.4-final-tests.txt').read_text('utf-8')
assert deploy['protected_services_unchanged'] and deploy['health']['version']=='1.4.0'
assert public['health']['version']=='1.4.0' and public['encrypted_endpoints']['/api/bots']['placement_metadata_present']
assert mika['coordinate_tap'] and mika['real_local_llm_loop'] and live['native_media_store_download_bytes']==768034
assert '879d8a5eb7e55e054925d1030f350b3441276e46bef549214001b2bfc4672329' in (WORK/'apk-1.4-signature.txt').read_text('utf-8')
assert (WORK/'quiet-mode-1.4.txt').read_text('utf-8-sig').strip()=='quiet'
secrets=[(ROOT/'private/device-token').read_bytes().strip(),(ROOT/'private/keystore-password').read_bytes().strip()]
with zipfile.ZipFile(apk) as archive:
    for entry in archive.infolist():
        if any(secret in archive.read(entry) for secret in secrets):raise RuntimeError('Private credential in APK')

screens=OUT/'screenshots-1.4';screens.mkdir(exist_ok=True)
for folder,prefixes in [(WORK/'bot-management-acceptance/files',('bots-',)),(WORK/'android-v2-evidence/files',('v2-',)),(WORK/'mika-acceptance/files',('mika-',)),(WORK/'public-computer-acceptance/files',('public-',))]:
    for file in folder.glob('*.png'):
        if file.name.startswith(prefixes):shutil.copy2(file,screens/file.name)
for old,name in [('bot-management-acceptance/motion-acceptance.mp4','bots-1.4.mp4'),('motion-rebuild/evidence/motion-acceptance.mp4','motion-1.4.mp4'),('android-v2-evidence/motion-acceptance.mp4','ui-1.4.mp4'),('mika-acceptance/motion-acceptance.mp4','mika-local-1.4.mp4')]:shutil.copy2(WORK/old,OUT/name)
for old,name in [('final-release-gate.json','release-1.4-acceptance.json'),('bot-management-acceptance/files/bot-management-audit.json','bot-management-1.4-audit.json'),('motion-rebuild/evidence/files/motion-audit.json','motion-1.4-audit.json'),('cloud-computer/public-api-1.4.json','cloud-public-1.4.json')]:shutil.copy2(WORK/old,OUT/name)
for old,name in [('mika-acceptance/files/mika-acceptance.json','mika-1.4-acceptance.json'),('public-computer-acceptance/files/public-computer-acceptance.json','public-1.4-acceptance.json')]:shutil.copy2(WORK/old,OUT/name)
shutil.copy2(WORK/'startup-frame-summary-1.4.json',OUT/'startup-frame-summary-1.4.json')
shutil.copy2(WORK/'startup-profile-no-record/files/bot-management-audit.json',OUT/'startup-no-record-1.4-audit.json')
shutil.copy2(apk,OUT/'proactive-agent.apk');shutil.copy2(ROOT/'dist/proactive-personal-agent-source.zip',OUT/'proactive-personal-agent-source.zip')
font=ImageFont.truetype(r'C:\Windows\Fonts\msyh.ttc',18)
canvas=Image.new('RGB',(1032,792),'#161820');draw=ImageDraw.Draw(canvas)
for i,(name,label) in enumerate([('bots-pinned-home.png','首页置顶'),('bots-hidden-list.png','已隐藏助手'),('public-02-cloud-computer.png','日本节点云电脑')]):
    im=Image.open(screens/name).convert('RGB');im.thumbnail((324,702));x=12+i*344;canvas.paste(im,(x,48));draw.text((x+5,13),label,font=font,fill='#ededf3')
canvas.save(OUT/'ui-1.4-preview.png')
date=datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).isoformat(timespec='seconds')
checks='\n'.join(f"| {c['check']} | 通过 | {c['seconds']:.2f} s |" for c in gate['checks'])
common=f'''# Pulse Bot 1.4.0 交付与验收

验收时间：{date}

下载：<http://216.23.82.196/proactive-agent/download/proactive-agent.apk>

## 本次交付

- 首页长按助手、对话菜单中的置顶/取消置顶、隐藏/显示；独立「已隐藏助手」二级页面和恢复按钮。所有助手隐藏时提供恢复入口，设置中也可进入。
- 这些状态通过加密 API 保存到云端，重启后保留。隐藏不删除对话、记忆和任务，不暂停例行任务，也不关闭通知；每个会话的草稿继续保留。
- 修复首帧外观注入；页面加载前写入选定主题，WebView 内容准备完成后再显示。
- 原有 380/330 ms 切页过渡与手势保留。首次冷切页仍有绘制停顿；预绘制实验没有改善实际录屏，已撤回，不计作性能优化成果。未提高电脑性能模式。
- Mika 本机应用、虚拟屏/点击、模型配置、云电脑、资料库和透明 Markdown 记忆库均保留。

## APK 与公网回读

| 项目 | 实测 |
| --- | --- |
| 版本 | 1.4.0 / versionCode 5 |
| 字节数 | {apk.stat().st_size:,} |
| SHA-256 | `{digest}` |
| 签名证书 SHA-256 | `879d8a5eb7e55e054925d1030f350b3441276e46bef549214001b2bfc4672329` |
| curl HEAD | 200 OK |
| 完整公网 GET | {download['download_bytes']:,} 字节，SHA-256 与本地 APK 一致 |

## 验收

后端：30 项通过。以下五个安卓流程使用同一签名 Release APK。独立屏检查补足布局/首帧就绪前提后通过，其余已通过项直接沿用；另外运行一次不录屏的性能对照。

| 流程 | 结果 | 用时 |
| --- | --- | --- |
{checks}

- 名册：真实长按不误入对话、置顶排序、隐藏全部与空状态、恢复、设置入口、草稿保留及 Activity 重建后云端回读。
- UI：对话、工具执行记录、角色权限、笔记编辑回读、例行任务、WebSocket 通知与三级催单回执。
- Motion：正向/返回、快速打断、边缘返回成功/取消、面板拖动与重开、键盘、旧消息节点保留、减弱动画。
- Mika：签名助手以 uid 2000 交接，独立 VirtualDisplay、截图与实际坐标点击；真实本地模型循环接免费隔离 HTTP 桩；791,321 字节加密上传与原生下载相等。
- 公网：APK 直接连接已部署日本节点，查看云电脑、接管、原生下载 768,034 字节并核对哈希；六个 Mika 原生二级页面跟随门户选择的深色主题。
- 此次验收没有付费模型调用、真实 SMTP 发送或真实手机操作。模拟器录屏和 Window FrameMetrics 不等同于实体手机稳定 60 fps 证明。
- 无录屏对照：首次切页 Window 总帧时峰值 {perf['without_recording']['first-push']['max_total_ms']} ms，热切页峰值 {perf['without_recording']['warm-push']['max_total_ms']} ms，二者 JS Long Task 均为 0。录屏组首次峰值 {perf['recorded']['first-push']['max_total_ms']} ms。冷切页的长帧仍然存在，不能把它归因于录屏，也不能把首帧主题修复当作流畅度完成。

## 云端 Harness

日本节点 `216.23.82.196`，宿主机 `/opt/proactive-agent/`，独立 Python venv 与 systemd；主 API 仅监听 `127.0.0.1:18190`，既有 nginx 子路径提供 HTTP/WebSocket。

云电脑复用 Browser Use 0.13.14 与 Playwright 1.56，独立用户、Xvfb :91、Openbox、Chromium profile 和进程；私有控制 18193、网页代理 18194、CDP 均为回环。App 可看画面、暂停/接管、手动输入点击、交还控制、停止任务及上传/下载文件。原有 Pi 工具循环可读写文件、编辑、运行 Bash、抓取和搜索网页。

云电脑 AI 模型与 Bash 的实际调度、中文输入、浏览器下载及中途接管已在 1.3 的日本节点真实进程上使用免费模型桩验证；1.4 未改动该桌面实现。此次再次通过 APK 公网检查。当前模型就绪状态：small_ready={public['encrypted_endpoints']['/api/status']['small_ready']}，big_ready={public['encrypted_endpoints']['/api/status']['big_ready']}；需要填入用户自己的兼容 API Key/Model 才能进行真实 AI 任务。

主服务更新前没有活动 Harness 或云电脑任务。更新后 {deploy['protected_services_checked']} 项受保护服务状态与进程记录一致。未修改 nginx、既有 Claude/BB/浏览器/Docker 服务、配置或网络；没有 Docker 部署。Windows 回读 `quiet`。

## 源码与参照

`dist/proactive-personal-agent-source.zip`：{source['bytes']:,} 字节，SHA-256 `{source['sha256']}`。源码包排除设备配对码、签名密钥、私钥、用户数据和构建缓存。Mika 上游固定 `e9daf53d6834b236e713c6a62c3a095338eb7f95`，Apache-2.0；原生第三方许可证及对应源码随包保留。

原生对应源码：<http://216.23.82.196/proactive-agent/download/mika-corresponding-source.tar.gz>，{pub['gpl_source_bytes']:,} 字节，SHA-256 `{pub['gpl_source_sha256']}`。

名册交互参照：[Create and manage Bots](https://docs.x.ai/grok-bot/bots)；移动端参照：[Grok Bot for Mobile](https://docs.x.ai/grok-bot/mobile)；消息参照：[Message and collaborate](https://docs.x.ai/grok-bot/chat-and-collaboration)。这些公开文档与商店截图是当前 Grok 参照；原版 1.19 在本机 ARM 转译模拟器的 libpairipcore 崩溃，未进入登录后界面。

## 尚未完成的完整复刻

目标仍在进行。主助手替换、身份 Title/图片头像、删除/重置、群组/线程、完整技能和例行任务管理、连接器、语音仍有缺口；视觉与动画没有逐屏达到原版一致。Mika 原生 ARM Linux 运行时没有在 ARM64 实体手机上验收；当前 uid 2000、VirtualDisplay 和点击为模拟器实测，Root/Shizuku 真机路径不作已验收声明。

证据在 `outputs/screenshots-1.4/`、`bots-1.4.mp4`、`motion-1.4.mp4`、`ui-1.4.mp4`、`mika-local-1.4.mp4`、`release-1.4-acceptance.json`。`MOTION_AND_PARITY_PLAN.md` 记录未完事项。
'''
assert not any(value.decode() in common for value in secrets)
private=common+'\n## 私人设备配对\n\n此段仅供本机用户查看，不在公开报告或源码包中。\n\n设备配对码：`'+secrets[0].decode()+'`\n\nApp → 设置 → 云端节点；默认 URL 为 `http://216.23.82.196/proactive-agent`。填入配对码，再配置模型。手机本地模型可在「此手机」中明确复制已经保存的云端模型配置；也可单独配置。\n'
for path in [ROOT/'DELIVERY_REPORT.md',OUT/'DELIVERY_REPORT.md']:
    if path.exists() and '1.3.0 交付与验收' in path.read_text('utf-8'):shutil.copy2(path,path.with_name('DELIVERY_REPORT_1_3.md'))
    path.write_text(private,'utf-8')
(OUT/'RELEASE_1_4.md').write_text(common,'utf-8')
items=['proactive-agent.apk','proactive-personal-agent-source.zip','ui-1.4-preview.png','bots-1.4.mp4','motion-1.4.mp4','ui-1.4.mp4','mika-local-1.4.mp4']
manifest=''.join(hashlib.sha256((OUT/name).read_bytes()).hexdigest()+'  '+name+'\n' for name in items)
(OUT/'SHA256SUMS_1_4.txt').write_text(manifest,'utf-8')
(OUT/'SHA256SUMS.txt').write_text(manifest,'utf-8')
print(json.dumps({'apk_sha256':digest,'report':str(ROOT/'DELIVERY_REPORT.md'),'safe_report':str(OUT/'RELEASE_1_4.md'),'preview':str(OUT/'ui-1.4-preview.png'),'pairing_code_printed':False,'full_grok_clone_complete':False},indent=2))
