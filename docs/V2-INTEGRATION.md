# FeedForge v2 development integration

This branch integrates the official v2.0.1 release at
`0c58ac13bd5e1d84323c2a9e71969db97d87d676` into the preserved all-features
baseline `808d5bb876a69c6c4effaf07befd38e19c5d03a1`.
The original baseline and its portable test build remain available separately.

## Approved behavior

- Retain v2's desktop presentation, editor, lyrics, drum support, audio recovery,
  media previews, inspection fixes and compatible conversion improvements.
- Find Songs keeps the existing CustomsForge and source-verifying Songsterr
  importers. The separate Songsterr editor retains upstream's converter and
  manual recording/timing tools. Its validated exports do not inherit the
  automated importer's independent source-verification status.
- Generated practice difficulty is an explicit setting, off by default. It
  retains the complete notes while deriving easier phrase levels; generated
  levels are not described as authored source data.
- Stem splitting uses the saved preference and checks readiness. A confirmation
  is needed only when there is a decision to make.
- Preserve global output folder, naming and layout settings, Unicode names,
  validation policy, seven-string conversion settings and the existing worker
  and memory controls. Do not add provider tags to output filenames.
- Retain validation before publication and recovery of previous output when a
  replacement fails. The two Songsterr workflows have separate commands/jobs
  and share bounded converter admission and coordinated shutdown.
- Retain library review filters, pagination and duplicate criteria. Existing
  broad versus strict matching remains an explicit user choice.

## Packaging and maintenance

The Node test command includes both development lines' tests; Python tests are
also available through `npm run test:python`. The frozen converter includes both
CLI entry points, native audio tools, NumPy, imageio-ffmpeg, yt-dlp and its retained
plugins. TypeScript remains a build-verification dependency.

External installed Node dependencies may be reused when the entire locked
dependency graph is identical. Only the application's release version and root
license metadata may differ; source and dependency lock hashes are both recorded.
This does not install or update dependencies in another checkout.

Upstream cleanup utilities are retained for deliberate maintenance, but a build
does not automatically remove older release artifacts. Development packaging
uses an external owned folder and the distinct FeedForge Song Browser Test
application identity; it does not update the normal application.

See [the preserved feature inventory](ALL-FEATURES-INTEGRATION.md) for the
pre-v2 feature branches. That document describes the historical baseline; this
document describes the v2 integration decisions.
