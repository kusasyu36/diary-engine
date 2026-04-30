"""GitHub Pages 用の静的サイトを生成する。
出力先: site/
構造:
  site/index.html              — トップ (最新の日付の5キャラを並べる)
  site/{date}/index.html       — その日付の5キャラ一覧
  site/{date}/{character}.html — 個別ページ
  site/{character}/index.html  — キャラ別アーカイブ (時系列)
  site/notes/{date}/{character}.txt — note 手動コピー用プレーンテキスト
  site/feed.xml                — RSS フィード (任意の購読者用)
  site/css/style.css           — シンプルなスタイル

設計原則:
- Jekyll を使わない (純粋な静的 HTML)。GitHub Pages は HTML をそのまま配信する。
- output/daily/YYYY-MM-DD/{character}.md を読んで、site/ に展開する。
- 日記本文と内省は markdown のままページ内に小さな変換 (見出し / 改行) を挟むだけ。
- 既存の output/daily を Single Source of Truth として、site/ は再生成可能な派生物にする。
"""
from __future__ import annotations

import html
import re
import sys
from datetime import date, datetime
from pathlib import Path

from characters import CHARACTERS, all_ids


BASE_DIR = Path(__file__).parent
DAILY_DIR = BASE_DIR / "output" / "daily"
SITE_DIR = BASE_DIR / "site"

# GitHub Pages のサブパス。ユーザーサイト (kusasyu36.github.io) の下の
# /diary-engine/ 配下に配信される前提。空文字にすればルート配信用。
# 環境変数 SITE_BASE で上書き可能 (例: ローカルプレビュー時に "" にする)
import os as _os
SITE_BASE = _os.environ.get("SITE_BASE", "/diary-engine").rstrip("/")


def url(path: str) -> str:
    """サブパス付きのURLを返す。path は先頭スラッシュなしでも有りでもOK。"""
    if not path.startswith("/"):
        path = "/" + path
    return SITE_BASE + path

CSS = """
* { box-sizing: border-box; }
body { font-family: -apple-system, BlinkMacSystemFont, "Hiragino Sans", "Yu Gothic", sans-serif;
       max-width: 720px; margin: 2em auto; padding: 0 1em; line-height: 1.7;
       color: #2a2a2a; background: #fafaf7; }
header { border-bottom: 1px solid #ddd; padding-bottom: 0.5em; margin-bottom: 1.5em; }
h1 { font-size: 1.6em; margin: 0; }
h2 { font-size: 1.2em; margin-top: 1.8em; padding-left: 0.4em; border-left: 3px solid #888; }
h3 { font-size: 1.05em; color: #555; margin-top: 1.4em; }
nav { font-size: 0.9em; margin-bottom: 1em; color: #666; }
nav a { color: #666; margin-right: 0.5em; }
.entry { margin-bottom: 2.4em; padding-bottom: 1.5em; border-bottom: 1px dashed #ccc; }
.entry:last-child { border-bottom: none; }
.meta { color: #888; font-size: 0.9em; margin-bottom: 0.6em; }
.diary { white-space: pre-wrap; }
.reflection { background: #f3f0e8; padding: 0.8em 1em; border-radius: 4px;
              font-size: 0.95em; color: #444; }
.life-update { color: #999; font-size: 0.85em; font-style: italic; }
.character-list { list-style: none; padding: 0; }
.character-list li { margin: 0.6em 0; }
.character-list a { font-weight: 600; }
.archive-list { padding-left: 1em; }
.archive-list li { margin: 0.3em 0; }
.toc { background: #efece4; padding: 0.6em 0.9em; border-radius: 4px;
       font-size: 0.9em; margin: 1em 0; line-height: 1.5; }
.toc a { color: #444; margin: 0 0.2em; }
.nav-pn { margin: 1.5em 0; padding: 0.6em 0.8em; background: #f0eee8;
          border-radius: 4px; font-size: 0.9em; }
.nav-pn a { color: #555; }
.nav-pn .nav-label { color: #888; margin-right: 0.4em; }
.nav-pn .nav-sep { color: #ccc; }
.latest-date { font-size: 1.05em; margin-top: 1.6em; color: #555; }
.latest-date a { color: #555; }
footer { margin-top: 3em; padding-top: 1em; border-top: 1px solid #ddd;
         color: #888; font-size: 0.85em; }
"""


# ─── Markdown → HTML (最小限) ────────────────────

_HEADER_RE = re.compile(r"^(#{1,4})\s+(.+)$")


def md_to_html(md: str) -> str:
    """非常に軽量な markdown→html。
    対応: # 〜 #### / 段落 / リスト (-) / 改行
    複雑な記法は使わない前提。
    """
    lines = md.split("\n")
    out: list[str] = []
    in_list = False
    para: list[str] = []

    def flush_para():
        if para:
            text = " ".join(para).strip()
            if text:
                out.append(f"<p>{html.escape(text)}</p>")
            para.clear()

    for raw in lines:
        line = raw.rstrip()
        if not line.strip():
            flush_para()
            if in_list:
                out.append("</ul>")
                in_list = False
            continue
        m = _HEADER_RE.match(line)
        if m:
            flush_para()
            if in_list:
                out.append("</ul>"); in_list = False
            level = len(m.group(1))
            out.append(f"<h{level}>{html.escape(m.group(2))}</h{level}>")
            continue
        if line.startswith("- "):
            flush_para()
            if not in_list:
                out.append("<ul>"); in_list = True
            out.append(f"<li>{html.escape(line[2:])}</li>")
            continue
        para.append(line)

    flush_para()
    if in_list:
        out.append("</ul>")
    return "\n".join(out)


# ─── ページ組み立て ────────────────────────────

def page_shell(title: str, body: str, breadcrumb_html: str = "") -> str:
    return f"""<!doctype html>
<html lang="ja"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)}</title>
<link rel="stylesheet" href="{url('/css/style.css')}">
</head><body>
<header><h1><a href="{url('/')}" style="color:inherit;text-decoration:none">{html.escape(title)}</a></h1></header>
{breadcrumb_html}
{body}
<footer>5体のAIが毎日12時に書く日記。生成は Gemini 2.5 Flash。</footer>
</body></html>"""


def parse_diary_md(md_path: Path) -> dict:
    """output/daily/{date}/{character}.md をセクション分解する。"""
    text = md_path.read_text(encoding="utf-8")
    sections: dict[str, str] = {}
    current_key: str | None = None
    buf: list[str] = []
    title = ""

    for line in text.split("\n"):
        if line.startswith("# "):
            title = line[2:].strip()
            continue
        if line.startswith("## "):
            if current_key is not None:
                sections[current_key] = "\n".join(buf).strip()
            current_key = line[3:].strip()
            buf = []
            continue
        if current_key is None:
            # ヘッダ前のメタ行 (- 物語内日付: ...)
            sections.setdefault("meta", "")
            sections["meta"] += line + "\n"
        else:
            buf.append(line)
    if current_key is not None:
        sections[current_key] = "\n".join(buf).strip()
    sections["title"] = title
    sections["meta"] = sections.get("meta", "").strip()
    return sections


def _extract_diary_parts(sections: dict) -> tuple[str, str, str, str, str]:
    """sections から (title, meta, diary_md, reflection_md, angle, life_update_md) を抽出。"""
    title = sections.get("title", "")
    meta = sections.get("meta", "")
    diary_md = ""
    reflection_md = ""
    angle = ""
    life_update_md = ""
    for k, v in sections.items():
        if k in ("title", "meta"):
            continue
        if k == "日記":
            diary_md = v
        elif k.startswith("内省"):
            reflection_md = v
            m = re.search(r"角度\s*:\s*([^)）]+)", k)
            if m:
                angle = m.group(1).strip()
        elif k == "life_state 更新":
            life_update_md = v
    return title, meta, diary_md, reflection_md, angle, life_update_md


def render_diary_block(char_id: str, date_iso: str, sections: dict, link_to_full: bool = True) -> str:
    """1キャラ1日分の日記ブロック。日付ページ・トップページに埋め込み用。"""
    title, meta, diary_md, reflection_md, angle, life_update_md = _extract_diary_parts(sections)
    out: list[str] = [f'<article class="entry" id="{html.escape(char_id)}">']
    if link_to_full:
        out.append(
            f'<h2><a href="{url(f"/{date_iso}/{char_id}.html")}">'
            f'{html.escape(title)}</a></h2>'
        )
    else:
        out.append(f"<h2>{html.escape(title)}</h2>")
    if meta:
        out.append('<div class="meta">' + html.escape(meta).replace("\n", "<br>") + "</div>")
    if diary_md:
        out.append('<div class="diary">' + html.escape(diary_md) + "</div>")
    if reflection_md:
        head = "内省" + (f"（角度: {html.escape(angle)}）" if angle else "")
        out.append(f"<h3>{head}</h3>")
        out.append('<div class="reflection">' + html.escape(reflection_md) + "</div>")
    if life_update_md:
        out.append(f'<p class="life-update">life_state 更新: {html.escape(life_update_md)}</p>')
    out.append("</article>")
    return "\n".join(out)


def _nav_within_day(
    char_id: str,
    date_iso: str,
    day_entries: list[tuple[str, dict]],
) -> str:
    """同じ日付内の前後キャラへのリンク。"""
    ids = [cid for cid, _ in day_entries]
    try:
        idx = ids.index(char_id)
    except ValueError:
        return ""
    prev_html = ""
    next_html = ""
    if idx > 0:
        pid = ids[idx - 1]
        ptitle = CHARACTERS[pid].display_name if pid in CHARACTERS else pid
        prev_html = f'<a href="{url(f"/{date_iso}/{pid}.html")}">← {html.escape(ptitle)}</a>'
    if idx + 1 < len(ids):
        nid = ids[idx + 1]
        ntitle = CHARACTERS[nid].display_name if nid in CHARACTERS else nid
        next_html = f'<a href="{url(f"/{date_iso}/{nid}.html")}">{html.escape(ntitle)} →</a>'
    if not prev_html and not next_html:
        return ""
    return (
        '<nav class="nav-pn"><span class="nav-label">同じ日の他キャラ:</span> '
        f'{prev_html}<span class="nav-sep"> | </span>'
        f'<a href="{url(f"/{date_iso}/")}">{html.escape(date_iso)} 全員</a>'
        f'<span class="nav-sep"> | </span>{next_html}</nav>'
    )


def _nav_across_days(
    char_id: str,
    date_iso: str,
    char_posts: list[tuple[str, dict]],
) -> str:
    """同じキャラの前後日付へのリンク。char_posts は時系列昇順。"""
    dates = [d for d, _ in char_posts]
    try:
        idx = dates.index(date_iso)
    except ValueError:
        return ""
    prev_html = ""
    next_html = ""
    if idx > 0:
        pd = dates[idx - 1]
        prev_html = f'<a href="{url(f"/{pd}/{char_id}.html")}">← {html.escape(pd)}</a>'
    if idx + 1 < len(dates):
        nd = dates[idx + 1]
        next_html = f'<a href="{url(f"/{nd}/{char_id}.html")}">{html.escape(nd)} →</a>'
    if not prev_html and not next_html:
        return ""
    return (
        '<nav class="nav-pn"><span class="nav-label">同じキャラの前後の日:</span> '
        f'{prev_html}<span class="nav-sep"> | </span>'
        f'<a href="{url(f"/{char_id}/")}">{html.escape(char_id)} アーカイブ</a>'
        f'<span class="nav-sep"> | </span>{next_html}</nav>'
    )


def render_character_page(
    char_id: str,
    date_iso: str,
    sections: dict,
    day_entries: list[tuple[str, dict]],
    char_posts: list[tuple[str, dict]],
) -> str:
    title = sections.get("title", char_id)
    breadcrumb = (
        f'<nav><a href="{url("/")}">トップ</a> / '
        f'<a href="{url(f"/{date_iso}/")}">{html.escape(date_iso)}</a> / '
        f'<a href="{url(f"/{char_id}/")}">{html.escape(char_id)} アーカイブ</a></nav>'
    )
    body = render_diary_block(char_id, date_iso, sections, link_to_full=False)
    body += "\n" + _nav_within_day(char_id, date_iso, day_entries)
    body += "\n" + _nav_across_days(char_id, date_iso, char_posts)
    return page_shell(title, body, breadcrumb)


def render_date_index(
    date_iso: str,
    entries: list[tuple[str, dict]],
    sorted_dates: list[str],
) -> str:
    """その日付の5キャラを全文埋め込みで1ページに並べる。日付間ナビあり。"""
    breadcrumb = f'<nav><a href="{url("/")}">トップ</a></nav>'
    body_parts: list[str] = [f"<p class='meta'>実カレンダー: {date_iso}</p>"]
    # 日付内の目次 (アンカーリンク)
    body_parts.append('<nav class="toc"><strong>このページの目次:</strong> ')
    toc_links = []
    for cid, sec in entries:
        title = sec.get("title", cid)
        toc_links.append(f'<a href="#{html.escape(cid)}">{html.escape(title)}</a>')
    body_parts.append(" / ".join(toc_links))
    body_parts.append("</nav>")
    # 全文埋め込み
    for cid, sec in entries:
        body_parts.append(render_diary_block(cid, date_iso, sec, link_to_full=True))
    # 前後日付ナビ
    try:
        idx = sorted_dates.index(date_iso)
    except ValueError:
        idx = -1
    prev_html = ""
    next_html = ""
    if idx > 0:
        pd = sorted_dates[idx - 1]
        prev_html = f'<a href="{url(f"/{pd}/")}">← {html.escape(pd)}</a>'
    if 0 <= idx < len(sorted_dates) - 1:
        nd = sorted_dates[idx + 1]
        next_html = f'<a href="{url(f"/{nd}/")}">{html.escape(nd)} →</a>'
    if prev_html or next_html:
        body_parts.append(
            '<nav class="nav-pn"><span class="nav-label">前後の日付:</span> '
            f'{prev_html}<span class="nav-sep"> | </span>'
            f'<a href="{url("/")}">トップ</a>'
            f'<span class="nav-sep"> | </span>{next_html}</nav>'
        )
    return page_shell(f"{date_iso} の日記", "\n".join(body_parts), breadcrumb)


def render_top_index(
    latest_date: str | None,
    all_dates: list[str],
    latest_entries: list[tuple[str, dict]],
) -> str:
    body_parts: list[str] = []
    body_parts.append(
        '<p class="meta">5人のAIキャラが毎日12:00 (JST) に日記を書きます。'
        ' 最新の5本を以下にすべて掲載。古い日付はページ末尾のアーカイブから。</p>'
    )

    if latest_date and latest_entries:
        body_parts.append(
            f'<h2 class="latest-date"><a href="{url(f"/{latest_date}/")}">'
            f'{html.escape(latest_date)} の5本</a></h2>'
        )
        # 目次
        toc_links = []
        for cid, sec in latest_entries:
            title = sec.get("title", cid)
            toc_links.append(
                f'<a href="{url(f"/{latest_date}/{cid}.html")}">'
                f'{html.escape(title)}</a>'
            )
        body_parts.append('<nav class="toc">' + " / ".join(toc_links) + "</nav>")
        # 全文埋め込み
        for cid, sec in latest_entries:
            body_parts.append(render_diary_block(cid, latest_date, sec, link_to_full=True))

    body_parts.append("<h2>キャラクター別アーカイブ</h2><ul class='character-list'>")
    for cid in all_ids():
        cfg = CHARACTERS[cid]
        body_parts.append(
            f'<li><a href="{url(f"/{cid}/")}">{html.escape(cfg.display_name)}</a> '
            f'<span class="meta">({html.escape(cid)})</span></li>'
        )
    body_parts.append("</ul>")

    if all_dates:
        body_parts.append("<h2>過去の日付</h2><ul class='archive-list'>")
        for d in reversed(all_dates):
            body_parts.append(f'<li><a href="{url(f"/{d}/")}">{html.escape(d)}</a></li>')
        body_parts.append("</ul>")

    return page_shell("5体のAI日記", "\n".join(body_parts))


def render_character_archive(char_id: str, posts: list[tuple[str, dict]]) -> str:
    cfg = CHARACTERS[char_id]
    body_parts: list[str] = [
        f"<p class='meta'>{html.escape(char_id)} の全日記</p>",
        "<ul class='archive-list'>",
    ]
    for date_iso, sec in reversed(posts):
        title = sec.get("title", char_id)
        body_parts.append(
            f'<li><a href="{url(f"/{date_iso}/{char_id}.html")}">'
            f'{html.escape(date_iso)} — {html.escape(title)}</a></li>'
        )
    body_parts.append("</ul>")
    breadcrumb = f'<nav><a href="{url("/")}">トップ</a></nav>'
    return page_shell(f"{cfg.display_name} アーカイブ", "\n".join(body_parts), breadcrumb)


def render_note_text(char_id: str, date_iso: str, sections: dict) -> str:
    """note 手動コピー用のプレーンテキスト (タイトル + 日記本文だけ)。"""
    title = sections.get("title", char_id)
    diary = sections.get("日記", "")
    return f"{title}\n{date_iso}\n\n{diary}\n"


# ─── メイン ──────────────────────────────────

def main() -> int:
    if not DAILY_DIR.exists():
        print(f"[publish_site] {DAILY_DIR} がない。先に daily_run.py を実行してください。", file=sys.stderr)
        return 1

    # 全 (date, character) を走査
    posts_by_date: dict[str, list[tuple[str, dict]]] = {}
    posts_by_char: dict[str, list[tuple[str, dict]]] = {cid: [] for cid in all_ids()}

    for date_dir in sorted(DAILY_DIR.iterdir()):
        if not date_dir.is_dir():
            continue
        # date_dir.name は YYYY-MM-DD のはず
        try:
            date.fromisoformat(date_dir.name)
        except ValueError:
            continue
        date_iso = date_dir.name
        for cid in all_ids():
            md_path = date_dir / f"{cid}.md"
            if not md_path.exists():
                continue
            sections = parse_diary_md(md_path)
            posts_by_date.setdefault(date_iso, []).append((cid, sections))
            posts_by_char[cid].append((date_iso, sections))

    if not posts_by_date:
        print("[publish_site] まだ日記が1件もない。何もしない。", file=sys.stderr)
        return 1

    SITE_DIR.mkdir(parents=True, exist_ok=True)
    css_dir = SITE_DIR / "css"
    css_dir.mkdir(exist_ok=True)
    (css_dir / "style.css").write_text(CSS, encoding="utf-8")
    notes_dir = SITE_DIR / "notes"
    notes_dir.mkdir(exist_ok=True)

    sorted_dates = sorted(posts_by_date.keys())
    latest = sorted_dates[-1] if sorted_dates else None

    # トップページ (最新日付の全文埋め込み)
    latest_entries = posts_by_date.get(latest, []) if latest else []
    (SITE_DIR / "index.html").write_text(
        render_top_index(latest, sorted_dates, latest_entries), encoding="utf-8",
    )

    # 日付別ページ + 個別ページ + note テキスト
    for date_iso, entries in posts_by_date.items():
        d = SITE_DIR / date_iso
        d.mkdir(exist_ok=True)
        (d / "index.html").write_text(
            render_date_index(date_iso, entries, sorted_dates), encoding="utf-8",
        )
        nd = notes_dir / date_iso
        nd.mkdir(exist_ok=True)
        for cid, sec in entries:
            (d / f"{cid}.html").write_text(
                render_character_page(cid, date_iso, sec, entries, posts_by_char[cid]),
                encoding="utf-8",
            )
            (nd / f"{cid}.txt").write_text(
                render_note_text(cid, date_iso, sec), encoding="utf-8",
            )

    # キャラ別アーカイブ
    for cid, posts in posts_by_char.items():
        if not posts:
            continue
        cdir = SITE_DIR / cid
        cdir.mkdir(exist_ok=True)
        (cdir / "index.html").write_text(
            render_character_archive(cid, posts), encoding="utf-8",
        )

    # 簡易 RSS (任意)
    feed_items: list[str] = []
    pubdate_now = datetime.now().astimezone().strftime("%a, %d %b %Y %H:%M:%S %z")
    for date_iso in reversed(sorted_dates):
        for cid, sec in posts_by_date[date_iso]:
            title = sec.get("title", cid)
            link = url(f"/{date_iso}/{cid}.html")
            desc = sec.get("日記", "")[:300]
            feed_items.append(
                f"<item><title>{html.escape(title)}</title>"
                f"<link>{html.escape(link)}</link>"
                f"<description>{html.escape(desc)}</description>"
                f"<pubDate>{pubdate_now}</pubDate>"
                f"</item>"
            )
    rss = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<rss version="2.0"><channel>'
        '<title>5体のAI日記</title>'
        '<description>5人のAIキャラが毎日書く日記</description>'
        '<link>/</link>'
        + "".join(feed_items)
        + "</channel></rss>"
    )
    (SITE_DIR / "feed.xml").write_text(rss, encoding="utf-8")

    print(f"[publish_site] 生成完了: {SITE_DIR}")
    print(f"  日付数: {len(sorted_dates)}, 投稿数: {sum(len(p) for p in posts_by_date.values())}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
