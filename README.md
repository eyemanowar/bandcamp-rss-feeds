# Daily Bandcamp releases for eyemanowar

Feed: https://raw.githubusercontent.com/eyemanowar/bandcamp-rss-feeds/main/feed.xml

Add this URL as a subscription in your RSS reader. The workflow checks the public
Bandcamp profile https://bandcamp.com/eyemanowar and its followed artists and labels
daily at 06:17 UTC (07:17 Warsaw in winter; 08:17 in summer). GitHub may delay or drop scheduled runs. Fallback attempts at 07:29, 10:17 and 12:17 UTC retry missed or incomplete checks; they skip the scan after a complete check that day. Manual runs always scan.

The first successful catalogue check establishes a baseline. Existing music is not
announced as new. Newly followed artists and labels are baselined the same way.
Subsequent additions to a catalogue appear in one digest per UTC day. Releases are
deduplicated by URL across artists and labels. Preorders appear when added to the
catalogue; this does not send a second alert on their release date. Wishlist,
purchases, merchandise and artist messages are excluded.

The feed retains 180 nonempty daily digests. A welcome item confirms the initial
feed is ready. Public release-state.json stores catalogue baselines, seen URLs,
last check time and any failed catalogue URLs. No Bandcamp password is needed.
The repo, followed artist URLs and feed are public.

## Operations

Open Actions > Daily Bandcamp releases to inspect checks or select Run workflow.
Failed catalogue checks preserve their previous baseline and generate workflow
warnings. Healthy catalogues still update; failed catalogues retry on the next run.
If the profile or all catalogues fail, the run fails without overwriting state.
Bandcamp HTML/API changes or blocking may require maintenance.

Daily state commits keep the repository active. GitHub may nevertheless disable
scheduled workflows in public repositories after 60 days without activity; check
Actions if updates stop. Scheduled workflows in forks must be enabled explicitly.

This personal fork is based on https://github.com/JeffreyGaydos/bandcamp-rss-feeds.
The release-only workflow uses release_digest.py; original upstream scripts remain
for reference and are not scheduled. Do not merge upstream workflow files blindly.
