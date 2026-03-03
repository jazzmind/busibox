use crate::modules::hardware::{LlmBackend, MemoryTier};
use color_eyre::Result;
use serde::Deserialize;
use std::collections::HashMap;
use std::path::Path;

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

#[derive(Debug, Deserialize)]
struct ModelRegistryFile {
    available_models: Option<HashMap<String, AvailableModel>>,
    tiers: Option<HashMap<String, TierConfig>>,
}

#[derive(Debug, Deserialize)]
struct AvailableModel {
    model_name: Option<String>,
    memory_estimate_gb: Option<f64>,
    provider: Option<String>,
    description: Option<String>,
    gpu: Option<String>,
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
