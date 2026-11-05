"""Publish the verified 1.2 animation milestone without calling the full goal done."""
from pathlib import Path
from datetime import datetime,timezone,timedelta
import json,hashlib,shutil

ROOT=Path(__file__).resolve().parents[1]
OUT=Path(r'C:\Users\uiwcv\Documents\Codex\2026-11-02\ui-task-spec-for-gpt-md\outputs')
work=ROOT/'work/motion-rebuild';evidence=work/'evidence'
download=json.loads((ROOT/'work/apk-download-verification.json').read_text())
digest=hashlib.file_digest((ROOT/'dist/proactive-agent.apk').open('rb'),'sha256').hexdigest()
assert digest==download['sha256']==download['download_sha256']
assert download['head_status']==200 and download['local_bytes']==download['download_bytes']
assert 'OK (1 test)' in (work/'acceptance-hardware.txt').read_text('utf-8')
assert 'OK (1 test)' in (work/'full-ui-final.txt').read_text('utf-8')
audit=json.loads((evidence/'files/motion-audit.json').read_text())
assert all(item.get('distinct_positions',10)>=10 for item in audit)
OUT.mkdir(exist_ok=True,parents=True)
for source,name in [
 (ROOT/'dist/proactive-agent.apk','proactive-agent.apk'),
 (evidence/'motion-acceptance.mp4','motion-1.2.mp4'),
 (evidence/'files/motion-audit.json','motion-1.2-audit.json'),
 (work/'acceptance-hardware.txt','motion-1.2-acceptance.txt'),
 (work/'full-ui-final.txt','android-1.2-full-ui.txt'),
 (ROOT/'work/apk-download-verification.json','apk-verification.json'),
 (work/'motion-hardware-contact.png','motion-1.2-preview.png'),
]:shutil.copy2(source,OUT/name)
screens=OUT/'screenshots';screens.mkdir(exist_ok=True)
for path in (ROOT/'work/android-v2-evidence/files').glob('v2-*.png'):shutil.copy2(path,screens/path.name)
walk=OUT/'UI_WALKTHROUGH.md'
if walk.exists():
    text=walk.read_text('utf-8').replace('# Pulse Bot 1.1 界面走查','# Pulse Bot 1.2 界面走查')
    text+='\n## 1.2 动态验收\n\n[模拟器真实触摸录屏](motion-1.2.mp4) · [动画位移与回归记录](motion-1.2-audit.json)。页面推入/返回、安卓预测返回的完成与取消、面板拖动关闭、键盘与草稿、旧消息不重复入场均通过；24 个页面/状态走查与原生催单确认回读也通过。此阶段 APK 尚不包含正在接入的 Mika 和云电脑。\n'
    walk.write_text(text,encoding='utf-8')
moment=datetime.now(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M 中国时间')
report=f'''# 1.2 动画返工阶段交付

更新时间：{moment}

- APK：[公网下载]({download['url']})，可覆盖安装，版本 1.2.0 / versionCode 3。
- 本地：`D:\\bubuki\\proactive-personal-agent\\dist\\proactive-agent.apk`。
- 文件大小：{download['local_bytes']:,} 字节；HEAD 200；完整下载大小与 SHA-256 回读一致。
- SHA-256：`{digest}`。
- [真实安卓录屏](motion-1.2.mp4) · [动画与手势验收](motion-1.2-acceptance.txt) · [二级页面完整回归](android-1.2-full-ui.txt)。

## 实际改动

页面包含顶栏整体推入/返回；安卓 14+ 的系统预测返回驱动同一画面，可取消；底部面板有进退和下滑关闭；消息增量更新保留原节点，不重播旧消息；键盘跟随系统 Insets 动画；原生第三级卡片进退；按压和开关反馈；每段对话草稿分开保存；应用内可关闭动态效果与触觉，尊重系统减弱动画。首屏使用已保存外观，避免明暗闪变。

## 验证边界

签名 Release、构建和 Android Lint 通过。硬件加速的独立 Android 15 模拟器实际操作：动态验收 24.428 秒通过，24 页面/状态及原生 WebSocket、催单确认数据回读 56.89 秒通过。录屏使用隔离测试节点，没有真实模型或 SMTP Key；不把这些测试当作真实手机或真实 VLM 的质量结论。软件渲染基线出现掉帧，已如实保留；本录屏来自硬件加速配置，不宣称固定 60 FPS。

Windows 安静档位保持不变；测试模拟器已关闭。此次仅更新 APK，未修改日本机既有服务或代理配置。

## 完整目标仍在进行

用户目标：复刻 Grok Bot 与 Mika 的手机本地能力，并提供可复用的云电脑 Harness，优化一个 App 内的使用流程。

Mika 0.1.33 原版已下载、验证官方发布 SHA-256、签名并实际启动。源码与原生运行时输入已取得并核对，正在集成。云电脑正在接入 Browser Use 0.13.14 与独立宿主桌面；当前公网 APK 尚未包括这两部分。Grok Bot 原版仍有模拟器启动崩溃限制，无法据此声称登录后全页像素级复核或整套 100% 完成。
'''
(OUT/'MOTION_UPDATE.md').write_text(report,encoding='utf-8')
token=(ROOT/'private/device-token').read_text().strip()
private=report+'\n## 私人设备配对\n\n默认节点：`http://216.23.82.196/proactive-agent`。在 App 的云端节点设置填入以下配对码：\n\n```text\n'+token+'\n```\n\n此报告含私人配对码，只保存在本地，不上传公开下载目录。当前云端 API 仍为已部署的 1.1 服务；本阶段动画 APK 与其兼容。\n'
for target in [ROOT/'DELIVERY_REPORT.md',OUT/'DELIVERY_REPORT.md']:target.write_text(private,encoding='utf-8')
(OUT/'SHA256SUMS.txt').write_text(digest+'  proactive-agent.apk\n',encoding='utf-8')
print(json.dumps({'milestone':'1.2.0','goal_complete':False,'apk_bytes':download['local_bytes'],'sha256':digest,'report':str(OUT/'MOTION_UPDATE.md')},ensure_ascii=False))
