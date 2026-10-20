"""Initial schema for the integrated attendance slice.

Revision ID: 0001
"""
from alembic import op
import sqlalchemy as sa

revision = '0001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('users', sa.Column('id', sa.Integer, primary_key=True), sa.Column('name', sa.String(200), nullable=False), sa.Column('email', sa.String(254), nullable=False, unique=True), sa.Column('password_hash', sa.String(256), nullable=False), sa.Column('role', sa.String(20), nullable=False))
    op.create_table('students', sa.Column('id', sa.Integer, primary_key=True), sa.Column('name', sa.String(200), nullable=False), sa.Column('registration', sa.String(80), nullable=False, unique=True), sa.Column('course', sa.String(200), nullable=False), sa.Column('active', sa.Boolean, nullable=False))
    op.create_table('classes', sa.Column('id', sa.Integer, primary_key=True), sa.Column('name', sa.String(200), nullable=False), sa.Column('subject', sa.String(200), nullable=False), sa.Column('semester', sa.String(40), nullable=False), sa.Column('professor_id', sa.Integer, sa.ForeignKey('users.id'), nullable=False))
    op.create_index('ix_classes_professor_id', 'classes', ['professor_id'])
    op.create_table('enrollments', sa.Column('class_id', sa.Integer, sa.ForeignKey('classes.id'), primary_key=True), sa.Column('student_id', sa.Integer, sa.ForeignKey('students.id'), primary_key=True))
    op.create_table('cameras', sa.Column('id', sa.Integer, primary_key=True), sa.Column('name', sa.String(200), nullable=False), sa.Column('host', sa.String(253), nullable=False, unique=True))
    op.create_table('face_embeddings', sa.Column('id', sa.Integer, primary_key=True), sa.Column('student_id', sa.Integer, sa.ForeignKey('students.id'), nullable=False), sa.Column('model', sa.String(100), nullable=False), sa.Column('encrypted', sa.LargeBinary, nullable=False), sa.Column('authorization', sa.String(500), nullable=False), sa.Column('created_at', sa.DateTime(timezone=True), nullable=False))
    op.create_index('ix_face_embeddings_student_id', 'face_embeddings', ['student_id'])
    op.create_table('attendance_sessions', sa.Column('id', sa.Integer, primary_key=True), sa.Column('class_id', sa.Integer, sa.ForeignKey('classes.id'), nullable=False), sa.Column('camera_id', sa.Integer, sa.ForeignKey('cameras.id'), nullable=False), sa.Column('status', sa.String(20), nullable=False), sa.Column('created_at', sa.DateTime(timezone=True), nullable=False))
    op.create_index('ix_attendance_sessions_class_id', 'attendance_sessions', ['class_id'])
    op.create_table('recognition_events', sa.Column('id', sa.Integer, primary_key=True), sa.Column('session_id', sa.Integer, sa.ForeignKey('attendance_sessions.id'), nullable=False), sa.Column('student_id', sa.Integer, sa.ForeignKey('students.id')), sa.Column('score', sa.Float), sa.Column('state', sa.String(30), nullable=False), sa.Column('created_at', sa.DateTime(timezone=True), nullable=False))
    op.create_index('ix_recognition_events_session_id', 'recognition_events', ['session_id'])
    op.create_table('attendance_records', sa.Column('id', sa.Integer, primary_key=True), sa.Column('session_id', sa.Integer, sa.ForeignKey('attendance_sessions.id'), nullable=False), sa.Column('student_id', sa.Integer, sa.ForeignKey('students.id'), nullable=False), sa.Column('status', sa.String(20), nullable=False), sa.Column('source', sa.String(20), nullable=False), sa.Column('observations', sa.Integer, nullable=False), sa.Column('reviewed_by', sa.Integer, sa.ForeignKey('users.id')), sa.UniqueConstraint('session_id', 'student_id'))
    op.create_index('ix_attendance_records_session_id', 'attendance_records', ['session_id'])
    op.create_table('audit_logs', sa.Column('id', sa.Integer, primary_key=True), sa.Column('user_id', sa.Integer, sa.ForeignKey('users.id')), sa.Column('action', sa.String(200), nullable=False), sa.Column('created_at', sa.DateTime(timezone=True), nullable=False))


def downgrade():
    for table in ['audit_logs', 'attendance_records', 'recognition_events', 'attendance_sessions', 'face_embeddings', 'cameras', 'enrollments', 'classes', 'students', 'users']:
        op.drop_table(table)
