"""Generate the own-app Root fusion handoff from completed immutable evidence."""
from pathlib import Path
import hashlib,json,shutil
from PIL import Image,ImageDraw,ImageFont
ROOT=Path(__file__).resolve().parents[1];WORK=ROOT/'work'
OUT=Path(r'C:\Users\uiwcv\Documents\Codex\2026-11-02\ui-task-spec-for-gpt-md\outputs')
def read(name):return json.loads((WORK/name).read_text('utf-8'))
def main():
    gate=read('final-release-gate.json');pub=read('release-1.7-publication.json');download=read('apk-download-verification.json')
    native=read('native-surface-acceptance/files/native-surface-audit.json');parity=read('native-launch-acceptance/files/native-launch-audit.json')
    root=read('root-acceptance/files/root-engine-proof.json');motion=read('native-presented-summary-1.7.json');source=read('source-delivery.json')
    apk=ROOT/'dist/proactive-agent.apk';digest=hashlib.sha256(apk.read_bytes()).hexdigest()
    assert gate['version']=='1.7.0' and digest==gate['apk_sha256']==pub['apk_sha256']==download['download_sha256']
    assert native['completed'] and parity['completed'] and pub['protected_services_unchanged'] and motion['apk_sha256']==digest
    assert root['actual_root_uid']==0 and root['tool_loop'] and root['stop_released_display']
    OUT.mkdir(exist_ok=True);screens=OUT/'screenshots-1.7';screens.mkdir(exist_ok=True)
    for folder,prefix in [('native-surface-acceptance','native-'),('native-launch-acceptance','launch-'),('root-acceptance','root-'),('public-computer-acceptance','public-')]:
        for path in (WORK/folder/'files').glob(prefix+'*.png'):shutil.copy2(path,screens/path.name)
        if (WORK/folder/'motion-acceptance.mp4').exists():shutil.copy2(WORK/folder/'motion-acceptance.mp4',OUT/(folder+'-1.7.mp4'))
    for path,name in [('final-release-gate.json','release-1.7-acceptance.json'),('release-1.7-publication.json','release-1.7-publication.json'),('native-presented-summary-1.7.json','native-motion-1.7.json'),('root-acceptance/files/root-engine-proof.json','root-engine-proof-1.7.json')]:shutil.copy2(WORK/path,OUT/name)
    shutil.copy2(apk,OUT/'proactive-agent.apk');shutil.copy2(ROOT/'dist/proactive-personal-agent-source.zip',OUT/'proactive-personal-agent-source.zip')
    font=ImageFont.truetype(r'C:\Windows\Fonts\msyh.ttc',18);canvas=Image.new('RGB',(1376,792),'#11131A');draw=ImageDraw.Draw(canvas)
    for i,(name,label) in enumerate([('native-08-home-dark.png','自家原生首页'),('native-09-restart-chat-dark.png','深色对话与成果'),('root-03-own-workspace.png','Root 独立手机工作区'),('public-02-cloud-computer.png','日本节点云电脑')]):
        picture=Image.open(screens/name).convert('RGB');picture.thumbnail((324,702));x=12+i*344;canvas.paste(picture,(x,48));draw.text((x+4,12),label,font=font,fill='#ededf3')
    canvas.save(OUT/'ui-1.7-preview.png')
    rows='\n'.join(f"| {c['check']} | 通过 | {c['seconds']} s |" for c in gate['checks'])
    report=f'''# Pulse Bot 1.7.0 融合版交付

[下载已签名 APK](http://216.23.82.196/proactive-agent/download/proactive-agent.apk)。版本 1.7.0 / code 8，同一签名，可覆盖旧版。

## 本次实现

按用户最新纠正，参考第三方的工具和交互，融合为自己的产品。Grok Bot 是视觉与交互参考；Mika 是底层工具和通用 Markdown 代码的参考。完整 Mika 应用、重复模型设置、终端、PRoot/Alpine 运行时均未打进 APK；选用代码保留 Apache-2.0 许可和归属。

自己的 Compose 首页、对话、成果卡和工具记录成为默认入口；二级设置、透明记忆库、机器人配置、资料库与云电脑保留自己的导航和外观。支持实际附件选择/上传、草稿恢复、离线名册、Markdown 记忆链接编辑、通知回到所属对话与减弱动画。

手机任务直接留在本 App 的对话，共用执行模型配置、结果和取消。Root 独立工作区支持应用启动、截图、点击、滑动、中文输入、按键、接管继续、停止与释放。输入校验本次 displayId 和画面方向，拒绝 display 0 及搬动其他屏幕的已有任务。未授权 Root 时明确提示并保留草稿，没有 Shizuku/无障碍授权回退。剪贴板子进程由 Root 启动后降低权限到系统 shell 包的真实 uid，解决 Android 包名/uid 验证；坐标注入仍由 uid 0 父进程执行。

## 验证结果

同一签名 APK：{apk.stat().st_size:,} 字节，SHA-256 `{digest}`。

| 流程 | 结果 | 运行时间 |
|---|---|---:|
{rows}

Root 验收实际启动 uid 0 app_process，经身份与令牌校验的 LocalSocket 建立独立 display {root['display_id']}；完成截图、点击、滑动、中文粘贴及按键，并回读测试 Activity 变化。原剪贴板恢复、主屏焦点保持、错误屏号/越界/旧方向拒绝、停止后释放、接管继续和加密任务历史通过。测试模型收到实际 PNG，工具由本 App 的循环调用，未伪造执行结果。

SDK 模拟器的 su 0 由 shell 授权；测试仅在 instrumentation 内把这个真实 Root 会话接入生产工具链。这证明 Root 工具和 IPC 的实际运行，未把它当成真实手机 Magisk 授权流程成功，也没有给发布包添加测试授权入口。

构建与 lintRelease、v2 签名验证通过。公网 curl -I 为 200 OK；Content-Length、本地文件和公网完整下载均为上述字节数，完整 SHA-256 相同。源码 ZIP 也完整下载回读相符，签名密钥、私钥、配对 token 和用户数据已排除。

## 画面与动效

`outputs/ui-1.7-preview.png` 是实际截图拼版；原图在 `outputs/screenshots-1.7/`。原生首页/对话、二级页面和 Root 流程均保留真实操作录屏。

{motion['observation']}

## 范围与兼容性

独立焦点工作区需要 Root 和 Android 14+；更低系统版本可用云端功能。此次在自建 Android 35 模拟器验收，没有操作真实手机。OEM 虚拟屏限制、Magisk 首次授权、ARM64 真机兼容性和熄屏长时运行尚未实机验收。模型回环使用本地确定性接口，证明图像/工具链路，不代表商业 VLM 的任务成功率；没有付费模型或真实 SMTP 调用。Root 不构成隐身或免风控保证。

Grok Bot 原版曾在本模拟器 libpairipcore.so 崩溃，未声称完成登录后的逐屏对照。按用户最新纠正，当前验收范围是自家产品融合，旧的完整商业功能复刻清单不再是交付要求。

## 云端与交付

云电脑继续使用本项目 Browser Use 0.13.14、Playwright 与独立虚拟桌面。新版 APK 实际打开日本节点画面、接管和下载文件并验证字节；未配置生产模型时会提示配置。此轮发布本项目 APK、源码 ZIP 和版本元数据，后端仍为 {pub['health']['version']}，没有部署或重启 Python 后端。为修复源码 ZIP 原本缺失的公网路由，在本项目独立 nginx 片段末尾增加了一个精确静态文件地址，并通过 nginx -t 后平滑重载；原 API/WebSocket 代理规则字节未变。没有修改 systemd 单元、Docker、Claude、BB、既有浏览器、网络或其他业务代理。发布前后 {pub['protected_services_checked']} 项受保护服务状态、PID 和启动时间一致。Windows 安静模式只读回查为 quiet，未更改性能档位。

[Android 与服务端源码](http://216.23.82.196/proactive-agent/download/proactive-personal-agent-source.zip)：{source['bytes']:,} 字节，SHA-256 `{source['sha256']}`。Mika 参考锁定 e9daf53d6834b236e713c6a62c3a095338eb7f95；许可见 THIRD_PARTY_NOTICES.md 和应用内 mika-LICENSE.txt。历史 1.6 对应的原生源码下载仍保留，1.7 不含这些二进制。
'''
    path=ROOT/'DELIVERY_REPORT_1_7.md';path.write_text(report,'utf-8');shutil.copy2(path,OUT/path.name)
    current=ROOT/'DELIVERY_REPORT.md';old=current.read_text('utf-8');marker='<!-- current-client-1.7 -->'
    if marker not in old:current.write_text(marker+'\n最新客户端：**1.7.0 融合版**。验收见 [DELIVERY_REPORT_1_7.md](DELIVERY_REPORT_1_7.md)，[APK 下载](http://216.23.82.196/proactive-agent/download/proactive-agent.apk)。以下保留历史部署与本地配对信息。\n\n'+old,'utf-8')
    print(json.dumps({'report':str(path),'preview':str(OUT/'ui-1.7-preview.png'),'sha256':digest},ensure_ascii=False))
if __name__=='__main__':main()
