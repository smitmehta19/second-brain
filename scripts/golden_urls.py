"""Golden-URL extraction smoke test.

Runs a fixed set of representative, stable URLs (one per major platform we
support) through the real extraction pipeline and scores each result with
:func:`src.extractors.confidence.score_extraction`. This is a pure sanity
check for the *extraction* layer — it never touches the database or Notion,
so it's safe to run any time to catch extractor regressions (e.g. a
platform changed its markup and we're now silently getting a cookie wall).

Usage::

    python -m scripts.golden_urls

Exit code is 1 if more than 3 URLs come back FAIL, so this can be wired
into CI as a cheap regression gate. Each URL is capped at 45s so one dead
site can't hang the whole run.
"""

from __future__ import annotations

import asyncio
import logging
import sys
import time
from dataclasses import dataclass
from pathlib import Path

# Ensure the project root is on the path so src.* imports work when run
# via `python -m scripts.golden_urls` from anywhere.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from src.extractors import extract_content  # noqa: E402
from src.extractors.confidence import score_extraction  # noqa: E402
from src.models.schemas import ContentType, RawCapture  # noqa: E402

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger(__name__)

# Per-URL timeout — a hung extractor should never block the whole smoke run.
_TIMEOUT_SECONDS = 45

# Thresholds mirrored from confidence scoring / settings.extraction_confidence_threshold.
_PASS_THRESHOLD = 0.5
_WARN_THRESHOLD = 0.3

# Max FAILs tolerated before the script exits non-zero (CI regression gate).
_MAX_ALLOWED_FAILS = 3

# ---------------------------------------------------------------------------
# Golden URL set — one representative, stable URL per platform we extract.
# Picked for longevity: canonical pages that are unlikely to 404 or move.
# ---------------------------------------------------------------------------
GOLDEN_URLS: list[str] = [
    # YouTube video
    "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    # YouTube short
    "https://www.youtube.com/shorts/dQw4w9WgXcQ",
    # Instagram reel
    "https://www.instagram.com/reel/C1234567890/",
    # Substack post
    "https://stratechery.com/2023/an-interview-with-openai-ceo-sam-altman/",
    # News article (BBC)
    "https://www.bbc.com/news/technology-67096530",
    # Wikipedia page
    "https://en.wikipedia.org/wiki/Artificial_intelligence",
    # GitHub repo
    "https://github.com/torvalds/linux",
    # Amazon product
    "https://www.amazon.com/dp/B0BSHF7WHW",
    # Reddit thread
    "https://www.reddit.com/r/programming/comments/1b2m3k4/",
    # Medium post
    "https://medium.com/@karpathy/software-2-0-a64152b37c35",
    # arXiv abstract
    "https://arxiv.org/abs/1706.03762",
    # Recipe page
    "https://www.allrecipes.com/recipe/10813/best-chocolate-chip-cookies/",
]


@dataclass
class GoldenResult:
    url: str
    extractor: str
    word_count: int
    confidence: float | None
    verdict: str
    error: str | None = None


def _extractor_used(extracted) -> str:
    """Best-effort label for which extractor produced this content.

    Prefers an explicit ``extractor`` key in metadata (if a future extractor
    starts setting one); falls back to the coarser ``source_platform`` on
    the ExtractedContent, which every extractor already sets.
    """
    metadata = getattr(extracted, "metadata", None) or {}
    if metadata.get("extractor"):
        return str(metadata["extractor"])
    platform = getattr(extracted, "source_platform", None)
    return getattr(platform, "value", str(platform)) if platform else "unknown"


def _verdict(confidence: float | None) -> str:
    if confidence is None:
        return "FAIL"
    if confidence >= _PASS_THRESHOLD:
        return "PASS"
    if confidence >= _WARN_THRESHOLD:
        return "WARN"
    return "FAIL"


async def _run_one(url: str) -> GoldenResult:
    """Extract a single URL, guarded by a hard timeout. Never raises."""
    capture = RawCapture(content_type=ContentType.URL, url=url)
    try:
        extracted = await asyncio.wait_for(
            extract_content(capture), timeout=_TIMEOUT_SECONDS
        )
    except asyncio.TimeoutError:
        return GoldenResult(
            url=url, extractor="timeout", word_count=0, confidence=None,
            verdict="FAIL", error=f"timed out after {_TIMEOUT_SECONDS}s",
        )
    except Exception as exc:  # noqa: BLE001 — smoke test must not crash mid-run
        return GoldenResult(
            url=url, extractor="error", word_count=0, confidence=None,
            verdict="FAIL", error=str(exc),
        )

    content = (extracted.content or "").strip()
    word_count = len(content.split()) if content else 0
    try:
        confidence = score_extraction(extracted)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Scoring failed for %s: %s", url, exc)
        confidence = None

    return GoldenResult(
        url=url,
        extractor=_extractor_used(extracted),
        word_count=word_count,
        confidence=confidence,
        verdict=_verdict(confidence),
    )


def _print_table(results: list[GoldenResult]) -> None:
    col_url, col_ext, col_wc, col_conf, col_verdict = 60, 12, 8, 10, 8

    def row(url: str, ext: str, wc: str, conf: str, verdict: str) -> str:
        return (
            f"{url[:col_url]:<{col_url}} "
            f"{ext[:col_ext]:<{col_ext}} "
            f"{wc:<{col_wc}} "
            f"{conf:<{col_conf}} "
            f"{verdict:<{col_verdict}}"
        )

    print(row("URL", "EXTRACTOR", "WORDS", "CONF", "VERDICT"))
    print("-" * (col_url + col_ext + col_wc + col_conf + col_verdict + 4))
    for r in results:
        conf_str = f"{r.confidence:.2f}" if r.confidence is not None else "n/a"
        print(row(r.url, r.extractor, str(r.word_count), conf_str, r.verdict))
        if r.error:
            print(f"    ! {r.error}")


async def main() -> int:
    print(f"Running golden-URL extraction smoke test on {len(GOLDEN_URLS)} URLs...\n")
    start = time.monotonic()

    results = []
    for url in GOLDEN_URLS:
        results.append(await _run_one(url))

    elapsed = time.monotonic() - start
    _print_table(results)

    fail_count = sum(1 for r in results if r.verdict == "FAIL")
    warn_count = sum(1 for r in results if r.verdict == "WARN")
    pass_count = sum(1 for r in results if r.verdict == "PASS")

    print(
        f"\n{pass_count} PASS, {warn_count} WARN, {fail_count} FAIL "
        f"out of {len(results)} ({elapsed:.1f}s total)"
    )

    if fail_count > _MAX_ALLOWED_FAILS:
        print(f"FAILED: more than {_MAX_ALLOWED_FAILS} URLs failed extraction.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
