from __future__ import annotations

import pytest
from PIL import Image, ImageDraw

from brain import Brain
from core.vision_engine import VisionEngine
from harness import Harness
from storage import Store
from vault_manager import Vault


@pytest.mark.asyncio
async def test_round7_strict_acceptance_suite(tmp_path):
    store = Store(tmp_path / 'store')
    vault = Vault(tmp_path / 'vault')
    harness = Harness(tmp_path / 'workspace', store=store, vault=vault)

    async def _noop_pub(ev):
        pass

    class _NoopEsc:
        async def track(self, m):
            pass

    brain = Brain(store, vault, harness, _noop_pub, _NoopEsc())
    vision_eng = VisionEngine(store)
    harness.vision_reader = vision_eng.read_image_file_with_vision

    # 1. Adversarial Vision: text '5' + 3 green circles -> colored_shapes_summary must report green_circle=3
    adv_p = harness.root / 'vision-adversarial.png'
    im = Image.new('RGB', (400, 300), (255, 255, 255))
    dr = ImageDraw.Draw(im)
    dr.rectangle((15, 20, 45, 50), fill=(5, 5, 5))
    for cx in (90, 210, 330):
        dr.ellipse((cx - 30, 120, cx + 30, 180), fill=(20, 140, 40))
    im.save(adv_p, format='PNG')
    v_res = await harness.execute('read_file', {'path': 'vision-adversarial.png'})
    assert v_res.get('ok') is True
    assert v_res.get('color_counts', {}).get('green') == 3
    assert v_res.get('colored_shapes_summary', {}).get('green_circle') == 3
    assert v_res.get('multimodal_b64_len', 0) > 100
    assert bool(v_res.get('_multimodal_b64')) is True

    # Corrupt image -> explicit ok: False
    bad_p = harness.root / 'corrupt.png'
    bad_p.write_bytes(b'not_an_image')
    v_bad = await harness.execute('read_file', {'path': 'corrupt.png'})
    assert v_bad.get('ok') is False
    assert 'error' in v_bad

    # 2. Same-turn multi-image send_image: 3 distinct images must emit 3 ordered bubbles without overwriting
    brain.pending_sticker_var.set('')
    brain.pending_stickers_var.set(())
    for idx, col in enumerate([(220, 20, 20), (20, 180, 40), (30, 80, 220)], 1):
        ip = harness.root / f'img_{idx}.png'
        Image.new('RGB', (120, 120), col).save(ip, format='PNG')
        sr = await harness.execute('send_image', {'path': f'img_{idx}.png'})
        assert sr.get('ok') is True
        assert len(sr.get('output_md5', '')) == 32
        i_idx, i_tot, _ = brain._queue_turn_image(sr['chat_image_url'])
        assert i_idx == idx
        assert i_tot == idx
    before_n = len(store.state['messages'])
    await brain.emit('Three images test', kind='chat', notify=False)
    emitted = store.state['messages'][before_n:]
    assert len(emitted) == 3
    assert emitted[0]['image_index'] == 1 and emitted[0]['is_sub_turn'] is True
    assert emitted[1]['image_index'] == 2 and emitted[1]['is_sub_turn'] is True
    assert emitted[2]['image_index'] == 3 and emitted[2]['text'] == 'Three images test'
    assert len(set(m['sticker'] for m in emitted)) == 3

    # 3. move_file to .trash/ must not pollute INDEX.md
    await harness.execute('write_file', {'path': 'memory_vault/notes/temp_probe.md', 'content': '# Probe'})
    await harness.execute('move_file', {'src': 'memory_vault/notes/temp_probe.md', 'dst': 'memory_vault/.trash/temp_probe.md'})
    idx_md = (vault.root / 'INDEX.md').read_text('utf-8')
    assert '.trash' not in idx_md
    assert 'temp_probe' not in idx_md

    # 4. grep_search: file_path alias & binary null-byte exclusion
    bin_f = harness.root / 'raw_binary_blob'
    bin_f.write_bytes(b'\x00\x01\x02 SecretBinaryToken \x00\xff')
    g_bin = await harness.execute('grep_search', {'query': 'SecretBinaryToken'})
    assert g_bin.get('hits') == []
    await harness.execute('write_file', {'path': 'memory_vault/notes/target_doc.md', 'content': 'UniqueTargetWord'})
    await harness.execute('write_file', {'path': 'memory_vault/notes/other_doc.md', 'content': 'UniqueTargetWord'})
    g_scoped = await harness.execute('grep_search', {'query': 'UniqueTargetWord', 'file_path': 'memory_vault/notes/target_doc.md'})
    assert g_scoped.get('files_scanned') == 1
    assert len(g_scoped.get('hits', [])) == 1

    # 5. read_file out-of-bounds start_line & explicit ok:false on .txt write in memory_vault
    oob = await harness.execute('read_file', {'path': 'memory_vault/notes/target_doc.md', 'start_line': 500})
    assert oob.get('ok') is False
    assert '超出文件总行数' in oob.get('error', '')
    txt_wr = await harness.execute('write_file', {'path': 'memory_vault/notes/plain_probe.txt', 'content': 'Plain text note in vault\n'})
    assert txt_wr.get('ok') is False
    assert '仅接收 .md' in txt_wr.get('error', '')

    # 6. custom_tool.create: all 4 run() signatures
    for idx_s, code_s in enumerate([
        'def run(path):\n    return {"v": path}',
        'def run(args):\n    return {"v": args["path"]}',
        'def run(args, context):\n    return {"v": args["path"], "c": bool(context)}',
        'def run(*args):\n    return {"v": args[0]["path"]}',
    ]):
        tname = f'custom_sig_{idx_s}'
        cr = await harness.execute('custom_tool', {'action': 'create', 'name': tname, 'code': code_s})
        assert cr.get('ok') is True
        er = await harness.execute(tname, {'path': 'hello.md'})
        assert er.get('ok') is True
        assert er.get('v') == 'hello.md'

    # 7. write_file(append=True) adjacent duplicate line deduplication
    await harness.execute('write_file', {'path': 'memory_vault/notes/dedup.md', 'content': '# Title\n- Duplicate long line content here\n'})
    wr = await harness.execute('write_file', {'path': 'memory_vault/notes/dedup.md', 'content': '- Duplicate long line content here\n- Next line\n', 'append': True})
    assert wr.get('deduplicated_adjacent_lines') == 1
    lines = (vault.root / 'notes/dedup.md').read_text('utf-8').splitlines()
    assert lines.count('- Duplicate long line content here') == 1
