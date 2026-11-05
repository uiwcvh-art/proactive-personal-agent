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
        temp.replace(path)
    finally:
        if temp.exists():
            try:
                temp.unlink()
            except Exception:
                pass

TEMPLATES = {
    'INDEX.md': '# 共生记忆库\n\n所有记忆都可以查看、修改。只记录已知事实；猜测必须标注。\n\n',
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
}

class Vault:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self._read_cache: dict[str, tuple[int, int, str]] = {}
        self._files_cache: tuple[int, list[dict]] | None = None
        for path, text in TEMPLATES.items():
            if not self.path(path).exists():
                self.write(path, text)
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
            dir_mtime = self.root.stat().st_mtime_ns
            if self._files_cache and self._files_cache[0] == dir_mtime:
                return list(self._files_cache[1])
            items = [{'path': p.relative_to(self.root).as_posix(), 'bytes': p.stat().st_size,
                      'modified': p.stat().st_mtime} for p in sorted(self.root.rglob('*.md'))
                     if not p.is_symlink() and p.resolve().is_relative_to(self.root)
                     and '.history' not in p.relative_to(self.root).parts]
            self._files_cache = (dir_mtime, items)
            return list(items)

    def _save_snapshot(self, rel_name: str, old_text: str) -> str | None:
        if not old_text.strip() or rel_name == 'INDEX.md':
            return None
        try:
            hist_root = self.root / '.history'
            safe_rel = rel_name.replace('/', '__')
            stamp = datetime.now(TZ).strftime('%Y%m%d-%H%M%S')
            snap_name = f'{safe_rel}.{stamp}.md'
            snap_path = hist_root / snap_name
            atomic_write(snap_path, old_text)
            # Keep last 25 snapshots per file
            existing = sorted(hist_root.glob(f'{safe_rel}.*.md'))
            for old_snap in existing[:-25]:
                try:
                    old_snap.unlink()
                except Exception:
                    pass
            return f'memory_vault/.history/{snap_name}'
        except Exception:
            return None

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
            st = p.stat()
            cached = self._read_cache.get(name)
            if cached and cached[0] == st.st_mtime_ns and cached[1] == st.st_size:
                return cached[2]
            content = p.read_text(encoding='utf-8')
            self._read_cache[name] = (st.st_mtime_ns, st.st_size, content)
            return content

    def write(self, name, text) -> str | None:
        if len(text.encode('utf-8')) > 512_000:
            raise ValueError('单个 Markdown 文件不能超过 512 KB')
        with self.lock:
            p = self.path(name)
            rel_name = p.relative_to(self.root).as_posix()
            snap_saved = None
            if p.exists():
                try:
                    old_text = p.read_text(encoding='utf-8')
                    if old_text != text:
                        snap_saved = self._save_snapshot(rel_name, old_text)
                except Exception:
                    pass
            atomic_write(p, text)
            st = p.stat()
            self._read_cache[name] = (st.st_mtime_ns, st.st_size, text)
            self._files_cache = None
            return snap_saved

    def append(self, name, text):
        with self.lock:
            path = self.path(name)
            old = path.read_text(encoding='utf-8') if path.exists() else ''
            self.write(name, old + '\n' + text + '\n')

    def log(self, text):
        today = datetime.now(TZ).strftime('%Y-%m-%d')
        path = f'timeline/{today}.md'
        if not self.path(path).exists():
            self.write(path, f'# {today}\n')
        self.append(path, f'- {now_iso()} {text}')

    def reindex(self):
        links = '\n'.join(f'- [[{f["path"][:-3]}]]' for f in self.files() if f['path'] != 'INDEX.md')
        self.write('INDEX.md', TEMPLATES['INDEX.md'] + links + '\n')
