# -*- coding: utf-8 -*-
from flask import Blueprint, request, jsonify, Response
import subprocess, os, sys, time, json, threading, signal, shutil, codecs, platform, collections, queue, zipfile
from pathlib import Path
import tempfile as _tempfile
from auth import login_required, get_current_user, get_user_dir
from config import WORK_DIR, TORCH_PYTHON, BUFFER_MAX_LINES
from state import (
    _file_running, _file_lock,
    _user_processes, _user_proc_lock,
    _upload_running, _upload_lock,
    _nb_kernels, _nb_kernels_lock,
    _nb_kernel_files, _nb_kernel_users, _nb_kernel_busy,
    _terminals, _terminals_lock,
)
from utils import get_env, notify_run_complete, is_path_allowed

bp = Blueprint('execution', __name__)


# DataParallel 自动包装代码，注入到用户脚本开头
DATAPARALLEL_INJECT = r'''
import torch as _t
if _t.cuda.device_count() > 1:
    _orig_to = _t.nn.Module.to
    _dp_set = set()
    def _dp_to(self, *a, **k):
        _orig_to(self, *a, **k)
        try:
            if len(list(self.parameters())) > 0 and id(self) not in _dp_set:
                dev = str(a[0]) if a else str(k.get('device',''))
                if 'cuda' in dev or (not dev and next(self.parameters()).is_cuda):
                    self = _t.nn.DataParallel(self)
                    _dp_set.add(id(self))
                    print(f"[MultiGPU] DataParallel 包装完成，{_t.cuda.device_count()} GPUs")
        except: pass
        return self
    _t.nn.Module.to = _dp_to
    print(f"[MultiGPU] 检测到 {_t.cuda.device_count()} GPUs，已启用自动并行")
else:
    print("[MultiGPU] 仅 1 个 GPU，跳过")
'''


WRAPPER_SCRIPT = r'''
import sys, os, json, traceback
os.environ["MPLBACKEND"] = "Agg"
os.environ["PYTHONUNBUFFERED"] = "1"

__SENTINEL__ = "__SENTINEL_PATH__"
__OUTPUT__ = "__OUTPUT_PATH__"
__ns = {"__builtins__": __builtins__}
__ns["__name__"] = "__main__"

_outf = open(__OUTPUT__, "wb", buffering=0)
_outfd = _outf.fileno()
os.dup2(_outfd, 1)
os.dup2(_outfd, 2)
class _UnbufferedWriter:
    def __init__(self, fd):
        self._fd = fd
    def write(self, s):
        if isinstance(s, str):
            s = s.encode('utf-8', errors='replace')
        os.write(self._fd, s)
        return len(s)
    def flush(self):
        pass
    def reconfigure(self, **kw):
        pass
sys.stdout = _UnbufferedWriter(1)
sys.stderr = _UnbufferedWriter(2)

import builtins as _builtins
_real_print = _builtins.print
def _flush_print(*args, **kwargs):
    kwargs.setdefault('flush', True)
    _real_print(*args, **kwargs)
__ns["print"] = _flush_print

try:
    import matplotlib
    import matplotlib.pyplot as _plt
    import base64 as _b64
    matplotlib.rcParams['font.sans-serif'] = ['SimHei', 'Microsoft YaHei', 'DejaVu Sans']
    matplotlib.rcParams['axes.unicode_minus'] = False
    def _inline_show(*a, **kw):
        for num in _plt.get_fignums():
            fig = _plt.figure(num)
            from io import BytesIO
            buf = BytesIO()
            fig.savefig(buf, format="png", bbox_inches="tight", dpi=100)
            buf.seek(0)
            b64 = _b64.b64encode(buf.read()).decode("ascii")
            print("<!--IMG:" + b64 + "-->", flush=True)
        _plt.close("all")
    _plt.show = _inline_show
    __ns["_plt"] = _plt
except ImportError:
    pass

while True:
    code = ""
    for line in sys.stdin:
        stripped = line.rstrip("\n")
        if stripped == "__CELL_END__":
            break
        code += line
    else:
        break

    code = code.rstrip("\n")
    if not code:
        sys.stdout.flush()
        sys.stderr.flush()
        with open(__SENTINEL__, "w", encoding="utf-8") as f:
            json.dump({"rc": 0, "exc": None, "tb": None}, f)
        continue

    _outf.seek(0)
    _outf.truncate()

    try:
        exec(compile(code, "<cell>", "exec"), __ns)
        rc, exc, tb = 0, None, None
    except SystemExit as e:
        rc = int(getattr(e, "code", 1) or 0)
        exc, tb = "SystemExit", ""
    except Exception as e:
        rc = 1
        exc = type(e).__name__
        tb = traceback.format_exc()
        print(tb, end="", flush=True)

    sys.stdout.flush()
    sys.stderr.flush()
    with open(__SENTINEL__, "w", encoding="utf-8") as f:
        json.dump({"rc": rc, "exc": exc, "tb": tb}, f)
'''

FILE_RUNNER_WRAPPER = r'''
import sys, os
os.environ["MPLBACKEND"] = "Agg"
os.environ["PYTHONUNBUFFERED"] = "1"

__SCRIPT_PATH__ = "__SCRIPT_PATH_PLACEHOLDER__"

try:
    import matplotlib
    import matplotlib.pyplot as _plt
    import base64 as _b64
    matplotlib.rcParams['font.sans-serif'] = ['SimHei', 'Microsoft YaHei', 'DejaVu Sans']
    matplotlib.rcParams['axes.unicode_minus'] = False
    def _inline_show(*a, **kw):
        for num in _plt.get_fignums():
            fig = _plt.figure(num)
            from io import BytesIO
            buf = BytesIO()
            fig.savefig(buf, format="png", bbox_inches="tight", dpi=100)
            buf.seek(0)
            b64 = _b64.b64encode(buf.read()).decode("ascii")
            print("<!--IMG:" + b64 + "-->", flush=True)
        _plt.close("all")
    _plt.show = _inline_show
except ImportError:
    pass

import runpy
runpy.run_path(__SCRIPT_PATH__, run_name="__main__")

try:
    _plt.show()
except:
    pass
'''


def _get_or_create_kernel(path, python_exe, run_cwd, gpu_id=None, gpu_strategy=""):
    with _nb_kernels_lock:
        proc = _nb_kernels.get(path)
        files = _nb_kernel_files.get(path)
        if proc and proc.poll() is not None:
            proc = None
        if proc and files:
            return proc, files['sentinel'], files['output']
        tmp_dir = _tempfile.mkdtemp(prefix='_nb_kernel_')
        sentinel_path = os.path.join(tmp_dir, 'sentinel.json')
        output_path = os.path.join(tmp_dir, 'output.txt')
        wrapper_code = WRAPPER_SCRIPT.replace("__SENTINEL_PATH__", sentinel_path.replace("\\", "\\\\"))
        wrapper_code = wrapper_code.replace("__OUTPUT_PATH__", output_path.replace("\\", "\\\\"))
        if gpu_strategy == 'dataparallel':
            wrapper_code = wrapper_code.replace(
                'while True:',
                DATAPARALLEL_INJECT + '\nwhile True:',
                1
            )
        try:
            proc = subprocess.Popen(
                [python_exe, '-u', '-c', wrapper_code],
                cwd=run_cwd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                env=get_env(gpu_id),
                text=True,
                encoding='utf-8', errors='replace',
                bufsize=0,
            )
            _nb_kernels[path] = proc
            _nb_kernel_files[path] = {'sentinel': sentinel_path, 'output': output_path}
            return proc, sentinel_path, output_path
        except Exception as e:
            return None, str(e), None


def _read_kernel_output(sentinel_path, output_path, timeout=120):
    import time as _time
    deadline = _time.time() + timeout
    result = None
    while _time.time() < deadline:
        if os.path.exists(sentinel_path):
            try:
                with open(sentinel_path, 'r', encoding='utf-8') as f:
                    content = f.read().strip()
                if content:
                    result = json.loads(content)
                    break
            except (json.JSONDecodeError, OSError):
                pass
        _time.sleep(0.05)
    if result is None:
        result = {'rc': -1, 'exc': 'Timeout', 'tb': f'执行超时（{timeout}秒）'}
    output_lines = []
    try:
        if os.path.exists(output_path):
            with open(output_path, 'r', encoding='utf-8', errors='replace') as f:
                for line in f:
                    output_lines.append(line.rstrip('\n\r'))
    except Exception:
        pass
    try:
        if os.path.exists(sentinel_path):
            os.remove(sentinel_path)
    except Exception:
        pass
    return output_lines, result


@bp.route('/api/run_file')
@login_required
def run_file():
    user = get_current_user()
    path = request.args.get('path', '')
    python_path = request.args.get('python_path', '')
    env_choice = request.args.get('env', 'system')
    cwd = request.args.get('cwd', '')
    gpu_id = request.args.get('gpu', '')
    gpu_strategy = request.args.get('gpu_strategy', '')

    if not path:
        return jsonify({'error': '未指定文件路径'}), 400
    if not is_path_allowed(path, user['username']):
        return jsonify({'error': '无权访问该文件'}), 403

    p = Path(path)
    if not p.exists() or not p.is_file():
        return jsonify({'error': '文件不存在'}), 404
    if not p.name.endswith('.py') and not p.name.endswith('.m'):
        return jsonify({'error': '只支持 .py 和 .m 文件'}), 400

    with _file_lock:
        if p.name in _file_running:
            return jsonify({'error': '该文件正在运行中'}), 409

    def generate():
        proc = None
        drain_thread = None
        output_queue = queue.Queue()
        username = user['username']
        filename = p.name
        try:
            is_wsl = False
            if python_path and python_path.startswith('wsl:'):
                is_wsl = True
                wsl_python = python_path[4:]
                python_exe = 'wsl.exe'
            elif python_path:
                python_exe = python_path
            elif env_choice == 'torch':
                if TORCH_PYTHON:
                    python_exe = TORCH_PYTHON
                else:
                    yield 'data: ' + json.dumps({'type': 'error', 'msg': '未找到 PyTorch 环境'}) + '\n\n'
                    return
            elif env_choice and env_choice.startswith('wsl:'):
                is_wsl = True
                wsl_python = env_choice[4:]
                python_exe = 'wsl.exe'
            else:
                python_exe = sys.executable

            is_matlab = p.name.endswith('.m')
            matlab_exe = None
            if is_matlab:
                if python_path and os.path.basename(python_path).lower().startswith('matlab'):
                    matlab_exe = python_path
                elif python_path and os.path.basename(python_path).lower().startswith('octave'):
                    matlab_exe = python_path
                elif env_choice == 'matlab':
                    matlab_exe = 'matlab'
                elif env_choice == 'octave':
                    matlab_exe = 'octave'
                else:
                    for cmd in ['matlab', 'octave']:
                        try:
                            find_cmd = 'where' if platform.system() == 'Windows' else 'which'
                            r = subprocess.run([find_cmd, cmd], capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=3,
                                               creationflags=0x08000000 if platform.system() == 'Windows' else 0)
                            if r.returncode == 0 and r.stdout.strip():
                                matlab_exe = cmd
                                break
                        except Exception:
                            pass
                if not matlab_exe:
                    yield 'data: ' + json.dumps({'type': 'error', 'msg': '未找到 MATLAB 或 Octave，请先安装'}) + '\n\n'
                    return

            user_dir = get_user_dir(username)
            try:
                if cwd and is_path_allowed(cwd, username) and Path(cwd).is_dir():
                    run_cwd = cwd
                else:
                    run_cwd = str(user_dir)
            except Exception:
                run_cwd = str(user_dir)

            # 最终验证 run_cwd 是否有效，无效则回退到脚本所在目录或用户目录
            try:
                if not Path(run_cwd).is_dir():
                    run_cwd = str(p.parent) if p.parent.is_dir() else str(user_dir)
            except Exception:
                run_cwd = str(user_dir)

            print(f'[run_file] cwd={cwd!r}, run_cwd={run_cwd!r}, user_dir={str(user_dir)!r}, p.parent={str(p.parent)!r}')

            # 确保 run_cwd 目录存在
            try:
                Path(run_cwd).mkdir(parents=True, exist_ok=True)
            except Exception as e:
                print(f'[run_file] mkdir 失败: {e}, 回退到脚本目录')
                run_cwd = str(p.parent) if p.parent.is_dir() else str(user_dir)

            if is_matlab:
                import tempfile as _tmp
                is_octave = 'octave' in os.path.basename(matlab_exe).lower()
                if is_octave:
                    _img_dir = str(user_dir / 'config' / '_plots')
                    os.makedirs(_img_dir, exist_ok=True)
                    for _f in os.listdir(_img_dir):
                        try:
                            os.remove(os.path.join(_img_dir, _f))
                        except Exception:
                            pass
                    _img_dir_safe = _img_dir.replace('\\', '/')
                    _plot_wrapper = (
                        f"cd('{run_cwd.replace(chr(92), '/')}'); "
                        f"run('{str(p).replace(chr(92), '/')}'); "
                        "drawnow; "
                        "figs = get(0, 'Children'); "
                        "if ~isempty(figs) "
                        "  for i = 1:length(figs) "
                        "    drawnow; "
                        f"    print(figs(i), fullfile('{_img_dir_safe}', sprintf('plot_%d.png', i)), '-dpng', '-r600'); "
                        "  end "
                        "end"
                    )
                    _tmp_script = _tmp.NamedTemporaryFile(mode='w', suffix='.m', delete=False, dir=run_cwd)
                    _tmp_script.write(_plot_wrapper)
                    _tmp_script.close()
                    cmd = [matlab_exe, '--no-gui', '--no-window-system', _tmp_script.name]
                else:
                    script_name = p.stem
                    script_dir = str(p.parent)
                    _img_dir = str(user_dir / 'config' / '_plots')
                    os.makedirs(_img_dir, exist_ok=True)
                    for _f in os.listdir(_img_dir):
                        if _f.endswith('.png') or _f.endswith('.tmp') or _f.endswith('.log'):
                            try:
                                os.remove(os.path.join(_img_dir, _f))
                            except Exception:
                                pass
                    _img_dir_mat = _img_dir.replace('/', '\\')
                    _run_id = str(int(time.time() * 1000))
                    _marker_file = os.path.join(_img_dir, f'_done_{_run_id}.tmp').replace('\\', '/')
                    _log_file = os.path.join(_img_dir, f'_output_{_run_id}.log').replace('\\', '/')
                    _matlab_cmd = (
                        f"addpath('{script_dir.replace(chr(92), '/')}'); "
                        f"diary('{_log_file}'); diary on; "
                        f"try, {script_name}; catch e, disp(e.message); end; "
                        f"drawnow; pause(1); "
                        f"figs = findall(0,'Type','figure'); "
                        f"if ~isempty(figs), "
                        f"  if ~isfolder('{_img_dir_mat}'), mkdir('{_img_dir_mat}'); end; "
                        f"  for i = 1:length(figs), "
                        f"    drawnow; "
                        f"    fname = fullfile('{_img_dir_mat}', sprintf('plot_%d.png', i)); "
                        f"    try, "
                        f"      print(figs(i), fname, '-dpng', '-r600'); "
                        f"    catch e2, "
                        f"      disp(['保存图片失败: ' fname ' -> ' e2.message]); "
                        f"    end; "
                        f"  end; "
                        f"end; "
                        f"diary off; "
                        f"f = fopen('{_marker_file}', 'w'); fclose(f); "
                        f"exit"
                    )
                    cmd = [matlab_exe, '-nosplash', '-nodesktop', '-r', _matlab_cmd]
                env = os.environ.copy()
                if gpu_id:
                    env['CUDA_VISIBLE_DEVICES'] = gpu_id
                proc = subprocess.Popen(
                    cmd,
                    cwd=run_cwd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    env=env,
                    text=True,
                    encoding='utf-8', errors='replace',
                    bufsize=1,
                    creationflags=0x08000000 if platform.system() == 'Windows' else 0,
                )
                with _file_lock:
                    _file_running[filename] = proc
                output_buffer = collections.deque(maxlen=BUFFER_MAX_LINES)
                with _user_proc_lock:
                    if username not in _user_processes:
                        _user_processes[username] = {}
                    _user_processes[username][filename] = {
                        'proc': proc, 'buffer': output_buffer,
                        'start_time': time.time(), 'path': str(p),
                        'finished': False, 'return_code': None,
                    }
                def drain_matlab():
                    _marker = _marker_file
                    _log = _log_file
                    _read_pos = 0
                    try:
                        while True:
                            if os.path.isfile(_log):
                                try:
                                    with open(_log, 'r', encoding='utf-8', errors='replace') as _f:
                                        _f.seek(_read_pos)
                                        _new = _f.read()
                                        _read_pos = _f.tell()
                                    if _new:
                                        for _line in _new.splitlines():
                                            _line = _line.rstrip()
                                            if _line:
                                                output_buffer.append(_line)
                                                output_queue.put(('stdout', _line))
                                except Exception:
                                    pass
                            if os.path.isfile(_marker):
                                try:
                                    os.remove(_marker)
                                except Exception:
                                    pass
                                break
                            time.sleep(0.3)
                    except Exception:
                        pass
                    finally:
                        if os.path.isfile(_log):
                            try:
                                with open(_log, 'r', encoding='utf-8', errors='replace') as _f:
                                    _f.seek(_read_pos)
                                    _new = _f.read()
                                if _new:
                                    for _line in _new.splitlines():
                                        _line = _line.rstrip()
                                        if _line:
                                            output_buffer.append(_line)
                                            output_queue.put(('stdout', _line))
                            except Exception:
                                pass
                        try:
                            proc.wait(timeout=5)
                        except Exception:
                            pass
                        try:
                            import psutil
                            parent = psutil.Process(proc.pid)
                            for c in parent.children(recursive=True):
                                try:
                                    c.wait(timeout=10)
                                except Exception:
                                    try:
                                        c.kill()
                                    except Exception:
                                        pass
                        except Exception:
                            pass
                        rc = proc.returncode if proc.returncode is not None else 0
                        output_queue.put(('done', rc))
                        with _user_proc_lock:
                            if username in _user_processes and filename in _user_processes[username]:
                                _user_processes[username][filename]['finished'] = True
                                _user_processes[username][filename]['return_code'] = rc
                        with _file_lock:
                            _file_running.pop(filename, None)
                        try:
                            if '_tmp_script' in dir() and _tmp_script:
                                os.unlink(_tmp_script.name)
                        except Exception:
                            pass
                        try:
                            notify_run_complete(username, filename, rc == 0, rc)
                        except Exception:
                            pass
                drain_thread = threading.Thread(target=drain_matlab, daemon=True)
                drain_thread.start()
                yield 'data: ' + json.dumps({'type': 'start', 'file': filename}) + '\n\n'
                while True:
                    try:
                        msg_type, data = output_queue.get(timeout=0.5)
                        if msg_type == 'done':
                            import base64 as _b64_mod
                            _images = []
                            time.sleep(0.5)
                            if os.path.isdir(_img_dir):
                                for _f in sorted(os.listdir(_img_dir)):
                                    if _f.endswith('.png'):
                                        _fpath = os.path.join(_img_dir, _f)
                                        try:
                                            with open(_fpath, 'rb') as _imgf:
                                                _b64 = _b64_mod.b64encode(_imgf.read()).decode('ascii')
                                                _images.append(_b64)
                                            os.remove(_fpath)
                                        except Exception:
                                            pass
                            for _b64 in _images:
                                yield 'data: ' + json.dumps({'type': 'output', 'msg': '<!--IMG:' + _b64 + '-->'}) + '\n\n'
                            try:
                                if os.path.isfile(_log_file):
                                    os.remove(_log_file)
                            except Exception:
                                pass
                            try:
                                if os.path.isdir(_img_dir):
                                    import shutil as _shutil
                                    _shutil.rmtree(_img_dir, ignore_errors=True)
                            except Exception:
                                pass
                            yield 'data: ' + json.dumps({'type': 'done', 'return_code': data, 'file': filename}) + '\n\n'
                            break
                        elif msg_type == 'stdout':
                            yield 'data: ' + json.dumps({'type': 'output', 'msg': data}) + '\n\n'
                    except queue.Empty:
                        yield ': keepalive\n\n'
                return

            if gpu_strategy == 'accelerate':
                # Accelerate/DDP 策略
                # 非 WSL 时检查 accelerate 是否已安装
                if not is_wsl:
                    try:
                        import importlib
                        importlib.import_module('accelerate')
                    except ImportError:
                        yield 'data: ' + json.dumps({
                            'type': 'error',
                            'msg': 'accelerate 未安装，请运行: pip install accelerate'
                        }) + '\n\n'
                        return

                def _win_to_wsl(wp):
                    wp = wp.replace('\\', '/')
                    if len(wp) >= 2 and wp[1] == ':':
                        return '/mnt/' + wp[0].lower() + wp[2:]
                    return wp

                num_gpus = len(gpu_id.split(',')) if gpu_id else 1
                env = get_env(gpu_id)

                if is_wsl:
                    # WSL 模式：通过 wsl.exe -e 让 WSL 内部执行 accelerate
                    # 不用 subprocess.cwd（含 & 等特殊字符会被 CreateProcess 误解析）
                    # 改为在 bash -c 里用 cd 切换目录
                    import shlex
                    wsl_script = _win_to_wsl(str(p))
                    wsl_cwd_path = _win_to_wsl(run_cwd)
                    inner = (
                        f'cd {shlex.quote(wsl_cwd_path)} &&'
                        f' {wsl_python} -m accelerate.commands.launch'
                        f' --num_processes {num_gpus}'
                        f' --mixed_precision no'
                        f' {shlex.quote(wsl_script)}'
                    )
                    print(f'[accelerate-WSL] bash -c {inner}')
                    wsl_env = os.environ.copy()
                    if gpu_id:
                        wsl_env['CUDA_VISIBLE_DEVICES'] = gpu_id
                    proc = subprocess.Popen(
                        ['wsl.exe', '-e', 'bash', '-c', inner],
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        env=wsl_env,
                        text=True,
                        encoding='utf-8', errors='replace',
                        bufsize=1,
                        creationflags=0x08000000,
                    )
                else:
                    # Windows 本地模式
                    cmd = [python_exe, '-m', 'accelerate.commands.launch',
                           '--num_processes', str(num_gpus),
                           '--mixed_precision', 'no',
                           str(p)]
                    proc = subprocess.Popen(
                        cmd,
                        cwd=run_cwd,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        env=env,
                        text=True,
                        encoding='utf-8', errors='replace',
                        bufsize=1,
                    )
            else:
                # 单卡 或 DataParallel 策略
                wrapper_code = FILE_RUNNER_WRAPPER.replace(
                    "__SCRIPT_PATH_PLACEHOLDER__",
                    str(p).replace("\\", "\\\\")
                )
                if gpu_strategy == 'dataparallel':
                    wrapper_code = wrapper_code.replace(
                        'import runpy',
                        DATAPARALLEL_INJECT + '\nimport runpy'
                    )
                if is_wsl:
                    def win_to_wsl(wp):
                        wp = wp.replace('\\', '/')
                        if len(wp) >= 2 and wp[1] == ':':
                            return '/mnt/' + wp[0].lower() + wp[2:]
                        return wp
                    wsl_script_path = win_to_wsl(str(p))
                    wsl_cwd = win_to_wsl(run_cwd)
                    wrapper_code = wrapper_code.replace(
                        str(p).replace("\\", "\\\\"), wsl_script_path
                    ).replace(
                        str(p).replace("\\", "/"), wsl_script_path
                    )
                    wsl_env = os.environ.copy()
                    if gpu_id:
                        wsl_env['CUDA_VISIBLE_DEVICES'] = gpu_id
                    proc = subprocess.Popen(
                        ['wsl.exe', wsl_python, '-u', '-c', wrapper_code],
                        cwd=run_cwd,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        env=wsl_env,
                        text=True,
                        encoding='utf-8', errors='replace',
                        bufsize=1,
                        creationflags=0x08000000,
                    )
                else:
                    proc = subprocess.Popen(
                        [python_exe, '-u', '-c', wrapper_code],
                        cwd=run_cwd,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        env=get_env(gpu_id),
                        text=True,
                        encoding='utf-8', errors='replace',
                        bufsize=1,
                    )
            with _file_lock:
                _file_running[filename] = proc

            output_buffer = collections.deque(maxlen=BUFFER_MAX_LINES)
            with _user_proc_lock:
                if username not in _user_processes:
                    _user_processes[username] = {}
                _user_processes[username][filename] = {
                    'proc': proc,
                    'buffer': output_buffer,
                    'start_time': time.time(),
                    'path': str(p),
                    'finished': False,
                    'return_code': None,
                }

            def drain_pipe():
                try:
                    for line in proc.stdout:
                        line = line.rstrip()
                        output_buffer.append(line)
                        output_queue.put(('stdout', line))
                except Exception:
                    pass
                finally:
                    proc.wait()
                    rc = proc.returncode
                    output_queue.put(('done', rc))
                    with _user_proc_lock:
                        if username in _user_processes and filename in _user_processes[username]:
                            _user_processes[username][filename]['finished'] = True
                            _user_processes[username][filename]['return_code'] = rc
                    with _file_lock:
                        _file_running.pop(filename, None)
                    try:
                        notify_run_complete(username, filename, rc == 0, rc)
                    except Exception:
                        pass
                    def cleanup():
                        time.sleep(1800)
                        with _user_proc_lock:
                            if username in _user_processes and filename in _user_processes[username]:
                                if _user_processes[username][filename]['finished']:
                                    _user_processes[username].pop(filename, None)
                    threading.Thread(target=cleanup, daemon=True).start()

            drain_thread = threading.Thread(target=drain_pipe, daemon=True)
            drain_thread.start()

            yield 'data: ' + json.dumps({'type': 'status', 'msg': 'started'}) + '\n\n'

            while True:
                try:
                    msg_type, msg = output_queue.get(timeout=0.5)
                except queue.Empty:
                    yield 'data: ' + json.dumps({'type': 'heartbeat'}) + '\n\n'
                    continue

                if msg_type == 'done':
                    generated = []
                    for f in sorted(user_dir.iterdir()):
                        if f.suffix in ('.png', '.jpg', '.jpeg', '.bmp'):
                            if f.stat().st_mtime > time.time() - 60:
                                generated.append(f.name)
                    done = json.dumps({
                        'type': 'done',
                        'return_code': msg,
                        'images': generated,
                        'file': filename,
                    })
                    yield f'data: {done}\n\n'
                    break
                elif msg_type == 'stdout':
                    data = json.dumps({'type': 'stdout', 'msg': msg})
                    yield f'data: {data}\n\n'

        except GeneratorExit:
            pass
        except Exception as e:
            import traceback
            print(f'[run_file] generate() 异常: {e}')
            traceback.print_exc()
            err = json.dumps({'type': 'error', 'msg': str(e)})
            yield f'data: {err}\n\n'

    return Response(
        generate(),
        mimetype='text/event-stream',
        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'},
    )


@bp.route('/api/stop_file', methods=['POST'])
@login_required
def stop_file():
    user = get_current_user()
    username = user['username']
    data = request.get_json(silent=True) or {}
    target_name = data.get('filename', '')

    with _file_lock:
        if not _file_running:
            return jsonify({'error': '没有正在运行的文件'}), 404

        stopped_name = None
        if target_name:
            if target_name not in _file_running:
                return jsonify({'error': f'文件 {target_name} 未在运行'}), 404
            proc = _file_running[target_name]
            try:
                if sys.platform == 'win32':
                    proc.terminate()
                else:
                    os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            except Exception:
                pass
            _file_running.pop(target_name, None)
            stopped_name = target_name
        else:
            for name, proc in list(_file_running.items()):
                try:
                    if sys.platform == 'win32':
                        proc.terminate()
                    else:
                        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                except Exception:
                    pass
                _file_running.pop(name, None)
                stopped_name = name
                break

    if stopped_name:
        with _user_proc_lock:
            if username in _user_processes and stopped_name in _user_processes[username]:
                info = _user_processes[username][stopped_name]
                if not info['finished']:
                    info['finished'] = True
                    info['return_code'] = -1

    return jsonify({'status': 'stopped', 'filename': stopped_name})


@bp.route('/api/run_cell', methods=['POST'])
@login_required
def run_cell():
    user = get_current_user()
    data = request.get_json() or {}
    code = data.get('code', '')
    path = data.get('path', '')
    python_path = data.get('python_path', '')
    env_choice = data.get('env', 'system')
    gpu_id = data.get('gpu', '')
    gpu_strategy = data.get('gpu_strategy', '')

    if gpu_strategy == 'accelerate':
        return jsonify({'error': 'Accelerate/DDP 策略不支持 Notebook Cell 模式，请使用脚本运行或切换到 DataParallel'}), 400

    if not path:
        return jsonify({'error': '未指定 Notebook 路径'}), 400
    if not is_path_allowed(path, user['username']):
        return jsonify({'error': '无权访问该文件'}), 403
    if not code.strip():
        return jsonify({'error': 'Cell 代码为空'}), 400

    p = Path(path)
    if not p.exists():
        return jsonify({'error': 'Notebook 文件不存在'}), 404

    if python_path:
        python_exe = python_path
    elif env_choice == 'torch':
        if TORCH_PYTHON:
            python_exe = TORCH_PYTHON
        else:
            return jsonify({'error': '未找到 PyTorch 环境'}), 400
    else:
        python_exe = sys.executable

    run_cwd = str(p.parent)

    def generate():
        import time as _time
        try:
            proc, sentinel_path, output_path = _get_or_create_kernel(path, python_exe, run_cwd, gpu_id, gpu_strategy)
            with _nb_kernels_lock:
                _nb_kernel_users[path] = user['username']
                _nb_kernel_busy[path] = True
            if proc is None:
                with _nb_kernels_lock:
                    _nb_kernel_busy[path] = False
                err = json.dumps({'type': 'error', 'msg': f'无法启动内核：{sentinel_path}'})
                yield f'data: {err}\n\n'
                return

            try:
                if os.path.exists(sentinel_path):
                    os.remove(sentinel_path)
            except Exception:
                pass

            yield 'data: ' + json.dumps({'type': 'status', 'msg': 'started'}) + '\n\n'

            try:
                proc.stdin.write(code.rstrip('\n') + '\n__CELL_END__\n')
                proc.stdin.flush()
            except BrokenPipeError:
                err = json.dumps({'type': 'error', 'msg': '内核进程已断开，请重启内核'})
                yield f'data: {err}\n\n'
                return

            deadline = _time.time() + 120
            offset = 0
            result = None

            while _time.time() < deadline:
                if os.path.exists(sentinel_path):
                    try:
                        with open(sentinel_path, 'r', encoding='utf-8') as f:
                            content = f.read().strip()
                        if content:
                            result = json.loads(content)
                    except (json.JSONDecodeError, OSError):
                        pass

                if os.path.exists(output_path):
                    try:
                        with open(output_path, 'rb') as f:
                            f.seek(offset)
                            new_bytes = f.read()
                        if new_bytes:
                            offset += len(new_bytes)
                            new_data = new_bytes.decode('utf-8', errors='replace')
                            for line in new_data.split('\n'):
                                line = line.rstrip('\r')
                                if not line:
                                    continue
                                yield 'data: ' + json.dumps({'type': 'stdout', 'msg': line}) + '\n\n'
                    except Exception:
                        pass

                if result is not None:
                    if os.path.exists(output_path):
                        try:
                            with open(output_path, 'rb') as f:
                                f.seek(offset)
                                new_bytes = f.read()
                            if new_bytes:
                                new_data = new_bytes.decode('utf-8', errors='replace')
                                for line in new_data.split('\n'):
                                    line = line.rstrip('\r')
                                    if not line:
                                        continue
                                    yield 'data: ' + json.dumps({'type': 'stdout', 'msg': line}) + '\n\n'
                        except Exception:
                            pass
                    break

                _time.sleep(0.05)

            if result is None:
                result = {'rc': -1, 'exc': 'Timeout', 'tb': '执行超时'}

            try:
                if os.path.exists(sentinel_path):
                    os.remove(sentinel_path)
            except Exception:
                pass

            done_data = json.dumps({
                'type': 'done',
                'return_code': result.get('rc', -1),
                'images': [],
            })
            yield f'data: {done_data}\n\n'

        except Exception as e:
            err_data = json.dumps({'type': 'error', 'msg': str(e)})
            yield f'data: {err_data}\n\n'
        finally:
            with _nb_kernels_lock:
                _nb_kernel_busy[path] = False

    return Response(
        generate(),
        mimetype='text/event-stream',
        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'},
    )


@bp.route('/api/stop_cell', methods=['POST'])
@login_required
def stop_cell():
    user = get_current_user()
    data = request.get_json() or {}
    path = data.get('path', '')

    if not path:
        return jsonify({'error': '未指定 Notebook 路径'}), 400
    if not is_path_allowed(path, user['username']):
        return jsonify({'error': '无权访问'}), 403

    with _nb_kernels_lock:
        proc = _nb_kernels.pop(path, None)
        files = _nb_kernel_files.pop(path, None)
        _nb_kernel_users.pop(path, None)
        _nb_kernel_busy.pop(path, None)
        if proc and proc.poll() is None:
            try:
                proc.kill()
                proc.wait(timeout=3)
            except Exception:
                pass
        if files:
            tmp_dir = os.path.dirname(files.get('output', ''))
            if tmp_dir and os.path.isdir(tmp_dir):
                try:
                    shutil.rmtree(tmp_dir)
                except Exception:
                    pass

    return jsonify({'status': 'ok', 'msg': '已停止'})


@bp.route('/api/restart_kernel', methods=['POST'])
@login_required
def restart_kernel():
    user = get_current_user()
    data = request.get_json() or {}
    path = data.get('path', '')

    if not path:
        return jsonify({'error': '未指定 Notebook 路径'}), 400
    if not is_path_allowed(path, user['username']):
        return jsonify({'error': '无权访问'}), 403

    with _nb_kernels_lock:
        proc = _nb_kernels.pop(path, None)
        files = _nb_kernel_files.pop(path, None)
        _nb_kernel_users.pop(path, None)
        _nb_kernel_busy.pop(path, None)
        if proc and proc.poll() is None:
            try:
                proc.kill()
                proc.wait(timeout=3)
            except Exception:
                pass
        if files:
            tmp_dir = os.path.dirname(files.get('output', ''))
            if tmp_dir and os.path.isdir(tmp_dir):
                try:
                    shutil.rmtree(tmp_dir)
                except Exception:
                    pass

    return jsonify({'status': 'ok', 'msg': '内核已重启'})


@bp.route('/api/save_notebook', methods=['POST'])
@login_required
def api_save_notebook():
    user = get_current_user()
    data = request.get_json() or {}
    path = data.get('path', '')
    notebook = data.get('notebook', None)

    if not path:
        return jsonify({'error': '未指定文件路径'}), 400
    if notebook is None:
        return jsonify({'error': '未提供 Notebook 数据'}), 400
    if not is_path_allowed(path, user['username']):
        return jsonify({'error': '无权访问该文件'}), 403

    p = Path(path)
    if p.exists() and p.is_dir():
        return jsonify({'error': '不能覆盖文件夹'}), 400

    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        content = json.dumps(notebook, ensure_ascii=False, indent=1)
        with open(p, 'w', encoding='utf-8') as f:
            f.write(content)
        return jsonify({'status': 'ok'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@bp.route('/api/reconnect_output')
@login_required
def api_reconnect_output():
    user = get_current_user()
    username = user['username']
    filename = request.args.get('filename', '')

    if not filename:
        return jsonify({'error': '未指定文件名'}), 400

    with _user_proc_lock:
        procs = _user_processes.get(username, {})
        info = procs.get(filename)

    if not info:
        return jsonify({'error': '未找到该进程'}), 404

    def generate():
        try:
            for line in info['buffer']:
                data = json.dumps({'type': 'stdout', 'msg': line})
                yield f'data: {data}\n\n'

            if info['finished']:
                done = json.dumps({'type': 'done', 'return_code': info['return_code'], 'file': filename})
                yield f'data: {done}\n\n'
                return

            last_idx = len(info['buffer'])
            while True:
                time.sleep(0.3)
                current_buf = info['buffer']
                if len(current_buf) > last_idx:
                    for i in range(last_idx, len(current_buf)):
                        data = json.dumps({'type': 'stdout', 'msg': current_buf[i]})
                        yield f'data: {data}\n\n'
                    last_idx = len(current_buf)

                if info['finished']:
                    done = json.dumps({'type': 'done', 'return_code': info['return_code'], 'file': filename})
                    yield f'data: {done}\n\n'
                    break

        except GeneratorExit:
            pass

    return Response(
        generate(),
        mimetype='text/event-stream',
        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'},
    )


@bp.route('/api/terminal/start', methods=['POST'])
@login_required
def terminal_start():
    user = get_current_user()
    data = request.get_json() or {}
    cwd = data.get('cwd', '')
    if not cwd or not is_path_allowed(cwd, user['username']):
        cwd = str(get_user_dir(user['username']).resolve())

    import uuid
    term_id = str(uuid.uuid4())[:8]

    with _terminals_lock:
        _terminals[term_id] = {'cwd': cwd}

    return jsonify({'status': 'ok', 'term_id': term_id, 'cwd': cwd})


@bp.route('/api/terminal/exec', methods=['POST'])
@login_required
def terminal_exec():
    data = request.get_json() or {}
    term_id = data.get('term_id', '')
    cmd = data.get('cmd', '')

    with _terminals_lock:
        info = _terminals.get(term_id)
    if not info:
        return jsonify({'error': '终端不存在'}), 404

    cwd = info['cwd']

    stripped = cmd.strip()
    if stripped.lower().startswith('cd ') or stripped.lower().startswith('cd\t'):
        new_dir = stripped[3:].strip().strip('"').strip("'")
        if new_dir:
            test_path = Path(cwd) / new_dir if not Path(new_dir).is_absolute() else Path(new_dir)
            if test_path.is_dir():
                info['cwd'] = str(test_path.resolve())
                return Response(
                    'data: ' + json.dumps({'type': 'output', 'text': ''}) + '\n\n'
                    + 'data: ' + json.dumps({'type': 'done', 'rc': 0, 'cwd': info['cwd']}) + '\n\n',
                    mimetype='text/event-stream',
                    headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'},
                )
            else:
                return Response(
                    'data: ' + json.dumps({'type': 'output', 'text': '系统找不到指定的路径。\r\n'}) + '\n\n'
                    + 'data: ' + json.dumps({'type': 'done', 'rc': 1, 'cwd': cwd}) + '\n\n',
                    mimetype='text/event-stream',
                    headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'},
                )

    if stripped.lower() in ('cls', 'clear'):
        return Response(
            'data: ' + json.dumps({'type': 'clear'}) + '\n\n'
            + 'data: ' + json.dumps({'type': 'done', 'rc': 0, 'cwd': cwd}) + '\n\n',
            mimetype='text/event-stream',
            headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'},
        )

    env = os.environ.copy()
    env['PYTHONUNBUFFERED'] = '1'
    env['PYTHONIOENCODING'] = 'utf-8'

    def generate():
        try:
            if platform.system() == 'Windows':
                proc = subprocess.Popen(
                    cmd,
                    shell=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    cwd=cwd,
                    env=env,
                    bufsize=0,
                    creationflags=0x08000000,
                )
            else:
                proc = subprocess.Popen(
                    ['bash', '-c', cmd],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    cwd=cwd,
                    env=env,
                    bufsize=0,
                )

            decoder = codecs.getincrementaldecoder('utf-8')(errors='replace')
            while True:
                chunk = proc.stdout.read(1)
                if not chunk:
                    break
                text = decoder.decode(chunk)
                if text:
                    yield 'data: ' + json.dumps({'type': 'output', 'text': text}) + '\n\n'

            text = decoder.decode(b'', final=True)
            if text:
                yield 'data: ' + json.dumps({'type': 'output', 'text': text}) + '\n\n'

            rc = proc.wait()
            yield 'data: ' + json.dumps({'type': 'done', 'rc': rc, 'cwd': cwd}) + '\n\n'

        except Exception as e:
            yield 'data: ' + json.dumps({'type': 'output', 'text': str(e) + '\r\n'}) + '\n\n'
            yield 'data: ' + json.dumps({'type': 'done', 'rc': -1, 'cwd': cwd}) + '\n\n'

    return Response(
        generate(),
        mimetype='text/event-stream',
        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'},
    )


@bp.route('/api/terminal/close', methods=['POST'])
@login_required
def terminal_close():
    data = request.get_json() or {}
    term_id = data.get('term_id', '')
    with _terminals_lock:
        _terminals.pop(term_id, None)
    return jsonify({'status': 'ok'})


@bp.route('/api/terminal/token')
@login_required
def api_terminal_token():
    user = get_current_user()
    from terminal_server import generate_terminal_token
    token = generate_terminal_token(user['username'])
    return jsonify({'token': token})


@bp.route('/api/wsl_distros')
@login_required
def api_wsl_distros():
    try:
        result = subprocess.run(
            ['wsl.exe', '-l', '-q'],
            capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=10,
            creationflags=0x08000000,
        )
        distros = []
        for line in result.stdout.strip().split('\n'):
            name = line.strip().replace('\x00', '')
            if name and not name.startswith('-'):
                distros.append(name)
        return jsonify({'distros': distros})
    except Exception as e:
        return jsonify({'distros': [], 'error': str(e)})
