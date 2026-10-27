import asyncio
import httpx
import numpy as np
from sqlalchemy import select
from app.capture import CaptureService
from app.config import settings
from app.db import SessionLocal
from app.models import AttendanceSession, Camera, FaceEmbedding, AttendanceRecord
from app.recognition import MODEL, encrypt
import cv2

JPEG = cv2.imencode('.jpg', np.full((240, 320, 3), 90, np.uint8))[1].tobytes()


def test_capture_http_contract(setup):
    async def run():
        service = CaptureService()
        transport = httpx.MockTransport(lambda request: httpx.Response(200, headers={'content-type':'image/jpeg'}, content=JPEG))
        async with httpx.AsyncClient(transport=transport) as client:
            assert await service.fetch(client, Camera(host='192.168.1.100')) == JPEG
    asyncio.run(run())


def test_preview_and_recognition_serialize_camera_requests(setup):
    async def run():
        service = CaptureService()
        active = 0
        maximum = 0
        calls = 0

        async def respond(request):
            nonlocal active, maximum, calls
            assert request.headers['connection'] == 'close'
            calls += 1
            active += 1
            maximum = max(maximum, active)
            try:
                await asyncio.sleep(.01)
                if calls == 1:
                    raise httpx.RemoteProtocolError('Disconnected', request=request)
                return httpx.Response(200, headers={'content-type': 'image/jpeg'}, content=JPEG)
            finally:
                active -= 1

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            results = await asyncio.gather(*[
                service.fetch(client, Camera(host='192.168.1.100')) for _ in range(3)
            ])
        assert results == [JPEG] * 3
        assert maximum == 1
        assert calls == 4
    asyncio.run(run())


def test_capture_rejects_non_jpeg_and_timeout(setup):
    import pytest
    async def run():
        service = CaptureService()
        transport = httpx.MockTransport(lambda request: httpx.Response(200, headers={'content-type':'text/html'}, content=b'error'))
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(ValueError):
                await service.fetch(client, Camera(host='192.168.1.100'))
        def fail(request):
            raise httpx.ReadTimeout('Camera timeout', request=request)
        async with httpx.AsyncClient(transport=httpx.MockTransport(fail)) as client:
            with pytest.raises(httpx.ReadTimeout):
                await service.fetch(client, Camera(host='192.168.1.100'))
    asyncio.run(run())


def test_temporal_confirmation_and_no_duplicates(setup, monkeypatch):
    _, values = setup
    vector = np.array([1.,0.,0.])
    with SessionLocal() as db:
        db.get(AttendanceSession, values['session']).status = 'running'
        db.add(FaceEmbedding(student_id=values['student'], model=MODEL, encrypted=encrypt(vector), authorization='Test authorization only'))
        db.commit()
    service = CaptureService()
    clock = iter([0.,2.,4.,6.])
    monkeypatch.setattr('app.capture.monotonic', lambda: next(clock))
    for index in range(settings.observations):
        service.process(values['session'], [vector, vector])
        with SessionLocal() as db:
            records = list(db.scalars(select(AttendanceRecord)))
            assert len(records) == (1 if index == settings.observations-1 else 0)
    service.process(values['session'], [vector])
    with SessionLocal() as db:
        records = list(db.scalars(select(AttendanceRecord)))
        assert len(records) == 1
        assert records[0].status == 'provisional'


def test_embedding_from_lost_key_is_skipped_not_fatal(setup):
    from cryptography.fernet import Fernet
    _, values = setup
    vector = np.array([1., 0., 0.], np.float32)
    with SessionLocal() as db:
        db.get(AttendanceSession, values['session']).status = 'running'
        foreign = Fernet(Fernet.generate_key()).encrypt(vector.tobytes())
        db.add(FaceEmbedding(student_id=values['student'], model=MODEL, encrypted=foreign, authorization='Test authorization only'))
        db.commit()
    CaptureService().process(values['session'], [vector])
    with SessionLocal() as db:
        assert db.scalar(select(AttendanceRecord)) is None
