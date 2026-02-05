import flet
from flet import Page, TextField, Button, Text, Column, Row
import threading
import time
import requests
from datetime import datetime, time as dtime, timedelta


def send_worker(page: Page, token: str, server_id: str, channel_id: str, send_hm: str, message: str, status: Text):
    async def update_status_async(text: str):
        """Асинхронная функция для обновления статуса"""
        status.value = text
        page.update()
    
    def update_status(text: str):
        """Обновляет статус в главном потоке через run_task"""
        try:
            page.run_task(update_status_async, text)
        except Exception:
            # Fallback на синхронное обновление если run_task не работает
            status.value = text
            page.update()
    
    try:
        # parse HH:MM
        parts = send_hm.strip().split(":")
        if len(parts) != 2:
            raise ValueError("Неверный формат времени, ожидается HH:MM")
        hh = int(parts[0])
        mm = int(parts[1])
        if not (0 <= hh <= 23 and 0 <= mm <= 59):
            raise ValueError("Неверное время")

        now = datetime.now()
        target_time = datetime.combine(now.date(), dtime(hh, mm))
        if target_time <= now:
            target_time += timedelta(days=1)

        target_ts = target_time.timestamp()

        session = requests.Session()
        headers = {
            "Authorization": token,
            "Content-Type": "application/json"
        }

        # Оптимизированное ожидание: уменьшено до 50 мс перед целевым временем для более быстрой отправки
        last_update_time = 0
        while True:
            now_ts = time.time()
            remaining = target_ts - now_ts
            if remaining <= 0:
                break
            if remaining > 0.05:  # Уменьшено с 0.3 до 0.05 секунды
                sleep_time = remaining - 0.05
                if sleep_time > 5:
                    sleep_time = 5
                time.sleep(sleep_time)
                # Обновляем статус не чаще раза в секунду для плавности
                if now_ts - last_update_time >= 1.0:
                    update_status(f"Ожидание: {int(remaining)} сек.")
                    last_update_time = now_ts
            else:
                # Точное ожидание с минимальными задержками
                if remaining > 0.005:
                    time.sleep(remaining * 0.5)  # Спим половину оставшегося времени
                # Busy-wait для максимальной точности в последние миллисекунды
                # Используем небольшую задержку для снижения нагрузки на CPU (особенно на Windows)
                while time.time() < target_ts:
                    time.sleep(0.0001)  # Минимальная задержка для снижения нагрузки CPU

        update_status("Отправка сообщения...")

        url = f"https://discord.com/api/v9/channels/{channel_id}/messages"
        payload = {"content": message}

        def do_post():
            try:
                r = session.post(url, json=payload, headers=headers, timeout=5)  # Уменьшен timeout с 10 до 5
                return r
            except Exception:
                return None

        r = do_post()
        if r is not None and r.status_code == 200:
            update_status("Сообщение успешно отправлено")
        else:
            # Убрана задержка перед повторной попыткой для ускорения
            r2 = do_post()
            if r2 is not None and r2.status_code == 200:
                update_status("Сообщение успешно отправлено (повторная попытка)")
            else:
                code = r2.status_code if r2 is not None else 'no_response'
                update_status(f"Ошибка отправки, код: {code}")

    except Exception as e:
        update_status(f"Ошибка: {e}")


def main(page: Page):
    page.title = "Отложенная отправка сообщения в Discord (User token)"
    page.window_width = 720
    page.window_height = 520

    token_field = TextField(password=True, label="Токен пользователя (Authorization)", width=700)
    server_field = TextField(label="Server ID (необязательно)", width=340)
    channel_field = TextField(label="Channel ID", width=340)
    time_field = TextField(label="Время отправки (HH:MM)", width=200, value="00:00")
    message_field = TextField(label="Сообщение (поддерживает переносы)", multiline=True, min_lines=4, width=700)

    status = Text(value="Готово", size=14)

    def on_start(e):
        tok = token_field.value.strip()
        srv = server_field.value.strip()
        ch = channel_field.value.strip()
        tm = time_field.value.strip()
        msg = message_field.value

        if not tok or not ch or not tm or not msg:
            status.value = "Заполните токен, channel id, время и текст сообщения"
            page.update()
            return

        status.value = "Запуск фонового процесса..."
        page.update()

        t = threading.Thread(target=send_worker, args=(page, tok, srv, ch, tm, msg, status), daemon=True)
        t.start()

    start_btn = Button(content="Start", on_click=on_start)

    page.add(Column([token_field, Row([server_field, channel_field]), time_field, message_field, start_btn, status], spacing=12))


if __name__ == "__main__":
    flet.app(target=main)
