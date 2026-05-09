from collections.abc import MutableMapping
import os
import logging


def init_otel(service_name: str, config: MutableMapping):
    # Set standard OTEL env vars if present in config so SDK autoconfig can pick them up
    # Support both 'otel_env_dict' and 'otel_config' keys for flexibility
    otel_config = config.get("otel_env_dict", config.get("otel_config", {}))
    if isinstance(otel_config, MutableMapping):
        for k, v in otel_config.items():
            if isinstance(v, str) and k not in os.environ:
                os.environ[k] = v

    # If no OTEL_EXPORTER_OTLP_ENDPOINT is set, do not initialize OpenTelemetry
    if not os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"):
        return

    # Ignore polling URLs from traces to reduce noise
    if "OTEL_PYTHON_EXCLUDED_URLS" not in os.environ:
        os.environ["OTEL_PYTHON_EXCLUDED_URLS"] = ".*poll.*"

    # Temporary fix: fluent-bit drops Python OTLP logs, so we only export traces by default
    if "OTEL_LOGS_EXPORTER" not in os.environ:
        os.environ["OTEL_LOGS_EXPORTER"] = "none"

    try:
        from opentelemetry import trace
        from opentelemetry._logs import set_logger_provider
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
        from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter,
        )
        from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
        from opentelemetry.instrumentation.logging import LoggingInstrumentor
    except ImportError:
        logging.warning("OpenTelemetry not installed. Skipping setup.")
        return

    resource = Resource.create({"service.name": service_name})

    # Trace Provider
    if os.environ.get("OTEL_TRACES_EXPORTER") != "none":
        tracer_provider = TracerProvider(resource=resource)
        trace.set_tracer_provider(tracer_provider)
        span_exporter = OTLPSpanExporter()
        tracer_provider.add_span_processor(BatchSpanProcessor(span_exporter))

    # Logger Provider
    if os.environ.get("OTEL_LOGS_EXPORTER") != "none":
        logger_provider = LoggerProvider(resource=resource)
        set_logger_provider(logger_provider)
        log_exporter = OTLPLogExporter()
        logger_provider.add_log_record_processor(BatchLogRecordProcessor(log_exporter))

        # Add handler to root logger
        handler = LoggingHandler(level=logging.NOTSET, logger_provider=logger_provider)
        logging.getLogger().addHandler(handler)

        LoggingInstrumentor().instrument(
            set_logging_format=True, log_level=logging.INFO
        )
