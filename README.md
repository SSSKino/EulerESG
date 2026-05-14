# SASB OCR Extractor

从 SASB 行业标准 PDF 中抽取结构化 JSON，包含：

- `SUSTAINABILITY DISCLOSURE TOPICS & METRICS`
- 可选 `Activity Metrics`
- `definition`
- `simple_definition`
- `topic_summary`

## 运行约束

- 本地 Python 运行，不使用 Docker。
- 不默认选择 reader，必须显式传 `--reader`。
- `paddleocr-vl` 按 GPU 环境使用，不提供 CPU 路径。
- strict 模式：OCR 必须输出非空 Markdown/Text，否则直接失败。
- 单 GPU 使用 `paddleocr-vl` 时建议 `--workers 1`，避免多个 VLM 实例同时占用同一张 GPU。

## 目录

```text
project/
  data/
    input/    # 输入 PDF
    output/   # 输出 JSON
    work/     # 必要中间文件
  src/sasb_ocr_extractor/
  config.example.yaml
  pyproject.toml
  requirements.txt
  README.md
```

`data/work/<pdf_name>/` 默认只保留必要信息：

```text
merged_ocr.md
run_metadata.json
```

初始化目录：

```bash
mkdir -p data/input data/output data/work
```

Windows PowerShell：

```powershell
New-Item -ItemType Directory -Force data/input,data/output,data/work
```

## 安装

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -U pip setuptools wheel
python -m pip install -e .
```

Windows PowerShell：

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -U pip setuptools wheel
python -m pip install -e .
```

如果使用 PaddleOCR-VL，安装 GPU 依赖，例如 CUDA 12.6：

```bash
python -m pip install paddlepaddle-gpu==3.2.1 -i https://www.paddlepaddle.org.cn/packages/stable/cu126/
python -m pip install -U "paddleocr[doc-parser]"
```

## 单文件抽取

```bash
sasb-extract \
  --input data/input/your_file.pdf \
  --output data/output/your_file.json \
  --workdir data/work \
  --reader paddleocr-vl
```

指定模型路径：

```bash
sasb-extract \
  --input data/input/your_file.pdf \
  --output data/output/your_file.json \
  --workdir data/work \
  --reader paddleocr-vl \
  --model-name-or-path /path/to/model
```

## 批量抽取

```bash
sasb-extract-batch \
  --input-dir data/input \
  --output-dir data/output \
  --workdir data/work \
  --pattern "*.pdf" \
  --reader paddleocr-vl \
  --workers 1
```

```powershell
sasb-extract-batch `
  --input-dir data/input `
  --output-dir data/output `
  --workdir data/work `
  --pattern "*.pdf" `
  --reader paddleocr-vl `
  --workers 1
```

说明：

- `paddleocr-vl`：单 GPU 推荐 `--workers 1`。
- `command/custom` reader：可按机器资源提高 `--workers`。

## 替换模型

### command reader

外部命令必须生成 `{output_markdown}`：

```bash
sasb-extract \
  --input data/input/your_file.pdf \
  --output data/output/your_file.json \
  --workdir data/work \
  --reader command \
  --ocr-command "python my_ocr.py --input {input} --output-md {output_markdown}"
```

### custom reader

自定义 reader 必须返回 `sasb_ocr_extractor.models.OcrDocument`：

```bash
sasb-extract \
  --input data/input/your_file.pdf \
  --output data/output/your_file.json \
  --workdir data/work \
  --reader custom \
  --custom-reader my_package.my_reader:MyReader
```

## 常用开关

```text
--include-activity       包含 Activity Metrics
--no-activity            不包含 Activity Metrics
--no-split               不拆分 (1)(2)(3) 子指标
--no-topic-summary       不提取 Topic Summary
--model-name-or-path     指定模型路径
--model-kwarg KEY=VALUE  传递模型参数，可重复
--no-cache               不复用 work 中的 OCR Markdown
--workers N              批量并行 worker 数
```

## 输出字段

```json
{
  "Metric": "...",
  "Category": "Quantitative",
  "Unit": "Percentage (%)",
  "Code": "CG-XX-000a.1",
  "Topic": "...",
  "Type": "Sustainability Disclosure Topics & Metrics",
  "Value": null,
  "Page": null,
  "Context": null,
  "definition": "...",
  "simple_definition": "...",
  "topic_summary": "..."
}
```

## 常见问题

没有传 `--reader` 会直接报错：

```text
OCR reader backend is required. Pass --reader command, --reader custom, or --reader paddleocr-vl.
```

`paddleocr-vl` 多 worker 报 dtype / bfloat16 / float32 错误时，改用：

```bash
--workers 1
```
