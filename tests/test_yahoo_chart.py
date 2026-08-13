from data.yahoo_chart import parse_chart_json


def test_parse_yahoo_chart_skips_null_bars():
    payload = {
        "chart": {
            "result": [
                {
                    "timestamp": [1600000000, 1600003600],
                    "indicators": {
                        "quote": [
                            {
                                "open": [10.0, None],
                                "high": [11.0, 12.0],
                                "low": [9.0, 10.0],
                                "close": [10.5, 11.0],
                                "volume": [1, 2],
                            }
                        ]
                    },
                }
            ]
        }
    }
    df = parse_chart_json(payload)
    assert len(df) == 1
    assert df.iloc[0]["open"] == 10.0
