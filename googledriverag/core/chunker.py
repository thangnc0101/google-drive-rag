from __future__ import annotations

import hashlib
from dataclasses import dataclass

import tiktoken

from googledriverag.core.document_parser import ParsedDocument, SectionInfo


@dataclass
class Chunk:
    chunk_id: str
    text: str
    sequence_index: int
    token_count: int
    page: int | None = None
    section: str | None = None


class Chunker:
    def __init__(self, max_tokens: int = 512, overlap_tokens: int = 50):
        self.max_tokens = max_tokens
        self.overlap_tokens = overlap_tokens
        self.encoder = tiktoken.get_encoding("cl100k_base")

    def chunk(self, doc: ParsedDocument, doc_id: str = "") -> list[Chunk]:
        if doc.sections:
            return self._chunk_by_sections(doc, doc_id)
        return self._chunk_flat(doc, doc_id)

    def _chunk_by_sections(self, doc: ParsedDocument, doc_id: str) -> list[Chunk]:
        heading_stack: list[str] = []
        page_map = self._build_page_map(doc)
        chunks: list[Chunk] = []
        seq = 0

        for section in doc.sections:
            while heading_stack and len(heading_stack) >= section.level and section.level > 0:
                heading_stack.pop()
            if section.heading:
                heading_stack.append(section.heading)

            heading_path = " > ".join(heading_stack) if heading_stack else ""
            paragraphs = [p.strip() for p in section.text.split("\n\n") if p.strip()]
            if not paragraphs:
                continue

            segments = self._merge_paragraphs(paragraphs)
            for segment in segments:
                text = f"[{heading_path}] {segment}" if heading_path else segment
                hash_input = f"{doc_id}:{text}" if doc_id else text
                chunk_id = "chunk-" + hashlib.md5(hash_input.encode()).hexdigest()[:12]
                token_count = len(self.encoder.encode(text))
                page = page_map.get(segment[:80]) if page_map else None
                chunks.append(Chunk(
                    chunk_id=chunk_id,
                    text=text,
                    sequence_index=seq,
                    token_count=token_count,
                    page=page,
                    section=heading_path or None,
                ))
                seq += 1

        return chunks

    def _chunk_flat(self, doc: ParsedDocument, doc_id: str) -> list[Chunk]:
        paragraphs = [p.strip() for p in doc.text.split("\n\n") if p.strip()]
        if not paragraphs:
            return []

        merged_segments = self._merge_paragraphs(paragraphs)
        page_map = self._build_page_map(doc)

        chunks = []
        for i, segment in enumerate(merged_segments):
            hash_input = f"{doc_id}:{segment}" if doc_id else segment
            chunk_id = "chunk-" + hashlib.md5(hash_input.encode()).hexdigest()[:12]
            token_count = len(self.encoder.encode(segment))
            page = page_map.get(segment[:80]) if page_map else None
            chunks.append(Chunk(
                chunk_id=chunk_id,
                text=segment,
                sequence_index=i,
                token_count=token_count,
                page=page,
            ))
        return chunks

    def _merge_paragraphs(self, paragraphs: list[str]) -> list[str]:
        merged: list[str] = []
        current_segment = ""
        current_tokens = 0

        for para in paragraphs:
            para_tokens = len(self.encoder.encode(para))
            if current_tokens + para_tokens <= self.max_tokens:
                current_segment = (current_segment + "\n\n" + para).strip()
                current_tokens += para_tokens
            else:
                if current_segment:
                    merged.append(current_segment)
                if para_tokens > self.max_tokens:
                    merged.extend(self._split_long_text(para))
                else:
                    current_segment = para
                    current_tokens = para_tokens
                    continue
                current_segment = ""
                current_tokens = 0

        if current_segment:
            merged.append(current_segment)
        return merged

    def _split_long_text(self, text: str) -> list[str]:
        tokens = self.encoder.encode(text)
        step = self.max_tokens - self.overlap_tokens
        segments = []
        for start in range(0, len(tokens), step):
            chunk_tokens = tokens[start : start + self.max_tokens]
            segments.append(self.encoder.decode(chunk_tokens))
            if start + self.max_tokens >= len(tokens):
                break
        return segments

    def _build_page_map(self, doc: ParsedDocument) -> dict[str, int] | None:
        if not doc.pages:
            return None
        page_map = {}
        for p in doc.pages:
            if p.text.strip():
                page_map[p.text.strip()[:80]] = p.page_number
        return page_map
