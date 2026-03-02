use crate::modules::ssh::SshConnection;
use color_eyre::{eyre::eyre, Result};
use std::path::Path;
use std::process::{Command, Stdio};

/// Shell preamble that augments PATH for ansible/pip locations.
/// Uses `find` instead of shell globs to be safe in both bash and zsh
/// (zsh errors on unmatched globs by default).
pub const SHELL_PATH_PREAMBLE: &str = "\
    for d in \"$HOME/.local/bin\" /usr/local/bin /opt/homebrew/bin; do [ -d \"$d\" ] && export PATH=\"$d:$PATH\"; done; \
    for d in $(find \"$HOME/Library/Python\" -maxdepth 2 -name bin -type d 2>/dev/null); do export PATH=\"$d:$PATH\"; done; ";

const RSYNC_EXCLUDES: &[&str] = &[
    ".git/",
    ".busibox/vault-keys/",
    ".busibox/profiles/",
    ".busibox-state-*",
    ".env.*",
    "__pycache__/",
    "*.pyc",
    "node_modules/",
    "dist/",
    "build/",
    ".venv/",
    "venv/",
    ".next/",
    "*.log",
    "ssl/",
    "k8s/kubeconfig-*.yaml",
    "k8s/secrets/",
    ".DS_Store",
    "dev-apps/",
    "cli/busibox/target/",
    "provision/ansible/roles/secrets/vars/vault.dev.yml",
    "provision/ansible/roles/secrets/vars/vault.staging.yml",
    "provision/ansible/roles/secrets/vars/vault.prod.yml",
    "provision/ansible/roles/secrets/vars/vault.demo.yml",
];

/// Sync the local busibox repo to a remote host using rsync.
/// Output is captured so it doesn't bleed into the TUI.
pub fn sync(
    local_path: &Path,
    host: &str,
    user: &str,
    key_path: &str,
    remote_path: &str,
) -> Result<()> {
    let mut args: Vec<String> = vec![
        "-az".into(),
        "--delete".into(),
    ];

    for pattern in RSYNC_EXCLUDES {
        args.push("--exclude".into());
        args.push((*pattern).into());
    }

    let key_expanded = shellexpand(key_path);
    if !key_expanded.is_empty() && Path::new(&key_expanded).exists() {
        args.push("-e".into());
        args.push(format!(
            "ssh -i {key_expanded} -o StrictHostKeyChecking=accept-new"
        ));
    }

    let src = format!("{}/", local_path.to_string_lossy());
    let dest = format!("{user}@{host}:{remote_path}/");
    args.push(src);
    args.push(dest);

    let output = Command::new("rsync")
        .args(&args)
        .output()?;

    if output.status.success() {
        Ok(())
    } else {
        let stderr = String::from_utf8_lossy(&output.stderr);
        Err(eyre!("rsync failed (exit {:?}): {}", output.status.code(), stderr.trim()))
    }
}

/// Execute a make command on the remote host and stream output back.
/// Uses USE_MANAGER=0 to avoid spinning up ephemeral manager containers.
pub fn exec_make(
    ssh: &SshConnection,
    remote_path: &str,
    make_args: &str,
) -> Result<i32> {
    let cmd = format!("cd {remote_path} && USE_MANAGER=0 make {make_args}");
    let status = ssh.run_tty(&cmd)?;
    Ok(status.code().unwrap_or(1))
}

/// Run a local make command interactively (for long-running commands like logs).
/// Caller must suspend the TUI first with tui::suspend().
pub fn run_local_make_interactive(repo_root: &Path, args: &str) -> Result<i32> {
    run_local_make(repo_root, args)
}

/// Run a local make command with USE_MANAGER=0 to avoid spinning up
/// ephemeral manager containers for each invocation.
pub fn run_local_make(repo_root: &Path, args: &str) -> Result<i32> {
    let status = Command::new("make")
        .args(args.split_whitespace())
        .env("USE_MANAGER", "0")
        .current_dir(repo_root)
        .stdin(Stdio::inherit())
        .stdout(Stdio::inherit())
        .stderr(Stdio::inherit())
        .status()?;
    Ok(status.code().unwrap_or(1))
}

/// Run a local make command with USE_MANAGER=0, capture all output, return (exit_code, output).
/// This prevents make output from bleeding into the TUI.
pub fn run_local_make_quiet(repo_root: &Path, args: &str) -> Result<(i32, String)> {
    let output = Command::new("make")
        .args(args.split_whitespace())
        .env("USE_MANAGER", "0")
        .current_dir(repo_root)
        .output()?;
    let combined = format!(
        "{}{}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    Ok((output.status.code().unwrap_or(1), strip_ansi(&combined)))
}

/// Execute a make command on the remote host, capture output, return (exit_code, output).
/// This prevents SSH output from bleeding into the TUI.
pub fn exec_make_quiet(
    ssh: &SshConnection,
    remote_path: &str,
    make_args: &str,
) -> Result<(i32, String)> {
    let cmd = format!(
        "{SHELL_PATH_PREAMBLE}\
         [ -f \"$HOME/.profile\" ] && . \"$HOME/.profile\" 2>/dev/null || true; \
         [ -f \"$HOME/.bashrc\" ] && . \"$HOME/.bashrc\" 2>/dev/null || true; \
         cd {remote_path} && USE_MANAGER=0 make {make_args} 2>&1"
    );
    let mut args: Vec<String> = vec![
        "-o".into(),
        "BatchMode=yes".into(),
        "-o".into(),
        "StrictHostKeyChecking=accept-new".into(),
        "-o".into(),
        "ConnectTimeout=10".into(),
    ];
    let key = crate::modules::ssh::shellexpand_path(&ssh.key_path);
    if !key.is_empty() && Path::new(&key).exists() {
        args.push("-i".into());
        args.push(key);
    }
    args.push(ssh.ssh_target());
    args.push(cmd);

    let output = Command::new("ssh").args(&args).output()?;
    let exit_code = output.status.code().unwrap_or(1);
    let combined = format!(
        "{}{}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    Ok((exit_code, strip_ansi(&combined)))
}

/// Like `run_local_make_quiet` but injects the vault password via
/// the `ANSIBLE_VAULT_PASSWORD` environment variable so shell scripts
/// can pick it up without a plaintext file on disk.
pub fn run_local_make_quiet_with_vault(
    repo_root: &Path,
    args: &str,
    vault_password: &str,
) -> Result<(i32, String)> {
    let output = Command::new("make")
        .args(args.split_whitespace())
        .env("USE_MANAGER", "0")
        .env("ANSIBLE_VAULT_PASSWORD", vault_password)
        .current_dir(repo_root)
        .output()?;
    let combined = format!(
        "{}{}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    Ok((output.status.code().unwrap_or(1), strip_ansi(&combined)))
}

/// Like `exec_make_quiet` but securely delivers the vault password to the
/// remote host via ANSIBLE_VAULT_PASSWORD env var. The env var is read by
/// scripts/lib/vault-pass-from-env.sh which Ansible uses as --vault-password-file.
pub fn exec_make_quiet_with_vault(
    ssh: &SshConnection,
    remote_path: &str,
    make_args: &str,
    vault_password: &str,
) -> Result<(i32, String)> {
    let escaped_pw = vault_password.replace('\'', "'\\''");

    let cmd = format!(
        "{SHELL_PATH_PREAMBLE}\
         [ -f \"$HOME/.profile\" ] && . \"$HOME/.profile\" 2>/dev/null || true; \
         [ -f \"$HOME/.bashrc\" ] && . \"$HOME/.bashrc\" 2>/dev/null || true; \
         export ANSIBLE_VAULT_PASSWORD='{escaped_pw}'; \
         cd {remote_path} && USE_MANAGER=0 make {make_args} 2>&1"
    );
    let mut args: Vec<String> = vec![
        "-o".into(),
        "BatchMode=yes".into(),
        "-o".into(),
        "StrictHostKeyChecking=accept-new".into(),
        "-o".into(),
        "ConnectTimeout=10".into(),
    ];
    let key = crate::modules::ssh::shellexpand_path(&ssh.key_path);
    if !key.is_empty() && Path::new(&key).exists() {
        args.push("-i".into());
        args.push(key);
    }
    args.push(ssh.ssh_target());
    args.push(cmd);

    let output = Command::new("ssh").args(&args).output()?;
    let exit_code = output.status.code().unwrap_or(1);
    let combined = format!(
        "{}{}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    Ok((exit_code, strip_ansi(&combined)))
}

/// Like `run_local_make_quiet` but streams output line-by-line via a callback
/// instead of buffering until the process exits.
pub fn run_local_make_quiet_streaming<F>(
    repo_root: &Path,
    args: &str,
    mut on_line: F,
) -> Result<i32>
where
    F: FnMut(&str),
{
    use std::io::BufRead;

    let mut child = Command::new("make")
        .args(args.split_whitespace())
        .env("USE_MANAGER", "0")
        .current_dir(repo_root)
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()?;

    let stdout = child.stdout.take().ok_or_else(|| eyre!("no stdout"))?;
    let reader = std::io::BufReader::new(stdout);
    for line in reader.lines() {
        if let Ok(l) = line {
            let cleaned = strip_ansi(&l);
            let trimmed = cleaned.trim();
            if !trimmed.is_empty() {
                on_line(trimmed);
            }
        }
    }

    let status = child.wait()?;
    Ok(status.code().unwrap_or(1))
}

/// Like `run_local_make_quiet_with_vault` but streams output line-by-line.
pub fn run_local_make_quiet_with_vault_streaming<F>(
    repo_root: &Path,
    args: &str,
    vault_password: &str,
    mut on_line: F,
) -> Result<i32>
where
    F: FnMut(&str),
{
    use std::io::BufRead;

    let mut child = Command::new("make")
        .args(args.split_whitespace())
        .env("USE_MANAGER", "0")
        .env("ANSIBLE_VAULT_PASSWORD", vault_password)
        .current_dir(repo_root)
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()?;

    let stdout = child.stdout.take().ok_or_else(|| eyre!("no stdout"))?;
    let reader = std::io::BufReader::new(stdout);
    for line in reader.lines() {
        if let Ok(l) = line {
            let cleaned = strip_ansi(&l);
            let trimmed = cleaned.trim();
            if !trimmed.is_empty() {
                on_line(trimmed);
            }
        }
    }

    let status = child.wait()?;
    Ok(status.code().unwrap_or(1))
}

/// Like `exec_make_quiet` but streams output line-by-line via a callback.
pub fn exec_make_quiet_streaming<F>(
    ssh: &SshConnection,
    remote_path: &str,
    make_args: &str,
    mut on_line: F,
) -> Result<i32>
where
    F: FnMut(&str),
{
    use std::io::BufRead;

    let cmd = format!(
        "{SHELL_PATH_PREAMBLE}\
         [ -f \"$HOME/.profile\" ] && . \"$HOME/.profile\" 2>/dev/null || true; \
         [ -f \"$HOME/.bashrc\" ] && . \"$HOME/.bashrc\" 2>/dev/null || true; \
         cd {remote_path} && USE_MANAGER=0 make {make_args} 2>&1"
    );
    let mut args: Vec<String> = vec![
        "-o".into(),
        "BatchMode=yes".into(),
        "-o".into(),
        "StrictHostKeyChecking=accept-new".into(),
        "-o".into(),
        "ConnectTimeout=10".into(),
    ];
    let key = crate::modules::ssh::shellexpand_path(&ssh.key_path);
    if !key.is_empty() && Path::new(&key).exists() {
        args.push("-i".into());
        args.push(key);
    }
    args.push(ssh.ssh_target());
    args.push(cmd);

    let mut child = Command::new("ssh")
        .args(&args)
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()?;

    let stdout = child.stdout.take().ok_or_else(|| eyre!("no stdout"))?;
    let reader = std::io::BufReader::new(stdout);
    for line in reader.lines() {
        if let Ok(l) = line {
            let cleaned = strip_ansi(&l);
            let trimmed = cleaned.trim();
            if !trimmed.is_empty() {
                on_line(trimmed);
            }
        }
    }

    let status = child.wait()?;
    Ok(status.code().unwrap_or(1))
}

/// Like `exec_make_quiet_with_vault` but streams output line-by-line.
pub fn exec_make_quiet_with_vault_streaming<F>(
    ssh: &SshConnection,
    remote_path: &str,
    make_args: &str,
    vault_password: &str,
    mut on_line: F,
) -> Result<i32>
where
    F: FnMut(&str),
{
    use std::io::BufRead;

    let escaped_pw = vault_password.replace('\'', "'\\''");

    let cmd = format!(
        "{SHELL_PATH_PREAMBLE}\
         [ -f \"$HOME/.profile\" ] && . \"$HOME/.profile\" 2>/dev/null || true; \
         [ -f \"$HOME/.bashrc\" ] && . \"$HOME/.bashrc\" 2>/dev/null || true; \
         export ANSIBLE_VAULT_PASSWORD='{escaped_pw}'; \
         cd {remote_path} && USE_MANAGER=0 make {make_args} 2>&1"
    );
    let mut args: Vec<String> = vec![
        "-o".into(),
        "BatchMode=yes".into(),
        "-o".into(),
        "StrictHostKeyChecking=accept-new".into(),
        "-o".into(),
        "ConnectTimeout=10".into(),
    ];
    let key = crate::modules::ssh::shellexpand_path(&ssh.key_path);
    if !key.is_empty() && Path::new(&key).exists() {
        args.push("-i".into());
        args.push(key);
    }
    args.push(ssh.ssh_target());
    args.push(cmd);

    let mut child = Command::new("ssh")
        .args(&args)
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()?;

    let stdout = child.stdout.take().ok_or_else(|| eyre!("no stdout"))?;
    let reader = std::io::BufReader::new(stdout);
    for line in reader.lines() {
        if let Ok(l) = line {
            let cleaned = strip_ansi(&l);
            let trimmed = cleaned.trim();
            if !trimmed.is_empty() {
                on_line(trimmed);
            }
        }
    }

    let status = child.wait()?;
    Ok(status.code().unwrap_or(1))
}

/// Run an arbitrary command on the remote host and stream output line-by-line.
/// The command is run as: cd {remote_path} && {cmd} 2>&1
pub fn exec_remote_streaming<F>(
    ssh: &SshConnection,
    remote_path: &str,
    cmd: &str,
    mut on_line: F,
) -> Result<i32>
where
    F: FnMut(&str),
{
    use std::io::BufRead;

    let full_cmd = format!(
        "{SHELL_PATH_PREAMBLE}\
         [ -f \"$HOME/.profile\" ] && . \"$HOME/.profile\" 2>/dev/null || true; \
         [ -f \"$HOME/.bashrc\" ] && . \"$HOME/.bashrc\" 2>/dev/null || true; \
         cd {remote_path} && {cmd} 2>&1"
    );
    let mut args: Vec<String> = vec![
        "-o".into(),
        "BatchMode=yes".into(),
        "-o".into(),
        "StrictHostKeyChecking=accept-new".into(),
        "-o".into(),
        "ConnectTimeout=10".into(),
    ];
    let key = crate::modules::ssh::shellexpand_path(&ssh.key_path);
    if !key.is_empty() && Path::new(&key).exists() {
        args.push("-i".into());
        args.push(key);
    }
    args.push(ssh.ssh_target());
    args.push(full_cmd);

    let mut child = Command::new("ssh")
        .args(&args)
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()?;

    let stdout = child.stdout.take().ok_or_else(|| eyre!("no stdout"))?;
    let reader = std::io::BufReader::new(stdout);
    for line in reader.lines() {
        if let Ok(l) = line {
            let cleaned = strip_ansi(&l);
            let trimmed = cleaned.trim();
            if !trimmed.is_empty() {
                on_line(trimmed);
            }
        }
    }

    let status = child.wait()?;
    Ok(status.code().unwrap_or(1))
}

/// Run an arbitrary command on the remote host with the vault password
/// injected as ANSIBLE_VAULT_PASSWORD env var and ANSIBLE_VAULT_PASSWORD_FILE
/// pointing to vault-pass-from-env.sh. Returns (exit_code, combined_output).
pub fn exec_remote_with_vault(
    ssh: &SshConnection,
    remote_path: &str,
    script: &str,
    vault_password: &str,
) -> Result<(i32, String)> {
    let escaped_pw = vault_password.replace('\'', "'\\''");

    let cmd = format!(
        "{SHELL_PATH_PREAMBLE}\
         export ANSIBLE_VAULT_PASSWORD='{escaped_pw}'; \
         ANSIBLE_VAULT_PASSWORD_FILE=\"{remote_path}/scripts/lib/vault-pass-from-env.sh\"; \
         chmod +x \"$ANSIBLE_VAULT_PASSWORD_FILE\" 2>/dev/null; \
         cd {remote_path} && {script} 2>&1"
    );
    let mut args: Vec<String> = vec![
        "-o".into(),
        "BatchMode=yes".into(),
        "-o".into(),
        "StrictHostKeyChecking=accept-new".into(),
        "-o".into(),
        "ConnectTimeout=10".into(),
    ];
    let key = crate::modules::ssh::shellexpand_path(&ssh.key_path);
    if !key.is_empty() && Path::new(&key).exists() {
        args.push("-i".into());
        args.push(key);
    }
    args.push(ssh.ssh_target());
    args.push(cmd);

    let output = Command::new("ssh").args(&args).output()?;
    let exit_code = output.status.code().unwrap_or(1);
    let combined = format!(
        "{}{}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    Ok((exit_code, strip_ansi(&combined)))
}

/// Ensure the remote busibox directory exists.
pub fn ensure_remote_dir(ssh: &SshConnection, remote_path: &str) -> Result<()> {
    ssh.run(&format!("mkdir -p {remote_path}"))?;
    Ok(())
}

/// Strip ANSI escape sequences from a string.
/// Handles both real ESC bytes (\x1b) and literal \033 / \e strings.
pub fn strip_ansi(s: &str) -> String {
    use regex::Regex;
    // First, replace literal \033[ and \e[ sequences (text form, not actual ESC)
    let re_literal = Regex::new(r"\\033\[[\d;]*[a-zA-Z]").unwrap();
    let s = re_literal.replace_all(s, "");
    let re_literal_e = Regex::new(r"\\e\[[\d;]*[a-zA-Z]").unwrap();
    let s = re_literal_e.replace_all(&s, "");
    // Then strip real ESC byte sequences
    let re_real = Regex::new(r"\x1b\[[\d;]*[a-zA-Z]").unwrap();
    let s = re_real.replace_all(&s, "");
    // Also strip OSC sequences
    let re_osc = Regex::new(r"\x1b\][^\x07]*\x07").unwrap();
    let s = re_osc.replace_all(&s, "");
    s.to_string()
}

fn shellexpand(path: &str) -> String {
    if path.starts_with("~/") {
        if let Some(home) = dirs::home_dir() {
            return format!("{}{}", home.display(), &path[1..]);
        }
    }
    path.to_string()
}
