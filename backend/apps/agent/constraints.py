"""
Constraint analyzer for agent prompts.

Parses user prompts to detect implicit and explicit requirements:
- Number of separate searches required
- Number of open_citation calls required
- Exact quote requirements
- Conflict resolution requirements
- Required output sections

Used by the validator to ensure the agent satisfies constraints before finalizing.
"""
import re
import logging
from dataclasses import dataclass, field
from typing import List, Set, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class PromptConstraints:
    """
    Constraints extracted from the user prompt.
    
    The validator uses these to determine if the agent has done enough work.
    """
    # Search constraints
    min_searches: int = 1
    required_search_topics: List[str] = field(default_factory=list)
    
    # Citation constraints
    min_open_citations: int = 0
    
    # Content constraints
    requires_exact_quote: bool = False
    exact_quote_indicators: List[str] = field(default_factory=list)  # "SQL statement", "redirect URI"
    
    # Conflict resolution
    requires_conflict_resolution: bool = False
    conflict_resolution_rule: Optional[str] = None  # "newest", "most specific", etc.
    
    # Output structure requirements
    required_sections: List[str] = field(default_factory=list)
    requires_insufficiency_disclosure: bool = False
    
    # Answer complexity estimate
    estimated_min_answer_length: int = 50  # Minimum expected characters in answer
    is_complex_query: bool = False  # Multi-section or detailed requirement
    
    def to_dict(self) -> dict:
        return {
            'min_searches': self.min_searches,
            'required_search_topics': self.required_search_topics,
            'min_open_citations': self.min_open_citations,
            'requires_exact_quote': self.requires_exact_quote,
            'exact_quote_indicators': self.exact_quote_indicators,
            'requires_conflict_resolution': self.requires_conflict_resolution,
            'conflict_resolution_rule': self.conflict_resolution_rule,
            'required_sections': self.required_sections,
            'requires_insufficiency_disclosure': self.requires_insufficiency_disclosure,
            'estimated_min_answer_length': self.estimated_min_answer_length,
            'is_complex_query': self.is_complex_query,
        }


# Patterns for detecting search requirements
SEPARATE_SEARCH_PATTERNS = [
    # Patterns with explicit numbers first (higher priority)
    r'\(at\s+least\s+(\d+)\s+tool\s+call',  # "(at least 3 tool calls)"
    r'at\s+least\s+(\d+)\s+(?:tool\s+)?(?:call|search)',
    r'(\d+)\s+(?:tool\s+)?(?:calls?|searches)',  # "3 tool calls"
    # Patterns without explicit numbers (default to 2)
    r'separate\s+(?:tool\s+)?search(?:es)?',
    r'search\s+(?:for\s+)?(?:each|separately|individually)',
    r'multiple\s+search(?:es)?',
]

# Patterns for detecting required topics (quoted or emphasized)
TOPIC_EXTRACTION_PATTERNS = [
    r'"([^"]+)"',  # double quotes
    r"'([^']+)'",  # single quotes
    r'`([^`]+)`',  # backticks
    r'for\s+(\w+(?:\s+\w+){0,3})\s+(?:and|,)',  # "for X and Y"
]

# Patterns for open_citation requirements
OPEN_CITATION_PATTERNS = [
    r'open\s+(?:the\s+)?(?:top\s+)?(\d+)\s+citation',
    r'open_citation.*?at\s+least\s+(\d+)',
    r'at\s+least\s+(\w+)\s+citations?',  # "at least two citations"
    r'must\s+(?:call\s+)?open_citation',
    r'retrieve\s+(?:full\s+)?text',
    r'read\s+(?:the\s+)?(?:full|detailed|complete)\s+(?:text|content|chunk)',
]

# Word to number mapping
WORD_TO_NUM = {
    'one': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5,
    'six': 6, 'seven': 7, 'eight': 8, 'nine': 9, 'ten': 10
}

# Patterns for exact quote requirements
EXACT_QUOTE_PATTERNS = [
    r'exact\s+(?:sql\s+)?(?:statement|query|line|text|quote)',
    r'quote\s+(?:one|the)\s+exact',
    r'verbatim',
    r'word[- ]for[- ]word',
    r'exact\s+(?:redirect\s+)?(?:uri|url)',
    r'copy\s+(?:the\s+)?exact',
]

# Patterns for quote content types
QUOTE_TYPE_PATTERNS = [
    (r'sql\s+statement', 'SQL statement'),
    (r'redirect\s+uri', 'Redirect URI'),
    (r'url\s+(?:line|config)', 'URL configuration'),
    (r'command(?:\s+line)?', 'command'),
    (r'config(?:uration)?\s+(?:line|entry)', 'configuration'),
]

# Patterns for conflict resolution
CONFLICT_RESOLUTION_PATTERNS = [
    (r'newest[- ]?dated?\s+(?:doc|document|note)', 'newest'),
    (r'most\s+recent', 'newest'),
    (r'latest\s+(?:version|doc)', 'newest'),
    (r'highest\s+priority', 'priority'),
    (r'most\s+specific', 'specific'),
    (r'resolve\s+conflicts?', None),
]

# Patterns for required sections
SECTION_PATTERNS = [
    r'sections?:\s*([^.]+)',
    r'include\s+(?:the\s+following\s+)?sections?:\s*([^.]+)',
    r'output\s+(?:should\s+)?(?:have|include)\s+([^.]+)',
]

# Patterns for insufficiency disclosure
INSUFFICIENCY_PATTERNS = [
    r'insufficient\s+documentation',
    r'explicitly\s+(?:say|state|indicate)\s+(?:when\s+)?(?:information\s+is\s+)?missing',
    r'if\s+(?:not\s+found|missing|unavailable)',
    r'list\s+what\s+(?:was\s+)?(?:searched|tried)',
]


def extract_quoted_topics(text: str) -> List[str]:
    """Extract topics from quoted strings in the prompt."""
    topics = []
    
    # Find all quoted strings
    for pattern in TOPIC_EXTRACTION_PATTERNS[:3]:  # Quote patterns only
        matches = re.findall(pattern, text, re.IGNORECASE)
        for match in matches:
            # Filter out very short or very long matches
            if 3 <= len(match) <= 50:
                topics.append(match.strip())
    
    return topics


def count_topic_indicators(text: str) -> int:
    """
    Count how many distinct search topics are indicated in the prompt.
    
    Heuristic based on:
    - Quoted strings that look like queries
    - List items (commas, "and")
    - Bullet points
    """
    count = 0
    
    # Count quoted strings
    quoted = extract_quoted_topics(text)
    count = max(count, len(quoted))
    
    # Look for comma-separated lists in search context
    search_list_pattern = r'search\s+(?:for\s+)?(.+?)(?:\.|$)'
    search_match = re.search(search_list_pattern, text, re.IGNORECASE)
    if search_match:
        list_text = search_match.group(1)
        # Count commas and "and"
        parts = re.split(r',\s*(?:and\s+)?|\s+and\s+', list_text)
        if len(parts) > 1:
            count = max(count, len([p for p in parts if len(p.strip()) > 3]))
    
    return count


def analyze_constraints(prompt: str) -> PromptConstraints:
    """
    Analyze a user prompt to extract implicit and explicit constraints.
    
    Args:
        prompt: The user's question/request
        
    Returns:
        PromptConstraints with detected requirements
    """
    constraints = PromptConstraints()
    text = prompt.lower()
    
    # ========================================================================
    # 1. Analyze search requirements
    # ========================================================================
    
    # Check for explicit "separate searches" requirement
    for pattern in SEPARATE_SEARCH_PATTERNS:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            # Try to extract count if present
            if match.lastindex and match.group(1):
                try:
                    constraints.min_searches = max(2, int(match.group(1)))
                except ValueError:
                    constraints.min_searches = 2
            else:
                constraints.min_searches = 2
            break
    
    # Extract required topics
    constraints.required_search_topics = extract_quoted_topics(prompt)
    
    # Infer minimum searches from topic count
    topic_count = count_topic_indicators(text)
    if topic_count > 1:
        constraints.min_searches = max(constraints.min_searches, min(topic_count, 5))
    
    # ========================================================================
    # 2. Analyze open_citation requirements
    # ========================================================================
    
    for pattern in OPEN_CITATION_PATTERNS:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            if match.lastindex and match.group(1):
                try:
                    # Try numeric first
                    constraints.min_open_citations = max(1, int(match.group(1)))
                except ValueError:
                    # Try word-to-number
                    word = match.group(1).lower()
                    constraints.min_open_citations = WORD_TO_NUM.get(word, 1)
            else:
                constraints.min_open_citations = 1
            break
    
    # ========================================================================
    # 3. Analyze exact quote requirements
    # ========================================================================
    
    for pattern in EXACT_QUOTE_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            constraints.requires_exact_quote = True
            constraints.min_open_citations = max(constraints.min_open_citations, 1)
            break
    
    # Extract what types of quotes are required
    for pattern, quote_type in QUOTE_TYPE_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            constraints.exact_quote_indicators.append(quote_type)
    
    # ========================================================================
    # 4. Analyze conflict resolution requirements
    # ========================================================================
    
    for pattern, rule in CONFLICT_RESOLUTION_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            constraints.requires_conflict_resolution = True
            if rule:
                constraints.conflict_resolution_rule = rule
            break
    
    # ========================================================================
    # 5. Analyze section requirements
    # ========================================================================
    
    for pattern in SECTION_PATTERNS:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            sections_text = match.group(1)
            # Split on commas and "and"
            sections = re.split(r',\s*(?:and\s+)?|\s+and\s+', sections_text)
            constraints.required_sections = [s.strip() for s in sections if s.strip()]
            break
    
    # ========================================================================
    # 6. Analyze insufficiency disclosure requirement
    # ========================================================================
    
    for pattern in INSUFFICIENCY_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            constraints.requires_insufficiency_disclosure = True
            break
    
    # ========================================================================
    # 7. Estimate answer complexity
    # ========================================================================
    
    # Base minimum length
    min_length = 100
    
    # Add for required sections (each section needs some content)
    if constraints.required_sections:
        min_length += len(constraints.required_sections) * 150
        constraints.is_complex_query = True
    
    # Add for exact quotes required
    if constraints.requires_exact_quote:
        min_length += 100 * len(constraints.exact_quote_indicators) if constraints.exact_quote_indicators else 100
    
    # Add for conflict resolution
    if constraints.requires_conflict_resolution:
        min_length += 100
    
    # Add for multiple searches expected
    if constraints.min_searches > 2:
        min_length += 100
        constraints.is_complex_query = True
    
    # Check for runbook/guide/comprehensive keywords
    complex_keywords = ['runbook', 'guide', 'comprehensive', 'authoritative', 'detailed', 'step-by-step', 'checklist']
    if any(kw in text for kw in complex_keywords):
        min_length += 200
        constraints.is_complex_query = True
    
    constraints.estimated_min_answer_length = min(min_length, 2000)  # Cap at 2000
    
    # Log detected constraints
    logger.info(
        f"Analyzed constraints: min_searches={constraints.min_searches}, "
        f"min_opens={constraints.min_open_citations}, "
        f"exact_quote={constraints.requires_exact_quote}, "
        f"topics={constraints.required_search_topics}"
    )
    
    return constraints


def summarize_constraints(constraints: PromptConstraints) -> str:
    """
    Generate a human-readable summary of constraints for the LLM.
    
    Used in reprompt messages.
    """
    parts = []
    
    if constraints.min_searches > 1:
        parts.append(f"Perform at least {constraints.min_searches} separate searches")
    
    if constraints.required_search_topics:
        topics_str = ", ".join(f'"{t}"' for t in constraints.required_search_topics[:5])
        parts.append(f"Search for these topics: {topics_str}")
    
    if constraints.min_open_citations > 0:
        parts.append(f"Open at least {constraints.min_open_citations} citation(s) to read full text")
    
    if constraints.requires_exact_quote:
        if constraints.exact_quote_indicators:
            indicators = ", ".join(constraints.exact_quote_indicators)
            parts.append(f"Quote exact text for: {indicators}")
        else:
            parts.append("Include verbatim quotes from the documents")
    
    if constraints.requires_conflict_resolution:
        rule = constraints.conflict_resolution_rule or "explicit rule"
        parts.append(f"Resolve conflicts using {rule}")
    
    if constraints.requires_insufficiency_disclosure:
        parts.append("Explicitly state 'Insufficient documentation' where information is missing")
    
    if not parts:
        return "No special constraints detected."
    
    return "REQUIREMENTS:\n- " + "\n- ".join(parts)
