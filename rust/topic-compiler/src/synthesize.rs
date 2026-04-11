use crate::anthropic::Client;
use crate::cluster::Topic;
use crate::source::Bookmark;
use anyhow::Result;
use chrono::Utc;
use serde::{Deserialize, Serialize};
use std::collections::HashMap;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Article {
    pub slug: String,
    pub title: String,
    pub aliases: Vec<String>,
    pub tags: Vec<String>,
    pub confidence: f32,
    pub sources: Vec<SourceRef>,
    pub relations: Vec<Relation>,
    pub compiled_at: chrono::DateTime<Utc>,
    pub body: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SourceRef {
    pub bookmark_id: String,
    pub url: String,
    pub title: String,
    pub saved_at: chrono::DateTime<Utc>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Relation {
    pub kind: RelationKind,
    pub target: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub rationale: Option<String>,
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum RelationKind {
    Implements,
    Extends,
    Optimizes,
    Contradicts,
    Cites,
    PrerequisiteOf,
    TradesOff,
    DerivedFrom,
}

pub async fn compile_all(
    client: &Client,
    topics: &[Topic],
    corpus: &[Bookmark],
) -> Result<Vec<Article>> {
    let by_id: HashMap<&str, &Bookmark> =
        corpus.iter().map(|b| (b.id.as_str(), b)).collect();

    let mut articles = Vec::with_capacity(topics.len());
    for topic in topics {
        let refs: Vec<&Bookmark> = topic
            .bookmark_ids
            .iter()
            .filter_map(|id| by_id.get(id.as_str()).copied())
            .collect();

        if refs.is_empty() {
            tracing::warn!(slug = %topic.slug, "no matching bookmarks; skip");
            continue;
        }

        tracing::info!(slug = %topic.slug, sources = refs.len(), "synthesize");
        let article = synthesize_one(client, topic, &refs).await?;
        articles.push(article);
    }
    Ok(articles)
}

async fn synthesize_one(
    client: &Client,
    topic: &Topic,
    refs: &[&Bookmark],
) -> Result<Article> {
    let system = include_str!("../prompts/article_synthesis.md");

    // Cacheable corpus block — the bookmark bodies.
    let corpus_block: String = refs
        .iter()
        .map(|b| {
            format!(
                "## SOURCE id={}\nTitle: {}\nURL: {}\n\n{}\n",
                b.id,
                b.title,
                b.url,
                truncate(&b.ai_content, 8_000),
            )
        })
        .collect::<Vec<_>>()
        .join("\n---\n");

    let user = format!(
        "Topic slug: {}\nTitle: {}\nAliases: {:?}\n\nWrite the topic article in Markdown. \
         Use `[[slug]]` wikilinks for any related concept you mention. \
         Do NOT include frontmatter.",
        topic.slug, topic.title, topic.aliases
    );

    let body = client
        .complete(system, Some(&corpus_block), &user, 4096)
        .await?;

    Ok(Article {
        slug: topic.slug.clone(),
        title: topic.title.clone(),
        aliases: topic.aliases.clone(),
        tags: topic.tags.clone(),
        confidence: confidence_from_sources(refs.len()),
        sources: refs
            .iter()
            .map(|b| SourceRef {
                bookmark_id: b.id.clone(),
                url: b.url.clone(),
                title: b.title.clone(),
                saved_at: b.saved_at,
            })
            .collect(),
        relations: Vec::new(),
        compiled_at: Utc::now(),
        body: body.trim().to_string(),
    })
}

fn truncate(s: &str, max_chars: usize) -> String {
    if s.len() <= max_chars {
        s.to_string()
    } else {
        let mut out: String = s.chars().take(max_chars).collect();
        out.push_str("\n\n[...truncated...]");
        out
    }
}

fn confidence_from_sources(n: usize) -> f32 {
    // Trivial heuristic: more sources -> more confidence, asymptote at 0.95.
    let x = n as f32;
    0.95_f32.min(0.4 + 0.1 * x)
}
