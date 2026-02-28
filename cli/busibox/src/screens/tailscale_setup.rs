use crate::app::{App, InputMode, Screen, TailscaleAuthChoice, TailscaleStep};
use crate::modules::tailscale::{self, AuthMode};
use crate::theme;
use crossterm::event::{KeyCode, KeyEvent};
use ratatui::prelude::*;
use ratatui::widgets::*;

pub fn render(f: &mut Frame, app: &App) {
    let chunks = Layout::default()
        .direction(Direction::Vertical)
        .constraints([
            Constraint::Length(3),
            Constraint::Min(14),
            Constraint::Length(3),
        ])
        .margin(2)
        .split(f.area());

    let title = Paragraph::new("Tailscale VPN Setup")
        .style(theme::title())
        .alignment(Alignment::Center);
    f.render_widget(title, chunks[0]);

    let mut lines = Vec::new();

    // Local status
    if let Some(local) = &app.tailscale_local {
        lines.push(Line::from(Span::styled("Local Tailscale", theme::heading())));
        if local.installed && local.running {
            lines.push(Line::from(vec![
                Span::styled("  ✓ ", theme::success()),
                Span::styled(
                    format!("Running (IP: {})", local.ip.as_deref().unwrap_or("—")),
                    theme::success(),
                ),
            ]));
        } else if local.installed {
            lines.push(Line::from(vec![
                Span::styled("  ! ", theme::warning()),
                Span::styled("Installed but not connected", theme::warning()),
            ]));
        } else {
            lines.push(Line::from(vec![
                Span::styled("  ✗ ", theme::error()),
                Span::styled("Not installed", theme::error()),
            ]));
        }
    }

    // Remote status
    if let Some(remote) = &app.tailscale_remote {
        lines.push(Line::from(""));
        lines.push(Line::from(Span::styled(
            "Remote Tailscale",
            theme::heading(),
        )));
        if remote.installed && remote.running {
            lines.push(Line::from(vec![
                Span::styled("  ✓ ", theme::success()),
                Span::styled(
                    format!("Running (IP: {})", remote.ip.as_deref().unwrap_or("—")),
                    theme::success(),
                ),
            ]));
        } else if remote.installed {
            lines.push(Line::from(vec![
                Span::styled("  ! ", theme::warning()),
                Span::styled("Installed but not connected", theme::warning()),
            ]));
        } else {
            lines.push(Line::from(vec![
                Span::styled("  ✗ ", theme::error()),
                Span::styled("Not installed", theme::error()),
            ]));
        }
    }

    lines.push(Line::from(""));

    match &app.tailscale_step {
        TailscaleStep::CheckingLocal => {
            lines.push(Line::from(Span::styled(
                "  Checking local Tailscale status...",
                theme::info(),
            )));
        }
        TailscaleStep::CheckingRemote => {
            lines.push(Line::from(Span::styled(
                "  Checking remote Tailscale status...",
                theme::info(),
            )));
        }
        TailscaleStep::InstallingRemote => {
            lines.push(Line::from(Span::styled(
                "  Installing Tailscale on remote host...",
                theme::info(),
            )));
        }
        TailscaleStep::Authenticating => match &app.input_mode {
            InputMode::Normal => {
                let choices = ["Cloud (auth key)", "Headscale (self-hosted)", "Skip"];
                lines.push(Line::from(Span::styled(
                    "  Select authentication method:",
                    theme::heading(),
                )));
                for (i, choice) in choices.iter().enumerate() {
                    let style = if i == app.menu_selected {
                        theme::selected()
                    } else {
                        theme::normal()
                    };
                    lines.push(Line::from(Span::styled(format!("    {choice}"), style)));
                }
            }
            InputMode::Editing => {
                let label = match &app.tailscale_auth_choice {
                    TailscaleAuthChoice::Cloud => "Auth Key: ",
                    TailscaleAuthChoice::Headscale => "Server URL: ",
                    TailscaleAuthChoice::Skip => "",
                };
                lines.push(Line::from(vec![
                    Span::styled(format!("  {label}"), theme::muted()),
                    Span::styled(&app.tailscale_auth_input, theme::normal()),
                    Span::styled("_", theme::info()),
                ]));
                lines.push(Line::from(""));
                lines.push(Line::from(Span::styled(
                    "  Press Enter to authenticate",
                    theme::muted(),
                )));
            }
        },
        TailscaleStep::Verifying => {
            lines.push(Line::from(Span::styled(
                "  Verifying VPN connectivity...",
                theme::info(),
            )));
        }
        TailscaleStep::Done => {
            lines.push(Line::from(vec![
                Span::styled("  ✓ ", theme::success()),
                Span::styled(
                    "Tailscale VPN is connected and verified!",
                    theme::success(),
                ),
            ]));
            lines.push(Line::from(""));
            lines.push(Line::from(Span::styled(
                "  Press Enter to continue to hardware detection...",
                theme::info(),
            )));
        }
        TailscaleStep::Skipped => {
            lines.push(Line::from(vec![
                Span::styled("  — ", theme::muted()),
                Span::styled("Tailscale setup skipped", theme::muted()),
            ]));
            lines.push(Line::from(""));
            lines.push(Line::from(Span::styled(
                "  Press Enter to continue...",
                theme::info(),
            )));
        }
    }

    let content = Paragraph::new(lines).block(
        Block::default()
            .borders(Borders::ALL)
            .border_style(theme::dim())
            .title(" Tailscale VPN ")
            .title_style(theme::heading()),
    );
    f.render_widget(content, chunks[1]);

    let help = Paragraph::new(Line::from(Span::styled(
        " Enter Continue  Esc Back",
        theme::muted(),
    )));
    f.render_widget(help, chunks[2]);
}

pub fn handle_key(app: &mut App, key: KeyEvent) {
    match &app.tailscale_step {
        TailscaleStep::Authenticating => match &app.input_mode {
            InputMode::Normal => handle_auth_choice(app, key),
            InputMode::Editing => handle_auth_input(app, key),
        },
        TailscaleStep::Done | TailscaleStep::Skipped => match key.code {
            KeyCode::Enter => {
                app.screen = Screen::HardwareReport;
            }
            KeyCode::Esc => {
                app.screen = Screen::SshSetup;
                app.tailscale_step = TailscaleStep::CheckingLocal;
            }
            _ => {}
        },
        _ => match key.code {
            KeyCode::Esc => {
                app.screen = Screen::SshSetup;
                app.tailscale_step = TailscaleStep::CheckingLocal;
            }
            KeyCode::Enter => run_tailscale_step(app),
            _ => {}
        },
    }
}

fn handle_auth_choice(app: &mut App, key: KeyEvent) {
    match key.code {
        KeyCode::Up | KeyCode::Char('k') => {
            if app.menu_selected > 0 {
                app.menu_selected -= 1;
            }
        }
        KeyCode::Down | KeyCode::Char('j') => {
            if app.menu_selected < 2 {
                app.menu_selected += 1;
            }
        }
        KeyCode::Enter => match app.menu_selected {
            0 => {
                app.tailscale_auth_choice = TailscaleAuthChoice::Cloud;
                app.input_mode = InputMode::Editing;
                app.tailscale_auth_input.clear();
            }
            1 => {
                app.tailscale_auth_choice = TailscaleAuthChoice::Headscale;
                app.input_mode = InputMode::Editing;
                app.tailscale_auth_input.clear();
            }
            2 => {
                app.tailscale_step = TailscaleStep::Skipped;
            }
            _ => {}
        },
        KeyCode::Esc => {
            app.screen = Screen::SshSetup;
            app.tailscale_step = TailscaleStep::CheckingLocal;
        }
        _ => {}
    }
}

fn handle_auth_input(app: &mut App, key: KeyEvent) {
    match key.code {
        KeyCode::Esc => {
            app.input_mode = InputMode::Normal;
        }
        KeyCode::Enter => {
            if !app.tailscale_auth_input.is_empty() {
                do_tailscale_auth(app);
            }
        }
        KeyCode::Char(c) => {
            app.tailscale_auth_input.push(c);
        }
        KeyCode::Backspace => {
            app.tailscale_auth_input.pop();
        }
        _ => {}
    }
}

pub fn init_tailscale_check(app: &mut App) {
    app.tailscale_step = TailscaleStep::CheckingLocal;
    app.tailscale_local = Some(tailscale::local_status());

    if let Some(ssh) = &app.ssh_connection {
        app.tailscale_step = TailscaleStep::CheckingRemote;
        app.tailscale_remote = Some(tailscale::remote_status(ssh));
    }

    let local_ok = app
        .tailscale_local
        .as_ref()
        .map(|s| s.running)
        .unwrap_or(false);
    let remote_ok = app
        .tailscale_remote
        .as_ref()
        .map(|s| s.running)
        .unwrap_or(false);

    if local_ok && remote_ok {
        app.tailscale_step = TailscaleStep::Verifying;
        verify_tailscale(app);
    } else {
        let needs_remote_install = app
            .tailscale_remote
            .as_ref()
            .map(|s| !s.installed)
            .unwrap_or(false);

        if needs_remote_install {
            app.tailscale_step = TailscaleStep::InstallingRemote;
        } else {
            app.tailscale_step = TailscaleStep::Authenticating;
            app.menu_selected = 0;
        }
    }
}

fn run_tailscale_step(app: &mut App) {
    match &app.tailscale_step {
        TailscaleStep::InstallingRemote => {
            if let Some(ssh) = &app.ssh_connection {
                match tailscale::install_remote(ssh) {
                    Ok(()) => {
                        app.tailscale_remote = Some(tailscale::remote_status(ssh));
                        app.tailscale_step = TailscaleStep::Authenticating;
                        app.menu_selected = 0;
                    }
                    Err(e) => {
                        app.set_message(
                            &format!("Tailscale install failed: {e}"),
                            crate::app::MessageKind::Error,
                        );
                    }
                }
            }
        }
        TailscaleStep::CheckingLocal | TailscaleStep::CheckingRemote => {
            init_tailscale_check(app);
        }
        _ => {}
    }
}

fn do_tailscale_auth(app: &mut App) {
    let mode = match &app.tailscale_auth_choice {
        TailscaleAuthChoice::Cloud => AuthMode::Cloud {
            auth_key: app.tailscale_auth_input.clone(),
        },
        TailscaleAuthChoice::Headscale => AuthMode::Headscale {
            server_url: app.tailscale_auth_input.clone(),
        },
        TailscaleAuthChoice::Skip => {
            app.tailscale_step = TailscaleStep::Skipped;
            return;
        }
    };

    if let Some(ssh) = &app.ssh_connection {
        match tailscale::authenticate_remote(ssh, &mode) {
            Ok(()) => {
                app.tailscale_remote = Some(tailscale::remote_status(ssh));
                app.tailscale_step = TailscaleStep::Verifying;
                app.input_mode = InputMode::Normal;
                verify_tailscale(app);
            }
            Err(e) => {
                app.set_message(
                    &format!("Auth failed: {e}"),
                    crate::app::MessageKind::Error,
                );
            }
        }
    }
}

fn verify_tailscale(app: &mut App) {
    let remote_ip = app
        .tailscale_remote
        .as_ref()
        .and_then(|s| s.ip.clone());

    if let Some(ip) = remote_ip {
        if tailscale::verify_connectivity(&ip) {
            app.tailscale_step = TailscaleStep::Done;
        } else {
            app.set_message(
                "Tailscale connectivity check failed - may need time to converge",
                crate::app::MessageKind::Warning,
            );
            app.tailscale_step = TailscaleStep::Done;
        }
    } else {
        app.tailscale_step = TailscaleStep::Done;
    }
}
