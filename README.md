# GRI 披露框架与定义提取器

这是一个精简版 GRI 官方文件提取工具。它只保留当前任务需要的功能：

1. 从 `data/input/topic` 中读取所有 GRI Topic / Universal / Glossary 官方 PDF，并提取 Glossary 定义。
2. 从 `data/input/sector` 中读取 GRI Sector 官方 PDF，例如 GRI 11 / 12 / 13 / 14，并提取披露框架。
3. 根据 sector 记录中引用到的 GRI code / Disclosure code，到 topic 定义库中查找对应定义。
4. 将定义直接写入每条记录的 `Definition` 字段。

输出不会包含 `DefinitionSources`、`MatchedCodes`、`SourcePage` 等多余字段。

---

## 1. 文件放置方式

将文件按下面结构放置：

```text
.gri_extractor/
  data/
    input/
      topic/      # 放 GRI Topic / Universal / Glossary 官方 PDF
      sector/     # 放 GRI 11 / 12 / 13 / 14 等 Sector 官方 PDF
    output/       # 程序输出 JSON
```

示例：

```text
data/input/topic/
  GRI 305 Emissions 2016.pdf
  GRI 302 Energy 2016.pdf
  GRI 403 Occupational Health and Safety 2018.pdf
  GRI Standards Glossary 2025.pdf

data/input/sector/
  GRI 11 Oil and Gas Sector 2021.pdf
  GRI 12 Coal Sector 2022.pdf
  GRI 13 Agriculture Aquaculture and Fishing Sector.pdf
  GRI 14 Mining Sector 2024.pdf
```

---

## 2. 最简单运行方式

进入项目目录：

```powershell
cd C:\Users\NINGMEI\Downloads\sasb\gri_extractor
```

直接运行：

```powershell
& E:/anaconda3/envs/PYG/python.exe run.py
```

这条命令会自动执行完整流程：

```text
input/topic  -> 提取定义
input/sector -> 提取披露框架
定义匹配      -> 写入 Definition 字段
output       -> 输出 JSON
```

---

## 3. 第一次使用需要安装依赖

如果没有安装依赖，先执行：

```powershell
& E:/anaconda3/envs/PYG/python.exe -m pip install -r requirements.txt
```

也可以安装成可编辑包：

```powershell
& E:/anaconda3/envs/PYG/python.exe -m pip install -e . --force-reinstall
```

注意：不要只在 `(base)` 里直接运行 `pip install`。你运行代码使用的是 `PYG` 环境，所以安装也要用：

```powershell
& E:/anaconda3/envs/PYG/python.exe -m pip ...
```

---

## 4. 输出结果

每个 sector PDF 会输出一个对应 JSON 文件，例如：

```text
data/output/
  GRI_11_Oil_and_Gas_Sector_2021.json
  GRI_12_Coal_Sector_2022.json
  GRI_13_Agriculture_Aquaculture_and_Fishing_Sector.json
  GRI_14_Mining_Sector_2024.json
```

每条记录字段如下：

```text
Standard
Metric
Code
Topic
Type
Value
Page
Context
Definition
```

`Definition` 是纯文本字段，例如：

```text
emissions: release of substances into the air
GHG emissions: greenhouse gas emissions released into the atmosphere
```

如果没有匹配到定义，则 `Definition` 为空字符串。

---

## 5. 可选运行参数

默认运行已经足够。只有在需要调整时才使用下面命令。

### 5.1 指定输入输出目录

```powershell
& E:/anaconda3/envs/PYG/python.exe run.py `
  --topic-dir data/input/topic `
  --sector-dir data/input/sector `
  --output-dir data/output
```

### 5.2 每条记录只保留文本中实际出现的定义词条

默认模式是 `all-by-code`：只要 sector 记录引用了某个 GRI code，就把该 code 对应定义加入 `Definition`。

如果希望更严格，只保留该条记录文本中实际出现过的定义词条，使用：

```powershell
& E:/anaconda3/envs/PYG/python.exe run.py --definition-selection term-in-record
```

### 5.3 限制 Definition 长度

默认不截断。若希望每条 Definition 最多 1000 字符：

```powershell
& E:/anaconda3/envs/PYG/python.exe run.py --definition-max-chars 1000
```

### 5.4 输出定义库用于检查

```powershell
& E:/anaconda3/envs/PYG/python.exe run.py --write-definition-bank data/output/definition_bank.json
```

---

## 6. 仍然可以使用 python -m 方式

如果不想用 `run.py`，也可以直接运行模块：

```powershell
& E:/anaconda3/envs/PYG/python.exe -m gri_ocr_extractor.batch_cli `
  --topic-dir data/input/topic `
  --sector-dir data/input/sector `
  --output-dir data/output
```

不建议直接使用 `gri-extract-batch`，因为 Windows PowerShell 有时找不到环境中的 Scripts 路径。

---

## 7. 当前代码保留内容

保留：

```text
PDF 文本读取
GRI Sector 披露框架提取
GRI Glossary 定义提取
Definition 字段补全
JSON 输出
```

删除：

```text
OCR / VLM / PaddleOCR
旧 SASB 逻辑
多余 compare 工具
DefinitionSources / MatchedCodes / SourcePage 等冗余字段
示例旧输出文件
```

---

## 8. 常见问题

### 问题 1：`gri-extract-batch` 无法识别

直接用：

```powershell
& E:/anaconda3/envs/PYG/python.exe run.py
```

或者：

```powershell
& E:/anaconda3/envs/PYG/python.exe -m gri_ocr_extractor.batch_cli
```

### 问题 2：没有输出结果

检查：

```text
data/input/topic 是否有 PDF
data/input/sector 是否有 PDF
```

### 问题 3：Definition 为空

可能原因：

```text
input/topic 中没有对应 GRI code 的文件
该 topic 文件的 Glossary 中没有该词条
sector 记录没有识别到对应 disclosure code
```

可以用下面命令输出定义库检查：

```powershell
& E:/anaconda3/envs/PYG/python.exe run.py --write-definition-bank data/output/definition_bank.json
```
