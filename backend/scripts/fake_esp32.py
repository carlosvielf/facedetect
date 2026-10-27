"""DEVELOPMENT ONLY: imitates the HTTP interface of the Freenove CameraWebServer.

Lets you exercise the monitor, overlay and attendance flow without the ESP32.
It is never used by the application itself and does not validate the hardware.

    ..\\.venv\\Scripts\\python.exe scripts\\fake_esp32.py --image foto.jpg [--host 127.0.0.1]

Serves / /status /control /capture on port 80 and /stream on port 81, like the
firmware (stream server = server_port + 1, one client at a time). The image can
be swapped at runtime: GET http://HOST/sim?image=C:\\path\\other.jpg
Add the host to CAMERA_HOSTS only in a development .env.
"""
import argparse
import asyncio
from pathlib import Path
import cv2
import numpy as np
import uvicorn
from starlette.applications import Starlette
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route

BOUNDARY = '123456789000000000000987654321'
state = {'framesize': 10, 'quality': 10, 'frame': b'', 'source': None}
stream_lock = asyncio.Lock()
SIZES = {6: (320, 240), 8: (400, 296), 9: (480, 320), 10: (640, 480), 11: (800, 600), 12: (1024, 768)}


def render():
    image = cv2.imread(str(state['source'])) if state['source'] else None
    if image is None:
        image = np.full((480, 640, 3), 60, np.uint8)
    width, height = SIZES.get(state['framesize'], (640, 480))
    scale = min(width / image.shape[1], height / image.shape[0])
    resized = cv2.resize(image, (int(image.shape[1] * scale), int(image.shape[0] * scale)))
    canvas = np.zeros((height, width, 3), np.uint8)
    y, x = (height - resized.shape[0]) // 2, (width - resized.shape[1]) // 2
    canvas[y:y + resized.shape[0], x:x + resized.shape[1]] = resized
    state['frame'] = cv2.imencode('.jpg', canvas, [cv2.IMWRITE_JPEG_QUALITY, 85])[1].tobytes()


async def index(request):
    return Response('<html><body>fake CameraWebServer</body></html>', media_type='text/html')


async def status(request):
    return JSONResponse({'framesize': state['framesize'], 'quality': state['quality'], 'xclk': 10, 'pixformat': 4})


async def control(request):
    if request.query_params.get('var') == 'framesize':
        state['framesize'] = int(request.query_params['val'])
        render()
    return Response('')


async def capture(request):
    return Response(state['frame'], media_type='image/jpeg')


async def sim(request):
    state['source'] = Path(request.query_params['image'])
    render()
    return JSONResponse({'image': str(state['source'])})


async def stream(request):
    if stream_lock.locked():
        # The real firmware blocks a second client; never answering reproduces that.
        await asyncio.sleep(3600)

    async def parts():
        async with stream_lock:
            while True:
                frame = state['frame']
                yield (f'\r\n--{BOUNDARY}\r\nContent-Type: image/jpeg\r\nContent-Length: {len(frame)}\r\n'
                       f'X-Timestamp: 0.0\r\n\r\n').encode() + frame
                await asyncio.sleep(1 / 12)
    return StreamingResponse(parts(), media_type=f'multipart/x-mixed-replace;boundary={BOUNDARY}')


async def main(args):
    state['source'] = Path(args.image) if args.image else None
    render()
    control_app = Starlette(routes=[Route('/', index), Route('/status', status), Route('/control', control),
                                    Route('/capture', capture), Route('/sim', sim)])
    stream_app = Starlette(routes=[Route('/stream', stream)])
    servers = [uvicorn.Server(uvicorn.Config(control_app, host=args.host, port=80, log_level='warning')),
               uvicorn.Server(uvicorn.Config(stream_app, host=args.host, port=81, log_level='warning'))]
    print(f'Fake ESP32 em http://{args.host}/ e http://{args.host}:81/stream (somente desenvolvimento)')
    await asyncio.gather(*(server.serve() for server in servers))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--image')
    parser.add_argument('--host', default='127.0.0.1')
    asyncio.run(main(parser.parse_args()))
