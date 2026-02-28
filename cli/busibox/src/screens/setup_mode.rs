use crate::app::{App, InputMode, Screen, SetupTarget};
use crate::theme;
use crossterm::event::{KeyCode, KeyEvent};
use ratatui::prelude::*;
use ratatui::widgets::*;

pub fn render(f: &mut Frame, app: &App) {
    let chunks = Layout::default()
        .direction(Direction::Vertical)
        .constraints([
            Constraint::Length(3),
            Constraint::Length(3),
            Constraint::Min(10),
            Constraint::Length(3),
        ])
        .margin(2)
        .split(f.area());

    let title = Paragraph::new("Setup Mode")
        .style(theme::title())
        .alignment(Alignment::Center);
    f.render_widget(title, chunks[0]);

    let subtitle = Paragraph::new("Where will Busibox run?")
        .style(theme::muted())
        .alignment(Alignment::Center);
    f.render_widget(subtitle, chunks[1]);

    match app.input_mode {
        InputMode::Normal => render_mode_selection(f, app, chunks[2]),
        InputMode::Editing => render_remote_input(f, app, chunks[2]),
    }

    let help = Paragraph::new(Line::from(Span::styled(
        " ↑/↓ Navigate  Enter Select  Esc Back",
        theme::muted(),
    )));
    f.render_widget(help, chunks[3]);
}

fn render_mode_selection(f: &mut Frame, app: &App, area: Rect) {
    let choices = [
        (
            "Local Machine",
            "Install on this machine (Docker)",
        ),
        (
            "Remote Machine",
            "Install on a remote host via SSH + Tailscale",
        ),
    ];

    let items: Vec<ListItem> = choices
        .iter()
        .enumerate()
        .map(|(i, (title, desc))| {
            let style = if i == app.menu_selected {
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
            .title(" Target ")
            .title_style(theme::heading()),
    );
    f.render_widget(list, area);
}

fn render_remote_input(f: &mut Frame, app: &App, area: Rect) {
    let inner_chunks = Layout::default()
        .direction(Direction::Vertical)
        .constraints([
            Constraint::Length(3), // Host
            Constraint::Length(3), // User
            Constraint::Length(3), // Path
            Constraint::Length(3), // Backend
            Constraint::Length(3), // Environment
            Constraint::Min(0),
        ])
        .split(area);

    // Host input
    let host_style = if app.input_cursor == 0 {
        theme::highlight()
    } else {
        theme::dim()
    };
    let host = Paragraph::new(app.remote_host_input.as_str())
        .block(
            Block::default()
                .borders(Borders::ALL)
                .border_style(host_style)
                .title(" Remote Host (IP or hostname) ")
                .title_style(theme::heading()),
        )
        .style(theme::normal());
    f.render_widget(host, inner_chunks[0]);

    // User input
    let user_style = if app.input_cursor == 1 {
        theme::highlight()
    } else {
        theme::dim()
    };
    let user = Paragraph::new(app.remote_user_input.as_str())
        .block(
            Block::default()
                .borders(Borders::ALL)
                .border_style(user_style)
                .title(" SSH User ")
                .title_style(theme::heading()),
        )
        .style(theme::normal());
    f.render_widget(user, inner_chunks[1]);

    // Remote path input
    let path_style = if app.input_cursor == 2 {
        theme::highlight()
    } else {
        theme::dim()
    };
    let path = Paragraph::new(app.remote_path_input.as_str())
        .block(
            Block::default()
                .borders(Borders::ALL)
                .border_style(path_style)
                .title(" Remote Busibox Path ")
                .title_style(theme::heading()),
        )
        .style(theme::normal());
    f.render_widget(path, inner_chunks[2]);

    // Backend choice
    let backends = app.backend_choices();
    let backend_text = backends[app.remote_backend_choice];
    let backend_style = if app.input_cursor == 3 {
        theme::highlight()
    } else {
        theme::dim()
    };
    let backend = Paragraph::new(format!("< {backend_text} >"))
        .block(
            Block::default()
                .borders(Borders::ALL)
                .border_style(backend_style)
                .title(" Backend on Remote Host ")
                .title_style(theme::heading()),
        )
        .style(theme::normal());
    f.render_widget(backend, inner_chunks[3]);

    // Environment choice
    let envs = app.env_choices();
    let env_text = envs[app.remote_env_choice];
    let env_style = if app.input_cursor == 4 {
        theme::highlight()
    } else {
        theme::dim()
    };
    let env = Paragraph::new(format!("< {env_text} >"))
        .block(
            Block::default()
                .borders(Borders::ALL)
                .border_style(env_style)
                .title(" Environment ")
                .title_style(theme::heading()),
        )
        .style(theme::normal());
    f.render_widget(env, inner_chunks[4]);

    if app.input_cursor <= 2 {
        let text_len = match app.input_cursor {
            0 => app.remote_host_input.len(),
            1 => app.remote_user_input.len(),
            2 => app.remote_path_input.len(),
            _ => 0,
        };
        let x = inner_chunks[app.input_cursor].x + 1 + text_len as u16;
        let y = inner_chunks[app.input_cursor].y + 1;
        f.set_cursor_position(Position::new(x, y));
    }
}

pub fn handle_key(app: &mut App, key: KeyEvent) {
    match app.input_mode {
        InputMode::Normal => handle_mode_selection(app, key),
        InputMode::Editing => handle_remote_input(app, key),
    }
}

fn handle_mode_selection(app: &mut App, key: KeyEvent) {
    match key.code {
        KeyCode::Esc => {
            app.screen = Screen::Welcome;
            app.menu_selected = 0;
        }
        KeyCode::Up | KeyCode::Char('k') => {
            if app.menu_selected > 0 {
                app.menu_selected -= 1;
            }
        }
        KeyCode::Down | KeyCode::Char('j') => {
            if app.menu_selected < 1 {
                app.menu_selected += 1;
            }
        }
        KeyCode::Enter => match app.menu_selected {
            0 => {
                app.setup_target = SetupTarget::Local;
                app.screen = Screen::HardwareReport;
            }
            1 => {
                app.setup_target = SetupTarget::Remote;
                app.input_mode = InputMode::Editing;
                app.input_cursor = 0;
            }
            _ => {}
        },
        _ => {}
    }
}

fn handle_remote_input(app: &mut App, key: KeyEvent) {
    match key.code {
        KeyCode::Esc => {
            app.input_mode = InputMode::Normal;
            app.menu_selected = 0;
        }
        KeyCode::Tab | KeyCode::Down => {
            app.input_cursor = (app.input_cursor + 1).min(4);
        }
        KeyCode::BackTab | KeyCode::Up => {
            if app.input_cursor > 0 {
                app.input_cursor -= 1;
            }
        }
        KeyCode::Enter => {
            if !app.remote_host_input.is_empty() {
                app.screen = Screen::SshSetup;
                app.input_mode = InputMode::Normal;
            }
        }
        KeyCode::Left => {
            if app.input_cursor == 3 {
                if app.remote_backend_choice > 0 {
                    app.remote_backend_choice -= 1;
                }
            } else if app.input_cursor == 4 && app.remote_env_choice > 0 {
                app.remote_env_choice -= 1;
            }
        }
        KeyCode::Right => {
            if app.input_cursor == 3 {
                let max = app.backend_choices().len() - 1;
                if app.remote_backend_choice < max {
                    app.remote_backend_choice += 1;
                }
            } else if app.input_cursor == 4 {
                let max = app.env_choices().len() - 1;
                if app.remote_env_choice < max {
                    app.remote_env_choice += 1;
                }
            }
        }
        KeyCode::Char(c) => {
            if app.input_cursor == 0 {
                app.remote_host_input.push(c);
            } else if app.input_cursor == 1 {
                app.remote_user_input.push(c);
            } else if app.input_cursor == 2 {
                app.remote_path_input.push(c);
            }
        }
        KeyCode::Backspace => {
            if app.input_cursor == 0 {
                app.remote_host_input.pop();
            } else if app.input_cursor == 1 {
                app.remote_user_input.pop();
            } else if app.input_cursor == 2 {
                app.remote_path_input.pop();
            }
        }
        _ => {}
    }
}
