"""Fetch raw content for routed node references and enforce a token budget.

Each retrieved chunk is optionally enriched with a parent-section context
header when the parent node carries a ``prefix_summary`` field. This prevents
answers from being contextually orphaned — a section like "3.2 Retry Logic"
makes more sense when the reader knows it lives inside "3. Error Handling" and
what that parent section establishes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import logging
import re
from pathlib import Path

from ingestion.docx_converter import docx_to_markdown
from storage.store import DocumentStore
from utils import estimate_tokens, find_parent_node, find_tree_node, iter_tree_nodes

try:
    import fitz
except ImportError:  # pragma: no cover - depends on local installation
    fitz = None


logger = logging.getLogger(__name__)


@dataclass
class RetrievedChunk:
    """One fetched chunk of raw document content, ready for answer synthesis."""

    node_ref: str
    doc_id: str
    node_id: str
    title: str
    start_index: int
    end_index: int
    text: str
    estimated_tokens: int
    truncated: bool = False
    is_expansion: bool = False
    """True when this chunk was added via node-neighborhood expansion, not primary navigation."""


@dataclass
class FetchResult:
    """The combined retrieval payload plus bookkeeping about truncation."""

    combined_text: str
    chunks: list[RetrievedChunk]
    truncated: bool
    token_budget: int


def _split_node_ref(node_ref: str) -> tuple[str, str]:
    """Split a compound `doc_id::node_id` reference into its two parts."""
    if "::" not in node_ref:
        raise ValueError(f"Invalid node_ref '{node_ref}'. Expected '<doc_id>::<node_id>'.")
    return tuple(node_ref.split("::", 1))  # type: ignore[return-value]


def _extract_pdf_text(file_path: str, start_index: int, end_index: int) -> str:
    """Extract page text from a PDF using the 1-based span stored in the tree."""
    if fitz is None:
        raise ImportError("PyMuPDF (fitz) is required for PDF node extraction.")

    page_text: list[str] = []
    with fitz.open(file_path) as document:
        start_page = max(0, int(start_index) - 1)
        end_page = min(document.page_count - 1, int(end_index) - 1)
        for page_number in range(start_page, end_page + 1):
            page_text.append(document.load_page(page_number).get_text())
    return "\n".join(page_text).strip()


def _extract_markdown_by_heading(markdown_text: str, title: str) -> str:
    """Fallback markdown extraction that slices from one heading to the next."""
    lines = markdown_text.splitlines()
    title = title.strip()
    heading_index = None
    heading_level = None

    for index, line in enumerate(lines):
        match = re.match(r"^(#+)\s+(.*)$", line.strip())
        if match and match.group(2).strip() == title:
            heading_index = index
            heading_level = len(match.group(1))
            break

    if heading_index is None or heading_level is None:
        return markdown_text

    end_index = len(lines)
    for index in range(heading_index + 1, len(lines)):
        match = re.match(r"^(#+)\s+(.*)$", lines[index].strip())
        if match and len(match.group(1)) <= heading_level:
            end_index = index
            break

    return "\n".join(lines[heading_index:end_index]).strip()


def _extract_markdown_text_from_content(markdown_text: str, node: dict) -> str:
    """Extract markdown text using stored offsets when available, else headings."""
    start_index = node.get("start_index")
    end_index = node.get("end_index")

    if (
        isinstance(start_index, int)
        and isinstance(end_index, int)
        and start_index >= 1
        and end_index >= start_index
    ):
        bounded_end = min(end_index, len(markdown_text))
        if start_index <= bounded_end:
            return markdown_text[start_index - 1 : bounded_end].strip()

    return _extract_markdown_by_heading(markdown_text, node.get("title", ""))


def _extract_markdown_text(file_path: str, node: dict) -> str:
    """Read a markdown file and extract the content for one tree node."""
    markdown_text = Path(file_path).read_text(encoding="utf-8")
    return _extract_markdown_text_from_content(markdown_text, node)


def _build_parent_context_header(
    node_id: str,
    per_doc_tree: dict,
) -> str:
    """Return a context header string from the parent node's prefix_summary.

    When a node lives inside a larger section, the parent's ``prefix_summary``
    (the introductory prose before the first child section) anchors what the
    child section means. Including it prevents the answer LLM from treating
    a fetched chunk as if it stands alone.

    Returns an empty string when:
    - The node has no parent (it is a root-level node).
    - The parent exists but carries no ``prefix_summary``.
    - The ``prefix_summary`` is blank after stripping.
    """
    parent = find_parent_node(per_doc_tree, node_id)
    if parent is None:
        return ""

    prefix = (parent.get("prefix_summary") or "").strip()
    if not prefix:
        return ""

    parent_title = (parent.get("title") or "").strip()
    label = f'"{parent_title}"' if parent_title else "parent section"
    # Cap at 400 chars — enough to convey framing, not so much it crowds the chunk.
    if len(prefix) > 400:
        prefix = prefix[:400].rstrip() + "…"

    return f"[Context from parent section {label}]\n{prefix}\n"


def _build_retrieved_chunk(
    node_ref: str,
    per_doc_tree: dict,
    file_path: str,
    is_expansion: bool = False,
) -> RetrievedChunk:
    """Resolve one node reference into a chunk object with estimated tokens.

    The chunk text is structured as:
      [optional parent context header]
      [doc_id :: title :: pages start-end]

      <raw section body>
    """
    doc_id, node_id = _split_node_ref(node_ref)
    node = find_tree_node(per_doc_tree, node_id)
    if node is None:
        raise KeyError(f"Node '{node_id}' not found in tree for '{doc_id}'.")

    start_index = int(node.get("start_index", 1))
    end_index = int(node.get("end_index", start_index))
    title = node.get("title", f"Node {node_id}")
    suffix = Path(file_path).suffix.lower()

    if suffix == ".pdf":
        body = _extract_pdf_text(file_path, start_index, end_index)
    elif suffix in {".md", ".markdown"}:
        body = _extract_markdown_text(file_path, node)
    elif suffix == ".docx":
        body = _extract_markdown_text_from_content(docx_to_markdown(file_path), node)
    else:
        raise ValueError(f"Unsupported document type for node fetch: {suffix}")

    parent_header = _build_parent_context_header(node_id, per_doc_tree)
    expansion_marker = "[expanded context] " if is_expansion else ""
    section_header = f"[{expansion_marker}{doc_id} :: {title} :: pages {start_index}-{end_index}]"
    text = f"{parent_header}{section_header}\n\n{body}"

    return RetrievedChunk(
        node_ref=node_ref,
        doc_id=doc_id,
        node_id=node_id,
        title=title,
        start_index=start_index,
        end_index=end_index,
        text=text,
        estimated_tokens=estimate_tokens(text),
        is_expansion=is_expansion,
    )


def _truncate_chunk(chunk: RetrievedChunk, remaining_tokens: int) -> RetrievedChunk:
    """Approximate a token-safe truncation by converting tokens into characters.

    This is intentionally approximate. Exact token-aware truncation would require
    re-encoding repeatedly, which is more expensive than we need here.
    """
    approx_chars = max(0, remaining_tokens * 4)
    truncated_text = chunk.text[:approx_chars].rstrip()
    return RetrievedChunk(
        node_ref=chunk.node_ref,
        doc_id=chunk.doc_id,
        node_id=chunk.node_id,
        title=chunk.title,
        start_index=chunk.start_index,
        end_index=chunk.end_index,
        text=truncated_text,
        estimated_tokens=estimate_tokens(truncated_text),
        truncated=True,
        is_expansion=chunk.is_expansion,
    )


def _find_sibling_node_ids(per_doc_tree: dict, node_id: str) -> list[str]:
    """Return node_ids of siblings (same parent, excluding self).

    Siblings are nodes that share the same parent in the tree.  For root-level
    nodes, siblings are other root-level nodes.
    """
    target = str(node_id)

    def _children_of_parent(nodes: list) -> list[str] | None:
        """DFS: find the sibling list containing target, return their ids."""
        for node in nodes:
            children: list = node.get("nodes") or []
            for child in children:
                if str(child.get("node_id")) == target:
                    # Found the parent — return all sibling ids except self.
                    return [
                        str(c.get("node_id"))
                        for c in children
                        if c.get("node_id") and str(c.get("node_id")) != target
                    ]
            result = _children_of_parent(children)
            if result is not None:
                return result
        return None

    root_nodes: list = []
    if isinstance(per_doc_tree, dict):
        root_nodes = per_doc_tree.get("structure") or per_doc_tree.get("nodes") or []

    # Check if target is a root-level node first.
    root_ids = [str(n.get("node_id")) for n in root_nodes if n.get("node_id")]
    if target in root_ids:
        return [nid for nid in root_ids if nid != target]

    result = _children_of_parent(root_nodes)
    return result or []


def _find_first_child_node_id(per_doc_tree: dict, node_id: str) -> str | None:
    """Return the first child node_id of ``node_id``, or None if it has no children."""
    node = find_tree_node(per_doc_tree, node_id)
    if node is None:
        return None
    children: list = node.get("nodes") or []
    if not children:
        return None
    first_child_id = children[0].get("node_id")
    return str(first_child_id) if first_child_id else None


def _find_parent_node_id(per_doc_tree: dict, node_id: str) -> str | None:
    """Return the parent's node_id, or None when ``node_id`` is a root node."""
    parent = find_parent_node(per_doc_tree, node_id)
    if parent is None:
        return None
    pid = parent.get("node_id")
    return str(pid) if pid else None


def collect_expansion_node_refs(
    primary_node_refs: list[str],
    per_doc_trees: dict[str, dict],
    max_siblings: int = 1,
) -> list[str]:
    """Build a bounded list of expansion node refs around the primary selection.

    Strategy (in priority order, per primary node):
    1. One adjacent sibling (first sibling not already selected).
    2. First child node of the primary (if the node is broad/top-level).
    3. Parent node (if the primary is a leaf and the parent adds context).

    Expansion candidates are de-duplicated against each other and against
    primary_node_refs.  The caller is responsible for enforcing the token budget
    — this function only selects candidates, not their content.

    Parameters
    ----------
    primary_node_refs:
        Already-selected ``doc_id::node_id`` refs (not mutated).
    per_doc_trees:
        Dict mapping doc_id → per-document tree dict.
    max_siblings:
        Maximum number of sibling candidates to emit per primary node.
        Keep this small (1) to avoid flooding the context.
    """
    primary_set: set[str] = set(primary_node_refs)
    expansion_refs: list[str] = []
    seen: set[str] = set(primary_node_refs)

    for node_ref in primary_node_refs:
        doc_id, node_id = _split_node_ref(node_ref)
        tree = per_doc_trees.get(doc_id)
        if tree is None:
            continue

        # 1. One adjacent sibling.
        sibling_ids = _find_sibling_node_ids(tree, node_id)
        siblings_added = 0
        for sib_id in sibling_ids:
            if siblings_added >= max_siblings:
                break
            sib_ref = f"{doc_id}::{sib_id}"
            if sib_ref not in seen:
                expansion_refs.append(sib_ref)
                seen.add(sib_ref)
                siblings_added += 1

        # 2. First child (useful when primary is a broad section).
        child_id = _find_first_child_node_id(tree, node_id)
        if child_id:
            child_ref = f"{doc_id}::{child_id}"
            if child_ref not in seen:
                expansion_refs.append(child_ref)
                seen.add(child_ref)

        # 3. Parent (useful when primary is a very narrow leaf node).
        parent_id = _find_parent_node_id(tree, node_id)
        if parent_id:
            parent_ref = f"{doc_id}::{parent_id}"
            if parent_ref not in seen:
                expansion_refs.append(parent_ref)
                seen.add(parent_ref)

    return expansion_refs


def fetch_node_content(
    node_ref: str,
    per_doc_tree: dict,
    file_path: str,
) -> str:
    """Convenience wrapper that returns raw text instead of a full chunk object."""
    return _build_retrieved_chunk(node_ref, per_doc_tree, file_path).text


async def fetch_multiple_nodes_detailed(
    node_refs: list[str],
    storage: DocumentStore,
    model_max_tokens: int = 100000,
    expansion_refs: list[str] | None = None,
) -> FetchResult:
    """Fetch multiple node refs in order while staying under the model token budget.

    Primary ``node_refs`` are fetched first and take priority over the budget.
    Optional ``expansion_refs`` are fetched afterwards if budget allows; they are
    marked with ``is_expansion=True`` on the resulting chunks.
    """
    token_budget = int(model_max_tokens * 0.7)
    total_tokens = 0
    retrieved_chunks: list[RetrievedChunk] = []
    loaded_trees: dict[str, dict] = {}
    truncated = False

    all_refs = list(node_refs) + list(expansion_refs or [])
    primary_set: set[str] = set(node_refs)

    for node_ref in all_refs:
        is_expansion = node_ref not in primary_set
        doc_id, _ = _split_node_ref(node_ref)

        try:
            if doc_id not in loaded_trees:
                loaded_trees[doc_id] = storage.load_doc_tree(doc_id)

            file_path = storage.load_doc_source_path(doc_id)
            chunk = _build_retrieved_chunk(
                node_ref, loaded_trees[doc_id], file_path, is_expansion=is_expansion
            )
        except (FileNotFoundError, ImportError, KeyError, ValueError) as exc:
            logger.warning("Skipping node '%s': %s", node_ref, exc)
            continue

        if total_tokens + chunk.estimated_tokens <= token_budget:
            retrieved_chunks.append(chunk)
            total_tokens += chunk.estimated_tokens
            continue

        remaining_tokens = token_budget - total_tokens
        truncated = True
        if remaining_tokens > 0 and not is_expansion:
            # Only truncate primary nodes; skip expansion chunks when budget is tight.
            retrieved_chunks.append(_truncate_chunk(chunk, remaining_tokens))
        break

    return FetchResult(
        combined_text="\n\n".join(chunk.text for chunk in retrieved_chunks),
        chunks=retrieved_chunks,
        truncated=truncated,
        token_budget=token_budget,
    )


async def fetch_multiple_nodes(
    node_refs: list[str],
    storage: DocumentStore,
    model_max_tokens: int = 100000,
) -> str:
    """Return only the concatenated retrieval text when metadata is not needed."""
    result = await fetch_multiple_nodes_detailed(
        node_refs=node_refs,
        storage=storage,
        model_max_tokens=model_max_tokens,
    )
    return result.combined_text
