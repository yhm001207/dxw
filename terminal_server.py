# -*- coding: utf-8 -*-
"""
WebSocket 终端服务器
提供真实的 cmd/bash PTY 终端，支持 xterm.js
带 token 认证，只有登录用户才能连接
"""
import asyncio
import json
import os
import signal
import sys
import threading
import time

import websockets
from winpty import PtyProcess


# ==================== Token 认证 ====================
# 共享 token 存储（Flask 和终端服务器在同一进程，共享内存）
_terminal_tokens = {}  # {token: expire_timestamp}
_tokens_lock = threading.Lock()


def generate_terminal_token(username, ttl=3600):
    """生成终端连接 token，有效期 ttl 秒（默认 1 小时）"""
    import uuid
    token = str(uuid.uuid4())
    with _tokens_lock:
        _terminal_tokens[token] = {
            'username': username,
            'expire': time.time() + ttl,
        }
    # 清理过期 token
    _cleanup_tokens()
    return token


def validate_terminal_token(token):
    """验证 token，返回 username 或 None"""
    if not token:
        return None
    with _tokens_lock:
        info = _terminal_tokens.get(token)
        if not info:
            return None
        if time.time() > info['expire']:
            del _terminal_tokens[token]
            return None
        return info['username']


def _cleanup_tokens():
    """清理过期 token"""
    now = time.time()
    with _tokens_lock:
        expired = [t for t, info in _terminal_tokens.items() if now > info['expire']]
        for t in expired:
            del _terminal_tokens[t]


# ==================== 终端会话 ====================

class TerminalSession:
    def __init__(self, ws, shell=None, cwd=None, username=None):
        self.ws = ws
        self.pty = None
        self.shell = shell or os.environ.get('COMSPEC', 'cmd.exe')
        self.cwd = cwd or os.path.expanduser('~')
        self.username = username
        self._running = False

    async def start(self):
        try:
            self.pty = PtyProcess.spawn(
                self.shell,
                cwd=self.cwd,
                dimensions=(24, 80),
                env=os.environ.copy(),
            )
            self._running = True
            await asyncio.gather(
                self._read_pty(),
                self._write_pty(),
            )
        except Exception as e:
            await self.ws.send(json.dumps({'type': 'error', 'data': str(e)}))
        finally:
            self.close()

    async def _read_pty(self):
        loop = asyncio.get_event_loop()
        try:
            while self._running and not self.pty.isalive():
                await asyncio.sleep(0.05)
            while self._running:
                try:
                    data = await loop.run_in_executor(None, self._read_blocking)
                    if data is None:
                        break
                    if data:
                        await self.ws.send(json.dumps({'type': 'output', 'data': data}))
                except Exception:
                    break
        except Exception:
            pass
        finally:
            try:
                await self.ws.send(json.dumps({'type': 'exit'}))
            except Exception:
                pass

    def _read_blocking(self):
        try:
            if not self.pty.isalive():
                return None
            data = self.pty.read(4096)
            if data is None:
                return None
            # 解码为字符串，处理编码错误
            if isinstance(data, bytes):
                data = data.decode('utf-8', errors='replace')
            return data
        except EOFError:
            return None
        except Exception:
            return None

    async def _write_pty(self):
        try:
            async for message in self.ws:
                try:
                    msg = json.loads(message)
                    if msg.get('type') == 'input':
                        self.pty.write(msg['data'])
                    elif msg.get('type') == 'resize':
                        rows = msg.get('rows', 24)
                        cols = msg.get('cols', 80)
                        try:
                            self.pty.setwinsize(rows, cols)
                        except Exception:
                            pass
                    elif msg.get('type') == 'kill':
                        self.close()
                        break
                except json.JSONDecodeError:
                    self.pty.write(message)
        except Exception:
            pass

    def close(self):
        self._running = False
        try:
            if self.pty and self.pty.isalive():
                self.pty.kill(signal.SIGTERM)
        except Exception:
            try:
                if self.pty:
                    self.pty.close()
            except Exception:
                pass


sessions = {}


async def terminal_handler(websocket):
    session_id = None
    username = None
    try:
        # 等待客户端发送初始化消息
        init_msg = await asyncio.wait_for(websocket.recv(), timeout=10)
        init = json.loads(init_msg)

        if init.get('type') != 'init':
            await websocket.send(json.dumps({'type': 'error', 'data': 'Expected init message'}))
            return

        # 验证 token
        token = init.get('token', '')
        username = validate_terminal_token(token)
        if not username:
            await websocket.send(json.dumps({'type': 'error', 'data': '认证失败，请重新登录'}))
            return

        shell = init.get('shell', os.environ.get('COMSPEC', 'cmd.exe'))
        cwd = init.get('cwd', os.path.expanduser('~'))
        session_id = init.get('session_id', str(id(websocket)))

        await websocket.send(json.dumps({'type': 'connected', 'session_id': session_id, 'username': username}))

        session = TerminalSession(websocket, shell=shell, cwd=cwd, username=username)
        sessions[session_id] = session

        await session.start()

    except asyncio.TimeoutError:
        try:
            await websocket.send(json.dumps({'type': 'error', 'data': '连接超时'}))
        except Exception:
            pass
    except Exception as e:
        try:
            await websocket.send(json.dumps({'type': 'error', 'data': str(e)}))
        except Exception:
            pass
    finally:
        if session_id and session_id in sessions:
            sessions[session_id].close()
            del sessions[session_id]


async def main(host='0.0.0.0', port=5001):
    print(f'[Terminal] WebSocket terminal server on ws://{host}:{port}')
    print(f'[Terminal] Token authentication enabled')
    async with websockets.serve(
        terminal_handler, host, port,
        max_size=1024*1024,
        ping_interval=30,
        ping_timeout=10,
        close_timeout=5,
    ):
        await asyncio.Future()


def start_terminal_server(port=5001):
    """在后台线程中启动终端服务器"""
    def run():
        asyncio.run(main(port=port))
    t = threading.Thread(target=run, daemon=True)
    t.start()
    return t


if __name__ == '__main__':
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 5001
    asyncio.run(main(port=port))
