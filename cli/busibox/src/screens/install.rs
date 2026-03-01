use crate::app::{App, InstallStatus, MessageKind, ModelInstallState, Screen, ServiceInstallState, SetupTarget};
use crate::modules::remote;
use crate::theme;
use crossterm::event::{KeyCode, KeyEvent};
use ratatui::layout::Margin;
use ratatui::prelude::*;
use ratatui::widgets::{Scrollbar, ScrollbarOrientation, ScrollbarState, *};

const SPINNER: &[&str] = &["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"];

/// Map environment name to container/vault prefix.
/// Must match scripts/make/service-deploy.sh get_container_prefix().
/// Map environment name to container/vault prefix.
/// Must match scripts/make/service-deploy.sh get_container_prefix().
/// Docker can be: development, staging, production, demo
/// Proxmox can be: staging, production
pub fn env_to_prefix(environment: &str) -> String {
    match environment {
        "demo" => "demo",
        "development" => "dev",
        "staging" => "staging",
        "production" => "prod",
        _ => "dev", // default same as service-deploy.sh
    }
    .to_string()
}

fn shell_escape(s: &str) -> String {
    format!("'{}'", s.replace('\'', "'\\''"))
}

/// Verify a locally encrypted vault file starts with $ANSIBLE_VAULT.
fn verify_local_vault_encrypted(vault_path: &std::path::Path) -> bool {
    if let Ok(content) = std::fs::read_to_string(vault_path) {
        content.starts_with("$ANSIBLE_VAULT")
    } else {
        false
    }
}

/// Encrypt a vault file locally using the ANSIBLE_VAULT_PASSWORD env var.
/// Returns Ok(true) if encrypted and verified, Ok(false) if verification failed.
fn encrypt_vault_local(vault_path: &std::path::Path, vault_password: &str) -> color_eyre::Result<bool> {
    let repo_root = vault_path
        .ancestors()
        .find(|p| p.join("scripts/lib/vault-pass-from-env.sh").exists())
        .ok_or_else(|| color_eyre::eyre::eyre!("Cannot find repo root from vault path"))?;

    let env_script = repo_root.join("scripts/lib/vault-pass-from-env.sh");
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let _ = std::fs::set_permissions(&env_script, std::fs::Permissions::from_mode(0o755));
    }

    let output = std::process::Command::new("ansible-vault")
        .args(["encrypt", &vault_path.to_string_lossy(), "--vault-password-file", &env_script.to_string_lossy()])
        .env("ANSIBLE_VAULT_PASSWORD", vault_password)
        .output()?;

    if !output.status.success() {
        let stderr = String::from_utf8_lossy(&output.stderr);
        return Err(color_eyre::eyre::eyre!("ansible-vault encrypt failed: {}", stderr.trim()));
    }

    Ok(verify_local_vault_encrypted(vault_path))
}

fn get_bootstrap_stages(_app: &App) -> Vec<(&'static str, &'static str, Vec<String>)> {
    vec![
        ("Prerequisites", "Ansible & Dependencies", vec!["_prerequisites".into()]),
        ("Docker Cleanup", "Stop conflicting containers", vec!["_docker_cleanup".into()]),
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

    // Model download section
    lines.push(Line::from(""));

    if app.install_model_status.is_empty() && !app.install_complete {
        // Download hasn't started outputting yet
        lines.push(Line::from(vec![
            Span::styled(format!("  {} ", spinner_char), theme::info()),
            Span::styled("Models          ", theme::normal()),
            Span::styled("preparing download...", theme::dim()),
        ]));
    } else if app.install_model_status.is_empty() && app.install_complete {
        // Install complete but no model status was tracked (shouldn't happen normally)
        lines.push(Line::from(vec![
            Span::styled("  ✓ ", theme::success()),
            Span::styled("Models          ", theme::normal()),
            Span::styled("cached", theme::success()),
        ]));
    } else {
        // Show header
        let all_models_terminal = !app.install_model_status.is_empty()
            && app.install_model_status.iter().all(|(_, _, s)| {
                matches!(
                    s,
                    ModelInstallState::Cached
                        | ModelInstallState::Skipped
                        | ModelInstallState::Failed
                )
            });
        let models_done =
            app.install_models_complete || app.install_complete || all_models_terminal;
        let all_cached = app.install_model_status.iter().all(|(_, _, s)| {
            *s == ModelInstallState::Cached || *s == ModelInstallState::Skipped
        });
        let header_icon = if models_done && all_cached {
            Span::styled("  ✓ ", theme::success())
        } else if models_done && !all_cached {
            Span::styled("  ⚠ ", theme::warning())
        } else {
            Span::styled(format!("  {} ", spinner_char), theme::info())
        };

        let cached_count = app
            .install_model_status
            .iter()
            .filter(|(_, _, s)| *s == ModelInstallState::Cached)
            .count();
        let total_count = app
            .install_model_status
            .iter()
            .filter(|(_, _, s)| *s != ModelInstallState::Skipped)
            .count();

        let header_text = if total_count == 0 {
            "Models          n/a (no models for this tier)".to_string()
        } else if models_done && all_cached {
            format!("Models          all cached ({cached_count}/{total_count})")
        } else if models_done {
            format!("Models          {cached_count}/{total_count} cached")
        } else {
            format!("Models          {cached_count}/{total_count} cached, downloading...")
        };

        let header_style = if models_done && all_cached {
            theme::success()
        } else if models_done {
            theme::warning()
        } else {
            theme::info()
        };

        lines.push(Line::from(vec![
            header_icon,
            Span::styled(header_text, header_style),
        ]));

        // Per-model status rows
        let spinner = spinner_char.to_string();
        for (role, model_name, state) in &app.install_model_status {
            let short_name = model_name.rsplit('/').next().unwrap_or(model_name);
            let (icon, detail, detail_style): (String, String, ratatui::style::Style) = match state {
                ModelInstallState::Cached => ("✓".into(), short_name.to_string(), theme::success()),
                ModelInstallState::Downloading => {
                    (spinner.clone(), format!("{short_name}..."), theme::info())
                }
                ModelInstallState::Pending => ("○".into(), "waiting".into(), theme::dim()),
                ModelInstallState::Skipped => ("·".into(), "n/a".into(), theme::dim()),
                ModelInstallState::Failed => ("✗".into(), format!("{short_name} failed"), theme::error()),
            };
            lines.push(Line::from(vec![
                Span::styled(format!("    {icon} "), detail_style),
                Span::styled(format!("{:<10}", role), theme::dim()),
                Span::styled(detail, detail_style),
            ]));
        }
    }

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
    let scroll = if app.install_log_autoscroll {
        max_scroll
    } else {
        app.install_log_scroll.min(max_scroll)
    };

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

    let autoscroll_indicator = if app.install_log_autoscroll { " [AUTO] " } else { "" };
    let scrollbar_info = if app.install_log.len() > log_height {
        format!(
            " Log ({}-{} of {}){} ",
            scroll + 1,
            (scroll + log_height).min(app.install_log.len()),
            app.install_log.len(),
            autoscroll_indicator
        )
    } else {
        format!(" Log{} ", autoscroll_indicator)
    };

    let log_panel = Paragraph::new(visible).block(
        Block::default()
            .borders(Borders::ALL)
            .border_style(theme::dim())
            .title(scrollbar_info)
            .title_style(theme::heading()),
    );
    f.render_widget(log_panel, chunks[1]);

    if app.install_log.len() > log_height {
        let mut scrollbar_state = ScrollbarState::new(app.install_log.len())
            .position(scroll);
        let scrollbar = Scrollbar::new(ScrollbarOrientation::VerticalRight)
            .begin_symbol(Some("↑"))
            .end_symbol(Some("↓"));
        f.render_stateful_widget(
            scrollbar,
            chunks[1].inner(Margin { vertical: 1, horizontal: 0 }),
            &mut scrollbar_state,
        );
    }

    let help = Paragraph::new(Line::from(vec![
        Span::styled(" s ", theme::highlight()),
        Span::styled("Start  ", theme::normal()),
        Span::styled("e ", theme::highlight()),
        Span::styled("End/Auto  ", theme::normal()),
        Span::styled("↑/↓ ", theme::highlight()),
        Span::styled("Scroll  ", theme::normal()),
        Span::styled("c ", theme::highlight()),
        Span::styled("Copy  ", theme::normal()),
        Span::styled("l/Esc ", theme::muted()),
        Span::styled("Close", theme::muted()),
    ]));
    f.render_widget(help, chunks[2]);
}

/// Process an install log message. Returns true if the message was consumed (internal signal)
/// and should not be added to the visible log.
pub fn process_install_log(msg: &str, app: &mut App) -> bool {
    // Internal signal: model download complete
    if msg == "[model-complete]" {
        app.install_models_complete = true;
        return true;
    }

    // Parse model status from [model] prefixed lines
    if let Some(model_line) = msg.strip_prefix("  [model] ") {
        let cleaned = crate::modules::remote::strip_ansi(model_line);
        parse_model_status_line(app, &cleaned);
    }

    false
}

/// Parse model download output lines and update install_model_status
fn parse_model_status_line(app: &mut App, line: &str) {
    let line = line.trim();

    // Skip empty lines, headers, and generic messages
    if line.is_empty()
        || line.starts_with("Downloading all models")
        || line.starts_with("[INFO] Downloading all models")
        || line.starts_with("Model download complete")
        || line.starts_with("[SUCCESS] Model download complete")
    {
        return;
    }

    // "All Marker/Surya models cached" or "Marker/Surya models cached"
    if line.contains("Marker/Surya models cached") {
        update_model_state(app, "marker", "Marker/Surya", ModelInstallState::Cached);
        return;
    }

    // "Marker model download had errors" or "data-worker not running"
    if line.contains("Marker model download had errors") || line.contains("data-worker not running") {
        update_model_state(app, "marker", "Marker/Surya", ModelInstallState::Skipped);
        return;
    }

    // "Pre-downloading Marker/Surya models..."
    if line.contains("Pre-downloading Marker") {
        update_model_state(app, "marker", "Marker/Surya", ModelInstallState::Downloading);
        return;
    }

    // "[INFO] Skipping Marker/Surya models..." - tier too low
    if line.contains("Skipping Marker") {
        update_model_state(app, "marker", "Marker/Surya", ModelInstallState::Skipped);
        return;
    }

    // Lines with format: "✓ role: model_name (cached)" or "✓ role: model_name" or "[SUCCESS] role: model_name"
    if let Some(rest) = line.strip_prefix("✓ ").or_else(|| line.strip_prefix("[SUCCESS] ")) {
        if let Some((role, model)) = parse_role_model(rest) {
            update_model_state(app, &role, &model, ModelInstallState::Cached);
        }
        return;
    }

    // "Downloading role: model_name..." or "[INFO] Downloading role: model_name..."
    if let Some(rest) = line.strip_prefix("Downloading ").or_else(|| line.strip_prefix("[INFO] Downloading ")) {
        let rest = rest.trim_end_matches("...");
        if let Some((role, model)) = parse_role_model(rest) {
            update_model_state(app, &role, &model, ModelInstallState::Downloading);
        }
        return;
    }

    // "⚠ role: model_name — download may have failed" or "[WARNING] role: model_name"
    if let Some(rest) = line.strip_prefix("⚠ ").or_else(|| line.strip_prefix("[WARNING] ")) {
        if let Some((role, model)) = parse_role_model(rest) {
            if model.contains("not configured") {
                update_model_state(app, &role, "", ModelInstallState::Skipped);
            } else {
                update_model_state(app, &role, &model, ModelInstallState::Failed);
            }
        }
        return;
    }

    // "○ role: (not configured for this tier)"
    if let Some(rest) = line.strip_prefix("○ ") {
        if let Some((role, _)) = parse_role_model(rest) {
            update_model_state(app, &role, "", ModelInstallState::Skipped);
        }
        return;
    }

    // "[INFO] role: not configured for tier/backend, skipping"
    if let Some(rest) = line.strip_prefix("[INFO] ") {
        if let Some((role, msg)) = parse_role_model(rest) {
            if msg.contains("not configured") || msg.contains("skipping") {
                update_model_state(app, &role, "", ModelInstallState::Skipped);
                return;
            }
        }
    }
}

fn parse_role_model(s: &str) -> Option<(String, String)> {
    // Parse "role: model_name" or "role: model_name (cached)" or "role: (not configured...)"
    let colon_pos = s.find(':')?;
    let role = s[..colon_pos].trim().to_string();
    let model = s[colon_pos + 1..]
        .trim()
        .trim_end_matches(" (cached)")
        .trim_end_matches("...")
        .to_string();
    Some((role, model))
}

fn update_model_state(app: &mut App, role: &str, model: &str, state: ModelInstallState) {
    if let Some(entry) = app.install_model_status.iter_mut().find(|(r, _, _)| r == role) {
        if !model.is_empty() {
            entry.1 = model.to_string();
        }
        entry.2 = state;
    } else {
        app.install_model_status.push((role.to_string(), model.to_string(), state));
    }
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
            app.install_log_autoscroll = true;
        }
        KeyCode::Char('r') => {
            if app.install_complete {
                let any_failed = app.install_services.iter().any(|s| matches!(s.status, InstallStatus::Failed(_)));
                if any_failed {
                    // Reset and re-run install
                    app.install_complete = false;
                    app.install_model_status.clear();
                    app.install_models_complete = false;
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
            app.install_log_autoscroll = false;
            if app.install_log_scroll > 0 {
                app.install_log_scroll -= 1;
            }
        }
        KeyCode::Down | KeyCode::Char('j') => {
            app.install_log_autoscroll = false;
            app.install_log_scroll += 1;
        }
        KeyCode::Home | KeyCode::Char('s') => {
            app.install_log_autoscroll = false;
            app.install_log_scroll = 0;
        }
        KeyCode::End | KeyCode::Char('e') => {
            app.install_log_autoscroll = true;
            app.install_log_scroll = app.install_log.len().saturating_sub(1);
        }
        KeyCode::Char('c') => {
            // Copy log to clipboard
            let log_text = app.install_log.join("\n");
            let _ = copy_to_clipboard(&log_text);
            app.set_message("Log copied to clipboard", MessageKind::Info);
        }
        _ => {}
    }
}

fn copy_to_clipboard(text: &str) -> std::io::Result<()> {
    use std::io::Write;
    use std::process::{Command, Stdio};

    // Use pbcopy on macOS, xclip on Linux
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

pub fn auto_start(app: &mut App) {
    init_install(app);

    // If we already have a vault password (from profile selection unlock), proceed
    if app.vault_password.is_some() {
        spawn_install_worker(app);
        return;
    }

    // No vault password yet — trigger vault setup (first-time generation or unlock)
    app.pending_vault_setup = true;
}

/// Public entry point to start the install worker after vault setup completes.
pub fn spawn_install_worker_pub(app: &mut App) {
    spawn_install_worker(app);
}

fn init_install(app: &mut App) {
    app.install_services.clear();
    app.install_log.clear();
    app.install_complete = false;
    app.install_model_status.clear();
    app.install_models_complete = false;
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
    let vault_password: Option<String> = app.vault_password.clone();
    let clean_install = app.clean_install;
    // Derive vault prefix from environment (same logic as service-deploy.sh get_container_prefix)
    let vault_prefix: String = app
        .active_profile()
        .map(|(_, p)| env_to_prefix(&p.environment))
        .unwrap_or_else(|| "dev".into());

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
    let admin_email: Option<String> = app
        .active_profile()
        .and_then(|(_, p)| p.admin_email.clone());
    let frontend_ref: Option<String> = app
        .active_profile()
        .and_then(|(_, p)| p.frontend_ref.clone());
    let profile_model_tier: Option<String> = app
        .active_profile()
        .and_then(|(_, p)| p.effective_model_tier().map(|t| t.name().to_string()));
    let profile_llm_backend: Option<String> = app
        .active_profile()
        .and_then(|(_, p)| p.hardware.as_ref().map(|h| match h.llm_backend {
            crate::modules::hardware::LlmBackend::Mlx => "mlx".to_string(),
            crate::modules::hardware::LlmBackend::Vllm => "vllm".to_string(),
            crate::modules::hardware::LlmBackend::Cloud => "cloud".to_string(),
        }));

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

        // For clean installs, delete existing vault so it gets recreated fresh from example
        if clean_install {
            if let Some(ref _vp) = vault_password {
                let vault_rel = format!(
                    "provision/ansible/roles/secrets/vars/vault.{vault_prefix}.yml"
                );

                if is_remote {
                    if let Some((ref host, ref user, ref key)) = ssh_details {
                        let ssh = crate::modules::ssh::SshConnection::new(
                            profile_host.as_deref().unwrap_or(host),
                            user,
                            key,
                        );
                        let remote_path = profile_remote_path
                            .as_deref()
                            .unwrap_or(&remote_path_input);
                        let del_cmd = format!(
                            "cd {} && rm -f {}",
                            remote_path, vault_rel
                        );
                        let _ = ssh.run(&del_cmd);
                        let _ = tx.send(InstallUpdate::Log(
                            "  Clean install: removed existing vault file".into(),
                        ));
                    }
                } else {
                    let vault_path = repo_root.join(&vault_rel);
                    if vault_path.exists() {
                        let _ = std::fs::remove_file(&vault_path);
                        let _ = tx.send(InstallUpdate::Log(
                            "  Clean install: removed existing vault file".into(),
                        ));
                    }
                }
            }
        }

        // Verify vault password matches the vault file on the remote (or local)
        if let Some(ref vp) = vault_password {
            let _ = tx.send(InstallUpdate::Log("Verifying vault password...".into()));

            let vault_rel = format!(
                "provision/ansible/roles/secrets/vars/vault.{vault_prefix}.yml"
            );
            let example_rel = format!(
                "provision/ansible/roles/secrets/vars/vault.example.yml"
            );
            let _ = tx.send(InstallUpdate::Log(format!(
                "  vault_prefix={vault_prefix}, vault_rel={vault_rel}"
            )));
            let _ = tx.send(InstallUpdate::Log(format!(
                "  password first 10 chars: {}...", &vp[..vp.len().min(10)]
            )));

            if is_remote {
                if let Some((ref host, ref user, ref key)) = ssh_details {
                    let ssh = crate::modules::ssh::SshConnection::new(
                        profile_host.as_deref().unwrap_or(host),
                        user,
                        key,
                    );

                    // Check if vault file exists
                    let check_exists = format!(
                        "cd {} && [ -f {} ] && echo EXISTS || echo MISSING",
                        remote_path, vault_rel
                    );
                    let vault_exists = ssh
                        .run(&check_exists)
                        .map(|o| o.trim() == "EXISTS")
                        .unwrap_or(false);

                    let _ = tx.send(InstallUpdate::Log(format!(
                        "  vault exists on remote: {vault_exists}"
                    )));

                    if vault_exists {
                        // Test decryption using env var
                        let test_script = format!(
                            "ansible-vault view {vault_rel} --vault-password-file=\"$ANSIBLE_VAULT_PASSWORD_FILE\" >/dev/null 2>&1 && echo DECRYPT_OK || echo DECRYPT_FAIL"
                        );
                        let (_, test_output) = remote::exec_remote_with_vault(
                            &ssh, &remote_path, &test_script, vp,
                        ).unwrap_or((1, "DECRYPT_FAIL".into()));

                        if test_output.contains("DECRYPT_OK") {
                            let _ = tx.send(InstallUpdate::Log(
                                "✓ Vault password verified".into(),
                            ));
                        } else {
                            let _ = tx.send(InstallUpdate::Log(
                                "⚠ Vault password mismatch — recreating vault file...".into(),
                            ));

                            let recreate_script = format!(
                                "rm -f {vault_rel} && cp {example_rel} {vault_rel} && \
                                 ansible-vault encrypt {vault_rel} --vault-password-file=\"$ANSIBLE_VAULT_PASSWORD_FILE\" && \
                                 echo ENCRYPT_OK || echo ENCRYPT_FAIL"
                            );
                            let (rc, output) = remote::exec_remote_with_vault(
                                &ssh, &remote_path, &recreate_script, vp,
                            ).unwrap_or((1, "ENCRYPT_FAIL".into()));

                            if rc == 0 && output.contains("ENCRYPT_OK") {
                                let _ = tx.send(InstallUpdate::Log(
                                    "✓ Vault file recreated with correct password".into(),
                                ));
                            } else {
                                for line in output.lines() {
                                    let _ = tx.send(InstallUpdate::Log(format!("  {}", line)));
                                }
                                let _ = tx.send(InstallUpdate::Log("ERROR: Vault encryption failed".into()));
                                let _ = tx.send(InstallUpdate::Complete { portal_url: None });
                                return;
                            }
                        }
                    } else {
                        let _ = tx.send(InstallUpdate::Log(
                            "Creating vault file from example...".into(),
                        ));
                        let create_script = format!(
                            "cp {example_rel} {vault_rel} && \
                             ansible-vault encrypt {vault_rel} --vault-password-file=\"$ANSIBLE_VAULT_PASSWORD_FILE\" && \
                             echo ENCRYPT_OK || echo ENCRYPT_FAIL"
                        );
                        let (rc, output) = remote::exec_remote_with_vault(
                            &ssh, &remote_path, &create_script, vp,
                        ).unwrap_or((1, "ENCRYPT_FAIL".into()));

                        if rc == 0 && output.contains("ENCRYPT_OK") {
                            let _ = tx.send(InstallUpdate::Log(
                                "✓ Vault file created and encrypted".into(),
                            ));
                        } else {
                            for line in output.lines() {
                                let _ = tx.send(InstallUpdate::Log(format!("  {}", line)));
                            }
                            let _ = tx.send(InstallUpdate::Log("ERROR: Vault encryption failed".into()));
                            let _ = tx.send(InstallUpdate::Complete { portal_url: None });
                            return;
                        }
                    }
                }
            } else {
                // Local: use env var based vault operations
                let vault_path = repo_root.join(format!(
                    "provision/ansible/roles/secrets/vars/vault.{vault_prefix}.yml"
                ));
                let example_path =
                    repo_root.join("provision/ansible/roles/secrets/vars/vault.example.yml");

                let env_script = repo_root.join("scripts/lib/vault-pass-from-env.sh");
                #[cfg(unix)]
                {
                    use std::os::unix::fs::PermissionsExt;
                    let _ = std::fs::set_permissions(&env_script, std::fs::Permissions::from_mode(0o755));
                }

                if vault_path.exists() {
                    let test_result = std::process::Command::new("ansible-vault")
                        .args(["view", &vault_path.to_string_lossy(), "--vault-password-file", &env_script.to_string_lossy()])
                        .env("ANSIBLE_VAULT_PASSWORD", vp.as_str())
                        .stdout(std::process::Stdio::null())
                        .stderr(std::process::Stdio::null())
                        .status();

                    let can_decrypt = test_result.map(|s| s.success()).unwrap_or(false);

                    if !can_decrypt {
                        let _ = tx.send(InstallUpdate::Log(
                            "⚠ Vault password mismatch — recreating vault file...".into(),
                        ));
                        let _ = std::fs::remove_file(&vault_path);
                        if let Err(e) = std::fs::copy(&example_path, &vault_path) {
                            let _ = tx.send(InstallUpdate::Log(format!("ERROR: {e}")));
                            let _ = tx.send(InstallUpdate::Complete { portal_url: None });
                            return;
                        }
                        match encrypt_vault_local(&vault_path, vp) {
                            Ok(true) => {
                                let _ = tx.send(InstallUpdate::Log("✓ Vault file recreated".into()));
                            }
                            Ok(false) => {
                                let _ = tx.send(InstallUpdate::Log("ERROR: Vault file not encrypted after attempt".into()));
                                let _ = tx.send(InstallUpdate::Complete { portal_url: None });
                                return;
                            }
                            Err(e) => {
                                let _ = tx.send(InstallUpdate::Log(format!("ERROR: {e}")));
                                let _ = tx.send(InstallUpdate::Complete { portal_url: None });
                                return;
                            }
                        }
                    } else {
                        let _ = tx.send(InstallUpdate::Log("✓ Vault password verified".into()));
                    }
                } else if example_path.exists() {
                    let _ = tx.send(InstallUpdate::Log("Creating vault file from example...".into()));
                    if let Err(e) = std::fs::copy(&example_path, &vault_path) {
                        let _ = tx.send(InstallUpdate::Log(format!("ERROR: {e}")));
                        let _ = tx.send(InstallUpdate::Complete { portal_url: None });
                        return;
                    }
                    match encrypt_vault_local(&vault_path, vp) {
                        Ok(true) => {
                            let _ = tx.send(InstallUpdate::Log("✓ Vault file created and encrypted".into()));
                        }
                        Ok(false) => {
                            let _ = tx.send(InstallUpdate::Log("ERROR: Vault file not encrypted after attempt".into()));
                            let _ = tx.send(InstallUpdate::Complete { portal_url: None });
                            return;
                        }
                        Err(e) => {
                            let _ = tx.send(InstallUpdate::Log(format!("ERROR: {e}")));
                            let _ = tx.send(InstallUpdate::Complete { portal_url: None });
                            return;
                        }
                    }
                }
            }
        }

        // === Generate secrets for CHANGE_ME placeholders ===
        if let Some(ref vp) = vault_password {
            let _ = tx.send(InstallUpdate::Log("Generating vault secrets...".into()));

            let vault_rel = format!(
                "provision/ansible/roles/secrets/vars/vault.{vault_prefix}.yml"
            );

            // Script uses ANSIBLE_VAULT_PASSWORD env var via vault-pass-from-env.sh
            let admin_email_sed = admin_email
                .as_deref()
                .filter(|e| !e.is_empty())
                .map(|email| format!("sed -i.bak \"s/CHANGE_ME_ADMIN_EMAILS/{email}/g\" \"$VAULT_FILE\" && rm -f \"${{VAULT_FILE}}.bak\"\n"))
                .unwrap_or_default();

            // Read GitHub token from ~/.gittoken if it exists
            let github_token_sed = {
                let token = dirs::home_dir()
                    .map(|h| h.join(".gittoken"))
                    .and_then(|p| std::fs::read_to_string(&p).ok())
                    .map(|t| t.trim().to_string())
                    .filter(|t| !t.is_empty());
                token
                    .map(|t| format!("sed -i.bak \"s/CHANGE_ME_GITHUB_PERSONAL_ACCESS_TOKEN/{t}/g\" \"$VAULT_FILE\" && rm -f \"${{VAULT_FILE}}.bak\"\n"))
                    .unwrap_or_default()
            };

            let secrets_script = format!(
                r#"set -euo pipefail
VAULT_FILE="{vault_rel}"
VPF="$ANSIBLE_VAULT_PASSWORD_FILE"

if [ ! -f "$VAULT_FILE" ]; then
    echo "ERROR: Vault file not found: $VAULT_FILE"
    exit 1
fi

FIRST_LINE=$(head -1 "$VAULT_FILE")
IS_ENCRYPTED=false
if [[ "$FIRST_LINE" == *'$ANSIBLE_VAULT'* ]]; then
    IS_ENCRYPTED=true
fi

if [ "$IS_ENCRYPTED" = true ]; then
    CONTENT=$(ansible-vault view "$VAULT_FILE" --vault-password-file="$VPF" 2>&1) || {{
        echo "ERROR: Cannot decrypt vault file"
        echo "$CONTENT"
        exit 1
    }}
else
    CONTENT=$(cat "$VAULT_FILE")
fi

if ! echo "$CONTENT" | grep -q 'CHANGE_ME'; then
    echo "✓ All secrets already configured"
    exit 0
fi

if [ "$IS_ENCRYPTED" = true ]; then
    ansible-vault decrypt "$VAULT_FILE" --vault-password-file="$VPF"
fi

gen() {{ openssl rand -base64 "$1" | tr -d '/+=' | head -c "$1"; }}

PG_PASS=$(gen 24)
MINIO_PASS=$(gen 24)
JWT=$(gen 32)
AUTHZ_KEY=$(gen 32)
LITELLM_API=$(gen 16)
LITELLM_MASTER=$(openssl rand -hex 16)
LITELLM_SALT=$(gen 32)

sed -i.bak \
  -e "s/CHANGE_ME_POSTGRES_PASSWORD/$PG_PASS/g" \
  -e "s/CHANGE_ME_MINIO_ROOT_USER/minioadmin/g" \
  -e "s/CHANGE_ME_MINIO_ROOT_PASSWORD/$MINIO_PASS/g" \
  -e "s/CHANGE_ME_JWT_SECRET_32_BYTES/$JWT/g" \
  -e "s/CHANGE_ME_SESSION_SECRET_32_BYTES/$JWT/g" \
  -e "s/CHANGE_ME_AUTHZ_MASTER_KEY_32_BYTES/$AUTHZ_KEY/g" \
  -e "s/CHANGE_ME_LITELLM_API_KEY/$LITELLM_API/g" \
  -e "s/CHANGE_ME_LITELLM_MASTER_KEY/$LITELLM_MASTER/g" \
  -e "s/CHANGE_ME_LITELLM_SALT_KEY/$LITELLM_SALT/g" \
  "$VAULT_FILE"
rm -f "${{VAULT_FILE}}.bak"
{admin_email_sed}
{github_token_sed}
REMAINING=$(grep -c 'CHANGE_ME' "$VAULT_FILE" 2>/dev/null || echo 0)

ansible-vault encrypt "$VAULT_FILE" --vault-password-file="$VPF" 2>&1 || {{
    echo "ERROR: Failed to re-encrypt vault after secret generation"
    exit 1
}}

VERIFY_LINE=$(head -1 "$VAULT_FILE")
if [[ "$VERIFY_LINE" != *'$ANSIBLE_VAULT'* ]]; then
    echo "ERROR: Vault file not encrypted after re-encryption attempt"
    exit 1
fi

echo "✓ Generated 9 bootstrap secrets ($REMAINING optional placeholders remain)"
"#,
                vault_rel = vault_rel,
                admin_email_sed = admin_email_sed,
                github_token_sed = github_token_sed,
            );

            let gen_result: color_eyre::Result<(i32, String)> = if is_remote {
                if let Some((ref host, ref user, ref key)) = ssh_details {
                    let ssh = crate::modules::ssh::SshConnection::new(
                        profile_host.as_deref().unwrap_or(host),
                        user,
                        key,
                    );
                    remote::exec_remote_with_vault(&ssh, &remote_path, &secrets_script, vp)
                } else {
                    Err(color_eyre::eyre::eyre!("No SSH connection"))
                }
            } else {
                let env_script = repo_root.join("scripts/lib/vault-pass-from-env.sh");
                match std::process::Command::new("bash")
                    .arg("-c")
                    .arg(&secrets_script)
                    .env("ANSIBLE_VAULT_PASSWORD", vp.as_str())
                    .env("ANSIBLE_VAULT_PASSWORD_FILE", env_script.to_string_lossy().as_ref())
                    .current_dir(&repo_root)
                    .output()
                {
                    Ok(output) => {
                        let exit_code = output.status.code().unwrap_or(1);
                        let combined = format!(
                            "{}{}",
                            String::from_utf8_lossy(&output.stdout),
                            String::from_utf8_lossy(&output.stderr)
                        );
                        Ok((exit_code, remote::strip_ansi(&combined)))
                    }
                    Err(e) => Err(color_eyre::eyre::eyre!("{e}")),
                }
            };

            match gen_result {
                Ok((0, output)) => {
                    for line in output.lines() {
                        let trimmed = line.trim();
                        if !trimmed.is_empty() {
                            let _ = tx.send(InstallUpdate::Log(format!("  {trimmed}")));
                        }
                    }
                }
                Ok((code, output)) => {
                    for line in output.lines() {
                        let trimmed = line.trim();
                        if !trimmed.is_empty() {
                            let _ = tx.send(InstallUpdate::Log(format!("  {trimmed}")));
                        }
                    }
                    let _ = tx.send(InstallUpdate::Log(format!(
                        "WARNING: Secret generation had issues (exit code {code}) — continuing anyway"
                    )));
                }
                Err(e) => {
                    let _ = tx.send(InstallUpdate::Log(format!(
                        "WARNING: Secret generation failed: {e} — continuing anyway"
                    )));
                }
            }
        }

        let _ = tx.send(InstallUpdate::Log(
            "Starting model download in background...".into(),
        ));
        let dl_tx = tx.clone();
        let dl_tier = profile_model_tier.clone();
        let dl_backend = profile_llm_backend.clone();
        let dl_prefix = vault_prefix.clone();
        let model_download_handle: Option<std::thread::JoinHandle<i32>> = if is_remote {
            if let Some((ref host, ref user, ref key)) = ssh_details {
                let host = host.clone();
                let user = user.clone();
                let key = key.clone();
                let rp = remote_path.clone();
                Some(std::thread::spawn(move || -> i32 {
                    let ssh_conn =
                        crate::modules::ssh::SshConnection::new(&host, &user, &key);
                    let mut env_prefix = String::new();
                    if let Some(ref tier) = dl_tier {
                        env_prefix.push_str(&format!("LLM_TIER={tier} "));
                    }
                    if let Some(ref backend) = dl_backend {
                        env_prefix.push_str(&format!("LLM_BACKEND={backend} "));
                    }
                    let cmd = format!(
                        "{env_prefix}CONTAINER_PREFIX={dl_prefix} bash scripts/llm/download-models.sh"
                    );
                    let on_line = |line: &str| {
                        let _ = dl_tx.send(InstallUpdate::Log(format!("  [model] {line}")));
                    };
                    match remote::exec_remote_streaming(&ssh_conn, &rp, &cmd, on_line) {
                        Ok(0) => 0,
                        Ok(code) => {
                            let _ = dl_tx.send(InstallUpdate::Log(format!(
                                "  [model] Error: exit code {code}"
                            )));
                            code
                        }
                        Err(e) => {
                            let _ = dl_tx.send(InstallUpdate::Log(format!(
                                "  [model] Error: {e}"
                            )));
                            1
                        }
                    }
                }))
            } else {
                None
            }
        } else {
            let repo = repo_root.clone();
            Some(std::thread::spawn(move || -> i32 {
                use std::io::BufRead;

                let script = repo.join("scripts/llm/download-models.sh");
                if !script.exists() {
                    let _ = dl_tx.send(InstallUpdate::Log(format!(
                        "  [model] Error: script not found: {}",
                        script.display()
                    )));
                    return 1;
                }
                let script_path = script.to_string_lossy();
                let cmd_str = format!(
                    "bash '{}' 2>&1",
                    script_path.replace("'", "'\\''")
                );
                let mut cmd = std::process::Command::new("bash");
                cmd.arg("-c")
                    .arg(&cmd_str)
                    .current_dir(&repo)
                    .stdout(std::process::Stdio::piped())
                    .stderr(std::process::Stdio::piped())
                    .env("CONTAINER_PREFIX", &dl_prefix);
                if let Some(ref tier) = dl_tier {
                    cmd.env("LLM_TIER", tier);
                }
                if let Some(ref backend) = dl_backend {
                    cmd.env("LLM_BACKEND", backend);
                }
                let mut child = match cmd.spawn() {
                    Ok(c) => c,
                    Err(e) => {
                        let _ = dl_tx.send(InstallUpdate::Log(format!(
                            "  [model] Error: {e}"
                        )));
                        return 1;
                    }
                };
                if let Some(stdout) = child.stdout.take() {
                    let reader = std::io::BufReader::new(stdout);
                    for line in reader.lines().flatten() {
                        let _ = dl_tx.send(InstallUpdate::Log(format!("  [model] {line}")));
                    }
                }
                child
                    .wait()
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

            // Handle prerequisites specially - install Ansible etc. instead of make install
            if stage_services_to_deploy.len() == 1
                && stage_services_to_deploy.first().map(|s| s.as_str()) == Some("_prerequisites")
            {
                let _ = tx.send(InstallUpdate::Log(
                    "Checking and installing prerequisites...".into(),
                ));
                for svc in services {
                    let _ = tx.send(InstallUpdate::ServiceStatus {
                        name: svc.clone(),
                        status: InstallStatus::Deploying,
                    });
                }

                let prereq_script = r#"
                    set -e
                    # Expand PATH to include common pip install locations (zsh-safe: no bare globs)
                    for pydir in "$HOME/.local/bin" /usr/local/bin /opt/homebrew/bin; do
                        [ -d "$pydir" ] && export PATH="$pydir:$PATH"
                    done
                    for pydir in $(find "$HOME/Library/Python" -maxdepth 2 -name bin -type d 2>/dev/null); do
                        [ -d "$pydir" ] && export PATH="$pydir:$PATH"
                    done
                    # Ensure pip is available
                    if ! command -v pip3 &>/dev/null && ! command -v pip &>/dev/null; then
                        echo "Installing pip..."
                        if command -v apt-get &>/dev/null; then
                            apt-get update -qq && apt-get install -y -qq python3-pip 2>&1
                        elif command -v yum &>/dev/null; then
                            yum install -y python3-pip 2>&1
                        elif command -v brew &>/dev/null; then
                            brew install python3 2>&1
                        fi
                    fi
                    PIP=$(command -v pip3 2>/dev/null || command -v pip 2>/dev/null || echo pip3)
                    # Install ansible if not present
                    if ! command -v ansible-playbook &>/dev/null; then
                        echo "Installing Ansible..."
                        $PIP install --quiet ansible 2>&1
                        # Re-expand PATH after install (pip may have created new dirs)
                        for pydir in "$HOME/.local/bin" /usr/local/bin /opt/homebrew/bin; do
                            [ -d "$pydir" ] && export PATH="$pydir:$PATH"
                        done
                        for pydir in $(find "$HOME/Library/Python" -maxdepth 2 -name bin -type d 2>/dev/null); do
                            [ -d "$pydir" ] && export PATH="$pydir:$PATH"
                        done
                    fi
                    if ! command -v ansible-vault &>/dev/null; then
                        echo "Installing Ansible (vault missing)..."
                        $PIP install --quiet ansible 2>&1
                        for pydir in "$HOME/.local/bin" /usr/local/bin /opt/homebrew/bin; do
                            [ -d "$pydir" ] && export PATH="$pydir:$PATH"
                        done
                        for pydir in $(find "$HOME/Library/Python" -maxdepth 2 -name bin -type d 2>/dev/null); do
                            [ -d "$pydir" ] && export PATH="$pydir:$PATH"
                        done
                    fi
                    # Verify Ansible
                    if command -v ansible-playbook &>/dev/null; then
                        echo "✓ ansible-playbook: $(ansible-playbook --version | head -1)"
                    else
                        echo "ERROR: ansible-playbook still not found after install"
                        echo "Searched PATH: $PATH"
                        exit 1
                    fi

                    # === Docker checks ===
                    # On macOS with Docker Desktop, install Homebrew docker CLI
                    # to avoid credential helper issues in non-interactive SSH sessions.
                    # The Homebrew CLI is clean and talks to Docker Desktop's daemon via socket.
                    if [ "$(uname)" = "Darwin" ] && command -v brew &>/dev/null; then
                        # Check if Docker Desktop is installed
                        if [ -d "/Applications/Docker.app" ] || [ -d "$HOME/Applications/Docker.app" ]; then
                            # Install Homebrew docker CLI if not already from Homebrew
                            DOCKER_PATH=$(command -v docker 2>/dev/null || echo "")
                            if [ -z "$DOCKER_PATH" ] || [[ "$DOCKER_PATH" == *"Docker.app"* ]] || [[ "$DOCKER_PATH" == "/usr/local/bin/docker" ]]; then
                                echo "  Installing Homebrew docker CLI (avoids Desktop credential issues)..."
                                brew install --quiet docker 2>&1 || true
                                brew install --quiet docker-compose 2>&1 || true
                                # Ensure Homebrew docker is first in PATH
                                BREW_PREFIX=$(brew --prefix 2>/dev/null || echo "/opt/homebrew")
                                export PATH="$BREW_PREFIX/bin:$PATH"
                            fi
                            # Ensure Docker Desktop daemon is running
                            if ! docker info &>/dev/null; then
                                echo "Docker daemon not running — starting Docker Desktop..."
                                if [ -d "/Applications/Docker.app" ]; then
                                    open -a Docker 2>/dev/null || true
                                elif [ -d "$HOME/Applications/Docker.app" ]; then
                                    open -a "$HOME/Applications/Docker.app" 2>/dev/null || true
                                fi
                                WAITED=0
                                while ! docker info &>/dev/null; do
                                    sleep 2
                                    WAITED=$((WAITED + 2))
                                    if [ $WAITED -ge 60 ]; then
                                        echo "ERROR: Docker daemon did not start within 60 seconds"
                                        echo "Please start Docker Desktop manually and retry."
                                        exit 1
                                    fi
                                    if [ $((WAITED % 10)) -eq 0 ]; then
                                        echo "  Still waiting... (${WAITED}s)"
                                    fi
                                done
                                echo "✓ Docker daemon started (took ${WAITED}s)"
                            else
                                echo "✓ Docker daemon running"
                            fi
                        else
                            # No Docker Desktop — check for other docker installations
                            if ! command -v docker &>/dev/null; then
                                echo "ERROR: Docker is not installed."
                                echo "Please install Docker Desktop: https://docs.docker.com/desktop/install/mac-install/"
                                exit 1
                            fi
                            if ! docker info &>/dev/null; then
                                echo "ERROR: Docker daemon is not running."
                                exit 1
                            fi
                            echo "✓ Docker daemon running"
                        fi
                    else
                        # Linux or no Homebrew
                        if ! command -v docker &>/dev/null; then
                            echo "ERROR: Docker is not installed."
                            echo "Please install Docker Engine: https://docs.docker.com/engine/install/"
                            exit 1
                        fi
                        if ! docker info &>/dev/null; then
                            echo "Docker daemon not running — attempting to start..."
                            if command -v systemctl &>/dev/null; then
                                sudo systemctl start docker 2>/dev/null || true
                            elif command -v service &>/dev/null; then
                                sudo service docker start 2>/dev/null || true
                            fi
                            WAITED=0
                            while ! docker info &>/dev/null; do
                                sleep 2
                                WAITED=$((WAITED + 2))
                                if [ $WAITED -ge 60 ]; then
                                    echo "ERROR: Docker daemon did not start within 60 seconds"
                                    exit 1
                                fi
                                if [ $((WAITED % 10)) -eq 0 ]; then
                                    echo "  Still waiting... (${WAITED}s)"
                                fi
                            done
                            echo "✓ Docker daemon started (took ${WAITED}s)"
                        else
                            echo "✓ Docker daemon running"
                        fi
                    fi
                    echo "✓ docker: $(docker --version)"

                    # Check docker compose
                    if docker compose version &>/dev/null; then
                        echo "✓ docker compose: $(docker compose version --short 2>/dev/null || docker compose version)"
                    elif command -v docker-compose &>/dev/null; then
                        echo "✓ docker-compose: $(docker-compose --version)"
                    else
                        echo "ERROR: docker compose is not available"
                        echo "Please install Docker Compose v2."
                        exit 1
                    fi

                    # Ensure jq is available (needed by Ansible health checks)
                    if ! command -v jq &>/dev/null; then
                        echo "Installing jq..."
                        if command -v apt-get &>/dev/null; then
                            sudo apt-get install -y -qq jq 2>&1
                        elif command -v yum &>/dev/null; then
                            sudo yum install -y jq 2>&1
                        elif command -v brew &>/dev/null; then
                            brew install --quiet jq 2>&1
                        else
                            echo "WARNING: jq not found and no package manager available to install it"
                        fi
                    fi
                    if command -v jq &>/dev/null; then
                        echo "✓ jq: $(jq --version)"
                    else
                        echo "WARNING: jq not available — health checks may not work correctly"
                    fi

                    echo "✓ Prerequisites installed"
                "#;

                let result: color_eyre::Result<(i32, String)> = if is_remote {
                    if let Some((ref host, ref user, ref key)) = ssh_details {
                        let ssh = crate::modules::ssh::SshConnection::new(
                            profile_host.as_deref().unwrap_or(host),
                            user,
                            key,
                        );
                        let full_cmd = format!(
                            "{} bash -c {}",
                            crate::modules::remote::SHELL_PATH_PREAMBLE,
                            shell_escape(prereq_script)
                        );
                        let mut args: Vec<String> = vec![
                            "-o".into(),
                            "BatchMode=yes".into(),
                            "-o".into(),
                            "StrictHostKeyChecking=accept-new".into(),
                            "-o".into(),
                            "ConnectTimeout=10".into(),
                        ];
                        let key_expanded =
                            crate::modules::ssh::shellexpand_path(key);
                        if !key_expanded.is_empty()
                            && std::path::Path::new(&key_expanded).exists()
                        {
                            args.push("-i".into());
                            args.push(key_expanded);
                        }
                        args.push(ssh.ssh_target());
                        args.push(full_cmd);
                        match std::process::Command::new("ssh").args(&args).output() {
                            Ok(output) => {
                                let exit_code = output.status.code().unwrap_or(1);
                                let combined = format!(
                                    "{}{}",
                                    String::from_utf8_lossy(&output.stdout),
                                    String::from_utf8_lossy(&output.stderr)
                                );
                                Ok((exit_code, remote::strip_ansi(&combined)))
                            }
                            Err(e) => Err(color_eyre::eyre::eyre!("{e}")),
                        }
                    } else {
                        Err(color_eyre::eyre::eyre!("No SSH connection"))
                    }
                } else {
                    match std::process::Command::new("bash")
                        .arg("-c")
                        .arg(prereq_script)
                        .output()
                    {
                        Ok(output) => {
                            let exit_code = output.status.code().unwrap_or(1);
                            let combined = format!(
                                "{}{}",
                                String::from_utf8_lossy(&output.stdout),
                                String::from_utf8_lossy(&output.stderr)
                            );
                            Ok((exit_code, remote::strip_ansi(&combined)))
                        }
                        Err(e) => Err(color_eyre::eyre::eyre!("{e}")),
                    }
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
                        let _ = tx.send(InstallUpdate::Log("✓ Prerequisites ready".into()));
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
                            "FAILED: Prerequisites (exit code {code})"
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
                            "ERROR: Prerequisites: {e}"
                        )));
                    }
                }

                if any_failed {
                    break;
                }
                continue;
            } else if stage_services_to_deploy.len() == 1
                && stage_services_to_deploy.first().map(|s| s.as_str()) == Some("_docker_cleanup")
            {
                let _ = tx.send(InstallUpdate::Log(
                    "Checking for existing Docker containers...".into(),
                ));
                for svc in services {
                    let _ = tx.send(InstallUpdate::ServiceStatus {
                        name: svc.clone(),
                        status: InstallStatus::Deploying,
                    });
                }

                // Build cleanup script
                // 1. Always: stop any *-busibox compose projects that conflict
                // 2. If clean_install: also remove containers, volumes, networks
                let cleanup_script = if clean_install {
                    format!(r#"
                        set -e
                        echo "Clean install: removing all busibox Docker resources..."
                        # Find and stop ALL busibox compose projects
                        for project in $(docker compose ls --format '{{{{.Name}}}}' 2>/dev/null | grep -i busibox || true); do
                            echo "  Stopping project: $project"
                            docker compose -p "$project" down --remove-orphans 2>&1 || true
                        done
                        # Also clean up any orphaned busibox containers
                        ORPHANS=$(docker ps -a --filter "name=.*busibox.*\|.*-postgres\|.*-redis\|.*-minio\|.*-milvus\|.*-authz\|.*-agent\|.*-litellm" --format '{{{{.Names}}}}' 2>/dev/null | grep -E "(dev|staging|prod|demo)-" || true)
                        if [ -n "$ORPHANS" ]; then
                            echo "  Removing orphaned containers: $ORPHANS"
                            echo "$ORPHANS" | xargs docker rm -f 2>/dev/null || true
                        fi
                        # Remove busibox volumes (preserve model caches for faster reinstall)
                        VOLS=$(docker volume ls --format '{{{{.Name}}}}' 2>/dev/null | grep -E "(dev|staging|prod|demo)-busibox" | grep -v -E "model_cache|fastembed_cache|vllm_cache|ollama" || true)
                        if [ -n "$VOLS" ]; then
                            echo "  Removing volumes: $VOLS"
                            echo "$VOLS" | xargs docker volume rm -f 2>/dev/null || true
                        fi
                        PRESERVED=$(docker volume ls --format '{{{{.Name}}}}' 2>/dev/null | grep -E "(dev|staging|prod|demo)-busibox" | grep -E "model_cache|fastembed_cache|vllm_cache|ollama" || true)
                        if [ -n "$PRESERVED" ]; then
                            echo "  ✓ Preserved model cache volumes: $PRESERVED"
                        fi
                        # Remove busibox networks
                        NETS=$(docker network ls --format '{{{{.Name}}}}' 2>/dev/null | grep -E "(dev|staging|prod|demo)-busibox" || true)
                        if [ -n "$NETS" ]; then
                            echo "  Removing networks: $NETS"
                            echo "$NETS" | xargs docker network rm 2>/dev/null || true
                        fi
                        # Check for non-Docker processes on ports we need
                        BLOCKED_PORTS=""
                        for port in 5432 6379 9000 19530 8010 4111 8002 8001 3000; do
                            # Use lsof to find listeners (works on macOS and Linux)
                            HOLDER=$(lsof -i ":$port" -sTCP:LISTEN -t 2>/dev/null | head -1 || true)
                            if [ -n "$HOLDER" ]; then
                                PNAME=$(ps -p "$HOLDER" -o comm= 2>/dev/null || echo "unknown")
                                # Skip if it's a docker process (those are expected)
                                if ! echo "$PNAME" | grep -qi "docker\|com.docker\|vpnkit"; then
                                    echo "  ⚠ Port $port in use by non-Docker process: $PNAME (PID $HOLDER)"
                                    # Try to stop common services
                                    case "$PNAME" in
                                        postgres|postmaster)
                                            echo "    → Stopping local PostgreSQL..."
                                            if [ "$(uname)" = "Darwin" ]; then
                                                brew services stop postgresql 2>/dev/null || true
                                                brew services stop postgresql@14 2>/dev/null || true
                                                brew services stop postgresql@15 2>/dev/null || true
                                                brew services stop postgresql@16 2>/dev/null || true
                                            else
                                                sudo systemctl stop postgresql 2>/dev/null || true
                                                sudo service postgresql stop 2>/dev/null || true
                                            fi
                                            sleep 1
                                            # Verify it stopped
                                            if lsof -i ":$port" -sTCP:LISTEN -t &>/dev/null; then
                                                BLOCKED_PORTS="$BLOCKED_PORTS $port"
                                            else
                                                echo "    ✓ PostgreSQL stopped"
                                            fi
                                            ;;
                                        redis-server|redis)
                                            echo "    → Stopping local Redis..."
                                            if [ "$(uname)" = "Darwin" ]; then
                                                brew services stop redis 2>/dev/null || true
                                            else
                                                sudo systemctl stop redis 2>/dev/null || sudo systemctl stop redis-server 2>/dev/null || true
                                            fi
                                            sleep 1
                                            if lsof -i ":$port" -sTCP:LISTEN -t &>/dev/null; then
                                                BLOCKED_PORTS="$BLOCKED_PORTS $port"
                                            else
                                                echo "    ✓ Redis stopped"
                                            fi
                                            ;;
                                        *)
                                            BLOCKED_PORTS="$BLOCKED_PORTS $port"
                                            ;;
                                    esac
                                fi
                            fi
                        done
                        if [ -n "$BLOCKED_PORTS" ]; then
                            echo "  ⚠ WARNING: Ports still in use:$BLOCKED_PORTS"
                            echo "  Some services may fail to start. Stop the processes manually if needed."
                        fi
                        echo "✓ Clean install: all previous busibox resources removed"
                    "#)
                } else {
                    format!(r#"
                        set -e
                        PREFIX="{vault_prefix}"
                        # Check for running busibox compose projects and stop conflicting ones
                        # Normal install: only stop containers, preserve volumes/images/caches for faster rebuilds
                        PROJECTS=$(docker compose ls --format '{{{{.Name}}}}' 2>/dev/null | grep -i busibox || true)
                        if [ -n "$PROJECTS" ]; then
                            echo "  Found compose projects: $PROJECTS"
                            for project in $PROJECTS; do
                                if [ "$project" = "${{PREFIX}}-busibox" ]; then
                                    echo "  Found current project: $project (will be updated)"
                                else
                                    echo "  Stopping conflicting project: $project"
                                    docker compose -p "$project" stop 2>&1 || true
                                fi
                            done
                        fi
                        # Check for port conflicts from other Docker containers
                        for port in 5432 6379 9000 19530 8010 4111 8002 8001 3000; do
                            HOLDER=$(docker ps --filter "publish=$port" --format '{{{{.Names}}}}' 2>/dev/null || true)
                            if [ -n "$HOLDER" ]; then
                                if ! echo "$HOLDER" | grep -q "^${{PREFIX}}-"; then
                                    echo "  ⚠ Port $port in use by: $HOLDER — stopping it"
                                    docker stop "$HOLDER" 2>/dev/null || true
                                fi
                            fi
                        done
                        # Check for non-Docker processes on critical ports
                        BLOCKED_PORTS=""
                        for port in 5432 6379 9000 19530 8010 4111 8002 8001 3000; do
                            HOLDER=$(lsof -i ":$port" -sTCP:LISTEN -t 2>/dev/null | head -1 || true)
                            if [ -n "$HOLDER" ]; then
                                PNAME=$(ps -p "$HOLDER" -o comm= 2>/dev/null || echo "unknown")
                                if ! echo "$PNAME" | grep -qi "docker\|com.docker\|vpnkit"; then
                                    echo "  ⚠ Port $port in use by non-Docker process: $PNAME (PID $HOLDER)"
                                    case "$PNAME" in
                                        postgres|postmaster)
                                            echo "    → Stopping local PostgreSQL..."
                                            if [ "$(uname)" = "Darwin" ]; then
                                                brew services stop postgresql 2>/dev/null || true
                                                brew services stop postgresql@14 2>/dev/null || true
                                                brew services stop postgresql@15 2>/dev/null || true
                                                brew services stop postgresql@16 2>/dev/null || true
                                            else
                                                sudo systemctl stop postgresql 2>/dev/null || true
                                                sudo service postgresql stop 2>/dev/null || true
                                            fi
                                            sleep 1
                                            if lsof -i ":$port" -sTCP:LISTEN -t &>/dev/null; then
                                                BLOCKED_PORTS="$BLOCKED_PORTS $port"
                                            else
                                                echo "    ✓ PostgreSQL stopped"
                                            fi
                                            ;;
                                        redis-server|redis)
                                            echo "    → Stopping local Redis..."
                                            if [ "$(uname)" = "Darwin" ]; then
                                                brew services stop redis 2>/dev/null || true
                                            else
                                                sudo systemctl stop redis 2>/dev/null || sudo systemctl stop redis-server 2>/dev/null || true
                                            fi
                                            sleep 1
                                            if lsof -i ":$port" -sTCP:LISTEN -t &>/dev/null; then
                                                BLOCKED_PORTS="$BLOCKED_PORTS $port"
                                            else
                                                echo "    ✓ Redis stopped"
                                            fi
                                            ;;
                                        *)
                                            BLOCKED_PORTS="$BLOCKED_PORTS $port"
                                            ;;
                                    esac
                                fi
                            fi
                        done
                        if [ -n "$BLOCKED_PORTS" ]; then
                            echo "  ⚠ WARNING: Ports still in use:$BLOCKED_PORTS"
                            echo "  Some services may fail to start. Stop the processes manually if needed."
                        fi
                        echo "✓ Docker environment ready"
                    "#)
                };

                let result: color_eyre::Result<(i32, String)> = if is_remote {
                    if let Some((ref host, ref user, ref key)) = ssh_details {
                        let ssh = crate::modules::ssh::SshConnection::new(
                            profile_host.as_deref().unwrap_or(host),
                            user,
                            key,
                        );
                        let full_cmd = format!(
                            "bash -c {}",
                            shell_escape(&cleanup_script)
                        );
                        let mut args: Vec<String> = vec![
                            "-o".into(), "BatchMode=yes".into(),
                            "-o".into(), "StrictHostKeyChecking=accept-new".into(),
                            "-o".into(), "ConnectTimeout=10".into(),
                        ];
                        let key_expanded = crate::modules::ssh::shellexpand_path(key);
                        if !key_expanded.is_empty()
                            && std::path::Path::new(&key_expanded).exists()
                        {
                            args.push("-i".into());
                            args.push(key_expanded);
                        }
                        args.push(ssh.ssh_target());
                        args.push(full_cmd);
                        match std::process::Command::new("ssh").args(&args).output() {
                            Ok(output) => {
                                let exit_code = output.status.code().unwrap_or(1);
                                let combined = format!(
                                    "{}{}",
                                    String::from_utf8_lossy(&output.stdout),
                                    String::from_utf8_lossy(&output.stderr)
                                );
                                Ok((exit_code, remote::strip_ansi(&combined)))
                            }
                            Err(e) => Err(color_eyre::eyre::eyre!("{e}")),
                        }
                    } else {
                        Err(color_eyre::eyre::eyre!("No SSH details"))
                    }
                } else {
                    match std::process::Command::new("bash")
                        .arg("-c")
                        .arg(&cleanup_script)
                        .output()
                    {
                        Ok(output) => {
                            let exit_code = output.status.code().unwrap_or(1);
                            let combined = format!(
                                "{}{}",
                                String::from_utf8_lossy(&output.stdout),
                                String::from_utf8_lossy(&output.stderr)
                            );
                            Ok((exit_code, remote::strip_ansi(&combined)))
                        }
                        Err(e) => Err(color_eyre::eyre::eyre!("{e}")),
                    }
                };

                match result {
                    Ok((code, output)) => {
                        for line in output.lines() {
                            let trimmed = line.trim();
                            if !trimmed.is_empty() {
                                let _ = tx.send(InstallUpdate::Log(format!("  {trimmed}")));
                            }
                        }
                        if code == 0 {
                            for svc in services {
                                let _ = tx.send(InstallUpdate::ServiceStatus {
                                    name: svc.clone(),
                                    status: InstallStatus::Healthy,
                                });
                            }
                            let _ = tx.send(InstallUpdate::Log(
                                "✓ Docker cleanup complete".into(),
                            ));
                        } else {
                            for svc in services {
                                let _ = tx.send(InstallUpdate::ServiceStatus {
                                    name: svc.clone(),
                                    status: InstallStatus::Failed(format!("exit code {code}")),
                                });
                            }
                            let _ = tx.send(InstallUpdate::Log(format!(
                                "WARNING: Docker cleanup had issues (exit code {code})"
                            )));
                            // Don't abort — cleanup failures are non-fatal
                        }
                    }
                    Err(e) => {
                        let _ = tx.send(InstallUpdate::Log(format!(
                            "WARNING: Docker cleanup skipped: {e}"
                        )));
                        for svc in services {
                            let _ = tx.send(InstallUpdate::ServiceStatus {
                                name: svc.clone(),
                                status: InstallStatus::Healthy, // Non-fatal, continue
                            });
                        }
                    }
                }
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

            // Map frontend_ref to env: None/"latest" -> main, else use value
            let ref_val = frontend_ref
                .as_deref()
                .filter(|r| !r.is_empty() && *r != "latest")
                .unwrap_or("main");
            let ref_exports = format!(
                "BUSIBOX_FRONTEND_GITHUB_REF={ref_val} \
                 MODEL_HOST_CACHE=$HOME/.cache \
                 HF_HOST_CACHE=$HOME/.cache/huggingface \
                 FASTEMBED_HOST_CACHE=$HOME/.cache/fastembed "
            );
            let make_args = format!("{ref_exports}install SERVICE={service_list}");

            // Use streaming functions so each line appears in the log immediately
            let tx_stream = tx.clone();
            let on_line = |line: &str| {
                let _ = tx_stream.send(InstallUpdate::Log(format!("  {line}")));
            };

            let result: color_eyre::Result<i32> = if is_remote {
                if let Some((ref host, ref user, ref key)) = ssh_details {
                    let ssh =
                        crate::modules::ssh::SshConnection::new(host, user, key);
                    if let Some(ref vp) = vault_password {
                        remote::exec_make_quiet_with_vault_streaming(
                            &ssh,
                            &remote_path,
                            &make_args,
                            vp,
                            on_line,
                        )
                    } else {
                        remote::exec_make_quiet_streaming(
                            &ssh,
                            &remote_path,
                            &make_args,
                            on_line,
                        )
                    }
                } else {
                    Err(color_eyre::eyre::eyre!("No SSH connection"))
                }
            } else if let Some(ref vp) = vault_password {
                remote::run_local_make_quiet_with_vault_streaming(
                    &repo_root,
                    &make_args,
                    vp,
                    on_line,
                )
            } else {
                remote::run_local_make_quiet_streaming(
                    &repo_root,
                    &make_args,
                    on_line,
                )
            };

            match result {
                Ok(0) => {
                    for svc in services {
                        let _ = tx.send(InstallUpdate::ServiceStatus {
                            name: svc.clone(),
                            status: InstallStatus::Healthy,
                        });
                    }
                    let _ = tx.send(InstallUpdate::Log(format!("✓ {stage_name} installed")));
                }
                Ok(code) => {
                    any_failed = true;
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
            let _ = tx.send(InstallUpdate::Log("[model-complete]".into()));
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
