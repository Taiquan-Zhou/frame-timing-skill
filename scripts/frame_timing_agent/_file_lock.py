from __future__ import annotations

import errno
import os
import stat
from pathlib import Path
from typing import BinaryIO


class LockUnavailableError(RuntimeError):
    """Raised when another process owns a non-blocking file lock."""


class UnsafeLockFileError(ValueError):
    """Raised when a lock path does not resolve to the file that was opened."""


def open_lock_file(lock_path: Path) -> BinaryIO:
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(lock_path, flags, 0o600)
    except OSError as error:
        raise UnsafeLockFileError("lock file is unsafe") from error
    try:
        path_stat = lock_path.lstat()
        file_stat = os.fstat(descriptor)
        reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
        unsafe = (
            not stat.S_ISREG(path_stat.st_mode)
            or path_stat.st_nlink != 1
            or (reparse_flag and getattr(path_stat, "st_file_attributes", 0) & reparse_flag)
            or (path_stat.st_dev, path_stat.st_ino) != (file_stat.st_dev, file_stat.st_ino)
        )
        if unsafe:
            raise UnsafeLockFileError("lock file is unsafe")
        return os.fdopen(descriptor, "r+b")
    except BaseException:
        os.close(descriptor)
        raise


def acquire_lock(lock_file: BinaryIO) -> None:
    lock_file.seek(0, os.SEEK_END)
    if lock_file.tell() == 0:
        lock_file.write(b"L")
        lock_file.flush()
    lock_file.seek(0)
    if os.name == "nt":
        import msvcrt

        try:
            getattr(msvcrt, "locking")(lock_file.fileno(), getattr(msvcrt, "LK_NBLCK"), 1)
        except OSError as error:
            if error.errno in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                raise LockUnavailableError from error
            raise
        return
    import fcntl

    try:
        getattr(fcntl, "flock")(
            lock_file.fileno(),
            getattr(fcntl, "LOCK_EX") | getattr(fcntl, "LOCK_NB"),
        )
    except BlockingIOError as error:
        raise LockUnavailableError from error


def release_lock(lock_file: BinaryIO) -> None:
    lock_file.seek(0)
    if os.name == "nt":
        import msvcrt

        getattr(msvcrt, "locking")(lock_file.fileno(), getattr(msvcrt, "LK_UNLCK"), 1)
        return
    import fcntl

    getattr(fcntl, "flock")(lock_file.fileno(), getattr(fcntl, "LOCK_UN"))
