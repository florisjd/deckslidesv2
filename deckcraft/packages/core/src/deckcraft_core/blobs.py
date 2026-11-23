"""Content-addressed immutable blob store: blobs/sha256/ab/cd<rest>."""
from __future__ import annotations
import hashlib, os, tempfile
from pathlib import Path


class BlobStore:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def path_for(self, sha: str) -> Path:
        return self.root / "sha256" / sha[:2] / sha[2:]

    def put(self, data: bytes) -> str:
        sha = hashlib.sha256(data).hexdigest()
        p = self.path_for(sha)
        if not p.exists():
            p.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=p.parent)
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            os.replace(tmp, p)  # atomic
        return sha

    def get(self, sha: str) -> bytes:
        return self.path_for(sha).read_bytes()

    def has(self, sha: str) -> bool:
        return self.path_for(sha).exists()
