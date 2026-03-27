//! Fetch CLI: URL → JSON with status, HTML size, optional `<title>` (for Python/CrewAI ingestion).

use clap::Parser;
use regex::Regex;
use serde::Serialize;
use std::sync::OnceLock;

#[derive(Parser, Debug)]
#[command(name = "blogmarks-fetch")]
struct Args {
    #[arg(help = "Page URL to fetch")]
    url: String,
}

#[derive(Serialize)]
struct FetchOut {
    url: String,
    ok: bool,
    status: u16,
    html_bytes: usize,
    title: Option<String>,
    error: Option<String>,
}

static TITLE_RE: OnceLock<Regex> = OnceLock::new();

fn title_regex() -> &'static Regex {
    TITLE_RE.get_or_init(|| {
        Regex::new(r"(?is)<title[^>]*>([^<]{1,2000})</title>").expect("title regex")
    })
}

fn extract_title(html: &str) -> Option<String> {
    title_regex()
        .captures(html)
        .and_then(|c| c.get(1))
        .map(|m| {
            let t = m.as_str();
            // Collapse internal whitespace
            t.split_whitespace().collect::<Vec<_>>().join(" ")
        })
        .filter(|s| !s.is_empty())
}

#[tokio::main]
async fn main() {
    let args = Args::parse();
    let client = match reqwest::Client::builder()
        .user_agent("blogmarks-fetch/0.1 (+https://github.com/kayaman/mcp-bookmarks)")
        .build()
    {
        Ok(c) => c,
        Err(e) => {
            let out = FetchOut {
                url: args.url.clone(),
                ok: false,
                status: 0,
                html_bytes: 0,
                title: None,
                error: Some(format!("client: {e}")),
            };
            println!("{}", serde_json::to_string(&out).unwrap());
            std::process::exit(1);
        }
    };

    let res = client.get(&args.url).send().await;
    let out = match res {
        Ok(r) => {
            let status = r.status().as_u16();
            let body = r.bytes().await.unwrap_or_default();
            let html = String::from_utf8_lossy(&body);
            let title = if status < 400 {
                extract_title(&html)
            } else {
                None
            };
            FetchOut {
                url: args.url,
                ok: status < 400,
                status,
                html_bytes: body.len(),
                title,
                error: None,
            }
        }
        Err(e) => FetchOut {
            url: args.url,
            ok: false,
            status: 0,
            html_bytes: 0,
            title: None,
            error: Some(e.to_string()),
        },
    };

    println!("{}", serde_json::to_string(&out).unwrap());
    if !out.ok {
        std::process::exit(1);
    }
}
