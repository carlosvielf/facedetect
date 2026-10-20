import os
from cryptography.fernet import Fernet

os.environ['DATABASE_URL'] = 'sqlite://'
os.environ['JWT_SECRET'] = 'test-only-secret-with-more-than-32-characters'
os.environ['EMBEDDING_KEY'] = Fernet.generate_key().decode()
os.environ['CAMERA_HOSTS'] = '["192.168.1.100"]'

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from app.db import Base, SessionLocal
from app.models import User, Student, Classroom, Enrollment, Camera, AttendanceSession
from app.security import passwords, token
from app.main import app, requests


@pytest.fixture
def setup():
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    SessionLocal.configure(bind=engine)
    Base.metadata.create_all(engine)
    requests.clear()
    with SessionLocal() as db:
        admin = User(name='Admin Test', email='admin@test.invalid', password_hash=passwords.hash('test-password-123'), role='admin')
        teacher = User(name='Teacher', email='teacher@test.invalid', password_hash=passwords.hash('test-password-123'), role='professor')
        outsider = User(name='Other', email='other@test.invalid', password_hash=passwords.hash('test-password-123'), role='professor')
        db.add_all([admin, teacher, outsider])
        db.flush()
        student = Student(name='Student Test', registration='001', course='Course')
        group = Classroom(name='Group', subject='Subject', semester='2026.2', professor_id=teacher.id)
        camera = Camera(name='Test camera', host='192.168.1.100')
        db.add_all([student, group, camera])
        db.flush()
        db.add(Enrollment(class_id=group.id, student_id=student.id))
        session = AttendanceSession(class_id=group.id, camera_id=camera.id, status='stopped')
        db.add(session)
        db.commit()
        values = {'admin': token(admin), 'teacher': token(teacher), 'outsider': token(outsider), 'session': session.id, 'student': student.id}
    # No context manager: lifespan intentionally omitted; unit/integration tests do not start real camera capture.
    with TestClient(app, raise_server_exceptions=True) as client:
        yield client, values
    engine.dispose()


def headers(values, role='teacher'):
    return {'Authorization': f'Bearer {values[role]}'}
