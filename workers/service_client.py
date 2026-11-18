"""HTTP-клиент воркера: напоминания и получатели. Очередь доставки — в queue_client."""

import httpx


class ServiceError(RuntimeError):
    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status


class ServiceClient:
    def __init__(self, base_url: str, token: str | None = None) -> None:
        headers = {}
        if token:
            headers["X-Internal-Token"] = token
        self._http = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers=headers,
            timeout=20,
        )

    def close(self) -> None:
        self._http.close()

    def _detail(self, response: httpx.Response) -> str:
        try:
            body = response.json()
        except Exception:
            return response.text
        detail = body.get("detail", body) if isinstance(body, dict) else body
        if isinstance(detail, list):
            parts = []
            for item in detail:
                if isinstance(item, dict):
                    parts.append(str(item.get("msg", item)))
                else:
                    parts.append(str(item))
            return "; ".join(parts)
        return str(detail)

    def _raise(self, response: httpx.Response) -> None:
        if response.is_error:
            raise ServiceError(response.status_code, self._detail(response))

    def list_recipients(
        self, channel: str, address: str, route_key: str | None
    ) -> list[dict]:
        params: dict[str, str] = {"channel": channel, "address": address}
        if route_key is not None:
            params["route_key"] = route_key
        response = self._http.get("/recipients", params=params)
        self._raise(response)
        return response.json()

    def ensure_recipient(
        self,
        name: str,
        channel: str,
        address: str,
        route_key: str | None,
    ) -> dict:
        response = self._http.post(
            "/recipients/ensure",
            json={
                "name": name,
                "channel": channel,
                "address": address,
                "route_key": route_key,
            },
        )
        self._raise(response)
        return response.json()

    def patch_recipient(self, recipient_id: int, **fields) -> dict:
        response = self._http.patch(f"/recipients/{recipient_id}", json=fields)
        self._raise(response)
        return response.json()

    def list_reminders(self, recipient_id: int) -> list[dict]:
        response = self._http.get(
            "/reminders", params={"recipient_id": recipient_id}
        )
        self._raise(response)
        return response.json()

    def get_reminder(self, reminder_id: int) -> dict | None:
        response = self._http.get(f"/reminders/{reminder_id}")
        if response.status_code == 404:
            return None
        self._raise(response)
        return response.json()

    def create_reminder(self, payload: dict) -> dict:
        response = self._http.post("/reminders", json=payload)
        self._raise(response)
        return response.json()

    def update_reminder(self, reminder_id: int, payload: dict) -> dict:
        response = self._http.patch(f"/reminders/{reminder_id}", json=payload)
        self._raise(response)
        return response.json()

    def delete_reminder(self, reminder_id: int) -> None:
        response = self._http.delete(f"/reminders/{reminder_id}")
        self._raise(response)

    def enable_reminder(self, reminder_id: int) -> dict:
        response = self._http.post(f"/reminders/{reminder_id}/enable")
        self._raise(response)
        return response.json()

    def disable_reminder(self, reminder_id: int) -> dict:
        response = self._http.post(f"/reminders/{reminder_id}/disable")
        self._raise(response)
        return response.json()

    def snooze_reminder(self, reminder_id: int, until: str) -> dict:
        response = self._http.post(
            f"/reminders/{reminder_id}/snooze", json={"until": until}
        )
        self._raise(response)
        return response.json()

    def upcoming(self, reminder_id: int) -> list[str]:
        response = self._http.get(f"/reminders/{reminder_id}/upcoming")
        self._raise(response)
        return response.json()
