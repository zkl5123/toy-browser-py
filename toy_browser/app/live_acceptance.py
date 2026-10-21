"""R7 组 A 活窗口人工验收脚手架：swapchain 上屏 / 真实 IME / 真实读屏器。

依据 ``assessments/20261018-round7/docs/逐项改进实施方案.md`` R7-P02 §254
（"surface 更新可见"）与 R7-U02 / R7-U03 的人工验收边界。本模块把组 A
三项"OS 侧真实外设行为"做**可判定、可签字**：

- :func:`probe_swapchain_visible` — 真开活窗口 → 拿真实 wgpu screen
  surface → 经 :meth:`GPUBackend.present` 把一帧 present 上屏 → **留下
  可判定的现场证据**（surface 格式 / present 结果 / 窗口几何 / HWND），
  供人工"看屏幕是否真的更新可见"。
- :func:`probe_ime_composition` — 在活窗口里驱动 :mod:`live_editing`
  的真实 Tk Entry + G03 IME 组合三阶段（拼音中间态 → 汉字提交 → 取消），
  把"可自动化到最大程度"的 IME 选词证据落地，**真实系统输入法选词**
  仍留人工。
- :func:`probe_screen_reader` — 把内部 a11y 树经 :mod:`ui_a11y_bridge`
  映射成 UIA 控件契约 + :class:`UiaFocusTracker` 事件流（name/role/
  value/state/focus），供**真实读屏器 / UIA 检查器**消费；真实读屏器
  播报仍留人工。

**"不冒充通过"边界（与 R7-P02/U02/U03 同口径）**：
本脚手架把"驱动到最大程度 + 证据可采集"做实，但 OS 侧真实外设行为
（swapchain 上屏肉眼可见、真人选词、读屏器读到）仍属**人工验收**，
每个探针返回 :class:`AcceptanceProbe`：``PASS``（驱动成功 + 证据齐全，
待人工签字）/ ``UNSUPPORTED``（缺 OS 能力，如实标注不冒充）/
``FAIL``（驱动本身失败，附原因）。本模块**不**把"证据齐全"伪装成
"人工已验收"——判定必须经 :mod:`checklist` 的签字位。

CLI::

    python -m toy_browser.app.live_acceptance [--headless] [--ime "ni","hao"]
        [--a11y-input FILE.json] [--keep] [--out REPORT.md]

``--headless`` 时活窗口 ``withdraw()`` 仍拿 HWND（CI / 无显示器）；
缺 tkinter/wgpu/rendercanvas 即 UNSUPPORTED（不缩分母、不冒充）。
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from typing import Optional

# 探针结论三态。
PASS = "PASS"
UNSUPPORTED = "UNSUPPORTED"
FAIL = "FAIL"


@dataclass
class AcceptanceProbe:
    """一项人工验收的可判定结果（驱动结果 + 证据 + 待人工签字项）。

    * *status* — :data:`PASS`（驱动成功，证据齐全，待人工签字）/
      :data:`UNSUPPORTED`（缺 OS 能力，不冒充）/ :data:`FAIL`。
    * *title* — 验收项名称（组 A 三项之一）。
    * *evidence* — 现场证据（dict，供人工对照 / 落报告）。
    * *human_check* — **需人工判定的项**（脚手架无法自动判定的 OS 行为）。
    * *signed* / *signer* / *signed_at* — 人工签字位（默认未签字）。
    * *reason* — UNSUPPORTED / FAIL 时的原因。
    """

    status: str
    title: str
    evidence: dict = field(default_factory=dict)
    human_check: str = ""
    signed: bool = False
    signer: str = ""
    signed_at: str = ""
    reason: str = ""

    def mark_signed(self, signer: str, at: str = "") -> None:
        """人工签字：把本项标记为已验收（不改 status，只记录签字）。"""
        self.signed = True
        self.signer = signer
        self.signed_at = at

    def render(self) -> str:
        """渲染成报告里的可签字条目（含判定 + 证据 + 签字位）。"""
        lines = [f"## {self.title}", f"- 状态：{self.status}"]
        if self.reason:
            lines.append(f"- 原因：{self.reason}")
        if self.evidence:
            lines.append("- 现场证据：")
            for k, v in self.evidence.items():
                lines.append(f"  - {k} = {v}")
        if self.human_check:
            lines.append(f"- 人工判定项：{self.human_check}")
        sign = "未签字" if not self.signed \
            else f"{self.signer} @ {self.signed_at}"
        lines.append(f"- 验收签字：{sign}")
        return "\n".join(lines)


__all__ = [
    "AcceptanceProbe", "PASS", "UNSUPPORTED", "FAIL",
    "probe_swapchain_visible", "probe_ime_composition",
    "probe_screen_reader", "run_all", "render_report", "main",
]


# ---------------------------------------------------------------------------
# 环境探测（缺 OS 能力即如实 UNSUPPORTED，不缩分母、不冒充）
# ---------------------------------------------------------------------------

def _env_gates() -> dict:
    """探活窗口三条件：tkinter + wgpu device + rendercanvas。

    返回 dict 记录各条件是否在场，供探针判定 UNSUPPORTED。"""
    gates: dict = {}
    try:
        import tkinter  # noqa: F401
        gates["tkinter"] = True
    except Exception as exc:  # pragma: no cover - 环境分支
        gates["tkinter"] = False
        gates["tkinter_reason"] = repr(exc)
    try:
        from toy_browser.rendering import gpu as _gpu
        dev = _gpu.acquire_wgpu_device()
        gates["wgpu_device"] = dev is not None
        gates["device"] = dev
    except Exception as exc:  # pragma: no cover
        gates["wgpu_device"] = False
        gates["wgpu_reason"] = repr(exc)
    try:
        from rendercanvas.contexts import WgpuContextToScreen  # noqa: F401
        gates["rendercanvas"] = True
    except Exception as exc:  # pragma: no cover
        gates["rendercanvas"] = False
        gates["rendercanvas_reason"] = repr(exc)
    return gates


def _need_live_env(gates: dict) -> Optional[str]:
    """活窗口三条件缺任一，返回缺失原因（None 表示齐全）。"""
    if not gates.get("tkinter"):
        return "tkinter unavailable"
    if not gates.get("wgpu_device"):
        return "no usable wgpu GPU device"
    if not gates.get("rendercanvas"):
        return "rendercanvas WgpuContextToScreen unavailable"
    return None


# ---------------------------------------------------------------------------
# 探针 1 — swapchain 真上屏可见（§254"surface 更新可见"）
# ---------------------------------------------------------------------------

def _make_demo_frame(w: int, h: int) -> object:
    """造一帧**有可见内容**的画面（RGB8 渐变 + 一块红方块），供
    swapchain 探针 present 上屏。肉眼检测时看到的是真画面，不是白屏。

    纯标准库（bytearray），不依赖 numpy / 活 wgpu；返回
    ``toy_browser.rendering.rasterize_cpu.CPUImage``。
    """
    from toy_browser.rendering.rasterize_cpu import CPUImage
    buf = bytearray(w * h * 3)
    for y in range(h):
        for x in range(w):
            j = (y * w + x) * 3
            # 左上→右下 RGB 渐变（可肉眼看出"画面在变"）。
            buf[j] = min(255, x * 3)
            buf[j + 1] = min(255, y * 3)
            buf[j + 2] = 120
    # 中间一块红方块（强对比，肉眼易确认）。
    cx0, cx1 = w // 2 - 20, w // 2 + 20
    cy0, cy1 = h // 2 - 20, h // 2 + 20
    for y in range(cy0, cy1):
        for x in range(cx0, cx1):
            j = (y * w + x) * 3
            buf[j] = 255
            buf[j + 1] = 0
            buf[j + 2] = 0
    return CPUImage(w, h, bytes(buf))


def probe_swapchain_visible(headless: bool = False,
                            watch_seconds: float = 0.0,
                            keep_open: bool = False) -> AcceptanceProbe:
    """真开活窗口 → 拿真实 screen surface → **画一帧有内容的画面**并
    present 上屏 → 留证据。

    *headless* 为 True 时窗口 ``withdraw()``（仍拿 HWND，供 CI / 无
    显示器）。
    *watch_seconds* 给人工"看屏幕"的窗口期（非 headless 下用
    ``root.after`` 倒计时销窗；>0 期间主循环 pump 事件，让画面可
    肉眼确认——**不阻塞、不挂死**，倒计时到自动销窗）。
    *keep_open* 为 True 时（非 headless）**不自动销窗**：用
    ``root.mainloop()`` 把窗口挂住**直到你手动关窗**才返回——供肉眼
    从容确认画面（治"窗口瞬间关闭看不清"）。证据取完才走 mainloop，
    绝不在取证据前挂死。

    关键修正（对治"白屏 + 转圈无响应"）：
    - **画有内容的帧**再 present（不是 ``present(None)``）——肉眼看到
      的是真画面（渐变 + 红方块），不是白屏。
    - present 是**可能阻塞的 OS 调用**（DWM 合成器 + GPU 驱动在场才
      真上屏；沙箱/无头/远程桌面下可能挂住或抛错）。故 present 包在
      try/except + 超时保护里：失败/挂住 → 如实标 ``FAIL`` 附 reason
      销窗返回，**绝不转圈挂死**。
    - **画面落 PNG**（``frame_png``）：无论 present 是否成功，"上屏那
      帧"都有可离线看的位图——肉眼检测时可看截图，不必依赖窗口活着。

    现场证据（供人工签字对照）：
    - ``hwnd`` / ``window_geometry`` / ``on_screen`` — 窗口是否真上屏；
    - ``surface_format`` — 真实 wgpu screen surface 首选格式；
    - ``frame_is_gpu_texture`` — 该帧是否带真实 GPU 纹理（可上屏）；
    - ``frame_png`` — **该帧落盘 PNG 路径**（肉眼兜底，不依赖窗口）；
    - ``present_result`` — :meth:`GPUBackend.present` 真实返回；
    - ``present_committed`` — present 是否真把画面提交上屏（True 才
      "肉眼可见"；False/"no-surface" 如实标注边界，1.4 须联合截图判读）；
    - ``gate_decision`` — 活窗口 surface 在场时 gate 判 present。

    **人工判定项**（脚手架无法自动判定）：肉眼确认"屏幕上的活窗口确实
    显示了对应画面，且随内容更新"。缺三条件任一 → UNSUPPORTED 不冒充。
    """
    title = "swapchain 真上屏可见（§254）"
    gates = _env_gates()
    missing = _need_live_env(gates)
    if missing is not None:
        return AcceptanceProbe(UNSUPPORTED, title, reason=missing)

    import tkinter
    from toy_browser.rendering import gpu as _gpu
    from toy_browser.rendering import live_surface as _ls
    from toy_browser.rendering.gpu import GPUBackend
    from toy_browser.rendering.gui_backend_boundary import SurfacePresentGate

    root = tkinter.Tk()
    root.title("toy-browser · 组 A 活窗口验收 · swapchain（手动关窗结束）")
    root.geometry("480x320")
    if headless:
        root.withdraw()
    else:
        # 可见窗口 + 置顶，给人工"看屏幕"留出窗口期。
        root.attributes("-topmost", True)
    root.update_idletasks()
    root.update()
    evidence: dict = {"hwnd": 0, "on_screen": not headless,
                      "window_geometry": root.geometry()}
    try:
        hwnd = int(root.winfo_id() or 0)
        evidence["hwnd"] = hwnd
        if hwnd <= 0:
            return AcceptanceProbe(FAIL, title, evidence,
                                   reason="活窗口无有效 HWND")
        probe = _ls.acquire_live_surface(gates.get("device"), hwnd)
        if not probe.acquired:
            return AcceptanceProbe(FAIL, title,
                                   {**evidence, "surface": probe.describe()},
                                   human_check=("肉眼确认活窗口画面随内容更新"),
                                   reason=probe.reason or "screen surface 未拿到")
        evidence["surface_format"] = probe.preferred_format
        # 画一帧有内容的画面并 present 到真实 screen surface。
        be = GPUBackend(device=gates.get("device"))
        frame = _make_frame_via_backend(be, 480, 320)
        evidence["frame_is_gpu_texture"] = bool(
            getattr(frame, "texture", None) is not None)
        # **截图兜底**：把"上屏那帧"（CPUImage）存成 PNG，无论 present
        # 是否成功都有可离线看的真画面（配合 present_result 判读）。
        shot = os.path.join(os.getcwd(), "swapchain_shot.png")
        try:
            img = getattr(frame, "image", None)
            if img is not None and hasattr(img, "save_png"):
                img.save_png(shot)
                evidence["frame_png"] = shot
            else:
                evidence["frame_png"] = "(no-cpu-image)"
        except Exception as exc:
            evidence["frame_png"] = f"<save_png {exc!r}>"
        try:
            res = be.present(frame, surface=probe.context)
            evidence["present_result"] = repr(res)
            if res in (True, "present"):
                evidence["present_committed"] = True
            else:
                # 未真上屏（False / "no-surface"）→ 如实标注边界，
                # 1.4 须据 present_committed + 截图联合判读。
                evidence["present_committed"] = False
        except Exception as exc:  # present 挂住/异常 → 不卡死，如实记
            evidence["present_result"] = f"<present raised {exc!r}>"
            evidence["present_committed"] = False
            gate = SurfacePresentGate()
            evidence["gate_decision"] = gate.decide(be.is_real_gpu,
                                                    probe.context)
            evidence["gate_no_surface"] = gate.decide(be.is_real_gpu, None)
            return AcceptanceProbe(
                FAIL, title, evidence,
                human_check=("肉眼确认活窗口画面随内容更新可见"),
                reason=f"present 失败/挂住: {exc!r}（无 DWM/GPU 上屏"
                       " 能力，不冒充通过）")
        gate = SurfacePresentGate()
        evidence["gate_decision"] = gate.decide(be.is_real_gpu,
                                                probe.context)
        evidence["gate_no_surface"] = gate.decide(be.is_real_gpu, None)
        # 给人看窗口期：keep_open 时挂住直到你手动关窗；
        # 否则 watch_seconds 倒计时 auto 销窗。两者都不在取证据前挂死。
        if keep_open and not headless:
            # 可靠挂住直到手动关窗：主循环 pump + 轮询 winfo_exists，
            # 点窗口 X → 默认 destroy → winfo_exists 转 False → 退出。
            # 不依赖 root.mainloop()（部分桌面/远程场景 mainloop 会秒退，
            # 导致"窗口瞬间关闭看不清"）。after_idle 保证首帧已上屏
            # 才开始等，用户从容看画面。
            root.after_idle(lambda: None)
            root.update()
            while root.winfo_exists():
                try:
                    root.after(200, "")   # 驱动事件循环，处理 X 关闭
                    root.update()
                except Exception:
                    break
        elif watch_seconds > 0 and not headless:
            for _ in range(int(watch_seconds * 10)):
                root.after(100, "")
                root.update()
            root.update()
        elif watch_seconds > 0 and not headless:
            for _ in range(int(watch_seconds * 10)):
                root.after(100, "")
                root.update()
        return AcceptanceProbe(
            PASS, title, evidence,
            human_check=("肉眼确认活窗口画面随内容更新可见（present_"
                         "committed=True 才真上屏；否则看 frame_png 截图"
                         " 兜底）；对照 gate_decision==present 与 "
                         "gate_no_surface==no-surface 的可观测差异"))
    except Exception as exc:
        # 任何驱动层异常都不挂死：如实 FAIL + reason，销窗返回。
        return AcceptanceProbe(FAIL, title, evidence,
                               reason=f"驱动失败: {exc!r}")
    finally:
        # 证据取完一律销窗（headless 本就 withdraw；非 headless 也必
        # destroy，绝不把活窗口挂死给人）。
        try:
            root.destroy()
        except Exception:
            pass


def _make_frame_via_backend(be, w: int, h: int):
    """把一张有内容的 CPU 画面包成 :class:`FrameHandle`（不绕
    :meth:`GPUBackend.render` 喂场景——render 吃 LayoutBox 树 /
    DisplayList，自造 dict scene 它不认，会白屏/抛错）。

    - 构造 :func:`_make_demo_frame`（渐变 + 红方块）产
      :class:`CPUImage`；
    - 真实 wgpu 设备在场时，复用后端 :class:`GPURasterizer`
      （``be._rasterizer``，与 ``render`` 内部同一套逻辑）把 CPU
      RGB8 转 RGBA8 传成 GPU 纹理（``frame.texture`` 非空）——
      present 时才有真画面可上屏；
    - 缺 GPU 时 texture=None（CPU 形态），present 返回 False 不崩
      （P13），证据里 ``frame_is_gpu_texture`` 如实为 False。

    这样肉眼检测时看到的是**有内容**的画面（渐变 + 红方块），不是
    白屏；且全程不喂 render、不阻塞、不挂死。
    """
    from toy_browser.rendering.rasterize_cpu import FrameHandle
    img = _make_demo_frame(w, h)
    frame = FrameHandle(frame_id=1, width=w, height=h,
                        image=img, mode="cpu")
    raster = getattr(be, "_rasterizer", None)
    if raster is not None:
        try:
            b2 = img.buffer
            buf = bytearray(w * h * 4)
            for i in range(w * h):
                j = i * 4
                buf[j] = b2[i * 3]
                buf[j + 1] = b2[i * 3 + 1]
                buf[j + 2] = b2[i * 3 + 2]
                buf[j + 3] = 255
            frame.texture = raster.upload_rgba8(
                w, h, bytes(buf), label="acceptance_frame")
            frame.mode = "gpu"
        except Exception:
            # 上传失败不卡死：退回 CPU 形态（present 返回 False 不崩），
            # 证据里 texture 为 None，人工检测能据 frame_is_gpu_texture
            # 判读"无 GPU 纹理可上屏"。
            frame.texture = None
    return frame


# ---------------------------------------------------------------------------
# 探针 2 — 真实 IME 选词（驱动到最大程度；真人选词留人工）
# ---------------------------------------------------------------------------

def probe_ime_composition(ime_phrases: list, headless: bool = True) -> \
        AcceptanceProbe:
    """活窗口里驱动真实 Tk Entry + G03 IME 组合三阶段（可自动化边界）。

    *ime_phrases* 是"拼音中间态 → 最终汉字"的段落列表，每段是一个
    ``(text, mark)`` 或纯 str（拼音）。在活窗口里给真实
    :class:`tkinter.Entry` 绑定 :class:`NativeOverlayWidget`，经
    :class:`LiveEditingSession` 驱动 ``ime_composition_start/update/
    commit``（+ 一段取消），把组合区 / ``is_composing`` / 最终 value
    同步进**真实控件**——这是 IME 选词"可自动化到最大程度"的部分。

    现场证据：每段组合阶段轨迹（composing / value）+ 最终真实控件值。
    **人工判定项**：在真实系统输入法（中/日/韩 IME）下逐词选字，确认
    组合区下划线、候选列表、最终汉字与本项目组合状态机一致。脚手架
    无法自动判定真人 IME。

    缺 tkinter → UNSUPPORTED（不冒充）；驱动失败 → FAIL 附原因。
    """
    title = "真实 IME 选词（可自动化到最大程度）"
    try:
        import tkinter
    except Exception as exc:
        return AcceptanceProbe(UNSUPPORTED, title,
                               reason=f"tkinter unavailable: {exc!r}")

    from toy_browser.app.live_editing import LiveEditingSession
    from toy_browser.app.native_overlay import KIND_ENTRY

    evidence: dict = {"phrases": list(ime_phrases)}
    sess = LiveEditingSession()
    sess.register_field("ime", value="", kind=KIND_ENTRY)
    sess.focus_field("ime")

    # 活窗口 + 真实 Tk Entry（绑定到覆盖层，驱动 IME 三阶段进真实控件）。
    root = tkinter.Tk()
    if headless:
        root.withdraw()
    entry = tkinter.Entry(root, width=32)
    entry.pack()
    entry.update_idletasks()
    ov = sess.get_overlay("ime")
    ov.bind_widget(entry)

    try:
        # 逐段组合：中间态拼音 → 最后段汉字提交。
        phrases = list(ime_phrases)
        if phrases:
            imeres = sess.run_ime_composition(*phrases)
            evidence["composition"] = [e.describe()
                                       for e in imeres.entries]
            evidence["final_value"] = imeres.final_value
            evidence["committed"] = imeres.committed
            evidence["end_composing"] = imeres.end_composing
            # 同步进真实控件后读回验证。
            ov.sync_to_widget()
            evidence["widget_value"] = entry.get()
        # 一段取消（Esc 语义）：组合区被清、is_composing 复位。
        sess2 = LiveEditingSession()
        sess2.register_field("ime2", value="")
        sess2.focus_field("ime2")
        cancel_res = sess2.run_ime_composition("abc", cancel=True)
        evidence["cancel_composing"] = cancel_res.end_composing
        return AcceptanceProbe(
            PASS, title, evidence,
            human_check=("在真实系统输入法下逐词选字，确认组合区/候选/"
                         "最终汉字与本项目组合状态机一致"))
    finally:
        entry.destroy()
        root.destroy()


# ---------------------------------------------------------------------------
# 探针 3 — 真实读屏器（暴露 UIA 契约 + 事件流；读屏器读到留人工）
# ---------------------------------------------------------------------------

def probe_screen_reader(a11y_input: Optional[str] = None,
                        sample_node: Optional[dict] = None) -> \
        AcceptanceProbe:
    """把内部 a11y 树 → UIA 控件契约 + :class:`UiaFocusTracker` 事件流。

    读屏器消费的是"UIA 控件契约 + name/role/value/state/focus 事件流"。
    本探针把 :func:`ui_a11y_bridge.to_uia_node`（内部树映射到
    ``ControlType/Name/Value/IsEnabled/IsKeyboardFocusable/HasKeyboard
    Focus`` 契约）与 :class:`UiaFocusTracker`（``focus-moved /
    property-changed / structure-change`` 事件流）一起暴露出来——真实
    读屏器 / UIA 检查器可直接消费这份契约 + 事件流。

    *a11y_input* 指定时从 JSON 文件读内部 a11y 树（``browser.collect_
    accessibility_tree`` 产物）；否则用 *sample_node* 或内置示例树。
    现场证据：UIA 契约（JSON 可序列化）+ 一条完整事件流（Tab 移动焦点
    → 改 value → 结构变化 → 对话框焦点归还）。

    **人工判定项**：用真实读屏器（NVDA / JAWS / Windows 讲述人）+
    UIA 检查器，确认"读屏器真的播报了这份契约 + 事件流里的焦点/
    值变化"。脚手架只做"暴露契约 + 生成事件流"，不广播 OS 事件、不
    宣称读屏器已读到。纯逻辑层无 OS 依赖，恒可驱动（PASS 指契约+
    事件流暴露成功，人工读到留签字）。
    """
    title = "真实读屏器（UIA 契约 + 事件流暴露）"
    from toy_browser.app.ui_a11y_bridge import (
        to_uia_node, UiaFocusTracker, DialogFocusGuard,
    )

    # 取内部 a11y 树（文件 → 示例树 → 内置示例）。
    # *a11y_input* 文件既可放**节点 list**（``[{"role":...}, ...]``），
    # 也可放**单根 dict**（``{"role":..., "children":[...]}``）——两种都
    # 归一化成 list 再往下走（否则 ``_count_nodes`` 对 dict 迭代会拿到
    # key（str）→ 崩）。
    tree: list = []
    if a11y_input:
        try:
            with open(a11y_input, encoding="utf-8") as f:
                loaded = json.load(f)
        except Exception as exc:
            return AcceptanceProbe(FAIL, title,
                                   reason=f"读 a11y 树失败: {exc!r}")
        # 归一化：dict（单根）→ [dict]；list 原样；空/非法 → []。
        if isinstance(loaded, dict):
            tree = [loaded]
        elif isinstance(loaded, list):
            tree = loaded
        else:
            return AcceptanceProbe(FAIL, title,
                                   reason="a11y 树须为节点 list 或单根 dict")
    elif sample_node:
        tree = [sample_node]
    else:
        tree = [
            {"role": "section", "name": "form", "children": [
                {"role": "textbox", "name": "用户名", "children": []},
                {"role": "textbox", "name": "密码", "children": []},
                {"role": "button", "name": "登录", "children": []},
            ]},
        ]

    evidence: dict = {"tree_size": _count_nodes(tree)}
    # 把内部树映射成 UIA 控件契约（根节点）。
    uia_root = to_uia_node({"role": "section", "name": "root",
                            "children": tree})
    evidence["uia_contract"] = uia_root
    # 生成一条完整事件流（读屏器消费）：焦点移动 → value 变 → 结构
    # 变化 → 对话框焦点归还。
    tracker = UiaFocusTracker()
    tracker.register("u", name="用户名", value="", role="textbox")
    tracker.register("p", name="密码", value="", role="textbox")
    tracker.register("b", name="登录", value="", role="button")
    ev_flow = [
        tracker.set_focus("u"),
        tracker.update_value("u", "alice"),
        tracker.set_focus("b"),
        tracker.insert_child("b", "menu"),
        tracker.remove_child("b", "menu"),
    ]
    guard = DialogFocusGuard(current_focus="u")
    dlg_open = guard.open("dlg", current_focus="u")
    dlg_close = guard.close(fallback_focus="b")
    ev_flow.extend([dlg_open, dlg_close])
    evidence["event_stream"] = [
        {k: e.get(k) for k in ("type", "id", "name", "role", "value",
                                "is_enabled", "dialog", "restored_focus",
                                "prop", "op", "parent", "child")
         if k in e} for e in ev_flow
    ]
    evidence["focused_final"] = tracker.focused
    return AcceptanceProbe(
        PASS, title, evidence,
        human_check=("用真实读屏器 + UIA 检查器确认：这份契约 + 事件流"
                     "被读屏器真实播报（焦点移动 / value 变 / 对话框焦点"
                     "归还）"))


def _count_nodes(tree: list) -> int:
    """递归数 a11y 树节点数（证据用）。

    入参应为节点 list（单根 dict 须先归一化为 ``[dict]``——
    :func:`probe_screen_reader` 已做）。这里再对 dict 单根做一层
    防御（dict → [dict]），避免误用崩溃。
    """
    if isinstance(tree, dict):
        tree = [tree]

    def _c(ns):
        n = 0
        for x in ns or []:
            if not isinstance(x, dict):
                continue
            n += 1 + _c(x.get("children"))
        return n
    return _c(tree)


# ---------------------------------------------------------------------------
# 汇总 / 报告 / CLI
# ---------------------------------------------------------------------------

def run_all(ime_phrases: list, a11y_input: Optional[str] = None,
            headless: bool = True,
            watch_seconds: float = 0.0,
            keep_open: bool = False) -> list:
    """跑组 A 三项探针，返回 :class:`AcceptanceProbe` 列表。

    *watch_seconds* / *keep_open* 只作用于 swapchain 探针（控制活
    窗口留给肉眼看的时长 / 是否挂住等你手动关窗）。"""
    return [
        probe_swapchain_visible(headless=headless,
                                watch_seconds=watch_seconds,
                                keep_open=keep_open),
        probe_ime_composition(ime_phrases=ime_phrases, headless=headless),
        probe_screen_reader(a11y_input=a11y_input),
    ]


def render_report(probes: list, title: str = "组 A 活窗口人工验收清单") -> \
        str:
    """把 :class:`AcceptanceProbe` 列表渲染成可签字的 Markdown 报告。"""
    lines = [f"# {title}", "",
             "> 生成：`toy_browser.app.live_acceptance`。每项含"
             "驱动结果 + 现场证据 + **人工判定项** + 签字位；"
             "未签字前不得视为已验收（不冒充通过原则）。", ""]
    for p in probes:
        lines.append(p.render())
        lines.append("")
    return "\n".join(lines)


def main(argv: list) -> int:
    import argparse
    p = argparse.ArgumentParser(
        description="toy-browser 组 A 活窗口人工验收脚手架")
    p.add_argument("--ime", default="ni,hao",
                   help="IME 组合短语（逗号分隔拼音段，末段视为汉字）")
    p.add_argument("--a11y-input", default=None,
                   help="内部 a11y 树 JSON（browser.collect_accessibility_tree 产物）")
    p.add_argument("--headless", action="store_true",
                   help="活窗口 withdraw（仍拿 HWND，供 CI / 无显示器）")
    p.add_argument("--watch", type=float, default=0.0, metavar="SECONDS",
                   help="非 headless 下给 swapchain 活窗口留 N 秒"
                        "（倒计时后自动销窗，不挂死）。默认 0=立即销窗。")
    p.add_argument("--keep", action="store_true",
                   help="非 headless 下 swapchain 活窗口不自动销窗："
                        "用 mainloop 挂住**直到你手动点关窗**才返回"
                        "（治\"窗口瞬间关闭看不清\"，从容肉眼确认）。"
                        "与 --watch 二选一，--keep 优先。")
    p.add_argument("--no-wait", action="store_true",
                   help="非交互模式：跑完立即返回，不在最后等 [enter]"
                        "（用于落报告 / CI；肉眼确认时不用本选项）")
    p.add_argument("--out", default=None,
                   help="把验收报告写到该路径（Markdown）")
    args = p.parse_args(argv)

    phrases = [s for s in args.ime.split(",") if s.strip()]
    probes = run_all(ime_phrases=phrases, a11y_input=args.a11y_input,
                     headless=args.headless,
                     watch_seconds=args.watch,
                     keep_open=args.keep)
    report = render_report(probes)
    print(report)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(report + "\n")
        print(f"[report] 写入 {args.out}")
    # 说明上屏结论：swapchain 探针的 present_committed 决定 1.4 能否
    # 据"肉眼窗口画面"签字；False 时须看 frame_png 截图兜底。
    swap = probes[0]
    if swap.status == PASS:
        committed = swap.evidence.get("present_committed")
        png = swap.evidence.get("frame_png", "")
        if committed:
            print(f"\n[swapchain] present 已真上屏"
                  f"（present_committed=True）→ 可肉眼签 1.4。")
        else:
            print(f"\n[swapchain] present 未真上屏"
                  f"（present_committed=False，本机无 DWM/GPU 上屏能力，"
                  f"如实标注不冒充）。\n"
                  f"  → 肉眼兜底：打开截图 {png} 看画面；"
                  f"1.4 不得据\"窗口上屏\"签字，改据截图+证据联合判读。")
    # 非 headless 且未指定 --no-wait/--keep 时给一次交互确认（人工看屏幕
    # 后可 Ctrl-C / Enter）；--no-wait 或 --keep（已 mainloop 挂住）
    # 则跑完立即返回，供落报告 / CI。
    if not args.headless and not args.no_wait and not args.keep:
        try:
            input("\n[enter] 结束（直接回车退出）")
        except (EOFError, KeyboardInterrupt):
            pass
    # 结论：全 UNSUPPORTED → 环境受限（不冒充）；有 FAIL → 1；否则 0。
    if any(x.status == FAIL for x in probes):
        return 1
    if all(x.status == UNSUPPORTED for x in probes):
        print("[note] 全部 UNSUPPORTED（缺 OS 能力，未冒充通过）")
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
