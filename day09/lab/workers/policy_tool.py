"""
workers/policy_tool.py — Policy & Tool Worker
Sprint 2+3: Kiểm tra policy dựa vào context, gọi MCP tools khi cần.

Input (từ AgentState):
    - task: câu hỏi
    - retrieved_chunks: context từ retrieval_worker
    - needs_tool: True nếu supervisor quyết định cần tool call

Output (vào AgentState):
    - policy_result: {"policy_applies", "policy_name", "exceptions_found", "source"}
    - mcp_tools_used: list of tool calls đã thực hiện
    - worker_io_logs: log

Gọi độc lập để test:
    python workers/policy_tool.py
"""

import os
import sys
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

WORKER_NAME = "policy_tool_worker"

# Nếu set MCP_SERVER_URL trong .env → dùng HTTP mode
# Nếu không → fallback in-process import (mock mode)
MCP_SERVER_URL = os.getenv("MCP_SERVER_URL", "").rstrip("/")


# ─────────────────────────────────────────────
# MCP Client
# ─────────────────────────────────────────────

def _call_mcp_tool(tool_name: str, tool_input: dict) -> dict:
    """
    Gọi MCP tool.
    - HTTP mode:       nếu MCP_SERVER_URL set trong .env → POST /tools/call
    - In-process mode: fallback → import dispatch_tool từ mcp_server.py
    Trả về dict chuẩn: {tool, input, output, error, timestamp}
    """
    if MCP_SERVER_URL:
        # ── HTTP mode ──────────────────────────────────────
        try:
            import requests
            resp = requests.post(
                f"{MCP_SERVER_URL}/tools/call",
                json={"tool": tool_name, "input": tool_input},
                timeout=10,
            )
            resp.raise_for_status()
            return {
                "tool": tool_name,
                "input": tool_input,
                "output": resp.json(),
                "error": None,
                "timestamp": datetime.now().isoformat(),
                "mode": "http",
            }
        except Exception as e:
            return {
                "tool": tool_name,
                "input": tool_input,
                "output": None,
                "error": {"code": "HTTP_CALL_FAILED", "reason": str(e)},
                "timestamp": datetime.now().isoformat(),
                "mode": "http",
            }
    else:
        # ── In-process mode (fallback) ─────────────────────
        try:
            sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
            from mcp_server import dispatch_tool
            result = dispatch_tool(tool_name, tool_input)
            return {
                "tool": tool_name,
                "input": tool_input,
                "output": result,
                "error": None,
                "timestamp": datetime.now().isoformat(),
                "mode": "in-process",
            }
        except Exception as e:
            return {
                "tool": tool_name,
                "input": tool_input,
                "output": None,
                "error": {"code": "MCP_CALL_FAILED", "reason": str(e)},
                "timestamp": datetime.now().isoformat(),
                "mode": "in-process",
            }


# ─────────────────────────────────────────────
# Policy Analysis Logic
# ─────────────────────────────────────────────

def analyze_policy(task: str, chunks: list) -> dict:
    """
    Phân tích policy dựa trên context chunks.

    Xử lý các exceptions:
    - Flash Sale → không được hoàn tiền
    - Digital product / license key / subscription → không được hoàn tiền
    - Sản phẩm đã kích hoạt → không được hoàn tiền
    - Đơn hàng trước 01/02/2026 → áp dụng policy v3 (flag để synthesis biết)

    Returns:
        dict with: policy_applies, policy_name, exceptions_found, source, policy_version_note
    """
    task_lower = task.lower()
    context_text = " ".join([c.get("text", "") for c in chunks]).lower()

    exceptions_found = []

    # Exception 1: Flash Sale
    if "flash sale" in task_lower or "flash sale" in context_text:
        exceptions_found.append({
            "type": "flash_sale_exception",
            "rule": "Đơn hàng Flash Sale không được hoàn tiền (Điều 3, chính sách v4).",
            "source": "policy_refund_v4.txt",
        })

    # Exception 2: Digital product / license key
    if any(kw in task_lower for kw in ["license key", "license", "subscription", "kỹ thuật số"]):
        exceptions_found.append({
            "type": "digital_product_exception",
            "rule": "Sản phẩm kỹ thuật số (license key, subscription) không được hoàn tiền (Điều 3).",
            "source": "policy_refund_v4.txt",
        })

    # Exception 3: Activated product
    if any(kw in task_lower for kw in ["đã kích hoạt", "đã đăng ký", "đã sử dụng"]):
        exceptions_found.append({
            "type": "activated_exception",
            "rule": "Sản phẩm đã kích hoạt hoặc đăng ký tài khoản không được hoàn tiền (Điều 3).",
            "source": "policy_refund_v4.txt",
        })

    policy_applies = len(exceptions_found) == 0

    # Temporal scoping: đơn hàng trước 01/02/2026 → v3 (không có docs → cần flag)
    policy_name = "refund_policy_v4"
    policy_version_note = ""
    if any(kw in task_lower for kw in ["31/01", "30/01", "trước 01/02", "trước tháng 2"]):
        policy_version_note = (
            "Đơn hàng đặt trước 01/02/2026 áp dụng chính sách v3 "
            "(không có trong tài liệu hiện tại — cần xác minh thủ công)."
        )

    sources = list({c.get("source", "unknown") for c in chunks if c})

    return {
        "policy_applies": policy_applies,
        "policy_name": policy_name,
        "exceptions_found": exceptions_found,
        "source": sources,
        "policy_version_note": policy_version_note,
    }


# ─────────────────────────────────────────────
# Worker Entry Point
# ─────────────────────────────────────────────

def run(state: dict) -> dict:
    """
    Worker entry point — gọi từ graph.py.

    Luồng:
    1. Nếu chưa có chunks → gọi MCP search_kb
    2. Phân tích policy (rule-based)
    3. Nếu cần → gọi MCP check_access_permission (access level questions)
    4. Nếu cần → gọi MCP get_ticket_info (ticket/P1 questions)
    """
    task = state.get("task", "")
    task_lower = task.lower()
    chunks = state.get("retrieved_chunks", [])
    needs_tool = state.get("needs_tool", False)

    state.setdefault("workers_called", [])
    state.setdefault("history", [])
    state.setdefault("mcp_tools_used", [])

    state["workers_called"].append(WORKER_NAME)

    worker_io = {
        "worker": WORKER_NAME,
        "input": {
            "task": task,
            "chunks_count": len(chunks),
            "needs_tool": needs_tool,
        },
        "output": None,
        "error": None,
    }

    try:
        # Step 1: Nếu chưa có chunks → dùng MCP search_kb bổ sung
        if not chunks and needs_tool:
            mcp_result = _call_mcp_tool("search_kb", {"query": task, "top_k": 3})
            state["mcp_tools_used"].append(mcp_result)
            state["history"].append(f"[{WORKER_NAME}] MCP search_kb called")
            if mcp_result.get("output") and mcp_result["output"].get("chunks"):
                chunks = mcp_result["output"]["chunks"]
                state["retrieved_chunks"] = chunks
                state["retrieved_sources"] = list({c.get("source") for c in chunks if c.get("source")})

        # Step 2: Phân tích policy rule-based
        policy_result = analyze_policy(task, chunks)
        state["policy_result"] = policy_result

        # Step 3: Access control questions → gọi check_access_permission
        access_keywords = ["access level", "level 1", "level 2", "level 3", "cấp quyền", "quyền truy cập"]
        if needs_tool and any(kw in task_lower for kw in access_keywords):
            # Detect level từ task
            access_level = 3  # default
            if "level 1" in task_lower or "level-1" in task_lower:
                access_level = 1
            elif "level 2" in task_lower or "level-2" in task_lower:
                access_level = 2

            is_emergency = any(kw in task_lower for kw in ["emergency", "khẩn cấp", "gấp", "p1"])

            # Detect role nếu có (fallback = "engineer")
            requester_role = "engineer"
            for role_kw in ["manager", "contractor", "admin", "developer", "intern"]:
                if role_kw in task_lower:
                    requester_role = role_kw
                    break

            mcp_result = _call_mcp_tool("check_access_permission", {
                "access_level": access_level,
                "requester_role": requester_role,
                "is_emergency": is_emergency,
            })
            state["mcp_tools_used"].append(mcp_result)
            state["history"].append(
                f"[{WORKER_NAME}] MCP check_access_permission: level={access_level}, emergency={is_emergency}"
            )

            # Đưa kết quả access check vào policy_result để synthesis biết
            if mcp_result.get("output") and not mcp_result["output"].get("error"):
                policy_result["access_check"] = mcp_result["output"]
                state["policy_result"] = policy_result

        # Step 4: Ticket/P1 questions → gọi get_ticket_info
        if needs_tool and any(kw in task_lower for kw in ["ticket", "p1", "jira", "it-"]):
            mcp_result = _call_mcp_tool("get_ticket_info", {"ticket_id": "P1-LATEST"})
            state["mcp_tools_used"].append(mcp_result)
            state["history"].append(f"[{WORKER_NAME}] MCP get_ticket_info called")

        worker_io["output"] = {
            "policy_applies": policy_result["policy_applies"],
            "exceptions_count": len(policy_result.get("exceptions_found", [])),
            "mcp_calls": len(state["mcp_tools_used"]),
        }
        state["history"].append(
            f"[{WORKER_NAME}] policy_applies={policy_result['policy_applies']}, "
            f"exceptions={len(policy_result.get('exceptions_found', []))}, "
            f"mcp_calls={len(state['mcp_tools_used'])}"
        )

    except Exception as e:
        worker_io["error"] = {"code": "POLICY_CHECK_FAILED", "reason": str(e)}
        state["policy_result"] = {"error": str(e)}
        state["history"].append(f"[{WORKER_NAME}] ERROR: {e}")

    state.setdefault("worker_io_logs", []).append(worker_io)
    return state


# ─────────────────────────────────────────────
# Test độc lập
# ─────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 55)
    print("Policy Tool Worker — Standalone Test")
    print("=" * 55)

    test_cases = [
        {
            "task": "Khách hàng Flash Sale yêu cầu hoàn tiền vì sản phẩm lỗi — được không?",
            "retrieved_chunks": [
                {"text": "Ngoại lệ: Đơn hàng Flash Sale không được hoàn tiền.", "source": "policy_refund_v4.txt", "score": 0.9}
            ],
            "needs_tool": True,
        },
        {
            "task": "Khách hàng muốn hoàn tiền license key đã kích hoạt.",
            "retrieved_chunks": [
                {"text": "Sản phẩm kỹ thuật số (license key, subscription) không được hoàn tiền.", "source": "policy_refund_v4.txt", "score": 0.88}
            ],
            "needs_tool": True,
        },
        {
            "task": "Cần cấp access level 3 khẩn cấp để sửa P1. Quy trình là gì?",
            "retrieved_chunks": [],
            "needs_tool": True,
        },
        {
            "task": "Khách hàng yêu cầu hoàn tiền trong 5 ngày, sản phẩm lỗi, chưa kích hoạt.",
            "retrieved_chunks": [
                {"text": "Yêu cầu trong 7 ngày làm việc, sản phẩm lỗi nhà sản xuất, chưa dùng.", "source": "policy_refund_v4.txt", "score": 0.85}
            ],
            "needs_tool": False,
        },
    ]

    for tc in test_cases:
        print(f"\n Task: {tc['task'][:70]}...")
        result = run(tc.copy())
        pr = result.get("policy_result", {})
        print(f"  policy_applies : {pr.get('policy_applies')}")
        for ex in pr.get("exceptions_found", []):
            print(f"  exception      : {ex['type']} — {ex['rule'][:60]}...")
        if pr.get("access_check"):
            ac = pr["access_check"]
            print(f"  access_check   : can_grant={ac.get('can_grant')}, approvers={ac.get('required_approvers')}")
        print(f"  MCP calls      : {len(result.get('mcp_tools_used', []))}")

    print("\npolicy_tool_worker test done.")
