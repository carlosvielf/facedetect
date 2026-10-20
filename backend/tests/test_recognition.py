import numpy as np
import pytest
from app.recognition import normalize, match, encrypt, decrypt, recognizer


def test_matching_rejects_unknown_and_ambiguous():
    a = np.array([1., 0., 0.])
    b = np.array([0., 1., 0.])
    assert match(a, [(1,a), (2,b)], .6, .08)[0] == 1
    assert match(a, [(1,b)], .6, .08)[2] == 'unknown'
    assert match(a, [(1,a), (2,a)], .6, .08)[2] == 'ambiguous'
    # Multiple reference photos of one student must not cause ambiguity.
    assert match(a, [(1,a), (1,a)], .6, .08)[0] == 1


def test_encryption_round_trip():
    vector = np.array([1.,2.,3.])
    encrypted = encrypt(vector)
    assert normalize(vector).tobytes() not in encrypted
    np.testing.assert_allclose(decrypt(encrypted), normalize(vector))
    with pytest.raises(ValueError):
        normalize([float('nan')])


def test_invalid_photo_does_not_initialize_model():
    with pytest.raises(ValueError):
        recognizer.analyze(b'not a jpeg', individual=True)
