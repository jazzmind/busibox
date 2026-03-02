use crate::app::{App, InputMode, MessageKind, Screen, SetupTarget};
use crate::modules::hardware::MemoryTier;
use crate::modules::models::ModelRecommendation;
use crate::theme;
use crossterm::event::{KeyCode, KeyEvent};
use ratatui::prelude::*;
use ratatui::widgets::*;
use std::collections::HashMap;

pub fn render(f: &mut Frame, app: &App) {
    let chunks = Layout::default()
        .direction(Direction::Vertical)
        .constraints([
            Constraint::Length(3),
            Constraint::Min(10),
            Constraint::Length(3),
            Constraint::Length(3),
        ])
        .margin(2)
        .split(f.area());

    let title = Paragraph::new("Model Configuration")
        .style(theme::title())
        .alignment(Alignment::Center);
    f.render_widget(title, chunks[0]);

    let hw = if app.setup_target == SetupTarget::Remote {
        app.remote_hardware.as_ref()
    } else {
        app.local_hardware.as_ref()
    };
    let recommended_tier = hw.map(|h| h.memory_tier);
    let backend = hw.map(|h| &h.llm_backend);

    let content_chunks = Layout::default()
        .direction(Direction::Horizontal)
        .constraints([
            Constraint::Percentage(40),
            Constraint::Percentage(60),
        ])
        .split(chunks[1]);

    render_tier_list(f, app, content_chunks[0], recommended_tier);
    render_tier_details(f, app, content_chunks[1], backend);

    render_admin_email_input(f, app, chunks[2]);

    let help = if app.model_config_email_focused {
        Paragraph::new(Line::from(vec![
            Span::styled("Type email  ", theme::normal()),
            Span::styled("Tab ", theme::highlight()),
            Span::styled("Back to tiers  ", theme::normal()),
            Span::styled("Esc ", theme::muted()),
            Span::styled("Back", theme::muted()),
        ]))
    } else {
        Paragraph::new(Line::from(vec![
            Span::styled(" ↑/↓ ", theme::highlight()),
            Span::styled("Select tier  ", theme::normal()),
            Span::styled("Enter ", theme::highlight()),
            Span::styled("Confirm & Install  ", theme::normal()),
            Span::styled(" Tab ", theme::highlight()),
            Span::styled("Admin Email  ", theme::normal()),
            Span::styled("Esc ", theme::muted()),
            Span::styled("Back", theme::muted()),
        ]))
    };
    f.render_widget(help, chunks[3]);
}

fn render_admin_email_input(f: &mut Frame, app: &App, area: Rect) {
    let is_focused = app.model_config_email_focused;
    let border_style = if is_focused {
        theme::highlight()
    } else {
        theme::dim()
    };

    let content = if is_focused {
        let display = if app.admin_email_input.is_empty() {
            "▎".to_string()
        } else {
            format!("{}{}", app.admin_email_input, "▎")
        };
        Line::from(Span::styled(display, theme::normal()))
    } else {
        let display = if app.admin_email_input.is_empty() {
            "admin@example.com"
        } else {
            &app.admin_email_input
        };
        Line::from(Span::styled(display, theme::muted()))
    };

    let block = Block::default()
        .borders(Borders::ALL)
        .border_style(border_style)
        .title(" Admin Email ")
        .title_style(theme::heading());

    let paragraph = Paragraph::new(content).block(block);
    f.render_widget(paragraph, area);
}

fn render_tier_list(f: &mut Frame, app: &App, area: Rect, recommended: Option<MemoryTier>) {
    let tiers = MemoryTier::all();

    let items: Vec<ListItem> = tiers
        .iter()
        .enumerate()
        .map(|(i, tier)| {
            let is_recommended = recommended.map(|r| r == *tier).unwrap_or(false);
            let marker = if is_recommended { "★ " } else { "  " };
            let name = format!("{}{}", marker, capitalize(tier.name()));

            let style = if i == app.model_tier_selected {
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
            .title(" Select Tier ")
            .title_style(theme::heading()),
    );
    f.render_widget(list, area);
}

fn render_tier_details(f: &mut Frame, app: &App, area: Rect, backend: Option<&crate::modules::hardware::LlmBackend>) {
    let tiers = MemoryTier::all();
    let selected_tier = tiers.get(app.model_tier_selected).copied().unwrap_or(MemoryTier::Standard);

    let config_path = app.repo_root
        .join("provision")
        .join("ansible")
        .join("group_vars")
        .join("all")
        .join("model_registry.yml");
    let recommendation = if config_path.exists() {
        let backend_val = backend.cloned().unwrap_or(crate::modules::hardware::LlmBackend::Mlx);
        ModelRecommendation::from_config(&config_path, selected_tier, &backend_val).ok()
    } else {
        None
    };

    let mut lines: Vec<Line> = Vec::new();

    lines.push(Line::from(vec![
        Span::styled(format!(" {} ", capitalize(selected_tier.name())), theme::heading()),
        Span::styled(format!("— {}", selected_tier.description()), theme::muted()),
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

pub fn handle_key(app: &mut App, key: KeyEvent) {
    let tier_count = MemoryTier::all().len();

    if app.model_config_email_focused {
        match key.code {
            KeyCode::Tab | KeyCode::Enter => {
                app.model_config_email_focused = false;
                app.input_mode = InputMode::Normal;
            }
            KeyCode::Esc => {
                app.model_config_email_focused = false;
                app.input_mode = InputMode::Normal;
                app.screen = Screen::HardwareReport;
            }
            KeyCode::Backspace => {
                app.admin_email_input.pop();
            }
            KeyCode::Char(c) => {
                app.admin_email_input.push(c);
            }
            _ => {}
        }
        return;
    }

    match key.code {
        KeyCode::Tab => {
            app.model_config_email_focused = true;
            app.input_mode = InputMode::Editing;
        }
        KeyCode::Esc => {
            app.screen = Screen::HardwareReport;
        }
        KeyCode::Up | KeyCode::Char('k') => {
            if app.model_tier_selected > 0 {
                app.model_tier_selected -= 1;
            }
        }
        KeyCode::Down | KeyCode::Char('j') => {
            if app.model_tier_selected < tier_count.saturating_sub(1) {
                app.model_tier_selected += 1;
            }
        }
        KeyCode::Enter => {
            save_profile_and_continue(app);
        }
        _ => {}
    }
}

pub fn load_recommendations(app: &mut App) {
    let hw = if app.setup_target == SetupTarget::Remote {
        app.remote_hardware.as_ref()
    } else {
        app.local_hardware.as_ref()
    };

    if let Some(hw) = hw {
        if app.model_recommendation.is_none() {
            app.model_tier_selected = hw.memory_tier.index();
        }
    }

    if app.model_recommendation.is_some() {
        return;
    }

    let hw = if app.setup_target == SetupTarget::Remote {
        app.remote_hardware.as_ref()
    } else {
        app.local_hardware.as_ref()
    };

    let hw = match hw {
        Some(h) => h,
        None => return,
    };

    let config_path = app.repo_root
        .join("provision")
        .join("ansible")
        .join("group_vars")
        .join("all")
        .join("model_registry.yml");
    if !config_path.exists() {
        app.set_message(
            "model_registry.yml not found",
            MessageKind::Warning,
        );
        return;
    }

    match ModelRecommendation::from_config(&config_path, hw.memory_tier, &hw.llm_backend) {
        Ok(rec) => app.model_recommendation = Some(rec),
        Err(e) => {
            app.set_message(
                &format!("Failed to load model config: {e}"),
                MessageKind::Error,
            );
        }
    }
}

fn save_profile_and_continue(app: &mut App) {
    use crate::modules::profile::{self, Profile};

    let hw = if app.setup_target == SetupTarget::Remote {
        app.remote_hardware.clone()
    } else {
        app.local_hardware.clone()
    };

    let backends = app.backend_choices();
    let envs = app.env_choices();
    let backend = backends
        .get(app.remote_backend_choice)
        .unwrap_or(&"docker");
    let environment = envs
        .get(app.remote_env_choice)
        .unwrap_or(&"staging");

    let is_remote = app.setup_target == SetupTarget::Remote;
    let label = if is_remote {
        format!("{} {} ({})", environment, backend, app.remote_host_input)
    } else {
        format!("{} {} (local)", environment, backend)
    };

    let backend_lower = backend.to_lowercase();
    let profile_id = format!("{}-{}", environment, backend_lower);

    let selected_tier = MemoryTier::all()
        .get(app.model_tier_selected)
        .copied()
        .unwrap_or(MemoryTier::Standard);

    let hardware_tier = hw.as_ref().map(|h| h.memory_tier);
    let model_tier = if hardware_tier == Some(selected_tier) {
        None
    } else {
        Some(selected_tier.name().to_string())
    };

    let profile = Profile {
        environment: environment.to_string(),
        backend: backend_lower.clone(),
        label,
        created: Some(chrono_now()),
        vault_prefix: Some(if *environment == "production" {
            "prod".to_string()
        } else {
            "staging".to_string()
        }),
        remote: is_remote,
        remote_host: if is_remote {
            Some(app.remote_host_input.clone())
        } else {
            None
        },
        remote_user: if is_remote {
            Some(app.remote_user_input.clone())
        } else {
            None
        },
        remote_ssh_key: app.ssh_connection.as_ref().map(|c| c.key_path.clone()),
        remote_busibox_path: if is_remote {
            Some(app.remote_path_input.clone())
        } else {
            None
        },
        tailscale_ip: app
            .tailscale_remote
            .as_ref()
            .and_then(|s| s.ip.clone()),
        hardware: hw,
        kubeconfig: None,
        model_tier,
        admin_email: if app.admin_email_input.trim().is_empty() {
            None
        } else {
            Some(app.admin_email_input.trim().to_string())
        },
        allowed_email_domains: None,
        frontend_ref: None,
    };

    match profile::upsert_profile(&app.repo_root, &profile_id, profile, true) {
        Ok(()) => {
            match profile::load_profiles(&app.repo_root) {
                Ok(profiles) => app.profiles = Some(profiles),
                Err(_) => {}
            }
            app.screen = Screen::Install;
        }
        Err(e) => {
            app.set_message(
                &format!("Failed to save profile: {e}"),
                MessageKind::Error,
            );
        }
    }
}

fn chrono_now() -> String {
    use std::time::SystemTime;
    let now = SystemTime::now()
        .duration_since(SystemTime::UNIX_EPOCH)
        .unwrap()
        .as_secs();
    format!("{now}")
}

fn capitalize(s: &str) -> String {
    let mut chars = s.chars();
    match chars.next() {
        None => String::new(),
        Some(c) => c.to_uppercase().collect::<String>() + chars.as_str(),
    }
}
