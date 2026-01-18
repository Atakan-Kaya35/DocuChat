"""
Agent validator module.

Validates agent state before allowing FINAL action:
- Checks that required searches have been performed
- Checks that required citations have been opened
- Validates that claims in the answer are grounded in retrieved text
- Ensures exact quote requirements are met
- Verifies citation references are valid

Used as a gate before accepting FINAL to prevent premature finalization.
"""
import re
import logging
from dataclasses import dataclass, field
from typing import List, Dict, Set, Optional, Any, TYPE_CHECKING

if TYPE_CHECKING:
    from apps.agent.constraints import PromptConstraints

logger = logging.getLogger(__name__)


@dataclass
class ValidationError:
    """Single validation error."""
    code: str
    message: str
    severity: str = "error"  # "error" or "warning"
    
    def to_dict(self) -> dict:
        return {
            'code': self.code,
            'message': self.message,
            'severity': self.severity,
        }


@dataclass
class ValidationResult:
    """Result of validation check."""
    is_valid: bool
    errors: List[ValidationError] = field(default_factory=list)
    warnings: List[ValidationError] = field(default_factory=list)
    
    def add_error(self, code: str, message: str):
        """Add a validation error."""
        self.errors.append(ValidationError(code=code, message=message, severity="error"))
        self.is_valid = False
    
    def add_warning(self, code: str, message: str):
        """Add a validation warning (doesn't fail validation)."""
        self.warnings.append(ValidationError(code=code, message=message, severity="warning"))
    
    def to_dict(self) -> dict:
        return {
            'is_valid': self.is_valid,
            'errors': [e.to_dict() for e in self.errors],
            'warnings': [w.to_dict() for w in self.warnings],
        }
    
    def error_summary(self) -> str:
        """Generate a summary of errors for reprompt."""
        if not self.errors:
            return "No errors."
        return "\n".join(f"- {e.message}" for e in self.errors)


# Known technical terms that might appear in answers but shouldn't be claimed without doc support
SUSPICIOUS_TERMS = [
    # PostgreSQL operations
    'pg_reindex', 'reindex', 'vacuum', 'vacuum analyze', 'analyze table',
    # External tools
    'kubectl', 'helm', 'docker compose', 'systemctl', 'ansible',
    # Database operations
    'drop table', 'truncate', 'alter table', 'create index',
    # Common hallucinations
    'according to best practices', 'as recommended', 'typically',
]

# Patterns for detecting operational claims (commands, procedures)
OPERATIONAL_CLAIM_PATTERNS = [
    r'run\s+`([^`]+)`',  # `command` syntax
    r'execute\s+`([^`]+)`',
    r'run\s+the\s+following[:\s]+([^\n]+)',
    r'use\s+the\s+command[:\s]+([^\n]+)',
]


@dataclass
class AgentStateSnapshot:
    """
    Snapshot of agent state for validation.
    
    This is what the validator sees - a read-only view of what the agent has done.
    """
    search_count: int = 0
    search_queries: List[str] = field(default_factory=list)
    open_citation_count: int = 0
    opened_citation_texts: List[str] = field(default_factory=list)  # Full text of opened citations
    opened_citation_ids: List[Dict[str, str]] = field(default_factory=list)  # [{docId, chunkId}]
    search_snippets: List[str] = field(default_factory=list)  # Snippets from search results
    
    @classmethod
    def from_agent_state(cls, state: Any) -> 'AgentStateSnapshot':
        """Create snapshot from actual AgentState."""
        snapshot = cls()
        
        # Count unique search queries
        seen_queries = set()
        for result in getattr(state, 'search_results', []):
            query = getattr(result, 'query', None)
            if query and query not in seen_queries:
                seen_queries.add(query)
                snapshot.search_queries.append(query)
        snapshot.search_count = len(seen_queries) if seen_queries else (
            1 if state.search_results else 0
        )
        
        # Get search snippets
        for result in getattr(state, 'search_results', []):
            snippet = getattr(result, 'snippet', '')
            if snippet:
                snapshot.search_snippets.append(snippet)
        
        # Count opened citations
        opened = getattr(state, 'opened_citations', [])
        snapshot.open_citation_count = len(opened)
        
        for citation in opened:
            text = getattr(citation, 'text', '')
            if text:
                snapshot.opened_citation_texts.append(text)
            
            snapshot.opened_citation_ids.append({
                'docId': getattr(citation, 'doc_id', ''),
                'chunkId': getattr(citation, 'chunk_id', ''),
            })
        
        return snapshot


def validate_min_searches(
    snapshot: AgentStateSnapshot,
    constraints: 'PromptConstraints',
    result: ValidationResult
):
    """Check that minimum number of searches was performed."""
    if constraints.min_searches > 1 and snapshot.search_count < constraints.min_searches:
        result.add_error(
            "MIN_SEARCHES_UNMET",
            f"Required at least {constraints.min_searches} separate searches, "
            f"but only {snapshot.search_count} were performed. "
            f"Topics to search: {constraints.required_search_topics[:3]}"
        )


def validate_min_open_citations(
    snapshot: AgentStateSnapshot,
    constraints: 'PromptConstraints',
    result: ValidationResult
):
    """Check that minimum number of citations were opened."""
    if constraints.min_open_citations > 0 and snapshot.open_citation_count < constraints.min_open_citations:
        result.add_error(
            "MIN_OPEN_CITATIONS_UNMET",
            f"Required to open at least {constraints.min_open_citations} citation(s), "
            f"but only {snapshot.open_citation_count} were opened. "
            f"Call open_citation on search results before finalizing."
        )


def validate_citation_references(
    answer: str,
    citation_refs: List[int],
    snapshot: AgentStateSnapshot,
    result: ValidationResult
):
    """
    Validate that citation references in the answer are valid.
    
    Checks:
    - Citation numbers reference actually opened citations
    - No hallucinated citation numbers
    """
    # Find all [N] references in answer
    found_refs = set(int(m) for m in re.findall(r'\[(\d+)\]', answer))
    
    # Check that explicit refs are valid
    max_valid_ref = snapshot.open_citation_count
    
    for ref in citation_refs:
        if ref > max_valid_ref or ref < 1:
            result.add_warning(
                "INVALID_CITATION_REF",
                f"Citation reference [{ref}] does not correspond to an opened citation. "
                f"Only citations [1] through [{max_valid_ref}] are valid."
            )
    
    for ref in found_refs:
        if ref > max_valid_ref or ref < 1:
            result.add_warning(
                "HALLUCINATED_CITATION",
                f"Citation [{ref}] in answer is not a valid opened citation."
            )


def validate_grounded_claims(
    answer: str,
    snapshot: AgentStateSnapshot,
    result: ValidationResult
):
    """
    Check that claims in the answer are grounded in retrieved/opened text.
    
    Looks for suspicious terms and verifies they appear in source material.
    """
    answer_lower = answer.lower()
    
    # Build corpus of all retrieved text
    corpus = " ".join(snapshot.opened_citation_texts + snapshot.search_snippets).lower()
    
    # If we have no corpus, any specific claim is suspicious
    if not corpus.strip():
        # Check if answer makes specific claims
        if any(term in answer_lower for term in SUSPICIOUS_TERMS):
            result.add_error(
                "UNGROUNDED_CLAIM_NO_CONTEXT",
                "Answer contains specific technical claims but no source material was retrieved. "
                "Perform searches and open citations before making claims."
            )
        return
    
    # Check each suspicious term
    ungrounded = []
    for term in SUSPICIOUS_TERMS:
        if term in answer_lower and term not in corpus:
            ungrounded.append(term)
    
    if ungrounded:
        terms_str = ", ".join(f"'{t}'" for t in ungrounded[:3])
        if len(ungrounded) > 3:
            terms_str += f" and {len(ungrounded) - 3} more"
        
        result.add_error(
            "UNGROUNDED_CLAIM",
            f"These terms appear in the answer but not in any retrieved source: {terms_str}. "
            f"Only include information that appears in the documents."
        )


def validate_exact_quote_requirement(
    answer: str,
    constraints: 'PromptConstraints',
    snapshot: AgentStateSnapshot,
    result: ValidationResult
):
    """
    Check that exact quote requirements are satisfied.
    
    If the user asked for exact SQL, redirect URI, etc., verify that:
    1. At least one citation was opened
    2. A quoted/formatted block appears in the answer
    3. That block appears verbatim in opened citation text
    """
    if not constraints.requires_exact_quote:
        return
    
    # Must have opened citations to quote from
    if snapshot.open_citation_count == 0:
        result.add_error(
            "EXACT_QUOTE_NO_SOURCE",
            f"Exact quote is required for {constraints.exact_quote_indicators}, "
            f"but no citations were opened. Call open_citation first."
        )
        return
    
    # Look for code blocks or quoted text in answer
    code_block_patterns = [
        r'```[^`]*```',  # fenced code block
        r'`[^`]+`',  # inline code
        r'"[^"]{10,}"',  # quoted text (min 10 chars)
    ]
    
    found_quotes = []
    for pattern in code_block_patterns:
        matches = re.findall(pattern, answer, re.DOTALL)
        for match in matches:
            # Clean up the match
            cleaned = match.strip('`"').strip()
            if len(cleaned) >= 10:  # Meaningful length
                found_quotes.append(cleaned)
    
    if not found_quotes:
        result.add_warning(
            "NO_QUOTED_TEXT",
            f"Exact quote was required for {constraints.exact_quote_indicators}, "
            f"but no code blocks or quoted text found in answer."
        )
        return
    
    # Check if any quote appears in opened citation text
    corpus = "\n".join(snapshot.opened_citation_texts)
    
    grounded_quotes = []
    for quote in found_quotes:
        # Normalize for comparison (collapse whitespace)
        normalized_quote = " ".join(quote.split())
        normalized_corpus = " ".join(corpus.split())
        
        if normalized_quote in normalized_corpus or quote in corpus:
            grounded_quotes.append(quote[:50])
    
    if not grounded_quotes and found_quotes:
        result.add_warning(
            "QUOTE_NOT_VERBATIM",
            f"Found quoted text in answer, but it doesn't appear verbatim in opened citations. "
            f"Ensure quotes match the exact text from documents."
        )


def validate_no_empty_answer(
    answer: str,
    snapshot: AgentStateSnapshot,
    result: ValidationResult
):
    """Check that answer is not empty when we have sources."""
    if not answer or not answer.strip():
        result.add_error(
            "EMPTY_ANSWER",
            "Answer is empty. Provide a substantive response."
        )
        return
    
    # If we have sources but answer is just "I don't know"
    if snapshot.open_citation_count > 0 or len(snapshot.search_snippets) > 0:
        dont_know_patterns = [
            r"i don't know",
            r"i cannot find",
            r"no relevant information",
        ]
        answer_lower = answer.lower()
        for pattern in dont_know_patterns:
            if re.search(pattern, answer_lower) and len(answer) < 100:
                result.add_warning(
                    "UNEXPLAINED_DONT_KNOW",
                    "Answer claims no information found, but sources were retrieved. "
                    "Explain what was searched and why it doesn't answer the question."
                )
                break


def validate_insufficiency_disclosure(
    answer: str,
    constraints: 'PromptConstraints',
    insufficiencies: List[Dict[str, Any]],
    result: ValidationResult
):
    """
    Check that insufficiency disclosure is present when required.
    """
    if not constraints.requires_insufficiency_disclosure:
        return
    
    answer_lower = answer.lower()
    
    # Check for explicit insufficiency markers
    insufficiency_markers = [
        'insufficient documentation',
        'not found in documents',
        'missing from documentation',
        'no documentation available',
        'could not find',
    ]
    
    has_insufficiency_marker = any(marker in answer_lower for marker in insufficiency_markers)
    
    if insufficiencies and not has_insufficiency_marker:
        result.add_warning(
            "MISSING_INSUFFICIENCY_DISCLOSURE",
            f"Information gaps were found but not explicitly disclosed. "
            f"State 'Insufficient documentation' for: {[i.get('section') for i in insufficiencies]}"
        )


def validate_agent_state(
    answer: str,
    citation_refs: List[int],
    constraints: 'PromptConstraints',
    snapshot: AgentStateSnapshot,
    insufficiencies: Optional[List[Dict[str, Any]]] = None
) -> ValidationResult:
    """
    Main validation function - checks all constraints before allowing FINAL.
    
    Args:
        answer: The proposed final answer
        citation_refs: Citation numbers referenced by the answer
        constraints: Constraints extracted from the user prompt
        snapshot: Snapshot of agent state (what was searched/opened)
        insufficiencies: List of known information gaps
        
    Returns:
        ValidationResult with is_valid flag and any errors
    """
    result = ValidationResult(is_valid=True)
    insufficiencies = insufficiencies or []
    
    # Run all validators
    validate_no_empty_answer(answer, snapshot, result)
    validate_min_searches(snapshot, constraints, result)
    validate_min_open_citations(snapshot, constraints, result)
    validate_citation_references(answer, citation_refs, snapshot, result)
    validate_grounded_claims(answer, snapshot, result)
    validate_exact_quote_requirement(answer, constraints, snapshot, result)
    validate_insufficiency_disclosure(answer, constraints, insufficiencies, result)
    
    logger.info(
        f"Validation result: valid={result.is_valid}, "
        f"errors={len(result.errors)}, warnings={len(result.warnings)}"
    )
    
    return result


def generate_reprompt_message(
    validation_result: ValidationResult,
    constraints: 'PromptConstraints',
    remaining_tool_budget: int
) -> str:
    """
    Generate a reprompt message when validation fails.
    
    Args:
        validation_result: The failed validation result
        constraints: Original constraints
        remaining_tool_budget: How many tool calls remain
        
    Returns:
        Message to send to LLM to correct its behavior
    """
    lines = [
        "VALIDATION FAILED - Your answer does not meet requirements.",
        "",
        "ERRORS:",
        validation_result.error_summary(),
        "",
        f"REMAINING TOOL BUDGET: {remaining_tool_budget} calls",
        "",
    ]
    
    if remaining_tool_budget > 0:
        lines.append("You MUST output a TOOL_CALL to gather more information before finalizing.")
        lines.append("Output ONLY valid JSON in TOOL_CALL format.")
    else:
        lines.append("Tool budget exhausted. Output FINAL with explicit insufficiency notes.")
        lines.append('Include "insufficiencies" array listing what could not be found.')
    
    return "\n".join(lines)
