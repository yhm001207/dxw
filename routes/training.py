# -*- coding: utf-8 -*-
from flask import Blueprint, request, jsonify
import subprocess, os, sys, time, uuid, threading, platform
from pathlib import Path
from auth import login_required, get_current_user
from config import TORCH_PYTHON
from state import _train_queue, _train_queue_lock
from utils import get_user_webhook, save_user_webhook, send_webhook_notification

bp = Blueprint('training', __name__)


def _get_running_count():
    with _train_queue_lock:
        return sum(1 for t in _train_queue if t['status'] == 'running')


def _get_available_gpus():
    try:
        result = subprocess.run(
            ['nvidia-smi', '--query-gpu=index,memory.used,memory.total', '--format=csv,noheader,nounits'],
            capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=5
        )
        available = []
        for line in result.stdout.strip().split('\n'):
            if not line.strip():
                continue
            parts = [p.strip() for p in line.split(',')]
            idx, used, total = int(parts[0]), int(parts[1]), int(parts[2])
            if used < total * 0.1:
                available.append(idx)
        return available
    except Exception:
        return []


def _try_start_next():
    with _train_queue_lock:
        for task in _train_queue:
            if task['status'] != 'queued':
                continue
            if task['devices']:
                requested = [int(d) for d in task['devices'].split(',')]
                available = _get_available_gpus()
                if not all(g in available for g in requested):
                    continue
            task['status'] = 'running'
            task['started_at'] = time.time()
            t = threading.Thread(target=_run_queued_task, args=(task,), daemon=True)
            t.start()
            break


def _run_queued_task(task):
    try:
        _execute_script(
            task['username'],
            task['filename'],
            task['env'],
            task['devices'],
            task.get('gpu_strategy', ''),
        )
    except Exception as e:
        print(f'[Queue] 任务执行失败: {e}')
    finally:
        with _train_queue_lock:
            task['status'] = 'completed'
            task['finished_at'] = time.time()
        _try_start_next()


def _execute_script(username, filename, env, devices, gpu_strategy=''):
    filepath = Path(filename)
    if not filepath.exists():
        return

    # 解析 Python 可执行文件
    if env and env != 'system':
        python_exe = Path(env) / 'python.exe' if platform.system() == 'Windows' else Path(env) / 'bin' / 'python'
        if python_exe.exists():
            python_str = str(python_exe)
        else:
            python_str = env
    else:
        python_str = sys.executable

    env_vars = os.environ.copy()
    env_vars['PYTHONUNBUFFERED'] = '1'
    if devices:
        env_vars['CUDA_VISIBLE_DEVICES'] = devices

    patched_file = None

    if gpu_strategy == 'accelerate':
        # Accelerate/DDP 策略
        try:
            import importlib
            importlib.import_module('accelerate')
        except ImportError:
            print('[Queue] accelerate 未安装，请运行: pip install accelerate')
            return

        num_gpus = len(devices.split(',')) if devices else 1
        cmd_parts = [
            python_str, '-m', 'accelerate.commands.launch',
            '--num_processes', str(num_gpus),
            '--mixed_precision', 'no',
            str(filepath),
        ]
    elif gpu_strategy == 'dataparallel':
        # DataParallel 策略：创建临时补丁脚本
        dp_inject = r'''
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
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                original_code = f.read()
            patched_file = filepath.parent / f'_dp_patched_{uuid.uuid4().hex[:8]}.py'
            with open(patched_file, 'w', encoding='utf-8') as f:
                f.write(dp_inject + '\n' + original_code)
            cmd_parts = [python_str, str(patched_file)]
        except Exception as e:
            print(f'[Queue] 创建 DataParallel 补丁失败: {e}')
            return
    else:
        # 单卡策略
        cmd_parts = [python_str, str(filepath)]

    try:
        proc = subprocess.Popen(
            cmd_parts,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            cwd=str(filepath.parent),
            env=env_vars,
            bufsize=0,
            creationflags=0x08000000 if platform.system() == 'Windows' else 0,
        )
        proc.wait()
    except Exception as e:
        print(f'[Queue] 执行出错: {e}')
    finally:
        # 清理临时补丁文件
        if patched_file and patched_file.exists():
            try:
                patched_file.unlink()
            except Exception:
                pass


@bp.route('/api/train_queue', methods=['GET'])
@login_required
def api_get_queue():
    user = get_current_user()
    with _train_queue_lock:
        my_queue = [t for t in _train_queue if t['username'] == user['username']]
        running = [t for t in _train_queue if t['status'] == 'running']
    return jsonify({
        'queue': my_queue,
        'running_count': len(running),
        'total_in_queue': len(my_queue),
    })


@bp.route('/api/train_queue/add', methods=['POST'])
@login_required
def api_add_to_queue():
    user = get_current_user()
    data = request.get_json() or {}
    filename = data.get('filename', '')
    if not filename:
        return jsonify({'error': '缺少文件名'}), 400

    task_id = str(uuid.uuid4())[:8]
    task = {
        'id': task_id,
        'username': user['username'],
        'filename': filename,
        'env': data.get('env', 'system'),
        'devices': data.get('devices', ''),
        'gpu_strategy': data.get('gpu_strategy', ''),
        'status': 'queued',
        'added_at': time.time(),
        'started_at': None,
        'finished_at': None,
    }

    with _train_queue_lock:
        _train_queue.append(task)

    _try_start_next()

    return jsonify({'status': 'ok', 'task_id': task_id, 'queue_position': sum(1 for t in _train_queue if t['status'] == 'queued')})


@bp.route('/api/train_queue/remove', methods=['POST'])
@login_required
def api_remove_from_queue():
    data = request.get_json() or {}
    task_id = data.get('task_id', '')
    with _train_queue_lock:
        for i, t in enumerate(_train_queue):
            if t['id'] == task_id and t['status'] == 'queued':
                _train_queue.pop(i)
                return jsonify({'status': 'ok'})
    return jsonify({'error': '任务不存在或已在运行'}), 404


@bp.route('/api/train_queue/clear', methods=['POST'])
@login_required
def api_clear_queue():
    user = get_current_user()
    with _train_queue_lock:
        _train_queue[:] = [t for t in _train_queue if t['username'] != user['username'] or t['status'] == 'running']
    return jsonify({'status': 'ok'})


# ==================== 显存清理 ====================

@bp.route('/api/gpu/cleanup', methods=['POST'])
@login_required
def api_gpu_cleanup():
    try:
        server_pid = os.getpid()
        result = subprocess.run(
            ['nvidia-smi', '--query-compute-apps=pid,gpu_uuid,used_memory', '--format=csv,noheader,nounits'],
            capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=5
        )
        if result.returncode != 0:
            return jsonify({'error': 'nvidia-smi 执行失败'}), 500

        killed = []
        skipped = []
        for line in result.stdout.strip().split('\n'):
            if not line.strip():
                continue
            parts = [p.strip() for p in line.split(',')]
            pid = int(parts[0])
            mem = parts[2] if len(parts) > 2 else '?'
            if pid == server_pid:
                skipped.append(pid)
                continue
            try:
                os.kill(pid, 9)
                killed.append({'pid': pid, 'mem': mem + 'MB'})
            except ProcessLookupError:
                pass
            except PermissionError:
                skipped.append(pid)

        return jsonify({'killed': killed, 'skipped': len(skipped), 'total': len(killed) + len(skipped)})
    except FileNotFoundError:
        return jsonify({'error': 'nvidia-smi 未找到，可能没有 NVIDIA GPU'}), 400
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ==================== Webhook 通知配置 ====================

@bp.route('/api/webhook/config', methods=['GET'])
@login_required
def api_get_webhook():
    user = get_current_user()
    return jsonify(get_user_webhook(user['username']))


@bp.route('/api/webhook/config', methods=['POST'])
@login_required
def api_save_webhook():
    user = get_current_user()
    data = request.get_json() or {}
    current = get_user_webhook(user['username'])
    if 'enabled' in data:
        current['enabled'] = data['enabled']
    if 'provider' in data:
        current['provider'] = data['provider']
    if 'config' in data:
        current['config'] = data['config']
    if 'events' in data:
        current['events'] = data['events']
    save_user_webhook(user['username'], current)
    return jsonify({'status': 'ok'})


@bp.route('/api/webhook/test', methods=['POST'])
@login_required
def api_test_webhook():
    user = get_current_user()
    try:
        send_webhook_notification(user['username'], '测试通知', '这是一条测试消息，Webhook 配置成功！')
        return jsonify({'status': 'ok'})
    except Exception as e:
        return jsonify({'error': str(e)}), 400
