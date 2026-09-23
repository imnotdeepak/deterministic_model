# MVTec D2S Import Notes

## Source directories

The downloaded dataset is rooted at `data/`:

```text
data/
├── images/       # 31,000 JPEG files
└── annotations/  # 17 COCO JSON files
```

The importer treats everything below `data/` as immutable.

## Annotation format

The dataset uses COCO JSON. Public training and validation annotations contain:

- image records with ID, filename, width, and height
- instance records with image ID, category ID, COCO `[x, y, width, height]` boxes, area, and compressed RLE segmentation
- 60 category records

The importer uses the supplied COCO bounding boxes. It preserves source annotation and category IDs in generated annotations. It does not decode or modify the compressed RLE masks.

## Source files used

```text
annotations/D2S_training.json    # 4,380 annotated images
annotations/D2S_validation.json  # 3,600 annotated images
```

Excluded:

- `D2S_test_info*.json`, because public test annotations are withheld
- `D2S_augmented.json`, because this first benchmark targets original captured images
- training/validation subset JSON files, because they duplicate records in the full split files

## Class mapping

The source files define 60 categories. `catalog.json` preserves each D2S category name exactly. Stable catalog IDs use `d2s_<zero-padded-source-category-id>`. Generated annotations retain `source_category_id`.

## Scene and split information

Each physical scene contains 30 captures: ten rotations under three lighting conditions. The source image ID encodes:

```text
scene ID      = image_id // 100
rotation      = image_id % 10
lighting      = (image_id % 100) // 10
```

All inspected training and validation scenes contain exactly 30 images and use capture suffixes `00` through `29`.

The importer performs a deterministic 70/15/15 benchmark split while keeping every selected image from the same D2S scene in one split. The original D2S split remains available as `source_split` in the manifest.

## Conversion assumptions

- Source image orientation and dimensions are already canonical JPEG pixel coordinates.
- COCO boxes use top-left `[x, y, width, height]` coordinates with exclusive right and bottom edges.
- Every public training/validation annotation with `iscrowd = 0` represents one counted visible product instance.
- D2S category names are canonical and are not title-cased or otherwise rewritten.
- Malformed images, unknown categories, crowd records, dimension mismatches, and invalid boxes are reported and skipped during deterministic selection.

## License

The downloaded metadata declares **CC BY-NC-SA 4.0**. The dataset is restricted to non-commercial use and should not be treated as production customer data or a commercially redistributable benchmark without separate permission from MVTec.
