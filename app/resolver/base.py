from abc import ABC, abstractmethod
from dataclasses import dataclass


class ResolverNotConfigured(RuntimeError):
    """The selected platform has no configured parsing provider."""


@dataclass(frozen=True)
class ResolvedVideo:
    video_url: str
    title: str = ""
    author: str = ""
    # Only use this when the provider confirms it is the complete original soundtrack.
    original_audio_url: str | None = None


class MediaResolver(ABC):
    platform: str
    display_name: str
    available: bool = False

    @abstractmethod
    async def resolve(self, url: str) -> ResolvedVideo:
        """Resolve a validated platform URL to downloadable media."""
