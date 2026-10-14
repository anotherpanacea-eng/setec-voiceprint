#!/usr/bin/env python3
"""Voice-coherence acquisition library surface.

The implementation lives in ``setec.core.acquisition_core`` (CB-5); this module
re-exports it as the same objects and keeps the surface tag and
``pdf_text_from_bytes``, which imports the ``pdf_extract`` surface.
"""

from __future__ import annotations

import sys
from setec.paths import scripts_dir
from pathlib import Path

from setec.core.acquisition_core import (  # noqa: F401 -- re-exported for existing importers
    _filesystem_path,
    _ISO_DATE_RE,
    _prestrip_html,
    _SLUG_NON_ALNUM_RE,
    _SLUG_TRIM_RE,
    _trafilatura_extract,
    _unique_stem,
    _write_bytes_atomic,
    _write_text_atomic,
    AcquiredPiece,
    add_allow_empty_arg,
    add_user_agent_arg,
    append_manifest_entry,
    author_to_persona_slug,
    check_output_privacy,
    compose_manifest_entry,
    compute_content_hash,
    content_hash_already_present,
    DEFAULT_BASELINES_ENV,
    DEFAULT_BASELINES_FALLBACK,
    default_output_dir,
    DEFAULT_USER_AGENT,
    era_from_date,
    extract_main_content,
    Fetcher,
    FetchResult,
    FixtureFetcher,
    html_text_is_clean,
    html_to_text,
    is_private_safe_path,
    make_requests_fetcher,
    normalize_extracted_whitespace,
    parse_iso_date,
    preprocess_text,
    PRIVATE_DIR_NAME,
    resolve_baselines_dir,
    RunSummary,
    slugify,
    StableRedactionMap,
    write_piece,
)


# Task-surface tag. Acquisition is upstream of every voice-coherence
# tool that consumes the impostor pool, so it gets its own surface.
TASK_SURFACE = "voice_coherence_acquisition"


def pdf_text_from_bytes(
    data: bytes, *, artifact_profile: str | None = None,
) -> str:
    """Extract text from PDF bytes via the existing ``pdf_extract`` text-layer
    extractor (pypdf).

    ``pdf_extract.extract_text_layer`` takes a path, so the bytes are written
    to a temp file. Returns the extracted text, or ``""`` on empty / image-only
    / unparseable input (the caller treats ``""`` as a skip). OCR is out of
    scope here — image-only PDFs return ``""``; the operator runs the dedicated
    ``pdf_extract.py`` OCR pass for those.

    ``artifact_profile`` is an explicit source contract.  The default leaves
    extracted text untouched; callers may opt into a named, source-bounded
    repair profile such as ``"tanner"``.

    The PDF acquisition path (``acquire_pdf_urls.py``) calls this on bytes
    fetched via ``Fetcher.fetch_bytes``.
    """
    if not data:
        return ""
    import os
    import tempfile
    sys.path.insert(0, str(scripts_dir()))
    try:
        import pdf_extract  # type: ignore
    finally:
        sys.path.pop(0)
    tmp_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tf:
            tf.write(data)
            tmp_path = tf.name
        return pdf_extract.extract_text_layer(
            Path(tmp_path), artifact_profile=artifact_profile,
        ) or ""
    except Exception:
        return ""
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
