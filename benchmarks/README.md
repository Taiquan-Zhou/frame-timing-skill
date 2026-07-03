# External Benchmark Protocol

This directory defines the public result format for release smoke checks. Raw frames, private paths, and locally
generated benchmark results are not committed.

## Required Coverage

A release review set must contain independently reviewed examples of:

- slow translation;
- handheld jitter;
- rapid intentional turn;
- low texture;
- blur burst;
- parallax, including lateral or forward camera motion;
- independent foreground motion.

Each case records correct detections, false positives, false negatives, review requests, and reconstruction coverage
risk. A pending human review cannot pass the release gate. The collected cases are a smoke and regression set, not a statistical accuracy claim and not evidence of zero errors on unknown videos.

## Run One Case

```powershell
frame-timing-benchmark `
  --frames $env:FRAME_TIMING_BENCHMARK_FRAMES `
  --output-root output/benchmark `
  --case-id test3-cut2 `
  --device-category unspecified `
  --motion-type mixed_continuous_motion `
  --depth-structure unspecified `
  --lighting unspecified `
  --expected-active-range 0:136 `
  --expected-active-range 424:579 `
  --human-conclusion pending
```

The command analyzes the source once, then writes independent `strategy.json` and `validation.json` artifacts for
`coverage_first`, `balanced`, and `jitter_reduction`. It never applies a strategy or copies source images.

## Release Gate

A case fails automatically when an expected active range overlaps a `static` analysis range, any policy validation
fails, a deletion reason is not high-confidence jitter or quality-with-substitute, or a high-risk policy does not require human confirmation. It remains pending until human review is complete.
All machine-readable `*_removed` reasons are retained in the result; unknown deletion reasons fail closed instead of
being filtered from the audit record. Human-confirmation decisions use the same policy as the generated human review.
Passing individual cases does not authorize release until all seven required categories are represented and reviewed.
