use crate::frontmatter;
use crate::synthesize::Article;
use anyhow::{Context, Result};
use std::fs;
use std::path::Path;

pub fn write_collection(articles: &[Article], out: &Path) -> Result<()> {
    fs::create_dir_all(out).with_context(|| format!("mkdir {}", out.display()))?;

    for article in articles {
        let fm = frontmatter::from_article(article);
        let yaml = serde_yaml::to_string(&fm).context("serialize frontmatter")?;
        let file_path = out.join(format!("{}.md", article.slug));
        let contents = format!("---\n{yaml}---\n\n{}\n", article.body);
        write_atomic(&file_path, contents.as_bytes())?;
        tracing::info!(path = %file_path.display(), "wrote article");
    }
    Ok(())
}

fn write_atomic(path: &Path, bytes: &[u8]) -> Result<()> {
    let tmp = path.with_extension("md.tmp");
    fs::write(&tmp, bytes).with_context(|| format!("write {}", tmp.display()))?;
    fs::rename(&tmp, path).with_context(|| format!("rename into place: {}", path.display()))?;
    Ok(())
}
