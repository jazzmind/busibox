use crate::modules::ssh::SshConnection;
use color_eyre::{eyre::eyre, Result};
use std::process::Command;

#[derive(Debug, Clone)]
pub enum AuthMode {
    Cloud { auth_key: String },
    Headscale { server_url: String },
}

#[derive(Debug, Clone)]
pub struct TailscaleStatus {
    pub installed: bool,
    pub running: bool,
    pub ip: Option<String>,
    pub hostname: Option<String>,
}

/// Check if Tailscale is installed locally.
pub fn is_installed_locally() -> bool {
    which::which("tailscale").is_ok()
}

/// Get local Tailscale status.
pub fn local_status() -> TailscaleStatus {
    if !is_installed_locally() {
        return TailscaleStatus {
            installed: false,
            running: false,
            ip: None,
            hostname: None,
        };
    }

    let ip = Command::new("tailscale")
        .args(["ip", "-4"])
        .output()
        .ok()
        .filter(|o| o.status.success())
        .and_then(|o| String::from_utf8(o.stdout).ok())
        .map(|s| s.trim().to_string())
        .filter(|s| !s.is_empty());

    let hostname = Command::new("tailscale")
        .args(["status", "--self", "--json"])
        .output()
        .ok()
        .filter(|o| o.status.success())
        .and_then(|o| String::from_utf8(o.stdout).ok())
        .and_then(|s| {
            serde_json::from_str::<serde_json::Value>(&s)
                .ok()
                .and_then(|v| v["Self"]["HostName"].as_str().map(|s| s.to_string()))
        });

    let running = ip.is_some();

    TailscaleStatus {
        installed: true,
        running,
        ip,
        hostname,
    }
}

/// Check Tailscale status on a remote host.
pub fn remote_status(ssh: &SshConnection) -> TailscaleStatus {
    let installed = ssh.has_command("tailscale");
    if !installed {
        return TailscaleStatus {
            installed: false,
            running: false,
            ip: None,
            hostname: None,
        };
    }

    let ip = ssh
        .run("tailscale ip -4 2>/dev/null")
        .ok()
        .map(|s| s.trim().to_string())
        .filter(|s| !s.is_empty());

    let running = ip.is_some();

    TailscaleStatus {
        installed: true,
        running,
        ip,
        hostname: None,
    }
}

/// Install Tailscale on a remote host.
pub fn install_remote(ssh: &SshConnection) -> Result<()> {
    let result =
        ssh.run("curl -fsSL https://tailscale.com/install.sh | sh 2>&1");
    match result {
        Ok(_) => Ok(()),
        Err(e) => Err(eyre!("Failed to install Tailscale: {e}")),
    }
}

/// Authenticate Tailscale on a remote host.
pub fn authenticate_remote(ssh: &SshConnection, mode: &AuthMode) -> Result<()> {
    let cmd = match mode {
        AuthMode::Cloud { auth_key } => {
            format!("sudo tailscale up --authkey={auth_key} --ssh 2>&1")
        }
        AuthMode::Headscale { server_url } => {
            format!("sudo tailscale up --login-server={server_url} --ssh 2>&1")
        }
    };

    let result = ssh.run(&cmd);
    match result {
        Ok(_) => Ok(()),
        Err(e) => Err(eyre!("Failed to authenticate Tailscale: {e}")),
    }
}

/// Authenticate Tailscale locally.
pub fn authenticate_local(mode: &AuthMode) -> Result<()> {
    let mut args = vec!["up".to_string(), "--ssh".to_string()];
    match mode {
        AuthMode::Cloud { auth_key } => {
            args.push(format!("--authkey={auth_key}"));
        }
        AuthMode::Headscale { server_url } => {
            args.push(format!("--login-server={server_url}"));
        }
    }

    let status = Command::new("tailscale").args(&args).status()?;
    if status.success() {
        Ok(())
    } else {
        Err(eyre!("tailscale up failed"))
    }
}

/// Verify connectivity between two Tailscale IPs by pinging.
pub fn verify_connectivity(target_ip: &str) -> bool {
    Command::new("ping")
        .args(["-c", "1", "-W", "3", target_ip])
        .output()
        .map(|o| o.status.success())
        .unwrap_or(false)
}
