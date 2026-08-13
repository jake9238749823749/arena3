from research.report import conclude_from_suite


def _summary(dataset, exp, n=40, holdout_exp=-1.0, cost2=-1.0):
    return {
        "dataset": dataset,
        "cases": [
            {
                "name": "baseline",
                "expectancy": exp,
                "n_trades": n,
                "concentration_top5_of_wins": 0.2,
                "ambiguous_fraction": 0.0,
            }
        ],
        "cost_curve": [
            {"name": "cost_2", "expectancy": cost2},
            {"name": "cost_3", "expectancy": cost2},
        ],
        "grids": {},
        "walkforward": [],
        "holdout": {"n_trades": 10, "expectancy": holdout_exp},
    }


def test_random_walk_negative_is_expected_falsification():
    c = conclude_from_suite(_summary("random_walk", -10.0))
    assert c["verdict"] == "FALSIFIED_ON_RANDOM_WALK"


def test_random_walk_positive_is_leakage_alarm():
    c = conclude_from_suite(_summary("random_walk", 10.0, holdout_exp=5.0, cost2=5.0))
    assert c["verdict"] == "LEAKAGE_SUSPECTED"


def test_planted_positive_is_but_bad_holdout():
    c = conclude_from_suite(_summary("planted_frs", 20.0, holdout_exp=-5.0, cost2=10.0))
    assert c["verdict"] == "DETECTS_PLANT_NOT_SELECTIVE"


def test_planted_window_positive_overall_negative():
    summary = _summary("planted_frs", -10.0, holdout_exp=-5.0, cost2=-12.0)
    summary["cases"][0]["segments"] = {
        "entry_0930_1100": {"n": 80, "expectancy": 40.0, "net": 3200.0},
    }
    c = conclude_from_suite(summary)
    assert c["verdict"] == "DETECTS_PLANT_NOT_SELECTIVE"
