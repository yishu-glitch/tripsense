"""Layered knowledge system for TripSense."""

from .contracts import (
    CandidateContext,
    Freshness,
    KnowledgeEvidence,
    RealtimeNotice,
    RealtimeSnapshot,
    StableKnowledgeDocument,
    StructuredPOI,
)
from .hierarchy import (
    SiteDefinition,
    SiteRelationCandidate,
    discover_embedded_parent_relations,
    discover_site_relations,
    merge_relation_candidates,
    write_product_hierarchy,
)
from .realtime import (
    AmapWeatherProvider,
    RealtimeProvider,
    StaticRealtimeProvider,
    UnavailableRealtimeProvider,
    realtime_provider_from_env,
)
from .retrieval import LayeredKnowledgeRetriever
from .scope import SHANGHAI_DISTRICT_CODES, split_shanghai_scope
from .stable import LexicalStableKnowledgeStore
from .structured import StructuredPOIRepository

__all__ = [
    "SHANGHAI_DISTRICT_CODES",
    "AmapWeatherProvider",
    "CandidateContext",
    "Freshness",
    "KnowledgeEvidence",
    "LayeredKnowledgeRetriever",
    "LexicalStableKnowledgeStore",
    "RealtimeNotice",
    "RealtimeProvider",
    "RealtimeSnapshot",
    "SiteDefinition",
    "SiteRelationCandidate",
    "StableKnowledgeDocument",
    "StaticRealtimeProvider",
    "StructuredPOI",
    "StructuredPOIRepository",
    "UnavailableRealtimeProvider",
    "discover_embedded_parent_relations",
    "discover_site_relations",
    "merge_relation_candidates",
    "realtime_provider_from_env",
    "split_shanghai_scope",
    "write_product_hierarchy",
]
