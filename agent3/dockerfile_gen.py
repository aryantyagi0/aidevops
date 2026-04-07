import os
import re
from openai import OpenAI

from .scanner import deep_scan_repo


# ── Port helpers ──────────────────────────────────────────────────────────────

def detect_port_from_dockerfile(folder, fallback="8000"):
    """Read EXPOSE port from Dockerfile dynamically — works for any project."""
    dockerfile_path = os.path.join(folder, "Dockerfile")
    if not os.path.exists(dockerfile_path):
        return fallback
    with open(dockerfile_path, "r") as f:
        content = f.read()
    for line in content.splitlines():
        line = line.strip()
        if line.upper().startswith("EXPOSE"):
            parts = line.split()
            if len(parts) >= 2:
                port = parts[1].strip()
                match = re.search(r'\$\{PORT:-(\d+)\}', port)
                if match:
                    return match.group(1)
                elif port.isdigit():
                    return port
    return fallback


def get_test_port(ml_type, framework, folder=None):
    if folder:
        dockerfile_path = os.path.join(folder, "Dockerfile")
        if os.path.exists(dockerfile_path):
            with open(dockerfile_path, "r") as f:
                for line in f:
                    line = line.strip()
                    if line.upper().startswith("EXPOSE"):
                        parts = line.split()
                        if len(parts) >= 2:
                            port = parts[1].strip()
                            if "${PORT" in port:
                                match = re.search(r'\$\{PORT:-(\d+)\}', port)
                                if match:
                                    return match.group(1)
                            elif port.isdigit():
                                return port

    port_map = {
        "streamlit": "8501", "gradio": "7860", "jupyter": "8888",
        "fastapi_ml": "8000", "flask_ml": "5000", "ml_script": None,
        "fastapi": "8000", "flask": "5000", "django": "8000",
        "nextjs": "3000", "nuxt": "3000", "react": "80", "vue": "80",
        "angular": "80", "vite": "80", "svelte": "80",
        "static_html": "80", "static": "80", "none": "80",
        "express": "3000", "fastify": "3000",
    }
    key = ml_type if ml_type and ml_type != "unknown" else framework
    if key in ("unknown", "none", "", None):
        key = "static_html"
    return port_map.get(key, "8000")


def get_startup_wait(ml_type, framework):
    wait_map = {
        "streamlit": 15, "gradio": 15, "jupyter": 15,
        "fastapi_ml": 10, "flask_ml": 10, "huggingface": 30,
        "fastapi": 8, "flask": 8, "django": 8,
        "nextjs": 15, "react": 5, "vue": 5, "angular": 5,
    }
    key = ml_type if ml_type and ml_type != "unknown" else framework
    return wait_map.get(key, 10)


# ── Main Dockerfile generator ─────────────────────────────────────────────────

def generate_dockerfile_with_openai(folder, openai_api_key):
    context      = deep_scan_repo(folder)
    lang         = context["detected_language"]
    framework    = context["detected_framework"]
    app_var      = context["app_variable_name"]
    py_ver       = context["python_version"]
    node_ver     = context["node_version"]
    fe_type      = context["frontend_type"]
    is_ml        = context["is_ml"]
    ml_type      = context["ml_type"]
    ml_libs      = context["ml_frameworks"]
    uses_gpu     = context["uses_gpu"]
    entries      = context["entry_points_found"]
    entry        = entries[0] if entries else "app.py"
    entry_base   = os.path.basename(entry)
    entry_module = entry.replace("\\", "/").replace("/", ".").replace(".py", "")

    context_text = f"""
=== REPO ANALYSIS ===
All root files: {context['all_files']}
Language: {lang} | Framework: {framework}
ML project: {is_ml} | ML type: {ml_type} | ML libs: {ml_libs}
GPU: {uses_gpu} | Conda: {context['uses_conda']}
Frontend: {context['is_frontend']} | Frontend type: {fe_type}
Python version: {py_ver} | Node version: {node_ver}
Entry points: {entries}
Entry file (basename): {entry_base}
Entry module: {entry_module}
Model files: {context.get('model_files_found', [])}
Notebooks: {context.get('notebooks_found', [])}
"""
    for key, value in context.items():
        if key.startswith("dep_file_") or key.startswith("entrypoint_"):
            fname = key.replace("dep_file_", "").replace("entrypoint_", "")
            context_text += f"\n=== {fname} ===\n{value}\n"

    if "missing_requirements_warning" in context:
        context_text += f"\n⚠️  {context['missing_requirements_warning']}\n"

    notes_path = os.path.join(folder, "_agent_notes.txt")
    if os.path.exists(notes_path):
        with open(notes_path) as f:
            user_notes = f.read().strip()
        if user_notes:
            context_text += f"\n=== USER NOTES ===\n{user_notes}\n"
            print(f"[Agent] 📝 Using user notes in Dockerfile generation")

    if context.get("llm_detected_cmd"):
        context_text += f"\n=== LLM DETECTED ===\nCmd: {context['llm_detected_cmd']}\nPort: {context.get('llm_detected_port', '8000')}\n"

    gpu_base   = "nvidia/cuda:11.8.0-cudnn8-runtime-ubuntu22.04"
    base_image = gpu_base if uses_gpu else f"python:{py_ver}-slim"

    specific_instructions = ""

    if ml_type == "streamlit":
        e = context.get("streamlit_entry_file", entry_base)
        specific_instructions = f"""
PROJECT: Streamlit App
FROM {base_image}
WORKDIR /app
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
RUN apt-get update && apt-get install -y build-essential libgomp1 && rm -rf /var/lib/apt/lists/*
COPY requirements.txt* ./
RUN if [ -f requirements.txt ]; then pip install --no-cache-dir -r requirements.txt; fi
RUN pip install --no-cache-dir streamlit
COPY . .
EXPOSE 8501
CMD ["sh", "-c", "streamlit run {e} --server.port=${{PORT:-8501}} --server.address=0.0.0.0 --server.headless=true"]
ML libs: {ml_libs}
"""
    elif ml_type == "gradio":
        e = context.get("gradio_entry_file", entry_base)
        specific_instructions = f"""
PROJECT: Gradio App
FROM {base_image}
WORKDIR /app
ENV PYTHONUNBUFFERED=1
ENV GRADIO_SERVER_NAME=0.0.0.0
RUN apt-get update && apt-get install -y build-essential libgomp1 && rm -rf /var/lib/apt/lists/*
COPY requirements.txt* ./
RUN if [ -f requirements.txt ]; then pip install --no-cache-dir -r requirements.txt; fi
RUN pip install --no-cache-dir gradio
COPY . .
EXPOSE 7860
CMD ["sh", "-c", "python {e}"]
ML libs: {ml_libs}
"""
    elif ml_type == "jupyter":
        specific_instructions = f"""
PROJECT: Jupyter Notebook Server
FROM python:{py_ver}-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1
RUN apt-get update && apt-get install -y build-essential && rm -rf /var/lib/apt/lists/*
COPY requirements.txt* ./
RUN pip install --no-cache-dir jupyter notebook jupyterlab
RUN if [ -f requirements.txt ]; then pip install --no-cache-dir -r requirements.txt; fi
COPY . .
EXPOSE 8888
CMD ["sh", "-c", "jupyter notebook --ip=0.0.0.0 --port=${{PORT:-8888}} --no-browser --allow-root --NotebookApp.token=''"]
"""
    elif ml_type in ("fastapi_ml", "flask_ml"):
        e     = context.get("fastapi_entry_file") or context.get("flask_entry_file", entry)
        mod   = e.replace("\\", "/").replace("/", ".").replace(".py", "")
        is_fa = ml_type == "fastapi_ml"
        cmd   = (f"uvicorn {mod}:{app_var} --host 0.0.0.0 --port ${{PORT:-8000}}"
                 if is_fa else "flask run --host=0.0.0.0 --port=${PORT:-5000}")
        specific_instructions = f"""
PROJECT: {'FastAPI' if is_fa else 'Flask'} ML API
FROM {base_image}
WORKDIR /app
ENV PYTHONUNBUFFERED=1
RUN apt-get update && apt-get install -y build-essential libgomp1 && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
CMD ["sh", "-c", "{cmd}"]
ML libs: {ml_libs}
"""
    elif ml_type == "ml_script":
        e = context.get("ml_script_entry", entry_base)
        specific_instructions = f"""
PROJECT: Plain ML Script
FROM {base_image}
WORKDIR /app
ENV PYTHONUNBUFFERED=1
RUN apt-get update && apt-get install -y build-essential libgomp1 && rm -rf /var/lib/apt/lists/*
COPY requirements.txt* ./
RUN if [ -f requirements.txt ]; then pip install --no-cache-dir -r requirements.txt; fi
COPY . .
CMD ["python", "{e}"]
ML libs: {ml_libs}
"""
    elif framework == "fastapi":
        e   = context.get("fastapi_entry_file", entry)
        mod = e.replace("\\", "/").replace("/", ".").replace(".py", "")
        specific_instructions = f"""
PROJECT: FastAPI
FROM python:{py_ver}-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
COPY requirements.txt* ./
RUN if [ -f requirements.txt ]; then pip install --no-cache-dir -r requirements.txt; fi
COPY . .
CMD ["sh", "-c", "uvicorn {mod}:{app_var} --host 0.0.0.0 --port ${{PORT:-8000}}"]
"""
    elif framework == "flask":
        e = context.get("flask_entry_file", entry)
        specific_instructions = f"""
PROJECT: Flask
FROM python:{py_ver}-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1
ENV FLASK_APP={e}
COPY requirements.txt* ./
RUN if [ -f requirements.txt ]; then pip install --no-cache-dir -r requirements.txt; fi
COPY . .
CMD ["sh", "-c", "flask run --host=0.0.0.0 --port=${{PORT:-5000}}"]
"""
    elif framework == "django":
        specific_instructions = f"""
PROJECT: Django
FROM python:{py_ver}-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1
COPY requirements.txt* ./
RUN if [ -f requirements.txt ]; then pip install --no-cache-dir -r requirements.txt; fi
COPY . .
CMD ["sh", "-c", "python manage.py runserver 0.0.0.0:${{PORT:-8000}}"]
"""
    elif fe_type in ("react", "vue", "angular", "vite", "svelte"):
        specific_instructions = f"""
PROJECT: {fe_type.title()} SPA
Multi-stage: node:{node_ver}-alpine builder + nginx:alpine
RUN npm ci && npm run build
COPY build output to /usr/share/nginx/html
CMD ["nginx", "-g", "daemon off;"]
Use try_files $uri /index.html for SPA routing
"""
    elif fe_type == "nextjs":
        specific_instructions = f"""
PROJECT: Next.js
Multi-stage: node:{node_ver}-alpine builder + runner
RUN npm ci && npm run build
CMD ["sh", "-c", "npm start -- --port ${{PORT:-3000}}"]
"""
    elif fe_type == "static_html":
        specific_instructions = """
PROJECT: Static HTML
FROM nginx:alpine
COPY . /usr/share/nginx/html
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
"""
    elif lang == "java":
        specific_instructions = """
PROJECT: Java
Multi-stage: maven:3.9-eclipse-temurin-17 + eclipse-temurin:17-jre-slim
RUN mvn package -DskipTests
CMD ["java", "-jar", "target/app.jar"]
"""
    elif lang == "go":
        specific_instructions = """
PROJECT: Go
Multi-stage: golang:1.21-alpine + alpine:3.18
RUN go build -o main .
CMD ["./main"]
"""
    elif lang == "ruby":
        specific_instructions = f"""
PROJECT: Ruby {'Rails' if framework == 'rails' else ''}
FROM ruby:3.2-slim
RUN bundle install
CMD rails server or ruby {entry_base} with $PORT
"""
    else:
        cmd  = context.get("llm_detected_cmd", f"python {entry_base}")
        port = context.get("llm_detected_port", "8000")
        specific_instructions = f"""
PROJECT: Custom ({framework or 'unknown'}) — Language: {lang}
FROM python:{py_ver}-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
COPY requirements.txt* ./
RUN if [ -f requirements.txt ]; then pip install --no-cache-dir -r requirements.txt; fi
COPY . .
EXPOSE {port}
CMD ["sh", "-c", "{cmd}"]
NOTE: Use ${{PORT:-{port}}} pattern. Entry file is {entry_base}.
"""
        print(f"[Agent] 🤖 Using cmd: {cmd} on port {port}")

    prompt = f"""You are a Docker expert. Generate a production-ready Dockerfile.

{context_text}

INSTRUCTIONS:
{specific_instructions}

CRITICAL RULES:
1. NEVER hardcode ports — always use ${{PORT:-DEFAULT}}
2. Python: always set ENV PYTHONUNBUFFERED=1 and PYTHONDONTWRITEBYTECODE=1
3. Install ALL dependencies BEFORE COPY . . (layer caching)
4. The entry module is: {entry_module} — CMD must use this exact module path for uvicorn
5. WORKDIR must be /app — all files are copied there via COPY . .
6. Streamlit: --server.address=0.0.0.0 --server.headless=true --server.port=${{PORT:-8501}}
7. Gradio: ENV GRADIO_SERVER_NAME=0.0.0.0
8. XGBoost/LightGBM: apt-get install -y libgomp1
9. OpenCV: apt-get install -y libgl1-mesa-glx libglib2.0-0
10. HuggingFace: use python:3.11-slim not alpine
11. No requirements.txt: scan imports from entry file and RUN pip install each one
12. Output ONLY the raw Dockerfile — no markdown, no backticks, no explanation
"""

    client   = OpenAI(api_key=openai_api_key)
    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": f"Output ONLY raw Dockerfile. No markdown. No backticks. Entry module is {entry_module}. Always use ${{PORT:-DEFAULT}}."},
            {"role": "user",   "content": prompt}
        ],
        temperature=0.1,
    )

    dockerfile_content = response.choices[0].message.content.strip()

    if dockerfile_content.startswith("```"):
        lines = [l for l in dockerfile_content.splitlines() if not l.strip().startswith("```")]
        dockerfile_content = "\n".join(lines).strip()

    for port in ["8000", "8080", "5000", "3000", "8501", "7860", "8888"]:
        if f"--port {port}" in dockerfile_content and "${PORT" not in dockerfile_content:
            dockerfile_content = dockerfile_content.replace(
                f"--port {port}", f"--port ${{PORT:-{port}}}")

    if lang == "python" and "PYTHONUNBUFFERED" not in dockerfile_content:
        dockerfile_content = dockerfile_content.replace(
            "WORKDIR /app",
            "WORKDIR /app\n\nENV PYTHONUNBUFFERED=1\nENV PYTHONDONTWRITEBYTECODE=1")

    if ml_type == "streamlit":
        if "--server.headless" not in dockerfile_content:
            dockerfile_content = dockerfile_content.replace(
                "streamlit run", "streamlit run --server.headless=true")
        if "--server.address" not in dockerfile_content:
            dockerfile_content = dockerfile_content.replace(
                "streamlit run", "streamlit run --server.address=0.0.0.0")

    path = os.path.join(folder, "Dockerfile")
    with open(path, "w", encoding="utf-8") as f:
        f.write(dockerfile_content)

    print(f"\n[Agent] ── Dockerfile ({lang}/{framework or ml_type}) ──")
    print(dockerfile_content)
    print("[Agent] ──────────────────────────────────────────────────\n")

    # ── Auto-generate docker-compose.yml for DB-dependent apps ────────
    DB_DRIVERS = ["psycopg2", "asyncpg", "pymysql", "mysqlclient",
                  "pymongo", "motor", "aiomysql", "aiopg"]
    req_content = context.get("dep_file_requirements.txt", "").lower()
    db_needed   = any(kw in req_content for kw in DB_DRIVERS)

    if db_needed:
        if any(kw in req_content for kw in ["psycopg2", "asyncpg", "aiopg"]):
            db_type = "postgres"
        elif any(kw in req_content for kw in ["pymysql", "mysqlclient", "aiomysql"]):
            db_type = "mysql"
        elif "pymongo" in req_content or "motor" in req_content:
            db_type = "mongo"
        else:
            db_type = "postgres"

        env_file = os.path.join(folder, ".env")
        env_vars = {}
        if os.path.exists(env_file):
            with open(env_file) as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, _, v = line.partition("=")
                    env_vars[k.strip()] = v.strip().strip('"').strip("'")

        existing_url = env_vars.get("DATABASE_URL", "")
        if existing_url:
            try:
                match = re.match(
                    r"(?:postgresql|mysql|mongodb)(?:\+\w+)?://([^:]+):([^@]+)@[^:]+:\d+/(\w+)",
                    existing_url
                )
                if match:
                    parsed_user = match.group(1)
                    parsed_pass = match.group(2)
                    parsed_db   = match.group(3)
                    env_vars.setdefault("POSTGRES_USER",     parsed_user)
                    env_vars.setdefault("POSTGRES_PASSWORD", parsed_pass)
                    env_vars.setdefault("POSTGRES_DB",       parsed_db)
                    env_vars.setdefault("MYSQL_ROOT_PASSWORD", parsed_pass)
                    env_vars.setdefault("MYSQL_DATABASE",      parsed_db)
                    print(f"[Agent] 🔑 Parsed credentials from DATABASE_URL")
            except Exception:
                pass

        print(f"\n[Agent] 🗄️  Database detected ({db_type}) — collecting credentials for docker-compose...")
        print(f"[Agent] ℹ️  Docker will auto-create the DB — no local install needed!")
        print(f"[Agent] ℹ️  Just press Enter to accept defaults.\n")

        def ask(label, env_key, default):
            if env_key and env_key in env_vars:
                print(f"  ✅ {label}: loaded from .env ({env_vars[env_key]})")
                return env_vars[env_key]
            val = input(f"  {label} [{default}]: ").strip()
            return val if val else default

        if db_type == "postgres":
            db_user    = ask("Postgres user",     "POSTGRES_USER",     "postgres")
            db_pass    = ask("Postgres password", "POSTGRES_PASSWORD", "password")
            db_name    = ask("Postgres DB name",  "POSTGRES_DB",       "app")
            db_service = f"""  db:
    image: postgres:15
    restart: always
    environment:
      POSTGRES_USER: {db_user}
      POSTGRES_PASSWORD: {db_pass}
      POSTGRES_DB: {db_name}
    ports:
      - "5432:5432"
    volumes:
      - postgres_data:/var/lib/postgresql/data"""
            db_url  = f"postgresql://{db_user}:{db_pass}@db:5432/{db_name}"
            volumes = "\nvolumes:\n  postgres_data:"

        elif db_type == "mysql":
            db_pass    = ask("MySQL root password", "MYSQL_ROOT_PASSWORD", "password")
            db_name    = ask("MySQL DB name",       "MYSQL_DATABASE",      "app")
            db_service = f"""  db:
    image: mysql:8
    restart: always
    environment:
      MYSQL_ROOT_PASSWORD: {db_pass}
      MYSQL_DATABASE: {db_name}
    ports:
      - "3306:3306"
    volumes:
      - mysql_data:/var/lib/mysql"""
            db_url  = f"mysql+pymysql://root:{db_pass}@db:3306/{db_name}"
            volumes = "\nvolumes:\n  mysql_data:"

        elif db_type == "mongo":
            db_name    = ask("MongoDB DB name", "MONGO_DB", "app")
            db_service = f"""  db:
    image: mongo:6
    restart: always
    ports:
      - "27017:27017"
    volumes:
      - mongo_data:/data/db"""
            db_url  = f"mongodb://db:27017/{db_name}"
            volumes = "\nvolumes:\n  mongo_data:"

        existing_db_url  = env_vars.get("DATABASE_URL", "")
        is_localhost_url = any(h in existing_db_url for h in
                               ["localhost", "127.0.0.1", "0.0.0.0"])

        if existing_db_url and not is_localhost_url:
            final_db_url = existing_db_url
            print(f"\n[Agent] ℹ️  Using hosted DATABASE_URL from .env")
        else:
            final_db_url = db_url
            if existing_db_url and is_localhost_url:
                print(f"\n[Agent] ℹ️  .env has localhost DATABASE_URL — replacing with docker-compose internal URL")

        app_port = detect_port_from_dockerfile(folder, fallback="8000")

        compose_content = f"""version: "3.8"
services:
{db_service}

  app:
    build: .
    ports:
      - "{app_port}:{app_port}"
    environment:
      PORT: "{app_port}"
      DATABASE_URL: "{final_db_url}"
    depends_on:
      - db
    restart: always
{volumes}
"""
        compose_path = os.path.join(folder, "docker-compose.yml")
        with open(compose_path, "w", encoding="utf-8") as f:
            f.write(compose_content)

        print(f"\n[Agent] ✅ docker-compose.yml generated ({db_type} + app)")
        print(f"[Agent] ℹ️  Run locally with: docker-compose up --build")
        print(f"[Agent] ℹ️  App will be at:   http://localhost:{app_port}")
        print(f"[Agent] ℹ️  DB URL in compose: {final_db_url}\n")

    return dockerfile_content, context
