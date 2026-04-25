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
                word_count INTEGER
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


def count_by_type() -> dict:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT source_type, COUNT(*) as n FROM articles GROUP BY source_type"
        ).fetchall()
    return {r["source_type"]: r["n"] for r in rows}
