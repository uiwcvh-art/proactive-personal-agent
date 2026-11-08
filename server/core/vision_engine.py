"""Multimodal Vision & Screen Grounding Engine.

All models in this system are treated as native multimodal vision models.
Never guess "text model vs vision model" and never forward images to a secondary LLM for OCR.
Instead:
1. Draw calibrated 0..1000 normalized coordinate rulers & @eN Accessibility/DOM badges via PIL (~15ms).
2. Pass images directly to the active model via standard OpenAI/FastAPI `image_url` multimodal content blocks.
"""
from __future__ import annotations
import base64
import io
import re
from pathlib import Path


def annotate_tap_crosshair(img_bytes: bytes, tap_x: int, tap_y: int, width: int, height: int) -> bytes:
    """Draw a high-contrast red/white crosshair at (tap_x, tap_y) on the screenshot."""
    try:
        from PIL import Image, ImageDraw
        im = Image.open(io.BytesIO(img_bytes)).convert('RGB')
        iw, ih = im.size
        sx = int(round(tap_x * iw / max(1, width)))
        sy = int(round(tap_y * ih / max(1, height)))
        draw = ImageDraw.Draw(im)
        r_outer = max(14, min(iw, ih) // 28)
        r_inner = max(5, r_outer // 3)
        line_len = int(r_outer * 1.65)
        for w_stroke, col in ((5, (255, 255, 255)), (3, (255, 30, 30))):
            draw.ellipse((sx - r_outer, sy - r_outer, sx + r_outer, sy + r_outer), outline=col, width=w_stroke)
            draw.ellipse((sx - r_inner, sy - r_inner, sx + r_inner, sy + r_inner), outline=col, width=w_stroke)
            draw.line((sx - line_len, sy, sx + line_len, sy), fill=col, width=w_stroke)
            draw.line((sx, sy - line_len, sx, sy + line_len), fill=col, width=w_stroke)
        nx = int(round(tap_x * 1000.0 / max(1, width)))
        ny = int(round(tap_y * 1000.0 / max(1, height)))
        tag = f'TAP({nx},{ny})'
        tx = min(max(6, sx + r_outer + 6), max(6, iw - 130))
        ty = max(6, sy - r_outer - 22)
        draw.rectangle((tx - 3, ty - 2, tx + len(tag) * 7 + 6, ty + 15), fill=(220, 20, 20), outline=(255, 255, 255), width=1)
        draw.text((tx, ty), tag, fill=(255, 255, 255))
        buf = io.BytesIO()
        im.save(buf, format='JPEG', quality=86)
        return buf.getvalue()
    except Exception:
        return img_bytes


def draw_vision_ruler_overlay(img_bytes: bytes, a11y_summary: str = '', screen_w: int = 0, screen_h: int = 0) -> tuple[bytes, int, int]:
    """
    Draws:
    1. A calibrated 0..1000 normalized coordinate ruler & subtle 10% guide grid.
    2. Set-of-Mark (SoM) purple badges for any Accessibility/DOM nodes (`@e1..@eN`, `@b1..@bN`) at their reported `(cx, cy)`.
    Returns (annotated_jpeg_bytes, img_w, img_h).
    """
    try:
        from PIL import Image, ImageDraw
        im = Image.open(io.BytesIO(img_bytes)).convert('RGB')
        iw, ih = im.size
        draw = ImageDraw.Draw(im)
        for step in range(100, 1000, 100):
            y = int(round(step * ih / 1000.0))
            x = int(round(step * iw / 1000.0))
            for sx in range(0, iw, 16):
                draw.line((sx, y, min(iw - 1, sx + 6), y), fill=(0, 210, 255), width=1)
            if step in (200, 400, 500, 600, 800):
                for sy in range(0, ih, 20):
                    draw.line((x, sy, x, min(ih - 1, sy + 6)), fill=(0, 210, 255), width=1)
            lbl = f'Y:{step}'
            draw.rectangle((0, max(0, y - 8), 36, min(ih - 1, y + 7)), fill=(15, 20, 28), outline=(0, 210, 255), width=1)
            draw.text((2, max(0, y - 6)), lbl, fill=(120, 240, 255))
            draw.rectangle((max(0, iw - 37), max(0, y - 8), iw - 1, min(ih - 1, y + 7)), fill=(15, 20, 28), outline=(0, 210, 255), width=1)
            draw.text((max(0, iw - 35), max(0, y - 6)), lbl, fill=(120, 240, 255))
        for step in (200, 500, 800):
            x = int(round(step * iw / 1000.0))
            xlbl = f'X:{step}'
            draw.rectangle((max(0, x - 18), 0, min(iw - 1, x + 20), 14), fill=(15, 20, 28), outline=(0, 210, 255), width=1)
            draw.text((max(0, x - 16), 1), xlbl, fill=(255, 230, 100))

        if a11y_summary and screen_w > 0 and screen_h > 0:
            for m_e in re.finditer(r'(@[eb]\d+)\s+\[[^\]]+\].*?center=\((\d+),(\d+)\)', a11y_summary):
                eref = m_e.group(1)
                ecx, ecy = int(m_e.group(2)), int(m_e.group(3))
                px = int(round(ecx * iw / max(1, screen_w)))
                py = int(round(ecy * ih / max(1, screen_h)))
                draw.ellipse((px - 5, py - 5, px + 5, py + 5), fill=(255, 180, 0), outline=(0, 0, 0), width=1)
                bx = min(max(38, px + 6), max(38, iw - 68))
                by = min(max(15, py - 8), max(15, ih - 16))
                draw.rectangle((bx - 2, by - 1, bx + len(eref) * 6 + 4, by + 12), fill=(120, 30, 180), outline=(255, 220, 80), width=1)
                draw.text((bx, by), eref, fill=(255, 255, 255))

        buf = io.BytesIO()
        im.save(buf, format='JPEG', quality=85)
        return buf.getvalue(), iw, ih
    except Exception:
        return img_bytes, 0, 0


class VisionEngine:
    """Direct multimodal passthrough engine (no secondary LLM OCR calls)."""

    def __init__(self, store=None, llm_or_get_assistant=None, fetch_models_fn=None):
        self.store = store
        self.last_vision_nodes: list[dict] = []

    @staticmethod
    def is_vision_capable_model(model_id: str = '') -> bool:
        """All models configured in this system are multimodal vision models."""
        return True

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
        return {
            'image_file': file_path.name,
            'size_bytes': len(raw_bytes),
            'vision_content': f'已将图片 `{file_path.name}` 原图直传至你的多模态视觉通道，请直接看图作答。',
            '_multimodal_b64': b64,
            '_multimodal_mime': mime,
            '_multimodal_caption': f'【图片文件 `{file_path.name}` 实时画面】' + (f'（提示：{prompt}）' if prompt else ''),
        }

    @staticmethod
    def build_user_multimodal_content(raw_text: str, resolve_path_fn) -> str | list[dict]:
        """Attach uploaded images directly as standard OpenAI `image_url` blocks without secondary OCR."""
        matches = list(dict.fromkeys(re.findall(
            r'((?:uploads/|[A-Za-z0-9_.-]+\.)[^\s\n"\'<>]+?\.(?:png|jpg|jpeg|webp|gif|bmp))',
            raw_text,
            flags=re.IGNORECASE,
        )))
        if not matches:
            return raw_text
        valid_files: list[tuple[str, str, str]] = []
        for rel in matches[:4]:
            clean_rel = rel.removeprefix('workspace/').strip()
            try:
                p = resolve_path_fn(clean_rel)
                if p.exists() and p.is_file() and p.stat().st_size <= 10_000_000:
                    ext = p.suffix.lower()
                    mime = {'.png': 'image/png', '.webp': 'image/webp', '.gif': 'image/gif', '.bmp': 'image/bmp'}.get(ext, 'image/jpeg')
                    b64 = base64.b64encode(p.read_bytes()).decode('ascii')
                    valid_files.append((clean_rel, mime, b64))
            except Exception:
                continue
        if not valid_files:
            return raw_text
        parts: list[dict] = [{'type': 'text', 'text': raw_text}]
        for _, mime, b64 in valid_files:
            parts.append({'type': 'image_url', 'image_url': {'url': f'data:{mime};base64,{b64}'}})
        return parts
