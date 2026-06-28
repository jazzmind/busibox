use crate::app::{App, BrowseStep, Screen, SetupTarget, WizardStep};
use crate::modules::profile::{Profile, build_profile_id, load_profiles, try_lock_profile, upsert_profile};
use crate::theme;
use busibox_core::profiles::{resolve_services, AddonPack, BusiboxProfile, LocalLlmBackend};
use crossterm::event::{KeyCode, KeyEvent};
use ratatui::prelude::*;
use ratatui::widgets::*;
use std::collections::HashMap;

// ---------------------------------------------------------------------------
// Preset definitions
// ---------------------------------------------------------------------------

struct Preset {
    label: &'static str,
    tag: &'static str,
    description: &'static str,
    note: &'static str,
}

const PRESETS: &[Preset] = &[
    Preset {
        label: "Lite",
        tag: "lite",
        description: "Cloud LLM, minimal Docker install",
        note: "Fastest to start. Requires an OpenAI / Anthropic / Bedrock key.",
    },
    Preset {
        label: "Lite + Local LLM",
        tag: "lite+local",
        description: "Cloud LLM + a local model backend of your choice",
        note: "Best of both worlds. Backend selection on next step.",
    },
    Preset {
        label: "Full",
        tag: "full",
        description: "Everything — RAG, local models, graph, all services",
        note: "Requires significant RAM and disk. GPU strongly recommended.",
    },
];

// ---------------------------------------------------------------------------
// Render
// ---------------------------------------------------------------------------

pub fn render(f: &mut Frame, app: &App) {
    let chunks = Layout::default()
        .direction(Direction::Vertical)
        .constraints([
            Constraint::Length(3), // title
            Constraint::Length(2), // subtitle / progress
            Constraint::Min(10),   // content
            Constraint::Length(3), // help
        ])
        .margin(2)
        .split(f.area());

    // Title
    let title = Paragraph::new("Busibox Setup")
        .style(theme::title())
        .alignment(Alignment::Center);
    f.render_widget(title, chunks[0]);

    // Step progress
    let step_label = match app.wizard_step {
        WizardStep::Target      => "Step 1 of 4 — Where will Busibox run?",
        WizardStep::Preset      => "Step 2 of 4 — Choose a service preset",
        WizardStep::LlmBackend  => "Step 2b — Choose a local LLM backend",
        WizardStep::ModelSelect => "Step 3 of 4 — Select an initial model (optional)",
        WizardStep::Confirm     => "Step 4 of 4 — Review and create profile",
    };
    let subtitle = Paragraph::new(step_label)
        .style(theme::muted())
        .alignment(Alignment::Center);
    f.render_widget(subtitle, chunks[1]);

    match app.wizard_step {
        WizardStep::Target      => render_target(f, app, chunks[2]),
        WizardStep::Preset      => render_preset(f, app, chunks[2]),
        WizardStep::LlmBackend  => render_llm_backend(f, app, chunks[2]),
        WizardStep::ModelSelect => render_model_select(f, app, chunks[2]),
        WizardStep::Confirm     => render_confirm(f, app, chunks[2]),
    }

    // Help bar
    let help_text = match app.wizard_step {
        WizardStep::Confirm => " ↑/↓ Navigate  Enter Install  Esc Back",
        _ => " ↑/↓ Navigate  Enter Select  Esc Back",
    };
    let help = Paragraph::new(Line::from(Span::styled(help_text, theme::muted())));
    f.render_widget(help, chunks[3]);
}

fn render_target(f: &mut Frame, app: &App, area: Rect) {
    let choices: &[(&str, &str)] = &[
        ("Local Machine", "Install on this machine using Docker"),
        ("Remote Machine", "Install on a remote host via SSH"),
        ("Import Profile", "Connect to an existing Busibox installation"),
    ];

    let items: Vec<ListItem> = choices
        .iter()
        .enumerate()
        .map(|(i, (title, desc))| {
            let style = if i == app.wizard_target_selected {
                theme::selected()
            } else {
                theme::normal()
            };
            ListItem::new(vec![
                Line::from(Span::styled(format!("  {title}"), style)),
                Line::from(Span::styled(format!("    {desc}"), theme::muted())),
                Line::from(""),
            ])
        })
        .collect();

    let list = List::new(items).block(
        Block::default()
            .borders(Borders::ALL)
            .border_style(theme::dim())
            .title(" Installation Target ")
            .title_style(theme::heading()),
    );
    f.render_widget(list, area);
}

fn render_preset(f: &mut Frame, app: &App, area: Rect) {
    let items: Vec<ListItem> = PRESETS
        .iter()
        .enumerate()
        .map(|(i, preset)| {
            let style = if i == app.wizard_preset_selected {
                theme::selected()
            } else {
                theme::normal()
            };
            ListItem::new(vec![
                Line::from(Span::styled(format!("  {}", preset.label), style)),
                Line::from(Span::styled(
                    format!("    {}", preset.description),
                    theme::muted(),
                )),
                Line::from(Span::styled(format!("    {}", preset.note), theme::info())),
                Line::from(""),
            ])
        })
        .collect();

    let list = List::new(items).block(
        Block::default()
            .borders(Borders::ALL)
            .border_style(theme::dim())
            .title(" Service Preset ")
            .title_style(theme::heading()),
    );
    f.render_widget(list, area);
}

fn render_llm_backend(f: &mut Frame, app: &App, area: Rect) {
    let items: Vec<ListItem> = app
        .wizard_available_backends
        .iter()
        .enumerate()
        .map(|(i, backend_id)| {
            let backend = LocalLlmBackend::all()
                .iter()
                .find(|b| b.id() == backend_id.as_str())
                .copied();
            let (label, desc) = backend
                .map(|b| (b.label(), b.description()))
                .unwrap_or((backend_id.as_str(), ""));
            let style = if i == app.wizard_backend_selected {
                theme::selected()
            } else {
                theme::normal()
            };
            ListItem::new(vec![
                Line::from(Span::styled(format!("  {label}"), style)),
                Line::from(Span::styled(format!("    {desc}"), theme::muted())),
                Line::from(""),
            ])
        })
        .collect();

    let list = List::new(items).block(
        Block::default()
            .borders(Borders::ALL)
            .border_style(theme::dim())
            .title(" Local LLM Backend ")
            .title_style(theme::heading()),
    );
    f.render_widget(list, area);
}

fn render_model_select(f: &mut Frame, app: &App, area: Rect) {
    let engine_hint = engine_for_wizard(app);
    let choices: &[(&str, &str)] = &[
        ("Browse HuggingFace", "Open the model browser to pick and download an initial model"),
        ("Skip for now", "Install Busibox first, download models later from the Models screen"),
    ];

    let items: Vec<ListItem> = choices
        .iter()
        .enumerate()
        .map(|(i, (title, desc))| {
            let style = if i == app.wizard_target_selected % 2 {
                theme::selected()
            } else {
                theme::normal()
            };
            ListItem::new(vec![
                Line::from(Span::styled(format!("  {title}"), style)),
                Line::from(Span::styled(format!("    {desc}"), theme::muted())),
                Line::from(""),
            ])
        })
        .collect();

    let block = Block::default()
        .borders(Borders::ALL)
        .border_style(theme::dim())
        .title(format!(" Initial Model — engine: {} ", engine_hint))
        .title_style(theme::heading());

    let list = List::new(items).block(block);
    f.render_widget(list, area);
}

fn render_confirm(f: &mut Frame, app: &App, area: Rect) {
    let chunks = Layout::default()
        .direction(Direction::Vertical)
        .constraints([Constraint::Length(8), Constraint::Min(4)])
        .split(area);

    // Summary
    let preset_label = PRESETS
        .get(app.wizard_preset_selected)
        .map(|p| p.label)
        .unwrap_or("Lite");

    let backend_label = if app.wizard_preset_selected == 1 {
        app.wizard_available_backends
            .get(app.wizard_backend_selected)
            .and_then(|id| {
                LocalLlmBackend::all()
                    .iter()
                    .find(|b| b.id() == id.as_str())
                    .map(|b| b.label())
            })
            .unwrap_or("(none)")
    } else {
        ""
    };

    let target_label = match app.wizard_target_selected {
        0 => "Local (Docker)",
        1 => "Remote (SSH)",
        _ => "Import",
    };

    let mut summary_lines = vec![
        Line::from(vec![
            Span::styled("  Target:  ", theme::muted()),
            Span::styled(target_label, theme::highlight()),
        ]),
        Line::from(vec![
            Span::styled("  Preset:  ", theme::muted()),
            Span::styled(preset_label, theme::highlight()),
        ]),
    ];
    if !backend_label.is_empty() {
        summary_lines.push(Line::from(vec![
            Span::styled("  Backend: ", theme::muted()),
            Span::styled(backend_label, theme::highlight()),
        ]));
    }

    let summary = Paragraph::new(summary_lines).block(
        Block::default()
            .borders(Borders::ALL)
            .border_style(theme::dim())
            .title(" Profile Summary ")
            .title_style(theme::heading()),
    );
    f.render_widget(summary, chunks[0]);

    // Services list
    let services = resolved_services(app);
    let svc_text = services.join("  ");
    let services_widget = Paragraph::new(svc_text)
        .wrap(Wrap { trim: true })
        .style(theme::muted())
        .block(
            Block::default()
                .borders(Borders::ALL)
                .border_style(theme::dim())
                .title(" Services to Deploy ")
                .title_style(theme::heading()),
        );
    f.render_widget(services_widget, chunks[1]);
}

// ---------------------------------------------------------------------------
// Key handling
// ---------------------------------------------------------------------------

pub fn handle_key(app: &mut App, key: KeyEvent) {
    match app.wizard_step {
        WizardStep::Target      => handle_target(app, key),
        WizardStep::Preset      => handle_preset(app, key),
        WizardStep::LlmBackend  => handle_llm_backend(app, key),
        WizardStep::ModelSelect => handle_model_select(app, key),
        WizardStep::Confirm     => handle_confirm(app, key),
    }
}

fn handle_target(app: &mut App, key: KeyEvent) {
    match key.code {
        KeyCode::Esc => {
            // Return to welcome / profile select if profiles exist, else quit wizard
            if app.profiles.as_ref().map(|p| !p.profiles.is_empty()).unwrap_or(false) {
                app.screen = Screen::ProfileSelect;
            } else {
                app.should_quit = true;
            }
        }
        KeyCode::Up | KeyCode::Char('k') => {
            if app.wizard_target_selected > 0 {
                app.wizard_target_selected -= 1;
            }
        }
        KeyCode::Down | KeyCode::Char('j') => {
            if app.wizard_target_selected < 2 {
                app.wizard_target_selected += 1;
            }
        }
        KeyCode::Enter => match app.wizard_target_selected {
            0 => {
                // Local install — proceed to preset selection
                app.setup_target = SetupTarget::Local;
                app.wizard_preset_selected = 0;
                app.wizard_step = WizardStep::Preset;
            }
            1 => {
                // Remote install — hand off to SSH setup flow
                app.setup_target = SetupTarget::Remote;
                app.screen = Screen::SetupMode;
            }
            2 => {
                // Import profile — hand off to import flow
                app.screen = Screen::ProfileSelect;
            }
            _ => {}
        },
        _ => {}
    }
}

fn handle_preset(app: &mut App, key: KeyEvent) {
    match key.code {
        KeyCode::Esc => {
            app.wizard_step = WizardStep::Target;
        }
        KeyCode::Up | KeyCode::Char('k') => {
            if app.wizard_preset_selected > 0 {
                app.wizard_preset_selected -= 1;
            }
        }
        KeyCode::Down | KeyCode::Char('j') => {
            if app.wizard_preset_selected < PRESETS.len() - 1 {
                app.wizard_preset_selected += 1;
            }
        }
        KeyCode::Enter => {
            if app.wizard_preset_selected == 1 {
                // Lite + Local LLM: populate available backends from hardware
                populate_available_backends(app);
                app.wizard_backend_selected = 0;
                app.wizard_step = WizardStep::LlmBackend;
            } else {
                // Lite or Full: still offer model selection
                app.wizard_target_selected = 0;
                app.wizard_step = WizardStep::ModelSelect;
            }
        }
        _ => {}
    }
}

fn handle_llm_backend(app: &mut App, key: KeyEvent) {
    let backend_count = app.wizard_available_backends.len();
    match key.code {
        KeyCode::Esc => {
            app.wizard_step = WizardStep::Preset;
        }
        KeyCode::Up | KeyCode::Char('k') => {
            if app.wizard_backend_selected > 0 {
                app.wizard_backend_selected -= 1;
            }
        }
        KeyCode::Down | KeyCode::Char('j') => {
            if app.wizard_backend_selected < backend_count.saturating_sub(1) {
                app.wizard_backend_selected += 1;
            }
        }
        KeyCode::Enter => {
            // Proceed to model selection step (wizard_target_selected reused as 0=Browse/1=Skip)
            app.wizard_target_selected = 0;
            app.wizard_step = WizardStep::ModelSelect;
        }
        _ => {}
    }
}

fn handle_model_select(app: &mut App, key: KeyEvent) {
    match key.code {
        KeyCode::Esc => {
            // Go back to whichever step led here
            if app.wizard_preset_selected == 1 {
                app.wizard_step = WizardStep::LlmBackend;
            } else {
                app.wizard_step = WizardStep::Preset;
            }
        }
        KeyCode::Up | KeyCode::Char('k') => {
            if app.wizard_target_selected > 0 {
                app.wizard_target_selected -= 1;
            }
        }
        KeyCode::Down | KeyCode::Char('j') => {
            if app.wizard_target_selected < 1 {
                app.wizard_target_selected += 1;
            }
        }
        KeyCode::Enter => {
            if app.wizard_target_selected == 0 {
                // "Browse HuggingFace" — open model browser, return here after
                open_model_browser_from_wizard(app);
            } else {
                // "Skip" — proceed to confirm
                app.wizard_step = WizardStep::Confirm;
            }
        }
        _ => {}
    }
}

fn handle_confirm(app: &mut App, key: KeyEvent) {
    match key.code {
        KeyCode::Esc => {
            app.wizard_step = WizardStep::ModelSelect;
        }
        KeyCode::Enter => {
            if create_profile_from_wizard(app) {
                // Profile created — go to install screen
                app.screen = Screen::Install;
                crate::screens::install::auto_start(app);
            }
        }
        _ => {}
    }
}

// ---------------------------------------------------------------------------
// Helper logic
// ---------------------------------------------------------------------------

/// Determine which model format the wizard's selected backend uses.
/// - mlx-lm  → "mlx"   (Apple Silicon, MLX format)
/// - vllm-mlx → "mlx"  (vLLM on Apple Silicon, still uses MLX-quantized weights)
/// - vllm     → "vllm"  (NVIDIA CUDA, GPTQ/AWQ/FP8/safetensors)
/// - llama.cpp → "gguf" (CPU, GGUF format)
/// - ollama   → "gguf"  (wraps GGUF models)
pub fn engine_for_wizard(app: &App) -> &'static str {
    if let Some(backend_id) = app.wizard_available_backends.get(app.wizard_backend_selected) {
        return match backend_id.as_str() {
            "mlx-lm" | "vllm-mlx" => "mlx",
            "vllm"                 => "vllm",
            "llama.cpp" | "ollama" => "gguf",
            _                      => "mlx",
        };
    }
    // Fall back to hardware detection when no backend was selected (Lite/Full presets)
    app.local_hardware
        .as_ref()
        .map(|hw| match hw.llm_backend {
            busibox_core::hardware::LlmBackend::Mlx  => "mlx",
            busibox_core::hardware::LlmBackend::Vllm => "vllm",
            _                                         => "gguf",
        })
        .unwrap_or("mlx")
}

/// Open the model browser pre-configured for the wizard's chosen backend,
/// and mark that it should return to the install wizard when done.
fn open_model_browser_from_wizard(app: &mut App) {
    let engine = engine_for_wizard(app);

    // Pre-select the engine index
    let engine_idx = crate::screens::model_browse::ENGINES
        .iter()
        .position(|(id, _)| *id == engine)
        .unwrap_or(0);

    app.browse_step = BrowseStep::Token;
    app.browse_family_selected = 0;
    app.browse_engine_selected = engine_idx;
    app.browse_model_selected = 0;
    app.browse_model_scroll = 0;
    app.browse_models.clear();
    app.browse_loading = false;
    app.browse_rx = None;
    app.browse_return_to_wizard = true;
    // Engine pre-selected from wizard. In Models step, incompatible models are dimmed.
    // If the selected model is compatible, the Engine step is auto-skipped to Confirm.
    // If not compatible, the lock is cleared and the user picks a compatible engine.
    app.browse_engine_locked = true;
    app.screen = Screen::ModelBrowse;
}

/// Populate `wizard_available_backends` based on detected local hardware.
fn populate_available_backends(app: &mut App) {
    let (is_apple_silicon, has_nvidia) = app
        .local_hardware
        .as_ref()
        .map(|hw| {
            let is_apple = hw.apple_silicon;
            let has_nvidia = hw.gpus.iter().any(|g| {
                g.name.to_ascii_lowercase().contains("nvidia")
                    || g.name.to_ascii_lowercase().contains("cuda")
            });
            (is_apple, has_nvidia)
        })
        .unwrap_or((false, false));

    // Map to arch string for LocalLlmBackend::available_for_hardware
    let arch = if is_apple_silicon { "arm64" } else { "x86_64" };

    app.wizard_available_backends = LocalLlmBackend::available_for_hardware(arch, has_nvidia)
        .iter()
        .map(|b| b.id().to_string())
        .collect();

    // Always have at least Ollama as a fallback
    if app.wizard_available_backends.is_empty() {
        app.wizard_available_backends = vec!["ollama".to_string()];
    }
}

/// Resolve the flat service list for the wizard's current selections.
fn resolved_services(app: &App) -> Vec<String> {
    let base = match app.wizard_preset_selected {
        1 | 2 => BusiboxProfile::Standard, // Lite+Local or Full both start from standard shape
        _ => BusiboxProfile::Lite,
    };
    let full = app.wizard_preset_selected == 2;
    let base_profile = if full { BusiboxProfile::Full } else { base };

    let mut packs = Vec::new();
    if app.wizard_preset_selected == 1 {
        packs.push(AddonPack::LocalModels);
    } else if full {
        packs.push(AddonPack::RagMilvus);
        packs.push(AddonPack::LocalModels);
        packs.push(AddonPack::Graph);
    }

    resolve_services(base_profile, &packs)
        .iter()
        .map(|s| s.to_string())
        .collect()
}

/// Create the deployment profile from wizard state and save it to profiles.json.
/// Returns true on success.
fn create_profile_from_wizard(app: &mut App) -> bool {
    let preset_tag = PRESETS
        .get(app.wizard_preset_selected)
        .map(|p| p.tag)
        .unwrap_or("lite");

    let backend_id: Option<String> = if app.wizard_preset_selected == 1 {
        app.wizard_available_backends
            .get(app.wizard_backend_selected)
            .cloned()
    } else {
        None
    };

    // Derive addon_packs list
    let addon_packs: Vec<String> = match app.wizard_preset_selected {
        1 => vec!["local-models".to_string()],
        2 => vec![
            "rag-milvus".to_string(),
            "local-models".to_string(),
            "graph".to_string(),
        ],
        _ => vec![],
    };

    // Build label
    let label = if app.wizard_label_input.is_empty() {
        match app.wizard_preset_selected {
            1 => format!(
                "Local {}",
                backend_id
                    .as_deref()
                    .unwrap_or("local-llm")
            ),
            2 => "Local Full".to_string(),
            _ => "Local Lite".to_string(),
        }
    } else {
        app.wizard_label_input.clone()
    };

    // Determine the actual service_preset value
    let service_preset = match app.wizard_preset_selected {
        2 => "full".to_string(),
        _ => "lite".to_string(),
    };

    // Build a hostname for the profile id
    let hostname = std::process::Command::new("hostname")
        .output()
        .map(|o| String::from_utf8_lossy(&o.stdout).trim().to_string())
        .unwrap_or_else(|_| "local".to_string());
    let hostname = hostname.trim().to_string();
    let hostname = if hostname.is_empty() { "local".to_string() } else { hostname };

    let profile_id = build_profile_id(&hostname, "development", "docker");

    let profile = Profile {
        environment: "development".to_string(),
        backend: "docker".to_string(),
        label,
        created: Some(chrono_now()),
        vault_prefix: None,
        remote: false,
        remote_host: None,
        remote_user: None,
        remote_ssh_key: None,
        remote_busibox_path: None,
        tailscale_ip: None,
        hardware: app.local_hardware.clone(),
        kubeconfig: None,
        model_tier: None,
        admin_email: None,
        allowed_email_domains: None,
        frontend_ref: None,
        site_domain: None,
        ssl_cert_name: None,
        network_base_octets: None,
        use_production_vllm: None,
        docker_runtime: None,
        github_token: None,
        cloud_provider: None,
        cloud_api_key: None,
        llm_backend_override: backend_id.clone(),
        k8s_overlay: None,
        spot_token: None,
        dev_apps_dir: None,
        huggingface_token: None,
        direct_access: None,
        port_overrides: HashMap::new(),
        service_preset: Some(service_preset),
        addon_packs,
        local_llm_backend: backend_id,
    };

    let _ = preset_tag; // used in label derivation above

    match upsert_profile(&app.repo_root, &profile_id, profile, true) {
        Ok(()) => {
            // Reload profiles into app state
            match load_profiles(&app.repo_root) {
                Ok(profiles) => app.profiles = Some(profiles),
                Err(_) => {}
            }
            // Acquire profile lock
            match try_lock_profile(&app.repo_root, &profile_id) {
                Ok(Some(lock_file)) => {
                    app.profile_lock = Some(lock_file);
                }
                _ => {}
            }
            app.set_message(&format!("Profile '{profile_id}' created"), crate::app::MessageKind::Success);
            true
        }
        Err(e) => {
            app.set_message(
                &format!("Failed to create profile: {e}"),
                crate::app::MessageKind::Error,
            );
            false
        }
    }
}

fn chrono_now() -> String {
    use std::time::{SystemTime, UNIX_EPOCH};
    let secs = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0);
    // Format as ISO 8601 date (year-month-day)
    let days = secs / 86400;
    let year = 1970 + days / 365;
    format!("{year}-01-01") // simple approximation; good enough for a created label
}
