"""A transparent, atomic, path-confined Markdown vault."""
from __future__ import annotations
from datetime import datetime, timezone, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
import os
import threading

try:
    TZ = ZoneInfo('Asia/Shanghai')
except ZoneInfoNotFoundError:
    TZ = timezone(timedelta(hours=8))

def now_iso():
    return datetime.now(TZ).isoformat(timespec='seconds')

def atomic_write(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f'.{path.name}.{os.getpid()}.{threading.get_ident()}.tmp')
    try:
        with temp.open('w', encoding='utf-8', newline='\n') as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        if os.name != 'nt':
            temp.chmod(0o600)
            try:
                if hasattr(os, 'geteuid') and os.geteuid() == 0:
                    st = path.parent.stat()
                    if st.st_uid != 0:
                        os.chown(temp, st.st_uid, st.st_gid)
            except Exception:
                pass
        temp.replace(path)
    finally:
        if temp.exists():
            try:
                temp.unlink()
            except Exception:
                pass

SELF_ARCH_SKILL_MD = (
    '# 🏛️ DeepSeek Bridge (DSB / DS-Claw) 自身系统与多端记忆架构说明书\n'
    '当主人询问「你的记忆是怎么整理的 / 为什么没整理 / 你的架构是什么 / 怎么跨端通信 / 手机和电脑端怎么配合」时，以此文档为唯一权威事实依据。\n\n'
    '## 一、四节点多端对称与云上记忆中枢（`Windows一/二` + `安卓一/二` + `云电脑`）\n'
    '1. **云端统一大脑与记忆中枢**：\n'
    '   - 你的主控服务端（Brain + MemoryEngine + Vault）运行在云端宿主机（`/opt/proactive-agent/server`），真实持久化数据目录为 `/opt/proactive-agent/data/`。\n'
    '   - 所有记忆库（`memory_vault/`）、状态（`state.json`）与工具路由都在云端统一维护。\n'
    '2. **四节点独立会话与单端独立开关（`node_overrides`）**：\n'
    '   - 系统支持 4 个客户端节点：`Windows一` (`source="pc"`)、`Windows二` (`source="pc2"`)、`安卓一` (`source="phone"`)、`安卓二` (`source="phone2"`)。\n'
    '   - 所有节点共用 4 位常驻 Bot：`主理人` (`companion`)、`研究助手` (`research`)、`杠精` (`contrarian`)、`鲸鱼娘` (`whalechan`)。\n'
    '   - **全局设定 vs 单端独立开关**：Bot 的名字、角色设定 (`instructions`)、模型 (`model`)、工具集 (`tools`) 为全端统一；而「好奇心主动搭话 (`curiosity_enabled`)」与「通知提醒 (`notify`)」按节点存储在 `node_overrides` 中（例如手机 `安卓一` 开启好奇心、平板 `安卓二` 关闭好奇心，互不干扰）。\n'
    '3. **极简跨端/跨 Bot 协同规则（`send_message` 与 `dsh_workspace`）**：\n'
    '   - 发给同端同事：`send_message(to_bot_id="研究助手", text="...")`。\n'
    '   - 发给跨端指定 Bot：`send_message(to_bot_id="Windows一:白珂", text="...")` 或 `send_message(to_bot_id="安卓一:白珂", text="...")`。\n'
    '   - 电脑端 `Windows一` 本机桌面与窗口应用操控：在电脑端会话中可直接调用 `dsh_workspace(op="cua_desktop", action="list_windows"|"get_window_state"|"activate_window"|"click"|"type_text"|"press_key"|"scroll", window_id=..., include_screenshot=true)` 操控 QQ/浏览器等 Windows 桌面软件，或调用 `run_bash` (`pc_shell`) 与 `dsh_workspace(op="dispatch_session")`。\n'
    '   - **单向调用、自动回传**：主叫方调用 `send_message` 后，系统自动唤醒目标 Bot 执行任务并直接把目标 Bot 的正文回复带回主叫方会话（展示为「➔ 发给 ...」与「... · 协作汇报」），主叫方本轮立即完成；被叫方收到「📩 来自 ...」后只需直接用自然语言正文回答（**被叫方禁止再次调用 `send_message` 回传**，以免产生重复消息）。\n'
    '4. **手机端 (`phone_workspace`) 真实路径与验证码/扫码规范**：\n'
    '   - `phone_workspace(op="push_file", path="...")` 将云端文件推入手机的真实路径为 `/sdcard/Download/DeepSeek Claw/`，并自动触发系统 MediaScanner 刷新媒体库，在手机 QQ/微信扫码页点击「相册」即可直接选中刚推送的二维码图片。\n'
    '   - 读取手机短信验证码时，优先调用 `phone_workspace(op="notifications", action="list")` 查看通知收件箱，或调用 `phone_workspace(op="open", target="信息")` 打开系统短信 App 查看画面，严禁依赖无权限的 `content query --uri content://sms`。\n\n'
    '## 二、三层记忆金库（`memory_vault/`）与路径访问铁律\n'
    '1. **三层记忆结构**：\n'
    '   - **第 1 层 · 核心置顶与画像层（`profile/` & `INDEX.md`）**：\n'
    '     - `memory_vault/INDEX.md`：记忆库全量双链总目录。\n'
    '     - `memory_vault/profile/pinned_memories.md`：最高优先级的置顶核心记忆与关键事务最终定论，可通过 `memory_pin(action="pin"|"unpin"|"list")` 管理。\n'
    '     - `memory_vault/profile/personal_impression.md` & `memory_vault/profile/user_traits.md`：主人立体个人印象档案与核心偏好日志。\n'
    '   - **第 2 层 · 原子笔记与待办层（`notes/`、`tasks/`、`playbooks/`）**：\n'
    '     - `memory_vault/notes/*.md`：Zettelkasten 原子事实笔记（如订阅台账、接口配置、专项结论）。\n'
    '     - `memory_vault/tasks/active_deadlines.md` & `sniffed_opportunities.md`：死线与待办。\n'
    '   - **第 3 层 · 按需技能与时间线流水层（`skills/`、`timeline/`、`logs/`）**：\n'
    '     - `memory_vault/skills/INDEX.md` & `memory_vault/skills/*.md`：零额外 Token 按需加载的技能书（含本篇 `self_architecture.md`）。\n'
    '     - `memory_vault/timeline/YYYY-MM-DD.md` & `work_log.md`：每日完整对话流水与跨端一句话协同日志。\n'
    '2. **为什么在 `run_bash` 或 `cloud_desktop` 里直接 `find .` 看不到宿主机的 `memory_vault/`？**\n'
    '   - `run_bash` 运行在受限的沙箱工作区（`/opt/proactive-agent/data/workspace`），`cloud_desktop` 运行在独立的 Linux 桌面容器中；出于安全隔离设计，宿主机目录 `/opt/proactive-agent/data/memory_vault/` **不会直接暴露为云电脑容器 `/workspace` 下的普通子文件夹**。\n'
    '   - **正确访问方式**：读写记忆库必须直接调用服务端原生工具 `read_file("memory_vault/...")`、`write_file("memory_vault/...")`、`edit_file("memory_vault/...")`、`memory_search(query="...")`、`memory_pin(...)` 或 `custom_tool(action="open_skill", name="...")`！\n\n'
    '## 三、双轨记忆整理与 Prefix KV Cache 100% 保护机制\n'
    '1. **机制一：闲时隐藏整理（`sleep_time_consolidate`）**\n'
    '   - 当主人聊完停顿闲置后，后台引擎会自动静默运行 `sleep_time_consolidate`（全程隐藏，不打扰聊天流、不发多余通知），自动提取已完成操作、新偏好与个人印象更新写入 `memory_vault/`。\n'
    '2. **机制二：尾部增量注入（“放最底下” · Tail Memory Delta）**\n'
    '   - 会话开头的 `messages[0]`（System Prompt）在会话期间严格保持冻结，以确保 200K 上下文窗口内 100% 命中上游 LLM 的 Prefix KV Cache。\n'
    '   - 每当记忆库或跨端拓扑发生变更时，系统自动生成 `[System Memory Delta / 最新记忆同步]` 追加并锚定在当前对话历史尾部，后续轮次依然 100% 命中 KV Cache。\n'
)

SKILLS_INDEX_MD = (
    '# 🛠️ 按需技能库索引 (Skills Index)\n\n'
    '> 日常对话不预载技能正文以节省 Token；需要时请直接调用 `read_file("memory_vault/skills/<name>.md")` 或 `custom_tool(action="open_skill", name="<name>")`。\n\n'
    '## 📌 置顶必读核心技能\n'
    '- **[[skills/self_architecture]]** (`memory_vault/skills/self_architecture.md`)：**白珂自身系统与记忆架构说明书**（双端对称与云上记忆中枢、三层 `memory_vault/` 真实路径与为什么不能用 bash find、3分钟闲时隐藏整理 `sleep_time_consolidate`、以及保护 200K Prefix KV Cache 的「放最底下」尾部增量记忆同步机制）。当主人问起你的架构、记忆怎么整理、为什么没整理或记忆路径时，必须先读此文件！\n'
)

TEMPLATES = {
    'INDEX.md': (
        '# 共生记忆库\n\n'
        '所有记忆都可以查看、修改。只记录已知事实；猜测必须标注。\n'
        '> 📌 **架构自省置顶索引**：[[skills/self_architecture]]（白珂自身系统与双轨记忆整理架构说明）｜[[skills/INDEX]]（技能库目录）｜[[profile/pinned_memories]]（核心钉住记忆）\n\n'
    ),
    'skills/INDEX.md': SKILLS_INDEX_MD,
    'skills/self_architecture.md': SELF_ARCH_SKILL_MD,
    'profile/user_traits.md': '# 用户画像\n\n尚未收集到已确认的偏好。\n',
    'profile/personal_impression.md': (
        '# 主人立体个人印象档案 (Personal Impression)\n\n'
        '> 由助理在日常对话与好奇心互动中循序渐进补全，只记录主人亲口确认的事实。\n\n'
        '## 1. 出生、成长与故乡\n'
        '- 出生地 / 家乡：（待了解）\n'
        '- 生日 / 年代与成长阶段：（待了解）\n'
        '- 童年或成长中印象最深的事：（待了解）\n\n'
        '## 2. 身份、学业与事业角色\n'
        '- 当前核心身份 / 角色：（待了解）\n'
        '- 专业领域与擅长方向：（待了解）\n'
        '- 长期人生目标或理想状态：（待了解）\n\n'
        '## 3. 家庭、羁绊与人际关系\n'
        '- 家庭结构与家人相处氛围：（待了解）\n'
        '- 生活中最重要的人或宠物：（待了解）\n'
        '- 社交习惯与人际边界：（待了解）\n\n'
        '## 4. 生活方式、审美与精神角落\n'
        '- 作息规律与日常节奏：（待了解）\n'
        '- 饮食口味偏好与忌口：（待了解）\n'
        '- 音乐 / 游戏 / 影视 / 阅读等精神喜好：（待了解）\n\n'
        '## 5. 性格底色、价值观与相处雷区\n'
        '- 自我认同的性格特点：（待了解）\n'
        '- 最看重的品质与价值观：（待了解）\n'
        '- 讨厌的沟通方式或绝对雷区：（待了解）\n'
    ),
    'profile/annoyance_valve.md': '# 烦人度阀门\n\n当前：好奇心开启。用户可以随时暂停。\n',
    'profile/pinned_memories.md': '# 📌 核心钉住记忆 (Pinned Core Memories)\n\n',
    'tasks/sniffed_opportunities.md': '# 可能代劳的机会\n\n先问清卡点，再试做第一步；未经确认不固化职责。\n',
    'tasks/active_deadlines.md': '# 已确认死线\n\n只记录用户明确承诺的时间。\n',
    'playbooks/routines.md': '# 常驻职责\n\n只有点击「以后接着干」的成果会登记在这里。\n',
    'timeline/work_log.md': '# 跨端协同 Work Log (One-Line Summaries)\n\n> 记录手机端 Bot 与电脑端 DeepSeek Harness (DS-Claw) 的关键任务操作总结（每次仅记一句话，不污染静态系统前缀缓存）。\n',
}

class Vault:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self._read_cache: dict[str, tuple[int, int, str]] = {}
        self._files_cache: tuple[int, list[dict]] | None = None
        self.mutation_listeners: list = []
        for path, text in TEMPLATES.items():
            if not self.path(path).exists() or path in ('skills/self_architecture.md', 'skills/INDEX.md'):
                self.write(path, text)
        self.prune_storage()
        self.log('记忆库就绪。')
        self.reindex()

    def path(self, name: str) -> Path:
        if not name or '\\' in name or name.startswith('/') or ':' in name or '\x00' in name:
            raise ValueError('只允许记忆库内的相对 Markdown 路径')
        candidate = self.root / name
        if '..' in candidate.parts or candidate.suffix.lower() != '.md':
            raise ValueError('只允许 .md 文件，禁止上级路径')
        # Reject even an in-vault symlink: the destination could change later.
        if any(p.is_symlink() for p in [candidate, *list(candidate.parents)[:len(candidate.parts)-len(self.root.parts)]]):
            raise ValueError('不允许符号链接')
        resolved = candidate.resolve()
        if not resolved.is_relative_to(self.root):
            raise ValueError('路径超出记忆库')
        return resolved

    def files(self):
        with self.lock:
            subdirs = [self.root] + sorted(
                p for p in self.root.iterdir()
                if p.is_dir() and not p.is_symlink() and p.name != '.history'
            )
            dir_sig = tuple((d.name, d.stat().st_mtime_ns) for d in subdirs)
            if self._files_cache and self._files_cache[0] == dir_sig:
                return list(self._files_cache[1])
            items = [{'path': p.relative_to(self.root).as_posix(), 'bytes': p.stat().st_size,
                      'modified': p.stat().st_mtime} for p in sorted(self.root.rglob('*.md'))
                     if not p.is_symlink() and p.resolve().is_relative_to(self.root)
                     and '.history' not in p.relative_to(self.root).parts]
            self._files_cache = (dir_sig, items)
            return list(items)

    def _save_snapshot(self, rel_name: str, old_text: str, new_text: str = '') -> str | None:
        if not old_text.strip() or rel_name in ('INDEX.md', 'timeline/work_log.md') or rel_name.startswith(('logs/', 'config/')):
            return None
        try:
            hist_root = self.root / '.history'
            safe_rel = rel_name.replace('/', '__')
            is_timeline = rel_name.startswith('timeline/')
            existing = sorted(hist_root.glob(f'{safe_rel}.*.md')) if hist_root.exists() else []
            # For daily timeline logs, skip pure-append snapshots unless >6 hours since last snapshot
            if is_timeline and new_text and new_text.startswith(old_text.rstrip()):
                if existing:
                    try:
                        age = (datetime.now(TZ).timestamp() - existing[-1].stat().st_mtime)
                        if age < 21600:
                            return None
                    except Exception:
                        pass
                else:
                    return None
            stamp = datetime.now(TZ).strftime('%Y%m%d-%H%M%S-%f')[:19]
            snap_name = f'{safe_rel}.{stamp}.md'
            snap_path = hist_root / snap_name
            atomic_write(snap_path, old_text)
            max_keep = 2 if is_timeline else 8
            existing = sorted(hist_root.glob(f'{safe_rel}.*.md'))
            for old_snap in existing[:-max_keep]:
                try:
                    old_snap.unlink()
                except Exception:
                    pass
            return f'memory_vault/.history/{snap_name}'
        except Exception:
            return None

    def prune_storage(self, max_history_age_days: float = 7.0, max_history_total_bytes: int = 1_500_000) -> dict:
        """Prune redundant .history snapshots and bound high-frequency append logs while preserving core memories."""
        import re as _re
        removed_snaps = 0
        freed_bytes = 0
        trimmed_logs = 0
        now_ts = datetime.now(TZ).timestamp()
        today_safe = f"timeline__{datetime.now(TZ).strftime('%Y-%m-%d')}.md"
        with self.lock:
            hist_root = self.root / '.history'
            if hist_root.exists():
                by_prefix: dict[str, list[Path]] = {}
                for f in sorted(hist_root.glob('*.md')):
                    if not f.is_file() or f.is_symlink():
                        continue
                    # Match prefix before .YYYYMMDD-HHMMSS(-mmm).md
                    m_snap = _re.match(r'^(.+)\.\d{8}-\d{6}(?:-\d+)?\.md$', f.name)
                    if m_snap:
                        prefix = m_snap.group(1)
                    else:
                        parts = f.name.split('.')
                        prefix = '.'.join(parts[:-2]) if len(parts) >= 3 else f.stem
                    if not prefix.endswith('.md'):
                        prefix = f'{prefix}.md'
                    by_prefix.setdefault(prefix, []).append(f)

                for prefix, snaps in by_prefix.items():
                    snaps.sort(key=lambda p: p.stat().st_mtime)
                    # 1. Remove snapshots for transient logs, work_log, or past days' timeline files
                    if (
                        prefix.startswith(('logs__', 'config__', 'timeline__work_log'))
                        or (prefix.startswith('timeline__') and prefix != today_safe)
                        or '%5F' in prefix
                    ):
                        for s in snaps:
                            try:
                                sz = s.stat().st_size
                                s.unlink()
                                removed_snaps += 1
                                freed_bytes += sz
                            except Exception:
                                pass
                        continue

                    max_keep = 2 if prefix.startswith('timeline__') else 8
                    cutoff_ts = now_ts - max_history_age_days * 86400.0
                    # Always keep at least the newest 1 snapshot for core profile/notes/tasks/playbooks files
                    to_delete = snaps[:-max_keep] if len(snaps) > max_keep else []
                    kept = snaps[-max_keep:]
                    if len(kept) > 1:
                        for s in kept[:-1]:
                            try:
                                if s.stat().st_mtime < cutoff_ts:
                                    to_delete.append(s)
                            except Exception:
                                pass
                    for s in to_delete:
                        try:
                            sz = s.stat().st_size
                            s.unlink()
                            removed_snaps += 1
                            freed_bytes += sz
                        except Exception:
                            pass

                # Enforce total .history directory byte cap
                remaining = [f for f in hist_root.glob('*.md') if f.is_file() and not f.is_symlink()]
                remaining.sort(key=lambda p: p.stat().st_mtime)
                total_sz = sum(f.stat().st_size for f in remaining)
                while total_sz > max_history_total_bytes and len(remaining) > 4:
                    oldest = remaining.pop(0)
                    try:
                        sz = oldest.stat().st_size
                        oldest.unlink()
                        total_sz -= sz
                        removed_snaps += 1
                        freed_bytes += sz
                    except Exception:
                        pass

            # Bound logs/notifications_inbox.md so it never grows unbounded
            notif_log = self.root / 'logs' / 'notifications_inbox.md'
            if notif_log.exists() and notif_log.is_file():
                try:
                    raw_n = notif_log.read_text(encoding='utf-8', errors='replace')
                    n_lines = raw_n.splitlines()
                    if len(n_lines) > 450 or len(raw_n) > 64_000:
                        headers = [ln for ln in n_lines[:10] if not ln.startswith('- ')]
                        items = [ln for ln in n_lines if ln.startswith('- ')]
                        new_n = ('\n'.join(headers).strip() + '\n\n' if headers else '# 手机通知收敛日志\n\n') + '\n'.join(items[-300:]) + '\n'
                        atomic_write(notif_log, new_n)
                        self._read_cache.pop('logs/notifications_inbox.md', None)
                        trimmed_logs += 1
                except Exception:
                    pass

            self._files_cache = None
        return {
            'removed_snapshots': removed_snaps,
            'freed_bytes': freed_bytes,
            'trimmed_logs': trimmed_logs,
        }

    def history(self, name: str) -> list[dict]:
        with self.lock:
            p = self.path(name)
            rel_name = p.relative_to(self.root).as_posix()
            safe_rel = rel_name.replace('/', '__')
            hist_root = self.root / '.history'
            if not hist_root.exists():
                return []
            snaps = sorted(hist_root.glob(f'{safe_rel}.*.md'), reverse=True)
            return [
                {
                    'backup_path': f'memory_vault/.history/{s.name}',
                    'bytes': s.stat().st_size,
                    'modified': datetime.fromtimestamp(s.stat().st_mtime, TZ).isoformat(timespec='seconds'),
                }
                for s in snaps[:25]
            ]

    def read(self, name):
        with self.lock:
            p = self.path(name)
            rel_name = p.relative_to(self.root).as_posix()
            st = p.stat()
            cached = self._read_cache.get(rel_name)
            if cached and cached[0] == st.st_mtime_ns and cached[1] == st.st_size:
                return cached[2]
            content = p.read_text(encoding='utf-8')
            self._read_cache[rel_name] = (st.st_mtime_ns, st.st_size, content)
            return content

    def search(self, query: str, limit: int = 6) -> list[dict]:
        q = (query or '').strip().lower()
        if not q:
            return []
        import re as _re
        terms = [t for t in _re.split(r'[\s,，。；;、|]+', q) if t]
        hits: list[tuple[int, dict]] = []
        for f in self.files():
            rel = f['path']
            if rel in ('INDEX.md', 'skills/INDEX.md'):
                continue
            try:
                txt = self.read(rel)
            except Exception:
                continue
            low = txt.lower()
            score = sum(3 if t in low else 0 for t in terms)
            if q in low:
                score += 6
            if score > 0:
                matched_lines = [ln.strip() for ln in txt.splitlines() if any(t in ln.lower() for t in terms)]
                excerpt = '\n'.join(matched_lines[:5])[:500] if matched_lines else txt[:400]
                hits.append((score, {'path': f'memory_vault/{rel}', 'score': score, 'excerpt': excerpt}))
        hits.sort(key=lambda x: x[0], reverse=True)
        return [h[1] for h in hits[:max(1, int(limit or 6))]]

    def write(self, name, text) -> str | None:
        if len(text.encode('utf-8')) > 512_000:
            raise ValueError('单个 Markdown 文件不能超过 512 KB')
        changed_core = False
        rel_name = ''
        with self.lock:
            p = self.path(name)
            rel_name = p.relative_to(self.root).as_posix()
            snap_saved = None
            old_text = ''
            if p.exists():
                try:
                    old_text = p.read_text(encoding='utf-8')
                    if old_text != text:
                        snap_saved = self._save_snapshot(rel_name, old_text, new_text=text)
                except Exception:
                    pass
            if old_text != text and rel_name not in ('INDEX.md', 'skills/INDEX.md', 'timeline/work_log.md') and not rel_name.startswith(('timeline/', 'logs/', 'config/', '.history/')):
                changed_core = True
            atomic_write(p, text)
            st = p.stat()
            self._read_cache[rel_name] = (st.st_mtime_ns, st.st_size, text)
            self._files_cache = None
        if changed_core and getattr(self, 'mutation_listeners', None):
            for fn in list(self.mutation_listeners):
                try:
                    fn(rel_name, text)
                except Exception:
                    pass
        return snap_saved

    def append(self, name, text):
        with self.lock:
            path = self.path(name)
            rel_name = path.relative_to(self.root).as_posix()
            old = path.read_text(encoding='utf-8') if path.exists() else ''
            combined = old + '\n' + text + '\n'
            if rel_name == 'logs/notifications_inbox.md':
                lines = combined.splitlines()
                if len(lines) > 450 or len(combined) > 64_000:
                    headers = [ln for ln in lines[:10] if not ln.startswith('- ')]
                    items = [ln for ln in lines if ln.startswith('- ')]
                    combined = ('\n'.join(headers).strip() + '\n\n' if headers else '# 手机通知收敛日志\n\n') + '\n'.join(items[-300:]) + '\n'
            self.write(name, combined)

    def log(self, text):
        today = datetime.now(TZ).strftime('%Y-%m-%d')
        path = f'timeline/{today}.md'
        if not self.path(path).exists():
            self.write(path, f'# {today}\n')
        self.append(path, f'- {now_iso()} {text}')

    def append_work_log(self, summary: str) -> str:
        """Appends a single concise 1-line summary to timeline/work_log.md (bounded to last 200 entries)."""
        clean = ' '.join(str(summary or '').strip().splitlines()).strip()
        if not clean:
            return ''
        if not clean.startswith('- '):
            stamp = datetime.now(TZ).strftime('%Y-%m-%d %H:%M')
            entry = f'- {stamp} · {clean}'
        else:
            entry = clean
        rel = 'timeline/work_log.md'
        with self.lock:
            p = self.path(rel)
            old = p.read_text(encoding='utf-8') if p.exists() else TEMPLATES[rel]
            lines = old.splitlines()
            header_lines = [ln for ln in lines if not ln.startswith('- ')]
            item_lines = [ln for ln in lines if ln.startswith('- ')]
            item_lines.append(entry[:320])
            item_lines = item_lines[-200:]
            header_text = '\n'.join(header_lines).strip() or TEMPLATES[rel].strip()
            new_text = header_text + '\n\n' + '\n'.join(item_lines) + '\n'
            self.write(rel, new_text)
        return entry

    def read_work_log_tail(self, limit: int = 25) -> list[str]:
        rel = 'timeline/work_log.md'
        with self.lock:
            p = self.path(rel)
            if not p.exists():
                return []
            items = [ln.strip() for ln in p.read_text(encoding='utf-8').splitlines() if ln.strip().startswith('- ')]
            cap = max(1, min(100, int(limit or 25)))
            return items[-cap:]

    def reindex(self):
        all_files = self.files()
        skill_entries = [
            f'- [[{f["path"][:-3]}]] (`memory_vault/{f["path"]}`)'
            for f in all_files
            if f['path'].startswith('skills/') and f['path'] not in ('skills/INDEX.md', 'skills/self_architecture.md')
        ]
        skills_idx_body = SKILLS_INDEX_MD
        if skill_entries:
            skills_idx_body += '\n## 📚 其他已沉淀技能\n' + '\n'.join(skill_entries) + '\n'
        self.write('skills/INDEX.md', skills_idx_body)
        priority_paths = ('skills/self_architecture.md', 'skills/INDEX.md', 'profile/pinned_memories.md')
        top_links = [f'- [[{p[:-3]}]]' for p in priority_paths if any(f['path'] == p for f in all_files)]
        other_links = [f'- [[{f["path"][:-3]}]]' for f in all_files if f['path'] != 'INDEX.md' and f['path'] not in priority_paths]
        links = '\n'.join(top_links + other_links)
        self.write('INDEX.md', TEMPLATES['INDEX.md'] + links + '\n')
