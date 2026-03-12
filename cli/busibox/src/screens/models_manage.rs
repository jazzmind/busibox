use crate::app::{App, GpuAssignment, MessageKind, ModelsFocus, ModelsManageUpdate, Screen};
use crate::modules::hardware::{LlmBackend, MemoryTier};
use crate::modules::models::TierModelSet;
use crate::modules::remote;
use crate::theme;
use crossterm::event::{KeyCode, KeyEvent};
use ratatui::layout::Margin;
use ratatui::prelude::*;
use ratatui::widgets::{Scrollbar, ScrollbarOrientation, ScrollbarState, *};
use std::collections::HashMap;

const SPINNER: &[&str] = &["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"];

fn capitalize(s: &str) -> String {
    let mut chars = s.chars();
    match chars.next() {
        None => String::new(),
        Some(c) => c.to_uppercase().collect::<String>() + chars.as_str(),
    }
}

fn shell_escape(s: &str) -> String {
    format!("'{}'", s.replace('\'', "'\\''"))
}

fn get_hardware(app: &App) -> Option<&crate::modules::hardware::HardwareProfile> {
    let profile = app.active_profile().map(|(_, p)| p);
    let is_remote = profile.map(|p| p.remote).unwrap_or(false);
    if is_remote {
        app.remote_hardware
            .as_ref()
            .or_else(|| profile.and_then(|p| p.hardware.as_ref()))
    } else {
        app.local_hardware.as_ref()
    }
}

fn get_gpu_count(app: &App) -> usize {
    get_hardware(app).map(|h| h.gpus.len()).unwrap_or(0)
}

pub const CUSTOM_TIER_INDEX: usize = 4; // After the 4 standard MemoryTier entries (0-3)

/// Check whether a deployed model_config.yml exists (locally).
fn has_deployed_config(app: &App) -> bool {
    app.repo_root
        .join("provision/ansible/group_vars/all/model_config.yml")
        .exists()
}

/// Populate GPU assignments from a TierModelSet.
fn apply_gpu_assignments_from_tier_set(app: &mut App, tier_set: &TierModelSet) {
    app.models_manage_gpu_assignments.clear();
    for model in &tier_set.models {
        if !model.needs_gpu {
            continue;
        }
        if let Some(ref gpu_str) = model.gpu {
            let gpus: Vec<usize> = gpu_str
                .split(',')
                .filter_map(|s| s.trim().parse().ok())
                .collect();
            let tp = model.tensor_parallel.map(|v| v > 1).unwrap_or(false);
            app.models_manage_gpu_assignments.insert(
                model.model_key.clone(),
                crate::app::GpuAssignment {
                    gpus,
                    tensor_parallel: tp,
                },
            );
        }
    }
}

/// Load the deployed model_config.yml as a TierModelSet, enriched with registry metadata.
fn load_deployed_tier_set(app: &App) -> Option<TierModelSet> {
    let hw = get_hardware(app);
    let backend = hw.map(|h| h.llm_backend.clone()).unwrap_or(LlmBackend::Mlx);
    let model_config_path = app
        .repo_root
        .join("provision/ansible/group_vars/all/model_config.yml");
    let registry_path = app
        .repo_root
        .join("provision/ansible/group_vars/all/model_registry.yml");
    if model_config_path.exists() && registry_path.exists() {
        TierModelSet::from_deployed_config(&model_config_path, &registry_path, &backend).ok()
    } else {
        None
    }
}

/// Rebuild the cached TierModelSet and reset GPU assignments.
/// When the "Custom" slot is selected, loads from model_config.yml.
/// Otherwise loads the standard tier preset from model_registry.yml.
fn rebuild_tier_cache(app: &mut App) {
    let hw = get_hardware(app);
    let backend = hw.map(|h| h.llm_backend.clone()).unwrap_or(LlmBackend::Mlx);

    let mut tier_set = if app.models_manage_tier_selected == CUSTOM_TIER_INDEX {
        load_deployed_tier_set(app)
    } else {
        let tiers = MemoryTier::all();
        let selected_tier = tiers
            .get(app.models_manage_tier_selected)
            .copied()
            .unwrap_or(MemoryTier::Standard);
        let config_path = app
            .repo_root
            .join("provision/ansible/group_vars/all/model_registry.yml");
        if config_path.exists() {
            TierModelSet::from_config(&config_path, selected_tier, &backend).ok()
        } else {
            None
        }
    };

    // For vLLM backend, ensure GPU media models are present (they may not be
    // in model_config.yml but still consume GPU 0 VRAM).
    if backend == LlmBackend::Vllm {
        if let Some(ref mut ts) = tier_set {
            let registry_path = app
                .repo_root
                .join("provision/ansible/group_vars/all/model_registry.yml");
            ts.append_media_models(&registry_path);
        }
    }

    app.models_manage_gpu_assignments.clear();
    if let Some(ref ts) = tier_set {
        apply_gpu_assignments_from_tier_set(app, ts);
    }

    app.models_manage_tier_models = tier_set;
    app.models_manage_model_selected = 0;
}

pub fn init_screen(app: &mut App) {
    if app.models_manage_loaded {
        return;
    }
    app.models_manage_loaded = true;
    app.models_manage_focus = ModelsFocus::Tiers;
    app.models_manage_model_selected = 0;
    app.models_manage_gpu_assignments.clear();

    let is_mlx = get_hardware(app)
        .map(|h| matches!(h.llm_backend, LlmBackend::Mlx))
        .unwrap_or(false);

    let has_custom_deployed = !is_mlx && has_deployed_config(app);
    app.models_manage_is_custom = has_custom_deployed;

    if let Some((_, profile)) = app.active_profile() {
        let explicit_custom = profile
            .model_tier
            .as_deref()
            .map(|t| t.eq_ignore_ascii_case("custom"))
            .unwrap_or(false);

        if explicit_custom && has_custom_deployed {
            app.models_manage_tier_selected = CUSTOM_TIER_INDEX;
            app.models_manage_current_tier = Some("custom".to_string());
        } else if let Some(tier) = profile.effective_model_tier() {
            app.models_manage_tier_selected = tier.index();
            app.models_manage_current_tier = Some(tier.name().to_string());
        } else if has_custom_deployed {
            app.models_manage_tier_selected = CUSTOM_TIER_INDEX;
            app.models_manage_current_tier = Some("custom".to_string());
        }
    }

    rebuild_tier_cache(app);
}

pub fn render(f: &mut Frame, app: &App) {
    if app.models_manage_log_visible {
        render_log_viewer(f, app);
        return;
    }

    let chunks = Layout::default()
        .direction(Direction::Vertical)
        .constraints([
            Constraint::Length(3),
            Constraint::Min(12),
            Constraint::Length(3),
        ])
        .margin(2)
        .split(f.area());

    let title = Paragraph::new("Model Management")
        .style(theme::title())
        .alignment(Alignment::Center);
    f.render_widget(title, chunks[0]);

    let content_chunks = Layout::default()
        .direction(Direction::Horizontal)
        .constraints([
            Constraint::Percentage(25),
            Constraint::Percentage(75),
        ])
        .split(chunks[1]);

    render_tier_selector(f, app, content_chunks[0]);
    render_model_details(f, app, content_chunks[1]);

    let current_name = app
        .models_manage_current_tier
        .as_deref()
        .map(capitalize)
        .unwrap_or_else(|| "None".to_string());

    let selected_name = if app.models_manage_tier_selected == CUSTOM_TIER_INDEX {
        "Custom".to_string()
    } else {
        let selected_tier = MemoryTier::all()
            .get(app.models_manage_tier_selected)
            .copied()
            .unwrap_or(MemoryTier::Standard);
        capitalize(selected_tier.name())
    };
    let selected_tier_name = if app.models_manage_tier_selected == CUSTOM_TIER_INDEX {
        "custom"
    } else {
        MemoryTier::all()
            .get(app.models_manage_tier_selected)
            .map(|t| t.name())
            .unwrap_or("standard")
    };
    let is_changed = app
        .models_manage_current_tier
        .as_deref()
        .map(|c| c != selected_tier_name)
        .unwrap_or(true);

    let gpu_count = get_gpu_count(app);
    let has_gpus = gpu_count > 1;

    let mut help_spans = vec![
        Span::styled("Active: ", theme::muted()),
        Span::styled(current_name, theme::info()),
        Span::styled("  │  ", theme::dim()),
    ];

    match app.models_manage_focus {
        ModelsFocus::Tiers => {
            help_spans.push(Span::styled("↑/↓ ", theme::highlight()));
            help_spans.push(Span::styled("Tier  ", theme::normal()));
            if has_gpus {
                help_spans.push(Span::styled("Tab ", theme::highlight()));
                help_spans.push(Span::styled("GPU assign  ", theme::normal()));
            }
            help_spans.push(Span::styled("b ", theme::highlight()));
            help_spans.push(Span::styled("Bench  ", theme::normal()));
        }
        ModelsFocus::Models => {
            help_spans.push(Span::styled("↑/↓ ", theme::highlight()));
            help_spans.push(Span::styled("Model  ", theme::normal()));
            help_spans.push(Span::styled("0-9 ", theme::highlight()));
            help_spans.push(Span::styled("GPU  ", theme::normal()));
            help_spans.push(Span::styled("t ", theme::highlight()));
            help_spans.push(Span::styled("TP  ", theme::normal()));
            help_spans.push(Span::styled("r ", theme::highlight()));
            help_spans.push(Span::styled("Roles  ", theme::normal()));
            help_spans.push(Span::styled("a ", theme::highlight()));
            help_spans.push(Span::styled("Add  ", theme::normal()));
            help_spans.push(Span::styled("c ", theme::highlight()));
            help_spans.push(Span::styled("Change  ", theme::normal()));
            help_spans.push(Span::styled("d ", theme::highlight()));
            help_spans.push(Span::styled("Del  ", theme::normal()));
            help_spans.push(Span::styled("s ", theme::highlight()));
            help_spans.push(Span::styled("Save  ", theme::normal()));
            help_spans.push(Span::styled("b ", theme::highlight()));
            help_spans.push(Span::styled("Bench  ", theme::normal()));
            help_spans.push(Span::styled("Enter ", theme::highlight()));
            help_spans.push(Span::styled(
                format!("Apply {selected_name}  "),
                theme::success(),
            ));
            help_spans.push(Span::styled("Tab ", theme::highlight()));
            help_spans.push(Span::styled("Keep  ", theme::normal()));
            help_spans.push(Span::styled("Esc ", theme::muted()));
            help_spans.push(Span::styled("Revert", theme::warning()));
        }
    }

    if app.models_manage_focus == ModelsFocus::Tiers {
        help_spans.push(Span::styled("s ", theme::highlight()));
        help_spans.push(Span::styled("Save  ", theme::normal()));
        help_spans.push(Span::styled("Enter ", theme::highlight()));
        if is_changed {
            help_spans.push(Span::styled(
                format!("Apply {selected_name}  "),
                theme::success(),
            ));
        } else {
            help_spans.push(Span::styled("Re-apply  ", theme::success()));
        }
        help_spans.push(Span::styled("Esc ", theme::muted()));
        help_spans.push(Span::styled("Back", theme::muted()));
    }

    let help = Paragraph::new(Line::from(help_spans));
    f.render_widget(help, chunks[2]);

    if app.models_manage_add_mode {
        render_add_picker(f, app);
    }

    if app.models_manage_role_edit_mode {
        render_role_editor(f, app);
    }

    if let Some((ref msg, ref kind)) = app.status_message {
        let style = match kind {
            MessageKind::Success => theme::success(),
            MessageKind::Error => theme::error(),
            MessageKind::Warning => theme::warning(),
            MessageKind::Info => theme::info(),
        };
        let status_bar = Paragraph::new(Span::styled(msg, style)).alignment(Alignment::Center);
        let status_area = Rect {
            y: f.area().height.saturating_sub(1),
            height: 1,
            ..f.area()
        };
        f.render_widget(status_bar, status_area);
    }
}

fn render_add_picker(f: &mut Frame, app: &App) {
    let area = f.area();
    let popup_width = 70u16.min(area.width.saturating_sub(8));
    let popup_height = (app.models_manage_add_candidates.len() as u16 + 5).min(area.height.saturating_sub(6));
    let popup_area = Rect {
        x: area.x + (area.width.saturating_sub(popup_width)) / 2,
        y: area.y + (area.height.saturating_sub(popup_height)) / 2,
        width: popup_width,
        height: popup_height,
    };

    f.render_widget(Clear, popup_area);

    let inner_chunks = Layout::default()
        .direction(Direction::Vertical)
        .constraints([
            Constraint::Length(2),
            Constraint::Min(1),
            Constraint::Length(1),
        ])
        .split(popup_area.inner(Margin::new(1, 1)));

    let header_spans = vec![
        Span::styled(format!(" {:40}", "Model"), theme::heading()),
        Span::styled(format!("{:>7}", "Size"), theme::heading()),
        Span::styled(format!("  {:12}", "Provider"), theme::heading()),
    ];

    let mut lines: Vec<Line> = Vec::new();
    lines.push(Line::from(header_spans));
    lines.push(Line::from(Span::styled(
        " ".to_string() + &"─".repeat(popup_width as usize - 4),
        theme::dim(),
    )));

    let visible_height = inner_chunks[1].height as usize;
    let total = app.models_manage_add_candidates.len();
    let scroll_offset = if app.models_manage_add_selected >= visible_height {
        app.models_manage_add_selected - visible_height + 1
    } else {
        0
    };

    for (i, model) in app.models_manage_add_candidates.iter().enumerate() {
        if i < scroll_offset {
            continue;
        }
        if i >= scroll_offset + visible_height {
            break;
        }

        let is_selected = i == app.models_manage_add_selected;
        let name_display = if model.model_key.len() > 38 {
            format!("{}…", &model.model_key[..37])
        } else {
            model.model_key.clone()
        };
        let size_str = if model.estimated_size_gb < 1.0 {
            format!("{:.0} MB", model.estimated_size_gb * 1024.0)
        } else {
            format!("{:.1} GB", model.estimated_size_gb)
        };

        let row_style = if is_selected { theme::selected() } else { theme::normal() };
        let name_style = if is_selected { row_style } else { theme::info() };
        let size_style = if is_selected { row_style } else { theme::muted() };

        lines.push(Line::from(vec![
            Span::styled(format!(" {:40}", name_display), name_style),
            Span::styled(format!("{:>7}", size_str), size_style),
            Span::styled(format!("  {:12}", model.provider), row_style),
        ]));
    }

    let header_para = Paragraph::new(lines);
    f.render_widget(header_para, inner_chunks[0].union(inner_chunks[1]));

    let help = Paragraph::new(Line::from(vec![
        Span::styled(" ↑/↓ ", theme::highlight()),
        Span::styled("Select  ", theme::normal()),
        Span::styled("Enter ", theme::highlight()),
        Span::styled("Add  ", theme::success()),
        Span::styled("Esc ", theme::muted()),
        Span::styled("Cancel", theme::muted()),
        Span::styled(format!("  ({total} available)"), theme::dim()),
    ]));
    f.render_widget(help, inner_chunks[2]);

    let block = Block::default()
        .borders(Borders::ALL)
        .border_style(theme::highlight())
        .title(" Add Model ")
        .title_style(theme::heading());
    f.render_widget(block, popup_area);
}

fn render_role_editor(f: &mut Frame, app: &App) {
    let area = f.area();
    let model = app
        .models_manage_tier_models
        .as_ref()
        .and_then(|ts| ts.models.get(app.models_manage_model_selected));
    let (model_name, model_roles) = match model {
        Some(m) => (m.model_key.as_str(), &m.roles),
        None => return,
    };

    let role_count = app.models_manage_available_roles.len();
    let popup_width = 40u16.min(area.width.saturating_sub(8));
    let popup_height = (role_count as u16 + 5).min(area.height.saturating_sub(6));
    let popup_area = Rect {
        x: area.x + (area.width.saturating_sub(popup_width)) / 2,
        y: area.y + (area.height.saturating_sub(popup_height)) / 2,
        width: popup_width,
        height: popup_height,
    };

    f.render_widget(Clear, popup_area);

    let inner_chunks = Layout::default()
        .direction(Direction::Vertical)
        .constraints([
            Constraint::Length(1),
            Constraint::Min(1),
            Constraint::Length(1),
        ])
        .split(popup_area.inner(Margin::new(1, 1)));

    let mut lines: Vec<Line> = Vec::new();

    for (i, role) in app.models_manage_available_roles.iter().enumerate() {
        let is_selected = i == app.models_manage_role_edit_selected;
        let has_role = model_roles.contains(role);
        let check = if has_role { "[x]" } else { "[ ]" };

        let row_style = if is_selected { theme::selected() } else { theme::normal() };
        let check_style = if has_role {
            if is_selected { theme::selected() } else { theme::success() }
        } else {
            if is_selected { theme::selected() } else { theme::dim() }
        };

        lines.push(Line::from(vec![
            Span::styled(format!(" {check} "), check_style),
            Span::styled(format!("{role}"), row_style),
        ]));
    }

    let list = Paragraph::new(lines);
    f.render_widget(list, inner_chunks[1]);

    let help = Paragraph::new(Line::from(vec![
        Span::styled(" ↑/↓ ", theme::highlight()),
        Span::styled("Select  ", theme::normal()),
        Span::styled("Space ", theme::highlight()),
        Span::styled("Toggle  ", theme::success()),
        Span::styled("Esc ", theme::muted()),
        Span::styled("Done", theme::muted()),
    ]));
    f.render_widget(help, inner_chunks[2]);

    let title_display = if model_name.len() > 20 {
        format!(" Roles: {}… ", &model_name[..19])
    } else {
        format!(" Roles: {model_name} ")
    };

    let block = Block::default()
        .borders(Borders::ALL)
        .border_style(theme::highlight())
        .title(title_display)
        .title_style(theme::heading());
    f.render_widget(block, popup_area);
}

/// Total number of selectable tier entries (standard tiers + optional Custom).
fn tier_entry_count(app: &App) -> usize {
    let base = MemoryTier::all().len(); // 4
    if app.models_manage_is_custom {
        base + 1
    } else {
        base
    }
}

fn render_tier_selector(f: &mut Frame, app: &App, area: Rect) {
    let tiers = MemoryTier::all();

    let hw = get_hardware(app);
    let recommended_tier = hw.map(|h| h.memory_tier);
    let is_focused = app.models_manage_focus == ModelsFocus::Tiers;

    let mut items: Vec<ListItem> = tiers
        .iter()
        .enumerate()
        .map(|(i, tier)| {
            let is_recommended = recommended_tier.map(|r| r == *tier).unwrap_or(false);
            let is_current = app
                .models_manage_current_tier
                .as_deref()
                .map(|c| c == tier.name())
                .unwrap_or(false);

            let marker = if is_current {
                "● "
            } else if is_recommended {
                "★ "
            } else {
                "  "
            };
            let name = format!("{}{}", marker, capitalize(tier.name()));

            let style = if i == app.models_manage_tier_selected && is_focused {
                theme::selected()
            } else if i == app.models_manage_tier_selected {
                theme::highlight()
            } else if is_current {
                theme::success()
            } else if is_recommended {
                theme::highlight()
            } else {
                theme::normal()
            };

            let ram = tier.ram_range();
            ListItem::new(vec![
                Line::from(Span::styled(name, style)),
                Line::from(Span::styled(format!("    {ram}"), theme::muted())),
                Line::from(""),
            ])
        })
        .collect();

    if app.models_manage_is_custom {
        let is_custom_current = app
            .models_manage_current_tier
            .as_deref()
            .map(|c| c == "custom")
            .unwrap_or(false);
        let marker = if is_custom_current { "● " } else { "  " };
        let name = format!("{marker}Custom");
        let style = if CUSTOM_TIER_INDEX == app.models_manage_tier_selected && is_focused {
            theme::selected()
        } else if CUSTOM_TIER_INDEX == app.models_manage_tier_selected {
            theme::highlight()
        } else if is_custom_current {
            theme::success()
        } else {
            theme::normal()
        };
        items.push(ListItem::new(vec![
            Line::from(Span::styled(name, style)),
            Line::from(Span::styled("    Deployed", theme::muted())),
            Line::from(""),
        ]));
    }

    let border_style = if is_focused {
        theme::highlight()
    } else {
        theme::dim()
    };

    let list = List::new(items).block(
        Block::default()
            .borders(Borders::ALL)
            .border_style(border_style)
            .title(" Select Tier ")
            .title_style(theme::heading()),
    );
    f.render_widget(list, area);
}

fn render_model_details(f: &mut Frame, app: &App, area: Rect) {
    let hw = get_hardware(app);
    let backend = hw.map(|h| &h.llm_backend);
    let gpu_count = get_gpu_count(app);
    let has_gpus = gpu_count > 1;
    let is_focused = app.models_manage_focus == ModelsFocus::Models;

    let tier_set = app.models_manage_tier_models.as_ref();

    let backend_label = match backend {
        Some(LlmBackend::Mlx) => "MLX",
        Some(LlmBackend::Vllm) => "vLLM",
        Some(LlmBackend::Cloud) => "Cloud",
        None => "Unknown",
    };

    let (header_name, header_desc) = if app.models_manage_tier_selected == CUSTOM_TIER_INDEX {
        ("Custom".to_string(), "Deployed configuration".to_string())
    } else {
        let tiers = MemoryTier::all();
        let selected_tier = tiers
            .get(app.models_manage_tier_selected)
            .copied()
            .unwrap_or(MemoryTier::Standard);
        (capitalize(selected_tier.name()), selected_tier.description().to_string())
    };

    let mut lines: Vec<Line> = Vec::new();

    lines.push(Line::from(vec![
        Span::styled(
            format!(" {header_name} "),
            theme::heading(),
        ),
        Span::styled(
            format!("— {header_desc} ({backend_label})"),
            theme::muted(),
        ),
    ]));

    if has_gpus {
        let gpu_names: Vec<String> = hw
            .map(|h| {
                h.gpus
                    .iter()
                    .enumerate()
                    .map(|(i, g)| format!("GPU {i}: {} ({}GB)", g.name, g.vram_gb))
                    .collect()
            })
            .unwrap_or_default();
        lines.push(Line::from(Span::styled(
            format!(" GPUs: {}", gpu_names.join(", ")),
            theme::muted(),
        )));

        // Per-GPU VRAM usage summary with headroom
        if let (Some(ref ts), Some(h)) = (tier_set, hw) {
            let mut gpu_usage: HashMap<usize, f64> = HashMap::new();
            for model in &ts.models {
                if !model.needs_gpu {
                    continue;
                }
                let assigned_gpus: Vec<usize> = if model.is_media {
                    // Media models are always on GPU 0
                    vec![0]
                } else if let Some(assign) = app.models_manage_gpu_assignments.get(&model.model_key) {
                    assign.gpus.clone()
                } else if let Some(ref gpu_str) = model.gpu {
                    gpu_str.split(',').filter_map(|s| s.trim().parse().ok()).collect()
                } else {
                    continue;
                };

                let vram_gb = if let Some(gmu) = model.gpu_memory_utilization {
                    // Use gpu_memory_utilization fraction * first assigned GPU's VRAM
                    let gpu_vram = assigned_gpus.first()
                        .and_then(|&idx| h.gpus.get(idx))
                        .map(|g| g.vram_gb as f64)
                        .unwrap_or(24.0);
                    gmu * gpu_vram
                } else {
                    model.estimated_size_gb
                };

                for &gpu_idx in &assigned_gpus {
                    *gpu_usage.entry(gpu_idx).or_insert(0.0) += vram_gb;
                }
            }

            let mut usage_spans: Vec<Span> = vec![Span::styled(" VRAM: ", theme::muted())];
            for (i, gpu_info) in h.gpus.iter().enumerate() {
                let used = gpu_usage.get(&i).copied().unwrap_or(0.0);
                let total = gpu_info.vram_gb as f64;
                let free = total - used;
                let pct_free = if total > 0.0 { free / total } else { 0.0 };

                let color = if pct_free > 0.30 {
                    theme::success()
                } else if pct_free > 0.10 {
                    theme::warning()
                } else {
                    theme::error()
                };

                if i > 0 {
                    usage_spans.push(Span::styled("  ", theme::dim()));
                }
                usage_spans.push(Span::styled(
                    format!("GPU {i}: {used:.1}/{total:.0}GB ({free:.1} free)"),
                    color,
                ));
            }
            lines.push(Line::from(usage_spans));
        }
    }

    lines.push(Line::from(""));

    if has_gpus {
        let header_spans = vec![
            Span::styled(format!(" {:12}", "Role(s)"), theme::heading()),
            Span::styled(format!("{:38}", "Model"), theme::heading()),
            Span::styled(format!("{:>7}", "Size"), theme::heading()),
            Span::styled(format!("  {:>8}", "GPU"), theme::heading()),
        ];
        lines.push(Line::from(header_spans));
        lines.push(Line::from(Span::styled(
            " ─".to_string() + &"─".repeat(69),
            theme::dim(),
        )));
    } else {
        let header_spans = vec![
            Span::styled(format!(" {:12}", "Role(s)"), theme::heading()),
            Span::styled(format!("{:38}", "Model"), theme::heading()),
            Span::styled(format!("{:>7}", "Size"), theme::heading()),
        ];
        lines.push(Line::from(header_spans));
        lines.push(Line::from(Span::styled(
            " ─".to_string() + &"─".repeat(58),
            theme::dim(),
        )));
    }

    if let Some(ref ts) = tier_set {
        let mut unique_sizes: HashMap<&str, f64> = HashMap::new();

        for (i, model) in ts.models.iter().enumerate() {
            let is_selected = is_focused && i == app.models_manage_model_selected;

            let roles_str = model.roles.join(", ");
            let roles_display = if roles_str.len() > 11 {
                format!("{}…", &roles_str[..10])
            } else {
                roles_str
            };

            let name_display = if model.model_key.len() > 36 {
                format!("{}…", &model.model_key[..35])
            } else {
                model.model_key.clone()
            };

            let size_str = if model.estimated_size_gb < 1.0 {
                format!("{:.0} MB", model.estimated_size_gb * 1024.0)
            } else {
                format!("{:.1} GB", model.estimated_size_gb)
            };

            let row_style = if is_selected {
                theme::selected()
            } else if model.is_media {
                theme::dim()
            } else {
                theme::normal()
            };

            let mut spans = vec![
                Span::styled(
                    format!(" {:12}", roles_display),
                    if is_selected { row_style } else if model.is_media { theme::dim() } else { theme::info() },
                ),
                Span::styled(format!("{:38}", name_display), row_style),
                Span::styled(
                    format!("{:>7}", size_str),
                    if is_selected { row_style } else { theme::muted() },
                ),
            ];

            if has_gpus {
                let gpu_display = if !model.needs_gpu {
                    "cpu".to_string()
                } else if model.is_media {
                    "0 ⚡".to_string()
                } else {
                    app.models_manage_gpu_assignments
                        .get(&model.model_key)
                        .map(|a| a.display())
                        .unwrap_or_else(|| "auto".to_string())
                };

                let gpu_style = if is_selected && model.needs_gpu {
                    theme::selected()
                } else if !model.needs_gpu || model.is_media {
                    theme::dim()
                } else {
                    theme::highlight()
                };
                spans.push(Span::styled(format!("  {:>8}", gpu_display), gpu_style));
            }

            lines.push(Line::from(spans));

            if !model.model_name.is_empty() {
                unique_sizes
                    .entry(&model.model_name)
                    .or_insert(model.estimated_size_gb);
            }
        }

        lines.push(Line::from(Span::styled(
            if has_gpus {
                " ─".to_string() + &"─".repeat(69)
            } else {
                " ─".to_string() + &"─".repeat(58)
            },
            theme::dim(),
        )));

        let total: f64 = unique_sizes.values().sum();
        let total_str = if total < 1.0 {
            format!("{:.0} MB", total * 1024.0)
        } else {
            format!("{:.1} GB", total)
        };

        let unique_count = unique_sizes.len();
        let model_count = ts.models.len();
        let dedup_note = if unique_count < model_count {
            format!(" ({unique_count} unique)")
        } else {
            String::new()
        };

        let mut total_spans = vec![
            Span::styled(format!(" {:12}", "TOTAL"), theme::heading()),
            Span::styled(format!("{:38}", ""), theme::normal()),
            Span::styled(format!("{:>7}", total_str), theme::highlight()),
        ];
        if has_gpus {
            total_spans.push(Span::styled(format!("  {:>8}", ""), theme::normal()));
        }
        total_spans.push(Span::styled(dedup_note, theme::muted()));
        lines.push(Line::from(total_spans));
    } else {
        lines.push(Line::from(Span::styled(
            " No model configuration available",
            theme::muted(),
        )));
        lines.push(Line::from(""));
        lines.push(Line::from(Span::styled(
            " model_registry.yml not found",
            theme::error(),
        )));
    }

    lines.push(Line::from(""));
    if app.models_manage_config_dirty {
        lines.push(Line::from(vec![
            Span::styled(" Unsaved changes  ", theme::warning()),
            Span::styled("s Save  ", theme::highlight()),
            Span::styled("Enter Deploy", theme::success()),
        ]));
    } else if app.models_manage_config_undeployed {
        lines.push(Line::from(vec![
            Span::styled(" Saved (not deployed)  ", theme::info()),
            Span::styled("Enter Deploy to GPUs", theme::success()),
        ]));
    } else {
        lines.push(Line::from(Span::styled(
            " Configuration matches deployed",
            theme::success(),
        )));
    }

    let border_style = if is_focused {
        theme::highlight()
    } else {
        theme::dim()
    };

    let paragraph = Paragraph::new(lines)
        .block(
            Block::default()
                .borders(Borders::ALL)
                .border_style(border_style)
                .title(format!(
                    " {} Models ",
                    header_name,
                ))
                .title_style(theme::heading()),
        )
        .wrap(Wrap { trim: false });

    f.render_widget(paragraph, area);
}

fn render_log_viewer(f: &mut Frame, app: &App) {
    let chunks = Layout::default()
        .direction(Direction::Vertical)
        .constraints([
            Constraint::Length(3),
            Constraint::Min(8),
            Constraint::Length(3),
        ])
        .margin(2)
        .split(f.area());

    let spinner_char = if app.models_manage_action_running {
        SPINNER[app.models_manage_tick % SPINNER.len()]
    } else {
        ""
    };

    let title = if app.models_manage_action_running {
        Paragraph::new(Line::from(vec![
            Span::styled(format!("{spinner_char} "), theme::info()),
            Span::styled("Applying Model Tier...", theme::title()),
        ]))
    } else if app.models_manage_action_complete {
        Paragraph::new(Line::from(vec![
            Span::styled("✓ ", theme::success()),
            Span::styled("Model Tier Update Complete", theme::title()),
        ]))
    } else {
        Paragraph::new("Model Tier Update")
            .style(theme::title())
    }
    .alignment(Alignment::Center);
    f.render_widget(title, chunks[0]);

    let log_lines: Vec<Line> = app
        .models_manage_log
        .iter()
        .map(|line| {
            let style = if line.starts_with("ERROR") || line.contains("failed") {
                theme::error()
            } else if line.starts_with('✓') || line.contains("success") {
                theme::success()
            } else if line.starts_with(">>>") || line.starts_with("---") {
                theme::heading()
            } else {
                theme::normal()
            };
            Line::from(Span::styled(line.as_str(), style))
        })
        .collect();

    let log_area = chunks[1].inner(Margin::new(0, 0));
    let visible_height = log_area.height.saturating_sub(2) as usize;
    let total = log_lines.len();
    let max_scroll = total.saturating_sub(visible_height);
    let offset = if app.models_manage_log_autoscroll {
        max_scroll
    } else {
        app.models_manage_log_scroll.min(max_scroll)
    };

    let autoscroll_indicator = if app.models_manage_log_autoscroll { " [AUTO] " } else { "" };
    let scrollbar_info = if total > visible_height {
        format!(
            " Log ({}-{} of {}){} ",
            offset + 1,
            (offset + visible_height).min(total),
            total,
            autoscroll_indicator
        )
    } else {
        format!(" Log{} ", autoscroll_indicator)
    };

    let paragraph = Paragraph::new(log_lines)
        .block(
            Block::default()
                .borders(Borders::ALL)
                .border_style(theme::dim())
                .title(scrollbar_info)
                .title_style(theme::heading()),
        )
        .scroll((offset as u16, 0))
        .wrap(Wrap { trim: false });
    f.render_widget(paragraph, log_area);

    if total > visible_height {
        let mut scrollbar_state =
            ScrollbarState::new(total.saturating_sub(visible_height)).position(offset);
        let scrollbar = Scrollbar::new(ScrollbarOrientation::VerticalRight)
            .begin_symbol(Some("↑"))
            .end_symbol(Some("↓"));
        f.render_stateful_widget(
            scrollbar,
            log_area.inner(Margin::new(0, 1)),
            &mut scrollbar_state,
        );
    }

    let mut help_spans = vec![
        Span::styled(" s ", theme::highlight()),
        Span::styled("Start  ", theme::normal()),
        Span::styled("e ", theme::highlight()),
        Span::styled("End/Auto  ", theme::normal()),
        Span::styled("↑/↓ ", theme::highlight()),
        Span::styled("Scroll  ", theme::normal()),
        Span::styled("c ", theme::highlight()),
        Span::styled("Copy  ", theme::normal()),
    ];
    if app.models_manage_action_running {
        help_spans.push(Span::styled("(working...)", theme::muted()));
    } else {
        help_spans.push(Span::styled("Esc ", theme::muted()));
        help_spans.push(Span::styled("Back", theme::muted()));
    }
    let help = Paragraph::new(Line::from(help_spans));
    f.render_widget(help, chunks[2]);
}

pub fn handle_key(app: &mut App, key: KeyEvent) {
    if app.models_manage_log_visible {
        handle_log_key(app, key);
        return;
    }

    if app.models_manage_role_edit_mode {
        handle_role_edit_key(app, key);
        return;
    }

    if app.models_manage_add_mode {
        handle_add_picker_key(app, key);
        return;
    }

    match app.models_manage_focus {
        ModelsFocus::Tiers => handle_tier_key(app, key),
        ModelsFocus::Models => handle_model_key(app, key),
    }
}

fn handle_tier_key(app: &mut App, key: KeyEvent) {
    let tier_count = tier_entry_count(app);

    match key.code {
        KeyCode::Esc => {
            app.models_manage_loaded = false;
            app.screen = Screen::Welcome;
        }
        KeyCode::Up | KeyCode::Char('k') => {
            if app.models_manage_tier_selected > 0 {
                app.models_manage_tier_selected -= 1;
                rebuild_tier_cache(app);
                app.models_manage_config_dirty = true;
            }
        }
        KeyCode::Down | KeyCode::Char('j') => {
            if app.models_manage_tier_selected < tier_count.saturating_sub(1) {
                app.models_manage_tier_selected += 1;
                rebuild_tier_cache(app);
                app.models_manage_config_dirty = true;
            }
        }
        KeyCode::Tab => {
            let gpu_count = get_gpu_count(app);
            if gpu_count > 1 {
                app.models_manage_gpu_saved =
                    Some(app.models_manage_gpu_assignments.clone());
                app.models_manage_focus = ModelsFocus::Models;
                app.models_manage_model_selected = 0;
            }
        }
        KeyCode::Char('b') => {
            crate::screens::model_benchmark::init_screen(app, None);
            app.screen = Screen::ModelBenchmark;
        }
        KeyCode::Char('s') => {
            save_config(app);
        }
        KeyCode::Enter => {
            apply_tier(app);
        }
        _ => {}
    }
}

fn handle_model_key(app: &mut App, key: KeyEvent) {
    let model_count = app
        .models_manage_tier_models
        .as_ref()
        .map(|ts| ts.models.len())
        .unwrap_or(0);
    let gpu_count = get_gpu_count(app);

    match key.code {
        KeyCode::Esc => {
            if let Some(saved) = app.models_manage_gpu_saved.take() {
                app.models_manage_gpu_assignments = saved;
            }
            app.models_manage_focus = ModelsFocus::Tiers;
        }
        KeyCode::Tab => {
            app.models_manage_gpu_saved = None;
            app.models_manage_focus = ModelsFocus::Tiers;
        }
        KeyCode::Up | KeyCode::Char('k') => {
            if app.models_manage_model_selected > 0 {
                app.models_manage_model_selected -= 1;
            }
        }
        KeyCode::Down | KeyCode::Char('j') => {
            if app.models_manage_model_selected < model_count.saturating_sub(1) {
                app.models_manage_model_selected += 1;
            }
        }
        KeyCode::Char(c) if c.is_ascii_digit() => {
            let gpu_idx = c.to_digit(10).unwrap() as usize;
            if gpu_idx < gpu_count {
                toggle_gpu(app, gpu_idx);
            }
        }
        KeyCode::Char('t') => {
            toggle_tp(app);
        }
        KeyCode::Char('a') => {
            open_add_picker(app);
        }
        KeyCode::Char('c') => {
            change_model(app);
        }
        KeyCode::Char('b') => {
            let preselect_port = app
                .deployed_models
                .as_ref()
                .and_then(|ds| {
                    let selected_key = app
                        .models_manage_tier_models
                        .as_ref()
                        .and_then(|ts| ts.models.get(app.models_manage_model_selected))
                        .map(|m| m.model_key.clone());
                    selected_key.and_then(|key| {
                        ds.models
                            .iter()
                            .find(|m| m.model_key == key && m.provider == "vllm" && m.assigned)
                            .map(|m| m.port)
                    })
                });
            crate::screens::model_benchmark::init_screen(app, preselect_port);
            app.screen = Screen::ModelBenchmark;
        }
        KeyCode::Char('r') => {
            open_role_editor(app);
        }
        KeyCode::Char('d') | KeyCode::Delete => {
            remove_selected_model(app);
        }
        KeyCode::Char('s') => {
            save_config(app);
        }
        KeyCode::Enter => {
            apply_tier(app);
        }
        _ => {}
    }
}

fn change_model(app: &mut App) {
    let idx = app.models_manage_model_selected;
    let model = app
        .models_manage_tier_models
        .as_ref()
        .and_then(|ts| ts.models.get(idx));
    let model = match model {
        Some(m) => m,
        None => return,
    };
    if model.is_media {
        app.set_message("Cannot change media models", MessageKind::Warning);
        return;
    }
    if !matches!(model.provider.as_str(), "vllm" | "mlx") {
        app.set_message("Change only applies to LLM models", MessageKind::Warning);
        return;
    }
    app.models_manage_change_inherit_roles = Some(model.roles.clone());
    app.models_manage_change_inherit_gpu =
        app.models_manage_gpu_assignments.get(&model.model_key).cloned();
    app.models_manage_change_insert_index = Some(idx);
    remove_selected_model(app);
    open_add_picker(app);
}

/// Open the add-model picker popup, populating candidates from the registry.
fn open_add_picker(app: &mut App) {
    let hw = get_hardware(app);
    let backend = hw.map(|h| h.llm_backend.clone()).unwrap_or(LlmBackend::Mlx);
    let registry_path = app
        .repo_root
        .join("provision/ansible/group_vars/all/model_registry.yml");

    let all = TierModelSet::all_available_for_backend(&registry_path, &backend);

    let existing_keys: Vec<String> = app
        .models_manage_tier_models
        .as_ref()
        .map(|ts| ts.models.iter().map(|m| m.model_key.clone()).collect())
        .unwrap_or_default();

    let candidates: Vec<_> = all
        .into_iter()
        .filter(|m| !existing_keys.contains(&m.model_key))
        .collect();

    if candidates.is_empty() {
        app.set_message("No additional models available to add", MessageKind::Info);
        return;
    }

    app.models_manage_add_candidates = candidates;
    app.models_manage_add_selected = 0;
    app.models_manage_add_mode = true;
}

/// Remove the currently selected model (skip if media/read-only or no selection).
fn remove_selected_model(app: &mut App) {
    let idx = app.models_manage_model_selected;
    let is_media = app
        .models_manage_tier_models
        .as_ref()
        .and_then(|ts| ts.models.get(idx))
        .map(|m| m.is_media)
        .unwrap_or(true);

    if is_media {
        app.set_message("Cannot remove media models (pinned to GPU 0)", MessageKind::Warning);
        return;
    }

    let removed_key = if let Some(ref mut ts) = app.models_manage_tier_models {
        if idx < ts.models.len() {
            let model = ts.models.remove(idx);
            Some(model.model_key)
        } else {
            None
        }
    } else {
        None
    };

    if let Some(key) = removed_key {
        app.models_manage_gpu_assignments.remove(&key);
        let model_count = app
            .models_manage_tier_models
            .as_ref()
            .map(|ts| ts.models.len())
            .unwrap_or(0);
        if app.models_manage_model_selected >= model_count && model_count > 0 {
            app.models_manage_model_selected = model_count - 1;
        }
        app.models_manage_config_dirty = true;
    }
}

/// Handle keys in the add-model picker popup.
fn handle_add_picker_key(app: &mut App, key: KeyEvent) {
    let count = app.models_manage_add_candidates.len();

    match key.code {
        KeyCode::Esc => {
            app.models_manage_add_mode = false;
            app.models_manage_add_candidates.clear();
        }
        KeyCode::Up | KeyCode::Char('k') => {
            if app.models_manage_add_selected > 0 {
                app.models_manage_add_selected -= 1;
            }
        }
        KeyCode::Down | KeyCode::Char('j') => {
            if app.models_manage_add_selected < count.saturating_sub(1) {
                app.models_manage_add_selected += 1;
            }
        }
        KeyCode::Enter => {
            if app.models_manage_add_selected < count {
                let mut model = app.models_manage_add_candidates.remove(app.models_manage_add_selected);
                let model_key = model.model_key.clone();

                let inherit_roles = app.models_manage_change_inherit_roles.take();
                let inherit_gpu = app.models_manage_change_inherit_gpu.take();
                let insert_index = app.models_manage_change_insert_index.take();

                if let Some(roles) = inherit_roles {
                    model.roles = roles;
                }
                if model.needs_gpu {
                    if let Some(assign) = inherit_gpu {
                        app.models_manage_gpu_assignments.insert(model_key.clone(), assign);
                    } else if let Some(ref gpu_str) = model.gpu {
                        let gpus: Vec<usize> = gpu_str
                            .split(',')
                            .filter_map(|s| s.trim().parse().ok())
                            .collect();
                        if !gpus.is_empty() {
                            app.models_manage_gpu_assignments.insert(
                                model_key.clone(),
                                GpuAssignment {
                                    gpus,
                                    tensor_parallel: false,
                                },
                            );
                        }
                    }
                }

                if let Some(ref mut ts) = app.models_manage_tier_models {
                    if let Some(idx) = insert_index {
                        let idx = idx.min(ts.models.len());
                        ts.models.insert(idx, model);
                        app.models_manage_model_selected = idx;
                    } else {
                        ts.models.push(model);
                    }
                }
                app.models_manage_config_dirty = true;

                if app.models_manage_add_candidates.is_empty() {
                    app.models_manage_add_mode = false;
                } else if app.models_manage_add_selected >= app.models_manage_add_candidates.len() {
                    app.models_manage_add_selected = app.models_manage_add_candidates.len().saturating_sub(1);
                }
            }
        }
        _ => {}
    }
}

const LLM_ROLES: &[&str] = &[
    "fast", "test", "classify",
    "default", "agent", "chat",
    "research", "tool_calling", "vision",
    "parsing", "cleanup",
];

fn open_role_editor(app: &mut App) {
    let idx = app.models_manage_model_selected;
    let model = app
        .models_manage_tier_models
        .as_ref()
        .and_then(|ts| ts.models.get(idx));
    let model = match model {
        Some(m) => m,
        None => return,
    };
    if model.is_media {
        app.set_message("Cannot edit roles for media models", MessageKind::Warning);
        return;
    }
    if !matches!(model.provider.as_str(), "vllm" | "mlx") {
        app.set_message("Roles only apply to LLM models", MessageKind::Warning);
        return;
    }
    app.models_manage_available_roles = LLM_ROLES.iter().map(|s| s.to_string()).collect();
    app.models_manage_role_edit_selected = 0;
    app.models_manage_role_edit_mode = true;
}

fn handle_role_edit_key(app: &mut App, key: KeyEvent) {
    let count = app.models_manage_available_roles.len();

    match key.code {
        KeyCode::Esc => {
            app.models_manage_role_edit_mode = false;
        }
        KeyCode::Up | KeyCode::Char('k') => {
            if app.models_manage_role_edit_selected > 0 {
                app.models_manage_role_edit_selected -= 1;
            }
        }
        KeyCode::Down | KeyCode::Char('j') => {
            if app.models_manage_role_edit_selected < count.saturating_sub(1) {
                app.models_manage_role_edit_selected += 1;
            }
        }
        KeyCode::Char(' ') | KeyCode::Enter => {
            if app.models_manage_role_edit_selected < count {
                let role = app.models_manage_available_roles[app.models_manage_role_edit_selected].clone();
                let idx = app.models_manage_model_selected;
                let model_key = app
                    .models_manage_tier_models
                    .as_ref()
                    .and_then(|ts| ts.models.get(idx))
                    .map(|m| m.model_key.clone());

                if let Some(ref current_key) = model_key {
                    if let Some(ref mut ts) = app.models_manage_tier_models {
                        let has_role = ts.models.get(idx)
                            .map(|m| m.roles.contains(&role))
                            .unwrap_or(false);

                        if has_role {
                            if let Some(m) = ts.models.get_mut(idx) {
                                m.roles.retain(|r| r != &role);
                            }
                        } else {
                            for m in &mut ts.models {
                                if m.model_key != *current_key {
                                    m.roles.retain(|r| r != &role);
                                }
                            }
                            if let Some(m) = ts.models.get_mut(idx) {
                                m.roles.push(role);
                                m.roles.sort();
                                m.roles.dedup();
                            }
                        }
                        app.models_manage_config_dirty = true;
                    }
                }
            }
        }
        _ => {}
    }
}

fn selected_model_key(app: &App) -> Option<String> {
    app.models_manage_tier_models
        .as_ref()
        .and_then(|ts| ts.models.get(app.models_manage_model_selected))
        .map(|m| m.model_key.clone())
}

fn is_selected_model_gpu(app: &App) -> bool {
    app.models_manage_tier_models
        .as_ref()
        .and_then(|ts| ts.models.get(app.models_manage_model_selected))
        .map(|m| m.needs_gpu)
        .unwrap_or(false)
}

fn is_selected_model_media(app: &App) -> bool {
    app.models_manage_tier_models
        .as_ref()
        .and_then(|ts| ts.models.get(app.models_manage_model_selected))
        .map(|m| m.is_media)
        .unwrap_or(false)
}

/// Toggle a single GPU index on/off for the selected model.
fn toggle_gpu(app: &mut App, gpu_idx: usize) {
    let key = match selected_model_key(app) {
        Some(k) => k,
        None => return,
    };
    if !is_selected_model_gpu(app) || is_selected_model_media(app) {
        return;
    }

    let entry = app
        .models_manage_gpu_assignments
        .entry(key.clone())
        .or_insert_with(|| GpuAssignment {
            gpus: Vec::new(),
            tensor_parallel: false,
        });

    if let Some(pos) = entry.gpus.iter().position(|&g| g == gpu_idx) {
        entry.gpus.remove(pos);
    } else {
        entry.gpus.push(gpu_idx);
        entry.gpus.sort();
    }

    if entry.gpus.len() <= 1 {
        entry.tensor_parallel = false;
    }

    let should_remove = entry.gpus.is_empty();
    if should_remove {
        app.models_manage_gpu_assignments.remove(&key);
    }
    app.models_manage_config_dirty = true;
}

/// Toggle tensor parallelism for the selected model (only meaningful with >1 GPU).
fn toggle_tp(app: &mut App) {
    let key = match selected_model_key(app) {
        Some(k) => k,
        None => return,
    };
    if !is_selected_model_gpu(app) || is_selected_model_media(app) {
        return;
    }

    if let Some(entry) = app.models_manage_gpu_assignments.get_mut(&key) {
        if entry.gpus.len() > 1 {
            entry.tensor_parallel = !entry.tensor_parallel;
            app.models_manage_config_dirty = true;
        }
    }
}

fn handle_log_key(app: &mut App, key: KeyEvent) {
    match key.code {
        KeyCode::Esc => {
            if !app.models_manage_action_running {
                app.models_manage_log_visible = false;
            }
        }
        KeyCode::Up | KeyCode::Char('k') => {
            app.models_manage_log_autoscroll = false;
            if app.models_manage_log_scroll > 0 {
                app.models_manage_log_scroll -= 1;
            }
        }
        KeyCode::Down | KeyCode::Char('j') => {
            app.models_manage_log_autoscroll = false;
            app.models_manage_log_scroll += 1;
        }
        KeyCode::Home | KeyCode::Char('s') => {
            app.models_manage_log_autoscroll = false;
            app.models_manage_log_scroll = 0;
        }
        KeyCode::End | KeyCode::Char('e') => {
            app.models_manage_log_autoscroll = true;
            app.models_manage_log_scroll = app.models_manage_log.len().saturating_sub(1);
        }
        KeyCode::Char('c') => {
            let log_text = app.models_manage_log.join("\n");
            let _ = copy_to_clipboard(&log_text);
            app.set_message("Log copied to clipboard", MessageKind::Info);
        }
        _ => {}
    }
}

fn copy_to_clipboard(text: &str) -> std::io::Result<()> {
    use std::io::Write;
    use std::process::{Command, Stdio};

    #[cfg(target_os = "macos")]
    let mut child = Command::new("pbcopy")
        .stdin(Stdio::piped())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()?;

    #[cfg(target_os = "linux")]
    let mut child = Command::new("xclip")
        .args(["-selection", "clipboard"])
        .stdin(Stdio::piped())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()?;

    #[cfg(not(any(target_os = "macos", target_os = "linux")))]
    return Err(std::io::Error::new(
        std::io::ErrorKind::Unsupported,
        "clipboard not supported",
    ));

    if let Some(mut stdin) = child.stdin.take() {
        stdin.write_all(text.as_bytes())?;
    }
    child.wait()?;
    Ok(())
}

fn save_config(app: &mut App) {
    apply_tier_inner(app, false);
}

fn apply_tier(app: &mut App) {
    apply_tier_inner(app, true);
}

fn apply_tier_inner(app: &mut App, deploy: bool) {
    if !app.has_profiles() {
        app.set_message("No profile configured", MessageKind::Error);
        return;
    }

    if app.vault_password.is_none() {
        app.set_message(
            "Vault not unlocked — restart busibox CLI to unlock vault first",
            MessageKind::Error,
        );
        return;
    }

    let (tx, rx) = std::sync::mpsc::channel::<ModelsManageUpdate>();
    app.models_manage_rx = Some(rx);
    app.models_manage_log.clear();
    app.models_manage_log_visible = true;
    app.models_manage_log_scroll = 0;
    app.models_manage_log_autoscroll = true;
    app.models_manage_action_running = true;
    app.models_manage_action_complete = false;

    let is_remote = app
        .active_profile()
        .map(|(_, p)| p.remote)
        .unwrap_or(false);
    let repo_root = app.repo_root.clone();
    let tier_name = if app.models_manage_tier_selected == CUSTOM_TIER_INDEX {
        "custom".to_string()
    } else {
        let tiers = MemoryTier::all();
        tiers
            .get(app.models_manage_tier_selected)
            .map(|t| t.name())
            .unwrap_or("standard")
            .to_string()
    };
    let vault_password = app.vault_password.clone();

    let llm_backend: Option<String> = app.active_profile().and_then(|(_, p)| {
        p.hardware.as_ref().map(|h| match h.llm_backend {
            LlmBackend::Mlx => "mlx".to_string(),
            LlmBackend::Vllm => "vllm".to_string(),
            LlmBackend::Cloud => "cloud".to_string(),
        })
    });

    let ssh_details: Option<(String, String, String)> = if is_remote {
        app.active_profile().and_then(|(_, p)| {
            p.effective_host().map(|h| {
                (
                    h.to_string(),
                    p.effective_user().to_string(),
                    p.effective_ssh_key().to_string(),
                )
            })
        })
    } else {
        None
    };

    let remote_path: String = app
        .active_profile()
        .map(|(_, p)| p.effective_remote_path().to_string())
        .unwrap_or_else(|| "~/busibox".to_string());

    let network_base_octets: Option<String> = app
        .active_profile()
        .and_then(|(_, p)| p.network_base_octets.clone())
        .filter(|v| !v.trim().is_empty());

    let profile_id: Option<String> = app.active_profile().map(|(id, _)| id.to_string());

    let is_proxmox = app
        .active_profile()
        .map(|(_, p)| p.backend == "proxmox")
        .unwrap_or(false);

    let vllm_network_base: String = app
        .active_profile()
        .map(|(_, p)| p.vllm_network_base().to_string())
        .unwrap_or_else(|| "10.96.200".to_string());

    let gpu_assignments = app.models_manage_gpu_assignments.clone();

    // Build purpose overrides: role -> model_key for any roles assigned in the UI
    let purpose_overrides: std::collections::HashMap<String, String> = app
        .models_manage_tier_models
        .as_ref()
        .map(|ts| {
            let mut map = std::collections::HashMap::new();
            for model in &ts.models {
                for role in &model.roles {
                    map.insert(role.clone(), model.model_key.clone());
                }
            }
            map
        })
        .unwrap_or_default();

    let deploy = deploy;
    std::thread::spawn(move || {
        let header = if deploy {
            format!(">>> Applying tier: {tier_name}")
        } else {
            "--- Saving configuration (no deploy) ---".to_string()
        };
        let _ = tx.send(ModelsManageUpdate::Log(header));

        if !gpu_assignments.is_empty() {
            let _ = tx.send(ModelsManageUpdate::Log(
                "GPU assignments:".into(),
            ));
            for (model_key, assignment) in &gpu_assignments {
                let _ = tx.send(ModelsManageUpdate::Log(format!(
                    "  {model_key} → {}",
                    assignment.display()
                )));
            }
        }

        let backend_str = llm_backend.as_deref().unwrap_or("mlx");
        let llm_svc = match backend_str {
            "vllm" => "vllm",
            _ => "mlx",
        };

        let mut env_parts = vec![
            format!("MODEL_TIER={}", shell_escape(&tier_name)),
            format!("LLM_TIER={}", shell_escape(&tier_name)),
        ];
        if let Some(ref b) = llm_backend {
            env_parts.push(format!("LLM_BACKEND={}", shell_escape(b)));
        }
        if let Some(ref o) = network_base_octets {
            env_parts.push(format!("NETWORK_BASE_OCTETS={}", shell_escape(o)));
        }

        // Pass GPU assignments: GPU_ASSIGN_<KEY>=<gpus> and GPU_TP_<KEY>=<tp>
        for (model_key, assignment) in &gpu_assignments {
            if assignment.gpus.is_empty() {
                continue;
            }
            let env_suffix = model_key.replace('-', "_").replace('.', "_").to_uppercase();
            env_parts.push(format!(
                "GPU_ASSIGN_{}={}",
                env_suffix,
                shell_escape(&assignment.env_gpu_value())
            ));
            env_parts.push(format!(
                "GPU_TP_{}={}",
                env_suffix,
                assignment.env_tp_value()
            ));
        }

        // Pass purpose overrides: PURPOSE_<ROLE>=<model_key>
        if !purpose_overrides.is_empty() {
            for (role, model_key) in &purpose_overrides {
                let env_key = format!("PURPOSE_{}", role.replace('-', "_").to_uppercase());
                env_parts.push(format!("{}={}", env_key, shell_escape(model_key)));
            }
        }

        // Snapshot current vLLM config before regeneration for change detection
        let pre_snapshot = if backend_str == "vllm" {
            snapshot_vllm_config(&repo_root, is_remote, &ssh_details, &remote_path)
        } else {
            String::new()
        };

        // Step 1: Generate model_config.yml
        let _ = tx.send(ModelsManageUpdate::Log(
            "--- Step 1/5: Generating model_config.yml ---".into(),
        ));

        let gen_cmd = format!(
            "{} bash scripts/llm/generate-model-config.sh",
            env_parts.join(" ")
        );

        let gen_ok = if is_remote {
            if let Some((ref host, ref user, ref key)) = ssh_details {
                let ssh =
                    crate::modules::ssh::SshConnection::new(host, user, key);

                if let Err(e) = remote::sync(&repo_root, host, user, key, &remote_path) {
                    let _ = tx.send(ModelsManageUpdate::Log(format!(
                        "ERROR: rsync failed: {e}"
                    )));
                    let _ = tx.send(ModelsManageUpdate::Complete { success: false, deployed: false });
                    return;
                }
                let _ = tx.send(ModelsManageUpdate::Log("✓ Files synced".into()));

                let tx2 = tx.clone();
                let result = remote::exec_remote_streaming(
                    &ssh,
                    &remote_path,
                    &gen_cmd,
                    |line| {
                        let _ = tx2.send(ModelsManageUpdate::Log(format!("  {line}")));
                    },
                );
                match result {
                    Ok(0) => {
                        let _ = tx.send(ModelsManageUpdate::Log(
                            "✓ model_config.yml generated".into(),
                        ));

                        let remote_cfg = format!(
                            "{}/provision/ansible/group_vars/all/model_config.yml",
                            remote_path.trim_end_matches('/')
                        );
                        let local_cfg = repo_root
                            .join("provision/ansible/group_vars/all/model_config.yml");
                        if let Err(e) =
                            remote::pull_file(host, user, key, &remote_cfg, &local_cfg)
                        {
                            let _ = tx.send(ModelsManageUpdate::Log(format!(
                                "Warning: could not pull model_config.yml: {e}"
                            )));
                        }
                        true
                    }
                    Ok(code) => {
                        let _ = tx.send(ModelsManageUpdate::Log(format!(
                            "ERROR: generate-model-config.sh exited {code}"
                        )));
                        false
                    }
                    Err(e) => {
                        let _ = tx.send(ModelsManageUpdate::Log(format!(
                            "ERROR: {e}"
                        )));
                        false
                    }
                }
            } else {
                let _ = tx.send(ModelsManageUpdate::Log(
                    "ERROR: No SSH connection for remote profile".into(),
                ));
                let _ = tx.send(ModelsManageUpdate::Complete { success: false, deployed: false });
                return;
            }
        } else {
            let tx2 = tx.clone();
            let result = run_local_command_streaming(
                &repo_root,
                "bash",
                &["scripts/llm/generate-model-config.sh"],
                &env_parts,
                |line| {
                    let _ = tx2.send(ModelsManageUpdate::Log(format!("  {line}")));
                },
            );
            match result {
                Ok(0) => {
                    let _ = tx.send(ModelsManageUpdate::Log(
                        "✓ model_config.yml generated".into(),
                    ));
                    true
                }
                Ok(code) => {
                    let _ = tx.send(ModelsManageUpdate::Log(format!(
                        "ERROR: generate-model-config.sh exited {code}"
                    )));
                    false
                }
                Err(e) => {
                    let _ = tx.send(ModelsManageUpdate::Log(format!("ERROR: {e}")));
                    false
                }
            }
        };

        if !gen_ok {
            let _ = tx.send(ModelsManageUpdate::Log(
                "Continuing despite config generation failure...".into(),
            ));
        }

        let vllm_changed = if deploy && backend_str == "vllm" && gen_ok {
            let post_snapshot = snapshot_vllm_config(&repo_root, is_remote, &ssh_details, &remote_path);
            if !pre_snapshot.is_empty() && pre_snapshot == post_snapshot {
                let vllm_ports = collect_assigned_vllm_ports(&repo_root, is_remote, &ssh_details, &remote_path);
                let all_running = if vllm_ports.is_empty() {
                    true
                } else {
                    let _ = tx.send(ModelsManageUpdate::Log(
                        "Config unchanged — checking if models are running...".into(),
                    ));
                    check_vllm_ports_healthy(
                        &vllm_ports,
                        is_remote,
                        is_proxmox,
                        &ssh_details,
                        &vllm_network_base,
                    )
                };
                if all_running {
                    let _ = tx.send(ModelsManageUpdate::Log(
                        "✓ vLLM model config unchanged and all models running — skipping deploy, wait, and litellm redeploy".into(),
                    ));
                    false
                } else {
                    let _ = tx.send(ModelsManageUpdate::Log(
                        "vLLM config unchanged but some models are not running — redeploying".into(),
                    ));
                    true
                }
            } else {
                if pre_snapshot.is_empty() {
                    let _ = tx.send(ModelsManageUpdate::Log(
                        "No previous vLLM config found — full deploy required".into(),
                    ));
                } else {
                    let _ = tx.send(ModelsManageUpdate::Log(
                        "vLLM config changed — deploying updated models".into(),
                    ));
                }
                true
            }
        } else {
            deploy
        };

        let (mut step2_ok, mut step3_ok) = (true, true);

        if vllm_changed && deploy {
            // Step 2: Deploy LLM services
            if backend_str == "mlx" {
                let _ = tx.send(ModelsManageUpdate::Log(
                    "--- Step 2/5: Redeploying MLX models ---".into(),
                ));

                let mlx_args = format!(
                    "manage SERVICE=mlx ACTION=redeploy LLM_BACKEND=mlx LLM_TIER={}",
                    shell_escape(&tier_name)
                );
                step2_ok = run_make_step(
                    &tx,
                    is_remote,
                    &repo_root,
                    &ssh_details,
                    &remote_path,
                    &mlx_args,
                    vault_password.as_deref(),
                );
            } else {
                let _ = tx.send(ModelsManageUpdate::Log(
                    "--- Step 2/5: Deploying vLLM services ---".into(),
                ));

                let download_args = format!("install SERVICE={llm_svc}");
                step2_ok = run_make_step(
                    &tx,
                    is_remote,
                    &repo_root,
                    &ssh_details,
                    &remote_path,
                    &download_args,
                    vault_password.as_deref(),
                );
            }

            if !step2_ok {
                let _ = tx.send(ModelsManageUpdate::Log(
                    "WARNING: Model download/install may have failed".into(),
                ));
            }

            // Step 3: Wait for models to be available
            if backend_str == "mlx" {
                let _ = tx.send(ModelsManageUpdate::Log(
                    "--- Step 3/5: Waiting for MLX models to become available ---".into(),
                ));

                let mlx_ports: Vec<u16> = vec![8080, 18081];
                let all_ready = wait_for_mlx_models(
                    &tx,
                    &mlx_ports,
                    is_remote,
                    &ssh_details,
                );

                if all_ready {
                    let _ = tx.send(ModelsManageUpdate::Log(
                        "✓ All MLX models are responding".into(),
                    ));
                } else {
                    let _ = tx.send(ModelsManageUpdate::Log(
                        "WARNING: Some MLX models did not become ready within timeout — proceeding anyway".into(),
                    ));
                }
            } else if backend_str == "vllm" {
                let _ = tx.send(ModelsManageUpdate::Log(
                    "--- Step 3/5: Waiting for vLLM models to become available ---".into(),
                ));

                let vllm_ports = collect_assigned_vllm_ports(&repo_root, is_remote, &ssh_details, &remote_path);

                if vllm_ports.is_empty() {
                    let _ = tx.send(ModelsManageUpdate::Log(
                        "No assigned vLLM ports found — skipping wait".into(),
                    ));
                } else {
                    let _ = tx.send(ModelsManageUpdate::Log(format!(
                        "Waiting for {} vLLM model(s) on ports: {:?}",
                        vllm_ports.len(),
                        vllm_ports,
                    )));

                    let all_ready = wait_for_vllm_models(
                        &tx,
                        &vllm_ports,
                        is_remote,
                        is_proxmox,
                        &ssh_details,
                        &vllm_network_base,
                    );

                    if all_ready {
                        let _ = tx.send(ModelsManageUpdate::Log(
                            "✓ All vLLM models are responding".into(),
                        ));
                    } else {
                        let _ = tx.send(ModelsManageUpdate::Log(
                            "WARNING: Some vLLM models did not become ready within timeout — proceeding anyway".into(),
                        ));
                    }
                }
            }

            // Step 4: Redeploy litellm
            let _ = tx.send(ModelsManageUpdate::Log(
                "--- Step 4/5: Deploying saved config to LiteLLM ---".into(),
            ));

            let litellm_args = if let Some(ref b) = llm_backend {
                format!("install SERVICE=litellm LLM_BACKEND={} LLM_TIER={}", shell_escape(b), shell_escape(&tier_name))
            } else {
                "install SERVICE=litellm".to_string()
            };
            step3_ok = run_make_step(
                &tx,
                is_remote,
                &repo_root,
                &ssh_details,
                &remote_path,
                &litellm_args,
                vault_password.as_deref(),
            );

            if !step3_ok {
                let _ = tx.send(ModelsManageUpdate::Log(
                    "WARNING: litellm redeploy may have failed".into(),
                ));
            }
        } else {
            let _ = tx.send(ModelsManageUpdate::Log(
                "--- Steps 2-4 skipped (no changes) ---".into(),
            ));
        }

        // Step 5: Update profile
        let _ = tx.send(ModelsManageUpdate::Log(
            "--- Step 5/5: Updating profile ---".into(),
        ));

        if let Some(ref pid) = profile_id {
            match update_profile_tier(&repo_root, pid, &tier_name) {
                Ok(()) => {
                    let _ = tx.send(ModelsManageUpdate::Log(format!(
                        "✓ Profile updated to tier '{tier_name}'"
                    )));
                }
                Err(e) => {
                    let _ = tx.send(ModelsManageUpdate::Log(format!(
                        "WARNING: Could not update profile: {e}"
                    )));
                }
            }
        }

        if deploy {
            let _ = tx.send(ModelsManageUpdate::Log(format!(
                "✓ Tier '{tier_name}' applied successfully"
            )));
            let _ = tx.send(ModelsManageUpdate::Complete {
                success: step2_ok && step3_ok,
                deployed: true,
            });
        } else {
            let _ = tx.send(ModelsManageUpdate::Log(
                "✓ Configuration saved. Use Enter to deploy to GPUs.".into(),
            ));
            let _ = tx.send(ModelsManageUpdate::Complete {
                success: gen_ok,
                deployed: false,
            });
        }
    });
}

fn run_make_step(
    tx: &std::sync::mpsc::Sender<ModelsManageUpdate>,
    is_remote: bool,
    repo_root: &std::path::Path,
    ssh_details: &Option<(String, String, String)>,
    remote_path: &str,
    make_args: &str,
    vault_password: Option<&str>,
) -> bool {
    let tx2 = tx.clone();
    let on_line = move |line: &str| {
        let _ = tx2.send(ModelsManageUpdate::Log(format!("  {line}")));
    };

    if is_remote {
        if let Some((ref host, ref user, ref key)) = ssh_details {
            let ssh = crate::modules::ssh::SshConnection::new(host, user, key);
            let result = if let Some(pw) = vault_password {
                remote::exec_make_quiet_with_vault_streaming(&ssh, remote_path, make_args, pw, on_line)
            } else {
                remote::exec_make_quiet_streaming(&ssh, remote_path, make_args, on_line)
            };
            match result {
                Ok(0) => {
                    let _ = tx.send(ModelsManageUpdate::Log("✓ Done".into()));
                    true
                }
                Ok(code) => {
                    let _ = tx.send(ModelsManageUpdate::Log(format!(
                        "ERROR: exited with code {code}"
                    )));
                    false
                }
                Err(e) => {
                    let _ = tx.send(ModelsManageUpdate::Log(format!("ERROR: {e}")));
                    false
                }
            }
        } else {
            let _ = tx.send(ModelsManageUpdate::Log(
                "ERROR: No SSH credentials".into(),
            ));
            false
        }
    } else {
        let result = if let Some(pw) = vault_password {
            remote::run_local_make_quiet_with_vault_streaming(repo_root, make_args, pw, on_line)
        } else {
            remote::run_local_make_quiet_streaming(repo_root, make_args, on_line)
        };
        match result {
            Ok(0) => {
                let _ = tx.send(ModelsManageUpdate::Log("✓ Done".into()));
                true
            }
            Ok(code) => {
                let _ = tx.send(ModelsManageUpdate::Log(format!(
                    "ERROR: exited with code {code}"
                )));
                false
            }
            Err(e) => {
                let _ = tx.send(ModelsManageUpdate::Log(format!("ERROR: {e}")));
                false
            }
        }
    }
}

fn run_local_command_streaming<F>(
    cwd: &std::path::Path,
    program: &str,
    args: &[&str],
    env_vars: &[String],
    mut on_line: F,
) -> Result<i32, String>
where
    F: FnMut(&str),
{
    use std::io::BufRead;
    use std::process::{Command, Stdio};

    let mut cmd = Command::new(program);
    cmd.args(args).current_dir(cwd);
    for env_pair in env_vars {
        if let Some((k, v)) = env_pair.split_once('=') {
            let v = v.trim_matches('\'');
            cmd.env(k, v);
        }
    }
    cmd.stdout(Stdio::piped()).stderr(Stdio::piped());

    let mut child = cmd.spawn().map_err(|e| format!("spawn failed: {e}"))?;
    let stdout = child
        .stdout
        .take()
        .ok_or_else(|| "no stdout".to_string())?;
    let reader = std::io::BufReader::new(stdout);
    for line in reader.lines() {
        if let Ok(l) = line {
            let cleaned = remote::strip_ansi(&l);
            let trimmed = cleaned.trim();
            if !trimmed.is_empty() {
                on_line(trimmed);
            }
        }
    }
    let status = child.wait().map_err(|e| format!("wait failed: {e}"))?;
    Ok(status.code().unwrap_or(1))
}

/// Read model_config.yml and return a normalized snapshot of all vLLM-assigned entries.
/// The snapshot is a sorted, serialized representation suitable for equality comparison.
/// Returns an empty string if the file doesn't exist or has no vLLM entries.
fn snapshot_vllm_config(
    repo_root: &std::path::Path,
    is_remote: bool,
    ssh_details: &Option<(String, String, String)>,
    remote_path: &str,
) -> String {
    let yaml_content = read_model_config_yaml(repo_root, is_remote, ssh_details, remote_path);
    if yaml_content.trim().is_empty() {
        return String::new();
    }
    normalize_vllm_entries(&yaml_content)
}

fn read_model_config_yaml(
    repo_root: &std::path::Path,
    is_remote: bool,
    ssh_details: &Option<(String, String, String)>,
    remote_path: &str,
) -> String {
    if is_remote {
        if let Some((ref host, ref user, ref key)) = ssh_details {
            let ssh = crate::modules::ssh::SshConnection::new(host, user, key);
            let remote_file = format!(
                "{}/provision/ansible/group_vars/all/model_config.yml",
                remote_path.trim_end_matches('/')
            );
            let cmd = format!("cat {} 2>/dev/null", remote_file);
            ssh.run(&cmd).unwrap_or_default()
        } else {
            String::new()
        }
    } else {
        let path = repo_root.join("provision/ansible/group_vars/all/model_config.yml");
        std::fs::read_to_string(path).unwrap_or_default()
    }
}

/// Extract and normalize vLLM-assigned entries into a deterministic string for comparison.
fn normalize_vllm_entries(yaml_content: &str) -> String {
    let parsed: serde_yaml::Value = match serde_yaml::from_str(yaml_content) {
        Ok(v) => v,
        Err(_) => return String::new(),
    };

    let models = match parsed.get("models").and_then(|m| m.as_mapping()) {
        Some(m) => m,
        None => return String::new(),
    };

    let mut entries: Vec<(String, serde_yaml::Value)> = Vec::new();
    for (name, entry) in models {
        let provider = entry.get("provider").and_then(|v| v.as_str()).unwrap_or("");
        let assigned = entry.get("assigned").and_then(|v| v.as_bool()).unwrap_or(false);
        if provider == "vllm" && assigned {
            let name_str = name.as_str().unwrap_or("").to_string();
            entries.push((name_str, entry.clone()));
        }
    }

    if entries.is_empty() {
        return String::new();
    }

    entries.sort_by(|a, b| a.0.cmp(&b.0));

    let mut parts = Vec::new();
    for (name, entry) in &entries {
        parts.push(format!("{}: {}", name, serde_yaml::to_string(entry).unwrap_or_default()));
    }
    parts.join("\n")
}

/// Parse model_config.yml (local or remote) and return the list of assigned vLLM ports.
fn collect_assigned_vllm_ports(
    repo_root: &std::path::Path,
    is_remote: bool,
    ssh_details: &Option<(String, String, String)>,
    remote_path: &str,
) -> Vec<u16> {
    let yaml_content = read_model_config_yaml(repo_root, is_remote, ssh_details, remote_path);

    if yaml_content.trim().is_empty() {
        return vec![];
    }

    let parsed: serde_yaml::Value = match serde_yaml::from_str(&yaml_content) {
        Ok(v) => v,
        Err(_) => return vec![],
    };

    let mut ports = vec![];
    if let Some(models) = parsed.get("models").and_then(|m| m.as_mapping()) {
        for (_name, entry) in models {
            let provider = entry.get("provider").and_then(|v| v.as_str()).unwrap_or("");
            let assigned = entry.get("assigned").and_then(|v| v.as_bool()).unwrap_or(false);
            let port = entry.get("port").and_then(|v| v.as_u64()).unwrap_or(0) as u16;
            if provider == "vllm" && assigned && port > 0 {
                if !ports.contains(&port) {
                    ports.push(port);
                }
            }
        }
    }
    ports.sort();
    ports
}

/// Quick one-shot health check: returns true only if ALL ports return HTTP 200.
fn check_vllm_ports_healthy(
    ports: &[u16],
    is_remote: bool,
    is_proxmox: bool,
    ssh_details: &Option<(String, String, String)>,
    vllm_network_base: &str,
) -> bool {
    let vllm_ip = if is_proxmox {
        format!("{vllm_network_base}.208")
    } else {
        "localhost".to_string()
    };

    for &port in ports {
        let curl_cmd = format!(
            "curl -s -o /dev/null -w '%{{http_code}}' --max-time 5 'http://{}:{}/v1/models'",
            vllm_ip, port
        );

        let http_code: u16 = if is_remote {
            if let Some((ref host, ref user, ref key)) = ssh_details {
                let ssh = crate::modules::ssh::SshConnection::new(host, user, key);
                let full_cmd = format!(
                    "{}{}",
                    crate::modules::remote::SHELL_PATH_PREAMBLE,
                    curl_cmd
                );
                ssh.run(&full_cmd)
                    .unwrap_or_default()
                    .trim()
                    .parse()
                    .unwrap_or(0)
            } else {
                0
            }
        } else {
            std::process::Command::new("sh")
                .arg("-c")
                .arg(&curl_cmd)
                .output()
                .ok()
                .and_then(|o| String::from_utf8(o.stdout).ok())
                .unwrap_or_default()
                .trim()
                .parse()
                .unwrap_or(0)
        };

        if http_code != 200 {
            return false;
        }
    }
    true
}

/// Poll vLLM ports until all respond with HTTP 200 on /v1/models.
/// Returns true if all became ready within the timeout (10 minutes).
fn wait_for_mlx_models(
    tx: &std::sync::mpsc::Sender<ModelsManageUpdate>,
    ports: &[u16],
    is_remote: bool,
    ssh_details: &Option<(String, String, String)>,
) -> bool {
    use crate::modules::models::query_mlx_model;
    use std::time::{Duration, Instant};

    let timeout = Duration::from_secs(300);
    let poll_interval = Duration::from_secs(10);
    let start = Instant::now();

    let mut remaining: Vec<u16> = ports.to_vec();

    while !remaining.is_empty() && start.elapsed() < timeout {
        std::thread::sleep(poll_interval);

        let mut still_waiting = vec![];
        for &port in &remaining {
            if query_mlx_model(port, is_remote, ssh_details).is_some() {
                let _ = tx.send(ModelsManageUpdate::Log(format!(
                    "  ✓ MLX port {port} ready"
                )));
            } else {
                still_waiting.push(port);
            }
        }

        if !still_waiting.is_empty() {
            let elapsed = start.elapsed().as_secs();
            let _ = tx.send(ModelsManageUpdate::Log(format!(
                "  Waiting for MLX ports {:?} ({elapsed}s elapsed)...",
                still_waiting
            )));
        }

        remaining = still_waiting;
    }

    remaining.is_empty()
}

fn wait_for_vllm_models(
    tx: &std::sync::mpsc::Sender<ModelsManageUpdate>,
    ports: &[u16],
    is_remote: bool,
    is_proxmox: bool,
    ssh_details: &Option<(String, String, String)>,
    vllm_network_base: &str,
) -> bool {
    use std::time::{Duration, Instant};

    let timeout = Duration::from_secs(600);
    let poll_interval = Duration::from_secs(10);
    let start = Instant::now();

    let vllm_ip = if is_proxmox {
        format!("{vllm_network_base}.208")
    } else {
        "localhost".to_string()
    };

    let mut remaining: Vec<u16> = ports.to_vec();

    while !remaining.is_empty() && start.elapsed() < timeout {
        std::thread::sleep(poll_interval);

        let mut still_waiting = vec![];
        for &port in &remaining {
            let curl_cmd = format!(
                "curl -s -o /dev/null -w '%{{http_code}}' --max-time 5 'http://{}:{}/v1/models'",
                vllm_ip, port
            );

            let http_code: u16 = if is_remote {
                if let Some((ref host, ref user, ref key)) = ssh_details {
                    let ssh = crate::modules::ssh::SshConnection::new(host, user, key);
                    let full_cmd = format!(
                        "{}{}",
                        crate::modules::remote::SHELL_PATH_PREAMBLE,
                        curl_cmd
                    );
                    ssh.run(&full_cmd)
                        .unwrap_or_default()
                        .trim()
                        .parse()
                        .unwrap_or(0)
                } else {
                    0
                }
            } else {
                std::process::Command::new("sh")
                    .arg("-c")
                    .arg(&curl_cmd)
                    .output()
                    .ok()
                    .and_then(|o| String::from_utf8(o.stdout).ok())
                    .unwrap_or_default()
                    .trim()
                    .parse()
                    .unwrap_or(0)
            };

            if http_code == 200 {
                let _ = tx.send(ModelsManageUpdate::Log(format!(
                    "  ✓ Port {port} ready"
                )));
            } else {
                still_waiting.push(port);
            }
        }

        if !still_waiting.is_empty() {
            let elapsed = start.elapsed().as_secs();
            let _ = tx.send(ModelsManageUpdate::Log(format!(
                "  Waiting for ports {:?} ({elapsed}s elapsed)...",
                still_waiting
            )));
        }

        remaining = still_waiting;
    }

    remaining.is_empty()
}

fn update_profile_tier(
    repo_root: &std::path::Path,
    profile_id: &str,
    tier_name: &str,
) -> Result<(), String> {
    use crate::modules::profile;

    let profiles =
        profile::load_profiles(repo_root).map_err(|e| format!("load profiles: {e}"))?;
    let profile = profiles
        .profiles
        .get(profile_id)
        .ok_or_else(|| format!("profile '{profile_id}' not found"))?;

    let mut updated = profile.clone();
    updated.model_tier = Some(tier_name.to_string());

    profile::upsert_profile(repo_root, profile_id, updated, false)
        .map_err(|e| format!("save profile: {e}"))?;

    Ok(())
}
