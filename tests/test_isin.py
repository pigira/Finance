import pytest

from app.isin import is_valid_isin, normalize_isin


@pytest.mark.parametrize("isin", ["US5949181045", "IE00B4L5Y983", "IE00BK5BQT80"])
def test_valid_isins(isin: str) -> None:
    assert is_valid_isin(isin)


@pytest.mark.parametrize(
    "isin",
    [
        "IE00B4L5Y984",  # wrong check digit
        "US5949181046",  # wrong check digit
        "IE00B4L5Y98",  # 11 characters
        "IE00B4L5Y9833",  # 13 characters
        "",
        "1E00B4L5Y983",  # country prefix must be two letters
        "IE00B4L5Y98X",  # check digit must be a digit
        "ie00b4l5y983",  # validation expects the normalized (upper-case) form
        "IE00-4L5Y983",
    ],
)
def test_invalid_isins(isin: str) -> None:
    assert not is_valid_isin(isin)


def test_normalize_isin_strips_spaces_and_upper_cases() -> None:
    assert normalize_isin(" ie00 b4l5 y983 ") == "IE00B4L5Y983"
    assert is_valid_isin(normalize_isin(" ie00 b4l5 y983 "))
