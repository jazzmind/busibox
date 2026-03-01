use crate::app::{App, ModelCacheCheckState, ModelCacheEntry, Screen};
use crate::modules::models::ModelRecommendation;
use crate::theme;
use crossterm::event::{KeyCode, KeyEvent};
use ratatui::layout::Margin;
use ratatui::prelude::*;
use ratatui::widgets::{Scrollbar, ScrollbarOrientation, ScrollbarState, *};

pub fn render(f: &mut Frame, app: &App) {
    let chunks = Layout::default()
        .direction(Direction::Vertical)
        .constraints([
            Constraint::Length(5),
            Constraint::Length(3),
            Constraint::Min(14),
            Constraint::Length(3),
        ])
        .margin(2)
        .split(f.area());

    // Logo
    let logo = Paragraph::new(theme::LOGO)
        .style(theme::title())
        .alignment(Alignment::Center);
    f.render_widget(logo, chunks[0]);

    // Subtitle
    let subtitle = Paragraph::new("Local LLM Infrastructure Platform")
        .style(theme::muted())
        .alignment(Alignment::Center);
    f.render_widget(subtitle, chunks[1]);

    // Main content: system info + menu
    let content_chunks = Layout::default()
        .direction(Direction::Horizontal)
        .constraints([Constraint::Percentage(50), Constraint::Percentage(50)])
        .split(chunks[2]);

    // System info panel — show active profile's machine stats
    let mut info_lines = vec![
        Line::from(Span::styled("System Info", theme::heading())),
        Line::from(""),
    ];

    let is_remote = app.active_profile().map(|(_, p)| p.remote).unwrap_or(false);
    let profile_hw = if is_remote {
        app.remote_hardware.as_ref()
            .or_else(|| app.active_profile().and_then(|(_, p)| p.hardware.as_ref()))
    } else {
        app.local_hardware.as_ref()
    };

    if let Some(hw) = profile_hw {
        info_lines.push(Line::from(vec![
            Span::styled("  OS:    ", theme::muted()),
            Span::styled(hw.os.to_string(), theme::normal()),
        ]));
        info_lines.push(Line::from(vec![
            Span::styled("  Arch:  ", theme::muted()),
            Span::styled(hw.arch.to_string(), theme::normal()),
        ]));
        info_lines.push(Line::from(vec![
            Span::styled("  RAM:   ", theme::muted()),
            Span::styled(format!("{} GB", hw.ram_gb), theme::normal()),
        ]));
        info_lines.push(Line::from(vec![
            Span::styled("  LLM:   ", theme::muted()),
            Span::styled(hw.llm_backend.to_string(), theme::normal()),
        ]));
        info_lines.push(Line::from(vec![
            Span::styled("  Tier:  ", theme::muted()),
            Span::styled(hw.memory_tier.to_string(), theme::info()),
        ]));
        if !hw.gpus.is_empty() {
            for gpu in &hw.gpus {
                info_lines.push(Line::from(vec![
                    Span::styled("  GPU:   ", theme::muted()),
                    Span::styled(
                        format!("{} ({}GB)", gpu.name, gpu.vram_gb),
                        theme::normal(),
                    ),
                ]));
            }
        }
    } else {
        info_lines.push(Line::from(Span::styled(
            "  Detecting...",
            theme::muted(),
        )));
    }

    // Model cache status
    if !app.model_cache_status.is_empty() {
        info_lines.push(Line::from(""));
        info_lines.push(Line::from(Span::styled("Model Cache", theme::heading())));
        for entry in &app.model_cache_status {
            let (icon, style) = if entry.cached {
                ("✓", theme::success())
            } else {
                ("○", theme::warning())
            };
            let short_name = entry.name.rsplit('/').next().unwrap_or(&entry.name);
            info_lines.push(Line::from(vec![
                Span::styled(format!("  {icon} "), style),
                Span::styled(format!("{}: ", entry.role), theme::muted()),
                Span::styled(short_name, theme::normal()),
            ]));
        }
        let cached_count = app.model_cache_status.iter().filter(|e| e.cached).count();
        let total_count = app.model_cache_status.len();
        if cached_count == total_count {
            info_lines.push(Line::from(Span::styled(
                "  All models ready",
                theme::success(),
            )));
        } else {
            info_lines.push(Line::from(Span::styled(
                format!("  {}/{} cached", cached_count, total_count),
                theme::dim(),
            )));
        }
    } else if app.model_cache_check_state == ModelCacheCheckState::Checking {
        info_lines.push(Line::from(""));
        info_lines.push(Line::from(Span::styled("Model Cache", theme::heading())));
        info_lines.push(Line::from(Span::styled(
            "  Checking...",
            theme::muted(),
        )));
    }

    if let Some((id, profile)) = app.active_profile() {
        info_lines.push(Line::from(""));
        info_lines.push(Line::from(Span::styled("Active Profile", theme::heading())));
        info_lines.push(Line::from(vec![
            Span::styled("  Name:  ", theme::muted()),
            Span::styled(id, theme::info()),
        ]));
        info_lines.push(Line::from(vec![
            Span::styled("  Env:   ", theme::muted()),
            Span::styled(&profile.environment, theme::normal()),
        ]));
        if profile.remote {
            let host_display = profile.effective_host().unwrap_or("unknown");
            info_lines.push(Line::from(vec![
                Span::styled("  Host:  ", theme::muted()),
                Span::styled(
                    format!("{} (remote)", host_display),
                    theme::info(),
                ),
            ]));
        } else {
            info_lines.push(Line::from(vec![
                Span::styled("  Host:  ", theme::muted()),
                Span::styled("localhost", theme::normal()),
            ]));
        }
    }

    let info_height = content_chunks[0].height.saturating_sub(2) as usize; // borders
    let info_lines_len = info_lines.len();
    let scroll_y = if info_lines_len > info_height {
        (info_lines_len - info_height) as u16
    } else {
        0
    };

    let info_block = Paragraph::new(info_lines)
        .block(
            Block::default()
                .borders(Borders::ALL)
                .border_style(theme::dim())
                .title(" System ")
                .title_style(theme::heading()),
        )
        .scroll((scroll_y, 0));
    f.render_widget(info_block, content_chunks[0]);

    if scroll_y > 0 {
        let mut scrollbar_state = ScrollbarState::new(info_lines_len)
            .position(scroll_y as usize);
        let scrollbar = Scrollbar::new(ScrollbarOrientation::VerticalRight)
            .begin_symbol(Some("↑"))
            .end_symbol(Some("↓"));
        f.render_stateful_widget(
            scrollbar,
            content_chunks[0].inner(Margin { vertical: 1, horizontal: 0 }),
            &mut scrollbar_state,
        );
    }

    // Menu panel
    if app.install_submenu_open {
        let sub_items = app.install_submenu_items();
        let selected = app.install_submenu_selected;

        let mut items: Vec<ListItem> = Vec::new();

        // Show deployment state context
        let state_line = match &app.deployment_state {
            crate::app::DeploymentState::Checking => "  Checking containers...".to_string(),
            crate::app::DeploymentState::None => "  No containers found".to_string(),
            crate::app::DeploymentState::Partial(n) => format!("  {n} container(s) running"),
            crate::app::DeploymentState::BootstrapComplete => "  Bootstrap services ready — complete setup in browser".to_string(),
            crate::app::DeploymentState::Complete => "  All services running".to_string(),
            crate::app::DeploymentState::Unknown => "  Status unknown".to_string(),
        };
        items.push(ListItem::new(state_line).style(theme::dim()));
        items.push(ListItem::new(""));

        for (i, item) in sub_items.iter().enumerate() {
            let style = if i == selected {
                theme::selected()
            } else {
                theme::normal()
            };
            items.push(ListItem::new(format!("  {item}  ")).style(style));
        }

        let menu = List::new(items)
            .block(
                Block::default()
                    .borders(Borders::ALL)
                    .border_style(theme::dim())
                    .title(" Install / Update ")
                    .title_style(theme::heading()),
            )
            .highlight_style(theme::selected());
        f.render_widget(menu, content_chunks[1]);
    } else {
        let menu_items = app.welcome_menu_items();
        let items: Vec<ListItem> = menu_items
            .iter()
            .enumerate()
            .map(|(i, item)| {
                let style = if i == app.menu_selected {
                    theme::selected()
                } else {
                    theme::normal()
                };
                ListItem::new(format!("  {item}  ")).style(style)
            })
            .collect();

        let menu = List::new(items)
            .block(
                Block::default()
                    .borders(Borders::ALL)
                    .border_style(theme::dim())
                    .title(" Menu ")
                    .title_style(theme::heading()),
            )
            .highlight_style(theme::selected());
        f.render_widget(menu, content_chunks[1]);
    }

    // Status bar
    let status_text = if let Some((msg, kind)) = &app.status_message {
        let style = match kind {
            crate::app::MessageKind::Info => theme::info(),
            crate::app::MessageKind::Success => theme::success(),
            crate::app::MessageKind::Warning => theme::warning(),
            crate::app::MessageKind::Error => theme::error(),
        };
        Span::styled(msg.as_str(), style)
    } else if app.install_submenu_open {
        Span::styled(
            " ↑/↓ Navigate  Enter Select  Esc Back",
            theme::muted(),
        )
    } else {
        Span::styled(
            " ↑/↓ Navigate  Enter Select  m Models  x Export  p Password  b Deploy CLI  q Quit",
            theme::muted(),
        )
    };
    let status = Paragraph::new(Line::from(status_text));
    f.render_widget(status, chunks[3]);
}

pub fn handle_key(app: &mut App, key: KeyEvent) {
    if app.install_submenu_open {
        handle_submenu_key(app, key);
        return;
    }

    let menu_items = app.welcome_menu_items();
    match key.code {
        KeyCode::Char('q') | KeyCode::Esc => app.should_quit = true,
        KeyCode::Up | KeyCode::Char('k') => {
            if app.menu_selected > 0 {
                app.menu_selected -= 1;
            }
        }
        KeyCode::Down | KeyCode::Char('j') => {
            if app.menu_selected < menu_items.len() - 1 {
                app.menu_selected += 1;
            }
        }
        KeyCode::Char('m') => {
            check_model_cache(app);
        }
        KeyCode::Enter => {
            let item = menu_items[app.menu_selected];
            match item {
                "Install / Update" => {
                    app.install_submenu_open = true;
                    app.install_submenu_selected = 0;
                    app.deployment_state = crate::app::DeploymentState::Unknown;
                    detect_deployment_state(app);
                }
                "Profiles" => {
                    app.screen = Screen::ProfileSelect;
                    app.menu_selected = 0;
                }
                "Quit" => app.should_quit = true,
                _ => {}
            }
        }
        KeyCode::Char('x') => {
            if let Some((_, profile)) = app.active_profile() {
                if profile.remote && app.vault_password.is_some() {
                    app.pending_profile_export = true;
                } else if !profile.remote {
                    app.set_message("Export is only for remote profiles", crate::app::MessageKind::Info);
                } else {
                    app.set_message("Unlock vault first (select profile)", crate::app::MessageKind::Info);
                }
            }
        }
        KeyCode::Char('p') => {
            if let Some((id, _)) = app.active_profile() {
                if crate::modules::vault::has_vault_key(&id) {
                    app.pending_password_change = true;
                } else {
                    app.set_message("No vault key for this profile", crate::app::MessageKind::Info);
                }
            }
        }
        KeyCode::Char('b') => {
            if let Some((_, profile)) = app.active_profile() {
                if profile.remote {
                    app.pending_deploy_binary = true;
                } else {
                    app.set_message("Deploy CLI is only for remote profiles", crate::app::MessageKind::Info);
                }
            }
        }
        _ => {}
    }
}

fn handle_submenu_key(app: &mut App, key: KeyEvent) {
    let sub_items = app.install_submenu_items();
    match key.code {
        KeyCode::Esc => {
            app.install_submenu_open = false;
            app.clear_message();
        }
        KeyCode::Up | KeyCode::Char('k') => {
            if app.install_submenu_selected > 0 {
                app.install_submenu_selected -= 1;
            }
        }
        KeyCode::Down | KeyCode::Char('j') => {
            if app.install_submenu_selected < sub_items.len().saturating_sub(1) {
                app.install_submenu_selected += 1;
            }
        }
        KeyCode::Enter => {
            if matches!(app.deployment_state, crate::app::DeploymentState::Checking) {
                return;
            }
            let item = sub_items[app.install_submenu_selected];
            match item {
                "Install" | "Continue Install" | "Update" => {
                    app.install_submenu_open = false;
                    app.set_message(
                        "⠋ Connecting to remote host...",
                        crate::app::MessageKind::Info,
                    );
                    app.pending_resume_install = true;
                }
                "Clean Install" => {
                    app.install_submenu_open = false;
                    app.clean_install = true;
                    app.set_message(
                        "⠋ Preparing clean install...",
                        crate::app::MessageKind::Info,
                    );
                    app.pending_resume_install = true;
                }
                "Admin Login" => {
                    app.install_submenu_open = false;
                    app.admin_login_magic_link = None;
                    app.admin_login_totp_code = None;
                    app.admin_login_verify_url = None;
                    app.admin_login_error = None;
                    app.admin_login_loading = true;
                    app.screen = Screen::AdminLogin;
                    app.pending_admin_login = true;
                }
                "Manage" => {
                    app.install_submenu_open = false;
                    app.screen = Screen::Manage;
                    app.menu_selected = 0;
                }
                "Checking..." => {}
                _ => {}
            }
        }
        _ => {}
    }
}

/// Detect deployment state by checking running Docker containers.
/// Sets app.deployment_state based on container count and core-apps health.
pub fn detect_deployment_state(app: &mut App) {
    use crate::app::DeploymentState;
    use crate::modules::remote;
    use crate::screens::install::env_to_prefix;

    app.deployment_state = DeploymentState::Checking;

    let profile = match app.active_profile() {
        Some((_, p)) => p.clone(),
        None => {
            app.deployment_state = DeploymentState::None;
            return;
        }
    };

    let prefix = env_to_prefix(&profile.environment);
    let docker_cmd = format!(
        "docker ps --format '{{{{.Names}}}} {{{{.Status}}}}' --filter 'name=^{prefix}-' 2>/dev/null"
    );

    let output = if profile.remote {
        if let Some(ssh) = &app.ssh_connection {
            let cmd = format!("{}{docker_cmd}", remote::SHELL_PATH_PREAMBLE);
            ssh.run(&cmd).unwrap_or_default()
        } else if let (Some(host), Some(key)) = (&profile.remote_host, &profile.remote_ssh_key) {
            let user = profile.remote_user.as_deref().unwrap_or("root");
            let conn = crate::modules::ssh::SshConnection::new(host, user, key);
            if conn.test_connection() {
                app.ssh_connection = Some(conn);
                let cmd = format!("{}{docker_cmd}", remote::SHELL_PATH_PREAMBLE);
                app.ssh_connection.as_ref().unwrap().run(&cmd).unwrap_or_default()
            } else {
                app.deployment_state = DeploymentState::Unknown;
                return;
            }
        } else {
            app.deployment_state = DeploymentState::Unknown;
            return;
        }
    } else {
        let output = std::process::Command::new("bash")
            .arg("-c")
            .arg(&docker_cmd)
            .output();
        match output {
            Ok(o) => String::from_utf8_lossy(&o.stdout).to_string(),
            Err(_) => {
                app.deployment_state = DeploymentState::Unknown;
                return;
            }
        }
    };

    let clean = remote::strip_ansi(&output);
    let lines: Vec<&str> = clean.lines().filter(|l| !l.trim().is_empty()).collect();
    let container_count = lines.len();

    if container_count == 0 {
        app.deployment_state = DeploymentState::None;
        return;
    }

    // Check which key services are present (running or healthy)
    let has_container = |name: &str| -> bool {
        lines.iter().any(|line| line.contains(&format!("{prefix}-{name}")))
    };

    // Bootstrap services: postgres, authz-api, deploy-api, proxy, core-apps
    let bootstrap_done = has_container("postgres")
        && has_container("authz-api")
        && has_container("deploy-api")
        && has_container("proxy")
        && has_container("core-apps");

    // Full platform additionally needs agent-api and litellm
    let full_platform = bootstrap_done
        && has_container("agent-api")
        && has_container("litellm");

    if full_platform {
        app.deployment_state = DeploymentState::Complete;
    } else if bootstrap_done {
        app.deployment_state = DeploymentState::BootstrapComplete;
    } else {
        app.deployment_state = DeploymentState::Partial(container_count);
    }
}

/// Check model cache status for the active profile.
/// For local profiles: checks HuggingFace cache directory directly.
/// For remote profiles: connects via SSH to check remote cache.
pub fn check_model_cache(app: &mut App) {
    use crate::modules::models;

    app.model_cache_check_state = ModelCacheCheckState::Checking;
    app.model_cache_status.clear();

    let profile = match app.active_profile() {
        Some((_, p)) => p.clone(),
        None => {
            app.model_cache_check_state = ModelCacheCheckState::Failed;
            return;
        }
    };

    let hw = if profile.remote {
        app.remote_hardware.as_ref()
            .or(profile.hardware.as_ref())
    } else {
        app.local_hardware.as_ref()
    };
    let hw = match hw {
        Some(h) => h,
        None => {
            app.model_cache_check_state = ModelCacheCheckState::Failed;
            return;
        }
    };

    let tier = profile.effective_model_tier().unwrap_or(hw.memory_tier);
    let backend = &hw.llm_backend;
    let config_path = app.repo_root
        .join("provision")
        .join("ansible")
        .join("group_vars")
        .join("all")
        .join("model_registry.yml");

    let rec = match ModelRecommendation::from_config(&config_path, tier, backend) {
        Ok(r) => r,
        Err(_) => {
            app.model_cache_check_state = ModelCacheCheckState::Failed;
            return;
        }
    };

    if profile.remote {
        // Remote: check via SSH
        let host = match profile.effective_host() {
            Some(h) => h.to_string(),
            None => {
                app.model_cache_check_state = ModelCacheCheckState::Failed;
                return;
            }
        };
        let user = profile.effective_user().to_string();
        let key = profile.effective_ssh_key().to_string();
        let remote_path = profile.effective_remote_path().to_string();

        let ssh = crate::modules::ssh::SshConnection::new(&host, &user, &key);

        let model_list: Vec<(String, String)> = rec.models()
            .iter()
            .filter(|m| !m.name.is_empty())
            .map(|m| (m.name.clone(), m.role.clone()))
            .collect();

        let results = models::check_remote_model_cache(&ssh, &remote_path, &model_list);
        app.model_cache_status = results
            .into_iter()
            .map(|(name, role, cached)| ModelCacheEntry { name, role, cached })
            .collect();
    } else {
        // Local: check HuggingFace cache directory
        let mut seen = std::collections::HashSet::new();
        for m in rec.models() {
            if !m.name.is_empty() && seen.insert(m.name.clone()) {
                let cached = models::is_model_cached_locally(&m.name);
                app.model_cache_status.push(ModelCacheEntry {
                    name: m.name.clone(),
                    role: m.role.clone(),
                    cached,
                });
            }
        }
    }

    app.model_cache_check_state = ModelCacheCheckState::Done;
}
