"""Dataset validation tests for the optional YOLO training script."""

import importlib.util
import tempfile
import unittest
from pathlib import Path

from train_model import DatasetValidationError, validate_dataset


@unittest.skipUnless(importlib.util.find_spec("yaml"), "PyYAML is part of the optional YOLO dependencies.")
class TrainModelDatasetValidationTests(unittest.TestCase):
    def setUp(self):
        self.temp_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_directory.cleanup)
        self.dataset_root = Path(self.temp_directory.name)
        for split in ("train", "val"):
            (self.dataset_root / "images" / split).mkdir(parents=True)
            (self.dataset_root / "labels" / split).mkdir(parents=True)
            (self.dataset_root / "images" / split / "road.jpg").write_bytes(b"test image")
            (self.dataset_root / "labels" / split / "road.txt").write_text(
                "0 0.5 0.5 0.25 0.25\n",
                encoding="utf-8",
            )

        self.yaml_path = self.dataset_root / "data.yaml"
        self.write_config()

    def write_config(self, train_labels="0 0.5 0.5 0.25 0.25\n"):
        (self.dataset_root / "labels" / "train" / "road.txt").write_text(
            train_labels,
            encoding="utf-8",
        )
        self.yaml_path.write_text(
            "\n".join(
                (
                    f'path: "{self.dataset_root.as_posix()}"',
                    "train: images/train",
                    "val: images/val",
                    "names:",
                    "  0: pothole",
                )
            ),
            encoding="utf-8",
        )

    def test_valid_dataset_returns_class_names_and_splits(self):
        _yaml_path, root, names, splits = validate_dataset(self.yaml_path)

        self.assertEqual(root, self.dataset_root.resolve())
        self.assertEqual(names, {0: "pothole"})
        self.assertEqual(len(splits["train"]), 1)
        self.assertEqual(len(splits["val"]), 1)

    def test_invalid_class_id_is_rejected_before_training(self):
        self.write_config("1 0.5 0.5 0.25 0.25\n")

        with self.assertRaisesRegex(DatasetValidationError, "valid IDs are 0 through 0"):
            validate_dataset(self.yaml_path)

    def test_missing_dataset_yaml_has_a_clear_error(self):
        missing_yaml = self.dataset_root / "missing.yaml"

        with self.assertRaisesRegex(DatasetValidationError, "does not exist"):
            validate_dataset(missing_yaml)


if __name__ == "__main__":
    unittest.main()
