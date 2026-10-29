"""Turn documents into chunks for the knowledge store: by heading first, then by size.

Config `stores.knowledge` in config/context.yaml:

    chunk_tokens: 400     the most tokens one chunk may hold (chars_div_4 estimate)
    chunk_overlap: 40     tokens repeated from the end of one chunk at the start of the next

Markdown is split at every `#`, `##` and `###` heading, so a chunk never straddles two
sections and its citation can name the section. A section longer than `chunk_tokens` is cut
into windows of whole words that overlap by `chunk_overlap`. PDF text comes from `pypdf`,
page by page; a page is a section named `page N`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

HEADING = re.compile(r"^(#{1,3})\s+(.+?)\s*#*\s*$")


@dataclass
class Section:
    heading: str  # "" for text before the first heading
    text: str


@dataclass
class Chunk:
    position: int
    heading: str
    text: str


@dataclass
class Document:
    doc_id: str
    title: str
    path: str
    text: str
    sections: list[Section]
    kind: str  # markdown | pdf


def split_markdown(text: str) -> tuple[str, list[Section]]:
    """The title (the first `#` heading) and the sections under each heading."""
    if text.startswith("---\n"):
        _, _, text = text[4:].partition("\n---\n")
    title, sections = "", []
    heading, lines = "", []
    for line in text.splitlines():
        m = HEADING.match(line)
        if m:
            if "\n".join(lines).strip():
                sections.append(Section(heading, "\n".join(lines).strip()))
            level, name = len(m.group(1)), m.group(2).strip()
            if level == 1 and not title:
                title, heading = name, ""
            else:
                heading = name
            lines = []
        else:
            lines.append(line)
    if "\n".join(lines).strip():
        sections.append(Section(heading, "\n".join(lines).strip()))
    return title, sections


def windows(text: str, chunk_tokens: int, overlap_tokens: int) -> list[str]:
    """Cut text into word windows of at most chunk_tokens (4 chars a token), overlapping."""
    max_chars, overlap_chars = chunk_tokens * 4, max(0, overlap_tokens * 4)
    if len(text) <= max_chars:
        return [text]
    words = text.split()
    out, start = [], 0
    while start < len(words):
        end, size = start, 0
        while end < len(words) and size + len(words[end]) + (1 if end > start else 0) <= max_chars:
            size += len(words[end]) + (1 if end > start else 0)
            end += 1
        end = max(end, start + 1)  # one giant word still moves forward
        out.append(" ".join(words[start:end]))
        if end >= len(words):
            break
        back, kept = end, 0
        while back > start + 1 and kept + len(words[back - 1]) + 1 <= overlap_chars:
            back -= 1
            kept += len(words[back]) + 1
        start = back
    return out


def chunk_document(doc: Document, chunk_tokens: int = 400, overlap: int = 40) -> list[Chunk]:
    chunks: list[Chunk] = []
    for section in doc.sections:
        for piece in windows(section.text, chunk_tokens, overlap):
            chunks.append(Chunk(len(chunks), section.heading, piece))
    return chunks


def load_markdown(path: Path, doc_id: str) -> Document:
    text = path.read_text(encoding="utf-8")
    title, sections = split_markdown(text)
    return Document(
        doc_id, title or path.stem.replace("_", " "), path.name, text, sections, "markdown"
    )


def load_pdf(path: Path, doc_id: str) -> Document:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    sections = []
    for n, page in enumerate(reader.pages, start=1):
        text = re.sub(r"[ \t]+", " ", page.extract_text() or "").strip()
        if text:
            sections.append(Section(f"page {n}", text))
    meta_title = (reader.metadata.title if reader.metadata else None) or ""
    title = meta_title.strip() or path.stem.replace("_", " ")
    full = "\n\n".join(s.text for s in sections)
    return Document(doc_id, title, path.name, full, sections, "pdf")


def load_document(path: Path) -> Document | None:
    doc_id = re.sub(r"[^a-z0-9_]+", "_", path.stem.lower()).strip("_") or "doc"
    if path.suffix.lower() in (".md", ".markdown"):
        return load_markdown(path, doc_id)
    if path.suffix.lower() == ".pdf":
        return load_pdf(path, doc_id)
    return None
