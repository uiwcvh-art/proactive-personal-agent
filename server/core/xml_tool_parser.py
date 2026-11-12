"""Universal Inline XML / DSML / Python-style Tool Call Parser & Proactive Silence Filter."""
from __future__ import annotations
import ast, json, re, secrets
from typing import Any, Dict, List, Tuple

KNOWN_XML_TOOLS = (
    'cloud_desktop', 'phone_workspace', 'dsh_workspace', 'read_file', 'write_file', 'edit_file',
    'delete_file', 'move_file', 'send_image', 'list_dir', 'glob_files', 'grep_search',
    'run_bash', 'run_command', 'bg_task', 'watchdog_task', 'conversation_search', 'ctx_search',
    'context_search', 'system_status', 'present_choices', 'memory_pin', 'blackboard',
    'custom_tool', 'phone_macro', 'send_message', 'message_phone_bot', 'manage_bot',
    'list_models', 'schedule_task', 'memory_search', 'notify_user', 'stay_silent', 'do_not_disturb',
    'emergency_alarm', 'web_fetch', 'web_search', 'integration_hub',
)
_KNOWN_TOOLS_ALT = '|'.join(KNOWN_XML_TOOLS)
_STRING_ONLY_PARAMS = {
    'text', 'content', 'old', 'new', 'path', 'url', 'uri', 'query', 'goal', 'target', 'ref', 'app',
    'package', 'command', 'script', 'code', 'key', 'name', 'title', 'body', 'reason', 'prompt',
    'at_time', 'daily_time', 'weekdays', 'task_id', 'bot_id', 'to_bot_id', 'ctx_id', 'pin_id',
    'label', 'direction', 'engine', 'source',
}
_SILENT_PREFIX_RX = re.compile(
    r'^[\s(（\[【]*(?:静默|SILENT_OK|SILENT|NO_PUSH|SKIP|不适用|无需提醒|不推送|暂不打扰|不打扰)', re.IGNORECASE
)
_SILENT_EXACT_RX = re.compile(
    r'^[\s`"\'（(\[【]*(?:SILENT_OK|SILENT|NO_PUSH|SKIP|NONE)[\s`"\'）)\]】。！!.,]*$', re.IGNORECASE
)


def is_silent_proactive_output(text: str) -> bool:
    """Detect whether a proactive / scheduled / curiosity output is a silent no-op declaration."""
    s = strip_inline_tool_markup(text or '').strip()
    if not s or _SILENT_EXACT_RX.match(s):
        return True
    if len(s) <= 120 and '\n\n' not in s and _SILENT_PREFIX_RX.match(s):
        return True
    if len(s) <= 120 and s[0] in '(（[【' and s[-1] in ')）]】':
        if any(kw in s for kw in ('静默', '不打扰', '无需推送', '不推送', '无需提醒', '暂不打扰', 'SILENT', 'NO_PUSH', 'SKIP')):
            return True
    return False


def coerce_xml_param(p_name: str, raw_val: str, is_str_flag: str = '') -> Any:
    val = (raw_val or '').strip()
    if is_str_flag == 'true' or (p_name in _STRING_ONLY_PARAMS and not val.startswith(('[', '{'))):
        return val
    if is_str_flag == 'false' or val.startswith(('[', '{')) or val in ('true', 'false', 'null') or re.fullmatch(r'-?\d+(?:\.\d+)?', val):
        try:
            return json.loads(val)
        except Exception:
            pass
    return val


def _make_call_obj(fn_name: str, args_dict: Dict[str, Any], idx: int) -> Dict[str, Any]:
    return {
        'id': f'call_xml_{idx}_{secrets.token_hex(4)}',
        'type': 'function',
        'function': {'name': fn_name.strip(), 'arguments': json.dumps(args_dict, ensure_ascii=False)},
    }


def _parse_parameter_tags(body: str) -> Dict[str, Any]:
    args: Dict[str, Any] = {}
    # Match <｜DSML｜parameter name="k">v</｜DSML｜parameter>, <parameter=k>v</parameter>, <parameter name="k">v</parameter>, <param name="k">v</param>
    param_rx = re.compile(
        r'<[|｜\s]*(?:DSML[|｜\s]*)?(?:parameter|param)(?:\s+name=\s*["\']?([^"\'>\s]+)["\']?|=\s*["\']?([^"\'>\s]+)["\']?)(?:\s+string=["\']([^"\']+)["\'])?[^>]*>([\s\S]*?)</[|｜\s]*(?:DSML[|｜\s]*)?(?:parameter|param)\s*>',
        re.IGNORECASE,
    )
    for m in param_rx.finditer(body):
        k = (m.group(1) or m.group(2) or '').strip()
        s_flag = (m.group(3) or '').strip().lower()
        v = (m.group(4) or '').strip()
        if k:
            args[k] = coerce_xml_param(k, v, s_flag)
    if not args:
        for m_child in re.finditer(r'<([a-zA-Z_][a-zA-Z0-9_]*)[^>]*>([\s\S]*?)</\s*\1\s*>', body):
            k = m_child.group(1).strip()
            if k.lower() not in ('function', 'parameter', 'param', 'invoke', 'tool_call', 'call'):
                args[k] = coerce_xml_param(k, (m_child.group(2) or '').strip())
    return args


def _parse_json_or_py_call(inner: str) -> List[Tuple[str, Dict[str, Any]]]:
    s = (inner or '').strip()
    if not s:
        return []
    if '<function' in s.lower() or 'invoke' in s.lower():
        out = []
        for sc in extract_inline_tool_calls(s):
            fn = sc.get('function') or {}
            try:
                out.append((fn['name'], json.loads(fn.get('arguments') or '{}')))
            except Exception:
                pass
        if out:
            return out
    for cand in (s, s.strip('` \n')):
        if cand.startswith(('{', '[')):
            try:
                parsed = json.loads(cand)
                items = parsed if isinstance(parsed, list) else [parsed]
                res: List[Tuple[str, Dict[str, Any]]] = []
                for it in items:
                    if not isinstance(it, dict):
                        continue
                    fn_part = it.get('function') if isinstance(it.get('function'), dict) else it
                    name = str(fn_part.get('name') or fn_part.get('tool') or fn_part.get('function') or '').strip()
                    raw_a = fn_part.get('arguments') if 'arguments' in fn_part else fn_part.get('parameters', fn_part.get('args', {}))
                    if isinstance(raw_a, str):
                        try: raw_a = json.loads(raw_a)
                        except Exception: raw_a = {}
                    if not isinstance(raw_a, dict):
                        raw_a = {k: v for k, v in fn_part.items() if k not in ('name', 'tool', 'function', 'type', 'id')}
                    if name:
                        res.append((name, raw_a))
                if res:
                    return res
            except Exception:
                pass
    m_py = re.match(r'^([a-zA-Z_][a-zA-Z0-9_]*)\s*\(([\s\S]*)\)$', s)
    if m_py:
        fn_name = m_py.group(1).strip()
        try:
            tree = ast.parse(s, mode='eval')
            if isinstance(tree.body, ast.Call):
                args_d: Dict[str, Any] = {kw.arg: ast.literal_eval(kw.value) for kw in tree.body.keywords if kw.arg}
                if not args_d and tree.body.args:
                    first_val = ast.literal_eval(tree.body.args[0])
                    args_d = first_val if isinstance(first_val, dict) else ({'command' if fn_name == 'run_bash' else 'target': first_val} if isinstance(first_val, str) else {})
                return [(fn_name, args_d)]
        except Exception:
            args_d = {km.group(1): coerce_xml_param(km.group(1), km.group(2) if km.group(2) is not None else (km.group(3) if km.group(3) is not None else (km.group(4) or ''))) for km in re.finditer(r'([a-zA-Z_][a-zA-Z0-9_]*)\s*=\s*(?:"([^"]*)"|\'([^\']*)\'|([^\s,)]+))', m_py.group(2))}
            return [(fn_name, args_d)]
    return []


def extract_inline_tool_calls(text: str) -> List[Dict[str, Any]]:
    """Extract all 5 inline tool call formats into standard OpenAI tool_calls list."""
    if not text or '<' not in text:
        return []
    calls: List[Dict[str, Any]] = []
    consumed_spans: List[Tuple[int, int]] = []
    _overlaps = lambda s, e: any(not (e <= cs or s >= ce) for cs, ce in consumed_spans)

    fn_inv_rx = re.compile(r'<[|｜\s]*(?:DSML[|｜\s]*)?(?:invoke\s+name=\s*["\']?([^"\'>\s]+)["\']?|function(?:\s+name=\s*["\']?([^"\'>\s]+)["\']?|=\s*["\']?([^"\'>\s]+)["\']?))[^>]*>([\s\S]*?)</[|｜\s]*(?:DSML[|｜\s]*)?(?:invoke|function)\s*>', re.IGNORECASE)
    for m in fn_inv_rx.finditer(text):
        fn_name, body = (m.group(1) or m.group(2) or m.group(3) or '').strip(), m.group(4) or ''
        if not fn_name:
            continue
        consumed_spans.append((m.start(), m.end()))
        args_d = _parse_parameter_tags(body)
        if not args_d and body.strip().startswith('{'):
            try: args_d = json.loads(body.strip())
            except Exception: pass
        calls.append(_make_call_obj(fn_name, args_d, len(calls)))

    wrapper_rx = re.compile(r'<\s*(functions_tool_call|tool_call|call|functions)\b[^>]*>([\s\S]*?)</\s*\1\s*>', re.IGNORECASE)
    for m in wrapper_rx.finditer(text):
        if _overlaps(m.start(), m.end()):
            continue
        consumed_spans.append((m.start(), m.end()))
        for fn_name, args_d in _parse_json_or_py_call(m.group(2) or ''):
            calls.append(_make_call_obj(fn_name, args_d, len(calls)))

    # 6. Direct known tool tags: <phone_workspace op="..."/> or <run_bash>...</run_bash>
    direct_rx = re.compile(
        rf'(?<!`)<(?P<tname>{_KNOWN_TOOLS_ALT})(?P<attrs>(?:\s+[^>]*?|["\'][^>]*?))?(?:/>\s*|>(?P<body>[\s\S]*?)</\s*(?P=tname)\s*>)',
        re.IGNORECASE,
    )
    for m in direct_rx.finditer(text):
        if _overlaps(m.start(), m.end()):
            continue
        t_name = m.group('tname').strip().lower()
        attrs_s = m.group('attrs') or ''
        body_s = (m.group('body') or '').strip()
        args_d: Dict[str, Any] = {}
        for ma in re.finditer(r'([a-zA-Z_][a-zA-Z0-9_-]*)\s*=\s*(?:"([^"]*)"|\'([^\']*)\'|([^\s"\'=<>`/]+))', attrs_s):
            k = ma.group(1).strip()
            v = ma.group(2) if ma.group(2) is not None else (ma.group(3) if ma.group(3) is not None else (ma.group(4) or ''))
            args_d[k] = coerce_xml_param(k, v)
        if body_s:
            child_args = _parse_parameter_tags(body_s)
            if child_args:
                args_d.update(child_args)
            elif body_s.startswith('{') and body_s.endswith('}'):
                try:
                    args_d.update(json.loads(body_s))
                except Exception:
                    pass
            elif not args_d:
                default_k = 'command' if t_name in ('run_bash', 'run_command') else ('path' if t_name in ('read_file', 'list_dir') else ('url' if t_name == 'web_fetch' else 'query'))
                args_d = {'op': 'shell', 'command': body_s} if t_name == 'phone_workspace' else {default_k: body_s}
        if args_d or t_name in ('list_models', 'system_status'):
            consumed_spans.append((m.start(), m.end()))
            calls.append(_make_call_obj(t_name, args_d, len(calls)))
    return calls


def strip_inline_tool_markup(text: str) -> str:
    """Strip all inline XML/DSML tool blocks, unclosed trailing tool tags, and <think>/<turn_calibration> blocks."""
    if not text:
        return ''
    s = re.sub(r'<(?:think|thought|thinking|turn_calibration|response_calibration)>[\s\S]*?</(?:think|thought|thinking|turn_calibration|response_calibration)>', '', text, flags=re.IGNORECASE)
    s = re.sub(r'<(?:think|thought|thinking|turn_calibration|response_calibration)>[\s\S]*$', '', s, flags=re.IGNORECASE)
    s = re.sub(r'\[turn_calibration\][\s\S]*?\[/turn_calibration\]', '', s, flags=re.IGNORECASE)
    s = re.sub(r'(?<!`)<[|｜\s]*(?:DSML[|｜\s]*)?(?:function_calls|calls|functions_tool_call|functions|tool_call|call|invoke|function)\b[\s\S]*?</[|｜\s]*(?:DSML[|｜\s]*)?(?:function_calls|calls|functions_tool_call|functions|tool_call|call|invoke|function)\s*>', '', s, flags=re.IGNORECASE)
    s = re.sub(r'(?<!`)<[|｜\s]*(?:DSML[|｜\s]*)?(?:function_calls|calls|functions_tool_call|functions|tool_call|call|invoke\s+name=|function(?:=|\s+name=))[\s\S]*$', '', s, flags=re.IGNORECASE)
    s = re.sub(rf'(?<!`)<(?P<tname>{_KNOWN_TOOLS_ALT})(?:\s+[^>]*?|["\'][^>]*?)?(?:/>|>[\s\S]*?</\s*(?P=tname)\s*>)', '', s, flags=re.IGNORECASE)
    s = re.sub(rf'(?<!`)<(?:{_KNOWN_TOOLS_ALT})(?:\s+[^>]*?|["\'][^>]*?)?>[\s\S]*$', '', s, flags=re.IGNORECASE)
    s = re.sub(r'(?<!`)</?[|｜\s]*(?:DSML[|｜\s]*)?(?:function_calls|calls|functions_tool_call|functions|tool_call|call|invoke|function|parameter|param|turn_calibration|response_calibration)\b[^>]*>', '', s, flags=re.IGNORECASE)
    s = re.sub(r'(?<!`)<[|｜]{1,2}(?:DSML[|｜\s]*)?[A-Za-z_]*$', '', s)
    s = re.sub(r'\n*\[系统(?:真实记录|最后修正|修正|注):[^\]]*\]', '', s)
    return re.sub(r'\n{3,}', '\n\n', s).strip()
