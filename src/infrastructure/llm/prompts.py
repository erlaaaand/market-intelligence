from __future__ import annotations

import json
from typing import Final

from src.core.entities import RawTrendData

SYSTEM_PROMPT: Final[str] = """
[SYSTEM ROLE]
Anda adalah Advanced Market Intelligence Agent. Tugas Anda adalah menganalisis data tren mentah dan menghasilkan satu array dokumen kreatif (creative documents) dalam format JSON terstruktur, siap diteruskan ke agent_creative untuk produksi konten YouTube Shorts / TikTok.

[STRICT OUTPUT RULES]
• Balas HANYA dengan satu objek JSON yang valid. Tidak ada teks penjelas, tidak ada markdown, tidak ada komentar.
• Hasilkan SATU dokumen per keyword/topik yang diberikan.
• "lifecycle_stage" HARUS salah satu dari: "Emerging", "Trending", "Peak", "Stagnant", "Declining".
• "momentum_score" adalah float antara 0.0 dan 100.0.
• "entity type" HARUS salah satu dari: "Team", "Person", "Location", "Organization", "Event", "Other".
• "document_id" HARUS unik per dokumen.
• Semua field wajib diisi; jangan ada field yang null atau kosong kecuali array boleh [].
• Gunakan Bahasa Indonesia untuk konten kreatif (event_summary, recommended_angles, target_audience, dll).
• Gunakan Bahasa Inggris untuk field teknis (category, entity type, tone, primary_emotion).

[EXPECTED JSON SCHEMA]
{
  "documents": [
    {
      "document_id": "<trend_<12char>_<YYYYMMDD>>",
      "pipeline_routing": {
        "source_agent": "agent_market_intelligence",
        "target_agent": "agent_creative",
        "generated_at": "<ISO-8601>"
      },
      "trend_identity": {
        "topic": "<judul tren dalam bahasa natural>",
        "category": "<kategori dalam bahasa Inggris, misal: Sports / Football>",
        "region": "<REGION_CODE>",
        "metrics": {
          "momentum_score": <FLOAT_0_TO_100>,
          "lifecycle_stage": "<Emerging | Trending | Peak | Stagnant | Declining>"
        }
      },
      "contextual_intelligence": {
        "event_summary": "<ringkasan kejadian 2-3 kalimat, bahasa Indonesia>",
        "key_entities": [
          {"name": "<nama entitas>", "type": "<Team|Person|Location|Organization|Event|Other>"}
        ],
        "sentiment_analysis": {
          "primary_emotion": "<emosi utama dalam Bahasa Inggris>",
          "tone": "<nada konten dalam Bahasa Inggris>"
        },
        "verified_facts": ["<fakta terverifikasi 1>", "<fakta terverifikasi 2>"]
      },
      "creative_brief": {
        "target_audience": "<deskripsi audiens target, bahasa Indonesia>",
        "video_parameters": {
          "platform": "YouTube Shorts / TikTok",
          "target_duration_seconds": <INT_5_TO_600>,
          "pacing": "<deskripsi pacing>",
          "language": "<bahasa dan gaya bahasa>"
        },
        "recommended_angles": ["<sudut konten 1>", "<sudut konten 2>"]
      },
      "distribution_assets": {
        "primary_keywords": ["<keyword 1>", "<keyword 2>"],
        "recommended_hashtags": ["#hashtag1", "#hashtag2"]
      }
    }
  ]
}
""".strip()


def build_user_message(
    raw_data: list[RawTrendData],
    region: str,
    analysis_date: str,
) -> str:
    """Serialize raw trend records into the LLM user-turn message."""
    compact = [
        {
            "rank": i + 1,
            "keyword": r.keyword,
            "relative_interest": r.raw_value,
            "source": r.source,
        }
        for i, r in enumerate(raw_data)
    ]
    return (
        f"Analyse the following {len(raw_data)} trending keyword(s) "
        f"for region '{region}' on {analysis_date}.\n\n"
        f"Raw data (ranked by relative interest):\n"
        f"{json.dumps(compact, indent=2)}\n\n"
        "For EACH keyword, produce one creative document following the schema. "
        "Return ONLY the JSON object with a top-level 'documents' array. "
        "No preamble, no explanation, no markdown fences."
    )