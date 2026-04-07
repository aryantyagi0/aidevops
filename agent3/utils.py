import os
import shutil
import shutil as _shutil
import stat
import json
import subprocess

STATE_FILE = "_agent_state.json"


def _tool_ok(name):
    return _shutil.which(name) is not None


def setup_environment():
    if not _tool_ok("git"):
        raise EnvironmentError("git not found in PATH. Install Git before running the agent.")
    if not _tool_ok("docker"):
        print("[Agent] ⚠️  docker not found in PATH — Docker tests will fail.")
    subprocess.run(["git", "config", "--global", "user.name", "AI-Agent"])
    subprocess.run(["git", "config", "--global", "user.email", "ai-agent@example.com"])


def _remove_readonly(func, path, _):
    try:
        os.chmod(path, stat.S_IWRITE)
        func(path)
    except FileNotFoundError:
        pass


def safe_rmtree(path):
    shutil.rmtree(path, onerror=_remove_readonly)


def make_github_headers(token):
    return {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def save_state(data):
    with open(STATE_FILE, "w") as f:
        json.dump(data, f, indent=2)
    print(f"[Agent] 💾 State saved to {STATE_FILE}")


def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return None
