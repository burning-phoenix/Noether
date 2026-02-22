import logging
from .tracer import tracer

class TextualLogHandler(logging.Handler):
    """
    A custom logging handler that routes standard Python log messages
    to the Noether tracer, allowing them to be streamed to the UI's RichLog.
    """
    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            name = record.name
            
            # Extract additional metadata if passed in 'extra'
            metadata = {
                "level": record.levelname,
                "module": record.module,
                "funcName": record.funcName,
            }

            if record.levelno >= logging.ERROR:
                # Include exception info if present
                if record.exc_info:
                    error_details = self.formatter.formatException(record.exc_info) if self.formatter else str(record.exc_info)
                    tracer.log_error(name, f"{msg}\n{error_details}", **metadata)
                else:
                    tracer.log_error(name, msg, **metadata)
            else:
                tracer.log_event(name, message=msg, **metadata)

        except Exception:
            self.handleError(record)

class NoetherLogFilter(logging.Filter):
    """Filter out noisy logs from external libraries."""
    def filter(self, record):
        if record.name.startswith("textual.") or record.name.startswith("httpcore") or record.name.startswith("httpx"):
            return record.levelno >= logging.WARNING
        return True

def setup_global_logging():
    """Configure the root logger to use TextualLogHandler."""
    root_logger = logging.getLogger()
    
    # Check if we already added it
    if any(isinstance(h, TextualLogHandler) for h in root_logger.handlers):
        return
        
    handler = TextualLogHandler()
    handler.addFilter(NoetherLogFilter())
    formatter = logging.Formatter('%(message)s')
    handler.setFormatter(formatter)
    
    # We might want to set this to INFO or DEBUG depending on verbosity needed
    handler.setLevel(logging.INFO)
    
    # Keep other handlers like Textual's default file handler if they exist
    root_logger.addHandler(handler)
    
    # Optionally explicitly set level of the root logger
    root_logger.setLevel(logging.INFO)
