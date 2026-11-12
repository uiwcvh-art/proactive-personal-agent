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
    1. A calibrated 4-sided (top, bottom, left, right) 0..1000 normalized coordinate ruler & subtle 10% guide grid.
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
            for sy in range(0, ih, 20):
                draw.line((x, sy, x, min(ih - 1, sy + 6)), fill=(0, 210, 255), width=1)
            # Left & Right Y:100..900 ruler ticks
            ylbl = f'Y:{step}'
            draw.rectangle((0, max(0, y - 8), 36, min(ih - 1, y + 7)), fill=(15, 20, 28), outline=(0, 210, 255), width=1)
            draw.text((2, max(0, y - 6)), ylbl, fill=(120, 240, 255))
            draw.rectangle((max(0, iw - 37), max(0, y - 8), iw - 1, min(ih - 1, y + 7)), fill=(15, 20, 28), outline=(0, 210, 255), width=1)
            draw.text((max(0, iw - 35), max(0, y - 6)), ylbl, fill=(120, 240, 255))
            # Top & Bottom X:100..900 ruler ticks
            xlbl = f'X:{step}' if step in (200, 500, 800) else str(step)
            half_w = 18 if step in (200, 500, 800) else 13
            draw.rectangle((max(0, x - half_w), 0, min(iw - 1, x + half_w + 2), 14), fill=(15, 20, 28), outline=(0, 210, 255), width=1)
            draw.text((max(0, x - half_w + 2), 1), xlbl, fill=(255, 230, 100))
            draw.rectangle((max(0, x - half_w), max(0, ih - 15), min(iw - 1, x + half_w + 2), ih - 1), fill=(15, 20, 28), outline=(0, 210, 255), width=1)
            draw.text((max(0, x - half_w + 2), max(0, ih - 14)), xlbl, fill=(255, 230, 100))

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


def _classify_rgb_color(r: int, g: int, b: int) -> str:
    mx = max(r, g, b)
    mn = min(r, g, b)
    diff = mx - mn
    if diff < 28:
        if mx < 55:
            return 'black'
        if mn > 215:
            return 'white'
        return 'gray'
    if g >= r + 35 and g >= b + 35:
        return 'green'
    if r >= g + 45 and r >= b + 45:
        return 'red'
    if b >= r + 35 and b >= g + 35:
        return 'blue'
    if r > 180 and g > 150 and b < 90:
        return 'yellow'
    if r > 190 and 80 <= g <= 165 and b < 90:
        return 'orange'
    if r > 120 and b > 120 and g < min(r, b) - 25:
        return 'purple'
    if g > 130 and b > 130 and r < min(g, b) - 30:
        return 'cyan'
    return 'other'


def _analyze_visual_blobs(rgb_im) -> tuple[list[dict], dict[str, int], dict[str, int]]:
    """Fast connected-component color & shape detector (~10ms on 240x240 grid)."""
    from collections import deque
    from PIL import Image

    iw, ih = rgb_im.size
    max_dim = 240
    scale = min(1.0, max_dim / float(max(1, iw, ih)))
    sw = max(1, int(round(iw * scale)))
    sh = max(1, int(round(ih * scale)))
    small = rgb_im.resize((sw, sh), Image.Resampling.NEAREST) if scale < 1.0 else rgb_im
    px = small.load()

    # Estimate background color from the 4 corners
    corners = [px[0, 0], px[sw - 1, 0], px[0, sh - 1], px[sw - 1, sh - 1]]
    bg = sorted(corners, key=lambda c: sum(abs(c[i] - o[i]) for o in corners for i in range(3)))[0]

    visited = bytearray(sw * sh)
    blobs: list[dict] = []
    min_pixels = max(4, int(sw * sh * 0.0006))

    for y0 in range(sh):
        for x0 in range(sw):
            idx0 = y0 * sw + x0
            if visited[idx0]:
                continue
            visited[idx0] = 1
            p0 = px[x0, y0]
            if abs(p0[0] - bg[0]) + abs(p0[1] - bg[1]) + abs(p0[2] - bg[2]) <= 36:
                continue
            c0 = _classify_rgb_color(p0[0], p0[1], p0[2])
            q = deque([(x0, y0)])
            cnt = 0
            sr = sg = sb = 0
            min_x = max_x = x0
            min_y = max_y = y0
            while q:
                cx, cy = q.popleft()
                cp = px[cx, cy]
                cnt += 1
                sr += cp[0]
                sg += cp[1]
                sb += cp[2]
                if cx < min_x:
                    min_x = cx
                if cx > max_x:
                    max_x = cx
                if cy < min_y:
                    min_y = cy
                if cy > max_y:
                    max_y = cy
                for ny in range(max(0, cy - 1), min(sh, cy + 2)):
                    row_off = ny * sw
                    for nx in range(max(0, cx - 1), min(sw, cx + 2)):
                        nidx = row_off + nx
                        if visited[nidx]:
                            continue
                        np = px[nx, ny]
                        if abs(np[0] - bg[0]) + abs(np[1] - bg[1]) + abs(np[2] - bg[2]) <= 36:
                            visited[nidx] = 1
                            continue
                        if _classify_rgb_color(np[0], np[1], np[2]) == c0 or (
                            abs(np[0] - p0[0]) + abs(np[1] - p0[1]) + abs(np[2] - p0[2]) <= 75
                        ):
                            visited[nidx] = 1
                            q.append((nx, ny))
            if cnt < min_pixels:
                continue
            bw = max_x - min_x + 1
            bh = max_y - min_y + 1
            box_area = max(1, bw * bh)
            fill_ratio = cnt / float(box_area)
            aspect = bw / float(max(1, bh))
            if 0.66 <= fill_ratio <= 0.87 and 0.72 <= aspect <= 1.38 and min(bw, bh) >= 5:
                shape_hint = 'circle'
            elif fill_ratio > 0.87 and min(bw, bh) >= 5:
                shape_hint = 'rectangle'
            else:
                shape_hint = 'glyph_or_stroke'
            mr, mg, mb = int(round(sr / cnt)), int(round(sg / cnt)), int(round(sb / cnt))
            color_name = _classify_rgb_color(mr, mg, mb)
            ox1 = int(round(min_x / scale))
            oy1 = int(round(min_y / scale))
            ox2 = int(round((max_x + 1) / scale))
            oy2 = int(round((max_y + 1) / scale))
            blobs.append({
                'color': color_name,
                'shape': shape_hint,
                'rgb': [mr, mg, mb],
                'bbox': [ox1, oy1, min(iw, ox2), min(ih, oy2)],
                'center_norm': [
                    int(round(((min_x + max_x + 1) * 500.0) / max(1, sw))),
                    int(round(((min_y + max_y + 1) * 500.0) / max(1, sh))),
                ],
                'fill_ratio': round(fill_ratio, 2),
                'area_pct': round((cnt * 100.0) / max(1, sw * sh), 2),
            })

    blobs.sort(key=lambda b: (b['bbox'][1], b['bbox'][0]))
    color_counts: dict[str, int] = {}
    shape_counts: dict[str, int] = {}
    for b in blobs:
        c_key = b['color']
        s_key = f"{b['color']}_{b['shape']}"
        color_counts[c_key] = color_counts.get(c_key, 0) + 1
        shape_counts[s_key] = shape_counts.get(s_key, 0) + 1
    return blobs[:24], color_counts, shape_counts


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
            return {'ok': False, 'error': f'图片文件不存在: {file_path.name}'}
        try:
            raw_bytes = file_path.read_bytes()
        except Exception as exc:
            return {'ok': False, 'error': f'读取图片文件失败 ({file_path.name}): {exc}'}
        if not raw_bytes:
            return {'ok': False, 'error': f'图片文件为空 (0 bytes): {file_path.name}'}

        import hashlib
        import shutil
        import subprocess
        import tempfile
        from PIL import Image, ImageOps, ImageStat

        try:
            im = Image.open(io.BytesIO(raw_bytes))
            img_fmt = str(im.format or file_path.suffix.lstrip('.').upper() or 'IMAGE')
            img_mode = str(im.mode or 'RGB')
            iw, ih = im.size
            rgb_im = im.convert('RGB')
            stat = ImageStat.Stat(rgb_im)
            mean_rgb = [int(round(v)) for v in (stat.mean[:3] if stat.mean else [0, 0, 0])]
            # Estimate non-blank pixel ratio relative to top-left background color
            bg = rgb_im.getpixel((0, 0))
            sample_im = rgb_im.resize((min(120, max(1, iw)), min(120, max(1, ih))))
            px_list = list(sample_im.getdata())
            diff_cnt = sum(1 for p in px_list if abs(p[0] - bg[0]) + abs(p[1] - bg[1]) + abs(p[2] - bg[2]) > 24)
            non_blank_pct = round((diff_cnt * 100.0) / max(1, len(px_list)), 1)
            visual_blobs, color_counts, colored_shapes_summary = _analyze_visual_blobs(rgb_im)
        except Exception as exc:
            return {'ok': False, 'error': f'无效或损坏的图片文件 ({file_path.name}): {exc}'}

        ocr_text = ''
        tess_bin = shutil.which('tesseract')
        if tess_bin:
            tmp_ocr_path = None
            try:
                ocr_im = rgb_im
                if max(iw, ih) < 900:
                    ocr_im = ocr_im.resize((max(1, iw * 2), max(1, ih * 2)), Image.Resampling.LANCZOS)
                ocr_im = ImageOps.autocontrast(ocr_im)
                with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as tf:
                    tmp_ocr_path = Path(tf.name)
                ocr_im.save(tmp_ocr_path, format='PNG')
                for lang_arg in ('eng+chi_sim', 'eng'):
                    proc = subprocess.run(
                        [tess_bin, str(tmp_ocr_path), 'stdout', '--psm', '6', '-l', lang_arg],
                        capture_output=True,
                        text=True,
                        timeout=6,
                    )
                    if proc.returncode == 0 and proc.stdout.strip():
                        ocr_text = proc.stdout.strip()
                        break
            except Exception:
                pass
            finally:
                if tmp_ocr_path is not None:
                    try:
                        tmp_ocr_path.unlink(missing_ok=True)
                    except Exception:
                        pass

        sha16 = hashlib.sha256(raw_bytes).hexdigest()[:16]
        ext = file_path.suffix.lower()
        mime = {
            '.png': 'image/png',
            '.webp': 'image/webp',
            '.gif': 'image/gif',
            '.bmp': 'image/bmp',
        }.get(ext, 'image/jpeg')
        b64 = base64.b64encode(raw_bytes).decode('ascii')
        if not b64:
            return {'ok': False, 'error': f'图片 Base64 编码失败 ({file_path.name})'}
        cap_parts = [f'【图片文件 `{file_path.name}` ({iw}x{ih} {img_fmt}, sha256={sha16}) 实时画面】']
        if ocr_text:
            cap_parts.append(f'（OCR 文字：{ocr_text[:400]}）')
        if colored_shapes_summary:
            cap_parts.append(f'（像素连通色块统计：{colored_shapes_summary}）')
        if prompt:
            cap_parts.append(f'（提示：{prompt}）')
        return {
            'ok': True,
            'image_file': file_path.name,
            'size_bytes': len(raw_bytes),
            'width': iw,
            'height': ih,
            'format': img_fmt,
            'mode': img_mode,
            'sha256': sha16,
            'mean_rgb': mean_rgb,
            'non_blank_pct': non_blank_pct,
            'ocr_text': ocr_text,
            'color_counts': color_counts,
            'colored_shapes_summary': colored_shapes_summary,
            'visual_blobs': visual_blobs,
            'multimodal_b64_len': len(b64),
            '_keep_in_turn': True,
            '_multimodal_b64': b64,
            '_multimodal_mime': mime,
            '_multimodal_caption': ''.join(cap_parts),
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
