"""Cooperative POSIX compute lease. Candidate only; no listener, queue or lock deletion.
Use the same lock inode for ear analyze, optional rhythm and eye look. Keep the fd
in every model child with pass_fds; close references, never LOCK_UN a shared fd.
"""
import fcntl
import os
import stat

DEFAULT_LOCK = os.path.expanduser('~/AI-Listen/.analyze.lock')
FD_ENV = 'AI_HEAVY_LOCK_FD'
PATH_ENV = 'AI_HEAVY_LOCK_PATH'


class HeavyJobError(ValueError):
    code = 'configuration'


class HeavyJobBusy(HeavyJobError):
    code = 'busy'


def _verify(fd, path):
    if not isinstance(fd, int) or fd < 3:
        raise HeavyJobError('heavy-job descriptor must not alias stdin/stdout/stderr')
    actual, wanted = os.fstat(fd), os.lstat(path)
    if (not stat.S_ISREG(actual.st_mode) or not stat.S_ISREG(wanted.st_mode)
            or actual.st_uid != os.getuid() or wanted.st_uid != os.getuid()
            or (actual.st_dev, actual.st_ino) != (wanted.st_dev, wanted.st_ino)):
        raise HeavyJobError('inherited heavy-job descriptor does not match the shared regular lock file')


class Lease:
    """Immediate busy/no queue. An inherited reference is borrowed, not unlocked/closed.
    New references are closed on exit; inherited model-child references keep exclusion
    alive if an owning parent is killed. No claim covers an unadapted direct model call.
    """
    def __init__(self, path=DEFAULT_LOCK, inherited_fd=None):
        self.path = os.path.abspath(os.fspath(path))
        self.fd = inherited_fd
        self.owns_fd = inherited_fd is None
        self.entered = False

    def __enter__(self):
        if self.entered:
            raise HeavyJobError('lease object is already entered')
        try:
            if self.owns_fd:
                self.fd = os.open(self.path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
            _verify(self.fd, self.path)
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            if self.owns_fd and self.fd is not None:
                os.close(self.fd); self.fd = None
            raise HeavyJobBusy('another ear/eye heavy job holds the shared compute lock') from exc
        except (OSError, TypeError, ValueError) as exc:
            if self.owns_fd and self.fd is not None:
                os.close(self.fd); self.fd = None
            if isinstance(exc, HeavyJobError):
                raise
            raise HeavyJobError('heavy-job lock unavailable: '+str(exc)[:500]) from exc
        self.entered = True
        return self

    def __exit__(self, *args):
        if self.owns_fd and self.fd is not None:
            os.close(self.fd)  # NOT LOCK_UN: child/ancestor references share this open-file description
            self.fd = None
        self.entered = False

    def pass_fds(self):
        if not self.entered or self.fd is None:
            raise HeavyJobError('heavy-job lease is not active')
        _verify(self.fd, self.path)
        return (self.fd,)

    def child_env(self, base):
        """Explicit environment for a new participant; does not mutate process-global os.environ."""
        self.pass_fds()
        return dict(base, **{FD_ENV: str(self.fd), PATH_ENV: self.path})

    def exec_env(self, base):
        """For the fixed ml wrapper's execve, not subprocess. The same fd survives exec."""
        env = self.child_env(base)
        os.set_inheritable(self.fd, True)
        return env


def from_env(path=DEFAULT_LOCK, env=None):
    """Only reuse an explicitly passed, inode-verified fd. Never trust a marker alone."""
    env = os.environ if env is None else env
    marker, inherited_path = env.get(FD_ENV), env.get(PATH_ENV)
    if marker is None and inherited_path is None:
        return Lease(path)
    if (not isinstance(marker, str) or not marker.isdecimal()
            or inherited_path != os.path.abspath(os.fspath(path))):
        raise HeavyJobError('invalid inherited heavy-job marker')
    return Lease(path, int(marker))
