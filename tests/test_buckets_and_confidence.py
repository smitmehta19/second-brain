"""Smoke tests: rules-first bucket cascade, extraction confidence, distillation."""

from __future__ import annotations

from src.categorizer.ai_categorizer import _rules_bucket
from src.categorizer.prompts import distill_content
from src.extractors.confidence import score_extraction
from src.models.schemas import ContentType, ExtractedContent


def _extracted(content: str, title: str = "A Real Title") -> ExtractedContent:
    return ExtractedContent(
        raw_id="test", title=title, content=content,
        content_type=ContentType.URL, url="https://example.com/x",
    )


class TestRulesBucketCascade:
    def test_high_confidence_types_bypass_ai(self):
        assert _rules_bucket("ecommerce", "https://amazon.com/p") == "SHOP"
        assert _rules_bucket("recipe", "https://cooking.com/r") == "MAKE"
        assert _rules_bucket("long_video", "https://youtube.com/watch?v=x") == "WATCH-LONG"
        assert _rules_bucket("short_video", "https://youtube.com/shorts/x") == "WATCH-SHORT"
        assert _rules_bucket("job", "https://linkedin.com/jobs/1") == "CAREER"

    def test_ambiguous_types_defer_to_ai(self):
        assert _rules_bucket("blog_article", "https://blog.com/post") is None
        assert _rules_bucket("unknown", "https://example.com") is None


class TestExtractionConfidence:
    def test_empty_content_scores_zero(self):
        assert score_extraction(_extracted("")) == 0.0

    def test_real_article_scores_high(self):
        content = " ".join(["word"] * 300)
        assert score_extraction(_extracted(content)) >= 0.7

    def test_cookie_wall_capped_low(self):
        content = "Please enable JavaScript to continue. " + " ".join(["word"] * 300)
        assert score_extraction(_extracted(content)) <= 0.3

    def test_score_clamped_to_unit_interval(self):
        s = score_extraction(_extracted(" ".join(["word"] * 1000)))
        assert 0.0 <= s <= 1.0


class TestDistillContent:
    def test_short_text_unchanged(self):
        assert distill_content("short text", max_chars=5000) == "short text"

    def test_respects_budget(self):
        long_text = "\n\n".join(f"Paragraph {i}: " + "x " * 200 for i in range(60))
        assert len(distill_content(long_text, max_chars=5000)) <= 5500

    def test_keeps_lead_and_tail(self):
        paragraphs = [f"UNIQUE_LEAD_{i} " + "x " * 100 for i in range(3)]
        paragraphs += ["middle " + "y " * 150 for _ in range(30)]
        paragraphs += ["UNIQUE_TAIL_MARKER " + "z " * 50]
        distilled = distill_content("\n\n".join(paragraphs), max_chars=5000)
        assert "UNIQUE_LEAD_0" in distilled
        assert "UNIQUE_TAIL_MARKER" in distilled
