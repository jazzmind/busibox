use crate::app::{App, BrowsableModel, BrowseStep, MessageKind, Screen};
use crate::theme;
use busibox_core::model_catalog::{self, CatalogModel};
use crossterm::event::{KeyCode, KeyEvent, KeyModifiers};
use ratatui::layout::Margin;
use ratatui::prelude::*;
use ratatui::widgets::{Scrollbar, ScrollbarOrientation, ScrollbarState, *};
use std::sync::mpsc;

// ---------------------------------------------------------------------------
// Background HF API types
// ---------------------------------------------------------------------------

/// Update messages sent from the background HF API query thread.
pub enum BrowseUpdate {
    /// Live results fetched from HF API — merged with curated list.
    Results(Vec<BrowsableModel>),
    /// Error occurred; show a warning but fall back to curated list.
    Error(String),
}

/// Families displayed in the picker, in the order shown.
pub const FAMILIES: &[(&str, &str)] = &[
    ("qwen",      "Qwen — Alibaba, multimodal, tool calling, Apache-2.0"),
    ("deepseek",  "DeepSeek — strong reasoning & code, MIT/commercial"),
    ("gemma",     "Gemma — Google, high quality, requires HF token"),
    ("glm",       "GLM — THUDM/Tsinghua, bilingual CN+EN"),
    ("kimi",      "Kimi — Moonshot AI, vision + long context"),
];

/// All supported engine options with display labels.
/// Order matches what we show when filtering.
pub const ENGINES: &[(&str, &str)] = &[
    ("mlx",   "MLX  — Apple Silicon (unified memory, mlx-lm / vllm-mlx)"),
    ("vllm",  "vLLM — NVIDIA GPU (CUDA, tensor parallel)"),
    ("gguf",  "GGUF — llama.cpp (CPU / any hardware)"),
];

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

pub fn hf_token(app: &App) -> Option<String> {
    app.active_profile()
        .and_then(|(_, p)| p.huggingface_token.clone())
        .or_else(|| {
            app.profiles
                .as_ref()
                .and_then(|pf| pf.defaults.as_ref())
                .and_then(|d| d.huggingface_token.clone())
        })
        .filter(|t| !t.is_empty())
}

/// Return (soft_gb, hard_gb) RAM budgets for model selection.
/// soft = RAM - 5GB (recommended; busibox overhead + headroom)
/// hard = RAM - 2.5GB (absolute maximum; busibox needs at least 2.5GB)
pub fn budget_gb(app: &App) -> (f32, f32) {
    let hw = if app.setup_target == crate::app::SetupTarget::Remote {
        app.remote_hardware.as_ref()
    } else {
        app.local_hardware.as_ref()
    };
    let ram = hw.map(|h| h.ram_gb as f32).unwrap_or(16.0);
    let soft = (ram - 5.0).max(1.0);
    let hard = (ram - 2.5).max(1.0);
    (soft, hard)
}

/// Populate `app.browse_models` from the curated catalog for the currently
/// selected family — ALL engines, deduplicated by hf_repo.
/// Sort: version-rank desc → fits-soft first → fits-hard → param-count desc.
pub fn populate_from_catalog(app: &mut App) {
    let family_id = FAMILIES
        .get(app.browse_family_selected)
        .map(|(id, _)| *id)
        .unwrap_or("qwen");
    let (soft, _hard) = budget_gb(app);

    // Collect all entries for family across all engines; merge by hf_repo.
    let mut by_repo: std::collections::HashMap<&str, &'static CatalogModel> =
        std::collections::HashMap::new();
    for m in model_catalog::all_for_family(family_id) {
        // Keep the first occurrence per repo (catalog order is stable)
        by_repo.entry(m.hf_repo).or_insert(m);
    }

    // Build BrowsableModel list, merging engines from all catalog entries sharing the same repo.
    let mut repo_engines: std::collections::HashMap<&str, Vec<&'static str>> =
        std::collections::HashMap::new();
    for m in model_catalog::all_for_family(family_id) {
        for eng in m.engines {
            let list = repo_engines.entry(m.hf_repo).or_default();
            if !list.contains(eng) {
                list.push(eng);
            }
        }
    }

    let mut models: Vec<BrowsableModel> = by_repo
        .values()
        .map(|m: &&'static CatalogModel| {
            let engines = repo_engines
                .get(m.hf_repo)
                .map(|v| v.iter().map(|s| s.to_string()).collect())
                .unwrap_or_default();
            BrowsableModel {
                hf_repo: m.hf_repo.to_string(),
                display_name: m.display_name.to_string(),
                param_billions: m.param_billions,
                quantization: m.quantization.to_string(),
                size_gb: m.size_gb,
                description: m.description.to_string(),
                gated: m.gated,
                curated: true,
                downloads: 0,
                engines,
            }
        })
        .collect();

    sort_models(&mut models, soft);

    app.browse_models = models;
    app.browse_model_selected = 0;
    app.browse_model_scroll = 0;
}

/// Sort models by: version-rank desc, then fits-soft, then param-count desc.
fn sort_models(models: &mut Vec<BrowsableModel>, soft_gb: f32) {
    models.sort_by(|a, b| {
        // 1. Version rank (newer first)
        let va = model_catalog::version_rank(&a.display_name);
        let vb = model_catalog::version_rank(&b.display_name);
        let ver_cmp = vb.cmp(&va);
        if ver_cmp != std::cmp::Ordering::Equal {
            return ver_cmp;
        }
        // 2. Fits-in-soft-budget first
        let a_fits = a.size_gb <= soft_gb;
        let b_fits = b.size_gb <= soft_gb;
        match (a_fits, b_fits) {
            (true, false) => return std::cmp::Ordering::Less,
            (false, true) => return std::cmp::Ordering::Greater,
            _ => {}
        }
        // 3. Larger param count first (more capable)
        b.param_billions
            .partial_cmp(&a.param_billions)
            .unwrap_or(std::cmp::Ordering::Equal)
    });
}

/// Return the engine entries that the currently selected model supports,
/// filtered to those in `ENGINES`.
pub fn engines_for_selected_model(app: &App) -> Vec<(usize, &'static str, &'static str)> {
    let model_engines = app
        .browse_models
        .get(app.browse_model_selected)
        .map(|m| m.engines.as_slice())
        .unwrap_or(&[]);

    ENGINES
        .iter()
        .enumerate()
        .filter(|(_, (id, _))| model_engines.iter().any(|e| e == id))
        .map(|(i, (id, label))| (i, *id, *label))
        .collect()
}

/// Kick off a background thread that queries the HF API and sends results back.
pub fn start_hf_api_query(app: &mut App) {
    let family_id = FAMILIES
        .get(app.browse_family_selected)
        .map(|(id, _)| id.to_string())
        .unwrap_or_else(|| "qwen".to_string());
    let token = hf_token(app);
    let (soft, _) = budget_gb(app);

    // Snapshot current curated list for merge baseline
    let curated_snapshot = app.browse_models.clone();

    let (tx, rx) = mpsc::channel::<BrowseUpdate>();
    app.browse_rx = Some(rx);
    app.browse_loading = true;

    std::thread::spawn(move || {
        match query_hf_api(&family_id, token.as_deref(), soft, curated_snapshot) {
            Ok(models) => {
                let _ = tx.send(BrowseUpdate::Results(models));
            }
            Err(e) => {
                let _ = tx.send(BrowseUpdate::Error(e));
            }
        }
    });
}

/// Query HF API for models matching the family (all engine tags), merge with curated list.
fn query_hf_api(
    family: &str,
    token: Option<&str>,
    soft_gb: f32,
    curated: Vec<BrowsableModel>,
) -> Result<Vec<BrowsableModel>, String> {
    let curated_repos: std::collections::HashSet<String> =
        curated.iter().map(|m| m.hf_repo.clone()).collect();

    // Pull family-name prefix for filtering results
    let (search_author, family_prefixes): (&str, &[&str]) = match family {
        "qwen"     => ("Qwen", &["Qwen", "qwen", "unsloth"]),
        "deepseek" => ("deepseek-ai", &["DeepSeek", "deepseek"]),
        "gemma"    => ("google", &["gemma", "Gemma"]),
        "glm"      => ("THUDM", &["GLM", "glm", "chatglm"]),
        "kimi"     => ("moonshotai", &["Kimi", "kimi"]),
        _          => return Ok(curated),
    };

    // Query across mlx-community (for MLX), the main org (for vLLM/BF16), and gguf tag
    // We fire a single search by author and rely on model name filtering.
    let mut api_models: Vec<BrowsableModel> = Vec::new();

    let queries: &[(&str, &str)] = &[
        (search_author, "mlx"),
        (search_author, "text-generation-inference"),
        (search_author, "gguf"),
        ("mlx-community", "mlx"),
        ("bartowski", "gguf"),
        ("unsloth", "gguf"),
    ];

    for (author, tag) in queries {
        let url = format!(
            "https://huggingface.co/api/models?author={}&tags={}&sort=lastModified&limit=20",
            author, tag
        );

        let mut cmd = std::process::Command::new("curl");
        cmd.arg("-fsSL").arg("--max-time").arg("8").arg(&url);
        if let Some(tok) = token {
            cmd.arg("-H").arg(format!("Authorization: Bearer {tok}"));
        }

        let Ok(output) = cmd.output() else { continue };
        if !output.status.success() {
            continue;
        }

        let body = String::from_utf8_lossy(&output.stdout);
        let Ok(parsed) = serde_json::from_str::<serde_json::Value>(&body) else { continue };
        let Some(items) = parsed.as_array() else { continue };

        for item in items {
            let Some(repo) = item["modelId"].as_str().or_else(|| item["id"].as_str()) else {
                continue;
            };

            // Must mention the family in the repo name
            let repo_lower = repo.to_lowercase();
            let family_match = family_prefixes
                .iter()
                .any(|p| repo_lower.contains(&p.to_lowercase()));
            if !family_match {
                continue;
            }

            // Skip already-curated entries
            if curated_repos.contains(repo) {
                continue;
            }

            // Skip duplicates already added from another query
            if api_models.iter().any(|m| m.hf_repo == repo) {
                continue;
            }

            // Skip known noise patterns (MTP is multi-token prediction, not a chat model;
            // pi-tune is a tuning artifact, not a user-facing model)
            let lower = repo.to_lowercase();
            if lower.contains("-mtp") || lower.contains("_mtp") {
                continue;
            }
            if lower.contains("-pi") && lower.ends_with("-pi") {
                continue;
            }

            let downloads = item["downloads"].as_u64().unwrap_or(0);
            let gated = item["gated"].as_bool().unwrap_or(false);

            // Infer which engine this repo targets
            let inferred_engines = infer_engines_from_repo(repo, *tag);
            if inferred_engines.is_empty() {
                continue;
            }

            let size_gb = item["safetensors"]["total"]
                .as_f64()
                .map(|b| b / 1e9)
                .unwrap_or_else(|| estimate_size_from_repo(repo)) as f32;

            let display = repo.rsplit('/').next().unwrap_or(repo);
            let quant = infer_quantization(display, &inferred_engines[0]);

            api_models.push(BrowsableModel {
                hf_repo: repo.to_string(),
                display_name: display.to_string(),
                param_billions: infer_params(display),
                quantization: quant,
                size_gb,
                description: String::new(),
                gated,
                curated: false,
                downloads,
                engines: inferred_engines,
            });
        }
    }

    // Merge: curated first (verified), then API extras
    let mut merged = curated;
    merged.append(&mut api_models);

    sort_models(&mut merged, soft_gb);

    Ok(merged)
}

/// Infer which inference engines a HF repo is compatible with based on its name and tags.
fn infer_engines_from_repo(repo: &str, hint_tag: &str) -> Vec<String> {
    let lower = repo.to_lowercase();
    let author = repo.split('/').next().unwrap_or("").to_lowercase();

    let mut engines = Vec::new();

    // MLX repos
    if author == "mlx-community" || lower.contains("-mlx") || lower.contains("_mlx") || hint_tag == "mlx" {
        engines.push("mlx".to_string());
    }
    // GGUF repos
    if lower.contains("gguf") || lower.contains("-gguf") || hint_tag == "gguf" {
        engines.push("gguf".to_string());
    }
    // vLLM / TGI repos (safetensors BF16/FP8/AWQ/GPTQ)
    if (hint_tag == "text-generation-inference" || lower.contains("awq") || lower.contains("fp8") || lower.contains("gptq"))
        && !engines.contains(&"mlx".to_string())
        && !engines.contains(&"gguf".to_string())
    {
        engines.push("vllm".to_string());
    }
    // Plain author repos (no quant suffix) → vLLM BF16
    if engines.is_empty() {
        engines.push("vllm".to_string());
    }

    engines
}

fn infer_params(repo: &str) -> f32 {
    let lower = repo.to_lowercase();
    for token in lower.split(['-', '_', '.', ' ']) {
        if let Some(stripped) = token.strip_suffix('b') {
            if let Ok(n) = stripped.parse::<f32>() {
                return n;
            }
        }
    }
    0.0
}

fn infer_quantization(repo: &str, engine: &str) -> String {
    let lower = repo.to_lowercase();
    if lower.contains("fp8") {
        "FP8".into()
    } else if lower.contains("awq") {
        "AWQ".into()
    } else if lower.contains("gptq") {
        "GPTQ".into()
    } else if lower.contains("q8_0") {
        "GGUF Q8_0".into()
    } else if lower.contains("q4_k_m") || lower.contains("q4km") {
        "GGUF Q4_K_M".into()
    } else if lower.contains("q4_k_xl") || lower.contains("ud-q4") {
        "GGUF UD-Q4_K_XL".into()
    } else if lower.contains("8bit") || lower.contains("8-bit") {
        "8-bit".into()
    } else if lower.contains("4bit") || lower.contains("4-bit") {
        "4-bit".into()
    } else if lower.contains("gguf") {
        "GGUF".into()
    } else {
        match engine {
            "mlx"  => "MLX".into(),
            "vllm" => "BF16".into(),
            "gguf" => "GGUF".into(),
            _      => String::new(),
        }
    }
}

fn estimate_size_from_repo(repo: &str) -> f64 {
    let params = infer_params(repo);
    if params > 0.0 {
        let lower = repo.to_lowercase();
        let bytes_per_param: f64 = if lower.contains("4bit") || lower.contains("awq") || lower.contains("q4") {
            0.55
        } else if lower.contains("fp8") || lower.contains("8bit") {
            1.05
        } else {
            2.0
        };
        (params as f64) * 1e9 * bytes_per_param / 1e9
    } else {
        0.0
    }
}

// ---------------------------------------------------------------------------
// Drain background receiver
// ---------------------------------------------------------------------------

pub fn drain_browse_rx(app: &mut App) {
    let updates: Vec<BrowseUpdate> = app
        .browse_rx
        .as_ref()
        .map(|rx| rx.try_iter().collect())
        .unwrap_or_default();

    for update in updates {
        match update {
            BrowseUpdate::Results(mut models) => {
                let (soft, _) = budget_gb(app);
                sort_models(&mut models, soft);
                app.browse_models = models;
                app.browse_model_selected = 0;
                app.browse_model_scroll = 0;
                app.browse_loading = false;
            }
            BrowseUpdate::Error(e) => {
                app.set_message(
                    &format!("HF API error: {e}. Showing curated list."),
                    MessageKind::Warning,
                );
                app.browse_loading = false;
            }
        }
    }
}

// ---------------------------------------------------------------------------
// Render
// ---------------------------------------------------------------------------

pub fn render(f: &mut Frame, app: &App) {
    let area = f.area();
    let chunks = Layout::default()
        .direction(Direction::Vertical)
        .constraints([
            Constraint::Length(3),
            Constraint::Min(10),
            Constraint::Length(3),
        ])
        .margin(2)
        .split(area);

    // Step numbering: Engine is skipped when locked, making it 4 steps.
    let engine_locked = app.browse_engine_locked;
    let total_steps = if engine_locked { 4 } else { 5 };
    let (step_num, step_title) = if engine_locked {
        match &app.browse_step {
            BrowseStep::Token   => (1, "HuggingFace Token"),
            BrowseStep::Family  => (2, "Model Family"),
            BrowseStep::Models  => (3, "Select Model"),
            BrowseStep::Engine  => (3, "Select Model"), // shouldn't show when locked
            BrowseStep::Confirm => (4, "Confirm Download"),
        }
    } else {
        match &app.browse_step {
            BrowseStep::Token   => (1, "HuggingFace Token"),
            BrowseStep::Family  => (2, "Model Family"),
            BrowseStep::Models  => (3, "Select Model"),
            BrowseStep::Engine  => (4, "Inference Engine"),
            BrowseStep::Confirm => (5, "Confirm Download"),
        }
    };

    let engine_hint = if engine_locked {
        let engine_label = ENGINES
            .get(app.browse_engine_selected)
            .map(|(_, label)| *label)
            .unwrap_or("Auto");
        format!(" [engine: {}]", engine_label.split_once(" —").map(|(n, _)| n).unwrap_or(engine_label))
    } else {
        String::new()
    };

    let title = Paragraph::new(format!(
        "  Browse HuggingFace Models — Step {step_num}/{total_steps}: {step_title}{engine_hint}"
    ))
    .style(theme::title())
    .alignment(Alignment::Center);
    f.render_widget(title, chunks[0]);

    match &app.browse_step {
        BrowseStep::Token   => render_token(f, app, chunks[1]),
        BrowseStep::Family  => render_family(f, app, chunks[1]),
        BrowseStep::Models  => render_models(f, app, chunks[1]),
        BrowseStep::Engine  => render_engine(f, app, chunks[1]),
        BrowseStep::Confirm => render_confirm(f, app, chunks[1]),
    }

    render_help(f, app, chunks[2]);
}

fn render_token(f: &mut Frame, app: &App, area: Rect) {
    let existing = hf_token(app);
    let mut lines = Vec::new();

    lines.push(Line::from(""));
    if let Some(ref tok) = existing {
        let masked = if tok.len() > 8 {
            format!("{}...{}", &tok[..4], &tok[tok.len() - 4..])
        } else {
            "****".to_string()
        };
        lines.push(Line::from(vec![
            Span::styled("  Current token: ", theme::muted()),
            Span::styled(masked, theme::success()),
            Span::styled("  (press Enter to continue)", theme::dim()),
        ]));
        lines.push(Line::from(""));
        lines.push(Line::from(vec![
            Span::styled("  Press ", theme::dim()),
            Span::styled("c", theme::info()),
            Span::styled(" to clear and enter a new token, or ", theme::dim()),
            Span::styled("Enter", theme::info()),
            Span::styled(" to continue.", theme::dim()),
        ]));
    } else {
        lines.push(Line::from(vec![
            Span::styled("  A HuggingFace token is required to download gated models (Gemma, etc.).", theme::normal()),
        ]));
        lines.push(Line::from(vec![
            Span::styled("  For public models it is optional, but recommended for higher rate limits.", theme::muted()),
        ]));
        lines.push(Line::from(""));
        lines.push(Line::from(vec![
            Span::styled("  Get your token at: ", theme::muted()),
            Span::styled("https://huggingface.co/settings/tokens", theme::info()),
        ]));
        lines.push(Line::from(""));
        lines.push(Line::from(vec![
            Span::styled("  Token: ", theme::muted()),
            Span::styled(&app.browse_hf_token_input, theme::normal()),
            Span::styled("█", theme::highlight()),
        ]));
    }

    let block = Block::default()
        .borders(Borders::ALL)
        .border_style(theme::dim())
        .title(" HuggingFace Token ")
        .title_style(theme::heading());
    f.render_widget(Paragraph::new(lines).block(block), area);
}

fn render_family(f: &mut Frame, app: &App, area: Rect) {
    let mut lines = Vec::new();
    lines.push(Line::from(""));
    lines.push(Line::from(vec![
        Span::styled("  Select a model family to browse:", theme::muted()),
    ]));
    lines.push(Line::from(""));

    for (i, (_, label)) in FAMILIES.iter().enumerate() {
        let style = if i == app.browse_family_selected {
            theme::selected()
        } else {
            theme::normal()
        };
        let prefix = if i == app.browse_family_selected { "▶ " } else { "  " };
        lines.push(Line::from(Span::styled(format!("{prefix}{label}"), style)));
    }

    let block = Block::default()
        .borders(Borders::ALL)
        .border_style(theme::dim())
        .title(" Model Family ")
        .title_style(theme::heading());
    f.render_widget(Paragraph::new(lines).block(block), area);
}

fn render_models(f: &mut Frame, app: &App, area: Rect) {
    let (soft, hard) = budget_gb(app);

    if app.browse_loading && app.browse_models.is_empty() {
        let content = Paragraph::new(vec![
            Line::from(""),
            Line::from(Span::styled("  Fetching models from HuggingFace…", theme::info())),
        ])
        .block(
            Block::default()
                .borders(Borders::ALL)
                .border_style(theme::dim())
                .title(" Loading… ")
                .title_style(theme::heading()),
        );
        f.render_widget(content, area);
        return;
    }

    if app.browse_models.is_empty() {
        let content = Paragraph::new("  No models found for this family.")
            .style(theme::muted())
            .block(Block::default().borders(Borders::ALL).border_style(theme::dim()));
        f.render_widget(content, area);
        return;
    }

    let mut lines = Vec::new();
    let loading_tag = if app.browse_loading { " ⟳" } else { "" };

    for (i, m) in app.browse_models.iter().enumerate() {
        let fits_soft = m.size_gb <= soft;
        let fits_hard = m.size_gb <= hard;
        let is_selected = i == app.browse_model_selected;

        // Determine if the locked engine is compatible with this model
        let engine_ok = if app.browse_engine_locked {
            let locked_id = ENGINES
                .get(app.browse_engine_selected)
                .map(|(id, _)| *id)
                .unwrap_or("");
            m.engines.iter().any(|e| e == locked_id)
        } else {
            true
        };

        let name_style = if is_selected {
            theme::selected()
        } else if !engine_ok || !fits_hard {
            theme::dim()
        } else if fits_soft {
            theme::normal()
        } else {
            theme::warning()
        };

        let (size_style, fits_icon) = if fits_soft {
            (theme::success(), "✓")
        } else if fits_hard {
            (theme::warning(), "~")
        } else {
            (theme::error(), "✗")
        };

        let curated_tag = if m.curated { " ★" } else { "" };
        let gated_tag = if m.gated { " 🔒" } else { "" };
        let incompatible_tag = if !engine_ok { " [incompatible engine]" } else { "" };
        let prefix = if is_selected { "▶ " } else { "  " };

        // Engine tags: show up to 3 short labels
        let engine_tags: String = m
            .engines
            .iter()
            .map(|e| match e.as_str() {
                "mlx"  => "[MLX]",
                "vllm" => "[vLLM]",
                "gguf" => "[GGUF]",
                _      => "[?]",
            })
            .collect::<Vec<_>>()
            .join(" ");

        lines.push(Line::from(vec![
            Span::styled(format!("{prefix}{}", m.display_name), name_style),
            Span::styled(curated_tag, theme::warning()),
            Span::styled(gated_tag, theme::dim()),
            Span::styled(incompatible_tag, theme::error()),
        ]));

        if is_selected {
            lines.push(Line::from(vec![
                Span::styled("    ", theme::dim()),
                Span::styled(fits_icon, size_style),
                Span::styled(format!(" {:.1}GB", m.size_gb), size_style),
                Span::styled(" · ", theme::dim()),
                Span::styled(&m.quantization, theme::info()),
                Span::styled(" · ", theme::dim()),
                Span::styled(engine_tags, theme::info()),
                if !m.description.is_empty() {
                    Span::styled(format!("  {}", m.description), theme::dim())
                } else {
                    Span::styled("", theme::dim())
                },
            ]));
        }
    }

    let content_height = area.height.saturating_sub(4) as usize;
    let total = lines.len();
    let max_scroll = total.saturating_sub(content_height);
    let scroll = app.browse_model_scroll.min(max_scroll);

    let block = Block::default()
        .borders(Borders::ALL)
        .border_style(theme::dim())
        .title(format!(
            " Models ({} available, soft budget {:.0}GB, hard limit {:.0}GB){} ",
            app.browse_models.len(),
            soft,
            hard,
            loading_tag,
        ))
        .title_style(theme::heading());

    f.render_widget(
        Paragraph::new(lines).scroll((scroll as u16, 0)).block(block),
        area,
    );

    if total > content_height {
        let mut scrollbar_state = ScrollbarState::new(total).position(scroll);
        let scrollbar = Scrollbar::new(ScrollbarOrientation::VerticalRight)
            .begin_symbol(Some("↑"))
            .end_symbol(Some("↓"));
        f.render_stateful_widget(
            scrollbar,
            area.inner(Margin { vertical: 1, horizontal: 0 }),
            &mut scrollbar_state,
        );
    }
}

fn render_engine(f: &mut Frame, app: &App, area: Rect) {
    let hw = if app.setup_target == crate::app::SetupTarget::Remote {
        app.remote_hardware.as_ref()
    } else {
        app.local_hardware.as_ref()
    };

    let recommended_engine = hw.map(|h| match &h.llm_backend {
        busibox_core::hardware::LlmBackend::Mlx  => "mlx",
        busibox_core::hardware::LlmBackend::Vllm => "vllm",
        busibox_core::hardware::LlmBackend::Cloud => "mlx",
    });

    let (soft, _) = budget_gb(app);
    let compatible = engines_for_selected_model(app);

    let selected_model_name = app
        .browse_models
        .get(app.browse_model_selected)
        .map(|m| m.display_name.as_str())
        .unwrap_or("selected model");

    let mut lines = Vec::new();
    lines.push(Line::from(""));
    lines.push(Line::from(vec![
        Span::styled(
            format!("  Compatible engines for: {selected_model_name}"),
            theme::muted(),
        ),
        if let Some(gb) = hw.map(|h| h.ram_gb) {
            Span::styled(
                format!("   (RAM: {gb}GB, budget {soft:.0}GB)"),
                theme::dim(),
            )
        } else {
            Span::styled("", theme::dim())
        },
    ]));
    lines.push(Line::from(""));

    if compatible.is_empty() {
        lines.push(Line::from(Span::styled(
            "  No compatible engines found for this model.",
            theme::error(),
        )));
    } else {
        for (engine_idx, id, label) in &compatible {
            let is_selected = *engine_idx == app.browse_engine_selected;
            let is_recommended = recommended_engine.map(|r| r == *id).unwrap_or(false);
            let style = if is_selected { theme::selected() } else { theme::normal() };
            let prefix = if is_selected { "▶ " } else { "  " };
            let rec_tag = if is_recommended { " ★ recommended" } else { "" };
            lines.push(Line::from(vec![
                Span::styled(format!("{prefix}{label}"), style),
                Span::styled(rec_tag, theme::success()),
            ]));
        }
    }

    let block = Block::default()
        .borders(Borders::ALL)
        .border_style(theme::dim())
        .title(" Inference Engine ")
        .title_style(theme::heading());
    f.render_widget(Paragraph::new(lines).block(block), area);
}

fn render_confirm(f: &mut Frame, app: &App, area: Rect) {
    let model = app.browse_models.get(app.browse_model_selected);

    let engine_label = ENGINES
        .get(app.browse_engine_selected)
        .map(|(_, l)| *l)
        .unwrap_or("Unknown");
    let (soft, _) = budget_gb(app);

    let mut lines = Vec::new();
    lines.push(Line::from(""));
    lines.push(Line::from(vec![
        Span::styled("  Selected model: ", theme::muted()),
        Span::styled(
            model.map(|m| m.display_name.as_str()).unwrap_or("—"),
            theme::highlight(),
        ),
    ]));

    if let Some(m) = model {
        lines.push(Line::from(vec![
            Span::styled("  Repository:     ", theme::muted()),
            Span::styled(&m.hf_repo, theme::info()),
        ]));
        lines.push(Line::from(vec![
            Span::styled("  Quantization:   ", theme::muted()),
            Span::styled(&m.quantization, theme::normal()),
        ]));
        let size_style = if m.size_gb <= soft { theme::success() } else { theme::warning() };
        lines.push(Line::from(vec![
            Span::styled("  Size:           ", theme::muted()),
            Span::styled(format!("{:.1} GB", m.size_gb), size_style),
            Span::styled(format!(" (budget {soft:.0} GB)", ), theme::dim()),
        ]));
        if m.gated {
            lines.push(Line::from(vec![
                Span::styled("  ⚠  Gated model — HuggingFace token required.", theme::warning()),
            ]));
        }
        if !m.description.is_empty() {
            lines.push(Line::from(""));
            lines.push(Line::from(vec![
                Span::styled("  ", theme::dim()),
                Span::styled(&m.description, theme::muted()),
            ]));
        }
    }

    lines.push(Line::from(""));
    lines.push(Line::from(vec![
        Span::styled("  Engine: ", theme::muted()),
        Span::styled(engine_label, theme::normal()),
    ]));
    lines.push(Line::from(""));
    lines.push(Line::from(vec![
        Span::styled("  Press ", theme::dim()),
        Span::styled("Enter", theme::success()),
        Span::styled(" to start download  ", theme::dim()),
        Span::styled("s", theme::muted()),
        Span::styled(" to skip  ", theme::dim()),
        Span::styled("Esc", theme::muted()),
        Span::styled(" to go back.", theme::dim()),
    ]));

    let block = Block::default()
        .borders(Borders::ALL)
        .border_style(theme::dim())
        .title(" Confirm Download ")
        .title_style(theme::heading());
    f.render_widget(Paragraph::new(lines).block(block), area);
}

fn render_help(f: &mut Frame, app: &App, area: Rect) {
    let text = match &app.browse_step {
        BrowseStep::Token   => " Enter Confirm/Skip  Esc Back  (type to enter token)",
        BrowseStep::Family  => " ↑↓ Navigate  Enter Select  Esc Back",
        BrowseStep::Models  => " ↑↓ Navigate  Enter Select  r Refresh from HF  Esc Back",
        BrowseStep::Engine  => " ↑↓ Navigate  Enter Select  Esc Back",
        BrowseStep::Confirm => " Enter Download  s Skip  Esc Back",
    };
    f.render_widget(
        Paragraph::new(Line::from(Span::styled(text, theme::muted()))),
        area,
    );
}

// ---------------------------------------------------------------------------
// Key handler
// ---------------------------------------------------------------------------

pub fn handle_key(app: &mut App, key: KeyEvent) {
    drain_browse_rx(app);

    match &app.browse_step.clone() {
        BrowseStep::Token   => handle_token(app, key),
        BrowseStep::Family  => handle_family(app, key),
        BrowseStep::Models  => handle_models(app, key),
        BrowseStep::Engine  => handle_engine(app, key),
        BrowseStep::Confirm => handle_confirm(app, key),
    }
}

fn back_from_browse(app: &mut App) {
    if app.browse_return_to_wizard {
        app.browse_return_to_wizard = false;
        app.browse_engine_locked = false;
        app.wizard_step = crate::app::WizardStep::ModelSelect;
        app.wizard_target_selected = 1; // highlight "Skip" so Esc feels natural
        app.screen = Screen::InstallWizard;
    } else {
        app.screen = Screen::ModelsManage;
    }
}

fn handle_token(app: &mut App, key: KeyEvent) {
    match key.code {
        KeyCode::Esc => back_from_browse(app),
        KeyCode::Char('c') if key.modifiers.contains(KeyModifiers::CONTROL) => {
            back_from_browse(app);
        }
        KeyCode::Enter => {
            if !app.browse_hf_token_input.is_empty() {
                save_hf_token(app);
            }
            app.browse_step = BrowseStep::Family;
        }
        KeyCode::Char('c') => {
            app.browse_hf_token_input.clear();
            if let Some(profiles) = &mut app.profiles {
                if let Some(defaults) = &mut profiles.defaults {
                    defaults.huggingface_token = None;
                }
            }
            app.set_message("HF token cleared. Type new token and press Enter.", MessageKind::Info);
        }
        KeyCode::Backspace => {
            app.browse_hf_token_input.pop();
        }
        KeyCode::Char(c) => {
            app.browse_hf_token_input.push(c);
        }
        _ => {}
    }
}

fn save_hf_token(app: &mut App) {
    let token = app.browse_hf_token_input.clone();
    if token.is_empty() {
        return;
    }
    let repo_root = app.repo_root.clone();
    if let Some(profiles) = &mut app.profiles {
        let defaults = profiles.defaults.get_or_insert_with(Default::default);
        defaults.huggingface_token = Some(token);
        let _ = crate::modules::profile::save_profiles(&repo_root, profiles);
    }
    app.browse_hf_token_input.clear();
    app.set_message("HF token saved.", MessageKind::Success);
}

fn handle_family(app: &mut App, key: KeyEvent) {
    match key.code {
        KeyCode::Esc => {
            if hf_token(app).is_some() || !app.browse_hf_token_input.is_empty() {
                app.browse_step = BrowseStep::Token;
            } else {
                back_from_browse(app);
            }
        }
        KeyCode::Up | KeyCode::Char('k') => {
            if app.browse_family_selected > 0 {
                app.browse_family_selected -= 1;
            }
        }
        KeyCode::Down | KeyCode::Char('j') => {
            if app.browse_family_selected + 1 < FAMILIES.len() {
                app.browse_family_selected += 1;
            }
        }
        KeyCode::Enter => {
            // Always go to Models (all engines shown per row); engine is chosen after
            populate_from_catalog(app);
            start_hf_api_query(app);
            app.browse_step = BrowseStep::Models;
        }
        _ => {}
    }
}

fn handle_models(app: &mut App, key: KeyEvent) {
    let count = app.browse_models.len();
    match key.code {
        KeyCode::Esc => {
            app.browse_loading = false;
            app.browse_rx = None;
            app.browse_step = BrowseStep::Family;
        }
        KeyCode::Up | KeyCode::Char('k') => {
            if app.browse_model_selected > 0 {
                app.browse_model_selected -= 1;
                if app.browse_model_selected < app.browse_model_scroll {
                    app.browse_model_scroll = app.browse_model_selected;
                }
            }
        }
        KeyCode::Down | KeyCode::Char('j') => {
            if count > 0 && app.browse_model_selected + 1 < count {
                app.browse_model_selected += 1;
            }
        }
        KeyCode::Char('r') => {
            populate_from_catalog(app);
            start_hf_api_query(app);
            app.set_message("Refreshing from HuggingFace…", MessageKind::Info);
        }
        KeyCode::Enter => {
            if count == 0 {
                return;
            }
            // If engine is locked from wizard, check compatibility
            if app.browse_engine_locked {
                let locked_id = ENGINES
                    .get(app.browse_engine_selected)
                    .map(|(id, _)| *id)
                    .unwrap_or("");
                let compatible = app
                    .browse_models
                    .get(app.browse_model_selected)
                    .map(|m| m.engines.iter().any(|e| e == locked_id))
                    .unwrap_or(false);

                if compatible {
                    // Engine is locked and compatible — skip Engine step, go to Confirm
                    app.browse_step = BrowseStep::Confirm;
                } else {
                    // Engine locked but model not compatible — warn and go to Engine step
                    // so user can see and pick a compatible engine (unlocks engine for this model)
                    app.browse_engine_locked = false;
                    let compatible_engines = engines_for_selected_model(app);
                    if !compatible_engines.is_empty() {
                        app.browse_engine_selected = compatible_engines[0].0;
                    }
                    app.set_message(
                        "Selected model is incompatible with the pre-selected engine. Choose a compatible one.",
                        MessageKind::Warning,
                    );
                    app.browse_step = BrowseStep::Engine;
                }
            } else {
                let compatible = engines_for_selected_model(app);
                if compatible.len() == 1 {
                    // Only one engine available — auto-select and skip Engine step
                    app.browse_engine_selected = compatible[0].0;
                    app.browse_step = BrowseStep::Confirm;
                } else if compatible.is_empty() {
                    app.set_message("No compatible engines found for this model.", MessageKind::Warning);
                } else {
                    // Pre-select first compatible engine then let user choose
                    app.browse_engine_selected = compatible[0].0;
                    app.browse_step = BrowseStep::Engine;
                }
            }
        }
        _ => {}
    }
}

fn handle_engine(app: &mut App, key: KeyEvent) {
    let compatible = engines_for_selected_model(app);
    let compatible_indices: Vec<usize> = compatible.iter().map(|(i, _, _)| *i).collect();

    match key.code {
        KeyCode::Esc => {
            app.browse_step = BrowseStep::Models;
        }
        KeyCode::Up | KeyCode::Char('k') => {
            // Navigate within compatible engines only
            if let Some(pos) = compatible_indices.iter().position(|&i| i == app.browse_engine_selected) {
                if pos > 0 {
                    app.browse_engine_selected = compatible_indices[pos - 1];
                }
            }
        }
        KeyCode::Down | KeyCode::Char('j') => {
            if let Some(pos) = compatible_indices.iter().position(|&i| i == app.browse_engine_selected) {
                if pos + 1 < compatible_indices.len() {
                    app.browse_engine_selected = compatible_indices[pos + 1];
                }
            }
        }
        KeyCode::Enter => {
            if !compatible.is_empty() {
                // Ensure selected index is valid
                if !compatible_indices.contains(&app.browse_engine_selected) {
                    app.browse_engine_selected = compatible_indices[0];
                }
                app.browse_step = BrowseStep::Confirm;
            }
        }
        _ => {}
    }
}

fn handle_confirm(app: &mut App, key: KeyEvent) {
    match key.code {
        KeyCode::Esc => {
            // If engine was auto-selected (only one compatible), go back to Models
            let compatible = engines_for_selected_model(app);
            if compatible.len() <= 1 {
                app.browse_step = BrowseStep::Models;
            } else {
                app.browse_step = BrowseStep::Engine;
            }
        }
        KeyCode::Enter => {
            trigger_download(app);
            if app.browse_return_to_wizard {
                app.browse_return_to_wizard = false;
                app.browse_engine_locked = false;
                app.wizard_step = crate::app::WizardStep::Confirm;
                app.screen = Screen::InstallWizard;
            }
        }
        KeyCode::Char('s') => {
            if app.browse_return_to_wizard {
                app.browse_return_to_wizard = false;
                app.browse_engine_locked = false;
                app.wizard_step = crate::app::WizardStep::Confirm;
                app.screen = Screen::InstallWizard;
            }
        }
        _ => {}
    }
}

// ---------------------------------------------------------------------------
// Download trigger
// ---------------------------------------------------------------------------

fn trigger_download(app: &mut App) {
    let model = match app.browse_models.get(app.browse_model_selected) {
        Some(m) => m.clone(),
        None => return,
    };

    let engine_id = ENGINES
        .get(app.browse_engine_selected)
        .map(|(id, _)| id.to_string())
        .unwrap_or_else(|| "mlx".to_string());

    let hf_repo = model.hf_repo.clone();

    if !app.browse_hf_token_input.is_empty() {
        save_hf_token(app);
    }

    let token = hf_token(app);

    let dl_state = crate::app::ModelDownloadState {
        name: hf_repo.clone(),
        role: engine_id.clone(),
        progress: 0.0,
        status: crate::app::DownloadStatus::Pending,
    };
    app.model_download_progress = vec![dl_state];

    app.model_recommendation = Some(crate::modules::models::ModelRecommendation {
        tier: busibox_core::hardware::MemoryTier::Entry,
        tier_description: "custom".to_string(),
        fast: crate::modules::models::ModelInfo {
            name: String::new(),
            role: "fast".to_string(),
            estimated_size_gb: 0.0,
            provider: engine_id.clone(),
        },
        agent: crate::modules::models::ModelInfo {
            name: hf_repo.clone(),
            role: "agent".to_string(),
            estimated_size_gb: model.size_gb as f64,
            provider: engine_id.clone(),
        },
        embed: crate::modules::models::ModelInfo {
            name: String::new(),
            role: "embed".to_string(),
            estimated_size_gb: 0.0,
            provider: "fastembed".to_string(),
        },
        reranker: None,
        whisper: None,
        kokoro: None,
        flux: None,
    });

    let result = run_hf_download(&hf_repo, token.as_deref());
    match result {
        Ok(_) => {
            if let Some(dl) = app.model_download_progress.first_mut() {
                dl.status = crate::app::DownloadStatus::Complete;
                dl.progress = 1.0;
            }
            app.set_message(
                &format!("Downloaded {hf_repo} successfully."),
                MessageKind::Success,
            );
        }
        Err(e) => {
            if let Some(dl) = app.model_download_progress.first_mut() {
                dl.status = crate::app::DownloadStatus::Failed(e.clone());
            }
            app.set_message(&format!("Download failed: {e}"), MessageKind::Error);
        }
    }

    app.screen = Screen::ModelDownload;
}

fn run_hf_download(hf_repo: &str, token: Option<&str>) -> Result<(), String> {
    let mut cmd = std::process::Command::new("huggingface-cli");
    cmd.arg("download").arg(hf_repo);
    if let Some(tok) = token {
        cmd.env("HF_TOKEN", tok);
    }

    let status = cmd.status().map_err(|e| {
        format!("huggingface-cli not found: {e}. Install with: pip install huggingface_hub")
    })?;

    if status.success() {
        Ok(())
    } else {
        Err(format!(
            "huggingface-cli exit code {}",
            status.code().unwrap_or(-1)
        ))
    }
}
