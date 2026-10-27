"""Similarity threshold calibration from authorized photos.

Folder layout (one subfolder per person, at least 2 photos each, ideally taken
by the classroom ESP32 at the real distance/lighting):

    calibracao/
      pessoa01/ foto1.jpg foto2.jpg ...
      pessoa02/ ...

Usage (from backend/):
    ..\\.venv\\Scripts\\python.exe scripts\\calibrate.py C:\\caminho\\calibracao [--target-far 0.001]

Prints genuine/impostor similarity distributions and, per threshold, the false
accept rate (FAR) and false reject rate (FRR) measured on THIS sample. Nothing
is written to the database. A threshold chosen here is only as good as the
sample: use many people and real conditions, and repeat after changing the
camera, resolution or model.
"""
import argparse
import itertools
import sys
from pathlib import Path
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.recognition import recognizer  # noqa: E402


def main(args):
    people = {}
    for folder in sorted(p for p in Path(args.folder).iterdir() if p.is_dir()):
        vectors = []
        for photo in sorted(folder.iterdir()):
            if photo.suffix.lower() not in ('.jpg', '.jpeg', '.png'):
                continue
            try:
                found = recognizer.analyze(photo.read_bytes())
            except ValueError as exc:
                print(f'Ignorada {photo.name}: {exc}')
                continue
            if len(found) != 1:
                print(f'Ignorada {folder.name}/{photo.name}: {len(found)} rosto(s) utilizável(is)')
                continue
            vectors.append(found[0])
        if len(vectors) >= 2:
            people[folder.name] = vectors
    if len(people) < 2:
        print('São necessárias pelo menos 2 pessoas com 2 fotos utilizáveis cada.')
        return 1
    genuine = [float(a @ b) for vs in people.values() for a, b in itertools.combinations(vs, 2)]
    impostor = [float(a @ b) for (_, va), (_, vb) in itertools.combinations(people.items(), 2) for a in va for b in vb]
    genuine, impostor = np.array(genuine), np.array(impostor)
    print(f'\nPessoas: {len(people)}  pares genuínos: {len(genuine)}  pares impostores: {len(impostor)}')
    for name, values in (('Genuínos', genuine), ('Impostores', impostor)):
        print(f'{name:11s} min {values.min():.3f}  p05 {np.percentile(values, 5):.3f}  mediana {np.median(values):.3f}  '
              f'p95 {np.percentile(values, 95):.3f}  max {values.max():.3f}')
    print('\nLimiar   FAR (impostor aceito)   FRR (genuíno rejeitado)')
    chosen = None
    for threshold in np.arange(.20, .85, .05):
        far, frr = float((impostor >= threshold).mean()), float((genuine < threshold).mean())
        print(f'{threshold:5.2f}   {far:8.4f}                {frr:8.4f}')
        if chosen is None and far <= args.target_far:
            chosen = threshold
    print(f'\nMaior similaridade entre pessoas diferentes nesta amostra: {impostor.max():.3f}')
    if chosen is not None:
        print(f'Menor limiar com FAR ≤ {args.target_far} nesta amostra: {chosen:.2f} (FRR {float((genuine < chosen).mean()):.3f}).')
        print('Defina THRESHOLD no .env com folga acima do maior impostor e valide novamente com outras pessoas.')
    if len(impostor) < 1 / max(args.target_far, 1e-9):
        print(f'Atenção: {len(impostor)} pares impostores não bastam para estimar FAR {args.target_far} com confiança.')
    return 0


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('folder')
    parser.add_argument('--target-far', type=float, default=.001)
    sys.exit(main(parser.parse_args()))
