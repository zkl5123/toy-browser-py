# 第八轮GUI与工程质量审计

基线`551da74`；本轮未启动人工交互式浏览器验收，不把PageInstance方法测试称为真实鼠标/IME/UIA测试。

## 1. GUI默认调用链

`gui.py:2811`默认提交直接调用page.load_url(url,800,600)，网络、脚本和布局仍可阻塞UI线程。应沿用户实际入口测量心跳/Stop/切标签响应，不能据spawn成功推断UI已异步。

与R7不同，document_process.py现已有真实文档计算能力：`_do_doc_work`（219起）在带HTML的navigate时调用document_renderer，在resize/scroll时复用DocumentState；不是只计数。共享帧也已有frame_shared接线与验证能力。它们的存在不证明默认导航完整委派。约260–262仍注明网络由调用方先fetch，默认GUI同步入口依然存在。

验收应记录UI/worker PID、一次navigate实际走哪条代码、网络发生在哪个进程、帧像素如何到达UI；拿到HTML以后在worker计算是中间里程碑，不能取代整次导航不阻塞的结果。

## 2. 文档生命周期与策略

DocumentState/session代理已引入；about页`_clear_document_state`（213起）清DOM、Host、显示列表、交互状态和信任判定，是修复方向。

但后退/前进（1149–1163）、reload经`_load_no_history`（1165起）读取HTTP后只传html到load_html，丢弃response policy/final URL/资源上下文。真实本地方法探针复现CSP none仍执行inline，详见[安全专题](网络脚本与安全审计.md)。必须先解决这一P0，再评价历史导航“功能完成”。

每次新文档应原子创建身份、策略、请求上下文、存储分区、Host、资源表与取消令牌；同文档restate只改变layout revision；错误/about页明确新身份与opaque规则。禁止“某入口只收html，默认执行脚本”作为远程导航回退。

## 3. 三项全量回归失败及测试可信性

完整环境154模块、2874项：2871通过、3失败、退出1。报告落盘native-full.json和JUnit XML，证明R7内部FAIL漏统计已有改善。不能将3项解释后改写为“实际上全绿”。

- test_gui_back_forward（tests/test_browser.py:1952）：直接改back_stack与当前_hist冲突；在公开导航方法基础上重写回归，分别测试文件→about、HTTP→HTTP、replace、前进后新导航截断。若back_stack承诺可写兼容则同步，否则公开标记只读投影并迁移所有调用方。
- test_p24_comment_doctype_dropped（3721）：旧白名单遗漏新补head；测试应检查标准DOM节点和渲染忽略逻辑。
- t5_half_lead_cache：两浮点偏移仅最后一位不同。采用明确绝对容差、检查误差量，不能用整段try/except或空断言跳过。

导入失败记ERROR、未知only报错、SkipTest和JUnit等改进应保留。新发现`tests/run.py:260–269`超时分支用`e.proc`，而TimeoutExpired没有该属性。有界模拟已复现AttributeError，尚未启动真实卡死进程验证清理。应持有Popen句柄，communicate(timeout)失败后按平台终止进程树，收集部分输出，再生成TIMEOUT。使用假时钟/注入进程验证异常路径，再做受控真实子进程回归。

## 4. 输入、可访问性与地址栏

已有编辑覆盖层、IME/UIA状态契约不能独立证明原生控件与系统事件已通。本轮全量自有测试通过的对应项目属于其原定义范围；未观察中文拼音候选窗、组合态取消、焦点迁移、读屏朗读和真实OS accessibility tree。

应为键盘Tab/Shift-Tab、IME composition、剪贴板、选择/光标、缩放命中、DPI、滚动、表单提交建立可重复实机脚本和人工检查表；报告设备、输入法、读屏版本与观察结果。

地址栏安全状态必须来自实际TLS验证结果，https字符串不等于可信。导航后应展示最终URL、来源与错误类别，拒绝证书与网络错误不能偷偷显示旧页面或旧锁标。

## 5. 工程结构与可维护性

产品已达115文件/约6.2万行Python，复杂度来自重复入口与兼容层，而不只是单文件规模。建议以契约收敛为前提拆分网络授权、脚本WebAPI、文档生命周期、渲染后端，保留现有toy_browser包名和公共导入。

新增模块需附“谁调用、默认入口是否启用、验证级别”。文档中的完成状态分为：组件逻辑、产品接入、回环端到端、真实平台验收。不要把一批数十个逻辑断言全部PASS作为后两级完成的证据。

测试和运行环境：受管Python路径标3.13.12而实际sys.version为3.13.14，缺Tk/wgpu/PIL；完整3.11.9具备依赖。本轮全量与截图/性能用完整环境，纯语义/回环逻辑用受管环境。应锁定依赖、平台、字体、GPU驱动信息，并在无可选依赖时区分SKIP与支持的CPU回退。

## 6. 改进验收

1. 首次/刷新/历史/脚本跳转共享DocumentSession，带CSP禁止页在所有入口都不执行。
2. 默认GUI导航走可取消工作流，慢网络/长脚本期间UI心跳持续；Stop后旧结果不能提交。
3. worker崩溃仅影响对应标签；像素共享段在关闭/取消/异常后释放，持续循环不泄漏。
4. JSON/JUnit/stdout状态一致；超时、导入异常、未知模块、无断言测试均不能虚报成功。
5. 固定外部测试版本、记录每例期望来源；缺实现时明确失败/跳过理由，不将本地改写的小样本称为官方全量符合性。
6. 实机IME/UIA/GPU验证与纯逻辑测试分别签收，未验证项继续公开列出。
