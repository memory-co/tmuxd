"""State on disk: clues, not truth.

tmux is the truth about which sessions exist. These files hold the part tmux
cannot answer -- what directory a session started in, what command it ran,
when it was created, when it was last attached.

Two rules (works/01-library.md §6):

* **Atomic writes.** Temp file plus ``os.replace``, so what is on disk is
  always a complete JSON document.
* **Cross-process locking.** The library can be imported by several processes
  at once -- a web backend's workers, for instance -- so writes take a file
  lock rather than relying on there being a single writer.
"""

import errno
import fcntl
import json
import os
import time
from urllib.parse import quote


class Store:
    def __init__(self, root):
        self.root = root
        self.sessions_dir = os.path.join(root, "sessions")
        os.makedirs(self.sessions_dir, exist_ok=True)
        self._lock_path = os.path.join(root, ".lock")

    # -- locking --------------------------------------------------------

    class _Lock:
        def __init__(self, path):
            self.path = path
            self.fd = None

        def __enter__(self):
            self.fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
            fcntl.flock(self.fd, fcntl.LOCK_EX)
            return self

        def __exit__(self, *exc):
            fcntl.flock(self.fd, fcntl.LOCK_UN)
            os.close(self.fd)
            self.fd = None

    def lock(self):
        return self._Lock(self._lock_path)

    # -- paths ----------------------------------------------------------

    def path_for(self, sid):
        # Session ids may contain characters that are awkward in filenames
        # (slashes, above all), so the file name is the percent-encoded id.
        return os.path.join(self.sessions_dir, quote(sid, safe="") + ".json")

    # -- io -------------------------------------------------------------

    @staticmethod
    def _write_atomic(path, payload):
        tmp = path + ".tmp.%d" % os.getpid()
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2, sort_keys=True)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)

    def read(self, sid):
        try:
            with open(self.path_for(sid), encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return None

    def write(self, record):
        with self.lock():
            self._write_atomic(self.path_for(record["id"]), record)

    def touch_attached(self, sid):
        with self.lock():
            record = self.read(sid)
            if record is None:
                return None
            record["last_attached"] = now()
            self._write_atomic(self.path_for(sid), record)
            return record

    def delete(self, sid):
        try:
            os.unlink(self.path_for(sid))
        except OSError as exc:
            if exc.errno != errno.ENOENT:
                raise

    def rename(self, sid, new):
        with self.lock():
            record = self.read(sid)
            if record is None:
                return None
            record["id"] = new
            self._write_atomic(self.path_for(new), record)
            self.delete(sid)
            return record

    def all(self):
        records = {}
        for name in sorted(os.listdir(self.sessions_dir)):
            if not name.endswith(".json") or name.endswith(".tmp.json"):
                continue
            try:
                with open(os.path.join(self.sessions_dir, name), encoding="utf-8") as fh:
                    record = json.load(fh)
            except (OSError, ValueError):
                continue
            if isinstance(record, dict) and "id" in record:
                records[record["id"]] = record
        return records

    def age(self, sid):
        try:
            return time.time() - os.stat(self.path_for(sid)).st_mtime
        except OSError:
            return 0.0


def now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
