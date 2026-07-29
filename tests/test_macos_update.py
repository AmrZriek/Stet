
import pytest

import stet.macos_update as update


def test_bundle_detection_allows_bundle_root_but_rejects_contents(tmp_path):
    app = tmp_path / "Stet.app"

    assert update.is_inside_app_bundle(app, app) is False
    assert update.is_inside_app_bundle(app / "Contents" / "MacOS" / "Stet", app) is True


def test_update_staging_directory_is_external(tmp_path):
    app = tmp_path / "Stet.app"

    staging = update.update_staging_directory(temporary_root=tmp_path, app_bundle=app)

    assert staging == (tmp_path / "Stet-updates").resolve()
    assert not staging.exists()


def test_external_path_rejects_bundle_writes(tmp_path):
    app = tmp_path / "Stet.app"

    with pytest.raises(update.UnsafeUpdatePath):
        update.assert_external_path(app / "Contents" / "Resources" / "update.zip", app_bundle=app)


def test_external_path_rejects_any_app_bundle(tmp_path):
    current_app = tmp_path / "Stet.app"
    another_app_file = tmp_path / "Other.app" / "Contents" / "Resources" / "update.zip"

    with pytest.raises(update.UnsafeUpdatePath):
        update.assert_external_path(another_app_file, app_bundle=current_app)


def test_handoff_checks_paths_and_uses_external_process(tmp_path):
    app = tmp_path / "Stet.app"
    archive = tmp_path / "Stet-update.zip"
    installer = tmp_path / "StetInstaller"
    archive.write_bytes(b"archive")
    installer.write_bytes(b"installer")
    installer.chmod(0o755)
    calls = []

    def runner(command, **kwargs):
        calls.append((command, kwargs))
        return "process"

    handoff = update.handoff_to_installer(
        archive,
        installer,
        app,
        app_bundle=app,
        runner=runner,
        extra_args=("--quiet",),
    )

    assert handoff.process == "process"
    assert calls == [
        (
            [
                str(installer),
                "--install",
                str(archive.resolve()),
                "--target",
                str(app.resolve()),
                "--quiet",
            ],
            {"shell": False, "close_fds": True},
        )
    ]
    assert not (app / "Contents").exists()


def test_check_update_rejects_installer_inside_bundle(tmp_path):
    app = tmp_path / "Stet.app"
    archive = tmp_path / "Stet-update.zip"
    archive.write_bytes(b"archive")

    with pytest.raises(update.UnsafeUpdatePath):
        update.check_update(
            archive,
            app / "Contents" / "Helpers" / "installer",
            app,
            app_bundle=app,
        )
