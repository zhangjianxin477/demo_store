"""Run the online Agent-RAG acceptance set.

The script reports task metadata only (counts, latency and quality flags); it
never prints document contents, API keys or generated drafts. Set
AGENT_BASE_URL, AGENT_EVAL_USER and AGENT_EVAL_PASSWORD when the service is
protected by login.
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx


ROOT = Path(__file__).resolve().parents[1]
BASE_URL = os.getenv("AGENT_BASE_URL", "http://localhost:8080/api/v1").rstrip("/")
USER = os.getenv("AGENT_EVAL_USER", "eval-runner")
PASSWORD = os.getenv("AGENT_EVAL_PASSWORD", "change-me")
PATHS = [
    "自动抓取/2026-08-30/我的_obsidian_人生系统运行_4_个月后_暴露了一个漏洞.md",
    "自动抓取/2026-08-30/Agent_Harness_三种范式_DSH_OpenCode_Pi.md",
    "自动抓取/2026-08-29/转型AI产品经理需要掌握的硬知识二_AI常见概念和算法梳理.md",
]


def _safe_result(task: dict, latency_ms: float) -> dict:
    result = task.get("result") or {}
    quality = result.get("quality") or {}
    perception = result.get("perception") or {}
    draft = str(result.get("draft") or "")
    evidence = result.get("evidence") or []
    selected = perception.get("constraints", {}).get("selected_file_paths", [])
    sources = {str(item.get("source_path") or "") for item in evidence}
    return {
        "status": task.get("status"),
        "latency_ms": round(latency_ms, 1),
        "intent": perception.get("intent"),
        "route": perception.get("route"),
        "evidence_count": len(evidence),
        "draft_chars": len(draft),
        "quality_passed": quality.get("passed"),
        "selected_count": len(selected),
        "selected_coverage": round(len(sources.intersection(set(selected))) / max(1, len(selected)), 3),
        "has_multi_document_structure": "分篇梳理" in draft,
        "degraded": any(marker in draft for marker in ("当前模型未完成跨文章综合", "模型未返回可用的完整正文")),
        "error": task.get("error"),
    }


def main() -> int:
    goal = "这些文章讲的什么，帮我梳理一下：" + "，".join(PATHS)
    report = {"generated_at": datetime.now(timezone.utc).isoformat(), "base_url": BASE_URL, "cases": []}
    with httpx.Client(timeout=30.0) as client:
        auth = client.post(f"{BASE_URL}/auth/login", json={"user_id": USER, "password": PASSWORD})
        auth.raise_for_status()
        token = auth.json().get("access_token", "")
        if not token:
            raise RuntimeError("登录未返回 access_token")
        headers = {"Authorization": f"Bearer {token}"}
        started = time.perf_counter()
        created = client.post(
            f"{BASE_URL}/agent/tasks",
            headers=headers,
            json={"goal": goal, "template": "auto", "output_format": "markdown",
                  "scope": ["file_resources"], "top_k": 5},
        )
        created.raise_for_status()
        task_id = created.json()["task_id"]
        while True:
            task_resp = client.get(f"{BASE_URL}/agent/tasks/{task_id}", headers=headers)
            task_resp.raise_for_status()
            task = task_resp.json()
            if task.get("status") in {"completed", "failed", "cancelled"}:
                break
            if time.perf_counter() - started > 600:
                raise TimeoutError("Agent 任务超过 10 分钟未完成")
            time.sleep(2)
        report["task_id"] = task_id
        report["cases"].append(_safe_result(task, (time.perf_counter() - started) * 1000))

    item = report["cases"][0]
    pipeline_passed = item["status"] == "completed" and item["evidence_count"] == len(PATHS) \
        and item["draft_chars"] >= 100 and item["quality_passed"] is True \
        and item["has_multi_document_structure"]
    model_passed = not item["degraded"]
    require_llm = os.getenv("AGENT_REQUIRE_LLM", "1") == "1"
    report["summary"] = {
        "passed": pipeline_passed and (model_passed if require_llm else True),
        "pipeline_passed": pipeline_passed,
        "model_generation_passed": model_passed,
        "required_llm": require_llm,
        "required_documents": len(PATHS),
        "retrieved_documents": item["evidence_count"],
    }
    out_dir = ROOT / "data" / "evaluations"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    output = out_dir / f"agent-{stamp}.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"report": str(output), "summary": report["summary"], "case": item}, ensure_ascii=False, indent=2))
    return 0 if report["summary"]["passed"] else 2


if __name__ == "__main__":
    sys.exit(main())
