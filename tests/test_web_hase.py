"""Tests fuer die Hase-Steuerseite und die /docs/-Route in monitoring/web.py."""

import pytest

from eule.monitoring import web


# ---------------------------------------------------------------------------
# Action-Validierung
# ---------------------------------------------------------------------------


class TestValidateHaseAction:
    def test_valid_pause(self):
        form = {"env": "staging-ibkr", "strategy": "carver-scalping", "action": "pause"}
        assert web.validate_hase_action(form) is None

    def test_unknown_env(self):
        form = {"env": "prod-hl", "strategy": "carver-scalping", "action": "pause"}
        assert "Environment" in web.validate_hase_action(form)

    def test_unknown_action(self):
        form = {"env": "staging-ibkr", "strategy": "carver-scalping", "action": "restart"}
        assert "Aktion" in web.validate_hase_action(form)

    def test_action_case_sensitive(self):
        form = {"env": "staging-ibkr", "strategy": "carver-scalping", "action": "KILL"}
        assert web.validate_hase_action(form) is not None

    @pytest.mark.parametrize("name", ["", "../etc/passwd", "a/b", "foo bar", "x" * 65])
    def test_bad_strategy_names(self, name):
        form = {"env": "staging-ibkr", "strategy": name, "action": "pause"}
        assert "Strategie-Name" in web.validate_hase_action(form)

    def test_combos_requires_numeric_value(self):
        form = {"env": "staging-ibkr", "strategy": "spx-0dte-mon-put",
                "action": "combos", "value": "drei"}
        assert "num_combos" in web.validate_hase_action(form)

    def test_combos_rejects_negative(self):
        form = {"env": "staging-ibkr", "strategy": "spx-0dte-mon-put",
                "action": "combos", "value": "-1"}
        assert web.validate_hase_action(form) is not None

    def test_combos_accepts_int(self):
        form = {"env": "staging-ibkr", "strategy": "spx-0dte-mon-put",
                "action": "combos", "value": "3"}
        assert web.validate_hase_action(form) is None

    def test_all_actions_are_known(self):
        assert set(web.HASE_ACTIONS) == {
            "pause", "resume", "disable", "combos", "flatten", "kill",
        }


# ---------------------------------------------------------------------------
# Docs-Pfad-Sicherheit
# ---------------------------------------------------------------------------


@pytest.fixture
def docs_site(tmp_path):
    base = tmp_path / "docs-site"
    (base / "sub").mkdir(parents=True)
    (base / "index.html").write_text("<h1>root</h1>")
    (base / "sub" / "index.html").write_text("<h1>sub</h1>")
    (base / "sub" / "page.html").write_text("<h1>page</h1>")
    (tmp_path / "secret.txt").write_text("geheim")
    return base


class TestResolveDocsPath:
    def test_root_serves_index(self, docs_site):
        assert web.resolve_docs_path("", docs_site) == docs_site / "index.html"

    def test_slash_serves_index(self, docs_site):
        assert web.resolve_docs_path("/", docs_site) == docs_site / "index.html"

    def test_directory_serves_index(self, docs_site):
        assert web.resolve_docs_path("/sub/", docs_site) == docs_site / "sub" / "index.html"

    def test_file(self, docs_site):
        assert web.resolve_docs_path("/sub/page.html", docs_site) == docs_site / "sub" / "page.html"

    def test_missing_file(self, docs_site):
        assert web.resolve_docs_path("/nope.html", docs_site) is None

    @pytest.mark.parametrize("rel", [
        "/../secret.txt",
        "/sub/../../secret.txt",
        "/%2e%2e/secret.txt",
        "/../../../../etc/passwd",
    ])
    def test_traversal_blocked(self, rel, docs_site):
        assert web.resolve_docs_path(rel, docs_site) is None

    def test_absolute_path_stays_inside(self, docs_site):
        assert web.resolve_docs_path("//etc/passwd", docs_site) is None

    def test_symlink_out_of_base_blocked(self, docs_site, tmp_path):
        (docs_site / "escape.txt").symlink_to(tmp_path / "secret.txt")
        assert web.resolve_docs_path("/escape.txt", docs_site) is None

    def test_missing_base_dir(self, tmp_path):
        assert web.resolve_docs_path("/index.html", tmp_path / "gibt-es-nicht") is None

    def test_docs_dir_from_env(self, monkeypatch, tmp_path):
        monkeypatch.setenv("EULE_DOCS_DIR", str(tmp_path))
        assert web.docs_dir() == tmp_path

    def test_docs_dir_default(self, monkeypatch):
        monkeypatch.delenv("EULE_DOCS_DIR", raising=False)
        assert str(web.docs_dir()) == web.DEFAULT_DOCS_DIR


# ---------------------------------------------------------------------------
# Aktionen (mit gemockten API-Calls)
# ---------------------------------------------------------------------------


@pytest.fixture
def posts(monkeypatch):
    """Sammelt alle POST-Calls; liefert per Default 200 + leeres Dict."""
    calls = []
    responses = {}

    def fake_post(port, endpoint, body=None):
        calls.append({"port": port, "endpoint": endpoint, "body": body})
        return responses.get(endpoint, (200, {"strategy": "x"}))

    monkeypatch.setattr(web, "_hase_post", fake_post)
    return {"calls": calls, "responses": responses}


class TestHandleHaseAction:
    def test_pause_proxies_to_control_api(self, posts):
        html = web.handle_hase_action(
            {"env": "staging-ibkr", "strategy": "carver-scalping", "action": "pause"}
        )
        assert posts["calls"] == [
            {"port": 8776, "endpoint": "/strategy/carver-scalping/pause", "body": None}
        ]
        assert "OK" in html

    def test_invalid_env_does_not_call_api(self, posts):
        html = web.handle_hase_action(
            {"env": "boese", "strategy": "carver-scalping", "action": "pause"}
        )
        assert posts["calls"] == []
        assert "Environment" in html

    def test_combos_posts_params(self, posts):
        web.handle_hase_action({
            "env": "real-ibkr", "strategy": "spx-0dte-mon-put",
            "action": "combos", "value": "3",
        })
        assert posts["calls"] == [{
            "port": 8767,
            "endpoint": "/strategy/spx-0dte-mon-put/params",
            "body": {"num_combos": 3},
        }]

    def test_combos_conflict_is_rendered(self, posts):
        posts["responses"]["/strategy/spx-0dte-mon-put/params"] = (
            409, {"detail": "Strategy has open position — params locked until flat"}
        )
        html = web.handle_hase_action({
            "env": "real-ibkr", "strategy": "spx-0dte-mon-put",
            "action": "combos", "value": "3",
        })
        assert "409" in html
        assert "open position" in html

    def test_api_unreachable_is_rendered(self, posts):
        posts["responses"]["/strategy/carver-scalping/pause"] = (None, "connection refused")
        html = web.handle_hase_action(
            {"env": "staging-ibkr", "strategy": "carver-scalping", "action": "pause"}
        )
        assert "nicht erreichbar" in html

    def test_flatten_without_confirm_is_dry_run(self, posts):
        posts["responses"]["/strategy/carver-scalping/flatten?dry_run=true"] = (200, {
            "strategy": "carver-scalping",
            "op": "flatten",
            "positions_to_close": [{"key": "MCL", "broker_id": "1", "size": 2.0, "side": "SELL"}],
            "pending_to_cancel": [],
            "dry_run": True,
        })
        html = web.handle_hase_action(
            {"env": "staging-ibkr", "strategy": "carver-scalping", "action": "flatten"}
        )
        assert [c["endpoint"] for c in posts["calls"]] == [
            "/strategy/carver-scalping/flatten?dry_run=true"
        ]
        assert "Vorschau" in html
        assert "MCL" in html
        assert 'name="confirm" value="yes"' in html

    def test_flatten_with_confirm_executes(self, posts):
        web.handle_hase_action({
            "env": "staging-ibkr", "strategy": "carver-scalping",
            "action": "flatten", "confirm": "yes",
        })
        assert [c["endpoint"] for c in posts["calls"]] == ["/strategy/carver-scalping/flatten"]

    def test_kill_dry_run_shows_nested_flatten(self, posts):
        posts["responses"]["/strategy/spx-0dte-mon-put/kill?dry_run=true"] = (200, {
            "strategy": "spx-0dte-mon-put",
            "op": "kill",
            "flatten": {
                "positions_to_close": [],
                "pending_to_cancel": [{"order_id": "spx_42", "side": "BUY", "size": 1.0}],
            },
            "dry_run": True,
        })
        html = web.handle_hase_action(
            {"env": "real2-ibkr", "strategy": "spx-0dte-mon-put", "action": "kill"}
        )
        assert "spx_42" in html
        assert "disabled" in html

    def test_disable_is_not_two_stage(self, posts):
        web.handle_hase_action(
            {"env": "staging-hl", "strategy": "crypto-bb-short-mr", "action": "disable"}
        )
        assert [c["endpoint"] for c in posts["calls"]] == [
            "/strategy/crypto-bb-short-mr/disable"
        ]


# ---------------------------------------------------------------------------
# Render-Smoke
# ---------------------------------------------------------------------------


_STRATEGIES = [{
    "index": 0,
    "class": "Spx0dte",
    "name": "spx-0dte-mon-put",
    "params": {"num_combos": 2},
    "display": {
        "status_message": "Warte auf Entry-Zeit 11:30",
        "fsm_state": "WAITING",
        "next_action_time": "2026-07-30T11:30:00-04:00",
    },
    "stats": {"realized_pnl": 120.5, "unrealized_pnl": -30.0, "trades_count": 1},
    "health": {"health": "WARN", "problems": ["orders_rejected: 1"]},
    "worker": {"alive": True},
}]

_PORTFOLIO = {
    "cash": {"current_cash": 10500.0, "currency": "USD"},
    "pnl": {"daily_realized_pnl": 120.5, "daily_unrealized_pnl": -30.0},
    "positions": [{
        "broker_id": "111", "key": "SPX_5000P", "strategy_key": "spx-0dte-mon-put",
        "product": {"type": "Option", "instr": "SPX", "strike": 5000.0, "option_type": "PUT"},
        "curr_count": -2.0, "curr_price": 1.5, "daily_pnl": -30.0,
    }],
    "positions_count": 1,
}

_PENDING = {"haendler_pending": [{
    "order_id": "spx_42", "instr_key": "SPX_5000P", "side": "BUY", "size": 2.0,
    "strategy_key": "spx-0dte-mon-put", "broker_state": "SUBMITTED",
}]}

_PARAMS = {
    "strategy": "spx-0dte-mon-put",
    "params": {"num_combos": 2},
    "mutable": {"num_combos": {"type": "int", "min": 1, "max": 10}},
}


@pytest.fixture
def fake_api(monkeypatch):
    """Nur real-ibkr antwortet; die anderen Envs sind 'gestoppt'."""
    def fake_get(port, endpoint):
        if port != 8767:
            return None
        if endpoint == "/strategies":
            return _STRATEGIES
        if endpoint == "/portfolio":
            return _PORTFOLIO
        if endpoint == "/debug/orders/pending":
            return _PENDING
        if endpoint.endswith("/params"):
            return _PARAMS
        return None

    monkeypatch.setattr(web, "_hase_get", fake_get)


class TestPageHase:
    def test_renders_live_state(self, fake_api):
        html = web._page_hase()
        assert "spx-0dte-mon-put" in html
        assert "Warte auf Entry-Zeit 11:30" in html
        assert "WAITING" in html
        assert "orders_rejected: 1" in html
        assert "SPX_5000P" in html
        assert "spx_42" in html

    def test_production_env_is_marked(self, fake_api):
        html = web._page_hase()
        assert "Echtgeld" in html
        assert "env-prod" in html

    def test_unreachable_env_is_not_an_error(self, fake_api):
        html = web._page_hase()
        assert "API nicht erreichbar" in html
        assert "staging-ibkr" in html

    def test_combos_buttons_use_bounds(self, fake_api):
        html = web._page_hase()
        assert "num_combos: 2 (1–10)" in html
        assert 'name="value" value="3"' in html
        assert 'name="value" value="1"' in html

    def test_actions_use_post_only(self, fake_api):
        html = web._page_hase()
        assert 'method="post" action="/hase/action"' in html
        assert "/hase/action?" not in html

    def test_all_envs_rendered(self, fake_api):
        html = web._page_hase()
        for env in web._hase_environments():
            assert env in html
