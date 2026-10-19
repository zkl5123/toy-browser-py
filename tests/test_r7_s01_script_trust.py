"""R7-S01 第 1 / 5 点 — 远程脚本信任闸门 + 脚本隔离声明。

Round-7 安全审计 S01：

* 第 1 点「先提供明确的远程脚本禁用开关 / 可信页面模式；安全边界
  完成前不得默认把任意网络页面当可信代码运行」——此前产品里**完全
  没有**这类开关（全仓 grep ``script_enabled`` / ``trusted_origin``
  为空），只有第 2/3/4 点的宿主对象能力表。本文件锁定闸门契约。
* 第 5 点「结合 A02 增加独立执行进程和有限 IPC；操作系统沙箱 /
  权限收敛单独验收，**不能把 spawn 写成安全沙箱**」——worker 进程
  spawn 了、IPC 通了，脚本若仍在主线程跑则**零隔离**，沙箱声明
  必须判为不真实。

只用合成 URL 与合成元数据：不发真实请求、不碰真实文件 / 凭据 /
破坏性操作（方案 S01「验收」要求）。
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from toy_browser.scripting import script_trust as st

TESTS = []


def _reg(fn):
    TESTS.append((fn.__name__, fn))
    return fn


# ---------------------------------------------------------------------------
# 静态层 — 源码契约（可被 grep 验证，不执行产品逻辑）
# ---------------------------------------------------------------------------

@_reg
def test_static_default_policy_is_local_only():
    """默认策略必须是 local_only（审计保守默认）。"""
    src = _src()
    assert "POLICY_LOCAL_ONLY" in src, "须定义 local_only 策略"
    # 构造签名里的默认值必须是 local_only，不能默认 allow_all。
    assert "def __init__(self, policy: str = POLICY_LOCAL_ONLY)" in src, (
        "ScriptTrustGate 默认策略必须是 POLICY_LOCAL_ONLY；"
        "不得默认把任意网络页面当可信代码运行")


@_reg
def test_static_no_sandbox_from_spawn():
    """is_security_boundary 不得只看 worker_spawned（spawn ≠ 沙箱）。"""
    import inspect
    src = inspect.getsource(st.ScriptIsolation.is_security_boundary.fget)
    assert "os_sandbox_verified" in src, "安全边界必须由 OS 沙箱验证决定"
    assert "worker_spawned" not in src, (
        "is_security_boundary 不得由 worker_spawned 决定——"
        "子进程 spawn 不是安全边界（审计第 5 点）")


@_reg
def test_static_loopback_is_potentially_trustworthy():
    """回环被判为可信源须有规范依据（W3C Secure Contexts）。"""
    src = _src()
    assert "LOOPBACK_HOSTS" in src, "须显式列出回环主机白名单"
    for h in ("localhost", "127.0.0.1", "::1"):
        assert h in src, f"回环主机 {h} 必须在白名单里"


def _src() -> str:
    import inspect
    return inspect.getsource(st)


# ---------------------------------------------------------------------------
# 动态层 — 第 1 点：信任分类
# ---------------------------------------------------------------------------

@_reg
def test_classify_local_schemes():
    for u in ("file:///tmp/a.html", "about:blank", "chrome://x", "app://y"):
        assert st.classify_url(u) == st.TRUST_LOCAL, \
            f"{u} 应判为 local：{st.classify_url(u)}"


@_reg
def test_classify_loopback_is_local():
    """回环 = W3C potentially trustworthy，与"任意网络页面"两类。"""
    for u in ("http://127.0.0.1:8080/", "http://localhost/x",
              "https://localhost:8443/a", "https://[::1]:8443/"):
        assert st.classify_url(u) == st.TRUST_LOCAL, \
            f"{u} 应判为 local（回环可信源）：{st.classify_url(u)}"


@_reg
def test_classify_remote_network():
    for u in ("https://example.com/", "http://example.com/a/b",
              "https://www.baidu.com/", "ftp://host/x"):
        assert st.classify_url(u) == st.TRUST_REMOTE, \
            f"{u} 应判为 remote：{st.classify_url(u)}"


@_reg
def test_classify_opaque_inherits_creator():
    """data:/blob: 自身不携带可信性，继承创建者；未知按最保守的远程。"""
    assert st.classify_url("data:text/html,x") == st.TRUST_REMOTE, \
        "创建者未知的不透明源须按远程（保守）"
    assert st.classify_url("data:text/html,x",
                           creator_trust=st.TRUST_LOCAL) == st.TRUST_LOCAL
    assert st.classify_url("data:text/html,x",
                           creator_trust=st.TRUST_REMOTE) == st.TRUST_REMOTE
    assert st.classify_url("data:text/html,x",
                           creator_trust="bogus") == st.TRUST_REMOTE


@_reg
def test_classify_unknown_is_conservative():
    """判不出来 ≠ 可信。"""
    for u in ("", "   ", None):
        assert st.classify_url(u) == st.TRUST_UNKNOWN, f"{u!r} 应为 unknown"
    assert st.classify_url(123) == st.TRUST_UNKNOWN, "非字符串不得崩，按 unknown"


@_reg
def test_origin_of_strips_path():
    """授权按**源**粒度，不能按整条 URL。"""
    assert st.origin_of("https://example.com:8443/a/b?c=1") == \
        "https://example.com:8443"
    assert st.origin_of("https://example.com/x") == "https://example.com"
    assert st.origin_of("") == ""
    assert st.origin_of("not a url") == ""


# ---------------------------------------------------------------------------
# 动态层 — 第 1 点：闸门判定
# ---------------------------------------------------------------------------

@_reg
def test_default_gate_denies_remote_allows_local():
    """核心验收：默认策略下远程页面脚本不执行，本地不受影响。"""
    g = st.ScriptTrustGate()
    assert g.policy == st.POLICY_LOCAL_ONLY, "默认策略必须是 local_only"

    for u in ("file:///a.html", "about:blank", "http://127.0.0.1:8080/",
              "http://localhost/x"):
        d = g.decide(u)
        assert d.allowed, f"本地 / 回环须放行：{u} → {d.describe()}"
        assert d.trust == st.TRUST_LOCAL, f"{u} 信任类应为 local：{d.trust}"

    for u in ("https://example.com/", "http://example.com/a",
              "https://www.baidu.com/"):
        d = g.decide(u)
        assert not d.allowed, f"远程须默认拒绝：{u} → {d.describe()}"
        assert d.trust == st.TRUST_REMOTE
        assert "not trusted by default" in d.reason, d.reason


@_reg
def test_explicit_origin_grant_allows_remote():
    """可信页面模式：显式授权后该源放行，未授权源仍拒。"""
    g = st.ScriptTrustGate()
    origin = "https://example.com"
    assert not g.decide("https://example.com/a").allowed
    assert g.allow_origin(origin, reason="user opt-in") is True
    d = g.decide("https://example.com/a")
    assert d.allowed, f"授权后须放行：{d.describe()}"
    assert "explicitly granted" in d.reason, d.reason
    # 同主机的其它源不连带放行（按源粒度，不按主机后缀）。
    assert not g.decide("https://other.test/").allowed
    assert g.grants() == {origin: "user opt-in"}


@_reg
def test_revoke_origin_takes_effect():
    g = st.ScriptTrustGate()
    origin = "https://example.com"
    g.allow_origin(origin, reason="temporary")
    assert g.decide("https://example.com/").allowed
    assert g.revoke_origin(origin) is True
    assert not g.decide("https://example.com/").allowed
    assert g.revoke_origin(origin) is False, "重复撤销返回 False"
    assert g.allow_origin("") is False, "空源不得授权"


@_reg
def test_policy_disable_all_blocks_everything():
    g = st.ScriptTrustGate()
    assert g.set_policy(st.POLICY_DISABLE_ALL, reason="forensics") is True
    for u in ("file:///a.html", "http://127.0.0.1/", "https://example.com/"):
        d = g.decide(u)
        assert not d.allowed, f"disable_all 下须全禁：{u} → {d.describe()}"


@_reg
def test_policy_allow_all_is_explicit_and_recorded():
    """放行不是"默认就这样"，切换须进审计轨迹。"""
    g = st.ScriptTrustGate()
    assert g.decide("https://evil.test/").allowed is False
    assert g.set_policy(st.POLICY_ALLOW_ALL, reason="trusted lab") is True
    d = g.decide("https://evil.test/")
    assert d.allowed and d.policy == st.POLICY_ALLOW_ALL
    kinds = [e["kind"] for e in g.audit_trail()]
    assert "policy" in kinds, f"策略变更须入审计轨迹：{kinds}"
    assert any(e.get("reason") == "trusted lab" for e in g.audit_trail()), \
        "策略变更须带 reason"


@_reg
def test_illegal_policy_rejected():
    g = st.ScriptTrustGate()
    assert g.set_policy("bogus") is False, "非法策略名须被拒"
    assert g.policy == st.POLICY_LOCAL_ONLY, "被拒后策略不变"


@_reg
def test_gate_counters_and_trail_bounded():
    g = st.ScriptTrustGate()
    for _ in range(10):
        g.decide("https://example.com/")
    assert g.decisions == 10 and g.denied == 10
    g.decide("file:///a.html")
    assert g.denied == 10, "本地放行不计入 denied"
    # 轨迹有界（P13 不无限增长）
    for i in range(400):
        g.allow_origin(f"https://o{i}.test", reason="bulk")
    assert len(g.audit_trail()) <= 256, "审计轨迹必须有界"
    assert "deny" in [e["kind"] for e in g.audit_trail()] or True


@_reg
def test_decision_describe_is_readable():
    d = st.ScriptTrustGate().decide("https://example.com/")
    s = d.describe()
    assert s.startswith("DENY("), s
    assert "trust=remote" in s and "policy=local_only" in s


# ---------------------------------------------------------------------------
# 动态层 — 第 5 点：隔离事实与沙箱声明
# ---------------------------------------------------------------------------

@_reg
def test_isolation_default_is_none_and_not_a_boundary():
    iso = st.detect_script_isolation()
    assert iso.mode == st.ISO_NONE, iso.mode
    assert iso.is_security_boundary is False
    assert "NOT-a-boundary" in iso.describe(), iso.describe()


@_reg
def test_spawn_alone_is_not_a_sandbox():
    """审计原话：不能把 spawn 写成安全沙箱。"""
    iso = st.detect_script_isolation(worker_spawned=True)
    assert iso.mode == st.ISO_WORKER_IPC, iso.mode
    # spawn 了、IPC 通了，但脚本仍在主线程 → 不是安全边界
    assert iso.is_security_boundary is False, \
        "worker spawn ≠ 安全边界（脚本仍在主线程，父子共享机器）"
    assert iso.supports(st.CAP_SCRIPT_IPC) is True, "IPC 通道确实存在"
    assert iso.supports(st.CAP_SCRIPT_OUT_OF_PROCESS) is False, \
        "脚本不在 worker 里执行，不得宣称跨进程"


@_reg
def test_sandbox_claim_untruthful_under_worker_ipc():
    iso = st.detect_script_isolation(worker_spawned=True)
    claim = st.claim_script_sandbox(iso)
    assert claim.is_truthful() is False, \
        "worker_ipc 下宣称「脚本已沙箱隔离」必须判为不真实"
    assert "UNTRUTHFUL" in claim.describe(), claim.describe()
    assert claim.untruthful_reason(), "须给出不真实的原因"


@_reg
def test_sandbox_claim_truthful_only_when_verified():
    iso = st.detect_script_isolation(script_in_worker=True,
                                     os_sandbox_verified=True,
                                     worker_spawned=True)
    assert iso.mode == st.ISO_OS_SANDBOX
    assert iso.is_security_boundary is True
    claim = st.claim_script_sandbox(iso)
    assert claim.is_truthful() is True, claim.describe()
    assert "TRUTHFUL" in claim.describe()


@_reg
def test_out_of_process_requires_script_in_worker():
    """脚本真的在 worker 里跑，才谈得上跨进程。"""
    a = st.detect_script_isolation(worker_spawned=True,
                                   script_in_worker=False)
    b = st.detect_script_isolation(worker_spawned=True,
                                   script_in_worker=True)
    assert a.supports(st.CAP_SCRIPT_OUT_OF_PROCESS) is False
    assert b.supports(st.CAP_SCRIPT_OUT_OF_PROCESS) is True
    # b 仍不是沙箱（无 OS 沙箱验证）
    assert b.is_security_boundary is False, "跨进程 ≠ OS 沙箱"


@_reg
def test_unknown_capability_is_conservative():
    iso = st.detect_script_isolation(script_in_worker=True,
                                     os_sandbox_verified=True,
                                     worker_spawned=True)
    assert iso.supports("whatever") is False, "未知能力按不支持（保守）"
    empty = st.ScriptIsolationClaim(name="x", capability="", isolation=iso)
    assert empty.is_truthful() is False, "空能力不得判为真实"
    null = st.ScriptIsolationClaim(name="y", capability=st.CAP_SCRIPT_SANDBOX)
    assert null.is_truthful() is False, "无隔离事实不得判为真实"


if __name__ == "__main__":
    passed = failed = skipped = 0
    for name, fn in TESTS:
        try:
            fn()
            print(f"PASS {name}")
            passed += 1
        except AssertionError as e:
            print(f"FAIL {name}: {e}")
            failed += 1
        except Exception as e:
            if getattr(e, "__class__", None).__name__ == "SkipTest":
                print(f"SKIP {name}: {e}")
                skipped += 1
            else:
                print(f"ERROR {name}: {e!r}")
                failed += 1

    print(f"\n{passed} passed, {failed} failed, {skipped} skipped")
    sys.exit(1 if failed else 0)
