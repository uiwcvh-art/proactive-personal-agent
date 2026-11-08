# 全量代码审计 Bug 总账本（10 名专职子代理全量落盘汇总）
> 本文件由 10 份独立子代理完整报告（`audit_1` ~ `audit_10`）100% 无删减合并生成，作为后续逐条修复与验收的唯一持久化依据。



---
# 【分卷】Auditor-1 (服务端核心 brain.py / harness.py / main.py) (`audit_1_server_core.md`)

# 服务端核心大脑、工具契约与路由层全量审计报告 (Auditor-1: `audit_1_server_core.md`)

> **审计员**: Auditor-1 (`2170a651-3a9a-4112-b4e6-a9ad030fdc0d`)
> **审计范围**:
> 1. `D:\bubuki\proactive-personal-agent\server\harness.py` (1–833 行，全量审阅)
> 2. `D:\bubuki\proactive-personal-agent\server\brain.py` (1–5815 行，全量审阅)
> 3. `D:\bubuki\proactive-personal-agent\server\main.py` (1–1245 行，全量审阅)
> 4. `D:\bubuki\proactive-personal-agent\server\storage.py` (1–659 行，全量审阅)
> 5. `D:\bubuki\proactive-personal-agent\server\vault_manager.py` (1–429 行，全量审阅)
> 6. `D:\bubuki\proactive-personal-agent\server\assistants.py` (1–265 行，全量审阅)
> 7. `D:\bubuki\proactive-personal-agent\server\bot_identity.py` (1–226 行，全量审阅)
> 8. `D:\bubuki\proactive-personal-agent\server\escalation.py` (1–83 行，全量审阅)
> 9. `D:\bubuki\proactive-personal-agent\server\security.py` (1–61 行，全量审阅)
> 10. `D:\bubuki\proactive-personal-agent\server\sandbox.py` (1–29 行，全量审阅)
> 11. 关联服务端核心子模块：`server/core/memory_engine.py`、`server/core/context_search.py`、`server/core/notification_hub.py`、`server/transport/extension_registry.py`
> **跨代理联合对账**:
> - 已与 **Auditor-2 (PC插件与云桌面)** (`6732ae86-806e-4484-a681-d1103785d320`) 完成双向接口契约逐项核验
> - 已与 **Auditor-3 (安卓虚拟屏与PhoneHarness)** (`9b3933de-9273-4004-8134-543cb488bdc0`) 发起契约对账
> - 已与 **Auditor-4 (安卓主界面与网络同步)** (`4728e652-4126-4e79-aef9-0f954c8e0016`) 完成双向状态同步契约逐项核验

---

## 目录与严重程度汇总

- **第一部分：工具 Schema 与实际执行器/路由层的契约矛盾 & 跨端调用崩溃 (`SRV-001` ~ `SRV-011`)**
- **第二部分：PC 端与云端工作区路由分裂 (Split-Brain) (`SRV-012` ~ `SRV-017`)**
- **第三部分：手机工具 `_exec_phone_workspace` 与虚拟屏生命周期控制缺陷 (`SRV-018` ~ `SRV-024`)**
- **第四部分：并行工具执行 (`can_parallel`)、流式输出、打断 (`stop_immediately`)、多 Bot 互联 (`bot_interconnect`) 与存储并发隐患 (`SRV-025` ~ `SRV-035`)**

---

## 第一部分：工具 Schema 与实际执行器/路由层的契约矛盾 & 跨端调用崩溃

---

### [SRV-001] [CRITICAL] `brain.py:_sync_topology_delta_if_changed` 调用不存在的 `self.memory_engine.record_delta()` 导致对话首轮或拓扑变更时直接崩溃 (`AttributeError`)
- **分类**: 方法签名/契约不匹配 (`Contract Mismatch / Runtime Crash`)
- **精确文件与行号**:
  - `D:\bubuki\proactive-personal-agent\server\brain.py:391-396`
  - `D:\bubuki\proactive-personal-agent\server\brain.py:4726`
  - `D:\bubuki\proactive-personal-agent\server\core\memory_engine.py:83`
- **根因与代码证据**:
  在 `brain.py:4726`（`Brain._chat` 主流程进入 LLM 循环前）无条件调用了 `self._sync_topology_delta_if_changed()`。
  在 `brain.py:391-396` 中：
  ```python
  self.memory_engine.record_delta(
      kind='topology_sync',
      summary=summary,
      path='multi_device_topology',
      source_node='system',
  )
  ```
  然而在 `server/core/memory_engine.py:83` 中，`MemoryEngine` 类**根本没有 `record_delta` 方法**，实际定义的方法名与参数签名为：
  ```python
  def record_memory_delta(self, summary: str, path: str = '', source: str = 'all', after_msg_id: str | None = None) -> dict:
  ```
  不仅方法名写错（`record_delta` vs `record_memory_delta`），而且 `record_memory_delta` 也不接受 `kind` 和 `source_node` 关键字参数。
- **具体后果**:
  虽然 `brain.py:397` 有 `except Exception: pass` 吞掉了异常，导致不会炸毁整个 `_chat()` 协程，但**多设备拓扑状态变更（手机/PC 上下线、DSH 会话变化）永远无法写入 `memory_deltas`**，且每次拓扑变化都会在后台静默抛出 `AttributeError: 'MemoryEngine' object has no attribute 'record_delta'`。
- **修复方案**:
  将 `brain.py:391-396` 改为调用 `self.memory_engine.record_memory_delta(summary=summary, path='multi_device_topology', source='all')`（或调用 `self._record_memory_delta(summary, path='multi_device_topology', source='all')`）。

---

### [SRV-002] [CRITICAL] PC 端 `edit_file` 路由 (`pc_fs`) 参数名 100% 不匹配（`old`/`new` vs `old_text`/`new_text`）且存在 JS `$` 替换损坏文件漏洞
- **分类**: 跨层工具契约不匹配 (`Cross-Layer Contract Mismatch — Server <-> PC Plugin`)
- **精确文件与行号**:
  - `D:\bubuki\proactive-personal-agent\server\harness.py:58-63`
  - `D:\bubuki\proactive-personal-agent\server\brain.py:4881-4882` (并行分支), `4954-4955` (串行分支)
  - `D:\dsh-baike-plugin\index.js:718-724` (与 Auditor-2 联合核实)
- **根因与代码证据**:
  1. `harness.py:58-63` 定义 `edit_file` 工具 Schema 为：
     ```python
     tool('edit_file', '精确替换文件中的一段文本...', {
         'path': STR, 'old': STR, 'new': STR
     }, ['path', 'old', 'new'])
     ```
     由于 `tool()` (`harness.py:35`) 设置了 `'additionalProperties': False`，模型只能传入 `path`、`old`、`new`。
  2. 当 `src in ('pc', 'pc2')` 时，`brain.py:4881-4882` 与 `4954-4955` 原样转发 `args`：
     ```python
     res = await self._raw_dsh_rpc('pc_fs', {'fs_op': name, **args}, timeout=25.0)
     ```
  3. 经与 Auditor-2 核对，`D:\dsh-baike-plugin\index.js:718-724` 的 `runLocalPcFsOperation` 实现为：
     ```javascript
     const oldText = String(args.old_text ?? args.target ?? "");
     const newText = String(args.new_text ?? args.replacement ?? "");
     if (!oldText || !raw.includes(oldText)) {
       return { ok: false, error: `未在文件中找到待替换内容 (${targetPath})`, path: targetPath, hostname: HOSTNAME };
     }
     const updated = raw.replace(oldText, newText);
     ```
     `index.js` 只读取 `args.old_text ?? args.target` 和 `args.new_text ?? args.replacement`，完全忽略了 `args.old` 和 `args.new`！
- **具体后果**:
  1. 在 PC 会话（`pc` / `pc2`）中，Agent 调用 `edit_file` 修改任何 Windows 本地文件时，`oldText` 恒为空字符串 `""`，**100% 必现报错 `未在文件中找到待替换内容`**！
  2. 即使参数名修复，`raw.replace(oldText, newText)` 在 JS 中若第 2 个参数为字符串，会解析 `$&`、`$$`、`$` `、`$' ` 等特殊替换模式，当编辑含 `$` 的代码（如 Bash/PHP/正则/模板字符串）时会静默篡改代码内容。
- **修复方案**:
  1. 在 `brain.py` 调用 `_raw_dsh_rpc('pc_fs', ...)` 时同时兼容映射 `'old_text': args.get('old')` 和 `'new_text': args.get('new')`；
  2. 在 `D:\dsh-baike-plugin\index.js:718-724` 中支持 `args.old` / `args.new`，并将 `raw.replace(oldText, newText)` 改为 `raw.replace(oldText, () => newText)` 以禁用 `$` 特殊替换模式。

---

### [SRV-003] [CRITICAL] PC 端 `write_file(append=True)` 路由 (`pc_fs`) 完全忽略 `append` 参数，直接清空并覆盖原文件（数据丢失）
- **分类**: 跨层工具契约不匹配 / 数据丢失 (`Cross-Layer Contract Mismatch / Data Loss`)
- **精确文件与行号**:
  - `D:\bubuki\proactive-personal-agent\server\harness.py:52-57, 520-530`
  - `D:\bubuki\proactive-personal-agent\server\brain.py:4881-4882, 4954-4955`
  - `D:\dsh-baike-plugin\index.js:707-712` (与 Auditor-2 联合核实)
- **根因与代码证据**:
  `harness.py:52-57` 的 `write_file` 定义了 `'append': BOOL` 参数，云端 `harness.py:522-526` 根据 `args.get('append')` 区分追加与覆盖。
  但当路由到 PC 端 `pc_fs` 时，`D:\dsh-baike-plugin\index.js:707-712` 直接执行：
  ```javascript
  fs.writeFileSync(targetPath, content, "utf8");
  ```
  完全忽略了 `args.append`！
- **具体后果**:
  当 Agent 在 PC 会话中调用 `write_file(path=..., content=..., append=True)` 追加日志、配置或笔记时，Windows 本机上的目标文件会被**整文件清空覆盖**，造成严重的数据丢失！
- **修复方案**:
  在 `D:\dsh-baike-plugin\index.js:707-712` 中检查 `if (args.append) { fs.appendFileSync(targetPath, content, "utf8"); } else { fs.writeFileSync(targetPath, content, "utf8"); }`。

---

### [SRV-004] [CRITICAL] `dsh_workspace` 的 `cua_desktop` / `wake_gui` / `new_session` 跨端契约断裂与 RPC 25 秒永久挂起死锁
- **分类**: 工具 Schema 缺失 & 跨端 RPC 死锁 (`Schema Omission & Cross-Layer RPC Deadlock`)
- **精确文件与行号**:
  - `D:\bubuki\proactive-personal-agent\server\harness.py:295-318`
  - `D:\bubuki\proactive-personal-agent\server\brain.py:2769-2770, 2857-2858`
  - `D:\dsh-baike-plugin\index.js:1101-1113, 1465` (与 Auditor-2 联合核实)
- **根因与代码证据**:
  1. **Schema 锁死**: `harness.py:295-318` 定义 `dsh_workspace` 时启用了 `'additionalProperties': False`，其属性列表仅有 `op`, `session_id`, `workspace`, `title`, `message`, `query`, `limit`, `include_blank`, `max_messages`, `max_tools`, `include_reasoning`, `timeout_sec`, `url`, `app`, `command`, `target`。**完全没有定义 `cua_desktop` 所需的 `tool`、`action`、`x`、`y`、`nx`、`ny`、`key`、`text` 等参数**！
  2. **工具名与异常吞没导致 25s 超时死锁**:
     - `brain.py:2857-2858` 在 `op == 'cua_desktop'` 时执行 `return await self._raw_dsh_rpc('cua_desktop', args, timeout=25.0)`。
     - `D:\dsh-baike-plugin\index.js:1108` 执行 `await callCuaTool(args.tool || "cua_Status", args)`。
     - 因为 `harness.py` Schema 不允许传 `tool`（且 `index.js:1465` 自己的插件 Schema 叫 `action` 不叫 `tool`），`args.tool` 恒为 `undefined`，回退为 `"cua_Status"`。
     - 然而 `cua/index.mjs:387-640` 的 `TOOLS` 中**根本不存在 `"cua_Status"`**！`callCuaTool` 直接 `reject(new Error("unknown tool: cua_Status"))`。
     - `index.js:1108` 没有局部 `try/catch`，直接跳入外层 `index.js:1113` 的 `catch {}` 静默吞掉异常，**永远不会向服务端发送 `POST /api/dsh/result`**！服务端 `_raw_dsh_rpc` 必死等 25 秒直到 `TimeoutError`！
  3. **`wake_gui` 与 `new_session` 在 PC 插件端未实现**:
     - `harness.py:299` 和 `brain.py:2829, 2834` 支持 `op="wake_gui"` 和 `op="new_session"` 并通过 `_raw_dsh_rpc` 发给 PC。
     - 但 `D:\dsh-baike-plugin\index.js:1101-1109` 根本没有 `wake_gui` 和 `new_session` 分支，100% 落入 `{ ok: false, error: "不支持的桌面端操作 (wake_gui / new_session)" }`。
  4. **PC Bridge 单线程阻塞心跳**:
     - `index.js:1096-1111` 在同一个 `while` 轮询循环中串行 `await` 执行每个 `inbox` 命令（如 `dispatch_session` 耗时 90s，`pc_shell` 耗时 60s），期间无法上报心跳，导致服务端 `brain.py:2659` (`time.time() - float(self.dsh_Status.get('updated_at') or 0) < 35.0`) 在 35 秒后误判 PC 离线（`pc_online = False`）！
- **具体后果**:
  `dsh_workspace(op="cua_desktop")` 100% 挂死 25 秒超时；`op="wake_gui"` 和 `op="new_session"` 100% 报不支持的操作；长耗时命令执行超过 35 秒会导致服务端误判 PC 掉线。
- **修复方案**:
  1. 在 `harness.py:295-318` 的 `dsh_workspace` Schema 中补齐 `tool`、`action`、`x`、`y`、`nx`、`ny`、`key`、`text` 参数；
  2. 在 `index.js:1101-1113` 中为每个 `cmd` 执行包裹内部 `try/catch` 确保异常时也回传 `/api/dsh/result`，补齐 `wake_gui` / `new_session` 实现，将 `"cua_Status"` 修正为有效工具（如 `"list_windows"`）并兼容 `args.tool || args.action`，且将心跳与命令执行解耦为异步并发。

---

### [SRV-005] [CRITICAL] `brain.py:_exec_cloud_desktop` 调用 `self.computer.client.upload_file(src_file)`，但 `ComputerClient` 根本不存在 `upload_file` 方法
- **分类**: 方法缺失 / 运行时崩溃 (`Missing Method / AttributeError`)
- **精确文件与行号**:
  - `D:\bubuki\proactive-personal-agent\server\brain.py:2038`
  - `D:\bubuki\proactive-personal-agent\server\computer_client.py:1-60` (与 Auditor-2 联合核实)
- **根因与代码证据**:
  在 `brain.py:2024-2050`（`_exec_cloud_desktop` 的 `op == 'sync_file'` 且 `direction == 'to_desktop'` 分支）中：
  ```python
  up_res = await self.computer.client.upload_file(src_file)
  ```
  经与 Auditor-2 核实，`server/computer_client.py` 中的 `ComputerClient` 类仅定义了 `__init__`、`configured`、`call`、`status`、`get_file` 五个方法，**完全没有定义 `upload_file` 方法**！
- **具体后果**:
  虽然 `brain.py:2039` 有 `except Exception:` 回退到 `_ sync_file_to_desktop_smart(src_file, target_path)`，但首选通道 `self.computer.client.upload_file` 100% 抛出 `AttributeError: 'ComputerClient' object has no attribute 'upload_file'`。
- **修复方案**:
  在 `ComputerClient` (`server/computer_client.py`) 中实现 `upload_file(self, src_file: Path, target_path: str = '')`，或直接调用 `self._sync_file_to_desktop_smart(src_file, target_path)`。

---

### [SRV-006] [HIGH] `main.py` 停止盯盘哨兵接口 `/api/watchdogs/{watchdog_id}/stop` 参数名与 `brain.py:_tool_watchdog_task` 不匹配导致无法停止哨兵
- **分类**: 内部接口参数名不匹配 (`Parameter Name Mismatch`)
- **精确文件与行号**:
  - `D:\bubuki\proactive-personal-agent\server\main.py:809-811`
  - `D:\bubuki\proactive-personal-agent\server\brain.py:1160-1169`
- **根因与代码证据**:
  在 `main.py:809-811` 中：
  ```python
  @app.post('/api/watchdogs/{watchdog_id}/stop')
  async def stop_watchdog(watchdog_id: str, _: str = Depends(auth)):
      return await brain._tool_watchdog_task({'action': 'stop', 'watchdog_id': watchdog_id})
  ```
  而在 `brain.py:1160-1169` 中，`_tool_watchdog_task` 的 `action == 'stop'` 分支只读取 `'task_id'` 或 `'name'`：
  ```python
  if action == 'stop':
      tid = str(args.get('task_id') or args.get('name') or '').strip()
      meta = self._watchdogs.get(tid)
      ...
      if not meta:
          return {'error': f'未找到盯盘哨兵 {tid}'}
  ```
  `_tool_watchdog_task` 完全没有读取 `args.get('watchdog_id')`！
- **具体后果**:
  前端或客户端调用 `POST /api/watchdogs/{watchdog_id}/stop` 停止任何盯盘哨兵时，`tid` 永远是空字符串 `""`，**100% 返回 `{'error': '未找到盯盘哨兵 '}`**，哨兵任务无法通过 API 停止！
- **修复方案**:
  1. 在 `main.py:811` 改为 `{'action': 'stop', 'task_id': watchdog_id, 'watchdog_id': watchdog_id}`；
  2. 在 `brain.py:1160` 改为 `tid = str(args.get('task_id') or args.get('watchdog_id') or args.get('name') or '').strip()`。

---

### [SRV-007] [HIGH] `core/context_search.py` 调用不存在的 `vault.search()`，且 `core/memory_engine.py` 调用未初始化的 `self.llm`
- **分类**: 方法/属性缺失 (`Missing Method & Uninitialized Attribute`)
- **精确文件与行号**:
  - `D:\bubuki\proactive-personal-agent\server\core\context_search.py:320-326`
  - `D:\bubuki\proactive-personal-agent\server\vault_manager.py:127-429`
  - `D:\bubuki\proactive-personal-agent\server\core\memory_engine.py:26-40, 266-267`
  - `D:\bubuki\proactive-personal-agent\server\brain.py:4160-4161`
- **根因与代码证据**:
  1. **`vault.search` 不存在**:
     在 `server/core/context_search.py:320-326`（`conversation_search` 函数）中：
     ```python
     vault_hits = []
     if query.strip() and vault is not None:
         try:
             v_matches = vault.search(query.strip(), limit=6)
     ```
     然而在 `server/vault_manager.py` 的 `Vault` 类（127–429 行）中，只有 `read`、`write`、`append`、`patch`、`delete`、`list_notes`、`overview`、`search_lessons` 等方法，**根本没有 `.search()` 方法**！每次调用 `conversation_search(query=...)` 时都会在 `vault.search` 处抛出 `AttributeError: 'Vault' object has no attribute 'search'` 并被 `except Exception: pass` 静默吞掉。
  2. **`self.llm` 未在 `MemoryEngine` 中初始化**:
     在 `server/core/memory_engine.py:266-267` 中：
     ```python
     def estimate_messages_tokens(self, messages: list[dict]) -> int:
         return self.llm._estimate_payload_tokens(messages)
     ```
     以及 `brain.py:4160-4161` 的 `Brain._estimate_messages_tokens` 调用了 `self.memory_engine.estimate_messages_tokens(messages)`。但 `MemoryEngine.__init__` (`core/memory_engine.py:26-40`) **从未接收或设置 `self.llm`**！一旦调用必抛 `AttributeError: 'MemoryEngine' object has no attribute 'llm'`。
- **具体后果**:
  1. `conversation_search` 工具返回的 `archived_vault_summaries` 永远为空列表 `[]`，无法检索归档在 Vault 中的历史摘要；
  2. 任何调用 `estimate_messages_tokens` 的路径都会直接触发 `AttributeError`。
- **修复方案**:
  1. 在 `Vault` (`vault_manager.py`) 中实现通用的 `search(self, query: str, limit: int = 6)` 方法；
  2. 在 `MemoryEngine.__init__` 中传入 `llm` 实例（或使用本模块 `core/memory_engine.py:259` 已有的 `estimate_payload_tokens(messages)` 函数）。

---

### [SRV-008] [HIGH] `brain.py:_tool_send_message` 写入 `work-log.md` 与 `vault_manager.py` 的 `timeline/work_log.md` 路径不一致，导致工作日志丢失并污染 `memory_deltas`
- **分类**: 路径/文件名契约不一致 (`Path Contract Mismatch & State Pollution`)
- **精确文件与行号**:
  - `D:\bubuki\proactive-personal-agent\server\brain.py:4100`
  - `D:\bubuki\proactive-personal-agent\server\vault_manager.py:124, 344, 389, 404`
  - `D:\bubuki\proactive-personal-agent\server\harness.py:344`
- **根因与代码证据**:
  1. 在 `vault_manager.py:124, 389, 404` 和 `harness.py:344` 中，系统统一使用的工作日志路径是 **`timeline/work_log.md`**（带下划线，位于 `timeline/` 子目录下）。
  2. `vault_manager.py:344` 在 `append()` 中特意排除了 `'timeline/work_log.md'` 不触发 `_on_vault_mutation`：
     ```python
     if self._on_mutation and norm != 'timeline/work_log.md':
         try: self._on_mutation('append', norm, content)
     ```
  3. 然而在 `brain.py:4100`（`_tool_send_message` 跨分身协同完成后）却写成了：
     ```python
     self.vault.append('work-log.md', digest_line)
     ```
- **具体后果**:
  1. 跨分身协同（`send_message`）产生的工作日志被写到了孤立文件 `memory_vault/work-log.md`，而系统提示词注入的 `self.vault.read_work_log_tail()` 读取的是 `timeline/work_log.md`，导致**所有跨分身协同记录在工作日志中完全不可见**！
  2. 因为 `'work-log.md' != 'timeline/work_log.md'`，每次 `send_message` 都会误触发 `self._on_mutation('append', 'work-log.md', ...)`，向 `memory_deltas` 灌入冗余变更事件。
- **修复方案**:
  将 `brain.py:4100` 的 `self.vault.append('work-log.md', digest_line)` 改为 `self.vault.append_work_log(digest_line)` 或 `self.vault.append('timeline/work_log.md', digest_line)`。

---

### [SRV-009] [HIGH] `cloud_desktop` 归一化坐标 (`nx`/`ny`)、拖拽 (`drag`) 与滚动 (`scroll`) 同 `computer_models.py` 存在多处契约冲突（触发 HTTP 422 或坐标归零）
- **分类**: 跨模块参数契约冲突 (`Schema & Coordinate Resolution Bug`)
- **精确文件与行号**:
  - `D:\bubuki\proactive-personal-agent\server\brain.py:2078-2103`
  - `D:\bubuki\proactive-personal-agent\server\harness.py:263-294`
  - `D:\bubuki\proactive-personal-agent\server\computer_models.py:22-36` (与 Auditor-2 联合核实)
  - `D:\bubuki\proactive-personal-agent\server\computer_desktop.py:413-441, 1272` (与 Auditor-2 联合核实)
- **根因与代码证据**:
  1. **`scroll` 完全忽略 `nx`/`ny` 且 `direction`/`amount` 越界触发 HTTP 422**:
     - `brain.py:2097-2103`：
       ```python
       elif op == 'scroll':
           res = await self.computer.execute({
               'action': 'scroll',
               'x': int(args.get('x') or 640),
               'y': int(args.get('y') or 400),
               'direction': args.get('direction') or 'down',
               'amount': int(args.get('amount') or 4),
           })
       ```
       此处完全未传递 `nx`、`ny`、`grid`，若模型传入 `nx=800, ny=500` 在特定侧边栏滚动，会被强制重置到屏幕中心 `(640, 400)`。
     - 更严重的是，`harness.py:283` 声明 `'direction': {**STR, 'description': 'scroll 方向：up|down|left|right'}`，但 `computer_models.py:35-36` 定义 `direction: Literal['up', 'down'] = 'down'` 且 `amount: int = Field(3, ge=1, le=12)`。一旦模型传入 `direction='left'|'right'` 或 `amount > 12`，`computer` 服务直接返回 **HTTP 422 ValidationError**！
  2. **`drag` 忽略 `nx`/`ny` 且不支持归一化终点坐标**:
     - `brain.py:2086-2092` 只读取 `x`, `y`, `x2`, `y2`，完全未传 `nx`, `ny`；且 `computer_desktop.py:1272` 的 `drag` 直接使用像素 `x2, y2`，若模型未传像素坐标则直接拖拽到 `(0, 0)`。
  3. **像素坐标范围限制 `lt=800` 与 `0..1000` 归一化坐标冲突**:
     - `computer_models.py:22-25` 限制 `y: int = Field(0, ge=0, lt=800)`。若模型误将 `0..1000` 归一化坐标填入 `y`（如 `y=850`），在进入 `resolve_grid_or_norm_xy` 之前就会被 Pydantic 拦截并抛出 **HTTP 422**！
- **具体后果**:
  云桌面 `scroll` 左右滚动或大步长滚动必报 HTTP 422；在指定 `nx`/`ny` 区域滚动失效；`drag` 使用归一化坐标时拖拽到 `(0, 0)`。
- **修复方案**:
  1. 在 `brain.py:2086-2103` 的 `drag` 和 `scroll` 中将 `nx`、`ny`、`grid` 转换或透传，并对 `amount` 做 `max(1, min(12, int(args.get('amount') or 4)))` 钳制；
  2. 在 `computer_models.py` 中放宽 `x, y, x2, y2` 的上限并支持 `direction: Literal['up', 'down', 'left', 'right']`。

---

### [SRV-010] [MEDIUM] `harness.py` 工具 Schema 严苛锁死 (`additionalProperties: False`) 导致多个底层已实现的参数和动作无法被模型传入
- **分类**: 工具 Schema 与实现不一致 (`Schema vs. Implementation Mismatch`)
- **精确文件与行号**:
  - `D:\bubuki\proactive-personal-agent\server\harness.py:33-35, 175-196, 239-261`
  - `D:\bubuki\proactive-personal-agent\server\brain.py:933-936, 1319, 3124, 3422`
- **根因与代码证据**:
  `harness.py:33-35` 的 `tool()` 辅助函数对所有工具强制设置了 `'additionalProperties': False`。但以下底层实现的参数/操作未写入 `TOOL_DEFS`：
  1. **`schedule_task`**:
     - `brain.py:933-936` 实现了 `if action in ('snooze', 'dnd', 'quiet'): mins = int(args.get('minutes') or args.get('delay_minutes') or 60)`。
     - 但 `harness.py:175-191` 的描述仅列出 `list|add_timer|add_routine|update|remove`，且参数表中没有 `'minutes'`。
  2. **`memory_search`**:
     - `brain.py:1319` 读取 `ctx_id = str(args.get('ctx_id') or '').strip()` 支持按归档块 ID 精确检索，但 `harness.py:192-196` 的 `memory_search` Schema 只有 `'query'`, `'bot_id'`, `'limit'`，没有 `'ctx_id'`，导致模型无法传入 `ctx_id`。
  3. **`phone_workspace` 的 `open_uri` 与 `apps`**:
     - `brain.py:3124` 的错误提示引导模型调用：`phone_workspace(op="open_uri", uri="...")`；`brain.py:3422` 也实现了 `op == 'open_uri'`。
     - 但 `harness.py:244` 的 `phone_workspace` Schema 说明中只写了 `open`（未提 `open_uri` 和 `apps`），虽不至于被 Schema 拦截（`op` 是 `STR`），但会造成提示词与工具文档不一致。
  4. **`DEFAULT_SYSTEM_MD` 引用了未注册的 `integration_hub` 工具**:
     - `harness.py:382` 的系统提示词写道：`调用 integration_hub 执行 email_list/email_read/email_send/calendar_list 等外部通信操作`。但 `TOOL_DEFS` 中根本没有 `integration_hub` 工具！
- **具体后果**:
  模型在尝试调用 `memory_search(ctx_id=...)`、`schedule_task(action="snooze", minutes=30)` 或 `integration_hub` 时会因 Schema 校验失败或工具不存在而报错。
- **修复方案**:
  在 `harness.py` 的 `schedule_task` 中加入 `'minutes': INT`，在 `memory_search` 中加入 `'ctx_id': STR`，并清理或注册 `integration_hub`。

---

### [SRV-011] [MEDIUM] `sandbox.py` 构造全新 `env` 字典覆盖了 `harness.py:run_bash` 注入的 `TZ`、`PYTHONUSERBASE`、`PIP_USER` 与 `~/.py_user_base/bin` `PATH`
- **分类**: 沙箱环境变量丢失 (`Sandbox Environment Stripping Bug`)
- **精确文件与行号**:
  - `D:\bubuki\proactive-personal-agent\server\harness.py:623-633`
  - `D:\bubuki\proactive-personal-agent\server\sandbox.py:22-25`
- **根因与代码证据**:
  在 `harness.py:623-633` 中，`run_bash` 精心准备了环境变量并传给 `sandbox.py`：
  ```python
  env = os.environ.copy()
  env['TZ'] = 'Asia/Shanghai'
  py_user_base = self.root / '.py_user_base'
  py_user_base.mkdir(parents=True, exist_ok=True)
  env['PYTHONUSERBASE'] = str(py_user_base)
  env['PIP_USER'] = '1'
  env['PATH'] = f"{py_user_base / 'bin'}:{env.get('PATH', '/usr/bin:/bin')}"
  proc = await asyncio.create_subprocess_exec(
      sys.executable, str(Path(__file__).with_name('sandbox.py')), str(self.root), command,
      cwd=str(cwd), env=env, ...
  )
  ```
  然而在 `sandbox.py:22-25` 中，它在 `os.execve` 前硬编码重建了一个极简 `env`：
  ```python
  env = {'PATH':'/usr/bin:/bin','HOME':root,'TMPDIR':root,'LANG':'C.UTF-8'}
  for k in ('API_BASE_URL','API_KEY','DEFAULT_MODEL'):
      if os.environ.get(k): env[k] = os.environ[k]
  os.execve('/bin/bash',['bash','--noprofile','--norc','-c',sys.argv[2]], env)
  ```
  `harness.py` 传入的 `TZ`、`PYTHONUSERBASE`、`PIP_USER` 以及 `PATH` 中的 `.py_user_base/bin` 被 `sandbox.py` **100% 丢弃**！
- **具体后果**:
  1. 在 Linux 云端沙箱中执行 `pip install <pkg>` 时，由于 `PIP_USER=1` 和 `PYTHONUSERBASE` 被丢弃，且 Landlock 将 `/usr` 挂载为只读，哪怕安装了脚本可执行文件也不会在 `PATH` 中；
  2. 沙箱内 `date` 或 Python/Node 时间全部退回 UTC，与 `harness.py` 预期的 `Asia/Shanghai` 不一致。
- **修复方案**:
  在 `sandbox.py:23` 的保留环境变量列表中加入 `'TZ'`, `'PYTHONUSERBASE'`, `'PIP_USER'`, `'PATH'`（即 `for k in ('API_BASE_URL','API_KEY','DEFAULT_MODEL','TZ','PYTHONUSERBASE','PIP_USER','PATH'):`）。

---

## 第二部分：PC 端与云端工作区路由分裂 (Split-Brain)

---

### [SRV-012] [CRITICAL] PC 会话 (`src in ('pc', 'pc2')`) 文件系统与 Shell 工具严重脑裂：`run_bash` 在 Windows 本机执行，而 `list_dir`/`glob_files`/`grep_search`/`bg_task` 在云端 Linux 执行，且 `_should_use_pc_fs` 导致同名/相对路径文件跨机错位
- **分类**: 路由脑裂 (`Routing Split-Brain — PC vs. Cloud Linux`)
- **精确文件与行号**:
  - `D:\bubuki\proactive-personal-agent\server\brain.py:343-364` (`_should_use_pc_fs`)
  - `D:\bubuki\proactive-personal-agent\server\brain.py:4744-4753` (PC 系统提示词欺骗)
  - `D:\bubuki\proactive-personal-agent\server\brain.py:4879-4884` (并行工具路由), `4952-4957` (串行工具路由)
  - `D:\dsh-baike-plugin\index.js:644-657` (`resolvePcPath`, 与 Auditor-2 联合核实)
- **根因与代码证据**:
  1. **系统提示词声称在 Windows 本机执行，但仅拦截了 4 个工具**:
     `brain.py:4746-4747` 向模型注入：
     ```python
     f"你在当前会话里调用的 `run_bash`、`read_file`、`write_file`、`edit_file`、`dsh_workspace` 均**直接在 Windows 电脑本机 ({pc_host})** 上执行！\n"
     ```
     然而在 `brain.py:4879-4884` 和 `4952-4957` 中：
     ```python
     elif name == 'run_bash' and src in ('pc', 'pc2'):
         res = await self._raw_dsh_rpc('pc_shell', args, timeout=65.0)
     elif name in ('read_file', 'write_file', 'edit_file') and src in ('pc', 'pc2') and self._should_use_pc_fs(args):
         res = await self._raw_dsh_rpc('pc_fs', {'fs_op': name, **args}, timeout=25.0)
     else:
         res = await self.harness.execute(name, args)
     ```
     **`list_dir`、`glob_files`、`grep_search`、`bg_task`、`watchdog_task` 完全没有路由到 PC**，100% 落入 `self.harness.execute(name, args)` 在**云端 Linux 容器**中执行！
  2. **`_should_use_pc_fs` 存在严重的云端劫持与工作区基准目录分裂**:
     查看 `brain.py:343-364`：
     ```python
     if norm.startswith(('workspace/', 'work/', 'uploads/', 'notes/', 'skills/', '/root/', '/tmp/', '/var/', '/etc/', '/home/')):
         return False
     try:
         cand = (self.harness.root / norm).resolve()
         if cand.exists():
             return False
     except Exception:
         pass
     return True
     ```
     - **同名文件劫持 Bug**: 只要云端 `self.harness.root` 下恰好存在同名相对路径文件（例如 `README.md`、`package.json`、`main.py`、`test.py`），`if cand.exists(): return False` 就会返回 `False`！于是：
       - 模型调 `run_bash("python test.py")` -> 在 **Windows PC** 的 `~/Documents/deepseek-harness/default-workspace/test.py` 执行；
       - 模型调 `read_file("test.py")` 或 `edit_file("test.py", ...)` -> 因为云端也有个 `test.py`，`cand.exists()` 为 `True`，被劫持到 **云端 Linux** 读写！
     - **上传图片/文件找不到 Bug**: 当用户在 PC 端上传文件或截图时，文件保存在云端 `self.harness.root / 'uploads/...'`。`_should_use_pc_fs` 对 `'uploads/...'` 返回 `False`（留在云端给 `read_file` 读取），但如果模型在 PC 会话中调用 `run_bash("python process.py uploads/foo.png")`，`run_bash` 无条件发往 Windows PC (`pc_shell`)，而 Windows PC 本机上根本没有云端的 `uploads/foo.png`！
     - **子目录 `workspace/`、`work/`、`notes/`、`skills/` 读写分裂**: 模型在 PC 上调 `write_file("notes/todo.md", ...)` 写到了云端 Linux，随后调 `run_bash("type notes\\todo.md")` 却在 Windows PC 上找不到文件！
- **具体后果**:
  在 PC 会话中，Agent 用 `list_dir`/`glob_files`/`grep_search` 看到的是云端 Linux 目录，用 `run_bash` 操作的是 Windows PC 目录，用 `read_file`/`write_file` 则根据云端是否存在同名文件随机在云端与 Windows PC 之间横跳，造成极严重的认知错乱与文件误改。
- **修复方案**:
  1. 为 `pc_fs` 增加 `list_dir`、`glob_files`、`grep_search` 支持，或在显式指定 `cloud:` 前缀时才走云端；
  2. 移除 `_should_use_pc_fs` 中 `if cand.exists(): return False` 的隐式同名劫持逻辑（仅当路径显式指向云端 `uploads/` 且该文件确实是云端上传附件时才走云端，并在 `run_bash` 涉及 `uploads/` 时自动同步或提示）。

---

### [SRV-013] [HIGH] 子代理 (`delegate_task` / `brain._research` / `harness.run`) 与定时任务 (`run_scheduled`) 完全绕过 `brain.py` 的 `pc_shell` / `pc_fs` 路由
- **分类**: 子代理与主代理路由不一致 (`Subagent Routing Bypass`)
- **精确文件与行号**:
  - `D:\bubuki\proactive-personal-agent\server\brain.py:5157-5241` (`_research`)
  - `D:\bubuki\proactive-personal-agent\server\harness.py:721-832` (`AgentHarness.run`)
  - `D:\bubuki\proactive-personal-agent\server\brain.py:5380` (`run_scheduled`)
- **根因与代码证据**:
  当用户在 PC 会话（`src in ('pc', 'pc2')`）中让主代理工作，主代理调用 `delegate_task` 派生子代理时：
  - `brain.py:5219` 调用 `await self.harness.run(...)`；
  - `harness.py:767` (并行) 和 `harness.py:802` (串行) 直接调用 `await self.execute(c['name'], c['arguments'])`；
  - `AgentHarness.execute` (`harness.py:486-565`) **完全没有任何 `pc_shell` 或 `pc_fs` 路由逻辑**！
  同样，在 `brain.py:5380` (`run_scheduled`) 中，对于非 `_brain_handled` 工具（如 `run_bash`、`read_file`、`write_file`、`edit_file`），直接执行 `res = await self.harness.execute(name, args)`，即使该定时任务原本属于 PC 节点也不会路由到 PC。
- **具体后果**:
  在 PC 会话中，主代理的 `run_bash`/`write_file` 操作的是用户的 Windows 电脑，而它派生的 `delegate_task` 子代理执行 `run_bash`/`read_file`/`write_file` 时却全部跑在云端 Linux 容器里，主代理与子代理处于完全隔离的两台机器上，无法共享中间文件！
- **修复方案**:
  为 `AgentHarness.run()` 提供可选的 `executor_override` 回调（或在 `AgentHarness` 中注入带 `source_context` 感知的执行器钩子），使子代理继承父会话的设备路由策略。

---

### [SRV-014] [HIGH] PC 端 `pc_fs` 的 `read_file` 不支持图片多模态视觉读取 (`_multimodal_b64`)、不支持二进制安全读取，且静默截断为 250 行
- **分类**: 跨层功能退化与静默截断 (`Cross-Layer Feature Regression & Silent Truncation`)
- **精确文件与行号**:
  - `D:\bubuki\proactive-personal-agent\server\harness.py:504-518`
  - `D:\bubuki\proactive-personal-agent\server\brain.py:4881-4882, 4954-4955`
  - `D:\dsh-baike-plugin\index.js:700-705` (与 Auditor-2 联合核实)
- **根因与代码证据**:
  1. 在云端 `harness.py:505-507` 中，当 `read_file` 读取 `.png/.jpg/.jpeg/.webp/.gif/.bmp` 图片时，会调用 `self.vision_reader`（或附加 `_multimodal_b64`）让模型“看图”。
  2. 但当 `src in ('pc', 'pc2')` 路由到 PC `pc_fs` 时，`D:\dsh-baike-plugin\index.js:700-705` 执行：
     ```javascript
     const raw = fs.readFileSync(targetPath, "utf8");
     const lines = raw.split(/\r?\n/);
     const start = Math.max(1, Number(args.start_line || 1));
     const end = Math.min(lines.length, Number(args.end_line || start + 250));
     const content = lines.slice(start - 1, end).join("\n").slice(0, 24000);
     return { ok: true, path: targetPath, total_lines: lines.length, content, hostname: HOSTNAME };
     ```
     - 若读取 Windows 本机上的图片文件（如 `C:\Users\...\screenshot.png`），`fs.readFileSync(targetPath, "utf8")` 直接返回高达 24000 字符的 **UTF-8 乱码二进制文本 (`\ufffdPNG...`)** 塞进 LLM 上下文，且没有任何 `_multimodal_b64` 视觉帧！
     - 若读取大文件且未传 `end_line`，`index.js` 只返回 `start` 到 `start + 250` 行，且不返回 `end_line` 或 `truncated: true` 标记，模型误以为已经读完整个文件。
     - `fs.readFileSync` 先将整个文件一次性读入内存再 `split`，遇到大体积日志或二进制文件会导致 Node.js 进程内存暴涨或卡死。
- **具体后果**:
  PC 会话中 `read_file` 无法看本机图片（反而把 24KB 二进制乱码灌入上下文浪费 Token），且读超过 250 行的代码文件时被静默截断。
- **修复方案**:
  在 `index.js` 的 `read_file` 中检测图片后缀并返回 base64 缩略图与 `_multimodal_b64`，并在返回结果中明确标注 `start_line`、`end_line`、`truncated: end < lines.length`。

---

### [SRV-015] [HIGH] `brain.py:_raw_dsh_rpc` 的本地 `DSHController` 回退分支缺失 `dispatch_session`、`pc_shell`、`pc_fs`、`cua_desktop`，且依赖不存在的 `dsh_core.py`
- **分类**: 回退分支残缺与死代码 (`Incomplete Fallback & Missing Module`)
- **精确文件与行号**:
  - `D:\bubuki\proactive-personal-agent\server\brain.py:2704-2745`
  - `D:\bubuki\proactive-personal-agent\server\brain.py:3908`
  - `D:\dsh-baike-plugin\package.json:19-20` (与 Auditor-2 联合核实)
- **根因与代码证据**:
  在 `brain.py:2704-2745` 中，当 `self.dsh_dispatch` 未就绪时，`_raw_dsh_rpc` 尝试通过 `from dsh_core import DSHController` 进行本地控制：
  1. 经与 Auditor-2 核实，`D:\dsh-baike-plugin` 和 `server/` 下**根本不存在 `dsh_core.py` 和 `dsh_sentinel.py` 文件**，`import dsh_core` 100% 抛出 `ModuleNotFoundError`。
  2. 即便 `dsh_core` 存在，2715–2733 行的 `if/elif` 分支也只写了 `status`、`list_sessions`、`inspect_session`、`send_message`、`new_session`、`wake_gui`，**完全漏掉了 `dispatch_session`（`brain.py:3908` 跨端委派的核心 RPC）、`pc_shell`、`pc_fs`、`cua_desktop`**！
- **具体后果**:
  本地回退分支完全无法工作，且代码具有误导性。
- **修复方案**:
  清理无效的 `from dsh_core import DSHController` 死代码，直接返回清晰的 PC 桥接未在线错误提示。

---

### [SRV-016] [MEDIUM] `main.py:/ws/dsh-bridge` 与 `brain.py:resolve_dsh_command` 对 `data` vs `result` 字段解析不一致，导致仅传 `data` 时结果丢失
- **分类**: WebSocket 消息字段契约不一致 (`Payload Key Mismatch`)
- **精确文件与行号**:
  - `D:\bubuki\proactive-personal-agent\server\main.py:541-542`
  - `D:\bubuki\proactive-personal-agent\server\brain.py:2627-2635`
- **根因与代码证据**:
  在 `main.py:541-542`（`/ws/dsh-bridge` WebSocket 接收端）中：
  ```python
  if payload.get('request_id') and ('result' in payload or 'data' in payload):
      brain.resolve_dsh_command(payload)
  ```
  `main.py` 允许客户端通过 `'result'` **或** `'data'` 传回 RPC 结果。
  然而在 `brain.py:2631-2633` 的 `resolve_dsh_command` 中：
  ```python
  if fut and not fut.done():
      fut.set_result(event.get('result') if isinstance(event.get('result'), dict) else {'ok': True, 'data': event.get('result')})
  ```
  `resolve_dsh_command` 只取 `event.get('result')`，根本不读取 `event.get('data')`！如果客户端发送的是 `{'request_id': '...', 'data': {'ok': True, ...}}`（不含 `'result'` 键），`event.get('result')` 为 `None`，最终 `fut.set_result({'ok': True, 'data': None})`，真实返回数据被彻底丢弃！
- **具体后果**:
  任何通过 `/ws/dsh-bridge` 或 `/api/dsh/result` 以 `data` 字段上报的结果都会变成 `{'ok': True, 'data': None}`。
- **修复方案**:
  在 `brain.py:2631-2633` 中改为：
  ```python
  raw_res = event.get('result') if 'result' in event else event.get('data')
  fut.set_result(raw_res if isinstance(raw_res, dict) else {'ok': True, 'data': raw_res})
  ```

---

### [SRV-017] [MEDIUM] `harness.py:public_url` SSRF 防护存在 DNS Rebinding 与 IPv4-mapped IPv6 (`::ffff:127.0.0.1`) 边界绕过风险
- **分类**: 安全校验缺陷 (`Security / SSRF Validation Flaw`)
- **精确文件与行号**:
  - `D:\bubuki\proactive-personal-agent\server\harness.py:420-430, 567-609`
- **根因与代码证据**:
  `harness.py:420-430` 的 `public_url(url)` 使用 `socket.getaddrinfo` 解析域名并检查 `ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified`：
  1. **TOCTOU / DNS Rebinding**: `public_url(url)` 在请求前做了一次 DNS 解析，随后 `web_fetch` (`harness.py:579`) 用 `urllib.request.urlopen`（开启了默认 HTTP 301/302 重定向跟随！）再次独立发起连接：
     - 如果目标 URL 返回 `302 Found -> http://127.0.0.1:8000/api/state` 或内网元数据地址 `http://169.254.169.254/...`，`urllib.request.urlopen` 会**直接跟随 302 重定向访问内网地址，而不会对重定向后的 URL 再次调用 `public_url()`**！
     - 同样，在 `_exec_cloud_desktop` (`brain.py:1872-1875`) 的 `op == 'open_url'` 中，完全没有检查 `file://` 或内网地址就直接让云桌面浏览器打开。
- **具体后果**:
  攻击者或恶意网页可通过 301/302 重定向绕过 `public_url` 检查，诱导 `web_fetch` 访问云端本机 `127.0.0.1` 服务或云厂商元数据接口。
- **修复方案**:
  为 `urllib.request.build_opener` 自定义 `HTTPRedirectHandler`，在每次 `redirect_request` 时对新 `newurl` 强制重新执行 `public_url(newurl)` 校验。

---

## 第三部分：手机工具 `_exec_phone_workspace` 与虚拟屏生命周期控制缺陷

---

### [SRV-018] [CRITICAL] `phone_workspace(op="open_uri")`（及带 URL 的 `op="open"`）会连续发送两次 RPC，且第二次将 `op="ui_tree"` 与 `args={'op': 'open_uri'}` 混装发送，覆盖第一次结果并造成客户端 `op` 冲突
- **分类**: 状态机穿透 / 双重执行与参数污染 (`Fallthrough Bug / Double RPC Execution`)
- **精确文件与行号**:
  - `D:\bubuki\proactive-personal-agent\server\brain.py:3358-3365`
  - `D:\bubuki\proactive-personal-agent\server\brain.py:3422-3442`
  - `D:\bubuki\proactive-personal-agent\server\brain.py:3609`
- **根因与代码证据**:
  跟踪 `brain.py` 中 `phone_workspace(op="open", url="https://...")` 或 `phone_workspace(op="open_uri", uri="https://...")` 的完整执行路径：
  1. 在 `3358-3365` 行：
     ```python
     if op == 'open':
         u_cand = str(args.get('uri') or args.get('url') or ...).strip()
         if re.match(r'^[a-zA-Z][a-zA-Z0-9+.-]*://', u_cand):
             op = 'open_uri'
             args = {**args, 'op': 'open_uri', 'uri': u_cand}
     ```
  2. 随后进入 `3422-3442` 行：
     ```python
     if op == 'open_uri':
         uri_str = str(args.get('uri') or args.get('url') or args.get('text') or args.get('target') or '').strip()
         if not uri_str:
             return {'ok': False, 'error': 'open_uri 缺少 uri 参数'}
         res = await self._raw_phone_rpc('open_uri', {'op': 'open_uri', 'uri': uri_str}, timeout=15.0)
         if not res.get('ok'):
             ...
             res = await self._raw_phone_rpc('shell', {'op': 'shell', 'command': safe_cmd}, timeout=12.0)
         await asyncio.sleep(1.5)
         op = 'ui_tree'
     ```
     **注意第 3442 行：`op = 'ui_tree'` 之后既没有 `return`，也没有更新 `args` 字典中的 `'op'` 键（此时 `args['op']` 仍然是 `'open_uri'`）！**
  3. 代码继续向下穿透，经过 `3454-3605` 行（不命中其他 `if`），直达第 `3609` 行：
     ```python
     res = await self._raw_phone_rpc(op, args, timeout=rpc_timeout)
     ```
     此时 `op` 是 `'ui_tree'`，而 `args` 是 `{'op': 'open_uri', 'uri': 'https://...'}`！
     而在 `_raw_phone_rpc` (`brain.py:2865-2871`) 中：
     ```python
     cmd = {
         'request_id': req_id,
         'op': op,          # <--- 这里是 'ui_tree'
         'args': args,      # <--- 这里 args['op'] 却是 'open_uri'！
         'created_at': now_iso(),
     }
     ```
- **具体后果**:
  1. 第 3431/3440 行好不容易拿到的 `open_uri` / `am start` 执行结果 `res` 在第 3609 行被**无条件覆盖丢失**！
  2. 如果安卓客户端优先读取 `args.getString("op")`（为 `'open_uri'`），则会把同一个 URI **重复打开两次**；如果读取顶层 `cmd.getString("op")`（为 `'ui_tree'`），则 `open_uri` 是否成功、错误原因是什么被完全抹除！
- **修复方案**:
  在第 3442 行 যদি需要将打开 URI 后的页面 UI 树附带回传，应当保留原 `open_uri_res = res`，并以干净的 `{'op': 'ui_tree'}` 调用 `_raw_phone_rpc('ui_tree', {'op': 'ui_tree'})` 后合并两个结果字典，而不是只改 `op = 'ui_tree'` 向下穿透覆盖 `res`。

---

### [SRV-019] [HIGH] `phone_workspace(op="open")` 未传 `app`/`uri`（仅传 `component`）或传入裸域名（如 `docs.qq.com`）时被错误改写为 `op="launch", app=""` 或非法包名启动
- **分类**: 路由转换逻辑缺陷 (`Action Normalization Bug`)
- **精确文件与行号**:
  - `D:\bubuki\proactive-personal-agent\server\brain.py:3358-3365`
  - `D:\bubuki\proactive-personal-agent\server\harness.py:250-251`
- **根因与代码证据**:
  查看 `brain.py:3358-3365`：
  ```python
  if op == 'open':
      u_cand = str(args.get('uri') or args.get('url') or args.get('target') or args.get('app') or args.get('package') or args.get('text') or '').strip()
      if re.match(r'^[a-zA-Z][a-zA-Z0-9+.-]*://', u_cand):
          op = 'open_uri'
          args = {**args, 'op': 'open_uri', 'uri': u_cand}
      else:
          op = 'launch'
          args = {**args, 'op': 'launch', 'app': u_cand}
  ```
  这里有 3 个明显的边界 Bug：
  1. **`component` 参数被漏检**: `harness.py:251` 明确定义了 `'component': {**STR, 'description': '可选 Activity 组件名'}`。如果模型调用 `phone_workspace(op="open", component="com.tencent.mm/.ui.LauncherUI")`（未传 `app`/`package`），`u_cand` 取不到 `component`，变为空字符串 `""`，被改写为 `op='launch', app=''`！
  2. **无参调用 `phone_workspace(op="open")` 变成 `op="launch", app=""`**: 如果模型想“打开虚拟屏”而调用 `phone_workspace(op="open")`（不传任何其他参数），它不会打开或检视虚拟屏，而是被强制改成 `op='launch', app=''`，随后在安卓端触发空应用启动报错！
  3. **裸域名 URL 未补全 `https://`**: 如果模型传入 `phone_workspace(op="open", url="www.baidu.com")` 或 `url="docs.qq.com/sheet/..."`（未带 `https://` 协议头），`re.match(r'^[a-zA-Z][a-zA-Z0-9+.-]*://', u_cand)` 不匹配，竟直接走 `else` 分支变成 **`op='launch', app='www.baidu.com'`**（试图把网址当成安卓 App 名称去启动）！（对比 `_exec_cloud_desktop` 第 1873 行专门做了 `if not re.match(...): url = 'https://' + url`，而 `phone_workspace` 却没有！）
- **具体后果**:
  - 传入裸域名时被当成 App 包名去 `launch` 导致失败；
  - 仅传 `component` 或无参调 `op="open"` 时变成 `op="launch", app=""`。
- **修复方案**:
  1. 在 `u_cand` 提取中加入 `args.get('component')`；
  2. 若 `args.get('url')` 或 `args.get('uri')` 存在但缺少协议头（或 `u_cand` 形如 `www.xxx` / `xxx.com/...` 且非安卓包名特征），自动补全 `https://` 并走 `open_uri`；
  3. 若 `u_cand` 为空，回退为 `op = 'state'` 或返回明确的参数缺失提示，绝不构造 `app=''` 的 `launch`。

---

### [SRV-020] [HIGH] `phone_workspace(op="long_press")` 完全忽略 `target`/`ref` 节点定位（未传坐标时永远长按屏幕正中央），且提前 `return` 跳过了视觉截图与坐标尺标注！
- **分类**: 动作定位缺失与提前返回漏标 (`Target Resolution Bypass & Premature Return`)
- **精确文件与行号**:
  - `D:\bubuki\proactive-personal-agent\server\brain.py:3444-3452`
  - `D:\bubuki\proactive-personal-agent\server\brain.py:3481-3605`
  - `D:\bubuki\proactive-personal-agent\server\brain.py:3666-3807`
- **根因与代码证据**:
  查看 `brain.py:3444-3452`：
  ```python
  if op == 'long_press':
      cur_w, cur_h = self._get_phone_screen_size()
      lx, ly = self._resolve_phone_xy(args, cur_w, cur_h, cur_w // 2, cur_h // 2)
      dur = int(args.get('duration_ms') or 900)
      res = await self._raw_phone_rpc('swipe', {
          'op': 'swipe', 'x1': lx, 'y1': ly, 'x2': lx, 'y2': ly, 'duration_ms': dur,
      }, timeout=12.0)
      res['long_pressed'] = {'x': lx, 'y': ly, 'duration_ms': dur}
      return res
  ```
  对比第 `3481-3605` 行的 `tap` / `click` 逻辑与第 `3666-3807` 行的后处理逻辑：
  1. **完全不支持 `target` / `ref`（如 `@e3`、`@v2` 或文本匹配）**:
     `long_press` 放在第 3444 行（在 `3481` 行的 `target`/`ref` 节点解析之前），且只调用了 `_resolve_phone_xy(args, cur_w, cur_h, cur_w // 2, cur_h // 2)`。如果模型调用 `phone_workspace(op="long_press", target="@e5")` 或 `target="某条消息"` 而未传 `x, y` / `nx, ny`，`_resolve_phone_xy` 直接返回默认值 `(cur_w // 2, cur_h // 2)`，**无脑长按屏幕正中心**！
  2. **第 3452 行直接 `return res` 导致后续视觉标注 (`is_visual_op`) 沦为死代码**:
     看第 `3669` 行：
     ```python
     is_visual_op = op in (
         'screenshot', 'tap', 'click', 'double_tap', 'long_press', 'swipe', 'scroll', 'drag',
         'text', 'type', 'input', 'tap_and_type', 'key', 'press_key', 'back', 'home', 'enter',
         'launch', 'open', 'open_uri', 'wait', 'state', 'status', 'ui_tree', 'dump_tree'
     )
     ```
     第 3669 行明明把 `'long_press'` 列入了 `is_visual_op`，准备为长按后的界面自动抓取截图、画红十字准星 (`_annotate_action_crosshair`)、加 `0..1000` 坐标标尺并附带 `_multimodal_b64` 给模型看长按弹出的上下文菜单。但因为第 3452 行直接 `return res`，**`long_press` 永远走不到第 3669 行**，模型在长按后完全看不到弹出的菜单截图和最新 UI 树！
- **具体后果**:
  1. `phone_workspace(op="long_press", target="@e5")` 100% 误长按屏幕正中央；
  2. 每次 `long_press` 执行完后不返回操作后截图与新 UI 树，模型不知道长按是否弹出了菜单。
- **修复方案**:
  1. 让 `long_press` 复用 `tap` 的 `target`/`ref` 节点中心坐标解析逻辑；
  2. 去掉第 3452 行的提前 `return res`，让 `long_press` 继续流向第 3634–3807 行的自动等待、UI 树刷新、准星绘制与多模态截图注入流程。

---

### [SRV-021] [HIGH] `_vdisplay_currently_open` 与 `_used_phone_in_turn` 状态标志被提前污染、跨会话共享竞态，且 `curiosity()` / `harness.run()` 结束后漏调 `_auto_release_phone_if_used()` 导致虚拟屏泄漏不释放
- **分类**: 虚拟屏生命周期状态机缺陷 (`Virtual Display Lifecycle Leak & State Pollution`)
- **精确文件与行号**:
  - `D:\bubuki\proactive-personal-agent\server\brain.py:3227-3262`
  - `D:\bubuki\proactive-personal-agent\server\brain.py:2934-2960` (`_auto_release_phone_if_used`)
  - `D:\bubuki\proactive-personal-agent\server\brain.py:4200-4377` (`curiosity`)
  - `D:\bubuki\proactive-personal-agent\server\brain.py:5150-5152` (`_chat` finally)
- **根因与代码证据**:
  1. **只读操作与失败调用提前把 `_vdisplay_currently_open` 置为 `True`**:
     在 `brain.py:3251-3262` 中，只要 `op` 不在 `('status', 'state', 'apps', 'list_apps', 'packages', 'shell', 'open_uri', 'release', 'close', 'stop_display', 'home')` 中，**甚至在参数校验（如 `launch` 缺少 `app`）和 RPC 发送之前**，就无条件执行：
     ```python
     self._used_phone_in_turn = True
     self._vdisplay_currently_open = True
     ```
     例如：
     - 调用 `op="send_screenshot"`（在 3272 行转成 `_raw_phone_rpc('preview', ...)`）或直接调用 `op="preview"`；
     - 调用 `op="launch"` 但因参数错误在第 3375 行直接返回 `{'ok': False, ...}`（根本没发 RPC 也没开虚拟屏）；
     - 调用时手机根本不在线（`_raw_phone_rpc` 立即返回 `phone_online: False`）；
     以上情况全部会把 `self._used_phone_in_turn` 和 `self._vdisplay_currently_open` 污染为 `True`，导致回合结束时 `_auto_release_phone_if_used()` 向手机发送多余的 `release` RPC！
  2. **`op="promote"` / `"expand_to_foreground"` 被硬编码强制改写为 `op="release", to_foreground=False`**:
     在 `brain.py:3241-3246` 中：
     ```python
     if op in ('Destroy', 'close_display', 'finish', 'done', 'exit',
               'promote', 'expand_to_foreground', 'to_foreground', 'fullscreen'):
         op = 'release'
         args = {**args, 'op': 'release', 'to_foreground': False}
     ```
     原本用户或模型希望将虚拟屏内打开的页面“投送到主屏前台给主人接管” (`promote` / `expand_to_foreground` / `to_foreground=True`)，却被这行代码强制覆盖成了 `'to_foreground': False`（直接销毁虚拟屏而不切到前台）！而且即便原本调用的是 `phone_workspace(op="release", to_foreground=True)`，在第 3266 行：
     ```python
     res = await self._raw_phone_rpc('release', {'op': 'release', 'to_foreground': False}, timeout=10.0)
     ```
     也把 `to_foreground` 硬编码写死成了 `False`！
  3. **`curiosity()` 主动探索流与 `harness.run()` 子代理完全漏调 `_auto_release_phone_if_used()`**:
     `brain.py:5151` (在 `_chat` 的 `finally` 中) 和 `brain.py:5401` (在 `run_scheduled` 中) 调用了 `await self._auto_release_phone_if_used()`。
     但在 `brain.py:4200-4377` 的 **`curiosity()` 心跳主动探索流**中，模型同样拥有 `TOOL_DEFS`（含 `phone_workspace`），其 `finally` 块（4366–4376 行）**完全没有调用 `await self._auto_release_phone_if_used()`**！一旦主动探索流使用了 `phone_workspace`，虚拟屏将永远悬停在用户手机屏幕上不释放！
  4. **全局布尔变量并发竞态**:
     `self._used_phone_in_turn` 和 `self._vdisplay_currently_open` 是 `Brain` 单例上的普通布尔属性，而非按 `turn_id` 引用计数。当两个分身（如 `companion` 和 `analyst`，或 `_chat` 与 `run_scheduled`）并发执行时，先结束但未使用手机的回合若读取到被另一个回合置为 `True` 的 `self._used_phone_in_turn`，就会在 `_auto_release_phone_if_used()` 中**把正在操作手机的另一个分身的虚拟屏中途强行销毁 (`release`)**！
- **具体后果**:
  1. 并发回合（如后台定时任务与前台对话同时运行）会提前误关对方正在使用的手机虚拟屏；
  2. `curiosity()` 使用手机后虚拟屏泄漏不关闭；
  3. `to_foreground=True` / `promote` 功能被彻底阉割。
- **修复方案**:
  1. 将 `_used_phone_in_turn` 改为绑定在 `ContextVar`（或按活跃使用者的 `set[str]` 引用计数 `self._active_phone_turns`）上，仅当最后一个使用手机的活跃 turn 结束时才触发 `release`；
  2. 在 `curiosity()` 的 `finally` 块中补上 `await self._auto_release_phone_if_used()`；
  3. 仅在 `_raw_phone_rpc` 实际成功打开/操作虚拟屏后才标记 `_vdisplay_currently_open = True`，并将 `'preview'` / `'send_screenshot'` 加入免虚拟屏释放白名单；
  4. 保留 `args.get('to_foreground', False)`，不要对 `promote` / `expand_to_foreground` 强制覆写为 `False`。

---

### [SRV-022] [MEDIUM] `phone_workspace(op="tap_and_type")` 在查找视觉节点 (`@v`) 时错误读取 `n.get('type')`（实际字段名为 `'role'`），且丢弃了 `clear` 参数
- **分类**: 内部数据结构字段名错配 (`Internal Field Name Mismatch`)
- **精确文件与行号**:
  - `D:\bubuki\proactive-personal-agent\server\brain.py:3109-3119` (`_vision_extract_ui_tree`)
  - `D:\bubuki\proactive-personal-agent\server\brain.py:3529-3531, 3546-3548, 3563` (`tap_and_type`)
- **根因与代码证据**:
  1. 在 `brain.py:3109-3119`（`_vision_extract_ui_tree` 构造 `@v1, @v2...` 视觉节点列表）中，每个节点的类型存储在键 **`'role'`** 中：
     ```python
     nodes.append({
         'ref': f'@v{idx}',
         'text': label,
         'role': role,
         'bounds': [x1, y1, x2, y2],
         'center': [cx, cy],
         'norm_center': [nx, ny],
     })
     ```
     没有任何 `'type'` 键！
  2. 然而在 `brain.py:3529-3531` 和 `3546-3548`（`tap_and_type` 查找输入框视觉节点）中，代码却写成了：
     ```python
     for n in self._last_vision_nodes:
         if str(n.get('type') or '').lower() in ('input', 'edittext', 'search'):
     ```
     因为 `n.get('type')` 永远是 `None`，这两处输入框角色匹配逻辑 **100% 失效**！
  3. 此外，在第 `3563` 行：
     ```python
     type_res = await self._raw_phone_rpc('text', {'op': 'text', 'text': content_txt}, timeout=12.0)
     ```
     漏传了 `'clear': bool(args.get('clear', False))`（对比第 3349 行的普通 `text` 分支保留了 `args` 中的 `'clear'`）。
- **具体后果**:
  `tap_and_type` 在基于视觉树寻找输入框时无法匹配 `role="input"` 的节点；且 `tap_and_type(..., clear=True)` 不会清空原有文本。
- **修复方案**:
  将 `3530` 和 `3547` 行的 `n.get('type')` 改为 `n.get('role') or n.get('type')`，并在 `3563` 行透传 `'clear': bool(args.get('clear', False))`。

---

### [SRV-023] [MEDIUM] `phone_workspace(op="swipe")` 在未传坐标时默认变成向上滑动，但未支持 `direction` 参数（如 `direction="down"|"left"|"right"`）
- **分类**: 工具参数契约缺陷 (`Parameter Handling Defect`)
- **精确文件与行号**:
  - `D:\bubuki\proactive-personal-agent\server\brain.py:3310-3329`
  - `D:\bubuki\proactive-personal-agent\server\harness.py:249`
- **根因与代码证据**:
  在 `brain.py:3310-3321` 中：
  - 当 `op == 'scroll'` 时，代码会根据 `direction = str(args.get('direction') or 'down').lower()` 计算 `up`/`down`/`left`/`right` 的起止坐标并转为 `op = 'swipe'`。
  - 但如果模型直接调用 `phone_workspace(op="swipe", direction="down")`（未传 `x1, y1, x2, y2`），它跳过 `if op == 'scroll':`，直接进入 `if op in ('swipe', 'drag'):` (`3322-3329` 行)：
    ```python
    x1, y1 = self._resolve_phone_xy(args, cur_w, cur_h, cur_w // 2, int(cur_h * 0.75), 'x1', 'y1', 'nx1', 'ny1')
    x2, y2 = self._resolve_phone_xy(args, cur_w, cur_h, cur_w // 2, int(cur_h * 0.25), 'x2', 'y2', 'nx2', 'ny2')
    ```
    完全忽略了 `args.get('direction')`，永远执行从 `0.75*h` 到 `0.25*h` 的向上滑动！
- **具体后果**:
  模型调用 `phone_workspace(op="swipe", direction="down")` 或 `direction="right"` 且未显式指定 `x1..y2` 时，滑动方向与模型意图完全相反或错位。
- **修复方案**:
  当 `op == 'swipe'` 且未提供 `(x1, y1, x2, y2)` / `(nx1, ny1, nx2, ny2)` 但提供了 `direction` 时，复用 `op == 'scroll'` 的四向坐标计算逻辑。

---

### [SRV-024] [HIGH] `main.py:publish()` 将 `phone_workspace_command` 无差别广播给所有在线 `/ws/client`（包括 `phone` 和 `phone2`），且 `brain.py:_raw_phone_rpc` 单一 `phone_futures` 被多手机同时抢答
- **分类**: 多手机节点路由冲突 (`Multi-Phone Routing Broadcast Collision`)
- **精确文件与行号**:
  - `D:\bubuki\proactive-personal-agent\server\main.py:88-112`
  - `D:\bubuki\proactive-personal-agent\server\brain.py:2861-2890`
- **根因与代码证据**:
  1. 在 `brain.py:2872` 中，`_raw_phone_rpc` 发送手机控制指令：
     ```python
     await self.publish({'type': 'phone_workspace_command', 'command': cmd})
     ```
     该事件没有携带目标 `source`（如 `'phone'` 还是 `'phone2'`）。
  2. 在 `main.py:92` 中：
     ```python
     if etype not in ('message', 'stream', 'execution', 'stopped'):
         return None
     ```
     因为 `'phone_workspace_command'` 不在该元组内，`_event_source(event)` 返回 `None`，`main.py:106-112` 会把这条 `phone_workspace_command` **同时广播给所有连接在 `/ws/client` 上的安卓客户端（例如主力机 `phone` 和备用机 `phone2`）**！
  3. 两台手机会同时在各自的虚拟屏上执行同一个点击/输入/启动 App 命令，并同时回调 `/api/phone-workspace/result`，先返回的那台手机会 `set_result` 解决 `self.phone_futures[req_id]`，后返回的被忽略，导致两台手机同时乱动且状态错乱！
- **具体后果**:
  当用户同时连接了两台安卓设备（`phone` 与 `phone2`）时，任何 `phone_workspace` 指令都会在两台手机上同时触发执行！
- **修复方案**:
  在 `phone_workspace_command` 事件中附带目标 `source`（默认取当前会话若为 `'phone2'` 则发往 `'phone2'`，否则发往 `'phone'`），并在 `main.py:_event_source` 中将 `'phone_workspace_command'` 纳入按 `source` 定向投递的范围。

---

## 第四部分：并行工具执行 (`can_parallel`)、流式输出、打断 (`stop_immediately`)、多 Bot 互联 (`bot_interconnect`) 与存储并发隐患

---

### [SRV-025] [HIGH] `brain.py` 并行工具分支 (`can_parallel`) 与串行分支存在 5 处严重行为不一致（缺死循环熔断、缺 `custom_tool` 热加载、多模态帧互相覆盖、缺 `present_choices` 早退、`ContextVar` 并发污染）
- **分类**: 并行与串行执行分支逻辑分裂 (`Parallel vs. Serial Execution Divergence & Race Condition`)
- **精确文件与行号**:
  - `D:\bubuki\proactive-personal-agent\server\brain.py:4850-4924` (并行分支)
  - `D:\bubuki\proactive-personal-agent\server\brain.py:4926-5005` (串行分支)
  - `D:\bubuki\proactive-personal-agent\server\harness.py:761-784` (`AgentHarness.run` 并行分支)
- **根因与代码证据**:
  对比 `brain.py` 的并行分支（4850–4924 行）与串行分支（4926–5005 行）：
  1. **并行分支完全缺失死循环调用拦截 (`recent_call_sigs`)**:
     - 串行分支（4932–4949 行）计算 `call_sig = f"{name}:{json.dumps(args, sort_keys=True)}"` 并追加到 `recent_call_sigs`，当连续 3 次相同时拦截返回 `[系统防死循环拦截]`。
     - 并行分支（4850–4924 行）**既不向 `recent_call_sigs` 追加记录，也不检查重复调用**！如果模型每轮同时发出 2 个相同的并行工具调用（如 `read_file("a.txt")` 和 `read_file("b.txt")`），可以无限循环直到耗尽 `max_steps=45`！
  2. **并行分支缺失 `custom_tool` Schema 热更新**:
     - 串行分支（4993–5001 行）在执行 `custom_tool(action="create"|"delete")` 后会刷新 `all_schemas = self.harness.all_tools(...)`。
     - 并行分支完全没有这段逻辑！如果模型将 `custom_tool(action="create")` 与其他工具一起发出，下一轮 `all_schemas` 依然是旧列表，新创建的工具无法调用。
  3. **并行分支多张图片返回时 `latest_mm_frame` 被覆盖丢失**:
     - 在 `brain.py:4898-4903` 和 `harness.py:773-777` 中，`latest_mm_frame` 是单个变量。如果模型并行调用了两个带视觉返回的工具（例如同时 `read_file("img1.png")` 和 `read_file("img2.png")`，或 `read_file` + `cloud_desktop`），循环中后一个工具的 `_multimodal_b64` 会直接覆盖前一个工具的 `latest_mm_frame`，前一张图片被彻底丢弃！
  4. **并行分支缺失 `present_choices` 早退逻辑**:
     - 串行分支（5004–5005 行）检查 `if all(c.get('name') == 'present_choices' for c in calls) and len(final_turn_text.strip()) >= 12: break`，并行分支缺失。
  5. **并行分支并发执行 `send_message` 导致 `ContextVar` 与 `_reply_box_by_sig` 竞态互踩**:
     - `can_parallel` (`brain.py:4849`) 仅排除了 `('phone_workspace', 'cloud_desktop')`，**允许多个 `send_message` 并行执行**！
     - 当 `_run_one` 在 `asyncio.gather` 中并发调用两个 `self._tool_send_message` (`brain.py:4001-4120`) 时：
       - 第 4055 行 `self._reply_box_by_sig[cur_sig] = []` 使用同一个 `cur_sig = f"{cur_src}:{cur_bot}:{cur_turn_id}"`，两个并发 `send_message` 会互相清空对方的收件箱列表；
       - 第 4057–4080 行直接修改 `self.bot_context`、`self.room_context`、`self.source_context`、`self.turn_id_var`、`self.hop_depth`、`self.stream_prefix_var` 等实例级变量/共享上下文；
       - 第 4089 行 `my_boxes = self._reply_box_by_sig.pop(cur_sig, [])` 第一个完成的 `send_message` 会把整个 `cur_sig` 弹出，导致第二个完成的 `send_message` 拿到空列表 `[]`！
- **具体后果**:
  并行调用 `send_message` 时跨分身回执丢失且上下文错乱；并行调用多个图片读取工具时只保留最后一张图；并行工具调用不受死循环熔断保护。
- **修复方案**:
  1. 将 `'send_message'`、`'custom_tool'`、`'dsh_workspace'`、`'run_bash'` 加入 `can_parallel` 的排除列表（或为每次 `send_message` 生成独立 `call_id` 作为 `_reply_box_by_sig` 的键）；
  2. 将 `latest_mm_frame` 改为列表 `mm_frames: list[dict]`，支持同一轮注入多张视觉图片；
  3. 在并行分支同步补齐 `recent_call_sigs` 记录与检查。

---

### [SRV-026] [HIGH] `stop_immediately` 与 `_clear_bot_stream` 状态同步断层：WebSocket `"stopped"` 事件未被安卓端消费，且清空流时未广播空流事件导致客户端气泡卡死在“思考中”
- **分类**: 跨端状态机同步缺陷 (`Cross-Layer State Sync Defect — Server <-> Android UI`)
- **精确文件与行号**:
  - `D:\bubuki\proactive-personal-agent\server\brain.py:124-165` (`_set_bot_stream`, `_clear_bot_stream`)
  - `D:\bubuki\proactive-personal-agent\server\brain.py:251-298` (`stop_immediately`)
  - `D:\bubuki\proactive-personal-agent\server\brain.py:5127-5152` (`_chat` `CancelledError` & `finally`)
  - `android/app/src/main/java/com/user/localagent/NativePulseActivity.kt:786-993` (与 Auditor-4 联合核实)
- **根因与代码证据**:
  1. **`_clear_bot_stream` 从不发送 WebSocket 广播**:
     在 `brain.py:155-165` 中，`_clear_bot_stream` 只删除了服务端内存中的 `self.streams_by_node_bot` 和 `self.streams_by_bot` 字典项，**没有通过 `self.publish({'type': 'stream', ...})` 发送任何清空通知**。
  2. **`stop_immediately` 发送的 `type="stopped"` 事件被安卓端忽略**:
     在 `brain.py:293` 中，`stop_immediately` 发布了 `await self.publish({'type': 'stopped', 'bot_id': target_bot, 'source': norm_src, ...})`。
     经与 Auditor-4 核实，安卓端 `NativePulseActivity.kt:786-993` 的 WebSocket 消息分发器只处理了 `stream`、`execution`、`message`、`messages_truncated`、`phone_workspace_command`，**完全没有处理 `stopped` 事件分支**！
  3. **未生成文本前被打断时，无 `message` 事件落盘**:
     在 `brain.py:5127-5142` 中，当 `_chat` 收到 `CancelledError` 时，仅当 `partial_text` 或 `partial_reasoning` 非空时才调用 `add_message`（发送 `type="message"`）。如果模型尚在等待首 Token 或工具刚启动（`partial_text` 与 `partial_reasoning` 均为空）就被用户点击停止，服务端既不发 `type="message"`，`_clear_bot_stream` 也不发 `type="stream"`，而 `type="stopped"` 又被安卓忽略！
- **具体后果**:
  用户在模型刚进入“正在思考...”但尚未吐字时点击停止（或后台任务取消），安卓客户端得不到流结束信号，UI 状态滞留，直到下一次全量轮询才恢复。
- **修复方案**:
  1. 在 `stop_immediately` 和 `_chat` 的 `CancelledError` 处理中，额外发布一条 `{'type': 'stream', 'bot_id': bot_id, 'source': src, 'stream': None}`；
  2. 安卓端 `NativePulseActivity.kt` 增加对 `type == "stopped"` 和 `stream == null` 的即时清理处理。

---

### [SRV-027] [HIGH] `main.py` WebSocket 事件路由与 `brain.py` 后台任务缺失 `source` 字段，导致 `messages_truncated` 跨端误删消息、`phone2` 收不到主动通知、以及 `_research`/`run_scheduled` 执行态脱管
- **分类**: 多端房间隔离与事件路由漏洞 (`Multi-Node Room Isolation & WS Routing Flaw`)
- **精确文件与行号**:
  - `D:\bubuki\proactive-personal-agent\server\main.py:88-112`
  - `D:\bubuki\proactive-personal-agent\server\brain.py:1139-1143` (`bg_task` 完成通知)
  - `D:\bubuki\proactive-personal-agent\server\brain.py:4216` (`curiosity`)
  - `D:\bubuki\proactive-personal-agent\server\brain.py:4491, 5468` (`messages_truncated`)
  - `D:\bubuki\proactive-personal-agent\server\brain.py:5173-5183, 5233-5241` (`_research`)
  - `D:\bubuki\proactive-personal-agent\server\brain.py:5261, 5277, 5376, 5402` (`run_scheduled`)
  - `D:\bubuki\proactive-personal-agent\server\brain.py:5747` (`deadlines`)
- **根因与代码证据**:
  查看 `main.py:88-112` 的 WebSocket 广播过滤逻辑：
  ```python
  def _event_source(event: dict) -> str | None:
      etype = str(event.get('type') or '')
      if etype not in ('message', 'stream', 'execution', 'stopped'):
          return None
      ...
  ```
  这里引发了 4 类多端同步故障：
  1. **`messages_truncated` 未按 `source` 隔离广播**:
     `etype == 'messages_truncated'` 不在 `('message', 'stream', 'execution', 'stopped')` 中，因此 `_event_source` 返回 `None`，`main.py:108` 将其广播给**所有终端（`phone`, `phone2`, `pc`）**！且经与 Auditor-4 核实，安卓端收到 `messages_truncated` 时只检查 `bot_id` 而不检查 `source`，导致在 PC 端撤回/重发一条消息时，手机端同分身下 `id > upto_id` 的手机聊天记录被当场误删截断！
  2. **`bg_task` 完成通知硬编码漏传 `source`**:
     在 `brain.py:1139-1143` 中，`bg_task` 后台进程结束时调用 `self.store.add_message('assistant', msg, {'kind': 'bg_task_done', 'task_id': tid}, bot_id=bot_id)`，**未传 `source` 参数**！`storage.py:296` 默认将其归入 `source='phone'`。如果用户在 `pc` 或 `phone2` 启动了 `bg_task`，完成通知会发到 `phone` 房间，原发起端根本收不到！
  3. **`_research` 与 `run_scheduled` 的 `execution` 事件漏传 `source` 且未写入 `executions_by_node_bot`**:
     - `brain.py:5173-5183, 5233-5241` (`_research`) 和 `brain.py:5277, 5376, 5402` (`run_scheduled`) 在构造 `my_exec` 及调用 `self.publish({'type': 'execution', 'bot_id': ..., 'execution': ...})` 时**均未包含 `'source'`**！
     - `main.py:98` 对无 `source` 的 `execution` 事件默认归为 `'phone'`，导致 `pc` 和 `phone2` 客户端在 `/ws/client` 上被 `if ev_src and ev_src != c_src: continue` (`main.py:108`) 过滤掉，完全看不到子代理或定时任务的执行进度！
  4. **`curiosity`、`run_scheduled`、`deadlines` 硬编码 `source='phone'` 导致 `phone2` 永远收不到主动消息**:
     `brain.py:4216, 5261, 5747` 全部硬编码 `source='phone'`，当用户使用 `phone2`（安卓二）连接 `/ws/client?source=phone2` 时，所有主动提醒、闹钟和日程通知都被 `main.py:108` 拦截丢弃！
- **具体后果**:
  PC 重发消息会清空手机端新消息；PC/`phone2` 发起的 `bg_task` 和子代理执行态丢失；`phone2` 收不到任何闹钟与主动关怀消息。
- **修复方案**:
  1. 在 `main.py:92` 将 `'messages_truncated'` 与 `'phone_workspace_command'` 加入 `_event_source` 过滤列表；
  2. 在 `bg_task` (`brain.py:1086`) 创建时捕获 `src = self._current_source()` 并在第 1140 行传入 `source=src`；
  3. 在 `_research` 和 `run_scheduled` 中同步维护 `executions_by_node_bot` 并携带 `source`。

---

### [SRV-028] [MEDIUM] `main.py:/api/pairing-info` 返回的 `fingerprint` 永远为空字符串 `""`
- **分类**: 接口字段缺失 (`Missing Return Key`)
- **精确文件与行号**:
  - `D:\bubuki\proactive-personal-agent\server\main.py:616-625`
  - `D:\bubuki\proactive-personal-agent\server\security.py:26-27`
- **根因与代码证据**:
  在 `main.py:616-624` 中：
  ```python
  pub = crypto.public()
  return {
      ...
      'public_key': pub.get('public_key', ''),
      'fingerprint': pub.get('fingerprint', ''),
  }
  ```
  而在 `security.py:26-27` 中：
  ```python
  def public(self) -> dict[str, str]:
      return {'public_key': b64(self.public_der), 'algorithm': 'RSA-OAEP-SHA256+A256GCM'}
  ```
  `CryptoBox.public()` 根本没有计算或返回 `'fingerprint'` 键！
- **具体后果**:
  `/api/pairing-info` 接口返回的 `'fingerprint'` 永远是 `""`，客户端扫码配对时无法展示或核对服务端公钥指纹。
- **修复方案**:
  在 `security.py:27` 中增加 `'fingerprint': hashlib.sha256(self.public_der).hexdigest()[:16]`。

---

### [SRV-029] [MEDIUM] `main.py:/api/context/compact` 忽略请求体中的 `source`，且 `MemoryEngine` 的 Token 统计与上下文估算把同一 `bot_id` 下所有终端 (`phone`/`pc`/`phone2`) 混在一起
- **分类**: 多终端上下文管理混串 (`Cross-Source Context Compaction & Token Stats Mixing`)
- **精确文件与行号**:
  - `D:\bubuki\proactive-personal-agent\server\main.py:821-834`
  - `D:\bubuki\proactive-personal-agent\server\core\memory_engine.py:34-36, 419-461, 463-504`
- **根因与代码证据**:
  1. 在 `main.py:821-834` 中：
     ```python
     @app.post('/api/context/compact')
     async def manual_compact_context(body: dict = Body(default={}), _: str = Depends(auth)):
         target_bot = str((body or {}).get('bot_id') or '').strip()
         ...
         res = await brain._auto_compact_if_needed(target_bot, force=True)
     ```
     `manual_compact_context` 完全没有读取 `body.get('source')` 并传给 `brain._auto_compact_if_needed(target_bot, force=True, source=target_src)`！导致用户在 PC 端点击“手动压缩上下文”时，实际压缩的是 `'phone'` 房间的消息！
  2. 在 `server/core/memory_engine.py:420`（`get_context_stats`）中：
     ```python
     bot_msgs = [m for m in self.store.state.get('messages', []) if m.get('bot_id', 'companion') == bot_id]
     ```
     这里完全没有按 `source` 过滤消息！且 `self.last_prompt_tokens` 和 `self.last_cached_tokens` (`memory_engine.py:34-35`) 仅以 `bot_id` 为键（而非 `f"{src}:{bot_id}"`），导致 PC 端与手机端同一分身的 Token 统计互相覆盖，且 `get_context_stats` 把所有终端的消息加在一起计算体积，触发过早压缩！
- **具体后果**:
  PC 端手动压缩上下文无效（误压缩手机端）；多端共用同一分身时 Token 占用显示错误。
- **修复方案**:
  1. `main.py:831` 读取 `target_src = str((body or {}).get('source') or 'phone').strip()` 并传给 `_auto_compact_if_needed`；
  2. `MemoryEngine.get_context_stats` 支持 `source` 参数并按 `(bot_id, source)` 隔离统计。

---

### [SRV-030] [MEDIUM] `brain.py:enqueue_chat` 的 3 秒幂等去重分支漏返 `user_message` 和 `turn_id`，且 `_recent_enqueued` 用 `len(content)` 做签名会将不同长文本误判为重复消息丢弃！
- **分类**: 幂等去重逻辑缺陷与返回契约不一致 (`Deduplication Collision & Return Contract Mismatch`)
- **精确文件与行号**:
  - `D:\bubuki\proactive-personal-agent\server\brain.py:4453-4469, 4565-4577`
- **根因与代码证据**:
  查看 `brain.py:4453-4468`：
  ```python
  dedup_sig = f"{norm_src}:{bot_id}:{content.strip()[:120]}:{len(content)}"
  now_ts = time.time()
  last_ts = self._recent_enqueued.get(dedup_sig, 0.0)
  if now_ts - last_ts < 3.0:
      return {
          'ok': True,
          'queued': True,
          'deduplicated': True,
          'preempted': False,
          'bot_id': bot_id,
          'source': norm_src,
          'state': self.export_runtime(self.store.snapshot()),
      }
  ```
  1. **返回字段缺失**: 正常路径（4565–4577 行）返回字典包含 `'user_message': user_msg` 和 `'turn_id': turn_id`，而去重分支（4460–4468 行）漏掉了这两个字段。
  2. **签名碰撞风险**: `dedup_sig` 仅取前 120 个字符 + 总长度 `len(content)`，且未包含附件列表 `attachments`！如果用户在 3 秒内连续发送两张不同的图片（文字均为空 `""` 或相同提示词，此时 `content.strip()[:120]` 为 `""`，`len(content)` 为 `0`），**第二张图片消息会被直接判定为重复消息静默丢弃**！
  3. **提前写入 `_recent_enqueued`**: 第 4469 行在 `if not content.strip() and not attachments: return {'ok': False}` 之前就写入了 `self._recent_enqueued[dedup_sig] = now_ts`。
- **具体后果**:
  用户 3 秒内连续发送两张不同图片（不带文字或带相同文字）时，第二张图片被直接吞掉！
- **修复方案**:
  将 `attachments` 的路径/哈希加入 `dedup_sig`（并用完整内容的 `hashlib.md5` 替代 `content[:120]:len`），并在去重命中时返回上次记录的 `user_message` 与 `turn_id`。

---

### [SRV-031] [MEDIUM] `storage.py` 与 `vault_manager.py` 在高并发异步协程下的线程锁 (`RLock`) 阻塞事件循环与非原子写盘隐患
- **分类**: 存储并发与阻塞隐患 (`Storage Concurrency & Event Loop Blocking`)
- **精确文件与行号**:
  - `D:\bubuki\proactive-personal-agent\server\storage.py:216-247`
  - `D:\bubuki\proactive-personal-agent\server\vault_manager.py:322-370`
- **根因与代码证据**:
  1. **`vault_manager.py` 完全无锁且非原子写入**:
     `Vault.write` (`vault_manager.py:322-332`)、`Vault.append` (`334-349`)、`Vault.patch` (`351-368`) 直接在主线程调用 `p.write_text(...)`，没有任何锁保护，也没有使用临时文件 `os.replace` 原子替换。当多个分身或后台任务（如 `curiosity`、`_chat`、`run_scheduled`）同时调用 `vault.append('timeline/work_log.md', ...)` 或 `vault.patch` 时，会发生并发读写交错甚至文件截断损坏。
  2. **`storage.py:save()` 在高频调用下阻塞事件循环**:
     虽然 `storage.py:224-239` 对超过 256KB 的 `state` 在无活跃 `_save_thread` 时使用守护线程落盘，但当 `_save_thread.is_alive()` 为 `True`（即上一次落盘尚未结束）且 `_dirty` 再次触发，或者在 `flush_sync()`（如 `/api/state` 或关闭阶段）时，会在 FastAPI 主事件循环线程同步执行 `json.dumps(self.state)` + `os.replace`，当 `state.json` 达到数 MB 时会造成主事件循环瞬间卡顿数百毫秒。
- **具体后果**:
  多任务并发写 Vault 笔记或工作日志时存在内容覆盖风险。
- **修复方案**:
  为 `Vault` 增加 `threading.RLock()` 并对 `write`/`patch` 采用 `.tmp` + `os.replace` 原子写入。

---

### [SRV-032] [LOW] `bot_identity.py`、`assistants.py` 与 `escalation.py` 边界问题汇总
- **分类**: 边界条件与状态一致性 (`Edge Cases & State Consistency`)
- **精确文件与行号**:
  - `D:\bubuki\proactive-personal-agent\server\bot_identity.py:130-156` (`sync_from_vault`)
  - `D:\bubuki\proactive-personal-agent\server\assistants.py:173-195` (`save_bots`)
  - `D:\bubuki\proactive-personal-agent\server\escalation.py:42-82` (`decide`)
- **根因与代码证据**:
  1. **`bot_identity.py:130-156` (`sync_from_vault`) 反向同步截断多段内容**:
     `sync_from_vault` 按 `\n## ` 切分 `self/bot_identity.md` 并写回 `identity/{bot_id}/` 下的各个文件。如果用户在某个二级章节（如 `## SOUL.md — 核心性格与三观`）的正文内部自己写了二级标题 `\n## 我的准则`，`content.split('\n## ')` 会将其错误切碎并因不匹配 `' — '` 而直接丢弃该二级标题之后的所有正文内容！
  2. **`assistants.py:173-195` (`save_bots`) 删除分身时不清理 `identity/{bot_id}` 与运行时状态**:
     当通过 `PUT /api/bots` 删除某个自定义分身时，`save_bots` 只更新了 `store.state['assistants']`，未清理 `brain.queues_by_bot`、`brain.executions_by_bot` 及对应的活跃任务。
  3. **`escalation.py:55` 凌晨静默期边界**:
     `quiet_hours = now_hour < 8 and level < 2` 硬编码使用服务器本地 `time.localtime().tm_hour`（若容器未设 `TZ=Asia/Shanghai` 则为 UTC 时间 0..7 点，对应北京时间 08:00–15:59 白天工作时段！），导致在未配置容器全局 `TZ` 时，北京时间上午 8 点到下午 4 点之间所有 `level < 2` 的主动关怀被误判为“凌晨静默期”拦截！
- **具体后果**:
  1. `bot_identity.md` 内含用户自建 `## ` 子标题时反向同步会截断丢失后半部分；
  2. 若服务器系统时区为 UTC，`escalation.py` 会在北京时间白天（08:00-16:00）误开启免打扰静默，而在北京时间凌晨照常打扰用户！
- **修复方案**:
  1. `bot_identity.py:137` 改为仅按精确匹配已知章节头 `\n## (IDENTITY|SOUL|USER|MEMORY|TOOLS|HEARTBEAT)\.md — ` 的正则切分；
  2. `escalation.py:42` 使用 `datetime.now(ZoneInfo('Asia/Shanghai')).hour` 获取准确的北京时间小时数。

---

## 附：跨代理联合对账结论汇总表

| 对接方向 | 核心核实项 | 状态 | 对应 Bug 编号 |
| :--- | :--- | :--- | :--- |
| **Auditor-1 <-> Auditor-2** (服务端 <-> PC插件/云桌面) | `pc_fs` (`edit_file` `old`/`new` vs `old_text`/`new_text`, `write_file` `append`, `read_file` 截断与图片乱码) | 已双向证实 | `SRV-002`, `SRV-003`, `SRV-012`, `SRV-014` |
| **Auditor-1 <-> Auditor-2** (服务端 <-> PC插件/云桌面) | `dsh_workspace` (`cua_desktop` `"cua_Status"` 异常吞没挂死 25s, `wake_gui`/`new_session` 未实现, `dsh_core.py` 缺失) | 已双向证实 | `SRV-004`, `SRV-015`, `SRV-016` |
| **Auditor-1 <-> Auditor-2** (服务端 <-> PC插件/云桌面) | `cloud_desktop` (`ComputerClient.upload_file` 缺失, `scroll`/`drag` `nx`/`ny` 与 HTTP 422) | 已双向证实 | `SRV-005`, `SRV-009` |
| **Auditor-1 <-> Auditor-3** (服务端 <-> 安卓虚拟屏) | `phone_workspace` (`open_uri` 双重 RPC 与 `op` 覆盖、`open` 空 `app` 与裸域名、`long_press` 忽略 `target` 与漏标截图、`_vdisplay_currently_open` 提前污染与泄漏、多手机广播冲突) | 已完备取证 | `SRV-018` ~ `SRV-024` |
| **Auditor-1 <-> Auditor-4** (服务端 <-> 安卓主界面/WS) | `/ws/client` (`stopped` 事件忽略、`_clear_bot_stream` 不广播、`messages_truncated` 跨 `source` 误删、`phone2` 通知丢弃、`/api/pairing-info` `fingerprint` 为空) | 已双向证实 | `SRV-026` ~ `SRV-030` |


---
# 【分卷】Auditor-2 (PC插件 index.js / CUA / 云桌面契约) (`audit_2_pc_and_desktop.md`)

# Auditor-2 全量代码审计报告（PC 端插件、CUA 桥接、前端渲染与云桌面）

> **审计范围**：
> - `D:\dsh-baike-plugin\index.js`, `client.js`, `cua-bridge.mjs`, `cua/index.mjs`, `setup.mjs`, `cordis.patch.yml`, `bridge_config.json`, `package.json`
> - `D:\bubuki\proactive-personal-agent\server\computer_desktop.py`, `computer_api.py`, `computer_client.py`, `computer_files.py`, `computer_models.py`, `computer_runtime.py`, `computer_security.py`, `computer_shell.py`, `cloud_os/*`
> - 已完成与 **Auditor-1** (`brain.py` / `harness.py`) 及 **Auditor-4** (`NativePulseScreen.kt` / `NativePulseActivity.kt`) 的双向契约核对。

---

## 第一部分：PC 插件 RPC (`index.js`) 与服务端契约不一致及执行缺陷

### [BUG-A2-01] `CRITICAL` — `pc_fs` 的 `edit_file` 参数名与服务端完全不匹配（100% 必现失败）+ `$` 替换模式篡改缺陷
- **位置**：`D:\dsh-baike-plugin\index.js:713-726` vs `D:\bubuki\proactive-personal-agent\server\harness.py:58-63`, `brain.py:4881-4882, 4954-4955`
- **代码证据**：
  - 服务端 `harness.py:58-63` 定义 `edit_file` 参数为 `{'path': STR, 'old': STR, 'new': STR}`，`brain.py:4882` 原样转发 `_raw_dsh_rpc('pc_fs', {'fs_op': name, **args})`。
  - PC 插件 `index.js:718-723`：
    ```js
    const oldText = String(args.old_text ?? args.target ?? "");
    const newText = String(args.new_text ?? args.replacement ?? "");
    if (!oldText || !raw.includes(oldText)) {
      return { ok: false, error: `未在文件中找到待替换内容 (${targetPath})`, path: targetPath, hostname: HOSTNAME };
    }
    const updated = raw.replace(oldText, newText);
    ```
- **后果**：
  1. `args.old` 与 `args.new` 被完全忽略，`oldText` 恒为 `""`，导致 PC 会话下所有 `edit_file` 调用 100% 报错 `未在文件中找到待替换内容`。
  2. `raw.replace(oldText, newText)` 当 `newText` 含 `$&`, `$'`, `` $` ``, `$$`（如 JS/Bash/正则代码）时会被 JS 引擎当作特殊替换模式解析，静默损坏代码文件。
- **修复方案**：
  - 兼容 `args.old ?? args.old_text ?? args.target` 和 `args.new ?? args.new_text ?? args.replacement`。
  - 使用函数替换 `raw.replace(oldText, () => newText)`（或支持 `args.replace_all` 时使用 `raw.replaceAll(oldText, () => newText)`），并校验唯一匹配。

---

### [BUG-A2-02] `HIGH` — `pc_fs` 的 `write_file` 忽略 `append` 参数，追加写入变成全量覆盖
- **位置**：`D:\dsh-baike-plugin\index.js:707-712` vs `harness.py:52-57`
- **代码证据**：
  ```js
  if (fsOp === "write_file" || fsOp === "write") {
    const content = String(args.content ?? "");
    fs.mkdirSync(path.dirname(targetPath), { recursive: true });
    fs.writeFileSync(targetPath, content, "utf8");
  ```
- **后果**：服务端调用 `write_file(path=..., content=..., append=True)` 时，PC 插件直接覆盖清空原文件，造成用户数据丢失。
- **修复方案**：检查 `Boolean(args.append)`，若为真且文件存在则读取原内容（自动补齐换行符）后追加写入或调用 `fs.appendFileSync`。

---

### [BUG-A2-03] `CRITICAL` — PC 会话文件系统与命令行工具路由脑裂 (Split-Brain) + `pc_fs` 缺失目录/搜索操作
- **位置**：`D:\dsh-baike-plugin\index.js:687-731` vs `brain.py:343-364, 4746-4747, 4879-4884, 4952-4957, 5193-5199`
- **代码证据**：
  1. `index.js:687-731` (`runLocalPcFsOperation`) 只实现了 `read_file`/`write_file`/`edit_file`，未实现 `list_dir`、`glob_files`、`grep_search`。
  2. `brain.py:4879-4884` 在 `src in ('pc', 'pc2')` 时只将 `run_bash`、`read_file`、`write_file`、`edit_file` 路由给 PC，而 `list_dir`、`glob_files`、`grep_search`、`bg_task`、`watchdog_task` 以及 `brain.research()` (`harness.py:run()`) 全部留在云端 Linux 执行！
  3. `brain.py:354-363` (`_should_use_pc_fs`) 当相对路径在云端 `self.harness.root` 下恰好存在同名文件（`if cand.exists(): return False`）或以 `work/`、`workspace/`、`notes/`、`skills/` 开头时，强制在云端读写，而 `run_bash` 却在 Windows PC 本机执行！
  4. `index.js:644-657` (`resolvePcPath`) 将相对路径固定解析到 `~/Documents/deepseek-harness/default-workspace`，且 `read_file` (`index.js:700-705`) 整文件 `fs.readFileSync(targetPath, "utf8")` 无大文件流式保护、不支持读取图片返回视觉格式，未传 `end_line` 时静默截断为 250 行且不返回 `truncated` 标志。
- **后果**：AI 用 `run_bash` 在 PC 上创建/修改文件后，用 `list_dir`/`grep_search`/`read_file` 查看到的是云端 Linux 目录或旧文件，产生严重幻觉与误操作。
- **修复方案**：在 `index.js` 中完整实现 `list_dir`、`glob_files`、`grep_search` 及图片 base64 读取；在 `brain.py` 中对 `src in ('pc', 'pc2')` 统一路由所有文件与搜索工具到 PC，移除 `if cand.exists(): return False` 的跨端混淆逻辑。

---

### [BUG-A2-04] `CRITICAL` — `dsh_workspace` 的 `cua_desktop` / `wake_gui` / `new_session` 契约断裂 + 异常吞没导致服务端 RPC 死锁超时
- **位置**：`D:\dsh-baike-plugin\index.js:1096-1113, 1457-1495`、`D:\dsh-baike-plugin\cua-bridge.mjs:93-124`、`D:\dsh-baike-plugin\cua\index.mjs:387-640` vs `harness.py:295-318`, `brain.py:2704-2863`
- **代码证据**：
  1. `index.js:1108`：`op === "cua_desktop" || op === "desktop" ? await callCuaTool(args.tool || "cua_Status", args)`
     - 本地工具 schema (`index.js:1465`) 用的是 `args.action`，而 RPC 分支读的是 `args.tool`；
     - `cua/index.mjs:387-640` 的 `TOOLS` 中根本不存在 `"cua_Status"` 工具（调用必抛 `-32602 unknown tool: cua_Status`）；
     - `harness.py:295-318` 的 `dsh_workspace` schema (`additionalProperties: False`) 根本没有定义 `action`/`tool`/`window_id`/`element_index`/`x`/`y`/`key` 等 GUI 参数！
  2. `index.js:1108` 调用 `await callCuaTool(...)` 没有 `try/catch`，一旦 `callCuaTool` reject，直接跳入外层 `index.js:1113` 的 `catch {}`，**跳过第 1110 行的 `POST /api/dsh/result`**！
  3. `index.js:1101-1109` 未实现 `op === "wake_gui"` 和 `op === "new_session"`，直接返回 `不支持的桌面端操作`。
  4. `index.js:1096-1111` 在单线程 `while` 循环内串行 `await` 每个命令（如 `dispatch_session` 最长 90s，`pc_shell` 最长 60s），执行期间完全阻塞 Bridge 心跳与并发命令处理。
  5. `package.json:19-20` 声明了 `"dsh_core.py"`, `"dsh_sentinel.py"`（且未包含 `"cua-bridge.mjs"` 和 `"cua"`），但磁盘上根本不存在这两个 `.py` 文件。
- **后果**：云端调用 `cua_desktop` 100% 触发未捕获异常并导致服务端 `_raw_dsh_rpc` 死锁等待 25 秒超时；长耗时命令阻塞整个 PC Bridge 在线状态。
- **修复方案**：
  - 在 `index.js:1096-1111` 中对每个 `cmd` 使用异步并发 IIFE + 内层 `try/catch`，确保任何异常都回传 `{ ok: false, error }` 到 `/api/dsh/result`；
  - 将 `args.tool || "cua_Status"` 修正为 `args.action || args.tool || "list_windows"`，并补齐 `wake_gui`、`new_session` 分支及 `harness.py` 的 `dsh_workspace` schema 参数。

---

### [BUG-A2-05] `HIGH` — Windows `pc_shell` 编码、超时进程树残留与 `stderr` 退出码误判
- **位置**：`D:\dsh-baike-plugin\index.js:659-685`
- **代码证据**：
  ```js
  const wrapped = `[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; ${cmdStr}`;
  const child = execFile("powershell.exe", ["-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", wrapped],
    { cwd, timeout: timeoutMs, maxBuffer: 4 * 1024 * 1024, windowsHide: true },
    (err, stdout, stderr) => {
      resolve({
        ok: !err || Boolean(stdout),
        exit_code: err && typeof err.code === "number" ? err.code : 0,
  ```
- **后果**：
  1. 只设置了 `[Console]::OutputEncoding`，未设置 `[Console]::InputEncoding` 和 `$OutputEncoding = [System.Text.Encoding]::UTF8`，管道传递中文给原生程序时仍可能乱码。
  2. `ok: !err || Boolean(stdout)`：即使命令执行失败（非零退出码或超时被杀），只要前面输出过一行 `stdout`，`ok` 就被设为 `true`，且当 `err.code` 为字符串（如 `'ETIMEDOUT'`）时 `exit_code` 被伪造为 `0`！
  3. Node.js 在 Windows 下 `execFile` 超时默认仅终止顶层 `powershell.exe`，其派生的子进程树（如后台构建或长命令）会残留在系统中成为僵尸进程。
- **修复方案**：
  - 命令前缀补齐 `[Console]::InputEncoding = [Console]::OutputEncoding = $OutputEncoding = [System.Text.Encoding]::UTF8;`；
  - `ok: !err`，超时或信号终止时 `exit_code: -1` 并返回明确 `timed_out: true` 与 `error`；超时后执行 `taskkill /PID <pid> /T /F` 清理进程树。

---

## 第二部分：PC 前端 (`client.js` & `index.js`) 流式状态、渲染与交互缺陷

### [BUG-A2-06] `HIGH` — 后台同步 `streams_by_bot` 漏拷 `speaker_id`/`speaker_node` + 非本机触发的流式输出在 PC 前端完全不显示
- **位置**：`D:\dsh-baike-plugin\index.js:1195-1201`、`D:\dsh-baike-plugin\client.js:936, 963-974, 1055-1094`
- **代码证据**：
  1. `index.js:1195-1201` 在 `/local/infinite-chat/sync` 后台轮询中构建 `runtimeState.liveStreamsByBot[localB.id]` 时，只拷贝了 `text, thought, tool, trace, speaker_name`，**漏掉了 `speaker_id` 和 `speaker_node`**（对比 `index.js:820-822` 包含这两个字段）。
  2. `client.js:963, 1055` 判断是否渲染流式气泡 `#dsb-live-turn-slot` 的条件写成了 `if (!isSending)`（即仅当本机前端处于 `state.sendingByBot[bot.id] === true` 时才显示），而不是 `if (!isSending && !liveSt)`！
- **后果**：
  - 当手机端或跨 Bot 协同向 PC Bot 派发任务并产生流式输出时，虽然 `state.streamsByBot[bot.id]` 有实时流数据，但因 `isSending === false`，PC 前端完全不渲染流式气泡；
  - 后台同步流缺少 `speaker_id` 和 `speaker_node` 导致跨端/子代理头像与右下角角标（`phone`/`session`/`colleague`）回退错误。
- **修复方案**：在 `index.js:1195-1201` 补齐 `speaker_id` 和 `speaker_node`；在 `client.js:936, 963, 1055` 将激活条件改为 `var hasLiveTurn = isSending || Boolean(liveSt && (liveSt.text || liveSt.thought || liveSt.tool || (liveSt.trace && liveSt.trace.length)))`。

---

### [BUG-A2-07] `HIGH` — PC 前端完全缺失 Markdown 渲染、代码块复制、图片/贴纸 (`sticker`)、附件上传与选择题 (`choices`)
- **位置**：`D:\dsh-baike-plugin\client.js:75-99, 101-149, 1007-1041`、`D:\dsh-baike-plugin\index.js:503-551, 553-642`（与 Auditor-4 安卓端对比）
- **代码证据**：
  1. `client.js:75-99` (`renderRichMessageHtml`) 仅提取 `<svg>...</svg>`，其余内容一律 `escapeHtml` 原样输出为纯文本，不支持 Markdown 标题/列表/表格/代码块高亮，代码块无复制按钮，整条消息也无复制按钮。
  2. `index.js:510, 571` (`if (!text) continue;`) 直接丢弃所有无文本的纯图片/贴纸 (`sticker`) 消息，且不保存 `sticker`、`attachments`、`choices` 字段。
  3. `client.js:101-149` (`renderThoughtAndTraceHtml`) 的工具胶囊仅显示工具名，无法展开查看 `arguments`、`result`、`duration_ms` 或报错详情。
- **后果**：AI 输出的 Markdown 在 PC 端显示为裸符号源码 (`**`, ` ``` `)，生成的图片、表情包、选择题卡片在 PC 端全部丢失或无法交互。
- **修复方案**：在 `index.js` 保留 `sticker`、`attachments`、`choices` 字段（允许 `text` 为空但有 `sticker`/`attachments` 的消息）；在 `client.js` 实现轻量安全的 Markdown + 代码块一键复制 + 图片/贴纸/选择题卡片渲染，并支持点击工具胶囊查看入参与结果。

---

### [BUG-A2-08] `MEDIUM` — `cua/index.mjs` 缺失 `hud-banner.ps1` 且 CUA 截图未被 `cua_desktop` 工具渲染给模型
- **位置**：`D:\dsh-baike-plugin\cua\index.mjs:26, 80-99`、`D:\dsh-baike-plugin\index.js:1409-1412, 1486-1494`
- **代码证据**：
  1. `cua/index.mjs:26` 引用 `const HUD_SCRIPT = path.join(HERE, "hud-banner.ps1")`，但 `D:\dsh-baike-plugin\cua\` 下不存在该文件。
  2. `index.js:1411` 的 `outSchema.render: (_a, v) => [{ type: "text", text: v.summary }]` 只渲染文本 `v.summary`，当 `cua_desktop(action="get_window_state", include_screenshot=true)` 返回 `res.images` (`cua-bridge.mjs:114`) 时，截图被彻底丢弃，模型无法看到窗口画面。
- **修复方案**：在 `outSchema.render` 中将 `v.data?.images` 一并映射为图像 content block 返回给模型；补齐或移除无效的 `hud-banner.ps1` 引用。

---

## 第三部分：云桌面 (`server/computer_*.py` & `cloud_os/*`) 契约与实现缺陷

### [BUG-A2-09] `CRITICAL` — `ComputerClient.upload_file` 方法缺失，调用时必抛 `AttributeError`
- **位置**：`D:\bubuki\proactive-personal-agent\server\computer_client.py:9-60` vs `brain.py` (`computer.client.upload_file`)
- **代码证据**：
  - `computer_client.py` 中的 `ComputerClient` 仅实现了 `__init__`, `configured`, `call`, `status`, `get_file(self, path, target: Path)`，**完全没有定义 `upload_file` 方法**！
  - 而 `computer_files.py:46-78` 与 `computer_api.py:167-174` 要求分三步调用 `POST /library/uploads` -> `PUT /library/uploads/{key}` -> `POST /library/uploads/{key}/finish`。
- **后果**：当 `brain.py` 尝试向云桌面上传文件调用 `self.computer.client.upload_file(src_file)` 时，直接抛出 `AttributeError: 'ComputerClient' object has no attribute 'upload_file'`。
- **修复方案**：在 `ComputerClient` (`computer_client.py`) 中实现 `async def upload_file(self, src_file: Path, name: str | None = None) -> dict`，内部计算 `sha256` 并按 256KB 分块调用 `/library/uploads`、`/library/uploads/{key}` 和 `/library/uploads/{key}/finish`。

---

### [BUG-A2-10] `HIGH` — 云桌面 `InputAction` 坐标/方向/滚动量校验与 `drag`/`scroll` 归一化坐标断裂
- **位置**：`D:\bubuki\proactive-personal-agent\server\computer_models.py:22-37`、`computer_desktop.py:1269-1276` vs `brain.py:2079-2103`
- **代码证据**：
  1. `computer_models.py:22-25`：
     ```python
     x: int = Field(0, ge=0, lt=WIDTH)   # lt=1280
     y: int = Field(0, ge=0, lt=HEIGHT)  # lt=800
     x2: int = Field(0, ge=0, lt=WIDTH)
     y2: int = Field(0, ge=0, lt=HEIGHT)
     ```
     当模型将 `0..1000` 归一化坐标传入 `y` 或 `y2`（如 `y=850`）或屏幕右/下边缘（`x=1280` 或 `y=800`）时，Pydantic 直接抛出 **HTTP 422 ValidationError**！
  2. `computer_models.py` 没有 `nx2, ny2` 字段，且 `computer_desktop.py:1272` 的 `drag` 终点 `(x2, y2)` 未经过 `resolve_grid_or_norm_xy` 转换；同时 `brain.py:2086-2092` 在 `op == 'drag'` 时未传递 `nx, ny`，若模型传归一化坐标会导致起点或终点变成 `(0, 0)`！
  3. `computer_models.py:35-36` 限定 `direction: Literal['up', 'down'] = 'down'` 且 `amount: int = Field(3, ge=1, le=12)`，不支持水平滚动 `'left'|'right'`，且若传入 `amount > 12` 直接触发 HTTP 422；`brain.py:2097-2103` 在 `op == 'scroll'` 时还忽略了 `nx, ny` 固定滚动 `(640, 400)`。
- **修复方案**：
  - 在 `computer_models.py` 中放宽 `x, y, x2, y2` 范围并在 `computer_desktop.py` 中统一 clamp；新增 `nx2, ny2` 并对 `drag` 终点调用 `resolve_grid_or_norm_xy`；
  - `direction` 支持 `'up', 'down', 'left', 'right'`（对应 `xdotool click 4/5/6/7`），`amount` 在内部 clamp 到 `1..20` 而非直接 422 拒绝。

---

### [BUG-A2-11] `MEDIUM` — 云桌面分辨率常量不一致 (`1280x800` vs `1280x720`) 导致窗口平铺与浏览器视口底部留白/偏移
- **位置**：`D:\bubuki\proactive-personal-agent\server\computer_models.py:4` (`HEIGHT = 800`) vs `computer_desktop.py:105` (`1280×720`)、`cloud_os/agent_browser.py:570, 740` (`height: int = 720`)、`cloud_os/subsystem.py:111` (`--window-size=1252,736`)、`cloud_os/window_manager.py:180` (`height: int = 720`)
- **代码证据**：
  - `Xvfb` 实际以 `1280x800` 启动 (`computer_models.py:4`)，且 `agent_browser.py:236` 归一化坐标按 `800` 计算：`ny = Math.round((cy * 1000) / 800)`；
  - 但 `subsystem.py:111` 的 Chromium 包装脚本硬编码 `--window-size=1252,736`（按 720p 计算），`window_manager.py:180` 和 `agent_browser.py:570` 默认 `height=720`。
- **后果**：通过命令行启动 Chromium 或未显式传 `height=800` 时，窗口高度比 800p 桌面少 64px，且与底栏任务栏位置不对齐。
- **修复方案**：将 `agent_browser.py`、`window_manager.py`、`subsystem.py`、`HOME_HTML` 中的默认高度统一为 `800`（窗口高度 `800 - 64 = 736` 或与 `WIDTH, HEIGHT` 动态绑定）。


---
# 【分卷】Auditor-3 (安卓虚拟屏 FloatingVirtualDisplay / DisplayRuntime / RootPhoneHarness / AgentForegroundService) (`audit_3_android_vdisplay_harness.md`)

# Auditor-3 深度全量代码审计报告：安卓虚拟屏引擎、无障碍/Root 执行器与后台服务

> **审计领地**：
> - [FloatingVirtualDisplay.kt](file:///D:/bubuki/proactive-personal-agent/android/app/src/main/java/dev/bubuki/proactive/FloatingVirtualDisplay.kt)（1-1756行）
> - [DisplayRuntime.java](file:///D:/bubuki/proactive-personal-agent/android/app/src/main/java/dev/bubuki/proactive/DisplayRuntime.java)（1-1312行）
> - [RootPhoneHarness.kt](file:///D:/bubuki/proactive-personal-agent/android/app/src/main/java/dev/bubuki/proactive/RootPhoneHarness.kt)（1-1149行）
> - [AgentForegroundService.java](file:///D:/bubuki/proactive-personal-agent/android/app/src/main/java/dev/bubuki/proactive/AgentForegroundService.java)（1-396行）
> - [PulseAccessibilityService.kt](file:///D:/bubuki/proactive-personal-agent/android/app/src/main/java/dev/bubuki/proactive/PulseAccessibilityService.kt)（1-561行）
> - [RootPhoneScreen.kt](file:///D:/bubuki/proactive-personal-agent/android/app/src/main/java/dev/bubuki/proactive/RootPhoneScreen.kt)、[PrivilegeManager.java](file:///D:/bubuki/proactive-personal-agent/android/app/src/main/java/dev/bubuki/proactive/PrivilegeManager.java)、[RootPhoneAgent.kt](file:///D:/bubuki/proactive-personal-agent/android/app/src/main/java/dev/bubuki/proactive/RootPhoneAgent.kt)、[OverlayController.java](file:///D:/bubuki/proactive-personal-agent/android/app/src/main/java/dev/bubuki/proactive/OverlayController.java)、[RootDisplayServer.java](file:///D:/bubuki/proactive-personal-agent/android/app/src/main/java/dev/bubuki/proactive/RootDisplayServer.java)、[RootPhoneService.kt](file:///D:/bubuki/proactive-personal-agent/android/app/src/main/java/dev/bubuki/proactive/RootPhoneService.kt)、[RootClipboardBroker.java](file:///D:/bubuki/proactive-personal-agent/android/app/src/main/java/dev/bubuki/proactive/RootClipboardBroker.java)、[RootClipboard.java](file:///D:/bubuki/proactive-personal-agent/android/app/src/main/java/dev/bubuki/proactive/RootClipboard.java)、[RootCommands.java](file:///D:/bubuki/proactive-personal-agent/android/app/src/main/java/dev/bubuki/proactive/RootCommands.java)、[DisplayTestActivity.java](file:///D:/bubuki/proactive-personal-agent/android/app/src/main/java/dev/bubuki/proactive/DisplayTestActivity.java)

---

## 一、核心专题根因拆解：为什么虚拟屏“莫名其妙弹出来”且“甚至关不掉”？

经过逐行代码比对，虚拟屏“幽灵弹出”和“死活关不掉”并非单一原因，而是由 **5 个环环相扣的设计冲突与状态机闭环漏洞** 共同导致的：

1. **假释放（Fake Release）导致 `created` 永远为 `true`（[DisplayRuntime.java:512-514, 1042, 1118-1127](file:///D:/bubuki/proactive-personal-agent/android/app/src/main/java/dev/bubuki/proactive/DisplayRuntime.java#L1118-L1127)）**：
   - 当执行 `op: "release"`（且未传 `destroy: true`）时，`DisplayRuntime.release(false)` **故意不释放 `this.display`**（`this.display` 依然非 null），只解绑了 `ImageReader` 回调。
   - 然而 `DisplayRuntime.state()`（第 1042 行）判断 `.put("created", vd != null)`！这意味着一旦虚拟屏创建过一次，无论调用多少次 `release`，`state()` 返回的 `"created"` **永远是 `true`**，`displayId` 也永远 `> 0`！
2. **展开态右上角红色 `×` 按钮（`POWER_CLOSE`）根本不调用 `hide()`，而是变成胶囊（Pill）！（[FloatingVirtualDisplay.kt:769-777](file:///D:/bubuki/proactive-personal-agent/android/app/src/main/java/dev/bubuki/proactive/FloatingVirtualDisplay.kt#L769-L777)）**：
   - 展开态右上角的红色关闭按钮点击回调写的竟然是：`minimizedToPill = true; transitionSizeAndRebuild(...); command("release")`！它既没有调用 `hide()` 移除悬浮窗，又因为后台 `release` 后 `created` 依然是 `true`，所以点击红色 `×` 后窗口**根本不会消失**，而是缩成一个右下角胶囊继续挂在屏幕上！
   - 更离谱的是，应用内降级组件 `InAppFloatingVirtualDisplay`（[FloatingVirtualDisplay.kt:1712-1719](file:///D:/bubuki/proactive-personal-agent/android/app/src/main/java/dev/bubuki/proactive/FloatingVirtualDisplay.kt#L1712-L1719)）点击红色 `×` 只设置了 `expanded = false` 并发送 `release`，既没有 `hiddenToPill = true` 也没有本地隐藏状态，而 `visible` 取决于 `created`（永远为 `true`），导致应用内虚拟屏点 `×` 只能缩回小窗，**彻底无法关闭**！
3. **小窗模式（Mini-PiP，`expanded = false`）没有任何关闭或收起按钮（[FloatingVirtualDisplay.kt:675-696](file:///D:/bubuki/proactive-personal-agent/android/app/src/main/java/dev/bubuki/proactive/FloatingVirtualDisplay.kt#L675-L696) & [1590-1699](file:///D:/bubuki/proactive-personal-agent/android/app/src/main/java/dev/bubuki/proactive/FloatingVirtualDisplay.kt#L1590-L1699)）**：
   - 当悬浮窗处于默认的小窗模式（`!expanded && !minimizedToPill`）时，界面上**没有任何关闭或隐藏按钮**。用户点击小窗只会触发 `onTap = { expanded = true }` 把小窗放大。
4. **任何被动查询或无效指令都会强行自动 `create` 并弹出黑屏虚拟屏（[AgentForegroundService.java:198-210](file:///D:/bubuki/proactive-personal-agent/android/app/src/main/java/dev/bubuki/proactive/AgentForegroundService.java#L198-L210) & [RootPhoneHarness.kt:840-846](file:///D:/bubuki/proactive-personal-agent/android/app/src/main/java/dev/bubuki/proactive/RootPhoneHarness.kt#L840-L846)）**：
   - `AgentForegroundService.java` 第 198 行对 `"capture", "ui_tree", "dump", "tap", ..., "launch"` 等所有操作，只要发现 `displayId <= 0`，就无条件先调用 `RootPhoneHarness.command(this, new JSONObject().put("op", "create"))`！
   - 而 `RootPhoneHarness.kt` 第 846 行在 `op == "create"` 成功后，无条件执行 `FloatingVirtualDisplay.show(base.applicationContext)`，这会**强制把 `userDismissed = false; minimizedToPill = false` 重置并立即弹出系统悬浮窗**！
   - 哪怕接下来的操作是被动读取（`ui_tree` / `capture`）或者是一个 `app=""` 的无效 `launch`（在第 802 行 `resolveLaunchComponent` 抛异常失败），第 206 行的 `create` 已经执行完毕，导致屏幕上莫名其妙弹出一个写着“虚拟屏”的黑屏悬浮窗！
5. **熄屏自动释放 + 后续任意状态更新导致 `userDismissed` 失效或反复拉起（[AgentForegroundService.java:80-83](file:///D:/bubuki/proactive-personal-agent/android/app/src/main/java/dev/bubuki/proactive/AgentForegroundService.java#L80-L83) & [FloatingVirtualDisplay.kt:440-457](file:///D:/bubuki/proactive-personal-agent/android/app/src/main/java/dev/bubuki/proactive/FloatingVirtualDisplay.kt#L440-L457)）**：
   - 每次熄屏（`ACTION_SCREEN_OFF`）都会触发 `scheduleAutoRelease` -> `command("release")` -> `onWorkspaceStateChanged`。由于 `release` 不销毁虚拟屏（`created` 仍为 `true`），如果此前没有点胶囊上的 `×`（例如只是缩成胶囊），或者一旦收到任何 `create` 指令，`userDismissed` 就会被清零，悬浮窗再次强行挂载。

---

## 二、全量 Bug 清单（逐条编号、定级、精确行号与修复方案）

### 【BUG-A3-01】`CRITICAL` — 假释放悖论：`DisplayRuntime.release(false)` 不释放 `VirtualDisplay` 且无 `released` 状态位，导致 `state()` 中 `"created"` 永远为 `true`
- **类别**：State Machine Split-Brain / Lifecycle Leak
- **精确位置**：
  - [DisplayRuntime.java:508-515](file:///D:/bubuki/proactive-personal-agent/android/app/src/main/java/dev/bubuki/proactive/DisplayRuntime.java#L508-L515)
  - [DisplayRuntime.java:1036-1052](file:///D:/bubuki/proactive-personal-agent/android/app/src/main/java/dev/bubuki/proactive/DisplayRuntime.java#L1036-L1052)
  - [DisplayRuntime.java:1113-1127](file:///D:/bubuki/proactive-personal-agent/android/app/src/main/java/dev/bubuki/proactive/DisplayRuntime.java#L1113-L1127)
  - [RootPhoneHarness.kt:440-456, 847-855, 1091-1109](file:///D:/bubuki/proactive-personal-agent/android/app/src/main/java/dev/bubuki/proactive/RootPhoneHarness.kt#L847-L855)
- **根因与代码证据**：
  ```java
  // DisplayRuntime.java:508-514
  } else if (op.equals("release") || op.equals("promote") || op.equals("expand_to_foreground")) {
      if (display == null) {
          return state().put("ok", true).put("noop", true).put("released_to_background", true);
      }
      boolean destroy = cmd.optBoolean("destroy", false);
      release(destroy);
      return state().put("ok", true).put("released_to_background", true).put("destroyed", destroy);
  }
  // DisplayRuntime.java:1118-1127
  if (!destroy && display != null) {
      synchronized (frameLock) {
          if (reader != null) {
              try { reader.setOnImageAvailableListener(null, null); } catch (Throwable ignored) {}
          }
      }
      return "";
  }
  // DisplayRuntime.java:1041-1043
  return new JSONObject()
      .put("created", vd != null)
      .put("displayId", (vd == null || vd.getDisplay() == null) ? -1 : vd.getDisplay().getDisplayId())
  ```
  当调用 `op="release"` 时，默认 `destroy=false`，`this.display` 保持非空。随后的 `state()` 依然返回 `"created": true, "displayId": <id>`。
  而 `FloatingVirtualDisplay.kt:444-455`、`ensureWatching`（463行）、`InAppFloatingVirtualDisplay`（1417行）、`RootPhoneScreen.kt`（41行）以及 `RootPhoneHarness.isVirtualDisplayActive()`（80行）**全部依赖 `optBoolean("created")` 来判断虚拟屏是否开启**！
- **具体后果**：
  1. 执行 `release` 后，所有 UI 组件（悬浮窗、应用内小窗、工作区设置页、后台轮询）都认为虚拟屏**仍然处于开启（`created=true`）状态**！
  2. `FloatingVirtualDisplay.onWorkspaceStateChanged`（445行）檢查 `if (!created)` 永远不成立，导致 `release` 时**绝对不会调用 `removeWindow()`**！
  3. 45秒空闲自动释放看门狗 `startIdleWatchdog`（207行）在超时后调用 `command("release")`，结果 `created` 还是 `true`，悬浮窗根本不关闭！
  4. 任务结束 2.5 秒后的 `scheduleAutoRelease(base, 2500L)`（1083行）调用 `command("release")`，悬浮窗同样根本不关闭！
- **修复方案**：
  - 在 `DisplayRuntime.java` 中增加 `private volatile boolean releasedToBackground = false;`：
    - `create` 时设为 `releasedToBackground = false`；
    - `release(false)` 时设为 `releasedToBackground = true`；
    - `state()` 中返回 `.put("created", vd != null && !releasedToBackground).put("allocated", vd != null).put("released", releasedToBackground)`，或者当用户点击关闭/释放时明确销毁或将 `active/visible` 与底层后台缓存的 `VirtualDisplay` 解耦。

---

### 【BUG-A3-02】`CRITICAL` — 展开态右上角红色关闭按钮 `POWER_CLOSE` 与应用内关闭按钮点击后不关闭窗口
- **类别**：UI/UX Defect / State Machine Deadlock
- **精确位置**：
  - [FloatingVirtualDisplay.kt:769-778](file:///D:/bubuki/proactive-personal-agent/android/app/src/main/java/dev/bubuki/proactive/FloatingVirtualDisplay.kt#L769-L778)
  - [FloatingVirtualDisplay.kt:1419-1427, 1712-1725](file:///D:/bubuki/proactive-personal-agent/android/app/src/main/java/dev/bubuki/proactive/FloatingVirtualDisplay.kt#L1712-L1725)
- **根因与代码证据**：
  1. 系统级悬浮窗 `FloatingVirtualDisplay` 展开态的红色 `×` 按钮（769-778行）：
     ```kotlin
     val closeDisplayBtn = PipVectorIconButton(context, PipVectorIconButton.IconKind.POWER_CLOSE, isDanger = true) {
         pickingApp = false
         expanded = false
         minimizedToPill = true
         transitionSizeAndRebuild(context, RootPhoneHarness.changes.value)
         scope.launch(Dispatchers.IO) {
             runCatching { RootPhoneHarness.command(context, JSONObject().put("op", "release")) }
         }
     }
     ```
     对比胶囊态的关闭按钮（583-588行，调用了 `hide()`），展开态的红色 `×` 按钮**居然没有调用 `hide()`**，而是设为了 `minimizedToPill = true`（跟左上角 `EYE_OFF` 按钮行为一模一样）！再叠加 `op="release"` 后 `created` 仍为 `true`（BUG-A3-01），点 `×` 永远只能缩成胶囊，关不掉悬浮窗！
  2. 应用内悬浮窗 `InAppFloatingVirtualDisplay`（1419, 1712-1725行）：
     ```kotlin
     val visible = created && state.phase != "stopping" && state.phase != "stopped" && !systemOverlayShowing
     ...
     ComposePipCircleButton(
         isDanger = true,
         onClick = {
             expanded = false
             scope.launch(Dispatchers.IO) {
                 runCatching { RootPhoneHarness.command(context, JSONObject().put("op", "release")) }
             }
         }
     )
     ```
     在 `InAppFloatingVirtualDisplay` 中：
     - 胶囊态（1455-1477行）**根本没有关闭按钮**；
     - 展开态点红色 `×` 只把 `expanded = false`（缩回小窗），然后发 `op="release"`；因为 `release` 后 `created` 依然是 `true`，`visible` 依然是 `true`，所以应用内悬浮窗**永远无法关闭**！
- **具体后果**：
  用户无论在系统悬浮窗展开态还是应用内悬浮窗点击右上角红色 `×`，都无法关闭虚拟屏窗口。
- **修复方案**：
  - 在 `FloatingVirtualDisplay.kt:769` 的 `closeDisplayBtn` 中直接调用 `hide()`（设置 `userDismissed = true` 并 `removeWindow()`），并在后台发送 `release`；
  - 在 `InAppFloatingVirtualDisplay` 中增加 `var dismissed by remember { mutableStateOf(false) }`（或统一读取 `FloatingVirtualDisplay` 的 `userDismissed` `StateFlow`），点击红色 `×` 时设置 `dismissed = true` 并调用 `FloatingVirtualDisplay.hide()`；同时在胶囊态也补齐关闭按钮。

---

### 【BUG-A3-03】`HIGH` — 小窗模式（Mini-PiP，`!expanded`）下无任何关闭或收起按钮，点击即强制展开
- **类别**：UI/UX Defect
- **精确位置**：
  - [FloatingVirtualDisplay.kt:675-696](file:///D:/bubuki/proactive-personal-agent/android/app/src/main/java/dev/bubuki/proactive/FloatingVirtualDisplay.kt#L675-L696)
  - [FloatingVirtualDisplay.kt:1590-1593](file:///D:/bubuki/proactive-personal-agent/android/app/src/main/java/dev/bubuki/proactive/FloatingVirtualDisplay.kt#L1590-L1593)
- **根因与代码证据**：
  虚拟屏首次 `create` / `show(context, forceExpand = false)` 时，默认以 `!expanded && !minimizedToPill`（小窗模式）弹出在屏幕右上角。
  在 675-696 行中，当 `!expanded` 时，除了 `waiting_user` 标签外，**没有添加任何关闭按钮或最小化按钮**，且整个 `glassFrame` 绑定了 `onTap = { expanded = true }`。
- **具体后果**：
  小窗弹出后，用户无法在小窗上直接点关闭，必须先点一下小窗让它放大占满大半个屏幕，再去点右上角按钮（而且点 `×` 还会触发 BUG-A3-02 变成胶囊，还得再点一次胶囊上的 `×` 才能真正关掉，总共需要点 3 次！）。
- **修复方案**：
  在小窗模式（`!expanded`）的右上角同样提供一个紧凑的 `POWER_CLOSE`（`×`）按钮，允许一键关闭虚拟屏。

---

### 【BUG-A3-04】`CRITICAL` — `AgentForegroundService` 对被动查询（`ui_tree`/`capture`）和未校验参数的 `launch` 无条件自动 `create` 虚拟屏并强行弹窗
- **类别**：Routing / Auto-Popup Defect
- **精确位置**：
  - [AgentForegroundService.java:198-220](file:///D:/bubuki/proactive-personal-agent/android/app/src/main/java/dev/bubuki/proactive/AgentForegroundService.java#L198-L220)
  - [RootPhoneHarness.kt:840-846](file:///D:/bubuki/proactive-personal-agent/android/app/src/main/java/dev/bubuki/proactive/RootPhoneHarness.kt#L840-L846)
  - [FloatingVirtualDisplay.kt:407-418](file:///D:/bubuki/proactive-personal-agent/android/app/src/main/java/dev/bubuki/proactive/FloatingVirtualDisplay.kt#L407-L418)
- **根因与代码证据**：
  ```java
  // AgentForegroundService.java:198-208
  if(Arrays.asList("capture","ui_tree","dump","tap","tap_node","tap_and_type","long_press","batch","swipe","text","type_node","key","launch","open_uri","open_url").contains(op)){
      JSONObject st=RootPhoneHarness.status(this);
      ...
      JSONObject disp=st.optJSONObject("display");
      if(disp==null||disp.optInt("displayId",-1)<=0){
          JSONObject created=RootPhoneHarness.command(this,new JSONObject().put("op","create"));
          dispIdForSnap=created.optInt("displayId",-1);
      }
  ```
  并且一旦执行 `RootPhoneHarness.command(..., {"op": "create"})`：
  ```kotlin
  // RootPhoneHarness.kt:840-846
  if (operation == "create" && result.optBoolean("created")) {
      ...
      FloatingVirtualDisplay.show(base.applicationContext)
  }
  ```
  而 `FloatingVirtualDisplay.show()`（407-418行）会立即将 `userDismissed = false; minimizedToPill = false` 并挂载悬浮窗！
- **具体后果**：
  1. **被动查询触发弹窗**：当云端 Agent 只是想调 `ui_tree`、`dump`、`capture` 看看有没有东西，或者误调了一次 `phone_workspace`，即使虚拟屏根本没开、里面没有任何 App，`AgentForegroundService` 也会强行 `create` 并弹出一个写着“虚拟屏”的黑屏小窗！
  2. **无效 `launch` 触发黑屏弹窗**：当云端传了 `op="launch"` 但 `app=""` / `package=""` 或应用不存在时，`AgentForegroundService` 在第 206 行**先创建并弹出了虚拟屏**，然后第 220 行调用 `RootPhoneHarness.command(this, input)` 在第 802 行 `resolveLaunchComponent` 抛出异常 `"请提供要打开的应用名或组件名"`。结果命令失败了，但黑屏虚拟屏已经被弹出来挂在桌面上了！
  3. **用户手动关闭后被再次强行拉起**：如果虚拟屏之前被 `release` 过（或一旦 `created` 变为 `false`），或者任何地方调用了 `op="create"`（例如用户点了一下胶囊 595 行、或者云端发来命令），都会重置 `userDismissed = false` 再次弹窗。
- **修复方案**：
  1. **只有真正启动应用的动作（`launch`, `open_uri`, `open_url`）在校验目标应用/URI 合法且存在之后**，才允许自动创建并显示虚拟屏！
  2. 当虚拟屏未创建或未运行任何应用时，对 `ui_tree`、`dump`、`capture`、`tap`、`swipe`、`key` 等操作直接返回明确错误提示（如 `"虚拟屏尚未启动任何应用，请先调用 launch 打开目标应用"`），严禁自动 `create` 黑屏虚拟屏！
  3. 在 `AgentForegroundService.java` 中，如果 `op` 是 `launch`，必须先验证 `resolveLaunchComponent` 成功（或把自动 `create` 移到 `RootPhoneHarness.command` 中 `resolveLaunchComponent` 校验通过之后），再执行 `create` 和 `show`！

---

### 【BUG-A3-05】`HIGH` — 字段命名分裂：`display_id` vs `displayId`，导致 `RootPhoneState.displayId` 永远返回 `0`
- **类别**：Contract Mismatch (snake_case vs camelCase)
- **精确位置**：
  - [DisplayRuntime.java:1043](file:///D:/bubuki/proactive-personal-agent/android/app/src/main/java/dev/bubuki/proactive/DisplayRuntime.java#L1043)
  - [RootPhoneHarness.kt:36](file:///D:/bubuki/proactive-personal-agent/android/app/src/main/java/dev/bubuki/proactive/RootPhoneHarness.kt#L36)
  - [RootPhoneAgent.kt:85, 103-113](file:///D:/bubuki/proactive-personal-agent/android/app/src/main/java/dev/bubuki/proactive/RootPhoneAgent.kt#L85)
- **根因与代码证据**：
  `DisplayRuntime.state()`（第 1043 行）输出的 JSON 字段名是驼峰 **`"displayId"`**：
  ```java
  .put("displayId", (vd == null || vd.getDisplay() == null) ? -1 : vd.getDisplay().getDisplayId())
  ```
  但 `RootPhoneState`（[RootPhoneHarness.kt:36](file:///D:/bubuki/proactive-personal-agent/android/app/src/main/java/dev/bubuki/proactive/RootPhoneHarness.kt#L36)）读取的却是下划线 **`"display_id"`**：
  ```kotlin
  val displayId: Int get() = runCatching { JSONObject(display).optInt("display_id", 0) }.getOrDefault(0)
  ```
- **具体后果**：
  `RootPhoneState.displayId` 属性在任何时候读取都恒为 `0`（主屏幕 ID）！任何依赖 `state.displayId` 的外部调用者都会误以为虚拟屏未创建或拿到 `displayId = 0`。
- **修复方案**：
  `RootPhoneState.displayId` 改为 `JSONObject(display).let { it.optInt("displayId", it.optInt("display_id", -1)) }`，同时在 `DisplayRuntime.state()` 中同时输出 `"displayId"` 和 `"display_id"` 以兼容两端。

---

### 【BUG-A3-06】`HIGH` — `tap_and_type` 提交回车时发送 `"keycode": 66`，但 `DisplayRuntime.java` 只认 `"key": "enter"`，导致抛异常崩溃！
- **类别**：Contract Mismatch / Broken Feature
- **精确位置**：
  - [RootPhoneHarness.kt:688-691](file:///D:/bubuki/proactive-personal-agent/android/app/src/main/java/dev/bubuki/proactive/RootPhoneHarness.kt#L688-L691)
  - [DisplayRuntime.java:585-606](file:///D:/bubuki/proactive-personal-agent/android/app/src/main/java/dev/bubuki/proactive/DisplayRuntime.java#L585-L606)
- **根因与代码证据**：
  在 `RootPhoneHarness.kt` 的 `tap_and_type` 分支中，当 `submit=true` / `press_enter=true` / `send=true` 时（第 690 行）：
  ```kotlin
  if (input.optBoolean("submit", false) || input.optBoolean("press_enter", false) || input.optBoolean("send", false)) {
      Thread.sleep(200)
      privilege!!.execute(JSONObject().put("op", "key").put("display_id", currentDisplay).put("keycode", 66))
  }
  ```
  然而 `DisplayRuntime.java` 的 `op.equals("key")` 分支（第 588 行）使用的是 `cmd.getString("key")`（必填字符串，只支持 `"enter"`, `"back"`, `"home"` 等枚举）：
  ```java
  } else if (op.equals("key")) {
      int id = requireDisplay();
      checkTarget(cmd, id);
      String key = cmd.getString("key").toLowerCase();
  ```
- **具体后果**：
  只要调用 `tap_and_type` 并传入 `submit=true` / `press_enter=true` / `send=true`，在输入完文字准备按回车时，`DisplayRuntime` 会因为找不到 `"key"` 字段直接抛出 `JSONException: No value for key`，导致整个 `tap_and_type` 动作报错失败！
  同理，如果云端传给 `op="key"` 的参数是 `key_code` 或 `keycode` 或 `"search"`，也会在 `DisplayRuntime.java:588` 抛出异常！
- **修复方案**：
  1. `RootPhoneHarness.kt:690` 改为 `.put("key", "enter")`；
  2. `DisplayRuntime.java:588` 兼容 `key` 字符串与 `keycode` / `key_code` 整数（以及 `"search"` -> `KEYCODE_SEARCH` / `KEYCODE_ENTER`）。

---

### 【BUG-A3-07】`HIGH` — `swipe` 参数名与归一化坐标/网格坐标转换漏洞（未转换 `x1, y1, x2, y2` 及不支持 `start_x, start_y, end_x, end_y`）
- **类别**：Parameter Mismatch & Coordinate System Bug
- **精确位置**：
  - [RootPhoneHarness.kt:610-633](file:///D:/bubuki/proactive-personal-agent/android/app/src/main/java/dev/bubuki/proactive/RootPhoneHarness.kt#L610-L633)
  - [DisplayRuntime.java:563-576](file:///D:/bubuki/proactive-personal-agent/android/app/src/main/java/dev/bubuki/proactive/DisplayRuntime.java#L563-L576)
- **根因与代码证据**：
  1. `RootPhoneHarness.kt:610-633` 的 `resolveGridOrNorm(input)` **只转换了 `x, y` 和 `nx, ny`**，**完全没有转换 `swipe` 所需的 `x1, y1, x2, y2`（或 `nx1, ny1, nx2, ny2` / `start_x, start_y, end_x, end_y`）**！
  2. 如果云端 `harness.py` / LLM 传入的是 `start_x, start_y, end_x, end_y`（常见 Agent 参数名）或者 `0..1000` 归一化坐标，或者传入的是预设方向 `direction: "up" | "down" | "left" | "right"`，`DisplayRuntime.java:566-568` 会直接因 `cmd.getInt("x2")` 缺失而抛 `JSONException`，或因归一化坐标 `y2=850` 与虚拟屏像素坐标混淆导致滑动距离错乱，甚至当 `x2 >= width`（例如紧凑档 `width=544`，归一化 `x2=800`）时在 `checkPoint(x2, y2)` 直接抛出 `"坐标超出独立虚拟屏"`！
  3. 即使对于 `tap(x, y)`，第 627 行的归一化启发式判断是：
     ```kotlin
     if ((rx > dispW || ry > dispH) && rx <= 1000 && ry <= 1000) {
         cmdObj.put("x", (rx / 1000.0 * dispW).toInt())
         cmdObj.put("y", (ry / 1000.0 * dispH).toInt())
     }
     ```
     由于虚拟屏分辨率通常是 `720x1600`，当 LLM 输出 `0..1000` 归一化坐标（如 `(500, 800)`）时，`500 <= 720` 且 `800 <= 1600`，**根本不会进入这个 `if` 分支**！结果 `(500, 800)` 被当成绝对像素直接点在 `(500, 800)`（即屏幕偏右上方，而非正中心 `(360, 1280)`），造成严重的点击位置偏移！
- **具体后果**：
  - `swipe` 传 `start_x/end_x` 或 `direction` 直接崩溃；
  - `swipe` 传 `0..1000` 归一化坐标在 `x2 > dispW`（如 `x2=800 > 720`）时直接越界报错；
  - `tap` 传 `0..1000` 归一化坐标且落在 `720x1000` 以内时，完全不做缩放，点击位置严重错位！
- **修复方案**：
  - 在 `RootPhoneHarness.kt` 中支持 `start_x`/`start_y`/`end_x`/`end_y` 映射到 `x1`/`y1`/`x2`/`y2`，支持 `direction`（up/down/left/right）自动生成安全滑动坐标；
  - 支持 `nx1/ny1/nx2/ny2` 归一化坐标，并检查 `coord_space` / `normalized` 标志位；对 `x1, y1, x2, y2` 同样执行归一化转换与边界 `coerceIn(1, dispW - 2)`。

---

### 【BUG-A3-08】`HIGH` — 屏幕旋转死锁：截图后若目标应用发生横竖屏切换，所有后续 `tap`/`swipe`/`text`/`key` 永久报错卡死
- **类别**：State Machine Deadlock
- **精确位置**：
  - [RootPhoneHarness.kt:809, 816](file:///D:/bubuki/proactive-personal-agent/android/app/src/main/java/dev/bubuki/proactive/RootPhoneHarness.kt#L809)
  - [DisplayRuntime.java:1025-1027](file:///D:/bubuki/proactive-personal-agent/android/app/src/main/java/dev/bubuki/proactive/DisplayRuntime.java#L1025-L1027)
- **根因与代码证据**：
  ```kotlin
  // RootPhoneHarness.kt:809, 816
  if (operation in listOf("tap", "long_press", "swipe", "text", "key") && !input.optBoolean("manual")) lastCaptureRotation?.let { command.put("expected_rotation", it) }
  ...
  if (operation == "capture" && !input.optBoolean("manual")) lastCaptureRotation = result.optInt("rotation")
  ```
  ```java
  // DisplayRuntime.java:1025-1027
  if (command.has("expected_rotation") && command.getInt("expected_rotation") != display.getDisplay().getRotation()) {
      throw new Exception("画面方向已改变，请重新截图后再操作");
  }
  ```
  注意当前 Agent 的核心策略是 **`ui_tree` 优先，几乎不调用 `capture`**！
  一旦在过去某次任务中调用过一次 `capture`，`lastCaptureRotation` 就被永久锁死（甚至跨任务、`release` 后都不重置！）。如果后续应用旋转了屏幕，Agent 按照提示词调用 `ui_tree`（不更新 `lastCaptureRotation`），然后调用 `swipe`、`key`、`text` 或 `tap`，都会因为 `expected_rotation` 不匹配而无限抛出 `"画面方向已改变，请重新截图后再操作"`！
- **具体后果**：
  在横屏应用/视频/游戏或旋转屏幕后，使用 `ui_tree` + `swipe`/`key`/`tap` 会陷入死锁，永远无法执行任何操作。
- **修复方案**：
  - 在 `ui_tree`、`create`、`release`、`launch` 时同步更新或清空 `lastCaptureRotation = null`；
  - 对 `key` 和 `text` 操作根本不需要校验 `expected_rotation`。

---

### 【BUG-A3-09】`MEDIUM` — `RootPhoneAgent.kt` 将 JPEG 截图硬编码伪装成 `image/png` Data URL 发送给多模态大模型
- **类别**：Contract / MIME Mismatch
- **精确位置**：
  - [DisplayRuntime.java:321, 548](file:///D:/bubuki/proactive-personal-agent/android/app/src/main/java/dev/bubuki/proactive/DisplayRuntime.java#L548)
  - [RootPhoneAgent.kt:86](file:///D:/bubuki/proactive-personal-agent/android/app/src/main/java/dev/bubuki/proactive/RootPhoneAgent.kt#L86)
- **根因与代码证据**：
  `DisplayRuntime.java` 第 548 行对 `capture` 截图使用的是 **JPEG 压缩**：
  ```java
  upright.compress(Bitmap.CompressFormat.JPEG, 84, out);
  ```
  但 `RootPhoneAgent.kt` 第 86 行却将其包装为 `data:image/png;base64,$image`：
  ```kotlin
  images.put(JSONObject().put("type", "image_url").put("image_url", JSONObject().put("url", "data:image/png;base64,$image")))
  ```
  （此外在 [RootDisplayServer.java:39, 49](file:///D:/bubuki/proactive-personal-agent/android/app/src/main/java/dev/bubuki/proactive/RootDisplayServer.java#L39) 中也把 JPEG 字节直接写进了 `.png` 后缀文件）。
- **具体后果**：
  严格校验图片魔数（Magic Bytes `FF D8 FF` vs PNG `89 50 4E 47`）的视觉模型 API（如部分 Anthropic / Gemini / 严格网关）会直接返回 `400 Bad Request: Image media type does not match image data`。
- **修复方案**：
  将 `RootPhoneAgent.kt:86` 改为 `data:image/jpeg;base64,$image`。

---

### 【BUG-A3-10】`MEDIUM` — `DisplayRuntime.fastTouch` 坐标裁剪宽高用反，在竖屏下允许 X 坐标越界到 `height - 1`
- **类别**：Coordinate Clamping Bug
- **精确位置**：
  - [DisplayRuntime.java:230-231](file:///D:/bubuki/proactive-personal-agent/android/app/src/main/java/dev/bubuki/proactive/DisplayRuntime.java#L230-L231)
- **根因与代码证据**：
  ```java
  final float x = (float) Math.max(0, Math.min(Math.max(width, height) - 1, cmd.optInt("x", 0)));
  final float y = (float) Math.max(0, Math.min(Math.max(width, height) - 1, cmd.optInt("y", 0)));
  ```
  无论是 `x` 还是 `y`，上限都被写成了 `Math.max(width, height) - 1`（例如 1600），而没有根据当前 `rotation` 区分 `logicalW`（720）和 `logicalH`（1600）。
- **具体后果**：
  实时触控拖拽超出右边界时，X 坐标未被正确限制在 `logicalW - 1` 内。
- **修复方案**：
  根据 `vd.getDisplay().getRotation()` 计算 `int lw = (rot % 2 == 0) ? width : height; int lh = (rot % 2 == 0) ? height : width;`，分别用 `lw - 1` 和 `lh - 1` 裁剪 `x` 和 `y`。

---

### 【BUG-A3-11】`MEDIUM` — `InAppFloatingVirtualDisplay` 在展开态（`expanded = true`）下完全无法拖动窗口，且缺少应用选择器、档位切换与底部返回/接管胶囊
- **类别**：UI/UX Defect & Feature Parity Gap
- **精确位置**：
  - [FloatingVirtualDisplay.kt:1537-1538, 1610-1611, 1679-1727](file:///D:/bubuki/proactive-personal-agent/android/app/src/main/java/dev/bubuki/proactive/FloatingVirtualDisplay.kt#L1537-L1538)
- **根因与代码证据**：
  1. 在 `InAppFloatingVirtualDisplay` 中，外层 `Box` 的 `pointerInput` 第 1538 行写了：
     ```kotlin
     if (expanded) return@pointerInput
     ```
     而内层 `Image` 的 `pointerInput`（1610行）在 `expanded` 时把所有触摸事件（包括顶部拖拽区）全部当成 `sendLiveTouch` 发给虚拟屏！
  2. 相比系统级 `FloatingVirtualDisplay`，`InAppFloatingVirtualDisplay` 展开态缺少：
     - 顶部拖拽热区（无法移动展开后的窗口）；
     - 应用选择器按钮（`APPS_GRID`）与应用列表弹窗；
     - 分辨率档位切换按钮（`S/M/L`）；
     - 底部 `‹ 返回` 胶囊与 `waiting_user` 时的 `我处理好了，继续` 按钮（导致在无悬浮窗权限时遇到 `waiting_user` 无法在浮窗内点继续！）。
- **具体后果**：
  在未授予系统悬浮窗权限、使用应用内浮窗时，展开后窗口焊死在原位无法拖动，无法选应用、无法按返回键、无法点“我处理好了，继续”。
- **修复方案**：
  在 `InAppFloatingVirtualDisplay` 展开态补齐顶部拖拽区、底部返回/继续胶囊以及关闭逻辑。

---

### 【BUG-A3-12】`MEDIUM` — `FloatingVirtualDisplay` 胶囊态点击恢复时，在后台线程误发 `op="create"` 导致覆盖用户状态与冗余 IPC
- **类别**：Redundant RPC / State Glitch
- **精确位置**：
  - [FloatingVirtualDisplay.kt:591-597](file:///D:/bubuki/proactive-personal-agent/android/app/src/main/java/dev/bubuki/proactive/FloatingVirtualDisplay.kt#L591-L597)
- **根因与代码证据**：
  ```kotlin
  attachSpringDragAndTap(context, pill, onTap = {
      minimizedToPill = false
      transitionSizeAndRebuild(context, RootPhoneHarness.changes.value)
      scope.launch(Dispatchers.IO) {
          runCatching { RootPhoneHarness.command(context, JSONObject().put("op", "create").put("manual", true)) }
      }
  })
  ```
  由于 `RootPhoneHarness.kt:595` 有逻辑：
  ```kotlin
  if (state.value.phase in listOf("running", "waiting_user") && input.optBoolean("manual") && operation !in listOf("capture", "resize")) {
      job?.cancel(); agent?.cancel()
      state.update { it.copy(phase = "idle") }
  }
  ```
  当后台本地 Agent（`RootPhoneAgent`）正在 `running` 或 `waiting_user` 时，用户如果先把悬浮窗收成胶囊（`minimizedToPill = true`），然后**点一下胶囊想看看画面**，第 595 行就会发送带 `"manual": true` 的 `"op": "create"`，直接命中 `RootPhoneHarness.kt:595`，**把正在运行的手机任务当场 cancel 杀死**！
- **具体后果**：
  任务执行期间或等待用户接管（`waiting_user`）期间，用户把浮窗收成胶囊后再点开胶囊，正在执行的任务被直接中断取消！
- **修复方案**：
  点击胶囊恢复小窗时只需更新本地 UI 状态并确保 `startLivePreview`，或不在 `op == "create"` 时因 `manual=true` 取消正在运行的任务（将 `"create"` 加入 `RootPhoneHarness.kt:595` 的豁免列表 `listOf("capture", "resize", "create", "apps")`）。

---

### 【BUG-A3-13】`LOW` — `RootClipboard.java` 启动 `RootClipboardBroker` 时未设置 `CLASSPATH`，导致剪贴板粘贴 fallback 必定因 `ClassNotFoundException` 失败
- **类别**：Broken Fallback / Classpath Omission
- **精确位置**：
  - [RootClipboard.java:20](file:///D:/bubuki/proactive-personal-agent/android/app/src/main/java/dev/bubuki/proactive/RootClipboard.java#L20)
- **根因与代码证据**：
  ```java
  // RootClipboard.java:20
  java.lang.Process broker=new ProcessBuilder("app_process","/system/bin","dev.bubuki.proactive.RootClipboardBroker").redirectError(new File("/dev/null")).start();
  ```
  雖然父进程 `RootDisplayServer` 启动时在 shell 中设置了 `export CLASSPATH='...'`，但在部分 Android ROM / SELinux 环境下，或当 APK 更新后路径变化时，直接用 `ProcessBuilder("app_process", ...)` 而不显式传递 `pb.environment().put("CLASSPATH", ...)` 可能导致子进程找不到类，且 `redirectError(new File("/dev/null"))` 吞掉了所有报错，最终表现为超时 8 秒抛出 `"Root 系统命令超时"` 或 `"剪贴板会话已断开"`。
- **具体后果**：
  当无障碍 `ACTION_SET_TEXT` 不可用（如自绘输入框）回退到 `RootClipboard.paste` 时，可能卡顿 8 秒后失败。
- **修复方案**：
  在 `RootClipboard` 中保存 `context` 或当前进程的 `System.getenv("CLASSPATH")`，在 `ProcessBuilder` 中显式注入 `CLASSPATH` 环境变量。


---

## 三、跨代理对接（与 Auditor-1 服务端 & Auditor-4 安卓主界面）核实的跨层契约与生命周期 Bug

### 【BUG-A3-14】`CRITICAL` — 多客户端广播风暴：服务端 `phone_workspace_command` 未按 `source` 过滤，且 `AgentForegroundService.handle()` 未校验设备 `source`，导致多台手机/模拟器同时弹窗执行
- **类别**：Cross-Layer Routing Split-Brain (Server `main.py` ↔ Android `AgentForegroundService.java`)
- **精确位置**：
  - [AgentForegroundService.java:121-131, 170](file:///D:/bubuki/proactive-personal-agent/android/app/src/main/java/dev/bubuki/proactive/AgentForegroundService.java#L121-L131)
  - 服务端 `main.py:88-112`
- **根因与代码证据**：
  1. 服务端 `_event_node_source(event)` 只过滤了 `('message','stream','execution','stopped')`，对 `type == "phone_workspace_command"` 返回 `None`，广播给所有 `/ws/client` 连接。
  2. 安卓端 `AgentForegroundService.java` 第 129 行同样只过滤了 `Arrays.asList("message","stream","execution","stopped").contains(type)`：
     ```java
     if(!evSource.isEmpty()&&!evSource.equals(mySource)&&Arrays.asList("message","stream","execution","stopped").contains(type)){
         return;
     }
     ```
     第 170 行处理 `if(type.equals("phone_workspace_command"))` 时完全没有检查目标节点，如果用户同时连接了备用机或模拟器，所有安卓端会同时自动 `create` 虚拟屏并抢答同一个 `request_id`！
- **修复方案**：
  服务端与安卓端 `AgentForegroundService.java` 对 `phone_workspace_command` 同步校验目标 `source`（或僅由主活动手机节点处理）。

---

### 【BUG-A3-15】`CRITICAL` — `brain.py` 处理 `op="open"` 时产生空 `app=""` 的 `launch` 指令，直接触发安卓端 `AgentForegroundService` 先 `create` 黑屏虚拟屏再报错
- **类别**：Cross-Layer Auto-Popup Chain (`brain.py:3358-3365` ↔ `AgentForegroundService.java:198-220` ↔ `RootPhoneHarness.kt:800-804, 898`)
- **精确位置**：
  - [AgentForegroundService.java:198-220](file:///D:/bubuki/proactive-personal-agent/android/app/src/main/java/dev/bubuki/proactive/AgentForegroundService.java#L198-L220)
  - [RootPhoneHarness.kt:800-804, 840-846, 898](file:///D:/bubuki/proactive-personal-agent/android/app/src/main/java/dev/bubuki/proactive/RootPhoneHarness.kt#L800-L804)
- **根因与代码证据**：
  1. 当 LLM 调用 `phone_workspace(op="open")` 且只传 `component="com.tencent.mm/.ui.LauncherUI"` 或没传 `app` 时，`brain.py:3358-3365` 未读取 `component`，导致 `u_cand=""`，并将其改写为 `op="launch", args={..., "app": ""}` 下发给安卓端。
  2. 安卓端 `AgentForegroundService.java:198-207` 收到 `op="launch"` 后，**不检查 `app`/`component` 是否为空**，先在第 206 行调用 `RootPhoneHarness.command(this, {"op": "create"})`，触发 `FloatingVirtualDisplay.show()` 强行弹出黑屏虚拟屏！
  3. 紧接着第 220 行调用 `RootPhoneHarness.command(this, input)`，若 `component` 也没传，则在 `RootPhoneHarness.kt:898` 抛出 `Exception("请提供要打开的应用名或组件名")`！此时指令报错返回，但黑屏虚拟屏已经留在用户桌面上了！
- **修复方案**：
  - `AgentForegroundService.java:198` 绝不能在 `launch` 参数未验证前盲目调用 `create`；
  - `RootPhoneHarness.kt` 必须在 `resolveLaunchComponent` 验证通过拿到合法的 `package/activity` 后，若虚拟屏尚未创建再按需创建虚拟屏。

---

### 【BUG-A3-16】`HIGH` — `NativePulseActivity.launchFloatingVirtualDisplay()` 在未授予悬浮窗权限时请求权限后未 `return`，且与 `InAppFloatingVirtualDisplay` 冲突
- **类别**：Cross-Layer UI Lifecycle Bug (`NativePulseActivity.kt` / `NativePulseScreen.kt` ↔ `FloatingVirtualDisplay.kt`)
- **精确位置**：
  - [FloatingVirtualDisplay.kt:407-418, 493-497, 1412-1427](file:///D:/bubuki/proactive-personal-agent/android/app/src/main/java/dev/bubuki/proactive/FloatingVirtualDisplay.kt#L407-L418)
  - `NativePulseActivity.kt:2250-2275`、`NativePulseScreen.kt:292, 417`
- **根因与代码证据**：
  1. 在 `NativePulseActivity.kt:2252-2254` 中，当 `!FloatingVirtualDisplay.canOverlay(activity)` 时调用了 `FloatingVirtualDisplay.requestOverlayPermission(activity)` 跳转到系统设置页，但**没有 `return`**，继续向下执行 `RootPhoneHarness.command("create")` 和 `FloatingVirtualDisplay.show(activity, forceExpand = true)`。
  2. 此时由于还没有权限，`FloatingVirtualDisplay.show()` 内部 `canOverlay(appCtx)` 为 `false`（`_overlayVisible.value` 保持 `false`），而 `created` 变为 `true`，导致 `NativePulseScreen` 内的 `InAppFloatingVirtualDisplay`（第 1419 行：`val visible = created && ... && !systemOverlayShowing`）立即在应用内弹出一块黑色虚拟屏！
  3. 当用户在系统设置中开启悬浮窗权限返回 App，或者 `PrivilegeManager.autoGrantPermissionsViaRoot`（[PrivilegeManager.java:108, 213](file:///D:/bubuki/proactive-personal-agent/android/app/src/main/java/dev/bubuki/proactive/PrivilegeManager.java#L108)）通过 Root 自动执行了 `appops set ... SYSTEM_ALERT_WINDOW allow` 后，`canOverlay` 变为 `true`，下一次 `RootPhoneHarness.changes` 发出状态时，`FloatingVirtualDisplay.ensureWatching`（465行）立即挂载系统级 `TYPE_APPLICATION_OVERLAY` 悬浮窗！
- **修复方案**：
  - `launchFloatingVirtualDisplay()` 请求权限后若仍无权限应立即 `return` 或明确告知用户；
  - 统一 `FloatingVirtualDisplay` 与 `InAppFloatingVirtualDisplay` 的 `userDismissed` 与 `minimizedToPill` 状态源（使用 `StateFlow` 共享），防止两边状态各自为政。

---

### 【BUG-A3-17】`HIGH` — `NativePulseActivity.stop()` 与 `AgentForegroundService.abortPhoneCommands()` 交互缺陷：停止普通对话会误杀后台虚拟屏，而停止本地手机任务却漏调 `RootPhoneHarness.stop()`
- **类别**：Cross-Layer Control Flow Bug (`NativePulseActivity.kt` ↔ `AgentForegroundService.java` ↔ `RootPhoneHarness.kt`)
- **精确位置**：
  - [AgentForegroundService.java:20-27](file:///D:/bubuki/proactive-personal-agent/android/app/src/main/java/dev/bubuki/proactive/AgentForegroundService.java#L20-L27)
  - [RootPhoneHarness.kt:78-81, 1091-1109](file:///D:/bubuki/proactive-personal-agent/android/app/src/main/java/dev/bubuki/proactive/RootPhoneHarness.kt#L1091-L1109)
  - `NativePulseActivity.kt:2534-2550`
- **根因与代码证据**：
  1. 在 [RootPhoneHarness.kt:78-81](file:///D:/bubuki/proactive-personal-agent/android/app/src/main/java/dev/bubuki/proactive/RootPhoneHarness.kt#L78-L81) 中：
     ```kotlin
     @JvmStatic fun isVirtualDisplayActive(): Boolean {
         val st = state.value
         return st.displayCreated || st.active
     }
     ```
     由于 BUG-A3-01（`release` 后 `displayCreated` 永远为 `true`），`isVirtualDisplayActive()` 一旦创建过一次虚拟屏就**永远返回 `true`**！
  2. 在 `NativePulseActivity.stop()` 中，`hasPhoneToolInTurn` 判断包含了 `phone.displayCreated`。因此只要曾经创建过虚拟屏，哪怕当前用户只是在停止一段纯文本对话，也会触发 `AgentForegroundService.abortPhoneCommands(activity)` -> `RootPhoneHarness.stop(ctx)`，把 `RootPhoneHarness` 的 `phase` 强行改成 `"stopped"`，并设置 5 秒 `phoneAbortUntilMs` 拒绝一切手机命令！
  3. 而在 [RootPhoneHarness.kt:1091-1109](file:///D:/bubuki/proactive-personal-agent/android/app/src/main/java/dev/bubuki/proactive/RootPhoneHarness.kt#L1091-L1109) 的 `stop(base: Context)` 中，调用的是 `command(base, JSONObject().put("op", "release"))`（没有传 `"destroy": true`），导致 `stop()` 之后 `displayCreated` 依然是 `true`！
- **修复方案**：
  - 在 `RootPhoneHarness.stop(base)` 中传入 `JSONObject().put("op", "release").put("destroy", true)`（或配合 BUG-A3-01 修复使 `release` 后 `displayCreated` 正确变为 `false`）；
  - `hasPhoneToolInTurn` 只在当前 turn 确实使用了 `phone_workspace` 或 `phone.active` 为 `true` 时才触发 `abortPhoneCommands`。

---

### 【BUG-A3-18】`MEDIUM` — `RootPhoneHarness.kt` 的 `text` / `type_node` 忽略了 `clear` 参数，且 `long_press` 不支持按节点 `ref` / `target` 定位
- **类别**：Cross-Layer Parameter Omission (`brain.py` ↔ `RootPhoneHarness.kt`)
- **精确位置**：
  - [RootPhoneHarness.kt:755-795, 805-810](file:///D:/bubuki/proactive-personal-agent/android/app/src/main/java/dev/bubuki/proactive/RootPhoneHarness.kt#L755-L795)
  - [DisplayRuntime.java:577-584, 613-621](file:///D:/bubuki/proactive-personal-agent/android/app/src/main/java/dev/bubuki/proactive/DisplayRuntime.java#L613-L621)
- **根因与代码证据**：
  1. 当 `operation == "long_press"` 时，`RootPhoneHarness.kt` 直接走第 805 行透传给 `DisplayRuntime.java:613`（强制要求 `cmd.getInt("x"), cmd.getInt("y")`），没有像 `tap` 一样支持 `ref` / `text` / `target` 节点解析；而 `brain.py:3445` 在 `long_press` 未传坐标时直接填了屏幕中心 `(cur_w // 2, cur_h // 2)`，导致按节点长按时总是长按屏幕正中央！
  2. 当无障碍 `ACTION_SET_TEXT` 失败回退到 `DisplayRuntime` 的 `op="text"`（`RootClipboard.paste`）时，既没有先全选清空旧文本（当 `clear=true` 时），也没有处理 `submit=true` 回车提交。
- **修复方案**：
  - 在 `RootPhoneHarness.kt` 中为 `long_press` 增加与 `tap_node` 相同的 `ref` / `text` / `id` 节点中心坐标解析；
  - 在 `type_node` / `text` 中支持 `submit` / `press_enter`（发送 `"op": "key", "key": "enter"`）。


---
# 【分卷】Auditor-4 (安卓主界面前半部 NativePulseScreen / 多端状态同步) (`audit_4_android_ui_transport.md`)

# Auditor-4 安卓主界面 UI、聊天流式渲染、网络传输与多端同步全量审计报告

## 第一部分：虚拟屏弹窗打架、状态机死锁与流式同步核心漏洞 (BUG-A4-01 ~ BUG-A4-09)

---

### BUG-A4-01 (`CRITICAL`)：系统级悬浮窗 (`FloatingVirtualDisplay`) 与应用内悬浮窗 (`InAppFloatingVirtualDisplay`) 互斥逻辑缺陷导致“关了一个另一个立即弹出来”
- **类别**：窗口生命周期与状态机悖论 (UI Window Conflict)
- **文件与行号**：
  - `D:\bubuki\proactive-personal-agent\android\app\src\main\java\dev\bubuki\proactive\NativePulseScreen.kt:292, 417`
  - `D:\bubuki\proactive-personal-agent\android\app\src\main\java\dev\bubuki\proactive\FloatingVirtualDisplay.kt:420-424, 460-470, 769-777, 1419`
- **根因与代码证据**：
  1. `NativePulseScreen.kt:292`（平板）和 `417`（手机）无条件挂载了 `@Composable InAppFloatingVirtualDisplay()`。
  2. `FloatingVirtualDisplay.kt:1419` 中 `InAppFloatingVirtualDisplay` 的显示条件为：
     ```kotlin
     val visible = created && state.phase != "stopping" && state.phase != "stopped" && !systemOverlayShowing
     ```
  3. 当系统级悬浮窗通过 `FloatingVirtualDisplay.hide()`（`FloatingVirtualDisplay.kt:420-424`）隐藏时，`removeWindow()` 将 `_overlayVisible.value = false`（即 `systemOverlayShowing = false`），但底层虚拟屏 `disp.optBoolean("created")` 依然为 `true`！
  4. 此外，在系统悬浮窗点击红色电源关闭按钮（`FloatingVirtualDisplay.kt:769-777` `Action.POWER_CLOSE`）时，代码并未立即调用 `hide()`，而是设 `minimizedToPill = true` 先缩成胶囊，异步执行 `RootPhoneHarness.command("release")` 期间若 `_overlayVisible` 变化或权限切换，两套窗口就会抢占同一个 `RootPhoneHarness` 画面。
- **具体后果**：
  - 只要调用 `FloatingVirtualDisplay.hide()` 关闭系统悬浮窗，而底层 `created` 仍为 `true`，`!systemOverlayShowing` 瞬间变为 `true`，应用内悬浮窗 `InAppFloatingVirtualDisplay` 立即在主界面右下角弹出来（典型的“关了一个另一个又弹出来”）。
- **修复方案**：
  - 引入明确的显示模式状态（如 `overlayMode: NONE / SYSTEM / IN_APP`）或 `userDismissedOverlay` 标志；当用户主动关闭或 `hide()` 系统悬浮窗时，禁止 `InAppFloatingVirtualDisplay` 自动回退弹出，仅在确实无系统悬浮窗权限（`!canOverlay(ctx)`）且用户未关闭窗口时才展示 `InAppFloatingVirtualDisplay`。

---

### BUG-A4-02 (`CRITICAL`)：`NativePulseActivity.stop()` 误判 `phone.displayCreated`，导致普通文字聊天点停止也会强杀空闲虚拟屏并触发弹窗复活
- **类别**：状态机耦合与误杀 (State Machine Coupling)
- **文件与行号**：
  - `D:\bubuki\proactive-personal-agent\android\app\src\main\java\dev\bubuki\proactive\NativePulseActivity.kt:2534-2550, 2881-2886`
  - `D:\bubuki\proactive-personal-agent\android\app\src\main\java\dev\bubuki\proactive\RootPhoneHarness.kt:78-81`
- **根因与代码证据**：
  - `NativePulseActivity.kt:2537-2543`：
    ```kotlin
    val hasPhoneToolInTurn = phone.active || phone.displayCreated ||
        ui.streamTool == "phone_workspace" ||
        ui.streamTrace.any { it.optString("tool") == "phone_workspace" }
    if (hasPhoneToolInTurn) {
        FloatingVirtualDisplay.hide()
        AgentForegroundService.abortPhoneCommands(activity)
    }
    ```
  - 同时 `NativePulseActivity.kt:2544-2548` 当 `phone.active && phone.botId == targetBot` 且无远端流时提前 `return`，未清理本地 `ui.busyBot`、`ui.workingBot` 和 `sendGuardBotId`。
- **具体后果**：
  1. 如果用户手动打开了虚拟屏挂在后台（此时 `phone.displayCreated == true` 且 `phone.active == false`），随后在任意助手聊天室打字聊天并点击“停止生成”，`hasPhoneToolInTurn` 因 `phone.displayCreated` 为真而触发，不仅把系统悬浮窗 `hide()`（瞬间又触发 BUG-A4-01 弹出应用内悬浮窗），还通过 `abortPhoneCommands` -> `RootPhoneHarness.stop()` 把空闲虚拟屏强行销毁！
  2. 在主页左滑停止其他助手（`stopBotById`）时同样会误杀正在运行的虚拟屏。
- **修复方案**：
  - 将 `hasPhoneToolInTurn` 条件改为仅当 `(phone.active && (phone.botId.isBlank() || phone.botId == targetBot)) || (ui.streamBotId == targetBot && (ui.streamTool == "phone_workspace" || ui.streamTrace.any { it.optString("tool") == "phone_workspace" }))` 时才中止手机工具，绝不能因 `phone.displayCreated` 为真就销毁虚拟屏；且提前 `return` 前必须清理 `busyBot / workingBot / sendGuardBotId`。

---

### BUG-A4-03 (`HIGH`)：`launchFloatingVirtualDisplay()` 请求悬浮窗权限后漏写 `return`，在设置页后台强行创建虚拟屏
- **类别**：权限流程缺陷 (Permission Flow Bug)
- **文件与行号**：
  - `D:\bubuki\proactive-personal-agent\android\app\src\main\java\dev\bubuki\proactive\NativePulseActivity.kt:2250-2275`
- **根因与代码证据**：
  ```kotlin
  if (!FloatingVirtualDisplay.canOverlay(activity)) {
      FloatingVirtualDisplay.requestOverlayPermission(activity)
  }
  ui = ui.copy(phoneError = null)
  ```
- **具体后果**：
  - 当没有悬浮窗权限时，跳转系统权限设置页后代码没有 `return`，继续往下执行 `RootPhoneHarness.command("create")` 和 `FloatingVirtualDisplay.show()`。此时 `created=true` 而 `canOverlay=false`，导致应用内悬浮窗 `InAppFloatingVirtualDisplay` 在后台弹出；等用户授权返回后，应用内悬浮窗与系统悬浮窗发生切换闪烁。
- **修复方案**：
  - 在 `FloatingVirtualDisplay.requestOverlayPermission(activity)` 后立即弹出提示并 `return`。

---

### BUG-A4-04 (`CRITICAL`)：WebSocket `onRealtimeEvent` 漏处理 `"stopped"` 事件，且收到 `execution: null` 时反向延长 20 秒思考门禁
- **类别**：实时流状态同步死锁 (Stream Lifecycle Deadlock)
- **文件与行号**：
  - `D:\bubuki\proactive-personal-agent\android\app\src\main\java\dev\bubuki\proactive\NativePulseActivity.kt:783, 786-993`
  - 跨层核对：`brain.py:256, 286, 5147`（Auditor-1 确认）
- **根因与代码证据**：
  1. `NativePulseActivity.kt:783` 过滤列表包含 `"stopped"`：`if (type in listOf("message", "stream", "execution", "stopped") && evSource != mySource) return@launch`，但 `786-993` 的 `when (type)` **完全没有 `"stopped"` 分支**！服务端在停止任务时调用 `_clear_bot_stream`（不发 `stream: null`）并广播 `{"type": "stopped", "bot_id": ..., "message": committed_msg}`。
  2. 在 `"stream"` 分支（`818` 行）：`val stream = event.optJSONObject("stream") ?: return@launch`，若 `stream` 为 `null` 直接丢弃，不清空 `ui.streamBotId`。
  3. 在 `"execution"` 分支（`891-925` 行）：当 `exec == null`（即 `working == null`）时，`910` 行写成了：
     ```kotlin
     val activeBot = working ?: ui.workingBot ?: ui.streamBotId ?: sendGuardBotId
     ```
     不仅没有清除 `workingBot`，反而用旧的 `ui.workingBot` 兜底，并在 `913` 行执行 `sendGuardUntilUptime = maxOf(sendGuardUntilUptime, now + 20_000L)`，把“正在思考…”状态强行续期 20 秒！
- **具体后果**：
  - 当任务被停止、超时或无流式文本结束时，安卓端忽略 `"stopped"` 事件且在收到空 `execution` 时续期门禁，导致界面长期卡死在“正在思考… / 正在输入…”长达 20~45 秒（WS 在线时 HTTP 轮询间隔为 45 秒）。
- **修复方案**：
  - 在 `when (type)` 中增加 `"stopped"` 分支：合并 `event.optJSONObject("message")`（若非空），并立即清除对应 `bot_id`（或 `"all"`）的 `busyBot`、`workingBot`、`streamBotId`、`streamText`、`streamTrace` 及 `sendGuardBotId`。
  - 在 `"execution"` 分支中，当 `exec == null` 时清除 `workingBot` 与 `workingText`，严禁在 `working == null` 时延长 `sendGuardUntilUptime`。

---

### BUG-A4-05 (`CRITICAL`)：多 Bot 并发流式输出串台 —— 后台 Bot B 的流式回复会直接渲染到当前打开的 Bot A 聊天室中
- **类别**：多智能体状态串流 (Multi-Bot Stream Cross-Contamination)
- **文件与行号**：
  - `D:\bubuki\proactive-personal-agent\android\app\src\main\java\dev\bubuki\proactive\NativePulseActivity.kt:841, 869-888, 1980-1985`
  - `D:\bubuki\proactive-personal-agent\android\app\src\main\java\dev\bubuki\proactive\NativePulseScreen.kt:1557, 1764-1774`
- **根因与代码证据**：
  1. `NativePulseActivity.kt:841`：
     ```kotlin
     if (ui.chatOpen && sBot != ui.botId && ui.streamBotId == ui.botId) return@launch
     ```
     当用户打开 Bot A 的聊天室（`ui.chatOpen == true, ui.botId == "botA"`），用户刚给 Bot A 发消息（此时 `ui.busyBot == "botA"`，但 Bot A 还没吐出第一个 token，故 `ui.streamBotId == null`），若后台 Bot B 推来一条 `"stream"` 事件，`ui.streamBotId == ui.botId` 为 `false`，代码**不会 return**，而是把全局唯一的 `ui.streamBotId = "botB"`、`ui.streamText = "Bot B 的文字"`、`ui.streamTrace = ...` 全部覆盖！
  2. 紧接着在 `NativePulseScreen.kt:1557`：
     ```kotlin
     val isLiveHere = !duplicateWithTail && (
         s.streamBotId == bot.id || s.workingBot == bot.id || s.busyBot == bot.id ||
             (s.streamBotId == bot.id && s.streamTrace.isNotEmpty())
     )
     ```
     因为 `s.busyBot == bot.id`（`"botA"`）为 `true`，`isLiveHere` 为 `true`！而 `NativeLiveStreamBubble`（`1764-1774` 行）直接读取全局 `s.streamText`、`s.streamThought`、`s.streamTrace` 渲染，完全没校验 `s.streamBotId == bot.id`！
- **具体后果**：
  - 用户在 Bot A 房间等待回复时，聊天室里直接把后台 Bot B 的流式文字、思考过程和工具调用渲染成 Bot A 的气泡！切换聊天室时还会丢失另一方的流式进度。
- **修复方案**：
  - 在 `NativeLiveStreamBubble` 中，只有当 `s.streamBotId == bot.id` 时才读取 `s.streamText / s.streamThought / s.streamTrace / s.streamChoices`，否则只显示“正在思考…”占位动画；同时在 `onRealtimeEvent("stream")` 中若 `ui.chatOpen && sBot != ui.botId` 不应覆盖当前打开房间的流状态（或改为按 `botId` 字典存储流状态）。

---

### BUG-A4-06 (`HIGH`)：`messages_truncated` 事件未校验 `source` 节点隔离，PC 端重答/编辑消息会重置手机端 `lastMessagesEpoch` 引发全量闪烁
- **类别**：多端节点隔离漏洞 (Multi-Node Isolation Leak)
- **文件与行号**：
  - `D:\bubuki\proactive-personal-agent\android\app\src\main\java\dev\bubuki\proactive\NativePulseActivity.kt:783, 787-800`
  - `D:\bubuki\proactive-personal-agent\android\app\src\main\java\dev\bubuki\proactive\AgentForegroundService.java:129`
  - 跨层核对：`main.py:88-102`（Auditor-1 确认）
- **根因与代码证据**：
  - `NativePulseActivity.kt:783` 仅对 `listOf("message", "stream", "execution", "stopped")` 校验了 `evSource != mySource`，未包含 `"messages_truncated"`。
  - `787-800` 行收到任何节点的 `"messages_truncated"` 都会无条件更新 `lastMessagesEpoch = epoch` 并调用 `refreshStreamOnly()`。
- **具体后果**：
  - 当用户在 PC 端（`source="pc"`）或另一台安卓设备（`source="phone2"`）重答/编辑消息时，当前手机端（`source="phone"`）的 `lastMessagesEpoch` 被污染，下次 `refresh()` 时 `incomingEpoch != lastMessagesEpoch` 判定 `epochChanged = true`（`1029, 1143` 行），导致消息列表和打字机状态被强制全量重置闪烁。
- **修复方案**：
  - 将 `"messages_truncated"` 加入 `NativePulseActivity.kt:783`、`AgentForegroundService.java:129` 以及服务端 `main.py:92` 的 `source` 过滤列表中。

---

### BUG-A4-07 (`HIGH`)：当用户在安卓端切换节点为 `"phone2"`（安卓二）时，无法触发上下文压缩、主动关心与研究，且收不到后台定时任务通知
- **类别**：跨层 `source` 契约缺失 (Client-Server Node Contract Mismatch)
- **文件与行号**：
  - `D:\bubuki\proactive-personal-agent\android\app\src\main\java\dev\bubuki\proactive\NativePulseActivity.kt:1895-1910, 2799-2805`
  - 跨层核对：`main.py:821-834, 933-945`、`brain.py:1139, 4216`（Auditor-1 确认）
- **根因与代码证据**：
  1. `NativePulseActivity.kt:2799` `compactContext()` 调用 `POST /api/context/compact` 时只传了 `put("bot_id", ui.botId)`，未传 `put("source", currentNodeSource())`；且 `main.py:821` 也不读取 `source`，永远只压缩 `'phone'` 节点的上下文。
  2. `NativePulseActivity.kt:1896, 1903` 调用 `/api/trigger-curiosity` 和 `/api/research` 未传 `source`，服务端固定写入 `'phone'` 节点，导致用户在 `'phone2'`（安卓二）点击“主动关心”或“自由研究”后，当前节点没有任何消息响应。
- **具体后果**：
  - 多端切换中的 `"phone2"` 节点成为半残废节点：上下文压缩无效、主动触发功能无响应、后台 `bg_task` 完成消息和定时任务提醒全被路由到 `'phone'` 节点。
- **修复方案**：
  - 客户端在 `/api/context/compact`、`/api/trigger-curiosity`、`/api/research` 请求体中统一携带 `"source": currentNodeSource()`，服务端读取并传入 `source_context`。

---

### BUG-A4-08 (`HIGH`)：盯盘哨兵（Watchdog）“停止”按钮因服务端参数名不匹配完全失效
- **类别**：前后端接口契约断层 (API Parameter Contract Mismatch)
- **文件与行号**：
  - `D:\bubuki\proactive-personal-agent\android\app\src\main\java\dev\bubuki\proactive\NativePulseActivity.kt:2775-2781`
  - `D:\bubuki\proactive-personal-agent\android\app\src\main\java\dev\bubuki\proactive\NativePulseScreen.kt:4858`
  - 跨层核对：`main.py:809-811` vs `brain.py:1160`（Auditor-1 确认）
- **根因与代码证据**：
  - 安卓端 `NativePulseActivity.kt:2777` 调用 `request("POST", "/api/watchdogs/$watchdogId/stop")`；
  - `main.py:810` 将路径参数封装为 `{'action': 'stop', 'watchdog_id': watchdog_id}` 传给 `brain._tool_watchdog_task`，但 `brain.py:1160` 只读取 `args.get('task_id') or args.get('name')`，完全忽略了 `watchdog_id`！
- **具体后果**：
  - 用户在安卓端“日程与闹钟”面板点击任何盯盘哨兵的“停止”按钮，100% 失败并弹出错误提示 `未找到盯盘哨兵`。
- **修复方案**：
  - 服务端 `main.py:810` 改为传 `'task_id': watchdog_id`（并在 `brain.py:1160` 兼容 `args.get('watchdog_id')`）。

---

### BUG-A4-09 (`HIGH`)：`/api/chat` 触发 `client_msg_id` 去重分支时漏返回 `user_message`，导致安卓端乐观消息卡在 `sending=true`
- **类别**：异步发送去重契约漏洞 (Async Ack Deduplication Bug)
- **文件与行号**：
  - `D:\bubuki\proactive-personal-agent\android\app\src\main\java\dev\bubuki\proactive\NativePulseActivity.kt:2412-2437`
  - 跨层核对：`brain.py:4458-4466`（Auditor-1 确认）
- **根因与代码证据**：
  - 当网络抖动重试触发 `brain.py:4458` 的 `client_msg_id` 去重分支时，服务端返回 `{'queued': True, 'deduplicated': True, 'recent_tail': tail, ...}`，**没有返回 `user_message` 字段**。
  - 而 `NativePulseActivity.kt:2419` 只解析 `val serverUserMsg = res.optJSONObject("user_message")?.let(PulseMessage::from)`，未从 `recent_tail` 中回捞对应的用户消息，导致本地乐观消息 `localId` 无法在当前回调中被替换为正式消息。
- **具体后果**：
  - 触发去重时，用户发送的气泡会一直保留本地临时 `local-...` ID 和 `sending = true` 状态，直到下一次全量 `refresh()`。
- **修复方案**：
  - 服务端去重分支补齐 `user_message` 与 `turn_id` 字段；安卓端 `NativePulseActivity.kt:2419` 当 `user_message` 为空时同時解析 `res.optJSONArray("recent_tail")` 进行合并。


---

## 第二部分：Compose UI 渲染、多 Bot 互联气泡、Markdown、网络传输与系统服务漏洞 (BUG-A4-10 ~ BUG-A4-18)

---

### BUG-A4-10 (`HIGH`)：多 Bot 互联（`bot_interconnect`）聊天气泡缺失头像、右下角角标（`sub_avatar`），且将“任务委派”与“协作汇报”混为一谈
- **类别**：多端 UI 渲染不一致与信息丢失 (Multi-Bot Interconnect Rendering Defect)
- **文件与行号**：
  - `D:\bubuki\proactive-personal-agent\android\app\src\main\java\dev\bubuki\proactive\NativePulseActivity.kt:186-228`
  - `D:\bubuki\proactive-personal-agent\android\app\src\main\java\dev\bubuki\proactive\NativePulseScreen.kt:1760-1992, 2859-2990`
  - 跨层核对：`client.js:203-370`（Auditor-2 确认）
- **根因与代码证据**：
  1. `PulseMessage.from()`（`NativePulseActivity.kt:204-226`）解析服务端消息 JSON 时，直接丢弃了 `from_node`、`to_bot_id`、`to_bot_name`、`to_node`、`direction` 字段。
  2. 与 PC 端 `client.js:203-270`（完整渲染主头像 + 右下角 `sub_avatar` 节点/分身角标 `phone/pc/session/colleague`）相比，安卓端 `NativeMessage`（`NativePulseScreen.kt:2859-3044`）和 `NativeLiveStreamBubble`（`1760-1992`）**完全没有渲染消息发送方头像与右下角角标**。
  3. 在 `NativePulseScreen.kt:2988-2990` 中：
     ```kotlin
     if (isBotToBotReply || isOtherBot) {
         Text(" · 协作汇报", color = c.accent, fontSize = 11.sp, fontWeight = FontWeight.Medium)
     }
     ```
     当 `message.kind == "bot_message"`（主 Bot 向同事 Bot 发起的**委派任务**）时，因为 `fromBotId` 非空或 `speakerId != currentBotId`，`isOtherBot` 为 `true`，安卓端把**发出的任务委派也错误显示为 `" · 协作汇报"`**，并且隐藏了接收方 `to_bot_name`！
  4. 在流式输出气泡 `NativeLiveStreamBubble`（`1760-1992`）中，未使用 `s.streamSpeakerName` / `s.streamSpeakerId` 展示当前正在流式发言的同事 Bot 名称和头像。
- **具体后果**：
  - 多智能体协作对话在安卓端无法区分“谁在给谁派活”还是“谁在向谁汇报”，也看不出消息来自哪个终端节点或分身。
- **修复方案**：
  - 在 `PulseMessage` 中保留 `fromNode`、`toBotId`、`toBotName`、`toNode`、`direction`；在 `NativeMessage` 和 `NativeLiveStreamBubble` 左侧增加带右下角角标的 `BotAvatar`，并区分 `kind == "bot_message"`（显示 `委派给 $toBotName`）与 `kind == "bot_reply"`（显示 `来自 $fromBotName 的协作汇报`）。

---

### BUG-A4-11 (`HIGH`)：Markdown 内联图片 `![alt](url)` 被降级为纯文本，且相对路径图片链接连点击跳转都失效
- **类别**：Markdown 渲染缺陷 (Markdown Image Rendering Bug)
- **文件与行号**：
  - `D:\bubuki\proactive-personal-agent\android\app\src\main\java\dev\bubuki\proactive\markdown\MarkdownModel.kt:186-196`
- **根因与代码证据**：
  ```kotlin
  is Image -> link(n.destination, s) {
      if (n.firstChild != null) appendInlines(n, s) else append(n.destination.orEmpty())
  }
  ```
  且 `link()`（`195` 行）要求 `url.startsWith("#vault:") || url.substringBefore(':', "").lowercase() in LINK_SCHEMES`（仅允许 `http, https, mailto, tel`）。
- **具体后果**：
  - 当 AI 在回复正文中输出 Markdown 图片（例如 `![截图](/workspace-media/uploads/xxx.png)` 或 `![图表](https://...)`）时，安卓端既不渲染图片，且对于 `/workspace-media/...` 或 `/stickers/...` 等相对路径连可点击链接都不是，直接变成死文本！
- **修复方案**：
  - 在 `MarkdownModel.kt` 中支持将独立或内联 `Image` 节点解析为图片块（复用 `PulseStickerImage` 渲染），并在 `link()` 中允许 `/` 开头的服务端相对路径通过 `model.serverUrl` 补全后点击预览。

---

### BUG-A4-12 (`HIGH`)：`loadStickerBitmap` 未走认证头、无并发去重，且 `stickerCache` 无上限永不回收导致大图 OOM 崩溃
- **类别**：内存泄漏与网络传输缺陷 (OOM & Unauthenticated Download)
- **文件与行号**：
  - `D:\bubuki\proactive-personal-agent\android\app\src\main\java\dev\bubuki\proactive\NativePulseActivity.kt:2818-2842`
  - `D:\bubuki\proactive-personal-agent\android\app\src\main\java\dev\bubuki\proactive\NativePulseScreen.kt:2994-3044`
- **根因与代码证据**：
  1. `private val stickerCache = ConcurrentHashMap<String, ImageBitmap>()`（`2818` 行）是无界 Map，从不淘汰旧图，且 `BitmapFactory.decodeByteArray(bytes, 0, bytes.size)` 未设置 `inSampleSize` 降采样。
  2. `loadStickerBitmap` 没有像 `avatarLoading`（`2819` 行）那样做并发去重集合（`stickerLoading`），Compose 重组时会针对同一个 URL 并发发起多次重复 HTTP 下载。
  3. 若 AI 输出了受保护的文件路径（如 `/api/files/download?path=...`），`loadStickerBitmap` 未携带 `X-Device-Token` / `Authorization` 头，直接返回 401 静默失败。
- **具体后果**：
  - 聊天室中包含多张手机高清截图或云桌面截图（`/workspace-media/...`，单张解码后占 10~30MB 内存）时，`stickerCache` 迅速耗尽堆内存引发 `OutOfMemoryError` 闪退。
- **修复方案**：
  - 将 `stickerCache` 改为基于字节大小的 `LruCache<String, ImageBitmap>`（例如上限 32MB），解码时使用 `inSampleSize` 限制最大边长（如 1440px），增加 `stickerLoading` 去重集合，并附带 `Authorization: Bearer $deviceToken` 请求头。

---

### BUG-A4-13 (`MEDIUM`)：纯图片/纯表情包 AI 消息无法长按或点击呼出操作菜单（无法重答/引用/看时间），纯附件用户消息点击“复制”会复制空字符串
- **类别**：聊天交互缺陷 (Chat UX Defect)
- **文件与行号**：
  - `D:\bubuki\proactive-personal-agent\android\app\src\main\java\dev\bubuki\proactive\NativePulseScreen.kt:2708, 2848, 2950-2965`
- **根因与代码证据**：
  1. 在 `NativeMessage`（`2950-2965` 行）中，当 AI 发送纯截图或纯表情包（`message.sticker.isNotBlank() && message.text.isBlank()`）时，`2950` 行渲染了 `PulseStickerImage`，但 `2955` 行 `if (message.text.isNotBlank() || isError)` 为 `false`！由于切换 `showActions = !showActions` 的点击事件只绑定在文字气泡 `Surface`（`2959` 行）上，用户无法呼出操作栏。
  2. 在用户消息气泡（`2708` 行）中，当用户只发附件未写文字时，`cleanBody == "请查看这些附件。"` 被替换为 `bodyPart = ""`；用户点击操作栏“复制”（`2848` 行 `model.copy(bodyPart)`）时，剪贴板被写入空字符串 `""`。
- **具体后果**：
  - 纯图片/表情包回复无法查看时间戳、无法引用回复、无法重答（Reroll）；纯附件消息复制出空白。
- **修复方案**：
  - 在 `PulseStickerImage` 支持长按或点击触发父级 `onToggleActions = { showActions = !showActions }`；用户消息复制时使用 `bodyPart.ifBlank { message.text }`。

---

### BUG-A4-14 (`MEDIUM`)：“日程与闹钟”面板 (`NativeSchedulesPanel`) 漏渲染一次性闹钟/倒计时 (`timers`)
- **类别**：UI 数据漏渲染 (Missing UI Section)
- **文件与行号**：
  - `D:\bubuki\proactive-personal-agent\android\app\src\main\java\dev\bubuki\proactive\NativePulseActivity.kt:1794`
  - `D:\bubuki\proactive-personal-agent\android\app\src\main\java\dev\bubuki\proactive\NativePulseScreen.kt:4684-4942`
- **根因与代码证据**：
  - `NativePulseActivity.kt:1794` 在 `openSchedules()` 中将 `/api/routines` 返回的 `"timers"` 存入了 `schedulesData`（`merged.put("timers", rRes?.optJSONArray("timers") ?: JSONArray())`）。
  - 但 `NativeSchedulesPanel`（`NativePulseScreen.kt:4688-4691`）只提取并渲染了 `routines`、`deadlines` 和 `watchdogs`，**完全漏掉了 `timers`**！
- **具体后果**：
  - 用户让助手设置的所有一次性闹钟或倒计时提醒（`timers`），在“日程与闹钟”页面完全看不见，也无法查看或管理。
- **修复方案**：
  - 在 `NativeSchedulesPanel` 中解析 `data?.optJSONArray("timers")` 并渲染“一次性闹钟 / 倒计时”列表卡片。

---

### BUG-A4-15 (`MEDIUM`)：`RootPhoneHarness` 下载文件到手机时返回的 `phone_path` 目录名与 `CloudFiles.download` 真实目录不一致
- **类别**：文件路径契约错误 (File Path Mismatch)
- **文件与行号**：
  - `D:\bubuki\proactive-personal-agent\android\app\src\main\java\dev\bubuki\proactive\CloudFiles.java:128`
  - `D:\bubuki\proactive-personal-agent\android\app\src\main\java\dev\bubuki\proactive\RootPhoneHarness.kt:427`
- **根因与代码证据**：
  - `CloudFiles.download`（`CloudFiles.java:128`）将文件保存在系统下载目录的 `"DeepSeek Claw"` 子目录下（`Environment.DIRECTORY_DOWNLOADS + "/DeepSeek Claw"`）。
  - 而 `RootPhoneHarness.kt:427`（`push_file` 命令）执行 `CloudFiles.download` 成功后，返回给服务端的路径却硬编码成了：
    ```kotlin
    .put("phone_path", "/sdcard/Download/Pulse Bot/$name")
    ```
- **具体后果**：
  - 当 AI 调用 `push_file` 把服务端文件传到手机后，后续再去访问 `/sdcard/Download/Pulse Bot/xxx` 时会找不到文件（实际在 `/sdcard/Download/DeepSeek Claw/xxx`）。
- **修复方案**：
  - 将 `RootPhoneHarness.kt:427` 的返回路径修正为 `/sdcard/Download/DeepSeek Claw/$name`（或直接由 `CloudFiles.download` 返回真实写入路径）。

---

### BUG-A4-16 (`MEDIUM`)：`MarkdownText.kt` 中的 `SvgBlock` 每次滑出屏幕都泄漏原生 `WebView` 实例
- **类别**：原生资源泄漏 (WebView Resource Leak)
- **文件与行号**：
  - `D:\bubuki\proactive-personal-agent\android\app\src\main\java\dev\bubuki\proactive\markdown\MarkdownText.kt:190-224`
- **根因与代码证据**：
  - `SvgBlock` 在 `LazyColumn` 聊天列表项中使用 `AndroidView(factory = { ctx -> WebView(ctx)... })` 渲染 SVG，但未配置 `onRelease = { webView -> webView.stopLoading(); webView.destroy() }`，且未设置 `backgroundColor = Color.TRANSPARENT` 的软件渲染兼容。
- **具体后果**：
  - 包含 SVG 图表的消息在列表上下滑动反复进出屏幕时，原生 `WebView` 实例不会被 `destroy()`，引发内存持续增长与滑动掉帧。
- **修复方案**：
  - 为 `SvgBlock` 的 `AndroidView` 添加 `onRelease = { it.stopLoading(); it.loadUrl("about:blank"); it.destroy() }`。

---

### BUG-A4-17 (`MEDIUM`)：助手编辑面板 (`NativeBotEditorPanel`) 缺失头像上传、`editBotColor` 与 `editBotNotify` 开关，且原生界面无工作区文件浏览器入口
- **类别**：功能缺失与死状态字段 (Dead UI State / Missing Controls)
- **文件与行号**：
  - `D:\bubuki\proactive-personal-agent\android\app\src\main\java\dev\bubuki\proactive\NativePulseActivity.kt:308-309, 1598-1599, 1650-1653`
  - `D:\bubuki\proactive-personal-agent\android\app\src\main\java\dev\bubuki\proactive\NativePulseScreen.kt:4142-4236`
  - `D:\bubuki\proactive-personal-agent\android\app\src\main\java\dev\bubuki\proactive\AvatarImages.java:1-44`
- **根因与代码证据**：
  1. `NativePulseState` 定义了 `editBotColor`、`editBotNotify` 并在 `saveBotEditor()` 中提交给服务端，但 `NativeBotEditorPanel`（`4142-4236` 行）没有任何 UI 控件可修改这两个字段。
  2. 项目实现了 `AvatarImages.compressForUpload` 和 `/api/bots/{id}/avatar` 头像加载，但 `NativeBotEditorPanel` 没有提供上传/更换助手头像的按钮。
  3. `CloudFiles.download` 仅在旧版 WebView `MainActivity.java:163` 中绑定，在 `NativePulseActivity` / `NativePulseScreen` 中无任何调用入口。
- **具体后果**：
  - 用户无法在原生 App 中修改助手主题色、主动通知开关、助手头像，也无法浏览或下载云端工作区文件。
- **修复方案**：
  - 在 `NativeBotEditorPanel` 中补齐头像选择上传、主题色选择器与主动通知 `Switch`；在设置或聊天附件栏增加工作区文件浏览与下载面板。

---

### BUG-A4-18 (`LOW`)：`AgentNotificationListener.java` 通知移除匹配 Key 不一致导致 `"active"` 永远为 `true`，且时间戳缺少显式时区
- **类别**：通知监听逻辑缺陷 (Notification Listener Bug)
- **文件与行号**：
  - `D:\bubuki\proactive-personal-agent\android\app\src\main\java\dev\bubuki\proactive\AgentNotificationListener.java:78-86, 117-143`
- **根因与代码证据**：
  1. `recordPassive`（`117, 143` 行）向 `recentBuffer` 存入通知时使用的 Key 是 `dedupKey = pkg + "|" + title + "|" + text`；
  2. 而 `onNotificationRemoved`（`78-80` 行）查找 `recentBuffer` 时使用的 Key 却是 `sbn.getKey()`（或 `pkg + ":" + id`）！两个 Key 格式完全不同，`recentBuffer.get(key)` 永远返回 `null`！
  3. `128` 行 `new SimpleDateFormat("MM-dd HH:mm:ss", Locale.CHINA)` 未调用 `setTimeZone(TimeZone.getTimeZone("Asia/Shanghai"))`。
- **具体后果**：
  - 手机上已被用户划掉或清除的通知，在 `recentBuffer` 中 `"active"` 字段永远保持 `true`，误导 AI 认为旧通知依然挂在通知栏上。
- **修复方案**：
  - 在 `recordPassive` 中同时记录 `"sbn_key": sbn.getKey()`，并在 `onNotificationRemoved` 中遍历 `recentBuffer.values()` 匹配 `sbn_key` 将 `"active"` 置为 `false`；为 `SimpleDateFormat` 显式设置 `Asia/Shanghai` 时区。


---
# 【分卷】Auditor-5 (服务端 brain.py 下半部主循环 / 并行与多Bot互联) (`audit_5_brain_part2.md`)

# Audit Report: `brain.py` (Lines 2800–5814) — Auditor-5

**Scope**: `D:\bubuki\proactive-personal-agent\server\brain.py` (Lines 2800–5814: Tool Executors, Phone Workspace, Cloud Desktop, Bot Interconnect / Delegate / BG Task / Custom Tool, Parallel vs. Serial Tool Loop, Streaming & Cancellation).

---

## Summary of Findings

| Bug ID | Severity | Category | Location (`brain.py`) | Short Title |
| :--- | :--- | :--- | :--- | :--- |
| **B5-01** | `CRITICAL` | Parallel vs. Serial Loop Split-Brain | Lines 4842–4924 vs. 4925–5005 | Parallel tool execution branch (`can_parallel`) omits 3x duplicate-call loop breaker, `custom_tool` schema hot-reload, `present_choices` early exit, and exception isolation |
| **B5-02** | `HIGH` | Multi-Bot Interconnect / Turn Lifecycle | Lines 3953–3955, 4138–4140, 5006–5025 | `_tool_send_message` sets `_already_emitted_turn_bubbles=True` and forces `_chat` to return early before Caller LLM can process the reply or sibling tool results |
| **B5-03** | `HIGH` | Concurrency / State Corruption | Lines 3980–3985, 4055, 4081 | `_reply_box_by_sig[cur_sig]` key collision when multiple `send_message` calls run in parallel or overlap for the same caller bot |
| **B5-04** | `HIGH` | Cancellation / Sub-Turn Deadlock | Lines 4058–4071, 4611, 4785, 4805, 5125–5141 | Sub-turn in `_tool_send_message` mints a new `turn_id` and switches `bot_context`, blinding `_chat` to caller `stop_immediately` and swallowing `CancelledError` |
| **B5-05** | `HIGH` | Schema Key Mismatch (`type` vs `role`) | Lines 3109–3119 vs. 3527–3534, 3544–3551 | `_tool_phone_workspace(op="tap_and_type")` checks `n.get('type')` instead of `n.get('role')`, breaking vision auto-detection of `EditText` input boxes |
| **B5-06** | `HIGH` | Concurrency / Lock Hierarchy Split-Brain | Lines 4204, 4610, 5163, 5266 | `_chat` only locks `_get_bot_lock(bot_id, src)` while `_research` and `run_scheduled` only lock `self.lock`, allowing simultaneous execution on the same bot |
| **B5-07** | `HIGH` | Cross-Node State Overwrite | Lines 4519–4521, 4635–4639, 5147 | Single-slot `executions_by_bot[bot_id]` and `active_tasks_by_bot[bot_id]` overwrite across `phone` and `pc` nodes |
| **B5-08** | `MEDIUM` | Phone Workspace State & `op="open"` Normalization | Lines 3159–3180, 3260–3262, 3358–3365 | `op="open"` with empty target converts to invalid `op="launch", app=""`, and singleton `_vdisplay_currently_open` is set prematurely & shared across concurrent turns |
| **B5-09** | `MEDIUM` | Coordinate Conversion & Falsiness Bug | Lines 3183–3207, 3444–3452 | `_resolve_phone_grid_or_norm` ignores float `0..1 (x, y)` promised by docstring, and `long_press` ignores `ref`/`target` and mishandles `x=0`/`y=0` |
| **B5-10** | `MEDIUM` | Permission / Tool Filtering Leak | Lines 4765–4766 vs. 4998–5001 | Serial `custom_tool` schema refresh forgets to strip mobile-only tools (`phone_workspace`, `phone_macro`, `notify_user`, `emergency_alarm`) on PC nodes |
| **B5-11** | `MEDIUM` | Scheduled Task Tooling & GUI Collision | Lines 5323–5324, 5378–5385 | `run_scheduled` uses static `TOOLS` (omitting `custom_tool` plugins) and runs all tool calls via `asyncio.gather` without `stateful_gui_count` guard |
| **B5-12** | `MEDIUM` | Post-Tool Summary Fallback | Lines 5042–5073 | Final summary `self.complete` leaves `tools` enabled with `tool_choice='auto'`, silently dropping any emitted `tool_calls` and falling back to raw JSON dump |

---

## Detailed Bug Reports

### [B5-01] `CRITICAL` — Parallel Tool Execution Branch (`can_parallel`) vs. Serial Branch Split-Brain
- **Category**: Logic Contradiction / Loop Protection Bypass / State Desync
- **File & Lines**: `D:\bubuki\proactive-personal-agent\server\brain.py`, lines 4842–4924 (parallel branch) vs. lines 4925–5005 (serial branch)
- **Root Cause & Exact Code Evidence**:
  In `_chat`, whenever `len(batch_calls) > 1 and stateful_gui_count <= 1` (line 4847), execution enters the `if can_parallel:` branch (lines 4850–4924) instead of the `else:` serial branch (lines 4925–5005). Four essential safeguards implemented in the serial branch are completely missing from the parallel branch:
  1. **No 3x duplicate tool call detection (`recent_call_sigs`)**:
     - Serial branch (lines 4932–4933, 4948–4949):
       ```python
       sig = f"{name}:{json.dumps(args, sort_keys=True, ensure_ascii=False)}"
       recent_call_sigs.append(sig)
       ...
       if len(recent_call_sigs) >= 3 and recent_call_sigs[-1] == recent_call_sigs[-2] == recent_call_sigs[-3]:
           result = {'error': f'检测到连续 3 次重复调用相同工具与参数 ({name})，已自动拦截...'}
       ```
     - Parallel branch (lines 4850–4924): Never updates or checks `recent_call_sigs`. If the model repeatedly emits 2+ tool calls per step, it bypasses duplicate-call interception for all 40 iterations.
  2. **No `custom_tool` dynamic schema refresh**:
     - Serial branch (lines 4993–5001) refreshes `all_schemas`, `plugin_names`, `allowed`, and `available_tools` when `custom_tool` is called with `"create"` or `"delete"`.
     - Parallel branch (lines 4850–4924) omits this block. Creating a custom tool in parallel with any other tool (e.g. `read_file` + `custom_tool`) leaves `available_tools` stale for the remainder of the turn.
  3. **No `present_choices` early-exit `break`**:
     - Serial branch (lines 5004–5005):
       ```python
       if all(c['function']['name'] == 'present_choices' for c in batch_calls) and len(final_turn_text.strip()) >= 12:
           break
       ```
     - Parallel branch omits this check.
  4. **No exception isolation in `asyncio.gather` and insufficient `stateful_gui_count` guard**:
     - Line 4887 runs `parallel_results = await asyncio.gather(*[_run_one(c_item) for c_item in batch_calls])` without `try/except Exception` inside `_run_one`. If one tool throws an unexpected exception, all sibling tool results in the batch are lost and `running_entries` remain stuck in `status: 'running'`.
     - Lines 4842–4846 only count `phone_workspace` and `cloud_desktop` toward `stateful_gui_count`, allowing multiple `phone_macro`, `dsh_workspace`, or `send_message` calls to run concurrently in `asyncio.gather`.
- **Concrete Impact**:
  Infinite repeated tool loops when the LLM emits parallel tool calls; newly created `custom_tool` plugins cannot be invoked in the same turn if created in a multi-tool step; parallel `send_message` calls race on shared state; any single tool exception aborts the entire turn.
- **Recommended Fix**:
  Unify tool execution preprocessing/postprocessing between parallel and serial branches: record and check `recent_call_sigs` before executing each tool in `_run_one`, wrap tool execution in `try/except Exception as e` (excluding ` asyncio.CancelledError`) returning `{'ok': False, 'error': str(e)}`, include `phone_macro` and `send_message` in `stateful_gui_count`, and run the `custom_tool` schema refresh (including PC tool exclusions) and `present_choices` break after both branches.

---

### [B5-02] `HIGH` — `_tool_send_message` Forces Premature Turn Exit in `_chat`, Dropping Caller Post-Processing & Sibling Tool Results
- **Category**: Multi-Bot Interconnect / Turn Lifecycle Defect
- **File & Lines**: `D:\bubuki\proactive-personal-agent\server\brain.py`, lines 3953–3955, 4138–4140, 4892–4897, 4961–4966, 5006–5025
- **Root Cause & Exact Code Evidence**:
  When `self.hop_depth.get() == 0`, `_tool_send_message` emits `outbound_msg` (Bubble 1) and `reply_msg_in_caller` / `sess_reply_msg` (Bubble 2) directly into the caller's chat room, and returns:
  ```python
  '_already_emitted_turn_bubbles': bool(reply_msg_in_caller is not None),
  '_final_emitted_msg': reply_msg_in_caller,
  ```
  In `_chat` (lines 5006–5025), right after the tool batch finishes:
  ```python
  if self.hop_depth.get() == 0 and final_interconnect_msg is not None:
      ...
      return final_interconnect_msg
  ```
  `_chat` immediately returns `final_interconnect_msg` without allowing the caller bot's LLM to run another step!
- **Concrete Impact**:
  1. **Multi-step workflows break after `send_message`**: If the user instructs Bot A to *"ask Research Assistant for X and then save X into a file / summarize it for me"*, Bot A calls `send_message`, Research Assistant replies, and `_chat` immediately terminates at line 5022. Bot A never sees the reply and never writes the file or answers the user.
  2. **Sibling tools in the same batch lose their trace and output**: Because `outbound_msg` is emitted at line 3997 *before* `send_message` completes, and `reply_msg_in_caller` (line 4119) only carries `target_bot`'s `trace_items`, any other tool executed after or alongside `send_message` in the same batch has its `trace` and output discarded when `_chat` returns early at line 5022.
  3. **Multiple `send_message` calls in one batch**: Each call with `hop_depth == 0` emits its own `outbound_msg` (duplicating `caller_thought` and `outbound_trace`), and `final_interconnect_msg` overwrites earlier ones.
- **Recommended Fix**:
  Do not unconditionally terminate the caller's `_chat` loop when `final_interconnect_msg` is set if the caller still needs to process the reply or if other tools were called in the batch; or let the caller LLM decide whether to finish or perform follow-up tool calls, and only skip emitting a redundant final bubble if the caller LLM produces no additional tool calls or new synthesis beyond the interconnect reply.

---

### [B5-03] `HIGH` — `_reply_box_by_sig[cur_sig]` Key Collision During Parallel or Overlapping `send_message` Calls
- **Category**: Concurrency / Race Condition
- **File & Lines**: `D:\bubuki\proactive-personal-agent\server\brain.py`, lines 3980–3985, 4055, 4081
- **Root Cause & Exact Code Evidence**:
  In `_tool_send_message`:
  ```python
  self._reply_box_by_sig[cur_sig] = []
  ...
  try:
      peer_res = await self._chat(...)
  finally:
      ...
      replies = self._reply_box_by_sig.pop(cur_sig, [])
  ```
  `self._reply_box_by_sig` is keyed solely by `cur_sig` (`f"{cur_src}:{from_id}"`). When a bot invokes `send_message` twice in the same parallel batch (e.g. querying two colleague bots concurrently via `asyncio.gather` at line 4887):
  - The second call overwrites `self._reply_box_by_sig[cur_sig] = []`.
  - The first call to finish pops `cur_sig` from `self._reply_box_by_sig` in its `finally` block (line 4081).
  - When the second target bot calls `send_message` back to `cur_sig`, `if target_sig in self._reply_box_by_sig:` (line 3980) evaluates to `False`! It then falls through to line 4052 (`if target_sig in chain:`) and fails with `'检测到 Bot 循环调用链路...已自动拦截'`.
- **Concrete Impact**:
  Parallel `send_message` calls from the same bot corrupt each other's reply box and trigger false-positive cycle detection errors on the replying bot.
- **Recommended Fix**:
  Key `_reply_box_by_sig` by `(cur_sig, target_sig)` or a stack/counter per `cur_sig`, or disallow parallel execution of multiple `send_message` calls in `can_parallel`.

---

### [B5-04] `HIGH` — Sub-Turn Cancellation Blind Spot in `_tool_send_message` & `_chat`
- **Category**: Cancellation / State Machine Flaw
- **File & Lines**: `D:\bubuki\proactive-personal-agent\server\brain.py`, lines 4058–4071, 4611, 4785, 4805, 5125–5141
- **Root Cause & Exact Code Evidence**:
  When `_tool_send_message` wakes `target_bot` via `await self._chat(...)` (lines 4058–4071):
  ```python
  depth_token = self.hop_depth.set(depth + 1)
  ...
  bot_token = self.bot_context.set(target_id)
  ...
  sub_turn_token = self.turn_id_var.set('turn-' + secrets.token_hex(6))
  ```
  If the user stops or preempts the parent bot (`stop_immediately(bot_id=from_id, source=cur_src)`):
  1. `stop_immediately` adds the **parent** `turn_id` to `self._stopped_turn_ids` and increments `self._abort_epoch_by_bot[from_id]`.
  2. Inside the sub-turn's `_chat`, `self.turn_id_var.get()` is the new `sub_turn_token` (NOT in `_stopped_turn_ids`), and `bot_id` is `target_id` (whose `_abort_epoch_by_bot[target_id]` was NOT incremented). Thus lines 3232, 4611, 4785, and 4805 never detect that the parent turn was stopped!
  3. Even when `active_task.cancel()` raises `asyncio.CancelledError` inside the sub-turn's `_chat`, lines 5125–5141 catch `asyncio.CancelledError` and **return** `{'stopped': True}` (or emit a partial reply) instead of re-raising `CancelledError` when `self.hop_depth.get() > 0`! Consequently, `_tool_send_message` receives `peer_res = {'stopped': True}` and continues executing as if the sub-turn completed normally.
- **Concrete Impact**:
  Stopping or preempting a turn while it is waiting inside a cross-bot `send_message` sub-turn fails to abort the parent turn cleanly, allowing the parent turn to continue running after cancellation.
- **Recommended Fix**:
  1. In `_chat`'s `except asyncio.CancelledError:` block (line 5125), if `self.hop_depth.get() > 0`, always re-raise `asyncio.CancelledError` (or check if the root caller turn was stopped).
  2. Propagate the root `turn_id` (or check all turns/bots in `self.call_chain_var.get()`) against `_stopped_turn_ids` and `_abort_epoch_by_bot`.

---

### [B5-05] `HIGH` — `_tool_phone_workspace(op="tap_and_type")` Checks `n.get('type')` Instead of `n.get('role')`
- **Category**: Contract / Schema Key Mismatch
- **File & Lines**: `D:\bubuki\proactive-personal-agent\server\brain.py`, lines 3109–3119 vs. lines 3527–3534 & 3544–3551
- **Root Cause & Exact Code Evidence**:
  `_vision_extract_ui_tree` constructs vision nodes with key `'role'` (lines 3109–3119):
  ```python
  node_obj = {
      'ref': ref,
      'role': role,
      'text': txt,
      ...
  }
  ```
  However, in `_tool_phone_workspace` when `op == 'tap_and_type'` searches `self._last_vision_nodes` for an input box (lines 3529–3531 and 3546–3548):
  ```python
  n for n in (self._last_vision_nodes or [])
  if str(n.get('type') or '').lower() in ('input', 'edittext', 'search')
  or any(k in str(n.get('text') or '') for k in ('搜索', '输入', '发消息', '填写', 'Search'))
  ```
  Because `'type'` does not exist on vision nodes (`'role'` is used instead), `n.get('type')` is always `None`.
- **Concrete Impact**:
  Any vision-detected input box (`role="EditText"`) whose label/text does not contain one of the 5 hardcoded keywords (`搜索`, `输入`, `发消息`, `填写`, `Search`) fails auto-detection in `tap_and_type` and returns an error.
- **Recommended Fix**:
  Check `str(n.get('role') or n.get('type') or '').lower() in ('input', 'edittext', 'search')`.

---

### [B5-06] `HIGH` — Lock Hierarchy Mismatch Between `_chat` and `_research` / `run_scheduled`
- **Category**: Concurrency / Race Condition
- **File & Lines**: `D:\bubuki\proactive-personal-agent\server\brain.py`, lines 4204, 4610, 5163, 5266
- **Root Cause & Exact Code Evidence**:
  - `curiosity` acquires `async with self.lock, self._get_bot_lock(target_id, 'phone'):` (line 4204).
  - `_chat` acquires ONLY `async with self._get_bot_lock(bot_id, src):` (line 4610) and never acquires `self.lock`.
  - `_research` acquires ONLY `async with self.lock:` (line 5163) and never acquires `self._get_bot_lock(bot_id, 'phone')`.
  - `run_scheduled` acquires ONLY `async with self.lock:` (line 5266) and never acquires `self._get_bot_lock(target_id, 'phone')`.
  Furthermore, `_research` (line 5173) and `run_scheduled` (line 5277) create `my_exec` without `'source': 'phone'` and without setting `self.executions_by_node_bot[f'phone:{bot_id}']`.
- **Concrete Impact**:
  If a user sends a chat message while `run_scheduled` or `_research` is running for the same bot:
  1. `self._get_bot_lock(bot_id, 'phone').locked()` is `False`, and `normalize_node_source(cur_exec_for_bot.get('source'))` may not match or preemption wait loop (lines 4472–4475) immediately exits because `_get_bot_lock` is unlocked.
  2. `_chat` runs concurrently with `run_scheduled` / `_research` on the exact same `bot_id`, corrupting `self.execution`, `self.executions_by_bot[bot_id]`, `self.active_tasks_by_bot[bot_id]`, and `live_stream`.
- **Recommended Fix**:
  Ensure `_research` and `run_scheduled` also acquire `self._get_bot_lock(target_id, 'phone')` (in consistent lock order `async with self.lock, self._get_bot_lock(target_id, 'phone'):`) and populate `'source': 'phone'` and `self.executions_by_node_bot[f'phone:{target_id}']`.

---

### [B5-07] `HIGH` — Single-Slot `executions_by_bot[bot_id]` and `active_tasks_by_bot[bot_id]` Overwrite Across Nodes (`phone` vs `pc`)
- **Category**: Cross-Node State Collision
- **File & Lines**: `D:\bubuki\proactive-personal-agent\server\brain.py`, lines 4449–4454, 4519–4521, 4635–4639, 5147
- **Root Cause & Exact Code Evidence**:
  In `enqueue_chat` (line 4520) and `_chat` (lines 4635, 4638):
  ```python
  self.active_tasks_by_bot[bot_id] = cur_task
  ...
  self.executions_by_node_bot[f'{src}:{bot_id}'] = my_exec
  self.executions_by_bot[bot_id] = my_exec
  ```
  When the same `bot_id` (e.g. `'companion'`) is active on both `phone` and `pc` concurrently:
  - The second node overwrites `self.executions_by_bot[bot_id]` and `self.active_tasks_by_bot[bot_id]`.
  - `enqueue_chat` (line 4449) checks `cur_exec_for_bot = self.executions_by_bot.get(bot_id)` instead of `self.executions_by_node_bot.get(f'{src}:{bot_id}')`.
  - When one node finishes and calls `self._clear_bot_stream(bot_id, source=src)` (line 5147), or when `stop_immediately` cancels `active_tasks_by_bot[bot_id]`, it can cancel or clear the wrong node's execution/task.
- **Concrete Impact**:
  Concurrent chats with the same assistant on Phone and PC clobber each other's active task handle and execution status.
- **Recommended Fix**:
  Track active tasks per `(src, bot_id)` in `self.active_tasks_by_node_bot[f'{src}:{bot_id}']` and query `self.executions_by_node_bot.get(f'{src}:{bot_id}')` first in `enqueue_chat`, `chat`, and `_preempt_if_bot_busy`.

---

### [B5-08] `MEDIUM` — `_tool_phone_workspace` `op="open"` Empty Target & Singleton `_vdisplay_currently_open` Race
- **Category**: Tool Normalization & State Lifecycle
- **File & Lines**: `D:\bubuki\proactive-personal-agent\server\brain.py`, lines 3159–3180, 3260–3262, 3358–3365
- **Root Cause & Exact Code Evidence**:
  1. Lines 3358–3365:
     ```python
     if op == 'open':
         u_cand = str(args.get('uri') or args.get('url') or args.get('target') or args.get('app') or args.get('package') or args.get('text') or '').strip()
         if re.match(r'^[a-zA-Z][a-zA-Z0-9+.-]*://', u_cand):
             op = 'open_uri'
             args = {**args, 'op': 'open_uri', 'uri': u_cand}
         else:
             op = 'launch'
             args = {**args, 'op': 'launch', 'app': u_cand}
     ```
     If `phone_workspace(op="open")` is called without an app or URI (to open/check the virtual display), `u_cand` is `""`, converting the call into `op="launch", app=""`, which fails on Android.
  2. Lines 3260–3262 set `self._used_phone_in_turn = True` and `self._vdisplay_currently_open = True` before argument validation or RPC execution. Furthermore, these flags are singleton attributes on `self`, so any concurrent turn finishing and calling `_auto_release_phone_if_used()` (line 3159) will send `auto_release` while another turn is still using the virtual display.
- **Concrete Impact**:
  `phone_workspace(op="open")` with empty `app` errors out instead of opening/inspecting the virtual display; concurrent turns prematurely release the virtual display.
- **Recommended Fix**:
  When `op == 'open'` and `not u_cand`, normalize `op` to `'ui_tree'` (or `'connect'`). Set `_used_phone_in_turn` / `_vdisplay_currently_open` only after a GUI RPC actually succeeds.

---

### [B5-09] `MEDIUM` — `_resolve_phone_grid_or_norm` Ignores Float `0..1 (x, y)` and `long_press` Ignores `ref`/`target` & `0` Coordinates
- **Category**: Coordinate Resolution Defect
- **File & Lines**: `D:\bubuki\proactive-personal-agent\server\brain.py`, lines 3183–3207, 3444–3452
- **Root Cause & Exact Code Evidence**:
  1. `_resolve_phone_grid_or_norm` docstring (line 3185) states:
     `Convert coarse grid (A1..F10), normalized 0-1000 (nx, ny), or float 0..1 (x, y) into exact virtual display pixel coordinates (px, py).`
     Yet lines 3188–3207 only check `grid` and `nx`/`ny`, never converting float `0 < x < 1` or `0 < y < 1` in `args['x'], args['y']`.
  2. In `op == 'long_press'` (lines 3444–3448):
     ```python
     lx = int(args.get('x') or (cur_w // 2))
     ly = int(args.get('y') or (cur_h // 2))
     ```
     `long_press` ignores `ref`, `target`, `text`, and `view_id` (unlike `tap`), so `phone_workspace(op="long_press", ref="@e3")` blindly long-presses the screen center `(cur_w // 2, cur_h // 2)`! Also `args.get('x') or ...` treats `x=0` as falsy.
- **Concrete Impact**:
  Calling `long_press` with a node `ref` or `target` long-presses the center of the screen instead of the target element; float `0..1` coordinates in `x, y` are truncated to `(0, 0)`.
- **Recommended Fix**:
  Support float `0 < x <= 1.0` in `_resolve_phone_grid_or_norm`, and resolve `ref`/`target` nodes in `long_press` before falling back to `cur_w // 2, cur_h // 2`.

---

### [B5-10] `MEDIUM` — Serial `custom_tool` Schema Refresh Re-Enables Mobile-Only Tools on PC Nodes
- **Category**: Tool Routing / Permission Leak
- **File & Lines**: `D:\bubuki\proactive-personal-agent\server\brain.py`, lines 4765–4766 vs. 4998–5001
- **Root Cause & Exact Code Evidence**:
  Initial setup in `_chat` (lines 4764–4767):
  ```python
  allowed = set(bot.get('tools') or [t['function']['name'] for t in all_schemas]) | plugin_names
  if src in ('pc', 'pc2'):
      allowed -= {'phone_workspace', 'phone_macro', 'notify_user', 'emergency_alarm'}
  available_tools = [t for t in all_schemas if t['function']['name'] in allowed]
  ```
  When `custom_tool` creates/deletes a tool in the serial branch (lines 4998–5001), `allowed` is recomputed without subtracting `{'phone_workspace', 'phone_macro', 'notify_user', 'emergency_alarm'}` when `src in ('pc', 'pc2')`.
- **Concrete Impact**:
  After creating or deleting a custom tool on a PC node, mobile-only tools are exposed to the PC bot for the rest of the turn.
- **Recommended Fix**:
  Re-apply `if src in ('pc', 'pc2'): allowed -= {'phone_workspace', 'phone_macro', 'notify_user', 'emergency_alarm'}` during the `custom_tool` schema refresh.

---

### [B5-11] `MEDIUM` — `run_scheduled` Omits Dynamic `custom_tool` Plugins and Runs Parallel GUI Tools Without Guard
- **Category**: Scheduled Task Execution Defect
- **File & Lines**: `D:\bubuki\proactive-personal-agent\server\brain.py`, lines 5323–5324, 5378–5385
- **Root Cause & Exact Code Evidence**:
  1. Lines 5323–5324 use the static `TOOLS` constant instead of `self.harness.all_tools()` and omit `self.harness.extension_registry.tool_plugins.keys()`.
  2. Lines 5384 executes all tool calls in `batch_s` concurrently via `asyncio.gather` without checking `stateful_gui_count` or routing `pc_shell`/`pc_fs`.
- **Concrete Impact**:
  Scheduled tasks cannot invoke user/AI-created custom tools (`custom_tool`), and multiple `phone_workspace` operations in a single scheduled step collide in parallel.
- **Recommended Fix**:
  Build `available_tools` from `self.harness.all_tools()` + `plugin_names` (minus `{'notify_user', 'schedule_task'}`), and serialize tool execution when `stateful_gui_count > 1`.

---

### [B5-12] `MEDIUM` — Post-Tool Summary Call in `_chat` Leaves `tools` Active and Drops Returned `tool_calls`
- **Category**: LLM Loop Termination Defect
- **File & Lines**: `D:\bubuki\proactive-personal-agent\server\brain.py`, lines 5042–5073
- **Root Cause & Exact Code Evidence**:
  At lines 5047–5051, when `_chat` requests a final natural-language summary after tool execution (`if (not accumulated_texts or not last_step_text) and trace:`), it calls `self.complete(..., tools=available_tools or None, big=use_big)` with default `tool_choice='auto'`. If the LLM responds with another `tool_calls` and empty `content`, `final_msg.get('tool_calls')` is ignored and lines 5071–5073 dump raw truncated tool JSON (`已执行 \`...\`, 结果：{...}`) as the assistant's reply.
- **Concrete Impact**:
  Users occasionally receive raw JSON tool dumps instead of a natural-language answer when the model attempts one more tool call on the summary prompt.
- **Recommended Fix**:
  Either execute the returned `tool_calls` or pass `tools=None` / `tool_choice='none'` when forcing the final post-loop summary.


---
# 【分卷】Auditor-6 (服务端 main.py API / storage.py / vault_manager.py / assistants.py / 安全模块) (`audit_6_main_storage_vault.md`)

# Audit Report 6: `main.py` Gateway, `storage.py`, `vault_manager.py`, Identity & Security Modules

**Auditor**: Auditor-6 (`43931fce-dbf2-490a-b0e6-9655d0e7804a`)
**Scope**:
1. `D:\bubuki\proactive-personal-agent\server\main.py`
2. `D:\bubuki\proactive-personal-agent\server\storage.py`
3. `D:\bubuki\proactive-personal-agent\server\vault_manager.py`
4. `D:\bubuki\proactive-personal-agent\server\assistants.py`
5. `D:\bubuki\proactive-personal-agent\server\bot_identity.py`
6. `D:\bubuki\proactive-personal-agent\server\escalation.py`, `security.py`, `sandbox.py`

---

## Part 1: Findings in `storage.py`, `vault_manager.py`, `assistants.py`, `bot_identity.py`, `escalation.py`, `security.py`, `sandbox.py`

### [BUG-601] CRITICAL | Security & Credential Leak: Hardcoded Third-Party API Key in `storage.py` and Plaintext Secret Mirroring to `workspace/settings.json`
- **Category**: Security / Credential Exposure
- **File(s)**:
  - `D:\bubuki\proactive-personal-agent\server\storage.py` (Lines 49–51, 294–308)
  - `D:\bubuki\proactive-personal-agent\server\sandbox.py` (Lines 23–24)
- **Root Cause & Code Evidence**:
  1. `Config` in `storage.py:49-51` hardcodes a live proxy IP and plaintext API key as default values:
     ```python
     fallback_base_url: str = Field('http://149.104.25.8:3000/v1', max_length=500)
     fallback_api_key: str = Field('sk-daye-aggregate-2026', max_length=4096)
     fallback_model: str = Field('commandcode/deepseek/deepseek-v4.1-flash', max_length=200)
     ```
  2. `Store._sync_to_workspace()` (`storage.py:294-308`) writes both `api_key` and `fallback_api_key` in plaintext into `workspace/settings.json`:
     ```python
     settings_payload = {
         'api_base_url': self.config.effective_base_url(),
         'api_key': self.config.effective_api_key(),
         'default_model': self.config.effective_model(),
         'fallback_base_url': self.config.fallback_base_url,
         'fallback_api_key': self.config.fallback_api_key,
         ...
     }
     atomic_write(self.settings_ws_path, json.dumps(settings_payload, ensure_ascii=False, indent=2))
     ```
- **Concrete Impact**:
  - Any workspace file listing/download endpoint (`/api/workspace/files/settings.json`), prompt injection via `read_file("settings.json")` or `run_bash`, or workspace sync leaks the user's primary and fallback API keys in plaintext.
- **Recommended Fix**:
  - Move fallback defaults to environment variables (`os.environ.get(...)`) with empty string fallback; redact or omit raw `api_key` / `fallback_api_key` when writing `workspace/settings.json` (or ignore masked values when syncing back in `sync_from_workspace`).

---

### [BUG-602] HIGH | Config Split-Brain & Data Loss: `_sync_to_workspace` + `sync_from_workspace` + `update_config` Overwrites `big_brain` Config with `small_brain` Values
- **Category**: Logic Contradiction / Silent Config Corruption
- **File(s)**: `D:\bubuki\proactive-personal-agent\server\storage.py` (Lines 294–308, 376–395, 404–430)
- **Root Cause & Code Evidence**:
  1. `_sync_to_workspace()` writes `settings.json` using `self.config.effective_api_key()` (`big=False`) and `self.config.effective_model()` (`big=False`), which return `small_brain.api_key` and `small_brain.model`:
     ```python
     'api_base_url': self.config.effective_base_url(),
     'api_key': self.config.effective_api_key(),
     'default_model': self.config.effective_model(),
     ```
  2. When `sync_from_workspace(force=True)` runs or `workspace/settings.json` is edited by a tool, lines 382–393 read `api_base_url`, `api_key`, and `default_model` from `settings.json` and pass them to `self.update_config(pending_config_update)`.
  3. `update_config()` (`storage.py:416-425`) then unconditionally overwrites **both** `small_brain` and `big_brain` with those values:
     ```python
     if unified_url is not None:
         data['small_brain']['base_url'] = unified_url
         data['big_brain']['base_url'] = unified_url
     if unified_key is not None and unified_key != '':
         data['small_brain']['api_key'] = unified_key
         data['big_brain']['api_key'] = unified_key
     if unified_model is not None and unified_model != '':
         data['small_brain']['model'] = unified_model
         data['big_brain']['model'] = unified_model
     ```
  4. Additionally, if a user intentionally clears `api_key` or `default_model` by passing `""`, `unified_key != ''` prevents clearing `small_brain.api_key` and `big_brain.api_key`, so `effective_api_key()` continues returning the old key.
- **Concrete Impact**:
  - Any separate `big_brain` model/key/base_url configuration is silently overwritten by `small_brain`'s configuration whenever `settings.json` is synced back or when top-level unified fields are included in a config update.
- **Recommended Fix**:
  - Only overwrite `small_brain` / `big_brain` sub-configs if the caller did not explicitly configure them or when explicitly updating unified config; include `small_brain` and `big_brain` in `settings.json` or preserve distinct brain settings; allow explicit key clearing when requested.

---

### [BUG-603] HIGH | Silent Failure & Infinite Disk Re-Read Loop in `Store.sync_from_workspace()`
- **Category**: Silent Exception Swallowing / Performance & State Bug
- **File(s)**: `D:\bubuki\proactive-personal-agent\server\storage.py` (Lines 317–397)
- **Root Cause & Code Evidence**:
  - `sync_from_workspace()` has three `except Exception: pass` blocks (lines 352, 373, 390) around parsing `bots.json`, `routines.json`, and `settings.json`.
  - When `mtime != getattr(self, '_bots_mtime', 0)` (or `_routines_mtime`, `_settings_mtime`), if the file content is semantically unchanged (`valid_bots == self.state.get('assistants')`) OR if JSON parsing raises an exception (swallowed by `except Exception: pass`), `self._bots_mtime` / `self._routines_mtime` / `self._settings_mtime` is **never updated** because `self.save()` is not called.
  - Furthermore, for `bots.json` (`storage.py:333-348`), `item.setdefault('tools', ...)` is completely omitted:
    ```python
    for item in raw[:30]:
        if isinstance(item, dict) and item.get('id') and item.get('name'):
            item.setdefault('title', '')
            ...
            item.setdefault('avatar_version', '')
            valid_bots.append(item)
    ```
- **Concrete Impact**:
  1. Any bot added via `workspace/bots.json` without an explicit `tools` list gets `tools=None` (no tools!) and is never upgraded by `ensure_assistants` because `set(current_tools) >= old_default_six` is `False` for empty tools.
  2. If `bots.json`, `routines.json`, or `settings.json` is touched without state diff or contains malformed JSON from a Bot edit, the error is silently swallowed and the server re-reads and re-parses the file on every request (>1.5s) forever.
- **Recommended Fix**:
  - Update `self._bots_mtime = mtime` (and `_routines_mtime`, `_settings_mtime`) even when no state change occurred or after logging a warning on parse failure; add `item.setdefault('tools', TOOL_NAMES.copy())` and validate tool names in `sync_from_workspace()`.

---

### [BUG-604] HIGH | Tool Permission Upgrade Logic Flaw in `assistants.py:ensure_assistants()`
- **Category**: Tool Routing & Permission Regression
- **File(s)**: `D:\bubuki\proactive-personal-agent\server\assistants.py` (Lines 171–175)
- **Root Cause & Code Evidence**:
  ```python
  old_default_six = {'read_file', 'write_file', 'edit_file', 'run_bash', 'web_fetch', 'web_search'}
  current_tools = bot.get('tools') or []
  if set(current_tools) >= old_default_six and len(current_tools) < len(TOOL_NAMES):
      bot['tools'] = TOOL_NAMES.copy()
      changed = True
  ```
- **Concrete Impact**:
  1. **Cannot disable optional tools**: If a user disables *any* single tool (e.g., `send_email` or `cloud_desktop`) on an assistant while keeping the 6 basic tools (`read_file`, `write_file`, `edit_file`, `run_bash`, `web_fetch`, `web_search`), `set(current_tools) >= old_default_six and len(current_tools) < len(TOOL_NAMES)` evaluates to `True`! Every time `ensure_assistants(store)` runs (on startup or assistant lookup fallback), it forcibly resets `bot['tools'] = TOOL_NAMES.copy()`, silently re-enabling the tools the user disabled.
  2. **Bots without all 6 basic tools miss new tools**: Conversely, if a bot had one of the 6 basic tools unchecked (or had `tools: []` from `bots.json`), `set(current_tools) >= old_default_six` is `False`, so newly added tools in `harness.py` are never granted to it.
- **Recommended Fix**:
  - Track a `tools_schema_version` or `known_tools` list on each bot dictionary so only newly introduced tools (not present in the bot's previous `known_tools` snapshot) are appended, while respecting tools explicitly unchecked by the user; if `'tools' not in bot or not bot['tools']`, initialize to `TOOL_NAMES.copy()`.

---

### [BUG-605] HIGH | Stale Cache & Snapshot Collision/Deletion Bugs in `vault_manager.py`
- **Category**: Persistence / Cache Invalidation / Data Loss
- **File(s)**: `D:\bubuki\proactive-personal-agent\server\vault_manager.py` (Lines 156–166, 173–198, 217–219, 281–285, 365–369)
- **Root Cause & Code Evidence**:
  1. **Broken directory mtime cache in `Vault.files()` (`vault_manager.py:158-160`)**:
     ```python
     dir_mtime = self.root.stat().st_mtime_ns
     if self._files_cache and self._files_cache[0] == dir_mtime:
         return list(self._files_cache[1])
     ```
     `self.root` is `memory_vault/`. Modifying, creating, or deleting files inside subdirectories (`memory_vault/notes/`, `memory_vault/profile/`, `memory_vault/tasks/`, `memory_vault/timeline/`) only updates the **subdirectory's** `st_mtime_ns`, NOT `self.root.stat().st_mtime_ns`! Thus `dir_mtime` never changes when subdirectory files change outside `Vault.write()`.
  2. **Broken snapshot prefix grouping in `Vault.prune_storage()` (`vault_manager.py:217-218`)**:
     ```python
     parts = f.name.split('.')
     prefix = '.'.join(parts[:2]) if len(parts) >= 3 else f.stem
     ```
     `_save_snapshot()` names snapshots `{safe_rel}.{stamp}.md` (e.g., `notes__v1.2_plan.md.20261108-140000.md`). If a markdown filename contains dots (e.g., `notes/v1.2.md` and `notes/v1.3.md`), `parts[:2]` groups both files under prefix `notes__v1.2` / `notes__v1`, causing `prune_storage()` to misclassify or cross-delete snapshots! It should be `'.'.join(parts[:-2])`.
  3. **Second-level snapshot filename collision (`vault_manager.py:187`)**:
     `stamp = datetime.now(TZ).strftime('%Y%m%d-%H%M%S')` — if a file is modified twice within the same second (e.g., two `edit_file` tool calls in the same turn), the second snapshot overwrites the first snapshot of that second, losing the original pre-turn state.
  4. **Multiline log corruption in `logs/notifications_inbox.md` and `timeline/work_log.md` (`vault_manager.py:282-284, 367-369, 394-395`)**:
     ```python
     headers = [ln for ln in n_lines[:10] if not ln.startswith('- ')]
     items = [ln for ln in n_lines if ln.startswith('- ')]
     ```
     Any continuation line of a notification/work-log entry that does not start with `'- '` is silently deleted (or if within the first 10 lines, permanently duplicated into `headers`).
- **Concrete Impact**:
  - `Vault.files()` can serve stale file lists/sizes; rapid edits overwrite backup snapshots; dotted note names have broken snapshot retention; multiline notifications corrupt `notifications_inbox.md`.
- **Recommended Fix**:
  - Compute `dir_mtime` across `self.root` and its immediate subdirectories (or invalidate properly); use `'.'.join(parts[:-2])` in `prune_storage()`; include milliseconds (`%Y%m%d-%H%M%S-%f`) in snapshot filenames; sanitize newlines in `notifications_inbox.md` entries before appending.

---

### [BUG-606] HIGH | Avatar 404 & Path Traversal When Accessing `/api/bots/{bot_id}/identity` or `/avatar` by Bot Name, plus Broken `big_brain` Config Lookup in `/avatar/generate`
- **Category**: Routing / Path Traversal / Config Lookup Bug
- **File(s)**: `D:\bubuki\proactive-personal-agent\server\bot_identity.py` (Lines 135–143, 178–225)
- **Root Cause & Code Evidence**:
  1. In `update_identity()` (`bot_identity.py:192, 201, 206`) and `get_avatar()` (`bot_identity.py:217`), the file path uses the raw URL parameter `bot_id` instead of the resolved canonical `bot['id']`:
     ```python
     bot = get_assistant(store, bot_id)
     ...
     atomic_write(avatar_dir / (bot_id + '-' + version + '.txt'), image)
     ```
     Because `get_assistant(store, bot_id)` matches by either `b['id'] == q` OR `b['name'] == q`, calling `PATCH /api/bots/白珂/identity` writes `avatars/白珂-<version>.txt`, whereas `GET /api/bots/companion/avatar` reads `avatars/companion-<version>.txt` -> **404 Not Found**! Furthermore, `AssistantInput.clean_name` does not forbid `/` or `..` in `name`, allowing path traversal outside `avatar_dir` if `bot_id` contains path separators.
  2. In `generate_avatar()` (`bot_identity.py:135-143`):
     ```python
     config = store.config.big_brain
     if not config.api_key:
         raise HTTPException(409, '先在执行大脑中保存图像服务的 API Key 和 Base URL')
     ```
     It accesses `store.config.big_brain.api_key` and `store.config.big_brain.base_url` directly instead of `store.config.effective_api_key(big=True)` and `store.config.effective_base_url()`, failing with HTTP 409/502 when only top-level `api_key`/`api_base_url` or fallback is configured.
- **Concrete Impact**:
  - Avatar updates via bot name fail to load via bot ID; avatar generation fails when using unified/fallback API config.
- **Recommended Fix**:
  - Always use `canonical_id = bot['id']` (validated against `^[a-zA-Z0-9_-]+$`) for avatar filenames and `bots_changed` events; use `store.config.effective_base_url()` and `store.config.effective_api_key(big=True)` in `generate_avatar()`.

---

### [BUG-607] MEDIUM | Thread-Safety Race on `store.state['pending']` in `escalation.py` and Replay/Memory DoS in `security.py`
- **Category**: Concurrency / Security
- **File(s)**:
  - `D:\bubuki\proactive-personal-agent\server\escalation.py` (Lines 11–25, 60–83)
  - `D:\bubuki\proactive-personal-agent\server\security.py` (Lines 38–50, 58–60)
- **Root Cause & Code Evidence**:
  1. `Escalation.track()`, `ack()`, `snooze()`, and `tick()` mutate `self.store.state['pending']` and `self.store.state['snooze_until']` on the asyncio loop thread **without acquiring `self.store.lock`**, while worker threads call `self.store.save()` -> `json.dumps(self.store.state)` inside `self.store.lock`. Concurrent dict mutation during `json.dumps` raises `RuntimeError: dictionary changed size during iteration`.
  2. `CryptoBox.frame(key, envelope)` (`security.py:59-60`) decrypts WebSocket frames without checking payload size, timestamp, or nonce replay (`self.seen`), allowing captured encrypted WS frames to be replayed within the same WS session.
  3. In `CryptoBox.open()` (`security.py:38-47`), when `len(self._session_keys) > 256` or `len(self.seen) > 2000` and all entries are newer than the TTL (e.g. >2000 requests within 5 minutes), the dictionary comprehension retains >2000 entries and runs an $O(N)$ full dictionary rebuild on every single subsequent request without bound.
- **Concrete Impact**:
  - Potential `RuntimeError` crash during `store.save()` when escalations are tracked/acked concurrently with tool execution; WebSocket frame replay vulnerability; CPU/memory spike under burst traffic.
- **Recommended Fix**:
  - Acquire `self.store.lock` before mutating `self.store.state` in `escalation.py`; enforce size limit, timestamp/sequence check, and bounded LRU eviction (`collections.OrderedDict`) in `CryptoBox.open()` and `CryptoBox.frame()`.

---

## Part 2: Findings in `main.py` (HTTP Routes, WebSocket Endpoints, Cross-Node Sync & Security)

### [BUG-608] CRITICAL | Authentication Bypass on `/workspace-media/{rel_path:path}` Exposes Private Workspace Images
- **Category**: Security / Authentication Bypass
- **File(s)**: `D:\bubuki\proactive-personal-agent\server\main.py` (Lines 1169–1179)
- **Root Cause & Code Evidence**:
  ```python
  @app.get('/workspace-media/{rel_path:path}')
  async def get_workspace_media(rel_path:str):
      clean_rel=rel_path.removeprefix('workspace/').lstrip('/')
      try:
          p=harness.path(clean_rel)
      except Exception:
          raise HTTPException(400,'非法路径')
      if not p.is_file() or p.suffix.lower() not in ('.png','.jpg','.jpeg','.webp','.gif','.bmp'):
          raise HTTPException(404,'图片不存在')
      media={'.png':'image/png','.webp':'image/webp','.gif':'image/gif','.bmp':'image/bmp'}.get(p.suffix.lower(),'image/jpeg')
      return FileResponse(p,media_type=media)
  ```
  Unlike `/api/workspace/files/...`, `/workspace-media/{rel_path:path}` has **no `dependencies=[Depends(auth)]`** and no query token verification.
- **Concrete Impact**:
  - Any unauthenticated party on the network who guesses or enumerates image paths under `workspace/` (e.g., screenshots, uploaded photos in `uploads/`, camera captures) can download them directly without authentication.
- **Recommended Fix**:
  - Require either `Authorization: Bearer <token>` or a short-lived HMAC-signed query token (or token query parameter validated with `hmac.compare_digest`) on `/workspace-media/{rel_path:path}`.

---

### [BUG-609] HIGH | `/api/secure` Encrypted Gateway Crashes with HTTP 400 on Any Non-JSON Response (File Downloads, Binary Endpoints) & Path Normalization Bypass
- **Category**: Gateway Routing / Crash on File Download
- **File(s)**: `D:\bubuki\proactive-personal-agent\server\main.py` (Lines 173–181, 189–205)
- **Root Cause & Code Evidence**:
  ```python
  @app.post('/api/secure')
  async def secure(request:Request):
      try:
          key,data=crypto.open(await request.json())
          if not hmac.compare_digest(str(data.get('token','')),token):
              return crypto.seal(key,{'status':401,'body':{'detail':'设备配对码不正确'}})
          path=data['path']
          if not path.startswith('/api/') or path in ('/api/secure','/api/transport') or '?' in path or '#' in path:
              raise ValueError('非法 API 路径')
          ic=internal_client_holder.get('client')
          if ic is not None:
              response=await ic.request(data['method'],path,params=data.get('query'),json=data.get('body'),headers={'Authorization':'Bearer '+token})
          ...
          return crypto.seal(key,{'status':response.status_code,'body':response.json()})
      except (ValueError,KeyError,TypeError,InvalidTag): raise HTTPException(400,'加密请求无效、过期或重放')
  ```
  1. Line 204 unconditionally calls `response.json()`. When the proxied endpoint returns a binary file (`FileResponse` from `GET /api/workspace/files/{path}`), plain text, or an empty body, `response.json()` raises `json.JSONDecodeError` (a subclass of `ValueError`), which is caught at line 205 and converted into `HTTPException(400, '加密请求无效、过期或重放')`!
  2. Line 196 checks `path.startswith('/api/')` without checking `'..' in path`, allowing `/api/../download/release.json` or other non-`/api/` routes after URL normalization.
  3. In `size_limit` middleware (`main.py:175`), `int(request.headers.get('content-length','0'))>6_000_000` raises `ValueError` (500 error) if `Content-Length` is malformed, bypasses the 6MB limit completely when `Transfer-Encoding: chunked` is used without `Content-Length`, and blocks legitimate chunked file uploads > 6MB if sent in one request.
- **Concrete Impact**:
  - Any request sent through `/api/secure` whose response is not JSON (such as downloading workspace files) fails with HTTP 400 `'加密请求无效、过期或重放'`.
- **Recommended Fix**:
  - In `/api/secure`, inspect `response.headers.get('content-type', '')`; if not JSON, either base64-encode `response.content` or parse JSON with fallback to `response.text`; reject `'..' in path`.

---

### [BUG-610] HIGH | Cross-Device Cache Contamination in `/api/phone/command` (`phone_read_cache` Ignores `source`/`device_id`)
- **Category**: Multi-Node Split-Brain / Cache Bug
- **File(s)**: `D:\bubuki\proactive-personal-agent\server\main.py` (Lines 164, 711–727)
- **Root Cause & Code Evidence**:
  ```python
  phone_read_cache:dict[str,tuple[float,Any]]={}
  ...
  @app.post('/api/phone/command',dependencies=[Depends(auth)])
  async def phone_command(payload:dict[str,Any]):
      op=str(payload.get('op') or 'status').strip()
      force=bool(payload.get('force',False))
      now=time.time()
      ttl=300.0 if op=='apps' else (1.5 if op in ('status','ui_tree') else 0.0)
      if ttl>0 and not force and op in phone_read_cache:
          ts,cached_val=phone_read_cache[op]
          if now-ts<ttl:
              return {'cached':True,**cached_val} if isinstance(cached_val,dict) else cached_val
  ```
  `phone_read_cache` is indexed solely by `op` (`'apps'`, `'status'`, `'ui_tree'`) without including the target phone node (`source` / `device_id`, e.g., `phone` vs `phone2`).
- **Concrete Impact**:
  - When two Android devices (`安卓一` and `安卓二`) are connected, querying `apps` on `安卓二` returns `安卓一`'s installed app list for 5 minutes (300s), and querying `status`/`ui_tree` within 1.5s returns the wrong phone's screen state.
- **Recommended Fix**:
  - Key `phone_read_cache` by `(normalize_node_source(payload.get('source') or payload.get('device_id') or 'phone'), op)` and invalidate per-device on mutating ops.

---

### [BUG-611] HIGH | Multi-PC Command Stealing & Broadcast Collision in `/ws/dsh-bridge` and `/api/dsh/inbox`
- **Category**: Routing Split-Brain / Concurrency Race
- **File(s)**: `D:\bubuki\proactive-personal-agent\server\main.py` (Lines 87, 113–117, 543–558, 1080–1138)
- **Root Cause & Code Evidence**:
  1. `dsh_clients: dict[str, asyncio.Queue] = {}` (`main.py:87`) does not record which `device_id` (`pc` / `pc2`) owns each WebSocket connection. `dispatch_dsh(event)` (`main.py:113-117`) broadcasts every command event to **all** connected `dsh_clients` queues indiscriminately.
  2. In `/api/dsh/inbox` (`main.py:543-558`):
     ```python
     caller_dev=str(payload.get('device_id') or payload.get('peer_id') or request.query_params.get('device_id') or '').strip()
     all_inbox=list(brain.dsh_bridge_info.get('inbox') or [])
     if caller_dev and caller_dev!='pc_dsh':
         inbox=[
             item for item in all_inbox
             if not item.get('device_id') or item.get('device_id') in ('pc_dsh',caller_dev)
         ]
     else:
         inbox=all_inbox
     if payload.get('drain') or request.query_params.get('drain') in ('1','true'):
         if caller_dev and caller_dev!='pc_dsh':
             drained_ids={id(x) for x in inbox}
             brain.dsh_bridge_info['inbox']=[item for item in all_inbox if id(item) not in drained_ids]
         else:
             brain.dsh_bridge_info['inbox']=[]
     ```
     - When `Windows一` (`caller_dev == 'pc_dsh'` or empty) polls `/api/dsh/inbox?drain=1`, it takes the `else` branch, receiving and wiping **all** inbox commands including those targeted at `pc2` (`Windows二`)!
     - When `Windows二` (`caller_dev == 'pc2'`) polls `/api/dsh/inbox?drain=1`, the condition `item.get('device_id') in ('pc_dsh', caller_dev)` matches commands targeted at `'pc_dsh'` (`Windows一`), so `Windows二` also drains and executes `Windows一`'s commands!
- **Concrete Impact**:
  - In a multi-PC setup (`Windows一` + `Windows二`), commands routed to a specific PC are either executed on both PCs simultaneously (via WS broadcast) or stolen by whichever PC polls `/api/dsh/inbox` first.
- **Recommended Fix**:
  - Store `{'queue': queue, 'device_id': peer_dev_id}` in `dsh_clients` and filter `dispatch_dsh(event)` by `event.get('device_id')`; in `dsh_inbox`, only match items whose normalized `device_id` equals normalized `caller_dev` (or has no `device_id` when targeting the default PC).

---

### [BUG-612] HIGH | Non-Canonical `bot_id` Propagation in `/api/chat`, `/api/messages`, `/api/sync`, `/api/bots/{bot_id}`, and Orphaned Routines on `DELETE /api/bots/{bot_id}`
- **Category**: Identifier Normalization / Data Integrity Bug
- **File(s)**: `D:\bubuki\proactive-personal-agent\server\main.py` (Lines 361–362, 776–779, 824–834, 869–917)
- **Root Cause & Code Evidence**:
  1. In `POST /api/chat` (`main.py:901-916`):
     ```python
     bot_id=data.bot_id or store.state['primary_bot_id']
     src=normalize_node_source(data.source)
     try: bot=get_assistant(store,bot_id)
     except ValueError as e: raise HTTPException(404,str(e))
     if data.async_ack:
         return await brain.enqueue_chat(data.text.strip(),bot_id,data.mode,client_msg_id=data.client_msg_id,source=src)
     try:
         res=await brain.chat(data.text.strip(),bot_id,data.mode,client_msg_id=data.client_msg_id,source=src)
     ```
     `get_assistant(store, bot_id)` resolves `bot_id` by ID or display name (e.g. `'白珂'` -> `bot['id'] == 'companion'`), **but `bot['id']` is not assigned back to `bot_id`**! If a client passes a bot name or uppercase ID, messages are saved and streamed under the non-canonical `bot_id`, splitting the bot's chat history!
  2. The same bug exists in `GET /api/messages` (`main.py:776-779`), `GET /api/sync` (`main.py:361-362`), `POST /api/context/compact` (`main.py:824`), and `PUT /api/bots/{bot_id}` (`main.py:874`).
  3. In `DELETE /api/bots/{bot_id}` (`main.py:880-893`), deleting a bot does not remove its avatar file in `avatars/` and does not reassign or delete routines in `store.state['routines']` whose `bot_id` was the deleted bot. When those routines later trigger, `get_assistant()` raises `ValueError('助手不存在')`, causing HTTP 502 / scheduler failure.
- **Concrete Impact**:
  - Passing bot name or non-canonical case splits message history and fails to filter messages; deleting a bot leaves broken routines that fail on execution.
- **Recommended Fix**:
  - Set `bot_id = bot['id']` immediately after `bot = get_assistant(store, bot_id)` in all endpoints; in `delete_bot`, clean up avatar files and reassign routines of the deleted bot to `store.state['primary_bot_id']`.

---

### [BUG-613] MEDIUM | Broken `fingerprint` in `/api/pairing-info` (Key Missing from `CryptoBox.public()`)
- **Category**: Contract Mismatch / Security Metadata Omission
- **File(s)**:
  - `D:\bubuki\proactive-personal-agent\server\main.py` (Lines 616–628)
  - `D:\bubuki\proactive-personal-agent\server\security.py` (Line 27)
- **Root Cause & Code Evidence**:
  In `main.py:616-625`:
  ```python
  pub=crypto.public()
  pair_uri=f"dsclaw://pair?server={_up.quote(base_url,safe='')}&token={_up.quote(token,safe='')}&fp={_up.quote(str(pub.get('fingerprint','')),safe='')}"
  return {
      ...
      'fingerprint':pub.get('fingerprint',''),
      'pairing_uri':pair_uri,
  ```
  In `security.py:27`:
  ```python
  def public(self): return {'public_key':b64(self.public_der), 'algorithm':'RSA-OAEP-SHA256+A256GCM'}
  ```
  `CryptoBox.public()` never computes or returns `'fingerprint'`.
- **Concrete Impact**:
  - `/api/pairing-info` always returns `'fingerprint': ''` and generates `dsclaw://pair?...&fp=` with an empty SPKI fingerprint.
- **Recommended Fix**:
  - Include `'fingerprint': hashlib.sha256(self.public_der).hexdigest()` in `CryptoBox.public()`.

---

### [BUG-614] MEDIUM | Contract & State Discrepancies Between `/api/status`, `/api/sync`, and WebSockets (`/ws/client`, `/ws/dsh-bridge`)
- **Category**: Contract Mismatch / Security & State Sync
- **File(s)**: `D:\bubuki\proactive-personal-agent\server\main.py` (Lines 121, 309–347, 356–403, 511, 558, 597, 643, 1041–1072, 1088–1126)
- **Root Cause & Code Evidence**:
  1. **`unread` count mismatch between `/api/status` and `/api/sync`**:
     - `/api/status` (`main.py:334-336`) computes `'unread': len(src_pending)` filtered by `req_src`.
     - `/api/sync` (`main.py:392`) computes `'unread': len(s['pending'])` unfiltered across all sources.
  2. **Flawed `small_ready` / `big_ready` calculation (`main.py:309-310, 336-337, 356-357, 392`)**:
     `has_key = bool(effective_api_key(False) or effective_api_key(True))` and `has_model = bool(effective_model(False) or effective_model(True))` are combined as `has_key and has_model`, which evaluates to `True` even if `small_brain` only has a key (no model) and `big_brain` only has a model (no key).
  3. **`phone_online` false positive (`main.py:121, 335, 391, ...`)**:
     `len(clients) > 0` counts any `/ws/client` connection (including `source == 'pc'`) as `phone_online = True`.
  4. **WebSocket security & protocol flaws (`main.py:1054-1063, 1114-1116`)**:
     - In `/ws/client` (`main.py:1060`), `if len(last_nonce)>1000: last_nonce={nonce}` wipes all 1,000 previous nonces at once, allowing immediate replay of earlier frames.
     - In `/ws/client` (`main.py:1063`), `elif event.get('type')=='snooze': await escalation.snooze(60)` ignores `event.get('minutes')`.
     - In `/ws/dsh-bridge` (`main.py:1115`), `crypto.frame(key,packet) if (key is not None and isinstance(packet,dict) and 'data' in packet) else packet` allows an attacker to inject unencrypted plaintext packets onto an encrypted bridge session and performs zero nonce replay checking.
  5. **Uncaught `ValueError` (HTTP 500) on non-integer `limit` parameters (`main.py:511, 558, 597, 643`)**:
     `int(payload.get('limit') or ...)` is called without `try/except ValueError`, crashing with HTTP 500 on non-numeric input.
- **Concrete Impact**:
  - Unread badge flickers between `/api/status` and `/api/sync`; `phone_online` reports true when only a PC is connected to `/ws/client`; WS snooze ignores custom duration; `/ws/dsh-bridge` accepts plaintext downgrade frames.
- **Recommended Fix**:
  - Unify status/sync payload computation into a shared helper; check `any(c.get('source') in ('phone', 'phone2') for c in clients.values())` for `phone_online`; enforce encryption once `key is not None` on `/ws/dsh-bridge`; honor `event.get('minutes', 60)` on WS snooze; wrap `int(...)` casts in a safe helper.

---

### [BUG-615] MEDIUM | Live Chromium Cache Deletion & Upload Reference Breakage in `storage.py:run_storage_maintenance()`
- **Category**: Storage GC / Resource Corruption
- **File(s)**: `D:\bubuki\proactive-personal-agent\server\storage.py` (Lines 526–656)
- **Root Cause & Code Evidence**:
  1. **Deleting older duplicate uploads breaks existing file references (`storage.py:577-600`)**:
     ```python
     up_files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
     ...
     if h in seen_hashes:
         uf.unlink(missing_ok=True)
     ```
     Because `up_files` is sorted newest-first, when a user uploads the same image/file twice at different times, `run_storage_maintenance` keeps the **newest** filename and unlinks the **older** filename, breaking all historical chat messages or notes that point to the original upload path.
  2. **Deleting live Chromium caches while the desktop container is running (`storage.py:622-654`)**:
     `_periodic_storage_gc()` runs 5 seconds after boot and every hour thereafter, executing `shutil.rmtree` on `Default/Cache`, `Default/Code Cache`, `Default/GPUCache`, and `Default/Service Worker/CacheStorage` without checking whether Chromium is currently running in the cloud desktop container.
  3. **0-byte SQLite file race (`storage.py:527-534`)**:
     Unlinks `0`-byte `.db` files without checking if a connection is currently initializing them.
- **Concrete Impact**:
  - Historical uploaded attachments return 404 after re-uploading an identical file; live Chromium sessions in Cloud Desktop can experience cache index errors when GC runs mid-task.
- **Recommended Fix**:
  - Do not delete duplicate filenames in `uploads/` without updating references (or deduplicate at upload time by returning the existing file path); skip Chromium profile cache deletion when Chromium processes are active.

---

## Summary Table of Audited Bugs (Auditor-6)

| Bug ID | Severity | File(s) | Summary |
| :--- | :--- | :--- | :--- |
| **BUG-601** | `CRITICAL` | `storage.py`, `sandbox.py` | Hardcoded fallback API key (`sk-daye-aggregate-2026`) & IP in `Config`, plus plaintext API keys written into `workspace/settings.json` |
| **BUG-608** | `CRITICAL` | `main.py` | Unauthenticated `/workspace-media/{rel_path:path}` exposes all private images in `workspace/` without Bearer token |
| **BUG-602** | `HIGH` | `storage.py` | `_sync_to_workspace` + `sync_from_workspace` + `update_config` overwrites `big_brain` config with `small_brain` values and prevents clearing `api_key` |
| **BUG-603** | `HIGH` | `storage.py` | `sync_from_workspace` omits `tools` default on `bots.json`, swallows parse errors via `except Exception: pass`, and fails to update `_bots_mtime` / `_routines_mtime` / `_settings_mtime`, causing an infinite disk re-read loop |
| **BUG-604** | `HIGH` | `assistants.py` | `ensure_assistants` tool migration (`set(current_tools) >= old_default_six`) forcibly resets user-customized tool lists back to all tools while failing to upgrade bots that lack any of the 6 basic tools |
| **BUG-605** | `HIGH` | `vault_manager.py` | `Vault.files()` caches by top-level `self.root` mtime (misses subdirectory edits), `prune_storage()` breaks on dotted filenames (`parts[:2]`), `.history` snapshots collide within the same second, and multiline entries corrupt `notifications_inbox.md` |
| **BUG-606** | `HIGH` | `bot_identity.py` | `/api/bots/{bot_id}/identity` and `/avatar` use raw `bot_id` parameter (can be bot name or path traversal) instead of canonical `bot['id']`; `/avatar/generate` ignores `effective_api_key`/`effective_base_url` |
| **BUG-609** | `HIGH` | `main.py` | `/api/secure` crashes with HTTP 400 on any non-JSON response (`FileResponse` downloads) and allows `..` in `data['path']`; `size_limit` middleware bypassed by chunked transfer encoding |
| **BUG-610** | `HIGH` | `main.py` | `/api/phone/command` `phone_read_cache` keys only by `op` without `source`/`device_id`, causing cross-device cache contamination between `安卓一` (`phone`) and `安卓二` (`phone2`) |
| **BUG-611** | `HIGH` | `main.py` | Multi-PC split-brain: `/ws/dsh-bridge` broadcasts commands to all PCs regardless of `device_id`, and `/api/dsh/inbox?drain=1` lets `Windows一` and `Windows二` steal/wipe each other's queued commands |
| **BUG-612** | `HIGH` | `main.py` | `/api/chat`, `/api/messages`, `/api/sync`, `/api/context/compact`, and `PUT /api/bots/{bot_id}` do not normalize `bot_id` to canonical `bot['id']`; `DELETE /api/bots/{bot_id}` leaves orphaned routines and avatar files |
| **BUG-607** | `MEDIUM` | `escalation.py`, `security.py` | Unlocked mutations to `store.state['pending']` in `escalation.py` race with `store.save()`; `CryptoBox.frame()` lacks replay/size checks and `CryptoBox.open()` has unbounded dict rebuild under burst load |
| **BUG-613** | `MEDIUM` | `main.py`, `security.py` | `/api/pairing-info` reads `pub.get('fingerprint', '')`, which is missing from `CryptoBox.public()`, resulting in an always-empty SPKI fingerprint |
| **BUG-614** | `MEDIUM` | `main.py` | `/api/status` vs `/api/sync` `unread` & `small_ready`/`big_ready` discrepancies; `phone_online` counts PC `/ws/client` sockets; `/ws/client` wipes nonce set at 1000 and ignores custom snooze minutes; `/ws/dsh-bridge` allows plaintext downgrade; unvalidated `int(limit)` causes HTTP 500 |
| **BUG-615** | `MEDIUM` | `storage.py` | `run_storage_maintenance` deletes older duplicate uploads (breaking existing message paths) and deletes live Chromium cache directories while Cloud Desktop is running |


---
# 【分卷】Auditor-7 (安卓 NativePulseActivity / SecureTransport / CloudFiles / NotificationListener) (`audit_7_activity_transport.md`)

# Audit Report 7: Android `NativePulseActivity.kt`, Transport, Storage & Receivers

**Auditor**: Auditor-7 (`4d5ae7ce-bde3-4b30-8bfa-cc5f3c49fd7c`)
**Scope**:
1. `D:\bubuki\proactive-personal-agent\android\app\src\main\java\dev\bubuki\proactive\NativePulseActivity.kt`
2. `D:\bubuki\proactive-personal-agent\android\app\src\main\java\dev\bubuki\proactive\SecureTransport.java`
3. `D:\bubuki\proactive-personal-agent\android\app\src\main\java\dev\bubuki\proactive\CloudFiles.java`
4. `D:\bubuki\proactive-personal-agent\android\app\src\main\java\dev\bubuki\proactive\AgentNotificationListener.java`
5. `D:\bubuki\proactive-personal-agent\android\app\src\main\java\dev\bubuki\proactive\AgentActionReceiver.java`
6. `D:\bubuki\proactive-personal-agent\android\app\src\main\java\dev\bubuki\proactive\BootReceiver.java`
7. `D:\bubuki\proactive-personal-agent\android\app\src\main\java\dev\bubuki\proactive\SecurePrefs.java`
8. `D:\bubuki\proactive-personal-agent\android\app\src\main\java\dev\bubuki\proactive\UiPreferences.kt`

---

## Part 1: `SecureTransport.java`, `CloudFiles.java` & `NativePulseActivity.kt` (Lines 1–800)

### [BUG-701] `HIGH` — `SecureTransport.session()` Cache TTL Bug Causes Disk Read + Keystore AES-GCM Decrypt on Every Request After 15 Seconds
- **Category**: Performance / Main-Thread & IO Contention
- **File**: `D:\bubuki\proactive-personal-agent\android\app\src\main\java\dev\bubuki\proactive\SecureTransport.java` (Lines 68–100)
- **Root Cause & Evidence**:
  ```java
  if(forceNewKey || base==null || token==null || publicKey==null || (now - cachedAtMs) > 15_000L){
      JSONObject settings=SecurePrefs.read(context);
      ...
      cachedBase=base;
      cachedToken=token;
      cachedPubKeyB64=publicKey;
  }
  byte[] aesKey=cachedAesKey;
  String wrappedKey=cachedWrappedKeyB64;
  if(forceNewKey || aesKey==null || wrappedKey==null || (now - cachedAtMs) > 1200_000L){
      ...
      cachedAtMs=now;
  }
  ```
  `cachedAtMs` is only updated inside the 20-minute (`1200_000L`) RSA key-wrapping block, and is **never** updated inside the 15-second (`15_000L`) settings check block.
- **Concrete Impact**:
  Once 15 seconds have elapsed after initial key generation, `(now - cachedAtMs) > 15_000L` evaluates to `true` on **every single HTTP request** for the next 19 minutes and 45 seconds. Because `SecurePrefs.read(context)` is `synchronized` and performs file reads + Android Keystore AES-GCM decryption, high-frequency polling (e.g. Cloud Desktop frame streaming, status polling, avatar loading) repeatedly blocks on `SecurePrefs.read(context)` on every request.
- **Recommended Fix**:
  Track `cachedSettingsAtMs` separately from `cachedKeyAtMs`, and update `cachedSettingsAtMs = now` when settings are reloaded every 15 seconds.

---

### [BUG-702] `HIGH` — Data Race Between `SecureTransport.session()` and `invalidateCache()` Can Pair Mismatched `aesKey` and `wrappedKey`
- **Category**: Concurrency / Race Condition
- **File**: `D:\bubuki\proactive-personal-agent\android\app\src\main\java\dev\bubuki\proactive\SecureTransport.java` (Lines 28–39, 63–109)
- **Root Cause & Evidence**:
  `invalidateCache()` is `static synchronized`, whereas `session(JSONObject inner, boolean forceNewKey)` is an unsynchronized instance method reading and writing static volatile fields (`cachedAesKey`, `cachedWrappedKeyB64`, `cachedBase`, `cachedPubKeyB64`, `cachedAtMs`).
  - Thread A reads `byte[] aesKey = cachedAesKey;` (non-null), then Thread B calls `invalidateCache()` or rotates `cachedAesKey` & `cachedWrappedKeyB64`.
  - Or two concurrent requests both enter the key generation block (`aesKey == null` on startup or after `invalidateCache()`), generating two different AES keys and overwriting `cachedAesKey` and `cachedWrappedKeyB64` non-atomically.
- **Concrete Impact**:
  A request can encrypt its payload with `aesKey_1` while attaching `wrappedKey_2` (or encounter `null`), causing the server to fail decryption with HTTP 400 or the client to fail response decryption with `AEADBadTagException`.
- **Recommended Fix**:
  Synchronize the cache inspection/update block on `SecureTransport.class` or store an immutable `CachedSessionState(base, token, pubKey, aesKey, wrappedKey, settingsAtMs, keyAtMs)` in a single `AtomicReference`.

---

### [BUG-703] `MEDIUM` — `CloudFiles.tryCompressImage()` Strips EXIF Orientation & Mismatches File Extension When Converting Large PNG/WebP to JPEG
- **Category**: Media Processing / Data Integrity
- **File**: `D:\bubuki\proactive-personal-agent\android\app\src\main\java\dev\bubuki\proactive\CloudFiles.java` (Lines 16–65, 89–106)
- **Root Cause & Evidence**:
  ```java
  decoded = BitmapFactory.decodeStream(in, null, opts);
  ...
  Bitmap.CompressFormat fmt = lower.endsWith(".png") && longest <= 1080
      ? Bitmap.CompressFormat.PNG
      : Bitmap.CompressFormat.JPEG;
  decoded.compress(fmt, 82, out);
  ```
  1. `BitmapFactory.decodeStream` ignores EXIF orientation tags (`TAG_ORIENTATION`), and `decoded.compress()` writes raw pixels without EXIF metadata.
  2. When a `.png` (with `longest > 1080`) or `.webp` image is compressed, it is encoded as `JPEG`, but `name` retains its original `.png` / `.webp` extension when uploaded to `/api/files/uploads`.
- **Concrete Impact**:
  Portrait photos taken by the camera are uploaded rotated 90°/270°, and `.webp` or large `.png` images are saved on the server with a `.png`/`.webp` filename containing JPEG magic bytes.
- **Recommended Fix**:
  Read EXIF orientation via `ExifInterface` and rotate the bitmap before compressing; if re-encoding as JPEG, normalize the uploaded filename extension to `.jpg`.

---

### [BUG-704] `LOW` — `CloudFiles.download()` Fails to Extract Basename for Windows Backslash Paths (`source == "computer"`)
- **Category**: Cross-Platform Path Handling
- **File**: `D:\bubuki\proactive-personal-agent\android\app\src\main\java\dev\bubuki\proactive\CloudFiles.java` (Line 123)
- **Root Cause & Evidence**:
  ```java
  String name=path.substring(path.lastIndexOf('/')+1).replaceAll("[\\\\/:*?\"<>|\\p{Cntrl}]","_");
  ```
  Only `/` is checked via `lastIndexOf('/')`, ignoring `\`.
- **Concrete Impact**:
  When downloading a file from a Windows computer node (e.g., `C:\Users\admin\Desktop\report.pdf`), `lastIndexOf('/')` returns `-1`, producing the mangled filename `C__Users_admin_Desktop_report.pdf`.
- **Recommended Fix**:
  Use `Math.max(path.lastIndexOf('/'), path.lastIndexOf('\\')) + 1` before sanitizing.

---

### [BUG-705] `MEDIUM` — `NativePulseModel.mergeRemoteMessages()` Drops Newly Arrived WebSocket Messages on `fullReplace = true` and Leaves Incremental Merges Unsorted
- **Category**: State Synchronization / Race Condition
- **File**: `D:\bubuki\proactive-personal-agent\android\app\src\main\java\dev\bubuki\proactive\NativePulseActivity.kt` (Lines 654–675)
- **Root Cause & Evidence**:
  ```kotlin
  private fun mergeRemoteMessages(incoming: List<PulseMessage>, fullReplace: Boolean = false) {
      if (incoming.isEmpty() && !fullReplace) return
      val oldIds = remoteMessages.asSequence().map { it.id }.toHashSet()
      val rawMerged = if (fullReplace) {
          incoming
      } else {
          ...
          incoming.forEach { byId[it.id] = it }
          byId.values.toList()
      }
  ```
  1. Despite the docstring claiming newly arrived messages are never lost when an older high-latency HTTP response lands out of order, `refresh()` calls `mergeRemoteMessages(msgs, fullReplace = true)` which blindly replaces `remoteMessages` with `incoming`. If a WebSocket `message` push arrived while the `/api/messages` HTTP request was in flight, `fullReplace = true` wipes out the WebSocket message until the next refresh.
  2. When `fullReplace = false`, `byId.values.toList()` preserves `LinkedHashMap` insertion order without sorting by `created`, so out-of-order message arrivals stay permanently out of chronological order until a full refresh.
- **Concrete Impact**:
  Messages pushed over WebSocket during an in-flight `refresh()` flicker out of existence when `/api/messages` returns; out-of-order incremental messages render in wrong chronological order.
- **Recommended Fix**:
  Even on `fullReplace = true`, retain any message in `remoteMessages` whose `created` timestamp is newer than the maximum `created` timestamp in `incoming` (unless its ID was explicitly truncated in `lastMessagesEpoch`), and sort `byId.values` by `created`.

---

### [BUG-706] `LOW` — `NativePulseModel.cacheRoster()` Omits `is_sub_turn` and `metrics` When Persisting `nativeHomeCache`
- **Category**: Local Cache / Schema Omission
- **File**: `D:\bubuki\proactive-personal-agent\android\app\src\main\java\dev\bubuki\proactive\NativePulseActivity.kt` (Lines 525–545)
- **Root Cause & Evidence**:
  `cacheRoster` serializes recent messages to `nativeHomeCache`, but omits `is_sub_turn`, `api_calls`, and the `metrics` object (`ttft_ms`, `tps`, `prompt_tokens`, `cached_tokens`, `cache_hit_pct`, `completion_tokens`, `total_ms`) that `PulseMessage.from()` reads on startup.
- **Concrete Impact**:
  On cold start before network sync completes, cached sub-turn messages render as main turns (`isSubTurn = false`) and all message token/latency metrics are zeroed out.
- **Recommended Fix**:
  Include `is_sub_turn`, `api_calls`, and `metrics` in the `JSONObject` serialized in `cacheRoster()`.

---

## Part 2: `AgentNotificationListener.java`, `AgentActionReceiver.java` & `NativePulseActivity.kt` (Lines 801–1440)

### [BUG-707] `HIGH` — `AgentNotificationListener.onNotificationRemoved()` Uses `sbn.getKey()` Instead of `dedupKey`, Never Marking Removed Notifications Inactive
- **Category**: Key Contract Mismatch / Stale State
- **File**: `D:\bubuki\proactive-personal-agent\android\app\src\main\java\dev\bubuki\proactive\AgentNotificationListener.java` (Lines 75–87 vs Lines 117–144)
- **Root Cause & Evidence**:
  In `recordPassive()` (Lines 117, 141–144):
  ```java
  String dedupKey = pkg + "|" + title + "|" + text;
  ...
  synchronized (recentBuffer) {
      recentBuffer.remove(dedupKey);
      recentBuffer.put(dedupKey, item);
  }
  ```
  In `onNotificationRemoved()` (Lines 78–85):
  ```java
  String key = sbn.getKey() != null ? sbn.getKey() : (sbn.getPackageName() + ":" + sbn.getId());
  synchronized (recentBuffer) {
      JSONObject existing = recentBuffer.get(key);
      if (existing != null) {
          existing.put("active", false);
      }
  }
  ```
  `recentBuffer` is keyed by `pkg + "|" + title + "|" + text`, whereas `onNotificationRemoved()` queries `recentBuffer` using Android's system notification key `sbn.getKey()`.
- **Concrete Impact**:
  `recentBuffer.get(key)` in `onNotificationRemoved()` **always returns `null`**. Dismissed or cancelled notifications remain `"active": true` forever in `getRecentNotifications()`, misleading the AI when inspecting `phone_workspace(op="notifications")`.
- **Recommended Fix**:
  Store `sbn_key` inside each notification `JSONObject` and scan `recentBuffer.values()` in `onNotificationRemoved()` to match `sbn.getKey()` (or compute the same `dedupKey` from `sbn`).

---

### [BUG-708] `HIGH` — `NativePulseModel.onRealtimeEvent()` Missing `"stopped"` Handler Despite Filtering for `"stopped"` Events
- **Category**: Missing Event Handler / Stuck Streaming UI
- **File**: `D:\bubuki\proactive-personal-agent\android\app\src\main\java\dev\bubuki\proactive\NativePulseActivity.kt` (Lines 783–994)
- **Root Cause & Evidence**:
  Line 783 explicitly checks:
  ```kotlin
  if (evSource.isNotEmpty() && evSource != mySource && type in listOf("message", "stream", "execution", "stopped")) {
      return@launch
  }
  ```
  However, the `when (type)` block (Lines 786–993) only handles `"messages_truncated"`, `"cloud_desktop_frame"`, `"stream"`, `"execution"`, and `"message"`. There is no `"stopped"` branch.
- **Concrete Impact**:
  When a turn is stopped from another client/tab or the backend emits a `"stopped"` WebSocket event without an assistant message, `workingBot`, `streamBotId`, `sendGuardBotId`, and `streamPacerJob` remain stuck in the streaming state until the 45-second fallback poll.
- **Recommended Fix**:
  Add a `"stopped"` branch in `onRealtimeEvent` that marks `turn_id` completed, cancels `streamPacerJob`, and resets `workingBot`, `streamBotId`, `sendGuardBotId`, and stream UI fields.

---

### [BUG-709] `HIGH` — Background Bot WebSocket `stream` / `execution` Events Hijack Active Chat State When Current Bot Is Idle
- **Category**: Multi-Bot State Bleed / Race Condition
- **File**: `D:\bubuki\proactive-personal-agent\android\app\src\main\java\dev\bubuki\proactive\NativePulseActivity.kt` (Lines 840–889, 909–925)
- **Root Cause & Evidence**:
  ```kotlin
  // If user is currently viewing a specific Bot's chat, ignore unrelated background Bot stream events
  if (ui.chatOpen && sBot != ui.botId && ui.streamBotId == ui.botId) return@launch
  ```
  and in `"execution"` (Line 909):
  ```kotlin
  if (ui.chatOpen && working != null && working != ui.botId && ui.workingBot == ui.botId) return@launch
  ```
  When the user has Bot A's chat open (`ui.chatOpen == true`, `ui.botId == "companion"`) and Bot A is **idle** (`ui.streamBotId == null`, `ui.workingBot == null`), the guard `ui.streamBotId == ui.botId` is `false`!
  As a result, when a background Bot B (e.g. a scheduled routine or delegated bot `"research"`) emits a `"stream"` or `"execution"` event:
  1. It passes the guard and overwrites global scalar state (`currentTurnId`, `targetStreamFullText`, `sendGuardBotId = "research"`, `ui.streamBotId = "research"`, `ui.streamText`, `ui.streamTrace`, `ui.streamSubAgents`, `ui.streamTodos`).
  2. Subsequent events from Bot B continue to pass the guard because `ui.streamBotId` is now `"research"` (`!= ui.botId`).
  3. Meanwhile, `refresh()` / `refreshStreamOnly()` queries `streamsByBot.optJSONObject(ui.botId)` (which is `null` for idle Bot A), causing `streamBotId` and `streamTrace` to thrash between Bot B's stream and `null`!
- **Concrete Impact**:
  While reading an idle bot's chat, any background bot or routine execution corrupts `currentTurnId`, `targetStreamFullText`, `streamTrace`, and `workingBot`, causing status indicator flickering and cross-bot stream state contamination.
- **Recommended Fix**:
  When `ui.chatOpen && sBot != ui.botId`, do not overwrite the active chat's `streamText`, `streamTrace`, `targetStreamFullText`, or `currentTurnId` (or maintain per-bot stream state maps).

---

### [BUG-710] `MEDIUM` — `NativePulseModel.refresh()` Never Updates or Clears `streamSubAgents` and `streamTodos`
- **Category**: State Synchronization Omission
- **File**: `D:\bubuki\proactive-personal-agent\android\app\src\main\java\dev\bubuki\proactive\NativePulseActivity.kt` (Lines 1093–1119)
- **Root Cause & Evidence**:
  Unlike `refreshStreamOnly()` (Lines 1199–1200, 1223–1224), `refresh()` constructs `ui = ui.copy(...)` without setting `streamSubAgents` or `streamTodos`.
- **Concrete Impact**:
  If a streaming turn finishes or resets between `refresh()` calls, `ui.streamSubAgents` and `ui.streamTodos` are not cleared by `refresh()`, leaving stale sub-agent and todo cards visible.
- **Recommended Fix**:
  Compute and pass `streamSubAgents` and `streamTodos` in `refresh()` identically to `refreshStreamOnly()`.

---

## Part 3: `NativePulseActivity.kt` (Lines 1420–2200)

### [BUG-711] `HIGH` — `fetchModels()` and `saveSettings()` Do Not Call `SecureTransport.invalidateCache()`, Sending Requests to Stale `serverUrl`
- **Category**: Transport Cache Invalidation / Routing Defect
- **File**: `D:\bubuki\proactive-personal-agent\android\app\src\main\java\dev\bubuki\proactive\NativePulseActivity.kt` (Lines 1502–1509, 1536–1574)
- **Root Cause & Evidence**:
  In `fetchModels()` (Lines 1502–1509):
  ```kotlin
  withContext(Dispatchers.IO) {
      val s = SecurePrefs.read(activity)
      s.put("serverUrl", ui.serverUrl.trim().ifBlank { SecureTransport.DEFAULT_SERVER })
      SecurePrefs.save(activity, s)
  }
  ...
  val res = request("POST", "/api/models", payload)
  ```
  And in `saveSettings()` (Lines 1536–1574):
  ```kotlin
  withContext(Dispatchers.IO) {
      val s = SecurePrefs.read(activity)
      s.put("serverUrl", cleanServer)
      ...
      SecurePrefs.save(activity, s)
      prefs = s
  }
  ...
  request("POST", "/api/config", updatePayload)
  ```
  Neither method calls `SecureTransport.invalidateCache()` (which `toggleNodeSource()` at Line 2051 does call). Because `SecureTransport` caches `cachedBase` (`serverUrl`) in static memory for at least 15 seconds, the immediate `request("POST", "/api/models", ...)` or `request("POST", "/api/config", ...)` is sent to the **old** `cachedBase` URL instead of the newly saved `serverUrl`.
- **Concrete Impact**:
  When a user edits the Server URL in Settings and clicks "获取模型" or "保存设置" within 15 seconds of any background poll, the request goes to the old server URL and fails or updates the wrong server.
- **Recommended Fix**:
  Call `SecureTransport.invalidateCache()` immediately after `SecurePrefs.save(activity, s)` in `fetchModels()`, `saveSettings()`, and any onboarding connection test.

---

### [BUG-712] `MEDIUM` — `open(id)` Fails to Clear `streamChoices`, `streamSubAgents`, `streamTodos`, `streamSpeaker*`, and `editingMessage` When Switching Bots
- **Category**: Multi-Bot State Bleed / UI Defect
- **File**: `D:\bubuki\proactive-personal-agent\android\app\src\main\java\dev\bubuki\proactive\NativePulseActivity.kt` (Lines 1970–1991)
- **Root Cause & Evidence**:
  ```kotlin
  ui = ui.copy(
      botId = id, chatOpen = true, activePanel = null, mode = draftMode(id), reply = null,
      draft = draft(id), attachments = attachments(id), menu = null,
      streamBotId = if (switchingBot && ui.streamBotId != id) null else ui.streamBotId,
      workingBot = if (switchingBot && ui.workingBot != id) null else ui.workingBot,
      streamText = if (switchingBot && ui.streamBotId != id) "" else ui.streamText,
      streamThought = if (switchingBot && ui.streamBotId != id) "" else ui.streamThought,
      streamTool = if (switchingBot && ui.streamBotId != id) "" else ui.streamTool,
      streamTrace = if (switchingBot && ui.streamBotId != id) emptyList() else ui.streamTrace
  )
  ```
  When `switchingBot && ui.streamBotId != id`, `open(id)` clears `streamText`, `streamThought`, `streamTool`, and `streamTrace`, but **forgets** to clear `streamChoices`, `streamSubAgents`, `streamTodos`, `streamSpeakerId`, `streamSpeakerName`, `streamStartedAt`, and `editingMessage`.
- **Concrete Impact**:
  Switching from a streaming Bot A to an idle Bot B briefly displays Bot A's sub-agent cards, todo list, quick choices, speaker badge, and active message-edit banner inside Bot B's chat until `refreshStreamOnly()` completes.
- **Recommended Fix**:
  Reset `streamChoices = emptyList()`, `streamSubAgents = emptyList()`, `streamTodos = emptyList()`, `streamSpeakerId = null`, `streamSpeakerName = ""`, `streamStartedAt = 0L`, and `editingMessage = null` when `switchingBot` is true.

---

### [BUG-713] `LOW` — `openSettings()` Calls `SecurePrefs.read(activity)` Synchronously on the Main Thread
- **Category**: Main Thread Disk & Keystore I/O / ANR Risk
- **File**: `D:\bubuki\proactive-personal-agent\android\app\src\main\java\dev\bubuki\proactive\NativePulseActivity.kt` (Line 1270)
- **Root Cause & Evidence**:
  ```kotlin
  fun openSettings() {
      dismissMenu()
      val saved = runCatching { SecurePrefs.read(activity) }.getOrElse { JSONObject() }
  ```
  `SecurePrefs.read(activity)` is `static synchronized` on `SecurePrefs.class` and performs Android KeyStore AES-GCM decryption. If `draftWriter` or a background poll is holding the `SecurePrefs.class` monitor on an I/O thread, calling `openSettings()` blocks the UI thread.
- **Concrete Impact**:
  Opening the Settings panel can stutter or freeze the UI thread when concurrent preference/cache writes are running.
- **Recommended Fix**:
  Use the in-memory `prefs` snapshot for initial UI population and read `SecurePrefs.read(activity)` inside `withContext(Dispatchers.IO)` in `perform { ... }`.

---

## Part 4: `NativePulseActivity.kt` (Lines 2201–2891) & Lifecycle / Virtual Display / Streaming Races

### [BUG-714] `CRITICAL` — Race Condition in `send()`: Late HTTP Response from `POST /api/chat` Resurrects Stuck Streaming Bubble After WebSocket Already Completed the Turn
- **Category**: Concurrency / Async Stream-vs-HTTP Race Condition
- **File**: `D:\bubuki\proactive-personal-agent\android\app\src\main\java\dev\bubuki\proactive\NativePulseActivity.kt` (Lines 2360–2397)
- **Root Cause & Evidence**:
  In `send()`:
  ```kotlin
  val res = request("POST", "/api/chat", JSONObject().put("text", text).put("bot_id", id).put("mode", "auto").put("async_ack", true).put("client_msg_id", pending.id))
  ...
  val turnId = res.optString("turn_id", "")
  val isQueued = res.optBoolean("queued", false)
  if (turnId.isNotBlank()) {
      if (isQueued) {
          currentTurnId = turnId
          sendGuardBotId = id
          sendGuardUntilUptime = SystemClock.uptimeMillis() + 10_000L
      }
      ...
  }
  ...
  if (isQueued) {
      remoteWorkingBot = id
      ui = ui.copy(
          messages = combinedMessages(),
          workingBot = id,
          streamBotId = ui.streamBotId ?: id
      )
  }
  ```
  Because `async_ack = true` queues the turn on the server and immediately starts the agent loop, on fast turns (or when the HTTP response experiences latency jitter), the WebSocket `"message"` event for the completed turn can arrive **before** the `POST /api/chat` HTTP response returns to `send()`.
  When `onRealtimeEvent("message")` runs first, it records `markTurnCompleted(turnId)` and clears `workingBot = null`, `streamBotId = null`, `sendGuardBotId = null`.
  Moments later, `POST /api/chat` returns `isQueued = true` with the same `turnId`. Because `send()` **never checks `turnId in completedTurnIds`**, it overwrites `remoteWorkingBot = id`, `workingBot = id`, `streamBotId = id`, and sets `sendGuardUntilUptime = now + 10_000L`.
- **Concrete Impact**:
  After the assistant reply has already appeared in the chat, a phantom `"正在思考…"` streaming bubble pops back up underneath the finished message and stays stuck for at least 10–45 seconds.
- **Recommended Fix**:
  In `send()`, check `val alreadyDone = turnId.isNotBlank() && turnId in completedTurnIds`. If `alreadyDone` is true, treat the turn as completed (`remoteWorkingBot = null`, `sendGuardBotId = null`, do not set `workingBot` or `streamBotId`).

---

### [BUG-715] `HIGH` — `stop()` Fails to Stop Local `RootPhoneHarness` Tasks, Blindly Hides Floating Virtual Display When `phone.displayCreated` Is True, and Never Calls `/api/computer/stop`
- **Category**: Virtual Display & Stop State Machine Defect
- **File**: `D:\bubuki\proactive-personal-agent\android\app\src\main\java\dev\bubuki\proactive\NativePulseActivity.kt` (Lines 2534–2597, 2881–2886)
- **Root Cause & Evidence**:
  ```kotlin
  fun stop() {
      val phone = RootPhoneHarness.changes.value
      val targetBot = ui.botId
      val hasPhoneToolInTurn = phone.active || phone.displayCreated ||
          ui.streamTool == "phone_workspace" ||
          ui.streamTrace.any { it.optString("tool") == "phone_workspace" }
      if (hasPhoneToolInTurn) {
          FloatingVirtualDisplay.hide()
          AgentForegroundService.abortPhoneCommands(activity)
      }
      if (phone.active && phone.botId == targetBot) {
          if (ui.streamBotId != targetBot && remoteWorkingBot != targetBot) {
              return
          }
      }
      ...
      if (ui.computerBot == targetBot) {
          request("POST", "/api/computer/stop")
      } else {
          request("POST", "/api/stop", JSONObject().put("bot_id", targetBot))
      }
  ```
  Four distinct bugs exist in `stop()` / `stopBotById()`:
  1. **Local `RootPhoneHarness` tasks are never stopped**: When `phone.active` is true (started via `sendPhone()`), `stop()` calls `FloatingVirtualDisplay.hide()` and `AgentForegroundService.abortPhoneCommands(activity)`, but **never calls `RootPhoneHarness.stop(activity)`**, and then returns early at Line 2546 without clearing `workingBot`!
  2. **`phone.displayCreated` causes unrelated chat stops to kill the Floating Virtual Display**: Once a virtual display has been created (`phone.displayCreated == true`), `hasPhoneToolInTurn` is permanently `true` for the rest of the app process lifetime. Pressing "Stop" on **any** normal text conversation forcibly hides the user's `FloatingVirtualDisplay`.
  3. **`ui.computerBot` is dead code (`null`)**: `ui.computerBot` is initialized to `null` (Line 237) and reset to `null` in `refresh()` (Line 1097), and is never assigned a non-null value anywhere. Thus `if (ui.computerBot == targetBot)` is always `false` and `/api/computer/stop` is never called.
  4. **`stopBotById(targetBotId)` cross-contaminates bot state**: `stopBotById` mutates `ui = ui.copy(botId = targetBotId)`, calls `stop()`, and restores `botId = prevBot`. If `prevBot` was actively streaming, `stop()` freezes `prevBot`'s `ui.streamText` into a `"stopped-..."` message attributed to `targetBotId` and wipes `prevBot`'s streaming UI!
- **Concrete Impact**:
  - Stopping a local phone task hides the window while leaving the background task running.
  - Stopping any regular text generation unexpectedly closes the user's floating virtual display window if `displayCreated` is true.
  - Stopping a specific bot by ID from a multi-bot UI corrupts the active bot's stream.
- **Recommended Fix**:
  - Call `RootPhoneHarness.stop(activity)` when `phone.active` is true.
  - Remove `phone.displayCreated` from `hasPhoneToolInTurn` (only check if the active turn is actually using `phone_workspace` or `phone.active`).
  - Check `ui.cloudDesktopLiveBotId == targetBot` (or call both `/api/stop` and `/api/computer/stop` when Cloud Desktop was used in the turn).
  - Parameterize `stop(targetBot: String = ui.botId)` instead of temporarily mutating `ui.botId`, and only freeze `ui.streamText` if `ui.streamBotId == targetBot`.

---

### [BUG-716] `MEDIUM` — `editMessageAndResend()` and `rerollMessage()` Do Not Reset `currentTurnId`, `targetStreamFullText`, `streamSubAgents`, or `streamTodos`
- **Category**: State Machine Reset Omission
- **File**: `D:\bubuki\proactive-personal-agent\android\app\src\main\java\dev\bubuki\proactive\NativePulseActivity.kt` (Lines 2609–2741)
- **Root Cause & Evidence**:
  Unlike `send()` (which sets `currentTurnId = "pending-${System.currentTimeMillis()}"` and clears `streamSubAgents = emptyList()`, `streamTodos = emptyList()`), `editMessageAndResend()` and `rerollMessage()`:
  1. Do not update `currentTurnId` to `"pending-..."` and do not clear `targetStreamFullText` or cancel `streamPacerJob`.
  2. Do not clear `streamSubAgents` or `streamTodos`.
  3. Because `targetStreamFullText` still holds the previous turn's text and `currentTurnId` still holds the previous turn's ID, if a status poll runs before the new turn's `turn_id` is emitted, `resolveStreamText()` sees `sameTurn == true` and `isStreamPlaceholder("正在思考…") == true`, and returns `prevFull` (the old turn's full text!) instead of `"正在思考…"` or `"正在重新生成…"`!
- **Concrete Impact**:
  When editing and resending a message or rerolling a reply, the old assistant response text can flash back into the streaming bubble before the new stream starts, and old sub-agent/todo cards remain visible.
- **Recommended Fix**:
  In `send()`, `editMessageAndResend()`, and `rerollMessage()`, cancel `streamPacerJob`, reset `targetStreamFullText = ""`, set `currentTurnId = "pending-${System.currentTimeMillis()}"`, and clear `streamSubAgents = emptyList()` and `streamTodos = emptyList()`.

---

### [BUG-717] `MEDIUM` — Background Polling Loop in `NativePulseActivity.onResume()` Polls Faster When Backgrounded (`!resumed`) Than When Foregrounded (`resumed && wsOnline`)
- **Category**: Lifecycle / Battery & Network Drain
- **File**: `D:\bubuki\proactive-personal-agent\android\app\src\main\java\dev\bubuki\proactive\NativePulseActivity.kt` (Lines 97–136)
- **Root Cause & Evidence**:
  ```kotlin
  val fast = model.hasInFlightWork() || (resumed && model.ui.activePanel == "cloud_desktop")
  if (resumed && model.ui.activePanel == "cloud_desktop") {
      ...
  } else if (fast) {
      if (!wsOnline || !resumed) {
          model.refreshStreamOnly()
      }
      delay(if (wsOnline && resumed) 12_000L else if (!resumed) 2200L else 800L)
  }
  ```
  When the Activity is in the foreground (`resumed == true`) and WebSocket is connected (`wsOnline == true`), it does **not** call `refreshStreamOnly()` in the `fast` branch and waits `12_000L` (relying on 0ms WebSocket push).
  However, as soon as the user presses Home (`onPause()` sets `resumed = false`), if `model.hasInFlightWork()` is true (or stuck true due to a background bot working or BUG-708 / BUG-714), `!wsOnline || !resumed` becomes `true` and the loop polls `/api/status` over HTTP **every 2.2 seconds in the background** even though `AgentForegroundService` WebSocket is connected!
- **Concrete Impact**:
  Backgrounding the app during any active or stuck turn triggers continuous 2.2-second HTTP polling in the background despite an active WebSocket connection.
- **Recommended Fix**:
  Only poll in the background if `!wsOnline`, and cap background in-flight polling duration (e.g. max 60 seconds after `onPause()`) so a stuck `workingBot` state cannot poll forever.

---

### [BUG-718] `LOW` — `switchAdjacentBot()` Iterates Over Raw `ui.bots` Instead of `visibleBots()`, Navigating Into Hidden Bots
- **Category**: UI Navigation / Logic Defect
- **File**: `D:\bubuki\proactive-personal-agent\android\app\src\main\java\dev\bubuki\proactive\NativePulseActivity.kt` (Lines 2861–2869)
- **Root Cause & Evidence**:
  ```kotlin
  fun switchAdjacentBot(delta: Int) {
      val list = ui.bots
      if (list.size <= 1) return
      val curIdx = list.indexOfFirst { it.id == ui.botId }.coerceAtLeast(0)
      val nextIdx = (curIdx + delta + list.size) % list.size
      val nextBot = list[nextIdx]
      ui = ui.copy(lastSwitchDir = if (delta >= 0) 1 else -1)
      open(nextBot.id)
  }
  ```
  `switchAdjacentBot` uses `ui.bots` instead of `visibleBots()` (or `ui.bots.filter { !it.hidden }`).
- **Concrete Impact**:
  Swiping between bots opens hidden bots (`bot.hidden == true`) and ignores the pinned/recent sort order displayed on the home screen.
- **Recommended Fix**:
  Use `val list = visibleBots().ifEmpty { ui.bots.filter { !it.hidden } }` in `switchAdjacentBot()`.

---

### [BUG-719] `LOW` — Unbounded In-Memory `stickerCache` and Unclosed `HttpURLConnection` in `loadStickerBitmap()`, `checkForUpdates()`, and `downloadLatestApk()`
- **Category**: Resource Management / Memory & Socket Leak
- **File**: `D:\bubuki\proactive-personal-agent\android\app\src\main\java\dev\bubuki\proactive\NativePulseActivity.kt` (Lines 1333–1339, 1384–1411, 2818–2842)
- **Root Cause & Evidence**:
  1. `stickerCache` is a `ConcurrentHashMap<String, ImageBitmap>()` with no size limit or eviction policy, retaining decoded `ImageBitmap` instances in memory for the lifetime of `NativePulseModel`.
  2. `loadStickerBitmap()`, `checkForUpdates()`, and `downloadLatestApk()` open `java.net.HttpURLConnection` without calling `conn.disconnect()` in a `finally` block (and without using the shared `SecureTransport.HTTP` `OkHttpClient` pool).
- **Concrete Impact**:
  Decoded sticker bitmaps accumulate in heap memory without bound, and failed/timed-out `HttpURLConnection` instances are not explicitly disconnected.
- **Recommended Fix**:
  Use an `LruCache<String, ImageBitmap>` (e.g. capped at 32 entries) for `stickerCache`, and either use `SecureTransport.HTTP` or ensure `conn.disconnect()` in `finally`.

---


---
# 【分卷】Auditor-8 (安卓 NativePulseScreen 下半部 / Markdown渲染器 / 主题与旧Activity) (`audit_8_screen_part2_markdown.md`)

# Auditor-8 Report: `NativePulseScreen.kt` (Part 2: Lines 2200–End), `markdown/*`, & Legacy/Support Activities

## Scope
1. `D:\bubuki\proactive-personal-agent\android\app\src\main\java\dev\bubuki\proactive\NativePulseScreen.kt` (Lines 2200 to EOF)
2. `D:\bubuki\proactive-personal-agent\android\app\src\main\java\dev\bubuki\proactive\markdown\MarkdownModel.kt`, `MarkdownText.kt`, `StreamingMarkdown.kt`
3. `D:\bubuki\proactive-personal-agent\android\app\src\main\java\dev\bubuki\proactive\MainActivity.java`, `NativePhoneActivity.kt`, `LauncherActivity.java`, `NativePulseIcons.kt`, `AvatarImages.java`, `PulseTheme.kt`

---
## Section 1: `NativePulseScreen.kt` (Lines 2200–2999)

### [BUG-801] MEDIUM — Surrogate-Pair Tearing in Typewriter Substring Slicing (`String.take`)
- **Category**: UI/UX Defect / Text Rendering Corruption
- **File & Lines**: `D:\bubuki\proactive-personal-agent\android\app\src\main\java\dev\bubuki\proactive\NativePulseScreen.kt:2991-2993`
- **Root Cause & Evidence**:
  ```kotlin
  val isTypingThis = s.activeTypewriterMsgId == message.id && s.activeTypewriterChars < message.text.length
  val shownText = if (isTypingThis) message.text.take(s.activeTypewriterChars.coerceAtLeast(1)) else message.text
  ```
  `String.take(n)` slices by 16-bit UTF-16 `Char` units rather than Unicode code points or grapheme clusters. Whenever `message.text` contains supplementary characters (emojis like `😊`, `🔥`, flags, or CJK Extension B+ characters), `s.activeTypewriterChars` frequently lands between a high surrogate (`\uD800`..`\uDBFF`) and low surrogate (`\uDC00`..`\uDFFF`), leaving a dangling high surrogate at the end of `shownText`.
- **Concrete Impact**: During streaming/typewriter animation, emojis and supplementary characters flicker as corrupt replacement characters (`\uFFFD` / ``) and cause layout jitter.
- **Recommended Fix**: Adjust the slice index so that if `shownText.last().isHighSurrogate()`, either include the next char (if available) or drop the trailing unpaired high surrogate.

### [BUG-802] MEDIUM — User Bubble Parsing Falsely Treats User-Written Markdown Blockquotes as Reply Quotes & Corrupts Copy Action
- **Category**: Logic Error / Data Loss on Copy
- **File & Lines**: `D:\bubuki\proactive-personal-agent\android\app\src\main\java\dev\bubuki\proactive\NativePulseScreen.kt:2698-2708`, `2848`
- **Root Cause & Evidence**:
  ```kotlin
  val hasQuote = rawText.startsWith("> ") && rawText.contains("\n\n")
  val quotePart = if (hasQuote) rawText.substringBefore("\n\n").lines().joinToString(" ") { it.removePrefix("> ").trim() } else ""
  val rawBody = if (hasQuote) rawText.substringAfter("\n\n") else rawText
  ...
  TelegramActionChip("copy", "复制", c.ink) { model.copy(bodyPart); showActions = false }
  ```
  1. Any user message that legitimately begins with a Markdown blockquote (`> ...`) followed by `\n\n` is misclassified as a "引用消息" reply header, flattening all lines of the first paragraph and stripping it from `bodyPart`.
  2. Clicking "复制" (`model.copy(bodyPart)`) only copies `bodyPart`, silently dropping the first paragraph (`quotePart`) if it started with `> `, or copying an empty string `""` if the user message only contained attachments (`bodyPart == ""`).
- **Concrete Impact**: Users cannot copy the full text of messages that begin with `> `, and copying an attachment-only message overwrites the clipboard with an empty string.
- **Recommended Fix**: Distinguish system-generated reply quotes (e.g., via metadata or a dedicated reply prefix format) or fall back to copying `cleanBody.ifBlank { rawText }` when `bodyPart` is empty.

### [BUG-803] LOW — Sticker Drawer Hardcodes `"喵～"` Persona Suffix for All Bots & Sends Text Instead of Sticker
- **Category**: UI/UX Persona Defect
- **File & Lines**: `D:\bubuki\proactive-personal-agent\android\app\src\main\java\dev\bubuki\proactive\NativePulseScreen.kt:2208-2211`
- **Root Cause & Evidence**:
  ```kotlin
  .iosPressScale(0.93f) {
      showStickerDrawer = false
      model.sendQuickPrompt("[表情: $slabel] 喵～")
  }
  ```
  Tapping a sticker in the drawer always sends the literal text `"[表情: $slabel] 喵～"`, regardless of the current bot's persona or user language/style, and does not render the sticker image in the user's outgoing bubble.
- **Concrete Impact**: Every sticker tap forces the user to say `"喵～"` even when chatting with serious/professional sub-bots, and the outgoing message shows raw text rather than the sticker image.
- **Recommended Fix**: Remove the hardcoded `" 喵～"` suffix and either attach/render the sticker URL (`url`) in the user bubble or send `[表情: $slabel]`.

### [BUG-804] MEDIUM — Send Button Remains Active While Attachment Upload Is Still In Progress (`s.uploading == true`)
- **Category**: Race Condition / UX Defect
- **File & Lines**: `D:\bubuki\proactive-personal-agent\android\app\src\main\java\dev\bubuki\proactive\NativePulseScreen.kt:2466-2496`
- **Root Cause & Evidence**:
  ```kotlin
  val showStopOnly = executing && !hasDraft
  val buttonEnabled = hasDraft || showStopOnly
  ```
  `buttonEnabled` does not check `!s.uploading`. If the user has typed text in `s.draft` and selects an image/file that is currently uploading (`s.uploading == true`), the Send button remains enabled and blue. Tapping it triggers `model.send()` before the upload finishes.
- **Concrete Impact**: Message is sent without the attachment that is still uploading, or races with `s.uploading` state.
- **Recommended Fix**: Disable sending while `s.uploading` is true (`val buttonEnabled = (!s.uploading && hasDraft) || showStopOnly`).

## Section 2: `NativePulseScreen.kt` (Lines 3000–3799)

### [BUG-805] HIGH — "触感震动反馈" (`s.haptics`) and "阻尼弹簧过渡动效" (`s.motion`) Settings Switches Are Completely Non-Functional Placebos
- **Category**: UI Logic Contradiction / Dead Settings Toggle
- **File & Lines**: `D:\bubuki\proactive-personal-agent\android\app\src\main\java\dev\bubuki\proactive\NativePulseScreen.kt:3700-3715` vs. lines `2390, 2407, 2451, 2492, 2622, 2665, 2750, 2754, 2758, 2914, 2961, 2965, 2969, 3059, 3111, 3252, 3790`
- **Root Cause & Evidence**:
  In `NativeSettingsPanel` (`subPage == "appearance"`), the UI presents two switches:
  ```kotlin
  Switch(checked = s.motion, onCheckedChange = { model.updateSettingsField(motion = it) })
  ...
  Switch(checked = s.haptics, onCheckedChange = { model.updateSettingsField(haptics = it) })
  ```
  However, throughout `NativePulseScreen.kt`, every single call to `haptics.performHapticFeedback(...)` (20+ call sites) executes unconditionally without checking `s.haptics`, and all spring animations / transitions execute unconditionally without checking `s.motion`.
- **Concrete Impact**: Turning off "触感震动反馈" or "阻尼弹簧过渡动效" in Settings has zero effect; the app continues vibrating on every tap/swipe and running all spring animations.
- **Recommended Fix**: Provide a wrapper or custom `HapticFeedback` via `CompositionLocalProvider` that no-ops when `!s.haptics`, and gate spring/parallax animations on `s.motion`.

### [BUG-806] MEDIUM — Missing System `BackHandler` in `NativeSettingsPanel` Sub-Pages
- **Category**: Navigation / UX Defect
- **File & Lines**: `D:\bubuki\proactive-personal-agent\android\app\src\main\java\dev\bubuki\proactive\NativePulseScreen.kt:3337, 3363`
- **Root Cause & Evidence**:
  `NativeSettingsPanel` uses local state `var subPage by rememberSaveable { mutableStateOf<String?>(null) }` for its 5 second-level pages (`"api"`, `"phone"`, `"rhythm"`, `"appearance"`, `"update"`). While the top-left UI button checks `if (subPage != null) subPage = null else model.closePanel()`, there is no `BackHandler(enabled = subPage != null) { subPage = null }` registered.
- **Concrete Impact**: When a user is inside any second-level settings page (e.g., editing API keys in `"api"`) and uses the Android system Back gesture/button, the entire Settings panel closes instead of returning to the level-1 Settings menu.
- **Recommended Fix**: Add `BackHandler(enabled = subPage != null) { focus.clearFocus(); subPage = null }` inside `NativeSettingsPanel`.

### [BUG-807] LOW — `NativeMessage` "钉住记忆" Truncates Using UTF-16 `take(280)` and Copies Raw Uncleaned Text
- **Category**: Text Handling / UX Defect
- **File & Lines**: `D:\bubuki\proactive-personal-agent\android\app\src\main\java\dev\bubuki\proactive\NativePulseScreen.kt:3099`
- **Root Cause & Evidence**:
  ```kotlin
  TelegramActionChip("pin", "钉住记忆", c.accent) { model.addMemoryPin(message.text.take(280)); showActions = false }
  ```
  `message.text.take(280)` can split a UTF-16 surrogate pair at index 280 and silently truncates long important facts without indicating truncation (`…`).
- **Concrete Impact**: Memory pins created from long bot messages may end mid-sentence or with a broken surrogate character.
- **Recommended Fix**: Use a surrogate-safe truncation helper and append `"…"` when `message.text.length > 280`.

## Section 3: `NativePulseScreen.kt` (Lines 3800–4964)

### [BUG-808] HIGH — `nativeWikiMarkdown` Corrupts Bash Conditionals `[[ ... ]]` and Nested Arrays Inside Code Blocks
- **Category**: Markdown Preprocessor / Code Corruption Bug
- **File & Lines**: `D:\bubuki\proactive-personal-agent\android\app\src\main\java\dev\bubuki\proactive\NativePulseScreen.kt:4413-4418` (called at lines `2642`, `2994`)
- **Root Cause & Evidence**:
  ```kotlin
  internal fun nativeWikiMarkdown(text: String): String = Regex("\\[\\[([^\\]\\n]+)\\]\\]").replace(text) { match ->
      val parts = match.groupValues[1].split('|', limit = 2)
      val path = parts[0]
      val label = parts.getOrElse(1) { path }.replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]")
      "[$label](#vault:${android.net.Uri.encode(path)})"
  }
  ```
  `nativeWikiMarkdown` applies a global regex replacement across the entire raw message text before Markdown parsing, without skipping fenced code blocks (` ```...``` `) or inline code spans (`` `...` ``).
- **Concrete Impact**: Any Bash script with `if [[ -f /path ]]; then` or Python/JS 2D array like `arr = [[1, 2]]` inside a code block is rewritten into `[ -f /path ](#vault:%20-f%20%2Fpath%20)` or `[1, 2](#vault:1%2C%202)`. Both the displayed code block and the "复制代码" button output broken, unrunnable code.
- **Recommended Fix**: Only transform `[[...]]` wiki-links outside of fenced code blocks and inline code spans (or handle `[[...]]` directly inside `parseInlineMarkdown`).

### [BUG-809] HIGH — `NativeCloudDesktopPanel` Ignores `takingControl` Flag for Screen Clicks and Dock App Launches
- **Category**: UI Logic Contradiction / Accidental Remote Input
- **File & Lines**: `D:\bubuki\proactive-personal-agent\android\app\src\main\java\dev\bubuki\proactive\NativePulseScreen.kt:4427, 4505-4526, 4577, 4663-4678`
- **Root Cause & Evidence**:
  The bottom bar displays:
  ```kotlin
  if (takingControl) "您当前拥有控制权（可直接点击画面或输入）" else "$botName 拥有控制权"
  ```
  and a button `"获取控制权"` that toggles `takingControl`. However, `Image(...).pointerInput(takingControl)` at lines 4505–4526 never checks `if (!takingControl) return@detectTapGestures`:
  ```kotlin
  modifier = Modifier.fillMaxSize().pointerInput(takingControl) {
      detectTapGestures(
          onTap = { offset ->
              if (size.width > 0 && size.height > 0) {
                  focus.clearFocus()
                  model.cloudDesktopClick(offset.x / size.width.toFloat(), offset.y / size.height.toFloat(), "click")
              }
          }, ...
  ```
  Similarly, the dock buttons at line 4577 call `model.cloudDesktopLaunchApp(appKey, urlInput)` unconditionally even when `takingControl == false`.
- **Concrete Impact**: While the user is merely viewing the cloud desktop (`takingControl == false`, "$botName 拥有控制权"), accidental touches on the screen or dock immediately inject mouse clicks, right-clicks, or launch apps, interfering with the Bot's active browser/desktop automation.
- **Recommended Fix**: Gate `detectTapGestures` (or `pointerInput`) and dock app launches on `if (takingControl)`.

### [BUG-810] MEDIUM — `NativeBotEditorPanel` "删除此 Bot" Has Zero Confirmation Dialog and Shares Identical Styling with Normal Action
- **Category**: Destructive Action UX / Data Loss Risk
- **File & Lines**: `D:\bubuki\proactive-personal-agent\android\app\src\main\java\dev\bubuki\proactive\NativePulseScreen.kt:4243-4248, 4945-4963`
- **Root Cause & Evidence**:
  ```kotlin
  if (s.primary != s.editingBotId) {
      PulseAction("将此 Bot 设为主助理", filled = false) { model.setPrimaryBot(s.editingBotId) }
  }
  if (s.bots.size > 1 && s.primary != s.editingBotId) {
      PulseAction("删除此 Bot", filled = false) { model.deleteBot(s.editingBotId) }
  }
  ```
  `PulseAction(..., filled = false)` renders in neutral surface/ink colors and immediately invokes `model.deleteBot(s.editingBotId)` on a single tap without any confirmation dialog.
- **Concrete Impact**: A single mis-tap at the bottom of the Bot Profile screen irreversibly deletes the Bot and its configuration.
- **Recommended Fix**: Style destructive actions with `c.warn` text and require an explicit confirmation dialog (`AlertDialog`) before calling `model.deleteBot(...)`.

### [BUG-811] MEDIUM — `NativeVaultPanel` Does Not Reset Scroll Position on File Switch, Drops Unsaved Edits Silently, and Lacks `imePadding`
- **Category**: UI State & Keyboard Handling Defect
- **File & Lines**: `D:\bubuki\proactive-personal-agent\android\app\src\main\java\dev\bubuki\proactive\NativePulseScreen.kt:4278-4355`
- **Root Cause & Evidence**:
  ```kotlin
  BasicTextField(
      value = s.vaultContent,
      onValueChange = { model.updateVaultContent(it) },
      modifier = Modifier.fillMaxSize().verticalScroll(rememberScrollState()),
      textStyle = TextStyle(color = c.ink, fontSize = 14.5.sp, lineHeight = 22.sp),
      cursorBrush = SolidColor(c.accent)
  )
  ```
  1. `rememberScrollState()` is not keyed on `s.selectedVaultPath`, so switching between vault files preserves the old scroll offset.
  2. `NativeVaultPanel` has no `imePadding()`, so opening the soft keyboard covers the bottom half of the editor.
  3. Tapping another file tab (`model.selectVaultFile(path)`) or "返回" (`model.closePanel()`) while `s.vaultContent` has unsaved modifications silently discards changes.
- **Concrete Impact**: Editing long Markdown vault files on mobile is obstructed by the keyboard, scroll position jumps unexpectedly when switching files, and unsaved edits are easily lost.
- **Recommended Fix**: Key the scroll state with `remember(s.selectedVaultPath) { ScrollState(0) }`, add `.imePadding()` to `NativeVaultPanel`, and prompt or auto-save before switching files/closing.

### [BUG-812] LOW — `NativeSchedulesPanel` Cannot Restart or Delete Stopped Watchdogs, Nor Delete Routines/Deadlines
- **Category**: Missing Lifecycle Controls in UI
- **File & Lines**: `D:\bubuki\proactive-personal-agent\android\app\src\main\java\dev\bubuki\proactive\NativePulseScreen.kt:4785-4872, 4915-4937`
- **Root Cause & Evidence**:
  In `NativeSchedulesPanel`, watchdog items only render a `"停止"` button when `enabled == true` (lines 4853–4864). Once stopped (`enabled == false`), there is no button to re-enable or delete the watchdog. Similarly, routines and deadlines have no delete action in the panel.
- **Concrete Impact**: Stopped watchdogs and obsolete routines/deadlines accumulate permanently in the UI with no manual cleanup button.
- **Recommended Fix**: Provide a toggle switch or restart/delete buttons for watchdogs, routines, and deadlines.

## Section 4: `markdown/` (`MarkdownModel.kt`, `MarkdownText.kt`, `StreamingMarkdown.kt`)

### [BUG-813] HIGH — `StreamingMarkdown.closeInline` Treats `\` as Escape Inside Inline Code Spans and Skips `\n` on Trailing Backslash `\\\n`
- **Category**: Streaming Markdown Parser Logic Bug
- **File & Lines**: `D:\bubuki\proactive-personal-agent\android\app\src\main\java\dev\bubuki\proactive\markdown\StreamingMarkdown.kt:78-96`
- **Root Cause & Evidence**:
  ```kotlin
  val c = source[i]
  if (c == '\n') { lineStart = true; i++; continue }
  lineStart = false
  if (fence != null) { i++; continue }
  if (c == '\\') { i = (i + 2).coerceAtMost(source.length); continue }
  if (c == ']' && source.getOrNull(i + 1) == '(') {
      val end = source.indexOf(')', i + 2)
      if (end >= 0) { i = end + 1; continue }
  }
  if (c == '`') {
      val count = runLength(source, i, '`')
      val marker = "`".repeat(count)
      if (code == marker) { opened.removeAt(opened.lastIndex); code = null }
      else if (code == null) { opened += Open(marker, i); code = marker }
      i += count
      continue
  }
  if (code != null || c !in "*_~") { i++; continue }
  ```
  There are three compounding parser defects here:
  1. **Backslash inside inline code (`code != null`)**: According to CommonMark, backslashes `\` inside inline code spans (`` `...` ``) are literal characters, not escapes. Because `if (c == '\\')` executes *before* `if (c == '`')` and *before* `if (code != null)`, any inline code ending with a backslash (such as `` `C:\Users\` `` or `` `\` ``) causes `i = i + 2` to skip over the closing backtick `` ` ``. `closeInline` then believes `code` is still open for the remainder of the document and appends a bogus `` ` `` at the end.
  2. **Link syntax `](...)` inside inline code (`code != null`)**: Line 83 (`if (c == ']' && ...)`) also runs while `code != null`, skipping to `)` and jumping over any closing backtick inside `(...)` (e.g., `` `a](b` `` followed by `)`).
  3. **Hard line break `\\\n` skips newline detection**: When `\` is at the end of a line (`\\\n`), `i = i + 2` skips over `\n` without setting `lineStart = true`. If the next line starts with a fenced code block ```` ``` ````, `if (lineStart)` at line 66 is `false`, so `closeInline` fails to enter `fence` mode and misinterprets ```` ``` ```` as an inline code span.
- **Concrete Impact**: Streaming messages containing Windows paths (`` `D:\bubuki\` ``), regexes, LaTeX, or `\\\n` before code blocks suffer severe formatting corruption and flickering during typewriter streaming.
- **Recommended Fix**:
  1. Check `if (code != null)` immediately after `if (fence != null)`: when inside inline code, only scan for backtick runs (`c == '`'`), ignoring `\`, `](`, and `*_~`.
  2. In `if (c == '\\')`, if `source.getOrNull(i + 1) == '\n'`, advance only `i++` so the next iteration processes `\n` and sets `lineStart = true`.

### [BUG-814] MEDIUM — `MarkdownModel.normalizeInlineSvgBlocks` Corrupts `<svg>` Inside Inline Code and Emits Extra ```` ``` ```` When Fence Count Is Odd
- **Category**: Markdown Preprocessor Bug
- **File & Lines**: `D:\bubuki\proactive-personal-agent\android\app\src\main\java\dev\bubuki\proactive\markdown\MarkdownModel.kt:83-101`
- **Root Cause & Evidence**:
  ```kotlin
  val parts = markdown.split("```")
  if (parts.size == 1) {
      return rawSvgRegex.replace(markdown) { m -> "\n```svg\n${m.value.trim()}\n```\n" }
  }
  val sb = StringBuilder()
  for (i in parts.indices) {
      if (i % 2 == 0) {
          sb.append(rawSvgRegex.replace(parts[i]) { m -> "\n```svg\n${m.value.trim()}\n```\n" })
      } else {
          sb.append("```").append(parts[i]).append("```")
      }
  }
  ```
  1. When `parts.size` is even (an odd number of ```` ``` ```` delimiters, e.g., an unclosed code fence during streaming or `` ` ``` ` `` inside inline code), the final segment has an odd index `i`, and line 97 appends a trailing `"```"` that did not exist in the original string.
  2. Inline code spans like `` `<svg>...</svg>` `` are in even-indexed `parts[i]`, so `rawSvgRegex` matches them and injects `\n```svg\n...\n```\n` right inside the inline code span.
- **Concrete Impact**: Mentioning `<svg>...</svg>` in inline code breaks the sentence into a broken `WebView` block, and streaming messages with both `<svg>` and an unclosed code fence get prematurely closed.
- **Recommended Fix**: Only wrap standalone block-level `<svg>...</svg>` tags (not preceded by `` ` `` on the same line) and only append `"```"` between split segments rather than on both sides of odd segments when `i == parts.lastIndex`.

### [BUG-815] MEDIUM — `MarkdownText` Causes Double Recomposition Per Typewriter Tick & Runs Synchronous Main-Thread Parse on Streaming Start
- **Category**: Performance / High-Frequency Recomposition Jank
- **File & Lines**: `D:\bubuki\proactive-personal-agent\android\app\src\main\java\dev\bubuki\proactive\markdown\MarkdownText.kt:54-66`
- **Root Cause & Evidence**:
  ```kotlin
  var streamingBlocks by remember { mutableStateOf<List<MdBlock>>(emptyList()) }
  LaunchedEffect(markdown, styles, streaming) {
      if (streaming) {
          streamingBlocks = withContext(Dispatchers.Default) { MarkdownModel.parseStreaming(markdown, styles) }
      }
  }
  val blocks = if (streaming) {
      if (streamingBlocks.isEmpty() && markdown.isNotBlank()) remember(markdown, styles) { MarkdownModel.parseStreaming(markdown, styles) }
      else streamingBlocks
  } else remember(markdown, styles) { MarkdownModel.parse(markdown, styles) }
  ```
  1. On the initial streaming frame (`streamingBlocks.isEmpty()`), line 64 parses `markdown` synchronously on the UI thread, and `LaunchedEffect` *also* parses the exact same `markdown` on `Dispatchers.Default` and writes `streamingBlocks`, triggering an immediate redundant recomposition.
  2. On every subsequent typewriter tick (~20ms), `markdown` changes -> `MarkdownText` recomposes once with stale `streamingBlocks` -> `LaunchedEffect` completes and updates `streamingBlocks` -> `MarkdownText` recomposes a second time.
  3. When `streaming` transitions from `true` to `false`, `streamingBlocks` retains its last list in memory and is never cleared; if `streaming` becomes `true` again for another message sharing the slot, stale blocks flash for one frame.
- **Concrete Impact**: Doubles Compose recomposition overhead during typewriter streaming and can flash stale blocks on slot reuse.
- **Recommended Fix**: Seed `streamingBlocks` directly or clear it when `!streaming`, and avoid running both synchronous `remember` parse and `LaunchedEffect` parse for the same `markdown` value.

### [BUG-816] MEDIUM — `SvgBlock` Leaks `WebView` Instances on Scroll, Allows Unintended External Network Loads, and Renders Black-on-Dark in Dark Mode
- **Category**: Resource Leak / Security / Dark Mode Rendering
- **File & Lines**: `D:\bubuki\proactive-personal-agent\android\app\src\main\java\dev\bubuki\proactive\markdown\MarkdownText.kt:190-224`
- **Root Cause & Evidence**:
  ```kotlin
  AndroidView(
      modifier = Modifier.fillMaxWidth().heightIn(min = 120.dp, max = 320.dp),
      factory = { ctx ->
          WebView(ctx).apply {
              setBackgroundColor(AndroidColor.TRANSPARENT)
              settings.javaScriptEnabled = false
              settings.allowFileAccess = false
              settings.allowContentAccess = false
  ```
  1. `AndroidView` does not provide an `onRelease = { it.destroy() }` callback, so `WebView` instances created inside `LazyColumn` chat items are not destroyed when scrolled off-screen.
  2. `settings.blockNetworkLoads = true` is not set, allowing `<svg><image href="https://..."/></svg>` to make external network requests.
  3. The injected HTML `<style>` does not set `color` to match `PulseTheme.colors.ink`, so SVGs using `currentColor` or default text fill render black (`#000`) against dark-mode bubbles.
- **Concrete Impact**: Native WebView memory leak on scroll, potential tracking pixel requests, and illegible dark-mode SVG text/strokes.
- **Recommended Fix**: Add `settings.blockNetworkLoads = true`, add `onRelease = { it.stopLoading(); it.destroy() }`, and pass the hex color of `PulseTheme.colors.ink` into the `<style>` (`body { color: ... }`).

### [BUG-817] LOW — `TableBlock` Forces `Constraints.fixed` Using `minIntrinsicHeight`, Clipping Multi-Line Wrapped Cell Text
- **Category**: Markdown Table Layout Defect
- **File & Lines**: `D:\bubuki\proactive-personal-agent\android\app\src\main\java\dev\bubuki\proactive\markdown\MarkdownText.kt:169-171`
- **Root Cause & Evidence**:
  ```kotlin
  val heights = measurables.chunked(cols).map { row -> row.withIndex().maxOf { (c, m) -> m.minIntrinsicHeight(widths[c]) } }
  val placeables = measurables.mapIndexed { i, m -> m.measure(Constraints.fixed(widths[i % cols], heights[i / cols])) }
  ```
  In Compose, `minIntrinsicHeight(width)` on a `Box` containing `Text` can underestimate the actual laid-out `MultiParagraph` height when mixed CJK/Latin/monospace spans wrap. Measuring with `Constraints.fixed(widths[c], heights[r])` clips the bottom line when actual text layout needs more height than `minIntrinsicHeight`.
- **Concrete Impact**: Multi-line table cells with inline code or mixed CJK/English text can have their bottom line clipped.
- **Recommended Fix**: Measure each cell first with `Constraints(minWidth = widths[c], maxWidth = widths[c])` to find the true maximum `placeable.height` for each row, then re-measure (or draw borders) to match the row height.

## Section 5: `MainActivity.java`, `NativePhoneActivity.kt`, `LauncherActivity.java`, `NativePulseIcons.kt`, `AvatarImages.java`, & `PulseTheme.kt`

### [BUG-818] HIGH — `NativePhoneActivity` Opens Legacy WebView `MainActivity` Instead of `NativePulseActivity` When Tapping Settings
- **Category**: Activity Routing Split-Brain / Legacy Conflict
- **File & Lines**: `D:\bubuki\proactive-personal-agent\android\app\src\main\java\dev\bubuki\proactive\NativePhoneActivity.kt:51-54` vs. `LauncherActivity.java:20`
- **Root Cause & Evidence**:
  In `LauncherActivity.java:20`, the app's main entry point is `NativePulseActivity.class`. However, in `NativePhoneActivity.kt:51-54`:
  ```kotlin
  private fun settings() {
      @Suppress("DEPRECATION")
      startActivityForResult(Intent(this, MainActivity::class.java).putExtra("nativeParent", true).putExtra("entryRoute", "settings"), 740)
  }
  ```
  Tapping the Settings button in `NativePhoneActivity` launches the legacy WebView `MainActivity` (which loads `assets/index.html` with a 200,000-byte read cap at `MainActivity.java:95` and overwrites `SecurePrefs` in `MainActivity.java:146`).
- **Concrete Impact**: Users navigating from the Root Phone screen into Settings are dropped into the obsolete WebView settings screen (`MainActivity`) instead of `NativePulseActivity`'s native settings, causing preference desynchronization and potential `SecurePrefs` key loss.
- **Recommended Fix**: Route `NativePhoneActivity.settings()` to `NativePulseActivity` (or finish back to `NativePulseActivity` with an intent extra to open the settings panel).

### [BUG-819] HIGH — `MarkdownText` Reads `PulseTheme.colors` (`LocalPulseColors`) While `NativePulseScreen` Uses `LocalPulsePalette`, Breaking Light Mode Rendering
- **Category**: Theme CompositionLocal Desynchronization
- **File & Lines**: `D:\bubuki\proactive-personal-agent\android\app\src\main\java\dev\bubuki\proactive\PulseTheme.kt:22, 36-46` vs. `MarkdownText.kt:55, 114, 122, 129, 135, 151, 200`
- **Root Cause & Evidence**:
  `PulseTheme.kt:22` defines:
  ```kotlin
  private val LocalPulseColors = staticCompositionLocalOf { DarkColors }
  ```
  And `PulseTheme.invoke` reads `UiPreferences.changes(LocalContext.current).collectAsState()`. Meanwhile, `NativePulseScreen.kt` manages its own theme via `LocalPulsePalette` and `s.appearance` in `NativePulseModel`. Whenever `LocalPulseColors` is not updated in lockstep with `LocalPulsePalette` (or when `NativePulseScreen` is in Light Mode while `LocalPulseColors` defaults to `DarkColors`), `MarkdownText` uses `DarkColors` (`fill = 0xFF1C1C1E`, `ink = 0xFFFFFFFF`, `ink2 = White 60%`) inside light-colored chat bubbles.
- **Concrete Impact**: In Light Mode, Markdown code blocks render with a pitch-black background, and blockquotes / list markers (`PulseTheme.colors.ink2`) render in semi-transparent white on a white bubble (becoming unreadable).
- **Recommended Fix**: Unify `PulseTheme.colors` and `LocalPulsePalette` so `MarkdownText` derives its colors from the active `LocalPulsePalette` (or ensure `PulseTheme(dark = s.appearance != "light")` wraps `NativePulseScreen` and updates `UiPreferences`).

### [BUG-820] MEDIUM — `NativePhoneActivity` "background" Toggle Uses Wrong Default (`false` Instead of `true`), Causing Inverted Toggle on First Tap
- **Category**: State Sync Contradiction
- **File & Lines**: `D:\bubuki\proactive-personal-agent\android\app\src\main\java\dev\bubuki\proactive\NativePhoneActivity.kt:41-45` vs. `MainActivity.java:202`
- **Root Cause & Evidence**:
  ```kotlin
  "background" -> {
      val enabled = withContext(Dispatchers.IO) { synchronized(SecurePrefs::class.java) { val saved = SecurePrefs.read(this@NativePhoneActivity); val enabled = !saved.optBoolean("background"); SecurePrefs.save(this@NativePhoneActivity, saved.put("background", enabled)); enabled } }
  ```
  `saved.optBoolean("background")` (1-arg overload) returns `false` when `"background"` has not been explicitly written to `SecurePrefs` yet, even though the background service is enabled by default (`optBoolean("background", true)` in the rest of the app).
- **Concrete Impact**: The first tap on the background service toggle in `NativePhoneActivity` sets `enabled = !false = true` instead of turning it off.
- **Recommended Fix**: Use `val enabled = !saved.optBoolean("background", true)`.

### [BUG-821] MEDIUM — `PulseAvatar` Runs Two Unconditional `infiniteRepeatable` Canvas Animations at 60/120fps Forever for Every Visible Avatar
- **Category**: Performance / Battery Drain / Ignored Reduce-Motion Setting
- **File & Lines**: `D:\bubuki\proactive-personal-agent\android\app\src\main\java\dev\bubuki\proactive\NativePulseIcons.kt:153-216` & `PulseTheme.kt:37`
- **Root Cause & Evidence**:
  In `PulseAvatar`, `rememberInfiniteTransition` runs `eyeScaleY` (4200ms loop) and `eyeShiftX` (1200ms/3600ms loop) continuously for every rendered geometric avatar, redrawing the `Canvas` on every frame even when the bot is completely idle and even when `state.motion == false` or `animationsOff(context)` is true. Furthermore, `PulseTheme.invoke(dark: Boolean, reduceMotion: Boolean = false, ...)` in `PulseTheme.kt:37` accepts `reduceMotion` but never reads or provides it.
- **Concrete Impact**: Continuous 60/120Hz GPU/CPU recomposition and canvas redraws on the chat screen and drawer, draining battery even when the screen is completely static.
- **Recommended Fix**: Only run the continuous `eyeShiftX` animation when `isSpeaking` (or `state.motion` is enabled), and pause infinite transitions when idle or when motion is disabled.

### [BUG-822] LOW — `AvatarImages.read` Encodes Up to 1536×1536 Photos as Lossless PNG Base64 (3–8 MB Payload) and Can Throw NPE on Null InputStream
- **Category**: Memory / Network Payload Bloat & Null Safety
- **File & Lines**: `D:\bubuki\proactive-personal-agent\android\app\src\main\java\dev\bubuki\proactive\AvatarImages.java:26-41`
- **Root Cause & Evidence**:
  ```java
  try(InputStream in=context.getContentResolver().openInputStream(uri)){bytes=Io.read(in,PHOTO_LIMIT+1);}
  ...
  bitmap.compress(Bitmap.CompressFormat.PNG,100,out);
  return new JSONObject().put("image","data:image/png;base64,"+Base64.encodeToString(out.toByteArray(),Base64.NO_WRAP))
  ```
  1. `ContentResolver.openInputStream(uri)` can return `null`, causing `Io.read(null, ...)` to throw an uninformative `NullPointerException`.
  2. Compressing a `1536x1536` camera photo as lossless `PNG` produces a 3–6 MB binary (4–8 MB Base64 JSON string) for a small circular avatar thumbnail.
- **Concrete Impact**: Slow avatar upload/sync and bloated JSON payloads in `MainActivity`.
- **Recommended Fix**: Check `if (in == null) throw new IOException("无法读取所选图片")`, center-crop to a smaller square (e.g. `512x512`), and compress as JPEG/WebP (quality 85).

---

## Summary Table of All 22 Findings (Auditor-8)

| Bug ID | Severity | File | Line(s) | Title |
| :--- | :--- | :--- | :--- | :--- |
| **BUG-805** | `HIGH` | `NativePulseScreen.kt` | 3700–3715 | `s.haptics` ("触感震动反馈") and `s.motion` ("阻尼弹簧过渡动效") settings switches are completely non-functional placebos |
| **BUG-808** | `HIGH` | `NativePulseScreen.kt` | 4413–4418 | `nativeWikiMarkdown` corrupts Bash conditionals `[[ ... ]]` and nested 2D arrays inside fenced/inline code blocks |
| **BUG-809** | `HIGH` | `NativePulseScreen.kt` | 4505–4526, 4577 | `NativeCloudDesktopPanel` ignores `takingControl` flag—screen clicks and dock app launches fire even when Bot holds control |
| **BUG-813** | `HIGH` | `StreamingMarkdown.kt` | 78–96 | `StreamingMarkdown.closeInline` treats `\` and `](` as active inside inline code spans and skips `\n` on trailing `\\\n` |
| **BUG-818** | `HIGH` | `NativePhoneActivity.kt` | 51–54 | `NativePhoneActivity` launches legacy WebView `MainActivity` instead of `NativePulseActivity` when opening Settings |
| **BUG-819** | `HIGH` | `PulseTheme.kt` / `MarkdownText.kt` | 22, 36–46 / 55+ | `MarkdownText` reads `PulseTheme.colors` while `NativePulseScreen` uses `LocalPulsePalette`, breaking Light Mode rendering |
| **BUG-801** | `MEDIUM` | `NativePulseScreen.kt` | 2991–2993 | UTF-16 surrogate-pair tearing in typewriter substring slicing (`message.text.take(...)`) |
| **BUG-802** | `MEDIUM` | `NativePulseScreen.kt` | 2698–2708, 2848 | User bubble parser misclassifies user-written `> ` blockquotes as reply quotes and strips them on Copy |
| **BUG-804** | `MEDIUM` | `NativePulseScreen.kt` | 2466–2496 | Send button remains enabled while attachment upload (`s.uploading`) is still in progress |
| **BUG-806** | `MEDIUM` | `NativePulseScreen.kt` | 3337, 3363 | Missing `BackHandler` in `NativeSettingsPanel` sub-pages closes the entire panel on system Back gesture |
| **BUG-810** | `MEDIUM` | `NativePulseScreen.kt` | 4246–4248 | `NativeBotEditorPanel` "删除此 Bot" has zero confirmation dialog and neutral button styling |
| **BUG-811** | `MEDIUM` | `NativePulseScreen.kt` | 4278–4355 | `NativeVaultPanel` shares scroll state across files, drops unsaved edits silently, and lacks `imePadding` |
| **BUG-814** | `MEDIUM` | `MarkdownModel.kt` | 83–101 | `normalizeInlineSvgBlocks` corrupts `<svg>` inside inline code and emits extra ```` ``` ```` when fence count is odd |
| **BUG-815** | `MEDIUM` | `MarkdownText.kt` | 54–66 | `MarkdownText` causes double recompositions per typewriter tick and synchronous main-thread parse on start |
| **BUG-816** | `MEDIUM` | `MarkdownText.kt` | 190–224 | `SvgBlock` leaks `WebView` instances on scroll, allows external network loads, and renders black-on-dark in Dark Mode |
| **BUG-820** | `MEDIUM` | `NativePhoneActivity.kt` | 41–45 | `NativePhoneActivity` `"background"` toggle defaults `optBoolean` to `false` instead of `true` |
| **BUG-821** | `MEDIUM` | `NativePulseIcons.kt` | 153–216 | `PulseAvatar` runs two unconditional `infiniteRepeatable` canvas animations at 60/120fps forever |
| **BUG-803** | `LOW` | `NativePulseScreen.kt` | 2208–2211 | Sticker drawer hardcodes `"喵～"` suffix for all bots and sends plain text instead of rendering sticker |
| **BUG-807** | `LOW` | `NativePulseScreen.kt` | 3099 | "钉住记忆" truncates with `take(280)` without surrogate safety or ellipsis indicator |
| **BUG-812** | `LOW` | `NativePulseScreen.kt` | 4785–4937 | `NativeSchedulesPanel` lacks UI actions to restart/delete stopped watchdogs or delete routines/deadlines |
| **BUG-817** | `LOW` | `MarkdownText.kt` | 169–171 | `TableBlock` forces `Constraints.fixed` using `minIntrinsicHeight`, clipping wrapped cell text |
| **BUG-822** | `LOW` | `AvatarImages.java` | 26–41 | `AvatarImages.read` encodes 1536×1536 photos as multi-MB lossless PNG Base64 and lacks null stream check |


---
# 【分卷】Auditor-9 (PC前端 client.js / 云桌面 computer_*.py) (`audit_9_clientjs_clouddesktop.md`)

# Audit Report 9: PC Frontend (`client.js`) & Cloud Desktop (`computer_*.py`)

**Auditor**: Auditor-9
**Target Files**:
1. `D:\dsh-baike-plugin\client.js` (1,296 lines)
2. `D:\bubuki\proactive-personal-agent\server\computer_desktop.py`
3. `D:\bubuki\proactive-personal-agent\server\computer_api.py`, `computer_client.py`, `computer_files.py`, `computer_models.py`, `computer_runtime.py`, `computer_security.py`, `computer_shell.py`

---

## Part 1: `D:\dsh-baike-plugin\client.js` Audit Findings

### [BUG-901] CRITICAL — Stored / Reflected XSS in `sanitizeSvgSnippet()` via `<style>`, `<use>`, `<image>`, SMIL `<set>`/`<animate>`, and Unquoted Event Handlers
- **Category**: Security / XSS Vulnerability
- **File & Lines**: `D:\dsh-baike-plugin\client.js:64-73`, `75-99`
- **Root Cause & Code Evidence**:
  ```javascript
  function sanitizeSvgSnippet(rawSvg) {
    var s = String(rawSvg || "").trim();
    if (!/^<svg[\s>]/i.test(s) || !/<\/svg>$/i.test(s)) return "";
    s = s
      .replace(/<script[\s\S]*?<\/script>/gi, "")
      .replace(/<foreignObject[\s\S]*?<\/foreignObject>/gi, "")
      .replace(/\son[a-z]+\s*=\s*(?:"[^"]*"|'[^']*'|[^\s>]+)/gi, "")
      .replace(/(?:href|xlink:href)\s*=\s*(?:"\s*javascript:[^"]*"|'\s*javascript:[^']*')/gi, "");
    return s;
  }
  ```
  1. **SMIL Animation XSS**: `<set attributeName="onload" to="alert(1)"/>` or `<animate attributeName="href" values="javascript:..." />` executes arbitrary JS inside an inline `<svg>` without using `on*` attributes or `<script>`.
  2. **HTML Entities & Unquoted `href`**: `href="&#106;avascript:alert(1)"` or `href=javascript:alert(1)` (unquoted) or `href="data:text/html,..."` completely bypasses `/(?:href|xlink:href)\s*=\s*(?:"\s*javascript:[^"]*"|'\s*javascript:[^']*')/gi`.
  3. **Nested/Self-closing `<script>` or `<svg><script .../>`**: Self-closing `<script xlink:href="data:text/javascript,..." />` is not matched by `/<script[\s\S]*?<\/script>/gi`.
  4. **Event handler regex bypass**: `/\son[a-z]+\s*=/gi` requires a whitespace character before `on`, which fails when preceded by `/` or `"` or `'` without space (e.g., `<svg/onload=alert(1)>` or `<rect fill="red"onload="alert(1)">`).
- **Concrete Impact**: Any prompt output, colleague message, or external web/document content rendered as an inline `<svg>` in `renderRichMessageHtml` can execute arbitrary JavaScript in the Electron renderer (`dsh-deepseek-claw` context, which has access to `http://127.0.0.1:18195` local RPC and `require("@cordisjs/client")`).
- **Recommended Fix**: Parse SVG using `DOMParser` (`image/svg+xml`), strictly whitelist allowed SVG tags (`svg`, `g`, `path`, `rect`, `circle`, `ellipse`, `line`, `polyline`, `polygon`, `text`, `tspan`, `defs`, `linearGradient`, `radialGradient`, `stop`, `clipPath`, `mask`, `pattern`) and safe attributes (strip all `on*`, `href`, `xlink:href`, `<script>`, `<foreignObject>`, `<set>`, `<animate*>`), or render SVGs inside a sandboxed `<img>` data URI (`<img src="data:image/svg+xml;utf8,...">`).

---

### [BUG-902] HIGH — XSS & Broken Attribute/Selector Escaping in `whaleSrc`, `escapeHtml`, and `openMsgIds` Restoration
- **Category**: Security / DOM Injection & Crash
- **File & Lines**: `D:\dsh-baike-plugin\client.js:56-62`, `242-252`, `1044-1049`
- **Root Cause & Code Evidence**:
  1. In `renderBotAvatar` (line 251):
     ```javascript
     '<image href="' + whaleSrc + '" x="2" y="2" width="96" height="96" preserveAspectRatio="xMidYMid slice"/>'
     ```
     `whaleSrc` (`b.avatar_url || state.whaleAvatarUrl`) is concatenated directly into `href="..."` without `escapeHtml(whaleSrc)`! Additionally, if `b.avatar_url` is a relative server path or requires auth token headers/query params, it is neither resolved against the backend origin nor escaped.
  2. In `escapeHtml` (lines 56-62): Single quotes `'` are not escaped (`.replace(/'/g, "&#39;")` is missing).
  3. In `updateChatStreamOnly` (line 1046):
     ```javascript
     var restoredBox = historyListEl.querySelector('.dsb-msg-item[data-msg-id="' + midKey + '"] details.dsb-thought-box');
     ```
     `midKey` is interpolated into a CSS attribute selector without `CSS.escape(midKey)`, which throws a `DOMException: Failed to execute 'querySelector'` if a message ID ever contains quotes or special CSS characters.
- **Concrete Impact**: Unescaped `avatar_url` allows SVG/HTML injection; special characters in `msg.id` crash `updateChatStreamOnly`.
- **Recommended Fix**: Escape `whaleSrc` with `escapeHtml(whaleSrc)`, add `.replace(/'/g, "&#39;")` to `escapeHtml`, and use `CSS.escape(midKey)` in `querySelector`.

---

### [BUG-903] HIGH — Missing Interactive Choice Cards (`choices`), Missing `sub_avatar` Field Support, and Missing Image/Media Rendering in `client.js`
- **Category**: Feature / Contract Omission (PC Frontend vs Backend/Mobile)
- **File & Lines**: `D:\dsh-baike-plugin\client.js:203-234`, `287-370`, `951-1042`, `1053-1146`
- **Root Cause & Code Evidence**:
  1. **Choices Card (`m.choices` / `liveSt.choices`) Completely Missing**: Nowhere in `client.js` is `m.choices` inspected or rendered. When the backend or `ask_user` / interactive choice tool emits structured `choices` on an assistant message, `client.js` silently ignores `m.choices`, leaving the user with no clickable choice cards on PC.
  2. **`sub_avatar` Field Ignored**: `resolveMessageInterconnectMeta(m, roomBot)` (lines 287-370) and `renderAvatarCornerBadge(badgeKind)` (lines 203-234) only check `from_node`, `to_node`, `from_bot_id`, `from_bot_name`, and hardcoded strings (`"phone"`, `"pc"`, `"session"`, `"colleague"`). If a message object carries `m.sub_avatar` (or custom bot avatar URL for non-whalechan bots, since `renderBotAvatar` lines 245-263 ONLY renders `b.avatar_url` when `kind === "whalechan" || b.id === "whalechan"`!), any custom bot's `avatar_url` or `sub_avatar` is completely ignored and replaced by a generic SVG flower/star.
  3. **Message Images / Attachments Ignored**: `renderRichMessageHtml(rawText)` (lines 75-99) only extracts inline `<svg>` blocks and `escapeHtml`s everything else. Markdown images `![alt](url)`, `m.images`, `m.image_url`, or `m.attachments` are never rendered as `<img>` tags, nor is any backend host/auth URL prefixing performed. Furthermore, `getActiveBotMessages()` (line 647) filters with `return m && m.text && ...`, which **drops image-only or tool-only messages where `m.text` is empty**!
- **Concrete Impact**:
  - Users on PC cannot see or click `choices` cards.
  - Custom bots with `avatar_url` or messages with `sub_avatar` fail to display their actual avatar/sub-avatar image.
  - Messages containing images/screenshots (e.g. from `cloud_desktop` or `phone_workspace`) or empty `text` with `choices`/`images`/`tool_trace` are either dropped by `m && m.text` or displayed as raw unrendered text.
- **Recommended Fix**:
  - Render `m.choices` as interactive option buttons that call `sendChatMessage` (or submit the selected choice).
  - Support `m.sub_avatar` and `b.avatar_url` for all bots (not just `whalechan`), resolving relative `/api/...` or `/files/...` URLs against the active backend origin with auth parameters if needed.
  - In `getActiveBotMessages()`, allow messages that have `m.text || (m.choices && m.choices.length) || (m.images && m.images.length) || (m.tool_trace && m.tool_trace.length)`.

---

### [BUG-904] HIGH — Stream Output & Multi-Bot Interconnect Desync: Background/Proactive/Colleague Streams Never Render Unless Initiated by Local `runChatTurnAction`
- **Category**: State Machine / Streaming Desync
- **File & Lines**: `D:\dsh-baike-plugin\client.js:504-506`, `893`, `936`, `963-974`, `1158-1180`
- **Root Cause & Code Evidence**:
  In `updateChatStreamOnly` (lines 936, 963, 1055) and `updateComposerControlsOnly` (line 893) and `renderSidebarDock` (line 602):
  ```javascript
  var isSending = Boolean(state.sendingByBot[bot.id]);
  var liveSt = state.streamsByBot && state.streamsByBot[bot.id] ? state.streamsByBot[bot.id] : null;
  ```
  `isSending` is ONLY set to `true` inside `runChatTurnAction` (line 1162) when the local PC user clicks Send/Edit/Reroll, and is immediately set to `false` when that single HTTP request returns.
  However, `applyBackendState` (lines 504-506) receives `res.streams_by_bot` from `/local/infinite-chat/sync`:
  ```javascript
  if (res.streams_by_bot && typeof res.streams_by_bot === "object") {
    state.streamsByBot = res.streams_by_bot;
  }
  ```
  If a bot is streaming due to:
  - A colleague bot (`message_colleague` / `send_message`) triggering a reply turn,
  - A proactive turn or scheduled task,
  - A turn started before page reload, or
  - `res.busy_bots` / `state.streamsByBot[bot.id]` being active on the backend while `state.sendingByBot[bot.id]` is `false` locally,
  `isSending` evaluates to `false`! Consequently, `liveSig` becomes `"idle"` (line 974) and the live streaming bubble (`#dsb-live-turn-slot`) and Stop button are **completely hidden** even though `state.streamsByBot[bot.id]` has active streaming data!
- **Concrete Impact**: Multi-bot interconnect replies, proactive messages, and streams after a window refresh do not show live streaming bubbles, thinking indicators, or the Stop button.
- **Recommended Fix**: Compute `var hasActiveLiveStream = Boolean(liveSt && (liveSt.active || liveSt.text || liveSt.thought || liveSt.tool || (liveSt.trace && liveSt.trace.length)));` and define `var isSending = Boolean(state.sendingByBot[bot.id] || hasActiveLiveStream);`.

---

### [BUG-905] MEDIUM — Race Condition Between `stopBotGeneration()` and In-Flight `runChatTurnAction()` Overwriting State & Leaking Overlapping `syncFromBackend` Calls
- **Category**: Concurrency / Race Condition
- **File & Lines**: `D:\dsh-baike-plugin\client.js:1158-1180`, `1240-1250`
- **Root Cause & Code Evidence**:
  1. In `runChatTurnAction`:
     ```javascript
     var livePoll = setInterval(function () {
       if (!state.sendingByBot[bot.id]) { clearInterval(livePoll); return; }
       syncFromBackend(false);
     }, 280);
     ```
     `syncFromBackend(false)` is an `async` HTTP call fired every `280ms` without checking if a previous `syncFromBackend` is still in flight. If the backend takes >280ms to respond, dozens of concurrent `syncFromBackend` requests pile up and resolve out-of-order, overwriting newer `state.messages` and `state.streamsByBot` with stale snapshots!
  2. When the user clicks the Stop button (`stopBotGeneration`, lines 1240-1250):
     `state.sendingByBot[bot.id] = false;` is set immediately, allowing the user to send a *new* message on the same `bot.id` (`runChatTurnAction` checks `if (state.sendingByBot[bot.id]) return;`). When the *old* aborted `/local/infinite-chat/send` request finally resolves in the first `runChatTurnAction` (line 1170), it executes:
     ```javascript
     state.sendingByBot[bot.id] = false;
     if (state.streamsByBot) delete state.streamsByBot[bot.id];
     ```
     This prematurely kills the *new* turn's `sendingByBot[bot.id]` state and deletes its live stream!
  3. Furthermore, while `state.sendingByBot[bot.id]` is `true` and optimistic local messages (`pc_local: true`) are pushed into `state.messages` (line 1196), the 280ms `syncFromBackend(false)` calls `applyBackendState(res)` which replaces `state.messages = res.messages.filter(...)` (line 502). If the backend hasn't persisted the new message yet at `t=280ms`, the user's optimistic message bubble **vanishes for a split second and flickers back**, causing scroll jumps!
- **Concrete Impact**:
  - Out-of-order `syncFromBackend` responses cause streaming text to jump backward.
  - Optimistic user bubble disappears on the first 280ms poll if backend write is still initializing.
  - Clicking Stop and immediately sending a new message causes the old request's resolution to clobber the new turn's streaming state.
- **Recommended Fix**:
  - Guard `syncFromBackend` with an `inFlightSync` lock and a monotonic request sequence counter.
  - Track a per-bot `turnToken` in `state.sendingByBot[bot.id] = ++turnSeq` and only clear `state.sendingByBot[bot.id]` in `runChatTurnAction` if it still matches `myTurnToken`.
  - Preserve `pc_local` optimistic messages in `applyBackendState` until their `client_msg_id` or timestamp is reflected in `res.messages`.

---

### [BUG-906] MEDIUM — `historySig` Omits Tool Trace Arguments/Results & `liveSig` Omits `liveTrace` Content, Freezing Trace Pill Updates
- **Category**: UI Rendering / State Diffing Defect
- **File & Lines**: `D:\dsh-baike-plugin\client.js:951-974`, `1087-1091`
- **Root Cause & Code Evidence**:
  ```javascript
  var historySig = JSON.stringify({
    ...
    m: botMsgs.map(function (x) {
      return [
        x.id, x.role, x.kind || "", x.from_bot_id || "", x.from_bot_name || "",
        x.to_bot_name || "", x.from_node || "", x.to_node || "", x.text, x.thought,
        (x.tool_trace || x.trace || []).length
      ];
    })
  });
  var liveSig = isSending
    ? JSON.stringify([
        bot.id,
        liveSt ? liveSt.speaker_id || "" : "",
        liveSt ? liveSt.speaker_name || "" : "",
        liveSt ? liveSt.speaker_node || "" : "",
        liveSt ? liveSt.text : "",
        liveSt ? liveSt.thought : "",
        liveSt ? liveSt.tool : "",
        liveSt && liveSt.trace ? liveSt.trace.length : 0
      ])
    : "idle";
  ```
  Both `historySig` and `liveSig` only include `.length` of `trace` / `tool_trace`, not the tool names, `op`s, or `result` targets! When a tool trace item updates in-place (e.g., `result.to_bot_name` or `arguments.op` is populated without changing `trace.length`), `liveSig` and `historySig` do not change, so the DOM never updates.
  Additionally, in `formatTracePillLabel` (lines 101-117), `cloud_desktop` is not handled with `args.op` (only `dsh_workspace` and `phone_workspace` show `tool(op)`, whereas `cloud_desktop` just shows `cloud_desktop` without the `op`).
- **Concrete Impact**: Tool trace pills fail to update when trace items are updated in-place, and `cloud_desktop` tool calls do not display their `op` (e.g. `cloud_desktop(click)`).
- **Recommended Fix**: Include mapped `formatTracePillLabel` strings in `historySig` and `liveSig`, and add `if (tName === "cloud_desktop" && args.op) return tName + "(" + args.op + ")";` in `formatTracePillLabel`.

---

### [BUG-907] LOW — `boot()` Called Twice on Startup & Global Event Listeners/DOM Elements Leak Across Plugin Reloads
- **Category**: Resource / Memory Leak
- **File & Lines**: `D:\dsh-baike-plugin\client.js:570-580`, `1272-1293`
- **Root Cause & Code Evidence**:
  At lines 1272-1276, `boot()` is scheduled immediately when the module loads (`setTimeout(boot, 50)` or `DOMContentLoaded`). Then at line 1281 inside `apply: function (ctx)`, `boot()` is called a second time!
  Furthermore, when the Cordis plugin unmounts (`ctx.effect` cleanup at lines 1284-1289), it only clears `window.__dsbActiveTimers`, leaving `#dsh-baike-sidebar-dock`, `#dsh-baike-main-view`, `#dsh-baike-native-style`, any active `voiceRecorder` media stream, and the `aside` click listener (`aside.dataset.dsbClickBound = "1"` bound to the old closure's `state`) attached to the DOM! If the plugin is hot-reloaded, `aside.dataset.dsbClickBound` is already `"1"`, so the new instance's `closeBotView()` is never bound to `aside` clicks!
- **Concrete Impact**: Duplicate `syncFromBackend(true)` calls on boot; broken sidebar click-outside behavior after plugin reload; leaked DOM nodes and microphone stream on unmount.
- **Recommended Fix**: Remove the redundant top-level `boot()` call or guard with an active instance flag, and clean up DOM elements / `aside.dataset.dsbClickBound` / `voiceRecorder` inside the `ctx.effect` disposer.

---

## Part 2: `D:\bubuki\proactive-personal-agent\server\computer_desktop.py` Audit Findings

### [BUG-908] HIGH — `drag` End Coordinates (`x2`, `y2`) Never Scaled when `nx2`/`ny2` or Normalized Coordinates Are Used, and `resolve_grid_or_norm_xy` Is Bypassed for `(x2, y2)`
- **Category**: Coordinate Scaling / Resolution Bug
- **File & Lines**: `D:\bubuki\proactive-personal-agent\server\computer_desktop.py:1243`, `1269-1274`, `1345-1348`
- **Root Cause & Code Evidence**:
  In `_exec_single_input` (lines 1243 & 1269-1274):
  ```python
  px, py = resolve_grid_or_norm_xy(x, y, nx=nx, ny=ny, grid=grid, width=WIDTH, height=HEIGHT)
  ...
  elif kind == 'drag':
      await asyncio.to_thread(run, 'mousedown', '1')
      await asyncio.sleep(0.05)
      await asyncio.to_thread(run, 'mousemove', '--sync', str(max(0, min(WIDTH - 1, x2))), str(max(0, min(HEIGHT - 1, y2))))
      await asyncio.sleep(0.05)
      await asyncio.to_thread(run, 'mouseup', '1')
  ```
  1. `px, py` supports `0..1000` normalized coordinates (`nx`, `ny`) and `grid` (`A1..H10`), which the system prompt and `HOME_HTML` ("calibrated 0..1000 vision grounding") instruct the LLM to use. However, `drag` has **no `nx2` / `ny2` / `grid2` parameters** in `_exec_single_input` or `InputAction`, and `x2, y2` are passed directly to `xdotool mousemove` without `resolve_grid_or_norm_xy` or any `0..1000 -> 1280x720` scaling!
  2. Furthermore, if the LLM passes normalized `0..1000` values into `x, y` (without `nx, ny`) or `x2, y2` (because vision models output 0-1000 coordinates in `x`/`y`/`x2`/`y2`), `resolve_grid_or_norm_xy` (lines 435-440) ONLY scales `nx, ny`, and treats `x, y` as raw pixels!
- **Concrete Impact**: Any `drag` operation using normalized coordinates or grid references drags to the wrong screen position (or `(0, 0)` if `to_x`/`to_y` or `nx2`/`ny2` were passed by the agent).
- **Recommended Fix**: Add `nx2: int = -1`, `ny2: int = -1`, `grid2: str = ''` (and aliases `to_x`, `to_y`) to `InputAction` and `_exec_single_input`, and resolve `(px2, py2) = resolve_grid_or_norm_xy(x2, y2, nx=nx2, ny=ny2, grid=grid2, width=WIDTH, height=HEIGHT)`.

---

### [BUG-909] HIGH — `status()` Hardcodes `'control': 'agent'`, Breaking User Takeover / Handback State Machine (`takeover()` / `handback()` / `stop()`)
- **Category**: State Machine Contradiction
- **File & Lines**: `D:\bubuki\proactive-personal-agent\server\computer_desktop.py:455`, `777-801`, `846-862`
- **Root Cause & Code Evidence**:
  In `takeover()`, `handback()`, and `stop()` (lines 846-862):
  ```python
  async def takeover(self):
      self.control = 'user'
      return self.status()

  async def handback(self):
      self.control = 'agent'
      return self.status()

  async def stop(self):
      ...
      self.control = 'user'
      self.job = None
      return self.status()
  ```
  Yet in `status()` (lines 781-786):
  ```python
  return {
      'available': self.started,
      'width': WIDTH,
      'height': HEIGHT,
      'control': 'agent',
      'control_ready': True,
  ```
  `status()` hardcodes `'control': 'agent'` instead of returning `self.control`! And `'job': None` is also hardcoded at line 798 instead of `self.job`!
- **Concrete Impact**: Calling `/takeover` or `/stop` sets `self.control = 'user'`, but the returned `status()` payload (and every subsequent `/status` poll by the frontend) always reports `'control': 'agent'`, making the UI believe the agent still holds control and breaking user takeover mode.
- **Recommended Fix**: Change line 785 to `'control': self.control` and line 798 to `'job': self.job`.

---

### [BUG-910] HIGH — XImage Struct & Buffer Memory Leak in `_set_x11_root_wallpaper()` and Unreaped Zombie Processes in `self.children` & `venv` Creation
- **Category**: Resource / Memory & Process Leak
- **File & Lines**: `D:\bubuki\proactive-personal-agent\server\computer_desktop.py:394-406`, `598-604`, `672-686`, `954-1026`
- **Root Cause & Code Evidence**:
  1. **Zombie Process Leak on `venv` initialization** (lines 598-603):
     ```python
     if not (self.venv_dir / 'bin' / 'python3').exists():
         subprocess.Popen(
             [sys.executable, '-m', 'venv', str(self.venv_dir)],
             stdout=subprocess.DEVNULL,
             stderr=subprocess.DEVNULL,
         )
     ```
     The `Popen` object is not stored, awaited, or reaped—leaving a zombie `<defunct>` python process and creating a race condition where `venv` is incomplete when the first bash command runs!
  2. **Unbounded `self.children` List & Zombie Processes** (lines 672-686, 954-1026):
     Every time `_launch_or_focus_desktop_app` spawns `pcmanfm`, `geany`, `gnumeric`, `mousepad`, `lxtask`, `galculator`, `zathura`, or `gpicview`, `self.spawn(...)` appends the `Popen` object to `self.children` without ever polling or removing exited processes from `self.children`. Worse, for `pcmanfm`, `zathura`, and `gpicview` (lines 951-957, 1009-1026), `_launch_or_focus_desktop_app` does **not** call `focus_window` first (unlike `geany`/`gnumeric`/`lxtask`), so every click on "此电脑 Files" spawns a brand new `pcmanfm` process!
  3. **X11 `XDestroyImage` Missing** (lines 395-398):
     `ximg = x11.XCreateImage(...)` allocates an `XImage` C struct in `libX11`, which is never freed after `XPutImage`.
- **Concrete Impact**: Repeated app launches leak `<defunct>` zombie processes in the 1C 1G container, and clicking "Files" repeatedly spawns duplicate `pcmanfm` windows until RAM is exhausted.
- **Recommended Fix**:
  - In `spawn()`, reap dead children (`self.children = [c for c in self.children if c.poll() is None]`).
  - In `_launch_or_focus_desktop_app('files', ...)`, check `if not arg_text and focus_window(env, 'pcmanfm'): return 'pcmanfm_focused'` before spawning.
  - Wait for or track the `venv` creation process.

---

### [BUG-911] MEDIUM — Path Traversal in `_launch_or_focus_desktop_app()` vs `mousepad` Subdirectory Stripping Bug
- **Category**: Security / Path Handling Bug
- **File & Lines**: `D:\bubuki\proactive-personal-agent\server\computer_desktop.py:953`, `964`, `976`, `988`, `1013`, `1022`
- **Root Cause & Code Evidence**:
  - For `files`, `code`, `excel`, `pdf`, `image` (lines 953, 964, 976, 1013, 1022):
    `str(self.workspace / arg_text)` is used directly without checking `..` or `is_relative_to(self.workspace)`, allowing `arg_text = "../../etc/passwd"` to open files outside `self.workspace`.
  - Conversely, for `notepad` / `mousepad` (line 988):
    `cmd.append(str(self.workspace / Path(arg_text).name))` strips all subdirectory components via `.name`! If the agent passes `arg_text = "subdir/notes.txt"`, `mousepad` opens `workspace/notes.txt` instead of `workspace/subdir/notes.txt`!
- **Concrete Impact**: Opening a file in a workspace subdirectory with `notepad` silently opens the wrong path (`workspace/<filename>`), while other apps lack workspace boundary checks.
- **Recommended Fix**: Resolve `target_path = (self.workspace / arg_text).resolve()` and verify `target_path.is_relative_to(self.workspace.resolve())` across all desktop apps including `mousepad`.

---

### [BUG-912] MEDIUM — Blocking Synchronous `subprocess.run`, `time.sleep`, and X11 Calls on FastAPI Event Loop in `status()` and `_exec_single_input()`
- **Category**: Performance / Event Loop Blocking
- **File & Lines**: `D:\bubuki\proactive-personal-agent\server\computer_desktop.py:777-801`, `902`, `923`, `1156`, `1166`, `1182`, `1261`, `1307`, `1392`, `1503-1505`
- **Root Cause & Code Evidence**:
  - `GET /status` (`async def status(): return desktop.status()`, lines 1503-1505) calls `desktop.status()` **synchronously on the asyncio event loop**!
  - Inside `desktop.status()`, `self._sync_active_dom_state()` calls `self._list_windows()` (which runs synchronous `xprop` subprocesses) and `extract_live_cdp_dom_snapshot()` (which performs synchronous HTTP/WebSocket CDP calls to `:9222`!).
  - Similarly, in `search()` (line 902), `navigate()` (line 923), and `_exec_single_input()` (lines 1156, 1166, 1182, 1261, 1307, 1392), `self._list_windows()`, `self._active_windows_summary()`, and `tile_windows()` are called synchronously inside `async def` functions without `await asyncio.to_thread(...)`.
- **Concrete Impact**: Every `/status` poll or window query blocks the entire FastAPI event loop in `computer_desktop.py` for 50–500ms, stalling concurrent `/frame` streaming and input actions.
- **Recommended Fix**: Run `desktop.status` via `await asyncio.to_thread(desktop.status)` in the `/status` route and wrap synchronous X11/CDP helpers in `asyncio.to_thread`.

---

## Part 3: `computer_api.py`, `computer_client.py`, `computer_files.py`, `computer_models.py`, `computer_runtime.py`, `computer_security.py`, `computer_shell.py` Audit Findings

### [BUG-913] CRITICAL — `computer_api.py` `/api/computer/action` and `/api/computer/input` Fail with HTTP 422 on `navigate`, `search`, `open`, `find_text`, `wait`, `run`, and `steer` Operations
- **Category**: Contract Mismatch / Routing Failure (`harness.py` / `computer_api.py` vs `InputAction` in `computer_models.py`)
- **File & Lines**:
  - `D:\bubuki\proactive-personal-agent\server\computer_api.py:119-151`
  - `D:\bubuki\proactive-personal-agent\server\computer_models.py:15-21`
- **Root Cause & Code Evidence**:
  In `computer_api.py` (lines 133-151):
  ```python
  @router.post('/action')
  async def computer_action(payload:dict[str,Any]):
      raw=dict(payload or {})
      op=str(raw.get('op') or raw.get('action') or raw.get('kind') or 'status').strip().lower()
      ...
      if coordinator.brain is not None and hasattr(coordinator.brain,'_tool_cloud_desktop'):
          res=await coordinator.brain._tool_cloud_desktop({**raw,'op':op})
          if isinstance(res,dict) and not res.get('error'):
              return res
      return await input_action({**raw,'kind':op})
  ```
  1. When `coordinator.brain._tool_cloud_desktop` is unavailable or returns a dict with `'error'`, OR when the client calls `POST /api/computer/input` directly with `op`/`kind` values like `navigate`, `open`, `search`, `find_text`, `wait`, ` sleep`:
     `input_action` validates `raw` against `InputAction` (`computer_models.py:16-21`):
     ```python
     kind: Literal[
         'click', 'double_click', 'right_click', 'move', 'hover', 'drag',
         'scroll', 'key', 'hotkey', 'type', 'launch_app', 'bash', 'batch',
         'focus_window', 'close_window', 'tile_windows', 'clipboard',
         'click_ref', 'type_ref',
     ]
     ```
     Notice that:
     - `'navigate'` / `'open'` is NOT in `InputAction.kind` (and `/api/computer/action` does not route `op in ('navigate', 'open')` to `POST /navigate` or `/api/computer/search` to `POST /search`!).
     - `'search'` has a dedicated `POST /search` route on `computer_desktop.py:1532`, and `ComputerClient.call` allows `'/search'` (`computer_client.py:20`), **but `computer_api.py` has NO `/api/computer/search` route at all** and does not handle `op == 'search'` in `computer_action` if `_tool_cloud_desktop` fails or is bypassed!
     - `'wait'` is NOT in `InputAction.kind` (`computer_models.py:16-21`) nor in `computer_desktop.py:_exec_single_input`! If a `batch` step or `/input` action contains `kind: 'wait'`, `/input` rejects it with HTTP 422, or `_exec_single_input` silently does nothing (falls through all `if/elif` branches and only sleeps `0.12s`)!
     - In `computer_api.py:124`:
       `alias_map={'tap':'click','press':'key','press_key':'key','type_text':'type','shell':'bash','exec':'bash'}`
       Missing common aliases used by agents: `'open_app': 'launch_app'`, `'launch': 'launch_app'`, `' write': 'type'`, `'input_text': 'type'`.
  2. Furthermore, in `computer_api.py:148-151`:
     If `coordinator.brain._tool_cloud_desktop` returns `{'ok': False, 'error': '...'}` (e.g., because `navigate` failed due to an invalid URL or `search` failed), `if isinstance(res, dict) and not res.get('error'):` is **False**, so `computer_action` falls through to `return await input_action({**raw, 'kind': op})`, which then crashes with **HTTP 422 ValidationError** (`Input should be 'click', ...`) instead of returning the actual error from `_tool_cloud_desktop`!
- **Concrete Impact**: Any failed `_tool_cloud_desktop` call for `navigate`/`search`/`find_text`/`wait` masks the real error with a confusing HTTP 422 Pydantic `ValidationError` on `InputAction.kind`; direct calls to `/api/computer/action` or `batch` steps with `wait` fail or no-op.
- **Recommended Fix**:
  - In `computer_api.py:computer_action`, return `res` directly from `_tool_cloud_desktop` when `op` is handled by it, and explicitly route `op in ('navigate', 'open')` to `/navigate` and `op == 'search'` to `/search`.
  - Add `'wait'` to `InputAction.kind` in `computer_models.py` and handle `if kind == 'wait': await asyncio.sleep(max(0.1, min(15.0, float(amount or timeout or 1))))` in `computer_desktop.py:_exec_single_input`.

---

### [BUG-914] HIGH — Screen Resolution Contradiction: `HOME_HTML` & System Prompts Claim `1280×720`, While `computer_models.py` Defines `WIDTH, HEIGHT = 1280, 800`
- **Category**: Resolution / Coordinate Scaling Mismatch
- **File & Lines**:
  - `D:\bubuki\proactive-personal-agent\server\computer_models.py:4` (`WIDTH, HEIGHT = 1280, 800`)
  - `D:\bubuki\proactive-personal-agent\server\computer_desktop.py:105` (`Display :91 (1280×720)`)
- **Root Cause & Code Evidence**:
  - `computer_models.py:4` sets `WIDTH, HEIGHT = 1280, 800`.
  - `computer_desktop.py:105` displays `Display :91 (1280×720)` in `HOME_HTML`.
  - If the LLM reads the page text or prompt stating `1280×720` and computes pixel `y` coordinates for a 720p screen (e.g. clicking the bottom taskbar at `y = 700`), `y = 700` on an `800px`-high screen misses the taskbar (`py >= HEIGHT - 45` -> `py >= 755` at line 1253 of `computer_desktop.py`) by **75 pixels**!
- **Concrete Impact**: Agents grounding raw pixel coordinates `(x, y)` based on the `1280×720` text miss vertical targets (especially bottom taskbar icons at `y = 757..797`) by up to 80px.
- **Recommended Fix**: Update `HOME_HTML` (and any prompt strings mentioning `1280×720`) to `1280×800`, and in `resolve_grid_or_norm_xy`, ensure normalized `nx, ny` (`0..1000`) is consistently used.

---

### [BUG-915] HIGH — `InputAction` Validation Rejects `nx`/`ny` Normalized Coordinates Passed in `x`/`y` when `y >= 800` (`HEIGHT = 800`)
- **Category**: Schema Validation / Coordinate Mismatch
- **File & Lines**: `D:\bubuki\proactive-personal-agent\server\computer_models.py:22-27`
- **Root Cause & Code Evidence**:
  ```python
  x: int = Field(0, ge=0, lt=WIDTH)
  y: int = Field(0, ge=0, lt=HEIGHT)
  x2: int = Field(0, ge=0, lt=WIDTH)
  y2: int = Field(0, ge=0, lt=HEIGHT)
  nx: int = Field(-1, ge=-1, le=1000)
  ny: int = Field(-1, ge=-1, le=1000)
  ```
  Because `HEIGHT = 800`, `y` and `y2` have `lt=800` (`0 <= y < 800`).
  If an LLM or caller passes normalized `0..1000` coordinates in `x` and `y` (or if `x=1280` / `y=800` is on the exact bottom/right edge, or if `y` is in `800..1000` normalized range), Pydantic `InputAction.model_validate` throws HTTP 422 `Input should be less than 800`!
  Even worse, in `computer_desktop.py:1341-1359`, `batch` steps bypass `InputAction` field validation for individual steps (`step.get('x')`), whereas single actions validate against `lt=WIDTH` / `lt=HEIGHT`.
- **Concrete Impact**: Any tool call where `y` or `y2` is in `[800, 1000]` (e.g., normalized coordinates passed in `x, y` instead of `nx, ny`, or edge pixel `1280`/`800`) fails with HTTP 422 ValidationError.
- **Recommended Fix**: Relax `x, y, x2, y2` in `InputAction` to `ge=0, le=4096` (since `resolve_grid_or_norm_xy` already clamps to `0..WIDTH-1` and `0..HEIGHT-1`), and add `nx2: int = Field(-1, ge=-1, le=1000)` and `ny2: int = Field(-1, ge=-1, le=1000)`.

---

### [BUG-916] MEDIUM — Command Injection & Early Exit Bug in `computer_shell.py` Wrapper Script (`raw_cmd` Newline/Exit Handling & `cwd` Persistence Bypassed)
- **Category**: Shell Execution / State Persistence Bug
- **File & Lines**: `D:\bubuki\proactive-personal-agent\server\computer_shell.py:37`, `107-116`
- **Root Cause & Code Evidence**:
  1. In `computer_shell.py:109-115`:
     ```python
     wrapped = (
         f'{raw_cmd}\n'
         f'__EC=$?\n'
         f'pwd > "{cwd_file}" 2>/dev/null || true\n'
         f'exec >/dev/null 2>&1\n'
         f'exit $__EC\n'
     )
     os.execve('/bin/bash', ['bash', '--noprofile', '--norc', '-c', wrapped], env)
     ```
     If `raw_cmd` contains `exit 0`, `set -e` (which causes bash to exit immediately when any subcommand returns non-zero before reaching `pwd > "{cwd_file}"`!), or a syntax error / `exec`, `pwd > "{cwd_file}"` and `exec >/dev/null 2>&1` are **never executed**! Because `TRAP` on `EXIT` is not used, any command that uses `set -e` (very common in agent bash scripts) or `exit` fails to persist `agent_cwd` and fails to close stdout/stderr on background jobs!
  2. In `computer_shell.py:37`:
     ```python
     if saved_cwd and os.path.isdir(saved_cwd) and saved_cwd.startswith(desktop_home):
     ```
     `saved_cwd.startswith(desktop_home)` is a prefix string check without a trailing `/` (or `Path.is_relative_to`), and it rejects `/tmp` even though `/tmp` is inside the allowed `rw` Landlock paths!
- **Concrete Impact**: Any agent bash command using `set -e` that fails after `cd some_dir` loses the directory change and leaves inherited stdout/stderr pipes open if a background process was started.
- **Recommended Fix**: Use an `EXIT` trap at the top of `wrapped`:
  ```bash
  __ds_cleanup() {
    local __ec=$?
    pwd > "$__DS_CWD_FILE" 2>/dev/null || true
    exec >/dev/null 2>&1
    exit $__ec
  }
  trap __ds_cleanup EXIT
  ```

---

### [BUG-917] MEDIUM — `PublicWebProxy` in `computer_security.py` Is Never Wired into `_proxy_env()` or `computer_shell.py`, Leaving Port `18194` Dead While `computer_shell.py` Hardcodes Port `40001`
- **Category**: Network / Security Configuration Split-Brain
- **File & Lines**:
  - `D:\bubuki\proactive-personal-agent\server\computer_desktop.py:474-502`, `644-645`
  - `D:\bubuki\proactive-personal-agent\server\computer_security.py:85-135`
  - `D:\bubuki\proactive-personal-agent\server\computer_shell.py:98-105`
- **Root Cause & Code Evidence**:
  - `computer_desktop.py:644-645` starts `self.proxy = PublicWebProxy(18194, blocked_hosts={'216.23.82.196'})`.
  - However, `computer_desktop.py:_proxy_env()` (lines 474-502) **never sets `HTTP_PROXY` / `HTTPS_PROXY` to `http://127.0.0.1:18194`**!
  - Meanwhile, `computer_shell.py:99-103` probes `127.0.0.1:40001` (Cloudflare WARP local proxy) and sets `NO_PROXY='127.0.0.1,localhost'`, completely bypassing `PublicWebProxy(18194)` and its SSRF/private-network checks (`public_addresses`)!
  - Furthermore, in `PublicWebProxy.handle` (`computer_security.py:109-110`):
    `addresses` is resolved via `public_addresses(host, port)` in the proxy, and then `asyncio.open_connection(addresses[0], port)` connects to `addresses[0]`. However, for ` PublicWebProxy`, `writer.wait_closed()` is never awaited in `finally:` (lines 133-134), and `PublicWebProxy(18194)` sits completely unused by both the browser and shell.
- **Concrete Impact**: `PublicWebProxy` on port `18194` consumes a listening socket and resources without being used by `_proxy_env()` or `computer_shell.py`, while `computer_shell.py` processes can connect directly to `127.0.0.1` local services (like `127.0.0.1:18195` or `127.0.0.1:18193` if token is known in env!).
- **Recommended Fix**: Ensure `COMPUTER_CONTROL_TOKEN` is stripped from child environments in `spawn()` (`env = os.environ.copy()` at `computer_desktop.py:673` currently **leaks `COMPUTER_CONTROL_TOKEN` and all host environment variables** into every spawned X11 desktop app!), and align proxy configuration between `_proxy_env()` and `computer_shell.py`.

---

### [BUG-918] CRITICAL — `CloudDesktop.spawn()` Leaks `COMPUTER_CONTROL_TOKEN` and All Host Environment Variables to Unprivileged Desktop Apps & Browser
- **Category**: Security / Credential & Token Leak
- **File & Lines**: `D:\bubuki\proactive-personal-agent\server\computer_desktop.py:672-686`
- **Root Cause & Code Evidence**:
  ```python
  def spawn(self, args, cwd=None, extra_env=None):
      env = os.environ.copy()
      env.update(self._proxy_env())
      if extra_env:
          env.update(extra_env)
      child = subprocess.Popen(
          args,
          cwd=cwd or str(self.workspace),
          ...
          env=env,
      )
  ```
  `env = os.environ.copy()` copies the **entire environment of the `computer_desktop.py` service process**—including `COMPUTER_CONTROL_TOKEN` (and any API keys/secrets present in `os.environ`)—into every spawned child process (`openbox`, `pcmanfm`, `geany`, `gnumeric`, `lxterminal`, etc.)!
  And in `_ensure_live_terminal_window` / `launch_modern_browser`, any child inspecting `/proc/<pid>/environ` of `openbox` or any spawned app can read `COMPUTER_CONTROL_TOKEN` in plaintext!
- **Concrete Impact**: Any code running inside the cloud computer (or an untrusted script/extension in the desktop session) can read `COMPUTER_CONTROL_TOKEN` from `/proc/*/environ` and call `http://127.0.0.1:18193` directly.
- **Recommended Fix**: In `spawn()`, use `env = self._proxy_env()` directly (instead of `os.environ.copy()`), or explicitly `env.pop('COMPUTER_CONTROL_TOKEN', None)` before spawning any child process.


---
# 【分卷】Auditor-10 (安卓无障碍 PulseAccessibilityService / Root底层服务群) (`audit_10_accessibility_root.md`)

# Audit Report 10: Android Accessibility Service (`PulseAccessibilityService.kt`) & Root Underlying Services

## Part 1: `PulseAccessibilityService.kt` Audit Findings

### [BUG-10-01] `HIGH` — `CachedNode.toJson()` Drops `hint`, `checkable`, and `pkg`, Blinding JSON Consumers to Empty Input Fields
- **Category**: Schema Omission / Contract Mismatch
- **File & Lines**: `D:\bubuki\proactive-personal-agent\android\app\src\main\java\dev\bubuki\proactive\PulseAccessibilityService.kt:62-92`
- **Root Cause & Evidence**:
  `CachedNode` captures `hint` (line 67), `pkg` (line 69), and `checkable` (line 76), and `formatCompactSummary` uses `hint` as a fallback label (`listOf(n.text, n.desc, n.hint).firstOrNull { it.isNotBlank() }`, line 531) and `matchNodeInSnapshot` matches against `it.hint` (lines 386, 390, 395, 399). However, `CachedNode.toJson()` (lines 79-92) completely omits `hint`, `checkable`, and `pkg`:
  ```kotlin
  fun toJson(): JSONObject = JSONObject()
      .put("ref", ref)
      .put("role", role)
      .put("text", text)
      .put("desc", desc)
      .put("id", resourceId)
      .put("bounds", JSONArray().put(bounds.left).put(bounds.top).put(bounds.right).put(bounds.bottom))
      .put("center_x", centerX)
      .put("center_y", centerY)
      .put("clickable", clickable)
      .put("editable", editable)
      .put("scrollable", scrollable)
      .put("checked", checked)
  ```
- **Concrete Impact**:
  When an `EditText` search box or form field is empty, its label is almost always in `hintText` (`hint`), while `text` and `desc` are `""`. Any caller or agent inspecting the `nodes` JSON array returned by `dumpDisplayTree()` sees `"text": ""` and `"desc": ""` with no `"hint"` field, making multiple empty input fields indistinguishable in structured JSON. Similarly, `checkable` is lost in JSON.
- **Recommended Fix**:
  Add `.put("hint", hint)`, `.put("checkable", checkable)`, and `.put("pkg", pkg)` to `CachedNode.toJson()`.

---

### [BUG-10-02] `HIGH` — Aggressive Center-Proximity Deduplication Drops Interactive Children (`EditText`, `CheckBox`, Icon Buttons) Inside Centered Containers
- **Category**: UI Tree Traversal / Logic Defect
- **File & Lines**: `D:\bubuki\proactive-personal-agent\android\app\src\main\java\dev\bubuki\proactive\PulseAccessibilityService.kt:252-258`
- **Root Cause & Evidence**:
  During pre-order DFS (`traverse`, lines 204-289), parent nodes are visited and added to `collected` before their children. Deduplication checks:
  ```kotlin
  val duplicate = collected.any { prev ->
      kotlin.math.abs(prev.centerX - cx) <= 12 &&
          kotlin.math.abs(prev.centerY - cy) <= 12 &&
          (prev.text == effectiveText && prev.desc == ownDesc || (prev.text.isNotEmpty() && effectiveText.isEmpty()))
  }
  ```
  This causes three distinct false-positive drops:
  1. **Empty `EditText` with `hint` inside a centered `ScrollView` or container**: Both parent `prev` (e.g., `ScrollView` with `text=""`, `desc=""`) and child `EditText` (`text=""`, `desc=""`, `hint="请输入验证码"`) have `prev.text == effectiveText` (`"" == ""`) and `prev.desc == ownDesc` (`"" == ""`). Because `hint`, `role`, `editable`, and `clickable` are ignored, the `EditText` is dropped as a duplicate of the `ScrollView`.
  2. **Child with `ownDesc` (e.g., icon button / `CheckBox` / `Switch`) inside a text-labeled row**: If `prev` has `prev.text.isNotEmpty()` and the child has `effectiveText.isEmpty()` (because its label is in `ownDesc` or `hint`, or it is a `CheckBox`), the second branch `(prev.text.isNotEmpty() && effectiveText.isEmpty())` evaluates to `true` **without checking `ownDesc`, `hint`, `editable`, or `checkable`**, dropping the child.
  3. **Non-clickable parent wrapper shadowing clickable child**: Because traversal is pre-order, if a non-clickable parent `TextView`/wrapper has the same text and center as its clickable/editable child, the non-clickable parent is kept and the clickable child is discarded.
- **Concrete Impact**:
  Interactive controls (`EditText`, `CheckBox`, `Switch`, and `contentDescription`-labeled buttons) located near the center of their parent container are silently omitted from the accessibility snapshot, preventing the agent from referencing or acting on them.
- **Recommended Fix**:
  Include `hint`, `role`, `clickable`, `editable`, and `checkable` in the deduplication comparison, never let a node with `prev.text.isNotEmpty()` suppress a child that has its own non-empty `ownDesc`/`hint` or `editable`/`checkable` state, and if a clickable/editable child collides with a non-clickable ancestor at the same center, replace the non-clickable ancestor with the interactive child.

---

### [BUG-10-03] `MEDIUM` — `rect.intersect(screenRect)` Mutates Cached Node Bounds In-Place, Breaking `findLiveNode()` Coordinate Matching for Partially Off-Screen Nodes
- **Category**: Coordinate Mismatch / Action Execution Failure
- **File & Lines**: `D:\bubuki\proactive-personal-agent\android\app\src\main\java\dev\bubuki\proactive\PulseAccessibilityService.kt:208-210`, `250-251`, `465-479`
- **Root Cause & Evidence**:
  In `dumpDisplayTree` (lines 208-210):
  ```kotlin
  val rect = Rect()
  try { node.getBoundsInScreen(rect) } catch (t: Throwable) {}
  val intersects = rect.intersect(screenRect) && rect.width() >= 6 && rect.height() >= 6
  ```
  `Rect.intersect(screenRect)` mutates `rect` in-place to the clipped intersection with `screenRect`. `CachedNode` then stores the clipped `rect` as `bounds` and computes `centerX = rect.centerX()` and `centerY = rect.centerY()` from the clipped rectangle.
  However, in `findLiveNode` (lines 465-475):
  ```kotlin
  val r = Rect()
  try { node.getBoundsInScreen(r) } catch (t: Throwable) {}
  if (kotlin.math.abs(r.centerX() - target.centerX) <= 10 && kotlin.math.abs(r.centerY() - target.centerY) <= 10) {
      ...
      (node.isClickable == target.clickable && kotlin.math.abs(r.width() - target.bounds.width()) <= 16)
  ```
  `r` is NOT intersected with `screenRect`!
- **Concrete Impact**:
  For any node that extends more than 20px past the edge of the screen (e.g., a partially scrolled list item, a tall container, or a wide carousel card), its unclipped `r.centerX()` / `r.centerY()` / `r.width()` in `findLiveNode()` differs from the clipped `target.centerX` / `target.centerY` / `target.bounds.width()` by more than 10–16px. `findLiveNode()` fails to match the node, causing `performAccessibilityAction()` to return `false`.
- **Recommended Fix**:
  Either intersect `r` with the same `screenRect` inside `findLiveNode()`, or store both raw and clipped bounds on `CachedNode` and match against raw bounds in `findLiveNode()`.

---

### [BUG-10-04] `MEDIUM` — `computeTreeHash()` Ignores `checked`, `hint`, and `editable`/`clickable` State Changes
- **Category**: State Change Detection Defect
- **File & Lines**: `D:\bubuki\proactive-personal-agent\android\app\src\main\java\dev\bubuki\proactive\PulseAccessibilityService.kt:505-517`
- **Root Cause & Evidence**:
  ```kotlin
  internal fun computeTreeHash(nodes: List<CachedNode>, activePkg: String): String {
      ...
      feed(activePkg)
      for (n in nodes) {
          feed(n.role); feed(n.text); feed(n.desc); feed("${n.centerX},${n.centerY}")
      }
      return Integer.toHexString(h)
  }
  ```
  `computeTreeHash` does not feed `n.checked`, `n.hint`, `n.resourceId`, or `n.clickable`.
- **Concrete Impact**:
  Toggling a `CheckBox` or `Switch` (where `checked` flips between `true` and `false` while `text`, `desc`, and coordinates remain unchanged) produces the exact same `treeHash`. Callers relying on `treeHash` to verify whether a click action took effect will falsely detect a no-op / stuck UI.
- **Recommended Fix**:
  Include `n.checked`, `n.hint`, `n.resourceId`, and `n.clickable` in `computeTreeHash()`.

---

### [BUG-10-05] `MEDIUM` — `findLiveNode()` Performs Unbounded Synchronous IPC DFS Without a Visited-Node Cap, and Special-Cases `com.tencent.mm` to Skip Transition Waits
- **Category**: Performance / UI Freeze & Race Condition
- **File & Lines**: `D:\bubuki\proactive-personal-agent\android\app\src\main\java\dev\bubuki\proactive\PulseAccessibilityService.kt:294-302`, `463-487`
- **Root Cause & Evidence**:
  1. In `dumpDisplayTree` (line 205), `traverse` limits IPC traversal with `++visitedNodes > 650`. In contrast, `findLiveNode` (lines 463-487) recurses up to `depth > 48` across all windows with **no `visitedNodes` cap**, executing synchronous Binder IPC (`getChild`, `getBoundsInScreen`, `text`, `contentDescription`, `viewIdResourceName`) on every node in large trees.
  2. In `dumpDisplayTree` (lines 294-302):
     ```kotlin
     if (collected. isNotEmpty() || activePkg == "com.tencent.mm" || SystemClock.uptimeMillis() >= deadline) {
         return snapshotToJson(snap, fromCache = false)
     }
     ```
     When `activePkg == "com.tencent.mm"` and `collected` is empty during an Activity/window transition, `dumpDisplayTree` immediately returns the empty snapshot on the first loop iteration (`0ms` waited) instead of waiting up to `waitMs` (`420ms`) for the window tree to populate.
- **Concrete Impact**:
  1. Calling `performAccessibilityAction` on a heavy page where the target node moved or disappeared can block the calling thread with thousands of Binder IPC calls.
  2. On WeChat (`com.tencent.mm`), calling `dumpDisplayTree` during a screen transition immediately returns `nodeCount = 0` without retrying.
- **Recommended Fix**:
  Add a `visitedNodes` counter cap (e.g., 650) to `findLiveNode()`, and allow `com.tencent.mm` to retry until `deadline` when `collected.isEmpty()`.

---
### [BUG-10-06] `HIGH` — `PulseAccessibilityService` Hardcodes Filtering Out `"dev.bubuki.proactive"`, Breaking `DisplayTestActivity` on the Virtual Display and Ignoring `isLongClickable` / `hint` in `performAccessibilityAction()`
- **Category**: Multi-Display Window Filtering / Action Execution Defect
- **File & Lines**:
  - `D:\bubuki\proactive-personal-agent\android\app\src\main\java\dev\bubuki\proactive\PulseAccessibilityService.kt:195-197`, `216`, `419`, `437`, `463-479`
  - `D:\bubuki\proactive-personal-agent\android\app\src\main\java\dev\bubuki\proactive\DisplayTestActivity.java:7-15`
- **Root Cause & Evidence**:
  1. Both `dumpDisplayTree` (line 197) and `performAccessibilityAction` (line 419) skip any window whose root package is `"dev.bubuki.proactive"`:
     ```kotlin
     // Never read our own host app UI on the virtual display
     if (winPkg == "dev.bubuki.proactive") continue
     ```
     However, `dumpDisplayTree` and `performAccessibilityAction` already strictly filter `win.displayId == displayId` (`displayId > 0`), and `DisplayTestActivity` (`dev.bubuki.proactive/.DisplayTestActivity`, launched on the virtual display in `RootDisplayServer.java:35`) belongs to package `"dev.bubuki.proactive"`. As a result, `DisplayTestActivity` is completely invisible to `PulseAccessibilityService`.
  2. In `dumpDisplayTree` (line 216), `clickable` is computed as:
     ```kotlin
     val clickable = node.isClickable || node.isLongClickable || node.isContextClickable
     ```
     Yet in `performAccessibilityAction` (line 437), it only checks `current.isClickable`:
     ```kotlin
     if (current.isClickable && current.isEnabled && current.performAction(AccessibilityNodeInfo.ACTION_CLICK))
     ```
     Nodes that are only `isLongClickable` or `isContextClickable` are marked `clickable = true` in the tree, but `performAccessibilityAction` refuses to act on them and instead walks up to `current.parent`.
  3. In `findLiveNode` (lines 471-478):
     ```kotlin
     if (
         (target.resourceId.isNotEmpty() && resId == target.resourceId) ||
         (txt.isNotEmpty() && txt == target.text) ||
         (desc.isNotEmpty() && desc == target.desc) ||
         (node.isClickable == target.clickable && kotlin.math.abs(r.width() - target.bounds.width()) <= 16)
     ) {
         return node
     }
     ```
     `findLiveNode` does not check `hintText == target.hint` or `node.isEditable == target.editable`. Because `findLiveNode` performs pre-order DFS (parent visited before child), when `target` is an empty `EditText` (matched via `hint`, with `text=""`, `desc=""`, `resourceId=""`), any parent wrapper (`FrameLayout` / `TextInputLayout`) with the same center, width (`<= 16px`), and `isClickable` flag matches line 475 first and is returned instead of the child `EditText`. Calling `ACTION_SET_TEXT` on that parent wrapper then fails (`returns false`).
- **Concrete Impact**:
  - `DisplayTestActivity` cannot be inspected or interacted with via `PulseAccessibilityService`.
  - `phone_type_node` on empty `EditText` inputs wrapped in same-width containers fails to set text via accessibility because `findLiveNode` returns the non-editable parent container instead of the `EditText`.
- **Recommended Fix**:
  1. Allow `DisplayTestActivity` (or only filter out `MainActivity`/`NativePulseActivity` rather than the entire `dev.bubuki.proactive` package on `displayId > 0`).
  2. In `findLiveNode()`, require `node.isEditable == target.editable` when `target.editable` is true, and check `node.hintText?.toString()?.trim() == target.hint`.

---

## Part 2: `RootPhoneScreen.kt` Audit Findings

### [BUG-10-07] `HIGH` — Unguarded `JSONObject(state.display)` and `JSONArray(state.trace)` in `RootPhoneScreen` Can Crash the Main UI Thread With `JSONException`
- **Category**: UI Crash / Unhandled Exception
- **File & Lines**: `D:\bubuki\proactive-personal-agent\android\app\src\main\java\dev\bubuki\proactive\RootPhoneScreen.kt:40`, `167`
- **Root Cause & Evidence**:
  - At line 40:
    ```kotlin
    val display = remember(state.display) { JSONObject(state.display) }
    ```
    Unlike line 101 (`runCatching { JSONObject(state.imageInfo.ifBlank { state.display }) }.getOrDefault(JSONObject())`), line 40 calls `JSONObject(state.display)` without `runCatching`. If `state.display` is blank (`""`) or malformed, `JSONObject("")` throws `JSONException` during composition.
  - At line 167:
    ```kotlin
    Text("已记录 ${org.json.JSONArray(state.trace).length()} 个工具动作 · 完整记录在对话中", color = colors.ink2, fontSize = 11.sp)
    ```
    `org.json.JSONArray(state.trace)` is parsed on every recomposition without `remember` or `runCatching`. If `state.trace` is blank (`""`), `JSONArray("")` throws `JSONException` and crashes the app whenever `state.active || state.result.isNotBlank()` is true.
- **Concrete Impact**:
  Any state initialization or reset where `state.display` or `state.trace` is `""` immediately crashes `NativePulseActivity` / `MainActivity` with an uncaught `org.json.JSONException`.
- **Recommended Fix**:
  Wrap both `JSONObject(state.display)` and `JSONArray(state.trace)` in `runCatching { ... }.getOrDefault(...)` inside `remember(...)`.

---

### [BUG-10-08] `HIGH` — `pointerInput(manual, state.imageInfo)` Cancels Active Touch Gesture Whenever `state.imageInfo` Updates, Leaving Finger Stuck Down (`"down"` Without `"up"`) on the Virtual Display
- **Category**: Concurrency / Gesture State Machine Leak
- **File & Lines**: `D:\bubuki\proactive-personal-agent\android\app\src\main\java\dev\bubuki\proactive\RootPhoneScreen.kt:125-156`
- **Root Cause & Evidence**:
  ```kotlin
  else Image(bitmap, "独立工作区实时画面", Modifier.size(w, h).clip(RoundedCornerShape(9.dp)).pointerInput(manual, state.imageInfo) {
      if (!manual) return@pointerInput
      awaitEachGesture {
          val down = awaitFirstDown(requireUnconsumed = false)
          ...
          RootPhoneHarness.sendLiveTouch(activity, "down", x(down.position.x), y(down.position.y))
          ...
          do {
              val event = awaitPointerEvent()
              ...
          } while (true)
          RootPhoneHarness.sendLiveTouch(activity, "up", x(end.x), y(end.y))
      }
  })
  ```
  1. `state.imageInfo` is passed as a key to `Modifier.pointerInput(manual, state.imageInfo)`. In Jetpack Compose, whenever any key to `pointerInput` changes, Compose immediately cancels and restarts the `pointerInput` coroutine.
  2. When the user touches the preview (`sendLiveTouch(..., "down", ...)`), the screen updates or captures a new frame, changing `state.imageInfo` (or `manual` flips to `false`, or the parent `verticalScroll` at line 51 intercepts the vertical drag).
  3. `awaitPointerEvent()` throws `CancellationException`, exiting the coroutine **before** line 154 (`RootPhoneHarness.sendLiveTouch(activity, "up", ...)`). Because there is no `try { ... } finally { ... }` block, the `"up"` touch event is never sent.
  4. Furthermore, `down.consume()` is never called after `awaitFirstDown(requireUnconsumed = false)` (line 128), allowing the outer `Column(...verticalScroll(rememberScrollState()))` (line 51) to steal vertical swipe gestures intended for the virtual display.
- **Concrete Impact**:
  Touching or dragging on the virtual display preview in `RootPhoneScreen` while frames refresh or scrolling vertically causes the parent page to scroll and/or cancels the gesture coroutine mid-press, leaving a phantom finger held down on the virtual display (triggering unintended long-press menus or blocking subsequent touches).
- **Recommended Fix**:
  1. Do not key `pointerInput` on `state.imageInfo`; instead use `rememberUpdatedState(width)` and `rememberUpdatedState(height)`.
  2. Consume the initial `down` change (`down.consume()`) so the parent `verticalScroll` does not intercept vertical swipes on the virtual screen.
  3. Wrap the gesture loop after sending `"down"` in a `try { ... } finally { ... }` block so `"up"` is guaranteed to be dispatched even on coroutine cancellation.

---

## Part 3: `PrivilegeManager.java` Audit Findings

### [BUG-10-09] `CRITICAL` — `PrivilegeManager.readRpcLine()` and `execute()` Do Not Handle `InterruptedException`, Orphaning Blocked Reader Threads on `rootIn` and Corrupting Subsequent RPC Calls
- **Category**: Concurrency / Pipe Stream Corruption & Deadlock
- **File & Lines**: `D:\bubuki\proactive-personal-agent\android\app\src\main\java\dev\bubuki\proactive\PrivilegeManager.java:180-203`, `243-266`
- **Root Cause & Evidence**:
  In `readRpcLine(long timeoutMs)` (lines 243-266):
  ```java
  Future<String> future = ioPool.submit(() -> {
      String line;
      while ((line = reader.readLine()) != null) {
          int idx = line.indexOf(RPC_PREFIX);
          if (idx >= 0) {
              return line.substring(idx + RPC_PREFIX.length()).trim();
          }
      }
      return null;
  });
  try {
      return future.get(timeoutMs, TimeUnit.MILLISECONDS);
  } catch (TimeoutException e) {
      future.cancel(true);
      throw new IOException("等待 Root 工具响应超时");
  } catch (ExecutionException e) { ... }
  ```
  And in `execute(JSONObject command)` (lines 186-201):
  ```java
  try {
      ensureRoot();
      rootOut.write(command.toString());
      rootOut.newLine();
      rootOut.flush();
      String line = readRpcLine(20000);
      ...
  } catch (IOException e) {
      closeRoot();
      lastErr = e;
  }
  ```
  1. When a Kotlin coroutine calling `PrivilegeManager.execute()` is cancelled (e.g., user clicks "停止" -> `RootPhoneHarness.stop()`), the thread blocked in `future.get(...)` throws `InterruptedException`.
  2. `readRpcLine` only catches `TimeoutException` and `ExecutionException`—it does **not** catch `InterruptedException`, so `future.cancel(true)` is never called.
  3. `execute()` only catches `IOException`—it does **not** catch `InterruptedException`, so `closeRoot()` is never called!
  4. The `ioPool` task remains blocked inside `rootIn.readLine()`. When the next `execute()` call runs, `ensureRoot()` sees `rootProcess.isAlive()` and reuses the same `rootIn`, submitting a **second** `ioPool` task reading from the same `BufferedReader`. The orphaned first task steals the RPC response meant for the second task (or corrupts `BufferedReader`'s internal buffer under concurrent access).
  5. Additionally, even on `TimeoutException`, `future.cancel(true)` cannot interrupt a native `FileInputStream.readBytes` blocked on an anonymous pipe until `closeRoot()` closes `rootIn` and destroys `rootProcess`.
- **Concrete Impact**:
  Cancelling a running phone task or interrupting an RPC call leaves an orphaned reader thread on `rootIn`, causing the next RPC command to have its response stolen, return out-of-order data from the previous command, or hang for 20 seconds.
- **Recommended Fix**:
  Catch `InterruptedException` in `readRpcLine()`, call `future.cancel(true)`, restore `Thread.currentThread().interrupt()`, and in `execute()` call `closeRoot()` on `InterruptedException` (or any `Throwable` that leaves the request/response framing out of sync).

---

### [BUG-10-10] `HIGH` — Stale `rootGrantedHint` Persists Forever Across Root Revocation and Skips `autoGrantPermissionsViaRoot` on App Restart
- **Category**: Privilege State Management / Permission Check Flaw
- **File & Lines**: `D:\bubuki\proactive-personal-agent\android\app\src\main\java\dev\bubuki\proactive\PrivilegeManager.java:33-42`, `61-100`, `180-203`
- **Root Cause & Evidence**:
  1. In the constructor (lines 36-41):
     ```java
     JSONObject saved = SecurePrefs.read(this.context);
     if (saved.optBoolean("rootGrantedHint", false)) {
         this.rootGranted = true;
         this.suMode = saved.optInt("suModeHint", 0);
     }
     ```
     `rootGranted` is immediately set to `true` from cached prefs without probing `su`. `status()` then reports `"root": true`.
  2. If the user later revokes Root permission in Magisk / KernelSU / APatch (or changes Root manager so `suMode` changes), `execute()` fails in `ensureRoot()` ("Root 工具进程握手未完成"), throws `IOException`, and **never resets `rootGranted = false` or `persistHint(false, 0)`**!
  3. Because `state.root` remains `true`, `RootPhoneScreen.kt:61` (`if (!state.root)`) hides the `"连接 Root"` button permanently, leaving the user unable to re-trigger `probe()`.
  4. Moreover, in `probe()` (lines 70-89), `process.waitFor(25, TimeUnit.SECONDS)` is called sequentially across 5 modes (`0..4`) while holding the `synchronized` lock on `PrivilegeManager` and **before** draining stdout/stderr pipes, which can block all callers for up to 125 seconds if `su` hangs.
- **Concrete Impact**:
  If Root access is revoked or `suMode` changes after initial setup, the app remains permanently stuck claiming `"Root 已连接 · uid 0"` while every workspace action fails with handshake errors and the `"连接 Root"` button is hidden.
- **Recommended Fix**:
  When `ensureRoot()` fails across both retry attempts in `execute()`, reset `rootGranted = false` and `persistHint(false, 0)` (or re-run `probe()` before giving up).

---

## Part 4: `RootPhoneAgent.kt` Audit Findings

### [BUG-10-11] `HIGH` — `phone_tap_node` and `phone_type_node` Tool Schemas Omit `display_id`, Allow Empty Calls, and Use `target` Instead of `text`/`id`
- **Category**: Tool Schema / Contract Mismatch
- **File & Lines**: `D:\bubuki\proactive-personal-agent\android\app\src\main\java\dev\bubuki\proactive\RootPhoneAgent.kt:96-115`
- **Root Cause & Evidence**:
  ```kotlin
  definition("phone_tap_node", "【首选】根据 phone_ui_tree 返回的节点编号 ref（如 '@e3'）或可见文字 text（如 '作业'）精准点击控件", mapOf("ref" to field("string", "节点编号，如 '@e1'"), "text" to field("string", "控件上的文字或描述，如 '签到' / '作业'"), "id" to field("string", "可选 resource_id")), emptyList()),
  definition("phone_type_node", "【首选】在指定输入框节点（ref 如 '@e2' 或 target 提示词）中输入文字", mapOf("ref" to field("string", "输入框节点编号，如 '@e2'"), "target" to field("string", "可选输入框提示文字"), "text" to field("string", "要输入的文字")), listOf("text")),
  ```
  1. Every other workspace tool (`phone_launch`, `phone_ui_tree`, `phone_swipe`, `phone_key`, `phone_capture`, `phone_tap`, `phone_text`) declares `"display_id"` in `properties`, and `definition()` sets `"additionalProperties": false` (line 98). However, `phone_tap_node` and `phone_type_node` omit `"display_id"`. If the model includes `display_id` (as instructed by `当前工作区：$workspace`), strict schema validators reject the tool call; if it omits `display_id`, the executor receives no `display_id` parameter.
  2. `phone_type_node` names the node-matching text parameter `"target"` (because `"text"` is the text to type) and omits `"id"` (`resource_id`), whereas `phone_tap_node` uses `"ref"`, `"text"`, `"id"`.
- **Concrete Impact**:
  Strict OpenAI-compatible schema enforcement rejects `phone_tap_node` / `phone_type_node` calls when the LLM includes `display_id`, or `phone_type_node` fails to locate an input box by `id`.
- **Recommended Fix**:
  Add `"display_id" to display` to both `phone_tap_node` and `phone_type_node`, and add `"id" to field("string", "可选 resource_id")` to `phone_type_node`.

---

### [BUG-10-12] `MEDIUM` — Unbounded Accumulation of Base64 Screenshots in `RootPhoneAgent` Conversation Context Across 24 Steps
- **Category**: Memory / Payload Bloat & API Failure
- **File & Lines**: `D:\bubuki\proactive-personal-agent\android\app\src\main\java\dev\bubuki\proactive\RootPhoneAgent.kt:45-90`
- **Root Cause & Evidence**:
  Whenever `phone_capture` (or any tool returning `"image"`) runs inside the `repeat(24)` loop (lines 84-89):
  ```kotlin
  if (image.isNotBlank()) {
      images.put(JSONObject().put("type", "text").put("text", "刚刚获取的工作区截图..."))
      images.put(JSONObject().put("type", "image_url").put("image_url", JSONObject().put("url", "data:image/png;base64,$image")))
  }
  if (images.length() > 0) messages.put(JSONObject().put("role", "user").put("content", images))
  ```
  Previous base64 screenshots in `messages` are never evicted or replaced when newer screenshots are captured. Additionally, line 55 (`JSONObject(...).getJSONArray("choices").getJSONObject(0).getJSONObject("message")`) does not check for empty `choices` or gateway error payloads before indexing `choices[0]`.
- **Concrete Impact**:
  In fallback visual sessions with multiple `phone_capture` calls, `messages` accumulates multiple full-resolution base64 PNGs, causing multi-megabyte HTTP POST bodies, OOM, or HTTP 413 / 400 context-length errors from the model provider.
- **Recommended Fix**:
  Evict or replace `image_url` blocks from older turns in `messages` so only the latest 1–2 screenshots are sent to the vision model.

---

## Part 5: `OverlayController.java` Audit Findings

### [BUG-10-13] `MEDIUM` — Uncancelled `dim` `ValueAnimator` and Fade-Out State Race in `OverlayController`
- **Category**: UI Animation Race / WindowManager Leak
- **File & Lines**: `D:\bubuki\proactive-personal-agent\android\app\src\main\java\dev\bubuki\proactive\OverlayController.java:18-44`, `55-61`
- **Root Cause & Evidence**:
  1. In `show(String id, String message)` (line 41):
     ```java
     android.animation.ValueAnimator dim=android.animation.ValueAnimator.ofFloat(0,.65f);dim.setDuration(280);dim.addUpdateListener(a->{if(view==card){p.dimAmount=(float)a.getAnimatedValue();try{manager.updateViewLayout(card,p);}catch(Exception ignored){}}});dim.start();
     ```
     `dim` is a standalone `ValueAnimator` that is never saved in a field and never cancelled in `hide()` or `hideAnimated()`. When `hideAnimated()` runs within 280ms of `show()`, `view == card` remains `true` for the 180ms fade-out, so `dim` keeps increasing `p.dimAmount` up to `0.65f` while the card is fading out.
  2. During the 180ms fade-out in `hideAnimated()` (lines 55-59), `current` and `view` are not cleared until `withEndAction` runs. If `show(id, message)` is called with the same `id` during that 180ms window, line 19 (`if (... || id.equals(current) && view != null) return;`) returns early, and milliseconds later `withEndAction` calls `hide()`, leaving no overlay visible.
- **Concrete Impact**:
  Rapidly re-triggering the same overlay ID while it is animating out silently drops the new overlay, and the uncancelled `dim` animator fights `hideAnimated()`.
- **Recommended Fix**:
  Store `dim` as a field and cancel it in `hide()` / `hideAnimated()`, and mark a `hiding` flag or clear `current` at the start of `hideAnimated()`.

---

## Part 6: `RootDisplayServer.java`, `RootPhoneService.kt`, `RootClipboardBroker.java`, `RootClipboard.java`, `RootCommands.java` Audit Findings

### [BUG-10-14] `CRITICAL` — `RootClipboardBroker` Calls `IClipboard.getMethod()` and `read(getter, service)` Outside `try/catch`, Crashing Before Handshake When `getPrimaryClip` Signature Differs or Shell Lacks Clipboard Read Access
- **Category**: Root Clipboard Crash / Uncaught Exception
- **File & Lines**:
  - `D:\bubuki\proactive-personal-agent\android\app\src\main\java\dev\bubuki\proactive\RootClipboardBroker.java:30-46`
  - `D:\bubuki\proactive-personal-agent\android\app\src\main\java\dev\bubuki\proactive\RootClipboard.java:18-29`
- **Root Cause & Evidence**:
  In `RootClipboardBroker.java` (lines 30-43):
  ```java
  Class<?> type=Class.forName("android.content.IClipboard");Method getter;
  try{getter=type.getMethod("getPrimaryClip",String.class,String.class,int.class,int.class);}catch(NoSuchMethodException error){getter=type.getMethod("getPrimaryClip",String.class,String.class,int.class);}
  BufferedReader in=new BufferedReader(new InputStreamReader(System.in,StandardCharsets.UTF_8));PrintWriter out=new PrintWriter(new OutputStreamWriter(System.out,StandardCharsets.UTF_8),true);
  ClipData previous=read(getter,service);String label="pulse-input-"+UUID.randomUUID();boolean changed=false;
  try {
      String request=in.readLine();if(request==null)return;String text=new JSONObject(request).getString("text");
      ...
      manager.setPrimaryClip(pending);changed=true;out.println("{\"ready\":true}");
      in.readLine();
  }catch(Exception error){out.println(new JSONObject().put("error",error.getClass().getSimpleName()+": "+error.getMessage()));}
  finally{
      if(changed){ClipData current=read(getter,service);if(current!=null&&label.contentEquals(current.getDescription().getLabel())){if(previous==null)manager.clearPrimaryClip();else manager.setPrimaryClip(previous);}}
      out.println("{\"restored\":true}");
  }
  ```
  There are three critical defects here:
  1. **Uncaught reflection & initial clipboard read before `try` block**:
     - Lines 30-31 only probe 4-arg `(String, String, int, int)` and 3-arg `(String, String, int)` overloads of `IClipboard.getPrimaryClip`. On Android versions or OEM ROMs (e.g., Xiaomi HyperOS / Samsung OneUI / Android 14-15 QPR variants) where `IClipboard.getPrimaryClip` has a 5-arg or 2-arg signature (such as adding `int deviceId` + `String` or `AttributionSource`), line 31 throws `NoSuchMethodException` **outside** the `try` block.
     - Line 33 calls `ClipData previous = read(getter, service);` **outside** the `try` block. On Android 12–15, `ClipboardService.clipboardAccessAllowed()` denies `getPrimaryClip` to `com.android.shell` (`uid 2000`) when shell is not the focused top app or default IME, either returning `null` or throwing `SecurityException`. Because line 33 is outside `try`, any exception crashes `RootClipboardBroker` before it ever reads `in.readLine()` or prints `{"ready":true}`!
  2. **Uncaught exception in `finally` skips `{"restored":true}`**:
     - In `finally` (line 41), `read(getter, service)` and `manager.clearPrimaryClip()` / `manager.setPrimaryClip(previous)` are not wrapped in `try/catch`. If restoring the clipboard throws (e.g. `clearPrimaryClip` hidden API restriction or `SecurityException`), line 42 (`out.println("{\"restored\":true}");`) is skipped, causing `RootClipboard.paste()` line 27 (`line(in)`) to fail with `"剪贴板会话已断开"` even though the paste already succeeded!
  3. **Inner `finally` in `RootClipboard.paste()` masks `RootCommands.run` exceptions**:
     - In `RootClipboard.java` (lines 23-27):
       ```java
       try{
           RootCommands.run("input","-d",String.valueOf(displayId),"keyevent","279");
           try{Thread.sleep(240L);}catch(InterruptedException ignored){}
       }
       finally{out.write("restore\n");out.flush();line(in);}
       ```
       If `broker` died early, `out.write("restore\n")` or `line(in)` in `finally` throws an `IOException` that overwrites any exception from `RootCommands.run`.
- **Concrete Impact**:
  Whenever `getPrimaryClip` is denied to background `uid 2000` or has a different AIDL signature on the device ROM, `RootClipboardBroker` crashes on startup (with stderr redirected to `/dev/null`), completely breaking `RootClipboard.paste()` (`phone_text`) even though `setPrimaryClip` would have worked without reading `previous`.
- **Recommended Fix**:
  1. Make `getter` lookup and `previous = read(getter, service)` best-effort inside their own `try { ... } catch (Throwable ignored) { previous = null; }` so a failure to read the old clipboard never prevents setting the new clip and pasting.
  2. Wrap the clipboard restore logic inside `finally` (line 41) in `try { ... } catch (Throwable ignored) {}` so `out.println("{\"restored\":true}")` is always printed.

---

### [BUG-10-15] `HIGH` — `RootPhoneService.kt` Keeps Ongoing `"手机工作区正在执行"` Foreground Notification Stuck After Task Completion or Failure
- **Category**: Service Lifecycle / Notification State Stuck
- **File & Lines**: `D:\bubuki\proactive-personal-agent\android\app\src\main\java\dev\bubuki\proactive\RootPhoneService.kt:20-25`, `37-47`
- **Root Cause & Evidence**:
  ```kotlin
  scope.launch { RootPhoneHarness.changes.collect { state ->
      runCatching { notifications.notify(18192, notification()) }
      if (state.phase == "running" && awake?.isHeld != true) {
          awake = getSystemService(PowerManager::class.java).newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "$packageName:phone-task").apply { acquire(45 * 60 * 1000L) }
      } else if (state.phase != "running") { if (awake?.isHeld == true) awake?.release(); awake = null }
  } }
  ```
  And in `notification()` (line 42):
  ```kotlin
  .setContentTitle(if (state.phase == "waiting_user") "手机工作区需要你处理" else "手机工作区正在执行")
  ```
  1. When `state.active` transitions to `false` (e.g., `phase` becomes `"done"`, `"failed"`, or `"stopped"`), `RootPhoneService`'s collector in `onCreate` calls `notifications.notify(18192, notification())` **before** or without stopping itself, re-posting an `ongoing = true` notification with the misleading title `"手机工作区正在执行"`.
  2. `RootPhoneService` never calls `stopForeground(STOP_FOREGROUND_REMOVE)` or `stopSelf()` when `!state.active && state.phase != "running" && state.phase != "waiting_user"`.
- **Concrete Impact**:
  If `RootPhoneService` is not externally stopped or if `RootPhoneHarness.changes` emits a non-active state before `onDestroy` completes, the `notifications.notify(18192, notification())` call re-posts a persistent notification claiming `"手机工作区正在执行"` after the task has already stopped.
- **Recommended Fix**:
  In `RootPhoneService`'s `changes.collect` block, when `!state.active`, call `stopForeground(STOP_FOREGROUND_REMOVE)` and `stopSelf()` instead of calling `notifications.notify(18192, notification())`.

---

### [BUG-10-16] `MEDIUM` — `RootCommands.run()` Blocks Indefinitely After 8-Second Timeout If Any Grandchild Process Inherits `stdout`
- **Category**: Process Management / Pipe Hang
- **File & Lines**: `D:\bubuki\proactive-personal-agent\android\app\src\main\java\dev\bubuki\proactive\RootCommands.java:11-19`
- **Root Cause & Evidence**:
  ```java
  java.lang.Process process=new ProcessBuilder(args).redirectErrorStream(true).start();AtomicBoolean timedOut=new AtomicBoolean();
  ScheduledFuture<?> deadline=TIMER.schedule(()->{timedOut.set(true);process.destroyForcibly();},8,TimeUnit.SECONDS);
  try(InputStream input=process.getInputStream()){
      String output=new String(Io.read(input,1000000),StandardCharsets.UTF_8);process.waitFor();
      if(timedOut.get())throw new Exception("Root 系统命令超时");
  ```
  When the 8-second timer fires, it calls `process.destroyForcibly()`, which sends `SIGKILL` only to the direct child PID (`process`), and does **not** close `process.getInputStream()`. If the command (e.g., `am start` or `input` or a shell wrapper) spawned a child process that inherited the stdout pipe descriptor, `Io.read(input, 1000000)` never sees EOF and blocks forever on `RootDisplayServer`'s single RPC thread (`"pulse-root-rpc"`).
- **Concrete Impact**:
  A single hung sub-command inside `RootCommands.run()` permanently blocks the single-threaded `RootDisplayServer` RPC loop even after the 8-second timeout expires.
- **Recommended Fix**:
  In the timeout callback, in addition to `process.destroyForcibly()`, close `process.getInputStream()` (`try { process.getInputStream().close(); } catch (IOException ignored) {}`) to unblock `Io.read()`.


---

# Auditor-11 持续深度猎虫与回归审查报告 (`Continuous-Bug-Hunter`)

> 审查范围：`server/core/`、`server/transport/`、`server/plugins/`、`desktop/`、`scripts/`、`android/app/src/main/AndroidManifest.xml` & `build.gradle`、`server/tests/`

---

## 一、`server/core/` 深度审查发现

### BUG-11-01 [HIGH] `MemoryEngine.estimate_messages_tokens` 访问不存在的 `self.llm` 属性导致必现 `AttributeError`
- **类别**: Attribute Omission / Runtime Crash
- **文件与行号**: `D:\bubuki\proactive-personal-agent\server\core\memory_engine.py:266-267` (对比 `__init__` 第 `26-40` 行)
- **根因与代码证据**:
  ```python
  def estimate_messages_tokens(self, messages: list[dict]) -> int:
      return self.llm._estimate_payload_tokens(messages)
  ```
  `MemoryEngine.__init__` 仅初始化了 `self.store, self.vault, self.harness, self.last_prompt_tokens, self.last_cached_tokens, self.context_search`，从未绑定 `self.llm`。
- **实际影响**: 任何调用 `memory_engine.estimate_messages_tokens(messages)` 的路径均直接抛出 `AttributeError: 'MemoryEngine' object has no attribute 'llm'`。
- **修复建议**: 改为使用 `LLMClient(self.store)._estimate_payload_tokens(messages)` 或将 `_estimate_payload_tokens` 提取为 `@classmethod` / `@staticmethod` 直接调用 `LLMClient._estimate_payload_tokens(messages)`。

---

### BUG-11-02 [CRITICAL] `sleep_time_consolidate` 在 LLM 返回非 JSON 或带标点 `NONE` 时未更新 `last_sleep_consolidate`，引发后台死循环请求风暴
- **类别**: State Machine Loop / Token Burn
- **文件与行号**: `D:\bubuki\proactive-personal-agent\server\core\memory_engine.py:645-656, 711-714`
- **根因与代码证据**:
  ```python
  res_msg = await silent_complete_fn([{'role': 'user', 'content': prompt}])
  raw = (res_msg.get('content') or '').strip()
  if not raw:
      return
  if raw.upper() == 'NONE':
      s['last_sleep_consolidate'] = time.time()
      self.store.save()
      return
  m_json = re.search(r'\{.*\}', raw, flags=re.DOTALL)
  if not m_json:
      return
  data = json.loads(m_json.group(0))
  ```
  当模型返回 `"NONE."`、`"无需整理 (NONE)"`、无 `{...}` 的文本，或 `json.loads` 抛出 `JSONDecodeError`（被第 713 行 `except Exception: pass` 吞掉）时，`s['last_sleep_consolidate']` 永远不会被更新。
- **实际影响**: `last_user > last_consolidated` 与 `(time.time() - last_user) >= 45` 持续为真，后台循环在用户不发新消息期间每个 tick 无限重复调用 LLM 进行静默记忆整理，造成严重 Token 消耗与网关限流。
- **修复建议**: 只要成功向 LLM 发起过该轮 `new_msgs` 的整理尝试（无论返回格式是否匹配 JSON 或解析异常，除非是网络/网关临时异常需有限退避），都应推进 `s['last_sleep_consolidate'] = time.time()`，并放宽 `NONE` 判定（如 `raw.upper().strip('.!`\'" ') == 'NONE'`）。

---

### BUG-11-03 [HIGH] `_on_vault_mutation` 未过滤高频日志/索引文件且 `_suppress_vault_listener` 从未生效，导致手机通知与压缩日志污染 `memory_deltas` 及跨 Bot 误清空
- **类别**: Context Pollution / Cross-Bot State Loss
- **文件与行号**: `D:\bubuki\proactive-personal-agent\server\core\memory_engine.py:42, 66-81, 249-255, 546-548, 598, 659-710`；`server\core\notification_hub.py:340`
- **根因与代码证据**:
  1. `_suppress_vault_listener` 在第 42 行初始化为 `False`，但全库没有任何地方将其置为 `True`。
  2. `_on_vault_mutation` 监听所有 `vault.write` / `vault.append`，未过滤 `INDEX.md`、`logs/notifications_inbox.md`、`config/notification_filters.json`、`notes/compact_*.md`。每当手机收到任何通知（`NotificationHub.ingest_raw_notifications` 调用 `self.vault.append('logs/notifications_inbox.md', ...)`），都会触发 `_on_vault_mutation` 写入一条 `memory_deltas` 并同步落盘 `store.save()`！
  3. `sleep_time_consolidate` 内部写入多个文件时重复触发 `_on_vault_mutation`，随后第 710 行再次手动调用 `self.record_memory_delta`，造成同一批变更被重复注入。
  4. `auto_compact_if_needed`（第 598 行）在单个 `(bot_id, source)` 压缩后直接调用 `self.clear_memory_deltas()` 清空全局 `store.state['memory_deltas']`，导致其他 Bot 或另一端（PC/手机）尚未消费的记忆增量全部丢失。
- **实际影响**: 手机通知与自动压缩操作不断向所有对话尾部注入冗余 `[System Memory Delta]`，且任一房间压缩会抹掉其他房间的未消费记忆同步。
- **修复建议**:
  1. 在 `_on_vault_mutation` 中忽略 `INDEX.md`、`logs/`、`config/`、`timeline/`、`notes/compact_` 等非核心档案路径；
  2. 在 `sleep_time_consolidate` 和 `auto_compact_if_needed` 写 vault 期间使用 `self._suppress_vault_listener = True`；
  3. `clear_memory_deltas(bot_id, source)` 仅清理当前 `f"{bot_id}:{src}"` 的锚点状态，而非直接清空全局列表。

---

### BUG-11-04 [MEDIUM] `consume_tail_memory_delta` 与 `inject_memory_deltas` 锚点逻辑不一致，且新激活的 Bot 会被一次性注入全部历史 `memory_deltas`
- **类别**: Logic Contradiction / Context Bloat
- **文件与行号**: `D:\bubuki\proactive-personal-agent\server\core\memory_engine.py:137-204`
- **根因与代码证据**:
  - `consume_tail_memory_delta`（第 153-157 行）在未传 `tail_msg_id` 时仅将 `consumer_key` 加入 `consumed_by`，未写入 `anchors[consumer_key]`；而 `inject_memory_deltas`（第 194-200 行）只检查 `anchor_id = anchors.get(consumer_key)`，忽略了 `consumer_key in consumed_by`，导致已消费过的 delta 被二次锚定。
  - 当切换到此前未对话过的副 Bot 或新端时，`memory_deltas` 中积攒的历史条目（最多 40 条）因无该 `consumer_key` 锚点，全部被绑定到该 Bot 的首条用户消息尾部。
- **实际影响**: 记忆增量重复注入或跨日旧增量在切换助手时突然集中灌入当前消息。
- **修复建议**: 在 `inject_memory_deltas` 中跳过创建时间早于该房间首条活跃消息或已在 `consumed_by` 中但无有效锚点的过期增量。

---

### BUG-11-05 [MEDIUM] `get_context_stats` 与 `last_prompt_tokens` 未按 `source` (`pc` vs `phone`) 隔离，且 `auto_compact_if_needed` 计算 `kept_tokens` 漏掉 `llm_text`
- **类别**: Cross-Source State Split-Brain
- **文件与行号**: `D:\bubuki\proactive-personal-agent\server\core\memory_engine.py:418-450, 471-492`；`server\core\llm_client.py:358-369`
- **根因与代码证据**:
  1. `auto_compact_if_needed` 按 `(bot_id, source)` 过滤消息（第 472-475 行），但 `get_context_stats`（第 420 行）与 `last_prompt_tokens[bot_id]`（第 359 行）仅按 `bot_id` 索引，不区分 `pc` 与 `phone`。
  2. 第 478 行计算总消息 Token 使用 `m.get('llm_text') or m.get('text', '')`，但第 491 行计算保留消息 `kept_tokens` 时仅写为 `self.estimate_tokens(bot_msgs[i].get('text', '')) + 12`，漏掉了包含视觉描述/文件内容的 `llm_text`。
- **实际影响**: PC 端简短对话会覆盖手机端同一 Bot 的 190K Token 计数（或反之），且保留窗口 `kept_tokens` 在含长 `llm_text` 时严重低估，保留过多大消息导致压缩后仍超阈值。
- **修复建议**: `kept_tokens` 统一使用 `bot_msgs[i].get('llm_text') or bot_msgs[i].get('text', '')`，并支持按 `source` 统计或区分上下文计数。

---

### BUG-11-06 [MEDIUM] `ContextSearchEngine` 中文长句固定 6 字切片导致跨边界检索漏召回，且主线程同步 `VACUUM` 阻塞事件循环
- **类别**: Search Recall Defect & Event Loop Blocking
- **文件与行号**: `D:\bubuki\proactive-personal-agent\server\core\context_search.py:201-212, 426-430, 770-778`
- **根因与代码证据**:
  1. `_extract_query_terms`（第 209 行）使用 `re.findall(r'[a-zA-Z0-9_.-]{2,}|[\u4e00-\u9fff]{2,6}', t)` 对无空格中文长词从左起按 6 字硬切（例如 `"怎么解除腾讯视频自动续订"` 被切为 `['怎么解除腾讯', '视频自动续订']`），导致查询与原文 `"腾讯视频自动续订已解除"` 在 FTS5 (`unicode61`) 与 `LIKE` 子串扫描中双双失配。
  2. `store_tool_context`（第 426-428 行）在达到 25 次写入或 1800 秒时，在主事件循环线程内同步执行 `prune_and_optimize()`，其中第 773-774 行直接运行阻塞式 `VACUUM;` 与 `PRAGMA wal_checkpoint(TRUNCATE);`。
- **实际影响**: 中文自然语言问句检索历史对话容易漏召回；大工具输出卸载触发 `VACUUM` 时会阻塞 FastAPI 异步事件循环数百毫秒至数秒。
- **修复建议**: 对长度 `>4` 的中文连续片段补充 2~3 字滑动 bi-gram/tri-gram 关键词以保证召回；将周期性 `VACUUM` 移至后台线程或仅在空闲维护任务中执行。

---

### BUG-11-07 [HIGH] `LLMClient.parse_and_strip_dsml_calls` 在未提取到任何工具调用时仍记录 `first_tag_pos` 并截断正常回复正文
- **类别**: False-Positive Content Truncation
- **文件与行号**: `D:\bubuki\proactive-personal-agent\server\core\llm_client.py:145-152, 230-257`
- **根因与代码证据**:
  ```python
  m_unc = unclosed_rx.search(text)
  if m_unc and not any(s <= m_unc.start() < e for s, e in paired_spans):
      _mark_pos(m_unc.start())
      ...
      if args_d and not extracted_calls:
          extracted_calls.append(...)
  if not extracted_calls and first_tag_pos is None:
      return text, []
  if first_tag_pos is not None and text[:first_tag_pos].strip():
      cleaned = text[:first_tag_pos].strip()
  ```
  当模型在正文中提及类似 `<read_file>` 或 `DSML` 相关标签但并未构成合法参数（`args_d` 为空，`extracted_calls` 为空）时，第 152 行和第 237 行依然提前调用了 `_mark_pos(...)` 设置 `first_tag_pos`。随后第 252 行因 `first_tag_pos is not None` 不再提前返回，第 257 行直接把 `first_tag_pos` 之后的所有正常回复内容全部截断丢弃！
- **实际影响**: 只要模型在讲解代码或工具用法时出现未带反引号的 `<read_file>` 等同名尖括号，其后整段回答会被静默截断，且不触发任何工具调用。
- **修复建议**: 仅在确认解析出有效 `args_d`（或确认为真实 DSML 块）时才调用 `_mark_pos()`；若最终 `not extracted_calls` 且非未闭合的真实 DSML 块，不应截断正文。

---

### BUG-11-08 [HIGH] `VisionEngine` 缺失备用网关 (`effective_fallback`) 容灾，且 `extract_ui_tree` 坐标解析在浮点数或 `0` 时崩溃/偏移
- **类别**: Vision Failover Omission & Coordinate Parsing Bug
- **文件与行号**: `D:\bubuki\proactive-personal-agent\server\core\vision_engine.py:57-96, 166-168`
- **根因与代码证据**:
  1. `analyze_image_b64`（第 58-87 行）只读取主配置 `effective_base_url()` 与 `effective_api_key()`，未像 `LLMClient._resolve_endpoint_candidates` 那样接入 `effective_fallback`。更重要的是，当主模型为纯文本模型（如 DeepSeek）时，`pick_vision_model` 会回退返回 `'antigravity/gemini-3-flash'`（第 55 行），但 `analyze_image_b64` 却将该模型名发往不支持它的主网关，收到 HTTP 400/404 后直接静默返回 `''`！
  2. `extract_ui_tree`（第 167-168 行）：
     ```python
     cx = max(0, min(width - 1, int(item.get('x') or (width // 2))))
     cy = max(0, min(height - 1, int(item.get('y') or (height // 2))))
     ```
     若视觉模型返回浮点字符串坐标（如 `"x": "128.5"`），`int("128.5")` 会抛出未捕获的 `ValueError` 导致整个 `extract_ui_tree` 崩溃；且当 `x == 0` 时，`0 or (width // 2)` 会错误地将坐标重置为屏幕正中央 `width // 2`。
- **实际影响**: 当主配置为非多模态端点且依赖 Fallback 提供多模态时，所有图片识别与视觉 UI 树提取全部静默失效；视觉节点含浮点字符串坐标时直接报错崩溃。
- **修复建议**: `analyze_image_b64` 支持主备网关候选重试；坐标解析改为 `int(round(float(item['x']))) if item.get('x') is not None else (width // 2)` 并加 `try/except (ValueError, TypeError)`。

---

### BUG-11-09 [MEDIUM] `SchedulerEngine` 未校验 `HH:MM` 数值合法性（如 `24:00` 抛未捕获异常）且 `update` 定时器未捕获 `parse_wall_time` 异常
- **类别**: Unhandled Exception / Input Validation
- **文件与行号**: `D:\bubuki\proactive-personal-agent\server\core\scheduler_engine.py:58-77, 153, 187, 199-205`
- **根因与代码证据**:
  1. `compute_next_routine_run`（第 58-75 行）用正则 `r'(\d{1,2}):(\d{2})(?::(\d{2}))?'` 提取时分秒后直接调用 `day_dt.replace(hour=hh, minute=mm, second=ss)`，若传入 `"24:00"` 或 `"08:60"` 会直接抛出 `ValueError: hour must be in 0..23`，导致 `add_routine` 及后续每次 `decorate_schedule_view()` 崩溃。
  2. `handle_tool` 的 `update` 分支（第 202 行）调用 `self.parse_wall_time(raw_at)` 未包裹 `try...except`（对比 `add_timer` 第 126-129 行有捕获），非法时间格式会直接抛异常。
  3. 第 153、187 行使用 `int(args.get('interval_hours') or 24)`，当传入小数（如 `"0.5"` 或 `1.5`，尽管第 81 行 `float(r.get('interval_hours'))` 支持 `0.05` 小时粒度）时会抛 `ValueError` 或被截断为整数。
- **实际影响**: 只要一条 routine 写入了 `"24:00"`，整个定时任务面板与 `schedule_task(action="list")` 将永久报错 500。
- **修复建议**: 在 `parse_wall_time` 与 `compute_next_routine_run` 中校验 `0 <= hh <= 23`、`0 <= mm <= 59`、`0 <= ss <= 59`（可将 `24:00` 归一化为次日 `00:00`），`update` 分支捕获 `ValueError`，并将 `interval_hours` 改为 `float(...)`。

---

## 二、`server/transport/`、`server/plugins/` 与 `desktop/` 深度审查发现

### BUG-11-10 [MEDIUM] `ExtensionRegistry` 与 `core/interfaces.py` 的 `ToolPlugin` 协议契约不一致，且 `search_plugins` / `memory_hooks` 从未被核心引擎调用
- **类别**: Interface Contract Mismatch / Dead Extension Hooks
- **文件与行号**: `D:\bubuki\proactive-personal-agent\server\transport\extension_registry.py:24-25, 34-40, 104-115`；`server\core\interfaces.py:15-43`
- **根因与代码证据**:
  1. `core/interfaces.py` 中 `ToolPlugin` 协议将 `required: list[str]` 定义为顶层属性（与 `parameters` 并列），但 `ExtensionRegistry.tool_schemas()`（第 107-114 行）完全未读取 `getattr(plugin, 'required', None)`，若第三方插件按协议定义顶层 `required`，生成的 OpenAI schema 会丢失必填项约束。
  2. `ExtensionRegistry` 注册了 `self.search_plugins` 与 `self.memory_hooks`，但在 `core/search_engine.py` 与 `core/memory_engine.py` 中没有任何地方调用 `registry.search_plugins` 或 `registry.memory_hooks`（`on_message_saved` / `on_sleep_consolidate` 均为死钩子）。
- **实际影响**: 第三方开发者按照 `interfaces.py` 编写的 `SearchPlugin` 与 `MemoryHook` 注册后完全不会触发。
- **修复建议**: 在 `tool_schemas()` 中自动合并顶层 `plugin.required` 到 `parameters['required']`，并在 `MemoryEngine` / `search_engine` 中对接 `memory_hooks` 与 `search_plugins`。

---

### BUG-11-11 [HIGH] `GmailToolPlugin` 在 OAuth2 模式下缺失 `read`/`send` 分支误报“未配置”，在 IMAP 模式下忽略 `message_id` 且仅在最新 8 封邮件中过滤 `query`
- **类别**: Broken Action Routing / Search Truncation Bug
- **文件与行号**: `D:\bubuki\proactive-personal-agent\server\plugins\gmail_plugin.py:29, 37, 88-110, 141-179, 207-253`
- **根因与代码证据**:
  1. **OAuth2 模式漏实现 `read` 与 `send`**（第 141-179 行）：当用户配置了 `oauth_token` / `refresh_token` 但未配置 IMAP `app_password` 时，Mode A 仅处理 `action in ('search', 'list', 'latest_codes')`。调用文档声明支持的 `action="read"` 或 `action="send"` 时会穿透到第 250 行，错误返回 `{'configured': False, 'error': '邮箱插件尚未配置授权凭证...'}`。
  2. **IMAP 模式完全忽略 `message_id` 与 `read` 动作**（第 207-246 行）：`parameters` 声明了 `message_id`，但代码中从未读取 `args.get('message_id')`。
  3. **IMAP 搜索先切片 `[-limit:]` 后过滤 `query`**（第 211-236 行）：`ids = (data[0] or b'').split()[-limit:]` 仅取收件箱最后 8 封邮件，再在内存比对 `query.lower() not in hay.lower()`，导致第 9 封及更早的目标邮件永远搜不到。
  4. `_refresh_oauth_if_needed`（第 88-110 行）未检查 token 过期时间，每次调用工具都无条件向 Google 请求刷新 Token 并写盘。
- **实际影响**: Gmail 插件的邮件详情读取、OAuth 发送及历史邮件搜索功能失灵。
- **修复建议**: 在 OAuth2 分支补齐 `read`（按 `message_id` 拉取正文）与 `send`（`/gmail/v1/users/me/messages/send`）；IMAP 分支支持按 `message_id` 单封读取，且有 `query` 时擴大扫描窗口或使用 IMAP `TEXT` 搜索。

---

### BUG-11-12 [HIGH] `IntegrationHubPlugin` 存在钉钉/Discord/Bark 推送载荷格式错误、Notion `notion_create_page` 漏实现及 IMAP 搜索截断
- **类别**: Protocol Payload Defect & Missing Action Implementation
- **文件与行号**: `D:\bubuki\proactive-personal-agent\server\plugins\integration_hub.py:61, 70-73, 436-451, 575-598, 629-639`
- **根因与代码证据**:
  1. **Webhook 载荷键名拼写错误与跨平台不兼容**（第 636 行）：
     ```python
     payload = {'msg_type': 'text', 'content': {'text': text}, 'text': text, ' msgtype': 'text'}
     ```
     - `' msgtype'` 开头多了一个空格，导致钉钉群机器人因找不到 `"msgtype"` 字段报 `40035 param error: msgtype is empty`；
     - `"content": {"text": text}` 为字典而非字符串，导致 Discord Webhook 报 `HTTP 400 Invalid Form Body (content: Must be a string)`；
     - Bark 在 `CATALOG_SUMMARY`（第 72 行）声明配置项为 `bark_url`，但第 631 行只读取 `bark_webhook`。
  2. **`notion_create_page` 声明但未实现**（第 61 行 vs 第 575-598 行）：`CATALOG_SUMMARY` 与模块文档声明支持 `notion_create_page`，但 `_notion_action` 中未处理该分支，直接返回 `'未知 Notion 动作: notion_create_page'`。
  3. **`_imap_list` 先截断 `ids[-limit:]` 后过滤 `query`**（第 437-451 行）：与 `gmail_plugin.py` 类似，只检查最新 10 封邮件标题/发件人是否含 `query`，稍微靠前的邮件全部漏检。
- **实际影响**: 钉钉、Discord、Bark 消息推送失败；Notion 创建页面失败；邮箱按关键词搜索超过 10 封以外的邮件全部漏检。
- **修复建议**: 按 `app_name` 分别构造 Discord (`{'content': text}`)、钉钉 (`{'msgtype': 'text', 'text': {'content': text}}`)、飞书 (`{'msg_type': 'text', 'content': {'text': text}}`) 与 Bark (`bark_url`) 载荷；在 `_notion_action` 中补齐 `notion_create_page`；在 `_imap_list` 有 `query` 时扩大扫描范围。

---

### BUG-11-13 [HIGH] `desktop_agent.py` 在 UI 树摘要中输出十六进制 `hwnd=0x...`，但 `resolve_target_hwnd` 用十进制 `int()` 解析导致必现 `ValueError`
- **类别**: Self-Contradictory Parameter Format / Crash
- **文件与行号**: `D:\bubuki\proactive-personal-agent\desktop\desktop_agent.py:422, 567, 842, 856, 899, 914`
- **根因与代码证据**:
  - `dump_window_tree`（第 567 行）及所有错误提示均将窗口句柄格式化为十六进制字符串：`hwnd={hex(hwnd)}`（如 `hwnd=0x1a04f2`）。
  - 但 `resolve_target_hwnd`（第 422 行）写为：
    ```python
    explicit = int(args.get('hwnd') or 0)
    ```
    当 LLM 从 `dump_window_tree` 复制 `"0x1a04f2"` 作为 `hwnd` 参数传入时，`int("0x1a04f2")`（默认 10 进制）立即抛出 `ValueError: invalid literal for int() with base 10: '0x1a04f2'`！
- **实际影响**: 只要模型传入十六进制 `hwnd` 字符串，桌面控制指令立即异常报错。
- **修复建议**: 将 `resolve_target_hwnd` 中的解析改为 `raw_h = args.get('hwnd'); explicit = int(str(raw_h).strip(), 0) if raw_h else 0`。

---

### BUG-11-14 [MEDIUM] `desktop_agent.py` 后台滚轮未定位子窗口、Unicode 辅助平面字符 (`Emoji`) `WM_CHAR` 截断，且 `run_desktop.bat` 缺省 Token 静默闪退
- **类别**: Win32 Input Defect & Startup Failure
- **文件与行号**: `D:\bubuki\proactive-personal-agent\desktop\desktop_agent.py:654-656, 1015-1020, 1114-1120`；`desktop\run_desktop.bat:5`
- **根因与代码证据**:
  1. `scroll`（第 1015-1020 行）直接向顶层 `hwnd` 发送 `WM_MOUSEWHEEL`，未像 `_post_background_click` 那样通过 `ChildWindowFromPointEx` 查找坐标下的实际可滚动子窗口句柄，导致绝大多数多面板 Win32 子窗口无法响应后台滚轮。
  2. `_post_background_text`（第 654-655 行）直接将 `ord(ch)` 作为 `WPARAM` 传给 `WM_CHAR`。对于 `ord(ch) > 0xFFFF` 的 Emoji 等字符，Win32 `WM_CHAR` 需要拆分为 UTF-16 高低代理对（Surrogate Pair）分两次发送，否则字符损坏。
  3. `run_desktop.bat`（第 5 行）使用无窗口的 `pythonw.exe` 启动且未传 `--token`，当独立部署缺少 `private/device-token` 且未设环境变量时，进程在后台静默退出，而批处理仍打印启动成功。
- **实际影响**: 桌面端后台滚动失效、发送 Emoji 乱码、独立机器双击 `run_desktop.bat` 静默退出无报错提示。
- **修复建议**: `scroll` 先解析 `ChildWindowFromPointEx` 目标子窗口；`_post_background_text` 将 `ch.encode('utf-16-le')` 按 2 字节 `uint16` 逐个发送 `WM_CHAR`；`run_desktop.bat` 校验 Token 文件或环境变量。

---

## 三、Android 清单/构建配置、跨层通知协议与存储/测试套件深度审查发现

### BUG-11-15 [CRITICAL] `AgentNotificationListener.java` 与 `server/core/notification_hub.py` 时间戳字段契约失配 (`timestamp_ms` vs `time`/`postTime`) 且 `onNotificationRemoved` 键名不匹配导致状态永不更新
- **类别**: Cross-Layer Contract Mismatch & Key Mismatch
- **文件与行号**: `D:\bubuki\proactive-personal-agent\android\app\src\main\java\dev\bubuki\proactive\AgentNotificationListener.java:76-87, 117, 136-137, 143`；`server\core\notification_hub.py:258-262`
- **根因与代码证据**:
  1. **跨端时间戳字段失配**：
     - Android 端 `AgentNotificationListener.java`（第 136-137 行）写入：
       ```java
       item.put("time", timeStr); // 格式为 "MM-dd HH:mm:ss"
       item.put("timestamp_ms", postTime); // 真实毫秒时间戳
       ```
     - 服务端 `NotificationHub.ingest_raw_notifications`（第 258-262 行）却读取：
       ```python
       post_ms = raw.get('time') or raw.get('postTime') or 0
       try:
           ts = float(post_ms) / 1000.0 if float(post_ms) > 1e11 else ...
       except (ValueError, TypeError):
           ts = now_ts
       ```
       由于未读取 `timestamp_ms`，且 `raw.get('time')` 是 `"11-08 14:30:12"` 字符串，`float(post_ms)` 必抛 `ValueError`，导致所有历史通知的真实产生时间全部丢失，被篡改为服务端拉取那一刻的 `now_ts`！
  2. **`onNotificationRemoved` 缓存键不一致**：
     - `recordPassive`（第 117、143 行）使用 `dedupKey = pkg + "|" + title + "|" + text` 作为 `recentBuffer` 的 key；
     - `onNotificationRemoved`（第 78-80 行）却用 `sbn.getKey()` 去查 `recentBuffer.get(key)`，永远返回 `null`，导致被用户划走的通知 `active` 永远保持 `true`。
- **实际影响**: AI 看到的通知时间全部变成拉取当下的时间，历史通知时间线完全失真；已划走通知在客户端缓冲中永远显示为活跃状态。
- **修复建议**: `notification_hub.py` 优先读取 `raw.get('timestamp_ms') or raw.get('postTime') or raw.get('time')`；`AgentNotificationListener` 在 `item` 中记录 `sbn_key` 并在 `onNotificationRemoved` 中遍历匹配更新 `active = false`。

---

### BUG-11-16 [CRITICAL] `android/app/build.gradle` 将私有 `private/device-token` 明文编译进 APK `BuildConfig.DEFAULT_DEVICE_TOKEN`，导致主控 Token 泄露且与发布门禁脚本冲突
- **类别**: Security Credential Leak / Release Gate Contradiction
- **文件与行号**: `D:\bubuki\proactive-personal-agent\android\app\build.gradle:16-18`；`android\app\src\main\java\dev\bubuki\proactive\SecureTransport.java:23, 74`；`scripts\publish_release_1_7.py:36-42`
- **根因与代码证据**:
  - `build.gradle`（第 16-18 行）：
    ```groovy
    def tokenFile = file('../../private/device-token')
    def defaultDeviceToken = tokenFile.exists() ? tokenFile.getText('UTF-8').trim() : (System.getenv('DEFAULT_DEVICE_TOKEN') ?: '')
    buildConfigField 'String', 'DEFAULT_DEVICE_TOKEN', '"' + defaultDeviceToken... + '"'
    ```
  - `publish_release_1_7.py`（第 36-42 行）：
    ```python
    private=[(ROOT/'private/device-token').read_bytes().strip(), ...]
    assert not any(secret and secret in blob for secret in private),'Private credential embedded in APK'
    ```
- **实际影响**: 只要本地存在 `private/device-token`，构建出的公開下载版 APK (`proactive-agent.apk`) 就会在 `classes.dex` 中硬编码明文设备 Token，任何下载 APK 的人都可提取该 Token 接管服务端；同时导致 `publish_release_1_7.py` 的安全门禁断言直接失败。
- **修复建议**: 移除 `build.gradle` 自动读取 `../../private/device-token` 的逻辑（默认置空字符串 `""`），仅在用户扫码/填入配对码或显式注入本地调试变量时使用。

---

### BUG-11-17 [HIGH] `LauncherActivity.java` 转发 `ACTION_SEND` 分享 Intent 时若原 Intent 仅含 `EXTRA_STREAM` 而无 `ClipData`，未传递临时 URI 读权限导致读取附件抛出 `SecurityException`
- **类别**: Android Intent Permission Propagation Defect
- **文件与行号**: `D:\bubuki\proactive-personal-agent\android\app\src\main\java\dev\bubuki\proactive\LauncherActivity.java:22-34`；`AndroidManifest.xml:27-60`
- **根因与代码证据**:
  ```java
  if (isShare && src != null) {
      next.setAction(act);
      next.setType(src.getType());
      if (src.getClipData() != null) {
          next.setClipData(src.getClipData());
          next.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION);
      }
      if (src.getExtras() != null) {
          next.putExtras(src.getExtras());
      }
  }
  startActivity(next);
  finish();
  ```
  当第三方应用通过 `Intent.EXTRA_STREAM` 分享文件/图片且未设置 `ClipData` 时，`LauncherActivity` 未添加 `FLAG_GRANT_READ_URI_PERMISSION` 且未将 `EXTRA_STREAM` 的 URI 封装进 `next.setClipData(...)`，随即调用 `finish()` 销毁自身，导致系统赋予 `LauncherActivity` 的临时 `content://` 读取权限在 `NativePulseActivity` 异步读取前失效。
- **实际影响**: 从部分相册/文件管理器“甩给白kawa”分享图片或文档时，由于 URI 权限未继承导致附件读取失败。
- **修复建议**: 只要 `isShare` 为真，无条件 `next.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)`，且当 `src.getClipData() == null` 时从 `EXTRA_STREAM` 提取 `Uri` 构造 `ClipData` 挂到 `next` 上。

---

### BUG-11-18 [MEDIUM] `AgentActionReceiver.java` 在高达 300s 超时的同步网络请求成功后才取消系统通知，离线或弱网时点击通知栏“已知晓/稍后提醒”无响应且易触发 Broadcast ANR
- **类别**: Android BroadcastReceiver ANR & UX Defect
- **文件与行号**: `D:\bubuki\proactive-personal-agent\android\app\src\main\java\dev\bubuki\proactive\AgentActionReceiver.java:17-26`；`SecureTransport.java:24-25`
- **根因与代码证据**:
  ```java
  PendingResult pending=goAsync();
  new Thread(()->{try{
      ...
      new SecureTransport(c).request("POST","ack".equals(action)?"/api/ack":"/api/snooze",body,null);
      c.getSystemService(NotificationManager.class).cancel(id==null?0:id.hashCode());
      c.sendBroadcast(new Intent(c.getPackageName()+".UPDATE").setPackage(c.getPackageName()));
  }catch(Exception ignored){}finally{pending.finish();}},"pulse-action").start();
  ```
  1. `NotificationManager.cancel(...)` 放在了网络请求之后，一旦手机处于离线或请求抛异常，用户点击“已知晓”后通知栏卡片无法消除；
  2. `SecureTransport.HTTP` 的 `connectTimeout=30s, readTimeout=300s`（且 400 会重试），远超 Android `BroadcastReceiver.goAsync()` 的 10 秒上限，弱网下会触发系统 ANR。
- **实际影响**: 弱网/断网下点击通知按钮无法关闭通知，且后台线程持有 `PendingResult` 超过 10 秒触发系统 ANR。
- **修复建议**: 点击后立即先调用 `NotificationManager.cancel(...)` 消除通知，并对 Receiver 内的网络请求限制超时在 6 秒以内。

---

### BUG-11-19 [HIGH] `storage.py`、---------------------------、`brain.py` 与 `context_search.py` 存在多处跨模块状态吞噬（工具 `trace` 结果在入库检索前被清空、`cached_vault_context` 写后即焚、多节点标签被降级）
- **类别**: Cross-Module State Destruction
- **文件与行号**: `D:\bubuki\proactive-personal-agent\server\storage.py:137-176, 283-286, 482-483`；`server\core\context_search.py:145-158, 174`；`server\core\memory_engine.py:287-294, 330-334, 708`；`server\brain.py:4160-4161, 4730-4736`
- **根因与代码证据**:
  1. **FTS5 工具轨迹索引为空**：`store.message()`（`storage.py:483`）先调用 `compact_trace_list()` 将所有非报错工具的 `result` 字段彻底删除，随后 `context_search.index_message(m)` 才读取 `m.get('trace')` 生成 `trace_text`（`context_search.py:154-157`），导致 FTS5 索引库中的 `trace_text` 永远没有工具返回结果（全为 `[tool({...})] -> `）。
  2. **`Brain._estimate_messages_tokens` 调用签名崩溃**：`brain.py:4161` 调用 `MemoryEngine.estimate_messages_tokens(messages)`（未传 `self`），而 `memory_engine.py:266` 将其定义为实例方法且内部访问不存在的 `self.llm`，双重崩溃。
  3. **`cached_vault_context` 互相覆写**：`memory_engine.py:708` 更新 `store.state['cached_vault_context']`，但未同步更新 `brain._cached_vault_context` 内存缓存；而 `storage.py:285` 在 `_scrub_state_bloat()` 中又把 `state['cached_vault_context']` 当作废弃字段删除。
  4. **四节点 `source` 在钉住记忆中被二元降级**：`storage.py:111-134` 支持 `phone`、`phone2`（安卓二）、`pc`、`pc2`（Windows二），但 `memory_engine.py:287-294, 333` 使用 `if src == 'pc' else '[来源: 手机端]'`，导致来自 `pc2`（Windows二）的钉住记忆被错误打上 `[来源: 手机端]` 并归类为 `phone`。
- **实际影响**: `conversation_search` 无法搜到历史工具执行结果；`_estimate_messages_tokens` 调用必崩；静默记忆整理更新后的画像无法同步到活跃缓存；Windows二/安卓二节点的记忆来源标签错乱。
- **修复建议**: 在 `store.message()` 压缩 `trace` 前先保留摘要供 `context_search` 索引；将 `MemoryEngine.estimate_messages_tokens` 改为 `@staticmethod`；统一 `cached_vault_context` 生命周期；`add_pin` / `_parse_pin_source_and_text` 支持全部 4 个节点来源。

---

### BUG-11-20 [HIGH] 现有测试套件 `server/tests/` 存在 2 处真实回归失败点与多处未覆盖的假阳性盲区
- **类别**: Broken Regression Tests & Test Coverage Gaps
- **文件与行号**: `D:\bubuki\proactive-personal-agent\server\tests\test_contract_and_memory_closure.py:200`；`server\tests\test_assistants.py:178`
- **根因与代码证据**:
  1. `test_contract_and_memory_closure.py:200` 断言 `assert mem.SLEEP_CONSOLIDATE_IDLE_SECONDS == 180`，但 `server/core/memory_engine.py:23` 当前值为 `45`，测试直接失败。
  2. `test_assistants.py:178` 断言 `assert json.loads((ws/'settings.json').read_text('utf-8'))['api_key']=='sk-test-key'`，但 `server/storage.py:294-318` (`_sync_to_workspace`) 已将明文 `api_key` 从 `workspace/settings.json` 中移除（仅保留 `has_api_key`），导致抛出 `KeyError: 'api_key'`。
  3. **假阳性测试盲区**：
     - `test_notification_and_screenshot.py:40-66` 构造的测试数据未传 Android 端真实的 `time: "MM-dd HH:mm:ss"` 与 `timestamp_ms`，掩盖了 `BUG-11-15` 的跨层时间戳解析失败；
     - `test_contract_and_memory_closure.py:236-260` 仅测试了 LLM 返回完美 JSON 与抛出异常两种情况，未测试 LLM 返回带标点 `NONE` 或普通文本时的死循环（`BUG-11-02`）。
- **实际影响**: 自动化测试套件未能全绿通过，且关键跨端字段契约在单测中被简化 Mock 掩盖。
- **修复建议**: 对齐 `SLEEP_CONSOLIDATE_IDLE_SECONDS` 与 `settings.json` 的测试契约，并补充覆盖真实 Android 通知载荷与非标准 LLM 输出的回归单测。
