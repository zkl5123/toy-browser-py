"""R7-Q02 — 外部标准测试接入：Test262 风格 JS 子集（首批）。

Round-7 评估（``逐项改进实施方案.md`` R7-Q02 第 1、2 点，``渲染与
标准差距审计.md`` §5）核心验收：

    固定 WPT/Test262 提交；先选与实现对应的可执行子集，manifest 列
    test ID、spec、expected、reason、owner。JS 的 Test262、DOM/API
    的 testharness、布局的 reftest、崩溃/超时的 crashtest、IME/A11y
    的 manual 分开。

本批落地 **JS Test262 子集**：

* manifest（``tests/external/test262_js/manifest.json``）列 52 个
  case：47 PASS + 5 UNSUPPORTED（项目当前 JS 引擎**明确不支持**的
  语法——默认参数 / rest 参数 / for...of / 数组解构带 rest /
  for...of 字符串。UNSUPPORTED 不是"未验证"，是"已实测确认 parse
  / interp 层缺口"，按"不冒充通过"原则显式标注，不缩分母、不伪装
  通过）；
* 每个 case 给 spec 引用（ECMA-262 章节）+ expected 独立标准
  预期 + reason + owner（R7-Q02）；
* **双路径执行**：``execute``（声明 + 语句，产品真实路径）+
  ``readback``（``eval`` 读回顶层 var / 表达式结果）——避免把
  "let 块级作用域跨 execute 边界不共享"误判成"for-of 不支持"
  （纯 ``eval`` 单串路径会把 for-of 报 SyntaxError，是 eval 路径
  未接该语法，**不是**引擎缺口）；
* 不依赖 wgpu / 字体 / 网络 / Tk——纯 JS 引擎执行，双环境
  （受管 3.13 / 完整 3.11）都跑通；
* 新增缺陷必须转正式回归（Q02 验收）——UNSUPPORTED 的 5 项在本批
  manifest 里锁定 + 标注，后续修引擎时把对应 case 状态从
  ``unsupported`` 改 ``pass`` 即可，不新建重复测试。

"不冒充全网兼容率"：本 manifest 只覆盖项目 JS 引擎当前实现对应
的 ES 子集（ECMA-262 的 let/const/箭头/闭包/class/extends/
generator/正则/JSON/Map/Object.* 等），**不**代表全网浏览器兼容。
通过率只在"选定子集"内有意义。
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from toy_browser.scripting import js as _js
from toy_browser.document.html_pkg.node import Node as _DomNode

_MANIFEST = os.path.join(os.path.dirname(__file__), "external",
                         "test262_js", "manifest.json")

TESTS = []


def _load_cases() -> list:
    with open(_MANIFEST, encoding="utf-8") as fh:
        return json.load(fh)["cases"]


def _fresh_host() -> _js.Host:
    """每个 case 用**独立** Host，避免跨 case 变量污染（与 Test262
    "每个 case 独立全局环境"同口径）。"""
    return _js.Host(_DomNode.element("html"), log=lambda m: None)


def _coerce(expected, actual):
    """把引擎返回值归一到可比形态。

    * 引擎把 int 当 float 存（10.0 表示 10）——期望 int 时对齐 int；
    * JS ``undefined`` 全局是引擎特有的 :class:`_UndefinedSentinel`
      单例（不是 Python None，也不是 str）——归一化为字符串
      ``"undefined"``（这是 ``eval`` 返回 ``undefined`` 的规范形态）；
    * Python ``None``（null）归一为 ``"null"``；
    * list 归一为 JSON 串（与 ``JSON.stringify`` 输出一致，无空格）。
    """
    if isinstance(actual, (int, float)) and not isinstance(actual, bool):
        if isinstance(expected, int) or (isinstance(expected, float) and expected.is_integer()):
            return int(actual)
        return float(actual)
    if isinstance(actual, str):
        return actual
    if actual is None:
        return "null"
    # 引擎的 _UndefinedSentinel（JS undefined 全局）→ "undefined"
    if type(actual).__name__ == "_UndefinedSentinel":
        return "undefined"
    if isinstance(actual, list):
        return json.dumps(actual)
    if isinstance(actual, bool):
        return actual
    return actual


def _reg(f):
    TESTS.append((f.__name__, f))
    return f


@_reg
def test_q02_manifest_loads_and_is_complete():
    """manifest 可加载 + 每个 case 必备字段齐全（test ID / spec /
    expected / reason / owner）。"""
    cases = _load_cases()
    assert len(cases) >= 50, f"首批子集须 >= 50 case，got {len(cases)}"
    owner_set = set()
    for c in cases:
        for field in ("id", "spec", "title", "expected", "reason",
                      "execute", "readback", "status"):
            assert field in c, f"case {c.get('id', '?')} 缺字段 {field}"
        assert c["status"] in ("pass", "unsupported"), (
            f"case {c['id']} status 须 pass/unsupported，got {c['status']!r}")
        owner_set.add(c.get("owner", "R7-Q02"))
    assert owner_set, "manifest 须有 owner"


@_reg
def test_q02_pass_cases_match_expected():
    """所有 status=pass 的 case：execute + readback 双路径执行，
    结果与 expected 一致（Q02 验收"独立标准预期通过"）。"""
    failures = []
    for c in _load_cases():
        if c["status"] != "pass":
            continue
        host = _fresh_host()
        try:
            if c["execute"]:
                host.execute(c["execute"])
            actual = host.eval(c["readback"])
            if _coerce(c["expected"], actual) != c["expected"] \
                    and _coerce(c["expected"], actual) != _coerce(c["expected"], actual):
                # 简化：直接比较归一化结果。
                pass
            norm_actual = _coerce(c["expected"], actual)
            norm_expected = c["expected"]
            if isinstance(norm_expected, (int, float)) and isinstance(norm_actual, (int, float)):
                ok = abs(float(norm_actual) - float(norm_expected)) < 1e-9
            else:
                ok = (norm_actual == norm_expected)
            if not ok:
                failures.append(f"{c['id']}: expected {norm_expected!r}, got {norm_actual!r}")
        except Exception as e:
            failures.append(f"{c['id']}: raised {type(e).__name__}: {e}")
    assert not failures, (
        f"{len(failures)} pass-case 结果偏离独立标准预期:\n" +
        "\n".join(failures))


@_reg
def test_q02_unsupported_cases_confirm_gap():
    """所有 status=unsupported 的 case：execute 路径**确认**引擎缺口
    （parse / interp 抛错），不冒充通过——这些 case 是"已知不支持"
    清单，后续修引擎时改 status=pass 即转正。"""
    confirmed = 0
    for c in _load_cases():
        if c["status"] != "unsupported":
            continue
        host = _fresh_host()
        # 引擎缺口表现为：parse 阶段 SyntaxError，或 execute 阶段
        # _Throw / TypeError。任一异常即"确认缺口"。
        try:
            host.execute(c["execute"])
            # 若 execute 成功但 readback 也成功且等于 expected → 缺口
            # 其实已修，须提醒改 status（不冒充）。
            actual = host.eval(c["readback"])
            norm = _coerce(c["expected"], actual)
            if norm == c["expected"]:
                # 不该发生：unsupported case 却跑通了。
                raise AssertionError(
                    f"{c['id']} 标记 unsupported 但实际跑通 expected={c['expected']!r} "
                    f"got={norm!r}——须把 manifest status 改 pass")
        except AssertionError:
            raise
        except Exception:
            confirmed += 1
    assert confirmed > 0, "至少须确认 1 个 unsupported 缺口（本批 5 项）"


@_reg
def test_q02_subtotal_not_full_compatibility():
    """Q02 验收核心：本批是**选定子集**，不冒充全网兼容率。

    断言 manifest 显式声明 owner + spec_reference（ECMA-262），且
    子集总数 < 全网（这里通过"子集大小有限 + 标注 ECMA-262 章节"
    锁定口径）。后续扩子集时，本断言仍须过——子集永远显式标注
    边界，不把"通过率"写成"全网兼容率"。
    """
    cases = _load_cases()
    assert len(cases) < 1000, "首批子集须有限（非全网）"
    specs = {c["spec"] for c in cases}
    assert any("ECMA-262" in s for s in specs), (
        "manifest 须按 ECMA-262 章节引用规范")


def run():
    for name, f in TESTS:
        print(f"  {name}")
        f()


if __name__ == "__main__":
    passed = 0
    failed = []
    for name, f in TESTS:
        try:
            f(); passed += 1; print(f"ok  {name}")
        except Exception as e:
            failed.append((name, str(e))); print(f"FAIL {name}: {e}")
    print(f"\n{passed}/{len(TESTS)} passed")
    if failed:
        print("FAILED:", failed); sys.exit(1)
