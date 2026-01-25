"""
文件管理服务
负责处理文件上传、存储、移动和清理
"""

import os
import shutil
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Dict, List, Any
from loguru import logger
import hashlib
import json

import numpy as np

from .models import ReportContent, TextSegment

class FileManager:
    """ESG系统文件管理器"""

    def __init__(self, base_upload_dir: str = "../../uploads"):
        # Use absolute path from file location to ensure correct uploads directory
        # Path: backend/src/esg_encoding/ -> backend/ -> ESG DEMO/ -> uploads/
        if base_upload_dir == "../../uploads":
            self.base_dir = Path(__file__).parent.parent.parent.parent / "uploads"
        else:
            self.base_dir = Path(base_upload_dir)
        self.reports_dir = self.base_dir / "reports"
        self.metrics_dir = self.base_dir / "metrics"
        self.outputs_dir = self.base_dir / "outputs"
        
        # 子目录
        self.pending_reports = self.reports_dir / "pending"
        self.processed_reports = self.reports_dir / "processed"
        self.failed_reports = self.reports_dir / "failed"
        
        self.excel_metrics = self.metrics_dir / "excel"
        self.json_metrics = self.metrics_dir / "json"
        
        self.compliance_outputs = self.outputs_dir / "compliance_reports"
        self.markdown_outputs = self.outputs_dir / "markdown"
        self.embeddings_outputs = self.outputs_dir / "embeddings"
        
        # 确保所有目录存在
        self._create_directories()
        
        # 文件元数据存储
        self.metadata_file = self.base_dir / "file_metadata.json"
        self.metadata = self._load_metadata()
    
    def _create_directories(self):
        """创建所有必要的目录"""
        directories = [
            self.pending_reports,
            self.processed_reports,
            self.failed_reports,
            self.excel_metrics,
            self.json_metrics,
            self.compliance_outputs,
            self.markdown_outputs,
            self.embeddings_outputs
        ]
        
        for directory in directories:
            directory.mkdir(parents=True, exist_ok=True)
            logger.info(f"确保目录存在: {directory}")
    
    def _load_metadata(self) -> Dict:
        """加载文件元数据"""
        if self.metadata_file.exists():
            try:
                with open(self.metadata_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"加载元数据失败: {e}")
        return {"files": {}, "sessions": {}}
    
    def _save_metadata(self):
        """保存文件元数据"""
        try:
            with open(self.metadata_file, 'w', encoding='utf-8') as f:
                json.dump(self.metadata, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"保存元数据失败: {e}")
    
    def _generate_file_hash(self, file_path: Path) -> str:
        """生成文件哈希值"""
        hash_md5 = hashlib.md5()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                hash_md5.update(chunk)
        return hash_md5.hexdigest()
    
    def save_uploaded_file(self, file_content: bytes, filename: str, 
                          file_type: str = "report", industry: str = None,
                          framework: str = None, semi_industry: str = None,
                          user_id: Optional[int] = None) -> Dict[str, str]:
        """
        保存上传的文件
        
        Args:
            file_content: 文件内容
            filename: 原始文件名
            file_type: 文件类型 ('report', 'metrics')
            industry: 行业分类
            framework: 框架类型
            semi_industry: 子行业
            user_id: 用户ID
            
        Returns:
            文件信息字典
        """
        # 生成唯一文件ID
        file_id = str(uuid.uuid4())
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        # 获取文件扩展名
        file_extension = Path(filename).suffix
        safe_filename = f"{timestamp}_{file_id}{file_extension}"
        
        # 根据文件类型选择存储目录
        if file_type == "report":
            target_dir = self.pending_reports
        elif file_type == "metrics":
            if file_extension.lower() in ['.xlsx', '.xls']:
                target_dir = self.excel_metrics
            else:
                target_dir = self.json_metrics
        else:
            raise ValueError(f"不支持的文件类型: {file_type}")
        
        target_path = target_dir / safe_filename
        
        # 保存文件
        try:
            with open(target_path, 'wb') as f:
                f.write(file_content)
            
            # 生成文件哈希
            file_hash = self._generate_file_hash(target_path)
            
            # 记录文件元数据
            file_info = {
                "file_id": file_id,
                "original_name": filename,
                "safe_filename": safe_filename,
                "file_path": str(target_path),
                "file_type": file_type,
                "file_size": len(file_content),
                "file_hash": file_hash,
                "upload_time": datetime.now().isoformat(),
                "status": "pending" if file_type == "report" else "uploaded",
                "processing_history": [],
                "industry": industry,
                "framework": framework,
                "semi_industry": semi_industry,
                "user_id": user_id
            }
            
            self.metadata["files"][file_id] = file_info
            self._save_metadata()
            
            logger.info(f"文件保存成功: {filename} -> {safe_filename}")
            return file_info
            
        except Exception as e:
            logger.error(f"保存文件失败: {e}")
            # 清理可能创建的文件
            if target_path.exists():
                target_path.unlink()
            raise
    
    def move_report_file(self, file_id: str, status: str) -> bool:
        """
        移动报告文件到对应状态目录
        
        Args:
            file_id: 文件ID
            status: 目标状态 ('processed', 'failed')
            
        Returns:
            是否移动成功
        """
        if file_id not in self.metadata["files"]:
            logger.error(f"文件ID不存在: {file_id}")
            return False
        
        file_info = self.metadata["files"][file_id]
        current_path = Path(file_info["file_path"])
        
        if not current_path.exists():
            logger.error(f"源文件不存在: {current_path}")
            return False
        
        # 确定目标目录
        if status == "processed":
            target_dir = self.processed_reports
        elif status == "failed":
            target_dir = self.failed_reports
        else:
            logger.error(f"不支持的状态: {status}")
            return False
        
        target_path = target_dir / current_path.name
        
        try:
            # Move extracted markdown (created during PDF extraction) together with the PDF
            extracted_src = current_path.parent / f"{current_path.stem}_extracted.md"
            extracted_dst = target_dir / extracted_src.name

            shutil.move(str(current_path), str(target_path))

            if extracted_src.exists():
                try:
                    shutil.move(str(extracted_src), str(extracted_dst))
                    file_info["extracted_md_path"] = str(extracted_dst)
                    logger.info(f"Moved extracted markdown to: {extracted_dst}")
                except Exception as e:
                    logger.warning(f"Failed to move extracted markdown {extracted_src} -> {extracted_dst}: {e}")
            
            # Also support legacy markdown saved as <stem>.md (older runs)
            legacy_src = current_path.parent / f"{current_path.stem}.md"
            legacy_dst = target_dir / legacy_src.name
            if legacy_src.exists() and not extracted_src.exists():
                try:
                    shutil.move(str(legacy_src), str(legacy_dst))
                    file_info["extracted_md_path"] = str(legacy_dst)
                    logger.info(f"Moved legacy markdown to: {legacy_dst}")
                except Exception as e:
                    logger.warning(f"Failed to move legacy markdown {legacy_src} -> {legacy_dst}: {e}")


            # 更新元数据
            file_info["file_path"] = str(target_path)
            file_info["status"] = status
            file_info["processing_history"].append({
                "timestamp": datetime.now().isoformat(),
                "action": f"moved_to_{status}",
                "previous_path": str(current_path),
                "new_path": str(target_path)
            })
            
            self._save_metadata()
            logger.info(f"文件移动成功: {current_path} -> {target_path}")
            return True
            
        except Exception as e:
            logger.error(f"移动文件失败: {e}")
            return False
    
    def get_file_info(self, file_id: str, user_id: Optional[int] = None) -> Optional[Dict]:
        """
        获取文件信息
        
        Args:
            file_id: 文件ID
            user_id: 用户ID (如果提供,会检查文件是否属于该用户)
            
        Returns:
            文件信息字典,如果文件不存在或不属于该用户则返回None
        """
        file_info = self.metadata["files"].get(file_id)
        if not file_info:
            return None
        
        # 如果提供了user_id,检查文件是否属于该用户
        if user_id is not None:
            file_user_id = file_info.get("user_id")
            if file_user_id is not None and file_user_id != user_id:
                return None
        
        return file_info

    # =============================
    # Embeddings / Segments artifacts
    # =============================

    def save_report_artifacts(self, file_id: str, report_content: ReportContent) -> Dict[str, str]:
        """Persist segments + embeddings for fast chat retrieval.

        Files written under: uploads/outputs/embeddings/
          - {file_id}_segments.json
          - {file_id}_embeddings.npz  (float32 matrix)
          - {file_id}_embeddings_meta.json

        Returns:
            dict with paths.
        """
        self.embeddings_outputs.mkdir(parents=True, exist_ok=True)

        segments_path = self.embeddings_outputs / f"{file_id}_segments.json"
        emb_path = self.embeddings_outputs / f"{file_id}_embeddings.npz"
        meta_path = self.embeddings_outputs / f"{file_id}_embeddings_meta.json"

        # 1) segments
        segments = report_content.document_content.segments or []
        segments_payload = [
            {
                "segment_id": s.segment_id,
                "content": s.content,
                "page_number": s.page_number,
                "position_y": s.position_y,
            }
            for s in segments
        ]
        segments_path.write_text(json.dumps(segments_payload, ensure_ascii=False, indent=2), encoding="utf-8")

        # 2) embeddings
        emb_objs = report_content.embeddings or []
        seg_ids = [e.segment_id for e in emb_objs]
        emb_matrix = np.array([e.embedding for e in emb_objs], dtype=np.float32) if emb_objs else np.zeros((0, 0), dtype=np.float32)
        np.savez_compressed(emb_path, embeddings=emb_matrix, segment_ids=np.array(seg_ids, dtype=object))

        # 3) meta
        meta = {
            "file_id": file_id,
            "document_id": report_content.document_id,
            "embedding_dim": int(emb_matrix.shape[1]) if emb_matrix.ndim == 2 and emb_matrix.size else 0,
            "segment_count": int(len(segments)),
            "embedding_count": int(len(seg_ids)),
            "saved_at": datetime.now().isoformat(),
        }
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

        # 4) Update file metadata (best-effort)
        try:
            if file_id in self.metadata.get("files", {}):
                info = self.metadata["files"][file_id]
                info["segments_path"] = str(segments_path)
                info["embeddings_path"] = str(emb_path)
                info["embeddings_meta_path"] = str(meta_path)
                self._save_metadata()
        except Exception as e:
            logger.warning(f"Failed to update file metadata with artifact paths for {file_id}: {e}")

        return {
            "segments_path": str(segments_path),
            "embeddings_path": str(emb_path),
            "embeddings_meta_path": str(meta_path),
        }

    def load_report_artifacts(self, file_id: str) -> Optional[Dict[str, Any]]:
        """Load persisted segments + embeddings.

        Returns None if artifacts are missing.
        """
        # Prefer paths saved in metadata
        info = self.metadata.get("files", {}).get(file_id, {})
        seg_path = Path(info.get("segments_path")) if info.get("segments_path") else (self.embeddings_outputs / f"{file_id}_segments.json")
        emb_path = Path(info.get("embeddings_path")) if info.get("embeddings_path") else (self.embeddings_outputs / f"{file_id}_embeddings.npz")

        if not seg_path.exists() or not emb_path.exists():
            return None

        try:
            segments_raw = json.loads(seg_path.read_text(encoding="utf-8"))
            segments: List[TextSegment] = []
            for s in segments_raw:
                if not isinstance(s, dict):
                    continue
                try:
                    segments.append(
                        TextSegment(
                            segment_id=str(s.get("segment_id")),
                            content=str(s.get("content") or ""),
                            page_number=int(s.get("page_number") or 1),
                            position_y=float(s.get("position_y") or 0.0),
                        )
                    )
                except Exception:
                    continue

            data = np.load(emb_path, allow_pickle=True)
            emb_matrix = data.get("embeddings")
            seg_ids = data.get("segment_ids")
            seg_ids = [str(x) for x in (seg_ids.tolist() if seg_ids is not None else [])]

            return {
                "segments": segments,
                "embedding_matrix": emb_matrix,
                "embedding_segment_ids": seg_ids,
                "segments_path": str(seg_path),
                "embeddings_path": str(emb_path),
            }
        except Exception as e:
            logger.warning(f"Failed to load report artifacts for {file_id}: {e}")
            return None
    
    def list_files_by_type(self, file_type: str, status: Optional[str] = None, 
                          user_id: Optional[int] = None) -> List[Dict]:
        """
        按类型和状态列出文件
        
        Args:
            file_type: 文件类型
            status: 文件状态（可选）
            user_id: 用户ID (如果提供,只返回该用户的文件)
            
        Returns:
            文件信息列表
        """
        files = []
        for file_id, file_info in self.metadata["files"].items():
            # 检查文件类型
            if file_info["file_type"] != file_type:
                continue
            
            # 检查状态
            if status is not None and file_info["status"] != status:
                continue
            
            # 检查用户ID
            if user_id is not None:
                file_user_id = file_info.get("user_id")
                # Backward-compatibility:
                # - Historical runs may have user_id omitted (None).
                # - For those "public" files, allow listing for any logged-in user.
                # This prevents dashboards from becoming empty after auth/user resets.
                if file_user_id is not None and file_user_id != user_id:
                    continue
            
            files.append(file_info)
        
        # 按上传时间排序
        files.sort(key=lambda x: x["upload_time"], reverse=True)
        return files
    
    def list_user_files(self, user_id: int, file_type: Optional[str] = None, 
                       status: Optional[str] = None) -> List[Dict]:
        """
        列出指定用户的所有文件
        
        Args:
            user_id: 用户ID
            file_type: 文件类型过滤 (可选)
            status: 文件状态过滤 (可选)
            
        Returns:
            文件信息列表
        """
        files = []
        for file_id, file_info in self.metadata["files"].items():
            # 检查用户ID
            file_user_id = file_info.get("user_id")
            # Same compatibility as list_files_by_type:
            # treat missing user_id as public so the user can still see old data.
            if file_user_id is not None and file_user_id != user_id:
                continue
            
            # 检查文件类型
            if file_type is not None and file_info["file_type"] != file_type:
                continue
            
            # 检查状态
            if status is not None and file_info["status"] != status:
                continue
            
            files.append(file_info)
        
        # 按上传时间排序
        files.sort(key=lambda x: x["upload_time"], reverse=True)
        return files
    
    def cleanup_old_files(self, days: int = 30) -> int:
        """
        清理指定天数前的文件
        
        Args:
            days: 保留天数
            
        Returns:
            清理的文件数量
        """
        cutoff_time = datetime.now() - timedelta(days=days)
        cleaned_count = 0
        
        files_to_remove = []
        for file_id, file_info in self.metadata["files"].items():
            upload_time = datetime.fromisoformat(file_info["upload_time"])
            if upload_time < cutoff_time:
                file_path = Path(file_info["file_path"])
                if file_path.exists():
                    try:
                        file_path.unlink()
                        logger.info(f"清理旧文件: {file_path}")
                        cleaned_count += 1
                    except Exception as e:
                        logger.error(f"清理文件失败: {e}")
                
                files_to_remove.append(file_id)
        
        # 从元数据中移除
        for file_id in files_to_remove:
            del self.metadata["files"][file_id]
        
        if files_to_remove:
            self._save_metadata()
        
        logger.info(f"清理完成，共清理 {cleaned_count} 个文件")
        return cleaned_count
    
    def get_storage_stats(self) -> Dict:
        """获取存储统计信息"""
        stats = {
            "total_files": len(self.metadata["files"]),
            "by_type": {},
            "by_status": {},
            "storage_size": 0,
            "directories": {}
        }
        
        # 按类型和状态统计
        for file_info in self.metadata["files"].values():
            file_type = file_info["file_type"]
            status = file_info["status"]
            file_size = file_info.get("file_size", 0)
            
            stats["by_type"][file_type] = stats["by_type"].get(file_type, 0) + 1
            stats["by_status"][status] = stats["by_status"].get(status, 0) + 1
            stats["storage_size"] += file_size
        
        # 目录大小统计
        def get_dir_size(directory):
            total_size = 0
            if directory.exists():
                for path in directory.rglob('*'):
                    if path.is_file():
                        total_size += path.stat().st_size
            return total_size
        
        stats["directories"] = {
            "reports": get_dir_size(self.reports_dir),
            "metrics": get_dir_size(self.metrics_dir),
            "outputs": get_dir_size(self.outputs_dir)
        }
        
        return stats


# 全局文件管理器实例
file_manager = FileManager()