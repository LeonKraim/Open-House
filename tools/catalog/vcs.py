"""Git as the source of truth about what is in the repository.

Three checks need the repository's file list or its history, and all three need
the same guarantee: that the answer is about *this repository* and not about
whatever happens to be lying in the working tree. A virtualenv, a built panel, a
scratch file -- none of them are the project, and a check that read them would be
slower, would fail differently on different machines for reasons that have
nothing to do with the project, and would be enforcing something other than what
the requirement says. The registry-boundary requirement says **committed
artifact files**, so that is what is enumerated here.

Untracked files that are not ignored are included alongside the tracked ones.
That is a strictness the requirement does not demand and it is deliberate: the
requirement's stated purpose is that pointer files cannot accrete *in the
meantime*, and accrual happens in the working tree before it happens in the
index. Ignored files are excluded whatever they contain, which is what keeps the
four reference clones and the withheld-expression store out of every scan.
"""

from __future__ import annotations

import subprocess

from . import paths


def run_git(*args: str) -> subprocess.CompletedProcess[str]:
    """Run git in the repository root and capture its output as text.

    For plumbing that yields hashes, refs and path names -- all of which are
    ASCII by construction -- this is the right shape. It is *not* the right shape
    for file contents; see `run_git_bytes`.
    """
    return subprocess.run(
        ["git", *args],
        cwd=paths.ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def run_git_bytes(*args: str) -> subprocess.CompletedProcess[bytes]:
    """Run git and capture raw bytes.

    Text mode enables universal-newline translation, which folds `\\r\\n` and
    `\\r` to `\\n` on the way in. Two Phase 0 checks compare a working-tree file
    against what git has stored, and one side of that comparison is byte-exact
    (`errors.read_text`). Reading the other side through a translating decoder
    would make the two sides disagree about a file that was merely checked out
    with different line endings -- which is precisely the false positive the
    byte-exact read exists to avoid.
    """
    return subprocess.run(
        ["git", *args],
        cwd=paths.ROOT,
        capture_output=True,
        text=False,
        check=False,
    )


def is_repository() -> bool:
    """Whether the project root is inside a git working tree.

    The requirement that the project be under version control is not decorative:
    without history the immutability check has nothing to compare against, and a
    check that quietly passed there would be reporting on a repository it never
    read.
    """
    proc = run_git("rev-parse", "--is-inside-work-tree")
    return proc.returncode == 0 and proc.stdout.strip() == "true"


def listed_files() -> list[str]:
    """Every committed file, plus every untracked file that is not ignored.

    `-z` rather than a newline split because a path may legally contain a
    newline, and a file whose name split a line would be scanned as two paths
    that both exist and neither of which is real.
    """
    proc = run_git("ls-files", "--cached", "--others", "--exclude-standard", "-z")
    if proc.returncode != 0:
        return []
    return [entry for entry in proc.stdout.split("\0") if entry]


def tracked_files() -> list[str]:
    """Every file git has a record of, and nothing else.

    `-z` for the same reason `listed_files` uses it: a path may legally contain a
    newline, and splitting on one would read a single file as two paths that both
    exist and neither of which is real.
    """
    proc = run_git("ls-files", "-z")
    if proc.returncode != 0:
        return []
    return [entry for entry in proc.stdout.split("\0") if entry]


def introducing_commit(relative_path: str) -> str | None:
    """The commit that first added a path, or None if git has no record of it.

    `--diff-filter=A` restricts the log to the commit that *added* the file, and
    `--follow` is deliberately absent: a rename should read as a different path
    rather than silently inheriting the old one's history.
    """
    proc = run_git("log", "--diff-filter=A", "--format=%H", "--", relative_path)
    if proc.returncode != 0:
        return None
    commits = [line for line in proc.stdout.splitlines() if line.strip()]
    return commits[-1] if commits else None


def introduced_paths(prefix: str) -> list[str]:
    """Every path git has ever recorded as *added* under a prefix.

    This is the candidate set for "a published file that is no longer there".
    The immutability check cannot learn that a file was deleted from the working
    tree, because a deleted file is not in the working tree; it cannot learn it
    from the `supersedes` links either, because a deleted *current* version is
    named by nobody. History is the only place the answer exists, so the question
    asked here is "was this ever part of the repository", and a path that was
    added and later removed is still listed.

    A path added and then removed is not filtered out: its absence from the
    working tree is exactly the finding.

    `-z` again, and here it is not merely consistent with `listed_files` but
    necessary: the same repositories whose filenames carry non-ASCII also carry
    odd names, and a newline inside one would split it into two paths.
    """
    proc = run_git(
        "log", "--diff-filter=A", "--name-only", "--format=", "-z", "--", prefix
    )
    if proc.returncode != 0:
        return []
    return [entry for entry in proc.stdout.split("\0") if entry.strip()]


def file_at(commit: str, relative_path: str) -> str | None:
    """A file's content at a commit, or None when it is not readable there.

    Compared against `errors.read_text` by the immutability check, so a file
    that changed and a file that was checked out differently do not look alike.
    The bytes are decoded here rather than by `subprocess`, so that no newline
    translation happens between git's copy and the working-tree copy.
    """
    proc = run_git_bytes("show", f"{commit}:{relative_path}")
    if proc.returncode != 0:
        return None
    try:
        return proc.stdout.decode("utf-8")
    except UnicodeDecodeError:
        # A stored blob that is not UTF-8 cannot equal a working-tree file read
        # as UTF-8 either, so reporting it as unreadable is the honest answer.
        return None
