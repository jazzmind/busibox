use crate::modules::hardware::{LlmBackend, MemoryTier};
use color_eyre::Result;
use serde::Deserialize;
use std::collections::HashMap;
use std::fmt;
use std::path::Path;
use std::sync::mpsc;

#[derive(Debug, Clone)]
#[allow(dead_code)]
pub struct ModelRecommendation {
    pub tier: MemoryTier,
    pub tier_description: String,
    pub fast: ModelInfo,
    pub agent: ModelInfo,
    pub embed: ModelInfo,
    pub whisper: Option<ModelInfo>,
    pub kokoro: Option<ModelInfo>,
    pub flux: Option<ModelInfo>,
}

#[derive(Debug, Clone)]
pub struct ModelInfo {
    pub name: String,
    pub role: String,
    pub estimated_size_gb: f64,
}

/// A single unique model that needs to be loaded, with all the roles it serves
/// and the GPU it's currently assigned to (for vLLM multi-GPU setups).
#[derive(Debug, Clone)]
#[allow(dead_code)]
pub struct TierModel {
    pub model_key: String,
    pub model_name: String,
    pub roles: Vec<String>,
    pub estimated_size_gb: f64,
    pub provider: String,
    pub gpu: Option<String>,
    pub description: String,
    /// Whether this model runs on GPU (vllm/gpu provider) vs CPU (fastembed/local).
    pub needs_gpu: bool,
    /// Media model (provider=gpu with audio/image mode) -- shown read-only, pinned to GPU 0.
    pub is_media: bool,
    /// vLLM gpu_memory_utilization fraction (0.0-1.0), for VRAM headroom calculation.
    pub gpu_memory_utilization: Option<f64>,
    /// Tensor parallel size from model_config.yml (1 = no TP, >1 = TP across GPUs).
    pub tensor_parallel: Option<u16>,
}

/// Full tier breakdown: all unique models for a tier+backend, with GPU info.
#[derive(Debug, Clone)]
#[allow(dead_code)]
pub struct TierModelSet {
    pub tier: MemoryTier,
    pub tier_description: String,
    pub backend: LlmBackend,
    pub models: Vec<TierModel>,
}

// --- Deployed model status (hybrid dashboard) ---

#[derive(Debug, Clone)]
#[allow(dead_code)]
pub struct DeployedModel {
    pub model_name: String,
    pub model_key: String,
    pub provider: String,
    pub gpu: String,
    pub port: u16,
    pub tensor_parallel: u16,
    pub assigned: bool,
    pub live_status: LiveStatus,
}

#[derive(Debug, Clone, PartialEq)]
pub enum LiveStatus {
    Unknown,
    Checking,
    Running,
    Down,
    Error(String),
}

impl fmt::Display for LiveStatus {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            LiveStatus::Unknown => write!(f, "unknown"),
            LiveStatus::Checking => write!(f, "checking..."),
            LiveStatus::Running => write!(f, "running"),
            LiveStatus::Down => write!(f, "down"),
            LiveStatus::Error(e) => write!(f, "error: {e}"),
        }
    }
}

#[derive(Debug, Clone)]
pub struct DeployedModelSet {
    pub models: Vec<DeployedModel>,
    pub loaded_from: String,
}

pub enum DeployedModelUpdate {
    ConfigLoaded(DeployedModelSet),
    ModelStatus { port: u16, status: LiveStatus },
    Complete,
}

#[derive(Debug, Deserialize)]
struct ModelConfigFile {
    models: Option<HashMap<String, ModelConfigEntry>>,
    model_purposes: Option<HashMap<String, String>>,
}

#[derive(Debug, Deserialize)]
struct ModelConfigEntry {
    provider: Option<String>,
    model_key: Option<String>,
    gpu: Option<serde_yaml::Value>,
    port: Option<u16>,
    tensor_parallel: Option<u16>,
    assigned: Option<bool>,
    #[serde(flatten)]
    _extra: HashMap<String, serde_yaml::Value>,
}

impl DeployedModelSet {
    /// Parse model_config.yml into a DeployedModelSet.
    pub fn from_model_config(path: &Path, source: &str) -> Result<Self> {
        let contents = std::fs::read_to_string(path)?;
        let file: ModelConfigFile = serde_yaml::from_str(&contents)?;

        let models_map = file.models.unwrap_or_default();
        let mut models: Vec<DeployedModel> = models_map
            .into_iter()
            .map(|(model_name, entry)| {
                let gpu = match &entry.gpu {
                    Some(serde_yaml::Value::Number(n)) => n.to_string(),
                    Some(serde_yaml::Value::String(s)) => s.clone(),
                    _ => String::new(),
                };
                DeployedModel {
                    model_name,
                    model_key: entry.model_key.unwrap_or_default(),
                    provider: entry.provider.unwrap_or_default(),
                    gpu,
                    port: entry.port.unwrap_or(0),
                    tensor_parallel: entry.tensor_parallel.unwrap_or(1),
                    assigned: entry.assigned.unwrap_or(false),
                    live_status: LiveStatus::Unknown,
                }
            })
            .collect();

        models.sort_by_key(|m| m.port);

        Ok(DeployedModelSet {
            models,
            loaded_from: source.to_string(),
        })
    }
}

/// Start loading deployed models in a background thread.
/// For remote profiles, reads model_config.yml from the remote via SSH cat.
/// For local profiles, reads directly from disk.
/// After config is loaded, queries each vLLM port for live status.
pub fn start_deployed_model_loading(
    repo_root: std::path::PathBuf,
    is_remote: bool,
    is_proxmox: bool,
    ssh_details: Option<(String, String, String)>,
    vllm_network_base: String,
) -> mpsc::Receiver<DeployedModelUpdate> {
    let (tx, rx) = mpsc::channel();

    std::thread::spawn(move || {
        let config_path = repo_root.join("provision/ansible/group_vars/all/model_config.yml");

        // Phase 1: Load model_config.yml
        let model_set = if is_remote {
            if let Some((ref host, ref user, ref key_path)) = ssh_details {
                let ssh = crate::modules::ssh::SshConnection::new(host, user, key_path);
                let remote_file = "busibox/provision/ansible/group_vars/all/model_config.yml";
                let cmd = format!("cat ~/{remote_file} 2>/dev/null");
                match ssh.run(&cmd) {
                    Ok(contents) if !contents.trim().is_empty() => {
                        let tmp_path = std::env::temp_dir().join("busibox-model-config.yml");
                        if std::fs::write(&tmp_path, &contents).is_ok() {
                            DeployedModelSet::from_model_config(&tmp_path, "remote").ok()
                        } else {
                            None
                        }
                    }
                    _ => {
                        // Fallback: try local copy
                        DeployedModelSet::from_model_config(&config_path, "local").ok()
                    }
                }
            } else {
                DeployedModelSet::from_model_config(&config_path, "local").ok()
            }
        } else {
            DeployedModelSet::from_model_config(&config_path, "local").ok()
        };

        let model_set = match model_set {
            Some(ms) => ms,
            None => {
                let _ = tx.send(DeployedModelUpdate::Complete);
                return;
            }
        };

        let _ = tx.send(DeployedModelUpdate::ConfigLoaded(model_set.clone()));

        // Phase 2: Query each vLLM model's port for live status
        let vllm_models: Vec<_> = model_set
            .models
            .iter()
            .filter(|m| m.provider == "vllm" && m.assigned && m.port > 0)
            .collect();

        for model in &vllm_models {
            let port = model.port;
            let _ = tx.send(DeployedModelUpdate::ModelStatus {
                port,
                status: LiveStatus::Checking,
            });

            let status = check_vllm_model_live(
                port,
                &model.model_name,
                is_remote,
                is_proxmox,
                &ssh_details,
                &vllm_network_base,
            );

            let _ = tx.send(DeployedModelUpdate::ModelStatus { port, status });
        }

        let _ = tx.send(DeployedModelUpdate::Complete);
    });

    rx
}

/// Query a single vLLM port to see if the expected model is loaded.
fn check_vllm_model_live(
    port: u16,
    _expected_model: &str,
    is_remote: bool,
    is_proxmox: bool,
    ssh_details: &Option<(String, String, String)>,
    vllm_network_base: &str,
) -> LiveStatus {
    let vllm_ip = if is_proxmox {
        // vLLM container is ID 208
        format!("{vllm_network_base}.208")
    } else {
        "localhost".to_string()
    };

    let curl_cmd = format!(
        "curl -s -o /dev/null -w '%{{http_code}}' --max-time 5 'http://{vllm_ip}:{port}/v1/models'"
    );

    let output = if is_remote {
        if let Some((ref host, ref user, ref key_path)) = ssh_details {
            let ssh = crate::modules::ssh::SshConnection::new(host, user, key_path);
            let full_cmd = format!(
                "{}{}",
                crate::modules::remote::SHELL_PATH_PREAMBLE,
                curl_cmd
            );
            ssh.run(&full_cmd).unwrap_or_default()
        } else {
            return LiveStatus::Error("no SSH details".into());
        }
    } else {
        // Local: run curl directly
        std::process::Command::new("sh")
            .arg("-c")
            .arg(&curl_cmd)
            .output()
            .map(|o| String::from_utf8_lossy(&o.stdout).to_string())
            .unwrap_or_default()
    };

    let code = output.trim();
    match code {
        "200" => LiveStatus::Running,
        "" => LiveStatus::Down,
        _ => {
            let code_num: u16 = code.parse().unwrap_or(0);
            if code_num >= 400 {
                LiveStatus::Error(format!("HTTP {code_num}"))
            } else if code_num > 0 {
                LiveStatus::Running
            } else {
                LiveStatus::Down
            }
        }
    }
}

// --- Model registry types ---

#[derive(Debug, Deserialize)]
struct ModelRegistryFile {
    available_models: Option<HashMap<String, AvailableModel>>,
    tiers: Option<HashMap<String, TierConfig>>,
    default_purposes: Option<HashMap<String, String>>,
    model_purposes: Option<HashMap<String, String>>,
}

#[derive(Debug, Deserialize)]
#[allow(dead_code)]
struct AvailableModel {
    model_name: Option<String>,
    memory_estimate_gb: Option<f64>,
    gpu_memory_utilization: Option<f64>,
    provider: Option<String>,
    description: Option<String>,
    gpu: Option<String>,
    mode: Option<String>,
    on_demand: Option<bool>,
    #[serde(flatten)]
    _extra: HashMap<String, serde_yaml::Value>,
}

#[derive(Debug, Deserialize)]
struct TierConfig {
    description: Option<String>,
    mlx: Option<std::collections::BTreeMap<String, String>>,
    vllm: Option<std::collections::BTreeMap<String, String>>,
    #[serde(flatten)]
    _extra: HashMap<String, serde_yaml::Value>,
}

impl TierModelSet {
    /// Load all unique models for a tier+backend from model_registry.yml.
    /// Deduplicates models that serve multiple roles.
    pub fn from_config(
        config_path: &Path,
        tier: MemoryTier,
        backend: &LlmBackend,
    ) -> Result<Self> {
        let contents = std::fs::read_to_string(config_path)?;
        let file: ModelRegistryFile = serde_yaml::from_str(&contents)?;

        let tiers = file.tiers.as_ref().ok_or_else(|| {
            color_eyre::eyre::eyre!("No 'tiers' section in model registry")
        })?;
        let available = file.available_models.as_ref().ok_or_else(|| {
            color_eyre::eyre::eyre!("No 'available_models' section in model registry")
        })?;

        let tier_name = tier.name();
        let tier_config = tiers.get(tier_name).ok_or_else(|| {
            color_eyre::eyre::eyre!("Tier '{}' not found in model registry", tier_name)
        })?;

        let backend_models = match backend {
            LlmBackend::Mlx => tier_config.mlx.as_ref(),
            LlmBackend::Vllm => tier_config.vllm.as_ref(),
            LlmBackend::Cloud => None,
        };

        let role_map = match backend_models {
            Some(m) => m,
            None => {
                return Ok(TierModelSet {
                    tier,
                    tier_description: tier_config
                        .description
                        .clone()
                        .unwrap_or_else(|| tier.description().to_string()),
                    backend: backend.clone(),
                    models: Vec::new(),
                });
            }
        };

        // BTreeMap iterates in sorted key order, giving stable output.
        // Group roles by model_key, preserving first-seen order for model_key.
        let mut key_order: Vec<String> = Vec::new();
        let mut key_roles: HashMap<String, Vec<String>> = HashMap::new();
        for (role, model_key) in role_map.iter() {
            key_roles
                .entry(model_key.clone())
                .or_insert_with(Vec::new)
                .push(role.clone());
            if !key_order.contains(model_key) {
                key_order.push(model_key.clone());
            }
        }

        let models: Vec<TierModel> = key_order
            .into_iter()
            .filter_map(|model_key| {
                let entry = available.get(&model_key)?;
                let model_name = entry.model_name.clone().unwrap_or_default();
                if model_name.is_empty() {
                    return None;
                }
                let size = entry
                    .memory_estimate_gb
                    .unwrap_or_else(|| estimate_model_size(&model_name));
                let provider = entry.provider.clone().unwrap_or_default();
                let description = entry.description.clone().unwrap_or_default();
                let gpu = entry.gpu.clone();
                let needs_gpu = matches!(
                    provider.to_lowercase().as_str(),
                    "vllm" | "gpu"
                );
                let is_media = provider.to_lowercase() == "gpu"
                    && entry.mode.as_ref().map(|m| {
                        matches!(m.as_str(), "audio_transcription" | "audio_speech" | "image_generation")
                    }).unwrap_or(false);
                let mut roles = key_roles.remove(&model_key).unwrap_or_default();
                roles.sort();
                Some(TierModel {
                    model_key,
                    model_name,
                    roles,
                    estimated_size_gb: size,
                    provider,
                    gpu,
                    description,
                    needs_gpu,
                    is_media,
                    gpu_memory_utilization: entry.gpu_memory_utilization,
                    tensor_parallel: None,
                })
            })
            .collect();

        Ok(TierModelSet {
            tier,
            tier_description: tier_config
                .description
                .clone()
                .unwrap_or_else(|| tier.description().to_string()),
            backend: backend.clone(),
            models,
        })
    }

    /// Build a TierModelSet from the deployed model_config.yml, enriched with
    /// metadata from model_registry.yml. This shows what's actually deployed
    /// rather than what a tier preset would configure.
    pub fn from_deployed_config(
        model_config_path: &Path,
        registry_path: &Path,
        backend: &LlmBackend,
    ) -> Result<Self> {
        let mc_contents = std::fs::read_to_string(model_config_path)?;
        let mc_file: ModelConfigFile = serde_yaml::from_str(&mc_contents)?;
        let mc_models = mc_file.models.unwrap_or_default();

        let reg_contents = std::fs::read_to_string(registry_path)?;
        let reg_file: ModelRegistryFile = serde_yaml::from_str(&reg_contents)?;
        let available = reg_file.available_models.unwrap_or_default();

        let defaults = reg_file.default_purposes.unwrap_or_default();
        let registry_overrides = reg_file.model_purposes.unwrap_or_default();
        let config_purposes = mc_file.model_purposes.unwrap_or_default();
        let mut merged_purposes: HashMap<String, String> = defaults;
        merged_purposes.extend(registry_overrides);
        // CLI-persisted purpose overrides from model_config.yml take highest priority
        if !config_purposes.is_empty() {
            merged_purposes.extend(config_purposes);
        }

        fn resolve_alias(
            key: &str,
            purposes: &HashMap<String, String>,
            available: &HashMap<String, AvailableModel>,
            depth: usize,
        ) -> String {
            if depth > 10 {
                return key.to_string();
            }
            if let Some(val) = purposes.get(key) {
                if val != key && purposes.contains_key(val.as_str()) && !available.contains_key(val.as_str()) {
                    return resolve_alias(val, purposes, available, depth + 1);
                }
                return val.clone();
            }
            key.to_string()
        }

        let mut resolved_purposes: HashMap<String, String> = HashMap::new();
        for purpose in merged_purposes.keys() {
            resolved_purposes.insert(
                purpose.clone(),
                resolve_alias(purpose, &merged_purposes, &available, 0),
            );
        }

        // Reverse map: model_key -> roles
        let mut key_to_roles: HashMap<String, Vec<String>> = HashMap::new();
        for (role, model_key) in &resolved_purposes {
            key_to_roles
                .entry(model_key.clone())
                .or_default()
                .push(role.clone());
        }

        let mut models: Vec<TierModel> = Vec::new();
        let mut seen_keys: Vec<String> = Vec::new();

        for (model_name, entry) in &mc_models {
            let provider = entry.provider.clone().unwrap_or_default();
            if !entry.assigned.unwrap_or(false) {
                continue;
            }
            let model_key = entry.model_key.clone().unwrap_or_default();
            if model_key.is_empty() || seen_keys.contains(&model_key) {
                continue;
            }
            seen_keys.push(model_key.clone());

            let gpu = match &entry.gpu {
                Some(serde_yaml::Value::Number(n)) => Some(n.to_string()),
                Some(serde_yaml::Value::String(s)) => Some(s.clone()),
                _ => None,
            };

            let avail_entry = available.get(&model_key);
            let description = avail_entry
                .and_then(|a| a.description.clone())
                .unwrap_or_default();
            let size = avail_entry
                .and_then(|a| a.memory_estimate_gb)
                .unwrap_or_else(|| estimate_model_size(model_name));
            let needs_gpu = matches!(provider.to_lowercase().as_str(), "vllm" | "gpu");
            let is_media = provider.to_lowercase() == "gpu"
                && avail_entry.and_then(|a| a.mode.as_ref()).map(|m| {
                    matches!(m.as_str(), "audio_transcription" | "audio_speech" | "image_generation")
                }).unwrap_or(false);
            let gmu = avail_entry.and_then(|a| a.gpu_memory_utilization);

            let mut roles = key_to_roles.remove(&model_key).unwrap_or_default();
            roles.sort();
            roles.dedup();

            models.push(TierModel {
                model_key,
                model_name: model_name.clone(),
                roles,
                estimated_size_gb: size,
                provider,
                gpu,
                description,
                needs_gpu,
                is_media,
                gpu_memory_utilization: gmu,
                tensor_parallel: entry.tensor_parallel,
            });
        }

        models.sort_by(|a, b| {
            let pa = if a.provider == "vllm" { 0 } else { 1 };
            let pb = if b.provider == "vllm" { 0 } else { 1 };
            pa.cmp(&pb).then_with(|| a.model_key.cmp(&b.model_key))
        });

        Ok(TierModelSet {
            tier: MemoryTier::Standard,
            tier_description: "Custom (deployed configuration)".to_string(),
            backend: backend.clone(),
            models,
        })
    }

    /// Append GPU media models from the registry that aren't already in this set.
    /// Media models are always pinned to GPU 0 and shown as read-only.
    pub fn append_media_models(&mut self, registry_path: &Path) {
        let contents = match std::fs::read_to_string(registry_path) {
            Ok(c) => c,
            Err(_) => return,
        };
        let file: ModelRegistryFile = match serde_yaml::from_str(&contents) {
            Ok(f) => f,
            Err(_) => return,
        };
        let available = match file.available_models {
            Some(a) => a,
            None => return,
        };

        let existing_keys: Vec<String> = self.models.iter().map(|m| m.model_key.clone()).collect();

        for (key, entry) in &available {
            let provider = entry.provider.as_deref().unwrap_or("");
            if provider != "gpu" {
                continue;
            }
            let is_media = entry.mode.as_ref().map(|m| {
                matches!(m.as_str(), "audio_transcription" | "audio_speech" | "image_generation")
            }).unwrap_or(false);
            if !is_media {
                continue;
            }
            // Only include GPU-variant media models (not MLX)
            if !key.ends_with("-gpu") {
                continue;
            }
            if existing_keys.contains(key) {
                continue;
            }
            let model_name = entry.model_name.clone().unwrap_or_default();
            if model_name.is_empty() {
                continue;
            }
            let size = entry.memory_estimate_gb.unwrap_or_else(|| estimate_model_size(&model_name));
            let mode_str = entry.mode.as_deref().unwrap_or("");
            let role = match mode_str {
                "audio_transcription" => "transcribe",
                "audio_speech" => "voice",
                "image_generation" => "image",
                _ => continue,
            };

            self.models.push(TierModel {
                model_key: key.clone(),
                model_name,
                roles: vec![role.to_string()],
                estimated_size_gb: size,
                provider: provider.to_string(),
                gpu: Some("0".to_string()),
                description: entry.description.clone().unwrap_or_default(),
                needs_gpu: true,
                is_media: true,
                gpu_memory_utilization: entry.gpu_memory_utilization,
                tensor_parallel: None,
            });
        }
    }

    /// Load all available models matching the given backend from model_registry.yml.
    /// Used by the add-model picker to show models not already in the tier set.
    pub fn all_available_for_backend(
        registry_path: &Path,
        backend: &LlmBackend,
    ) -> Vec<TierModel> {
        let contents = match std::fs::read_to_string(registry_path) {
            Ok(c) => c,
            Err(_) => return Vec::new(),
        };
        let file: ModelRegistryFile = match serde_yaml::from_str(&contents) {
            Ok(f) => f,
            Err(_) => return Vec::new(),
        };
        let available = match file.available_models {
            Some(a) => a,
            None => return Vec::new(),
        };

        let target_providers: &[&str] = match backend {
            LlmBackend::Vllm => &["vllm", "gpu", "fastembed"],
            LlmBackend::Mlx => &["mlx", "fastembed", "local"],
            LlmBackend::Cloud => return Vec::new(),
        };

        let mut models: Vec<TierModel> = Vec::new();
        for (key, entry) in &available {
            let provider = entry.provider.as_deref().unwrap_or("").to_lowercase();
            if !target_providers.contains(&provider.as_str()) {
                continue;
            }
            let model_name = entry.model_name.clone().unwrap_or_default();
            if model_name.is_empty() {
                continue;
            }
            let size = entry.memory_estimate_gb.unwrap_or_else(|| estimate_model_size(&model_name));
            let needs_gpu = matches!(provider.as_str(), "vllm" | "gpu");
            let is_media = provider == "gpu"
                && entry.mode.as_ref().map(|m| {
                    matches!(m.as_str(), "audio_transcription" | "audio_speech" | "image_generation")
                }).unwrap_or(false);

            models.push(TierModel {
                model_key: key.clone(),
                model_name,
                roles: Vec::new(),
                estimated_size_gb: size,
                provider: entry.provider.clone().unwrap_or_default(),
                gpu: entry.gpu.clone(),
                description: entry.description.clone().unwrap_or_default(),
                needs_gpu,
                is_media,
                gpu_memory_utilization: entry.gpu_memory_utilization,
                tensor_parallel: None,
            });
        }

        models.sort_by(|a, b| a.model_key.cmp(&b.model_key));
        models
    }
}

impl ModelRecommendation {
    /// Load model recommendations from model_registry.yml based on hardware tier.
    pub fn from_config(
        config_path: &Path,
        tier: MemoryTier,
        backend: &LlmBackend,
    ) -> Result<Self> {
        let contents = std::fs::read_to_string(config_path)?;
        let file: ModelRegistryFile = serde_yaml::from_str(&contents)?;

        let tiers = file.tiers.as_ref().ok_or_else(|| {
            color_eyre::eyre::eyre!("No 'tiers' section in model registry")
        })?;
        let available = file.available_models.as_ref().ok_or_else(|| {
            color_eyre::eyre::eyre!("No 'available_models' section in model registry")
        })?;

        let tier_name = tier.name();
        let tier_config = tiers.get(tier_name).ok_or_else(|| {
            color_eyre::eyre::eyre!("Tier '{}' not found in model registry", tier_name)
        })?;

        let backend_models = match backend {
            LlmBackend::Mlx => tier_config.mlx.as_ref(),
            LlmBackend::Vllm => tier_config.vllm.as_ref(),
            LlmBackend::Cloud => None,
        };

        let resolve = |key: &str| -> String {
            available
                .get(key)
                .and_then(|m| m.model_name.clone())
                .unwrap_or_default()
        };

        let resolve_size = |key: &str| -> f64 {
            available
                .get(key)
                .and_then(|m| m.memory_estimate_gb)
                .unwrap_or_else(|| {
                    let name = available
                        .get(key)
                        .and_then(|m| m.model_name.clone())
                        .unwrap_or_default();
                    estimate_model_size(&name)
                })
        };

        let get_model = |role: &str| -> (String, f64) {
            backend_models
                .and_then(|bm| bm.get(role))
                .map(|key| (resolve(key), resolve_size(key)))
                .unwrap_or_default()
        };

        let get_optional_model = |role: &str| -> Option<ModelInfo> {
            backend_models
                .and_then(|bm| bm.get(role))
                .map(|key| {
                    let name = resolve(key);
                    let size = resolve_size(key);
                    ModelInfo {
                        name,
                        role: role.to_string(),
                        estimated_size_gb: size,
                    }
                })
                .filter(|m| !m.name.is_empty())
        };

        let (fast_name, fast_size) = get_model("fast");
        let (agent_name, agent_size) = get_model("agent");
        let (embed_name, embed_size) = {
            let (n, s) = get_model("embed");
            if n.is_empty() {
                ("nomic-ai/nomic-embed-text-v1.5".to_string(), 0.5)
            } else {
                (n, s)
            }
        };

        Ok(ModelRecommendation {
            tier,
            tier_description: tier_config
                .description
                .clone()
                .unwrap_or_else(|| tier.description().to_string()),
            fast: ModelInfo {
                name: fast_name,
                role: "fast".into(),
                estimated_size_gb: fast_size,
            },
            agent: ModelInfo {
                name: agent_name,
                role: "agent".into(),
                estimated_size_gb: agent_size,
            },
            embed: ModelInfo {
                name: embed_name,
                role: "embed".into(),
                estimated_size_gb: embed_size,
            },
            whisper: get_optional_model("whisper")
                .or_else(|| get_optional_model("transcribe")),
            kokoro: get_optional_model("kokoro")
                .or_else(|| get_optional_model("voice")),
            flux: get_optional_model("flux")
                .or_else(|| get_optional_model("image")),
        })
    }

    #[allow(dead_code)]
    pub fn total_size_gb(&self) -> f64 {
        let mut total = self.fast.estimated_size_gb
            + self.agent.estimated_size_gb
            + self.embed.estimated_size_gb;
        if let Some(ref m) = self.whisper {
            total += m.estimated_size_gb;
        }
        if let Some(ref m) = self.kokoro {
            total += m.estimated_size_gb;
        }
        if let Some(ref m) = self.flux {
            total += m.estimated_size_gb;
        }
        total
    }

    pub fn models(&self) -> Vec<&ModelInfo> {
        let mut v = vec![&self.fast, &self.agent, &self.embed];
        if let Some(ref m) = self.whisper {
            v.push(m);
        }
        if let Some(ref m) = self.kokoro {
            v.push(m);
        }
        if let Some(ref m) = self.flux {
            v.push(m);
        }
        v
    }
}

/// Check if a model is cached locally in the HuggingFace cache directory.
pub fn is_model_cached_locally(model_name: &str) -> bool {
    if model_name.is_empty() {
        return false;
    }
    let home = std::env::var("HOME").unwrap_or_else(|_| "/tmp".to_string());
    let cache_dir = format!("{home}/.cache/huggingface/hub");
    let model_dir_name = format!("models--{}", model_name.replace('/', "--"));
    let model_path = std::path::Path::new(&cache_dir).join(&model_dir_name);
    model_path.is_dir()
}

/// Check model cache status on a remote machine via SSH.
/// Returns a list of (model_name, role, is_cached) tuples.
pub fn check_remote_model_cache(
    ssh: &crate::modules::ssh::SshConnection,
    models: &[(String, String)], // (name, role)
) -> Vec<(String, String, bool)> {
    let mut results = Vec::new();
    for (name, role) in models {
        if name.is_empty() {
            continue;
        }
        let model_dir_name = format!("models--{}", name.replace('/', "--"));
        let cmd = format!(
            "[ -d \"$HOME/.cache/huggingface/hub/{model_dir_name}\" ] && echo CACHED || echo MISSING"
        );
        let cached = ssh
            .run(&cmd)
            .map(|o| o.trim() == "CACHED")
            .unwrap_or(false);
        results.push((name.clone(), role.clone(), cached));
    }

    results
}

/// Rough estimate of model download size based on the model name.
fn estimate_model_size(name: &str) -> f64 {
    if name.is_empty() {
        return 0.0;
    }
    let lower = name.to_lowercase();
    if lower.contains("nomic") {
        0.5
    } else if lower.contains("235b") {
        65.0
    } else if lower.contains("80b") {
        45.0
    } else if lower.contains("72b") {
        40.0
    } else if lower.contains("70b") {
        40.0
    } else if lower.contains("32b") || lower.contains("30b") {
        18.0
    } else if lower.contains("14b") {
        8.0
    } else if lower.contains("7b") {
        4.0
    } else if lower.contains("4b") {
        2.5
    } else if lower.contains("3b") {
        2.0
    } else if lower.contains("1.5b") {
        1.0
    } else if lower.contains("0.5b") || lower.contains("0.6b") {
        0.3
    } else if lower.contains("whisper-tiny") {
        0.15
    } else if lower.contains("whisper-large") {
        3.0
    } else if lower.contains("kokoro") {
        0.2
    } else if lower.contains("flux") {
        3.5
    } else {
        2.0
    }
}
