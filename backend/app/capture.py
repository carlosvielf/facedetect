import asyncio
import logging
from collections import defaultdict, deque
from time import monotonic
import httpx
from sqlalchemy import select
from app.config import settings
from app.db import SessionLocal
from app.models import AttendanceSession, Camera, Enrollment, FaceEmbedding, Student, RecognitionEvent, AttendanceRecord
from app.recognition import MODEL, recognizer, decrypt, match

log = logging.getLogger(__name__)


class CaptureService:
    """One serial inference pipeline; no overlapping model calls or retained frames."""
    def __init__(self):
        self.status = {}
        self.observations = defaultdict(deque)
        self.camera_locks = defaultdict(asyncio.Lock)

    async def fetch(self, client, camera):
        if camera.host not in settings.camera_hosts:
            raise ValueError('Host da câmera não autorizado no backend')
        # Preview requests and recognition must not capture simultaneously.
        async with self.camera_locks[camera.host]:
            for attempt in range(2):
                try:
                    return await self._fetch(client, camera)
                except (httpx.TransportError, httpx.HTTPStatusError):
                    if attempt:
                        raise
                    await asyncio.sleep(.2)

    async def _fetch(self, client, camera):
        chunks = bytearray()
        # CameraWebServer responses can leave bytes on a persistent connection.
        # Use a fresh connection for each JPEG to avoid corrupting the next response.
        async with client.stream('GET', f'http://{camera.host}/capture',
                                 headers={'Connection': 'close'}) as response:
            response.raise_for_status()
            if 'image/jpeg' not in response.headers.get('content-type', ''):
                raise ValueError('A câmera não retornou JPEG')
            async for chunk in response.aiter_bytes():
                chunks.extend(chunk)
                if len(chunks) > 8_000_000:
                    raise ValueError('Captura excede 8 MB')
        return bytes(chunks)

    def process(self, session_id, vectors):
        with SessionLocal() as db:
            session = db.scalar(select(AttendanceSession).where(AttendanceSession.id == session_id).with_for_update())
            if session is None or session.status != 'running':
                return
            rows = db.execute(select(FaceEmbedding).join(Student).join(
                Enrollment, Enrollment.student_id == Student.id).where(
                Enrollment.class_id == session.class_id, Student.active.is_(True), FaceEmbedding.model == MODEL)).scalars()
            candidates = [(row.student_id, decrypt(row.encrypted)) for row in rows]
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
                    if record is None and len(times) >= settings.observations:
                        db.add(AttendanceRecord(session_id=session_id, student_id=student_id, observations=len(times)))
                        state = 'provisional'
                    elif record:
                        continue
                db.add(RecognitionEvent(session_id=session_id, student_id=student_id, score=score, state=state))
            db.commit()

    async def run(self):
        async with httpx.AsyncClient(timeout=5, follow_redirects=False, trust_env=False) as client:
            while True:
                with SessionLocal() as db:
                    active = db.execute(select(AttendanceSession.id, Camera).join(
                        Camera, Camera.id == AttendanceSession.camera_id).where(AttendanceSession.status == 'running')).all()
                for session_id, camera in active:
                    try:
                        content = await self.fetch(client, camera)
                        vectors = await asyncio.to_thread(recognizer.analyze, content)
                        await asyncio.to_thread(self.process, session_id, vectors)
                        self.status[camera.id] = 'connected'
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        self.status[camera.id] = 'error'
                        log.exception('Falha na captura ou inferência da câmera %s', camera.id)
                active_ids = {row[0] for row in active}
                for key in list(self.observations):
                    if key[0] not in active_ids:
                        del self.observations[key]
                await asyncio.sleep(settings.interval_seconds)


capture = CaptureService()
