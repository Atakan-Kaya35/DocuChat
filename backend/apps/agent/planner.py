"""
Agent planning module.

Generates bounded 2-5 step plans for answering questions.
Validates plan output and falls back to safe default if parsing fails.

Supports multiple LLM providers via the llm_client abstraction.
"""
import json
import logging
import re
from dataclasses import dataclass, field
from typing import List, Optional

from django.conf import settings

from apps.rag.llm_client import get_llm_client, LLMMessage, LLMError

logger = logging.getLogger(__name__)

# Plan limits
MIN_PLAN_STEPS = 2
MAX_PLAN_STEPS = 5

# Default fallback plan when parsing fails
DEFAULT_PLAN = [
    "Search documents for relevant information",
    "Open the best matching citations",
    "Synthesize answer with citations"
]


PLAN_SYSTEM_PROMPT = """You are a planning assistant for a document Q&A system.

Your task is to create a SHORT, FOCUSED plan to answer the user's question using their uploaded documents.

AVAILABLE TOOLS:
1. search_docs(query) - Search the user's documents for relevant content. Returns top 5 matching chunks.
2. open_citation(docId, chunkId) - Retrieve the full text of a specific chunk for detailed reading.

RULES:
1. Output EXACTLY 2-5 steps. No more, no less.
2. Each step must be ONE clear, actionable instruction.
3. Steps should reference tools by name when a tool is needed.
4. The final step should always be about synthesizing/answering.
5. Be specific about what to search for.
6. Do NOT include introductions, explanations, or commentary.

OUTPUT FORMAT:
Return a JSON array of strings, each string being one step.

Example:
["Search for 'quarterly revenue figures'", "Open the top 2 citations to read details", "Synthesize the answer with specific numbers and citations"]

Now create a plan for the following question:"""


@dataclass
class Plan:
    """Represents an agent execution plan."""
    steps: List[str]
    is_fallback: bool = False
    
    def to_dict(self) -> dict:
        return {
            'type': 'plan',
            'steps': self.steps,
        }


class PlanningError(Exception):
    """Raised when planning fails."""
    pass


def parse_plan_response(response_text: str) -> List[str]:
    """
    Parse the LLM response to extract plan steps.
    
    Tries multiple parsing strategies:
    1. JSON array
    2. Numbered list
    3. Bullet points
    
    Args:
        response_text: Raw LLM output
        
    Returns:
        List of step strings
        
    Raises:
        ValueError: If parsing fails
    """
    text = response_text.strip()
    
    # Strategy 1: Try JSON array
    try:
        # Find JSON array in response (may have preamble text)
        json_match = re.search(r'\[[\s\S]*\]', text)
        if json_match:
            steps = json.loads(json_match.group())
            if isinstance(steps, list) and all(isinstance(s, str) for s in steps):
                return [s.strip() for s in steps if s.strip()]
    except json.JSONDecodeError:
        pass
    
    # Strategy 2: Numbered list (1. Step one, 2. Step two)
    numbered_pattern = r'^\s*\d+[\.\)]\s*(.+)$'
    numbered_steps = re.findall(numbered_pattern, text, re.MULTILINE)
    if numbered_steps and len(numbered_steps) >= MIN_PLAN_STEPS:
        return [s.strip() for s in numbered_steps if s.strip()]
    
    # Strategy 3: Bullet points (- Step or * Step)
    bullet_pattern = r'^\s*[-\*•]\s*(.+)$'
    bullet_steps = re.findall(bullet_pattern, text, re.MULTILINE)
    if bullet_steps and len(bullet_steps) >= MIN_PLAN_STEPS:
        return [s.strip() for s in bullet_steps if s.strip()]
    
    # Strategy 4: Line-by-line (if each line looks like a step)
    lines = [line.strip() for line in text.split('\n') if line.strip()]
    # Filter out lines that look like meta-commentary
    step_lines = [
        line for line in lines 
        if not line.lower().startswith(('here', 'plan:', 'steps:', 'the plan', 'i will', 'let me'))
        and len(line) > 10
    ]
    if step_lines and len(step_lines) >= MIN_PLAN_STEPS:
        return step_lines
    
    raise ValueError(f"Could not parse plan from response: {text[:200]}")


def validate_plan(steps: List[str]) -> List[str]:
    """
    Validate and normalize plan steps.
    
    Args:
        steps: List of step strings
        
    Returns:
        Validated list of steps (2-5 items)
        
    Raises:
        ValueError: If plan is invalid
    """
    # Filter empty steps
    steps = [s.strip() for s in steps if s and s.strip()]
    
    if len(steps) < MIN_PLAN_STEPS:
        raise ValueError(f"Plan has fewer than {MIN_PLAN_STEPS} steps")
    
    # Truncate if too many steps
    if len(steps) > MAX_PLAN_STEPS:
        logger.warning(f"Plan truncated from {len(steps)} to {MAX_PLAN_STEPS} steps")
        steps = steps[:MAX_PLAN_STEPS]
    
    # Basic validation: each step should be reasonably actionable
    for i, step in enumerate(steps):
        if len(step) < 5:
            raise ValueError(f"Step {i+1} is too short: '{step}'")
        if len(step) > 500:
            steps[i] = step[:500] + "..."
    
    return steps


def generate_plan(question: str) -> Plan:
    """
    Generate an execution plan for answering a question.
    
    Calls the LLM to create a 2-5 step plan, with fallback to default
    if parsing fails.
    
    Args:
        question: The user's question
        
    Returns:
        Plan object with steps
    """
    if not question or not question.strip():
        logger.warning("Empty question, using default plan")
        return Plan(steps=DEFAULT_PLAN.copy(), is_fallback=True)
    
    prompt = f"{PLAN_SYSTEM_PROMPT}\n\nQuestion: {question}"
    
    logger.info(f"Generating plan for question: {question[:100]}...")
    
    try:
        client = get_llm_client()
        messages = [LLMMessage(role="user", content=prompt)]
        
        response = client.chat(
            messages,
            temperature=0.3,  # Low for consistent plans
            max_tokens=300,   # Plans are short
        )
        
        content = response.content
        if not content:
            raise PlanningError("Empty response from LLM")
        
        logger.debug(f"Plan LLM response: {content}")
        
        # Parse and validate
        steps = parse_plan_response(content)
        steps = validate_plan(steps)
        
        logger.info(f"Generated plan with {len(steps)} steps")
        return Plan(steps=steps, is_fallback=False)
        
    except LLMError as e:
        logger.error(f"LLM request failed: {e}")
    except ValueError as e:
        logger.warning(f"Plan parsing failed: {e}")
    except Exception as e:
        logger.exception(f"Unexpected error in planning: {e}")
    
    # Fallback to default plan
    logger.info("Using fallback plan")
    return Plan(steps=DEFAULT_PLAN.copy(), is_fallback=True)
