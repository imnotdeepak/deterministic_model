import importlib.util
import sys
from pathlib import Path

from PIL import Image


SRC = Path(__file__).parents[1] / "src"
sys.path.insert(0, str(SRC))
SPEC = importlib.util.spec_from_file_location(
    "train_crop_classifier", SRC / "train_crop_classifier.py"
)
train_crop_classifier = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = train_crop_classifier
SPEC.loader.exec_module(train_crop_classifier)


def test_crop_bounds_expands_and_clamps():
    assert train_crop_classifier.crop_bounds((0.1, 0.2, 0.2, 0.2), 100, 50, 0.1) == (
        0,
        4,
        22,
        16,
    )


def test_pad_square_centers_image():
    image = Image.new("RGB", (10, 4), color=(1, 2, 3))
    padded = train_crop_classifier.pad_square(image)
    assert padded.size == (10, 10)
    assert padded.getpixel((5, 5)) == (1, 2, 3)
    assert padded.getpixel((0, 0)) == (114, 114, 114)


def test_class_sample_weights_balance_total_weight():
    path = Path("image.jpg")
    records = [
        train_crop_classifier.CropRecord(path, 0, (0.5, 0.5, 0.2, 0.2)),
        train_crop_classifier.CropRecord(path, 0, (0.5, 0.5, 0.2, 0.2)),
        train_crop_classifier.CropRecord(path, 1, (0.5, 0.5, 0.2, 0.2)),
    ]
    weights = train_crop_classifier.class_sample_weights(records)
    assert weights == [0.5, 0.5, 1.0]
