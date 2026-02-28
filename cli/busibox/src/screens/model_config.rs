use crate::app::{App, MessageKind, Screen, SetupTarget};
use crate::modules::models::ModelRecommendation;
use crate::theme;
use crossterm::event::{KeyCode, KeyEvent};
use ratatui::prelude::*;
use ratatui::widgets::*;

pub fn render(f: &mut Frame, app: &App) {
    let chunks = Layout::default()
        .direction(Direction::Vertical)
        .constraints([
            Constraint::Length(3),
            Constraint::Min(12),
            Constraint::Length(3),
        ])
        .margin(2)
        .split(f.area());

    let title = Paragraph::new("Model Configuration")
        .style(theme::title())
        .alignment(Alignment::Center);
    f.render_widget(title, chunks[0]);

    if let Some(rec) = &app.model_recommendation {
        let mut rows = Vec::new();
        for model in rec.models() {
            let size_str = if model.estimated_size_gb < 1.0 {
                format!("{:.0} MB", model.estimated_size_gb * 1024.0)
            } else {
                format!("{:.1} GB", model.estimated_size_gb)
            };
            rows.push(Row::new(vec![
                Cell::from(model.role.clone()).style(theme::heading()),
                Cell::from(model.name.clone()).style(theme::normal()),
                Cell::from(size_str).style(theme::info()),
            ]));
        }

        // Deduplicate: if models are the same, only count once
        let mut unique_sizes: std::collections::HashMap<&str, f64> = std::collections::HashMap::new();
        for model in rec.models() {
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
            format!(
                " ({} unique model{})",
                unique_count,
                if unique_count == 1 { "" } else { "s" }
            )
        } else {
            String::new()
        };

        rows.push(Row::new(vec![
            Cell::from("").style(theme::dim()),
            Cell::from("").style(theme::dim()),
            Cell::from("").style(theme::dim()),
        ]));
        rows.push(Row::new(vec![
            Cell::from("TOTAL").style(theme::heading()),
            Cell::from(dedup_note).style(theme::muted()),
            Cell::from(total_str).style(theme::highlight()),
        ]));

        let table = Table::new(
            rows,
            [
                Constraint::Length(12),
                Constraint::Min(40),
                Constraint::Length(12),
            ],
        )
        .header(
            Row::new(vec![
                Cell::from("Role").style(theme::muted()),
                Cell::from("Model").style(theme::muted()),
                Cell::from("Size").style(theme::muted()),
            ])
            .bottom_margin(1),
        )
        .block(
            Block::default()
                .borders(Borders::ALL)
                .border_style(theme::dim())
                .title(format!(
                    " {} Tier — {} ",
                    rec.tier, rec.tier_description
                ))
                .title_style(theme::heading()),
        );
        f.render_widget(table, chunks[1]);
    } else {
        let loading = Paragraph::new("Loading model recommendations...")
            .style(theme::info())
            .alignment(Alignment::Center)
            .block(
                Block::default()
                    .borders(Borders::ALL)
                    .border_style(theme::dim()),
            );
        f.render_widget(loading, chunks[1]);
    }

    let help = if app.model_recommendation.is_some() {
        Paragraph::new(Line::from(vec![
            Span::styled(" Enter ", theme::highlight()),
            Span::styled("Confirm & Install  ", theme::normal()),
            Span::styled("Esc ", theme::muted()),
            Span::styled("Back", theme::muted()),
        ]))
    } else {
        Paragraph::new(Line::from(Span::styled(
            " ⠋ Loading model recommendations...",
            theme::info(),
        )))
    };
    f.render_widget(help, chunks[2]);
}

pub fn handle_key(app: &mut App, key: KeyEvent) {
    match key.code {
        KeyCode::Esc => {
            app.screen = Screen::HardwareReport;
        }
        KeyCode::Enter => {
            if app.model_recommendation.is_some() {
                save_profile_and_continue(app);
            }
        }
        _ => {}
    }
}

pub fn load_recommendations(app: &mut App) {
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

    let config_path = app.repo_root.join("config").join("demo-models.yaml");
    if !config_path.exists() {
        app.set_message(
            "config/demo-models.yaml not found",
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

    let profile_id = format!("{}-{}", environment, backend);

    let profile = Profile {
        environment: environment.to_string(),
        backend: backend.to_string(),
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
