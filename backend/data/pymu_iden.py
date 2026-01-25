import os

import fitz
import json

# EXTRACT WITH PyMuPDF CV
def extract_with_pymupdf(pdf_path, page_num):
    doc = fitz.open(pdf_path)
    page = doc.load_page(page_num - 1)

    tables = page.find_tables()
    
    if tables:
        print(f"PyMuPDF found {len(tables.tables)} table(s) on the page.")
        # .extract() returns the table content as a list of lists
        table_data = tables[0].extract() 
        for row in table_data:
            print(row)
        return table_data
    else:
        print("PyMuPDF could not automatically find a table on this page.")
        return None

print('''
Automatic extractor for the ESG tables and documents

The argument required is,
1. Path to metrics PDF
2. Page no. which the metrics standards table is on

Output is directly in STDOUT (terminal)
This is incase of that the table is multi-page

Copy the output into your desired JSON file.
    ''')

print("What is the PDF file path?")
file_path = input()
if not os.path.exists(file_path):
    print("Given metrics standards PDF path doesn't exist!")
    exit(1)

print("What is the PDF page number containing the metrics standards table?")

page_num = input()
if not page_num.isdecimal():
    print("Please give a number for page number!")
    exit(1)

page_with_table = int(page_num)
pdf_file_path = file_path
table_data = extract_with_pymupdf(pdf_file_path, page_with_table)


#print("What is the sub-industry title")
#doc_title = input()
print("What is the metric type")
print("1: Disclosure Topics & Metrics")
print("2: Activity Metrics")
metric_type = "Disclosure Topics & Metrics" if int(input()) == 1 else "Activity Metrics"

# SANITIZE AND COLLECT

header = table_data[0]
content = table_data[1:]

new_header = [head.replace('\n', ' ') for head in header]

prev_topic = None
for row in content:
    if row[0] is not None:
        prev_topic = row[0].replace('\n', ' ')
    else:
        row[0] = prev_topic

    for i in range(len(row)):
        row[i] = row[i].replace('\n', ' ')
    
print(new_header)
print(content)

# CONVERT TO JSON

metrics_list = []

header_to_json_key_map = {
    'TOPIC': 'Topic',
    'METRIC': 'Metric',
    'CATEGORY': 'Category',
    'UNIT OF MEASURE': 'Unit',
    'CODE': 'Code'
}

for row in content:
    row_data = dict(zip(header, row))

    metric = {
        'Metric': row_data.get('METRIC'),
        'Category': row_data.get('CATEGORY'),
        'Unit': row[3] if row[3] != 'n/a' else None,
        'Code': row_data.get('CODE'),
        'Topic': row_data.get('TOPIC'),

        'Type': metric_type,
        'Value': None,
        'Page': None,
        'Context': None
    }

    metrics_list.append(metric)

json_out = json.dumps(metrics_list, indent=2)

print(json_out)


