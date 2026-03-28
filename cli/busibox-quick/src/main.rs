//! busibox-quick — simple single-machine installer.
//!
//! For users who just want to run Busibox locally via Docker.
//! No TUI, no multi-profile management — just guided sequential prompts.

use busibox_core::deploy::DeployContext;
use busibox_core::hardware::HardwareProfile;
use busibox_core::profile::{self, Profile};
use busibox_core::vault;
use busibox_providers::backend;
use color_eyre::Result;
use std::io::{self, Write};
use std::path::PathBuf;

const PROFILE_ID: &str = "local-development-docker";

fn main() -> Result<()> {
    color_eyre::install()?;

    println!();
    println!("  ╔══════════════════════════════════════════════╗");
    println!("  ║         Busibox Quick Installer              ║");
    println!("  ║  Local Docker setup for trying out Busibox   ║");
    println!("  ╚══════════════════════════════════════════════╝");
    println!();

    let repo_root = find_repo_root()?;
    println!("  Repository: {}", repo_root.display());

    // Step 1: Detect hardware
    println!();
    println!("  Detecting hardware...");
    let hw = HardwareProfile::detect_local()?;
    println!("  OS:     {} ({})", hw.os, hw.arch);
    println!("  RAM:    {} GB", hw.ram_gb);
    println!("  LLM:    {}", hw.llm_backend);
    println!("  Tier:   {} ({})", hw.memory_tier.name(), hw.memory_tier.description());

    if !hw.docker_available {
        eprintln!();
        eprintln!("  ERROR: Docker is not installed.");
        eprintln!("  Install Docker Desktop (macOS) or Docker Engine (Linux) first.");
        eprintln!();
        std::process::exit(1);
    }
    println!("  Docker: available");

    // Step 2: Get admin email
    println!();
    let admin_email = prompt("  Admin email address: ")?;
    if admin_email.trim().is_empty() {
        eprintln!("  ERROR: Admin email is required.");
        std::process::exit(1);
    }

    // Step 3: Set up vault
    println!();
    println!("  Setting up secrets vault...");
    let vault_password = vault::generate_vault_password();

    let vault_exists = vault::has_vault_file(&repo_root, PROFILE_ID);
    if !vault_exists {
        println!("  Creating vault from template...");
        vault::create_vault_from_example(&repo_root, PROFILE_ID, &vault_password)?;
        println!("  Vault created and encrypted.");
    } else {
        println!("  Vault file already exists.");
    }

    // Step 4: Encrypt the vault password with a master password
    println!();
    println!("  Choose a master password to protect your vault.");
    println!("  You will need this password to manage Busibox later.");
    let master_password = rpassword::prompt_password("  Master password: ")
        .map_err(|e| color_eyre::eyre::eyre!("Failed to read password: {e}"))?;
    if master_password.is_empty() {
        eprintln!("  ERROR: Master password cannot be empty.");
        std::process::exit(1);
    }
    let confirm = rpassword::prompt_password("  Confirm password: ")
        .map_err(|e| color_eyre::eyre::eyre!("Failed to read password: {e}"))?;
    if master_password != confirm {
        eprintln!("  ERROR: Passwords do not match.");
        std::process::exit(1);
    }

    let encrypted = vault::encrypt_vault_password(&vault_password, &master_password)?;
    let key_path = vault::vault_key_path(PROFILE_ID)?;
    vault::save_encrypted_vault(&key_path, &encrypted)?;
    println!("  Vault key saved.");

    // Step 5: Create profile
    let prof = Profile {
        environment: "development".to_string(),
        backend: "docker".to_string(),
        label: "Local Development".to_string(),
        created: Some(chrono_now()),
        vault_prefix: Some(PROFILE_ID.to_string()),
        remote: false,
        remote_host: None,
        remote_user: None,
        remote_ssh_key: None,
        remote_busibox_path: None,
        tailscale_ip: None,
        hardware: Some(hw.clone()),
        kubeconfig: None,
        model_tier: Some(hw.memory_tier.name().to_string()),
        admin_email: Some(admin_email.trim().to_string()),
        allowed_email_domains: None,
        frontend_ref: None,
        site_domain: Some("localhost".to_string()),
        ssl_cert_name: None,
        network_base_octets: None,
        use_production_vllm: None,
        docker_runtime: Some("auto".to_string()),
        github_token: None,
        cloud_provider: None,
        cloud_api_key: None,
        llm_backend_override: None,
        k8s_overlay: None,
        spot_token: None,
        dev_apps_dir: None,
        huggingface_token: None,
        direct_access: None,
    };

    profile::upsert_profile(&repo_root, PROFILE_ID, prof.clone(), true)?;
    println!("  Profile created: {PROFILE_ID}");

    // Step 6: Deploy
    println!();
    println!("  Starting deployment...");
    println!("  This will set up PostgreSQL, AuthZ, Deploy API, Portal, and more.");
    println!();

    let ctx = DeployContext::from_profile(PROFILE_ID, &prof, repo_root.clone(), Some(vault_password));
    let backend = backend::create_backend(ctx.clone());

    // Run prerequisite checks
    let checks = backend.prerequisite_checks()?;
    for check in &checks {
        let icon = if check.passed { "✓" } else { "✗" };
        println!("  {icon} {}", check.message);
    }
    if checks.iter().any(|c| !c.passed) {
        eprintln!();
        eprintln!("  ERROR: Prerequisites not met. Install missing dependencies and retry.");
        std::process::exit(1);
    }

    // Deploy bootstrap services
    let bootstrap_services = ["postgres", "authz", "config", "deploy", "core-apps", "proxy"];
    let env = ctx.make_env();

    for service in &bootstrap_services {
        println!();
        println!("  Deploying {service}...");
        let (tx, rx) = std::sync::mpsc::channel();

        let service_clone = service.to_string();
        let env_clone = env.clone();
        let backend_ref = backend::create_backend(ctx.clone());

        let handle = std::thread::spawn(move || {
            backend_ref.deploy_service(&service_clone, &env_clone, &tx)
        });

        // Print output as it streams
        for line in rx {
            println!("    {line}");
        }

        match handle.join() {
            Ok(Ok(0)) => println!("  ✓ {service} deployed successfully"),
            Ok(Ok(code)) => {
                eprintln!("  ✗ {service} failed (exit code {code})");
                eprintln!("  You can retry later with: make install SERVICE={service}");
            }
            Ok(Err(e)) => {
                eprintln!("  ✗ {service} error: {e}");
            }
            Err(_) => {
                eprintln!("  ✗ {service} thread panicked");
            }
        }
    }

    println!();
    println!("  ══════════════════════════════════════════════");
    println!("  Installation complete!");
    println!();
    println!("  Open the Busibox Portal to finish setup:");
    println!("  http://localhost/portal");
    println!();
    println!("  To manage your installation later:");
    println!("    busibox          # Full interactive TUI");
    println!("    make manage      # Service management menu");
    println!("  ══════════════════════════════════════════════");
    println!();

    Ok(())
}

fn find_repo_root() -> Result<PathBuf> {
    // Try the current directory first
    if let Ok(root) = profile::find_repo_root() {
        return Ok(root);
    }
    // Try the directory of the executable
    if let Ok(exe) = std::env::current_exe() {
        if let Some(parent) = exe.parent() {
            let mut dir = parent.to_path_buf();
            loop {
                if dir.join("Makefile").exists() && dir.join("scripts").exists() {
                    return Ok(dir);
                }
                if !dir.pop() {
                    break;
                }
            }
        }
    }
    Err(color_eyre::eyre::eyre!(
        "Could not find busibox repo root. Run from within the busibox directory."
    ))
}

fn prompt(msg: &str) -> Result<String> {
    print!("{msg}");
    io::stdout().flush()?;
    let mut input = String::new();
    io::stdin().read_line(&mut input)?;
    Ok(input.trim().to_string())
}

fn chrono_now() -> String {
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default();
    format!("{}", now.as_secs())
}
