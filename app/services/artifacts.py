"""Shared metadata for the two real delivery formats."""

import re
from dataclasses import dataclass
from typing import Literal

ArtifactKind = Literal["audio", "video"]


@dataclass(frozen=True)
class ArtifactSpec:
    stored_name: str
    extension: str
    media_type: str
    fallback_title: str
    force_attachment: bool = False


ARTIFACTS: dict[ArtifactKind, ArtifactSpec] = {
    "audio": ArtifactSpec("audio.mp3", ".mp3", "audio/mpeg", "音拾音频"),
    "video": ArtifactSpec("video.mp4", ".mp4", "video/mp4", "音拾视频", True),
}


def download_filename(title: str, kind: ArtifactKind = "audio") -> str:
    spec = ARTIFACTS[kind]
    # JSON may carry unpaired UTF-16 surrogates; normal emoji remain intact.
    title = title.encode("utf-16", errors="surrogatepass").decode("utf-16", errors="replace")
    clean = re.sub(r'[\x00-\x1f\x7f/\\:*?"<>|]', "_", title)
    clean = re.sub(r"\s+", " ", clean).strip(" ._")
    return (clean[:60] or spec.fallback_title) + spec.extension
