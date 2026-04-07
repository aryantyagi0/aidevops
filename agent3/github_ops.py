import os
import subprocess
import time
import requests

from .utils import make_github_headers, safe_rmtree


def get_authenticated_user(token):
    me = requests.get("https://api.github.com/user", headers=make_github_headers(token))
    if me.status_code != 200:
        raise RuntimeError(f"Invalid GitHub token. Status: {me.status_code}")
    login = me.json()["login"]
    print(f"[Agent] Authenticated as: {login}")
    return login


def get_default_branch(repo_url, token):
    repo = repo_url.replace("https://github.com/", "").rstrip("/")
    r = requests.get(f"https://api.github.com/repos/{repo}", headers=make_github_headers(token))
    if r.status_code != 200:
        raise RuntimeError("Failed to get repository info")
    branch = r.json()["default_branch"]
    print(f"[Agent] Default branch: {branch}")
    return branch


def fork_repo(repo_url, token):
    print("[Agent] Forking repository...")
    repo = repo_url.replace("https://github.com/", "").rstrip("/")
    url = f"https://api.github.com/repos/{repo}/forks"
    try:
        r = requests.post(url, headers=make_github_headers(token), timeout=15)
    except Exception as e:
        raise RuntimeError(f"Network error while forking: {e}")

    print("STATUS:", r.status_code)
    print("RESPONSE:", r.text)

    if r.status_code == 403 and "already exists" in r.text.lower():
        print("[Agent] Repo already forked. Using existing fork...")
        login = get_authenticated_user(token)
        return f"https://github.com/{login}/{repo.split('/')[-1]}.git"

    if r.status_code not in (200, 202):
        raise RuntimeError(f"Forking failed: {r.text}")

    data     = r.json()
    fork_url = data.get("clone_url")

    original_owner      = repo.split("/")[0].lower()
    fork_owner_returned = data.get("owner", {}).get("login", "").lower()
    if fork_owner_returned == original_owner:
        print(f"[Agent] ℹ️  You own this repo — working directly on original (no fork needed)")

    print("[Agent] Fork requested:", fork_url)

    for attempt in range(20):
        try:
            check = requests.get(data["url"], headers=make_github_headers(token), timeout=10)
            if check.status_code == 200:
                print(f"[Agent] Fork ready (after {attempt + 1} checks)")
                break
        except Exception as e:
            print("Retrying check...", e)
        time.sleep(3)
    else:
        raise RuntimeError("Fork did not become ready in time")

    return fork_url


def download_repo(repo_url, fork_url, default_branch):
    print(f"[Agent] Cloning latest upstream default branch: {default_branch}")
    repo_name = repo_url.split("/")[-1].replace(".git", "")
    WORKSPACE = os.path.join(os.path.expanduser("~"), "ai-devops-workspace")
    os.makedirs(WORKSPACE, exist_ok=True)
    repo_path = os.path.join(WORKSPACE, repo_name)
    if os.path.exists(repo_path):
        safe_rmtree(repo_path)
    subprocess.run(
        ["git", "clone", "--branch", default_branch, "--single-branch", repo_url, repo_path],
        check=True
    )
    subprocess.run(["git", "remote", "rename", "origin", "upstream"], cwd=repo_path, check=True)
    subprocess.run(["git", "remote", "add", "origin", fork_url], cwd=repo_path, check=True)
    print("[Agent] Repo cloned from upstream:", repo_path)
    print("[Agent] Remotes configured: upstream=original repo, origin=fork")
    return repo_path
