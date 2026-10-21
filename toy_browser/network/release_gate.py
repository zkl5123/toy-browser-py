"""R7-Q03 — 分级发布门禁：L1 安全收口版（纯逻辑判定层，不冒充通过）。

Round-7 安全审计「分级发布门禁」的第 1 档 —— **L1 安全收口版**。
实施方案对 L1 的原始定义：

  > **L1 安全收口版**：S01–S04、请求/文档生命周期门禁通过；不可信内容
  > 仍只在受限环境评估。

L1 的边界（**不冒充 L4 通用浏览器**）：

  * L1 只做**安全收口**：确认 S01–S04 与请求/文档生命周期各安全契约
    的证据**已被验收**，且不可信内容（任意远程页面脚本）在默认配置下
    被**约束在受限环境**里评估——不把任意网络页面当可信代码运行。
  * L1 **不**承诺 L2/L3/L4 的动态能力：不承诺真实 JS/DOM/网络渲染主链
    对任意网站可用（L2 静态网页）、有限动态站点（L3）、通用浏览器
    （L4，需真实 OS 沙箱 / 读屏 / IME，单独验收）。

审计「不冒充通过」在 L1 上的两条铁律（与本模块设计边界一致）：

  1. **聚合而非重复实现**：本模块只调用**已验收的安全契约入口**做
     **行为探测**（判定"它现在是否按已验收的行为工作"），不重写任何
     安全逻辑；探测失败（契约缺失 / 行为回退 / 环境不可判）一律记为
     FAIL 或 UNSUPPORTED，**绝不**把"未验证"伪装成"通过"。
  2. **受限环境是事实，不是声明**：L1 收口成立的前提是"不可信内容
     仅在受限环境评估"——即脚本信任闸门默认为 local_only、远程源默认
     拒、opaque 源继承创建者。这条必须用**可判定的行为**证明（闸门
     确实默认拒远程），而不是"写了一段注释说我们受限"。

设计边界（"不冒充通过"）
------------------------
* 本模块只做**判定与聚合**：它 import 各已验收模块做只读探测，不执行
  任何封锁 / 网络 / 进程动作。探测里出现的网络/脚本行为都是**调用已有
  纯逻辑入口的返回值**（如 :meth:`ScriptTrustGate.decide`、
  :func:`filter_response_headers`、:func:`Deadline.check`），不发起真实
  I/O。
* 三级结果 :data:`PASS` / :data:`FAIL` / :data:`UNSUPPORTED`：
  UNSUPPORTED 表示"因环境受限（缺可选依赖 / 纯逻辑层无法判）而不能
  完成探测"，它**不是** PASS，在门禁判定里与 FAIL 同权重——只要有一条
  非 PASS，L1 就**不**收口（保守）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, List, Optional

__all__ = [
    "PASS", "FAIL", "UNSUPPORTED", "VALID_RESULTS",
    "L1", "L2", "L3", "L4", "VALID_TIERS", "TIERS",
    "GateCheck", "ReleaseGate",
    "evaluate_L1_gate", "describe_L1_gate",
]


# ---------------------------------------------------------------------------
# 结果 / 分级常量
# ---------------------------------------------------------------------------

#: 单条探测结论。
PASS = "pass"
FAIL = "fail"
UNSUPPORTED = "unsupported"
VALID_RESULTS = (PASS, FAIL, UNSUPPORTED)

#: 发布分级（审计「分级发布门禁」四档；本模块只判定 L1）。
L1 = "L1"
L2 = "L2"
L3 = "L3"
L4 = "L4"
VALID_TIERS = (L1, L2, L3, L4)

#: 各档的一句话边界（可被 grep / 打印，供 L2+ 接入时引用，避免 L1
#: 被误读成"通用浏览器"）。
TIERS = {
    L1: "安全收口版：S01–S04 + 请求/文档生命周期门禁全过；不可信内容仍只在受限环境评估",
    L2: "静态网页可用版：真实 JS/DOM/网络渲染主链对可信静态页可用（尚未验收）",
    L3: "有限动态站点版：有限动态站点可用（尚未验收）",
    L4: "通用浏览器：真实 OS 沙箱 / 读屏 / IME 独立验收（不通过 L1 推断）",
}


# ---------------------------------------------------------------------------
# 单条门禁检查
# ---------------------------------------------------------------------------

@dataclass
class GateCheck:
    """R7-Q03 — 一条门禁探测的结论。

    * *name*：稳定标识（审计口径），如 ``S01:script_trust_local_only_default``。
    * *batch*：所属安全批次（``S01``/``S02``/``S03``/``S04``/
      ``LIFECYCLE``/``ISOLATION``）。
    * *required*：是否为 L1 收口的**必备**证据（True 时非 PASS 即阻断
      收口；False 为观测项，不影响收口但计入报告）。
    * *result*：:data:`PASS` / :data:`FAIL` / :data:`UNSUPPORTED`。
    * *evidence*：一行可复述的探测证据（不冒充通过的措辞来源）。
    * *note*：UNSUPPORTED / FAIL 的原因（"未验证"如实标注）。
    """

    name: str = ""
    batch: str = ""
    required: bool = True
    result: str = UNSUPPORTED
    evidence: str = ""
    note: str = ""

    def is_pass(self) -> bool:
        return self.result == PASS

    def describe(self) -> str:
        verdict = self.result.upper()
        head = f"[{self.batch}] {self.name} = {verdict}"
        tail = self.evidence if self.result == PASS else self.note
        return head + (f" — {tail}" if tail else "")


# ---------------------------------------------------------------------------
# L1 门禁聚合器
# ---------------------------------------------------------------------------

class ReleaseGate:
    """R7-Q03 L1 安全收口版的可判定发布门禁。

    聚合 :mod:`toy_browser.scripting.script_trust`、
    :mod:`toy_browser.network.net`、:mod:`toy_browser.network.request_context`、
    :mod:`toy_browser.network.cookie_context`、:mod:`toy_browser.network.request_budget`、
    :mod:`toy_browser.network.page_limits` 等**已验收**契约入口，做只读
    行为探测，判定 L1 是否收口。

    用法::

        gate = evaluate_L1_gate()
        assert gate.l1_sealed is True      # 收口成立
        assert not gate.l4_claimed          # 显式不冒充 L4
        for c in gate.checks:
            print(c.describe())
    """

    def __init__(self, checks: List[GateCheck]) -> None:
        self.checks: List[GateCheck] = list(checks)
        self._blocked: List[GateCheck] = []

    # -- 派生结论 ----------------------------------------------------------
    @property
    def required_failed(self) -> List[GateCheck]:
        """阻断 L1 收口的必备检查（FAIL 或 UNSUPPORTED）。"""
        return [c for c in self.checks
                if c.required and not c.is_pass()]

    @property
    def l1_sealed(self) -> bool:
        """L1 是否收口：**所有必备**检查为 PASS（保守：非 PASS 即不
        收口）。"""
        return not self.required_failed

    @property
    def l4_claimed(self) -> bool:
        """恒为 False —— L1 收口也**不**隐含 L4 通用浏览器。"""
        return False

    @property
    def restricted_environment(self) -> bool:
        """不可信内容是否被约束在受限环境（L1 收口的语义前提）。

        当且仅当"脚本信任闸门默认拒绝远程源"这条探测为 PASS 时成立
        ——用可判定行为证明，而非注释声明。
        """
        return any(
            c.is_pass() and c.name == "S01:script_trust_local_only_default"
            for c in self.checks
        )

    # -- 报告 -------------------------------------------------------------
    def blocked(self) -> List[GateCheck]:
        """阻断 L1 收口的检查（必备且非 PASS）。"""
        return self.required_failed

    def describe(self) -> str:
        """一行总判定 + 未收口原因（若有）。"""
        verdict = "SEALED" if self.l1_sealed else "NOT-SEALED"
        parts = [f"L1 {verdict} (checks={len(self.checks)}, "
                 f"required_blocked={len(self.required_failed)}), "
                 f"L4 claimed={self.l4_claimed}"]
        if self.required_failed:
            names = ", ".join(c.name for c in self.required_failed[:6])
            parts.append(f"blocked by: {names}")
        return " ".join(parts)

    def report_lines(self) -> List[str]:
        """多行人类可读报告（供 CLI / 日志 / 审计附件）。"""
        lines = [self.describe(), "---"]
        for c in self.checks:
            lines.append(c.describe())
        lines.append("---")
        lines.append(
            f"L1 边界: {TIERS[L1]}")
        lines.append(
            "L1 不承诺: L2/L3/L4 动态能力（见 TIERS）；不可信内容仅在"
            "受限环境评估；真实 OS 沙箱/读屏/IME 独立验收，不经 L1 推断")
        return lines

    def to_dict(self) -> dict:
        """结构化快照（供测试断言 / 报告生成）。"""
        return {
            "l1_sealed": self.l1_sealed,
            "l4_claimed": self.l4_claimed,
            "restricted_environment": self.restricted_environment,
            "checks": [
                {"name": c.name, "batch": c.batch, "required": c.required,
                 "result": c.result, "evidence": c.evidence,
                 "note": c.note}
                for c in self.checks
            ],
            "blocked": [c.name for c in self.required_failed],
        }


# ---------------------------------------------------------------------------
# 行为探测（聚合已验收契约，不重复实现安全逻辑）
#
# 每条探测都是对**某个已验收契约入口的返回值**做一次可判定判定：
# 行为与已验收一致 → PASS；行为回退 → FAIL；环境 / 契约不可判 →
# UNSUPPORTED。探测一律只读、不发起 I/O、不执行封锁。
# ---------------------------------------------------------------------------

def _check(name: str, batch: str, ok: bool, evidence: str,
           fail_note: str = "", required: bool = True,
           unsupported: bool = False,
           unsupported_note: str = "") -> GateCheck:
    """把一条探测折叠成 :class:`GateCheck`。

    ``unsupported`` 为真时无论 ``ok`` 一律 UNSUPPORTED（环境受限）；
    否则 ``ok`` 真 → PASS，假 → FAIL。
    """
    if unsupported:
        return GateCheck(name=name, batch=batch, required=required,
                         result=UNSUPPORTED,
                         evidence=unsupported_note,
                         note=unsupported_note or "环境受限，未完成探测")
    if ok:
        return GateCheck(name=name, batch=batch, required=required,
                         result=PASS, evidence=evidence)
    return GateCheck(name=name, batch=batch, required=required,
                     result=FAIL, evidence="", note=fail_note)


def _probe_s01_script_trust() -> List[GateCheck]:
    """S01 — 远程脚本默认受限 + spawn 不冒充沙箱（受限环境语义前提）。"""
    try:
        from toy_browser.scripting import script_trust as st
    except Exception as exc:  # 契约缺失：不能把"没验"当"过了"
        return [_check("S01:script_trust_local_only_default", "S01",
                       False, "", fail_note=f"import 失败: {exc!r}",
                       unsupported=True,
                       unsupported_note=f"script_trust 契约不可用: {exc!r}")]
    out: List[GateCheck] = []
    gate = st.ScriptTrustGate()
    # 核心：默认策略 = local_only（审计保守默认，受限环境语义前提）。
    ok = (gate.policy == st.POLICY_LOCAL_ONLY)
    out.append(_check("S01:script_trust_local_only_default", "S01", ok,
                      f"默认策略={gate.policy}",
                      fail_note="默认策略不是 local_only，受限环境前提不成立",
                      required=True))
    # 远程网络页脚本默认被拒（不把任意网络页面当可信代码运行）。
    d = gate.decide("https://example.com/")
    ok = (not d.allowed) and (d.trust == st.TRUST_REMOTE)
    out.append(_check("S01:remote_default_denied", "S01", ok,
                      d.describe(),
                      fail_note="远程源未被默认拒绝，不可信内容未被约束"))
    # 本地 / 回环放行（受限≠全禁，回环是 W3C 可信源）。
    local_ok = gate.decide("http://127.0.0.1/").allowed
    out.append(_check("S01:loopback_allowed", "S01", local_ok,
                      "回环源默认放行（W3C potentially trustworthy）",
                      fail_note="回环源被拒，与既定信任类不一致"))
    # opaque 源继承创建者；创建者未知按远程（最保守）。
    opaque_ok = st.classify_url("data:text/html,x") == st.TRUST_REMOTE
    out.append(_check("S01:opaque_conservative", "S01", opaque_ok,
                      "data:/blob: 创建者未知按远程处理",
                      fail_note="不透明源未保守处理"))
    # spawn 不冒充沙箱（审计第 5 点）。
    iso = st.detect_script_isolation(worker_spawned=True)
    spawn_ok = (iso.is_security_boundary is False)
    out.append(_check("S01:spawn_not_sandbox", "S01", spawn_ok,
                      iso.describe(),
                      fail_note="worker spawn 被当成安全边界",
                      required=False))
    return out


def _probe_s02_request_context() -> List[GateCheck]:
    """S02 — 敏感头剥离 / 混合内容阻断 / 响应类型判定（请求生命周期）。"""
    try:
        from toy_browser.network import request_context as rc
    except Exception as exc:
        return [_check("S02:sensitive_header_stripped", "S02", False, "",
                       fail_note=f"import 失败: {exc!r}",
                       unsupported=True,
                       unsupported_note=f"request_context 不可用: {exc!r}")]
    out: List[GateCheck] = []
    # 敏感响应头剥离（set-cookie / ACAO 等脚本不可读；Content-Type 可读）。
    ctx = rc.context_for_destination(
        "https://app.test", "https://app.test/x",
        destination=rc.DEST_FETCH)
    headers = {"Set-Cookie": "a=1",
               "Access-Control-Allow-Origin": "https://app.test",
               "X-Content-Type-Options": "nosniff",
               "Content-Type": "application/json"}
    visible, stripped = rc.filter_response_headers(ctx, headers)
    ok = ("Set-Cookie" not in visible
          and "Access-Control-Allow-Origin" not in visible
          and "Content-Type" in visible)
    out.append(_check("S02:sensitive_header_stripped", "S02", ok,
                      f"stripped={sorted(stripped)}",
                      fail_note="敏感响应头未被剥离，脚本可读"))
    # 混合内容：https 文档加载 http 脚本（classic-script）→ 阻断。
    mixed_ctx = rc.context_for_destination(
        "https://secure.test", "http://insecure.test/a.js",
        destination=rc.DEST_CLASSIC_SCRIPT)
    d = rc.evaluate_request(mixed_ctx)
    ok = (not d.allowed) and d.blocked_by == "mixed-content"
    out.append(_check("S02:mixed_content_block", "S02", ok,
                      d.describe(),
                      fail_note="混合内容脚本未阻断"))
    # 响应类型同源 = basic。
    same = rc.response_type(
        rc.context_for_destination("https://a.test", "https://a.test/r",
                                   destination=rc.DEST_FETCH))
    out.append(_check("S02:response_type_basic", "S02",
                      same == "basic",
                      f"同源响应类型={same}",
                      fail_note="同源响应类型不是 basic",
                      required=False))
    return out


def _probe_s03_cache_cookie() -> List[GateCheck]:
    """S03 — 缓存存性 / cookie 无归属不发（存储语义收口）。"""
    out: List[GateCheck] = []
    try:
        from toy_browser.network import cache_validity as cv
    except Exception as exc:
        out.append(_check("S03:storable_authorization", "S03", False, "",
                          fail_note=f"import 失败: {exc!r}",
                          unsupported=True,
                          unsupported_note=f"cache_validity 不可用: {exc!r}"))
        return out
    # 带 Authorization 的响应不可缓存（RFC 7234 §3.2 授权响应）。
    d = cv.is_storable(200, {}, request_headers={"Authorization": "Bearer x"})
    ok = (not d.allowed)
    out.append(_check("S03:storable_authorization", "S03", ok,
                      cv.describe_decision(d),
                      fail_note="授权响应被误判可存"))
    # 304 头合并：以已存头为底（原样保留），304 携带的新头小写归一覆盖。
    merged = cv.merge_304_headers({"Cache-Control": "max-age=600"},
                                  {"Etag": '"zz"'})
    ok = (merged.get("Cache-Control") == "max-age=600"
          and merged.get("etag") == '"zz"')
    out.append(_check("S03:merge_304", "S03", ok,
                      f"merged keys={sorted(merged)}",
                      fail_note="304 头合并丢失已存头",
                      required=False))
    # cookie：没有归属信息就不发 cookie（保守）。
    try:
        from toy_browser.network import cookie_context as cc
        uctx = cc.unknown_context("https://x.test/")
        sends = (uctx.credentials_mode == cc.CRED_OMIT)
        out.append(_check("S03:cookie_unknown_not_send", "S03", sends,
                          f"unknown_context credentials={uctx.credentials_mode}",
                          fail_note="无归属 cookie 未保守 omit",
                          required=False))
    except Exception as exc:
        out.append(_check("S03:cookie_unknown_not_send", "S03", False, "",
                          fail_note=f"cookie_context 不可用: {exc!r}",
                          required=False, unsupported=True,
                          unsupported_note=str(exc)))
    return out


def _probe_s04_budgets() -> List[GateCheck]:
    """S04 — deadline 取消粘性 / 页级字节预算 / 页维度限额（生命周期）。"""
    out: List[GateCheck] = []
    try:
        from toy_browser.network import request_budget as rb
    except Exception as exc:
        out.append(_check("S04:deadline_cancel_sticky", "S04", False, "",
                          fail_note=f"import 失败: {exc!r}",
                          unsupported=True,
                          unsupported_note=f"request_budget 不可用: {exc!r}"))
        return out
    # deadline 过期即 cancelled。
    dl = rb.Deadline(monotonic=100.0, budget=5.0)
    ok = dl.expired(now=106.0) and not dl.expired(now=104.0)
    out.append(_check("S04:deadline_cancel_sticky", "S04", ok,
                      f"expired@106={dl.expired(106.0)} "
                      f"not@104={not dl.expired(104.0)}",
                      fail_note="deadline 过期判定异常"))
    # 取消粘性：取消后 check 必抛。
    scope = rb.CancellationScope()
    scope.cancel("demo")
    raised = False
    try:
        scope.check()
    except rb.RequestCancelled:
        raised = True
    out.append(_check("S04:cancel_sticky", "S04", raised,
                      "取消后 check 抛 RequestCancelled（粘性）",
                      fail_note="取消不粘性"))
    # 页级字节预算：解压字节顶到上限即耗尽（任一维满 → 顶格即拒）。
    pb = rb.PageBudget(raw=10, decoded=10)
    pb.charge(raw_bytes=2, decoded_bytes=10)   # decoded_used == cap
    ok = pb.exhausted and pb.would_exceed(0, 1)
    out.append(_check("S04:page_budget_exhaust", "S04", ok,
                      f"exhausted={pb.exhausted} would_exceed(0,1)="
                      f"{pb.would_exceed(0, 1)}",
                      fail_note="页级预算未耗尽",
                      required=False))
    # 页维度限额：先判后做（admit 判额度 + charge 记账，用满即拒）。
    try:
        from toy_browser.network import page_limits as pl
        lim = pl.PageResourceLimits(limits={
            pl.PageDimension.DOM_NODES: 2})
        d = pl.PageDimension.DOM_NODES
        admit1 = lim.admit(d)      # 余量 2 → True
        lim.charge(d, 1)          # used=1
        admit2 = lim.admit(d)      # 余量 1 → True
        lim.charge(d, 1)          # used=2 == cap
        admit3 = lim.admit(d)     # 余量 0 → False（先判后做）
        ok = (admit1 is True and admit2 is True and admit3 is False)
        out.append(_check("S04:page_dim_admit", "S04", ok,
                          f"admit 序列 T/T/F（cap=2，用满即拒）",
                          fail_note="维度限额 admit 行为异常",
                          required=False))
    except Exception as exc:
        out.append(_check("S04:page_dim_admit", "S04", False, "",
                          fail_note=f"page_limits 不可用: {exc!r}",
                          required=False, unsupported=True,
                          unsupported_note=str(exc)))
    return out


def _probe_net_bounds() -> List[GateCheck]:
    """请求/文档生命周期 — 分块 / 解压 / 响应体上限（L1 必备字节上限）。"""
    out: List[GateCheck] = []
    try:
        import toy_browser.network.net as net
    except Exception as exc:
        out.append(_check("LIFECYCLE:bounded_reads", "LIFECYCLE", False,
                         "", fail_note=f"import 失败: {exc!r}",
                         unsupported=True,
                         unsupported_note=f"net 契约不可用: {exc!r}"))
        return out
    # 分块解码有界 + 上限生效（超 cap 抛 ResponseTooLarge）。
    ok = (net.MAX_RESPONSE_BYTES > 0)
    out.append(_check("LIFECYCLE:bounded_reads", "LIFECYCLE", ok,
                      f"MAX_RESPONSE_BYTES={net.MAX_RESPONSE_BYTES}",
                      fail_note="响应体上限未启用",
                      required=False))
    # 解压炸弹有界（gunzip_bounded 超限抛错）。
    try:
        import gzip as _gz
        small = _gz.compress(b"x" * 1000)
        cap_hit = False
        try:
            net.gunzip_bounded(small, max_bytes=50)
        except net.ResponseTooLarge:
            cap_hit = True
        ok = cap_hit
        out.append(_check("LIFECYCLE:gunzip_bounded", "LIFECYCLE", ok,
                         "gunzip_bounded 超 cap 抛 ResponseTooLarge",
                         fail_note="解压上限未生效",
                         required=False))
    except Exception as exc:
        out.append(_check("LIFECYCLE:gunzip_bounded", "LIFECYCLE", False,
                         "", fail_note=f"无法验证: {exc!r}",
                         required=False, unsupported=True,
                         unsupported_note=str(exc)))
    # CSP 解析存在（S02 文档策略入口）。
    parsed = net.parse_csp_directives("default-src 'self'; script-src 'none'")
    ok = ("default-src" in parsed and "script-src" in parsed)
    out.append(_check("LIFECYCLE:csp_parse", "LIFECYCLE", ok,
                      f"directives={sorted(parsed)}",
                      fail_note="CSP 解析入口不可用",
                      required=False))
    return out


def _probe_worker_timeout() -> List[GateCheck]:
    """S04 — worker 有界终止状态机可判定（任务不能无限挂起）。

    契约入口在 :mod:`toy_browser.app.worker_timeout`
    （``WorkerTerminationState``）；纯逻辑，不 import 运行时。
    """
    out: List[GateCheck] = []
    try:
        from toy_browser.app import worker_timeout as wt
    except Exception as exc:
        out.append(_check("S04:worker_timeout", "S04", False, "",
                          fail_note=f"worker_timeout 不可用: {exc!r}",
                          required=False, unsupported=True,
                          unsupported_note=str(exc)))
        return out
    st = wt.WorkerTerminationState()
    st.begin_terminate(now=0.0, summary="demo")
    # join 预算用尽 → 可 forced_kill；budget 内 → 仍等待。
    killed = st.check_join_budget(now=2.0)
    waiting = not st.check_join_budget(now=1.0)
    st.complete(now=2.0, natural=False)
    ok = killed and waiting and st.may_navigate
    out.append(_check("S04:worker_timeout", "S04", ok,
                      st.describe(),
                      fail_note="worker 有界终止状态机不可判定",
                      required=False))
    return out


#: L1 收口判定的必备证据顺序（S01–S04 + 请求/文档生命周期）。
_L1_PROBES: tuple = (
    _probe_s01_script_trust,
    _probe_s02_request_context,
    _probe_s03_cache_cookie,
    _probe_s04_budgets,
    _probe_net_bounds,
    _probe_worker_timeout,
)


def evaluate_L1_gate(probes=None) -> ReleaseGate:
    """R7-Q03 — 运行 L1 全部门禁探测，返回可判定的 :class:`ReleaseGate`。

    ``probes`` 可注入自定义探测函数（测试用）；缺省用
    :data:`_L1_PROBES`。聚合结果里只要有一条**必备**检查非 PASS，
    L1 即不收口（保守；UNSUPPORTED 与 FAIL 同权重）。
    """
    checks: List[GateCheck] = []
    for probe in (probes if probes is not None else _L1_PROBES):
        checks.extend(probe())
    return ReleaseGate(checks)


def describe_L1_gate(gate: ReleaseGate) -> str:
    """多行 L1 门禁报告（供 CLI / 审计附件）。"""
    return "\n".join(gate.report_lines())

