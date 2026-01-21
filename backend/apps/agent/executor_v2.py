"""
Agent executor v2 - Robust bounded agent loop with validation gates.

This module implements a strict state machine for agent execution:
1. Analyze prompt constraints
2. Generate plan (2-5 steps)  
3. Execute tool loop with validation gates
4. Validate before allowing FINAL
5. Reprompt on constraint failures
6. Ground citations in actual retrieved data

Key improvements over v1:
- Strict JSON action format (TOOL_CALL / FINAL)
- Validator gate prevents premature finalization
- Constraint-aware reprompting
- Citation bookkeeping with grounding verification
- Exhaustion fallback with insufficiency disclosure

See OPERATIONS.md for limits and behavior.
"""
import json
import logging
import re
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, Tuple, Generator, Set
from enum import Enum

import httpx
from django.conf import settings

from apps.agent.planner import generate_plan, Plan
from apps.agent.constraints import analyze_constraints, summarize_constraints, PromptConstraints
from apps.agent.validator import (
    validate_agent_state,
    ValidationResult,
    AgentStateSnapshot,
    generate_reprompt_message,
)
from apps.agent.tools import (
    search_docs, 
    open_citation,
    SearchDocsOutput,
    OpenCitationOutput,
    SearchResult,
    ToolError,
    ToolValidationError,
    ToolAccessError,
)

logger = logging.getLogger(__name__)

# ============================================================================
# Hard limits (see OPERATIONS.md)
# ============================================================================
MAX_TOOL_CALLS = 5
MAX_ITERATIONS = 10  # More iterations to allow for reprompts
MAX_REPROMPTS = 3    # Max times we'll reprompt on validation failure
MAX_QUESTION_LENGTH = 1000
MAX_CONTEXT_CITATIONS = 5  # Rolling window for opened citations
MAX_CITATION_TEXT_FOR_LLM = 2000  # Chars per citation in prompt


class TraceType(str, Enum):
    PLAN = "plan"
    TOOL_CALL = "tool_call"
    VALIDATION = "validation"
    REPROMPT = "reprompt"
    FINAL = "final"
    ERROR = "error"


@dataclass
class TraceEntry:
    """Single entry in the execution trace."""
    type: TraceType
    tool: Optional[str] = None
    input: Optional[Dict[str, Any]] = None
    output_summary: Optional[str] = None
    steps: Optional[List[str]] = None
    notes: Optional[str] = None
    error: Optional[str] = None
    validation_errors: Optional[List[str]] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dict, omitting None fields for minimal trace."""
        result = {"type": self.type.value}
        if self.tool:
            result["tool"] = self.tool
        if self.input:
            result["input"] = self.input
        if self.output_summary:
            result["outputSummary"] = self.output_summary
        if self.steps:
            result["steps"] = self.steps
        if self.notes:
            result["notes"] = self.notes
        if self.error:
            result["error"] = self.error
        if self.validation_errors:
            result["validationErrors"] = self.validation_errors
        return result


@dataclass
class GroundedCitation:
    """A citation that we've verified exists in the DB."""
    doc_id: str
    chunk_id: str
    chunk_index: int
    snippet: str
    filename: str
    score: float = 0.0
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "docId": self.doc_id,
            "chunkId": self.chunk_id,
            "chunkIndex": self.chunk_index,
            "snippet": self.snippet,
            "documentTitle": self.filename,
            "score": self.score,
        }


@dataclass 
class Insufficiency:
    """Records what information could not be found."""
    section: str
    missing: str
    queries_tried: List[str] = field(default_factory=list)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "section": self.section,
            "missing": self.missing,
            "queriesTried": self.queries_tried,
        }


@dataclass
class AgentResult:
    """Result of agent execution."""
    answer: str
    citations: List[GroundedCitation]
    insufficiencies: List[Insufficiency] = field(default_factory=list)
    trace: List[TraceEntry] = field(default_factory=list)
    
    def to_dict(self, include_trace: bool = True) -> Dict[str, Any]:
        result = {
            "answer": self.answer,
            "citations": [c.to_dict() for c in self.citations],
        }
        if self.insufficiencies:
            result["insufficiencies"] = [i.to_dict() for i in self.insufficiencies]
        if include_trace:
            result["trace"] = [t.to_dict() for t in self.trace]
        return result


class AgentError(Exception):
    """Raised when agent execution fails."""
    pass


# ============================================================================
# Strict JSON Action Format
# ============================================================================

@dataclass
class ToolCallAction:
    """Parsed TOOL_CALL action."""
    tool: str  # "search_docs" or "open_citation"
    input: Dict[str, Any]


@dataclass
class FinalAction:
    """Parsed FINAL action."""
    answer: str
    used_citations: List[Dict[str, Any]]  # [{docId, chunkId, chunkIndex}]
    insufficiencies: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class ParsedAction:
    """Union type for parsed actions."""
    action_type: str  # "tool_call", "final", or "invalid"
    tool_call: Optional[ToolCallAction] = None
    final: Optional[FinalAction] = None
    error: Optional[str] = None


def parse_strict_json_action(response: str) -> ParsedAction:
    """
    Parse LLM response expecting strict JSON format.
    
    Expected formats:
    
    TOOL_CALL:
    {"type": "tool_call", "tool": "search_docs", "input": {"query": "..."}}
    
    FINAL:
    {"type": "final", "answer": "...", "used_citations": [...], "insufficiencies": [...]}
    
    Returns ParsedAction with appropriate type.
    """
    text = response.strip()
    
    # Try to extract JSON from the response
    # Handle case where LLM adds text before/after JSON
    json_match = re.search(r'\{[\s\S]*\}', text)
    if not json_match:
        return ParsedAction(
            action_type="invalid",
            error="No JSON object found in response"
        )
    
    try:
        data = json.loads(json_match.group())
    except json.JSONDecodeError as e:
        return ParsedAction(
            action_type="invalid",
            error=f"Invalid JSON: {str(e)[:50]}"
        )
    
    action_type = data.get('type', '').lower()
    
    if action_type == 'tool_call':
        tool = data.get('tool', '')
        tool_input = data.get('input', {})
        
        if tool not in ('search_docs', 'open_citation'):
            return ParsedAction(
                action_type="invalid",
                error=f"Unknown tool: {tool}. Use 'search_docs' or 'open_citation'."
            )
        
        if not isinstance(tool_input, dict):
            return ParsedAction(
                action_type="invalid",
                error="'input' must be an object"
            )
        
        return ParsedAction(
            action_type="tool_call",
            tool_call=ToolCallAction(tool=tool, input=tool_input)
        )
    
    elif action_type == 'final':
        answer = data.get('answer', '')
        used_citations = data.get('used_citations', [])
        insufficiencies = data.get('insufficiencies', [])
        
        if not isinstance(answer, str):
            return ParsedAction(
                action_type="invalid",
                error="'answer' must be a string"
            )
        
        if not isinstance(used_citations, list):
            used_citations = []
        
        if not isinstance(insufficiencies, list):
            insufficiencies = []
        
        return ParsedAction(
            action_type="final",
            final=FinalAction(
                answer=answer,
                used_citations=used_citations,
                insufficiencies=insufficiencies,
            )
        )
    
    else:
        # Try to infer type from structure
        if 'tool' in data and 'input' in data:
            tool = data.get('tool', '')
            if tool in ('search_docs', 'open_citation'):
                return ParsedAction(
                    action_type="tool_call",
                    tool_call=ToolCallAction(tool=tool, input=data.get('input', {}))
                )
        
        if 'answer' in data:
            return ParsedAction(
                action_type="final",
                final=FinalAction(
                    answer=data.get('answer', ''),
                    used_citations=data.get('used_citations', data.get('citations', [])),
                    insufficiencies=data.get('insufficiencies', []),
                )
            )
        
        return ParsedAction(
            action_type="invalid",
            error=f"Unknown action type: {action_type}. Use 'tool_call' or 'final'."
        )


# ============================================================================
# Enhanced Agent State
# ============================================================================

@dataclass
class SearchResultItem:
    """Compressed search result for context."""
    doc_id: str
    chunk_id: str
    chunk_index: int
    snippet: str
    score: float
    filename: str
    query: str  # The query that produced this result


@dataclass 
class OpenedCitation:
    """An opened citation with text."""
    doc_id: str
    chunk_id: str
    chunk_index: int
    text: str
    filename: str
    citation_num: int  # [1], [2], etc.


class AgentState:
    """
    Enhanced agent state with constraint tracking.
    
    Tracks:
    - Tool calls and budget
    - Search queries performed
    - Citations opened
    - Notes and insufficiencies
    """
    
    def __init__(self, constraints: PromptConstraints):
        self.constraints = constraints
        self.tool_calls_used: int = 0
        self.search_results: List[SearchResultItem] = []
        self.opened_citations: List[OpenedCitation] = []
        self.notes: List[str] = []
        self.insufficiencies: List[Insufficiency] = []
        self._citation_counter: int = 0
        self._search_queries: List[str] = []  # Track queries for validation
    
    @property
    def remaining_tool_budget(self) -> int:
        return MAX_TOOL_CALLS - self.tool_calls_used
    
    @property
    def search_count(self) -> int:
        return len(self._search_queries)
    
    def add_search_results(self, query: str, output: SearchDocsOutput, filename_map: Dict[str, str]):
        """Add search results with query tracking."""
        self._search_queries.append(query)
        
        for r in output.results:
            self.search_results.append(SearchResultItem(
                doc_id=r.doc_id,
                chunk_id=r.chunk_id,
                chunk_index=r.chunk_index,
                snippet=r.snippet[:250],  # Cap snippet length
                score=r.score,
                filename=filename_map.get(r.doc_id, "document"),
                query=query,
            ))
    
    def add_opened_citation(self, output: OpenCitationOutput):
        """Add opened citation."""
        self._citation_counter += 1
        
        # Keep full text for validation (compressed version for LLM prompt)
        text = output.text
        if len(text) > MAX_CITATION_TEXT_FOR_LLM:
            text = text[:MAX_CITATION_TEXT_FOR_LLM] + "..."
        
        self.opened_citations.append(OpenedCitation(
            doc_id=output.doc_id,
            chunk_id=output.chunk_id,
            chunk_index=output.chunk_index,
            text=text,
            filename=output.filename,
            citation_num=self._citation_counter
        ))
        
        # Rolling window: keep only last N
        if len(self.opened_citations) > MAX_CONTEXT_CITATIONS:
            self.opened_citations = self.opened_citations[-MAX_CONTEXT_CITATIONS:]
    
    def add_insufficiency(self, section: str, missing: str):
        """Record an information gap."""
        self.insufficiencies.append(Insufficiency(
            section=section,
            missing=missing,
            queries_tried=list(self._search_queries),
        ))
    
    def to_snapshot(self) -> AgentStateSnapshot:
        """Create a snapshot for validation."""
        snapshot = AgentStateSnapshot()
        snapshot.search_count = self.search_count
        snapshot.search_queries = list(self._search_queries)
        snapshot.open_citation_count = len(self.opened_citations)
        snapshot.opened_citation_texts = [c.text for c in self.opened_citations]
        snapshot.opened_citation_ids = [
            {'docId': c.doc_id, 'chunkId': c.chunk_id}
            for c in self.opened_citations
        ]
        snapshot.search_snippets = [r.snippet for r in self.search_results]
        return snapshot
    
    def build_context_string(self) -> str:
        """Build context string for LLM prompt."""
        parts = []
        
        # Search results summary (grouped by query)
        if self.search_results:
            parts.append("=== SEARCH RESULTS ===")
            seen_queries = set()
            for r in self.search_results:
                if r.query not in seen_queries:
                    parts.append(f"\nQuery: \"{r.query}\"")
                    seen_queries.add(r.query)
                # IMPORTANT: Show FULL docId and chunkId so LLM can use them in open_citation
                parts.append(f"  - {r.filename}: \"{r.snippet[:100]}...\"\n    docId={r.doc_id}\n    chunkId={r.chunk_id}")
        
        # Opened citations (full text)
        if self.opened_citations:
            parts.append("\n=== OPENED CITATIONS (Full Text) ===")
            for c in self.opened_citations:
                parts.append(f"\n[{c.citation_num}] {c.filename} (chunk {c.chunk_index}):\n{c.text}")
        
        # Notes
        if self.notes:
            parts.append("\n=== NOTES ===")
            for note in self.notes[-3:]:
                parts.append(f"- {note}")
        
        return "\n".join(parts) if parts else "(No information gathered yet)"
    
    def build_available_citations_list(self) -> str:
        """List available citations for FINAL action."""
        if not self.opened_citations:
            return "(No citations opened yet)"
        
        lines = []
        for c in self.opened_citations:
            lines.append(
                f"[{c.citation_num}] docId={c.doc_id}, chunkId={c.chunk_id}, "
                f"chunkIndex={c.chunk_index}, file={c.filename}"
            )
        return "\n".join(lines)


# ============================================================================
# System Prompts
# ============================================================================

TOOL_LOOP_SYSTEM_PROMPT = """You are an AI assistant executing a plan to answer questions using document search tools.

STRICT OUTPUT FORMAT:
You MUST output EXACTLY ONE valid JSON object per response. No text before or after.

For tool calls:
{
  "type": "tool_call",
  "tool": "search_docs" | "open_citation",
  "input": { ... }
}

For final answer (ONLY when you have gathered enough information):
{
  "type": "final",
  "answer": "Your answer with [1], [2] citation markers",
  "used_citations": [
    {"docId": "...", "chunkId": "...", "chunkIndex": 0}
  ],
  "insufficiencies": [
    {"section": "...", "missing": "...", "queries_tried": ["..."]}
  ]
}

AVAILABLE TOOLS:
1. search_docs - Search documents
   Input: {"query": "search terms"}
   
2. open_citation - Read full text of a chunk (REQUIRED before citing)
   Input: {"docId": "FULL-UUID-HERE", "chunkId": "FULL-UUID-HERE"}
   IMPORTANT: You MUST use the COMPLETE UUID strings shown in search results.
   UUIDs look like: "c5bd8bfc-1234-5678-abcd-1234567890ab" (36 characters with dashes)
   Do NOT truncate or shorten the UUIDs!

CRITICAL RULES:
1. You MUST call open_citation before you can cite a source in your final answer
2. Use the FULL docId and chunkId from search results - do not truncate!
3. Citation numbers [1], [2] must match opened citations
4. Do NOT include information not found in opened citations
5. If information is missing, include it in "insufficiencies"
6. Say "I don't know based on the provided documents" if nothing relevant found
7. NEVER invent tools, commands, or procedures not in the documents"""


def build_iteration_prompt(
    question: str,
    plan_summary: str,
    constraints: PromptConstraints,
    state: AgentState,
    step_num: int,
    total_steps: int,
    reprompt_message: Optional[str] = None,
) -> str:
    """Build the prompt for each iteration."""
    
    constraint_summary = summarize_constraints(constraints)
    
    prompt_parts = [
        TOOL_LOOP_SYSTEM_PROMPT,
        "",
        f"QUESTION: {question}",
        "",
        f"PLAN: {plan_summary}",
        f"CURRENT STEP: {step_num} of {total_steps}",
        "",
        constraint_summary,
        "",
        f"TOOL BUDGET: {state.remaining_tool_budget} calls remaining (max {MAX_TOOL_CALLS})",
        f"SEARCHES DONE: {state.search_count}",
        f"CITATIONS OPENED: {len(state.opened_citations)}",
        "",
        "CURRENT CONTEXT:",
        state.build_context_string(),
        "",
    ]
    
    if state.opened_citations:
        prompt_parts.extend([
            "AVAILABLE CITATIONS FOR FINAL:",
            state.build_available_citations_list(),
            "",
        ])
    
    if reprompt_message:
        prompt_parts.extend([
            "=== CORRECTION REQUIRED ===",
            reprompt_message,
            "===========================",
            "",
        ])
    
    prompt_parts.append("Output your next action as JSON:")
    
    return "\n".join(prompt_parts)


SYNTHESIS_PROMPT = """Based on the gathered information, answer the question.

STRICT RULES:
1. Use ONLY the provided context - never make up information
2. Cite sources using [1], [2] notation matching the citation numbers below
3. If the context doesn't answer the question, say: "I don't know based on the provided documents."
4. If some information is missing, explicitly state "Insufficient documentation" for those parts
5. Be factual and concise

Question: {question}

Available sources:
{context}

Answer (use [1], [2] etc. to cite sources):"""


# ============================================================================
# Tool Execution
# ============================================================================

def execute_tool(
    tool_call: ToolCallAction, 
    user_id: str,
    state: AgentState,
    trace: List[TraceEntry],
    rerank: bool = False
) -> Tuple[bool, str]:
    """
    Execute a single tool and update state.
    
    Returns:
        (success: bool, message: str)
    """
    state.tool_calls_used += 1
    
    try:
        if tool_call.tool == 'search_docs':
            query = tool_call.input.get('query', '')
            if not query:
                return False, "Query is required"
            
            result = search_docs(query, user_id, rerank=rerank)
            
            # Get filename mapping
            filename_map = {}
            from apps.docs.models import Document
            doc_ids = [r.doc_id for r in result.results]
            if doc_ids:
                docs = Document.objects.filter(id__in=doc_ids)
                filename_map = {str(d.id): d.filename for d in docs}
            
            state.add_search_results(query, result, filename_map)
            
            trace.append(TraceEntry(
                type=TraceType.TOOL_CALL,
                tool='search_docs',
                input={'query': query[:100]},
                output_summary=result.summary()
            ))
            
            return True, result.summary()
            
        elif tool_call.tool == 'open_citation':
            doc_id = tool_call.input.get('docId', '')
            chunk_id = tool_call.input.get('chunkId', '')
            if not doc_id or not chunk_id:
                return False, "docId and chunkId are required"
            
            result = open_citation(doc_id, chunk_id, user_id)
            state.add_opened_citation(result)
            
            trace.append(TraceEntry(
                type=TraceType.TOOL_CALL,
                tool='open_citation',
                input={'docId': doc_id[:20], 'chunkId': chunk_id[:20]},
                output_summary=result.summary()
            ))
            
            return True, result.summary()
            
        else:
            trace.append(TraceEntry(
                type=TraceType.ERROR,
                error=f"Unknown tool: {tool_call.tool}"
            ))
            return False, f"Unknown tool: {tool_call.tool}"
            
    except ToolValidationError as e:
        trace.append(TraceEntry(
            type=TraceType.ERROR,
            tool=tool_call.tool,
            error=str(e)[:100]
        ))
        state.notes.append(f"Tool error: {e}")
        return False, str(e)
        
    except ToolAccessError as e:
        trace.append(TraceEntry(
            type=TraceType.ERROR,
            tool=tool_call.tool,
            error=str(e)[:100]
        ))
        state.notes.append(f"Access denied: {e}")
        return False, str(e)
        
    except ToolError as e:
        trace.append(TraceEntry(
            type=TraceType.ERROR,
            tool=tool_call.tool,
            error=str(e)[:100]
        ))
        return False, str(e)


# ============================================================================
# LLM Integration
# ============================================================================

def call_llm(prompt: str, max_tokens: int = 600) -> str:
    """Call LLM and return response text."""
    ollama_url = getattr(settings, 'OLLAMA_BASE_URL', 'http://ollama:11434')
    chat_model = getattr(settings, 'OLLAMA_CHAT_MODEL', 'llama3.2')
    chat_timeout = getattr(settings, 'OLLAMA_CHAT_TIMEOUT', 600)
    
    logger.debug(f"Calling LLM (model={chat_model}, timeout={chat_timeout}s)")
    
    with httpx.Client(timeout=float(chat_timeout)) as client:
        response = client.post(
            f"{ollama_url}/api/chat",
            json={
                "model": chat_model,
                "messages": [{"role": "user", "content": prompt}],
                "stream": False,
                "options": {
                    "temperature": 0.1,  # Low temp for predictable JSON
                    "num_predict": max_tokens,
                }
            }
        )
        response.raise_for_status()
        data = response.json()
        return data.get("message", {}).get("content", "")


# ============================================================================
# Citation Grounding
# ============================================================================

def ground_citations_from_state(
    final_action: FinalAction,
    state: AgentState
) -> Tuple[str, List[GroundedCitation]]:
    """
    Map citation references to actual opened citations.
    
    Only accepts citations that were actually opened via open_citation.
    Strips hallucinated references.
    """
    grounded: List[GroundedCitation] = []
    used_ids: Set[Tuple[str, str]] = set()
    
    # Build lookup from opened citations
    citation_by_num: Dict[int, OpenedCitation] = {
        c.citation_num: c for c in state.opened_citations
    }
    citation_by_id: Dict[Tuple[str, str], OpenedCitation] = {
        (c.doc_id, c.chunk_id): c for c in state.opened_citations
    }
    
    # Process explicit used_citations from FINAL
    for cite_ref in final_action.used_citations:
        if isinstance(cite_ref, dict):
            doc_id = cite_ref.get('docId', '')
            chunk_id = cite_ref.get('chunkId', '')
            key = (doc_id, chunk_id)
            
            if key in citation_by_id and key not in used_ids:
                c = citation_by_id[key]
                grounded.append(GroundedCitation(
                    doc_id=c.doc_id,
                    chunk_id=c.chunk_id,
                    chunk_index=c.chunk_index,
                    snippet=c.text[:200],
                    filename=c.filename,
                ))
                used_ids.add(key)
    
    # Find [N] markers in answer and validate
    answer = final_action.answer
    cleaned_answer = answer
    found_refs = re.findall(r'\[(\d+)\]', answer)
    
    for ref_str in found_refs:
        ref = int(ref_str)
        if ref in citation_by_num:
            c = citation_by_num[ref]
            key = (c.doc_id, c.chunk_id)
            if key not in used_ids:
                grounded.append(GroundedCitation(
                    doc_id=c.doc_id,
                    chunk_id=c.chunk_id,
                    chunk_index=c.chunk_index,
                    snippet=c.text[:200],
                    filename=c.filename,
                ))
                used_ids.add(key)
        else:
            # Hallucinated reference - strip it
            cleaned_answer = re.sub(rf'\[{ref}\]', '', cleaned_answer)
    
    # Clean up double spaces
    cleaned_answer = re.sub(r'\s+', ' ', cleaned_answer).strip()
    
    return cleaned_answer, grounded


def fallback_citations_from_search(state: AgentState) -> List[GroundedCitation]:
    """
    Create citations from search results when no citations were opened.
    
    Used as last resort when agent fails to open citations.
    """
    grounded = []
    for r in state.search_results[:3]:
        grounded.append(GroundedCitation(
            doc_id=r.doc_id,
            chunk_id=r.chunk_id,
            chunk_index=r.chunk_index,
            snippet=r.snippet,
            filename=r.filename,
            score=r.score,
        ))
    return grounded


# ============================================================================
# Main Agent Loop
# ============================================================================

def run_agent_v2(question: str, user_id: str, rerank: bool = False) -> AgentResult:
    """
    Execute the bounded agent loop with validation gates.
    
    1. Analyze prompt constraints
    2. Generate plan (2-5 steps)
    3. Execute tool loop with TOOL_CALL/FINAL protocol
    4. Validate before accepting FINAL
    5. Reprompt on constraint failures (max 3 times)
    6. Ground citations in actual retrieved data
    
    Args:
        question: User's question (max 1000 chars)
        user_id: Keycloak subject ID
        
    Returns:
        AgentResult with answer, citations, insufficiencies, and trace
    """
    trace: List[TraceEntry] = []
    
    # Validate and truncate question
    if not question or not question.strip():
        raise AgentError("Question is required")
    
    question = question.strip()
    if len(question) > MAX_QUESTION_LENGTH:
        logger.warning(f"Question truncated from {len(question)} to {MAX_QUESTION_LENGTH}")
        question = question[:MAX_QUESTION_LENGTH]
    
    logger.info(f"Agent v2 starting for question: {question[:100]}...")
    
    # ========================================================================
    # Step 1: Analyze constraints
    # ========================================================================
    constraints = analyze_constraints(question)
    
    # ========================================================================
    # Step 2: Generate plan
    # ========================================================================
    plan = generate_plan(question)
    
    trace.append(TraceEntry(
        type=TraceType.PLAN,
        steps=plan.steps,
        notes="Fallback plan used" if plan.is_fallback else None
    ))
    
    plan_summary = "; ".join(plan.steps[:3])
    if len(plan.steps) > 3:
        plan_summary += f"... (+{len(plan.steps)-3} more)"
    
    # ========================================================================
    # Step 3: Initialize state
    # ========================================================================
    state = AgentState(constraints)
    
    # ========================================================================
    # Step 4: Execute tool loop
    # ========================================================================
    iteration = 0
    reprompt_count = 0
    json_error_count = 0
    final_action: Optional[FinalAction] = None
    reprompt_message: Optional[str] = None
    
    while iteration < MAX_ITERATIONS and state.tool_calls_used < MAX_TOOL_CALLS:
        iteration += 1
        
        # Build prompt
        prompt = build_iteration_prompt(
            question=question,
            plan_summary=plan_summary,
            constraints=constraints,
            state=state,
            step_num=min(iteration, len(plan.steps)),
            total_steps=len(plan.steps),
            reprompt_message=reprompt_message,
        )
        
        reprompt_message = None  # Clear for next iteration
        
        # Get LLM response
        try:
            response = call_llm(prompt, max_tokens=600)
        except Exception as e:
            logger.error(f"LLM call failed: {e}")
            trace.append(TraceEntry(
                type=TraceType.ERROR,
                error=f"LLM error: {str(e)[:100]}"
            ))
            break
        
        # Parse action
        action = parse_strict_json_action(response)
        
        if action.action_type == "invalid":
            json_error_count += 1
            if json_error_count >= 2:
                state.notes.append(f"Model output malformed: {action.error}")
                break
            reprompt_message = f"Invalid JSON: {action.error}\nOutput ONLY valid JSON."
            continue
        
        if action.action_type == "tool_call" and action.tool_call:
            if state.remaining_tool_budget <= 0:
                state.notes.append("Tool budget exhausted")
                break
            
            success, message = execute_tool(
                action.tool_call,
                user_id,
                state,
                trace,
                rerank=rerank
            )
            
            if not success:
                state.notes.append(f"Tool failed: {message}")
            
            continue  # Get next action
        
        if action.action_type == "final" and action.final:
            # ================================================================
            # VALIDATION GATE
            # ================================================================
            snapshot = state.to_snapshot()
            
            # Extract citation refs for validation
            citation_refs = []
            for ref_str in re.findall(r'\[(\d+)\]', action.final.answer):
                citation_refs.append(int(ref_str))
            
            validation = validate_agent_state(
                answer=action.final.answer,
                citation_refs=citation_refs,
                constraints=constraints,
                snapshot=snapshot,
                insufficiencies=[i.to_dict() for i in state.insufficiencies],
            )
            
            if validation.is_valid:
                # Accept the final answer
                final_action = action.final
                trace.append(TraceEntry(
                    type=TraceType.FINAL,
                    notes=f"Validated with {len(state.opened_citations)} citations"
                ))
                break
            else:
                # Validation failed - reprompt
                reprompt_count += 1
                
                trace.append(TraceEntry(
                    type=TraceType.VALIDATION,
                    validation_errors=[e.message for e in validation.errors],
                    notes=f"Validation failed (attempt {reprompt_count}/{MAX_REPROMPTS})"
                ))
                
                if reprompt_count >= MAX_REPROMPTS:
                    # Max reprompts reached - accept with warnings
                    logger.warning("Max reprompts reached, accepting answer with warnings")
                    final_action = action.final
                    trace.append(TraceEntry(
                        type=TraceType.FINAL,
                        notes="Accepted after max reprompts (may have validation issues)"
                    ))
                    break
                
                reprompt_message = generate_reprompt_message(
                    validation,
                    constraints,
                    state.remaining_tool_budget,
                )
                
                trace.append(TraceEntry(
                    type=TraceType.REPROMPT,
                    notes=validation.error_summary()[:200]
                ))
                
                continue
    
    # ========================================================================
    # Step 5: Handle exhaustion / synthesis
    # ========================================================================
    if final_action is None:
        # No valid FINAL was produced - synthesize from gathered context
        if not state.opened_citations and not state.search_results:
            cleaned_answer = "I don't know based on the provided documents."
            grounded_citations = []
            trace.append(TraceEntry(
                type=TraceType.FINAL,
                notes="No relevant sources found"
            ))
        else:
            # Build synthesis prompt
            context_parts = []
            for c in state.opened_citations:
                context_parts.append(f"[{c.citation_num}] {c.filename} (chunk {c.chunk_index}):\n{c.text}")
            
            if not context_parts and state.search_results:
                for i, r in enumerate(state.search_results[:3], 1):
                    context_parts.append(f"[{i}] {r.filename}:\n{r.snippet}")
            
            synthesis_prompt = SYNTHESIS_PROMPT.format(
                question=question,
                context="\n\n".join(context_parts)
            )
            
            try:
                synthesis_response = call_llm(synthesis_prompt, max_tokens=600)
                cleaned_answer = synthesis_response.strip()
                
                # Ground citations from synthesis
                if state.opened_citations:
                    grounded_citations = [
                        GroundedCitation(
                            doc_id=c.doc_id,
                            chunk_id=c.chunk_id,
                            chunk_index=c.chunk_index,
                            snippet=c.text[:200],
                            filename=c.filename,
                        )
                        for c in state.opened_citations
                    ]
                else:
                    grounded_citations = fallback_citations_from_search(state)
                
                trace.append(TraceEntry(
                    type=TraceType.FINAL,
                    notes=f"Synthesized from {len(grounded_citations)} sources (exhaustion fallback)"
                ))
            except Exception as e:
                logger.error(f"Synthesis failed: {e}")
                cleaned_answer = "I encountered an error generating the answer."
                grounded_citations = []
                trace.append(TraceEntry(
                    type=TraceType.ERROR,
                    error=f"Synthesis failed: {str(e)[:100]}"
                ))
    else:
        # Ground citations from FINAL action
        cleaned_answer, grounded_citations = ground_citations_from_state(final_action, state)
        
        # If no grounded citations but we have search results, use those
        if not grounded_citations and state.search_results:
            grounded_citations = fallback_citations_from_search(state)
    
    # ========================================================================
    # Step 6: Build result
    # ========================================================================
    insufficiencies = state.insufficiencies
    
    # Add insufficiencies from FINAL action
    if final_action and final_action.insufficiencies:
        for insuff in final_action.insufficiencies:
            if isinstance(insuff, dict):
                insufficiencies.append(Insufficiency(
                    section=insuff.get('section', 'Unknown'),
                    missing=insuff.get('missing', ''),
                    queries_tried=insuff.get('queries_tried', insuff.get('queriesTried', [])),
                ))
    
    logger.info(
        f"Agent v2 completed: {state.tool_calls_used} tool calls, "
        f"{len(grounded_citations)} citations, {len(insufficiencies)} insufficiencies"
    )
    
    return AgentResult(
        answer=cleaned_answer,
        citations=grounded_citations,
        insufficiencies=insufficiencies,
        trace=trace
    )


# ============================================================================
# Streaming Generator Version (for SSE)
# ============================================================================

def run_agent_v2_streaming(question: str, user_id: str, rerank: bool = False) -> Generator:
    """
    Execute the bounded agent loop with streaming events.
    
    Yields TraceEntry objects as they occur, then yields final AgentResult.
    
    Args:
        question: User's question (max 1000 chars)
        user_id: Keycloak subject ID
        rerank: Whether to apply cross-encoder reranking
        
    Yields:
        TraceEntry objects during execution
        AgentResult as final yield
    """
    trace: List[TraceEntry] = []
    
    if not question or not question.strip():
        raise AgentError("Question is required")
    
    question = question.strip()
    if len(question) > MAX_QUESTION_LENGTH:
        question = question[:MAX_QUESTION_LENGTH]
    
    logger.info(f"Agent v2 (streaming) starting: {question[:100]}...")
    
    # Analyze constraints
    constraints = analyze_constraints(question)
    
    # Generate plan
    plan = generate_plan(question)
    
    plan_entry = TraceEntry(
        type=TraceType.PLAN,
        steps=plan.steps,
        notes="Fallback plan used" if plan.is_fallback else None
    )
    trace.append(plan_entry)
    yield plan_entry
    
    plan_summary = "; ".join(plan.steps[:3])
    if len(plan.steps) > 3:
        plan_summary += f"... (+{len(plan.steps)-3} more)"
    
    # Initialize state
    state = AgentState(constraints)
    
    # Execute tool loop
    iteration = 0
    reprompt_count = 0
    json_error_count = 0
    final_action: Optional[FinalAction] = None
    reprompt_message: Optional[str] = None
    
    while iteration < MAX_ITERATIONS and state.tool_calls_used < MAX_TOOL_CALLS:
        iteration += 1
        
        # Yield thinking event
        thinking_entry = TraceEntry(
            type=TraceType.TOOL_CALL,
            tool="thinking",
            notes=f"Step {iteration}: Analyzing..."
        )
        yield thinking_entry
        
        prompt = build_iteration_prompt(
            question=question,
            plan_summary=plan_summary,
            constraints=constraints,
            state=state,
            step_num=min(iteration, len(plan.steps)),
            total_steps=len(plan.steps),
            reprompt_message=reprompt_message,
        )
        
        reprompt_message = None
        
        try:
            response = call_llm(prompt, max_tokens=600)
        except Exception as e:
            error_entry = TraceEntry(
                type=TraceType.ERROR,
                error=f"LLM error: {str(e)[:100]}"
            )
            trace.append(error_entry)
            yield error_entry
            break
        
        action = parse_strict_json_action(response)
        
        if action.action_type == "invalid":
            json_error_count += 1
            if json_error_count >= 2:
                break
            reprompt_message = f"Invalid JSON: {action.error}"
            continue
        
        if action.action_type == "tool_call" and action.tool_call:
            if state.remaining_tool_budget <= 0:
                break
            
            success, message = execute_tool(
                action.tool_call,
                user_id,
                state,
                trace,
                rerank=rerank
            )
            
            if trace:
                yield trace[-1]
            
            continue
        
        if action.action_type == "final" and action.final:
            snapshot = state.to_snapshot()
            citation_refs = [int(r) for r in re.findall(r'\[(\d+)\]', action.final.answer)]
            
            validation = validate_agent_state(
                answer=action.final.answer,
                citation_refs=citation_refs,
                constraints=constraints,
                snapshot=snapshot,
            )
            
            if validation.is_valid:
                final_action = action.final
                final_entry = TraceEntry(
                    type=TraceType.FINAL,
                    notes=f"Validated with {len(state.opened_citations)} citations"
                )
                trace.append(final_entry)
                yield final_entry
                break
            else:
                reprompt_count += 1
                
                validation_entry = TraceEntry(
                    type=TraceType.VALIDATION,
                    validation_errors=[e.message for e in validation.errors],
                    notes=f"Validation failed ({reprompt_count}/{MAX_REPROMPTS})"
                )
                trace.append(validation_entry)
                yield validation_entry
                
                if reprompt_count >= MAX_REPROMPTS:
                    final_action = action.final
                    break
                
                reprompt_message = generate_reprompt_message(
                    validation, constraints, state.remaining_tool_budget
                )
                continue
    
    # Handle exhaustion / synthesis
    if final_action is None:
        if not state.opened_citations and not state.search_results:
            cleaned_answer = "I don't know based on the provided documents."
            grounded_citations = []
        else:
            synth_entry = TraceEntry(
                type=TraceType.TOOL_CALL,
                tool="synthesizing",
                notes="Generating final answer..."
            )
            yield synth_entry
            
            context_parts = []
            for c in state.opened_citations:
                context_parts.append(f"[{c.citation_num}] {c.filename}:\n{c.text}")
            
            if not context_parts and state.search_results:
                for i, r in enumerate(state.search_results[:3], 1):
                    context_parts.append(f"[{i}] {r.filename}:\n{r.snippet}")
            
            try:
                synthesis_response = call_llm(
                    SYNTHESIS_PROMPT.format(question=question, context="\n\n".join(context_parts)),
                    max_tokens=600
                )
                cleaned_answer = synthesis_response.strip()
                grounded_citations = (
                    [GroundedCitation(
                        doc_id=c.doc_id, chunk_id=c.chunk_id, chunk_index=c.chunk_index,
                        snippet=c.text[:200], filename=c.filename
                    ) for c in state.opened_citations]
                    or fallback_citations_from_search(state)
                )
            except Exception as e:
                cleaned_answer = "I encountered an error generating the answer."
                grounded_citations = []
    else:
        cleaned_answer, grounded_citations = ground_citations_from_state(final_action, state)
        if not grounded_citations and state.search_results:
            grounded_citations = fallback_citations_from_search(state)
    
    # Build and yield final result
    yield AgentResult(
        answer=cleaned_answer,
        citations=grounded_citations,
        insufficiencies=state.insufficiencies,
        trace=trace
    )
