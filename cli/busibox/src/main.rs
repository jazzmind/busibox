mod app;
mod modules;
mod screens;
mod theme;
mod tui;

use app::{App, Screen};
use crate::modules::remote;
use clap::Parser;
use color_eyre::Result;
use crossterm::event::{self, Event, KeyEventKind};
use std::path::PathBuf;
use std::time::Duration;

#[derive(Parser)]
#[command(name = "busibox", about = "Busibox Infrastructure CLI")]
struct Cli {
    /// Path to the busibox repository root
    #[arg(short, long)]
    root: Option<PathBuf>,
}

fn main() -> Result<()> {
    color_eyre::install()?;
    let cli = Cli::parse();

    let repo_root = cli
        .root
        .or_else(|| modules::profile::find_repo_root().ok())
        .unwrap_or_else(|| std::env::current_dir().unwrap_or_else(|_| PathBuf::from(".")));

    let mut app = App::new(repo_root.clone());

    // Load profiles
    match modules::profile::load_profiles(&repo_root) {
        Ok(profiles) => app.profiles = Some(profiles),
        Err(_) => {}
    }

    // Detect local hardware (non-blocking quick scan)
    match modules::hardware::HardwareProfile::detect_local() {
        Ok(hw) => app.local_hardware = Some(hw),
        Err(_) => {}
    }

    let mut terminal = tui::init()?;

    while !app.should_quit {
        terminal.draw(|f| render(&app, f))?;

        // Tick spinner animation on install screen
        if app.screen == Screen::Install {
            app.install_tick = app.install_tick.wrapping_add(1);
        }

        // Drain install updates from background worker
        if let Some(rx) = app.install_rx.take() {
            use std::sync::mpsc::TryRecvError;
            let mut put_back = true;
            loop {
                match rx.try_recv() {
                    Ok(app::InstallUpdate::Log(msg)) => {
                        app.install_log.push(msg);
                    }
                    Ok(app::InstallUpdate::ServiceStatus { name, status }) => {
                        for svc in app.install_services.iter_mut() {
                            if svc.name == name {
                                svc.status = status.clone();
                            }
                        }
                    }
                    Ok(app::InstallUpdate::Complete { portal_url }) => {
                        app.install_complete = true;
                        if let Some(url) = portal_url {
                            app.install_portal_url = Some(url.clone());
                            app.install_log.push(format!("Portal setup: {url}"));
                            screens::install::open_browser(&url);
                            app.pending_login = true;
                        }
                        put_back = false;
                        break;
                    }
                    Err(TryRecvError::Empty) => break,
                    Err(TryRecvError::Disconnected) => {
                        put_back = false;
                        break;
                    }
                }
            }
            if put_back {
                app.install_rx = Some(rx);
            }
        }

        // Handle deferred resume install (so status message renders first)
        if app.pending_resume_install {
            app.pending_resume_install = false;
            perform_resume_install(&mut app);
            trigger_side_effects(&mut app);
        }

        if event::poll(Duration::from_millis(100))? {
            if let Event::Key(key) = event::read()? {
                if key.kind == KeyEventKind::Press {
                    handle_key(&mut app, key);
                }
            }
        }

        // Handle post-install login (make login) with TUI suspended
        if app.pending_login {
            app.pending_login = false;
            tui::suspend()?;
            eprintln!("\n--- Generate admin login credentials ---\n");

            let is_remote = app.setup_target == app::SetupTarget::Remote;
            if is_remote {
                if let Some(ssh) = &app.ssh_connection {
                    let profile = app.active_profile().map(|(_, p)| p.clone());
                    let remote_path = profile
                        .as_ref()
                        .map(|p| p.effective_remote_path())
                        .unwrap_or_else(|| app.remote_path_input.as_str());
                    let cmd = format!("cd {} && USE_MANAGER=0 make login", remote_path);
                    let _ = ssh.run_tty(&cmd);
                }
            } else {
                let _ = remote::run_local_make(&app.repo_root, "login");
            }

            eprintln!("\n--- Press Enter to return to Busibox TUI... ---");
            let _ = std::io::stdin().read_line(&mut String::new());
            terminal = tui::resume()?;
        }

        // Handle interactive commands (like logs) that need TUI suspended
        if let Some(cmd) = app.pending_interactive_cmd.take() {
            tui::suspend()?;
            eprintln!("\n--- Press Ctrl+C to stop viewing logs and return to Busibox ---\n");

            if cmd.starts_with("REMOTE:") {
                // Parse remote command: "REMOTE:host:key:command"
                let parts: Vec<&str> = cmd.splitn(4, ':').collect();
                if parts.len() == 4 {
                    let host = parts[1];
                    let key = parts[2];
                    let remote_cmd = parts[3];
                    let ssh = modules::ssh::SshConnection::new(host, "root", key);
                    let _ = ssh.run_tty(remote_cmd);
                }
            } else {
                let _ = remote::run_local_make_interactive(&app.repo_root, &cmd);
            }

            eprintln!("\n--- Returning to Busibox TUI... ---\n");
            terminal = tui::resume()?;
        }

        // Handle SSH key copy (needs TUI suspended for password prompt)
        if let Some((key_path, host, user)) = app.pending_ssh_copy.take() {
            tui::suspend()?;
            eprintln!("\n--- Copying SSH key to {}@{} ---", user, host);
            eprintln!("--- You will be prompted for the remote password ---\n");

            let path = std::path::PathBuf::from(&key_path);
            match modules::ssh::copy_key_interactive(&path, &host, &user) {
                Ok(true) => {
                    eprintln!("\n--- Key copied successfully, testing connection... ---\n");
                    let conn = modules::ssh::SshConnection::new(&host, &user, &key_path);
                    if conn.test_connection() {
                        app.ssh_connection = Some(conn);
                        app.ssh_status = app::SshSetupStatus::Connected;
                        app.screen = app::Screen::HardwareReport;
                    } else {
                        app.ssh_status = app::SshSetupStatus::Failed(
                            "Key copied but connection test failed".into(),
                        );
                    }
                }
                Ok(false) => {
                    eprintln!("\n--- ssh-copy-id failed ---\n");
                    app.ssh_status = app::SshSetupStatus::Failed("ssh-copy-id failed".into());
                }
                Err(e) => {
                    eprintln!("\n--- Error: {} ---\n", e);
                    app.ssh_status = app::SshSetupStatus::Failed(format!("Error: {e}"));
                }
            }

            std::thread::sleep(std::time::Duration::from_secs(1));
            eprintln!("--- Returning to Busibox TUI... ---\n");
            terminal = tui::resume()?;
        }

        trigger_side_effects(&mut app);
    }

    tui::restore()?;
    Ok(())
}

fn render(app: &App, f: &mut ratatui::Frame) {
    // Clear with background color
    let area = f.area();
    let block = ratatui::widgets::Block::default().style(
        ratatui::style::Style::default().bg(theme::BRAND_BG),
    );
    f.render_widget(block, area);

    match &app.screen {
        Screen::Welcome => screens::welcome::render(f, app),
        Screen::SetupMode => screens::setup_mode::render(f, app),
        Screen::SshSetup => screens::ssh_setup::render(f, app),
        Screen::TailscaleSetup => screens::tailscale_setup::render(f, app),
        Screen::HardwareReport => screens::hardware_report::render(f, app),
        Screen::ModelConfig => screens::model_config::render(f, app),
        Screen::ModelDownload => screens::model_download::render(f, app),
        Screen::Install => screens::install::render(f, app),
        Screen::Manage => screens::manage::render(f, app),
        Screen::ProfileSelect => screens::profile_select::render(f, app),
    }
}

fn handle_key(app: &mut App, key: crossterm::event::KeyEvent) {
    use crossterm::event::KeyCode;

    // Global quit: 'q' quits from any screen (unless user is typing or viewing install logs)
    if key.code == KeyCode::Char('q')
        && app.input_mode != app::InputMode::Editing
        && !app.install_log_visible
    {
        app.should_quit = true;
        return;
    }

    app.clear_message();

    match &app.screen {
        Screen::Welcome => screens::welcome::handle_key(app, key),
        Screen::SetupMode => screens::setup_mode::handle_key(app, key),
        Screen::SshSetup => screens::ssh_setup::handle_key(app, key),
        Screen::TailscaleSetup => screens::tailscale_setup::handle_key(app, key),
        Screen::HardwareReport => screens::hardware_report::handle_key(app, key),
        Screen::ModelConfig => screens::model_config::handle_key(app, key),
        Screen::ModelDownload => screens::model_download::handle_key(app, key),
        Screen::Install => screens::install::handle_key(app, key),
        Screen::Manage => screens::manage::handle_key(app, key),
        Screen::ProfileSelect => screens::profile_select::handle_key(app, key),
    }
}

fn trigger_side_effects(app: &mut App) {
    match &app.screen {
        Screen::SshSetup => {
            if app.ssh_status == app::SshSetupStatus::NotStarted {
                screens::ssh_setup::auto_start(app);
            }
        }
        Screen::TailscaleSetup => {
            if app.tailscale_local.is_none() {
                screens::tailscale_setup::init_tailscale_check(app);
            }
        }
        Screen::HardwareReport => {
            screens::hardware_report::detect_hardware(app);
        }
        Screen::ModelConfig => {
            screens::model_config::load_recommendations(app);
        }
        Screen::Install => {
            if app.install_services.is_empty() {
                screens::install::auto_start(app);
            }
        }
        Screen::Manage => {
            if app.manage_services.is_empty() {
                screens::manage::load_service_status(app);
            }
        }
        _ => {}
    }
}

fn perform_resume_install(app: &mut App) {
    use crate::app::SetupTarget;

    if let Some((_, profile)) = app.active_profile() {
        let profile = profile.clone();
        if profile.remote {
            app.setup_target = SetupTarget::Remote;
            if let Some(host) = &profile.remote_host {
                app.remote_host_input = host.clone();
            }
            if let Some(user) = &profile.remote_user {
                app.remote_user_input = user.clone();
            }
            if let Some(path) = &profile.remote_busibox_path {
                app.remote_path_input = path.clone();
            }
            // Restore SSH connection if we have the details
            if let (Some(host), Some(key)) = (&profile.remote_host, &profile.remote_ssh_key) {
                let user = profile.remote_user.as_deref().unwrap_or("root");
                let conn = crate::modules::ssh::SshConnection::new(host, user, key);
                if conn.test_connection() {
                    app.ssh_connection = Some(conn);
                } else {
                    app.set_message(
                        "SSH connection failed — try Setup New",
                        crate::app::MessageKind::Error,
                    );
                    return;
                }
            }
        } else {
            app.setup_target = SetupTarget::Local;
        }
        // Restore hardware from profile
        if profile.remote {
            app.remote_hardware = profile.hardware.clone();
        } else {
            app.local_hardware = profile.hardware.clone();
        }
        // Set backend choice
        let backend_idx = app
            .backend_choices()
            .iter()
            .position(|b| b.to_lowercase() == profile.backend.to_lowercase())
            .unwrap_or(0);
        app.remote_backend_choice = backend_idx;
        let env_idx = app
            .env_choices()
            .iter()
            .position(|e| *e == profile.environment)
            .unwrap_or(0);
        app.remote_env_choice = env_idx;
    }
    // Clear any previous install state and go to Install screen
    app.install_services.clear();
    app.install_log.clear();
    app.install_complete = false;
    app.install_portal_url = None;
    app.clear_message();
    app.screen = Screen::Install;
    app.menu_selected = 0;
}
