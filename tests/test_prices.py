from pathlib import Path

import pytest

from finance import prices

FIXTURES = Path(__file__).parent / "fixtures"


def recorded(name):
    """A `get` that answers every request with one recorded payload, and remembers what was asked."""
    asked = []

    def get(url, user_agent):
        asked.append(url)
        return (FIXTURES / name).read_bytes()
    get.asked = asked
    return get


def test_fund_prices_come_from_yahoos_daily_closes_dated_in_new_york_and_skip_todays_unpublished_nav():
    get = recorded("yahoo_chart_FSPSX.json")  # recorded Fri 2026-10-30, before that day's NAV was out
    assert prices.fetch("FSPSX", "2026-10-27", get) == [
        ("2026-10-27", 17.88), ("2026-10-28", 17.93), ("2026-10-29", 18.02)]
    assert "/chart/FSPSX?" in get.asked[0]


def test_tsp_prices_come_from_the_tsp_gov_history_column_for_the_fund_oldest_first():
    get = recorded("tsp_fund_price_history.csv")
    assert prices.fetch("TSP:C", "2026-10-27", get) == [
        ("2026-10-27", 124.6710), ("2026-10-28", 125.0167), ("2026-10-29", 124.6356)]
    assert prices.fetch("TSP:L 2050", "2026-10-29", get) == [("2026-10-29", 46.5631)]
    assert get.asked[0].startswith("https://www.tsp.gov/")


def test_a_fund_the_source_does_not_list_is_an_error_not_an_empty_history():
    with pytest.raises(ValueError, match="X Fund"):
        prices.fetch("TSP:X", "2026-10-27", recorded("tsp_fund_price_history.csv"))
