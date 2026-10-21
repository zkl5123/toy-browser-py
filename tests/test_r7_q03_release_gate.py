"""R7-Q03 — 分级发布门禁 L1 安全收口版（可判定、不冒充通过）。

Round-7 审计「分级发布门禁」L1 定义：

  > S01–S04、请求/文档生命周期门禁通过；不可信内容仍只在受限环境评估。

本模块锁定 :mod:`toy_browser.network.release_gate` 的三条验收契约：

  1. **门禁可判定**：:func:`evaluate_L1_gate` 聚合 S01–S04 + 请求/文档
     生命周期各已验收证据，产出 :class:`ReleaseGate`；默认环境下 L1
     收口成立（``l1_sealed=True``），且不可信内容确实被约束在受限
     环境（``restricted_environment=True``）。
  2. **不冒充通过**：任意一条**必备**证据非 PASS（FAIL 或
     UNSUPPORTED）都会阻断 L1 收口（保守：非 PASS 即不收口）；
     聚合器**绝不**把"未验证 / 环境受限"伪装成"通过"。
  3. **不冒充 L4**：即便 L1 收口，``l4_claimed`` 恒为 False —— L1 是
     "安全收口版"，不承诺 L2/L3/L4 动态能力，真实 OS 沙箱 / 读屏 /
     IME 独立验收，不经 L1 推断。

只用纯逻辑契约入口的返回值做行为探测：不发真实请求、不碰真实文件 /
凭据 / 破坏性操作。
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from toy_browser.network import release_gate as rg

TESTS = []


def _reg(fn):
    TESTS.append((fn.__name__, fn))
    return fn


# ---------------------------------------------------------------------------
# 静态层 — 门禁语义契约（不执行探测，锁定"不冒充"口径）
# ---------------------------------------------------------------------------

@_reg
def test_static_l1_boundary_not_l4():
    """L1 边界文案必须显式声明"安全收口"，且四档分级都在册。"""
    for tier in rg.VALID_TIERS:
        assert tier in rg.TIERS, f"分级 {tier} 必须在册"
    assert "安全收口" in rg.TIERS[rg.L1]
    # L4 档文案必须点出"真实 OS 沙箱"独立验收，L1 不推断。
    assert "OS 沙箱" in rg.TIERS[rg.L4]
    # L1 文案不得把 L1 说成"通用浏览器"（防 L1 冒充 L4）。
    assert "通用浏览器" not in rg.TIERS[rg.L1]


@_reg
def test_static_unsupported_is_not_pass():
    """UNSUPPORTED 与 FAIL 同权重：非 PASS 的必备项必阻断收口。"""
    gated = rg.ReleaseGate([
        rg.GateCheck(name="a", batch="S01", required=True,
                     result=rg.UNSUPPORTED),
    ])
    assert gated.l1_sealed is False, "UNSUPPORTED 不得被当通过"


@_reg
def test_static_fail_blocks_sealing():
    gated = rg.ReleaseGate([
        rg.GateCheck(name="a", batch="S01", required=True,
                     result=rg.FAIL),
    ])
    assert gated.l1_sealed is False, "FAIL 必备项必阻断 L1"


# ---------------------------------------------------------------------------
# 动态层 — L1 收口（默认环境）
# ---------------------------------------------------------------------------

@_reg
def test_l1_sealed_in_default_environment():
    """默认环境下 S01–S04 + 生命周期门禁全 PASS → L1 收口成立。"""
    g = rg.evaluate_L1_gate()
    assert g.l1_sealed is True, g.describe()
    # 收口的前提是不可信内容被约束在受限环境。
    assert g.restricted_environment is True, \
        "L1 收口须证明不可信内容仅在受限环境评估"
    # 没有任何必备项被阻断。
    assert g.required_failed == [], [c.name for c in g.required_failed]


@_reg
def test_l1_restricted_environment_proved_by_behavior():
    """受限环境必须用可判定行为证明（默认策略 = local_only + 远程拒）。"""
    g = rg.evaluate_L1_gate()
    by_name = {c.name: c for c in g.checks}
    assert by_name["S01:script_trust_local_only_default"].is_pass()
    assert by_name["S01:remote_default_denied"].is_pass(), \
        "远程源默认拒是受限环境的语义前提"
    assert g.restricted_environment is True


@_reg
def test_l1_does_not_claim_l4_even_when_sealed():
    """L1 收口也绝不声明 L4 通用浏览器（不冒充）。"""
    g = rg.evaluate_L1_gate()
    assert g.l1_sealed is True
    assert g.l4_claimed is False, "L1 不得隐含 L4 通用浏览器"
    # 报告里必须显式标注 L1 不承诺 L2/L3/L4。
    text = rg.describe_L1_gate(g)
    assert "L4 claimed=False" in text
    assert "不承诺" in text, "报告须显式声明 L1 不承诺 L2/L3/L4"


# ---------------------------------------------------------------------------
# 动态层 — 聚合器不冒充（人为破坏证据 → 收口被阻断）
# ---------------------------------------------------------------------------

@_reg
def test_injected_fail_blocks_sealing():
    """把一条必备证据翻成 FAIL，L1 必须收口失败（不冒充通过）。"""
    checks = []
    for probe in rg._L1_PROBES:
        checks.extend(probe())
    # 找到一条必备证据，强制翻成 FAIL。
    target = next(c for c in checks if c.required and c.is_pass())
    target.result = rg.FAIL
    target.note = "人为翻为 FAIL（验收不冒充）"
    gated = rg.ReleaseGate(checks)
    assert gated.l1_sealed is False
    assert target.name in [c.name for c in gated.required_failed]


@_reg
def test_injected_unsupported_blocks_sealing():
    """把一条必备证据翻成 UNSUPPORTED（环境受限），L1 同样不收口。"""
    checks = []
    for probe in rg._L1_PROBES:
        checks.extend(probe())
    target = next(c for c in checks if c.required and c.is_pass())
    target.result = rg.UNSUPPORTED
    target.note = "环境受限，未完成探测（不冒充通过）"
    gated = rg.ReleaseGate(checks)
    assert gated.l1_sealed is False, "UNSUPPORTED 不得被当通过"
    assert target.name in [c.name for c in gated.required_failed]


@_reg
def test_optional_fail_does_not_block_sealing():
    """非必备（观测）项 FAIL 不阻断 L1 收口（但计入报告）。"""
    checks = [
        rg.GateCheck(name="req", batch="S01", required=True,
                     result=rg.PASS),
        rg.GateCheck(name="obs", batch="S01", required=False,
                     result=rg.FAIL),
    ]
    gated = rg.ReleaseGate(checks)
    assert gated.l1_sealed is True, "非必备 FAIL 不应阻断 L1"
    assert gated.required_failed == []


@_reg
def test_probe_coverage_anchors_all_batches():
    """L1 探测须覆盖 S01–S04 + 请求/文档生命周期（不漏批次）。"""
    g = rg.evaluate_L1_gate()
    batches = {c.batch for c in g.checks}
    for b in ("S01", "S02", "S03", "S04", "LIFECYCLE"):
        assert b in batches, f"L1 探测未覆盖批次 {b}：{batches}"
    # 必备证据数量 ≥ 各批次关键项（保守下限，防止探测缩水）。
    required = [c for c in g.checks if c.required]
    assert len(required) >= 8, f"必备证据过少：{len(required)}"


# ---------------------------------------------------------------------------
# 动态层 — 报告 / 结构化快照可判
# ---------------------------------------------------------------------------

@_reg
def test_report_lines_readable():
    g = rg.evaluate_L1_gate()
    lines = g.report_lines()
    joined = "\n".join(lines)
    assert "L1" in joined
    assert "SEALED" in joined or "NOT-SEALED" in joined
    # 每条检查都有可读行。
    assert len([ln for ln in lines if "=" in ln]) >= len(g.checks)


@_reg
def test_to_dict_is_snapshot():
    g = rg.evaluate_L1_gate()
    d = g.to_dict()
    assert d["l1_sealed"] is True
    assert d["l4_claimed"] is False
    assert d["restricted_environment"] is True
    assert isinstance(d["checks"], list) and len(d["checks"]) >= 8
    # 快照里每条检查都带 result（可被下游断言）。
    assert all("result" in c for c in d["checks"])


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
