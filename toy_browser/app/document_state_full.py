"""C01 完整 — DocumentState：文档生命周期与任务版本（架构约束层）。

依据 assessments/20261016-round5/docs/逐项改进实施方案.md C01（完整
抽取，批次 7）：

1. **抽取 DocumentState**，拥有 DOM、Host、DocumentPolicy、资源、
   样式/布局版本和稳定 node_id。
2. **解析和脚本初始化属于新文档加载**；hover / focus / scroll /
   resize 只能使相应阶段失效，**不重新创建 Host**（重排不重跑脚本）。
3. **区分 document_id / navigation_epoch / layout_revision /
   frame_id**；每类结果明确携带版本（C01 骨架已在批次 1 落地，
   本批把四个计数器收敛进 DocumentState 的唯一持有者）。
4. **唯一修改入口**：GUI / CLI / 测试都接到同一流程（本批提供
   纯逻辑核心 + 版本回归；公共 API 包装层兼容旧调用由 GUI 侧
   批次接入）。
5. **新导航先创建新上下文**，旧上下文待任务与帧资源释放后销毁，
   **不能跨源复用策略或 JS 全局**。

本模块是**纯逻辑核心**（不依赖 tkinter / 网络）：

* :class:`DocumentState` — 一个文档的完整生命周期容器：
  - 身份：``doc_version``（C01 四个计数器）+ ``policy``（文档策略）
    + ``base_url``（CSP / 策略上下文的源）。
  - 内容：``dom_tree`` + ``script_host``（同一棵 DOM、同一个
    Host；脚本初始化只发生一次，:meth:`script_init_count` 计数
    验证"脚本初始化只增加一次"）。
  - 资源：``resources`` 子资源注册表（url → 结果，跨重排复用）。
  - 版本门控：``mark_restate`` / ``mark_render`` / ``new_document``
    / ``new_context`` 把阶段失效规则固化成方法（重排只 bump
    layout_revision，不重建 Host；渲染只 bump frame_id）。
  - 稳定 node_id：``node_ids``（元素索引 → 稳定 id，跨重排不变，
    点击监听 / 焦点不因重排失效——"重排后点击监听仍在"）。
* :func:`assert_same_dom` — CLI / GUI 相同输入得到同一 DOM
  （验收标准）：比对两棵树的标签 / 属性 / 文本序列。

与骨架（``document_state.py`` 的 :class:`DocumentVersion`）的关系：
本批的 :class:`DocumentState` *持有* 一个 :class:`DocumentVersion`
作为版本源（不复制计数器），并叠加策略 / DOM / Host / 资源 /
node_id 的完整生命周期。骨架的 ``DocumentVersion`` 原样保留，
供 G07 / G08 的命令队列与导航状态机继续消费。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from toy_browser.app.document_state import DocumentVersion


# 资源类型（子资源注册表 key 前缀）
RES_STYLE = "stylesheet"
RES_IMAGE = "image"
RES_SCRIPT = "script"


@dataclass
class DocumentState:
    """C01 step 1 — 一个文档的完整生命周期容器（纯逻辑）。

    * ``doc_version`` — C01 四计数器（:class:`DocumentVersion`）。
    * ``policy`` — 文档策略上下文（CSP / origin 等；toy 层用一个
      dict 承载，真实 :class:`net.DocumentPolicy` 在 GUI 侧注入）。
    * ``base_url`` — 文档绝对 URL（策略 / 子资源相对解析的源）。
    * ``dom_tree`` / ``script_host`` — 同一棵 DOM + 同一个 Host
      （脚本初始化只发生一次；重排 / 渲染不重建 Host）。
    * ``resources`` — 子资源注册表（url → 结果），跨重排复用。
    * ``node_ids`` — 元素索引 → 稳定 node_id（跨重排不变）。
    * ``script_init_count`` — 脚本初始化次数（验收：只增加一次）。

    生命周期入口（C01 step 2/4/5）：

    * :meth:`new_document` — 新文档加载：解析 + 脚本初始化（计数 +1）。
    * :meth:`mark_restate` — hover/focus/scroll/resize 重排：只 bump
      ``layout_revision``，**不重建 Host、不重跑脚本**（计数不变）。
    * :meth:`mark_render` — 光栅化新帧：只 bump ``frame_id``。
    * :meth:`new_context` — 新导航：创建新上下文，旧上下文标记待销毁
      （不能跨源复用策略 / JS 全局，C01 step 5）。
    """

    doc_version: DocumentVersion = field(default_factory=DocumentVersion)
    policy: dict = field(default_factory=dict)
    base_url: str = ""
    dom_tree: object = None
    script_host: object = None
    resources: dict = field(default_factory=dict)
    node_ids: dict = field(default_factory=dict)
    script_init_count: int = 0
    disposed: bool = False
    # 资源注册表条目：{"url", "kind", "ok", "body"(bytes 占位)}
    _resource_seq = 0

    # ------------------------------------------------------------------
    # 新文档加载（C01 step 2：解析 + 脚本初始化 = 新文档）
    # ------------------------------------------------------------------
    def new_document(self, dom_tree: object, host: object,
                     policy: Optional[dict] = None,
                     base_url: str = "") -> None:
        """C01 — 解析 + 脚本初始化属于新文档加载：

        * 分配新的 C01 文档身份（``doc_version.new_document()``）；
        * 绑定 *dom_tree* / *host* / *policy* / *base_url*；
        * **脚本初始化计数 +1**（同一文档只应初始化一次；重排 /
          渲染路径绝不经过这里，所以计数保持 1）。
        """
        self.doc_version.new_document()
        self.dom_tree = dom_tree
        self.script_host = host
        self.policy = dict(policy or {})
        self.base_url = base_url
        self.script_init_count += 1
        self.disposed = False
        # 稳定 node_id：初始为元素索引自身（跨重排不变）
        self.node_ids = {i: i for i in range(self._count_elements(dom_tree))}

    # ------------------------------------------------------------------
    # 重排（C01 step 2：只使 layout 阶段失效，不重建 Host）
    # ------------------------------------------------------------------
    def mark_restate(self) -> None:
        """hover / focus / scroll / resize 重排：只 bump
        ``layout_revision``；**不**重建 Host、**不**重跑脚本
        （``script_init_count`` 保持不变——验收"脚本初始化只增加
        一次、重排后点击监听仍在"）。"""
        self.doc_version.bump_layout()

    def mark_render(self) -> None:
        """光栅化新帧：只 bump ``frame_id``。"""
        self.doc_version.bump_frame()

    # ------------------------------------------------------------------
    # 资源注册表（C01 step 1：资源归属文档，跨重排复用）
    # ------------------------------------------------------------------
    def register_resource(self, url: str, kind: str,
                          ok: bool = True, body: bytes = b"") -> int:
        """登记一条子资源（跨重排复用，不重复下载）。返回资源 id。"""
        self._resource_seq += 1
        rid = self._resource_seq
        self.resources[rid] = {"url": url, "kind": kind, "ok": ok,
                               "body": body}
        return rid

    def resource_for(self, url: str, kind: Optional[str] = None):
        """按 url（+ 可选 kind）取已登记资源；未登记返回 None。"""
        for rid, r in self.resources.items():
            if r["url"] == url and (kind is None or r["kind"] == kind):
                return r
        return None

    # ------------------------------------------------------------------
    # 新导航（C01 step 5：新上下文，旧上下文待销毁，不跨源复用）
    # ------------------------------------------------------------------
    def new_context(self) -> "DocumentState":
        """C01 — 新导航先创建**新**上下文：旧上下文标记 ``disposed``
        （任务与帧资源释放后销毁），不能跨源复用策略 / JS 全局。

        返回一个全新的 :class:`DocumentState`（继承 C01 版本序列，
        但 DOM / Host / 策略 / 资源全部重置）；旧对象保留引用直到
        调用方释放。"""
        self.disposed = True
        fresh = DocumentState()
        # 版本计数器是模块级单调序列（C01 骨架），fresh 继承全局
        # 单调性；这里显式为新文档 bump，使旧文档的帧 / 任务全 stale。
        fresh.doc_version.new_document()
        return fresh

    # ------------------------------------------------------------------
    # 版本门控（C01 step 3：每类结果携带版本）
    # ------------------------------------------------------------------
    def is_stale_frame(self, frame_doc_id: int, frame_id: int) -> bool:
        return self.doc_version.is_stale_frame(frame_doc_id, frame_id)

    def is_stale_layout(self, layout_revision: int) -> bool:
        return self.doc_version.is_stale_layout(layout_revision)

    def identity(self) -> tuple:
        return self.doc_version.identity()

    def node_id(self, element_index: int) -> int:
        """C01 — 稳定 node_id：重排 / 渲染后同一元素仍映射到同一 id
        （点击监听 / 焦点不因重排失效）。"""
        return self.node_ids.get(element_index, element_index)

    # ------------------------------------------------------------------
    # 辅助
    # ------------------------------------------------------------------
    @staticmethod
    def _count_elements(tree) -> int:
        """粗略统计元素节点数（用于初始 node_id 映射；toy 层用 DFS
        计数，不依赖具体 DOM 类型）。"""
        if tree is None:
            return 0
        count = 0

        def walk(n):
            nonlocal count
            if getattr(n, "is_element", lambda: False)() \
                    or getattr(n, "tag", None) is not None:
                count += 1
            for c in getattr(n, "children", []) or []:
                walk(c)
        walk(tree)
        return count


# ----------------------------------------------------------------------
# CLI / GUI 一致性（C01 验收：相同输入得到同一 DOM）
# ----------------------------------------------------------------------
def _dom_fingerprint(tree) -> tuple:
    """一棵 DOM 树的"标签 / 属性 / 文本"序列指纹（CLI 与 GUI 对
    比用；不含 node_id，避免把实现细节算进一致性）。"""
    seq = []

    def walk(n):
        tag = getattr(n, "tag", None)
        if tag is not None:
            attrs = {}
            for k in sorted(getattr(n, "attrs", {}) or {}):
                attrs[k] = (getattr(n, "attrs", {}) or {})[k]
            seq.append((tag, tuple(attrs.items())))
        text = getattr(n, "text", None)
        if text:
            seq.append(("\t#text", (text,)))
        for c in getattr(n, "children", []) or []:
            walk(c)
    walk(tree)
    return tuple(seq)


def assert_same_dom(tree_a, tree_b) -> bool:
    """C01 验收 — CLI / GUI 对**相同输入**应得到同一 DOM（指纹
    一致）。返回是否一致；不一致时抛 :class:`AssertionError` 并
    附两棵树的指纹差异摘要。"""
    fa, fb = _dom_fingerprint(tree_a), _dom_fingerprint(tree_b)
    if fa == fb:
        return True
    # 摘要：第一个不同的节点
    diff_at = next((i for i in range(min(len(fa), len(fb)))
                    if fa[i] != fb[i]), min(len(fa), len(fb)))
    raise AssertionError(
        f"DOM mismatch at node #{diff_at}: cli={fa[diff_at]!r} "
        f"gui={fb[diff_at]!r} (len {len(fa)} vs {len(fb)})")
