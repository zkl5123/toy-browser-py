# 长期项目约定（toy-browser-py）

## Python 环境（本机两套，验证须区分）
- **完整环境**：系统 `C:\Users\zklzk\AppData\Local\Python\pythoncore-3.11-64\python.exe`（3.11.9），
  含 tkinter / wgpu 0.32.0 / PIL 12.3.0 / numpy 2.4.6。GUI 与 wgpu 回归**必须用它**。
- **受管隔离环境**：`C:\Users\zklzk\.workbuddy\binaries\python\versions\3.13.12\python.exe`（3.13.14），
  缺 tkinter/wgpu/PIL/numpy。该环境下 5 个 GUI 测试 import ERROR、wgpu 不可用，均为环境缺库而非代码缺陷。
- 默认工具链（沙箱）用受管 3.13.12 跑 fast 层即可；要验证 GUI/完整回归时切到 3.11.9。

## 测试
- `tests/run.py`：自动发现 + 分层（fast/full/only）+ Tcl 隔离子进程（event_generate 敏感模块单独干净子进程）。
- `--json <PATH>` 仅在全 PASS 时落盘；有 FAIL 时退出码 1 且不写 JSON。
- 新测试文件 TESTS 必须声明为 `(name, fn)` 二元组列表（`run.py` 按 `for name, fn in tests` 解包）。

## 架构约定
- 包名 `toy_browser` 不改（大量 import 依赖）；产品代码在 `toy_browser/`，GUI 在 `toy_browser/app/`。
- DOM 节点身份：`hit_test_at`/`_node_index_to_dom`/`_build_focus_chain` 三套索引统一用**元素节点计数**
  （is_element 才推进 counter，文本节点不占位）。

## 审计"不冒充通过"原则（round7 起贯彻）
- 平台能力（读屏/UIA/IME/真机）只落**纯逻辑映射层**，不 import 运行时（comtypes/UIA/OS）；
  真实 OS 事件分发 + 读屏/IME 人工验证保持"未验证"，在工作日志与开放问题里显式标注。
  例：`ui_a11y_bridge.py`（内部 a11y 树→UIA 控件契约 + name/role/value/state/focus 事件流 +
  地址栏 origin/最终URL/错误类型/TLS 可见，TLS 可信态**严格分离**于 https scheme，不推断）。

## R7-S03 三个新模块（网络/存储语义的唯一来源，别再各写一套）
- `network/cookie_context.py`：`CookieRequestContext`（不可变）——schemeful
  same-site、`credentials` 门禁、逐跳 `for_redirect` 重算。**不得**再写
  `{"is_cross_site": False, "is_top_level": True}` 这种缺省 dict；
  `unknown_context()` = 没有归属信息就不发 cookie。
- `network/cache_validity.py`：Vary 匹配 / `is_storable`（RFC 7234 §3.2
  授权响应）/ `merge_304_headers`（以已存头为底覆盖 304 头）。缓存键含
  凭据模式；已声明凭据严格不跨桶，未声明（""）才探测已知桶。
- `scripting/storage_service.py`：`StorageArea`（增量配额 + 失败原子化 +
  内部 dict 私有）与 `StorageService`（local=profile+origin，
  session=tab+origin，opaque=每文档一份）。`web_api.Storage` 只是外观。
  opaque token 用单调递增序号，**不要**用 `id(host)`（CPython 地址复用
  会让两个文档错误共享分区，表现为偶发串数据）。

## TLS 契约（ui_a11y_bridge.AddressBarState）
- `is_secure_display()` 仅当 `scheme_is_https()` **且** `tls_trusted==TLS_TRUSTED` 才显示安全；
  二者是独立字段，http 永不安全。审计 §229 明确禁止"由 URL 是否 https 推断可信"。
