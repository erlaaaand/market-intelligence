"""
Backward-compatible re-export.

All implementation now lives in the google_trends sub-package:
  src/infrastructure/google_trends/constants.py   — constants & pure helpers
  src/infrastructure/google_trends/tier1_tier2.py  — Tier 1 & Tier 2 fetchers
  src/infrastructure/google_trends/tier3.py        — Tier 3 interest_over_time
  src/infrastructure/google_trends/adapter.py      — GoogleTrendsAdapter (orchestrator)
"""
from src.infrastructure.google_trends.adapter import GoogleTrendsAdapter

__all__ = ["GoogleTrendsAdapter"]