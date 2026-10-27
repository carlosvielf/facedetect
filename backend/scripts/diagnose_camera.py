"""ESP32-S3 CameraWebServer diagnostics: reachability, /status, /capture, /stream.

Usage (from backend/):
    ..\\.venv\\Scripts\\python.exe scripts\\diagnose_camera.py --host 10.64.70.86 [--save frame.jpg] [--detect]

Exit code 0 only when every HTTP test passed. Close browser tabs showing the
camera stream first: port 81 serves a single client.
"""
import argparse
import asyncio
import json
import statistics
import sys
from pathlib import Path
from time import perf_counter
import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.camera import describe, jpeg_size, split_mjpeg  # noqa: E402


def report(name, ok, detail):
    print(f"[{'OK ' if ok else 'ERRO'}] {name}: {detail}")
    return ok


async def main(args):
    results = []
    async with httpx.AsyncClient(timeout=args.timeout, trust_env=False) as http:
        started = perf_counter()
        try:
            response = await http.get(f'http://{args.host}/')
            results.append(report('GET /', response.status_code == 200,
                                  f'HTTP {response.status_code}, {len(response.content)} bytes em {(perf_counter()-started)*1000:.0f} ms'))
        except Exception as exc:
            report('GET /', False, describe(exc))
            print('\nA câmera não respondeu. Verifique no monitor serial (115200 baud) se aparece "Camera Ready!" e o IP;'
                  '\nconfirme que o computador e a ESP32 estão na mesma rede e que a rede não isola clientes.')
            return 1
        try:
            status = (await http.get(f'http://{args.host}/status')).json()
            results.append(report('GET /status', True, json.dumps({k: status.get(k) for k in ('framesize', 'quality', 'xclk', 'pixformat')})))
        except Exception as exc:
            results.append(report('GET /status', False, describe(exc)))
        times, sizes, last = [], [], None
        for _ in range(args.frames):
            started = perf_counter()
            try:
                response = await http.get(f'http://{args.host}/capture', headers={'Connection': 'close'})
                response.raise_for_status()
                size = jpeg_size(response.content)
                if size is None:
                    results.append(report('GET /capture', False, f'JPEG inválido/truncado ({len(response.content)} bytes)'))
                    continue
                times.append((perf_counter() - started) * 1000)
                sizes.append(size)
                last = response.content
            except Exception as exc:
                results.append(report('GET /capture', False, describe(exc)))
        if times:
            results.append(report('GET /capture', len(times) == args.frames,
                                  f'{len(times)}/{args.frames} JPEG válidos, {sizes[-1][0]}×{sizes[-1][1]}, '
                                  f'latência mediana {statistics.median(times):.0f} ms (máx {max(times):.0f} ms)'))
            if sizes[-1][0] < 640:
                print('       Aviso: resolução abaixo de VGA reduz o tamanho dos rostos; use VGA (framesize=10) para reconhecimento.')
        if last and args.save:
            Path(args.save).write_bytes(last)
            print(f'       Frame salvo em {args.save} (apague-o depois se contiver pessoas).')
        started = perf_counter()
        try:
            async with http.stream('GET', f'http://{args.host}:{args.stream_port}/stream') as response:
                response.raise_for_status()
                boundary = response.headers['content-type'].split('boundary=', 1)[1].strip().encode()
                buffer, parts, first = bytearray(), [], None
                async for chunk in response.aiter_bytes():
                    buffer.extend(chunk)
                    parts += split_mjpeg(buffer, boundary)
                    first = first or (parts and perf_counter())
                    if len(parts) >= args.frames * 4:
                        break
            valid = [p for p in parts if jpeg_size(p)]
            elapsed = perf_counter() - first if first else 0
            fps = (len(parts) - 1) / elapsed if elapsed > 0 and len(parts) > 1 else 0
            results.append(report(f'GET :{args.stream_port}/stream', len(valid) == len(parts) and valid != [],
                                  f'{len(valid)}/{len(parts)} frames válidos, {fps:.1f} FPS'))
        except Exception as exc:
            results.append(report(f'GET :{args.stream_port}/stream', False, describe(exc)))
    if last and args.detect:
        from app.recognition import decode, recognizer
        image = decode(last)
        faces = recognizer.inspect(image, embeddings=False)
        report('Detecção UniFace no frame da ESP32', True, f'{len(faces)} rosto(s): ' +
               ', '.join(f"{f['bbox']} det={f['confidence']:.2f} {f['issue'] or 'ok'}" for f in faces))
    return 0 if all(results) else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--host', required=True, help='IP da ESP32, sem http://')
    parser.add_argument('--stream-port', type=int, default=81)
    parser.add_argument('--frames', type=int, default=5)
    parser.add_argument('--timeout', type=float, default=5)
    parser.add_argument('--save', help='salvar o último JPEG em um arquivo')
    parser.add_argument('--detect', action='store_true', help='rodar detecção facial no frame (Teste D)')
    sys.exit(asyncio.run(main(parser.parse_args())))
