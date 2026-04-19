# src/infrastructure/rule_based_brief_generator.py

"""
Rule-based brief generator — concrete implementation of BriefGeneratorPort.

Generates fully structured ContentBrief entities using a deterministic rule
engine. No LLM, no external network call, no randomness — every output field
is derived from explicit business rules keyed on:

    1. TrendTier  — classified from search_volume via fixed thresholds.
    2. keyword    — used for text personalisation (titles, hashtags, summaries).
    3. volume     — used for continuous fields (SEO difficulty, word-count scaling).

This adapter is a drop-in replacement target: swapping it for an LLM-backed
implementation requires zero changes to the use case or any other layer.

Rule modules (private static methods by component):
    _determine_tier            → TrendTier
    _build_executive_summary   → str
    _build_audience_profile    → AudienceProfile
    _build_recommended_format  → RecommendedFormat
    _build_outline             → list[OutlineSection]
    _build_seo_recommendations → SeoRecommendations
    _build_distribution_plan   → DistributionPlan
    _generate_hashtags         → list[str]   (shared utility)
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Final

from src.core.brief_entities import (
    AudienceProfile,
    ContentBrief,
    ContentFormatType,
    DistributionPlan,
    OutlineSection,
    RecommendedFormat,
    SearchIntent,
    SeoRecommendations,
    TrendTier,
    UrgencyLevel,
)
from src.core.brief_ports import BriefGeneratorPort
from src.core.entities import TrendTopic
from src.core.exceptions import BriefGenerationError

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tier classification thresholds — must mirror TrendAnalyzerUseCase constants.
# ---------------------------------------------------------------------------
_BREAKING_THRESHOLD: Final[int] = 80
_EMERGING_THRESHOLD: Final[int] = 60

# Output caps.
_MAX_HASHTAGS: Final[int] = 8
_MAX_SEMANTIC_KEYWORDS: Final[int] = 10
_MAX_TITLE_VARIANTS: Final[int] = 4


class RuleBasedBriefGeneratorAdapter(BriefGeneratorPort):
    """
    Stateless, deterministic implementation of BriefGeneratorPort.

    Every public call to `generate()` produces an independent, immutable
    ContentBrief. The adapter holds no mutable state and is safe for
    concurrent use without any synchronisation.
    """

    # ------------------------------------------------------------------
    # BriefGeneratorPort
    # ------------------------------------------------------------------

    def generate(self, topic: TrendTopic, source_file: str) -> ContentBrief:
        """
        Construct a ContentBrief from the given TrendTopic using the rule engine.

        Args:
            topic:       Validated upstream TrendTopic entity.
            source_file: Bare filename of the source trend data (for audit trail).

        Returns:
            Immutable, fully validated ContentBrief entity.

        Raises:
            BriefGenerationError: Wraps any unexpected error so domain exceptions
                                  are all the caller ever sees.
        """
        try:
            tier = self._determine_tier(topic.search_volume)
            keyword = topic.topic_name  # Already title-cased by upstream validator.

            logger.debug(
                "Building brief — keyword='%s'  volume=%d  tier=%s.",
                keyword,
                topic.search_volume,
                tier.value,
            )

            return ContentBrief(
                topic_name=keyword,
                region=topic.target_country,
                search_volume=topic.search_volume,
                trend_tier=tier,
                is_growing=topic.is_growing,
                suggested_angle=topic.suggested_angle,
                executive_summary=self._build_executive_summary(topic, tier),
                audience_profile=self._build_audience_profile(tier, keyword),
                recommended_format=self._build_recommended_format(tier),
                content_outline=self._build_outline(tier, keyword),
                seo_recommendations=self._build_seo_recommendations(
                    tier, keyword, topic.search_volume
                ),
                distribution_plan=self._build_distribution_plan(tier, keyword),
                source_trend_file=source_file,
            )

        except Exception as exc:
            raise BriefGenerationError(
                topic=topic.topic_name,
                reason=f"{type(exc).__name__}: {exc}",
            ) from exc

    # ------------------------------------------------------------------
    # Tier classification
    # ------------------------------------------------------------------

    @staticmethod
    def _determine_tier(volume: int) -> TrendTier:
        """
        Map a normalised search volume score to a TrendTier.

        Thresholds are deliberately aligned with the upstream
        TrendAnalyzerUseCase constants to ensure cross-pipeline consistency.

        Args:
            volume: Integer search interest score in [0, 100].

        Returns:
            TrendTier enum member.
        """
        if volume >= _BREAKING_THRESHOLD:
            return TrendTier.BREAKING
        if volume >= _EMERGING_THRESHOLD:
            return TrendTier.EMERGING
        return TrendTier.DEEP_DIVE

    # ------------------------------------------------------------------
    # Executive summary
    # ------------------------------------------------------------------

    @staticmethod
    def _build_executive_summary(topic: TrendTopic, tier: TrendTier) -> str:
        """
        Compose a 3–5 sentence strategic overview personalised to the tier.

        The summary communicates urgency, opportunity, and the brief's core
        strategic rationale — giving a content manager immediate context
        without reading the full brief.

        Args:
            topic: Source TrendTopic carrying volume, region, and momentum data.
            tier:  Classified TrendTier.

        Returns:
            Non-empty, stripped executive summary string.
        """
        kw = topic.topic_name
        vol = topic.search_volume
        region = topic.target_country
        momentum = "and accelerating" if topic.is_growing else "with sustained interest"

        if tier == TrendTier.BREAKING:
            return (
                f"'{kw}' has surged to a search volume score of {vol}/100 in "
                f"{region} {momentum}, placing it in the top tier of trending topics. "
                f"This breaking-level interest signals a significant cultural or market "
                f"moment demanding immediate, high-quality content coverage. "
                f"The brief prescribes a rapid-turnaround strategy optimised to capture "
                f"peak organic traffic before competitors saturate the keyword. "
                f"Speed-to-publish is the primary success factor — every hour of delay "
                f"concedes search real estate to faster-moving publishers."
            )

        if tier == TrendTier.EMERGING:
            return (
                f"'{kw}' is registering a search volume score of {vol}/100 in "
                f"{region} {momentum}, signalling an early-stage trend with substantial "
                f"upside for first-mover content creators. "
                f"This brief outlines a strategic roadmap for establishing topical "
                f"authority before mainstream media coverage commoditises the keyword. "
                f"The window for differentiated, high-ranking content is open now — "
                f"this plan is optimised to exploit it efficiently."
            )

        # DEEP_DIVE
        return (
            f"'{kw}' maintains a focused search volume score of {vol}/100 in "
            f"{region} {momentum}, characteristic of a niche topic with a highly "
            f"engaged specialist audience that values depth over brevity. "
            f"This brief outlines a comprehensive, long-form strategy designed to "
            f"position the publisher as the definitive authority on this subject. "
            f"Ranking in this niche requires thorough coverage, authoritative sourcing, "
            f"and a patient, community-first distribution strategy — "
            f"this plan is built around those constraints."
        )

    # ------------------------------------------------------------------
    # Audience profile
    # ------------------------------------------------------------------

    @staticmethod
    def _build_audience_profile(tier: TrendTier, keyword: str) -> AudienceProfile:
        """
        Derive a target audience persona from the trend tier and keyword text.

        Personas use archetype-based language: concrete enough to guide
        content writing, generic enough to apply across diverse industries.

        Args:
            tier:    TrendTier classification.
            keyword: Title-cased trend keyword for inline contextualisation.

        Returns:
            Validated, immutable AudienceProfile entity.
        """
        kw = keyword.lower()

        if tier == TrendTier.BREAKING:
            return AudienceProfile(
                primary_segment="General public, news consumers, and social-media power users",
                secondary_segment=f"Industry professionals seeking real-time updates on '{kw}'",
                pain_points=[
                    f"Fear of missing the '{kw}' conversation as it evolves",
                    "Difficulty finding reliable, fast-moving summaries amid noise",
                    "Information overload — need the essential facts, immediately",
                ],
                goals=[
                    "Stay informed and share credible insights quickly",
                    f"Understand the implications of '{kw}' for their field or daily life",
                    "Be among the first in their network to discuss this development",
                ],
                content_consumption_habits=[
                    "Social feeds (X/Twitter, LinkedIn, Reddit, Threads)",
                    "Push notifications from trusted news sources",
                    "News aggregators (Google News, Apple News, Flipboard)",
                    "Short-form video (YouTube Shorts, TikTok, Instagram Reels)",
                ],
                demographic_hint=(
                    "Ages 22–45, broad demographic, high digital engagement, mobile-first"
                ),
            )

        if tier == TrendTier.EMERGING:
            return AudienceProfile(
                primary_segment=(
                    "Early adopters, enthusiasts, and professionals adjacent to the trend"
                ),
                secondary_segment=(
                    f"Forward-thinking decision-makers evaluating '{kw}' for adoption"
                ),
                pain_points=[
                    f"Lack of comprehensive, credible resources on '{kw}'",
                    "Difficulty evaluating competing narratives or early-stage hype",
                    "Uncertainty about when and how to act on emerging information",
                ],
                goals=[
                    f"Build early expertise and credibility around '{kw}'",
                    "Identify practical applications before the mainstream catches up",
                    "Network with peers who are exploring the same emerging space",
                ],
                content_consumption_habits=[
                    "Industry newsletters and Substack publications",
                    "Long-form articles on company blogs, Medium, and trade press",
                    "LinkedIn thought-leadership posts and articles",
                    "Specialist podcasts, webinars, and async video content",
                ],
                demographic_hint=(
                    "Ages 28–48, educated, career-focused, active on LinkedIn and niche communities"
                ),
            )

        # DEEP_DIVE
        return AudienceProfile(
            primary_segment=(
                "Subject-matter experts, researchers, and senior technical practitioners"
            ),
            secondary_segment=(
                f"Senior decision-makers seeking authoritative briefings on '{kw}'"
            ),
            pain_points=[
                f"Existing content on '{kw}' is too shallow or commercially biased",
                "Difficulty connecting rigorous analysis to actionable professional decisions",
                "Limited access to well-sourced, peer-reviewed reference material",
            ],
            goals=[
                f"Deepen specialist expertise with rigorous, cited content on '{kw}'",
                "Identify nuanced insights absent from mainstream coverage",
                f"Use '{kw}' analysis to justify or inform high-stakes decisions",
            ],
            content_consumption_habits=[
                "Academic papers and pre-print servers (arXiv, SSRN)",
                "Analyst reports, whitepapers, and institutional publications",
                "Long-form journalism (The Economist, FT, Wired, The Atlantic)",
                "Curated email newsletters from recognised domain experts",
            ],
            demographic_hint=(
                "Ages 30–55, post-graduate education, professional context, primarily desktop readers"
            ),
        )

    # ------------------------------------------------------------------
    # Recommended format
    # ------------------------------------------------------------------

    @staticmethod
    def _build_recommended_format(tier: TrendTier) -> RecommendedFormat:
        """
        Select the optimal content format and platform stack for the tier.

        BREAKING → Social thread (fastest production, highest velocity reach)
        EMERGING → Blog post, medium length (SEO + shareability balance)
        DEEP_DIVE → Blog post, long-form (maximum authority and SEO durability)

        Args:
            tier: TrendTier classification.

        Returns:
            Validated, immutable RecommendedFormat entity.
        """
        if tier == TrendTier.BREAKING:
            return RecommendedFormat(
                format_type=ContentFormatType.SOCIAL_THREAD,
                estimated_length="8–12 posts (≈ 800–1,200 characters total)",
                primary_platform="X (Twitter)",
                secondary_platforms=[
                    "LinkedIn (condensed professional-insight post)",
                    "Reddit (relevant subreddit thread)",
                    "Instagram Stories (key stats as image cards)",
                ],
                tone=(
                    "Urgent, factual, and conversational — "
                    "breaking-news journalist covering a live story"
                ),
            )

        if tier == TrendTier.EMERGING:
            return RecommendedFormat(
                format_type=ContentFormatType.BLOG_POST,
                estimated_length="1,400–2,000 words",
                primary_platform="Company blog / personal Substack",
                secondary_platforms=[
                    "LinkedIn article (adapted 800-word version)",
                    "Medium (canonical-tagged republish, 2 weeks post-launch)",
                    "Email newsletter (teaser + full-article link)",
                ],
                tone=(
                    "Authoritative yet approachable — expert guide, not academic paper; "
                    "jargon explained, not avoided"
                ),
            )

        # DEEP_DIVE
        return RecommendedFormat(
            format_type=ContentFormatType.BLOG_POST,
            estimated_length="2,500–4,000 words",
            primary_platform="Company blog with strong domain authority",
            secondary_platforms=[
                "SlideShare (executive-summary deck version)",
                "Email deep-dive newsletter segment",
                "Podcast episode (article as script + Q&A)",
            ],
            tone=(
                "Rigorous, well-cited, and intellectually precise — "
                "academic quality with journalist-level readability"
            ),
        )

    # ------------------------------------------------------------------
    # Content outline (dispatcher + tier-specific builders)
    # ------------------------------------------------------------------

    def _build_outline(self, tier: TrendTier, keyword: str) -> list[OutlineSection]:
        """
        Construct a tier-appropriate, keyword-personalised content outline.

        Dispatches to a tier-specific private builder. Section counts and word
        counts are calibrated to the corresponding RecommendedFormat length.

        Args:
            tier:    TrendTier classification.
            keyword: Title-cased trend keyword.

        Returns:
            Ordered list of OutlineSection entities (len 5, 6, or 7 by tier).
        """
        if tier == TrendTier.BREAKING:
            return self._breaking_outline(keyword)
        if tier == TrendTier.EMERGING:
            return self._emerging_outline(keyword)
        return self._deep_dive_outline(keyword)

    @staticmethod
    def _breaking_outline(keyword: str) -> list[OutlineSection]:
        """5-section, ~1,050-word outline for BREAKING tier. Optimised for speed."""
        kw = keyword
        return [
            OutlineSection(
                section_number=1,
                title=f"The Story: What Is Happening With {kw} Right Now",
                purpose=(
                    "Hook readers immediately. Establish urgency and relevance "
                    "within the first two sentences."
                ),
                key_points=[
                    f"Open with the central breaking fact: '{kw}' is at peak search interest",
                    "Name the specific trigger event or signal driving the surge",
                    "Establish stakes in one sentence — who is affected and why it matters today",
                ],
                estimated_word_count=150,
            ),
            OutlineSection(
                section_number=2,
                title=f"Context: Why {kw} Is Exploding Now",
                purpose="Give readers the minimum background needed to follow the story.",
                key_points=[
                    f"Brief origin or background of '{kw}' (2–3 sentences maximum)",
                    "The specific catalyst making this a breaking moment",
                    "Key stakeholders, communities, or industries directly affected",
                ],
                estimated_word_count=300,
            ),
            OutlineSection(
                section_number=3,
                title="The Numbers: What the Data Shows",
                purpose="Anchor the narrative in verifiable, shareable evidence.",
                key_points=[
                    f"Search volume context: '{kw}' reached peak interest (score 80–100/100)",
                    "Timeline of the trend's acceleration over 24–72 hours",
                    "Supporting data points: social mentions, coverage spikes, or related metrics",
                ],
                estimated_word_count=200,
            ),
            OutlineSection(
                section_number=4,
                title="Reactions and Perspectives: What People Are Saying",
                purpose="Add journalistic balance by surfacing diverse viewpoints.",
                key_points=[
                    "Leading voices or official statements (paraphrase; limit direct quotes)",
                    "Community sentiment: consensus, dissent, and notable reactions",
                    "Contrarian or sceptical perspective for credibility and nuance",
                ],
                estimated_word_count=250,
            ),
            OutlineSection(
                section_number=5,
                title=f"What Happens Next: The {kw} Outlook",
                purpose="Leave readers with forward-looking value and a clear CTA.",
                key_points=[
                    f"Short-term implications: next 24–72 hours",
                    f"Key signals to monitor as '{kw}' evolves",
                    "CTA: follow for real-time updates or subscribe to the newsletter",
                ],
                estimated_word_count=150,
            ),
        ]

    @staticmethod
    def _emerging_outline(keyword: str) -> list[OutlineSection]:
        """6-section, ~1,650-word outline for EMERGING tier. Optimised for SEO authority."""
        kw = keyword
        return [
            OutlineSection(
                section_number=1,
                title=f"Why {kw} Is Gaining Serious Momentum",
                purpose=(
                    "Hook readers by framing the opportunity before the mainstream notices."
                ),
                key_points=[
                    f"Opening hook: '{kw}' is trending — but most people haven't caught up yet",
                    "The specific signal (search data, funding news, industry buzz) driving interest",
                    "Why this matters and who should be paying attention right now",
                ],
                estimated_word_count=200,
            ),
            OutlineSection(
                section_number=2,
                title=f"What Is {kw}? Background and Origins",
                purpose=(
                    "Establish foundational understanding without condescending "
                    "to informed readers."
                ),
                key_points=[
                    f"Define '{kw}' clearly in plain language (no jargon without explanation)",
                    "Where it came from and how it developed to its current stage",
                    "Key terminology a newcomer will encounter when researching this topic",
                ],
                estimated_word_count=300,
            ),
            OutlineSection(
                section_number=3,
                title=f"Real-World Applications: Where {kw} Is Already Being Used",
                purpose="Ground the trend in tangible use cases to bridge theory and practice.",
                key_points=[
                    "2–3 concrete examples of organisations or individuals already leveraging this",
                    "Quantified outcomes or results where available",
                    f"The most accessible entry point for readers who are new to '{kw}'",
                ],
                estimated_word_count=400,
            ),
            OutlineSection(
                section_number=4,
                title="Who Is Already Winning — and What They're Doing Right",
                purpose="Profile early movers to give readers an actionable competitive benchmark.",
                key_points=[
                    f"Early adopters of '{kw}': what differentiates their approach",
                    "Patterns or behaviours consistently observed in successful early movers",
                    "Distilled lessons from their experience",
                ],
                estimated_word_count=300,
            ),
            OutlineSection(
                section_number=5,
                title="Challenges and Honest Considerations",
                purpose="Build credibility by acknowledging real complexity and risk.",
                key_points=[
                    f"Common objections or adoption barriers to '{kw}'",
                    "Limitations of current evidence or the trend's maturity stage",
                    "Genuine risks or downsides early adopters should factor in",
                ],
                estimated_word_count=250,
            ),
            OutlineSection(
                section_number=6,
                title=f"Your First Steps: How to Get Ahead of {kw}",
                purpose="Convert interest into action with a low-barrier, concrete entry plan.",
                key_points=[
                    "3 actionable steps readers can take this week",
                    f"The best starting resources for going deeper on '{kw}'",
                    "CTA: subscribe, share, or join the community discussion",
                ],
                estimated_word_count=200,
            ),
        ]

    @staticmethod
    def _deep_dive_outline(keyword: str) -> list[OutlineSection]:
        """7-section, ~2,300-word outline for DEEP_DIVE tier. Optimised for authority."""
        kw = keyword
        return [
            OutlineSection(
                section_number=1,
                title=f"Introduction: Why {kw} Deserves Rigorous Attention",
                purpose="Frame the article's value proposition for a specialist audience.",
                key_points=[
                    f"Why '{kw}' is under-covered relative to its structural importance",
                    "The precise question or problem this article will answer",
                    "A brief roadmap of the article's analytical structure",
                ],
                estimated_word_count=200,
            ),
            OutlineSection(
                section_number=2,
                title=f"Historical Context: How {kw} Evolved to Its Current State",
                purpose=(
                    "Establish analytical depth by grounding the topic in its "
                    "developmental arc — a hallmark of authoritative content."
                ),
                key_points=[
                    f"Origins and earliest documented instances of '{kw}'",
                    "Key inflection points: what changed, when, and the causal drivers",
                    "Current state vs. where the field or concept began",
                ],
                estimated_word_count=400,
            ),
            OutlineSection(
                section_number=3,
                title=f"Technical Analysis: How {kw} Actually Works",
                purpose=(
                    "Deliver the substantive depth that differentiates expert content "
                    "from surface-level summaries."
                ),
                key_points=[
                    "Core mechanisms, frameworks, or models underpinning this topic",
                    "Technical nuances frequently misrepresented in mainstream coverage",
                    "Recommended visual: diagram, process chart, or annotated framework",
                ],
                estimated_word_count=500,
            ),
            OutlineSection(
                section_number=4,
                title="Case Studies: Evidence From the Real World",
                purpose="Ground abstract analysis in concrete, verifiable real-world outcomes.",
                key_points=[
                    "Case Study 1: a successful application with measurable, cited outcomes",
                    "Case Study 2: a failure or cautionary example — root cause analysis",
                    "Cross-case synthesis: what the evidence consistently supports",
                ],
                estimated_word_count=450,
            ),
            OutlineSection(
                section_number=5,
                title="Comparative Analysis: Positioning in the Broader Landscape",
                purpose=(
                    "Give specialist readers a decision framework by positioning "
                    "the topic relative to alternatives."
                ),
                key_points=[
                    f"How '{kw}' compares to 2–3 adjacent approaches or competing paradigms",
                    "Trade-offs, overlaps, and unique advantages of each path",
                    "Decision framework: when to choose this over the alternatives",
                ],
                estimated_word_count=350,
            ),
            OutlineSection(
                section_number=6,
                title="Future Trajectory: Where This Is Heading",
                purpose=(
                    "Demonstrate forward-looking expertise and give readers "
                    "strategic foresight they cannot get from historical coverage."
                ),
                key_points=[
                    f"Short-term trajectory of '{kw}' over the next 6–18 months",
                    "Longer-term structural forces shaping the space",
                    "Open questions and areas of active research or unresolved debate",
                ],
                estimated_word_count=250,
            ),
            OutlineSection(
                section_number=7,
                title="Conclusion: Key Takeaways and Curated Resources",
                purpose=(
                    "Consolidate learning and convert readers into subscribers "
                    "or repeat visitors."
                ),
                key_points=[
                    "3–5 distilled, actionable takeaways the reader can apply immediately",
                    f"Curated reading list: 3–5 authoritative external sources on '{kw}'",
                    "CTA: subscribe for ongoing expert coverage on this topic",
                ],
                estimated_word_count=150,
            ),
        ]

    # ------------------------------------------------------------------
    # SEO recommendations
    # ------------------------------------------------------------------

    @staticmethod
    def _build_seo_recommendations(
        tier: TrendTier,
        keyword: str,
        volume: int,
    ) -> SeoRecommendations:
        """
        Construct keyword strategy and metadata recommendations from the rule engine.

        Keyword difficulty estimation formula (tier-weighted):
            BREAKING  → min(90, round(0.80 × volume + 5))
                        High volume = intense competition for fresh content.
            EMERGING  → min(80, round(0.65 × volume + 5))
                        Moderate and growing competition.
            DEEP_DIVE → min(75, round(0.50 × volume + 15))
                        Established niche with entrenched authority sites.

        Args:
            tier:    TrendTier classification.
            keyword: Title-cased trend keyword.
            volume:  Normalised search interest score (0–100).

        Returns:
            Validated, immutable SeoRecommendations entity.
        """
        kw_lower = keyword.lower()
        year = datetime.now(tz=timezone.utc).year

        # -- Difficulty & intent --------------------------------------------
        if tier == TrendTier.BREAKING:
            difficulty = min(90, round(0.80 * volume + 5))
            intent = SearchIntent.NAVIGATIONAL
        elif tier == TrendTier.EMERGING:
            difficulty = min(80, round(0.65 * volume + 5))
            intent = SearchIntent.COMMERCIAL
        else:
            difficulty = min(75, round(0.50 * volume + 15))
            intent = SearchIntent.INFORMATIONAL

        # -- Semantic (LSI) keywords ----------------------------------------
        semantic_keywords: list[str] = [
            f"what is {kw_lower}",
            f"{kw_lower} explained",
            f"how {kw_lower} works",
            f"{kw_lower} {year}",
            f"{kw_lower} guide",
            f"best {kw_lower} resources",
            f"{kw_lower} for beginners",
            f"{kw_lower} examples",
            f"why {kw_lower} is trending",
            f"{kw_lower} tips and strategies",
        ][:_MAX_SEMANTIC_KEYWORDS]

        # -- Title variants -------------------------------------------------
        if tier == TrendTier.BREAKING:
            title_variants: list[str] = [
                f"Why '{keyword}' Is Dominating Search Right Now",
                f"Breaking: Everything You Need to Know About {keyword}",
                f"The Real Story Behind {keyword} — Explained",
                f"{keyword}: What's Happening and What It Means for You",
            ]
            meta = (
                f"Find out why '{kw_lower}' is trending right now. "
                f"Get the full picture — fast. Breaking coverage updated for {year}."
            )

        elif tier == TrendTier.EMERGING:
            title_variants = [
                f"The Complete Guide to {keyword} ({year})",
                f"Why {keyword} Is the Next Big Thing — And How to Get Ahead",
                f"{keyword}: What Early Adopters Know That You Don't Yet",
                f"How {keyword} Is Changing the Game in {year}",
            ]
            meta = (
                f"Discover the emerging trend of '{kw_lower}' before it goes mainstream. "
                f"Strategy, real-world use cases, and early-mover insight for {year}."
            )

        else:
            title_variants = [
                f"The Definitive Guide to {keyword} ({year} Edition)",
                f"{keyword} Deep Dive: A Comprehensive Expert Analysis",
                f"Everything You Need to Know About {keyword}",
                f"{keyword}: An Expert's Complete Reference for {year}",
            ]
            meta = (
                f"An authoritative, in-depth analysis of '{kw_lower}': "
                f"history, mechanics, case studies, and future trajectory. "
                f"The only reference you need in {year}."
            )

        return SeoRecommendations(
            primary_keyword=keyword,
            semantic_keywords=semantic_keywords,
            title_variants=title_variants[:_MAX_TITLE_VARIANTS],
            meta_description_template=meta[:300],
            target_search_intent=intent,
            estimated_keyword_difficulty=difficulty,
        )

    # ------------------------------------------------------------------
    # Distribution plan
    # ------------------------------------------------------------------

    @staticmethod
    def _build_distribution_plan(tier: TrendTier, keyword: str) -> DistributionPlan:
        """
        Prescribe a multi-channel publishing and timing strategy.

        Urgency is directly coupled to tier:
            BREAKING  → IMMEDIATE, social-first
            EMERGING  → THIS_WEEK, blog and search-optimised
            DEEP_DIVE → THIS_MONTH, email and community-first

        Args:
            tier:    TrendTier classification.
            keyword: Title-cased trend keyword for hashtag generation.

        Returns:
            Validated, immutable DistributionPlan entity.
        """
        hashtags = RuleBasedBriefGeneratorAdapter._generate_hashtags(keyword, tier)

        if tier == TrendTier.BREAKING:
            return DistributionPlan(
                primary_channel="X (Twitter) — thread format for maximum velocity reach",
                secondary_channels=[
                    "LinkedIn (reframed as a professional-insight post)",
                    "Reddit (2–3 relevant subreddits; read community rules first)",
                    "Instagram Stories (3 key data points as image cards)",
                ],
                optimal_publish_window=(
                    "Immediately — within 2–4 hours of brief generation"
                ),
                hashtag_suggestions=hashtags,
                urgency_level=UrgencyLevel.IMMEDIATE,
                cross_posting_notes=(
                    "LinkedIn: open with the professional implication, not the trending angle. "
                    "Reddit: avoid explicit self-promotion; lead with value, link in comments. "
                    "Instagram: convert 3 data points into visually distinct, text-on-image story cards."
                ),
            )

        if tier == TrendTier.EMERGING:
            return DistributionPlan(
                primary_channel=(
                    "Company blog / Substack — long-form for SEO authority and shareability"
                ),
                secondary_channels=[
                    "LinkedIn article (adapted 800-word version, posted 48 h after primary)",
                    "Email newsletter (send to full list with teaser + article link)",
                    "Medium (republish with canonical tag pointing to original, 2 weeks later)",
                ],
                optimal_publish_window=(
                    "Tuesday–Thursday, 9:00 AM–11:00 AM in the target region's timezone"
                ),
                hashtag_suggestions=hashtags,
                urgency_level=UrgencyLevel.THIS_WEEK,
                cross_posting_notes=(
                    "Wait 48 hours after primary publication before LinkedIn cross-post to avoid "
                    "cannibalising organic reach windows. "
                    "Always add a Medium canonical URL pointing to the original post to protect SEO. "
                    "Newsletter subject line: include one specific, actionable takeaway."
                ),
            )

        # DEEP_DIVE
        return DistributionPlan(
            primary_channel=(
                "Company blog / owned publication — SEO-optimised, long-form anchor content"
            ),
            secondary_channels=[
                "Email newsletter — dedicated deep-dive segment for highest-intent subscribers",
                "SlideShare — executive-summary deck version for B2B reach",
                "Relevant specialist Slack communities or Discord servers",
                "Podcast episode using article as script with live Q&A segment",
            ],
            optimal_publish_window=(
                "Sunday–Monday, 7:00 AM–9:00 AM in the target region's timezone"
            ),
            hashtag_suggestions=hashtags,
            urgency_level=UrgencyLevel.THIS_MONTH,
            cross_posting_notes=(
                "Repurpose the 'Historical Context' section as a standalone LinkedIn carousel. "
                "The Comparative Analysis table is an excellent standalone infographic for Pinterest or Instagram. "
                "Pitch the full article to 2–3 relevant industry newsletters for earned distribution. "
                "Consider a Reddit or Hacker News submission after the primary piece is indexed."
            ),
        )

    # ------------------------------------------------------------------
    # Shared utility: hashtag generation
    # ------------------------------------------------------------------

    @staticmethod
    def _generate_hashtags(keyword: str, tier: TrendTier) -> list[str]:
        """
        Generate relevant, platform-appropriate hashtags from the keyword and tier.

        Strategy (applied in order, deduplicated, capped at _MAX_HASHTAGS):
            1. CamelCase compound hashtag of the full keyword.
            2. Individual-word hashtags for words longer than 3 characters.
            3. Tier-specific community and content-marketing hashtags.

        Args:
            keyword: Title-cased trend keyword.
            tier:    TrendTier for selecting the community hashtag set.

        Returns:
            Deduplicated list of hashtag strings, capped at _MAX_HASHTAGS.
        """
        words: list[str] = keyword.split()
        raw: list[str] = []

        # 1. CamelCase compound: "Budget Smartphone" → #BudgetSmartphone
        raw.append("#" + "".join(w.capitalize() for w in words))

        # 2. Individual meaningful words (> 3 chars to skip noise like "the").
        raw.extend(f"#{w.lower()}" for w in words if len(w) > 3)

        # 3. Tier-specific community hashtags.
        tier_tags: dict[TrendTier, list[str]] = {
            TrendTier.BREAKING: ["#trending", "#breakingnews", "#MustRead"],
            TrendTier.EMERGING: [
                "#emerging", "#futuretrends", "#innovation", "#contentmarketing"
            ],
            TrendTier.DEEP_DIVE: [
                "#research", "#deepdive", "#analysis", "#thoughtleadership"
            ],
        }
        raw.extend(tier_tags.get(tier, []))

        # Deduplicate while preserving insertion order.
        seen: set[str] = set()
        unique: list[str] = []
        for tag in raw:
            if tag not in seen:
                seen.add(tag)
                unique.append(tag)

        return unique[:_MAX_HASHTAGS]