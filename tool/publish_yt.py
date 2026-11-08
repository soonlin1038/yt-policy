# -*- coding: utf-8 -*-
r"""把做好的 Reels 發到 YouTube(Shorts;YouTube Data API v3;順林醫師 2026-11-08「直接規劃 API 自動」)。

公開發文仍要順林醫師點頭:他說「發」才跑 --publish,他指定時間才跑 --schedule。
補發(--backfill)是 2026-11-08 醫師授權:「之前的說書片和知識片都補發」。

  py publish_yt.py --setup-auth <client_secret.json>   一次性:用 OAuth 桌面用戶端登入頻道擁有者帳號(醫師本人按同意),
                                                       用戶端與 refresh token 存進 Secret Manager(不印出來)
  py publish_yt.py --whoami                            授權的是哪個頻道
  py publish_yt.py <reel 資料夾> --dry                 本機檢查(規格、YouTube 版標題/說明、合規),不連網
  py publish_yt.py <reel 資料夾> --publish             上傳 → 馬上公開
  py publish_yt.py <reel 資料夾> --schedule="2026-11-20 18:00"   上傳成私人＋publishAt,YouTube 到點自己公開(電腦可以關)
  py publish_yt.py <reel 資料夾> --reschedule="2026-11-21 18:00" 已排程的改時間
  py publish_yt.py <reel 資料夾> --unschedule          還沒公開的直接刪掉(YouTube API 可以刪,FB 才不行)
  py publish_yt.py --backlog                           列出說書片／冷知識片(reel.json pet_only 且有 cast)在 YouTube 的狀態
  py publish_yt.py --backfill=N [--dry-run]            補發:其他平台已發、YouTube 還沒有的,最舊的 N 支馬上公開
  py publish_yt.py --sync-schedule [--dry-run]         其他平台已排程、YouTube 還沒有的,照 IG 的排程時間排 YouTube

用 Python310 跑(要 google-api-python-client、google-auth-oauthlib)。
設定(機密都不在本機檔案裡):Secret Manager `yt-oauth-client`(OAuth 桌面用戶端 JSON)、`yt-oauth-token`(授權後的 refresh token)。
文案:06_final/發文文案.md 的「## YouTube 版」;沒有這段就用 reel.json content.title 當標題、「## FB 版」當說明(補發舊片用)。
每支固定:類別 27 教育、非兒童內容、containsSyntheticMedia=true(阿哲等 AI 人物;reel.json no_ai_visuals 例外)。
狀態與紀錄:06_final/_yt_state.json、_yt_publish.log。上傳前查頻道已上傳影片,同標題就不重傳(--force 略過)。
🔴 API 專案通過 YouTube API 稽核前,API 上傳的影片會被 YouTube 鎖成私人 —— 腳本會偵測並寫進紀錄(locked_private)。
配額:每天預設 10,000 單位,上傳一支約 1,600 → 一天約 6 支。
"""
import datetime, glob, json, os, re, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import yt_helpers as IG           # gcloud, find_files, ffprobe, wording check
import yt_helpers as W

SECRET_CLIENT = "yt-oauth-client"
SECRET_TOKEN = "yt-oauth-token"
SCOPES = ["https://www.googleapis.com/auth/youtube"]   # 上傳、改、刪、列都要;youtube.upload 不能改刪
REELS_ROOTS = [r"C:\Users\soonlin\我的雲端硬碟\reels", r"G:\我的雲端硬碟\reels"]
SKIP_SLUGS = {"spanish-flu-1918"}                      # v1 不發(醫師 10/29 選 v2)
_PROJ = None


def log(proj, msg):
    line = "%s  %s" % (datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg)
    print(line)
    if proj:
        with open(os.path.join(proj, "06_final", "_yt_publish.log"), "a", encoding="utf-8") as f:
            f.write(line + "\n")


# ---------------- 授權 ----------------
def secret_put(name, value):
    try:
        IG.gcloud("secrets", "describe", name, "--project=" + IG.GCP_PROJECT)
    except SystemExit:
        IG.gcloud("secrets", "create", name, "--replication-policy=automatic", "--project=" + IG.GCP_PROJECT)
    IG.gcloud("secrets", "versions", "add", name, "--project=" + IG.GCP_PROJECT, "--data-file=-", input_text=value)


def secret_get(name):
    v = IG.gcloud("secrets", "versions", "access", "latest", "--secret=" + name, "--project=" + IG.GCP_PROJECT)
    return (v or "").lstrip("\ufeff").strip()


def setup_auth(client_path):
    from google_auth_oauthlib.flow import InstalledAppFlow
    cfg = json.load(open(client_path, encoding="utf-8"))
    if "installed" not in cfg:
        raise SystemExit("這不是「桌面應用程式」類型的 OAuth 用戶端 JSON(要有 installed 欄位)")
    flow = InstalledAppFlow.from_client_config(cfg, SCOPES)
    print("瀏覽器會開 Google 登入頁:請選頻道擁有者帳號(lin.soon@gmail.com)與頻道「林麻-冷知識研究室」,"
          "看到「Google 尚未驗證這個應用程式」按「進階 → 繼續」,再按允許。")
    creds = flow.run_local_server(port=0, open_browser=True, prompt="consent", access_type="offline")
    if not creds.refresh_token:
        raise SystemExit("沒有拿到 refresh token(要帶 prompt=consent;請到 Google 帳戶「第三方存取權」移除後重跑)")
    secret_put(SECRET_CLIENT, json.dumps(cfg))
    secret_put(SECRET_TOKEN, creds.to_json())
    yt = build_client(creds)
    cid, title = whoami(yt)
    print("已授權頻道「%s」(%s);用戶端與 refresh token 已存進 Secret Manager %s、%s"
          % (title, cid, SECRET_CLIENT, SECRET_TOKEN))
    print("可以刪掉本機的 %s 了(已存進 Secret Manager)" % os.path.basename(client_path))


def build_client(creds=None):
    from googleapiclient.discovery import build
    if creds is None:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        raw = secret_get(SECRET_TOKEN)
        if not raw:
            raise SystemExit("Secret Manager 的 %s 是空的:先跑 --setup-auth" % SECRET_TOKEN)
        creds = Credentials.from_authorized_user_info(json.loads(raw), SCOPES)
        if not creds.valid:
            try:
                creds.refresh(Request())
            except Exception as e:
                raise SystemExit("YouTube 授權失效(%s)。OAuth 同意畫面若還是「測試中」,refresh token 7 天就過期:"
                                 "改成「正式版」後重跑 --setup-auth" % str(e)[:200])
    return build("youtube", "v3", credentials=creds, cache_discovery=False)


def whoami(yt):
    r = yt.channels().list(part="id,snippet,contentDetails", mine=True).execute()
    items = r.get("items", [])
    if not items:
        raise SystemExit("這個帳號沒有 YouTube 頻道(或授權時沒選到品牌帳戶頻道)")
    c = items[0]
    return c["id"], c["snippet"]["title"]


def uploads_index(yt):
    """頻道全部上傳:{標題: (video_id, 隱私狀態)}。playlistItems 每頁 1 單位。"""
    ch = yt.channels().list(part="contentDetails", mine=True).execute()["items"][0]
    pl = ch["contentDetails"]["relatedPlaylists"]["uploads"]
    out, tok = {}, None
    while True:
        r = yt.playlistItems().list(part="snippet,status", playlistId=pl, maxResults=50, pageToken=tok).execute()
        for it in r.get("items", []):
            out[W.norm(it["snippet"]["title"])] = (it["snippet"]["resourceId"]["videoId"],
                                                   it.get("status", {}).get("privacyStatus"))
        tok = r.get("nextPageToken")
        if not tok:
            return out


# ---------------- 文案 ----------------
def clean(s):
    return s.replace("<", "〈").replace(">", "〉").strip()   # YouTube 標題/說明不收 < >


def reel_json(proj):
    p = os.path.join(proj, "workflows", "reel.json")
    return json.load(open(p, encoding="utf-8")) if os.path.exists(p) else {}


def yt_texts(proj, copy_path):
    text = open(copy_path, encoding="utf-8").read()
    m = re.search(r"^## YouTube 版[^\n]*\n(.*?)(?:\n---|\Z)", text, re.S | re.M)
    if m:
        body = m.group(1).strip()
        t = re.search(r"^標題[:：]\s*(.+)$", body, re.M)
        if not t:
            raise SystemExit("「## YouTube 版」要有一行「標題:…」")
        title = t.group(1)
        desc = re.sub(r"^標題[:：].*\n?", "", body, count=1, flags=re.M).strip()
    else:
        title = (reel_json(proj).get("content") or {}).get("title", "").strip()   # 醫師核過的標題,空白照原樣
        if not title:
            raise SystemExit("沒有「## YouTube 版」,reel.json 也沒有 content.title")
        fb = re.search(r"^## (?:FB 版|Facebook)[^\n]*\n(.*?)\n---", text, re.S | re.M)
        desc = fb.group(1).strip() if fb else ""
        desc = re.sub(r"\A傳給[^\n]*\n+", "", desc)      # 11/2 原則:不用「傳給…」開頭
    title, desc = clean(title), clean(desc)
    tags = [h.lstrip("#") for h in re.findall(r"#[^\s#]+", desc)][:15]
    return title, desc, tags


def local_checks(proj, video, title, desc):
    fails, notes = [], []
    info = IG.probe(video)
    fmt = info.get("format", {})
    dur = float(fmt.get("duration", 0))
    v = next((s for s in info["streams"] if s["codec_type"] == "video"), {})
    w, h = int(v.get("width", 0)), int(v.get("height", 0))
    if dur > 180:
        fails.append("影片 %.1f 秒,Shorts 最長 3 分鐘" % dur)
    if w > h:
        fails.append("影片是橫式 %dx%d,Shorts 要直式或正方形" % (w, h))
    if not title or len(title) > 100:
        fails.append("標題 %d 字,要 1–100 字" % len(title))
    if len(desc.encode("utf-8")) > 5000:
        fails.append("說明超過 5000 位元組")
    fails += IG.caption_checks(proj, title + "\n" + desc)
    notes.append("影片 %s:%dx%d、%.1f 秒" % (os.path.basename(video), w, h, dur))
    notes.append("標題(%d 字):%s" % (len(title), title))
    notes.append("說明 %d 字;第一行:%s" % (len(desc), (desc.splitlines() or [""])[0]))
    notes.append("合成內容揭露:%s" % ("否(no_ai_visuals)" if IG.no_ai(proj) else "是"))
    return fails, notes


# ---------------- 狀態 ----------------
def sp(proj):
    return os.path.join(proj, "06_final", "_yt_state.json")


def load(proj):
    return json.load(open(sp(proj), encoding="utf-8")) if os.path.exists(sp(proj)) else {}


def save(proj, st):
    json.dump(st, open(sp(proj), "w", encoding="utf-8"), ensure_ascii=False, indent=1)


def status_body(proj, privacy, when=None):
    s = {"privacyStatus": privacy, "selfDeclaredMadeForKids": False,
         "containsSyntheticMedia": not IG.no_ai(proj), "embeddable": True, "license": "youtube"}
    if when is not None:
        s["publishAt"] = when.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    return s


# ---------------- 發佈/排程 ----------------
def post(proj, when=None, yt=None, index=None):
    from googleapiclient.http import MediaFileUpload
    video, cover, copy = IG.find_files(proj)
    title, desc, tags = yt_texts(proj, copy)
    fails, notes = local_checks(proj, video, title, desc)
    for n in notes:
        print("  " + n)
    if fails:
        for f in fails:
            print("FAIL " + f)
        raise SystemExit("本機檢查沒過,先修再發")
    st = load(proj)
    if st.get("video_id"):
        raise SystemExit("這支 YouTube 已經傳過了:%s" % st.get("url"))
    if when is not None and when - datetime.datetime.now() < datetime.timedelta(minutes=15):
        raise SystemExit("排程時間要在 15 分鐘以後")
    yt = yt or build_client()
    cid, ctitle = whoami(yt)
    if "--force" not in sys.argv:
        index = index if index is not None else uploads_index(yt)
        hit = index.get(W.norm(title))
        if hit:
            st.update(channel_id=cid, video_id=hit[0], url="https://youtube.com/shorts/" + hit[0],
                      privacy=hit[1], found_on_platform=True)
            save(proj, st)
            raise SystemExit("頻道上已經有同標題的影片:%s —— 紀錄已補上,這次不傳(確定要再傳加 --force)" % st["url"])
    body = {"snippet": {"title": title, "description": desc, "tags": tags, "categoryId": "27",
                        "defaultLanguage": "zh-Hant", "defaultAudioLanguage": "zh-Hant"},
            "status": status_body(proj, "public" if when is None else "private", when)}
    log(proj, "YouTube → 頻道「%s」%s:%s" % (ctitle, "" if when is None else ",排 " + when.strftime("%Y-%m-%d %H:%M"), title))
    req = yt.videos().insert(part="snippet,status", body=body,
                             media_body=MediaFileUpload(video, chunksize=8 * 1024 * 1024, resumable=True))
    resp = None
    while resp is None:
        _, resp = req.next_chunk()
    vid = resp["id"]
    got = resp.get("status", {})
    st = {"channel_id": cid, "channel": ctitle, "video_id": vid, "url": "https://youtube.com/shorts/" + vid,
          "title": title, "video": os.path.basename(video), "uploaded_at": datetime.datetime.now().isoformat(timespec="seconds"),
          "privacy": got.get("privacyStatus")}
    if when is not None:
        st["scheduled_for"] = when.isoformat(timespec="minutes")
    else:
        st["published_at"] = st["uploaded_at"]
    want = "public" if when is None else "private"
    if got.get("privacyStatus") != want or (when is not None and not got.get("publishAt")):
        st["locked_private"] = True
        log(proj, "⚠ YouTube 回的隱私狀態是 %s(要的是 %s%s):API 專案還沒通過稽核,影片被鎖成私人"
            % (got.get("privacyStatus"), want, "＋排程" if when else ""))
    save(proj, st)
    log(proj, "已上傳 YouTube:%s(%s)" % (st["url"], "公開" if when is None else "排 " + st["scheduled_for"]))
    return st


def reschedule(proj, when):
    st = load(proj)
    if not st.get("video_id") or st.get("published_at"):
        raise SystemExit("紀錄裡沒有還沒公開的 YouTube 影片")
    yt = build_client()
    yt.videos().update(part="status", body={"id": st["video_id"], "status": status_body(proj, "private", when)}).execute()
    old, st["scheduled_for"] = st.get("scheduled_for"), when.isoformat(timespec="minutes")
    save(proj, st)
    log(proj, "YouTube 改排程 %s -> %s(%s)" % (old, st["scheduled_for"], st["video_id"]))


def unschedule(proj):
    st = load(proj)
    if not st.get("video_id"):
        raise SystemExit("紀錄裡沒有 YouTube 影片")
    if st.get("published_at") and "--force" not in sys.argv:
        raise SystemExit("這支已經公開了(%s);確定要刪加 --force" % st["url"])
    yt = build_client()
    yt.videos().delete(id=st["video_id"]).execute()
    os.rename(sp(proj), sp(proj) + ".deleted_" + datetime.datetime.now().strftime("%Y%m%d%H%M"))
    log(proj, "YouTube 影片 %s 已刪除" % st["video_id"])


# ---------------- 補發 ----------------
def candidates():
    """說書片、冷知識片:reel.json pet_only 且有 cast(對談/單人主持),排除 SKIP_SLUGS。"""
    root = next((r for r in REELS_ROOTS if os.path.isdir(r)), None)
    if not root:
        raise SystemExit("找不到雲端硬碟 reels 資料夾")
    out = []
    for proj in sorted(glob.glob(os.path.join(root, "*"))):
        slug = os.path.basename(proj)
        j = reel_json(proj)
        if slug in SKIP_SLUGS or not (j.get("pet_only") and j.get("cast")) or not os.path.isdir(os.path.join(proj, "06_final")):
            continue
        ig = os.path.join(proj, "06_final", "_ig_state.json")
        igs = json.load(open(ig, encoding="utf-8")) if os.path.exists(ig) else {}
        out.append({"proj": proj, "slug": slug, "ig_pub": igs.get("published_at") if igs.get("permalink") else None,
                    "ig_sched": None if igs.get("permalink") else igs.get("scheduled_for"), "yt": load(proj)})
    return out


def backlog():
    rows = candidates()
    for r in rows:
        y = r["yt"]
        ys = ("公開 " + y["url"]) if y.get("published_at") else ("排 %s" % y["scheduled_for"]) if y.get("scheduled_for") \
            else ("已有 " + y["url"]) if y.get("video_id") else "—"
        if y.get("locked_private"):
            ys += "(鎖私人)"
        ig = ("已發 " + (r["ig_pub"] or "")[:10]) if r["ig_pub"] else ("排 " + r["ig_sched"][:16]) if r["ig_sched"] else "未排"
        print("%-32s IG:%-18s YT:%s" % (r["slug"], ig, ys))
    print("共 %d 支;YouTube 待補發 %d、待同步排程 %d" % (
        len(rows), sum(1 for r in rows if r["ig_pub"] and not r["yt"].get("video_id")),
        sum(1 for r in rows if r["ig_sched"] and not r["yt"].get("video_id"))))


def backfill(n, dry):
    todo = sorted([r for r in candidates() if r["ig_pub"] and not r["yt"].get("video_id")], key=lambda r: r["ig_pub"])[:n]
    if dry:
        for r in todo:
            print("會補發:", r["slug"])
        return
    yt = build_client()
    index = uploads_index(yt)
    for r in todo:
        try:
            post(r["proj"], None, yt, index)
        except SystemExit as e:
            log(r["proj"], "補發沒成功:%s" % str(e.code)[:300])
            if "quota" in str(e.code).lower():
                break
        except Exception as e:
            log(r["proj"], "補發錯誤 %s: %s" % (type(e).__name__, str(e)[:300]))
            if "quotaExceeded" in str(e) or "uploadLimitExceeded" in str(e):
                print("今天的配額或上傳上限用完了,明天再繼續")
                break


def sync_schedule(dry):
    now = datetime.datetime.now() + datetime.timedelta(minutes=20)
    todo = []
    for r in candidates():
        if r["ig_sched"] and not r["yt"].get("video_id"):
            when = datetime.datetime.fromisoformat(r["ig_sched"][:16])
            if when > now:
                todo.append((when, r))
    todo.sort(key=lambda x: x[0])
    if dry:
        for when, r in todo:
            print("會排:", when.strftime("%Y-%m-%d %H:%M"), r["slug"])
        return
    yt = build_client()
    index = uploads_index(yt)
    for when, r in todo[:6]:
        try:
            post(r["proj"], when, yt, index)
        except Exception as e:
            log(r["proj"], "同步排程沒成功:%s" % str(getattr(e, "code", e))[:300])
            if "quota" in str(e).lower() or "uploadLimitExceeded" in str(e):
                break


def main():
    global _PROJ
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = {a.split("=", 1)[0] for a in sys.argv[1:] if a.startswith("--")}
    opts = dict(a[2:].split("=", 1) for a in sys.argv[1:] if a.startswith("--") and "=" in a)
    if "--setup-auth" in flags:
        if len(args) != 1:
            raise SystemExit("用法:--setup-auth <client_secret.json>")
        return setup_auth(args[0])
    if "--whoami" in flags:
        cid, title = whoami(build_client())
        print("授權的頻道:「%s」(%s)" % (title, cid))
        return
    if "--backlog" in flags:
        return backlog()
    if "--backfill" in flags:
        return backfill(int(opts.get("backfill", "6")), "--dry-run" in flags)
    if "--sync-schedule" in flags:
        return sync_schedule("--dry-run" in flags)
    if len(args) != 1 or not os.path.isdir(os.path.join(args[0], "06_final")):
        raise SystemExit(__doc__)
    proj = _PROJ = os.path.abspath(args[0])
    if "--dry" in flags:
        video, cover, copy = IG.find_files(proj)
        title, desc, _ = yt_texts(proj, copy)
        fails, notes = local_checks(proj, video, title, desc)
        for n in notes:
            print("  " + n)
        for f in fails:
            print("FAIL " + f)
        print("本機檢查:%s" % ("沒過" if fails else "通過(沒有連網、沒有上傳)"))
        sys.exit(1 if fails else 0)
    when_of = lambda k: datetime.datetime.strptime(opts.get(k, "").strip().replace("T", " "), "%Y-%m-%d %H:%M")
    if "--reschedule" in flags:
        return reschedule(proj, when_of("reschedule"))
    if "--unschedule" in flags:
        return unschedule(proj)
    if "--schedule" in flags:
        return post(proj, when_of("schedule"))
    if "--publish" in flags:
        return post(proj)
    raise SystemExit(__doc__)


if __name__ == "__main__":
    try:
        main()
    except (SystemExit, Exception) as e:
        msg = e.code if isinstance(e, SystemExit) else "%s: %s" % (type(e).__name__, e)
        if _PROJ and isinstance(msg, str):
            try:
                log(_PROJ, W.log_prefix(msg) + " / ".join(msg.strip().splitlines())[:800])
            except Exception:
                pass
        raise
