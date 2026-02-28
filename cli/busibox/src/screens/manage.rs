use crate::app::{App, MessageKind, Screen, ServiceStatus, SetupTarget};
use crate::modules::remote;
use crate::theme;
use crossterm::event::{KeyCode, KeyEvent};
use ratatui::prelude::*;
use ratatui::widgets::*;

fn get_all_services(app: &App) -> Vec<(&'static str, String)> {
    let mut services = vec![
        ("Infrastructure", "postgres".to_string()),
        ("Infrastructure", "redis".to_string()),
        ("Infrastructure", "minio".to_string()),
        ("Infrastructure", "milvus".to_string()),
        ("APIs", "authz".to_string()),
        ("APIs", "agent".to_string()),
        ("APIs", "ingest".to_string()),
        ("APIs", "search".to_string()),
        ("APIs", "deploy".to_string()),
        ("APIs", "docs".to_string()),
        ("APIs", "embedding".to_string()),
    ];
    for svc in app.llm_services() {
        services.push(("LLM", svc.to_string()));
    }
    services.push(("Frontend", "proxy".to_string()));
    services.push(("Frontend", "core-apps".to_string()));
    services
}

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

    let title = Paragraph::new("Service Management")
        .style(theme::title())
        .alignment(Alignment::Center);
    f.render_widget(title, chunks[0]);

    if app.manage_services.is_empty() {
        let msg = Paragraph::new("Loading service status...")
            .style(theme::info())
            .alignment(Alignment::Center)
            .block(
                Block::default()
                    .borders(Borders::ALL)
                    .border_style(theme::dim())
                    .title(" Services ")
                    .title_style(theme::heading()),
            );
        f.render_widget(msg, chunks[1]);
    } else {
        let rows: Vec<Row> = app
            .manage_services
            .iter()
            .enumerate()
            .map(|(i, svc)| {
                let status_style = if svc.status.contains("running")
                    || svc.status.contains("healthy")
                    || svc.status.contains("active")
                {
                    theme::success()
                } else if svc.status.contains("stopped") || svc.status.contains("inactive") {
                    theme::warning()
                } else if svc.status.contains("error") || svc.status.contains("failed") {
                    theme::error()
                } else {
                    theme::dim()
                };

                let row_style = if i == app.manage_selected {
                    theme::selected()
                } else {
                    Style::default()
                };

                Row::new(vec![
                    Cell::from(svc.group.clone()).style(theme::muted()),
                    Cell::from(svc.name.clone()).style(theme::normal()),
                    Cell::from(svc.status.clone()).style(status_style),
                ])
                .style(row_style)
            })
            .collect();

        let table = Table::new(
            rows,
            [
                Constraint::Length(16),
                Constraint::Min(20),
                Constraint::Length(20),
            ],
        )
        .header(
            Row::new(vec![
                Cell::from("Group").style(theme::muted()),
                Cell::from("Service").style(theme::muted()),
                Cell::from("Status").style(theme::muted()),
            ])
            .bottom_margin(1),
        )
        .block(
            Block::default()
                .borders(Borders::ALL)
                .border_style(theme::dim())
                .title(" Services ")
                .title_style(theme::heading()),
        );
        f.render_widget(table, chunks[1]);
    }

    let help_text = if app.manage_services.is_empty() {
        " Enter Load  Esc Back"
    } else {
        " r Restart  l Logs  s Stop/Start  d Redeploy  Enter Refresh  Esc Back"
    };
    let help = Paragraph::new(Line::from(Span::styled(help_text, theme::muted())));
    f.render_widget(help, chunks[2]);
}

pub fn handle_key(app: &mut App, key: KeyEvent) {
    match key.code {
        KeyCode::Esc => {
            app.screen = Screen::Welcome;
            app.menu_selected = 0;
        }
        KeyCode::Up | KeyCode::Char('k') => {
            if app.manage_selected > 0 {
                app.manage_selected -= 1;
            }
        }
        KeyCode::Down | KeyCode::Char('j') => {
            if app.manage_selected < app.manage_services.len().saturating_sub(1) {
                app.manage_selected += 1;
            }
        }
        KeyCode::Enter => {
            load_service_status(app);
        }
        KeyCode::Char('r') => {
            run_action(app, "restart");
        }
        KeyCode::Char('l') => {
            run_action(app, "logs");
        }
        KeyCode::Char('s') => {
            let current_status = app
                .manage_services
                .get(app.manage_selected)
                .map(|s| s.status.clone())
                .unwrap_or_default();
            if current_status.contains("running")
                || current_status.contains("active")
            {
                run_action(app, "stop");
            } else {
                run_action(app, "start");
            }
        }
        KeyCode::Char('d') => {
            run_action(app, "redeploy");
        }
        _ => {}
    }
}

pub fn load_service_status(app: &mut App) {
    app.manage_services.clear();

    for (group, name) in get_all_services(app) {
        app.manage_services.push(ServiceStatus {
            name,
            group: group.to_string(),
            status: "checking...".into(),
        });
    }

    // Batch all status checks in one make call (avoids N container startups)
    let is_remote = app.setup_target == SetupTarget::Remote;
    let profile_data = app.active_profile().map(|(_, p)| p.clone());
    let ssh_ref = app.ssh_connection.clone();

    let all_services: Vec<String> = app.manage_services.iter().map(|s| s.name.clone()).collect();
    let service_list = all_services.join(",");

    let batch_output = if is_remote {
        if let Some(ssh) = &ssh_ref {
            let remote_path = profile_data
                .as_ref()
                .map(|p| p.effective_remote_path())
                .unwrap_or_else(|| app.remote_path_input.as_str());
            remote::exec_make_capture(
                ssh,
                remote_path,
                &format!("manage SERVICE={service_list} ACTION=status"),
            )
            .unwrap_or_default()
        } else {
            String::new()
        }
    } else {
        remote::run_local_make_capture(
            &app.repo_root,
            &format!("manage SERVICE={service_list} ACTION=status"),
        )
        .unwrap_or_default()
    };

    // Parse the batch output — each service's status line typically contains the service name
    for svc in &mut app.manage_services {
        let raw_line = batch_output
            .lines()
            .filter(|l| l.contains(&svc.name))
            .last()
            .unwrap_or("unknown")
            .trim()
            .to_string();
        let clean = remote::strip_ansi(&raw_line);
        svc.status = if clean.is_empty() {
            "unknown".into()
        } else {
            clean
        };
    }
}

fn run_action(app: &mut App, action: &str) {
    let svc = match app.manage_services.get(app.manage_selected) {
        Some(s) => s.clone(),
        None => return,
    };

    let is_remote = app.setup_target == SetupTarget::Remote;
    let make_args = format!("manage SERVICE={} ACTION={action}", svc.name);

    // For logs, signal the main loop to run interactively (with TUI suspended)
    if action == "logs" {
        if is_remote {
            if let Some(ssh) = &app.ssh_connection {
                let profile = app.active_profile().map(|(_, p)| p.clone());
                let remote_path = profile
                    .as_ref()
                    .map(|p| p.effective_remote_path())
                    .unwrap_or_else(|| app.remote_path_input.as_str());
                app.pending_interactive_cmd = Some(format!(
                    "REMOTE:{}:{}:cd {} && USE_MANAGER=0 make {}",
                    ssh.host, ssh.key_path, remote_path, make_args
                ));
            } else {
                app.set_message("No SSH connection", MessageKind::Error);
            }
        } else {
            app.pending_interactive_cmd = Some(make_args);
        }
        return;
    }

    let result = if is_remote {
        if let Some(ssh) = &app.ssh_connection {
            let profile = app.active_profile().map(|(_, p)| p.clone());
            let remote_path = profile
                .as_ref()
                .map(|p| p.effective_remote_path())
                .unwrap_or_else(|| app.remote_path_input.as_str());
            remote::exec_make(ssh, remote_path, &make_args)
        } else {
            Err(color_eyre::eyre::eyre!("No SSH"))
        }
    } else {
        remote::run_local_make(&app.repo_root, &make_args)
            .map_err(|e| color_eyre::eyre::eyre!("{e}"))
    };

    match result {
        Ok(0) => {
            app.set_message(
                &format!("{} {} successful", action, svc.name),
                MessageKind::Success,
            );
        }
        Ok(code) => {
            app.set_message(
                &format!("{} {} failed (exit {})", action, svc.name, code),
                MessageKind::Error,
            );
        }
        Err(e) => {
            app.set_message(&format!("{action} error: {e}"), MessageKind::Error);
        }
    }

    // Refresh status after action
    load_service_status(app);
}
