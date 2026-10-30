from pathlib import Path
from uuid import uuid4

from scholarmotion.persistence.storage import LocalObjectStore
from scripts.migrate_project_storage import relocate


def test_project_files_live_in_the_owners_folder(tmp_path):
    store = LocalObjectStore(tmp_path)
    owner, project = uuid4(), uuid4()
    store.assign_project(project, owner)

    store.put_bytes(f"{project}/video/v1/final.mp4", b"video")

    assert (tmp_path / str(owner) / str(project) / "video/v1/final.mp4").read_bytes() == b"video"
    assert store.local_path(f"{project}/video/v1/final.mp4").parent.parent.parent.parent.name == (
        str(owner)
    )


def test_a_new_store_finds_an_assigned_project(tmp_path):
    owner, project = uuid4(), uuid4()
    LocalObjectStore(tmp_path).assign_project(project, owner)

    # The worker runs in another process with its own store.
    LocalObjectStore(tmp_path).put_bytes(f"{project}/notes.txt", b"x")

    assert (tmp_path / str(owner) / str(project) / "notes.txt").is_file()


def test_unassigned_projects_and_other_keys_stay_flat(tmp_path):
    store = LocalObjectStore(tmp_path)
    project = uuid4()
    store.assign_project(project, None)

    store.put_bytes(f"{project}/a.json", b"{}")
    store.put_bytes("shared/b.json", b"{}")

    assert (tmp_path / str(project) / "a.json").is_file()
    assert (tmp_path / "shared/b.json").is_file()


def test_relocate_moves_paths_under_the_owner_and_is_idempotent():
    owner, project = str(uuid4()), str(uuid4())
    new_root = Path("/data/projects")
    old = f"C:\\old\\projects\\{project}\\scenes\\S01\\code\\v1.py"

    moved = relocate(old, {project: owner}, new_root)

    assert moved == str(new_root / owner / project / "scenes" / "S01" / "code" / "v1.py")
    assert relocate(moved, {project: owner}, new_root) is None
    assert relocate(f"{project}/artifacts/x.json", {project: owner}, new_root) is None
