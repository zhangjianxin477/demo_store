import os
import json
import uuid
import shutil
import logging
import zipfile
from typing import List, Optional, Dict, Any
from datetime import datetime

from app.core.config import settings

logger = logging.getLogger(__name__)


class BackupService:
    def __init__(self):
        self._backup_dir = os.path.join(settings.DATA_DIR, "backups")
        os.makedirs(self._backup_dir, exist_ok=True)
        self._manifest_file = os.path.join(self._backup_dir, "manifest.json")
        self._manifest: List[Dict] = []
        self._load_manifest()

    def _load_manifest(self):
        if os.path.exists(self._manifest_file):
            try:
                with open(self._manifest_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self._manifest = data.get("backups", [])
            except Exception as e:
                logger.error(f"备份清单加载失败: {e}")

    def _save_manifest(self):
        data = {"backups": self._manifest}
        with open(self._manifest_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def create_backup(self, description: str = "", backup_type: str = "full",
                      created_by: str = "admin") -> Dict:
        backup_id = f"backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
        backup_path = os.path.join(self._backup_dir, f"{backup_id}.zip")

        now = datetime.now()
        try:
            with zipfile.ZipFile(backup_path, 'w', zipfile.ZIP_DEFLATED) as zf:
                if backup_type == "full":
                    self._add_dir_to_zip(zf, settings.DATA_DIR, "data", exclude_dirs=["backups", "search_index"])
                elif backup_type == "wiki_only":
                    wiki_dir = os.path.join(settings.DATA_DIR, "wiki")
                    if os.path.exists(wiki_dir):
                        self._add_dir_to_zip(zf, wiki_dir, "data/wiki")
                elif backup_type == "incremental":
                    cutoff = now.strftime("%Y-%m-%d")
                    self._add_dir_to_zip(zf, settings.DATA_DIR, "data",
                                         exclude_dirs=["backups", "search_index"],
                                         since_date=cutoff)

                metadata = {
                    "backup_id": backup_id,
                    "backup_type": backup_type,
                    "description": description,
                    "created_at": now.isoformat(),
                    "created_by": created_by,
                    "file_count": len(zf.namelist()),
                }
                zf.writestr("backup_metadata.json", json.dumps(metadata, ensure_ascii=False, indent=2))

            file_size = os.path.getsize(backup_path)
            entry = {
                "backup_id": backup_id,
                "backup_type": backup_type,
                "description": description,
                "created_at": now.isoformat(),
                "created_by": created_by,
                "file_size": file_size,
                "file_size_mb": round(file_size / (1024 * 1024), 2),
                "filepath": backup_path,
            }
            self._manifest.append(entry)
            self._save_manifest()

            logger.info(f"备份创建成功: {backup_id} ({entry['file_size_mb']}MB)")
            return {"success": True, **entry}

        except Exception as e:
            logger.error(f"备份创建失败: {e}")
            if os.path.exists(backup_path):
                os.remove(backup_path)
            return {"success": False, "error": str(e)}

    def _add_dir_to_zip(self, zf: zipfile.ZipFile, dir_path: str, zip_prefix: str,
                         exclude_dirs: Optional[List[str]] = None,
                         since_date: Optional[str] = None):
        exclude = set(exclude_dirs or [])
        for root, dirs, files in os.walk(dir_path):
            dirs[:] = [d for d in dirs if d not in exclude]
            for file in files:
                file_path = os.path.join(root, file)
                if since_date:
                    try:
                        mtime = datetime.fromtimestamp(os.path.getmtime(file_path)).strftime("%Y-%m-%d")
                        if mtime < since_date:
                            continue
                    except Exception:
                        pass
                arcname = os.path.join(zip_prefix, os.path.relpath(file_path, dir_path))
                try:
                    zf.write(file_path, arcname)
                except Exception as e:
                    logger.warning(f"跳过文件 {file_path}: {e}")

    def restore_backup(self, backup_id: str, restore_type: str = "replace",
                       created_by: str = "admin") -> Dict:
        entry = None
        for b in self._manifest:
            if b["backup_id"] == backup_id:
                entry = b
                break
        if not entry:
            return {"success": False, "error": "备份不存在"}

        backup_path = entry.get("filepath", "")
        if not os.path.exists(backup_path):
            return {"success": False, "error": "备份文件不存在"}

        try:
            safety_backup_id = f"pre_restore_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            safety_path = os.path.join(self._backup_dir, f"{safety_backup_id}.zip")
            with zipfile.ZipFile(safety_path, 'w', zipfile.ZIP_DEFLATED) as zf:
                self._add_dir_to_zip(zf, settings.DATA_DIR, "data",
                                     exclude_dirs=["backups", "search_index"])

            if restore_type == "replace":
                wiki_dir = os.path.join(settings.DATA_DIR, "wiki")
                if os.path.exists(wiki_dir):
                    shutil.rmtree(wiki_dir)

            with zipfile.ZipFile(backup_path, 'r') as zf:
                zf.extractall(settings.DATA_DIR)

            logger.info(f"备份恢复成功: {backup_id}")
            return {
                "success": True,
                "backup_id": backup_id,
                "safety_backup": safety_backup_id,
                "message": "备份已恢复，请重启服务以加载新数据",
            }

        except Exception as e:
            logger.error(f"备份恢复失败: {e}")
            return {"success": False, "error": str(e)}

    def list_backups(self) -> List[Dict]:
        valid = []
        for entry in self._manifest:
            if os.path.exists(entry.get("filepath", "")):
                valid.append(entry)
            else:
                entry["file_missing"] = True
                valid.append(entry)
        return sorted(valid, key=lambda x: x.get("created_at", ""), reverse=True)

    def delete_backup(self, backup_id: str) -> bool:
        for i, entry in enumerate(self._manifest):
            if entry["backup_id"] == backup_id:
                filepath = entry.get("filepath", "")
                if os.path.exists(filepath):
                    os.remove(filepath)
                self._manifest.pop(i)
                self._save_manifest()
                return True
        return False

    def download_backup(self, backup_id: str) -> Optional[str]:
        for entry in self._manifest:
            if entry["backup_id"] == backup_id:
                filepath = entry.get("filepath", "")
                if os.path.exists(filepath):
                    return filepath
        return None

    def get_stats(self) -> Dict:
        total_size = sum(e.get("file_size", 0) for e in self._manifest)
        type_counts = {}
        for e in self._manifest:
            bt = e.get("backup_type", "unknown")
            type_counts[bt] = type_counts.get(bt, 0) + 1
        return {
            "total_backups": len(self._manifest),
            "total_size_mb": round(total_size / (1024 * 1024), 2),
            "type_counts": type_counts,
        }


backup_service = BackupService()
