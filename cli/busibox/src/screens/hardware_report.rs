use crate::app::{App, MessageKind, Screen, SetupTarget};
use crate::modules::hardware::HardwareProfile;
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

    let title = Paragraph::new("Hardware Detection")
        .style(theme::title())
        .alignment(Alignment::Center);
    f.render_widget(title, chunks[0]);

    if app.setup_target == SetupTarget::Remote
        && app.local_hardware.is_some()
        && app.remote_hardware.is_some()
    {
        // Two-column layout for local vs remote
        let cols = Layout::default()
            .direction(Direction::Horizontal)
            .constraints([Constraint::Percentage(50), Constraint::Percentage(50)])
            .split(chunks[1]);

        render_hw_panel(f, "Local", app.local_hardware.as_ref().unwrap(), cols[0]);
        render_hw_panel(f, "Remote", app.remote_hardware.as_ref().unwrap(), cols[1]);
    } else {
        let hw = if app.setup_target == SetupTarget::Remote {
            app.remote_hardware.as_ref()
        } else {
            app.local_hardware.as_ref()
        };

        if let Some(hw) = hw {
            render_hw_panel(f, "Hardware", hw, chunks[1]);
        } else {
            let detecting = Paragraph::new("Detecting hardware...")
                .style(theme::info())
                .alignment(Alignment::Center)
                .block(
                    Block::default()
                        .borders(Borders::ALL)
                        .border_style(theme::dim()),
                );
            f.render_widget(detecting, chunks[1]);
        }
    }

    let hw_ready = if app.setup_target == SetupTarget::Remote {
        app.remote_hardware.is_some()
    } else {
        app.local_hardware.is_some()
    };

    let help = if hw_ready {
        Paragraph::new(Line::from(vec![
            Span::styled(" Enter ", theme::highlight()),
            Span::styled("Continue  ", theme::normal()),
            Span::styled("Esc ", theme::muted()),
            Span::styled("Back", theme::muted()),
        ]))
    } else {
        Paragraph::new(Line::from(Span::styled(
            " ⠋ Detecting hardware...",
            theme::info(),
        )))
    };
    f.render_widget(help, chunks[2]);
}

fn render_hw_panel(f: &mut Frame, label: &str, hw: &HardwareProfile, area: Rect) {
    let mut lines = vec![
        Line::from(vec![
            Span::styled("  OS:       ", theme::muted()),
            Span::styled(hw.os.to_string(), theme::normal()),
        ]),
        Line::from(vec![
            Span::styled("  Arch:     ", theme::muted()),
            Span::styled(hw.arch.to_string(), theme::normal()),
        ]),
        Line::from(vec![
            Span::styled("  RAM:      ", theme::muted()),
            Span::styled(format!("{} GB", hw.ram_gb), theme::normal()),
        ]),
    ];

    if hw.apple_silicon {
        lines.push(Line::from(vec![
            Span::styled("  Silicon:  ", theme::muted()),
            Span::styled("Apple Silicon (Unified Memory)", theme::info()),
        ]));
    }

    if hw.gpus.is_empty() {
        lines.push(Line::from(vec![
            Span::styled("  GPU:      ", theme::muted()),
            Span::styled("None detected", theme::dim()),
        ]));
    } else {
        for (i, gpu) in hw.gpus.iter().enumerate() {
            lines.push(Line::from(vec![
                Span::styled(format!("  GPU {i}:    "), theme::muted()),
                Span::styled(
                    format!("{} ({} GB VRAM)", gpu.name, gpu.vram_gb),
                    theme::normal(),
                ),
            ]));
        }
    }

    lines.push(Line::from(""));
    lines.push(Line::from(vec![
        Span::styled("  Docker:   ", theme::muted()),
        if hw.docker_available {
            Span::styled("✓ available", theme::success())
        } else {
            Span::styled("✗ not found", theme::dim())
        },
    ]));
    lines.push(Line::from(vec![
        Span::styled("  Proxmox:  ", theme::muted()),
        if hw.proxmox_available {
            Span::styled("✓ available", theme::success())
        } else {
            Span::styled("✗ not found", theme::dim())
        },
    ]));

    lines.push(Line::from(""));
    lines.push(Line::from(vec![
        Span::styled("  LLM Backend: ", theme::muted()),
        Span::styled(hw.llm_backend.to_string(), theme::info()),
    ]));
    lines.push(Line::from(vec![
        Span::styled("  Memory Tier: ", theme::muted()),
        Span::styled(
            format!("{} - {}", hw.memory_tier, hw.memory_tier.description()),
            theme::highlight(),
        ),
    ]));

    let panel = Paragraph::new(lines).block(
        Block::default()
            .borders(Borders::ALL)
            .border_style(theme::dim())
            .title(format!(" {label} "))
            .title_style(theme::heading()),
    );
    f.render_widget(panel, area);
}

pub fn handle_key(app: &mut App, key: KeyEvent) {
    match key.code {
        KeyCode::Esc => {
            if app.setup_target == SetupTarget::Remote {
                app.screen = Screen::SshSetup;
            } else {
                app.screen = Screen::SetupMode;
            }
        }
        KeyCode::Enter => {
            let hw_ready = if app.setup_target == SetupTarget::Remote {
                app.remote_hardware.is_some()
            } else {
                app.local_hardware.is_some()
            };
            if hw_ready {
                app.screen = Screen::ModelConfig;
            }
        }
        _ => {}
    }
}

pub fn detect_hardware(app: &mut App) {
    if app.local_hardware.is_none() {
        match HardwareProfile::detect_local() {
            Ok(hw) => app.local_hardware = Some(hw),
            Err(e) => {
                app.set_message(
                    &format!("Local HW detection failed: {e}"),
                    MessageKind::Error,
                );
            }
        }
    }

    if app.setup_target == SetupTarget::Remote && app.remote_hardware.is_none() {
        if let Some(ssh) = &app.ssh_connection {
            match HardwareProfile::detect_remote(&ssh.host, &ssh.user, &ssh.key_path) {
                Ok(hw) => app.remote_hardware = Some(hw),
                Err(e) => {
                    app.set_message(
                        &format!("Remote HW detection failed: {e}"),
                        MessageKind::Error,
                    );
                }
            }
        }
    }
}
