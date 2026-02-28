use crate::app::{App, Screen};
use crate::theme;
use crossterm::event::{KeyCode, KeyEvent};
use ratatui::prelude::*;
use ratatui::widgets::*;

pub fn render(f: &mut Frame, app: &App) {
    let chunks = Layout::default()
        .direction(Direction::Vertical)
        .constraints([
            Constraint::Length(7),
            Constraint::Length(3),
            Constraint::Min(8),
            Constraint::Length(3),
        ])
        .margin(2)
        .split(f.area());

    // Logo
    let logo = Paragraph::new(theme::LOGO.trim_start_matches('\n'))
        .style(theme::title())
        .alignment(Alignment::Center);
    f.render_widget(logo, chunks[0]);

    // Subtitle
    let subtitle = Paragraph::new("Local LLM Infrastructure Platform")
        .style(theme::muted())
        .alignment(Alignment::Center);
    f.render_widget(subtitle, chunks[1]);

    // Main content: system info + menu
    let content_chunks = Layout::default()
        .direction(Direction::Horizontal)
        .constraints([Constraint::Percentage(50), Constraint::Percentage(50)])
        .split(chunks[2]);

    // System info panel
    let mut info_lines = vec![
        Line::from(Span::styled("System Info", theme::heading())),
        Line::from(""),
    ];

    if let Some(hw) = &app.local_hardware {
        info_lines.push(Line::from(vec![
            Span::styled("  OS:    ", theme::muted()),
            Span::styled(hw.os.to_string(), theme::normal()),
        ]));
        info_lines.push(Line::from(vec![
            Span::styled("  Arch:  ", theme::muted()),
            Span::styled(hw.arch.to_string(), theme::normal()),
        ]));
        info_lines.push(Line::from(vec![
            Span::styled("  RAM:   ", theme::muted()),
            Span::styled(format!("{} GB", hw.ram_gb), theme::normal()),
        ]));
        info_lines.push(Line::from(vec![
            Span::styled("  LLM:   ", theme::muted()),
            Span::styled(hw.llm_backend.to_string(), theme::normal()),
        ]));
        info_lines.push(Line::from(vec![
            Span::styled("  Tier:  ", theme::muted()),
            Span::styled(hw.memory_tier.to_string(), theme::info()),
        ]));
        if !hw.gpus.is_empty() {
            for gpu in &hw.gpus {
                info_lines.push(Line::from(vec![
                    Span::styled("  GPU:   ", theme::muted()),
                    Span::styled(
                        format!("{} ({}GB)", gpu.name, gpu.vram_gb),
                        theme::normal(),
                    ),
                ]));
            }
        }
    } else {
        info_lines.push(Line::from(Span::styled(
            "  Detecting...",
            theme::muted(),
        )));
    }

    if let Some((id, profile)) = app.active_profile() {
        info_lines.push(Line::from(""));
        info_lines.push(Line::from(Span::styled("Active Profile", theme::heading())));
        info_lines.push(Line::from(vec![
            Span::styled("  Name:  ", theme::muted()),
            Span::styled(id, theme::info()),
        ]));
        info_lines.push(Line::from(vec![
            Span::styled("  Env:   ", theme::muted()),
            Span::styled(&profile.environment, theme::normal()),
        ]));
        info_lines.push(Line::from(vec![
            Span::styled("  Back:  ", theme::muted()),
            Span::styled(&profile.backend, theme::normal()),
        ]));
        if profile.remote {
            info_lines.push(Line::from(vec![
                Span::styled("  Host:  ", theme::muted()),
                Span::styled(
                    profile.effective_host().unwrap_or("—"),
                    theme::info(),
                ),
            ]));
        }
        // Show a hint about install status
        info_lines.push(Line::from(""));
        info_lines.push(Line::from(Span::styled(
            "  ↳ Select 'Resume Install' to deploy",
            theme::muted(),
        )));
    }

    let info_block = Paragraph::new(info_lines).block(
        Block::default()
            .borders(Borders::ALL)
            .border_style(theme::dim())
            .title(" System ")
            .title_style(theme::heading()),
    );
    f.render_widget(info_block, content_chunks[0]);

    // Menu panel
    let menu_items = app.welcome_menu_items();
    let items: Vec<ListItem> = menu_items
        .iter()
        .enumerate()
        .map(|(i, item)| {
            let style = if i == app.menu_selected {
                theme::selected()
            } else {
                theme::normal()
            };
            ListItem::new(format!("  {item}  ")).style(style)
        })
        .collect();

    let menu = List::new(items)
        .block(
            Block::default()
                .borders(Borders::ALL)
                .border_style(theme::dim())
                .title(" Menu ")
                .title_style(theme::heading()),
        )
        .highlight_style(theme::selected());
    f.render_widget(menu, content_chunks[1]);

    // Status bar
    let status_text = if let Some((msg, kind)) = &app.status_message {
        let style = match kind {
            crate::app::MessageKind::Info => theme::info(),
            crate::app::MessageKind::Success => theme::success(),
            crate::app::MessageKind::Warning => theme::warning(),
            crate::app::MessageKind::Error => theme::error(),
        };
        Span::styled(msg.as_str(), style)
    } else {
        Span::styled(
            " ↑/↓ Navigate  Enter Select  q Quit",
            theme::muted(),
        )
    };
    let status = Paragraph::new(Line::from(status_text));
    f.render_widget(status, chunks[3]);
}

pub fn handle_key(app: &mut App, key: KeyEvent) {
    let menu_items = app.welcome_menu_items();
    match key.code {
        KeyCode::Char('q') | KeyCode::Esc => app.should_quit = true,
        KeyCode::Up | KeyCode::Char('k') => {
            if app.menu_selected > 0 {
                app.menu_selected -= 1;
            }
        }
        KeyCode::Down | KeyCode::Char('j') => {
            if app.menu_selected < menu_items.len() - 1 {
                app.menu_selected += 1;
            }
        }
        KeyCode::Enter => {
            let item = menu_items[app.menu_selected];
            match item {
                "Setup New" => {
                    app.screen = Screen::SetupMode;
                    app.menu_selected = 0;
                }
                "Resume Install" => {
                    app.set_message(
                        "⠋ Connecting to remote host...",
                        crate::app::MessageKind::Info,
                    );
                    app.pending_resume_install = true;
                }
                "Profiles" => {
                    app.screen = Screen::ProfileSelect;
                    app.menu_selected = 0;
                }
                "Manage" => {
                    app.screen = Screen::Manage;
                    app.menu_selected = 0;
                }
                "Quit" => app.should_quit = true,
                _ => {}
            }
        }
        KeyCode::Char('s') => {
            app.screen = Screen::SetupMode;
            app.menu_selected = 0;
        }
        _ => {}
    }
}
