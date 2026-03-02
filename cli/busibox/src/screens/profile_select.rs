use crate::app::{App, MessageKind, Screen};
use crate::modules::{profile, vault};
use crate::theme;
use crossterm::event::{KeyCode, KeyEvent};
use ratatui::layout::Margin;
use ratatui::prelude::*;
use ratatui::widgets::{Scrollbar, ScrollbarOrientation, ScrollbarState, *};

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

        let items_total = items.len();
        let list_height = chunks[1].height.saturating_sub(2) as usize; // borders
        let items_per_page = list_height / 3; // each profile takes 3 lines
        let scroll_offset = if app.profile_selected >= items_per_page {
            app.profile_selected - items_per_page + 1
        } else {
            0
        };
        let visible_items: Vec<ListItem> = items
            .into_iter()
            .skip(scroll_offset)
            .take(items_per_page)
            .collect();

        let list = List::new(visible_items).block(
            Block::default()
                .borders(Borders::ALL)
                .border_style(theme::dim())
                .title(format!(" {} profiles ", profiles.profiles.len()))
                .title_style(theme::heading()),
        );
        f.render_widget(list, chunks[1]);

        if items_total > items_per_page {
            let mut scrollbar_state = ScrollbarState::new(items_total)
                .position(scroll_offset);
            let scrollbar = Scrollbar::new(ScrollbarOrientation::VerticalRight)
                .begin_symbol(Some("↑"))
                .end_symbol(Some("↓"));
            f.render_stateful_widget(
                scrollbar,
                chunks[1].inner(Margin { vertical: 1, horizontal: 0 }),
                &mut scrollbar_state,
            );
        }
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

    let help = Paragraph::new(Line::from(vec![
        Span::styled(" Enter ", theme::highlight()),
        Span::styled("Activate  ", theme::normal()),
        Span::styled("n ", theme::highlight()),
        Span::styled("New  ", theme::normal()),
        Span::styled("e ", theme::highlight()),
        Span::styled("Edit  ", theme::normal()),
        Span::styled("d ", theme::highlight()),
        Span::styled("Defaults  ", theme::normal()),
        Span::styled("p ", theme::highlight()),
        Span::styled("Password  ", theme::normal()),
        Span::styled("↑/↓ ", theme::highlight()),
        Span::styled("Navigate  ", theme::normal()),
        Span::styled("Esc ", theme::muted()),
        Span::styled("Back", theme::muted()),
    ]));
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
                    let switched_profile = id.clone();
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
                            app.vault_password = None;
                            if vault::has_vault_key(&switched_profile) {
                                app.pending_vault_setup = true;
                            }
                            // Reset health state and trigger fresh checks
                            app.health_results.clear();
                            app.health_groups.clear();
                            app.action_menu_selected = 0;
                            app.screen = Screen::Welcome;
                            app.menu_selected = 0;
                            crate::screens::welcome::trigger_health_checks(app);
                        }
                    }
                }
            }
        }
        KeyCode::Char('e') => {
            if let Some(profiles) = &app.profiles {
                let profile_ids: Vec<&String> = profiles.profiles.keys().collect();
                if let Some(id) = profile_ids.get(app.profile_selected) {
                    app.profile_edit_id = Some((*id).clone());
                    app.profile_edit_field = 0;
                    app.profile_editing = false;
                    app.profile_edit_tier_selecting = false;
                    app.screen = Screen::ProfileEdit;
                }
            }
        }
        KeyCode::Char('n') => {
            app.screen = Screen::SetupMode;
            app.menu_selected = 0;
        }
        KeyCode::Char('d') => {
            // Edit default settings
            app.profile_edit_id = Some("__defaults__".to_string());
            app.profile_edit_field = 0;
            app.profile_editing = false;
            app.profile_edit_tier_selecting = false;
            app.screen = Screen::ProfileEdit;
        }
        KeyCode::Char('p') => {
            // Change master password for the selected profile
            if let Some(profiles) = &app.profiles {
                let profile_ids: Vec<&String> = profiles.profiles.keys().collect();
                if let Some(id) = profile_ids.get(app.profile_selected) {
                    let id_str = (*id).clone();
                    if vault::has_vault_key(&id_str) {
                        app.pending_password_change = true;
                        app.pending_password_change_profile = Some(id_str);
                    } else {
                        app.set_message(
                            "No vault key for this profile",
                            MessageKind::Info,
                        );
                    }
                }
            }
        }
        _ => {}
    }
}
