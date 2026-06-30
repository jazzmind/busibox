use crate::app::{App, MessageKind, Screen, TestUpdate};
use crate::modules::remote;
use crate::theme;
use crossterm::event::{KeyCode, KeyEvent};
use ratatui::prelude::*;
use ratatui::widgets::*;
use std::path::Path;
use std::process::{Command, Stdio};

const SPINNER: &[&str] = &["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"];

const TEST_SERVICES: &[&str] = &["all", "authz", "agent", "data", "search"];
const TEST_SERVICES_DISPLAY: &[&str] =
    &["All Services", "authz", "agent", "data", "search"];

const TEST_SUITES: &[&str] = &["Integration", "Unit", "Security", "Custom Args"];
const TEST_SUITE_DESCS: &[&str] = &[
    "Full integration test suite",
    "Unit tests only (fast, no network)",
    "Security / penetration test suite",
    "Specify custom pytest args",
];

// ─────────────────────────────────────────────────────────────────────────────
// Render
// ─────────────────────────────────────────────────────────────────────────────

pub fn render(f: &mut Frame, app: &App) {
    if app.test_log_visible {
        render_log_viewer(f, app);
    } else if app.test_custom_input_active {
        render_custom_args(f, app);
    } else if app.test_focus_suite {
        render_suite_step(f, app);
    } else {
        render_service_step(f, app);
    }
}

fn header_area(f: &mut Frame, app: &App, subtitle: &str) -> [Rect; 3] {
    let area = f.area();
    let env_label = app
        .active_profile()
        .map(|(_, p)| format!("{}  ·  {}", p.environment, p.backend))
        .unwrap_or_else(|| "unknown".to_string());

    let outer = Layout::default()
        .direction(Direction::Vertical)
        .constraints([
            Constraint::Length(1),  // top margin
            Constraint::Length(1),  // title
            Constraint::Length(1),  // env label
            Constraint::Length(1),  // subtitle / breadcrumb
            Constraint::Length(1),  // spacer
            Constraint::Min(4),     // content
            Constraint::Length(1),  // help bar
        ])
        .split(area);

    f.render_widget(
        Paragraph::new("Run Tests")
            .style(theme::title())
            .alignment(Alignment::Center),
        outer[1],
    );
    f.render_widget(
        Paragraph::new(env_label)
            .style(theme::muted())
            .alignment(Alignment::Center),
        outer[2],
    );
    f.render_widget(
        Paragraph::new(subtitle)
            .style(theme::info())
            .alignment(Alignment::Center),
        outer[3],
    );

    [outer[5], outer[6], area]
}

fn render_service_step(f: &mut Frame, app: &App) {
    let [content, help_area, _] = header_area(f, app, "Step 1 of 2 — Select a service");

    let items: Vec<ListItem> = TEST_SERVICES_DISPLAY
        .iter()
        .enumerate()
        .map(|(i, name)| {
            let style = if i == app.test_service_selected {
                theme::selected()
            } else {
                theme::normal()
            };
            let prefix = if i == app.test_service_selected { "▶ " } else { "  " };
            ListItem::new(format!("{prefix}{name}")).style(style)
        })
        .collect();

    let list = List::new(items).block(
        Block::default()
            .borders(Borders::ALL)
            .border_style(theme::selected())
            .title(" Service ")
            .title_style(theme::heading()),
    );
    f.render_widget(list, content);

    render_help(
        f,
        help_area,
        &[("↑↓", "Select"), ("Enter", "Next"), ("Esc", "Back")],
    );
}

fn render_suite_step(f: &mut Frame, app: &App) {
    let svc_name = TEST_SERVICES_DISPLAY
        .get(app.test_service_selected)
        .copied()
        .unwrap_or("all");
    let subtitle = format!("Step 2 of 2 — Service: {svc_name}  →  Select a suite");
    let [content, help_area, _] = header_area(f, app, &subtitle);

    let items: Vec<ListItem> = TEST_SUITES
        .iter()
        .zip(TEST_SUITE_DESCS.iter())
        .enumerate()
        .map(|(i, (name, desc))| {
            let selected = i == app.test_suite_selected;
            let name_style = if selected { theme::selected() } else { theme::normal() };
            let desc_style = if selected { theme::muted() } else { theme::dim() };
            let prefix = if selected { "▶ " } else { "  " };
            let line = Line::from(vec![
                Span::styled(format!("{prefix}{name:<16}", name = name), name_style),
                Span::styled(format!("  {desc}"), desc_style),
            ]);
            ListItem::new(line)
        })
        .collect();

    let list = List::new(items).block(
        Block::default()
            .borders(Borders::ALL)
            .border_style(theme::selected())
            .title(" Suite ")
            .title_style(theme::heading()),
    );
    f.render_widget(list, content);

    render_help(
        f,
        help_area,
        &[("↑↓", "Select"), ("Enter", "Run"), ("Esc", "Back")],
    );
}

fn render_custom_args(f: &mut Frame, app: &App) {
    let svc_name = TEST_SERVICES_DISPLAY
        .get(app.test_service_selected)
        .copied()
        .unwrap_or("all");
    let subtitle = format!("Custom Args — Service: {svc_name}");
    let [content, help_area, _] = header_area(f, app, &subtitle);

    let inner = Layout::default()
        .direction(Direction::Vertical)
        .constraints([
            Constraint::Length(2),
            Constraint::Length(3),
            Constraint::Min(1),
        ])
        .margin(1)
        .split(content);

    f.render_widget(
        Block::default()
            .borders(Borders::ALL)
            .border_style(theme::selected())
            .title(" Custom pytest args ")
            .title_style(theme::heading()),
        content,
    );

    let hint = if svc_name == "All Services" {
        "Patterns: llm  |  integration/test_llm  |  tests/unit/test_foo.py::test_bar"
    } else {
        "Patterns: llm  |  integration/test_llm  |  tests/unit/test_foo.py::test_bar  (within this service)"
    };
    f.render_widget(
        Paragraph::new(hint).style(theme::muted()),
        inner[0],
    );

    let input_display = format!(" {} ", app.test_custom_args);
    f.render_widget(
        Paragraph::new(input_display.as_str())
            .style(theme::selected())
            .block(
                Block::default()
                    .borders(Borders::ALL)
                    .border_style(theme::selected()),
            ),
        inner[1],
    );

    render_help(
        f,
        help_area,
        &[("Enter", "Run"), ("Esc", "Cancel")],
    );
}

fn render_log_viewer(f: &mut Frame, app: &App) {
    let chunks = Layout::default()
        .direction(Direction::Vertical)
        .constraints([
            Constraint::Length(3),
            Constraint::Length(1),
            Constraint::Min(6),
            Constraint::Length(1),
        ])
        .margin(1)
        .split(f.area());

    let svc = TEST_SERVICES_DISPLAY
        .get(app.test_service_selected)
        .copied()
        .unwrap_or("unknown");
    let suite = TEST_SUITES
        .get(app.test_suite_selected)
        .copied()
        .unwrap_or("unknown");

    f.render_widget(
        Paragraph::new(format!("Test Output — {svc} / {suite}"))
            .style(theme::title())
            .alignment(Alignment::Center),
        chunks[0],
    );

    let tick = app.test_tick;
    let spinner_char = SPINNER[tick % SPINNER.len()];
    let subtitle = if app.test_action_running {
        Paragraph::new(Line::from(vec![
            Span::styled(format!("{spinner_char} "), theme::info()),
            Span::styled("Running...", theme::info()),
        ]))
        .alignment(Alignment::Center)
    } else if app.test_action_complete {
        let failed = app.test_log.iter().any(|l| {
            l.contains("ERROR") || l.contains("FAILED") || l.contains("failed") || l.contains("error")
        });
        if failed {
            let hint = if app.test_can_resume {
                "Tests FAILED — press r to resume from failure, R to restart suite"
            } else {
                "Tests FAILED"
            };
            Paragraph::new(hint)
                .style(theme::error())
                .alignment(Alignment::Center)
        } else {
            Paragraph::new("✓ Tests passed").style(theme::success()).alignment(Alignment::Center)
        }
    } else {
        Paragraph::new("").alignment(Alignment::Center)
    };
    f.render_widget(subtitle, chunks[1]);

    let log_height = chunks[2].height.saturating_sub(2) as usize;
    let max_scroll = app.test_log.len().saturating_sub(log_height);
    let scroll = app.test_log_scroll.min(max_scroll);

    let visible: Vec<Line> = app
        .test_log
        .iter()
        .skip(scroll)
        .take(log_height)
        .map(|l| {
            let style = if l.contains("ERROR") || l.contains("FAILED") || l.contains("failed") {
                theme::error()
            } else if l.contains("passed") || l.contains("✓") || l.contains("PASSED") {
                theme::success()
            } else if l.starts_with("  Syncing") || l.starts_with("  ✓ Synced") {
                theme::info()
            } else if l.starts_with("  Running") || l.starts_with("  make") || l.starts_with("Running") {
                theme::info()
            } else if l.contains("warning") || l.contains("WARNING") {
                theme::warning()
            } else {
                theme::normal()
            };
            Line::from(Span::styled(l.as_str(), style))
        })
        .collect();

    let scrollbar_title = if app.test_log.len() > log_height {
        format!(
            " Output ({}-{} of {}) ",
            scroll + 1,
            (scroll + log_height).min(app.test_log.len()),
            app.test_log.len()
        )
    } else {
        " Output ".to_string()
    };

    f.render_widget(
        Paragraph::new(visible).block(
            Block::default()
                .borders(Borders::ALL)
                .border_style(theme::dim())
                .title(scrollbar_title)
                .title_style(theme::heading()),
        ),
        chunks[2],
    );

    let help_text = if app.test_action_running {
        "↑/↓ Scroll  (tests running...)"
    } else if app.test_can_resume
        && app.test_log.iter().any(|l| {
            l.contains("FAILED") || l.contains("ERROR") || l.contains("Tests FAILED")
        })
    {
        "↑/↓ Scroll  r — resume from failure  R — restart suite  c — copy  Esc — back"
    } else {
        "↑/↓ Scroll  PgUp/PgDn  End — jump to end  c — copy output  Esc — back"
    };
    f.render_widget(
        Paragraph::new(Line::from(Span::styled(help_text, theme::muted()))),
        chunks[3],
    );
}

fn render_help(f: &mut Frame, area: Rect, bindings: &[(&str, &str)]) {
    let mut spans = vec![];
    for (i, (key, label)) in bindings.iter().enumerate() {
        if i > 0 {
            spans.push(Span::styled("   ", theme::muted()));
        }
        spans.push(Span::styled(*key, theme::info()));
        spans.push(Span::styled(format!(" {label}"), theme::muted()));
    }
    f.render_widget(
        Paragraph::new(Line::from(spans)).alignment(Alignment::Center),
        area,
    );
}

// ─────────────────────────────────────────────────────────────────────────────
// Key handling
// ─────────────────────────────────────────────────────────────────────────────

pub fn handle_key(app: &mut App, key: KeyEvent) {
    if app.test_log_visible {
        handle_log_key(app, key);
    } else if app.test_custom_input_active {
        handle_custom_input_key(app, key);
    } else if app.test_focus_suite {
        handle_suite_key(app, key);
    } else {
        handle_service_key(app, key);
    }
}

fn handle_service_key(app: &mut App, key: KeyEvent) {
    match key.code {
        KeyCode::Esc => {
            app.screen = Screen::Welcome;
            app.action_menu_selected = 0;
        }
        KeyCode::Up => {
            if app.test_service_selected > 0 {
                app.test_service_selected -= 1;
            }
        }
        KeyCode::Down => {
            if app.test_service_selected + 1 < TEST_SERVICES.len() {
                app.test_service_selected += 1;
            }
        }
        KeyCode::Enter => {
            app.test_focus_suite = true;
        }
        _ => {}
    }
}

fn handle_suite_key(app: &mut App, key: KeyEvent) {
    match key.code {
        KeyCode::Esc => {
            app.test_focus_suite = false;
        }
        KeyCode::Up => {
            if app.test_suite_selected > 0 {
                app.test_suite_selected -= 1;
            }
        }
        KeyCode::Down => {
            if app.test_suite_selected + 1 < TEST_SUITES.len() {
                app.test_suite_selected += 1;
            }
        }
        KeyCode::Enter => {
            if TEST_SUITES.get(app.test_suite_selected) == Some(&"Custom Args") {
                app.test_custom_input_active = true;
            } else {
                spawn_test_worker_fresh(app);
            }
        }
        _ => {}
    }
}

fn handle_custom_input_key(app: &mut App, key: KeyEvent) {
    match key.code {
        KeyCode::Esc => {
            app.test_custom_input_active = false;
            app.test_custom_args.clear();
        }
        KeyCode::Enter => {
            app.test_custom_input_active = false;
            if !app.test_custom_args.is_empty() {
                spawn_test_worker_fresh(app);
            }
        }
        KeyCode::Backspace => {
            app.test_custom_args.pop();
        }
        KeyCode::Char(c) => {
            app.test_custom_args.push(c);
        }
        _ => {}
    }
}

fn handle_log_key(app: &mut App, key: KeyEvent) {
    let log_height: usize = 20;
    match key.code {
        KeyCode::Esc => {
            if !app.test_action_running {
                app.test_log_visible = false;
            }
        }
        KeyCode::Up => {
            app.test_log_autoscroll = false;
            app.test_log_scroll = app.test_log_scroll.saturating_sub(1);
        }
        KeyCode::Down => {
            let max = app.test_log.len().saturating_sub(log_height);
            if app.test_log_scroll < max {
                app.test_log_scroll += 1;
            } else {
                app.test_log_autoscroll = true;
            }
        }
        KeyCode::PageUp => {
            app.test_log_autoscroll = false;
            app.test_log_scroll = app.test_log_scroll.saturating_sub(log_height);
        }
        KeyCode::PageDown => {
            let max = app.test_log.len().saturating_sub(log_height);
            app.test_log_scroll = (app.test_log_scroll + log_height).min(max);
        }
        KeyCode::End => {
            app.test_log_autoscroll = true;
            app.test_log_scroll = app.test_log.len().saturating_sub(log_height);
        }
        KeyCode::Char('c') | KeyCode::Char('C') => {
            copy_log_to_clipboard(app);
        }
        KeyCode::Char('r') => {
            if !app.test_action_running && app.test_can_resume {
                resume_test_worker(app, false);
            }
        }
        KeyCode::Char('R') => {
            if !app.test_action_running && app.test_can_resume {
                resume_test_worker(app, true);
            }
        }
        _ => {}
    }
}

/// Re-run the last test command, optionally resetting pytest --stepwise cache.
fn resume_test_worker(app: &mut App, reset_stepwise: bool) {
    app.test_service_selected = app.test_last_service_selected;
    app.test_suite_selected = app.test_last_suite_selected;
    app.test_custom_args = app.test_last_custom_args.clone();
    spawn_test_worker(app, reset_stepwise);
}

/// New run from the suite menu — reset stepwise so a changed ARGS/service starts clean.
pub fn spawn_test_worker_fresh(app: &mut App) {
    spawn_test_worker(app, true);
}

fn copy_log_to_clipboard(app: &mut App) {
    let text = app.test_log.join("\n");
    // Try pbcopy (macOS), then xclip / xsel (Linux).
    let copied = Command::new("pbcopy")
        .stdin(Stdio::piped())
        .spawn()
        .ok()
        .and_then(|mut child| {
            use std::io::Write;
            child.stdin.take()?.write_all(text.as_bytes()).ok()?;
            child.wait().ok()?.success().then_some(())
        })
        .or_else(|| {
            Command::new("xclip")
                .args(["-selection", "clipboard"])
                .stdin(Stdio::piped())
                .spawn()
                .ok()
                .and_then(|mut child| {
                    use std::io::Write;
                    child.stdin.take()?.write_all(text.as_bytes()).ok()?;
                    child.wait().ok()?.success().then_some(())
                })
        })
        .or_else(|| {
            Command::new("xsel")
                .args(["--clipboard", "--input"])
                .stdin(Stdio::piped())
                .spawn()
                .ok()
                .and_then(|mut child| {
                    use std::io::Write;
                    child.stdin.take()?.write_all(text.as_bytes()).ok()?;
                    child.wait().ok()?.success().then_some(())
                })
        });

    if copied.is_some() {
        app.set_message(
            &format!("Copied {} lines to clipboard", app.test_log.len()),
            crate::app::MessageKind::Success,
        );
    } else {
        app.set_message("Could not copy — pbcopy/xclip not available", crate::app::MessageKind::Warning);
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// Local vault credential extraction
// ─────────────────────────────────────────────────────────────────────────────

/// Decrypt the Ansible vault file locally (on the admin workstation where
/// `ansible-vault` and `python3` are guaranteed to be available) and return
/// the test credentials as `(KEY, value)` pairs.
///
/// These are then injected directly as env vars in the remote SSH command so
/// `extract_vault_credentials` in `test.sh` can skip vault decryption on the
/// remote host (which may not have ansible installed).
fn extract_vault_creds_locally(
    repo_root: &Path,
    vault_prefix: &str,
    vault_password: &str,
) -> Vec<(String, String)> {
    let vault_file = repo_root.join(format!(
        "provision/ansible/roles/secrets/vars/vault.{vault_prefix}.yml"
    ));
    if !vault_file.exists() {
        return vec![];
    }

    // Write vault password to a temp executable script so ansible-vault can
    // call it as --vault-password-file.
    let tmp_path = std::env::temp_dir()
        .join(format!("busibox-test-vp-{}.sh", std::process::id()));
    let pw_escaped = vault_password.replace('\'', "'\\''");
    if std::fs::write(&tmp_path, format!("#!/bin/sh\necho '{pw_escaped}'")).is_err() {
        return vec![];
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let _ = std::fs::set_permissions(&tmp_path, std::fs::Permissions::from_mode(0o700));
    }

    // Decrypt the vault file.
    let vault_output = Command::new("ansible-vault")
        .args(["view", vault_file.to_str().unwrap_or(""), "--vault-password-file"])
        .arg(&tmp_path)
        .env("PATH", {
            let home = std::env::var("HOME").unwrap_or_default();
            let venv = format!("{home}/.busibox/venv/bin");
            let cur = std::env::var("PATH").unwrap_or_default();
            if cur.is_empty() { venv } else { format!("{venv}:{cur}") }
        })
        .output();
    let _ = std::fs::remove_file(&tmp_path);

    let yaml_content = match vault_output {
        Ok(o) if o.status.success() => String::from_utf8_lossy(&o.stdout).to_string(),
        _ => return vec![],
    };

    // Extract the needed test credentials using Python (same logic as test.sh).
    let python_script = r#"
import yaml, sys
vault = yaml.safe_load(sys.stdin.read()) or {}
secrets = vault.get('secrets', {})
pg = secrets.get('postgresql', {})
authz = secrets.get('authz', {})
minio = secrets.get('minio', {})
test_creds = secrets.get('test_credentials', {})
print(f"POSTGRES_PASSWORD={pg.get('password', '')}")
print(f"TEST_DB_PASSWORD={pg.get('password', '')}")
print(f"AUTHZ_MASTER_KEY={authz.get('master_key', '')}")
print(f"MINIO_ACCESS_KEY={minio.get('minio_access_key', '') or minio.get('access_key', '')}")
print(f"MINIO_SECRET_KEY={minio.get('minio_secret_key', '') or minio.get('secret_key', '')}")
print(f"TEST_USER_ID={test_creds.get('test_user_id', '')}")
print(f"JWT_SECRET={secrets.get('jwt_secret', '')}")
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
        Ok(out) if out.status.success() => String::from_utf8_lossy(&out.stdout)
            .lines()
            .filter_map(|line| {
                let mut parts = line.splitn(2, '=');
                let key = parts.next()?.trim().to_string();
                let val = parts.next()?.to_string();
                if key.is_empty() { None } else { Some((key, val)) }
            })
            .collect(),
        _ => vec![],
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// Worker
// ─────────────────────────────────────────────────────────────────────────────

pub fn spawn_test_worker(app: &mut App, stepwise_reset: bool) {
    app.test_last_service_selected = app.test_service_selected;
    app.test_last_suite_selected = app.test_suite_selected;
    app.test_last_custom_args = app.test_custom_args.clone();
    app.test_can_resume = false;

    let svc_key = TEST_SERVICES
        .get(app.test_service_selected)
        .copied()
        .unwrap_or("all");
    let suite = TEST_SUITES
        .get(app.test_suite_selected)
        .copied()
        .unwrap_or("Integration");

    let vault_password = match app.vault_password.clone() {
        Some(pw) => pw,
        None => {
            app.set_message("Vault password not set — unlock profile first", MessageKind::Warning);
            return;
        }
    };

    let is_remote = app.active_profile().map(|(_, p)| p.remote).unwrap_or(false);
    let profile_env: String = app
        .active_profile()
        .map(|(_, p)| p.environment.clone())
        .unwrap_or_else(|| "staging".to_string());
    let profile_backend: String = app
        .active_profile()
        .map(|(_, p)| p.backend.to_lowercase())
        .unwrap_or_else(|| "proxmox".to_string());
    // vault_prefix resolves to the profile's explicit prefix or falls back to
    // the profile ID — this matches how profiles.sh picks the vault file.
    let vault_prefix: String = app
        .active_profile()
        .map(|(id, p)| {
            p.vault_prefix
                .clone()
                .unwrap_or_else(|| id.to_string())
        })
        .unwrap_or_else(|| "dev".to_string());

    let ssh_details: Option<(String, String, String)> = if is_remote {
        app.active_profile().and_then(|(_, p)| {
            p.effective_host().map(|h| {
                (
                    h.to_string(),
                    p.effective_user().to_string(),
                    p.effective_ssh_key().to_string(),
                )
            })
        })
    } else {
        None
    };

    let profile_remote_path: Option<String> = app
        .active_profile()
        .map(|(_, p)| p.effective_remote_path().to_string());
    let profile_host: Option<String> = app
        .active_profile()
        .and_then(|(_, p)| p.effective_host().map(|s| s.to_string()));

    let repo_root = app.repo_root.clone();
    let custom_args = app.test_custom_args.clone();
    let vault_prefix = vault_prefix.clone();
    let stepwise_reset = stepwise_reset;

    // Decrypt the vault locally so test.sh never needs ansible-vault on the
    // remote host.  Failures are soft — the remote fallback still tries.
    let local_creds: Vec<(String, String)> =
        extract_vault_creds_locally(&repo_root, &vault_prefix, &vault_password);

    // Clear log and show log viewer
    app.test_log.clear();
    app.test_log_visible = true;
    app.test_log_scroll = 0;
    app.test_log_autoscroll = true;
    app.test_action_running = true;
    app.test_action_complete = false;

    let (tx, rx) = std::sync::mpsc::channel::<TestUpdate>();
    app.test_rx = Some(rx);

    let svc_key = svc_key.to_string();
    let suite = suite.to_string();
    let local_creds = local_creds; // move into thread

    std::thread::spawn(move || {
        let remote_path = profile_remote_path
            .as_deref()
            .unwrap_or("~/busibox")
            .to_string();

        // ── Step 1: sync repo to remote (Proxmox only) ──────────────────────
        if is_remote {
            if let Some((ref host, ref user, ref key)) = ssh_details {
                let display_host = profile_host.as_deref().unwrap_or(host);
                let _ = tx.send(TestUpdate::Log(format!(
                    "Syncing code to {display_host}..."
                )));
                match remote::sync(&repo_root, host, user, key, &remote_path) {
                    Ok(()) => {
                        let _ = tx.send(TestUpdate::Log("✓ Synced".to_string()));
                    }
                    Err(e) => {
                        let _ = tx.send(TestUpdate::Log(format!("WARNING: sync failed: {e}")));
                        let _ = tx.send(TestUpdate::Log(
                            "Continuing with existing remote code...".to_string(),
                        ));
                    }
                }
                let _ = tx.send(TestUpdate::Log(String::new()));
            }
        }

        // ── Step 2: build make args ──────────────────────────────────────────
        let is_docker = profile_backend == "docker";
        // VAULT_PREFIX is passed as a make variable so test.sh can find the
        // correct vault.{prefix}.yml file without interactive prompts.
        let vp_arg = if !is_docker && !vault_prefix.is_empty() {
            format!("VAULT_PREFIX={vault_prefix} ")
        } else {
            String::new()
        };

        let make_args = if suite == "Security" {
            format!("{vp_arg}test-security")
        } else if suite == "Unit" {
            if is_docker {
                format!("test-docker SERVICE={svc_key} ARGS=\"tests/unit\"")
            } else {
                format!("{vp_arg}test SERVICE={svc_key} INV={profile_env} ARGS=\"tests/unit\"")
            }
        } else if suite == "Custom Args" && !custom_args.is_empty() {
            if is_docker {
                format!("test-docker SERVICE={svc_key} ARGS=\"{custom_args}\"")
            } else {
                format!("{vp_arg}test SERVICE={svc_key} INV={profile_env} ARGS=\"{custom_args}\"")
            }
        } else {
            if is_docker {
                format!("test-docker SERVICE={svc_key}")
            } else {
                format!("{vp_arg}test SERVICE={svc_key} INV={profile_env}")
            }
        };

        let _ = tx.send(TestUpdate::Log(format!("Running: make {make_args}")));
        let _ = tx.send(TestUpdate::Log(format!(
            "Environment: {profile_env} ({profile_backend})"
        )));
        if stepwise_reset {
            let _ = tx.send(TestUpdate::Log(
                "Pytest: --stepwise-reset (fresh run from start of suite)".to_string(),
            ));
        } else {
            let _ = tx.send(TestUpdate::Log(
                "Pytest: --stepwise (resume from last failure, skip passed tests)".to_string(),
            ));
        }
        let _ = tx.send(TestUpdate::Log(String::new()));

        let stream_tx = tx.clone();
        let on_line = move |line: &str| {
            let _ = stream_tx.send(TestUpdate::Log(format!("  {line}")));
        };

        // ── Step 3: run tests ────────────────────────────────────────────────
        let result: color_eyre::Result<i32> = if is_remote {
            if let Some((ref host, ref user, ref key)) = ssh_details {
                let display_host = profile_host.as_deref().unwrap_or(host);
                let ssh = crate::modules::ssh::SshConnection::new(display_host, user, key);

                // Build a shell command that injects vault password AND any
                // test credentials decrypted locally, then runs make.
                let escaped_pw = vault_password.replace('\'', "'\\''");
                let mut env_block = format!("export ANSIBLE_VAULT_PASSWORD='{escaped_pw}'; ");
                // Pre-inject test credentials so test.sh skips ansible-vault
                // on the remote host (which may not have ansible installed).
                for (k, v) in &local_creds {
                    let v_esc = v.replace('\'', "'\\''");
                    env_block.push_str(&format!("export {k}='{v_esc}'; "));
                }
                // Always run tests against the test database — never production.
                env_block.push_str("export AUTHZ_TEST_MODE_ENABLED='true'; ");
                if stepwise_reset {
                    env_block.push_str("export PYTEST_STEPWISE_RESET='1'; ");
                }
                let cmd = format!("{env_block}USE_MANAGER=0 make {make_args}");
                remote::exec_remote_streaming(&ssh, &remote_path, &cmd, on_line)
            } else {
                Err(color_eyre::eyre::eyre!("Remote profile has no SSH host configured"))
            }
        } else {
            const STEPWISE_RESET_ENV: [(&str, &str); 1] = [("PYTEST_STEPWISE_RESET", "1")];
            remote::run_local_make_quiet_with_vault_streaming(
                &repo_root,
                &make_args,
                &vault_password,
                if stepwise_reset {
                    Some(&STEPWISE_RESET_ENV)
                } else {
                    None
                },
                on_line,
            )
        };

        match result {
            Ok(0) => {
                let _ = tx.send(TestUpdate::Log(String::new()));
                let _ = tx.send(TestUpdate::Log("✓ Tests passed".to_string()));
                let _ = tx.send(TestUpdate::Complete { success: true });
            }
            Ok(code) => {
                let _ = tx.send(TestUpdate::Log(String::new()));
                let _ = tx.send(TestUpdate::Log(format!("Tests FAILED (exit code {code})")));
                let _ = tx.send(TestUpdate::Complete { success: false });
            }
            Err(e) => {
                let _ = tx.send(TestUpdate::Log(format!("ERROR: {e}")));
                let _ = tx.send(TestUpdate::Complete { success: false });
            }
        }
    });
}
