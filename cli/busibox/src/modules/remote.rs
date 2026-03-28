use crate::modules::ssh::SshConnection;
use color_eyre::{eyre::eyre, Result};
use std::path::Path;
use std::process::{Command, Stdio};

pub use busibox_core::shell::SHELL_PATH_PREAMBLE;

/// Extra patterns to exclude beyond what .gitignore covers.
/// The sync function uses rsync's `--filter=':- .gitignore'` to honour
/// gitignore rules automatically; these catch things git itself doesn't
/// need to ignore (e.g. `.git/`) or that we never want on a remote host.
const RSYNC_EXTRA_EXCLUDES: &[&str] = &[
    ".git/",
    ".cursor/",
    ".vscode/",
    ".idea/",
    "ssl/",
];

/// Files that are gitignored but must still be synced to the remote because
/// they are deployment-critical generated config.  Rsync applies the first
/// matching rule, so these `--include` entries are added *before* the
/// `--filter=':- .gitignore'` directive.
const RSYNC_FORCE_INCLUDE: &[&str] = &[
    "provision/ansible/group_vars/all/model_config.yml",
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

    for pattern in RSYNC_FORCE_INCLUDE {
        args.push("--include".into());
        args.push((*pattern).into());
    }

    // Honour .gitignore (and nested .gitignore files) as exclude rules.
    // This is the dir-merge syntax: rsync reads each directory's
    // .gitignore and applies it as exclusions for that subtree.
    // Force-included files above take precedence.
    args.push("--filter=:- .gitignore".into());

    for pattern in RSYNC_EXTRA_EXCLUDES {
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

    let code = output.status.code().unwrap_or(1);
    if output.status.success() || code == 23 {
        // Exit code 23 = "partial transfer due to error" — typically
        // permission denied on a few files that couldn't be deleted.
        // The actual file transfer succeeded, so treat as OK.
        Ok(())
    } else {
        let stderr = String::from_utf8_lossy(&output.stderr);
        Err(eyre!("rsync failed (exit {:?}): {}", output.status.code(), stderr.trim()))
    }
}

/// Pull a single file from remote host to local path using rsync.
/// Creates local parent directories when needed.
pub fn pull_file(
    host: &str,
    user: &str,
    key_path: &str,
    remote_file: &str,
    local_file: &Path,
) -> Result<()> {
    if let Some(parent) = local_file.parent() {
        std::fs::create_dir_all(parent)?;
    }

    let mut args: Vec<String> = vec!["-az".into()];

    let key_expanded = shellexpand(key_path);
    if !key_expanded.is_empty() && Path::new(&key_expanded).exists() {
        args.push("-e".into());
        args.push(format!(
            "ssh -i {key_expanded} -o StrictHostKeyChecking=accept-new"
        ));
    }

    let src = format!("{user}@{host}:{remote_file}");
    let dest = local_file.to_string_lossy().to_string();
    args.push(src);
    args.push(dest);

    let output = Command::new("rsync").args(&args).output()?;
    if output.status.success() {
        Ok(())
    } else {
        let stderr = String::from_utf8_lossy(&output.stderr);
        Err(eyre!(
            "rsync pull failed (exit {:?}): {}",
            output.status.code(),
            stderr.trim()
        ))
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

/// Run a local make command with USE_MANAGER=0, streaming output line-by-line
/// via a callback instead of buffering until the process exits.
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
        .env("PYTHONUNBUFFERED", "1")
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

/// Run a local make command with vault password injected, streaming output line-by-line.
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
        .env("PYTHONUNBUFFERED", "1")
        .env("ANSIBLE_VAULT_PASSWORD", vault_password)
        .current_dir(repo_root)
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()?;

    let stdout = child.stdout.take().ok_or_else(|| eyre!("no stdout"))?;
    let stderr = child.stderr.take();

    let stderr_tx = {
        let (tx, rx) = std::sync::mpsc::channel::<String>();
        let handle = std::thread::spawn(move || {
            if let Some(se) = stderr {
                let reader = std::io::BufReader::new(se);
                for line in reader.lines().flatten() {
                    let _ = tx.send(line);
                }
            }
        });
        (rx, handle)
    };

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

    // Drain any stderr lines collected in the background
    for line in stderr_tx.0.try_iter() {
        let cleaned = strip_ansi(&line);
        let trimmed = cleaned.trim();
        if !trimmed.is_empty() {
            on_line(trimmed);
        }
    }
    let _ = stderr_tx.1.join();

    let status = child.wait()?;
    Ok(status.code().unwrap_or(1))
}

/// Execute a make command on the remote host, streaming output line-by-line via a callback.
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
         export PYTHONUNBUFFERED=1; \
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

/// Execute a make command on the remote host with vault password injected, streaming output line-by-line.
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
         export PYTHONUNBUFFERED=1; \
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
         export PYTHONUNBUFFERED=1; \
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

/// Clean up files on the remote that shouldn't exist when managed remotely:
/// - .busibox/ directory (local profile state — conflicts with remote management)
/// - .busibox-state-* files
/// - .busibox-vault-pass-* files
///
/// Vault files are NOT deleted: all remote profiles run Ansible on-host, so
/// the profile's vault YAML must remain. `sync_vault_file` pushes it before
/// this function runs.
pub fn cleanup_remote_state(
    ssh: &crate::modules::ssh::SshConnection,
    remote_path: &str,
) -> Result<String> {
    let cmd = format!(
        "cd {remote_path} && \
         removed=''; \
         if [ -d .busibox ]; then rm -rf .busibox && removed=\"$removed .busibox/\"; fi; \
         for f in .busibox-state-*; do [ -f \"$f\" ] && rm -f \"$f\" && removed=\"$removed $f\"; done; \
         for f in .busibox-vault-pass-*; do [ -f \"$f\" ] && rm -f \"$f\" && removed=\"$removed $f\"; done; \
         echo \"DONE:$removed\""
    );

    let output = ssh.run(&cmd)?;
    let cleaned = output.trim();
    if let Some(rest) = cleaned.strip_prefix("DONE:") {
        Ok(rest.trim().to_string())
    } else {
        Ok(cleaned.to_string())
    }
}

/// Strip legacy per-app keys (ai_portal, agent_manager, openai_api_key, site_domain)
/// from a local vault file. These keys contain unresolvable Jinja2 template references
/// that cause Ansible's include_vars to fail.
///
/// Requires `vault_password` to decrypt/re-encrypt. No-op if no legacy keys found.
/// Returns Ok(true) if keys were stripped, Ok(false) if vault was already clean.
///
/// Superseded by `validate_and_upgrade_vault()` which handles this and more.
#[allow(dead_code)]
pub fn clean_vault_legacy_keys(
    repo_root: &Path,
    vault_prefix: &str,
    vault_password: &str,
) -> Result<bool> {
    let script = repo_root.join("scripts/lib/test-vault-decrypt.sh");
    let vault_file = repo_root.join(format!(
        "provision/ansible/roles/secrets/vars/vault.{vault_prefix}.yml"
    ));
    if !vault_file.exists() || !script.exists() {
        return Ok(false);
    }

    let check = Command::new("bash")
        .arg(&script)
        .arg(&vault_file)
        .arg("--check-templates")
        .env("ANSIBLE_VAULT_PASSWORD", vault_password)
        .output()?;

    if check.status.success() {
        return Ok(false);
    }

    let strip = Command::new("bash")
        .arg(&script)
        .arg(&vault_file)
        .arg("--strip-legacy")
        .env("ANSIBLE_VAULT_PASSWORD", vault_password)
        .output()?;

    if strip.status.success() {
        Ok(true)
    } else {
        let stderr = String::from_utf8_lossy(&strip.stderr);
        Err(eyre!("Failed to strip legacy vault keys: {}", stderr.trim()))
    }
}

/// Push the profile's vault file to a remote host.
/// All remote profiles run Ansible on-host, so the vault YAML must be present.
/// Uses rsync to push a single file (bypassing .gitignore exclusions).
pub fn sync_vault_file(
    local_path: &Path,
    host: &str,
    user: &str,
    key_path: &str,
    remote_path: &str,
    vault_prefix: &str,
) -> Result<()> {
    let vault_rel = format!(
        "provision/ansible/roles/secrets/vars/vault.{vault_prefix}.yml"
    );
    let local_file = local_path.join(&vault_rel);
    if !local_file.exists() {
        return Err(eyre!(
            "Vault file not found: {}",
            local_file.display()
        ));
    }

    let key_expanded = shellexpand(key_path);
    let mut args: Vec<String> = vec!["-az".into()];
    if !key_expanded.is_empty() && Path::new(&key_expanded).exists() {
        args.push("-e".into());
        args.push(format!(
            "ssh -i {key_expanded} -o StrictHostKeyChecking=accept-new"
        ));
    }
    args.push(local_file.to_string_lossy().into_owned());
    args.push(format!("{user}@{host}:{remote_path}/{vault_rel}"));

    let output = Command::new("rsync").args(&args).output()?;
    if output.status.success() {
        Ok(())
    } else {
        let stderr = String::from_utf8_lossy(&output.stderr);
        Err(eyre!(
            "vault rsync failed (exit {:?}): {}",
            output.status.code(),
            stderr.trim()
        ))
    }
}

/// Known insecure/placeholder defaults that indicate a vault value hasn't been
/// configured for production. Mirrors the blocklist in
/// provision/ansible/roles/validate_env/tasks/docker.yml.
const INSECURE_DEFAULTS: &[&str] = &[
    "devpassword",
    "minioadmin",
    "sk-local-dev-key",
    "local-master-key-change-in-production",
    "dev-encryption-key",
    "dev-sso-secret",
    "dev-master-key-change-me",
    "dev-jwt-secret-change-me",
    "dev-session-secret-change-me",
    "sk-dev-litellm-key",
    "default-service-secret-change-in-production",
    "default-jwt-secret",
    "sk-litellm-master-key-change-me",
];

/// Required keys in the decrypted vault YAML that must have real values.
/// Uses dot-notation matching against the YAML structure under `secrets:`.
pub const REQUIRED_VAULT_KEYS: &[&str] = &[
    "postgresql.password",
    "minio.root_user",
    "minio.root_password",
    "neo4j.password",
    "authz_master_key",
    "jwt_secret",
    "session_secret",
    "litellm_master_key",
    "litellm_salt_key",
];

/// Result of validating the local vault file (and optionally comparing with remote).
#[allow(dead_code)]
pub struct VaultValidation {
    pub file_exists: bool,
    pub can_decrypt: bool,
    pub bad_values: Vec<String>,
    /// None = not checked, Some(true) = matches, Some(false) = mismatch or missing
    pub remote_in_sync: Option<bool>,
    pub summary: String,
}

/// Validate that the local vault file exists, can be decrypted, and has no
/// placeholder/insecure values for required keys.
///
/// When `ssh` is provided (remote profiles), also compares the local file's
/// SHA-256 against the remote copy to detect drift.
#[allow(dead_code)]
pub fn validate_vault_secrets(
    repo_root: &Path,
    vault_prefix: &str,
    vault_password: &str,
    ssh: Option<(&crate::modules::ssh::SshConnection, &str)>,
) -> Result<VaultValidation> {
    let vault_rel = format!(
        "provision/ansible/roles/secrets/vars/vault.{vault_prefix}.yml"
    );
    let local_vault = repo_root.join(&vault_rel);
    let file_exists = local_vault.exists();

    if !file_exists {
        return Ok(VaultValidation {
            file_exists: false,
            can_decrypt: false,
            bad_values: vec![],
            remote_in_sync: None,
            summary: format!("vault.{vault_prefix}.yml: file not found"),
        });
    }

    // Write a temporary vault-password-file script
    let tmp_pw = std::env::temp_dir().join(format!("busibox-vault-val-{}", std::process::id()));
    std::fs::write(&tmp_pw, format!("#!/bin/sh\necho '{}'", vault_password.replace('\'', "'\\''")))?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(&tmp_pw, std::fs::Permissions::from_mode(0o700))?;
    }

    let output = Command::new("ansible-vault")
        .args(["view", &local_vault.to_string_lossy(), "--vault-password-file", &tmp_pw.to_string_lossy()])
        .output();

    let _ = std::fs::remove_file(&tmp_pw);

    let output = match output {
        Ok(o) => o,
        Err(e) => {
            return Ok(VaultValidation {
                file_exists: true,
                can_decrypt: false,
                bad_values: vec![],
                remote_in_sync: None,
                summary: format!("vault.{vault_prefix}.yml: ansible-vault not found: {e}"),
            });
        }
    };

    if !output.status.success() {
        return Ok(VaultValidation {
            file_exists: true,
            can_decrypt: false,
            bad_values: vec![],
            remote_in_sync: None,
            summary: format!("vault.{vault_prefix}.yml: decryption failed (wrong password?)"),
        });
    }

    let content = String::from_utf8_lossy(&output.stdout);
    let bad_values = check_vault_content_for_placeholders(&content);

    // Compare local vs remote vault file via SHA-256
    let remote_in_sync = if let Some((ssh_conn, remote_path)) = ssh {
        Some(compare_vault_hash(repo_root, ssh_conn, remote_path, &vault_rel))
    } else {
        None
    };

    let mut parts: Vec<String> = Vec::new();
    parts.push(format!("vault.{vault_prefix}.yml: ✓ decrypted OK"));
    if !bad_values.is_empty() {
        parts.push(format!(
            "✗ {} value(s) need attention: {}",
            bad_values.len(),
            bad_values.join(", ")
        ));
    }
    match remote_in_sync {
        Some(true) => parts.push("✓ remote in sync".into()),
        Some(false) => parts.push("✗ remote OUT OF SYNC (run Sync first)".into()),
        None => {}
    }

    let summary = parts.join(" | ");

    Ok(VaultValidation {
        file_exists: true,
        can_decrypt: true,
        bad_values,
        remote_in_sync,
        summary,
    })
}

/// Compare SHA-256 of a local file against the same file on the remote.
/// Returns `true` if they match, `false` if mismatch or remote file missing.
#[allow(dead_code)]
fn compare_vault_hash(
    repo_root: &Path,
    ssh: &crate::modules::ssh::SshConnection,
    remote_path: &str,
    vault_rel: &str,
) -> bool {
    use std::io::Read;

    // Local SHA-256
    let local_file = repo_root.join(vault_rel);
    let local_hash = match std::fs::File::open(&local_file) {
        Ok(mut f) => {
            let mut buf = Vec::new();
            if f.read_to_end(&mut buf).is_err() {
                return false;
            }
            sha256_hex(&buf)
        }
        Err(_) => return false,
    };

    // Remote SHA-256
    let cmd = format!(
        "sha256sum {remote_path}/{vault_rel} 2>/dev/null | awk '{{print $1}}'"
    );
    match ssh.run(&cmd) {
        Ok(output) => {
            let remote_hash = output.trim().to_string();
            !remote_hash.is_empty() && remote_hash == local_hash
        }
        Err(_) => false,
    }
}

#[allow(dead_code)]
fn sha256_hex(data: &[u8]) -> String {
    // Shell out to shasum (macOS) or sha256sum (Linux) to avoid adding a crypto dep
    let mut child = match Command::new("shasum")
        .args(["-a", "256"])
        .stdin(std::process::Stdio::piped())
        .stdout(std::process::Stdio::piped())
        .spawn()
    {
        Ok(c) => c,
        Err(_) => {
            // Fallback: try sha256sum (Linux)
            match Command::new("sha256sum")
                .stdin(std::process::Stdio::piped())
                .stdout(std::process::Stdio::piped())
                .spawn()
            {
                Ok(c) => c,
                Err(_) => return String::new(),
            }
        }
    };
    if let Some(ref mut stdin) = child.stdin {
        use std::io::Write as IoWrite;
        let _ = stdin.write_all(data);
    }
    drop(child.stdin.take());
    match child.wait_with_output() {
        Ok(out) => {
            let s = String::from_utf8_lossy(&out.stdout);
            s.split_whitespace().next().unwrap_or("").to_string()
        }
        Err(_) => String::new(),
    }
}

/// Scan decrypted vault YAML content for placeholder/insecure values.
/// Returns a list of key paths that have problems.
#[allow(dead_code)]
fn check_vault_content_for_placeholders(content: &str) -> Vec<String> {
    let mut bad = Vec::new();

    // Build a flat key→value map from the YAML by tracking indentation.
    // The vault YAML is nested under a top-level `secrets:` key, e.g.:
    //   secrets:
    //     postgresql:
    //       password: devpassword
    // We flatten to "postgresql.password" → "devpassword".
    let mut path_stack: Vec<(usize, String)> = Vec::new();
    let mut in_secrets = false;
    let secrets_indent;

    // Find the `secrets:` top-level key
    let mut lines = content.lines().peekable();
    let mut si = 0usize;
    while let Some(line) = lines.peek() {
        let trimmed = line.trim();
        if trimmed == "secrets:" || trimmed.starts_with("secrets:") {
            si = line.len() - line.trim_start().len();
            in_secrets = true;
            lines.next();
            break;
        }
        lines.next();
    }
    secrets_indent = si;

    if !in_secrets {
        bad.push("secrets (top-level key missing from vault)".to_string());
        return bad;
    }

    for line in lines {
        let stripped = line.trim_start();
        if stripped.is_empty() || stripped.starts_with('#') {
            continue;
        }
        let indent = line.len() - stripped.len();
        // If we've un-indented back to or past the secrets level, we're done
        if indent <= secrets_indent && !stripped.is_empty() {
            break;
        }

        if let Some(colon_pos) = stripped.find(':') {
            let key = stripped[..colon_pos].trim();
            let value_part = stripped[colon_pos + 1..].trim();

            // Pop stack entries at same or deeper indent
            while let Some((last_indent, _)) = path_stack.last() {
                if *last_indent >= indent {
                    path_stack.pop();
                } else {
                    break;
                }
            }

            if value_part.is_empty() {
                // This is a parent key (e.g. "postgresql:")
                path_stack.push((indent, key.to_string()));
            } else {
                // This is a leaf key with a value
                let mut full_key = String::new();
                for (_, part) in &path_stack {
                    full_key.push_str(part);
                    full_key.push('.');
                }
                full_key.push_str(key);

                // Check if this is a required key
                if REQUIRED_VAULT_KEYS.iter().any(|rk| *rk == full_key) {
                    let val = value_part.trim_matches(|c| c == '\'' || c == '"');
                    if val.is_empty()
                        || val == "null"
                        || val == "~"
                        || val == "None"
                        || val.contains("CHANGE_ME")
                        || INSECURE_DEFAULTS.iter().any(|d| *d == val)
                    {
                        bad.push(full_key);
                    }
                }
            }
        }
    }

    // Also flag any required keys that weren't found at all
    for rk in REQUIRED_VAULT_KEYS {
        if !bad.contains(&rk.to_string()) {
            // Check if the key was present (found and OK) by re-scanning
            // If it wasn't in `bad` and also wasn't found, it's missing
            let found = content.lines().any(|line| {
                let t = line.trim();
                if let Some(cp) = t.find(':') {
                    let k = t[..cp].trim();
                    let short_key = rk.rsplit('.').next().unwrap_or(rk);
                    k == short_key && !t[cp + 1..].trim().is_empty()
                } else {
                    false
                }
            });
            if !found {
                bad.push(format!("{} (missing)", rk));
            }
        }
    }

    bad
}

/// Outcome of the vault upgrade check.
#[derive(Debug, Clone)]
pub enum VaultUpgradeResult {
    /// Vault was already clean — no changes needed.
    Clean,
    /// Vault was auto-upgraded. Contains a human-readable summary of changes.
    Upgraded {
        added: Vec<String>,
        removed: Vec<String>,
        copied: Vec<String>,
    },
    /// Vault was created from scratch (no prior vault file).
    Created {
        added: Vec<String>,
    },
    /// There are issues that cannot be auto-resolved.
    Issues {
        message: String,
    },
}

/// Validate and auto-upgrade the local vault file against the canonical
/// `vault.example.yml` schema. This is the single entry point for all
/// vault maintenance — called at profile unlock time.
///
/// Behaviour:
/// - If no vault file exists, creates one from `vault.example.yml` with random values.
/// - Strips legacy keys (ai_portal, agent_manager, openai_api_key, site_domain).
/// - Adds missing keys with random values (pattern-matched from placeholders).
/// - Copies fallback values (e.g. litellm_master_key → litellm_salt_key).
/// - Re-encrypts the vault if changes were made.
pub fn validate_and_upgrade_vault(
    repo_root: &Path,
    vault_prefix: &str,
    vault_password: &str,
) -> Result<VaultUpgradeResult> {
    let script = repo_root.join("scripts/lib/test-vault-decrypt.sh");
    if !script.exists() {
        return Err(eyre!(
            "test-vault-decrypt.sh not found at {}",
            script.display()
        ));
    }

    let vault_file = repo_root.join(format!(
        "provision/ansible/roles/secrets/vars/vault.{vault_prefix}.yml"
    ));
    let example_file = repo_root.join(
        "provision/ansible/roles/secrets/vars/vault.example.yml"
    );
    if !example_file.exists() {
        return Err(eyre!(
            "vault.example.yml not found at {}",
            example_file.display()
        ));
    }

    let output = Command::new("bash")
        .arg(&script)
        .arg(&vault_file)
        .arg("--upgrade")
        .arg(&example_file)
        .env("ANSIBLE_VAULT_PASSWORD", vault_password)
        .output()?;

    let stdout = String::from_utf8_lossy(&output.stdout);
    let stderr = String::from_utf8_lossy(&output.stderr);

    // The script outputs JSON on stdout
    let json_line = stdout.lines()
        .find(|l| l.starts_with('{'))
        .unwrap_or("");

    if json_line.is_empty() {
        return Err(eyre!(
            "Vault upgrade script produced no JSON output.\nstdout: {}\nstderr: {}",
            stdout.trim(),
            stderr.trim()
        ));
    }

    let parsed: serde_json::Value = serde_json::from_str(json_line)
        .map_err(|e| eyre!("Failed to parse upgrade result JSON: {e}\nraw: {json_line}"))?;

    let status = parsed["status"].as_str().unwrap_or("error");

    match status {
        "clean" => Ok(VaultUpgradeResult::Clean),
        "created" => {
            let added = json_str_vec(&parsed["added"]);
            Ok(VaultUpgradeResult::Created { added })
        }
        "upgraded" => {
            let added = json_str_vec(&parsed["added"]);
            let removed = json_str_vec(&parsed["removed"]);
            let copied = json_str_vec(&parsed["copied"]);
            Ok(VaultUpgradeResult::Upgraded { added, removed, copied })
        }
        "error" => {
            let msg = parsed["message"]
                .as_str()
                .unwrap_or("unknown error")
                .to_string();
            Ok(VaultUpgradeResult::Issues { message: msg })
        }
        _ => {
            let issues = &parsed["issues"];
            if issues.is_array() && !issues.as_array().unwrap().is_empty() {
                let problems: Vec<String> = issues
                    .as_array()
                    .unwrap()
                    .iter()
                    .map(|i| {
                        format!(
                            "{}: {}",
                            i["key"].as_str().unwrap_or("?"),
                            i["reason"].as_str().unwrap_or("?")
                        )
                    })
                    .collect();
                Ok(VaultUpgradeResult::Issues {
                    message: problems.join(", "),
                })
            } else {
                Ok(VaultUpgradeResult::Clean)
            }
        }
    }
}

/// Extract a Vec<String> from a serde_json array value.
fn json_str_vec(val: &serde_json::Value) -> Vec<String> {
    val.as_array()
        .map(|arr| {
            arr.iter()
                .filter_map(|v| v.as_str().map(String::from))
                .collect()
        })
        .unwrap_or_default()
}

fn shellexpand(path: &str) -> String {
    if path.starts_with("~/") {
        if let Some(home) = dirs::home_dir() {
            return format!("{}{}", home.display(), &path[1..]);
        }
    }
    path.to_string()
}

// ---------------------------------------------------------------------------
// Structured vault key inspection (used by the ValidateSecrets screen)
// ---------------------------------------------------------------------------

/// Status of a single secret key in a vault file.
#[derive(Debug, Clone, PartialEq)]
#[allow(dead_code)]
pub enum KeyState {
    Ok,
    Missing,
    Placeholder,
    InsecureDefault,
    NullOrEmpty,
    NotChecked,
    Pending,
}

impl KeyState {
    pub fn is_bad(&self) -> bool {
        !matches!(self, KeyState::Ok | KeyState::NotChecked | KeyState::Pending)
    }
}

/// Per-key validation result with local and optional remote status.
#[derive(Debug, Clone)]
pub struct SecretKeyStatus {
    pub key_path: String,
    pub required: bool,
    pub local: KeyState,
    pub remote: KeyState,
}

/// Classify a single trimmed, unquoted value string.
fn classify_value(val: &str) -> KeyState {
    if val.is_empty() || val == "null" || val == "~" || val == "None" || val == "''" || val == "\"\"" {
        return KeyState::NullOrEmpty;
    }
    if val.contains("CHANGE_ME") {
        return KeyState::Placeholder;
    }
    if INSECURE_DEFAULTS.iter().any(|d| *d == val) {
        return KeyState::InsecureDefault;
    }
    KeyState::Ok
}

/// Parse decrypted vault YAML and return per-key status.
///
/// Returns a tuple of (all_keys_found, required_keys_status):
/// - `all_keys_found`: every leaf key under `secrets:` with its dot-path
/// - The returned vec covers REQUIRED_VAULT_KEYS first (in order), then any
///   additional keys found in the vault.
pub fn parse_vault_content(content: &str) -> Vec<(String, KeyState)> {
    let mut found_keys: Vec<(String, KeyState)> = Vec::new();
    let mut path_stack: Vec<(usize, String)> = Vec::new();
    let mut in_secrets = false;
    let secrets_indent;

    let mut lines = content.lines().peekable();
    let mut si = 0usize;
    while let Some(line) = lines.peek() {
        let trimmed = line.trim();
        if trimmed == "secrets:" || trimmed.starts_with("secrets:") {
            si = line.len() - line.trim_start().len();
            in_secrets = true;
            lines.next();
            break;
        }
        lines.next();
    }
    secrets_indent = si;

    if !in_secrets {
        // No secrets block at all -- every required key is missing
        for rk in REQUIRED_VAULT_KEYS {
            found_keys.push((rk.to_string(), KeyState::Missing));
        }
        return found_keys;
    }

    let mut seen_paths: Vec<(String, KeyState)> = Vec::new();

    for line in lines {
        let stripped = line.trim_start();
        if stripped.is_empty() || stripped.starts_with('#') {
            continue;
        }
        let indent = line.len() - stripped.len();
        if indent <= secrets_indent && !stripped.is_empty() {
            break;
        }

        if let Some(colon_pos) = stripped.find(':') {
            let key = stripped[..colon_pos].trim();
            let value_part = stripped[colon_pos + 1..].trim();

            while let Some((last_indent, _)) = path_stack.last() {
                if *last_indent >= indent {
                    path_stack.pop();
                } else {
                    break;
                }
            }

            if value_part.is_empty() {
                path_stack.push((indent, key.to_string()));
            } else {
                let mut full_key = String::new();
                for (_, part) in &path_stack {
                    full_key.push_str(part);
                    full_key.push('.');
                }
                full_key.push_str(key);

                let val = value_part.trim_matches(|c| c == '\'' || c == '"');
                let state = classify_value(val);
                seen_paths.push((full_key, state));
            }
        }
    }

    // Build result: required keys first (in order), then extras
    let mut seen_set: std::collections::HashSet<String> = std::collections::HashSet::new();

    for rk in REQUIRED_VAULT_KEYS {
        if let Some(entry) = seen_paths.iter().find(|(k, _)| k == rk) {
            found_keys.push(entry.clone());
        } else {
            found_keys.push((rk.to_string(), KeyState::Missing));
        }
        seen_set.insert(rk.to_string());
    }

    for entry in &seen_paths {
        if !seen_set.contains(&entry.0) {
            found_keys.push(entry.clone());
            seen_set.insert(entry.0.clone());
        }
    }

    found_keys
}

/// Decrypt a vault file on a remote host via SSH and return the plaintext.
pub fn decrypt_remote_vault(
    ssh: &crate::modules::ssh::SshConnection,
    remote_path: &str,
    vault_prefix: &str,
    vault_password: &str,
) -> Result<String> {
    let vault_rel = format!(
        "provision/ansible/roles/secrets/vars/vault.{vault_prefix}.yml"
    );
    let escaped_pw = vault_password.replace('\'', "'\\''");
    let cmd = format!(
        "{preamble} cd {remote_path} && \
         TMP=$(mktemp) && \
         printf '#!/bin/sh\\necho '\"'\"'{escaped_pw}'\"'\"'\\n' > \"$TMP\" && \
         chmod 700 \"$TMP\" && \
         ansible-vault view {vault_rel} --vault-password-file \"$TMP\" 2>&1; \
         RC=$?; rm -f \"$TMP\"; exit $RC",
        preamble = SHELL_PATH_PREAMBLE,
    );
    ssh.run(&cmd)
}

/// Decrypt the local vault file and return the plaintext.
pub fn decrypt_local_vault(
    repo_root: &Path,
    vault_prefix: &str,
    vault_password: &str,
) -> Result<Option<String>> {
    let vault_rel = format!(
        "provision/ansible/roles/secrets/vars/vault.{vault_prefix}.yml"
    );
    let local_vault = repo_root.join(&vault_rel);
    if !local_vault.exists() {
        return Ok(None);
    }

    let tmp_pw = std::env::temp_dir().join(format!("busibox-vault-val-{}", std::process::id()));
    std::fs::write(&tmp_pw, format!("#!/bin/sh\necho '{}'", vault_password.replace('\'', "'\\''")))?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(&tmp_pw, std::fs::Permissions::from_mode(0o700))?;
    }

    let output = Command::new("ansible-vault")
        .args(["view", &local_vault.to_string_lossy(), "--vault-password-file", &tmp_pw.to_string_lossy()])
        .output();

    let _ = std::fs::remove_file(&tmp_pw);

    let output = output.map_err(|e| eyre!("ansible-vault not found: {e}"))?;

    if !output.status.success() {
        return Err(eyre!("decryption failed (wrong password?)"));
    }

    Ok(Some(String::from_utf8_lossy(&output.stdout).to_string()))
}
