use crate::app::{App, MessageKind, ModelsFocus, ModelsManageUpdate, Screen};
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

/// Rebuild the cached TierModelSet and reset GPU assignments.
/// Called on init and whenever the selected tier changes.
fn rebuild_tier_cache(app: &mut App) {
    let hw = get_hardware(app);
    let backend = hw.map(|h| h.llm_backend.clone()).unwrap_or(LlmBackend::Mlx);
    let tiers = MemoryTier::all();
    let selected_tier = tiers
        .get(app.models_manage_tier_selected)
        .copied()
        .unwrap_or(MemoryTier::Standard);
    let config_path = app
        .repo_root
        .join("provision/ansible/group_vars/all/model_registry.yml");

    let tier_set = if config_path.exists() {
        TierModelSet::from_config(&config_path, selected_tier, &backend).ok()
    } else {
        None
    };

    app.models_manage_gpu_assignments.clear();
    if let Some(ref ts) = tier_set {
        for model in &ts.models {
            if !model.needs_gpu {
                continue;
            }
            if let Some(ref gpu_str) = model.gpu {
                let gpus: Vec<usize> = gpu_str
                    .split(',')
                    .filter_map(|s| s.trim().parse().ok())
                    .collect();
                let tp = gpus.len() > 1;
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

    if let Some((_, profile)) = app.active_profile() {
        if let Some(tier) = profile.effective_model_tier() {
            app.models_manage_tier_selected = tier.index();
            app.models_manage_current_tier = Some(tier.name().to_string());
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

    let selected_tier = MemoryTier::all()
        .get(app.models_manage_tier_selected)
        .copied()
        .unwrap_or(MemoryTier::Standard);
    let selected_name = capitalize(selected_tier.name());
    let is_changed = app
        .models_manage_current_tier
        .as_deref()
        .map(|c| c != selected_tier.name())
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
        }
        ModelsFocus::Models => {
            help_spans.push(Span::styled("↑/↓ ", theme::highlight()));
            help_spans.push(Span::styled("Model  ", theme::normal()));
            help_spans.push(Span::styled("0-9 ", theme::highlight()));
            help_spans.push(Span::styled("GPU  ", theme::normal()));
            help_spans.push(Span::styled("t ", theme::highlight()));
            help_spans.push(Span::styled("TP  ", theme::normal()));
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
        if is_changed {
            help_spans.push(Span::styled("Enter ", theme::highlight()));
            help_spans.push(Span::styled(
                format!("Apply {selected_name}  "),
                theme::success(),
            ));
        }
        help_spans.push(Span::styled("Esc ", theme::muted()));
        help_spans.push(Span::styled("Back", theme::muted()));
    }

    let help = Paragraph::new(Line::from(help_spans));
    f.render_widget(help, chunks[2]);

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

fn render_tier_selector(f: &mut Frame, app: &App, area: Rect) {
    let tiers = MemoryTier::all();

    let hw = get_hardware(app);
    let recommended_tier = hw.map(|h| h.memory_tier);
    let is_focused = app.models_manage_focus == ModelsFocus::Tiers;

    let items: Vec<ListItem> = tiers
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
    let tiers = MemoryTier::all();
    let selected_tier = tiers
        .get(app.models_manage_tier_selected)
        .copied()
        .unwrap_or(MemoryTier::Standard);

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

    let mut lines: Vec<Line> = Vec::new();

    lines.push(Line::from(vec![
        Span::styled(
            format!(" {} ", capitalize(selected_tier.name())),
            theme::heading(),
        ),
        Span::styled(
            format!("— {} ({})", selected_tier.description(), backend_label),
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

            let name_display = if model.model_name.len() > 36 {
                format!("{}…", &model.model_name[..35])
            } else {
                model.model_name.clone()
            };

            let size_str = if model.estimated_size_gb < 1.0 {
                format!("{:.0} MB", model.estimated_size_gb * 1024.0)
            } else {
                format!("{:.1} GB", model.estimated_size_gb)
            };

            let row_style = if is_selected {
                theme::selected()
            } else {
                theme::normal()
            };

            let mut spans = vec![
                Span::styled(format!(" {:12}", roles_display), if is_selected { row_style } else { theme::info() }),
                Span::styled(format!("{:38}", name_display), row_style),
                Span::styled(format!("{:>7}", size_str), if is_selected { row_style } else { theme::muted() }),
            ];

            if has_gpus {
                let gpu_display = if !model.needs_gpu {
                    "cpu".to_string()
                } else {
                    app.models_manage_gpu_assignments
                        .get(&model.model_key)
                        .map(|a| a.display())
                        .unwrap_or_else(|| "auto".to_string())
                };

                let gpu_style = if is_selected && model.needs_gpu {
                    theme::selected()
                } else if !model.needs_gpu {
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

    let is_changed = app
        .models_manage_current_tier
        .as_deref()
        .map(|c| c != selected_tier.name())
        .unwrap_or(true);

    if is_changed {
        lines.push(Line::from(""));
        lines.push(Line::from(Span::styled(
            " Press Enter to apply this tier",
            theme::warning(),
        )));
        lines.push(Line::from(Span::styled(
            " (downloads models → updates mlx/vllm → updates litellm)",
            theme::muted(),
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
                    capitalize(selected_tier.name())
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
    let visible_height = log_area.height as usize;
    let total = log_lines.len();
    let offset = if total > visible_height {
        app.models_manage_log_scroll
            .min(total.saturating_sub(visible_height))
    } else {
        0
    };

    let paragraph = Paragraph::new(log_lines)
        .block(
            Block::default()
                .borders(Borders::ALL)
                .border_style(theme::dim())
                .title(" Log ")
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

    let help_text = if app.models_manage_action_running {
        " ↑/↓ Scroll  (working...)"
    } else {
        " ↑/↓ Scroll  Esc Back"
    };
    let help = Paragraph::new(Line::from(Span::styled(help_text, theme::muted())));
    f.render_widget(help, chunks[2]);
}

pub fn handle_key(app: &mut App, key: KeyEvent) {
    if app.models_manage_log_visible {
        handle_log_key(app, key);
        return;
    }

    match app.models_manage_focus {
        ModelsFocus::Tiers => handle_tier_key(app, key),
        ModelsFocus::Models => handle_model_key(app, key),
    }
}

fn handle_tier_key(app: &mut App, key: KeyEvent) {
    let tier_count = MemoryTier::all().len();

    match key.code {
        KeyCode::Esc => {
            app.models_manage_loaded = false;
            app.screen = Screen::Welcome;
        }
        KeyCode::Up | KeyCode::Char('k') => {
            if app.models_manage_tier_selected > 0 {
                app.models_manage_tier_selected -= 1;
                rebuild_tier_cache(app);
            }
        }
        KeyCode::Down | KeyCode::Char('j') => {
            if app.models_manage_tier_selected < tier_count.saturating_sub(1) {
                app.models_manage_tier_selected += 1;
                rebuild_tier_cache(app);
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
        KeyCode::Enter => {
            apply_tier(app);
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

/// Toggle a single GPU index on/off for the selected model.
fn toggle_gpu(app: &mut App, gpu_idx: usize) {
    let key = match selected_model_key(app) {
        Some(k) => k,
        None => return,
    };
    if !is_selected_model_gpu(app) {
        return;
    }

    use crate::app::GpuAssignment;
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
}

/// Toggle tensor parallelism for the selected model (only meaningful with >1 GPU).
fn toggle_tp(app: &mut App) {
    let key = match selected_model_key(app) {
        Some(k) => k,
        None => return,
    };
    if !is_selected_model_gpu(app) {
        return;
    }

    if let Some(entry) = app.models_manage_gpu_assignments.get_mut(&key) {
        if entry.gpus.len() > 1 {
            entry.tensor_parallel = !entry.tensor_parallel;
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
            if app.models_manage_log_scroll > 0 {
                app.models_manage_log_scroll -= 1;
            }
        }
        KeyCode::Down | KeyCode::Char('j') => {
            if app.models_manage_log_scroll < app.models_manage_log.len().saturating_sub(1) {
                app.models_manage_log_scroll += 1;
            }
        }
        _ => {}
    }
}

fn apply_tier(app: &mut App) {
    let tiers = MemoryTier::all();
    let selected = tiers
        .get(app.models_manage_tier_selected)
        .copied()
        .unwrap_or(MemoryTier::Standard);

    let is_same = app
        .models_manage_current_tier
        .as_deref()
        .map(|c| c == selected.name())
        .unwrap_or(false);

    if is_same {
        app.set_message("Already on this tier", MessageKind::Info);
        return;
    }

    if !app.has_profiles() {
        app.set_message("No profile configured", MessageKind::Error);
        return;
    }

    let (tx, rx) = std::sync::mpsc::channel::<ModelsManageUpdate>();
    app.models_manage_rx = Some(rx);
    app.models_manage_log.clear();
    app.models_manage_log_visible = true;
    app.models_manage_log_scroll = 0;
    app.models_manage_action_running = true;
    app.models_manage_action_complete = false;

    let is_remote = app
        .active_profile()
        .map(|(_, p)| p.remote)
        .unwrap_or(false);
    let repo_root = app.repo_root.clone();
    let tier_name = selected.name().to_string();
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

    let gpu_assignments = app.models_manage_gpu_assignments.clone();

    std::thread::spawn(move || {
        let _ = tx.send(ModelsManageUpdate::Log(format!(
            ">>> Applying tier: {tier_name}"
        )));

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

        // Step 1: Generate model_config.yml
        let _ = tx.send(ModelsManageUpdate::Log(
            "--- Step 1/4: Generating model_config.yml ---".into(),
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
                    let _ = tx.send(ModelsManageUpdate::Complete { success: false });
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
                let _ = tx.send(ModelsManageUpdate::Complete { success: false });
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

        // Step 2: Download uncached models
        let _ = tx.send(ModelsManageUpdate::Log(
            "--- Step 2/4: Downloading uncached models ---".into(),
        ));

        let download_args = format!("install SERVICE={llm_svc}");
        let step2_ok = run_make_step(
            &tx,
            is_remote,
            &repo_root,
            &ssh_details,
            &remote_path,
            &download_args,
            vault_password.as_deref(),
        );

        if !step2_ok {
            let _ = tx.send(ModelsManageUpdate::Log(
                "WARNING: Model download/install may have failed".into(),
            ));
        }

        // Step 3: Redeploy litellm
        let _ = tx.send(ModelsManageUpdate::Log(
            "--- Step 3/4: Redeploying litellm ---".into(),
        ));

        let litellm_args = "install SERVICE=litellm".to_string();
        let step3_ok = run_make_step(
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

        // Step 4: Update profile
        let _ = tx.send(ModelsManageUpdate::Log(
            "--- Step 4/4: Updating profile ---".into(),
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

        let _ = tx.send(ModelsManageUpdate::Log(format!(
            "✓ Tier '{tier_name}' applied successfully"
        )));
        let _ = tx.send(ModelsManageUpdate::Complete {
            success: step2_ok && step3_ok,
        });
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
