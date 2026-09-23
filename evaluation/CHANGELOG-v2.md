# Evaluation Starter v2

- Missing predictions now count as failures.
- Coverage statistics added.
- Catalog validation added.
- Duplicate product entries rejected.
- Annotation integrity validation added.
- Pillow image-dimension validation added.
- Evidence contract validation added.
- IoU-based localization scoring added.
- Reference coverage and localization correctness separated.
- Exact pair accuracy now uses expected/predicted union.
- Count error metrics renamed and clarified.
- Canonical API evidence example added.
- Reproducibility metadata and hashes added.

- Invalid evidence bounding boxes are now excluded from localization scoring while remaining in validation errors.
