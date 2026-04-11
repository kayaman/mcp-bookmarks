use super::Bookmark;
use anyhow::{Context, Result};
use aws_sdk_dynamodb::{types::AttributeValue, Client};
use chrono::{DateTime, Utc};
use std::collections::HashMap;

pub async fn load(only_tag: Option<&str>) -> Result<Vec<Bookmark>> {
    let config = aws_config::load_from_env().await;
    let client = Client::new(&config);

    let table =
        std::env::var("DYNAMODB_LINKS_TABLE").unwrap_or_else(|_| "blogmarks-links".into());

    // Scan is fine at this scale; switch to Query on feed-savedAt GSI later.
    let mut out = Vec::new();
    let mut last_key: Option<HashMap<String, AttributeValue>> = None;

    loop {
        let mut req = client.scan().table_name(&table);
        if let Some(k) = last_key.clone() {
            req = req.set_exclusive_start_key(Some(k));
        }
        let resp = req.send().await.context("dynamodb scan")?;

        for item in resp.items() {
            let Some(bm) = item_to_bookmark(item) else { continue };
            if let Some(only) = only_tag {
                if !bm.ai_tags.iter().any(|t| t == only) {
                    continue;
                }
            }
            out.push(bm);
        }

        match resp.last_evaluated_key {
            Some(k) if !k.is_empty() => last_key = Some(k),
            _ => break,
        }
    }

    Ok(out)
}

fn item_to_bookmark(item: &HashMap<String, AttributeValue>) -> Option<Bookmark> {
    let status = item.get("aiStatus").and_then(|a| a.as_s().ok())?;
    if status != "DONE" {
        return None;
    }
    let content = item.get("aiContent").and_then(|a| a.as_s().ok())?.to_string();
    if content.is_empty() {
        return None;
    }

    let id = item.get("id").and_then(|a| a.as_s().ok())?.to_string();
    let url = item.get("url").and_then(|a| a.as_s().ok())?.to_string();
    let title = item
        .get("title")
        .and_then(|a| a.as_s().ok())
        .cloned()
        .unwrap_or_default();
    let summary = item
        .get("aiSummary")
        .and_then(|a| a.as_s().ok())
        .cloned()
        .unwrap_or_default();
    let tags: Vec<String> = item
        .get("aiTags")
        .and_then(|a| a.as_l().ok())
        .map(|l| l.iter().filter_map(|v| v.as_s().ok().cloned()).collect())
        .unwrap_or_default();
    let saved_at: DateTime<Utc> = item
        .get("savedAt")
        .and_then(|a| a.as_s().ok())
        .and_then(|s| {
            DateTime::parse_from_rfc3339(s)
                .map_err(|e| {
                    tracing::warn!(bookmark_id = %id, raw = %s, err = %e, "unparseable savedAt; using UNIX_EPOCH");
                })
                .ok()
        })
        .map(|d| d.with_timezone(&Utc))
        .unwrap_or_else(|| DateTime::<Utc>::from_timestamp(0, 0).expect("epoch is valid"));

    Some(Bookmark {
        id,
        url,
        title,
        ai_summary: summary,
        ai_content: content,
        ai_tags: tags,
        saved_at,
    })
}
