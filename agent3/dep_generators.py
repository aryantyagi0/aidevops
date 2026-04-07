import os
import ast
import re
from openai import OpenAI


# ── Shared LLM refiner ────────────────────────────────────────────────────────

def _refine_with_llm(dep_path, lang, pip_packages, context,
                     folder, openai_api_key, system_prompt, user_prompt):
    """Call GPT-4o to refine detected packages and write the dependency file."""
    print(f"[Agent] 🤖 Asking GPT-4o to generate {os.path.basename(dep_path)}...")

    entries  = context.get("entry_points_found", [])
    snippets = ""
    for e in entries[:3]:
        fpath = os.path.join(folder, e)
        if os.path.exists(fpath):
            try:
                with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                    snippets += f"\n--- {e} ---\n{f.read(2000)}\n"
            except Exception:
                pass

    full_prompt = user_prompt
    if snippets:
        full_prompt += f"\n\nProject file snippets:\n{snippets}"

    client   = OpenAI(api_key=openai_api_key)
    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": full_prompt},
        ],
        temperature=0.1,
    )

    content = response.choices[0].message.content.strip()
    if content.startswith("```"):
        lines   = [l for l in content.splitlines() if not l.strip().startswith("```")]
        content = "\n".join(lines).strip()

    with open(dep_path, "w") as f:
        f.write(content + "\n")

    print(f"\n[Agent] ✅ {os.path.basename(dep_path)} generated:")
    print(f"{'─'*40}")
    print(content)
    print(f"{'─'*40}")
    print(f"[Agent] ℹ️  Review it in VS Code and edit if needed before continuing\n")


# ── Top-level dispatcher ──────────────────────────────────────────────────────

def _ensure_requirements(folder: str, context: dict, openai_api_key: str):
    """Auto-generate dependency file if missing — works for all languages/frameworks."""
    lang = context.get("detected_language", "unknown")

    if lang in ("python", "unknown"):
        dep_file = "requirements.txt"
    elif lang == "nodejs":
        dep_file = "package.json"
    elif lang == "ruby":
        dep_file = "Gemfile"
    elif lang == "go":
        dep_file = "go.mod"
    elif lang == "rust":
        dep_file = "Cargo.toml"
    elif lang == "php":
        dep_file = "composer.json"
    elif lang == "java":
        dep_file = "pom.xml"
    elif lang in ("html", "static"):
        print(f"[Agent] ℹ️  Static HTML project — no dependency file needed")
        return
    else:
        print(f"[Agent] ℹ️  Language '{lang}' — skipping dependency check")
        return

    dep_path = os.path.join(folder, dep_file)

    if os.path.exists(dep_path):
        print(f"[Agent] ✅ {dep_file} already exists — skipping generation")
        return

    print(f"\n[Agent] ⚠️  No {dep_file} found for {lang} project")
    answer = input(f"[Agent] Auto-generate {dep_file}? (y/n): ").strip().lower()
    if answer not in ("y", "yes"):
        print(f"[Agent] ℹ️  Skipping {dep_file} generation")
        return

    if lang in ("python", "unknown"):
        _generate_python_requirements(folder, context, openai_api_key, dep_path)
    elif lang == "nodejs":
        _generate_package_json(folder, context, openai_api_key, dep_path)
    elif lang == "ruby":
        _generate_gemfile(folder, context, openai_api_key, dep_path)
    elif lang == "go":
        _generate_go_mod(folder, context, dep_path)
    elif lang == "rust":
        _generate_cargo_toml(folder, context, openai_api_key, dep_path)
    elif lang == "php":
        _generate_composer_json(folder, context, openai_api_key, dep_path)
    elif lang == "java":
        _generate_pom_xml(folder, context, openai_api_key, dep_path)


# ── Python ────────────────────────────────────────────────────────────────────

def _generate_python_requirements(folder, context, openai_api_key, dep_path):
    print(f"[Agent] 🔍 Scanning all .py files for imports...")

    stdlib_modules = {
        "os", "sys", "re", "json", "time", "datetime", "math", "random",
        "collections", "itertools", "functools", "pathlib", "shutil",
        "subprocess", "threading", "multiprocessing", "logging", "warnings",
        "typing", "abc", "io", "copy", "enum", "dataclasses", "contextlib",
        "hashlib", "hmac", "base64", "urllib", "http", "email", "html",
        "xml", "csv", "sqlite3", "pickle", "struct", "socket", "ssl",
        "uuid", "string", "textwrap", "traceback", "inspect", "ast",
        "unittest", "argparse", "configparser", "tempfile", "glob",
        "fnmatch", "stat", "platform", "gc", "weakref", "signal",
        "builtins", "types", "operator", "dis", "tokenize", "token",
        "importlib", "pkgutil", "site", "sysconfig", "distutils",
    }

    found_imports = set()

    for walk_root, walk_dirs, walk_files in os.walk(folder):
        walk_dirs[:] = [d for d in walk_dirs if d not in
                        (".git", "__pycache__", "venv", ".venv",
                         "node_modules", "_test_venv")]
        for fname in walk_files:
            if not fname.endswith(".py"):
                continue
            fpath = os.path.join(walk_root, fname)
            try:
                with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                    source = f.read()
                tree = ast.parse(source)
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        for alias in node.names:
                            found_imports.add(alias.name.split(".")[0])
                    elif isinstance(node, ast.ImportFrom):
                        if node.module:
                            found_imports.add(node.module.split(".")[0])
            except Exception:
                for match in re.finditer(
                    r'^(?:import|from)\s+([a-zA-Z_][a-zA-Z0-9_]*)',
                    source, re.MULTILINE
                ):
                    found_imports.add(match.group(1))

    third_party = sorted([
        imp for imp in found_imports
        if imp not in stdlib_modules
        and not imp.startswith("_")
        and imp != ""
    ])

    print(f"[Agent] 📦 Found imports: {third_party}")

    if not third_party:
        print(f"[Agent] ℹ️  No third-party imports found — skipping")
        return

    IMPORT_TO_PIP = {
        "cv2":             "opencv-python",
        "PIL":             "Pillow",
        "sklearn":         "scikit-learn",
        "bs4":             "beautifulsoup4",
        "yaml":            "PyYAML",
        "dotenv":          "python-dotenv",
        "googleapiclient": "google-api-python-client",
        "jwt":             "PyJWT",
        "dateutil":        "python-dateutil",
        "attr":            "attrs",
        "pkg_resources":   "setuptools",
        "magic":           "python-magic",
        "serial":          "pyserial",
        "Crypto":          "pycryptodome",
        "Image":           "Pillow",
        "telegram":        "python-telegram-bot",
        "discord":         "discord.py",
        "tweepy":          "tweepy",
        "instaloader":     "instaloader",
        "usaddress":       "usaddress",
        "gi":              "PyGObject",
        "wx":              "wxPython",
    }

    pip_packages = [IMPORT_TO_PIP.get(imp, imp) for imp in third_party]

    _refine_with_llm(
        dep_path=dep_path,
        lang="python",
        pip_packages=pip_packages,
        context=context,
        folder=folder,
        openai_api_key=openai_api_key,
        system_prompt=(
            "You are a Python packaging expert. Output ONLY a valid requirements.txt "
            "— one package per line with minimum versions. No comments, no markdown."
        ),
        user_prompt=f"""
Generate a requirements.txt for this Python project.

Detected imports (raw): {pip_packages}

Rules:
- Remove any stdlib modules that sneaked in
- Use correct pip package names (cv2 → opencv-python, PIL → Pillow, sklearn → scikit-learn etc)
- Add realistic minimum versions (e.g. streamlit>=1.28.0)
- Include framework itself if detected (streamlit, fastapi, flask etc)
- Output ONLY requirements.txt content, nothing else
""",
    )


# ── Node.js ───────────────────────────────────────────────────────────────────

def _generate_package_json(folder, context, openai_api_key, dep_path):
    print(f"[Agent] 🔍 Scanning .js/.ts files for require/import statements...")

    framework = context.get("detected_framework", "unknown")
    entries   = context.get("entry_points_found", [])
    found     = set()

    for walk_root, walk_dirs, walk_files in os.walk(folder):
        walk_dirs[:] = [d for d in walk_dirs if d not in
                        (".git", "node_modules", ".next", "dist", "build")]
        for fname in walk_files:
            if not any(fname.endswith(ext) for ext in (".js", ".ts", ".jsx", ".tsx", ".mjs")):
                continue
            fpath = os.path.join(walk_root, fname)
            try:
                with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()
                for match in re.finditer(
                    r"""(?:require\(['"]|from\s+['"])([^./'"@][^'"]*?)['"]""",
                    content
                ):
                    pkg = match.group(1).split("/")[0]
                    found.add(pkg)
            except Exception:
                pass

    print(f"[Agent] 📦 Found packages: {sorted(found)}")

    _refine_with_llm(
        dep_path=dep_path,
        lang="nodejs",
        pip_packages=sorted(found),
        context=context,
        folder=folder,
        openai_api_key=openai_api_key,
        system_prompt=(
            "You are a Node.js expert. Output ONLY valid package.json content. "
            "No markdown, no explanation."
        ),
        user_prompt=f"""
Generate a package.json for this Node.js project.

Framework detected: {framework}
Detected packages: {sorted(found)}
Entry points: {entries}

Rules:
- Include correct name, version, scripts (start, build, dev)
- Add all detected packages as dependencies with realistic versions
- Add framework-specific scripts (e.g. next dev, react-scripts start)
- Include engines.node if detectable
- Output ONLY valid package.json, nothing else
""",
    )


# ── Ruby ──────────────────────────────────────────────────────────────────────

def _generate_gemfile(folder, context, openai_api_key, dep_path):
    print(f"[Agent] 🔍 Scanning .rb files for require statements...")

    framework = context.get("detected_framework", "unknown")
    found     = set()

    for walk_root, walk_dirs, walk_files in os.walk(folder):
        walk_dirs[:] = [d for d in walk_dirs if d not in (".git", "vendor")]
        for fname in walk_files:
            if not fname.endswith(".rb"):
                continue
            fpath = os.path.join(walk_root, fname)
            try:
                with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()
                for match in re.finditer(r"""require\s+['"]([^'"./][^'"]*?)['"]""", content):
                    found.add(match.group(1))
            except Exception:
                pass

    print(f"[Agent] 📦 Found gems: {sorted(found)}")

    _refine_with_llm(
        dep_path=dep_path,
        lang="ruby",
        pip_packages=sorted(found),
        context=context,
        folder=folder,
        openai_api_key=openai_api_key,
        system_prompt=(
            "You are a Ruby expert. Output ONLY valid Gemfile content. "
            "No markdown, no explanation."
        ),
        user_prompt=f"""
Generate a Gemfile for this Ruby project.

Framework: {framework}
Detected requires: {sorted(found)}

Rules:
- Start with source 'https://rubygems.org'
- Include ruby version if detectable
- Add rails and all detected gems with realistic versions
- Output ONLY valid Gemfile content, nothing else
""",
    )


# ── Go ────────────────────────────────────────────────────────────────────────

def _generate_go_mod(folder, context, dep_path):
    print(f"[Agent] 🔍 Scanning .go files for imports...")

    found    = set()
    mod_name = os.path.basename(folder).lower().replace(" ", "-") or "myapp"

    for walk_root, walk_dirs, walk_files in os.walk(folder):
        walk_dirs[:] = [d for d in walk_dirs if d not in (".git", "vendor")]
        for fname in walk_files:
            if not fname.endswith(".go"):
                continue
            fpath = os.path.join(walk_root, fname)
            try:
                with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()
                for match in re.finditer(r'"([a-zA-Z][^"]*\.[^"]+/[^"]+)"', content):
                    pkg = match.group(1)
                    parts = pkg.split("/")
                    if "." in parts[0]:
                        found.add("/".join(parts[:3]))
            except Exception:
                pass

    print(f"[Agent] 📦 Found packages: {sorted(found)}")

    go_version = "1.21"
    content    = f"module {mod_name}\n\ngo {go_version}\n"

    if found:
        content += "\nrequire (\n"
        for pkg in sorted(found):
            content += f"\t{pkg} v0.0.0\n"
        content += ")\n"

    with open(dep_path, "w") as f:
        f.write(content)

    print(f"\n[Agent] ✅ go.mod generated:")
    print(f"{'─'*40}")
    print(content)
    print(f"{'─'*40}")
    print(f"[Agent] ⚠️  Run 'go mod tidy' to fix versions\n")


# ── Rust ──────────────────────────────────────────────────────────────────────

def _generate_cargo_toml(folder, context, openai_api_key, dep_path):
    print(f"[Agent] 🔍 Scanning .rs files for extern crate / use statements...")

    found    = set()
    app_name = os.path.basename(folder).lower().replace(" ", "-") or "myapp"

    for walk_root, walk_dirs, walk_files in os.walk(folder):
        walk_dirs[:] = [d for d in walk_dirs if d not in (".git", "target")]
        for fname in walk_files:
            if not fname.endswith(".rs"):
                continue
            fpath = os.path.join(walk_root, fname)
            try:
                with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()
                for match in re.finditer(r'extern crate ([a-zA-Z_][a-zA-Z0-9_]*)', content):
                    found.add(match.group(1))
                for match in re.finditer(r'^use ([a-zA-Z_][a-zA-Z0-9_]*)::', content, re.MULTILINE):
                    found.add(match.group(1))
            except Exception:
                pass

    RUST_STDLIB = {"std", "core", "alloc", "proc_macro", "test"}
    found = found - RUST_STDLIB

    print(f"[Agent] 📦 Found crates: {sorted(found)}")

    _refine_with_llm(
        dep_path=dep_path,
        lang="rust",
        pip_packages=sorted(found),
        context=context,
        folder=folder,
        openai_api_key=openai_api_key,
        system_prompt=(
            "You are a Rust expert. Output ONLY valid Cargo.toml content. "
            "No markdown, no explanation."
        ),
        user_prompt=f"""
Generate a Cargo.toml for this Rust project.

App name: {app_name}
Detected crates: {sorted(found)}

Rules:
- Include [package] with name, version = "0.1.0", edition = "2021"
- Add all detected crates as [dependencies] with realistic versions
- Output ONLY valid Cargo.toml content, nothing else
""",
    )


# ── PHP ───────────────────────────────────────────────────────────────────────

def _generate_composer_json(folder, context, openai_api_key, dep_path):
    print(f"[Agent] 🔍 Scanning .php files for use/require statements...")

    framework = context.get("detected_framework", "unknown")
    found     = set()

    for walk_root, walk_dirs, walk_files in os.walk(folder):
        walk_dirs[:] = [d for d in walk_dirs if d not in (".git", "vendor")]
        for fname in walk_files:
            if not fname.endswith(".php"):
                continue
            fpath = os.path.join(walk_root, fname)
            try:
                with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()
                for match in re.finditer(r'use\s+([A-Z][a-zA-Z\\]+)\\', content):
                    ns = match.group(1)
                    found.add(ns)
            except Exception:
                pass

    print(f"[Agent] 📦 Found namespaces: {sorted(found)}")

    _refine_with_llm(
        dep_path=dep_path,
        lang="php",
        pip_packages=sorted(found),
        context=context,
        folder=folder,
        openai_api_key=openai_api_key,
        system_prompt=(
            "You are a PHP/Composer expert. Output ONLY valid composer.json content. "
            "No markdown, no explanation."
        ),
        user_prompt=f"""
Generate a composer.json for this PHP project.

Framework: {framework}
Detected namespaces: {sorted(found)}

Rules:
- Include name, description, require with php version and detected packages
- Use correct packagist package names
- Add autoload psr-4 if applicable
- Output ONLY valid composer.json content, nothing else
""",
    )


# ── Java ──────────────────────────────────────────────────────────────────────

def _generate_pom_xml(folder, context, openai_api_key, dep_path):
    print(f"[Agent] 🔍 Scanning .java files for import statements...")

    framework = context.get("detected_framework", "unknown")
    found     = set()
    app_name  = os.path.basename(folder).lower().replace(" ", "-") or "myapp"

    for walk_root, walk_dirs, walk_files in os.walk(folder):
        walk_dirs[:] = [d for d in walk_dirs if d not in (".git", "target", ".gradle")]
        for fname in walk_files:
            if not fname.endswith(".java"):
                continue
            fpath = os.path.join(walk_root, fname)
            try:
                with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()
                for match in re.finditer(r'^import\s+([\w.]+);', content, re.MULTILINE):
                    pkg = match.group(1)
                    if not any(pkg.startswith(p) for p in ("java.", "javax.", "sun.", "com.sun.")):
                        found.add(pkg.split(".")[0] + "." + pkg.split(".")[1] if len(pkg.split(".")) > 1 else pkg)
            except Exception:
                pass

    print(f"[Agent] 📦 Found packages: {sorted(found)}")

    _refine_with_llm(
        dep_path=dep_path,
        lang="java",
        pip_packages=sorted(found),
        context=context,
        folder=folder,
        openai_api_key=openai_api_key,
        system_prompt=(
            "You are a Java/Maven expert. Output ONLY valid pom.xml content. "
            "No markdown, no explanation."
        ),
        user_prompt=f"""
Generate a pom.xml for this Java project.

App name: {app_name}
Framework: {framework}
Detected imports: {sorted(found)}

Rules:
- Include groupId, artifactId, version, packaging
- Add spring-boot-starter-parent if Spring detected
- Add all detected third-party dependencies with realistic versions
- Include maven-compiler-plugin with Java 17
- Output ONLY valid pom.xml content, nothing else
""",
    )
