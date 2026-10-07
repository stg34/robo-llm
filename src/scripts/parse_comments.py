#!/usr/bin/env python3
"""Парсер комментариев Habr (article/comments.html) → markdown.

Habr рендерит комментарии плоским списком <article class="tm-comment-thread__comment">,
а вложенность ответов кодируется классом tm-comment-thread__indent_l-N (глубина 0..N).
Берём каждый <article>, вытаскиваем автора/время/рейтинг/тело и рендерим деревом.
"""
import re
import sys

from bs4 import BeautifulSoup, NavigableString, Tag


def render_body(node):
    """Рекурсивно превращает тело комментария в markdown-подобный текст."""
    out = []

    def walk(el):
        for child in el.children:
            if isinstance(child, NavigableString):
                out.append(str(child))
                continue
            if not isinstance(child, Tag):
                continue
            name = child.name
            if name == "p":
                walk(child)
                out.append("\n\n")
            elif name == "br":
                out.append("\n")
            elif name == "blockquote":
                inner = render_body(child)
                out.append("\n" + "\n".join("> " + l for l in inner.split("\n")) + "\n\n")
            elif name == "pre":
                out.append("\n```\n" + child.get_text() + "\n```\n\n")
            elif name == "code":
                out.append("`" + child.get_text() + "`")
            elif name in ("ul", "ol"):
                for li in child.find_all("li", recursive=False):
                    out.append("\n- " + render_body(li).strip())
                out.append("\n\n")
            elif name in ("b", "strong"):
                out.append("**"); walk(child); out.append("**")
            elif name in ("i", "em"):
                out.append("*"); walk(child); out.append("*")
            elif name == "a":
                txt = child.get_text(strip=True)
                href = child.get("href", "")
                out.append(f"[{txt}]({href})" if txt else "")
            elif name == "img":
                src = child.get("src", "")
                if src.startswith("//"):
                    src = "https:" + src
                out.append(f"![]({src})")
            else:
                walk(child)

    walk(node)
    text = "".join(out)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def parse(html):
    soup = BeautifulSoup(html, "html.parser")
    comments = []
    for art in soup.select("article.tm-comment-thread__comment"):
        cid = art.get("id", "")

        user_el = art.select_one(".tm-user-info__username")
        user = user_el.get_text(strip=True) if user_el else "—"

        time_el = art.select_one("time")
        when = (time_el.get("title") or time_el.get_text(strip=True)) if time_el else ""

        indent_el = art.select_one('[class*="tm-comment-thread__indent_l-"]')
        depth = 0
        if indent_el:
            m = re.search(r"tm-comment-thread__indent_l-(\d+)", " ".join(indent_el.get("class", [])))
            depth = int(m.group(1)) if m else 0

        score_el = art.select_one(".tm-votes-meter__value")
        score = score_el.get_text(strip=True) if score_el else None

        body_el = art.select_one(".tm-comment__body-content")
        body = render_body(body_el) if body_el else ""

        comments.append(dict(id=cid, user=user, when=when, depth=depth,
                             score=score, body=body))
    return comments


def to_markdown(comments):
    lines = ["# Комментарии к статье\n", f"Всего: {len(comments)}\n"]
    for c in comments:
        indent = "  " * c["depth"]
        score = ""
        if c["score"] and c["score"] not in ("0", "+0"):
            score = f" · рейтинг {c['score']}"
        lines.append(f"{indent}- **{c['user']}** · {c['when']}{score}")
        body = c["body"] or "_(пусто)_"
        for ln in body.split("\n"):
            lines.append(f"{indent}  {ln}" if ln else "")
        lines.append("")
    return "\n".join(lines)


def main():
    src = sys.argv[1] if len(sys.argv) > 1 else "article/comments.html"
    dst = sys.argv[2] if len(sys.argv) > 2 else "article/comments.md"
    comments = parse(open(src, encoding="utf-8").read())
    open(dst, "w", encoding="utf-8").write(to_markdown(comments))
    print(f"{len(comments)} комментариев → {dst}")


if __name__ == "__main__":
    main()
