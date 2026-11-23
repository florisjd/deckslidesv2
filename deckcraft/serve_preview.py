"""Serve the review workspace on an existing data folder: uv run --package deckcraft-core python serve_preview.py [data_dir]"""
import pathlib, sys, uvicorn
from deckcraft_core.api.app import create_app
from deckcraft_core.settings import Settings

root = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "out/team-guide-s1").resolve()
uvicorn.run(create_app(Settings(root=root)), host="127.0.0.1", port=8765, log_level="warning")
