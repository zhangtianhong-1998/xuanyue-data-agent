# 执行沙盒与用户画像：证据和取舍

日期：2026-09-25。状态：`research`。这里记录上游事实、调研判断和未验证项，不表示方案已经批准。沙盒的产品契约只在 [执行沙盒设计](../architecture/05-sandbox.md) 定义，本文不重复该契约。

## 1. 这次调研改变了什么

- **建议**：沙盒后端独立于 Agent 框架选型。先保留一个产品自有的适配接口，以本地 OCI 容器后端做首轮候选实验；不要因为某框架附带 Sandbox 类就直接确定整个内核。
- **事实**：AgentScope Runtime、OpenSandbox、Docker/Podman、操作系统原语处在不同层次。前两者提供执行和生命周期接口，实际隔离仍依赖配置及底层运行环境。[S1][S2][S3][S4]
- **事实**：本机 Docker 客户端可用，daemon 不可连接；本轮只完成只读可用性探测，隔离负测未执行。[本机结果](../../research/spikes/sandbox-probe/results.json)
- **建议**：用户画像由产品拥有更新和删除规则，框架 Memory API 只承担适配。永久保存、冲突解决和授权不能交由模型自行决定。

## 2. 沙盒候选比较

以下是文档和源码层面的能力核对。没有一行表示本项目已经完成相应的安全验证。

| 候选 | 查到的事实 | 对本产品的判断 | 尚需验证 |
| --- | --- | --- | --- |
| Docker / Podman + 自有适配器 | Docker Desktop 面向 Mac、Windows、Linux；Mac 的 Docker daemon/container 在 Linux VM 内运行。Podman 在 Mac、Windows 需要虚拟机，在 Linux 可选；具体 VM provider 随平台不同。[S3][S4] | 适合先统一 Python/命令运行环境，但用户需要运行容器/虚拟机后端。应比较个人安装成本和资源占用，再定默认后端 | 三系统安装、ARM64/x86_64 镜像、目录语义、网络、资源限制、升级恢复；Docker Desktop 分发/使用条件需在产品发布前另行核对 |
| AgentScope Runtime | 官方提供 Python、shell、浏览器、文件工具沙盒；本地可选 Docker、BoxLite，远程有其他后端。独立 Runtime README 的 LangGraph 适配表中，Message/Event 为支持，Tool 为进行中。[S1] | 有复用价值，但不把 AgentScope Agent 框架、Runtime 服务和具体沙盒后端视作一个不可拆的选项。换内核时仍可评估复用沙盒接口 | 所选固定版本的 LangGraph 工具适配、取消、资源限制、默认网络、消息映射；尚未本地运行 |
| OpenSandbox | 提供 Docker/Kubernetes 运行接口；本地示例要求 Docker。当前固定源码中，`docker.network_mode` 默认 `host`，网络策略需要 `bridge` 和 egress sidecar；`dns` 模式不执行静态 IP/CIDR 规则，IPv6 有已记录的覆盖缺口。[S2] | 生命周期管理值得参考；不能直接把默认配置当作产品所需隔离策略。单人桌面 MVP 是否需要额外服务，取决于自有薄适配器的维护成本 | 切换 bridge、DNS/IP/IPv6 绕过、sidecar 异常、清理和本地服务认证；尚未本地运行 |
| BoxLite | 官方支持表列出 Apple Silicon macOS、Linux x86_64/ARM64、Windows WSL2 x86_64；Intel Mac 标为 Coming soon。Windows 要求 WSL2 的 KVM 支持。[S5] | 可以作为后续嵌入式 VM 候选；当前证据不支持“所有 Mac/Windows 零依赖原生运行” | 本机嵌入、分发、签名、启动延迟、Windows 适配；本轮未安装 |
| E2B 云沙盒 | 官方文件 API 将本地文件内容写入 sandbox；其安全页说明托管存储环境。SDK 提供网络控制选项。[S6] | 云执行需要独立的数据外发授权；“关闭沙盒出网”不会撤回此前客户端上传的数据。可作为自选远程后端，不能静默替代本地后端 | 地域、保留/删除、服务条款和用户授权粒度；本轮未创建云沙盒、未上传文件 |
| Linux bubblewrap / Landlock | bubblewrap 明确说明自己不是带完整安全策略的现成沙盒；保护边界取决于调用参数。Landlock 是进程主动收紧权限的 Linux 机制，能力需检查实际 ABI；网络规则并非通用域名白名单。[S7][S8] | 适合 Linux 专用后端或补充限制，不解决三系统统一交付。不能仅检测到命令存在就声明已隔离 | 文件/链接/设备/IPC、实际内核能力、缺失能力时拒绝执行、资源限制组合 |
| Windows AppContainer / macOS App Sandbox | AppContainer 覆盖文件、网络、进程等资源；Apple 以签名、entitlements 和用户文件选择扩展 App Sandbox 的访问范围。[S9][S10] | 适合原生受限 worker 的独立研究，需要各平台实现和测试。不能由一个跨平台 `subprocess` 包装自动获得 | Win32/Python 依赖兼容性、子进程、文件授权、签名/打包和限制策略；本轮未实现 |

**工程判断**：个人版本先验证“本地受限后端 + 后端不可用就停用任意代码执行”最容易把边界做清楚。需要容器/虚拟机是安装体验上的代价，不应隐藏。如果这项代价不可接受，下一步应做嵌入式 VM 或原生 worker 实验，再调整方案。

## 3. 两个容易混淆的边界

**桌面 UI 沙盒与代码执行沙盒。** Electron renderer sandbox 限制渲染进程；特权操作经 IPC 交给主进程。它不自动约束主进程启动的 Python 工具。Electron 官方还说明，开启 renderer 的 Node.js integration 会关闭该进程沙盒。[S11] 因此，本产品的 renderer 配置和生成代码后端必须分别验收。

**容器与权限策略。** Docker 官方警告：控制 daemon 的主体可以挂载宿主目录；默认容器没有自动配置 CPU/内存上限。[S12][S13] 因此，Agent 不能自行提交 Docker CLI 参数或接触 socket；仅凭“在 Docker 中运行”不足以证明文件、网络和资源限制有效。

## 4. 本地探测结果及可复现范围

环境：2026-09-25，macOS arm64。脚本见 [sandbox-probe](../../research/spikes/sandbox-probe/README.md)，只依赖 Python 标准库。

| 观察项 | 结果 | 允许得出的结论 |
| --- | --- | --- |
| Docker CLI | 28.1.1；version 查询 exit 1，Server 为空 | 客户端存在，当前进程不能连接 daemon |
| Podman、bubblewrap、Lima、Colima、QEMU 命令 | 未找到 | 当前 PATH 中没有这些命令，不证明整台机器完全没有相关软件 |
| sandbox-exec | 命令存在 | 仅证明存在，未验证策略或兼容性 |
| 文件越界、网络阻断、资源限制、取消清理、撤销授权后重放 | 全部 `not_run` | 没有执行隔离负测，不计入通过项或失败项 |

本轮没有启动 daemon、安装后端、下载镜像或执行云沙盒。可用性探测的失败与缺失状态已经留在可公开结果中。接下来的实验应先确认真实后端可用，再逐项运行 [沙盒验收用例](../architecture/05-sandbox.md#7-验证顺序与当前缺口)。

## 5. 用户画像：框架给了什么，产品还要决定什么

**上游事实。** LangGraph 文档区分会话检查点和跨会话 Store，并指出不断重写的大型 profile 容易出错。AgentScope 的历史教程提供 `LongTermMemoryBase` 和 Mem0、ReMe 示例；该教程不能作为本轮 2.0.8 发布包接口证明，采用前需重新核对。[M1][M2]

**推断。** 这些接口可以装载画像记录，但不自动解决本产品的证据、纠正、过期、权限和彻底删除。接入向量检索也不会补齐这些规则。

当前画像规则只在[长期画像](../architecture/06-profile-memory.md)维护。历史教程接口不作为 AgentScope 2.0.8 已验证能力；本轮没有真实用户行为学习实验。

## 6. 证据登记

全部链接访问日期为 **2026-09-25**。固定源码链接已按相应 commit 读取并核对相关段落；在线文档为访问日快照，没有把 `latest/main` 当作锁定发布版。以下事实仅代表文档/源码审阅范围，不是独立复现。

| ID | 官方来源 / 固定版本 | 核对范围 |
| --- | --- | --- |
| S1 | [AgentScope Runtime 沙盒文档](https://runtime.agentscope.io/en/sandbox/sandbox.html)；[README，22072fd7075ce0c6f43cb39509d6a14b0e60ddb5](https://github.com/agentscope-ai/agentscope-runtime/blob/22072fd7075ce0c6f43cb39509d6a14b0e60ddb5/README.md) | 后端、工具类别、框架适配表 |
| S2 | [OpenSandbox README，f3950db2499e8d572694bf9939e4bf985a2eab8a](https://github.com/opensandbox-group/OpenSandbox/blob/f3950db2499e8d572694bf9939e4bf985a2eab8a/README.md)；[同版本 configuration.md](https://github.com/opensandbox-group/OpenSandbox/blob/f3950db2499e8d572694bf9939e4bf985a2eab8a/server/configuration.md) | Docker/Kubernetes、默认网络、egress 前提和覆盖边界 |
| S3 | [Docker Desktop](https://docs.docker.com/desktop/)；[Mac 权限与 VM](https://docs.docker.com/desktop/setup/install/mac-permission-requirements/) | 平台和虚拟机边界；未评估商业许可 |
| S4 | [Podman machine 官方手册](https://docs.podman.io/en/latest/markdown/podman-machine.1.html) | Mac/Windows 的 VM 前提与平台 provider |
| S5 | [BoxLite README，a35976c088b584dbc6836377344c2770d456d451](https://github.com/boxlite-ai/boxlite/blob/a35976c088b584dbc6836377344c2770d456d451/README.md) | 平台支持表、WSL2/KVM、Intel Mac 状态 |
| S6 | [E2B 上传文件](https://docs.e2b.dev/filesystem/upload)；[安全页](https://e2b.dev/security)；[SDK 网络选项，ccaf9fc0ffe6ac39c7ec786af7608ab1de19467b](https://github.com/e2b-dev/E2B/blob/ccaf9fc0ffe6ac39c7ec786af7608ab1de19467b/packages/js-sdk/src/sandbox/sandboxApi.ts) | 客户端上传路径、托管环境、网络选项；未验证服务保留政策 |
| S7 | [bubblewrap README，f8e1e5077eb8613ca559bac0d422282a910015d6](https://github.com/containers/bubblewrap/blob/f8e1e5077eb8613ca559bac0d422282a910015d6/README.md) | 策略由调用方决定，非现成完整沙盒 |
| S8 | [Linux Landlock 官方文档](https://docs.kernel.org/userspace-api/landlock.html) | ABI 探测、文件和网络规则；不假定目标机器具有最新 ABI |
| S9 | [Microsoft AppContainer isolation](https://learn.microsoft.com/en-us/windows/win32/secauthz/appcontainer-isolation) | 文件、网络、进程等隔离目标；未验证 Python 适配 |
| S10 | [Apple：访问 App Sandbox 外文件](https://developer.apple.com/documentation/security/accessing-files-from-the-macos-app-sandbox) | 用户选中文件、签名、entitlements、访问撤销 |
| S11 | [Electron Process Sandboxing](https://www.electronjs.org/docs/latest/tutorial/sandbox) | renderer/main 边界、Node integration |
| S12 | [Docker Engine security](https://docs.docker.com/engine/security/) | daemon 权限与挂载风险 |
| S13 | [Docker resource constraints](https://docs.docker.com/engine/containers/resource_constraints/) | 默认无限额、CPU/内存限制 |
| M1 | [LangGraph Memory overview](https://docs.langchain.com/oss/python/concepts/memory) | 检查点、Store、profile/collection 的边界 |
| M2 | [AgentScope Long-Term Memory](https://doc.agentscope.io/tutorial/task_long_term_memory.html) | 控制模式和自定义接口；未复现第三方记忆服务 |
