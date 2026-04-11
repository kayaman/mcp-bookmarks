use crate::anthropic::Client;
use crate::source::Bookmark;
use anyhow::{Context, Result};
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Topic {
    pub slug: String,
    pub title: String,
    pub aliases: Vec<String>,
    pub tags: Vec<String>,
    pub bookmark_ids: Vec<String>,
}

/// Group bookmarks by tag and ask the LLM to pick topics per tag.
pub async fn discover_topics(
    client: &Client,
    corpus: &[Bookmark],
    min_bookmarks: usize,
) -> Result<Vec<Topic>> {
    let mut by_tag: BTreeMap<String, Vec<&Bookmark>> = BTreeMap::new();
    for b in corpus {
        for t in &b.ai_tags {
            by_tag.entry(t.clone()).or_default().push(b);
        }
    }

    let mut topics = Vec::new();
    for (tag, bookmarks) in by_tag {
        if bookmarks.len() < min_bookmarks {
            tracing::debug!(%tag, count = bookmarks.len(), "skip - below min_bookmarks");
            continue;
        }
        tracing::info!(%tag, count = bookmarks.len(), "topic discovery");
        let discovered = discover_for_tag(client, &tag, &bookmarks).await?;
        topics.extend(discovered);
    }

    Ok(topics)
}

async fn discover_for_tag(
    client: &Client,
    tag: &str,
    bookmarks: &[&Bookmark],
) -> Result<Vec<Topic>> {
    let system = include_str!("../prompts/topic_discovery.md");

    // Compact listing: title + summary only (keeps the prompt small).
    let listing: String = bookmarks
        .iter()
        .map(|b| format!("- id={} | {}\n  {}", b.id, b.title, b.ai_summary))
        .collect::<Vec<_>>()
        .join("\n");

    let user = format!(
        "Tag: {tag}\n\nBookmarks:\n{listing}\n\nReturn JSON array of topics."
    );

    let raw = client.complete(system, None, &user, 4096).await?;

    #[derive(Deserialize)]
    struct DiscoveredTopic {
        slug: String,
        title: String,
        #[serde(default)]
        aliases: Vec<String>,
        bookmark_ids: Vec<String>,
    }

    let json = strip_fences(&raw);
    let parsed: Vec<DiscoveredTopic> = serde_json::from_str(&json)
        .with_context(|| format!("parsing topic JSON: {json}"))?;

    Ok(parsed
        .into_iter()
        .map(|t| Topic {
            slug: t.slug,
            title: t.title,
            aliases: t.aliases,
            tags: vec![tag.to_string()],
            bookmark_ids: t.bookmark_ids,
        })
        .collect())
}

fn strip_fences(s: &str) -> String {
    let t = s.trim();
    if let Some(r) = t.strip_prefix("```json") {
        if let Some(end) = r.rfind("```") {
            return r[..end].trim().to_string();
        }
    }
    if let Some(r) = t.strip_prefix("```") {
        if let Some(end) = r.rfind("```") {
            return r[..end].trim().to_string();
        }
    }
    t.to_string()
}
