from __future__ import annotations

from decimal import Decimal

from spenda.db import database, initialize
from spenda.models import TokenUsage
from spenda.pricing import add_price, calculate_cost, seed_prices


def priced_conn(tmp_path):
    path = tmp_path / "price.sqlite"
    initialize(path)
    conn = database(path)
    return path


def test_cached_and_reasoning_semantics():
    usage = TokenUsage(1000, 600, 300, 200, 150, 1200)
    assert usage.uncached_input_tokens == 100
    assert usage.total_tokens == 1200
    assert usage.reasoning_output_tokens <= usage.output_tokens


def test_one_root_one_model_cost(tmp_path):
    path = tmp_path / "db.sqlite"
    initialize(path)
    with database(path) as conn:
        seed_prices(conn)
        usage = TokenUsage(100_000, 50_000, 40_000, 10_000, 5_000, 110_000)
        cost = calculate_cost(conn, usage, "gpt-5.6-sol", "openai", "2026-09-08T10:00:00Z")
    # 10k*4 + 50k*.4 + 40k*5 + 10k*20, all per million.
    assert cost.total_usd == Decimal("0.46")


def test_reasoning_is_not_charged_twice(tmp_path):
    path = tmp_path / "db.sqlite"; initialize(path)
    with database(path) as conn:
        seed_prices(conn)
        a = calculate_cost(conn, TokenUsage(0, 0, 0, 1000, 900, 1000), "gpt-5.6-luna", "openai", "2026-09-08T10:00:00Z")
        b = calculate_cost(conn, TokenUsage(0, 0, 0, 1000, 0, 1000), "gpt-5.6-luna", "openai", "2026-09-08T10:00:00Z")
    assert a.total_usd == b.total_usd == Decimal("0.0012")


def test_long_context_multiplier(tmp_path):
    path = tmp_path / "db.sqlite"; initialize(path)
    with database(path) as conn:
        seed_prices(conn)
        cost = calculate_cost(conn, TokenUsage(272001, 0, 0, 1000, 0, 273001), "gpt-5.6-sol", "openai", "2026-09-08T10:00:00Z")
    assert cost.uncached_input_usd == Decimal(272001) * Decimal(8) / Decimal(1_000_000)
    assert cost.output_usd == Decimal("0.03")


def test_unknown_model_and_provider(tmp_path):
    path = tmp_path / "db.sqlite"; initialize(path)
    with database(path) as conn:
        seed_prices(conn)
        unknown = calculate_cost(conn, TokenUsage(input_tokens=10), "future-model", "openai", "2026-09-08T10:00:00Z")
        azure = calculate_cost(conn, TokenUsage(input_tokens=10), "gpt-5.6-sol", "azure", "2026-09-08T10:00:00Z")
    assert unknown.total_usd is None and azure.total_usd is None


def test_explicit_alias(tmp_path):
    path = tmp_path / "db.sqlite"; initialize(path)
    with database(path) as conn:
        seed_prices(conn)
        cost = calculate_cost(conn, TokenUsage(input_tokens=1000), "gpt-5.6", "openai", "2026-09-08T10:00:00Z")
    assert cost.total_usd is not None


def test_historical_price_change(tmp_path):
    path = tmp_path / "db.sqlite"; initialize(path)
    with database(path) as conn:
        add_price(conn, model="test", effective_from="2026-01-01T00:00:00Z", input_per_million="1",
                  cached_input_per_million="1", cache_write_per_million="1", output_per_million="1", source="test")
        add_price(conn, model="test", effective_from="2026-06-01T00:00:00Z", input_per_million="2",
                  cached_input_per_million="2", cache_write_per_million="2", output_per_million="2", source="test")
        old = calculate_cost(conn, TokenUsage(input_tokens=1_000_000), "test", "openai", "2026-03-01T00:00:00Z")
        new = calculate_cost(conn, TokenUsage(input_tokens=1_000_000), "test", "openai", "2026-07-01T00:00:00Z")
    assert old.total_usd == Decimal("1")
    assert new.total_usd == Decimal("2")
