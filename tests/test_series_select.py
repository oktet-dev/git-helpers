from gg.series_select import LAST, series_spec


def test_series_spec_values() -> None:
    assert series_spec("3,4") == [(3, None), (4, None)]
    assert series_spec("$:1-3") == [(LAST, [(1, 3)])]
    assert series_spec("2:2-$") == [(2, [(2, LAST)])]
