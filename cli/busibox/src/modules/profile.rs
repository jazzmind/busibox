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
    pub frontend_ref: Option<String>,
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
}

/// Get the path to .busibox/profiles.json relative to a repo root.
pub fn profiles_path(repo_root: &Path) -> PathBuf {
    repo_root.join(".busibox").join("profiles.json")
}

/// Load profiles from disk.
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
    let profiles: ProfilesFile = serde_json::from_str(&contents)?;
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
                && !l.starts_with("MODEL_TIER=")
                && !l.starts_with("LLM_BACKEND=")
        })
        .map(|l| l.to_string())
        .collect();

    if let Some(ref email) = profile.admin_email {
        lines.push(format!("ADMIN_EMAIL={email}"));
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

    let content = lines.join("\n") + "\n";
    std::fs::write(&state_file, content)?;
    Ok(())
}
