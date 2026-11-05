"""Publish local delivery evidence only after the final checks and public readback pass."""
from pathlib import Path
import hashlib,json,shutil,zipfile,datetime
from PIL import Image,ImageDraw,ImageFont
ROOT=Path(__file__).resolve().parents[1]
OUT=Path(r'C:\Users\uiwcv\Documents\Codex\2026-11-02\ui-task-spec-for-gpt-md\outputs')
work=ROOT/'work'
apk=ROOT/'dist/proactive-agent.apk'
digest=hashlib.sha256(apk.read_bytes()).hexdigest()
download=json.loads((work/'apk-download-verification.json').read_text())
deployment=json.loads((work/'redesign-deployment.json').read_text())
public_api=json.loads((work/'redesign-public-api.json').read_text())
acceptance=(ROOT/'work-android-redesign-acceptance.log').read_text('utf-8')
assert 'OK (1 test)' in acceptance and '"overlay_only": false' in acceptance
assert '19 passed' in (ROOT/'work-server-redesign-tests.log').read_text('utf-8')
assert deployment['health']['version']=='1.1.0' and deployment['protected_services_unchanged']
assert public_api['health']['version']=='1.1.0' and all(v['status']==200 for v in public_api['encrypted_endpoints'].values())
assert 'Verifies' in (work/'apk-v2-signature.txt').read_text('utf-8')
assert download['sha256']==download['download_sha256']==digest and download['head_status']==200
images=work/'android-v2-evidence/files'
names=[
 ('v2-01-bots-light','助手列表'),('v2-02-chat-empty','对话空白页'),('v2-03-keyboard','键盘与输入框'),('v2-04-chat','对话与引用'),
 ('v2-05-bot-profile','助手详情'),('v2-06-bot-settings','角色与工具设置'),('v2-07-phone','手机与虚拟屏'),('v2-08-memory-list','记忆文件列表'),
 ('v2-09-memory-read','Markdown 阅读'),('v2-10-memory-edit','编辑与保存'),('v2-11-settings','设置'),('v2-12-small-model','小脑配置'),
 ('v2-13-execution-model','执行模型配置'),('v2-14-email','邮箱配置'),('v2-15-tools','工具目录'),('v2-16-tool-detail','工具详情'),
 ('v2-17-result','执行成果'),('v2-18-tool-trace','参数与执行输出'),('v2-19-routines','例行任务列表'),('v2-20-routine-detail','例行任务详情'),
 ('v2-21-create-bot','创建助手'),('v2-22-bots-dark','深色助手列表'),('v2-23-chat-dark','深色对话'),('v2-24-stage3','原生第三级提示卡')]
assert all((images/(name+'.png')).exists() for name,_ in names)
OUT.mkdir(exist_ok=True,parents=True);screens=OUT/'screenshots';screens.mkdir(exist_ok=True)
for name,_ in names:shutil.copy2(images/(name+'.png'),screens/(name+'.png'))
font_path=Path(r'C:\Windows\Fonts\msyh.ttc')
font=ImageFont.truetype(str(font_path),18)
def contact(selected,path,columns=3,width=360):
    height=round(width*2340/1080);cell_h=height+50;rows=(len(selected)+columns-1)//columns
    canvas=Image.new('RGB',(columns*(width+20)+20,rows*(cell_h+20)+20),'#e6e7eb');draw=ImageDraw.Draw(canvas)
    for i,(name,title) in enumerate(selected):
        x=20+(i%columns)*(width+20);y=20+(i//columns)*(cell_h+20)
        image=Image.open(images/(name+'.png')).convert('RGB');image.thumbnail((width,height))
        canvas.paste(image,(x,y));draw.text((x+4,y+height+13),title,font=font,fill='#24252c')
    canvas.save(path)
contact([names[i] for i in (0,3,20,4,14,15)],OUT/'ui-v2-preview.png')
contact([names[i] for i in (5,8,9,10,11,12,13,17,18,19,21,23)],OUT/'ui-v2-secondary-preview.png',columns=4,width=290)
walk='# Pulse Bot 1.1 界面走查\n\n以下图片均来自签名 Release APK 在独立 Android 15 模拟器中的运行截图。测试使用隔离 API 数据，未连接真实模型或 SMTP。\n\n'
walk+='| 页面 / 状态 | 截图 |\n|---|---|\n'+''.join(f'| {title} | [查看](screenshots/{name}.png) |\n' for name,title in names)
walk+='\n## 实际触摸与数据验证\n\n触摸助手、编辑角色、编辑 Markdown 并保存回读、进入每个配置页、展开工具参数与输出、确认成果形成例行任务、暂停任务、创建助手、切换主题。原生 WebSocket 依次触发通知、SMTP 未配置状态和第三级提示卡；实际点击确认后，服务端 pending 条目消失。\n'
(OUT/'UI_WALKTHROUGH.md').write_text(walk,encoding='utf-8')
reference='''# 参考来源与实际观察边界

## Grok Bot

- [官方移动端说明](https://docs.x.ai/grok-bot/mobile)
- [官方 Android 商店页面与截图](https://play.google.com/store/apps/details?id=ai.x.grok.bot)
- [官方对话与工具活动说明](https://docs.x.ai/grok-bot/chat-and-collaboration)
- [官方例行任务说明](https://docs.x.ai/grok-bot/skills-routines-and-automations)

原包 `ai.x.grok.bot` 已下载、检查签名并安装到独立模拟器。实际下载包内版本为 1.19.0 / 3023；签名证书 SHA-256 为 `f68872ec9f95894ecea37bf8d096d3609abef72765d9350cf10f1c3231863b74`，与 APKMirror 所列签名一致。启动时原生库 `libpairipcore.so` 报 SIGSEGV，未能进入登录后的界面；没有修改或移除该库。不能声称完整原版已经逐页运行。

直接按公开截图对齐了列表顶栏、圆形返回按钮、助手胶囊、气泡、独立加号与胶囊输入框、创建助手面板、头像形状和颜色选择、工具目录的列表结构。

## 开源实现

- [OpenBot](https://github.com/regnull/openbot)，MIT，研究版本 `0bda7e31fcfb25e22446d6225b08325b7753157e`。已下载源码，研究工具执行状态、展开参数与输出、Markdown 清理和例行任务。采用其 marked + DOMPurify 渲染模式，保留许可证。
- [Rakazo](https://github.com/elie222/rakazo)，Apache-2.0，研究版本 `91aeb75a885afe48900068eb6288065fb3d13dc6`。研究其移动端助手详情、角色设置、模型连接、文件和例行任务的独立路由。未将其后端或运行时混入服务器。
- [RikkaHub 3.1.1](https://github.com/rikkahub/rikkahub/releases/tag/3.1.1)，AGPL-3.0。APK 已下载并在同一独立模拟器实际启动；操作聊天输入区、加号菜单、扩展面板、导航、助手列表和助手设置。没有复制其源代码。

## 未宣称的功能等价

此版本交付原任务要求的文件、命令、联网、记忆、例行任务、催单及手机虚拟屏工具。Grok Bot 的第三方 SaaS 连接器、订阅账户、共享云桌面、群组与实时语音没有被实现成完整等价产品，不会用未接通的按钮冒充连接成功。

二级页面依据可访问的官方资料与开源实现重做；无法观察的原版页面不能称为像素级逐页验证。用户要求的整套 Grok Bot 100% 功能复制仍未达到。此报告明确区分界面重做、实际运行验证和缺口。
'''
(OUT/'REFERENCE_STUDY.md').write_text(reference,encoding='utf-8')
shutil.copy2(apk,OUT/'proactive-agent.apk')
(OUT/'SHA256SUMS.txt').write_text(digest+'  proactive-agent.apk\n',encoding='utf-8')
for src,dst in [(ROOT/'work-server-redesign-tests.log','server-tests.txt'),(ROOT/'work-android-redesign-build.log','android-build.txt'),(work/'android-v2-instrumentation.txt','android-ui-acceptance.txt'),(work/'redesign-deployment.json','deployment.json'),(work/'apk-download-verification.json','apk-verification.json')]:shutil.copy2(src,OUT/dst)
for src,dst in [(work/'redesign-public-api.json','public-api-verification.json'),(work/'apk-v2-signature.txt','apk-signature.txt'),(ROOT/'work-android-redesign-final-checks.log','android-final-checks.txt'),(work/'apk-v2-head.txt','apk-head.txt')]:shutil.copy2(src,OUT/dst)
token=(ROOT/'private/device-token').read_text().strip()
now=datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).isoformat(timespec='seconds')
report=f'''# Pulse Bot 1.1 交付报告

更新：{now}

## 下载

- [Android APK](http://216.23.82.196/proactive-agent/download/proactive-agent.apk)
- [日本节点](http://216.23.82.196/proactive-agent/)
- 本地：`D:\\bubuki\\proactive-personal-agent\\dist\\proactive-agent.apk`
- 版本 1.1.0 / versionCode 2，Android 12+；与上一版使用同一签名，可覆盖安装。
- 文件大小：{apk.stat().st_size:,} 字节。
- SHA-256：`{digest}`
- 签名证书 SHA-256：`879d8a5eb7e55e054925d1030f350b3441276e46bef549214001b2bfc4672329`

## 私人设备配对码

本段仅在本机私人报告中保存，不进入 APK、源码包或公网页面。

`{token}`

首次打开：左上头像 → 云端节点 → 复制配对码 → 保存并同步。分别进入常驻小脑、执行大脑和邮件配置。原有用户安装升级后保留本机配置。

## 本次重做

助手列表、搜索、新建助手、头像形状和颜色、对话、助手详情、角色设定、工具开关与通知、模型选择、工具目录与详情、参数与输出、停止执行、例行任务列表与详情、Markdown 文件预览与编辑、独立模型/节点/邮件配置页、手机与虚拟屏、浅色/深色/跟随系统及原生第三级提示卡。

云端新增持久化的助手身份和独立对话历史、逐助手工具权限、通知开关、执行进度、停止执行及例行任务创建、启停和立即运行。原有 Markdown 记忆与配置保留。

## 验收

- 服务端：19 项通过，涵盖原链路、身份/历史隔离、工具权限、通知关闭、模式切换，以及加密 HTTP 请求中的停止执行。
- Android Release 构建与 Lint 通过，签名可验证。
- 签名 APK 在独立 `emulator-5560` 上完成一次干净的原生触摸走查；24 张截图见 [完整界面走查](UI_WALKTHROUGH.md)。包括键盘、保存回读、工具明细、例行任务与原生提示卡点击回执。
- 界面验收使用本机隔离的加密 API fixture，未写入生产会话与记忆，未调用付费模型或 SMTP。
- 公网健康版本：`{deployment['health']['version']}`；APK HEAD 200，完整 GET 大小与 SHA-256 一致。
- 本次部署只更新 `/opt/proactive-agent/server/` 内代码并重启 `proactive-agent.service`。未改 nginx、systemd 单元、依赖、网络、代理或防火墙。受保护服务 {deployment['protected_services_checked']} 项状态/PID 对比一致。
- 应用代码回退备份：`{deployment['backup']}`。数据不在自动回退过程中被覆盖。

## 参考与未完成部分

Grok Bot 原包已经下载并实际尝试启动，但在 `libpairipcore.so` 崩溃，不能声称登录后的原版逐页实测完成。界面对齐依据官方公开截图/文档，并研究 OpenBot、Rakazo 和实际操作的 RikkaHub。详见 [参考与边界](REFERENCE_STUDY.md)。

用户要求的整套 Grok Bot 100% 照搬仍未达到：第三方 SaaS 连接器、共享云桌面、群组和实时语音没有完整等价实现。交付的是原项目范围内的 UI 与工具交互重做。

真实外部模型质量、VLM 坐标质量和 SMTP 投递需要用户自己的配置；本次不声称已测通过。Root/Shizuku 真机授权不在本次验收中；此前 shell UID 独立屏创建、截图与点击证据保留，特权引擎没有替换为模拟按钮。

## 电脑状态

本次没有修改 Windows 性能/电源档位。工作期间回读为 `quiet`，现有安静守护为 Running；构建单 worker，模拟器单核、低优先级。只操作独立模拟器，没有操作真实手机。
'''
(ROOT/'DELIVERY_REPORT.md').write_text(report,encoding='utf-8');(OUT/'DELIVERY_REPORT.md').write_text(report,encoding='utf-8')
source=OUT/'proactive-personal-agent-source.zip'
with zipfile.ZipFile(source.with_suffix('.tmp'),'w',zipfile.ZIP_DEFLATED) as archive:
    for name in ['README.md','TASK_SPEC_FOR_GPT.md','THIRD_PARTY_NOTICES.md','.gitignore']:archive.write(ROOT/name,name)
    for directory in ['server','android','scripts']:
        for file in (ROOT/directory).rglob('*'):
            if not file.is_file():continue
            rel=file.relative_to(ROOT)
            if any(part in {'.venv','data','memory_vault','__pycache__','.pytest_cache','build','.gradle'} for part in rel.parts):continue
            if file.name=='local.properties':continue
            archive.write(file,str(rel).replace('\\','/'))
source.with_suffix('.tmp').replace(source)
with zipfile.ZipFile(source) as archive:
    assert not any('private/' in name or name=='DELIVERY_REPORT.md' or '/data/' in name for name in archive.namelist())
(OUT/'README.md').write_text('# Pulse Bot 1.1\n\n[APK 下载](http://216.23.82.196/proactive-agent/download/proactive-agent.apk) · [私人交付报告](DELIVERY_REPORT.md) · [24 张界面截图](UI_WALKTHROUGH.md) · [参考与未完成部分](REFERENCE_STUDY.md)\n\n`proactive-personal-agent-source.zip` 不含密钥、配对码、生产数据和签名私钥。\n',encoding='utf-8')
print(json.dumps({'outputs':str(OUT),'apk_bytes':apk.stat().st_size,'sha256':digest,'screenshots':len(names),'source_bytes':source.stat().st_size},indent=2))
