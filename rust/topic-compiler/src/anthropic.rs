use anyhow::{anyhow, Context, Result};
use reqwest::header::{HeaderMap, HeaderValue};
use serde::{Deserialize, Serialize};

const API_URL: &str = "https://api.anthropic.com/v1/messages";
const API_VERSION: &str = "2023-06-01";
const DEFAULT_MODEL: &str = "claude-opus-4-6";

pub struct Client {
    http: reqwest::Client,
    api_key: String,
    model: String,
}

#[derive(Serialize)]
struct MessageRequest<'a> {
    model: &'a str,
    max_tokens: u32,
    #[serde(skip_serializing_if = "Option::is_none")]
    system: Option<Vec<SystemBlock<'a>>>,
    messages: Vec<Message<'a>>,
}

#[derive(Serialize)]
struct SystemBlock<'a> {
    #[serde(rename = "type")]
    kind: &'static str,
    text: &'a str,
    #[serde(skip_serializing_if = "Option::is_none")]
    cache_control: Option<CacheControl>,
}

#[derive(Serialize)]
struct CacheControl {
    #[serde(rename = "type")]
    kind: &'static str,
}

#[derive(Serialize)]
struct Message<'a> {
    role: &'static str,
    content: &'a str,
}

#[derive(Deserialize)]
struct MessageResponse {
    content: Vec<ContentBlock>,
}

#[derive(Deserialize)]
struct ContentBlock {
    #[serde(rename = "type")]
    kind: String,
    #[serde(default)]
    text: String,
}

impl Client {
    pub fn from_env() -> Result<Self> {
        let api_key = std::env::var("ANTHROPIC_API_KEY")
            .context("ANTHROPIC_API_KEY must be set")?;
        let model = std::env::var("ANTHROPIC_MODEL")
            .unwrap_or_else(|_| DEFAULT_MODEL.to_string());
        Ok(Self {
            http: reqwest::Client::new(),
            api_key,
            model,
        })
    }

    /// Single-turn completion with an optional prompt-cached context block.
    pub async fn complete(
        &self,
        system: &str,
        cached_context: Option<&str>,
        user: &str,
        max_tokens: u32,
    ) -> Result<String> {
        let mut headers = HeaderMap::new();
        headers.insert(
            "x-api-key",
            HeaderValue::from_str(&self.api_key).context("invalid api key header")?,
        );
        headers.insert("anthropic-version", HeaderValue::from_static(API_VERSION));
        headers.insert("content-type", HeaderValue::from_static("application/json"));

        let mut system_blocks: Vec<SystemBlock> = vec![SystemBlock {
            kind: "text",
            text: system,
            cache_control: None,
        }];
        if let Some(ctx) = cached_context {
            system_blocks.push(SystemBlock {
                kind: "text",
                text: ctx,
                cache_control: Some(CacheControl { kind: "ephemeral" }),
            });
        }

        let req = MessageRequest {
            model: &self.model,
            max_tokens,
            system: Some(system_blocks),
            messages: vec![Message {
                role: "user",
                content: user,
            }],
        };

        let resp = self
            .http
            .post(API_URL)
            .headers(headers)
            .json(&req)
            .send()
            .await
            .context("POST /v1/messages")?;

        let status = resp.status();
        let body = resp.text().await?;
        if !status.is_success() {
            return Err(anyhow!("Anthropic API {}: {}", status, body));
        }

        let parsed: MessageResponse = serde_json::from_str(&body)
            .with_context(|| format!("parsing Anthropic response: {body}"))?;

        Ok(parsed
            .content
            .into_iter()
            .filter(|b| b.kind == "text")
            .map(|b| b.text)
            .collect::<Vec<_>>()
            .join("\n"))
    }
}
