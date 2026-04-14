# Báo Cáo Nhóm — Lab Day 09: Multi-Agent Orchestration

**Tên nhóm:** C401 - C5  
**Thành viên:**
| Tên | Vai trò | Mã học viên |
|-----|---------|-------|
| Nguyễn Khánh Nam | Supervisor Owner | ___ |
| Đỗ Minh Phúc | Worker Owner | ___ |
| Lê Tú Nam | Worker Owner | ___ |
| Lê Hữu Hưng | Worker Owner | 2A202600098 |
| Chu Minh Quân | MCP Owner | ___ |
| Nguyễn Minh Hiếu | Trace & Docs Owner | ___ |

**Ngày nộp:** 14/04/2026  
**Repo:** https://github.com/nam-k-nguyen/Day_08_09_10_C401_C5

---

## 1. Kiến trúc nhóm đã xây dựng

**Hệ thống tổng quan:**  
Nhóm xây dựng hệ thống theo mô hình **Supervisor-Worker** với 1 Supervisor điều phối và 3 Workers chuyên biệt: `retrieval_worker` (dense retrieval từ ChromaDB), `policy_tool_worker` (kiểm tra luật nghiệp vụ + MCP), và `synthesis_worker` (tổng hợp câu trả lời với citation). Ngoài ra có node `human_review` (HITL) cho các trường hợp rủi ro cao. Graph được implement bằng Python thuần (không cần LangGraph), đủ để trace toàn bộ pipeline qua `AgentState` TypedDict dùng chung.

**Routing logic cốt lõi:**  
Supervisor dùng **keyword matching + regex** với 2 nhóm từ khóa độc lập:
- Policy keywords (`"hoàn tiền"`, `"refund"`, `"flash sale"`, `"license"`, `"cấp quyền"`, `"level 2/3"`, ...) → `policy_tool_worker`  
- Retrieval keywords (`"p1"`, `"sla"`, `"ticket"`, `"escalat"`, `"mật khẩu"`, `"remote"`, ...) → `retrieval_worker`  
- Regex `\berr-[a-z0-9\-]+\b` phát hiện mã lỗi lạ → `human_review` (HITL)  
- Nếu câu hỏi chứa từ khóa của **cả hai nhóm** (VD: `"level 3"` + `"p1"`) → multi-hop, route sang `policy_tool_worker` và gọi tiếp `retrieval_worker`

Qua 36 lần chạy với 17 loại câu hỏi: **50% route sang `policy_tool_worker`**, **50% route sang `retrieval_worker`**, phản ánh đúng sự phân bổ đa dạng của test cases.

**MCP tools đã tích hợp:**  
- `search_kb`: tìm kiếm thêm trong ChromaDB khi `needs_tool=True`  
- `get_ticket_info`: tra cứu thông tin Jira ticket (mocked)  
- `check_access_permission`: kiểm tra approval matrix Level 1/2/3  
- `create_ticket`: tạo ticket mới (mock)

---

## 2. Quyết định kỹ thuật quan trọng nhất

**Quyết định chính: Chấp nhận đánh đổi Latency để lấy Observability.**

**Bối cảnh vấn đề:**  
Day 08 dùng Single Agent RAG — khi trả lời sai (VD: áp sai policy v3 thay vì v4), không có cách nào xác định lỗi nằm ở bước retrieval hay suy luận. Nhóm cần kiểm soát được từng chặng của pipeline.

**Các phương án đã cân nhắc:**

| Phương án | Ưu điểm | Nhược điểm |
|-----------|---------|-----------|
| Single Agent (Day 08) | Nhanh (~1,850ms), ít LLM call | "Hộp đen", không biết bước nào sai, dễ hallucinate |
| **Supervisor-Worker (Day 09)** | Trace rõ từng node, kiểm soát được output, multi-hop chính xác | Latency tăng (~16,315ms avg), nhiều LLM call hơn |

**Phương án chọn:** Supervisor-Worker, vì bài toán IT Helpdesk doanh nghiệp cần **Governance** hơn là tốc độ.

**Quyết định phụ: Fail-loud trong `route_decision()` thay vì silent fallback.**  
Khi `supervisor_route` rỗng hoặc không hợp lệ, hệ thống raise `ValueError` ngay lập tức thay vì fallback về default. Lý do: lỗi routing ẩn rất khó phát hiện khi chạy production.

```python
# graph.py — route_decision()
if not route:
    raise ValueError(
        f"[route_decision] supervisor_route is empty — "
        f"supervisor_node chưa chạy hoặc state bị corrupt. "
        f"run_id={state.get('run_id')}"
    )
if route not in VALID_ROUTES:
    raise ValueError(
        f"[route_decision] unknown route: '{route}'. "
        f"Expected one of {VALID_ROUTES}."
    )
```

**Bằng chứng từ trace:** Trong 36 lần chạy, không có lần nào bị silent wrong-route — tất cả đều có `route_reason` rõ ràng, ví dụ `"multi-hop: access control + SLA context | risk_high flagged"` cho câu `"Cần cấp quyền Level 3 để khắc phục P1 khẩn cấp"`.

---

## 3. Kết quả test questions

Nhóm đã chạy pipeline trên 17 loại câu hỏi (36 traces tổng cộng):

**Routing Distribution:**

| Worker | Số câu route | Tỷ lệ |
|--------|-------------|-------|
| `retrieval_worker` | 18/36 | 50% |
| `policy_tool_worker` | 18/36 | 50% |
| `human_review` (HITL) | 1 loại câu hỏi | ERR-403-AUTH |

**Các trường hợp đáng chú ý:**

**HITL hoạt động đúng:** Câu `"ERR-403-AUTH là lỗi gì?"` kích hoạt regex `\berr-[a-z0-9\-]+\b` → route sang `human_review` → HITL triggered, `hitl_triggered=True`, confidence=0.3 (system abstain đúng vì không có doc nào mô tả mã lỗi này).

**Multi-hop hoạt động đúng:** Câu `"Ticket P1 lúc 2am. Cần cấp Level 2 access tạm thời cho contractor"` → supervisor phát hiện `"level 2"` + `"p1"` → `is_multi_hop=True` → route `policy_tool_worker`, `risk_high=True`, `needs_tool=True` → cả 2 docs được truy xuất, confidence=0.75.

**Abstain đúng:** Câu `"Ticket P1 được tạo lúc 22:47 — ai nhận thông báo?"` trả về `hitl_triggered=True`, confidence=0.3 (synthesis nhận ra thiếu thông tin cụ thể về kênh thông báo lúc 22:47).

**Metrics tổng hợp:**
- Avg confidence (tất cả runs): 0.623
- Avg confidence (runs với real LLM): ~0.74–0.79
- Min latency: 2,869ms | Max latency: 128,667ms (run đầu với cold LLM)
- HITL rate: ~2/17 câu hỏi unique (11.8%)

---

## 4. So sánh Day 08 vs Day 09 — Điều nhóm quan sát được

**Metric thay đổi rõ nhất (có số liệu):**

| Metric | Day 08 (Single Agent) | Day 09 (Multi-Agent) | Ghi chú |
|--------|----------------------|---------------------|---------|
| Avg confidence | ~0.82 | 0.74 (real LLM runs) | Tương đương |
| Avg latency | ~1,850ms | ~16,315ms | Tăng ~8.8× (trade-off) |
| Abstain rate | ~13% | ~11.8% | Nhỉnh hơn một chút |
| Routing visibility | Không có | Có (`route_reason` trong mọi trace) | Cải thiện lớn nhất |
| Debug time | 20–30 phút/lỗi | 5–10 phút/lỗi | Nhờ `workers_called` trong trace |

**Điều nhóm bất ngờ nhất:**  
Khả năng **abstain** của Multi-Agent tốt hơn rõ rệt. Nhờ `policy_tool_worker` kiểm tra policy trước khi `synthesis_worker` viết câu trả lời, các câu hỏi thiếu context (VD: ERR-403-AUTH, P1@22:47) được xử lý đúng: confidence thấp → `hitl_triggered=True` → không hallucinate. Day 08 với single prompt thường "tự suy diễn" trong những trường hợp này.

**Trường hợp multi-agent KHÔNG giúp ích:**  
Với câu đơn giản như `"SLA ticket P1 là bao lâu?"`, pipeline vẫn phải qua đủ Supervisor → Retrieval → Synthesis — tốn ~16s trong khi Day 08 trả lời tương đương chỉ ~1.8s. Overhead 8–9× là không cần thiết cho câu hỏi single-doc lookup.

---

## 5. Phân công và đánh giá nhóm

**Phân công thực tế:**

| Thành viên | Phần đã làm | Sprint | Bằng chứng |
|------------|-------------|--------|-----------|
| Nguyễn Khánh Nam | Supervisor Owner — `supervisor_node()`, `route_decision()`, `build_graph()` | 1 | Keyword lists, regex ERR-xxx, multi-hop detection trong `graph.py` |
| Đỗ Minh Phúc | Worker Owner — `synthesis.py`: LLM prompting, abstain logic, confidence heuristic | 2 | `HITL_THRESHOLD=0.4`, `_estimate_confidence()`, `SYSTEM_PROMPT` |
| Lê Tú Nam | Worker Owner — `policy_tool.py`: rule-based exception detection, LLM hybrid | 2 | Rules Flash Sale / digital / activated, `analyze_policy()` |
| Lê Hữu Hưng |Worker Owner — `retrieval.py`: ChromaDB, sliding window chunking, embedding fallback chain | 2 | `_get_embedding_fn()`, `_chunk_text()`, `build_index()`|
| Chu Minh Quân | MCP Owner — `mcp_server.py`: 4 tools, TOOL_REGISTRY, dispatch_tool() | 3 | `search_kb`, `get_ticket_info`, `check_access_permission`, `create_ticket` |
| Nguyễn Minh Hiếu | Trace & Docs Owner — `eval_trace.py`, trace analysis, documentation | 4 | 36 trace files trong `artifacts/traces/`, `docs/*.md` |

**Điều nhóm làm tốt:**  
Thống nhất `AgentState` TypedDict ngay từ Sprint 1 giúp 3 Worker Owner làm việc song song mà không gặp lỗi tương thích khi lắp ghép vào `graph.py`. `contracts/worker_contracts.yaml` làm interface contract rõ ràng — mỗi worker biết chính xác field nào cần đọc và ghi.

**Điều nhóm làm chưa tốt:**  
Tích hợp MCP trong Sprint 3 bị chậm do ban đầu chưa nắm giao thức dispatch — `policy_tool_worker` cần gọi `dispatch_tool()` từ `mcp_server.py` nhưng import path bị nhầm lẫn (lab/ vs root). Mất ~30 phút debug. Sau đó đã thêm `sys.path` handling trong `policy_tool.py` để resolve.

---

## 6. Nếu có thêm 1 ngày, nhóm sẽ làm gì?

Nhóm sẽ thực hiện 2 cải tiến dựa trên trace thực tế:

1. **Async Orchestration:** `build_graph()` hiện chạy tuần tự — latency ~16s phần lớn là LLM wait time. Chuyển sang `asyncio.gather()` để `retrieval_worker` và `policy_tool_worker` chạy song song có thể giảm latency xuống ~8–10s. Bằng chứng: trace `run_20260414_174518.json` ghi 47,899ms cho câu Flash Sale — retrieval và policy chạy nối tiếp nhau không cần thiết.

2. **Semantic Supervisor:** Keyword matching hiện không phân biệt được câu như `"Mật khẩu Flash Sale"` (retrieval keyword `"mật khẩu"` override policy keyword `"flash sale"`). Thay bằng embedding-based intent classifier (BERT few-shot hoặc LLM zero-shot) sẽ xử lý chính xác hơn các câu chồng lấn domain.

---
