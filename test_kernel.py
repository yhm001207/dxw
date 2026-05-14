import subprocess, os, sys, time, json

sentinel = "test_sentinel.json"
output_f = "test_output.txt"

# Read wrapper from app
sys.path.insert(0, os.path.dirname(__file__))
from app import WRAPPER_SCRIPT

wrapper_code = WRAPPER_SCRIPT.replace("__SENTINEL_PATH__", os.path.abspath(sentinel).replace("\\", "\\\\"))
wrapper_code = wrapper_code.replace("__OUTPUT_PATH__", os.path.abspath(output_f).replace("\\", "\\\\"))

env = os.environ.copy()
env["PYTHONUNBUFFERED"] = "1"
proc = subprocess.Popen(
    [sys.executable, "-u", "-c", wrapper_code],
    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    env=env, text=True, encoding="utf-8", bufsize=0,
)

def wait_and_read(sp, op, timeout=30):
    result = None
    deadline = time.time() + timeout
    while time.time() < deadline:
        if os.path.exists(sp):
            try:
                with open(sp) as f:
                    c = f.read().strip()
                if c:
                    result = json.loads(c)
                    break
            except:
                pass
        time.sleep(0.05)
    lines = []
    try:
        if os.path.exists(op):
            with open(op, encoding="utf-8", errors="replace") as f:
                for line in f:
                    lines.append(line.rstrip("\n\r"))
    except:
        pass
    try:
        if os.path.exists(sp): os.remove(sp)
    except:
        pass
    return lines, result

def clean():
    for fp in (sentinel, output_f):
        try:
            if os.path.exists(fp): os.remove(fp)
        except:
            pass

# Test 1: print
clean()
proc.stdin.write('print("hello world")\n__CELL_END__\n')
proc.stdin.flush()
lines, result = wait_and_read(sentinel, output_f)
print(f"T1 (print): {lines}")

# Test 2: matplotlib
clean()
proc.stdin.write('import matplotlib.pyplot as plt\nplt.plot([1,2,3],[4,5,6])\nplt.title("test")\nplt.show()\n__CELL_END__\n')
proc.stdin.flush()
lines, result = wait_and_read(sentinel, output_f)
has_img = any("<!--IMG:" in l for l in lines)
print(f"T2 (matplotlib): {len(lines)} lines, has_img={has_img}")

# Test 3: variable sharing
clean()
proc.stdin.write('print("x+10=", 42+10)\n__CELL_END__\n')
proc.stdin.flush()
lines, result = wait_and_read(sentinel, output_f)
print(f"T3 (variable): {lines}")

# Test 4: error
clean()
proc.stdin.write('raise ValueError("test error")\n__CELL_END__\n')
proc.stdin.flush()
lines, result = wait_and_read(sentinel, output_f)
has_err = any("ValueError" in l for l in lines)
print(f"T4 (error): has_err={has_err}, rc={result.get('rc') if result else 'N/A'}")

proc.kill()
clean()
print("ALL DONE")
