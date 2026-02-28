use crate::modules::ssh::SshConnection;
use color_eyre::{eyre::eyre, Result};
use std::path::Path;
use std::process::{Command, Stdio};

const RSYNC_EXCLUDES: &[&str] = &[
    ".git/",
    ".busibox/",
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
];

/// Sync the local busibox repo to a remote host using rsync.
pub fn sync(
    local_path: &Path,
    host: &str,
    user: &str,
    key_path: &str,
    remote_path: &str,
) -> Result<()> {
    let mut args: Vec<String> = vec![
        "-azP".into(),
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

    let status = Command::new("rsync")
        .args(&args)
        .stdout(Stdio::inherit())
        .stderr(Stdio::inherit())
        .status()?;

    if status.success() {
        Ok(())
    } else {
        Err(eyre!("rsync failed with exit code {:?}", status.code()))
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

/// Execute a make command on the remote host and capture stdout.
pub fn exec_make_capture(
    ssh: &SshConnection,
    remote_path: &str,
    make_args: &str,
) -> Result<String> {
    let cmd = format!("cd {remote_path} && USE_MANAGER=0 make {make_args} 2>&1");
    ssh.run(&cmd).map(|s| strip_ansi(&s))
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

/// Run a local make command with USE_MANAGER=0 and capture stdout.
pub fn run_local_make_capture(repo_root: &Path, args: &str) -> Result<String> {
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
    Ok(strip_ansi(&combined))
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
    let cmd = format!("cd {remote_path} && USE_MANAGER=0 make {make_args} 2>&1");
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
