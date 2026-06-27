# Wattbike data notes

## Timestamp oddity

The API metadata and the downloaded source files can disagree about start
timestamps during British Summer Time.

The `metadata.json` and `sessions.json` `startDate.iso` values appear to be the
correct UTC instants. For example, a ride that started at `10:28:49 BST` should
be represented as `09:28:49Z` in UTC:

- `metadata.json`: `2026-06-17T09:28:49.649Z`
- Europe/London local time: `2026-06-17 10:28:49 BST`

The manual `WB-HUB-DI-G-*.json` filenames, downloaded `.wbs` `startDate`, and
downloaded `.tcx` activity timestamps can use the London wall-clock time but
still append `Z`:

- `WB-HUB-DI-G-2026-06-17T10_28_49Z.json`
- `.wbs` `startDate`: `2026-06-17T10:28:49Z`
- `.tcx` `<Id>`: `2026-06-17T10:28:49Z`
- `.tcx` first trackpoint: `2026-06-17T10:28:50Z`

That is not valid UTC for this ride. It is local BST mislabeled as UTC.

This pattern was consistent across the checked sample sessions:

| API start UTC              | London local              | Source-file/manual timestamp labeled `Z` |
| ---                        | ---                       | ---                                      |
| `2026-05-28T08:59:00.509Z` | `2026-05-28 09:59:00 BST` | `2026-05-28T09:59:00Z`                   |
| `2026-06-01T09:14:09.376Z` | `2026-06-01 10:14:09 BST` | `2026-06-01T10:14:09Z`                   |
| `2026-06-03T09:22:41.165Z` | `2026-06-03 10:22:41 BST` | `2026-06-03T10:22:41Z`                   |
| `2026-06-08T09:22:24.468Z` | `2026-06-08 10:22:24 BST` | `2026-06-08T10:22:24Z`                   |
| `2026-06-10T07:45:58.737Z` | `2026-06-10 08:45:58 BST` | `2026-06-10T08:45:58Z`                   |
| `2026-06-16T09:46:34.568Z` | `2026-06-16 10:46:34 BST` | `2026-06-16T10:46:34Z`                   |
| `2026-06-17T09:28:49.649Z` | `2026-06-17 10:28:49 BST` | `2026-06-17T10:28:49Z`                   |

Other timestamp fields support the API value as the real UTC start time:

- `createdAt`, `updatedAt`, and `sessionSummary.createdAt` are API/Parse
  timestamps and look like real UTC instants. They are save/processing times
  around the end of the ride, not alternative start times.
- `processingTime` is also a real UTC timestamp, stored as Unix milliseconds.
  It decodes to a time a few seconds after `createdAt`, so it appears to be an
  internal processing/save completion timestamp.
- `createdAt` occurs shortly after `startDate.iso + sessionSummary.time`.
  For example, a ride starting at `09:28:49Z` with a duration of about
  40 minutes lines up with `10:09:21Z` creation/update timestamps and a
  nearby `processingTime`.
- `profile.json` and `profile-objects.json` timestamps are account/object
  metadata from the API and are unrelated to individual ride starts.

The likely provenance is:

- `sessions.json` and `metadata.json` are from the Wattbike API
  `RideSession.startDate.iso` field, stored as UTC.
- Session directory names are produced by this exporter from
  `RideSession.startDate.iso`, so they use the correct UTC instant.
- `.wbs`, `.tcx`, `.fit`, and `.wbsr` are downloaded Wattbike source files;
  this exporter does not rewrite their timestamps.
- Manual `WB-HUB-DI-G-*.json` exports contain the same relative revolution data
  as `.wbs`; their only absolute timestamp is in the filename, and
  that filename follows the same mislabeled-local-time convention as `.wbs` and
  `.tcx`.

Conclusion: prefer `metadata.json` / `sessions.json` `startDate.iso` as the
canonical ride start instant. Treat `.wbs`, `.tcx`, and `WB-HUB-DI-G-*` filename
timestamps as Europe/London local wall-clock timestamps with an incorrect `Z`
suffix for checked BST rides.

## Bike summary fields

The bike displays a post-session summary with fields that mostly map to
`metadata.json` `sessionSummary` values. This was checked against local exports
in `/data/exports/wattbike`.

| Bike field    | Export status     | Export field / derivation                                      |
| ---           | ---               | ---                                                            |
| Duration      | present           | `metadata.json` `sessionSummary.time`                          |
| Power peak    | present           | `metadata.json` `sessionSummary.powerMax`                      |
| Power avg     | present           | `metadata.json` `sessionSummary.powerAvg`                      |
| Power/mass    | derived           | `sessionSummary.powerAvg / userPerformanceState.weight`        |
| Energy        | present           | `metadata.json` `sessionSummary.energy`                        |
| Cadence avg   | present           | `metadata.json` `sessionSummary.cadenceAvg`                    |
| Cadence peak  | present           | `metadata.json` `sessionSummary.cadenceMax`                    |
| Rev count     | present           | `metadata.json` `sessionSummary.revolutionsCount`              |
| Speed avg     | present           | `metadata.json` `sessionSummary.speedAvg`                      |
| Distance      | present           | `metadata.json` `sessionSummary.distance`                      |
| Force L/R (%) | partially present | `sessionSummary.balanceAvg`; per-revolution `balance` exists   |
| Fmax angle    | present           | `anglePeakForceLeftAvg` and `anglePeakForceRightAvg`           |
| HR avg        | not found         | no session HR average field found in JSON exports              |
| HR peak       | not found         | `userPerformanceState.mhr` is profile max HR, not session peak |
| Pace avg      | derived           | derive from `sessionSummary.speedAvg`                          |
| Device Id     | present           | `wattbikeDevice.serialNumber`; `.wbs` top-level `serialNumber` |
| Sensor id     | not found         | was not found in JSON or FIT string data                       |

