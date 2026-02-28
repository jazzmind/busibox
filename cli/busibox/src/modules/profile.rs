use crate::modules::hardware::HardwareProfile;
use color_eyre::{eyre::eyre, Result};
use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::path::{Path, PathBuf};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ProfilesFile {
    pub active: String,
    pub profiles: HashMap<String, Profile>,
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
}

impl Profile {
    pub fn effective_host(&self) -> Option<&str> {
        self.tailscale_ip
            .as_deref()
            .or(self.remote_host.as_deref())
    }

    pub fn effective_user(&self) -> &str {
        self.remote_user.as_deref().unwrap_or("root")
    }

    pub fn effective_ssh_key(&self) -> &str {
        self.remote_ssh_key.as_deref().unwrap_or("~/.ssh/id_ed25519")
    }

    pub fn effective_remote_path(&self) -> &str {
        self.remote_busibox_path
            .as_deref()
            .unwrap_or("~/busibox")
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
pub fn upsert_profile(
    repo_root: &Path,
    id: &str,
    profile: Profile,
    set_active: bool,
) -> Result<()> {
    let mut profiles = load_profiles(repo_root)?;
    profiles.profiles.insert(id.to_string(), profile);
    if set_active {
        profiles.active = id.to_string();
    }
    save_profiles(repo_root, &profiles)
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
