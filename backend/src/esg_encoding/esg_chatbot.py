"""
ESG智能聊天机器人后端模块
"""

import json
import uuid
from typing import List, Optional, Dict, Any
from datetime import datetime
import openai
from loguru import logger
from sklearn.metrics.pairwise import cosine_similarity

from .models import (
    ProcessingConfig,
    ChatMessage,
    ChatSession,
    ChatRequest,
    ChatResponse,
    ReportContent,
    ComplianceAssessment,
    DisclosureStatus
)


class ESGChatbot:
    """交互式ESG聊天机器人"""
    
    def __init__(self, config: ProcessingConfig):
        """
        初始化聊天机器人
        
        Args:
            config: 处理配置
        """
        self.config = config
        self.llm_client = self._init_llm_client()
        
        # 原缓存内对话实例
        # 加入ChatID后，session会由上游API自由添加
        self.sessions: Dict[str, ChatSession] = {}
        self.report_content: Optional[ReportContent] = None
        self.compliance_assessment: Optional[ComplianceAssessment] = None
        
    def _init_llm_client(self):
        """初始化LLM客户端"""
        if not self.config.llm_api_key:
            raise ValueError("LLM API key is required for chatbot. Please configure LLM_API_KEY in your .env file.")

        client = openai.OpenAI(
            api_key=self.config.llm_api_key,
            base_url=self.config.llm_base_url if self.config.llm_base_url else "https://dashscope.aliyuncs.com/compatible-mode/v1"
        )
        logger.info("Chatbot LLM client initialized successfully")
        return client
    
    def load_context(
        self, 
        report_content: Optional[ReportContent] = None,
        compliance_assessment: Optional[ComplianceAssessment] = None
    ):
        """
        加载报告和合规评估上下文
        
        Args:
            report_content: 报告内容
            compliance_assessment: 合规评估结果
        """
        if report_content:
            self.report_content = report_content
            logger.info(f"Loaded report context: {report_content.document_id}")
            
        if compliance_assessment:
            self.compliance_assessment = compliance_assessment
            logger.info(f"Loaded compliance assessment for {compliance_assessment.total_metrics_analyzed} metrics")
    
    # NEW FUNCTION
    def restore_session(self, session_id: str, history_data: List[Dict[str, Any]]) -> str:
        """
        根据缓存或本地加载的对话历史重建对话实例
        新的架构会使用本地存储来实现persistence

        Args:
            session_id: 会话ID
            history_data: 对话历史，由上游api加载对应ID的历史
            
        Returns:
            str: 会话ID
        """
        restored_messages = []
        for msg_data in history_data:
            # Handle string timestamp back to datetime object
            timestamp = msg_data.get("timestamp")
            if isinstance(timestamp, str):
                try:
                    timestamp = datetime.fromisoformat(timestamp)
                except ValueError:
                    timestamp = datetime.now()
            else:
                timestamp = datetime.now()

            restored_messages.append(ChatMessage(
                role=msg_data.get("role", "user"),
                content=msg_data.get("content", ""),
                timestamp=timestamp
            ))

        session = ChatSession(
            session_id=session_id,
            report_context=self.report_content.document_id if self.report_content else None,
            compliance_context=self.compliance_assessment.report_id if self.compliance_assessment else None,
            messages=restored_messages
        )
        
        self.sessions[session_id] = session
        logger.info(f"Restored session {session_id} with {len(restored_messages)} messages")
        return session_id

    def create_session(self, session_id: Optional[str] = None) -> str:
        """
        创建新的聊天会话
        
        Args:
            session_id: 会话ID（可选）
            
        Returns:
            str: 会话ID
        """
        if not session_id:
            session_id = str(uuid.uuid4())
        
        session = ChatSession(
            session_id=session_id,
            report_context=self.report_content.document_id if self.report_content else None,
            compliance_context=self.compliance_assessment.report_id if self.compliance_assessment else None,
            messages=[]
        )
        
        self.sessions[session_id] = session
        logger.info(f"Created chat session: {session_id}")
        return session_id
    
    def chat(self, request: ChatRequest) -> ChatResponse:
        """
        处理聊天请求
        假设load_context()和restore_session()已经被上游API trigger
        
        Args:
            request: 聊天请求
            
        Returns:
            ChatResponse: 聊天响应
        """
        # Resolve Session
        session_id = request.session_id
        if not session_id:
            session_id = self.create_session()
        
        if session_id not in self.sessions:
            self.create_session(session_id)
            
        session = self.sessions[session_id]
        
        # 加载对话
        user_message = ChatMessage(
            role="user",
            content=request.message,
            timestamp=datetime.now()
        )
        session.messages.append(user_message)
        
        # Analyze & Retrieve
        question_type = self._analyze_question_type(request.message)
        
        relevant_segments = []
        relevant_content_text = []
        
        if request.include_context and self.report_content:
            relevant_segments = self._search_relevant_content(request.message)
            relevant_content_text = self._get_segments_content(relevant_segments[:5])
        
        # Generate LLM Response
        # We pass the *entire* history (including the msg we just added) to the LLM context builder
        response_text = self._generate_llm_response(
            question=request.message,
            question_type=question_type,
            relevant_content=relevant_content_text,
            conversation_history=session.messages[-10:] # Keep context window manageable
        )
        
        assistant_message = ChatMessage(
            role="assistant",
            content=response_text,
            timestamp=datetime.now()
        )
        session.messages.append(assistant_message)
        session.updated_at = datetime.now()
        
        return ChatResponse(
            session_id=session.session_id,
            response=response_text,
            relevant_segments=relevant_segments[:3]
        )
            
    def _analyze_question_type(self, question: str) -> str:
        """
        分析问题类型
        
        Args:
            question: 用户问题
            
        Returns:
            str: 问题类型
        """
        question_lower = question.lower()
        
        # Define question type keywords
        if any(word in question_lower for word in ["what is", "explain", "definition", "meaning", "define"]):
            return "definition"
        elif any(word in question_lower for word in ["how much", "data", "number", "value", "specific", "score", "percentage"]):
            return "data_query"
        elif any(word in question_lower for word in ["summary", "summarize", "overview", "main", "overall"]):
            return "summary"
        elif any(word in question_lower for word in ["compliance", "disclosure", "disclosed", "compliant", "whether"]):
            return "compliance"
        elif any(word in question_lower for word in ["advice", "how to", "suggest", "recommendation", "improve"]):
            return "advice"
        else:
            return "general"
    
    def _search_relevant_content(self, query: str) -> List[str]:
        """
        搜索与问题相关的内容段落
        
        Args:
            query: 查询问题
            
        Returns:
            List[str]: 相关段落ID列表
        """
        if not self.report_content or not self.report_content.embeddings:
            return []
        
        relevant_segments = []
        query_lower = query.lower()
        
        # 关键词搜索
        for segment in self.report_content.document_content.segments:
            if any(keyword in segment.content.lower() for keyword in query_lower.split()):
                relevant_segments.append(segment.segment_id)
                if len(relevant_segments) >= 10:
                    break
        
        return relevant_segments
    
    def _get_segments_content(self, segment_ids: List[str]) -> List[str]:
        """
        获取段落内容
        
        Args:
            segment_ids: 段落ID列表
            
        Returns:
            List[str]: 段落内容列表
        """
        if not self.report_content:
            return []
        
        contents = []
        for segment_id in segment_ids:
            for segment in self.report_content.document_content.segments:
                if segment.segment_id == segment_id:
                    contents.append(f"[{segment_id} - Page {segment.page_number}]\n{segment.content}")
                    break
        
        return contents
    
    def _generate_llm_response(
        self,
        question: str,
        question_type: str,
        relevant_content: List[str],
        conversation_history: List[ChatMessage]
    ) -> str:
        """
        使用LLM生成回复
        
        Args:
            question: 用户问题
            question_type: 问题类型
            relevant_content: 相关内容
            conversation_history: 对话历史
            
        Returns:
            str: 回复文本
        """
        # 构建提示词
        prompt = self._build_chat_prompt(
            question,
            question_type,
            relevant_content,
            conversation_history
        )
        
        try:
            # 调用LLM
            response = self.llm_client.chat.completions.create(
                model=self.config.llm_model,
                messages=[
                    {"role": "system", "content": "You are a professional ESG consultant assistant helping users understand and analyze ESG report content. Please answer questions using professional, accurate, and friendly language."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.7,
                max_tokens=1000
            )
            
            response_text = response.choices[0].message.content
            
        except Exception as e:
            logger.error(f"LLM generation failed: {e}")
            raise RuntimeError(f"LLM response generation error: {e}")

        return response_text
    
    def _build_chat_prompt(
        self,
        question: str,
        question_type: str,
        relevant_content: List[str],
        conversation_history: List[ChatMessage]
    ) -> str:
        """
        构建聊天提示词
        
        Args:
            question: 用户问题
            question_type: 问题类型
            relevant_content: 相关内容
            conversation_history: 对话历史
            
        Returns:
            str: 提示词
        """
        prompt = f"User question: {question}\n\n"
        
        # 添加报告背景信息
        if self.compliance_assessment:
            fully_disclosed = self.compliance_assessment.disclosure_summary.get("fully_disclosed", 0)
            partially_disclosed = self.compliance_assessment.disclosure_summary.get("partially_disclosed", 0)
            not_disclosed = self.compliance_assessment.disclosure_summary.get("not_disclosed", 0)
            
            prompt += f"""Report Background Information:
- Report ID: {self.compliance_assessment.report_id}
- Total Analyzed Metrics: {self.compliance_assessment.total_metrics_analyzed}
- Overall Compliance Score: {self.compliance_assessment.overall_compliance_score:.1%}
- Fully Disclosed: {fully_disclosed} metrics ({fully_disclosed/self.compliance_assessment.total_metrics_analyzed*100:.1f}%)
- Partially Disclosed: {partially_disclosed} metrics ({partially_disclosed/self.compliance_assessment.total_metrics_analyzed*100:.1f}%)
- Not Disclosed: {not_disclosed} metrics ({not_disclosed/self.compliance_assessment.total_metrics_analyzed*100:.1f}%)

Key Metric Analysis Examples:
"""
            # 添加一些具体的指标分析作为上下文
            if hasattr(self.compliance_assessment, 'metric_analyses') and self.compliance_assessment.metric_analyses:
                for i, analysis in enumerate(self.compliance_assessment.metric_analyses[:3]):  # 展示前3个作为样例
                    status_text = {
                        "fully_disclosed": "Fully Disclosed",
                        "partially_disclosed": "Partially Disclosed", 
                        "not_disclosed": "Not Disclosed"
                    }
                    status = getattr(analysis, 'disclosure_status', 'not_disclosed')
                    if isinstance(status, str):
                        status_display = status_text.get(status, status)
                    else:
                        status_display = str(status)
                        
                    metric_name = getattr(analysis, 'metric_name', 'Unknown')
                    metric_id = getattr(analysis, 'metric_id', 'Unknown')
                    reasoning = getattr(analysis, 'reasoning', '')[:200]  # 限制长度
                    
                    prompt += f"- {metric_name} ({metric_id}): {status_display}\n  Analysis: {reasoning}...\n\n"
            
            prompt += "\n"
        
        # 添加相关内容
        if relevant_content:
            prompt += "Relevant Report Content:\n"
            for i, content in enumerate(relevant_content, 1):
                prompt += f"\nSegment {i}:\n{content}\n"
            prompt += "\n"
        
        # 添加对话历史（最近3轮）
        if len(conversation_history) > 1:
            prompt += "Recent Conversation History:\n"
            for msg in conversation_history[-6:-1]:  # 排除当前消息
                if msg.role == "user":
                    prompt += f"User: {msg.content}\n"
                else:
                    prompt += f"Assistant: {msg.content[:200]}...\n"
            prompt += "\n"
        
        # Add specific guidance based on question type
        if question_type == "definition":
            prompt += "Please provide clear definitions and explanations, including relevant ESG standards."
        elif question_type == "data_query":
            prompt += "Please search for specific data from the relevant content, and clearly indicate the source page if found."
        elif question_type == "summary":
            prompt += "Please provide a concise summary highlighting key information."
        elif question_type == "compliance":
            prompt += "Please answer based on compliance assessment results, explaining disclosure status and relevant evidence."
        elif question_type == "advice":
            prompt += "Please provide professional advice and improvement recommendations."
        else:
            prompt += "Please provide accurate and professional answers."
        
        prompt += "\n\nIf there is specific page information in the content, please point it out in your answer."
        
        return prompt
    
    def get_session_history(self, session_id: str) -> Optional[List[ChatMessage]]:
        """
        获取会话历史
        
        Args:
            session_id: 会话ID
            
        Returns:
            Optional[List[ChatMessage]]: 消息历史
        """
        if session_id in self.sessions:
            return self.sessions[session_id].messages
        return None

    def get_session_history_as_dict(self, session_id: str) -> List[Dict]:
        """Helper to return history in a JSON-serializable format for the API"""
        if session_id in self.sessions:
            return [
                {
                    "role": msg.role, 
                    "content": msg.content, 
                    "timestamp": msg.timestamp.isoformat()
                } 
                for msg in self.sessions[session_id].messages
            ]
        return []
    
    def clear_session(self, session_id: str) -> bool:
        """
        清除会话
        
        Args:
            session_id: 会话ID
            
        Returns:
            bool: 是否成功
        """
        if session_id in self.sessions:
            del self.sessions[session_id]
            logger.info(f"Cleared session: {session_id}")
            return True
        return False
    
    def export_session(self, session_id: str) -> Optional[Dict]:
        """
        导出会话数据
        
        Args:
            session_id: 会话ID
            
        Returns:
            Optional[Dict]: 会话数据
        """
        if session_id not in self.sessions:
            return None
        
        session = self.sessions[session_id]
        return {
            "session_id": session.session_id,
            "created_at": session.created_at.isoformat(),
            "updated_at": session.updated_at.isoformat(),
            "messages": [
                {
                    "role": msg.role,
                    "content": msg.content,
                    "timestamp": msg.timestamp.isoformat()
                }
                for msg in session.messages
            ],
            "report_context": session.report_context,
            "compliance_context": session.compliance_context
        }
