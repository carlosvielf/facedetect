"""Create the first administrator interactively, without a default password."""
from getpass import getpass
from sqlalchemy import select
from app.db import SessionLocal
from app.models import User
from app.security import passwords


def main():
    with SessionLocal() as db:
        if db.scalar(select(User.id).where(User.role == 'admin')):
            raise SystemExit('Administrador já cadastrado')
        name = input('Nome: ').strip()
        email = input('E-mail institucional: ').strip().lower()
        password = getpass('Senha (mínimo 12 caracteres): ')
        if len(password) < 12 or '@' not in email or not name:
            raise SystemExit('Dados inválidos')
        db.add(User(name=name, email=email, password_hash=passwords.hash(password), role='admin'))
        db.commit()


if __name__ == '__main__':
    main()
