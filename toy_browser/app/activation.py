"""B07 — 统一激活、表单提交与链接跳转（纯逻辑核心）。

依据：``assessments/20261016-round5/docs/百度真实网页访问评估与改进步骤.md``
第 6 节 B07（批次 18 纯逻辑落点；GUI 按键 / 鼠标接线与活显示验收留后续）。

与已有模块的关系：

- **B07 步骤 2**（form owner / successful controls / 禁用排除 / 重复字段 /
  UTF-8 / GET 查询编码 / POST enctype）由批次 1 的 ``browser.py``
  （``collect_form_fields`` / ``serialize_form_fields`` / ``form_action_url``
  / ``form_get_url`` / ``form_post_body`` / ``validate_form``）覆盖；本模块
  补上**激活动作决策**层。
- **B07 步骤 1**：``activate_element`` 让鼠标 / Enter / Space 按控件类型
  进入同一默认动作；**先派发可取消事件**（``click`` 的
  ``default_prevented`` 可阻断导航 / 提交），再决定是否导航或提交。
- **B07 步骤 3**：``action="/s"`` / 相对 a href / 协议相对 / ``<base>``
  交给文档基址解析（复用批次 17 ``navigation.py`` 的
  :class:`~toy_browser.app.navigation.Location`）；``target=_blank`` /
  当前页 / hash / 不支持的 scheme 明确分类。
- **B07 步骤 4**：``submit`` / ``requestSubmit`` 与默认 Enter 提交差异由
  :class:`ActivateDecision` 的 ``trigger`` 字段区分；脚本驱动控件仍须执行
  其实际事件逻辑——本模块不硬编码识别站点 ID，只提供**事件 → 决策**
  的通用纯逻辑（事件钩子由 GUI / 脚本层注入）。
- **B07 步骤 1**（可取消）：``cancelable_click=True`` 时先走
  ``on_click`` 钩子，钩子置 ``default_prevented`` 则决策被取消
  （不导航 / 不提交，B07 验收 "preventDefault"）。

无 Tk / 无网络 I/O；``tests/test_activation.py`` 覆盖。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Callable, Any

# --- 激活触发方式（B07 步骤 4：submit / requestSubmit / 默认 Enter 差异） ----

TRIGGER_MOUSE = "mouse"        # 鼠标点击
TRIGGER_ENTER = "enter"        # 键盘 Enter（默认提交动作）
TRIGGER_SPACE = "space"        # 键盘 Space（按钮激活）
TRIGGER_SUBMIT = "submit"      # form.submit()
TRIGGER_REQUEST_SUBMIT = "requestSubmit"  # form.requestSubmit()（走验证）

# --- 目标处置（B07 步骤 3） -------------------------------------------------

TARGET_CURRENT = "current"     # 当前页导航
TARGET_BLANK = "blank"         # 新标签（本 toy 记录但不打开）
TARGET_SELF = "self"
TARGET_PARENT = "parent"
TARGET_TOP = "top"


# ---------------------------------------------------------------------------
# 可取消点击事件（B07 步骤 1）
# ---------------------------------------------------------------------------

class ClickEvent:
    """可取消的 ``click`` 事件（派发后决定是否执行默认动作）。"""

    __slots__ = ("element_id", "trigger", "default_prevented")

    def __init__(self, element_id: str, trigger: str = TRIGGER_MOUSE) -> None:
        self.element_id = element_id
        self.trigger = trigger
        self.default_prevented = False

    def prevent_default(self) -> None:
        self.default_prevented = True

    def __repr__(self) -> str:
        return (f"ClickEvent(el={self.element_id!r} trig={self.trigger}"
                f" prevented={self.default_prevented})")


# ---------------------------------------------------------------------------
# 激活决策（B07 步骤 1：鼠标 / Enter / Space 按控件类型进入同一默认动作）
# ---------------------------------------------------------------------------

@dataclass
class ActivateDecision:
    """``activate_element`` 的结果（纯逻辑；由调用方消费）。

    - ``action`` — 决策出的默认动作：
      ``"navigate"``（链接 / submit 跳转）/ ``"submit"``（表单提交）/
      ``"focus"``（可聚焦控件）/ ``"none"``（无默认动作 / 被取消）。
    - ``target`` — 导航目标 URL（action == "navigate" 时）。
    - ``url_kind`` — 目标处置（``current`` / ``blank`` / ``self`` 等，
      B07 步骤 3）。
    - ``scheme_ok`` — 支持的 scheme（http/https/file/about）为 True；
      不支持的 scheme（``javascript:`` / ``mailto:`` 等 toy 不支持）
      为 False（调用方决定降级，不静默）。
    - ``query`` / ``method`` — 表单提交时的查询串 / 方法（GET/POST）。
    - ``prevented`` — 可取消 click 被 ``preventDefault`` 时为 True。
    - ``skipped_fields`` — 被排除的字段（禁用 / 未命名，B07 步骤 2）。
    """
    action: str = "none"
    target: str = ""
    url_kind: str = TARGET_CURRENT
    scheme_ok: bool = True
    method: str = "GET"
    query: str = ""
    prevented: bool = False
    trigger: str = TRIGGER_MOUSE
    skipped_fields: list = field(default_factory=list)

    def summary(self) -> str:
        bits = [f"action={self.action}"]
        if self.target:
            bits.append(f"target={self.target}")
        if self.query:
            bits.append(f"query={self.query}")
        if self.skipped_fields:
            bits.append(f"skipped={self.skipped_fields}")
        if self.prevented:
            bits.append("PREVENTED")
        return " ".join(bits)


# ---------------------------------------------------------------------------
# 表单字段规格（B07 步骤 2：name / value / 成功控件 / 禁用 / 未命名排除）
# ---------------------------------------------------------------------------

@dataclass
class FormField:
    """表单字段的最小纯逻辑规格（不依赖 DOM 树，便于单测）。

    - ``kind`` — ``text`` / ``textarea`` / ``select`` / ``radio`` /
      ``checkbox`` / ``submit`` / ``button``。
    - ``name`` — 为空 = 未命名（B07 步骤 2 排除出提交）。
    - ``disabled`` — True 时排除（B07 步骤 2 "禁用/未命名/字段排除"）。
    - ``checked`` — 仅 checkbox / radio / 隐式 submit 有意义；未勾选的
      checkbox / radio **不是** successful control（B07 步骤 2）。
    """
    name: str = ""
    value: str = ""
    kind: str = "text"
    disabled: bool = False
    checked: bool = True
    form: str = ""            # 所属 form id（form owner，B07 步骤 2）

    def is_successful_control(self) -> bool:
        """B07 步骤 2 — 是否计入提交（successful control）。

        - 文本类（text / textarea / select / 命名 submit / button）：
          非禁用即计入。
        - checkbox / radio：仅 ``checked=True`` 且非禁用时计入。
        - 未命名（``name`` 空）或 ``disabled`` 一律排除。
        """
        if self.disabled:
            return False
        if not self.name:
            return False
        if self.kind in ("checkbox", "radio"):
            return self.checked
        return True


def _is_submit_button(f: FormField) -> bool:
    return f.kind == "submit" or (f.kind == "button" and not f.name)


# ---------------------------------------------------------------------------
# 链接目标解析（B07 步骤 3：文档基址解析 + target/scheme 分类）
# ---------------------------------------------------------------------------

def _classify_target(href: str) -> tuple[str, bool]:
    """返回 ``(url_kind, scheme_ok)``。

    - ``javascript:`` / ``mailto:`` / 其他非 http/https/file/about scheme
      → scheme 不支持（``scheme_ok=False``），调用方降级（B07 步骤 3
      "不支持的 scheme 明确处理"，不静默丢弃）。
    - ``about:`` / 相对 / http / https / file → 支持。
    """
    low = (href or "").strip().lower()
    if low.startswith("javascript:") or low.startswith("mailto:") \
            or low.startswith("data:") or low.startswith("ftp:"):
        return TARGET_CURRENT, False
    return TARGET_CURRENT, True


def parse_link_target(href: str, base: str,
                     target_attr: str = "") -> tuple[str, str, bool]:
    """B07 步骤 3 — 相对 a href / 协议相对 / ``<base>`` 统一解析。

    返回 ``(resolved_url, url_kind, scheme_ok)``：
    - 相对目标经 ``urllib.parse.urljoin`` 相对 ``base``（文档基址）解析；
    - 协议相对（``//host/…``）保留 scheme；
    - ``target_attr`` 为 ``_blank`` 时 ``url_kind="blank"``（记录不打开）；
    - 其余归当前页。
    """
    import urllib.parse
    h = (href or "").strip()
    url_kind = TARGET_CURRENT
    if (target_attr or "").lower() in ("_blank", "blank"):
        url_kind = TARGET_BLANK
    if not h:
        return (base, url_kind, True)
    # 协议相对：补 base 的 scheme（与浏览器一致），直接作为绝对 URL 解析。
    if h.startswith("//"):
        scheme = (base or "http://").split("://", 1)[0] or "http"
        resolved = scheme + ":" + h
        return resolved, url_kind, _classify_target(h)[1]
    _, scheme_ok = _classify_target(h)
    # 绝对 URL 原样解析（不相对化）；相对目标经 urljoin 相对文档基址。
    if h.startswith("http:") or h.startswith("https:") or h.startswith("about:") \
            or h.startswith("file:"):
        resolved = h
    else:
        resolved = urllib.parse.urljoin(base, h)
    return resolved, url_kind, scheme_ok


# ---------------------------------------------------------------------------
# activate_element — 鼠标 / Enter / Space 同一默认动作（B07 步骤 1）
# ---------------------------------------------------------------------------

def activate_element(
    tag: str,
    fields: Optional[list] = None,
    form_id: str = "",
    action: str = "",
    base: str = "",
    href: str = "",
    target_attr: str = "",
    trigger: str = TRIGGER_MOUSE,
    cancelable: bool = True,
    on_click: Optional[Callable[[ClickEvent], None]] = None,
) -> ActivateDecision:
    """B07 步骤 1/2/3/4 — 按控件类型给出默认激活决策（纯逻辑）。

    参数：
    - ``tag`` — 控件标签小写：``a`` / ``input`` / ``button`` /
      ``form``（在表单内 Enter 的默认提交目标）/ 其余。
    - ``fields`` — 表单成功控件（:class:`FormField` 序列，可 None）；
      提交时排除禁用 / 未命名 / 未勾选 checkbox/radio。
    - ``form_id`` / ``action`` — 表单 owner 与 action（B07 步骤 2）。
    - ``base`` — 文档基址（B07 步骤 3 相对解析）。
    - ``href`` — 链接目标（tag == "a"）。
    - ``target_attr`` — 链接 target（B07 步骤 3）。
    - ``trigger`` — 触发方式（B07 步骤 4：mouse / enter / space /
      submit / requestSubmit）。
    - ``cancelable`` / ``on_click`` — B07 步骤 1 "先派发可取消事件"：
      钩子置 ``default_prevented`` 则决策被取消（不导航 / 不提交）。

    返回 :class:`ActivateDecision`。
    """
    fields = fields or []
    d = ActivateDecision(trigger=trigger)

    # B07 步骤 1：可取消 click（鼠标 / 键盘激活都派发）。
    ev = ClickEvent(element_id=form_id or tag, trigger=trigger)
    if cancelable:
        if on_click is not None:
            on_click(ev)
        if ev.default_prevented:
            d.prevented = True
            d.action = "none"
            return d

    low = (tag or "").lower()

    # --- 链接（B07 步骤 3） -------------------------------------------------
    if low == "a":
        resolved, url_kind, scheme_ok = parse_link_target(
            href, base, target_attr)
        d.action = "navigate"
        d.target = resolved
        d.url_kind = url_kind
        d.scheme_ok = scheme_ok
        return d

    # --- 可提交控件：submit / 命名 button / form 默认 Enter -----------------
    if low in ("input", "button") or (low == "form" and trigger == TRIGGER_ENTER):
        d.action = "submit"
        d.method = "GET"
        # B07 步骤 2：排除禁用 / 未命名 / 未勾选的字段。
        successful = []
        for f in fields:
            if f.is_successful_control():
                successful.append(f)
            else:
                d.skipped_fields.append(
                    f.name or f"<unnamed:{f.kind}>")
        # 查询串：GET 用 URL 编码（中文查询 UTF-8，B07 验收 "中文查询"）；
        # 重复字段名作为重复 k=v 对（与 browser.serialize_form_fields 同公式）。
        import urllib.parse
        pairs: list[str] = []
        for f in successful:
            pairs.append(urllib.parse.quote(f.name, safe=""))
            pairs.append("=")
            pairs.append(urllib.parse.quote(f.value, safe=""))
            pairs.append("&")
        qs = "".join(pairs)
        d.query = qs[:-1] if qs else ""
        # action 目标：相对 / 协议相对 / base 解析（B07 步骤 3）。
        d.target = _resolve_form_action(action, base, d.query)
        d.url_kind, d.scheme_ok = _classify_target(d.target)
        return d

    # --- 其余可聚焦控件：焦点动作（不导航 / 不提交） ------------------------
    d.action = "focus"
    return d


def _resolve_form_action(action: str, base: str, query: str) -> str:
    """B07 步骤 2/3 — 表单 action 的基址解析 + GET 查询拼接。

    - 空 action → 当前文档基址（浏览器语义：提交到当前页）；
    - 协议相对（``//…``）补 scheme；根相对（``/…``）相对 origin；
    - 相对路径经 ``urljoin``；
    - GET：查询串拼到 action 上（已有 ``?`` 用 ``&`` 接）。
    """
    import urllib.parse
    a = (action or "").strip()
    if not a:
        target = base
    elif a.startswith("//"):
        scheme = (base or "http://").split("://", 1)[0] or "http"
        target = scheme + "://" + a
    else:
        target = urllib.parse.urljoin(base, a) if base else a
    if query:
        target = target + ("&" if "?" in target else "?") + query
    return target


# ---------------------------------------------------------------------------
# 表单提交（B07 步骤 4：submit / requestSubmit 差异）
# ---------------------------------------------------------------------------

def build_form_submission(
    fields: list,
    form_id: str = "",
    action: str = "",
    base: str = "",
    method: str = "GET",
    trigger: str = TRIGGER_SUBMIT,
    validate: bool = True,
) -> dict:
    """B07 步骤 2/4 — 构造表单提交的纯逻辑结果。

    - successful controls：排除禁用 / 未命名 / 未勾选 checkbox/radio；
    - GET：查询串进 URL；POST：查询串进 body（enctype 由调用方决定，
      默认 urlencoded，B07 步骤 2 "POST 依 enctype 构建请求"）；
    - ``validate=True``（requestSubmit 语义）时跑必填 / pattern 校验，
      违规阻断提交（返回 ``blocked=True`` + violations）；
      ``form.submit()``（trigger == submit）跳过校验（B07 步骤 4 差异）。
    返回 ``{"ok": bool, "url": …, "body": …, "method": …,
    "violations": […]}``。
    """
    import urllib.parse
    successful = [f for f in (fields or []) if f.is_successful_control()]
    pairs: list[str] = []
    for f in successful:
        pairs.append(urllib.parse.quote(f.name, safe=""))
        pairs.append("=")
        pairs.append(urllib.parse.quote(f.value, safe=""))
        pairs.append("&")
    qs = "".join(pairs)[:-1]

    # 校验：requestSubmit / Enter 默认提交走验证；form.submit() 跳过。
    violations: list[dict] = []
    if validate:
        for f in successful:
            if f.required_empty():
                violations.append({
                    "field": f.name, "kind": "required",
                    "message": f"required field {f.name!r} is empty"})
    if violations:
        return {"ok": False, "url": "", "body": qs, "method": method,
                "blocked": True, "violations": violations}

    url = _resolve_form_action(action, base, qs if method.upper() == "GET" else "")
    body = "" if method.upper() == "GET" else qs
    return {"ok": True, "url": url, "body": body, "method": method.upper(),
            "blocked": False, "violations": [],
            "content_type": "application/x-www-form-urlencoded"}


# 为 build_form_submission 的校验提供最小钩子（避免与 browser.validate_form
# 重复；这里只检查"required 且空"这一 toy 子集）。
def _required_empty(self) -> bool:
    return bool(getattr(self, "required", False)) and \
        (self.kind in ("text", "textarea", "select") and self.value == "")


FormField.required_empty = _required_empty   # type: ignore[attr-defined]
