# -*- coding: utf-8 -*-
from flask import Blueprint, request, jsonify, session
import psutil, subprocess, os, sys, time, platform
from datetime import datetime
from auth import login_required, get_current_user, is_whitelisted, get_all_users
from config import WORK_DIR, TORCH_PYTHON
from state import (
    VISITORS, VISITORS_LOCK,
    _USER_TRAFFIC, _USER_TRAFFIC_LOCK,
    SERVER_STOPPED, SERVER_LOCK,
    _user_processes, _user_proc_lock,
    _running_processes, _running_lock,
    _nb_kernels, _nb_kernels_lock, _nb_kernel_users, _nb_kernel_busy,
)
from utils import detect_gpus, format_size, format_time

bp = Blueprint('monitoring', __name__)


@bp.route('/api/visitors')
@login_required
def api_visitors():
    user = get_current_user()
    if not is_whitelisted(user['username']):
        return jsonify({'error': '需要白名单权限'}), 403
    with VISITORS_LOCK:
        data = list(reversed(VISITORS[-100:]))
    return jsonify(data)


@bp.route('/api/traffic')
@login_required
def api_traffic():
    user = get_current_user()
    if not is_whitelisted(user['username']):
        return jsonify({'error': '需要白名单权限'}), 403
    with _USER_TRAFFIC_LOCK:
        result = []
        for username, t in _USER_TRAFFIC.items():
            result.append({
                'username': username,
                'upload': t['upload'],
                'download': t['download'],
                'upload_fmt': format_size(t['upload']),
                'download_fmt': format_size(t['download']),
                'total_fmt': format_size(t['upload'] + t['download']),
                'requests': t['requests'],
            })
    result.sort(key=lambda x: x['upload'] + x['download'], reverse=True)
    return jsonify(result)


@bp.route('/api/shutdown', methods=['POST'])
def shutdown():
    import state
    with SERVER_LOCK:
        state.SERVER_STOPPED = True
    return jsonify({'status': 'stopped'})


@bp.route('/api/start', methods=['POST'])
def start_server():
    import state
    with SERVER_LOCK:
        state.SERVER_STOPPED = False
    return jsonify({'status': 'running'})


@bp.route('/api/status')
def api_status():
    with SERVER_LOCK:
        return jsonify({'stopped': SERVER_STOPPED})


@bp.route('/api/showcase_status')
def api_showcase_status():
    cpu_percent = psutil.cpu_percent(interval=0.3)
    cpu_count = psutil.cpu_count()
    cpu_freq = psutil.cpu_freq()

    if not hasattr(api_showcase_status, '_cpu_name'):
        api_showcase_status._cpu_name = ''
        try:
            r = subprocess.run(['wmic', 'cpu', 'get', 'name'], capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=5,
                               creationflags=0x08000000 if os.name == 'nt' else 0)
            if r.returncode == 0:
                lines = [l.strip() for l in r.stdout.strip().split('\n') if l.strip() and l.strip() != 'Name']
                if lines:
                    api_showcase_status._cpu_name = lines[0]
        except Exception:
            try:
                api_showcase_status._cpu_name = platform.processor() or ''
            except Exception:
                pass
    cpu_name = api_showcase_status._cpu_name

    mem = psutil.virtual_memory()

    gpus = detect_gpus()
    for g in gpus:
        g['mem_percent'] = round(g['mem_used'] / g['mem_total'] * 100, 1) if g['mem_total'] > 0 else 0

    return jsonify({
        'cpu': {
            'name': cpu_name,
            'percent': cpu_percent,
            'count': cpu_count,
            'freq': f"{cpu_freq.current:.0f}MHz" if cpu_freq else 'N/A',
        },
        'memory': {
            'percent': mem.percent,
            'total': format_size(mem.total),
            'used': format_size(mem.used),
            'available': format_size(mem.available),
        },
        'gpus': gpus,
    })


@bp.route('/api/system_status')
@login_required
def api_system_status():
    user = get_current_user()
    username = user['username']

    cpu_percent = psutil.cpu_percent(interval=0.5)
    cpu_count = psutil.cpu_count()
    cpu_freq = psutil.cpu_freq()

    mem = psutil.virtual_memory()
    disk = psutil.disk_usage('/')

    gpu_info = None
    try:
        result = subprocess.run(
            [TORCH_PYTHON or sys.executable, '-c',
             'import torch;print(torch.cuda.is_available());'
             'print(torch.cuda.get_device_name(0) if torch.cuda.is_available() else "");'
             'print(torch.cuda.get_device_properties(0).total_memory if torch.cuda.is_available() else 0);'
             'print(torch.cuda.memory_allocated(0) if torch.cuda.is_available() else 0);'
             'print(torch.cuda.memory_reserved(0) if torch.cuda.is_available() else 0)'],
            capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=10
        )
        if result.returncode == 0:
            lines = result.stdout.strip().split('\n')
            if lines[0] == 'True':
                gpu_info = {
                    'name': lines[1],
                    'vram_total': format_size(int(lines[2])),
                    'vram_used': format_size(int(lines[3])),
                    'vram_cached': format_size(int(lines[4])),
                    'vram_percent': round(int(lines[3]) / int(lines[2]) * 100, 1) if int(lines[2]) > 0 else 0,
                }
    except Exception:
        pass

    from auth import get_user_dir
    user_dir = get_user_dir(username)
    user_dir_size = 0
    file_count = 0
    if user_dir.exists():
        for f in user_dir.rglob('*'):
            if f.is_file():
                try:
                    user_dir_size += f.stat().st_size
                    file_count += 1
                except Exception:
                    pass

    running_count = 0
    with _user_proc_lock:
        procs = _user_processes.get(username, {})
        running_count = sum(1 for p in procs.values() if not p['finished'])

    from utils import detect_python_environments
    environments = detect_python_environments()

    server_proc = psutil.Process(os.getpid())
    server_mem = server_proc.memory_info()
    server_start = server_proc.create_time()
    server_uptime = time.time() - server_start

    login_time = session.get('login_time', time.time())
    online_seconds = time.time() - login_time

    users_dir = WORK_DIR / 'users'
    leaderboard = []
    if users_dir.exists():
        for user_folder in users_dir.iterdir():
            if not user_folder.is_dir():
                continue
            uname = user_folder.name
            u_size = 0
            u_files = 0
            u_py_count = 0
            for f in user_folder.rglob('*'):
                if f.is_file():
                    try:
                        u_size += f.stat().st_size
                        u_files += 1
                        if f.suffix == '.py':
                            u_py_count += 1
                    except Exception:
                        pass
            leaderboard.append({
                'username': uname,
                'disk_bytes': u_size,
                'disk': format_size(u_size),
                'file_count': u_files,
                'py_count': u_py_count,
            })

    disk_rank = sorted(leaderboard, key=lambda x: x['disk_bytes'], reverse=True)
    file_rank = sorted(leaderboard, key=lambda x: x['file_count'], reverse=True)
    py_rank = sorted(leaderboard, key=lambda x: x['py_count'], reverse=True)

    return jsonify({
        'cpu': {
            'percent': cpu_percent,
            'count': cpu_count,
            'freq': f'{cpu_freq.current:.0f}MHz' if cpu_freq else '未知',
        },
        'memory': {
            'total': format_size(mem.total),
            'used': format_size(mem.used),
            'available': format_size(mem.available),
            'percent': mem.percent,
        },
        'disk': {
            'total': format_size(disk.total),
            'used': format_size(disk.used),
            'free': format_size(disk.free),
            'percent': disk.percent,
        },
        'gpu': gpu_info,
        'user': {
            'username': username,
            'dir_size': format_size(user_dir_size),
            'file_count': file_count,
            'running_processes': running_count,
            'online_time': format_time(online_seconds),
        },
        'server': {
            'uptime': format_time(server_uptime),
            'memory': format_size(server_mem.rss),
            'python_version': platform.python_version(),
            'platform': platform.platform(),
        },
        'python_environments': environments,
        'leaderboard': {
            'disk': disk_rank,
            'files': file_rank,
            'scripts': py_rank,
        },
    })


@bp.route('/api/leaderboard')
@login_required
def api_leaderboard():
    users_dir = WORK_DIR / 'users'
    leaderboard = []
    if users_dir.exists():
        for user_folder in users_dir.iterdir():
            if not user_folder.is_dir():
                continue
            uname = user_folder.name
            u_size = 0
            u_files = 0
            u_py_count = 0
            for f in user_folder.rglob('*'):
                if f.is_file():
                    try:
                        u_size += f.stat().st_size
                        u_files += 1
                        if f.suffix == '.py':
                            u_py_count += 1
                    except Exception:
                        pass
            leaderboard.append({
                'username': uname,
                'disk_bytes': u_size,
                'disk': format_size(u_size),
                'file_count': u_files,
                'py_count': u_py_count,
            })
    disk_rank = sorted(leaderboard, key=lambda x: x['disk_bytes'], reverse=True)
    file_rank = sorted(leaderboard, key=lambda x: x['file_count'], reverse=True)
    py_rank = sorted(leaderboard, key=lambda x: x['py_count'], reverse=True)
    return jsonify({
        'disk': disk_rank,
        'files': file_rank,
        'scripts': py_rank,
    })


@bp.route('/api/gpus')
@login_required
def api_gpus():
    gpus = detect_gpus()
    return jsonify({'gpus': gpus, 'count': len(gpus)})


@bp.route('/api/gpu_status')
@login_required
def api_gpu_status():
    gpus = detect_gpus()
    for g in gpus:
        g['mem_total_fmt'] = f"{g['mem_total']}MB"
        g['mem_used_fmt'] = f"{g['mem_used']}MB"
        g['mem_free_fmt'] = f"{g['mem_free']}MB"
        g['mem_percent'] = round(g['mem_used'] / g['mem_total'] * 100, 1) if g['mem_total'] > 0 else 0
    return jsonify({'gpus': gpus})


@bp.route('/api/performance_status')
@login_required
def api_performance_status():
    cpu_percent = psutil.cpu_percent(interval=0.3)
    cpu_count = psutil.cpu_count()
    cpu_freq = psutil.cpu_freq()

    if not hasattr(api_performance_status, '_cpu_name'):
        api_performance_status._cpu_name = ''
        try:
            r = subprocess.run(['wmic', 'cpu', 'get', 'name'], capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=5)
            if r.returncode == 0:
                lines = [l.strip() for l in r.stdout.strip().split('\n') if l.strip() and l.strip() != 'Name']
                if lines:
                    api_performance_status._cpu_name = lines[0]
        except Exception:
            try:
                import platform
                api_performance_status._cpu_name = platform.processor() or ''
            except Exception:
                pass
    cpu_name = api_performance_status._cpu_name

    cpu_temp = None
    try:
        import wmi
        w = wmi.WMI(namespace=r'root\OpenHardwareMonitor')
        sensors = w.Sensor()
        for s in sensors:
            if s.SensorType == 'Temperature' and 'CPU' in s.Name.upper():
                cpu_temp = float(s.Value)
                break
        if cpu_temp is None:
            temps = [float(s.Value) for s in sensors if s.SensorType == 'Temperature']
            if temps:
                cpu_temp = max(temps)
    except Exception:
        pass
    if cpu_temp is None:
        try:
            r = subprocess.run(
                ['powershell', '-Command',
                 r"Get-CimInstance MSAcpi_ThermalZoneTemperature -Namespace 'root/wmi' | Select -First 1 -ExpandProperty CurrentTemperature"],
                capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=5
            )
            if r.returncode == 0 and r.stdout.strip():
                raw = int(r.stdout.strip())
                cpu_temp = round(raw / 10.0 - 273.15, 1)
        except Exception:
            pass

    mem = psutil.virtual_memory()

    gpus = detect_gpus()
    for g in gpus:
        g['mem_percent'] = round(g['mem_used'] / g['mem_total'] * 100, 1) if g['mem_total'] > 0 else 0

    return jsonify({
        'cpu': {
            'name': cpu_name,
            'percent': cpu_percent,
            'count': cpu_count,
            'freq': f"{cpu_freq.current:.0f}MHz" if cpu_freq else 'N/A',
            'temp': cpu_temp,
        },
        'memory': {
            'percent': mem.percent,
            'used': format_size(mem.used),
            'available': format_size(mem.available),
            'total': format_size(mem.total),
            'used_bytes': mem.used,
            'total_bytes': mem.total,
        },
        'gpus': gpus,
    })


@bp.route('/api/users/list')
@login_required
def api_users_list():
    user = get_current_user()
    from auth import get_all_admins
    admins = get_all_admins()
    all_users = get_all_users()
    result = []
    for u in all_users:
        if u['username'] == user['username']:
            continue
        result.append({
            'username': u['username'],
            'role': u['role'],
        })
    return jsonify(result)


@bp.route('/api/python_environments')
def api_python_environments():
    from utils import detect_python_environments
    environments = detect_python_environments()
    return jsonify({'environments': environments})


@bp.route('/api/running_processes')
@login_required
def api_running_processes():
    user = get_current_user()
    username = user['username']
    result = []
    with _user_proc_lock:
        procs = _user_processes.get(username, {})
        for fname, info in procs.items():
            result.append({
                'filename': fname,
                'path': info['path'],
                'start_time': info['start_time'],
                'finished': info['finished'],
                'return_code': info['return_code'],
                'output_lines': len(info['buffer']),
            })
    return jsonify(result)


@bp.route('/api/active_user_processes')
@login_required
def api_active_user_processes():
    user = get_current_user()
    if not is_whitelisted(user['username']):
        return jsonify({'error': '无权限'}), 403

    # 获取 GPU 进程映射
    gpu_pid_map = _get_gpu_pid_map_v2()

    result = []
    now = time.time()
    with _user_proc_lock:
        for uname, procs in _user_processes.items():
            for fname, info in procs.items():
                if info.get('finished'):
                    continue
                elapsed = int(now - info['start_time'])
                mins, secs = divmod(elapsed, 60)
                hrs, mins = divmod(mins, 60)
                if hrs > 0:
                    elapsed_str = f'{hrs}h{mins}m'
                elif mins > 0:
                    elapsed_str = f'{mins}m{secs}s'
                else:
                    elapsed_str = f'{secs}s'
                pid = info['proc'].pid if hasattr(info['proc'], 'pid') else None
                gpu_info = gpu_pid_map.get(pid, []) if pid else []
                # 获取 CPU 和内存
                cpu_pct = 0.0
                mem_mb = 0
                if pid:
                    try:
                        p = psutil.Process(pid)
                        cpu_pct = p.cpu_percent(interval=0)
                        mem_info = p.memory_info()
                        mem_mb = round(mem_info.rss / 1024 / 1024, 1) if mem_info else 0
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        pass
                result.append({
                    'username': uname,
                    'filename': fname,
                    'path': info['path'],
                    'start_time': info['start_time'],
                    'elapsed': elapsed_str,
                    'pid': pid,
                    'gpu': gpu_info,
                    'cpu_percent': cpu_pct,
                    'mem_mb': mem_mb,
                })

    with _running_lock:
        for sname, proc in _running_processes.items():
            pid = proc.pid if hasattr(proc, 'pid') else None
            already = any(
                r['pid'] == pid for r in result if r['pid'] and pid
            ) if pid else False
            if not already:
                gpu_info = gpu_pid_map.get(pid, []) if pid else []
                cpu_pct = 0.0
                mem_mb = 0
                if pid:
                    try:
                        p = psutil.Process(pid)
                        cpu_pct = p.cpu_percent(interval=0)
                        mem_info = p.memory_info()
                        mem_mb = round(mem_info.rss / 1024 / 1024, 1) if mem_info else 0
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        pass
                result.append({
                    'username': 'system',
                    'filename': sname,
                    'path': '',
                    'start_time': time.time(),
                    'elapsed': '',
                    'pid': pid,
                    'gpu': gpu_info,
                    'cpu_percent': cpu_pct,
                    'mem_mb': mem_mb,
                })

    with _nb_kernels_lock:
        for nb_path, proc in _nb_kernels.items():
            if proc.poll() is not None:
                continue
            if not _nb_kernel_busy.get(nb_path):
                continue
            pid = proc.pid if hasattr(proc, 'pid') else None
            already = any(
                r['pid'] == pid for r in result if r['pid'] and pid
            ) if pid else False
            if not already:
                from pathlib import Path
                nb_p = Path(nb_path)
                nb_username = _nb_kernel_users.get(nb_path, '')
                gpu_info = gpu_pid_map.get(pid, []) if pid else []
                cpu_pct = 0.0
                mem_mb = 0
                if pid:
                    try:
                        p = psutil.Process(pid)
                        cpu_pct = p.cpu_percent(interval=0)
                        mem_info = p.memory_info()
                        mem_mb = round(mem_info.rss / 1024 / 1024, 1) if mem_info else 0
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        pass
                result.append({
                    'username': nb_username or '?',
                    'filename': nb_p.name,
                    'path': nb_path,
                    'start_time': time.time(),
                    'elapsed': '',
                    'pid': pid,
                    'gpu': gpu_info,
                    'cpu_percent': cpu_pct,
                    'mem_mb': mem_mb,
                })

    return jsonify(result)


@bp.route('/online_users')
@login_required
def online_users_page():
    user = get_current_user()
    if not is_whitelisted(user['username']):
        return jsonify({'error': '需要白名单权限'}), 403
    from flask import render_template
    return render_template('online_users.html')


@bp.route('/api/online_users')
@login_required
def api_online_users():
    user = get_current_user()
    if not is_whitelisted(user['username']):
        return jsonify({'error': '需要白名单权限'}), 403
    cutoff = time.time() - 300
    user_activity = {}
    with VISITORS_LOCK:
        for v in VISITORS:
            try:
                t = datetime.strptime(v['time'], '%Y-%m-%d %H:%M:%S').timestamp()
            except Exception:
                continue
            if t < cutoff:
                continue
            uname = v.get('username', '-')
            if uname == '-':
                continue
            entry = user_activity.setdefault(uname, {
                'username': uname,
                'last_time': v['time'],
                'last_path': v['path'],
                'ip': v['ip'],
                'requests': 0,
            })
            entry['requests'] += 1
            if v['time'] > entry['last_time']:
                entry['last_time'] = v['time']
                entry['last_path'] = v['path']
                entry['ip'] = v['ip']
    result = sorted(user_activity.values(), key=lambda x: x['last_time'], reverse=True)
    return jsonify(result)


def _get_gpu_pid_map_v2():
    """用 nvidia-smi 获取每个 PID 使用的 GPU 编号和显存"""
    gpu_map = {}  # pid -> [{gpu_id, gpu_name, mem_mb}]
    try:
        gpus = detect_gpus()
        gpu_id_map = {g['id']: g['name'] for g in gpus}

        for gpu_id, gpu_name in gpu_id_map.items():
            result = subprocess.run(
                ['nvidia-smi', '-i', str(gpu_id),
                 '--query-compute-apps=pid,used_gpu_memory',
                 '--format=csv,noheader,nounits'],
                capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=5,
                creationflags=0x08000000 if os.name == 'nt' else 0,
            )
            if result.returncode == 0:
                for line in result.stdout.strip().split('\n'):
                    if not line.strip():
                        continue
                    parts = [p.strip() for p in line.split(',')]
                    if len(parts) >= 1:
                        try:
                            pid = int(parts[0])
                            mem_str = parts[1] if len(parts) >= 2 else '0'
                            mem = int(mem_str) if mem_str.isdigit() else 0
                            if pid not in gpu_map:
                                gpu_map[pid] = []
                            gpu_map[pid].append({
                                'gpu_id': gpu_id,
                                'gpu_name': gpu_name,
                                'mem_mb': mem,
                            })
                        except (ValueError, IndexError):
                            continue
    except Exception:
        pass

    # 构建 pid -> 父pid 映射，用于子进程继承 GPU 信息
    child_to_parent = {}
    try:
        for proc in psutil.process_iter(['pid', 'ppid']):
            try:
                info = proc.info
                child_to_parent[info['pid']] = info.get('ppid', 0)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
    except Exception:
        pass

    # 将 GPU 信息向上继承给父进程
    parent_gpu_map = {}
    for pid, gpu_list in gpu_map.items():
        # 向上查找最多 3 层父进程
        cur = pid
        for _ in range(3):
            ppid = child_to_parent.get(cur, 0)
            if not ppid or ppid == cur:
                break
            if ppid not in parent_gpu_map:
                parent_gpu_map[ppid] = []
            parent_gpu_map[ppid].extend(gpu_list)
            cur = ppid

    # 合并：直接 GPU 进程 + 继承 GPU 的父进程
    for pid, gpu_list in parent_gpu_map.items():
        if pid not in gpu_map:
            gpu_map[pid] = gpu_list

    return gpu_map


@bp.route('/api/system_processes')
@login_required
def api_system_processes():
    """扫描系统中正在运行的脚本进程（Python/MATLAB等）"""
    user = get_current_user()
    if not is_whitelisted(user['username']):
        return jsonify({'error': '无权限'}), 403

    # 要监控的进程关键词（移除 R 和 Node.js）
    keywords = {
        'python': 'Python',
        'pythonw': 'Python',
        'matlab': 'MATLAB',
        'matlab.exe': 'MATLAB',
        'julia': 'Julia',
        'java': 'Java',
        'cscript': 'VBScript',
        'wscript': 'VBScript',
        'powershell': 'PowerShell',
    }

    # 获取 GPU 进程映射
    gpu_pid_map = _get_gpu_pid_map_v2()

    # 收集 web 端产生的进程 PID → username 映射
    web_pid_user = {}  # pid -> username
    with _user_proc_lock:
        for uname, procs in _user_processes.items():
            for fname, info in procs.items():
                p = info.get('proc')
                if p and hasattr(p, 'pid'):
                    web_pid_user[p.pid] = uname
    with _running_lock:
        for sname, proc in _running_processes.items():
            if hasattr(proc, 'pid'):
                web_pid_user[proc.pid] = 'web'

    result = []
    seen_pids = set()

    try:
        for proc in psutil.process_iter(['pid', 'name', 'cmdline', 'cpu_percent', 'memory_info', 'create_time', 'username', 'status']):
            try:
                info = proc.info
                name = (info['name'] or '').lower()
                cmdline = info.get('cmdline') or []
                cmdline_str = ' '.join(cmdline) if cmdline else ''

                # 匹配进程名或命令行中的关键词
                matched_type = None
                for kw, label in keywords.items():
                    if kw in name or kw in cmdline_str.lower():
                        matched_type = label
                        break

                if not matched_type:
                    continue

                # 跳过系统自身进程（如 Flask waitress）
                if 'waitress' in cmdline_str.lower() or 'flask' in cmdline_str.lower():
                    continue

                pid = info['pid']
                if pid in seen_pids:
                    continue
                seen_pids.add(pid)

                # 提取脚本文件路径
                script_path = ''
                for arg in cmdline:
                    if arg.endswith(('.py', '.m', '.mlx', '.jl', '.ps1', '.bat', '.sh')):
                        script_path = arg
                        break

                # CPU 和内存
                cpu_pct = info.get('cpu_percent', 0) or 0
                mem_info = info.get('memory_info')
                mem_mb = round(mem_info.rss / 1024 / 1024, 1) if mem_info else 0

                # 运行时间
                create_time = info.get('create_time', 0)
                elapsed = int(time.time() - create_time) if create_time else 0
                elapsed_str = ''
                if elapsed >= 3600:
                    elapsed_str = f'{elapsed // 3600}h{(elapsed % 3600) // 60}m'
                elif elapsed >= 60:
                    elapsed_str = f'{elapsed // 60}m{elapsed % 60}s'
                else:
                    elapsed_str = f'{elapsed}s'

                # GPU 信息
                gpu_info = gpu_pid_map.get(pid, [])

                result.append({
                    'pid': pid,
                    'type': matched_type,
                    'name': info['name'],
                    'script': script_path,
                    'cmdline': cmdline_str[:200],
                    'cpu_percent': cpu_pct,
                    'mem_mb': mem_mb,
                    'elapsed': elapsed_str,
                    'username': info.get('username', ''),
                    'status': info.get('status', ''),
                    'gpu': gpu_info,
                    'web_user': web_pid_user.get(pid, ''),
                })
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue
    except Exception as e:
        return jsonify({'error': str(e)}), 500

    # 按类型分组
    result.sort(key=lambda x: (x['type'], -x['cpu_percent']))
    return jsonify(result)
