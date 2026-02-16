"""
K8s Executor - Kubernetes Backend for Deploy-API
=================================================

This module handles deployment of both CORE APPS and USER APPS to Kubernetes.

Architecture:
- Uses the in-cluster build-server (DinD) to build Docker images
- Pushes images to the in-cluster registry (localhost:30500)
- Creates/updates Kubernetes Deployments and Services
- Patches the nginx ConfigMap for routing

Security Model:
- Deploy-API runs with a ServiceAccount that has RBAC permissions for:
  Deployments, Services, ConfigMaps in the busibox namespace
- GitHub tokens used for cloning private repos (passed securely)

Execution Flow (for app deployment):
1. kubectl exec build-server: git clone <repo> /workspace/apps/<app-id>
2. kubectl exec build-server: docker build -t localhost:30500/<app-id>:<tag>
3. kubectl exec build-server: docker push localhost:30500/<app-id>:<tag>
4. Create/update K8s Deployment pointing to localhost:30500/<app-id>:<tag>
5. Create/update K8s Service for the app
6. Patch nginx ConfigMap to add route
7. Trigger nginx reload (kubectl exec nginx -- nginx -s reload)
"""

import asyncio
import logging
import json
from typing import Tuple, List, Optional, Dict

logger = logging.getLogger(__name__)

# Attempt to import kubernetes client; if unavailable, flag it
try:
    from kubernetes import client, config as k8s_config
    from kubernetes.client.rest import ApiException
    K8S_AVAILABLE = True
except ImportError:
    K8S_AVAILABLE = False
    logger.warning("kubernetes Python client not installed - K8s backend unavailable")

# In-cluster registry address (NodePort exposed on localhost:30500)
REGISTRY = "localhost:30500"
NAMESPACE = "busibox"
BUILD_SERVER_LABEL = "app=build-server"


def _load_k8s_config():
    """Load K8s config - in-cluster when running as a pod, or from kubeconfig."""
    if not K8S_AVAILABLE:
        raise RuntimeError("kubernetes Python client not installed")
    try:
        k8s_config.load_incluster_config()
        logger.info("Loaded in-cluster K8s config")
    except k8s_config.ConfigException:
        k8s_config.load_kube_config()
        logger.info("Loaded kubeconfig from default location")


def _get_build_server_pod() -> str:
    """Get the name of the running build-server pod."""
    v1 = client.CoreV1Api()
    pods = v1.list_namespaced_pod(
        namespace=NAMESPACE,
        label_selector=BUILD_SERVER_LABEL,
    )
    for pod in pods.items:
        if pod.status.phase == "Running":
            return pod.metadata.name
    raise RuntimeError("No running build-server pod found in namespace " + NAMESPACE)


async def _exec_in_build_server(command: str, timeout: int = 600) -> Tuple[str, str, int]:
    """
    Execute a shell command inside the build-server pod via kubectl exec.
    
    We shell out to kubectl rather than using the kubernetes-client exec streaming
    API because kubectl handles TTY, timeouts, and buffering more robustly for
    long-running build commands.
    """
    pod_name = _get_build_server_pod()
    
    kubectl_cmd = [
        "kubectl", "exec", pod_name,
        "-n", NAMESPACE,
        "--", "/bin/sh", "-c", command,
    ]
    
    cmd_preview = command[:120] + "..." if len(command) > 120 else command
    logger.debug(f"exec in build-server ({pod_name}): {cmd_preview}")
    
    proc = await asyncio.create_subprocess_exec(
        *kubectl_cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return stdout.decode(), stderr.decode(), proc.returncode or 0
    except asyncio.TimeoutError:
        proc.kill()
        return "", f"Command timed out after {timeout}s", 1


async def _exec_in_pod(pod_label: str, command: str, timeout: int = 60) -> Tuple[str, str, int]:
    """Execute a command in an arbitrary pod by label selector."""
    v1 = client.CoreV1Api()
    pods = v1.list_namespaced_pod(namespace=NAMESPACE, label_selector=pod_label)
    pod_name = None
    for pod in pods.items:
        if pod.status.phase == "Running":
            pod_name = pod.metadata.name
            break
    if not pod_name:
        return "", f"No running pod with label {pod_label}", 1
    
    kubectl_cmd = [
        "kubectl", "exec", pod_name,
        "-n", NAMESPACE,
        "--", "/bin/sh", "-c", command,
    ]
    
    proc = await asyncio.create_subprocess_exec(
        *kubectl_cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return stdout.decode(), stderr.decode(), proc.returncode or 0
    except asyncio.TimeoutError:
        proc.kill()
        return "", f"Command timed out after {timeout}s", 1


# ============================================================================
# Build Functions
# ============================================================================

async def clone_repo_on_build_server(
    repo_url: str,
    app_id: str,
    branch: str = "main",
    github_token: Optional[str] = None,
    logs: Optional[List[str]] = None,
) -> Tuple[bool, str]:
    """
    Clone or update a git repo on the build-server pod.
    
    Returns (success, app_path_on_build_server).
    """
    if logs is None:
        logs = []
    
    # Use authenticated URL for private repos
    if github_token and "github.com" in repo_url:
        # Insert token into URL: https://TOKEN@github.com/...
        auth_url = repo_url.replace("https://", f"https://{github_token}@")
    else:
        auth_url = repo_url
    
    app_path = f"/workspace/apps/{app_id}"
    
    clone_cmd = f"""
set -e
if [ -d "{app_path}/.git" ]; then
    echo "Updating existing repo..."
    cd "{app_path}"
    git fetch origin
    git checkout {branch}
    git reset --hard origin/{branch}
else
    echo "Cloning repo..."
    mkdir -p /workspace/apps
    rm -rf "{app_path}"
    git clone --branch {branch} --depth 1 {auth_url} "{app_path}"
fi
echo "CLONE_OK"
"""
    
    logs.append(f"📥 Cloning/updating {app_id} on build server...")
    stdout, stderr, code = await _exec_in_build_server(clone_cmd, timeout=120)
    
    if code != 0 or "CLONE_OK" not in stdout:
        logs.append(f"❌ Clone failed: {stderr or stdout}")
        return False, app_path
    
    logs.append(f"✅ Repository ready at {app_path}")
    return True, app_path


async def build_app_image(
    app_id: str,
    dockerfile_path: str = "Dockerfile",
    build_context: str = ".",
    tag: str = "latest",
    build_args: Optional[Dict[str, str]] = None,
    logs: Optional[List[str]] = None,
) -> Tuple[bool, str]:
    """
    Build a Docker image on the build-server and push to in-cluster registry.
    
    Args:
        app_id: Used as the image name (e.g., "busibox-portal")
        dockerfile_path: Path to Dockerfile relative to build context
        build_context: Path to build context on the build-server
        tag: Image tag (default: "latest")
        build_args: Optional build-time arguments
        logs: List to append log messages
        
    Returns:
        (success, full_image_ref)
    """
    if logs is None:
        logs = []
    
    image_ref = f"{REGISTRY}/{app_id}:{tag}"
    image_latest = f"{REGISTRY}/{app_id}:latest"
    
    # Build --build-arg flags
    build_arg_flags = ""
    if build_args:
        for k, v in build_args.items():
            build_arg_flags += f' --build-arg {k}="{v}"'
    
    build_cmd = f"""
set -e
echo "Building {app_id}..."
docker build \\
    -t {image_ref} \\
    -t {image_latest} \\
    {build_arg_flags} \\
    -f {dockerfile_path} \\
    {build_context}
echo "BUILD_OK"
"""
    
    logs.append(f"🔨 Building {app_id} image on build server...")
    stdout, stderr, code = await _exec_in_build_server(build_cmd, timeout=600)
    
    if code != 0 or "BUILD_OK" not in stdout:
        logs.append(f"❌ Build failed: {stderr or stdout}")
        return False, image_ref
    
    logs.append(f"✅ Image built: {image_ref}")
    
    # Push to in-cluster registry
    push_cmd = f"""
set -e
docker push {image_ref}
docker push {image_latest}
echo "PUSH_OK"
"""
    
    logs.append(f"📤 Pushing {app_id} to in-cluster registry...")
    stdout, stderr, code = await _exec_in_build_server(push_cmd, timeout=120)
    
    if code != 0 or "PUSH_OK" not in stdout:
        logs.append(f"❌ Push failed: {stderr or stdout}")
        return False, image_ref
    
    logs.append(f"✅ Pushed to registry: {image_ref}")
    return True, image_ref


# ============================================================================
# Kubernetes Resource Management
# ============================================================================

def create_or_update_deployment(
    app_id: str,
    image: str,
    port: int,
    env_vars: Optional[Dict[str, str]] = None,
    replicas: int = 1,
    cpu_request: str = "100m",
    memory_request: str = "256Mi",
    cpu_limit: str = "500m",
    memory_limit: str = "1Gi",
) -> bool:
    """
    Create or update a K8s Deployment for an app.
    
    Returns True on success.
    """
    _load_k8s_config()
    apps_v1 = client.AppsV1Api()
    
    # Build env list
    env_list = []
    if env_vars:
        for k, v in env_vars.items():
            env_list.append(client.V1EnvVar(name=k, value=str(v)))
    
    container = client.V1Container(
        name=app_id,
        image=image,
        image_pull_policy="Always",
        ports=[client.V1ContainerPort(container_port=port)],
        env=env_list,
        resources=client.V1ResourceRequirements(
            requests={"cpu": cpu_request, "memory": memory_request},
            limits={"cpu": cpu_limit, "memory": memory_limit},
        ),
        liveness_probe=client.V1Probe(
            http_get=client.V1HTTPGetAction(path="/api/health", port=port),
            initial_delay_seconds=30,
            period_seconds=30,
            timeout_seconds=5,
        ),
        readiness_probe=client.V1Probe(
            http_get=client.V1HTTPGetAction(path="/api/health", port=port),
            initial_delay_seconds=10,
            period_seconds=10,
            timeout_seconds=5,
        ),
    )
    
    deployment = client.V1Deployment(
        api_version="apps/v1",
        kind="Deployment",
        metadata=client.V1ObjectMeta(
            name=app_id,
            namespace=NAMESPACE,
            labels={"app": app_id, "tier": "apps", "managed-by": "deploy-api"},
        ),
        spec=client.V1DeploymentSpec(
            replicas=replicas,
            selector=client.V1LabelSelector(
                match_labels={"app": app_id},
            ),
            template=client.V1PodTemplateSpec(
                metadata=client.V1ObjectMeta(
                    labels={"app": app_id, "tier": "apps", "managed-by": "deploy-api"},
                ),
                spec=client.V1PodSpec(containers=[container]),
            ),
        ),
    )
    
    try:
        apps_v1.read_namespaced_deployment(name=app_id, namespace=NAMESPACE)
        # Exists - update it
        apps_v1.replace_namespaced_deployment(
            name=app_id, namespace=NAMESPACE, body=deployment,
        )
        logger.info(f"Updated Deployment {app_id}")
    except ApiException as e:
        if e.status == 404:
            apps_v1.create_namespaced_deployment(namespace=NAMESPACE, body=deployment)
            logger.info(f"Created Deployment {app_id}")
        else:
            logger.error(f"Failed to create/update Deployment {app_id}: {e}")
            return False
    
    return True


def create_or_update_service(
    app_id: str,
    port: int,
    target_port: int = None,
) -> bool:
    """Create or update a ClusterIP Service for an app."""
    _load_k8s_config()
    v1 = client.CoreV1Api()
    
    if target_port is None:
        target_port = port
    
    service = client.V1Service(
        api_version="v1",
        kind="Service",
        metadata=client.V1ObjectMeta(
            name=app_id,
            namespace=NAMESPACE,
            labels={"app": app_id, "managed-by": "deploy-api"},
        ),
        spec=client.V1ServiceSpec(
            selector={"app": app_id},
            ports=[client.V1ServicePort(port=port, target_port=target_port)],
        ),
    )
    
    try:
        v1.read_namespaced_service(name=app_id, namespace=NAMESPACE)
        v1.replace_namespaced_service(name=app_id, namespace=NAMESPACE, body=service)
        logger.info(f"Updated Service {app_id}")
    except ApiException as e:
        if e.status == 404:
            v1.create_namespaced_service(namespace=NAMESPACE, body=service)
            logger.info(f"Created Service {app_id}")
        else:
            logger.error(f"Failed to create/update Service {app_id}: {e}")
            return False
    
    return True


def delete_app_resources(app_id: str) -> bool:
    """Delete Deployment and Service for an app."""
    _load_k8s_config()
    apps_v1 = client.AppsV1Api()
    v1 = client.CoreV1Api()
    
    try:
        apps_v1.delete_namespaced_deployment(name=app_id, namespace=NAMESPACE)
        logger.info(f"Deleted Deployment {app_id}")
    except ApiException as e:
        if e.status != 404:
            logger.error(f"Failed to delete Deployment {app_id}: {e}")
    
    try:
        v1.delete_namespaced_service(name=app_id, namespace=NAMESPACE)
        logger.info(f"Deleted Service {app_id}")
    except ApiException as e:
        if e.status != 404:
            logger.error(f"Failed to delete Service {app_id}: {e}")
    
    return True


# ============================================================================
# Nginx ConfigMap Patching
# ============================================================================

def patch_nginx_route(
    app_id: str,
    path: str,
    service_name: str,
    service_port: int,
) -> bool:
    """
    Add or update a route in the proxy ConfigMap.
    
    Reads the current nginx.conf from the ConfigMap, adds/updates a location
    block for the given path, then patches the ConfigMap.
    """
    _load_k8s_config()
    v1 = client.CoreV1Api()
    
    try:
        cm = v1.read_namespaced_config_map(name="proxy-config", namespace=NAMESPACE)
    except ApiException:
        logger.error("proxy-config ConfigMap not found")
        return False
    
    nginx_conf = cm.data.get("nginx.conf", "")
    
    # Build the location block
    # Ensure path ends with /
    location_path = path.rstrip("/") + "/"
    upstream_name = app_id.replace("-", "_")
    
    location_block = f"""
        # {app_id} (managed by deploy-api)
        upstream {upstream_name} {{ server {service_name}:{service_port}; }}
        location {location_path} {{
            proxy_pass http://{upstream_name}/;
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto $scheme;
            # SSE support
            proxy_buffering off;
            proxy_cache off;
            proxy_read_timeout 300;
        }}
"""
    
    # Check if this app already has a location block
    marker_start = f"# {app_id} (managed by deploy-api)"
    if marker_start in nginx_conf:
        # Replace existing block - find from marker to next location or closing brace
        import re
        pattern = rf"(\s*# {re.escape(app_id)} \(managed by deploy-api\).*?upstream {re.escape(upstream_name)}.*?\}}.*?location {re.escape(location_path)}.*?\}})"
        nginx_conf = re.sub(pattern, location_block, nginx_conf, flags=re.DOTALL)
    else:
        # Insert before the root location block or at end of server block
        # Find "location / {" (root location) and insert before it
        root_loc = "            # Root - return service info"
        if root_loc in nginx_conf:
            nginx_conf = nginx_conf.replace(root_loc, location_block + "\n" + root_loc)
        else:
            # Fallback: insert before last closing brace pair
            logger.warning("Could not find root location block, appending to end of http block")
    
    # Also need to add the upstream definition in the http block (outside server)
    # Actually, we include it in the location_block above which goes inside the server block,
    # but upstreams in nginx can be defined inside http{} but outside server{}.
    # For simplicity, we define them inside the server block (nginx allows this in newer versions
    # only if using proxy_pass with inline upstream). Let's use direct proxy_pass instead.
    
    # Simpler approach: use proxy_pass with direct service:port (no separate upstream needed)
    location_block_simple = f"""
            # {app_id} (managed by deploy-api)
            location {location_path} {{
                proxy_pass http://{service_name}:{service_port}/;
                proxy_set_header Host $host;
                proxy_set_header X-Real-IP $remote_addr;
                proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
                proxy_set_header X-Forwarded-Proto $scheme;
                proxy_buffering off;
                proxy_cache off;
                proxy_read_timeout 300;
            }}
"""
    
    # Re-read and do the simpler replacement
    cm = v1.read_namespaced_config_map(name="proxy-config", namespace=NAMESPACE)
    nginx_conf = cm.data.get("nginx.conf", "")
    
    marker = f"# {app_id} (managed by deploy-api)"
    if marker in nginx_conf:
        # Replace existing block
        import re
        pattern = rf"\s*# {re.escape(app_id)} \(managed by deploy-api\)\s*location {re.escape(location_path)} \{{.*?\}}"
        nginx_conf = re.sub(pattern, location_block_simple, nginx_conf, flags=re.DOTALL)
    else:
        root_loc = "            # Root - return service info"
        if root_loc in nginx_conf:
            nginx_conf = nginx_conf.replace(root_loc, location_block_simple + "\n" + root_loc)
        else:
            logger.warning("Could not find insertion point in nginx config")
            return False
    
    # Patch the ConfigMap
    cm.data["nginx.conf"] = nginx_conf
    v1.replace_namespaced_config_map(name="proxy-config", namespace=NAMESPACE, body=cm)
    logger.info(f"Patched proxy ConfigMap with route for {app_id}")
    return True


def remove_nginx_route(app_id: str, path: str) -> bool:
    """Remove an app's route from the proxy ConfigMap."""
    _load_k8s_config()
    v1 = client.CoreV1Api()
    
    try:
        cm = v1.read_namespaced_config_map(name="proxy-config", namespace=NAMESPACE)
    except ApiException:
        return False
    
    nginx_conf = cm.data.get("nginx.conf", "")
    marker = f"# {app_id} (managed by deploy-api)"
    
    if marker not in nginx_conf:
        return True  # Already removed
    
    import re
    location_path = path.rstrip("/") + "/"
    pattern = rf"\s*# {re.escape(app_id)} \(managed by deploy-api\)\s*location {re.escape(location_path)} \{{.*?\}}"
    nginx_conf = re.sub(pattern, "", nginx_conf, flags=re.DOTALL)
    
    cm.data["nginx.conf"] = nginx_conf
    v1.replace_namespaced_config_map(name="proxy-config", namespace=NAMESPACE, body=cm)
    logger.info(f"Removed nginx route for {app_id}")
    return True


async def reload_nginx(logs: Optional[List[str]] = None) -> bool:
    """Reload nginx by exec'ing into the nginx pod."""
    if logs is None:
        logs = []
    
    logs.append("🔄 Reloading nginx...")
    stdout, stderr, code = await _exec_in_pod("app=proxy", "nginx -t && nginx -s reload")
    
    if code != 0:
        logs.append(f"❌ Nginx reload failed: {stderr}")
        return False
    
    logs.append("✅ Nginx reloaded")
    return True


# ============================================================================
# High-Level Deployment Functions
# ============================================================================

async def deploy_core_app(
    app_id: str,
    github_repo: str,
    github_ref: str = "main",
    port: int = 3000,
    base_path: str = "/",
    health_endpoint: str = "/api/health",
    github_token: Optional[str] = None,
    env_vars: Optional[Dict[str, str]] = None,
    logs: Optional[List[str]] = None,
) -> Tuple[bool, str]:
    """
    Deploy a core app (busibox-portal, busibox-agents) to K8s.
    
    Flow:
    1. Clone repo on build-server
    2. Build Docker image (Next.js multi-stage)
    3. Push to in-cluster registry
    4. Create/update Deployment + Service
    5. Patch nginx route
    6. Reload nginx
    """
    if logs is None:
        logs = []
    
    if not K8S_AVAILABLE:
        return False, "kubernetes Python client not installed"
    
    _load_k8s_config()
    
    repo_url = f"https://github.com/{github_repo}.git"
    
    # Step 1: Clone
    ok, app_path = await clone_repo_on_build_server(
        repo_url, app_id, github_ref, github_token, logs,
    )
    if not ok:
        return False, "Clone failed"
    
    # Step 2 & 3: Build + Push
    # Core apps should have a Dockerfile in their repo root
    build_args = {}
    if env_vars:
        # Pass NEXT_PUBLIC_* vars as build args for Next.js
        for k, v in env_vars.items():
            if k.startswith("NEXT_PUBLIC_"):
                build_args[k] = v
    
    ok, image_ref = await build_app_image(
        app_id=app_id,
        dockerfile_path=f"{app_path}/Dockerfile",
        build_context=app_path,
        build_args=build_args if build_args else None,
        logs=logs,
    )
    if not ok:
        return False, "Build/push failed"
    
    # Step 4: Create/update Deployment + Service
    all_env = dict(env_vars or {})
    all_env["PORT"] = str(port)
    all_env["NODE_ENV"] = "production"
    
    logs.append(f"📦 Creating/updating Deployment for {app_id}...")
    if not create_or_update_deployment(
        app_id=app_id,
        image=image_ref,
        port=port,
        env_vars=all_env,
    ):
        return False, "Deployment creation failed"
    
    logs.append(f"🔌 Creating/updating Service for {app_id}...")
    if not create_or_update_service(app_id=app_id, port=port):
        return False, "Service creation failed"
    
    # Step 5: Patch nginx route
    logs.append(f"🌐 Adding nginx route: {base_path} -> {app_id}:{port}")
    patch_nginx_route(app_id, base_path, app_id, port)
    
    # Step 6: Reload nginx
    await reload_nginx(logs)
    
    logs.append(f"✅ {app_id} deployed to K8s successfully")
    return True, f"{app_id} deployed"


async def deploy_user_app(
    app_id: str,
    repo_url: str,
    branch: str = "main",
    port: int = 3000,
    base_path: str = "/",
    dockerfile_path: str = "Dockerfile",
    github_token: Optional[str] = None,
    env_vars: Optional[Dict[str, str]] = None,
    logs: Optional[List[str]] = None,
) -> Tuple[bool, str]:
    """
    Deploy a user app to K8s.
    
    Same flow as core apps but repo URL comes from the deploy request.
    """
    if logs is None:
        logs = []
    
    if not K8S_AVAILABLE:
        return False, "kubernetes Python client not installed"
    
    _load_k8s_config()
    
    # Step 1: Clone
    ok, app_path = await clone_repo_on_build_server(
        repo_url, app_id, branch, github_token, logs,
    )
    if not ok:
        return False, "Clone failed"
    
    # Step 2 & 3: Build + Push
    ok, image_ref = await build_app_image(
        app_id=app_id,
        dockerfile_path=f"{app_path}/{dockerfile_path}",
        build_context=app_path,
        logs=logs,
    )
    if not ok:
        return False, "Build/push failed"
    
    # Step 4: Deployment + Service
    all_env = dict(env_vars or {})
    all_env["PORT"] = str(port)
    
    logs.append(f"📦 Creating/updating Deployment for {app_id}...")
    if not create_or_update_deployment(app_id=app_id, image=image_ref, port=port, env_vars=all_env):
        return False, "Deployment creation failed"
    
    logs.append(f"🔌 Creating/updating Service for {app_id}...")
    if not create_or_update_service(app_id=app_id, port=port):
        return False, "Service creation failed"
    
    # Step 5: Nginx route
    logs.append(f"🌐 Adding nginx route: {base_path} -> {app_id}:{port}")
    patch_nginx_route(app_id, base_path, app_id, port)
    
    # Step 6: Reload nginx
    await reload_nginx(logs)
    
    logs.append(f"✅ {app_id} deployed to K8s successfully")
    return True, f"{app_id} deployed"


async def undeploy_app(
    app_id: str,
    base_path: str = "/",
    logs: Optional[List[str]] = None,
) -> Tuple[bool, str]:
    """Remove an app's Deployment, Service, and nginx route."""
    if logs is None:
        logs = []
    
    if not K8S_AVAILABLE:
        return False, "kubernetes Python client not installed"
    
    _load_k8s_config()
    
    logs.append(f"🗑️ Undeploying {app_id}...")
    
    # Remove nginx route
    remove_nginx_route(app_id, base_path)
    await reload_nginx(logs)
    
    # Delete K8s resources
    delete_app_resources(app_id)
    
    logs.append(f"✅ {app_id} removed from K8s")
    return True, f"{app_id} removed"


async def get_app_status(app_id: str) -> dict:
    """Get the status of an app's Deployment."""
    if not K8S_AVAILABLE:
        return {"app_id": app_id, "status": "unknown", "error": "k8s client unavailable"}
    
    _load_k8s_config()
    apps_v1 = client.AppsV1Api()
    
    try:
        dep = apps_v1.read_namespaced_deployment(name=app_id, namespace=NAMESPACE)
        status = dep.status
        return {
            "app_id": app_id,
            "status": "running" if (status.ready_replicas or 0) > 0 else "starting",
            "replicas": status.replicas or 0,
            "ready_replicas": status.ready_replicas or 0,
            "available_replicas": status.available_replicas or 0,
            "image": dep.spec.template.spec.containers[0].image if dep.spec.template.spec.containers else "unknown",
        }
    except ApiException as e:
        if e.status == 404:
            return {"app_id": app_id, "status": "not_found"}
        return {"app_id": app_id, "status": "error", "error": str(e)}
