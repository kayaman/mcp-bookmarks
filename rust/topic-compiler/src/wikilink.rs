use crate::synthesize::Article;
use regex::Regex;
use std::collections::HashMap;

/// Build a {title|alias -> slug} map and rewrite inline mentions to `[[slug]]`.
/// Existing `[[...]]` links and fenced code blocks are left untouched.
pub fn rewrite(mut articles: Vec<Article>) -> Vec<Article> {
    let mut lookup: HashMap<String, String> = HashMap::new();
    for a in &articles {
        lookup.insert(normalize(&a.title), a.slug.clone());
        for alias in &a.aliases {
            lookup.insert(normalize(alias), a.slug.clone());
        }
    }

    // Sort keys by length, longest first — avoids partial matches.
    let mut keys: Vec<String> = lookup.keys().cloned().collect();
    keys.sort_by_key(|k| std::cmp::Reverse(k.len()));

    let code_fence = Regex::new(r"(?ms)```.*?```").expect("valid regex");

    for article in &mut articles {
        let body = article.body.clone();
        let mut rewritten = String::with_capacity(body.len());
        let mut cursor = 0;
        for m in code_fence.find_iter(&body) {
            rewritten.push_str(&rewrite_plain(&body[cursor..m.start()], &lookup, &keys));
            rewritten.push_str(&body[m.start()..m.end()]);
            cursor = m.end();
        }
        rewritten.push_str(&rewrite_plain(&body[cursor..], &lookup, &keys));
        article.body = rewritten;
    }

    articles
}

fn rewrite_plain(segment: &str, lookup: &HashMap<String, String>, keys: &[String]) -> String {
    let existing = Regex::new(r"\[\[[^\]]+\]\]").expect("valid regex");
    let mut out = String::with_capacity(segment.len());
    let mut cursor = 0;
    for m in existing.find_iter(segment) {
        out.push_str(&replace_terms(&segment[cursor..m.start()], lookup, keys));
        out.push_str(&segment[m.start()..m.end()]);
        cursor = m.end();
    }
    out.push_str(&replace_terms(&segment[cursor..], lookup, keys));
    out
}

fn replace_terms(text: &str, lookup: &HashMap<String, String>, keys: &[String]) -> String {
    let mut s = text.to_string();
    for key in keys {
        if let Some(slug) = lookup.get(key) {
            let pattern = format!(r"(?i)\b{}\b", regex::escape(key));
            if let Ok(re) = Regex::new(&pattern) {
                s = re.replace_all(&s, format!("[[{slug}]]")).into_owned();
            }
        }
    }
    s
}

fn normalize(s: &str) -> String {
    s.trim().to_lowercase()
}
