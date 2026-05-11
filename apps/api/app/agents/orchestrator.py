"""Wires the LangGraph state machine: RolePlay → Coach → Judge → END.

Sprint 0 verification (foundation §6.3): "跑一遍 RolePlay → Coach →
Judge 状态机". This module builds and compiles that graph; running
it lives in the API layer (next PR).

The graph is intentionally linear and stateless across turns — turn
history is supplied via `SessionState.history` from the caller. We'll
add cycles + checkpointing in Sprint 1 once the session store schema
firms up.
"""

from __future__ import annotations

from typing import Any

from langgraph.graph import END, StateGraph

from app.agents.coach import make_coach_node
from app.agents.judge import make_judge_node
from app.agents.roleplay import make_roleplay_node
from app.agents.state import SessionState
from app.llm import LLMProvider

NODE_ROLEPLAY = "roleplay"
NODE_COACH = "coach"
NODE_JUDGE = "judge"


def build_graph(provider: LLMProvider) -> Any:
    """Compile the 3-node graph with `provider` injected into each node.

    Returns the compiled graph (`Pregel`); callers use `ainvoke` /
    `astream` on it. We don't pin the LangGraph runtime type in the
    signature because LangGraph re-exports it from a few places and
    its public name has shifted between minor releases.
    """
    graph = StateGraph(SessionState)

    graph.add_node(NODE_ROLEPLAY, make_roleplay_node(provider))
    graph.add_node(NODE_COACH, make_coach_node(provider))
    graph.add_node(NODE_JUDGE, make_judge_node(provider))

    graph.set_entry_point(NODE_ROLEPLAY)
    graph.add_edge(NODE_ROLEPLAY, NODE_COACH)
    graph.add_edge(NODE_COACH, NODE_JUDGE)
    graph.add_edge(NODE_JUDGE, END)

    return graph.compile()
