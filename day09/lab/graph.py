"""
graph.py — Supervisor Orchestrator
Sprint 1: AgentState, supervisor_node, route_decision và kết nối graph.

Kiến trúc:
    Input → Supervisor → [retrieval_worker | policy_tool_worker | human_review] → synthesis → Output

Chạy thử:
    python graph.py
"""

import json
import os
import sys
from datetime import datetime
from typing import TypedDict, Literal, Optional

from dotenv import load_dotenv
load_dotenv()

sys.path.insert(0, os.path.dirname(__file__))

from workers.retrieval import run as retrieval_run
from workers.policy_tool import run as policy_tool_run
from workers.synthesis import run as synthesis_run


# ─────────────────────────────────────────────
# 1. Shared State
# ─────────────────────────────────────────────

class AgentState(TypedDict):
    # Input
    task: str

    # Supervisor decisions
    route_reason: str
    risk_high: bool
    needs_tool: bool
    hitl_triggered: bool

    # Worker outputs
    retrieved_chunks: list
    retrieved_sources: list
    policy_result: dict
    mcp_tools_used: list

    # Final output
    final_answer: str
    sources: list
    confidence: float

    # Trace & history
    history: list
    workers_called: list
    supervisor_route: str
    worker_io_logs: list
    latency_ms: Optional[int]
    run_id: str


def make_initial_state(task: str) -> AgentState:
    """Khởi tạo state cho một run mới."""
    return {
        "task": task,
        "route_reason": "",
        "risk_high": False,
        "needs_tool": False,
        "hitl_triggered": False,
        "retrieved_chunks": [],
        "retrieved_sources": [],
        "policy_result": {},
        "mcp_tools_used": [],
        "final_answer": "",
        "sources": [],
        "confidence": 0.0,
        "history": [],
        "workers_called": [],
        "supervisor_route": "",
        "worker_io_logs": [],
        "latency_ms": None,
        "run_id": f"run_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}",
    }


# ─────────────────────────────────────────────
# 2. Supervisor Node
# ─────────────────────────────────────────────

# Keyword sets cho routing
_POLICY_KEYWORDS = [
    "hoàn tiền", "refund", "flash sale", "license", "license key",
    "subscription", "kỹ thuật số", "đã kích hoạt", "đã đăng ký",
    "cấp quyền", "access", "access level", "level 1", "level 2", "level 3",
    "quyền truy cập", "phê duyệt quyền",
]
_RISK_KEYWORDS = [
    "emergency", "khẩn cấp", "gấp", "2am", "lúc đêm", "ngoài giờ",
    "không rõ", "err-", "lỗi không xác định",
]
_RETRIEVAL_KEYWORDS = [
    "p1", "p2", "p3", "p4", "sla", "ticket", "escalation",
    "sự cố", "incident", "on-call", "oncall", "hr", "nghỉ phép",
    "helpdesk", "faq", "hỏi đáp",
]


def supervisor_node(state: AgentState) -> AgentState:
    """
    Supervisor phân tích task và quyết định:
    1. Route sang worker nào
    2. Có cần MCP tool không
    3. Có risk cao cần HITL không
    """
    task = state["task"].lower()
    state["history"].append(f"[supervisor] received: {state['task'][:80]}")

    route = "retrieval_worker"
    route_reason = "default: câu hỏi tra cứu tài liệu nội bộ"
    needs_tool = False
    risk_high = False

    # Ưu tiên 1: Policy / access control
    matched_policy = [kw for kw in _POLICY_KEYWORDS if kw in task]
    if matched_policy:
        route = "policy_tool_worker"
        needs_tool = True
        route_reason = f"task chứa keyword policy/access: {matched_policy[:2]}"

    # Ưu tiên 2: SLA / ticket / FAQ rõ ràng → retrieval
    matched_retrieval = [kw for kw in _RETRIEVAL_KEYWORDS if kw in task]
    if matched_retrieval and not matched_policy:
        route = "retrieval_worker"
        route_reason = f"task chứa keyword SLA/ticket/FAQ: {matched_retrieval[:2]}"

    # Risk flag
    matched_risk = [kw for kw in _RISK_KEYWORDS if kw in task]
    if matched_risk:
        risk_high = True
        route_reason += f" | risk_high: {matched_risk[:2]}"

    # Human review: mã lỗi không rõ + không đủ context
    if risk_high and "err-" in task and not any(kw in task for kw in _POLICY_KEYWORDS + _RETRIEVAL_KEYWORDS):
        route = "human_review"
        route_reason = "unknown error code + risk_high → human review"
        needs_tool = False

    state["supervisor_route"] = route
    state["route_reason"] = route_reason
    state["needs_tool"] = needs_tool
    state["risk_high"] = risk_high
    state["history"].append(f"[supervisor] route={route} | reason={route_reason}")

    return state


# ─────────────────────────────────────────────
# 3. Route Decision
# ─────────────────────────────────────────────

def route_decision(state: AgentState) -> Literal["retrieval_worker", "policy_tool_worker", "human_review"]:
    """Conditional edge: trả về tên worker tiếp theo."""
    return state.get("supervisor_route", "retrieval_worker")  # type: ignore


# ─────────────────────────────────────────────
# 4. Human Review Node (HITL placeholder)
# ─────────────────────────────────────────────

def human_review_node(state: AgentState) -> AgentState:
    """HITL node: pause và chờ human approval (auto-approve trong lab)."""
    state["hitl_triggered"] = True
    state["workers_called"].append("human_review")
    state["history"].append("[human_review] HITL triggered — awaiting human input")

    print(f"\n  [HITL] Task: {state['task']}")
    print(f"  [HITL] Reason: {state['route_reason']}")
    print(f"  [HITL] Auto-approving in lab mode → routing to retrieval\n")

    state["supervisor_route"] = "retrieval_worker"
    state["route_reason"] += " | human approved → retrieval"
    return state


# ─────────────────────────────────────────────
# 5. Worker Node Wrappers
# ─────────────────────────────────────────────

def retrieval_worker_node(state: AgentState) -> AgentState:
    """Wrapper: gọi retrieval worker thật."""
    return retrieval_run(state)


def policy_tool_worker_node(state: AgentState) -> AgentState:
    """Wrapper: gọi policy/tool worker thật."""
    return policy_tool_run(state)


def synthesis_worker_node(state: AgentState) -> AgentState:
    """Wrapper: gọi synthesis worker thật."""
    return synthesis_run(state)


# ─────────────────────────────────────────────
# 6. Build Graph (Python orchestrator)
# ─────────────────────────────────────────────

def build_graph():
    """
    Xây dựng graph với supervisor-worker pattern (Python thuần, không cần LangGraph).

    Luồng:
        supervisor → route_decision
            → retrieval_worker → synthesis
            → policy_tool_worker → (retrieval nếu chưa có chunks) → synthesis
            → human_review → retrieval → synthesis
    """
    def run(state: AgentState) -> AgentState:
        import time
        start = time.time()

        # Step 1: Supervisor quyết định route
        state = supervisor_node(state)
        route = route_decision(state)

        # Step 2: Route tới worker tương ứng
        if route == "human_review":
            state = human_review_node(state)
            # Sau human approve → retrieval
            state = retrieval_worker_node(state)

        elif route == "policy_tool_worker":
            # Policy worker cần context từ retrieval trước
            state = retrieval_worker_node(state)
            state = policy_tool_worker_node(state)

        else:
            # Default: retrieval_worker
            state = retrieval_worker_node(state)

        # Step 3: Luôn chạy synthesis để tổng hợp câu trả lời
        state = synthesis_worker_node(state)

        state["latency_ms"] = int((time.time() - start) * 1000)
        state["history"].append(f"[graph] completed in {state['latency_ms']}ms")
        return state

    return run


# ─────────────────────────────────────────────
# 7. Public API
# ─────────────────────────────────────────────

_graph = build_graph()


def run_graph(task: str) -> AgentState:
    """
    Entry point: nhận câu hỏi, trả về AgentState với full trace.

    Args:
        task: Câu hỏi từ user

    Returns:
        AgentState với final_answer, trace, routing info, v.v.
    """
    state = make_initial_state(task)
    return _graph(state)


def save_trace(state: AgentState, output_dir: str = "./artifacts/traces") -> str:
    """Lưu trace ra file JSON."""
    os.makedirs(output_dir, exist_ok=True)
    filename = f"{output_dir}/{state['run_id']}.json"
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    return filename


# ─────────────────────────────────────────────
# 8. Manual Test
# ─────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print("Day 09 Lab — Supervisor-Worker Graph")
    print("=" * 60)

    test_queries = [
        "SLA xử lý ticket P1 là bao lâu?",
        "Khách hàng Flash Sale yêu cầu hoàn tiền vì sản phẩm lỗi — được không?",
        "Cần cấp quyền Level 3 để khắc phục P1 khẩn cấp. Quy trình là gì?",
    ]

    for query in test_queries:
        print(f"\n Query: {query}")
        result = run_graph(query)
        print(f"  Route      : {result['supervisor_route']}")
        print(f"  Reason     : {result['route_reason']}")
        print(f"  Workers    : {result['workers_called']}")
        print(f"  Answer     : {result['final_answer'][:120]}...")
        print(f"  Confidence : {result['confidence']}")
        print(f"  Latency    : {result['latency_ms']}ms")
        trace_file = save_trace(result)
        print(f"  Trace      : {trace_file}")

    print("\ngraph.py test complete.")
