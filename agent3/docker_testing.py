import os
import re
import subprocess
import time
import socket
import urllib.request
from openai import OpenAI

from .dockerfile_gen import (
    detect_port_from_dockerfile,
    get_startup_wait,
    generate_dockerfile_with_openai,
)


def get_container_logs(container_name):
    result = subprocess.run(
        ["docker", "logs", "--tail", "100", container_name],
        capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )
    return result.stdout + result.stderr


def cleanup_test_container(container_name, image_tag):
    print(f"[Test] Cleaning up test container and image...")
    subprocess.run(["docker", "rm", "-f", container_name], capture_output=True)
    subprocess.run(["docker", "rmi", "-f", image_tag],     capture_output=True)
    print(f"[Test] Cleanup done")


def _find_free_port(preferred):
    """Return preferred port if free, otherwise find the next available one."""
    if preferred is None:
        return None
    preferred = int(preferred)
    for port in range(preferred, preferred + 20):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("", port))
                return str(port)
            except OSError:
                continue
    return str(preferred)


def fix_dockerfile_with_llm(dockerfile_path, error_output, error_type, context, openai_api_key):
    with open(dockerfile_path, "r", encoding="utf-8", errors="replace") as f:
        current_dockerfile = f.read()

    entries      = context.get("entry_points_found", [])
    entry        = entries[0] if entries else "app.py"
    entry_base   = os.path.basename(entry)
    entry_module = entry.replace("\\", "/").replace("/", ".").replace(".py", "")
    app_var      = context.get("app_variable_name", "app")
    framework    = context.get("detected_framework", "unknown")
    ml_type      = context.get("ml_type", "unknown")

    if framework == "fastapi" or ml_type == "fastapi_ml":
        e   = context.get("fastapi_entry_file", entry)
        mod = e.replace("\\", "/").replace("/", ".").replace(".py", "")
        correct_cmd = f'CMD ["sh", "-c", "uvicorn {mod}:{app_var} --host 0.0.0.0 --port ${{PORT:-8000}}"]'
    elif framework == "flask" or ml_type == "flask_ml":
        correct_cmd = f'CMD ["sh", "-c", "flask run --host=0.0.0.0 --port=${{PORT:-5000}}"]'
    elif framework == "django":
        correct_cmd = f'CMD ["sh", "-c", "python manage.py runserver 0.0.0.0:${{PORT:-8000}}"]'
    elif ml_type == "streamlit":
        e = context.get("streamlit_entry_file", entry_base)
        correct_cmd = f'CMD ["sh", "-c", "streamlit run {e} --server.port=${{PORT:-8501}} --server.address=0.0.0.0 --server.headless=true"]'
    elif ml_type == "gradio":
        e = context.get("gradio_entry_file", entry_base)
        correct_cmd = f'CMD ["sh", "-c", "python {e}"]'
    else:
        correct_cmd = f'CMD ["sh", "-c", "python {entry_base}"]'

    error_descriptions = {
        "build":        "The Docker image failed to BUILD with this error",
        "runtime":      "The Docker container failed to START with this error",
        "runtime_exit": "The Docker container STARTED but then EXITED immediately with this error",
        "no_response":  "The Docker container is running but NOT RESPONDING to HTTP requests",
    }
    error_desc = error_descriptions.get(error_type, "There was an error")

    prompt = f"""You are a Docker expert. {error_desc}:

ERROR OUTPUT:
{error_output[-3000:]}

CURRENT DOCKERFILE:
{current_dockerfile}

PROJECT INFO:
- Language:     {context.get("detected_language", "unknown")}
- Framework:    {framework}
- ML type:      {ml_type}
- ML libraries: {context.get("ml_frameworks", [])}
- Entry file:   {entry_base}        ← ACTUAL FILENAME, USE THIS EXACTLY
- Module name:  {entry_module}      ← USE FOR uvicorn (e.g. app.main for app/main.py)
- App variable: {app_var}           ← USE FOR uvicorn app variable

CORRECT CMD FOR THIS PROJECT — USE EXACTLY THIS:
{correct_cmd}

CRITICAL RULES — NEVER VIOLATE:
1. CMD MUST use sh -c shell form:
   ✅ CMD ["sh", "-c", "uvicorn {entry_module}:{app_var} --host 0.0.0.0 --port ${{PORT:-8000}}"]
   ❌ CMD ["uvicorn", "...", "${{PORT:-8000}}"]  ← shell vars do NOT expand in JSON array
2. Module name MUST be "{entry_module}" — if file is app/main.py use "app.main" not "main"
3. App variable MUST be "{app_var}"
4. Port MUST use ${{PORT:-DEFAULT}} inside sh -c

COMMON FIXES:
- "Could not import module": wrong module name — use "{entry_module}"
- "${{PORT:-8000}} is not a valid integer": switch to sh -c form immediately
- ModuleNotFoundError: add missing pip install
- libgomp not found: RUN apt-get install -y libgomp1
- libGL not found: RUN apt-get install -y libgl1-mesa-glx libglib2.0-0
- Container exits: check CMD references correct module "{entry_module}"

Output ONLY the fixed raw Dockerfile. No markdown, no backticks, no explanation.
"""

    client   = OpenAI(api_key=openai_api_key)
    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": f"Output ONLY the fixed raw Dockerfile. No markdown. No backticks. Entry module is '{entry_module}', app variable is '{app_var}'. CMD must use sh -c form."},
            {"role": "user",   "content": prompt},
        ],
        temperature=0.1,
    )

    fixed_content = response.choices[0].message.content.strip()
    if fixed_content.startswith("```"):
        lines = [l for l in fixed_content.splitlines() if not l.strip().startswith("```")]
        fixed_content = "\n".join(lines).strip()

    if not fixed_content.startswith("FROM"):
        print("[Test] ⚠️  LLM response doesn't look like a Dockerfile, skipping fix")
        return False

    if re.search(r'CMD \[.*?\$\{PORT', fixed_content, re.DOTALL):
        print("[Test] ⚠️  Post-fix: JSON array CMD with ${PORT} detected — forcing sh -c")
        fixed_content = re.sub(r'CMD \[.*?\]', correct_cmd, fixed_content, flags=re.DOTALL)

    with open(dockerfile_path, "w", encoding="utf-8") as f:
        f.write(fixed_content)

    print(f"[Test] ✅ Dockerfile updated by GPT-4o fix")
    print(f"[Test] Fixed Dockerfile:\n{fixed_content}\n")
    return True


def test_docker_image(folder, app_name, context, openai_api_key, max_retries=3):
    ml_type        = context.get("ml_type", "unknown")
    framework      = context.get("detected_framework", "unknown")
    lang           = context.get("detected_language", "unknown")
    test_port      = _find_free_port(detect_port_from_dockerfile(folder))
    image_tag      = f"{app_name}-test:latest"
    container_name = f"{app_name}-test-container"

    print(f"\n[Test] ══════════════════════════════════════════════════")
    print(f"[Test] Starting Docker image test for: {app_name}")
    print(f"[Test] Project type: {lang}/{framework or ml_type}")
    print(f"[Test] Test port: {test_port}")
    print(f"[Test] ══════════════════════════════════════════════════\n")

    dockerfile_path = os.path.join(folder, "Dockerfile")
    DB_DRIVERS = ["psycopg2", "asyncpg", "pymysql", "mysqlclient",
                  "pymongo", "motor", "redis", "aiomysql", "aiopg"]
    db_needed = any(
        kw in context.get("dep_file_requirements.txt", "").lower()
        for kw in DB_DRIVERS
    )
    if db_needed:
        print(f"[Test] ℹ️  Database dependency detected — switching to build-only test mode")
        print(f"[Test] ℹ️  Runtime skipped — app needs external DB (connects at deploy time via DATABASE_URL)")
        for attempt in range(1, max_retries + 1):
            print(f"\n[Test] ── Build attempt {attempt}/{max_retries} ──────────────────────")
            build_result = subprocess.run(
                ["docker", "build", "-t", image_tag, "."],
                cwd=folder, capture_output=True, text=True,
                encoding="utf-8", errors="replace",
            )
            if build_result.returncode == 0:
                print(f"[Test] ✅✅✅ DOCKER BUILD PASSED ✅✅✅")
                print(f"[Test] ℹ️  Dockerfile is correct — DB will connect when env vars are set at deploy time")
                subprocess.run(["docker", "rmi", "-f", image_tag], capture_output=True)
                return True
            else:
                print(f"[Test] ❌ Build FAILED on attempt {attempt}")
                print(f"[Test] Build error:\n{build_result.stderr[-2000:]}")
                if attempt < max_retries:
                    print(f"[Test] 🔧 Asking GPT-4o to fix the Dockerfile...")
                    fix_dockerfile_with_llm(
                        dockerfile_path, error_output=build_result.stderr,
                        error_type="build", context=context, openai_api_key=openai_api_key,
                    )
                else:
                    print(f"[Test] ❌ All {max_retries} build attempts failed")
                    return False

    error_log = []

    for attempt in range(1, max_retries + 1):
        print(f"\n[Test] ── Attempt {attempt}/{max_retries} ──────────────────────")
        print(f"[Test] Building image: {image_tag}")
        build_result = subprocess.run(
            ["docker", "build", "-t", image_tag, "."],
            cwd=folder, capture_output=True, text=True,
            encoding="utf-8", errors="replace",
        )

        if build_result.returncode != 0:
            print(f"[Test] ❌ Build FAILED on attempt {attempt}")
            print(f"[Test] Build error:\n{build_result.stderr[-3000:]}")
            error_log.append(f"Attempt {attempt} (build error): {build_result.stderr[-1000:]}")
            if attempt < max_retries:
                print(f"[Test] 🔧 Asking GPT-4o to fix the Dockerfile...")
                fixed = fix_dockerfile_with_llm(
                    dockerfile_path, error_output=build_result.stderr,
                    error_type="build", context=context, openai_api_key=openai_api_key,
                )
                if fixed:
                    print(f"[Test] ✅ Dockerfile updated, retrying build...")
                    continue
                else:
                    print(f"[Test] ❌ Could not auto-fix Dockerfile")
                    break
            else:
                print(f"[Test] ❌ All {max_retries} build attempts failed")
                break

        print(f"[Test] ✅ Image built successfully: {image_tag}")
        test_port = _find_free_port(detect_port_from_dockerfile(folder))
        print(f"[Test] Using port: {test_port}")

        if test_port is None:
            print(f"[Test] ℹ️  ML script project — no web server to test")
            print(f"[Test] ✅ Build passed — skipping runtime test")
            cleanup_test_container(container_name, image_tag)
            return True

        subprocess.run(["docker", "rm", "-f", container_name], capture_output=True)
        print(f"[Test] Starting container on port {test_port}...")
        run_result = subprocess.run(
            ["docker", "run", "-d", "--name", container_name,
             "-p", f"{test_port}:{test_port}", "-e", f"PORT={test_port}", image_tag],
            capture_output=True, text=True,
        )

        if run_result.returncode != 0:
            print(f"[Test] ❌ Container failed to start")
            logs = get_container_logs(container_name)
            print(f"[Test] Container logs:\n{logs}")
            error_log.append(f"Attempt {attempt} (runtime exit): {logs or run_result.stderr}")
            if attempt < max_retries:
                fix_dockerfile_with_llm(dockerfile_path, error_output=logs or run_result.stderr,
                                        error_type="runtime", context=context, openai_api_key=openai_api_key)
                cleanup_test_container(container_name, image_tag)
                continue
            else:
                cleanup_test_container(container_name, image_tag)
                break

        startup_wait = get_startup_wait(ml_type, framework)
        print(f"[Test] Waiting {startup_wait}s for app to start...")
        time.sleep(startup_wait)

        if container_name not in subprocess.run(
            ["docker", "ps", "--filter", f"name={container_name}", "--format", "{{.Names}}"],
            capture_output=True, text=True
        ).stdout:
            print(f"[Test] ❌ Container exited unexpectedly")
            logs = get_container_logs(container_name)
            print(f"[Test] Container logs:\n{logs}")
            error_log.append(f"Attempt {attempt} (runtime exit): {logs}")
            if attempt < max_retries:
                fix_dockerfile_with_llm(dockerfile_path, error_output=logs,
                                        error_type="runtime_exit", context=context, openai_api_key=openai_api_key)
                cleanup_test_container(container_name, image_tag)
                continue
            else:
                cleanup_test_container(container_name, image_tag)
                break

        print(f"[Test] Checking if app responds on http://localhost:{test_port} ...")
        health_ok = False
        for check_attempt in range(5):
            try:
                req = urllib.request.urlopen(f"http://localhost:{test_port}", timeout=10)
                print(f"[Test] ✅ HTTP {req.getcode()} — app is responding!")
                health_ok = True
                break
            except Exception as e:
                print(f"[Test] HTTP check {check_attempt+1}/5 failed: {e}")
                time.sleep(5)

        if health_ok:
            print(f"\n[Test] ✅✅✅ DOCKER TEST PASSED ✅✅✅")
            cleanup_test_container(container_name, image_tag)
            return True
        else:
            logs = get_container_logs(container_name)
            print(f"[Test] ❌ App not responding. Logs:\n{logs}")
            error_log.append(f"Attempt {attempt} (no response): container started but port never opened. Logs: {logs}")
            if attempt < max_retries:
                fix_dockerfile_with_llm(dockerfile_path, error_output=logs,
                                        error_type="no_response", context=context, openai_api_key=openai_api_key)
                cleanup_test_container(container_name, image_tag)
                continue
            else:
                cleanup_test_container(container_name, image_tag)
                break

    # ── Nuclear round ─────────────────────────────────────────────────────────
    print(f"\n[Test] ☢️  All {max_retries} patch attempts failed — nuclear regeneration")
    notes_path = os.path.join(folder, "_agent_notes.txt")
    nuclear_context = "PREVIOUS DOCKERFILE ATTEMPTS ALL FAILED — DO NOT REPEAT THESE MISTAKES:\n\n"
    for entry in error_log:
        nuclear_context += f"- {entry}\n"
    with open(notes_path, "w", encoding="utf-8") as f:
        f.write(nuclear_context)

    print(f"[Test] 🔄 Regenerating Dockerfile from scratch with full error history...")
    try:
        generate_dockerfile_with_openai(folder, openai_api_key)
    finally:
        if os.path.exists(notes_path):
            os.remove(notes_path)

    print(f"[Test] ── Nuclear build ──────────────────────────────────────")
    nuclear_build = subprocess.run(
        ["docker", "build", "-t", image_tag, "."],
        cwd=folder, capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )
    if nuclear_build.returncode != 0:
        print(f"[Test] ❌ Nuclear build failed:\n{nuclear_build.stderr[-2000:]}")
        return False

    print(f"[Test] ✅ Nuclear build succeeded")
    test_port = _find_free_port(detect_port_from_dockerfile(folder))
    if test_port is None:
        cleanup_test_container(container_name, image_tag)
        return True

    subprocess.run(["docker", "rm", "-f", container_name], capture_output=True)
    nuclear_run = subprocess.run(
        ["docker", "run", "-d", "--name", container_name,
         "-p", f"{test_port}:{test_port}", "-e", f"PORT={test_port}", image_tag],
        capture_output=True, text=True,
    )
    if nuclear_run.returncode != 0:
        print(f"[Test] ❌ Nuclear container failed to start")
        cleanup_test_container(container_name, image_tag)
        return False

    startup_wait = get_startup_wait(ml_type, framework)
    print(f"[Test] Waiting {startup_wait}s for app to start...")
    time.sleep(startup_wait)

    health_ok = False
    for check_attempt in range(5):
        try:
            req = urllib.request.urlopen(f"http://localhost:{test_port}", timeout=10)
            print(f"[Test] ✅ HTTP {req.getcode()} — nuclear round passed!")
            health_ok = True
            break
        except Exception as e:
            print(f"[Test] HTTP check {check_attempt+1}/5 failed: {e}")
            time.sleep(5)

    if health_ok:
        print(f"\n[Test] ✅✅✅ DOCKER TEST PASSED (nuclear round) ✅✅✅")
    else:
        print(f"[Test] ❌ Nuclear round also failed — giving up")
    cleanup_test_container(container_name, image_tag)
    return health_ok
