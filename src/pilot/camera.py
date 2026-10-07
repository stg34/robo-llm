"""
CameraSource: непрерывный захват RTSP-потока с Tapo-камеры.

Фоновый поток читает кадры постоянно; capture_frame() возвращает
последний готовый кадр мгновенно — без переподключения на каждый запрос.

Использование:
    cam = CameraSource("rtsp://admin:pass@192.168.1.100:554/stream1")
    cam.start()
    frame = cam.capture_frame()   # np.ndarray (BGR) или None
    jpeg  = cam.encode_jpeg(frame)
    cam.stop()
"""
import logging
import threading
import time

import cv2
import numpy as np

logger = logging.getLogger(__name__)

TARGET_WIDTH = 896   # ширина кадра для Claude Vision


class CameraSource:
    """Непрерывный захват RTSP-потока в фоновом потоке.

    start() — запустить фоновый захват.
    stop()  — остановить.
    capture_frame() — вернуть копию последнего кадра (или None).
    latest_frame()  — то же, без побочных эффектов (для display-петли).
    """

    def __init__(self, rtsp_url: str):
        self._url = rtsp_url
        self._frame: np.ndarray | None = None          # оригинальный размер
        self._frame_small: np.ndarray | None = None    # масштабированный до TARGET_WIDTH
        self._lock = threading.Lock()
        self._running = False
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """Запустить фоновый поток захвата."""
        self._running = True
        self._thread = threading.Thread(target=self._reader, daemon=True)
        self._thread.start()
        logger.info("CameraSource: фоновый захват запущен")

    def stop(self) -> None:
        """Остановить фоновый поток."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=3)
        logger.info("CameraSource: фоновый захват остановлен")

    def capture_frame(self) -> np.ndarray | None:
        """Вернуть масштабированный кадр (TARGET_WIDTH) для Claude API."""
        with self._lock:
            return self._frame_small.copy() if self._frame_small is not None else None

    def capture_frames(self) -> tuple[np.ndarray | None, np.ndarray | None]:
        """Вернуть (оригинал, масштабированный) атомарно — для зума."""
        with self._lock:
            orig = self._frame.copy() if self._frame is not None else None
            small = self._frame_small.copy() if self._frame_small is not None else None
        return orig, small

    def latest_frame(self) -> np.ndarray | None:
        """Вернуть оригинальный кадр для live display (thread-safe)."""
        with self._lock:
            return self._frame.copy() if self._frame is not None else None

    def _reader(self) -> None:
        cap = cv2.VideoCapture(self._url)
        if not cap.isOpened():
            logger.error("Не удалось открыть RTSP-поток: %s", self._url)
            self._running = False
            return

        logger.info("CameraSource: RTSP-соединение установлено")
        while self._running:
            ret, frame = cap.read()
            if not ret or frame is None:
                logger.warning("Потеря кадра, переподключение...")
                cap.release()
                time.sleep(1)
                cap = cv2.VideoCapture(self._url)
                continue

            with self._lock:
                self._frame = frame
                self._frame_small = _scale_to_width(frame, TARGET_WIDTH)

        cap.release()

    @staticmethod
    def encode_jpeg(frame: np.ndarray, quality: int = 85) -> bytes:
        """Закодировать кадр в JPEG-байты для отправки в Claude API."""
        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, quality])
        if not ok:
            raise ValueError("cv2.imencode failed")
        return buf.tobytes()


def _scale_to_width(frame: np.ndarray, width: int) -> np.ndarray:
    h, w = frame.shape[:2]
    if w == width:
        return frame
    scale = width / w
    return cv2.resize(frame, (width, int(h * scale)), interpolation=cv2.INTER_AREA)
