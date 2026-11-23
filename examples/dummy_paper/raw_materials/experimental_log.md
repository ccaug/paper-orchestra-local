# Experimental log — TRACE (DUMMY DATA)

> NOTE: ALL numbers below are SYNTHETIC and were generated only to test the paper-writing pipeline.
> They do not come from real experiments.

## Setup
- Dataset: CIFAR-10 (50,000 training images, 10,000 test images), standard random-crop + horizontal-flip augmentation.
- Model: ResNet-18 (CIFAR variant), trained from scratch.
- Optimizer: SGD with momentum 0.9, weight decay 5e-4, batch size 128, initial learning rate 0.1 with cosine decay.
- Training length: 100 epochs. Each configuration is run with 5 random seeds (0-4); we report mean ± standard deviation of final test accuracy.
- TRACE settings: window size w = 5 epochs, linear pacing from 40% of the data (epoch 1) to 100% of the data (epoch 60), then full data.
- Hardware: 1 consumer GPU (24 GB), about 0.9 GPU-hours per run.

## Table 1 — Main results, clean labels (test accuracy %, 5 seeds)
| Method | Test accuracy (%) | Epochs to reach 90% |
|---|---|---|
| Random order (standard training) | 92.4 ± 0.2 | 48 |
| Self-paced learning (loss threshold) | 92.6 ± 0.3 | 46 |
| Forgetting-score curriculum | 92.9 ± 0.2 | 44 |
| EL2N-score curriculum | 92.8 ± 0.3 | 45 |
| TRACE (ours, w = 5) | 93.5 ± 0.2 | 39 |

Paired t-test over seeds, TRACE vs. random order: p = 0.004. TRACE vs. best baseline (forgetting): p = 0.012.

## Table 2 — Ablation on window size w (test accuracy %, 5 seeds)
| Window w | Test accuracy (%) |
|---|---|
| 1 (current loss only) | 92.9 ± 0.3 |
| 3 | 93.2 ± 0.2 |
| 5 | 93.5 ± 0.2 |
| 10 | 93.3 ± 0.3 |

## Table 3 — Pacing function (w = 5)
| Pacing | Test accuracy (%) |
|---|---|
| Linear (default) | 93.5 ± 0.2 |
| Exponential | 93.1 ± 0.3 |
| Step | 92.8 ± 0.3 |

## Table 4 — Robustness to label noise (20% symmetric noise, test accuracy %, 5 seeds)
| Method | Test accuracy (%) |
|---|---|
| Random order | 85.1 ± 0.5 |
| Self-paced learning | 86.0 ± 0.6 |
| TRACE (ours, w = 5) | 87.9 ± 0.4 |

## Observations (raw, not interpretation)
- TRACE reaches 90% test accuracy in 39 epochs versus 48 for random order (9 epochs earlier, about 19% fewer epochs).
- Using only the current loss (w = 1) gives most of the gain of the best baselines but not the full gain of TRACE.
- Window size w = 10 is slightly worse than w = 5; the difference (0.2 points) is within one standard deviation.
- Overhead: computing TRACE scores adds 1.8% to wall-clock training time because per-example losses are already available.
- Under 20% label noise the gap between TRACE and random order grows to 2.8 points.
- All results are on CIFAR-10 only; no experiments on larger datasets were run.

## Figures available
- fig_convergence.png: test accuracy versus epoch for random order and TRACE (mean over 5 seeds).
- fig_ablation.png: test accuracy versus window size w.
