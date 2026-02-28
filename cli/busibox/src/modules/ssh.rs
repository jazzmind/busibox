use color_eyre::{eyre::eyre, Result};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};

#[derive(Debug, Clone)]
pub struct SshKey {
    pub private_path: PathBuf,
    pub public_path: PathBuf,
    pub key_type: String,
}

#[derive(Debug, Clone)]
pub struct SshConnection {
    pub host: String,
    pub user: String,
    pub key_path: String,
}

impl SshConnection {
    pub fn new(host: &str, user: &str, key_path: &str) -> Self {
        Self {
            host: host.to_string(),
            user: user.to_string(),
            key_path: key_path.to_string(),
        }
    }

    pub fn ssh_target(&self) -> String {
        format!("{}@{}", self.user, self.host)
    }

    fn build_args(&self) -> Vec<String> {
        let mut args = vec![
            "-o".into(),
            "BatchMode=yes".into(),
            "-o".into(),
            "StrictHostKeyChecking=accept-new".into(),
            "-o".into(),
            "ConnectTimeout=10".into(),
        ];
        let key = shellexpand_path(&self.key_path);
        if !key.is_empty() && Path::new(&key).exists() {
            args.push("-i".into());
            args.push(key);
        }
        args.push(self.ssh_target());
        args
    }

    /// Test if SSH connection works without a password.
    pub fn test_connection(&self) -> bool {
        let mut args = self.build_args();
        args.push("true".into());
        Command::new("ssh")
            .args(&args)
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .status()
            .map(|s| s.success())
            .unwrap_or(false)
    }

    /// Run a command on the remote host, return stdout.
    pub fn run(&self, cmd: &str) -> Result<String> {
        let mut args = self.build_args();
        args.push(cmd.to_string());
        let output = Command::new("ssh").args(&args).output()?;
        if output.status.success() {
            Ok(String::from_utf8_lossy(&output.stdout).to_string())
        } else {
            let stderr = String::from_utf8_lossy(&output.stderr);
            Err(eyre!("SSH command failed: {}", stderr.trim()))
        }
    }

    /// Run a command with a live TTY (for interactive output like logs).
    pub fn run_tty(&self, cmd: &str) -> Result<std::process::ExitStatus> {
        let mut args = vec!["-t".to_string()];
        let key = shellexpand_path(&self.key_path);
        if !key.is_empty() && Path::new(&key).exists() {
            args.push("-i".into());
            args.push(key);
        }
        args.push("-o".into());
        args.push("StrictHostKeyChecking=accept-new".into());
        args.push(self.ssh_target());
        args.push(cmd.to_string());
        Ok(Command::new("ssh")
            .args(&args)
            .stdin(Stdio::inherit())
            .stdout(Stdio::inherit())
            .stderr(Stdio::inherit())
            .status()?)
    }

    /// Check if a command exists on the remote host.
    pub fn has_command(&self, cmd: &str) -> bool {
        self.run(&format!("which {cmd} 2>/dev/null"))
            .map(|s| !s.trim().is_empty())
            .unwrap_or(false)
    }
}

/// Find existing SSH keys in ~/.ssh/.
pub fn find_ssh_keys() -> Vec<SshKey> {
    let ssh_dir = match dirs::home_dir() {
        Some(h) => h.join(".ssh"),
        None => return Vec::new(),
    };

    let key_names = [
        ("id_ed25519", "ed25519"),
        ("id_rsa", "rsa"),
        ("id_ecdsa", "ecdsa"),
        ("busibox-remote-ed25519", "ed25519"),
    ];

    key_names
        .iter()
        .filter_map(|(name, key_type)| {
            let private_path = ssh_dir.join(name);
            let public_path = ssh_dir.join(format!("{name}.pub"));
            if private_path.exists() {
                Some(SshKey {
                    private_path,
                    public_path,
                    key_type: key_type.to_string(),
                })
            } else {
                None
            }
        })
        .collect()
}

/// Generate a new ed25519 SSH key.
pub fn generate_key(path: &Path) -> Result<SshKey> {
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)?;
    }

    let status = Command::new("ssh-keygen")
        .args([
            "-t",
            "ed25519",
            "-f",
            &path.to_string_lossy(),
            "-N",
            "",
            "-C",
            "busibox-cli",
        ])
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status()?;

    if !status.success() {
        return Err(eyre!("ssh-keygen failed"));
    }

    Ok(SshKey {
        private_path: path.to_path_buf(),
        public_path: path.with_extension("pub"),
        key_type: "ed25519".into(),
    })
}

/// Copy an SSH key to a remote host using ssh-copy-id.
/// This will prompt the user for a password interactively.
pub fn copy_key_interactive(key_path: &Path, host: &str, user: &str) -> Result<bool> {
    let status = Command::new("ssh-copy-id")
        .args([
            "-i",
            &key_path.to_string_lossy(),
            &format!("{user}@{host}"),
        ])
        .stdin(Stdio::inherit())
        .stdout(Stdio::inherit())
        .stderr(Stdio::inherit())
        .status()?;

    Ok(status.success())
}

/// Test if we can connect to a host with any available key (no password).
pub fn test_any_key_connection(host: &str, user: &str) -> Option<SshKey> {
    for key in find_ssh_keys() {
        let conn = SshConnection::new(host, user, &key.private_path.to_string_lossy());
        if conn.test_connection() {
            return Some(key);
        }
    }
    None
}

pub fn shellexpand_path(path: &str) -> String {
    if path.starts_with("~/") {
        if let Some(home) = dirs::home_dir() {
            return format!("{}{}", home.display(), &path[1..]);
        }
    }
    path.to_string()
}
