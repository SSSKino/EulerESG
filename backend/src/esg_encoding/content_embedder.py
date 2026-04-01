"""
简化的内容嵌入器

直接使用预训练的BGE-M3模型生成文本嵌入。
"""

from typing import List
import os
import torch
from sentence_transformers import SentenceTransformer
from loguru import logger

from .hf_cache import prefer_local_model

from .models import TextSegment, SegmentEmbedding, DocumentContent, ReportContent, ProcessingConfig
from .exceptions import ContentEmbeddingError


class ContentEmbedder:
    """
    简化的内容嵌入器
    
    直接使用BGE-M3模型生成嵌入向量
    """
    
    def __init__(self, config: ProcessingConfig = None):
        """初始化嵌入器"""
        self.config = config or ProcessingConfig()
        self.logger = logger.bind(component="ContentEmbedder")
        
        # 设置设备
        self.device = torch.device(self.config.device if torch.cuda.is_available() else "cpu")
        
        # 加载模型
        self.model = None
        self._load_model()

    def _load_model(self):
        """加载嵌入模型（优先本地 HF cache，缺失/损坏才允许远端下载）"""

        def _cleanup_corrupt_snapshot(err: Exception, hf_home: str) -> bool:
            """
            If HF cache contains an incomplete snapshot (common after interrupted downloads),
            sentence-transformers may crash with missing files like '1_Pooling/config.json'.

            We try to locate the snapshot dir from the exception message and remove it,
            so a subsequent remote load can re-download a clean snapshot.
            """
            import re
            import shutil
            from pathlib import Path

            msg = str(err)

            # Common pattern: "... No such file or directory: '/root/.cache/.../snapshots/<sha>/...'"
            m = re.search(r"No such file or directory: '([^']+)'", msg)
            if not m:
                return False

            missing_path = Path(m.group(1))
            p = str(missing_path)

            if "/snapshots/" not in p:
                return False

            prefix, rest = p.split("/snapshots/", 1)
            sha = rest.split("/", 1)[0]
            snap_dir = Path(prefix) / "snapshots" / sha

            try:
                hf_root = Path(hf_home).resolve()
                snap_dir_resolved = snap_dir.resolve()
            except Exception:
                return False

            # Only delete if it's under HF_HOME to avoid accidental removal.
            if not str(snap_dir_resolved).startswith(str(hf_root)):
                return False

            try:
                shutil.rmtree(snap_dir_resolved, ignore_errors=True)
                return True
            except Exception:
                return False

        try:
            repo_id = str(getattr(self.config, "embedding_model", "BAAI/bge-m3"))
            hf_home = os.getenv("HF_HOME", "/root/.cache/huggingface")

            explicit_path = os.getenv("LOCAL_EMBEDDINGS_MODEL_PATH") or None
            allow_remote = os.getenv("HF_ALLOW_ONLINE", "1") != "0"

            def _resolve_local() -> str | None:
                ref = prefer_local_model(repo_id, explicit_local_path=explicit_path, hf_home=hf_home)
                return ref.local_path

            local_path = _resolve_local()
            model_id = local_path or repo_id

            if local_path:
                # Ensure we don't accidentally hit the network when user has a staged cache volume.
                os.environ.setdefault("HF_HUB_OFFLINE", "1")
                os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
                self.logger.info(f"加载嵌入模型（本地缓存）: {repo_id} -> {local_path}")
            else:
                self.logger.info(f"加载嵌入模型（远端/自动缓存）: {repo_id}")

            try:
                self.model = SentenceTransformer(model_id, device=self.device, cache_folder=hf_home)
            except FileNotFoundError as e:
                # If cache is corrupt/incomplete, clean the snapshot and retry.
                cleaned = _cleanup_corrupt_snapshot(e, hf_home=hf_home)
                if cleaned:
                    self.logger.warning("检测到损坏的 HuggingFace 缓存快照，已清理；将重新解析本地缓存并重试。")
                    # Try local again after cleanup.
                    local_path2 = _resolve_local()
                    if local_path2:
                        os.environ.setdefault("HF_HUB_OFFLINE", "1")
                        os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
                        self.model = SentenceTransformer(local_path2, device=self.device, cache_folder=hf_home)
                    elif allow_remote:
                        # Only fall back to remote when local cache is missing or still broken.
                        os.environ.pop("HF_HUB_OFFLINE", None)
                        os.environ.pop("TRANSFORMERS_OFFLINE", None)
                        self.model = SentenceTransformer(repo_id, device=self.device, cache_folder=hf_home)
                    else:
                        raise FileNotFoundError(
                            f"本地缓存缺失/损坏且已禁用远端下载（HF_ALLOW_ONLINE=0）。请检查 {hf_home} 下是否包含 {repo_id} 的完整模型。"
                        )
                else:
                    raise

            self.logger.info(f"模型加载成功，设备: {self.device}")

        except Exception as e:
            raise ContentEmbeddingError(f"模型加载失败: {e}")


    def embed_document(self, document_content: DocumentContent) -> ReportContent:
        """
        为文档生成嵌入
        
        Args:
            document_content: 文档内容
            
        Returns:
            包含嵌入的报告内容
        """
        try:
            self.logger.info(f"开始生成嵌入: {len(document_content.segments)} 个段落")
            
            # 准备文本列表
            texts = [segment.content for segment in document_content.segments]
            
            # 批量生成嵌入
            embeddings = self._generate_embeddings(texts)
            
            # 创建嵌入对象
            segment_embeddings = []
            for i, segment in enumerate(document_content.segments):
                embedding = SegmentEmbedding(
                    segment_id=segment.segment_id,
                    embedding=embeddings[i].tolist()
                )
                segment_embeddings.append(embedding)
            
            # 创建报告内容
            report_content = ReportContent(
                document_id=document_content.document_id,
                document_content=document_content,
                embeddings=segment_embeddings
            )
            
            self.logger.info(f"嵌入生成完成: {len(segment_embeddings)} 个向量")
            return report_content
            
        except Exception as e:
            raise ContentEmbeddingError(f"嵌入生成失败: {e}")
    
    def _generate_embeddings(self, texts: List[str]):
        """
        生成文本嵌入
        
        Args:
            texts: 文本列表
            
        Returns:
            嵌入向量数组
        """
        try:
            # 使用模型生成嵌入
            embeddings = self.model.encode(
                texts,
                batch_size=self.config.batch_size,
                show_progress_bar=True,
                convert_to_numpy=True,
                normalize_embeddings=True
            )
            
            return embeddings
            
        except Exception as e:
            raise ContentEmbeddingError(f"嵌入计算失败: {e}")
    
    def compute_similarity(self, query_text: str, report_content: ReportContent, top_k: int = 10):
        """
        计算查询与段落的相似度
        
        Args:
            query_text: 查询文本
            report_content: 报告内容
            top_k: 返回最相似的前k个段落
            
        Returns:
            相似度结果列表
        """
        try:
            # 生成查询嵌入
            query_embedding = self.model.encode([query_text], normalize_embeddings=True)[0]
            
            # 计算相似度
            similarities = []
            
            for embedding_obj in report_content.embeddings:
                # 计算余弦相似度
                embedding = torch.tensor(embedding_obj.embedding)
                similarity = torch.cosine_similarity(
                    torch.tensor(query_embedding).unsqueeze(0),
                    embedding.unsqueeze(0)
                ).item()
                
                similarities.append((embedding_obj.segment_id, similarity))
            
            # 按相似度排序
            similarities.sort(key=lambda x: x[1], reverse=True)
            
            return similarities[:top_k]
            
        except Exception as e:
            raise ContentEmbeddingError(f"相似度计算失败: {e}") 