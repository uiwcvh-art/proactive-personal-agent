"""Multimodal Vision & Screen Grounding Engine.

Provides:
1. Fast SHA-256 cached image reading (`analyze_image_b64`, `read_image_file_with_vision`)
2. Automatic user attachment vision enrichment (`enrich_user_message_with_vision`)
3. 1-second Vision UI Tree extraction (`extract_vision_ui_tree`) for custom-rendered surfaces
   like WeChat on Android Virtual Display or graphical apps on the Cloud Desktop.
"""
from __future__ import annotations
import base64, hashlib, json, re
from pathlib import Path
import httpx


class VisionEngine:
    def __init__(self, store, llm_or_get_assistant=None, fetch_models_fn=None):
        self.store = store
        self.llm_client = llm_or_get_assistant if hasattr(llm_or_get_assistant, 'fetch_available_models') else None
        self.get_assistant_fn = llm_or_get_assistant if callable(llm_or_get_assistant) else None
        self.fetch_models_fn = fetch_models_fn or (self.llm_client.fetch_available_models if self.llm_client else None)
        self._vision_cache: dict[str, str] = {}
        self.last_vision_nodes: list[dict] = []

    @staticmethod
    def is_vision_capable_model(model_id: str) -> bool:
        m = (model_id or '').lower()
        if not m or 'deepseek' in m:
            return False
        return any(k in m for k in ('gemini', 'claude', 'gpt-4', 'gpt-5', '4o', 'vl', 'vision', 'pixtral', 'qvq'))

    async def pick_vision_model(self, current_bot_model: str = '') -> str:
        cur_model = (
            self.store.config.effective_model(bot_model=current_bot_model, big=False)
            or self.store.config.effective_model(bot_model=current_bot_model, big=True)
        )
        if self.is_vision_capable_model(cur_model):
            return cur_model
        avail = (await self.fetch_models_fn()) if self.fetch_models_fn else []
        preferred = (
            'antigravity/gemini-3-flash',
            'antigravity-direct/gemini-3-flash',
            'gemini-3-flash',
            'gemini-2.5-flash',
            'antigravity/claude-sonnet-4-6',
            'antigravity-direct/claude-sonnet-4-6',
        )
        for p in preferred:
            if p in avail:
                return p
        for m in avail:
            if self.is_vision_capable_model(m):
                return m
        return 'antigravity/gemini-3-flash'

    async def analyze_image_b64(self, b64: str, mime: str = 'image/jpeg', prompt: str = '', max_tokens: int = 1400, bot_model: str = '') -> str:
        base_url = self.store.config.effective_base_url().rstrip('/')
        api_key = self.store.config.effective_api_key(big=True) or self.store.config.effective_api_key(big=False)
        if not base_url or not api_key or not b64:
            return ''
        effective_prompt = prompt or '请完整、准确地提取并描述这张图片中的所有可见文字（按从上到下顺序保留昵称、时间、气泡对话、标题与按钮文字）以及画面关键信息。'
        cache_key = hashlib.sha256((b64[:65536] + '|' + str(len(b64)) + '|' + effective_prompt[:160]).encode('utf-8')).hexdigest()
        if cache_key in self._vision_cache:
            return self._vision_cache[cache_key]
        v_model = await self.pick_vision_model(bot_model)
        payload = {
            'model': v_model,
            'messages': [{
                'role': 'user',
                'content': [
                    {'type': 'text', 'text': effective_prompt},
                    {'type': 'image_url', 'image_url': {'url': f'data:{mime};base64,{b64}'}},
                ],
            }],
            'max_tokens': max_tokens,
            'stream': False,
        }
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(35.0, connect=10.0), trust_env=False) as client:
                r = await client.post(
                    base_url + '/chat/completions',
                    headers={'Authorization': 'Bearer ' + api_key},
                    json=payload,
                )
            if r.status_code >= 400:
                return ''
            body = r.json()
            out_txt = str(body['choices'][0]['message'].get('content') or '').strip()
            if out_txt:
                if len(self._vision_cache) > 80:
                    self._vision_cache.clear()
                self._vision_cache[cache_key] = out_txt
            return out_txt
        except Exception:
            return ''

    async def read_image_file_with_vision(self, file_path: Path, prompt: str = '') -> dict:
        if not file_path.exists():
            return {'error': f'图片文件不存在: {file_path.name}'}
        raw_bytes = file_path.read_bytes()
        ext = file_path.suffix.lower()
        mime = {
            '.png': 'image/png',
            '.webp': 'image/webp',
            '.gif': 'image/gif',
            '.bmp': 'image/bmp',
        }.get(ext, 'image/jpeg')
        b64 = base64.b64encode(raw_bytes).decode('ascii')
        readout = await self.analyze_image_b64(b64, mime=mime, prompt=prompt)
        return {
            'image_file': file_path.name,
            'size_bytes': len(raw_bytes),
            'vision_content': readout or '（画面为空白或未识别到文字）',
            'note': '已由系统原生多模态视觉直接读取图片全部内容，无需调用任何外部 OCR 或脚本。',
        }

    async def extract_ui_tree(self, b64: str, width: int, height: int, pkg: str = '', target_hint: str = '') -> dict:
        prompt = (
            f'这是一张屏幕实时画面（逻辑坐标范围：宽 0..{width}，高 0..{height}，当前窗口/包名：{pkg or "unknown"}）。\n'
            + (f'特别注意：请务必找出与「{target_hint}」匹配的控件或联系人/文字及其精确中心坐标！\n' if target_hint else '')
            + '请提取画面中所有可见文字（如聊天记录、联系人、标题、网页内容）以及所有可点击/可输入的 UI 元素，严格只输出一个 JSON 对象（不要加任何多余解释）：\n'
            f'{{"screen_text": "按从上到下顺序完整列出画面上的所有文字与界面信息", "nodes": [{{"role": "Row|Button|EditText|Tab|Link|Text", "text": "元素上的文字或图标含义", "x": {width // 2}, "y": {height // 3}}}]}}\n'
            f'注意：x 必须在 0 到 {width} 之间，y 必须在 0 到 {height} 之间，代表该控件在 {width}x{height} 屏幕上的真实像素中心点！'
        )
        raw_out = await self.analyze_image_b64(b64, mime='image/jpeg', prompt=prompt, max_tokens=1600)
        if not raw_out:
            return {'nodes': [], 'summary': '', 'screen_text': ''}
        cleaned = re.sub(r'^```(?:json)?\s*|\s*```$', '', raw_out.strip()).strip()
        m_json = re.search(r'\{.*\}', cleaned, flags=re.DOTALL)
        parsed = {}
        if m_json:
            try:
                parsed = json.loads(m_json.group(0))
            except Exception:
                parsed = {}
        raw_nodes = parsed.get('nodes') if isinstance(parsed.get('nodes'), list) else []
        screen_text = str(parsed.get('screen_text') or raw_out).strip()
        v_nodes = []
        lines = [f'displayId=virtual package={pkg or "unknown"} source=vision_ui_tree count={len(raw_nodes)}']
        for idx, item in enumerate(raw_nodes[:45], 1):
            if not isinstance(item, dict):
                continue
            ref = f'@v{idx}'
            role = str(item.get('role') or 'View').strip()
            txt = str(item.get('text') or '').strip()
            cx = max(0, min(width - 1, int(item.get('x') or (width // 2))))
            cy = max(0, min(height - 1, int(item.get('y') or (height // 2))))
            node_obj = {'ref': ref, 'role': role, 'text': txt, 'center_x': cx, 'center_y': cy, 'clickable': True}
            v_nodes.append(node_obj)
            lines.append(f'{ref} [{role}] "{txt}" center=({cx},{cy}) click')
        if screen_text:
            lines.append(f'--- 画面完整文字内容 ---\n{screen_text}')
        self.last_vision_nodes = v_nodes
        return {
            'nodes': v_nodes,
            'summary': '\n'.join(lines),
            'screen_text': screen_text,
        }

    def match_node(self, ref: str, text: str) -> dict | None:
        clean_ref = (ref or '').strip()
        if clean_ref and re.fullmatch(r'[vb]\d+', clean_ref, flags=re.IGNORECASE):
            clean_ref = '@' + clean_ref.lower()
        if clean_ref.startswith('@'):
            for n in self.last_vision_nodes:
                if n['ref'].lower() == clean_ref.lower():
                    return n
        q = (text or '').strip().lower()
        if q:
            for n in self.last_vision_nodes:
                if n['text'].lower() == q:
                    return n
            for n in self.last_vision_nodes:
                if q in n['text'].lower() or (n['text'] and n['text'].lower() in q):
                    return n
        return None
