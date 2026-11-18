"""gRPC-клиент очереди. Напоминания воркер по-прежнему читает по HTTP."""

import grpc

from app.grpcgen import reminder_pb2, reminder_pb2_grpc
from workers.service_client import ServiceError


class QueueClient:
    def __init__(self, target: str, token: str | None = None) -> None:
        self._channel = grpc.insecure_channel(target)
        self._stub = reminder_pb2_grpc.TaskDistributorStub(self._channel)
        self._metadata = (("x-internal-token", token),) if token else ()

    def close(self) -> None:
        self._channel.close()

    def lease(
        self, worker_id: str, channel: str, route_key: str | None
    ) -> dict | None:
        request = reminder_pb2.WorkerRequest(
            worker_id=worker_id,
            channel=channel,
            route_key=route_key or "",
        )
        try:
            reply = self._stub.RequestTask(
                request, metadata=self._metadata, timeout=20
            )
        except grpc.RpcError as exc:
            raise ServiceError(self._code(exc), exc.details() or str(exc)) from exc
        if not reply.has_task:
            return None
        return {
            "id": reply.task_id,
            "channel": reply.channel,
            "address": reply.address,
            "title": reply.title,
            "body": reply.body,
            "kind": reply.kind,
            "route_key": reply.route_key or None,
        }

    def complete(
        self, task_id: int, worker_id: str, success: bool, message: str | None = None
    ) -> None:
        request = reminder_pb2.WorkerResult(
            worker_id=worker_id,
            task_id=task_id,
            success=success,
            message=message or "",
        )
        try:
            self._stub.SubmitResult(request, metadata=self._metadata, timeout=20)
        except grpc.RpcError as exc:
            raise ServiceError(self._code(exc), exc.details() or str(exc)) from exc

    @staticmethod
    def _code(exc: grpc.RpcError) -> int:
        code = exc.code()
        if code == grpc.StatusCode.UNAUTHENTICATED:
            return 401
        if code == grpc.StatusCode.NOT_FOUND:
            return 404
        if code == grpc.StatusCode.FAILED_PRECONDITION:
            return 409
        return 500
