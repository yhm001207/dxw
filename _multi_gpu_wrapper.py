"""
多 GPU 并行包装器
用法: python _multi_gpu_wrapper.py <script.py> [args...]
自动将 model.to(device) 替换为 DataParallel 包装
"""
import sys
import os
import re
import tempfile

def patch_script(script_path):
    """读取脚本，注入 DataParallel 包装代码"""
    with open(script_path, 'r', encoding='utf-8') as f:
        code = f.read()

    # 注入头：monkey-patch model.to() 自动包装 DataParallel
    header = '''# === 多 GPU 并行注入（自动生成） ===
import torch
if torch.cuda.device_count() > 1:
    _original_torch_module_to = torch.nn.Module.to
    _dp_wrapped = set()
    def _auto_parallel_to(self, *args, **kwargs):
        _original_torch_module_to(self, *args, **kwargs)
        # 检查是否是模型（有 parameters 且参数量 > 0）
        try:
            params = list(self.parameters())
            if len(params) > 0 and id(self) not in _dp_wrapped:
                device = args[0] if args else kwargs.get('device', None)
                device_str = str(device) if device else ''
                if 'cuda' in device_str or (not device_str and next(self.parameters()).is_cuda):
                    self = torch.nn.DataParallel(self)
                    _dp_wrapped.add(id(self))
                    print(f"[MultiGPU] 已用 DataParallel 包装模型，使用 {torch.cuda.device_count()} 个 GPU")
        except (StopIteration, AttributeError):
            pass
        return self
    torch.nn.Module.to = _auto_parallel_to
    print(f"[MultiGPU] 检测到 {torch.cuda.device_count()} 个 GPU，已启用自动并行")
else:
    print("[MultiGPU] 仅检测到 1 个 GPU，跳过并行")
# === 注入结束 ===

'''

    # 写入临时文件
    tmp = tempfile.NamedTemporaryFile(
        mode='w', suffix='.py', delete=False,
        encoding='utf-8', dir=os.path.dirname(script_path)
    )
    tmp.write(header)
    tmp.write(code)
    tmp.close()
    return tmp.name


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("用法: python _multi_gpu_wrapper.py <script.py> [args...]")
        sys.exit(1)

    script = sys.argv[1]
    args = sys.argv[2:]

    patched = patch_script(script)
    try:
        # 用 exec 运行，保持 __name__ == '__main__'
        import subprocess
        result = subprocess.run(
            [sys.executable, patched] + args,
            cwd=os.path.dirname(os.path.abspath(script)) or '.'
        )
        sys.exit(result.returncode)
    finally:
        try:
            os.unlink(patched)
        except Exception:
            pass
