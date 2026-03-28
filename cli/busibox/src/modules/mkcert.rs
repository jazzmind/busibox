use color_eyre::{eyre::eyre, Result};
use std::path::Path;
use std::process::Command;

/// Check if mkcert is installed and available in PATH.
pub fn is_installed() -> bool {
    which::which("mkcert").is_ok()
}

/// Install mkcert using the platform package manager.
/// Returns Ok(()) on success, Err if installation fails.
pub fn install() -> Result<()> {
    if is_installed() {
        return Ok(());
    }

    #[cfg(target_os = "macos")]
    {
        let status = Command::new("brew")
            .args(["install", "mkcert", "nss"])
            .status()
            .map_err(|e| eyre!("Failed to run brew: {e}. Is Homebrew installed?"))?;
        if !status.success() {
            return Err(eyre!("brew install mkcert failed (exit {:?})", status.code()));
        }
    }

    #[cfg(target_os = "linux")]
    {
        // Try apt first (Debian/Ubuntu), then dnf (Fedora), then snap
        let apt = Command::new("apt-get")
            .args(["install", "-y", "mkcert"])
            .status();
        let succeeded = apt.map(|s| s.success()).unwrap_or(false);
        if !succeeded {
            let dnf = Command::new("dnf")
                .args(["install", "-y", "mkcert"])
                .status();
            let succeeded = dnf.map(|s| s.success()).unwrap_or(false);
            if !succeeded {
                let snap = Command::new("snap")
                    .args(["install", "mkcert"])
                    .status();
                if !snap.map(|s| s.success()).unwrap_or(false) {
                    return Err(eyre!(
                        "Could not install mkcert. Install it manually: https://github.com/FiloSottile/mkcert#installation"
                    ));
                }
            }
        }
    }

    if !is_installed() {
        return Err(eyre!("mkcert was not found in PATH after installation"));
    }
    Ok(())
}

/// Install the mkcert root CA into the system trust store.
/// This only needs to be done once per machine.
pub fn install_ca() -> Result<()> {
    let output = Command::new("mkcert")
        .arg("-install")
        .output()
        .map_err(|e| eyre!("Failed to run mkcert -install: {e}"))?;
    if !output.status.success() {
        let stderr = String::from_utf8_lossy(&output.stderr);
        return Err(eyre!("mkcert -install failed: {}", stderr.trim()));
    }
    Ok(())
}

/// Check if the mkcert CA is already installed in the system trust store.
pub fn is_ca_installed() -> bool {
    let output = Command::new("mkcert")
        .arg("-CAROOT")
        .output();
    match output {
        Ok(o) if o.status.success() => {
            let root = String::from_utf8_lossy(&o.stdout);
            let ca_cert = Path::new(root.trim()).join("rootCA.pem");
            ca_cert.exists()
        }
        _ => false,
    }
}

/// Generate TLS certificates for the given domains.
/// Writes `{output_name}.crt` and `{output_name}.key` into `output_dir`.
pub fn generate_certs(
    domains: &[&str],
    output_dir: &Path,
    output_name: &str,
) -> Result<()> {
    if domains.is_empty() {
        return Err(eyre!("No domains specified for certificate generation"));
    }

    std::fs::create_dir_all(output_dir)?;

    let cert_file = output_dir.join(format!("{output_name}.crt"));
    let key_file = output_dir.join(format!("{output_name}.key"));

    let mut args: Vec<String> = vec![
        "-cert-file".into(),
        cert_file.to_string_lossy().into_owned(),
        "-key-file".into(),
        key_file.to_string_lossy().into_owned(),
    ];
    for d in domains {
        args.push((*d).to_string());
    }

    let output = Command::new("mkcert")
        .args(&args)
        .output()
        .map_err(|e| eyre!("Failed to run mkcert: {e}"))?;

    if !output.status.success() {
        let stderr = String::from_utf8_lossy(&output.stderr);
        return Err(eyre!("mkcert failed: {}", stderr.trim()));
    }

    if !cert_file.exists() || !key_file.exists() {
        return Err(eyre!(
            "mkcert succeeded but cert/key files not found at {}",
            output_dir.display()
        ));
    }
    Ok(())
}

/// Set up mkcert end-to-end: install if needed, install CA, generate certs.
/// Returns the path to the generated cert file on success.
pub fn ensure_certs(
    domains: &[&str],
    output_dir: &Path,
    output_name: &str,
) -> Result<std::path::PathBuf> {
    if !is_installed() {
        install()?;
    }
    if !is_ca_installed() {
        install_ca()?;
    }
    generate_certs(domains, output_dir, output_name)?;
    Ok(output_dir.join(format!("{output_name}.crt")))
}
