# 跨平台桌面与运行时分发证据

状态：技术路线候选，等待评审。调研日期：2026-09-24。

本文针对已确认的首版目标：个人可用的经营数据分析客户端，本地保存数据，经授权向云模型发送必要内容。比较桌面外壳、Python 分析进程、分发与持久执行；不决定 Agent 内核框架，也不实现产品。

证据范围：本轮查阅官方文档，未创建 Electron/Tauri 应用，未进行三系统打包、签名、性能或故障恢复实验。下文“事实”有来源，“判断/建议”为本项目候选设计，“待验证”必须通过实验才能结论化。文档的 `latest`、`v2`、`stable` 是访问时的文档分支，不能代替未来实验的依赖锁文件。

## 1. 当前建议

**建议优先验证 Electron + React + TypeScript 桌面程序，以独立 Python worker 执行数据计算；Tauri 2 + React + Python sidecar 保留为替代候选。** 当前依据是 UI 渲染环境、开发语言数量和首次可用版本的交付工作量。没有测量证据表明本项目使用 Electron 会更快或 Tauri 会更省内存。

两者都能作为三系统产品的基础。框架支持 macOS、Windows、Linux，并不代表产品已经支持所有系统版本、CPU 架构和 Linux 发行版。首轮需要明确一个有限支持矩阵，例如 macOS arm64、Windows x64、Ubuntu x64；具体版本仍待评审，Intel Mac、Windows arm64 和其他 Linux 发行版不能默认为已支持。

桌面选型与 Agent 选型可以分别评审。前端经稳定协议访问 Agent 服务，Agent 再调用分析 worker；不要让 React 组件直接依赖内核框架的对象、检查点格式或 Python 函数。

后续需求修订（2026-09-26）：自主任务无需先编排工作流；独立确定性工作流的可编辑画布何时交付仍待讨论。具体行为只在[工作流与轨迹](../architecture/04-workflow-and-trace.md)维护。

## 2. 桌面候选矩阵

以下是相对于个人开发、经营数据分析首版的定性比较，不是性能评分。

| 评审项 | Electron + React + TS + Python | Tauri 2 + React + Python | 本项目含义 |
| --- | --- | --- | --- |
| UI 引擎 | Chromium 多进程，主进程有 Node.js 能力 [E1] | Windows WebView2、macOS WKWebView、Linux WebKitGTK [T1] | Electron 的浏览器版本随应用分发；Tauri 要测试系统 WebView 差异 |
| 开发边界 | TS/JS 桌面与前端，加 Python 分析 | TS/JS 前端、Rust 桌面，加 Python 分析 | Tauri 增加需要维护的 Rust 部分；团队经验可能改变这个权重 |
| 进程隔离 | renderer、main、utility process；Python 另起子进程 [E1][E3] | core、WebView；可打包外部 sidecar [T1][T3] | 两路都需要进程管理、超时、取消和崩溃恢复 |
| 权限机制 | renderer sandbox、context isolation、窄 IPC 接口 [E2] | capabilities/scopes 控制 WebView 可调用能力 [T2] | 权限机制要结合业务动作设计；不能把 worker 的全部文件访问能力交给 renderer |
| Python 分发 | 打包为应用资源并由后台进程启动；具体生命周期由项目实现 | `externalBin` 明确支持 sidecar，按目标架构提供二进制 [T3] | sidecar 机制不会替项目解决 Python 动态依赖和三系统验证 |
| 密钥存储 | `safeStorage` 使用系统相关后端，但各平台保护范围不同 [E4] | Stronghold 是密钥保险库插件，需设计解锁密码及其保管 [T5] | 不能把 Stronghold 的存在直接写成系统 Keychain 已集成 |
| 应用更新 | 内置 `autoUpdater` 支持 macOS/Windows，Linux 另选渠道 [E5] | updater 文档覆盖三系统对应产物，要求更新签名 [T4] | 更新方案必须和实际安装格式一同选定 |
| 原生依赖 | Node 原生模块需匹配 Electron ABI [E7]；Python 扩展独立处理 | Rust crate、系统 WebView 与 Python 扩展均需验证 | 不在两种运行时里重复引入同一套数据引擎，减少打包面 |
| 安装包、内存、启动速度 | 本项目未测量 | 不随应用内嵌 WebView 库，但加入 Python 后总成本未测量 [T1] | 不用网上空壳 demo 的数字作为整套产品预算 |
| 当前建议 | 优先进入小型打包实验 | 保留对照候选 | 评审前不创建完整应用或写入 Accepted ADR |

**改选 Tauri 的触发条件：**用户明确把安装包体积或常驻资源列为首要约束，并且同功能实验显示 Tauri 达到约束、图表和导出功能通过三系统测试，同时愿意维护 Rust 端。只有“看起来更轻”不足以改选。

**继续选择 Electron 的条件：**同功能实验在目标机器上的资源使用可以接受，复杂图表、报告预览、文件操作和 Python 分发可控。其长期成本包括持续跟进 Electron/Chromium 安全更新；这项维护不会因个人版本而消失。[E2]

## 3. 进程与数据传输边界

建议采用以下职责划分，具体 Agent Host 语言由内核评审确定：

```text
React UI（聊天、任务、图表、计划编辑、报告预览）
    ↕ 窄化且带版本的 IPC；只传动作、状态和有限结果
Desktop Host（窗口、选文件、凭据、更新、进程监管）
    ↕ 本地 RPC / stdio
Agent Host（计划、协作、工具权限、执行记录、模型调用）
    ↕ 数据集 ID、查询、产物 ID、进度
Python Analysis Worker（文件解析、SQL/统计计算、结果物化）
```

这是逻辑划分，不要求四个常驻独立服务。若 Agent 内核选 Python，Agent Host 与 worker 可共享发行包，但重计算仍需移出 UI 和桌面主进程。多个 Agent 是任务角色，不能自动等同于必须启动多个常驻 Python 解释器。

### 文件和大结果

建议用户“上传”在桌面端表现为选文件或拖入文件。Desktop Host 记录明确授权的文件；导入服务生成 `dataset_id`、原文件哈希、导入版本和 schema。是否复制文件到项目数据目录由产品评审决定，直接引用原路径要处理源文件被移动或修改的情况。

UI 只取预览页、聚合结果和图表需要的数据。避免把整个 CSV、Excel 或 DataFrame 转成 JSON，先传到 renderer，再送回 Python。较大中间结果写入本地 artifact 文件，由 ID 引用；导出使用后台文件流。分页必须保留总量和筛选条件，不能把预览行数误写成完整分析范围。

任务并发建议从有限数量开始，分别约束模型请求并发和数据计算并发。Python、SQL 引擎和底层数学库可能各有线程池，需要在实验中记录总进程数和线程配置，不能只限制 Agent 数。

### IPC 的候选选择

| 方式 | 建议用途 | 必须处理的事情 |
| --- | --- | --- |
| Electron IPC / Tauri commands | UI 到 Desktop Host | 消息 schema、调用方、动作白名单；不暴露任意 shell、任意路径读写或完整底层 IPC 对象 |
| 父子进程 stdio RPC | 本地 worker 的首选实验方式 | 消息长度上限、请求 ID、取消、超时、背压、协议版本；stdout 只作协议，日志走 stderr |
| Electron MessagePort | TS/Node 进程之间的流式事件 | 不能直接假设 Python 能消费 Node MessagePort；需经宿主桥接 [E3] |
| 回环 HTTP/WebSocket | 需要多客户端或现成协议时再选 | 绑定 loopback、随机端口、每次启动的凭证、校验 Origin/Host、禁止宽泛 CORS；不得监听 `0.0.0.0` |
| Unix socket / Windows named pipe | 可替代 stdio 的后续候选 | 两套平台实现、权限和重连；未证明需要前不要提前引入 |

上述防护是候选工程要求。回环端口并不天然可信；CORS 也不能代替服务认证。内置 worker 不应因“本地服务”而接收任何进程的请求。

Electron `utilityProcess` 是带 Node.js 的子进程机制，不是 Python 运行时。若采用它，可让其承担 TS Agent Host；启动 Python 仍需相应子进程接口和监管。[E3]

### 生命周期

建议握手至少包含 `protocol_version`、worker 版本、已加载能力、就绪状态。异常不能统一归为“模型思考中”。UI 要能区分下载/加载、等待授权、模型请求、数据计算、导出、失败和恢复。

需要明确定义：关闭窗口、退出应用、系统休眠、worker 被杀、内核异常、模型断网分别如何处理。首版建议“应用退出后停止执行，重启后可恢复或明确重试”；用户若要求退出后继续，才评审系统后台服务、托盘行为和开机启动。

终止分析要先发取消请求，超时再结束相关进程树。Mac/Linux 的信号与 Windows 子进程行为不同，不能仅在 Mac 验证父进程退出。所有强制终止均留下 interrupted/unknown 记录，不能伪装成成功取消。

## 4. 安全能力的实际范围

### UI 防护不等于分析代码沙箱

**事实：**Electron 建议启用 context isolation 和 renderer sandbox，校验 IPC sender，并限制导航、远程内容和权限。[E2] Tauri capabilities 控制某个窗口/WebView 的命令权限；多个 capability 的权限会合并，应用自定义命令也需要核对实际暴露规则。[T2]

**判断：**上述机制保护的是 UI 到宿主的边界。普通 Python sidecar、MCP stdio 服务和由其启动的子进程，通常继承启动用户可用的权限；换成 Tauri 不会自动获得文件、网络和资源的 OS 级隔离。

执行隔离已进入 K1；后端选择与验证条件见[沙盒设计](../architecture/05-sandbox.md)。本页只说明 UI 与宿主边界。

报告中的 HTML、Markdown、SVG、第三方网页和模型生成的可执行内容都要按不可信输入处理。建议图表先采用声明式配置；预览不授予桌面 IPC；外部链接交给系统浏览器。后续如要内嵌浏览器研究能力，应单独定义浏览器权限与数据交换，不默认复用主窗口权限。

### 密钥和数据外发

**事实：**Electron 的 `safeStorage` 在 macOS、Windows、Linux 上使用不同后端和安全语义。Windows DPAPI 不承诺隔离同一用户下的其他应用；Linux 可能返回 `basic_text`，文档说明此时不能获得有效存储保护。[E4]

**建议：**设置页存 `credential_ref`，主进程取密钥，渲染进程和日志不接收明文。Linux 无可靠密钥后端时默认只在本次会话保留，不悄悄降级为普通配置文件。更新签名变化、Keychain 被锁或用户拒绝访问，要有可恢复错误提示。

Tauri Stronghold 提供保险库和密码派生接口；如果选择它，还要决定解锁密码来自用户输入还是系统凭据存储。不能把示例中写在 JS 里的固定密码搬进产品。[T5]

本地保存与授权外发建议分成两类权限：工具能读取哪些本地资源；哪些已筛选内容可以发送给哪个模型/TTS/MCP 服务。批准一次模型服务不自动授权发送全表、历史会话或其他项目数据。授权记录应可回看，且只保存必要摘要；API key、业务内容和原始请求日志不纳入 Git。

MCP 服务既可能是本地可执行程序，也可能是网络服务。接入时需要记录来源、版本、启动命令或地址、能力清单和授权范围。Skill 中的文本不能自行提升工具权限；若 Skill 带脚本，其执行服从相同边界。

## 5. 分发、签名与升级

### Python 运行时

**事实：**PyInstaller 可以把解释器与依赖一起打包，使用者无需预装 Python。其产物与构建时的 OS、Python 和架构有关；动态导入可能需要 hooks/hidden imports。[P1]

**建议：**首个打包实验用固定 Python 版本和依赖锁文件；先尝试目录式分发以便检查依赖，再决定是否有必要转为单文件。用户安装客户端后不应被要求执行 `pip install` 或配置自己的 Python。

需要覆盖解析器、数据计算库和原生扩展，记录 bundle 内动态库、许可证清单、运行时加载方式及可执行文件哈希。开发环境能 import 不等于打包应用能加载。若未来增加 OCR、本地语音或本地模型，宜把大型资源设计成独立且校验完整性的可选包，重新评估磁盘和分发成本。

### 三系统交付矩阵

| 平台 | 首轮候选交付物 | 主要验证点 | 当前状态 |
| --- | --- | --- | --- |
| macOS | `.app` + `.dmg`，优先当前机器架构 | 包内 Python/动态库签名；Developer ID、公证、Gatekeeper；路径与 Keychain；升级后旧数据可读 [E6][T6] | 未构建、未签名、未公证 |
| Windows | 安装包；格式随所选更新器确定 | Python exe/DLL、签名、普通用户安装、长路径/中文路径、进程树终止、升级时文件占用 [E5][T7] | 未构建、未测试 |
| Linux | 先指定一种发行版；AppImage 或发行版包 | glibc/系统库、图形环境、WebView 依赖（Tauri）、secret service、安装权限和更新渠道 [E5][T1][T4] | 未构建、未测试 |

**事实：**macOS 对外分发的签名和公证是独立步骤。Tauri 的 ad-hoc 签名不能当作 Apple 验证的分发身份。[E6][T6] Windows 签名机制也要单独配置。[T7] 本文不承诺签名后所有设备都不会提示安全或信誉警告。

**建议：**个人本机验证可以先做开发包；给其他使用者试用前，补齐签名、公证、校验和、版本说明、卸载与数据保留策略。当前不需要购买证书，也不需要对外发布。

### 升级必须同时处理应用和数据

Electron 官方内置更新器不覆盖 Linux。[E5] 若最终选 Electron，需要在 Linux 包管理器更新、手动下载，或另一个经过评审的更新组件中做出明确选择；不能写成“Electron 自带三平台自动升级”。Tauri updater 支持相应的 Linux AppImage、macOS、Windows 更新产物，且要求更新签名。[T4]

建议升级包包含桌面、Agent Host、Python worker 的兼容版本。执行任务时延后安装；安装前保存状态、停止 worker、备份需迁移的本地元数据。数据 schema 迁移要有版本、可重入处理和失败恢复。恢复旧应用不等于数据库自动兼容旧版本；不可逆迁移需有备份恢复路径。

## 6. workflow 与恢复

当前执行语义只在[工作流与轨迹](../architecture/04-workflow-and-trace.md)维护，内核路线见[选型](../architecture/02-kernel-selection.md)。本页保留桌面进程和分发证据。

## 7. 进入实现前的小型实验

所有实验应放在 `research/spikes/`，用合成输入、固定版本与可复现脚本。两种壳对照时使用同一 React 页面、同一 Python 分析程序、同一输入和相同指标。可先做 Electron 样本；只有体积、内存或 WebView 取舍仍有实质疑问时再投入完整 Tauri 对照，避免把框架竞赛变成主项目。

| 实验 | 需要回答的问题 | 可审核结果 | 状态 |
| --- | --- | --- | --- |
| S-D1 桌面 + Python 打包 | 没装开发依赖的机器能否导入合成文件并计算？ | 包版本/哈希、环境、输入哈希、日志摘要、失败记录 | 未运行 |
| S-D2 文件与 IPC | 大文件是否阻塞 UI；分页、取消和流式事件是否正确？ | 文件大小/行数、schema、峰值进程树内存、UI 响应时间、取消结果 | 未运行 |
| S-D3 恢复 | 导入、模型请求、结果落盘之间崩溃会怎样？ | 每个故障点的前后状态、重复调用计数、产物哈希、未知状态处理 | 未运行 |
| S-D4 权限 | renderer/Skill/MCP 能否越权读取文件或调用工具？ | 越权用例与拒绝记录；正常动作仍可完成 | 未运行 |
| S-D5 安装与升级 | 安装、卸载、升级失败是否损坏本地项目？ | 数据迁移与恢复记录、旧/新版本兼容矩阵 | 未运行 |

性能输入建议分两级：代表日常工作的中等合成数据和显著更大的压力数据。具体大小由数据样本讨论后确定；应同时记录字节数、行列数、字段类型、文件格式和查询复杂度。只报“支持百万行”无法比较内存或响应时间。

资源测量分为空闲、模型流式输出、导入、查询、图表交互、导出。记录整个进程树的内存、冷启动、磁盘包大小和临时空间；跨操作系统的指标口径必须写清。通过标准由实验前评审确定，不能观察结果后再调整门槛。

### 三系统 smoke gates

以下是未来的最小验收门槛，当前全部未验证。Mac 上通过的结果只能标记为 Mac 已验证。

| Gate | 三系统都需要的结果 | 额外平台检查 |
| --- | --- | --- |
| G1 干净环境安装 | 不依赖用户 Python/Node；可启动、卸载、重新安装 | Mac 签名/公证；Windows 普通用户权限；Linux 系统依赖 |
| G2 数据导入 | CSV/XLSX/Parquet 的选定支持范围；中文/空格路径；损坏输入有明确错误 | 大小写路径差异、文件锁、编码、日期时区 |
| G3 图表与交互 | 同一结果能分页、筛选、下钻、导出，状态一致 | Tauri 三种 WebView；字体、DPI、快捷键 |
| G4 进程恢复 | UI/worker 强杀后可恢复或显示明确失败；不留无主进程 | Windows 子进程树；Mac 关窗和退出区别；休眠恢复 |
| G5 云授权与凭据 | 未授权内容不发出；取消有效；日志无密钥 | Linux 无 secret service；Keychain 拒绝；凭据失效 |
| G6 MCP 生命周期 | 启动/断开/超时/撤销权限有明确状态 | 本地可执行文件路径、stdio 编码与进程回收 |
| G7 更新与数据 | 新版本读取旧项目；失败可恢复；运行中不强行换 worker | 各安装格式对应的更新机制与文件锁 |
| G8 结果证据 | 相同合成业务问题的数值与来源符合预期 | 工程通过和业务分析正确分别出具结果 |

## 8. 当前评审入口

研发顺序与本轮问题见[设计入口](../00-discovery-summary.md)。桌面候选尚未接受，本页 2026-09-24 的来源记录不代表后续平台实测。

## 9. 官方来源记录

访问日期均为 **2026-09-24**。未注明精确版本的滚动文档仅用于能力与边界核查；后续实验必须固定软件版本、依赖锁文件和构建环境。

| 编号 | 来源与文档版本 | 本次核查范围 |
| --- | --- | --- |
| E1 | [Electron Process Model](https://www.electronjs.org/docs/latest/tutorial/process-model)，`latest` | main/renderer/utility process 与 Node.js 边界 |
| E2 | [Electron Security](https://www.electronjs.org/docs/latest/tutorial/security)，`latest` | context isolation、sandbox、IPC sender、远程内容与更新建议 |
| E3 | [Electron utilityProcess](https://www.electronjs.org/docs/latest/api/utility-process)，`latest` | Node 子进程与 MessagePort；不是 Python 解释器 |
| E4 | [Electron safeStorage](https://www.electronjs.org/docs/latest/api/safe-storage)，`latest` | 各系统后端、Linux basic_text、macOS 签名身份相关性 |
| E5 | [Electron autoUpdater](https://www.electronjs.org/docs/latest/api/auto-updater)，`latest` | macOS/Windows 支持，Linux 无内置支持 |
| E6 | [Electron Code Signing](https://www.electronjs.org/docs/latest/tutorial/code-signing)，`latest` | 签名、公证与工具配置 |
| E7 | [Electron Native Node Modules](https://www.electronjs.org/docs/latest/tutorial/using-native-node-modules)，`latest` | 原生模块与 Electron ABI/rebuild |
| T1 | [Tauri Process Model](https://v2.tauri.app/concept/process-model/)，Tauri 2 文档 | Core/WebView、平台 WebView、库不随应用内嵌 |
| T2 | [Tauri Capabilities](https://v2.tauri.app/security/capabilities/)，Tauri 2 文档 | 窗口权限、权限合并、自定义命令默认暴露与远程访问边界 |
| T3 | [Tauri Embedding External Binaries](https://v2.tauri.app/develop/sidecar/)，Tauri 2 文档 | `externalBin`、目标 triple、sidecar 参数权限 |
| T4 | [Tauri Updater](https://v2.tauri.app/plugin/updater/)，Tauri 2 插件文档 | 更新签名、平台产物、HTTPS、Windows 更新前退出 |
| T5 | [Tauri Stronghold](https://v2.tauri.app/plugin/stronghold/)，Tauri 2 插件文档 | 密钥保险库、密码派生与保存方式 |
| T6 | [Tauri macOS Code Signing](https://v2.tauri.app/distribute/sign/macos/)，Tauri 2 文档 | 签名、公证、ad-hoc 的边界 |
| T7 | [Tauri Windows Code Signing](https://v2.tauri.app/distribute/sign/windows/)，Tauri 2 文档 | Windows 签名及平台构建配置 |
| P1 | [PyInstaller Operating Mode](https://pyinstaller.org/en/stable/operating-mode.html)，页面标注 6.22.3 | 自包含分发、OS/解释器相关性、动态导入处理 |
| W1 | [React Flow Computing Flows](https://reactflow.dev/learn/advanced-use/computing-flows)，现行文档 | 节点数据流与外部数据处理 |
| W2 | [React Flow Save and Restore](https://reactflow.dev/examples/interaction/save-and-restore)，现行文档 | 图编辑状态保存/恢复 |
| W3 | [LangGraph Persistence](https://docs.langchain.com/oss/python/langgraph/persistence)，Python 现行文档 | checkpointer/store、内存后端丢失、SQLite 定位 |
| W4 | [LangGraph Checkpointers](https://docs.langchain.com/oss/python/langgraph/checkpointers)，Python 现行文档 | 线程检查点、故障恢复与后端选项；具体行为待锁版本实验 |
| W5 | [Temporal Workflow Execution](https://docs.temporal.io/workflow-execution)，现行文档 | Event History、replay、Service/Worker 与执行状态 |

本轮未安装新运行时、未修改系统设置、未获得签名证书，也未运行任何应用性能测试。

[E1]: https://www.electronjs.org/docs/latest/tutorial/process-model
[E2]: https://www.electronjs.org/docs/latest/tutorial/security
[E3]: https://www.electronjs.org/docs/latest/api/utility-process
[E4]: https://www.electronjs.org/docs/latest/api/safe-storage
[E5]: https://www.electronjs.org/docs/latest/api/auto-updater
[E6]: https://www.electronjs.org/docs/latest/tutorial/code-signing
[E7]: https://www.electronjs.org/docs/latest/tutorial/using-native-node-modules
[T1]: https://v2.tauri.app/concept/process-model/
[T2]: https://v2.tauri.app/security/capabilities/
[T3]: https://v2.tauri.app/develop/sidecar/
[T4]: https://v2.tauri.app/plugin/updater/
[T5]: https://v2.tauri.app/plugin/stronghold/
[T6]: https://v2.tauri.app/distribute/sign/macos/
[T7]: https://v2.tauri.app/distribute/sign/windows/
[P1]: https://pyinstaller.org/en/stable/operating-mode.html
[W1]: https://reactflow.dev/learn/advanced-use/computing-flows
[W2]: https://reactflow.dev/examples/interaction/save-and-restore
[W3]: https://docs.langchain.com/oss/python/langgraph/persistence
[W4]: https://docs.langchain.com/oss/python/langgraph/checkpointers
[W5]: https://docs.temporal.io/workflow-execution
