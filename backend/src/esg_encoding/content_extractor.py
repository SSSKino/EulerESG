"""
简化的PDF内容提取器

逐页提取PDF文本，自动生成段落标签，输出markdown格式。
"""

import hashlib
from collections import Counter
import re
from typing import Dict, List, Set, Tuple
from pathlib import Path

import fitz  # PyMuPDF
from loguru import logger

from .models import TextSegment, DocumentContent, ProcessingConfig
from .exceptions import ContentExtractionError


class ContentExtractor:
    """
    简化的PDF内容提取器
    
    功能：
    1. 逐页提取PDF文本
    2. 自动生成段落标签
    3. 输出markdown格式
    """
    
    def __init__(self, config: ProcessingConfig = None):
        """初始化提取器"""
        self.config = config or ProcessingConfig()
        self.logger = logger.bind(component="ContentExtractor")
    
    def extract_pdf(self, file_path: str) -> DocumentContent:
        """
        提取PDF内容
        
        Args:
            file_path: PDF文件路径
            
        Returns:
            文档内容对象
        """
        file_path = Path(file_path)
        self.logger.info(f"开始提取PDF: {file_path}")
        
        try:
            # 打开PDF文档
            doc = fitz.open(str(file_path))
            
            # 预扫描重复页眉/页脚文本，避免噪音进入检索
            repeated_margin_texts = self._detect_repeated_margin_texts(doc)

            # 提取所有段落
            all_segments = []
            markdown_lines = []
            
            # 逐页提取
            for page_num in range(len(doc)):
                page = doc[page_num]
                page_segments = self._extract_page_text(
                    page,
                    page_num + 1,
                    repeated_margin_texts=repeated_margin_texts,
                )
                
                all_segments.extend(page_segments)
                
                # 添加到markdown
                if page_segments:
                    markdown_lines.append(f"\n## 第 {page_num + 1} 页\n")
                    for segment in page_segments:
                        markdown_lines.append(f"**{segment.segment_id}**\n\n{segment.content}\n\n---\n")
            
            doc.close()
            
            # 生成文档ID
            document_id = self._generate_document_id(file_path)
            
            # 合并markdown内容
            markdown_content = "".join(markdown_lines)
            
            # 创建文档内容对象
            document_content = DocumentContent(
                document_id=document_id,
                file_path=str(file_path),
                segments=all_segments,
                markdown_content=markdown_content
            )
            
            self.logger.info(f"提取完成: {len(all_segments)} 个段落")
            return document_content
            
        except Exception as e:
            raise ContentExtractionError(f"PDF提取失败: {e}", str(file_path))
    
    def _extract_page_text(self, page: fitz.Page, page_number: int, 
                          repeated_margin_texts: Set[str] = None) -> List[TextSegment]:
        """
        提取单页文本（增强版：布局感知 + 表格/正文统一排序）
        
        Args:
            page: PDF页面对象
            page_number: 页码
            repeated_margin_texts: 需要过滤的重复页眉/页脚文本
            
        Returns:
            文本段落列表
        """
        segments: List[TextSegment] = []
        repeated_margin_texts = repeated_margin_texts or set()

        try:
            raw_text_blocks = self._extract_raw_text_blocks(page, repeated_margin_texts)
            median_font_size = self._estimate_median_font_size(raw_text_blocks)

            # 布局感知排序：检测双栏并生成阅读顺序
            sorted_text_blocks = self._sort_text_blocks_by_layout(page, raw_text_blocks)

            # 表格提取，并尽量绑定表格标题/图注
            table_items = self._extract_page_tables(page, page_number, sorted_text_blocks)

            # 轻量标题层级识别：将标题并到下一个正文段落，减少标题/正文割裂
            normalized_text_blocks = self._merge_heading_blocks(sorted_text_blocks, median_font_size)

            items = []
            table_counter = 0
            text_counter = 0

            for table_item in table_items:
                segment_id = f"P{page_number:03d}_T{table_counter:03d}"
                table_counter += 1
                items.append({
                    'segment_id': segment_id,
                    'content': table_item['content'],
                    'position_y': table_item['position_y'],
                    'position_x': table_item['position_x'],
                })

            for block in normalized_text_blocks:
                segment_id = f"P{page_number:03d}_S{text_counter:03d}"
                text_counter += 1
                items.append({
                    'segment_id': segment_id,
                    'content': block['text'],
                    'position_y': block['position_y'],
                    'position_x': block['position_x'],
                })

            # 统一排序，保证正文/表格相邻关系在 segment 顺序中可见
            items.sort(key=lambda x: (x['position_y'], x['position_x']))

            for item in items:
                segments.append(
                    TextSegment(
                        segment_id=item['segment_id'],
                        content=item['content'],
                        page_number=page_number,
                        position_y=item['position_y'],
                    )
                )

        except Exception as e:
            self.logger.warning(f"页面 {page_number} 提取失败: {e}")

        return segments

    def _extract_page_tables(self, page: fitz.Page, page_number: int, 
                             text_blocks: List[dict] = None) -> List[dict]:
        """
        提取页面表格，并尽量绑定表格标题/图注
        
        Args:
            page: PDF页面对象
            page_number: 页码
            text_blocks: 页面文本块（用于查找表格标题）
            
        Returns:
            表格项目列表
        """
        segments: List[dict] = []
        text_blocks = text_blocks or []

        try:
            tables = page.find_tables()
            
            for table in tables:
                table_data = table.extract()
                if not table_data or len(table_data) == 0:
                    continue

                table_markdown = self._convert_table_to_markdown(table_data)
                if len(table_markdown) < self.config.min_text_length:
                    continue

                table_bbox = table.bbox
                position_y = table_bbox[1]
                position_x = table_bbox[0]

                table_title = self._find_table_caption(text_blocks, table_bbox)
                content = f"**[表格]**\n\n{table_markdown}"
                if table_title:
                    content = f"**[表格标题]** {table_title}\n\n{content}"

                segments.append({
                    'content': content,
                    'position_y': position_y,
                    'position_x': position_x,
                })
                        
        except Exception as e:
            self.logger.warning(f"页面 {page_number} 表格提取失败: {e}")
        
        return segments

    def _extract_raw_text_blocks(self, page: fitz.Page, repeated_margin_texts: Set[str]) -> List[dict]:
        """提取页面原始文本块并附带布局/字体信息"""
        text_dict = page.get_text("dict")
        page_width = float(page.rect.width or 1.0)
        blocks: List[dict] = []

        for block_idx, block in enumerate(text_dict.get("blocks", [])):
            if "lines" not in block:
                continue

            spans_text = []
            font_sizes = []
            for line in block.get("lines", []):
                line_text_parts = []
                for span in line.get("spans", []):
                    txt = (span.get("text") or "").strip()
                    if txt:
                        line_text_parts.append(txt)
                        try:
                            font_sizes.append(float(span.get("size") or 0))
                        except Exception:
                            pass
                if line_text_parts:
                    spans_text.append(" ".join(line_text_parts))

            block_text = " ".join(spans_text)
            clean_text = self._clean_text(block_text)
            if len(clean_text) < self.config.min_text_length:
                continue
            if clean_text in repeated_margin_texts:
                continue

            bbox = block.get("bbox", (0, 0, 0, 0))
            x0, y0, x1, y1 = bbox
            blocks.append({
                'text': clean_text,
                'position_y': y0,
                'position_x': x0,
                'bbox': bbox,
                'width_ratio': max(0.0, min(1.0, (x1 - x0) / page_width)),
                'max_font_size': max(font_sizes) if font_sizes else 0.0,
                'avg_font_size': (sum(font_sizes) / len(font_sizes)) if font_sizes else 0.0,
                'original_index': block_idx,
            })

        return blocks

    def _estimate_median_font_size(self, text_blocks: List[dict]) -> float:
        font_sizes = sorted([b.get('avg_font_size', 0.0) for b in text_blocks if b.get('avg_font_size', 0.0) > 0])
        if not font_sizes:
            return 0.0
        mid = len(font_sizes) // 2
        if len(font_sizes) % 2:
            return font_sizes[mid]
        return (font_sizes[mid - 1] + font_sizes[mid]) / 2

    def _sort_text_blocks_by_layout(self, page: fitz.Page, text_blocks: List[dict]) -> List[dict]:
        """布局感知排序：简单检测双栏，否则按 y/x 排序"""
        if not text_blocks:
            return []

        page_width = float(page.rect.width or 1.0)
        left_blocks, right_blocks, full_blocks = [], [], []

        for block in text_blocks:
            x0, y0, x1, y1 = block['bbox']
            spans_middle = x0 < page_width * 0.45 and x1 > page_width * 0.55
            if block.get('width_ratio', 1.0) >= 0.70 or spans_middle:
                full_blocks.append(block)
            elif x0 < page_width * 0.5:
                left_blocks.append(block)
            else:
                right_blocks.append(block)

        two_column = len(left_blocks) >= 2 and len(right_blocks) >= 2
        if not two_column:
            return sorted(text_blocks, key=lambda x: (x['position_y'], x['position_x']))

        left_sorted = sorted(left_blocks, key=lambda x: (x['position_y'], x['position_x']))
        right_sorted = sorted(right_blocks, key=lambda x: (x['position_y'], x['position_x']))
        full_sorted = sorted(full_blocks, key=lambda x: (x['position_y'], x['position_x']))

        column_y_values = [b['position_y'] for b in left_blocks + right_blocks]
        col_min_y = min(column_y_values) if column_y_values else 0.0
        col_max_y = max(column_y_values) if column_y_values else 0.0

        before_cols = [b for b in full_sorted if b['position_y'] < col_min_y - 10]
        within_cols = [b for b in full_sorted if col_min_y - 10 <= b['position_y'] <= col_max_y + 10]
        after_cols = [b for b in full_sorted if b['position_y'] > col_max_y + 10]

        # 阅读顺序：列区上方全文块 -> 左栏 -> 右栏 -> 列区内部剩余全文块 -> 列区下方全文块
        return before_cols + left_sorted + right_sorted + within_cols + after_cols

    def _is_heading_block(self, block: dict, median_font_size: float) -> bool:
        text = (block.get('text') or '').strip()
        if not text:
            return False
        if len(text) > 160:
            return False
        if text.endswith('.') and len(text) > 80:
            return False
        max_font = block.get('max_font_size', 0.0)
        if median_font_size > 0 and max_font >= median_font_size + 1.0:
            return True
        if re.match(r'^(table|figure|chart|appendix|section)\b', text, re.IGNORECASE):
            return True
        return False

    def _merge_heading_blocks(self, text_blocks: List[dict], median_font_size: float) -> List[dict]:
        """将短标题合并到紧随其后的正文块中，降低标题/正文割裂"""
        if not text_blocks:
            return []

        merged: List[dict] = []
        i = 0
        while i < len(text_blocks):
            current = dict(text_blocks[i])
            if self._is_heading_block(current, median_font_size) and i + 1 < len(text_blocks):
                nxt = dict(text_blocks[i + 1])
                same_page_band = abs(nxt['position_y'] - current['position_y']) <= 120
                if same_page_band:
                    nxt['text'] = f"{current['text']}\n\n{nxt['text']}"
                    nxt['position_y'] = min(current['position_y'], nxt['position_y'])
                    merged.append(nxt)
                    i += 2
                    continue
            merged.append(current)
            i += 1

        return merged

    def _find_table_caption(self, text_blocks: List[dict], table_bbox) -> str:
        if not text_blocks:
            return ""
        x0, y0, x1, y1 = table_bbox
        candidates = []
        for block in text_blocks:
            bx0, by0, bx1, by1 = block['bbox']
            text = (block.get('text') or '').strip()
            if not text or len(text) > 220:
                continue

            horizontal_overlap = min(x1, bx1) - max(x0, bx0)
            overlap_ratio = horizontal_overlap / max(1.0, min(x1 - x0, bx1 - bx0)) if horizontal_overlap > 0 else 0.0

            # 优先找表格上方短标题，其次找下方图注/说明
            if by1 <= y0 and (y0 - by1) <= 70 and overlap_ratio >= 0.2:
                candidates.append((y0 - by1, text))
            elif by0 >= y1 and (by0 - y1) <= 35 and overlap_ratio >= 0.2 and re.match(r'^(source|note|notes|figure|table)\b', text, re.IGNORECASE):
                candidates.append((by0 - y1 + 1000, text))

        if not candidates:
            return ""
        candidates.sort(key=lambda x: x[0])
        return candidates[0][1]

    def _detect_repeated_margin_texts(self, doc: fitz.Document) -> Set[str]:
        """检测跨页重复出现的页眉/页脚文本，用于去噪"""
        repeated_candidates: List[str] = []
        for page_num in range(len(doc)):
            page = doc[page_num]
            page_height = float(page.rect.height or 1.0)
            top_limit = page_height * 0.10
            bottom_limit = page_height * 0.90
            for block in self._extract_raw_text_blocks(page, set()):
                y0 = block['bbox'][1]
                y1 = block['bbox'][3]
                text = block['text']
                if len(text) > 180:
                    continue
                if y0 <= top_limit or y1 >= bottom_limit:
                    repeated_candidates.append(text)

        counts = Counter(repeated_candidates)
        threshold = 2 if len(doc) < 6 else 3
        return {text for text, count in counts.items() if count >= threshold}

    def _convert_table_to_markdown(self, table_data: list) -> str:
        """
        将表格数据转换为Markdown格式
        
        Args:
            table_data: 表格数据（二维列表）
            
        Returns:
            Markdown格式的表格
        """
        if not table_data or len(table_data) == 0:
            return ""
        
        # 清理表格数据
        cleaned_data = []
        for row in table_data:
            cleaned_row = [self._clean_text(cell) if cell else "" for cell in row]
            cleaned_data.append(cleaned_row)
        
        # 生成Markdown表格
        markdown_lines = []
        
        # 表头
        if cleaned_data:
            header = "| " + " | ".join(cleaned_data[0]) + " |"
            markdown_lines.append(header)
            
            # 分隔线
            separator = "| " + " | ".join(["-" * max(3, len(cell)) for cell in cleaned_data[0]]) + " |"
            markdown_lines.append(separator)
            
            # 表格内容
            for row in cleaned_data[1:]:
                content_row = "| " + " | ".join(row) + " |"
                markdown_lines.append(content_row)
        
        return "\n".join(markdown_lines)
    
    def _clean_text(self, text: str) -> str:
        """
        清理文本
        
        Args:
            text: 原始文本
            
        Returns:
            清理后的文本
        """
        if not text:
            return ""
        
        # 移除多余空格和换行
        text = " ".join(text.split())
        
        # 移除控制字符
        text = ''.join(char for char in text if ord(char) >= 32)
        
        return text.strip()
    
    def _generate_document_id(self, file_path: Path) -> str:
        """
        生成文档ID
        
        Args:
            file_path: 文件路径
            
        Returns:
            文档ID
        """
        # 使用文件名和路径生成哈希
        content = f"{file_path.name}_{file_path.stat().st_size}"
        hash_value = hashlib.md5(content.encode()).hexdigest()[:8]
        return f"doc_{file_path.stem}_{hash_value}"
    
    def save_markdown(self, document_content: DocumentContent, output_path: str = None) -> str:
        """
        保存markdown文件
        
        Args:
            document_content: 文档内容
            output_path: 输出路径，如果不指定则自动生成
            
        Returns:
            保存的文件路径
        """
        if output_path is None:
            # 自动生成输出路径
            original_path = Path(document_content.file_path)
            output_path = original_path.parent / f"{original_path.stem}_extracted.md"
        
        output_path = Path(output_path)
        
        try:
            # 生成完整的markdown内容
            markdown_content = f"""# {Path(document_content.file_path).name}

**文档ID**: {document_content.document_id}  
**提取时间**: {document_content.created_at.strftime('%Y-%m-%d %H:%M:%S')}  
**总段落数**: {len(document_content.segments)}

---

{document_content.markdown_content}

---

## 段落索引

| 段落ID | 页码 | 内容预览 |
|--------|------|----------|
"""
            
            # 添加段落索引
            for segment in document_content.segments:
                preview = segment.content[:50] + "..." if len(segment.content) > 50 else segment.content
                markdown_content += f"| {segment.segment_id} | {segment.page_number} | {preview} |\n"
            
            # 写入文件
            with open(output_path, 'w', encoding='utf-8') as f:
                f.write(markdown_content)
            
            self.logger.info(f"Markdown文件已保存: {output_path}")
            return str(output_path)
            
        except Exception as e:
            raise ContentExtractionError(f"保存Markdown失败: {e}", str(output_path)) 