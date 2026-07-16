import uuid
from typing import Final, Literal

PublicIdPrefix = Literal[
    "org",
    "usr",
    "cus",
    "pay",
    "auth",
    "cap",
    "ref",
    "op",
    "jrn",
    "evt",
    "wh",
    "whv",
    "del",
    "scn",
]

_PUBLIC_ID_SEPARATOR: Final = "_"


def new_uuid() -> uuid.UUID:
    return uuid.uuid7() if hasattr(uuid, "uuid7") else uuid.uuid4()


def new_public_id(prefix: PublicIdPrefix) -> str:
    return f"{prefix}{_PUBLIC_ID_SEPARATOR}{new_uuid().hex}"


def parse_public_id(value: str, prefix: PublicIdPrefix) -> str:
    expected = f"{prefix}{_PUBLIC_ID_SEPARATOR}"
    if not value.startswith(expected) or len(value) != len(expected) + 32:
        raise ValueError(f"expected a canonical {prefix} public identifier")
    uuid.UUID(hex=value[len(expected) :])
    return value
