import difflib
import logging
from typing import Dict, List, Any, Optional
from datetime import datetime

from app.models.wiki import wiki_store

logger = logging.getLogger(__name__)


class VersionDiffService:

    def compute_diff(self, old_text: str, new_text: str,
                     context_lines: int = 3) -> Dict[str, Any]:
        old_lines = old_text.splitlines(keepends=True)
        new_lines = new_text.splitlines(keepends=True)

        differ = difflib.SequenceMatcher(None, old_lines, new_lines)
        opcodes = differ.get_opcodes()

        changes = []
        for tag, i1, i2, j1, j2 in opcodes:
            if tag == 'equal':
                continue
            change = {
                "type": tag,
                "old_start": i1 + 1,
                "old_end": i2,
                "new_start": j1 + 1,
                "new_end": j2,
            }
            if tag in ('replace', 'delete'):
                change["old_content"] = "".join(old_lines[i1:i2])
            if tag in ('replace', 'insert'):
                change["new_content"] = "".join(new_lines[j1:j2])
            changes.append(change)

        unified = difflib.unified_diff(
            old_lines, new_lines,
            fromfile="旧版本", tofile="新版本",
            lineterm="", n=context_lines,
        )
        unified_diff = "\n".join(unified)

        html_diff = difflib.HtmlDiff(tabsize=4, wrapcolumn=80)
        try:
            html_diff_result = html_diff.make_table(
                old_lines, new_lines,
                fromdesc="旧版本", todesc="新版本",
                context=True, numlines=context_lines,
            )
        except Exception:
            html_diff_result = ""

        stats = self._compute_stats(old_text, new_text, changes)

        return {
            "success": True,
            "changes": changes,
            "unified_diff": unified_diff,
            "html_diff": html_diff_result,
            "stats": stats,
        }

    def compute_version_diff(self, page_id: str, version1: int,
                              version2: int) -> Dict[str, Any]:
        versions = wiki_store._versions.get(page_id, [])
        v1_data = None
        v2_data = None
        for v in versions:
            if v.version_number == version1:
                v1_data = v
            if v.version_number == version2:
                v2_data = v

        if not v1_data:
            return {"success": False, "error": f"版本 {version1} 不存在"}
        if not v2_data:
            return {"success": False, "error": f"版本 {version2} 不存在"}

        result = self.compute_diff(v1_data.content, v2_data.content)
        result["version1"] = {
            "number": v1_data.version_number,
            "author": v1_data.author,
            "created_at": v1_data.created_at,
            "change_summary": v1_data.change_summary,
        }
        result["version2"] = {
            "number": v2_data.version_number,
            "author": v2_data.author,
            "created_at": v2_data.created_at,
            "change_summary": v2_data.change_summary,
        }
        return result

    def compute_page_history_diff(self, page_id: str) -> Dict[str, Any]:
        versions = wiki_store._versions.get(page_id, [])
        if len(versions) < 2:
            return {"success": True, "diffs": [], "total_changes": 0}

        diffs = []
        for i in range(1, len(versions)):
            v_old = versions[i - 1]
            v_new = versions[i]
            diff = self.compute_diff(v_old.content, v_new.content, context_lines=1)
            diffs.append({
                "from_version": v_old.version_number,
                "to_version": v_new.version_number,
                "author": v_new.author,
                "change_summary": v_new.change_summary,
                "created_at": v_new.created_at,
                "stats": diff["stats"],
                "changes": diff["changes"],
            })

        return {
            "success": True,
            "diffs": diffs,
            "total_changes": len(diffs),
        }

    def _compute_stats(self, old_text: str, new_text: str,
                       changes: List[Dict]) -> Dict[str, Any]:
        lines_added = sum(
            c["new_content"].count("\n") + 1
            for c in changes if c["type"] in ("insert", "replace") and "new_content" in c
        )
        lines_removed = sum(
            c["old_content"].count("\n") + 1
            for c in changes if c["type"] in ("delete", "replace") and "old_content" in c
        )

        chars_added = sum(
            len(c.get("new_content", ""))
            for c in changes if c["type"] in ("insert", "replace")
        )
        chars_removed = sum(
            len(c.get("old_content", ""))
            for c in changes if c["type"] in ("delete", "replace")
        )

        similarity = 0.0
        if old_text or new_text:
            ratio = difflib.SequenceMatcher(None, old_text, new_text).ratio()
            similarity = round(ratio * 100, 1)

        return {
            "lines_added": lines_added,
            "lines_removed": lines_removed,
            "chars_added": chars_added,
            "chars_removed": chars_removed,
            "change_count": len(changes),
            "similarity": similarity,
        }


version_diff_service = VersionDiffService()
