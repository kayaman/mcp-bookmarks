use crate::synthesize::Article;
use serde::Serialize;

/// YAML-serializable mirror of synthesize::Article with camelCase matching
/// the Astro content-collection schema in wiki/src/content/config.ts.
#[derive(Serialize)]
pub struct Frontmatter<'a> {
    pub slug: &'a str,
    pub title: &'a str,
    pub aliases: &'a [String],
    pub tags: &'a [String],
    pub confidence: f32,
    pub sources: Vec<SourceFm<'a>>,
    pub relations: Vec<RelationFm<'a>>,
    #[serde(rename = "compiledAt")]
    pub compiled_at: String,
    #[serde(rename = "compilerVersion")]
    pub compiler_version: &'static str,
}

#[derive(Serialize)]
pub struct SourceFm<'a> {
    #[serde(rename = "bookmarkId")]
    pub bookmark_id: &'a str,
    pub url: &'a str,
    pub title: &'a str,
    #[serde(rename = "savedAt")]
    pub saved_at: String,
}

#[derive(Serialize)]
pub struct RelationFm<'a> {
    pub kind: &'static str,
    pub target: &'a str,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub rationale: Option<&'a str>,
}

pub fn from_article(article: &Article) -> Frontmatter<'_> {
    Frontmatter {
        slug: &article.slug,
        title: &article.title,
        aliases: &article.aliases,
        tags: &article.tags,
        confidence: article.confidence,
        sources: article
            .sources
            .iter()
            .map(|s| SourceFm {
                bookmark_id: &s.bookmark_id,
                url: &s.url,
                title: &s.title,
                saved_at: s.saved_at.to_rfc3339(),
            })
            .collect(),
        relations: article
            .relations
            .iter()
            .map(|r| RelationFm {
                kind: relation_kind_str(r.kind),
                target: &r.target,
                rationale: r.rationale.as_deref(),
            })
            .collect(),
        compiled_at: article.compiled_at.to_rfc3339(),
        compiler_version: env!("CARGO_PKG_VERSION"),
    }
}

fn relation_kind_str(k: crate::synthesize::RelationKind) -> &'static str {
    use crate::synthesize::RelationKind::*;
    match k {
        Implements => "implements",
        Extends => "extends",
        Optimizes => "optimizes",
        Contradicts => "contradicts",
        Cites => "cites",
        PrerequisiteOf => "prerequisite_of",
        TradesOff => "trades_off",
        DerivedFrom => "derived_from",
    }
}
