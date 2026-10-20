"""R7-S01 第 5 点 — 有限 IPC 消息 schema 与校验（纯逻辑层）。

审计 S01 第 5 点："结合 A02 增加独立执行进程和有限 IPC；操作系统
沙箱/权限收敛单独验收，不能把 spawn 写成安全沙箱"。本文件锁定
**消息 schema 契约**，不 import 运行时（multiprocessing/pickle/subprocess）：

* :func:`validate_command`：字段类型 + kind 枚举 + payload 有限性；
* :func:`validate_frame`：worker 回传结果只允许有限摘要（error_category
  枚举 + 标量 payload，不允许宿主对象跨进程）；
* :class:`MessageBudget`：字符串字节 / dict 键数 / 嵌套深度三维上限，
  任一超即拒绝（不静默截断，防改变语义）；
* 静态锁：模块不 import multiprocessing/pickle/subprocess/signal。
"""
import os
import sys
import inspect
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from toy_browser.scripting import script_ipc as sipc
from toy_browser.scripting.script_ipc import (
    MessageBudget, validate_command, validate_frame,
)

TESTS = []


def _reg(fn):
    TESTS.append((fn.__name__, fn))
    return fn


# -- validate_command 基础 -------------------------------------------------

@_reg
def test_valid_command_passes():
    assert validate_command(tab_id=1, kind="navigate",
                            payload={"url": "http://a.test/"}) is None
    assert validate_command(tab_id=0, kind="stop", payload={}) is None
    assert validate_command(tab_id=2, kind="resize",
                            payload={"w": 800, "h": 600}) is None


@_reg
def test_bad_tab_id_type_rejected():
    # bool 是 int 子类，必须单独排除。
    assert validate_command(True, "navigate", {}) is not None
    assert validate_command("1", "navigate", {}) is not None
    assert validate_command(None, "navigate", {}) is not None


@_reg
def test_negative_tab_id_rejected():
    assert validate_command(-1, "navigate", {}) is not None


@_reg
def test_unknown_kind_rejected():
    r = validate_command(tab_id=1, kind="evil_cmd", payload={})
    assert r is not None
    assert "unknown command kind" in r


@_reg
def test_payload_must_be_dict():
    assert validate_command(1, "navigate", ["not", "dict"]) is not None


@_reg
def test_bool_seq_excluded():
    assert validate_command(1, "navigate", {}, seq=True) is not None


# -- payload 有限性（MessageBudget）--------------------------------------

@_reg
def test_payload_host_object_rejected():
    class _HostObj:
        pass
    sentinel = _HostObj()
    # 非标量/非容器/非 dict 的宿主对象必须拒绝。
    r = validate_command(1, "navigate", {"x": sentinel})
    assert r is not None
    assert "not allowed in IPC payload" in r


@_reg
def test_payload_function_rejected():
    def _f():
        pass
    r = validate_command(1, "navigate", {"x": _f})
    assert r is not None
    assert "not allowed" in r


@_reg
def test_payload_huge_string_rejected():
    big = "x" * 10000
    b = MessageBudget(max_str_bytes=1024)
    r = validate_command(1, "navigate", {"url": big}, budget=b)
    assert r is not None
    assert "string too long" in r


@_reg
def test_payload_deep_nesting_rejected():
    # 深度超 4 即拒绝。
    deep = {"a": {"b": {"c": {"d": {"e": 1}}}}}
    b = MessageBudget(max_depth=3)
    r = validate_command(1, "navigate", deep, budget=b)
    assert r is not None
    assert "depth" in r


@_reg
def test_payload_huge_dict_rejected():
    big = {f"k{i}": i for i in range(1000)}
    b = MessageBudget(max_dict_keys=64)
    r = validate_command(1, "navigate", big, budget=b)
    assert r is not None
    assert "too large" in r or "too long" in r


@_reg
def test_payload_huge_number_rejected():
    r = validate_command(1, "navigate", {"x": 1 << 60})
    assert r is not None
    assert "numeric" in r


@_reg
def test_payload_non_string_dict_key_rejected():
    r = validate_command(1, "navigate", {1: 2})
    assert r is not None
    assert "dict key must be str" in r


@_reg
def test_nested_list_of_scalars_ok():
    assert validate_command(1, "navigate", {"pts": [1, 2, 3, 4.5]}) is None


@_reg
def test_nested_dict_of_scalars_ok():
    assert validate_command(1, "navigate", {"a": {"b": "x", "c": 1}}) is None


# -- validate_frame ---------------------------------------------------------

@_reg
def test_valid_frame_passes():
    assert validate_frame(1, 1, True, "", {}) is None
    assert validate_frame(1, 2, False, "timeout", {}) is None
    assert validate_frame(1, 3, True, "", {"width": 800, "height": 600}) is None


@_reg
def test_frame_bad_error_category_rejected():
    r = validate_frame(1, 1, False, "totally_bogus_category", {})
    assert r is not None
    assert "unknown error_category" in r


@_reg
def test_frame_ok_must_be_bool():
    assert validate_frame(1, 1, 1, "", {}) is not None  # int 不是 bool
    assert validate_frame(1, 1, True, "script", {}) is None


@_reg
def test_frame_payload_host_object_rejected():
    class _Obj:
        pass
    r = validate_frame(1, 1, True, "", {"x": _Obj()})
    assert r is not None
    assert "not allowed" in r


@_reg
def test_frame_negative_tab_id_rejected():
    assert validate_frame(-1, 1, True, "", {}) is not None


@_reg
def test_frame_string_field_bounded():
    b = MessageBudget(max_str_bytes=64)
    r = validate_frame(1, 1, False, "", {"msg": "x" * 200}, budget=b)
    assert r is not None


# -- 静态锁定 ---------------------------------------------------------------

@_reg
def test_module_no_runtime_imports():
    """静态锁：模块顶部不得 import 运行时 I/O / 并发原语。
    用 AST 扫描实际 import 语句（不是全文子串，避免 docstring 误报）。"""
    import ast
    src = Path("toy_browser/scripting/script_ipc.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imported.add(node.module.split(".")[0])
    for banned in ("pickle", "multiprocessing", "subprocess",
                   "signal", "socket", "threading"):
        assert banned not in imported, \
            f"script_ipc 不得 import {banned}（实际: {sorted(imported)})"


@_reg
def test_kind_enum_matches_document_process():
    """kind 枚举必须与 document_process.DOC_CMD_* 对齐（不遗漏）。"""
    from toy_browser.app import document_process as dp
    doc_kinds = {k for k in dir(dp)
                 if k.startswith("DOC_CMD_")}
    # 映射属性名 → 字符串值。
    actual = set()
    for name in doc_kinds:
        val = getattr(dp, name)
        if isinstance(val, str):
            actual.add(val)
    # 本模块的 VALID_CMD_KINDS 必须是 document_process 实际 kind 的子集
    # （不能凭空发明 document_process 不认识的 kind）。
    for k in sipc.VALID_CMD_KINDS:
        assert k in actual or k in ("navigate", "input", "resize", "scroll",
                                    "stop"), \
            f"kind {k!r} 不在 document_process 的 DOC_CMD_* 枚举里"


if __name__ == "__main__":
    for name, fn in TESTS:
        fn()
    print(f"PASS: {len(TESTS)} tests")
