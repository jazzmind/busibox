//! Non-interactive subcommand handlers for the `busibox` binary.
//!
//! The `busibox` binary is *one* executable that behaves as either a CLI or
//! a TUI depending on how it's invoked.  This module owns the CLI side:
//! everything that runs without entering ratatui.  TUI mode is launched by
//! `main.rs` when no subcommand is given and stdin/stdout are a terminal.
//!
//! Conventions:
//!
//! - All handlers return `Result<i32>`.  The integer becomes the process
//!   exit code (0 success, non-zero failure).
//! - Output goes to stdout for normal results, stderr for warnings/errors.
//! - Nothing here blocks on user input.  These commands must be safe to run
//!   under CI, agents, and pipes.

use busibox_core::profiles::{resolve_services, AddonPack, BusiboxProfile};
use color_eyre::Result;
use std::path::Path;
use std::str::FromStr;

/// Build version string (from Cargo.toml at compile time).
pub const VERSION: &str = env!("CARGO_PKG_VERSION");

/// `busibox version` — print the binary version and exit 0.
pub fn version() -> Result<i32> {
    println!("busibox {VERSION}");
    Ok(0)
}

/// `busibox profile list` — print the available preset profiles.
pub fn profile_list() -> Result<i32> {
    println!("Available Busibox profiles:");
    println!();
    for p in BusiboxProfile::all() {
        let marker = if *p == BusiboxProfile::DEFAULT {
            " (default)"
        } else {
            ""
        };
        println!("  {:<10}{marker}", p.id());
        println!("    {}", p.description());
        println!("    services: {}", p.services().join(", "));
        println!();
    }
    println!("Add-on packs:");
    println!();
    for pack in AddonPack::all() {
        let tag = if pack.is_placeholder() {
            " [placeholder — not yet wired in this tree]"
        } else {
            ""
        };
        println!("  {:<14}{tag}", pack.id());
        println!("    {}", pack.description());
        if !pack.services().is_empty() {
            println!("    services: {}", pack.services().join(", "));
        }
        println!();
    }
    Ok(0)
}

/// `busibox profile show <name>` — print the resolved service list for a
/// profile (optionally combined with add-on packs).
pub fn profile_show(name: &str, packs: &[String]) -> Result<i32> {
    let profile = match BusiboxProfile::from_str(name) {
        Ok(p) => p,
        Err(e) => {
            eprintln!("error: {e}");
            return Ok(2);
        }
    };
    let parsed_packs: Vec<AddonPack> = match packs.iter().map(|s| AddonPack::from_str(s)).collect()
    {
        Ok(v) => v,
        Err(e) => {
            eprintln!("error: {e}");
            return Ok(2);
        }
    };

    println!("Profile:     {profile}");
    println!("Description: {}", profile.description());
    println!(
        "Cloud LLM:   {}",
        if profile.requires_cloud_llm_key() {
            "required (no local model serving in this profile)"
        } else {
            "optional"
        }
    );
    if !parsed_packs.is_empty() {
        let ids: Vec<&str> = parsed_packs.iter().map(|p| p.id()).collect();
        println!("Packs:       {}", ids.join(", "));
        let placeholders: Vec<&AddonPack> =
            parsed_packs.iter().filter(|p| p.is_placeholder()).collect();
        if !placeholders.is_empty() {
            let ids: Vec<&str> = placeholders.iter().map(|p| p.id()).collect();
            eprintln!(
                "warning: the following packs are placeholders and currently deploy nothing: {}",
                ids.join(", ")
            );
        }
    }
    let services = resolve_services(profile, &parsed_packs);
    println!();
    println!("Services to deploy ({}):", services.len());
    for s in &services {
        println!("  - {s}");
    }

    let excluded = profile.excluded_services();
    if !excluded.is_empty() && parsed_packs.is_empty() {
        println!();
        println!("Excluded vs. full ({}):", excluded.len());
        for s in &excluded {
            println!("  - {s}");
        }
    }
    Ok(0)
}

/// `busibox doctor` — quick read-only environment summary.
///
/// This is deliberately scoped: it reports what's installed, what services
/// the chosen profile *would* deploy, and any obviously-missing pieces
/// (e.g. Docker not present for a Docker-backend profile).  It does not
/// touch the network, the vault, or any container.
pub fn doctor(repo_root: &Path, profile: BusiboxProfile, packs: &[AddonPack]) -> Result<i32> {
    use busibox_core::hardware::HardwareProfile;

    println!("Busibox doctor — environment check");
    println!("==================================");
    println!();

    println!("Binary version: {VERSION}");
    println!("Repository:     {}", repo_root.display());
    println!();

    let hw = match HardwareProfile::detect_local() {
        Ok(hw) => hw,
        Err(e) => {
            eprintln!("warning: hardware detection failed: {e}");
            return Ok(1);
        }
    };
    println!("Host:");
    println!("  OS:        {} ({})", hw.os, hw.arch);
    println!("  RAM:       {} GB", hw.ram_gb);
    println!("  Tier:      {}", hw.memory_tier.name());
    println!(
        "  Docker:    {}",
        if hw.docker_available {
            "available"
        } else {
            "MISSING"
        }
    );
    println!("  LLM:       {}", hw.llm_backend);
    println!();

    println!("Profile:        {profile}");
    println!("Description:    {}", profile.description());
    if !packs.is_empty() {
        let ids: Vec<&str> = packs.iter().map(|p| p.id()).collect();
        println!("Packs:          {}", ids.join(", "));
    }

    let services = resolve_services(profile, packs);
    println!("Services ({}):  {}", services.len(), services.join(", "));
    println!();

    let mut warnings: Vec<String> = Vec::new();
    let mut errors: Vec<String> = Vec::new();

    if !hw.docker_available {
        errors.push("Docker is not available on the host; Lite/Standard require Docker.".into());
    }
    if profile.requires_cloud_llm_key() && !packs.contains(&AddonPack::LocalModels) {
        warnings.push(
            "This profile needs a cloud LLM API key (OpenAI / Anthropic / Bedrock) — \
             set it in .env.local before `busibox up`."
                .into(),
        );
    }
    for pack in packs {
        if pack.is_placeholder() {
            warnings.push(format!(
                "Pack '{pack}' is a placeholder — no services for it ship in this tree yet."
            ));
        }
    }

    if warnings.is_empty() && errors.is_empty() {
        println!("All checks passed.");
        Ok(0)
    } else {
        for w in &warnings {
            println!("warning: {w}");
        }
        for e in &errors {
            eprintln!("error:   {e}");
        }
        if errors.is_empty() {
            Ok(0)
        } else {
            Ok(1)
        }
    }
}

/// `busibox verify` — deterministic, non-destructive sanity check.
///
/// Intended to be safe for CI and for agents to call repeatedly.
/// Today it verifies:
///   - the repo root looks like a Busibox checkout (has `Makefile`, `cli/`)
///   - every service named by the chosen profile is recognized by the
///     service registry (so deployment won't silently no-op)
pub fn verify(repo_root: &Path, profile: BusiboxProfile, packs: &[AddonPack]) -> Result<i32> {
    use busibox_core::services::container_for_service;

    let mut errs: Vec<String> = Vec::new();

    if !repo_root.join("Makefile").exists() {
        errs.push(format!(
            "{} does not contain a Makefile — not a Busibox checkout?",
            repo_root.display()
        ));
    }
    if !repo_root.join("cli").exists() {
        errs.push(format!(
            "{} does not contain a cli/ directory.",
            repo_root.display()
        ));
    }

    let services = resolve_services(profile, packs);
    for svc in &services {
        if container_for_service(svc).is_none() {
            errs.push(format!(
                "service '{svc}' (from profile {profile}) is not in the service registry"
            ));
        }
    }

    if errs.is_empty() {
        println!(
            "OK  busibox verify  profile={profile}  services={}",
            services.len()
        );
        Ok(0)
    } else {
        for e in &errs {
            eprintln!("FAIL {e}");
        }
        Ok(1)
    }
}

/// `busibox up` — print the deploy plan for a profile (+ packs) without
/// actually deploying.  This is the safe, no-side-effect entrypoint:
/// it tells the user (or agent) exactly what `make install` invocations
/// the chosen profile would translate to.  Wiring it to actually call
/// the deploy backend is left to a follow-up PR so this change is purely
/// additive and reviewable.
pub fn up_plan(profile: BusiboxProfile, packs: &[AddonPack], dry_run_only: bool) -> Result<i32> {
    let services = resolve_services(profile, packs);
    println!("Plan for `busibox up --profile {profile}`:");
    if !packs.is_empty() {
        let ids: Vec<&str> = packs.iter().map(|p| p.id()).collect();
        println!("  add-on packs: {}", ids.join(", "));
    }
    let placeholders: Vec<&AddonPack> = packs.iter().filter(|p| p.is_placeholder()).collect();
    if !placeholders.is_empty() {
        let ids: Vec<&str> = placeholders.iter().map(|p| p.id()).collect();
        eprintln!(
            "warning: pack(s) {} are placeholders — they currently deploy nothing.",
            ids.join(", ")
        );
    }
    println!();
    println!("Would deploy the following services in order:");
    for s in &services {
        println!("  - {s}");
    }
    println!();
    println!("Equivalent make invocation:");
    println!("  make install SERVICE={}", services.join(","));
    if !dry_run_only {
        eprintln!();
        eprintln!("note: `busibox up` currently only prints the plan. Run the");
        eprintln!("      `make install` line above to actually deploy, or use the");
        eprintln!("      interactive TUI: `busibox`.");
    }
    Ok(0)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn version_is_non_empty() {
        assert!(!VERSION.is_empty());
    }

    #[test]
    fn profile_list_returns_zero() {
        assert_eq!(profile_list().unwrap(), 0);
    }

    #[test]
    fn profile_show_unknown_profile_exits_two() {
        assert_eq!(profile_show("bogus", &[]).unwrap(), 2);
    }

    #[test]
    fn profile_show_unknown_pack_exits_two() {
        assert_eq!(
            profile_show("lite", &["bogus-pack".to_string()]).unwrap(),
            2
        );
    }

    #[test]
    fn profile_show_lite_returns_zero() {
        assert_eq!(profile_show("lite", &[]).unwrap(), 0);
    }

    #[test]
    fn up_plan_lite_returns_zero() {
        assert_eq!(up_plan(BusiboxProfile::Lite, &[], true).unwrap(), 0);
    }

    #[test]
    fn verify_in_busibox_root_returns_zero() {
        // The test runs from the crate directory; the repo root is two
        // levels up.
        let manifest = Path::new(env!("CARGO_MANIFEST_DIR"));
        let repo_root = manifest.parent().unwrap().parent().unwrap();
        assert_eq!(verify(repo_root, BusiboxProfile::Lite, &[]).unwrap(), 0);
    }

    #[test]
    fn verify_in_empty_dir_returns_one() {
        let tmp = std::env::temp_dir();
        // Use a subdirectory that definitely doesn't have a Makefile.
        let p = tmp.join("busibox-cli-verify-test-empty");
        let _ = std::fs::create_dir_all(&p);
        assert_eq!(verify(&p, BusiboxProfile::Lite, &[]).unwrap(), 1);
    }
}
