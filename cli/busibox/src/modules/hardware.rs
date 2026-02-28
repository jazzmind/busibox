use color_eyre::Result;
use serde::{Deserialize, Serialize};
use std::process::Command;

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "lowercase")]
pub enum Os {
    Linux,
    Darwin,
    Unknown,
}

impl std::fmt::Display for Os {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Os::Linux => write!(f, "Linux"),
            Os::Darwin => write!(f, "macOS"),
            Os::Unknown => write!(f, "Unknown"),
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "lowercase")]
pub enum Arch {
    Aarch64,
    X86_64,
    Unknown,
}

impl std::fmt::Display for Arch {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Arch::Aarch64 => write!(f, "ARM64"),
            Arch::X86_64 => write!(f, "x86_64"),
            Arch::Unknown => write!(f, "Unknown"),
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "lowercase")]
pub enum LlmBackend {
    Mlx,
    Vllm,
    Cloud,
}

impl std::fmt::Display for LlmBackend {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            LlmBackend::Mlx => write!(f, "MLX (Apple Silicon)"),
            LlmBackend::Vllm => write!(f, "vLLM (NVIDIA GPU)"),
            LlmBackend::Cloud => write!(f, "Cloud (no local GPU)"),
        }
    }
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq, PartialOrd, Ord)]
#[serde(rename_all = "lowercase")]
pub enum MemoryTier {
    Test,
    Minimal,
    Standard,
    Enhanced,
    Professional,
    Enterprise,
    Ultra,
}

impl MemoryTier {
    pub fn from_ram_gb(ram_gb: u64) -> Self {
        match ram_gb {
            0..=15 => MemoryTier::Test,
            16..=23 => MemoryTier::Minimal,
            24..=47 => MemoryTier::Standard,
            48..=95 => MemoryTier::Enhanced,
            96..=127 => MemoryTier::Professional,
            128..=255 => MemoryTier::Enterprise,
            _ => MemoryTier::Ultra,
        }
    }

    pub fn name(&self) -> &'static str {
        match self {
            MemoryTier::Test => "test",
            MemoryTier::Minimal => "minimal",
            MemoryTier::Standard => "standard",
            MemoryTier::Enhanced => "enhanced",
            MemoryTier::Professional => "professional",
            MemoryTier::Enterprise => "enterprise",
            MemoryTier::Ultra => "ultra",
        }
    }

    pub fn description(&self) -> &'static str {
        match self {
            MemoryTier::Test => "Tiny model for installation testing (~300MB)",
            MemoryTier::Minimal => "Lightweight models for basic use",
            MemoryTier::Standard => "Balanced performance for most workloads",
            MemoryTier::Enhanced => "High-quality models",
            MemoryTier::Professional => "Professional-grade inference",
            MemoryTier::Enterprise => "Enterprise-scale with largest models",
            MemoryTier::Ultra => "Maximum capability - run anything",
        }
    }
}

impl std::fmt::Display for MemoryTier {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{}", self.name())
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct GpuInfo {
    pub name: String,
    pub vram_gb: u64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct HardwareProfile {
    pub os: Os,
    pub arch: Arch,
    pub ram_gb: u64,
    pub gpus: Vec<GpuInfo>,
    pub apple_silicon: bool,
    pub docker_available: bool,
    pub proxmox_available: bool,
    pub llm_backend: LlmBackend,
    pub memory_tier: MemoryTier,
}

impl HardwareProfile {
    /// Detect hardware on the local machine.
    pub fn detect_local() -> Result<Self> {
        let os = detect_os();
        let arch = detect_arch();
        let ram_gb = detect_ram_gb(&os);
        let apple_silicon = os == Os::Darwin && arch == Arch::Aarch64;
        let gpus = detect_gpus();
        let docker_available = which::which("docker").is_ok();
        let proxmox_available = which::which("pct").is_ok();

        let llm_backend = if apple_silicon {
            LlmBackend::Mlx
        } else if !gpus.is_empty() {
            LlmBackend::Vllm
        } else {
            LlmBackend::Cloud
        };

        let effective_ram = if llm_backend == LlmBackend::Vllm {
            gpus.iter().map(|g| g.vram_gb).sum()
        } else {
            ram_gb
        };

        let memory_tier = MemoryTier::from_ram_gb(effective_ram);

        Ok(HardwareProfile {
            os,
            arch,
            ram_gb,
            gpus,
            apple_silicon,
            docker_available,
            proxmox_available,
            llm_backend,
            memory_tier,
        })
    }

    /// Detect hardware on a remote machine via SSH.
    pub fn detect_remote(host: &str, user: &str, key: &str) -> Result<Self> {
        let ssh_prefix = build_ssh_prefix(host, user, key);

        let os = detect_os_remote(&ssh_prefix);
        let arch = detect_arch_remote(&ssh_prefix);
        let ram_gb = detect_ram_gb_remote(&ssh_prefix, &os);
        let apple_silicon = os == Os::Darwin && arch == Arch::Aarch64;
        let gpus = detect_gpus_remote(&ssh_prefix);
        let docker_available = ssh_command_succeeds(&ssh_prefix, "command -v docker >/dev/null 2>&1 || test -x /usr/bin/docker || test -x /usr/local/bin/docker");
        let proxmox_available = ssh_command_succeeds(&ssh_prefix, "command -v pct >/dev/null 2>&1 || test -x /usr/sbin/pct || test -x /usr/bin/pct");

        let llm_backend = if apple_silicon {
            LlmBackend::Mlx
        } else if !gpus.is_empty() {
            LlmBackend::Vllm
        } else {
            LlmBackend::Cloud
        };

        let effective_ram = if llm_backend == LlmBackend::Vllm {
            gpus.iter().map(|g| g.vram_gb).sum()
        } else {
            ram_gb
        };

        let memory_tier = MemoryTier::from_ram_gb(effective_ram);

        Ok(HardwareProfile {
            os,
            arch,
            ram_gb,
            gpus,
            apple_silicon,
            docker_available,
            proxmox_available,
            llm_backend,
            memory_tier,
        })
    }
}

fn detect_os() -> Os {
    match std::env::consts::OS {
        "linux" => Os::Linux,
        "macos" => Os::Darwin,
        _ => Os::Unknown,
    }
}

fn detect_arch() -> Arch {
    match std::env::consts::ARCH {
        "aarch64" => Arch::Aarch64,
        "x86_64" => Arch::X86_64,
        _ => Arch::Unknown,
    }
}

fn detect_ram_gb(os: &Os) -> u64 {
    match os {
        Os::Darwin => {
            Command::new("sysctl")
                .args(["-n", "hw.memsize"])
                .output()
                .ok()
                .and_then(|o| String::from_utf8(o.stdout).ok())
                .and_then(|s| s.trim().parse::<u64>().ok())
                .map(|bytes| bytes / (1024 * 1024 * 1024))
                .unwrap_or(0)
        }
        Os::Linux => {
            std::fs::read_to_string("/proc/meminfo")
                .ok()
                .and_then(|contents| {
                    contents
                        .lines()
                        .find(|l| l.starts_with("MemTotal:"))
                        .and_then(|line| {
                            line.split_whitespace()
                                .nth(1)
                                .and_then(|kb| kb.parse::<u64>().ok())
                        })
                })
                .map(|kb| kb / (1024 * 1024))
                .unwrap_or(0)
        }
        _ => 0,
    }
}

fn detect_gpus() -> Vec<GpuInfo> {
    let output = Command::new("nvidia-smi")
        .args([
            "--query-gpu=name,memory.total",
            "--format=csv,noheader,nounits",
        ])
        .output();

    match output {
        Ok(o) if o.status.success() => parse_nvidia_output(&String::from_utf8_lossy(&o.stdout)),
        _ => Vec::new(),
    }
}

fn parse_nvidia_output(output: &str) -> Vec<GpuInfo> {
    output
        .lines()
        .filter(|l| !l.trim().is_empty())
        .filter_map(|line| {
            let parts: Vec<&str> = line.splitn(2, ',').collect();
            if parts.len() == 2 {
                let name = parts[0].trim().to_string();
                let vram_mb: u64 = parts[1].trim().parse().unwrap_or(0);
                Some(GpuInfo {
                    name,
                    vram_gb: vram_mb / 1024,
                })
            } else {
                None
            }
        })
        .collect()
}

fn build_ssh_prefix(host: &str, user: &str, key: &str) -> Vec<String> {
    let mut args = vec![
        "ssh".to_string(),
        "-o".to_string(),
        "BatchMode=yes".to_string(),
        "-o".to_string(),
        "StrictHostKeyChecking=accept-new".to_string(),
        "-o".to_string(),
        "ConnectTimeout=10".to_string(),
    ];
    if !key.is_empty() {
        args.push("-i".to_string());
        args.push(shellexpand(key));
    }
    args.push(format!("{user}@{host}"));
    args
}

fn shellexpand(path: &str) -> String {
    if path.starts_with("~/") {
        if let Some(home) = dirs::home_dir() {
            return format!("{}{}", home.display(), &path[1..]);
        }
    }
    path.to_string()
}

fn ssh_run(prefix: &[String], cmd: &str) -> Option<String> {
    let mut args = prefix.to_vec();
    args.push(cmd.to_string());
    Command::new(&args[0])
        .args(&args[1..])
        .output()
        .ok()
        .filter(|o| o.status.success())
        .and_then(|o| String::from_utf8(o.stdout).ok())
}

fn ssh_command_succeeds(prefix: &[String], cmd: &str) -> bool {
    ssh_run(prefix, cmd).is_some()
}

fn detect_os_remote(prefix: &[String]) -> Os {
    ssh_run(prefix, "uname -s")
        .map(|s| match s.trim().to_lowercase().as_str() {
            "linux" => Os::Linux,
            "darwin" => Os::Darwin,
            _ => Os::Unknown,
        })
        .unwrap_or(Os::Unknown)
}

fn detect_arch_remote(prefix: &[String]) -> Arch {
    ssh_run(prefix, "uname -m")
        .map(|s| match s.trim() {
            "aarch64" | "arm64" => Arch::Aarch64,
            "x86_64" => Arch::X86_64,
            _ => Arch::Unknown,
        })
        .unwrap_or(Arch::Unknown)
}

fn detect_ram_gb_remote(prefix: &[String], os: &Os) -> u64 {
    match os {
        Os::Darwin => ssh_run(prefix, "sysctl -n hw.memsize")
            .and_then(|s| s.trim().parse::<u64>().ok())
            .map(|b| b / (1024 * 1024 * 1024))
            .unwrap_or(0),
        Os::Linux => ssh_run(prefix, "grep MemTotal /proc/meminfo")
            .and_then(|line| {
                line.split_whitespace()
                    .nth(1)
                    .and_then(|kb| kb.parse::<u64>().ok())
            })
            .map(|kb| kb / (1024 * 1024))
            .unwrap_or(0),
        _ => 0,
    }
}

fn detect_gpus_remote(prefix: &[String]) -> Vec<GpuInfo> {
    ssh_run(
        prefix,
        "nvidia-smi --query-gpu=name,memory.total --format=csv,noheader,nounits 2>/dev/null",
    )
    .map(|s| parse_nvidia_output(&s))
    .unwrap_or_default()
}
