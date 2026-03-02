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
    },
    ServiceHealthDef {
        name: "redis",
        group: "Core Services",
        check: CheckMethod::Cli {
            command: "docker exec {PREFIX}-redis redis-cli ping 2>/dev/null",
        },
    },
    ServiceHealthDef {
        name: "minio",
        group: "Core Services",
        check: CheckMethod::Http {
            path: "/minio/health/live",
            port: 9000,
        },
    },
    ServiceHealthDef {
        name: "milvus",
        group: "Core Services",
        check: CheckMethod::Http {
            path: "/healthz",
            port: 9091,
        },
    },
    ServiceHealthDef {
        name: "neo4j",
        group: "Core Services",
        check: CheckMethod::Cli {
            command: "docker exec {PREFIX}-neo4j wget -q --spider http://localhost:7474 2>/dev/null && echo ok",
        },
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
    },
    ServiceHealthDef {
        name: "agent",
        group: "APIs",
        check: CheckMethod::Http {
            path: "/health",
            port: 8000,
        },
    },
    ServiceHealthDef {
        name: "data",
        group: "APIs",
        check: CheckMethod::Http {
            path: "/health",
            port: 8002,
        },
    },
    ServiceHealthDef {
        name: "data-worker",
        group: "APIs",
        check: CheckMethod::Cli {
            command: "docker ps --filter name=^{PREFIX}-data-worker$ --filter status=running --format '{{.Names}}' 2>/dev/null",
        },
    },
    ServiceHealthDef {
        name: "search",
        group: "APIs",
        check: CheckMethod::Http {
            path: "/health",
            port: 8003,
        },
    },
    ServiceHealthDef {
        name: "deploy",
        group: "APIs",
        check: CheckMethod::Http {
            path: "/health/live",
            port: 8011,
        },
    },
    ServiceHealthDef {
        name: "docs",
        group: "APIs",
        check: CheckMethod::Http {
            path: "/health/live",
            port: 8004,
        },
    },
    ServiceHealthDef {
        name: "embedding",
        group: "APIs",
        check: CheckMethod::Http {
            path: "/health",
            port: 8005,
        },
    },
    ServiceHealthDef {
        name: "bridge",
        group: "APIs",
        check: CheckMethod::Http {
            path: "/health",
            port: 8081,
        },
    },
];

const LLM_VLLM: ServiceHealthDef = ServiceHealthDef {
    name: "vllm",
    group: "LLM",
    check: CheckMethod::Http {
        path: "/health",
        port: 8000,
    },
};

const LLM_MLX: ServiceHealthDef = ServiceHealthDef {
    name: "mlx",
    group: "LLM",
    check: CheckMethod::Http {
        path: "/v1/models",
        port: 8080,
    },
};

const LLM_LITELLM: ServiceHealthDef = ServiceHealthDef {
    name: "litellm",
    group: "LLM",
    check: CheckMethod::Http {
        path: "/health/liveliness",
        port: 4000,
    },
};

const APP_SERVICES: &[ServiceHealthDef] = &[
    ServiceHealthDef {
        name: "proxy",
        group: "Apps",
        check: CheckMethod::Http {
            path: "/health",
            port: 80,
        },
    },
    ServiceHealthDef {
        name: "portal",
        group: "Apps",
        check: CheckMethod::Cli {
            command: "docker exec {PREFIX}-core-apps curl -sf -o /dev/null -w '%{http_code}' --max-time 3 http://localhost:3000/portal/api/health 2>/dev/null",
        },
    },
    ServiceHealthDef {
        name: "admin",
        group: "Apps",
        check: CheckMethod::Cli {
            command: "docker exec {PREFIX}-core-apps curl -sf -o /dev/null -w '%{http_code}' --max-time 3 http://localhost:3002/admin/api/health 2>/dev/null",
        },
    },
    ServiceHealthDef {
        name: "agents",
        group: "Apps",
        check: CheckMethod::Cli {
            command: "docker exec {PREFIX}-core-apps curl -sf -o /dev/null -w '%{http_code}' --max-time 3 http://localhost:3001/agents/api/health 2>/dev/null",
        },
    },
    ServiceHealthDef {
        name: "chat",
        group: "Apps",
        check: CheckMethod::Cli {
            command: "docker exec {PREFIX}-core-apps curl -sf -o /dev/null -w '%{http_code}' --max-time 3 http://localhost:3003/chat/api/health 2>/dev/null",
        },
    },
    ServiceHealthDef {
        name: "appbuilder",
        group: "Apps",
        check: CheckMethod::Cli {
            command: "docker exec {PREFIX}-core-apps curl -sf -o /dev/null -w '%{http_code}' --max-time 3 http://localhost:3004/builder/api/health 2>/dev/null",
        },
    },
    ServiceHealthDef {
        name: "media",
        group: "Apps",
        check: CheckMethod::Cli {
            command: "docker exec {PREFIX}-core-apps curl -sf -o /dev/null -w '%{http_code}' --max-time 3 http://localhost:3005/media/api/health 2>/dev/null",
        },
    },
    ServiceHealthDef {
        name: "documents",
        group: "Apps",
        check: CheckMethod::Cli {
            command: "docker exec {PREFIX}-core-apps curl -sf -o /dev/null -w '%{http_code}' --max-time 3 http://localhost:3006/documents/api/health 2>/dev/null",
        },
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
) -> HealthStatus {
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

/// Public wrapper for check_service, usable from manage screen.
pub fn check_service_pub(
    def: &ServiceHealthDef,
    host: &str,
    prefix: &str,
    ssh: Option<&SshConnection>,
) -> HealthStatus {
    check_service(def, host, prefix, ssh)
}

/// Run all health checks in parallel, sending results through the channel.
/// `host` is "localhost" for local profiles or the remote hostname.
/// `prefix` is the container prefix (e.g., "prod", "dev").
pub fn run_health_checks(
    defs: Vec<ServiceHealthDef>,
    host: String,
    prefix: String,
    ssh_details: Option<(String, String, String)>, // (host, user, key)
    tx: mpsc::Sender<HealthUpdate>,
) {
    std::thread::spawn(move || {
        let mut handles = Vec::new();

        for def in defs {
            let host = host.clone();
            let prefix = prefix.clone();
            let ssh_details = ssh_details.clone();
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

        let _ = tx.send(HealthUpdate::Complete);
    });
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
) -> mpsc::Receiver<HealthUpdate> {
    let (tx, rx) = mpsc::channel();
    let defs = all_service_defs(is_mlx);
    let host = if is_remote {
        host.to_string()
    } else {
        "localhost".to_string()
    };

    run_health_checks(defs, host, prefix.to_string(), ssh_details, tx);
    rx
}
