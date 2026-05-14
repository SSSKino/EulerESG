"""
简化的PDF内容提取器

逐页提取PDF文本，自动生成段落标签，输出markdown格式。
"""

import hashlib
import io
import re
from typing import Any, Dict, List, Optional, Tuple
from pathlib import Path

import fitz  # PyMuPDF
from loguru import logger

try:
    from PIL import Image
except Exception:  # pragma: no cover - optional dependency
    Image = None

try:
    import pytesseract
    from pytesseract import Output as TesseractOutput
except Exception:  # pragma: no cover - optional dependency
    pytesseract = None
    TesseractOutput = None

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

            # 提取所有段落
            all_segments = []

            # 逐页提取
            for page_num in range(len(doc)):
                page = doc[page_num]
                page_segments = self._extract_page_text(page, page_num + 1)
                all_segments.extend(page_segments)

            doc.close()

            # 跨页续表合并：统一 logical table id，并尽量继承前页表头
            all_segments = self._merge_cross_page_table_continuations(all_segments)
            all_segments = self._enrich_structured_table_segments(all_segments)

            # 生成文档ID
            document_id = self._generate_document_id(file_path)

            # 合并markdown内容（基于合并后的最终 segment）
            markdown_lines: List[str] = []
            current_page = None
            for segment in all_segments:
                page_no = int(getattr(segment, "page_number", 0) or 0)
                if page_no != current_page:
                    current_page = page_no
                    markdown_lines.append(f"\n## 第 {page_no} 页\n")
                markdown_lines.append(f"**{segment.segment_id}**\n\n{segment.content}\n\n---\n")
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

    def _extract_page_text(self, page: fitz.Page, page_number: int) -> List[TextSegment]:
        """
        提取单页文本

        Args:
            page: PDF页面对象
            page_number: 页码

        Returns:
            文本段落列表
        """
        segments: List[TextSegment] = []

        try:
            text_dict = page.get_text("dict")
            raw_text_blocks = self._collect_text_blocks(text_dict, page)

            # 提取表格（优先处理），并获取表格区域用于正文去重
            table_segments, table_regions = self._extract_page_tables(page, page_number, raw_text_blocks)
            segments.extend(table_segments)

            # OCR 回退：低文本页 / 图片型页面 / find_tables() 失败时补入 OCR 文本块
            if self._should_apply_ocr(page, raw_text_blocks, table_regions):
                ocr_blocks = self._extract_page_ocr_blocks(page, page_number)
                if ocr_blocks:
                    ocr_table_segments, ocr_table_regions = self._extract_ocr_table_segments(
                        page=page,
                        page_number=page_number,
                        ocr_blocks=ocr_blocks,
                        text_blocks=raw_text_blocks,
                        existing_table_count=sum(1 for seg in table_segments if getattr(seg, "segment_type", "") == "table"),
                    )
                    if ocr_table_segments:
                        segments.extend(ocr_table_segments)
                        table_regions.extend(ocr_table_regions)
                    raw_text_blocks = self._deduplicate_blocks(raw_text_blocks + ocr_blocks)

            # 过滤掉表格覆盖区域中的文本块，同时做页眉页脚降噪
            filtered_blocks = []
            for block in raw_text_blocks:
                min_text_len = self._get_int_config("ocr_min_text_len", self.config.min_text_length) if block.get("segment_type") == "ocr_text" else self.config.min_text_length
                if len(block["text"]) < min_text_len:
                    continue
                if self._overlaps_any_table(block["bbox"], table_regions):
                    continue
                if self._is_header_footer_noise(block, page):
                    continue
                filtered_blocks.append(block)

            # 布局感知排序 + 碎片块修复
            ordered_blocks = self._sort_blocks_by_layout(filtered_blocks, page.rect.width)
            ordered_blocks = self._merge_adjacent_ordered_blocks(ordered_blocks, page.rect.width)

            # 生成正文段落
            text_segments: List[TextSegment] = []
            for segment_idx, block in enumerate(ordered_blocks):
                segment_id = f"P{page_number:03d}_S{segment_idx:03d}"
                segment = TextSegment(
                    segment_id=segment_id,
                    content=block["text"],
                    page_number=page_number,
                    position_y=block["position_y"],
                    position_x=block.get("position_x"),
                    segment_type=str(block.get("segment_type") or "body_text")
                )
                text_segments.append(segment)
                segments.append(segment)

            for cluster_idx, cluster in enumerate(self._build_paragraph_clusters(ordered_blocks, page_number)):
                cluster_segment = TextSegment(
                    segment_id=f"P{page_number:03d}_PC{cluster_idx:03d}",
                    content=cluster["text"],
                    page_number=page_number,
                    position_y=cluster["position_y"],
                    position_x=cluster.get("position_x"),
                    segment_type="paragraph_cluster",
                    structured_data={
                        "member_block_indexes": cluster.get("member_indexes", []),
                        "member_types": cluster.get("member_types", []),
                    },
                )
                segments.append(cluster_segment)

        except Exception as e:
            self.logger.warning(f"页面 {page_number} 提取失败: {e}")

        return segments

    def _collect_text_blocks(self, text_dict: Dict, page: fitz.Page) -> List[Dict]:
        """收集文本块及其布局特征。"""
        blocks: List[Dict] = []
        page_width = float(page.rect.width)

        for block_idx, block in enumerate(text_dict.get("blocks", [])):
            lines = block.get("lines") or []
            if not lines:
                continue

            block_text = self._build_block_text(block)
            clean_text = self._clean_multiline_text(block_text)
            if not clean_text:
                continue

            bbox = tuple(block.get("bbox") or (0, 0, 0, 0))
            font_sizes = []
            for line in lines:
                for span in line.get("spans", []):
                    size = span.get("size")
                    if isinstance(size, (int, float)):
                        font_sizes.append(float(size))

            max_font_size = max(font_sizes) if font_sizes else 0.0
            avg_font_size = sum(font_sizes) / len(font_sizes) if font_sizes else 0.0
            width = max(0.0, float(bbox[2] - bbox[0]))
            height = max(0.0, float(bbox[3] - bbox[1]))
            center_x = float(bbox[0] + bbox[2]) / 2.0

            blocks.append({
                "text": clean_text,
                "position_y": float(bbox[1]),
                "position_x": float(bbox[0]),
                "bbox": bbox,
                "width": width,
                "height": height,
                "center_x": center_x,
                "max_font_size": max_font_size,
                "avg_font_size": avg_font_size,
                "is_probable_title": self._is_probable_title(clean_text, max_font_size, avg_font_size, width, page_width),
                "is_figure_caption": self._is_figure_caption(clean_text),
                "original_index": block_idx,
                "segment_type": "heading" if self._is_probable_title(clean_text, max_font_size, avg_font_size, width, page_width) else "body_text",
            })

        return blocks

    def _build_block_text(self, block: Dict) -> str:
        """构建更贴近版面的 block 文本，保留必要行信息。"""
        line_texts: List[str] = []
        for line in block.get("lines", []):
            line_text = self._build_line_text(line)
            if line_text:
                line_texts.append(line_text)
        return "\n".join(line_texts)

    def _build_line_text(self, line: Dict) -> str:
        """基于 span 间距拼接单行文本，尽量避免单词/数字/单位粘连。"""
        spans = line.get("spans", []) or []
        if not spans:
            return ""

        parts: List[str] = []
        prev_end_x: Optional[float] = None

        for span in spans:
            text = str(span.get("text") or "")
            if not text:
                continue

            bbox = span.get("bbox") or (None, None, None, None)
            start_x = bbox[0] if len(bbox) >= 1 else None
            end_x = bbox[2] if len(bbox) >= 3 else None

            if parts:
                prev_text = parts[-1]
                gap = (float(start_x) - float(prev_end_x)) if start_x is not None and prev_end_x is not None else 0.0
                if self._needs_space_between(prev_text, text, gap):
                    parts.append(" ")

            parts.append(text)
            prev_end_x = float(end_x) if end_x is not None else prev_end_x

        return "".join(parts)

    def _needs_space_between(self, prev_text: str, next_text: str, gap: float) -> bool:
        """判断两个 span 之间是否需要补空格。"""
        if not prev_text or not next_text:
            return False

        prev_char = prev_text[-1]
        next_char = next_text[0]

        if prev_char.isspace() or next_char.isspace():
            return False
        if prev_char in "([{/\\" or next_char in ")]},.;:%/\\":
            return False
        if gap >= 1.2:
            return True
        if prev_char.isalnum() and next_char.isalnum():
            return True
        return False

    def _sort_blocks_by_layout(self, text_blocks: List[Dict], page_width: float) -> List[Dict]:
        """布局感知排序：常规页面按 y/x，明显双栏页面按顶部全宽 -> 左栏 -> 右栏。"""
        if not text_blocks:
            return []

        default_sorted = sorted(text_blocks, key=lambda x: (x["position_y"], x["position_x"]))
        if not self._detect_two_column_layout(text_blocks, page_width):
            return default_sorted

        narrow_blocks = [b for b in text_blocks if b["width"] <= page_width * 0.70]
        if len(narrow_blocks) < 6:
            return default_sorted

        split_x = page_width / 2.0
        left_blocks = [b for b in narrow_blocks if b["center_x"] < split_x]
        right_blocks = [b for b in narrow_blocks if b["center_x"] >= split_x]
        if len(left_blocks) < 3 or len(right_blocks) < 3:
            return default_sorted

        first_column_y = min(min(b["position_y"] for b in left_blocks), min(b["position_y"] for b in right_blocks))
        top_full_width = [
            b for b in text_blocks
            if b["width"] > page_width * 0.70 and b["position_y"] <= first_column_y + 40
        ]
        top_ids = {id(b) for b in top_full_width}
        remaining = [b for b in text_blocks if id(b) not in top_ids]

        remaining_narrow = [b for b in remaining if b["width"] <= page_width * 0.70]
        remaining_wide = [b for b in remaining if b["width"] > page_width * 0.70]

        left_sorted = sorted([b for b in remaining_narrow if b["center_x"] < split_x], key=lambda x: (x["position_y"], x["position_x"]))
        right_sorted = sorted([b for b in remaining_narrow if b["center_x"] >= split_x], key=lambda x: (x["position_y"], x["position_x"]))
        wide_sorted = sorted(remaining_wide, key=lambda x: (x["position_y"], x["position_x"]))
        top_sorted = sorted(top_full_width, key=lambda x: (x["position_y"], x["position_x"]))

        return top_sorted + left_sorted + wide_sorted + right_sorted

    def _merge_adjacent_ordered_blocks(self, ordered_blocks: List[Dict], page_width: float) -> List[Dict]:
        """合并同列中明显被截断的短文本块，减少碎片化 evidence。"""
        if not ordered_blocks:
            return []

        merged: List[Dict] = []
        for block in ordered_blocks:
            current = dict(block)
            if not merged:
                merged.append(current)
                continue

            prev = merged[-1]
            if not self._should_merge_adjacent_blocks(prev, current, page_width):
                merged.append(current)
                continue

            joiner = self._block_merge_joiner(prev, current)
            prev["text"] = self._clean_multiline_text(f"{prev.get('text') or ''}{joiner}{current.get('text') or ''}")
            prev_bbox = prev.get("bbox") or (0.0, 0.0, 0.0, 0.0)
            cur_bbox = current.get("bbox") or (0.0, 0.0, 0.0, 0.0)
            new_bbox = (
                min(float(prev_bbox[0]), float(cur_bbox[0])),
                min(float(prev_bbox[1]), float(cur_bbox[1])),
                max(float(prev_bbox[2]), float(cur_bbox[2])),
                max(float(prev_bbox[3]), float(cur_bbox[3])),
            )
            prev["bbox"] = new_bbox
            prev["position_y"] = float(new_bbox[1])
            prev["position_x"] = float(new_bbox[0])
            prev["width"] = max(0.0, float(new_bbox[2] - new_bbox[0]))
            prev["height"] = max(0.0, float(new_bbox[3] - new_bbox[1]))
            prev["center_x"] = float(new_bbox[0] + new_bbox[2]) / 2.0
            prev["is_probable_title"] = bool(prev.get("is_probable_title") or current.get("is_probable_title"))
            prev["is_figure_caption"] = bool(prev.get("is_figure_caption") or current.get("is_figure_caption"))

        return merged

    def _should_merge_adjacent_blocks(self, prev: Dict, current: Dict, page_width: float) -> bool:
        prev_type = str(prev.get("segment_type") or "text")
        current_type = str(current.get("segment_type") or "text")
        narrative_types = {"text", "body_text", "heading", "ocr_text"}
        if prev_type not in narrative_types or current_type not in narrative_types:
            return False
        if prev_type == "ocr_text" or current_type == "ocr_text":
            if prev_type != current_type:
                return False
        elif prev_type == "heading":
            if current_type not in {"heading", "body_text", "text"}:
                return False
        elif current_type == "heading":
            return False

        prev_x = float(prev.get("position_x") or 0.0)
        curr_x = float(current.get("position_x") or 0.0)
        x_gap = abs(curr_x - prev_x)
        if x_gap > max(84.0, page_width * 0.16):
            return False

        prev_bbox = prev.get("bbox") or (0.0, 0.0, 0.0, 0.0)
        curr_bbox = current.get("bbox") or (0.0, 0.0, 0.0, 0.0)
        y_gap = float(curr_bbox[1]) - float(prev_bbox[3])
        prev_height = float(prev.get("height") or 0.0)
        if y_gap < -10.0 or y_gap > max(58.0, prev_height * 3.2):
            return False

        prev_text = self._clean_multiline_text(prev.get("text") or "")
        curr_text = self._clean_multiline_text(current.get("text") or "")
        if not prev_text or not curr_text:
            return False
        if len(prev_text) > 520 and len(curr_text) > 520 and prev_type != "heading":
            return False

        short_fragment = len(prev_text) <= 96 or len(curr_text) <= 96
        broken_word = bool(re.search(r"[A-Za-z]{1,10}$", prev_text)) and bool(re.match(r"^[A-Za-z]{1,16}", curr_text))
        lowercase_continuation = curr_text[:1].islower() or prev_text[-1:].islower()
        numeric_continuation = bool(re.match(r"^[\d(%$]", curr_text)) and len(prev_text) <= 180
        heading_continuation = prev_type == "heading" or bool(re.search(r"[:\-–/,(]$", prev_text))
        clause_continuation = not bool(re.search(r"[.!?]$", prev_text)) and len(prev_text) <= 220
        column_continuation = x_gap <= max(28.0, page_width * 0.05)
        return short_fragment or broken_word or lowercase_continuation or numeric_continuation or heading_continuation or clause_continuation or column_continuation

    def _block_merge_joiner(self, prev: Dict, current: Dict) -> str:
        prev_text = self._clean_multiline_text(prev.get("text") or "")
        curr_text = self._clean_multiline_text(current.get("text") or "")
        if not prev_text or not curr_text:
            return " "
        if prev_text.endswith((":", "-", "–", "/", "(")):
            return " "
        if curr_text[:1].islower() or bool(re.match(r"^[\d(]", curr_text)):
            return " "
        return "\n"

    def _build_paragraph_clusters(self, ordered_blocks: List[Dict], page_number: int) -> List[Dict]:
        """更宽松地聚合连续 narrative 块，优先形成 heading + body / body + body 的完整语义段。"""
        clusters: List[Dict] = []
        if not ordered_blocks:
            return clusters

        allowed_types = {"heading", "body_text", "text", "ocr_text"}
        idx = 0
        while idx < len(ordered_blocks):
            block = ordered_blocks[idx]
            block_type = str(block.get("segment_type") or "body_text")
            if block_type not in allowed_types:
                idx += 1
                continue

            chain = [block]
            next_idx = idx + 1
            while next_idx < len(ordered_blocks) and len(chain) < 5:
                nxt = ordered_blocks[next_idx]
                nxt_type = str(nxt.get("segment_type") or "body_text")
                if nxt_type not in allowed_types:
                    break
                same_column = abs(float(nxt.get("position_x") or 0.0) - float(chain[-1].get("position_x") or 0.0)) <= 90
                y_gap = float(nxt.get("position_y") or 0.0) - float(chain[-1].get("position_y") or 0.0)
                if not same_column or y_gap < -12 or y_gap > 110:
                    break
                prev_text = self._clean_multiline_text(chain[-1].get("text") or "")
                next_text = self._clean_multiline_text(nxt.get("text") or "")
                if not prev_text or not next_text:
                    break
                if len(prev_text) > 900 and len(next_text) > 900:
                    break
                chain.append(nxt)
                next_idx += 1

            heading_count = sum(1 for item in chain if str(item.get("segment_type") or "") == "heading")
            body_count = sum(1 for item in chain if str(item.get("segment_type") or "") in {"body_text", "text", "ocr_text"})
            total_chars = sum(len(self._clean_multiline_text(item.get("text") or "")) for item in chain)
            if len(chain) >= 2 and total_chars >= 60 and (body_count >= 2 or (heading_count >= 1 and body_count >= 1)):
                formatted_parts: List[str] = []
                member_indexes: List[int] = []
                for pos, item in enumerate(chain, start=idx):
                    part_text = self._clean_multiline_text(item.get("text") or "")
                    if not part_text:
                        continue
                    label = "[heading]" if str(item.get("segment_type") or "") == "heading" else "[body]"
                    formatted_parts.append(f"{label}\n{part_text}")
                    member_indexes.append(pos)
                merged_text = "\n\n".join(formatted_parts).strip()
                if merged_text:
                    clusters.append({
                        "text": merged_text,
                        "position_y": float(chain[0].get("position_y") or 0.0),
                        "position_x": float(chain[0].get("position_x") or 0.0),
                        "member_indexes": member_indexes,
                        "member_types": [str(item.get("segment_type") or "body_text") for item in chain],
                    })
            idx += 1
        return clusters

    def _detect_two_column_layout(self, text_blocks: List[Dict], page_width: float) -> bool:
        """简单检测双栏布局。"""
        if len(text_blocks) < 8:
            return False

        candidate_blocks = [
            b for b in text_blocks
            if page_width * 0.18 <= b["width"] <= page_width * 0.65 and b["height"] >= 8
        ]
        if len(candidate_blocks) < 6:
            return False

        split_x = page_width / 2.0
        left_count = sum(1 for b in candidate_blocks if b["center_x"] < split_x * 0.98)
        right_count = sum(1 for b in candidate_blocks if b["center_x"] > split_x * 1.02)

        return left_count >= 3 and right_count >= 3

    def _extract_page_tables(self, page: fitz.Page, page_number: int, text_blocks: Optional[List[Dict]] = None) -> Tuple[List[TextSegment], List[Tuple[float, float, float, float]]]:
        """
        提取页面表格

        Args:
            page: PDF页面对象
            page_number: 页码
            text_blocks: 页面文本块（用于表格标题绑定）

        Returns:
            (表格段落列表, 表格区域列表)
        """
        segments: List[TextSegment] = []
        table_regions: List[Tuple[float, float, float, float]] = []

        try:
            tables = page.find_tables()

            for table_idx, table in enumerate(tables):
                table_data = table.extract()
                cleaned_data = self._normalize_table_data(table_data)
                if not cleaned_data:
                    continue

                table_markdown = self._convert_table_to_markdown(cleaned_data)
                if len(table_markdown) < self.config.min_text_length:
                    continue

                table_bbox = tuple(table.bbox)
                table_regions.append(table_bbox)
                position_y = float(table_bbox[1])
                position_x = float(table_bbox[0])
                table_id = f"P{page_number:03d}_T{table_idx:03d}"

                table_title = self._find_table_title(text_blocks or [], table_bbox, page.rect.width)
                content_parts = ["**[表格]**"]
                if table_title:
                    content_parts.append(table_title)
                content_parts.append(table_markdown)

                header_rows, body_rows = self._split_table_header_rows(cleaned_data)
                column_headers = self._compose_column_headers(header_rows)

                table_segment = TextSegment(
                    segment_id=table_id,
                    content="\n\n".join(content_parts),
                    page_number=page_number,
                    position_y=position_y,
                    position_x=position_x,
                    segment_type="table",
                    source_table_id=table_id,
                    structured_data={
                        "table_id": table_id,
                        "table_title": table_title or "",
                        "page": page_number,
                        "bbox": [float(x) for x in table_bbox],
                        "header_rows": header_rows,
                        "column_headers": column_headers,
                        "body_row_count": len(body_rows),
                        "ocr_table": False,
                    },
                )
                segments.append(table_segment)

                row_segments, cell_segments = self._build_structured_table_segments(
                    cleaned_data=cleaned_data,
                    table_id=table_id,
                    table_title=table_title,
                    page_number=page_number,
                    position_y=position_y,
                    position_x=position_x,
                )
                segments.extend(row_segments)
                segments.extend(cell_segments)

        except Exception as e:
            self.logger.warning(f"页面 {page_number} 表格提取失败: {e}")

        return segments, table_regions

    def _build_structured_table_segments(
        self,
        cleaned_data: List[List[str]],
        table_id: str,
        table_title: str,
        page_number: int,
        position_y: float,
        position_x: float,
    ) -> Tuple[List[TextSegment], List[TextSegment]]:
        """基于检测到的表格生成行级与单元格级结构化证据对象。"""
        if len(cleaned_data) < 2:
            return [], []

        header_rows, body_rows = self._split_table_header_rows(cleaned_data)
        headers = self._compose_column_headers(header_rows)
        if not headers or not body_rows:
            return [], []

        row_segments: List[TextSegment] = []
        cell_segments: List[TextSegment] = []
        unit_col_idx = self._find_unit_column_index(headers)
        last_row_header = ""

        for row_idx, row in enumerate(body_rows, start=1):
            normalized_row = list(row) + [""] * max(0, len(headers) - len(row))
            row_header = self._pick_row_header(normalized_row, last_row_header=last_row_header)
            if not row_header:
                continue
            last_row_header = row_header

            row_unit = self._infer_row_unit(table_title, headers, normalized_row, unit_col_idx)
            row_pairs = []
            row_values_for_text = [row_header]
            for col_idx, cell in enumerate(normalized_row):
                cell_text = self._clean_text(cell)
                if not cell_text or col_idx == 0:
                    continue
                col_header = self._normalize_column_header(headers, col_idx)
                if col_idx == unit_col_idx:
                    if row_unit is None:
                        row_unit = cell_text
                    continue
                row_pairs.append({"col_header": col_header, "value": cell_text, "col_idx": col_idx})
                row_values_for_text.extend([col_header, cell_text])

            if not row_pairs:
                continue

            row_text = " | ".join([part for part in row_values_for_text if part])
            row_years = [self._extract_year_from_text(pair.get("col_header")) for pair in row_pairs]
            row_years = [year for year in row_years if year is not None]
            row_content_lines = [
                "[table_row]",
                f"table_id = {table_id}",
                f"row_header = {row_header}",
            ]
            if table_title:
                row_content_lines.append(f"table_title = {table_title}")
            if row_unit:
                row_content_lines.append(f"unit = {row_unit}")
            if row_years:
                row_content_lines.append(f"year = {row_years[0]}")
            row_content_lines.append(f"page = {page_number}")
            row_content_lines.append(f"row_text = {row_text}")

            row_segment = TextSegment(
                segment_id=f"{table_id}_R{row_idx:03d}",
                content="\n".join(row_content_lines),
                page_number=page_number,
                position_y=position_y + row_idx * 0.001,
                position_x=position_x,
                segment_type="table_row",
                source_table_id=table_id,
                row_header=row_header,
                unit=row_unit,
                structured_data={
                    "table_id": table_id,
                    "table_title": table_title or "",
                    "page": page_number,
                    "header_rows": header_rows,
                    "column_headers": headers,
                    "row_header": row_header,
                    "unit": row_unit,
                    "row_pairs": row_pairs,
                    "row_text": row_text,
                    "year_hints": row_years,
                    "row_index": row_idx,
                    "layout_order": row_idx,
                },
            )
            row_segments.append(row_segment)

            for pair_idx, pair in enumerate(row_pairs, start=1):
                value_text = pair["value"]
                col_header = pair["col_header"]
                year_hint = self._extract_year_from_text(col_header)
                cell_content_lines = [
                    "[table_cell]",
                    f"table_id = {table_id}",
                    f"row_header = {row_header}",
                    f"col_header = {col_header}",
                    f"value = {value_text}",
                ]
                if row_unit:
                    cell_content_lines.append(f"unit = {row_unit}")
                if year_hint is not None:
                    cell_content_lines.append(f"year = {year_hint}")
                cell_content_lines.append(f"page = {page_number}")
                if table_title:
                    cell_content_lines.append(f"table_title = {table_title}")

                cell_segment = TextSegment(
                    segment_id=f"{table_id}_R{row_idx:03d}_C{pair_idx:03d}",
                    content="\n".join(cell_content_lines),
                    page_number=page_number,
                    position_y=position_y + row_idx * 0.001 + pair_idx * 0.0001,
                    position_x=position_x,
                    segment_type="table_cell",
                    source_table_id=table_id,
                    row_header=row_header,
                    col_header=col_header,
                    value_text=value_text,
                    unit=row_unit,
                    structured_data={
                        "table_id": table_id,
                        "table_title": table_title or "",
                        "page": page_number,
                        "header_rows": header_rows,
                        "column_headers": headers,
                        "row_header": row_header,
                        "col_header": col_header,
                        "value": value_text,
                        "unit": row_unit,
                        "year": year_hint,
                        "row_text": row_text,
                        "row_index": row_idx,
                        "col_idx": pair.get("col_idx", pair_idx),
                        "layout_order": row_idx * 100 + pair_idx,
                    },
                )
                cell_segments.append(cell_segment)

        return row_segments, cell_segments

    def _normalize_table_data(self, table_data: Optional[List[List[str]]]) -> List[List[str]]:
        """清理表格数据并移除完全空白的行。"""
        if not table_data:
            return []

        normalized: List[List[str]] = []
        max_cols = max((len(row or []) for row in table_data), default=0)
        if max_cols <= 0:
            return []

        for row in table_data:
            raw_row = row or []
            cleaned_row = [self._clean_text(cell) if cell else "" for cell in raw_row]
            if len(cleaned_row) < max_cols:
                cleaned_row.extend([""] * (max_cols - len(cleaned_row)))
            if any(cell for cell in cleaned_row):
                normalized.append(cleaned_row)

        return normalized

    def _find_unit_column_index(self, headers: List[str]) -> Optional[int]:
        """查找表格中明显的单位列。"""
        for idx, header in enumerate(headers):
            h = (header or "").strip().lower()
            if h in {"unit", "units", "uom"}:
                return idx
        return None

    def _split_table_header_rows(self, cleaned_data: List[List[str]]) -> Tuple[List[List[str]], List[List[str]]]:
        """为多行表头生成更稳定的列标题路径，避免把第二行表头误当数据。"""
        if not cleaned_data:
            return [], []
        if len(cleaned_data) == 1:
            return [cleaned_data[0]], []

        header_rows = [cleaned_data[0]]
        body_rows = cleaned_data[1:]

        if len(cleaned_data) >= 3 and self._should_merge_second_header_row(cleaned_data[0], cleaned_data[1], cleaned_data[2]):
            header_rows.append(cleaned_data[1])
            body_rows = cleaned_data[2:]

        return header_rows, body_rows

    def _should_merge_second_header_row(self, first_row: List[str], second_row: List[str], third_row: List[str]) -> bool:
        """启发式判断第二行是否仍属于表头。"""
        trailing_first = [self._clean_text(cell) for cell in first_row[1:]]
        trailing_second = [self._clean_text(cell) for cell in second_row[1:]]
        trailing_third = [self._clean_text(cell) for cell in third_row[1:]]

        second_nonempty = sum(1 for cell in trailing_second if cell)
        if second_nonempty < 2:
            return False

        yearish = sum(1 for cell in trailing_second if self._looks_like_period_header(cell))
        first_sparse = sum(1 for cell in trailing_first if cell) <= max(1, len(trailing_first) // 2)
        third_numeric = sum(1 for cell in trailing_third if self._looks_like_numeric_cell(cell))

        if yearish >= 1 and third_numeric >= 1:
            return True
        if first_sparse and third_numeric >= 2:
            return True
        return False

    def _compose_column_headers(self, header_rows: List[List[str]]) -> List[str]:
        """将 1-2 行表头折叠成列标题路径。"""
        if not header_rows:
            return []

        max_cols = max(len(row) for row in header_rows)
        headers: List[str] = []
        carry_parts: List[str] = [""] * max_cols
        for col_idx in range(max_cols):
            parts: List[str] = []
            for row_idx, row in enumerate(header_rows):
                cell = self._clean_text(row[col_idx]) if col_idx < len(row) else ""
                if cell:
                    carry_parts[col_idx] = cell
                    if not parts or parts[-1].lower() != cell.lower():
                        parts.append(cell)
                elif row_idx == 0 and carry_parts[col_idx]:
                    if not parts or parts[-1].lower() != carry_parts[col_idx].lower():
                        parts.append(carry_parts[col_idx])
            header_value = " | ".join(part for part in parts if part)
            headers.append(header_value or f"column_{col_idx + 1}")
        return headers

    def _looks_like_period_header(self, text: str) -> bool:
        text = self._clean_text(text)
        if not text:
            return False
        return bool(re.search(r"\b(?:FY)?20\d{2}\b|\bQ[1-4]\b|\bH[12]\b|current|previous|base year", text, flags=re.IGNORECASE))

    def _looks_like_numeric_cell(self, text: str) -> bool:
        text = self._clean_text(text)
        if not text:
            return False
        return bool(re.search(r"-?\d[\d,]*(?:\.\d+)?", text))

    def _pick_row_header(self, row: List[str], last_row_header: str = "") -> str:
        """优先取首列作为行标题；空首列时谨慎继承上一行标题。"""
        if row and row[0]:
            return row[0]
        if last_row_header and any(self._looks_like_numeric_cell(cell) for cell in row[1:]):
            return last_row_header
        for cell in row:
            if cell:
                return cell
        return ""

    def _normalize_column_header(self, headers: List[str], col_idx: int) -> str:
        """获取列标题，空标题时使用稳定占位名。"""
        if 0 <= col_idx < len(headers) and headers[col_idx]:
            return headers[col_idx]
        return f"column_{col_idx + 1}"

    def _infer_row_unit(self, table_title: str, headers: List[str], row: List[str], unit_col_idx: Optional[int]) -> Optional[str]:
        """根据显式 unit 列或文本模式推断该行单位。"""
        if unit_col_idx is not None and 0 <= unit_col_idx < len(row):
            candidate = self._clean_text(row[unit_col_idx])
            if candidate:
                return candidate

        search_text = " | ".join([table_title or ""] + [h for h in headers if h] + [c for c in row if c])
        unit_match = re.search(
            r"\b(tCO2e|tCO₂e|ktCO2e|ktCO₂e|MtCO2e|MtCO₂e|CO2e|CO₂e|m3|m³|km3|km³|ML|kL|GJ|MWh|kWh|TJ|MW|%|ppb|ppm|tonnes?|tons?|kg|g/L|mg/L|m³/day|L/s)\b",
            search_text,
            flags=re.IGNORECASE,
        )
        if unit_match:
            return unit_match.group(1)
        return None

    def _extract_year_from_text(self, text: Optional[str]) -> Optional[int]:
        value = self._clean_text(text or "")
        if not value:
            return None
        match = re.search(r"(?<!\d)(19\d{2}|20\d{2}|21\d{2})(?!\d)", value)
        if not match:
            return None
        try:
            return int(match.group(1))
        except Exception:
            return None

    def _should_apply_ocr(self, page: fitz.Page, raw_text_blocks: List[Dict], table_regions: List[Tuple[float, float, float, float]]) -> bool:
        """判断当前页面是否需要 OCR 回退。"""
        if not self._ocr_is_available():
            return False
        if not self._get_bool_config("enable_ocr", False):
            return False

        page_text_length = sum(len((b.get("text") or "").strip()) for b in raw_text_blocks)
        low_text_threshold = self._get_int_config("ocr_page_text_threshold", 50)
        if page_text_length < low_text_threshold:
            return True

        if self._get_bool_config("enable_ocr_table", False) and not table_regions and self._page_has_large_images(page):
            return True

        return False

    def _extract_page_ocr_blocks(self, page: fitz.Page, page_number: int) -> List[Dict]:
        """从整页渲染图中提取 OCR 文本块。"""
        if not self._ocr_is_available():
            return []

        try:
            image = self._render_page_for_ocr(page)
            if image is None:
                return []

            lang = self._get_ocr_lang()
            config = "--psm 6"
            ocr_data = pytesseract.image_to_data(
                image,
                lang=lang,
                config=config,
                output_type=TesseractOutput.DICT,
            )
            if not ocr_data:
                return []

            img_width = max(1, int(image.width))
            img_height = max(1, int(image.height))
            scale_x = float(page.rect.width) / float(img_width)
            scale_y = float(page.rect.height) / float(img_height)

            grouped: Dict[Tuple[int, int, int], Dict] = {}
            text_items = ocr_data.get("text", []) or []
            for idx, raw_text in enumerate(text_items):
                token = self._clean_text(raw_text)
                if not token:
                    continue
                conf_raw = (ocr_data.get("conf", []) or ["-1"] * len(text_items))[idx]
                try:
                    conf = float(conf_raw)
                except Exception:
                    conf = -1.0
                if conf < 0:
                    continue

                block_num = int((ocr_data.get("block_num", []) or [0] * len(text_items))[idx] or 0)
                par_num = int((ocr_data.get("par_num", []) or [0] * len(text_items))[idx] or 0)
                line_num = int((ocr_data.get("line_num", []) or [0] * len(text_items))[idx] or 0)
                key = (block_num, par_num, line_num)
                left = int((ocr_data.get("left", []) or [0] * len(text_items))[idx] or 0)
                top = int((ocr_data.get("top", []) or [0] * len(text_items))[idx] or 0)
                width = int((ocr_data.get("width", []) or [0] * len(text_items))[idx] or 0)
                height = int((ocr_data.get("height", []) or [0] * len(text_items))[idx] or 0)

                entry = grouped.setdefault(key, {
                    "tokens": [],
                    "left": left,
                    "top": top,
                    "right": left + width,
                    "bottom": top + height,
                })
                entry["tokens"].append({
                    "left": left,
                    "right": left + width,
                    "top": top,
                    "bottom": top + height,
                    "text": token,
                })
                entry["left"] = min(entry["left"], left)
                entry["top"] = min(entry["top"], top)
                entry["right"] = max(entry["right"], left + width)
                entry["bottom"] = max(entry["bottom"], top + height)

            blocks: List[Dict] = []
            page_width = float(page.rect.width)
            original_index = len(grouped) + 100000
            for _, entry in sorted(grouped.items(), key=lambda kv: (kv[1]["top"], kv[1]["left"])):
                sorted_tokens = sorted(entry["tokens"], key=lambda item: item["left"])
                tokens = [token["text"] for token in sorted_tokens]
                line_text = self._clean_multiline_text(" ".join(tokens))
                min_len = self._get_int_config("ocr_min_text_len", 12)
                if len(line_text) < min_len:
                    continue

                bbox = (
                    float(entry["left"]) * scale_x,
                    float(entry["top"]) * scale_y,
                    float(entry["right"]) * scale_x,
                    float(entry["bottom"]) * scale_y,
                )
                width = max(0.0, float(bbox[2] - bbox[0]))
                height = max(0.0, float(bbox[3] - bbox[1]))
                center_x = float(bbox[0] + bbox[2]) / 2.0

                blocks.append({
                    "text": line_text,
                    "position_y": float(bbox[1]),
                    "position_x": float(bbox[0]),
                    "bbox": bbox,
                    "width": width,
                    "height": height,
                    "center_x": center_x,
                    "max_font_size": 0.0,
                    "avg_font_size": 0.0,
                    "is_probable_title": self._is_probable_title(line_text, 0.0, 0.0, width, page_width),
                    "is_figure_caption": self._is_figure_caption(line_text),
                    "original_index": original_index,
                    "segment_type": "ocr_text",
                    "ocr_tokens": [
                        {
                            "left": float(tok["left"]) * scale_x,
                            "right": float(tok["right"]) * scale_x,
                            "top": float(tok["top"]) * scale_y,
                            "bottom": float(tok["bottom"]) * scale_y,
                            "text": tok["text"],
                        }
                        for tok in sorted_tokens
                    ],
                })
                original_index += 1

            if blocks:
                self.logger.info(f"页面 {page_number} OCR 回退生成 {len(blocks)} 个文本块")
            return blocks
        except Exception as e:
            self.logger.warning(f"页面 {page_number} OCR 提取失败: {e}")
            return []

    def _extract_ocr_table_segments(
        self,
        page: fitz.Page,
        page_number: int,
        ocr_blocks: List[Dict],
        text_blocks: Optional[List[Dict]] = None,
        existing_table_count: int = 0,
    ) -> Tuple[List[TextSegment], List[Tuple[float, float, float, float]]]:
        """尽量将 OCR 行块恢复成结构化表格，再生成 table / table_row / table_cell 证据。"""
        if not ocr_blocks:
            return [], []

        table_segments: List[TextSegment] = []
        table_regions: List[Tuple[float, float, float, float]] = []
        groups = self._group_ocr_table_candidates(page, ocr_blocks)
        if not groups:
            return [], []

        for group_idx, group in enumerate(groups):
            table_data = self._build_table_from_ocr_group(group)
            cleaned_data = self._normalize_table_data(table_data)
            if len(cleaned_data) < 2:
                continue
            max_cols = max((len(row) for row in cleaned_data), default=0)
            if max_cols < 2:
                continue

            bbox = (
                min(float(block["bbox"][0]) for block in group),
                min(float(block["bbox"][1]) for block in group),
                max(float(block["bbox"][2]) for block in group),
                max(float(block["bbox"][3]) for block in group),
            )
            table_regions.append(bbox)
            table_id = f"P{page_number:03d}_OT{existing_table_count + group_idx:03d}"
            table_title = self._find_table_title(text_blocks or [], bbox, page.rect.width)
            table_markdown = self._convert_table_to_markdown(cleaned_data)
            if len(table_markdown) < self.config.min_text_length:
                continue

            header_rows, body_rows = self._split_table_header_rows(cleaned_data)
            column_headers = self._compose_column_headers(header_rows)
            content_parts = ["**[OCR表格]**"]
            if table_title:
                content_parts.append(table_title)
            content_parts.append(table_markdown)

            table_segments.append(TextSegment(
                segment_id=table_id,
                content="\n\n".join(content_parts),
                page_number=page_number,
                position_y=float(bbox[1]),
                position_x=float(bbox[0]),
                segment_type="table",
                source_table_id=table_id,
                structured_data={
                    "table_id": table_id,
                    "table_title": table_title or "",
                    "page": page_number,
                    "bbox": [float(x) for x in bbox],
                    "header_rows": header_rows,
                    "column_headers": column_headers,
                    "body_row_count": len(body_rows),
                    "ocr_table": True,
                },
            ))

            row_segments, cell_segments = self._build_structured_table_segments(
                cleaned_data=cleaned_data,
                table_id=table_id,
                table_title=table_title,
                page_number=page_number,
                position_y=float(bbox[1]),
                position_x=float(bbox[0]),
            )
            table_segments.extend(row_segments)
            table_segments.extend(cell_segments)

        return table_segments, table_regions

    def _group_ocr_table_candidates(self, page: fitz.Page, ocr_blocks: List[Dict]) -> List[List[Dict]]:
        """将 OCR 行块按垂直邻近关系分组为候选表格区域。"""
        if not ocr_blocks:
            return []
        ordered = sorted(ocr_blocks, key=lambda b: (float(b.get("position_y") or 0.0), float(b.get("position_x") or 0.0)))
        groups: List[List[Dict]] = []
        current: List[Dict] = []
        last_bottom: Optional[float] = None
        gap_threshold = max(18.0, float(page.rect.height) * 0.012)

        for block in ordered:
            bbox = block.get("bbox") or (0.0, 0.0, 0.0, 0.0)
            top = float(bbox[1])
            bottom = float(bbox[3])
            if current and last_bottom is not None and (top - last_bottom) > gap_threshold:
                if self._is_probable_ocr_table_group(current):
                    groups.append(current)
                current = []
            current.append(block)
            last_bottom = bottom

        if current and self._is_probable_ocr_table_group(current):
            groups.append(current)
        return groups

    def _is_probable_ocr_table_group(self, group: List[Dict]) -> bool:
        """启发式判断 OCR 行块组是否更像表格而非正文。"""
        if len(group) < 2:
            return False
        rows_with_multiple_cells = 0
        numeric_rows = 0
        max_cols = 0
        for block in group:
            cells = self._split_ocr_line_into_cells(block)
            max_cols = max(max_cols, len(cells))
            if len(cells) >= 2:
                rows_with_multiple_cells += 1
            if any(self._looks_like_numeric_cell(cell) for cell in cells[1:]):
                numeric_rows += 1
        return max_cols >= 2 and rows_with_multiple_cells >= 2 and numeric_rows >= 1

    def _build_table_from_ocr_group(self, group: List[Dict]) -> List[List[str]]:
        """基于 OCR token 间距把一组行块拆成表格二维数组。"""
        rows: List[List[str]] = []
        max_cols = 0
        for block in group:
            cells = self._split_ocr_line_into_cells(block)
            if not cells:
                continue
            rows.append(cells)
            max_cols = max(max_cols, len(cells))
        if max_cols <= 0:
            return []
        normalized = []
        for row in rows:
            padded = list(row) + [""] * max(0, max_cols - len(row))
            normalized.append([self._clean_text(cell) for cell in padded])
        return normalized

    def _split_ocr_line_into_cells(self, block: Dict) -> List[str]:
        """根据 OCR token 的水平大间距把一行拆成多个单元格。"""
        tokens = list(block.get("ocr_tokens") or [])
        if not tokens:
            text = self._clean_text(block.get("text") or "")
            return [text] if text else []

        tokens = sorted(tokens, key=lambda item: float(item.get("left") or 0.0))
        widths = [max(1.0, float(tok.get("right", 0.0)) - float(tok.get("left", 0.0))) for tok in tokens]
        median_width = sorted(widths)[len(widths) // 2] if widths else 12.0
        gap_threshold = max(18.0, median_width * 1.8)

        cells: List[str] = []
        current_parts: List[str] = []
        prev_right: Optional[float] = None
        for tok in tokens:
            left = float(tok.get("left") or 0.0)
            right = float(tok.get("right") or left)
            text = self._clean_text(tok.get("text") or "")
            if not text:
                continue
            if prev_right is not None and (left - prev_right) >= gap_threshold and current_parts:
                cells.append(self._clean_multiline_text(" ".join(current_parts)))
                current_parts = []
            current_parts.append(text)
            prev_right = right
        if current_parts:
            cells.append(self._clean_multiline_text(" ".join(current_parts)))
        return [cell for cell in cells if cell]

    def _render_page_for_ocr(self, page: fitz.Page):
        """将页面渲染为 OCR 用图像。"""
        if Image is None:
            return None

        zoom = max(1.0, float(self._get_float_config("ocr_render_zoom", 2.0)))
        matrix = fitz.Matrix(zoom, zoom)
        pix = page.get_pixmap(matrix=matrix, alpha=False)
        image_bytes = pix.tobytes("png")
        return Image.open(io.BytesIO(image_bytes))

    def _ocr_is_available(self) -> bool:
        """OCR 依赖是否可用。"""
        return Image is not None and pytesseract is not None and TesseractOutput is not None

    def _page_has_large_images(self, page: fitz.Page) -> bool:
        """判断页面是否含有足够大的图片区域，用于触发 OCR 回退。"""
        try:
            infos = page.get_image_info(xrefs=True)
        except Exception:
            return False

        if not infos:
            return False

        page_area = max(1.0, float(page.rect.width * page.rect.height))
        min_area = float(self._get_int_config("ocr_image_min_area", 50000))
        max_images = max(1, self._get_int_config("ocr_max_images_per_page", 4))
        large_images = 0
        accumulated_area = 0.0

        for info in infos:
            bbox = info.get("bbox")
            if not bbox or len(bbox) < 4:
                continue
            area = max(0.0, float(bbox[2] - bbox[0])) * max(0.0, float(bbox[3] - bbox[1]))
            if area >= min_area:
                large_images += 1
                accumulated_area += area
                if large_images >= max_images:
                    return True

        return (accumulated_area / page_area) >= 0.18 if accumulated_area > 0 else False

    def _deduplicate_blocks(self, blocks: List[Dict]) -> List[Dict]:
        """对 OCR 与原生文本块做轻量去重，优先保留原生文本块。"""
        deduped: List[Dict] = []
        seen: Dict[Tuple[str, float], int] = {}
        ordered_blocks = sorted(blocks, key=lambda x: (x.get("position_y", 0.0), x.get("position_x", 0.0), x.get("original_index", 0)))
        for block in ordered_blocks:
            normalized_text = re.sub(r"\s+", " ", (block.get("text") or "")).strip().lower()
            if not normalized_text:
                continue
            key = (normalized_text, round(float(block.get("position_y") or 0.0), 1))
            existing_idx = seen.get(key)
            if existing_idx is None:
                seen[key] = len(deduped)
                deduped.append(block)
                continue

            existing = deduped[existing_idx]
            existing_type = str(existing.get("segment_type") or "text")
            current_type = str(block.get("segment_type") or "text")
            if existing_type == "ocr_text" and current_type != "ocr_text":
                deduped[existing_idx] = block
        return deduped

    def _merge_cross_page_table_continuations(self, segments: List[TextSegment]) -> List[TextSegment]:
        """识别跨页续表，统一 logical table id，并尽量继承前页表头。"""
        if not segments:
            return segments

        ordered_tables = sorted(
            [seg for seg in segments if getattr(seg, "segment_type", "") == "table"],
            key=lambda seg: (getattr(seg, "page_number", 0) or 0, getattr(seg, "position_y", 0.0) or 0.0, getattr(seg, "segment_id", "")),
        )
        if len(ordered_tables) < 2:
            return segments

        segment_map = {seg.segment_id: seg for seg in segments}
        group_by_table: Dict[str, List[TextSegment]] = {}
        for seg in segments:
            table_key = str(getattr(seg, "source_table_id", None) or "")
            if table_key:
                group_by_table.setdefault(table_key, []).append(seg)

        logical_pages: Dict[str, List[int]] = {}
        for table_seg in ordered_tables:
            table_id = str(table_seg.source_table_id or table_seg.segment_id)
            logical_pages.setdefault(table_id, [int(getattr(table_seg, "page_number", 0) or 0)])

        prev_table = ordered_tables[0]
        for current_table in ordered_tables[1:]:
            if not self._is_table_continuation_candidate(prev_table, current_table):
                prev_table = current_table
                continue

            prev_logical_id = str(prev_table.source_table_id or prev_table.segment_id)
            current_physical_id = str(current_table.segment_id)
            prev_headers = list((getattr(prev_table, "structured_data", None) or {}).get("column_headers") or [])

            for seg in group_by_table.get(current_physical_id, []):
                seg.source_table_id = prev_logical_id
                structured = dict(getattr(seg, "structured_data", None) or {})
                structured["physical_table_id"] = current_physical_id
                structured["logical_table_id"] = prev_logical_id
                structured["table_id"] = prev_logical_id
                structured["continued_from"] = str(prev_table.segment_id)
                structured["continued"] = True
                if prev_headers:
                    self._adopt_previous_table_headers(seg, prev_headers)
                    structured = dict(getattr(seg, "structured_data", None) or structured)
                seg.structured_data = structured

            current_table_data = dict(getattr(current_table, "structured_data", None) or {})
            current_table_data["physical_table_id"] = current_physical_id
            current_table_data["logical_table_id"] = prev_logical_id
            current_table_data["table_id"] = prev_logical_id
            current_table_data["continued_from"] = str(prev_table.segment_id)
            current_table_data["continued"] = True
            pages = sorted(set(logical_pages.get(prev_logical_id, []) + [int(getattr(current_table, "page_number", 0) or 0)]))
            current_table_data["pages"] = pages
            current_table.structured_data = current_table_data
            logical_pages[prev_logical_id] = pages

            root_table = segment_map.get(prev_logical_id) if prev_table.segment_id != prev_logical_id else prev_table
            if root_table is not None:
                root_structured = dict(getattr(root_table, "structured_data", None) or {})
                existing_pages = list(root_structured.get("pages") or [int(getattr(root_table, "page_number", 0) or 0)])
                root_structured["pages"] = sorted(set(existing_pages + pages))
                root_structured["continued"] = True
                root_table.structured_data = root_structured

            prev_table = current_table

        return segments

    def _is_table_continuation_candidate(self, prev_table: TextSegment, current_table: TextSegment) -> bool:
        prev_page = int(getattr(prev_table, "page_number", 0) or 0)
        current_page = int(getattr(current_table, "page_number", 0) or 0)
        if current_page - prev_page != 1:
            return False

        prev_data = dict(getattr(prev_table, "structured_data", None) or {})
        current_data = dict(getattr(current_table, "structured_data", None) or {})
        prev_title = self._normalize_continuation_title(prev_data.get("table_title") or getattr(prev_table, "content", ""))
        current_title_raw = str(current_data.get("table_title") or getattr(current_table, "content", ""))
        current_title = self._normalize_continuation_title(current_title_raw)
        continuation_marker = bool(re.search(r"\bcontinued\b|续表|continued on next page", current_title_raw, flags=re.IGNORECASE))

        prev_headers = list(prev_data.get("column_headers") or [])
        current_headers = list(current_data.get("column_headers") or [])
        header_overlap = self._normalized_header_overlap(prev_headers, current_headers)
        current_generic_ratio = 0.0
        if current_headers:
            generic_count = sum(1 for header in current_headers if re.fullmatch(r"column_\d+", str(header or ""), flags=re.IGNORECASE))
            current_generic_ratio = generic_count / max(1, len(current_headers))

        same_title = bool(prev_title and current_title and prev_title == current_title)
        compatible_title = bool(prev_title and current_title and (prev_title in current_title or current_title in prev_title))
        if same_title or compatible_title:
            return True
        if continuation_marker and header_overlap >= 0.34:
            return True
        if header_overlap >= 0.60:
            return True
        if header_overlap >= 0.34 and current_generic_ratio >= 0.5:
            return True
        return False

    def _normalize_continuation_title(self, text: str) -> str:
        value = self._clean_text(text)
        value = re.sub(r"\bcontinued\b|continued on next page|continued from previous page|续表", " ", value, flags=re.IGNORECASE)
        value = re.sub(r"\s+", " ", value).strip().lower()
        return value

    def _normalized_header_overlap(self, prev_headers: List[str], current_headers: List[str]) -> float:
        def normalize(headers: List[str]) -> List[str]:
            values = []
            for header in headers or []:
                normalized = self._clean_text(header).strip().lower()
                if not normalized or re.fullmatch(r"column_\d+", normalized):
                    continue
                values.append(normalized)
            return values

        left = set(normalize(prev_headers))
        right = set(normalize(current_headers))
        if not left or not right:
            return 0.0
        return len(left & right) / float(max(1, min(len(left), len(right))))

    def _adopt_previous_table_headers(self, segment: TextSegment, prev_headers: List[str]) -> None:
        """当续表页缺少表头时，用上一页表头替换 generic column_x。"""
        structured = dict(getattr(segment, "structured_data", None) or {})
        if not prev_headers:
            segment.structured_data = structured
            return

        def remap_header(header: str) -> str:
            value = str(header or "").strip()
            match = re.fullmatch(r"column_(\d+)", value, flags=re.IGNORECASE)
            if not match:
                return value
            idx = int(match.group(1)) - 1
            if 0 <= idx < len(prev_headers) and prev_headers[idx]:
                return str(prev_headers[idx])
            return value

        if getattr(segment, "segment_type", "") == "table":
            current_headers = list(structured.get("column_headers") or [])
            if current_headers and all(re.fullmatch(r"column_\d+", str(h or ""), flags=re.IGNORECASE) for h in current_headers[1:]):
                structured["column_headers"] = list(prev_headers)
        elif getattr(segment, "segment_type", "") == "table_row":
            structured["column_headers"] = [remap_header(h) for h in list(structured.get("column_headers") or [])]
            row_pairs = []
            for pair in list(structured.get("row_pairs") or []):
                row_pairs.append({**pair, "col_header": remap_header(pair.get("col_header") or "")})
            structured["row_pairs"] = row_pairs
        elif getattr(segment, "segment_type", "") == "table_cell":
            new_header = remap_header(structured.get("col_header") or getattr(segment, "col_header", None) or "")
            if new_header:
                segment.col_header = new_header
                structured["col_header"] = new_header
                segment.content = self._replace_content_field(segment.content, "col_header", new_header)

        segment.structured_data = structured

    def _replace_content_field(self, content: str, field_name: str, value: str) -> str:
        pattern = rf"^{re.escape(field_name)}\s*=.*$"
        replacement = f"{field_name} = {value}"
        if re.search(pattern, str(content or ""), flags=re.MULTILINE):
            return re.sub(pattern, replacement, str(content or ""), count=1, flags=re.MULTILINE)
        return str(content or "")

    def _enrich_structured_table_segments(self, segments: List[TextSegment]) -> List[TextSegment]:
        if not segments:
            return segments

        groups: Dict[str, List[TextSegment]] = {}
        for seg in segments:
            table_key = str(getattr(seg, "source_table_id", None) or "")
            if table_key:
                groups.setdefault(table_key, []).append(seg)

        for table_id, group in groups.items():
            table_segment = next((seg for seg in group if getattr(seg, "segment_type", "") == "table"), None)
            table_headers = list((getattr(table_segment, "structured_data", None) or {}).get("column_headers") or []) if table_segment else []
            row_segments = sorted(
                [seg for seg in group if getattr(seg, "segment_type", "") == "table_row"],
                key=lambda seg: (float(getattr(seg, "position_y", 0.0) or 0.0), float(getattr(seg, "position_x", 0.0) or 0.0), getattr(seg, "segment_id", "")),
            )
            cell_segments = sorted(
                [seg for seg in group if getattr(seg, "segment_type", "") == "table_cell"],
                key=lambda seg: (float(getattr(seg, "position_y", 0.0) or 0.0), float(getattr(seg, "position_x", 0.0) or 0.0), getattr(seg, "segment_id", "")),
            )

            row_map: Dict[str, TextSegment] = {}
            for row_seg in row_segments:
                row_key = str(getattr(row_seg, "segment_id", "")).split("_C", 1)[0]
                row_map[row_key] = row_seg
                self._refresh_structured_segment_content(row_seg, table_headers)

            synthesized_rows: Dict[str, Dict] = {}
            for cell_seg in cell_segments:
                row_key = str(getattr(cell_seg, "segment_id", "")).rsplit("_C", 1)[0]
                row_seg = row_map.get(row_key)
                structured = dict(getattr(cell_seg, "structured_data", None) or {})
                if row_seg is not None:
                    row_struct = dict(getattr(row_seg, "structured_data", None) or {})
                    if not getattr(cell_seg, "row_header", None):
                        row_header = row_struct.get("row_header") or getattr(row_seg, "row_header", None)
                        if row_header:
                            cell_seg.row_header = row_header
                            structured["row_header"] = row_header
                    if not getattr(cell_seg, "unit", None):
                        unit = row_struct.get("unit") or getattr(row_seg, "unit", None)
                        if unit:
                            cell_seg.unit = unit
                            structured["unit"] = unit
                    if not getattr(cell_seg, "col_header", None):
                        col_header = structured.get("col_header") or row_struct.get("col_header")
                        if col_header:
                            cell_seg.col_header = col_header
                            structured["col_header"] = col_header
                if table_headers and (not getattr(cell_seg, "col_header", None) or re.fullmatch(r"column_\d+", str(getattr(cell_seg, "col_header", None) or ""), flags=re.IGNORECASE)):
                    current = str(getattr(cell_seg, "col_header", None) or structured.get("col_header") or "")
                    m = re.fullmatch(r"column_(\d+)", current, flags=re.IGNORECASE)
                    if m:
                        idx = int(m.group(1)) - 1
                        if 0 <= idx < len(table_headers) and table_headers[idx]:
                            cell_seg.col_header = str(table_headers[idx])
                            structured["col_header"] = str(table_headers[idx])
                year_hint = self._extract_year_from_text(getattr(cell_seg, "col_header", None) or structured.get("col_header"))
                if year_hint is not None:
                    structured["year"] = year_hint
                cell_seg.structured_data = structured
                self._refresh_structured_segment_content(cell_seg, table_headers, row_segment=row_seg)

                synth = synthesized_rows.setdefault(row_key, {
                    "table_id": table_id,
                    "page": getattr(cell_seg, "page_number", None),
                    "row_header": getattr(cell_seg, "row_header", None) or structured.get("row_header") or "",
                    "unit": getattr(cell_seg, "unit", None) or structured.get("unit"),
                    "pairs": [],
                    "position_y": float(getattr(cell_seg, "position_y", 0.0) or 0.0),
                    "position_x": float(getattr(cell_seg, "position_x", 0.0) or 0.0),
                })
                synth["pairs"].append({
                    "col_header": getattr(cell_seg, "col_header", None) or structured.get("col_header") or "",
                    "value": getattr(cell_seg, "value_text", None) or structured.get("value") or "",
                    "col_idx": structured.get("col_idx") or structured.get("layout_order") or 0,
                })

            for row_key, synth in synthesized_rows.items():
                if row_key in row_map:
                    continue
                row_pairs = sorted([pair for pair in synth["pairs"] if pair.get("value")], key=lambda pair: (int(pair.get("col_idx") or 0), str(pair.get("col_header") or "")))
                if not row_pairs or not synth.get("row_header"):
                    continue
                row_text_parts = [synth["row_header"]]
                for pair in row_pairs:
                    if pair.get("col_header"):
                        row_text_parts.append(pair["col_header"])
                    row_text_parts.append(pair["value"])
                row_content_lines = [
                    "[table_row]",
                    f"table_id = {table_id}",
                    f"row_header = {synth['row_header']}",
                ]
                if synth.get("unit"):
                    row_content_lines.append(f"unit = {synth['unit']}")
                row_content_lines.append(f"page = {synth.get('page')}")
                row_content_lines.append(f"row_text = {' | '.join([p for p in row_text_parts if p])}")
                new_seg = TextSegment(
                    segment_id=row_key,
                    content="\n".join(row_content_lines),
                    page_number=int(synth.get("page") or 0),
                    position_y=float(synth.get("position_y") or 0.0),
                    position_x=float(synth.get("position_x") or 0.0),
                    segment_type="table_row",
                    source_table_id=table_id,
                    row_header=synth["row_header"],
                    unit=synth.get("unit"),
                    structured_data={
                        "table_id": table_id,
                        "page": synth.get("page"),
                        "column_headers": table_headers,
                        "row_header": synth["row_header"],
                        "unit": synth.get("unit"),
                        "row_pairs": row_pairs,
                        "row_text": ' | '.join([p for p in row_text_parts if p]),
                    },
                )
                self._refresh_structured_segment_content(new_seg, table_headers)
                segments.append(new_seg)

            if table_segment is not None:
                self._refresh_structured_segment_content(table_segment, table_headers)

        return sorted(segments, key=lambda seg: (int(getattr(seg, "page_number", 0) or 0), float(getattr(seg, "position_y", 0.0) or 0.0), float(getattr(seg, "position_x", 0.0) or 0.0), getattr(seg, "segment_id", "")))

    def _refresh_structured_segment_content(self, segment: TextSegment, table_headers: Optional[List[str]] = None, row_segment: Optional[TextSegment] = None) -> None:
        seg_type = str(getattr(segment, "segment_type", "") or "")
        structured = dict(getattr(segment, "structured_data", None) or {})
        table_headers = list(table_headers or structured.get("column_headers") or [])

        if seg_type == "table":
            table_title = structured.get("table_title") or ""
            header_rows = structured.get("header_rows") or []
            content_parts = ["**[表格]**"]
            if table_title:
                content_parts.append(str(table_title))
            if header_rows:
                markdown = self._convert_table_to_markdown(header_rows + [table_headers] if table_headers and len(header_rows) == 1 else header_rows)
                if markdown:
                    content_parts.append(markdown)
            segment.content = "\n\n".join(content_parts)
            segment.structured_data = structured
            return

        if seg_type == "table_row":
            row_header = structured.get("row_header") or getattr(segment, "row_header", None) or ""
            row_pairs = list(structured.get("row_pairs") or [])
            unit = structured.get("unit") or getattr(segment, "unit", None)
            year_hints = [self._extract_year_from_text(pair.get("col_header")) for pair in row_pairs]
            year_hints = [y for y in year_hints if y is not None]
            row_text_parts = [row_header]
            for pair in row_pairs:
                if pair.get("col_header"):
                    row_text_parts.append(str(pair["col_header"]))
                if pair.get("value"):
                    row_text_parts.append(str(pair["value"]))
            lines = ["[table_row]", f"table_id = {structured.get('table_id') or getattr(segment, 'source_table_id', None) or ''}", f"row_header = {row_header}"]
            table_title = structured.get("table_title") or ""
            if table_title:
                lines.append(f"table_title = {table_title}")
            if unit:
                lines.append(f"unit = {unit}")
            if year_hints:
                lines.append(f"year = {year_hints[0]}")
            lines.append(f"page = {getattr(segment, 'page_number', 0)}")
            lines.append(f"row_text = {' | '.join([p for p in row_text_parts if p])}")
            segment.content = "\n".join(lines)
            segment.row_header = row_header or None
            segment.unit = unit or None
            structured["year_hints"] = year_hints
            structured["row_text"] = ' | '.join([p for p in row_text_parts if p])
            segment.structured_data = structured
            return

        if seg_type == "table_cell":
            if row_segment is not None:
                row_struct = dict(getattr(row_segment, "structured_data", None) or {})
                structured.setdefault("row_text", row_struct.get("row_text"))
            row_header = structured.get("row_header") or getattr(segment, "row_header", None) or ""
            col_header = structured.get("col_header") or getattr(segment, "col_header", None) or ""
            value = structured.get("value") or getattr(segment, "value_text", None) or ""
            unit = structured.get("unit") or getattr(segment, "unit", None) or ""
            year_hint = structured.get("year") or self._extract_year_from_text(col_header)
            lines = [
                "[table_cell]",
                f"table_id = {structured.get('table_id') or getattr(segment, 'source_table_id', None) or ''}",
                f"row_header = {row_header}",
                f"col_header = {col_header}",
                f"value = {value}",
            ]
            if unit:
                lines.append(f"unit = {unit}")
            if year_hint is not None:
                lines.append(f"year = {year_hint}")
            lines.append(f"page = {getattr(segment, 'page_number', 0)}")
            table_title = structured.get("table_title") or ""
            if table_title:
                lines.append(f"table_title = {table_title}")
            row_text = structured.get("row_text") or (dict(getattr(row_segment, "structured_data", None) or {}).get("row_text") if row_segment is not None else None)
            if row_text:
                lines.append(f"row_text = {row_text}")
                structured["row_text"] = row_text
            segment.content = "\n".join(lines)
            segment.row_header = row_header or None
            segment.col_header = col_header or None
            segment.value_text = value or None
            segment.unit = unit or None
            structured["year"] = year_hint
            segment.structured_data = structured

    def _get_bool_config(self, name: str, default: bool) -> bool:
        return bool(getattr(self.config, name, default))

    def _get_int_config(self, name: str, default: int) -> int:
        try:
            return int(getattr(self.config, name, default))
        except Exception:
            return int(default)

    def _get_float_config(self, name: str, default: float) -> float:
        try:
            return float(getattr(self.config, name, default))
        except Exception:
            return float(default)

    def _get_ocr_lang(self) -> str:
        raw_lang = getattr(self.config, "ocr_lang", None) or "eng"
        raw_lang = str(raw_lang).strip()
        if not raw_lang:
            return "eng"
        lang_aliases = {
            "en": "eng",
            "eng": "eng",
            "english": "eng",
            "ch": "chi_sim",
            "chi": "chi_sim",
            "chi_sim": "chi_sim",
            "zh": "chi_sim",
            "zh-cn": "chi_sim",
        }
        return lang_aliases.get(raw_lang.lower(), raw_lang)

    def _find_table_title(self, text_blocks: List[Dict], table_bbox: Tuple[float, float, float, float], page_width: float) -> str:
        """将表格标题或紧邻表格的说明文本绑定到表格段落。"""
        if not text_blocks:
            return ""

        table_top = float(table_bbox[1])
        table_left = float(table_bbox[0])
        table_right = float(table_bbox[2])

        candidates = []
        for block in text_blocks:
            bbox = block.get("bbox") or (0, 0, 0, 0)
            block_bottom = float(bbox[3])
            block_left = float(bbox[0])
            block_right = float(bbox[2])
            vertical_gap = table_top - block_bottom
            if vertical_gap < 0 or vertical_gap > 90:
                continue
            if not self._has_horizontal_overlap((table_left, table_right), (block_left, block_right), min_overlap_ratio=0.15):
                continue
            if self._is_figure_caption(block["text"]):
                continue
            if self._is_table_title_candidate(block, page_width):
                candidates.append((vertical_gap, block))

        if not candidates:
            return ""

        candidates.sort(key=lambda item: (item[0], item[1]["position_y"]))
        return candidates[0][1]["text"]

    def _is_table_title_candidate(self, block: Dict, page_width: float) -> bool:
        """判断文本块是否可能是表格标题。"""
        text = (block.get("text") or "").strip()
        if not text:
            return False

        if re.match(r"^(table|tab\.?|appendix)\b", text, flags=re.IGNORECASE):
            return True
        if block.get("is_figure_caption"):
            return False
        if block.get("is_probable_title") and len(text) <= 220 and block.get("width", 0.0) <= page_width * 0.90:
            return True
        return False

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

        cleaned_data = []
        for row in table_data:
            cleaned_row = [self._clean_text(cell) if cell else "" for cell in row]
            cleaned_data.append(cleaned_row)

        markdown_lines = []

        if cleaned_data:
            header = "| " + " | ".join(cleaned_data[0]) + " |"
            markdown_lines.append(header)

            separator = "| " + " | ".join(["-" * max(3, len(cell)) for cell in cleaned_data[0]]) + " |"
            markdown_lines.append(separator)

            for row in cleaned_data[1:]:
                content_row = "| " + " | ".join(row) + " |"
                markdown_lines.append(content_row)

        return "\n".join(markdown_lines)

    def _clean_multiline_text(self, text: str) -> str:
        """清理 block 文本，保留必要换行。"""
        if not text:
            return ""

        text = text.replace("\u00a0", " ").replace("\r", "\n")
        text = ''.join(char for char in text if ord(char) >= 32 or char == "\n")

        lines = []
        for raw_line in text.split("\n"):
            line = re.sub(r"\s+", " ", raw_line).strip()
            if line:
                lines.append(line)

        return "\n".join(lines).strip()

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

        text = " ".join(str(text).split())
        text = ''.join(char for char in text if ord(char) >= 32)
        return text.strip()

    def _is_probable_title(self, text: str, max_font_size: float, avg_font_size: float, width: float, page_width: float) -> bool:
        """粗略判断标题/标题层级文本。"""
        if not text:
            return False
        if len(text) > 180:
            return False
        if re.match(r"^(table|tab\.?|figure|fig\.?|chart)\b", text, flags=re.IGNORECASE):
            return True
        if width >= page_width * 0.92:
            return False
        if max_font_size >= 13.5 or avg_font_size >= 12.0:
            return True
        return bool(re.match(r"^(?:[A-Z][A-Za-z0-9\-&,()/ ]{2,}|\d+(?:\.\d+){0,3}\s+.+)$", text))

    def _is_figure_caption(self, text: str) -> bool:
        """判断是否是图注/图表说明。"""
        return bool(re.match(r"^(figure|fig\.?|chart)\b", (text or "").strip(), flags=re.IGNORECASE))

    def _is_header_footer_noise(self, block: Dict, page: fitz.Page) -> bool:
        """简单页眉页脚去噪，避免误删顶部标题。"""
        text = (block.get("text") or "").strip()
        if not text:
            return True

        page_height = float(page.rect.height)
        top_margin = page_height * 0.055
        bottom_margin = page_height * 0.94
        bbox = block.get("bbox") or (0, 0, 0, 0)
        y0 = float(bbox[1])
        y1 = float(bbox[3])

        near_top = y0 <= top_margin
        near_bottom = y1 >= bottom_margin
        if not near_top and not near_bottom:
            return False

        if near_top and block.get("is_probable_title") and block.get("width", 0.0) > page.rect.width * 0.35:
            return False
        if re.match(r"^(table|tab\.?|figure|fig\.?|chart|appendix)\b", text, flags=re.IGNORECASE):
            return False
        if len(text) > 90:
            return False
        if re.fullmatch(r"(?:page\s+)?\d+(?:\s+of\s+\d+)?", text, flags=re.IGNORECASE):
            return True
        if re.search(r"www\.|http[s]?://|@", text, flags=re.IGNORECASE):
            return True
        if near_bottom and len(text) <= 60:
            return True
        if near_top and len(text) <= 40 and block.get("avg_font_size", 0.0) <= 10.5:
            return True
        return False

    def _overlaps_any_table(self, bbox: Tuple[float, float, float, float], table_regions: List[Tuple[float, float, float, float]]) -> bool:
        """判断文本块是否与已提取表格区域显著重叠。"""
        if not table_regions:
            return False
        for table_bbox in table_regions:
            if self._bbox_overlap_ratio(bbox, table_bbox) >= 0.35:
                return True
        return False

    def _bbox_overlap_ratio(self, a: Tuple[float, float, float, float], b: Tuple[float, float, float, float]) -> float:
        """返回交集面积 / 较小矩形面积。"""
        ax0, ay0, ax1, ay1 = map(float, a)
        bx0, by0, bx1, by1 = map(float, b)

        inter_w = max(0.0, min(ax1, bx1) - max(ax0, bx0))
        inter_h = max(0.0, min(ay1, by1) - max(ay0, by0))
        if inter_w <= 0 or inter_h <= 0:
            return 0.0

        inter_area = inter_w * inter_h
        a_area = max(1.0, (ax1 - ax0) * (ay1 - ay0))
        b_area = max(1.0, (bx1 - bx0) * (by1 - by0))
        return inter_area / min(a_area, b_area)

    def _has_horizontal_overlap(self, a: Tuple[float, float], b: Tuple[float, float], min_overlap_ratio: float = 0.1) -> bool:
        """判断两个水平区间是否存在足够重叠。"""
        a0, a1 = map(float, a)
        b0, b1 = map(float, b)
        overlap = max(0.0, min(a1, b1) - max(a0, b0))
        base = max(1.0, min(a1 - a0, b1 - b0))
        return (overlap / base) >= min_overlap_ratio

    def _generate_document_id(self, file_path: Path) -> str:
        """
        生成文档ID

        Args:
            file_path: 文件路径

        Returns:
            文档ID
        """
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
            original_path = Path(document_content.file_path)
            output_path = original_path.parent / f"{original_path.stem}_extracted.md"

        output_path = Path(output_path)

        try:
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

            for segment in document_content.segments:
                preview = segment.content[:50] + "..." if len(segment.content) > 50 else segment.content
                markdown_content += f"| {segment.segment_id} | {segment.page_number} | {preview} |\n"

            with open(output_path, 'w', encoding='utf-8') as f:
                f.write(markdown_content)

            self.logger.info(f"Markdown文件已保存: {output_path}")
            return str(output_path)

        except Exception as e:
            raise ContentExtractionError(f"保存Markdown失败: {e}", str(output_path))
