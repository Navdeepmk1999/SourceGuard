import re
from dataclasses import dataclass
from enum import Enum

# Sentence-boundary split: keep the delimiter with the sentence that precedes it.
_CLAIM_SPLIT_PATTERN = re.compile(r"(?<=[.!?])\s+")

# Whole-word tokens only. Combined with the word-boundary regex in
# `_chunk_contains_keyword`, this guarantees claim keywords are matched as
# complete words in the source text, never as a substring of a longer word.
_WORD_PATTERN = re.compile(r"\b[a-zA-Z0-9]+\b")

_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "in",
    "is", "it", "its", "of", "on", "or", "that", "the", "this", "to", "was",
    "were", "with",
}


class EntailmentLabel(str, Enum):
    ENTAILED = "entailed"
    NOT_ENTAILED = "not_entailed"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


@dataclass
class ClaimVerification:
    claim: str
    label: EntailmentLabel
    score: float
    supporting_chunk_index: int | None


@dataclass
class VerificationResult:
    claims: list[ClaimVerification]
    overall_score: float
    is_fully_supported: bool


def _chunk_contains_keyword(chunk_text: str, keyword: str) -> bool:
    """True if `keyword` appears in `chunk_text` as a whole word.

    CRITICAL: word boundaries (\\b) are mandatory here to prevent partial
    substring matches (e.g. claim keyword "cat" must not match inside
    "category" or "concatenate").
    """
    pattern = rf"\b{re.escape(keyword)}\b"
    return re.search(pattern, chunk_text, re.IGNORECASE) is not None


class NLIVerifierService:
    """Decomposes a generated answer into claims and scores each claim's
    entailment against a set of source chunks.

    This is a lightweight, dependency-free heuristic (keyword coverage) so
    verification works without downloading a DeBERTa/NLI model. It is designed
    to be swapped for a real cross-encoder/NLI model later by replacing
    `_score_claim_against_chunk` without touching the decomposition or
    aggregation logic.
    """

    def __init__(self, entailment_threshold: float = 0.6, insufficient_threshold: float = 0.25) -> None:
        if not 0.0 <= insufficient_threshold <= entailment_threshold <= 1.0:
            raise ValueError(
                "Thresholds must satisfy 0 <= insufficient_threshold <= entailment_threshold <= 1"
            )
        self.entailment_threshold = entailment_threshold
        self.insufficient_threshold = insufficient_threshold

    def decompose_claims(self, answer: str) -> list[str]:
        """Splits an answer into individual sentence-level claims."""
        if not answer or not answer.strip():
            return []
        raw_claims = _CLAIM_SPLIT_PATTERN.split(answer.strip())
        return [claim.strip() for claim in raw_claims if claim.strip()]

    def _extract_keywords(self, text: str) -> set[str]:
        words = _WORD_PATTERN.findall(text.lower())
        return {word for word in words if word not in _STOPWORDS}

    def _score_claim_against_chunk(self, claim_keywords: set[str], chunk_text: str) -> float:
        """Fraction of claim keywords found as whole words in `chunk_text`."""
        if not claim_keywords:
            return 0.0
        matched = sum(1 for keyword in claim_keywords if _chunk_contains_keyword(chunk_text, keyword))
        return matched / len(claim_keywords)

    def verify_claim(self, claim: str, source_chunks: list[str]) -> ClaimVerification:
        claim_keywords = self._extract_keywords(claim)

        best_score = 0.0
        best_index: int | None = None
        for index, chunk_text in enumerate(source_chunks):
            score = self._score_claim_against_chunk(claim_keywords, chunk_text)
            if score > best_score:
                best_score = score
                best_index = index

        if best_score >= self.entailment_threshold:
            label = EntailmentLabel.ENTAILED
        elif best_score <= self.insufficient_threshold:
            label = EntailmentLabel.INSUFFICIENT_EVIDENCE
        else:
            label = EntailmentLabel.NOT_ENTAILED

        return ClaimVerification(
            claim=claim,
            label=label,
            score=round(best_score, 4),
            supporting_chunk_index=best_index,
        )

    def verify_answer(self, answer: str, source_chunks: list[str]) -> VerificationResult:
        claims = self.decompose_claims(answer)
        claim_verifications = [self.verify_claim(claim, source_chunks) for claim in claims]

        if not claim_verifications:
            return VerificationResult(claims=[], overall_score=0.0, is_fully_supported=False)

        overall_score = sum(cv.score for cv in claim_verifications) / len(claim_verifications)
        is_fully_supported = all(cv.label == EntailmentLabel.ENTAILED for cv in claim_verifications)

        return VerificationResult(
            claims=claim_verifications,
            overall_score=round(overall_score, 4),
            is_fully_supported=is_fully_supported,
        )
