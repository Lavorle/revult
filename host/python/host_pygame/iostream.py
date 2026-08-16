"""IOStream bridge for host (replaces renpy.pygame.iostream / SDL_IOStream).

Classic Ren'Py binds ``renpy.loader.open_file = IOStream`` and calls
``IOStream(path, "rb")`` / ``from_buffer`` / ``from_split``. The original host
stub only accepted ``IOStream(data=b"")``, so ``open_file(fn, "rb")`` raised
TypeError, was swallowed in ``load_core``, and painted present-on-disk assets
as ``Couldn't find file`` error tiles while ``loadable()`` stayed True.
"""

from __future__ import annotations

import io
import os
from typing import Optional, Union


def _as_bytes(data) -> bytes:
    if data is None:
        return b""
    if isinstance(data, memoryview):
        return data.tobytes()
    if isinstance(data, (bytes, bytearray)):
        return bytes(data)
    if isinstance(data, str):
        return data.encode("utf-8")
    # buffer protocol
    try:
        return bytes(data)
    except Exception:
        raise TypeError(f"cannot convert {type(data)!r} to bytes") from None


class IOStream(io.RawIOBase):
    """File-like stream compatible with classic ``renpy.pygame.iostream.IOStream``.

    Constructor forms used by the engine:
      * ``IOStream(path, mode='rb', base=None, length=None, name=None)``
      * ``IOStream(filelike, mode='rb', ...)``
      * ``IOStream(None, name=...)`` then fill via classmethods
      * legacy host: ``IOStream(bytes_or_str)`` as an in-memory buffer
    """

    def __init__(
        self,
        filelike=b"",
        mode: str = "rb",
        base: Optional[int] = None,
        length: Optional[int] = None,
        name: Optional[str] = None,
    ):
        super().__init__()
        self.mode = mode or "rb"
        self.base = base
        self.length = length
        self._closed = False
        self._pos = 0
        self._data = bytearray()
        self.name = name

        # Empty shell (from_buffer / from_split construct then assign).
        if filelike is None:
            if name is not None:
                self.name = name
            return

        # Legacy host / from_memory: single bytes-like or bare str *content*
        # (not a path). Distinguishes path strings by mode+base/length usage:
        # loader always passes mode explicitly as second arg.
        if isinstance(filelike, (bytes, bytearray, memoryview)):
            self._data = bytearray(_as_bytes(filelike))
            self.name = name if name is not None else getattr(filelike, "name", None)
            return

        if isinstance(filelike, str) and base is None and length is None and name is None and mode == "rb":
            # Ambiguous: path vs legacy string-as-content. Prefer path if it exists.
            if os.path.isfile(filelike) or os.path.sep in filelike or (
                os.path.altsep and os.path.altsep in filelike
            ):
                self._load_path(filelike, mode, base, length, name)
                return
            # legacy: treat as text content
            self._data = bytearray(filelike.encode("utf-8"))
            self.name = name
            return

        if isinstance(filelike, str):
            self._load_path(filelike, mode, base, length, name)
            return

        # File-like object
        if hasattr(filelike, "read"):
            self.name = name if name is not None else getattr(filelike, "name", None)
            data = filelike.read()
            data = _as_bytes(data)
            if base is not None or length is not None:
                start = int(base or 0)
                end = start + int(length) if length is not None else None
                data = data[start:end]
            self._data = bytearray(data)
            return

        raise TypeError(
            f"IOStream expected path, bytes, or file-like; got {type(filelike)!r}"
        )

    def _load_path(
        self,
        path: str,
        mode: str,
        base: Optional[int],
        length: Optional[int],
        name: Optional[str],
    ) -> None:
        self.name = name if name is not None else path
        # Always open binary for image/asset loads; honor 'b' if present.
        open_mode = mode if "b" in mode else mode + "b"
        # Normalize write modes away — host assets are read through loader.
        if "r" not in open_mode:
            open_mode = "rb"
        with open(path, open_mode) as f:
            if base is not None:
                f.seek(int(base))
            if length is not None:
                data = f.read(int(length))
            else:
                data = f.read()
        self._data = bytearray(data)
        self.base = base
        self.length = length

    # --- classmethods (classic API) -----------------------------------------

    @staticmethod
    def from_buffer(buffer, mode: str = "rb", name: Optional[str] = None) -> "IOStream":
        rv = IOStream(None, mode=mode, name=name)
        rv._data = bytearray(_as_bytes(buffer))
        rv._pos = 0
        return rv

    @staticmethod
    def from_split(a, b, name: Optional[str] = None) -> "IOStream":
        def _read_all(part) -> bytes:
            if part is None:
                return b""
            if isinstance(part, IOStream):
                cur = part.tell()
                try:
                    part.seek(0)
                    return part.read() or b""
                finally:
                    part.seek(cur)
            if isinstance(part, (bytes, bytearray, memoryview)):
                return _as_bytes(part)
            if hasattr(part, "read"):
                cur = part.tell() if hasattr(part, "tell") else None
                if hasattr(part, "seek"):
                    part.seek(0)
                data = part.read() or b""
                if cur is not None and hasattr(part, "seek"):
                    part.seek(cur)
                return _as_bytes(data)
            raise TypeError(f"from_split part not readable: {type(part)!r}")

        rv = IOStream(None, name=name)
        rv._data = bytearray(_read_all(a) + _read_all(b))
        rv._pos = 0
        return rv

    # --- io.RawIOBase -------------------------------------------------------

    def readable(self) -> bool:
        return True

    def writable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    @property
    def closed(self) -> bool:
        return self._closed

    def close(self) -> None:
        self._closed = True

    def tell(self) -> int:
        if self._closed:
            raise ValueError("I/O operation on closed file.")
        return self._pos

    def seek(self, offset: int, whence: int = 0) -> int:
        if self._closed:
            raise ValueError("I/O operation on closed file.")
        if whence == 0:
            self._pos = int(offset)
        elif whence == 1:
            self._pos += int(offset)
        elif whence == 2:
            self._pos = len(self._data) + int(offset)
        else:
            raise ValueError(f"invalid whence: {whence}")
        if self._pos < 0:
            self._pos = 0
        return self._pos

    def read(self, n: int = -1) -> bytes:  # type: ignore[override]
        if self._closed:
            raise ValueError("I/O operation on closed file.")
        if n is None or n < 0:
            n = len(self._data) - self._pos
        out = bytes(self._data[self._pos : self._pos + n])
        self._pos += len(out)
        return out

    def readinto(self, b) -> int:  # type: ignore[override]
        if self._closed:
            raise ValueError("I/O operation on closed file.")
        mv = memoryview(b).cast("B")
        n = len(mv)
        chunk = self.read(n)
        mv[: len(chunk)] = chunk
        return len(chunk)

    def write(self, data) -> int:  # type: ignore[override]
        if self._closed:
            raise ValueError("I/O operation on closed file.")
        raw = _as_bytes(data)
        end = self._pos + len(raw)
        if end > len(self._data):
            self._data.extend(b"\x00" * (end - len(self._data)))
        self._data[self._pos : end] = raw
        self._pos = end
        return len(raw)

    def __repr__(self) -> str:
        if self.base is not None:
            return f"<host.IOStream {self.name!r} base={self.base!r} length={self.length!r}>"
        return f"<host.IOStream {self.name!r}>"


# Module-level helpers used by renpy.wgpu.model / assimp gate (Phase 1–2 bridge).

def from_file(path: str, mode: str = "rb") -> IOStream:
    with open(path, mode if "b" in mode else mode + "b") as f:
        return IOStream.from_buffer(f.read(), name=path)


def from_memory(data) -> IOStream:
    return IOStream.from_buffer(data)
