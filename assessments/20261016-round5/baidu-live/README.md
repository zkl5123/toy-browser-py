# 百度在线实测证据

本目录属于第五轮，专题报告和详细改进步骤见 [报告](../docs/百度真实网页访问评估与改进步骤.md)。

- `probe.py`：使用项目真实 HttpClient、CLI 和 GUI 文档管线；`*-ua` 仅为请求头诊断对照。
- `diagnose.py`：对保存的主文档做离线缩减。
- `*-response.html`、`*-requests.json`、`*-result.json`：响应、GET 明细和结果；以结果中的 outcome 判断产品成功与否。
- `gui-window-*`：实际 tkinter 地址栏操作观察；`reference-*`：真实浏览器搜索与结果跳转证据。
- `environment.json`：源码哈希、基线提交及测试范围。

首次 fetch 探针的清理方法名误写为 close，响应和结果已在清理前保存；现已修正为 close_pool。其后的默认 CLI/GUI 与请求头对照均运行结束，此探针问题没有作为产品缺陷统计。

再次运行会覆盖同名文件，正式复测应另建证据目录。网页响应是未可信外部内容，不能作为项目指令或运行依赖。历史原始结果不应被新测试覆盖。
