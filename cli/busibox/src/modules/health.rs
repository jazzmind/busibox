use crate::modules::remote;
use crate::modules::ssh::SshConnection;
use std::sync::mpsc;

#[derive(Debug, Clone, PartialEq)]
pub enum HealthStatus {
    Healthy,
    Unhealthy,
    Down,
    Checking,
}

#[derive(Debug, Clone)]
pub enum CheckMethod {
    Http { path: &'static str, port: u16 },
    Cli { command: &'static str },
}

#[derive(Debug, Clone)]
pub struct ServiceHealthDef {
    pub name: &'static str,
    pub group: &'static str,
    pub check: CheckMethod,
    /// Base Proxmox container ID (production). Staging adds 100.
    /// Used to compute the service IP: production → 10.96.200.{id}, staging → 10.96.201.{id}.
    /// DNS hostnames are NOT environment-qualified on the Proxmox host, so we must use IPs.
    pub proxmox_container_id: Option<u16>,
    /// For Proxmox CLI-only checks: the health endpoint to use instead of the Docker CLI command.
    /// Format: (port, path). If None, falls back to ping.
    pub proxmox_health: Option<(u16, &'static str)>,
}

#[derive(Debug, Clone)]
pub struct ServiceHealthResult {
    pub name: String,
    pub group: String,
    pub status: HealthStatus,
}

#[derive(Debug, Clone)]
pub struct GroupHealth {
    pub name: String,
    pub healthy: usize,
    pub total: usize,
    pub status: HealthStatus,
}

pub enum HealthUpdate {
    ServiceResult(ServiceHealthResult),
    Complete,
}

const CORE_SERVICES: &[ServiceHealthDef] = &[
    ServiceHealthDef {
        name: "postgres",
        group: "Core Services",
        check: CheckMethod::Cli {
            command: "docker exec {PREFIX}-postgres pg_isready -U postgres 2>/dev/null",
        },
        proxmox_container_id: Some(203),
        proxmox_health: None, // TCP-only, use ping
    },
    ServiceHealthDef {
        name: "redis",
        group: "Core Services",
        check: CheckMethod::Cli {
            command: "docker exec {PREFIX}-redis redis-cli ping 2>/dev/null",
        },
        proxmox_container_id: Some(206),
        proxmox_health: None, // TCP-only, use ping
    },
    ServiceHealthDef {
        name: "minio",
        group: "Core Services",
        check: CheckMethod::Http {
            path: "/minio/health/live",
            port: 9000,
        },
        proxmox_container_id: Some(205),
        proxmox_health: None,
    },
    ServiceHealthDef {
        name: "milvus",
        group: "Core Services",
        check: CheckMethod::Http {
            path: "/healthz",
            port: 9091,
        },
        proxmox_container_id: Some(204),
        proxmox_health: None,
    },
    ServiceHealthDef {
        name: "neo4j",
        group: "Core Services",
        check: CheckMethod::Cli {
            command: "docker exec {PREFIX}-neo4j wget -q --spider http://localhost:7474 2>/dev/null && echo ok",
        },
        proxmox_container_id: Some(213),
        proxmox_health: Some((7474, "/")),
    },
];

const API_SERVICES: &[ServiceHealthDef] = &[
    ServiceHealthDef {
        name: "authz",
        group: "APIs",
        check: CheckMethod::Http {
            path: "/health/live",
            port: 8010,
        },
        proxmox_container_id: Some(210),
        proxmox_health: None,
    },
    ServiceHealthDef {
        name: "agent",
        group: "APIs",
        check: CheckMethod::Http {
            path: "/health",
            port: 8000,
        },
        proxmox_container_id: Some(202),
        proxmox_health: None,
    },
    ServiceHealthDef {
        name: "data",
        group: "APIs",
        check: CheckMethod::Http {
            path: "/health",
            port: 8002,
        },
        proxmox_container_id: Some(206),
        proxmox_health: None,
    },
    ServiceHealthDef {
        name: "data-worker",
        group: "APIs",
        check: CheckMethod::Cli {
            command: "docker ps --filter name=^{PREFIX}-data-worker$ --filter status=running --format '{{.Names}}' 2>/dev/null",
        },
        proxmox_container_id: Some(206),
        proxmox_health: Some((8002, "/health")),
    },
    ServiceHealthDef {
        name: "search",
        group: "APIs",
        check: CheckMethod::Http {
            path: "/health",
            port: 8003,
        },
        proxmox_container_id: Some(204),
        proxmox_health: None,
    },
    ServiceHealthDef {
        name: "deploy",
        group: "APIs",
        check: CheckMethod::Http {
            path: "/health/live",
            port: 8011,
        },
        proxmox_container_id: Some(210),
        proxmox_health: None,
    },
    ServiceHealthDef {
        name: "docs",
        group: "APIs",
        check: CheckMethod::Http {
            path: "/health/live",
            port: 8004,
        },
        proxmox_container_id: Some(202),
        proxmox_health: None,
    },
    ServiceHealthDef {
        name: "embedding",
        group: "APIs",
        check: CheckMethod::Http {
            path: "/health",
            port: 8005,
        },
        proxmox_container_id: Some(206),
        proxmox_health: None,
    },
    ServiceHealthDef {
        name: "bridge",
        group: "APIs",
        check: CheckMethod::Http {
            path: "/health",
            port: 8081,
        },
        proxmox_container_id: Some(211),
        proxmox_health: None,
    },
];

const LLM_VLLM: ServiceHealthDef = ServiceHealthDef {
    name: "vllm",
    group: "LLM",
    check: CheckMethod::Http {
        path: "/health",
        port: 8000,
    },
    proxmox_container_id: Some(208),
    proxmox_health: None,
};

const LLM_MLX: ServiceHealthDef = ServiceHealthDef {
    name: "mlx",
    group: "LLM",
    check: CheckMethod::Http {
        path: "/v1/models",
        port: 8080,
    },
    proxmox_container_id: Some(211),
    proxmox_health: None,
};

const LLM_LITELLM: ServiceHealthDef = ServiceHealthDef {
    name: "litellm",
    group: "LLM",
    check: CheckMethod::Http {
        path: "/health/liveliness",
        port: 4000,
    },
    proxmox_container_id: Some(207),
    proxmox_health: None,
};

const APP_SERVICES: &[ServiceHealthDef] = &[
    ServiceHealthDef {
        name: "proxy",
        group: "Apps",
        check: CheckMethod::Http {
            path: "/",
            port: 80,
        },
        proxmox_container_id: Some(200),
        proxmox_health: None,
    },
    ServiceHealthDef {
        name: "core-apps",
        group: "Apps",
        check: CheckMethod::Cli {
            command: "docker ps --filter name=^{PREFIX}-core-apps$ --filter status=running --format '{{.Names}}' 2>/dev/null",
        },
        proxmox_container_id: Some(201),
        proxmox_health: Some((3000, "/portal/api/health")),
    },
    ServiceHealthDef {
        name: "portal",
        group: "Apps",
        check: CheckMethod::Cli {
            command: "docker exec {PREFIX}-core-apps curl -sf -o /dev/null -w '%{http_code}' --max-time 3 http://localhost:3000/portal/api/health 2>/dev/null",
        },
        proxmox_container_id: Some(201),
        proxmox_health: Some((3000, "/portal/api/health")),
    },
    ServiceHealthDef {
        name: "admin",
        group: "Apps",
        check: CheckMethod::Cli {
            command: "docker exec {PREFIX}-core-apps curl -sf -o /dev/null -w '%{http_code}' --max-time 3 http://localhost:3002/admin/api/health 2>/dev/null",
        },
        proxmox_container_id: Some(201),
        proxmox_health: Some((3002, "/admin/api/health")),
    },
    ServiceHealthDef {
        name: "agents",
        group: "Apps",
        check: CheckMethod::Cli {
            command: "docker exec {PREFIX}-core-apps curl -sf -o /dev/null -w '%{http_code}' --max-time 3 http://localhost:3001/agents/api/health 2>/dev/null",
        },
        proxmox_container_id: Some(201),
        proxmox_health: Some((3001, "/agents/api/health")),
    },
    ServiceHealthDef {
        name: "chat",
        group: "Apps",
        check: CheckMethod::Cli {
            command: "docker exec {PREFIX}-core-apps curl -sf -o /dev/null -w '%{http_code}' --max-time 3 http://localhost:3003/chat/api/health 2>/dev/null",
        },
        proxmox_container_id: Some(201),
        proxmox_health: Some((3003, "/chat/api/health")),
    },
    ServiceHealthDef {
        name: "appbuilder",
        group: "Apps",
        check: CheckMethod::Cli {
            command: "docker exec {PREFIX}-core-apps curl -sf -o /dev/null -w '%{http_code}' --max-time 3 http://localhost:3004/builder/api/health 2>/dev/null",
        },
        proxmox_container_id: Some(201),
        proxmox_health: Some((3004, "/builder/api/health")),
    },
    ServiceHealthDef {
        name: "media",
        group: "Apps",
        check: CheckMethod::Cli {
            command: "docker exec {PREFIX}-core-apps curl -sf -o /dev/null -w '%{http_code}' --max-time 3 http://localhost:3005/media/api/health 2>/dev/null",
        },
        proxmox_container_id: Some(201),
        proxmox_health: Some((3005, "/media/api/health")),
    },
    ServiceHealthDef {
        name: "documents",
        group: "Apps",
        check: CheckMethod::Cli {
            command: "docker exec {PREFIX}-core-apps curl -sf -o /dev/null -w '%{http_code}' --max-time 3 http://localhost:3006/documents/api/health 2>/dev/null",
        },
        proxmox_container_id: Some(201),
        proxmox_health: Some((3006, "/documents/api/health")),
    },
];

/// Build the full list of services to check, adjusting LLM services based on backend.
pub fn all_service_defs(is_mlx: bool) -> Vec<ServiceHealthDef> {
    let mut defs: Vec<ServiceHealthDef> = Vec::new();
    defs.extend_from_slice(CORE_SERVICES);
    defs.extend_from_slice(API_SERVICES);
    defs.push(LLM_LITELLM);
    if is_mlx {
        defs.push(LLM_MLX);
    } else {
        defs.push(LLM_VLLM);
    }
    defs.extend_from_slice(APP_SERVICES);
    defs
}

/// Run a single health check, returning the status.
fn check_service(
    def: &ServiceHealthDef,
    host: &str,
    prefix: &str,
    ssh: Option<&SshConnection>,
    is_proxmox: bool,
    network_base: &str,
) -> HealthStatus {
    if is_proxmox {
        return check_service_proxmox(def, ssh, network_base);
    }

    match &def.check {
        CheckMethod::Http { path, port } => {
            let url = format!("http://{host}:{port}{path}");
            let curl_cmd =
                format!("curl -s -o /dev/null -w '%{{http_code}}' --max-time 3 '{url}'");

            let output = if let Some(ssh) = ssh {
                let full_cmd = format!("{}{curl_cmd}", remote::SHELL_PATH_PREAMBLE);
                ssh.run(&full_cmd).unwrap_or_default()
            } else {
                std::process::Command::new("bash")
                    .arg("-c")
                    .arg(&curl_cmd)
                    .output()
                    .ok()
                    .map(|o| String::from_utf8_lossy(&o.stdout).trim().to_string())
                    .unwrap_or_default()
            };

            let code = output.trim().parse::<u16>().unwrap_or(0);
            if (200..300).contains(&code) {
                HealthStatus::Healthy
            } else if code == 503 {
                HealthStatus::Unhealthy
            } else if code == 0 {
                HealthStatus::Down
            } else {
                HealthStatus::Unhealthy
            }
        }
        CheckMethod::Cli { command } => {
            let cmd = command.replace("{PREFIX}", prefix);

            let output = if let Some(ssh) = ssh {
                let full_cmd = format!("{}{cmd}", remote::SHELL_PATH_PREAMBLE);
                ssh.run(&full_cmd)
            } else {
                std::process::Command::new("bash")
                    .arg("-c")
                    .arg(&cmd)
                    .output()
                    .map(|o| {
                        if o.status.success() {
                            String::from_utf8_lossy(&o.stdout).to_string()
                        } else {
                            String::new()
                        }
                    })
                    .map_err(|e| color_eyre::eyre::eyre!("{e}"))
            };

            match output {
                Ok(out) if !out.trim().is_empty() => HealthStatus::Healthy,
                _ => HealthStatus::Down,
            }
        }
    }
}

/// Interpret an HTTP status code for Proxmox health checks.
/// Matches the bash backend (proxmox.sh) which treats 200, 301, 302, 401, 403
/// as healthy — services behind auth or with redirects are still "up".
fn proxmox_status_from_http(code: u16) -> HealthStatus {
    match code {
        0 => HealthStatus::Down,
        200..=299 | 301 | 302 | 401 | 403 => HealthStatus::Healthy,
        500..=599 => HealthStatus::Unhealthy,
        _ => HealthStatus::Unhealthy,
    }
}

/// Compute the LXC container IP from its base container ID and the profile's network base octets.
/// E.g., network_base="10.96.201", base_id=210 → "10.96.201.210"
fn container_ip(base_id: u16, network_base: &str) -> String {
    format!("{network_base}.{base_id}")
}

/// Proxmox health check: runs via SSH on the Proxmox host, targeting computed container IPs
/// (not DNS hostnames, which are not environment-qualified on the Proxmox host).
fn check_service_proxmox(
    def: &ServiceHealthDef,
    ssh: Option<&SshConnection>,
    network_base: &str,
) -> HealthStatus {
    let ssh = match ssh {
        Some(s) => s,
        None => return HealthStatus::Down,
    };

    let base_id = match def.proxmox_container_id {
        Some(id) => id,
        None => return HealthStatus::Down,
    };

    let ip = container_ip(base_id, network_base);

    match &def.check {
        CheckMethod::Http { path, port } => {
            let url = format!("http://{ip}:{port}{path}");
            let curl_cmd = format!(
                "curl -s -o /dev/null -w '%{{http_code}}' --max-time 5 --connect-timeout 3 '{url}'"
            );
            let full_cmd = format!("{}{curl_cmd}", remote::SHELL_PATH_PREAMBLE);
            let output = ssh.run(&full_cmd).unwrap_or_default();
            let code = output.trim().parse::<u16>().unwrap_or(0);
            proxmox_status_from_http(code)
        }
        CheckMethod::Cli { .. } => {
            if let Some((port, path)) = def.proxmox_health {
                let url = format!("http://{ip}:{port}{path}");
                let curl_cmd = format!(
                    "curl -s -o /dev/null -w '%{{http_code}}' --max-time 5 --connect-timeout 3 '{url}'"
                );
                let full_cmd = format!("{}{curl_cmd}", remote::SHELL_PATH_PREAMBLE);
                let output = ssh.run(&full_cmd).unwrap_or_default();
                let code = output.trim().parse::<u16>().unwrap_or(0);
                proxmox_status_from_http(code)
            } else {
                let ping_cmd = format!("ping -c 1 -W 2 {ip} >/dev/null 2>&1 && echo ok");
                let full_cmd = format!("{}{ping_cmd}", remote::SHELL_PATH_PREAMBLE);
                match ssh.run(&full_cmd) {
                    Ok(out) if out.trim().contains("ok") => HealthStatus::Healthy,
                    _ => HealthStatus::Down,
                }
            }
        }
    }
}

/// Public wrapper for check_service, usable from manage screen.
pub fn check_service_pub(
    def: &ServiceHealthDef,
    host: &str,
    prefix: &str,
    ssh: Option<&SshConnection>,
    is_proxmox: bool,
    network_base: &str,
    vllm_network_base: &str,
) -> HealthStatus {
    let effective_base = if def.name == "vllm" { vllm_network_base } else { network_base };
    check_service(def, host, prefix, ssh, is_proxmox, effective_base)
}

/// Run all health checks, sending results through the channel as they complete.
///
/// For Proxmox: builds a single batched SSH command that runs all curl/ping checks
/// in one round-trip, then parses the results. This avoids flaky parallel SSH issues.
///
/// For Docker (local or remote-docker): runs checks in parallel threads since each
/// check is a local subprocess or a single SSH call with no contention.
///
/// `vllm_network_base`: when staging uses production vLLM, this points vLLM health
/// checks at the production network instead of the profile's own network.
pub fn run_health_checks(
    defs: Vec<ServiceHealthDef>,
    host: String,
    prefix: String,
    ssh_details: Option<(String, String, String)>, // (host, user, key)
    is_proxmox: bool,
    network_base: String,
    vllm_network_base: String,
    tx: mpsc::Sender<HealthUpdate>,
) {
    std::thread::spawn(move || {
        if is_proxmox {
            run_health_checks_proxmox_batched(&defs, &ssh_details, &network_base, &vllm_network_base, &tx);
        } else {
            run_health_checks_parallel(&defs, &host, &prefix, &ssh_details, &network_base, &tx);
        }
        let _ = tx.send(HealthUpdate::Complete);
    });
}

/// Proxmox batched health checks: builds one SSH command that runs all checks sequentially
/// on the remote host, separated by markers, and parses all results from a single SSH call.
fn run_health_checks_proxmox_batched(
    defs: &[ServiceHealthDef],
    ssh_details: &Option<(String, String, String)>,
    network_base: &str,
    vllm_network_base: &str,
    tx: &mpsc::Sender<HealthUpdate>,
) {
    let ssh = match ssh_details {
        Some((h, u, k)) => SshConnection::new(h, u, k),
        None => {
            for def in defs {
                let _ = tx.send(HealthUpdate::ServiceResult(ServiceHealthResult {
                    name: def.name.to_string(),
                    group: def.group.to_string(),
                    status: HealthStatus::Down,
                }));
            }
            return;
        }
    };

    let mut commands: Vec<String> = Vec::new();
    let marker = "___BUSIBOX_SEP___";

    for def in defs {
        let base_id = match def.proxmox_container_id {
            Some(id) => id,
            None => {
                commands.push(format!("echo '{marker}:000'"));
                continue;
            }
        };
        let effective_base = if def.name == "vllm" { vllm_network_base } else { network_base };
        let ip = container_ip(base_id, effective_base);

        let check_cmd = match &def.check {
            CheckMethod::Http { path, port } => {
                let url = format!("http://{ip}:{port}{path}");
                format!(
                    "echo -n '{marker}:'; curl -s -o /dev/null -w '%{{http_code}}' --max-time 5 --connect-timeout 3 '{url}' 2>/dev/null || echo 000"
                )
            }
            CheckMethod::Cli { .. } => {
                if let Some((port, path)) = def.proxmox_health {
                    let url = format!("http://{ip}:{port}{path}");
                    format!(
                        "echo -n '{marker}:'; curl -s -o /dev/null -w '%{{http_code}}' --max-time 5 --connect-timeout 3 '{url}' 2>/dev/null || echo 000"
                    )
                } else {
                    format!(
                        "ping -c 1 -W 2 {ip} >/dev/null 2>&1 && echo '{marker}:PING_OK' || echo '{marker}:PING_FAIL'"
                    )
                }
            }
        };
        commands.push(check_cmd);
    }

    let batch = commands.join("; ");
    let full_cmd = format!("{}{batch}", remote::SHELL_PATH_PREAMBLE);

    let output = ssh.run(&full_cmd).unwrap_or_default();

    let results: Vec<&str> = output.split(marker).filter(|s| !s.is_empty()).collect();

    for (i, def) in defs.iter().enumerate() {
        let status = if let Some(result) = results.get(i) {
            let trimmed = result.trim().trim_start_matches(':');
            if trimmed == "PING_OK" {
                HealthStatus::Healthy
            } else if trimmed == "PING_FAIL" {
                HealthStatus::Down
            } else {
                let code = trimmed.parse::<u16>().unwrap_or(0);
                proxmox_status_from_http(code)
            }
        } else {
            HealthStatus::Down
        };

        let _ = tx.send(HealthUpdate::ServiceResult(ServiceHealthResult {
            name: def.name.to_string(),
            group: def.group.to_string(),
            status,
        }));
    }
}

/// Non-Proxmox parallel health checks (Docker local/remote).
fn run_health_checks_parallel(
    defs: &[ServiceHealthDef],
    host: &str,
    prefix: &str,
    ssh_details: &Option<(String, String, String)>,
    network_base: &str,
    tx: &mpsc::Sender<HealthUpdate>,
) {
    let mut handles = Vec::new();

    for def in defs {
        let def = def.clone();
        let host = host.to_string();
        let prefix = prefix.to_string();
        let ssh_details = ssh_details.clone();
        let network_base = network_base.to_string();
        let tx = tx.clone();

        let handle = std::thread::spawn(move || {
            let ssh = ssh_details.as_ref().map(|(h, u, k)| {
                SshConnection::new(h, u, k)
            });

            let status = check_service(
                &def,
                &host,
                &prefix,
                ssh.as_ref(),
                false,
                &network_base,
            );

            let _ = tx.send(HealthUpdate::ServiceResult(ServiceHealthResult {
                name: def.name.to_string(),
                group: def.group.to_string(),
                status,
            }));
        });
        handles.push(handle);
    }

    for handle in handles {
        let _ = handle.join();
    }
}

/// Aggregate individual service results into group-level health.
pub fn aggregate_groups(results: &[ServiceHealthResult]) -> Vec<GroupHealth> {
    let group_order = ["Core Services", "APIs", "LLM", "Apps"];
    let mut groups: Vec<GroupHealth> = Vec::new();

    for group_name in &group_order {
        let services: Vec<&ServiceHealthResult> = results
            .iter()
            .filter(|r| r.group == *group_name)
            .collect();

        if services.is_empty() {
            continue;
        }

        let total = services.len();
        let healthy = services
            .iter()
            .filter(|s| s.status == HealthStatus::Healthy)
            .count();
        let any_checking = services
            .iter()
            .any(|s| s.status == HealthStatus::Checking);

        let status = if any_checking {
            HealthStatus::Checking
        } else if healthy == total {
            HealthStatus::Healthy
        } else if healthy > 0 {
            HealthStatus::Unhealthy
        } else {
            HealthStatus::Down
        };

        groups.push(GroupHealth {
            name: group_name.to_string(),
            healthy,
            total,
            status,
        });
    }

    groups
}

/// Determine deployment state from health results (replaces docker ps parsing).
pub fn deployment_state_from_health(results: &[ServiceHealthResult]) -> crate::app::DeploymentState {
    use crate::app::DeploymentState;

    if results.is_empty() || results.iter().all(|r| r.status == HealthStatus::Checking) {
        return DeploymentState::Checking;
    }

    let is_up = |name: &str| -> bool {
        results
            .iter()
            .any(|r| r.name == name && r.status == HealthStatus::Healthy)
    };

    let total_healthy = results
        .iter()
        .filter(|r| r.status == HealthStatus::Healthy)
        .count();

    if total_healthy == 0 {
        return DeploymentState::None;
    }

    let bootstrap_done = is_up("postgres")
        && is_up("authz")
        && is_up("deploy")
        && is_up("proxy")
        && is_up("portal");

    let full_platform = bootstrap_done && is_up("agent") && is_up("litellm");

    if full_platform {
        DeploymentState::Complete
    } else if bootstrap_done {
        DeploymentState::BootstrapComplete
    } else {
        DeploymentState::Partial(total_healthy)
    }
}

/// Build and run health checks for the given profile context.
/// Returns a receiver for async processing in the main loop.
pub fn start_health_checks(
    is_remote: bool,
    is_mlx: bool,
    host: &str,
    prefix: &str,
    ssh_details: Option<(String, String, String)>,
    is_proxmox: bool,
    network_base: &str,
    vllm_network_base: &str,
) -> mpsc::Receiver<HealthUpdate> {
    let (tx, rx) = mpsc::channel();
    let defs = all_service_defs(is_mlx);
    let host = if is_remote {
        host.to_string()
    } else {
        "localhost".to_string()
    };

    run_health_checks(defs, host, prefix.to_string(), ssh_details, is_proxmox, network_base.to_string(), vllm_network_base.to_string(), tx);
    rx
}
