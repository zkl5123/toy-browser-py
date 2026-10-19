# 第七轮GUI与工程质量审计

2026-10-18；代码基线`2d1bdd3`。本报告审查默认入口是否真正使用已有组件，以及测试结果是否足以支撑能力声明。GUI专项主要是**静态调用链证据**；补充全量回归包含现有Tk测试，但不等同人工窗口、物理键盘、IME、读屏或完整网站任务验收。

## 1. 结论

项目有实际可运行的Tk外壳、页面位图、标签页、输入/焦点/历史等基础，也有真实multiprocessing spawn。问题是文档层、输入层、worker和默认GUI入口尚未形成一致的产品路径；多个“已接线”注释与实际调用链不一致。

最重要的改进不是再创建一层控制器，而是让现有DocumentSession/NavigationController成为真实状态所有者，并让默认动作经过它。相关实施见 [逐项改进实施方案](逐项改进实施方案.md) A01/A02/U01/U02/U03/Q01。

## 2. 默认导航与工作进程

### G07：spawn成立，不等于页面工作外移

证据链：

1. `toy_browser/app/gui.py:1060` 调用 `_g07_spawn_doc_server`，确实有创建文档进程机制。
2. 同文件 `_g07_post_nav`、`_g07_post_resize`、`_g07_drain_frames`（约1372/1381/1391）是适配方法，但没有形成默认导航、resize和Tk循环的完整调用闭环。
3. 默认 `_commit_navigation:1654-1669` 在`:1665`直接`page.load_url(url,800,600)`，随后刷新画布。
4. `PageInstance.load_url:683-803`自己同步获取文档/子资源、执行JS、样式与绘制。
5. `document_process.DocumentProcessWorker.handle:130-161`维护doc_id/nav_epoch/frame_id，navigate返回URL和版本，input/resize返回kind和版本；没有执行DOM/JS/layout/raster负载。

因此，真实进程存在的测试只能证明机制层，不证明页面渲染并行、输入不阻塞或多核收益。当前报告不虚构实测UI延迟数字，性能影响依据同步调用链判断。

**实施**：worker持有真正DocumentSession；UI只发命令并接帧；版本化IPC、有限队列、取消和背压；trace必须能看到默认导航产生工作请求及带像素的frame。用250ms回环延迟+UI心跳验证，而非只assert spawn。

## 3. 导航生命周期仍分叉

### B01/B03已有修复必须保留

启动参数经`resolve_initial_route`进入URL/local/blank路由；B03语法异常隔离已由本轮安全探针证明。不能照抄旧“启动URL全部丢弃”“坏脚本整页崩溃”的结论。

### B05 GUI缺消费端

`browser.render_document_from_url:194-216`会消费同步脚本的`_pending_nav`并递减跳转上限。GUI首次HTTP路径则在`gui.py:748-772`执行脚本，`:785-786`绘制/记历史，`:803`返回，没有对应pending导航消费。因此location排队/API跟跳测试通过不能证明GUI完成脚本导航。

### 刷新和前后退丢失策略

`go_back/go_forward:822-832`、`reload:968-970`进入`_load_no_history`；其`:858-859`获取HTML但没有把响应策略转入完整统一管线，再调用`load_html`。`load_html`经`execute_all`而非首次HTTP的带CSP路径。该结论与安全审计交叉核对，**是静态缺口，未在本轮冒充已执行完整GUI CSP任务**。

### about页面没有原子替换文档

`load_url:652-659`更新url/title/bitmap、清_full_bitmap并滚动复位，但提前返回于`:663 _reuse_doc=False`之前，没有清旧DOM/Host/_last_dl。`_refresh_canvas`在有缓存显示列表时优先走CPUImage重绘（约1458）。这形成“地址已变为about，数据或呈现仍引用上一页”的风险。

**实施与验收**：所有初次/刷新/历史/about/error/脚本导航都走同一DocumentSession commit；URL、DOM、Host、policy、display-list、hit tree、frame版本同时切换。测试旧页有脚本/有色块→about空页，断言旧节点不可点、旧回调不执行、旧显示列表不复用。assign/replace/hash/reload分别验历史语义。

## 4. 命中身份与编辑层

### 两套节点索引不一致

`gui.py:_node_index_to_dom:383-387`仅遇元素才计数；`hit_test_at:414-417`遇所有节点都计数、只对元素保存映射。document/text节点会造成索引错位。注释声称两边都是element-only，但源码不是。不能靠元素很少或无文本的按钮测试覆盖这一点。

**实施**：使用稳定node_id和明确映射，命中树直接关联DOM identity；文字命中归属、滚动/transform坐标、焦点和hover统一。验收在按钮前放文本、空白、嵌套span，再点击同一几何点，得到正确按钮并触发同一监听器。

### “原生编辑覆盖层”目前主要是状态模型

`editor_overlay.EditorOverlayController.attach:141-165`创建`EditorState`与`OverlayPlacement`并设置可见状态，没有创建实际Tk Entry/Text。模型中的selection、placement、composition等存在不等同操作系统输入法已经接入。

**实施**：实际原生控件或输入桥、value/selection双向同步、滚动缩放定位、焦点/blur/submit契约；密码/textarea/read-only/disabled分流；中文候选框、组合输入与撤销必须人工或平台自动化验收。

### 无障碍不是只导出一棵树

已有内部可访问性结构、Tab/键盘规则是基础。仍需平台UI Automation等桥接、名称/状态/焦点变化事件与真实读屏验证。没有本轮平台证据时只能写“内部模型已存在”，不能写“完整无障碍已完成”。

## 5. 窗口关闭、资源和版本

`gui.py:1064-1072`注册WM_DELETE_WINDOW回调，设置closing并shutdown文档server，但没有root.destroy/quit；回调替换默认关闭后可能保留窗口，后续重复关闭又因closing直接返回。这是确定的静态遗漏，未以本轮人工点击实测冒充确认。

修复：先取消/回收任务，超时降级，finally销毁root；清理PhotoImage、临时frame与子进程；关闭标签同步失效tab_id/navigation_generation。不要依赖全局gc来代表所有资源已正确归属与释放。

版本计数应绑定真实DOM/布局/绘制阶段。仅让计数增长、回包带版本，不证明旧帧不会覆盖新页；需在延迟响应和快速切页中断言旧结果被丢弃。

## 6. 本轮回归结果的真实含义

### 两种运行时分开报告

| 环境 | 结果 | 解释 |
|---|---|---|
| 已有完整Python3.11.9/Tk/wgpu0.32.0 | runner1914/1914，exit0，760.5s | 内部2条性能FAIL未被计入；不能说所有内部断言通过 |
| 受管Python实际3.13.14，无Tk/wgpu | 1825/1864，exit1，401.4s | 33缺Tk异常、6GPU回退失败，另5导入失败模块50项漏分母 |

日志见 [tests-native-full-stdout.txt](../tests-native-full-stdout.txt)、[tests-full-stdout.txt](../tests-full-stdout.txt)，两套stderr均为空。空stderr不等于未跳过、未吞异常或所有硬件路径均已运行。

### 结果编排缺陷

| 问题 | 证据 | 修复 |
|---|---|---|
| import异常只WARN | `tests/run.py:225-230` | ERROR加入总结果并非零退出，不缩分母 |
| 未知--only仅WARN | `:202-207` | 无效/空选择失败 |
| 测试函数提前return | 多个Tk/GPU环境分支 | 显式SKIP含原因，不能PASS |
| 只打印内部FAIL | `test_p34_r5/r6` helper与总runner未关联 | 抛断言或返回结构化内部失败 |
| 恒真断言 | `test_document_process_spawn.py:54 assert ... or True`、`:59 len(frames)>=0` | 证明真实工作与非空合法frame |
| Tcl隔离无timeout、吞stderr | `tests/run.py:161-166`及子进程处理 | 有界timeout、保留日志、校验summary与实际数量 |
| Tcl计时混入全套耗时 | `:285`使用time.time()-t_wall | 独立记录子进程起止时间 |

完整环境日志里3000节点15.9s和10000节点46.4s均打印FAIL，最后却1914/1914。正确改法不是删除FAIL或扩大阈值，而是先定义它究竟是信息基准还是硬门禁：信息基准用INFO/WARN，硬门禁独立稳定环境并真正失败。

## 7. 工程结构与文档治理

- 功能分包已有价值，但`gui.py`、`browser.py`、`js.py`、`net.py`仍有多条相似主链和兼容回退。新增“完整状态”包装层若不替换真实入口，会进一步增加理解成本。
- README“零依赖”应准确区分CPU标准库路径、Tk组件环境、可选wgpu依赖；标准库模块不保证受管发行版一定打包Tk。
- “真实GPU光栅化”“原生编辑覆盖层”“多核收益落地”等描述应改成可追溯的实现层级：组件/接线/真实端到端验收。
- 历史报告是历史快照不能覆盖；新报告和总索引明确新基线，并给出旧结论哪些已修、哪些仍只完成骨架。
- 建能力矩阵：功能、规范范围、默认入口、测试ID、设备环境、最近验证提交、已知限制；不要以批次数代替产品验收。
- CI建议分无Tk/无GPU headless、完整Tk、GPU可用、GPU不可用回退；版本固定，测试状态明确。外部标准子集和用户任务独立于自有单元测试。

## 8. 可执行实施清单

1. **Q01**：修runner状态、失败传播、恒真断言与超时，生成JSON/JUnit。
2. **A01**：唯一DocumentSession/NavigationController，收敛初次/刷新/历史/about/error/JS导航。
3. **U01**：stable node ID命中，文档原子替换，窗口关闭与资源回收。
4. **A02**：实际解析/JS/layout/raster移进worker，默认UI循环发/收帧，取消与generation门禁。
5. **U02**：真正创建编辑控件和IME桥，不只更新OverlayPlacement。
6. **U03**：平台可访问性与键盘完整用户任务。
7. **Q02/Q03**：固定外部测试子集、真实用户流程、多环境门禁与分级发布。

每项都必须先有修改前失败证据，再有默认入口修改后通过证据；纯类/API存在不得标记任务完成。建议首个端到端fixture：本地打开页面→文本前按钮正确点击→输入中文→提交回环POST→脚本replace→后退/刷新策略一致→about空页→关闭无残留进程。

## 9. 本轮未做的验证

没有人工/物理键盘IME、系统读屏、全网站交互、长时间稳定性或延迟UI心跳测量。以上GUI风险按源码调用链定性，不伪造点击截图和延迟数字。已有全量Tk测试成功是有限证据，应在测试可信化后扩展真正端到端任务。
