use crate::app::{App, ManageUpdate, MessageKind, Screen, ServiceStatus};
use crate::modules::remote;
use crate::theme;
use crossterm::event::{KeyCode, KeyEvent};
use ratatui::layout::Margin;
use ratatui::prelude::*;
use ratatui::widgets::{Scrollbar, ScrollbarOrientation, ScrollbarState, *};

const SPINNER: &[&str] = &["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"];

fn shell_escape(s: &str) -> String {
    format!("'{}'", s.replace('\'', "'\\''"))
}

/// Map display name to make SERVICE= value for manage commands.
fn service_to_make_name(display_name: &str) -> &str {
    match display_name {
        "portal" => "busibox-portal",
        "admin" => "busibox-admin",
        "agents" => "busibox-agents",
        "chat" => "busibox-chat",
        "appbuilder" => "busibox-appbuilder",
        "media" => "busibox-media",
        "documents" => "busibox-documents",
        other => other,
    }
}

/// Map display name → Docker container suffix for `docker inspect`.
/// Returns None for services that don't have a single container (e.g. infra or frontend sub-apps).
fn service_to_docker_container(display_name: &str) -> Option<&'static str> {
    match display_name {
        "postgres" => Some("postgres"),
        "redis" => Some("redis"),
        "minio" => Some("minio"),
        "milvus" => Some("milvus"),
        "neo4j" => Some("neo4j"),
        "authz" => Some("authz-api"),
        "agent" => Some("agent-api"),
        "data" => Some("data-api"),
        "data-worker" => Some("data-worker"),
        "search" => Some("search-api"),
        "deploy" => Some("deploy-api"),
        "docs" => Some("docs-api"),
        "embedding" => Some("embedding-api"),
        "bridge" => Some("bridge-api"),
        "config" => Some("config-api"),
        "litellm" => Some("litellm"),
        "vllm" => Some("vllm"),
        "mlx" => Some("mlx"),
        "proxy" => Some("proxy"),
        "core-apps" => Some("core-apps"),
        "user-apps" => Some("user-apps"),
        _ => None,
    }
}

/// Map display name → Proxmox `.deploy_version` file path.
/// Only API services that Ansible deploys from the busibox repo have these.
/// Returns (production_container_id, file_path) for each service with a `.deploy_version` file.
/// On Proxmox the file lives *inside* the LXC container, so we need `pct exec <ctid>`.
/// Production CTIDs are 2xx; staging adds +100 (3xx).
fn service_to_deploy_version_info(display_name: &str) -> Option<(u32, &'static str)> {
    match display_name {
        // API services (busibox repo, .deploy_version)
        "authz"       => Some((210, "/opt/authz/.deploy_version")),
        "agent"       => Some((202, "/opt/agent-api/.deploy_version")),
        "data"        => Some((206, "/srv/data/.deploy_version_api")),
        "data-worker" => Some((206, "/srv/data/.deploy_version_worker")),
        "search"      => Some((204, "/opt/search-api/.deploy_version")),
        "deploy"      => Some((210, "/opt/deploy/.deploy_version")),
        "docs"        => Some((202, "/srv/docs/.deploy_version")),
        "embedding"   => Some((206, "/opt/embedding/.deploy_version")),
        "config"      => Some((210, "/opt/config/.deploy_version")),
        // Infrastructure services
        "minio"       => Some((205, "/opt/minio/.deploy_version")),
        "litellm"     => Some((207, "/opt/litellm/.deploy_version")),
        "bridge"      => Some((211, "/srv/bridge/.deploy_version")),
        // Frontend apps (busibox-frontend repo, .deployed-version written by app_deployer)
        "portal"      => Some((201, "/srv/apps/busibox-portal/.deployed-version")),
        "admin"       => Some((201, "/srv/apps/busibox-admin/.deployed-version")),
        "agents"      => Some((201, "/srv/apps/busibox-agents/.deployed-version")),
        "chat"        => Some((201, "/srv/apps/busibox-chat/.deployed-version")),
        "appbuilder"  => Some((201, "/srv/apps/busibox-appbuilder/.deployed-version")),
        "media"       => Some((201, "/srv/apps/busibox-media/.deployed-version")),
        "documents"   => Some((201, "/srv/apps/busibox-documents/.deployed-version")),
        _ => None,
    }
}

/// Resolve the actual CTID for a service given the environment.
/// Production uses base IDs (2xx), staging adds +100 (3xx).
fn resolve_ctid(base_ctid: u32, is_staging: bool) -> u32 {
    if is_staging { base_ctid + 100 } else { base_ctid }
}

/// Parsed deployment version info from a `.deploy_version` JSON blob.
struct DeployVersionInfo {
    commit: String,
    branch: String,
}

/// Extract the git commit SHA and branch from a `.deploy_version` JSON blob.
/// Tries `git_commit` first (agent uses a content-hash for `commit`), then `commit`.
fn extract_deploy_version_info(json_str: &str) -> Option<DeployVersionInfo> {
    if let Ok(v) = serde_json::from_str::<serde_json::Value>(json_str) {
        let commit = v.get("git_commit")
            .and_then(|v| v.as_str())
            .filter(|s| !s.trim().is_empty() && s.trim() != "unknown")
            .or_else(|| v.get("commit")
                .and_then(|v| v.as_str())
                .filter(|s| !s.trim().is_empty() && s.trim() != "unknown"))
            .map(|s| s.trim().to_string())?;

        let branch = v.get("branch")
            .and_then(|v| v.as_str())
            .filter(|s| !s.trim().is_empty() && s.trim() != "unknown")
            .unwrap_or("main")
            .trim()
            .to_string();

        Some(DeployVersionInfo { commit, branch })
    } else {
        None
    }
}

/// Definition for querying upstream service versions.
struct UpstreamVersionDef {
    name: &'static str,
    /// Production Proxmox CTID (staging adds +100).
    ctid: u32,
    /// Shell command to get the running version (run inside the container).
    version_cmd: &'static str,
    /// GitHub owner/repo for checking latest releases.
    github_repo: &'static str,
    /// Prefix to strip from GitHub release tags (e.g. "v" or "RELEASE.").
    tag_prefix: &'static str,
    /// If true, only consider stable releases (even minor version numbers).
    /// Used for nginx where odd = mainline, even = stable.
    stable_only: bool,
}

const UPSTREAM_SERVICES: &[UpstreamVersionDef] = &[
    UpstreamVersionDef {
        name: "postgres",
        ctid: 203,
        version_cmd: "postgres --version 2>/dev/null | grep -oE '[0-9]+\\.[0-9]+'",
        github_repo: "postgres/postgres",
        tag_prefix: "REL_",
        stable_only: false,
    },
    UpstreamVersionDef {
        name: "redis",
        ctid: 206,
        version_cmd: "redis-server --version 2>/dev/null | grep -oE '[0-9]+\\.[0-9]+\\.[0-9]+'",
        github_repo: "redis/redis",
        tag_prefix: "",
        stable_only: false,
    },
    UpstreamVersionDef {
        name: "minio",
        ctid: 205,
        version_cmd: "docker exec $(docker ps -q --filter name=minio) minio --version 2>/dev/null | grep -oE 'RELEASE\\.[0-9T-]+Z' | head -1",
        github_repo: "minio/minio",
        tag_prefix: "",
        stable_only: false,
    },
    UpstreamVersionDef {
        name: "milvus",
        ctid: 204,
        version_cmd: "docker exec $(docker ps -q --filter name=milvus-standalone) milvus version 2>/dev/null | head -1 | grep -oE 'v[0-9]+\\.[0-9]+\\.[0-9]+'",
        github_repo: "milvus-io/milvus",
        tag_prefix: "v",
        stable_only: false,
    },
    UpstreamVersionDef {
        name: "neo4j",
        ctid: 213,
        version_cmd: "docker exec $(docker ps -q --filter name=neo4j) neo4j version 2>/dev/null | grep -oE '[0-9]+\\.[0-9]+\\.[0-9]+'",
        github_repo: "neo4j/neo4j",
        tag_prefix: "",
        stable_only: false,
    },
    UpstreamVersionDef {
        name: "litellm",
        ctid: 207,
        version_cmd: "/opt/litellm/venv/bin/pip show litellm 2>/dev/null | grep -i '^Version:' | awk '{print $2}'",
        github_repo: "BerriAI/litellm",
        tag_prefix: "v",
        stable_only: false,
    },
    UpstreamVersionDef {
        name: "vllm",
        ctid: 208,
        version_cmd: "python3 -c 'import vllm; print(vllm.__version__)' 2>/dev/null || /opt/vllm/venv/bin/python -c 'import vllm; print(vllm.__version__)' 2>/dev/null",
        github_repo: "vllm-project/vllm",
        tag_prefix: "v",
        stable_only: false,
    },
    UpstreamVersionDef {
        name: "mlx",
        ctid: 208,
        version_cmd: "python3 -c 'import mlx_lm; print(mlx_lm.__version__)' 2>/dev/null || echo unknown",
        github_repo: "ml-explore/mlx-lm",
        tag_prefix: "v",
        stable_only: false,
    },
    UpstreamVersionDef {
        name: "proxy",
        ctid: 200,
        version_cmd: "nginx -v 2>&1 | grep -oE '[0-9]+\\.[0-9]+\\.[0-9]+'",
        github_repo: "nginx/nginx",
        tag_prefix: "release-",
        stable_only: true,
    },
];

/// Check if a service is an upstream (non-busibox) service.
fn is_upstream_service(name: &str) -> bool {
    UPSTREAM_SERVICES.iter().any(|u| u.name == name)
}

/// Returns true if the version string looks like a pre-release (RC, alpha, beta, nightly, dev).
/// Handles both hyphen-separated (`1.2.3-rc.1`) and dot-separated (`1.2.3.rc.1`) conventions.
fn is_prerelease_version(v: &str) -> bool {
    let lower = v.to_lowercase();
    // Check for common pre-release indicators anywhere in the version string
    for marker in &["rc", "alpha", "beta", "nightly", "dev", "pre", "snapshot", "canary"] {
        // Match as a distinct segment: preceded by `.` or `-` and followed by `.`, `-`, digit, or end
        for sep in &['.', '-', '_'] {
            let with_sep = format!("{sep}{marker}");
            if lower.contains(&with_sep) {
                return true;
            }
        }
    }
    false
}

/// Parse a semver-like version string into (major, minor, patch) for comparison.
/// Strips pre-release/build metadata after `-` or `+` (e.g. `1.82.3-stable.patch.4` -> `1.82.3`).
fn parse_semver(v: &str) -> Option<(u32, u32, u32)> {
    let v = v.trim().trim_start_matches('v');
    let base = v.split(['-', '+']).next().unwrap_or(v);
    let parts: Vec<&str> = base.split('.').collect();
    if parts.len() >= 2 {
        let major = parts[0].parse().ok()?;
        let minor = parts[1].parse().ok()?;
        let patch = if parts.len() >= 3 { parts[2].parse().unwrap_or(0) } else { 0 };
        Some((major, minor, patch))
    } else {
        None
    }
}

/// Compare two semver versions. Returns true if `latest` is a newer minor/patch
/// within the same major version as `current`.
pub fn is_upstream_update_available_pub(current: &str, latest: &str) -> bool {
    is_upstream_update_available(current, latest)
}

/// Returns true if the deployed version does NOT match the available version
/// (i.e. an update is needed). Handles SHA prefix comparison and upstream semver.
pub fn compute_needs_update(svc: &crate::app::ServiceStatus) -> bool {
    if svc.available_version.is_empty() {
        return false;
    }
    if svc.source_repo == "upstream" {
        if svc.version.is_empty() {
            return false;
        }
        return is_upstream_update_available(&svc.version, &svc.available_version);
    }
    // If deployed version is unknown we can't determine update status
    if svc.version.is_empty() {
        return false;
    }
    let min_len = svc.version.len().min(svc.available_version.len());
    if min_len < 4 {
        return false;
    }
    svc.version[..min_len] != svc.available_version[..min_len]
}

fn is_upstream_update_available(current: &str, latest: &str) -> bool {
    match (parse_semver(current), parse_semver(latest)) {
        (Some((cmaj, cmin, cpat)), Some((lmaj, lmin, lpat))) => {
            if cmaj != lmaj {
                return false; // different major - don't flag
            }
            (lmin, lpat) > (cmin, cpat)
        }
        _ => false,
    }
}

/// Format the "Secrets" column text and style.
fn format_secrets_cell(svc: &ServiceStatus) -> (String, Style) {
    let s = &svc.secrets_status;
    if s == "synced" {
        ("synced".to_string(), theme::success())
    } else if s.starts_with("mismatch") {
        (s.clone(), theme::error())
    } else if s == "down" {
        ("down".to_string(), theme::warning())
    } else if s == "checking..." {
        ("\u{2026}".to_string(), theme::dim())
    } else if s == "no vault pw" {
        ("no vault pw".to_string(), theme::dim())
    } else if s == "error" || s.starts_with("err:") {
        (s.clone(), theme::error())
    } else {
        ("\u{2014}".to_string(), theme::dim())
    }
}

/// Format the "Deployed" column text and style.
fn format_deployed_cell(svc: &ServiceStatus) -> (String, Style) {
    if svc.version.is_empty() {
        if svc.source_repo == "upstream" {
            return ("…".to_string(), theme::dim());
        }
        return ("—".to_string(), theme::dim());
    }
    if svc.source_repo == "upstream" {
        return (svc.version.clone(), theme::muted());
    }
    if !svc.deployed_ref.is_empty() {
        (format!("{}@{}", svc.deployed_ref, svc.version), theme::muted())
    } else {
        (svc.version.clone(), theme::muted())
    }
}

/// Format the "Available" column text and style.
fn format_available_cell(svc: &ServiceStatus) -> (String, Style) {
    if svc.source_repo == "upstream" {
        return format_available_cell_upstream(svc);
    }
    if svc.available_version.is_empty() {
        return ("…".to_string(), theme::dim());
    }
    if svc.version.is_empty() {
        let text = if !svc.available_ref.is_empty() {
            format!("{}@{}", svc.available_ref, svc.available_version)
        } else {
            svc.available_version.clone()
        };
        return (text, theme::dim());
    }
    // Check if same SHA (normalize to shorter length for prefix comparison)
    let min_len = svc.version.len().min(svc.available_version.len());
    let version_matches = min_len >= 4
        && svc.version[..min_len] == svc.available_version[..min_len];
    if version_matches {
        return ("✓ current".to_string(), theme::success());
    }
    // New release available
    if !svc.available_ref.is_empty() && svc.available_ref != svc.deployed_ref {
        return (format!("↑ {}", svc.available_ref), theme::warning());
    }
    // Behind on same branch
    let text = if !svc.available_ref.is_empty() {
        format!("{}@{}", svc.available_ref, svc.available_version)
    } else {
        svc.available_version.clone()
    };
    match svc.commits_behind {
        Some(n) if n > 0 => (format!("{text} (↑{n})"), theme::warning()),
        _ => (text, theme::warning()),
    }
}

fn format_available_cell_upstream(svc: &ServiceStatus) -> (String, Style) {
    if svc.available_version.is_empty() {
        return ("…".to_string(), theme::dim());
    }
    if svc.version.is_empty() {
        return (format!("latest: {}", svc.available_version), theme::dim());
    }
    if svc.version == svc.available_version {
        return ("✓ current".to_string(), theme::success());
    }
    if is_upstream_update_available(&svc.version, &svc.available_version) {
        return (format!("↑ {}", svc.available_version), theme::warning());
    }
    ("✓ current".to_string(), theme::success())
}

/// (group, name, source_repo)
fn get_all_services(app: &App) -> Vec<(&'static str, String, &'static str)> {
    use crate::modules::hardware::LlmBackend;

    let mut services: Vec<(&str, String, &str)> = vec![
        ("Infrastructure", "postgres".to_string(), "upstream"),
        ("Infrastructure", "redis".to_string(), "upstream"),
        ("Infrastructure", "minio".to_string(), "upstream"),
        ("Infrastructure", "milvus".to_string(), "upstream"),
        ("Infrastructure", "neo4j".to_string(), "upstream"),
        ("APIs", "authz".to_string(), "busibox"),
        ("APIs", "agent".to_string(), "busibox"),
        ("APIs", "data".to_string(), "busibox"),
        ("APIs", "data-worker".to_string(), "busibox"),
        ("APIs", "search".to_string(), "busibox"),
        ("APIs", "deploy".to_string(), "busibox"),
        ("APIs", "docs".to_string(), "busibox"),
        ("APIs", "embedding".to_string(), "busibox"),
        ("APIs", "bridge".to_string(), "busibox"),
        ("APIs", "config".to_string(), "busibox"),
    ];

    let profile = app.active_profile().map(|(_, p)| p);
    let is_remote = profile.map(|p| p.remote).unwrap_or(false);
    let hw = if is_remote {
        app.remote_hardware
            .as_ref()
            .or_else(|| profile.and_then(|p| p.hardware.as_ref()))
    } else {
        app.local_hardware.as_ref()
    };
    let is_mlx = hw
        .map(|h| matches!(h.llm_backend, LlmBackend::Mlx))
        .unwrap_or(false);

    services.push(("LLM", "litellm".to_string(), "upstream"));
    if is_mlx {
        services.push(("LLM", "mlx".to_string(), "upstream"));
    } else {
        services.push(("LLM", "vllm".to_string(), "upstream"));
    }

    services.push(("Frontend", "proxy".to_string(), "upstream"));
    services.push(("Frontend", "core-apps".to_string(), "busibox-frontend"));
    services.push(("Frontend", "user-apps".to_string(), "busibox"));
    services.push(("Frontend", "custom-services".to_string(), "busibox"));
    services.push(("Frontend", "portal".to_string(), "busibox-frontend"));
    services.push(("Frontend", "admin".to_string(), "busibox-frontend"));
    services.push(("Frontend", "agents".to_string(), "busibox-frontend"));
    services.push(("Frontend", "chat".to_string(), "busibox-frontend"));
    services.push(("Frontend", "appbuilder".to_string(), "busibox-frontend"));
    services.push(("Frontend", "media".to_string(), "busibox-frontend"));
    services.push(("Frontend", "documents".to_string(), "busibox-frontend"));

    services.push(("CLI", "busibox-cli".to_string(), "busibox"));
    services
}

/// Map a service display name to the subdirectory paths to check for changes.
/// Returns paths relative to the repo root. If shared code changed, caller must cascade.
fn service_to_source_paths(name: &str) -> Vec<&'static str> {
    match name {
        "authz" => vec!["srv/authz/", "provision/ansible/roles/authz/"],
        "agent" => vec!["srv/agent/", "provision/ansible/roles/agent_api/"],
        "data" | "data-worker" => vec!["srv/data/", "provision/ansible/roles/data_api/"],
        "search" => vec!["srv/search/", "provision/ansible/roles/search_api/"],
        "deploy" => vec!["srv/deploy/", "provision/ansible/roles/deploy_api/"],
        "docs" => vec!["srv/docs/", "provision/ansible/roles/docs_api/"],
        "embedding" => vec!["srv/embedding/", "provision/ansible/roles/embedding_api/"],
        "bridge" => vec!["srv/bridge/"],
        "config" => vec!["srv/config/", "provision/ansible/roles/config_api/"],
        "litellm" => vec!["provision/ansible/roles/litellm/"],
        "vllm" | "mlx" => vec!["provision/ansible/roles/vllm/", "provision/ansible/roles/mlx/"],
        "postgres" => vec!["provision/ansible/roles/postgres/"],
        "redis" => vec!["provision/ansible/roles/redis/"],
        "minio" => vec!["provision/ansible/roles/minio/"],
        "milvus" => vec!["provision/ansible/roles/milvus/"],
        "neo4j" => vec!["provision/ansible/roles/neo4j/"],
        "proxy" => vec!["provision/ansible/roles/proxy/"],
        // Frontend apps - paths in busibox-frontend repo
        "portal" => vec!["apps/portal/"],
        "admin" => vec!["apps/admin/"],
        "agents" => vec!["apps/agents/"],
        "chat" => vec!["apps/chat/"],
        "appbuilder" => vec!["apps/appbuilder/"],
        "media" => vec!["apps/media/"],
        "documents" => vec!["apps/documents/"],
        "core-apps" => vec!["apps/", "packages/"],
        "custom-services" => vec!["provision/ansible/roles/custom_services/"],
        "busibox-cli" => vec!["cli/"],
        _ => vec![],
    }
}


pub fn render(f: &mut Frame, app: &App) {
    if app.manage_log_visible {
        render_log_viewer(f, app);
        return;
    }

    let chunks = Layout::default()
        .direction(Direction::Vertical)
        .constraints([
            Constraint::Length(3),
            Constraint::Min(12),
            Constraint::Length(3),
        ])
        .margin(2)
        .split(f.area());

    let title = Paragraph::new("Service Management")
        .style(theme::title())
        .alignment(Alignment::Center);
    f.render_widget(title, chunks[0]);

    if app.manage_services.is_empty() {
        let msg = Paragraph::new("Loading service status...")
            .style(theme::info())
            .alignment(Alignment::Center)
            .block(
                Block::default()
                    .borders(Borders::ALL)
                    .border_style(theme::dim())
                    .title(" Services ")
                    .title_style(theme::heading()),
            );
        f.render_widget(msg, chunks[1]);
    } else {
        let update_count: usize = app.manage_services.iter().filter(|s| s.needs_update).count();

        let rows: Vec<Row> = app
            .manage_services
            .iter()
            .enumerate()
            .map(|(i, svc)| {
                let status_style = if svc.status == "healthy" {
                    theme::success()
                } else if svc.status == "unhealthy" {
                    theme::warning()
                } else if svc.status == "down" {
                    theme::error()
                } else if svc.status == "checking..." {
                    theme::dim()
                } else {
                    theme::muted()
                };

                let (deployed_text, deployed_style) = format_deployed_cell(svc);
                let (available_text, available_style) = format_available_cell(svc);
                let (secrets_text, secrets_style) = format_secrets_cell(svc);

                let row_style = if i == app.manage_selected {
                    theme::selected()
                } else {
                    Style::default()
                };

                Row::new(vec![
                    Cell::from(svc.group.clone()).style(theme::muted()),
                    Cell::from(svc.name.clone()).style(theme::normal()),
                    Cell::from(svc.status.clone()).style(status_style),
                    Cell::from(deployed_text).style(deployed_style),
                    Cell::from(available_text).style(available_style),
                    Cell::from(secrets_text).style(secrets_style),
                ])
                .style(row_style)
            })
            .collect();

        let table_height = chunks[1].height.saturating_sub(4) as usize;
        let total_rows = rows.len();
        let scroll_offset = if app.manage_selected >= table_height {
            app.manage_selected - table_height + 1
        } else {
            0
        };
        let visible_rows: Vec<Row> = rows
            .into_iter()
            .skip(scroll_offset)
            .take(table_height)
            .collect();

        let scroll_info = if total_rows > table_height {
            format!(
                " {}-{}/{} ",
                scroll_offset + 1,
                (scroll_offset + table_height).min(total_rows),
                total_rows
            )
        } else {
            String::new()
        };

        let title_suffix = if update_count > 0 {
            format!(" — {update_count} update(s) available")
        } else {
            String::new()
        };

        let table = Table::new(
            visible_rows,
            [
                Constraint::Length(14),
                Constraint::Length(16),
                Constraint::Length(10),
                Constraint::Length(10),
                Constraint::Length(14),
                Constraint::Min(20),
            ],
        )
        .header(
            Row::new(vec![
                Cell::from("Group").style(theme::muted()),
                Cell::from("Service").style(theme::muted()),
                Cell::from("Status").style(theme::muted()),
                Cell::from("Deployed").style(theme::muted()),
                Cell::from("Available").style(theme::muted()),
                Cell::from("Secrets").style(theme::muted()),
            ])
            .bottom_margin(1),
        )
        .block(
            Block::default()
                .borders(Borders::ALL)
                .border_style(theme::dim())
                .title(format!(" Services{scroll_info}{title_suffix}"))
                .title_style(theme::heading()),
        );
        f.render_widget(table, chunks[1]);

        if total_rows > table_height {
            let mut scrollbar_state = ScrollbarState::new(total_rows)
                .position(scroll_offset);
            let scrollbar = Scrollbar::new(ScrollbarOrientation::VerticalRight)
                .begin_symbol(Some("↑"))
                .end_symbol(Some("↓"));
            f.render_stateful_widget(
                scrollbar,
                chunks[1].inner(Margin { vertical: 1, horizontal: 0 }),
                &mut scrollbar_state,
            );
        }
    }

    let mut help_spans: Vec<Span> = Vec::new();
    if app.manage_services.is_empty() {
        help_spans.push(Span::styled(" Enter Load  Esc Back", theme::muted()));
    } else {
        let update_count: usize = app.manage_services.iter()
            .filter(|s| {
                s.needs_update
                && !["user-apps", "busibox-cli", "core-apps"].contains(&s.name.as_str())
                && (s.source_repo != "upstream" || is_busibox_managed_upstream(&s.name))
            })
            .count();
        help_spans.push(Span::styled("f Fetch  u Update  ", theme::muted()));
        if update_count > 0 {
            help_spans.push(Span::styled(
                format!("U Update All ({update_count})  "),
                theme::warning(),
            ));
        } else {
            help_spans.push(Span::styled("U Update All  ", theme::dim()));
        }
        help_spans.push(Span::styled(
            "r Restart  l Logs  s Stop/Start  t Tunnel  Esc Back",
            theme::muted(),
        ));
    }
    if app.ssh_tunnel_active {
        help_spans.push(Span::styled("  🔗 tunnel:4443", theme::success()));
    }
    let help = Paragraph::new(Line::from(help_spans));
    f.render_widget(help, chunks[2]);
}

fn render_log_viewer(f: &mut Frame, app: &App) {
    let chunks = Layout::default()
        .direction(Direction::Vertical)
        .constraints([
            Constraint::Length(3),
            Constraint::Length(1),
            Constraint::Min(6),
            Constraint::Length(3),
        ])
        .margin(2)
        .split(f.area());

    let svc_name = app
        .manage_services
        .get(app.manage_selected)
        .map(|s| s.name.as_str())
        .unwrap_or("service");

    let title_text = if app.manage_log_streaming {
        format!("Live Logs — {svc_name}")
    } else {
        format!("Action Log — {svc_name}")
    };
    let title = Paragraph::new(title_text)
        .style(theme::title())
        .alignment(Alignment::Center);
    f.render_widget(title, chunks[0]);

    let tick = app.manage_tick;
    let spinner_char = SPINNER[tick % SPINNER.len()];

    let subtitle = if app.manage_waiting_confirm.is_some() {
        Paragraph::new(Line::from(vec![
            Span::styled("? ", theme::warning()),
            Span::styled(&app.manage_confirm_prompt, theme::warning()),
            Span::styled("  [y/n]", theme::muted()),
        ]))
        .alignment(Alignment::Center)
    } else if app.manage_log_streaming && app.manage_action_running {
        Paragraph::new(Line::from(vec![
            Span::styled(format!("{spinner_char} "), theme::info()),
            Span::styled("Streaming live logs...", theme::info()),
        ]))
        .alignment(Alignment::Center)
    } else if app.manage_action_running {
        Paragraph::new(Line::from(vec![
            Span::styled(format!("{spinner_char} "), theme::info()),
            Span::styled("Running...", theme::info()),
        ]))
        .alignment(Alignment::Center)
    } else if app.manage_action_complete {
        let last = app.manage_log.last().map(|s| s.as_str()).unwrap_or("");
        if last.contains("ERROR") || last.contains("FAILED") || last.contains("failed") {
            Paragraph::new("Action failed")
                .style(theme::error())
                .alignment(Alignment::Center)
        } else {
            Paragraph::new("Action complete")
                .style(theme::success())
                .alignment(Alignment::Center)
        }
    } else {
        Paragraph::new("").alignment(Alignment::Center)
    };
    f.render_widget(subtitle, chunks[1]);

    let log_height = chunks[2].height.saturating_sub(2) as usize;
    let max_scroll = app.manage_log.len().saturating_sub(log_height);
    let scroll = app.manage_log_scroll.min(max_scroll);

    let visible: Vec<Line> = app
        .manage_log
        .iter()
        .skip(scroll)
        .take(log_height)
        .map(|l| {
            let style = if l.contains("ERROR") || l.contains("FAILED") {
                theme::error()
            } else if l.contains("✓") || l.contains("SUCCESS") || l.contains("successful") {
                theme::success()
            } else if l.starts_with("Deploying") || l.starts_with("Running") {
                theme::info()
            } else {
                theme::normal()
            };
            Line::from(Span::styled(l.as_str(), style))
        })
        .collect();

    let scrollbar_info = if app.manage_log.len() > log_height {
        format!(
            " Log ({}-{} of {}) ",
            scroll + 1,
            (scroll + log_height).min(app.manage_log.len()),
            app.manage_log.len()
        )
    } else {
        " Log ".to_string()
    };

    let log_panel = Paragraph::new(visible).block(
        Block::default()
            .borders(Borders::ALL)
            .border_style(theme::dim())
            .title(scrollbar_info)
            .title_style(theme::heading()),
    );
    f.render_widget(log_panel, chunks[2]);

    if app.manage_log.len() > log_height {
        let mut scrollbar_state =
            ScrollbarState::new(app.manage_log.len()).position(scroll);
        let scrollbar = Scrollbar::new(ScrollbarOrientation::VerticalRight)
            .begin_symbol(Some("↑"))
            .end_symbol(Some("↓"));
        f.render_stateful_widget(
            scrollbar,
            chunks[2].inner(Margin {
                vertical: 1,
                horizontal: 0,
            }),
            &mut scrollbar_state,
        );
    }

    let help_text = if app.manage_waiting_confirm.is_some() {
        " y Yes (regenerate remote -> replace local)  n No (keep local saved config)  ↑/↓ Scroll"
    } else if app.manage_log_streaming && app.manage_action_running {
        " ↑/↓ Scroll  End Auto-scroll  c Copy  Esc Stop tailing"
    } else if app.manage_action_running {
        " ↑/↓ Scroll  (waiting for action to complete...)"
    } else {
        " ↑/↓ Scroll  c Copy  Esc Close"
    };
    let help = Paragraph::new(Line::from(Span::styled(help_text, theme::muted())));
    f.render_widget(help, chunks[3]);
}

pub fn handle_key(app: &mut App, key: KeyEvent) {
    if app.manage_log_visible {
        handle_log_viewer_key(app, key);
        return;
    }

    match key.code {
        KeyCode::Esc => {
            app.screen = Screen::Welcome;
            app.menu_selected = 0;
            crate::screens::welcome::trigger_health_checks(app);
        }
        KeyCode::Up | KeyCode::Char('k') => {
            if app.manage_selected > 0 {
                app.manage_selected -= 1;
            }
        }
        KeyCode::Down | KeyCode::Char('j') => {
            if app.manage_selected < app.manage_services.len().saturating_sub(1) {
                app.manage_selected += 1;
            }
        }
        KeyCode::Enter | KeyCode::Char('f') => {
            load_service_status(app);
        }
        KeyCode::Char('r') => {
            run_action(app, "restart");
        }
        KeyCode::Char('l') => {
            if app.manage_log_streaming {
                app.manage_log_visible = true;
            } else {
                spawn_log_tail_worker(app);
            }
        }
        KeyCode::Char('s') => {
            let current_status = app
                .manage_services
                .get(app.manage_selected)
                .map(|s| s.status.clone())
                .unwrap_or_default();
            if current_status == "healthy" || current_status == "running" {
                run_action(app, "stop");
            } else {
                run_action(app, "start");
            }
        }
        KeyCode::Char('u') => {
            run_update_selected(app);
        }
        KeyCode::Char('U') => {
            run_update_all(app);
        }
        KeyCode::Char('t') => {
            app.toggle_ssh_tunnel();
        }
        _ => {}
    }
}

fn handle_log_viewer_key(app: &mut App, key: KeyEvent) {
    if let Some(sender) = app.manage_waiting_confirm.take() {
        match key.code {
            KeyCode::Char('y') | KeyCode::Char('Y') => {
                let _ = sender.send(true);
                app.manage_confirm_prompt.clear();
                return;
            }
            KeyCode::Char('n') | KeyCode::Char('N') => {
                let _ = sender.send(false);
                app.manage_confirm_prompt.clear();
                return;
            }
            KeyCode::Up | KeyCode::Char('k') => {
                if app.manage_log_scroll > 0 {
                    app.manage_log_scroll -= 1;
                }
                app.manage_waiting_confirm = Some(sender);
                return;
            }
            KeyCode::Down | KeyCode::Char('j') => {
                app.manage_log_scroll += 1;
                app.manage_waiting_confirm = Some(sender);
                return;
            }
            _ => {
                app.manage_waiting_confirm = Some(sender);
                return;
            }
        }
    }

    match key.code {
        KeyCode::Esc | KeyCode::Char('q') => {
            let was_streaming = app.manage_log_streaming;
            kill_log_stream(app);
            app.manage_log_visible = false;
            if !was_streaming {
                load_service_status(app);
            }
        }
        KeyCode::Up | KeyCode::Char('k') => {
            app.manage_log_autoscroll = false;
            if app.manage_log_scroll > 0 {
                app.manage_log_scroll -= 1;
            }
        }
        KeyCode::Down | KeyCode::Char('j') => {
            app.manage_log_scroll += 1;
        }
        KeyCode::Home => {
            app.manage_log_autoscroll = false;
            app.manage_log_scroll = 0;
        }
        KeyCode::End => {
            app.manage_log_autoscroll = true;
            app.manage_log_scroll = app.manage_log.len().saturating_sub(1);
        }
        KeyCode::Char('c') => {
            let log_text = app.manage_log.join("\n");
            let _ = copy_to_clipboard(&log_text);
            app.set_message("Log copied to clipboard", MessageKind::Info);
        }
        _ => {}
    }
}

pub fn load_service_status(app: &mut App) {
    use crate::modules::health::{self, HealthStatus};
    use crate::modules::hardware::LlmBackend;
    use crate::screens::install::env_to_prefix;

    app.manage_services.clear();
    for (group, name, source_repo) in get_all_services(app) {
        let is_cli = name == "busibox-cli";
        let cli_sha = if is_cli { env!("GIT_COMMIT") } else { "" };
        app.manage_services.push(ServiceStatus {
            name,
            group: group.to_string(),
            status: if is_cli { "running".into() } else { "checking...".into() },
            version: if is_cli { cli_sha.to_string() } else { String::new() },
            deployed_ref: String::new(),
            deployed_type: String::new(),
            available_version: String::new(),
            available_ref: String::new(),
            commits_behind: None,
            needs_update: false,
            source_repo: source_repo.to_string(),
            secrets_status: "checking...".into(),
        });
    }

    // Get profile info for health checks
    let (profile_id, profile) = match app.active_profile() {
        Some((id, p)) => (id.to_string(), p.clone()),
        None => {
            // No profile - mark all unknown
            for svc in &mut app.manage_services {
                svc.status = "no profile".into();
            }
            return;
        }
    };

    let prefix = env_to_prefix(&profile.environment);
    let is_remote = profile.remote;
    let is_proxmox = profile.backend == "proxmox";
    let hw = if is_remote {
        app.remote_hardware
            .as_ref()
            .or(profile.hardware.as_ref())
    } else {
        app.local_hardware
            .as_ref()
            .or(profile.hardware.as_ref())
    };
    let is_mlx = hw
        .map(|h| matches!(h.llm_backend, LlmBackend::Mlx))
        .unwrap_or(false);

    let host = if is_remote {
        profile.effective_host().unwrap_or("localhost").to_string()
    } else {
        "localhost".to_string()
    };

    let ssh_details = if is_remote {
        let ssh_host = profile.effective_host().unwrap_or("localhost").to_string();
        let ssh_user = profile.effective_user().to_string();
        let ssh_key = profile.effective_ssh_key().to_string();
        Some((ssh_host, ssh_user, ssh_key))
    } else {
        None
    };

    // Use health module for parallel checks
    let (tx, rx) = std::sync::mpsc::channel::<ManageUpdate>();
    app.manage_rx = Some(rx);
    app.manage_action_running = true;

    let service_names: Vec<String> = app.manage_services.iter().map(|s| s.name.clone()).collect();
    let network_base = profile.effective_network_base().to_string();
    let vllm_network_base = profile.vllm_network_base().to_string();
    let repo_root = app.repo_root.clone();

    // Resolve busibox-frontend sibling directory (shared by version + remote threads)
    let frontend_dir: Option<String> = {
        let sibling = app.repo_root.parent().map(|p| p.join("busibox-frontend"));
        sibling.filter(|p| p.exists()).map(|p| p.to_string_lossy().to_string())
    };

    let is_staging = profile.environment.contains("staging");

    // Clone values needed by the version check thread
    let version_tx = tx.clone();
    let version_service_names = service_names.clone();
    let version_is_proxmox = is_proxmox;
    let version_is_remote = is_remote;
    let version_ssh_details = ssh_details.clone();
    let version_prefix = prefix.clone();
    let version_repo_root = repo_root.clone();
    let version_frontend_dir = frontend_dir.clone();
    let version_is_staging = is_staging;

    // Clone values needed by the upstream version threads
    let upstream_tx = tx.clone();
    let upstream_service_names = service_names.clone();
    let upstream_ssh_details = ssh_details.clone();
    let upstream_prefix = prefix.clone();
    let latest_tx = tx.clone();
    let latest_service_names = service_names.clone();

    // Clone values needed by the secrets check thread (must happen before health
    // thread consumes ssh_details/prefix via move closure).
    let secrets_tx = tx.clone();
    let secrets_ssh_details = ssh_details.clone();
    let secrets_is_proxmox = is_proxmox;
    let secrets_is_staging = is_staging;
    let secrets_prefix = prefix.clone();
    let secrets_vault_prefix = profile.vault_prefix.clone().unwrap_or_else(|| profile_id.clone());
    let secrets_backend = profile.backend.clone();
    let secrets_remote_path = profile.remote_busibox_path.clone();
    let secrets_repo_root = repo_root.clone();
    let secrets_is_remote = is_remote;
    let secrets_vault_password = app.vault_password.clone();

    // Thread 1: health checks (existing logic)
    let health_tx = tx.clone();
    let health_handle = std::thread::spawn(move || {
        let defs = health::all_service_defs(is_mlx);

        let check_defs: Vec<&health::ServiceHealthDef> = service_names
            .iter()
            .filter_map(|name| defs.iter().find(|d| d.name == *name))
            .collect();

        if is_proxmox {
            let ssh = ssh_details.as_ref().map(|(h, u, k)| {
                crate::modules::ssh::SshConnection::new(h, u, k)
            });

            for def in &check_defs {
                let status = health::check_service_pub(
                    def, &host, &prefix, ssh.as_ref(), true, &network_base, &vllm_network_base,
                );
                let status_str = match status {
                    HealthStatus::Healthy => "healthy".to_string(),
                    HealthStatus::Unhealthy => "unhealthy".to_string(),
                    HealthStatus::Down => "down".to_string(),
                    HealthStatus::Checking => "checking...".to_string(),
                };
                let _ = health_tx.send(ManageUpdate::StatusResult {
                    name: def.name.to_string(),
                    status: status_str,
                });
            }
        } else {
            let mut handles = Vec::new();
            for def in check_defs {
                let def = def.clone();
                let host = host.clone();
                let prefix = prefix.clone();
                let ssh_details = ssh_details.clone();
                let network_base = network_base.clone();
                let vllm_network_base = vllm_network_base.clone();
                let health_tx = health_tx.clone();

                let handle = std::thread::spawn(move || {
                    let ssh = ssh_details.as_ref().map(|(h, u, k)| {
                        crate::modules::ssh::SshConnection::new(h, u, k)
                    });
                    let status = health::check_service_pub(
                        &def, &host, &prefix, ssh.as_ref(), false, &network_base, &vllm_network_base,
                    );
                    let status_str = match status {
                        HealthStatus::Healthy => "healthy".to_string(),
                        HealthStatus::Unhealthy => "unhealthy".to_string(),
                        HealthStatus::Down => "down".to_string(),
                        HealthStatus::Checking => "checking...".to_string(),
                    };
                    let _ = health_tx.send(ManageUpdate::StatusResult {
                        name: def.name.to_string(),
                        status: status_str,
                    });
                });
                handles.push(handle);
            }
            for handle in handles {
                let _ = handle.join();
            }
        }
    });

    // Thread 2: version checks (runs in parallel with health)
    let version_handle = std::thread::spawn(move || {
        fetch_service_versions(
            &version_service_names,
            &version_tx,
            version_is_proxmox,
            version_is_remote,
            version_ssh_details.as_ref(),
            &version_prefix,
            &version_repo_root,
            version_frontend_dir.as_deref(),
            version_is_staging,
        );
    });

    // Thread 3: fetch remote available versions and per-service change detection
    let remote_tx = tx.clone();
    let remote_repo_root = repo_root.clone();
    let remote_service_names: Vec<String> = app.manage_services.iter().map(|s| s.name.clone()).collect();
    let remote_source_repos: Vec<String> = app.manage_services.iter().map(|s| s.source_repo.clone()).collect();

    let remote_handle = std::thread::spawn(move || {
        fetch_remote_versions_and_changes(
            &remote_tx,
            &remote_repo_root,
            frontend_dir.as_deref(),
            &remote_service_names,
            &remote_source_repos,
        );
    });

    // Thread 4: upstream service running versions (pct exec / docker exec)
    let upstream_is_proxmox = is_proxmox;
    let upstream_is_staging = is_staging;
    let upstream_handle = std::thread::spawn(move || {
        fetch_upstream_running_versions(
            &upstream_service_names,
            &upstream_tx,
            upstream_ssh_details.as_ref(),
            upstream_is_proxmox,
            upstream_is_staging,
            &upstream_prefix,
        );
    });

    // Thread 5: upstream latest versions from GitHub API (parallel per-service)
    let latest_handle = std::thread::spawn(move || {
        fetch_upstream_latest_versions(&latest_service_names, &latest_tx);
    });

    // Thread 6: secrets sync check (vault vs container env vars)
    // (clones already captured above, before health thread consumed originals)
    let secrets_handle = std::thread::spawn(move || {
        fetch_secrets_sync(
            &secrets_tx,
            secrets_ssh_details.as_ref(),
            secrets_is_remote,
            secrets_is_proxmox,
            secrets_is_staging,
            &secrets_prefix,
            &secrets_vault_prefix,
            &secrets_backend,
            secrets_remote_path.as_deref(),
            &secrets_repo_root,
            secrets_vault_password.as_deref(),
        );
    });

    // Coordinator thread: wait for all, then send Complete
    std::thread::spawn(move || {
        let _ = health_handle.join();
        let _ = version_handle.join();
        let _ = remote_handle.join();
        let _ = upstream_handle.join();
        let _ = latest_handle.join();
        let _ = secrets_handle.join();
        let _ = tx.send(ManageUpdate::Complete { success: true });
    });
}

/// Fetch deployed version info for busibox-managed services (skips upstream).
fn fetch_service_versions(
    service_names: &[String],
    tx: &std::sync::mpsc::Sender<ManageUpdate>,
    is_proxmox: bool,
    is_remote: bool,
    ssh_details: Option<&(String, String, String)>,
    prefix: &str,
    repo_root: &std::path::Path,
    frontend_dir: Option<&str>,
    is_staging: bool,
) {
    let non_upstream: Vec<String> = service_names
        .iter()
        .filter(|n| !is_upstream_service(n))
        .cloned()
        .collect();
    if non_upstream.is_empty() {
        return;
    }

    let local_head = std::process::Command::new("git")
        .args(["rev-parse", "--short", "HEAD"])
        .current_dir(repo_root)
        .output()
        .ok()
        .and_then(|o| {
            if o.status.success() {
                Some(String::from_utf8_lossy(&o.stdout).trim().to_string())
            } else {
                None
            }
        })
        .unwrap_or_default();

    if is_proxmox {
        fetch_versions_proxmox(&non_upstream, tx, ssh_details, &local_head, repo_root, is_staging);
    } else {
        fetch_versions_docker(&non_upstream, tx, is_remote, ssh_details, prefix, &local_head, repo_root, frontend_dir);
    }
}

/// Fetch versions from Docker container labels using a single batched command.
fn fetch_versions_docker(
    service_names: &[String],
    tx: &std::sync::mpsc::Sender<ManageUpdate>,
    is_remote: bool,
    ssh_details: Option<&(String, String, String)>,
    prefix: &str,
    local_head: &str,
    repo_root: &std::path::Path,
    frontend_dir: Option<&str>,
) {
    // Build a single shell command that reads version labels from all containers at once.
    // Output: one line per container: "container_suffix|version_label"
    let mut inspect_parts: Vec<String> = Vec::new();
    let mut name_to_container: Vec<(String, String)> = Vec::new();

    let infra_containers: &[&str] = &["postgres", "redis", "minio", "milvus", "neo4j"];

    for name in service_names {
        if let Some(container_suffix) = service_to_docker_container(name) {
            let container_name = format!("{prefix}-{container_suffix}");
            let label = if infra_containers.contains(&container_suffix) {
                "config_version"
            } else {
                "version"
            };
            inspect_parts.push(format!(
                "echo \"{container_suffix}|$(docker inspect --format '{{{{index .Config.Labels \"{label}\"}}}}' '{container_name}' 2>/dev/null || echo '')\""
            ));
            name_to_container.push((name.clone(), container_suffix.to_string()));
        }
    }

    if inspect_parts.is_empty() {
        return;
    }

    let batch_cmd = inspect_parts.join("; ");
    let output = if is_remote {
        if let Some((host, user, key)) = ssh_details {
            let ssh = crate::modules::ssh::SshConnection::new(host, user, key);
            let full_cmd = format!("{}{batch_cmd}", remote::SHELL_PATH_PREAMBLE);
            ssh.run(&full_cmd).unwrap_or_default()
        } else {
            return;
        }
    } else {
        std::process::Command::new("bash")
            .arg("-c")
            .arg(&batch_cmd)
            .output()
            .ok()
            .map(|o| String::from_utf8_lossy(&o.stdout).to_string())
            .unwrap_or_default()
    };

    // Parse output lines: "container_suffix|version"
    let mut version_map: std::collections::HashMap<String, String> = std::collections::HashMap::new();
    for line in output.lines() {
        if let Some((suffix, version)) = line.split_once('|') {
            let v = version.trim().to_string();
            if !v.is_empty() && v != "<no value>" && v != "unknown" {
                version_map.insert(suffix.trim().to_string(), v);
            }
        }
    }

    // Services whose source_repo is busibox-frontend need the frontend HEAD, not
    // the busibox GIT_COMMIT from the Docker label.
    let frontend_services: &[&str] = &[
        "core-apps", "portal", "admin", "agents", "chat", "appbuilder", "media", "documents",
    ];

    let frontend_head: Option<String> = frontend_dir.and_then(|fdir| {
        let fpath = std::path::Path::new(fdir);
        if fpath.join(".git").exists() {
            std::process::Command::new("git")
                .args(["rev-parse", "--short", "HEAD"])
                .current_dir(fpath)
                .output()
                .ok()
                .filter(|o| o.status.success())
                .map(|o| String::from_utf8_lossy(&o.stdout).trim().to_string())
                .filter(|s| !s.is_empty())
        } else {
            None
        }
    });

    for (name, container_suffix) in &name_to_container {
        if frontend_services.contains(&name.as_str()) {
            continue; // handled below with frontend HEAD
        }
        let deployed_commit = version_map.get(container_suffix.as_str()).cloned()
            .unwrap_or_else(|| {
                if !is_remote { local_head.to_string() } else { String::new() }
            });
        if deployed_commit.is_empty() {
            continue;
        }
        let commits_behind = count_commits_behind(&deployed_commit, local_head, repo_root);
        let _ = tx.send(ManageUpdate::VersionResult {
            name: name.clone(),
            version: deployed_commit,
            commits_behind,
            deployed_ref: None,
            deployed_type: None,
        });
    }

    // Frontend services get the busibox-frontend HEAD as their deployed version.
    if let Some(ref fe_head) = frontend_head {
        for svc_name in frontend_services {
            let _ = tx.send(ManageUpdate::VersionResult {
                name: svc_name.to_string(),
                version: fe_head.clone(),
                commits_behind: None,
                deployed_ref: None,
                deployed_type: None,
            });
        }
    }
}

/// Fetch versions from Proxmox `.deploy_version` files using a single batched SSH command.
fn fetch_versions_proxmox(
    service_names: &[String],
    tx: &std::sync::mpsc::Sender<ManageUpdate>,
    ssh_details: Option<&(String, String, String)>,
    local_head: &str,
    repo_root: &std::path::Path,
    is_staging: bool,
) {
    let (host, user, key) = match ssh_details {
        Some(d) => (&d.0, &d.1, &d.2),
        None => return,
    };

    // Build a single command that reads all deploy_version files via pct exec
    // (each file lives inside the respective LXC container, not on the Proxmox host).
    let mut cat_parts: Vec<String> = Vec::new();

    for name in service_names {
        if let Some((base_ctid, path)) = service_to_deploy_version_info(name) {
            let ctid = resolve_ctid(base_ctid, is_staging);
            cat_parts.push(format!(
                "echo \"DEPLOY_VERSION_START:{name}\"; pct exec {ctid} -- cat '{path}' 2>/dev/null || echo '{{}}'; echo \"DEPLOY_VERSION_END:{name}\""
            ));
        }
    }

    if cat_parts.is_empty() {
        return;
    }

    let batch_cmd = cat_parts.join("; ");
    let ssh = crate::modules::ssh::SshConnection::new(host, user, key);
    let full_cmd = format!("{}{batch_cmd}", remote::SHELL_PATH_PREAMBLE);
    let output = ssh.run(&full_cmd).unwrap_or_default();

    // Parse blocks: DEPLOY_VERSION_START:name ... json ... DEPLOY_VERSION_END:name
    let mut current_name: Option<String> = None;
    let mut current_json = String::new();

    for line in output.lines() {
        if let Some(rest) = line.strip_prefix("DEPLOY_VERSION_START:") {
            current_name = Some(rest.trim().to_string());
            current_json.clear();
        } else if let Some(rest) = line.strip_prefix("DEPLOY_VERSION_END:") {
            let end_name = rest.trim();
            if current_name.as_deref() == Some(end_name) {
                if let Some(info) = extract_deploy_version_info(&current_json) {
                    let commits_behind = count_commits_behind(&info.commit, local_head, repo_root);
                    let is_tag = info.branch.starts_with('v') && info.branch.contains('.');
                    let _ = tx.send(ManageUpdate::VersionResult {
                        name: end_name.to_string(),
                        version: info.commit,
                        commits_behind,
                        deployed_ref: Some(info.branch),
                        deployed_type: Some(if is_tag { "release".to_string() } else { "branch".to_string() }),
                    });
                }
            }
            current_name = None;
            current_json.clear();
        } else if current_name.is_some() {
            if !current_json.is_empty() {
                current_json.push('\n');
            }
            current_json.push_str(line);
        }
    }
}

/// Fetch running versions for upstream services via pct exec (Proxmox) or docker exec.
/// Runs a single batched SSH command to get all versions in parallel.
fn fetch_upstream_running_versions(
    service_names: &[String],
    tx: &std::sync::mpsc::Sender<ManageUpdate>,
    ssh_details: Option<&(String, String, String)>,
    is_proxmox: bool,
    is_staging: bool,
    prefix: &str,
) {
    let upstream_names: Vec<&String> = service_names
        .iter()
        .filter(|n| UPSTREAM_SERVICES.iter().any(|u| u.name == n.as_str()))
        .collect();
    if upstream_names.is_empty() {
        return;
    }

    let mut cmd_parts: Vec<String> = Vec::new();
    for name in &upstream_names {
        if let Some(def) = UPSTREAM_SERVICES.iter().find(|u| u.name == name.as_str()) {
            let version_cmd = if is_proxmox {
                let ctid = resolve_ctid(def.ctid, is_staging);
                format!(
                    "echo \"UPSTREAM_START:{name}\"; pct exec {ctid} -- sh -c '{cmd}' 2>/dev/null || echo 'unknown'; echo \"UPSTREAM_END:{name}\"",
                    cmd = def.version_cmd.replace('\'', "'\\''"),
                )
            } else {
                let docker_cmd = match name.as_str() {
                    "postgres" => format!("docker exec {prefix}-postgres postgres --version 2>/dev/null | grep -oE '[0-9]+\\.[0-9]+'"),
                    "redis" => format!("docker exec {prefix}-redis redis-server --version 2>/dev/null | grep -oE '[0-9]+\\.[0-9]+\\.[0-9]+'"),
                    "milvus" => format!("docker exec {prefix}-milvus-standalone milvus version 2>/dev/null | head -1 | grep -oE 'v[0-9]+\\.[0-9]+\\.[0-9]+'"),
                    "neo4j" => format!("docker exec {prefix}-neo4j neo4j version 2>/dev/null | grep -oE '[0-9]+\\.[0-9]+\\.[0-9]+'"),
                    "minio" => format!("docker exec {prefix}-minio minio --version 2>/dev/null | grep -oE 'RELEASE\\.[0-9T-]+Z' | head -1"),
                    "litellm" => format!("docker exec {prefix}-litellm pip show litellm 2>/dev/null | grep -i '^Version:' | awk '{{print $2}}'"),
                    "vllm" => format!("docker exec {prefix}-vllm python3 -c 'import vllm; print(vllm.__version__)' 2>/dev/null"),
                    "mlx" => format!("docker exec {prefix}-mlx python3 -c 'import mlx_lm; print(mlx_lm.__version__)' 2>/dev/null"),
                    "proxy" => format!("docker exec {prefix}-nginx nginx -v 2>&1 | grep -oE '[0-9]+\\.[0-9]+\\.[0-9]+'"),
                    _ => continue,
                };
                format!(
                    "echo \"UPSTREAM_START:{name}\"; {docker_cmd} || echo 'unknown'; echo \"UPSTREAM_END:{name}\""
                )
            };
            cmd_parts.push(version_cmd);
        }
    }

    if cmd_parts.is_empty() {
        return;
    }

    let batch_cmd = cmd_parts.join("; ");
    let output = if let Some((host, user, key)) = ssh_details {
        let ssh = crate::modules::ssh::SshConnection::new(host, user, key);
        let full_cmd = format!("{}{batch_cmd}", crate::modules::remote::SHELL_PATH_PREAMBLE);
        ssh.run(&full_cmd).unwrap_or_default()
    } else {
        std::process::Command::new("sh")
            .args(["-c", &batch_cmd])
            .output()
            .ok()
            .map(|o| String::from_utf8_lossy(&o.stdout).to_string())
            .unwrap_or_default()
    };

    let mut current_name: Option<String> = None;
    let mut current_version = String::new();

    for line in output.lines() {
        if let Some(rest) = line.strip_prefix("UPSTREAM_START:") {
            current_name = Some(rest.trim().to_string());
            current_version.clear();
        } else if let Some(rest) = line.strip_prefix("UPSTREAM_END:") {
            let end_name = rest.trim();
            if current_name.as_deref() == Some(end_name) {
                let ver = current_version.trim().to_string();
                if !ver.is_empty() && ver != "unknown" {
                    let clean_ver = ver.trim_start_matches('v').to_string();
                    let _ = tx.send(ManageUpdate::VersionResult {
                        name: end_name.to_string(),
                        version: clean_ver,
                        commits_behind: None,
                        deployed_ref: None,
                        deployed_type: None,
                    });
                }
            }
            current_name = None;
            current_version.clear();
        } else if current_name.is_some() {
            let trimmed = line.trim();
            if !trimmed.is_empty() && current_version.is_empty() {
                current_version = trimmed.to_string();
            }
        }
    }
}

/// Fetch latest upstream release versions from GitHub API (runs in parallel for each service).
fn fetch_upstream_latest_versions(
    service_names: &[String],
    tx: &std::sync::mpsc::Sender<ManageUpdate>,
) {
    let upstream_names: Vec<&String> = service_names
        .iter()
        .filter(|n| UPSTREAM_SERVICES.iter().any(|u| u.name == n.as_str()))
        .collect();

    let handles: Vec<_> = upstream_names
        .iter()
        .filter_map(|name| {
            UPSTREAM_SERVICES.iter().find(|u| u.name == name.as_str()).map(|def| {
                let name = name.to_string();
                let github_repo = def.github_repo.to_string();
                let tag_prefix = def.tag_prefix.to_string();
                let stable_only = def.stable_only;
                let tx = tx.clone();
                std::thread::spawn(move || {
                    let version = if stable_only {
                        fetch_github_latest_stable_release(&github_repo, &tag_prefix, &name)
                    } else {
                        fetch_github_latest_release(&github_repo, &tag_prefix, &name)
                    };
                    if let Some(version) = version {
                        let _ = tx.send(ManageUpdate::UpstreamLatestResult {
                            name,
                            latest_version: version,
                        });
                    }
                })
            })
        })
        .collect();

    for h in handles {
        let _ = h.join();
    }
}

/// Fetch the latest stable release tag from a GitHub repo.
/// Skips pre-releases, RCs, alphas, betas, nightlies, etc.
fn fetch_github_latest_release(repo: &str, tag_prefix: &str, name: &str) -> Option<String> {
    // First try /releases/latest (fast path)
    let url = format!("https://api.github.com/repos/{repo}/releases/latest");
    let output = std::process::Command::new("curl")
        .args([
            "-s", "-f", "--max-time", "10",
            "-H", "Accept: application/vnd.github+json",
            &url,
        ])
        .output()
        .ok()?;

    if output.status.success() {
        let body = String::from_utf8_lossy(&output.stdout);
        if let Some(version) = parse_release_version(&body, tag_prefix, name) {
            if !is_prerelease_version(&version) {
                return Some(version);
            }
        }
    }

    // /releases/latest returned a pre-release or failed — scan recent releases
    fetch_github_latest_stable_from_releases(repo, tag_prefix, name)
}

/// Parse a version string from a GitHub release JSON response.
fn parse_release_version(body: &str, tag_prefix: &str, name: &str) -> Option<String> {
    let v: serde_json::Value = serde_json::from_str(body).ok()?;
    let tag = v.get("tag_name")?.as_str()?;

    let version = if !tag_prefix.is_empty() {
        tag.strip_prefix(tag_prefix).unwrap_or(tag)
    } else {
        tag
    };
    let version = if name == "postgres" {
        version.replace('_', ".")
    } else {
        version.trim_start_matches('v').to_string()
    };
    if version.is_empty() { None } else { Some(version) }
}

/// Scan recent GitHub releases (up to 20) and return the highest semver that isn't a pre-release.
fn fetch_github_latest_stable_from_releases(repo: &str, tag_prefix: &str, name: &str) -> Option<String> {
    let url = format!("https://api.github.com/repos/{repo}/releases?per_page=20");
    let output = std::process::Command::new("curl")
        .args([
            "-s", "-f", "--max-time", "10",
            "-H", "Accept: application/vnd.github+json",
            &url,
        ])
        .output()
        .ok()?;
    if !output.status.success() {
        return fetch_github_latest_tag(repo, tag_prefix, name);
    }
    let body = String::from_utf8_lossy(&output.stdout);
    let releases: Vec<serde_json::Value> = serde_json::from_str(&body).ok()?;

    let mut versions: Vec<(u32, u32, u32, String)> = Vec::new();
    for rel in &releases {
        if rel.get("prerelease").and_then(|v| v.as_bool()).unwrap_or(false) {
            continue;
        }
        let tag = match rel.get("tag_name").and_then(|v| v.as_str()) {
            Some(t) => t,
            None => continue,
        };
        let stripped = if !tag_prefix.is_empty() {
            match tag.strip_prefix(tag_prefix) {
                Some(s) => s.to_string(),
                None => continue,
            }
        } else {
            tag.to_string()
        };
        let clean = if name == "postgres" {
            stripped.replace('_', ".")
        } else {
            stripped.trim_start_matches('v').to_string()
        };
        if is_prerelease_version(&clean) {
            continue;
        }
        if let Some((maj, min, pat)) = parse_semver(&clean) {
            versions.push((maj, min, pat, clean));
        }
    }
    versions.sort_by(|a, b| (b.0, b.1, b.2).cmp(&(a.0, a.1, a.2)));
    versions.into_iter().next().map(|(_, _, _, v)| v)
}

/// Fallback: fetch the latest tag (sorted semver, excluding pre-releases) from a GitHub repo.
fn fetch_github_latest_tag(repo: &str, tag_prefix: &str, name: &str) -> Option<String> {
    let url = format!("https://api.github.com/repos/{repo}/tags?per_page=30");
    let output = std::process::Command::new("curl")
        .args([
            "-s", "-f", "--max-time", "10",
            "-H", "Accept: application/vnd.github+json",
            &url,
        ])
        .output()
        .ok()?;
    if !output.status.success() {
        return None;
    }
    let body = String::from_utf8_lossy(&output.stdout);
    let tags: Vec<serde_json::Value> = serde_json::from_str(&body).ok()?;

    let mut versions: Vec<(u32, u32, u32, String)> = Vec::new();
    for tag_obj in &tags {
        let tag = tag_obj.get("name")?.as_str()?;
        let stripped = if !tag_prefix.is_empty() {
            match tag.strip_prefix(tag_prefix) {
                Some(s) => s.to_string(),
                None => continue,
            }
        } else {
            tag.to_string()
        };
        let clean = if name == "postgres" {
            stripped.replace('_', ".")
        } else {
            stripped.trim_start_matches('v').to_string()
        };
        if is_prerelease_version(&clean) {
            continue;
        }
        if let Some((maj, min, pat)) = parse_semver(&clean) {
            versions.push((maj, min, pat, clean));
        }
    }
    versions.sort_by(|a, b| (b.0, b.1, b.2).cmp(&(a.0, a.1, a.2)));
    versions.into_iter().next().map(|(_, _, _, v)| v)
}

/// Fetch the latest *stable* release from a GitHub repo (even minor version numbers).
/// Used for nginx where odd minor = mainline, even minor = stable.
fn fetch_github_latest_stable_release(repo: &str, tag_prefix: &str, name: &str) -> Option<String> {
    let url = format!("https://api.github.com/repos/{repo}/tags?per_page=50");
    let output = std::process::Command::new("curl")
        .args([
            "-s", "-f", "--max-time", "10",
            "-H", "Accept: application/vnd.github+json",
            &url,
        ])
        .output()
        .ok()?;
    if !output.status.success() {
        return None;
    }
    let body = String::from_utf8_lossy(&output.stdout);
    let tags: Vec<serde_json::Value> = serde_json::from_str(&body).ok()?;

    let mut versions: Vec<(u32, u32, u32, String)> = Vec::new();
    for tag_obj in &tags {
        let tag = tag_obj.get("name")?.as_str()?;
        let stripped = if !tag_prefix.is_empty() {
            match tag.strip_prefix(tag_prefix) {
                Some(s) => s.to_string(),
                None => continue,
            }
        } else {
            tag.to_string()
        };
        let clean = if name == "postgres" {
            stripped.replace('_', ".")
        } else {
            stripped.trim_start_matches('v').to_string()
        };
        if is_prerelease_version(&clean) {
            continue;
        }
        if let Some((maj, min, pat)) = parse_semver(&clean) {
            if min % 2 == 0 {
                versions.push((maj, min, pat, clean));
            }
        }
    }
    versions.sort_by(|a, b| (b.0, b.1, b.2).cmp(&(a.0, a.1, a.2)));
    versions.into_iter().next().map(|(_, _, _, v)| v)
}

/// Count how many commits `deployed_commit` is behind `local_head`.
/// Returns None if the comparison can't be made (unknown commit, not in history, etc.).
/// Returns Some(0) if they match, Some(N) if behind by N commits.
fn count_commits_behind(deployed_commit: &str, local_head: &str, repo_root: &std::path::Path) -> Option<i32> {
    if deployed_commit.is_empty() || local_head.is_empty() {
        return None;
    }
    // Quick check: if the short SHAs match, it's current
    if deployed_commit.starts_with(local_head) || local_head.starts_with(deployed_commit) {
        return Some(0);
    }
    // Resolve the deployed short SHA to a full SHA for unambiguous comparison
    let resolved = std::process::Command::new("git")
        .args(["rev-parse", deployed_commit])
        .current_dir(repo_root)
        .output()
        .ok()
        .filter(|o| o.status.success())
        .map(|o| String::from_utf8_lossy(&o.stdout).trim().to_string());
    let full_commit = match resolved {
        Some(ref c) if !c.is_empty() => c.as_str(),
        _ => return None,
    };
    // Count commits between deployed and HEAD: git rev-list --count <full>..HEAD
    let output = std::process::Command::new("git")
        .args(["rev-list", "--count", &format!("{full_commit}..HEAD")])
        .current_dir(repo_root)
        .output()
        .ok()?;
    if output.status.success() {
        let count_str = String::from_utf8_lossy(&output.stdout).trim().to_string();
        count_str.parse::<i32>().ok()
    } else {
        None
    }
}

/// Fetch remote available versions for both repos, then run per-service change detection.
fn fetch_remote_versions_and_changes(
    tx: &std::sync::mpsc::Sender<ManageUpdate>,
    repo_root: &std::path::Path,
    frontend_dir: Option<&str>,
    service_names: &[String],
    source_repos: &[String],
) {
    // 1) Fetch + get remote HEAD for busibox repo
    let busibox_available = fetch_remote_head(repo_root, "origin", "main");
    if let Some(ref sha) = busibox_available {
        let _ = tx.send(ManageUpdate::RemoteVersionResult {
            repo: "busibox".to_string(),
            available_version: sha.clone(),
            available_ref: "main".to_string(),
        });
    }

    // Check for latest release tag in busibox repo
    let busibox_latest_tag = get_latest_release_tag(repo_root);
    if let Some(ref tag) = busibox_latest_tag {
        // If services are deployed from a release, send the new release info
        let tag_sha = resolve_ref_to_short_sha(repo_root, tag);
        if let Some(ref sha) = tag_sha {
            // Only send if it differs from current main — this enriches the available info
            // for services that were deployed from a release tag
            let _ = tx.send(ManageUpdate::RemoteVersionResult {
                repo: "busibox".to_string(),
                available_version: sha.clone(),
                available_ref: tag.clone(),
            });
        }
    }

    // 2) Fetch + get remote HEAD for busibox-frontend repo (if available)
    let frontend_available = if let Some(fdir) = frontend_dir {
        let fpath = std::path::Path::new(fdir);
        if fpath.join(".git").exists() {
            fetch_remote_head(fpath, "origin", "main")
        } else {
            None
        }
    } else {
        None
    };
    if let Some(ref sha) = frontend_available {
        let _ = tx.send(ManageUpdate::RemoteVersionResult {
            repo: "busibox-frontend".to_string(),
            available_version: sha.clone(),
            available_ref: "main".to_string(),
        });
    }

    // 3) Per-service change detection: check if srv/shared/ changed (cascades to all APIs)
    let shared_changed_busibox = if let Some(ref available) = busibox_available {
        check_paths_changed(repo_root, available, &["srv/shared/", "provision/ansible/roles/common/"])
    } else {
        false
    };

    let shared_changed_frontend = if let (Some(ref available), Some(fdir)) = (&frontend_available, frontend_dir) {
        let fpath = std::path::Path::new(fdir);
        check_paths_changed(fpath, available, &["packages/app/", "packages/tsconfig/"])
    } else {
        false
    };

    for (i, name) in service_names.iter().enumerate() {
        let repo = &source_repos[i];
        let paths = service_to_source_paths(name);
        if paths.is_empty() {
            continue;
        }

        let needs_update = match repo.as_str() {
            "busibox" => {
                if shared_changed_busibox && is_api_service(name) {
                    true
                } else if let Some(ref available) = busibox_available {
                    check_paths_changed(repo_root, available, &paths.iter().map(|s| *s).collect::<Vec<_>>())
                } else {
                    false
                }
            }
            "busibox-frontend" => {
                if shared_changed_frontend {
                    true
                } else if let (Some(ref available), Some(fdir)) = (&frontend_available, frontend_dir) {
                    let fpath = std::path::Path::new(fdir);
                    check_paths_changed(fpath, available, &paths.iter().map(|s| *s).collect::<Vec<_>>())
                } else {
                    false
                }
            }
            "upstream" if is_busibox_managed_upstream(name) => {
                if let Some(ref available) = busibox_available {
                    check_paths_changed(repo_root, available, &paths.iter().map(|s| *s).collect::<Vec<_>>())
                } else {
                    false
                }
            }
            _ => false,
        };

        let _ = tx.send(ManageUpdate::NeedsUpdateResult {
            name: name.clone(),
            needs_update,
        });
    }
}

fn is_api_service(name: &str) -> bool {
    matches!(name, "authz" | "agent" | "data" | "data-worker" | "search" | "deploy" | "docs" | "embedding" | "bridge" | "config")
}

/// Upstream services that also have busibox Ansible roles managing their config.
/// These track both upstream package version AND busibox config changes.
fn is_busibox_managed_upstream(name: &str) -> bool {
    matches!(name, "litellm" | "proxy" | "postgres" | "redis" | "minio" | "milvus" | "neo4j" | "vllm" | "mlx")
}

/// Run `git fetch origin` then `git rev-parse --short origin/<branch>` in the given repo.
fn fetch_remote_head(repo_root: &std::path::Path, remote: &str, branch: &str) -> Option<String> {
    let _ = std::process::Command::new("git")
        .args(["fetch", remote, "--quiet"])
        .current_dir(repo_root)
        .output();

    let output = std::process::Command::new("git")
        .args(["rev-parse", "--short", &format!("{remote}/{branch}")])
        .current_dir(repo_root)
        .output()
        .ok()?;

    if output.status.success() {
        let sha = String::from_utf8_lossy(&output.stdout).trim().to_string();
        if !sha.is_empty() { Some(sha) } else { None }
    } else {
        None
    }
}

/// Get the latest release tag (semver-style vX.Y.Z) from the repo.
fn get_latest_release_tag(repo_root: &std::path::Path) -> Option<String> {
    let output = std::process::Command::new("git")
        .args(["tag", "--sort=-version:refname", "-l", "v*"])
        .current_dir(repo_root)
        .output()
        .ok()?;

    if output.status.success() {
        let stdout = String::from_utf8_lossy(&output.stdout);
        stdout.lines().next().map(|s| s.trim().to_string()).filter(|s| !s.is_empty())
    } else {
        None
    }
}

/// Resolve a ref (tag or branch) to a short SHA.
fn resolve_ref_to_short_sha(repo_root: &std::path::Path, refname: &str) -> Option<String> {
    let output = std::process::Command::new("git")
        .args(["rev-parse", "--short", refname])
        .current_dir(repo_root)
        .output()
        .ok()?;

    if output.status.success() {
        let sha = String::from_utf8_lossy(&output.stdout).trim().to_string();
        if !sha.is_empty() { Some(sha) } else { None }
    } else {
        None
    }
}

/// Check if any of the given paths have changes between HEAD and the given available ref.
/// Uses `git diff --quiet HEAD..<available> -- <paths>` to detect changes.
fn check_paths_changed(repo_root: &std::path::Path, available_sha: &str, paths: &[&str]) -> bool {
    if paths.is_empty() {
        return false;
    }
    let mut args: Vec<&str> = vec!["diff", "--quiet", &format!("HEAD..{available_sha}"), "--"];
    // We need to build a range string that lives long enough
    let range = format!("HEAD..{available_sha}");
    args = vec!["diff", "--quiet", &range, "--"];
    for p in paths {
        args.push(p);
    }
    let output = std::process::Command::new("git")
        .args(&args)
        .current_dir(repo_root)
        .output();

    match output {
        Ok(o) => !o.status.success(), // exit code 1 = differences found
        Err(_) => false,
    }
}

/// Update a single selected service (redeploy via make install).
fn run_update_selected(app: &mut App) {
    let svc = match app.manage_services.get(app.manage_selected) {
        Some(s) => s.clone(),
        None => return,
    };
    let make_name = service_to_make_name(&svc.name).to_string();
    spawn_update_worker(app, &make_name);
}

/// Update all services that need updating.
fn run_update_all(app: &mut App) {
    const EXCLUDED: &[&str] = &["user-apps", "busibox-cli", "core-apps"];
    let services_to_update: Vec<String> = app
        .manage_services
        .iter()
        .filter(|s| {
            if EXCLUDED.contains(&s.name.as_str()) {
                return false;
            }
            if s.source_repo == "upstream" {
                s.needs_update && is_busibox_managed_upstream(&s.name)
            } else {
                s.needs_update
            }
        })
        .map(|s| service_to_make_name(&s.name).to_string())
        .collect();

    if services_to_update.is_empty() {
        app.set_message("All services are up to date", crate::app::MessageKind::Info);
        return;
    }

    let service_list = services_to_update.join(",");
    spawn_update_worker(app, &service_list);
}

/// Spawn a background worker that runs `make install SERVICE=<services>` for updates.
fn spawn_update_worker(app: &mut App, service_list: &str) {
    use crate::modules::hardware::LlmBackend;

    kill_log_stream(app);

    let (tx, rx) = std::sync::mpsc::channel::<ManageUpdate>();
    app.manage_rx = Some(rx);
    app.manage_log.clear();
    app.manage_log_visible = true;
    app.manage_log_scroll = 0;
    app.manage_log_autoscroll = true;
    app.manage_log_streaming = false;
    app.manage_action_running = true;
    app.manage_action_complete = false;

    let is_remote = app.active_profile().map(|(_, p)| p.remote).unwrap_or(false);
    let repo_root = app.repo_root.clone();
    let services = service_list.to_string();
    let vault_password = app.vault_password.clone();
    let profile_env: Option<String> = app.active_profile().map(|(_, p)| p.environment.clone());
    let profile_backend: Option<String> = app.active_profile().map(|(_, p)| p.backend.to_lowercase());

    // Capture the local git commit and branch so remote deploys (where .git/ is
    // not synced) can still write accurate .deploy_version files.
    let git_commit: Option<String> = std::process::Command::new("git")
        .args(["rev-parse", "--short", "HEAD"])
        .current_dir(&repo_root)
        .output()
        .ok()
        .filter(|o| o.status.success())
        .map(|o| String::from_utf8_lossy(&o.stdout).trim().to_string())
        .filter(|s| !s.is_empty());
    let git_branch: Option<String> = std::process::Command::new("git")
        .args(["branch", "--show-current"])
        .current_dir(&repo_root)
        .output()
        .ok()
        .filter(|o| o.status.success())
        .map(|o| String::from_utf8_lossy(&o.stdout).trim().to_string())
        .filter(|s| !s.is_empty());

    let ssh_details: Option<(String, String, String)> = if is_remote {
        app.active_profile().and_then(|(_, p)| {
            p.effective_host().map(|h| (
                h.to_string(),
                p.effective_user().to_string(),
                p.effective_ssh_key().to_string(),
            ))
        })
    } else {
        None
    };

    let profile_remote_path: Option<String> = app.active_profile().map(|(_, p)| p.effective_remote_path().to_string());
    let profile_host: Option<String> = app.active_profile().and_then(|(_, p)| p.effective_host().map(|s| s.to_string()));
    let profile_vault_prefix: Option<String> = app.active_profile().and_then(|(id, p)| p.vault_prefix.clone().or(Some(id.to_string())));
    let profile_site_domain: Option<String> = app.active_profile().and_then(|(_, p)| p.site_domain.clone()).filter(|v| !v.trim().is_empty());
    let profile_llm_backend: Option<String> = app.active_profile().and_then(|(_, p)| {
        p.hardware.as_ref().map(|h| match h.llm_backend {
            LlmBackend::Mlx => "mlx".to_string(),
            LlmBackend::Vllm => "vllm".to_string(),
            LlmBackend::Cloud => "cloud".to_string(),
        })
    });
    let profile_admin_email: Option<String> = app.active_profile().and_then(|(_, p)| p.admin_email.clone());
    let profile_allowed_email_domains: Option<String> = app.active_profile().and_then(|(_, p)| p.allowed_email_domains.clone());

    std::thread::spawn(move || {
        let remote_path = profile_remote_path.as_deref().unwrap_or("~/busibox").to_string();

        let _ = tx.send(ManageUpdate::Log(format!("Updating: {services}")));

        // Sync if remote
        if is_remote {
            if let Some((ref host, ref user, ref key)) = ssh_details {
                let display_host = profile_host.as_deref().unwrap_or(host);
                let _ = tx.send(ManageUpdate::Log(format!("Syncing files to {display_host}:{remote_path}...")));
                let ssh = crate::modules::ssh::SshConnection::new(display_host, user, key);
                if let Err(e) = remote::ensure_remote_dir(&ssh, &remote_path) {
                    let _ = tx.send(ManageUpdate::Log(format!("ERROR: {e}")));
                    let _ = tx.send(ManageUpdate::Complete { success: false });
                    return;
                }
                if let Err(e) = remote::sync(&repo_root, display_host, user, key, &remote_path) {
                    let _ = tx.send(ManageUpdate::Log(format!("ERROR: rsync failed: {e}")));
                    let _ = tx.send(ManageUpdate::Complete { success: false });
                    return;
                }
                let _ = tx.send(ManageUpdate::Log("✓ Files synced".into()));

                if let Some(ref vp) = profile_vault_prefix {
                    if let Err(e) = remote::sync_vault_file(&repo_root, display_host, user, key, &remote_path, vp) {
                        let _ = tx.send(ManageUpdate::Log(format!("WARNING: vault push failed: {e}")));
                    }
                }
                let _ = remote::cleanup_remote_state(&ssh, &remote_path);
            }
        }

        let env_val = profile_env.as_deref().unwrap_or("development");
        let backend_val = profile_backend.as_deref().unwrap_or("docker");
        let site_domain_export = profile_site_domain.as_deref().map(|d| format!("SITE_DOMAIN={d} ")).unwrap_or_default();
        let llm_backend_export = profile_llm_backend.as_deref().map(|b| format!("LLM_BACKEND={b} ")).unwrap_or_default();
        let vault_prefix_export = profile_vault_prefix.as_deref().map(|vp| format!("VAULT_PREFIX={vp} ")).unwrap_or_default();
        let admin_email_export = profile_admin_email.as_deref().map(|e| format!("ADMIN_EMAIL={e} ")).unwrap_or_default();
        let allowed_domains_export = profile_allowed_email_domains.as_deref().map(|d| format!("ALLOWED_DOMAINS={d} ")).unwrap_or_default();
        let git_commit_export = git_commit.as_deref().map(|c| format!("GIT_COMMIT={c} ")).unwrap_or_default();
        let git_branch_export = git_branch.as_deref().map(|b| format!("GIT_BRANCH={b} ")).unwrap_or_default();

        let make_args = format!(
            "{site_domain_export}{llm_backend_export}{vault_prefix_export}{admin_email_export}{allowed_domains_export}{git_commit_export}{git_branch_export}install SERVICE={services} ENV={env_val} BUSIBOX_ENV={env_val} BUSIBOX_BACKEND={backend_val}"
        );
        let _ = tx.send(ManageUpdate::Log(format!("Running: make {make_args}")));

        let stream_tx = tx.clone();
        let mut last_line = String::new();
        let mut censored_count: usize = 0;
        let on_line = move |line: &str| {
            if line.contains("censored due to no_log") {
                censored_count += 1;
                return;
            }
            if censored_count > 0 {
                let _ = stream_tx.send(ManageUpdate::Log(
                    format!("  ... ({censored_count} censored lines suppressed)")
                ));
                censored_count = 0;
            }
            if line == last_line {
                return;
            }
            last_line = line.to_string();
            let _ = stream_tx.send(ManageUpdate::Log(format!("  {line}")));
        };

        let result: color_eyre::Result<i32> = if is_remote {
            if let Some((ref host, ref user, ref key)) = ssh_details {
                let ssh = crate::modules::ssh::SshConnection::new(
                    profile_host.as_deref().unwrap_or(host), user, key,
                );
                if let Some(ref vp) = vault_password {
                    remote::exec_make_quiet_with_vault_streaming(&ssh, &remote_path, &make_args, vp, on_line)
                } else {
                    remote::exec_make_quiet_streaming(&ssh, &remote_path, &make_args, on_line)
                }
            } else {
                Err(color_eyre::eyre::eyre!("No SSH connection"))
            }
        } else if let Some(ref vp) = vault_password {
            remote::run_local_make_quiet_with_vault_streaming(&repo_root, &make_args, vp, None, on_line)
        } else {
            remote::run_local_make_quiet_streaming(&repo_root, &make_args, on_line)
        };

        match result {
            Ok(0) => {
                let _ = tx.send(ManageUpdate::Log(format!("✓ Update {services} successful")));
                let _ = tx.send(ManageUpdate::Complete { success: true });
            }
            Ok(code) => {
                let _ = tx.send(ManageUpdate::Log(format!("FAILED: update {services} (exit code {code})")));
                let _ = tx.send(ManageUpdate::Complete { success: false });
            }
            Err(e) => {
                let _ = tx.send(ManageUpdate::Log(format!("ERROR: update {services}: {e}")));
                let _ = tx.send(ManageUpdate::Complete { success: false });
            }
        }
    });
}

/// Resolve which model_config.yml to use for a LiteLLM deploy.
///
/// 1. If no local config exists → generate a fresh one from the remote pipeline.
/// 2. If local exists → fetch the currently deployed config from remote and compare.
///    - If identical → proceed silently (no question asked).
///    - If different → show both side-by-side and ask the user which to use.
/// Returns true if a fresh config was generated (so caller can offer vLLM redeploy).
fn resolve_model_config(
    tx: &std::sync::mpsc::Sender<ManageUpdate>,
    repo_root: &std::path::Path,
    remote_path: &str,
    ssh_details: &Option<(String, String, String)>,
    profile_host: Option<&str>,
    profile_model_tier: Option<&str>,
    profile_llm_backend: Option<&str>,
    profile_network_base_octets: Option<&str>,
) -> bool {
    let (host, user, key) = match ssh_details {
        Some((h, u, k)) => (h.as_str(), u.as_str(), k.as_str()),
        None => return false,
    };
    let display_host = profile_host.unwrap_or(host);
    let model_cfg_rel = "provision/ansible/group_vars/all/model_config.yml";
    let local_model_cfg = repo_root.join(model_cfg_rel);
    let remote_model_cfg = format!("{}/{}", remote_path.trim_end_matches('/'), model_cfg_rel);

    if !local_model_cfg.exists() {
        // No local config — generate a fresh one
        let _ = tx.send(ManageUpdate::Log(
            "No local model_config.yml found — generating from remote...".into(),
        ));
        return generate_and_pull_model_config(
            tx, repo_root, remote_path, display_host, user, key,
            profile_model_tier, profile_llm_backend, profile_network_base_octets,
        );
    }

    // Local config exists. Fetch the deployed config to compare.
    let _ = tx.send(ManageUpdate::Log(
        "Checking LLM config: comparing local vs deployed...".into(),
    ));

    let ssh = crate::modules::ssh::SshConnection::new(display_host, user, key);
    let cat_cmd = format!(
        "{}cat '{}' 2>/dev/null || echo ''",
        remote::SHELL_PATH_PREAMBLE,
        remote_model_cfg,
    );
    let deployed_content = ssh.run(&cat_cmd).unwrap_or_default();
    let deployed_content = deployed_content.trim();

    let local_content = std::fs::read_to_string(&local_model_cfg).unwrap_or_default();
    let local_content = local_content.trim();

    if deployed_content.is_empty() {
        // Nothing deployed yet — use local
        let _ = tx.send(ManageUpdate::Log(
            "No config deployed on remote yet — using local saved config.".into(),
        ));
        return false;
    }

    // Normalize for comparison: trim each line, skip blank lines and comments
    let normalize = |s: &str| -> Vec<String> {
        s.lines()
            .map(|l| l.trim().to_string())
            .filter(|l| !l.is_empty() && !l.starts_with('#'))
            .collect()
    };
    let local_lines = normalize(local_content);
    let deployed_lines = normalize(deployed_content);

    if local_lines == deployed_lines {
        let _ = tx.send(ManageUpdate::Log(
            "✓ Local config matches deployed config — no changes needed.".into(),
        ));
        return false;
    }

    // Configs differ — show both and ask
    let _ = tx.send(ManageUpdate::Log(String::new()));
    let _ = tx.send(ManageUpdate::Log(
        "╔══ LLM config mismatch detected ══╗".into(),
    ));
    let _ = tx.send(ManageUpdate::Log(
        "The local saved config differs from what's currently deployed.".into(),
    ));

    // Show a compact summary of differences
    let _ = tx.send(ManageUpdate::Log(String::new()));
    let _ = tx.send(ManageUpdate::Log("── LOCAL (saved from Models screen) ──".into()));
    show_config_summary(tx, local_content);

    let _ = tx.send(ManageUpdate::Log(String::new()));
    let _ = tx.send(ManageUpdate::Log("── DEPLOYED (currently running on remote) ──".into()));
    show_config_summary(tx, deployed_content);

    let _ = tx.send(ManageUpdate::Log(String::new()));

    let (confirm_tx, confirm_rx) = std::sync::mpsc::channel::<bool>();
    let _ = tx.send(ManageUpdate::WaitForConfirm {
        prompt: "Deploy LOCAL saved config? (y=local, n=keep deployed)".to_string(),
        response: confirm_tx,
    });

    match confirm_rx.recv() {
        Ok(true) => {
            // User chose local — rsync will push it; nothing else needed
            let _ = tx.send(ManageUpdate::Log(
                "Using local saved config for deploy.".into(),
            ));
            // Re-sync to push local config to remote
            let _ = tx.send(ManageUpdate::Log("Syncing local config to remote...".into()));
            if let Err(e) = remote::sync(repo_root, display_host, user, key, remote_path) {
                let _ = tx.send(ManageUpdate::Log(format!("WARNING: Re-sync failed: {e}")));
            }
            true
        }
        Ok(false) => {
            // User chose deployed — pull deployed to local so they stay in sync
            let _ = tx.send(ManageUpdate::Log(
                "Keeping deployed config. Pulling to local to stay in sync...".into(),
            ));
            match remote::pull_file(display_host, user, key, &remote_model_cfg, &local_model_cfg) {
                Ok(()) => {
                    let _ = tx.send(ManageUpdate::Log("✓ Local config updated from deployed.".into()));
                }
                Err(e) => {
                    let _ = tx.send(ManageUpdate::Log(format!("WARNING: Failed to pull: {e}")));
                }
            }
            false
        }
        Err(_) => false,
    }
}

/// Show a compact summary of a model_config.yml: just the model entries.
fn show_config_summary(tx: &std::sync::mpsc::Sender<ManageUpdate>, content: &str) {
    let mut model_count = 0;
    for line in content.lines() {
        let trimmed = line.trim();
        // Show lines that define models (look for model_name or litellm_params entries)
        if trimmed.starts_with("- model_name:") || trimmed.starts_with("model_name:") {
            model_count += 1;
            let _ = tx.send(ManageUpdate::Log(format!("  {trimmed}")));
        }
    }
    if model_count == 0 {
        // Fallback: show first 15 non-comment lines
        let mut shown = 0;
        for line in content.lines() {
            let trimmed = line.trim();
            if trimmed.is_empty() || trimmed.starts_with('#') {
                continue;
            }
            let _ = tx.send(ManageUpdate::Log(format!("  {trimmed}")));
            shown += 1;
            if shown >= 15 {
                let _ = tx.send(ManageUpdate::Log("  ...".into()));
                break;
            }
        }
    } else {
        let _ = tx.send(ManageUpdate::Log(format!("  ({model_count} model(s) configured)")));
    }
}

/// Generate model_config.yml on the remote host and pull it back locally.
/// Returns true on success.
fn generate_and_pull_model_config(
    tx: &std::sync::mpsc::Sender<ManageUpdate>,
    repo_root: &std::path::Path,
    remote_path: &str,
    display_host: &str,
    user: &str,
    key: &str,
    profile_model_tier: Option<&str>,
    profile_llm_backend: Option<&str>,
    profile_network_base_octets: Option<&str>,
) -> bool {
    let ssh = crate::modules::ssh::SshConnection::new(display_host, user, key);
    let tx_model = tx.clone();
    let on_model_line = |line: &str| {
        let _ = tx_model.send(ManageUpdate::Log(format!("  [model-pipeline] {line}")));
    };

    let mut env_prefix = String::new();
    if let Some(tier) = profile_model_tier {
        env_prefix.push_str(&format!(
            "LLM_TIER={} MODEL_TIER={} ",
            shell_escape(tier),
            shell_escape(tier),
        ));
    }
    if let Some(backend) = profile_llm_backend {
        env_prefix.push_str(&format!("LLM_BACKEND={} ", shell_escape(backend)));
    }
    if let Some(octets) = profile_network_base_octets {
        env_prefix.push_str(&format!("NETWORK_BASE_OCTETS={} ", shell_escape(octets)));
    }
    let gen_cmd = format!("{env_prefix}bash scripts/llm/generate-model-config.sh");

    let model_cfg_rel = "provision/ansible/group_vars/all/model_config.yml";
    let local_model_cfg = repo_root.join(model_cfg_rel);
    let remote_model_cfg = format!("{}/{}", remote_path.trim_end_matches('/'), model_cfg_rel);

    match remote::exec_remote_streaming(&ssh, remote_path, &gen_cmd, on_model_line) {
        Ok(0) => {
            match remote::pull_file(display_host, user, key, &remote_model_cfg, &local_model_cfg) {
                Ok(()) => {
                    let _ = tx.send(ManageUpdate::Log(format!(
                        "✓ Generated and saved model_config.yml to {}",
                        local_model_cfg.display(),
                    )));
                    // Re-sync so the config is available on remote for the deploy
                    if let Err(e) = remote::sync(repo_root, display_host, user, key, remote_path) {
                        let _ = tx.send(ManageUpdate::Log(format!("WARNING: Re-sync failed: {e}")));
                    }
                    true
                }
                Err(e) => {
                    let _ = tx.send(ManageUpdate::Log(format!(
                        "WARNING: Failed to pull model_config.yml: {e}"
                    )));
                    false
                }
            }
        }
        Ok(code) => {
            let _ = tx.send(ManageUpdate::Log(format!(
                "WARNING: generate-model-config.sh exited with code {code}"
            )));
            false
        }
        Err(e) => {
            let _ = tx.send(ManageUpdate::Log(format!(
                "WARNING: generate-model-config.sh failed: {e}"
            )));
            false
        }
    }
}

fn run_action(app: &mut App, action: &str) {
    let svc = match app.manage_services.get(app.manage_selected) {
        Some(s) => s.clone(),
        None => return,
    };

    // All other actions (restart, redeploy, stop, start) use async worker with log viewer
    let make_svc = service_to_make_name(&svc.name).to_string();
    spawn_action_worker(app, &make_svc, action);
}

fn spawn_action_worker(app: &mut App, service_name: &str, action: &str) {
    use crate::modules::hardware::LlmBackend;

    kill_log_stream(app);

    let (tx, rx) = std::sync::mpsc::channel::<ManageUpdate>();
    app.manage_rx = Some(rx);
    app.manage_log.clear();
    app.manage_log_visible = true;
    app.manage_log_scroll = 0;
    app.manage_log_autoscroll = true;
    app.manage_log_streaming = false;
    app.manage_action_running = true;
    app.manage_action_complete = false;

    let is_remote = app.active_profile().map(|(_, p)| p.remote).unwrap_or(false);
    let repo_root = app.repo_root.clone();
    let service = service_name.to_string();
    let action = action.to_string();
    let vault_password = app.vault_password.clone();
    let profile_env: Option<String> = app
        .active_profile()
        .map(|(_, p)| p.environment.clone());
    let profile_backend: Option<String> = app
        .active_profile()
        .map(|(_, p)| p.backend.to_lowercase());

    let ssh_details: Option<(String, String, String)> = if is_remote {
        app.active_profile().and_then(|(_, p)| {
            p.effective_host().map(|h| {
                (
                    h.to_string(),
                    p.effective_user().to_string(),
                    p.effective_ssh_key().to_string(),
                )
            })
        })
    } else {
        None
    };

    let profile_remote_path: Option<String> = app
        .active_profile()
        .map(|(_, p)| p.effective_remote_path().to_string());
    let profile_host: Option<String> = app
        .active_profile()
        .and_then(|(_, p)| p.effective_host().map(|s| s.to_string()));

    let profile_model_tier: Option<String> = app
        .active_profile()
        .and_then(|(_, p)| p.effective_model_tier().map(|t| t.name().to_string()));
    let profile_llm_backend: Option<String> = app
        .active_profile()
        .and_then(|(_, p)| p.hardware.as_ref().map(|h| match h.llm_backend {
            LlmBackend::Mlx => "mlx".to_string(),
            LlmBackend::Vllm => "vllm".to_string(),
            LlmBackend::Cloud => "cloud".to_string(),
        }));
    let profile_network_base_octets: Option<String> = app
        .active_profile()
        .and_then(|(_, p)| p.network_base_octets.clone())
        .filter(|v| !v.trim().is_empty());
    let profile_site_domain: Option<String> = app
        .active_profile()
        .and_then(|(_, p)| p.site_domain.clone())
        .filter(|v| !v.trim().is_empty());
    let profile_vault_prefix: Option<String> = app
        .active_profile()
        .and_then(|(id, p)| p.vault_prefix.clone().or(Some(id.to_string())));
    let profile_admin_email: Option<String> = app
        .active_profile()
        .and_then(|(_, p)| p.admin_email.clone());
    let profile_allowed_email_domains: Option<String> = app
        .active_profile()
        .and_then(|(_, p)| p.allowed_email_domains.clone());

    std::thread::spawn(move || {
        let remote_path = profile_remote_path
            .as_deref()
            .unwrap_or("~/busibox")
            .to_string();

        let _ = tx.send(ManageUpdate::Log(format!(
            "Running {action} for {service}..."
        )));

        // For redeploy, also rsync first if remote
        if action == "redeploy" && is_remote {
            if let Some((ref host, ref user, ref key)) = ssh_details {
                let display_host = profile_host.as_deref().unwrap_or(host);
                let _ = tx.send(ManageUpdate::Log(format!(
                    "Syncing files to {display_host}:{remote_path}..."
                )));

                let ssh = crate::modules::ssh::SshConnection::new(
                    display_host, user, key,
                );

                if let Err(e) = remote::ensure_remote_dir(&ssh, &remote_path) {
                    let _ = tx.send(ManageUpdate::Log(format!(
                        "ERROR: Failed to create remote dir: {e}"
                    )));
                    let _ = tx.send(ManageUpdate::Complete { success: false });
                    return;
                }

                if let Err(e) =
                    remote::sync(&repo_root, display_host, user, key, &remote_path)
                {
                    let _ = tx.send(ManageUpdate::Log(format!(
                        "ERROR: rsync failed: {e}"
                    )));
                    let _ = tx.send(ManageUpdate::Complete { success: false });
                    return;
                }
                let _ = tx.send(ManageUpdate::Log("✓ Files synced".into()));

                // Push vault file to remote (already validated at profile unlock time)
                if let Some(ref vp) = profile_vault_prefix {
                    if let Err(e) = remote::sync_vault_file(
                        &repo_root, display_host, user, key, &remote_path, vp,
                    ) {
                        let _ = tx.send(ManageUpdate::Log(format!(
                            "WARNING: vault push failed: {e}"
                        )));
                    }
                }

                // Clean up stale local state from remote
                let _ = remote::cleanup_remote_state(&ssh, &remote_path);
            }
        }

        // For litellm redeploy/restart, resolve which model_config.yml to deploy.
        // Compare local (saved from model-screen) vs deployed (on remote host).
        // If identical: proceed silently. If different: show both and ask.
        let mut model_config_regenerated = false;
        if (service == "litellm") && (action == "redeploy" || action == "restart") && is_remote {
            model_config_regenerated = resolve_model_config(
                &tx,
                &repo_root,
                &remote_path,
                &ssh_details,
                profile_host.as_deref(),
                profile_model_tier.as_deref(),
                profile_llm_backend.as_deref(),
                profile_network_base_octets.as_deref(),
            );
        }

        let env_val = profile_env.as_deref().unwrap_or("development");
        let backend_val = profile_backend.as_deref().unwrap_or("docker");
        let site_domain_export = profile_site_domain
            .as_deref()
            .map(|d| format!("SITE_DOMAIN={d} "))
            .unwrap_or_default();
        let llm_backend_export = profile_llm_backend
            .as_deref()
            .map(|b| format!("LLM_BACKEND={b} "))
            .unwrap_or_default();
        let vault_prefix_export = profile_vault_prefix
            .as_deref()
            .map(|vp| format!("VAULT_PREFIX={vp} "))
            .unwrap_or_default();
        let admin_email_export = profile_admin_email
            .as_deref()
            .map(|e| format!("ADMIN_EMAIL={e} "))
            .unwrap_or_default();
        let allowed_domains_export = profile_allowed_email_domains
            .as_deref()
            .map(|d| format!("ALLOWED_DOMAINS={d} "))
            .unwrap_or_default();
        let make_args = format!(
            "{site_domain_export}{llm_backend_export}{vault_prefix_export}{admin_email_export}{allowed_domains_export}manage SERVICE={service} ACTION={action} ENV={env_val} BUSIBOX_ENV={env_val} BUSIBOX_BACKEND={backend_val}"
        );
        let _ = tx.send(ManageUpdate::Log(format!("Running: make {make_args}")));

        let stream_tx = tx.clone();
        let mut last_line_m = String::new();
        let mut censored_count_m: usize = 0;
        let on_line = move |line: &str| {
            if line.contains("censored due to no_log") {
                censored_count_m += 1;
                return;
            }
            if censored_count_m > 0 {
                let _ = stream_tx.send(ManageUpdate::Log(
                    format!("  ... ({censored_count_m} censored lines suppressed)")
                ));
                censored_count_m = 0;
            }
            if line == last_line_m {
                return;
            }
            last_line_m = line.to_string();
            let _ = stream_tx.send(ManageUpdate::Log(format!("  {line}")));
        };

        let result: color_eyre::Result<i32> = if is_remote {
            if let Some((ref host, ref user, ref key)) = ssh_details {
                let ssh = crate::modules::ssh::SshConnection::new(
                    profile_host.as_deref().unwrap_or(host),
                    user,
                    key,
                );
                if let Some(ref vp) = vault_password {
                    remote::exec_make_quiet_with_vault_streaming(&ssh, &remote_path, &make_args, vp, on_line)
                } else {
                    remote::exec_make_quiet_streaming(&ssh, &remote_path, &make_args, on_line)
                }
            } else {
                Err(color_eyre::eyre::eyre!("No SSH connection"))
            }
        } else if let Some(ref vp) = vault_password {
            remote::run_local_make_quiet_with_vault_streaming(&repo_root, &make_args, vp, None, on_line)
        } else {
            remote::run_local_make_quiet_streaming(&repo_root, &make_args, on_line)
        };

        match result {
            Ok(0) => {
                let _ = tx.send(ManageUpdate::Log(format!(
                    "✓ {action} {service} successful"
                )));

                // After litellm redeploy with regenerated model config, offer vLLM redeploy
                if model_config_regenerated && service == "litellm" && is_remote {
                    let (confirm_tx, confirm_rx) = std::sync::mpsc::channel::<bool>();
                    let _ = tx.send(ManageUpdate::WaitForConfirm {
                        prompt: "Also redeploy vllm to apply model changes?".to_string(),
                        response: confirm_tx,
                    });
                    let do_vllm = confirm_rx.recv().unwrap_or(false);
                    if do_vllm {
                        let _ = tx.send(ManageUpdate::Log(
                            "Redeploying vllm...".into(),
                        ));
                        let env_val = profile_env.as_deref().unwrap_or("development");
                        let backend_val = profile_backend.as_deref().unwrap_or("docker");
                        let sd = profile_site_domain
                            .as_deref()
                            .map(|d| format!("SITE_DOMAIN={d} "))
                            .unwrap_or_default();
                        let lb = profile_llm_backend
                            .as_deref()
                            .map(|b| format!("LLM_BACKEND={b} "))
                            .unwrap_or_default();
                        let vp_export = profile_vault_prefix
                            .as_deref()
                            .map(|vp| format!("VAULT_PREFIX={vp} "))
                            .unwrap_or_default();
                        let ae = profile_admin_email
                            .as_deref()
                            .map(|e| format!("ADMIN_EMAIL={e} "))
                            .unwrap_or_default();
                        let ad = profile_allowed_email_domains
                            .as_deref()
                            .map(|d| format!("ALLOWED_DOMAINS={d} "))
                            .unwrap_or_default();
                        let vllm_args = format!(
                            "{sd}{lb}{vp_export}{ae}{ad}manage SERVICE=vllm ACTION=redeploy ENV={env_val} BUSIBOX_ENV={env_val} BUSIBOX_BACKEND={backend_val}"
                        );
                        let _ = tx.send(ManageUpdate::Log(format!("Running: make {vllm_args}")));

                        let vllm_tx = tx.clone();
                        let vllm_on_line = move |line: &str| {
                            let _ = vllm_tx.send(ManageUpdate::Log(format!("  {line}")));
                        };

                        let vllm_result: color_eyre::Result<i32> = if let Some((ref host, ref user, ref key)) = ssh_details {
                            let ssh = crate::modules::ssh::SshConnection::new(
                                profile_host.as_deref().unwrap_or(host),
                                user,
                                key,
                            );
                            if let Some(ref vp) = vault_password {
                                remote::exec_make_quiet_with_vault_streaming(&ssh, &remote_path, &vllm_args, vp, vllm_on_line)
                            } else {
                                remote::exec_make_quiet_streaming(&ssh, &remote_path, &vllm_args, vllm_on_line)
                            }
                        } else {
                            Err(color_eyre::eyre::eyre!("No SSH connection"))
                        };

                        match vllm_result {
                            Ok(0) => {
                                let _ = tx.send(ManageUpdate::Log(
                                    "✓ vllm redeploy successful".into(),
                                ));
                            }
                            Ok(code) => {
                                let _ = tx.send(ManageUpdate::Log(format!(
                                    "WARNING: vllm redeploy failed (exit code {code})"
                                )));
                            }
                            Err(e) => {
                                let _ = tx.send(ManageUpdate::Log(format!(
                                    "WARNING: vllm redeploy error: {e}"
                                )));
                            }
                        }
                    } else {
                        let _ = tx.send(ManageUpdate::Log(
                            "Skipping vLLM redeploy".into(),
                        ));
                    }
                }

                let _ = tx.send(ManageUpdate::Complete { success: true });
            }
            Ok(code) => {
                let _ = tx.send(ManageUpdate::Log(format!(
                    "FAILED: {action} {service} (exit code {code})"
                )));
                let _ = tx.send(ManageUpdate::Complete { success: false });
            }
            Err(e) => {
                let _ = tx.send(ManageUpdate::Log(format!(
                    "ERROR: {action} {service}: {e}"
                )));
                let _ = tx.send(ManageUpdate::Complete { success: false });
            }
        }
    });
}

fn kill_log_stream(app: &mut App) {
    if let Some(pid) = app.manage_log_child_pid.take() {
        unsafe {
            libc::kill(pid as i32, libc::SIGTERM);
        }
    }
    app.manage_log_streaming = false;
    app.manage_action_running = false;
}

/// Spawn a background worker that tails live application logs into the TUI log viewer.
/// Docker: `make manage SERVICE=x ACTION=logs`
/// Proxmox: same (underlying script uses journalctl -f)
fn spawn_log_tail_worker(app: &mut App) {
    let svc = match app.manage_services.get(app.manage_selected) {
        Some(s) => s.clone(),
        None => return,
    };

    kill_log_stream(app);

    let make_svc = service_to_make_name(&svc.name).to_string();

    let (tx, rx) = std::sync::mpsc::channel::<ManageUpdate>();
    app.manage_rx = Some(rx);
    app.manage_log.clear();
    app.manage_log_visible = true;
    app.manage_log_scroll = 0;
    app.manage_log_autoscroll = true;
    app.manage_action_running = true;
    app.manage_action_complete = false;
    app.manage_log_streaming = true;

    let is_remote = app.active_profile().map(|(_, p)| p.remote).unwrap_or(false);
    let repo_root = app.repo_root.clone();

    let profile_env: Option<String> = app
        .active_profile()
        .map(|(_, p)| p.environment.clone());
    let profile_backend: Option<String> = app
        .active_profile()
        .map(|(_, p)| p.backend.to_lowercase());
    let profile_llm_backend: Option<String> = app
        .active_profile()
        .and_then(|(_, p)| {
            p.hardware.as_ref().map(|h| match h.llm_backend {
                crate::modules::hardware::LlmBackend::Mlx => "mlx".to_string(),
                crate::modules::hardware::LlmBackend::Vllm => "vllm".to_string(),
                crate::modules::hardware::LlmBackend::Cloud => "cloud".to_string(),
            })
        });
    let profile_site_domain: Option<String> = app
        .active_profile()
        .and_then(|(_, p)| p.site_domain.clone())
        .filter(|v| !v.trim().is_empty());

    let ssh_details: Option<(String, String, String)> = if is_remote {
        app.active_profile().and_then(|(_, p)| {
            p.effective_host().map(|h| {
                (
                    h.to_string(),
                    p.effective_user().to_string(),
                    p.effective_ssh_key().to_string(),
                )
            })
        })
    } else {
        None
    };

    let profile_remote_path: Option<String> = app
        .active_profile()
        .map(|(_, p)| p.effective_remote_path().to_string());
    let profile_host: Option<String> = app
        .active_profile()
        .and_then(|(_, p)| p.effective_host().map(|s| s.to_string()));

    let (pid_tx, pid_rx) = std::sync::mpsc::channel::<u32>();

    std::thread::spawn(move || {
        use std::io::BufRead;
        use std::process::{Command, Stdio};

        let env_val = profile_env.as_deref().unwrap_or("development");
        let backend_val = profile_backend.as_deref().unwrap_or("docker");
        let site_domain_export = profile_site_domain
            .as_deref()
            .map(|d| format!("SITE_DOMAIN={d} "))
            .unwrap_or_default();
        let llm_backend_export = profile_llm_backend
            .as_deref()
            .map(|b| format!("LLM_BACKEND={b} "))
            .unwrap_or_default();
        let make_args = format!(
            "{site_domain_export}{llm_backend_export}manage SERVICE={make_svc} ACTION=logs ENV={env_val} BUSIBOX_ENV={env_val} BUSIBOX_BACKEND={backend_val}"
        );

        let child_result: std::io::Result<std::process::Child> = if is_remote {
            if let Some((ref host, ref user, ref key)) = ssh_details {
                let display_host = profile_host.as_deref().unwrap_or(host);
                let remote_path = profile_remote_path
                    .as_deref()
                    .unwrap_or("~/busibox");

                let full_cmd = format!(
                    "{preamble}\
                     [ -f \"$HOME/.profile\" ] && . \"$HOME/.profile\" 2>/dev/null || true; \
                     [ -f \"$HOME/.bashrc\" ] && . \"$HOME/.bashrc\" 2>/dev/null || true; \
                     export PYTHONUNBUFFERED=1; \
                     cd {remote_path} && USE_MANAGER=0 make {make_args} 2>&1",
                    preamble = remote::SHELL_PATH_PREAMBLE,
                );
                let mut args: Vec<String> = vec![
                    "-o".into(), "BatchMode=yes".into(),
                    "-o".into(), "StrictHostKeyChecking=accept-new".into(),
                    "-o".into(), "ConnectTimeout=10".into(),
                ];
                let key_path = crate::modules::ssh::shellexpand_path(key);
                if !key_path.is_empty() && std::path::Path::new(&key_path).exists() {
                    args.push("-i".into());
                    args.push(key_path);
                }
                let ssh_target = format!("{user}@{display_host}");
                args.push(ssh_target);
                args.push(full_cmd);

                Command::new("ssh")
                    .args(&args)
                    .stdout(Stdio::piped())
                    .stderr(Stdio::piped())
                    .spawn()
            } else {
                let _ = tx.send(ManageUpdate::Log("ERROR: No SSH connection configured".into()));
                let _ = tx.send(ManageUpdate::Complete { success: false });
                return;
            }
        } else {
            Command::new("make")
                .args(make_args.split_whitespace())
                .env("USE_MANAGER", "0")
                .env("PYTHONUNBUFFERED", "1")
                .current_dir(&repo_root)
                .stdout(Stdio::piped())
                .stderr(Stdio::piped())
                .spawn()
        };

        let mut child = match child_result {
            Ok(c) => c,
            Err(e) => {
                let _ = tx.send(ManageUpdate::Log(format!("ERROR: Failed to start log tail: {e}")));
                let _ = tx.send(ManageUpdate::Complete { success: false });
                return;
            }
        };

        let _ = pid_tx.send(child.id());

        let _ = tx.send(ManageUpdate::Log(format!(
            "Tailing logs for {make_svc}... (Esc to stop)"
        )));
        let _ = tx.send(ManageUpdate::Log(String::new()));

        let stdout = match child.stdout.take() {
            Some(s) => s,
            None => {
                let _ = tx.send(ManageUpdate::Log("ERROR: No stdout from log process".into()));
                let _ = tx.send(ManageUpdate::Complete { success: false });
                return;
            }
        };

        let stderr_tx = tx.clone();
        let stderr_handle = child.stderr.take().map(|stderr| {
            std::thread::spawn(move || {
                let reader = std::io::BufReader::new(stderr);
                for line in reader.lines() {
                    match line {
                        Ok(l) => {
                            let cleaned = remote::strip_ansi(&l);
                            if !cleaned.is_empty() {
                                let _ = stderr_tx.send(ManageUpdate::Log(cleaned));
                            }
                        }
                        Err(_) => break,
                    }
                }
            })
        });

        let reader = std::io::BufReader::new(stdout);
        for line in reader.lines() {
            match line {
                Ok(l) => {
                    let cleaned = remote::strip_ansi(&l);
                    let _ = tx.send(ManageUpdate::Log(cleaned));
                }
                Err(_) => break,
            }
        }

        if let Some(handle) = stderr_handle {
            let _ = handle.join();
        }

        let _ = child.wait();
        let _ = tx.send(ManageUpdate::Log(String::new()));
        let _ = tx.send(ManageUpdate::Log("--- Log stream ended ---".into()));
        let _ = tx.send(ManageUpdate::Complete { success: true });
    });

    if let Ok(pid) = pid_rx.recv_timeout(std::time::Duration::from_secs(5)) {
        app.manage_log_child_pid = Some(pid);
    }
}

fn copy_to_clipboard(text: &str) -> std::io::Result<()> {
    use std::io::Write;
    use std::process::{Command, Stdio};

    #[cfg(target_os = "macos")]
    let mut child = Command::new("pbcopy")
        .stdin(Stdio::piped())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()?;

    #[cfg(target_os = "linux")]
    let mut child = Command::new("xclip")
        .args(["-selection", "clipboard"])
        .stdin(Stdio::piped())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()?;

    #[cfg(not(any(target_os = "macos", target_os = "linux")))]
    return Err(std::io::Error::new(
        std::io::ErrorKind::Unsupported,
        "clipboard not supported",
    ));

    if let Some(mut stdin) = child.stdin.take() {
        stdin.write_all(text.as_bytes())?;
    }
    child.wait()?;
    Ok(())
}

/// Spawn a background worker that runs `make install SERVICE=<services> <extra_env>`
/// and feeds output into the manage screen's log viewer.
pub fn spawn_install_with_env(app: &mut App, services: &str, extra_env: &str) {
    kill_log_stream(app);

    let (tx, rx) = std::sync::mpsc::channel::<ManageUpdate>();
    app.manage_rx = Some(rx);
    app.manage_log.clear();
    app.manage_log_visible = true;
    app.manage_log_scroll = 0;
    app.manage_log_autoscroll = true;
    app.manage_log_streaming = false;
    app.manage_action_running = true;
    app.manage_action_complete = false;
    app.screen = Screen::Manage;

    let is_remote = app.active_profile().map(|(_, p)| p.remote).unwrap_or(false);
    let repo_root = app.repo_root.clone();
    let vault_password = app.vault_password.clone();
    let services = services.to_string();
    let extra_env = extra_env.to_string();

    let profile_env: Option<String> = app
        .active_profile()
        .map(|(_, p)| p.environment.clone());
    let profile_backend: Option<String> = app
        .active_profile()
        .map(|(_, p)| p.backend.to_lowercase());
    let profile_site_domain: Option<String> = app
        .active_profile()
        .and_then(|(_, p)| p.site_domain.clone())
        .filter(|v| !v.trim().is_empty());
    let profile_llm_backend: Option<String> = app
        .active_profile()
        .and_then(|(_, p)| {
            p.hardware.as_ref().map(|h| match h.llm_backend {
                crate::modules::hardware::LlmBackend::Mlx => "mlx".to_string(),
                crate::modules::hardware::LlmBackend::Vllm => "vllm".to_string(),
                crate::modules::hardware::LlmBackend::Cloud => "cloud".to_string(),
            })
        });

    let ssh_details: Option<(String, String, String)> = if is_remote {
        app.active_profile().and_then(|(_, p)| {
            p.effective_host().map(|h| {
                (
                    h.to_string(),
                    p.effective_user().to_string(),
                    p.effective_ssh_key().to_string(),
                )
            })
        })
    } else {
        None
    };

    let profile_remote_path: Option<String> = app
        .active_profile()
        .map(|(_, p)| p.effective_remote_path().to_string());
    let profile_host: Option<String> = app
        .active_profile()
        .and_then(|(_, p)| p.effective_host().map(|s| s.to_string()));
    let install_vault_prefix: Option<String> = app
        .active_profile()
        .and_then(|(id, p)| p.vault_prefix.clone().or(Some(id.to_string())));

    std::thread::spawn(move || {
        let remote_path = profile_remote_path
            .as_deref()
            .unwrap_or("~/busibox")
            .to_string();

        let _ = tx.send(ManageUpdate::Log(format!(
            "Installing {services} with updated settings..."
        )));

        if is_remote {
            if let Some((ref host, ref user, ref key)) = ssh_details {
                let display_host = profile_host.as_deref().unwrap_or(host);
                let _ = tx.send(ManageUpdate::Log(format!(
                    "Syncing files to {display_host}:{remote_path}..."
                )));
                let ssh = crate::modules::ssh::SshConnection::new(display_host, user, key);
                if let Err(e) = remote::ensure_remote_dir(&ssh, &remote_path) {
                    let _ = tx.send(ManageUpdate::Log(format!("ERROR: {e}")));
                    let _ = tx.send(ManageUpdate::Complete { success: false });
                    return;
                }
                if let Err(e) = remote::sync(&repo_root, display_host, user, key, &remote_path) {
                    let _ = tx.send(ManageUpdate::Log(format!("ERROR: rsync failed: {e}")));
                    let _ = tx.send(ManageUpdate::Complete { success: false });
                    return;
                }
                let _ = tx.send(ManageUpdate::Log("✓ Files synced".into()));

                // Push vault file to remote (already validated at profile unlock time)
                if let Some(ref vp) = install_vault_prefix {
                    if let Err(e) = remote::sync_vault_file(
                        &repo_root, display_host, user, key, &remote_path, vp,
                    ) {
                        let _ = tx.send(ManageUpdate::Log(format!(
                            "WARNING: vault push failed: {e}"
                        )));
                    }
                }

                // Clean up stale local state from remote
                let _ = remote::cleanup_remote_state(&ssh, &remote_path);
            }
        }

        let env_val = profile_env.as_deref().unwrap_or("development");
        let backend_val = profile_backend.as_deref().unwrap_or("docker");
        let site_domain_export = profile_site_domain
            .as_deref()
            .map(|d| format!("SITE_DOMAIN={d} "))
            .unwrap_or_default();
        let llm_backend_export = profile_llm_backend
            .as_deref()
            .map(|b| format!("LLM_BACKEND={b} "))
            .unwrap_or_default();
        let make_args = format!(
            "{extra_env} {site_domain_export}{llm_backend_export}install SERVICE={services} ENV={env_val} BUSIBOX_ENV={env_val} BUSIBOX_BACKEND={backend_val}"
        );
        let _ = tx.send(ManageUpdate::Log(format!("Running: make {make_args}")));

        let stream_tx = tx.clone();
        let mut last_line_i = String::new();
        let mut censored_count_i: usize = 0;
        let on_line = move |line: &str| {
            if line.contains("censored due to no_log") {
                censored_count_i += 1;
                return;
            }
            if censored_count_i > 0 {
                let _ = stream_tx.send(ManageUpdate::Log(
                    format!("  ... ({censored_count_i} censored lines suppressed)")
                ));
                censored_count_i = 0;
            }
            if line == last_line_i {
                return;
            }
            last_line_i = line.to_string();
            let _ = stream_tx.send(ManageUpdate::Log(format!("  {line}")));
        };

        let result: color_eyre::Result<i32> = if is_remote {
            if let Some((ref host, ref user, ref key)) = ssh_details {
                let ssh = crate::modules::ssh::SshConnection::new(
                    profile_host.as_deref().unwrap_or(host),
                    user,
                    key,
                );
                if let Some(ref vp) = vault_password {
                    remote::exec_make_quiet_with_vault_streaming(&ssh, &remote_path, &make_args, vp, on_line)
                } else {
                    remote::exec_make_quiet_streaming(&ssh, &remote_path, &make_args, on_line)
                }
            } else {
                Err(color_eyre::eyre::eyre!("No SSH connection"))
            }
        } else if let Some(ref vp) = vault_password {
            remote::run_local_make_quiet_with_vault_streaming(&repo_root, &make_args, vp, None, on_line)
        } else {
            remote::run_local_make_quiet_streaming(&repo_root, &make_args, on_line)
        };

        match result {
            Ok(0) => {
                let _ = tx.send(ManageUpdate::Log(format!(
                    "✓ {services} installed successfully"
                )));
                let _ = tx.send(ManageUpdate::Complete { success: true });
            }
            Ok(code) => {
                let _ = tx.send(ManageUpdate::Log(format!(
                    "✗ install {services} failed (exit code {code})"
                )));
                let _ = tx.send(ManageUpdate::Complete { success: false });
            }
            Err(e) => {
                let _ = tx.send(ManageUpdate::Log(format!("ERROR: {e}")));
                let _ = tx.send(ManageUpdate::Complete { success: false });
            }
        }
    });
}

// ─── Secrets sync check ─────────────────────────────────────────────────────

/// Run secrets-sync.sh locally (vault decryption needs local ansible-vault + vault password).
/// The script SSHs to the Proxmox host itself to read container env files.
fn fetch_secrets_sync(
    tx: &std::sync::mpsc::Sender<ManageUpdate>,
    ssh_details: Option<&(String, String, String)>,
    _is_remote: bool,
    _is_proxmox: bool,
    is_staging: bool,
    prefix: &str,
    vault_prefix: &str,
    backend: &str,
    _remote_busibox_path: Option<&str>,
    repo_root: &std::path::Path,
    vault_password: Option<&str>,
) {
    if vault_password.is_none() {
        send_all_secrets_status(tx, "no vault pw");
        return;
    }

    let script = repo_root.join("scripts/check/secrets-sync.sh");
    if !script.exists() {
        send_all_secrets_status(tx, "\u{2014}");
        return;
    }

    let mut args = vec![
        script.to_string_lossy().to_string(),
        "--backend".to_string(), backend.to_string(),
        "--prefix".to_string(), prefix.to_string(),
    ];
    if is_staging {
        args.push("--staging".to_string());
    }
    if !vault_prefix.is_empty() {
        args.push("--vault-prefix".to_string());
        args.push(vault_prefix.to_string());
    }
    if let Some((host, user, key)) = ssh_details {
        args.push("--ssh-host".to_string());
        args.push(host.clone());
        args.push("--ssh-user".to_string());
        args.push(user.clone());
        if !key.is_empty() {
            args.push("--ssh-key".to_string());
            args.push(key.clone());
        }
    }

    let mut cmd = std::process::Command::new("bash");
    cmd.args(&args);
    if let Some(pw) = vault_password {
        cmd.env("ANSIBLE_VAULT_PASSWORD", pw);
    }
    let raw_output = cmd.output().ok();
    // Log stderr debug lines to the manage log
    if let Some(ref out) = raw_output {
        if let Ok(stderr) = String::from_utf8(out.stderr.clone()) {
            for line in stderr.lines().filter(|l| l.starts_with("[DEBUG]")) {
                let _ = tx.send(ManageUpdate::Log(format!("secrets: {line}")));
            }
        }
    }
    let output = raw_output.and_then(|o| String::from_utf8(o.stdout).ok());

    let json_str = match output {
        Some(s) if !s.trim().is_empty() => s,
        _ => {
            // Mark all services as unchecked
            send_all_secrets_status(tx, "\u{2014}"); // em-dash
            return;
        }
    };

    // Parse JSON: {"services":{"authz":{"status":"synced","mismatched":[]},...}}
    // or {"error":"..."}
    let parsed: serde_json::Value = match serde_json::from_str(json_str.trim()) {
        Ok(v) => v,
        Err(_) => {
            send_all_secrets_status(tx, "error");
            return;
        }
    };

    if let Some(err) = parsed.get("error").and_then(|v| v.as_str()) {
        let msg = if err.len() > 20 { &err[..20] } else { err };
        send_all_secrets_status(tx, &format!("err: {msg}"));
        return;
    }

    if let Some(services) = parsed.get("services").and_then(|v| v.as_object()) {
        for (name, info) in services {
            let status = info.get("status").and_then(|v| v.as_str()).unwrap_or("error");
            let mismatched: Vec<&str> = info.get("mismatched")
                .and_then(|v| v.as_array())
                .map(|arr| arr.iter().filter_map(|v| v.as_str()).collect())
                .unwrap_or_default();

            let display = if status == "synced" {
                "synced".to_string()
            } else if status == "mismatch" {
                format!("mismatch: {}", mismatched.join(", "))
            } else if status == "down" {
                "down".to_string()
            } else {
                status.to_string()
            };

            let _ = tx.send(ManageUpdate::SecretsResult {
                name: name.clone(),
                secrets_status: display,
            });
        }
    }
}

/// Send a blanket secrets status to all services.
fn send_all_secrets_status(tx: &std::sync::mpsc::Sender<ManageUpdate>, status: &str) {
    // Services that have secrets to check
    let services = [
        "authz", "agent", "data", "search", "deploy", "litellm",
        "portal", "bridge", "postgres", "minio",
    ];
    for name in &services {
        let _ = tx.send(ManageUpdate::SecretsResult {
            name: name.to_string(),
            secrets_status: status.to_string(),
        });
    }
}
