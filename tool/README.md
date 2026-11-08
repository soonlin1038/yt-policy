# Wee Reels Publisher — YouTube uploader

Private single-user command-line tool. It uploads the channel owner's own educational Shorts to the owner's own
YouTube channel (林麻-冷知識研究室, UCf5qKwUWSPR6apNzF2oTnfQ). Not offered to anyone else.

- `publish_yt.py` — the uploader (YouTube Data API v3, OAuth 2.0 installed-app flow, scope `https://www.googleapis.com/auth/youtube`)
- `yt_helpers.py` — small helpers (gcloud Secret Manager wrapper, file lookup, ffprobe)

API calls used: `videos.insert`, `videos.update` (status.publishAt), `videos.delete` (own unpublished videos),
`channels.list` and `playlistItems.list` with `mine=true` (own uploads, duplicate check). No other users' data.

```
python publish_yt.py --setup-auth client_secret.json    # one-time OAuth consent by the channel owner
python publish_yt.py --whoami
python publish_yt.py <video folder> --dry
python publish_yt.py <video folder> --schedule="2026-11-20 18:00"
python publish_yt.py <video folder> --reschedule="2026-11-21 18:00"
python publish_yt.py <video folder> --unschedule        # deletes the not-yet-public upload
```
Requires `google-api-python-client`, `google-auth-oauthlib`, ffprobe and gcloud. Credentials are stored only in the
owner's Google Cloud Secret Manager. Privacy policy: https://soonlin1038.github.io/yt-policy/#privacy
