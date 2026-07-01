"""Resume adapter with complementary extraction strategies."""

from app.sources.resume.adapter import ResumeAdapter
from app.sources.resume.extractors import (
    GlinerExtractor,
    ProseExtractor,
    ResumeExtractor,
    SectionExtractor,
)
from app.sources.resume.gliner import (
    _dedupe_entities,
    _filter_redundant_ner_claims,
    _load_gliner_model,
    _predict_entities,
    _split_gliner_chunks,
    preload_gliner_model,
)

__all__ = [
    "GlinerExtractor",
    "ProseExtractor",
    "ResumeAdapter",
    "ResumeExtractor",
    "SectionExtractor",
    "_dedupe_entities",
    "_filter_redundant_ner_claims",
    "_load_gliner_model",
    "_predict_entities",
    "_split_gliner_chunks",
    "preload_gliner_model",
]
