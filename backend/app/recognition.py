import threading
import cv2
import numpy as np
from cryptography.fernet import Fernet
from app.config import settings

MODEL = 'uniface-4.1.0/scrfd-500m/arcface-mnet'
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


class RecognitionEngine:
    def __init__(self):
        self.analyzer = None
        self.lock = threading.Lock()

    def analyze(self, content: bytes, individual=False):
        if len(content) > 8_000_000:
            raise ValueError('Imagem excede 8 MB')
        image = cv2.imdecode(np.frombuffer(content, np.uint8), cv2.IMREAD_COLOR)
        if image is None or image.shape[0] * image.shape[1] > 16_000_000:
            raise ValueError('JPEG inválido ou resolução excessiva')
        with self.lock:
            if self.analyzer is None:
                from uniface import FaceAnalyzer
                from uniface.constants import SCRFDWeights, ArcFaceWeights
                from uniface.detection import SCRFD
                from uniface.recognition import ArcFace
                self.analyzer = FaceAnalyzer(
                    detector=SCRFD(model_name=SCRFDWeights.SCRFD_500M_KPS),
                    recognizer=ArcFace(model_name=ArcFaceWeights.MNET),
                )
            faces = self.analyzer.analyze(image)
        if individual and len(faces) != 1:
            raise ValueError('Envie uma imagem com exatamente um rosto')
        results = []
        for face in faces:
            x1, y1, x2, y2 = [int(v) for v in face.bbox]
            crop = image[max(0, y1):max(0, y2), max(0, x1):max(0, x2)]
            valid = (face.embedding is not None and float(face.confidence) >= .8
                     and min(x2-x1, y2-y1) >= 80 and crop.size > 0)
            if valid:
                valid = cv2.Laplacian(cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY), cv2.CV_64F).var() >= 40
            if not valid:
                if individual:
                    raise ValueError('Rosto pequeno, desfocado ou com confiança insuficiente')
                continue
            results.append(normalize(face.embedding))
        return results


recognizer = RecognitionEngine()


def encrypt(vector):
    return cipher.encrypt(normalize(vector).tobytes())


def decrypt(value):
    return np.frombuffer(cipher.decrypt(value), dtype=np.float32)
