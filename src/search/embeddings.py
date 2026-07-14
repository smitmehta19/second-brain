"""Multilingual sentence embeddings for smart categorization and search.

Uses intfloat/multilingual-e5-small (100+ languages, 384-dim embeddings).
Loads lazily on first use. Falls back gracefully if dependencies are missing.

On Oracle Cloud free tier (ARM64, no GPU): use ONNX int8 backend (~140MB RAM).
Locally: uses PyTorch backend by default.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

_MODEL_NAME = "intfloat/multilingual-e5-small"
_model = None
_available: bool | None = None


def is_available() -> bool:
    """Check if sentence-transformers is installed AND usable.

    Catches broader exceptions than ImportError because broken dependency
    versions (e.g. PyTorch/NumPy version mismatch) raise NameError/RuntimeError
    on import. Caches the result so repeated probes are free.
    """
    global _available
    if _available is None:
        try:
            import sentence_transformers  # noqa: F401
            _available = True
        except Exception as exc:
            _available = False
            logger.info("semantic features disabled (%s: %s)", type(exc).__name__, str(exc)[:80])
    return _available


def _get_model():
    """Lazy-load the embedding model (first call takes ~5s)."""
    global _model
    if _model is not None:
        return _model
    if not is_available():
        return None

    from sentence_transformers import SentenceTransformer
    from pathlib import Path
    import platform

    cache_dir = Path(__file__).resolve().parent.parent.parent / "data" / "models"
    cache_dir.mkdir(parents=True, exist_ok=True)

    onnx_path = cache_dir / "multilingual-e5-small-onnx"
    backend = "default"

    # Use ONNX on ARM64 (Oracle Cloud) for efficiency
    if platform.machine() in ("aarch64", "arm64"):
        try:
            import onnxruntime  # noqa: F401
            if onnx_path.exists():
                _model = SentenceTransformer(str(onnx_path), backend="onnx")
                logger.info("Loaded multilingual-e5-small (ONNX ARM64)")
                return _model
            backend = "default"
        except ImportError:
            pass

    _model = SentenceTransformer(_MODEL_NAME, cache_folder=str(cache_dir))
    logger.info("Loaded multilingual-e5-small (PyTorch)")
    return _model


def encode(texts: list[str], prefix: str = "query: ") -> list[list[float]]:
    """Encode texts into 384-dim embeddings.

    E5 models require a prefix: "query: " for queries, "passage: " for documents.
    """
    model = _get_model()
    if model is None:
        return []
    prefixed = [f"{prefix}{t}" for t in texts]
    embeddings = model.encode(prefixed, normalize_embeddings=True)
    return embeddings.tolist()


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two vectors."""
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(x * x for x in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


# ---------------------------------------------------------------------------
# Domain descriptions for semantic matching — richer than keyword lists
# ---------------------------------------------------------------------------

_DOMAIN_DESCRIPTIONS: dict[str, str] = {
    "data-engineering": "Data pipelines, ETL, databases, SQL, Spark, Airflow, dbt, Kafka, data warehousing, streaming, batch processing, data modeling",
    "gen-ai": "Large language models, GPT, Claude, transformers, fine-tuning, RAG, prompt engineering, LangChain, vector databases, embeddings, diffusion models, AI agents",
    "data-science": "Machine learning, deep learning, neural networks, statistics, regression, classification, clustering, pandas, scikit-learn, PyTorch, TensorFlow",
    "computer-science": "Algorithms, data structures, system design, distributed systems, concurrency, networking, operating systems, complexity theory, dynamic programming",
    "job-search": "Resume, CV, interviews, hiring, recruiters, job postings, salary negotiation, LinkedIn, career growth, job market, applications",
    "fitness": "Workouts, exercise, gym, muscle building, cardio, strength training, protein, calories, running, yoga, flexibility, recovery, HIIT",
    "cooking": "Recipes, vegetarian food, vegan cooking, ingredients, meal prep, nutrition, Indian food, paneer, dal, curry, baking, kitchen",
    "personal-finance": "Investing, stocks, mutual funds, ETF, SIP, budgeting, savings, retirement, tax planning, insurance, portfolio management, crypto",
    "wedding": "Wedding planning, venues, catering, invitations, ceremony, reception, photography, decorations, guest lists, engagement",
    "politics": "Elections, government, policy, parliament, democracy, legislation, geopolitics, diplomacy, political parties, voting",
    "india": "India, Indian culture, cities like Delhi Mumbai Bangalore, rupee, NRI, Aadhaar, UPI, Indian regulations",
    "ireland": "Ireland, Dublin, Irish visa, stamp permissions, PPS number, Eircode, HSE, Revenue, living in Ireland",
    "anime": "Anime, manga, shonen, isekai, One Piece, Naruto, Attack on Titan, Jujutsu Kaisen, Crunchyroll, Studio Ghibli",
    "market-intelligence": "Market research, competitor analysis, industry trends, market sizing, TAM, business models, startups, venture capital, funding, IPOs",
    "applied-ai": "AI applications, production ML, MLOps, model deployment, inference, AI products, AI startups, automation, computer vision, NLP",
}

_domain_embeddings: dict[str, list[float]] | None = None


def _get_domain_embeddings() -> dict[str, list[float]]:
    """Compute and cache domain description embeddings."""
    global _domain_embeddings
    if _domain_embeddings is not None:
        return _domain_embeddings

    from src.config.domains import DOMAINS

    # Build descriptions for all domains (including dynamically discovered ones)
    descriptions = {}
    for key in DOMAINS:
        if key in _DOMAIN_DESCRIPTIONS:
            descriptions[key] = _DOMAIN_DESCRIPTIONS[key]
        else:
            display = key.replace("-", " ").title()
            kws = ", ".join(DOMAINS[key].get("keywords", [key]))
            descriptions[key] = f"{display}: {kws}"

    keys = list(descriptions.keys())
    texts = [descriptions[k] for k in keys]
    embeddings = encode(texts, prefix="passage: ")

    if not embeddings:
        _domain_embeddings = {}
        return _domain_embeddings

    _domain_embeddings = dict(zip(keys, embeddings))
    return _domain_embeddings


def match_domains(text: str, top_k: int = 3, threshold: float = 0.3) -> list[tuple[str, float]]:
    """Find the most semantically similar domains for a piece of text.

    Returns list of (domain_key, similarity_score) sorted by score descending.
    Only returns domains above the threshold.
    """
    if not is_available():
        return []

    domain_embs = _get_domain_embeddings()
    if not domain_embs:
        return []

    text_emb = encode([text[:2000]], prefix="query: ")
    if not text_emb:
        return []

    scores = []
    for domain_key, domain_emb in domain_embs.items():
        sim = cosine_similarity(text_emb[0], domain_emb)
        if sim >= threshold:
            scores.append((domain_key, sim))

    scores.sort(key=lambda x: x[1], reverse=True)
    return scores[:top_k]


def extract_keywords(text: str, top_n: int = 8) -> list[str]:
    """Extract keywords using KeyBERT with the multilingual backend."""
    if not is_available():
        return []

    try:
        from keybert import KeyBERT

        model = _get_model()
        if model is None:
            return []

        kw_model = KeyBERT(model=model)
        keywords = kw_model.extract_keywords(
            text[:3000],
            keyphrase_ngram_range=(1, 2),
            stop_words="english",
            top_n=top_n,
            use_mmr=True,
            diversity=0.5,
        )
        return [kw for kw, _ in keywords]
    except ImportError:
        logger.debug("keybert not installed — keyword extraction unavailable")
        return []
    except Exception:
        logger.debug("Keyword extraction failed", exc_info=True)
        return []
