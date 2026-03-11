"""Tests for the git package."""

import os
import subprocess

import pytest

from git.manager import (
    GENERATED_PREFIX,
    INTENT_PREFIX,
    REFINE_PREFIX,
    GitCLIManager,
    GitStatus,
    new_git_manager,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _init_repo(path: str) -> None:
    """Create a fresh git repo with an initial commit at *path*."""
    subprocess.run(["git", "init", path], capture_output=True, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=path, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, capture_output=True, check=True)
    # Initial commit so HEAD exists.
    dummy = os.path.join(path, ".gitkeep")
    with open(dummy, "w") as f:
        f.write("")
    subprocess.run(["git", "add", "."], cwd=path, capture_output=True, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=path, capture_output=True, check=True)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

def test_commit_prefix_constants():
    assert INTENT_PREFIX == "intent:"
    assert GENERATED_PREFIX == "generated:"
    assert REFINE_PREFIX == "refine:"


# ---------------------------------------------------------------------------
# GitStatus model
# ---------------------------------------------------------------------------

def test_git_status_defaults():
    status = GitStatus()
    assert status.branch == ""
    assert status.clean is True
    assert status.modified_files == []
    assert status.untracked_files == []
    assert status.staged_files == []


def test_git_status_custom():
    status = GitStatus(
        branch="main",
        clean=False,
        modified_files=["a.py"],
        untracked_files=["b.py"],
        staged_files=["c.py"],
    )
    assert status.branch == "main"
    assert status.clean is False
    assert status.modified_files == ["a.py"]
    assert status.untracked_files == ["b.py"]
    assert status.staged_files == ["c.py"]


def test_git_status_serializable():
    status = GitStatus(branch="main", clean=True)
    data = status.model_dump()
    assert data["branch"] == "main"
    assert data["clean"] is True


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def test_new_git_manager():
    mgr = new_git_manager()
    assert isinstance(mgr, GitCLIManager)


# ---------------------------------------------------------------------------
# initialize
# ---------------------------------------------------------------------------

def test_initialize_valid_repo(tmp_path):
    repo = str(tmp_path / "repo")
    _init_repo(repo)
    mgr = GitCLIManager()
    mgr.initialize(repo)  # Should not raise.


def test_initialize_not_a_repo(tmp_path):
    not_repo = str(tmp_path / "not_a_repo")
    os.makedirs(not_repo)
    mgr = GitCLIManager()
    with pytest.raises(RuntimeError, match="not a git repository"):
        mgr.initialize(not_repo)


# ---------------------------------------------------------------------------
# is_git_repo
# ---------------------------------------------------------------------------

def test_is_git_repo_true(tmp_path):
    repo = str(tmp_path / "repo")
    _init_repo(repo)
    mgr = GitCLIManager()
    mgr.initialize(repo)
    assert mgr.is_git_repo() is True


def test_is_git_repo_false(tmp_path):
    not_repo = str(tmp_path / "dir")
    os.makedirs(not_repo)
    mgr = GitCLIManager()
    mgr._project_root = not_repo
    assert mgr.is_git_repo() is False


# ---------------------------------------------------------------------------
# get_current_branch
# ---------------------------------------------------------------------------

def test_get_current_branch(tmp_path):
    repo = str(tmp_path / "repo")
    _init_repo(repo)
    mgr = GitCLIManager()
    mgr.initialize(repo)
    # Default branch name varies; just confirm it returns a non-empty string.
    branch = mgr.get_current_branch()
    assert isinstance(branch, str)
    assert len(branch) > 0


# ---------------------------------------------------------------------------
# get_commit_hash
# ---------------------------------------------------------------------------

def test_get_commit_hash(tmp_path):
    repo = str(tmp_path / "repo")
    _init_repo(repo)
    mgr = GitCLIManager()
    mgr.initialize(repo)
    commit_hash = mgr.get_commit_hash()
    assert isinstance(commit_hash, str)
    assert len(commit_hash) == 40  # Full SHA-1 hash.


# ---------------------------------------------------------------------------
# add and commit
# ---------------------------------------------------------------------------

def test_add_and_commit(tmp_path):
    repo = str(tmp_path / "repo")
    _init_repo(repo)
    mgr = GitCLIManager()
    mgr.initialize(repo)

    # Create a new file and commit it.
    filepath = os.path.join(repo, "hello.txt")
    with open(filepath, "w") as f:
        f.write("hello world")

    mgr.add(["hello.txt"])
    mgr.commit("add hello.txt")

    # Verify the commit happened by checking the log.
    result = subprocess.run(
        ["git", "log", "--oneline", "-1"],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    assert "add hello.txt" in result.stdout


def test_commit_with_prefix(tmp_path):
    repo = str(tmp_path / "repo")
    _init_repo(repo)
    mgr = GitCLIManager()
    mgr.initialize(repo)

    filepath = os.path.join(repo, "intent.ic")
    with open(filepath, "w") as f:
        f.write("name: test")

    mgr.add(["intent.ic"])
    mgr.commit(f"{INTENT_PREFIX} add intent spec")

    result = subprocess.run(
        ["git", "log", "--oneline", "-1"],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    assert INTENT_PREFIX in result.stdout


# ---------------------------------------------------------------------------
# get_status
# ---------------------------------------------------------------------------

def test_status_clean(tmp_path):
    repo = str(tmp_path / "repo")
    _init_repo(repo)
    mgr = GitCLIManager()
    mgr.initialize(repo)

    status = mgr.get_status()
    assert status.clean is True
    assert status.modified_files == []
    assert status.untracked_files == []
    assert status.staged_files == []
    assert len(status.branch) > 0


def test_status_untracked(tmp_path):
    repo = str(tmp_path / "repo")
    _init_repo(repo)
    mgr = GitCLIManager()
    mgr.initialize(repo)

    # Create an untracked file.
    with open(os.path.join(repo, "new_file.txt"), "w") as f:
        f.write("new")

    status = mgr.get_status()
    assert status.clean is False
    assert "new_file.txt" in status.untracked_files


def test_status_staged(tmp_path):
    repo = str(tmp_path / "repo")
    _init_repo(repo)
    mgr = GitCLIManager()
    mgr.initialize(repo)

    # Create and stage a file.
    with open(os.path.join(repo, "staged.txt"), "w") as f:
        f.write("staged content")

    mgr.add(["staged.txt"])

    status = mgr.get_status()
    assert status.clean is False
    assert "staged.txt" in status.staged_files


def test_status_modified(tmp_path):
    repo = str(tmp_path / "repo")
    _init_repo(repo)
    mgr = GitCLIManager()
    mgr.initialize(repo)

    # Create, commit, then modify a file.
    filepath = os.path.join(repo, "modify_me.txt")
    with open(filepath, "w") as f:
        f.write("original")
    mgr.add(["modify_me.txt"])
    mgr.commit("add modify_me.txt")

    with open(filepath, "w") as f:
        f.write("modified")

    status = mgr.get_status()
    assert status.clean is False
    assert "modify_me.txt" in status.modified_files


def test_status_staged_modified(tmp_path):
    """A file that is staged and then modified again should appear in both lists."""
    repo = str(tmp_path / "repo")
    _init_repo(repo)
    mgr = GitCLIManager()
    mgr.initialize(repo)

    filepath = os.path.join(repo, "both.txt")
    with open(filepath, "w") as f:
        f.write("original")
    mgr.add(["both.txt"])
    mgr.commit("add both.txt")

    # Modify and stage.
    with open(filepath, "w") as f:
        f.write("staged version")
    mgr.add(["both.txt"])

    # Modify again without staging.
    with open(filepath, "w") as f:
        f.write("worktree version")

    status = mgr.get_status()
    assert status.clean is False
    assert "both.txt" in status.staged_files
    assert "both.txt" in status.modified_files


def test_status_new_staged_file(tmp_path):
    """A new file that is staged (A in index) should appear in staged_files."""
    repo = str(tmp_path / "repo")
    _init_repo(repo)
    mgr = GitCLIManager()
    mgr.initialize(repo)

    with open(os.path.join(repo, "brand_new.txt"), "w") as f:
        f.write("content")
    mgr.add(["brand_new.txt"])

    status = mgr.get_status()
    assert "brand_new.txt" in status.staged_files


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------

def test_add_nonexistent_file(tmp_path):
    repo = str(tmp_path / "repo")
    _init_repo(repo)
    mgr = GitCLIManager()
    mgr.initialize(repo)

    with pytest.raises(RuntimeError, match="failed"):
        mgr.add(["does_not_exist.txt"])


def test_commit_nothing_staged(tmp_path):
    repo = str(tmp_path / "repo")
    _init_repo(repo)
    mgr = GitCLIManager()
    mgr.initialize(repo)

    with pytest.raises(RuntimeError, match="failed"):
        mgr.commit("empty commit")


def test_get_commit_hash_no_commits(tmp_path):
    """Attempting to get HEAD on a brand-new repo with no commits should fail."""
    repo = str(tmp_path / "empty_repo")
    os.makedirs(repo)
    subprocess.run(["git", "init", repo], capture_output=True, check=True)
    mgr = GitCLIManager()
    mgr._project_root = repo
    with pytest.raises(RuntimeError, match="failed"):
        mgr.get_commit_hash()


# ---------------------------------------------------------------------------
# Multiple operations sequence
# ---------------------------------------------------------------------------

def test_full_workflow(tmp_path):
    """End-to-end: init, create files, add, commit, verify status and hash."""
    repo = str(tmp_path / "repo")
    _init_repo(repo)
    mgr = GitCLIManager()
    mgr.initialize(repo)

    assert mgr.is_git_repo() is True

    # Initially clean.
    status = mgr.get_status()
    assert status.clean is True
    hash1 = mgr.get_commit_hash()

    # Create and commit a file.
    with open(os.path.join(repo, "app.py"), "w") as f:
        f.write("print('hello')")

    status = mgr.get_status()
    assert status.clean is False
    assert "app.py" in status.untracked_files

    mgr.add(["app.py"])
    status = mgr.get_status()
    assert "app.py" in status.staged_files

    mgr.commit(f"{GENERATED_PREFIX} create app.py")
    status = mgr.get_status()
    assert status.clean is True

    hash2 = mgr.get_commit_hash()
    assert hash2 != hash1
    assert len(hash2) == 40


# ---------------------------------------------------------------------------
# get_diff / get_diff_stat
# ---------------------------------------------------------------------------

class TestGitDiff:
    def test_get_diff_empty_on_clean_repo(self, tmp_path):
        repo = str(tmp_path / "repo")
        _init_repo(repo)
        mgr = GitCLIManager()
        mgr.initialize(repo)

        assert mgr.get_diff() == ""

    def test_get_diff_shows_changes(self, tmp_path):
        repo = str(tmp_path / "repo")
        _init_repo(repo)
        mgr = GitCLIManager()
        mgr.initialize(repo)

        # Create and commit a file, then modify it.
        filepath = os.path.join(repo, "hello.txt")
        with open(filepath, "w") as f:
            f.write("original\n")
        mgr.add(["hello.txt"])
        mgr.commit("add hello.txt")

        with open(filepath, "w") as f:
            f.write("modified\n")

        diff = mgr.get_diff()
        assert "hello.txt" in diff
        assert "-original" in diff
        assert "+modified" in diff

    def test_get_diff_with_paths(self, tmp_path):
        repo = str(tmp_path / "repo")
        _init_repo(repo)
        mgr = GitCLIManager()
        mgr.initialize(repo)

        # Create and commit two files.
        for name in ("a.txt", "b.txt"):
            with open(os.path.join(repo, name), "w") as f:
                f.write("original\n")
        mgr.add(["a.txt", "b.txt"])
        mgr.commit("add files")

        # Modify both.
        for name in ("a.txt", "b.txt"):
            with open(os.path.join(repo, name), "w") as f:
                f.write("changed\n")

        diff = mgr.get_diff(paths=["a.txt"])
        assert "a.txt" in diff
        assert "b.txt" not in diff

    def test_get_diff_stat(self, tmp_path):
        repo = str(tmp_path / "repo")
        _init_repo(repo)
        mgr = GitCLIManager()
        mgr.initialize(repo)

        filepath = os.path.join(repo, "data.txt")
        with open(filepath, "w") as f:
            f.write("line1\n")
        mgr.add(["data.txt"])
        mgr.commit("add data.txt")

        with open(filepath, "w") as f:
            f.write("line1\nline2\n")

        stat = mgr.get_diff_stat()
        assert "1 file changed" in stat

    def test_get_diff_stat_empty(self, tmp_path):
        repo = str(tmp_path / "repo")
        _init_repo(repo)
        mgr = GitCLIManager()
        mgr.initialize(repo)

        assert mgr.get_diff_stat() == ""

    def test_get_diff_includes_untracked(self, tmp_path):
        repo = str(tmp_path / "repo")
        _init_repo(repo)
        mgr = GitCLIManager()
        mgr.initialize(repo)

        # Create a brand-new untracked file.
        filepath = os.path.join(repo, "new_file.txt")
        with open(filepath, "w") as f:
            f.write("brand new content\n")

        # Without include_untracked, new file should NOT appear.
        diff_without = mgr.get_diff()
        assert "new_file.txt" not in diff_without

        # With include_untracked and paths, it should appear.
        diff_with = mgr.get_diff(paths=["new_file.txt"], include_untracked=True)
        assert "new_file.txt" in diff_with
        assert "+brand new content" in diff_with
