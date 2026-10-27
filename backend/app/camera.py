"""Single access point to each ESP32-S3 camera.

The CameraWebServer firmware serves /capture on port 80 and /stream on port 81
(server_port + 1). Its stream server handles one client at a time and the
sensor has one or two frame buffers, so every consumer here (preview, overlay
inference, attendance capture) shares one reader per camera instead of opening
its own connection.
"""
import asyncio
import logging
import re
import struct
from collections import defaultdict
from dataclasses import dataclass
from time import monotonic, perf_counter, time
import httpx
from app.config import settings

log = logging.getLogger(__name__)
MAX_FRAME = 8_000_000
host_locks = defaultdict(asyncio.Lock)


def jpeg_size(data: bytes):
    """(width, height) of a complete baseline/progressive JPEG, or None if truncated/corrupt."""
    # Entropy-coded data byte-stuffs 0xFF, so FFD9 near the end can only be the EOI marker.
    # Some sensors pad after EOI, hence the tail search instead of endswith().
    if len(data) < 128 or not data.startswith(b'\xff\xd8') or b'\xff\xd9' not in data[-1024:]:
        return None
    i = 2
    while i + 9 < len(data):
        if data[i] != 0xFF:
            return None
        marker = data[i + 1]
        if marker == 0xFF:
            i += 1
            continue
        if marker == 0x01 or 0xD0 <= marker <= 0xD8:
            i += 2
            continue
        length = struct.unpack('>H', data[i + 2:i + 4])[0]
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            height, width = struct.unpack('>HH', data[i + 5:i + 9])
            return (width, height) if width and height else None
        i += 2 + length
    return None


def split_mjpeg(buffer: bytearray, boundary: bytes):
    """Remove and return complete JPEG parts from a multipart/x-mixed-replace buffer."""
    marker = b'--' + boundary
    parts = []
    while True:
        start = buffer.find(marker)
        if start < 0:
            # Keep a tail in case the boundary is split across chunks.
            del buffer[:max(0, len(buffer) - len(marker))]
            return parts
        head_end = buffer.find(b'\r\n\r\n', start)
        if head_end < 0:
            return parts
        headers = bytes(buffer[start:head_end]).decode('latin-1')
        body = head_end + 4
        length = re.search(r'content-length:\s*(\d+)', headers, re.I)
        if length:
            end = body + int(length.group(1))
            if int(length.group(1)) > MAX_FRAME:
                raise ValueError('Frame do stream excede 8 MB')
            if len(buffer) < end:
                return parts
            parts.append(bytes(buffer[body:end]))
            del buffer[:end]
        else:
            following = buffer.find(marker, body)
            if following < 0:
                return parts
            parts.append(bytes(buffer[body:following]).rstrip(b'\r\n'))
            del buffer[:following]


def describe(exc: Exception):
    """Human-readable cause for the UI; the exception type stays in the server log."""
    if isinstance(exc, httpx.ConnectTimeout):
        return 'Tempo esgotado ao conectar. A ESP32 está ligada, na mesma rede e com o IP correto? Redes com isolamento de clientes bloqueiam o acesso.'
    if isinstance(exc, httpx.ReadTimeout):
        return 'A câmera conectou, mas parou de enviar dados (frame congelado). Outro cliente pode estar usando o stream (feche abas em :81/stream) ou a alimentação está instável.'
    if isinstance(exc, httpx.ConnectError):
        return 'Conexão recusada ou rede inalcançável. Confira o IP no monitor serial da ESP32.'
    if isinstance(exc, httpx.RemoteProtocolError):
        return 'A câmera encerrou a conexão no meio da resposta (reinício, Wi-Fi fraco ou falta de memória).'
    if isinstance(exc, httpx.TransportError):
        return 'A conexão com a câmera caiu (reinício da ESP32, Wi-Fi instável ou queda de alimentação).'
    if isinstance(exc, httpx.HTTPStatusError):
        return f'A câmera respondeu HTTP {exc.response.status_code}. HTTP 500 em /capture indica falha do sensor (confira o cabo flat e a PSRAM).'
    return str(exc) or type(exc).__name__


def client():
    return httpx.AsyncClient(timeout=settings.camera_timeout, follow_redirects=False, trust_env=False)


async def fetch_jpeg(http: httpx.AsyncClient, host: str):
    """One validated JPEG from http://host/capture; serialized per host, one retry."""
    if host not in settings.camera_hosts:
        raise ValueError('Host da câmera não autorizado no backend')
    async with host_locks[host]:
        for attempt in range(2):
            try:
                return await _fetch(http, host)
            except (httpx.TransportError, httpx.HTTPStatusError):
                if attempt:
                    raise
                await asyncio.sleep(.2)


async def _fetch(http, host):
    chunks = bytearray()
    # CameraWebServer responses can leave bytes on a persistent connection.
    # Use a fresh connection for each JPEG to avoid corrupting the next response.
    async with http.stream('GET', f'http://{host}/capture', headers={'Connection': 'close'}) as response:
        response.raise_for_status()
        if 'image/jpeg' not in response.headers.get('content-type', ''):
            raise ValueError('A câmera não retornou JPEG')
        async for chunk in response.aiter_bytes():
            chunks.extend(chunk)
            if len(chunks) > MAX_FRAME:
                raise ValueError('Captura excede 8 MB')
    if jpeg_size(chunks) is None:
        raise ValueError('JPEG incompleto ou corrompido recebido da câmera')
    return bytes(chunks)


@dataclass
class Frame:
    seq: int
    data: bytes
    width: int
    height: int
    received: float  # monotonic
    timestamp: float  # wall clock
    acquisition_ms: float


class CameraReader:
    def __init__(self, camera_id: int, host: str):
        self.camera_id = camera_id
        self.host = host
        self.owners = {}  # owner -> (mode, session_id)
        self.state = 'disconnected'  # disconnected | connecting | streaming | error
        self.source = None
        self.error = None
        self.frame: Frame | None = None
        self.fps = 0.0
        self.frames = 0
        self.corrupted = 0
        self.reconnects = 0
        self.analysis = None
        self.version = 0
        self.changed = asyncio.Condition()
        self.task = None
        self.inference = None
        self.stopper = None
        self.stream_retry_at = 0.0

    async def notify(self):
        self.version += 1
        async with self.changed:
            self.changed.notify_all()

    def snapshot(self):
        frame = self.frame
        return {'camera_id': self.camera_id, 'state': self.state, 'source': self.source, 'error': self.error,
                'fps': round(self.fps, 2), 'frames': self.frames, 'corrupted': self.corrupted, 'reconnects': self.reconnects,
                'consumers': len(self.owners),
                'width': frame.width if frame else None, 'height': frame.height if frame else None,
                'acquisition_ms': round(frame.acquisition_ms, 1) if frame else None,
                'last_frame_at': frame.timestamp if frame else None,
                'frame_age_ms': round((monotonic() - frame.received) * 1000) if frame else None}

    async def publish(self, data: bytes, acquisition_ms: float):
        size = jpeg_size(data)
        if size is None:
            self.corrupted += 1
            return
        now = monotonic()
        if self.frame:
            interval = now - self.frame.received
            if interval > 0:
                self.fps = 1 / interval if not self.fps else .8 * self.fps + .2 / interval
        self.frames += 1
        self.frame = Frame(self.frames, data, size[0], size[1], now, time(), acquisition_ms)
        self.state, self.error = 'streaming', None
        await self.notify()

    async def run(self):
        backoff = 1.0
        async with client() as http:
            while self.owners:
                try:
                    if settings.camera_source == 'stream' and monotonic() >= self.stream_retry_at:
                        await self._stream(http)
                    else:
                        await self._poll(http)
                    backoff = 1.0
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    if self.source == 'stream':
                        # Port 81 serves one client; poll /capture for a while before retrying it.
                        self.stream_retry_at = monotonic() + 30
                    self.state, self.error = 'error', describe(exc)
                    self.reconnects += 1
                    self.fps = 0.0
                    log.warning('Câmera %s (%s): %s', self.camera_id, self.source, exc)
                    await self.notify()
                    # Bounded exponential backoff, never a tight reconnect loop.
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, 10)
        self.state, self.fps = 'disconnected', 0.0
        await self.notify()

    async def _stream(self, http):
        self.source, self.state = 'stream', 'connecting'
        await self.notify()
        url = f'http://{self.host}:{settings.camera_stream_port}/stream'
        async with http.stream('GET', url) as response:
            response.raise_for_status()
            kind = response.headers.get('content-type', '')
            if 'multipart' not in kind or 'boundary=' not in kind:
                raise ValueError(f'Stream sem multipart/x-mixed-replace ({kind or "sem content-type"})')
            boundary = kind.split('boundary=', 1)[1].strip().strip('"').encode()
            buffer = bytearray()
            part_started = perf_counter()
            async for chunk in response.aiter_bytes():
                if not self.owners:
                    return
                buffer.extend(chunk)
                for part in split_mjpeg(buffer, boundary):
                    await self.publish(part, (perf_counter() - part_started) * 1000)
                    part_started = perf_counter()
                if len(buffer) > MAX_FRAME:
                    raise ValueError('Stream sem delimitadores válidos')
            raise httpx.RemoteProtocolError('Stream encerrado pela câmera')

    async def _poll(self, http):
        self.source = 'capture'
        while self.owners:
            started = perf_counter()
            data = await fetch_jpeg(http, self.host)
            elapsed = perf_counter() - started
            await self.publish(data, elapsed * 1000)
            await asyncio.sleep(max(0, 1 / settings.monitor_max_fps - elapsed))
            if settings.camera_source == 'stream' and monotonic() >= self.stream_retry_at:
                return

    async def latest(self, max_age: float, wait: float):
        """A frame no older than max_age seconds, waiting up to `wait` seconds for one."""
        deadline = monotonic() + wait
        while True:
            frame = self.frame
            if frame and monotonic() - frame.received <= max_age:
                return frame
            remaining = deadline - monotonic()
            if remaining <= 0:
                return None
            async with self.changed:
                try:
                    await asyncio.wait_for(self.changed.wait(), remaining)
                except asyncio.TimeoutError:
                    return None

    def wants_analysis(self):
        modes = [mode for mode in self.owners.values() if mode[0] != 'preview']
        if not modes:
            return None
        sessions = [session for mode, session in modes if mode == 'recognition']
        return ('recognition', sessions[0]) if sessions else ('detection', None)


class CameraHub:
    def __init__(self):
        self.readers: dict[int, CameraReader] = {}
        self.analyzer = None  # set by app.monitor to avoid an import cycle

    def get(self, camera_id):
        return self.readers.get(camera_id)

    def acquire(self, camera, owner, mode=('preview', None)):
        reader = self.readers.get(camera.id)
        if reader is None or reader.host != camera.host:
            reader = self.readers[camera.id] = CameraReader(camera.id, camera.host)
        reader.owners[owner] = mode
        if reader.stopper:
            reader.stopper.cancel()
            reader.stopper = None
        if reader.task is None or reader.task.done():
            reader.task = asyncio.create_task(reader.run())
        self.set_mode(reader, owner, mode)
        return reader

    def set_mode(self, reader, owner, mode):
        reader.owners[owner] = mode
        if reader.wants_analysis() and (reader.inference is None or reader.inference.done()) and self.analyzer:
            reader.inference = asyncio.create_task(self.analyzer(reader))

    def release(self, camera_id, owner):
        reader = self.readers.get(camera_id)
        if reader is None:
            return
        reader.owners.pop(owner, None)
        if not reader.owners and reader.stopper is None:
            reader.stopper = asyncio.create_task(self._stop_later(reader))

    async def _stop_later(self, reader):
        # Grace period: a page reload or a new consumer reuses the open connection.
        await asyncio.sleep(settings.camera_idle_seconds)
        if not reader.owners:
            for task in (reader.task, reader.inference):
                if task and not task.done():
                    task.cancel()
            reader.state, reader.fps, reader.error = 'disconnected', 0.0, None
            await reader.notify()
        reader.stopper = None

    async def close(self):
        tasks = [t for r in self.readers.values() for t in (r.task, r.inference, r.stopper) if t and not t.done()]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.readers.clear()


hub = CameraHub()
