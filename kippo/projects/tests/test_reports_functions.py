import datetime
from unittest.mock import MagicMock

from django.test import TestCase

from projects.reports.functions import _build_cost_report_blocks, _build_itemized_section


class BuildItemizedSectionTestCase(TestCase):
    """Tests for _build_itemized_section helper function."""

    def test_build_itemized_section_basic(self):
        """Test basic itemized section with service costs."""
        title = "Test Account"
        itemized_cost = {
            "Amazon EC2": 100.50,
            "Amazon S3": 50.25,
            "Amazon RDS": 25.00,
        }

        result = _build_itemized_section(title, itemized_cost)

        self.assertEqual(result["type"], "section")
        self.assertEqual(result["text"]["type"], "mrkdwn")
        # Check title with total is present
        self.assertIn("*Test Account:* $175.75", result["text"]["text"])
        # Check services are listed (sorted by cost descending)
        text = result["text"]["text"]
        ec2_pos = text.find("Amazon EC2")
        s3_pos = text.find("Amazon S3")
        rds_pos = text.find("Amazon RDS")
        self.assertTrue(ec2_pos < s3_pos < rds_pos, "Services should be sorted by cost descending")

    def test_build_itemized_section_empty(self):
        """Test itemized section with empty costs."""
        result = _build_itemized_section("Empty Account", {})

        self.assertEqual(result["type"], "section")
        self.assertIn("*Empty Account:* $0.00", result["text"]["text"])


class BuildCostReportBlocksTestCase(TestCase):
    """Tests for _build_cost_report_blocks function."""

    def setUp(self):
        """Set up test fixtures."""
        self.mock_project = MagicMock()
        self.mock_project.name = "Test Project"
        self.cumulative_cost = 1500.00
        self.current_month_cost = 500.00
        self.current_month = datetime.date(2025, 1, 1)

    def test_no_itemized_cost(self):
        """Test report blocks without itemized cost."""
        blocks = _build_cost_report_blocks(
            project=self.mock_project,
            cumulative_cost=self.cumulative_cost,
            current_month_cost=self.current_month_cost,
            current_month=self.current_month,
            current_month_itemized_cost=None,
        )

        # Should have header, divider, summary, divider (4 blocks)
        self.assertEqual(len(blocks), 4)
        self.assertEqual(blocks[0]["type"], "header")
        self.assertEqual(blocks[1]["type"], "divider")
        self.assertEqual(blocks[2]["type"], "section")
        self.assertEqual(blocks[3]["type"], "divider")

    def test_legacy_flat_itemized_cost(self):
        """Test report blocks with legacy flat itemized cost format."""
        itemized_cost = {
            "Amazon EC2": 300.00,
            "Amazon S3": 150.00,
            "Amazon RDS": 50.00,
        }

        blocks = _build_cost_report_blocks(
            project=self.mock_project,
            cumulative_cost=self.cumulative_cost,
            current_month_cost=self.current_month_cost,
            current_month=self.current_month,
            current_month_itemized_cost=itemized_cost,
        )

        # Should have header, divider, summary, divider, itemized, divider (6 blocks)
        self.assertEqual(len(blocks), 6)

        # Find the itemized block
        itemized_block = blocks[4]
        self.assertEqual(itemized_block["type"], "section")
        text = itemized_block["text"]["text"]
        self.assertIn("Itemized (2025-01)", text)
        self.assertIn("Amazon EC2", text)
        self.assertIn("$300.00", text)

    def test_nested_per_account_itemized_cost(self):
        """Test report blocks with new nested per-account itemized cost format."""
        itemized_cost = {
            "kiconiaworks": {
                "Amazon EC2": 200.00,
                "Amazon S3": 100.00,
            },
            "internal-services": {
                "Amazon EC2": 100.00,
                "Amazon RDS": 50.00,
            },
            "total": {
                "Amazon EC2": 300.00,
                "Amazon S3": 100.00,
                "Amazon RDS": 50.00,
            },
        }

        blocks = _build_cost_report_blocks(
            project=self.mock_project,
            cumulative_cost=self.cumulative_cost,
            current_month_cost=self.current_month_cost,
            current_month=self.current_month,
            current_month_itemized_cost=itemized_cost,
        )

        # Should have: header, divider, summary, divider,
        #              account1 section, account2 section, divider, total section, divider
        # = 9 blocks
        self.assertEqual(len(blocks), 9)

        # Find account sections (after the summary divider at index 3)
        account_sections = [b for b in blocks[4:7] if b["type"] == "section"]
        self.assertEqual(len(account_sections), 2)

        # Check that accounts are sorted by total cost descending
        # kiconiaworks total: 300, internal-services total: 150
        first_account_text = account_sections[0]["text"]["text"]
        self.assertIn("kiconiaworks", first_account_text)
        self.assertIn("$300.00", first_account_text)

        second_account_text = account_sections[1]["text"]["text"]
        self.assertIn("internal-services", second_account_text)
        self.assertIn("$150.00", second_account_text)

        # Check total section
        total_section = blocks[7]
        self.assertEqual(total_section["type"], "section")
        total_text = total_section["text"]["text"]
        self.assertIn("Total (2025-01)", total_text)
        self.assertIn("$450.00", total_text)

    def test_nested_single_account_itemized_cost(self):
        """Test report blocks with single account in nested format."""
        itemized_cost = {
            "main-account": {
                "Amazon EC2": 200.00,
            },
            "total": {
                "Amazon EC2": 200.00,
            },
        }

        blocks = _build_cost_report_blocks(
            project=self.mock_project,
            cumulative_cost=self.cumulative_cost,
            current_month_cost=self.current_month_cost,
            current_month=self.current_month,
            current_month_itemized_cost=itemized_cost,
        )

        # Should have: header, divider, summary, divider,
        #              account section, divider, total section, divider
        # = 8 blocks
        self.assertEqual(len(blocks), 8)

        # Check account section
        account_section = blocks[4]
        self.assertIn("main-account", account_section["text"]["text"])

        # Check total section
        total_section = blocks[6]
        self.assertIn("Total (2025-01)", total_section["text"]["text"])

    def test_total_key_with_numeric_value_treated_as_legacy(self):
        """Test that 'total' key with numeric value is treated as legacy format."""
        # This edge case ensures backward compatibility if someone has a service named 'total'
        itemized_cost = {
            "Amazon EC2": 200.00,
            "total": 50.00,  # Numeric value, not dict - treat as legacy service name
        }

        blocks = _build_cost_report_blocks(
            project=self.mock_project,
            cumulative_cost=self.cumulative_cost,
            current_month_cost=self.current_month_cost,
            current_month=self.current_month,
            current_month_itemized_cost=itemized_cost,
        )

        # Should use legacy format (6 blocks: header, div, summary, div, itemized, div)
        self.assertEqual(len(blocks), 6)

        # Check that it's treated as legacy flat format
        itemized_block = blocks[4]
        text = itemized_block["text"]["text"]
        self.assertIn("Itemized (2025-01)", text)
        self.assertIn("Amazon EC2", text)
        self.assertIn("total", text)
