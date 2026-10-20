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


def current_user(credentials: HTTPAuthorizationCredentials = Depends(bearer), db: Session = Depends(database)):
    try:
        payload = jwt.decode(credentials.credentials, settings.jwt_secret, algorithms=['HS256'])
        user = db.get(User, int(payload['sub']))
        if user is None:
            raise ValueError()
        return user
    except (jwt.PyJWTError, ValueError, KeyError):
        raise HTTPException(401, 'Sessão inválida ou expirada')


def admin(user: User = Depends(current_user)):
    if user.role != 'admin':
        raise HTTPException(403, 'Acesso administrativo necessário')
    return user
