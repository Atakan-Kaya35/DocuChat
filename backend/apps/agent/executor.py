"""
Agent executor.

Runs the bounded agent loop with strict action format:
1. Generate plan (2-5 steps)
2. Execute tools via TOOL_CALL / FINAL protocol
3. Synthesize final answer with grounded citations

See OPERATIONS.md for limits and behavior.
"""
import json
import logging
import re
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, Tuple
from enum import Enum

import httpx
from django.conf import settings

from apps.agent.planner import generate_plan, Plan
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
MAX_ITERATIONS = 5
MAX_QUESTION_LENGTH = 1000
MAX_CONTEXT_CITATIONS = 3  # Rolling window for opened citations
MAX_CITATION_TEXT_FOR_LLM = 1500  # Chars per citation in prompt


class TraceType(str, Enum):
    PLAN = "plan"
    TOOL_CALL = "tool_call"
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
class AgentResult:
    """Result of agent execution."""
    answer: str
    citations: List[GroundedCitation]
    trace: List[TraceEntry] = field(default_factory=list)
    
    def to_dict(self, include_trace: bool = True) -> Dict[str, Any]:
        result = {
            "answer": self.answer,
            "citations": [c.to_dict() for c in self.citations],
        }
        if include_trace:
            result["trace"] = [t.to_dict() for t in self.trace]
        return result


class AgentError(Exception):
    """Raised when agent execution fails."""
    pass


# ============================================================================
# System prompts for strict action format
# ============================================================================

TOOL_LOOP_PROMPT = """You are executing a plan to answer a question using document tools.

AVAILABLE TOOLS:
1. search_docs - Search documents. Call with: TOOL_CALL {{"tool": "search_docs", "input": {{"query": "search terms"}}}}
2. open_citation - Read a specific chunk. Call with: TOOL_CALL {{"tool": "open_citation", "input": {{"docId": "...", "chunkId": "..."}}}}

STRICT RULES:
- You MUST output EXACTLY one of these formats:
  a) TOOL_CALL {{"tool": "...", "input": {{...}}}}
  b) FINAL {{"answer": "your answer", "citations": [1, 2]}}
- The "citations" in FINAL must be numbers [1, 2, 3] referencing opened chunks
- If you cannot find relevant info, use: FINAL {{"answer": "I don't know based on the provided documents.", "citations": []}}
- DO NOT output anything else. NO explanations before/after.

CURRENT CONTEXT:
Plan: {plan}
Current step: {step_num} of {total_steps}
Question: {question}

Previous tool results:
{context}

What is your next action? Output ONLY a TOOL_CALL or FINAL line:"""


SYNTHESIS_PROMPT = """Based on the gathered information, answer the question.

STRICT RULES:
1. Use ONLY the provided context - never make up information
2. Cite sources using [1], [2] notation matching the chunk numbers below
3. If the context doesn't answer the question, say: "I don't know based on the provided documents."
4. Be concise and factual

Question: {question}

Available sources:
{context}

Answer (use [1], [2] etc. to cite sources):"""


# ============================================================================
# LLM Response Parsing
# ============================================================================

@dataclass
class ParsedAction:
    """Parsed action from LLM response."""
    action_type: str  # "tool_call" or "final"
    tool: Optional[str] = None
    input: Optional[Dict[str, Any]] = None
    answer: Optional[str] = None
    citation_refs: Optional[List[int]] = None  # [1, 2, 3] from FINAL


def parse_llm_action(response: str) -> Optional[ParsedAction]:
    """
    Parse LLM response expecting TOOL_CALL or FINAL format.
    
    Returns ParsedAction or None if malformed.
    """
    text = response.strip()
    
    # Try TOOL_CALL format
    tool_match = re.search(r'TOOL_CALL\s*(\{[\s\S]*?\})', text, re.IGNORECASE)
    if tool_match:
        try:
            data = json.loads(tool_match.group(1))
            if 'tool' in data and 'input' in data:
                return ParsedAction(
                    action_type="tool_call",
                    tool=data['tool'],
                    input=data['input']
                )
        except json.JSONDecodeError:
            pass
    
    # Try FINAL format
    final_match = re.search(r'FINAL\s*(\{[\s\S]*?\})', text, re.IGNORECASE)
    if final_match:
        try:
            data = json.loads(final_match.group(1))
            answer = data.get('answer', '')
            citations = data.get('citations', [])
            # Ensure citations is list of ints
            if isinstance(citations, list):
                citations = [int(c) for c in citations if isinstance(c, (int, float, str)) and str(c).isdigit()]
            else:
                citations = []
            return ParsedAction(
                action_type="final",
                answer=answer,
                citation_refs=citations
            )
        except (json.JSONDecodeError, ValueError):
            pass
    
    # Try to find any JSON that looks like a tool call (fallback)
    try:
        json_match = re.search(r'\{[\s\S]*\}', text)
        if json_match:
            data = json.loads(json_match.group())
            if 'tool' in data and 'input' in data:
                return ParsedAction(
                    action_type="tool_call",
                    tool=data['tool'],
                    input=data['input']
                )
            if 'answer' in data:
                citations = data.get('citations', [])
                if isinstance(citations, list):
                    citations = [int(c) for c in citations if isinstance(c, (int, float, str)) and str(c).isdigit()]
                else:
                    citations = []
                return ParsedAction(
                    action_type="final",
                    answer=data['answer'],
                    citation_refs=citations
                )
    except (json.JSONDecodeError, ValueError):
        pass
    
    return None


# ============================================================================
# Tool Execution
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


@dataclass 
class OpenedCitation:
    """An opened citation with compressed text."""
    doc_id: str
    chunk_id: str
    chunk_index: int
    text: str  # Compressed to MAX_CITATION_TEXT_FOR_LLM
    filename: str
    citation_num: int  # [1], [2], etc.


class AgentState:
    """Internal state during agent execution."""
    
    def __init__(self):
        self.tool_calls_used: int = 0
        self.search_results: List[SearchResultItem] = []
        self.opened_citations: List[OpenedCitation] = []
        self.notes: List[str] = []
        self._citation_counter: int = 0
    
    def add_search_results(self, output: SearchDocsOutput, filename_map: Dict[str, str]):
        """Add search results with compression (only docId/chunkId/snippet/score)."""
        for r in output.results:
            self.search_results.append(SearchResultItem(
                doc_id=r.doc_id,
                chunk_id=r.chunk_id,
                chunk_index=r.chunk_index,
                snippet=r.snippet[:200],  # Cap snippet length
                score=r.score,
                filename=filename_map.get(r.doc_id, "document")
            ))
    
    def add_opened_citation(self, output: OpenCitationOutput):
        """Add opened citation with compression and rolling window."""
        self._citation_counter += 1
        
        # Compress text to limit
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
    
    def build_context_string(self) -> str:
        """Build context string for LLM prompt."""
        parts = []
        
        # Search results summary
        if self.search_results:
            parts.append("=== Search Results ===")
            for i, r in enumerate(self.search_results, 1):
                parts.append(f"[Search {i}] {r.filename}: \"{r.snippet}\" (docId={r.doc_id}, chunkId={r.chunk_id})")
        
        # Opened citations
        if self.opened_citations:
            parts.append("\n=== Opened Citations ===")
            for c in self.opened_citations:
                parts.append(f"[{c.citation_num}] {c.filename} (chunk {c.chunk_index}):\n{c.text}")
        
        # Notes
        if self.notes:
            parts.append("\n=== Notes ===")
            for note in self.notes[-3:]:  # Last 3 notes only
                parts.append(f"- {note}")
        
        return "\n".join(parts) if parts else "(No information gathered yet)"


def execute_tool(
    tool_name: str, 
    tool_input: Dict[str, Any], 
    user_id: str,
    state: AgentState,
    trace: List[TraceEntry]
) -> Tuple[bool, str]:
    """
    Execute a single tool and update state.
    
    Returns:
        (success: bool, message: str)
    """
    state.tool_calls_used += 1
    
    try:
        if tool_name == 'search_docs':
            query = tool_input.get('query', '')
            if not query:
                return False, "Query is required"
            
            result = search_docs(query, user_id)
            
            # Get filename mapping (we'll need doc titles)
            filename_map = {}
            from apps.docs.models import Document
            doc_ids = [r.doc_id for r in result.results]
            if doc_ids:
                docs = Document.objects.filter(id__in=doc_ids)
                filename_map = {str(d.id): d.filename for d in docs}
            
            state.add_search_results(result, filename_map)
            
            trace.append(TraceEntry(
                type=TraceType.TOOL_CALL,
                tool='search_docs',
                input={'query': query[:100]},  # Truncate for trace
                output_summary=result.summary()
            ))
            
            return True, result.summary()
            
        elif tool_name == 'open_citation':
            doc_id = tool_input.get('docId', '')
            chunk_id = tool_input.get('chunkId', '')
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
                error=f"Unknown tool: {tool_name}"
            ))
            return False, f"Unknown tool: {tool_name}"
            
    except ToolValidationError as e:
        trace.append(TraceEntry(
            type=TraceType.ERROR,
            tool=tool_name,
            error=str(e)[:100]
        ))
        state.notes.append(f"Tool error: {e}")
        return False, str(e)
        
    except ToolAccessError as e:
        trace.append(TraceEntry(
            type=TraceType.ERROR,
            tool=tool_name,
            error=str(e)[:100]
        ))
        state.notes.append(f"Access denied: {e}")
        return False, str(e)
        
    except ToolError as e:
        trace.append(TraceEntry(
            type=TraceType.ERROR,
            tool=tool_name,
            error=str(e)[:100]
        ))
        return False, str(e)


# ============================================================================
# LLM Integration
# ============================================================================

def call_llm(prompt: str, max_tokens: int = 500) -> str:
    """Call LLM and return response text."""
    ollama_url = getattr(settings, 'OLLAMA_BASE_URL', 'http://ollama:11434')
    chat_model = getattr(settings, 'OLLAMA_CHAT_MODEL', 'llama3.2')
    
    with httpx.Client(timeout=60.0) as client:
        response = client.post(
            f"{ollama_url}/api/chat",
            json={
                "model": chat_model,
                "messages": [{"role": "user", "content": prompt}],
                "stream": False,
                "options": {
                    "temperature": 0.1,  # Low temp for predictable format
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

def ground_citations(
    answer: str,
    citation_refs: List[int],
    opened_citations: List[OpenedCitation]
) -> Tuple[str, List[GroundedCitation]]:
    """
    Map citation references to actual opened citations.
    Strip/repair any hallucinated references.
    
    Returns:
        (cleaned_answer, grounded_citations)
    """
    # Build mapping from citation_num to citation
    citation_map: Dict[int, OpenedCitation] = {
        c.citation_num: c for c in opened_citations
    }
    
    grounded: List[GroundedCitation] = []
    used_refs: set = set()
    
    # First pass: collect valid citations from the explicit refs
    for ref in citation_refs:
        if ref in citation_map and ref not in used_refs:
            c = citation_map[ref]
            grounded.append(GroundedCitation(
                doc_id=c.doc_id,
                chunk_id=c.chunk_id,
                chunk_index=c.chunk_index,
                snippet=c.text[:200],
                filename=c.filename,
            ))
            used_refs.add(ref)
    
    # Second pass: find [N] markers in answer and validate/repair
    cleaned_answer = answer
    found_refs = re.findall(r'\[(\d+)\]', answer)
    
    for ref_str in found_refs:
        ref = int(ref_str)
        if ref not in citation_map:
            # Hallucinated reference - strip it
            cleaned_answer = re.sub(rf'\[{ref}\]', '', cleaned_answer)
        elif ref not in used_refs:
            # Valid reference not in explicit list - add it
            c = citation_map[ref]
            grounded.append(GroundedCitation(
                doc_id=c.doc_id,
                chunk_id=c.chunk_id,
                chunk_index=c.chunk_index,
                snippet=c.text[:200],
                filename=c.filename,
            ))
            used_refs.add(ref)
    
    # Clean up any double spaces from stripped refs
    cleaned_answer = re.sub(r'\s+', ' ', cleaned_answer).strip()
    
    return cleaned_answer, grounded


# ============================================================================
# Main Agent Loop
# ============================================================================

def run_agent(question: str, user_id: str) -> AgentResult:
    """
    Execute the bounded agent loop.
    
    1. Generate plan (2-5 steps)
    2. Execute tools via TOOL_CALL/FINAL protocol (max 5 calls, 5 iterations)
    3. Ground citations and return answer
    
    Args:
        question: User's question (max 1000 chars)
        user_id: Keycloak subject ID
        
    Returns:
        AgentResult with answer, citations, and trace
    """
    trace: List[TraceEntry] = []
    state = AgentState()
    
    # Validate and truncate question
    if not question or not question.strip():
        raise AgentError("Question is required")
    
    question = question.strip()
    if len(question) > MAX_QUESTION_LENGTH:
        logger.warning(f"Question truncated from {len(question)} to {MAX_QUESTION_LENGTH}")
        question = question[:MAX_QUESTION_LENGTH]
    
    logger.info(f"Agent starting for question: {question[:100]}...")
    
    # ========================================================================
    # Step 1: Generate plan
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
    # Step 2: Execute tool loop
    # ========================================================================
    iteration = 0
    final_answer = None
    final_citation_refs = []
    reprompt_count = 0
    
    while iteration < MAX_ITERATIONS and state.tool_calls_used < MAX_TOOL_CALLS:
        iteration += 1
        
        # Build prompt
        prompt = TOOL_LOOP_PROMPT.format(
            plan=plan_summary,
            step_num=min(iteration, len(plan.steps)),
            total_steps=len(plan.steps),
            question=question,
            context=state.build_context_string()
        )
        
        # Get LLM response
        try:
            response = call_llm(prompt, max_tokens=400)
        except Exception as e:
            logger.error(f"LLM call failed: {e}")
            trace.append(TraceEntry(
                type=TraceType.ERROR,
                error=f"LLM error: {str(e)[:100]}"
            ))
            break
        
        # Parse action
        action = parse_llm_action(response)
        
        if action is None:
            # Malformed output - treat as implicit FINAL or reprompt once
            reprompt_count += 1
            if reprompt_count >= 2:
                # Give up and force synthesis
                state.notes.append("Model output malformed, forcing synthesis")
                break
            continue
        
        if action.action_type == "final":
            final_answer = action.answer
            final_citation_refs = action.citation_refs or []
            break
        
        if action.action_type == "tool_call":
            if state.tool_calls_used >= MAX_TOOL_CALLS:
                state.notes.append(f"Tool call limit ({MAX_TOOL_CALLS}) reached")
                break
            
            success, message = execute_tool(
                action.tool,
                action.input or {},
                user_id,
                state,
                trace
            )
            
            if not success:
                state.notes.append(f"Tool failed: {message}")
    
    # ========================================================================
    # Step 3: Synthesize if no explicit FINAL
    # ========================================================================
    if final_answer is None:
        # Need to synthesize from gathered context
        if not state.opened_citations and not state.search_results:
            final_answer = "I don't know based on the provided documents."
            trace.append(TraceEntry(
                type=TraceType.FINAL,
                notes="No relevant sources found"
            ))
        else:
            # Build synthesis context
            context_parts = []
            for c in state.opened_citations:
                context_parts.append(f"[{c.citation_num}] {c.filename} (chunk {c.chunk_index}):\n{c.text}")
            
            if not context_parts and state.search_results:
                # No opened citations, use search snippets
                for i, r in enumerate(state.search_results[:3], 1):
                    context_parts.append(f"[{i}] {r.filename}:\n{r.snippet}")
            
            synthesis_prompt = SYNTHESIS_PROMPT.format(
                question=question,
                context="\n\n".join(context_parts)
            )
            
            try:
                final_answer = call_llm(synthesis_prompt, max_tokens=600)
                trace.append(TraceEntry(
                    type=TraceType.FINAL,
                    notes=f"Synthesized from {len(state.opened_citations)} citations"
                ))
            except Exception as e:
                logger.error(f"Synthesis failed: {e}")
                final_answer = "I encountered an error generating the answer."
                trace.append(TraceEntry(
                    type=TraceType.ERROR,
                    error=f"Synthesis failed: {str(e)[:100]}"
                ))
    
    # ========================================================================
    # Step 4: Ground citations
    # ========================================================================
    cleaned_answer, grounded_citations = ground_citations(
        final_answer,
        final_citation_refs,
        state.opened_citations
    )
    
    # If no opened citations but we have search results, use those as citations
    if not grounded_citations and state.search_results:
        for r in state.search_results[:3]:
            grounded_citations.append(GroundedCitation(
                doc_id=r.doc_id,
                chunk_id=r.chunk_id,
                chunk_index=r.chunk_index,
                snippet=r.snippet,
                filename=r.filename,
                score=r.score,
            ))
    
    logger.info(f"Agent completed: {state.tool_calls_used} tool calls, {len(grounded_citations)} citations")
    
    return AgentResult(
        answer=cleaned_answer,
        citations=grounded_citations,
        trace=trace
    )
