from sqlalchemy import select, func
from app.db import SessionLocal
from app.models import AttendanceRecord, AuditLog
from conftest import headers


def test_authorization_and_manual_confirmation(setup):
    client, values = setup
    path = f'/api/attendance/sessions/{values["session"]}'
    assert client.get(path+'/recognitions', headers=headers(values, 'outsider')).status_code == 404
    assert client.post('/api/students', json={'name':'New Student','registration':'002'}, headers=headers(values)).status_code == 403
    payload = {'student_id':values['student'], 'status':'present'}
    for _ in range(2):
        assert client.put(path+'/records', json=payload, headers=headers(values)).status_code == 200
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(AttendanceRecord)) == 1
        assert db.scalar(select(func.count()).select_from(AuditLog)) == 2
    assert client.post(path+'/confirm', headers=headers(values)).status_code == 200
    assert client.put(path+'/records', json=payload, headers=headers(values)).status_code == 409
    assert client.post(path+'/start', headers=headers(values)).status_code == 409


def test_provisional_cannot_be_finalized(setup):
    client, values = setup
    with SessionLocal() as db:
        db.add(AttendanceRecord(session_id=values['session'], student_id=values['student']))
        db.commit()
    response = client.post(f'/api/attendance/sessions/{values["session"]}/confirm', headers=headers(values))
    assert response.status_code == 409


def test_camera_allowlist_and_export(setup):
    client, values = setup
    assert client.post('/api/cameras', json={'name':'Bad Camera','host':'169.254.169.254'}, headers=headers(values,'admin')).status_code == 422
    assert client.get(f'/api/attendance/export?session_id={values["session"]}', headers=headers(values,'outsider')).status_code == 404
    for format in ['csv','xlsx','pdf']:
        result = client.get(f'/api/attendance/export?session_id={values["session"]}&format={format}', headers=headers(values))
        assert result.status_code == 200
        assert len(result.content) > 20


def test_student_outside_roster_cannot_be_marked(setup):
    client, values = setup
    result = client.put(f'/api/attendance/sessions/{values["session"]}/records', json={'student_id':999,'status':'present'}, headers=headers(values))
    assert result.status_code == 422
