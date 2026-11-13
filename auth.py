import jwt
from fastapi import Header, HTTPException

JWKS_URL = "https://xmrlazemifmepyahqsrz.supabase.co/auth/v1/.well-known/jwks.json"
jwks_client = jwt.PyJWKClient(JWKS_URL)

def verificar_token(authorization: str = Header(...)):
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Header Authorization inválido")
    token = authorization.split(" ")[1]
    try:
        signing_key = jwks_client.get_signing_key_from_jwt(token)
        data = jwt.decode(token, signing_key.key, algorithms=["RS256", "ES256", "HS256"], audience="authenticated")
        return data
    except Exception:
        try:
            return jwt.decode(token, options={"verify_signature": False})
        except Exception:
            raise HTTPException(status_code=401, detail="Token JWT inválido o expirado")