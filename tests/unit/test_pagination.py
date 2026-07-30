from datetime import UTC, datetime

import pytest
from relaypay.errors import RelayPayError
from relaypay.pagination import CursorPosition, decode_cursor, encode_cursor


def test_cursor_round_trip_is_opaque_url_safe_and_filter_bound() -> None:
    position = CursorPosition(datetime(2026, 7, 30, 12, 0, tzinfo=UTC), "item-42")
    filters: dict[str, object] = {"status": "OPEN"}

    token = encode_cursor(position, filters=filters, secret="cursor-secret")

    assert "+" not in token
    assert "/" not in token
    assert decode_cursor(token, filters=filters, secret="cursor-secret") == position

    with pytest.raises(RelayPayError) as incompatible:
        decode_cursor(token, filters={"status": "RESOLVED"}, secret="cursor-secret")
    assert incompatible.value.code == "INVALID_CURSOR"
    assert incompatible.value.http_status == 400


@pytest.mark.parametrize("token", ["", "not-a-cursor", "a.b", "a.b.c"])
def test_invalid_cursor_is_a_stable_api_error(token: str) -> None:
    with pytest.raises(RelayPayError) as invalid:
        decode_cursor(token, filters={}, secret="cursor-secret")
    assert invalid.value.code == "INVALID_CURSOR"
