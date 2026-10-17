"""批次 22 — C01 GUI 接入桥 + G10 诊断区面板 回归。

被测：``toy_browser/app/gui_document_bridge.py``。
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from toy_browser.app.gui_document_bridge import (
    GuiDocumentBridge, DiagnosticPanel, export_diagnostic_snapshot,
)
from toy_browser.app.diagnostics import DiagnosticsLog

TESTS = []
def _reg(fn):
    TESTS.append((fn.__name__, fn))
    return fn


# ---------------------------------------------------------------------------
# C01 step 4 — GuiDocumentBridge 生命周期
# ---------------------------------------------------------------------------

@_reg
def test_bridge_initial_state():
    """未初始化时：document 存在、initialized=False、版本全 0。"""
    b = GuiDocumentBridge()
    assert b.initialized is False
    snap = b.version_snapshot()
    assert snap["script_init_count"] == 0
    assert snap["initialized"] is False


@_reg
def test_bridge_begin_document():
    """begin_document：脚本初始化计数 +1、initialized=True。"""
    b = GuiDocumentBridge()
    b.begin_document(dom_tree=None, script_host=None,
                     base_url="http://example.com")
    assert b.initialized is True
    assert b.version_snapshot()["script_init_count"] == 1


@_reg
def test_bridge_restate_no_script_reinit():
    """restate：只 bump layout_revision，脚本初始化计数不变（C01 验收）。"""
    b = GuiDocumentBridge()
    b.begin_document(dom_tree=None, script_host=None)
    before = b.version_snapshot()
    b.restate()
    after = b.version_snapshot()
    assert after["script_init_count"] == before["script_init_count"] == 1
    # layout_revision 应增加
    assert after["layout_revision"] > before["layout_revision"]


@_reg
def test_bridge_render_bumps_frame():
    """render：只 bump frame_id。"""
    b = GuiDocumentBridge()
    b.begin_document(dom_tree=None, script_host=None)
    before = b.version_snapshot()
    b.render()
    after = b.version_snapshot()
    assert after["frame_id"] > before["frame_id"]
    assert after["script_init_count"] == 1


@_reg
def test_bridge_restate_requires_init():
    """restate 未初始化 → ValueError。"""
    b = GuiDocumentBridge()
    try:
        b.restate()
        assert False, "should raise"
    except ValueError:
        pass


@_reg
def test_bridge_navigate_new_context():
    """navigate：创建新 DocumentState，旧上下文 disposed。"""
    b = GuiDocumentBridge()
    b.begin_document(dom_tree=None, script_host=None)
    old_doc = b.document
    new_doc = b.navigate()
    assert old_doc.disposed is True
    assert new_doc.disposed is False
    assert b.initialized is False


@_reg
def test_bridge_node_id_stable():
    """node_id：元素索引 → 稳定 id（重排后仍有效）。"""
    b = GuiDocumentBridge()
    b.begin_document(dom_tree=None, script_host=None)
    nid = b.node_id(5)
    b.restate()
    assert b.node_id(5) == nid


@_reg
def test_bridge_sync_page_state():
    """sync_page_state：hover/focus/form → 稳定 node_id。"""
    b = GuiDocumentBridge()
    b.begin_document(dom_tree=None, script_host=None)
    res = b.sync_page_state(hover_id=3, focus_id=7,
                            active_form_field=2)
    assert res["hover_node_id"] is not None
    assert res["focus_node_id"] is not None
    assert res["form_field_node_id"] is not None


@_reg
def test_bridge_sync_page_state_none():
    """sync_page_state 传 None → 对应 node_id 为 None。"""
    b = GuiDocumentBridge()
    b.begin_document(dom_tree=None, script_host=None)
    res = b.sync_page_state()
    assert res["hover_node_id"] is None
    assert res["focus_node_id"] is None


# ---------------------------------------------------------------------------
# G10 step 4 — DiagnosticPanel 展开 / 收起
# ---------------------------------------------------------------------------

@_reg
def test_panel_default_collapsed():
    """诊断区默认收起（普通使用不堆放内核信息）。"""
    p = DiagnosticPanel()
    assert p.expanded is False
    assert p.panel_height == 0
    assert p.visible_events() == []


@_reg
def test_panel_toggle():
    """toggle：展开 → 收起 → 展开。"""
    p = DiagnosticPanel()
    assert p.toggle() is True
    assert p.expanded is True
    assert p.panel_height > 0
    assert p.toggle() is False
    assert p.expanded is False
    assert p.panel_height == 0


@_reg
def test_panel_open_close():
    """open / close 显式切换。"""
    p = DiagnosticPanel()
    p.open()
    assert p.expanded is True
    p.close()
    assert p.expanded is False


@_reg
def test_panel_event_filter_by_document():
    """visible_events 按 document_id 过滤。"""
    log = DiagnosticsLog()
    log.start_document(1)
    log.record(1, "nav", ts_ms=0)
    log.record(1, "parse", ts_ms=5)
    log.start_document(2)
    log.record(2, "nav", ts_ms=0)
    p = DiagnosticPanel(log=log, expanded=True,
                       visible_document_id=1)
    evs = p.visible_events()
    assert len(evs) == 2
    assert all(e.document_id == 1 for e in evs)


@_reg
def test_panel_event_filter_all():
    """visible_document_id=None → 显示全部事件。"""
    log = DiagnosticsLog()
    log.start_document(1)
    log.record(1, "nav", ts_ms=0)
    log.start_document(2)
    log.record(2, "nav", ts_ms=0)
    p = DiagnosticPanel(log=log, expanded=True,
                       visible_document_id=None)
    assert len(p.visible_events()) == 2


@_reg
def test_panel_collapsed_no_events():
    """收起态 → visible_events 恒空（不显示内核信息）。"""
    log = DiagnosticsLog()
    log.start_document(1)
    log.record(1, "nav", ts_ms=0)
    p = DiagnosticPanel(log=log, expanded=False,
                        visible_document_id=1)
    assert p.visible_events() == []


@_reg
def test_panel_layout_spec():
    """layout_spec：返回诊断区布局规格 dict。"""
    p = DiagnosticPanel(expanded=True)
    spec = p.layout_spec()
    assert spec["expanded"] is True
    assert spec["panel_height"] > 0
    assert "phases" in spec
    assert spec["backend"] == "cpu"
    assert "event_count" in spec


@_reg
def test_panel_close_no_side_effect():
    """G10 验收 — 关闭诊断区不改变 C01 文档版本（无副作用）。"""
    b = GuiDocumentBridge()
    b.begin_document(dom_tree=None, script_host=None)
    before = b.version_snapshot()
    p = DiagnosticPanel()
    p.open()
    p.close()
    after = b.version_snapshot()
    assert p.assert_close_no_side_effect(before, after) is True


@_reg
def test_panel_close_no_side_effect_detects_change():
    """版本确实变化时 → assert_close_no_side_effect 返回 False。"""
    p = DiagnosticPanel()
    before = {"document_id": 1, "navigation_epoch": 1,
              "layout_revision": 1, "frame_id": 1,
              "script_init_count": 1}
    after = {"document_id": 1, "navigation_epoch": 1,
             "layout_revision": 2, "frame_id": 1,
             "script_init_count": 1}
    assert p.assert_close_no_side_effect(before, after) is False


# ---------------------------------------------------------------------------
# G10 step 5 — 诊断导出
# ---------------------------------------------------------------------------

@_reg
def test_export_snapshot_json():
    """export_diagnostic_snapshot：返回 JSON 文本（含 backend / 阶段 /
    事件）。"""
    log = DiagnosticsLog()
    log.start_document(1)
    log.record(1, "nav", ts_ms=0)
    log.record(1, "parse", ts_ms=10, detail="ok")
    p = DiagnosticPanel(log=log, expanded=True,
                       visible_document_id=1)
    text = export_diagnostic_snapshot(p, document_id=1)
    import json
    data = json.loads(text)
    assert data["document_id"] == 1
    assert data["backend"] == "cpu"
    assert data["event_count"] >= 2
    assert len(data["events"]) >= 2


@_reg
def test_export_snapshot_empty_doc():
    """导出空文档 → 事件列表为空、不抛异常。"""
    p = DiagnosticPanel(expanded=True, visible_document_id=99)
    text = export_diagnostic_snapshot(p, document_id=99)
    import json
    data = json.loads(text)
    assert data["event_count"] == 0
    assert data["events"] == []


if __name__ == "__main__":
    passed = 0
    failed = []
    for name, fn in TESTS:
        try:
            fn(); passed += 1; print(f"ok  {name}")
        except Exception as e:
            failed.append((name, str(e))); print(f"FAIL {name}: {e}")
    print(f"\n{passed}/{len(TESTS)} passed")
    if failed:
        print("FAILED:", failed); sys.exit(1)
