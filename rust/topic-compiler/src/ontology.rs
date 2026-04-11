use crate::anthropic::Client;
use crate::synthesize::{Article, Relation, RelationKind};
use anyhow::{Context, Result};
use serde::Deserialize;
use std::path::Path;

pub async fn extract(client: &Client, mut articles: Vec<Article>) -> Result<Vec<Article>> {
    if articles.len() < 2 {
        return Ok(articles);
    }

    let system = include_str!("../prompts/ontology_extraction.md");

    let index: String = articles
        .iter()
        .map(|a| {
            let preview: String = a.body.lines().take(3).collect::<Vec<_>>().join(" ");
            format!("- slug={} | {}\n  {}", a.slug, a.title, truncate(&preview, 300))
        })
        .collect::<Vec<_>>()
        .join("\n");

    let user = format!(
        "Topics in the knowledge base:\n\n{index}\n\n\
         Return JSON: an array of {{from,kind,to,rationale}} objects where `kind` is one of \
         implements|extends|optimizes|contradicts|cites|prerequisite_of|trades_off|derived_from."
    );

    let raw = client.complete(system, None, &user, 4096).await?;

    #[derive(Deserialize)]
    struct Edge {
        from: String,
        kind: RelationKind,
        to: String,
        #[serde(default)]
        rationale: Option<String>,
    }

    let json = strip_fences(&raw);
    let edges: Vec<Edge> = serde_json::from_str(&json)
        .with_context(|| format!("parsing ontology JSON: {json}"))?;

    for edge in edges {
        if let Some(article) = articles.iter_mut().find(|a| a.slug == edge.from) {
            article.relations.push(Relation {
                kind: edge.kind,
                target: edge.to,
                rationale: edge.rationale,
            });
        }
    }

    Ok(articles)
}

pub async fn rebuild_from_fs(_client: &Client, _dir: &Path) -> Result<()> {
    // TODO(v0.2): read existing *.md, parse frontmatter via serde_yaml,
    // re-run extract(), then rewrite frontmatter in place.
    anyhow::bail!("graph subcommand not yet implemented")
}

fn truncate(s: &str, n: usize) -> String {
    if s.chars().count() <= n {
        s.to_string()
    } else {
        s.chars().take(n).collect()
    }
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
