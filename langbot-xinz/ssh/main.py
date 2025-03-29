#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
LangBot SSH 插件模板
版本: 1.0.0
描述: 通过 LangBot 连接和管理 SSH 设备。
作者: Your Name (基于 JS 示例改编)
"""

# --- 标准库导入 ---
import os
import json
import logging
import traceback
import time
import datetime
import asyncio
import subprocess
import shlex
from typing import Dict, Any, Optional, List, Tuple

# --- 第三方库导入 ---
try:
    import paramiko
except ImportError:
    print("错误：缺少 'paramiko' 库。请在 LangBot 环境中执行 'pip install paramiko'")
    paramiko = None # 设置为 None 以便后续检查

# --- LangBot 核心模块导入 ---
try:
    from pkg.plugin.context import register, handler, BasePlugin, APIHost, EventContext
    from pkg.plugin.events import GroupNormalMessageReceived, PersonNormalMessageReceived
    from pkg.platform.types.message import Plain
    # from pkg.core.app import Application # 如果需要 Application 类型提示
except ImportError as e:
    print(f"错误：无法导入 LangBot 框架核心模块: {e}")
    raise

# --- 插件注册信息 ---
@register(
    name="MySshPlugin",
    description="通过聊天界面管理 SSH 连接和执行命令",
    version="1.0.0",
    author="Your Name"
)
class SshPlugin(BasePlugin):
    """SSH 管理插件主类"""

    # --- 初始化与状态管理 ---
    def __init__(self, host: APIHost):
        super().__init__(host)
        self._logger = None
        self.plugin_config: Dict[str, Any] = {}
        # 用户会话状态存储: key 为 (user_id, chat_id) 元组, value 为会话信息字典
        # chat_id 对于私聊是 'person'，对于群聊是 group_id
        self.user_sessions: Dict[Tuple[str, str], Dict[str, Any]] = {}

        self._setup_logger()
        self.plugin_config = self._load_plugin_config()
        if self.plugin_config.get("debug", False):
             self._logger.setLevel(logging.DEBUG)
        self._logger.info("SSH 插件同步初始化完成。")
        if not paramiko:
             self._logger.error("Paramiko 库未安装或导入失败，SSH 功能将不可用！")

    def _setup_logger(self):
        """设置 Logger"""
        try:
            self._logger = self.ap.logger.getChild(self.plugin_name())
        except AttributeError:
            self._logger = logging.getLogger(self.plugin_name())
            if not self._logger.hasHandlers():
                 handler_ = logging.StreamHandler(sys.stdout)
                 handler_.setFormatter(logging.Formatter('%(asctime)s - %(name)s - [%(levelname)s] - %(message)s'))
                 self._logger.addHandler(handler_)
                 self._logger.setLevel(logging.INFO)
            self._logger.warning("未能访问 self.ap.logger，已启用标准 logging。")

    def _get_plugin_dir(self) -> str:
        return os.path.dirname(os.path.abspath(__file__))

    def _load_plugin_config(self) -> Dict[str, Any]:
        """加载插件配置文件 config.json"""
        config_path = os.path.join(self._get_plugin_dir(), "config.json")
        # 定义默认配置
        default_config = {
            "devices": [],
            "timeouts": {
                "selection": 60, "command": 120, "connect": 10,
                "ping": 2, "auth_test": 10, "exec_command": 60
            },
            "output_max_length": 2000,
            "enable_ping_check": True,
            "enable_auth_test": True,
            "debug": False
        }
        try:
            if os.path.exists(config_path):
                with open(config_path, 'r', encoding='utf-8') as f:
                    user_config = json.load(f)
                    # 简单合并，用户配置可覆盖默认值，但不会删除默认键
                    for key in default_config:
                        if key in user_config:
                             # 特殊处理 timeouts 字典的合并
                             if key == "timeouts" and isinstance(user_config[key], dict):
                                  default_config[key].update(user_config[key])
                             else:
                                  default_config[key] = user_config[key]
                    self._logger.info(f"已从 config.json 加载配置。")
            else:
                self._logger.warning(f"插件配置文件 config.json 不存在，将使用默认配置。")
                # 创建默认配置文件
                with open(config_path, 'w', encoding='utf-8') as f:
                     json.dump(default_config, f, indent=4, ensure_ascii=False)
                self._logger.info(f"已创建默认配置文件 config.json，请修改后使用。")
        except Exception as e:
            self._logger.error(f"加载插件配置 config.json 失败: {e}", exc_info=True)
        return default_config

    def _get_session_key(self, ctx: EventContext) -> Tuple[str, str]:
        """生成唯一的会话标识符 (用户ID, 聊天ID)"""
        user_id = ctx.event.sender_id
        chat_id = ctx.event.group_id if hasattr(ctx.event, 'group_id') else 'person'
        return (str(user_id), str(chat_id))

    def _get_user_state(self, session_key: Tuple[str, str]) -> Optional[Dict[str, Any]]:
        """获取用户当前会话状态"""
        return self.user_sessions.get(session_key)

    def _set_user_state(self, session_key: Tuple[str, str], status: str, data: Optional[Dict[str, Any]] = None):
        """设置用户会话状态"""
        if status == "idle": # 清理状态
            self._clear_user_state(session_key)
            return

        state_data = self.user_sessions.get(session_key, {})
        state_data['status'] = status
        state_data['last_activity'] = time.time()
        if data:
            state_data.update(data)
        self.user_sessions[session_key] = state_data
        self._logger.debug(f"用户 {session_key} 状态更新为: {status}, 数据: {data}")

    def _clear_user_state(self, session_key: Tuple[str, str]):
        """清理用户会话状态并关闭连接"""
        state = self.user_sessions.pop(session_key, None)
        if state and state.get('ssh_client'):
            try:
                state['ssh_client'].close()
                self._logger.info(f"用户 {session_key} 的 SSH 连接已关闭。")
            except Exception as e:
                self._logger.error(f"关闭用户 {session_key} 的 SSH 连接时出错: {e}")
        self._logger.info(f"用户 {session_key} 的会话状态已清除。")

    # --- 事件处理器 ---
    @handler(GroupNormalMessageReceived)
    @handler(PersonNormalMessageReceived) # 同时处理群聊和私聊
    async def handle_message(self, ctx: EventContext):
        """处理收到的普通消息，根据用户状态进行路由"""
        if not paramiko: # 检查依赖库是否可用
             # 可以选择回复用户或仅记录日志
             # ctx.add_return("reply", [Plain("抱歉，SSH 功能所需的核心库未能加载，请联系管理员。")])
             self._logger.error("Paramiko 库不可用，无法处理 SSH 命令。")
             return

        msg_text = ctx.event.text_message.strip()
        session_key = self._get_session_key(ctx)
        user_state = self._get_user_state(session_key)
        current_status = user_state.get('status', 'idle') if user_state else 'idle'

        self._logger.debug(f"收到消息: '{msg_text}', 用户: {session_key}, 当前状态: {current_status}")

        # --- 状态路由 ---
        try:
            # 状态: 空闲 (idle)
            if current_status == 'idle':
                if msg_text.lower() == '/ssh': # 主命令触发
                    await self._show_device_menu(ctx)
                    self._set_user_state(session_key, 'selecting_device')
                    ctx.prevent_default()
                # 其他消息忽略

            # 状态: 等待设备选择 (selecting_device)
            elif current_status == 'selecting_device':
                if msg_text.lower() == 'q': # 退出选择
                    await ctx.add_return("reply", [Plain("已取消设备选择。")])
                    self._clear_user_state(session_key) # 清理状态
                else:
                    await self._handle_device_selection(ctx, msg_text)
                ctx.prevent_default()

            # 状态: 已连接 (connected)
            elif current_status == 'connected':
                if msg_text.lower() == 'exit': # 退出连接
                    await self._handle_disconnect(ctx)
                elif msg_text.lower() == 'menu': # （可选）返回菜单，等同于 exit
                     await self._handle_disconnect(ctx) # 简化处理，直接断开
                else: # 执行命令
                    await self._handle_command_execution(ctx, msg_text)
                ctx.prevent_default()

            # 其他状态 (例如 error) - 可以选择重置或提示
            else:
                 if msg_text.lower() == '/ssh': # 允许在任何状态下重新开始
                      self._clear_user_state(session_key) # 清理旧状态
                      await self._show_device_menu(ctx)
                      self._set_user_state(session_key, 'selecting_device')
                      ctx.prevent_default()
                 # else: # 可以选择提示用户当前状态异常
                 #    await ctx.add_return("reply", [Plain(f"当前状态异常: {current_status}，请稍后再试或使用 /ssh exit 重置。")])

        except Exception as e:
             self._logger.error(f"处理用户 {session_key} 消息时发生未捕获异常:", exc_info=True)
             await ctx.add_return("reply", [Plain(f"处理您的请求时发生内部错误: {e}")])
             self._clear_user_state(session_key) # 发生严重错误时清理状态

    # --- 核心功能实现 ---
    async def _show_device_menu(self, ctx: EventContext):
        """显示设备选择菜单"""
        devices = self.plugin_config.get("devices", [])
        if not devices:
            await ctx.add_return("reply", [Plain("错误：插件配置文件中未找到任何设备信息。请先配置 config.json。")])
            return

        menu_items = [
            "🔧 SSH终端管家 - 设备列表",
            "────────────────",
        ]
        for i, d in enumerate(devices):
            menu_items.append(f"{i+1}. {d.get('icon','')} {d.get('name', '未知设备')}\n   ▸ {d.get('host', 'N/A')}:{d.get('port', 22)}")
        menu_items.extend([
            "────────────────",
            "请输入序号选择设备 (回复 'q' 退出)"
        ])
        await ctx.add_return("reply", [Plain("\n".join(menu_items))])

    async def _handle_device_selection(self, ctx: EventContext, choice: str):
        """处理用户的设备选择，进行验证和连接"""
        session_key = self._get_session_key(ctx)
        devices = self.plugin_config.get("devices", [])
        timeouts = self.plugin_config.get("timeouts", DEFAULT_CONFIG["timeouts"])

        try:
            index = int(choice) - 1
            if not (0 <= index < len(devices)):
                raise ValueError("序号超出范围")
            selected_device = devices[index]
        except ValueError:
            await ctx.add_return("reply", [Plain("无效的序号，请重新输入数字序号或 'q' 退出。")])
            return # 保持在 selecting_device 状态

        await ctx.add_return("reply", [Plain(f"🔍 正在连接并验证 {selected_device.get('icon','')} {selected_device.get('name', '未知设备')}...")])

        # --- 连接验证 ---
        verification_report = []
        connection_ok = False
        error_message = None

        # 1. Ping 检查 (可选)
        ping_ok = True
        if self.plugin_config.get("enable_ping_check", True):
            ping_ok = await self._ping_host(selected_device.get('host'), timeouts.get('ping', 2))
            verification_report.append(f"主机可达性 (Ping): {'✅' if ping_ok else '❌'}")
            if not ping_ok:
                 error_message = "主机 Ping 不可达。"

        # 2. 认证测试 (可选，且 Ping 成功时)
        auth_ok = True
        if ping_ok and self.plugin_config.get("enable_auth_test", True):
             auth_ok = await self._test_credentials(selected_device, timeouts.get('auth_test', 10))
             verification_report.append(f"SSH 认证测试: {'✅' if auth_ok else '❌'}")
             if not auth_ok:
                  error_message = "SSH 用户名或密码错误。"

        # 3. 实际连接 (如果验证通过或未进行验证)
        if ping_ok and auth_ok:
             try:
                 connect_timeout = timeouts.get('connect', 10)
                 ssh_client = await asyncio.wait_for(
                     self._connect_ssh(selected_device),
                     timeout=connect_timeout
                 )
                 connection_ok = True
                 verification_report.append(f"SSH 连接状态: ✅")

                 # 连接成功，更新状态
                 session_data = {
                     'device_config': selected_device,
                     'ssh_client': ssh_client,
                     'start_time': time.time(),
                     'command_count': 0
                 }
                 self._set_user_state(session_key, 'connected', session_data)

                 # 发送连接成功消息
                 connect_msg = [
                     "🔐 安全连接已建立", "────────────────",
                     f"设备: {selected_device.get('icon','')} {selected_device.get('name')}",
                     f"地址: {selected_device.get('host')}:{selected_device.get('port')}",
                     # 可以尝试获取更多连接信息，但 paramiko 不像 ssh2 那样直接提供协议/算法
                     "────────────────", "请输入 Linux 命令 (输入 'exit' 结束):"
                 ]
                 await ctx.add_return("reply", [Plain("\n".join(connect_msg))])

             except asyncio.TimeoutError:
                  error_message = f"SSH 连接超时 ({connect_timeout}秒)。"
                  verification_report.append(f"SSH 连接状态: ❌ (超时)")
             except paramiko.AuthenticationException:
                  error_message = "SSH 认证失败 (用户名/密码错误)。"
                  verification_report.append(f"SSH 连接状态: ❌ (认证失败)")
             except Exception as e:
                  error_message = f"SSH 连接失败: {e}"
                  verification_report.append(f"SSH 连接状态: ❌ ({e})")
                  self._logger.error(f"SSH 连接到 {selected_device.get('host')} 时出错:", exc_info=True)

        # --- 处理连接结果 ---
        if not connection_ok:
             # 如果连接失败，发送包含验证报告的错误消息
             final_report = ["⚠️ 连接失败", "────────────────"] + verification_report
             final_report.append(f"原因: {error_message or '未知错误'}")
             await ctx.add_return("reply", [Plain("\n".join(final_report))])
             self._clear_user_state(session_key) # 连接失败，清理状态

    async def _handle_command_execution(self, ctx: EventContext, command: str):
        """处理用户输入的命令并在远程主机上执行"""
        session_key = self._get_session_key(ctx)
        user_state = self._get_user_state(session_key)
        timeouts = self.plugin_config.get("timeouts", DEFAULT_CONFIG["timeouts"])
        max_len = self.plugin_config.get("output_max_length", 2000)

        if not user_state or 'ssh_client' not in user_state:
            await ctx.add_return("reply", [Plain("错误：SSH 连接丢失或状态异常，请重新使用 /ssh 连接。")])
            self._clear_user_state(session_key)
            return

        ssh_client: paramiko.SSHClient = user_state['ssh_client']
        device_name = user_state['device_config'].get('name', '当前设备')

        await ctx.add_return("reply", [Plain(f"在 {device_name} 上执行: `{command}` ...")]) # 提示正在执行

        try:
            exec_timeout = timeouts.get('exec_command', 60)
            # 在异步环境中执行同步的 paramiko 操作，需要使用 run_in_executor
            loop = asyncio.get_running_loop()
            stdout, stderr = await loop.run_in_executor(
                None, # 使用默认线程池执行器
                lambda: self._execute_ssh_command_sync(ssh_client, command, exec_timeout)
            )

            # 更新状态
            user_state['command_count'] += 1
            user_state['last_activity'] = time.time()
            self._set_user_state(session_key, 'connected', user_state) # 保存更新后的状态

            # 格式化输出
            output = ""
            if stdout:
                 output += stdout
            if stderr:
                 # 将 stderr 标记出来
                 output += ("\n--- STDERR ---\n" + stderr)

            output = output.strip()
            truncated_output = output
            if len(output) > max_len:
                 truncated_output = output[:max_len] + f"\n\n... (输出超过 {max_len} 字符，已截断)"

            result_msg = [
                f"📊 来自 {device_name} 的执行结果 (`{command}`):",
                "────────────────",
                truncated_output if truncated_output else "(无输出)",
                "────────────────",
                f"状态: {'❌' if stderr else '✅'} | 字符数: {len(output)}"
            ]
            await ctx.add_return("reply", [Plain("\n".join(result_msg))])

        except asyncio.TimeoutError: # run_in_executor 不直接抛 TimeoutError，需要内部处理
             await ctx.add_return("reply", [Plain(f"错误：在 {device_name} 上执行命令 '{command}' 超时 ({exec_timeout} 秒)。")])
        except Exception as e:
             self._logger.error(f"执行 SSH 命令时出错:", exc_info=True)
             await ctx.add_return("reply", [Plain(f"错误：在 {device_name} 上执行命令时发生异常: {e}")])
             # 发生命令执行错误时，不一定需要断开连接，看情况
             # self._clear_user_state(session_key)

    def _execute_ssh_command_sync(self, client: paramiko.SSHClient, command: str, timeout: int) -> Tuple[str, str]:
        """同步执行 SSH 命令（用于 run_in_executor）"""
        try:
            stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
            stdout_data = stdout.read().decode('utf-8', errors='replace')
            stderr_data = stderr.read().decode('utf-8', errors='replace')
            # 检查退出状态码 (可选，但推荐)
            # exit_status = stdout.channel.recv_exit_status()
            # if exit_status != 0:
            #    stderr_data += f"\n[Command exited with status {exit_status}]"
            return stdout_data, stderr_data
        except Exception as e:
            # 将异常信息通过 stderr 返回
             return "", f"[执行命令时内部错误: {e}]"


    async def _handle_disconnect(self, ctx: EventContext):
        """处理断开连接请求"""
        session_key = self._get_session_key(ctx)
        user_state = self._get_user_state(session_key)

        if not user_state:
            await ctx.add_return("reply", [Plain("您当前没有活动的 SSH 连接。")])
            return

        device_name = user_state['device_config'].get('name', '当前设备')
        start_time = user_state.get('start_time')
        command_count = user_state.get('command_count', 0)

        # 准备退出摘要信息
        summary = ["🛑 SSH 会话已终止", "────────────────"]
        summary.append(f"设备: {user_state['device_config'].get('icon','')} {device_name}")
        if start_time:
             duration = datetime.timedelta(seconds=int(time.time() - start_time))
             summary.append(f"时长: {str(duration)}")
        summary.append(f"执行命令: {command_count} 次")
        summary.append("────────────────")
        summary.append("连接已安全断开。")

        await ctx.add_return("reply", [Plain("\n".join(summary))])
        self._clear_user_state(session_key) # 清理状态并关闭连接

    # --- 异步辅助函数 ---
    async def _ping_host(self, host: str, timeout: int) -> bool:
        """使用系统 ping 命令异步检查主机可达性"""
        if not host: return False
        # 构建 ping 命令 (兼容 Linux 和 Windows 的简单形式)
        command = ['ping', '-c', '1', '-W', str(timeout), host] if platform.system() != "Windows" else ['ping', '-n', '1', '-w', str(timeout * 1000), host]
        try:
            self._logger.debug(f"Pinging host: {' '.join(command)}")
            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout + 1)
            return process.returncode == 0
        except asyncio.TimeoutError:
            self._logger.warning(f"Ping 超时: {host}")
            return False
        except Exception as e:
            self._logger.error(f"Ping 执行失败: {host}, Error: {e}")
            return False

    async def _test_credentials(self, device_config: Dict[str, Any], timeout: int) -> bool:
        """异步测试 SSH 凭据有效性"""
        if not device_config or not paramiko: return False
        host = device_config.get('host')
        port = device_config.get('port', 22)
        username = device_config.get('username')
        password = device_config.get('password') # 也可以支持密钥

        if not all([host, port, username, password]):
             self._logger.warning("认证测试缺少必要的设备信息 (host, port, username, password)")
             return False

        loop = asyncio.get_running_loop()
        try:
            # 在 executor 中运行同步的 paramiko 连接测试
            auth_result = await asyncio.wait_for(
                loop.run_in_executor(None, self._test_credentials_sync, device_config),
                timeout=timeout
            )
            return auth_result
        except asyncio.TimeoutError:
            self._logger.warning(f"认证测试超时: {host}:{port}")
            return False
        except Exception as e:
             self._logger.error(f"认证测试时发生错误: {host}:{port}, {e}")
             return False

    def _test_credentials_sync(self, device_config: Dict[str, Any]) -> bool:
        """同步测试凭据（用于 run_in_executor）"""
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy()) # 或 WarningPolicy
        try:
            client.connect(
                hostname=device_config['host'],
                port=device_config['port'],
                username=device_config['username'],
                password=device_config['password'],
                timeout=self.plugin_config['timeouts'].get('auth_test', 10) -1 # 内部超时略小于外部超时
            )
            return True
        except paramiko.AuthenticationException:
            self._logger.warning(f"认证测试失败 (凭据错误): {device_config.get('host')}")
            return False
        except Exception as e:
             # 记录其他连接错误，但也视为认证测试失败
             self._logger.warning(f"认证测试连接时出错: {device_config.get('host')}, {e}")
             return False
        finally:
            client.close()

    async def _connect_ssh(self, device_config: Dict[str, Any]) -> paramiko.SSHClient:
        """异步建立 SSH 连接（实际连接在 executor 中完成）"""
        if not device_config or not paramiko:
             raise ConnectionError("设备配置或 Paramiko 库无效")

        loop = asyncio.get_running_loop()
        try:
            # 在 executor 中运行同步的 paramiko 连接
            client = await loop.run_in_executor(
                None,
                self._connect_ssh_sync,
                device_config
            )
            self._logger.info(f"成功连接到 SSH: {device_config.get('host')}")
            return client
        except Exception as e:
            # 重新抛出异常，以便上层处理具体的错误类型
            self._logger.error(f"连接 SSH 时出错: {device_config.get('host')}", exc_info=False) # 只记录错误摘要
            raise ConnectionError(f"SSH 连接失败: {e}") from e

    def _connect_ssh_sync(self, device_config: Dict[str, Any]) -> paramiko.SSHClient:
        """同步建立 SSH 连接（用于 run_in_executor）"""
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            client.connect(
                hostname=device_config['host'],
                port=device_config['port'],
                username=device_config['username'],
                password=device_config['password'],
                timeout=self.plugin_config['timeouts'].get('connect', 10)
            )
            return client
        except Exception as e:
            client.close() # 确保失败时关闭
            # 将 paramiko 的具体异常或其他异常包装后重新抛出
            raise e

    # --- 清理函数 (可选) ---
    def destroy(self):
        """插件卸载或程序退出时执行清理"""
        self._logger.info("SSH 插件正在执行清理 (destroy)...")
        # 关闭所有活动的 SSH 连接
        active_sessions = list(self.user_sessions.keys()) # 复制 keys 以防迭代时修改
        for session_key in active_sessions:
            self._clear_user_state(session_key)
        self._logger.info("所有活动 SSH 会话已清理。")

# --- 插件模板结束 ---#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
LangBot SSH 插件模板
版本: 1.0.0
描述: 通过 LangBot 连接和管理 SSH 设备。
作者: Your Name (基于 JS 示例改编)
"""

# --- 标准库导入 ---
import os
import json
import logging
import traceback
import time
import datetime
import asyncio
import subprocess
import shlex
from typing import Dict, Any, Optional, List, Tuple

# --- 第三方库导入 ---
try:
    import paramiko
except ImportError:
    print("错误：缺少 'paramiko' 库。请在 LangBot 环境中执行 'pip install paramiko'")
    paramiko = None # 设置为 None 以便后续检查

# --- LangBot 核心模块导入 ---
try:
    from pkg.plugin.context import register, handler, BasePlugin, APIHost, EventContext
    from pkg.plugin.events import GroupNormalMessageReceived, PersonNormalMessageReceived
    from pkg.platform.types.message import Plain
    # from pkg.core.app import Application # 如果需要 Application 类型提示
except ImportError as e:
    print(f"错误：无法导入 LangBot 框架核心模块: {e}")
    raise

# --- 插件注册信息 ---
@register(
    name="MySshPlugin",
    description="通过聊天界面管理 SSH 连接和执行命令",
    version="1.0.0",
    author="Your Name"
)
class SshPlugin(BasePlugin):
    """SSH 管理插件主类"""

    # --- 初始化与状态管理 ---
    def __init__(self, host: APIHost):
        super().__init__(host)
        self._logger = None
        self.plugin_config: Dict[str, Any] = {}
        # 用户会话状态存储: key 为 (user_id, chat_id) 元组, value 为会话信息字典
        # chat_id 对于私聊是 'person'，对于群聊是 group_id
        self.user_sessions: Dict[Tuple[str, str], Dict[str, Any]] = {}

        self._setup_logger()
        self.plugin_config = self._load_plugin_config()
        if self.plugin_config.get("debug", False):
             self._logger.setLevel(logging.DEBUG)
        self._logger.info("SSH 插件同步初始化完成。")
        if not paramiko:
             self._logger.error("Paramiko 库未安装或导入失败，SSH 功能将不可用！")

    def _setup_logger(self):
        """设置 Logger"""
        try:
            self._logger = self.ap.logger.getChild(self.plugin_name())
        except AttributeError:
            self._logger = logging.getLogger(self.plugin_name())
            if not self._logger.hasHandlers():
                 handler_ = logging.StreamHandler(sys.stdout)
                 handler_.setFormatter(logging.Formatter('%(asctime)s - %(name)s - [%(levelname)s] - %(message)s'))
                 self._logger.addHandler(handler_)
                 self._logger.setLevel(logging.INFO)
            self._logger.warning("未能访问 self.ap.logger，已启用标准 logging。")

    def _get_plugin_dir(self) -> str:
        return os.path.dirname(os.path.abspath(__file__))

    def _load_plugin_config(self) -> Dict[str, Any]:
        """加载插件配置文件 config.json"""
        config_path = os.path.join(self._get_plugin_dir(), "config.json")
        # 定义默认配置
        default_config = {
            "devices": [],
            "timeouts": {
                "selection": 60, "command": 120, "connect": 10,
                "ping": 2, "auth_test": 10, "exec_command": 60
            },
            "output_max_length": 2000,
            "enable_ping_check": True,
            "enable_auth_test": True,
            "debug": False
        }
        try:
            if os.path.exists(config_path):
                with open(config_path, 'r', encoding='utf-8') as f:
                    user_config = json.load(f)
                    # 简单合并，用户配置可覆盖默认值，但不会删除默认键
                    for key in default_config:
                        if key in user_config:
                             # 特殊处理 timeouts 字典的合并
                             if key == "timeouts" and isinstance(user_config[key], dict):
                                  default_config[key].update(user_config[key])
                             else:
                                  default_config[key] = user_config[key]
                    self._logger.info(f"已从 config.json 加载配置。")
            else:
                self._logger.warning(f"插件配置文件 config.json 不存在，将使用默认配置。")
                # 创建默认配置文件
                with open(config_path, 'w', encoding='utf-8') as f:
                     json.dump(default_config, f, indent=4, ensure_ascii=False)
                self._logger.info(f"已创建默认配置文件 config.json，请修改后使用。")
        except Exception as e:
            self._logger.error(f"加载插件配置 config.json 失败: {e}", exc_info=True)
        return default_config

    def _get_session_key(self, ctx: EventContext) -> Tuple[str, str]:
        """生成唯一的会话标识符 (用户ID, 聊天ID)"""
        user_id = ctx.event.sender_id
        chat_id = ctx.event.group_id if hasattr(ctx.event, 'group_id') else 'person'
        return (str(user_id), str(chat_id))

    def _get_user_state(self, session_key: Tuple[str, str]) -> Optional[Dict[str, Any]]:
        """获取用户当前会话状态"""
        return self.user_sessions.get(session_key)

    def _set_user_state(self, session_key: Tuple[str, str], status: str, data: Optional[Dict[str, Any]] = None):
        """设置用户会话状态"""
        if status == "idle": # 清理状态
            self._clear_user_state(session_key)
            return

        state_data = self.user_sessions.get(session_key, {})
        state_data['status'] = status
        state_data['last_activity'] = time.time()
        if data:
            state_data.update(data)
        self.user_sessions[session_key] = state_data
        self._logger.debug(f"用户 {session_key} 状态更新为: {status}, 数据: {data}")

    def _clear_user_state(self, session_key: Tuple[str, str]):
        """清理用户会话状态并关闭连接"""
        state = self.user_sessions.pop(session_key, None)
        if state and state.get('ssh_client'):
            try:
                state['ssh_client'].close()
                self._logger.info(f"用户 {session_key} 的 SSH 连接已关闭。")
            except Exception as e:
                self._logger.error(f"关闭用户 {session_key} 的 SSH 连接时出错: {e}")
        self._logger.info(f"用户 {session_key} 的会话状态已清除。")

    # --- 事件处理器 ---
    @handler(GroupNormalMessageReceived)
    @handler(PersonNormalMessageReceived) # 同时处理群聊和私聊
    async def handle_message(self, ctx: EventContext):
        """处理收到的普通消息，根据用户状态进行路由"""
        if not paramiko: # 检查依赖库是否可用
             # 可以选择回复用户或仅记录日志
             # ctx.add_return("reply", [Plain("抱歉，SSH 功能所需的核心库未能加载，请联系管理员。")])
             self._logger.error("Paramiko 库不可用，无法处理 SSH 命令。")
             return

        msg_text = ctx.event.text_message.strip()
        session_key = self._get_session_key(ctx)
        user_state = self._get_user_state(session_key)
        current_status = user_state.get('status', 'idle') if user_state else 'idle'

        self._logger.debug(f"收到消息: '{msg_text}', 用户: {session_key}, 当前状态: {current_status}")

        # --- 状态路由 ---
        try:
            # 状态: 空闲 (idle)
            if current_status == 'idle':
                if msg_text.lower() == '/ssh': # 主命令触发
                    await self._show_device_menu(ctx)
                    self._set_user_state(session_key, 'selecting_device')
                    ctx.prevent_default()
                # 其他消息忽略

            # 状态: 等待设备选择 (selecting_device)
            elif current_status == 'selecting_device':
                if msg_text.lower() == 'q': # 退出选择
                    await ctx.add_return("reply", [Plain("已取消设备选择。")])
                    self._clear_user_state(session_key) # 清理状态
                else:
                    await self._handle_device_selection(ctx, msg_text)
                ctx.prevent_default()

            # 状态: 已连接 (connected)
            elif current_status == 'connected':
                if msg_text.lower() == 'exit': # 退出连接
                    await self._handle_disconnect(ctx)
                elif msg_text.lower() == 'menu': # （可选）返回菜单，等同于 exit
                     await self._handle_disconnect(ctx) # 简化处理，直接断开
                else: # 执行命令
                    await self._handle_command_execution(ctx, msg_text)
                ctx.prevent_default()

            # 其他状态 (例如 error) - 可以选择重置或提示
            else:
                 if msg_text.lower() == '/ssh': # 允许在任何状态下重新开始
                      self._clear_user_state(session_key) # 清理旧状态
                      await self._show_device_menu(ctx)
                      self._set_user_state(session_key, 'selecting_device')
                      ctx.prevent_default()
                 # else: # 可以选择提示用户当前状态异常
                 #    await ctx.add_return("reply", [Plain(f"当前状态异常: {current_status}，请稍后再试或使用 /ssh exit 重置。")])

        except Exception as e:
             self._logger.error(f"处理用户 {session_key} 消息时发生未捕获异常:", exc_info=True)
             await ctx.add_return("reply", [Plain(f"处理您的请求时发生内部错误: {e}")])
             self._clear_user_state(session_key) # 发生严重错误时清理状态

    # --- 核心功能实现 ---
    async def _show_device_menu(self, ctx: EventContext):
        """显示设备选择菜单"""
        devices = self.plugin_config.get("devices", [])
        if not devices:
            await ctx.add_return("reply", [Plain("错误：插件配置文件中未找到任何设备信息。请先配置 config.json。")])
            return

        menu_items = [
            "🔧 SSH终端管家 - 设备列表",
            "────────────────",
        ]
        for i, d in enumerate(devices):
            menu_items.append(f"{i+1}. {d.get('icon','')} {d.get('name', '未知设备')}\n   ▸ {d.get('host', 'N/A')}:{d.get('port', 22)}")
        menu_items.extend([
            "────────────────",
            "请输入序号选择设备 (回复 'q' 退出)"
        ])
        await ctx.add_return("reply", [Plain("\n".join(menu_items))])

    async def _handle_device_selection(self, ctx: EventContext, choice: str):
        """处理用户的设备选择，进行验证和连接"""
        session_key = self._get_session_key(ctx)
        devices = self.plugin_config.get("devices", [])
        timeouts = self.plugin_config.get("timeouts", DEFAULT_CONFIG["timeouts"])

        try:
            index = int(choice) - 1
            if not (0 <= index < len(devices)):
                raise ValueError("序号超出范围")
            selected_device = devices[index]
        except ValueError:
            await ctx.add_return("reply", [Plain("无效的序号，请重新输入数字序号或 'q' 退出。")])
            return # 保持在 selecting_device 状态

        await ctx.add_return("reply", [Plain(f"🔍 正在连接并验证 {selected_device.get('icon','')} {selected_device.get('name', '未知设备')}...")])

        # --- 连接验证 ---
        verification_report = []
        connection_ok = False
        error_message = None

        # 1. Ping 检查 (可选)
        ping_ok = True
        if self.plugin_config.get("enable_ping_check", True):
            ping_ok = await self._ping_host(selected_device.get('host'), timeouts.get('ping', 2))
            verification_report.append(f"主机可达性 (Ping): {'✅' if ping_ok else '❌'}")
            if not ping_ok:
                 error_message = "主机 Ping 不可达。"

        # 2. 认证测试 (可选，且 Ping 成功时)
        auth_ok = True
        if ping_ok and self.plugin_config.get("enable_auth_test", True):
             auth_ok = await self._test_credentials(selected_device, timeouts.get('auth_test', 10))
             verification_report.append(f"SSH 认证测试: {'✅' if auth_ok else '❌'}")
             if not auth_ok:
                  error_message = "SSH 用户名或密码错误。"

        # 3. 实际连接 (如果验证通过或未进行验证)
        if ping_ok and auth_ok:
             try:
                 connect_timeout = timeouts.get('connect', 10)
                 ssh_client = await asyncio.wait_for(
                     self._connect_ssh(selected_device),
                     timeout=connect_timeout
                 )
                 connection_ok = True
                 verification_report.append(f"SSH 连接状态: ✅")

                 # 连接成功，更新状态
                 session_data = {
                     'device_config': selected_device,
                     'ssh_client': ssh_client,
                     'start_time': time.time(),
                     'command_count': 0
                 }
                 self._set_user_state(session_key, 'connected', session_data)

                 # 发送连接成功消息
                 connect_msg = [
                     "🔐 安全连接已建立", "────────────────",
                     f"设备: {selected_device.get('icon','')} {selected_device.get('name')}",
                     f"地址: {selected_device.get('host')}:{selected_device.get('port')}",
                     # 可以尝试获取更多连接信息，但 paramiko 不像 ssh2 那样直接提供协议/算法
                     "────────────────", "请输入 Linux 命令 (输入 'exit' 结束):"
                 ]
                 await ctx.add_return("reply", [Plain("\n".join(connect_msg))])

             except asyncio.TimeoutError:
                  error_message = f"SSH 连接超时 ({connect_timeout}秒)。"
                  verification_report.append(f"SSH 连接状态: ❌ (超时)")
             except paramiko.AuthenticationException:
                  error_message = "SSH 认证失败 (用户名/密码错误)。"
                  verification_report.append(f"SSH 连接状态: ❌ (认证失败)")
             except Exception as e:
                  error_message = f"SSH 连接失败: {e}"
                  verification_report.append(f"SSH 连接状态: ❌ ({e})")
                  self._logger.error(f"SSH 连接到 {selected_device.get('host')} 时出错:", exc_info=True)

        # --- 处理连接结果 ---
        if not connection_ok:
             # 如果连接失败，发送包含验证报告的错误消息
             final_report = ["⚠️ 连接失败", "────────────────"] + verification_report
             final_report.append(f"原因: {error_message or '未知错误'}")
             await ctx.add_return("reply", [Plain("\n".join(final_report))])
             self._clear_user_state(session_key) # 连接失败，清理状态

    async def _handle_command_execution(self, ctx: EventContext, command: str):
        """处理用户输入的命令并在远程主机上执行"""
        session_key = self._get_session_key(ctx)
        user_state = self._get_user_state(session_key)
        timeouts = self.plugin_config.get("timeouts", DEFAULT_CONFIG["timeouts"])
        max_len = self.plugin_config.get("output_max_length", 2000)

        if not user_state or 'ssh_client' not in user_state:
            await ctx.add_return("reply", [Plain("错误：SSH 连接丢失或状态异常，请重新使用 /ssh 连接。")])
            self._clear_user_state(session_key)
            return

        ssh_client: paramiko.SSHClient = user_state['ssh_client']
        device_name = user_state['device_config'].get('name', '当前设备')

        await ctx.add_return("reply", [Plain(f"在 {device_name} 上执行: `{command}` ...")]) # 提示正在执行

        try:
            exec_timeout = timeouts.get('exec_command', 60)
            # 在异步环境中执行同步的 paramiko 操作，需要使用 run_in_executor
            loop = asyncio.get_running_loop()
            stdout, stderr = await loop.run_in_executor(
                None, # 使用默认线程池执行器
                lambda: self._execute_ssh_command_sync(ssh_client, command, exec_timeout)
            )

            # 更新状态
            user_state['command_count'] += 1
            user_state['last_activity'] = time.time()
            self._set_user_state(session_key, 'connected', user_state) # 保存更新后的状态

            # 格式化输出
            output = ""
            if stdout:
                 output += stdout
            if stderr:
                 # 将 stderr 标记出来
                 output += ("\n--- STDERR ---\n" + stderr)

            output = output.strip()
            truncated_output = output
            if len(output) > max_len:
                 truncated_output = output[:max_len] + f"\n\n... (输出超过 {max_len} 字符，已截断)"

            result_msg = [
                f"📊 来自 {device_name} 的执行结果 (`{command}`):",
                "────────────────",
                truncated_output if truncated_output else "(无输出)",
                "────────────────",
                f"状态: {'❌' if stderr else '✅'} | 字符数: {len(output)}"
            ]
            await ctx.add_return("reply", [Plain("\n".join(result_msg))])

        except asyncio.TimeoutError: # run_in_executor 不直接抛 TimeoutError，需要内部处理
             await ctx.add_return("reply", [Plain(f"错误：在 {device_name} 上执行命令 '{command}' 超时 ({exec_timeout} 秒)。")])
        except Exception as e:
             self._logger.error(f"执行 SSH 命令时出错:", exc_info=True)
             await ctx.add_return("reply", [Plain(f"错误：在 {device_name} 上执行命令时发生异常: {e}")])
             # 发生命令执行错误时，不一定需要断开连接，看情况
             # self._clear_user_state(session_key)

    def _execute_ssh_command_sync(self, client: paramiko.SSHClient, command: str, timeout: int) -> Tuple[str, str]:
        """同步执行 SSH 命令（用于 run_in_executor）"""
        try:
            stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
            stdout_data = stdout.read().decode('utf-8', errors='replace')
            stderr_data = stderr.read().decode('utf-8', errors='replace')
            # 检查退出状态码 (可选，但推荐)
            # exit_status = stdout.channel.recv_exit_status()
            # if exit_status != 0:
            #    stderr_data += f"\n[Command exited with status {exit_status}]"
            return stdout_data, stderr_data
        except Exception as e:
            # 将异常信息通过 stderr 返回
             return "", f"[执行命令时内部错误: {e}]"


    async def _handle_disconnect(self, ctx: EventContext):
        """处理断开连接请求"""
        session_key = self._get_session_key(ctx)
        user_state = self._get_user_state(session_key)

        if not user_state:
            await ctx.add_return("reply", [Plain("您当前没有活动的 SSH 连接。")])
            return

        device_name = user_state['device_config'].get('name', '当前设备')
        start_time = user_state.get('start_time')
        command_count = user_state.get('command_count', 0)

        # 准备退出摘要信息
        summary = ["🛑 SSH 会话已终止", "────────────────"]
        summary.append(f"设备: {user_state['device_config'].get('icon','')} {device_name}")
        if start_time:
             duration = datetime.timedelta(seconds=int(time.time() - start_time))
             summary.append(f"时长: {str(duration)}")
        summary.append(f"执行命令: {command_count} 次")
        summary.append("────────────────")
        summary.append("连接已安全断开。")

        await ctx.add_return("reply", [Plain("\n".join(summary))])
        self._clear_user_state(session_key) # 清理状态并关闭连接

    # --- 异步辅助函数 ---
    async def _ping_host(self, host: str, timeout: int) -> bool:
        """使用系统 ping 命令异步检查主机可达性"""
        if not host: return False
        # 构建 ping 命令 (兼容 Linux 和 Windows 的简单形式)
        command = ['ping', '-c', '1', '-W', str(timeout), host] if platform.system() != "Windows" else ['ping', '-n', '1', '-w', str(timeout * 1000), host]
        try:
            self._logger.debug(f"Pinging host: {' '.join(command)}")
            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout + 1)
            return process.returncode == 0
        except asyncio.TimeoutError:
            self._logger.warning(f"Ping 超时: {host}")
            return False
        except Exception as e:
            self._logger.error(f"Ping 执行失败: {host}, Error: {e}")
            return False

    async def _test_credentials(self, device_config: Dict[str, Any], timeout: int) -> bool:
        """异步测试 SSH 凭据有效性"""
        if not device_config or not paramiko: return False
        host = device_config.get('host')
        port = device_config.get('port', 22)
        username = device_config.get('username')
        password = device_config.get('password') # 也可以支持密钥

        if not all([host, port, username, password]):
             self._logger.warning("认证测试缺少必要的设备信息 (host, port, username, password)")
             return False

        loop = asyncio.get_running_loop()
        try:
            # 在 executor 中运行同步的 paramiko 连接测试
            auth_result = await asyncio.wait_for(
                loop.run_in_executor(None, self._test_credentials_sync, device_config),
                timeout=timeout
            )
            return auth_result
        except asyncio.TimeoutError:
            self._logger.warning(f"认证测试超时: {host}:{port}")
            return False
        except Exception as e:
             self._logger.error(f"认证测试时发生错误: {host}:{port}, {e}")
             return False

    def _test_credentials_sync(self, device_config: Dict[str, Any]) -> bool:
        """同步测试凭据（用于 run_in_executor）"""
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy()) # 或 WarningPolicy
        try:
            client.connect(
                hostname=device_config['host'],
                port=device_config['port'],
                username=device_config['username'],
                password=device_config['password'],
                timeout=self.plugin_config['timeouts'].get('auth_test', 10) -1 # 内部超时略小于外部超时
            )
            return True
        except paramiko.AuthenticationException:
            self._logger.warning(f"认证测试失败 (凭据错误): {device_config.get('host')}")
            return False
        except Exception as e:
             # 记录其他连接错误，但也视为认证测试失败
             self._logger.warning(f"认证测试连接时出错: {device_config.get('host')}, {e}")
             return False
        finally:
            client.close()

    async def _connect_ssh(self, device_config: Dict[str, Any]) -> paramiko.SSHClient:
        """异步建立 SSH 连接（实际连接在 executor 中完成）"""
        if not device_config or not paramiko:
             raise ConnectionError("设备配置或 Paramiko 库无效")

        loop = asyncio.get_running_loop()
        try:
            # 在 executor 中运行同步的 paramiko 连接
            client = await loop.run_in_executor(
                None,
                self._connect_ssh_sync,
                device_config
            )
            self._logger.info(f"成功连接到 SSH: {device_config.get('host')}")
            return client
        except Exception as e:
            # 重新抛出异常，以便上层处理具体的错误类型
            self._logger.error(f"连接 SSH 时出错: {device_config.get('host')}", exc_info=False) # 只记录错误摘要
            raise ConnectionError(f"SSH 连接失败: {e}") from e

    def _connect_ssh_sync(self, device_config: Dict[str, Any]) -> paramiko.SSHClient:
        """同步建立 SSH 连接（用于 run_in_executor）"""
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            client.connect(
                hostname=device_config['host'],
                port=device_config['port'],
                username=device_config['username'],
                password=device_config['password'],
                timeout=self.plugin_config['timeouts'].get('connect', 10)
            )
            return client
        except Exception as e:
            client.close() # 确保失败时关闭
            # 将 paramiko 的具体异常或其他异常包装后重新抛出
            raise e

    # --- 清理函数 (可选) ---
    def destroy(self):
        """插件卸载或程序退出时执行清理"""
        self._logger.info("SSH 插件正在执行清理 (destroy)...")
        # 关闭所有活动的 SSH 连接
        active_sessions = list(self.user_sessions.keys()) # 复制 keys 以防迭代时修改
        for session_key in active_sessions:
            self._clear_user_state(session_key)
        self._logger.info("所有活动 SSH 会话已清理。")

# --- 插件模板结束 ---