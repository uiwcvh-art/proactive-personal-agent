"""A transparent, atomic, path-confined Markdown vault."""
from __future__ import annotations
from datetime import datetime, timezone, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
import os
import re
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
    '# 🏛️ DeepSeek Bridge (DSB / DS-Claw) 自身系统、多端分治与事件黑板架构说明书 (v4.0)\n'
    '当主人询问「你的架构是什么 / 多端怎么协同 / 黑板怎么用 / 闹钟和看门狗在哪 / 记忆怎么整理 / 云电脑是什么」时，以此文档为唯一权威事实依据。\n\n'
    '## 一、三域物理拓扑与分端独立 Bot 花名册（`手机端` + `Windows 电脑端` + `共享云电脑`）\n'
    '1. **云端统一中枢（`vps-jp` · `/opt/proactive-agent`）**：\n'
    '   - 核心大脑（`Brain` + `BlackboardEngine` + `MemoryEngine` + `Vault`）常驻云端 `/opt/proactive-agent/server`，持久化目录为 `/opt/proactive-agent/data/`。\n'
    '   - 记忆金库（`memory_vault/`）、事件黑板（`blackboard`）、多端消息与归档检索库（SQLite FTS5）均在云端统一维护。\n'
    '2. **不同端采用不同名字的专属常驻 Bot（`home_node: "phone" | "pc"`）+ 档案一键导入导出**：\n'
    '   - **手机端（`source="phone" / "phone2"`）**：常驻手机专属助理团（如主助理/白珂 `companion`、研究助手 `research`、杠精 `contrarian`、鲸鱼娘 `whalechan`），专职掌控手机虚拟屏 `phone_workspace` 与共享云电脑 `cloud_desktop`。\n'
    '   - **电脑端（`source="pc" / "pc2"`）**：常驻电脑专属助理团（默认：总工程师「司工」`pc_engineer`、深度调研「探微」`pc_research`、红队审校「审校」`pc_reviewer`、桌面伴侣「云汐」`pc_companion`），专职掌控 Windows 本机 `dsh_workspace`（含 `cua_desktop`、Git Bash `pc_shell`、DSH 原生会话 `dispatch_session`）**以及直接操控共享云电脑 `cloud_desktop`**！\n'
    '   - **为什么分端不同名？** 全局名字唯一，在黑板上 `@司工`、`@白珂`、`@探微`、`@杠精` 零歧义直达对应端的专属 Bot，再也不会发生跨端同名混淆。\n'
    '   - **快速导出与导入档案（`.dsb-bot.json`）**：支持通过 `/api/bots/{bot_id}/export` 与 `/api/bots/import`（或工作坊面板按钮）一键导出/恢复任意 Bot 的人设、外观、历史对话、置顶记忆与关联笔记，换 Bot 或重置后可秒级无损回归。\n'
    '3. **全天候共享 Linux 云电脑（`cloud_desktop` · 手机端与电脑端均可直接操控！）**：\n'
    '   - 运行在独立隔离的 `proactive-desktop.service`（1 vCPU · 1GB RAM + 2GB Swap · 10GB SSD，X11 `:91` + Chromium CDP `@b1..@bN` + Tmux 持久终端 + `~/workspace`）。\n'
    '   - **电脑端和手机端都拥有 `cloud_desktop` 直接控制权**：适合挂机长任务、隔离环境测试、Linux 编译抓取与跨端文件暂存（云电脑桌面自带 `~/workspace/ARCHITECTURE.md` 自述档案）。\n\n'
    '## 二、彻底废除点对点 `send_message` 乱象 —— 全面改用事件驱动「共享黑板 (`blackboard`)」+ `peek_bots`\n'
    '1. **为什么废除互相发消息（`send_message`）？**\n'
    '   - 旧式点对点同步发消息会导致长耗时工具阻塞、循环互回与上下文污染。现已全面退役，统一由 **`blackboard`（事件黑板）** 与 **`peek_bots`（只读窥视镜）** 承担多 Bot 调度。\n'
    '2. **事件黑板（`blackboard`）六大核心能力**：\n'
    '   - **毫秒级发帖与 `@唤醒`（`<50ms` 瞬时返回，绝不阻塞当前工具轮）**：\n'
    '     调用 `blackboard(action="post", content="...", mentions=["@司工"])` 或 `@everyone` / `@self`。工具在毫秒内立即返回 `{ok: true, post_id}`，后台异步用极简提示词（*“有人在黑板 @ 了你，请自己扫黑板处理”*）唤醒被点名的 Bot 自主读板干活。\n'
    '   - **插话抢占指导（`steer=True`）**：\n'
    '     当主人发出第二轮新指导，或你在黑板发帖带 `steer=True` 给正在忙的 Bot / DSH 会话时，系统直接打断其第一轮旧操作（保留已完成步骤轨迹），像真人插话一样带着第二轮新指导立即继续！电脑端 `dsh_workspace(op="dispatch_session")` 默认秒回（`wait=False`），若目标会话正忙则自动以 `mode="steer"` 插话打断续跑。\n'
    '   - **看板认领防撞单（`action="claim"`）**：\n'
    '     面对 `@everyone` 悬赏帖，空闲 Bot 调用 `blackboard(action="claim", post_id="...")` 原子认领（带 15 分钟租约超时自动释放），绝不重复劳动。\n'
    '   - **产物交接棒（`artifact_path` + `next_action`）**：\n'
    '     长代码、数据与报告写进工作区文件，黑板帖直接挂载 `artifact_path="workspace/..."` 与 `next_action="..."` 干净交棒。\n'
    '   - **按需双盲验收门禁（`review_by` —— 仅在主人或任务明确要求时启用）**：\n'
    '     平时普通任务直接结项；仅当明确要求审查验收时传入 `review_by="@审校"`（或 `@杠精`），提交 `action="complete"` 后自动流转 `review_required` 并唤醒审校员执行 `action="review", verdict="approved"|"rejected"`。\n'
    '   - **闹钟、定时提醒与零 Token 事件看门狗合一**：\n'
    '     黑板原生支持定时与执行动作：传 `at_time="07:30"` / `delay_minutes=15` / `daily_time="08:00"` 配合 `exec_action="wake_bot" | "ring_alarm" | "notify_user"`（默认 `@self` 到点自动叫醒自己或拉响手机闹钟）；支持挂载零 Token 探针 `watch={"kind": "file_exists"|"cmd_exit"|"cmd_contains"|"http_status", "target": "..."}`，后台用纯 Python 轮询（等待期消耗 **0 LLM Token**），条件满足瞬间贴上证据并 `@` 唤醒目标 Bot！\n'
    '3. **跨端进度只读窥视镜（`peek_bots`）**：\n'
    '   - 想快速知道其他端/其他 Bot 干到哪了？直接调用 `peek_bots(target="all" | "<Bot名>", limit=5)`，毫秒级只读获取各 Bot 所在端（`phone`/`pc`）、实时状态（`idle`/`running`）、当前执行工具、流式输出片段、最新消息与名下黑板任务，无需发消息打扰对方。\n\n'
    '## 三、原生 Agent 工具历史格式、98%~99% 缓存命中与满 100K 分段式局部压缩\n'
    '1. **100% 还原原生 Agent 训练分布（`assistant.tool_calls` + `role: "tool"`）**：\n'
    '   - 历史消息不再压扁成伪造的文字旁白，而是严格按大模型原生 Agent 训练格式保留真实的 `assistant(tool_calls)` 与 `role: "tool"` 结果序列（跨轮次自动剥离旧 `reasoning_content`，同轮内完整保留思维链），彻底消除工具调用格式漂移与输出报错。\n'
    '2. **Append-Only 前缀缓存（98%~99% 命中）+ 满 100K「告一段落」分段局部压缩**：\n'
    '   - 对话总上下文 **小于 `100K` Tokens 时绝不触发自动压缩**，全程纯追加（Append-Only），配合尾部 `[System Memory Delta]` 保持 98%~99% Prefix KV Cache 高命中率。\n'
    '   - 当上下文 **达到或超过 `100K` Tokens** 时，触发**分段式局部压缩**：绝不一刀切清空全部对话，而是自动识别前序「已告一段落」的旧话题边界，仅将前半段已完结事项浓缩为结构化归档摘要（原文同步存入 `memory_vault/notes/` 与 SQLite FTS5 供 `conversation_search` 随时召回），原地替换旧片段，完整保留近期正在推进的 30K~40K 原生对话与工具上下文！\n'
    '3. **三层记忆金库（`memory_vault/`）访问铁律**：\n'
    '   - 读写记忆必须使用原生工具 `read_file("memory_vault/...")`、`write_file`、`edit_file`、`memory_search`、`memory_pin`；闲置时由后台 `sleep_time_consolidate` 静默整理沉淀。\n'
)

SKILLS_INDEX_MD = (
    '# 🛠️ 按需技能库索引 (Skills Index)\n\n'
    '> 日常对话不预载技能正文以节省 Token；需要时请直接调用 `read_file("memory_vault/skills/<name>.md")` 或 `custom_tool(action="open_skill", name="<name>")`。\n\n'
    '## 📌 置顶必读核心技能\n'
    '- **[[skills/self_architecture]]** (`memory_vault/skills/self_architecture.md`)：**DSB v4.0 自身系统、分端专属 Bot、事件黑板 (`blackboard`) 与满 100K 局部压缩架构说明书**（涵盖：手机/电脑分端独立花名册与 `.dsb-bot.json` 快速导入导出、`@mention` 事件黑板与闹钟/零Token看门狗合一、`peek_bots` 跨Bot进度窥视、双端直控 `cloud_desktop` 云电脑、原生 `tool_calls` 历史格式及 `<100K` 免压缩 / `>=100K` 告一段落局部压缩）。当主人问起架构、多端协同、黑板、闹钟或记忆机制时必读！\n'
)

TEMPLATES = {
    'INDEX.md': (
        '# 共生记忆库\n\n'
        '所有记忆都可以查看、修改。只记录已知事实；猜测必须标注。\n'
        '> 📌 **架构自省置顶索引**：[[skills/self_architecture]]（自身系统与双轨记忆整理架构说明）｜[[skills/INDEX]]（技能库目录）｜[[profile/pinned_memories]]（核心钉住记忆）\n\n'
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
            excluded_dirs = {'.history', '.trash', '_trash'}
            subdirs = [self.root] + sorted(
                p for p in self.root.iterdir()
                if p.is_dir() and not p.is_symlink() and p.name not in excluded_dirs and not p.name.startswith('.')
            )
            dir_sig = tuple((d.name, d.stat().st_mtime_ns) for d in subdirs)
            if self._files_cache and self._files_cache[0] == dir_sig:
                return list(self._files_cache[1])
            items = []
            for p in sorted(self.root.rglob('*.md')):
                if p.is_symlink() or not p.resolve().is_relative_to(self.root):
                    continue
                rel_parts = p.relative_to(self.root).parts
                if any(part in excluded_dirs or part.startswith('.') or (part != rel_parts[-1] and part.startswith('_')) for part in rel_parts):
                    continue
                items.append({
                    'path': p.relative_to(self.root).as_posix(),
                    'bytes': p.stat().st_size,
                    'modified': p.stat().st_mtime,
                })
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
            head_low = '\n'.join(txt.splitlines()[:12]).lower()
            if 'index: false' in head_low:
                stem_low = Path(rel).stem.lower()
                if not (
                    any(kw in q for kw in ('worldbook', '世界书', 'asmr', stem_low))
                    or any(t in stem_low for t in terms if len(t) >= 3)
                ):
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
            head_new = '\n'.join(text.splitlines()[:12]).lower()
            is_unindexed = 'index: false' in head_new
            if (
                old_text != text
                and not is_unindexed
                and rel_name not in ('INDEX.md', 'skills/INDEX.md', 'timeline/work_log.md')
                and not rel_name.startswith(('timeline/', 'logs/', 'config/', '.history/', '.trash/', '_trash/'))
            ):
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

    def log(self, text, bot_id: str | None = None):
        today = datetime.now(TZ).strftime('%Y-%m-%d')
        safe_bot = re.sub(r'[^a-zA-Z0-9_-]', '', str(bot_id or '').strip())
        if safe_bot:
            path = f'timeline/bots/{safe_bot}/{today}.md'
            if not self.path(path).exists():
                self.write(path, f'---\nindex: false\n---\n# {today} ({safe_bot})\n')
        else:
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

    def _should_exclude_from_index(self, rel_path: str) -> bool:
        parts = Path(rel_path).parts
        if any(part in ('.history', '.trash', '_trash') or part.startswith('.') or part.startswith('_') for part in parts):
            return True
        if rel_path == 'work-log.md' and (self.root / 'timeline' / 'work_log.md').exists():
            return True
        try:
            head_txt = '\n'.join(self.read(rel_path).splitlines()[:12])
            if 'index: false' in head_txt.lower():
                return True
        except Exception:
            pass
        return False

    def _prune_empty_parents(self, start_dir: Path) -> None:
        cur = start_dir
        while cur != self.root and cur.is_relative_to(self.root):
            try:
                if cur.exists() and cur.is_dir() and not any(cur.iterdir()):
                    cur.rmdir()
                    cur = cur.parent
                else:
                    break
            except Exception:
                break

    def reindex(self):
        all_files = [f for f in self.files() if not self._should_exclude_from_index(f['path'])]
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

    def purge_history_backups(self, rel_name: str) -> list[str]:
        clean = str(rel_name or '').removeprefix('memory_vault/').lstrip('/')
        safe_rel = clean.replace('/', '__')
        hist_root = self.root / '.history'
        removed: list[str] = []
        if not hist_root.exists():
            return removed
        for pattern in (f'{safe_rel}.*.md', f'*{safe_rel}*.md'):
            for f in sorted(hist_root.glob(pattern)):
                if f.is_file() and not f.is_symlink():
                    try:
                        f.unlink()
                        removed.append(f.name)
                    except Exception:
                        pass
        return removed

    def delete(self, name: str, purge: bool = True) -> dict:
        clean = str(name or '').removeprefix('memory_vault/').lstrip('/')
        if clean in ('INDEX.md', 'skills/INDEX.md', 'skills/self_architecture.md', 'profile/personal_impression.md', 'profile/user_traits.md'):
            raise ValueError(f'受保护的核心记忆库文件禁止删除: {clean}')
        with self.lock:
            p = self.path(clean)
            rel_name = clean
            snap_saved = None
            purged_backups: list[str] = []
            if p.exists() and p.is_file():
                rel_name = p.relative_to(self.root).as_posix()
                old_text = p.read_text(encoding='utf-8', errors='replace')
                if not purge:
                    snap_saved = self._save_snapshot(rel_name, old_text, new_text='')
                parent_dir = p.parent
                p.unlink()
                self._prune_empty_parents(parent_dir)
            elif purge:
                purged_backups = self.purge_history_backups(rel_name)
                if not purged_backups:
                    raise ValueError(f'要删除的记忆库文件不存在: memory_vault/{clean}')
            else:
                raise ValueError(f'要删除的记忆库文件不存在: memory_vault/{clean}')
            if purge:
                purged_backups.extend(x for x in self.purge_history_backups(rel_name) if x not in purged_backups)
            self._read_cache.pop(rel_name, None)
            self._files_cache = None
            self.reindex()
        return {
            'ok': True,
            'deleted': f'memory_vault/{rel_name}',
            'resolved_path': f'memory_vault/{rel_name}',
            'purged': bool(purge),
            'purged_history_backups': purged_backups,
            'previous_backup': snap_saved,
        }

    def move(self, src: str, dst: str) -> dict:
        clean_src = str(src or '').removeprefix('memory_vault/').lstrip('/')
        clean_dst = str(dst or '').removeprefix('memory_vault/').lstrip('/')
        if clean_src == clean_dst:
            return {'ok': False, 'error': f'源路径与目标路径相同 (memory_vault/{clean_src})，无需移动'}
        if clean_src in ('INDEX.md', 'skills/INDEX.md', 'skills/self_architecture.md'):
            raise ValueError(f'受保护的核心索引文件禁止移动: {clean_src}')
        with self.lock:
            p_src = self.path(clean_src)
            p_dst = self.path(clean_dst)
            if not p_src.exists() or not p_src.is_file():
                raise ValueError(f'源文件不存在: memory_vault/{clean_src}')
            rel_src = p_src.relative_to(self.root).as_posix()
            rel_dst = p_dst.relative_to(self.root).as_posix()
            if rel_src == rel_dst:
                return {'ok': False, 'error': f'源路径与目标路径相同 (memory_vault/{rel_src})，无需移动'}
            content = p_src.read_text(encoding='utf-8', errors='replace')
            self.write(rel_dst, content)
            src_parent = p_src.parent
            p_src.unlink()
            self._prune_empty_parents(src_parent)
            self._read_cache.pop(rel_src, None)
            self._files_cache = None
            self.reindex()
        return {
            'ok': True,
            'moved_from': f'memory_vault/{rel_src}',
            'moved_to': f'memory_vault/{rel_dst}',
        }
