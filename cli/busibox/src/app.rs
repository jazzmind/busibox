use crate::modules::benchmark::{self, BenchmarkConfig, BenchmarkResult};
use crate::modules::hardware::HardwareProfile;
use crate::modules::health::{GroupHealth, HealthUpdate, ServiceHealthResult};
use crate::modules::models::{DeployedModel, DeployedModelSet, DeployedModelUpdate, ModelDownloadUpdate, ModelRecommendation, TierModel, TierModelSet};
use crate::modules::profile::{Profile, ProfilesFile};
use crate::modules::ssh::SshConnection;
use crate::modules::tailscale::TailscaleStatus;
use std::sync::mpsc;

#[derive(Debug, Clone, PartialEq)]
#[allow(dead_code)]
pub enum Screen {
    Welcome,
    SetupMode,
    SshSetup,
    TailscaleSetup,
    HardwareReport,
    ModelConfig,
    ModelDownload,
    Install,
    Manage,
    ModelsManage,
    ModelBenchmark,
    ProfileSelect,
    ProfileEdit,
    AdminLogin,
    K8sSetup,
    K8sManage,
    ValidateSecrets,
}

#[derive(Debug, Clone, PartialEq)]
pub enum SetupTarget {
    Local,
    Remote,
}

#[derive(Debug, Clone, PartialEq)]
#[allow(dead_code)]
pub enum TailscaleAuthChoice {
    Cloud,
    Headscale,
    Skip,
}

#[derive(Debug)]
pub struct App {
    pub screen: Screen,
    pub should_quit: bool,
    pub repo_root: std::path::PathBuf,

    // Setup state
    pub setup_target: SetupTarget,
    pub remote_host_input: String,
    pub remote_user_input: String,
    pub remote_path_input: String,
    pub admin_email_input: String,
    pub remote_backend_choice: usize,
    pub remote_env_choice: usize,

    // SSH state
    pub ssh_status: SshSetupStatus,
    pub ssh_connection: Option<SshConnection>,

    // Tailscale state
    pub tailscale_local: Option<TailscaleStatus>,
    pub tailscale_remote: Option<TailscaleStatus>,
    pub tailscale_auth_choice: TailscaleAuthChoice,
    pub tailscale_auth_input: String,
    pub tailscale_step: TailscaleStep,

    // Hardware state
    pub local_hardware: Option<HardwareProfile>,
    pub remote_hardware: Option<HardwareProfile>,

    // Model state
    pub model_recommendation: Option<ModelRecommendation>,
    pub model_download_progress: Vec<ModelDownloadState>,
    pub model_cache_status: Vec<ModelCacheEntry>,
    pub model_cache_check_state: ModelCacheCheckState,
    pub active_tier_models: Option<TierModelSet>,

    // Background model download state (auto-download from welcome screen)
    pub model_bg_download_rx: Option<mpsc::Receiver<ModelDownloadUpdate>>,
    pub model_bg_download_active: bool,

    // Deployed models state (hybrid dashboard)
    pub deployed_models: Option<DeployedModelSet>,
    pub deployed_models_rx: Option<mpsc::Receiver<DeployedModelUpdate>>,
    pub deployed_models_loading: bool,

    // Models manage screen state
    pub models_manage_tier_selected: usize,
    pub models_manage_loaded: bool,
    pub models_manage_current_tier: Option<String>,
    /// When true, show the "Custom" tier entry sourced from deployed
    /// model_config.yml alongside standard presets.
    pub models_manage_is_custom: bool,
    pub models_manage_model_selected: usize,
    pub models_manage_focus: ModelsFocus,
    pub models_manage_gpu_assignments: std::collections::HashMap<String, GpuAssignment>,
    pub models_manage_gpu_saved: Option<std::collections::HashMap<String, GpuAssignment>>,
    pub models_manage_tier_models: Option<TierModelSet>,
    pub models_manage_log: Vec<String>,
    pub models_manage_log_visible: bool,
    pub models_manage_log_scroll: usize,
    pub models_manage_log_autoscroll: bool,
    pub models_manage_action_running: bool,
    pub models_manage_action_complete: bool,
    /// True when the in-memory config has unsaved changes vs model_config.yml
    pub models_manage_config_dirty: bool,
    /// True when the saved model_config.yml differs from what is actually running on GPUs
    pub models_manage_config_undeployed: bool,
    pub models_manage_tick: usize,
    pub models_manage_rx: Option<mpsc::Receiver<ModelsManageUpdate>>,

    // Add-model picker state
    pub models_manage_add_mode: bool,
    pub models_manage_add_candidates: Vec<TierModel>,
    pub models_manage_add_selected: usize,

    // Role/purpose editor state
    pub models_manage_role_edit_mode: bool,
    pub models_manage_role_edit_selected: usize,
    pub models_manage_available_roles: Vec<String>,

    pub models_manage_change_inherit_roles: Option<Vec<String>>,
    pub models_manage_change_inherit_gpu: Option<GpuAssignment>,
    pub models_manage_change_insert_index: Option<usize>,

    // Benchmark screen state
    pub benchmark_models: Vec<DeployedModel>,
    pub benchmark_selected: usize,
    pub benchmark_toggled: Vec<bool>,
    pub benchmark_results: Vec<BenchmarkResult>,
    pub benchmark_log: Vec<String>,
    pub benchmark_log_scroll: usize,
    pub benchmark_running: bool,
    pub benchmark_complete: bool,
    pub benchmark_tick: usize,
    pub benchmark_rx: Option<mpsc::Receiver<BenchmarkUpdate>>,
    pub benchmark_config: BenchmarkConfig,
    pub benchmark_mode: benchmark::BenchmarkMode,
    pub benchmark_model_test_results: Vec<benchmark::ModelTestResult>,

    // Install state
    pub install_services: Vec<ServiceInstallState>,
    pub install_log: Vec<String>,
    pub install_log_visible: bool,
    pub install_log_scroll: usize,
    pub install_log_autoscroll: bool,
    pub install_tick: usize,
    pub install_complete: bool,
    pub install_model_status: Vec<(String, String, ModelInstallState)>, // (role, model_name, state)
    pub install_models_complete: bool,
    pub install_portal_url: Option<String>,
    pub install_rx: Option<mpsc::Receiver<InstallUpdate>>,
    pub install_waiting_retry: Option<std::sync::mpsc::Sender<bool>>,
    pub install_prereq_hint: Vec<String>,
    pub install_waiting_confirm: Option<std::sync::mpsc::Sender<bool>>,
    pub install_confirm_prompt: String,
    pub install_waiting_token: Option<std::sync::mpsc::Sender<String>>,
    pub install_token_input: String,
    pub install_token_message: String,
    pub install_token_error: String,

    // Manage state
    pub manage_services: Vec<ServiceStatus>,
    pub manage_selected: usize,
    pub manage_log: Vec<String>,
    pub manage_log_visible: bool,
    pub manage_log_scroll: usize,
    pub manage_action_running: bool,
    pub manage_action_complete: bool,
    pub manage_tick: usize,
    pub manage_rx: Option<mpsc::Receiver<ManageUpdate>>,
    pub manage_waiting_confirm: Option<std::sync::mpsc::Sender<bool>>,
    pub manage_confirm_prompt: String,
    /// PID of the child process streaming live logs (docker logs -f / journalctl -f).
    /// Used to kill the stream when the user closes the log viewer.
    pub manage_log_child_pid: Option<u32>,
    /// When true, the log viewer auto-scrolls to follow new output.
    pub manage_log_autoscroll: bool,
    /// True when the log viewer is showing a live tail stream (not a completed action).
    pub manage_log_streaming: bool,

    // Scroll state (model download, hardware report, ssh setup)
    pub model_download_scroll: usize,
    pub hardware_report_scroll: usize,
    pub ssh_setup_scroll: usize,

    // Model tier selection (index into MemoryTier::all())
    pub model_tier_selected: usize,
    pub model_config_input_cursor: usize,
    pub site_domain_input: String,
    pub ssl_cert_name_input: String,

    // Cloud LLM configuration (ModelConfig screen)
    pub llm_mode_choice: usize,          // 0=Local (auto-detected), 1=Cloud (API)
    pub cloud_provider_choice: usize,    // 0=OpenAI, 1=Anthropic, 2=Bedrock
    pub cloud_api_key_input: String,

    // K8s setup state
    pub k8s_kubeconfig_input: String,
    pub k8s_overlay_input: String,
    pub k8s_spot_token_input: String,
    pub k8s_env_choice: usize,
    pub k8s_input_cursor: usize,

    // K8s manage state
    #[allow(dead_code)]
    pub k8s_manage_section: usize,
    pub k8s_manage_selected: usize,
    pub k8s_manage_log: Vec<String>,
    pub k8s_manage_log_visible: bool,
    pub k8s_manage_log_scroll: usize,
    pub k8s_manage_log_autoscroll: bool,
    pub k8s_manage_action_running: bool,
    pub k8s_manage_action_complete: bool,
    pub k8s_manage_tick: usize,
    pub k8s_manage_rx: Option<mpsc::Receiver<K8sManageUpdate>>,
    pub k8s_manage_input_mode: bool,
    pub k8s_manage_input_buffer: String,
    pub k8s_manage_input_label: String,
    #[allow(dead_code)]
    pub k8s_manage_input_tx: Option<std::sync::mpsc::Sender<String>>,

    // K8s cluster status (for welcome screen)
    pub k8s_cluster_status: K8sClusterStatus,
    pub k8s_cluster_info: Option<K8sClusterInfo>,
    pub k8s_cluster_rx: Option<mpsc::Receiver<K8sClusterUpdate>>,

    // Validate secrets screen state
    pub validate_secrets_results: Vec<crate::modules::remote::SecretKeyStatus>,
    pub validate_secrets_scroll: usize,
    pub validate_secrets_loading: bool,
    pub validate_secrets_rx: Option<mpsc::Receiver<ValidateSecretsUpdate>>,
    pub validate_secrets_vault_file: String,
    pub validate_secrets_is_remote: bool,
    pub validate_secrets_error: Option<String>,

    // Profile state
    pub profiles: Option<ProfilesFile>,
    pub profile_selected: usize,
    /// Held file handle for the active profile's advisory lock.
    /// Dropping this releases the lock so another instance can claim the profile.
    pub profile_lock: Option<std::fs::File>,

    // Profile delete confirmation
    pub profile_delete_confirming: bool,

    // Profile edit state
    pub profile_edit_field: usize,
    pub profile_edit_buffer: String,
    pub profile_editing: bool,
    pub profile_edit_id: Option<String>,
    pub profile_edit_tier_selecting: bool,
    pub profile_edit_tier_cursor: usize,

    // UI state
    pub menu_selected: usize,
    pub input_mode: InputMode,
    pub status_message: Option<(String, MessageKind)>,
    pub input_cursor: usize,

    // Interactive command request (for logs, etc.)
    pub pending_interactive_cmd: Option<String>,

    // Pending SSH key copy (needs TUI suspended for password prompt)
    pub pending_ssh_copy: Option<(String, String, String)>, // (key_path, host, user)

    // Deferred resume install (so status message renders before blocking SSH)
    pub pending_resume_install: bool,

    // Run `make login` after successful bootstrap install
    pub pending_login: bool,

    // Vault password decrypted in memory for the current install session
    pub vault_password: Option<String>,

    // Pending vault setup (needs TUI suspended for password prompts)
    pub pending_vault_setup: bool,

    // Pending profile export to remote host (needs TUI suspended for password prompts)
    pub pending_profile_export: bool,

    // Pending profile export to local file (needs TUI suspended for password prompts)
    pub pending_local_export: bool,

    // Pending master password change (needs TUI suspended)
    pub pending_password_change: bool,
    /// Profile ID for password change when triggered from profile select (else uses active)
    pub pending_password_change_profile: Option<String>,

    // Pending binary deployment to remote host
    pub pending_deploy_binary: bool,

    // Clean install: tear down all existing containers and volumes before installing
    pub clean_install: bool,

    // Update mode: redeploy ALL services (not just bootstrap) with latest code and secrets
    pub is_update: bool,

    // Clean install confirmation flow
    pub pending_clean_install_confirm: bool,
    pub clean_install_confirm_input: String,
    // Update confirmation flow
    pub pending_update_confirm: bool,
    pub update_confirm_input: String,

    // Health check state (welcome screen status panel)
    pub health_results: Vec<ServiceHealthResult>,
    pub health_groups: Vec<GroupHealth>,
    pub health_rx: Option<mpsc::Receiver<HealthUpdate>>,
    pub health_check_running: bool,
    pub health_tick: usize,

    // Install submenu / contextual action state
    pub deployment_state: DeploymentState,
    pub action_menu_selected: usize,

    // Admin login screen state
    pub admin_login_magic_link: Option<String>,
    pub admin_login_totp_code: Option<String>,
    pub admin_login_verify_url: Option<String>,
    pub admin_login_error: Option<String>,
    pub admin_login_loading: bool,
    pub admin_login_use_setup: bool,

    // Pending admin login generation
    pub pending_admin_login: bool,
    // Pending "Continue Install (Web)" flow: sync remote repo first, then run admin login.
    pub pending_sync_admin_login: bool,
    // Pending standalone code sync to remote host.
    pub pending_code_sync: bool,
    // Pending compare secrets between local and remote.
    pub pending_compare_secrets: bool,
    pub pending_mkcert_setup: bool,

    pub ssh_tunnel_process: Option<std::process::Child>,

    /// Persistent SSH tunnel (survives screen navigation). Forwards local:4443 → remote:443.
    pub ssh_tunnel_active: bool,
    pub ssh_tunnel_child: Option<std::process::Child>,
}

#[derive(Debug, Clone, PartialEq)]
pub enum InputMode {
    Normal,
    Editing,
}

#[derive(Debug, Clone, PartialEq)]
pub enum MessageKind {
    Info,
    Success,
    Warning,
    Error,
}

#[derive(Debug, Clone, PartialEq)]
#[allow(dead_code)]
pub enum SshSetupStatus {
    NotStarted,
    CheckingKeys,
    KeyFound(String),
    NoKeyFound,
    Generating,
    KeyGenerated(String),
    CopyingKey,
    Testing,
    Connected,
    Failed(String),
}

#[derive(Debug, Clone, PartialEq)]
pub enum TailscaleStep {
    CheckingLocal,
    CheckingRemote,
    InstallingRemote,
    Authenticating,
    Verifying,
    Done,
    Skipped,
}

#[derive(Debug, Clone)]
pub struct ModelDownloadState {
    pub name: String,
    pub role: String,
    pub progress: f64,
    pub status: DownloadStatus,
}

#[derive(Debug, Clone)]
pub struct ModelCacheEntry {
    pub name: String,
    pub role: String,
    pub cached: bool,
    pub downloading: bool,
}

#[derive(Debug, Clone, PartialEq)]
pub enum ModelCacheCheckState {
    NotChecked,
    Checking,
    Done,
    Failed,
}

#[derive(Debug, Clone, PartialEq)]
pub enum DownloadStatus {
    Pending,
    Downloading,
    Complete,
    Failed(String),
}

#[derive(Debug, Clone, PartialEq)]
#[allow(dead_code)]
pub enum ModelInstallState {
    Pending,
    Downloading,
    Cached,
    Skipped, // not configured for this tier
    Failed,
}

#[derive(Debug, Clone)]
#[allow(dead_code)]
pub struct ServiceInstallState {
    pub name: String,
    pub group: String,
    pub status: InstallStatus,
}

pub use busibox_core::deploy::DeploymentState;

#[derive(Debug, Clone, PartialEq)]
pub enum InstallStatus {
    Pending,
    Deploying,
    Healthy,
    Failed(String),
}

pub enum InstallUpdate {
    Log(String),
    ServiceStatus { name: String, status: InstallStatus },
    Complete { portal_url: Option<String> },
    /// Worker is paused waiting for user to press Enter (retry) or Esc (abort).
    /// `hint` lines are displayed prominently on the install screen.
    WaitForRetry {
        hint: Vec<String>,
        response: std::sync::mpsc::Sender<bool>,
    },
    /// Worker pauses and asks a yes/no question.
    WaitForConfirm {
        prompt: String,
        response: std::sync::mpsc::Sender<bool>,
    },
    /// Worker needs a GitHub token to continue.
    /// User types the token, presses Enter, and it's sent back.
    NeedGitHubToken {
        message: String,
        response: std::sync::mpsc::Sender<String>,
    },
}

#[derive(Debug)]
pub enum ManageUpdate {
    Log(String),
    StatusResult { name: String, status: String },
    VersionResult { name: String, version: String, commits_behind: Option<i32> },
    Complete { success: bool },
    /// Worker pauses and asks a yes/no question.
    /// The user's answer (true = yes/overwrite, false = no/keep) is sent back.
    WaitForConfirm {
        prompt: String,
        response: std::sync::mpsc::Sender<bool>,
    },
}

#[derive(Debug, Clone, PartialEq)]
pub enum ModelsFocus {
    Tiers,
    Models,
}

#[derive(Debug, Clone)]
pub struct GpuAssignment {
    pub gpus: Vec<usize>,
    pub tensor_parallel: bool,
}

impl GpuAssignment {
    pub fn display(&self) -> String {
        if self.gpus.is_empty() {
            return "auto".to_string();
        }
        let gpu_str: String = self
            .gpus
            .iter()
            .map(|g| g.to_string())
            .collect::<Vec<_>>()
            .join(",");
        if self.gpus.len() > 1 {
            let tp_label = if self.tensor_parallel { "TP" } else { "dup" };
            format!("{gpu_str} {tp_label}")
        } else {
            gpu_str
        }
    }

    pub fn env_gpu_value(&self) -> String {
        self.gpus
            .iter()
            .map(|g| g.to_string())
            .collect::<Vec<_>>()
            .join(",")
    }

    pub fn env_tp_value(&self) -> usize {
        if self.tensor_parallel {
            self.gpus.len()
        } else {
            1
        }
    }
}

#[derive(Debug)]
pub enum ModelsManageUpdate {
    Log(String),
    Complete { success: bool, deployed: bool },
}

pub enum BenchmarkUpdate {
    Log(String),
    Result(BenchmarkResult),
    ModelTestResult(benchmark::ModelTestResult),
    Complete,
}

#[derive(Debug)]
pub enum K8sManageUpdate {
    Log(String),
    Complete { #[allow(dead_code)] success: bool },
}

pub enum ValidateSecretsUpdate {
    Results {
        keys: Vec<crate::modules::remote::SecretKeyStatus>,
        local_error: Option<String>,
        remote_error: Option<String>,
    },
}

#[derive(Debug, Clone, PartialEq)]
pub enum K8sClusterStatus {
    Unknown,
    Checking,
    Connected,
    Disconnected,
    Error(String),
}

#[derive(Debug, Clone)]
pub struct K8sClusterInfo {
    pub server_url: String,
    #[allow(dead_code)]
    pub cluster_name: String,
    #[allow(dead_code)]
    pub namespace: String,
    pub node_count: usize,
    pub node_info: Vec<K8sNodeInfo>,
    pub pod_summary: Option<String>,
}

#[derive(Debug, Clone)]
pub struct K8sNodeInfo {
    pub name: String,
    pub status: String,
    #[allow(dead_code)]
    pub roles: String,
    pub version: String,
}

#[derive(Debug)]
pub enum K8sClusterUpdate {
    Status(K8sClusterStatus),
    Info(K8sClusterInfo),
    Complete,
}

impl std::fmt::Display for InstallStatus {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            InstallStatus::Pending => write!(f, "pending"),
            InstallStatus::Deploying => write!(f, "deploying"),
            InstallStatus::Healthy => write!(f, "healthy"),
            InstallStatus::Failed(e) => write!(f, "failed: {e}"),
        }
    }
}

#[derive(Debug, Clone)]
pub struct ServiceStatus {
    pub name: String,
    pub group: String,
    pub status: String,
    /// Deployed git commit (short SHA), or empty if unknown.
    pub version: String,
    /// How many commits behind HEAD this deploy is. None = unknown/checking.
    pub commits_behind: Option<i32>,
}

impl App {
    pub fn new(repo_root: std::path::PathBuf) -> Self {
        Self {
            screen: Screen::Welcome,
            should_quit: false,
            repo_root,
            setup_target: SetupTarget::Local,
            remote_host_input: String::new(),
            remote_user_input: "root".into(),
            remote_path_input: "~/busibox".into(),
            admin_email_input: String::new(),
            remote_backend_choice: 0,
            remote_env_choice: 0,
            ssh_status: SshSetupStatus::NotStarted,
            ssh_connection: None,
            tailscale_local: None,
            tailscale_remote: None,
            tailscale_auth_choice: TailscaleAuthChoice::Cloud,
            tailscale_auth_input: String::new(),
            tailscale_step: TailscaleStep::CheckingLocal,
            local_hardware: None,
            remote_hardware: None,
            model_recommendation: None,
            model_download_progress: Vec::new(),
            model_cache_status: Vec::new(),
            model_cache_check_state: ModelCacheCheckState::NotChecked,
            active_tier_models: None,
            model_bg_download_rx: None,
            model_bg_download_active: false,
            deployed_models: None,
            deployed_models_rx: None,
            deployed_models_loading: false,
            models_manage_tier_selected: 0,
            models_manage_loaded: false,
            models_manage_current_tier: None,
            models_manage_is_custom: false,
            models_manage_model_selected: 0,
            models_manage_focus: ModelsFocus::Tiers,
            models_manage_gpu_assignments: std::collections::HashMap::new(),
            models_manage_gpu_saved: None,
            models_manage_tier_models: None,
            models_manage_log: Vec::new(),
            models_manage_log_visible: false,
            models_manage_log_scroll: 0,
            models_manage_log_autoscroll: true,
            models_manage_action_running: false,
            models_manage_action_complete: false,
            models_manage_config_dirty: false,
            models_manage_config_undeployed: false,
            models_manage_tick: 0,
            models_manage_rx: None,
            models_manage_add_mode: false,
            models_manage_add_candidates: Vec::new(),
            models_manage_add_selected: 0,
            models_manage_role_edit_mode: false,
            models_manage_role_edit_selected: 0,
            models_manage_available_roles: Vec::new(),
            models_manage_change_inherit_roles: None,
            models_manage_change_inherit_gpu: None,
            models_manage_change_insert_index: None,
            benchmark_models: Vec::new(),
            benchmark_selected: 0,
            benchmark_toggled: Vec::new(),
            benchmark_results: Vec::new(),
            benchmark_log: Vec::new(),
            benchmark_log_scroll: 0,
            benchmark_running: false,
            benchmark_complete: false,
            benchmark_tick: 0,
            benchmark_rx: None,
            benchmark_config: BenchmarkConfig::default(),
            benchmark_mode: benchmark::BenchmarkMode::Performance,
            benchmark_model_test_results: Vec::new(),
            install_services: Vec::new(),
            install_log: Vec::new(),
            install_log_visible: false,
            install_log_scroll: 0,
            install_log_autoscroll: true,
            install_tick: 0,
            install_complete: false,
            install_model_status: Vec::new(),
            install_models_complete: false,
            install_portal_url: None,
            install_rx: None,
            install_waiting_retry: None,
            install_prereq_hint: Vec::new(),
            install_waiting_confirm: None,
            install_confirm_prompt: String::new(),
            install_waiting_token: None,
            install_token_input: String::new(),
            install_token_message: String::new(),
            install_token_error: String::new(),
            manage_services: Vec::new(),
            manage_selected: 0,
            manage_log: Vec::new(),
            manage_log_visible: false,
            manage_log_scroll: 0,
            manage_action_running: false,
            manage_action_complete: false,
            manage_tick: 0,
            manage_rx: None,
            manage_waiting_confirm: None,
            manage_confirm_prompt: String::new(),
            manage_log_child_pid: None,
            manage_log_autoscroll: true,
            manage_log_streaming: false,
            model_download_scroll: 0,
            hardware_report_scroll: 0,
            ssh_setup_scroll: 0,
            model_tier_selected: 0,
            model_config_input_cursor: 0,
            site_domain_input: "localhost".into(),
            ssl_cert_name_input: String::new(),
            llm_mode_choice: 0,
            cloud_provider_choice: 0,
            cloud_api_key_input: String::new(),
            k8s_kubeconfig_input: String::new(),
            k8s_overlay_input: "rackspace-spot".into(),
            k8s_spot_token_input: String::new(),
            k8s_env_choice: 0,
            k8s_input_cursor: 0,
            k8s_manage_section: 0,
            k8s_manage_selected: 0,
            k8s_manage_log: Vec::new(),
            k8s_manage_log_visible: false,
            k8s_manage_log_scroll: 0,
            k8s_manage_log_autoscroll: true,
            k8s_manage_action_running: false,
            k8s_manage_action_complete: false,
            k8s_manage_tick: 0,
            k8s_manage_rx: None,
            k8s_manage_input_mode: false,
            k8s_manage_input_buffer: String::new(),
            k8s_manage_input_label: String::new(),
            k8s_manage_input_tx: None,
            k8s_cluster_status: K8sClusterStatus::Unknown,
            k8s_cluster_info: None,
            k8s_cluster_rx: None,
            validate_secrets_results: Vec::new(),
            validate_secrets_scroll: 0,
            validate_secrets_loading: false,
            validate_secrets_rx: None,
            validate_secrets_vault_file: String::new(),
            validate_secrets_is_remote: false,
            validate_secrets_error: None,
            profiles: None,
            profile_selected: 0,
            profile_lock: None,
            profile_delete_confirming: false,
            profile_edit_field: 0,
            profile_edit_buffer: String::new(),
            profile_editing: false,
            profile_edit_id: None,
            profile_edit_tier_selecting: false,
            profile_edit_tier_cursor: 0,
            menu_selected: 0,
            input_mode: InputMode::Normal,
            status_message: None,
            input_cursor: 0,
            pending_interactive_cmd: None,
            pending_ssh_copy: None,
            pending_resume_install: false,
            pending_login: false,
            vault_password: None,
            pending_vault_setup: false,
            pending_profile_export: false,
            pending_local_export: false,
            pending_password_change: false,
            pending_password_change_profile: None,
            pending_deploy_binary: false,
            clean_install: false,
            is_update: false,
            pending_clean_install_confirm: false,
            clean_install_confirm_input: String::new(),
            pending_update_confirm: false,
            update_confirm_input: String::new(),
            health_results: Vec::new(),
            health_groups: Vec::new(),
            health_rx: None,
            health_check_running: false,
            health_tick: 0,
            deployment_state: DeploymentState::Unknown,
            action_menu_selected: 0,
            admin_login_magic_link: None,
            admin_login_totp_code: None,
            admin_login_verify_url: None,
            admin_login_error: None,
            admin_login_loading: false,
            admin_login_use_setup: false,
            pending_admin_login: false,
            pending_sync_admin_login: false,
            pending_code_sync: false,
            pending_compare_secrets: false,
            pending_mkcert_setup: false,
            ssh_tunnel_process: None,
            ssh_tunnel_active: false,
            ssh_tunnel_child: None,
        }
    }

    pub fn active_profile(&self) -> Option<(&str, &Profile)> {
        self.profiles
            .as_ref()
            .and_then(crate::modules::profile::get_active_profile)
    }

    pub fn has_profiles(&self) -> bool {
        self.profiles
            .as_ref()
            .map(|p| !p.profiles.is_empty())
            .unwrap_or(false)
    }

    #[allow(dead_code)]
    pub fn is_installed(&self) -> bool {
        self.active_profile()
            .and_then(|(_, p)| p.hardware.as_ref())
            .is_some()
    }

    pub fn set_message(&mut self, msg: &str, kind: MessageKind) {
        self.status_message = Some((msg.to_string(), kind));
    }

    pub fn clear_message(&mut self) {
        self.status_message = None;
    }

    pub fn kill_ssh_tunnel(&mut self) {
        if let Some(ref mut child) = self.ssh_tunnel_child {
            let _ = child.kill();
            let _ = child.wait();
        }
        self.ssh_tunnel_child = None;
        self.ssh_tunnel_active = false;
    }

    pub fn toggle_ssh_tunnel(&mut self) {
        if self.ssh_tunnel_active {
            self.kill_ssh_tunnel();
            self.set_message("SSH tunnel stopped", MessageKind::Info);
            return;
        }

        let profile = match self.active_profile() {
            Some((_, p)) if p.remote => p.clone(),
            _ => {
                self.set_message("SSH tunnel requires a remote profile", MessageKind::Warning);
                return;
            }
        };

        let host = match profile.effective_host() {
            Some(h) => h.to_string(),
            None => {
                self.set_message("No remote host configured", MessageKind::Warning);
                return;
            }
        };
        let user = profile.effective_user().to_string();
        let key = crate::modules::ssh::shellexpand_path(profile.effective_ssh_key());

        let mut args: Vec<String> = vec![
            "-N".into(),
            "-L".into(),
            "4443:localhost:443".into(),
            "-o".into(),
            "StrictHostKeyChecking=accept-new".into(),
            "-o".into(),
            "ExitOnForwardFailure=yes".into(),
        ];
        if !key.is_empty() && std::path::Path::new(&key).exists() {
            args.push("-i".into());
            args.push(key);
        }
        args.push(format!("{user}@{host}"));

        match std::process::Command::new("ssh")
            .args(&args)
            .stdin(std::process::Stdio::null())
            .stdout(std::process::Stdio::null())
            .stderr(std::process::Stdio::null())
            .spawn()
        {
            Ok(child) => {
                self.ssh_tunnel_child = Some(child);
                self.ssh_tunnel_active = true;
                self.set_message(
                    "SSH tunnel active — https://localhost:4443",
                    MessageKind::Success,
                );
            }
            Err(e) => {
                self.set_message(
                    &format!("Failed to start SSH tunnel: {e}"),
                    MessageKind::Error,
                );
            }
        }
    }

    pub fn backend_choices(&self) -> &[&str] {
        &["Docker", "Proxmox", "K8s (Rackspace Spot)"]
    }

    pub fn env_choices(&self) -> &[&str] {
        &["development", "staging", "production"]
    }

    #[allow(dead_code)]
    pub fn is_target_apple_silicon(&self) -> bool {
        let hw = if self.setup_target == SetupTarget::Remote {
            self.remote_hardware.as_ref()
        } else {
            self.local_hardware.as_ref()
        };
        hw.map(|h| h.apple_silicon).unwrap_or(false)
    }

    /// Context-sensitive actions for the welcome screen, based on deployment state.
    pub fn contextual_actions(&self) -> Vec<&str> {
        if !self.has_profiles() {
            return vec![];
        }

        let is_k8s = self.active_profile()
            .map(|(_, p)| p.backend == "k8s")
            .unwrap_or(false);

        if is_k8s {
            return match &self.deployment_state {
                DeploymentState::Unknown | DeploymentState::Checking => vec![],
                DeploymentState::None => vec!["Manage"],
                _ => vec!["Admin Login", "Manage"],
            };
        }

        let mut actions = match &self.deployment_state {
            DeploymentState::Unknown | DeploymentState::Checking => {
                vec![]
            }
            DeploymentState::None => {
                vec!["Install"]
            }
            DeploymentState::Partial(_) => {
                vec!["Continue Install", "Manage Services", "Update", "Clean Install"]
            }
            DeploymentState::BootstrapComplete => {
                vec!["Continue Install (Web)", "Admin Login", "Manage Services", "Update", "Clean Install"]
            }
            DeploymentState::Complete => {
                vec!["Admin Login", "Manage Services", "Benchmark Models", "Update", "Clean Install"]
            }
        };

        if self.vault_password.is_some() && !actions.is_empty() {
            actions.push("Validate Secrets");
        }

        if !actions.is_empty() && !crate::modules::mkcert::is_installed() {
            actions.push("Setup mkcert (TLS certs)");
        }

        actions
    }
}
