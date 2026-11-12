# ROADGUARD road-damage model

There are no trained weights or dataset files in this repository. Do not copy
a generic COCO YOLO checkpoint to `road_damage.pt` and treat it as a pothole
detector. The training script uses a detection checkpoint only to initialize
training on your labeled road-damage data.

## Obtain a dataset and check its terms

RDD2022 is one possible source. Start with the
[Road Damage Detector project](https://github.com/sekilab/RoadDamageDetector) and its
[CRDDC'2022 data page](https://crddc2022.sekilab.global/data/). Follow the
dataset provider's current access instructions. Before downloading or using
images, read the specific dataset license/terms for attribution, redistribution,
commercial use, and any restrictions. Do not assume that a research download is
automatically licensed for every use.

You can also use another road-damage dataset or your own images, provided you
have permission to use them. Include varied roads, weather, lighting, and
camera angles. Remove or protect personal information in images where
appropriate. Keep training, validation, and test splits separated by road or
recording session where possible so nearly identical frames do not leak across
splits.

## Prepare YOLO detection labels

For each image, create a matching text file containing one line per labeled
object:

```text
class_id x_center y_center width height
```

Coordinates must be normalized between `0` and `1`; class IDs start at zero.
An image with no hazards may have an empty label file. Ensure each class ID maps
to the correct class name in `data.yaml`. For example, use `pothole` as a class
name if that ID represents potholes; map crack types to separate names if the
dataset labels support them.

Example folder layout:

```text
road-damage/
  images/
    train/
    val/
    test/
  labels/
    train/
    val/
    test/
  data.yaml
```

Example `data.yaml` (update the root and class mapping to match your data):

```yaml
path: D:/datasets/road-damage
train: images/train
val: images/val
test: images/test
names:
  0: pothole
  1: longitudinal_crack
  2: transverse_crack
  3: alligator_crack
```

`backend/train_model.py` checks that the YAML, train/validation images, matching
YOLO label files, class IDs, and normalized coordinates are usable before
training starts. It does not establish that the dataset labels are correct or
that a model is accurate.

## Train on Windows 11 with VS Code

Use a Python version supported by the installed PyTorch and Ultralytics versions.
If the current backend interpreter is unsupported, create a separate virtual
environment with a supported Python version; do not install incompatible
packages into the existing environment.

In the VS Code terminal:

```powershell
cd D:\New folder\ROADGAURD_\backend
python -m pip install -r requirements-yolo.txt
python train_model.py --data D:\datasets\road-damage\data.yaml --epochs 100 --imgsz 640
```

The script uses `yolo11n.pt` as an initial object-detection checkpoint by
default. That checkpoint is not treated as road-damage detection proof;
Ultralytics fine-tunes it on the dataset labels. To use a different starting
checkpoint, pass `--base-model`. The best trained weights are copied to
`backend/models/road_damage.pt`. Training refuses to replace an existing model
unless `--overwrite` is passed deliberately. If a `test` split is configured,
the script evaluates it after training and prints the library's metrics.

Ultralytics/PyTorch may need to download the starting checkpoint the first time.
Training can take a long time and may require a CUDA-compatible GPU for a
reasonable run time. A successful script exit is not, by itself, evidence of
good detection quality.

## Validate before deployment

Review the validation and (if provided) test metrics, especially per-class
precision, recall, and mAP. Inspect false positives and missed hazards against
images from roads not used in training. Choose confidence thresholds based on
that evaluation. Confidence is not proof that a road hazard exists.

The Flask API loads only the configured checkpoint at `ROADGUARD_YOLO_MODEL`
(default `backend/models/road_damage.pt`). Configure thresholds in
`backend/.env`:

```text
AI_DETECTION_CONFIDENCE=0.10
AI_DETECTION_REVIEW_THRESHOLD=0.35
```

Detections below the review threshold are marked `Low Confidence`; stronger
ones are marked `Pending Admin Review`. Both remain unconfirmed until a human
administrator verifies the associated report. A model result never approves a
citizen report or creates a verified driver hazard by itself. Without weights
or the optional Ultralytics package, uploads are still validated and stored,
and the API reports `Model Unavailable` instead of fabricating detections.

Apply `database/ai_detection_migration.sql` on a fresh install. For an existing
AI-detection table, also apply the rerunnable
`database/ai_detection_inference_migration.sql` before using report linking and
the new detection status.
