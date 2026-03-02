use crate::modules::hardware::HardwareProfile;
use crate::modules::health::{GroupHealth, HealthUpdate, ServiceHealthResult};
use crate::modules::models::ModelRecommendation;
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
    ProfileSelect,
    ProfileEdit,
    AdminLogin,
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

    // Scroll state (model download, hardware report, ssh setup)
    pub model_download_scroll: usize,
    pub hardware_report_scroll: usize,
    pub ssh_setup_scroll: usize,

    // Model tier selection (index into MemoryTier::all())
    pub model_tier_selected: usize,
    pub model_config_email_focused: bool,

    // Profile state
    pub profiles: Option<ProfilesFile>,
    pub profile_selected: usize,

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

    // Pending profile export (needs TUI suspended for password prompts)
    pub pending_profile_export: bool,

    // Pending master password change (needs TUI suspended)
    pub pending_password_change: bool,
    /// Profile ID for password change when triggered from profile select (else uses active)
    pub pending_password_change_profile: Option<String>,

    // Pending binary deployment to remote host
    pub pending_deploy_binary: bool,

    // Clean install: tear down all existing containers and volumes before installing
    pub clean_install: bool,

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

    // Pending admin login generation
    pub pending_admin_login: bool,

    pub ssh_tunnel_process: Option<std::process::Child>,
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

#[derive(Debug, Clone, PartialEq)]
pub enum DeploymentState {
    Unknown,
    Checking,
    None,
    Partial(usize),       // some containers running, bootstrap not done
    BootstrapComplete,    // bootstrap services healthy (postgres, authz, deploy, proxy, core-apps)
    Complete,             // full platform deployed (agent, litellm, data, etc.)
}

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
    Complete { success: bool },
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
            model_download_scroll: 0,
            hardware_report_scroll: 0,
            ssh_setup_scroll: 0,
            model_tier_selected: 0,
            model_config_email_focused: false,
            profiles: None,
            profile_selected: 0,
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
            pending_password_change: false,
            pending_password_change_profile: None,
            pending_deploy_binary: false,
            clean_install: false,
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
            pending_admin_login: false,
            ssh_tunnel_process: None,
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

    pub fn backend_choices(&self) -> &[&str] {
        &["Docker", "Proxmox"]
    }

    pub fn env_choices(&self) -> &[&str] {
        &["staging", "production"]
    }

    /// Get the LLM service names based on detected hardware.
    /// Returns ("litellm", "vllm") or ("litellm", "mlx") depending on backend.
    /// Falls back to ("litellm", "vllm") if no hardware detected.
    pub fn llm_services(&self) -> Vec<&str> {
        use crate::modules::hardware::LlmBackend;
        let hw = if self.setup_target == SetupTarget::Remote {
            self.remote_hardware.as_ref()
        } else {
            self.local_hardware.as_ref()
        };
        match hw.map(|h| &h.llm_backend) {
            Some(LlmBackend::Mlx) => vec!["litellm", "mlx"],
            Some(LlmBackend::Cloud) => vec!["litellm"],
            _ => vec!["litellm", "vllm"],
        }
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
        match &self.deployment_state {
            DeploymentState::Unknown | DeploymentState::Checking => {
                vec![]
            }
            DeploymentState::None => {
                vec!["Install"]
            }
            DeploymentState::Partial(_) => {
                vec!["Continue Install", "Manage Services", "Clean Install"]
            }
            DeploymentState::BootstrapComplete => {
                vec!["Continue Install (Web)", "Admin Login", "Manage Services", "Clean Install"]
            }
            DeploymentState::Complete => {
                vec!["Admin Login", "Manage Services", "Update", "Clean Install"]
            }
        }
    }
}
