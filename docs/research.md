# Wattbike export research

Research checked on 18 June 2026.

## Supported session export

Wattbike officially supports exporting an individual session as a TCX file:

1. Sign in at [Wattbike Hub](https://hub.wattbike.com/).
2. Open **Sessions**.
3. Select a session.
4. Select **Download .TCX file**.

TCX is widely supported by Strava, TrainingPeaks, GoldenCheetah, and similar
tools. Wattbike's documentation says that the Hub does not currently provide
a bulk-download facility.

Wattbike can also automatically send future sessions to Strava,
TrainingPeaks, and Apple Health after linking the service under
**Settings > Profile**. Older sessions must be downloaded and imported
manually.

Official documentation:

- [How to export session data](https://support.wattbike.com/en-GB/how-to-export-session-data-2477551)
- [How to upload to Strava/Training Peaks](https://support.wattbike.com/en-GB/how-to-upload-to-strava-training-peaks-2477321)
- [Connecting to Strava, Training Peaks & Apple Health](https://support.wattbike.com/en-GB/connecting-to-strava-training-peaks-apple-health-2477236)

## Full personal-data request

The session export does not cover profile information, orders, support
records, connected services, or other account data.

Wattbike's privacy policy provides rights to:

- request a copy of personal data;
- receive applicable data in a structured, commonly used, machine-readable
  format; and
- have applicable data transferred to another party.

Send a data subject access and portability request to `dpo@wattbike.com`.
A suitable request is:

> Please provide a copy of all personal data associated with my Wattbike and
> Wattbike Hub accounts, including all session and activity data, in a
> structured, commonly used, machine-readable format.

See the [Wattbike privacy policy](https://wattbike.com/policies/privacy-policy),
last updated 28 May 2026.

## Bulk export through the Hub API

Wattbike does not publish a supported API, but the Hub uses an undocumented
API at `https://api.wattbike.com/v2`. The current web flow:

1. signs in through the Cognito hosted UI at `auth.wattbike.com`;
2. exchanges the returned Cognito ID token at `/v2/custom/session`;
3. uses the resulting Parse session token to query the profile and
   `RideSession` records; and
4. downloads the files named by each record's `sessionData`.

This flow and the account export were verified on 18 June 2026. Public sample
`.tcx` and `.wbs` files also remained available. This is useful evidence, not
a stability guarantee.

A complete archive should retain:

- the profile and referenced account objects;
- every `RideSession` metadata record as JSON;
- the generated `.tcx` file;
- raw `.wbs` session data;
- `.wbsr` and any downloadable `.wbss` lap or segment files named in
  `sessionData`; and

TCX is the portable representation. The WBS-family files should also be kept
because they contain Wattbike-specific data that TCX may not represent.

The exporter should avoid normalizing or interpreting these files. Derived
views belong in an offline data access layer, following the design described
in [Building data liberation infrastructure](https://beepb00p.xyz/exports.html).

## Existing projects

### `AartGoossens/wblib`

Repository: [AartGoossens/wblib](https://github.com/AartGoossens/wblib)

This is the best technical reference. It contains the authenticated v2 login,
`RideSession` query, file URL construction, and models for session metadata.
It is MIT licensed.

Do not take it as a dependency unchanged:

- its last commit was in November 2017;
- it targets Python 3.6-era packages;
- its dependency pins are obsolete;
- it assumes ten-character session IDs in one path;
- it does not implement robust pagination or resumable downloads; and
- some model behavior assumes that queries always return at least one result.

Reuse the API behavior and fixtures, either by porting small MIT-licensed
sections with attribution or by independently implementing the same HTTP
requests.

### `93tilinfinity/wattbike-analysis`

Repository:
[93tilinfinity/wattbike-analysis](https://github.com/93tilinfinity/wattbike-analysis)

Its `download.py` is a useful end-to-end example of authenticated session
enumeration followed by WBS download. It is a script rather than a reusable
library, stores credentials in a Python file, catches every exception, writes
Pandas pickles instead of original files, and has no visible license.

Use it as behavioral evidence only. Do not copy its code without permission.

### `AartGoossens/wattbike-hub-exporter`

Repository:
[AartGoossens/wattbike-hub-exporter](https://github.com/AartGoossens/wattbike-hub-exporter)

This MIT-licensed project predates the authenticated v2 client. It converts
legacy session JSON into TCX and includes unfinished support for merging
sessions. Its last commit was in April 2016.

It is not needed for ordinary exports because Wattbike already generates a
TCX file for each current session. Its interpolation and merge logic could be
ported later if merging split sessions is a required feature.

### `AartGoossens/wattbikehublib`

Repository:
[AartGoossens/wattbikehublib](https://github.com/AartGoossens/wattbikehublib)

This is an older MIT-licensed client for public profile pages and the legacy
`ranking/getSessionRows` endpoint. Despite recent GitHub repository metadata,
its last code commit was in July 2016.

It should not be part of the new exporter. It is useful only for understanding
old session IDs or supporting data that predates the v2 API.
