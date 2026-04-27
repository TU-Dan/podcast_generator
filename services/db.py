import sqlite3
import json
import os
import re
import uuid
from datetime import datetime, timezone

DB_PATH = "data/knowledge.db"
SCRIPTS_DIR = "static/scripts"


def get_conn():
    os.makedirs("data", exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS articles (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                source_url TEXT,
                source_type TEXT,
                tags TEXT DEFAULT '[]',
                summary TEXT,
                article_md_path TEXT,
                transcript_path TEXT,
                audio_url TEXT,
                audio_length INTEGER,
                image_url TEXT,
                created_at TEXT,
                word_count INTEGER,
                insights TEXT
            )
        """)
        # Migration: add insights column if it doesn't exist
        try:
            conn.execute("ALTER TABLE articles ADD COLUMN insights TEXT")
        except Exception:
            pass
        conn.execute("""
            CREATE TABLE IF NOT EXISTS feeds (
                id TEXT PRIMARY KEY,
                name TEXT,
                url TEXT NOT NULL,
                feed_type TEXT DEFAULT 'rss',
                last_fetched_at TEXT,
                created_at TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS feed_items (
                id TEXT PRIMARY KEY,
                feed_id TEXT REFERENCES feeds(id) ON DELETE CASCADE,
                feed_name TEXT,
                title TEXT,
                url TEXT,
                description TEXT,
                published_at TEXT,
                relevance_score INTEGER DEFAULT 50,
                status TEXT DEFAULT 'pending',
                created_at TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS highlights (
                id TEXT PRIMARY KEY,
                article_id TEXT NOT NULL,
                text TEXT NOT NULL,
                note TEXT,
                created_at TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS outputs (
                id TEXT PRIMARY KEY,
                article_id TEXT NOT NULL,
                format_type TEXT,
                pain_point TEXT,
                content TEXT,
                created_at TEXT
            )
        """)
        conn.commit()
    _scan_scripts_dir()


def _parse_script(path: str) -> dict:
    """Extract title, created_at, summary, word_count from a script .md file."""
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()

    lines = content.splitlines()

    # Title: first # heading
    title = ""
    for line in lines:
        if line.startswith("# "):
            title = line[2:].strip()
            break

    # created_at: **生成时间**: line
    created_at = datetime.now(timezone.utc).isoformat()
    for line in lines:
        if "生成时间" in line:
            # format: **生成时间**: 2026-04-09 19:48
            match = re.search(r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2})', line)
            if match:
                try:
                    dt = datetime.strptime(match.group(1), "%Y-%m-%d %H:%M")
                    created_at = dt.replace(tzinfo=timezone.utc).isoformat()
                except ValueError:
                    pass
            break

    # Body: everything after first ---
    body = ""
    sep_idx = content.find("\n---\n")
    if sep_idx != -1:
        body = content[sep_idx + 5:].strip()

    summary = body[:200].replace("\n", " ") if body else ""
    word_count = len(body)

    return {
        "title": title,
        "created_at": created_at,
        "summary": summary,
        "word_count": word_count,
    }


def _scan_scripts_dir():
    """Index all .md files in static/scripts/ that are not yet in the DB."""
    if not os.path.exists(SCRIPTS_DIR):
        return

    with get_conn() as conn:
        existing = {
            row[0] for row in conn.execute("SELECT article_md_path FROM articles WHERE article_md_path IS NOT NULL").fetchall()
        }

    added = 0
    for filename in sorted(os.listdir(SCRIPTS_DIR)):
        if not filename.endswith(".md"):
            continue
        url_path = f"/static/scripts/{filename}"
        if url_path in existing:
            continue

        file_path = os.path.join(SCRIPTS_DIR, filename)
        try:
            parsed = _parse_script(file_path)
        except Exception as e:
            print(f"[db] Failed to parse {filename}: {e}")
            continue

        add_article(
            title=parsed["title"] or filename,
            source_type="script",
            summary=parsed["summary"],
            article_md_path=url_path,
            word_count=parsed["word_count"],
            created_at_override=parsed["created_at"],
        )
        added += 1

    if added:
        print(f"[db] Indexed {added} scripts from {SCRIPTS_DIR}")


def add_article(
    title: str,
    source_url: str = None,
    source_type: str = None,
    summary: str = None,
    article_md_path: str = None,
    transcript_path: str = None,
    audio_url: str = None,
    audio_length: int = None,
    image_url: str = None,
    word_count: int = None,
    created_at_override: str = None,
) -> str:
    article_id = uuid.uuid4().hex
    created_at = created_at_override or datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO articles
                (id, title, source_url, source_type, summary, article_md_path,
                 transcript_path, audio_url, audio_length, image_url, created_at, word_count)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            article_id, title, source_url, source_type, summary, article_md_path,
            transcript_path, audio_url, audio_length, image_url,
            created_at, word_count,
        ))
        conn.commit()
    return article_id


def list_articles(source_type: str = None, query: str = None, tags: list[str] = None, limit: int = 100, offset: int = 0) -> list[dict]:
    with get_conn() as conn:
        conditions = []
        params = []

        if query:
            conditions.append("(a.title LIKE ? OR a.summary LIKE ?)")
            pattern = f"%{query}%"
            params.extend([pattern, pattern])

        if source_type:
            conditions.append("a.source_type = ?")
            params.append(source_type)

        if tags:
            placeholders = ",".join("?" * len(tags))
            conditions.append(
                f"(SELECT COUNT(DISTINCT je.value) FROM json_each(a.tags) je WHERE je.value IN ({placeholders})) = ?"
            )
            params.extend(tags)
            params.append(len(tags))

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        rows = conn.execute(
            f"SELECT a.* FROM articles a {where} ORDER BY a.created_at DESC LIMIT ? OFFSET ?",
            (*params, limit, offset),
        ).fetchall()
    return [dict(r) for r in rows]


def get_article(article_id: str) -> dict | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM articles WHERE id=?", (article_id,)).fetchone()
    return dict(row) if row else None


def get_untagged_articles() -> list[dict]:
    """Return articles that have no tags and have a script file."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM articles WHERE (tags IS NULL OR tags = '[]') AND article_md_path IS NOT NULL"
        ).fetchall()
    return [dict(r) for r in rows]


def update_insights(article_id: str, insights: dict):
    with get_conn() as conn:
        conn.execute(
            "UPDATE articles SET insights=? WHERE id=?",
            (json.dumps(insights, ensure_ascii=False), article_id)
        )
        conn.commit()


def update_tags(article_id: str, tags: list[str]):
    with get_conn() as conn:
        conn.execute(
            "UPDATE articles SET tags=? WHERE id=?",
            (json.dumps(tags, ensure_ascii=False), article_id)
        )
        conn.commit()


def list_all_tags() -> list[dict]:
    """Return all distinct tags with counts, sorted by frequency descending."""
    with get_conn() as conn:
        rows = conn.execute("SELECT tags FROM articles WHERE tags IS NOT NULL AND tags != '[]'").fetchall()
    freq: dict[str, int] = {}
    for row in rows:
        try:
            for tag in json.loads(row[0]):
                freq[tag] = freq.get(tag, 0) + 1
        except Exception:
            pass
    return [{"tag": t, "count": freq[t]} for t in sorted(freq, key=lambda t: -freq[t])]


def delete_article(article_id: str) -> bool:
    with get_conn() as conn:
        row = conn.execute("SELECT article_md_path, transcript_path FROM articles WHERE id=?", (article_id,)).fetchone()
        cur = conn.execute("DELETE FROM articles WHERE id=?", (article_id,))
        conn.commit()
    if row:
        for path in (row[0], row[1]):
            if path:
                try:
                    os.remove(path.lstrip("/"))
                except FileNotFoundError:
                    pass
    return cur.rowcount > 0


def update_article_title(article_id: str, title: str):
    with get_conn() as conn:
        conn.execute("UPDATE articles SET title=? WHERE id=?", (title, article_id))
        conn.commit()


# ── Feed management ──────────────────────────────────────────────────────────

def add_feed(url: str, name: str, feed_type: str = "rss") -> str:
    feed_id = uuid.uuid4().hex
    now = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO feeds (id, url, name, feed_type, created_at) VALUES (?,?,?,?,?)",
            (feed_id, url, name, feed_type, now),
        )
        conn.commit()
    return feed_id


def list_feeds() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM feeds ORDER BY created_at DESC").fetchall()
    return [dict(r) for r in rows]


def delete_feed(feed_id: str):
    with get_conn() as conn:
        conn.execute("DELETE FROM feed_items WHERE feed_id=?", (feed_id,))
        conn.execute("DELETE FROM feeds WHERE id=?", (feed_id,))
        conn.commit()


def update_feed_fetched(feed_id: str):
    with get_conn() as conn:
        conn.execute(
            "UPDATE feeds SET last_fetched_at=? WHERE id=?",
            (datetime.now(timezone.utc).isoformat(), feed_id),
        )
        conn.commit()


def upsert_feed_items(items: list[dict]):
    """Insert new feed items, skip duplicates by url."""
    with get_conn() as conn:
        existing = {r[0] for r in conn.execute("SELECT url FROM feed_items").fetchall()}
        for item in items:
            if item["url"] in existing:
                continue
            conn.execute(
                """INSERT INTO feed_items
                   (id, feed_id, feed_name, title, url, description, published_at, relevance_score, status, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (
                    uuid.uuid4().hex,
                    item["feed_id"],
                    item.get("feed_name", ""),
                    item["title"],
                    item["url"],
                    item.get("description", ""),
                    item.get("published_at", ""),
                    item.get("relevance_score", 50),
                    "pending",
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
        conn.commit()


def list_inbox(min_score: int = 0) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT * FROM feed_items WHERE status='pending' AND relevance_score >= ?
               ORDER BY relevance_score DESC, created_at DESC LIMIT 100""",
            (min_score,),
        ).fetchall()
    return [dict(r) for r in rows]


def count_inbox() -> int:
    with get_conn() as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM feed_items WHERE status='pending'"
        ).fetchone()[0]


def update_feed_item_status(item_id: str, status: str):
    with get_conn() as conn:
        conn.execute("UPDATE feed_items SET status=? WHERE id=?", (status, item_id))
        conn.commit()


# ── Article counts ────────────────────────────────────────────────────────────

def count_by_type() -> dict:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT source_type, COUNT(*) as n FROM articles GROUP BY source_type"
        ).fetchall()
    return {r["source_type"]: r["n"] for r in rows}


# ── Highlights ────────────────────────────────────────────────────────────────

def add_highlight(article_id: str, text: str) -> str:
    hid = uuid.uuid4().hex
    now = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO highlights (id, article_id, text, created_at) VALUES (?,?,?,?)",
            (hid, article_id, text, now),
        )
        conn.commit()
    return hid


def list_highlights(article_id: str) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM highlights WHERE article_id=? ORDER BY created_at ASC",
            (article_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_highlight(highlight_id: str) -> dict | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM highlights WHERE id=?", (highlight_id,)).fetchone()
    return dict(row) if row else None


def update_highlight_note(highlight_id: str, note: str):
    with get_conn() as conn:
        conn.execute("UPDATE highlights SET note=? WHERE id=?", (note, highlight_id))
        conn.commit()


def delete_highlight(highlight_id: str):
    with get_conn() as conn:
        conn.execute("DELETE FROM highlights WHERE id=?", (highlight_id,))
        conn.commit()


# ── Outputs ───────────────────────────────────────────────────────────────────

def save_output(article_id: str, format_type: str, content: str, pain_point: str = "") -> str:
    oid = uuid.uuid4().hex
    now = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO outputs (id, article_id, format_type, pain_point, content, created_at) VALUES (?,?,?,?,?,?)",
            (oid, article_id, format_type, pain_point, content, now),
        )
        conn.commit()
    return oid


def list_outputs(article_id: str) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM outputs WHERE article_id=? ORDER BY created_at DESC LIMIT 50",
            (article_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def delete_output(output_id: str):
    with get_conn() as conn:
        conn.execute("DELETE FROM outputs WHERE id=?", (output_id,))
        conn.commit()
