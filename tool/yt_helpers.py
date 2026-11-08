# -*- coding: utf-8 -*-
"""Helpers used by publish_yt.py (Wee Reels Publisher, YouTube part).

The private production copy shares these helpers with the owner's Instagram / Facebook tools;
this file contains only the parts the YouTube uploader needs, so the tool runs on its own.
"""
import glob, json, os, re, shutil, subprocess, time

GCP_PROJECT = "wee-clinic-line"          # the owner's own Google Cloud project (Secret Manager)
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
FORBIDDEN = ["最", "第一", "唯一", "保證", "無痛", "根治"]   # wording the owner never publishes (Taiwan medical advertising law)


def gcloud(*args, input_text=None, retries=0):
    """Run the gcloud CLI (used only for Secret Manager: store/read the OAuth client and refresh token)."""
    exe = shutil.which("gcloud") or shutil.which("gcloud.cmd")
    if not exe:
        raise SystemExit("gcloud not found")
    extra = {} if input_text is not None else {"stdin": subprocess.DEVNULL}
    for i in range(retries + 1):
        r = subprocess.run([exe] + list(args), capture_output=True, text=True, encoding="utf-8",
                           errors="replace", input=input_text, creationflags=NO_WINDOW, **extra)
        if r.returncode == 0:
            return r.stdout.strip()
        if i < retries:
            time.sleep(20)
    raise SystemExit("gcloud failed: %s\n%s" % (" ".join(args[:3]), r.stderr.strip()[-800:]))


def find_files(proj):
    """Each video folder has 06_final/ with exactly one mp4, cover.jpg and 發文文案.md (post copy)."""
    fin = os.path.join(proj, "06_final")
    vids = glob.glob(os.path.join(fin, "*.mp4"))
    if len(vids) != 1:
        raise SystemExit("06_final must contain exactly one mp4: %s" % [os.path.basename(v) for v in vids])
    cover, copy = os.path.join(fin, "cover.jpg"), os.path.join(fin, "發文文案.md")
    for p in (cover, copy):
        if not os.path.exists(p):
            raise SystemExit("missing %s" % p)
    return vids[0], cover, copy


def probe(video):
    ff = shutil.which("ffprobe")
    if not ff:
        raise SystemExit("ffprobe not found")
    r = subprocess.run([ff, "-v", "error", "-show_entries",
                        "format=duration,size:stream=codec_type,codec_name,width,height,r_frame_rate",
                        "-of", "json", video], capture_output=True, text=True, encoding="utf-8")
    return json.loads(r.stdout)


def no_ai(proj):
    """reel.json "no_ai_visuals": true = no AI-generated imagery -> synthetic-content disclosure off."""
    p = os.path.join(proj, "workflows", "reel.json")
    return os.path.exists(p) and bool(json.load(open(p, encoding="utf-8")).get("no_ai_visuals"))


def caption_checks(proj, caption):
    """Wording check before publishing; returns a list of problems."""
    return ["contains forbidden word「%s」" % w for w in FORBIDDEN if w in caption]


def norm(text):
    return re.sub(r"[\s​-‍️]+", "", text or "")


def log_prefix(msg):
    return "skipped: " if "紀錄已補上" in msg else "failed: "
