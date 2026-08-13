from tests.helpers import compressed_breakout_bars, make_engine


def _fingerprint(result):
    fills = [(f.ts.isoformat(), f.order_id, f.price, f.quantity, f.side, f.reason) for f in result.fills]
    trades = [(t.entry_ts.isoformat(), t.exit_ts.isoformat(), t.pnl, t.quantity) for t in result.trades]
    equity = [(p.ts.isoformat(), round(p.equity, 8)) for p in result.equity]
    return fills, trades, equity


def test_identical_inputs_identical_outputs():
    bars = compressed_breakout_bars(after=8)
    a = make_engine().run(bars)
    b = make_engine().run(bars)
    assert _fingerprint(a) == _fingerprint(b)


def test_replay_after_shuffle_of_input_list():
    bars = compressed_breakout_bars(after=8)
    reversed_input = list(reversed(bars))
    a = make_engine().run(bars)
    b = make_engine().run(reversed_input)
    assert _fingerprint(a) == _fingerprint(b)
