import time

import jwt as _jwt


def create_jwt(secret: str, validity_seconds: int = 3600) -> str:
    now = int(time.time())
    return _jwt.encode(
        {"iat": now, "exp": now + validity_seconds},
        secret,
        algorithm="HS256",
    )


def validate_jwt(token: str, secret: str) -> bool:
    try:
        _jwt.decode(token, secret, algorithms=["HS256"])
        return True
    except _jwt.InvalidTokenError:
        return False
