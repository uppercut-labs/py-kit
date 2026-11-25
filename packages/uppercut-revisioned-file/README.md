# uppercut-revisioned-file

Two things for code that writes files other code reads. First, atomic writes: `write_bytes`, `write_text` and `write_json` replace a file so that a crash or a second writer leaves either the old bytes or the new ones, never a half-written file. Second, `RevisionedFile`: one document edited whole by several clients, with an opaque revision, a conflict error that carries the current revision, fsynced backups with retention, and optional line-ending repair. Standard library only.

```sh
pip install uppercut-revisioned-file
```

## Atomic writes

```python
from uppercut_revisioned_file import write_json, write_text

write_json("state/run.json", {"status": "done", "items": 12})  # two-space indent, sorted keys, trailing newline
write_text("notes/today.txt", "first line\nsecond line\n")
```

Each write goes to a new temporary file in the same directory (its name is unique, so writers never share one), is flushed and fsynced, is renamed over the target with `os.replace`, and then the directory is fsynced where the platform allows it. Missing parent directories are created. An existing file keeps its permission bits on POSIX. On Windows the replace is retried for about one second while another process holds the file open. If any step fails the target is unchanged and the temporary file is removed.

`write_json` serializes the value before touching the disk, and refuses NaN and infinity because they are not valid JSON. `write_text` encodes UTF-8 and writes line endings exactly as given.

## RevisionedFile

```python
from uppercut_revisioned_file import RevisionConflict, RevisionedFile

doc = RevisionedFile("data/sheet.txt", backups="data/.sheet-backups", keep=20, max_bytes=1_000_000)

data, revision = doc.read()                  # bytes and an opaque revision string
try:
    new_revision, unchanged = doc.write(edited, revision)
except RevisionConflict as conflict:
    current = conflict.current_revision      # reload, reapply the edit, and try again
```

| Member | Meaning |
| --- | --- |
| `RevisionedFile(path, *, backups=None, keep=20, max_bytes=1 MiB, repair_line_endings=False)` | `backups` defaults to `.<stem>-backups` beside the file. `keep` must be at least 1 |
| `read()` | `(bytes, revision)`. The file must exist |
| `write(data, base_revision)` | `(revision, unchanged)`. Raises `RevisionConflict` when the file no longer has `base_revision`, and `FileTooLarge` when the content is over `max_bytes` |
| `RevisionConflict.current_revision` | The revision on disk when the write was refused |
| `revision_of(data)` | The revision of some bytes: 16 hex characters of SHA-256. Clients should treat it as opaque |

Before a changed file is replaced, its previous bytes are copied to the backups directory. Each backup is created exclusively and fsynced, and only the newest `keep` are kept. Writing bytes identical to the file returns `unchanged=True` and changes nothing.

### Line-ending repair

With `repair_line_endings=True`, a file that mixes CRLF, LF and lone CR is rewritten to its majority ending. `read()` repairs the file on disk after a backup and returns the repaired bytes with the new revision. If that rewrite fails, `read()` still returns the repaired bytes with the on-disk revision, so the next `write` replaces the mixed endings. `write()` converts incoming line breaks to the file's ending, so a save never adds a mix. Content that is not UTF-8 is never rewritten. Without the option, bytes are stored exactly as given.

## Notes

- A revision check and a replace are serialized per `RevisionedFile` instance. Another program editing the file between the check and the replace can still race it.
- Backups hold the full previous contents, so they can contain whatever the file holds. Backup files are created with `tempfile.mkstemp`, so they are private on POSIX; the backup directory itself gets the default mode, so keep it somewhere only the owner can reach if the file holds secrets.
- Directory fsync is skipped on Windows and ignored where a filesystem refuses it. The file itself is always fsynced before the rename.

## License

MIT
