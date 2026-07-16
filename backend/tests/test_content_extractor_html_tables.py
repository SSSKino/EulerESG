from __future__ import annotations

import unittest

from esg_encoding.content_extractor import ContentExtractor


class HtmlTableSpanTests(unittest.TestCase):
    def setUp(self) -> None:
        self.extractor = ContentExtractor()

    def test_rowspan_keeps_dell_year_columns_aligned(self):
        table = """
        <table>
          <tr>
            <th>Performance metric</th><th>Unit</th><th>FY22</th>
            <th>FY23</th><th>FY24</th><th>Notes</th>
          </tr>
          <tr>
            <td>Overall</td><td rowspan="4">%</td><td>33.9%</td>
            <td>34.8%</td><td>35.0%</td><td></td>
          </tr>
          <tr>
            <td>People leader roles</td><td>28.2%</td><td>29.2%</td>
            <td>29.1%</td><td></td>
          </tr>
          <tr>
            <td>Technical roles</td><td>22.8%</td><td>24.5%</td>
            <td>25.0%</td><td></td>
          </tr>
          <tr>
            <td>Non-technical roles</td><td>47.7%</td><td>48.1%</td>
            <td>47.5%</td><td></td>
          </tr>
        </table>
        """

        rows = self.extractor._parse_html_table_rows(table)

        self.assertTrue(all(len(row) == 6 for row in rows))
        self.assertEqual(
            rows[3],
            ["Technical roles", "%", "22.8%", "24.5%", "25.0%", ""],
        )

        segments = self.extractor._table_segments_from_markdown(
            table,
            document_id="dell",
            page=86,
            table_id="dell-p86-table-1",
        )
        technical_cells = {
            segment.col_header: segment.value_text
            for segment in segments
            if segment.segment_type == "table_cell"
            and segment.row_header == "Technical roles"
        }
        self.assertEqual(
            technical_cells,
            {
                "Performance metric": "Technical roles",
                "Unit": "%",
                "FY22": "22.8%",
                "FY23": "24.5%",
                "FY24": "25.0%",
            },
        )

    def test_colspan_and_rowspan_expand_to_a_rectangular_grid(self):
        table = """
        <table>
          <tr><th>Group</th><th>Unit</th><th>FY22</th><th>FY23</th><th>FY24</th></tr>
          <tr><td colspan="5">U.S. race and ethnicity representation</td></tr>
          <tr><td>Asian</td><td rowspan="2">%</td><td>10%</td><td>11%</td><td>12%</td></tr>
          <tr><td>Black</td><td>8%</td><td>9%</td><td>10%</td></tr>
        </table>
        """

        rows = self.extractor._parse_html_table_rows(table)

        self.assertEqual(
            rows[1],
            ["U.S. race and ethnicity representation"] * 5,
        )
        self.assertEqual(rows[3], ["Black", "%", "8%", "9%", "10%"])


if __name__ == "__main__":
    unittest.main()
