use crate::modules::hardware::{HardwareProfile, MemoryTier};
use color_eyre::{eyre::eyre, Result};
use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::path::{Path, PathBuf};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ProfilesFile {
    pub active: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub defaults: Option<ProfileDefaults>,
    pub profiles: HashMap<String, Profile>,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct ProfileDefaults {
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub admin_email: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub huggingface_token: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub frontend_ref: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub remote_user: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Profile {
    pub environment: String,
    pub backend: String,
    pub label: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub created: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub vault_prefix: Option<String>,
    #[serde(default)]
    pub remote: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub remote_host: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub remote_user: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub remote_ssh_key: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub remote_busibox_path: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub tailscale_ip: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub hardware: Option<HardwareProfile>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub kubeconfig: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub model_tier: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub admin_email: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub allowed_email_domains: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub frontend_ref: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub site_domain: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub ssl_cert_name: Option<String>,
    /// First three octets of the container network (e.g. "10.96.200").
    /// Defaults based on environment: "10.96.200" for production, "10.96.201" for staging.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub network_base_octets: Option<String>,
    /// When true (staging default), vLLM health checks target the production network
    /// instead of this profile's network, since staging shares production GPUs.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub use_production_vllm: Option<bool>,
    /// Docker runtime preference: "auto", "docker-desktop", or "colima".
    /// Only relevant when backend == "docker" on macOS.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub docker_runtime: Option<String>,
    /// GitHub Personal Access Token for private repo access during deployment.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub github_token: Option<String>,
}

impl Profile {
    pub fn effective_host(&self) -> Option<&str> {
        self.tailscale_ip
            .as_deref()
            .or(self.remote_host.as_deref())
    }

    #[allow(dead_code)]
    pub fn effective_user(&self) -> &str {
        self.remote_user.as_deref().unwrap_or("root")
    }

    #[allow(dead_code)]
    pub fn effective_ssh_key(&self) -> &str {
        self.remote_ssh_key.as_deref().unwrap_or("~/.ssh/id_ed25519")
    }

    pub fn effective_remote_path(&self) -> &str {
        self.remote_busibox_path
            .as_deref()
            .unwrap_or("~/busibox")
    }

    pub fn effective_model_tier(&self) -> Option<MemoryTier> {
        self.model_tier
            .as_deref()
            .and_then(MemoryTier::from_name)
            .or_else(|| self.hardware.as_ref().map(|h| h.memory_tier))
    }

    pub fn effective_network_base(&self) -> &str {
        if let Some(ref base) = self.network_base_octets {
            base.as_str()
        } else if self.environment == "staging" {
            "10.96.201"
        } else {
            "10.96.200"
        }
    }

    pub fn effective_use_production_vllm(&self) -> bool {
        self.use_production_vllm
            .unwrap_or_else(|| self.environment == "staging")
    }

    pub fn effective_docker_runtime(&self) -> &str {
        self.docker_runtime.as_deref().unwrap_or("auto")
    }

    /// Network base to use for vLLM health checks. When use_production_vllm is true,
    /// returns the production network base (defaulting to "10.96.200").
    pub fn vllm_network_base(&self) -> &str {
        if self.effective_use_production_vllm() {
            "10.96.200"
        } else {
            self.effective_network_base()
        }
    }
}

/// Get the path to .busibox/profiles.json relative to a repo root.
pub fn profiles_path(repo_root: &Path) -> PathBuf {
    repo_root.join(".busibox").join("profiles.json")
}

/// Load profiles from disk. Auto-migrates old-style profile IDs on first load.
pub fn load_profiles(repo_root: &Path) -> Result<ProfilesFile> {
    let path = profiles_path(repo_root);
    if !path.exists() {
        return Ok(ProfilesFile {
            active: String::new(),
            defaults: None,
            profiles: HashMap::new(),
        });
    }
    let contents = std::fs::read_to_string(&path)?;
    let mut profiles: ProfilesFile = serde_json::from_str(&contents)?;
    migrate_profile_ids(repo_root, &mut profiles);
    Ok(profiles)
}

/// Save profiles to disk.
pub fn save_profiles(repo_root: &Path, profiles: &ProfilesFile) -> Result<()> {
    let path = profiles_path(repo_root);
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)?;
    }
    let json = serde_json::to_string_pretty(profiles)?;
    std::fs::write(&path, format!("{json}\n"))?;
    Ok(())
}

/// Get the active profile, if any.
pub fn get_active_profile(profiles: &ProfilesFile) -> Option<(&str, &Profile)> {
    if profiles.active.is_empty() {
        return None;
    }
    profiles
        .profiles
        .get(&profiles.active)
        .map(|p| (profiles.active.as_str(), p))
}

/// Create or update a profile.
/// Also creates the state directory and writes profile state so bash make scripts can find it.
pub fn upsert_profile(
    repo_root: &Path,
    id: &str,
    profile: Profile,
    set_active: bool,
) -> Result<()> {
    let mut profiles = load_profiles(repo_root)?;
    profiles.profiles.insert(id.to_string(), profile.clone());
    if set_active {
        profiles.active = id.to_string();
    }
    save_profiles(repo_root, &profiles)?;
    ensure_profile_state_dir(repo_root, id)?;
    let _ = write_profile_state(repo_root, id, &profile);
    Ok(())
}

/// Delete a profile by ID. If the deleted profile was active, clears the active field.
/// Also removes the profile's state directory from disk.
pub fn delete_profile(repo_root: &Path, id: &str) -> Result<()> {
    let mut profiles = load_profiles(repo_root)?;
    if !profiles.profiles.contains_key(id) {
        return Err(eyre!("Profile '{id}' not found"));
    }
    profiles.profiles.remove(id);
    if profiles.active == id {
        profiles.active = profiles
            .profiles
            .keys()
            .next()
            .cloned()
            .unwrap_or_default();
    }
    save_profiles(repo_root, &profiles)?;

    let profile_dir = repo_root.join(".busibox").join("profiles").join(id);
    if profile_dir.exists() {
        let _ = std::fs::remove_dir_all(&profile_dir);
    }

    Ok(())
}

/// Find the busibox repo root by walking up from the current directory.
pub fn find_repo_root() -> Result<PathBuf> {
    let mut dir = std::env::current_dir()?;
    loop {
        if dir.join("Makefile").exists() && dir.join("scripts").exists() {
            return Ok(dir);
        }
        if !dir.pop() {
            break;
        }
    }
    Err(eyre!(
        "Could not find busibox repo root (no Makefile + scripts/ found)"
    ))
}

/// Create the profile state directory and initial state file on disk.
pub fn ensure_profile_state_dir(repo_root: &Path, profile_id: &str) -> Result<PathBuf> {
    let profile_dir = repo_root
        .join(".busibox")
        .join("profiles")
        .join(profile_id);
    std::fs::create_dir_all(&profile_dir)?;

    let state_file = profile_dir.join("state");
    if !state_file.exists() {
        std::fs::write(&state_file, "# Busibox State File\nINSTALL_STATUS=not_installed\n")?;
    }
    Ok(profile_dir)
}

/// Write profile-derived values (admin email, model tier) into the state file
/// so that bash scripts (make, service-deploy) can read them.
pub fn write_profile_state(repo_root: &Path, profile_id: &str, profile: &Profile) -> Result<()> {
    let profile_dir = ensure_profile_state_dir(repo_root, profile_id)?;
    let state_file = profile_dir.join("state");

    let existing = std::fs::read_to_string(&state_file).unwrap_or_default();

    let mut lines: Vec<String> = existing
        .lines()
        .filter(|l| {
            !l.starts_with("ADMIN_EMAIL=")
                && !l.starts_with("ALLOWED_DOMAINS=")
                && !l.starts_with("MODEL_TIER=")
                && !l.starts_with("LLM_BACKEND=")
                && !l.starts_with("SITE_DOMAIN=")
                && !l.starts_with("SSL_CERT_NAME=")
                && !l.starts_with("DOCKER_RUNTIME=")
        })
        .map(|l| l.to_string())
        .collect();

    if let Some(ref email) = profile.admin_email {
        lines.push(format!("ADMIN_EMAIL={email}"));
    }
    if let Some(ref domains) = profile.allowed_email_domains {
        lines.push(format!("ALLOWED_DOMAINS={domains}"));
    }
    if let Some(tier) = profile.effective_model_tier() {
        lines.push(format!("MODEL_TIER={}", tier.name()));
    }
    if let Some(ref hw) = profile.hardware {
        lines.push(format!("LLM_BACKEND={}", match hw.llm_backend {
            crate::modules::hardware::LlmBackend::Mlx => "mlx",
            crate::modules::hardware::LlmBackend::Vllm => "vllm",
            crate::modules::hardware::LlmBackend::Cloud => "cloud",
        }));
    }
    if let Some(ref domain) = profile.site_domain {
        lines.push(format!("SITE_DOMAIN={domain}"));
    }
    if let Some(ref cert_name) = profile.ssl_cert_name {
        lines.push(format!("SSL_CERT_NAME={cert_name}"));
    }
    if let Some(ref rt) = profile.docker_runtime {
        lines.push(format!("DOCKER_RUNTIME={rt}"));
    }

    let content = lines.join("\n") + "\n";
    std::fs::write(&state_file, content)?;
    Ok(())
}

/// Sanitize a hostname/IP for use in a profile ID.
/// Lowercases, replaces non-alphanumeric chars with hyphens, collapses runs,
/// and trims leading/trailing hyphens.
pub fn sanitize_host(host: &str) -> String {
    let lowered: String = host
        .to_lowercase()
        .chars()
        .map(|c| if c.is_ascii_alphanumeric() || c == '-' { c } else { '-' })
        .collect();
    let mut result = String::new();
    let mut prev_hyphen = true; // trim leading hyphens
    for c in lowered.chars() {
        if c == '-' {
            if !prev_hyphen {
                result.push('-');
            }
            prev_hyphen = true;
        } else {
            result.push(c);
            prev_hyphen = false;
        }
    }
    result.trim_end_matches('-').to_string()
}

/// Build a profile ID from host, environment, and backend.
/// Local profiles use "local" as the host prefix.
/// Remote profiles use the sanitized hostname/IP.
pub fn build_profile_id(host: &str, environment: &str, backend: &str) -> String {
    let prefix = sanitize_host(host);
    format!("{prefix}-{environment}-{backend}")
}

const OLD_STYLE_ENVIRONMENTS: &[&str] = &["production", "staging", "development"];
const OLD_STYLE_BACKENDS: &[&str] = &["docker", "proxmox"];

/// Check if a profile ID uses the old `{environment}-{backend}` naming scheme.
fn is_old_style_id(id: &str) -> bool {
    for env in OLD_STYLE_ENVIRONMENTS {
        for backend in OLD_STYLE_BACKENDS {
            if id == format!("{env}-{backend}") {
                return true;
            }
        }
    }
    false
}

/// Compute the new-style profile ID for a profile.
fn compute_new_profile_id(profile: &Profile) -> String {
    let host = if profile.remote {
        profile
            .remote_host
            .as_deref()
            .unwrap_or("remote")
    } else {
        "local"
    };
    build_profile_id(host, &profile.environment, &profile.backend)
}

/// Migrate old-style profile IDs (`{env}-{backend}`) to new host-prefixed IDs.
/// Renames the HashMap entry, vault key file, and state directory.
/// Returns true if any migrations were performed.
pub fn migrate_profile_ids(repo_root: &Path, profiles: &mut ProfilesFile) -> bool {
    let mut renames: Vec<(String, String)> = Vec::new();

    for (id, profile) in &profiles.profiles {
        if !is_old_style_id(id) {
            continue;
        }
        let new_id = compute_new_profile_id(profile);
        if *id == new_id {
            continue;
        }
        if profiles.profiles.contains_key(&new_id) {
            eprintln!(
                "[profile migration] Skipping '{id}' -> '{new_id}': target ID already exists"
            );
            continue;
        }
        renames.push((id.clone(), new_id));
    }

    if renames.is_empty() {
        return false;
    }

    for (old_id, new_id) in &renames {
        eprintln!("[profile migration] Renaming profile '{old_id}' -> '{new_id}'");

        if let Some(profile) = profiles.profiles.remove(old_id) {
            profiles.profiles.insert(new_id.clone(), profile);
        }

        if profiles.active == *old_id {
            profiles.active = new_id.clone();
        }

        // Rename vault key file: ~/.busibox/vault-keys/{old}.enc -> {new}.enc
        if let Ok(keys_dir) = crate::modules::vault::vault_keys_dir() {
            let old_key = keys_dir.join(format!("{old_id}.enc"));
            let new_key = keys_dir.join(format!("{new_id}.enc"));
            if old_key.exists() && !new_key.exists() {
                if let Err(e) = std::fs::rename(&old_key, &new_key) {
                    eprintln!(
                        "[profile migration] Warning: could not rename vault key: {e}"
                    );
                }
            }
        }

        // Rename state directory: .busibox/profiles/{old} -> {new}
        let old_dir = repo_root.join(".busibox").join("profiles").join(old_id);
        let new_dir = repo_root.join(".busibox").join("profiles").join(new_id);
        if old_dir.exists() && !new_dir.exists() {
            if let Err(e) = std::fs::rename(&old_dir, &new_dir) {
                eprintln!(
                    "[profile migration] Warning: could not rename state dir: {e}"
                );
            }
        }
    }

    // Save the updated profiles
    if let Err(e) = save_profiles(repo_root, profiles) {
        eprintln!("[profile migration] Warning: could not save migrated profiles: {e}");
    }

    true
}
