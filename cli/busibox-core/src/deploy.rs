use crate::hardware::LlmBackend;
use crate::profile::Profile;
use std::collections::HashMap;
use std::path::PathBuf;

#[derive(Debug, Clone, PartialEq)]
pub enum DeploymentState {
    Unknown,
    Checking,
    None,
    Partial(usize),
    BootstrapComplete,
    Complete,
}

/// Map environment name to container/vault prefix.
/// Must match scripts/make/service-deploy.sh get_container_prefix().
pub fn env_to_prefix(environment: &str) -> String {
    match environment {
        "demo" => "demo",
        "development" => "dev",
        "staging" => "staging",
        "production" => "prod",
        _ => "dev",
    }
    .to_string()
}

/// All deployment-relevant state extracted from a Profile.
///
/// This replaces the ~30 local variables that `install.rs` and `manage.rs`
/// extract from `App` before spawning worker threads. Both binaries
/// (full TUI and quick installer) can construct this from a `Profile`.
#[derive(Debug, Clone)]
pub struct DeployContext {
    pub repo_root: PathBuf,
    pub is_remote: bool,
    pub vault_password: Option<String>,

    // Profile identity
    pub profile_id: String,
    pub environment: String,
    pub backend: String,

    // Naming
    pub vault_prefix: String,
    pub container_prefix: String,

    // Remote
    pub remote_host: Option<String>,
    pub remote_user: Option<String>,
    pub remote_key: Option<String>,
    pub remote_path: Option<String>,

    // Hardware / LLM
    pub llm_backend: Option<String>,
    pub model_tier: Option<String>,
    pub docker_runtime: String,

    // Cloud LLM
    pub cloud_provider: Option<String>,
    pub cloud_api_key: Option<String>,

    // Network
    pub network_base_octets: Option<String>,
    pub site_domain: String,
    pub ssl_cert_name: Option<String>,

    // Auth
    pub admin_email: Option<String>,
    pub allowed_email_domains: Option<String>,

    // Frontend
    pub frontend_ref: Option<String>,

    // K8s
    pub kubeconfig: Option<String>,
    pub k8s_overlay: Option<String>,
    pub spot_token: Option<String>,

    // Dev
    pub dev_apps_dir: Option<String>,
    pub huggingface_token: Option<String>,
    pub github_token: Option<String>,
}

impl DeployContext {
    /// Build a DeployContext from a profile and repo root.
    pub fn from_profile(
        profile_id: &str,
        profile: &Profile,
        repo_root: PathBuf,
        vault_password: Option<String>,
    ) -> Self {
        let llm_backend = profile
            .llm_backend_override
            .clone()
            .or_else(|| {
                profile.hardware.as_ref().map(|h| match h.llm_backend {
                    LlmBackend::Mlx => "mlx".to_string(),
                    LlmBackend::Vllm => "vllm".to_string(),
                    LlmBackend::Cloud => "cloud".to_string(),
                })
            });

        Self {
            repo_root,
            is_remote: profile.remote,
            vault_password,
            profile_id: profile_id.to_string(),
            environment: profile.environment.clone(),
            backend: profile.backend.clone(),
            vault_prefix: profile
                .vault_prefix
                .clone()
                .unwrap_or_else(|| profile_id.to_string()),
            container_prefix: env_to_prefix(&profile.environment),
            remote_host: profile.effective_host().map(|s| s.to_string()),
            remote_user: profile.remote_user.clone(),
            remote_key: profile.remote_ssh_key.clone(),
            remote_path: Some(profile.effective_remote_path().to_string()),
            llm_backend,
            model_tier: profile.effective_model_tier().map(|t| t.name().to_string()),
            docker_runtime: profile.effective_docker_runtime().to_string(),
            cloud_provider: profile.cloud_provider.clone(),
            cloud_api_key: profile.cloud_api_key.clone(),
            network_base_octets: profile.network_base_octets.clone(),
            site_domain: profile
                .site_domain
                .clone()
                .filter(|d| !d.trim().is_empty())
                .unwrap_or_else(|| "localhost".to_string()),
            ssl_cert_name: profile.ssl_cert_name.clone().filter(|c| !c.trim().is_empty()),
            admin_email: profile.admin_email.clone(),
            allowed_email_domains: profile.allowed_email_domains.clone(),
            frontend_ref: profile.frontend_ref.clone(),
            kubeconfig: profile.kubeconfig.clone(),
            k8s_overlay: profile.k8s_overlay.clone(),
            spot_token: profile.spot_token.clone(),
            dev_apps_dir: profile.dev_apps_dir.clone(),
            huggingface_token: profile.huggingface_token.clone(),
            github_token: profile.github_token.clone(),
        }
    }

    pub fn is_mlx(&self) -> bool {
        self.llm_backend.as_deref() == Some("mlx")
    }

    pub fn is_k8s(&self) -> bool {
        self.backend == "k8s"
    }

    pub fn is_proxmox(&self) -> bool {
        self.backend == "proxmox"
    }

    pub fn is_docker(&self) -> bool {
        self.backend == "docker"
    }

    /// Build a map of environment variables for Ansible / make targets.
    pub fn make_env(&self) -> HashMap<String, String> {
        let mut env = HashMap::new();

        env.insert("BUSIBOX_ENV".to_string(), self.environment.clone());
        env.insert(
            "BUSIBOX_TARGET_BACKEND".to_string(),
            self.backend.clone(),
        );

        if let Some(ref vp) = self.vault_password {
            env.insert("ANSIBLE_VAULT_PASSWORD".to_string(), vp.clone());
        }
        if let Some(ref tier) = self.model_tier {
            env.insert("MODEL_TIER".to_string(), tier.clone());
        }
        if let Some(ref llm) = self.llm_backend {
            env.insert("LLM_BACKEND".to_string(), llm.clone());
        }
        if let Some(ref provider) = self.cloud_provider {
            env.insert("CLOUD_PROVIDER".to_string(), provider.clone());
        }
        if let Some(ref key) = self.cloud_api_key {
            match self.cloud_provider.as_deref() {
                Some("openai") => { env.insert("OPENAI_API_KEY".to_string(), key.clone()); }
                Some("anthropic") => { env.insert("ANTHROPIC_API_KEY".to_string(), key.clone()); }
                _ => { env.insert("CLOUD_API_KEY".to_string(), key.clone()); }
            }
        }
        if let Some(ref octets) = self.network_base_octets {
            env.insert("NETWORK_BASE_OCTETS".to_string(), octets.clone());
        }
        if let Some(ref email) = self.admin_email {
            env.insert("ADMIN_EMAIL".to_string(), email.clone());
        }
        if let Some(ref domains) = self.allowed_email_domains {
            env.insert("ALLOWED_DOMAINS".to_string(), domains.clone());
        }
        if let Some(ref fref) = self.frontend_ref {
            env.insert("FRONTEND_REF".to_string(), fref.clone());
        }
        if let Some(ref hf) = self.huggingface_token {
            env.insert("HF_TOKEN".to_string(), hf.clone());
        }
        env.insert("DOCKER_RUNTIME".to_string(), self.docker_runtime.clone());
        env.insert("SITE_DOMAIN".to_string(), self.site_domain.clone());
        if let Some(ref cert) = self.ssl_cert_name {
            env.insert("SSL_CERT_NAME".to_string(), cert.clone());
        }

        env
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::hardware::{Arch, HardwareProfile, LlmBackend, MemoryTier, Os};

    fn make_test_profile(backend: &str, environment: &str) -> Profile {
        Profile {
            environment: environment.to_string(),
            backend: backend.to_string(),
            label: "Test".to_string(),
            created: None,
            vault_prefix: Some("test-prefix".to_string()),
            remote: false,
            remote_host: None,
            remote_user: None,
            remote_ssh_key: None,
            remote_busibox_path: None,
            tailscale_ip: None,
            hardware: None,
            kubeconfig: None,
            model_tier: None,
            admin_email: Some("admin@example.com".to_string()),
            allowed_email_domains: Some("example.com".to_string()),
            frontend_ref: None,
            site_domain: Some("test.local".to_string()),
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
            port_overrides: HashMap::new(),
            service_preset: None,
            addon_packs: Vec::new(),
            local_llm_backend: None,
        }
    }

    #[test]
    fn env_to_prefix_maps_correctly() {
        assert_eq!(env_to_prefix("production"), "prod");
        assert_eq!(env_to_prefix("staging"), "staging");
        assert_eq!(env_to_prefix("development"), "dev");
        assert_eq!(env_to_prefix("demo"), "demo");
        assert_eq!(env_to_prefix("unknown"), "dev");
    }

    #[test]
    fn from_profile_basic_fields() {
        let profile = make_test_profile("docker", "production");
        let ctx = DeployContext::from_profile("my-id", &profile, PathBuf::from("/repo"), None);

        assert_eq!(ctx.profile_id, "my-id");
        assert_eq!(ctx.environment, "production");
        assert_eq!(ctx.backend, "docker");
        assert_eq!(ctx.vault_prefix, "test-prefix");
        assert_eq!(ctx.container_prefix, "prod");
        assert_eq!(ctx.site_domain, "test.local");
        assert!(!ctx.is_remote);
        assert!(ctx.vault_password.is_none());
    }

    #[test]
    fn from_profile_vault_prefix_defaults_to_id() {
        let mut profile = make_test_profile("docker", "development");
        profile.vault_prefix = None;
        let ctx = DeployContext::from_profile("my-id", &profile, PathBuf::from("/repo"), None);

        assert_eq!(ctx.vault_prefix, "my-id");
    }

    #[test]
    fn from_profile_site_domain_defaults_to_localhost() {
        let mut profile = make_test_profile("docker", "development");
        profile.site_domain = None;
        let ctx = DeployContext::from_profile("id", &profile, PathBuf::from("/repo"), None);
        assert_eq!(ctx.site_domain, "localhost");

        profile.site_domain = Some("  ".to_string());
        let ctx = DeployContext::from_profile("id", &profile, PathBuf::from("/repo"), None);
        assert_eq!(ctx.site_domain, "localhost");
    }

    #[test]
    fn from_profile_llm_backend_from_hardware() {
        let mut profile = make_test_profile("docker", "development");
        profile.hardware = Some(HardwareProfile {
            os: Os::Darwin,
            arch: Arch::Aarch64,
            ram_gb: 64,
            gpus: vec![],
            apple_silicon: true,
            docker_available: true,
            proxmox_available: false,
            llm_backend: LlmBackend::Mlx,
            memory_tier: MemoryTier::Standard,
        });
        let ctx = DeployContext::from_profile("id", &profile, PathBuf::from("/repo"), None);
        assert_eq!(ctx.llm_backend.as_deref(), Some("mlx"));
    }

    #[test]
    fn from_profile_llm_backend_override_wins() {
        let mut profile = make_test_profile("docker", "development");
        profile.hardware = Some(HardwareProfile {
            os: Os::Darwin,
            arch: Arch::Aarch64,
            ram_gb: 64,
            gpus: vec![],
            apple_silicon: true,
            docker_available: true,
            proxmox_available: false,
            llm_backend: LlmBackend::Mlx,
            memory_tier: MemoryTier::Standard,
        });
        profile.llm_backend_override = Some("cloud".to_string());
        let ctx = DeployContext::from_profile("id", &profile, PathBuf::from("/repo"), None);
        assert_eq!(ctx.llm_backend.as_deref(), Some("cloud"));
    }

    #[test]
    fn is_backend_helpers() {
        let docker = DeployContext::from_profile(
            "id", &make_test_profile("docker", "dev"), PathBuf::from("/r"), None);
        assert!(docker.is_docker());
        assert!(!docker.is_proxmox());
        assert!(!docker.is_k8s());

        let proxmox = DeployContext::from_profile(
            "id", &make_test_profile("proxmox", "prod"), PathBuf::from("/r"), None);
        assert!(proxmox.is_proxmox());
        assert!(!proxmox.is_docker());

        let k8s = DeployContext::from_profile(
            "id", &make_test_profile("k8s", "prod"), PathBuf::from("/r"), None);
        assert!(k8s.is_k8s());
    }

    #[test]
    fn is_mlx_helper() {
        let mut profile = make_test_profile("docker", "dev");
        profile.llm_backend_override = Some("mlx".to_string());
        let ctx = DeployContext::from_profile("id", &profile, PathBuf::from("/r"), None);
        assert!(ctx.is_mlx());

        let ctx2 = DeployContext::from_profile(
            "id", &make_test_profile("docker", "dev"), PathBuf::from("/r"), None);
        assert!(!ctx2.is_mlx());
    }

    #[test]
    fn make_env_always_includes_required_vars() {
        let profile = make_test_profile("docker", "production");
        let ctx = DeployContext::from_profile("id", &profile, PathBuf::from("/r"), None);
        let env = ctx.make_env();

        assert_eq!(env.get("BUSIBOX_ENV").unwrap(), "production");
        assert_eq!(env.get("BUSIBOX_TARGET_BACKEND").unwrap(), "docker");
        assert_eq!(env.get("SITE_DOMAIN").unwrap(), "test.local");
        assert!(env.contains_key("DOCKER_RUNTIME"));
    }

    #[test]
    fn make_env_includes_vault_password_when_set() {
        let profile = make_test_profile("docker", "dev");
        let ctx = DeployContext::from_profile(
            "id", &profile, PathBuf::from("/r"), Some("secret123".to_string()));
        let env = ctx.make_env();
        assert_eq!(env.get("ANSIBLE_VAULT_PASSWORD").unwrap(), "secret123");
    }

    #[test]
    fn make_env_omits_vault_password_when_none() {
        let profile = make_test_profile("docker", "dev");
        let ctx = DeployContext::from_profile("id", &profile, PathBuf::from("/r"), None);
        let env = ctx.make_env();
        assert!(!env.contains_key("ANSIBLE_VAULT_PASSWORD"));
    }

    #[test]
    fn make_env_includes_optional_fields_when_set() {
        let mut profile = make_test_profile("docker", "dev");
        profile.admin_email = Some("admin@test.com".to_string());
        profile.allowed_email_domains = Some("test.com".to_string());
        profile.frontend_ref = Some("v1.2.3".to_string());
        profile.huggingface_token = Some("hf_token".to_string());
        profile.network_base_octets = Some("10.96.200".to_string());
        profile.ssl_cert_name = Some("test-cert".to_string());

        let ctx = DeployContext::from_profile("id", &profile, PathBuf::from("/r"), None);
        let env = ctx.make_env();

        assert_eq!(env.get("ADMIN_EMAIL").unwrap(), "admin@test.com");
        assert_eq!(env.get("ALLOWED_DOMAINS").unwrap(), "test.com");
        assert_eq!(env.get("FRONTEND_REF").unwrap(), "v1.2.3");
        assert_eq!(env.get("HF_TOKEN").unwrap(), "hf_token");
        assert_eq!(env.get("NETWORK_BASE_OCTETS").unwrap(), "10.96.200");
        assert_eq!(env.get("SSL_CERT_NAME").unwrap(), "test-cert");
    }

    #[test]
    fn make_env_cloud_api_key_uses_provider_specific_var() {
        let mut profile = make_test_profile("docker", "dev");
        profile.cloud_provider = Some("openai".to_string());
        profile.cloud_api_key = Some("sk-test".to_string());
        let ctx = DeployContext::from_profile("id", &profile, PathBuf::from("/r"), None);
        let env = ctx.make_env();
        assert_eq!(env.get("OPENAI_API_KEY").unwrap(), "sk-test");
        assert!(!env.contains_key("CLOUD_API_KEY"));

        let mut profile2 = make_test_profile("docker", "dev");
        profile2.cloud_provider = Some("anthropic".to_string());
        profile2.cloud_api_key = Some("ant-key".to_string());
        let ctx2 = DeployContext::from_profile("id", &profile2, PathBuf::from("/r"), None);
        let env2 = ctx2.make_env();
        assert_eq!(env2.get("ANTHROPIC_API_KEY").unwrap(), "ant-key");

        let mut profile3 = make_test_profile("docker", "dev");
        profile3.cloud_provider = Some("other".to_string());
        profile3.cloud_api_key = Some("generic-key".to_string());
        let ctx3 = DeployContext::from_profile("id", &profile3, PathBuf::from("/r"), None);
        let env3 = ctx3.make_env();
        assert_eq!(env3.get("CLOUD_API_KEY").unwrap(), "generic-key");
    }
}
