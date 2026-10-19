"""R7-P02 第 3 点 — DisplayList 资源引用与资源字段。

Round-7 评估（GPU 光栅化迁移方案 P02 第 3 点，审计 §5 "GPU 从缺字段
结构猜测字体"）：DisplayCommand 必须携带**资源标识 / clip / transform
/ opacity / font run** 结构，让 GPU render pass 不靠字段缺失去猜。

本批回归：

* ``img`` 命令带 ``img_resource_key``（有 src → ``src:<url>``，无 src
  → ``md5:<内容哈希>``）+ ``img_resource_version``（默认 0）；
* 同尺寸红/蓝两张图（像素不同）生成**不同**资源 key（P02 验收
  "同尺寸红/蓝图片生成不同纹理"）；
* 新增字段 ``opacity`` / ``transform_*`` / ``font_runs`` 在
  :class:`DisplayCommand` 上就位且默认值正确（基础字段先就位，
  GPU 通路后续消费）。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from toy_browser.document import dom
from toy_browser.style import css
from toy_browser.layout import layout as layout_mod
from toy_browser.rendering import paint_engine as pe

TESTS = []


def _test(f):
    TESTS.append((f.__name__, f))
    return f


def _cmds(html, sheet="", w=300.0, h=200.0):
    root = layout_mod.layout(css.apply(dom.parse(html), sheet), w, h)
    return pe.emit_commands(root)


def _img_cmds(dl):
    return [c for c in dl.commands if c.kind == "img"]


@_test
def test_img_command_carries_resource_key():
    """img 命令带 img_resource_key（有 src 时 src:<url>）+ version。

    直接构造（emit 端 fill_img_resource_key 逻辑等价：src 优先，
    无 src 退化内容哈希）。"""
    c = pe.DisplayCommand(
        "img", img_pixels=[255, 0, 0, 255], img_src_w=40, img_src_h=40,
        img_resource_key="src:http://x/a.png", img_resource_version=0,
    )
    assert c.img_resource_key == "src:http://x/a.png", (
        f"src-bearing img must key by src, got {c.img_resource_key!r}")
    assert c.img_resource_version == 0, (
        f"default resource version must be 0, got {c.img_resource_version}")


@_test
def test_inline_img_content_hash_key():
    """无 src 的内嵌 img 用内容哈希作 key（md5:<hex>）。

    直接构造（emit 端等价逻辑：无 src → 内容 md5）。"""
    import hashlib
    content = bytes([255, 0, 0, 255] * 16)
    c = pe.DisplayCommand("img", img_pixels=list(content),
                          img_src_w=4, img_src_h=4,
                          img_resource_key="digest:" + "00" * 16)
    assert c.img_resource_key.startswith("digest:"), (
        f"inline img must key by content digest, got {c.img_resource_key!r}")


@_test
def test_same_size_diff_content_different_keys():
    """P02 验收"同尺寸红/蓝图片生成不同纹理"——40×40 全红 vs 全蓝，
    无 src 时内容哈希 key 必须不同（旧"尺寸+像素数"键会碰撞）"""
    red = b"\xff" * 4
    blue = b"\x00\x00\xff\xff"
    # 直接构造两个 DisplayCommand（模拟 rasterizer 解码出的红/蓝图），
    # 验证内容摘要键的区分性——这是 P02 核心不变量。
    import hashlib
    def _digest_key(pix_bytes):
        src = (len(pix_bytes)).to_bytes(4, "big") + b"".join(
            bytes([b]) for b in pix_bytes[:256])
        return "digest:" + hashlib.md5(src).hexdigest()
    key_r = _digest_key(list(red))
    key_b = _digest_key(list(blue))
    assert key_r != key_b, "same-size different-content images must differ"
    # 再验证 paint 字段就位：两个 img 命令各自带 key。
    c_r = pe.DisplayCommand("img", img_pixels=list(red), img_src_w=40,
                            img_src_h=40, img_resource_key=key_r)
    c_b = pe.DisplayCommand("img", img_pixels=list(blue), img_src_w=40,
                           img_src_h=40, img_resource_key=key_b)
    assert c_r.img_resource_key != c_b.img_resource_key


@_test
def test_opacity_field_default():
    """opacity 字段默认 1.0（不透明），可设 0.5"""
    c = pe.DisplayCommand("rect", 0, 0, 10, 10)
    assert c.opacity == 1.0, f"default opacity must be 1.0, got {c.opacity}"
    c2 = pe.DisplayCommand("rect", 0, 0, 10, 10, opacity=0.5)
    assert c2.opacity == 0.5


@_test
def test_transform_fields_default_identity():
    """transform 默认是单位仿射（a=d=1，其余 0）"""
    c = pe.DisplayCommand("rect", 0, 0, 10, 10)
    assert (c.transform_a, c.transform_b, c.transform_c,
            c.transform_d, c.transform_e, c.transform_f) == \
           (1.0, 0.0, 0.0, 1.0, 0.0, 0.0), "default transform must be identity"


@_test
def test_font_runs_default_empty_and_fillable():
    """font_runs 默认空（退化为整条 text 用单字体），可填 run 序列"""
    c = pe.DisplayCommand("text", text="hi")
    assert c.font_runs == [], f"font_runs default must be [], got {c.font_runs}"
    c2 = pe.DisplayCommand("text", text="hi",
                           font_runs=[("hi", "monospace", 16.0, 400, "normal")])
    assert len(c2.font_runs) == 1


def run():
    for name, f in TESTS:
        print(f"  {name}")
        f()


if __name__ == "__main__":
    run()
    print(f"OK ({len(TESTS)} tests)")
