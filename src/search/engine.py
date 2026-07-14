"""Smart search engine with TF-IDF scoring and keyword embeddings.

Provides fast, ranked search across all notes without external dependencies.
Builds an inverted index on startup, supports fuzzy matching and semantic-ish
search by expanding queries with related terms from the domain taxonomy.

Usage:
    from src.search.engine import SearchEngine
    engine = SearchEngine()
    await engine.build_index()  # loads from DB
    results = engine.search("RAG vector database", limit=10)
"""

from __future__ import annotations

import math
import re
import json
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class SearchDocument:
    """A searchable document in the index."""
    id: str
    title: str
    summary: str = ""
    content: str = ""
    domains: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    note_type: str = "literature"
    source_url: str = ""
    quality_score: int = 3
    created_at: str = ""
    key_takeaways: list[str] = field(default_factory=list)


@dataclass
class SearchResult:
    """A ranked search result."""
    doc: SearchDocument
    score: float
    highlights: list[str] = field(default_factory=list)  # matching snippets


# ---------------------------------------------------------------------------
# Text processing
# ---------------------------------------------------------------------------

_STOP_WORDS = frozenset([
    "the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "by", "from", "is", "are", "was", "were", "be", "been",
    "being", "have", "has", "had", "do", "does", "did", "will", "would",
    "could", "should", "may", "might", "shall", "can", "this", "that",
    "these", "those", "it", "its", "not", "no", "so", "if", "then",
    "than", "too", "very", "just", "about", "up", "out", "how", "what",
    "when", "where", "who", "which", "why", "all", "each", "every",
    "both", "few", "more", "most", "other", "some", "such", "only",
    "own", "same", "into", "over", "after", "before", "between",
    "through", "during", "above", "below", "under",
])

_WORD_RE = re.compile(r"[a-z0-9]+(?:[-'][a-z0-9]+)*")


def _tokenize(text: str) -> list[str]:
    """Lowercase, split into words, remove stop words."""
    return [w for w in _WORD_RE.findall(text.lower()) if w not in _STOP_WORDS and len(w) > 1]


def _ngrams(tokens: list[str], n: int = 2) -> list[str]:
    """Generate bigrams for better phrase matching."""
    return [" ".join(tokens[i:i + n]) for i in range(len(tokens) - n + 1)]


# ---------------------------------------------------------------------------
# Search Engine
# ---------------------------------------------------------------------------

class SearchEngine:
    """TF-IDF based search engine with keyword expansion and fuzzy matching."""

    def __init__(self):
        self._docs: dict[str, SearchDocument] = {}
        self._index: dict[str, set[str]] = defaultdict(set)  # term → doc IDs
        self._tf: dict[str, dict[str, float]] = {}  # doc_id → {term: freq}
        self._idf: dict[str, float] = {}  # term → inverse doc freq
        self._doc_lengths: dict[str, float] = {}  # doc_id → norm
        self._domain_synonyms: dict[str, list[str]] = {}
        self._built = False

    @property
    def doc_count(self) -> int:
        return len(self._docs)

    def add_document(self, doc: SearchDocument) -> None:
        """Add a document to the search corpus."""
        self._docs[doc.id] = doc
        self._built = False  # invalidate index

    async def build_index(self, from_db: bool = True) -> None:
        """Build the inverted index from all documents.

        If from_db is True, loads documents from the SQLite database.
        """
        if from_db:
            await self._load_from_db()

        self._index.clear()
        self._tf.clear()
        self._idf.clear()
        self._doc_lengths.clear()

        # Build term frequencies per document
        for doc_id, doc in self._docs.items():
            text = self._doc_to_text(doc)
            tokens = _tokenize(text)
            bigrams = _ngrams(tokens)
            all_terms = tokens + bigrams

            # Term frequency (normalized)
            term_counts: dict[str, int] = defaultdict(int)
            for term in all_terms:
                term_counts[term] += 1

            max_count = max(term_counts.values()) if term_counts else 1
            self._tf[doc_id] = {
                term: 0.5 + 0.5 * (count / max_count)
                for term, count in term_counts.items()
            }

            # Inverted index
            for term in term_counts:
                self._index[term].add(doc_id)

        # Inverse document frequency
        n = len(self._docs)
        for term, doc_ids in self._index.items():
            self._idf[term] = math.log((n + 1) / (len(doc_ids) + 1)) + 1

        # Document length norms (for cosine similarity)
        for doc_id, tf in self._tf.items():
            self._doc_lengths[doc_id] = math.sqrt(
                sum((tf_val * self._idf.get(term, 1)) ** 2 for term, tf_val in tf.items())
            )

        # Build domain synonyms from config
        try:
            from src.config.domains import DOMAINS
            for key, info in DOMAINS.items():
                self._domain_synonyms[key] = info.get("keywords", [])[:10]
        except Exception:
            pass

        self._built = True
        logger.info("Search index built: %d docs, %d terms", len(self._docs), len(self._index))

    def search(
        self,
        query: str,
        limit: int = 20,
        domain_filter: Optional[list[str]] = None,
        type_filter: Optional[str] = None,
        min_quality: int = 0,
    ) -> list[SearchResult]:
        """Search the index and return ranked results.

        Supports:
        - Full-text TF-IDF ranking
        - Query expansion via domain synonyms
        - Fuzzy prefix matching
        - Domain and type filtering
        - Quality score boosting
        """
        if not self._built:
            logger.warning("Search index not built — returning empty results")
            return []

        query_tokens = _tokenize(query)
        if not query_tokens:
            # No query — return all docs sorted by date
            results = list(self._docs.values())
            if domain_filter:
                results = [d for d in results if any(dm in d.domains for dm in domain_filter)]
            if type_filter:
                results = [d for d in results if d.note_type == type_filter]
            results.sort(key=lambda d: d.created_at, reverse=True)
            return [SearchResult(doc=d, score=1.0) for d in results[:limit]]

        # Expand query with domain synonyms
        expanded_tokens = list(query_tokens)
        for token in query_tokens:
            for domain_key, synonyms in self._domain_synonyms.items():
                if token in domain_key.replace("-", " ").split():
                    expanded_tokens.extend(_tokenize(" ".join(synonyms[:5])))
                    break

        query_bigrams = _ngrams(query_tokens)
        all_query_terms = expanded_tokens + query_bigrams

        # Find candidate documents
        candidates: set[str] = set()
        for term in all_query_terms:
            if term in self._index:
                candidates.update(self._index[term])
            else:
                # Fuzzy prefix matching — find terms starting with query term
                for idx_term in self._index:
                    if idx_term.startswith(term[:3]) and term[:3] == idx_term[:3]:
                        candidates.update(self._index[idx_term])

        # Score candidates using TF-IDF cosine similarity
        scored: list[tuple[str, float]] = []
        for doc_id in candidates:
            score = 0.0
            doc_tf = self._tf.get(doc_id, {})
            doc_len = self._doc_lengths.get(doc_id, 1.0)

            for term in all_query_terms:
                if term in doc_tf:
                    tf = doc_tf[term]
                    idf = self._idf.get(term, 1.0)
                    score += tf * idf

            if doc_len > 0:
                score /= doc_len

            # Boost by quality score
            doc = self._docs[doc_id]
            score *= (1 + doc.quality_score * 0.1)

            # Title match bonus (2x if query appears in title)
            title_lower = doc.title.lower()
            if any(t in title_lower for t in query_tokens):
                score *= 2.0

            # Domain match bonus
            if any(t in " ".join(doc.domains) for t in query_tokens):
                score *= 1.5

            scored.append((doc_id, score))

        # Sort by score
        scored.sort(key=lambda x: x[1], reverse=True)

        # Apply filters
        results: list[SearchResult] = []
        for doc_id, score in scored:
            doc = self._docs[doc_id]

            if domain_filter and not any(d in doc.domains for d in domain_filter):
                continue
            if type_filter and doc.note_type != type_filter:
                continue
            if doc.quality_score < min_quality:
                continue

            # Generate highlights (snippets containing query terms)
            highlights = self._generate_highlights(doc, query_tokens)

            results.append(SearchResult(doc=doc, score=score, highlights=highlights))

            if len(results) >= limit:
                break

        return results

    def _generate_highlights(self, doc: SearchDocument, query_tokens: list[str]) -> list[str]:
        """Extract text snippets containing query terms."""
        text = doc.summary or doc.content[:500]
        sentences = re.split(r'[.!?\n]+', text)
        highlights = []

        for sent in sentences:
            sent = sent.strip()
            if not sent:
                continue
            sent_lower = sent.lower()
            if any(token in sent_lower for token in query_tokens):
                highlights.append(sent[:150])
                if len(highlights) >= 3:
                    break

        return highlights

    def _doc_to_text(self, doc: SearchDocument) -> str:
        """Combine all searchable fields into one text blob."""
        parts = [
            doc.title,
            doc.title,  # double-weight title
            doc.summary,
            " ".join(doc.domains),
            " ".join(doc.tags),
            doc.note_type,
            " ".join(doc.key_takeaways),
            doc.content[:2000],
        ]
        return " ".join(p for p in parts if p)

    async def _load_from_db(self) -> None:
        """Load all notes from the SQLite database."""
        try:
            from src.pipeline.database import init_db, _get_db
            import json as _json

            db = _get_db()
            cursor = await db.execute(
                "SELECT id, title, note_type, domains, tags, source_url, "
                "quality_score, created_at, summary, key_takeaways "
                "FROM notes ORDER BY created_at DESC"
            )
            rows = await cursor.fetchall()

            for row in rows:
                kt = []
                if row["key_takeaways"]:
                    try:
                        kt = _json.loads(row["key_takeaways"])
                    except (ValueError, TypeError):
                        pass
                doc = SearchDocument(
                    id=row["id"],
                    title=row["title"],
                    note_type=row["note_type"],
                    domains=_json.loads(row["domains"]) if row["domains"] else [],
                    tags=_json.loads(row["tags"]) if row["tags"] else [],
                    source_url=row["source_url"] or "",
                    quality_score=row["quality_score"] or 3,
                    created_at=row["created_at"] or "",
                    summary=row["summary"] or "",
                    key_takeaways=kt,
                )
                self._docs[doc.id] = doc

            logger.info("Loaded %d documents from database", len(self._docs))

        except Exception as exc:
            logger.warning("Could not load from database: %s", exc)


# Singleton instance
_engine: Optional[SearchEngine] = None


async def get_search_engine() -> SearchEngine:
    """Get or create the search engine singleton."""
    global _engine
    if _engine is None:
        _engine = SearchEngine()
        await _engine.build_index()
    return _engine


async def smart_search(
    query: str,
    limit: int = 20,
    domain_filter: Optional[list[str]] = None,
    type_filter: Optional[str] = None,
) -> list[dict]:
    """High-level search function for the bot and API."""
    engine = await get_search_engine()
    results = engine.search(query, limit=limit, domain_filter=domain_filter, type_filter=type_filter)
    return [
        {
            "id": r.doc.id,
            "title": r.doc.title,
            "summary": r.doc.summary,
            "domains": r.doc.domains,
            "tags": r.doc.tags,
            "note_type": r.doc.note_type,
            "source_url": r.doc.source_url,
            "quality_score": r.doc.quality_score,
            "score": round(r.score, 2),
            "highlights": r.highlights,
        }
        for r in results
    ]
