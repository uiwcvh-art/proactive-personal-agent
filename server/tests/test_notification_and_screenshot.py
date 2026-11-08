from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.notification_hub import NotificationHub


class DummyStore:
    def __init__(self):
        self.state = {}

    def save(self):
        pass


class DummyVault:
    def __init__(self, root: str | Path):
        self.root = Path(root)

    def append(self, rel_path: str, text: str):
        p = self.root / rel_path
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open('a', encoding='utf-8') as f:
            f.write(text + '\n')


class TestNotificationAndScreenshot(unittest.TestCase):
    def test_notification_convergence_and_filters(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = DummyStore()
            vault = DummyVault(tmp)
            hub = NotificationHub(store, vault)

            # 1. Built-in VPN / speed meter filter + session grouping
            res = hub.ingest_raw_notifications([
                {
                    'package': 'com.follow.clash',
                    'app': 'FlClash',
                    'title': 'FlClash 正在运行',
                    'text': '↑ 12.4 KB/s ↓ 500 KB/s',
                    'ongoing': True,
                },
                {
                    'package': 'com.tencent.mm',
                    'app': '微信',
                    'title': '算法竞赛群',
                    'text': '张三: 今晚八点打比赛',
                },
                {
                    'package': 'com.tencent.mm',
                    'app': '微信',
                    'title': '算法竞赛群',
                    'text': '李四: 收到！链接发群里了',
                },
                {
                    'package': 'com.example.noisyad',
                    'app': '天气桌面插件',
                    'title': '常驻天气提醒',
                    'text': '今日多云，点击领取金币福利',
                },
            ])
            self.assertEqual(res['added'], 2)
            self.assertEqual(res['updated'], 1)
            self.assertEqual(res['filtered'], 1)

            q = hub.query_inbox()
            self.assertEqual(len(q['notifications']), 2)

            # 2. Duplicate ingestion should not re-add identical message
            res_dup = hub.ingest_raw_notifications([
                {
                    'package': 'com.tencent.mm',
                    'app': '微信',
                    'title': '算法竞赛群',
                    'text': '李四: 收到！链接发群里了',
                },
            ])
            self.assertEqual(res_dup['added'], 0)
            self.assertEqual(res_dup['updated'], 0)

            # 3. AI adds custom filter rule for com.example.noisyad -> retroactively purges inbox
            f_res = hub.add_filter(package='com.example.noisyad', app='天气桌面插件', reason='常驻天气广告')
            self.assertTrue(f_res['ok'])
            self.assertEqual(f_res['removed_from_inbox'], 1)

            q2 = hub.query_inbox()
            self.assertEqual(len(q2['notifications']), 1)
            self.assertEqual(q2['notifications'][0]['title'], '算法竞赛群')
            self.assertEqual(len(q2['notifications'][0]['history']), 2)

            # 4. Curiosity digest returns unseen accumulated items once
            digest = hub.build_curiosity_digest(mark_seen=True)
            self.assertIn('算法竞赛群', digest)
            self.assertIn('张三: 今晚八点打比赛', digest)
            self.assertIn('李四: 收到！链接发群里了', digest)
            digest_after = hub.build_curiosity_digest(mark_seen=True)
            self.assertNotIn('🆕 新积攒的未读手机通知', digest_after)

    def test_message_trace_note_formatting(self):
        from brain import Brain
        note = Brain._format_message_trace_note({
            'role': 'assistant',
            'text': '01:20，该收尾了喵。',
            'api_calls': 2,
            'trace': [
                {'tool': 'read_file'},
                {'tool': 'read_file'},
                {'tool': 'read_file'},
            ],
        })
        self.assertIn('共发起 2 次 API 请求', note)
        self.assertIn('执行了 3 个工具', note)
        self.assertEqual(Brain._format_message_trace_note({'role': 'assistant', 'text': 'hi', 'api_calls': 1}), '')

    def test_on_demand_skills_and_syntax_check(self):
        import asyncio
        from main import create_app

        with tempfile.TemporaryDirectory() as tmp:
            app = create_app(Path(tmp), scheduler=False)
            brain = app.state.brain

            # 1. Save a skill and verify it is NOT injected into build_system_prompt (zero idle tokens)
            res_save = asyncio.run(brain._tool_custom_tool({
                'action': 'save_skill',
                'name': 'wechat_pay_flow',
                'description': '微信扫码与付款操作经验',
                'code': '1. 先用 screenshot_to_chat 发二维码\n2. 不要连点支付按钮',
            }))
            self.assertTrue(res_save['ok'])
            self.assertEqual(res_save['saved_skill'], 'wechat_pay_flow')

            sys_prompt = brain.context() + brain.persona('companion')
            self.assertNotIn('先用 screenshot_to_chat 发二维码', sys_prompt)

            # 2. List and open skill on demand
            res_list = asyncio.run(brain._tool_custom_tool({'action': 'list_skills'}))
            self.assertTrue(res_list['ok'])
            self.assertTrue(any(s['name'] == 'wechat_pay_flow' for s in res_list['skills']))

            res_open = asyncio.run(brain._tool_custom_tool({'action': 'open_skill', 'name': 'wechat_pay_flow'}))
            self.assertTrue(res_open['ok'])
            self.assertIn('先用 screenshot_to_chat 发二维码', res_open['content'])

            res_del = asyncio.run(brain._tool_custom_tool({'action': 'delete_skill', 'name': 'wechat_pay_flow'}))
            self.assertTrue(res_del['ok'])

            # 3. Syntax check on custom_tool(action="create")
            bad_code = asyncio.run(brain._tool_custom_tool({
                'action': 'create',
                'name': 'broken_tool',
                'description': 'broken',
                'code': 'def execute(args):\n    return (\n',
            }))
            self.assertIn('error', bad_code)
            self.assertIn('语法', bad_code['error'])

    def test_phone_shell_file_bridge_and_non_gui_flag(self):
        import asyncio
        from main import create_app

        with tempfile.TemporaryDirectory() as tmp:
            app = create_app(Path(tmp), scheduler=False)
            brain = app.state.brain

            dispatched = []

            async def fake_rpc(op, payload, timeout=32.0):
                dispatched.append((op, payload))
                if op == 'shell':
                    return {'ok': True, 'exit_code': 0, 'mode': 'root', 'output': 'Linux localhost'}
                if op == 'device_info':
                    return {'ok': True, 'device_info': {'battery_level': 88, 'charging': True, 'network': 'wifi'}}
                if op == 'pull_file':
                    return {
                        'ok': True,
                        'file': {'id': 'f123', 'name': 'report.pdf', 'url': '/api/files/f123/report.pdf', 'size': 1024},
                    }
                return {'ok': True, 'op': op}

            brain._raw_phone_rpc = fake_rpc
            brain._used_phone_in_turn = False

            # Non-GUI shell command should NOT mark _used_phone_in_turn = True
            res_sh = asyncio.run(brain._tool_phone_workspace({'op': 'shell', 'command': 'uname -a'}))
            self.assertTrue(res_sh['ok'])
            self.assertEqual(res_sh['output'], 'Linux localhost')
            self.assertFalse(brain._used_phone_in_turn)

            # Non-GUI device_info should NOT mark _used_phone_in_turn = True
            res_info = asyncio.run(brain._tool_phone_workspace({'op': 'device_info'}))
            self.assertTrue(res_info['ok'])
            self.assertEqual(res_info['device_info']['battery_level'], 88)
            self.assertFalse(brain._used_phone_in_turn)

            # pull_file with "latest" and copy_to_desktop=False
            res_pull = asyncio.run(brain._tool_phone_workspace({'op': 'pull_file', 'path': 'latest', 'copy_to_desktop': False}))
            self.assertTrue(res_pull['ok'])
            self.assertEqual(res_pull['file']['id'], 'f123')
            self.assertFalse(brain._used_phone_in_turn)

            # GUI op ("tap") SHOULD mark _used_phone_in_turn = True
            asyncio.run(brain._tool_phone_workspace({'op': 'tap', 'x': 100, 'y': 200}))
            self.assertTrue(brain._used_phone_in_turn)

    def test_release_json_compatible_fields(self):
        from fastapi.testclient import TestClient
        from main import create_app

        with tempfile.TemporaryDirectory() as tmp:
            app = create_app(Path(tmp), scheduler=False)
            with TestClient(app) as client:
                r = client.get('/download/release.json')
                self.assertEqual(r.status_code, 200)
                body = r.json()
                self.assertTrue(body.get('versionName'))
                self.assertEqual(body.get('versionName'), body.get('version_name'))
                self.assertTrue(body.get('tag_name', '').startswith('v'))
                self.assertGreater(int(body.get('versionCode') or 0), 50)

    def test_dsml_parser_curiosity_guard_and_metrics(self):
        import json
        from core.llm_client import LLMClient

        raw_dsml = (
            '你在忙什么呢？<｜｜DSML｜｜ calls>\n'
            '<｜｜DSML｜｜ invoke name="present_choices">\n'
            '<｜｜DSML｜｜ parameter name="prompt" string="true">现在在忙什么呀？</｜｜DSML｜｜ parameter>\n'
            '<｜｜DSML｜｜ parameter name="options" string="false">["在工作", "在休息"]</｜｜DSML｜｜ parameter>\n'
            '</｜｜DSML｜｜ invoke>\n'
            '</｜｜DSML｜｜ calls>'
        )
        self.assertEqual(LLMClient.strip_streaming_dsml_tail(raw_dsml), '你在忙什么呢？')
        clean_text, calls = LLMClient.parse_and_strip_dsml_calls(raw_dsml)
        self.assertEqual(clean_text, '你在忙什么呢？')
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]['function']['name'], 'present_choices')
        args = json.loads(calls[0]['function']['arguments'])
        self.assertEqual(args['prompt'], '现在在忙什么呀？')
        self.assertEqual(args['options'], ['在工作', '在休息'])

        # Pseudo-XML self-closing tool call + post-tag hallucination truncation
        raw_pseudo_1 = (
            '备用邮箱收到，余填进去发验证码。\n\n'
            '<cloud_desktop op="type" ref="输入框" text="uiwcvb@outlook.com"/>\n\n'
            '邮箱填好了，验证码已经发出去了。\n[系统最后修正: 实际已填入]'
        )
        self.assertEqual(LLMClient.strip_streaming_dsml_tail(raw_pseudo_1), '备用邮箱收到，余填进去发验证码。')
        clean_p1, calls_p1 = LLMClient.parse_and_strip_dsml_calls(raw_pseudo_1)
        self.assertEqual(clean_p1, '备用邮箱收到，余填进去发验证码。')
        self.assertEqual(len(calls_p1), 1)
        self.assertEqual(calls_p1[0]['function']['name'], 'cloud_desktop')
        self.assertEqual(
            json.loads(calls_p1[0]['function']['arguments']),
            {'op': 'type', 'ref': '输入框', 'text': 'uiwcvb@outlook.com'},
        )

        # Malformed paired block pseudo-XML tool calls (<cloud_desktop">...) + numeric string text preservation
        raw_pseudo_2 = (
            '余这就填进去。\n\n'
            '<cloud_desktop">\n<op>tap</op>\n<nx>500</nx>\n<ny>611</ny>\n</cloud_desktop>\n\n'
            '<cloud_desktop">\n<op>type</op>\n<text>594114</text>\n</cloud_desktop>'
        )
        clean_p2, calls_p2 = LLMClient.parse_and_strip_dsml_calls(raw_pseudo_2)
        self.assertEqual(clean_p2, '余这就填进去。')
        self.assertEqual(len(calls_p2), 2)
        self.assertEqual(json.loads(calls_p2[0]['function']['arguments']), {'op': 'tap', 'nx': 500, 'ny': 611})
        self.assertEqual(json.loads(calls_p2[1]['function']['arguments']), {'op': 'type', 'text': '594114'})

        # Verify llm_text separation prevents multi-step narration poisoning in recent history
        from brain import Brain
        multi_step_msg = {
            'role': 'assistant',
            'text': '第一步打开网页。\n\n第二步点击按钮。\n\n最终结论：已停在验证码输入框，请发验证码。',
            'llm_text': '最终结论：已停在验证码输入框，请发验证码。',
            'api_calls': 3,
            'trace': [{'tool': 'cloud_desktop'}, {'tool': 'cloud_desktop'}],
        }
        self.assertEqual(Brain._get_llm_history_text(multi_step_msg), '最终结论：已停在验证码输入框，请发验证码。')

        import asyncio
        from main import create_app

        with tempfile.TemporaryDirectory() as tmp:
            app = create_app(Path(tmp), scheduler=False)
            brain = app.state.brain
            brain.activity = 'curiosity'
            blocked = asyncio.run(brain._tool_phone_workspace({'op': 'open', 'target': '支付宝'}))
            self.assertIn('error', blocked)
            self.assertIn('禁止操控虚拟屏', blocked['error'])

    def test_prefix_cache_immutability_across_tool_calls_and_240_messages(self):
        import asyncio
        import json
        from main import create_app

        with tempfile.TemporaryDirectory() as tmp:
            app = create_app(Path(tmp), scheduler=False)
            store = app.state.store
            brain = app.state.brain
            store.update_config({'small_brain': {'api_key': 'test-key', 'model': 'deepseek-chat'}})

            # 1. Seed 250 historical messages (> 240 old sliding-window limit)
            for i in range(125):
                store.message('user', f'历史用户消息 #{i}', bot_id='companion')
                store.message('assistant', f'历史助理回复 #{i}', bot_id='companion', llm_text=f'历史助理回复 #{i}')
            first_msg_id = store.state['messages'][0]['id']
            self.assertEqual(len(store.state['messages']), 250)

            # Simulate PC peer online with recent dialogs to verify user message enrichment stays 100% immutable across turns
            brain.dsh_bridge_info['online'] = True
            brain.dsh_bridge_info['last_seen'] = 9999999999.0
            brain.dsh_bridge_info['status'] = {
                'last_3_dialogs': [{'time': '12:00:00', 'user': '你好', 'assistant': '主人好喵'}]
            }

            captured_payloads = []
            captured_choices = []

            async def fake_complete(messages, tools=None, big=False):
                # Record deep copy of messages and _next_tool_choice on every step
                captured_payloads.append(json.loads(json.dumps(messages, ensure_ascii=False)))
                captured_choices.append(getattr(brain, '_next_tool_choice', None))
                brain._next_tool_choice = None
                step_idx = len(captured_payloads)
                if step_idx == 1:
                    # Turn 1 Step 1: Call a tool (memory_pin) without inline text
                    return {
                        'role': 'assistant',
                        'content': '',
                        'tool_calls': [{
                            'id': 'tc_1',
                            'type': 'function',
                            'function': {
                                'name': 'memory_pin',
                                'arguments': json.dumps({'action': 'pin', 'text': '主人喜欢喝冰美式'}, ensure_ascii=False),
                            },
                        }],
                        '_metrics': {'ttft_ms': 100, 'tps': 30.0, 'prompt_tokens': 35000, 'cached_tokens': 34560, 'cache_hit_pct': 99, 'completion_tokens': 25},
                    }
                if step_idx == 2:
                    # Turn 1 Step 2: Model returns empty content and no tool calls -> triggers post-tool summary call (Step 3)
                    return {
                        'role': 'assistant',
                        'content': '',
                        '_metrics': {'ttft_ms': 100, 'tps': 30.0, 'prompt_tokens': 35200, 'cached_tokens': 35008, 'cache_hit_pct': 99, 'completion_tokens': 10},
                    }
                if step_idx == 3:
                    # Turn 1 Step 3 (post-tool summary): Verify tool_choice was NOT mutated to 'none'
                    return {
                        'role': 'assistant',
                        'content': '已经帮主人记住喜欢喝冰美式啦喵～',
                        '_metrics': {'ttft_ms': 120, 'tps': 32.0, 'prompt_tokens': 35300, 'cached_tokens': 35136, 'cache_hit_pct': 99, 'completion_tokens': 18},
                    }
                # Turn 2 Step 1: Normal follow-up chat turn after tool turn
                return {
                    'role': 'assistant',
                    'content': '不客气喵！',
                    '_metrics': {'ttft_ms': 90, 'tps': 35.0, 'prompt_tokens': 35100, 'cached_tokens': 34944, 'cache_hit_pct': 99, 'completion_tokens': 8},
                }

            brain.complete = fake_complete

            # Execute Turn 1 (multi-step tool call + post-tool summary)
            asyncio.run(brain.chat('帮我钉住：我喜欢喝冰美式', bot_id='companion'))
            self.assertEqual(len(captured_payloads), 3)
            # Post-tool summary step MUST NOT set _next_tool_choice = 'none'
            self.assertIsNone(captured_choices[2])

            # Change PC status before Turn 2 to prove Turn 1's user message in history does NOT mutate on Turn 2
            brain.dsh_bridge_info['online'] = False
            brain.dsh_bridge_info['status'] = {}

            # Execute Turn 2 (follow-up chat right after tool execution)
            asyncio.run(brain.chat('谢谢白珂', bot_id='companion'))
            self.assertEqual(len(captured_payloads), 4)

            # Verify oldest message was NOT evicted (no 1-by-1 sliding window shift!)
            self.assertEqual(store.state['messages'][0]['id'], first_msg_id)

            # Verify 100% byte-for-byte prefix identity between Turn 1 Step 1 (`captured_payloads[0]`)
            # and the corresponding prefix of Turn 2 Step 1 (`captured_payloads[3]`)!
            turn1_msgs = captured_payloads[0]
            turn2_msgs = captured_payloads[3]
            self.assertEqual(len(turn2_msgs), len(turn1_msgs) + 2)
            for idx, m1 in enumerate(turn1_msgs):
                self.assertEqual(
                    m1,
                    turn2_msgs[idx],
                    f'Prefix cache broken at message index {idx}!',
                )

            # Verify fallback usage estimation when fallback_tokens < prev_pt (after transient tool loop outputs drop)
            brain.last_prompt_tokens['companion'] = 48633
            ok_u, pt, ct, _ = brain.llm._record_usage('companion', None, is_foreground=True, fallback_tokens=32700)
            self.assertTrue(ok_u)
            self.assertEqual(pt, 32700)
            self.assertGreater(ct, 32000)

    def test_dual_passthrough_vision_and_no_tap_blocker(self):
        """Verify direct multimodal image_url passthrough, custom-drawn WeChat (node_count=0) dual passthrough, and zero same-spot tap blockers."""
        import asyncio
        import base64
        import io
        from PIL import Image
        from main import create_app

        with tempfile.TemporaryDirectory() as tmp:
            app = create_app(Path(tmp))
            brain = app.state.brain

            # Create a dummy JPEG screenshot
            img = Image.new('RGB', (720, 1600), color=(40, 120, 200))
            buf = io.BytesIO()
            img.save(buf, format='JPEG')
            b64_frame = base64.b64encode(buf.getvalue()).decode('ascii')

            uploads_dir = brain.harness.root / 'uploads'
            uploads_dir.mkdir(parents=True, exist_ok=True)
            (uploads_dir / 'test_upload.jpg').write_bytes(buf.getvalue())

            # 1. Verify user uploaded image is converted directly to multimodal image_url list (no secondary LLM OCR call)
            mm_content = asyncio.run(
                brain._enrich_user_message_with_vision('请看这张图 uploads/test_upload.jpg', 'deepseek-chat', 'companion', '白珂')
            )
            self.assertIsInstance(mm_content, list)
            self.assertEqual(mm_content[0]['type'], 'text')
            self.assertEqual(mm_content[1]['type'], 'image_url')
            self.assertTrue(mm_content[1]['image_url']['url'].startswith('data:image/jpeg;base64,'))

            # 2. Verify custom-drawn WeChat (node_count=0) returns both ui_tree and _multimodal_b64, and allows repeated taps at same spot
            async def fake_phone_rpc(op, args, timeout=32.0):
                return {
                    'ok': True,
                    'op': op,
                    'package': 'com.tencent.mm',
                    'node_count': 0,
                    'width': 1080,
                    'height': 2412,
                    'ui_changed': True,
                    'change_summary': '✅ 界面已响应: 包名 com.tencent.mm -> com.tencent.mm, 节点数 0 -> 0（自绘画面帧已更新）',
                    'ui_tree': '当前界面共有 0 个可交互节点（包名: com.tencent.mm）：',
                    'image': f'data:image/jpeg;base64,{b64_frame}',
                }

            brain._raw_phone_rpc = fake_phone_rpc
            for _ in range(5):
                res = asyncio.run(brain._tool_phone_workspace({'op': 'tap', 'nx': 500, 'ny': 500}))
                self.assertTrue(res.get('ok'))
                self.assertTrue(res.get('vision_direct'))
                self.assertTrue(bool(res.get('_multimodal_b64')))
                self.assertNotIn('防死循环拦截', str(res.get('error') or ''))


if __name__ == '__main__':
    unittest.main()
