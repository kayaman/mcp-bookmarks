//! Minimal fetch CLI: URL → JSON with status and HTML length.
//! Extend with readability-style extraction and stream to Python/CrewAI.

use clap::Parser;
use serde::Serialize;

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
    error: Option<String>,
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
            FetchOut {
                url: args.url,
                ok: status < 400,
                status,
                html_bytes: body.len(),
                error: None,
            }
        }
        Err(e) => FetchOut {
            url: args.url,
            ok: false,
            status: 0,
            html_bytes: 0,
            error: Some(e.to_string()),
        },
    };

    println!("{}", serde_json::to_string(&out).unwrap());
    if !out.ok {
        std::process::exit(1);
    }
}
