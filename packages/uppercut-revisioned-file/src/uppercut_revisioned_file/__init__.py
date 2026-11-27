"""Atomic file writes and a whole-file store with opaque revisions."""

from ._atomic import write_bytes, write_json, write_text
from ._revisioned import FileTooLarge, RevisionConflict, RevisionedFile, revision_of

__all__ = [
    "FileTooLarge",
    "RevisionConflict",
    "RevisionedFile",
    "revision_of",
    "write_bytes",
    "write_json",
    "write_text",
]
__version__ = "1.0.0"
