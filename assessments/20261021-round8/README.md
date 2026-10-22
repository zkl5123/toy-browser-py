# 第八轮真实浏览器标准评估（2026-10-21）

源码基线：`551da747862b6d35384e8c97e6f6a4e86b389a57`。主会话独立完成，无子智能体、无子任务；只新增评估材料，不修改产品代码和已有测试。

## 文档入口

1. [实测评估报告](docs/实测评估报告.md)：总评、真实测试结果、已修复与剩余问题。
2. [逐项改进实施方案](docs/逐项改进实施方案.md)：18项工作，含模块、步骤、依赖和验收。
3. [网络脚本与安全审计](docs/网络脚本与安全审计.md)：CORS、XHR、GUI策略、存储与限额。
4. [渲染与标准差距审计](docs/渲染与标准差距审计.md)：解析/CSS/布局/字体/GPU与性能。
5. [GUI与工程质量审计](docs/GUI与工程质量审计.md)：默认调用链、worker、输入、测试可信性。
6. [评估方法与标准基线](docs/评估方法与标准基线.md)：标准、环境、复现、计数和未验证范围。

## 结果摘要

- 完整回归：2871/2874通过，3失败，退出码1；三失败分别涉及历史栈契约、旧DOM断言、浮点精确相等。
- 当轮Node语义差分24/24一致；历史Edge JS样本51例trim日志一致；48个历史布局框误差≤1px。
- 渲染原始32项29匹配、3不匹配；其中2项GPU探针前提不适用，不能直接记作产品缺陷。
- 仍有P0：预检允许被错误替代实际CORS授权、XHR正文边界、GUI刷新/历史丢失策略。
- 有实质改善，但尚不具备任意不可信网页的安全日常浏览条件。

## 文件结构（沿用历史轮次）

```text
20261021-round8/
  README.md
  docs/                     六份报告
  *_probe.py                当前及历史有界复现
  *-probes.json             原始观测
  native-full.json/xml     全量机器结果
  *-stdout/stderr.txt       原始日志
  *-run.json                命令、退出码、耗时
  *_cases.json              测试输入
  edge_*_results.json       冻结历史参考（非当轮运行）
  toy_*_results.json        当前重放结果
  toy_shot_*.png            四张当前截图，未视觉验收
  pipeline_profile.json    分段性能
  source-inventory.json    初始源码哈希
  fixture-provenance.json  历史样本来源
  delivery-check.json      交付完整性（非标准符合性证明）
  manifest.json            打包文件校验清单
```

新完整压缩包在项目`outputs/toy-browser-py-第八轮评估-20261021.zip`。历史轮次不覆盖。

## 原始证据索引

| 材料 | 文件 |
|---|---|
| 全量回归 | [运行记录](native-full-run.json)、[stdout](native-full-stdout.txt)、[JSON](native-full.json)、[JUnit](native-full.xml) |
| 差分摘要 | [comparison](comparison.json)、[当轮语义](semantic-probes.json) |
| 当前入口/协议探针 | [脚本](current_probe.py)、[结果](current-probes.json) |
| 真实回环安全探针 | [脚本](network_probe.py)、[结果](network-probes.json) |
| 历史安全重放 | [结果](security-probes.json)，必须结合专题解释接口漂移 |
| 渲染与几何 | [结果](render-probes.json)、[当前布局](toy_layout_results.json) |
| 性能 | [原始三次样本](pipeline_profile.json) |

阅读时注意：没有全量标准兼容率；截图未判读；IME/UIA/真实GPU呈现未验收；注入探针不冒充完整网页运行。
