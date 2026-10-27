"""Real UniFace inference (tests A-F, H) on public-domain scikit-image portraits.

Skipped when scikit-image is missing or the weights cannot be loaded (offline first run).
These verify the integration, not accuracy on your classroom; see scripts/calibrate.py.
"""
import cv2
import numpy as np
import pytest
from app.recognition import decode, match, recognizer

data = pytest.importorskip('skimage.data')


@pytest.fixture(scope='module')
def images():
    try:
        recognizer.load()
    except Exception as exc:
        pytest.skip(f'Modelos UniFace indisponíveis: {exc}')
    astronaut = cv2.cvtColor(data.astronaut(), cv2.COLOR_RGB2BGR)
    return {'astronaut': astronaut, 'astronaut_qvga': cv2.resize(astronaut, (320, 240)),
            'other_person': cv2.cvtColor(data.camera(), cv2.COLOR_GRAY2BGR)}


def embedding(image):
    faces = [f for f in recognizer.inspect(image, min_size=1) if f['embedding'] is not None]
    assert len(faces) == 1
    return faces[0]['embedding']


def test_detection_on_static_image(images):
    faces = recognizer.inspect(images['astronaut'], embeddings=False)
    assert len(faces) == 1
    x1, y1, x2, y2 = faces[0]['bbox']
    assert 150 < x1 < x2 < 300 and 30 < y1 < y2 < 220
    # Detection score of a clear frontal face is below the former hard-coded 0.8 gate.
    assert faces[0]['confidence'] >= .6


def test_embeddings_are_valid_unit_vectors(images):
    vector = embedding(images['astronaut'])
    assert vector.shape == (512,) and np.isfinite(vector).all()
    assert abs(np.linalg.norm(vector) - 1) < 1e-4


def test_similarity_same_vs_different_person(images):
    reference = embedding(images['astronaut'])
    same = float(reference @ embedding(images['astronaut_qvga']))
    other = float(reference @ embedding(images['other_person']))
    assert same > .7 and other < .3


def test_unknown_person_is_not_matched(images):
    enrolled = [(1, embedding(images['astronaut']))]
    student_id, _, state = match(embedding(images['other_person']), enrolled, .55, .08)
    assert student_id is None and state == 'unknown'
    student_id, _, state = match(embedding(images['astronaut_qvga']), enrolled, .55, .08)
    assert student_id == 1 and state == 'candidate'


def test_full_pipeline_from_jpeg_bytes(images):
    jpeg = cv2.imencode('.jpg', images['astronaut'])[1].tobytes()
    assert decode(jpeg).shape == (512, 512, 3)
    assert len(recognizer.analyze(jpeg, individual=True)) == 1
