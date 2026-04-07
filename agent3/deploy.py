import os
import json
import subprocess
import time
import requests
from openai import OpenAI

from .dockerfile_gen import detect_port_from_dockerfile


# ── Target parser ─────────────────────────────────────────────────────────────

def parse_deploy_targets(user_input, openai_api_key):
    client   = OpenAI(api_key=openai_api_key)
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": f"""
Extract deployment platforms from this input: "{user_input}"

SUPPORTED PLATFORMS (only these, nothing else):
- aws
- azure
- render
- railway

RULES:
- Only return platforms explicitly mentioned
- "aws" means ONLY ["aws"], NOT azure/render/railway
- Return ONLY a JSON array, nothing else
- Examples:
  "aws" → ["aws"]
  "deploy to railway" → ["railway"]
  "render and aws" → ["render", "aws"]
  "azure" → ["azure"]

Return ONLY the JSON array:
"""}],
        temperature=0,
    )
    raw = response.choices[0].message.content.strip()
    if raw.startswith("```"):
        lines = [l for l in raw.splitlines() if not l.strip().startswith("```")]
        raw   = "\n".join(lines).strip()
    try:
        targets = json.loads(raw)
        valid   = {"aws", "azure", "render", "railway"}
        targets = [t for t in targets if t in valid]
        print(f"[Agent] Targets: {targets}")
        return targets
    except Exception:
        return []


# ── Credential collector ──────────────────────────────────────────────────────

def collect_credentials(targets, app_name):
    app_name = app_name.lower().replace(" ", "-")
    if len(app_name) < 4:
        app_name = f"{app_name}-app"
    print(f"[Agent] App name: {app_name}")
    creds = {}

    def get_value(env_key, label):
        val = os.getenv(env_key, "").strip()
        if val:
            print(f"  ✅ {env_key} loaded from .env")
            return val
        return input(f"  {label}: ").strip()

    if "aws" in targets:
        creds["aws"] = {
            "access_key": get_value("AWS_ACCESS_KEY_ID",     "AWS_ACCESS_KEY_ID"),
            "secret_key": get_value("AWS_SECRET_ACCESS_KEY", "AWS_SECRET_ACCESS_KEY"),
            "region":     get_value("AWS_REGION",            "AWS_REGION (e.g. ap-south-1)"),
            "app_name":   app_name,
        }

    if "azure" in targets:
        creds["azure"] = {
            "client_id":       get_value("AZURE_CLIENT_ID",       "AZURE_CLIENT_ID"),
            "client_secret":   get_value("AZURE_CLIENT_SECRET",   "AZURE_CLIENT_SECRET"),
            "tenant_id":       get_value("AZURE_TENANT_ID",       "AZURE_TENANT_ID"),
            "subscription_id": get_value("AZURE_SUBSCRIPTION_ID", "AZURE_SUBSCRIPTION_ID"),
            "resource_group":  get_value("AZURE_RESOURCE_GROUP",  "AZURE_RESOURCE_GROUP"),
            "dockerhub_user":  get_value("DOCKERHUB_USERNAME",    "Docker Hub Username"),
            "dockerhub_pass":  get_value("DOCKERHUB_PASSWORD",    "Docker Hub Password"),
            "app_name":        app_name,
            "fork_url":        "",
        }

    if "render" in targets:
        creds["render"] = {
            "api_key":  get_value("RENDER_API_KEY", "RENDER_API_KEY"),
            "app_name": app_name,
            "fork_url": "",
        }

    if "railway" in targets:
        creds["railway"] = {
            "token":          get_value("RAILWAY_TOKEN",      "RAILWAY_TOKEN"),
            "dockerhub_user": get_value("DOCKERHUB_USERNAME", "Docker Hub Username"),
            "dockerhub_pass": get_value("DOCKERHUB_PASSWORD", "Docker Hub Password"),
            "app_name":       app_name,
        }

    return creds


# ── AWS deployer ──────────────────────────────────────────────────────────────

def _get_free_tier_instance(ec2_client):
    try:
        resp = ec2_client.describe_instance_types(
            Filters=[{"Name": "free-tier-eligible", "Values": ["true"]}]
        )
        types = [i["InstanceType"] for i in resp.get("InstanceTypes", [])]
        print(f"[AWS] ℹ️  Free tier eligible types in this region: {types}")

        for preferred in ["t2.micro", "t3.micro", "t4g.micro", "t2.small"]:
            if preferred in types:
                print(f"[AWS] ✅ Using free tier instance: {preferred}")
                return preferred

        if types:
            print(f"[AWS] ✅ Using free tier instance: {types[0]}")
            return types[0]

    except Exception as e:
        print(f"[AWS] ⚠️  Could not detect free tier instance: {e}")

    print(f"[AWS] ⚠️  Falling back to t3.micro")
    return "t3.micro"


def deploy_to_aws(folder, creds):
    import boto3
    import base64

    app_name   = creds["app_name"]
    region     = creds["region"]
    access_key = creds["access_key"]
    secret_key = creds["secret_key"]
    env_vars   = creds.get("env_vars", {})
    port       = detect_port_from_dockerfile(folder, fallback="8080")

    ec2 = boto3.client(
        "ec2",
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name=region,
    )
    ecr = boto3.client(
        "ecr",
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name=region,
    )

    print(f"[AWS] 🔧 Setting up ECR repository: {app_name}...")
    try:
        repo_resp = ecr.create_repository(repositoryName=app_name)
        repo_uri  = repo_resp["repository"]["repositoryUri"]
        print(f"[AWS] ✅ ECR repo created: {repo_uri}")
    except ecr.exceptions.RepositoryAlreadyExistsException:
        repo_resp = ecr.describe_repositories(repositoryNames=[app_name])
        repo_uri  = repo_resp["repositories"][0]["repositoryUri"]
        print(f"[AWS] ℹ️  ECR repo exists: {repo_uri}")

    print(f"[AWS] 🔐 Logging Docker into ECR...")
    token_resp   = ecr.get_authorization_token()
    auth_data    = token_resp["authorizationData"][0]
    auth_token   = base64.b64decode(auth_data["authorizationToken"]).decode()
    ecr_user, ecr_pass = auth_token.split(":", 1)
    registry_url = auth_data["proxyEndpoint"]

    subprocess.run(
        ["docker", "login", "--username", ecr_user, "--password-stdin", registry_url],
        input=ecr_pass.encode(), capture_output=True, check=True
    )
    print(f"[AWS] ✅ Docker logged into ECR")

    image_tag = f"{repo_uri}:latest"
    local_tag = f"{app_name}:latest"

    print(f"[AWS] 🔨 Building image...")
    subprocess.run(["docker", "build", "-t", local_tag, "."],
                   cwd=folder, check=True)
    subprocess.run(["docker", "tag", local_tag, image_tag], check=True)

    print(f"[AWS] 📤 Pushing to ECR...")
    subprocess.run(["docker", "push", image_tag], check=True)
    print(f"[AWS] ✅ Image pushed: {image_tag}")

    print(f"[AWS] 🔧 Setting up security group...")
    sg_name = f"{app_name}-sg"
    try:
        sg_resp = ec2.create_security_group(
            GroupName=sg_name,
            Description=f"Security group for {app_name}",
        )
        sg_id = sg_resp["GroupId"]
        ec2.authorize_security_group_ingress(
            GroupId=sg_id,
            IpPermissions=[
                {"IpProtocol": "tcp", "FromPort": int(port), "ToPort": int(port),
                 "IpRanges": [{"CidrIp": "0.0.0.0/0"}]},
                {"IpProtocol": "tcp", "FromPort": 22, "ToPort": 22,
                 "IpRanges": [{"CidrIp": "0.0.0.0/0"}]},
            ]
        )
        print(f"[AWS] ✅ Security group created: {sg_id}")
    except ec2.exceptions.ClientError as e:
        if "InvalidGroup.Duplicate" in str(e):
            sgs = ec2.describe_security_groups(GroupNames=[sg_name])
            sg_id = sgs["SecurityGroups"][0]["GroupId"]
            print(f"[AWS] ℹ️  Security group exists: {sg_id}")
        else:
            raise

    env_exports = "\n".join(
        f'export {k}="{v}"' for k, v in env_vars.items()
    )
    account_id = boto3.client(
        "sts",
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
    ).get_caller_identity()["Account"]

    user_data = f"""#!/bin/bash
yum update -y
yum install -y docker
service docker start
usermod -aG docker ec2-user

export AWS_ACCESS_KEY_ID={access_key}
export AWS_SECRET_ACCESS_KEY={secret_key}
export AWS_DEFAULT_REGION={region}

aws ecr get-login-password --region {region} | docker login --username AWS --password-stdin {account_id}.dkr.ecr.{region}.amazonaws.com

{env_exports}
export PORT={port}

docker pull {image_tag}
docker run -d --restart always \\
  -p {port}:{port} \\
  -e PORT={port} \\
  {" ".join(f'-e {k}="{v}"' for k, v in env_vars.items())} \\
  --name {app_name} \\
  {image_tag}
"""

    ami_resp = ec2.describe_images(
        Filters=[
            {"Name": "name",        "Values": ["amzn2-ami-hvm-*-x86_64-gp2"]},
            {"Name": "state",       "Values": ["available"]},
            {"Name": "owner-alias", "Values": ["amazon"]},
        ],
        Owners=["amazon"],
    )
    ami_id = sorted(
        ami_resp["Images"], key=lambda x: x["CreationDate"], reverse=True
    )[0]["ImageId"]
    print(f"[AWS] ℹ️  Using AMI: {ami_id}")

    free_tier_instance = _get_free_tier_instance(ec2)
    print(f"[AWS] 🚀 Launching {free_tier_instance} EC2 instance (free tier)...")

    instance_resp = ec2.run_instances(
        ImageId=ami_id,
        InstanceType=free_tier_instance,
        MinCount=1,
        MaxCount=1,
        SecurityGroupIds=[sg_id],
        UserData=user_data,
        TagSpecifications=[{
            "ResourceType": "instance",
            "Tags": [{"Key": "Name", "Value": app_name}],
        }],
    )

    instance_id = instance_resp["Instances"][0]["InstanceId"]
    print(f"[AWS] ✅ Instance launched: {instance_id}")
    print(f"[AWS] ⏳ Waiting for instance to get public IP (30s)...")
    time.sleep(30)

    desc      = ec2.describe_instances(InstanceIds=[instance_id])
    public_ip = desc["Reservations"][0]["Instances"][0].get("PublicIpAddress", "")

    if public_ip:
        url = f"http://{public_ip}:{port}"
        print(f"[AWS] ✅ Instance running at: {url}")
        print(f"[AWS] ⏳ App may take 5-7 minutes to start (install Docker + pull image + run)")
        print(f"[AWS] ℹ️  Instance ID: {instance_id} — stop it from AWS console to avoid charges")
        return url
    else:
        url = f"http://check-aws-console-for-ip:{port}"
        print(f"[AWS] ⚠️  Could not get public IP — check AWS console for instance: {instance_id}")
        return url


# ── Azure deployer ────────────────────────────────────────────────────────────

def deploy_to_azure(folder, creds):
    from azure.identity import ClientSecretCredential
    from azure.mgmt.containerregistry import ContainerRegistryManagementClient
    from azure.mgmt.appcontainers import ContainerAppsAPIClient

    app_name  = creds["app_name"]
    reg_name  = f"{app_name}registry".replace("-", "")[:50]

    cred = ClientSecretCredential(
        tenant_id=creds["tenant_id"],
        client_id=creds["client_id"],
        client_secret=creds["client_secret"],
    )

    acr      = ContainerRegistryManagementClient(cred, creds["subscription_id"])
    result   = acr.registries.begin_create(
        creds["resource_group"], reg_name,
        {"location": "eastus", "sku": {"name": "Basic"}, "admin_user_enabled": True}
    ).result()
    login_server = result.login_server
    acr_creds    = acr.registries.list_credentials(creds["resource_group"], reg_name)
    acr_user     = acr_creds.username
    acr_pass     = acr_creds.passwords[0].value
    image_tag    = f"{login_server}/{app_name}:latest"

    env_vars = creds.get("env_vars", {})
    env_list = [{"name": k, "value": v} for k, v in env_vars.items()]

    aca = ContainerAppsAPIClient(cred, creds["subscription_id"])
    res = aca.container_apps.begin_create_or_update(
        creds["resource_group"], app_name,
        {
            "location": "eastus",
            "properties": {
                "configuration": {
                    "ingress": {"external": True, "targetPort": 8080},
                    "registries": [{"server": login_server, "username": acr_user, "passwordSecretRef": "acr-pass"}],
                    "secrets": [{"name": "acr-pass", "value": acr_pass}],
                },
                "template": {
                    "containers": [{"name": app_name, "image": image_tag,
                                    "resources": {"cpu": 0.5, "memory": "1Gi"}, "env": env_list}],
                    "scale": {"minReplicas": 1, "maxReplicas": 3},
                },
            },
        }
    ).result()

    url = f"https://{res.properties.configuration.ingress.fqdn}"
    print(f"[Azure] ✅ {url}")
    return url


# ── Render deployer ───────────────────────────────────────────────────────────

def deploy_to_render(fork_url, creds, folder=""):
    app_name = creds["app_name"]
    headers  = {"Authorization": f"Bearer {creds['api_key']}", "Content-Type": "application/json"}

    owner_id = requests.get(
        "https://api.render.com/v1/owners?limit=1", headers=headers
    ).json()[0]["owner"]["id"]

    services = requests.get(
        "https://api.render.com/v1/services?limit=50", headers=headers
    ).json()
    existing = next((s["service"] for s in services
                     if s["service"]["name"] == app_name), None)

    if existing:
        svc_id = existing["id"]
        print(f"[Render] ℹ️  Service exists — updating env vars and redeploying...")
        env_vars = creds.get("env_vars", {})
        if env_vars:
            env_pairs = [{"key": k, "value": v} for k, v in env_vars.items()]
            requests.put(f"https://api.render.com/v1/services/{svc_id}/env-vars",
                         headers=headers, json=env_pairs)
            print(f"[Render] ✅ Updated {len(env_pairs)} env vars")
        requests.post(f"https://api.render.com/v1/services/{svc_id}/deploys",
                      headers=headers, json={"clearCache": "do_not_clear"})
        raw_url = existing['serviceDetails']['url']
        url = raw_url if raw_url.startswith("http") else f"https://{raw_url}"
        print(f"[Render] ✅ Redeployed: {url}")
        return url

    env_vars_list = [{"key": "PORT", "value": "10000"}]
    for k, v in creds.get("env_vars", {}).items():
        env_vars_list.append({"key": k, "value": v})
    if env_vars_list:
        print(f"[Render] 📦 Using {len(env_vars_list)-1} env vars")

    resp = requests.post("https://api.render.com/v1/services", headers=headers, json={
        "type":    "web_service",
        "name":    app_name,
        "ownerId": owner_id,
        "repo":    fork_url.replace(".git", ""),
        "branch":  "ai-docker-setup",
        "serviceDetails": {
            "env":    "docker",
            "plan":   "free",
            "region": "oregon",
            "envVars": env_vars_list,
            "pullRequestPreviewsEnabled": "no",
        },
    })
    if resp.status_code not in (200, 201):
        raise RuntimeError(f"Render failed: {resp.text}")

    svc     = resp.json()["service"]
    raw_url = svc['serviceDetails']['url']
    url     = raw_url if raw_url.startswith("http") else f"https://{raw_url}"
    print(f"[Render] ✅ Service created from GitHub branch ai-docker-setup")
    print(f"[Render] ✅ {url}")
    return url


# ── Railway deployer ──────────────────────────────────────────────────────────

def deploy_to_railway(folder, creds):
    app_name       = creds.get("app_name")
    dockerhub_user = creds.get("dockerhub_user")
    dockerhub_pass = creds.get("dockerhub_pass")
    token          = creds.get("token")

    if not app_name:
        raise Exception("App name is required")
    if not token:
        raise Exception("Railway token is required")

    if dockerhub_user and dockerhub_pass:
        print("[Railway] 🔐 Logging into Docker Hub...")
        login = subprocess.run(
            ["docker", "login", "--username", dockerhub_user, "--password-stdin"],
            input=dockerhub_pass, text=True, capture_output=True
        )
        if login.returncode != 0:
            print("[Railway] ❌ Docker login failed")
            print(login.stderr)
            raise Exception("Docker login failed")
        else:
            print("[Railway] ✅ Docker Hub login successful")
    else:
        print("[Railway] ⚠️ Skipping Docker login (no credentials provided)")

    image_name = f"{dockerhub_user}/{app_name}:latest" if dockerhub_user else f"{app_name}:latest"
    print(f"[Railway] 🏗️ Building Docker image: {image_name}")

    build = subprocess.run(
        ["docker", "build", "-t", image_name, "."],
        cwd=folder, capture_output=True, text=True
    )
    if build.returncode != 0:
        print("[Railway] ❌ Docker build failed")
        print(build.stderr)
        raise Exception("Docker build failed")
    print("[Railway] ✅ Docker build successful")

    if dockerhub_user and dockerhub_pass:
        print("[Railway] 🚀 Pushing image to Docker Hub...")
        push = subprocess.run(["docker", "push", image_name], capture_output=True, text=True)
        if push.returncode != 0:
            print("[Railway] ❌ Docker push failed")
            print(push.stderr)
            raise Exception("Docker push failed")
        print(f"[Railway] ✅ Pushed: {image_name}")
    else:
        raise Exception("Docker Hub credentials required for Railway deploy (image must be public or pushed)")

    headers     = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    graphql_url = "https://backboard.railway.app/graphql/v2"

    def gql(query):
        resp = requests.post(graphql_url, headers=headers, json={"query": query})
        data = resp.json()
        if "errors" in data:
            raise RuntimeError(data["errors"])
        return data

    print("[Railway] 🔗 Connecting to Railway...")
    ws = gql("query { me { workspaces { id name } } }")
    workspace_id = ws["data"]["me"]["workspaces"][0]["id"]

    print("[Railway] 📦 Creating project...")
    proj = gql(f"""
    mutation {{
        projectCreate(input:{{
            name:"{app_name}",
            workspaceId:"{workspace_id}"
        }}) {{
            id
            environments {{ edges {{ node {{ id name }} }} }}
        }}
    }}
    """)
    project_id     = proj["data"]["projectCreate"]["id"]
    environment_id = proj["data"]["projectCreate"]["environments"]["edges"][0]["node"]["id"]

    print("[Railway] 🚀 Creating service...")
    svc = gql(f"""
    mutation {{
        serviceCreate(input:{{
            projectId:"{project_id}",
            name:"{app_name}",
            source:{{ image:"{image_name}" }}
        }}) {{
            id
            name
        }}
    }}
    """)
    service_id = svc["data"]["serviceCreate"]["id"]

    railway_port = detect_port_from_dockerfile(folder, fallback="8000")
    gql(f"""
    mutation {{
        variableUpsert(input:{{
            projectId:"{project_id}",
            environmentId:"{environment_id}",
            serviceId:"{service_id}",
            name:"PORT",
            value:"{railway_port}"
        }})
    }}
    """)
    print(f"[Railway] ✅ PORT={railway_port} set")

    env_vars = creds.get("env_vars", {})
    if env_vars:
        print("[Railway] 📦 Setting environment variables...")
        for key, value in env_vars.items():
            try:
                gql(f"""
                mutation {{
                    variableUpsert(input:{{
                        projectId:"{project_id}",
                        environmentId:"{environment_id}",
                        serviceId:"{service_id}",
                        name:"{key}",
                        value:"{value}"
                    }})
                }}
                """)
                print(f"[Railway] ✅ {key}")
            except Exception as e:
                print(f"[Railway] ⚠️ Failed {key}: {e}")
    else:
        print("[Railway] ℹ️ No env vars provided")

    print("[Railway] 🚀 Triggering deployment...")
    gql(f"""
    mutation {{
        serviceInstanceDeploy(
            serviceId: "{service_id}",
            environmentId: "{environment_id}"
        )
    }}
    """)

    print("[Railway] 🌐 Creating domain...")
    time.sleep(20)

    domain_q = f"""
    mutation {{
        serviceDomainCreate(input:{{
            serviceId: "{service_id}",
            environmentId: "{environment_id}"
        }}) {{
            domain
        }}
    }}
    """
    domain_resp = gql(domain_q)

    try:
        domain = domain_resp["data"]["serviceDomainCreate"]["domain"]
        url = f"https://{domain}"
    except Exception:
        print("[Railway] ⚠️  Could not get domain — retrying in 15s...")
        time.sleep(15)
        try:
            domain_resp = gql(domain_q)
            domain = domain_resp["data"]["serviceDomainCreate"]["domain"]
            url = f"https://{domain}"
        except Exception:
            url = f"https://railway.app/project/{project_id}"
            print("[Railway] ⚠️  Using project URL — check dashboard for actual domain")

    print(f"[Railway] ✅ Deployed: {url}")
    print(f"[Railway] ⏳ App may take 1-2 minutes to fully start")
    return url


# ── Multi-platform dispatcher ─────────────────────────────────────────────────

def deploy_to_platforms(targets, folder, fork_url, creds):
    results = {}
    for platform in targets:
        print(f"\n{'='*50}\n[Agent] Deploying: {platform.upper()}\n{'='*50}")
        try:
            if platform == "aws":
                results["aws"]     = deploy_to_aws(folder, creds["aws"])
            elif platform == "azure":
                results["azure"]   = deploy_to_azure(folder, creds["azure"])
            elif platform == "render":
                results["render"]  = deploy_to_render(fork_url, creds["render"], folder=folder)
            elif platform == "railway":
                results["railway"] = deploy_to_railway(folder, creds["railway"])
        except Exception as e:
            print(f"[Agent] ❌ {platform}: {e}")
            results[platform] = f"FAILED: {e}"

    print(f"\n{'='*50}\n[Agent] 🚀 SUMMARY\n{'='*50}")
    for p, url in results.items():
        icon = "✅" if not str(url).startswith("FAILED") else "❌"
        print(f"  {icon} {p.upper():<10} -> {url}")
    print(f"{'='*50}\n")
    return results
