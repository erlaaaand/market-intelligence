"""
Backward-compatible re-export.

All implementation now lives in the llm sub-package:
  src/infrastructure/llm/prompts.py        — system prompt & user message builder
  src/infrastructure/llm/parser.py         — response parser & validator
  src/infrastructure/llm/ollama_adapter.py — OllamaLLMAdapter (HTTP streaming)
  src/infrastructure/llm/mock_adapter.py   — MockLLMAdapter + static data pools
"""
from src.infrastructure.llm.ollama_adapter import OllamaLLMAdapter
from src.infrastructure.llm.mock_adapter import MockLLMAdapter

__all__ = ["OllamaLLMAdapter", "MockLLMAdapter"]