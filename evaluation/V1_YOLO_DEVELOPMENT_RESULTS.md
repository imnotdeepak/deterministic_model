# YOLO development results

## Decision

Select the five-epoch `yolo11s` checkpoint with image size 960, IoU threshold
0.70, and confidence threshold 0.35. The checkpoint SHA-256 is
`30d76b9b5dbea84097f21de6991d6e31d4586f1713e92b35b4ada9253ad0b08e`.

The longer training run was rejected: its internal-validation mAP50-95 was
0.822 versus 0.860 for the five-epoch pilot, with several collapsed class
recalls.

## Development-only threshold selection

All 209 development images were evaluated. Validation and test were untouched.

| Confidence | Exact images | Product precision | Product recall | Localization accuracy | Count error/image |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 0.25 | 198/209 (94.74%) | 96.73% | 97.93% | 97.65% | 0.0813 |
| 0.35 | 201/209 (96.17%) | 97.13% | 97.93% | 98.21% | 0.0670 |
| 0.40 | 201/209 (96.17%) | 97.13% | 97.93% | 98.21% | 0.0670 |

Confidence 0.35 is the lower edge of the best observed plateau and is frozen
for validation. It removes three low-confidence duplicate or spurious
detections without reducing recall.

## Remaining errors

Eight development images remain non-exact at confidence 0.35. They are genuine
class confusions or omissions rather than output-contract failures. The common
confusions are closely related tea packages, beverage variants, visually
similar cereal/bar packaging, and beer-bottle identities. The runner produced
209/209 predictions with a 100% schema and prediction-contract valid rate.

The selected report is
`reports/v1-development-yolo-pilot5-conf035.json`; the 0.25 and 0.40 reports
are retained as threshold-selection evidence.
