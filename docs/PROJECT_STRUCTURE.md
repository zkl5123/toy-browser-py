# 项目目录与清理记录

2026 年 10 月 14 日整理。浏览器核心模块继续位于根目录，保持现有导入接口及 CLI、GUI 命令；测试、工具、文档和评估证据按用途归入目录。

## 目录用途

| 位置 | 内容 |
|---|---|
| 根目录 `main.py`、`gui.py` | CLI 和 tkinter GUI 入口 |
| 根目录 `test.py` | 完整测试编排入口，加载 `tests/` 并定位项目夹具 |
| 根目录其他 Python 模块 | DOM、CSS、布局、绘制、JS、HTTP、会话、字体等核心实现 |
| `html_pkg/` | HTML 分词、建树、节点、实体模块 |
| `tests/` | 27 个测试模块及共享辅助函数，保留未被总编排器收录的独立测试 |
| `tools/benchmarks/` | 4 个分阶段性能脚本；部分被测试引用，因此全部保留 |
| `fonts/`、`demo.html` | 运行资源、测试夹具与示例 |
| `docs/` | 架构、路线图、变更记录、历史评估报告及规划文档 |
| `assessments/20261014/` | 本次真实浏览器评估报告、输入、脚本、JSON 和截图证据 |
| `assessments/legacy/` | 原 `_agenttest/` 的历史评估资料，包括参考浏览器依赖清单 |

## 已清理内容

- 根目录的生成文件 `a11y.json`、空文件 `links.json`、`links.txt`。
- 沙箱限制下的失败测试日志 `assessment_tests.log`。
- Python 字节码缓存和评估 HTTP 缓存 `_cache_test/`。
- 本次评估目录的冗余运行日志；正式完整自测输出转存为 `tests-output.txt`。
- 三张相同内容的性能导出图 `perf_100.png`、`perf_1000.png`、`perf_5000.png`；性能原始计时仍在 JSON 中。
- 无调用引用的一次性草稿 `_agenttest/table_fix_snippet.py`。

历史报告、评估截图和 JSON 具有追溯用途，保留在对应目录。字体资源与回归测试具有运行用途，未按临时文件删除。生成输出、缓存和本机会话文件已补充到 `.gitignore`，避免再次混入源码。

## 运行与复现

在项目根目录运行，命令保持兼容：

```bash
python main.py demo.html out.png --png
python gui.py demo.html
python test.py
```

独立测试与基准的新路径：

```bash
python tests/test_p34_css_memo.py
python tools/benchmarks/p34r6_perf.py
python assessments/20261014/toy_layout_runner.py
```

测试和性能脚本会定位项目根目录，保证字体和 demo 等相对路径夹具可用。评估脚本也已更新导入根路径；浏览器对照运行器的外部依赖仍见该目录实测报告。历史脚本按历史行为保留，不将它们冒充本次兼容性结果。

目录整理后的完整回归为 **925/925 通过**，输出见 `assessments/20261014/structure-validation.txt`。
额外验证了全部 73 个 Python 文件的语法、独立 CSS 缓存测试的 4 项检查，以及移动后的布局评估和统计脚本。
目录整理没有修复先前评估发现的浏览器语义问题。
