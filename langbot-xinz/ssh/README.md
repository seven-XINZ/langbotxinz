# Md2QQImage

## 安装

配置完成 [QChatGPT](https://github.com/RockChinQ/QChatGPT) 主程序后使用管理员账号向机器人发送命令即可安装：

关键点说明:

依赖: 明确需要在 requirements.txt 中加入 paramiko。
配置: 使用 config.json 来存储设备列表和超时设置，使其更灵活。提供了 config.json.example。
状态管理: 使用 self.user_sessions 字典来跟踪每个用户（在特定聊天中）的连接状态（idle, selecting_device, connected）。session_key 由 (user_id, chat_id) 组成，确保不同聊天环境的状态隔离。
异步处理: 由于 paramiko 本身是同步库，而 LangBot 的事件处理器是 async 的，因此将所有阻塞的 SSH 操作（连接、认证测试、执行命令）都包装在 loop.run_in_executor(None, ...) 中，使其在线程池中运行，避免阻塞 LangBot 的主事件循环。Ping 操作则使用了 asyncio.create_subprocess_exec。
交互流程: 通过检查和更新 user_sessions 中的状态，模拟了多步交互：
/ssh -> 显示菜单，状态变为 selecting_device。
用户输入数字 -> 验证选择，尝试连接，成功则状态变为 connected 并保存 ssh_client，失败则提示错误并重置状态。
用户输入命令 (当状态为 connected) -> 执行命令，显示结果，保持 connected 状态。
用户输入 exit (当状态为 connected) -> 显示摘要，关闭连接，状态变回 idle。
错误处理: 在各个步骤（Ping, 认证, 连接, 命令执行）中加入了 try...except，并尝试向用户返回有意义的错误提示。
资源清理: 在 _clear_user_state 函数和插件的 destroy 方法中添加了关闭 SSH 连接的逻辑，确保资源被释放。
安全性: 使用 paramiko.AutoAddPolicy() 自动接受未知的主机密钥，这在生产环境中不安全，建议替换为 WarningPolicy 并进行手动验证或使用 known_hosts 文件。密码直接存储在配置文件中也是不安全的，实际应用中应考虑使用 SSH 密钥对或更安全的凭证管理方式。
命令执行: 示例代码将 stdout 和 stderr 合并输出，并在输出中标记了 stderr。可以根据需要调整输出格式。
## 使用


这只是一个加载程序内部包含一些案例，可以让用户自主添加小程序，比如/天气 /色图 /今日运势 之类的各种小功能

小程序开发及其简单，把你需要的功能告诉gpt然后把我的案例程序代码给GPT，GPT生成后丢到data目录下即可，目前还在更新测试

使用方法：

将上述三个部分（目录结构建议、requirements.txt、config.json.example、main.py）组合起来。
根据 config.json.example 创建并编辑您自己的 config.json 文件，填入您的 SSH 设备信息。
将整个插件目录（例如 MySshPlugin）放入 LangBot 的 plugins 文件夹。
确保您的 LangBot 环境安装了 paramiko (pip install paramiko)。
启动或重启 LangBot。
在聊天中发送 /ssh 命令开始使用。


