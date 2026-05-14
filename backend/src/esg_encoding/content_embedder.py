"""
简化的内容嵌入器

直接使用预训练的BGE-M3模型生成文本嵌入。
"""

from typing import List
import os
import torch
from loguru import logger

from .shared_embedding_model import get_shared_embedding_model
from .embedding_settings import get_configured_embedding_local_path, get_configured_embedding_model_name

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
        
        # 设置设备：尊重 Docker/config 的 cuda 配置，不因 torch.cuda 检查而静默切到 CPU。
        requested_device = os.getenv("LOCAL_EMBEDDINGS_DEVICE") or str(getattr(self.config, "device", "cuda") or "cuda")
        self.device = torch.device(requested_device)
        
        # 加载模型
        self.model = None
        self._load_model()

    def _load_model(self):
        """加载嵌入模型（优先本地 HF cache，缺失/损坏才允许远端下载）"""
        try:
            repo_id = str(getattr(self.config, "embedding_model", "") or get_configured_embedding_model_name())
            hf_home = os.getenv("HF_HOME", "/root/.cache/huggingface")
            explicit_path = get_configured_embedding_local_path()

            self.model = get_shared_embedding_model(
                repo_id,
                device=str(self.device),
                hf_home=hf_home,
                explicit_local_path=explicit_path,
                trust_remote_code=True,
            )
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