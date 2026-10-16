# 项目目录与文件引用

2026 年 10 月 16 日更新。核心实现按职责迁入 `toy_browser/`，文档、回归测试与实测评估分别保存。本文路径均相对于项目根目录。

## 目录职责

| 目录 | 内容 |
|---|---|
| `toy_browser/app/` | `browser.py` 渲染 API、`main.py` CLI、`gui.py` tkinter 界面、`session.py` 历史与书签 |
| `toy_browser/document/` | `dom.py` DOM 公共接口及 `html_pkg/` 分词、实体、节点和建树 |
| `toy_browser/style/` | `css.py` 样式解析/层叠、`transition.py` 过渡动画 |
| `toy_browser/layout/` | `layout.py` 布局树与几何计算 |
| `toy_browser/rendering/` | `paint.py` 位图与颜色、`paint_engine.py` 显示列表及绘制 |
| `toy_browser/typography/` | `font.py` 点阵字库、`text.py` 文本测量、`ttf_font.py` 字体解析、`rasterize.py` 字形光栅化 |
| `toy_browser/network/` | `net.py` HTTP/缓存/安全策略、`cookie_store.py` Cookie |
| `toy_browser/scripting/` | `js.py` JS 解释器和宿主、`web_api.py` Web API |
| `toy_browser/common/` | `mem_guard.py` 内存控制 |
| `tests/` | 回归用例、共享辅助函数、`fixtures/` 输入、`run.py` 测试编排 |
| `tools/` | 性能分析工具；`benchmarks/` 为被回归测试复用的分阶段性能脚本 |
| `docs/` | 架构、路线图、变更记录、贡献指南及通用规划 |
| `assessments/` | 所有实测评估报告、对照脚本、输入、JSON、截图、验证输出，按轮次归档 |
| `fonts/`、`demo.html` | 字体资源与示例页面；保留位置以兼容资源路径 |

根目录仅保留 `main.py`、`gui.py`、`test.py` 三个 Python 启动器，以及 README、LICENSE、资源和仓库配置。

## 运行与导入

在项目根目录运行，原启动命令保持可用：

```bash
python main.py demo.html out.png --png
python gui.py demo.html
python test.py
python tests/run.py
python tests/test_p34_css_memo.py
python tools/benchmarks/p34r6_perf.py
python assessments/20261016-round5/toy_layout_runner.py
```

源码和外部调用应使用完整包路径，例如：

```python
from toy_browser.app import browser
from toy_browser.document import dom
from toy_browser.style import css
from toy_browser.layout import layout
from toy_browser.scripting import js
bitmap = browser.render_document('<p>Hello</p>')
```

原来直接导入根目录核心文件的方式（例如 `import browser`）需改用上述包导入。根目录 GUI 启动器不再承载 `PageInstance` 等实现；其调用位置为 `toy_browser.app.gui`。本项目的测试、基准和历史评估脚本已同步迁移，避免同一模块被重复加载造成类型或缓存状态不一致。

## 文档与评估文件

- [贡献指南](CONTRIBUTING.md) 由根目录移入 `docs/`。
- [独立实测报告](../assessments/legacy/docs/实测评估报告2026.md) 由文档目录移入早期评估的 `legacy/docs/`。
- [逐项改进实施方案](../assessments/20261016-round5/docs/逐项改进实施方案.md) 按第五轮归档，源码位置同步更新。
- 根目录 `_verify_all.py` 与 `demo_out.png` 分别移入 `assessments/structure-20261016/verify_all.py` 和同目录图片。
- 历次评估按日期保留脚本、输入与结果的相邻关系，避免破坏复现路径。报告中的源码路径和脚本导入已更新；已有 JSON、截图和输出证据保留，不把旧结果当作本次运行数据。
- 文档目录树、源码位置、相对 Markdown 链接和导入示例随目录迁移更新。历史行号仅用于追溯原报告，不代表移动后精确行号。

各轮文档统一放在 `assessments/<轮次>/docs/`，并由该轮 `README.md` 索引。第四轮改进计划归入第四轮；GUI 建议、GPU 迁移方案及逐项实施方案归入第五轮。完整入口见 [轮次索引](../assessments/README.md)。

## 本次验证

验证输出集中在 `assessments/structure-20261016/`。939 项回归已完成验证（沙箱内 927 项通过，12 项临时目录权限受限用例在常规权限下重跑通过）；独立 CSS 用例 4 项通过。启动器、字体定位及文档链接的结果见 [验证记录](../assessments/structure-20261016/docs/验证记录.md)。本次工作仅整理目录和引用，不改变引擎功能。
