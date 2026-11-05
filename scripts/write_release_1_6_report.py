"""Write the 1.6 delivery only from the signed APK, completed acceptance and public readback."""
from pathlib import Path
import datetime,hashlib,json,shutil,zipfile
from PIL import Image,ImageDraw,ImageFont
ROOT=Path(__file__).resolve().parents[1];WORK=ROOT/'work'
OUT=Path(r'C:\Users\uiwcv\Documents\Codex\2026-11-02\ui-task-spec-for-gpt-md\outputs')
def read(name):return json.loads((WORK/name).read_text('utf-8'))
def main():
    gate=read('final-release-gate.json');pub=read('release-1.6-publication.json');download=read('apk-download-verification.json')
    deploy=read('deployment-1.6.json');source=read('source-delivery.json')
    identity=read('identity-acceptance/files/identity-acceptance.json')
    frames=read('bot-management-acceptance/files/bot-management-audit.json')['android_window_frames']
    observed={phase:{'samples':len([f for f in frames if f['phase']==phase]),'max_ms':round(max(f['total_ms'] for f in frames if f['phase']==phase),2)} for phase in ['first-push','warm-push']}
    local=read('mika-acceptance/files/mika-acceptance.json');live=read('public-computer-acceptance/files/public-computer-acceptance.json')
    apk=ROOT/'dist/proactive-agent.apk';digest=hashlib.sha256(apk.read_bytes()).hexdigest()
    assert gate['version']=='1.6.0' and digest==gate['apk_sha256']==pub['apk_sha256']==download['download_sha256']
    assert {c['check'] for c in gate['checks']}=={'identity','bots','ui','motion','mika','public'} and all(c['passed'] for c in gate['checks'])
    assert identity['completed'] and identity['photo_reload'] and identity['primary_routes_checkin'] and identity['crop_pan_and_zoom']
    assert local['coordinate_tap'] and local['real_local_llm_loop'] and live['native_media_store_download_bytes']==768034
    assert deploy['protected_services_unchanged'] and deploy['health']['version']=='1.6.0'
    assert all(hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==sha for name,sha in deploy['server_hashes'].items())
    assert '34 passed' in (WORK/'server-1.6-tests.txt').read_text('utf-8')
    signature=(WORK/'apk-1.6-signature.txt').read_text('utf-8')
    certificate='879d8a5eb7e55e054925d1030f350b3441276e46bef549214001b2bfc4672329'
    assert certificate in signature and 'Verified using v2 scheme' in signature
    assert (WORK/'quiet-mode-1.6.txt').read_text('utf-8-sig').strip()=='quiet'
    private=[(ROOT/'private/device-token').read_bytes().strip(),(ROOT/'private/keystore-password').read_bytes().strip()]
    with zipfile.ZipFile(apk) as archive:
        for entry in archive.infolist():
            blob=archive.read(entry)
            assert not any(secret in blob for secret in private),'Private credential in APK'
    OUT.mkdir(exist_ok=True);screens=OUT/'screenshots-1.6';screens.mkdir(exist_ok=True)
    for directory,prefix in [('identity-acceptance/files','identity-'),('android-v2-evidence/files','v2-'),('bot-management-acceptance/files','bots-'),('mika-acceptance/files','mika-'),('public-computer-acceptance/files','public-')]:
        for path in (WORK/directory).glob(prefix+'*.png'):shutil.copy2(path,screens/path.name)
    for directory,name in [('identity-acceptance','identity-1.6.mp4'),('motion-rebuild/evidence','motion-1.6.mp4'),('android-v2-evidence','ui-1.6.mp4'),('mika-acceptance','mika-local-1.6.mp4'),('bot-management-acceptance','bots-1.6.mp4')]:
        shutil.copy2(WORK/directory/'motion-acceptance.mp4',OUT/name)
    for path,name in [('final-release-gate.json','release-1.6-acceptance.json'),('identity-acceptance/files/identity-acceptance.json','identity-1.6-acceptance.json'),('deployment-1.6.json','deployment-1.6.json'),('mika-acceptance/files/mika-acceptance.json','mika-1.6-acceptance.json'),('public-computer-acceptance/files/public-computer-acceptance.json','public-1.6-acceptance.json')]:
        shutil.copy2(WORK/path,OUT/name)
    (OUT/'motion-1.6-observations.json').write_text(json.dumps({'apk_sha256':digest,'recorded':True,'android_window_frames':observed,'stable_60fps_proven':False},indent=2))
    shutil.copy2(WORK/'identity-1.6-preview.gif',OUT/'identity-1.6-preview.gif')
    shutil.copy2(apk,OUT/'proactive-agent.apk');shutil.copy2(ROOT/'dist/proactive-personal-agent-source.zip',OUT/'proactive-personal-agent-source.zip')
    font=ImageFont.truetype(r'C:\Windows\Fonts\msyh.ttc',18);canvas=Image.new('RGB',(1032,792),'#161820');draw=ImageDraw.Draw(canvas)
    for i,(name,label) in enumerate([('identity-07-reloaded.png','名称、Title 与图片头像'),('identity-04-crop.png','照片裁剪 · 拖动与缩放'),('identity-08-primary.png','选择主助手')]):
        picture=Image.open(screens/name).convert('RGB');picture.thumbnail((324,702));x=12+i*344;canvas.paste(picture,(x,48));draw.text((x+5,13),label,font=font,fill='#ededf3')
    canvas.save(OUT/'ui-1.6-preview.png')
    date=datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).isoformat(timespec='seconds')
    checks='\n'.join(f"| {c['check']} | 通过 | {c['seconds']:.2f} s |" for c in gate['checks'])
    text=f"""# Pulse Bot 1.6.0 交付与验收

时间：{date}

APK：<http://216.23.82.196/proactive-agent/download/proactive-agent.apk>

## 本次完成

- 独立身份编辑页：名称与 Title 标签；首页、对话顶部和详情同步更新，保存后不会被旧的角色设置草稿覆盖。
- 头像提供形状/颜色、生成、上传入口。上传由原生照片选择器或相机读取，照片在手机上拖动、双指/滑块缩放并裁剪；点击完成才同步云端。返回时可继续编辑或放弃修改。
- 生成入口复用执行大脑的服务与密钥，并单独填写图像模型；先展示裁剪预览，确认后保存。配置缺失或接口不支持时显示真实错误。
- 主助手选择页、菜单入口与首页标记。选中的助手接收后续主动追问、默认对话、闲时研究和全局到期提醒。原有对话、工具权限、通知开关与例行任务保留；进行中的回复维持原会话归属。
- PNG 头像经加密接口单独读取，名册不反复传输图片；服务端解码并去除图片元数据。图片可以改回形状头像。
- 同一 APK 的主模块与 Mika 版本为 1.6.0 / code 7。

交互参照 [Grok 官方 Bot 管理说明](https://docs.x.ai/grok-bot/bots)。该页列出的移动端 Name / Title、头像形状/生成/上传与主助手替换是本轮对照范围。原版登录后的逐屏视觉对照仍未完成。

## 签名与公网

| 项目 | 实测 |
| --- | --- |
| 客户端 | 1.6.0 / versionCode 7 / dev.bubuki.proactive |
| APK 大小 | {apk.stat().st_size:,} bytes |
| 本地 SHA-256 | {digest} |
| 公网 HEAD | 200 OK |
| 完整下载大小 | {download['download_bytes']:,} bytes |
| 完整下载 SHA-256 | {download['download_sha256']} |
| APK v2 签名证书 SHA-256 | {certificate} |
| 服务端健康版本 | {deploy['health']['version']} |
| 源码 ZIP 大小 | {source['bytes']:,} bytes |
| 源码 ZIP SHA-256 | {source['sha256']} |
| APK / 源码敏感值检查 | 通过；不含本机配对码、签名密码或私钥 |

本地 APK：D:/bubuki/proactive-personal-agent/dist/proactive-agent.apk

源码：D:/bubuki/proactive-personal-agent/dist/proactive-personal-agent-source.zip

Mika 原生配套源码：[下载对应源码](http://216.23.82.196/proactive-agent/download/mika-corresponding-source.tar.gz)，{pub['gpl_source_bytes']:,} bytes，SHA-256 {pub['gpl_source_sha256']}。本轮原生 C 二进制沿用既有版本，对应源码文件未替换。

## 验收证据

| 流程 | 结果 | 耗时 |
| --- | --- | --- |
{checks}

后端 34 项检查通过，覆盖身份持久化、图片实际解码/元数据清理、非法图片拒绝、权限认证、主助手选择、默认消息/研究路由、进行中会话归属、通知开关及加密 API。生成请求通过真实 HTTP 到隔离的本地图像服务夹具，未调用付费模型。

新增安卓验收实际打开系统照片选择器，选择 MediaStore 测试照片，在裁剪页触摸缩放/拖动，保存并重启 Activity 后从 API 回读图片；还验证主助手选择取消/确认、实际追问归属、放弃未保存修改、缺失 Key 提示、图像服务回环与图片改回形状。交付目录中 identity-1.6.mp4 是本次原生录屏，screenshots-1.6/identity-* 是相应截图。identity-1.6-preview.gif 是同一录屏中照片裁剪的常速节选。

其他五项继续验证置顶/隐藏、完整 UI、页面/面板/手势与键盘、本机 Mika 引擎及日本节点云电脑。云电脑验收含真实公网连接、接管页面、文件入口和原生 MediaStore 下载 768,034 bytes 后逐字节核对。

## 部署边界

仅更新 /opt/proactive-agent/server/，在现有应用 venv 中增加 Pillow==13.0.0，并重启 proactive-agent.service。其监听仍为 127.0.0.1:18190，现有 nginx 子路径沿用。没有 Docker 命令、路由/代理/防火墙修改，也没有重启云电脑服务或既有浏览器。

部署前后 {deploy['protected_services_checked']} 项受保护服务状态、PID 与启动时间一致；{len(deploy['server_hashes'])} 个部署文件与本地哈希一致。旧代码备份：{deploy['backup']}。

电脑档位回读 quiet；验收使用独立 Grok_Reference 模拟器，未操作实体手机。应用的免打扰与 Windows 安静档位是分别管理的设置。

本机模拟器直连日本 IP 的 TCP 80 曾超时；公网流程经现有本机 HTTP 代理完成，该代理仅作为独立模拟器的启动参数。APK 的公网 HEAD/完整下载核验也经现有代理。手机使用时需能访问指定的日本节点。

## 完整目标仍待完成

- 原版 Grok Bot 1.19.0 在本机 ARM 转译环境的 libpairipcore.so 启动崩溃，登录后页面尚不能逐屏实看；当前不能宣称像素及产品能力完全一致。
- 冷启动长帧尚未解决。本轮含录屏的首开 Window 帧时峰值 {observed['first-push']['max_ms']} ms、热切页 {observed['warm-push']['max_ms']} ms，样本分别 {observed['first-push']['samples']} / {observed['warm-push']['samples']} 个。它们是模拟器 Window 采样，不能作为实体手机稳定 60 fps 的证明；原始摘要见 motion-1.6-observations.json。
- 删除/重置、各 Bot 独立记忆的完整生命周期、群组/线程/提及/表情、完整技能与例行任务、连接器和语音等仍有缺项，继续按原目标推进。
- 真实图像模型、真实 SMTP 与实体手机 Root/Shizuku/ARM64 Linux 运行环境没有在本轮验收。相机读取与授权 URI 路径已实现；本轮照片流程实测覆盖系统照片选择器，相机快门流程尚未走查。
- 模型 Key 需由使用者填入。无 Key 时的本地引导和文件快照会如实标明；云电脑手动查看、接管与文件功能可独立使用。

"""
    token=(ROOT/'private/device-token').read_text().strip()
    private_text=text+f"\n## 私人配对信息\n\n节点：http://216.23.82.196/proactive-agent\n\n设备配对码：{token}\n\n这份本机报告含配对码，不上传到公网或包含在源码 ZIP 中。\n"
    for report in [ROOT/'DELIVERY_REPORT.md',OUT/'DELIVERY_REPORT.md']:
        if report.exists() and '1.5.0' in report.read_text('utf-8')[:120]:
            shutil.copy2(report,report.with_name('DELIVERY_REPORT_1_5.md'))
        report.write_text(private_text,'utf-8')
    (OUT/'RELEASE_1_6.md').write_text(text,'utf-8')
    artifacts=[OUT/'proactive-agent.apk',OUT/'proactive-personal-agent-source.zip',OUT/'identity-1.6.mp4',OUT/'motion-1.6.mp4',OUT/'ui-1.6-preview.png',OUT/'identity-1.6-preview.gif']
    sums='\n'.join(hashlib.sha256(p.read_bytes()).hexdigest()+'  '+p.name for p in artifacts)+'\n'
    (OUT/'SHA256SUMS_1_6.txt').write_text(sums,'utf-8');(OUT/'SHA256SUMS.txt').write_text(sums,'utf-8')
    print(json.dumps({'version':'1.6.0','apk_sha256':digest,'report':str(ROOT/'DELIVERY_REPORT.md'),'private_pairing_printed':False,'full_grok_goal_complete':False},indent=2))
if __name__=='__main__':main()
