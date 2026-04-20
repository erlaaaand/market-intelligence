from __future__ import annotations

import json
from typing import Final

from src.core.entities import RawTrendData

SYSTEM_PROMPT: Final[str] = """
[SYSTEM ROLE]
Anda adalah Advanced Market Intelligence Agent. Tugas Anda adalah menganalisis data tren mentah beserta konteks berita real-time yang disertakan, lalu menghasilkan satu array dokumen kreatif (creative documents) dalam format JSON terstruktur.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
⚠️  ANTI-HALLUCINATION RULES — WAJIB DIPATUHI
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

RULE 1 — STRICTLY USE THE PROVIDED CONTEXT:
Setiap keyword dilengkapi blok [REAL-TIME CONTEXT]. Anda WAJIB membaca dan menggunakan
isi blok tersebut untuk mengisi event_summary, key_entities, dan verified_facts.
DILARANG KERAS mengarang atau menebak informasi yang tidak ada di dalam blok konteks.

RULE 2 — DO NOT HALLUCINATE CATEGORIES OR FACTS:
JIKA konteks berita menyebut "Batu Bara" atau "Coal Mining", JANGAN tulis "Oil & Gas".
JIKA konteks berita menyebut "Musik" atau "Penyanyi", JANGAN tulis "Atlet" atau "Olahraga".
SELALU ikuti kategori yang TERCERMIN dari isi berita, bukan tebakan dari nama keyword saja.

RULE 3 — verified_facts MUST BE CONCRETE:
"verified_facts" HARUS berisi fakta spesifik yang bisa ditelusuri dari snippet berita
(nama orang nyata, angka, tanggal, nama perusahaan/tim/lokasi).
DILARANG mengisi verified_facts dengan kalimat generik seperti:
  ❌ "Topik ini sedang viral di media sosial."
  ❌ "Banyak orang membicarakan hal ini."
  ❌ "Data real-time tidak tersedia untuk topik ini."
Jika benar-benar tidak ada konteks, tulis: "Tidak ada berita spesifik yang tersedia; analisis berdasarkan pengetahuan umum."

RULE 4 — event_summary MUST REFLECT ACTUAL NEWS:
event_summary HARUS menceritakan KEJADIAN NYATA yang ada di snippet berita, bukan
deskripsi umum tentang topik tersebut. Sertakan nama, waktu, atau detail spesifik
dari berita yang diberikan.

RULE 5 — DO NOT INVENT ENTITIES:
key_entities HARUS berisi nama yang MUNCUL di snippet berita.
JANGAN mengarang nama tokoh, tim, atau organisasi yang tidak disebutkan dalam konteks.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📋  OUTPUT RULES
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

• Balas HANYA dengan satu objek JSON yang valid. Tidak ada teks penjelas, tidak ada markdown, tidak ada komentar.
• Hasilkan SATU dokumen per keyword/topik yang diberikan.
• "lifecycle_stage" HARUS salah satu dari: "Emerging", "Trending", "Peak", "Stagnant", "Declining".
• "momentum_score" adalah float antara 0.0 dan 100.0.
• "entity type" HARUS salah satu dari: "Team", "Person", "Location", "Organization", "Event", "Other".
• "document_id" HARUS unik per dokumen.
• Semua field wajib diisi; jangan ada field yang null atau kosong kecuali array boleh [].
• Gunakan Bahasa Indonesia untuk konten kreatif (event_summary, recommended_angles, target_audience, dll).
• Gunakan Bahasa Inggris untuk field teknis (category, entity type, tone, primary_emotion).

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📐  EXPECTED JSON SCHEMA
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

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
        "category": "<kategori dalam Bahasa Inggris SESUAI ISI BERITA, misal: Sports / Football>",
        "region": "<REGION_CODE>",
        "metrics": {
          "momentum_score": <FLOAT_0_TO_100>,
          "lifecycle_stage": "<Emerging | Trending | Peak | Stagnant | Declining>"
        }
      },
      "contextual_intelligence": {
        "event_summary": "<ringkasan KEJADIAN NYATA 2-3 kalimat dari berita, bahasa Indonesia — BUKAN deskripsi generik>",
        "key_entities": [
          {"name": "<nama NYATA dari berita>", "type": "<Team|Person|Location|Organization|Event|Other>"}
        ],
        "sentiment_analysis": {
          "primary_emotion": "<emosi utama dalam Bahasa Inggris>",
          "tone": "<nada konten dalam Bahasa Inggris>"
        },
        "verified_facts": [
          "<fakta konkret & spesifik dari snippet berita 1>",
          "<fakta konkret & spesifik dari snippet berita 2>"
        ]
      },
      "creative_brief": {
        "target_audience": "<deskripsi audiens target, bahasa Indonesia>",
        "video_parameters": {
          "platform": "YouTube Shorts / TikTok",
          "target_duration_seconds": <INT_5_TO_600>,
          "pacing": "<deskripsi pacing>",
          "language": "<bahasa dan gaya bahasa>"
        },
        "recommended_angles": [
          "<sudut konten RELEVAN dengan berita aktual 1>",
          "<sudut konten RELEVAN dengan berita aktual 2>"
        ]
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
    snippets_map: dict[str, list[dict[str, str]]] | None = None,
) -> str:
    """
    Bangun user-turn message untuk Ollama.

    snippets_map: dict[keyword_asli → list[{title, body, url}]]
    Jika None atau {}, blok konteks tidak disertakan.
    """
    snippets_map = snippets_map or {}
    has_any_snippet = bool(snippets_map)

    enriched_entries: list[str] = []
    keywords_with_context = 0
    keywords_without_context = 0

    for i, r in enumerate(raw_data):
        snippets = snippets_map.get(r.keyword, [])  # lookup pakai keyword ASLI
        has_snippet = bool(snippets)

        entry_lines = [
            f"{'=' * 60}",
            f"KEYWORD #{i + 1}: {r.keyword}",
            f"Relative Interest : {r.raw_value}/100",
            f"Source            : {r.source}",
            f"{'=' * 60}",
        ]

        if has_snippet:
            keywords_with_context += 1
            entry_lines.append(
                "⬇ [REAL-TIME CONTEXT — WAJIB DIGUNAKAN untuk event_summary, "
                "key_entities, dan verified_facts]"
            )
            for j, snip in enumerate(snippets, start=1):
                title = snip.get("title", "").strip()
                body  = snip.get("body",  "").strip()
                url   = snip.get("url",   "").strip()

                entry_lines.append(f"\n  Berita [{j}]:")
                if title:
                    entry_lines.append(f"    Judul  : {title}")
                if body:
                    # Potong jika terlalu panjang agar tidak membengkakkan prompt
                    entry_lines.append(f"    Isi    : {body[:400]}")
                if url:
                    entry_lines.append(f"    Sumber : {url}")

            entry_lines.append(
                "\n⚠ INGAT: Gunakan nama, fakta, dan detail DARI BERITA DI ATAS. "
                "JANGAN mengarang fakta yang tidak ada di sini."
            )
        else:
            keywords_without_context += 1
            entry_lines.append(
                "⚠ [TIDAK ADA KONTEKS REAL-TIME] — Gunakan pengetahuan umum model, "
                "dan isi verified_facts dengan: "
                '"Tidak ada berita spesifik yang tersedia; analisis berdasarkan pengetahuan umum."'
            )

        enriched_entries.append("\n".join(entry_lines))

    keywords_block = "\n\n".join(enriched_entries)

    # Ringkasan RAG untuk header pesan
    if has_any_snippet:
        rag_summary = (
            f"✅ Konteks web tersedia untuk {keywords_with_context}/{len(raw_data)} keyword. "
            f"Gunakan blok [REAL-TIME CONTEXT] untuk grounding.\n"
            f"{'⚠ ' + str(keywords_without_context) + ' keyword tidak punya konteks web.' if keywords_without_context else ''}"
        ).strip()
    else:
        rag_summary = (
            "⚠ TIDAK ADA konteks web real-time untuk batch ini. "
            "Gunakan pengetahuan umum model, dan tandai semua verified_facts "
            'dengan: "Tidak ada berita spesifik yang tersedia; analisis berdasarkan pengetahuan umum."'
        )

    return (
        f"Analisis {len(raw_data)} keyword tren berikut untuk region '{region}' "
        f"tanggal {analysis_date}.\n\n"
        f"{rag_summary}\n\n"
        f"DATA KEYWORD DAN KONTEKS BERITA:\n\n"
        f"{keywords_block}\n\n"
        "INSTRUKSI OUTPUT:\n"
        "Untuk SETIAP keyword di atas, buat SATU dokumen kreatif mengikuti JSON schema.\n"
        "Kembalikan HANYA objek JSON dengan top-level key 'documents' berisi array.\n"
        "JANGAN tambahkan preamble, penjelasan, atau markdown fence."
    )