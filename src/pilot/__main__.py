"""
Точка входа: python -m pilot "найди тапки"
           python -m pilot --fpv
"""
import logging
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import cv2

from pilot.brain import Brain
from pilot.camera import CameraSource, TARGET_WIDTH
from pilot.compass import Compass
from pilot.config import AWS_DEFAULT_REGION, MIN_BATTERY_VOLTAGE, SERIAL_PORT, tapo_rtsp_url, tapo_rtsp_url_record
from pilot.config import LLM_MODEL, LLM_TEMPERATURE, HUD_SHOW_TOKENS, resolve_llm_credentials, robot_hud_title
from pilot.experiment import load as load_experiment
from pilot.llm import make_backend
from pilot.navigator import Navigator
from pilot.serial_link import SerialLink
from pilot.speech import Speech
from pilot.widgets.hud import draw_hud

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

WINDOW_NAME = "FRANCY"
FPV_SPEED = 80   # смещение от нейтрали 128; подобрать по вкусу


def _fmt_tokens(n: int) -> str:
    if n == 0:
        return "—"
    return f"{n / 1000:.1f}k" if n >= 1000 else str(n)



def _fpv_loop(link, camera, nav):
    """Display + управление в FPV-режиме. Блокирует до выхода."""
    cv2.namedWindow(WINDOW_NAME, cv2.WINDOW_NORMAL)
    cv2.resizeWindow(WINDOW_NAME, 1280, 720)
    n = 128 + FPV_SPEED
    p = 128 - FPV_SPEED

    print("[FPV] W/S — вперёд/назад  |  A/D — влево/вправо  |  Space — стоп  |  Q — выход")

    gyro_angle = 0.0
    last_ts = None

    try:
        while True:
            frame = camera.latest_frame()
            if frame is not None:
                s = frame.shape[1] / TARGET_WIDTH
                t = link.get_telemetry()
                if last_ts is not None:
                    gyro_angle += t.gyro_z * (t.timestamp - last_ts)
                last_ts = t.timestamp
                draw_hud(frame,
                         range_mm=t.range_mm, voltage=t.voltage,
                         heading_str=f"{gyro_angle:.0f} deg",
                         left_speed=t.left_speed, right_speed=t.right_speed,
                         left_current=t.left_current, right_current=t.right_current,
                         scale=s, title="FRANCY  FPV", rows=[],
                         led_red=t.led_red, led_blue=t.led_blue)
                cv2.imshow(WINDOW_NAME, frame)

            # waitKey(50): держишь кнопку → команда каждые 50мс; отпустил → stop
            key = cv2.waitKey(50) & 0xFF
            if key == ord("q"):
                print("\nОстановка по Q")
                break
            elif key == ord("w"):
                link.set_motors(n, n)
            elif key == ord("s"):
                link.set_motors(p, p)
            elif key == ord("a"):
                link.set_motors(p, n)
            elif key == ord("d"):
                link.set_motors(n, p)
            else:
                link.set_motors(128, 128)

    except KeyboardInterrupt:
        print("\nОстановка по Ctrl+C")
    finally:
        nav.stop()
        camera.stop()
        link.close()
        cv2.destroyAllWindows()


def main():
    args = sys.argv[1:]

    fpv_mode = "--fpv" in args
    args = [a for a in args if a != "--fpv"]

    interactive = "--interactive" in args or "-i" in args
    args = [a for a in args if a not in ("--interactive", "-i")]

    # --config <file>
    exp = None
    if "--config" in args:
        idx = args.index("--config")
        if idx + 1 >= len(args):
            print("Ошибка: --config требует путь к файлу")
            sys.exit(1)
        config_path = args[idx + 1]
        args = args[:idx] + args[idx + 2:]
        try:
            exp = load_experiment(config_path)
        except Exception as e:
            print(f"Ошибка загрузки конфига: {e}")
            sys.exit(1)

    task = " ".join(args).strip()
    # CLI-задача перебивает задачу из конфига
    if not task and exp and exp.task:
        task = exp.task

    if not fpv_mode and not task:
        print("Использование: python -m pilot [--interactive] [-i] [--config FILE] \"задача\"")
        print("               python -m pilot --fpv")
        sys.exit(1)

    if not fpv_mode and not resolve_llm_credentials(
            (exp.model if exp and exp.model else None) or LLM_MODEL)[0]:
        print("Ошибка: API-ключ для выбранной модели не задан в .env")
        sys.exit(1)

    print(f"Подключение к {SERIAL_PORT}...")
    link = SerialLink(SERIAL_PORT)

    if not link.wait_for_packet(timeout=5.0):
        print("Ошибка: нет телеметрии от робота.")
        link.close()
        sys.exit(1)

    voltage = link.get_telemetry().voltage
    if voltage < MIN_BATTERY_VOLTAGE:
        print(f"Ошибка: аккумулятор разряжен ({voltage:.1f} V < {MIN_BATTERY_VOLTAGE} V). Зарядите и повторите.")
        link.close()
        sys.exit(1)
    print(f"Аккумулятор: {voltage:.1f} V (мин. {MIN_BATTERY_VOLTAGE} V) — OK")

    compass = Compass()
    nav = Navigator(link, compass)

    camera = CameraSource(tapo_rtsp_url())
    camera.start()

    print("Ожидание видеопотока...")
    for _ in range(20):
        if camera.latest_frame() is not None:
            break
        time.sleep(0.5)

    # -------------------------------------------------------------------
    # AI-режим
    # -------------------------------------------------------------------
    print("Калибровка гироскопа (~3с), не двигайте робота...", end=" ", flush=True)
    bias = link.calibrate_gyro(n_packets=30)
    link.send_gyro_bias_correction(bias)
    print(f"bias={bias:+.2f}°/s")

    # -------------------------------------------------------------------
    # FPV-режим: без ИИ, без калибровки гироскопа, без ffmpeg
    # -------------------------------------------------------------------
    if fpv_mode:
        _fpv_loop(link, camera, nav)
        return

    speech = Speech(region=AWS_DEFAULT_REGION)

    model       = (exp.model       if exp and exp.model       else None) or LLM_MODEL
    temperature = (exp.temperature if exp and exp.temperature is not None else None) \
                  if exp else LLM_TEMPERATURE
    if temperature is None:
        temperature = LLM_TEMPERATURE

    api_key, base_url = resolve_llm_credentials(model)
    backend = make_backend(model=model, api_key=api_key, base_url=base_url,
                           temperature=temperature)
    temp_str = f", temperature={temperature}" if temperature is not None else ""
    config_str = f" [конфиг: {config_path}]" if exp else ""
    key_hint = f"{api_key[:8]}…" if api_key else "НЕ ЗАДАН"
    print(f"Модель: {model}{temp_str}{config_str}")
    print(f"Провайдер: {base_url}  |  ключ: {key_hint}")

    brain = Brain(
        backend=backend,
        camera=camera,
        serial_link=link,
        compass=compass,
        navigator=nav,
        task=task,
        speech=speech,
        interactive=interactive,
        system_prompt=exp.system_prompt if exp else None,
        tools=exp.tools if exp else None,
        system_image=exp.system_image if exp else None,
    )

    if exp:
        shutil.copy2(config_path, brain.session_dir / "experiment.yaml")
        if exp.system_image:
            shutil.copy2(exp.system_image, brain.session_dir / exp.system_image.name)

    shutil.copy2(Path(__file__).parent / "tools.py", brain.session_dir / "tools.py")

    video_path = brain.session_dir / "video.mp4"
    ffmpeg_log = brain.session_dir / "ffmpeg.log"
    ffmpeg_proc = subprocess.Popen(
        [
            "ffmpeg",
            "-rtsp_transport", "tcp",
            "-i", tapo_rtsp_url_record(),
            "-c:v", "copy",
            "-c:a", "aac",
            "-y",
            str(video_path),
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=open(ffmpeg_log, "w"),
    )
    print(f"[ffmpeg] запись → {video_path}")

    time.sleep(1.0)
    sync_ts = link.beep(250)
    brain.log_sync_beep(sync_ts)
    print(f"[sync] beep at {sync_ts:.3f}")

    print(f"[управление] q — стоп | i — отправить сообщение ИИ перед следующим ходом"
          + (" | интерактивный режим включён" if interactive else ""))

    brain_thread = threading.Thread(target=brain.run, daemon=True)
    brain_thread.start()

    cv2.namedWindow(WINDOW_NAME, cv2.WINDOW_NORMAL)
    cv2.resizeWindow(WINDOW_NAME, 1280, 720)
    gyro_angle = 0.0
    last_ts = None

    try:
        while brain_thread.is_alive():
            frame = camera.latest_frame()
            if frame is not None:
                s = frame.shape[1] / TARGET_WIDTH
                t = link.get_telemetry()
                if last_ts is not None:
                    gyro_angle += t.gyro_z * (t.timestamp - last_ts)
                last_ts = t.timestamp
                draw_hud(frame,
                         range_mm=t.range_mm, voltage=t.voltage,
                         heading_str=f"{gyro_angle:.0f} deg",
                         left_speed=t.left_speed, right_speed=t.right_speed,
                         left_current=t.left_current, right_current=t.right_current,
                         scale=s,
                         title=robot_hud_title(model),
                         rows=[
                             ("Turn", str(brain.turn)),
                             *([("Tokens", _fmt_tokens(brain.total_tokens))] if HUD_SHOW_TOKENS else []),
                         ],
                         led_red=t.led_red, led_blue=t.led_blue)
                cv2.imshow(WINDOW_NAME, frame)
            key = cv2.waitKey(30) & 0xFF
            if key == ord("q"):
                print("\nОстановка по Q")
                break
            elif key == ord("i"):
                brain.request_user_input()
                print("[i] Ввод запрошен — будет задан перед следующим ходом")
    except KeyboardInterrupt:
        print("\nОстановка по Ctrl+C")
    finally:
        nav.stop()
        camera.stop()
        link.close()
        cv2.destroyAllWindows()

        time.sleep(3.0)

        if ffmpeg_proc.poll() is None:
            try:
                ffmpeg_proc.stdin.write(b"q")
                ffmpeg_proc.stdin.flush()
                ffmpeg_proc.wait(timeout=10)
                print(f"[ffmpeg] сохранено → {video_path}")
            except Exception:
                ffmpeg_proc.kill()

    brain_thread.join(timeout=5)


if __name__ == "__main__":
    main()
