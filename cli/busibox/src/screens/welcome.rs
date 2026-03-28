use crate::app::{App, DeploymentState, K8sClusterStatus, K8sClusterUpdate, ModelCacheCheckState, ModelCacheEntry, Screen};
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
            let is_k8s = app.active_profile()
                .map(|(_, p)| p.backend == "k8s")
                .unwrap_or(false);
            let hint = if is_k8s {
                " ↑/↓ Navigate  Enter Select  r Refresh  x Export  p Profiles  q Quit"
            } else {
                " ↑/↓ Navigate  Enter Select  t Tunnel  s Sync  r Refresh  x Export  m Models  p Profiles  q Quit"
            };
            Span::styled(hint, theme::muted())
        };
        let status = Paragraph::new(Line::from(status_text));
        f.render_widget(status, chunks[3]);
    }
}

fn render_system_info(f: &mut Frame, app: &App, area: Rect) {
    let is_k8s = app.active_profile()
        .map(|(_, p)| p.backend == "k8s")
        .unwrap_or(false);

    let info_lines = if is_k8s {
        render_k8s_system_info(app)
    } else {
        render_docker_proxmox_system_info(app)
    };

    let info_height = area.height.saturating_sub(2) as usize;
    let info_lines_len = info_lines.len();
    let scroll_y = if info_lines_len > info_height {
        (info_lines_len - info_height) as u16
    } else {
        0
    };

    let title = if is_k8s { " Cluster " } else { " System " };
    let info_block = Paragraph::new(info_lines)
        .block(
            Block::default()
                .borders(Borders::ALL)
                .border_style(theme::dim())
                .title(title)
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

fn render_k8s_system_info(app: &App) -> Vec<Line<'_>> {
    let mut lines = Vec::new();

    // Cluster connection status
    lines.push(Line::from(Span::styled("Connection", theme::heading())));
    lines.push(Line::from(""));

    let (icon, status_text, status_style) = match &app.k8s_cluster_status {
        K8sClusterStatus::Unknown => ("○", "Not checked", theme::dim()),
        K8sClusterStatus::Checking => ("⠋", "Connecting...", theme::info()),
        K8sClusterStatus::Connected => ("●", "Connected", theme::success()),
        K8sClusterStatus::Disconnected => ("●", "Disconnected", theme::error()),
        K8sClusterStatus::Error(_) => ("●", "Error", theme::error()),
    };
    lines.push(Line::from(vec![
        Span::styled(format!("  {icon} "), status_style),
        Span::styled("Status:  ", theme::muted()),
        Span::styled(status_text, status_style),
    ]));

    if let K8sClusterStatus::Error(ref e) = app.k8s_cluster_status {
        let short = if e.len() > 35 { &e[..35] } else { e };
        lines.push(Line::from(vec![
            Span::styled("           ", theme::muted()),
            Span::styled(short, theme::error()),
        ]));
    }

    // Cluster details from profile
    if let Some((_, profile)) = app.active_profile() {
        let overlay = profile.k8s_overlay.as_deref().unwrap_or("rackspace-spot");
        let kubeconfig = profile.kubeconfig.as_deref().unwrap_or("default");
        let kc_display = kubeconfig.rsplit('/').next().unwrap_or(kubeconfig);

        lines.push(Line::from(vec![
            Span::styled("  Overlay: ", theme::muted()),
            Span::styled(overlay, theme::info()),
        ]));
        lines.push(Line::from(vec![
            Span::styled("  Config:  ", theme::muted()),
            Span::styled(kc_display, theme::normal()),
        ]));
    }

    // Node information from cluster check
    if let Some(ref info) = app.k8s_cluster_info {
        lines.push(Line::from(""));
        lines.push(Line::from(Span::styled("Nodes", theme::heading())));
        if info.node_info.is_empty() {
            lines.push(Line::from(Span::styled(
                format!("  {} node(s)", info.node_count),
                theme::normal(),
            )));
        } else {
            for node in &info.node_info {
                let (icon, style) = if node.status == "Ready" {
                    ("●", theme::success())
                } else {
                    ("●", theme::warning())
                };
                let name_display = if node.name.len() > 20 {
                    format!("{}…", &node.name[..19])
                } else {
                    node.name.clone()
                };
                lines.push(Line::from(vec![
                    Span::styled(format!("  {icon} "), style),
                    Span::styled(name_display, theme::normal()),
                ]));
                lines.push(Line::from(vec![
                    Span::styled("      ", theme::muted()),
                    Span::styled(&node.status, style),
                    Span::styled("  ", theme::muted()),
                    Span::styled(&node.version, theme::dim()),
                ]));
            }
        }

        if !info.server_url.is_empty() {
            lines.push(Line::from(""));
            let url_display = if info.server_url.len() > 30 {
                format!("{}…", &info.server_url[..29])
            } else {
                info.server_url.clone()
            };
            lines.push(Line::from(vec![
                Span::styled("  Server: ", theme::muted()),
                Span::styled(url_display, theme::dim()),
            ]));
        }
    }

    // Profile info
    if let Some((id, profile)) = app.active_profile() {
        lines.push(Line::from(""));
        lines.push(Line::from(Span::styled("Profile", theme::heading())));
        lines.push(Line::from(vec![
            Span::styled("  Name:  ", theme::muted()),
            Span::styled(id, theme::info()),
        ]));
        lines.push(Line::from(vec![
            Span::styled("  Env:   ", theme::muted()),
            Span::styled(&profile.environment, theme::normal()),
        ]));
        if profile.spot_token.as_ref().map(|t| !t.is_empty()).unwrap_or(false) {
            lines.push(Line::from(vec![
                Span::styled("  Spot:  ", theme::muted()),
                Span::styled("configured", theme::success()),
            ]));
        }
    }

    lines
}

fn render_docker_proxmox_system_info(app: &App) -> Vec<Line<'_>> {
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
            } else if entry.downloading {
                ("↓", theme::info())
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
        let downloading_count = app.model_cache_status.iter().filter(|e| e.downloading).count();
        let total_count = app.model_cache_status.len();
        if cached_count == total_count {
            info_lines.push(Line::from(Span::styled(
                "  All models ready",
                theme::success(),
            )));
        } else if downloading_count > 0 {
            info_lines.push(Line::from(Span::styled(
                format!("  Downloading... {}/{} cached", cached_count, total_count),
                theme::info(),
            )));
        } else if app.model_bg_download_active {
            info_lines.push(Line::from(Span::styled(
                format!("  Queued for download... {}/{} cached", cached_count, total_count),
                theme::dim(),
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

    info_lines
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
    let is_k8s = app.active_profile()
        .map(|(_, p)| p.backend == "k8s")
        .unwrap_or(false);

    if is_k8s {
        render_k8s_status_panel(f, app, area);
        return;
    }

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
        let expected_label = if tier_set
            .tier_description
            .to_ascii_lowercase()
            .contains("custom")
        {
            "Custom".to_string()
        } else {
            tier_set.tier.to_string()
        };
        lines.push(Line::from(Span::styled(
            format!("Expected Models ({expected_label})"),
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

fn render_k8s_status_panel(f: &mut Frame, app: &App, area: Rect) {
    let mut lines: Vec<Line> = Vec::new();
    let tick = app.health_tick;
    let spinner_char = SPINNER[tick % SPINNER.len()];

    match &app.k8s_cluster_status {
        K8sClusterStatus::Unknown => {
            lines.push(Line::from(Span::styled(
                "  Press 'r' to check cluster status",
                theme::muted(),
            )));
        }
        K8sClusterStatus::Checking => {
            lines.push(Line::from(vec![
                Span::styled(format!("  {spinner_char} "), theme::info()),
                Span::styled("Checking cluster...", theme::muted()),
            ]));
        }
        K8sClusterStatus::Connected => {
            lines.push(Line::from(vec![
                Span::styled("  ● ", theme::success()),
                Span::styled("Cluster reachable", theme::success()),
            ]));

            if let Some(ref info) = app.k8s_cluster_info {
                if let Some(ref summary) = info.pod_summary {
                    lines.push(Line::from(""));
                    lines.push(Line::from(Span::styled("Pod Status", theme::heading())));
                    for line in summary.lines() {
                        lines.push(Line::from(Span::styled(
                            format!("  {line}"),
                            theme::normal(),
                        )));
                    }
                }
            }
        }
        K8sClusterStatus::Disconnected => {
            lines.push(Line::from(vec![
                Span::styled("  ● ", theme::error()),
                Span::styled("Cluster unreachable", theme::error()),
            ]));
            lines.push(Line::from(""));
            lines.push(Line::from(Span::styled(
                "  Check kubeconfig and network.",
                theme::muted(),
            )));
            lines.push(Line::from(Span::styled(
                "  Press 'r' to retry.",
                theme::muted(),
            )));
        }
        K8sClusterStatus::Error(ref e) => {
            lines.push(Line::from(vec![
                Span::styled("  ● ", theme::error()),
                Span::styled("Connection error", theme::error()),
            ]));
            lines.push(Line::from(""));
            let display = if e.len() > 50 {
                format!("  {}…", &e[..49])
            } else {
                format!("  {e}")
            };
            lines.push(Line::from(Span::styled(display, theme::dim())));
            lines.push(Line::from(Span::styled(
                "  Press 'r' to retry.",
                theme::muted(),
            )));
        }
    }

    // HTTPS tunnel status for K8s
    if app.ssh_tunnel_active {
        lines.push(Line::from(""));
        lines.push(Line::from(vec![
            Span::styled("  🔗 ", theme::success()),
            Span::styled("HTTPS tunnel active", theme::success()),
        ]));
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
            if app.active_profile().is_some() {
                if app.vault_password.is_some() {
                    app.pending_local_export = true;
                } else {
                    app.set_message(
                        "Unlock vault first (select profile)",
                        crate::app::MessageKind::Info,
                    );
                }
            }
        }
        KeyCode::Char('X') => {
            if let Some((_, profile)) = app.active_profile() {
                if profile.remote && app.vault_password.is_some() {
                    app.pending_profile_export = true;
                } else if !profile.remote {
                    app.set_message(
                        "Export to host is only for remote profiles",
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
        "K8s Manage" | "Manage" => {
            app.k8s_manage_selected = 0;
            app.k8s_manage_log_visible = false;
            app.k8s_manage_action_running = false;
            app.k8s_manage_action_complete = false;
            app.screen = Screen::K8sManage;
        }
        "Validate Secrets" => {
            app.set_message(
                "⠋ Validating vault secrets...",
                crate::app::MessageKind::Info,
            );
            app.pending_compare_secrets = true;
        }
        "Setup mkcert (TLS certs)" => {
            app.set_message(
                "⠋ Installing mkcert...",
                crate::app::MessageKind::Info,
            );
            app.pending_mkcert_setup = true;
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

    // K8s profiles use kubectl-based cluster checks instead of Docker health checks
    if profile.backend == "k8s" {
        trigger_k8s_cluster_check(app);
        return;
    }

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

/// Trigger K8s cluster status check using kubectl.
pub fn trigger_k8s_cluster_check(app: &mut App) {
    use crate::app::{K8sClusterInfo, K8sClusterUpdate, K8sNodeInfo};

    let profile = match app.active_profile() {
        Some((_, p)) => p.clone(),
        None => return,
    };

    app.k8s_cluster_status = K8sClusterStatus::Checking;
    app.deployment_state = DeploymentState::Checking;

    let (tx, rx) = std::sync::mpsc::channel::<K8sClusterUpdate>();
    app.k8s_cluster_rx = Some(rx);

    let kubeconfig = profile.kubeconfig.clone();
    let repo_root = app.repo_root.clone();

    std::thread::spawn(move || {
        let kc_path = kubeconfig.unwrap_or_else(|| {
            repo_root
                .join("k8s/kubeconfig-rackspace-spot.yaml")
                .display()
                .to_string()
        });

        let kctl = |args: &str| -> Result<String, String> {
            let mut cmd_args: Vec<&str> = args.split_whitespace().collect();
            // Add a default timeout to prevent hanging on slow/unreachable clusters
            let timeout_flag = "--request-timeout=15s".to_string();
            if !cmd_args.iter().any(|a| a.starts_with("--request-timeout")) {
                cmd_args.push(&timeout_flag);
            }
            let output = std::process::Command::new("kubectl")
                .args(&cmd_args)
                .env("KUBECONFIG", &kc_path)
                .output()
                .map_err(|e| format!("kubectl not found: {e}"))?;
            if output.status.success() {
                Ok(String::from_utf8_lossy(&output.stdout).trim().to_string())
            } else {
                Err(String::from_utf8_lossy(&output.stderr).trim().to_string())
            }
        };

        // Test connectivity
        match kctl("cluster-info") {
            Ok(info) => {
                let _ = tx.send(K8sClusterUpdate::Status(K8sClusterStatus::Connected));

                // Extract server URL from cluster-info
                let server_url = info
                    .lines()
                    .find(|l| l.contains("control plane") || l.contains("master"))
                    .and_then(|l| l.split("at ").nth(1))
                    .map(|s| {
                        // Strip ANSI color codes
                        let re_ansi = |s: &str| -> String {
                            let mut out = String::new();
                            let mut in_escape = false;
                            for c in s.chars() {
                                if c == '\x1b' {
                                    in_escape = true;
                                } else if in_escape {
                                    if c.is_ascii_alphabetic() {
                                        in_escape = false;
                                    }
                                } else {
                                    out.push(c);
                                }
                            }
                            out
                        };
                        re_ansi(s).trim().to_string()
                    })
                    .unwrap_or_default();

                // Get nodes
                let mut nodes = Vec::new();
                let mut node_count = 0;
                if let Ok(node_output) = kctl("get nodes -o wide --no-headers") {
                    for line in node_output.lines() {
                        let parts: Vec<&str> = line.split_whitespace().collect();
                        if parts.len() >= 5 {
                            node_count += 1;
                            nodes.push(K8sNodeInfo {
                                name: parts[0].to_string(),
                                status: parts[1].to_string(),
                                roles: parts[2].to_string(),
                                version: parts[4].to_string(),
                            });
                        }
                    }
                }

                // Get pod summary for busibox namespace
                let pod_summary = kctl("get pods -n busibox --no-headers").ok().map(|out| {
                    let lines: Vec<&str> = out.lines().collect();
                    let total = lines.len();
                    let running = lines.iter().filter(|l| l.contains("Running")).count();
                    let pending = lines.iter().filter(|l| l.contains("Pending")).count();
                    let failed = lines
                        .iter()
                        .filter(|l| {
                            l.contains("Error")
                                || l.contains("CrashLoopBackOff")
                                || l.contains("Failed")
                        })
                        .count();

                    let mut summary = format!("{running}/{total} running");
                    if pending > 0 {
                        summary.push_str(&format!(", {pending} pending"));
                    }
                    if failed > 0 {
                        summary.push_str(&format!(", {failed} failing"));
                    }
                    summary
                });

                let cluster_info = K8sClusterInfo {
                    server_url,
                    cluster_name: String::new(),
                    namespace: "busibox".to_string(),
                    node_count,
                    node_info: nodes,
                    pod_summary,
                };
                let _ = tx.send(K8sClusterUpdate::Info(cluster_info));

                // Set deployment state based on pod presence
                let has_pods = kctl("get pods -n busibox --no-headers")
                    .map(|o| !o.trim().is_empty())
                    .unwrap_or(false);
                if has_pods {
                    let _ = tx.send(K8sClusterUpdate::Status(K8sClusterStatus::Connected));
                }
            }
            Err(e) => {
                if e.contains("connect") || e.contains("refused") || e.contains("timeout") {
                    let _ = tx.send(K8sClusterUpdate::Status(K8sClusterStatus::Disconnected));
                } else {
                    let _ = tx.send(K8sClusterUpdate::Status(K8sClusterStatus::Error(e)));
                }
            }
        }

        let _ = tx.send(K8sClusterUpdate::Complete);
    });
}

/// Process K8s cluster check updates from background thread.
pub fn process_k8s_cluster_updates(app: &mut App) {
    if let Some(rx) = app.k8s_cluster_rx.take() {
        use std::sync::mpsc::TryRecvError;
        let mut put_back = true;

        loop {
            match rx.try_recv() {
                Ok(K8sClusterUpdate::Status(status)) => {
                    match &status {
                        K8sClusterStatus::Connected => {
                            app.deployment_state = DeploymentState::Complete;
                        }
                        K8sClusterStatus::Disconnected | K8sClusterStatus::Error(_) => {
                            app.deployment_state = DeploymentState::None;
                        }
                        _ => {}
                    }
                    app.k8s_cluster_status = status;
                }
                Ok(K8sClusterUpdate::Info(info)) => {
                    app.k8s_cluster_info = Some(info);
                }
                Ok(K8sClusterUpdate::Complete) => {
                    put_back = false;
                    break;
                }
                Err(TryRecvError::Empty) => break,
                Err(TryRecvError::Disconnected) => {
                    put_back = false;
                    break;
                }
            }
        }

        if put_back {
            app.k8s_cluster_rx = Some(rx);
        }
    }
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
    let deployed_model_config_path = app
        .repo_root
        .join("provision/ansible/group_vars/all/model_config.yml");

    let explicit_custom = profile
        .model_tier
        .as_deref()
        .map(|t| t.eq_ignore_ascii_case("custom"))
        .unwrap_or(false);

    app.active_tier_models = if explicit_custom
        && deployed_model_config_path.exists()
        && config_path.exists()
    {
        TierModelSet::from_deployed_config(
            &deployed_model_config_path,
            &config_path,
            backend,
        )
        .ok()
    } else {
        TierModelSet::from_config(&config_path, tier, backend).ok()
    };

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
                downloading: false,
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
                    downloading: false,
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

/// If the model cache check is done and there are uncached models, start
/// downloading them in the background. Skips if downloads are already active.
pub fn start_missing_model_downloads(app: &mut App) {
    use crate::modules::models;

    if app.model_bg_download_active {
        return;
    }
    if app.model_cache_check_state != ModelCacheCheckState::Done {
        return;
    }

    let missing: Vec<(String, String)> = app
        .model_cache_status
        .iter()
        .filter(|e| !e.cached && !e.name.is_empty())
        .map(|e| (e.name.clone(), e.role.clone()))
        .collect();

    if missing.is_empty() {
        return;
    }

    let profile = match app.active_profile() {
        Some((_, p)) => p.clone(),
        None => return,
    };

    let is_remote = profile.remote;
    let ssh_details = if is_remote {
        match profile.effective_host() {
            Some(host) => Some((
                host.to_string(),
                profile.effective_user().to_string(),
                profile.effective_ssh_key().to_string(),
            )),
            None => return,
        }
    } else {
        None
    };
    let remote_path = profile.effective_remote_path().to_string();

    let rx = models::start_background_downloads(
        missing,
        app.repo_root.clone(),
        is_remote,
        ssh_details,
        remote_path,
    );

    app.model_bg_download_rx = Some(rx);
    app.model_bg_download_active = true;
}

/// Process background model download updates. Called from the main loop tick.
pub fn process_model_download_updates(app: &mut App) {
    use crate::modules::models::ModelDownloadUpdate;

    if let Some(rx) = app.model_bg_download_rx.take() {
        use std::sync::mpsc::TryRecvError;
        let mut put_back = true;

        loop {
            match rx.try_recv() {
                Ok(ModelDownloadUpdate::Started { model_name, .. }) => {
                    for entry in &mut app.model_cache_status {
                        if entry.name == model_name {
                            entry.downloading = true;
                        }
                    }
                }
                Ok(ModelDownloadUpdate::Complete { model_name }) => {
                    for entry in &mut app.model_cache_status {
                        if entry.name == model_name {
                            entry.cached = true;
                            entry.downloading = false;
                        }
                    }
                }
                Ok(ModelDownloadUpdate::Failed { model_name, .. }) => {
                    for entry in &mut app.model_cache_status {
                        if entry.name == model_name {
                            entry.downloading = false;
                        }
                    }
                }
                Ok(ModelDownloadUpdate::AllDone) => {
                    app.model_bg_download_active = false;
                    put_back = false;
                    break;
                }
                Err(TryRecvError::Empty) => break,
                Err(TryRecvError::Disconnected) => {
                    app.model_bg_download_active = false;
                    put_back = false;
                    break;
                }
            }
        }

        if put_back {
            app.model_bg_download_rx = Some(rx);
        }
    }
}
