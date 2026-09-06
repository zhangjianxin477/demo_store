from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.passive.service import passive_service

router = APIRouter(prefix="/passive", tags=["passive-ingest"])


class SkillConfigPatch(BaseModel):
    schedule: Optional[Dict[str, Any]] = None
    sources: Optional[list[Dict[str, Any]]] = None
    filters: Optional[Dict[str, Any]] = None
    output: Optional[Dict[str, Any]] = None
    delivery: Optional[Dict[str, Any]] = None
    limits: Optional[Dict[str, Any]] = None
    workflow: Optional[Dict[str, Any]] = None
    triggers: Optional[list[str]] = None


class RunRequest(BaseModel):
    dry_run: bool = False
    force: bool = False


class SkillContentUpdate(BaseModel):
    content: str = Field(min_length=1, max_length=512 * 1024)
    config: Dict[str, Any] = Field(default_factory=dict)


class SkillMatchRequest(BaseModel):
    goal: str = Field(min_length=1, max_length=2000)


@router.get("/skills")
async def list_passive_skills():
    return {"success": True, "skills": passive_service.registry.list()}


@router.post("/skills/match")
async def match_passive_skill(payload: SkillMatchRequest):
    skill = passive_service.registry.match_enabled(payload.goal)
    return {"success": True, "matched": bool(skill), "skill": passive_service.registry._public(skill) if skill else None}


@router.get("/skills/{skill_id}")
async def get_passive_skill(skill_id: str):
    item = passive_service.registry.get(skill_id)
    if not item:
        raise HTTPException(status_code=404, detail="Skill 不存在")
    return {"success": True, "skill": passive_service.registry._public(item), "content": passive_service.registry.read_body(skill_id)}


@router.post("/skills/upload")
async def upload_passive_skill(file: UploadFile = File(...), config_json: str = Form("")):
    if not file.filename or not file.filename.lower().endswith((".md", ".markdown")):
        raise HTTPException(status_code=400, detail="请上传 SKILL.md 或 Markdown 文件")
    raw = await file.read()
    if len(raw) > 512 * 1024:
        raise HTTPException(status_code=400, detail="Skill 文件不能超过 512KB")
    try:
        content = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(status_code=400, detail="Skill 必须使用 UTF-8 编码")
    config = {}
    if config_json:
        try:
            config = json.loads(config_json)
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail=f"config_json 格式错误: {exc}")
    parsed = passive_service.registry.parse_markdown(content, file.filename)
    skill = passive_service.registry.install(content, file.filename, config)
    return {"success": True, "skill": skill, "parsed": {"skill_id": parsed["skill_id"], "name": parsed["name"], "triggers": parsed["triggers"], "discovered_urls": parsed["discovered_urls"]}}


@router.patch("/skills/{skill_id}")
async def patch_passive_skill(skill_id: str, payload: SkillConfigPatch):
    try:
        data = {key: value for key, value in payload.model_dump().items() if value is not None}
        return {"success": True, "skill": passive_service.registry.update_config(skill_id, data)}
    except KeyError:
        raise HTTPException(status_code=404, detail="Skill 不存在")


@router.put("/skills/{skill_id}/content")
async def update_passive_skill_content(skill_id: str, payload: SkillContentUpdate):
    current = passive_service.registry.get(skill_id)
    if not current:
        raise HTTPException(status_code=404, detail="Skill 不存在")
    parsed = passive_service.registry.parse_markdown(payload.content, "SKILL.md")
    if parsed["skill_id"] != skill_id:
        raise HTTPException(status_code=400, detail=f"Skill 内容中的 name/skill_id 必须保持为 {skill_id}")
    current_status = current.get("status", "disabled")
    skill = passive_service.registry.install(payload.content, "SKILL.md", payload.config)
    # Editing正文 should not unexpectedly turn an enabled Skill off. The new
    # version inherits the previous mount state and can be used immediately.
    if current_status == "enabled":
        passive_service.registry.enable(skill_id, skill.get("version"))
        skill = passive_service.registry.get(skill_id) or skill
    return {"success": True, "skill": passive_service.registry._public(skill), "message": "已保存为新版本"}


@router.post("/skills/{skill_id}/enable")
async def enable_passive_skill(skill_id: str, version: Optional[str] = None):
    try:
        return {"success": True, "skill": passive_service.registry.enable(skill_id, version)}
    except KeyError:
        raise HTTPException(status_code=404, detail="Skill 或版本不存在")


@router.post("/skills/{skill_id}/disable")
async def disable_passive_skill(skill_id: str):
    try:
        return {"success": True, "skill": passive_service.registry.disable(skill_id)}
    except KeyError:
        raise HTTPException(status_code=404, detail="Skill 不存在")


@router.delete("/skills/{skill_id}")
async def uninstall_passive_skill(skill_id: str):
    current = passive_service.registry.get(skill_id)
    if not current:
        raise HTTPException(status_code=404, detail="Skill 不存在")
    if current.get("status") == "enabled":
        # A mounted Skill is a live automation dependency. It must be
        # explicitly disabled before it can be archived, preventing an
        # accidental delete while scheduled runs are active.
        raise HTTPException(status_code=409, detail="已挂载 Skill 不能直接删除，请先停用后再卸载")
    try:
        if not passive_service.registry.uninstall(skill_id):
            raise HTTPException(status_code=404, detail="Skill 不存在")
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {"success": True, "status": "archived", "message": "Skill 已卸载，历史运行和版本保留"}


@router.post("/skills/{skill_id}/dry-run")
async def dry_run_passive_skill(skill_id: str):
    try:
        return {"success": True, "run": await passive_service.start_run(skill_id, dry_run=True, force=True)}
    except KeyError:
        raise HTTPException(status_code=404, detail="Skill 不存在")


@router.post("/skills/{skill_id}/run")
async def run_passive_skill(skill_id: str, payload: RunRequest = RunRequest()):
    try:
        return {"success": True, "run": await passive_service.start_run(skill_id, dry_run=payload.dry_run, force=payload.force)}
    except KeyError:
        raise HTTPException(status_code=404, detail="Skill 不存在")


@router.post("/weekly-review/run")
async def run_weekly_review(payload: RunRequest = RunRequest()):
    """手动触发一次按文件资源时间范围生成的每周主题综述。"""
    return {"success": True, "run": await passive_service.start_weekly_review(force=payload.force, dry_run=payload.dry_run)}


@router.get("/runs")
async def list_passive_runs(limit: int = 50):
    return {"success": True, "runs": passive_service.list_runs(limit)}


@router.get("/runs/{run_id}")
async def get_passive_run(run_id: str):
    run = passive_service.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="运行记录不存在")
    return {"success": True, "run": run}


@router.delete("/runs/{run_id}")
async def delete_passive_run(run_id: str):
    if not passive_service.delete_run(run_id):
        raise HTTPException(status_code=404, detail="运行记录不存在")
    return {"success": True, "run_id": run_id, "message": "运行记录已删除，日报文件仍保留"}


@router.get("/runs/{run_id}/artifact")
async def get_passive_artifact(run_id: str, format: str = "markdown"):
    run = passive_service.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="运行记录不存在")
    path_value = run.get("html_artifact_path") if format.lower() == "html" else run.get("artifact_path")
    if not path_value or not Path(path_value).is_file():
        raise HTTPException(status_code=404, detail="日报产物尚未生成")
    return FileResponse(path_value, filename=Path(path_value).name, media_type="text/html" if format.lower() == "html" else "text/markdown")
