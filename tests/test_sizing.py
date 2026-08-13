from strategy.sizing import size_position
from tests.helpers import es_mes_registry


def test_percent_equity_risk_and_notional_cap():
    spec = es_mes_registry()["MES"]
    # 25 bps of 100k = 250. stop 2 points * $5 = $10 / contract → 25 contracts
    # notional cap 1x: 25 * 4000 * 5 = 500k > 100k → cap at 5
    d = size_position(
        method="percent_equity_risk",
        equity=100_000,
        price=4000,
        stop_distance=2.0,
        spec=spec,
        risk_fraction=0.0025,
        notional_cap=1.0,
    )
    assert d.quantity == 5
    assert d.reason == "notional_capped"


def test_zero_stop_refuses_to_size():
    spec = es_mes_registry()["MES"]
    d = size_position(
        method="percent_equity_risk",
        equity=100_000,
        price=4000,
        stop_distance=0.0,
        spec=spec,
    )
    assert d.quantity == 0
    assert d.reason == "zero_stop"


def test_fixed_contracts():
    spec = es_mes_registry()["MES"]
    d = size_position(
        method="fixed_contracts",
        equity=100_000,
        price=4000,
        stop_distance=2.0,
        spec=spec,
        fixed_contracts=3,
        notional_cap=10.0,
    )
    assert d.quantity == 3


def test_sizing_not_embedded_in_signal():
    """Signals do not import sizing — architectural guard."""
    import strategy.signals as sig

    source = open(sig.__file__, encoding="utf-8").read()
    assert "size_position" not in source
    assert "risk_fraction" not in source
