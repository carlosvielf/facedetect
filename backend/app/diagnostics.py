"""Health, readiness and the admin diagnostics center.

Every check starts as "not_tested" and only becomes "ok" after it actually ran
and succeeded in this process.
"""
import asyncio
from datetime import datetime, timezone
from time import perf_counter, time
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy import select, text
from app.camera import hub, jpeg_size
from app.config import BACKEND, settings
from app.db import SessionLocal
from app.models import AttendanceSession, Camera, User
from app.recognition import decode, recognizer
from app.security import admin

router = APIRouter(prefix='/api')
started_at = time()
CHECKS = {
    'api': 'API FastAPI',
    'database': 'Banco PostgreSQL',
    'camera': 'Câmera ESP32-S3',
    'capture_service': 'Serviço de captura',
    'uniface': 'UniFace instalado',
    'models': 'Modelos de reconhecimento',
    'embeddings': 'Embeddings cadastrados',
    'frame_processing': 'Processamento de frames',
    'attendance_events': 'Serviço de eventos de chamada',
}
results = {key: {'key': key, 'label': label, 'status': 'not_tested', 'detail': 'Ainda não executado', 'ms': None, 'checked_at': None}
           for key, label in CHECKS.items()}


def migration_state():
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    config = Config(str(BACKEND / 'alembic.ini'))
    config.set_main_option('script_location', str(BACKEND / 'alembic'))
    head = ScriptDirectory.from_config(config).get_current_head()
    with SessionLocal() as db:
        db.execute(text('SELECT 1'))
        current = db.scalar(text('SELECT version_num FROM alembic_version'))
    return current, head


def check_database():
    current, head = migration_state()
    if current != head:
        return 'error', f'Migração {current} aplicada; esperado {head}. Execute alembic upgrade head.'
    return 'ok', f'Conectado; migração {current} (head)'


async def check_camera():
    from app.monitor import probe
    with SessionLocal() as db:
        cameras = list(db.scalars(select(Camera)))
    if not cameras:
        return 'not_tested', 'Nenhuma câmera cadastrada'
    reports = await asyncio.gather(*[probe(c.host, hub.get(c.id)) for c in cameras if c.host in settings.camera_hosts])
    if not reports:
        return 'error', 'Nenhuma câmera cadastrada está em CAMERA_HOSTS'
    lines = []
    for camera, report in zip([c for c in cameras if c.host in settings.camera_hosts], reports):
        steps = '; '.join(f"{s['name']}: {'ok' if s['ok'] else 'falha'} ({s['ms']} ms) {s['detail']}" for s in report['steps'])
        lines.append(f'{camera.name} [{camera.host}] → {steps}')
    statuses = {r['status'] for r in reports}
    status = 'ok' if statuses == {'ok'} else 'disconnected' if statuses == {'disconnected'} else 'error'
    return status, '\n'.join(lines)


def check_capture_service():
    from app.capture import capture
    age = time() - capture.heartbeat if capture.heartbeat else None
    if age is None:
        return 'error', 'O laço de captura não iniciou (o servidor foi iniciado com lifespan?)'
    if age > settings.interval_seconds * 3 + settings.camera_timeout * 2 + 5:
        return 'error', f'Último ciclo há {age:.0f} s; o laço pode estar travado'
    return 'ok', f'Último ciclo há {age:.1f} s'


def check_uniface():
    info = recognizer.info()
    if 'import_error' in info:
        return 'error', info['import_error']
    missing = [name for name, present in info['weights'].items() if not present]
    detail = f"uniface {info['uniface']}, onnxruntime {info['onnxruntime']}, providers {', '.join(info['providers'])}"
    if missing:
        detail += f". Pesos ausentes (serão baixados na primeira carga): {', '.join(missing)}"
    return 'ok', detail


def check_models():
    """Load the models and run detection + embedding on a public-domain portrait."""
    try:
        from skimage import data
        import cv2
        image = cv2.cvtColor(data.astronaut(), cv2.COLOR_RGB2BGR)
    except Exception:
        image = None
    started = perf_counter()
    recognizer.load()
    if image is None:
        return 'ok', f'Modelos carregados; imagem de teste indisponível, inferência não verificada ({recognizer.info()["model"]})'
    faces = recognizer.inspect(image, min_size=1)
    usable = [f for f in faces if f['embedding'] is not None]
    if not usable:
        return 'error', f'Modelos carregados, mas nenhum rosto com embedding na imagem de teste ({len(faces)} detectados)'
    return 'ok', (f'{len(faces)} rosto(s), embedding {usable[0]["embedding"].shape[0]}-d na imagem de teste '
                  f'em {(perf_counter() - started) * 1000:.0f} ms (detecção {faces[0]["confidence"]:.2f})')


def check_embeddings():
    from app.models import FaceEmbedding
    from app.recognition import MODEL, usable
    with SessionLocal() as db:
        rows = db.execute(select(FaceEmbedding.student_id, FaceEmbedding.encrypted, FaceEmbedding.model)).all()
    if not rows:
        return 'not_tested', 'Nenhuma biometria cadastrada'
    current = [(r.student_id, r.encrypted) for r in rows if r.model == MODEL]
    candidates, invalid = usable(current)
    other_model = len(rows) - len(current)
    detail = (f'{len(candidates)} utilizável(is) de {len(rows)}; {invalid} não descriptografa(m) com a EMBEDDING_KEY atual; '
              f'{other_model} de outro modelo')
    if invalid:
        detail += '. Restaure a EMBEDDING_KEY usada no cadastro ou exclua e recadastre essas biometrias.'
    return ('ok' if not invalid and candidates else 'error'), detail


async def check_frame_processing():
    from app.monitor import snapshot_bytes
    with SessionLocal() as db:
        cameras = [c for c in db.scalars(select(Camera)) if c.host in settings.camera_hosts]
    if not cameras:
        return 'not_tested', 'Nenhuma câmera autorizada cadastrada'
    errors = []
    for camera in cameras:
        try:
            data = await snapshot_bytes(camera)
        except Exception as exc:
            errors.append(f'{camera.name}: {exc}')
            continue
        started = perf_counter()
        image = await asyncio.to_thread(decode, data)
        faces = await asyncio.to_thread(recognizer.inspect, image, False)
        width, height = jpeg_size(data)
        return 'ok', (f'{camera.name}: frame {width}×{height} decodificado e analisado em '
                      f'{(perf_counter() - started) * 1000:.0f} ms; {len(faces)} rosto(s) detectado(s)')
    return 'disconnected', '; '.join(errors)


def check_attendance_events():
    from app.capture import capture
    with SessionLocal() as db:
        running = list(db.scalars(select(AttendanceSession.id).where(AttendanceSession.status == 'running')))
    if not running:
        return 'not_tested', 'Nenhuma chamada em andamento para observar'
    lines, status = [], 'ok'
    for session_id in running:
        info = capture.sessions.get(session_id)
        if info is None:
            lines.append(f'Chamada #{session_id}: aguardando o primeiro ciclo')
            status = 'not_tested' if status == 'ok' else status
        elif info['error']:
            lines.append(f'Chamada #{session_id}: {info["error"]}')
            status = 'error'
        else:
            lines.append(f'Chamada #{session_id}: último frame processado há {time() - info["ok_at"]:.0f} s, {info["faces"]} rosto(s) utilizável(is)')
    return status, '\n'.join(lines)


RUNNERS = {
    'api': lambda: ('ok', f'Respondendo há {time() - started_at:.0f} s'),
    'database': check_database,
    'camera': check_camera,
    'capture_service': check_capture_service,
    'uniface': check_uniface,
    'models': check_models,
    'embeddings': check_embeddings,
    'frame_processing': check_frame_processing,
    'attendance_events': check_attendance_events,
}


async def run_check(key):
    started = perf_counter()
    try:
        runner = RUNNERS[key]
        if asyncio.iscoroutinefunction(runner):
            status, detail = await asyncio.wait_for(runner(), 30)
        else:
            status, detail = await asyncio.to_thread(runner)
    except asyncio.TimeoutError:
        status, detail = 'error', 'Verificação excedeu 30 s'
    except Exception as exc:
        status, detail = 'error', f'{type(exc).__name__}: {exc}'
    results[key] = {**results[key], 'status': status, 'detail': detail, 'ms': round((perf_counter() - started) * 1000),
                    'checked_at': datetime.now(timezone.utc).isoformat()}
    return results[key]


@router.get('/live')
def live():
    return {'status': 'ok'}


@router.get('/ready')
def ready():
    try:
        current, head = migration_state()
    except Exception as exc:
        return JSONResponse({'status': 'error', 'database': f'{type(exc).__name__}: {exc}'}, status_code=503)
    ok = current == head and 'import_error' not in recognizer.info()
    return JSONResponse({'status': 'ok' if ok else 'error', 'migration': current, 'head': head,
                         'models_loaded': recognizer.analyzer is not None}, status_code=200 if ok else 503)


@router.get('/diagnostics')
def diagnostics(user: User = Depends(admin)):
    return {'checks': list(results.values()), 'config': {
        'camera_source': settings.camera_source, 'camera_stream_port': settings.camera_stream_port,
        'camera_hosts': settings.camera_hosts, 'cors_origins': settings.cors_origins,
        'threshold': settings.threshold, 'ambiguity_margin': settings.ambiguity_margin, 'uncertain_band': settings.uncertain_band,
        'min_detection_confidence': settings.min_detection_confidence, 'min_face_size': settings.min_face_size,
        'enroll_min_face_size': settings.enroll_min_face_size, 'inference_max_fps': settings.inference_max_fps},
        'model': recognizer.info(), 'readers': [reader.snapshot() for reader in hub.readers.values()]}


@router.post('/diagnostics/run')
async def run_diagnostics(check: str | None = None, user: User = Depends(admin)):
    if check is not None and check not in RUNNERS:
        raise HTTPException(404, 'Verificação desconhecida')
    keys = [check] if check else list(RUNNERS)
    # Sequential: camera tests must not compete for the single ESP32 stream.
    return [await run_check(key) for key in keys]
