from datetime import datetime, timezone, timedelta
from jose import JWTError, jwt
from passlib.context import CryptContext

from app.core.config import get_settings

_settings = get_settings()

_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    return _pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return _pwd_context.verify(plain, hashed)


def create_access_token(user_id: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(days=_settings.access_token_expire_days)
    payload = {"sub": user_id, "exp": expire}
    return jwt.encode(payload, _settings.jwt_secret, algorithm=_settings.jwt_algorithm)


def decode_access_token(token: str) -> str | None:
    try:
        payload = jwt.decode(
            token,
            _settings.jwt_secret,
            algorithms=[_settings.jwt_algorithm],
        )
        return payload.get("sub")
    except JWTError:
        return None
