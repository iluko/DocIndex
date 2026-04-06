"""Chunked baseline RAG artifact builder."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path

from ingestion.docx_converter import docx_to_markdown
from experiments.builds.base import ArtifactBuildAdapter, BuildAdapterResult, BuildExecutionContext
from experiments.models import ArtifactOutput, BuiltDocumentArtifact, RAGBuildConfig
from utils import atomic_write_text, estimate_tokens

try:
    import fitz
except ImportError:  # pragma: no cover - optional dependency in some environments
    fitz = None


@dataclass(frozen=True)
class _SourceBlock:
    """One text block extracted from a document before chunk assembly."""

    text: str
    page_start: int | None = None
    page_end: int | None = None


class RAGBuildAdapter(ArtifactBuildAdapter):
    """Build flat text chunks for standard-RAG-style baselines."""

    kind = "rag"
    artifact_family = "rag_chunks"

    async def build(self, context: BuildExecutionContext) -> BuildAdapterResult:
        config = context.config
        if not isinstance(config, RAGBuildConfig):
            raise TypeError("RAGBuildAdapter requires RAGBuildConfig.")

        chunks_path = context.build_dir / "rag_chunks.json"
        built_docs: list[BuiltDocumentArtifact] = []
        all_chunks: list[dict] = []

        for document in context.corpus.documents:
            source_blocks = self._extract_source_blocks(Path(document.normalized_path))
            doc_chunks = self._chunk_blocks(
                doc_id=document.doc_id,
                doc_title=document.doc_title,
                blocks=source_blocks,
                chunk_size_tokens=config.chunk_size_tokens,
                overlap_tokens=config.chunk_overlap_tokens,
                min_chunk_tokens=config.min_chunk_tokens,
            )
            all_chunks.extend(doc_chunks)
            built_docs.append(
                BuiltDocumentArtifact(
                    doc_id=document.doc_id,
                    artifact_path=str(chunks_path),
                    metadata={"chunk_count": len(doc_chunks)},
                )
            )

        atomic_write_text(chunks_path, json.dumps(all_chunks, indent=2, ensure_ascii=False))

        return BuildAdapterResult(
            outputs=[
                ArtifactOutput(
                    label="rag_chunks",
                    path=str(chunks_path),
                    description="Flat chunk artifact set for baseline RAG retrieval.",
                    metadata={"chunk_count": len(all_chunks)},
                )
            ],
            documents=built_docs,
            metrics={
                "docs_processed": len(context.corpus.documents),
                "total_chunks": len(all_chunks),
            },
        )

    def _extract_source_blocks(self, path: Path) -> list[_SourceBlock]:
        """Extract text blocks from a source document in a retrieval-friendly shape."""
        suffix = path.suffix.lower()
        if suffix == ".pdf":
            return self._extract_pdf_blocks(path)
        if suffix in {".md", ".markdown"}:
            return self._extract_plain_blocks(path.read_text(encoding="utf-8"))
        if suffix == ".docx":
            return self._extract_plain_blocks(docx_to_markdown(str(path)))
        raise ValueError(f"Unsupported source type for RAG build: {suffix}")

    def _extract_pdf_blocks(self, path: Path) -> list[_SourceBlock]:
        """Extract one block per PDF page to preserve page metadata."""
        if fitz is None:
            raise ImportError("PyMuPDF (fitz) is required for PDF RAG builds.")

        blocks: list[_SourceBlock] = []
        with fitz.open(str(path)) as document:
            for page_number in range(document.page_count):
                text = document.load_page(page_number).get_text().strip()
                if not text:
                    continue
                blocks.append(
                    _SourceBlock(
                        text=text,
                        page_start=page_number + 1,
                        page_end=page_number + 1,
                    )
                )
        return blocks

    def _extract_plain_blocks(self, text: str) -> list[_SourceBlock]:
        """Split markdown/plain text into paragraph-like blocks."""
        blocks: list[_SourceBlock] = []
        for raw in text.split("\n\n"):
            cleaned = raw.strip()
            if cleaned:
                blocks.append(_SourceBlock(text=cleaned))
        return blocks

    def _chunk_blocks(
        self,
        *,
        doc_id: str,
        doc_title: str,
        blocks: list[_SourceBlock],
        chunk_size_tokens: int,
        overlap_tokens: int,
        min_chunk_tokens: int,
    ) -> list[dict]:
        """Merge source blocks into bounded flat chunks with soft overlap."""
        chunks: list[dict] = []
        current: list[_SourceBlock] = []
        current_tokens = 0
        chunk_index = 0

        def _flush() -> None:
            nonlocal current, current_tokens, chunk_index
            if not current:
                return

            text = "\n\n".join(block.text for block in current).strip()
            tokens = estimate_tokens(text)
            if tokens < min_chunk_tokens and chunks:
                # Keep tiny trailing fragments attached to the previous chunk
                # instead of emitting noisy under-sized chunks.
                previous = chunks[-1]
                merged_text = f"{previous['text']}\n\n{text}".strip()
                previous["text"] = merged_text
                previous["estimated_tokens"] = estimate_tokens(merged_text)
                page_numbers = [b.page_start for b in current if b.page_start is not None]
                if page_numbers:
                    existing_start = previous.get("page_start")
                    existing_end = previous.get("page_end")
                    previous["page_start"] = (
                        min(existing_start, min(page_numbers))
                        if existing_start is not None
                        else min(page_numbers)
                    )
                    previous["page_end"] = (
                        max(existing_end, max(page_numbers))
                        if existing_end is not None
                        else max(page_numbers)
                    )
                current = []
                current_tokens = 0
                return

            page_numbers = [b.page_start for b in current if b.page_start is not None]
            chunk_payload = {
                "chunk_id": f"{doc_id}::chunk_{chunk_index:04d}",
                "doc_id": doc_id,
                "doc_title": doc_title,
                "text": text,
                "estimated_tokens": tokens,
                "page_start": min(page_numbers) if page_numbers else None,
                "page_end": max(page_numbers) if page_numbers else None,
            }
            chunks.append(chunk_payload)
            chunk_index += 1

            if overlap_tokens <= 0:
                current = []
                current_tokens = 0
                return

            overlap_blocks: list[_SourceBlock] = []
            overlap_total = 0
            for block in reversed(current):
                block_tokens = estimate_tokens(block.text)
                if overlap_total + block_tokens > overlap_tokens and overlap_blocks:
                    break
                overlap_blocks.insert(0, block)
                overlap_total += block_tokens
                if overlap_total >= overlap_tokens:
                    break

            current = overlap_blocks
            current_tokens = overlap_total

        for block in blocks:
            block_tokens = estimate_tokens(block.text)
            if current and current_tokens + block_tokens > chunk_size_tokens:
                _flush()
            current.append(block)
            current_tokens += block_tokens

        _flush()
        return chunks
