import json
import logging
from datetime import datetime, timezone

from django.db import DatabaseError


class JsonLogFormatter(logging.Formatter):
    def format(self, record):
        project_event = record.name.startswith("hotel.")
        data = {
            "timestamp": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "event": record.msg if project_event else "django.log",
        }

        if record.exc_info and record.exc_info[0] is not None:
            error_type = record.exc_info[0]
            data["exception_type"] = error_type.__name__
            if issubclass(error_type, DatabaseError):
                data["event"] = "database.error"
            elif not project_event:
                data["event"] = "django.error"

        request = getattr(record, "request", None)
        request_id = getattr(request, "request_id", None)
        if request_id:
            data["request_id"] = request_id

        # Raw messages, exception text, URLs, headers, bodies and SQL are excluded.
        for field in (
            "request_id",
            "route",
            "method",
            "status_code",
            "error_code",
            "booking_id",
            "idempotency_request_id",
            "idempotency_outcome",
        ):
            if field in record.__dict__:
                data[field] = record.__dict__[field]

        return json.dumps(data)
