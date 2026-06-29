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

// ============================================================================
// Vault file resolution (ported from scripts/lib/vault.sh)
// ============================================================================

/// Return the vault file path for a given prefix (profile ID or env prefix).
/// e.g., `vault_file_path(repo_root, "my-profile")` → `.../vault.my-profile.yml`
pub fn vault_file_path(repo_root: &Path, prefix: &str) -> PathBuf {
    repo_root
        .join("provision/ansible/roles/secrets/vars")
        .join(format!("vault.{prefix}.yml"))
}

/// Return the vault example file path.
pub fn vault_example_path(repo_root: &Path) -> PathBuf {
    repo_root
        .join("provision/ansible/roles/secrets/vars")
        .join("vault.example.yml")
}

/// Check if a vault file exists for a given prefix.
pub fn has_vault_file(repo_root: &Path, prefix: &str) -> bool {
    vault_file_path(repo_root, prefix).exists()
}

/// Find the legacy plaintext vault password file for a prefix.
/// Checks `BUSIBOX_VAULT_PASS_DIR` first, then `$HOME`, then legacy `~/.vault_pass`.
pub fn find_vault_pass_file(prefix: &str) -> Option<PathBuf> {
    let home = dirs::home_dir()?;

    // Check standard location
    let standard = home.join(format!(".busibox-vault-pass-{prefix}"));
    if standard.exists() {
        return Some(standard);
    }

    // Check legacy universal path
    let legacy = home.join(".vault_pass");
    if legacy.exists() {
        return Some(legacy);
    }

    None
}

/// Verify that a vault file can be decrypted with a given password.
/// Uses `ansible-vault view` under the hood.
pub fn verify_vault_decryption(vault_file: &Path, password: &str) -> Result<bool> {
    use std::io::Write;
    use std::process::{Command, Stdio};

    if !vault_file.exists() {
        return Err(eyre!("Vault file not found: {}", vault_file.display()));
    }

    let mut child = Command::new("ansible-vault")
        .args(["view", &vault_file.to_string_lossy()])
        .arg("--vault-password-file=/dev/stdin")
        .stdin(Stdio::piped())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
        .map_err(|e| eyre!("Failed to run ansible-vault: {e}"))?;

    if let Some(mut stdin) = child.stdin.take() {
        let _ = stdin.write_all(password.as_bytes());
    }

    let status = child.wait()?;
    Ok(status.success())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::PathBuf;

    fn hex(bytes: &[u8]) -> String {
        bytes.iter().map(|b| format!("{b:02x}")).collect()
    }

    // ===== DIAGNOSTIC TESTS =====
    // Run with: cargo test debug_ -- --nocapture
    // These print intermediate values to expose exactly where crypto diverges.

    /// Derive a key from a fixed known input and print the hex output.
    /// If Argon2 default parameters differ between builds or machines, the hex will differ.
    /// Cross-compare this output against a working machine to detect param drift.
    #[test]
    fn debug_argon2_params_are_stable() {
        // Fixed inputs — output must be identical everywhere if params are the same.
        let password = b"debug-password-busibox";
        let salt = [0x42u8; 32];

        let mut key = [0u8; 32];
        let start = std::time::Instant::now();
        Argon2::default()
            .hash_password_into(password, &salt, &mut key)
            .expect("Argon2 key derivation failed");
        let elapsed = start.elapsed();

        eprintln!("=== Argon2 parameter fingerprint ===");
        eprintln!("Input password: {:?}", std::str::from_utf8(password).unwrap());
        eprintln!("Salt (hex):     {}", hex(&salt));
        eprintln!("Output key:     {}", hex(&key));
        eprintln!("Derivation time: {:?}", elapsed);
        eprintln!();
        eprintln!("If this key differs from another machine, Argon2 default params changed.");
        eprintln!("Expected stable value (first run establishes baseline): {}", hex(&key));
    }

    /// Encrypt a known value and print every intermediate byte array.
    /// Use this to trace exactly what bytes are produced at each stage.
    #[test]
    fn debug_encrypt_decrypt_with_full_trace() {
        let vault_pw = "known-test-vault-pw-9999";
        let master_pw = "known-test-master-pw";

        eprintln!("=== Encrypt trace ===");
        eprintln!("Vault password:  {:?}", vault_pw);
        eprintln!("Vault pw bytes:  {:?}", vault_pw.as_bytes());
        eprintln!("Master password: {:?}", master_pw);

        let mut salt = [0u8; 32];
        OsRng.fill_bytes(&mut salt);
        eprintln!("Salt (hex):      {}", hex(&salt));
        eprintln!("Salt (b64):      {}", B64.encode(salt));

        let mut key_bytes = [0u8; 32];
        Argon2::default()
            .hash_password_into(master_pw.as_bytes(), &salt, &mut key_bytes)
            .unwrap();
        eprintln!("Derived key:     {}", hex(&key_bytes));

        let key = Key::<Aes256Gcm>::from_slice(&key_bytes);
        let cipher = Aes256Gcm::new(key);
        let nonce = Aes256Gcm::generate_nonce(&mut OsRng);
        eprintln!("Nonce (hex):     {}", hex(&nonce));
        eprintln!("Nonce len:       {} bytes (expected 12)", nonce.len());

        let ciphertext = cipher.encrypt(&nonce, vault_pw.as_bytes()).unwrap();
        eprintln!("Ciphertext (hex): {}", hex(&ciphertext));
        eprintln!(
            "Ciphertext len:  {} bytes ({} plaintext + 16 GCM tag)",
            ciphertext.len(),
            vault_pw.len()
        );

        eprintln!("\n=== Decrypt trace ===");
        let plaintext = cipher.decrypt(&nonce, ciphertext.as_ref()).unwrap();
        eprintln!("Plaintext bytes: {:?}", plaintext);
        let plaintext_str = String::from_utf8(plaintext.clone()).unwrap();
        eprintln!("Plaintext str:   {:?}", plaintext_str);
        eprintln!("Lengths match:   {}", plaintext.len() == vault_pw.len());
        assert_eq!(plaintext, vault_pw.as_bytes(), "round-trip mismatch");
    }

    /// Inspect the actual vault key file on disk without decrypting it.
    /// Reveals: file structure, byte lengths, whether data is valid base64.
    #[test]
    fn debug_inspect_vault_key_file_structure() {
        let profile = "local-development-docker";
        let path = vault_key_path(profile).unwrap();

        eprintln!("=== Vault key file inspection ===");
        eprintln!("Profile: {profile}");
        eprintln!("Path:    {}", path.display());
        eprintln!("Exists:  {}", path.exists());

        if !path.exists() {
            eprintln!("SKIP: file not found");
            return;
        }

        let raw = std::fs::read_to_string(&path).unwrap();
        eprintln!("Raw file contents:\n{raw}");

        let enc = load_encrypted_vault(&path).unwrap();
        eprintln!("Version:       {}", enc.version);

        let salt_bytes = B64.decode(&enc.salt).unwrap();
        let nonce_bytes = B64.decode(&enc.nonce).unwrap();
        let ct_bytes = B64.decode(&enc.ciphertext).unwrap();

        eprintln!("Salt:          {} bytes, hex: {}", salt_bytes.len(), hex(&salt_bytes));
        eprintln!("Nonce:         {} bytes, hex: {}", nonce_bytes.len(), hex(&nonce_bytes));
        eprintln!("Ciphertext:    {} bytes", ct_bytes.len());

        let pw_len = ct_bytes.len().saturating_sub(16);
        eprintln!("Decrypted pw will be {} chars (ciphertext - 16 byte GCM tag)", pw_len);

        if nonce_bytes.len() != 12 {
            eprintln!("WARNING: nonce is {} bytes, expected 12 for AES-GCM", nonce_bytes.len());
        }
        if salt_bytes.len() != 32 {
            eprintln!("WARNING: salt is {} bytes, expected 32", salt_bytes.len());
        }
        if pw_len != 32 {
            eprintln!("WARNING: expected 32-char vault password, got {pw_len} — possible trailing whitespace or version mismatch");
        }
    }

    /// Decrypt the actual vault key using a master password from env var.
    /// Shows the raw bytes of the decrypted vault password — exposes trailing
    /// newlines/whitespace that would silently corrupt ansible-vault decryption.
    ///
    /// Usage:
    ///   BUSIBOX_TEST_MASTER_PW="yourpassword" cargo test debug_decrypt_actual_vault -- --nocapture
    #[test]
    fn debug_decrypt_actual_vault_key() {
        let master_pw = match std::env::var("BUSIBOX_TEST_MASTER_PW") {
            Ok(v) => v,
            Err(_) => {
                eprintln!(
                    "SKIP: set BUSIBOX_TEST_MASTER_PW=<password> to run this test\n\
                     Example: BUSIBOX_TEST_MASTER_PW=mypassword cargo test debug_decrypt_actual_vault -- --nocapture"
                );
                return;
            }
        };

        let profile = std::env::var("BUSIBOX_TEST_PROFILE")
            .unwrap_or_else(|_| "local-development-docker".into());

        let path = vault_key_path(&profile).unwrap();
        eprintln!("=== Decrypt actual vault key ===");
        eprintln!("Profile: {profile}");
        eprintln!("Path:    {}", path.display());

        if !path.exists() {
            eprintln!("SKIP: vault key not found");
            return;
        }

        let enc = load_encrypted_vault(&path).unwrap();
        match decrypt_vault_password(&enc, &master_pw) {
            Ok(vault_pw) => {
                eprintln!("✓ AES-GCM decryption succeeded");
                eprintln!("Vault password length:    {} chars", vault_pw.len());
                eprintln!("Vault password hex:       {}", hex(vault_pw.as_bytes()));
                eprintln!("Has trailing newline:     {}", vault_pw.ends_with('\n'));
                eprintln!("Has trailing CR:          {}", vault_pw.ends_with('\r'));
                eprintln!("Has leading/trailing ws:  {:?} vs trimmed {:?}", vault_pw, vault_pw.trim());
                eprintln!("Is alphanumeric:          {}", vault_pw.chars().all(|c| c.is_ascii_alphanumeric()));
            }
            Err(e) => {
                eprintln!("✗ Decryption failed: {e}");
                eprintln!("This means the master password is wrong OR Argon2/AES params changed.");
                eprintln!("Run debug_argon2_params_are_stable on the machine that encrypted this key to compare.");
            }
        }
    }

    /// Write an encrypted vault, then try to decrypt it with a slightly-mutated
    /// master password (extra space, trailing newline, uppercase).
    /// Confirms AES-GCM auth tag correctly rejects all wrong-password variants.
    #[test]
    fn debug_password_variants_all_fail() {
        let vault_pw = "vault-pw-12345";
        let correct_pw = "correct-master-pw";
        let enc = encrypt_vault_password(vault_pw, correct_pw).unwrap();

        let bad_variants = [
            ("trailing newline", format!("{correct_pw}\n")),
            ("trailing space", format!("{correct_pw} ")),
            ("leading space", format!(" {correct_pw}")),
            ("uppercase", correct_pw.to_uppercase()),
            ("empty", String::new()),
        ];

        eprintln!("=== Wrong-password rejection test ===");
        for (label, bad_pw) in &bad_variants {
            let result = decrypt_vault_password(&enc, bad_pw);
            eprintln!("  [{label}] → {}", if result.is_err() { "correctly rejected ✓" } else { "INCORRECTLY ACCEPTED ✗" });
            assert!(result.is_err(), "variant '{label}' should have failed decryption");
        }

        // Correct password must still work
        let result = decrypt_vault_password(&enc, correct_pw);
        eprintln!("  [correct pw] → {}", if result.is_ok() { "accepted ✓" } else { "FAILED ✗" });
        assert!(result.is_ok());
        assert_eq!(result.unwrap(), vault_pw);
    }

    /// Test that the vault password written to ansible-vault has no hidden bytes.
    /// Ansible expects a password file with no trailing newline, or with exactly one.
    /// This test exercises the exact bytes that verify_vault_decryption sends via stdin.
    #[test]
    fn debug_vault_password_has_no_extra_bytes() {
        let vault_pw = generate_vault_password();
        let master_pw = "debug-master";

        let enc = encrypt_vault_password(&vault_pw, master_pw).unwrap();
        let decrypted = decrypt_vault_password(&enc, master_pw).unwrap();

        eprintln!("=== Vault password byte inspection ===");
        eprintln!("Original:  {:?} ({} bytes)", vault_pw, vault_pw.len());
        eprintln!("Decrypted: {:?} ({} bytes)", decrypted, decrypted.len());
        eprintln!("Byte-for-byte match: {}", vault_pw.as_bytes() == decrypted.as_bytes());

        // What gets sent to ansible-vault via stdin in verify_vault_decryption
        let stdin_bytes = decrypted.as_bytes();
        eprintln!("Bytes sent to ansible-vault stdin: {:?}", stdin_bytes);
        eprintln!("Last byte: 0x{:02x} (should be alphanumeric, not 0x0a/newline)", stdin_bytes.last().unwrap_or(&0));

        assert_eq!(vault_pw, decrypted, "round-trip produced different value");
        assert!(!decrypted.ends_with('\n'), "vault password has trailing newline — would break ansible-vault");
        assert!(!decrypted.ends_with('\r'), "vault password has trailing CR");
    }

    #[test]
    fn generate_vault_password_has_correct_length() {
        let pw = generate_vault_password();
        assert_eq!(pw.len(), 32);
    }

    #[test]
    fn generate_vault_password_is_alphanumeric() {
        let pw = generate_vault_password();
        assert!(pw.chars().all(|c| c.is_ascii_alphanumeric()));
    }

    #[test]
    fn generate_vault_password_is_random() {
        let pw1 = generate_vault_password();
        let pw2 = generate_vault_password();
        assert_ne!(pw1, pw2, "Two generated passwords should differ");
    }

    #[test]
    fn encrypt_decrypt_round_trip() {
        let vault_pw = "test-vault-password-12345";
        let master_pw = "master-secret";

        let encrypted = encrypt_vault_password(vault_pw, master_pw).unwrap();
        let decrypted = decrypt_vault_password(&encrypted, master_pw).unwrap();

        assert_eq!(decrypted, vault_pw);
    }

    #[test]
    fn encrypt_decrypt_with_special_chars() {
        let vault_pw = "p@$$w0rd!#%^&*()_+-=[]{}|;':\",./<>?";
        let master_pw = "müñ!çh€n🔑";

        let encrypted = encrypt_vault_password(vault_pw, master_pw).unwrap();
        let decrypted = decrypt_vault_password(&encrypted, master_pw).unwrap();

        assert_eq!(decrypted, vault_pw);
    }

    #[test]
    fn decrypt_with_wrong_password_fails() {
        let vault_pw = "test-vault-password";
        let master_pw = "correct-password";
        let wrong_pw = "wrong-password";

        let encrypted = encrypt_vault_password(vault_pw, master_pw).unwrap();
        let result = decrypt_vault_password(&encrypted, wrong_pw);

        assert!(result.is_err());
    }

    #[test]
    fn encrypted_vault_has_version_1() {
        let encrypted = encrypt_vault_password("pw", "master").unwrap();
        assert_eq!(encrypted.version, 1);
    }

    #[test]
    fn encrypted_vault_fields_are_base64() {
        let encrypted = encrypt_vault_password("pw", "master").unwrap();
        assert!(base64::engine::general_purpose::STANDARD.decode(&encrypted.salt).is_ok());
        assert!(base64::engine::general_purpose::STANDARD.decode(&encrypted.nonce).is_ok());
        assert!(base64::engine::general_purpose::STANDARD.decode(&encrypted.ciphertext).is_ok());
    }

    #[test]
    fn decrypt_unsupported_version_fails() {
        let mut encrypted = encrypt_vault_password("pw", "master").unwrap();
        encrypted.version = 99;
        let result = decrypt_vault_password(&encrypted, "master");
        assert!(result.is_err());
        assert!(result.unwrap_err().to_string().contains("Unsupported vault key version"));
    }

    #[test]
    fn save_and_load_encrypted_vault_round_trip() {
        let tmp = std::env::temp_dir().join("busibox-test-vault.enc");
        let encrypted = encrypt_vault_password("vault-pw", "master-pw").unwrap();

        save_encrypted_vault(&tmp, &encrypted).unwrap();
        let loaded = load_encrypted_vault(&tmp).unwrap();

        assert_eq!(loaded.version, encrypted.version);
        assert_eq!(loaded.salt, encrypted.salt);
        assert_eq!(loaded.nonce, encrypted.nonce);
        assert_eq!(loaded.ciphertext, encrypted.ciphertext);

        let decrypted = decrypt_vault_password(&loaded, "master-pw").unwrap();
        assert_eq!(decrypted, "vault-pw");

        let _ = std::fs::remove_file(&tmp);
    }

    #[test]
    fn load_encrypted_vault_missing_file_fails() {
        let result = load_encrypted_vault(Path::new("/nonexistent/path.enc"));
        assert!(result.is_err());
    }

    #[test]
    fn vault_file_path_builds_correct_path() {
        let repo = PathBuf::from("/home/user/busibox");
        let path = vault_file_path(&repo, "my-profile");
        assert_eq!(
            path,
            PathBuf::from("/home/user/busibox/provision/ansible/roles/secrets/vars/vault.my-profile.yml")
        );
    }

    #[test]
    fn vault_example_path_builds_correct_path() {
        let repo = PathBuf::from("/home/user/busibox");
        let path = vault_example_path(&repo);
        assert_eq!(
            path,
            PathBuf::from("/home/user/busibox/provision/ansible/roles/secrets/vars/vault.example.yml")
        );
    }

    #[test]
    fn vault_key_path_builds_correct_path() {
        let path = vault_key_path("my-profile").unwrap();
        let home = dirs::home_dir().unwrap();
        assert_eq!(path, home.join(".busibox/vault-keys/my-profile.enc"));
    }

    #[test]
    fn vault_keys_dir_is_under_home() {
        let dir = vault_keys_dir().unwrap();
        let home = dirs::home_dir().unwrap();
        assert!(dir.starts_with(&home));
        assert!(dir.ends_with("vault-keys"));
    }
}

/// Create a new vault file from the example template, then encrypt it.
pub fn create_vault_from_example(repo_root: &Path, prefix: &str, password: &str) -> Result<()> {
    let example = vault_example_path(repo_root);
    let target = vault_file_path(repo_root, prefix);

    if !example.exists() {
        return Err(eyre!("Vault example not found: {}", example.display()));
    }

    std::fs::copy(&example, &target)?;

    use std::io::Write;
    use std::process::{Command, Stdio};

    let mut child = Command::new("ansible-vault")
        .args(["encrypt", &target.to_string_lossy()])
        .arg("--vault-password-file=/dev/stdin")
        .stdin(Stdio::piped())
        .stdout(Stdio::null())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|e| eyre!("Failed to run ansible-vault encrypt: {e}"))?;

    if let Some(mut stdin) = child.stdin.take() {
        let _ = stdin.write_all(password.as_bytes());
    }

    let status = child.wait()?;
    if !status.success() {
        return Err(eyre!("ansible-vault encrypt failed"));
    }

    Ok(())
}
