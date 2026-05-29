mod anthropic;
mod cluster;
mod frontmatter;
mod ontology;
mod render;
mod source;
mod synthesize;
mod wikilink;

use anyhow::Result;
use clap::{Parser, Subcommand};
use std::path::PathBuf;

#[derive(Parser)]
#[command(name = "topic-compiler", version, about = "Compile a bookmark corpus into an Astro topic wiki")]
struct Cli {
    #[command(subcommand)]
    cmd: Cmd,
}

#[derive(Subcommand)]
enum Cmd {
    /// Compile topics from the current bookmark corpus.
    Compile {
        /// Source backend: "sqlite" (local mcp-bookmarks) or "dynamodb" (live corpus)
        #[arg(long, default_value = "sqlite")]
        source: String,
        /// Output directory (Astro content collection path)
        #[arg(long)]
        out: PathBuf,
        /// Minimum bookmarks under a tag before topic discovery runs
        #[arg(long, default_value_t = 5)]
        min_bookmarks: usize,
        /// Restrict compilation to a single tag slug
        #[arg(long)]
        only_tag: Option<String>,
        /// Skip ontology extraction pass (faster, wikilinks only)
        #[arg(long, default_value_t = false)]
        no_ontology: bool,
    },
    /// Re-run ontology extraction over an already-compiled collection on disk.
    Graph {
        #[arg(long)]
        dir: PathBuf,
    },
}

#[tokio::main]
async fn main() -> Result<()> {
    tracing_subscriber::fmt()
        .with_env_filter(tracing_subscriber::EnvFilter::from_default_env())
        .init();

    let cli = Cli::parse();
    match cli.cmd {
        Cmd::Compile {
            source,
            out,
            min_bookmarks,
            only_tag,
            no_ontology,
        } => {
            let corpus = source::load(&source, only_tag.as_deref()).await?;
            tracing::info!(count = corpus.len(), "loaded bookmarks");

            let client = anthropic::Client::from_env()?;

            let topics = cluster::discover_topics(&client, &corpus, min_bookmarks).await?;
            tracing::info!(count = topics.len(), "discovered topics");

            let mut articles = synthesize::compile_all(&client, &topics, &corpus).await?;

            if !no_ontology {
                articles = ontology::extract(&client, articles).await?;
            }

            let resolved = wikilink::rewrite(articles);
            render::write_collection(&resolved, &out)?;
            tracing::info!("compile complete");
        }
        Cmd::Graph { dir } => {
            let client = anthropic::Client::from_env()?;
            ontology::rebuild_from_fs(&client, &dir).await?;
        }
    }
    Ok(())
}
