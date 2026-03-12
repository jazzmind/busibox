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
use std::sync::atomic::{AtomicBool, Ordering};
use std::time::Duration;

static QUIT_SIGNAL: AtomicBool = AtomicBool::new(false);

#[derive(Parser)]
#[command(name = "busibox", about = "Busibox Infrastructure CLI")]
struct Cli {
    /// Path to the busibox repository root
    #[arg(short, long)]
    root: Option<PathBuf>,
}

fn main() -> Result<()> {
    color_eyre::install()?;

    // Ctrl+C force-quit works even when event loop is busy
    ctrlc::set_handler(|| {
        QUIT_SIGNAL.store(true, Ordering::SeqCst);
    })
    .expect("Error setting Ctrl-C handler");

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

    // Restore remote hardware from saved profile so tier/cache logic works
    if app.has_profiles() {
        if let Some((_, profile)) = app.active_profile() {
            if profile.remote {
                app.remote_hardware = profile.hardware.clone();
            }
        }
    }

    // Check model cache status for the active profile
    if app.has_profiles() {
        screens::welcome::check_model_cache(&mut app);
        screens::welcome::load_active_tier_models(&mut app);
        screens::welcome::trigger_health_checks(&mut app);
    }

    // If the active profile has an encrypted vault key, prompt for unlock on startup
    if let Some((id, _)) = app.active_profile() {
        if modules::vault::has_vault_key(&id) {
            app.pending_vault_setup = true;
        }
    }

    let mut terminal = tui::init()?;

    while !app.should_quit {
        if QUIT_SIGNAL.load(Ordering::SeqCst) {
            app.should_quit = true;
            break;
        }
        terminal.draw(|f| render(&app, f))?;

        // Tick spinner animation on install, manage, and welcome screens
        if app.screen == Screen::Install {
            app.install_tick = app.install_tick.wrapping_add(1);
        }
        if app.screen == Screen::Manage && app.manage_action_running {
            app.manage_tick = app.manage_tick.wrapping_add(1);
        }
        if app.screen == Screen::ModelsManage && app.models_manage_action_running {
            app.models_manage_tick = app.models_manage_tick.wrapping_add(1);
        }
        if app.screen == Screen::ModelBenchmark && app.benchmark_running {
            app.benchmark_tick = app.benchmark_tick.wrapping_add(1);
        }
        if app.screen == Screen::Welcome && app.health_check_running {
            app.health_tick = app.health_tick.wrapping_add(1);
        }

        // Drain health check updates
        screens::welcome::process_health_updates(&mut app);

        // Drain deployed model status updates
        screens::welcome::process_deployed_model_updates(&mut app);

        // Drain install updates from background worker
        if let Some(rx) = app.install_rx.take() {
            use std::sync::mpsc::TryRecvError;
            let mut put_back = true;
            loop {
                match rx.try_recv() {
                    Ok(app::InstallUpdate::Log(msg)) => {
                        if screens::install::process_install_log(&msg, &mut app) {
                            continue; // Internal signal, don't add to log
                        }
                        let was_at_bottom =
                            app.install_log_scroll >= app.install_log.len().saturating_sub(1);
                        app.install_log.push(msg);
                        if was_at_bottom || !app.install_log_visible {
                            app.install_log_scroll = app.install_log.len().saturating_sub(1);
                        }
                    }
                    Ok(app::InstallUpdate::ServiceStatus { name, status }) => {
                        for svc in app.install_services.iter_mut() {
                            if svc.name == name {
                                svc.status = status.clone();
                            }
                        }
                    }
                    Ok(app::InstallUpdate::WaitForRetry { hint, response }) => {
                        app.install_prereq_hint = hint;
                        app.install_waiting_retry = Some(response);
                    }
                    Ok(app::InstallUpdate::NeedGitHubToken { message, response }) => {
                        app.install_token_message = message;
                        app.install_token_input.clear();
                        app.install_token_error.clear();
                        app.install_waiting_token = Some(response);
                    }
                    Ok(app::InstallUpdate::Complete { portal_url }) => {
                        app.install_complete = true;
                        if let Some(url) = portal_url {
                            app.install_portal_url = Some(url.clone());
                            app.install_log.push(format!("Portal setup: {url}"));
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

        // Drain manage action updates from background worker
        {
            let mut manage_completed = false;
            let mut manage_success = false;
            if let Some(rx) = app.manage_rx.take() {
                use std::sync::mpsc::TryRecvError;
                let mut put_back = true;
                loop {
                    match rx.try_recv() {
                        Ok(app::ManageUpdate::Log(msg)) => {
                            app.manage_log.push(msg);
                            const MAX_LOG_LINES: usize = 5000;
                            if app.manage_log.len() > MAX_LOG_LINES {
                                let excess = app.manage_log.len() - MAX_LOG_LINES;
                                app.manage_log.drain(..excess);
                                app.manage_log_scroll =
                                    app.manage_log_scroll.saturating_sub(excess);
                            }
                            if app.manage_log_autoscroll {
                                app.manage_log_scroll =
                                    app.manage_log.len().saturating_sub(1);
                            }
                        }
                        Ok(app::ManageUpdate::StatusResult { name, status }) => {
                            if let Some(svc) =
                                app.manage_services.iter_mut().find(|s| s.name == name)
                            {
                                svc.status = status;
                            }
                        }
                        Ok(app::ManageUpdate::WaitForConfirm { prompt, response }) => {
                            app.manage_confirm_prompt = prompt;
                            app.manage_waiting_confirm = Some(response);
                            break;
                        }
                        Ok(app::ManageUpdate::Complete { success }) => {
                            app.manage_action_running = false;
                            app.manage_log_streaming = false;
                            app.manage_log_child_pid = None;
                            if app.manage_log_visible {
                                app.manage_action_complete = true;
                                app.manage_log_scroll =
                                    app.manage_log.len().saturating_sub(1);
                                manage_completed = true;
                                manage_success = success;
                            }
                            put_back = false;
                            break;
                        }
                        Err(TryRecvError::Empty) => break,
                        Err(TryRecvError::Disconnected) => {
                            app.manage_action_running = false;
                            put_back = false;
                            break;
                        }
                    }
                }
                if put_back {
                    app.manage_rx = Some(rx);
                }
            }
            if manage_completed {
                if manage_success {
                    app.set_message("Action completed", app::MessageKind::Success);
                } else {
                    app.set_message(
                        "Action failed — press l to view logs",
                        app::MessageKind::Error,
                    );
                }
            }
        }

        // Drain models manage updates
        {
            if let Some(rx) = app.models_manage_rx.take() {
                use std::sync::mpsc::TryRecvError;
                let mut put_back = true;
                loop {
                    match rx.try_recv() {
                        Ok(app::ModelsManageUpdate::Log(msg)) => {
                            let was_at_bottom = app.models_manage_log_scroll
                                >= app.models_manage_log.len().saturating_sub(1);
                            app.models_manage_log.push(msg);
                            if was_at_bottom {
                                app.models_manage_log_scroll =
                                    app.models_manage_log.len().saturating_sub(1);
                            }
                        }
                        Ok(app::ModelsManageUpdate::Complete { success, deployed }) => {
                            app.models_manage_action_running = false;
                            app.models_manage_action_complete = true;
                            app.models_manage_log_scroll =
                                app.models_manage_log.len().saturating_sub(1);
                            if success {
                                app.models_manage_config_dirty = false;
                                app.models_manage_config_undeployed = !deployed;
                                if deployed {
                                    app.models_manage_is_custom = true;
                                    app.models_manage_current_tier = Some("custom".to_string());
                                    app.models_manage_tier_selected =
                                        screens::models_manage::CUSTOM_TIER_INDEX;
                                    if let Ok(profiles) =
                                        modules::profile::load_profiles(&app.repo_root)
                                    {
                                        app.profiles = Some(profiles);
                                    }
                                    app.models_manage_loaded = false;
                                    screens::welcome::load_active_tier_models(&mut app);
                                }
                            }
                            put_back = false;
                            break;
                        }
                        Err(TryRecvError::Empty) => break,
                        Err(TryRecvError::Disconnected) => {
                            app.models_manage_action_running = false;
                            put_back = false;
                            break;
                        }
                    }
                }
                if put_back {
                    app.models_manage_rx = Some(rx);
                }
            }
        }

        // Drain benchmark updates
        {
            if let Some(rx) = app.benchmark_rx.take() {
                use std::sync::mpsc::TryRecvError;
                let mut put_back = true;
                loop {
                    match rx.try_recv() {
                        Ok(app::BenchmarkUpdate::Log(msg)) => {
                            let was_at_bottom = app.benchmark_log_scroll
                                >= app.benchmark_log.len().saturating_sub(1);
                            app.benchmark_log.push(msg);
                            if was_at_bottom {
                                app.benchmark_log_scroll =
                                    app.benchmark_log.len().saturating_sub(1);
                            }
                        }
                        Ok(app::BenchmarkUpdate::Result(result)) => {
                            app.benchmark_results.push(result);
                        }
                        Ok(app::BenchmarkUpdate::ModelTestResult(result)) => {
                            app.benchmark_model_test_results.push(result);
                        }
                        Ok(app::BenchmarkUpdate::Complete) => {
                            app.benchmark_running = false;
                            app.benchmark_complete = true;
                            app.benchmark_log_scroll =
                                app.benchmark_log.len().saturating_sub(1);
                            put_back = false;
                            break;
                        }
                        Err(TryRecvError::Empty) => break,
                        Err(TryRecvError::Disconnected) => {
                            app.benchmark_running = false;
                            put_back = false;
                            break;
                        }
                    }
                }
                if put_back {
                    app.benchmark_rx = Some(rx);
                }
            }
        }

        // Handle deferred resume install (so status message renders first)
        if app.pending_resume_install {
            app.pending_resume_install = false;
            perform_resume_install(&mut app);
            trigger_side_effects(&mut app);
        }

        // Handle deferred "Continue Install (Web)" sync + admin login flow.
        if app.pending_sync_admin_login {
            app.pending_sync_admin_login = false;
            perform_sync_then_admin_login(&mut app);
        }

        // Handle standalone code sync to remote host.
        if app.pending_code_sync {
            app.pending_code_sync = false;
            perform_code_sync(&mut app);
        }

        if event::poll(Duration::from_millis(100))? {
            if let Event::Key(key) = event::read()? {
                if key.kind == KeyEventKind::Press {
                    handle_key(&mut app, key);
                }
            }
        }

        // Handle vault setup (needs TUI suspended for password prompts)
        if app.pending_vault_setup {
            app.pending_vault_setup = false;
            tui::suspend()?;
            handle_vault_setup(&mut app);
            terminal = tui::resume()?;

            // If vault setup succeeded (password is set), start the install worker
            if app.vault_password.is_some() && app.screen == Screen::Install {
                screens::install::spawn_install_worker_pub(&mut app);
            }
        }

        // Handle profile export (needs TUI suspended for password prompts)
        if app.pending_profile_export {
            app.pending_profile_export = false;
            tui::suspend()?;
            handle_profile_export(&app);
            eprintln!("\nPress Enter to continue...");
            let _ = std::io::stdin().read_line(&mut String::new());
            terminal = tui::resume()?;
        }

        // Handle master password change (needs TUI suspended)
        if app.pending_password_change {
            app.pending_password_change = false;
            let profile_id = app.pending_password_change_profile.take();
            tui::suspend()?;
            handle_password_change(&app, profile_id.as_deref());
            eprintln!("\nPress Enter to continue...");
            let _ = std::io::stdin().read_line(&mut String::new());
            terminal = tui::resume()?;
        }

        // Handle binary deployment to remote host (needs TUI suspended)
        if app.pending_deploy_binary {
            app.pending_deploy_binary = false;
            tui::suspend()?;
            handle_deploy_binary(&app);
            eprintln!("\nPress Enter to continue...");
            let _ = std::io::stdin().read_line(&mut String::new());
            terminal = tui::resume()?;
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

        // Handle admin login credential generation (runs make login --json)
        if app.pending_admin_login {
            app.pending_admin_login = false;
            handle_admin_login(&mut app);
        }

        // Handle interactive commands (like logs) that need TUI suspended
        if let Some(cmd) = app.pending_interactive_cmd.take() {
            tui::suspend()?;
            eprintln!("\n--- Press Ctrl+C to stop viewing logs and return to Busibox ---\n");

            if cmd.starts_with("REMOTE:") {
                // Parse remote command: "REMOTE:host:user:key:command"
                let parts: Vec<&str> = cmd.splitn(5, ':').collect();
                if parts.len() >= 5 {
                    let host = parts[1];
                    let user = parts[2];
                    let key = parts[3];
                    let remote_cmd = parts[4];
                    let ssh = modules::ssh::SshConnection::new(host, user, key);
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

    app.kill_ssh_tunnel();
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
        Screen::ModelsManage => screens::models_manage::render(f, app),
        Screen::ModelBenchmark => screens::model_benchmark::render(f, app),
        Screen::ProfileSelect => screens::profile_select::render(f, app),
        Screen::ProfileEdit => screens::profile_edit::render(f, app),
        Screen::AdminLogin => screens::admin_login::render(f, app),
    }
}

fn handle_key(app: &mut App, key: crossterm::event::KeyEvent) {
    use crossterm::event::{KeyCode, KeyModifiers};

    // Force quit on Ctrl+C from any screen, any state
    if key.code == KeyCode::Char('c') && key.modifiers.contains(KeyModifiers::CONTROL) {
        app.should_quit = true;
        return;
    }

    // 'q' during models manage log viewer: close it
    if key.code == KeyCode::Char('q') && app.models_manage_log_visible {
        if !app.models_manage_action_running {
            app.models_manage_log_visible = false;
        }
        return;
    }

    // 'q' during log viewer: close log viewer and return to manage screen
    if key.code == KeyCode::Char('q') && app.manage_log_visible {
        app.manage_log_visible = false;
        if !app.manage_action_running {
            screens::manage::load_service_status(app);
        }
        return;
    }

    // Global quit: 'q' quits from any screen (unless user is typing or viewing install logs)
    if key.code == KeyCode::Char('q')
        && app.input_mode != app::InputMode::Editing
        && !app.install_log_visible
        && !app.models_manage_log_visible
        && !app.profile_editing
        && !app.profile_edit_tier_selecting
        && app.screen != Screen::AdminLogin
        && app.screen != Screen::ModelBenchmark
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
        Screen::ModelsManage => screens::models_manage::handle_key(app, key),
        Screen::ModelBenchmark => screens::model_benchmark::handle_key(app, key),
        Screen::ProfileSelect => screens::profile_select::handle_key(app, key),
        Screen::ProfileEdit => screens::profile_edit::handle_key(app, key),
        Screen::AdminLogin => screens::admin_login::handle_key(app, key),
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
        Screen::ModelsManage => {
            screens::models_manage::init_screen(app);
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
    app.install_model_status.clear();
    app.install_models_complete = false;
    app.install_portal_url = None;
    app.clear_message();
    app.screen = Screen::Install;
    app.menu_selected = 0;
}

fn perform_sync_then_admin_login(app: &mut App) {
    use crate::app::{MessageKind, SetupTarget};

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

            // Ensure SSH connection is available and valid.
            if app.ssh_connection.is_none() {
                if let (Some(host), Some(key)) = (&profile.remote_host, &profile.remote_ssh_key) {
                    let user = profile.remote_user.as_deref().unwrap_or("root");
                    let conn = crate::modules::ssh::SshConnection::new(host, user, key);
                    if conn.test_connection() {
                        app.ssh_connection = Some(conn);
                    } else {
                        app.set_message(
                            "SSH connection failed — cannot sync remote code",
                            MessageKind::Error,
                        );
                        return;
                    }
                } else {
                    app.set_message(
                        "No SSH credentials configured in profile",
                        MessageKind::Error,
                    );
                    return;
                }
            }

            let host = profile.effective_host().unwrap_or("localhost");
            let user = profile.effective_user();
            let key = profile.effective_ssh_key();
            let remote_path = profile.effective_remote_path();

            match remote::sync(&app.repo_root, host, user, key, remote_path) {
                Ok(()) => {
                    app.set_message("✓ Files synced", MessageKind::Success);
                }
                Err(e) => {
                    app.set_message(
                        &format!("Remote sync failed: {e}"),
                        MessageKind::Error,
                    );
                    return;
                }
            }
        } else {
            app.setup_target = SetupTarget::Local;
        }
    }

    app.admin_login_loading = true;
    app.admin_login_magic_link = None;
    app.admin_login_totp_code = None;
    app.admin_login_verify_url = None;
    app.admin_login_error = None;
    app.pending_admin_login = true;
    app.screen = Screen::AdminLogin;
}

/// Sync local busibox repo to the remote host (standalone, no follow-up action).
fn perform_code_sync(app: &mut App) {
    use crate::app::MessageKind;

    let profile = match app.active_profile() {
        Some((_, p)) => p.clone(),
        None => {
            app.set_message("No active profile", MessageKind::Error);
            return;
        }
    };

    if !profile.remote {
        app.set_message("Sync is only for remote profiles", MessageKind::Info);
        return;
    }

    let host = match profile.effective_host() {
        Some(h) => h,
        None => {
            app.set_message("No remote host configured", MessageKind::Error);
            return;
        }
    };
    let user = profile.effective_user();
    let key = profile.effective_ssh_key();
    let remote_path = profile.effective_remote_path();

    match remote::sync(&app.repo_root, host, user, key, remote_path) {
        Ok(()) => {
            app.set_message("✓ Code synced to remote host", MessageKind::Success);
        }
        Err(e) => {
            app.set_message(&format!("Sync failed: {e}"), MessageKind::Error);
        }
    }
}

/// Generate admin login credentials by running `make login --json` and parsing output.
fn handle_admin_login(app: &mut App) {
    use crate::app::SetupTarget;
    use crate::modules::remote;
    use std::process::Command;

    let mut debug_info = String::new();
    #[allow(unused_assignments)]
    let mut debug_output = String::new();

    let is_remote = app.setup_target == SetupTarget::Remote
        || app
            .active_profile()
            .map(|(_, p)| p.remote)
            .unwrap_or(false);

    // Get admin email from active profile, falling back to the transient input field
    let admin_email: Option<String> = app
        .active_profile()
        .and_then(|(_, p)| p.admin_email.clone())
        .or_else(|| {
            if app.admin_email_input.is_empty() {
                None
            } else {
                Some(app.admin_email_input.clone())
            }
        });

    let busibox_env: Option<String> = app
        .active_profile()
        .map(|(_, p)| p.environment.clone());

    let busibox_backend: Option<String> = app
        .active_profile()
        .map(|(_, p)| p.backend.clone());

    debug_info.push_str(&format!("email={:?}", admin_email));

    // Ensure SSH connection is established for remote
    if is_remote && app.ssh_connection.is_none() {
        if let Some((_, profile)) = app.active_profile() {
            if let (Some(host), Some(key)) = (&profile.remote_host, &profile.remote_ssh_key) {
                let user = profile.remote_user.as_deref().unwrap_or("root");
                let conn = crate::modules::ssh::SshConnection::new(host, user, key);
                if conn.test_connection() {
                    app.ssh_connection = Some(conn);
                } else {
                    app.admin_login_loading = false;
                    app.admin_login_error =
                        Some("SSH connection failed — cannot reach remote host".into());
                    return;
                }
            } else {
                app.admin_login_loading = false;
                app.admin_login_error =
                    Some("No SSH credentials configured in profile".into());
                return;
            }
        }
    }

    let result = if is_remote {
        if let Some(ssh) = &app.ssh_connection {
            let remote_path = app
                .active_profile()
                .map(|(_, p)| p.effective_remote_path().to_string())
                .unwrap_or_else(|| app.remote_path_input.clone());

            // Build the remote command with ADMIN_EMAIL env var
            let email_export = if let Some(ref email) = admin_email {
                let escaped = email.replace('\'', "'\\''");
                format!("export ADMIN_EMAIL='{escaped}'; ")
            } else {
                String::new()
            };

            let vault_export = if let Some(ref vault_pw) = app.vault_password {
                let escaped = vault_pw.replace('\'', "'\\''");
                format!("export ANSIBLE_VAULT_PASSWORD='{escaped}'; ")
            } else {
                String::new()
            };

            let env_export = if let Some(ref env_val) = busibox_env {
                let escaped = env_val.replace('\'', "'\\''");
                format!("export BUSIBOX_ENV='{escaped}'; ")
            } else {
                String::new()
            };

            let backend_export = if let Some(ref backend_val) = busibox_backend {
                let escaped = backend_val.replace('\'', "'\\''");
                format!("export BUSIBOX_BACKEND='{escaped}'; ")
            } else {
                String::new()
            };

            let cmd = format!(
                "{preamble}\
                 [ -f \"$HOME/.profile\" ] && . \"$HOME/.profile\" 2>/dev/null || true; \
                 [ -f \"$HOME/.bashrc\" ] && . \"$HOME/.bashrc\" 2>/dev/null || true; \
                 {vault_export}{email_export}{env_export}{backend_export}export JSON_OUTPUT=1; \
                 cd {remote_path} && bash scripts/make/login.sh 2>&1",
                preamble = remote::SHELL_PATH_PREAMBLE,
                vault_export = vault_export,
                email_export = email_export,
                env_export = env_export,
                backend_export = backend_export,
                remote_path = remote_path,
            );
            debug_info.push_str(&format!(" | env={:?} backend={:?} remote_cmd_len={}", busibox_env, busibox_backend, cmd.len()));

            let mut args: Vec<String> = vec![
                "-o".into(),
                "BatchMode=yes".into(),
                "-o".into(),
                "StrictHostKeyChecking=accept-new".into(),
                "-o".into(),
                "ConnectTimeout=10".into(),
            ];
            let key = crate::modules::ssh::shellexpand_path(&ssh.key_path);
            if !key.is_empty() && std::path::Path::new(&key).exists() {
                args.push("-i".into());
                args.push(key);
            }
            args.push(ssh.ssh_target());
            args.push(cmd);

            match Command::new("ssh").args(&args).output() {
                Ok(output) => {
                    let exit_code = output.status.code().unwrap_or(1);
                    let combined = format!(
                        "{}{}",
                        String::from_utf8_lossy(&output.stdout),
                        String::from_utf8_lossy(&output.stderr)
                    );
                    Ok((exit_code, remote::strip_ansi(&combined)))
                }
                Err(e) => Err(color_eyre::eyre::eyre!("SSH command failed: {e}")),
            }
        } else {
            Err(color_eyre::eyre::eyre!("No SSH connection"))
        }
    } else {
        // Local execution: run login.sh directly so env vars are inherited (make does not export CLI vars)
        let mut cmd = Command::new("bash");
        cmd.args(["scripts/make/login.sh"])
            .env("JSON_OUTPUT", "1")
            .current_dir(&app.repo_root);

        if let Some(ref email) = admin_email {
            cmd.env("ADMIN_EMAIL", email);
        }
        if let Some(ref vault_pw) = app.vault_password {
            cmd.env("ANSIBLE_VAULT_PASSWORD", vault_pw);
        }
        if let Some(ref env_val) = busibox_env {
            cmd.env("BUSIBOX_ENV", env_val);
        }
        if let Some(ref backend_val) = busibox_backend {
            cmd.env("BUSIBOX_BACKEND", backend_val);
        }

        debug_info.push_str(" | local_cmd=bash scripts/make/login.sh");

        match cmd.output() {
            Ok(output) => {
                let exit_code = output.status.code().unwrap_or(1);
                let combined = format!(
                    "{}{}",
                    String::from_utf8_lossy(&output.stdout),
                    String::from_utf8_lossy(&output.stderr)
                );
                Ok((exit_code, remote::strip_ansi(&combined)))
            }
            Err(e) => Err(color_eyre::eyre::eyre!("login failed: {e}")),
        }
    };

    match result {
        Ok((exit_code, output)) => {
            debug_info.push_str(&format!(" | exit={} output_len={}", exit_code, output.len()));
            debug_output = output.chars().take(500).collect::<String>();
            if let Some(creds) = screens::admin_login::parse_login_json(&output) {
                let mut magic_link = creds.magic_link;
                let mut verify_url = creds.verify_url;

                if app.admin_login_use_setup {
                    magic_link = magic_link.replace("/portal/verify", "/portal/setup");
                    verify_url = verify_url.replace("/portal/verify", "/portal/setup");
                }

                // For remote profiles, rewrite URLs to use localhost:4443 (SSH tunnel)
                if is_remote {
                    // Replace https://localhost/ or https://DOMAIN/ with https://localhost:4443/
                    let re_domain = |url: &str| -> String {
                        if let Some(path_start) = url.find("/portal/") {
                            format!("https://localhost:4443{}", &url[path_start..])
                        } else {
                            url.to_string()
                        }
                    };
                    magic_link = re_domain(&magic_link);
                    verify_url = re_domain(&verify_url);

                    // Start SSH tunnel if not already running (skip if persistent tunnel is active)
                    if app.ssh_tunnel_process.is_none() && !app.ssh_tunnel_active {
                        if let Some(ssh) = &app.ssh_connection {
                            let key = crate::modules::ssh::shellexpand_path(&ssh.key_path);
                            let mut tunnel_args: Vec<String> = vec![
                                "-N".into(),
                                "-L".into(),
                                "4443:localhost:443".into(),
                                "-o".into(),
                                "StrictHostKeyChecking=accept-new".into(),
                                "-o".into(),
                                "ExitOnForwardFailure=yes".into(),
                            ];
                            if !key.is_empty() && std::path::Path::new(&key).exists() {
                                tunnel_args.push("-i".into());
                                tunnel_args.push(key);
                            }
                            tunnel_args.push(ssh.ssh_target());

                            match Command::new("ssh").args(&tunnel_args).spawn() {
                                Ok(child) => {
                                    app.ssh_tunnel_process = Some(child);
                                }
                                Err(_e) => {
                                    // Non-fatal: tunnel failed but links are still shown
                                }
                            }
                        }
                    }
                }

                app.admin_login_magic_link = Some(magic_link);
                app.admin_login_totp_code = if creds.totp_code.is_empty() {
                    None
                } else {
                    Some(creds.totp_code)
                };
                app.admin_login_verify_url = if verify_url.is_empty() {
                    None
                } else {
                    Some(verify_url)
                };
                app.admin_login_error = None;
            } else {
                // Try to find error in output
                let error_msg = if output.contains("error") || output.contains("ERROR") {
                    output
                        .lines()
                        .find(|l| {
                            let lower = l.to_lowercase();
                            lower.contains("error") || lower.contains("failed")
                        })
                        .unwrap_or("Unknown error")
                        .trim()
                        .to_string()
                } else {
                    format!("Could not parse login output (exit code {exit_code})")
                };
                app.admin_login_error = Some(error_msg);
            }
        }
        Err(e) => {
            debug_info.push_str(&format!(" | err={}", e));
            debug_output = format!("{}", e);
            app.admin_login_error = Some(format!("Failed to run login: {e}"));
        }
    }

    if let Some(ref mut err) = app.admin_login_error {
        let truncated_output: String = debug_output.chars().take(800).collect();
        *err = format!("[DEBUG] {}\n[OUTPUT] {}", debug_info, truncated_output);
    }

    app.admin_login_loading = false;
}

/// Handle vault setup with interactive password prompts.
/// Called with TUI suspended so rpassword can read from the terminal.
fn handle_vault_setup(app: &mut App) {
    use modules::vault;

    let profile_id = match app.active_profile() {
        Some((id, _)) => id.to_string(),
        None => {
            eprintln!("No active profile — cannot set up vault.");
            eprintln!("Press Enter to continue...");
            let _ = std::io::stdin().read_line(&mut String::new());
            return;
        }
    };

    // Derive vault prefix from environment (same logic as service-deploy.sh get_container_prefix)
    let vault_prefix = crate::screens::install::env_to_prefix(
        &app.active_profile()
            .map(|(_, p)| p.environment.clone())
            .unwrap_or_else(|| "development".into()),
    );

    eprintln!("\n╔══════════════════════════════════════════════════════╗");
    eprintln!("║             Vault Password Setup                     ║");
    eprintln!("╚══════════════════════════════════════════════════════╝\n");

    // Check if we already have an encrypted vault key
    if vault::has_vault_key(&profile_id) {
        eprintln!("Encrypted vault key found for profile '{profile_id}'.");
        eprintln!("Enter your master password to unlock.\n");

        for attempt in 1..=3 {
            match vault::prompt_password("Master password: ") {
                Ok(pw) if pw.is_empty() => {
                    eprintln!("Password cannot be empty.\n");
                    continue;
                }
                Ok(pw) => {
                    let key_path = match vault::vault_key_path(&profile_id) {
                        Ok(p) => p,
                        Err(e) => {
                            eprintln!("Error: {e}");
                            break;
                        }
                    };
                    match vault::load_encrypted_vault(&key_path) {
                        Ok(enc) => match vault::decrypt_vault_password(&enc, &pw) {
                            Ok(vault_pw) => {
                                eprintln!("✓ Vault unlocked\n");
                                app.vault_password = Some(vault_pw);
                                return;
                            }
                            Err(_) => {
                                eprintln!(
                                    "Incorrect master password (attempt {attempt}/3)\n"
                                );
                            }
                        },
                        Err(e) => {
                            eprintln!("Error reading vault key: {e}");
                            break;
                        }
                    }
                }
                Err(e) => {
                    eprintln!("Error: {e}");
                    break;
                }
            }
        }
        eprintln!("Failed to unlock vault. Install will proceed without vault secrets.");
        eprintln!("Press Enter to continue...");
        let _ = std::io::stdin().read_line(&mut String::new());
        return;
    }

    // Check for legacy plaintext password file — offer migration
    if let Some(legacy_path) = vault::find_legacy_vault_pass(&vault_prefix) {
        eprintln!(
            "Found existing plaintext vault password: {}",
            legacy_path.display()
        );
        eprintln!("Migrating to encrypted vault key...\n");

        match std::fs::read_to_string(&legacy_path) {
            Ok(vault_pw) => {
                let vault_pw = vault_pw.trim().to_string();
                if vault_pw.is_empty() {
                    eprintln!("Warning: plaintext password file is empty. Skipping migration.");
                } else {
                    // Encrypt with admin master password
                    eprintln!("Set a master password to protect this vault key.");
                    eprintln!("You'll need this password each time you deploy.\n");
                    match vault::prompt_new_password("New master password: ") {
                        Ok(master_pw) => {
                            match vault::encrypt_vault_password(&vault_pw, &master_pw) {
                                Ok(enc) => {
                                    let key_path =
                                        match vault::vault_key_path(&profile_id) {
                                            Ok(p) => p,
                                            Err(e) => {
                                                eprintln!("Error: {e}");
                                                app.vault_password = Some(vault_pw);
                                                return;
                                            }
                                        };
                                    if let Err(e) = vault::save_encrypted_vault(&key_path, &enc) {
                                        eprintln!("Warning: could not save encrypted vault key: {e}");
                                    } else {
                                        eprintln!(
                                            "✓ Vault key saved: {}\n",
                                            key_path.display()
                                        );
                                    }
                                }
                                Err(e) => {
                                    eprintln!("Warning: encryption failed: {e}");
                                }
                            }

                            // Offer to set up remote user password
                            setup_remote_vault_key(app, &profile_id, &vault_pw);

                            // Remove plaintext file now that it's encrypted
                            eprint!(
                                "Delete plaintext password file {}? (Y/n) ",
                                legacy_path.display()
                            );
                            let mut answer = String::new();
                            let _ = std::io::stdin().read_line(&mut answer);
                            if !answer.trim().eq_ignore_ascii_case("n") {
                                if let Err(e) = std::fs::remove_file(&legacy_path) {
                                    eprintln!("Warning: could not delete {}: {e}", legacy_path.display());
                                } else {
                                    eprintln!("✓ Plaintext file removed\n");
                                }
                            }
                        }
                        Err(e) => {
                            eprintln!("Error: {e}");
                        }
                    }

                    app.vault_password = Some(vault_pw);
                    return;
                }
            }
            Err(e) => {
                eprintln!("Warning: could not read {}: {e}", legacy_path.display());
            }
        }
    }

    // First-time setup: generate a new vault password
    eprintln!("No vault configured for profile '{profile_id}'.");
    eprintln!("Setting up a new encrypted vault...\n");

    let vault_pw = vault::generate_vault_password();

    // Encrypt with admin master password
    eprintln!("Choose a master password for this profile.");
    eprintln!("You'll need this password each time you deploy.\n");

    match vault::prompt_new_password("Master password: ") {
        Ok(master_pw) => {
            match vault::encrypt_vault_password(&vault_pw, &master_pw) {
                Ok(enc) => {
                    let key_path = match vault::vault_key_path(&profile_id) {
                        Ok(p) => p,
                        Err(e) => {
                            eprintln!("Error: {e}");
                            app.vault_password = Some(vault_pw);
                            return;
                        }
                    };
                    if let Err(e) = vault::save_encrypted_vault(&key_path, &enc) {
                        eprintln!("Warning: could not save vault key: {e}");
                    } else {
                        eprintln!("✓ Admin vault key saved: {}\n", key_path.display());
                    }
                }
                Err(e) => {
                    eprintln!("Warning: encryption failed: {e}");
                }
            }
        }
        Err(e) => {
            eprintln!("Error: {e}");
            app.vault_password = Some(vault_pw);
            return;
        }
    }

    // Set up remote user vault key
    setup_remote_vault_key(app, &profile_id, &vault_pw);

    // Create the Ansible vault file on the target
    create_ansible_vault(app, &vault_pw, &vault_prefix);

    app.vault_password = Some(vault_pw);
    eprintln!("\n✓ Vault setup complete. Starting installation...\n");
    std::thread::sleep(std::time::Duration::from_secs(1));
}

/// Offer to set up a separate master password for the remote user.
fn setup_remote_vault_key(app: &App, profile_id: &str, vault_pw: &str) {
    use modules::vault;

    let is_remote = app.setup_target == app::SetupTarget::Remote;
    if !is_remote {
        return;
    }

    eprintln!("Set a master password for the remote user.");
    eprintln!("They'll use this to run local updates.\n");

    match vault::prompt_new_password("Remote user password: ") {
        Ok(remote_pw) => {
            match vault::encrypt_vault_password(vault_pw, &remote_pw) {
                Ok(enc) => {
                    let json = match serde_json::to_string_pretty(&enc) {
                        Ok(j) => j,
                        Err(e) => {
                            eprintln!("Warning: could not serialize vault key: {e}");
                            return;
                        }
                    };

                    // Deploy to remote via SSH
                    if let Some(ssh) = &app.ssh_connection {
                        let escaped_json = json.replace('\'', "'\\''");
                        let cmd = format!(
                            "mkdir -p ~/.busibox/vault-keys && \
                             printf '%s\\n' '{}' > ~/.busibox/vault-keys/{}.enc && \
                             chmod 600 ~/.busibox/vault-keys/{}.enc",
                            escaped_json, profile_id, profile_id
                        );
                        match ssh.run(&cmd) {
                            Ok(_) => {
                                eprintln!("✓ Remote vault key deployed\n");
                            }
                            Err(e) => {
                                eprintln!(
                                    "Warning: could not deploy remote vault key: {e}"
                                );
                            }
                        }
                    }
                }
                Err(e) => {
                    eprintln!("Warning: encryption failed: {e}");
                }
            }
        }
        Err(e) => {
            eprintln!("Warning: could not set remote password: {e}");
        }
    }
}

/// Create the Ansible vault file on the target (remote or local).
/// Copies vault.example.yml → vault.{prefix}.yml and encrypts it.
fn create_ansible_vault(app: &App, vault_pw: &str, vault_prefix: &str) {
    let is_remote = app.setup_target == app::SetupTarget::Remote;

    let vault_dir = "provision/ansible/roles/secrets/vars";
    let target_file = format!("{vault_dir}/vault.{vault_prefix}.yml");
    let example_file = format!("{vault_dir}/vault.example.yml");

    eprintln!("Creating Ansible vault: {target_file}...");

    if is_remote {
        if let Some(ssh) = &app.ssh_connection {
            let profile = app.active_profile().map(|(_, p)| p.clone());
            let remote_path = profile
                .as_ref()
                .map(|p| p.effective_remote_path().to_string())
                .unwrap_or_else(|| app.remote_path_input.clone());

            let check_cmd = format!(
                "[ -f {remote_path}/{target_file} ] && echo EXISTS || echo MISSING"
            );
            let exists = ssh
                .run(&check_cmd)
                .map(|o| o.trim() == "EXISTS")
                .unwrap_or(false);

            if exists {
                eprintln!("  Vault file already exists on remote, skipping creation.");
                return;
            }

            let create_script = format!(
                "cp {example_file} {target_file} && \
                 ansible-vault encrypt {target_file} --vault-password-file=\"$ANSIBLE_VAULT_PASSWORD_FILE\" && \
                 echo ENCRYPT_OK || echo ENCRYPT_FAIL"
            );
            match modules::remote::exec_remote_with_vault(ssh, &remote_path, &create_script, vault_pw) {
                Ok((rc, output)) => {
                    if rc == 0 && output.contains("ENCRYPT_OK") {
                        eprintln!("  ✓ Ansible vault created and encrypted on remote");
                    } else {
                        for line in output.lines() {
                            eprintln!("  {}", line);
                        }
                        eprintln!("  Warning: vault encryption may have failed");
                    }
                }
                Err(e) => {
                    eprintln!("  Warning: could not encrypt vault on remote: {e}");
                }
            }
        }
    } else {
        let vault_base = app.repo_root.join(vault_dir);
        let example_path = vault_base.join("vault.example.yml");
        let target_path = vault_base.join(format!("vault.{vault_prefix}.yml"));

        if target_path.exists() {
            eprintln!("  Vault file already exists locally, skipping creation.");
            return;
        }

        if !example_path.exists() {
            eprintln!("  Warning: vault.example.yml not found at {}", example_path.display());
            return;
        }

        if let Err(e) = std::fs::copy(&example_path, &target_path) {
            eprintln!("  Warning: could not copy vault example: {e}");
            return;
        }

        let env_script = app.repo_root.join("scripts/lib/vault-pass-from-env.sh");
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            let _ = std::fs::set_permissions(&env_script, std::fs::Permissions::from_mode(0o755));
        }

        let result = std::process::Command::new("ansible-vault")
            .args(["encrypt", &target_path.to_string_lossy(), "--vault-password-file", &env_script.to_string_lossy()])
            .env("ANSIBLE_VAULT_PASSWORD", vault_pw)
            .output();

        match result {
            Ok(ref o) if o.status.success() => {
                if let Ok(content) = std::fs::read_to_string(&target_path) {
                    if content.starts_with("$ANSIBLE_VAULT") {
                        eprintln!("  ✓ Ansible vault created and encrypted locally");
                    } else {
                        eprintln!("  Warning: ansible-vault reported success but file is not encrypted");
                    }
                }
            }
            Ok(ref o) => {
                let stderr = String::from_utf8_lossy(&o.stderr);
                eprintln!(
                    "  Warning: ansible-vault encrypt failed (exit {}): {}",
                    o.status.code().unwrap_or(-1),
                    stderr.trim()
                );
            }
            Err(e) => {
                eprintln!("  Warning: could not run ansible-vault: {e}");
            }
        }
    }
}

/// Export the active profile's vault key to a remote host with a new master password.
fn handle_profile_export(app: &App) {
    use modules::vault;

    let (profile_id, profile) = match app.active_profile() {
        Some((id, p)) => (id.to_string(), p.clone()),
        None => {
            eprintln!("No active profile.");
            return;
        }
    };

    if !profile.remote {
        eprintln!("Profile export is only for remote profiles.");
        return;
    }

    let vault_pw = match &app.vault_password {
        Some(pw) => pw.clone(),
        None => {
            eprintln!("Vault is not unlocked. Select the profile first to unlock.");
            return;
        }
    };

    let ssh = match &app.ssh_connection {
        Some(s) => s,
        None => {
            eprintln!("No SSH connection. Cannot export to remote.");
            return;
        }
    };

    eprintln!("\n╔══════════════════════════════════════════════════════╗");
    eprintln!("║             Export Profile to Host                    ║");
    eprintln!("╚══════════════════════════════════════════════════════╝\n");
    eprintln!("This will deploy an encrypted vault key to the remote host.");
    eprintln!("The remote user will use their own master password to unlock.\n");

    let remote_pw = match vault::prompt_new_password("Remote user's master password: ") {
        Ok(pw) => pw,
        Err(e) => {
            eprintln!("Error: {e}");
            return;
        }
    };

    let enc = match vault::encrypt_vault_password(&vault_pw, &remote_pw) {
        Ok(e) => e,
        Err(e) => {
            eprintln!("Error encrypting vault key: {e}");
            return;
        }
    };

    let json = match serde_json::to_string_pretty(&enc) {
        Ok(j) => j,
        Err(e) => {
            eprintln!("Error serializing vault key: {e}");
            return;
        }
    };

    let escaped_json = json.replace('\'', "'\\''");
    let cmd = format!(
        "mkdir -p ~/.busibox/vault-keys && \
         printf '%s\\n' '{escaped_json}' > ~/.busibox/vault-keys/{profile_id}.enc && \
         chmod 600 ~/.busibox/vault-keys/{profile_id}.enc && \
         echo EXPORT_OK"
    );
    match ssh.run(&cmd) {
        Ok(output) => {
            if output.contains("EXPORT_OK") {
                eprintln!("✓ Vault key exported to remote host");
                eprintln!("  Location: ~/.busibox/vault-keys/{profile_id}.enc");
            } else {
                eprintln!("Warning: export may have failed");
                for line in output.lines() {
                    eprintln!("  {}", line);
                }
            }
        }
        Err(e) => {
            eprintln!("Error deploying vault key: {e}");
        }
    }

    // Also export the profile config
    let profile_json = match serde_json::to_string_pretty(&profile) {
        Ok(j) => j,
        Err(e) => {
            eprintln!("Warning: could not serialize profile: {e}");
            return;
        }
    };

    let escaped_profile = profile_json.replace('\'', "'\\''");
    let profile_cmd = format!(
        "mkdir -p ~/.busibox && \
         printf '%s\\n' '{escaped_profile}' > ~/.busibox/profile-{profile_id}.json && \
         echo PROFILE_OK"
    );
    match ssh.run(&profile_cmd) {
        Ok(output) => {
            if output.contains("PROFILE_OK") {
                eprintln!("✓ Profile config exported to remote host");
            }
        }
        Err(e) => {
            eprintln!("Warning: could not export profile config: {e}");
        }
    }
}

/// Change the master password for a profile's vault key.
/// Uses profile_id_override if provided (e.g. from profile select), else the active profile.
fn handle_password_change(app: &App, profile_id_override: Option<&str>) {
    use modules::vault;

    let profile_id = match profile_id_override {
        Some(id) => id.to_string(),
        None => match app.active_profile() {
            Some((id, _)) => id.to_string(),
            None => {
                eprintln!("No active profile.");
                return;
            }
        },
    };

    if !vault::has_vault_key(&profile_id) {
        eprintln!("No vault key for profile '{profile_id}'.");
        return;
    }

    eprintln!("\n╔══════════════════════════════════════════════════════╗");
    eprintln!("║           Change Master Password                     ║");
    eprintln!("╚══════════════════════════════════════════════════════╝\n");

    // Verify current master password
    let current_pw = match vault::prompt_password("Current master password: ") {
        Ok(pw) => pw,
        Err(e) => {
            eprintln!("Error: {e}");
            return;
        }
    };

    let key_path = match vault::vault_key_path(&profile_id) {
        Ok(p) => p,
        Err(e) => {
            eprintln!("Error: {e}");
            return;
        }
    };

    let enc = match vault::load_encrypted_vault(&key_path) {
        Ok(e) => e,
        Err(e) => {
            eprintln!("Error loading vault key: {e}");
            return;
        }
    };

    let vault_pw = match vault::decrypt_vault_password(&enc, &current_pw) {
        Ok(pw) => pw,
        Err(_) => {
            eprintln!("Incorrect master password.");
            return;
        }
    };

    eprintln!("✓ Current password verified\n");

    // Prompt for new password
    let new_pw = match vault::prompt_new_password("New master password: ") {
        Ok(pw) => pw,
        Err(e) => {
            eprintln!("Error: {e}");
            return;
        }
    };

    // Re-encrypt with new master password
    let new_enc = match vault::encrypt_vault_password(&vault_pw, &new_pw) {
        Ok(e) => e,
        Err(e) => {
            eprintln!("Error re-encrypting: {e}");
            return;
        }
    };

    if let Err(e) = vault::save_encrypted_vault(&key_path, &new_enc) {
        eprintln!("Error saving vault key: {e}");
        return;
    }

    eprintln!("✓ Master password changed for profile '{profile_id}'");
}

/// Deploy the busibox CLI binary to the remote host.
fn handle_deploy_binary(app: &App) {
    let (_, profile) = match app.active_profile() {
        Some((id, p)) => (id.to_string(), p.clone()),
        None => {
            eprintln!("No active profile.");
            return;
        }
    };

    if !profile.remote {
        eprintln!("Deploy CLI is only for remote profiles.");
        return;
    }

    let ssh = match &app.ssh_connection {
        Some(s) => s,
        None => {
            eprintln!("No SSH connection. Connect to the remote host first.");
            return;
        }
    };

    eprintln!("\n╔══════════════════════════════════════════════════════╗");
    eprintln!("║            Deploy CLI to Remote Host                  ║");
    eprintln!("╚══════════════════════════════════════════════════════╝\n");

    // Find the currently running binary
    let current_binary = match std::env::current_exe() {
        Ok(p) => p,
        Err(e) => {
            eprintln!("Error: could not determine current binary path: {e}");
            return;
        }
    };

    if !current_binary.exists() {
        eprintln!("Error: current binary not found at {}", current_binary.display());
        return;
    }

    let remote_path = profile.effective_remote_path();

    // Detect remote architecture to see if we need cross-compilation
    let remote_arch = ssh.run("uname -m").unwrap_or_default().trim().to_string();
    let remote_os = ssh.run("uname -s").unwrap_or_default().trim().to_lowercase();
    let local_arch = std::env::consts::ARCH;
    let local_os = std::env::consts::OS;

    eprintln!("  Local:  {local_os}/{local_arch}");
    eprintln!("  Remote: {remote_os}/{remote_arch}");

    let arch_match = (local_os == remote_os || (local_os == "macos" && remote_os == "darwin"))
        && (local_arch == remote_arch
            || (local_arch == "aarch64" && remote_arch == "arm64")
            || (local_arch == "arm64" && remote_arch == "aarch64"));

    if !arch_match {
        eprintln!("\n  Architecture mismatch. Cross-compilation needed.");
        eprintln!("  Build the binary for {remote_os}/{remote_arch} first, then re-run.");
        eprintln!("  Example: cargo build --release --target <target-triple>");
        return;
    }

    eprintln!("  Architecture compatible. Deploying binary...\n");

    // Ensure remote directory exists
    if let Err(e) = ssh.run(&format!("mkdir -p {remote_path}")) {
        eprintln!("Error creating remote directory: {e}");
        return;
    }

    // Use rsync to copy the binary
    let key_expanded = modules::ssh::shellexpand_path(&ssh.key_path);
    let remote_dest = format!("{}@{}:{}/busibox", ssh.user, ssh.host, remote_path);

    let mut rsync_args = vec!["-az".to_string(), "--progress".to_string()];
    if !key_expanded.is_empty() && std::path::Path::new(&key_expanded).exists() {
        rsync_args.push("-e".to_string());
        rsync_args.push(format!("ssh -i {key_expanded} -o StrictHostKeyChecking=accept-new"));
    }
    rsync_args.push(current_binary.to_string_lossy().to_string());
    rsync_args.push(remote_dest);

    eprintln!("  Copying binary...");
    let output = std::process::Command::new("rsync")
        .args(&rsync_args)
        .stdin(std::process::Stdio::null())
        .stdout(std::process::Stdio::inherit())
        .stderr(std::process::Stdio::inherit())
        .status();

    match output {
        Ok(status) if status.success() => {
            // Make executable
            let chmod_cmd = format!("chmod +x {remote_path}/busibox");
            let _ = ssh.run(&chmod_cmd);
            eprintln!("\n✓ CLI binary deployed to {remote_path}/busibox");
        }
        Ok(status) => {
            eprintln!("Error: rsync failed (exit {})", status.code().unwrap_or(-1));
        }
        Err(e) => {
            eprintln!("Error: could not run rsync: {e}");
        }
    }
}
