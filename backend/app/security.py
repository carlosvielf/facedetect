from datetime import datetime, timedelta, timezone
import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pwdlib import PasswordHash
from sqlalchemy.orm import Session
from app.config import settings
from app.db import database
from app.models import User

passwords = PasswordHash.recommended()
bearer = HTTPBearer()


def token(user: User):
    return jwt.encode({'sub': str(user.id), 'exp': datetime.now(timezone.utc) + timedelta(hours=2)}, settings.jwt_secret, algorithm='HS256')


def user_from_token(db: Session, value: str):
    try:
        payload = jwt.decode(value, settings.jwt_secret, algorithms=['HS256'])
        user = db.get(User, int(payload['sub']))
        if user is None:
            raise ValueError()
        return user
    except (jwt.PyJWTError, ValueError, KeyError, TypeError):
        raise HTTPException(401, 'Sessão inválida ou expirada')


def current_user(credentials: HTTPAuthorizationCredentials = Depends(bearer), db: Session = Depends(database)):
    return user_from_token(db, credentials.credentials)


def admin(user: User = Depends(current_user)):
    if user.role != 'admin':
        raise HTTPException(403, 'Acesso administrativo necessário')
    return user
