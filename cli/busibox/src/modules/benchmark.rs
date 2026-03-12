use serde_json::Value;
use std::collections::HashMap;

#[derive(Debug, Clone, PartialEq)]
pub enum BenchmarkMode {
    Performance,
    ModelTests,
}

#[derive(Debug, Clone)]
pub struct ModelTestResult {
    pub test_name: String,
    pub tier: ModelTestTier,
    pub passed: bool,
    pub response_content: Option<String>,
    pub error: Option<String>,
    pub elapsed_ms: f64,
}

#[derive(Debug, Clone, PartialEq)]
pub enum ModelTestTier {
    DirectVllm,
    LiteLLM,
}

impl std::fmt::Display for ModelTestTier {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            ModelTestTier::DirectVllm => write!(f, "vLLM"),
            ModelTestTier::LiteLLM => write!(f, "LiteLLM"),
        }
    }
}

#[derive(Debug, Clone)]
pub struct BenchmarkConfig {
    pub max_tokens_throughput: usize,
    pub max_tokens_parallel: usize,
    pub parallel_count: usize,
    pub num_runs: usize,
    pub prompt: String,
}

impl Default for BenchmarkConfig {
    fn default() -> Self {
        Self {
            max_tokens_throughput: 256,
            max_tokens_parallel: 128,
            parallel_count: 4,
            num_runs: 3,
            prompt: "Write a short story about a robot learning to paint.".to_string(),
        }
    }
}

#[derive(Debug, Clone)]
pub struct BenchmarkResult {
    pub model_name: String,
    pub port: u16,
    pub ttft_ms: Option<f64>,
    pub throughput_tps: Option<f64>,
    pub parallel_tps: Option<f64>,
    pub parallel_latency_ms: Option<f64>,
}

impl BenchmarkResult {
    pub fn new(model_name: &str, port: u16) -> Self {
        Self {
            model_name: model_name.to_string(),
            port,
            ttft_ms: None,
            throughput_tps: None,
            parallel_tps: None,
            parallel_latency_ms: None,
        }
    }
}

#[derive(Debug, Clone)]
pub struct CurlResponse {
    pub completion_tokens: usize,
    pub elapsed_secs: f64,
    /// Error message from the API if the response was an error (e.g. "model not found").
    pub error_message: Option<String>,
}

/// Build a curl command that sends a chat completion request to vLLM and appends timing.
/// The output format is: JSON_BODY\n---BENCH_TIME:seconds---
pub fn build_curl_command(
    vllm_ip: &str,
    port: u16,
    model_name: &str,
    prompt: &str,
    max_tokens: usize,
) -> String {
    let body = serde_json::json!({
        "model": model_name,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "stream": false,
    });
    let body_str = body.to_string().replace('\'', "'\\''");

    format!(
        "curl -s -w '\\n---BENCH_TIME:%{{time_total}}---' \
         -H 'Content-Type: application/json' \
         --max-time 120 \
         -d '{}' \
         'http://{}:{}/v1/chat/completions'; true",
        body_str, vllm_ip, port
    )
}

/// Build a shell snippet that runs N parallel curl commands and collects all output.
/// Each request's output is delimited by ---BENCH_REQ:N--- markers.
pub fn build_parallel_curl_command(
    vllm_ip: &str,
    port: u16,
    model_name: &str,
    prompt: &str,
    max_tokens: usize,
    count: usize,
) -> String {
    let single = build_curl_command(vllm_ip, port, model_name, prompt, max_tokens);
    let mut script = String::from("OVERALL_START=$(date +%s%N 2>/dev/null || echo 0); ");
    for i in 0..count {
        script.push_str(&format!(
            "( echo '---BENCH_REQ:{i}---'; {single}; echo ''; echo '---BENCH_REQ_END:{i}---' ) & "
        ));
    }
    script.push_str("wait; ");
    script.push_str("OVERALL_END=$(date +%s%N 2>/dev/null || echo 0); ");
    script.push_str("echo \"---BENCH_WALL:$(( (OVERALL_END - OVERALL_START) ))---\"");
    script
}

/// Parse a single curl response that ends with ---BENCH_TIME:seconds---
pub fn parse_curl_response(output: &str) -> Option<CurlResponse> {
    let timing_marker = "---BENCH_TIME:";
    let timing_end = "---";

    let timing_pos = output.rfind(timing_marker)?;
    let after_marker = &output[timing_pos + timing_marker.len()..];
    let end_pos = after_marker.find(timing_end)?;
    let time_str = &after_marker[..end_pos];
    let elapsed_secs: f64 = time_str.trim().parse().ok()?;

    let json_part = output[..timing_pos].trim();
    let json: Value = serde_json::from_str(json_part).ok()?;

    let error_message = json
        .get("error")
        .and_then(|e| e.get("message"))
        .and_then(|m| m.as_str())
        .map(|s| s.to_string())
        .or_else(|| {
            json.get("message")
                .and_then(|m| m.as_str())
                .map(|s| s.to_string())
        });

    let completion_tokens = json
        .get("usage")
        .and_then(|u| u.get("completion_tokens"))
        .and_then(|t| t.as_u64())
        .unwrap_or(0) as usize;

    Some(CurlResponse {
        completion_tokens,
        elapsed_secs,
        error_message,
    })
}

/// Parse the output of a parallel curl run.
/// Returns individual CurlResponses and the overall wall-clock nanoseconds (if available).
pub fn parse_parallel_output(output: &str) -> (Vec<CurlResponse>, Option<u64>) {
    let mut responses = Vec::new();

    // Extract per-request blocks
    let mut i = 0;
    loop {
        let start_marker = format!("---BENCH_REQ:{i}---");
        let end_marker = format!("---BENCH_REQ_END:{i}---");

        let start_pos = match output.find(&start_marker) {
            Some(p) => p + start_marker.len(),
            None => break,
        };
        let end_pos = match output.find(&end_marker) {
            Some(p) => p,
            None => break,
        };

        let block = &output[start_pos..end_pos];
        if let Some(resp) = parse_curl_response(block.trim()) {
            responses.push(resp);
        }
        i += 1;
    }

    // Extract overall wall time in nanoseconds
    let wall_ns = output
        .rfind("---BENCH_WALL:")
        .and_then(|pos| {
            let after = &output[pos + "---BENCH_WALL:".len()..];
            let end = after.find("---")?;
            after[..end].trim().parse::<u64>().ok()
        });

    (responses, wall_ns)
}

/// Compute the median of a slice of f64 values.
pub fn median(values: &mut [f64]) -> f64 {
    if values.is_empty() {
        return 0.0;
    }
    values.sort_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));
    let mid = values.len() / 2;
    if values.len() % 2 == 0 {
        (values[mid - 1] + values[mid]) / 2.0
    } else {
        values[mid]
    }
}

// --- Model Test helpers ---

/// Build a curl command targeting LiteLLM proxy with auth header.
pub fn build_litellm_curl_command(
    litellm_ip: &str,
    port: u16,
    purpose_name: &str,
    api_key: &str,
    prompt: &str,
    max_tokens: usize,
) -> String {
    let body = serde_json::json!({
        "model": purpose_name,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "stream": false,
    });
    let body_str = body.to_string().replace('\'', "'\\''");

    // Append `; true` so the command always exits 0 — ssh.run() treats
    // non-zero exits as errors, but we want to parse the response ourselves.
    if api_key.is_empty() {
        format!(
            "curl -s -w '\\n---BENCH_TIME:%{{time_total}}---' \
             -H 'Content-Type: application/json' \
             --max-time 60 \
             -d '{}' \
             'http://{}:{}/v1/chat/completions'; true",
            body_str, litellm_ip, port
        )
    } else {
        let escaped_key = api_key.replace('\'', "'\\''");
        format!(
            "curl -s -w '\\n---BENCH_TIME:%{{time_total}}---' \
             -H 'Content-Type: application/json' \
             -H 'Authorization: Bearer {}' \
             --max-time 60 \
             -d '{}' \
             'http://{}:{}/v1/chat/completions'; true",
            escaped_key, body_str, litellm_ip, port
        )
    }
}

/// Parse a model test response, checking for valid choices content.
pub fn parse_model_test_response(output: &str) -> ModelTestResult {
    let mut result = ModelTestResult {
        test_name: String::new(),
        tier: ModelTestTier::DirectVllm,
        passed: false,
        response_content: None,
        error: None,
        elapsed_ms: 0.0,
    };

    match parse_curl_response(output) {
        Some(resp) => {
            result.elapsed_ms = resp.elapsed_secs * 1000.0;

            if let Some(ref err) = resp.error_message {
                result.error = Some(err.clone());
                return result;
            }

            // Try to extract the actual response content
            let timing_pos = output.rfind("---BENCH_TIME:").unwrap_or(output.len());
            let json_part = output[..timing_pos].trim();
            if let Ok(json) = serde_json::from_str::<Value>(json_part) {
                let content = json
                    .get("choices")
                    .and_then(|c| c.get(0))
                    .and_then(|c| c.get("message"))
                    .and_then(|m| m.get("content"))
                    .and_then(|c| c.as_str())
                    .map(|s| s.to_string());

                if let Some(text) = content {
                    if !text.is_empty() {
                        result.passed = true;
                        result.response_content = Some(text);
                    } else {
                        result.error = Some("Empty response content".to_string());
                    }
                } else {
                    result.error = Some("No choices[0].message.content in response".to_string());
                }
            } else {
                result.error = Some("Could not parse JSON response".to_string());
            }
        }
        None => {
            let preview: String = output.chars().take(120).collect();
            result.error = Some(format!("Could not parse curl output: {preview}"));
        }
    }

    result
}

/// Read model_purposes from a model_config.yml contents string.
pub fn parse_model_purposes(config_contents: &str) -> HashMap<String, String> {
    #[derive(serde::Deserialize)]
    struct Config {
        model_purposes: Option<HashMap<String, String>>,
    }

    serde_yaml::from_str::<Config>(config_contents)
        .ok()
        .and_then(|c| c.model_purposes)
        .unwrap_or_default()
}

/// Purposes that are testable via LiteLLM chat completions (excludes embedding, reranking, media).
pub fn testable_chat_purposes(purposes: &HashMap<String, String>) -> Vec<String> {
    let skip = ["embedding", "reranking", "image", "transcribe", "voice", "flux", "whisper", "kokoro"];
    let mut result: Vec<String> = purposes
        .keys()
        .filter(|k| !skip.iter().any(|s| k.as_str() == *s))
        .cloned()
        .collect();
    result.sort();
    result.dedup();
    result
}
