"""gRPC-распределитель: воркеры вызывают RequestTask и SubmitResult."""

import logging
from concurrent import futures

import grpc

from .channels import is_known_channel
from .config import get_settings
from .database import SessionLocal
from .distributor import complete, lease_next
from .grpcgen import reminder_pb2, reminder_pb2_grpc

log = logging.getLogger(__name__)

_server: grpc.Server | None = None


def _metadata_token(context) -> str | None:
    for key, value in context.invocation_metadata():
        if key == "x-internal-token":
            return value
    return None


def _check_token(context) -> None:
    expected = get_settings().internal_token
    if expected and _metadata_token(context) != expected:
        context.abort(grpc.StatusCode.UNAUTHENTICATED, "Неверный внутренний токен")


class TaskDistributorServicer(reminder_pb2_grpc.TaskDistributorServicer):
    def RequestTask(self, request, context):
        _check_token(context)
        if not request.worker_id:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, "worker_id пуст")
        if not is_known_channel(request.channel):
            context.abort(
                grpc.StatusCode.INVALID_ARGUMENT,
                f"Канал '{request.channel}' не поддерживается",
            )
        route_key = request.route_key or None
        db = SessionLocal()
        try:
            task = lease_next(db, request.worker_id, request.channel, route_key)
            if task is None:
                return reminder_pb2.TaskReply(has_task=False)
            return reminder_pb2.TaskReply(
                has_task=True,
                task_id=task.id,
                channel=task.channel,
                address=task.address,
                title=task.title,
                body=task.body,
                kind=task.kind,
                route_key=task.route_key or "",
            )
        finally:
            db.close()

    def SubmitResult(self, request, context):
        _check_token(context)
        db = SessionLocal()
        try:
            try:
                task = complete(
                    db,
                    int(request.task_id),
                    request.worker_id,
                    request.success,
                    request.message or None,
                )
            except KeyError:
                context.abort(grpc.StatusCode.NOT_FOUND, "Задача не найдена")
            except PermissionError:
                context.abort(
                    grpc.StatusCode.FAILED_PRECONDITION,
                    "Задача не арендована этим воркером",
                )
            return reminder_pb2.ResultAck(accepted=True, status=task.status)
        finally:
            db.close()


def start_grpc() -> grpc.Server:
    global _server
    if _server is not None:
        return _server
    port = get_settings().grpc_port
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=8))
    reminder_pb2_grpc.add_TaskDistributorServicer_to_server(
        TaskDistributorServicer(), server
    )
    server.add_insecure_port(f"0.0.0.0:{port}")
    server.start()
    _server = server
    log.info("gRPC-распределитель слушает порт %s", port)
    return server


def stop_grpc() -> None:
    global _server
    if _server is not None:
        _server.stop(grace=2)
        _server = None
        log.info("gRPC-распределитель остановлен")
