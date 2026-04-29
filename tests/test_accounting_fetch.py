"""Tests fuer eule.accounting.fetch — IBKR Flex Web Service Client."""

from unittest.mock import MagicMock

import pytest

from eule.accounting.fetch import (
    FLEX_BASE,
    FLEX_VERSION,
    FlexError,
    fetch_statement,
    request_statement,
)


def _http_response(text: str) -> MagicMock:
    resp = MagicMock()
    resp.text = text
    resp.raise_for_status = MagicMock()
    return resp


# ── SendRequest ──────────────────────────────────────────


def test_request_statement_success_returns_reference_code():
    client = MagicMock()
    client.get.return_value = _http_response(
        '<?xml version="1.0"?><FlexStatementResponse>'
        "<Status>Success</Status>"
        "<ReferenceCode>1234567890</ReferenceCode>"
        "<Url>https://example.com</Url>"
        "</FlexStatementResponse>"
    )

    ref = request_statement("TOKEN", "QID", client=client)

    assert ref == "1234567890"
    args, kwargs = client.get.call_args
    assert args[0] == f"{FLEX_BASE}.SendRequest"
    assert kwargs["params"] == {"t": "TOKEN", "q": "QID", "v": FLEX_VERSION}


def test_request_statement_fail_raises_flex_error():
    client = MagicMock()
    client.get.return_value = _http_response(
        "<FlexStatementResponse>"
        "<Status>Fail</Status>"
        "<ErrorCode>1012</ErrorCode>"
        "<ErrorMessage>Token has expired.</ErrorMessage>"
        "</FlexStatementResponse>"
    )

    with pytest.raises(FlexError, match="1012.*Token has expired"):
        request_statement("TOKEN", "QID", client=client)


def test_request_statement_missing_reference_raises():
    client = MagicMock()
    client.get.return_value = _http_response(
        "<FlexStatementResponse><Status>Success</Status></FlexStatementResponse>"
    )

    with pytest.raises(FlexError, match="Kein ReferenceCode"):
        request_statement("TOKEN", "QID", client=client)


def test_request_statement_invalid_xml_raises():
    client = MagicMock()
    client.get.return_value = _http_response("nicht xml")

    with pytest.raises(FlexError, match="kein gueltiges XML"):
        request_statement("TOKEN", "QID", client=client)


# ── GetStatement ─────────────────────────────────────────


def test_fetch_statement_success_returns_csv_directly():
    client = MagicMock()
    client.get.return_value = _http_response(
        "AssetClass,Description,Amount\nFUT,SOFR3 JUN28,433.92\n"
    )

    result = fetch_statement("TOKEN", "REF", client=client, sleep=lambda _: None)

    assert result.startswith("AssetClass")
    assert "SOFR3" in result
    args, kwargs = client.get.call_args
    assert args[0] == f"{FLEX_BASE}.GetStatement"
    assert kwargs["params"] == {"t": "TOKEN", "q": "REF", "v": FLEX_VERSION}


def test_fetch_statement_polls_until_ready():
    not_ready = _http_response(
        "<FlexStatementResponse>"
        "<Status>Warn</Status>"
        "<ErrorCode>1019</ErrorCode>"
        "<ErrorMessage>Statement generation in progress.</ErrorMessage>"
        "</FlexStatementResponse>"
    )
    ready = _http_response("AssetClass,Amount\nFUT,100\n")

    client = MagicMock()
    client.get.side_effect = [not_ready, not_ready, ready]
    sleeps: list[float] = []

    result = fetch_statement(
        "TOKEN", "REF", client=client, poll_interval=0, sleep=sleeps.append
    )

    assert result.startswith("AssetClass")
    assert client.get.call_count == 3
    assert sleeps == [0, 0]


def test_fetch_statement_timeout_raises():
    not_ready = _http_response(
        "<FlexStatementResponse>"
        "<Status>Warn</Status>"
        "<ErrorCode>1019</ErrorCode>"
        "<ErrorMessage>Statement generation in progress.</ErrorMessage>"
        "</FlexStatementResponse>"
    )

    client = MagicMock()
    client.get.return_value = not_ready

    with pytest.raises(FlexError, match="Timeout"):
        fetch_statement(
            "TOKEN",
            "REF",
            client=client,
            poll_interval=0,
            poll_timeout=-1,
            sleep=lambda _: None,
        )


def test_fetch_statement_other_error_raises_immediately():
    client = MagicMock()
    client.get.return_value = _http_response(
        "<FlexStatementResponse>"
        "<Status>Fail</Status>"
        "<ErrorCode>1003</ErrorCode>"
        "<ErrorMessage>Statement is not available.</ErrorMessage>"
        "</FlexStatementResponse>"
    )

    with pytest.raises(FlexError, match="1003.*not available"):
        fetch_statement(
            "TOKEN", "REF", client=client, poll_interval=0, sleep=lambda _: None
        )


def test_fetch_statement_csv_with_leading_whitespace():
    """CSV mit fuehrendem Whitespace darf nicht als XML interpretiert werden."""
    client = MagicMock()
    client.get.return_value = _http_response("\n\nAssetClass,Amount\nFUT,100\n")

    result = fetch_statement("TOKEN", "REF", client=client, sleep=lambda _: None)

    assert "AssetClass" in result


# ── End-to-end (env vars) ─────────────────────────────────


def test_fetch_sof_csv_requires_env_token(monkeypatch):
    from eule.accounting.fetch import fetch_sof_csv

    monkeypatch.delenv("EULE_IBKR_FLEX_TOKEN", raising=False)
    monkeypatch.setenv("EULE_IBKR_FLEX_QUERY_ID", "QID")

    with pytest.raises(FlexError, match="EULE_IBKR_FLEX_TOKEN"):
        fetch_sof_csv()


def test_fetch_sof_csv_requires_env_query_id(monkeypatch):
    from eule.accounting.fetch import fetch_sof_csv

    monkeypatch.setenv("EULE_IBKR_FLEX_TOKEN", "TOKEN")
    monkeypatch.delenv("EULE_IBKR_FLEX_QUERY_ID", raising=False)

    with pytest.raises(FlexError, match="EULE_IBKR_FLEX_QUERY_ID"):
        fetch_sof_csv()


# ── Cache-Pfade ──────────────────────────────────────────


def test_sof_current_path(tmp_path, monkeypatch):
    monkeypatch.setenv("EULE_TRADINGGBR_DIR", str(tmp_path))

    from eule.accounting.fetch import sof_current_path

    p = sof_current_path()

    assert p == tmp_path / "sof" / "sof-current.csv"
    assert p.parent.exists()
