"""Per-project settings: marker name, exported variable and the run roots for each platform."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from pathlib import Path, PurePath, PurePosixPath, PureWindowsPath
from typing import Any

from ._runs import DEFAULT_MARKER, check_marker, clean, create

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - exercised on Python 3.10 only
    import tomli as tomllib

TABLE = "uppercut-scratch-runs"
DEFAULT_VARIABLE = "SCRATCH_RUN"
PLATFORMS = ("windows", "wsl", "linux", "macos", "default")
TEMP_VARIABLES = ("TMPDIR", "TEMP", "TMP")
_PROJECT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
_VARIABLE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_KEYS = {"project", "marker", "variable", "roots"}


class ConfigError(ValueError):
    """The scratch settings are missing or invalid."""


def _is_wsl() -> bool:
    release = Path("/proc/sys/kernel/osrelease")
    try:
        return "microsoft" in release.read_text().lower()
    except OSError:
        return False


def platform_keys() -> tuple[str, ...]:
    """The ``roots`` keys tried on this machine, most specific first."""
    if os.name == "nt":
        return ("windows", "default")
    if sys.platform.startswith("linux"):
        return ("wsl", "linux", "default") if _is_wsl() else ("linux", "default")
    if sys.platform == "darwin":
        return ("macos", "default")
    return ("default",)


def _root_path(value: Any, where: str, key: str = "default") -> Path:
    if not isinstance(value, (str, os.PathLike)) or not os.fspath(value):
        raise ConfigError(f"{where} must be a path string")
    raw = value.as_posix() if isinstance(value, PurePath) else os.fspath(value)
    expanded = os.path.expanduser(raw)
    # A file shared between machines lists Windows roots on a Mac and POSIX roots on Windows,
    # so each key is checked with the path rules of the platform it is for.
    flavour = {"windows": PureWindowsPath, "default": Path}.get(key, PurePosixPath)
    if not flavour(expanded).is_absolute():
        raise ConfigError(f"{where} must be absolute (a leading ~ is expanded): {value}")
    return Path(expanded)


def _roots(value: Any) -> dict[str, tuple[Path, ...]]:
    if not isinstance(value, Mapping):
        raise ConfigError("roots must be a table of platform = [paths]")
    roots: dict[str, tuple[Path, ...]] = {}
    for key, paths in value.items():
        if key not in PLATFORMS:
            raise ConfigError(f"Unknown roots key {key!r}; use one of {', '.join(PLATFORMS)}")
        if isinstance(paths, (str, os.PathLike)):
            paths = [paths]
        if not isinstance(paths, Sequence) or not paths:
            raise ConfigError(f"roots.{key} must be a path or a non-empty list of paths")
        roots[key] = tuple(_root_path(path, f"roots.{key}", key) for path in paths)
    return roots


@dataclass(frozen=True)
class ScratchConfig:
    """Where runs go and how they are marked and exported.

    ``roots`` maps a platform key (``windows``, ``wsl``, ``linux``, ``macos`` or ``default``) to one
    or more absolute directories. The first root for the platform is the normal one; ``large=True``
    picks the last.
    """

    roots: Mapping[str, tuple[Path, ...]] = field(default_factory=dict)
    marker: str = DEFAULT_MARKER
    variable: str = DEFAULT_VARIABLE
    project: str | None = None

    def __post_init__(self) -> None:
        if self.project is not None and not _PROJECT.fullmatch(self.project):
            raise ConfigError(f"project must be letters, digits, '.', '_' or '-': {self.project!r}")
        try:
            check_marker(self.marker)
        except ValueError as exc:
            raise ConfigError(str(exc)) from None
        if not _VARIABLE.fullmatch(self.variable):
            raise ConfigError(f"variable must be an environment variable name: {self.variable!r}")
        object.__setattr__(self, "roots", _roots(dict(self.roots)))

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> ScratchConfig:
        """Build settings from a ``[tool.uppercut-scratch-runs]`` table."""
        unknown = set(data) - _KEYS
        if unknown:
            raise ConfigError(f"Unknown setting(s): {', '.join(sorted(unknown))}")
        project = data.get("project")
        if project is not None and not isinstance(project, str):
            raise ConfigError("project must be a string")
        marker = data.get("marker", f".{project}-run" if project else DEFAULT_MARKER)
        variable = data.get("variable", DEFAULT_VARIABLE)
        if not isinstance(marker, str) or not isinstance(variable, str):
            raise ConfigError("marker and variable must be strings")
        return cls(roots=_roots(data.get("roots", {})), marker=marker, variable=variable, project=project)

    def with_roots(self, roots: Sequence[os.PathLike | str]) -> ScratchConfig:
        """The same settings with ``roots`` used on every platform."""
        return replace(self, roots={"default": tuple(_root_path(root, "root") for root in roots)})

    def platform_roots(self, keys: Sequence[str] | None = None) -> tuple[Path, ...]:
        """The roots for this machine: the first ``roots`` key in ``keys`` that is set."""
        keys = platform_keys() if keys is None else tuple(keys)
        for key in keys:
            if key in self.roots:
                return self.roots[key]
        wanted = ", ".join(f"roots.{key}" for key in keys)
        raise ConfigError(f"No scratch roots for this platform; set one of {wanted}")

    def root(self, *, large: bool = False) -> Path:
        roots = self.platform_roots()
        return roots[-1] if large else roots[0]

    def new(self, *, large: bool = False) -> Path:
        """Allocate a run and return its absolute path."""
        return create(self.root(large=large), marker=self.marker)

    def clean(self, run: os.PathLike | str) -> None:
        """Remove a run made by :meth:`new` under any of this platform's roots."""
        clean(Path(run), self.platform_roots(), marker=self.marker)

    def environment(self, run: os.PathLike | str, base: Mapping[str, str] | None = None) -> dict[str, str]:
        """``base`` (default ``os.environ``) with ``TMPDIR``, ``TEMP``, ``TMP`` and the variable set to ``run``."""
        env = dict(os.environ if base is None else base)
        for name in (*TEMP_VARIABLES, self.variable):
            env[name] = str(run)
        return env

    @contextmanager
    def session(self, *, large: bool = False) -> Iterator[Path]:
        """Allocate a run for the ``with`` block and remove it afterwards, also on an exception."""
        run = self.new(large=large)
        try:
            yield run
        finally:
            self.clean(run)

    def run(self, command: Sequence[str], *, large: bool = False, **kwargs: Any) -> subprocess.CompletedProcess:
        """Run ``command`` with the run exported, then remove the run whatever the command returned."""
        with self.session(large=large) as run:
            env = self.environment(run, kwargs.pop("env", None))
            return subprocess.run(list(command), env=env, **kwargs)


def find_config(start: os.PathLike | str | None = None) -> Path | None:
    """The nearest ``pyproject.toml`` at or above ``start`` (default: the working directory) with the table."""
    directory = Path(start or os.getcwd()).absolute()
    for candidate in (directory, *directory.parents):
        path = candidate / "pyproject.toml"
        if path.is_file() and TABLE in _read(path).get("tool", {}):
            return path
    return None


def _read(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as stream:
            return tomllib.load(stream)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path}: {exc}") from None


def load_table(path: os.PathLike | str | None = None, *, start: os.PathLike | str | None = None) -> tuple[Path | None, dict[str, Any]]:
    """The raw ``[tool.uppercut-scratch-runs]`` table and the file it came from.

    With no ``path``, :func:`find_config` looks upward from ``start``; if nothing is found the table is empty.
    """
    if path is None:
        path = find_config(start)
        if path is None:
            return None, {}
    path = Path(path)
    table = _read(path).get("tool", {}).get(TABLE)
    if not isinstance(table, Mapping):
        raise ConfigError(f"{path} has no [tool.{TABLE}] table")
    return path, dict(table)


def load_config(path: os.PathLike | str | None = None, *, start: os.PathLike | str | None = None) -> ScratchConfig:
    """Settings from ``path``, or from the file :func:`find_config` finds.

    With no file found this returns the defaults with no roots, so callers can still pass roots.
    """
    source, table = load_table(path, start=start)
    try:
        return ScratchConfig.from_mapping(table)
    except ConfigError as exc:
        raise ConfigError(f"{source}: {exc}" if source else str(exc)) from None
