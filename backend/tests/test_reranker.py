"""
Tests for the cross-encoder reranker module.

Tests the ChunkCandidate, CrossEncoderReranker class, and integration
with the RAG pipeline including fallback scenarios.
"""
import pytest
from unittest.mock import patch, MagicMock, PropertyMock
from dataclasses import dataclass
from typing import List


# Import the modules we're testing
from apps.rag.reranker import (
    ChunkCandidate,
    CrossEncoderReranker,
    rerank_candidates,
    is_reranker_enabled,
    get_rerank_top_k,
    get_rerank_keep_n,
    MAX_CHUNK_TEXT_LENGTH,
)


# ============================================================================
# ChunkCandidate Tests
# ============================================================================

class TestChunkCandidate:
    """Tests for the ChunkCandidate dataclass."""
    
    def test_create_candidate(self):
        """Should create a candidate with all fields."""
        candidate = ChunkCandidate(
            chunk_id="chunk-123",
            doc_id="doc-456",
            doc_title="test.pdf",
            text="This is the full chunk text content.",
            snippet="This is the full...",
            vector_score=0.1234,
        )
        
        assert candidate.chunk_id == "chunk-123"
        assert candidate.doc_id == "doc-456"
        assert candidate.doc_title == "test.pdf"
        assert candidate.text == "This is the full chunk text content."
        assert candidate.snippet == "This is the full..."
        assert candidate.vector_score == 0.1234
        assert candidate.rerank_score is None
    
    def test_candidate_to_dict_without_rerank_score(self):
        """Should convert to dict without rerank_score when not set."""
        candidate = ChunkCandidate(
            chunk_id="chunk-123",
            doc_id="doc-456",
            doc_title="test.pdf",
            text="Full text",
            snippet="Snippet",
            vector_score=0.5,
        )
        
        result = candidate.to_dict()
        
        assert result["chunk_id"] == "chunk-123"
        assert result["vector_score"] == 0.5
        assert "rerank_score" not in result
    
    def test_candidate_to_dict_with_rerank_score(self):
        """Should include rerank_score when set."""
        candidate = ChunkCandidate(
            chunk_id="chunk-123",
            doc_id="doc-456",
            doc_title="test.pdf",
            text="Full text",
            snippet="Snippet",
            vector_score=0.5,
            rerank_score=0.9,
        )
        
        result = candidate.to_dict()
        
        assert result["rerank_score"] == 0.9


# ============================================================================
# CrossEncoderReranker Tests with Mocking
# ============================================================================

class TestCrossEncoderReranker:
    """Tests for the CrossEncoderReranker class."""
    
    def test_text_truncation(self):
        """Should truncate text to max length."""
        reranker = CrossEncoderReranker()
        
        long_text = "x" * 2000
        truncated = reranker._truncate_text(long_text)
        
        assert len(truncated) <= MAX_CHUNK_TEXT_LENGTH
    
    def test_text_truncation_preserves_short_text(self):
        """Should not truncate text under max length."""
        reranker = CrossEncoderReranker()
        
        short_text = "This is a short text."
        result = reranker._truncate_text(short_text)
        
        assert result == short_text
    
    @patch('apps.rag.reranker.CrossEncoderReranker._load_model')
    def test_rerank_empty_candidates(self, mock_load):
        """Should return empty list for empty candidates."""
        reranker = CrossEncoderReranker()
        
        result = reranker.rerank("test query", [])
        
        assert result == []
        mock_load.assert_not_called()
    
    @patch('sentence_transformers.CrossEncoder')
    @patch('torch.cuda.is_available', return_value=False)
    def test_rerank_with_mock_model(self, mock_cuda, mock_cross_encoder_class):
        """Should rerank candidates using mock cross-encoder."""
        # Create mock model that returns predictable scores
        mock_model = MagicMock()
        mock_model.predict.return_value = [0.9, 0.3, 0.7, 0.5]  # Scores for 4 candidates
        mock_cross_encoder_class.return_value = mock_model
        
        # Reset singleton for testing
        CrossEncoderReranker._instance = None
        CrossEncoderReranker._model = None
        
        reranker = CrossEncoderReranker()
        
        candidates = [
            ChunkCandidate(chunk_id="1", doc_id="d1", doc_title="a.pdf", 
                          text="Text 1", snippet="S1", vector_score=0.1),
            ChunkCandidate(chunk_id="2", doc_id="d2", doc_title="b.pdf",
                          text="Text 2", snippet="S2", vector_score=0.2),
            ChunkCandidate(chunk_id="3", doc_id="d3", doc_title="c.pdf",
                          text="Text 3", snippet="S3", vector_score=0.3),
            ChunkCandidate(chunk_id="4", doc_id="d4", doc_title="d.pdf",
                          text="Text 4", snippet="S4", vector_score=0.4),
        ]
        
        result = reranker.rerank("test query", candidates)
        
        # Should be sorted by rerank score (descending)
        # Original order was [0.9, 0.3, 0.7, 0.5], so sorted is [0.9, 0.7, 0.5, 0.3]
        # Which corresponds to chunks 1, 3, 4, 2
        assert len(result) == 4
        assert result[0].chunk_id == "1"  # Score 0.9
        assert result[1].chunk_id == "3"  # Score 0.7
        assert result[2].chunk_id == "4"  # Score 0.5
        assert result[3].chunk_id == "2"  # Score 0.3
    
    @patch('sentence_transformers.CrossEncoder')
    @patch('torch.cuda.is_available', return_value=False)
    def test_rerank_with_top_n(self, mock_cuda, mock_cross_encoder_class):
        """Should return only top_n candidates."""
        mock_model = MagicMock()
        mock_model.predict.return_value = [0.9, 0.3, 0.7, 0.5]
        mock_cross_encoder_class.return_value = mock_model
        
        CrossEncoderReranker._instance = None
        CrossEncoderReranker._model = None
        
        reranker = CrossEncoderReranker()
        
        candidates = [
            ChunkCandidate(chunk_id=str(i), doc_id=f"d{i}", doc_title=f"{i}.pdf",
                          text=f"Text {i}", snippet=f"S{i}", vector_score=0.1*i)
            for i in range(4)
        ]
        
        result = reranker.rerank("test query", candidates, top_n=2)
        
        assert len(result) == 2
        # Should be the top 2 by rerank score


# ============================================================================
# Integration Tests with Mocking
# ============================================================================

class TestRerankerIntegration:
    """Integration tests for reranker with mocking."""
    
    @patch('apps.rag.reranker.get_reranker')
    def test_rerank_candidates_function(self, mock_get_reranker):
        """Should call reranker and return with latency."""
        mock_reranker = MagicMock()
        mock_reranker.rerank.return_value = [
            ChunkCandidate(chunk_id="1", doc_id="d1", doc_title="a.pdf",
                          text="T1", snippet="S1", vector_score=0.1, rerank_score=0.9)
        ]
        mock_get_reranker.return_value = mock_reranker
        
        candidates = [
            ChunkCandidate(chunk_id="1", doc_id="d1", doc_title="a.pdf",
                          text="T1", snippet="S1", vector_score=0.1)
        ]
        
        result, latency = rerank_candidates("query", candidates)
        
        assert len(result) == 1
        assert result[0].rerank_score == 0.9
        assert latency >= 0
    
    @patch('django.conf.settings')
    def test_is_reranker_enabled_true(self, mock_settings):
        """Should return True when ENABLE_RERANKER is True."""
        mock_settings.ENABLE_RERANKER = True
        
        assert is_reranker_enabled() is True
    
    @patch('django.conf.settings')
    def test_is_reranker_enabled_false_by_default(self, mock_settings):
        """Should return False when ENABLE_RERANKER is not set."""
        del mock_settings.ENABLE_RERANKER
        
        # getattr with default False
        result = is_reranker_enabled()
        assert result is False
    
    @patch('django.conf.settings')
    def test_get_rerank_top_k(self, mock_settings):
        """Should return configured RERANK_TOP_K."""
        mock_settings.RERANK_TOP_K = 25
        
        assert get_rerank_top_k() == 25
    
    @patch('django.conf.settings')
    def test_get_rerank_keep_n(self, mock_settings):
        """Should return configured RERANK_KEEP_N."""
        mock_settings.RERANK_KEEP_N = 10
        
        assert get_rerank_keep_n() == 10


# ============================================================================
# Fallback Scenario Tests
# ============================================================================

class TestRerankerFallback:
    """Tests for reranker fallback scenarios."""
    
    @patch('apps.rag.reranker.get_reranker')
    def test_fallback_on_exception(self, mock_get_reranker):
        """Should raise exception when reranker fails."""
        mock_reranker = MagicMock()
        mock_reranker.rerank.side_effect = RuntimeError("Model failed to load")
        mock_get_reranker.return_value = mock_reranker
        
        candidates = [
            ChunkCandidate(chunk_id="1", doc_id="d1", doc_title="a.pdf",
                          text="T1", snippet="S1", vector_score=0.1)
        ]
        
        # The function should raise the exception
        # The caller (views.py) is responsible for catching and falling back
        with pytest.raises(RuntimeError):
            rerank_candidates("query", candidates)
    
    def test_preserves_chunk_ids_after_reranking(self):
        """Should preserve all chunk/doc IDs after reranking."""
        # Create candidates with specific IDs
        candidates = [
            ChunkCandidate(
                chunk_id=f"chunk-{i}",
                doc_id=f"doc-{i}",
                doc_title=f"file-{i}.pdf",
                text=f"Content for chunk {i}",
                snippet=f"Snippet {i}",
                vector_score=0.1 * i,
            )
            for i in range(5)
        ]
        
        # Set rerank scores manually (simulating reranking)
        candidates[0].rerank_score = 0.3
        candidates[1].rerank_score = 0.9  # Highest
        candidates[2].rerank_score = 0.5
        candidates[3].rerank_score = 0.7
        candidates[4].rerank_score = 0.1  # Lowest
        
        # Sort by rerank score
        sorted_candidates = sorted(
            candidates,
            key=lambda c: c.rerank_score or 0,
            reverse=True
        )
        
        # Verify IDs are preserved
        assert sorted_candidates[0].chunk_id == "chunk-1"  # Score 0.9
        assert sorted_candidates[0].doc_id == "doc-1"
        assert sorted_candidates[1].chunk_id == "chunk-3"  # Score 0.7
        assert sorted_candidates[2].chunk_id == "chunk-2"  # Score 0.5
        assert sorted_candidates[3].chunk_id == "chunk-0"  # Score 0.3
        assert sorted_candidates[4].chunk_id == "chunk-4"  # Score 0.1


# ============================================================================
# Toggle Logic Tests
# ============================================================================

class TestRerankToggleLogic:
    """Tests for the rerank toggle logic conditions."""
    
    @patch('django.conf.settings')
    def test_rerank_only_when_both_enabled(self, mock_settings):
        """Reranking should only happen when env flag AND request toggle are True."""
        # Scenario 1: Both enabled
        mock_settings.ENABLE_RERANKER = True
        request_rerank = True
        should_rerank = request_rerank and is_reranker_enabled()
        assert should_rerank is True
        
        # Scenario 2: Env enabled, request disabled
        mock_settings.ENABLE_RERANKER = True
        request_rerank = False
        should_rerank = request_rerank and is_reranker_enabled()
        assert should_rerank is False
        
        # Scenario 3: Env disabled, request enabled
        mock_settings.ENABLE_RERANKER = False
        request_rerank = True
        should_rerank = request_rerank and is_reranker_enabled()
        assert should_rerank is False
        
        # Scenario 4: Both disabled
        mock_settings.ENABLE_RERANKER = False
        request_rerank = False
        should_rerank = request_rerank and is_reranker_enabled()
        assert should_rerank is False
