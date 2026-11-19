"""Windows launcher; the existing FastAPI application runs in one owned child process."""
import json
import logging
from logging.handlers import RotatingFileHandler
import multiprocessing as mp
import os
from pathlib import Path
import socket
import sys
import threading
import time
import urllib.request
import webbrowser

APP_NAME = "ЭкоГеоТерра v0.2"


def data_dir() -> Path:
    return Path(os.getenv("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))) / "EcoGeoTerra"


def setup_logging():
    folder = data_dir() / "logs"
    folder.mkdir(parents=True, exist_ok=True)
    # Separate files avoid two processes rotating the same log on Windows.
    filename = "server.log" if mp.current_process().name == "GeoServer" else "launcher.log"
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        handlers=[RotatingFileHandler(folder / filename, maxBytes=2_000_000,
                                                      backupCount=1, encoding="utf-8")], force=True)


def reserve_listener(preferred: int = 8000) -> socket.socket:
    """Keep the successful bind until Uvicorn takes over; no check/bind race."""
    for port in [*range(preferred, min(preferred + 20, 65536)), 0]:
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            if sys.platform == "win32":
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            listener.bind(("127.0.0.1", port))
            return listener
        except OSError:
            listener.close()
    raise OSError("Не удалось выбрать свободный локальный порт.")


def serve(listener, stop_event):
    setup_logging()
    try:
        import uvicorn
        from app.main import app
        server = uvicorn.Server(uvicorn.Config(
            app, host="127.0.0.1", workers=1, loop="asyncio", http="h11", ws="none",
            log_config=None, access_log=False, timeout_graceful_shutdown=2,
        ))
        def watch_stop():
            stop_event.wait()
            server.should_exit = True
        threading.Thread(target=watch_stop, daemon=True).start()
        logging.info("Serving on 127.0.0.1:%s", listener.getsockname()[1])
        server.run(sockets=[listener])
    except BaseException:
        logging.exception("Local server failed")
        raise
    finally:
        listener.close()


def open_browser(url: str) -> bool:
    # PyInstaller changes the DLL search path. Do not pass it to a system browser.
    frozen_windows = sys.platform == "win32" and getattr(sys, "frozen", False)
    if frozen_windows:
        import ctypes
        ctypes.windll.kernel32.SetDllDirectoryW(None)
    try:
        return webbrowser.open(url, new=2)
    finally:
        if frozen_windows:
            ctypes.windll.kernel32.SetDllDirectoryW(sys._MEIPASS)


class Launcher:
    def __init__(self, root):
        import tkinter as tk
        from tkinter import ttk
        self.root, self.process, self.stop_event = root, None, None
        self.closing = False
        self.ready = False
        root.title(APP_NAME)
        root.geometry("560x290")
        root.minsize(500, 280)
        root.configure(background="#f3f5ef")
        frame = ttk.Frame(root, padding=24)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text=APP_NAME, font=("Segoe UI", 19, "bold")).pack(anchor="w")
        self.message = tk.StringVar(value="Запускаем локальный сервер…")
        ttk.Label(frame, textvariable=self.message, wraplength=490).pack(anchor="w", pady=(18, 10))
        self.address = tk.StringVar(value="")
        ttk.Entry(frame, textvariable=self.address, state="readonly").pack(fill="x")
        ttk.Label(frame, text="Для работы с картой и OSM нужен интернет.\nЗакройте это окно, чтобы остановить приложение.",
                  foreground="#647264").pack(anchor="w", pady=12)
        buttons = ttk.Frame(frame)
        buttons.pack(fill="x")
        self.browser_button = ttk.Button(buttons, text="Открыть в браузере", command=self.open, state="disabled")
        self.browser_button.pack(side="left")
        ttk.Button(buttons, text="Завершить", command=self.close).pack(side="right")
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.after(100, self.start)

    def start(self):
        listener = None
        try:
            listener = reserve_listener()
            self.url = f"http://127.0.0.1:{listener.getsockname()[1]}"
            self.address.set(self.url)
            context = mp.get_context("spawn")
            self.stop_event = context.Event()
            self.process = context.Process(target=serve, args=(listener, self.stop_event), name="GeoServer", daemon=True)
            self.process.start()
            logging.info("Server child PID %s; URL %s", self.process.pid, self.url)
            self.deadline = time.monotonic() + 45
            self.root.after(150, self.check)
        except Exception:
            self.fail("Не удалось запустить локальный сервер. Проверьте доступные порты и права на запись журнала.")
        finally:
            if listener:
                listener.close()

    def check(self):
        if self.closing:
            return
        if not self.process.is_alive():
            self.fail("Локальный сервер завершился с ошибкой. Подробности находятся в журнале server.log.")
            return
        if not self.ready:
            try:
                opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                with opener.open(self.url + "/api/health", timeout=.3) as response:
                    self.ready = json.load(response).get("status") == "ok"
            except (OSError, ValueError):
                pass
            if self.ready:
                self.message.set("Приложение работает. Интерфейс открыт в браузере.")
                self.browser_button.configure(state="normal")
                self.open()
            elif time.monotonic() > self.deadline:
                self.fail("Сервер не запустился за 45 секунд. Проверьте журнал server.log.")
                return
        self.root.after(750, self.check)

    def open(self):
        try:
            opened = open_browser(self.url)
            logging.info("Browser open requested: %s; result=%s", self.url, opened)
            if not opened:
                self.message.set("Не удалось открыть браузер. Скопируйте адрес ниже в адресную строку браузера.")
        except Exception:
            logging.exception("Cannot open browser")
            self.message.set("Не удалось открыть браузер. Скопируйте адрес ниже в адресную строку браузера.")

    def fail(self, message):
        from tkinter import messagebox
        logging.error(message, exc_info=sys.exc_info()[0] is not None)
        messagebox.showerror(APP_NAME, f"{message}\n\nЖурналы: {data_dir() / 'logs'}", parent=self.root)
        self.close()

    def close(self):
        if self.closing:
            return
        self.closing = True
        self.message.set("Останавливаем локальный сервер…")
        self.browser_button.configure(state="disabled")
        if self.stop_event:
            self.stop_event.set()
        self.stop_deadline = time.monotonic() + 4
        self.root.after(50, self.finish_stop)

    def finish_stop(self):
        if self.process and self.process.is_alive():
            if time.monotonic() < self.stop_deadline:
                self.root.after(100, self.finish_stop)
                return
            logging.warning("Stopping server child after graceful shutdown deadline")
            self.process.terminate()
            self.process.join(timeout=1)
        elif self.process:
            self.process.join(timeout=.2)
        logging.info("Application stopped")
        self.root.destroy()


def main():
    import tkinter as tk
    from tkinter import messagebox
    root = tk.Tk()
    launcher = None
    try:
        setup_logging()
        launcher = Launcher(root)
        root.mainloop()
    except Exception as exc:
        logging.exception("Launcher failed")
        messagebox.showerror(APP_NAME, f"Не удалось запустить приложение.\n{exc}", parent=root)
        root.destroy()
    finally:
        if launcher and launcher.process and launcher.process.is_alive():
            launcher.stop_event.set()
            launcher.process.join(timeout=3)
            if launcher.process.is_alive():
                launcher.process.terminate()
                launcher.process.join(timeout=1)


if __name__ == "__main__":
    mp.freeze_support()
    main()
