import os
import sys
import re
import subprocess
import threading
import time
from openai import OpenAI

from .utils import safe_rmtree


def _cleanup_venv(venv_dir):
    if venv_dir and os.path.exists(venv_dir):
        print(f"[LocalTest] 🧹 Cleaning up venv...")
        try:
            safe_rmtree(venv_dir)
            print(f"[LocalTest] ✅ Venv removed")
        except Exception as e:
            print(f"[LocalTest] ⚠️  Could not remove venv: {e}")


def run_project_locally(folder, context, openai_api_key=None, max_retries=1):
    lang      = context.get("detected_language", "unknown")
    framework = context.get("detected_framework", "unknown")
    ml_type   = context.get("ml_type", "unknown")
    entries   = context.get("entry_points_found", [])
    entry     = entries[0] if entries else ""

    print(f"\n[LocalTest] ══════════════════════════════════════════════════")
    print(f"[LocalTest] 🧪 Starting local test")
    print(f"[LocalTest] 📁 Folder:    {folder}")
    print(f"[LocalTest] 🐍 Language:  {lang}")
    print(f"[LocalTest] 🔧 Framework: {framework}")
    print(f"[LocalTest] 🤖 ML type:   {ml_type}")
    print(f"[LocalTest] 📄 Entry:     {entry}")
    print(f"[LocalTest] ══════════════════════════════════════════════════\n")

    FRAMEWORK_TIMEOUTS = {
        "streamlit":  120,
        "gradio":     120,
        "fastapi_ml":  60,
        "flask_ml":    60,
        "fastapi":     30,
        "flask":       30,
        "django":      30,
        "nextjs":      60,
        "express":     20,
        "fastify":     20,
        "default":     60,
    }

    def get_startup_timeout():
        key  = ml_type if ml_type not in ("unknown", "none", "") else framework
        base = FRAMEWORK_TIMEOUTS.get(key, FRAMEWORK_TIMEOUTS["default"])
        ml_libs = context.get("ml_frameworks", [])
        heavy   = {"pytorch", "tensorflow", "huggingface", "transformers",
                   "opencv", "fastai", "xgboost", "lightgbm"}
        if heavy & set(ml_libs):
            base += 60
            print(f"[LocalTest] ⚠️  Heavy ML libs detected {list(heavy & set(ml_libs))} — adding 60s buffer")
        print(f"[LocalTest] ⏱️  Startup timeout: {base}s (key: {key})")
        return base

    venv_dir = os.path.join(folder, "_test_venv")
    if lang in ("python", "unknown"):
        print(f"[LocalTest] 🔨 Creating isolated venv at {venv_dir}...")
        venv_result = subprocess.run(
            [sys.executable, "-m", "venv", venv_dir],
            capture_output=True, text=True
        )
        if venv_result.returncode != 0:
            print(f"[LocalTest] ⚠️  Venv creation failed: {venv_result.stderr}")
            print(f"[LocalTest] ⚠️  Falling back to system Python")
            venv_python = sys.executable
        else:
            print(f"[LocalTest] ✅ Venv created")
            venv_python = (
                os.path.join(venv_dir, "Scripts", "python.exe")
                if os.name == "nt"
                else os.path.join(venv_dir, "bin", "python")
            )
    else:
        venv_python = sys.executable

    req_path = os.path.join(folder, "requirements.txt")
    if os.path.exists(req_path):
        print(f"[LocalTest] 📦 Installing requirements into venv...")
        result = subprocess.run(
            [venv_python, "-m", "pip", "install", "-r", "requirements.txt", "--quiet"],
            cwd=folder, capture_output=True, text=True
        )
        if result.returncode == 0:
            print(f"[LocalTest] ✅ Requirements installed")
        else:
            print(f"[LocalTest] ⚠️  Some requirements failed:\n{result.stderr[-300:]}")
    else:
        print(f"[LocalTest] ⚠️  No requirements.txt found")

    pkg_path = os.path.join(folder, "package.json")
    if os.path.exists(pkg_path):
        node_check = subprocess.run(["node", "--version"], capture_output=True)
        if node_check.returncode == 0:
            print(f"[LocalTest] 📦 Running npm install...")
            subprocess.run(["npm", "install", "--silent"], cwd=folder, capture_output=True)
            print(f"[LocalTest] ✅ npm install done")
        else:
            print(f"[LocalTest] ⚠️  Node.js not found — skipping npm install")

    cmd             = None
    success_signals = []
    is_server       = False

    if lang in ("python", "unknown"):

        if ml_type == "streamlit" or framework == "streamlit":
            print(f"[LocalTest] 🔧 Installing streamlit into venv...")
            subprocess.run(
                [venv_python, "-m", "pip", "install", "streamlit", "--quiet"],
                capture_output=True
            )
            e   = context.get("streamlit_entry_file", entry)
            cmd = [
                venv_python, "-m", "streamlit", "run", e,
                "--server.headless=true",
                "--server.port=8501",
                "--server.address=0.0.0.0",
            ]
            success_signals = [
                "you can now view",
                "network url",
                "local url",
                "http://",
                "started server",
            ]
            is_server = True
            print(f"[LocalTest] 📋 Command: {' '.join(cmd)}")

        elif ml_type == "gradio" or framework == "gradio":
            print(f"[LocalTest] 🔧 Installing gradio into venv...")
            subprocess.run(
                [venv_python, "-m", "pip", "install", "gradio", "--quiet"],
                capture_output=True
            )
            e   = context.get("gradio_entry_file", entry)
            cmd = [venv_python, e]
            success_signals = ["running on", "local url", "gradio"]
            is_server       = True
            print(f"[LocalTest] 📋 Command: {' '.join(cmd)}")

        elif framework == "fastapi" or ml_type == "fastapi_ml":
            print(f"[LocalTest] 🔧 Installing uvicorn into venv...")
            subprocess.run(
                [venv_python, "-m", "pip", "install", "uvicorn", "--quiet"],
                capture_output=True
            )
            e       = context.get("fastapi_entry_file", entry)
            mod     = e.replace("\\", "/").replace("/", ".").replace(".py", "")
            app_var = context.get("app_variable_name", "app")
            cmd     = [venv_python, "-m", "uvicorn", f"{mod}:{app_var}",
                       "--host", "0.0.0.0", "--port", "8000"]
            success_signals = ["application startup complete", "uvicorn running"]
            is_server       = True
            print(f"[LocalTest] 📋 Command: {' '.join(cmd)}")

        elif framework == "flask" or ml_type == "flask_ml":
            print(f"[LocalTest] 🔧 Installing flask into venv...")
            subprocess.run(
                [venv_python, "-m", "pip", "install", "flask", "--quiet"],
                capture_output=True
            )
            e   = context.get("flask_entry_file", entry)
            cmd = [venv_python, e]
            success_signals = ["running on", "serving flask", "debugger"]
            is_server       = True
            print(f"[LocalTest] 📋 Command: {' '.join(cmd)}")

        elif framework == "django":
            print(f"[LocalTest] 🔧 Running Django check...")
            cmd       = [venv_python, "manage.py", "check"]
            is_server = False
            print(f"[LocalTest] 📋 Command: {' '.join(cmd)}")

        elif ml_type == "ml_script":
            e         = context.get("ml_script_entry", entry)
            cmd       = [venv_python, "-m", "py_compile", e]
            is_server = False
            print(f"[LocalTest] 📋 Syntax check: {' '.join(cmd)}")

        elif entry and entry.endswith(".py"):
            cmd       = [venv_python, "-m", "py_compile", entry]
            is_server = False
            print(f"[LocalTest] 📋 Syntax check: {' '.join(cmd)}")

        else:
            print(f"[LocalTest] ℹ️  No Python entry point found — skipping")
            _cleanup_venv(venv_dir)
            return True

    elif lang == "nodejs":
        node_check = subprocess.run(["node", "--version"], capture_output=True)
        if node_check.returncode != 0:
            print(f"[LocalTest] ⚠️  Node.js not found — skipping")
            return True
        if framework in ("react", "vue", "vite", "svelte", "angular", "nextjs"):
            cmd       = ["npm", "run", "build"]
            is_server = False
        elif framework in ("express", "fastify"):
            cmd             = ["node", entry or "index.js"]
            is_server       = True
            success_signals = ["listening", "started", "running"]
        else:
            print(f"[LocalTest] ℹ️  Unknown Node framework — skipping")
            return True

    else:
        print(f"[LocalTest] ℹ️  Language '{lang}' — skipping local test")
        return True

    if not cmd:
        print(f"[LocalTest] ℹ️  No run command — skipping")
        _cleanup_venv(venv_dir)
        return True

    if not is_server:
        print(f"[LocalTest] ▶️  Running command...")
        result = subprocess.run(cmd, cwd=folder, capture_output=True, text=True)
        _cleanup_venv(venv_dir)
        if result.returncode == 0:
            print(f"[LocalTest] ✅ Project OK locally")
            return True
        else:
            print(f"[LocalTest] ❌ Failed:\n{result.stderr[-500:]}")
            return False

    startup_timeout = get_startup_timeout()
    print(f"[LocalTest] ▶️  Starting server ({startup_timeout}s timeout)...")

    collected_lines = []
    lock            = threading.Lock()

    def stream_reader(stream, label):
        try:
            for raw_line in iter(stream.readline, ""):
                line = raw_line.rstrip()
                if line:
                    print(f"[LocalTest/{label}]   {line}")
                    with lock:
                        collected_lines.append(line.lower())
        except Exception:
            pass

    proc = subprocess.Popen(
        cmd,
        cwd=folder,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    t_out = threading.Thread(target=stream_reader, args=(proc.stdout, "OUT"), daemon=True)
    t_err = threading.Thread(target=stream_reader, args=(proc.stderr, "ERR"), daemon=True)
    t_out.start()
    t_err.start()

    FATAL_KEYWORDS = [
        "modulenotfounderror", "importerror", "no module named",
        "syntaxerror", "traceback (most recent", "error:",
        "address already in use",
    ]

    startup_ok  = False
    start_time  = time.time()
    last_report = start_time

    while time.time() - start_time < startup_timeout:
        with lock:
            lines_snapshot = list(collected_lines)

        if any(sig in line for line in lines_snapshot for sig in success_signals):
            startup_ok = True
            break

        if any(kw in line for line in lines_snapshot for kw in FATAL_KEYWORDS):
            print(f"[LocalTest] ❌ Fatal error detected — stopping early")
            with lock:
                fatal_lines = list(collected_lines)
            fatal_log = "\n".join(fatal_lines)

            if openai_api_key:
                file_match = re.search(r'File "([^"]+\.py)", line (\d+)', fatal_log)
                if file_match:
                    failing_file = file_match.group(1)
                    failing_line = file_match.group(2)
                    if os.path.isfile(failing_file):
                        print(f"[LocalTest] 🔧 Asking GPT-4o to fix: {os.path.basename(failing_file)} line {failing_line}")
                        try:
                            with open(failing_file, "r", encoding="utf-8", errors="ignore") as _f:
                                file_src = _f.read()
                            client = OpenAI(api_key=openai_api_key)
                            resp = client.chat.completions.create(
                                model="gpt-4o",
                                messages=[
                                    {"role": "system", "content": "You are a Python expert. Fix the bug in the file. Return ONLY the complete fixed file content, no markdown, no backticks."},
                                    {"role": "user", "content": f"ERROR:\n{fatal_log[-2000:]}\n\nFILE ({failing_file}):\n{file_src}"}
                                ],
                                temperature=0.1,
                            )
                            fixed_src = resp.choices[0].message.content.strip()
                            if fixed_src.startswith("```"):
                                fixed_src = "\n".join(
                                    l for l in fixed_src.splitlines()
                                    if not l.strip().startswith("```")
                                ).strip()
                            with open(failing_file, "w", encoding="utf-8") as _f:
                                _f.write(fixed_src)
                            print(f"[LocalTest] ✅ File fixed — restarting server...")
                            try:
                                proc.kill()
                                proc.wait(timeout=5)
                            except Exception:
                                pass
                            collected_lines.clear()
                            proc = subprocess.Popen(
                                cmd, cwd=folder,
                                stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE,
                                text=True,
                            )
                            t_out = threading.Thread(target=stream_reader, args=(proc.stdout, "OUT"), daemon=True)
                            t_err = threading.Thread(target=stream_reader, args=(proc.stderr, "ERR"), daemon=True)
                            t_out.start()
                            t_err.start()
                            start_time = time.time()
                            continue
                        except Exception as fix_err:
                            print(f"[LocalTest] ⚠️  Auto-fix failed: {fix_err}")
            break

        if proc.poll() is not None:
            time.sleep(0.5)
            with lock:
                lines_snapshot = list(collected_lines)
            if any(sig in line for line in lines_snapshot for sig in success_signals):
                startup_ok = True
            break

        now = time.time()
        if now - last_report >= 15:
            elapsed   = int(now - start_time)
            remaining = int(startup_timeout - elapsed)
            print(f"[LocalTest] ⏳ Still waiting for startup... ({elapsed}s elapsed, {remaining}s remaining)")
            last_report = now

        time.sleep(0.3)

    if startup_ok:
        elapsed = int(time.time() - start_time)
        print(f"\n[LocalTest] ✅ Server started successfully (took ~{elapsed}s)")
        print(f"[LocalTest] 🌐 Open in browser:")
        port_map = {
            "streamlit": "8501", "gradio": "7860", "jupyter": "8888",
            "fastapi": "8000", "fastapi_ml": "8000",
            "flask": "5000", "flask_ml": "5000",
            "django": "8000", "express": "3000", "fastify": "3000",
        }
        key          = ml_type if ml_type not in ("unknown", "none", "") else framework
        browser_port = port_map.get(key, "8000")
        print(f"[LocalTest]    → http://localhost:{browser_port}")
        print(f"[LocalTest]    → http://127.0.0.1:{browser_port}")
        print(f"\n[LocalTest] Server is running. Press Enter to stop and continue deployment...")

        try:
            input()
        except KeyboardInterrupt:
            print(f"\n[LocalTest] Ctrl+C received — stopping server...")

        print(f"[LocalTest] 🛑 Stopping server...")
        try:
            proc.kill()
            proc.wait(timeout=5)
        except Exception:
            pass

        _cleanup_venv(venv_dir)
        print(f"[LocalTest] ✅ Server stopped — continuing deployment")
        return True

    else:
        elapsed = int(time.time() - start_time)
        print(f"[LocalTest] ⚠️  No startup signal after {elapsed}s — proceeding anyway")
        try:
            proc.kill()
            proc.wait(timeout=5)
        except Exception:
            pass
        _cleanup_venv(venv_dir)
        return False
