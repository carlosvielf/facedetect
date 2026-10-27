import asyncio
import cv2
import httpx
import numpy as np
import pytest
from sqlalchemy import select
from app import camera as camera_module
from app.camera import CameraHub, jpeg_size, split_mjpeg
from app.config import settings
from app.db import SessionLocal
from app.models import AttendanceSession, Camera
from app.monitor import classify, view
from conftest import headers

JPEG = cv2.imencode('.jpg', np.full((240, 320, 3), 90, np.uint8))[1].tobytes()
BOUNDARY = b'123456789000000000000987654321'


def part(data):
    return b'\r\n--' + BOUNDARY + b'\r\nContent-Type: image/jpeg\r\nContent-Length: %d\r\n\r\n' % len(data) + data


def fake_camera(monkeypatch, stream_body=None, calls=None):
    """Route the hub's HTTP client to an in-memory CameraWebServer."""
    calls = calls if calls is not None else []

    def respond(request):
        calls.append(request.url.path)
        if request.url.path == '/stream' and stream_body is not None:
            return httpx.Response(200, headers={'content-type': 'multipart/x-mixed-replace;boundary=' + BOUNDARY.decode()}, content=stream_body)
        if request.url.path == '/capture':
            return httpx.Response(200, headers={'content-type': 'image/jpeg'}, content=JPEG)
        if request.url.path == '/status':
            return httpx.Response(200, json={'framesize': 10, 'quality': 10})
        raise httpx.ConnectError('offline', request=request)

    monkeypatch.setattr(camera_module, 'client', lambda: httpx.AsyncClient(transport=httpx.MockTransport(respond)))
    return calls


def test_jpeg_validation_detects_truncated_frames():
    assert jpeg_size(JPEG) == (320, 240)
    assert jpeg_size(JPEG[:-300]) is None
    assert jpeg_size(b'<html>error</html>' * 20) is None


def test_mjpeg_parser_handles_split_chunks():
    stream = part(JPEG) + part(JPEG[:-300]) + part(JPEG)
    buffer, frames = bytearray(), []
    for index in range(0, len(stream), 97):
        buffer.extend(stream[index:index + 97])
        frames += split_mjpeg(buffer, BOUNDARY)
    assert len(frames) == 3
    assert [jpeg_size(frame) is not None for frame in frames] == [True, False, True]


def test_hub_shares_one_reader_counts_corruption_and_falls_back(monkeypatch):
    calls = fake_camera(monkeypatch, part(JPEG) + part(JPEG[:-300]) + part(JPEG))
    monkeypatch.setattr(settings, 'camera_idle_seconds', 0)
    monkeypatch.setattr(settings, 'monitor_max_fps', 30)

    async def run():
        hub = CameraHub()
        camera = Camera(id=7, name='Cam', host='192.168.1.100')
        first = hub.acquire(camera, 'viewer-a')
        second = hub.acquire(camera, 'session:1')
        assert first is second
        frame = await first.latest(max_age=5, wait=2)
        assert frame is not None and (frame.width, frame.height) == (320, 240)
        # Stream ended -> reader reconnects through /capture instead of failing.
        for _ in range(100):
            if first.source == 'capture' and first.frames >= 3:
                break
            await asyncio.sleep(.05)
        assert first.corrupted == 1
        assert first.source == 'capture'
        assert calls.count('/stream') == 1
        hub.release(7, 'viewer-a')
        hub.release(7, 'session:1')
        await asyncio.sleep(.2)
        assert first.state == 'disconnected'
        assert first.task.done()
        await hub.close()
    asyncio.run(run())


def test_overlay_states_and_identity_visibility(monkeypatch):
    monkeypatch.setattr(settings, 'threshold', .6)
    monkeypatch.setattr(settings, 'uncertain_band', .1)
    a = np.array([1., 0., 0.], np.float32)
    b = np.array([0., 1., 0.], np.float32)
    near = np.array([.55, .835, 0.], np.float32)  # ~0.55 to a: below threshold, inside the uncertain band
    face = lambda vector: {'bbox': [0, 0, 90, 90], 'confidence': .78, 'issue': None if vector is not None else 'too_small', 'embedding': vector}
    candidates, names = [(1, a)], {1: 'Ana'}
    assert classify(face(a), candidates, names)['state'] == 'recognized'
    assert classify(face(a), candidates, names)['name'] == 'Ana'
    uncertain = classify(face(near / np.linalg.norm(near)), candidates, names)
    assert uncertain['state'] == 'uncertain' and uncertain['name'] is None
    assert classify(face(b), candidates, names)['state'] == 'unknown'
    assert classify(face(None), candidates, names)['state'] == 'detected'
    assert classify(face(a), None, {})['state'] == 'detected'
    analysis = {'seq': 1, 'mode': 'recognition', 'session_id': 5, 'faces': [classify(face(a), candidates, names)]}
    assert view(analysis, 'recognition', 5)['faces'][0]['name'] == 'Ana'
    hidden = view(analysis, 'detection', None)['faces'][0]
    assert hidden['name'] is None and hidden['similarity'] is None and hidden['state'] == 'detected'
    assert view(analysis, 'recognition', 6)['faces'][0]['name'] is None
    assert view(analysis, 'preview', None) is None


def camera_id():
    with SessionLocal() as db:
        return db.scalar(select(Camera.id))


def test_websocket_requires_auth_and_streams_preview(setup, monkeypatch):
    client, values = setup
    fake_camera(monkeypatch)
    monkeypatch.setattr(settings, 'camera_source', 'capture')
    with client.websocket_connect(f'/api/cameras/{camera_id()}/ws') as socket:
        socket.send_json({'type': 'auth', 'token': 'invalid'})
        assert socket.receive_json()['type'] == 'error'
    with client.websocket_connect(f'/api/cameras/{camera_id()}/ws') as socket:
        socket.send_json({'type': 'auth', 'token': values['teacher'], 'mode': 'preview'})
        assert socket.receive_json()['type'] == 'status'
        message = socket.receive_json()
        while message['type'] != 'frame':
            message = socket.receive_json()
        assert (message['width'], message['height']) == (320, 240)
        assert socket.receive_bytes() == JPEG


def test_recognition_mode_requires_running_authorized_session(setup, monkeypatch):
    client, values = setup
    fake_camera(monkeypatch)
    monkeypatch.setattr(settings, 'camera_source', 'capture')
    for token, expected in [('teacher', 'Inicie a captura'), ('outsider', 'Chamada não encontrada')]:
        with client.websocket_connect(f'/api/cameras/{camera_id()}/ws') as socket:
            socket.send_json({'type': 'auth', 'token': values[token], 'mode': 'recognition', 'session_id': values['session']})
            error = socket.receive_json()
            assert error['type'] == 'error' and expected in error['message']
            assert socket.receive_json()['mode'] == 'preview'


def test_connection_test_reports_each_step(setup, monkeypatch):
    client, values = setup
    fake_camera(monkeypatch, part(JPEG) + part(JPEG))
    report = client.post(f'/api/cameras/{camera_id()}/test', headers=headers(values)).json()
    assert report['status'] == 'ok'
    assert [s['name'] for s in report['steps']] == ['status', 'capture', 'stream']
    assert (report['width'], report['height'], report['framesize_label']) == (320, 240, 'VGA 640×480')
    offline = lambda: httpx.AsyncClient(transport=httpx.MockTransport(lambda r: (_ for _ in ()).throw(httpx.ConnectTimeout('t', request=r))))
    monkeypatch.setattr(camera_module, 'client', offline)
    report = client.post(f'/api/cameras/{camera_id()}/test', headers=headers(values)).json()
    assert report['status'] == 'disconnected'
    assert 'mesma rede' in report['steps'][0]['detail']
    response = client.get(f'/api/cameras/{camera_id()}/snapshot', headers=headers(values))
    assert response.status_code == 503


def test_snapshot_returns_valid_jpeg(setup, monkeypatch):
    client, values = setup
    fake_camera(monkeypatch)
    response = client.get(f'/api/cameras/{camera_id()}/snapshot', headers=headers(values))
    assert response.status_code == 200
    assert response.headers['x-frame-width'] == '320'
    assert response.content == JPEG


def test_diagnostics_never_report_untested_checks_as_ok(setup):
    client, values = setup
    assert client.get('/api/live').json() == {'status': 'ok'}
    assert client.get('/api/diagnostics', headers=headers(values)).status_code == 403
    checks = client.get('/api/diagnostics', headers=headers(values, 'admin')).json()['checks']
    from app.diagnostics import results
    for key in results:
        results[key]['status'] = 'not_tested'
    checks = client.get('/api/diagnostics', headers=headers(values, 'admin')).json()['checks']
    assert {c['status'] for c in checks} == {'not_tested'}
    ran = client.post('/api/diagnostics/run?check=api', headers=headers(values, 'admin')).json()
    assert ran[0]['status'] == 'ok'
    # SQLite test DB has no alembic_version table: this must surface as an error, not "ok".
    ran = client.post('/api/diagnostics/run?check=database', headers=headers(values, 'admin')).json()
    assert ran[0]['status'] == 'error'
    assert client.get('/api/ready').status_code == 503
    assert client.post('/api/diagnostics/run?check=nope', headers=headers(values, 'admin')).status_code == 404
