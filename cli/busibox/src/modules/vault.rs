use aes_gcm::{
    aead::{Aead, AeadCore, KeyInit, OsRng},
    Aes256Gcm, Key, Nonce,
};
use argon2::Argon2;
use base64::{engine::general_purpose::STANDARD as B64, Engine};
use color_eyre::{eyre::eyre, Result};
use rand::Rng;
use serde::{Deserialize, Serialize};
use std::path::{Path, PathBuf};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct EncryptedVault {
    pub version: u32,
    pub salt: String,
    pub nonce: String,
    pub ciphertext: String,
}

fn derive_key(master_password: &str, salt: &[u8]) -> Result<Key<Aes256Gcm>> {
    let mut key_bytes = [0u8; 32];
    Argon2::default()
        .hash_password_into(master_password.as_bytes(), salt, &mut key_bytes)
        .map_err(|e| eyre!("Key derivation failed: {e}"))?;
    Ok(*Key::<Aes256Gcm>::from_slice(&key_bytes))
}

/// Generate a cryptographically random vault password (32 alphanumeric chars).
pub fn generate_vault_password() -> String {
    const CHARSET: &[u8] = b"abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789";
    let mut rng = rand::thread_rng();
    (0..32)
        .map(|_| {
            let idx = rng.gen_range(0..CHARSET.len());
            CHARSET[idx] as char
        })
        .collect()
}

/// Encrypt a vault password with a master password.
pub fn encrypt_vault_password(vault_password: &str, master_password: &str) -> Result<EncryptedVault> {
    let mut salt = [0u8; 32];
    OsRng.fill_bytes(&mut salt);

    let key = derive_key(master_password, &salt)?;
    let cipher = Aes256Gcm::new(&key);
    let nonce = Aes256Gcm::generate_nonce(&mut OsRng);

    let ciphertext = cipher
        .encrypt(&nonce, vault_password.as_bytes())
        .map_err(|e| eyre!("Encryption failed: {e}"))?;

    Ok(EncryptedVault {
        version: 1,
        salt: B64.encode(salt),
        nonce: B64.encode(nonce),
        ciphertext: B64.encode(ciphertext),
    })
}

/// Decrypt a vault password using a master password.
pub fn decrypt_vault_password(enc: &EncryptedVault, master_password: &str) -> Result<String> {
    if enc.version != 1 {
        return Err(eyre!("Unsupported vault key version: {}", enc.version));
    }

    let salt = B64
        .decode(&enc.salt)
        .map_err(|e| eyre!("Invalid salt: {e}"))?;
    let nonce_bytes = B64
        .decode(&enc.nonce)
        .map_err(|e| eyre!("Invalid nonce: {e}"))?;
    let ciphertext = B64
        .decode(&enc.ciphertext)
        .map_err(|e| eyre!("Invalid ciphertext: {e}"))?;

    let key = derive_key(master_password, &salt)?;
    let cipher = Aes256Gcm::new(&key);
    let nonce = Nonce::from_slice(&nonce_bytes);

    let plaintext = cipher
        .decrypt(nonce, ciphertext.as_ref())
        .map_err(|_| eyre!("Decryption failed — wrong master password?"))?;

    String::from_utf8(plaintext).map_err(|e| eyre!("Vault password is not valid UTF-8: {e}"))
}

/// Save an encrypted vault to a JSON file using atomic write (temp + rename).
pub fn save_encrypted_vault(path: &Path, enc: &EncryptedVault) -> Result<()> {
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)?;
    }
    let json = serde_json::to_string_pretty(enc)?;
    let tmp = path.with_extension("enc.tmp");
    std::fs::write(&tmp, format!("{json}\n"))?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(&tmp, std::fs::Permissions::from_mode(0o600))?;
    }
    std::fs::rename(&tmp, path)?;
    Ok(())
}

/// Load an encrypted vault from a JSON file.
pub fn load_encrypted_vault(path: &Path) -> Result<EncryptedVault> {
    let contents = std::fs::read_to_string(path)
        .map_err(|e| eyre!("Cannot read vault key file {}: {e}", path.display()))?;
    let enc: EncryptedVault = serde_json::from_str(&contents)
        .map_err(|e| eyre!("Invalid vault key file {}: {e}", path.display()))?;
    Ok(enc)
}

/// Return the vault-keys directory: ~/.busibox/vault-keys/
pub fn vault_keys_dir() -> Result<PathBuf> {
    let home = dirs::home_dir().ok_or_else(|| eyre!("Cannot determine home directory"))?;
    Ok(home.join(".busibox").join("vault-keys"))
}

/// Return the path to the encrypted vault key for a profile.
/// e.g. ~/.busibox/vault-keys/{profile_id}.enc
pub fn vault_key_path(profile_id: &str) -> Result<PathBuf> {
    Ok(vault_keys_dir()?.join(format!("{profile_id}.enc")))
}

/// Check if an encrypted vault key exists for a profile.
pub fn has_vault_key(profile_id: &str) -> bool {
    vault_key_path(profile_id)
        .map(|p| p.exists())
        .unwrap_or(false)
}

/// Check if a legacy plaintext vault password file exists for a given prefix.
/// Returns the path if found.
pub fn find_legacy_vault_pass(prefix: &str) -> Option<PathBuf> {
    let home = dirs::home_dir()?;
    let path = home.join(format!(".busibox-vault-pass-{prefix}"));
    if path.exists() {
        Some(path)
    } else {
        let legacy = home.join(".vault_pass");
        if legacy.exists() {
            Some(legacy)
        } else {
            None
        }
    }
}

/// Prompt the user for a password (hidden input). Must be called outside raw mode.
pub fn prompt_password(prompt: &str) -> Result<String> {
    let password = rpassword::prompt_password(prompt)
        .map_err(|e| eyre!("Failed to read password: {e}"))?;
    Ok(password)
}

/// Prompt for a new password with confirmation. Must be called outside raw mode.
pub fn prompt_new_password(prompt: &str) -> Result<String> {
    loop {
        let p1 = prompt_password(prompt)?;
        if p1.is_empty() {
            eprintln!("Password cannot be empty.");
            continue;
        }
        let p2 = prompt_password("Confirm password: ")?;
        if p1 != p2 {
            eprintln!("Passwords do not match. Try again.");
            continue;
        }
        return Ok(p1);
    }
}

use aes_gcm::aead::rand_core::RngCore;
