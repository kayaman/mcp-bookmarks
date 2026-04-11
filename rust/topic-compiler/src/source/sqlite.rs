use super::Bookmark;
use anyhow::{Context, Result};
use chrono::{DateTime, NaiveDateTime, Utc};
use rusqlite::Connection;
use std::path::PathBuf;

pub async fn load(only_tag: Option<&str>) -> Result<Vec<Bookmark>> {
    let db_path = std::env::var("BOOKMARKS_DB_PATH")
        .map(PathBuf::from)
        .unwrap_or_else(|_| {
            let home = std::env::var("HOME").unwrap_or_else(|_| ".".into());
            PathBuf::from(home).join(".mcp-bookmarks/bookmarks.db")
        });

    let conn = Connection::open(&db_path)
        .with_context(|| format!("opening sqlite db at {}", db_path.display()))?;

    // Matches the schema in src/mcp_bookmarks/db.py (bookmarks + bookmark_tags + tags).
    // Adjust the column names here if the Python schema diverges.
    let sql = r#"
        SELECT
            b.id,
            b.url,
            COALESCE(b.title, '') AS title,
            COALESCE(b.summary, '') AS ai_summary,
            COALESCE(b.content, '') AS ai_content,
            COALESCE(GROUP_CONCAT(t.slug, '|'), '') AS tags,
            b.created_at
        FROM bookmarks b
        LEFT JOIN bookmark_tags bt ON bt.bookmark_id = b.id
        LEFT JOIN tags t ON t.slug = bt.tag_slug
        WHERE b.content IS NOT NULL AND b.content != ''
        GROUP BY b.id
    "#;

    let mut stmt = conn.prepare(sql)?;
    let rows = stmt.query_map([], |row| {
        Ok((
            row.get::<_, i64>(0)?.to_string(),
            row.get::<_, String>(1)?,
            row.get::<_, String>(2)?,
            row.get::<_, String>(3)?,
            row.get::<_, String>(4)?,
            row.get::<_, String>(5)?,
            row.get::<_, String>(6)?,
        ))
    })?;

    let mut out = Vec::new();
    for row in rows {
        let (id, url, title, summary, content, tags_raw, created_at) = row?;
        let tags: Vec<String> = if tags_raw.is_empty() {
            Vec::new()
        } else {
            tags_raw.split('|').map(|s| s.to_string()).collect()
        };
        if let Some(only) = only_tag {
            if !tags.iter().any(|t| t == only) {
                continue;
            }
        }
        out.push(Bookmark {
            id,
            url,
            title,
            ai_summary: summary,
            ai_content: content,
            ai_tags: tags,
            saved_at: parse_ts(&created_at),
        });
    }

    Ok(out)
}

fn parse_ts(s: &str) -> DateTime<Utc> {
    if let Ok(d) = DateTime::parse_from_rfc3339(s) {
        return d.with_timezone(&Utc);
    }
    if let Ok(n) = NaiveDateTime::parse_from_str(s, "%Y-%m-%d %H:%M:%S") {
        return DateTime::<Utc>::from_naive_utc_and_offset(n, Utc);
    }
    Utc::now()
}
