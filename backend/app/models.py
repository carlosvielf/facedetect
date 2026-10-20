from datetime import datetime, timezone
from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, LargeBinary, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from app.db import Base


def now():
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = 'users'
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    email: Mapped[str] = mapped_column(String(254), unique=True)
    password_hash: Mapped[str] = mapped_column(String(256))
    role: Mapped[str] = mapped_column(String(20), default='professor')


class Student(Base):
    __tablename__ = 'students'
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    registration: Mapped[str] = mapped_column(String(80), unique=True)
    course: Mapped[str] = mapped_column(String(200), default='')
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Classroom(Base):
    __tablename__ = 'classes'
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    subject: Mapped[str] = mapped_column(String(200))
    semester: Mapped[str] = mapped_column(String(40))
    professor_id: Mapped[int] = mapped_column(ForeignKey('users.id'), index=True)


class Enrollment(Base):
    __tablename__ = 'enrollments'
    class_id: Mapped[int] = mapped_column(ForeignKey('classes.id'), primary_key=True)
    student_id: Mapped[int] = mapped_column(ForeignKey('students.id'), primary_key=True)


class FaceEmbedding(Base):
    __tablename__ = 'face_embeddings'
    id: Mapped[int] = mapped_column(primary_key=True)
    student_id: Mapped[int] = mapped_column(ForeignKey('students.id'), index=True)
    model: Mapped[str] = mapped_column(String(100))
    encrypted: Mapped[bytes] = mapped_column(LargeBinary)
    authorization: Mapped[str] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Camera(Base):
    __tablename__ = 'cameras'
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    host: Mapped[str] = mapped_column(String(253), unique=True)


class AttendanceSession(Base):
    __tablename__ = 'attendance_sessions'
    id: Mapped[int] = mapped_column(primary_key=True)
    class_id: Mapped[int] = mapped_column(ForeignKey('classes.id'), index=True)
    camera_id: Mapped[int] = mapped_column(ForeignKey('cameras.id'))
    status: Mapped[str] = mapped_column(String(20), default='draft')
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class RecognitionEvent(Base):
    __tablename__ = 'recognition_events'
    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey('attendance_sessions.id'), index=True)
    student_id: Mapped[int | None] = mapped_column(ForeignKey('students.id'))
    score: Mapped[float | None] = mapped_column(Float)
    state: Mapped[str] = mapped_column(String(30))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class AttendanceRecord(Base):
    __tablename__ = 'attendance_records'
    __table_args__ = (UniqueConstraint('session_id', 'student_id'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey('attendance_sessions.id'), index=True)
    student_id: Mapped[int] = mapped_column(ForeignKey('students.id'))
    status: Mapped[str] = mapped_column(String(20), default='provisional')
    source: Mapped[str] = mapped_column(String(20), default='face')
    observations: Mapped[int] = mapped_column(Integer, default=0)
    reviewed_by: Mapped[int | None] = mapped_column(ForeignKey('users.id'))


class AuditLog(Base):
    __tablename__ = 'audit_logs'
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey('users.id'))
    action: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
