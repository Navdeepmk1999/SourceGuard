"""Semantic boundary chunking (Module 11).

Groups `LayoutElement`s into embedding-ready chunks along real structural
boundaries, replacing fixed-width character splitting for the ingestion
path. Three rules drive it:

1. **Tables are atomic.** A table is never split and never merged with
   surrounding prose. Splitting one drops the header row, which is what
   makes the remaining cells interpretable at all.
2. **Headings bind to the content they introduce.** A heading is emitted
   with its section body, and re-prepended as context to any continuation
   chunk, so a chunk retrieved in isolation still says what section it came
   from.
3. **Size is a ceiling, not a target.** Elements are packed until the next
   one would exceed `max_chunk_size`; the split then lands on an element
   boundary rather than mid-sentence.

`RecursiveChunker` is retained and still used - as the fallback for a single
element larger than the ceiling (a genuinely huge paragraph), which is the
one case where an arbitrary split is unavoidable.
"""

import uuid

from app.schemas.document import DocumentChunk
from app.services.chunker import RecursiveChunker
from app.services.layout_parser import ElementType, LayoutElement

ELEMENT_SEPARATOR = "\n\n"


class SemanticChunker:
    def __init__(
        self,
        max_chunk_size: int = 1000,
        fallback_chunker: RecursiveChunker | None = None,
    ) -> None:
        if max_chunk_size <= 0:
            raise ValueError("max_chunk_size must be positive")
        self.max_chunk_size = max_chunk_size
        # Only reached for a single oversized element; its overlap setting is
        # irrelevant to the element-boundary path.
        self._fallback = fallback_chunker or RecursiveChunker(
            chunk_size=max_chunk_size, chunk_overlap=min(200, max_chunk_size - 1)
        )

    def chunk_elements(
        self,
        elements: list[LayoutElement],
        document_id: str,
        extra_metadata: dict | None = None,
    ) -> list[DocumentChunk]:
        if not elements:
            return []

        # Reconstruct the canonical document text and record each element's
        # exact span in it, so chunks keep the offset traceability the
        # character-splitting path provided (DESIGN.md, Module 1) even though
        # chunk content is now assembled from elements rather than sliced
        # out of one flat string.
        offsets: list[tuple[int, int]] = []
        cursor = 0
        for element in elements:
            offsets.append((cursor, cursor + len(element.text)))
            cursor += len(element.text) + len(ELEMENT_SEPARATOR)

        groups = self._group(elements)

        chunks: list[DocumentChunk] = []
        for group in groups:
            indices = group["indices"]
            heading: LayoutElement | None = group["heading"]
            body = ELEMENT_SEPARATOR.join(elements[i].text for i in indices)
            start = offsets[indices[0]][0]
            end = offsets[indices[-1]][1]

            # Continuation chunk: its section heading isn't contiguous with
            # it, so prepend the heading text for retrieval context and flag
            # it - the content is then no longer a verbatim slice of the
            # source, and the offsets deliberately cover the body only.
            heading_prefixed = heading is not None and heading not in (
                elements[i] for i in indices
            )
            content = f"{heading.text}{ELEMENT_SEPARATOR}{body}" if heading_prefixed else body

            group_elements = [elements[i] for i in indices]
            base_metadata = {
                "strategy": "semantic_layout",
                "element_types": sorted({e.element_type.value for e in group_elements}),
                "contains_table": any(
                    e.element_type is ElementType.TABLE for e in group_elements
                ),
                "heading_prefixed": heading_prefixed,
            }
            if heading is not None:
                base_metadata["section"] = heading.text
            pages = sorted({e.page_number for e in group_elements if e.page_number})
            if pages:
                base_metadata["pages"] = pages
            if extra_metadata:
                base_metadata.update(extra_metadata)

            # One element, still too large even alone -> the only case where
            # an arbitrary character split is unavoidable.
            #
            # Atomic elements are exempt: a large table split by character
            # count loses its header row, so every fragment after the first
            # becomes uninterpretable cells - the exact failure this module
            # exists to prevent. It is emitted whole and oversized instead.
            # The tradeoff is real and deliberate: a table larger than the
            # embedding model's context will be truncated at embed time,
            # which degrades that one chunk, whereas splitting corrupts the
            # column semantics of all of them.
            is_atomic_group = any(e.is_atomic for e in group_elements)
            if not is_atomic_group and len(indices) == 1 and len(content) > self.max_chunk_size:
                for part in self._fallback.chunk_text(
                    content, document_id=document_id, extra_metadata=base_metadata
                ):
                    part.metadata["oversized_element_split"] = True
                    chunks.append(part)
                continue

            chunks.append(
                DocumentChunk(
                    chunk_id=str(uuid.uuid4()),
                    document_id=document_id,
                    content=content,
                    chunk_index=0,  # renumbered below
                    start_offset=start,
                    end_offset=end,
                    metadata=base_metadata,
                )
            )

        for index, chunk in enumerate(chunks):
            chunk.chunk_index = index
        return chunks

    def _group(self, elements: list[LayoutElement]) -> list[dict]:
        """Packs element indices into groups honoring the three rules above."""
        groups: list[dict] = []
        buffer: list[int] = []
        buffer_len = 0
        current_heading: LayoutElement | None = None
        # The heading that opened the section the buffer belongs to, used to
        # re-prepend context on continuation chunks.
        buffer_heading: LayoutElement | None = None

        def flush() -> None:
            nonlocal buffer, buffer_len
            if buffer:
                groups.append({"indices": buffer, "heading": buffer_heading})
                buffer = []
                buffer_len = 0

        for index, element in enumerate(elements):
            if element.is_atomic:
                # Rule 1: a table stands alone, carrying its section heading
                # as context.
                flush()
                groups.append({"indices": [index], "heading": current_heading})
                buffer_heading = current_heading
                continue

            if element.element_type is ElementType.HEADING:
                # Rule 2: a heading opens a new section, so close the old one
                # and start the buffer with the heading itself.
                flush()
                current_heading = element
                buffer_heading = element
                buffer = [index]
                buffer_len = len(element.text)
                continue

            addition = len(element.text) + (len(ELEMENT_SEPARATOR) if buffer else 0)
            if buffer and buffer_len + addition > self.max_chunk_size:
                # Rule 3: the ceiling is reached - split here, on an element
                # boundary.
                flush()
                buffer = [index]
                buffer_len = len(element.text)
                continue

            buffer.append(index)
            buffer_len += addition

        flush()
        return self._merge_heading_only_groups(groups, elements)

    def _merge_heading_only_groups(
        self, groups: list[dict], elements: list[LayoutElement]
    ) -> list[dict]:
        """Folds a heading-with-no-body group into the group that follows it.

        A back-to-back heading pair (a document title immediately followed by
        a section heading) would otherwise emit the title as a chunk of its
        own - a few words with no content, which is near-useless to retrieve
        and just dilutes the index. Only merges when the result stays within
        the ceiling, and never merges into an atomic table group (the table
        already carries its heading as prepended context).
        """
        merged: list[dict] = []
        pending: dict | None = None

        for group in groups:
            group_elements = [elements[i] for i in group["indices"]]
            is_heading_only = all(
                e.element_type is ElementType.HEADING for e in group_elements
            )

            if pending is not None:
                combined_indices = pending["indices"] + group["indices"]
                combined_len = sum(len(elements[i].text) for i in combined_indices)
                is_table_group = any(e.is_atomic for e in group_elements)
                if not is_table_group and combined_len <= self.max_chunk_size:
                    group = {"indices": combined_indices, "heading": pending["heading"]}
                else:
                    merged.append(pending)
                pending = None

            if is_heading_only:
                pending = group
                continue
            merged.append(group)

        if pending is not None:
            merged.append(pending)
        return merged
