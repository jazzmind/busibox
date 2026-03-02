use crate::app::{App, InputMode, MessageKind, Screen};
use crate::modules::hardware::{LlmBackend, MemoryTier};
use crate::modules::models::ModelRecommendation;
use crate::modules::profile;
use crate::theme;
use crossterm::event::{KeyCode, KeyEvent};
use ratatui::prelude::*;
use ratatui::widgets::*;
use std::collections::HashMap;

const FIELD_LABELS: &[&str] = &[
    "Label",
    "Environment",
    "Backend",
    "Remote Host",
    "Remote User",
    "Remote Path",
    "Tailscale IP",
    "Model Tier",
    "Admin Email",
    "Allowed Domains",
    "Frontend Ref",
];

const FIELD_COUNT: usize = 11;

const FIELD_LABEL: usize = 0;
const FIELD_ENVIRONMENT: usize = 1;
const FIELD_BACKEND: usize = 2;
const FIELD_REMOTE_HOST: usize = 3;
const FIELD_REMOTE_USER: usize = 4;
const FIELD_REMOTE_PATH: usize = 5;
const FIELD_TAILSCALE_IP: usize = 6;
const FIELD_MODEL_TIER: usize = 7;
const FIELD_ADMIN_EMAIL: usize = 8;
const FIELD_ALLOWED_DOMAINS: usize = 9;
const FIELD_FRONTEND_REF: usize = 10;

// Default settings use a subset of fields
const DEFAULTS_FIELD_LABELS: &[&str] = &[
    "Admin Email",
    "HuggingFace Token",
    "Frontend Ref",
    "Remote User",
];
const DEFAULTS_FIELD_COUNT: usize = 4;
const DEFAULTS_ADMIN_EMAIL: usize = 0;
const DEFAULTS_HF_TOKEN: usize = 1;
const DEFAULTS_FRONTEND_REF: usize = 2;
const DEFAULTS_REMOTE_USER: usize = 3;

pub fn render(f: &mut Frame, app: &App) {
    let chunks = Layout::default()
        .direction(Direction::Vertical)
        .constraints([
            Constraint::Length(3),
            Constraint::Min(10),
            Constraint::Length(3),
        ])
        .margin(2)
        .split(f.area());

    let is_defaults = app.profile_edit_id.as_deref() == Some("__defaults__");
    let profile_id = app.profile_edit_id.as_deref().unwrap_or("unknown");
    let title = if is_defaults {
        Paragraph::new("Default Settings")
    } else {
        Paragraph::new(format!("Edit Profile: {profile_id}"))
    }
    .style(theme::title())
    .alignment(Alignment::Center);
    f.render_widget(title, chunks[0]);

    let mut rows: Vec<Row> = Vec::new();

    if is_defaults {
        let defaults = app
            .profiles
            .as_ref()
            .and_then(|pf| pf.defaults.clone())
            .unwrap_or_default();

        for (i, label) in DEFAULTS_FIELD_LABELS.iter().enumerate() {
            let value = if app.profile_editing && i == app.profile_edit_field {
                format!("{}▎", app.profile_edit_buffer)
            } else {
                defaults_field_value(&defaults, i)
            };

            let label_style = if i == app.profile_edit_field {
                theme::highlight()
            } else {
                theme::heading()
            };

            let value_style = if app.profile_editing && i == app.profile_edit_field {
                Style::default().fg(Color::White).bg(Color::DarkGray)
            } else if i == app.profile_edit_field {
                theme::selected()
            } else {
                theme::normal()
            };

            rows.push(Row::new(vec![
                Cell::from(format!(" {label}")).style(label_style),
                Cell::from(format!(" {value}")).style(value_style),
                Cell::from(""),
            ]));
        }
    } else {
        let profile = get_editing_profile(app);

        for (i, label) in FIELD_LABELS.iter().enumerate() {
            let value = if app.profile_editing && i == app.profile_edit_field {
                format!("{}▎", app.profile_edit_buffer)
            } else {
                field_value(&profile, i)
            };

            let label_style = if i == app.profile_edit_field {
                theme::highlight()
            } else {
                theme::heading()
            };

            let value_style = if app.profile_editing && i == app.profile_edit_field {
                Style::default().fg(Color::White).bg(Color::DarkGray)
            } else if i == app.profile_edit_field {
                theme::selected()
            } else {
                theme::normal()
            };

            let hint = field_hint(i, &profile);
            let hint_cell = if !hint.is_empty() && i == app.profile_edit_field {
                Cell::from(hint).style(theme::muted())
            } else {
                Cell::from("")
            };

            rows.push(Row::new(vec![
                Cell::from(format!(" {label}")).style(label_style),
                Cell::from(format!(" {value}")).style(value_style),
                hint_cell,
            ]));
        }
    }

    let table = Table::new(
        rows,
        [
            Constraint::Length(16),
            Constraint::Min(30),
            Constraint::Min(25),
        ],
    )
    .block(
        Block::default()
            .borders(Borders::ALL)
            .border_style(theme::dim())
            .title(if is_defaults {
                " Default Fields "
            } else {
                " Profile Fields "
            })
            .title_style(theme::heading()),
    )
    .row_highlight_style(theme::selected());

    f.render_widget(table, chunks[1]);

    let help = if app.profile_edit_tier_selecting {
        Paragraph::new(Line::from(vec![
            Span::styled("↑/↓ ", theme::highlight()),
            Span::styled("Select  ", theme::normal()),
            Span::styled("Enter ", theme::highlight()),
            Span::styled("Confirm  ", theme::normal()),
            Span::styled("Esc ", theme::muted()),
            Span::styled("Cancel", theme::muted()),
        ]))
    } else if app.profile_editing {
        Paragraph::new(Line::from(vec![
            Span::styled(" Enter ", theme::highlight()),
            Span::styled("Save  ", theme::normal()),
            Span::styled("Esc ", theme::muted()),
            Span::styled("Cancel  ", theme::muted()),
            Span::styled("←/→ ", theme::muted()),
            Span::styled("Cycle (dropdowns)", theme::muted()),
        ]))
    } else {
        Paragraph::new(Line::from(vec![
            Span::styled(" Enter ", theme::highlight()),
            Span::styled("Edit  ", theme::normal()),
            Span::styled("↑/↓ ", theme::highlight()),
            Span::styled("Navigate  ", theme::normal()),
            Span::styled("s ", theme::highlight()),
            Span::styled("Save  ", theme::normal()),
            Span::styled("Esc ", theme::muted()),
            Span::styled("Back", theme::muted()),
        ]))
    };
    f.render_widget(help, chunks[2]);

    if app.profile_edit_tier_selecting {
        render_tier_overlay(f, app);
    }
}

fn render_tier_overlay(f: &mut Frame, app: &App) {
    let area = f.area();
    let overlay_area = Rect::new(
        area.x + 4,
        area.y + 4,
        area.width.saturating_sub(8),
        area.height.saturating_sub(8),
    );

    let clear = Clear;
    f.render_widget(clear, overlay_area);

    let inner = Block::default()
        .borders(Borders::ALL)
        .border_style(theme::dim())
        .title(" Select Model Tier ")
        .title_style(theme::heading());

    let inner_area = inner.inner(overlay_area);
    f.render_widget(inner, overlay_area);

    let content_chunks = Layout::default()
        .direction(Direction::Horizontal)
        .constraints([
            Constraint::Percentage(40),
            Constraint::Percentage(60),
        ])
        .split(inner_area);

    let profile = get_editing_profile(app);
    let recommended_tier = profile.hardware.as_ref().map(|h| h.memory_tier);
    let backend = profile
        .hardware
        .as_ref()
        .map(|h| &h.llm_backend)
        .unwrap_or(&LlmBackend::Mlx);

    render_tier_list_overlay(f, app, content_chunks[0], recommended_tier);
    render_tier_details_overlay(f, app, content_chunks[1], backend);
}

fn render_tier_list_overlay(
    f: &mut Frame,
    app: &App,
    area: Rect,
    recommended: Option<MemoryTier>,
) {
    let tiers = MemoryTier::all();

    let items: Vec<ListItem> = tiers
        .iter()
        .enumerate()
        .map(|(i, tier)| {
            let is_recommended = recommended.map(|r| r == *tier).unwrap_or(false);
            let marker = if is_recommended { "★ " } else { "  " };
            let name = format!("{}{}", marker, capitalize(tier.name()));

            let style = if i == app.profile_edit_tier_cursor {
                theme::selected()
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

    let list = List::new(items).block(
        Block::default()
            .borders(Borders::ALL)
            .border_style(theme::dim())
            .title(" Tiers ")
            .title_style(theme::heading()),
    );
    f.render_widget(list, area);
}

fn render_tier_details_overlay(
    f: &mut Frame,
    app: &App,
    area: Rect,
    backend: &LlmBackend,
) {
    let tiers = MemoryTier::all();
    let selected_tier = tiers
        .get(app.profile_edit_tier_cursor)
        .copied()
        .unwrap_or(MemoryTier::Standard);

    let config_path = app.repo_root
        .join("provision")
        .join("ansible")
        .join("group_vars")
        .join("all")
        .join("model_registry.yml");
    let recommendation = config_path
        .exists()
        .then(|| ModelRecommendation::from_config(&config_path, selected_tier, backend).ok())
        .flatten();

    let mut lines: Vec<Line> = Vec::new();

    lines.push(Line::from(vec![
        Span::styled(
            format!(" {} ", capitalize(selected_tier.name())),
            theme::heading(),
        ),
        Span::styled(
            format!("— {}", selected_tier.description()),
            theme::muted(),
        ),
    ]));
    lines.push(Line::from(""));

    if let Some(rec) = &recommendation {
        let mut unique_sizes: HashMap<&str, f64> = HashMap::new();

        for model in rec.models() {
            let size_str = if model.estimated_size_gb < 1.0 {
                format!("{:.0} MB", model.estimated_size_gb * 1024.0)
            } else {
                format!("{:.1} GB", model.estimated_size_gb)
            };

            lines.push(Line::from(vec![
                Span::styled(format!(" {:12}", model.role), theme::heading()),
                Span::styled(model.name.clone(), theme::normal()),
            ]));
            lines.push(Line::from(vec![
                Span::styled("             ", theme::normal()),
                Span::styled(size_str, theme::info()),
            ]));
            lines.push(Line::from(""));

            if !model.name.is_empty() {
                unique_sizes.entry(&model.name).or_insert(model.estimated_size_gb);
            }
        }

        let total: f64 = unique_sizes.values().sum();
        let total_str = if total < 1.0 {
            format!("{:.0} MB", total * 1024.0)
        } else {
            format!("{:.1} GB", total)
        };

        let unique_count = unique_sizes.len();
        let model_count = rec.models().len();
        let dedup_note = if unique_count < model_count {
            format!(" ({unique_count} unique)")
        } else {
            String::new()
        };

        lines.push(Line::from(vec![
            Span::styled(" TOTAL       ", theme::heading()),
            Span::styled(total_str, theme::highlight()),
            Span::styled(dedup_note, theme::muted()),
        ]));
    } else {
        lines.push(Line::from(Span::styled(
            " No model configuration available",
            theme::muted(),
        )));
    }

    let paragraph = Paragraph::new(lines)
        .block(
            Block::default()
                .borders(Borders::ALL)
                .border_style(theme::dim())
                .title(format!(" {} Models ", capitalize(selected_tier.name())))
                .title_style(theme::heading()),
        )
        .wrap(Wrap { trim: false });

    f.render_widget(paragraph, area);
}

fn capitalize(s: &str) -> String {
    let mut chars = s.chars();
    match chars.next() {
        None => String::new(),
        Some(c) => c.to_uppercase().collect::<String>() + chars.as_str(),
    }
}

pub fn handle_key(app: &mut App, key: KeyEvent) {
    if app.profile_editing {
        handle_edit_mode(app, key);
    } else {
        handle_nav_mode(app, key);
    }
}

fn handle_nav_mode(app: &mut App, key: KeyEvent) {
    match key.code {
        KeyCode::Esc => {
            app.screen = Screen::ProfileSelect;
            app.profile_edit_id = None;
            app.profile_editing = false;
            app.profile_edit_tier_selecting = false;
            app.input_mode = InputMode::Normal;
        }
        KeyCode::Up | KeyCode::Char('k') => {
            if app.profile_edit_field > 0 {
                app.profile_edit_field -= 1;
            }
        }
        KeyCode::Down | KeyCode::Char('j') => {
            let max_field = if app.profile_edit_id.as_deref() == Some("__defaults__") {
                DEFAULTS_FIELD_COUNT - 1
            } else {
                FIELD_COUNT - 1
            };
            if app.profile_edit_field < max_field {
                app.profile_edit_field += 1;
            }
        }
        KeyCode::Enter => {
            if app.profile_edit_id.as_deref() == Some("__defaults__") {
                // Defaults: all text fields
                let defaults = app
                    .profiles
                    .as_ref()
                    .and_then(|pf| pf.defaults.clone())
                    .unwrap_or_default();
                // For HF token, show real value when editing
                app.profile_edit_buffer = match app.profile_edit_field {
                    DEFAULTS_HF_TOKEN => defaults.huggingface_token.unwrap_or_default(),
                    _ => defaults_field_value(&defaults, app.profile_edit_field),
                };
                app.profile_editing = true;
                app.input_mode = InputMode::Editing;
            } else if app.profile_edit_field == FIELD_MODEL_TIER {
                let profile = get_editing_profile(app);
                let tier = profile.effective_model_tier();
                app.profile_edit_tier_cursor = tier
                    .map(|t| t.index())
                    .unwrap_or_else(|| MemoryTier::Standard.index());
                app.profile_edit_buffer = MemoryTier::all()
                    .get(app.profile_edit_tier_cursor)
                    .map(|t| t.name().to_string())
                    .unwrap_or_else(|| "(auto-detect)".into());
                app.profile_edit_tier_selecting = true;
                app.profile_editing = true;
                app.input_mode = InputMode::Editing;
            } else {
                let profile = get_editing_profile(app);
                app.profile_edit_buffer = field_value(&profile, app.profile_edit_field);
                app.profile_editing = true;
                app.input_mode = InputMode::Editing;
            }
        }
        KeyCode::Char('s') => {
            save_profile(app);
        }
        _ => {}
    }
}

fn handle_edit_mode(app: &mut App, key: KeyEvent) {
    if app.profile_edit_tier_selecting {
        handle_tier_selector(app, key);
        return;
    }

    let is_defaults = app.profile_edit_id.as_deref() == Some("__defaults__");
    if is_defaults {
        // All defaults fields are text fields
        handle_text_edit(app, key);
        return;
    }

    let field = app.profile_edit_field;

    match field {
        FIELD_ENVIRONMENT | FIELD_BACKEND | FIELD_MODEL_TIER => {
            handle_dropdown_edit(app, key);
        }
        _ => {
            handle_text_edit(app, key);
        }
    }
}

fn handle_tier_selector(app: &mut App, key: KeyEvent) {
    let tiers = MemoryTier::all();
    let tier_count = tiers.len();

    match key.code {
        KeyCode::Esc => {
            app.profile_edit_tier_selecting = false;
            app.profile_editing = false;
            app.input_mode = InputMode::Normal;
        }
        KeyCode::Enter => {
            let selected_tier = tiers
                .get(app.profile_edit_tier_cursor)
                .copied()
                .unwrap_or(MemoryTier::Standard);
            apply_field(app, FIELD_MODEL_TIER, selected_tier.name());
            app.profile_edit_tier_selecting = false;
            app.profile_editing = false;
            app.input_mode = InputMode::Normal;
        }
        KeyCode::Up | KeyCode::Char('k') => {
            if app.profile_edit_tier_cursor > 0 {
                app.profile_edit_tier_cursor -= 1;
            }
        }
        KeyCode::Down | KeyCode::Char('j') => {
            if app.profile_edit_tier_cursor < tier_count.saturating_sub(1) {
                app.profile_edit_tier_cursor += 1;
            }
        }
        _ => {}
    }
}

fn handle_dropdown_edit(app: &mut App, key: KeyEvent) {
    let field = app.profile_edit_field;

    match key.code {
        KeyCode::Esc => {
            app.profile_editing = false;
            app.input_mode = InputMode::Normal;
        }
        KeyCode::Enter => {
            apply_field(app, field, &app.profile_edit_buffer.clone());
            app.profile_editing = false;
            app.input_mode = InputMode::Normal;
        }
        KeyCode::Left | KeyCode::Right | KeyCode::Up | KeyCode::Down => {
            let options = dropdown_options(field);
            if options.is_empty() {
                return;
            }
            let current_idx = options
                .iter()
                .position(|o| *o == app.profile_edit_buffer)
                .unwrap_or(0);

            let next_idx = match key.code {
                KeyCode::Right | KeyCode::Down => {
                    if current_idx < options.len() - 1 {
                        current_idx + 1
                    } else {
                        0
                    }
                }
                _ => {
                    if current_idx > 0 {
                        current_idx - 1
                    } else {
                        options.len() - 1
                    }
                }
            };
            app.profile_edit_buffer = options[next_idx].to_string();
        }
        _ => {}
    }
}

fn handle_text_edit(app: &mut App, key: KeyEvent) {
    match key.code {
        KeyCode::Esc => {
            app.profile_editing = false;
            app.input_mode = InputMode::Normal;
        }
        KeyCode::Enter => {
            if app.profile_edit_id.as_deref() == Some("__defaults__") {
                apply_defaults_field(app, app.profile_edit_field, &app.profile_edit_buffer.clone());
            } else {
                apply_field(app, app.profile_edit_field, &app.profile_edit_buffer.clone());
            }
            app.profile_editing = false;
            app.input_mode = InputMode::Normal;
        }
        KeyCode::Backspace => {
            app.profile_edit_buffer.pop();
        }
        KeyCode::Char(c) => {
            app.profile_edit_buffer.push(c);
        }
        _ => {}
    }
}

fn get_editing_profile(app: &App) -> profile::Profile {
    let id = app.profile_edit_id.as_deref().unwrap_or("");
    app.profiles
        .as_ref()
        .and_then(|pf| pf.profiles.get(id))
        .cloned()
        .unwrap_or_else(default_profile)
}

fn default_profile() -> profile::Profile {
    profile::Profile {
        environment: "staging".into(),
        backend: "docker".into(),
        label: String::new(),
        created: None,
        vault_prefix: None,
        remote: false,
        remote_host: None,
        remote_user: None,
        remote_ssh_key: None,
        remote_busibox_path: None,
        tailscale_ip: None,
        hardware: None,
        kubeconfig: None,
        model_tier: None,
        admin_email: None,
        allowed_email_domains: None,
        frontend_ref: None,
    }
}

fn field_value(profile: &profile::Profile, field: usize) -> String {
    match field {
        FIELD_LABEL => profile.label.clone(),
        FIELD_ENVIRONMENT => profile.environment.clone(),
        FIELD_BACKEND => profile.backend.clone(),
        FIELD_REMOTE_HOST => profile.remote_host.clone().unwrap_or_default(),
        FIELD_REMOTE_USER => profile.remote_user.clone().unwrap_or_else(|| "root".into()),
        FIELD_REMOTE_PATH => profile
            .remote_busibox_path
            .clone()
            .unwrap_or_else(|| "~/busibox".into()),
        FIELD_TAILSCALE_IP => profile.tailscale_ip.clone().unwrap_or_default(),
        FIELD_MODEL_TIER => {
            let tier = profile.effective_model_tier();
            tier.map(|t| t.name().to_string())
                .unwrap_or_else(|| "(auto-detect)".into())
        }
        FIELD_ADMIN_EMAIL => profile.admin_email.clone().unwrap_or_default(),
        FIELD_ALLOWED_DOMAINS => profile
            .allowed_email_domains
            .clone()
            .unwrap_or_default(),
        FIELD_FRONTEND_REF => profile.frontend_ref.clone().unwrap_or_else(|| "latest".into()),
        _ => String::new(),
    }
}

fn field_hint(field: usize, profile: &profile::Profile) -> String {
    match field {
        FIELD_ENVIRONMENT => "←/→ to cycle: staging, production".into(),
        FIELD_BACKEND => "←/→ to cycle: docker, proxmox".into(),
        FIELD_MODEL_TIER => {
            let hw_tier = profile
                .hardware
                .as_ref()
                .map(|h| format!("HW recommended: {}", h.memory_tier.name()));
            hw_tier.unwrap_or_else(|| "←/→ to cycle tiers".into())
        }
        FIELD_ADMIN_EMAIL => "Used for initial login".into(),
        FIELD_ALLOWED_DOMAINS => "Comma-separated domains; leave blank to allow any domain".into(),
        FIELD_FRONTEND_REF => "Git ref: 'latest' (newest release), 'main' (dev), or tag 'v1.0.0'".into(),
        FIELD_REMOTE_HOST => {
            if profile.remote {
                "SSH hostname or IP".into()
            } else {
                "(not applicable for local)".into()
            }
        }
        _ => String::new(),
    }
}

fn dropdown_options(field: usize) -> Vec<&'static str> {
    match field {
        FIELD_ENVIRONMENT => vec!["staging", "production"],
        FIELD_BACKEND => vec!["docker", "proxmox"],
        FIELD_MODEL_TIER => {
            let tiers = MemoryTier::all();
            tiers.iter().map(|t| t.name()).collect()
        }
        _ => vec![],
    }
}

fn apply_field(app: &mut App, field: usize, value: &str) {
    let id = match &app.profile_edit_id {
        Some(id) => id.clone(),
        None => return,
    };

    let profiles = match &mut app.profiles {
        Some(p) => p,
        None => return,
    };

    let profile = match profiles.profiles.get_mut(&id) {
        Some(p) => p,
        None => return,
    };

    match field {
        FIELD_LABEL => profile.label = value.to_string(),
        FIELD_ENVIRONMENT => {
            profile.environment = value.to_string();
            profile.vault_prefix = Some(if value == "production" {
                "prod".to_string()
            } else {
                "staging".to_string()
            });
        }
        FIELD_BACKEND => profile.backend = value.to_string(),
        FIELD_REMOTE_HOST => {
            profile.remote_host = if value.is_empty() {
                None
            } else {
                Some(value.to_string())
            };
            profile.remote = !value.is_empty() || profile.tailscale_ip.is_some();
        }
        FIELD_REMOTE_USER => {
            profile.remote_user = if value.is_empty() || value == "root" {
                Some("root".into())
            } else {
                Some(value.to_string())
            };
        }
        FIELD_REMOTE_PATH => {
            profile.remote_busibox_path = if value.is_empty() {
                None
            } else {
                Some(value.to_string())
            };
        }
        FIELD_TAILSCALE_IP => {
            profile.tailscale_ip = if value.is_empty() {
                None
            } else {
                Some(value.to_string())
            };
        }
        FIELD_MODEL_TIER => {
            if MemoryTier::from_name(value).is_some() {
                let hw_tier = profile.hardware.as_ref().map(|h| h.memory_tier);
                if hw_tier.map(|t| t.name()) == Some(value) {
                    profile.model_tier = None;
                } else {
                    profile.model_tier = Some(value.to_string());
                }
            }
        }
        FIELD_ADMIN_EMAIL => {
            profile.admin_email = if value.is_empty() {
                None
            } else {
                Some(value.to_string())
            };
        }
        FIELD_ALLOWED_DOMAINS => {
            profile.allowed_email_domains = if value.is_empty() {
                None
            } else {
                Some(value.to_string())
            };
        }
        FIELD_FRONTEND_REF => {
            if value.is_empty() || value == "latest" {
                profile.frontend_ref = None;
            } else {
                profile.frontend_ref = Some(value.to_string());
            }
        }
        _ => {}
    }
}

fn defaults_field_value(defaults: &profile::ProfileDefaults, field: usize) -> String {
    match field {
        DEFAULTS_ADMIN_EMAIL => defaults.admin_email.clone().unwrap_or_default(),
        DEFAULTS_HF_TOKEN => defaults
            .huggingface_token
            .as_ref()
            .map(|t| {
                if t.len() > 8 {
                    format!("{}...{}", &t[..4], &t[t.len() - 4..])
                } else {
                    t.clone()
                }
            })
            .unwrap_or_default(),
        DEFAULTS_FRONTEND_REF => defaults
            .frontend_ref
            .clone()
            .unwrap_or_else(|| "latest".into()),
        DEFAULTS_REMOTE_USER => defaults
            .remote_user
            .clone()
            .unwrap_or_else(|| "root".into()),
        _ => String::new(),
    }
}

fn apply_defaults_field(app: &mut App, field: usize, value: &str) {
    let profiles = match &mut app.profiles {
        Some(p) => p,
        None => return,
    };

    let defaults = profiles.defaults.get_or_insert_with(Default::default);

    match field {
        DEFAULTS_ADMIN_EMAIL => {
            defaults.admin_email = if value.is_empty() {
                None
            } else {
                Some(value.to_string())
            };
        }
        DEFAULTS_HF_TOKEN => {
            // Only update if the user actually typed something new (not the masked version)
            if !value.contains("...") {
                defaults.huggingface_token = if value.is_empty() {
                    None
                } else {
                    Some(value.to_string())
                };
            }
        }
        DEFAULTS_FRONTEND_REF => {
            defaults.frontend_ref = if value.is_empty() || value == "latest" {
                None
            } else {
                Some(value.to_string())
            };
        }
        DEFAULTS_REMOTE_USER => {
            defaults.remote_user = if value.is_empty() || value == "root" {
                None
            } else {
                Some(value.to_string())
            };
        }
        _ => {}
    }
}

fn save_profile(app: &mut App) {
    let repo_root = app.repo_root.clone();

    if app.profile_edit_id.as_deref() == Some("__defaults__") {
        // Saving defaults
        if let Some(profiles) = &app.profiles {
            match profile::save_profiles(&repo_root, profiles) {
                Ok(()) => {
                    app.set_message("Default settings saved", MessageKind::Success);
                    app.screen = Screen::ProfileSelect;
                    app.profile_edit_id = None;
                    app.profile_editing = false;
                    app.input_mode = InputMode::Normal;
                }
                Err(e) => {
                    app.set_message(&format!("Failed to save: {e}"), MessageKind::Error);
                }
            }
        }
        return;
    }

    if let Some(profiles) = &app.profiles {
        match profile::save_profiles(&repo_root, profiles) {
            Ok(()) => {
                if let Some(ref id) = app.profile_edit_id {
                    if let Some(p) = profiles.profiles.get(id) {
                        let _ = profile::write_profile_state(&repo_root, id, p);
                    }
                }
                app.set_message("Profile saved", MessageKind::Success);
                app.screen = Screen::ProfileSelect;
                app.profile_edit_id = None;
                app.profile_editing = false;
                app.profile_edit_tier_selecting = false;
                app.input_mode = InputMode::Normal;
            }
            Err(e) => {
                app.set_message(
                    &format!("Failed to save: {e}"),
                    MessageKind::Error,
                );
            }
        }
    }
}
