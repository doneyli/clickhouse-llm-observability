"""Tests for tools layer: happy path and injected failure paths."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from src.tools.compliance import check_brand_guidelines, check_regulatory
from src.tools.error_injection import InjectedFault, maybe_inject
from src.tools.market import get_market_trends
from src.tools.sales import query_inventory, query_sales

# --- query_sales ---


def test_query_sales_happy_path():
    with patch("src.tools.sales.maybe_inject", return_value=None):
        result = query_sales.invoke({"brand": "Brand A", "region": "Southeast"})
    assert result["status"] == "ok"
    assert result["tool.outcome"] == "ok"
    assert result["row_count"] > 0
    assert result["total_units"] > 0


def test_query_sales_all_filters():
    with patch("src.tools.sales.maybe_inject", return_value=None):
        result = query_sales.invoke({
            "brand": "Brand A",
            "sku": "BRA-CLS-LRG",
            "region": "Midwest",
            "retail_partner": "MegaMart",
            "quarter_start": "2024-Q1",
            "quarter_end": "2024-Q4",
        })
    assert result["status"] == "ok"
    assert result["row_count"] >= 0


def test_query_sales_no_filters():
    with patch("src.tools.sales.maybe_inject", return_value=None):
        result = query_sales.invoke({})
    assert result["status"] == "ok"
    assert result["row_count"] > 0


@pytest.mark.parametrize("start,end", [("2024-Q1", "2025-Q2"), ("Q1-2024", "Q2-2025")])
def test_query_sales_quarter_formats_return_same_real_data(start, end):
    with patch("src.tools.sales.maybe_inject", return_value=None):
        expected = query_sales.invoke({
            "brand": "Brand A", "region": "Southeast",
            "quarter_start": "2024-Q1", "quarter_end": "2025-Q2",
        })
        actual = query_sales.invoke({
            "brand": "Brand A", "region": "Southeast",
            "quarter_start": start, "quarter_end": end,
        })
    assert actual == expected
    assert actual["row_count"] > 0
    assert actual["total_units"] > 0


@pytest.mark.parametrize("start,end", [("Q5-2024", None), ("2025-Q2", "2024-Q1")])
def test_query_sales_rejects_invalid_quarters_instead_of_empty_success(start, end):
    with patch("src.tools.sales.maybe_inject", return_value=None):
        result = query_sales.invoke({"quarter_start": start, "quarter_end": end})
    assert result["status"] == "error"
    assert result["tool.outcome"] == "error"
    assert result["error"]


@pytest.mark.parametrize("tool", [query_sales, query_inventory])
def test_product_name_returns_actionable_sku_error(tool):
    with patch("src.tools.sales.maybe_inject", return_value=None):
        result = tool.invoke({"sku": "Brand A Classic", "region": "Southeast"})
    assert result["status"] == "error"
    assert "BRA-CLS-LRG" in result["valid_skus"]
    assert "BRA-CLS-SML" in result["valid_skus"]


def test_query_sales_injected_timeout():
    with patch("src.tools.sales.maybe_inject", return_value=InjectedFault.SALES_API_TIMEOUT):
        with patch("src.tools.sales.time.sleep"):
            result = query_sales.invoke({"brand": "Brand A"})
    assert result["status"] == "error"
    assert result["tool.outcome"] == "timeout"


def test_query_sales_injected_tool_error():
    with patch("src.tools.sales.maybe_inject", return_value=InjectedFault.TOOL_ERROR):
        result = query_sales.invoke({"brand": "Brand A"})
    assert result["status"] == "error"
    assert result["tool.outcome"] == "error"


# --- query_inventory ---


def test_query_inventory_happy_path():
    with patch("src.tools.sales.maybe_inject", return_value=None):
        result = query_inventory.invoke({"sku": "BRA-CLS-LRG", "region": "Southeast"})
    assert result["status"] == "ok"
    assert result["row_count"] > 0


def test_query_inventory_injected_error():
    with patch("src.tools.sales.maybe_inject", return_value=InjectedFault.TOOL_ERROR):
        result = query_inventory.invoke({"sku": "BRA-CLS-LRG"})
    assert result["status"] == "error"


# --- get_market_trends ---


def test_get_market_trends_canned():
    with patch("src.tools.market.maybe_inject", return_value=None):
        result = get_market_trends.invoke({"brand": "Brand A", "region": "Southeast"})
    assert result["status"] == "ok"
    assert result["source"] == "canned"
    assert len(result["trends"]) > 0


def test_get_market_trends_injected_error():
    with patch("src.tools.market.maybe_inject", return_value=InjectedFault.TOOL_ERROR):
        result = get_market_trends.invoke({"brand": "Brand A"})
    assert result["status"] == "error"


# --- check_brand_guidelines ---


def test_check_brand_guidelines_children_violation():
    with patch("src.tools.compliance.maybe_inject", return_value=None):
        brief = "Promote Brand A snacks targeting families with children under 12 at school events."
        result = check_brand_guidelines.invoke({"brief": brief})
    assert result["status"] == "ok"
    assert result["has_high_severity"] is True
    high_findings = [f for f in result["findings"] if f["severity"] == "HIGH"]
    assert len(high_findings) >= 1


def test_check_brand_guidelines_clean_brief():
    with patch("src.tools.compliance.maybe_inject", return_value=None):
        brief = "Run a Q3 price promotion for Brand A Classic in the Southeast targeting adults."
        result = check_brand_guidelines.invoke({"brief": brief})
    assert result["status"] == "ok"
    high_findings = [f for f in result["findings"] if f["severity"] == "HIGH"]
    assert len(high_findings) == 0


def test_check_brand_guidelines_injected_error():
    with patch("src.tools.compliance.maybe_inject", return_value=InjectedFault.TOOL_ERROR):
        result = check_brand_guidelines.invoke({"brief": "any brief"})
    assert result["status"] == "error"


# --- check_regulatory ---


def test_check_regulatory_children_violation():
    with patch("src.tools.compliance.maybe_inject", return_value=None):
        brief = "Digital campaign targeting kids under 12 for Brand B beverages."
        result = check_regulatory.invoke({"brief": brief, "jurisdictions": ["Federal Trade Authority"]})
    assert result["status"] == "ok"
    assert result["has_high_severity"] is True


def test_check_regulatory_clean_brief():
    with patch("src.tools.compliance.maybe_inject", return_value=None):
        brief = "Q3 price reduction for Brand A Classic in Southeast for adult shoppers."
        result = check_regulatory.invoke({"brief": brief})
    assert result["status"] == "ok"
    high_findings = [f for f in result["findings"] if f["severity"] == "HIGH"]
    assert len(high_findings) == 0


def test_check_regulatory_injected_error():
    with patch("src.tools.compliance.maybe_inject", return_value=InjectedFault.TOOL_ERROR):
        result = check_regulatory.invoke({"brief": "any brief"})
    assert result["status"] == "error"


# --- error_injection ---


def test_maybe_inject_returns_none_or_fault():
    result = maybe_inject("test_tool")
    assert result is None or isinstance(result, InjectedFault)
