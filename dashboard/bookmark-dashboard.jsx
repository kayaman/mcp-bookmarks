import { useState, useEffect, useCallback } from "react";

const API_BASE = "http://localhost:8000";

// ── Data fetching ────────────────────────────────────────────────

async function fetchJSON(path) {
  try {
    const res = await fetch(`${API_BASE}${path}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return await res.json();
  } catch (e) {
    console.error(`Fetch ${path}:`, e);
    return null;
  }
}

// ── Tag cloud sizing ─────────────────────────────────────────────

function tagScale(count, max) {
  if (max === 0) return 0.8;
  return 0.7 + (count / max) * 0.8;
}

// ── Components ───────────────────────────────────────────────────

function StatCard({ label, value, icon, accent }) {
  return (
    <div
      style={{
        background: "rgba(255,255,255,0.03)",
        border: "1px solid rgba(255,255,255,0.06)",
        borderRadius: 14,
        padding: "20px 24px",
        display: "flex",
        alignItems: "center",
        gap: 16,
        minWidth: 180,
      }}
    >
      <div
        style={{
          fontSize: 28,
          width: 48,
          height: 48,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          borderRadius: 12,
          background: `${accent}15`,
        }}
      >
        {icon}
      </div>
      <div>
        <div
          style={{
            fontSize: 28,
            fontWeight: 700,
            color: accent,
            fontFamily: "'JetBrains Mono', monospace",
            letterSpacing: -1,
          }}
        >
          {value}
        </div>
        <div style={{ fontSize: 12, color: "#666", textTransform: "uppercase", letterSpacing: 1.5 }}>
          {label}
        </div>
      </div>
    </div>
  );
}

function TagCloud({ tags, selectedTag, onSelectTag }) {
  const maxUsage = Math.max(...tags.map((t) => t.usage_count), 1);

  return (
    <div style={{ display: "flex", flexWrap: "wrap", gap: 8, padding: "8px 0" }}>
      <button
        onClick={() => onSelectTag(null)}
        style={{
          background: !selectedTag ? "#4ade80" : "rgba(255,255,255,0.04)",
          color: !selectedTag ? "#0a0a12" : "#888",
          border: "1px solid " + (!selectedTag ? "#4ade80" : "rgba(255,255,255,0.08)"),
          borderRadius: 20,
          padding: "6px 14px",
          fontSize: 13,
          cursor: "pointer",
          fontFamily: "'JetBrains Mono', monospace",
          transition: "all 0.2s",
        }}
      >
        all
      </button>
      {tags.map((tag) => {
        const isActive = selectedTag === tag.slug;
        const scale = tagScale(tag.usage_count, maxUsage);
        return (
          <button
            key={tag.slug}
            onClick={() => onSelectTag(isActive ? null : tag.slug)}
            title={`${tag.description} (${tag.usage_count} bookmarks)`}
            style={{
              background: isActive ? "#22d3ee" : "rgba(255,255,255,0.04)",
              color: isActive ? "#0a0a12" : `rgba(200,220,255,${0.5 + scale * 0.5})`,
              border: `1px solid ${isActive ? "#22d3ee" : "rgba(255,255,255,0.08)"}`,
              borderRadius: 20,
              padding: "6px 14px",
              fontSize: 11 + scale * 4,
              fontWeight: isActive ? 700 : 500,
              cursor: "pointer",
              fontFamily: "'JetBrains Mono', monospace",
              transition: "all 0.2s",
            }}
          >
            {tag.slug}
            <span style={{ opacity: 0.5, marginLeft: 4, fontSize: 10 }}>{tag.usage_count}</span>
          </button>
        );
      })}
    </div>
  );
}

function BookmarkCard({ bookmark }) {
  const [expanded, setExpanded] = useState(false);

  return (
    <div
      style={{
        background: "rgba(255,255,255,0.02)",
        border: "1px solid rgba(255,255,255,0.06)",
        borderRadius: 12,
        padding: "16px 20px",
        cursor: "pointer",
        transition: "all 0.2s",
      }}
      onClick={() => setExpanded(!expanded)}
      onMouseEnter={(e) => (e.currentTarget.style.borderColor = "rgba(74,222,128,0.3)")}
      onMouseLeave={(e) => (e.currentTarget.style.borderColor = "rgba(255,255,255,0.06)")}
    >
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 12 }}>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div
            style={{
              fontSize: 14,
              fontWeight: 600,
              color: "#e0e8f0",
              marginBottom: 4,
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
            }}
          >
            {bookmark.title || bookmark.url}
          </div>
          <div
            style={{
              fontSize: 12,
              color: "#555",
              fontFamily: "'JetBrains Mono', monospace",
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
            }}
          >
            {bookmark.url}
          </div>
        </div>
        <div style={{ display: "flex", gap: 6, flexShrink: 0, alignItems: "center" }}>
          {bookmark.has_content && (
            <span title="Content extracted" style={{ fontSize: 14, opacity: 0.5 }}>
              📄
            </span>
          )}
          {bookmark.summary && (
            <span title="Has summary" style={{ fontSize: 14, opacity: 0.5 }}>
              📝
            </span>
          )}
          {bookmark.word_count > 0 && (
            <span
              style={{
                fontSize: 10,
                color: "#555",
                fontFamily: "'JetBrains Mono', monospace",
              }}
            >
              {bookmark.word_count.toLocaleString()}w
            </span>
          )}
        </div>
      </div>

      {bookmark.tags?.length > 0 && (
        <div style={{ display: "flex", gap: 4, marginTop: 8, flexWrap: "wrap" }}>
          {bookmark.tags.map((tag) => (
            <span
              key={tag}
              style={{
                fontSize: 10,
                background: "rgba(74,222,128,0.08)",
                color: "#4ade80",
                border: "1px solid rgba(74,222,128,0.15)",
                borderRadius: 12,
                padding: "2px 8px",
                fontFamily: "'JetBrains Mono', monospace",
              }}
            >
              {tag}
            </span>
          ))}
        </div>
      )}

      {expanded && bookmark.summary && (
        <div
          style={{
            marginTop: 12,
            paddingTop: 12,
            borderTop: "1px solid rgba(255,255,255,0.06)",
            fontSize: 13,
            color: "#999",
            lineHeight: 1.6,
          }}
        >
          {bookmark.summary}
        </div>
      )}
    </div>
  );
}

function QuickSave({ onSaved }) {
  const [url, setUrl] = useState("");
  const [saving, setSaving] = useState(false);
  const [result, setResult] = useState(null);

  const save = async () => {
    if (!url.trim()) return;
    setSaving(true);
    setResult(null);
    try {
      const res = await fetch(`${API_BASE}/api/save`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url: url.trim() }),
      });
      const data = await res.json();
      if (data.status === "saved") {
        setResult({ ok: true, msg: `✓ Saved "${data.title || url}" (${data.word_count}w)` });
        setUrl("");
        onSaved();
      } else {
        setResult({ ok: false, msg: data.error || "Save failed" });
      }
    } catch (e) {
      setResult({ ok: false, msg: String(e) });
    }
    setSaving(false);
  };

  return (
    <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
      <input
        type="url"
        placeholder="Paste a URL to save..."
        value={url}
        onChange={(e) => setUrl(e.target.value)}
        onKeyDown={(e) => e.key === "Enter" && save()}
        style={{
          flex: 1,
          background: "rgba(255,255,255,0.03)",
          border: "1px solid rgba(255,255,255,0.08)",
          borderRadius: 10,
          padding: "10px 16px",
          color: "#e0e8f0",
          fontSize: 14,
          fontFamily: "'JetBrains Mono', monospace",
          outline: "none",
        }}
      />
      <button
        onClick={save}
        disabled={saving || !url.trim()}
        style={{
          background: saving ? "#333" : "linear-gradient(135deg, #4ade80, #22d3ee)",
          color: "#0a0a12",
          border: "none",
          borderRadius: 10,
          padding: "10px 20px",
          fontSize: 14,
          fontWeight: 700,
          cursor: saving ? "wait" : "pointer",
          opacity: !url.trim() ? 0.4 : 1,
          transition: "all 0.2s",
          whiteSpace: "nowrap",
        }}
      >
        {saving ? "Saving..." : "Save"}
      </button>
      {result && (
        <div
          style={{
            fontSize: 12,
            color: result.ok ? "#4ade80" : "#f87171",
            maxWidth: 300,
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
          }}
        >
          {result.msg}
        </div>
      )}
    </div>
  );
}

// ── Main Dashboard ───────────────────────────────────────────────

export default function Dashboard() {
  const [stats, setStats] = useState(null);
  const [tags, setTags] = useState([]);
  const [bookmarks, setBookmarks] = useState([]);
  const [selectedTag, setSelectedTag] = useState(null);
  const [search, setSearch] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [s, t] = await Promise.all([fetchJSON("/api/stats"), fetchJSON("/api/tags")]);
      if (!s || !t) throw new Error("Could not connect to server");
      setStats(s);
      setTags(t.tags || []);
    } catch (e) {
      setError(
        `Cannot connect to ${API_BASE}. Make sure the server is running: uv run mcp-bookmarks`
      );
    }
    setLoading(false);
  }, []);

  const loadBookmarks = useCallback(async () => {
    const params = new URLSearchParams();
    if (selectedTag) params.set("tag", selectedTag);
    if (search.trim()) params.set("query", search.trim());
    params.set("limit", "50");
    const data = await fetchJSON(`/api/bookmarks?${params}`);
    if (data) setBookmarks(data.bookmarks || []);
  }, [selectedTag, search]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  useEffect(() => {
    if (!error) loadBookmarks();
  }, [loadBookmarks, error]);

  if (error) {
    return (
      <div
        style={{
          minHeight: "100vh",
          background: "#0a0a12",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          fontFamily: "system-ui",
          padding: 40,
        }}
      >
        <div
          style={{
            background: "#1a1a2e",
            border: "1px solid #2a2a4a",
            borderRadius: 16,
            padding: 40,
            maxWidth: 500,
            textAlign: "center",
          }}
        >
          <div style={{ fontSize: 48, marginBottom: 16 }}>📚</div>
          <div style={{ color: "#f87171", fontSize: 14, marginBottom: 16 }}>{error}</div>
          <button
            onClick={refresh}
            style={{
              background: "#4ade80",
              color: "#0a0a12",
              border: "none",
              borderRadius: 8,
              padding: "8px 20px",
              fontWeight: 700,
              cursor: "pointer",
            }}
          >
            Retry
          </button>
        </div>
      </div>
    );
  }

  return (
    <div
      style={{
        minHeight: "100vh",
        background: "#0a0a12",
        color: "#e0e8f0",
        fontFamily: "'Outfit', system-ui, sans-serif",
        padding: "24px 32px",
      }}
    >
      <link
        href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;700&family=Outfit:wght@300;400;600;700;800&display=swap"
        rel="stylesheet"
      />

      {/* Header */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 28 }}>
        <div>
          <h1
            style={{
              fontSize: 26,
              fontWeight: 800,
              margin: 0,
              background: "linear-gradient(135deg, #4ade80, #22d3ee)",
              WebkitBackgroundClip: "text",
              WebkitTextFillColor: "transparent",
              letterSpacing: -0.5,
            }}
          >
            📚 Bookmark Knowledge Base
          </h1>
          <div style={{ fontSize: 12, color: "#444", marginTop: 4, fontFamily: "'JetBrains Mono', monospace" }}>
            MCP-powered · {API_BASE}
          </div>
        </div>
        <button
          onClick={() => {
            refresh();
            loadBookmarks();
          }}
          style={{
            background: "rgba(255,255,255,0.04)",
            border: "1px solid rgba(255,255,255,0.08)",
            borderRadius: 8,
            padding: "8px 16px",
            color: "#888",
            fontSize: 12,
            cursor: "pointer",
            fontFamily: "'JetBrains Mono', monospace",
          }}
        >
          ↻ Refresh
        </button>
      </div>

      {/* Stats */}
      {stats && (
        <div style={{ display: "flex", gap: 12, marginBottom: 24, flexWrap: "wrap" }}>
          <StatCard label="Bookmarks" value={stats.total_bookmarks} icon="🔖" accent="#4ade80" />
          <StatCard label="Tags" value={stats.total_tags} icon="🏷️" accent="#22d3ee" />
          <StatCard
            label="With Content"
            value={bookmarks.filter((b) => b.has_content).length}
            icon="📄"
            accent="#a78bfa"
          />
          <StatCard
            label="Summarized"
            value={bookmarks.filter((b) => b.summary).length}
            icon="📝"
            accent="#fbbf24"
          />
        </div>
      )}

      {/* Quick Save */}
      <div style={{ marginBottom: 24 }}>
        <QuickSave
          onSaved={() => {
            refresh();
            loadBookmarks();
          }}
        />
      </div>

      {/* Tag Cloud */}
      {tags.length > 0 && (
        <div style={{ marginBottom: 20 }}>
          <div style={{ fontSize: 11, color: "#444", textTransform: "uppercase", letterSpacing: 1.5, marginBottom: 8 }}>
            Filter by tag
          </div>
          <TagCloud tags={tags} selectedTag={selectedTag} onSelectTag={setSelectedTag} />
        </div>
      )}

      {/* Search */}
      <div style={{ marginBottom: 20 }}>
        <input
          type="text"
          placeholder="Search bookmarks..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          style={{
            width: "100%",
            background: "rgba(255,255,255,0.03)",
            border: "1px solid rgba(255,255,255,0.06)",
            borderRadius: 10,
            padding: "10px 16px",
            color: "#e0e8f0",
            fontSize: 14,
            fontFamily: "'JetBrains Mono', monospace",
            outline: "none",
          }}
        />
      </div>

      {/* Bookmarks list */}
      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        {loading && bookmarks.length === 0 ? (
          <div style={{ textAlign: "center", padding: 40, color: "#444" }}>Loading...</div>
        ) : bookmarks.length === 0 ? (
          <div style={{ textAlign: "center", padding: 40, color: "#444" }}>
            <div style={{ fontSize: 32, marginBottom: 12 }}>🔖</div>
            <div>No bookmarks yet. Save a URL above or use the MCP tools.</div>
          </div>
        ) : (
          bookmarks.map((b) => <BookmarkCard key={b.id} bookmark={b} />)
        )}
      </div>

      {/* Footer */}
      <div
        style={{
          marginTop: 32,
          paddingTop: 16,
          borderTop: "1px solid rgba(255,255,255,0.04)",
          fontSize: 11,
          color: "#333",
          textAlign: "center",
          fontFamily: "'JetBrains Mono', monospace",
        }}
      >
        {bookmarks.length} bookmarks shown · {tags.length} tags · Connect via MCP to tag and summarize
      </div>
    </div>
  );
}
