pub mod docker;
pub mod k8s;
pub mod proxmox;

use busibox_core::deploy::{DeployContext, DeploymentState};
use color_eyre::Result;
use std::collections::HashMap;
use std::sync::mpsc::Sender;

#[derive(Debug, Clone, PartialEq)]
pub enum Action {
    Start,
    Stop,
    Restart,
    Logs,
    Status,
    Redeploy,
}

#[derive(Debug, Clone)]
pub enum ServiceStatus {
    Running,
    Stopped,
    Unknown,
    Error(String),
}

#[derive(Debug, Clone)]
pub struct CheckResult {
    pub name: String,
    pub passed: bool,
    pub message: String,
}

/// Unified interface for deployment backends (Docker, Proxmox, K8s, etc.).
///
/// Each backend knows how to deploy services, manage their lifecycle, and check
/// prerequisites for its specific infrastructure. Adding a new backend (e.g.,
/// AWS ECS) requires only implementing this trait — no changes to install or
/// manage screens.
pub trait Backend: Send + Sync {
    fn name(&self) -> &str;
    fn detect_installation(&self) -> Result<DeploymentState>;
    fn deploy_service(&self, service: &str, env: &HashMap<String, String>, tx: &Sender<String>) -> Result<i32>;
    fn service_action(&self, service: &str, action: &Action, env: &HashMap<String, String>) -> Result<()>;
    fn get_service_status(&self, service: &str) -> Result<ServiceStatus>;
    fn start_all(&self) -> Result<()>;
    fn stop_all(&self) -> Result<()>;
    fn prerequisite_checks(&self) -> Result<Vec<CheckResult>>;
    fn supported_services(&self) -> Vec<String>;
}

/// Create the appropriate backend for a DeployContext.
pub fn create_backend(ctx: DeployContext) -> Box<dyn Backend> {
    match ctx.backend.as_str() {
        "proxmox" => Box::new(proxmox::ProxmoxBackend::new(ctx)),
        "k8s" => Box::new(k8s::K8sBackend::new(ctx)),
        _ => Box::new(docker::DockerBackend::new(ctx)),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use busibox_core::profile::Profile;
    use std::path::PathBuf;

    fn make_ctx(backend: &str) -> DeployContext {
        let profile = Profile {
            environment: "development".to_string(),
            backend: backend.to_string(),
            label: "Test".to_string(),
            created: None,
            vault_prefix: Some("test".to_string()),
            remote: false,
            remote_host: None,
            remote_user: None,
            remote_ssh_key: None,
            remote_busibox_path: None,
            tailscale_ip: None,
            hardware: None,
            kubeconfig: None,
            model_tier: None,
            admin_email: None,
            allowed_email_domains: None,
            frontend_ref: None,
            site_domain: None,
            ssl_cert_name: None,
            network_base_octets: None,
            use_production_vllm: None,
            docker_runtime: None,
            github_token: None,
            cloud_provider: None,
            cloud_api_key: None,
            llm_backend_override: None,
            k8s_overlay: None,
            spot_token: None,
            dev_apps_dir: None,
            huggingface_token: None,
            direct_access: None,
            port_overrides: std::collections::HashMap::new(),
            service_preset: None,
            addon_packs: Vec::new(),
            local_llm_backend: None,
        };
        DeployContext::from_profile("test-id", &profile, PathBuf::from("/repo"), None)
    }

    #[test]
    fn create_backend_returns_docker_for_docker() {
        let backend = create_backend(make_ctx("docker"));
        assert_eq!(backend.name(), "docker");
    }

    #[test]
    fn create_backend_returns_proxmox_for_proxmox() {
        let backend = create_backend(make_ctx("proxmox"));
        assert_eq!(backend.name(), "proxmox");
    }

    #[test]
    fn create_backend_returns_k8s_for_k8s() {
        let backend = create_backend(make_ctx("k8s"));
        assert_eq!(backend.name(), "k8s");
    }

    #[test]
    fn create_backend_defaults_to_docker_for_unknown() {
        let backend = create_backend(make_ctx("something-else"));
        assert_eq!(backend.name(), "docker");
    }

    #[test]
    fn docker_backend_supported_services_not_empty() {
        let backend = create_backend(make_ctx("docker"));
        let services = backend.supported_services();
        assert!(!services.is_empty());
        assert!(services.contains(&"postgres".to_string()));
        assert!(services.contains(&"authz".to_string()));
    }

    #[test]
    fn k8s_backend_supported_services_includes_build() {
        let backend = create_backend(make_ctx("k8s"));
        let services = backend.supported_services();
        assert!(services.contains(&"build-server".to_string()));
        assert!(services.contains(&"registry".to_string()));
    }
}
