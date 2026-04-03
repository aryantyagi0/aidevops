# AI Docker Deployment Agent

An intelligent end-to-end DevOps automation agent that takes any GitHub repository and fully automates the path from raw source code to a live, cloud-deployed application — no manual Dockerfile writing, no cloud console clicking.

Built with **LangGraph · OpenAI GPT-4o · Docker · GitHub API**

![Pipeline](pipeline_diagram.png)

---

## What it does

1. Clones any public GitHub repository to your local machine
2. Deep-scans the project to detect language, framework, and entry point
3. Generates a production-ready Dockerfile using GPT-4o
4. Tests the Docker image locally with auto-fix on failure (up to 3 retries)
5. Pushes the Dockerfile via a Pull Request and waits for you to merge it
6. After merge — fresh-clones, sets up `.env`, runs a live local test so you can verify in your browser
7. Deploys to your chosen cloud platform and returns the public URL

---

## Supported Frameworks

| Language | Frameworks |
|----------|-----------|
| Python | FastAPI, Flask, Django, Streamlit, Gradio, ML scripts |
| Node.js | React, Next.js, Vue, Angular, Express, Fastify |
| Go | Standard HTTP servers |
| Java | Spring Boot (Maven / Gradle) |
| Ruby | Rails |
| PHP | Laravel (Composer) |
| Rust | Cargo |
| Static | HTML/CSS/JS |

---

## Supported Cloud Platforms

| Platform | Method |
|----------|--------|
| Railway | Docker Hub + GraphQL API |
| Render | GitHub branch (Docker env) |
| AWS | ECR + EC2 (Docker pull on launch) |
| Azure | ACR + Container Apps |

---

## Requirements

Before you start, make sure you have:

- **Python 3.10+** installed
- **Docker Desktop** installed and running
- **Git** configured (`git config --global user.name "Your Name"`)
- **VS Code** installed (for the HITL edit steps)
- **OpenAI API key** with GPT-4o access
- **GitHub Personal Access Token** with `repo` + `workflow` scopes

---

## Installation

### 1. Clone this repo

```bash
git clone https://github.com/aryantyagi0/emergent_ai.git
cd emergent_ai
```

### 2. Create a virtual environment

```bash
python -m venv venv

# Windows
venv\Scripts\activate

# macOS / Linux
source venv/bin/activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Create your `.env` file

```env
# Required
OPENAI_API_KEY=your_openai_key
GITHUB_TOKEN=your_github_token

# Railway (if deploying to Railway)
RAILWAY_TOKEN=your_railway_token
DOCKERHUB_USERNAME=your_dockerhub_username
DOCKERHUB_PASSWORD=your_dockerhub_password

# Render (if deploying to Render)
RENDER_API_KEY=your_render_key

# AWS (if deploying to AWS)
AWS_ACCESS_KEY_ID=your_access_key
AWS_SECRET_ACCESS_KEY=your_secret_key
AWS_REGION=ap-south-1

# Azure (if deploying to Azure)
AZURE_CLIENT_ID=
AZURE_CLIENT_SECRET=
AZURE_TENANT_ID=
AZURE_SUBSCRIPTION_ID=
AZURE_RESOURCE_GROUP=
DOCKERHUB_USERNAME=your_dockerhub_username
DOCKERHUB_PASSWORD=your_dockerhub_password
```

---

## Usage

### Option A — Streamlit Frontend (recommended)

```bash
streamlit run frontend.py
```

Opens a browser UI at `http://localhost:8501`. Walk through each step visually with buttons and forms.

### Option B — Terminal (CLI)

```bash
python langgraph_agent3.py
```

You will be prompted for the GitHub repo URL. Everything else is read from `.env`.

---

## Step-by-Step Pipeline

### Phase 1 — Setup

**Step 1: Authenticate**
The agent verifies your GitHub token against the GitHub API and confirms your OpenAI key is set. Both are loaded automatically from `.env`.

**Step 2: Get default branch**
Fetches the target repo's metadata via the GitHub API to identify whether the main branch is called `main` or `master`.

**Step 3: Fork repository**
Forks the target repo to your GitHub account using the GitHub Forks API. If you already have a fork, it reuses it. If you own the repo, it works directly on it without forking.

**Step 4: Clone repository**
Clones the fork to your local machine in the current directory. Configures two Git remotes:
- `upstream` → original repo
- `origin` → your fork

VS Code opens automatically at the cloned folder so you can start reviewing right away.

---

### Phase 2 — Human-in-the-Loop: Edit

**Step 5: HITL pause — Edit files**

The agent pauses here and VS Code is already open. You can:
- Edit source files (fix bugs, update configs)
- Add a `.env` file with API keys your app needs at runtime
- Fix `requirements.txt` if dependencies are missing or pinned wrong
- Add missing data files or model weights

The agent also detects which env vars your app likely needs (by scanning for `os.getenv`, `os.environ`, etc.) and shows them as hints.

When done, click **Done Editing** in the UI (or press `y` in the terminal).

---

### Phase 3 — AI Dockerfile Generation

**Step 6: Generate Dockerfile**

GPT-4o deep-scans the repository:
- Reads `requirements.txt`, `package.json`, `go.mod`, etc.
- Scores every `.py` / `.js` file for framework signals (FastAPI, Streamlit, Flask, etc.)
- Identifies the entry point file and app variable name
- Detects Python/Node version from runtime files

Then generates a production-ready Dockerfile with:
- Correct base image for the detected language + version
- Proper `COPY` and `RUN pip install` / `npm install` steps
- `CMD` using `sh -c` with `${PORT:-DEFAULT}` for Railway/Render compatibility
- `ENV PYTHONUNBUFFERED=1` and `ENV PYTHONDONTWRITEBYTECODE=1` for Python apps
- Streamlit-specific flags (`--server.headless=true`, `--server.address=0.0.0.0`)

If a database dependency is detected (`psycopg2`, `pymongo`, etc.), a `docker-compose.yml` is also generated automatically.

**Step 7: Test Docker locally**

The agent runs:
1. `docker build -t <app-name>:latest .`
2. `docker run -d -p <port>:<port> <app-name>:latest`
3. HTTP health check on `http://localhost:<port>`

**Step 8: Test passed?**

- **No** → GPT-4o reads the build/runtime error, rewrites the Dockerfile, and retries (up to 3 attempts). Common fixes: wrong module path, missing system libraries (`libgomp`, `libGL`), incorrect `CMD` syntax.
- **Yes** → proceed to PR

> **Database apps** skip the runtime test (can't connect to DB locally without credentials) and do a build-only test instead.

---

### Phase 4 — GitHub Pull Request

**Step 9: Push branch + create PR**

1. Creates a new branch `ai-docker-setup` from the latest upstream `main`
2. Commits the Dockerfile (and `docker-compose.yml` if generated)
3. Pushes to your fork
4. Opens a Pull Request against the original repo via the GitHub API

If merge conflicts are detected between your branch and upstream, GPT-4o analyzes them and recommends a resolution strategy (keep ours vs keep theirs, per file). You can accept the AI suggestion or resolve manually in VS Code.

**Step 10: Poll PR status**

The agent (or you, by clicking **Check PR Status** in the UI) polls GitHub every 30 seconds. Once you merge the PR on GitHub, the agent detects it and moves on automatically.

---

### Phase 5 — Local Verification

**Step 11: Fresh clone**

After the PR is merged, the agent does a fresh `git clone` of the original repo's `main` branch. This ensures you're testing exactly what's in production, not your local working copy.

VS Code opens automatically at the fresh clone.

**Step 12: HITL — .env setup**

Since `.env` is in `.gitignore`, it's not included in the clone. The agent shows an env var editor where you can paste your runtime secrets (database URLs, API keys, etc.) before the local test.

This step is optional — if your app doesn't need env vars, just skip it.

**Step 13: Local test (venv)**

The agent:
1. Creates an isolated Python `venv` in a `_test` copy of the folder
2. Installs `requirements.txt` into the venv
3. Starts your app server (uvicorn / streamlit / flask / etc.) as a background process
4. Detects which port it's listening on (avoids port conflicts with the Streamlit UI itself)
5. Shows you a clickable link: `http://localhost:<PORT>`

**Step 14: HITL — Verify in browser**

Open the link and test your app manually. When you're satisfied it works correctly, click **Stop Server & Continue to Deploy**. The test venv and folder are cleaned up automatically.

---

### Phase 6 — Deployment

**Step 15: Deploy the app?**

- **No** → pipeline ends, no deployment
- **Yes** → continue

**Step 16: Collect deploy info**

Select your target platform and enter:
- App name
- Platform credentials (auto-loaded from `.env` if present)
- Any additional env vars to set on the platform

**Step 17: Deploy**

| Platform | What happens |
|----------|-------------|
| **Railway** | Builds Docker image → pushes to Docker Hub → creates Railway project via GraphQL API → sets env vars → triggers deploy → creates public domain |
| **Render** | Creates a Render web service pointed at your GitHub branch → Render pulls and builds the Docker image itself |
| **AWS** | Builds image → pushes to ECR → creates security group → launches EC2 t2.micro → EC2 startup script installs Docker, pulls from ECR, runs container |
| **Azure** | Builds image → pushes to ACR → deploys to Azure Container Apps |

**Step 18: Deployment complete**

The agent prints the public URL. Your app is live.

> **Railway / Render** — app is usually live within 1–2 minutes.
>
> **AWS** — wait 5–7 minutes after the URL is printed for the EC2 instance to install Docker, pull the image, and start the container. The Instance ID is printed — use it to stop the instance from the AWS Console when not in use to avoid charges.

---

## AWS Deployment — Extra Details

No manual AWS setup required. The agent handles everything:

1. Creates an ECR repository for your image
2. Builds and pushes the image from your local machine
3. Creates a security group with your app port open
4. Launches an EC2 `t2.micro` (free tier eligible) with a user-data startup script
5. The EC2 script: installs Docker → authenticates to ECR → pulls image → runs container

**Required IAM permissions:**
- `AmazonEC2FullAccess`
- `AmazonEC2ContainerRegistryFullAccess`

**AWS Free Tier:**
| Service | Free |
|---------|------|
| EC2 t2.micro | 750 hrs/month for 12 months |
| ECR | 500 MB/month forever |

> Note: Raw EC2 IPs may be blocked on college/corporate networks (FortiGate "Not Rated"). Test on mobile data or map a domain to your EC2 IP.

---

## Project Structure

```
emergent_ai/
├── langgraph_agent3.py    # Core agent — all pipeline logic
├── frontend.py            # Streamlit UI
├── requirements.txt       # Python dependencies
├── .env                   # Your secrets (gitignored)
├── pipeline_diagram.png   # Visual pipeline flowchart
└── README.md
```

---

## Tech Stack

| Tool | Purpose |
|------|---------|
| LangGraph | Pipeline state machine / workflow orchestration |
| OpenAI GPT-4o | Dockerfile generation and auto-fix |
| Docker | Local image build, run, and health check |
| GitHub REST API | Fork, branch, push, PR, poll |
| Streamlit | Browser-based UI |
| Boto3 | AWS EC2 + ECR |
| Azure SDK | Azure Container Apps |
| Render REST API | Render web service |
| Railway GraphQL API | Railway deployment |

---

## Troubleshooting

**`Port 8501 is not available` during local test**
The Streamlit UI runs on 8501. The agent automatically picks the next free port (8502+) for test apps. If you still see this, another process is using ports in that range — check with `netstat -ano | findstr :850`.

**`Render failed: {"message":"invalid JSON"}`**
Fixed in latest version. Was caused by a boolean type mismatch in the Render API payload.

**`https://https://...` URL from Render**
Fixed in latest version. Render's API already returns a full URL — the agent was incorrectly prepending `https://` again.

**Docker test fails with `Could not import module`**
The module path in `CMD` is wrong. GPT-4o will auto-fix this on retry. If it fails 3 times, check that your entry file is at the repo root or in an `app/` folder.

**App works locally but fails on EC2**
Usually a missing env var. Set all required env vars during the "Collect deploy info" step.

---

## Author

**Aryan Tyagi**
BTech CSE (AI/ML) |  AI Engineer
