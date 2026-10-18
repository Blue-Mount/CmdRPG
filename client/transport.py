"""Adapter between the terminal and local SQLite saves."""

class TransportError(Exception):
    def __init__(self, code, message, *, status_code=None, revision=None,
                 retryable=False):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.revision = revision
        self.retryable = retryable


class LocalTransport:
    def __init__(self, database_path="data/game.sqlite3", repository=None):
        if repository is None:
            from saves.storage import Repository
            repository = Repository(database_path)
        self.repository = repository

    def _call(self, method, *args, **kwargs):
        try:
            return getattr(self.repository, method)(*args, **kwargs)
        except Exception as exc:
            if hasattr(exc, "code") and hasattr(exc, "message"):
                raise TransportError(
                    exc.code, exc.message,
                    status_code=getattr(exc, "status_code", 400),
                    revision=getattr(exc, "revision", None),
                ) from exc
            raise

    def list_runs(self):
        return self._call("list_runs")

    def create_run(self, payload):
        return self._call("create_run", **payload)

    def get_view(self, run_id):
        return self._call("get_view", run_id)

    def preview(self, run_id, command):
        return self._call("preview", run_id, command)

    def options(self, run_id, command):
        return self._call("options", run_id, command)

    def write(self, run_id, operation, payload):
        method = {"commands": "submit", "reactions": "react", "retry": "retry"}[operation]
        return self._call(method, run_id, **payload)

    def events(self, run_id, after=0, limit=20):
        return self._call("events", run_id, after=after, limit=limit)

    def close(self):
        self.repository.close()
