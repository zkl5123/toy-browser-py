"""批次 29 — C02 step 4 帧版本与读写所有权 回归。

被测：``toy_browser/app/frame_ownership.py``。
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from toy_browser.app.frame_ownership import (
    OwnedBuffer, FrameVersion, OwnershipViolation,
    OWNER_WORKER, OWNER_UI, OWNER_NONE,
)
from toy_browser.rendering.cpu_image import CPUImage

TESTS = []
def _reg(fn):
    TESTS.append((fn.__name__, fn))
    return fn


def _buf(w=4, h=4):
    return OwnedBuffer(CPUImage(width=w, height=h))


# ---------------------------------------------------------------------------
# 正常生命周期（worker 写 → bump → UI 读 → release）
# ---------------------------------------------------------------------------

@_reg
def test_normal_flow():
    """完整生命周期：acquire_write → 写 → bump → request_read →
    assert_readable → release_read，无越权。"""
    buf = _buf()
    assert buf.owner == OWNER_NONE
    assert buf.acquire_write() is True
    assert buf.owner == OWNER_WORKER
    buf.image.set_pixel(0, 0, 255, 0, 0)
    v = buf.bump()
    assert v == 1
    assert buf.owner == OWNER_NONE
    ok, locked = buf.request_read()
    assert ok is True
    assert buf.owner == OWNER_UI
    assert locked.version == 1
    assert buf.assert_readable(locked) is True
    buf.release_read()
    assert buf.owner == OWNER_NONE
    assert buf.violation_count() == 0


@_reg
def test_frame_version_snapshot():
    """frame_version：记录当前版本 + 几何。"""
    buf = _buf(8, 6)
    buf.acquire_write(); buf.image.set_pixel(1, 1, 10, 20, 30); buf.bump()
    fv = buf.frame_version()
    assert fv.version == 1
    assert fv.width == 8 and fv.height == 6
    assert fv.format == "RGB8"
    # is_stale：当前版本未变 → 不陈旧；bump 后 → 陈旧
    assert fv.is_stale(buf.version) is False
    buf.acquire_write(); buf.bump()
    assert fv.is_stale(buf.version) is True


# ---------------------------------------------------------------------------
# 越权拦截（C02 step 4 "不能 UI 读取时工作线程仍在改图"）
# ---------------------------------------------------------------------------

@_reg
def test_worker_read_blocked_when_ui_holds():
    """UI 持读权时 worker 取写权 → 拒绝 + 记违规（防读到正在改的图）。"""
    buf = _buf()
    ok, _ = buf.request_read()  # UI 持读权
    assert ok is True
    assert buf.acquire_write() is False  # 被拒
    assert buf.violation_count() == 1
    v = buf.violations()[0]
    assert v.operation == "acquire_write"
    assert v.owner_at_time == OWNER_UI


@_reg
def test_ui_read_blocked_when_worker_holds():
    """worker 持写权时 UI 请求读 → 拒绝 + 记违规（防读到正在改的图）。"""
    buf = _buf()
    buf.acquire_write()  # worker 写图中
    ok, locked = buf.request_read()
    assert ok is False  # 被拒（worker 仍持写权）
    assert locked.version == buf.version  # 返回当前版本供重试
    assert buf.violation_count() == 1
    assert buf.violations()[0].operation == "request_read"
    # worker 写完 bump → UI 可成功请求
    buf.bump()
    ok2, locked2 = buf.request_read()
    assert ok2 is True
    assert locked2.version == buf.version


@_reg
def test_bump_without_write_ownership():
    """无写权却 bump（越权改图）→ 记违规、版本不变。"""
    buf = _buf()
    # 没 acquire_write 直接 bump
    v = buf.bump()
    assert v == 0  # 版本没变（越权 bump 被拒）
    assert buf.violation_count() == 1
    assert buf.violations()[0].operation == "bump"


@_reg
def test_assert_readable_stale_version():
    """assert_readable：UI 锁的版本落后于当前帧 → 校验失败（读到旧帧）。"""
    buf = _buf()
    buf.acquire_write(); buf.bump()  # v1
    ok, locked = buf.request_read()
    assert ok is True  # 锁 v1
    buf.release_read()
    buf.acquire_write(); buf.bump()  # worker 又写到 v2
    ok2, _ = buf.request_read()
    assert ok2 is True
    # 拿一个 v1 的锁定去校验（此时当前是 v2）→ 应失败
    stale = FrameVersion(version=1, width=buf.width, height=buf.height,
                         stride=buf.stride, format="RGB8")
    assert buf.assert_readable(stale) is False
    assert buf.violation_count() >= 1


@_reg
def test_assert_readable_ownership_mismatch():
    """assert_readable：所有权不是 ui（如 none）→ 校验失败。"""
    buf = _buf()
    buf.acquire_write(); buf.bump()
    fv = buf.frame_version()  # 此刻 owner=none
    assert buf.assert_readable(fv) is False  # 没 request_read 就校验
    assert buf.violation_count() == 1


# ---------------------------------------------------------------------------
# 审计
# ---------------------------------------------------------------------------

@_reg
def test_violation_audit_records():
    """violations：审计记录字段完整 + 序号递增。"""
    buf = _buf()
    buf.acquire_write()
    buf.bump()
    buf.request_read()
    buf.acquire_write()  # 违规：UI 持读权却取写权
    vs = buf.violations()
    assert len(vs) == 1
    v = vs[0]
    assert v.seq == 1
    assert v.operation == "acquire_write"
    assert v.owner_at_time == OWNER_UI
    assert "UI 仍持读权" in v.reason or "owner=none" in v.expected
    s = v.summary()
    assert "acquire_write" in s and "worker" in s or "acquire_write" in s


@_reg
def test_clear_violations():
    """clear_violations 清空审计（重置回归基线）。"""
    buf = _buf()
    buf.acquire_write()
    buf.bump()
    buf.request_read()
    buf.acquire_write()  # 违规
    assert buf.violation_count() == 1
    buf.clear_violations()
    assert buf.violation_count() == 0
    assert buf.violations() == []


@_reg
def test_reuse_after_release():
    """release_read 后可再次完整写读循环（所有权复用不串）。"""
    buf = _buf()
    for i in range(3):
        assert buf.acquire_write() is True
        buf.image.set_pixel(i % 4, i % 4, i * 10, i * 20, i * 30)
        v = buf.bump()
        assert v == i + 1
        ok, locked = buf.request_read()
        assert ok is True and locked.version == i + 1
        assert buf.assert_readable(locked) is True
        buf.release_read()
    assert buf.violation_count() == 0
    assert buf.version == 3


@_reg
def test_release_read_idempotent():
    """release_read 非 UI 持有时幂等（不记违规）。"""
    buf = _buf()
    buf.release_read()  # owner=none，空操作
    assert buf.violation_count() == 0
    buf.release_read()
    assert buf.violation_count() == 0


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
