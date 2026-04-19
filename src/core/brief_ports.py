# src/core/brief_ports.py

"""
Abstract port definitions for the Content Brief Generator pipeline.

These ABCs define the interface contracts (the *what*) without any
implementation detail (the *how*). All concrete adapters in
`src/infrastructure/` implement these ports.

The application layer (`ContentBriefGeneratorUseCase`) depends exclusively
on these abstractions — never on infrastructure classes directly.
This enforces the Dependency Inversion Principle across all three ports.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from src.core.brief_entities import BriefBatch, ContentBrief
from src.core.entities import TrendTopic


class TrendReaderPort(ABC):
    """
    Input port: contract for reading processed TrendTopic entities from storage.

    Implementations translate serialised JSON files produced by the upstream
    TrendAnalyzerUseCase into validated TrendTopic domain entities.

    All implementations MUST raise only domain exceptions:
        TrendFileNotFoundError: When no matching files exist.
        TrendFileParseError:    When a file exists but cannot be deserialised.
    """

    @abstractmethod
    def list_available_files(self, region: str | None = None) -> list[str]:
        """
        Return a list of processable trend filenames, newest-first.

        Args:
            region: Optional ISO 3166-1 alpha-2 country code to filter by.
                    None means return all regions.

        Returns:
            Bare filenames (no directory prefix), sorted newest-first.

        Raises:
            TrendFileNotFoundError: If no matching files exist.
        """
        ...

    @abstractmethod
    def read_from_file(self, filename: str) -> list[TrendTopic]:
        """
        Deserialise a specific processed trend file into TrendTopic entities.

        Args:
            filename: Bare filename within the processed data directory.

        Returns:
            List of validated TrendTopic entities (may be empty).

        Raises:
            TrendFileNotFoundError: If the file does not exist.
            TrendFileParseError:    If the file is malformed or invalid.
        """
        ...

    @abstractmethod
    def read_latest(self, region: str | None = None) -> tuple[list[TrendTopic], str]:
        """
        Load the most recently modified processed trend file.

        Args:
            region: Optional ISO country code to filter by.

        Returns:
            A 2-tuple of (list of TrendTopic entities, bare source filename).

        Raises:
            TrendFileNotFoundError: If no matching files exist.
            TrendFileParseError:    If the file is malformed.
        """
        ...


class BriefGeneratorPort(ABC):
    """
    Processing port: contract for transforming a TrendTopic into a ContentBrief.

    Implementations may use rule engines, LLM APIs, or hybrid approaches
    internally, but must always return a fully validated ContentBrief entity.
    Swapping implementations (e.g. rule-based → GPT-backed) requires no
    changes to the use case or any other layer.

    All implementations MUST raise only domain exceptions:
        BriefGenerationError: If brief construction fails for any reason.
    """

    @abstractmethod
    def generate(self, topic: TrendTopic, source_file: str) -> ContentBrief:
        """
        Generate a structured content brief from a single trend topic.

        Args:
            topic:       Validated TrendTopic domain entity.
            source_file: Bare filename of the source trend data (for traceability).

        Returns:
            A fully populated, immutable ContentBrief entity.

        Raises:
            BriefGenerationError: If brief construction fails for any reason.
        """
        ...


class BriefStoragePort(ABC):
    """
    Output port: contract for persisting ContentBrief and BriefBatch entities.

    Implementations handle serialisation, directory management, and I/O errors
    transparently from the use case's perspective.

    All implementations MUST raise only domain exceptions:
        StorageError: If the underlying I/O operation fails.
    """

    @abstractmethod
    def save_brief(self, brief: ContentBrief) -> str:
        """
        Persist a single ContentBrief as a standalone JSON file.

        Args:
            brief: Validated ContentBrief entity.

        Returns:
            Bare filename of the saved file (without directory prefix).

        Raises:
            StorageError: If the write fails.
        """
        ...

    @abstractmethod
    def save_batch(self, batch: BriefBatch) -> str:
        """
        Persist a full BriefBatch as a single JSON file.

        Provides atomic access to the complete output of a pipeline run.

        Args:
            batch: BriefBatch entity containing all briefs for the run.

        Returns:
            Bare filename of the saved batch file.

        Raises:
            StorageError: If the write fails.
        """
        ...