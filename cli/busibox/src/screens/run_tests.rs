use crate::app::{App, MessageKind, Screen, TestUpdate};
use crate::modules::remote;
use crate::theme;
use crossterm::event::{KeyCode, KeyEvent};
use ratatui::layout::Margin;
use ratatui::prelude::*;
use ratatui::widgets::{Scrollbar, ScrollbarOrientation, ScrollbarState, *};

const SPINNER: &[&str] = &["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"];

const TEST_SERVICES: &[&str] = &["all", "authz", "agent", "data", "search"];
const TEST_SERVICES_DISPLAY: &[&str] =
    &["All Services", "authz", "agent", "data", "search"];

const TEST_SUITES: &[&str] = &["Integration", "Unit", "Security", "Custom Args"];

// ─────────────────────────────────────────────────────────────────────────────
// Render
// ─────────────────────────────────────────────────────────────────────────────

pub fn render(f: &mut Frame, app: &App) {
    if app.test_log_visible {
        render_log_viewer(f, app);
    } else {
        render_picker(f, app);
    }
}

fn render_picker(f: &mut Frame, app: &App) {
    let area = f.area();

    let outer = Layout::default()
        .direction(Direction::Vertical)
        .constraints([
            Constraint::Length(3),
            Constraint::Min(10),
            Constraint::Length(3),
        ])
        .margin(2)
        .split(area);

    // Title
    let env_label = app
        .active_profile()
        .map(|(_, p)| format!("{} ({})", p.environment, p.backend))
        .unwrap_or_else(|| "unknown".to_string());

    let title = Paragraph::new(format!("Run Tests  —  {env_label}"))
        .style(theme::title())
        .alignment(Alignment::Center);
    f.render_widget(title, outer[0]);

    // Body: two side-by-side panels
    let columns = Layout::default()
        .direction(Direction::Horizontal)
        .constraints([Constraint::Percentage(45), Constraint::Percentage(55)])
        .split(outer[1]);

    // ── Service list ──
    let svc_border_style = if !app.test_focus_suite {
        theme::selected()
    } else {
        theme::dim()
    };

    let svc_items: Vec<ListItem> = TEST_SERVICES_DISPLAY
        .iter()
        .enumerate()
        .map(|(i, name)| {
            let style = if i == app.test_service_selected && !app.test_focus_suite {
                theme::selected()
            } else if i == app.test_service_selected {
                theme::muted()
            } else {
                theme::normal()
            };
            ListItem::new(format!("  {name}  ")).style(style)
        })
        .collect();

    let svc_list = List::new(svc_items)
        .block(
            Block::default()
                .borders(Borders::ALL)
                .border_style(svc_border_style)
                .title(" Service ")
                .title_style(theme::heading()),
        )
        .highlight_style(theme::selected());
    f.render_widget(svc_list, columns[0]);

    // ── Suite / Args panel ──
    let suite_border_style = if app.test_focus_suite {
        theme::selected()
    } else {
        theme::dim()
    };

    let suite_block = Block::default()
        .borders(Borders::ALL)
        .border_style(suite_border_style)
        .title(" Suite ")
        .title_style(theme::heading());

    if app.test_custom_input_active {
        // Show text input for custom args
        let inner = suite_block.inner(columns[1]);
        f.render_widget(suite_block, columns[1]);

        let input_chunks = Layout::default()
            .direction(Direction::Vertical)
            .constraints([Constraint::Length(2), Constraint::Length(3), Constraint::Min(1)])
            .margin(1)
            .split(inner);

        let prompt = Paragraph::new("Enter pytest args (e.g. tests/integration/test_foo.py::test_bar):")
            .style(theme::muted());
        f.render_widget(prompt, input_chunks[0]);

        let input_display = format!(" {} ", app.test_custom_args);
        let input = Paragraph::new(input_display.as_str())
            .style(theme::selected())
            .block(
                Block::default()
                    .borders(Borders::ALL)
                    .border_style(theme::selected()),
            );
        f.render_widget(input, input_chunks[1]);

        let hint = Paragraph::new("Enter to confirm  Esc to cancel").style(theme::dim());
        f.render_widget(hint, input_chunks[2]);
    } else {
        let suite_items: Vec<ListItem> = TEST_SUITES
            .iter()
            .enumerate()
            .map(|(i, name)| {
                let label = if name == &"Custom Args" && !app.test_custom_args.is_empty() {
                    format!("  {name}: {}  ", app.test_custom_args)
                } else {
                    format!("  {name}  ")
                };
                let style = if i == app.test_suite_selected && app.test_focus_suite {
                    theme::selected()
                } else if i == app.test_suite_selected {
                    theme::muted()
                } else {
                    theme::normal()
                };
                ListItem::new(label).style(style)
            })
            .collect();

        let suite_list = List::new(suite_items).block(suite_block);
        f.render_widget(suite_list, columns[1]);
    }

    // ── Help bar ──
    let help_spans = vec![
        Span::styled("Tab", theme::info()),
        Span::styled(" Switch panel  ", theme::muted()),
        Span::styled("↑↓", theme::info()),
        Span::styled(" Select  ", theme::muted()),
        Span::styled("Enter", theme::info()),
        Span::styled(" Run  ", theme::muted()),
        Span::styled("Esc", theme::info()),
        Span::styled(" Back", theme::muted()),
    ];
    let help = Paragraph::new(Line::from(help_spans));
    f.render_widget(help, outer[2]);
}

fn render_log_viewer(f: &mut Frame, app: &App) {
    let chunks = Layout::default()
        .direction(Direction::Vertical)
        .constraints([
            Constraint::Length(3),
            Constraint::Length(1),
            Constraint::Min(6),
            Constraint::Length(3),
        ])
        .margin(2)
        .split(f.area());

    let svc = TEST_SERVICES_DISPLAY
        .get(app.test_service_selected)
        .copied()
        .unwrap_or("unknown");
    let suite = TEST_SUITES
        .get(app.test_suite_selected)
        .copied()
        .unwrap_or("unknown");

    let title = Paragraph::new(format!("Test Output — {svc} / {suite}"))
        .style(theme::title())
        .alignment(Alignment::Center);
    f.render_widget(title, chunks[0]);

    let tick = app.test_tick;
    let spinner_char = SPINNER[tick % SPINNER.len()];

    let subtitle = if app.test_action_running {
        Paragraph::new(Line::from(vec![
            Span::styled(format!("{spinner_char} "), theme::info()),
            Span::styled("Running tests...", theme::info()),
        ]))
        .alignment(Alignment::Center)
    } else if app.test_action_complete {
        let last = app.test_log.last().map(|s| s.as_str()).unwrap_or("");
        if last.contains("ERROR") || last.contains("FAILED") || last.contains("failed") || last.contains("error") {
            Paragraph::new("Tests failed")
                .style(theme::error())
                .alignment(Alignment::Center)
        } else {
            Paragraph::new("Tests complete")
                .style(theme::success())
                .alignment(Alignment::Center)
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
            } else if l.starts_with("  ") || l.starts_with("Running") || l.starts_with("make") {
                theme::info()
            } else if l.contains("warning") || l.contains("WARNING") {
                theme::warning()
            } else {
                theme::normal()
            };
            Line::from(Span::styled(l.as_str(), style))
        })
        .collect();

    let scrollbar_info = if app.test_log.len() > log_height {
        format!(
            " Output ({}-{} of {}) ",
            scroll + 1,
            (scroll + log_height).min(app.test_log.len()),
            app.test_log.len()
        )
    } else {
        " Output ".to_string()
    };

    let log_panel = Paragraph::new(visible).block(
        Block::default()
            .borders(Borders::ALL)
            .border_style(theme::dim())
            .title(scrollbar_info)
            .title_style(theme::heading()),
    );
    f.render_widget(log_panel, chunks[2]);

    if app.test_log.len() > log_height {
        let mut scrollbar_state = ScrollbarState::new(app.test_log.len()).position(scroll);
        let scrollbar = Scrollbar::new(ScrollbarOrientation::VerticalRight)
            .begin_symbol(Some("↑"))
            .end_symbol(Some("↓"));
        f.render_stateful_widget(
            scrollbar,
            chunks[2].inner(Margin {
                vertical: 1,
                horizontal: 0,
            }),
            &mut scrollbar_state,
        );
    }

    let help_text = if app.test_action_running {
        " ↑/↓ Scroll  (tests running...)"
    } else {
        " ↑/↓ Scroll  PgUp/PgDn  Esc Back to picker"
    };
    let help = Paragraph::new(Line::from(Span::styled(help_text, theme::muted())));
    f.render_widget(help, chunks[3]);
}

// ─────────────────────────────────────────────────────────────────────────────
// Key handling
// ─────────────────────────────────────────────────────────────────────────────

pub fn handle_key(app: &mut App, key: KeyEvent) {
    if app.test_log_visible {
        handle_log_key(app, key);
        return;
    }

    if app.test_custom_input_active {
        handle_custom_input_key(app, key);
        return;
    }

    handle_picker_key(app, key);
}

fn handle_picker_key(app: &mut App, key: KeyEvent) {
    match key.code {
        KeyCode::Esc => {
            app.screen = Screen::Welcome;
            app.action_menu_selected = 0;
        }
        KeyCode::Tab => {
            app.test_focus_suite = !app.test_focus_suite;
        }
        KeyCode::Up => {
            if app.test_focus_suite {
                if app.test_suite_selected > 0 {
                    app.test_suite_selected -= 1;
                }
            } else if app.test_service_selected > 0 {
                app.test_service_selected -= 1;
            }
        }
        KeyCode::Down => {
            if app.test_focus_suite {
                if app.test_suite_selected + 1 < TEST_SUITES.len() {
                    app.test_suite_selected += 1;
                }
            } else if app.test_service_selected + 1 < TEST_SERVICES.len() {
                app.test_service_selected += 1;
            }
        }
        KeyCode::Enter => {
            // If custom suite selected, first collect args
            if TEST_SUITES.get(app.test_suite_selected) == Some(&"Custom Args") && app.test_custom_args.is_empty() {
                app.test_focus_suite = true;
                app.test_custom_input_active = true;
                return;
            }
            spawn_test_worker(app);
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
                spawn_test_worker(app);
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
    let log_height: usize = 20; // approximate; scrolling is clamped anyway
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
        _ => {}
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// Worker
// ─────────────────────────────────────────────────────────────────────────────

pub fn spawn_test_worker(app: &mut App) {
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

    std::thread::spawn(move || {
        let remote_path = profile_remote_path
            .as_deref()
            .unwrap_or("~/busibox")
            .to_string();

        // Build the make command arguments.
        // Docker backend uses make test-docker; Proxmox/remote uses make test with INV.
        let is_docker = profile_backend == "docker";

        let make_args = if suite == "Security" {
            // Security tests don't take a SERVICE argument
            "test-security".to_string()
        } else if suite == "Unit" {
            if is_docker {
                format!("test-docker SERVICE={svc_key} ARGS=\"tests/unit\"")
            } else {
                format!("test SERVICE={svc_key} INV={profile_env} ARGS=\"tests/unit\"")
            }
        } else if suite == "Custom Args" && !custom_args.is_empty() {
            if is_docker {
                format!("test-docker SERVICE={svc_key} ARGS=\"{custom_args}\"")
            } else {
                format!("test SERVICE={svc_key} INV={profile_env} ARGS=\"{custom_args}\"")
            }
        } else {
            // Integration (default)
            if is_docker {
                format!("test-docker SERVICE={svc_key}")
            } else {
                format!("test SERVICE={svc_key} INV={profile_env}")
            }
        };

        let _ = tx.send(TestUpdate::Log(format!("Running: make {make_args}")));
        let _ = tx.send(TestUpdate::Log(format!(
            "Environment: {profile_env} ({profile_backend})"
        )));
        let _ = tx.send(TestUpdate::Log(String::new()));

        let stream_tx = tx.clone();
        let on_line = move |line: &str| {
            let _ = stream_tx.send(TestUpdate::Log(format!("  {line}")));
        };

        let result: color_eyre::Result<i32> = if is_remote {
            if let Some((ref host, ref user, ref key)) = ssh_details {
                let display_host = profile_host.as_deref().unwrap_or(host);
                let ssh = crate::modules::ssh::SshConnection::new(display_host, user, key);
                remote::exec_make_quiet_with_vault_streaming(
                    &ssh,
                    &remote_path,
                    &make_args,
                    &vault_password,
                    on_line,
                )
            } else {
                Err(color_eyre::eyre::eyre!("Remote profile has no SSH host configured"))
            }
        } else {
            remote::run_local_make_quiet_with_vault_streaming(
                &repo_root,
                &make_args,
                &vault_password,
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
                let _ = tx.send(TestUpdate::Log(format!(
                    "Tests FAILED (exit code {code})"
                )));
                let _ = tx.send(TestUpdate::Complete { success: false });
            }
            Err(e) => {
                let _ = tx.send(TestUpdate::Log(format!("ERROR: {e}")));
                let _ = tx.send(TestUpdate::Complete { success: false });
            }
        }
    });
}
