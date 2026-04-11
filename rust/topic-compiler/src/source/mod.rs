pub mod dynamodb;
pub mod sqlite;

use anyhow::{anyhow, Result};
use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Bookmark {
    pub id: String,
    pub url: String,
    pub title: String,
    pub ai_summary: String,
    pub ai_content: String,
    pub ai_tags: Vec<String>,
    pub saved_at: DateTime<Utc>,
}

pub async fn load(backend: &str, only_tag: Option<&str>) -> Result<Vec<Bookmark>> {
    match backend {
        "sqlite" => sqlite::load(only_tag).await,
        "dynamodb" => dynamodb::load(only_tag).await,
        other => Err(anyhow!(
            "unknown source backend: {other}. Allowed values: sqlite, dynamodb"
        )),
    }
}
