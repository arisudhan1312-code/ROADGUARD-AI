"""Validate a YOLO road-damage dataset and fine-tune an object detector."""

import argparse
import shutil
import sys
from datetime import datetime
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parent
DEFAULT_OUTPUT = BACKEND_DIR / "models" / "road_damage.pt"
IMAGE_SUFFIXES = {".bmp", ".jpeg", ".jpg", ".png", ".tif", ".tiff", ".webp"}


class DatasetValidationError(ValueError):
    """Raised when a YOLO dataset configuration or labels are invalid."""


def _load_yaml(yaml_path):
    try:
        import yaml
    except ImportError as error:
        raise RuntimeError(
            "PyYAML is required through Ultralytics. Install backend/requirements-yolo.txt."
        ) from error

    try:
        with yaml_path.open("r", encoding="utf-8") as yaml_file:
            data = yaml.safe_load(yaml_file)
    except OSError as error:
        raise DatasetValidationError(f"Could not read dataset YAML: {yaml_path}") from error
    except yaml.YAMLError as error:
        raise DatasetValidationError(f"Dataset YAML is invalid: {error}") from error

    if not isinstance(data, dict):
        raise DatasetValidationError("Dataset YAML must contain a mapping of settings.")
    return data


def _normalize_names(names):
    if isinstance(names, list):
        normalized = {index: name for index, name in enumerate(names)}
    elif isinstance(names, dict):
        try:
            normalized = {int(class_id): name for class_id, name in names.items()}
        except (TypeError, ValueError) as error:
            raise DatasetValidationError("Class IDs in names must be integers starting at 0.") from error
    else:
        raise DatasetValidationError("Dataset YAML must include a names list or mapping.")

    if not normalized or sorted(normalized) != list(range(len(normalized))):
        raise DatasetValidationError("Class IDs in names must be contiguous and start at 0.")
    if any(not isinstance(name, str) or not name.strip() for name in normalized.values()):
        raise DatasetValidationError("Every class must have a non-empty name.")
    return normalized


def _resolve_path(value, root):
    path = Path(value).expanduser()
    return path if path.is_absolute() else root / path


def _split_images(split_value, root):
    values = split_value if isinstance(split_value, list) else [split_value]
    images = []
    for value in values:
        if not isinstance(value, str) or not value.strip():
            raise DatasetValidationError("Each train/val path must be a non-empty string.")
        split_path = _resolve_path(value.strip(), root)
        if split_path.suffix.lower() == ".txt":
            try:
                entries = [
                    line.strip()
                    for line in split_path.read_text(encoding="utf-8").splitlines()
                    if line.strip()
                ]
            except OSError as error:
                raise DatasetValidationError(f"Could not read image list: {split_path}") from error
            images.extend(_resolve_path(entry, root) for entry in entries)
        elif split_path.is_dir():
            images.extend(
                image_path
                for image_path in split_path.rglob("*")
                if image_path.is_file() and image_path.suffix.lower() in IMAGE_SUFFIXES
            )
        elif split_path.is_file() and split_path.suffix.lower() in IMAGE_SUFFIXES:
            images.append(split_path)
        else:
            raise DatasetValidationError(f"Image path does not exist or is unsupported: {split_path}")

    if not images:
        raise DatasetValidationError("The configured split contains no supported images.")
    missing = [image for image in images if not image.is_file()]
    if missing:
        raise DatasetValidationError(f"Dataset image does not exist: {missing[0]}")
    return images


def _label_path(image_path, root):
    parts = list(image_path.parts)
    image_folder_indexes = [
        index for index, part in enumerate(parts) if part.lower() == "images"
    ]
    if image_folder_indexes:
        parts[image_folder_indexes[-1]] = "labels"
        return Path(*parts).with_suffix(".txt")

    try:
        relative_image = image_path.relative_to(root)
    except ValueError:
        raise DatasetValidationError(
            f"Images must be under an 'images' directory or within dataset root: {image_path}"
        )
    return (root / "labels" / relative_image).with_suffix(".txt")


def _validate_labels(images, root, class_count, split_name):
    labeled_image_count = 0
    for image_path in images:
        label_path = _label_path(image_path, root)
        if not label_path.is_file():
            continue
        labeled_image_count += 1
        try:
            lines = label_path.read_text(encoding="utf-8").splitlines()
        except OSError as error:
            raise DatasetValidationError(f"Could not read label file: {label_path}") from error

        for line_number, line in enumerate(lines, start=1):
            if not line.strip():
                continue
            values = line.split()
            if len(values) != 5:
                raise DatasetValidationError(
                    f"{label_path}:{line_number} must contain "
                    "'class_id x_center y_center width height'."
                )
            try:
                class_id = int(values[0])
                x_center, y_center, width, height = (float(value) for value in values[1:])
            except ValueError as error:
                raise DatasetValidationError(
                    f"{label_path}:{line_number} contains a non-numeric label value."
                ) from error

            if not 0 <= class_id < class_count:
                raise DatasetValidationError(
                    f"{label_path}:{line_number} uses class {class_id}; "
                    f"valid IDs are 0 through {class_count - 1}."
                )
            if not all(0 <= value <= 1 for value in (x_center, y_center, width, height)):
                raise DatasetValidationError(
                    f"{label_path}:{line_number} coordinates must be normalized to 0..1."
                )
            if width == 0 or height == 0:
                raise DatasetValidationError(
                    f"{label_path}:{line_number} box width and height must be greater than 0."
                )
            if (
                x_center - width / 2 < 0
                or x_center + width / 2 > 1
                or y_center - height / 2 < 0
                or y_center + height / 2 > 1
            ):
                raise DatasetValidationError(
                    f"{label_path}:{line_number} box extends outside the image."
                )

    if labeled_image_count == 0:
        raise DatasetValidationError(
            f"The {split_name} split has no matching YOLO label files. "
            "Expected one .txt file per image under a parallel labels/ directory."
        )


def validate_dataset(yaml_path):
    yaml_path = Path(yaml_path).expanduser().resolve()
    if not yaml_path.is_file():
        raise DatasetValidationError(f"Dataset YAML does not exist: {yaml_path}")

    data = _load_yaml(yaml_path)
    dataset_root_value = data.get("path", ".")
    if not isinstance(dataset_root_value, str):
        raise DatasetValidationError("The YAML 'path' setting must be a string.")
    dataset_root = _resolve_path(dataset_root_value, yaml_path.parent).resolve()
    names = _normalize_names(data.get("names"))

    splits = {}
    for split_name in ("train", "val"):
        if split_name not in data:
            raise DatasetValidationError(f"Dataset YAML is missing the required '{split_name}' split.")
        images = _split_images(data[split_name], dataset_root)
        _validate_labels(images, dataset_root, len(names), split_name)
        splits[split_name] = images

    if "test" in data and data["test"]:
        splits["test"] = _split_images(data["test"], dataset_root)
        _validate_labels(splits["test"], dataset_root, len(names), "test")

    return yaml_path, dataset_root, names, splits


def _positive_int(value):
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return number


def _non_negative_int(value):
    number = int(value)
    if number < 0:
        raise argparse.ArgumentTypeError("must be zero or greater")
    return number


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Validate and fine-tune a YOLO object detector on road-damage labels."
    )
    parser.add_argument("--data", required=True, help="Path to the dataset data.yaml file.")
    parser.add_argument(
        "--base-model",
        default="yolo11n.pt",
        help="Object-detection checkpoint used only as a training initialization.",
    )
    parser.add_argument("--epochs", type=_positive_int, default=100)
    parser.add_argument("--imgsz", type=_positive_int, default=640)
    parser.add_argument("--batch", type=_positive_int, default=16)
    parser.add_argument("--workers", type=_non_negative_int, default=0)
    parser.add_argument("--device", default=None, help="Optional Ultralytics device, e.g. 0 or cpu.")
    parser.add_argument(
        "--output",
        default=str(DEFAULT_OUTPUT),
        help=f"Destination for the best checkpoint (default: {DEFAULT_OUTPUT}).",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Allow replacing an existing destination checkpoint.",
    )
    return parser.parse_args(argv)


def train(args):
    yaml_path, _, names, splits = validate_dataset(args.data)
    output_path = Path(args.output).expanduser().resolve()
    if output_path.exists() and not args.overwrite:
        raise RuntimeError(
            f"Refusing to overwrite an existing model: {output_path}. "
            "Choose --output or pass --overwrite intentionally."
        )

    try:
        from ultralytics import YOLO
    except ImportError as error:
        raise RuntimeError(
            "Ultralytics is not installed. Run "
            "'python -m pip install -r requirements-yolo.txt' from backend."
        ) from error

    output_path.parent.mkdir(parents=True, exist_ok=True)
    run_name = f"road_damage_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    model = YOLO(args.base_model)
    training_options = {
        "data": str(yaml_path),
        "epochs": args.epochs,
        "imgsz": args.imgsz,
        "batch": args.batch,
        "workers": args.workers,
        "project": str(BACKEND_DIR / "runs" / "detect"),
        "name": run_name,
    }
    if args.device:
        training_options["device"] = args.device

    results = model.train(**training_options)
    run_directory = Path(results.save_dir)
    best_checkpoint = run_directory / "weights" / "best.pt"
    if not best_checkpoint.is_file():
        raise RuntimeError(
            f"Training completed without a best checkpoint at {best_checkpoint}. "
            "Review the Ultralytics output for training errors."
        )

    if "test" in splits:
        test_results = model.val(data=str(yaml_path), split="test", imgsz=args.imgsz)
        print(f"Test metrics: {test_results.results_dict}")

    shutil.copy2(best_checkpoint, output_path)
    print(f"Validated classes: {', '.join(names.values())}")
    print(f"Training run: {run_directory}")
    print(f"Best checkpoint copied to: {output_path}")
    print("Review validation metrics and false positives before using this model in production.")


def main(argv=None):
    args = parse_args(argv)
    try:
        train(args)
    except (DatasetValidationError, RuntimeError) as error:
        print(f"Training stopped: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
