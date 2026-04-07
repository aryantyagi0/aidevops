import os
import smtplib
import subprocess
import time
import requests
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from .utils import make_github_headers, safe_rmtree


# ── Large file handler ────────────────────────────────────────────────────────

def _handle_large_files(folder):
    """Detect files >100 MB in the last commit and handle them via LFS or .gitignore."""
    LIMIT = 100 * 1024 * 1024  # 100 MB

    result = subprocess.run(
        ["git", "diff", "--name-only", "HEAD~1", "HEAD"],
        cwd=folder, capture_output=True, text=True
    )
    if result.returncode != 0:
        result = subprocess.run(
            ["git", "ls-tree", "-r", "--name-only", "HEAD"],
            cwd=folder, capture_output=True, text=True
        )
    committed = [f.strip() for f in result.stdout.splitlines() if f.strip()]

    large_files = []
    for rel_path in committed:
        abs_path = os.path.join(folder, rel_path)
        if os.path.isfile(abs_path) and os.path.getsize(abs_path) > LIMIT:
            large_files.append(rel_path)

    if not large_files:
        return

    print(f"[Agent] ⚠️  Found {len(large_files)} file(s) exceeding GitHub's 100 MB limit:")
    for f in large_files:
        size_mb = os.path.getsize(os.path.join(folder, f)) / (1024 * 1024)
        print(f"         • {f} ({size_mb:.1f} MB)")

    lfs_available = subprocess.run(
        ["git", "lfs", "version"], capture_output=True
    ).returncode == 0

    if lfs_available:
        print("[Agent] 📦 Git LFS available — tracking large files with LFS")
        subprocess.run(["git", "lfs", "install"], cwd=folder, capture_output=True)

        gitattributes_path = os.path.join(folder, ".gitattributes")
        existing_patterns = set()
        if os.path.exists(gitattributes_path):
            with open(gitattributes_path, "r") as f:
                for line in f:
                    existing_patterns.add(line.strip())

        for rel_path in large_files:
            pattern = f"{rel_path} filter=lfs diff=lfs merge=lfs -text"
            if pattern not in existing_patterns:
                subprocess.run(
                    ["git", "lfs", "track", rel_path],
                    cwd=folder, capture_output=True
                )
                print(f"[Agent] ✅ LFS tracking: {rel_path}")
            else:
                print(f"[Agent] ℹ️  {rel_path} already covered by LFS pattern — skipping")

        subprocess.run(["git", "add", ".gitattributes"], cwd=folder, capture_output=True)
        for rel_path in large_files:
            subprocess.run(["git", "rm", "--cached", rel_path], cwd=folder, capture_output=True)
            subprocess.run(["git", "add", rel_path], cwd=folder, capture_output=True)
        subprocess.run(["git", "commit", "--amend", "--no-edit"], cwd=folder, capture_output=True)
        print("[Agent] ✅ Amended commit — large files now tracked via LFS")
    else:
        print("[Agent] ℹ️  Git LFS not available — adding large files to .gitignore")
        gitignore_path = os.path.join(folder, ".gitignore")
        existing = open(gitignore_path).read() if os.path.exists(gitignore_path) else ""
        with open(gitignore_path, "a") as f:
            for rel_path in large_files:
                entry = os.path.basename(rel_path)
                if entry not in existing:
                    f.write(f"\n{entry}")
                    print(f"[Agent] ✅ Added to .gitignore: {entry}")

        for rel_path in large_files:
            subprocess.run(["git", "rm", "--cached", rel_path], cwd=folder, capture_output=True)

        subprocess.run(["git", "add", ".gitignore"], cwd=folder, capture_output=True)
        subprocess.run(["git", "commit", "--amend", "--no-edit"], cwd=folder, capture_output=True)
        print("[Agent] ✅ Amended commit — large files excluded from push")


# ── Push / PR ─────────────────────────────────────────────────────────────────

def push_branch(folder, fork_url, token):
    _handle_large_files(folder)
    auth_url = fork_url.replace("https://", f"https://{token}@")
    subprocess.run(["git", "push", auth_url, "ai-docker-setup", "--force"],
                   cwd=folder, check=True)
    print("[Agent] Branch pushed")


def create_pull_request(repo_url, token, fork_owner, default_branch):
    repo      = repo_url.replace("https://github.com/", "").rstrip("/")
    headers   = make_github_headers(token)
    check_url = f"https://api.github.com/repos/{repo}/pulls"

    repo_owner = repo.split("/")[0]
    head_ref   = "ai-docker-setup" if fork_owner == repo_owner else f"{fork_owner}:ai-docker-setup"

    existing = requests.get(check_url, headers=headers,
                            params={"head": head_ref, "state": "open"})
    if existing.status_code == 200 and existing.json():
        url = existing.json()[0]["html_url"]
        print("[Agent] PR already exists (open):", url)
        return url

    closed = requests.get(check_url, headers=headers,
                          params={"head": head_ref, "state": "closed"})
    if closed.status_code == 200 and closed.json():
        pr = closed.json()[0]
        if pr.get("merged_at"):
            print("[Agent] ℹ️  Found old merged PR — creating new PR for latest changes")
        else:
            reopen = requests.patch(
                f"https://api.github.com/repos/{repo}/pulls/{pr['number']}",
                headers=headers, json={"state": "open"}
            )
            if reopen.status_code == 200:
                url = reopen.json()["html_url"]
                print(f"[Agent] ♻️  Reopened existing PR: {url}")
                return url

    data = {
        "title": "AI Generated Docker Setup (via OpenAI)",
        "head":  head_ref,
        "base":  default_branch,
        "body":  "Auto-generated Dockerfile by AI agent using OpenAI GPT-4o.",
    }
    r        = requests.post(check_url, headers=headers, json=data)
    response = r.json()

    if r.status_code == 201:
        url = response["html_url"]
        print("[Agent] PR created:", url)
        return url
    elif r.status_code == 422:
        for err in response.get("errors", []):
            print(f"[Agent] GitHub validation error: {err}")
        all_resp = requests.get(check_url, headers=headers,
                                params={"head": head_ref})
        if all_resp.status_code == 200 and all_resp.json():
            url = all_resp.json()[0]["html_url"]
            print(f"[Agent] Existing PR: {url}")
            return url
        raise RuntimeError(f"PR failed: {response.get('message')}")
    else:
        raise RuntimeError(f"PR failed: {r.status_code}")


def get_pr_details(repo_url, token, pr_number, retries=5, retry_delay=2):
    repo    = repo_url.replace("https://github.com/", "").rstrip("/")
    headers = make_github_headers(token)
    pr_url  = f"https://api.github.com/repos/{repo}/pulls/{pr_number}"

    last_payload = None
    for attempt in range(retries):
        r = requests.get(pr_url, headers=headers)
        if r.status_code != 200:
            raise RuntimeError(f"Failed to fetch PR details: {r.status_code} {r.text}")

        payload = r.json()
        last_payload = payload

        if payload.get("mergeable") is not None:
            return payload

        if attempt < retries - 1:
            time.sleep(retry_delay)

    return last_payload


def check_upstream_merge_conflicts(folder, repo_url, token, default_branch):
    auth_repo_url = repo_url.replace("https://", f"https://{token}@")
    fetch_result = subprocess.run(
        ["git", "fetch", "--no-tags", "--depth", "1", auth_repo_url, default_branch],
        cwd=folder, capture_output=True, text=True
    )
    if fetch_result.returncode != 0:
        return {
            "has_conflicts": False,
            "conflict_files": [],
            "conflict_text": fetch_result.stdout + fetch_result.stderr,
            "error": f"Failed to fetch upstream base branch '{default_branch}'",
        }

    merge_result = subprocess.run(
        ["git", "merge", "--no-commit", "--no-ff", "FETCH_HEAD"],
        cwd=folder, capture_output=True, text=True
    )

    diff_result = subprocess.run(
        ["git", "diff", "--name-only", "--diff-filter=U"],
        cwd=folder, capture_output=True, text=True
    )
    conflict_files = [f.strip() for f in diff_result.stdout.splitlines() if f.strip()]
    conflict_text = merge_result.stdout + merge_result.stderr

    abort_result = subprocess.run(
        ["git", "merge", "--abort"],
        cwd=folder, capture_output=True, text=True
    )
    if abort_result.returncode != 0:
        subprocess.run(["git", "reset", "--merge"], cwd=folder, capture_output=True, text=True)

    return {
        "has_conflicts": bool(conflict_files),
        "conflict_files": conflict_files,
        "conflict_text": conflict_text,
        "error": None,
    }


# ── PR polling ────────────────────────────────────────────────────────────────

def poll_pr_status(repo_url, token, fork_owner, poll_interval=30, timeout_minutes=30):
    repo      = repo_url.replace("https://github.com/", "").rstrip("/")
    headers   = make_github_headers(token)
    check_url = f"https://api.github.com/repos/{repo}/pulls"
    deadline  = time.time() + timeout_minutes * 60
    last_reported_state = None

    repo_owner = repo.split("/")[0]
    head_param = "ai-docker-setup" if fork_owner == repo_owner else f"{fork_owner}:ai-docker-setup"

    print(f"[Agent] 👀 Polling GitHub PR status every {poll_interval}s (timeout: {timeout_minutes}min)...")

    while time.time() < deadline:
        try:
            r = requests.get(check_url, headers=headers,
                             params={"head": head_param, "state": "open"})
            open_prs = r.json() if r.status_code == 200 else []

            if open_prs:
                pr_summary = open_prs[0]
                pr = get_pr_details(repo_url, token, pr_summary["number"])
                mergeable = pr.get("mergeable")
                mergeable_state = pr.get("mergeable_state", "unknown")

                if mergeable is None:
                    current_state = "computing"
                    if last_reported_state != current_state:
                        print(f"[Agent] ⏳ GitHub is still computing mergeability for: {pr['html_url']}")
                    last_reported_state = current_state
                elif mergeable is False or mergeable_state in {"dirty", "blocked", "behind", "unstable"}:
                    current_state = "conflict"
                    if last_reported_state != current_state:
                        print(f"\n[Agent] ⚠️  PR HAS MERGE ISSUES on GitHub!")
                        print(f"[Agent] 🔗 PR URL: {pr['html_url']}")
                        print(f"[Agent] ℹ️  mergeable={mergeable} | mergeable_state={mergeable_state}")
                        print(f"[Agent] ℹ️  GitHub cannot auto-merge yet.")
                        print(f"\n[Agent] Options:")
                        print(f"  1. Go to GitHub PR and resolve conflicts/update the branch")
                        print(f"  2. Or fix locally and force push to ai-docker-setup branch")
                        print()
                        print(f"[Agent] ⏳ Waiting for the PR to become mergeable...")
                    last_reported_state = current_state
                else:
                    current_state = "open_clean"
                    if last_reported_state != current_state:
                        print(f"[Agent] ⏳ PR still open and mergeable: {pr['html_url']}")
                    last_reported_state = current_state

                time.sleep(poll_interval)
                continue

            r2 = requests.get(check_url, headers=headers,
                              params={"head": head_param, "state": "closed"})
            closed_prs = r2.json() if r2.status_code == 200 else []

            if closed_prs:
                pr = closed_prs[0]
                if pr.get("merged_at"):
                    print(f"[Agent] ✅ PR MERGED: {pr['html_url']}")
                    return "merged"
                else:
                    print(f"[Agent] ❌ PR CLOSED/REJECTED: {pr['html_url']}")
                    return "closed"

            print("[Agent] ⏳ PR not found yet — waiting...")
            time.sleep(poll_interval)

        except Exception as e:
            print(f"[Agent] ⚠️  Poll error: {e} — retrying...")
            time.sleep(poll_interval)

    return "timeout"


# ── Email notification ────────────────────────────────────────────────────────

def _send_conflict_email(conflict_files, repo_url, folder):
    """Send email notification when merge conflicts are detected."""
    smtp_user  = os.getenv("NOTIFY_EMAIL", "")
    smtp_pass  = os.getenv("NOTIFY_EMAIL_PASSWORD", "")
    notify_to  = os.getenv("NOTIFY_EMAIL_TO", smtp_user)
    smtp_host  = os.getenv("SMTP_HOST", "smtp.gmail.com")
    smtp_port  = int(os.getenv("SMTP_PORT", "587"))

    if not smtp_user or not smtp_pass:
        print(f"[Agent] ℹ️  Email not configured — skipping notification")
        print(f"[Agent] ℹ️  Add NOTIFY_EMAIL, NOTIFY_EMAIL_PASSWORD, NOTIFY_EMAIL_TO to .env to enable")
        return

    try:
        conflict_list = "\n".join(f"  • {f}" for f in conflict_files)
        repo_name     = repo_url.rstrip("/").split("/")[-1].replace(".git", "")

        body = f"""
Hi,

Your AI Docker Agent detected MERGE CONFLICTS in your repository.

Repository: {repo_url}
Branch:     ai-docker-setup → main

Conflicting files:
{conflict_list}

The agent has paused and is waiting for your input.
Please check your terminal — GPT-4o has analyzed the conflicts
and is waiting for your approval to resolve them automatically.

— AI Docker Agent
"""
        msg            = MIMEMultipart()
        msg["From"]    = smtp_user
        msg["To"]      = notify_to
        msg["Subject"] = f"⚠️ Merge Conflicts Detected — {repo_name}"
        msg.attach(MIMEText(body, "plain"))

        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.sendmail(smtp_user, notify_to, msg.as_string())

        print(f"[Agent] 📧 Conflict notification sent to {notify_to}")

    except Exception as e:
        print(f"[Agent] ⚠️  Could not send email: {e}")
        print(f"[Agent] ℹ️  Check NOTIFY_EMAIL and NOTIFY_EMAIL_PASSWORD in .env")
