# 沙盒可用性探测

本探测仅检查命令是否存在，并对 Docker/Podman 执行只读的 `version` 查询。它不安装组件、不启动后台引擎、不拉取镜像、不执行生成代码、不上传文件。

运行：`python3 research/spikes/sandbox-probe/probe.py`。仅使用 Python 标准库；结果覆盖本目录的 `results.json`，需要保留多机结果时请先另存该文件。

2026-09-25 在 macOS arm64 上观察到 Docker CLI 28.1.1，但无法连接 daemon；Podman、bubblewrap、Lima、Colima、QEMU 命令未找到。`sandbox-exec` 命令存在，但没有验证其策略、适配器或隔离能力。

文件越界、断网、资源限制、取消清理、撤销授权后重放五类用例均为 `not_run`。这不是五项失败或通过；它们没有执行。后续真实后端验收见 [沙盒设计](../../../docs/architecture/05-sandbox.md)。

结果不保存用户名、socket 路径、远程地址、完整环境变量或原始诊断文本，只保留脱敏状态和版本。
