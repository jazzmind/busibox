use crate::app::{App, DownloadStatus, MessageKind, ModelDownloadState, Screen, SetupTarget};
use crate::modules::remote;
use crate::theme;
use crossterm::event::{KeyCode, KeyEvent};
use ratatui::layout::Margin;
use ratatui::prelude::*;
use ratatui::widgets::{Scrollbar, ScrollbarOrientation, ScrollbarState, *};
use std::process::Command;

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

    let title = Paragraph::new("Model Download")
        .style(theme::title())
        .alignment(Alignment::Center);
    f.render_widget(title, chunks[0]);

    let mut lines = Vec::new();

    if app.model_download_progress.is_empty() {
        lines.push(Line::from(Span::styled(
            "  Press Enter to start downloading models...",
            theme::info(),
        )));

        if let Some(rec) = &app.model_recommendation {
            lines.push(Line::from(""));
            for model in rec.models() {
                if !model.name.is_empty() {
                    let size = if model.estimated_size_gb < 1.0 {
                        format!("{:.0} MB", model.estimated_size_gb * 1024.0)
                    } else {
                        format!("{:.1} GB", model.estimated_size_gb)
                    };
                    lines.push(Line::from(vec![
                        Span::styled(format!("  {}: ", model.role), theme::muted()),
                        Span::styled(&model.name, theme::normal()),
                        Span::styled(format!(" ({size})"), theme::dim()),
                    ]));
                }
            }
        }
    } else {
        for dl in &app.model_download_progress {
            let (icon, style) = match &dl.status {
                DownloadStatus::Pending => ("○", theme::dim()),
                DownloadStatus::Downloading => ("↓", theme::info()),
                DownloadStatus::Complete => ("✓", theme::success()),
                DownloadStatus::Failed(_) => ("✗", theme::error()),
            };

            lines.push(Line::from(vec![
                Span::styled(format!("  {icon} "), style),
                Span::styled(format!("[{}] ", dl.role), theme::muted()),
                Span::styled(&dl.name, theme::normal()),
            ]));

            if dl.status == DownloadStatus::Downloading {
                let bar_width = 30;
                let filled = (dl.progress * bar_width as f64) as usize;
                let empty = bar_width - filled;
                let bar = format!(
                    "    [{}{}] {:.0}%",
                    "█".repeat(filled),
                    "░".repeat(empty),
                    dl.progress * 100.0
                );
                lines.push(Line::from(Span::styled(bar, theme::info())));
            }

            if let DownloadStatus::Failed(e) = &dl.status {
                lines.push(Line::from(Span::styled(
                    format!("    Error: {e}"),
                    theme::error(),
                )));
            }
        }

        let all_done = app
            .model_download_progress
            .iter()
            .all(|d| matches!(d.status, DownloadStatus::Complete | DownloadStatus::Failed(_)));

        if all_done {
            lines.push(Line::from(""));
            lines.push(Line::from(Span::styled(
                "  Press Enter to continue to installation...",
                theme::info(),
            )));
        }
    }

    let total_lines = lines.len();
    let content_height = chunks[1].height.saturating_sub(2) as usize;
    let max_scroll = total_lines.saturating_sub(content_height);
    let scroll_offset = app.model_download_scroll.min(max_scroll);

    let content = Paragraph::new(lines)
        .scroll((scroll_offset as u16, 0))
        .block(
            Block::default()
                .borders(Borders::ALL)
                .border_style(theme::dim())
                .title(if total_lines > content_height {
                    format!(
                        " Downloads ({}-{} of {}) ",
                        scroll_offset + 1,
                        (scroll_offset + content_height).min(total_lines),
                        total_lines
                    )
                } else {
                    " Downloads ".to_string()
                })
                .title_style(theme::heading()),
        );
    f.render_widget(content, chunks[1]);

    if total_lines > content_height {
        let mut scrollbar_state = ScrollbarState::new(total_lines)
            .position(scroll_offset);
        let scrollbar = Scrollbar::new(ScrollbarOrientation::VerticalRight)
            .begin_symbol(Some("↑"))
            .end_symbol(Some("↓"));
        f.render_stateful_widget(
            scrollbar,
            chunks[1].inner(Margin { vertical: 1, horizontal: 0 }),
            &mut scrollbar_state,
        );
    }

    let help = Paragraph::new(Line::from(Span::styled(
        " Enter Start/Continue  Esc Back",
        theme::muted(),
    )));
    f.render_widget(help, chunks[2]);
}

pub fn handle_key(app: &mut App, key: KeyEvent) {
    match key.code {
        KeyCode::Esc => {
            app.screen = Screen::ModelConfig;
        }
        KeyCode::Up | KeyCode::Char('k') => {
            if app.model_download_scroll > 0 {
                app.model_download_scroll -= 1;
            }
        }
        KeyCode::Down | KeyCode::Char('j') => {
            app.model_download_scroll = app.model_download_scroll.saturating_add(1);
        }
        KeyCode::Enter => {
            if app.model_download_progress.is_empty() {
                start_downloads(app);
            } else {
                let all_done = app.model_download_progress.iter().all(|d| {
                    matches!(d.status, DownloadStatus::Complete | DownloadStatus::Failed(_))
                });
                if all_done {
                    save_profile_and_continue(app);
                }
            }
        }
        _ => {}
    }
}

fn start_downloads(app: &mut App) {
    let rec = match &app.model_recommendation {
        Some(r) => r.clone(),
        None => return,
    };

    // Deduplicate: if the same model is used for multiple roles, only download once
    let mut seen = std::collections::HashSet::new();
    let mut models: Vec<ModelDownloadState> = Vec::new();
    for m in rec.models() {
        if !m.name.is_empty() && seen.insert(m.name.clone()) {
            let roles: Vec<&str> = rec
                .models()
                .iter()
                .filter(|other| other.name == m.name)
                .map(|other| other.role.as_str())
                .collect();
            let role_label = roles.join("+");
            models.push(ModelDownloadState {
                name: m.name.clone(),
                role: role_label,
                progress: 0.0,
                status: DownloadStatus::Pending,
            });
        }
    }

    app.model_download_progress = models;

    for dl in &mut app.model_download_progress {
        dl.status = DownloadStatus::Downloading;
        dl.progress = 0.5;

        let is_embedding = dl.role == "embed";

        let download_result = if app.setup_target == SetupTarget::Remote {
            if let Some(ssh) = &app.ssh_connection {
                let profile = app
                    .profiles
                    .as_ref()
                    .and_then(|p| p.profiles.get(&p.active));
                let remote_path = profile
                    .map(|p| p.effective_remote_path())
                    .unwrap_or(app.remote_path_input.as_str());

                if is_embedding {
                    remote::exec_make(ssh, remote_path, "warmup MODEL_TYPE=embedding")
                } else {
                    remote::exec_make(
                        ssh,
                        remote_path,
                        &format!("manage SERVICE=vllm ACTION=pull MODEL={}", dl.name),
                    )
                }
            } else {
                Err(color_eyre::eyre::eyre!("No SSH connection"))
            }
        } else if is_embedding {
            download_embedding_locally(&app.repo_root, &dl.name)
        } else {
            let script = app.repo_root.join("scripts/llm/download-models.sh");
            if script.exists() {
                Command::new("bash")
                    .args([script.to_str().unwrap(), &dl.name])
                    .status()
                    .map(|s| if s.success() { 0 } else { 1 })
                    .map_err(|e| color_eyre::eyre::eyre!("{e}"))
            } else {
                Ok(0)
            }
        };

        match download_result {
            Ok(0) => {
                dl.status = DownloadStatus::Complete;
                dl.progress = 1.0;
            }
            Ok(code) => {
                dl.status =
                    DownloadStatus::Failed(format!("Exit code {code}"));
            }
            Err(e) => {
                dl.status = DownloadStatus::Failed(e.to_string());
            }
        }
    }
}

fn download_embedding_locally(
    _repo_root: &std::path::Path,
    model_name: &str,
) -> Result<i32, color_eyre::eyre::Error> {
    let home = std::env::var("HOME").unwrap_or_else(|_| "/tmp".into());
    let cache_dir = format!("{home}/.cache/fastembed");
    std::fs::create_dir_all(&cache_dir).ok();

    let model_normalized = model_name.replace('/', "_");
    let model_dir = format!("{cache_dir}/{model_normalized}");
    if std::path::Path::new(&model_dir).join("model.onnx").exists()
        || std::path::Path::new(&model_dir)
            .join("model_optimized.onnx")
            .exists()
    {
        return Ok(0);
    }

    let python_code = format!(
        "from fastembed import TextEmbedding; \
         e = TextEmbedding(model_name='{}', cache_dir='/root/.cache/fastembed'); \
         list(e.embed(['warmup']))",
        model_name
    );

    Command::new("docker")
        .args([
            "run", "--rm",
            "-v", &format!("{cache_dir}:/root/.cache/fastembed"),
            "python:3.11-slim",
            "bash", "-c",
            &format!("pip install -q fastembed && python -c \"{}\"", python_code),
        ])
        .status()
        .map(|s| if s.success() { 0 } else { 1 })
        .map_err(|e| color_eyre::eyre::eyre!("{e}"))
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
    let host_prefix = if is_remote {
        app.remote_host_input.as_str()
    } else {
        "local"
    };
    let profile_id = profile::build_profile_id(host_prefix, environment, &backend_lower);

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
        model_tier: None,
        admin_email: None,
        allowed_email_domains: None,
        frontend_ref: None,
        site_domain: if app.site_domain_input.trim().is_empty() {
            Some("localhost".to_string())
        } else {
            Some(app.site_domain_input.trim().to_string())
        },
        ssl_cert_name: if app.ssl_cert_name_input.trim().is_empty() {
            None
        } else {
            Some(app.ssl_cert_name_input.trim().to_string())
        },
        network_base_octets: None,
        use_production_vllm: None,
        docker_runtime: None,
        github_token: None,
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
