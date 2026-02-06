import flet
from flet import Page, TextField, Button, Text, Column, Row
import threading
import time
import requests
from datetime import datetime, time as dtime, timedelta
import json
import os


CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.json")


def _parse_hms(value: str) -> dtime:
    parts = value.strip().split(":")
    if len(parts) != 3:
        raise ValueError("Неверный формат времени, ожидается HH:MM:SS")
    hh, mm, ss = (int(x) for x in parts)
    if not (0 <= hh <= 23 and 0 <= mm <= 59 and 0 <= ss <= 59):
        raise ValueError("Неверное время")
    return dtime(hh, mm, ss)


def _compute_window(now: datetime, start_t: dtime, end_t: dtime) -> tuple[datetime, datetime]:
    """
    Возвращает ближайшее окно [start_dt, end_dt].
    Если end <= start, окно считается переходящим через полночь.
    Если текущее время уже позже конца окна, окно сдвигается на следующий день.
    """
    start_dt = datetime.combine(now.date(), start_t)
    end_dt = datetime.combine(now.date(), end_t)
    if end_dt <= start_dt:
        end_dt += timedelta(days=1)
    if now > end_dt:
        start_dt += timedelta(days=1)
        end_dt += timedelta(days=1)
    return start_dt, end_dt


def send_worker(
    page: Page,
    token: str,
    channel_id: str,
    start_hms: str,
    end_hms: str,
    delay_ms: str,
    message: str,
    status: Text,
):
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
        start_t = _parse_hms(start_hms)
        end_t = _parse_hms(end_hms)

        try:
            delay = int(str(delay_ms).strip())
        except Exception:
            raise ValueError("Задержка должна быть числом (миллисекунды)")
        if delay <= 0:
            raise ValueError("Задержка должна быть больше 0 мс")
        delay_s = delay / 1000.0

        now = datetime.now()
        start_dt, end_dt = _compute_window(now, start_t, end_t)

        session = requests.Session()
        headers = {"Authorization": token, "Content-Type": "application/json"}

        typing_url = f"https://discord.com/api/v9/channels/{channel_id}/typing"
        send_url = f"https://discord.com/api/v9/channels/{channel_id}/messages"
        payload = {"content": message}

        # 1) Ждём до начала окна
        last_status_ts = 0.0
        while True:
            now_ts = time.time()
            now_dt = datetime.now()
            if now_dt >= start_dt:
                break
            remaining = (start_dt - now_dt).total_seconds()
            if now_ts - last_status_ts >= 1.0:
                update_status(f"Ожидание до старта: {int(remaining)} сек.")
                last_status_ts = now_ts
            time.sleep(min(1.0, max(0.05, remaining)))

        update_status(
            f"Окно активно: {start_dt.strftime('%H:%M:%S')}–{end_dt.strftime('%H:%M:%S')}. "
            f"Проверка каждые {delay} мс..."
        )

        # 2) В окне: проверяем доступность канала, при первом успехе — отправляем 1 раз и завершаем
        last_state: int | None = None
        while datetime.now() <= end_dt:
            # Проверка: "канал открыт" → typing endpoint возвращает 204
            try:
                r = session.post(typing_url, headers=headers, timeout=5)
                code = r.status_code
            except Exception:
                code = -1

            if code in (200, 204):
                update_status("Канал доступен. Отправляю сообщение...")
                try:
                    r_send = session.post(send_url, json=payload, headers=headers, timeout=5)
                    if 200 <= r_send.status_code < 300:
                        update_status("Сообщение отправлено.")
                    else:
                        update_status(f"Канал доступен, но отправка не удалась (код: {r_send.status_code}).")
                except Exception as e:
                    update_status(f"Канал доступен, но отправка не удалась: {e}")
                return

            # Канал недоступен — продолжаем до конца окна
            # Обновляем статус не чаще раза в секунду и только если код изменился/пора обновить
            now_ts = time.time()
            if (now_ts - last_status_ts >= 1.0) or (last_state != code):
                left = max(0, int((end_dt - datetime.now()).total_seconds()))
                detail = "нет ответа" if code == -1 else str(code)
                update_status(f"Канал недоступен (код: {detail}). Осталось {left} сек.")
                last_status_ts = now_ts
                last_state = code

            # Спим до следующей проверки, но не выходим за границу окна
            remaining_window = (end_dt - datetime.now()).total_seconds()
            if remaining_window <= 0:
                break
            time.sleep(min(delay_s, max(0.01, remaining_window)))

        update_status("Окно завершилось: сообщение не отправлено (канал был недоступен).")
        return

    except Exception as e:
        update_status(f"Ошибка: {e}")


def main(page: Page):
    page.title = "Отложенная отправка сообщения в Discord (User token)"
    page.window_width = 720
    page.window_height = 520

    token_field = TextField(password=True, label="Токен пользователя (Authorization)", width=700)
    channel_field = TextField(label="Channel ID", width=700)
    start_field = TextField(label="Начать в (HH:MM:SS)", width=220, value="00:00:00")
    end_field = TextField(label="Закончить в (HH:MM:SS)", width=220, value="00:00:10")
    delay_field = TextField(label="Задержка (мс)", width=180, value="200")
    message_field = TextField(label="Сообщение (поддерживает переносы)", multiline=True, min_lines=4, width=700)

    status = Text(value="Готово", size=14)

    # Попытка загрузить сохранённые значения (если конфиг существует)
    try:
        if os.path.exists(CONFIG_PATH):
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            token_field.value = cfg.get("token", "")
            channel_field.value = cfg.get("channel_id", "")
            start_field.value = cfg.get("start_at", start_field.value)
            end_field.value = cfg.get("end_at", end_field.value)
            delay_field.value = str(cfg.get("delay_ms", delay_field.value))
    except Exception:
        # Если что-то пошло не так при чтении конфига — просто игнорируем и оставляем поля по умолчанию
        pass

    def on_start(e):
        tok = token_field.value.strip()
        ch = channel_field.value.strip()
        st = start_field.value.strip()
        en = end_field.value.strip()
        dl = delay_field.value.strip()
        msg = message_field.value

        if not tok or not ch or not st or not en or not dl or not msg:
            status.value = "Заполните токен, channel id, начало, конец, задержку и текст сообщения"
            page.update()
            return

        # Базовая валидация до запуска фонового потока
        try:
            start_t = _parse_hms(st)
            end_t = _parse_hms(en)
        except ValueError as ex:
            status.value = f"Ошибка времени: {ex}"
            page.update()
            return

        try:
            delay_int = int(dl)
            if delay_int <= 0:
                raise ValueError
        except ValueError:
            status.value = "Задержка должна быть положительным числом (мс)"
            page.update()
            return

        # Ограничим максимально разумную длину окна (например, 12 часов), чтобы не зависать на сутки
        now = datetime.now()
        start_dt, end_dt = _compute_window(now, start_t, end_t)
        window_len = (end_dt - start_dt).total_seconds()
        if window_len > 12 * 3600:
            status.value = "Слишком длинное окно (больше 12 часов). Проверьте время начала/конца."
            page.update()
            return

        status.value = "Запуск фонового процесса..."
        page.update()

        t = threading.Thread(target=send_worker, args=(page, tok, ch, st, en, dl, msg, status), daemon=True)
        t.start()

    def on_save(e):
        cfg = {
            "token": token_field.value,
            "channel_id": channel_field.value,
            "start_at": start_field.value,
            "end_at": end_field.value,
            "delay_ms": delay_field.value,
        }
        try:
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
            status.value = "Значения сохранены."
        except Exception as ex:
            status.value = f"Не удалось сохранить значения: {ex}"
        page.update()

    start_btn = Button(content="Start", on_click=on_start)
    save_btn = Button(content="Сохранить значения", on_click=on_save)

    page.add(
        Column(
            [
                token_field,
                channel_field,
                Row([start_field, end_field, delay_field]),
                message_field,
                Row([start_btn, save_btn]),
                status,
            ],
            spacing=12,
        )
    )


if __name__ == "__main__":
    flet.app(target=main)
