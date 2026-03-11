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

/// Check if a GitHub repo is accessible without auth; returns true if private/inaccessible.
fn is_repo_private(owner_repo: &str) -> bool {
    let url = format!("https://api.github.com/repos/{owner_repo}");
    let output = std::process::Command::new("curl")
        .args(["-s", "-o", "/dev/null", "-w", "%{http_code}",
               "-H", "Accept: application/vnd.github.v3+json",
               &url])
        .output();
    match output {
        Ok(o) => {
            let code = String::from_utf8_lossy(&o.stdout).trim().to_string();
            code != "200"
        }
        Err(_) => true,
    }
}

/// Validate a GitHub token: check auth and repo access. Blocking.
fn validate_github_token_blocking(token: &str, repos: &[String]) -> Result<(), String> {
    // 1) Check token is valid (GET /user)
    let output = std::process::Command::new("curl")
        .args(["-s", "-w", "\n%{http_code}",
               "-H", &format!("Authorization: token {token}"),
               "-H", "Accept: application/vnd.github.v3+json",
               "https://api.github.com/user"])
        .output()
        .map_err(|e| format!("Failed to run curl: {e}"))?;
    let full = String::from_utf8_lossy(&output.stdout);
    let lines: Vec<&str> = full.trim().lines().collect();
    let http_code = lines.last().copied().unwrap_or("");
    if http_code != "200" {
        return Err(format!("Invalid token (HTTP {http_code}). Check that you pasted a valid GitHub Personal Access Token."));
    }

    // 2) Check repo access
    for repo in repos {
        let url = format!("https://api.github.com/repos/{repo}");
        let output = std::process::Command::new("curl")
            .args(["-s", "-o", "/dev/null", "-w", "%{http_code}",
                   "-H", &format!("Authorization: token {token}"),
                   "-H", "Accept: application/vnd.github.v3+json",
                   &url])
            .output()
            .map_err(|e| format!("Failed to check repo access: {e}"))?;
        let code = String::from_utf8_lossy(&output.stdout).trim().to_string();
        if code != "200" {
            return Err(format!("Token cannot access {repo} (HTTP {code}). Ensure the token has 'repo' scope."));
        }
    }

    Ok(())
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

fn get_bootstrap_stages(app: &App) -> Vec<(&'static str, &'static str, Vec<String>)> {
    use crate::modules::hardware::LlmBackend;

    let is_mlx = app
        .active_profile()
        .and_then(|(_, p)| p.hardware.as_ref())
        .map(|h| matches!(h.llm_backend, LlmBackend::Mlx))
        .unwrap_or(false);

    let mut stages = vec![
        ("Prerequisites", "Ansible & Dependencies", vec!["_prerequisites".into()]),
        ("Docker Cleanup", "Stop conflicting containers", vec!["_docker_cleanup".into()]),
        ("Database", "PostgreSQL", vec!["postgres".into()]),
        ("Authentication", "AuthZ API", vec!["authz".into()]),
        ("Deployment", "Deploy API", vec!["deploy".into()]),
    ];

    if is_mlx {
        stages.push(("MLX Host Agent", "Host agent for MLX control", vec!["_mlx_host_agent".into()]));
    }

    stages.push((
        "Portal",
        "Portal & Admin",
        vec!["core-apps".into()],
    ));

    stages
}

fn get_update_stages(app: &App) -> Vec<(&'static str, &'static str, Vec<String>)> {
    use crate::modules::hardware::LlmBackend;

    let is_mlx = app
        .active_profile()
        .and_then(|(_, p)| p.hardware.as_ref())
        .map(|h| matches!(h.llm_backend, LlmBackend::Mlx))
        .unwrap_or(false);

    let mut stages = vec![
        ("Prerequisites", "Ansible & Dependencies", vec!["_prerequisites".into()]),
        ("Database", "PostgreSQL", vec!["postgres".into()]),
        ("Infrastructure", "Redis, MinIO, Milvus, Neo4j", vec![
            "redis".into(), "minio".into(), "milvus".into(), "neo4j".into(),
        ]),
        ("Authentication", "AuthZ API", vec!["authz".into()]),
        ("Embedding", "Embedding API", vec!["embedding".into()]),
        ("Data", "Data API & Worker", vec!["data".into()]),
        ("Search", "Search API", vec!["search".into()]),
        ("Agent", "Agent API", vec!["agent".into()]),
        ("Deployment", "Deploy API", vec!["deploy".into()]),
        ("Bridge", "Bridge Service", vec!["bridge".into()]),
        ("Docs", "Docs API", vec!["docs".into()]),
        ("LLM Gateway", "LiteLLM", vec!["litellm".into()]),
    ];

    if is_mlx {
        stages.push(("MLX Host Agent", "Host agent for MLX control", vec!["_mlx_host_agent".into()]));
    }

    stages.push((
        "Frontend",
        "All Frontend Apps",
        vec!["core-apps".into()],
    ));
    stages.push(("Proxy", "Nginx Proxy", vec!["proxy".into()]));
    stages.push(("Validation", "Verify env secrets", vec!["_validate_env".into()]));

    stages
}

/// Returns the appropriate stages based on whether this is an update or bootstrap install.
fn get_stages(app: &App) -> Vec<(&'static str, &'static str, Vec<String>)> {
    if app.is_update {
        get_update_stages(app)
    } else {
        get_bootstrap_stages(app)
    }
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

    let title_text = if app.is_update { "Update All Services" } else { "Bootstrap Installation" };
    let title = Paragraph::new(title_text)
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
    let progress_text = if app.is_update { "Update" } else { "Installation" };
    let subtitle = if app.install_complete && any_failed {
        Paragraph::new(format!("{progress_text} finished with errors!"))
            .style(theme::error())
            .alignment(Alignment::Center)
    } else if app.install_complete {
        Paragraph::new(format!("{progress_text} complete!"))
            .style(theme::success())
            .alignment(Alignment::Center)
    } else {
        let active_text = if app.is_update { "Updating all services..." } else { "Installing core services..." };
        Paragraph::new(active_text)
            .style(theme::muted())
            .alignment(Alignment::Center)
    };
    f.render_widget(subtitle, chunks[1]);

    let tick = app.install_tick;
    let spinner_char = SPINNER[tick % SPINNER.len()];

    let mut lines: Vec<Line> = Vec::new();
    lines.push(Line::from(""));

    let stages = get_stages(app);
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

    if app.install_waiting_retry.is_some() && !app.install_prereq_hint.is_empty() {
        lines.push(Line::from(""));
        for hint_line in &app.install_prereq_hint {
            let style = if hint_line.starts_with("  ") && !hint_line.trim().is_empty()
                && !hint_line.contains("ERROR") && !hint_line.contains("Please")
                && !hint_line.contains("Then") {
                theme::highlight()
            } else if hint_line.contains("ERROR") {
                theme::error()
            } else {
                theme::normal()
            };
            lines.push(Line::from(Span::styled(format!("  {hint_line}"), style)));
        }
        lines.push(Line::from(""));
        lines.push(Line::from(Span::styled(
            "  Press Enter to retry after installing, or Esc to go back",
            theme::warning(),
        )));
    }

    if app.install_waiting_token.is_some() {
        lines.push(Line::from(""));
        for msg_line in app.install_token_message.lines() {
            let style = if msg_line.starts_with("  •") {
                theme::highlight()
            } else if msg_line.contains("https://") {
                theme::info()
            } else {
                theme::normal()
            };
            lines.push(Line::from(Span::styled(format!("  {msg_line}"), style)));
        }
        lines.push(Line::from(""));
        let masked = if app.install_token_input.is_empty() {
            "▎".to_string()
        } else {
            let len = app.install_token_input.len();
            if len <= 4 {
                format!("{}▎", "*".repeat(len))
            } else {
                format!("{}{}▎", &app.install_token_input[..4], "*".repeat(len - 4))
            }
        };
        lines.push(Line::from(vec![
            Span::styled("  Token: ", theme::normal()),
            Span::styled(masked, theme::highlight()),
        ]));
        if !app.install_token_error.is_empty() {
            let style = if app.install_token_error.contains("Validating") {
                theme::info()
            } else {
                theme::error()
            };
            lines.push(Line::from(Span::styled(format!("  {}", app.install_token_error), style)));
        }
        lines.push(Line::from(""));
        lines.push(Line::from(Span::styled(
            "  Paste token and press Enter to validate, or Esc to abort",
            theme::warning(),
        )));
    }

    if app.install_complete {
        lines.push(Line::from(""));
        if any_failed {
            lines.push(Line::from(Span::styled(
                "  Press r to retry or c to copy error details",
                theme::muted(),
            )));
        } else if let Some(url) = &app.install_portal_url {
            lines.push(Line::from(vec![
                Span::styled("  → ", theme::info()),
                Span::styled("Setup: ", theme::normal()),
                Span::styled(url.as_str(), theme::highlight()),
            ]));
            lines.push(Line::from(Span::styled(
                "  Press Enter to generate admin login credentials",
                theme::muted(),
            )));
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

    let help_text = if app.install_waiting_retry.is_some() {
        " Enter Retry  c Copy Log  l View logs  Esc Back"
    } else if app.install_complete && any_failed {
        " r Retry  c Copy Error  l View logs  Esc Back"
    } else if app.install_complete {
        " Enter Continue Setup  c Copy Log  l View logs  Esc Back"
    } else {
        " l View logs  Esc Cancel"
    };
    let help_style = if app.install_waiting_retry.is_some() || (app.install_complete && any_failed) {
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

    // Handle waiting-for-token state (worker needs a GitHub token)
    if app.install_waiting_token.is_some() {
        match key.code {
            KeyCode::Enter => {
                let token = app.install_token_input.trim().to_string();
                if token.is_empty() {
                    app.install_token_error = "Token cannot be empty".to_string();
                    return;
                }
                app.install_token_error = "Validating token...".to_string();
                let repos_to_check = vec![
                    "jazzmind/busibox-frontend".to_string(),
                ];
                let token_clone = token.clone();
                let valid = std::thread::spawn(move || {
                    validate_github_token_blocking(&token_clone, &repos_to_check)
                }).join().unwrap_or(Err("Thread panicked".to_string()));

                match valid {
                    Ok(()) => {
                        if let Some(home) = dirs::home_dir() {
                            let path = home.join(".gittoken");
                            let _ = std::fs::write(&path, &token);
                        }
                        app.install_token_error.clear();
                        app.install_token_message.clear();
                        if let Some(resp) = app.install_waiting_token.take() {
                            let _ = resp.send(token);
                        }
                    }
                    Err(e) => {
                        app.install_token_error = e;
                    }
                }
            }
            KeyCode::Esc => {
                app.install_token_input.clear();
                app.install_token_error.clear();
                app.install_token_message.clear();
                if let Some(resp) = app.install_waiting_token.take() {
                    let _ = resp.send(String::new()); // empty = abort
                }
            }
            KeyCode::Backspace => {
                app.install_token_input.pop();
                app.install_token_error.clear();
            }
            KeyCode::Char(c) => {
                app.install_token_input.push(c);
                app.install_token_error.clear();
            }
            _ => {}
        }
        return;
    }

    // Handle waiting-for-retry state (worker is blocked waiting for user)
    if app.install_waiting_retry.is_some() {
        match key.code {
            KeyCode::Enter => {
                if let Some(resp) = app.install_waiting_retry.take() {
                    app.install_prereq_hint.clear();
                    let _ = resp.send(true);
                }
            }
            KeyCode::Esc => {
                if let Some(resp) = app.install_waiting_retry.take() {
                    app.install_prereq_hint.clear();
                    let _ = resp.send(false);
                }
            }
            KeyCode::Char('l') => {
                app.install_log_visible = true;
                app.install_log_scroll = app.install_log.len().saturating_sub(1);
                app.install_log_autoscroll = true;
            }
            KeyCode::Char('c') => {
                let log_text = app.install_log.join("\n");
                let _ = copy_to_clipboard(&log_text);
                app.set_message("Log copied to clipboard", MessageKind::Info);
            }
            _ => {}
        }
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
        KeyCode::Char('c') => {
            if app.install_complete {
                let any_failed = app.install_services.iter().any(|s| matches!(s.status, InstallStatus::Failed(_)));
                if any_failed {
                    let mut error_text = String::from("=== Busibox Install Error ===\n\n");
                    for svc in &app.install_services {
                        if let InstallStatus::Failed(ref e) = svc.status {
                            error_text.push_str(&format!("{}: {}\n", svc.name, e));
                        }
                    }
                    error_text.push_str("\n=== Install Log (last 50 lines) ===\n\n");
                    let start = app.install_log.len().saturating_sub(50);
                    for line in &app.install_log[start..] {
                        error_text.push_str(line);
                        error_text.push('\n');
                    }
                    let _ = copy_to_clipboard(&error_text);
                    app.set_message("Error details copied to clipboard", MessageKind::Info);
                } else {
                    let log_text = app.install_log.join("\n");
                    let _ = copy_to_clipboard(&log_text);
                    app.set_message("Log copied to clipboard", MessageKind::Info);
                }
            }
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
                let any_failed = app.install_services.iter().any(|s| matches!(s.status, InstallStatus::Failed(_)));
                if !any_failed {
                    app.admin_login_loading = true;
                    app.admin_login_magic_link = None;
                    app.admin_login_totp_code = None;
                    app.admin_login_verify_url = None;
                    app.admin_login_error = None;
                    app.pending_admin_login = true;
                    app.screen = Screen::AdminLogin;
                }
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

    let stages = get_stages(app);
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
    let is_update = app.is_update;
    // Derive vault prefix from environment (same logic as service-deploy.sh get_container_prefix)
    let vault_prefix: String = app
        .active_profile()
        .map(|(_, p)| env_to_prefix(&p.environment))
        .unwrap_or_else(|| "dev".into());
    let profile_environment: String = app
        .active_profile()
        .map(|(_, p)| p.environment.clone())
        .unwrap_or_else(|| "development".into());
    let profile_backend: String = app
        .active_profile()
        .map(|(_, p)| p.backend.clone())
        .unwrap_or_else(|| "docker".into());
    let profile_docker_runtime: String = app
        .active_profile()
        .map(|(_, p)| p.effective_docker_runtime().to_string())
        .unwrap_or_else(|| "auto".into());

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
    let allowed_email_domains: Option<String> = app
        .active_profile()
        .and_then(|(_, p)| p.allowed_email_domains.clone());
    let frontend_ref: Option<String> = app
        .active_profile()
        .and_then(|(_, p)| p.frontend_ref.clone());
    let site_domain: String = app
        .active_profile()
        .and_then(|(_, p)| p.site_domain.clone())
        .filter(|d| !d.trim().is_empty())
        .unwrap_or_else(|| "localhost".to_string());
    let ssl_cert_name: Option<String> = app
        .active_profile()
        .and_then(|(_, p)| p.ssl_cert_name.clone())
        .filter(|c| !c.trim().is_empty());
    let profile_model_tier: Option<String> = app
        .active_profile()
        .and_then(|(_, p)| p.effective_model_tier().map(|t| t.name().to_string()));
    let profile_network_base_octets: Option<String> = app
        .active_profile()
        .and_then(|(_, p)| p.network_base_octets.clone())
        .filter(|v| !v.trim().is_empty());
    let profile_llm_backend: Option<String> = app
        .active_profile()
        .and_then(|(_, p)| p.hardware.as_ref().map(|h| match h.llm_backend {
            crate::modules::hardware::LlmBackend::Mlx => "mlx".to_string(),
            crate::modules::hardware::LlmBackend::Vllm => "vllm".to_string(),
            crate::modules::hardware::LlmBackend::Cloud => "cloud".to_string(),
        }));

    // Read GitHub token: profile first, then ~/.gittoken fallback
    let github_token: Option<String> = app
        .active_profile()
        .and_then(|(_, p)| p.github_token.clone())
        .or_else(|| {
            dirs::home_dir()
                .map(|h| h.join(".gittoken"))
                .and_then(|p| std::fs::read_to_string(&p).ok())
                .map(|t| t.trim().to_string())
                .filter(|t| !t.is_empty())
        });

    // Check which repos are private and will need a token
    let private_repos: Vec<String> = vec![
        "jazzmind/busibox-frontend",
    ].into_iter()
        .filter(|r| is_repo_private(r))
        .map(|r| r.to_string())
        .collect();

    let needs_token = !private_repos.is_empty() && github_token.is_none();

    let stages: Vec<(String, String, Vec<String>)> = get_stages(app)
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

        if let Err(e) = prepare_ssl_inputs(&repo_root, &site_domain, ssl_cert_name.as_deref()) {
            let _ = tx.send(InstallUpdate::Log(format!(
                "WARNING: SSL certificate preparation skipped: {e}"
            )));
        }

        // If private repos were detected and we have no token, prompt for one
        let mut github_token = github_token;
        if needs_token {
            let _ = tx.send(InstallUpdate::Log(
                "Private GitHub repos detected — a Personal Access Token is required.".into()
            ));
            let repos_list = private_repos.iter()
                .map(|r| format!("  • {r}"))
                .collect::<Vec<_>>()
                .join("\n");
            let message = format!(
                "The following repos are private:\n{repos_list}\n\n\
                Create a token at https://github.com/settings/tokens\n\
                with 'repo' scope, then paste it below."
            );
            let (resp_tx, resp_rx) = std::sync::mpsc::channel::<String>();
            let _ = tx.send(InstallUpdate::NeedGitHubToken { message, response: resp_tx });
            match resp_rx.recv() {
                Ok(token) if !token.is_empty() => {
                    let _ = tx.send(InstallUpdate::Log("✓ GitHub token validated and saved".into()));
                    github_token = Some(token);
                }
                _ => {
                    let _ = tx.send(InstallUpdate::Log("ERROR: GitHub token is required for private repos".into()));
                    let _ = tx.send(InstallUpdate::Complete { portal_url: None });
                    return;
                }
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

        // Check if ansible-vault is available on the target before vault operations.
        // On a fresh machine, prerequisites haven't installed Ansible yet.
        let ansible_vault_available = if is_remote {
            if let Some((ref host, ref user, ref key)) = ssh_details {
                let ssh = crate::modules::ssh::SshConnection::new(
                    profile_host.as_deref().unwrap_or(host),
                    user,
                    key,
                );
                let check = format!("cd {} && command -v ansible-vault >/dev/null 2>&1 && echo FOUND || echo MISSING", remote_path);
                ssh.run(&check).map(|o| o.trim().contains("FOUND")).unwrap_or(false)
            } else {
                false
            }
        } else {
            std::process::Command::new("which")
                .arg("ansible-vault")
                .stdout(std::process::Stdio::null())
                .stderr(std::process::Stdio::null())
                .status()
                .map(|s| s.success())
                .unwrap_or(false)
        };

        let mut vault_setup_done = false;

        if !ansible_vault_available && vault_password.is_some() {
            let _ = tx.send(InstallUpdate::Log(
                "Deferring vault setup — ansible-vault not yet installed (will be installed by prerequisites)".into(),
            ));
        }

        // Verify vault password matches the vault file on the remote (or local)
        if ansible_vault_available {
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
            let allowed_domains_sed = allowed_email_domains
                .as_deref()
                .map(|domains| format!("sed -i.bak \"s/CHANGE_ME_ALLOWED_EMAIL_DOMAINS/{domains}/g\" \"$VAULT_FILE\" && rm -f \"${{VAULT_FILE}}.bak\"\n"))
                .unwrap_or_else(|| "sed -i.bak \"s/CHANGE_ME_ALLOWED_EMAIL_DOMAINS//g\" \"$VAULT_FILE\" && rm -f \"${VAULT_FILE}.bak\"\n".to_string());

            // Replace GitHub token placeholder in vault
            // Try: token from local ~/.gittoken (baked in), then remote ~/.gittoken
            let github_token_sed = match github_token.as_deref() {
                Some(t) => format!(
                    "sed -i.bak \"s/CHANGE_ME_GITHUB_PERSONAL_ACCESS_TOKEN/{t}/g\" \"$VAULT_FILE\" && rm -f \"${{VAULT_FILE}}.bak\"\n"
                ),
                None => concat!(
                    "if [ -f \"$HOME/.gittoken\" ]; then\n",
                    "    _GITTOKEN=$(cat \"$HOME/.gittoken\" | tr -d '[:space:]')\n",
                    "    if [ -n \"$_GITTOKEN\" ]; then\n",
                    "        sed -i.bak \"s/CHANGE_ME_GITHUB_PERSONAL_ACCESS_TOKEN/$_GITTOKEN/g\" \"$VAULT_FILE\" && rm -f \"${VAULT_FILE}.bak\"\n",
                    "    fi\n",
                    "fi\n",
                ).to_string(),
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
{allowed_domains_sed}
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
                allowed_domains_sed = allowed_domains_sed,
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

            // Debug: dump vault contents to install log so we can verify secrets
            let dump_script = format!(
                r#"set -euo pipefail
VAULT_FILE="{vault_rel}"
VPF="$ANSIBLE_VAULT_PASSWORD_FILE"
if [ -f "$VAULT_FILE" ]; then
    FIRST_LINE=$(head -1 "$VAULT_FILE")
    if [[ "$FIRST_LINE" == *'$ANSIBLE_VAULT'* ]]; then
        echo "=== VAULT CONTENTS (decrypted) ==="
        ansible-vault view "$VAULT_FILE" --vault-password-file="$VPF" 2>&1 | grep -E '(password|token|key|secret|email|domain)' | sed 's/^\s*/  /'
        echo "=== END VAULT ==="
    else
        echo "=== VAULT CONTENTS (plaintext) ==="
        grep -E '(password|token|key|secret|email|domain)' "$VAULT_FILE" | sed 's/^\s*/  /'
        echo "=== END VAULT ==="
    fi
else
    echo "WARNING: Vault file not found at $VAULT_FILE"
fi
"#,
                vault_rel = vault_rel,
            );
            let dump_result: color_eyre::Result<(i32, String)> = if is_remote {
                if let Some((ref host, ref user, ref key)) = ssh_details {
                    let ssh = crate::modules::ssh::SshConnection::new(
                        profile_host.as_deref().unwrap_or(host),
                        user,
                        key,
                    );
                    remote::exec_remote_with_vault(&ssh, &remote_path, &dump_script, vp)
                } else {
                    Err(color_eyre::eyre::eyre!("No SSH connection"))
                }
            } else {
                let env_script = repo_root.join("scripts/lib/vault-pass-from-env.sh");
                match std::process::Command::new("bash")
                    .arg("-c")
                    .arg(&dump_script)
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
            match dump_result {
                Ok((_, output)) => {
                    for line in output.lines() {
                        let _ = tx.send(InstallUpdate::Log(format!("  {}", line)));
                    }
                }
                Err(e) => {
                    let _ = tx.send(InstallUpdate::Log(format!("  Vault dump failed: {e}")));
                }
            }
        }
        vault_setup_done = true;
        } // end if ansible_vault_available

        let mut model_download_handle: Option<std::thread::JoinHandle<i32>> = None;
        let mut any_failed = false;
        for (stage_name, _description, services) in &stages {
            // Skip stages where all services are already healthy
            let force_stage_run =
                clean_install && services.iter().any(|s| s.as_str() == "_docker_cleanup");
            let stage_services_to_deploy: Vec<String> = if force_stage_run {
                services.clone()
            } else {
                services
                    .iter()
                    .filter(|s| !already_healthy.contains(*s))
                    .cloned()
                    .collect()
            };
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

                    # On macOS, ensure Xcode Command Line Tools are installed (python3, git, clang)
                    if [ "$(uname)" = "Darwin" ] && ! xcode-select -p &>/dev/null; then
                        echo "Installing Xcode Command Line Tools..."
                        touch /tmp/.com.apple.dt.CommandLineTools.installondemand.in-progress
                        CLT_LABEL=$(softwareupdate -l 2>/dev/null | grep -o '.*Command Line Tools.*' | grep '^\*' | head -1 | sed 's/^\* Label: //')
                        if [ -z "$CLT_LABEL" ]; then
                            CLT_LABEL=$(softwareupdate -l 2>/dev/null | grep '.*Command Line.*' | head -1 | sed 's/^[ *]*//' | sed 's/^ *Label: //')
                        fi
                        if [ -n "$CLT_LABEL" ]; then
                            softwareupdate -i "$CLT_LABEL" --verbose 2>&1
                        else
                            echo "WARNING: Could not find Command Line Tools in software updates"
                            echo "You may need to run: xcode-select --install"
                        fi
                        rm -f /tmp/.com.apple.dt.CommandLineTools.installondemand.in-progress
                        if xcode-select -p &>/dev/null; then
                            echo "✓ Xcode Command Line Tools installed"
                        else
                            echo "WARNING: Xcode Command Line Tools may not be fully installed"
                        fi
                    fi

                    # On macOS, ensure Homebrew is available
                    if [ "$(uname)" = "Darwin" ]; then
                        if [ -f /opt/homebrew/bin/brew ]; then
                            eval "$(/opt/homebrew/bin/brew shellenv)"
                        elif [ -f /usr/local/bin/brew ]; then
                            eval "$(/usr/local/bin/brew shellenv)"
                        fi
                        if ! command -v brew &>/dev/null; then
                            echo ""
                            echo "ERROR: Homebrew is not installed."
                            echo ""
                            echo "Homebrew requires sudo to install. Please run this command on the target machine:"
                            echo ""
                            echo "  /bin/bash -c \"\$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)\""
                            echo ""
                            echo "Then retry the install."
                            exit 1
                        fi
                    fi

                    # Ensure Python 3.10+ is available (required by outlines/mlx-lm)
                    PYTHON3_MIN_MINOR=10
                    HAVE_MODERN_PYTHON=false
                    for candidate in python3.13 python3.12 python3.11 python3.10 python3; do
                        PY_PATH=$(command -v "$candidate" 2>/dev/null) || continue
                        PY_MINOR=$("$PY_PATH" -c 'import sys; print(sys.version_info.minor)' 2>/dev/null) || continue
                        if [ "$PY_MINOR" -ge "$PYTHON3_MIN_MINOR" ]; then
                            HAVE_MODERN_PYTHON=true
                            break
                        fi
                    done
                    if [ "$HAVE_MODERN_PYTHON" = false ]; then
                        echo "Python 3.10+ not found — installing via Homebrew..."
                        if command -v brew &>/dev/null; then
                            brew install --quiet python@3.12 2>&1 || true
                            eval "$(brew shellenv 2>/dev/null)" || true
                        elif command -v apt-get &>/dev/null; then
                            apt-get update -qq && apt-get install -y -qq python3.12 python3.12-venv 2>&1 || true
                        fi
                        # Verify
                        for candidate in python3.12 python3.13 python3.11 python3.10 python3; do
                            PY_PATH=$(command -v "$candidate" 2>/dev/null) || continue
                            PY_MINOR=$("$PY_PATH" -c 'import sys; print(sys.version_info.minor)' 2>/dev/null) || continue
                            if [ "$PY_MINOR" -ge "$PYTHON3_MIN_MINOR" ]; then
                                HAVE_MODERN_PYTHON=true
                                break
                            fi
                        done
                        if [ "$HAVE_MODERN_PYTHON" = true ]; then
                            echo "✓ Python: $($PY_PATH --version)"
                        else
                            echo "ERROR: Could not install Python 3.10+"
                            echo "Please install manually: brew install python@3.12"
                            exit 1
                        fi
                    else
                        echo "✓ Python: $($PY_PATH --version)"
                    fi

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
                    if [ "${BUSIBOX_TARGET_BACKEND:-docker}" = "proxmox" ]; then
                        echo "✓ Skipping Docker checks for proxmox backend"
                    elif [ "$(uname)" = "Darwin" ] && command -v brew &>/dev/null; then
                        # Install docker CLI + compose via Homebrew if not present
                        if ! command -v docker &>/dev/null; then
                            echo "Installing docker CLI via Homebrew..."
                            brew install --quiet docker docker-compose 2>&1 || true
                            BREW_PREFIX=$(brew --prefix 2>/dev/null || echo "/opt/homebrew")
                            export PATH="$BREW_PREFIX/bin:$PATH"
                        fi
                        # Symlink Homebrew docker CLI plugins so `docker compose` etc. work
                        BREW_PREFIX=$(brew --prefix 2>/dev/null || echo "/opt/homebrew")
                        BREW_PLUGINS="$BREW_PREFIX/lib/docker/cli-plugins"
                        if [ -d "$BREW_PLUGINS" ]; then
                            mkdir -p "$HOME/.docker/cli-plugins"
                            for p in "$BREW_PLUGINS"/docker-*; do
                                [ -f "$p" ] && ln -sf "$p" "$HOME/.docker/cli-plugins/$(basename "$p")" 2>/dev/null || true
                            done
                        fi
                        if ! command -v docker &>/dev/null; then
                            echo "ERROR: docker CLI could not be installed"
                            exit 1
                        fi
                        DOCKER_RT="${BUSIBOX_DOCKER_RUNTIME:-auto}"

                        # Helper: ensure Docker CLI can reach the running daemon.
                        # After uninstalling Docker Desktop the CLI context may still
                        # reference "desktop-linux" which no longer exists. This function
                        # tries docker context use, then falls back to DOCKER_HOST.
                        _ensure_docker_reachable() {
                            if docker info &>/dev/null; then return 0; fi
                            # Try switching to colima context
                            if docker context use colima &>/dev/null 2>&1 && docker info &>/dev/null; then return 0; fi
                            # Try switching to default context
                            if docker context use default &>/dev/null 2>&1 && docker info &>/dev/null; then return 0; fi
                            # Try Colima socket directly
                            for sock in "$HOME/.colima/default/docker.sock" "$HOME/.colima/docker.sock"; do
                                if [ -S "$sock" ]; then
                                    export DOCKER_HOST="unix://$sock"
                                    if docker info &>/dev/null; then return 0; fi
                                fi
                            done
                            unset DOCKER_HOST
                            return 1
                        }

                        if ! _ensure_docker_reachable; then
                            # Try Docker Desktop if runtime allows it
                            if [ "$DOCKER_RT" != "colima" ]; then
                                if [ -d "/Applications/Docker.app" ] || [ -d "$HOME/Applications/Docker.app" ]; then
                                    echo "Docker daemon not running — starting Docker Desktop..."
                                    open -a Docker 2>/dev/null || open -a "$HOME/Applications/Docker.app" 2>/dev/null || true
                                    WAITED=0
                                    while ! _ensure_docker_reachable; do
                                        sleep 2
                                        WAITED=$((WAITED + 2))
                                        if [ $WAITED -ge 60 ]; then
                                            if [ "$DOCKER_RT" = "docker-desktop" ]; then
                                                echo "ERROR: Docker Desktop did not start within 60s (docker_runtime=docker-desktop)"
                                                exit 1
                                            fi
                                            echo "WARNING: Docker Desktop did not start within 60s, falling back to Colima..."
                                            break
                                        fi
                                        if [ $((WAITED % 10)) -eq 0 ]; then
                                            echo "  Still waiting... (${WAITED}s)"
                                        fi
                                    done
                                    if _ensure_docker_reachable; then
                                        echo "✓ Docker daemon started via Docker Desktop"
                                    fi
                                fi
                            fi
                        fi
                        # If no daemon, try Colima (unless runtime is docker-desktop only)
                        if ! _ensure_docker_reachable; then
                            if [ "$DOCKER_RT" = "docker-desktop" ]; then
                                echo "ERROR: Docker Desktop not running and docker_runtime=docker-desktop"
                                exit 1
                            fi
                            if ! command -v colima &>/dev/null; then
                                echo "Installing Colima (lightweight Docker runtime)..."
                                brew install --quiet colima 2>&1 || true
                            fi
                            if command -v colima &>/dev/null; then
                                TOTAL_CPUS=$(sysctl -n hw.ncpu 2>/dev/null || echo 4)
                                TOTAL_MEM_BYTES=$(sysctl -n hw.memsize 2>/dev/null || echo 8589934592)
                                COLIMA_CPUS=$(( TOTAL_CPUS / 4 ))
                                [ "$COLIMA_CPUS" -lt 2 ] && COLIMA_CPUS=2
                                [ "$COLIMA_CPUS" -gt 8 ] && COLIMA_CPUS=8
                                COLIMA_MEM=$(( TOTAL_MEM_BYTES / 1073741824 / 2 ))
                                [ "$COLIMA_MEM" -lt 8 ] && COLIMA_MEM=8
                                [ "$COLIMA_MEM" -gt 16 ] && COLIMA_MEM=16
                                echo "Starting Colima with ${COLIMA_CPUS} CPUs, ${COLIMA_MEM}GB RAM..."
                                colima start --cpu "$COLIMA_CPUS" --memory "$COLIMA_MEM" 2>&1
                                if _ensure_docker_reachable; then
                                    echo "✓ Docker daemon started via Colima"
                                else
                                    echo "ERROR: Colima started but Docker daemon is not reachable"
                                    echo "  Try: docker context use colima"
                                    exit 1
                                fi
                            else
                                echo "ERROR: Could not install Colima"
                                echo ""
                                echo "Please install a Docker runtime (Docker Desktop, Colima, or OrbStack) and retry."
                                exit 1
                            fi
                        else
                            echo "✓ Docker daemon running"
                        fi
                        # If Colima is the runtime, ensure it has enough resources
                        if command -v colima &>/dev/null && colima status &>/dev/null; then
                            TOTAL_CPUS=$(sysctl -n hw.ncpu 2>/dev/null || echo 4)
                            TOTAL_MEM_BYTES=$(sysctl -n hw.memsize 2>/dev/null || echo 8589934592)
                            COLIMA_CPUS=$(( TOTAL_CPUS / 4 ))
                            [ "$COLIMA_CPUS" -lt 2 ] && COLIMA_CPUS=2
                            [ "$COLIMA_CPUS" -gt 8 ] && COLIMA_CPUS=8
                            COLIMA_MEM=$(( TOTAL_MEM_BYTES / 1073741824 / 2 ))
                            [ "$COLIMA_MEM" -lt 8 ] && COLIMA_MEM=8
                            [ "$COLIMA_MEM" -gt 16 ] && COLIMA_MEM=16
                            CURRENT_CPUS=$(colima list -j 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin)[0].get('cpu',2))" 2>/dev/null || echo 2)
                            CURRENT_MEM=$(colima list -j 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin)[0].get('memory',2))" 2>/dev/null || echo 2)
                            if [ "$CURRENT_CPUS" -lt "$COLIMA_CPUS" ] || [ "$CURRENT_MEM" -lt "$COLIMA_MEM" ]; then
                                echo "⚠ Colima has ${CURRENT_CPUS} CPUs, ${CURRENT_MEM}GB RAM — need ${COLIMA_CPUS} CPUs, ${COLIMA_MEM}GB RAM"
                                echo "  Restarting Colima with more resources (containers will restart)..."
                                colima stop 2>&1
                                colima start --cpu "$COLIMA_CPUS" --memory "$COLIMA_MEM" 2>&1
                                _ensure_docker_reachable
                                echo "✓ Colima restarted with ${COLIMA_CPUS} CPUs, ${COLIMA_MEM}GB RAM"
                            else
                                echo "✓ Colima: ${CURRENT_CPUS} CPUs, ${CURRENT_MEM}GB RAM"
                            fi
                        fi
                        # Display Docker runtime resources
                        DOCKER_CPUS=$(docker info --format '{{.NCPU}}' 2>/dev/null || echo '?')
                        DOCKER_MEM_BYTES=$(docker info --format '{{.MemTotal}}' 2>/dev/null || echo '0')
                        if [ "$DOCKER_MEM_BYTES" != "0" ] && [ "$DOCKER_MEM_BYTES" != "?" ]; then
                            DOCKER_MEM_GB=$(python3 -c "print(f'{int(${DOCKER_MEM_BYTES}) / 1073741824:.1f}')" 2>/dev/null || echo '?')
                        else
                            DOCKER_MEM_GB="?"
                        fi
                        echo "✓ Docker runtime: ${DOCKER_CPUS} CPUs, ${DOCKER_MEM_GB}GB RAM available"
                    else
                        # Linux
                        if ! command -v docker &>/dev/null; then
                            echo "ERROR: Docker is not installed."
                            echo ""
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
                    if [ "${BUSIBOX_TARGET_BACKEND:-docker}" != "proxmox" ]; then
                        echo "✓ docker: $(docker --version)"

                        # Check docker compose
                        if docker compose version &>/dev/null; then
                            echo "✓ docker compose: $(docker compose version --short 2>/dev/null || docker compose version)"
                        elif command -v docker-compose &>/dev/null; then
                            echo "✓ docker-compose: $(docker-compose --version)"
                        else
                            echo "ERROR: docker compose is not available."
                            echo ""
                            echo "Please install Docker Compose v2."
                            exit 1
                        fi
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

                let prereq_ok = loop {
                    let result: color_eyre::Result<(i32, String)> = if is_remote {
                        if let Some((ref host, ref user, ref key)) = ssh_details {
                            let ssh = crate::modules::ssh::SshConnection::new(
                                profile_host.as_deref().unwrap_or(host),
                                user,
                                key,
                            );
                            let full_cmd = format!(
                                "export BUSIBOX_TARGET_BACKEND={}; export BUSIBOX_DOCKER_RUNTIME={}; {} bash -c {}",
                                shell_escape(&profile_backend),
                                shell_escape(&profile_docker_runtime),
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
                            .env("BUSIBOX_TARGET_BACKEND", &profile_backend)
                            .env("BUSIBOX_DOCKER_RUNTIME", &profile_docker_runtime)
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
                            break true;
                        }
                        Ok((_code, output)) => {
                            // Extract user-facing hint lines (from ERROR: onward)
                            let mut hint: Vec<String> = Vec::new();
                            let mut capturing = false;
                            for line in output.lines() {
                                let trimmed = line.trim();
                                if !trimmed.is_empty() {
                                    let _ = tx.send(InstallUpdate::Log(format!("  {trimmed}")));
                                }
                                if trimmed.starts_with("ERROR:") {
                                    capturing = true;
                                }
                                if capturing {
                                    hint.push(trimmed.to_string());
                                }
                            }
                            if hint.is_empty() {
                                hint.push("Prerequisite check failed.".into());
                                hint.push("Check logs for details.".into());
                            }
                            for svc in services {
                                let _ = tx.send(InstallUpdate::ServiceStatus {
                                    name: svc.clone(),
                                    status: InstallStatus::Failed("missing prerequisites".into()),
                                });
                            }
                            let (resp_tx, resp_rx) = std::sync::mpsc::channel::<bool>();
                            let _ = tx.send(InstallUpdate::WaitForRetry { hint, response: resp_tx });
                            match resp_rx.recv() {
                                Ok(true) => {
                                    let _ = tx.send(InstallUpdate::Log(
                                        "Retrying prerequisites...".into(),
                                    ));
                                    for svc in services {
                                        let _ = tx.send(InstallUpdate::ServiceStatus {
                                            name: svc.clone(),
                                            status: InstallStatus::Deploying,
                                        });
                                    }
                                    continue;
                                }
                                _ => break false,
                            }
                        }
                        Err(e) => {
                            let _ = tx.send(InstallUpdate::Log(format!(
                                "ERROR: Prerequisites: {e}"
                            )));
                            let hint = vec![format!("ERROR: {e}")];
                            for svc in services {
                                let _ = tx.send(InstallUpdate::ServiceStatus {
                                    name: svc.clone(),
                                    status: InstallStatus::Failed(e.to_string()),
                                });
                            }
                            let (resp_tx, resp_rx) = std::sync::mpsc::channel::<bool>();
                            let _ = tx.send(InstallUpdate::WaitForRetry { hint, response: resp_tx });
                            match resp_rx.recv() {
                                Ok(true) => {
                                    let _ = tx.send(InstallUpdate::Log(
                                        "Retrying prerequisites...".into(),
                                    ));
                                    for svc in services {
                                        let _ = tx.send(InstallUpdate::ServiceStatus {
                                            name: svc.clone(),
                                            status: InstallStatus::Deploying,
                                        });
                                    }
                                    continue;
                                }
                                _ => break false,
                            }
                        }
                    }
                };
                if !prereq_ok {
                    any_failed = true;
                }

                if any_failed {
                    break;
                }

                // Run deferred vault setup now that ansible-vault is available
                if !vault_setup_done {
                    if let Some(ref vp) = vault_password {
                        let _ = tx.send(InstallUpdate::Log(
                            "Running deferred vault setup (ansible-vault now available)...".into(),
                        ));

                        let vault_rel = format!(
                            "provision/ansible/roles/secrets/vars/vault.{vault_prefix}.yml"
                        );
                        let example_rel = format!(
                            "provision/ansible/roles/secrets/vars/vault.example.yml"
                        );

                        // Step 1: Create vault from example if it doesn't exist
                        let create_ok = if is_remote {
                            if let Some((ref host, ref user, ref key)) = ssh_details {
                                let ssh = crate::modules::ssh::SshConnection::new(
                                    profile_host.as_deref().unwrap_or(host), user, key,
                                );
                                let create_script = format!(
                                    "if [ ! -f {vault_rel} ]; then \
                                       cp {example_rel} {vault_rel} && \
                                       ansible-vault encrypt {vault_rel} --vault-password-file=\"$ANSIBLE_VAULT_PASSWORD_FILE\" && \
                                       echo CREATED; \
                                     else echo EXISTS; fi"
                                );
                                match remote::exec_remote_with_vault(&ssh, &remote_path, &create_script, vp) {
                                    Ok((0, output)) => {
                                        if output.contains("CREATED") {
                                            let _ = tx.send(InstallUpdate::Log("  ✓ Vault file created and encrypted".into()));
                                        } else {
                                            let _ = tx.send(InstallUpdate::Log("  ✓ Vault file already exists".into()));
                                        }
                                        true
                                    }
                                    Ok((_, output)) => {
                                        for line in output.lines() {
                                            let _ = tx.send(InstallUpdate::Log(format!("  {line}")));
                                        }
                                        let _ = tx.send(InstallUpdate::Log("ERROR: Failed to create vault file".into()));
                                        false
                                    }
                                    Err(e) => {
                                        let _ = tx.send(InstallUpdate::Log(format!("ERROR: {e}")));
                                        false
                                    }
                                }
                            } else { false }
                        } else {
                            let vault_path = repo_root.join(&vault_rel);
                            let example_path = repo_root.join(&example_rel);
                            if !vault_path.exists() && example_path.exists() {
                                let _ = tx.send(InstallUpdate::Log("  Creating vault file from example...".into()));
                                if let Err(e) = std::fs::copy(&example_path, &vault_path) {
                                    let _ = tx.send(InstallUpdate::Log(format!("ERROR: {e}")));
                                    false
                                } else {
                                    match encrypt_vault_local(&vault_path, vp) {
                                        Ok(true) => {
                                            let _ = tx.send(InstallUpdate::Log("  ✓ Vault file created and encrypted".into()));
                                            true
                                        }
                                        _ => {
                                            let _ = tx.send(InstallUpdate::Log("ERROR: Failed to encrypt vault".into()));
                                            false
                                        }
                                    }
                                }
                            } else {
                                true
                            }
                        };

                        if !create_ok {
                            any_failed = true;
                            break;
                        }

                        // Step 2: Generate secrets (same script as the main vault setup)
                        let _ = tx.send(InstallUpdate::Log("Generating vault secrets...".into()));

                        let admin_email_sed = admin_email
                            .as_deref()
                            .filter(|e| !e.is_empty())
                            .map(|email| format!("sed -i.bak \"s/CHANGE_ME_ADMIN_EMAILS/{email}/g\" \"$VAULT_FILE\" && rm -f \"${{VAULT_FILE}}.bak\"\n"))
                            .unwrap_or_default();
                        let allowed_domains_sed = allowed_email_domains
                            .as_deref()
                            .map(|domains| format!("sed -i.bak \"s/CHANGE_ME_ALLOWED_EMAIL_DOMAINS/{domains}/g\" \"$VAULT_FILE\" && rm -f \"${{VAULT_FILE}}.bak\"\n"))
                            .unwrap_or_else(|| "sed -i.bak \"s/CHANGE_ME_ALLOWED_EMAIL_DOMAINS//g\" \"$VAULT_FILE\" && rm -f \"${VAULT_FILE}.bak\"\n".to_string());

                        let github_token_sed = match github_token.as_deref() {
                            Some(t) => format!(
                                "sed -i.bak \"s/CHANGE_ME_GITHUB_PERSONAL_ACCESS_TOKEN/{t}/g\" \"$VAULT_FILE\" && rm -f \"${{VAULT_FILE}}.bak\"\n"
                            ),
                            None => concat!(
                                "if [ -f \"$HOME/.gittoken\" ]; then\n",
                                "    _GITTOKEN=$(cat \"$HOME/.gittoken\" | tr -d '[:space:]')\n",
                                "    if [ -n \"$_GITTOKEN\" ]; then\n",
                                "        sed -i.bak \"s/CHANGE_ME_GITHUB_PERSONAL_ACCESS_TOKEN/$_GITTOKEN/g\" \"$VAULT_FILE\" && rm -f \"${VAULT_FILE}.bak\"\n",
                                "    fi\n",
                                "fi\n",
                            ).to_string(),
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
{allowed_domains_sed}
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
                            allowed_domains_sed = allowed_domains_sed,
                            github_token_sed = github_token_sed,
                        );

                        let gen_result: color_eyre::Result<(i32, String)> = if is_remote {
                            if let Some((ref host, ref user, ref key)) = ssh_details {
                                let ssh = crate::modules::ssh::SshConnection::new(
                                    profile_host.as_deref().unwrap_or(host), user, key,
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

                        // Debug: dump vault contents
                        let dump_script = format!(
                            r#"set -euo pipefail
VAULT_FILE="{vault_rel}"
VPF="$ANSIBLE_VAULT_PASSWORD_FILE"
if [ -f "$VAULT_FILE" ]; then
    FIRST_LINE=$(head -1 "$VAULT_FILE")
    if [[ "$FIRST_LINE" == *'$ANSIBLE_VAULT'* ]]; then
        echo "=== VAULT CONTENTS (decrypted) ==="
        ansible-vault view "$VAULT_FILE" --vault-password-file="$VPF" 2>&1 | grep -E '(password|token|key|secret|email|domain)' | sed 's/^\s*/  /'
        echo "=== END VAULT ==="
    else
        echo "=== VAULT CONTENTS (plaintext) ==="
        grep -E '(password|token|key|secret|email|domain)' "$VAULT_FILE" | sed 's/^\s*/  /'
        echo "=== END VAULT ==="
    fi
else
    echo "WARNING: Vault file not found at $VAULT_FILE"
fi
"#,
                            vault_rel = vault_rel,
                        );
                        let dump_result: color_eyre::Result<(i32, String)> = if is_remote {
                            if let Some((ref host, ref user, ref key)) = ssh_details {
                                let ssh = crate::modules::ssh::SshConnection::new(
                                    profile_host.as_deref().unwrap_or(host), user, key,
                                );
                                remote::exec_remote_with_vault(&ssh, &remote_path, &dump_script, vp)
                            } else {
                                Err(color_eyre::eyre::eyre!("No SSH connection"))
                            }
                        } else {
                            let env_script = repo_root.join("scripts/lib/vault-pass-from-env.sh");
                            match std::process::Command::new("bash")
                                .arg("-c")
                                .arg(&dump_script)
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
                        match dump_result {
                            Ok((_, output)) => {
                                for line in output.lines() {
                                    let _ = tx.send(InstallUpdate::Log(format!("  {}", line)));
                                }
                            }
                            Err(e) => {
                                let _ = tx.send(InstallUpdate::Log(format!("  Vault dump failed: {e}")));
                            }
                        }

                        vault_setup_done = true;
                    }
                }

                // Start model download in background now that prerequisites are installed
                if model_download_handle.is_none() {
                    let _ = tx.send(InstallUpdate::Log(
                        "Starting model download in background...".into(),
                    ));
                    let dl_tx = tx.clone();
                    let dl_tier = profile_model_tier.clone();
                    let dl_backend = profile_llm_backend.clone();
                    let dl_prefix = vault_prefix.clone();
                    model_download_handle = if is_remote {
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
                        # Find and stop ALL busibox compose projects (with volumes)
                        for project in $(docker compose ls --format '{{{{.Name}}}}' 2>/dev/null | grep -i busibox || true); do
                            echo "  Stopping project: $project"
                            docker compose -p "$project" down -v --remove-orphans 2>&1 || true
                        done
                        # Also stop/remove ALL containers with busibox-related names
                        ALL_BUSIBOX=$(docker ps -a --format '{{{{.Names}}}}' 2>/dev/null | grep -E "^(dev|staging|prod|demo)-" || true)
                        if [ -n "$ALL_BUSIBOX" ]; then
                            echo "  Removing containers: $ALL_BUSIBOX"
                            echo "$ALL_BUSIBOX" | xargs docker rm -f 2>/dev/null || true
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
            } else if stage_services_to_deploy.len() == 1
                && stage_services_to_deploy.first().map(|s| s.as_str()) == Some("_mlx_host_agent")
            {
                let _ = tx.send(InstallUpdate::Log(
                    "Setting up MLX host agent...".into(),
                ));
                for svc in services {
                    let _ = tx.send(InstallUpdate::ServiceStatus {
                        name: svc.clone(),
                        status: InstallStatus::Deploying,
                    });
                }

                let setup_cmd = format!("BUSIBOX_ENV={profile_environment} LLM_BACKEND=mlx bash scripts/make/install.sh --mlx-host-setup");

                let result: color_eyre::Result<(i32, String)> = if is_remote {
                    if let Some((ref host, ref user, ref key)) = ssh_details {
                        let ssh = crate::modules::ssh::SshConnection::new(
                            profile_host.as_deref().unwrap_or(host),
                            user,
                            key,
                        );
                        let full_cmd = format!("cd {} && {}", remote_path, setup_cmd);
                        match ssh.run(&full_cmd) {
                            Ok(output) => {
                                Ok((0, remote::strip_ansi(&output)))
                            }
                            Err(e) => Err(color_eyre::eyre::eyre!("{e}")),
                        }
                    } else {
                        Err(color_eyre::eyre::eyre!("No SSH details"))
                    }
                } else {
                    match std::process::Command::new("bash")
                        .arg("-c")
                        .arg(setup_cmd)
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
                                "✓ MLX host agent setup complete".into(),
                            ));
                        } else {
                            any_failed = true;
                            for svc in services {
                                let _ = tx.send(InstallUpdate::ServiceStatus {
                                    name: svc.clone(),
                                    status: InstallStatus::Failed(format!("exit code {code}")),
                                });
                            }
                            let _ = tx.send(InstallUpdate::Log(format!(
                                "FAILED: MLX host agent setup (exit code {code})"
                            )));
                        }
                    }
                    Err(e) => {
                        any_failed = true;
                        let _ = tx.send(InstallUpdate::Log(format!(
                            "FAILED: MLX host agent setup: {e}"
                        )));
                        for svc in services {
                            let _ = tx.send(InstallUpdate::ServiceStatus {
                                name: svc.clone(),
                                status: InstallStatus::Failed(e.to_string()),
                            });
                        }
                    }
                }

                if any_failed {
                    break;
                }
                continue;
            } else if stage_services_to_deploy.len() == 1
                && stage_services_to_deploy.first().map(|s| s.as_str()) == Some("_validate_env")
            {
                let _ = tx.send(InstallUpdate::Log(
                    "Validating environment secrets...".into(),
                ));
                for svc in services {
                    let _ = tx.send(InstallUpdate::ServiceStatus {
                        name: svc.clone(),
                        status: InstallStatus::Deploying,
                    });
                }

                let make_args = "validate-env".to_string();
                let tx_stream = tx.clone();
                let on_line = |line: &str| {
                    let _ = tx_stream.send(InstallUpdate::Log(format!("  {line}")));
                };

                let result: color_eyre::Result<i32> = if is_remote {
                    if let Some((ref host, ref user, ref key)) = ssh_details {
                        let ssh = crate::modules::ssh::SshConnection::new(
                            profile_host.as_deref().unwrap_or(host),
                            user,
                            key,
                        );
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
                        let _ = tx.send(InstallUpdate::Log(
                            "✓ Environment secrets validated".into(),
                        ));
                    }
                    Ok(code) => {
                        for svc in services {
                            let _ = tx.send(InstallUpdate::ServiceStatus {
                                name: svc.clone(),
                                status: InstallStatus::Failed(format!("Mismatches found (exit code {code})")),
                            });
                        }
                        let _ = tx.send(InstallUpdate::Log(format!(
                            "⚠ Environment validation found issues (exit code {code})"
                        )));
                    }
                    Err(e) => {
                        for svc in services {
                            let _ = tx.send(InstallUpdate::ServiceStatus {
                                name: svc.clone(),
                                status: InstallStatus::Failed(e.to_string()),
                            });
                        }
                    }
                }
                continue;
            }

            // For remote Proxmox installs, generate model_config.yml on the remote host
            // before deploying LiteLLM, then pull it back locally so Ansible can use it.
            if is_remote && stage_services_to_deploy.iter().any(|s| s == "litellm") {
                let _ = tx.send(InstallUpdate::Log(
                    "Preparing LiteLLM model pipeline...".into(),
                ));

                let model_cfg_result: color_eyre::Result<()> = if let Some((ref host, ref user, ref key)) = ssh_details {
                    let effective_host = profile_host.as_deref().unwrap_or(host);
                    let ssh = crate::modules::ssh::SshConnection::new(effective_host, user, key);
                    let tx_model = tx.clone();
                    let on_model_line = |line: &str| {
                        let _ = tx_model.send(InstallUpdate::Log(format!("  [model-pipeline] {line}")));
                    };

                    // Phase 3 path: try deploy-api endpoints first.
                    // Falls back to script-based generation if token/service URL is unavailable.
                    let api_pipeline_cmd = r#"
                        set -e
                        API_BASE="${DEPLOY_API_URL:-http://deploy-api:8011}"
                        TOKEN="${DEPLOY_BOOTSTRAP_TOKEN:-${LITELLM_API_KEY:-}}"
                        if [ -z "$TOKEN" ]; then
                          echo "No DEPLOY_BOOTSTRAP_TOKEN/LITELLM_API_KEY found; skipping deploy-api pipeline"
                          exit 99
                        fi
                        echo "Calling deploy-api auto assignment..."
                        curl -fsS -X POST "${API_BASE}/api/v1/services/vllm/assignments/auto" \
                          -H "Authorization: Bearer ${TOKEN}" >/tmp/busibox-vllm-auto.json
                        echo "Calling deploy-api apply..."
                        curl -fsS -X POST "${API_BASE}/api/v1/services/vllm/apply" \
                          -H "Authorization: Bearer ${TOKEN}" >/tmp/busibox-vllm-apply.sse
                        echo "Deploy-api model pipeline complete"
                    "#;

                    match remote::exec_remote_streaming(&ssh, &remote_path, api_pipeline_cmd, on_model_line) {
                        Ok(0) => {
                            let _ = tx.send(InstallUpdate::Log(
                                "✓ Deploy API model pipeline completed".into(),
                            ));
                            Ok(())
                        }
                        Ok(99) | Ok(_) => {
                            let _ = tx.send(InstallUpdate::Log(
                                "Deploy API model pipeline unavailable; using script fallback".into(),
                            ));

                            let mut env_prefix = String::new();
                            if let Some(ref tier) = profile_model_tier {
                                env_prefix.push_str(&format!("LLM_TIER={} MODEL_TIER={} ", shell_escape(tier), shell_escape(tier)));
                            }
                            if let Some(ref backend) = profile_llm_backend {
                                env_prefix.push_str(&format!("LLM_BACKEND={} ", shell_escape(backend)));
                            }
                            if let Some(ref octets) = profile_network_base_octets {
                                env_prefix.push_str(&format!("NETWORK_BASE_OCTETS={} ", shell_escape(octets)));
                            }
                            let gen_cmd = format!("{env_prefix}bash scripts/llm/generate-model-config.sh");
                            match remote::exec_remote_streaming(&ssh, &remote_path, &gen_cmd, on_model_line) {
                                Ok(0) => {
                                    let remote_model_cfg = format!(
                                        "{}/provision/ansible/group_vars/all/model_config.yml",
                                        remote_path.trim_end_matches('/')
                                    );
                                    let local_model_cfg = repo_root.join("provision/ansible/group_vars/all/model_config.yml");
                                    match remote::pull_file(
                                        effective_host,
                                        user,
                                        key,
                                        &remote_model_cfg,
                                        &local_model_cfg,
                                    ) {
                                        Ok(()) => {
                                            let _ = tx.send(InstallUpdate::Log(format!(
                                                "✓ Pulled model_config.yml to {}",
                                                local_model_cfg.display()
                                            )));
                                            Ok(())
                                        }
                                        Err(e) => Err(color_eyre::eyre::eyre!(e.to_string())),
                                    }
                                }
                                Ok(code) => Err(color_eyre::eyre::eyre!(
                                    "generate-model-config.sh failed (exit code {code})"
                                )),
                                Err(e) => Err(e),
                            }
                        }
                        Err(e) => Err(e),
                    }
                } else {
                    Err(color_eyre::eyre::eyre!("No SSH connection"))
                };

                if let Err(e) = model_cfg_result {
                    any_failed = true;
                    for svc in services {
                        let _ = tx.send(InstallUpdate::ServiceStatus {
                            name: svc.clone(),
                            status: InstallStatus::Failed(e.to_string()),
                        });
                    }
                    let _ = tx.send(InstallUpdate::Log(format!(
                        "ERROR: Failed to generate model_config.yml: {e}"
                    )));
                    break;
                }
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
            let mut ref_exports = format!(
                "BUSIBOX_FRONTEND_GITHUB_REF={ref_val} \
                 SITE_DOMAIN={site_domain} \
                 MODEL_HOST_CACHE=$HOME/.cache \
                 HF_HOST_CACHE=$HOME/.cache/huggingface \
                 FASTEMBED_HOST_CACHE=$HOME/.cache/fastembed "
            );
            if let Some(ref backend) = profile_llm_backend {
                ref_exports.push_str(&format!("LLM_BACKEND={backend} "));
            }
            if let Some(ref token) = github_token {
                ref_exports.push_str(&format!("GITHUB_AUTH_TOKEN={token} "));
            }
            if is_update && service_list.contains("core-apps") {
                ref_exports.push_str("ENABLED_APPS=all ");
            }
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
                format!("https://{site_domain}/portal/setup")
            } else {
                format!("https://{site_domain}/portal/setup")
            };
            let _ = tx.send(InstallUpdate::Complete {
                portal_url: Some(portal_url),
            });
        }
    });
}

fn prepare_ssl_inputs(
    repo_root: &std::path::Path,
    site_domain: &str,
    ssl_cert_name: Option<&str>,
) -> color_eyre::Result<()> {
    let site_domain = site_domain.trim();
    if site_domain.is_empty() {
        return Ok(());
    }
    let Some(cert_name) = ssl_cert_name.map(str::trim).filter(|c| !c.is_empty()) else {
        return Ok(());
    };
    if cert_name == site_domain {
        return Ok(());
    }

    let ssl_dir = repo_root.join("ssl");
    let source_crt = ssl_dir.join(format!("{cert_name}.crt"));
    let source_key = ssl_dir.join(format!("{cert_name}.key"));
    if !source_crt.exists() || !source_key.exists() {
        return Err(color_eyre::eyre::eyre!(
            "selected certificate pair not found: {} / {}",
            source_crt.display(),
            source_key.display()
        ));
    }

    std::fs::create_dir_all(&ssl_dir)?;
    let target_crt = ssl_dir.join(format!("{site_domain}.crt"));
    let target_key = ssl_dir.join(format!("{site_domain}.key"));
    std::fs::copy(&source_crt, &target_crt)?;
    std::fs::copy(&source_key, &target_key)?;
    Ok(())
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
