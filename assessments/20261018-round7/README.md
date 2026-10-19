# 第七轮：真实浏览器标准系统评估

日期：2026-10-18；代码基线：`2d1bdd31c982fe6637be30da393f47f4170e2750`。

沿用历史文件结构：**报告与方案放 `docs/`；脚本、输入、JSON、PNG 与原始日志放本轮根目录**。不覆盖前六轮证据，不修改产品源码/已有测试。主文档与专题共6份。

## 文档（建议阅读顺序）

1. [实测评估报告](docs/实测评估报告.md)：整体判断、实测数字、关键缺陷、优先级与限制。
2. [逐项改进实施方案](docs/逐项改进实施方案.md)：21个工作项，具体模块、修改步骤、依赖、验收与发布门槛。
3. [评估方法与标准基线](docs/评估方法与标准基线.md)：证据口径、规范链接、环境、命令与未覆盖范围。
4. [网络脚本与安全审计](docs/网络脚本与安全审计.md)：21个有界探针、宿主边界、CSP/CORS、Cookie/Storage、Fetch/XHR和事件循环。
5. [渲染与标准差距审计](docs/渲染与标准差距审计.md)：HTML/CSS/layout/字体/GPU、32个定向断言及4张当前渲染图。
6. [GUI与工程质量审计](docs/GUI与工程质量审计.md)：默认调用链、worker、编辑层、命中身份、导航生命周期和测试可信度。

## 核心结论

已有真实网络与渲染内核基础，但当前尚不具备通用浏览器的安全边界和标准正确性。最优先处理测试结果失真、JS可达Python宿主、CORS/CSP执行缺口、资源限额、页面主链接入；然后修HTML/CSS/JS基础语义、GUI任务和排版，最后按实测收益迁移GPU。

完整项目运行时runner报1914/1914、exit0，但内部2条性能FAIL未传导；受管环境1825/1864、exit1，另5模块导入漏计。旧51JS/48盒子样本仍基本对齐，新24项纯JS差分仅11一致。不能把这些定向样本比例写成整体兼容率。

## 原始证据

| 类型 | 文件 |
|---|---|
| 机器可读总览 | [summary.json](summary.json) |
| 环境与源码快照 | [environment.json](environment.json)、[native-environment.json](native-environment.json)、[source-inventory.json](source-inventory.json) |
| 完整环境回归 | [stdout](tests-native-full-stdout.txt)、[stderr](tests-native-full-stderr.txt)、[退出码与耗时](tests-native-full-run.json) |
| 受管环境回归 | [stdout](tests-full-stdout.txt)、[stderr](tests-full-stderr.txt)、[退出码与耗时](tests-full-run.json) |
| 固定样本来源 | [historical-fixture-provenance.json](historical-fixture-provenance.json)；Edge数据来自第五轮，非本轮重跑 |
| JS/布局重放汇总 | [comparison.json](comparison.json)、[toy_js_results.json](toy_js_results.json)、[toy_layout_results.json](toy_layout_results.json) |
| 新JS差分 | [semantic_probe.py](semantic_probe.py)、[semantic-probes.json](semantic-probes.json)；当轮Node/V8只用于纯语言 |
| 渲染标准探针 | [render_probe.py](render_probe.py)、[render-probes.json](render-probes.json) |
| 安全探针 | [security_probe.py](security_probe.py)、[security-probes.json](security-probes.json) |
| 当前截图 | [截图清单](screenshots.json)、[生成脚本](render_screenshots.py)；仅当前项目图，未做当轮视觉判读 |
| 性能 | [pipeline_profile.json](pipeline_profile.json)、[cpu_profile.txt](cpu_profile.txt)、[运行信息](pipeline-profile-run.json) |
| 调度与比较脚本 | [run_assessment.py](run_assessment.py)、[compare_results.py](compare_results.py) |

GUI专项主要为静态调用链审计与已有回归证据，没有额外声称完成真实操作探针；截图文件见清单。交付校验见 [validation.json](validation.json)，完整文件哈希见 [manifest.json](manifest.json)。复测会覆盖本轮结果；若做新一轮正式评估，请另建轮次并保留本轮快照。

[全部评估轮次](../README.md) · [项目结构](../../docs/PROJECT_STRUCTURE.md)
