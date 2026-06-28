use crate::app::{App, BrowsableModel, BrowseStep, MessageKind, Screen};
use crate::theme;
use busibox_core::model_catalog::{self, CatalogModel, CATALOG};
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

/// Engine options with display labels.
pub const ENGINES: &[(&str, &str)] = &[
    ("mlx",   "MLX  — Apple Silicon (unified memory, mlx-lm)"),
    ("vllm",  "vLLM — NVIDIA GPU (CUDA, tensor parallel)"),
    ("gguf",  "GGUF — llama.cpp (CPU / any hardware)"),
];

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

fn hf_token(app: &App) -> Option<String> {
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

/// RAM budget available for models (system RAM minus busibox overhead).
fn budget_gb(app: &App) -> f32 {
    let hw = if app.setup_target == crate::app::SetupTarget::Remote {
        app.remote_hardware.as_ref()
    } else {
        app.local_hardware.as_ref()
    };
    hw.map(|h| (h.ram_gb as f32).max(0.0) - 5.0)
        .unwrap_or(16.0 - 5.0)
        .max(1.0)
}

/// Populate `app.browse_models` from the curated catalog for the currently
/// selected family + engine. Sorted: fits-first, then by param count desc.
pub fn populate_from_catalog(app: &mut App) {
    let family_id = FAMILIES
        .get(app.browse_family_selected)
        .map(|(id, _)| *id)
        .unwrap_or("qwen");
    let engine_id = ENGINES
        .get(app.browse_engine_selected)
        .map(|(id, _)| *id)
        .unwrap_or("mlx");
    let budget = budget_gb(app);

    let mut models: Vec<BrowsableModel> = model_catalog::all_models_for(family_id, engine_id)
        .into_iter()
        .map(|m: &'static CatalogModel| BrowsableModel {
            hf_repo: m.hf_repo.to_string(),
            display_name: m.display_name.to_string(),
            param_billions: m.param_billions,
            quantization: m.quantization.to_string(),
            size_gb: m.size_gb,
            description: m.description.to_string(),
            gated: m.gated,
            curated: true,
            downloads: 0,
        })
        .collect();

    // Sort: fits-in-RAM first, then by param count descending (bigger = better)
    models.sort_by(|a, b| {
        let a_fits = a.size_gb <= budget;
        let b_fits = b.size_gb <= budget;
        match (a_fits, b_fits) {
            (true, false) => std::cmp::Ordering::Less,
            (false, true) => std::cmp::Ordering::Greater,
            _ => b
                .param_billions
                .partial_cmp(&a.param_billions)
                .unwrap_or(std::cmp::Ordering::Equal),
        }
    });

    app.browse_models = models;
    app.browse_model_selected = 0;
    app.browse_model_scroll = 0;
}

/// Kick off a background thread that queries the HF API and sends results back.
pub fn start_hf_api_query(app: &mut App) {
    let family_id = FAMILIES
        .get(app.browse_family_selected)
        .map(|(id, _)| id.to_string())
        .unwrap_or_else(|| "qwen".to_string());
    let engine_id = ENGINES
        .get(app.browse_engine_selected)
        .map(|(id, _)| id.to_string())
        .unwrap_or_else(|| "mlx".to_string());
    let token = hf_token(app);
    let budget = budget_gb(app);

    let (tx, rx) = mpsc::channel::<BrowseUpdate>();
    app.browse_rx = Some(rx);
    app.browse_loading = true;

    std::thread::spawn(move || {
        match query_hf_api(&family_id, &engine_id, token.as_deref(), budget) {
            Ok(models) => {
                let _ = tx.send(BrowseUpdate::Results(models));
            }
            Err(e) => {
                let _ = tx.send(BrowseUpdate::Error(e));
            }
        }
    });
}

/// Query HF API for models matching the family+engine, merge with curated list.
fn query_hf_api(
    family: &str,
    engine: &str,
    token: Option<&str>,
    budget_gb: f32,
) -> Result<Vec<BrowsableModel>, String> {
    // Determine HF org / search strategy per family
    let (search_author, search_tag) = match family {
        "qwen"     => ("Qwen", match engine { "mlx" => "mlx-community", _ => "Qwen" }),
        "deepseek" => ("deepseek-ai", match engine { "mlx" => "mlx-community", _ => "deepseek-ai" }),
        "gemma"    => ("google", match engine { "mlx" => "mlx-community", _ => "google" }),
        "glm"      => ("THUDM", "THUDM"),
        "kimi"     => ("moonshotai", match engine { "mlx" => "mlx-community", _ => "moonshotai" }),
        _          => return Ok(vec![]),
    };

    // Build query: filter by tag relevant to engine
    let engine_tag = match engine {
        "mlx"  => "mlx",
        "vllm" => "text-generation-inference",
        "gguf" => "gguf",
        _      => return Ok(vec![]),
    };

    let url = format!(
        "https://huggingface.co/api/models?author={}&tags={}&sort=downloads&limit=20",
        search_tag, engine_tag
    );

    // Perform the HTTP request using curl (avoids adding reqwest as a dep)
    let mut cmd = std::process::Command::new("curl");
    cmd.arg("-fsSL").arg("--max-time").arg("10").arg(&url);
    if let Some(tok) = token {
        cmd.arg("-H").arg(format!("Authorization: Bearer {tok}"));
    }

    let output = cmd
        .output()
        .map_err(|e| format!("curl failed: {e}"))?;

    if !output.status.success() {
        return Err(format!(
            "HF API returned HTTP {}",
            output.status.code().unwrap_or(-1)
        ));
    }

    let body = String::from_utf8_lossy(&output.stdout);
    let parsed: serde_json::Value =
        serde_json::from_str(&body).map_err(|e| format!("JSON parse error: {e}"))?;

    let items = match parsed.as_array() {
        Some(a) => a,
        None => return Ok(vec![]),
    };

    // Get the curated set as a deduplicated baseline
    let curated: Vec<BrowsableModel> = model_catalog::all_models_for(family, engine)
        .into_iter()
        .map(|m| BrowsableModel {
            hf_repo: m.hf_repo.to_string(),
            display_name: m.display_name.to_string(),
            param_billions: m.param_billions,
            quantization: m.quantization.to_string(),
            size_gb: m.size_gb,
            description: m.description.to_string(),
            gated: m.gated,
            curated: true,
            downloads: 0,
        })
        .collect();

    let curated_repos: std::collections::HashSet<String> =
        curated.iter().map(|m| m.hf_repo.clone()).collect();

    // Pull family-name prefix for filtering results
    let family_prefix = match family {
        "qwen"     => vec!["Qwen", "qwen"],
        "deepseek" => vec!["DeepSeek", "deepseek"],
        "gemma"    => vec!["gemma", "Gemma"],
        "glm"      => vec!["GLM", "glm", "chatglm"],
        "kimi"     => vec!["Kimi", "kimi"],
        _          => vec![],
    };

    let mut api_models: Vec<BrowsableModel> = items
        .iter()
        .filter_map(|item| {
            let repo = item["modelId"].as_str().or_else(|| item["id"].as_str())?;

            // Must mention the family in the repo name
            let repo_lower = repo.to_lowercase();
            if !family_prefix
                .iter()
                .any(|p| repo_lower.contains(&p.to_lowercase()))
            {
                return None;
            }

            // Skip already-curated entries (we'll merge later)
            if curated_repos.contains(repo) {
                return None;
            }

            let downloads = item["downloads"].as_u64().unwrap_or(0);
            let gated = item["gated"].as_bool().unwrap_or(false);

            // Estimate size from siblings metadata if available
            let size_gb = item["safetensors"]["total"]
                .as_f64()
                .map(|b| b / 1e9)
                .unwrap_or_else(|| estimate_size_from_repo(repo, engine)) as f32;

            // Derive display name from repo basename
            let display = repo.rsplit('/').next().unwrap_or(repo);
            let quant = infer_quantization(display, engine);

            Some(BrowsableModel {
                hf_repo: repo.to_string(),
                display_name: display.to_string(),
                param_billions: infer_params(display),
                quantization: quant,
                size_gb,
                description: String::new(),
                gated,
                curated: false,
                downloads,
            })
        })
        .collect();

    // Merge: curated first (with download counts patched in), then API extras
    let mut merged = curated;
    merged.append(&mut api_models);

    // Sort: fits-in-RAM first, then by downloads desc (for API entries) / params desc (for curated)
    merged.sort_by(|a, b| {
        let a_fits = a.size_gb <= budget_gb;
        let b_fits = b.size_gb <= budget_gb;
        match (a_fits, b_fits) {
            (true, false) => std::cmp::Ordering::Less,
            (false, true) => std::cmp::Ordering::Greater,
            _ => {
                if a.curated && b.curated {
                    b.param_billions
                        .partial_cmp(&a.param_billions)
                        .unwrap_or(std::cmp::Ordering::Equal)
                } else {
                    b.downloads.cmp(&a.downloads)
                }
            }
        }
    });

    Ok(merged)
}

fn infer_params(repo: &str) -> f32 {
    let lower = repo.to_lowercase();
    // Simple heuristic: look for patterns like "7b", "13b", "0.8b", "35b-a3b"
    for token in lower.split(['-', '_', '.']) {
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
    } else if lower.contains("8bit") || lower.contains("8-bit") {
        "8-bit".into()
    } else if lower.contains("4bit") || lower.contains("4-bit") {
        "4-bit".into()
    } else if lower.contains("q4_k_m") {
        "GGUF Q4_K_M".into()
    } else if lower.contains("q8_0") {
        "GGUF Q8_0".into()
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

fn estimate_size_from_repo(repo: &str, _engine: &str) -> f64 {
    let params = infer_params(repo);
    if params > 0.0 {
        // Rough heuristic: BF16 ~2 bytes/param, 4-bit ~0.5 bytes/param
        let lower = repo.to_lowercase();
        let bytes_per_param: f64 = if lower.contains("4bit") || lower.contains("awq") || lower.contains("q4") {
            0.55
        } else if lower.contains("fp8") {
            1.05
        } else if lower.contains("8bit") {
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
            BrowseUpdate::Results(models) => {
                app.browse_models = models;
                app.browse_model_selected = 0;
                app.browse_model_scroll = 0;
                app.browse_loading = false;
            }
            BrowseUpdate::Error(e) => {
                app.set_message(&format!("HF API error: {e}. Showing curated list."), MessageKind::Warning);
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
            Constraint::Length(3), // title
            Constraint::Min(10),   // content
            Constraint::Length(3), // help bar
        ])
        .margin(2)
        .split(area);

    let (step_num, step_title) = match &app.browse_step {
        BrowseStep::Token   => (1, "HuggingFace Token"),
        BrowseStep::Family  => (2, "Model Family"),
        BrowseStep::Engine  => (3, "Inference Engine"),
        BrowseStep::Models  => (4, "Select Model"),
        BrowseStep::Confirm => (5, "Confirm Download"),
    };
    let title = Paragraph::new(format!("  Browse HuggingFace Models — Step {step_num}/5: {step_title}"))
        .style(theme::title())
        .alignment(Alignment::Center);
    f.render_widget(title, chunks[0]);

    match &app.browse_step {
        BrowseStep::Token   => render_token(f, app, chunks[1]),
        BrowseStep::Family  => render_family(f, app, chunks[1]),
        BrowseStep::Engine  => render_engine(f, app, chunks[1]),
        BrowseStep::Models  => render_models(f, app, chunks[1]),
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
    let content = Paragraph::new(lines).block(block);
    f.render_widget(content, area);
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
    let content = Paragraph::new(lines).block(block);
    f.render_widget(content, area);
}

fn render_engine(f: &mut Frame, app: &App, area: Rect) {
    let hw = if app.setup_target == crate::app::SetupTarget::Remote {
        app.remote_hardware.as_ref()
    } else {
        app.local_hardware.as_ref()
    };

    let recommended_engine = hw.map(|h| match &h.llm_backend {
        busibox_core::hardware::LlmBackend::Mlx => "mlx",
        busibox_core::hardware::LlmBackend::Vllm => "vllm",
        busibox_core::hardware::LlmBackend::Cloud => "mlx",
    });

    let budget = budget_gb(app);

    let mut lines = Vec::new();
    lines.push(Line::from(""));
    lines.push(Line::from(vec![
        Span::styled("  Select inference engine", theme::muted()),
        if let Some(gb) = hw.map(|h| h.ram_gb) {
            Span::styled(
                format!("  (System RAM: {gb}GB, model budget: {:.0}GB)", budget),
                theme::dim(),
            )
        } else {
            Span::styled("", theme::dim())
        },
    ]));
    lines.push(Line::from(""));

    for (i, (id, label)) in ENGINES.iter().enumerate() {
        let is_recommended = recommended_engine.map(|r| r == *id).unwrap_or(false);
        let style = if i == app.browse_engine_selected {
            theme::selected()
        } else {
            theme::normal()
        };
        let prefix = if i == app.browse_engine_selected { "▶ " } else { "  " };
        let rec_tag = if is_recommended { " ★ recommended" } else { "" };
        lines.push(Line::from(vec![
            Span::styled(format!("{prefix}{label}"), style),
            Span::styled(rec_tag, theme::success()),
        ]));
    }

    let block = Block::default()
        .borders(Borders::ALL)
        .border_style(theme::dim())
        .title(" Inference Engine ")
        .title_style(theme::heading());
    let content = Paragraph::new(lines).block(block);
    f.render_widget(content, area);
}

fn render_models(f: &mut Frame, app: &App, area: Rect) {
    let budget = budget_gb(app);

    // Loading indicator
    if app.browse_loading {
        let content = Paragraph::new(vec![
            Line::from(""),
            Line::from(Span::styled("  Fetching models from HuggingFace…", theme::info())),
            Line::from(Span::styled("  Curated models shown below while loading.", theme::dim())),
        ])
        .block(
            Block::default()
                .borders(Borders::ALL)
                .border_style(theme::dim())
                .title(" Loading… ")
                .title_style(theme::heading()),
        );
        f.render_widget(content, area);
    }

    if app.browse_models.is_empty() {
        let msg = if app.browse_loading { "" } else { "  No models found for this family + engine combination." };
        let content = Paragraph::new(msg)
            .style(theme::muted())
            .block(
                Block::default()
                    .borders(Borders::ALL)
                    .border_style(theme::dim()),
            );
        f.render_widget(content, area);
        return;
    }

    let mut lines = Vec::new();

    for (i, m) in app.browse_models.iter().enumerate() {
        let fits = m.size_gb <= budget;
        let is_selected = i == app.browse_model_selected;

        let name_style = if is_selected {
            theme::selected()
        } else if fits {
            theme::normal()
        } else {
            theme::dim()
        };

        let size_style = if fits { theme::success() } else { theme::error() };
        let fits_icon = if fits { "✓" } else { "✗" };
        let curated_tag = if m.curated { " ★" } else { "" };
        let gated_tag = if m.gated { " 🔒" } else { "" };
        let prefix = if is_selected { "▶ " } else { "  " };

        lines.push(Line::from(vec![
            Span::styled(
                format!("{prefix}{}", m.display_name),
                name_style,
            ),
            Span::styled(curated_tag, theme::warning()),
            Span::styled(gated_tag, theme::dim()),
        ]));

        if is_selected {
            lines.push(Line::from(vec![
                Span::styled("    ", theme::dim()),
                Span::styled(fits_icon, size_style),
                Span::styled(format!(" {:.1}GB", m.size_gb), size_style),
                Span::styled(" · ", theme::dim()),
                Span::styled(&m.quantization, theme::info()),
                if !m.description.is_empty() {
                    Span::styled(format!(" · {}", m.description), theme::dim())
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
            " Models ({} available, budget {:.0}GB) ",
            app.browse_models.len(),
            budget,
        ))
        .title_style(theme::heading());

    let content = Paragraph::new(lines)
        .scroll((scroll as u16, 0))
        .block(block);
    f.render_widget(content, area);

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

fn render_confirm(f: &mut Frame, app: &App, area: Rect) {
    let model = app.browse_models.get(app.browse_model_selected);

    let family_label = FAMILIES
        .get(app.browse_family_selected)
        .map(|(_, l)| *l)
        .unwrap_or("Unknown");
    let engine_label = ENGINES
        .get(app.browse_engine_selected)
        .map(|(_, l)| *l)
        .unwrap_or("Unknown");
    let budget = budget_gb(app);

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
        let size_style = if m.size_gb <= budget { theme::success() } else { theme::warning() };
        lines.push(Line::from(vec![
            Span::styled("  Size:           ", theme::muted()),
            Span::styled(format!("{:.1} GB", m.size_gb), size_style),
            Span::styled(format!(" (budget {:.0} GB)", budget), theme::dim()),
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
    lines.push(Line::from(vec![
        Span::styled("  Family: ", theme::muted()),
        Span::styled(family_label, theme::normal()),
    ]));
    lines.push(Line::from(""));
    lines.push(Line::from(vec![
        Span::styled("  Press ", theme::dim()),
        Span::styled("Enter", theme::success()),
        Span::styled(" to start download, ", theme::dim()),
        Span::styled("Esc", theme::muted()),
        Span::styled(" to go back.", theme::dim()),
    ]));

    let block = Block::default()
        .borders(Borders::ALL)
        .border_style(theme::dim())
        .title(" Confirm Download ")
        .title_style(theme::heading());
    let content = Paragraph::new(lines).block(block);
    f.render_widget(content, area);
}

fn render_help(f: &mut Frame, app: &App, area: Rect) {
    let text = match &app.browse_step {
        BrowseStep::Token   => " Enter Confirm/Skip  Esc Back  (type to enter token)",
        BrowseStep::Family  => " ↑↓ Navigate  Enter Select  Esc Back",
        BrowseStep::Engine  => " ↑↓ Navigate  Enter Select  Esc Back",
        BrowseStep::Models  => " ↑↓ Navigate  Enter Select  r Refresh from HF  Esc Back",
        BrowseStep::Confirm => " Enter Download  Esc Back",
    };
    let help = Paragraph::new(Line::from(Span::styled(text, theme::muted())));
    f.render_widget(help, area);
}

// ---------------------------------------------------------------------------
// Key handler
// ---------------------------------------------------------------------------

pub fn handle_key(app: &mut App, key: KeyEvent) {
    // Poll any in-flight HF API results first
    drain_browse_rx(app);

    match &app.browse_step.clone() {
        BrowseStep::Token   => handle_token(app, key),
        BrowseStep::Family  => handle_family(app, key),
        BrowseStep::Engine  => handle_engine(app, key),
        BrowseStep::Models  => handle_models(app, key),
        BrowseStep::Confirm => handle_confirm(app, key),
    }
}

fn handle_token(app: &mut App, key: KeyEvent) {
    match key.code {
        KeyCode::Esc => {
            app.screen = Screen::ModelsManage;
        }
        KeyCode::Enter => {
            // If there's something typed, save it
            if !app.browse_hf_token_input.is_empty() {
                save_hf_token(app);
            }
            // Proceed regardless (token is optional)
            app.browse_step = BrowseStep::Family;
        }
        KeyCode::Char('c') if key.modifiers.contains(KeyModifiers::CONTROL) => {
            app.screen = Screen::ModelsManage;
        }
        KeyCode::Char('c') => {
            // Clear existing token
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
    // Save to profile defaults so it persists
    let repo_root = app.repo_root.clone();
    if let Some(profiles) = &mut app.profiles {
        let defaults = profiles.defaults.get_or_insert_with(Default::default);
        defaults.huggingface_token = Some(token.clone());
        let _ = crate::modules::profile::save_profiles(&repo_root, profiles);
    }
    app.browse_hf_token_input.clear();
    app.set_message("HF token saved.", MessageKind::Success);
}

fn handle_family(app: &mut App, key: KeyEvent) {
    match key.code {
        KeyCode::Esc => {
            if hf_token(app).is_some() {
                app.browse_step = BrowseStep::Token;
            } else {
                app.screen = Screen::ModelsManage;
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
            app.browse_step = BrowseStep::Engine;
        }
        _ => {}
    }
}

fn handle_engine(app: &mut App, key: KeyEvent) {
    match key.code {
        KeyCode::Esc => {
            app.browse_step = BrowseStep::Family;
        }
        KeyCode::Up | KeyCode::Char('k') => {
            if app.browse_engine_selected > 0 {
                app.browse_engine_selected -= 1;
            }
        }
        KeyCode::Down | KeyCode::Char('j') => {
            if app.browse_engine_selected + 1 < ENGINES.len() {
                app.browse_engine_selected += 1;
            }
        }
        KeyCode::Enter => {
            // Populate curated list first (instant), then start API query
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
            app.browse_step = BrowseStep::Engine;
            app.browse_loading = false;
            app.browse_rx = None;
        }
        KeyCode::Up | KeyCode::Char('k') => {
            if app.browse_model_selected > 0 {
                app.browse_model_selected -= 1;
                // Keep selection visible
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
            // Manual refresh from HF API
            populate_from_catalog(app);
            start_hf_api_query(app);
            app.set_message("Refreshing from HuggingFace…", MessageKind::Info);
        }
        KeyCode::Enter => {
            if count > 0 {
                app.browse_step = BrowseStep::Confirm;
            }
        }
        _ => {}
    }
}

fn handle_confirm(app: &mut App, key: KeyEvent) {
    match key.code {
        KeyCode::Esc => {
            app.browse_step = BrowseStep::Models;
        }
        KeyCode::Enter => {
            trigger_download(app);
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

    // Save token if there's one in the input buffer
    if !app.browse_hf_token_input.is_empty() {
        save_hf_token(app);
    }

    let token = hf_token(app);

    // Build a ModelDownloadState list and send to the existing download screen
    let dl_state = crate::app::ModelDownloadState {
        name: hf_repo.clone(),
        role: engine_id.clone(),
        progress: 0.0,
        status: crate::app::DownloadStatus::Pending,
    };
    app.model_download_progress = vec![dl_state];

    // Also set a ModelRecommendation so the existing model_download screen can display it
    // (we construct a minimal one with just this model as the "agent" role)
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

    // Perform the download directly using huggingface-cli
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
            app.set_message(
                &format!("Download failed: {e}"),
                MessageKind::Error,
            );
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

    let status = cmd
        .status()
        .map_err(|e| format!("huggingface-cli not found: {e}. Install with: pip install huggingface_hub"))?;

    if status.success() {
        Ok(())
    } else {
        Err(format!(
            "huggingface-cli exit code {}",
            status.code().unwrap_or(-1)
        ))
    }
}
