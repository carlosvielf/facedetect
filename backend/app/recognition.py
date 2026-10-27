import threading
from pathlib import Path
from time import perf_counter
import cv2
import numpy as np
from cryptography.fernet import Fernet
from app.config import settings

MODEL = 'uniface-4.1.0/scrfd-500m/arcface-mnet'
MODEL_FILES = ('scrfd_500m.onnx', 'arcface_mnet.onnx')
cipher = Fernet(settings.embedding_key.encode())


def normalize(vector):
    value = np.asarray(vector, dtype=np.float32).reshape(-1)
    norm = np.linalg.norm(value)
    if not np.isfinite(value).all() or norm < 1e-8:
        raise ValueError('Embedding inválido')
    return value / norm


def match(vector, candidates, threshold, margin):
    scores = {}
    for student_id, reference in candidates:
        score = float(np.dot(normalize(vector), normalize(reference)))
        scores[student_id] = max(score, scores.get(student_id, -1))
    ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    if not ranked:
        return None, None, 'unknown'
    student_id, score = ranked[0]
    if score < threshold:
        return None, score, 'unknown'
    if len(ranked) > 1 and score - ranked[1][1] < margin:
        return None, score, 'ambiguous'
    return student_id, score, 'candidate'


def decode(content: bytes):
    """JPEG/PNG bytes -> BGR array (OpenCV channel order, as UniFace expects)."""
    if len(content) > 8_000_000:
        raise ValueError('Imagem excede 8 MB')
    image = cv2.imdecode(np.frombuffer(content, np.uint8), cv2.IMREAD_COLOR)
    if image is None or image.shape[0] * image.shape[1] > 16_000_000:
        raise ValueError('JPEG inválido ou resolução excessiva')
    return image


class RecognitionEngine:
    def __init__(self):
        self.analyzer = None
        self.lock = threading.Lock()
        self.load_error = None
        self.load_seconds = None

    def _load(self):
        # Caller holds self.lock. Weights are downloaded to ~/.uniface/models on first use.
        if self.analyzer is not None:
            return
        started = perf_counter()
        try:
            from uniface import FaceAnalyzer
            from uniface.constants import SCRFDWeights, ArcFaceWeights
            from uniface.detection import SCRFD
            from uniface.recognition import ArcFace
            self.analyzer = FaceAnalyzer(
                detector=SCRFD(model_name=SCRFDWeights.SCRFD_500M_KPS),
                recognizer=ArcFace(model_name=ArcFaceWeights.MNET),
            )
            self.load_error = None
        except Exception as exc:
            self.load_error = f'{type(exc).__name__}: {exc}'
            raise
        self.load_seconds = perf_counter() - started

    def load(self):
        with self.lock:
            self._load()

    def quality(self, image, face, min_size):
        x1, y1, x2, y2 = [int(v) for v in face.bbox]
        crop = image[max(0, y1):max(0, y2), max(0, x1):max(0, x2)]
        if float(face.confidence) < settings.min_detection_confidence:
            return 'low_confidence'
        if min(x2-x1, y2-y1) < min_size or crop.size == 0:
            return 'too_small'
        if cv2.Laplacian(cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY), cv2.CV_64F).var() < settings.min_sharpness:
            return 'blurry'
        return None

    def inspect(self, image, embeddings=True, min_size=None):
        """All detected faces with bbox, detection score and, when usable, a normalized embedding.

        embeddings=False runs only the detector (no ArcFace cost) for detection-only preview.
        """
        min_size = settings.min_face_size if min_size is None else min_size
        with self.lock:
            self._load()
            if embeddings:
                faces = self.analyzer.analyze(image)
            else:
                faces = self.analyzer.detector.detect(image)
        result = []
        for face in faces:
            issue = self.quality(image, face, min_size)
            embedding = getattr(face, 'embedding', None) if embeddings else None
            if embeddings and issue is None and embedding is None:
                issue = 'no_embedding'
            result.append({'bbox': [int(v) for v in face.bbox], 'confidence': float(face.confidence),
                           'issue': issue, 'embedding': normalize(embedding) if embedding is not None and issue is None else None})
        return result

    def analyze(self, content: bytes, individual=False):
        image = decode(content)
        faces = self.inspect(image, min_size=settings.enroll_min_face_size if individual else None)
        if individual and len(faces) != 1:
            raise ValueError('Envie uma imagem com exatamente um rosto')
        results = []
        for face in faces:
            if face['embedding'] is None:
                if individual:
                    raise ValueError('Rosto pequeno, desfocado ou com confiança insuficiente')
                continue
            results.append(face['embedding'])
        return results

    def info(self):
        try:
            import onnxruntime
            import uniface
            versions = {'uniface': uniface.__version__, 'onnxruntime': onnxruntime.__version__,
                        'providers': onnxruntime.get_available_providers()}
        except Exception as exc:
            versions = {'import_error': f'{type(exc).__name__}: {exc}'}
        folder = Path.home() / '.uniface' / 'models'
        return {**versions, 'model': MODEL, 'loaded': self.analyzer is not None, 'load_error': self.load_error,
                'load_seconds': self.load_seconds,
                'weights': {name: (folder / name).exists() for name in MODEL_FILES}}


recognizer = RecognitionEngine()


def encrypt(vector):
    return cipher.encrypt(normalize(vector).tobytes())


def decrypt(value):
    return np.frombuffer(cipher.decrypt(value), dtype=np.float32)


def usable(rows):
    """(student_id, vector) pairs that decrypt with the current EMBEDDING_KEY, plus the failure count.

    Embeddings encrypted with a lost/rotated key cannot be used; they are skipped
    (and reported by diagnostics) instead of aborting every recognition cycle.
    """
    from cryptography.fernet import InvalidToken
    candidates, invalid = [], 0
    for student_id, encrypted in rows:
        try:
            candidates.append((student_id, decrypt(encrypted)))
        except (InvalidToken, ValueError):
            invalid += 1
    return candidates, invalid
