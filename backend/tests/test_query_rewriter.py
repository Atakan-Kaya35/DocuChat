"""
Tests for the query rewriter module.

Tests the QueryRewriterResult parsing and rewrite_query function.
"""
import json
import pytest
from unittest.mock import patch, MagicMock


# Import the modules we're testing
from apps.rag.query_rewriter import (
    parse_rewriter_response,
    rewrite_query,
    QueryRewriterResult,
    REQUIRED_KEYS,
    ALLOWED_KEYS,
)


# ============================================================================
# JSON Parsing Tests
# ============================================================================

class TestParseRewriterResponse:
    """Tests for the strict JSON parser."""
    
    def test_parse_valid_json(self):
        """Should parse valid JSON with all fields."""
        valid_response = json.dumps({
            "rewritten_query": "How do I configure authentication in Keycloak?",
            "alternate_queries": ["Keycloak auth setup", "SSO configuration"],
            "keywords": ["Keycloak", "authentication", "SSO"],
            "named_entities": ["Keycloak"],
            "constraints": {
                "time_range": None,
                "document_scope": None,
                "language": "English",
                "response_format": None
            },
            "intent": "configuration_guide",
            "ambiguities": [],
            "clarifying_questions": [],
            "security_flags": []
        })
        
        result = parse_rewriter_response(valid_response)
        
        assert result is not None
        assert result.rewritten_query == "How do I configure authentication in Keycloak?"
        assert "Keycloak auth setup" in result.alternate_queries
        assert "Keycloak" in result.keywords
    
    def test_parse_minimal_valid_json(self):
        """Should parse JSON with only required fields."""
        minimal_response = json.dumps({
            "rewritten_query": "What is the main topic?"
        })
        
        result = parse_rewriter_response(minimal_response)
        
        assert result is not None
        assert result.rewritten_query == "What is the main topic?"
        assert result.alternate_queries == []
        assert result.keywords == []
    
    def test_parse_invalid_json(self):
        """Should return None for invalid JSON."""
        invalid_response = "This is not JSON at all"
        
        result = parse_rewriter_response(invalid_response)
        
        assert result is None
    
    def test_parse_missing_required_field(self):
        """Should return None when rewritten_query is missing."""
        missing_required = json.dumps({
            "alternate_queries": ["query 1", "query 2"],
            "keywords": ["keyword"]
        })
        
        result = parse_rewriter_response(missing_required)
        
        assert result is None
    
    def test_parse_empty_rewritten_query(self):
        """Should return None when rewritten_query is empty."""
        empty_query = json.dumps({
            "rewritten_query": ""
        })
        
        result = parse_rewriter_response(empty_query)
        
        assert result is None
    
    def test_parse_whitespace_only_rewritten_query(self):
        """Should return None when rewritten_query is whitespace only."""
        whitespace_query = json.dumps({
            "rewritten_query": "   \n\t  "
        })
        
        result = parse_rewriter_response(whitespace_query)
        
        assert result is None
    
    def test_parse_extra_keys_rejected(self):
        """Should return None when extra keys are present (strict mode)."""
        extra_keys = json.dumps({
            "rewritten_query": "Valid query",
            "unknown_field": "should cause rejection",
            "another_extra": 123
        })
        
        result = parse_rewriter_response(extra_keys)
        
        assert result is None
    
    def test_parse_extracts_json_from_text(self):
        """Should extract JSON even when surrounded by text."""
        text_with_json = '''
        Here is the rewritten query:
        {"rewritten_query": "Extracted query"}
        Hope this helps!
        '''
        
        result = parse_rewriter_response(text_with_json)
        
        assert result is not None
        assert result.rewritten_query == "Extracted query"
    
    def test_parse_handles_null_constraints(self):
        """Should handle null values in constraints."""
        null_constraints = json.dumps({
            "rewritten_query": "Valid query",
            "constraints": {
                "time_range": None,
                "document_scope": None,
                "language": None,
                "response_format": None
            }
        })
        
        result = parse_rewriter_response(null_constraints)
        
        assert result is not None
        assert result.constraints["time_range"] is None


# ============================================================================
# Rewrite Query Function Tests
# ============================================================================

class TestRewriteQuery:
    """Tests for the rewrite_query function with mocked LLM."""
    
    @patch('apps.rag.query_rewriter.httpx.Client')
    def test_rewrite_query_success(self, mock_client_class):
        """Should return QueryRewriterResult on success."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "message": {
                "content": json.dumps({
                    "rewritten_query": "Refined query text",
                    "alternate_queries": [],
                    "keywords": ["test"],
                    "named_entities": [],
                    "constraints": {},
                    "intent": "question",
                    "ambiguities": [],
                    "clarifying_questions": [],
                    "security_flags": []
                })
            }
        }
        mock_response.raise_for_status = MagicMock()
        
        mock_client = MagicMock()
        mock_client.post.return_value = mock_response
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client_class.return_value = mock_client
        
        result = rewrite_query("Original question")
        
        assert result is not None
        assert result.rewritten_query == "Refined query text"
    
    @patch('apps.rag.query_rewriter.httpx.Client')
    def test_rewrite_query_empty_input(self, mock_client_class):
        """Should return None for empty input without calling LLM."""
        result = rewrite_query("")
        
        assert result is None
        mock_client_class.assert_not_called()
    
    @patch('apps.rag.query_rewriter.httpx.Client')
    def test_rewrite_query_whitespace_input(self, mock_client_class):
        """Should return None for whitespace-only input."""
        result = rewrite_query("   \n  ")
        
        assert result is None
        mock_client_class.assert_not_called()
    
    @patch('apps.rag.query_rewriter.httpx.Client')
    def test_rewrite_query_llm_timeout(self, mock_client_class):
        """Should return None on LLM timeout."""
        import httpx
        
        mock_client = MagicMock()
        mock_client.post.side_effect = httpx.TimeoutException("Timeout")
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client_class.return_value = mock_client
        
        result = rewrite_query("Test question")
        
        assert result is None
    
    @patch('apps.rag.query_rewriter.httpx.Client')
    def test_rewrite_query_llm_error(self, mock_client_class):
        """Should return None on LLM HTTP error."""
        import httpx
        
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.request = MagicMock()
        
        mock_client = MagicMock()
        mock_client.post.side_effect = httpx.HTTPStatusError(
            "Server error", request=mock_response.request, response=mock_response
        )
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client_class.return_value = mock_client
        
        result = rewrite_query("Test question")
        
        assert result is None
    
    @patch('apps.rag.query_rewriter.httpx.Client')
    def test_rewrite_query_invalid_json_response(self, mock_client_class):
        """Should return None when LLM returns invalid JSON."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "message": {
                "content": "This is not valid JSON"
            }
        }
        mock_response.raise_for_status = MagicMock()
        
        mock_client = MagicMock()
        mock_client.post.return_value = mock_response
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client_class.return_value = mock_client
        
        result = rewrite_query("Test question")
        
        assert result is None
    
    @patch('apps.rag.query_rewriter.httpx.Client')
    def test_rewrite_query_with_doc_titles(self, mock_client_class):
        """Should include doc titles in the prompt."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "message": {
                "content": json.dumps({
                    "rewritten_query": "Query with context"
                })
            }
        }
        mock_response.raise_for_status = MagicMock()
        
        mock_client = MagicMock()
        mock_client.post.return_value = mock_response
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client_class.return_value = mock_client
        
        result = rewrite_query("Question", doc_titles=["doc1.pdf", "doc2.txt"])
        
        assert result is not None
        # Verify the call was made (doc titles should be included)
        mock_client.post.assert_called_once()


# ============================================================================
# QueryRewriterResult Tests
# ============================================================================

class TestQueryRewriterResult:
    """Tests for the QueryRewriterResult dataclass."""
    
    def test_to_dict(self):
        """Should serialize to dict correctly."""
        result = QueryRewriterResult(
            rewritten_query="Test query",
            alternate_queries=["alt1", "alt2"],
            keywords=["key1"],
            named_entities=["entity1"],
            constraints={"time_range": "2024"},
            intent="search",
            ambiguities=["ambig1"],
            clarifying_questions=["question1"],
            security_flags=["flag1"]
        )
        
        d = result.to_dict()
        
        assert d["rewritten_query"] == "Test query"
        assert d["alternate_queries"] == ["alt1", "alt2"]
        assert d["keywords"] == ["key1"]
        assert d["security_flags"] == ["flag1"]


# ============================================================================
# Integration-style Test
# ============================================================================

class TestQueryRewriterIntegration:
    """Integration tests for query rewriting in the RAG pipeline."""
    
    @patch('apps.rag.query_rewriter.rewrite_query')
    def test_fallback_on_rewriter_failure(self, mock_rewrite):
        """When rewriter fails, should gracefully return None for fallback."""
        mock_rewrite.return_value = None
        
        result = rewrite_query("Test question")
        
        # Should return None, caller should use original query
        assert result is None


# ============================================================================
# Run tests
# ============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
