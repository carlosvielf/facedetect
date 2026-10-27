"""Camera monitor: preview, connection tests, snapshots and the optional face overlay.

Overlay results are for display only; attendance records are written exclusively
by CaptureService for running sessions, after temporal confirmation and with
teacher review.
"""
import asyncio
import json
import logging
import uuid
from time import monotonic, perf_counter
import httpx
from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session
from app import camera as cameras
from app.camera import describe, fetch_jpeg, hub, jpeg_size, split_mjpeg
from app.config import settings
from app.db import SessionLocal, database
from app.models import AttendanceSession, AuditLog, Camera, Classroom, Enrollment, FaceEmbedding, Student, User
from app.recognition import MODEL, decode, match, recognizer, usable
from app.security import current_user, user_from_token

log = logging.getLogger(__name__)
router = APIRouter(prefix='/api/cameras')
# esp32-camera framesize_t as shipped with arduino-esp32 3.x (sensor.h).
FRAMESIZES = {6: 'QVGA 320×240', 8: 'CIF 400×296', 9: 'HVGA 480×320', 10: 'VGA 640×480', 11: 'SVGA 800×600', 12: 'XGA 1024×768'}
MODES = ('preview', 'detection', 'recognition')


def camera_or_404(db, camera_id):
    camera = db.get(Camera, camera_id)
    if camera is None:
        raise HTTPException(404, 'Câmera não encontrada')
    if camera.host not in settings.camera_hosts:
        raise HTTPException(422, 'Host da câmera não está em CAMERA_HOSTS no backend')
    return camera


def recognition_session(db, user, camera_id, session_id):
    """Recognition needs a running session on this camera for a class the user teaches."""
    session = db.get(AttendanceSession, session_id) if session_id else None
    if session is None:
        raise HTTPException(409, 'Reconhecimento exige uma chamada selecionada')
    group = db.get(Classroom, session.class_id)
    if group is None or (user.role != 'admin' and group.professor_id != user.id):
        raise HTTPException(404, 'Chamada não encontrada')
    if session.camera_id != camera_id:
        raise HTTPException(409, 'A chamada selecionada usa outra câmera')
    if session.status != 'running':
        raise HTTPException(409, 'Inicie a captura da chamada para habilitar o reconhecimento')
    return session


async def probe(host: str, reader=None):
    """Step-by-step reachability test of the CameraWebServer endpoints."""
    steps, result = [], {'width': None, 'height': None, 'framesize': None, 'framesize_label': None}

    def step(name, ok, started, detail):
        steps.append({'name': name, 'ok': ok, 'ms': round((perf_counter() - started) * 1000), 'detail': detail})

    async with cameras.client() as http:
        started = perf_counter()
        try:
            response = await http.get(f'http://{host}/status')
            response.raise_for_status()
            status = response.json()
            result['framesize'] = status.get('framesize')
            result['framesize_label'] = FRAMESIZES.get(status.get('framesize'))
            step('status', True, started, f"framesize={status.get('framesize')} quality={status.get('quality')}")
        except Exception as exc:
            step('status', False, started, describe(exc))
            if isinstance(exc, httpx.TransportError):
                return {'status': 'disconnected', 'steps': steps, **result}
        started = perf_counter()
        try:
            data = await fetch_jpeg(http, host)
            result['width'], result['height'] = jpeg_size(data)
            step('capture', True, started, f'JPEG válido {result["width"]}×{result["height"]}, {len(data)} bytes')
        except Exception as exc:
            step('capture', False, started, describe(exc))
        started = perf_counter()
        if reader and reader.source == 'stream' and reader.state == 'streaming':
            # Opening a second stream would block: port 81 serves one client at a time.
            step('stream', True, started, f'Em uso pelo backend: {reader.fps:.1f} FPS, {reader.corrupted} frames corrompidos')
        else:
            try:
                url = f'http://{host}:{settings.camera_stream_port}/stream'
                async with http.stream('GET', url) as response:
                    response.raise_for_status()
                    boundary = response.headers.get('content-type', '').split('boundary=', 1)[1].strip().encode()
                    buffer, parts = bytearray(), []
                    async for chunk in response.aiter_bytes():
                        buffer.extend(chunk)
                        parts += split_mjpeg(buffer, boundary)
                        if len(parts) >= 2 or len(buffer) > 8_000_000:
                            break
                valid = sum(1 for part in parts if jpeg_size(part))
                step('stream', valid > 0, started, f'{valid}/{len(parts)} frames JPEG válidos na porta {settings.camera_stream_port}')
            except IndexError:
                step('stream', False, started, 'Resposta do stream sem boundary multipart')
            except Exception as exc:
                step('stream', False, started, describe(exc))
    ok = all(s['ok'] for s in steps)
    return {'status': 'ok' if ok else 'error', 'steps': steps, **result}


@router.get('/{camera_id}/monitor')
def monitor_status(camera_id: int, user: User = Depends(current_user), db: Session = Depends(database)):
    camera = camera_or_404(db, camera_id)
    reader = hub.get(camera.id)
    return reader.snapshot() if reader else {'camera_id': camera.id, 'state': 'disconnected', 'consumers': 0}


@router.post('/{camera_id}/test')
async def test_camera(camera_id: int, user: User = Depends(current_user), db: Session = Depends(database)):
    camera = camera_or_404(db, camera_id)
    return await probe(camera.host, hub.get(camera.id))


async def snapshot_bytes(camera):
    reader = hub.get(camera.id)
    # Reuse the shared frame when a reader is live instead of hitting the sensor again.
    frame = await reader.latest(max_age=2, wait=0) if reader else None
    if frame is not None:
        return frame.data
    try:
        async with cameras.client() as http:
            return await fetch_jpeg(http, camera.host)
    except (httpx.HTTPError, ValueError) as exc:
        raise ValueError(describe(exc)) from exc


@router.get('/{camera_id}/snapshot')
async def snapshot(camera_id: int, user: User = Depends(current_user), db: Session = Depends(database)):
    camera = camera_or_404(db, camera_id)
    try:
        data = await snapshot_bytes(camera)
    except ValueError as exc:
        raise HTTPException(503, str(exc))
    width, height = jpeg_size(data)
    return Response(data, media_type='image/jpeg', headers={
        'Cache-Control': 'no-store', 'X-Frame-Width': str(width), 'X-Frame-Height': str(height),
        'Content-Disposition': f'attachment; filename=camera-{camera.id}.jpg'})


class FramesizeInput(BaseModel):
    value: int


@router.get('/framesizes')
def framesizes(user: User = Depends(current_user)):
    return [{'value': value, 'label': label} for value, label in FRAMESIZES.items()]


@router.post('/{camera_id}/framesize')
async def set_framesize(camera_id: int, body: FramesizeInput, user: User = Depends(current_user), db: Session = Depends(database)):
    if body.value not in FRAMESIZES:
        raise HTTPException(422, 'Resolução não suportada')
    camera = camera_or_404(db, camera_id)
    try:
        async with cameras.client() as http:
            response = await http.get(f'http://{camera.host}/control', params={'var': 'framesize', 'val': body.value})
            response.raise_for_status()
    except httpx.HTTPError as exc:
        raise HTTPException(503, describe(exc))
    db.add(AuditLog(user_id=user.id, action=f'camera:framesize:{camera.id}:{body.value}'))
    db.commit()
    return {'ok': True, 'label': FRAMESIZES[body.value]}


candidate_cache = {}


def session_candidates(session_id):
    cached = candidate_cache.get(session_id)
    if cached and monotonic() - cached[0] < 15:
        return cached[1]
    with SessionLocal() as db:
        session = db.get(AttendanceSession, session_id)
        if session is None or session.status != 'running':
            candidate_cache.pop(session_id, None)
            return None
        rows = db.execute(select(FaceEmbedding.student_id, FaceEmbedding.encrypted, Student.name).join(Student).join(
            Enrollment, Enrollment.student_id == Student.id).where(
            Enrollment.class_id == session.class_id, Student.active.is_(True), FaceEmbedding.model == MODEL)).all()
    candidates, _ = usable((row.student_id, row.encrypted) for row in rows)
    value = (candidates, {row.student_id: row.name for row in rows})
    candidate_cache[session_id] = (monotonic(), value)
    return value


def classify(face, candidates, names):
    item = {'bbox': face['bbox'], 'detection_score': round(face['confidence'], 3), 'issue': face['issue'],
            'state': 'detected', 'name': None, 'student_id': None, 'similarity': None}
    if candidates is None or face['embedding'] is None:
        return item
    student_id, score, state = match(face['embedding'], candidates, settings.threshold, settings.ambiguity_margin)
    item['similarity'] = None if score is None else round(score, 3)
    if state == 'candidate':
        item.update(state='recognized', student_id=student_id, name=names.get(student_id))
    elif state == 'ambiguous' or (score is not None and score >= settings.threshold - settings.uncertain_band):
        item['state'] = 'uncertain'
    else:
        item['state'] = 'unknown'
    return item


def run_analysis(data, recognition_session_id):
    image = decode(data)
    started = perf_counter()
    candidates, names = None, {}
    if recognition_session_id:
        loaded = session_candidates(recognition_session_id)
        if loaded is None:
            raise ValueError('A chamada não está mais em andamento; reconhecimento suspenso')
        candidates, names = loaded
    faces = recognizer.inspect(image, embeddings=candidates is not None)
    return [classify(face, candidates, names) for face in faces], (perf_counter() - started) * 1000


async def analysis_loop(reader):
    """Adaptive overlay inference: always the newest frame, never a backlog."""
    last_seq, last_done, fps = 0, None, 0.0
    while True:
        wanted = reader.wants_analysis()
        if wanted is None:
            reader.analysis = None
            await reader.notify()
            return
        mode, session_id = wanted
        frame = reader.frame
        if frame is None or frame.seq == last_seq:
            async with reader.changed:
                try:
                    await asyncio.wait_for(reader.changed.wait(), 1)
                except asyncio.TimeoutError:
                    pass
            continue
        last_seq = frame.seq
        started = perf_counter()
        try:
            faces, processing_ms = await asyncio.to_thread(run_analysis, frame.data, session_id if mode == 'recognition' else None)
            error = None
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning('Inferência do monitor falhou: %s', exc)
            faces, processing_ms, error = [], None, str(exc)
        now = monotonic()
        if last_done:
            fps = 1 / (now - last_done) if not fps else .7 * fps + .3 / (now - last_done)
        last_done = now
        reader.analysis = {'type': 'analysis', 'seq': frame.seq, 'mode': mode, 'session_id': session_id,
                           'width': frame.width, 'height': frame.height, 'faces': faces, 'count': len(faces),
                           'processing_ms': None if processing_ms is None else round(processing_ms, 1),
                           'inference_fps': round(fps, 2), 'error': error,
                           'thresholds': {'similarity': settings.threshold, 'uncertain_band': settings.uncertain_band,
                                          'margin': settings.ambiguity_margin}}
        await reader.notify()
        # Cap CPU use; slower hardware simply yields a lower inference FPS.
        await asyncio.sleep(max(0, 1 / settings.inference_max_fps - (perf_counter() - started)))


hub.analyzer = analysis_loop


def view(analysis, mode, session_id):
    """What one client may see: identities only for its own authorized recognition session."""
    if analysis is None or mode == 'preview':
        return None
    if mode == 'recognition' and analysis['mode'] == 'recognition' and analysis['session_id'] == session_id:
        return analysis
    faces = [{**face, 'state': 'detected', 'name': None, 'student_id': None, 'similarity': None} for face in analysis['faces']]
    return {**analysis, 'mode': 'detection', 'session_id': None, 'faces': faces}


@router.websocket('/{camera_id}/ws')
async def camera_socket(websocket: WebSocket, camera_id: int):
    """Protocol: client sends {"type":"auth","token","mode","session_id"} first, then
    {"type":"mode",...} to switch modes. Server sends JSON "status"/"frame"/"analysis"/"error";
    each "frame" message is followed by one binary JPEG message."""
    await websocket.accept()
    owner = f'ws:{uuid.uuid4().hex}'
    reader = None
    try:
        try:
            hello = json.loads(await asyncio.wait_for(websocket.receive_text(), 10))
            with SessionLocal() as db:
                user = user_from_token(db, hello.get('token'))
                camera = camera_or_404(db, camera_id)
        except (asyncio.TimeoutError, ValueError, HTTPException) as exc:
            await websocket.send_json({'type': 'error', 'message': getattr(exc, 'detail', None) or 'Autenticação ausente'})
            await websocket.close(1008)
            return
        state = {'mode': ('preview', None)}

        def requested(message):
            mode = message.get('mode', 'preview')
            if mode not in MODES:
                raise HTTPException(422, 'Modo inválido')
            if mode != 'recognition':
                return mode, None
            with SessionLocal() as db:
                return mode, recognition_session(db, db.get(User, user.id), camera_id, message.get('session_id')).id

        try:
            state['mode'] = requested(hello)
        except HTTPException as exc:
            await websocket.send_json({'type': 'error', 'message': exc.detail})
        reader = hub.acquire(camera, owner, state['mode'])
        await websocket.send_json({'type': 'status', **reader.snapshot(), 'mode': state['mode'][0]})

        async def receive():
            while True:
                message = json.loads(await websocket.receive_text())
                if message.get('type') == 'mode':
                    try:
                        state['mode'] = requested(message)
                        hub.set_mode(reader, owner, state['mode'])
                    except HTTPException as exc:
                        await websocket.send_json({'type': 'error', 'message': exc.detail})
                    await websocket.send_json({'type': 'status', **reader.snapshot(), 'mode': state['mode'][0]})

        async def send():
            sent_seq, sent_analysis, last_status, last_send = 0, None, 0.0, 0.0
            while True:
                async with reader.changed:
                    try:
                        await asyncio.wait_for(reader.changed.wait(), 1)
                    except asyncio.TimeoutError:
                        pass
                frame = reader.frame
                now = monotonic()
                if frame and frame.seq != sent_seq and now - last_send >= 1 / settings.monitor_max_fps:
                    sent_seq, last_send = frame.seq, now
                    await websocket.send_json({'type': 'frame', 'seq': frame.seq, 'width': frame.width, 'height': frame.height,
                                               'timestamp': frame.timestamp, 'acquisition_ms': round(frame.acquisition_ms, 1),
                                               'fps': round(reader.fps, 2), 'source': reader.source})
                    await websocket.send_bytes(frame.data)
                shown = view(reader.analysis, *state['mode'])
                if shown is not None and shown['seq'] != sent_analysis:
                    sent_analysis = shown['seq']
                    await websocket.send_json(shown)
                if now - last_status >= 1:
                    last_status = now
                    await websocket.send_json({'type': 'status', **reader.snapshot(), 'mode': state['mode'][0]})

        tasks = [asyncio.create_task(receive()), asyncio.create_task(send())]
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
    except WebSocketDisconnect:
        pass
    finally:
        if reader is not None:
            hub.release(camera_id, owner)
