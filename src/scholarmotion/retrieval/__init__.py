"""Source ingestion (PDFs, web pages) and retrieval for grounding lessons."""

from .hybrid_retrieval import HybridRetriever, RetrievedChunk
from .paper_ingestion import PaperParser

__all__ = ["HybridRetriever", "PaperParser", "RetrievedChunk"]
from .qdrant_retrieval import QdrantHybridRetriever

__all__ = ["QdrantHybridRetriever"]
