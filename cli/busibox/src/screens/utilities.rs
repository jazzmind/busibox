use crate::app::{App, Screen, UtilitiesUpdate};
use crate::theme;
use crossterm::event::{KeyCode, KeyEvent};
use ratatui::prelude::*;
use ratatui::widgets::*;
use std::sync::mpsc;
use std::path::Path;
use std::process::{Command, Stdio};

// ─────────────────────────────────────────────────────────────────────────────
// Menu definition
// ─────────────────────────────────────────────────────────────────────────────

struct MenuItem {
    label: &'static str,
    description: &'static str,
}

const MENU: &[MenuItem] = &[
    MenuItem { label: "Validate Secrets",         description: "Decrypt and verify vault secrets" },
    MenuItem { label: "Run Tests",                description: "Run unit, integration & security tests" },
    MenuItem { label: "Clean Install",            description: "Wipe containers and reinstall from scratch" },
    MenuItem { label: "Install SSL Certificate",  description: "Generate & trust a local SSL cert (mkcert)" },
    MenuItem { label: "Toggle Agent Mode",        description: "Switch Claude Code between Local (busibox) and Normal (Anthropic)" },
    MenuItem { label: "Benchmark Models",         description: "Run LLM benchmark suite" },
];

// Index of the toggle item — kept as a constant so it's easy to find.
const TOGGLE_AGENT_IDX: usize = 4;

// ─────────────────────────────────────────────────────────────────────────────
// Mode detection
// ─────────────────────────────────────────────────────────────────────────────

/// Returns true when ~/.claude/settings.json routes through a local LiteLLM proxy.
pub fn detect_agents_mode() -> bool {
    let home = std::env::var("HOME").unwrap_or_default();
    let path = format!("{home}/.claude/settings.json");
    std::fs::read_to_string(&path)
        .map(|c| c.contains("\"ANTHROPIC_BASE_URL\"") && c.contains("localhost"))
        .unwrap_or(false)
}

// ─────────────────────────────────────────────────────────────────────────────
// Render
// ─────────────────────────────────────────────────────────────────────────────

pub fn render(f: &mut Frame, app: &App) {
    let area = f.area();

    let chunks = Layout::default()
        .direction(Direction::Vertical)
        .constraints([
            Constraint::Length(1), // title
            Constraint::Length(1), // spacer
            Constraint::Min(8),    // content
            Constraint::Length(3), // help bar
        ])
        .margin(2)
        .split(area);

    // Title
    let title = Paragraph::new(Line::from(vec![
        Span::styled("Utilities", theme::title()),
    ]))
    .alignment(Alignment::Center);
    f.render_widget(title, chunks[0]);

    // Content — split when log is visible
    if app.utilities_log_visible && !app.utilities_log.is_empty() {
        let cols = Layout::default()
            .direction(Direction::Horizontal)
            .constraints([Constraint::Percentage(42), Constraint::Percentage(58)])
            .split(chunks[2]);
        render_menu_panel(f, app, cols[0]);
        render_log_panel(f, app, cols[1]);
    } else {
        render_menu_panel(f, app, chunks[2]);
    }

    // Help bar
    let help = if app.utilities_action_running {
        Paragraph::new(Line::from(vec![
            Span::styled("Running…  ", theme::info()),
            Span::styled("l ", theme::highlight()),
            Span::styled("Toggle log  ", theme::normal()),
        ]))
    } else if !app.utilities_log.is_empty() {
        Paragraph::new(Line::from(vec![
            Span::styled("↑/↓ ", theme::highlight()),
            Span::styled("Navigate  ", theme::normal()),
            Span::styled("Enter ", theme::highlight()),
            Span::styled("Run  ", theme::normal()),
            Span::styled("l ", theme::highlight()),
            Span::styled("Toggle log  ", theme::normal()),
            Span::styled("Esc ", theme::muted()),
            Span::styled("Back", theme::muted()),
        ]))
    } else {
        Paragraph::new(Line::from(vec![
            Span::styled("↑/↓ ", theme::highlight()),
            Span::styled("Navigate  ", theme::normal()),
            Span::styled("Enter ", theme::highlight()),
            Span::styled("Run  ", theme::normal()),
            Span::styled("Esc ", theme::muted()),
            Span::styled("Back", theme::muted()),
        ]))
    };
    f.render_widget(help, chunks[3]);
}

fn render_menu_panel(f: &mut Frame, app: &App, area: Rect) {
    let rows: Vec<ListItem> = MENU.iter().enumerate().map(|(i, item)| {
        let style = if i == app.utilities_selected {
            theme::selected()
        } else {
            theme::normal()
        };
        let desc_style = if i == app.utilities_selected {
            theme::selected()
        } else {
            theme::dim()
        };

        // For the toggle item, show the current mode badge in the label.
        let label = if i == TOGGLE_AGENT_IDX {
            let badge = if app.agents_mode_is_local {
                " [LOCAL ●]"
            } else {
                " [NORMAL ○]"
            };
            format!("  {}{}", item.label, badge)
        } else {
            format!("  {}", item.label)
        };

        ListItem::new(vec![
            Line::from(Span::styled(label, style)),
            Line::from(Span::styled(format!("    {}", item.description), desc_style)),
            Line::from(""),
        ])
    }).collect();

    let list = List::new(rows).block(
        Block::default()
            .borders(Borders::ALL)
            .border_style(theme::dim())
            .title(" Actions ")
            .title_style(theme::heading()),
    );
    f.render_widget(list, area);
}

fn render_log_panel(f: &mut Frame, app: &App, area: Rect) {
    let log_text: Vec<Line> = app.utilities_log.iter().map(|line| {
        let style = if line.contains("✓") || line.contains("SUCCESS") || line.contains("success") {
            theme::success()
        } else if line.contains("✗") || line.contains("ERROR") || line.contains("error") || line.contains("failed") {
            theme::error()
        } else if line.contains("…") || line.contains("Running") || line.contains("Writing") {
            theme::info()
        } else {
            theme::normal()
        };
        Line::from(Span::styled(line.as_str(), style))
    }).collect();

    let scroll_offset = app.utilities_log.len().saturating_sub(
        area.height.saturating_sub(4) as usize
    );

    let title = if app.utilities_action_running {
        format!(" Output ({}) ", app.utilities_log.len())
    } else if app.utilities_action_success {
        " Output ✓ ".to_string()
    } else if !app.utilities_log.is_empty() {
        " Output ✗ ".to_string()
    } else {
        " Output ".to_string()
    };

    let log = Paragraph::new(log_text)
        .block(
            Block::default()
                .borders(Borders::ALL)
                .border_style(theme::dim())
                .title(title)
                .title_style(if app.utilities_action_running { theme::info() }
                             else if app.utilities_action_success { theme::success() }
                             else { theme::error() }),
        )
        .wrap(Wrap { trim: false })
        .scroll((scroll_offset as u16, 0));
    f.render_widget(log, area);
}

// ─────────────────────────────────────────────────────────────────────────────
// Key handling
// ─────────────────────────────────────────────────────────────────────────────

pub fn handle_key(app: &mut App, key: KeyEvent) {
    match key.code {
        KeyCode::Esc => {
            app.screen = Screen::Welcome;
        }
        KeyCode::Up => {
            if app.utilities_selected > 0 {
                app.utilities_selected -= 1;
            }
        }
        KeyCode::Down => {
            if app.utilities_selected + 1 < MENU.len() {
                app.utilities_selected += 1;
            }
        }
        KeyCode::Char('l') | KeyCode::Right => {
            if !app.utilities_log.is_empty() {
                app.utilities_log_visible = !app.utilities_log_visible;
            }
        }
        KeyCode::Enter => {
            if !app.utilities_action_running {
                dispatch_selected(app);
            }
        }
        _ => {}
    }
}

fn dispatch_selected(app: &mut App) {
    match app.utilities_selected {
        0 => {
            app.set_message("⠋ Validating vault secrets…", crate::app::MessageKind::Info);
            app.pending_compare_secrets = true;
        }
        1 => {
            app.test_service_selected = 0;
            app.test_suite_selected = 0;
            app.test_focus_suite = false;
            app.test_custom_args.clear();
            app.test_custom_input_active = false;
            app.test_log.clear();
            app.test_log_visible = false;
            app.test_action_running = false;
            app.test_action_complete = false;
            app.screen = Screen::RunTests;
        }
        2 => {
            app.pending_clean_install_confirm = true;
        }
        3 => {
            spawn_ssl_install(app);
        }
        TOGGLE_AGENT_IDX => {
            spawn_toggle_agents(app);
        }
        5 => {
            crate::screens::model_benchmark::init_screen(app, None);
            app.screen = Screen::ModelBenchmark;
        }
        _ => {}
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// SSL install worker
// ─────────────────────────────────────────────────────────────────────────────

fn spawn_ssl_install(app: &mut App) {
    let repo_root = app.repo_root.clone();
    app.utilities_log.clear();
    app.utilities_log_visible = true;
    app.utilities_action_running = true;
    app.utilities_action_success = false;

    let (tx, rx) = mpsc::channel::<UtilitiesUpdate>();
    app.utilities_rx = Some(rx);

    std::thread::spawn(move || {
        let script = repo_root.join("scripts/setup/generate-local-ssl.sh");
        if !script.exists() {
            let _ = tx.send(UtilitiesUpdate::Log(format!("✗ Script not found: {}", script.display())));
            let _ = tx.send(UtilitiesUpdate::Complete { success: false });
            return;
        }

        let _ = tx.send(UtilitiesUpdate::Log("Running generate-local-ssl.sh…".into()));

        let mut child = match Command::new("bash")
            .arg(&script)
            .current_dir(&repo_root)
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .spawn()
        {
            Ok(c) => c,
            Err(e) => {
                let _ = tx.send(UtilitiesUpdate::Log(format!("✗ Failed to start: {e}")));
                let _ = tx.send(UtilitiesUpdate::Complete { success: false });
                return;
            }
        };

        if let Some(stdout) = child.stdout.take() {
            use std::io::{BufRead, BufReader};
            for line in BufReader::new(stdout).lines().flatten() {
                let _ = tx.send(UtilitiesUpdate::Log(line));
            }
        }

        let success = child.wait().map(|s| s.success()).unwrap_or(false);
        if success {
            let _ = tx.send(UtilitiesUpdate::Log("✓ SSL certificate installed successfully".into()));
        } else {
            let _ = tx.send(UtilitiesUpdate::Log("✗ SSL certificate installation failed".into()));
        }
        let _ = tx.send(UtilitiesUpdate::Complete { success });
    });
}

// ─────────────────────────────────────────────────────────────────────────────
// Agent mode toggle worker
// ─────────────────────────────────────────────────────────────────────────────

fn spawn_toggle_agents(app: &mut App) {
    let home = std::env::var("HOME").unwrap_or_default();
    let settings_path     = format!("{home}/.claude/settings.json");
    let settings_local    = format!("{home}/.claude/settings.local.json");
    let settings_normal   = format!("{home}/.claude/settings.normal.json");
    let currently_local   = app.agents_mode_is_local;

    app.utilities_log.clear();
    app.utilities_log_visible = true;
    app.utilities_action_running = true;
    app.utilities_action_success = false;

    let (tx, rx) = mpsc::channel::<UtilitiesUpdate>();
    app.utilities_rx = Some(rx);

    if currently_local {
        // ── Switch to Normal ─────────────────────────────────────────────────
        std::thread::spawn(move || {
            if Path::new(&settings_normal).exists() {
                match std::fs::copy(&settings_normal, &settings_path) {
                    Ok(_) => { let _ = tx.send(UtilitiesUpdate::Log("✓ Restored settings.normal.json".into())); }
                    Err(e) => {
                        let _ = tx.send(UtilitiesUpdate::Log(format!("✗ Restore failed: {e}")));
                        let _ = tx.send(UtilitiesUpdate::Complete { success: false });
                        return;
                    }
                }
            } else {
                match std::fs::write(&settings_path, "{}") {
                    Ok(_) => { let _ = tx.send(UtilitiesUpdate::Log("✓ Wrote empty settings.json (normal mode)".into())); }
                    Err(e) => {
                        let _ = tx.send(UtilitiesUpdate::Log(format!("✗ Write failed: {e}")));
                        let _ = tx.send(UtilitiesUpdate::Complete { success: false });
                        return;
                    }
                }
            }
            let _ = tx.send(UtilitiesUpdate::Log("✓ Agent mode → Normal (direct Anthropic)".into()));
            let _ = tx.send(UtilitiesUpdate::Log("Restart Claude Code to pick up new settings.".into()));
            let _ = tx.send(UtilitiesUpdate::Complete { success: true });
        });
    } else {
        // ── Switch to Local ──────────────────────────────────────────────────
        // If settings.local.json already exists, just swap — no vault needed.
        let local_cached = Path::new(&settings_local).exists();

        let vault_password = if !local_cached {
            match app.vault_password.clone() {
                Some(pw) => pw,
                None => {
                    app.utilities_log.push(
                        "✗ No cached settings.local.json and vault is locked — unlock profile first".into()
                    );
                    app.utilities_action_running = false;
                    return;
                }
            }
        } else {
            String::new() // unused
        };

        let vault_prefix: String = app
            .active_profile()
            .map(|(id, p)| p.vault_prefix.clone().unwrap_or_else(|| id.to_string()))
            .unwrap_or_else(|| "dev".to_string());

        let litellm_port: u16 = app
            .active_profile()
            .map(|(_, p)| p.port_overrides.get("litellm").copied().unwrap_or(4000))
            .unwrap_or(4000);

        let repo_root = app.repo_root.clone();

        std::thread::spawn(move || {
            // Backup current settings.json once so we can restore it later.
            if !Path::new(&settings_normal).exists() {
                let backup_content = std::fs::read_to_string(&settings_path).unwrap_or_else(|_| "{}".to_string());
                if std::fs::write(&settings_normal, &backup_content).is_ok() {
                    let _ = tx.send(UtilitiesUpdate::Log("✓ Backed up settings.json → settings.normal.json".into()));
                }
            }

            if local_cached {
                // Fast path: just copy cached local settings.
                match std::fs::copy(&settings_local, &settings_path) {
                    Ok(_) => { let _ = tx.send(UtilitiesUpdate::Log("✓ Restored settings.local.json".into())); }
                    Err(e) => {
                        let _ = tx.send(UtilitiesUpdate::Log(format!("✗ Restore failed: {e}")));
                        let _ = tx.send(UtilitiesUpdate::Complete { success: false });
                        return;
                    }
                }
            } else {
                // First-time setup: extract key from vault and write both files.
                let _ = tx.send(UtilitiesUpdate::Log(
                    format!("Extracting LITELLM_MASTER_KEY (prefix: {vault_prefix})…")
                ));
                let master_key = match extract_litellm_key(&repo_root, &vault_prefix, &vault_password) {
                    Some(k) => {
                        let _ = tx.send(UtilitiesUpdate::Log("✓ LITELLM_MASTER_KEY found".into()));
                        k
                    }
                    None => {
                        let _ = tx.send(UtilitiesUpdate::Log("✗ Could not extract LITELLM_MASTER_KEY from vault".into()));
                        let _ = tx.send(UtilitiesUpdate::Complete { success: false });
                        return;
                    }
                };

                let _ = tx.send(UtilitiesUpdate::Log(format!("Writing local settings (litellm port: {litellm_port})…")));
                if !write_local_settings(&tx, litellm_port, &master_key, &settings_path, &settings_local) {
                    let _ = tx.send(UtilitiesUpdate::Complete { success: false });
                    return;
                }
                write_agent_files(&tx, &master_key);
            }

            let _ = tx.send(UtilitiesUpdate::Log("✓ Agent mode → Local (busibox/LiteLLM)".into()));
            let _ = tx.send(UtilitiesUpdate::Log("Restart Claude Code to pick up new settings.".into()));
            let _ = tx.send(UtilitiesUpdate::Complete { success: true });
        });
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// Settings file writers
// ─────────────────────────────────────────────────────────────────────────────

/// Write settings.json (and cache it as settings.local.json). Returns true on success.
fn write_local_settings(
    tx: &mpsc::Sender<UtilitiesUpdate>,
    litellm_port: u16,
    master_key: &str,
    settings_path: &str,
    settings_local: &str,
) -> bool {
    let settings = serde_json::json!({
        "env": {
            "ANTHROPIC_API_KEY": "",
            "ANTHROPIC_AUTH_TOKEN": master_key,
            "ANTHROPIC_BASE_URL": format!("http://localhost:{litellm_port}"),
            "ANTHROPIC_MODEL": "frontier",
            "CLAUDE_CODE_ATTRIBUTION_HEADER": "0",
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
            "CLAUDE_CODE_ENABLE_TELEMETRY": "0"
        }
    });

    let content = match serde_json::to_string_pretty(&settings) {
        Ok(c) => c,
        Err(e) => {
            let _ = tx.send(UtilitiesUpdate::Log(format!("✗ Serialize error: {e}")));
            return false;
        }
    };

    // Write active settings.json
    if let Err(e) = std::fs::write(settings_path, &content) {
        let _ = tx.send(UtilitiesUpdate::Log(format!("✗ Write {settings_path}: {e}")));
        return false;
    }
    let _ = tx.send(UtilitiesUpdate::Log(format!("✓ Written {settings_path}")));

    // Cache as settings.local.json for future fast toggles
    let _ = std::fs::write(settings_local, &content);
    let _ = tx.send(UtilitiesUpdate::Log(format!("✓ Cached {settings_local}")));

    true
}

/// Write ~/.claude/agents/*.md files for the four code-agent purposes.
fn write_agent_files(tx: &mpsc::Sender<UtilitiesUpdate>, _master_key: &str) {
    let home = std::env::var("HOME").unwrap_or_default();
    let agents_dir = format!("{home}/.claude/agents");

    if let Err(e) = std::fs::create_dir_all(&agents_dir) {
        let _ = tx.send(UtilitiesUpdate::Log(format!("✗ Could not create ~/.claude/agents: {e}")));
        return;
    }

    let agents: &[(&str, &str, &str, &str)] = &[
        (
            "local-coder.md",
            "code-writing",
            "Use this agent to execute heavy code generation, refactoring, complex logic implementation, architectural changes, debugging deep edge cases, and updating tests.",
            "Read, Write, Patch, Bash, SearchAndReplace",
        ),
        (
            "local-doc-writer.md",
            "code-documenting",
            "Use this agent to write markdown documentation, update READMEs, write inline code comments, generate JSDoc/Docstrings, or maintain changelogs.",
            "Read, Write, Patch, Grep, Glob",
        ),
        (
            "local-explorer.md",
            "code-reading",
            "Use this agent to search the codebase, read specific files, grep definitions, inspect directory structures, or parse logs. Do NOT use it to rewrite or patch code.",
            "Grep, Glob, Read, ViewDirectory, ListFiles",
        ),
        (
            "local-tester.md",
            "code-testing",
            "Use this agent to create unit tests, integration tests, mock data, or to automatically run the test suite and fix failing assertions.",
            "Read, Write, Patch, Bash",
        ),
        (
            "local-planner.md",
            "code-planning",
            "Use this agent to plan architecture, design multi-step implementation approaches, and evaluate tradeoffs before writing code. Do NOT use it to write or edit code directly.",
            "Read, Grep, Glob, ViewDirectory, ListFiles",
        ),
        (
            "local-security-reviewer.md",
            "code-securing",
            "Use this agent to review code for security vulnerabilities, unsafe patterns, and secrets/credential leaks before it ships.",
            "Read, Grep, Glob, Bash",
        ),
    ];

    for (filename, model, description, tools) in agents {
        let name = filename.trim_end_matches(".md");
        let content = format!(
            "---\nname: {name}\ndescription: {description}\ntools: [{tools}]\nmodel: {model}\n---\n"
        );
        let path = format!("{agents_dir}/{filename}");
        match std::fs::write(&path, &content) {
            Ok(_) => { let _ = tx.send(UtilitiesUpdate::Log(format!("✓ Written {path}"))); }
            Err(e) => { let _ = tx.send(UtilitiesUpdate::Log(format!("✗ {path}: {e}"))); }
        }
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// Vault key extraction
// ─────────────────────────────────────────────────────────────────────────────

fn extract_litellm_key(repo_root: &Path, vault_prefix: &str, vault_password: &str) -> Option<String> {
    let vault_file = repo_root.join(format!(
        "provision/ansible/roles/secrets/vars/vault.{vault_prefix}.yml"
    ));
    if !vault_file.exists() {
        return None;
    }

    let tmp_path = std::env::temp_dir()
        .join(format!("busibox-util-vp-{}.sh", std::process::id()));
    let pw_escaped = vault_password.replace('\'', "'\\''");
    if std::fs::write(&tmp_path, format!("#!/bin/sh\necho '{pw_escaped}'")).is_err() {
        return None;
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let _ = std::fs::set_permissions(&tmp_path, std::fs::Permissions::from_mode(0o700));
    }

    let home = std::env::var("HOME").unwrap_or_default();
    let venv_bin = format!("{home}/.busibox/venv/bin");
    let cur_path = std::env::var("PATH").unwrap_or_default();
    let full_path = if cur_path.is_empty() { venv_bin.clone() } else { format!("{venv_bin}:{cur_path}") };

    let output = Command::new("ansible-vault")
        .args(["view", vault_file.to_str().unwrap_or(""), "--vault-password-file"])
        .arg(&tmp_path)
        .env("PATH", &full_path)
        .output();
    let _ = std::fs::remove_file(&tmp_path);

    let yaml_content = match output {
        Ok(o) if o.status.success() => String::from_utf8_lossy(&o.stdout).to_string(),
        _ => return None,
    };

    let python_script = r#"
import yaml, sys
vault = yaml.safe_load(sys.stdin.read()) or {}
secrets = vault.get('secrets', {})
key = secrets.get('litellm_master_key', '') or secrets.get('litellm_api_key', '')
print(key)
"#;

    let result = Command::new("python3")
        .args(["-c", python_script])
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .spawn()
        .and_then(|mut child| {
            if let Some(mut stdin) = child.stdin.take() {
                use std::io::Write;
                let _ = stdin.write_all(yaml_content.as_bytes());
            }
            child.wait_with_output()
        });

    match result {
        Ok(out) if out.status.success() => {
            let key = String::from_utf8_lossy(&out.stdout).trim().to_string();
            if key.is_empty() { None } else { Some(key) }
        }
        _ => None,
    }
}
