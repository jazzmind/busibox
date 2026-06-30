/// Curated model catalog for the HuggingFace model browser.
///
/// Each entry describes a specific HuggingFace repo + quantization that has been
/// validated to work with the named inference engines. The CLI ships this list as a
/// baseline; it can be augmented at runtime via the HF API when a token is available.

#[derive(Debug, Clone)]
pub struct CatalogModel {
    /// Short model family identifier (lowercase): "qwen", "deepseek", "gemma", etc.
    pub family: &'static str,
    /// HuggingFace repo id, e.g. "mlx-community/Qwen3.5-4B-4bit"
    pub hf_repo: &'static str,
    /// Display name shown in the TUI list
    pub display_name: &'static str,
    /// Parameter count in billions (0.8, 4.0, 8.0, 32.0, etc.)
    pub param_billions: f32,
    /// Quantization label shown to the user: "4-bit", "8-bit", "AWQ", "FP8", "GGUF Q4_K_M", etc.
    pub quantization: &'static str,
    /// Estimated RAM/VRAM consumption in GB when loaded
    pub size_gb: f32,
    /// Inference engines this variant is compatible with: "mlx", "vllm", "gguf"
    pub engines: &'static [&'static str],
    /// One-line description of capabilities / ideal use-case
    pub description: &'static str,
    /// Whether the repo requires a HuggingFace token to download
    pub gated: bool,
}

impl CatalogModel {
    /// True if this model fits inside `budget_gb` of available RAM.
    pub fn fits(&self, budget_gb: f32) -> bool {
        self.size_gb <= budget_gb
    }

    /// True if this model works with the given engine id.
    pub fn supports_engine(&self, engine: &str) -> bool {
        self.engines.contains(&engine)
    }
}

/// All curated models, grouped loosely by family then size.
/// Verified against model_registry.yml entries and community quantization repos.
pub const CATALOG: &[CatalogModel] = &[
    // =========================================================================
    // Qwen (Alibaba) — primary supported family, Apache-2.0, natively multimodal
    // =========================================================================

    // --- MLX variants (Apple Silicon) ---
    CatalogModel {
        family: "qwen",
        hf_repo: "mlx-community/Qwen3.5-0.8B-4bit",
        display_name: "Qwen3.5 0.8B (4-bit)",
        param_billions: 0.8,
        quantization: "4-bit",
        size_gb: 0.6,
        engines: &["mlx"],
        description: "Tiny dispatch/classify model. 262K ctx, tool calling, vision.",
        gated: false,
    },
    CatalogModel {
        family: "qwen",
        hf_repo: "mlx-community/Qwen3.5-4B-4bit",
        display_name: "Qwen3.5 4B (4-bit)",
        param_billions: 4.0,
        quantization: "4-bit",
        size_gb: 3.0,
        engines: &["mlx"],
        description: "Compact agent model. 262K ctx, tool calling, vision. Good for 24GB Macs.",
        gated: false,
    },
    CatalogModel {
        family: "qwen",
        hf_repo: "mlx-community/Qwen3.5-8B-4bit",
        display_name: "Qwen3.5 8B (4-bit)",
        param_billions: 8.0,
        quantization: "4-bit",
        size_gb: 5.5,
        engines: &["mlx"],
        description: "Mid-range dense model. Strong reasoning + tool use. 32GB+ Macs.",
        gated: false,
    },
    CatalogModel {
        family: "qwen",
        hf_repo: "mlx-community/Qwen3.5-14B-4bit",
        display_name: "Qwen3.5 14B (4-bit)",
        param_billions: 14.0,
        quantization: "4-bit",
        size_gb: 9.0,
        engines: &["mlx"],
        description: "Capable 14B. Excellent coding and reasoning. 32GB Macs.",
        gated: false,
    },
    CatalogModel {
        family: "qwen",
        hf_repo: "mlx-community/Qwen3.5-32B-4bit",
        display_name: "Qwen3.5 32B (4-bit)",
        param_billions: 32.0,
        quantization: "4-bit",
        size_gb: 20.0,
        engines: &["mlx"],
        description: "High-quality 32B dense. Strong at complex agentic tasks. 48GB+ Macs.",
        gated: false,
    },
    CatalogModel {
        family: "qwen",
        hf_repo: "unsloth/Qwen3.6-35B-A3B-UD-MLX-4bit",
        display_name: "Qwen3.6 35B-A3B MoE (4-bit)",
        param_billions: 35.0,
        quantization: "4-bit dynamic",
        size_gb: 23.0,
        engines: &["mlx"],
        description: "MoE flagship: 35B total / 3B active. Best quality on 48GB+ Macs.",
        gated: false,
    },
    CatalogModel {
        family: "qwen",
        hf_repo: "unsloth/Qwen3.6-35B-A3B-MLX-8bit",
        display_name: "Qwen3.6 35B-A3B MoE (8-bit)",
        param_billions: 35.0,
        quantization: "8-bit dynamic",
        size_gb: 38.0,
        engines: &["mlx"],
        description: "Near-lossless MoE. Best quality available on Apple Silicon. 96GB+ Macs.",
        gated: false,
    },

    // --- vLLM variants (NVIDIA GPU) ---
    CatalogModel {
        family: "qwen",
        hf_repo: "Qwen/Qwen3.5-0.8B",
        display_name: "Qwen3.5 0.8B (BF16)",
        param_billions: 0.8,
        quantization: "BF16",
        size_gb: 1.5,
        engines: &["vllm"],
        description: "Tiny dispatch model. Low VRAM footprint — fits alongside larger models.",
        gated: false,
    },
    CatalogModel {
        family: "qwen",
        hf_repo: "Qwen/Qwen3.5-4B",
        display_name: "Qwen3.5 4B (BF16)",
        param_billions: 4.0,
        quantization: "BF16",
        size_gb: 8.0,
        engines: &["vllm"],
        description: "Compact 4B. Tool calling + vision. Single 24GB GPU.",
        gated: false,
    },
    CatalogModel {
        family: "qwen",
        hf_repo: "Qwen/Qwen3.5-8B",
        display_name: "Qwen3.5 8B (BF16)",
        param_billions: 8.0,
        quantization: "BF16",
        size_gb: 16.0,
        engines: &["vllm"],
        description: "Mid-range dense model. Fits single 24GB GPU with headroom.",
        gated: false,
    },
    CatalogModel {
        family: "qwen",
        hf_repo: "QuantTrio/Qwen3.5-35B-A3B-AWQ",
        display_name: "Qwen3.5 35B-A3B MoE (AWQ 4-bit)",
        param_billions: 35.0,
        quantization: "AWQ 4-bit",
        size_gb: 22.0,
        engines: &["vllm"],
        description: "MoE AWQ quant. Fits a single 24GB GPU. Excellent throughput.",
        gated: false,
    },
    CatalogModel {
        family: "qwen",
        hf_repo: "Qwen/Qwen3.6-35B-A3B-FP8",
        display_name: "Qwen3.6 35B-A3B MoE (FP8)",
        param_billions: 35.0,
        quantization: "FP8",
        size_gb: 37.0,
        engines: &["vllm"],
        description: "Official FP8 MoE. Best quality on multi-GPU (TP=2 across 2x 24GB GPUs).",
        gated: false,
    },

    // --- GGUF variants (llama.cpp, CPU/any) ---
    // Qwen3.6 MoE — latest generation
    CatalogModel {
        family: "qwen",
        hf_repo: "unsloth/Qwen3.6-35B-A3B-GGUF",
        display_name: "Qwen3.6 35B-A3B MoE (GGUF Q4_K_M)",
        param_billions: 35.0,
        quantization: "GGUF Q4_K_M",
        size_gb: 22.0,
        engines: &["gguf"],
        description: "Latest MoE flagship on CPU. 35B total / 3B active. Requires 32GB+ RAM.",
        gated: false,
    },
    CatalogModel {
        family: "qwen",
        hf_repo: "unsloth/Qwen3.6-35B-A3B-UD-GGUF",
        display_name: "Qwen3.6 35B-A3B MoE (GGUF UD-Q4_K_XL)",
        param_billions: 35.0,
        quantization: "GGUF UD-Q4_K_XL",
        size_gb: 24.0,
        engines: &["gguf"],
        description: "Unsloth dynamic quant MoE — better quality than standard Q4_K_M. 32GB+.",
        gated: false,
    },
    // Qwen3.5 — previous generation
    CatalogModel {
        family: "qwen",
        hf_repo: "Qwen/Qwen3.5-4B-GGUF",
        display_name: "Qwen3.5 4B (GGUF Q4_K_M)",
        param_billions: 4.0,
        quantization: "GGUF Q4_K_M",
        size_gb: 2.8,
        engines: &["gguf"],
        description: "CPU-friendly 4B. Good quality/speed balance for CPU inference.",
        gated: false,
    },
    CatalogModel {
        family: "qwen",
        hf_repo: "Qwen/Qwen3.5-8B-GGUF",
        display_name: "Qwen3.5 8B (GGUF Q4_K_M)",
        param_billions: 8.0,
        quantization: "GGUF Q4_K_M",
        size_gb: 5.0,
        engines: &["gguf"],
        description: "Good quality 8B for CPU inference. Balanced speed vs quality.",
        gated: false,
    },
    CatalogModel {
        family: "qwen",
        hf_repo: "Qwen/Qwen3.5-32B-GGUF",
        display_name: "Qwen3.5 32B (GGUF Q4_K_M)",
        param_billions: 32.0,
        quantization: "GGUF Q4_K_M",
        size_gb: 19.0,
        engines: &["gguf"],
        description: "High quality 32B on CPU. Requires 32GB+ RAM.",
        gated: false,
    },

    // =========================================================================
    // DeepSeek (DeepSeek AI) — strong reasoning / code models
    // =========================================================================

    // --- MLX variants ---
    CatalogModel {
        family: "deepseek",
        hf_repo: "mlx-community/DeepSeek-R1-0528-Qwen3-8B-4bit",
        display_name: "DeepSeek-R1-0528 8B (4-bit)",
        param_billions: 8.0,
        quantization: "4-bit",
        size_gb: 5.5,
        engines: &["mlx"],
        description: "Reasoning-focused R1 distill. Chain-of-thought out of the box. 32GB+ Macs.",
        gated: false,
    },
    CatalogModel {
        family: "deepseek",
        hf_repo: "mlx-community/DeepSeek-V3-0324-4bit",
        display_name: "DeepSeek-V3 0324 (4-bit)",
        param_billions: 671.0,
        quantization: "4-bit",
        size_gb: 26.0,
        engines: &["mlx"],
        description: "MoE V3 — strong at coding. 48GB+ Macs.",
        gated: false,
    },

    // --- vLLM variants ---
    CatalogModel {
        family: "deepseek",
        hf_repo: "deepseek-ai/DeepSeek-R1-Distill-Qwen-8B",
        display_name: "DeepSeek-R1-Distill 8B (BF16)",
        param_billions: 8.0,
        quantization: "BF16",
        size_gb: 16.0,
        engines: &["vllm"],
        description: "Reasoning distill from R1. Excellent at step-by-step problems.",
        gated: false,
    },
    CatalogModel {
        family: "deepseek",
        hf_repo: "deepseek-ai/DeepSeek-R1-Distill-Qwen-14B",
        display_name: "DeepSeek-R1-Distill 14B (BF16)",
        param_billions: 14.0,
        quantization: "BF16",
        size_gb: 28.0,
        engines: &["vllm"],
        description: "Stronger reasoning distill. Requires multi-GPU or large VRAM.",
        gated: false,
    },

    // --- GGUF variants ---
    CatalogModel {
        family: "deepseek",
        hf_repo: "bartowski/DeepSeek-R1-Distill-Qwen-8B-GGUF",
        display_name: "DeepSeek-R1-Distill 8B (GGUF Q4_K_M)",
        param_billions: 8.0,
        quantization: "GGUF Q4_K_M",
        size_gb: 5.0,
        engines: &["gguf"],
        description: "CPU-friendly reasoning model. Great for step-by-step tasks on CPU.",
        gated: false,
    },

    // =========================================================================
    // Gemma (Google) — gated, high quality general models
    // =========================================================================

    // --- MLX variants ---
    CatalogModel {
        family: "gemma",
        hf_repo: "mlx-community/gemma-3-4b-it-4bit",
        display_name: "Gemma 3 4B-IT (4-bit)",
        param_billions: 4.0,
        quantization: "4-bit",
        size_gb: 3.0,
        engines: &["mlx"],
        description: "Google Gemma 3 instruction-tuned. Strong at following instructions.",
        gated: true,
    },
    CatalogModel {
        family: "gemma",
        hf_repo: "mlx-community/gemma-3-12b-it-4bit",
        display_name: "Gemma 3 12B-IT (4-bit)",
        param_billions: 12.0,
        quantization: "4-bit",
        size_gb: 8.0,
        engines: &["mlx"],
        description: "Gemma 3 12B — strong STEM and code. 32GB+ Macs.",
        gated: true,
    },
    CatalogModel {
        family: "gemma",
        hf_repo: "mlx-community/gemma-3-27b-it-4bit",
        display_name: "Gemma 3 27B-IT (4-bit)",
        param_billions: 27.0,
        quantization: "4-bit",
        size_gb: 17.0,
        engines: &["mlx"],
        description: "Largest Gemma 3. Near-frontier quality. 48GB+ Macs.",
        gated: true,
    },

    // --- vLLM variants ---
    CatalogModel {
        family: "gemma",
        hf_repo: "google/gemma-3-4b-it",
        display_name: "Gemma 3 4B-IT (BF16)",
        param_billions: 4.0,
        quantization: "BF16",
        size_gb: 8.0,
        engines: &["vllm"],
        description: "Gemma 3 instruction-tuned. 128K ctx, multimodal.",
        gated: true,
    },
    CatalogModel {
        family: "gemma",
        hf_repo: "google/gemma-3-12b-it",
        display_name: "Gemma 3 12B-IT (BF16)",
        param_billions: 12.0,
        quantization: "BF16",
        size_gb: 24.0,
        engines: &["vllm"],
        description: "Gemma 3 12B — single 24GB GPU (tight with headroom).",
        gated: true,
    },

    // --- GGUF variants ---
    CatalogModel {
        family: "gemma",
        hf_repo: "bartowski/gemma-3-4b-it-GGUF",
        display_name: "Gemma 3 4B-IT (GGUF Q4_K_M)",
        param_billions: 4.0,
        quantization: "GGUF Q4_K_M",
        size_gb: 2.8,
        engines: &["gguf"],
        description: "CPU-friendly Gemma 3 4B. Good instruction following on CPU.",
        gated: false,
    },

    // =========================================================================
    // GLM (THUDM / Tsinghua) — ChatGLM series, strong Chinese + English
    // =========================================================================

    // --- MLX variants ---
    CatalogModel {
        family: "glm",
        hf_repo: "mlx-community/GLM-4-9B-Chat-4bit",
        display_name: "GLM-4 9B Chat (4-bit)",
        param_billions: 9.0,
        quantization: "4-bit",
        size_gb: 6.0,
        engines: &["mlx"],
        description: "GLM-4 chat model. Excellent bilingual (CN+EN). Tool calling support.",
        gated: false,
    },

    // --- vLLM variants ---
    CatalogModel {
        family: "glm",
        hf_repo: "THUDM/glm-4-9b-chat",
        display_name: "GLM-4 9B Chat (BF16)",
        param_billions: 9.0,
        quantization: "BF16",
        size_gb: 18.0,
        engines: &["vllm"],
        description: "GLM-4 chat. 128K ctx, function calling, excellent CN/EN.",
        gated: false,
    },
    CatalogModel {
        family: "glm",
        hf_repo: "THUDM/GLM-Z1-32B-0414",
        display_name: "GLM-Z1 32B (BF16)",
        param_billions: 32.0,
        quantization: "BF16",
        size_gb: 64.0,
        engines: &["vllm"],
        description: "GLM-Z1 32B reasoning model. Multi-GPU required.",
        gated: false,
    },

    // --- GGUF variants ---
    CatalogModel {
        family: "glm",
        hf_repo: "bartowski/glm-4-9b-chat-GGUF",
        display_name: "GLM-4 9B Chat (GGUF Q4_K_M)",
        param_billions: 9.0,
        quantization: "GGUF Q4_K_M",
        size_gb: 5.5,
        engines: &["gguf"],
        description: "CPU-friendly GLM-4 9B. Bilingual CN+EN, tool calling.",
        gated: false,
    },

    // =========================================================================
    // Kimi (Moonshot AI) — strong vision + long context
    // =========================================================================

    // --- MLX variants ---
    CatalogModel {
        family: "kimi",
        hf_repo: "mlx-community/Kimi-VL-A3B-Thinking-4bit",
        display_name: "Kimi-VL A3B Thinking (4-bit)",
        param_billions: 16.0,
        quantization: "4-bit",
        size_gb: 9.5,
        engines: &["mlx"],
        description: "MoE VL model with long-chain reasoning. Vision + thinking mode. 32GB+ Macs.",
        gated: false,
    },

    // --- vLLM variants ---
    CatalogModel {
        family: "kimi",
        hf_repo: "moonshotai/Kimi-VL-A3B-Thinking",
        display_name: "Kimi-VL A3B Thinking (BF16)",
        param_billions: 16.0,
        quantization: "BF16",
        size_gb: 32.0,
        engines: &["vllm"],
        description: "MoE VL with reasoning. Strong vision understanding and long-context.",
        gated: false,
    },

    // --- GGUF variants ---
    CatalogModel {
        family: "kimi",
        hf_repo: "bartowski/Kimi-VL-A3B-Thinking-GGUF",
        display_name: "Kimi-VL A3B Thinking (GGUF Q4_K_M)",
        param_billions: 16.0,
        quantization: "GGUF Q4_K_M",
        size_gb: 9.0,
        engines: &["gguf"],
        description: "CPU-friendly Kimi-VL. Vision + reasoning on CPU.",
        gated: false,
    },
];

/// Return all catalog models for a given family and engine, filtered by RAM budget.
pub fn models_for(family: &str, engine: &str, budget_gb: f32) -> Vec<&'static CatalogModel> {
    CATALOG
        .iter()
        .filter(|m| m.family == family && m.supports_engine(engine))
        .filter(|m| m.fits(budget_gb))
        .collect()
}

/// Return all catalog models for a given family and engine, including those that don't fit.
pub fn all_models_for(family: &str, engine: &str) -> Vec<&'static CatalogModel> {
    CATALOG
        .iter()
        .filter(|m| m.family == family && m.supports_engine(engine))
        .collect()
}

/// Return all catalog models for a given family regardless of engine.
/// Deduplication by hf_repo is NOT performed here — callers may receive the same
/// underlying model multiple times if it appears under different engines.
/// Use this when rendering the combined model list before engine selection.
pub fn all_for_family(family: &str) -> Vec<&'static CatalogModel> {
    CATALOG
        .iter()
        .filter(|m| m.family == family)
        .collect()
}

/// Return all unique family identifiers in the catalog.
pub fn families() -> Vec<&'static str> {
    let mut seen = std::collections::HashSet::new();
    CATALOG
        .iter()
        .filter_map(|m| {
            if seen.insert(m.family) {
                Some(m.family)
            } else {
                None
            }
        })
        .collect()
}

/// Extract a (major, minor) version tuple from a model display name for recency sorting.
/// Examples: "Qwen3.6 35B" -> (3, 6), "Qwen3.5 4B" -> (3, 5), "Qwen3 4B" -> (3, 0),
///           "DeepSeek-R1-0528 8B" -> (1, 528), "Gemma 3 4B" -> (3, 0).
/// Returns (0, 0) when no version is detected.
pub fn version_rank(display_name: &str) -> (u32, u32) {
    // Pattern: look for one or more digits optionally followed by dot + digits.
    // We scan for the first digit cluster that looks like a version.
    let bytes = display_name.as_bytes();
    let mut i = 0;
    while i < bytes.len() {
        if bytes[i].is_ascii_digit() {
            // Collect major number
            let start = i;
            while i < bytes.len() && bytes[i].is_ascii_digit() {
                i += 1;
            }
            let major: u32 = display_name[start..i].parse().unwrap_or(0);

            // Check for dot + minor
            let minor = if i < bytes.len() && bytes[i] == b'.' {
                let dot_pos = i;
                i += 1;
                let minor_start = i;
                while i < bytes.len() && bytes[i].is_ascii_digit() {
                    i += 1;
                }
                if i > minor_start {
                    display_name[minor_start..i].parse().unwrap_or(0)
                } else {
                    i = dot_pos; // rewind past the dot
                    0u32
                }
            } else {
                0u32
            };

            // Only trust if major looks like a version (> 0)
            if major > 0 {
                return (major, minor);
            }
        } else {
            i += 1;
        }
    }
    (0, 0)
}
