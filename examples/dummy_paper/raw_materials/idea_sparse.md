# Idea: TRACE — Trajectory-Aware Curriculum from Loss Evolution

> NOTE: this is a DUMMY research idea invented only to test the paper-writing pipeline.

## Problem
Curriculum learning orders training examples from easy to hard, but most difficulty scores are computed from a
single snapshot of a model (one loss value, one confidence). A snapshot is noisy early in training and can
mislabel examples that are only temporarily hard.

## Core idea
Score each training example by how its loss EVOLVES over a sliding window of the last w epochs instead of its
current loss. We call the score TRACE (Trajectory-Aware Curriculum Estimate). An example whose loss is high but
steadily decreasing is treated as "learnable soon" and is scheduled earlier than one whose loss is high and flat
or oscillating. The score combines (a) the mean loss over the window and (b) the negative slope of a linear fit to
the loss trajectory. A pacing function gradually increases the fraction of the data that is eligible for training.

## Claimed contributions
1. A trajectory-based difficulty score that is cheap (it reuses losses already computed during training).
2. A pacing schedule that needs no extra hyper-parameter beyond the window size w.
3. An empirical study on image classification showing faster convergence and better final accuracy, and
   robustness to label noise.

## Scope and honest limitations
Only small-scale image classification (CIFAR-10, ResNet-18) has been tried. No theory. Larger datasets,
language tasks and other architectures are future work.
