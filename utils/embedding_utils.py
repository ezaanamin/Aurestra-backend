"""
Embedding utilities for semantic search and knowledge base queries.
"""

from typing import List, Tuple
import numpy as np
from app.models import KnowledgeEmbedding


def decode_embedding_vector(embedding_str: str) -> np.ndarray:
    """Convert stored embedding string back to numpy array"""
    return np.array([float(x) for x in embedding_str.split(',')])


def cosine_similarity(vec1: np.ndarray, vec2: np.ndarray) -> float:
    """Calculate cosine similarity between two vectors"""
    dot_product = np.dot(vec1, vec2)
    norm_product = np.linalg.norm(vec1) * np.linalg.norm(vec2)
    
    if norm_product == 0:
        return 0.0
    
    return float(dot_product / norm_product)


def search_knowledge_embeddings(
    query_embedding: np.ndarray,
    top_k: int = 5,
    similarity_threshold: float = 0.3
) -> List[Tuple[KnowledgeEmbedding, float]]:
    """
    Search knowledge embeddings using semantic similarity.
    
    Args:
        query_embedding: Embedding vector of the query
        top_k: Return top K results
        similarity_threshold: Minimum similarity score to include
    
    Returns:
        List of (KnowledgeEmbedding, similarity_score) tuples
    """
    all_embeddings = KnowledgeEmbedding.query.all()
    
    if not all_embeddings:
        return []
    
    # Calculate similarity for all embeddings
    results = []
    for embedding_record in all_embeddings:
        stored_embedding = decode_embedding_vector(embedding_record.embedding_vector)
        similarity = cosine_similarity(query_embedding, stored_embedding)
        
        if similarity >= similarity_threshold:
            results.append((embedding_record, similarity))
    
    # Sort by similarity (descending) and return top K
    results.sort(key=lambda x: x[1], reverse=True)
    return results[:top_k]


def get_embedding_context(
    query_embedding: np.ndarray,
    top_k: int = 3
) -> str:
    """
    Get formatted context from top K similar embeddings for use in AI prompts.
    
    Args:
        query_embedding: Embedding vector of the query
        top_k: Number of top results to include
    
    Returns:
        Formatted string of relevant knowledge for AI context
    """
    results = search_knowledge_embeddings(query_embedding, top_k=top_k)
    
    if not results:
        return ""
    
    context_parts = []
    for embedding, similarity in results:
        context_parts.append(
            f"[{embedding.filename} (score: {similarity:.2f})]\n{embedding.content_preview}\n"
        )
    
    return "\n---\n".join(context_parts)
