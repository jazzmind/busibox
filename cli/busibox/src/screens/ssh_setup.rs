use crate::app::{App, Screen, SshSetupStatus};
use crate::modules::ssh;
use crate::theme;
use crossterm::event::{KeyCode, KeyEvent};
use ratatui::prelude::*;
use ratatui::widgets::*;

pub fn render(f: &mut Frame, app: &App) {
    let chunks = Layout::default()
        .direction(Direction::Vertical)
        .constraints([
            Constraint::Length(3),
            Constraint::Min(12),
            Constraint::Length(3),
        ])
        .margin(2)
        .split(f.area());

    let title = Paragraph::new("SSH Setup")
        .style(theme::title())
        .alignment(Alignment::Center);
    f.render_widget(title, chunks[0]);

    let mut lines = vec![
        Line::from(vec![
            Span::styled("  Host: ", theme::muted()),
            Span::styled(&app.remote_host_input, theme::info()),
        ]),
        Line::from(vec![
            Span::styled("  User: ", theme::muted()),
            Span::styled(&app.remote_user_input, theme::normal()),
        ]),
        Line::from(""),
    ];

    match &app.ssh_status {
        SshSetupStatus::NotStarted => {
            lines.push(Line::from(Span::styled(
                "  Checking SSH keys...",
                theme::info(),
            )));
        }
        SshSetupStatus::CheckingKeys => {
            lines.push(Line::from(Span::styled(
                "  Checking for existing SSH keys...",
                theme::info(),
            )));
        }
        SshSetupStatus::KeyFound(path) => {
            lines.push(Line::from(vec![
                Span::styled("  ✓ ", theme::success()),
                Span::styled(format!("Found key: {path}"), theme::normal()),
            ]));
            lines.push(Line::from(""));
            lines.push(Line::from(Span::styled(
                "  Key not yet authorized on remote host.",
                theme::warning(),
            )));
            lines.push(Line::from(Span::styled(
                "  Press Enter to copy key (will prompt for password)...",
                theme::info(),
            )));
        }
        SshSetupStatus::NoKeyFound => {
            lines.push(Line::from(vec![
                Span::styled("  ! ", theme::warning()),
                Span::styled("No SSH keys found", theme::normal()),
            ]));
            lines.push(Line::from(Span::styled(
                "  Generating a new ed25519 key...",
                theme::info(),
            )));
        }
        SshSetupStatus::Generating => {
            lines.push(Line::from(Span::styled(
                "  Generating ed25519 key...",
                theme::info(),
            )));
        }
        SshSetupStatus::KeyGenerated(path) => {
            lines.push(Line::from(vec![
                Span::styled("  ✓ ", theme::success()),
                Span::styled(format!("Key generated: {path}"), theme::normal()),
            ]));
            lines.push(Line::from(""));
            lines.push(Line::from(Span::styled(
                "  Press Enter to copy key to remote host (will prompt for password)...",
                theme::info(),
            )));
        }
        SshSetupStatus::CopyingKey => {
            lines.push(Line::from(Span::styled(
                "  Copying key to remote host (enter password in terminal)...",
                theme::warning(),
            )));
        }
        SshSetupStatus::Testing => {
            lines.push(Line::from(Span::styled(
                "  Testing SSH connection...",
                theme::info(),
            )));
        }
        SshSetupStatus::Connected => {
            lines.push(Line::from(vec![
                Span::styled("  ✓ ", theme::success()),
                Span::styled(
                    "SSH connection established! Continuing...",
                    theme::success(),
                ),
            ]));
        }
        SshSetupStatus::Failed(err) => {
            lines.push(Line::from(vec![
                Span::styled("  ✗ ", theme::error()),
                Span::styled(format!("SSH failed: {err}"), theme::error()),
            ]));
            lines.push(Line::from(""));
            lines.push(Line::from(Span::styled(
                "  Press 'r' to retry or Esc to go back",
                theme::muted(),
            )));
        }
    }

    let content = Paragraph::new(lines).block(
        Block::default()
            .borders(Borders::ALL)
            .border_style(theme::dim())
            .title(" SSH Connection ")
            .title_style(theme::heading()),
    );
    f.render_widget(content, chunks[1]);

    let help = Paragraph::new(Line::from(Span::styled(
        " Enter Continue  r Retry  Esc Back",
        theme::muted(),
    )));
    f.render_widget(help, chunks[2]);
}

pub fn handle_key(app: &mut App, key: KeyEvent) {
    match key.code {
        KeyCode::Esc => {
            app.screen = Screen::SetupMode;
            app.ssh_status = SshSetupStatus::NotStarted;
        }
        KeyCode::Enter => handle_enter(app),
        KeyCode::Char('r') => {
            app.ssh_status = SshSetupStatus::NotStarted;
            handle_enter(app);
        }
        _ => {}
    }
}

fn handle_enter(app: &mut App) {
    match &app.ssh_status {
        SshSetupStatus::NotStarted => {
            run_ssh_setup(app);
        }
        SshSetupStatus::KeyFound(_) | SshSetupStatus::KeyGenerated(_) => {
            // Signal main loop to suspend TUI and run ssh-copy-id
            let key_path = match &app.ssh_status {
                SshSetupStatus::KeyFound(p) | SshSetupStatus::KeyGenerated(p) => p.clone(),
                _ => return,
            };
            app.pending_ssh_copy = Some((
                key_path,
                app.remote_host_input.clone(),
                app.remote_user_input.clone(),
            ));
        }
        SshSetupStatus::Connected => {
            app.screen = Screen::HardwareReport;
        }
        SshSetupStatus::Failed(_) => {
            app.ssh_status = SshSetupStatus::NotStarted;
            run_ssh_setup(app);
        }
        _ => {}
    }
}

pub fn auto_start(app: &mut App) {
    run_ssh_setup(app);
}

fn run_ssh_setup(app: &mut App) {
    app.ssh_status = SshSetupStatus::CheckingKeys;
    let host = &app.remote_host_input;
    let user = &app.remote_user_input;

    // Try existing keys
    if let Some(key) = ssh::test_any_key_connection(host, user) {
        let path = key.private_path.to_string_lossy().to_string();
        app.ssh_connection = Some(ssh::SshConnection::new(host, user, &path));
        app.ssh_status = SshSetupStatus::Connected;
        app.screen = Screen::HardwareReport;
        return;
    }

    // Look for existing keys that might need to be copied
    let existing_keys = ssh::find_ssh_keys();
    if let Some(key) = existing_keys.first() {
        let path = key.private_path.to_string_lossy().to_string();
        app.ssh_status = SshSetupStatus::KeyFound(path);
        // Will try copy on next Enter press
        return;
    }

    // Generate new key
    app.ssh_status = SshSetupStatus::NoKeyFound;
    let key_path = dirs::home_dir()
        .unwrap_or_default()
        .join(".ssh")
        .join("busibox-remote-ed25519");

    match ssh::generate_key(&key_path) {
        Ok(key) => {
            let path = key.private_path.to_string_lossy().to_string();
            app.ssh_status = SshSetupStatus::KeyGenerated(path);
        }
        Err(e) => {
            app.ssh_status = SshSetupStatus::Failed(format!("Key generation failed: {e}"));
        }
    }
}

