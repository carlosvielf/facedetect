"""UniFace integration tests A-H, printed as a report.

Usage (from backend/):
    ..\\.venv\\Scripts\\python.exe scripts\\diagnose_uniface.py [--camera-host IP] [--photo aluno.jpg --registration 2026001]

A-F use a public-domain scikit-image portrait. D needs the ESP32. G needs an
authorized photo of an enrolled student (read in memory, never stored). H checks
that a person who is not enrolled (the second public-domain image) does not
match anyone enrolled in the database.
"""
import argparse
import sys
from pathlib import Path
from time import perf_counter

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

results = {}


def record(test, status, detail):
    results[test] = status
    print(f'Teste {test} [{status}] {detail}')


def main(args):
    started = perf_counter()
    try:
        import uniface
        import onnxruntime
        record('A', 'OK', f'uniface {uniface.__version__}, onnxruntime {onnxruntime.__version__}, '
                          f'providers {onnxruntime.get_available_providers()}, python {sys.version.split()[0]} ({sys.executable})')
    except Exception as exc:
        record('A', 'ERRO', f'importação falhou: {exc}')
        return 1
    import cv2
    import numpy as np
    from app.config import settings
    from app.recognition import MODEL, decode, match, recognizer, usable
    started = perf_counter()
    try:
        recognizer.load()
        record('B', 'OK', f'{MODEL} carregado em {(perf_counter()-started):.2f} s; pesos {recognizer.info()["weights"]}')
    except Exception as exc:
        record('B', 'ERRO', f'{exc} (primeira execução precisa de internet para baixar os pesos)')
        return 1
    from skimage import data
    astronaut = cv2.cvtColor(data.astronaut(), cv2.COLOR_RGB2BGR)
    other = cv2.cvtColor(data.camera(), cv2.COLOR_GRAY2BGR)
    started = perf_counter()
    faces = recognizer.inspect(astronaut, min_size=1)
    record('C', 'OK' if len(faces) == 1 else 'ERRO',
           f'{len(faces)} rosto(s) em {(perf_counter()-started)*1000:.0f} ms; ' + ', '.join(f"bbox={f['bbox']} det={f['confidence']:.3f}" for f in faces))
    if args.camera_host:
        import httpx
        from app.camera import jpeg_size
        try:
            content = httpx.get(f'http://{args.camera_host}/capture', timeout=5, trust_env=False).content
            if jpeg_size(content) is None:
                raise ValueError('JPEG inválido')
            live = recognizer.inspect(decode(content))
            record('D', 'OK', f'frame {jpeg_size(content)}: {len(live)} rosto(s); ' +
                   ', '.join(f"det={f['confidence']:.2f} {f['issue'] or 'utilizável'}" for f in live))
        except Exception as exc:
            record('D', 'ERRO', f'captura da ESP32 falhou: {exc}')
    else:
        record('D', 'NÃO TESTADO', 'informe --camera-host IP')
    vectors = [f['embedding'] for f in faces if f['embedding'] is not None]
    if vectors and vectors[0].shape == (512,) and abs(float(np.linalg.norm(vectors[0])) - 1) < 1e-4:
        record('E', 'OK', 'embedding 512-d, finito, norma L2 = 1')
    else:
        record('E', 'ERRO', 'embedding ausente ou inválido')
        return 1
    reference = vectors[0]
    same = float(reference @ recognizer.inspect(cv2.resize(astronaut, (320, 240)), min_size=1)[0]['embedding'])
    different = float(reference @ recognizer.inspect(other, min_size=1)[0]['embedding'])
    record('F', 'OK' if same > different else 'ERRO',
           f'cosseno mesma pessoa (512px vs 320×240) = {same:.3f}; pessoas diferentes = {different:.3f}; limiar atual {settings.threshold}')
    if args.photo and args.registration:
        from sqlalchemy import select
        from app.db import SessionLocal
        from app.models import FaceEmbedding, Student
        with SessionLocal() as db:
            student = db.scalar(select(Student).where(Student.registration == args.registration))
            rows = db.execute(select(FaceEmbedding.student_id, FaceEmbedding.encrypted).where(FaceEmbedding.model == MODEL)).all()
        candidates, invalid = usable((row.student_id, row.encrypted) for row in rows)
        probe = recognizer.analyze(Path(args.photo).read_bytes())
        if student is None or not probe:
            record('G', 'ERRO', 'aluno inexistente ou nenhum rosto utilizável na foto')
        else:
            found, score, state = match(probe[0], candidates, settings.threshold, settings.ambiguity_margin)
            record('G', 'OK' if found == student.id else 'ERRO', f'resultado={state}, aluno={found}, esperado={student.id}, similaridade={score:.3f}')
    else:
        record('G', 'NÃO TESTADO', 'informe --photo e --registration de um aluno cadastrado com autorização')
    try:
        from sqlalchemy import select
        from app.db import SessionLocal
        from app.models import FaceEmbedding
        with SessionLocal() as db:
            rows = db.execute(select(FaceEmbedding.student_id, FaceEmbedding.encrypted).where(FaceEmbedding.model == MODEL)).all()
        candidates, invalid = usable((row.student_id, row.encrypted) for row in rows)
        if invalid:
            print(f'ATENÇÃO: {invalid} embedding(s) no banco não descriptografam com a EMBEDDING_KEY atual '
                  '(chave trocada/perdida). Restaure a chave original ou exclua e recadastre essas biometrias.')
        if not candidates:
            record('H', 'NÃO TESTADO', f'nenhum embedding utilizável no banco ({invalid} ilegível(is))')
        else:
            stranger = recognizer.inspect(other, min_size=1)[0]['embedding']
            found, score, state = match(stranger, candidates, settings.threshold, settings.ambiguity_margin)
            record('H', 'OK' if found is None else 'ERRO',
                   f'pessoa não cadastrada contra {len(candidates)} embedding(s): {state}, maior similaridade {score:.3f}')
    except Exception as exc:
        record('H', 'ERRO', f'não foi possível ler o banco: {exc}')
    return 1 if 'ERRO' in results.values() else 0


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--camera-host')
    parser.add_argument('--photo')
    parser.add_argument('--registration')
    sys.exit(main(parser.parse_args()))
