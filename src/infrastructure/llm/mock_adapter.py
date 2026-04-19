from __future__ import annotations

import logging
import random
from datetime import datetime, timezone
from typing import Final

from src.core.entities import (
    ContextualIntelligence,
    CreativeBrief,
    CreativeDocument,
    CreativeDocumentBatch,
    DistributionAssets,
    EntityType,
    KeyEntity,
    LifecycleStage,
    PipelineRouting,
    RawTrendData,
    SentimentAnalysis,
    TrendIdentity,
    TrendIdentityMetrics,
    VideoParameters,
)
from src.core.ports import LLMPort

logger = logging.getLogger(__name__)

# ── Static data pools ─────────────────────────────────────────────────────────

_MOCK_CATEGORIES: Final[list[str]] = [
    "Sports / Football", "Technology / AI", "Entertainment / Music",
    "Politics / Government", "Health / Wellness", "Finance / Crypto",
    "Science / Space", "Culture / Viral", "Business / Economy",
    "Gaming / Esports",
]

_MOCK_SUMMARIES: Final[list[str]] = [
    "Topik ini viral setelah sebuah kejadian mengejutkan menarik perhatian jutaan pengguna media sosial dalam waktu singkat.",
    "Berita terbaru memicu gelombang diskusi publik yang besar, dengan berbagai pihak memberikan reaksi berbeda-beda.",
    "Sebuah momen langka terjadi dan langsung menjadi perbincangan hangat di berbagai platform digital.",
    "Tren ini dipicu oleh pengumuman resmi yang mengubah dinamika industri dan mendapat respons luas dari masyarakat.",
    "Insiden yang tidak terduga ini memicu reaksi berantai di media sosial dan menjadi topik terpanas hari ini.",
]

_MOCK_ENTITIES: Final[list[list[dict[str, str]]]] = [
    [{"name": "Public Figure A", "type": "Person"}, {"name": "Event Location", "type": "Location"}],
    [{"name": "Company X", "type": "Organization"}, {"name": "Industry Leader B", "type": "Person"}],
    [{"name": "Team Alpha", "type": "Team"}, {"name": "Team Beta", "type": "Team"}],
    [{"name": "Government Agency", "type": "Organization"}, {"name": "Capital City", "type": "Location"}],
    [{"name": "Tech Product", "type": "Other"}, {"name": "CEO C", "type": "Person"}],
]

_MOCK_EMOTIONS: Final[list[dict[str, str]]] = [
    {"primary_emotion": "Shock / Excitement", "tone": "Dramatic"},
    {"primary_emotion": "Curiosity / Interest", "tone": "Informative"},
    {"primary_emotion": "Pride / Celebration", "tone": "Positive"},
    {"primary_emotion": "Frustration / Concern", "tone": "Critical"},
    {"primary_emotion": "Anticipation / Hype", "tone": "Energetic"},
]

_MOCK_FACTS: Final[list[list[str]]] = [
    ["Kejadian ini pertama kali terjadi dalam 5 tahun terakhir.", "Lebih dari 1 juta orang membicarakannya dalam 24 jam."],
    ["Data resmi baru dirilis kemarin pukul 10.00 WIB.", "Ini merupakan rekor tertinggi yang pernah tercatat."],
    ["Peristiwa berlangsung selama kurang dari 2 jam namun dampaknya masif.", "Pihak berwenang belum mengeluarkan pernyataan resmi."],
    ["Tren ini naik 300% dibanding minggu lalu berdasarkan data pencarian.", "Mayoritas diskusi berasal dari pengguna usia 18-35 tahun."],
]

_MOCK_AUDIENCES: Final[list[str]] = [
    "Penggemar sepak bola Indonesia usia 15-35 tahun.",
    "Pengguna teknologi dan gadget usia 20-40 tahun.",
    "Penggemar hiburan dan pop culture usia 18-30 tahun.",
    "Investor dan pengamat ekonomi usia 25-45 tahun.",
    "Gamer dan komunitas esports usia 16-28 tahun.",
]

_MOCK_PACINGS: Final[list[str]] = [
    "Fast-paced (ganti scene setiap 3-4 detik)",
    "Medium-paced (ganti scene setiap 5-6 detik)",
    "Dynamic cut dengan beat musik",
    "Talking-head dengan B-roll cepat",
]

_MOCK_ANGLES: Final[list[list[str]]] = [
    ["Reaksi publik dan momen viral yang paling mengejutkan.", "Hitung-hitungan dampak ke depan: siapa yang paling diuntungkan?"],
    ["Behind the scenes: fakta yang belum banyak diketahui orang.", "Perbandingan dengan kejadian serupa di masa lalu."],
    ["Momen terbaik dan highlight utama yang wajib ditonton.", "Opini para ahli dan komentar tokoh berpengaruh."],
    ["Dampak langsung bagi masyarakat umum di Indonesia.", "Prediksi: apa yang akan terjadi selanjutnya?"],
    ["Kontroversi di balik berita: dua sisi yang berbeda.", "Tips dan pelajaran yang bisa diambil dari kejadian ini."],
]

_MOCK_KEYWORDS: Final[list[list[str]]] = [
    ["berita viral hari ini", "trending Indonesia", "breaking news"],
    ["update terbaru", "berita terkini", "highlight terbaik"],
    ["analisis mendalam", "fakta mengejutkan", "kronologi lengkap"],
    ["reaksi netizen", "viral media sosial", "heboh di Twitter"],
]

_MOCK_HASHTAGS: Final[list[list[str]]] = [
    ["#Trending", "#BeritaViral", "#Indonesia", "#TiktokIndonesia"],
    ["#BreakingNews", "#Viral", "#UpdateTerbaru", "#Shorts"],
    ["#Highlights", "#MustWatch", "#HotTopik", "#BeritaHariIni"],
    ["#Analisis", "#FaktaMenarik", "#Terkini", "#ContentCreator"],
]


# ── Adapter ───────────────────────────────────────────────────────────────────

class MockLLMAdapter(LLMPort):
    def __init__(self, inject_anomaly_probability: float = 0.3) -> None:
        # anomaly_probability kept for API compatibility, not used in new schema
        if not 0.0 <= inject_anomaly_probability <= 1.0:
            raise ValueError("inject_anomaly_probability must be in [0, 1]")
        self._anomaly_prob = inject_anomaly_probability

    def analyze_trends(
        self,
        raw_data: list[RawTrendData],
        region: str,
        analysis_date: str,
    ) -> CreativeDocumentBatch:
        logger.info(
            "MockLLMAdapter.analyze_trends  region='%s'  records=%d",
            region,
            len(raw_data),
        )
        documents = [
            self._build_mock_document(record, i, region, analysis_date)
            for i, record in enumerate(raw_data)
        ]
        return CreativeDocumentBatch(
            region=region,
            date=analysis_date,
            documents=documents,
        )

    def _build_mock_document(
        self,
        record: RawTrendData,
        index: int,
        region: str,
        analysis_date: str,
    ) -> CreativeDocument:
        v = record.raw_value
        momentum = min(100.0, round(v * 0.9 + (index % 5) * 2.0, 2))
        lifecycle = _lifecycle_from_value(v)

        rng = random.Random(f"{region}:{record.keyword}:{analysis_date}:{index}")
        i = index  # shorthand for pool cycling

        # Build key_entities
        raw_entities = _MOCK_ENTITIES[i % len(_MOCK_ENTITIES)]
        key_entities = [
            KeyEntity(
                name=e["name"],
                type=EntityType(e["type"]),
            )
            for e in raw_entities
        ]

        emotion_data = _MOCK_EMOTIONS[i % len(_MOCK_EMOTIONS)]

        return CreativeDocument(
            document_id=CreativeDocument.make_document_id(region, record.keyword, analysis_date),
            pipeline_routing=PipelineRouting(
                generated_at=datetime.now(tz=timezone.utc),
            ),
            trend_identity=TrendIdentity(
                topic=record.keyword,
                category=_MOCK_CATEGORIES[i % len(_MOCK_CATEGORIES)],
                region=region,
                metrics=TrendIdentityMetrics(
                    momentum_score=momentum,
                    lifecycle_stage=lifecycle,
                ),
            ),
            contextual_intelligence=ContextualIntelligence(
                event_summary=_MOCK_SUMMARIES[i % len(_MOCK_SUMMARIES)],
                key_entities=key_entities,
                sentiment_analysis=SentimentAnalysis(
                    primary_emotion=emotion_data["primary_emotion"],
                    tone=emotion_data["tone"],
                ),
                verified_facts=list(_MOCK_FACTS[i % len(_MOCK_FACTS)]),
            ),
            creative_brief=CreativeBrief(
                target_audience=_MOCK_AUDIENCES[i % len(_MOCK_AUDIENCES)],
                video_parameters=VideoParameters(
                    platform="YouTube Shorts / TikTok",
                    target_duration_seconds=60,
                    pacing=_MOCK_PACINGS[i % len(_MOCK_PACINGS)],
                    language="Indonesian (Gaya bahasa santai/gaul)",
                ),
                recommended_angles=list(_MOCK_ANGLES[i % len(_MOCK_ANGLES)]),
            ),
            distribution_assets=DistributionAssets(
                primary_keywords=list(_MOCK_KEYWORDS[i % len(_MOCK_KEYWORDS)]),
                recommended_hashtags=list(_MOCK_HASHTAGS[i % len(_MOCK_HASHTAGS)]),
            ),
        )


def _lifecycle_from_value(raw_value: int) -> LifecycleStage:
    if raw_value >= 80:
        return LifecycleStage.PEAK
    if raw_value >= 60:
        return LifecycleStage.TRENDING
    if raw_value >= 40:
        return LifecycleStage.EMERGING
    if raw_value >= 20:
        return LifecycleStage.STAGNANT
    return LifecycleStage.DECLINING