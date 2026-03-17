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
            .map(|(i, (id, profile_entry))| {
                let is_active = id == &profiles.active;
                let is_locked_by_other = !is_active
                    || app.profile_lock.is_none();
                let locked = if is_locked_by_other {
                    profile::is_profile_locked(&app.repo_root, id)
                } else {
                    false
                };

                let marker = if is_active && !locked {
                    "▸"
                } else if locked {
                    "🔒"
                } else {
                    " "
                };

                let style = if i == app.profile_selected {
                    theme::selected()
                } else if is_active {
                    theme::highlight()
                } else {
                    theme::normal()
                };

                let remote_info = if profile_entry.remote {
                    format!(
                        " → {}",
                        profile_entry.effective_host().unwrap_or("unknown")
                    )
                } else {
                    " (local)".into()
                };

                let hw_info = profile_entry
                    .hardware
                    .as_ref()
                    .map(|h| format!(" [{} {} {}GB]", h.os, h.arch, h.ram_gb))
                    .unwrap_or_default();

                let lock_info = if locked { " (in use)" } else { "" };

                ListItem::new(vec![
                    Line::from(Span::styled(
                        format!("{marker} {id} — {}{lock_info}", profile_entry.label),
                        style,
                    )),
                    Line::from(Span::styled(
                        format!(
                            "    {} / {}{}{}",
                            profile_entry.environment, profile_entry.backend, remote_info, hw_info
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

    let help = if app.profile_delete_confirming {
        Paragraph::new(Line::from(vec![
            Span::styled(" ⚠ Delete this profile? ", theme::warning()),
            Span::styled("y ", theme::highlight()),
            Span::styled("Yes  ", theme::normal()),
            Span::styled("n/Esc ", theme::muted()),
            Span::styled("Cancel", theme::muted()),
        ]))
    } else {
        Paragraph::new(Line::from(vec![
            Span::styled(" Enter ", theme::highlight()),
            Span::styled("Activate  ", theme::normal()),
            Span::styled("n ", theme::highlight()),
            Span::styled("New  ", theme::normal()),
            Span::styled("e ", theme::highlight()),
            Span::styled("Edit  ", theme::normal()),
            Span::styled("x ", theme::highlight()),
            Span::styled("Delete  ", theme::normal()),
            Span::styled("d ", theme::highlight()),
            Span::styled("Defaults  ", theme::normal()),
            Span::styled("p ", theme::highlight()),
            Span::styled("Password  ", theme::normal()),
            Span::styled("↑/↓ ", theme::highlight()),
            Span::styled("Navigate  ", theme::normal()),
            Span::styled("Esc ", theme::muted()),
            Span::styled("Back", theme::muted()),
        ]))
    };
    f.render_widget(help, chunks[2]);
}

pub fn handle_key(app: &mut App, key: KeyEvent) {
    // Delete confirmation mode
    if app.profile_delete_confirming {
        match key.code {
            KeyCode::Char('y') | KeyCode::Char('Y') => {
                app.profile_delete_confirming = false;
                if let Some(profiles) = &app.profiles {
                    let profile_ids: Vec<&String> = profiles.profiles.keys().collect();
                    if let Some(id) = profile_ids.get(app.profile_selected) {
                        let id = (*id).clone();
                        let is_active = id == profiles.active;
                        match profile::delete_profile(&app.repo_root, &id) {
                            Ok(()) => {
                                app.profiles = profile::load_profiles(&app.repo_root).ok();
                                let new_count = app
                                    .profiles
                                    .as_ref()
                                    .map(|p| p.profiles.len())
                                    .unwrap_or(0);
                                if app.profile_selected >= new_count && new_count > 0 {
                                    app.profile_selected = new_count - 1;
                                }
                                let msg = if is_active {
                                    format!("Deleted profile: {id} (switched active)")
                                } else {
                                    format!("Deleted profile: {id}")
                                };
                                app.set_message(&msg, MessageKind::Success);
                                if is_active {
                                    app.profile_lock = None;
                                    app.kill_ssh_tunnel();
                                    app.health_results.clear();
                                    app.health_groups.clear();
                                    app.vault_password = None;
                                }
                            }
                            Err(e) => {
                                app.set_message(
                                    &format!("Failed to delete: {e}"),
                                    MessageKind::Error,
                                );
                            }
                        }
                    }
                }
            }
            _ => {
                app.profile_delete_confirming = false;
            }
        }
        return;
    }

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
                    let is_already_active = id == profiles.active;

                    if is_already_active {
                        // Already the active profile — just go back to welcome
                        app.screen = Screen::Welcome;
                        app.menu_selected = 0;
                    } else {
                        let switched_profile = id.clone();

                        match profile::try_lock_profile(&app.repo_root, &id) {
                            Ok(Some(new_lock)) => {
                                app.profile_lock = None;
                                app.profile_lock = Some(new_lock);

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
                                        app.kill_ssh_tunnel();
                                        if vault::has_vault_key(&switched_profile) {
                                            app.pending_vault_setup = true;
                                        }
                                        app.health_results.clear();
                                        app.health_groups.clear();
                                        app.action_menu_selected = 0;
                                        app.models_manage_loaded = false;
                                        app.deployed_models = None;
                                        app.screen = Screen::Welcome;
                                        app.menu_selected = 0;
                                        crate::screens::welcome::load_active_tier_models(app);
                                        crate::screens::welcome::trigger_health_checks(app);
                                    }
                                }
                            }
                            Ok(None) => {
                                app.set_message(
                                    &format!("Profile '{id}' is in use by another instance"),
                                    MessageKind::Warning,
                                );
                            }
                            Err(e) => {
                                app.set_message(
                                    &format!("Failed to lock profile: {e}"),
                                    MessageKind::Error,
                                );
                            }
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
        KeyCode::Char('x') => {
            if profile_count > 0 {
                app.profile_delete_confirming = true;
            }
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
