use crate::app::{App, DeploymentState, ModelCacheCheckState, ModelCacheEntry, Screen};
use crate::modules::health::{self, HealthStatus, HealthUpdate};
use crate::modules::models::ModelRecommendation;
use crate::theme;
use crossterm::event::{KeyCode, KeyEvent};
use ratatui::layout::Margin;
use ratatui::prelude::*;
use ratatui::widgets::{Scrollbar, ScrollbarOrientation, ScrollbarState, *};

const SPINNER: &[&str] = &["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"];

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

    // Main content: system info (left) + status & actions (right)
    let content_chunks = Layout::default()
        .direction(Direction::Horizontal)
        .constraints([Constraint::Percentage(45), Constraint::Percentage(55)])
        .split(chunks[2]);

    render_system_info(f, app, content_chunks[0]);
    render_status_and_actions(f, app, content_chunks[1]);

    // Status bar / confirmation prompt
    if app.pending_clean_install_confirm {
        let prompt = Line::from(vec![
            Span::styled(
                " ⚠ This will DESTROY all data and containers. Type 'yes' to confirm: ",
                theme::warning(),
            ),
            Span::styled(&app.clean_install_confirm_input, Style::default().fg(Color::White).add_modifier(Modifier::BOLD)),
            Span::styled("█", Style::default().fg(Color::White)),
        ]);
        f.render_widget(Paragraph::new(prompt), chunks[3]);
    } else if app.pending_update_confirm {
        let prompt = Line::from(vec![
            Span::styled(
                " ⚠ Update redeploys all services. Type 'update' to confirm: ",
                theme::warning(),
            ),
            Span::styled(&app.update_confirm_input, Style::default().fg(Color::White).add_modifier(Modifier::BOLD)),
            Span::styled("█", Style::default().fg(Color::White)),
        ]);
        f.render_widget(Paragraph::new(prompt), chunks[3]);
    } else {
        let status_text = if let Some((msg, kind)) = &app.status_message {
            let style = match kind {
                crate::app::MessageKind::Info => theme::info(),
                crate::app::MessageKind::Success => theme::success(),
                crate::app::MessageKind::Warning => theme::warning(),
                crate::app::MessageKind::Error => theme::error(),
            };
            Span::styled(msg.as_str(), style)
        } else {
            Span::styled(
                " ↑/↓ Navigate  Enter Select  t Tunnel  s Sync  r Refresh  m Models  p Profiles  q Quit",
                theme::muted(),
            )
        };
        let status = Paragraph::new(Line::from(status_text));
        f.render_widget(status, chunks[3]);
    }
}

fn render_system_info(f: &mut Frame, app: &App, area: Rect) {
    let mut info_lines = vec![
        Line::from(Span::styled("System Info", theme::heading())),
        Line::from(""),
    ];

    let is_remote = app.active_profile().map(|(_, p)| p.remote).unwrap_or(false);
    let profile_hw = if is_remote {
        app.remote_hardware
            .as_ref()
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
                Span::styled(format!("{} (remote)", host_display), theme::info()),
            ]));
        } else {
            info_lines.push(Line::from(vec![
                Span::styled("  Host:  ", theme::muted()),
                Span::styled("localhost", theme::normal()),
            ]));
        }
    }

    // SSH tunnel status
    if let Some((_, profile)) = app.active_profile() {
        if profile.remote {
            info_lines.push(Line::from(""));
            if app.ssh_tunnel_active {
                info_lines.push(Line::from(vec![
                    Span::styled("  🔗 ", theme::success()),
                    Span::styled("Tunnel: ", theme::muted()),
                    Span::styled("https://localhost:4443", theme::success()),
                ]));
            } else {
                info_lines.push(Line::from(vec![
                    Span::styled("  ○ ", theme::dim()),
                    Span::styled("Tunnel: ", theme::muted()),
                    Span::styled("off — press 't' to connect", theme::dim()),
                ]));
            }
        }
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

    let info_height = area.height.saturating_sub(2) as usize;
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
    f.render_widget(info_block, area);

    if scroll_y > 0 {
        let mut scrollbar_state =
            ScrollbarState::new(info_lines_len).position(scroll_y as usize);
        let scrollbar = Scrollbar::new(ScrollbarOrientation::VerticalRight)
            .begin_symbol(Some("↑"))
            .end_symbol(Some("↓"));
        f.render_stateful_widget(
            scrollbar,
            area.inner(Margin {
                vertical: 1,
                horizontal: 0,
            }),
            &mut scrollbar_state,
        );
    }
}

fn render_status_and_actions(f: &mut Frame, app: &App, area: Rect) {
    // Split the right panel into status (top) and actions (bottom)
    let right_chunks = Layout::default()
        .direction(Direction::Vertical)
        .constraints([Constraint::Min(7), Constraint::Min(5)])
        .split(area);

    render_status_panel(f, app, right_chunks[0]);
    render_action_menu(f, app, right_chunks[1]);
}

fn render_status_panel(f: &mut Frame, app: &App, area: Rect) {
    let tick = app.health_tick;
    let spinner_char = SPINNER[tick % SPINNER.len()];

    let mut lines: Vec<Line> = Vec::new();

    if !app.has_profiles() {
        lines.push(Line::from(Span::styled(
            "  No profiles configured",
            theme::muted(),
        )));
        lines.push(Line::from(Span::styled(
            "  Press 'p' to create one",
            theme::muted(),
        )));
    } else if app.health_check_running && app.health_groups.is_empty() {
        lines.push(Line::from(vec![
            Span::styled(format!("  {spinner_char} "), theme::info()),
            Span::styled("Checking services...", theme::muted()),
        ]));
    } else if app.health_groups.is_empty() {
        match &app.deployment_state {
            DeploymentState::None => {
                lines.push(Line::from(Span::styled(
                    "  No services detected",
                    theme::muted(),
                )));
            }
            DeploymentState::Unknown => {
                lines.push(Line::from(Span::styled(
                    "  Status unknown — press 'r' to check",
                    theme::muted(),
                )));
            }
            _ => {
                lines.push(Line::from(Span::styled(
                    "  Waiting for health checks...",
                    theme::muted(),
                )));
            }
        }
    } else {
        for group in &app.health_groups {
            let (icon, style) = match &group.status {
                HealthStatus::Healthy => ("●", theme::success()),
                HealthStatus::Unhealthy => ("●", theme::warning()),
                HealthStatus::Down => ("○", theme::error()),
                HealthStatus::Checking => (spinner_char, theme::info()),
            };

            let count_str = format!("({}/{})", group.healthy, group.total);
            lines.push(Line::from(vec![
                Span::styled(format!("  {icon} "), style),
                Span::styled(format!("{:<18}", group.name), theme::normal()),
                Span::styled(count_str, theme::muted()),
            ]));
        }

        if app.health_check_running {
            lines.push(Line::from(""));
            lines.push(Line::from(vec![
                Span::styled(format!("  {spinner_char} "), theme::info()),
                Span::styled("Refreshing...", theme::dim()),
            ]));
        }
    }

    // Deployed models / GPU allocation (hybrid: config + live status)
    if let Some(ref deployed) = app.deployed_models {
        use crate::modules::models::LiveStatus;

        lines.push(Line::from(""));
        let loading_indicator = if app.deployed_models_loading {
            let tick = app.health_tick;
            format!(" {}", SPINNER[tick % SPINNER.len()])
        } else {
            String::new()
        };
        lines.push(Line::from(Span::styled(
            format!("Active Models ({}){loading_indicator}", deployed.loaded_from),
            theme::heading(),
        )));

        for model in &deployed.models {
            let short_name = model
                .model_name
                .rsplit('/')
                .next()
                .unwrap_or(&model.model_name);
            let short_name = if short_name.len() > 26 {
                format!("{}…", &short_name[..25])
            } else {
                short_name.to_string()
            };

            let gpu_str = if model.gpu.is_empty() {
                " cpu".to_string()
            } else {
                format!(" GPU {}", model.gpu)
            };

            let port_str = if model.port > 0 {
                format!(" :{}", model.port)
            } else {
                String::new()
            };

            let (status_str, status_style) = match &model.live_status {
                LiveStatus::Running => (" [running]".to_string(), theme::success()),
                LiveStatus::Down => (" [down]".to_string(), theme::error()),
                LiveStatus::Checking => (" [checking...]".to_string(), theme::warning()),
                LiveStatus::Error(e) => (format!(" [{e}]"), theme::error()),
                LiveStatus::Unknown => (" [?]".to_string(), theme::dim()),
            };

            let role_str = if !model.model_key.is_empty() {
                format!("  ({})", model.model_key)
            } else {
                String::new()
            };

            lines.push(Line::from(vec![
                Span::styled("  ▸ ", theme::info()),
                Span::styled(short_name, theme::normal()),
                Span::styled(gpu_str, theme::highlight()),
                Span::styled(port_str, theme::dim()),
                Span::styled(status_str, status_style),
                Span::styled(role_str, theme::dim()),
            ]));
        }
    } else if let Some(ref tier_set) = app.active_tier_models {
        // Fallback: show registry-based models when model_config.yml is unavailable
        lines.push(Line::from(""));
        lines.push(Line::from(Span::styled(
            format!("Expected Models ({})", tier_set.tier),
            theme::heading(),
        )));

        let gpu_models: Vec<&crate::modules::models::TierModel> =
            tier_set.models.iter().filter(|m| m.needs_gpu).collect();
        let cpu_models: Vec<&crate::modules::models::TierModel> =
            tier_set.models.iter().filter(|m| !m.needs_gpu).collect();

        for model in &gpu_models {
            let short_name = if model.model_name.len() > 24 {
                format!("{}…", &model.model_name[..23])
            } else {
                model.model_name.clone()
            };
            let gpu_str = model
                .gpu
                .as_deref()
                .map(|g| format!(" GPU {g}"))
                .unwrap_or_default();
            let roles: String = model.roles.iter().take(2).cloned().collect::<Vec<_>>().join(",");
            lines.push(Line::from(vec![
                Span::styled("  ▸ ", theme::info()),
                Span::styled(short_name, theme::normal()),
                Span::styled(gpu_str, theme::highlight()),
                Span::styled(format!("  ({roles})"), theme::dim()),
            ]));
        }

        if !cpu_models.is_empty() {
            let cpu_names: Vec<&str> = cpu_models
                .iter()
                .map(|m| {
                    m.model_name
                        .rsplit('/')
                        .next()
                        .unwrap_or(&m.model_name)
                })
                .collect();
            lines.push(Line::from(vec![
                Span::styled("  · ", theme::dim()),
                Span::styled(cpu_names.join(", "), theme::dim()),
                Span::styled(" (cpu)", theme::dim()),
            ]));
        }
    }

    let panel = Paragraph::new(lines).block(
        Block::default()
            .borders(Borders::ALL)
            .border_style(theme::dim())
            .title(" Status ")
            .title_style(theme::heading()),
    );
    f.render_widget(panel, area);
}

fn render_action_menu(f: &mut Frame, app: &App, area: Rect) {
    let actions = app.contextual_actions();

    if actions.is_empty() {
        let msg = if !app.has_profiles() {
            "  Create a profile to get started"
        } else {
            "  Checking..."
        };
        let empty = Paragraph::new(Span::styled(msg, theme::muted())).block(
            Block::default()
                .borders(Borders::ALL)
                .border_style(theme::dim())
                .title(" Actions ")
                .title_style(theme::heading()),
        );
        f.render_widget(empty, area);
        return;
    }

    let items: Vec<ListItem> = actions
        .iter()
        .enumerate()
        .map(|(i, item)| {
            let style = if i == app.action_menu_selected {
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
                .title(" Actions ")
                .title_style(theme::heading()),
        )
        .highlight_style(theme::selected());
    f.render_widget(menu, area);
}

pub fn handle_key(app: &mut App, key: KeyEvent) {
    // Clean install confirmation prompt
    if app.pending_clean_install_confirm {
        match key.code {
            KeyCode::Esc => {
                app.pending_clean_install_confirm = false;
                app.clean_install_confirm_input.clear();
                app.clear_message();
            }
            KeyCode::Enter => {
                if app.clean_install_confirm_input.trim().eq_ignore_ascii_case("yes") {
                    app.pending_clean_install_confirm = false;
                    app.clean_install_confirm_input.clear();
                    app.clean_install = true;
                    app.set_message(
                        "⠋ Preparing clean install...",
                        crate::app::MessageKind::Info,
                    );
                    app.pending_resume_install = true;
                } else {
                    app.pending_clean_install_confirm = false;
                    app.clean_install_confirm_input.clear();
                    app.set_message(
                        "Clean install cancelled.",
                        crate::app::MessageKind::Info,
                    );
                }
            }
            KeyCode::Backspace => {
                app.clean_install_confirm_input.pop();
            }
            KeyCode::Char(c) => {
                app.clean_install_confirm_input.push(c);
            }
            _ => {}
        }
        return;
    }
    // Update confirmation prompt
    if app.pending_update_confirm {
        match key.code {
            KeyCode::Esc => {
                app.pending_update_confirm = false;
                app.update_confirm_input.clear();
                app.clear_message();
            }
            KeyCode::Enter => {
                if app.update_confirm_input.trim().eq_ignore_ascii_case("update") {
                    app.pending_update_confirm = false;
                    app.update_confirm_input.clear();
                    app.is_update = true;
                    app.set_message(
                        "⠋ Preparing update (all services)...",
                        crate::app::MessageKind::Info,
                    );
                    app.pending_resume_install = true;
                } else {
                    app.pending_update_confirm = false;
                    app.update_confirm_input.clear();
                    app.set_message(
                        "Update cancelled.",
                        crate::app::MessageKind::Info,
                    );
                }
            }
            KeyCode::Backspace => {
                app.update_confirm_input.pop();
            }
            KeyCode::Char(c) => {
                app.update_confirm_input.push(c);
            }
            _ => {}
        }
        return;
    }

    let actions = app.contextual_actions();
    let action_count = actions.len();

    match key.code {
        KeyCode::Char('q') | KeyCode::Esc => app.should_quit = true,
        KeyCode::Up | KeyCode::Char('k') => {
            if app.action_menu_selected > 0 {
                app.action_menu_selected -= 1;
            }
        }
        KeyCode::Down | KeyCode::Char('j') => {
            if action_count > 0 && app.action_menu_selected < action_count - 1 {
                app.action_menu_selected += 1;
            }
        }
        KeyCode::Char('m') => {
            if app.has_profiles() {
                app.models_manage_loaded = false;
                app.screen = crate::app::Screen::ModelsManage;
            } else {
                app.set_message("No profile configured — set up first", crate::app::MessageKind::Warning);
            }
        }
        KeyCode::Char('r') => {
            trigger_health_checks(app);
        }
        KeyCode::Char('p') => {
            app.screen = Screen::ProfileSelect;
            app.menu_selected = 0;
        }
        KeyCode::Char('x') => {
            if let Some((_, profile)) = app.active_profile() {
                if profile.remote && app.vault_password.is_some() {
                    app.pending_profile_export = true;
                } else if !profile.remote {
                    app.set_message(
                        "Export is only for remote profiles",
                        crate::app::MessageKind::Info,
                    );
                } else {
                    app.set_message(
                        "Unlock vault first (select profile)",
                        crate::app::MessageKind::Info,
                    );
                }
            }
        }
        KeyCode::Char('b') => {
            if let Some((_, profile)) = app.active_profile() {
                if profile.remote {
                    app.pending_deploy_binary = true;
                } else {
                    app.set_message(
                        "Deploy CLI is only for remote profiles",
                        crate::app::MessageKind::Info,
                    );
                }
            }
        }
        KeyCode::Char('t') => {
            app.toggle_ssh_tunnel();
        }
        KeyCode::Char('s') => {
            if let Some((_, profile)) = app.active_profile() {
                if profile.remote {
                    app.set_message(
                        "⠋ Syncing code to remote host...",
                        crate::app::MessageKind::Info,
                    );
                    app.pending_code_sync = true;
                } else {
                    app.set_message(
                        "Sync is only for remote profiles",
                        crate::app::MessageKind::Info,
                    );
                }
            }
        }
        KeyCode::Enter => {
            if action_count == 0 {
                return;
            }
            let selected = app.action_menu_selected.min(action_count - 1);
            let item = actions[selected].to_string();
            handle_action_select(app, &item);
        }
        _ => {}
    }
}

fn handle_action_select(app: &mut App, action: &str) {
    match action {
        "Install" | "Continue Install" => {
            app.is_update = false;
            app.set_message(
                "⠋ Connecting to remote host...",
                crate::app::MessageKind::Info,
            );
            app.pending_resume_install = true;
        }
        "Update" => {
            app.pending_update_confirm = true;
            app.update_confirm_input.clear();
        }
        "Continue Install (Web)" => {
            app.set_message(
                "⠋ Syncing code to remote host...",
                crate::app::MessageKind::Info,
            );
            app.pending_sync_admin_login = true;
        }
        "Clean Install" => {
            app.pending_clean_install_confirm = true;
        }
        "Admin Login" => {
            app.admin_login_magic_link = None;
            app.admin_login_totp_code = None;
            app.admin_login_verify_url = None;
            app.admin_login_error = None;
            app.admin_login_loading = true;
            app.admin_login_use_setup = app.deployment_state != crate::app::DeploymentState::Complete;
            app.screen = Screen::AdminLogin;
            app.pending_admin_login = true;
        }
        "Manage Services" => {
            app.screen = Screen::Manage;
            app.menu_selected = 0;
        }
        "Benchmark Models" => {
            crate::screens::model_benchmark::init_screen(app, None);
            app.screen = Screen::ModelBenchmark;
        }
        _ => {}
    }
}

/// Trigger health checks for the active profile.
pub fn trigger_health_checks(app: &mut App) {
    use crate::modules::hardware::LlmBackend;
    use crate::screens::install::env_to_prefix;

    let profile = match app.active_profile() {
        Some((_, p)) => p.clone(),
        None => {
            app.deployment_state = DeploymentState::None;
            return;
        }
    };

    let prefix = env_to_prefix(&profile.environment);

    let is_remote = profile.remote;
    let is_proxmox = profile.backend == "proxmox";
    let is_mlx = profile
        .hardware
        .as_ref()
        .map(|h| matches!(h.llm_backend, LlmBackend::Mlx))
        .unwrap_or(false);

    let host = if is_remote {
        profile.effective_host().unwrap_or("localhost").to_string()
    } else {
        "localhost".to_string()
    };

    let ssh_details = if is_remote {
        let ssh_host = profile.effective_host().unwrap_or("localhost").to_string();
        let ssh_user = profile.effective_user().to_string();
        let ssh_key = profile.effective_ssh_key().to_string();
        Some((ssh_host, ssh_user, ssh_key))
    } else {
        None
    };

    // Initialize checking state
    app.health_check_running = true;
    app.deployment_state = DeploymentState::Checking;

    let defs = health::all_service_defs(is_mlx);
    app.health_results = defs
        .iter()
        .map(|d| health::ServiceHealthResult {
            name: d.name.to_string(),
            group: d.group.to_string(),
            status: HealthStatus::Checking,
        })
        .collect();
    app.health_groups = health::aggregate_groups(&app.health_results);

    let network_base = profile.effective_network_base().to_string();
    let vllm_network_base = profile.vllm_network_base().to_string();
    let rx = health::start_health_checks(is_remote, is_mlx, &host, &prefix, ssh_details, is_proxmox, &network_base, &vllm_network_base);
    app.health_rx = Some(rx);
}

/// Process health check results from the receiver. Called from the main loop.
pub fn process_health_updates(app: &mut App) {
    if let Some(rx) = app.health_rx.take() {
        use std::sync::mpsc::TryRecvError;
        let mut put_back = true;

        loop {
            match rx.try_recv() {
                Ok(HealthUpdate::ServiceResult(result)) => {
                    if let Some(existing) = app
                        .health_results
                        .iter_mut()
                        .find(|r| r.name == result.name)
                    {
                        existing.status = result.status;
                    }
                    app.health_groups = health::aggregate_groups(&app.health_results);
                    app.deployment_state =
                        health::deployment_state_from_health(&app.health_results);
                }
                Ok(HealthUpdate::Complete) => {
                    app.health_check_running = false;
                    app.health_groups = health::aggregate_groups(&app.health_results);
                    app.deployment_state =
                        health::deployment_state_from_health(&app.health_results);
                    put_back = false;
                    break;
                }
                Err(TryRecvError::Empty) => break,
                Err(TryRecvError::Disconnected) => {
                    app.health_check_running = false;
                    put_back = false;
                    break;
                }
            }
        }

        if put_back {
            app.health_rx = Some(rx);
        }
    }
}

/// Load the active tier's model list for display on the welcome screen.
pub fn load_active_tier_models(app: &mut App) {
    use crate::modules::models::TierModelSet;

    let profile = match app.active_profile() {
        Some((_, p)) => p.clone(),
        None => {
            app.active_tier_models = None;
            return;
        }
    };

    let hw = if profile.remote {
        app.remote_hardware
            .as_ref()
            .or(profile.hardware.as_ref())
    } else {
        app.local_hardware.as_ref()
    };
    let hw = match hw {
        Some(h) => h,
        None => {
            app.active_tier_models = None;
            return;
        }
    };

    let tier = profile.effective_model_tier().unwrap_or(hw.memory_tier);
    let backend = &hw.llm_backend;
    let config_path = app
        .repo_root
        .join("provision/ansible/group_vars/all/model_registry.yml");

    app.active_tier_models = TierModelSet::from_config(&config_path, tier, backend).ok();

    // Also trigger deployed model loading for hybrid dashboard
    trigger_deployed_model_loading(app);
}

/// Check model cache status for the active profile.
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
        app.remote_hardware
            .as_ref()
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
    let config_path = app
        .repo_root
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
        let host = match profile.effective_host() {
            Some(h) => h.to_string(),
            None => {
                app.model_cache_check_state = ModelCacheCheckState::Failed;
                return;
            }
        };
        let user = profile.effective_user().to_string();
        let key = profile.effective_ssh_key().to_string();

        let ssh = crate::modules::ssh::SshConnection::new(&host, &user, &key);

        let model_list: Vec<(String, String)> = rec
            .models()
            .iter()
            .filter(|m| !m.name.is_empty())
            .map(|m| (m.name.clone(), m.role.clone()))
            .collect();

        let results = models::check_remote_model_cache(&ssh, &model_list);
        app.model_cache_status = results
            .into_iter()
            .map(|(name, role, cached)| ModelCacheEntry {
                name,
                role,
                cached,
            })
            .collect();
    } else {
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

/// Trigger loading of deployed models from model_config.yml + live vLLM status.
pub fn trigger_deployed_model_loading(app: &mut App) {
    use crate::modules::hardware::LlmBackend;
    use crate::modules::models;

    let profile = match app.active_profile() {
        Some((_, p)) => p.clone(),
        None => {
            app.deployed_models = None;
            return;
        }
    };

    let is_remote = profile.remote;
    let is_proxmox = profile.backend == "proxmox";
    let effective_hw = if is_remote {
        app.remote_hardware
            .as_ref()
            .or(profile.hardware.as_ref())
    } else {
        app.local_hardware
            .as_ref()
            .or(profile.hardware.as_ref())
    };
    let is_mlx = effective_hw
        .map(|h| matches!(h.llm_backend, LlmBackend::Mlx))
        .unwrap_or(false);

    let ssh_details = if is_remote {
        let ssh_host = profile.effective_host().unwrap_or("localhost").to_string();
        let ssh_user = profile.effective_user().to_string();
        let ssh_key = profile.effective_ssh_key().to_string();
        Some((ssh_host, ssh_user, ssh_key))
    } else {
        None
    };

    app.deployed_models_loading = true;

    if is_mlx {
        let rx = models::start_mlx_model_loading(is_remote, ssh_details);
        app.deployed_models_rx = Some(rx);
    } else {
        let vllm_network_base = profile.vllm_network_base().to_string();
        let rx = models::start_deployed_model_loading(
            app.repo_root.clone(),
            is_remote,
            is_proxmox,
            ssh_details,
            vllm_network_base,
        );
        app.deployed_models_rx = Some(rx);
    }
}

/// Process deployed model updates from the background thread. Called from the main loop.
pub fn process_deployed_model_updates(app: &mut App) {
    use crate::modules::models::DeployedModelUpdate;

    if let Some(rx) = app.deployed_models_rx.take() {
        use std::sync::mpsc::TryRecvError;
        let mut put_back = true;

        loop {
            match rx.try_recv() {
                Ok(DeployedModelUpdate::ConfigLoaded(model_set)) => {
                    app.deployed_models = Some(model_set);
                }
                Ok(DeployedModelUpdate::ModelStatus { port, status }) => {
                    if let Some(ref mut ms) = app.deployed_models {
                        if let Some(model) = ms.models.iter_mut().find(|m| m.port == port) {
                            model.live_status = status;
                        }
                    }
                }
                Ok(DeployedModelUpdate::Complete) => {
                    app.deployed_models_loading = false;
                    put_back = false;
                    break;
                }
                Err(TryRecvError::Empty) => break,
                Err(TryRecvError::Disconnected) => {
                    app.deployed_models_loading = false;
                    put_back = false;
                    break;
                }
            }
        }

        if put_back {
            app.deployed_models_rx = Some(rx);
        }
    }
}
