use crate::app::{App, Screen};
use crate::modules::remote::{KeyState, LiveState, SecretKeyStatus};
use crate::theme;
use crossterm::event::{KeyCode, KeyEvent};
use ratatui::prelude::*;
use ratatui::widgets::*;

const SPINNER_FRAMES: &[&str] = &["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"];

fn key_state_label(state: &KeyState) -> (&str, Style) {
    match state {
        KeyState::Ok => ("OK", theme::success()),
        KeyState::Missing => ("MISSING", theme::error()),
        KeyState::Placeholder => ("PLACEHOLDER", theme::warning()),
        KeyState::InsecureDefault => ("INSECURE", theme::warning()),
        KeyState::NullOrEmpty => ("NULL/EMPTY", theme::error()),
        KeyState::NotChecked => ("n/a", theme::dim()),
        KeyState::Pending => ("...", theme::info()),
    }
}

fn live_state_label(state: &LiveState) -> (&str, Style) {
    match state {
        LiveState::NotChecked => ("n/a", theme::dim()),
        LiveState::Pending => ("...", theme::info()),
        LiveState::Pass => ("PASS", theme::success()),
        LiveState::Fail(_) => ("FAIL", theme::error()),
        LiveState::EnvMatch => ("MATCH", theme::success()),
        LiveState::EnvMismatch => ("MISMATCH", theme::error()),
        LiveState::Skipped => ("skip", theme::dim()),
    }
}

fn live_state_icon(state: &LiveState) -> &'static str {
    match state {
        LiveState::Pass | LiveState::EnvMatch => "✓ ",
        LiveState::Fail(_) | LiveState::EnvMismatch => "✗ ",
        _ => "  ",
    }
}

pub fn render(f: &mut Frame, app: &App) {
    let area = f.area();
    let chunks = Layout::default()
        .direction(Direction::Vertical)
        .constraints([
            Constraint::Length(1), // profile header spacer
            Constraint::Length(3), // title
            Constraint::Length(3), // info block
            Constraint::Min(8),   // table
            Constraint::Length(2), // help bar
        ])
        .margin(1)
        .split(area);

    // Title
    let title = Paragraph::new("Validate Secrets")
        .style(theme::title())
        .alignment(Alignment::Center);
    f.render_widget(title, chunks[1]);

    // Info block: vault file, status summary
    let info_text = if app.validate_secrets_loading {
        let tick = app.manage_tick;
        let frame = SPINNER_FRAMES[tick % SPINNER_FRAMES.len()];
        format!("{} Loading vault secrets and running live checks...", frame)
    } else if let Some(ref err) = app.validate_secrets_error {
        format!("Error: {}", err)
    } else {
        let total = app.validate_secrets_results.len();
        let required: Vec<&SecretKeyStatus> = app
            .validate_secrets_results
            .iter()
            .filter(|k| k.required)
            .collect();
        let ok_count = required.iter().filter(|k| k.local == KeyState::Ok).count();
        let live_pass = required
            .iter()
            .filter(|k| matches!(k.live, LiveState::Pass | LiveState::EnvMatch))
            .count();
        let live_fail = required
            .iter()
            .filter(|k| k.live.is_bad())
            .count();
        let remote_label = if app.validate_secrets_is_remote {
            let remote_ok = required
                .iter()
                .filter(|k| k.remote == KeyState::Ok)
                .count();
            format!(" | Remote: {}/{} OK", remote_ok, required.len())
        } else {
            String::new()
        };
        let live_label = if live_fail > 0 {
            format!(" | Live: {}/{} OK, {} FAIL", live_pass, required.len(), live_fail)
        } else {
            format!(" | Live: {}/{} OK", live_pass, required.len())
        };
        format!(
            "Vault: {}  |  Local: {}/{} OK  |  {} keys{}{}",
            app.validate_secrets_vault_file,
            ok_count,
            required.len(),
            total,
            live_label,
            remote_label,
        )
    };

    let info_style = if app.validate_secrets_error.is_some() {
        theme::error()
    } else if app.validate_secrets_loading {
        theme::info()
    } else {
        theme::normal()
    };
    let info = Paragraph::new(info_text)
        .style(info_style)
        .block(
            Block::default()
                .borders(Borders::ALL)
                .border_style(theme::dim()),
        );
    f.render_widget(info, chunks[2]);

    // Table
    let table_area = chunks[3];

    if app.validate_secrets_loading && app.validate_secrets_results.is_empty() {
        let loading = Paragraph::new("Decrypting vault and checking services...")
            .style(theme::info())
            .alignment(Alignment::Center)
            .block(
                Block::default()
                    .borders(Borders::ALL)
                    .border_style(theme::dim())
                    .title(" Secrets "),
            );
        f.render_widget(loading, table_area);
    } else if app.validate_secrets_results.is_empty() {
        let empty = Paragraph::new("No secrets found")
            .style(theme::muted())
            .alignment(Alignment::Center)
            .block(
                Block::default()
                    .borders(Borders::ALL)
                    .border_style(theme::dim())
                    .title(" Secrets "),
            );
        f.render_widget(empty, table_area);
    } else {
        render_secrets_table(f, app, table_area);
    }

    // Help bar — changes when the secret popup is open
    let help = if app.validate_secrets_show_secret.is_some() {
        Paragraph::new(Line::from(vec![
            Span::styled(" Esc ", theme::highlight()),
            Span::styled("Close  ", theme::normal()),
            Span::styled("c ", theme::highlight()),
            Span::styled("Copy to clipboard", theme::normal()),
        ]))
    } else {
        Paragraph::new(Line::from(vec![
            Span::styled(" Esc ", theme::highlight()),
            Span::styled("Back  ", theme::normal()),
            Span::styled("r ", theme::highlight()),
            Span::styled("Refresh  ", theme::normal()),
            Span::styled("j/k ", theme::muted()),
            Span::styled("Scroll  ", theme::muted()),
            Span::styled("s ", theme::highlight()),
            Span::styled("View secret", theme::normal()),
        ]))
    };
    f.render_widget(help, chunks[4]);

    // Secret popup — rendered on top of everything else
    if let Some(ref key) = app.validate_secrets_show_secret.clone() {
        render_secret_popup(f, app, area, key);
    }
}

fn render_secrets_table(f: &mut Frame, app: &App, area: Rect) {
    let show_remote = app.validate_secrets_is_remote;

    // Build header -- always show Live column
    let mut header_cells = vec![
        Cell::from(" Key").style(theme::heading()),
        Cell::from("Required").style(theme::heading()),
        Cell::from("Local").style(theme::heading()),
        Cell::from("Live").style(theme::heading()),
    ];
    if show_remote {
        header_cells.push(Cell::from("Remote").style(theme::heading()));
    }
    let header = Row::new(header_cells)
        .style(Style::default().bg(theme::BRAND_DIM))
        .height(1);

    // Compute scroll offset to keep selected row visible
    let results = &app.validate_secrets_results;
    let visible_height = area.height.saturating_sub(4) as usize;
    let selected = app.validate_secrets_selected.min(results.len().saturating_sub(1));

    // Derive scroll from selected: keep selected within the visible window
    let scroll = if selected < app.validate_secrets_scroll {
        selected
    } else if selected >= app.validate_secrets_scroll + visible_height {
        selected + 1 - visible_height
    } else {
        app.validate_secrets_scroll
    }
    .min(results.len().saturating_sub(visible_height));

    let rows: Vec<Row> = results
        .iter()
        .enumerate()
        .skip(scroll)
        .take(visible_height)
        .map(|(i, entry)| {
            let is_selected = i == selected;

            let (local_label, local_style) = key_state_label(&entry.local);
            let local_icon = match &entry.local {
                KeyState::Ok => "✓ ",
                KeyState::NotChecked | KeyState::Pending => "  ",
                _ => "✗ ",
            };

            let (live_label, live_style) = live_state_label(&entry.live);
            let live_icon = live_state_icon(&entry.live);

            let req_label = if entry.required { "yes" } else { "" };
            let req_style = if is_selected {
                theme::selected()
            } else if entry.required {
                theme::info()
            } else {
                theme::dim()
            };

            let key_style = if is_selected {
                theme::selected()
            } else if entry.required && (entry.local.is_bad() || entry.live.is_bad()) {
                theme::error()
            } else if !entry.required {
                theme::muted()
            } else {
                theme::normal()
            };

            let status_style = if is_selected { theme::selected() } else { local_style };
            let live_row_style = if is_selected { theme::selected() } else { live_style };

            let has_value = !app.validate_secrets_values
                .get(&entry.key_path)
                .map(|v| v.is_empty())
                .unwrap_or(true);

            // Append a hint indicator on the key cell if value is available
            let key_display = if has_value {
                format!(" {} ›", entry.key_path)
            } else {
                format!(" {}", entry.key_path)
            };

            let mut cells = vec![
                Cell::from(key_display).style(key_style),
                Cell::from(req_label).style(req_style),
                Cell::from(format!("{}{}", local_icon, local_label)).style(status_style),
                Cell::from(format!("{}{}", live_icon, live_label)).style(live_row_style),
            ];

            if show_remote {
                let (remote_label, remote_style) = key_state_label(&entry.remote);
                let remote_icon = match &entry.remote {
                    KeyState::Ok => "✓ ",
                    KeyState::NotChecked | KeyState::Pending => "  ",
                    _ => "✗ ",
                };
                let rs = if is_selected { theme::selected() } else { remote_style };
                cells.push(
                    Cell::from(format!("{}{}", remote_icon, remote_label)).style(rs),
                );
            }

            let row = Row::new(cells);
            if is_selected {
                row.style(theme::selected())
            } else {
                row
            }
        })
        .collect();

    let mut widths = vec![
        Constraint::Min(28),
        Constraint::Length(10),
        Constraint::Length(16),
        Constraint::Length(16),
    ];
    if show_remote {
        widths.push(Constraint::Length(16));
    }

    let table = Table::new(rows, widths)
        .header(header)
        .block(
            Block::default()
                .borders(Borders::ALL)
                .border_style(theme::dim())
                .title(" Secrets "),
        );
    f.render_widget(table, area);

    // Scrollbar
    if results.len() > visible_height {
        let scrollbar = Scrollbar::new(ScrollbarOrientation::VerticalRight)
            .begin_symbol(None)
            .end_symbol(None);
        let mut scrollbar_state =
            ScrollbarState::new(results.len().saturating_sub(visible_height)).position(scroll);
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

fn render_secret_popup(f: &mut Frame, app: &App, area: Rect, key: &str) {
    let value = app
        .validate_secrets_values
        .get(key)
        .map(|v| v.as_str())
        .unwrap_or("<value not available>");

    // Wrap value into lines of at most popup_inner_width chars
    let popup_width = 72u16.min(area.width.saturating_sub(8));
    let inner_width = popup_width.saturating_sub(4) as usize;

    let value_lines: Vec<Line> = if value.len() <= inner_width {
        vec![Line::from(Span::styled(value.to_string(), theme::normal()))]
    } else {
        value
            .as_bytes()
            .chunks(inner_width)
            .map(|chunk| {
                Line::from(Span::styled(
                    String::from_utf8_lossy(chunk).into_owned(),
                    theme::normal(),
                ))
            })
            .collect()
    };

    // popup height: title + key line + separator + value lines + empty + help
    let content_height = 1 + 1 + 1 + value_lines.len() as u16 + 1 + 1;
    let popup_height = (content_height + 2).min(area.height.saturating_sub(6));

    let popup_area = Rect {
        x: area.x + (area.width.saturating_sub(popup_width)) / 2,
        y: area.y + (area.height.saturating_sub(popup_height)) / 2,
        width: popup_width,
        height: popup_height,
    };

    f.render_widget(Clear, popup_area);

    let inner = popup_area.inner(Margin::new(2, 1));

    // Key path line
    let mut lines: Vec<Line> = vec![
        Line::from(vec![
            Span::styled("Key:  ", theme::muted()),
            Span::styled(key.to_string(), theme::info()),
        ]),
        Line::from(Span::styled(
            "─".repeat(inner.width as usize),
            theme::dim(),
        )),
        Line::from(vec![
            Span::styled("Value:", theme::muted()),
        ]),
    ];
    lines.extend(value_lines.into_iter().map(|l| {
        Line::from(vec![
            Span::raw("  "),
            l.spans.into_iter().next().unwrap_or_default(),
        ])
    }));
    lines.push(Line::from(""));
    lines.push(Line::from(vec![
        Span::styled(" c ", theme::highlight()),
        Span::styled("Copy to clipboard  ", theme::normal()),
        Span::styled(" Esc ", theme::highlight()),
        Span::styled("Close", theme::muted()),
    ]));

    let para = Paragraph::new(lines).wrap(Wrap { trim: false });
    f.render_widget(para, inner);

    let block = Block::default()
        .borders(Borders::ALL)
        .border_style(theme::highlight())
        .title(" View Secret ")
        .title_style(theme::heading());
    f.render_widget(block, popup_area);
}

pub fn handle_key(app: &mut App, key: KeyEvent) {
    // When the secret popup is open, only Esc (close) and c (copy) are active
    if app.validate_secrets_show_secret.is_some() {
        match key.code {
            KeyCode::Esc | KeyCode::Enter => {
                app.validate_secrets_show_secret = None;
            }
            KeyCode::Char('c') => {
                if let Some(ref k) = app.validate_secrets_show_secret.clone() {
                    if let Some(value) = app.validate_secrets_values.get(k) {
                        if copy_to_clipboard(value).is_ok() {
                            app.set_message(
                                &format!("Copied {} to clipboard", k),
                                crate::app::MessageKind::Success,
                            );
                        } else {
                            app.set_message(
                                "Clipboard copy failed (pbcopy/xclip not found?)",
                                crate::app::MessageKind::Warning,
                            );
                        }
                        app.validate_secrets_show_secret = None;
                    }
                }
            }
            _ => {}
        }
        return;
    }

    match key.code {
        KeyCode::Esc => {
            app.screen = Screen::Welcome;
            app.validate_secrets_results.clear();
            app.validate_secrets_values.clear();
            app.validate_secrets_scroll = 0;
            app.validate_secrets_selected = 0;
            app.validate_secrets_show_secret = None;
            app.validate_secrets_loading = false;
            app.validate_secrets_error = None;
        }
        KeyCode::Char('r') if !app.validate_secrets_loading => {
            app.validate_secrets_results.clear();
            app.validate_secrets_values.clear();
            app.validate_secrets_scroll = 0;
            app.validate_secrets_selected = 0;
            app.validate_secrets_show_secret = None;
            app.validate_secrets_error = None;
            app.pending_compare_secrets = true;
        }
        KeyCode::Down | KeyCode::Char('j') => {
            let max = app.validate_secrets_results.len().saturating_sub(1);
            if app.validate_secrets_selected < max {
                app.validate_secrets_selected += 1;
            }
        }
        KeyCode::Up | KeyCode::Char('k') => {
            if app.validate_secrets_selected > 0 {
                app.validate_secrets_selected -= 1;
            }
        }
        KeyCode::PageDown => {
            let max = app.validate_secrets_results.len().saturating_sub(1);
            app.validate_secrets_selected = (app.validate_secrets_selected + 10).min(max);
        }
        KeyCode::PageUp => {
            app.validate_secrets_selected = app.validate_secrets_selected.saturating_sub(10);
        }
        KeyCode::Char('s') if !app.validate_secrets_results.is_empty() => {
            let sel = app.validate_secrets_selected
                .min(app.validate_secrets_results.len().saturating_sub(1));
            if let Some(entry) = app.validate_secrets_results.get(sel) {
                let key = entry.key_path.clone();
                if app.validate_secrets_values.contains_key(&key) {
                    app.validate_secrets_show_secret = Some(key);
                } else {
                    app.set_message(
                        "No decrypted value available for this key",
                        crate::app::MessageKind::Info,
                    );
                }
            }
        }
        _ => {}
    }
}

fn copy_to_clipboard(text: &str) -> std::io::Result<()> {
    use std::io::Write;
    use std::process::{Command, Stdio};

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
        "clipboard not supported on this platform",
    ));

    if let Some(mut stdin) = child.stdin.take() {
        stdin.write_all(text.as_bytes())?;
    }
    child.wait()?;
    Ok(())
}
