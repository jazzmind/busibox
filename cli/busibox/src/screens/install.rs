use crate::app::{App, InstallStatus, Screen, ServiceInstallState, SetupTarget};
use crate::modules::remote;
use crate::theme;
use crossterm::event::{KeyCode, KeyEvent};
use ratatui::prelude::*;
use ratatui::widgets::*;

const SPINNER: &[&str] = &["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"];

fn get_bootstrap_stages(_app: &App) -> Vec<(&'static str, &'static str, Vec<String>)> {
    vec![
        ("Database", "PostgreSQL", vec!["postgres".into()]),
        ("Authentication", "AuthZ API", vec!["authz".into()]),
        ("Deployment", "Deploy API", vec!["deploy".into()]),
        (
            "Portal",
            "Portal & Admin",
            vec!["core-apps".into()],
        ),
    ]
}

pub fn render(f: &mut Frame, app: &App) {
    if app.install_log_visible {
        render_log_viewer(f, app);
        return;
    }

    let chunks = Layout::default()
        .direction(Direction::Vertical)
        .constraints([
            Constraint::Length(3),
            Constraint::Length(1),
            Constraint::Min(6),
            Constraint::Length(1),
            Constraint::Length(3),
        ])
        .margin(2)
        .split(f.area());

    let title = Paragraph::new("Bootstrap Installation")
        .style(theme::title())
        .alignment(Alignment::Center);
    f.render_widget(title, chunks[0]);

    if app.install_services.is_empty() {
        let msg = Paragraph::new("Preparing...")
            .style(theme::info())
            .alignment(Alignment::Center);
        f.render_widget(msg, chunks[2]);
        return;
    }

    let any_failed = app.install_services.iter().any(|s| matches!(s.status, InstallStatus::Failed(_)));
    let subtitle = if app.install_complete && any_failed {
        Paragraph::new("Installation finished with errors!")
            .style(theme::error())
            .alignment(Alignment::Center)
    } else if app.install_complete {
        Paragraph::new("Installation complete!")
            .style(theme::success())
            .alignment(Alignment::Center)
    } else {
        Paragraph::new("Installing core services...")
            .style(theme::muted())
            .alignment(Alignment::Center)
    };
    f.render_widget(subtitle, chunks[1]);

    let tick = app.install_tick;
    let spinner_char = SPINNER[tick % SPINNER.len()];

    let mut lines: Vec<Line> = Vec::new();
    lines.push(Line::from(""));

    let stages = get_bootstrap_stages(app);
    for (stage_name, description, services) in &stages {
        let stage_status = aggregate_stage_status(app, services);

        let (icon, style, detail) = match &stage_status {
            InstallStatus::Pending => ("○".to_string(), theme::dim(), description.to_string()),
            InstallStatus::Deploying => (
                spinner_char.to_string(),
                theme::info(),
                format!("{description} deploying..."),
            ),
            InstallStatus::Healthy => (
                "✓".to_string(),
                theme::success(),
                format!("{description} installed"),
            ),
            InstallStatus::Failed(e) => {
                ("✗".to_string(), theme::error(), format!("failed: {e}"))
            }
        };

        lines.push(Line::from(vec![
            Span::styled(format!("  {icon} "), style),
            Span::styled(format!("{stage_name:<16}"), theme::normal()),
            Span::styled(detail, style),
        ]));
    }

    // Model download line
    lines.push(Line::from(""));
    let model_status = if app.install_complete {
        Line::from(vec![
            Span::styled("  ✓ ", theme::success()),
            Span::styled("Models          ", theme::normal()),
            Span::styled("cached", theme::success()),
        ])
    } else {
        Line::from(vec![
            Span::styled(format!("  {} ", spinner_char), theme::info()),
            Span::styled("Models          ", theme::normal()),
            Span::styled("downloading in background...", theme::info()),
        ])
    };
    lines.push(model_status);

    if app.install_complete {
        lines.push(Line::from(""));
        if let Some(url) = &app.install_portal_url {
            lines.push(Line::from(vec![
                Span::styled("  → ", theme::info()),
                Span::styled("Opening ", theme::normal()),
                Span::styled(url.as_str(), theme::highlight()),
            ]));
        }
    }

    let content = Paragraph::new(lines).block(
        Block::default()
            .borders(Borders::ALL)
            .border_style(theme::dim())
            .title(" Bootstrap Services ")
            .title_style(theme::heading()),
    );
    f.render_widget(content, chunks[2]);

    // Last log line as mini-status
    let last_log = app
        .install_log
        .last()
        .map(|s| s.as_str())
        .unwrap_or("");
    let log_style = if last_log.contains("ERROR") || last_log.contains("FAILED") {
        theme::error()
    } else {
        theme::dim()
    };
    let log_line = Paragraph::new(Line::from(Span::styled(last_log, log_style)));
    f.render_widget(log_line, chunks[3]);

    let help_text = if app.install_complete && any_failed {
        " r Retry  l View error logs  Enter Continue  Esc Back"
    } else if app.install_complete {
        " Enter Manage Services  l View logs  Esc Back"
    } else {
        " l View logs  Esc Cancel"
    };
    let help_style = if app.install_complete && any_failed {
        theme::warning()
    } else {
        theme::muted()
    };
    let help = Paragraph::new(Line::from(Span::styled(help_text, help_style)));
    f.render_widget(help, chunks[4]);
}

fn render_log_viewer(f: &mut Frame, app: &App) {
    let chunks = Layout::default()
        .direction(Direction::Vertical)
        .constraints([
            Constraint::Length(3),
            Constraint::Min(6),
            Constraint::Length(3),
        ])
        .margin(2)
        .split(f.area());

    let title = Paragraph::new("Installation Log")
        .style(theme::title())
        .alignment(Alignment::Center);
    f.render_widget(title, chunks[0]);

    let log_height = chunks[1].height.saturating_sub(2) as usize;
    let max_scroll = app.install_log.len().saturating_sub(log_height);
    let scroll = app.install_log_scroll.min(max_scroll);

    let visible: Vec<Line> = app
        .install_log
        .iter()
        .skip(scroll)
        .take(log_height)
        .map(|l| {
            let style = if l.contains("ERROR") || l.contains("FAILED") {
                theme::error()
            } else if l.contains("✓") || l.contains("SUCCESS") {
                theme::success()
            } else if l.starts_with("Deploying") || l.starts_with("Syncing") {
                theme::info()
            } else {
                theme::normal()
            };
            Line::from(Span::styled(l.as_str(), style))
        })
        .collect();

    let scrollbar_info = if app.install_log.len() > log_height {
        format!(
            " Log ({}-{} of {}) ",
            scroll + 1,
            (scroll + log_height).min(app.install_log.len()),
            app.install_log.len()
        )
    } else {
        " Log ".to_string()
    };

    let log_panel = Paragraph::new(visible).block(
        Block::default()
            .borders(Borders::ALL)
            .border_style(theme::dim())
            .title(scrollbar_info)
            .title_style(theme::heading()),
    );
    f.render_widget(log_panel, chunks[1]);

    let help = Paragraph::new(Line::from(Span::styled(
        " ↑/↓ Scroll  l/Esc Close log viewer",
        theme::muted(),
    )));
    f.render_widget(help, chunks[2]);
}

fn aggregate_stage_status(app: &App, services: &[String]) -> InstallStatus {
    let mut any_deploying = false;
    let mut any_failed = false;
    let mut all_healthy = true;
    let mut fail_msg = String::new();

    for svc in &app.install_services {
        if services.contains(&svc.name) {
            match &svc.status {
                InstallStatus::Deploying => {
                    any_deploying = true;
                    all_healthy = false;
                }
                InstallStatus::Failed(e) => {
                    any_failed = true;
                    all_healthy = false;
                    fail_msg = e.clone();
                }
                InstallStatus::Pending => {
                    all_healthy = false;
                }
                InstallStatus::Healthy => {}
            }
        }
    }

    if any_failed {
        InstallStatus::Failed(fail_msg)
    } else if any_deploying {
        InstallStatus::Deploying
    } else if all_healthy && !services.is_empty() {
        InstallStatus::Healthy
    } else {
        InstallStatus::Pending
    }
}

pub fn handle_key(app: &mut App, key: KeyEvent) {
    if app.install_log_visible {
        handle_log_viewer_key(app, key);
        return;
    }

    match key.code {
        KeyCode::Esc => {
            app.screen = Screen::Welcome;
            app.menu_selected = 0;
        }
        KeyCode::Char('l') => {
            app.install_log_visible = true;
            app.install_log_scroll = app.install_log.len().saturating_sub(1);
        }
        KeyCode::Char('r') => {
            if app.install_complete {
                let any_failed = app.install_services.iter().any(|s| matches!(s.status, InstallStatus::Failed(_)));
                if any_failed {
                    // Reset and re-run install
                    app.install_complete = false;
                    app.install_portal_url = None;
                    app.install_rx = None;
                    // Don't clear log - append to it
                    app.install_log.push("".into());
                    app.install_log.push("═══ Retrying installation... ═══".into());
                    app.install_log.push("".into());
                    // Reset failed/pending services back to pending
                    for svc in app.install_services.iter_mut() {
                        if !matches!(svc.status, InstallStatus::Healthy) {
                            svc.status = InstallStatus::Pending;
                        }
                    }
                    spawn_install_worker(app);
                }
            }
        }
        KeyCode::Enter => {
            if app.install_complete {
                app.screen = Screen::Manage;
            }
        }
        _ => {}
    }
}

fn handle_log_viewer_key(app: &mut App, key: KeyEvent) {
    match key.code {
        KeyCode::Esc | KeyCode::Char('l') => {
            app.install_log_visible = false;
        }
        KeyCode::Up | KeyCode::Char('k') => {
            if app.install_log_scroll > 0 {
                app.install_log_scroll -= 1;
            }
        }
        KeyCode::Down | KeyCode::Char('j') => {
            app.install_log_scroll += 1;
        }
        KeyCode::Home => {
            app.install_log_scroll = 0;
        }
        KeyCode::End => {
            app.install_log_scroll = app.install_log.len().saturating_sub(1);
        }
        _ => {}
    }
}

pub fn auto_start(app: &mut App) {
    init_install(app);
    spawn_install_worker(app);
}

fn init_install(app: &mut App) {
    app.install_services.clear();
    app.install_log.clear();
    app.install_complete = false;
    app.install_portal_url = None;
    app.install_tick = 0;

    let stages = get_bootstrap_stages(app);
    for (_, _, services) in &stages {
        for svc in services {
            app.install_services.push(ServiceInstallState {
                name: svc.clone(),
                group: String::new(),
                status: InstallStatus::Pending,
            });
        }
    }
}

fn spawn_install_worker(app: &mut App) {
    use crate::app::InstallUpdate;

    let (tx, rx) = std::sync::mpsc::channel::<InstallUpdate>();
    app.install_rx = Some(rx);

    let is_remote = app.setup_target == SetupTarget::Remote;
    let repo_root = app.repo_root.clone();
    let remote_path_input = app.remote_path_input.clone();

    let ssh_details: Option<(String, String, String)> = app.ssh_connection.as_ref().map(|ssh| {
        (ssh.host.clone(), ssh.user.clone(), ssh.key_path.clone())
    });

    let profile_remote_path: Option<String> = app
        .active_profile()
        .map(|(_, p)| p.clone())
        .map(|p| p.effective_remote_path().to_string());
    let profile_host: Option<String> = app
        .active_profile()
        .and_then(|(_, p)| p.effective_host().map(|s| s.to_string()));

    let stages: Vec<(String, String, Vec<String>)> = get_bootstrap_stages(app)
        .into_iter()
        .map(|(name, desc, svcs)| (name.to_string(), desc.to_string(), svcs))
        .collect();

    let already_healthy: std::collections::HashSet<String> = app
        .install_services
        .iter()
        .filter(|s| matches!(s.status, InstallStatus::Healthy))
        .map(|s| s.name.clone())
        .collect();

    std::thread::spawn(move || {
        use crate::app::InstallUpdate;
        use crate::app::InstallStatus;

        let remote_path = profile_remote_path
            .as_deref()
            .unwrap_or(&remote_path_input)
            .to_string();

        if is_remote {
            if let Some((ref host, ref user, ref key)) = ssh_details {
                let ssh = crate::modules::ssh::SshConnection::new(
                    profile_host.as_deref().unwrap_or(host),
                    user,
                    key,
                );
                let display_host = profile_host.as_deref().unwrap_or(host);

                let _ = tx.send(InstallUpdate::Log(format!(
                    "Syncing files to {display_host}:{remote_path}..."
                )));

                if let Err(e) = remote::ensure_remote_dir(&ssh, &remote_path) {
                    let _ = tx.send(InstallUpdate::Log(format!(
                        "ERROR: Failed to create remote dir: {e}"
                    )));
                    let _ = tx.send(InstallUpdate::Complete {
                        portal_url: None,
                    });
                    return;
                }

                if let Err(e) = remote::sync(
                    &repo_root,
                    display_host,
                    user,
                    key,
                    &remote_path,
                ) {
                    let _ = tx.send(InstallUpdate::Log(format!("ERROR: rsync failed: {e}")));
                    let _ = tx.send(InstallUpdate::Complete {
                        portal_url: None,
                    });
                    return;
                }
                let _ = tx.send(InstallUpdate::Log("✓ Files synced".into()));
            }
        }

        let _ = tx.send(InstallUpdate::Log(
            "Starting model download in background...".into(),
        ));
        let model_download_handle: Option<std::thread::JoinHandle<i32>> = if is_remote {
            if let Some((ref host, ref user, ref key)) = ssh_details {
                let host = host.clone();
                let user = user.clone();
                let key = key.clone();
                let rp = remote_path.clone();
                Some(std::thread::spawn(move || -> i32 {
                    let ssh_conn =
                        crate::modules::ssh::SshConnection::new(&host, &user, &key);
                    let cmd =
                        format!("cd {} && bash scripts/llm/download-models.sh 2>&1", rp);
                    ssh_conn.run(&cmd).map(|_| 0).unwrap_or(1)
                }))
            } else {
                None
            }
        } else {
            let repo = repo_root.clone();
            Some(std::thread::spawn(move || -> i32 {
                let script = repo.join("scripts/llm/download-models.sh");
                if !script.exists() {
                    return 1;
                }
                std::process::Command::new("bash")
                    .arg(script)
                    .current_dir(&repo)
                    .stdout(std::process::Stdio::null())
                    .stderr(std::process::Stdio::null())
                    .status()
                    .map(|s| if s.success() { 0 } else { 1 })
                    .unwrap_or(1)
            }))
        };

        let mut any_failed = false;
        for (stage_name, _description, services) in &stages {
            // Skip stages where all services are already healthy
            let stage_services_to_deploy: Vec<String> = services
                .iter()
                .filter(|s| !already_healthy.contains(*s))
                .cloned()
                .collect();
            if stage_services_to_deploy.is_empty() {
                let _ = tx.send(InstallUpdate::Log(format!(
                    "✓ {stage_name} already installed, skipping"
                )));
                continue;
            }

            let service_list = stage_services_to_deploy.join(",");
            let _ = tx.send(InstallUpdate::Log(format!(
                "Deploying {stage_name}: {service_list}..."
            )));

            for svc in services {
                let _ = tx.send(InstallUpdate::ServiceStatus {
                    name: svc.clone(),
                    status: InstallStatus::Deploying,
                });
            }

            let result: color_eyre::Result<(i32, String)> = if is_remote {
                if let Some((ref host, ref user, ref key)) = ssh_details {
                    let ssh =
                        crate::modules::ssh::SshConnection::new(host, user, key);
                    remote::exec_make_quiet(
                        &ssh,
                        &remote_path,
                        &format!("install SERVICE={service_list}"),
                    )
                } else {
                    Err(color_eyre::eyre::eyre!("No SSH connection"))
                }
            } else {
                remote::run_local_make_quiet(
                    &repo_root,
                    &format!("install SERVICE={service_list}"),
                )
            };

            match result {
                Ok((0, output)) => {
                    for line in output.lines() {
                        let trimmed = line.trim();
                        if !trimmed.is_empty() {
                            let _ = tx.send(InstallUpdate::Log(format!("  {trimmed}")));
                        }
                    }
                    for svc in services {
                        let _ = tx.send(InstallUpdate::ServiceStatus {
                            name: svc.clone(),
                            status: InstallStatus::Healthy,
                        });
                    }
                    let _ = tx.send(InstallUpdate::Log(format!("✓ {stage_name} installed")));
                }
                Ok((code, output)) => {
                    any_failed = true;
                    for line in output.lines() {
                        let trimmed = line.trim();
                        if !trimmed.is_empty() {
                            let _ = tx.send(InstallUpdate::Log(format!("  {trimmed}")));
                        }
                    }
                    for svc in services {
                        let _ = tx.send(InstallUpdate::ServiceStatus {
                            name: svc.clone(),
                            status: InstallStatus::Failed(format!("exit code {code}")),
                        });
                    }
                    let _ = tx.send(InstallUpdate::Log(format!(
                        "FAILED: {stage_name} (exit code {code})"
                    )));
                }
                Err(e) => {
                    any_failed = true;
                    for svc in services {
                        let _ = tx.send(InstallUpdate::ServiceStatus {
                            name: svc.clone(),
                            status: InstallStatus::Failed(e.to_string()),
                        });
                    }
                    let _ = tx.send(InstallUpdate::Log(format!(
                        "ERROR: {stage_name}: {e}"
                    )));
                }
            }

            // Stop on first failure - don't continue to next stage
            if any_failed {
                break;
            }
        }

        if let Some(handle) = model_download_handle {
            let _ = tx.send(InstallUpdate::Log(
                "Waiting for model download to complete...".into(),
            ));
            match handle.join() {
                Ok(0) => {
                    let _ = tx.send(InstallUpdate::Log(
                        "✓ Models downloaded successfully".into(),
                    ));
                }
                _ => {
                    let _ = tx.send(InstallUpdate::Log(
                        "Models will download on first use".into(),
                    ));
                }
            }
        }

        if any_failed {
            let _ = tx.send(InstallUpdate::Log(
                "✗ Installation finished with errors — check logs for details".into(),
            ));
            let _ = tx.send(InstallUpdate::Complete {
                portal_url: None,
            });
        } else {
            let _ = tx.send(InstallUpdate::Log(
                "✓ Bootstrap installation complete".into(),
            ));
            let portal_url = if is_remote {
                let host = ssh_details
                    .as_ref()
                    .map(|(h, _, _)| h.as_str())
                    .unwrap_or("localhost");
                format!("http://{host}/portal/setup")
            } else {
                "http://localhost/portal/setup".to_string()
            };
            let _ = tx.send(InstallUpdate::Complete {
                portal_url: Some(portal_url),
            });
        }
    });
}

pub fn open_browser(url: &str) {
    #[cfg(target_os = "macos")]
    {
        let _ = std::process::Command::new("open")
            .arg(url)
            .spawn();
    }
    #[cfg(target_os = "linux")]
    {
        let _ = std::process::Command::new("xdg-open")
            .arg(url)
            .spawn();
    }
}
