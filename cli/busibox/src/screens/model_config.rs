use crate::app::{App, InputMode, MessageKind, Screen, SetupTarget};
use crate::modules::hardware::MemoryTier;
use crate::modules::models::ModelRecommendation;
use crate::theme;
use crossterm::event::{KeyCode, KeyEvent};
use ratatui::prelude::*;
use ratatui::widgets::*;
use std::collections::HashMap;
use std::path::Path;

const FIELD_TIER: usize = 0;
const FIELD_LLM_MODE: usize = 1;
const FIELD_CLOUD_PROVIDER: usize = 2;
const FIELD_CLOUD_API_KEY: usize = 3;
const FIELD_ADMIN_EMAIL: usize = 4;
const FIELD_SITE_DOMAIN: usize = 5;
const FIELD_SSL_CERT: usize = 6;

const LLM_MODES: &[&str] = &["Local (auto-detected)", "Cloud (API)"];
const CLOUD_PROVIDERS: &[&str] = &["OpenAI", "Anthropic", "Bedrock"];

pub fn render(f: &mut Frame, app: &App) {
    let is_cloud = app.llm_mode_choice == 1;
    let cloud_rows: u16 = if is_cloud { 6 } else { 3 };

    let chunks = Layout::default()
        .direction(Direction::Vertical)
        .constraints([
            Constraint::Length(3),        // title
            Constraint::Min(10),          // tier list + details
            Constraint::Length(cloud_rows), // LLM mode + cloud fields
            Constraint::Length(3),        // admin email
            Constraint::Length(3),        // site domain
            Constraint::Length(3),        // ssl cert
            Constraint::Length(3),        // help
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

    render_llm_mode_section(f, app, chunks[2]);

    render_text_input(
        f,
        chunks[3],
        " Admin Email ",
        &app.admin_email_input,
        "admin@example.com",
        app.model_config_input_cursor == FIELD_ADMIN_EMAIL,
    );
    render_text_input(
        f,
        chunks[4],
        " Site Domain ",
        &app.site_domain_input,
        "localhost",
        app.model_config_input_cursor == FIELD_SITE_DOMAIN,
    );
    let cert_display = if app.ssl_cert_name_input.is_empty() {
        "(auto-detect from domain)".to_string()
    } else {
        app.ssl_cert_name_input.clone()
    };
    render_picker_input(
        f,
        chunks[5],
        " SSL Certificate (from ssl/) ",
        &cert_display,
        app.model_config_input_cursor == FIELD_SSL_CERT,
    );

    let help = if app.model_config_input_cursor == FIELD_TIER {
        Paragraph::new(Line::from(vec![
            Span::styled("↑/↓ ", theme::highlight()),
            Span::styled("Select tier  ", theme::normal()),
            Span::styled("Enter ", theme::highlight()),
            Span::styled("Confirm & Install  ", theme::normal()),
            Span::styled("Tab ", theme::highlight()),
            Span::styled("Edit fields  ", theme::normal()),
            Span::styled("Esc ", theme::muted()),
            Span::styled("Back", theme::muted()),
        ]))
    } else if app.model_config_input_cursor == FIELD_LLM_MODE
        || app.model_config_input_cursor == FIELD_CLOUD_PROVIDER
    {
        Paragraph::new(Line::from(vec![
            Span::styled("←/→ ", theme::highlight()),
            Span::styled("Cycle  ", theme::normal()),
            Span::styled("Tab ", theme::highlight()),
            Span::styled("Next field  ", theme::normal()),
            Span::styled("Enter ", theme::highlight()),
            Span::styled("Back to tiers  ", theme::normal()),
            Span::styled("Esc ", theme::muted()),
            Span::styled("Back", theme::muted()),
        ]))
    } else {
        Paragraph::new(Line::from(vec![
            Span::styled("Type/Edit  ", theme::normal()),
            Span::styled("←/→ ", theme::highlight()),
            Span::styled("Cycle  ", theme::normal()),
            Span::styled("Tab ", theme::highlight()),
            Span::styled("Next field  ", theme::normal()),
            Span::styled("Enter ", theme::highlight()),
            Span::styled("Back to tiers  ", theme::normal()),
            Span::styled("Esc ", theme::muted()),
            Span::styled("Back", theme::muted()),
        ]))
    };
    f.render_widget(help, chunks[6]);
}

fn render_llm_mode_section(f: &mut Frame, app: &App, area: Rect) {
    let is_cloud = app.llm_mode_choice == 1;

    if is_cloud {
        let rows = Layout::default()
            .direction(Direction::Vertical)
            .constraints([
                Constraint::Length(3), // LLM mode picker
                Constraint::Length(3), // cloud provider + api key
            ])
            .split(area);

        render_picker_input(
            f,
            rows[0],
            " LLM Mode ",
            LLM_MODES[app.llm_mode_choice.min(1)],
            app.model_config_input_cursor == FIELD_LLM_MODE,
        );

        let cloud_cols = Layout::default()
            .direction(Direction::Horizontal)
            .constraints([Constraint::Percentage(40), Constraint::Percentage(60)])
            .split(rows[1]);

        render_picker_input(
            f,
            cloud_cols[0],
            " Provider ",
            CLOUD_PROVIDERS[app.cloud_provider_choice.min(2)],
            app.model_config_input_cursor == FIELD_CLOUD_PROVIDER,
        );

        let masked_key = if app.cloud_api_key_input.is_empty() {
            String::new()
        } else if app.model_config_input_cursor == FIELD_CLOUD_API_KEY {
            app.cloud_api_key_input.clone()
        } else {
            let len = app.cloud_api_key_input.len();
            if len <= 8 {
                "*".repeat(len)
            } else {
                format!("{}...{}", &app.cloud_api_key_input[..4], &app.cloud_api_key_input[len-4..])
            }
        };

        render_text_input(
            f,
            cloud_cols[1],
            " API Key ",
            &masked_key,
            "sk-... or access key",
            app.model_config_input_cursor == FIELD_CLOUD_API_KEY,
        );
    } else {
        render_picker_input(
            f,
            area,
            " LLM Mode ",
            LLM_MODES[app.llm_mode_choice.min(1)],
            app.model_config_input_cursor == FIELD_LLM_MODE,
        );
    }
}

fn render_text_input(
    f: &mut Frame,
    area: Rect,
    title: &str,
    value: &str,
    placeholder: &str,
    is_focused: bool,
) {
    let border_style = if is_focused {
        theme::highlight()
    } else {
        theme::dim()
    };

    let content = if is_focused {
        let display = if value.is_empty() {
            "▎".to_string()
        } else {
            format!("{value}▎")
        };
        Line::from(Span::styled(display, theme::normal()))
    } else {
        let display = if value.is_empty() {
            placeholder
        } else {
            value
        };
        Line::from(Span::styled(display, theme::muted()))
    };

    let block = Block::default()
        .borders(Borders::ALL)
        .border_style(border_style)
        .title(title)
        .title_style(theme::heading());

    let paragraph = Paragraph::new(content).block(block);
    f.render_widget(paragraph, area);
}

fn render_picker_input(
    f: &mut Frame,
    area: Rect,
    title: &str,
    value: &str,
    is_focused: bool,
) {
    let border_style = if is_focused {
        theme::highlight()
    } else {
        theme::dim()
    };
    let display = if is_focused {
        format!("< {value} >")
    } else {
        value.to_string()
    };
    let paragraph = Paragraph::new(Line::from(Span::styled(
        display,
        if is_focused {
            theme::normal()
        } else {
            theme::muted()
        },
    )))
    .block(
        Block::default()
            .borders(Borders::ALL)
            .border_style(border_style)
            .title(title)
            .title_style(theme::heading()),
    );
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

/// Advance to the next field, skipping cloud-only fields when in local mode.
fn next_field(current: usize, is_cloud: bool) -> usize {
    let next = current + 1;
    if !is_cloud && (next == FIELD_CLOUD_PROVIDER || next == FIELD_CLOUD_API_KEY) {
        FIELD_ADMIN_EMAIL
    } else {
        next.min(FIELD_SSL_CERT)
    }
}

pub fn handle_key(app: &mut App, key: KeyEvent) {
    let tier_count = MemoryTier::all().len();
    let cert_options = ssl_cert_options(&app.repo_root);
    let is_cloud = app.llm_mode_choice == 1;

    if app.model_config_input_cursor != FIELD_TIER {
        match key.code {
            KeyCode::Tab => {
                app.model_config_input_cursor = next_field(app.model_config_input_cursor, is_cloud);
            }
            KeyCode::Esc => {
                app.model_config_input_cursor = FIELD_TIER;
                app.input_mode = InputMode::Normal;
            }
            KeyCode::Enter => {
                app.model_config_input_cursor = FIELD_TIER;
                app.input_mode = InputMode::Normal;
            }
            KeyCode::Left => {
                match app.model_config_input_cursor {
                    FIELD_SSL_CERT => cycle_ssl_cert(app, &cert_options, false),
                    FIELD_LLM_MODE => {
                        if app.llm_mode_choice > 0 {
                            app.llm_mode_choice -= 1;
                        }
                    }
                    FIELD_CLOUD_PROVIDER => {
                        if app.cloud_provider_choice > 0 {
                            app.cloud_provider_choice -= 1;
                        }
                    }
                    _ => {}
                }
            }
            KeyCode::Right => {
                match app.model_config_input_cursor {
                    FIELD_SSL_CERT => cycle_ssl_cert(app, &cert_options, true),
                    FIELD_LLM_MODE => {
                        if app.llm_mode_choice < LLM_MODES.len() - 1 {
                            app.llm_mode_choice += 1;
                        }
                    }
                    FIELD_CLOUD_PROVIDER => {
                        if app.cloud_provider_choice < CLOUD_PROVIDERS.len() - 1 {
                            app.cloud_provider_choice += 1;
                        }
                    }
                    _ => {}
                }
            }
            KeyCode::Backspace => {
                match app.model_config_input_cursor {
                    FIELD_ADMIN_EMAIL => { app.admin_email_input.pop(); }
                    FIELD_SITE_DOMAIN => { app.site_domain_input.pop(); }
                    FIELD_CLOUD_API_KEY => { app.cloud_api_key_input.pop(); }
                    _ => {}
                }
            }
            KeyCode::Char(c) => {
                match app.model_config_input_cursor {
                    FIELD_ADMIN_EMAIL => app.admin_email_input.push(c),
                    FIELD_SITE_DOMAIN => app.site_domain_input.push(c),
                    FIELD_CLOUD_API_KEY => app.cloud_api_key_input.push(c),
                    _ => {}
                }
            }
            _ => {}
        }
        return;
    }

    match key.code {
        KeyCode::Tab => {
            app.model_config_input_cursor = FIELD_LLM_MODE;
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

fn cycle_ssl_cert(app: &mut App, options: &[String], forward: bool) {
    if options.is_empty() {
        app.ssl_cert_name_input.clear();
        return;
    }
    let current = options
        .iter()
        .position(|v| v == &app.ssl_cert_name_input)
        .unwrap_or(0);
    let next = if forward {
        (current + 1) % options.len()
    } else if current == 0 {
        options.len() - 1
    } else {
        current - 1
    };
    app.ssl_cert_name_input = options[next].clone();
}

fn ssl_cert_options(repo_root: &Path) -> Vec<String> {
    let mut options = vec![String::new()];
    let ssl_dir = repo_root.join("ssl");
    let Ok(entries) = std::fs::read_dir(ssl_dir) else {
        return options;
    };
    let mut certs = Vec::new();
    for entry in entries.flatten() {
        let path = entry.path();
        if path.extension().and_then(|e| e.to_str()) != Some("crt") {
            continue;
        }
        let Some(name) = path.file_stem().and_then(|s| s.to_str()) else {
            continue;
        };
        if name.ends_with(".fullchain") {
            continue;
        }
        let key_path = path.with_extension("key");
        if key_path.exists() {
            certs.push(name.to_string());
        }
    }
    certs.sort();
    certs.dedup();
    options.extend(certs);
    options
}

pub fn load_recommendations(app: &mut App) {
    if app.site_domain_input.trim().is_empty() {
        app.site_domain_input = "localhost".to_string();
    }
    if app.ssl_cert_name_input.trim().is_empty() {
        let options = ssl_cert_options(&app.repo_root);
        if options.iter().any(|v| v == &app.site_domain_input) {
            app.ssl_cert_name_input = app.site_domain_input.clone();
        }
    }

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

    let backend_lower = if backend.contains("K8s") {
        "k8s".to_string()
    } else {
        backend.to_lowercase()
    };
    let host_prefix = if backend_lower == "k8s" {
        app.k8s_overlay_input.trim()
    } else if is_remote {
        app.remote_host_input.as_str()
    } else {
        "local"
    };
    let profile_id = profile::build_profile_id(host_prefix, environment, &backend_lower);

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
        vault_prefix: Some(profile_id.clone()),
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
        kubeconfig: if backend_lower == "k8s" && !app.k8s_kubeconfig_input.trim().is_empty() {
            Some(app.k8s_kubeconfig_input.trim().to_string())
        } else {
            None
        },
        model_tier,
        admin_email: if app.admin_email_input.trim().is_empty() {
            None
        } else {
            Some(app.admin_email_input.trim().to_string())
        },
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
        cloud_provider: if app.llm_mode_choice == 1 {
            let providers = ["openai", "anthropic", "bedrock"];
            Some(providers[app.cloud_provider_choice.min(2)].to_string())
        } else {
            None
        },
        cloud_api_key: if app.llm_mode_choice == 1 && !app.cloud_api_key_input.trim().is_empty() {
            Some(app.cloud_api_key_input.trim().to_string())
        } else {
            None
        },
        llm_backend_override: if app.llm_mode_choice == 1 {
            Some("cloud".to_string())
        } else {
            None
        },
        k8s_overlay: if backend_lower == "k8s" {
            Some(app.k8s_overlay_input.clone())
        } else {
            None
        },
        spot_token: if backend_lower == "k8s" && !app.k8s_spot_token_input.trim().is_empty() {
            Some(app.k8s_spot_token_input.trim().to_string())
        } else {
            None
        },
        dev_apps_dir: None,
        huggingface_token: None,
        direct_access: None,
    };

    match profile::create_profile(&app.repo_root, &profile_id, profile, true) {
        Ok(()) => {
            // Release old lock, acquire lock on the newly-active profile
            app.profile_lock = None;
            match profile::try_lock_profile(&app.repo_root, &profile_id) {
                Ok(Some(lock)) => app.profile_lock = Some(lock),
                Ok(None) | Err(_) => {}
            }
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
