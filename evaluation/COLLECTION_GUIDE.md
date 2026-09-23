# Inventory-v0 Image Collection Guide

## Goal

Collect a small benchmark of controlled shelf/bin inventory images.

## Initial catalog

Use five product types for the first ten-image loop.

You can replace the starter catalog, but freeze the catalog before evaluating model runs.

## Suggested first 10 images

1. 3 products, front-facing, bright light
2. 5 products, front-facing, bright light
3. mixed products, front-facing
4. same products at slight camera angle
5. same products from farther away
6. mild product overlap
7. one partially visible product
8. darker indoor lighting
9. several similar-looking packages
10. sparse shelf with large empty areas

## Naming

```text
inv_0001.jpg
inv_0002.jpg
...
```

Never reuse a sample ID for a different image.

## Sessions

Give each physical setup a session ID.

Example:

```text
session_001
session_002
```

When the dataset grows, keep sessions entirely within one split.

## Ground truth checklist

Before accepting an annotation:

- image dimensions are correct
- every counted product exists in catalog.json
- canonical product names are exact
- quantities match visible instances
- one bounding box exists for every counted visible instance
- bounding boxes use original image coordinates
- no box extends outside image bounds
- annotation `data` validates against schema.json


## Evaluator-enforced integrity

The evaluator now verifies these rules automatically. A dataset integrity failure stops evaluation rather than lowering a model score.

## Bounding-box matching

Ground-truth boxes are also used to evaluate returned evidence localization.

Initial localization threshold:

```text
IoU >= 0.50
```

Keep boxes tight around the visible product instance and do not intentionally include neighboring products.
