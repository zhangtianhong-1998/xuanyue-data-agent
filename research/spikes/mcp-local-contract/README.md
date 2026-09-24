# SP-08：本地 MCP 协议与工具返回契约

执行日期：2026-09-24。状态：自编合成实验，非产品实现；没有模型、云服务或真实业务数据。

## 运行

在本目录执行 `uv run --frozen --python 3.12 run.py`。完整依赖及哈希见 `uv.lock`；本轮实际使用 Python 3.12.13、`mcp==2.2.0`、`mcp-types==2.2.0`。

真实客户端通过 stdio 启动另一个 Python 进程运行自编 MCPServer。观测到的协议版本为 `2026-07-28`。服务器只提供合成的地区贡献、状态探测、可取消等待和无真实副作用的导出计数器。

## 结果与失败记录

最终 `results.json` 的 12 项检查通过：协议版本可见；工具枚举；输入 schema；结构化结果；无效参数报错；未知工具报错；宿主权限拒绝；独立进程；被拒绝工具没有执行；取消到达已启动的服务端工具；未列入环境白名单的假标记未继承；退出后服务端进程已结束。

首次运行在 7 项检查后失败，已保存为 `failures/attempt-01-untyped-status.json`，对应的两个脚本也保留在该目录。原因是我们把普通 `dict` 返回值直接当成必有 `structured_content` 的结果。当前 SDK 并没有为这个裸 `dict` 注解生成结构化返回，客户端读取空值时报错；并发探测任务随后因连接关闭报错。

修正：状态工具使用明确 Pydantic 返回模型；客户端检查错误与结构化内容，并在异常路径清理正在等待的任务。没有修改验收目标来让测试通过。

设计影响：每个工具适配器都应声明接受 text 还是 structured result；消费前校验，不能无条件把 MCP 返回文本 `json.loads` 后当可信业务对象。`list_tools` 只代表工具存在，授权仍由宿主检查。

## 边界

- 同版本自编客户端/服务端，不证明所有第三方或旧版 MCP 服务兼容。
- 宿主 gate 是实验自己的白名单，测试只说明这条调用路径拒绝成功；没有构建完整的 Agent 权限系统。
- 取消只覆盖一个协作式异步工具，不保证任意同步 native 计算或外部程序可被及时取消。
- 没有测试远程 transport、OAuth、超大输出、断线重连、恶意进程隔离、云端外发或三系统分发。
- 环境白名单不是系统沙箱；服务端仍是当前用户运行的普通子进程。

参考（访问日 2026-09-24）：[官方 Python SDK](https://github.com/modelcontextprotocol/python-sdk)、[客户端结果](https://py.sdk.modelcontextprotocol.io/client/)、[stdio 生命周期](https://py.sdk.modelcontextprotocol.io/client/transports/)、[协议传输](https://modelcontextprotocol.io/specification/2026-07-28/basic/transports)。
