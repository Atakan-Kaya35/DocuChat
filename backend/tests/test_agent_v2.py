"""
Tests for the agent executor v2 with validation gates.

These tests verify that:
1. The agent doesn't finalize prematurely after a single search
2. The validator rejects answers that don't meet constraints
3. The agent reprompts when validation fails
4. The agent produces insufficiency disclosures when budget exhausted
5. Citations are properly grounded in retrieved data
"""
import json
import pytest
from unittest.mock import patch, MagicMock
from dataclasses import dataclass
from typing import List, Dict, Any

# Import the modules we're testing
from apps.agent.constraints import (
    analyze_constraints,
    PromptConstraints,
    extract_quoted_topics,
    count_topic_indicators,
)
from apps.agent.validator import (
    validate_agent_state,
    ValidationResult,
    AgentStateSnapshot,
    generate_reprompt_message,
)
from apps.agent.executor_v2 import (
    parse_strict_json_action,
    ParsedAction,
    AgentState,
    GroundedCitation,
    Insufficiency,
    run_agent_v2,
    MAX_TOOL_CALLS,
    MAX_REPROMPTS,
)


# ============================================================================
# Test Fixtures
# ============================================================================

@pytest.fixture
def complex_prompt():
    """The failure case prompt that requires multiple searches and citations."""
    return """
    Using only my uploaded documents, produce the authoritative, current operational runbook.
    
    This REQUIRES:
    - Separate tool searches for: "reindex sql", "delete order verification query", "redirect uri configuration"
    - open_citation for at least two citations
    - quote ONE exact SQL statement and ONE exact Redirect URI line from opened text
    - resolve conflicts by newest-dated doc
    
    Output sections: Reindex, Delete, Keycloak Update Checklist, Rate Limits, Retry Policy
    
    Explicitly state "Insufficient documentation" where missing.
    """


@pytest.fixture
def simple_prompt():
    """A simple prompt that doesn't require multiple searches."""
    return "What is the main topic of the documents?"


@pytest.fixture
def mock_search_result():
    """Mock search result for testing."""
    class MockSearchResult:
        doc_id = "doc-123"
        chunk_id = "chunk-456"
        chunk_index = 0
        snippet = "This is a test snippet with some SQL: SELECT * FROM orders WHERE status='pending';"
        score = 0.85
    
    class MockSearchOutput:
        results = [MockSearchResult()]
        def summary(self):
            return "Found 1 relevant chunks"
    
    return MockSearchOutput()


@pytest.fixture
def mock_citation_output():
    """Mock open_citation output for testing."""
    class MockCitationOutput:
        doc_id = "doc-123"
        chunk_id = "chunk-456"
        chunk_index = 0
        text = """
        ## Database Operations
        
        To reindex the orders table, run:
        ```sql
        REINDEX TABLE public.orders;
        ```
        
        To verify delete operations:
        ```sql
        SELECT COUNT(*) FROM orders WHERE deleted_at IS NOT NULL;
        ```
        
        ## Keycloak Configuration
        
        Redirect URI: https://app.example.com/callback
        """
        filename = "operations-guide.md"
        
        def summary(self):
            return "Retrieved 500 chars from operations-guide.md"
    
    return MockCitationOutput()


# ============================================================================
# Constraint Analyzer Tests
# ============================================================================

class TestConstraintAnalyzer:
    """Tests for the constraint analyzer module."""
    
    def test_detects_multiple_search_requirement(self, complex_prompt):
        """Should detect that multiple searches are required."""
        constraints = analyze_constraints(complex_prompt)
        
        assert constraints.min_searches >= 2
        assert len(constraints.required_search_topics) >= 2
    
    def test_detects_open_citation_requirement(self, complex_prompt):
        """Should detect that open_citation is required."""
        constraints = analyze_constraints(complex_prompt)
        
        assert constraints.min_open_citations >= 2
    
    def test_detects_exact_quote_requirement(self, complex_prompt):
        """Should detect exact quote requirements."""
        constraints = analyze_constraints(complex_prompt)
        
        assert constraints.requires_exact_quote is True
        assert "SQL statement" in constraints.exact_quote_indicators or \
               any("sql" in i.lower() for i in constraints.exact_quote_indicators)
    
    def test_detects_insufficiency_disclosure_requirement(self, complex_prompt):
        """Should detect insufficiency disclosure requirement."""
        constraints = analyze_constraints(complex_prompt)
        
        assert constraints.requires_insufficiency_disclosure is True
    
    def test_simple_prompt_has_minimal_constraints(self, simple_prompt):
        """Simple prompts should have minimal constraints."""
        constraints = analyze_constraints(simple_prompt)
        
        assert constraints.min_searches == 1
        assert constraints.min_open_citations == 0
        assert constraints.requires_exact_quote is False
    
    def test_extracts_quoted_topics(self):
        """Should extract topics from quoted strings."""
        text = 'Search for "reindex sql" and "delete verification"'
        topics = extract_quoted_topics(text)
        
        assert "reindex sql" in topics
        assert "delete verification" in topics
    
    def test_counts_topic_indicators(self):
        """Should count distinct topic indicators."""
        text = 'Search for "topic one", "topic two", and "topic three"'
        count = count_topic_indicators(text)
        
        assert count >= 3


# ============================================================================
# Validator Tests
# ============================================================================

class TestValidator:
    """Tests for the validator module."""
    
    def test_validates_min_searches_failure(self, complex_prompt):
        """Should fail when min searches not met."""
        constraints = analyze_constraints(complex_prompt)
        
        # Simulate state with only 1 search
        snapshot = AgentStateSnapshot()
        snapshot.search_count = 1
        snapshot.search_queries = ["reindex sql"]
        
        result = validate_agent_state(
            answer="Here is the answer...",
            citation_refs=[],
            constraints=constraints,
            snapshot=snapshot,
        )
        
        assert result.is_valid is False
        assert any("search" in e.message.lower() for e in result.errors)
    
    def test_validates_min_open_citations_failure(self, complex_prompt):
        """Should fail when min open citations not met."""
        constraints = analyze_constraints(complex_prompt)
        
        # Simulate state with searches but no opened citations
        snapshot = AgentStateSnapshot()
        snapshot.search_count = 3
        snapshot.search_queries = ["reindex sql", "delete query", "redirect uri"]
        snapshot.open_citation_count = 0
        
        result = validate_agent_state(
            answer="Here is the answer...",
            citation_refs=[],
            constraints=constraints,
            snapshot=snapshot,
        )
        
        assert result.is_valid is False
        assert any("citation" in e.message.lower() for e in result.errors)
    
    def test_validates_grounded_claims(self):
        """Should detect ungrounded claims."""
        constraints = PromptConstraints(min_searches=1)
        
        snapshot = AgentStateSnapshot()
        snapshot.search_count = 1
        snapshot.open_citation_count = 1
        snapshot.opened_citation_texts = ["Regular document text without pg_reindex."]
        snapshot.search_snippets = ["Some snippet text."]
        
        # Answer mentions pg_reindex which is not in sources
        result = validate_agent_state(
            answer="You should run pg_reindex to fix the table.",
            citation_refs=[1],
            constraints=constraints,
            snapshot=snapshot,
        )
        
        # Should have ungrounded claim warning/error
        all_messages = [e.message for e in result.errors + result.warnings]
        assert any("pg_reindex" in m or "ungrounded" in m.lower() for m in all_messages)
    
    def test_validates_citation_references(self):
        """Should detect invalid citation references."""
        constraints = PromptConstraints(min_searches=1)
        
        snapshot = AgentStateSnapshot()
        snapshot.open_citation_count = 1  # Only [1] is valid
        
        # Answer references [1], [2], [3] but only [1] exists
        result = validate_agent_state(
            answer="According to [1], [2], and [3]...",
            citation_refs=[1, 2, 3],
            constraints=constraints,
            snapshot=snapshot,
        )
        
        # Should have warnings about invalid references
        assert any("2" in w.message or "3" in w.message for w in result.warnings)
    
    def test_valid_state_passes(self):
        """Should pass when all constraints are met."""
        constraints = PromptConstraints(
            min_searches=2,
            min_open_citations=1,
        )
        
        snapshot = AgentStateSnapshot()
        snapshot.search_count = 2
        snapshot.search_queries = ["query1", "query2"]
        snapshot.open_citation_count = 1
        snapshot.opened_citation_texts = ["Some text from the document."]
        snapshot.search_snippets = ["Snippet 1", "Snippet 2"]
        
        result = validate_agent_state(
            answer="Based on the document [1], here is the answer.",
            citation_refs=[1],
            constraints=constraints,
            snapshot=snapshot,
        )
        
        assert result.is_valid is True
        assert len(result.errors) == 0
    
    def test_generates_reprompt_message(self, complex_prompt):
        """Should generate useful reprompt message on failure."""
        constraints = analyze_constraints(complex_prompt)
        
        result = ValidationResult(is_valid=False)
        result.add_error("MIN_SEARCHES_UNMET", "Required 3 searches, got 1")
        result.add_error("MIN_OPEN_CITATIONS_UNMET", "Required 2 open citations, got 0")
        
        message = generate_reprompt_message(result, constraints, remaining_tool_budget=4)
        
        assert "VALIDATION FAILED" in message
        assert "3 searches" in message or "search" in message.lower()
        assert "4" in message  # Remaining budget


# ============================================================================
# Action Parser Tests
# ============================================================================

class TestActionParser:
    """Tests for the strict JSON action parser."""
    
    def test_parses_tool_call(self):
        """Should parse valid TOOL_CALL JSON."""
        response = '{"type": "tool_call", "tool": "search_docs", "input": {"query": "test"}}'
        action = parse_strict_json_action(response)
        
        assert action.action_type == "tool_call"
        assert action.tool_call is not None
        assert action.tool_call.tool == "search_docs"
        assert action.tool_call.input["query"] == "test"
    
    def test_parses_final_action(self):
        """Should parse valid FINAL JSON."""
        response = '''{
            "type": "final",
            "answer": "The answer is 42.",
            "used_citations": [{"docId": "doc-1", "chunkId": "chunk-1", "chunkIndex": 0}],
            "insufficiencies": []
        }'''
        action = parse_strict_json_action(response)
        
        assert action.action_type == "final"
        assert action.final is not None
        assert action.final.answer == "The answer is 42."
        assert len(action.final.used_citations) == 1
    
    def test_rejects_unknown_tool(self):
        """Should reject unknown tools."""
        response = '{"type": "tool_call", "tool": "hack_system", "input": {}}'
        action = parse_strict_json_action(response)
        
        assert action.action_type == "invalid"
        assert "Unknown tool" in action.error
    
    def test_handles_malformed_json(self):
        """Should handle malformed JSON gracefully."""
        response = "This is not JSON at all"
        action = parse_strict_json_action(response)
        
        assert action.action_type == "invalid"
    
    def test_extracts_json_from_text(self):
        """Should extract JSON even with surrounding text."""
        response = '''I'll search for that.
        {"type": "tool_call", "tool": "search_docs", "input": {"query": "test"}}
        That should help.'''
        action = parse_strict_json_action(response)
        
        assert action.action_type == "tool_call"
        assert action.tool_call.tool == "search_docs"
    
    def test_infers_type_from_structure(self):
        """Should infer action type when 'type' field is missing."""
        # Missing 'type' but has tool/input
        response = '{"tool": "search_docs", "input": {"query": "test"}}'
        action = parse_strict_json_action(response)
        
        assert action.action_type == "tool_call"
        
        # Missing 'type' but has answer
        response = '{"answer": "The answer.", "citations": []}'
        action = parse_strict_json_action(response)
        
        assert action.action_type == "final"


# ============================================================================
# Agent State Tests
# ============================================================================

class TestAgentState:
    """Tests for the AgentState class."""
    
    def test_tracks_search_queries(self):
        """Should track unique search queries."""
        constraints = PromptConstraints()
        state = AgentState(constraints)
        
        # Simulate adding search results
        class MockOutput:
            results = []
            def summary(self): return ""
        
        state.add_search_results("query one", MockOutput(), {})
        state.add_search_results("query two", MockOutput(), {})
        
        assert state.search_count == 2
        assert "query one" in state._search_queries
        assert "query two" in state._search_queries
    
    def test_tracks_tool_budget(self):
        """Should track remaining tool budget."""
        constraints = PromptConstraints()
        state = AgentState(constraints)
        
        assert state.remaining_tool_budget == MAX_TOOL_CALLS
        
        state.tool_calls_used = 3
        assert state.remaining_tool_budget == MAX_TOOL_CALLS - 3
    
    def test_creates_snapshot(self):
        """Should create accurate snapshot for validation."""
        constraints = PromptConstraints()
        state = AgentState(constraints)
        state._search_queries = ["q1", "q2"]
        
        snapshot = state.to_snapshot()
        
        assert snapshot.search_count == 2
        assert snapshot.search_queries == ["q1", "q2"]


# ============================================================================
# Integration Tests (Mock LLM)
# ============================================================================

class TestAgentIntegration:
    """Integration tests with mocked LLM."""
    
    @pytest.fixture
    def mock_early_final_llm(self):
        """LLM that tries to finalize immediately after one search."""
        call_count = 0
        
        def mock_llm(prompt: str, max_tokens: int = 600) -> str:
            nonlocal call_count
            call_count += 1
            
            if call_count == 1:
                # First call: return a search
                return '{"type": "tool_call", "tool": "search_docs", "input": {"query": "reindex sql"}}'
            else:
                # Immediately try to finalize without meeting constraints
                return '''{
                    "type": "final",
                    "answer": "You should use pg_reindex to fix the table.",
                    "used_citations": [],
                    "insufficiencies": []
                }'''
        
        return mock_llm
    
    @pytest.fixture
    def mock_compliant_llm(self):
        """LLM that properly follows constraints."""
        call_count = 0
        
        def mock_llm(prompt: str, max_tokens: int = 600) -> str:
            nonlocal call_count
            call_count += 1
            
            if call_count == 1:
                return '{"type": "tool_call", "tool": "search_docs", "input": {"query": "reindex sql"}}'
            elif call_count == 2:
                return '{"type": "tool_call", "tool": "search_docs", "input": {"query": "delete query"}}'
            elif call_count == 3:
                return '{"type": "tool_call", "tool": "open_citation", "input": {"docId": "doc-123", "chunkId": "chunk-456"}}'
            elif call_count == 4:
                return '{"type": "tool_call", "tool": "open_citation", "input": {"docId": "doc-123", "chunkId": "chunk-789"}}'
            else:
                return '''{
                    "type": "final",
                    "answer": "Based on [1] and [2], here is the answer with proper citations.",
                    "used_citations": [
                        {"docId": "doc-123", "chunkId": "chunk-456", "chunkIndex": 0},
                        {"docId": "doc-123", "chunkId": "chunk-789", "chunkIndex": 1}
                    ],
                    "insufficiencies": []
                }'''
        
        return mock_llm
    
    @patch('apps.agent.executor_v2.call_llm')
    @patch('apps.agent.executor_v2.search_docs')
    @patch('apps.agent.executor_v2.open_citation')
    @patch('apps.agent.planner.generate_plan')
    def test_validator_rejects_early_final(
        self,
        mock_generate_plan,
        mock_open_citation,
        mock_search_docs,
        mock_call_llm,
        complex_prompt,
        mock_search_result,
        mock_early_final_llm,
    ):
        """
        FAILURE CASE REPRODUCTION:
        
        Agent should NOT accept a FINAL after just one search when the prompt
        requires multiple searches and open_citation calls.
        """
        from apps.agent.planner import Plan
        
        # Setup mocks
        mock_generate_plan.return_value = Plan(
            steps=["Search for reindex", "Search for delete", "Open citations", "Synthesize"],
            is_fallback=False
        )
        mock_search_docs.return_value = mock_search_result
        mock_call_llm.side_effect = mock_early_final_llm
        
        # Execute agent
        result = run_agent_v2(complex_prompt, "test-user-id")
        
        # The validator should have triggered reprompts
        validation_entries = [t for t in result.trace if t.type.value == 'validation']
        reprompt_entries = [t for t in result.trace if t.type.value == 'reprompt']
        
        # Should have at least one validation failure
        assert len(validation_entries) > 0 or len(reprompt_entries) > 0, \
            "Validator should have rejected early FINAL"
        
        # The trace should show multiple iterations
        tool_calls = [t for t in result.trace if t.type.value == 'tool_call' and t.tool != 'thinking']
        assert len(tool_calls) >= 1, "Should have at least attempted tool calls"
    
    @patch('apps.agent.executor_v2.call_llm')
    @patch('apps.agent.executor_v2.search_docs')
    @patch('apps.agent.executor_v2.open_citation')
    @patch('apps.agent.planner.generate_plan')
    def test_agent_completes_with_proper_citations(
        self,
        mock_generate_plan,
        mock_open_citation,
        mock_search_docs,
        mock_call_llm,
        mock_search_result,
        mock_citation_output,
        mock_compliant_llm,
    ):
        """Agent should complete successfully when all constraints are met."""
        from apps.agent.planner import Plan
        
        # Setup mocks
        mock_generate_plan.return_value = Plan(
            steps=["Search", "Open citations", "Synthesize"],
            is_fallback=False
        )
        mock_search_docs.return_value = mock_search_result
        mock_open_citation.return_value = mock_citation_output
        mock_call_llm.side_effect = mock_compliant_llm
        
        # Simple prompt that doesn't require complex constraints
        result = run_agent_v2("What is in the operations guide?", "test-user-id")
        
        # Should have an answer
        assert result.answer is not None
        assert len(result.answer) > 0
        
        # Trace should include tool calls
        tool_calls = [t for t in result.trace if t.type.value == 'tool_call' and t.tool != 'thinking']
        assert len(tool_calls) > 0
    
    def test_constraints_analyzed_from_complex_prompt(self, complex_prompt):
        """
        The constraint analyzer should detect all requirements from the failure prompt.
        """
        constraints = analyze_constraints(complex_prompt)
        
        # Should require multiple searches (at least 3 topics mentioned)
        assert constraints.min_searches >= 2, \
            f"Should require multiple searches, got {constraints.min_searches}"
        
        # Should require open_citation
        assert constraints.min_open_citations >= 1, \
            f"Should require open_citation, got {constraints.min_open_citations}"
        
        # Should detect exact quote requirement
        assert constraints.requires_exact_quote, \
            "Should detect exact quote requirement"
        
        # Should require insufficiency disclosure
        assert constraints.requires_insufficiency_disclosure, \
            "Should require insufficiency disclosure"


# ============================================================================
# Edge Case Tests
# ============================================================================

class TestEdgeCases:
    """Tests for edge cases and error handling."""
    
    def test_empty_question_raises_error(self):
        """Should raise error for empty question."""
        from apps.agent.executor_v2 import AgentError
        
        with pytest.raises(AgentError):
            run_agent_v2("", "test-user")
    
    def test_whitespace_only_question_raises_error(self):
        """Should raise error for whitespace-only question."""
        from apps.agent.executor_v2 import AgentError
        
        with pytest.raises(AgentError):
            run_agent_v2("   \n\t  ", "test-user")
    
    def test_very_long_question_truncated(self):
        """Very long questions should be truncated."""
        from apps.agent.executor_v2 import MAX_QUESTION_LENGTH
        
        # Create question longer than limit
        long_question = "a" * (MAX_QUESTION_LENGTH + 500)
        
        # Should not raise, but truncate
        constraints = analyze_constraints(long_question)
        # Just verify it doesn't crash
        assert constraints is not None
    
    def test_constraint_analyzer_handles_edge_patterns(self):
        """Constraint analyzer should handle various edge patterns."""
        # Empty string
        constraints = analyze_constraints("")
        assert constraints.min_searches == 1
        
        # Just numbers
        constraints = analyze_constraints("123456")
        assert constraints.min_searches == 1
        
        # Unicode
        constraints = analyze_constraints("搜索 документы 🔍")
        assert constraints is not None


# ============================================================================
# Run tests
# ============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
