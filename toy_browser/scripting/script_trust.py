"""R7-S01 — 远程脚本信任闸门 + 脚本隔离声明（纯逻辑决策层）。

Round-7 安全审计 S01 的第 1 点与第 5 点。第 2/3/4 点（JS→Python
宿主对象能力表）已在 ``scripting/js.py`` 的 ``_HostObjectPolicy``
落地并有 ``tests/test_r7_s01_host_boundary.py`` 覆盖；本模块补齐
审计同样点名、**此前完全缺失**的两块：

第 1 点 —「先提供明确的远程脚本禁用开关 / 可信页面模式；安全边界
完成前不得默认把任意网络页面当可信代码运行」：

* :func:`classify_url` 把 URL 归入四档信任类（local / remote /
  opaque / unknown）；
* :class:`ScriptTrustGate` 提供三态策略与显式授权白名单，默认
  :data:`POLICY_LOCAL_ONLY`（保守默认），远程页面脚本默认**不**执行。

第 5 点 —「结合 A02 增加独立执行进程和有限 IPC；操作系统沙箱 /
权限收敛单独验收，**不能把 spawn 写成安全沙箱**」：

* :class:`ScriptIsolation` 区分"脚本在哪执行"与"有没有 OS 沙箱"；
* 只有 OS 沙箱**经验证**时 :attr:`ScriptIsolation.is_security_boundary`
  才为真——worker 进程 spawn 了、IPC 通了，**都不构成安全边界**。

设计边界（"不冒充通过"）
------------------------
本模块只做**判定与声明校验**，不执行任何封锁动作：它不碰
socket、不起进程、不改 ``sys``。真实封锁由调用点按
:meth:`ScriptTrustGate.decide` 的结果执行；真实 OS 沙箱 / 权限收敛
需要操作系统层面的验证，本模块只能表达"未验证"（默认 False），
**不**把子进程 spawn 粉饰为沙箱。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlsplit

__all__ = [
    "TRUST_LOCAL", "TRUST_REMOTE", "TRUST_OPAQUE", "TRUST_UNKNOWN",
    "VALID_TRUST_CLASSES",
    "POLICY_DISABLE_ALL", "POLICY_LOCAL_ONLY", "POLICY_ALLOW_ALL",
    "VALID_TRUST_POLICIES",
    "classify_url", "origin_of",
    "ScriptDecision", "ScriptTrustGate",
    "ISO_NONE", "ISO_WORKER_IPC", "ISO_OS_SANDBOX",
    "VALID_ISOLATION_MODES",
    "ScriptIsolation", "detect_script_isolation",
    "ScriptIsolationClaim", "claim_script_sandbox",
    "default_script_trust_gate", "reset_default_script_trust_gate",
]


# ---------------------------------------------------------------------------
# 信任类（第 1 点）
# ---------------------------------------------------------------------------

#: 本地 / 可信：``file:``、``about:``、``chrome:`` 等无网络来源，以及
#: W3C Secure Contexts 认定的 "potentially trustworthy" 回环源
#: （``http://localhost``、``127.0.0.1``、``[::1]``——Chrome/Firefox
#: 与规范均把回环当作可信上下文）。
TRUST_LOCAL = "local"

#: 远程：走了真实网络的 ``http`` / ``https`` / ``ws`` / ``wss`` 且非
#: 回环。默认策略下**不**把它的脚本当可信代码运行（审计要求）。
TRUST_REMOTE = "remote"

#: 不透明源：``data:`` / ``blob:``。自身不携带可信性，须**继承创建者**
#: 的信任类（``creator_trust``）；创建者未知时按最保守的远程处理。
TRUST_OPAQUE = "opaque"

#: 无法判定（空 URL / 无 scheme / 解析异常）。保守按远程处理——
#: "判不出来"不等于"可信"。
TRUST_UNKNOWN = "unknown"

VALID_TRUST_CLASSES = (TRUST_LOCAL, TRUST_REMOTE,
                       TRUST_OPAQUE, TRUST_UNKNOWN)

#: W3C Secure Contexts — potentially trustworthy 回环主机。
LOOPBACK_HOSTS = frozenset({
    "localhost", "127.0.0.1", "::1", "[::1]",
    "0:0:0:0:0:0:0:1", "[0:0:0:0:0:0:0:1]",
})

#: 无网络来源的 scheme（本地文档 / 内部页）。
_LOCAL_SCHEMES = frozenset({"file", "about", "chrome", "resource", "app"})

#: 不透明源 scheme（信任须继承创建者）。
_OPAQUE_SCHEMES = frozenset({"data", "blob", "filesystem"})

#: 网络 scheme（非回环即远程）。
_NETWORK_SCHEMES = frozenset({"http", "https", "ws", "wss", "ftp"})


def _split(url: str):
    """安全切分 URL；任何异常都退化为 ``(None, None)``（不崩）。"""
    if not url or not isinstance(url, str):
        return (None, None)
    raw = url.strip()
    if not raw:
        # 空 / 纯空白：没有文档 URL，判不出来 → 保守由调用点按远程拒
        # （"判不出来"不等于"可信"）。
        return (None, None)
    try:
        parts = urlsplit(raw)
    except Exception:
        return (None, None)
    scheme = (parts.scheme or "").lower()
    host = (parts.hostname or "").lower() if parts.hostname else ""
    return (scheme, host)


def classify_url(url: str, creator_trust: Optional[str] = None) -> str:
    """R7-S01 第 1 点 — 把 *url* 归入四档信任类之一。

    ``creator_trust`` 只对不透明源（``data:`` / ``blob:``）有意义：
    它们本身不携带可信性，继承创建者的信任类；创建者未知或非法时
    按最保守的 :data:`TRUST_REMOTE` 处理。

    未知 / 无法解析 → :data:`TRUST_UNKNOWN`（保守，调用点按远程拒）。
    """
    scheme, host = _split(url)
    if scheme is None:
        return TRUST_UNKNOWN
    if scheme in _OPAQUE_SCHEMES:
        # 继承创建者；创建者不可信或未知 → 按远程（最保守）。
        return creator_trust if creator_trust in VALID_TRUST_CLASSES \
            else TRUST_REMOTE
    if scheme in _LOCAL_SCHEMES or scheme == "":
        # 无 scheme（相对 / 内联文档）视为本地合成文档。
        return TRUST_LOCAL
    if scheme in _NETWORK_SCHEMES:
        return TRUST_LOCAL if host in LOOPBACK_HOSTS else TRUST_REMOTE
    return TRUST_UNKNOWN


def origin_of(url: str) -> str:
    """URL 的源字符串 ``scheme://host[:port]``（失败退化为 ``""``）。

    用于授权白名单的键——白名单按**源**授权而不是按整条 URL，
    避免"授权了一个路径却放行整站脚本"的粒度错配。
    """
    scheme, host = _split(url)
    if not scheme or not host:
        return ""
    try:
        parts = urlsplit(url.strip())
        port = parts.port
    except Exception:
        port = None
    if port:
        return f"{scheme}://{host}:{port}"
    return f"{scheme}://{host}"


# ---------------------------------------------------------------------------
# 信任策略与闸门（第 1 点）
# ---------------------------------------------------------------------------

#: 全部禁用（无论本地还是远程）。故障排查 / 取证场景。
POLICY_DISABLE_ALL = "disable_all"

#: **默认** — 只跑本地 / 可信源脚本，远程网络页面脚本须显式授权。
#: 审计原话："安全边界完成前不得默认把任意网络页面当可信代码运行"。
POLICY_LOCAL_ONLY = "local_only"

#: 全部放行。必须经显式 :meth:`ScriptTrustGate.set_policy` 打开，
#: 打开动作进审计轨迹（不是"默认就这样"）。
POLICY_ALLOW_ALL = "allow_all"

VALID_TRUST_POLICIES = (POLICY_DISABLE_ALL, POLICY_LOCAL_ONLY,
                        POLICY_ALLOW_ALL)


@dataclass
class ScriptDecision:
    """R7-S01 — 一次"这段脚本能不能跑"的判定结果。"""

    allowed: bool
    trust: str = TRUST_UNKNOWN
    policy: str = POLICY_LOCAL_ONLY
    reason: str = ""

    def describe(self) -> str:
        verdict = "ALLOW" if self.allowed else "DENY"
        return f"{verdict}(trust={self.trust}, policy={self.policy}): {self.reason}"


class ScriptTrustGate:
    """R7-S01 第 1 点 — 远程脚本禁用开关 + 可信页面模式。

    三类能力合在一个可观测的闸门里：

    1. **全局开关**：三态策略（:data:`POLICY_DISABLE_ALL` /
       :data:`POLICY_LOCAL_ONLY`（默认）/ :data:`POLICY_ALLOW_ALL`）；
       策略变更必须带 ``reason`` 并记入审计轨迹。
    2. **可信页面模式**：按**源**显式授权（:meth:`allow_origin`），
       授权同样带 reason 入轨迹，可 :meth:`revoke_origin` 撤销。
    3. **判定**：:meth:`decide` 对每条 URL 给出 :class:`ScriptDecision`
       （含信任类与原因），调用点据此决定跑不跑。

    默认 :data:`POLICY_LOCAL_ONLY`：远程网络页面的脚本**默认不执行**，
    回环 / 本地 / ``file:`` 文档不受影响（既有离线与回环测试主链
    保持可用）。这不是"绕过"——回环是 W3C Secure Contexts 认定的
    potentially trustworthy 源，与"任意网络页面"是两类东西。
    """

    def __init__(self, policy: str = POLICY_LOCAL_ONLY) -> None:
        self._policy = policy if policy in VALID_TRUST_POLICIES \
            else POLICY_LOCAL_ONLY
        self._grants: dict[str, str] = {}      # origin → reason
        self._trail: list[dict] = []
        self.decisions = 0
        self.denied = 0
        self._trail.append({"kind": "init", "policy": self._policy})

    # -- 策略 ------------------------------------------------------------
    @property
    def policy(self) -> str:
        return self._policy

    def set_policy(self, policy: str, reason: str = "") -> bool:
        """切换全局策略。非法策略名被拒（返回 False，不改变现状）。"""
        if policy not in VALID_TRUST_POLICIES:
            return False
        if policy == self._policy:
            return True
        old = self._policy
        self._policy = policy
        self._trail.append({"kind": "policy", "from": old,
                            "to": policy, "reason": reason})
        return True

    # -- 授权白名单 ------------------------------------------------------
    def allow_origin(self, origin: str, reason: str = "") -> bool:
        """显式把某源标记为可信（"可信页面模式"）。空源被拒。"""
        if not origin:
            return False
        self._grants[origin] = reason
        self._trail.append({"kind": "grant", "origin": origin,
                            "reason": reason})
        return True

    def revoke_origin(self, origin: str) -> bool:
        if origin not in self._grants:
            return False
        del self._grants[origin]
        self._trail.append({"kind": "revoke", "origin": origin})
        return True

    def is_granted(self, origin: str) -> bool:
        return bool(origin) and origin in self._grants

    def grants(self) -> dict:
        """当前授权白名单快照 {origin: reason}。"""
        return dict(self._grants)

    # -- 判定 ------------------------------------------------------------
    def decide(self, url: str,
               creator_trust: Optional[str] = None) -> ScriptDecision:
        """判定 *url* 的脚本能否执行，返回 :class:`ScriptDecision`。"""
        self.decisions += 1
        trust = classify_url(url, creator_trust=creator_trust)

        if self._policy == POLICY_DISABLE_ALL:
            self.denied += 1
            return ScriptDecision(
                False, trust, self._policy,
                "script execution disabled globally (policy=disable_all)")

        if self._policy == POLICY_ALLOW_ALL:
            return ScriptDecision(
                True, trust, self._policy,
                "policy=allow_all (explicitly enabled; recorded in audit trail)")

        # POLICY_LOCAL_ONLY —— 默认
        if trust == TRUST_LOCAL:
            return ScriptDecision(
                True, trust, self._policy,
                "local / potentially-trustworthy origin")
        if trust == TRUST_OPAQUE:
            # 只有继承到本地信任的不透明源才放行。
            if creator_trust == TRUST_LOCAL:
                return ScriptDecision(
                    True, trust, self._policy,
                    "opaque origin inheriting local creator trust")
            self.denied += 1
            return ScriptDecision(
                False, trust, self._policy,
                "opaque origin (data:/blob:) with untrusted or unknown creator")
        # TRUST_REMOTE / TRUST_UNKNOWN —— 需显式授权
        origin = origin_of(url)
        if origin and self.is_granted(origin):
            return ScriptDecision(
                True, trust, self._policy,
                f"remote origin explicitly granted: {origin}")
        self.denied += 1
        self._trail.append({"kind": "deny", "url": url,
                            "trust": trust, "origin": origin})
        return ScriptDecision(
            False, trust, self._policy,
            "remote / unclassifiable page is not trusted by default; "
            "grant the origin explicitly to run its scripts")

    # -- 可观测性 --------------------------------------------------------
    def audit_trail(self) -> list:
        """审计轨迹（策略变更 / 授权 / 撤销 / 拒收）。有界 256 条。"""
        if len(self._trail) > 256:
            self._trail = self._trail[-256:]
        return list(self._trail)

    def describe(self) -> str:
        return (f"policy={self._policy} grants={len(self._grants)} "
                f"decisions={self.decisions} denied={self.denied}")


# ---------------------------------------------------------------------------
# 第 5 点 — 脚本隔离：不能把 spawn 写成安全沙箱
# ---------------------------------------------------------------------------

#: 脚本与 UI 同进程同线程（**当前事实**）。
ISO_NONE = "none"

#: 存在 worker 子进程且 IPC 通，但脚本仍在主线程执行（A02 现状：
#: worker 只做紧凑身份 / 命令计数，**不**执行脚本）。
ISO_WORKER_IPC = "worker_ipc"

#: 脚本在独立进程且 OS 级沙箱 / 权限收敛**经验证**。当前未实现。
ISO_OS_SANDBOX = "os_sandbox"

VALID_ISOLATION_MODES = (ISO_NONE, ISO_WORKER_IPC, ISO_OS_SANDBOX)

#: 可声明的隔离能力（未知能力一律保守按"不支持"）。
CAP_SCRIPT_IPC = "script_ipc_channel"
CAP_SCRIPT_OUT_OF_PROCESS = "script_out_of_process"
CAP_SCRIPT_SANDBOX = "script_sandbox_isolation"


@dataclass
class ScriptIsolation:
    """R7-S01 第 5 点 — 脚本隔离事实（不是声明）。"""

    mode: str = ISO_NONE
    worker_spawned: bool = False
    script_in_worker: bool = False
    os_sandbox_verified: bool = False
    note: str = ""

    @property
    def is_security_boundary(self) -> bool:
        """是否构成**安全边界**——只有 OS 沙箱经验证才为真。

        worker spawn / IPC 通**都不是**安全边界：同一台机器上父子
        进程共享文件系统与凭据，脚本若仍在主线程跑更是零隔离。
        """
        return bool(self.os_sandbox_verified) and self.mode == ISO_OS_SANDBOX

    def supports(self, capability: str) -> bool:
        """该隔离事实是否支撑 *capability*。未知能力保守 False。"""
        if capability == CAP_SCRIPT_IPC:
            return bool(self.worker_spawned)
        if capability == CAP_SCRIPT_OUT_OF_PROCESS:
            return bool(self.script_in_worker)
        if capability == CAP_SCRIPT_SANDBOX:
            return bool(self.os_sandbox_verified)
        return False

    def describe(self) -> str:
        boundary = "boundary" if self.is_security_boundary else "NOT-a-boundary"
        return (f"{self.mode}(worker_spawned={self.worker_spawned}, "
                f"script_in_worker={self.script_in_worker}, "
                f"os_sandbox_verified={self.os_sandbox_verified}) "
                f"[{boundary}]"
                + (f" {self.note}" if self.note else ""))


def detect_script_isolation(script_in_worker: bool = False,
                            os_sandbox_verified: bool = False,
                            worker_spawned: bool = False,
                            note: str = "") -> ScriptIsolation:
    """R7-S01 第 5 点 — 按**真实接线**探测脚本隔离事实。

    ``mode`` 只由"脚本在哪执行 + OS 沙箱是否验证"决定，与"有没有
    spawn 过子进程"**解耦**——这正是审计"不能把 spawn 写成安全沙箱"
    的可测表达：``worker_spawned=True`` 而 ``script_in_worker=False``
    时 mode 仍是 ``worker_ipc``，且 ``is_security_boundary`` 为假。
    """
    if os_sandbox_verified:
        mode = ISO_OS_SANDBOX
    elif worker_spawned:
        mode = ISO_WORKER_IPC
    else:
        mode = ISO_NONE
    return ScriptIsolation(mode=mode,
                           worker_spawned=bool(worker_spawned),
                           script_in_worker=bool(script_in_worker),
                           os_sandbox_verified=bool(os_sandbox_verified),
                           note=note)


@dataclass
class ScriptIsolationClaim:
    """R7-S01 第 5 点 — 一条隔离声明 + 真实性校验。

    与 :class:`ScriptIsolation`（事实）配对：声明必须**被事实支撑**，
    否则 :meth:`is_truthful` 为假并给出原因。
    """

    name: str = ""
    capability: str = ""
    isolation: Optional[ScriptIsolation] = None

    def is_truthful(self) -> bool:
        if self.isolation is None or not self.capability:
            return False
        return self.isolation.supports(self.capability)

    def untruthful_reason(self) -> str:
        if self.is_truthful():
            return ""
        if self.isolation is None:
            return "no isolation facts supplied"
        return (f"isolation {self.isolation.describe()} does not support "
                f"capability '{self.capability}'")

    def describe(self) -> str:
        verdict = "TRUTHFUL" if self.is_truthful() else "UNTRUTHFUL"
        reason = self.untruthful_reason()
        tail = f" — {reason}" if reason else ""
        return f"'{self.name}' [{verdict}]{tail}"


def claim_script_sandbox(isolation: ScriptIsolation) -> ScriptIsolationClaim:
    """审计点名场景：宣称"脚本已沙箱隔离"是否站得住。"""
    return ScriptIsolationClaim(name="script sandbox isolation",
                                capability=CAP_SCRIPT_SANDBOX,
                                isolation=isolation)


# ---------------------------------------------------------------------------
# 默认闸门（产品入口共享的同一实例）
# ---------------------------------------------------------------------------

_DEFAULT_GATE: Optional[ScriptTrustGate] = None


def default_script_trust_gate() -> ScriptTrustGate:
    """产品默认入口共用的信任闸门（惰性单例，默认 :data:`POLICY_LOCAL_ONLY`）。

    CLI（``browser.render_document_from_url``）与 GUI
    （``PageInstance.load_url``）都读这一个实例，避免"两条主链两套
    策略"（R7-A01 收敛主链的同类问题）。
    """
    global _DEFAULT_GATE
    if _DEFAULT_GATE is None:
        _DEFAULT_GATE = ScriptTrustGate()
    return _DEFAULT_GATE


def reset_default_script_trust_gate(
        policy: str = POLICY_LOCAL_ONLY) -> ScriptTrustGate:
    """重置默认闸门（测试 / 会话恢复入口），返回新实例。"""
    global _DEFAULT_GATE
    _DEFAULT_GATE = ScriptTrustGate(policy=policy)
    return _DEFAULT_GATE
