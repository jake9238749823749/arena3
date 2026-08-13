from futures.contracts import InstrumentRegistry, contract_code, parse_contract_code, third_friday

from tests.helpers import es_mes_registry


def test_tick_value_identity():
    reg = es_mes_registry()
    for spec in reg.specs.values():
        assert abs(spec.tick_value - spec.multiplier * spec.tick_size) < 1e-12


def test_yaml_registry_tick_identity():
    from research.configutil import repo_root

    reg = InstrumentRegistry.from_yaml(repo_root() / "config" / "instruments.yaml")
    for spec in reg.specs.values():
        assert abs(spec.tick_value - spec.multiplier * spec.tick_size) < 1e-12


def test_contract_codes_round_trip():
    code = contract_code("MES", 2021, 3)
    assert code == "MESH21"
    root, year, month = parse_contract_code(code)
    assert (root, year, month) == ("MES", 2021, 3)


def test_third_friday():
    assert third_friday(2021, 3).isoformat() == "2021-03-19"
    assert third_friday(2021, 6).isoformat() == "2021-06-18"


def test_snap():
    spec = es_mes_registry()["MES"]
    assert spec.snap(4000.12) == 4000.00
    assert spec.snap(4000.13) == 4000.25
