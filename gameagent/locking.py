"""Process-owned project mutation lock; released by the OS after crashes."""
from contextlib import contextmanager
import os
from pathlib import Path


@contextmanager
def project_lock(project: Path):
    project = Path(project).resolve()
    sidecar = project.parent / f'{project.name}.agent'
    sidecar.mkdir(parents=True, exist_ok=True)
    stream = (sidecar / 'project.lock').open('a+b')
    acquired = False
    try:
        stream.seek(0, 2)
        if stream.tell() == 0:
            stream.write(b'1'); stream.flush()
        stream.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
        except OSError as exc:
            raise RuntimeError(f'Project is already being modified by another LudoFabric process: {project}') from exc
        yield
    finally:
        if acquired:
            stream.seek(0)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        stream.close()
