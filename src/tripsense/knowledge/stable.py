from __future__ import annotations

import json
import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from .contracts import StableKnowledgeDocument


def _tokens(text: str) -> list[str]:
    normalized = re.sub(r"\s+", "", text.lower())
    latin = re.findall(r"[a-z0-9]+", normalized)
    chinese = re.findall(r"[\u4e00-\u9fff]", normalized)
    bigrams = ["".join(chinese[index : index + 2]) for index in range(len(chinese) - 1)]
    return latin + chinese + bigrams


@dataclass(slots=True)
class StableHit:
    document: StableKnowledgeDocument
    score: float
    matched_terms: list[str]


class LexicalStableKnowledgeStore:
    """Deterministic retrieval baseline with a replaceable embedding boundary.

    Character unigrams/bigrams work as a dependable Chinese baseline. The public
    ``search`` contract can later be backed by an embedding index without changing
    the planner or API layers.
    """

    def __init__(self, documents: list[StableKnowledgeDocument]):
        self.documents = documents
        self._document_tokens = {
            document.doc_id: Counter(_tokens(document.searchable_text()))
            for document in documents
        }
        document_frequency: Counter[str] = Counter()
        for tokens in self._document_tokens.values():
            document_frequency.update(tokens.keys())
        total = max(len(documents), 1)
        self._idf = {
            token: math.log((total + 1) / (frequency + 1)) + 1
            for token, frequency in document_frequency.items()
        }

    @classmethod
    def from_jsonl(cls, path: str | Path) -> "LexicalStableKnowledgeStore":
        documents = []
        with Path(path).open(encoding="utf-8") as stream:
            for line in stream:
                if line.strip():
                    documents.append(StableKnowledgeDocument(**json.loads(line)))
        return cls(documents)

    def search(self, query: str, *, city: str, limit: int = 20) -> list[StableHit]:
        query_tokens = Counter(_tokens(query))
        hits: list[StableHit] = []
        normalized_query = query.lower().replace(" ", "")
        for document in self.documents:
            if document.city != city:
                continue
            doc_tokens = self._document_tokens[document.doc_id]
            overlap = query_tokens.keys() & doc_tokens.keys()
            score = sum(
                min(query_tokens[token], doc_tokens[token]) * self._idf.get(token, 1.0)
                for token in overlap
            )
            if document.name.lower().replace(" ", "") in normalized_query:
                score += 8.0
            for alias in document.aliases:
                if alias.lower().replace(" ", "") in normalized_query:
                    score += 5.0
            for tag in document.experience_tags + document.suitable_for:
                if tag and tag.lower().replace(" ", "") in normalized_query:
                    score += 2.5
            if score > 0:
                hits.append(
                    StableHit(
                        document=document,
                        score=round(score, 4),
                        matched_terms=sorted(overlap, key=len, reverse=True)[:8],
                    )
                )
        hits.sort(key=lambda item: item.score, reverse=True)
        if not hits:
            return []
        maximum = hits[0].score
        for hit in hits:
            hit.score = round(hit.score / maximum, 4)
        return hits[:limit]

