from app.services.table_reconstruction_engine import _best_alias_match
from app.services.matching_cache import _HASH_CACHE, _HASH_CACHE_LOCK, _hashed_key, matching_cache_scope
from app.utils.fuzzy_keywords import matched_keywords


def test_fuzzy_keyword_matching_is_stable_and_reuses_repeated_comparisons():
    expected = ["quantity"]
    with matching_cache_scope() as cache:
        assert matched_keywords("Quantty 500", ("quantity", "description")) == expected
        assert matched_keywords("Quantty 500", ("quantity", "description")) == expected

        assert cache.hits >= 1
        assert cache.values

    assert cache.values == {}


def test_table_header_alias_matching_is_stable_and_reuses_repeated_comparisons():
    aliases = ("amount due", "net amount", "total")
    unique_header = "synthetic header cache probe alpha"
    expected_cache_key = _hashed_key(("table_alias", unique_header, aliases))

    with matching_cache_scope() as cache:
        expected = _best_alias_match(unique_header, aliases)
        assert _best_alias_match(unique_header, aliases) == expected

        assert expected is None

    assert cache.values == {}
    with _HASH_CACHE_LOCK:
        assert expected_cache_key in _HASH_CACHE
        assert len(_HASH_CACHE) <= 32768
        assert all(not _contains_string(key) for key in _HASH_CACHE)


def _contains_string(value):
    if isinstance(value, str):
        return True
    if isinstance(value, tuple):
        return any(_contains_string(item) for item in value)
    return False
