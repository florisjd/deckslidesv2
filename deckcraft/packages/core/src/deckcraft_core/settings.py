from __future__ import annotations
import os
from dataclasses import dataclass, field
from pathlib import Path
from platformdirs import user_data_dir


@dataclass(frozen=True)
class Settings:
    """All paths are injected so tests never touch real user data (plan P13)."""
    root: Path = field(default_factory=lambda: Path(user_data_dir("DeckCraft", appauthor=False)))
    ollama_url: str = "http://127.0.0.1:11434"
    llm_model: str = "gemma3:4b"
    max_upload_mb: int = 50
    model_dir: Path = field(default_factory=lambda: Path(os.environ.get("DECKCRAFT_MODELS", Path(__file__).resolve().parents[4] / "models")))
    theme: str = "emerald-editorial"
    voice: str = "af_heart"
    music_path: Path | None = None         # optional music bed (user-supplied file)
    enable_translation: bool = False       # feature flag (plan 18: multilingual later)
    pptx_rasteriser: str = "auto"          # auto | libreoffice | powerpoint | fallback
    render_workers: int = 3                # parallel Chromium workers in RenderPool

    @property
    def db_path(self) -> Path: return self.root / "library.db"
    @property
    def export_dir(self) -> Path: return self.root / "exports"
    @property
    def blob_dir(self) -> Path: return self.root / "blobs"
