use crate::app::{App, MessageKind, Screen};
use crate::theme;
use crossterm::event::{KeyCode, KeyEvent};
use ratatui::prelude::*;
use ratatui::widgets::*;

pub fn render(f: &mut Frame, app: &App) {
    let chunks = Layout::default()
        .direction(Direction::Vertical)
        .constraints([
            Constraint::Length(5),
            Constraint::Length(3),
            Constraint::Min(10),
            Constraint::Length(3),
        ])
        .margin(2)
        .split(f.area());

    // Logo
    let logo = Paragraph::new(theme::LOGO)
        .style(theme::title())
        .alignment(Alignment::Center);
    f.render_widget(logo, chunks[0]);

    // Title — show which mode is active
    let mode_label = if app.admin_login_use_setup { "Admin Login (Setup)" } else { "Admin Login" };
    let title = Paragraph::new(mode_label)
        .style(theme::heading())
        .alignment(Alignment::Center);
    f.render_widget(title, chunks[1]);

    // Main content
    let mut lines: Vec<Line> = Vec::new();

    if app.admin_login_loading {
        lines.push(Line::from(Span::styled(
            "  Generating admin credentials...",
            theme::info(),
        )));
    } else if let Some(error) = &app.admin_login_error {
        lines.push(Line::from(Span::styled(
            "  Failed to generate login credentials",
            theme::error(),
        )));
        lines.push(Line::from(""));
        // Show each line of the error/debug output, wrapping for readability
        for err_line in error.lines() {
            lines.push(Line::from(Span::styled(
                format!("  {err_line}"),
                theme::error(),
            )));
        }
        lines.push(Line::from(""));
        lines.push(Line::from(vec![
            Span::styled("  Press ", theme::muted()),
            Span::styled("c", theme::key_hint()),
            Span::styled(" to copy full output to clipboard", theme::muted()),
        ]));
    } else if let Some(magic_link) = &app.admin_login_magic_link {
        lines.push(Line::from(Span::styled(
            "  Magic Link (expires in 24h):",
            theme::heading(),
        )));
        lines.push(Line::from(""));
        lines.push(Line::from(Span::styled(
            format!("  {magic_link}"),
            theme::info(),
        )));

        if let Some(totp) = &app.admin_login_totp_code {
            lines.push(Line::from(""));
            lines.push(Line::from(Span::styled(
                "  Login Code (expires in 15min):",
                theme::heading(),
            )));
            lines.push(Line::from(""));
            lines.push(Line::from(Span::styled(
                format!("  {totp}"),
                theme::highlight(),
            )));
        }

        if let Some(verify_url) = &app.admin_login_verify_url {
            lines.push(Line::from(""));
            lines.push(Line::from(Span::styled(
                "  Or enter code at:",
                theme::muted(),
            )));
            lines.push(Line::from(Span::styled(
                format!("  {verify_url}"),
                theme::info(),
            )));
        }

        lines.push(Line::from(""));
        if app.ssh_tunnel_active || app.ssh_tunnel_process.is_some() {
            lines.push(Line::from(Span::styled(
                "  SSH tunnel active (local:4443 → remote:443)",
                theme::muted(),
            )));
        }
        lines.push(Line::from(""));
        lines.push(Line::from(vec![
            Span::styled("  Press ", theme::muted()),
            Span::styled("o", theme::key_hint()),
            Span::styled(" to open in browser, ", theme::muted()),
            Span::styled("c", theme::key_hint()),
            Span::styled(" to copy link to clipboard", theme::muted()),
        ]));
    } else {
        lines.push(Line::from(Span::styled(
            "  No login credentials available.",
            theme::muted(),
        )));
    }

    let content = Paragraph::new(lines).block(
        Block::default()
            .borders(Borders::ALL)
            .border_style(theme::dim())
            .title(" Admin Credentials ")
            .title_style(theme::heading()),
    );
    f.render_widget(content, chunks[2]);

    // Help bar
    let toggle_hint = if app.admin_login_use_setup { "s→Login" } else { "s→Setup" };
    let help_text = if app.admin_login_loading {
        " Generating...".to_string()
    } else if app.admin_login_magic_link.is_some() {
        format!(" o Open Browser  c Copy Link  r Regenerate  {toggle_hint}  Esc Back")
    } else if app.admin_login_error.is_some() {
        format!(" c Copy Output  r Retry  {toggle_hint}  Esc Back")
    } else {
        " Esc Back".to_string()
    };
    let help = Paragraph::new(Line::from(Span::styled(&help_text, theme::muted())));
    f.render_widget(help, chunks[3]);
}

pub fn handle_key(app: &mut App, key: KeyEvent) {
    match key.code {
        KeyCode::Esc => {
            // Kill SSH tunnel if running
            if let Some(ref mut child) = app.ssh_tunnel_process {
                let _ = child.kill();
                let _ = child.wait();
            }
            app.ssh_tunnel_process = None;
            app.screen = Screen::Welcome;
            app.menu_selected = 0;
        }
        KeyCode::Char('o') => {
            if let Some(link) = &app.admin_login_magic_link {
                crate::screens::install::open_browser(link);
                app.set_message("Opened in browser", MessageKind::Info);
            }
        }
        KeyCode::Char('c') => {
            if let Some(link) = &app.admin_login_magic_link {
                let _ = copy_to_clipboard(link);
                app.set_message("Magic link copied to clipboard", MessageKind::Success);
            } else if let Some(error) = &app.admin_login_error {
                let _ = copy_to_clipboard(error);
                app.set_message("Debug output copied to clipboard", MessageKind::Info);
            }
        }
        KeyCode::Char('r') => {
            app.admin_login_magic_link = None;
            app.admin_login_totp_code = None;
            app.admin_login_verify_url = None;
            app.admin_login_error = None;
            app.admin_login_loading = true;
            app.pending_admin_login = true;
        }
        KeyCode::Char('s') => {
            app.admin_login_use_setup = !app.admin_login_use_setup;
            app.admin_login_magic_link = None;
            app.admin_login_totp_code = None;
            app.admin_login_verify_url = None;
            app.admin_login_error = None;
            app.admin_login_loading = true;
            app.pending_admin_login = true;
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
        .spawn()?;

    #[cfg(target_os = "linux")]
    let mut child = Command::new("xclip")
        .args(["-selection", "clipboard"])
        .stdin(Stdio::piped())
        .spawn()?;

    #[cfg(not(any(target_os = "macos", target_os = "linux")))]
    return Ok(());

    #[cfg(any(target_os = "macos", target_os = "linux"))]
    {
        if let Some(stdin) = child.stdin.as_mut() {
            stdin.write_all(text.as_bytes())?;
        }
        child.wait()?;
    }

    Ok(())
}

/// Parse JSON output from `make login --json`.
/// Expected format: {"magic_link":"...","totp_code":"...","verify_url":"...","email":"..."}
pub fn parse_login_json(output: &str) -> Option<LoginCredentials> {
    // Find the JSON line in the output (skip any non-JSON lines from make/ansible)
    for line in output.lines() {
        let trimmed = line.trim();
        if trimmed.starts_with('{') && trimmed.ends_with('}') {
            // Simple manual JSON parsing to avoid adding serde_json dependency
            // just for this one struct (serde_json may already be available though)
            let magic_link = extract_json_string(trimmed, "magic_link");
            let totp_code = extract_json_string(trimmed, "totp_code");
            let verify_url = extract_json_string(trimmed, "verify_url");
            let email = extract_json_string(trimmed, "email");

            if magic_link.is_some() {
                return Some(LoginCredentials {
                    magic_link: magic_link.unwrap_or_default(),
                    totp_code: totp_code.unwrap_or_default(),
                    verify_url: verify_url.unwrap_or_default(),
                    email: email.unwrap_or_default(),
                });
            }
        }
    }
    None
}

/// Extract a string value from a simple flat JSON object.
/// Handles basic JSON like {"key":"value","key2":"value2"}
fn extract_json_string(json: &str, key: &str) -> Option<String> {
    let search = format!("\"{}\":\"", key);
    if let Some(start) = json.find(&search) {
        let value_start = start + search.len();
        if let Some(end) = json[value_start..].find('"') {
            let value = &json[value_start..value_start + end];
            // Unescape basic JSON escapes
            let unescaped = value
                .replace("\\\"", "\"")
                .replace("\\\\", "\\")
                .replace("\\/", "/");
            return Some(unescaped);
        }
    }
    None
}

#[derive(Debug, Clone)]
#[allow(dead_code)]
pub struct LoginCredentials {
    pub magic_link: String,
    pub totp_code: String,
    pub verify_url: String,
    pub email: String,
}
