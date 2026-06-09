import os
import zipfile
import pytest


def test_latest_build_artifact_contents():
    dist_dir = os.path.join(os.path.dirname(__file__), "..", "..", "dist")
    zip_path = os.path.join(dist_dir, "stet_portable.zip")

    if not os.path.exists(zip_path):
        pytest.skip("stet_portable.zip not found in dist/")

    expected_files = {
        "Stet.exe",
        "config.json",
        "run.bat",
        "logo.png",
    }
    with zipfile.ZipFile(zip_path, "r") as z:
        namelist = z.namelist()

        # Get all top-level items in the zip file
        top_level_contents = set()
        for name in namelist:
            top_level = name.split("/")[0]
            top_level_contents.add(top_level)

        missing_files = expected_files - top_level_contents
        assert not missing_files, f"Missing files in the zip artifact: {missing_files}"

        # Check for llama folder at the top level
        has_llama_folder = any(item.startswith("llama") for item in top_level_contents)
        assert has_llama_folder, "Missing 'llama' folder/placeholder in the zip artifact"
