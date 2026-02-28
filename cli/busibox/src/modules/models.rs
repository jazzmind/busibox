use crate::modules::hardware::{LlmBackend, MemoryTier};
use color_eyre::Result;
use serde::Deserialize;
use std::collections::HashMap;
use std::path::Path;

#[derive(Debug, Clone)]
pub struct ModelRecommendation {
    pub tier: MemoryTier,
    pub tier_description: String,
    pub fast: ModelInfo,
    pub agent: ModelInfo,
    pub frontier: ModelInfo,
}

#[derive(Debug, Clone)]
pub struct ModelInfo {
    pub name: String,
    pub role: String,
    pub estimated_size_gb: f64,
}

#[derive(Debug, Deserialize)]
struct DemoModelsFile {
    tiers: HashMap<String, TierConfig>,
}

#[derive(Debug, Deserialize)]
struct TierConfig {
    description: Option<String>,
    mlx: Option<BackendModels>,
    vllm: Option<BackendModels>,
}

#[derive(Debug, Deserialize)]
struct BackendModels {
    fast: Option<String>,
    agent: Option<String>,
    frontier: Option<String>,
}

impl ModelRecommendation {
    /// Load model recommendations from config/demo-models.yaml based on hardware.
    pub fn from_config(
        config_path: &Path,
        tier: MemoryTier,
        backend: &LlmBackend,
    ) -> Result<Self> {
        let contents = std::fs::read_to_string(config_path)?;
        let file: DemoModelsFile = serde_yaml::from_str(&contents)?;

        let tier_name = tier.name();
        let tier_config = file.tiers.get(tier_name).ok_or_else(|| {
            color_eyre::eyre::eyre!("Tier '{}' not found in config", tier_name)
        })?;

        let backend_models = match backend {
            LlmBackend::Mlx => tier_config.mlx.as_ref(),
            LlmBackend::Vllm => tier_config.vllm.as_ref(),
            LlmBackend::Cloud => None,
        };

        let (fast_name, agent_name, frontier_name) = if let Some(models) = backend_models {
            (
                models.fast.clone().unwrap_or_default(),
                models.agent.clone().unwrap_or_default(),
                models.frontier.clone().unwrap_or_default(),
            )
        } else {
            (String::new(), String::new(), String::new())
        };

        Ok(ModelRecommendation {
            tier,
            tier_description: tier_config
                .description
                .clone()
                .unwrap_or_else(|| tier.description().to_string()),
            fast: ModelInfo {
                name: fast_name.clone(),
                role: "fast".into(),
                estimated_size_gb: estimate_model_size(&fast_name),
            },
            agent: ModelInfo {
                name: agent_name.clone(),
                role: "agent".into(),
                estimated_size_gb: estimate_model_size(&agent_name),
            },
            frontier: ModelInfo {
                name: frontier_name.clone(),
                role: "frontier".into(),
                estimated_size_gb: estimate_model_size(&frontier_name),
            },
        })
    }

    pub fn total_size_gb(&self) -> f64 {
        self.fast.estimated_size_gb + self.agent.estimated_size_gb + self.frontier.estimated_size_gb
    }

    pub fn models(&self) -> Vec<&ModelInfo> {
        vec![&self.fast, &self.agent, &self.frontier]
    }
}

/// Rough estimate of model download size based on the model name.
fn estimate_model_size(name: &str) -> f64 {
    if name.is_empty() {
        return 0.0;
    }
    let lower = name.to_lowercase();
    if lower.contains("235b") {
        65.0
    } else if lower.contains("72b") {
        40.0
    } else if lower.contains("70b") {
        40.0
    } else if lower.contains("32b") {
        18.0
    } else if lower.contains("14b") {
        8.0
    } else if lower.contains("7b") {
        4.0
    } else if lower.contains("3b") {
        2.0
    } else if lower.contains("1.5b") {
        1.0
    } else if lower.contains("0.5b") || lower.contains("0.6b") {
        0.3
    } else {
        2.0
    }
}
