use crate::app::{App, MessageKind, Screen};
use crate::modules::profile;
use crate::theme;
use crossterm::event::{KeyCode, KeyEvent};
use ratatui::prelude::*;
use ratatui::widgets::*;

pub fn render(f: &mut Frame, app: &App) {
    let chunks = Layout::default()
        .direction(Direction::Vertical)
        .constraints([
            Constraint::Length(3),
            Constraint::Min(10),
            Constraint::Length(3),
        ])
        .margin(2)
        .split(f.area());

    let title = Paragraph::new("Profiles")
        .style(theme::title())
        .alignment(Alignment::Center);
    f.render_widget(title, chunks[0]);

    if let Some(profiles) = &app.profiles {
        let items: Vec<ListItem> = profiles
            .profiles
            .iter()
            .enumerate()
            .map(|(i, (id, profile))| {
                let is_active = id == &profiles.active;
                let marker = if is_active { "▸" } else { " " };

                let style = if i == app.profile_selected {
                    theme::selected()
                } else if is_active {
                    theme::highlight()
                } else {
                    theme::normal()
                };

                let remote_info = if profile.remote {
                    format!(
                        " → {}",
                        profile.effective_host().unwrap_or("unknown")
                    )
                } else {
                    " (local)".into()
                };

                let hw_info = profile
                    .hardware
                    .as_ref()
                    .map(|h| format!(" [{} {} {}GB]", h.os, h.arch, h.ram_gb))
                    .unwrap_or_default();

                ListItem::new(vec![
                    Line::from(Span::styled(
                        format!("{marker} {id} — {}", profile.label),
                        style,
                    )),
                    Line::from(Span::styled(
                        format!(
                            "    {} / {}{}{}",
                            profile.environment, profile.backend, remote_info, hw_info
                        ),
                        theme::muted(),
                    )),
                    Line::from(""),
                ])
            })
            .collect();

        let list = List::new(items).block(
            Block::default()
                .borders(Borders::ALL)
                .border_style(theme::dim())
                .title(format!(" {} profiles ", profiles.profiles.len()))
                .title_style(theme::heading()),
        );
        f.render_widget(list, chunks[1]);
    } else {
        let msg = Paragraph::new("No profiles found. Run Setup to create one.")
            .style(theme::muted())
            .alignment(Alignment::Center)
            .block(
                Block::default()
                    .borders(Borders::ALL)
                    .border_style(theme::dim()),
            );
        f.render_widget(msg, chunks[1]);
    }

    let help = Paragraph::new(Line::from(Span::styled(
        " Enter Activate  ↑/↓ Navigate  Esc Back",
        theme::muted(),
    )));
    f.render_widget(help, chunks[2]);
}

pub fn handle_key(app: &mut App, key: KeyEvent) {
    let profile_count = app
        .profiles
        .as_ref()
        .map(|p| p.profiles.len())
        .unwrap_or(0);

    match key.code {
        KeyCode::Esc => {
            app.screen = Screen::Welcome;
            app.menu_selected = 0;
        }
        KeyCode::Up | KeyCode::Char('k') => {
            if app.profile_selected > 0 {
                app.profile_selected -= 1;
            }
        }
        KeyCode::Down | KeyCode::Char('j') => {
            if app.profile_selected < profile_count.saturating_sub(1) {
                app.profile_selected += 1;
            }
        }
        KeyCode::Enter => {
            if let Some(profiles) = &app.profiles {
                let profile_ids: Vec<&String> = profiles.profiles.keys().collect();
                if let Some(id) = profile_ids.get(app.profile_selected) {
                    let id = (*id).clone();
                    if let Some(profiles) = &mut app.profiles {
                        profiles.active = id.clone();
                        if let Err(e) =
                            profile::save_profiles(&app.repo_root, profiles)
                        {
                            app.set_message(
                                &format!("Failed to save: {e}"),
                                MessageKind::Error,
                            );
                        } else {
                            app.set_message(
                                &format!("Switched to profile: {id}"),
                                MessageKind::Success,
                            );
                            app.screen = Screen::Welcome;
                            app.menu_selected = 0;
                        }
                    }
                }
            }
        }
        _ => {}
    }
}
