import os
from functools import lru_cache
from typing import Any
from uuid import UUID

import jwt
from fastapi import Header, HTTPException
from jwt import PyJWKClient, PyJWKClientError


AUTH_URL = os.getenv(
    "AUTH_URL",
    "https://xmrlazemifmepyahqsrz.supabase.co/auth/v1",
).rstrip("/")
JWKS_URL = os.getenv("JWKS_URL", f"{AUTH_URL}/.well-known/jwks.json")
JWT_ISSUER = os.getenv("JWT_ISSUER", AUTH_URL)
JWT_AUDIENCE = os.getenv("JWT_AUDIENCE", "authenticated")


@lru_cache(maxsize=1)
def _jwks_client() -> PyJWKClient:
    return PyJWKClient(JWKS_URL, timeout=5, cache_keys=True)


def verificar_token(authorization: str = Header(...)) -> dict[str, Any]:
    scheme, separator, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not separator or not token.strip():
        raise HTTPException(status_code=401, detail="Se requiere un token Bearer válido.")

    token = token.strip()
    if token == "dev-token" and os.getenv("ALLOW_DEV_TOKEN", "").lower() == "true":
        dev_user_id = os.getenv("DEV_USER_ID")
        try:
            subject = str(UUID(dev_user_id)) if dev_user_id else ""
        except ValueError:
            subject = ""
        if not subject:
            raise HTTPException(
                status_code=503,
                detail="ALLOW_DEV_TOKEN requiere un DEV_USER_ID UUID válido.",
            )
        return {
            "sub": subject,
            "app_metadata": {"role": os.getenv("DEV_USER_ROLE", "COMPRADOR")},
        }

    try:
        header = jwt.get_unverified_header(token)
        algorithm = header.get("alg")
        if algorithm == "HS256":
            secret = os.getenv("SUPABASE_AUTH_JWT_SECRET")
            if not secret:
                raise HTTPException(
                    status_code=401,
                    detail="El algoritmo del token no está habilitado para este servicio.",
                )
            key = secret
            algorithms = ["HS256"]
        elif algorithm in {"RS256", "ES256"}:
            key = _jwks_client().get_signing_key_from_jwt(token).key
            algorithms = [algorithm]
        else:
            raise HTTPException(status_code=401, detail="Algoritmo JWT no permitido.")

        claims = jwt.decode(
            token,
            key,
            algorithms=algorithms,
            audience=JWT_AUDIENCE,
            issuer=JWT_ISSUER,
            options={"require": ["exp", "sub"]},
        )
        UUID(claims["sub"])
        return claims
    except HTTPException:
        raise
    except jwt.InvalidTokenError as exc:
        raise HTTPException(
            status_code=401,
            detail="Token JWT inválido o expirado.",
        ) from exc
    except PyJWKClientError as exc:
        raise HTTPException(
            status_code=503,
            detail="No fue posible consultar las claves del servicio central de autenticación.",
        ) from exc
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=401, detail="Token JWT inválido.") from exc


def usuario_id(user: dict[str, Any]) -> str:
    try:
        return str(UUID(user["sub"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=401, detail="El token no contiene un usuario válido.") from exc


def _claim_role(user: dict[str, Any], metadata_name: str) -> str:
    metadata = user.get(metadata_name)
    if not isinstance(metadata, dict):
        return ""
    return str(metadata.get("role") or "").strip().upper()


def rol_usuario(user: dict[str, Any]) -> str:
    app_role = _claim_role(user, "app_metadata")
    user_role = _claim_role(user, "user_metadata")
    return app_role or user_role or str(user.get("role") or "").strip().upper()


def es_admin(user: dict[str, Any]) -> bool:
    return _claim_role(user, "app_metadata") in {
        "ADMIN",
        "ADMINISTRADOR",
        "ADMINISTRATOR",
    }


def requiere_admin(user: dict[str, Any]) -> None:
    if not es_admin(user):
        raise HTTPException(status_code=403, detail="Se requiere el rol de administrador.")


def requiere_vendedor(user: dict[str, Any], vendedor_id: str | None = None) -> str:
    role = rol_usuario(user)
    if role not in {"VENDEDOR", "SELLER", "ADMIN", "ADMINISTRADOR", "ADMINISTRATOR"}:
        raise HTTPException(status_code=403, detail="Se requiere el rol de vendedor.")
    subject = usuario_id(user)
    if vendedor_id and not es_admin(user) and subject != vendedor_id:
        raise HTTPException(status_code=403, detail="No puede modificar productos de otro vendedor.")
    return subject
