//! Busibox profile presets and add-on packs.
//!
//! A *Busibox profile* (here distinct from the per-installation `Profile`
//! struct in `profile.rs`) is a curated set of services that defines what a
//! Busibox installation actually runs.  Profiles are how we draw the line
//! between "Busibox Lite" (the public default — tiny, cloud-key-first) and
//! the full historical stack.
//!
//! These presets are advisory metadata.  They drive CLI flag parsing, doctor
//! output, and documentation; they do not yet replace the underlying Ansible
//! tag system or compose files.  When the deploy layer learns about profiles,
//! the canonical service list will move here.
//!
//! Three preset profiles ship in the OSS tree:
//!
//! - [`BusiboxProfile::Lite`]    — first-run preview.  Docker-local, cloud
//!   model key required, no GPU, no local model weights, no media/graph.
//! - [`BusiboxProfile::Standard`] — Lite plus local RAG via Milvus + the
//!   embedding/search APIs.  Still cloud-LLM by default.
//! - [`BusiboxProfile::Full`]    — everything currently in the service
//!   registry (matches today's `make install SERVICE=all`).
//!
//! Add-on packs ([`AddonPack`]) opt back into individual capabilities that
//! Lite excludes.  Multiple packs can be combined.  Packs do not yet have
//! their own Ansible groups; the CLI is the source of truth for what each
//! pack *would* enable, and `busibox doctor` will warn when a pack names a
//! backend that isn't actually wired in this repo.

use serde::{Deserialize, Serialize};
use std::fmt;
use std::str::FromStr;

/// A curated set of services that defines what a Busibox install runs.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum BusiboxProfile {
    /// Minimal cloud-LLM-key-first install.  This is the public default.
    Lite,
    /// Lite plus local Milvus-backed RAG (embedding + search APIs).
    Standard,
    /// Full historical stack (matches `make install SERVICE=all`).
    Full,
}

impl BusiboxProfile {
    /// The default profile used when none is specified.
    pub const DEFAULT: Self = Self::Lite;

    /// Stable lowercase identifier (used on the CLI and in config files).
    pub fn id(&self) -> &'static str {
        match self {
            Self::Lite => "lite",
            Self::Standard => "standard",
            Self::Full => "full",
        }
    }

    /// One-line human description.
    pub fn description(&self) -> &'static str {
        match self {
            Self::Lite => "Cloud-key Docker preview — no GPU, no local models, no media/graph",
            Self::Standard => "Lite + local Milvus RAG (embedding + search)",
            Self::Full => "Everything in the service registry",
        }
    }

    /// Services this profile deploys, in canonical name form (matches keys
    /// understood by [`crate::services::container_for_service`]).
    ///
    /// The Lite list is intentionally small: identity (`authz`), the deploy
    /// control plane (`deploy`, `config`), the Portal (`core-apps`), the
    /// reverse proxy (`nginx`), Postgres, Redis (a transitive companion of
    /// the data path, kept because authz/deploy/agent rely on it), MinIO for
    /// object storage, and the agent + data + LiteLLM trio so the user can
    /// actually chat against their cloud key.  Notably absent: `vllm`,
    /// `mlx`, `milvus`, `neo4j`, `embedding`, `search`, `user-apps`.
    pub fn services(&self) -> &'static [&'static str] {
        match self {
            Self::Lite => &[
                "nginx",
                "postgres",
                "redis",
                "minio",
                "authz",
                "config",
                "deploy",
                "agent",
                "data",
                "litellm",
                "core-apps",
            ],
            Self::Standard => &[
                "nginx",
                "postgres",
                "redis",
                "minio",
                "milvus",
                "authz",
                "config",
                "deploy",
                "agent",
                "data",
                "embedding",
                "search",
                "litellm",
                "core-apps",
            ],
            Self::Full => &[
                "nginx",
                "postgres",
                "redis",
                "minio",
                "milvus",
                "neo4j",
                "authz",
                "config",
                "deploy",
                "agent",
                "data",
                "embedding",
                "search",
                "bridge",
                "docs",
                "litellm",
                "vllm",
                "core-apps",
                "user-apps",
                "custom-services",
            ],
        }
    }

    /// Services this profile deliberately excludes vs. [`Self::Full`].
    /// Useful for the CLI's `profile show` output and `doctor` reporting.
    pub fn excluded_services(&self) -> Vec<&'static str> {
        let mine = self.services();
        Self::Full
            .services()
            .iter()
            .copied()
            .filter(|s| !mine.contains(s))
            .collect()
    }

    /// Does this profile require a hosted-LLM API key (no local LLM)?
    pub fn requires_cloud_llm_key(&self) -> bool {
        matches!(self, Self::Lite | Self::Standard)
    }

    /// All profiles in stable order — for help text, docs generation, etc.
    pub fn all() -> &'static [Self] {
        &[Self::Lite, Self::Standard, Self::Full]
    }
}

impl fmt::Display for BusiboxProfile {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.id())
    }
}

impl FromStr for BusiboxProfile {
    type Err = String;
    fn from_str(s: &str) -> Result<Self, Self::Err> {
        match s.trim().to_ascii_lowercase().as_str() {
            "lite" => Ok(Self::Lite),
            "standard" | "std" => Ok(Self::Standard),
            "full" | "all" => Ok(Self::Full),
            other => Err(format!(
                "unknown profile '{other}' (expected: lite, standard, full)"
            )),
        }
    }
}

/// Optional capability packs that opt back into things Lite excludes.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "kebab-case")]
pub enum AddonPack {
    /// Local model serving (Ollama / vLLM / MLX).  Adds GPU dependency.
    LocalModels,
    /// Graph database (Neo4j).
    Graph,
    /// Media stack — placeholder.  Not yet wired in this tree.
    Media,
    /// Fleet management (multi-host orchestration) — placeholder.
    Fleet,
    /// Local Milvus-backed RAG (same as enabling Standard).
    RagMilvus,
    /// Qdrant-backed RAG — placeholder.  No services exist for this yet.
    RagQdrant,
}

impl AddonPack {
    pub fn id(&self) -> &'static str {
        match self {
            Self::LocalModels => "local-models",
            Self::Graph => "graph",
            Self::Media => "media",
            Self::Fleet => "fleet",
            Self::RagMilvus => "rag-milvus",
            Self::RagQdrant => "rag-qdrant",
        }
    }

    pub fn description(&self) -> &'static str {
        match self {
            Self::LocalModels => "Local model serving (Ollama / vLLM / MLX) — requires GPU",
            Self::Graph => "Graph database (Neo4j)",
            Self::Media => "Media stack — not yet wired in this tree",
            Self::Fleet => "Fleet / multi-host orchestration — not yet wired in this tree",
            Self::RagMilvus => "Local Milvus-backed RAG (embedding + search)",
            Self::RagQdrant => "Qdrant-backed RAG — not yet wired in this tree",
        }
    }

    /// Extra services this pack enables on top of the base profile.
    pub fn services(&self) -> &'static [&'static str] {
        match self {
            Self::LocalModels => &["vllm"],
            Self::Graph => &["neo4j"],
            Self::RagMilvus => &["milvus", "embedding", "search"],
            // The following are intentionally empty: no services in this
            // tree implement them yet, and we refuse to fake it.
            Self::Media | Self::Fleet | Self::RagQdrant => &[],
        }
    }

    /// True if this pack names a backend that has no implementation in the
    /// current repo (so `doctor` can flag it instead of pretending to
    /// deploy it).
    pub fn is_placeholder(&self) -> bool {
        matches!(self, Self::Media | Self::Fleet | Self::RagQdrant)
    }

    pub fn all() -> &'static [Self] {
        &[
            Self::LocalModels,
            Self::Graph,
            Self::Media,
            Self::Fleet,
            Self::RagMilvus,
            Self::RagQdrant,
        ]
    }
}

impl fmt::Display for AddonPack {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.id())
    }
}

impl FromStr for AddonPack {
    type Err = String;
    fn from_str(s: &str) -> Result<Self, Self::Err> {
        match s.trim().to_ascii_lowercase().as_str() {
            "local-models" | "local_models" | "localmodels" => Ok(Self::LocalModels),
            "graph" | "neo4j" => Ok(Self::Graph),
            "media" => Ok(Self::Media),
            "fleet" => Ok(Self::Fleet),
            "rag-milvus" | "rag_milvus" | "milvus" => Ok(Self::RagMilvus),
            "rag-qdrant" | "rag_qdrant" | "qdrant" => Ok(Self::RagQdrant),
            other => Err(format!(
                "unknown add-on pack '{other}' (expected: local-models, graph, media, fleet, rag-milvus, rag-qdrant)"
            )),
        }
    }
}

/// Resolve a base profile plus add-on packs into the final flat list of
/// services to deploy.  Order is preserved from the base profile, with
/// pack additions appended in pack order.  Duplicates are removed.
pub fn resolve_services(profile: BusiboxProfile, packs: &[AddonPack]) -> Vec<&'static str> {
    let mut out: Vec<&'static str> = profile.services().to_vec();
    for pack in packs {
        for svc in pack.services() {
            if !out.contains(svc) {
                out.push(svc);
            }
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn lite_is_the_default() {
        assert_eq!(BusiboxProfile::DEFAULT, BusiboxProfile::Lite);
    }

    #[test]
    fn lite_excludes_heavyweight_services() {
        let lite = BusiboxProfile::Lite.services();
        for forbidden in &[
            "vllm",
            "mlx",
            "milvus",
            "neo4j",
            "embedding",
            "search",
            "user-apps",
        ] {
            assert!(
                !lite.contains(forbidden),
                "Lite must not include {forbidden}"
            );
        }
    }

    #[test]
    fn lite_keeps_the_busibox_core() {
        // Identity, control plane, portal, proxy, db, object store, LLM
        // gateway — these are the smallest *true* core.
        let lite = BusiboxProfile::Lite.services();
        for required in &[
            "authz",
            "config",
            "deploy",
            "core-apps",
            "nginx",
            "postgres",
            "minio",
            "litellm",
            "agent",
        ] {
            assert!(lite.contains(required), "Lite must include {required}");
        }
    }

    #[test]
    fn standard_adds_milvus_rag() {
        let s = BusiboxProfile::Standard.services();
        assert!(s.contains(&"milvus"));
        assert!(s.contains(&"embedding"));
        assert!(s.contains(&"search"));
        // ...but still no local LLM serving.
        assert!(!s.contains(&"vllm"));
        assert!(!s.contains(&"neo4j"));
    }

    #[test]
    fn full_contains_everything_each_smaller_profile_does() {
        let full: Vec<&'static str> = BusiboxProfile::Full.services().to_vec();
        for p in &[BusiboxProfile::Lite, BusiboxProfile::Standard] {
            for svc in p.services() {
                assert!(
                    full.contains(svc),
                    "Full must be a superset of {p}: missing {svc}"
                );
            }
        }
    }

    #[test]
    fn excluded_services_round_trip() {
        let excluded = BusiboxProfile::Lite.excluded_services();
        assert!(excluded.contains(&"vllm"));
        assert!(excluded.contains(&"milvus"));
        assert!(excluded.contains(&"neo4j"));
        // Standard excludes less than Lite.
        assert!(
            BusiboxProfile::Standard.excluded_services().len()
                < BusiboxProfile::Lite.excluded_services().len()
        );
        // Full excludes nothing.
        assert!(BusiboxProfile::Full.excluded_services().is_empty());
    }

    #[test]
    fn parse_profile_names() {
        assert_eq!(
            "lite".parse::<BusiboxProfile>().unwrap(),
            BusiboxProfile::Lite
        );
        assert_eq!(
            "LITE".parse::<BusiboxProfile>().unwrap(),
            BusiboxProfile::Lite
        );
        assert_eq!(
            "standard".parse::<BusiboxProfile>().unwrap(),
            BusiboxProfile::Standard
        );
        assert_eq!(
            "std".parse::<BusiboxProfile>().unwrap(),
            BusiboxProfile::Standard
        );
        assert_eq!(
            "full".parse::<BusiboxProfile>().unwrap(),
            BusiboxProfile::Full
        );
        assert_eq!(
            "all".parse::<BusiboxProfile>().unwrap(),
            BusiboxProfile::Full
        );
        assert!("bogus".parse::<BusiboxProfile>().is_err());
    }

    #[test]
    fn parse_addon_packs() {
        assert_eq!(
            "local-models".parse::<AddonPack>().unwrap(),
            AddonPack::LocalModels
        );
        assert_eq!("graph".parse::<AddonPack>().unwrap(), AddonPack::Graph);
        assert_eq!("neo4j".parse::<AddonPack>().unwrap(), AddonPack::Graph);
        assert_eq!(
            "rag-milvus".parse::<AddonPack>().unwrap(),
            AddonPack::RagMilvus
        );
        assert!("nope".parse::<AddonPack>().is_err());
    }

    #[test]
    fn placeholder_packs_have_no_services() {
        for p in [AddonPack::Media, AddonPack::Fleet, AddonPack::RagQdrant] {
            assert!(p.services().is_empty(), "{p} should have no services");
            assert!(p.is_placeholder(), "{p} should be flagged as placeholder");
        }
    }

    #[test]
    fn implemented_packs_are_not_placeholders() {
        for p in [
            AddonPack::LocalModels,
            AddonPack::Graph,
            AddonPack::RagMilvus,
        ] {
            assert!(
                !p.is_placeholder(),
                "{p} is wired and should not be a placeholder"
            );
            assert!(!p.services().is_empty(), "{p} should provide services");
        }
    }

    #[test]
    fn resolve_services_appends_packs_dedup() {
        let result = resolve_services(
            BusiboxProfile::Lite,
            &[AddonPack::RagMilvus, AddonPack::Graph],
        );
        // Lite base preserved at the front.
        assert_eq!(result[0], "nginx");
        // Pack additions appear.
        assert!(result.contains(&"milvus"));
        assert!(result.contains(&"neo4j"));
        assert!(result.contains(&"embedding"));
        // No duplicates.
        let mut sorted = result.clone();
        sorted.sort();
        sorted.dedup();
        assert_eq!(sorted.len(), result.len());
    }

    #[test]
    fn resolve_services_lite_plus_rag_milvus_matches_standard_shape() {
        // Adding the rag-milvus pack to Lite should yield the same service
        // set as Standard (order may differ).
        let mut a = resolve_services(BusiboxProfile::Lite, &[AddonPack::RagMilvus]);
        let mut b: Vec<&'static str> = BusiboxProfile::Standard.services().to_vec();
        a.sort();
        b.sort();
        assert_eq!(a, b);
    }

    #[test]
    fn cloud_key_required_for_lite_and_standard() {
        assert!(BusiboxProfile::Lite.requires_cloud_llm_key());
        assert!(BusiboxProfile::Standard.requires_cloud_llm_key());
        assert!(!BusiboxProfile::Full.requires_cloud_llm_key());
    }

    #[test]
    fn all_services_in_presets_are_recognized_by_registry() {
        // Every service name we emit must round-trip through the existing
        // service registry — otherwise we'd be promising a backend that
        // can't be deployed.
        use crate::services::container_for_service;
        for p in BusiboxProfile::all() {
            for svc in p.services() {
                assert!(
                    container_for_service(svc).is_some(),
                    "profile {p} names unknown service '{svc}'"
                );
            }
        }
        for pack in AddonPack::all() {
            for svc in pack.services() {
                assert!(
                    container_for_service(svc).is_some(),
                    "pack {pack} names unknown service '{svc}'"
                );
            }
        }
    }
}
