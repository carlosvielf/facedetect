import asyncio
import csv
import io
import re
from datetime import datetime
from contextlib import asynccontextmanager, suppress
from collections import defaultdict, deque
from time import monotonic
from fastapi import Depends, FastAPI, HTTPException, Request, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from app.config import settings
from app.db import database, SessionLocal
from app.models import User, Student, Classroom, Enrollment, Camera, FaceEmbedding, AttendanceSession, AttendanceRecord, RecognitionEvent, AuditLog
from app.security import admin, current_user, passwords, token
from app.recognition import MODEL, recognizer, encrypt
from app.capture import capture
from app.camera import hub
from app import diagnostics, monitor


@asynccontextmanager
async def lifespan(app):
    # A process restart requires the teacher to explicitly resume capture.
    with SessionLocal() as db:
        for session in db.scalars(select(AttendanceSession).where(AttendanceSession.status == 'running')):
            session.status = 'stopped'
        db.commit()
    task = asyncio.create_task(capture.run())
    yield
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task
    await hub.close()


app = FastAPI(title='Presença Acadêmica', lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_credentials=False,
                   allow_methods=['GET', 'POST', 'PUT', 'DELETE'], allow_headers=['Authorization', 'Content-Type'])
app.include_router(monitor.router)
app.include_router(diagnostics.router)
requests = defaultdict(deque)


@app.middleware('http')
async def rate_limit(request: Request, call_next):
    key = request.client.host if request.client else 'unknown'
    now = monotonic()
    for stale in [k for k, values in requests.items() if not values or now-values[-1] > 60]:
        del requests[stale]
    times = requests[key]
    while times and now-times[0] > 60:
        times.popleft()
    limit = 10 if request.url.path == '/api/auth/login' else 180
    bucket = key + ':login' if request.url.path == '/api/auth/login' else key
    times = requests[bucket]
    while times and now-times[0] > 60:
        times.popleft()
    if len(times) >= limit:
        return Response('Limite de requisições', status_code=429)
    times.append(now)
    result = await call_next(request)
    result.headers['Cache-Control'] = 'no-store'
    result.headers['X-Content-Type-Options'] = 'nosniff'
    return result


@app.exception_handler(IntegrityError)
async def conflict(request, exc):
    return Response('Registro duplicado ou vínculo inválido', status_code=409)


def audit(db, user, action):
    db.add(AuditLog(user_id=user.id, action=action))


def classroom(db, class_id, user):
    item = db.get(Classroom, class_id)
    if not item or (user.role != 'admin' and item.professor_id != user.id):
        raise HTTPException(404, 'Turma não encontrada')
    return item


def attendance(db, session_id, user, writable=False):
    item = db.get(AttendanceSession, session_id)
    if not item:
        raise HTTPException(404, 'Chamada não encontrada')
    classroom(db, item.class_id, user)
    if writable and item.status == 'finalized':
        raise HTTPException(409, 'Chamada finalizada; alteração bloqueada')
    return item


class Login(BaseModel):
    email: str = Field(max_length=254)
    password: str = Field(max_length=200)


@app.post('/api/auth/login')
def login(body: Login, db: Session = Depends(database)):
    user = db.scalar(select(User).where(User.email == body.email.strip().lower()))
    if not user or not passwords.verify(body.password, user.password_hash):
        raise HTTPException(401, 'Credenciais inválidas')
    return {'access_token': token(user), 'token_type': 'bearer'}


@app.get('/api/auth/me')
def me(user: User = Depends(current_user)):
    return {'id': user.id, 'name': user.name, 'role': user.role}


@app.get('/api/health')
def health(db: Session = Depends(database)):
    db.execute(select(1))
    return {'status': 'ok', 'recognition_loaded': recognizer.analyzer is not None}


class StudentInput(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    registration: str = Field(min_length=1, max_length=80)
    course: str = Field(default='', max_length=200)
    active: bool = True


@app.get('/api/students')
def students(q: str = '', user: User = Depends(current_user), db: Session = Depends(database)):
    query = select(Student)
    if user.role != 'admin':
        query = query.join(Enrollment).join(Classroom).where(Classroom.professor_id == user.id)
    if q:
        query = query.where(Student.name.ilike(f'%{q[:200]}%'))
    result = []
    for student in db.scalars(query.distinct().order_by(Student.name)):
        result.append({'id': student.id, 'name': student.name, 'registration': student.registration,
                       'course': student.course, 'active': student.active,
                       'biometric': bool(db.scalar(select(FaceEmbedding.id).where(FaceEmbedding.student_id == student.id).limit(1)))})
    return result


@app.post('/api/students')
def create_student(body: StudentInput, user: User = Depends(admin), db: Session = Depends(database)):
    item = Student(**body.model_dump())
    db.add(item)
    audit(db, user, 'student:create')
    db.commit()
    return {'id': item.id}


@app.put('/api/students/{student_id}')
def edit_student(student_id: int, body: StudentInput, user: User = Depends(admin), db: Session = Depends(database)):
    item = db.get(Student, student_id)
    if not item:
        raise HTTPException(404)
    for key, value in body.model_dump().items():
        setattr(item, key, value)
    audit(db, user, f'student:update:{student_id}')
    db.commit()
    return {'ok': True}


@app.post('/api/students/{student_id}/face-enrollment')
async def enroll_face(student_id: int, image: UploadFile = File(), authorization: str = Form(min_length=10, max_length=500),
                      user: User = Depends(admin), db: Session = Depends(database)):
    if not db.get(Student, student_id):
        raise HTTPException(404)
    content = await image.read(8_000_001)
    try:
        vectors = await asyncio.to_thread(recognizer.analyze, content, True)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    if not vectors:
        raise HTTPException(422, 'Nenhum rosto adequado')
    db.add(FaceEmbedding(student_id=student_id, model=MODEL, encrypted=encrypt(vectors[0]), authorization=authorization))
    audit(db, user, f'face:enroll:{student_id}')
    db.commit()
    return {'ok': True, 'model': MODEL}


@app.delete('/api/students/{student_id}/face-enrollment')
def delete_face(student_id: int, user: User = Depends(admin), db: Session = Depends(database)):
    from sqlalchemy import delete
    db.execute(delete(FaceEmbedding).where(FaceEmbedding.student_id == student_id))
    audit(db, user, f'face:delete:{student_id}')
    db.commit()
    return {'ok': True}


class ProfessorInput(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    email: str = Field(min_length=5, max_length=254, pattern=r'^[^\s@]+@[^\s@]+\.[^\s@]+$')
    password: str = Field(min_length=12, max_length=200)


@app.get('/api/professors')
def professors(user: User = Depends(admin), db: Session = Depends(database)):
    return [{'id': u.id, 'name': u.name, 'email': u.email} for u in db.scalars(select(User).where(User.role == 'professor'))]


@app.post('/api/professors')
def create_professor(body: ProfessorInput, user: User = Depends(admin), db: Session = Depends(database)):
    item = User(name=body.name, email=body.email.strip().lower(), password_hash=passwords.hash(body.password), role='professor')
    db.add(item)
    audit(db, user, 'professor:create')
    db.commit()
    return {'id': item.id}


class ClassInput(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    subject: str = Field(min_length=2, max_length=200)
    semester: str = Field(min_length=2, max_length=40)
    professor_id: int


@app.get('/api/classes')
def classes(user: User = Depends(current_user), db: Session = Depends(database)):
    query = select(Classroom)
    if user.role != 'admin':
        query = query.where(Classroom.professor_id == user.id)
    return list(db.scalars(query))


@app.post('/api/classes')
def create_class(body: ClassInput, user: User = Depends(admin), db: Session = Depends(database)):
    if not db.get(User, body.professor_id):
        raise HTTPException(422, 'Professor inválido')
    item = Classroom(**body.model_dump())
    db.add(item)
    audit(db, user, 'class:create')
    db.commit()
    return {'id': item.id}


@app.post('/api/classes/{class_id}/students/{student_id}')
def enroll(class_id: int, student_id: int, user: User = Depends(admin), db: Session = Depends(database)):
    classroom(db, class_id, user)
    if not db.get(Student, student_id):
        raise HTTPException(404)
    if not db.get(Enrollment, (class_id, student_id)):
        db.add(Enrollment(class_id=class_id, student_id=student_id))
        audit(db, user, f'enrollment:{class_id}:{student_id}')
        db.commit()
    return {'ok': True}


class CameraInput(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    host: str = Field(min_length=1, max_length=253)


@app.post('/api/cameras')
def add_camera(body: CameraInput, user: User = Depends(admin), db: Session = Depends(database)):
    if body.host not in settings.camera_hosts or not re.fullmatch(r'[a-zA-Z0-9.-]+', body.host):
        raise HTTPException(422, 'Inclua o IP ou hostname na lista CAMERA_HOSTS do servidor')
    item = Camera(**body.model_dump())
    db.add(item)
    audit(db, user, 'camera:create')
    db.commit()
    return {'id': item.id}


@app.get('/api/cameras')
def cameras(user: User = Depends(current_user), db: Session = Depends(database)):
    return [{'id': c.id, 'name': c.name, 'status': capture.status.get(c.id, 'idle'),
             'live': hub.get(c.id).state if hub.get(c.id) else 'disconnected'} for c in db.scalars(select(Camera))]


class SessionInput(BaseModel):
    class_id: int
    camera_id: int


@app.post('/api/attendance/sessions')
def new_session(body: SessionInput, user: User = Depends(current_user), db: Session = Depends(database)):
    classroom(db, body.class_id, user)
    if not db.get(Camera, body.camera_id):
        raise HTTPException(422, 'Câmera inválida')
    item = AttendanceSession(**body.model_dump())
    db.add(item)
    audit(db, user, 'attendance:create')
    db.commit()
    return {'id': item.id}


@app.post('/api/attendance/sessions/{session_id}/{action}')
def transition(session_id: int, action: str, user: User = Depends(current_user), db: Session = Depends(database)):
    # Lock serializes review/finalization against inference and other transitions.
    item = db.scalar(select(AttendanceSession).where(AttendanceSession.id == session_id).with_for_update())
    item = attendance(db, session_id, user, True)
    if action not in ('start', 'stop', 'confirm'):
        raise HTTPException(404)
    if action == 'confirm':
        provisional = db.scalar(select(AttendanceRecord.id).where(AttendanceRecord.session_id == session_id, AttendanceRecord.status == 'provisional'))
        if provisional:
            raise HTTPException(409, 'Revise todas as identificações provisórias antes de finalizar')
        item.status = 'finalized'
    else:
        item.status = 'running' if action == 'start' else 'stopped'
    audit(db, user, f'attendance:{action}:{session_id}')
    db.commit()
    return {'status': item.status}


class ReviewInput(BaseModel):
    student_id: int
    status: str = Field(pattern='^(present|absent|rejected)$')


@app.put('/api/attendance/sessions/{session_id}/records')
def review(session_id: int, body: ReviewInput, user: User = Depends(current_user), db: Session = Depends(database)):
    db.scalar(select(AttendanceSession).where(AttendanceSession.id == session_id).with_for_update())
    session = attendance(db, session_id, user, True)
    if not db.get(Enrollment, (session.class_id, body.student_id)):
        raise HTTPException(422, 'Aluno não matriculado nesta turma')
    record = db.scalar(select(AttendanceRecord).where(AttendanceRecord.session_id == session_id, AttendanceRecord.student_id == body.student_id))
    if not record:
        record = AttendanceRecord(session_id=session_id, student_id=body.student_id, source='manual')
        db.add(record)
    record.status = body.status
    record.reviewed_by = user.id
    audit(db, user, f'attendance:review:{session_id}:{body.student_id}:{body.status}')
    db.commit()
    return {'ok': True}


@app.get('/api/attendance/sessions/{session_id}/recognitions')
def recognitions(session_id: int, user: User = Depends(current_user), db: Session = Depends(database)):
    session = attendance(db, session_id, user)
    roster = db.scalars(select(Student).join(Enrollment).where(Enrollment.class_id == session.class_id)).all()
    records = {r.student_id: r for r in db.scalars(select(AttendanceRecord).where(AttendanceRecord.session_id == session_id))}
    return {'status': session.status, 'students': [
        {'id': s.id, 'name': s.name, 'registration': s.registration,
         'status': records[s.id].status if s.id in records else 'unmarked'} for s in roster],
        'events': list(db.scalars(select(RecognitionEvent).where(RecognitionEvent.session_id == session_id).order_by(RecognitionEvent.id.desc()).limit(50)))}


@app.get('/api/attendance/history')
def history(class_id: int | None = None, professor_id: int | None = None,
            student_id: int | None = None, status: str | None = None,
            after: datetime | None = None, before: datetime | None = None,
            user: User = Depends(current_user), db: Session = Depends(database)):
    query = select(AttendanceSession).join(Classroom)
    if user.role != 'admin':
        query = query.where(Classroom.professor_id == user.id)
    if class_id:
        query = query.where(AttendanceSession.class_id == class_id)
    if professor_id:
        query = query.where(Classroom.professor_id == professor_id)
    if after:
        query = query.where(AttendanceSession.created_at >= after)
    if before:
        query = query.where(AttendanceSession.created_at <= before)
    if student_id or status:
        query = query.join(AttendanceRecord)
        if student_id:
            query = query.where(AttendanceRecord.student_id == student_id)
        if status:
            query = query.where(AttendanceRecord.status == status)
    return list(db.scalars(query.distinct().order_by(AttendanceSession.id.desc()).limit(200)))


@app.get('/api/attendance/sessions/{session_id}/frame')
async def camera_frame(session_id: int, user: User = Depends(current_user), db: Session = Depends(database)):
    session = attendance(db, session_id, user)
    camera = db.get(Camera, session.camera_id)
    try:
        content = await monitor.snapshot_bytes(camera)
    except ValueError as exc:
        raise HTTPException(503, f'A ESP32 não respondeu à captura. {exc}')
    return Response(content, media_type='image/jpeg', headers={'Cache-Control': 'no-store'})


@app.get('/api/attendance/export')
def export(session_id: int, format: str = 'csv', user: User = Depends(current_user), db: Session = Depends(database)):
    data = recognitions(session_id, user, db)
    if format not in ('csv', 'xlsx', 'pdf'):
        raise HTTPException(422, 'Formato inválido')
    if format == 'xlsx':
        from openpyxl import Workbook
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = 'Chamada'
        sheet.append(['Matrícula', 'Nome', 'Presença'])
        for row in data['students']:
            sheet.append([row['registration'], row['name'], row['status']])
            for cell in sheet[sheet.max_row]:
                cell.data_type = 's'
        output_bytes = io.BytesIO()
        workbook.save(output_bytes)
        return Response(output_bytes.getvalue(), media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', headers={'Content-Disposition': f'attachment; filename=chamada-{session_id}.xlsx'})
    if format == 'pdf':
        from reportlab.pdfgen.canvas import Canvas
        output_bytes = io.BytesIO()
        canvas = Canvas(output_bytes)
        canvas.setTitle(f'Chamada {session_id}')
        canvas.drawString(40, 800, f'Chamada #{session_id} - {data["status"]}')
        y = 770
        for row in data['students']:
            if y < 50:
                canvas.showPage()
                y = 800
            canvas.drawString(40, y, f'{row["registration"]} | {row["name"][:65]} | {row["status"]}')
            y -= 20
        canvas.save()
        return Response(output_bytes.getvalue(), media_type='application/pdf', headers={'Content-Disposition': f'attachment; filename=chamada-{session_id}.pdf'})
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['Matrícula', 'Nome', 'Presença'])
    for row in data['students']:
        # Prevent spreadsheet formula injection, including leading whitespace.
        values = [row['registration'], row['name'], row['status']]
        writer.writerow(["'" + v if v.lstrip().startswith(('=', '+', '-', '@')) else v for v in values])
    return Response('\ufeff' + output.getvalue(), media_type='text/csv; charset=utf-8', headers={'Content-Disposition': f'attachment; filename=chamada-{session_id}.csv'})
