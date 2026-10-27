import asyncio
import logging
from collections import defaultdict, deque
from time import monotonic, time
from sqlalchemy import select
from app.config import settings
from app.db import SessionLocal
from app.models import AttendanceSession, Camera, Enrollment, FaceEmbedding, Student, RecognitionEvent, AttendanceRecord
from app.recognition import MODEL, recognizer, match, usable
from app.camera import describe, fetch_jpeg, hub

log = logging.getLogger(__name__)


class CaptureService:
    """One serial inference pipeline; no overlapping model calls or retained frames."""
    def __init__(self):
        self.status = {}
        self.observations = defaultdict(deque)
        self.heartbeat = None
        self.sessions = {}  # session_id -> {'ok_at', 'error', 'faces'} for diagnostics

    async def fetch(self, client, camera):
        return await fetch_jpeg(client, camera.host)

    def process(self, session_id, vectors):
        with SessionLocal() as db:
            session = db.scalar(select(AttendanceSession).where(AttendanceSession.id == session_id).with_for_update())
            if session is None or session.status != 'running':
                return
            rows = db.execute(select(FaceEmbedding).join(Student).join(
                Enrollment, Enrollment.student_id == Student.id).where(
                Enrollment.class_id == session.class_id, Student.active.is_(True), FaceEmbedding.model == MODEL)).scalars()
            candidates, invalid = usable((row.student_id, row.encrypted) for row in rows)
            if invalid:
                log.warning('Chamada %s: %s embedding(s) não descriptografam com a EMBEDDING_KEY atual', session_id, invalid)
            # At most one observation per student per frame, even with duplicate faces.
            observed = set()
            for vector in vectors:
                student_id, score, state = match(vector, candidates, settings.threshold, settings.ambiguity_margin)
                if student_id in observed:
                    continue
                if student_id is not None:
                    observed.add(student_id)
                    key = (session_id, student_id)
                    times = self.observations[key]
                    timestamp = monotonic()
                    while times and timestamp - times[0] > 30:
                        times.popleft()
                    if not times or timestamp - times[-1] >= settings.interval_seconds * .8:
                        times.append(timestamp)
                    record = db.scalar(select(AttendanceRecord).where(
                        AttendanceRecord.session_id == session_id, AttendanceRecord.student_id == student_id))
                    confirmed = len(times) >= settings.observations
                    sure = confirmed and score >= settings.auto_present_score
                    if record is None and confirmed:
                        state = 'present' if sure else 'provisional'
                        db.add(AttendanceRecord(session_id=session_id, student_id=student_id, observations=len(times), status=state))
                    elif record:
                        # Upgrade an unreviewed provisional record; never override the teacher.
                        if not (sure and record.status == 'provisional' and record.reviewed_by is None):
                            continue
                        record.status = state = 'present'
                        record.observations = len(times)
                db.add(RecognitionEvent(session_id=session_id, student_id=student_id, score=score, state=state))
            db.commit()

    async def run(self):
        held = {}  # session_id -> camera_id currently acquired from the hub
        processed = {}  # session_id -> last frame seq, so a frozen frame is never counted twice
        try:
            while True:
                self.heartbeat = time()
                with SessionLocal() as db:
                    active = db.execute(select(AttendanceSession.id, Camera).join(
                        Camera, Camera.id == AttendanceSession.camera_id).where(AttendanceSession.status == 'running')).all()
                for session_id, camera in active:
                    if held.get(session_id) != camera.id:
                        hub.acquire(camera, f'session:{session_id}')
                        held[session_id] = camera.id
                for session_id in set(held) - {row[0] for row in active}:
                    hub.release(held.pop(session_id), f'session:{session_id}')
                    processed.pop(session_id, None)
                    self.sessions.pop(session_id, None)
                for session_id, camera in active:
                    try:
                        if camera.host not in settings.camera_hosts:
                            raise ValueError('Host da câmera não autorizado no backend')
                        frame = await hub.get(camera.id).latest(max_age=settings.interval_seconds * 2, wait=settings.camera_timeout)
                        if frame is None:
                            raise ValueError(hub.get(camera.id).error or 'Nenhum frame recente da câmera')
                        if processed.get(session_id) == frame.seq:
                            continue
                        processed[session_id] = frame.seq
                        vectors = await asyncio.to_thread(recognizer.analyze, frame.data)
                        await asyncio.to_thread(self.process, session_id, vectors)
                        self.status[camera.id] = 'connected'
                        self.sessions[session_id] = {'ok_at': time(), 'error': None, 'faces': len(vectors)}
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        self.status[camera.id] = 'error'
                        self.sessions[session_id] = {**self.sessions.get(session_id, {'ok_at': None, 'faces': 0}), 'error': describe(exc)}
                        log.exception('Falha na captura ou inferência da câmera %s', camera.id)
                active_ids = {row[0] for row in active}
                for key in list(self.observations):
                    if key[0] not in active_ids:
                        del self.observations[key]
                await asyncio.sleep(settings.interval_seconds)
        finally:
            for session_id, camera_id in held.items():
                hub.release(camera_id, f'session:{session_id}')


capture = CaptureService()
