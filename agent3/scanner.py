import os
import shutil
import json
from openai import OpenAI


def deep_scan_repo(folder):
    print("[Agent] Deep scanning repository...")
    context = {}

    all_files = os.listdir(folder)
    context["all_files"] = all_files
    print(f"[Agent] Root files: {all_files}")

    JUNK_FILES = {
        ".git", ".github", ".gitignore", ".gitattributes",
        "README.md", "readme.md", "LICENSE", "license",
        ".DS_Store", "Thumbs.db", ".env.example",
    }
    ENTRY_SIGNALS = {
        "requirements.txt", "setup.py", "setup.cfg", "pyproject.toml",
        "Pipfile", "environment.yml", "environment.yaml",
        "app.py", "main.py", "server.py", "manage.py",
        "streamlit_app.py", "gradio_app.py", "run.py", "api.py",
        "package.json", "go.mod", "pom.xml", "build.gradle",
        "Gemfile", "Cargo.toml", "composer.json",
        "Dockerfile", "docker-compose.yml", "Makefile", "Procfile",
    }

    def find_project_root(base, max_depth=3):
        current = base
        for _ in range(max_depth):
            entries = os.listdir(current)
            real    = [e for e in entries if e not in JUNK_FILES]
            if any(e in ENTRY_SIGNALS for e in entries):
                return current
            if len(real) > 1:
                return current
            if len(real) == 1 and os.path.isdir(os.path.join(current, real[0])):
                print(f"[Agent] 📦 Diving into subfolder: {real[0]}/")
                current = os.path.join(current, real[0])
                continue
            break
        return current

    project_root = find_project_root(folder)

    if project_root != folder:
        print(f"[Agent] 📦 Project root detected at: {project_root}")
        print(f"[Agent] Promoting files to repo root...")
        for item in os.listdir(project_root):
            src = os.path.join(project_root, item)
            dst = os.path.join(folder, item)
            if os.path.exists(dst):
                print(f"[Agent] ⚠️  Skipping (exists at root): {item}")
                continue
            try:
                if os.path.isdir(src):
                    shutil.copytree(src, dst)
                else:
                    shutil.copy2(src, dst)
            except Exception as e:
                print(f"[Agent] ⚠️  Could not promote {item}: {e}")
        print(f"[Agent] ✅ Promotion done")
        all_files = os.listdir(folder)
        context["all_files"] = all_files
        print(f"[Agent] Root files after promotion: {all_files}")
    else:
        print(f"[Agent] ✅ Project already at root — no promotion needed")

    for subdir in ["src", "public", "app", "pages", "components",
                   "models", "notebooks", "data", "scripts", "api",
                   "lib", "utils", "training", "inference", "pipeline"]:
        path = os.path.join(folder, subdir)
        if os.path.isdir(path):
            context[f"subdir_{subdir}"] = os.listdir(path)

    dep_files = [
        "requirements.txt", "Pipfile", "pyproject.toml", "setup.py", "setup.cfg",
        "environment.yml", "environment.yaml", "conda.yml",
        "package.json", "yarn.lock", "package-lock.json",
        "pom.xml", "build.gradle", "go.mod", "Gemfile",
        "composer.json", "Cargo.toml",
        "runtime.txt", ".python-version", ".nvmrc",
        "vite.config.js", "vite.config.ts",
        "next.config.js", "next.config.ts",
        "nuxt.config.js", "angular.json",
        "Makefile", "config.yaml", "config.yml",
        "params.yaml", "dvc.yaml", "MLproject", "bentofile.yaml",
    ]
    for fname in dep_files:
        path = os.path.join(folder, fname)
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                context[f"dep_file_{fname}"] = f.read(5000)
            print(f"[Agent] Read: {fname}")

    print("[Agent] 🔍 Content-scanning all .py files for framework signals...")

    UI_SIGNALS = {
        "import streamlit": 100, "from streamlit": 100,
        "st.title": 90, "st.write": 80, "st.sidebar": 80,
        "st.button": 80, "st.selectbox": 80, "st.text_input": 80,
        "st.chat_message": 90, "st.chat_input": 90,
        "import gradio": 100, "from gradio": 100,
        "gr.interface": 90, "gr.blocks": 90, "gr.chatinterface": 90,
        "from fastapi": 90, "import fastapi": 90,
        "fastapi()": 95, "@app.get": 80, "@app.post": 80, "@router.get": 80,
        "from flask import": 90, "flask(__name__)": 95, "@app.route": 85,
        "import django": 85, "from django": 85,
        "uvicorn": 70, "starlette": 70,
    }
    BACKEND_PENALTIES = {
        "train.py": -60, "predict.py": -50, "inference.py": -50,
        "score.py": -50, "utils.py": -40, "helpers.py": -40,
        "config.py": -30, "settings.py": -30,
    }

    scored_files = {}

    for walk_root, walk_dirs, walk_files in os.walk(folder):
        walk_dirs[:] = [d for d in walk_dirs if d not in
                        (".git", "__pycache__", "venv", ".venv",
                         "node_modules", ".mypy_cache", "site-packages")]
        for fname in walk_files:
            if not fname.endswith(".py"):
                continue
            if any(fname.startswith(p) for p in ["test_", "__init__"]):
                continue
            fpath = os.path.join(walk_root, fname)
            try:
                with open(fpath, "r", encoding="utf-8", errors="ignore") as fh:
                    fcontent = fh.read(3000)
            except Exception:
                continue
            flower = fcontent.lower()
            score = sum(pts for sig, pts in UI_SIGNALS.items() if sig.lower() in flower)
            score += BACKEND_PENALTIES.get(fname, 0)
            if "if __name__" in fcontent:
                score += 5
            rel = os.path.relpath(fpath, folder).replace("\\", "/")
            scored_files[rel] = (score, fcontent, rel)

    for fname in ["index.js", "server.js", "app.js", "index.ts", "index.html"]:
        fpath = os.path.join(folder, fname)
        if os.path.exists(fpath):
            with open(fpath, "r", encoding="utf-8", errors="ignore") as fh:
                scored_files[fname] = (50, fh.read(3000), fname)

    for rel_key, (score, fcontent, rel) in scored_files.items():
        basename = os.path.basename(rel_key)
        context[f"entrypoint_{basename}"] = fcontent
        context[f"entrypoint_{rel_key}"] = fcontent

    ENTRY_PRIORITY = {
        "main.py": 200, "app.py": 190, "server.py": 180,
        "run.py": 170, "api.py": 160, "wsgi.py": 150, "asgi.py": 150,
        "manage.py": 140, "streamlit_app.py": 200, "gradio_app.py": 200,
    }
    boosted = {}
    for rel_key, (score, content, rel) in scored_files.items():
        basename      = os.path.basename(rel_key)
        boost         = ENTRY_PRIORITY.get(basename, 0)
        depth_penalty = rel_key.count("/") * 10
        boosted[rel_key] = (score + boost - depth_penalty, content, rel)

    sorted_files = sorted(boosted.items(), key=lambda x: x[1][0], reverse=True)
    print(f"[Agent] 📊 File scores (with boost): { {f: s for f, (s,_,_) in sorted_files[:8]} }")

    entry_points_found = []
    ui_candidates = [rel_key for rel_key, (score, _, _) in sorted_files if score > 0]

    if ui_candidates:
        entry_points_found = ui_candidates
        print(f"[Agent] ✅ Content-detected entry points: {entry_points_found}")
    else:
        print("[Agent] ⚠️  No UI signals found — falling back to filename matching")
        for target in ["app.py", "main.py", "server.py", "run.py", "api.py",
                       "manage.py", "wsgi.py", "asgi.py"]:
            match = next((k for k in scored_files if os.path.basename(k) == target), None)
            if match:
                entry_points_found.append(match)
        if not entry_points_found and scored_files:
            entry_points_found = [list(scored_files.keys())[0]]
            print(f"[Agent] 📄 Last resort entry point: {entry_points_found}")

    if not entry_points_found:
        print("[Agent] ⚠️  No standard entry point found — using LLM to detect...")
        extension_map = {
            ".py": "Python", ".js": "JavaScript", ".ts": "TypeScript",
            ".rb": "Ruby", ".go": "Go", ".java": "Java",
            ".php": "PHP", ".rs": "Rust", ".sh": "Shell",
        }
        special_files  = ["Makefile", "Procfile"]
        all_relevant   = []
        for f in all_files:
            ext = os.path.splitext(f)[1].lower()
            skip_prefixes = ["test_", "conf", "setup", "config", "__init__"]
            if ext in extension_map and not any(f.startswith(s) for s in skip_prefixes):
                all_relevant.append((f, extension_map[ext]))
            elif f in special_files:
                all_relevant.append((f, "Special"))

        file_snippets = {}
        for fname, lang_label in all_relevant[:20]:
            fpath = os.path.join(folder, fname)
            try:
                with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                    file_snippets[fname] = (lang_label, f.read(500))
            except Exception:
                pass

        if file_snippets:
            snippet_text = "\n\n".join(
                f"--- {fname} ({lang_label}) ---\n{content}"
                for fname, (lang_label, content) in file_snippets.items()
            )
            client   = OpenAI(api_key=os.getenv("OPENAI_API_KEY", ""))
            response = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": f"""
Given these files from a repository, identify which ONE file is the main entry point.
Files:\n{snippet_text}
Return ONLY the filename, nothing else.
"""}],
                temperature=0,
            )
            detected_entry = response.choices[0].message.content.strip()
            all_filenames  = [f for f, _ in all_relevant]
            if detected_entry in all_filenames:
                fpath = os.path.join(folder, detected_entry)
                try:
                    with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                        content = f.read(3000)
                    context[f"entrypoint_{detected_entry}"] = content
                    entry_points_found.append(detected_entry)
                    print(f"[Agent] 🤖 LLM detected entry point: {detected_entry}")
                except Exception:
                    pass
            else:
                fallback = next((f for f, _ in all_relevant if f != "__init__.py"), None)
                if fallback:
                    entry_points_found.append(fallback)
                    print(f"[Agent] 📄 Fallback entry point: {fallback}")

    context["entry_points_found"] = entry_points_found
    print(f"[Agent] Entry points: {entry_points_found}")

    notebooks   = [f for f in all_files if f.endswith(".ipynb")]
    context["notebooks_found"] = notebooks

    model_files = [f for f in all_files if any(
        f.endswith(ext) for ext in [
            ".pkl", ".joblib", ".h5", ".keras", ".pt", ".pth",
            ".onnx", ".pb", ".bin", ".safetensors", ".ckpt", ".model"
        ]
    )]
    context["model_files_found"] = model_files

    detected_lang      = "unknown"
    detected_framework = "unknown"
    app_variable       = "app"
    python_version     = "3.11"
    node_version       = "18"
    is_frontend        = False
    frontend_type      = "unknown"
    is_ml              = False
    ml_type            = "unknown"
    ml_frameworks      = []
    uses_conda         = False
    uses_gpu           = False
    build_output_dir   = "dist"

    if (any(f.endswith(".py") for f in all_files)
            or "requirements.txt" in all_files
            or "environment.yml" in all_files
            or "environment.yaml" in all_files):
        detected_lang = "python"

    if "environment.yml" in all_files or "environment.yaml" in all_files:
        uses_conda = True

    all_content = ""
    for key, val in context.items():
        if key.startswith("dep_file_") or key.startswith("entrypoint_"):
            all_content += val.lower() + "\n"

    ml_lib_map = {
        "tensorflow":  ["tensorflow", "tf.", "keras"],
        "pytorch":     ["torch", "torchvision", "torchaudio"],
        "sklearn":     ["sklearn", "scikit-learn"],
        "xgboost":     ["xgboost", "xgb."],
        "lightgbm":    ["lightgbm", "lgbm"],
        "catboost":    ["catboost"],
        "huggingface": ["transformers", "huggingface", "datasets", "diffusers"],
        "langchain":   ["langchain"],
        "openai":      ["openai"],
        "anthropic":   ["anthropic"],
        "spacy":       ["spacy"],
        "nltk":        ["nltk"],
        "pandas":      ["pandas"],
        "numpy":       ["numpy"],
        "matplotlib":  ["matplotlib"],
        "seaborn":     ["seaborn"],
        "plotly":      ["plotly"],
        "mlflow":      ["mlflow"],
        "bentoml":     ["bentoml"],
        "fastai":      ["fastai"],
        "opencv":      ["cv2", "opencv"],
        "streamlit":   ["streamlit"],
        "gradio":      ["gradio"],
    }
    for lib, keywords in ml_lib_map.items():
        if any(kw in all_content for kw in keywords):
            ml_frameworks.append(lib)

    if ml_frameworks:
        is_ml = True

    gpu_keywords = ["cuda", "torch.cuda", "device('cuda')", "tensorflow-gpu", ".to('cuda')"]
    if any(kw in all_content for kw in gpu_keywords):
        uses_gpu = True

    if detected_lang == "python" and is_ml:
        if "streamlit" in ml_frameworks or any(
            "streamlit" in context.get(f"entrypoint_{os.path.basename(e)}", "").lower() for e in entry_points_found
        ):
            detected_framework = "streamlit"
            ml_type = "streamlit"
            for c in ["streamlit_app.py", "app.py", "dashboard.py", "dashbord.py", "demo.py", "main.py"]:
                match = next((e for e in entry_points_found if os.path.basename(e) == c), None)
                if match:
                    if "streamlit" in context.get(f"entrypoint_{c}", "").lower() or c == "streamlit_app.py":
                        context["streamlit_entry_file"] = match
                        break
            if "streamlit_entry_file" not in context:
                context["streamlit_entry_file"] = entry_points_found[0] if entry_points_found else "app.py"

        elif "gradio" in ml_frameworks or any(
            "gradio" in context.get(f"entrypoint_{os.path.basename(e)}", "").lower() for e in entry_points_found
        ):
            detected_framework = "gradio"
            ml_type = "gradio"
            for c in ["app.py", "demo.py", "gradio_app.py", "main.py", "interface.py"]:
                match = next((e for e in entry_points_found if os.path.basename(e) == c), None)
                if match:
                    context["gradio_entry_file"] = match
                    break
            if "gradio_entry_file" not in context:
                context["gradio_entry_file"] = entry_points_found[0] if entry_points_found else "app.py"

        elif notebooks and not entry_points_found:
            detected_framework = "jupyter"
            ml_type = "jupyter"

        elif any("fastapi" in context.get(f"entrypoint_{os.path.basename(e)}", "").lower() for e in entry_points_found):
            detected_framework = "fastapi_ml"
            ml_type = "fastapi_ml"
            for e in entry_points_found:
                if "fastapi" in context.get(f"entrypoint_{os.path.basename(e)}", "").lower():
                    for line in context.get(f"entrypoint_{os.path.basename(e)}", "").splitlines():
                        if "fastapi()" in line.lower() and "=" in line:
                            app_variable = line.split("=")[0].strip()
                    context["fastapi_entry_file"] = e
                    break

        elif any("flask" in context.get(f"entrypoint_{os.path.basename(e)}", "").lower() for e in entry_points_found):
            detected_framework = "flask_ml"
            ml_type = "flask_ml"
            for e in entry_points_found:
                if "flask" in context.get(f"entrypoint_{os.path.basename(e)}", "").lower():
                    context["flask_entry_file"] = e
                    break

        elif any(os.path.basename(e) in ["train.py","predict.py","inference.py","score.py","main.py"]
                 for e in entry_points_found):
            detected_framework = "ml_script"
            ml_type = "ml_script"
            context["ml_script_entry"] = next(
                (e for e in entry_points_found
                 if os.path.basename(e) in ["train.py","predict.py","inference.py","score.py","main.py"]),
                entry_points_found[0]
            )

        elif "mlflow" in ml_frameworks or "MLproject" in all_files:
            detected_framework = "mlflow"
            ml_type = "mlflow"

        elif "bentoml" in ml_frameworks or "bentofile.yaml" in all_files:
            detected_framework = "bentoml"
            ml_type = "bentoml"

    if detected_lang == "python" and detected_framework == "unknown":

        for priority_basename in ["main.py", "app.py", "server.py", "run.py", "api.py"]:
            priority_file = next(
                (k for k in boosted if os.path.basename(k) == priority_basename), None
            )
            if not priority_file:
                continue
            content = context.get(f"entrypoint_{priority_basename}", "").lower()
            if not content:
                continue
            if "fastapi" in content:
                detected_framework = "fastapi"
                context["fastapi_entry_file"] = priority_file
                for line in context.get(f"entrypoint_{priority_basename}", "").splitlines():
                    if "fastapi()" in line.lower() and "=" in line:
                        app_variable = line.split("=")[0].strip()
                        break
                print(f"[Agent] ✅ Detected FastAPI in {priority_file} directly")
                break
            elif "flask" in content:
                detected_framework = "flask"
                context["flask_entry_file"] = priority_file
                for line in context.get(f"entrypoint_{priority_basename}", "").splitlines():
                    if "flask(" in line.lower() and "=" in line:
                        app_variable = line.split("=")[0].strip()
                        break
                print(f"[Agent] ✅ Detected Flask in {priority_file} directly")
                break
            elif "django" in content or priority_basename == "manage.py":
                detected_framework = "django"
                print(f"[Agent] ✅ Detected Django in {priority_file} directly")
                break
            elif "uvicorn" in content or "starlette" in content:
                detected_framework = "fastapi"
                context["fastapi_entry_file"] = priority_file
                print(f"[Agent] ✅ Detected FastAPI (uvicorn) in {priority_file} directly")
                break

        if detected_framework == "unknown":
            for e in entry_points_found:
                basename = os.path.basename(e)
                content = context.get(f"entrypoint_{basename}", "").lower()
                if "fastapi" in content:
                    detected_framework = "fastapi"
                    for line in context.get(f"entrypoint_{basename}", "").splitlines():
                        if "fastapi()" in line.lower() and "=" in line:
                            app_variable = line.split("=")[0].strip()
                    context["fastapi_entry_file"] = e
                    break
                elif "flask" in content:
                    detected_framework = "flask"
                    for line in context.get(f"entrypoint_{basename}", "").splitlines():
                        if "flask(" in line.lower() and "=" in line:
                            app_variable = line.split("=")[0].strip()
                    context["flask_entry_file"] = e
                    break
                elif "django" in content or basename == "manage.py":
                    detected_framework = "django"
                    break
                elif "uvicorn" in content or "starlette" in content:
                    detected_framework = "fastapi"
                    context["fastapi_entry_file"] = e
                    break

    if detected_framework == "unknown" and entry_points_found:
        print("[Agent] ⚠️  Framework unknown — asking LLM to detect...")
        all_context_text = ""
        for e in entry_points_found:
            basename = os.path.basename(e)
            all_context_text += f"\n--- {e} ---\n{context.get(f'entrypoint_{basename}', '')}\n"
        for key, val in context.items():
            if key.startswith("dep_file_"):
                fname = key.replace("dep_file_", "")
                all_context_text += f"\n--- {fname} ---\n{val[:1000]}\n"

        client   = OpenAI(api_key=os.getenv("OPENAI_API_KEY", ""))
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": f"""
Analyze these files and detect the framework/type of this project.
Files:\n{all_context_text[:4000]}
Return ONLY valid JSON:
{{"framework":"fastapi/flask/streamlit/etc","language":"python/nodejs/go/etc","cmd":"exact start command","port":"8000"}}
"""}],
            temperature=0,
        )
        raw = response.choices[0].message.content.strip()
        if raw.startswith("```"):
            lines = [l for l in raw.splitlines() if not l.strip().startswith("```")]
            raw   = "\n".join(lines).strip()
        try:
            detected           = json.loads(raw)
            detected_framework = detected.get("framework", "unknown")
            if detected_lang == "unknown":
                detected_lang = detected.get("language", "unknown")
            context["llm_detected_cmd"]  = detected.get("cmd", "")
            context["llm_detected_port"] = detected.get("port", "8000")
            print(f"[Agent] 🤖 LLM detected: {detected_framework} | cmd: {context['llm_detected_cmd']}")
        except Exception as e:
            print(f"[Agent] ⚠️  Could not parse LLM framework response: {e}")

    if "package.json" in all_files:
        if detected_lang == "unknown":
            detected_lang = "nodejs"
        pkg_lower = context.get("dep_file_package.json", "").lower()
        if '"next"'            in pkg_lower: detected_framework="nextjs";  is_frontend=True; frontend_type="nextjs";  build_output_dir=".next"
        elif '"react"'         in pkg_lower: detected_framework="react";   is_frontend=True; frontend_type="react";   build_output_dir="build"
        elif '"vue"'           in pkg_lower:
            if '"nuxt"'        in pkg_lower: detected_framework="nuxt";    is_frontend=True; frontend_type="nuxt";    build_output_dir=".output"
            else:                            detected_framework="vue";     is_frontend=True; frontend_type="vue";     build_output_dir="dist"
        elif '"@angular/core"' in pkg_lower: detected_framework="angular"; is_frontend=True; frontend_type="angular"; build_output_dir="dist"
        elif '"svelte"'        in pkg_lower: detected_framework="svelte";  is_frontend=True; frontend_type="svelte";  build_output_dir="build"
        elif '"vite"'          in pkg_lower: detected_framework="vite";    is_frontend=True; frontend_type="vite";    build_output_dir="dist"
        elif '"express"'       in pkg_lower: detected_framework="express"
        elif '"fastify"'       in pkg_lower: detected_framework="fastify"
        for key in ["dep_file_.nvmrc", "dep_file_.node-version"]:
            val = context.get(key, "").strip()
            if val:
                node_version = val.replace("v", "").split(".")[0]

    if "pom.xml" in all_files or "build.gradle" in all_files: detected_lang = "java"
    if "go.mod"        in all_files: detected_lang = "go"
    if "Gemfile"       in all_files:
        detected_lang = "ruby"
        detected_framework = "rails" if "rails" in context.get("dep_file_Gemfile", "").lower() else "ruby"
    if "composer.json" in all_files: detected_lang = "php"
    if "Cargo.toml"    in all_files: detected_lang = "rust"
    if "index.html" in all_files and "package.json" not in all_files:
        if detected_lang in ("unknown", "javascript"):
            detected_lang      = "html"
            detected_framework = "static"
            is_frontend        = True
            frontend_type      = "static_html"

    if detected_lang == "python":
        for key in ["dep_file_runtime.txt", "dep_file_.python-version"]:
            val = context.get(key, "")
            for v in ["3.8", "3.9", "3.10", "3.11", "3.12"]:
                if v in val:
                    python_version = v; break
        if "dep_file_requirements.txt" not in context:
            context["missing_requirements_warning"] = (
                "No requirements.txt. Scan entry points for all imports and install them."
            )

    context.update({
        "detected_language":  detected_lang,
        "detected_framework": detected_framework,
        "app_variable_name":  app_variable,
        "python_version":     python_version,
        "node_version":       node_version,
        "is_frontend":        is_frontend,
        "frontend_type":      frontend_type,
        "is_ml":              is_ml,
        "ml_type":            ml_type,
        "ml_frameworks":      ml_frameworks,
        "uses_conda":         uses_conda,
        "uses_gpu":           uses_gpu,
        "build_output_dir":   build_output_dir,
    })

    print(f"[Agent] Lang={detected_lang} | Framework={detected_framework} | "
          f"ML={is_ml} | MLType={ml_type} | GPU={uses_gpu} | Conda={uses_conda}")
    return context
