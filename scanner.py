#!/usr/bin/env python3
"""
S&D + ICT Fırsat Tarayıcı  (sürüm 4)
====================================
Strateji:
  1) Yön      : 4 saatlik trend (son kırılan salınım)
  2) Bölge    : 1 saatlik arz/talep bölgesi (küçük baz mum + güçlü çıkış), 1. veya 2. test
  3) Zaman    : Londra ve New York seansları (killzone)
  4) Teyit    : 15 dakikalıkta yapı kırılımı (MSS). Likidite süpürmesi kaliteyi artırır.
  5) Giriş    : FVG'ye / MSS mumuna geri çekilmede limit, karşı bölgeye en az 1:2 yer
  +) Kırılım + geri test: Asya / Londra / New York / dünkü tepe-dip kırılıp geri test edilince fırsat

Mesajlar:
  İZLEMEDE   : fiyat bölgeye geldi, ne beklendiği ve neyin iptal ettireceği
  KURULUM    : tüm kapılar açıldı, nedenleri + giriş/stop/hedef planı + kalite notu
  GİRİŞ / TP1 / TP2 / STOP / İPTAL : işlemin takibi

Modlar (MODE ortam değişkeni ya da ilk argüman):
  scan  : normal tarama (GitHub her 5 dakikada bunu çalıştırır)
  test  : Telegram'a test mesajı + anlık durum
  brief : günlük özet
  rapor : son ~59 gün geçmiş testi, 4 ayar seti karşılaştırmalı
"""
import datetime as dt
import html
import json
import os
import sys
import time
import traceback
import urllib.parse
import urllib.request
from dataclasses import dataclass
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

# =====================================================================
# AYARLAR — değiştirmek istersen sadece bu bölümü düzenle
# =====================================================================
SYMBOLS = {
    # ad       : Yahoo Finance kodu, fiyat basamağı, mesafe birimi
    "EURUSD": {"ticker": "EURUSD=X", "digits": 5, "pip": 0.0001, "unit": "pip"},
    "GBPUSD": {"ticker": "GBPUSD=X", "digits": 5, "pip": 0.0001, "unit": "pip"},
    "XAUUSD": {"ticker": "GC=F",     "digits": 2, "pip": 1.0,    "unit": "$"},     # Altın vadeli
    "US100":  {"ticker": "NQ=F",     "digits": 2, "pip": 1.0,    "unit": "puan"},  # Nasdaq 100 vadeli
    "BTCUSD": {"ticker": "BTC-USD",  "digits": 2, "pip": 1.0,    "unit": "$"},
}

SETTINGS = {
    "bias_tf": "4h",          # yön zaman dilimi
    "zone_tf": "1h",          # bölge zaman dilimi
    "use_bias": True,         # 4s yöne ters işlem arama
    "disp_mult": 1.0,         # bölge çıkış mumu gövdesi >= 1s ATR x bu
    "base_mult": 0.6,         # baz mum gövdesi <= 1s ATR x bu
    "max_zones": 8,           # taraf başına en fazla aktif bölge
    "max_tests": 2,           # bölgenin 1. ve 2. testi kurulum üretebilir (senin retest mantığın)
    "use_killzone": False,    # True yaparsan fırsatlar sadece seans saatlerinde gelir
    "killzones": [("02:00", "05:00", "Londra"), ("07:00", "11:30", "New York")],  # New York saati
    "require_sweep": False,   # True: süpürme şart. False: süpürme kaliteyi artırır
    "mss_mult": 0.6,          # MSS mumu gövdesi >= 15dk ATR x bu
    "piv_len": 2,             # 15dk salınım uzunluğu
    "htf_piv_len": 3,         # 4s salınım uzunluğu
    "watch_bars": 32,         # bölgeye dokunduktan sonra teyit bekleme (15dk mum, 32 = 8 saat)
    "arm_bars": 16,           # kurulumdan sonra girişe gelme bekleme (16 = 4 saat)
    "trade_bars": 192,        # açık işlemi en fazla izleme süresi (192 = 2 gün)
    "buf_mult": 0.5,          # stop tamponu = 15dk ATR x bu
    "min_rr": 2.0,            # karşı bölgeye en az R:R
    "use_retest": True,       # seans tepe/diplerinde kırılım + geri test fırsatları
    "retest_bars": 32,        # kırılımdan sonra geri test bekleme (32 = 8 saat)
    "retest_levels": ("Londra", "New York", "Dünkü"),  # takip edilen seans seviyeleri
    "break_mult": 0.8,        # kırılım mumu gövdesi >= 15dk ATR x bu (güçlü kırılım)
    "away_mult": 1.0,         # geri testten önce seviyeden en az ATR x bu uzaklaşmalı
    "fail_mult": 1.5,         # seviyenin ATR x bu kadar ötesinde kapanış = kırılım başarısız
}

# Rapor modunda karşılaştırılacak ayar setleri
VARIANTS = {
    "Dengeli (aktif)": {},
    "Katı (eski ayarlar)": {"use_killzone": True, "use_retest": False, "max_tests": 1, "require_sweep": True, "mss_mult": 0.8, "disp_mult": 1.2,
                            "base_mult": 0.5, "piv_len": 3, "watch_bars": 24,
                            "killzones": [("02:00", "05:00", "Londra"), ("09:30", "11:00", "NY AM")]},
    "Sadece bölgeden dönüş": {"use_retest": False},
    "Sadece seans saatleri": {"use_killzone": True},
    "Gevşek": {"mss_mult": 0.4, "disp_mult": 0.8, "min_rr": 1.5},
}

SEND_WATCH_ALERTS = True      # "İZLEMEDE" açıklama mesajları
SEND_CHARTS = True            # İZLEMEDE ve KURULUM mesajlarıyla birlikte grafik resmi gönder
WATCH_ONLY_IN_KILLZONE = False # izleme mesajı sadece seans saatlerinde gelsin (gece rahatsız etmez)
SEND_CANCEL_ALERTS = True     # açık fırsat iptal olunca haber ver (bekleyen emrini silmen için)
SEND_GIVEUP_ALERTS = False    # "fikrinden vazgeçtim" mesajları (saatlik durum zaten anlatıyor)
SEND_RESULT_ALERTS = True     # TP1 / TP2 / stop mesajları
ALERT_LOOKBACK_HOURS = 3      # son kaç saatteki olaylar bildirilsin (gecikmelere karşı)
DAILY_BRIEF_TR_HOUR = 8       # günlük özet saati (Türkiye saati)
HOURLY_STATUS = True          # her saat başı sade dille durum mesajı
HOURLY_HOURS_TR = range(0, 24)  # saatlik mesajların geleceği saatler (Türkiye saati). 0-24 = 7/24
STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state.json")

NY = ZoneInfo("America/New_York")
TR = ZoneInfo("Europe/Istanbul")
BAR = pd.Timedelta("15min")


# =====================================================================
# Yardımcılar
# =====================================================================
def rma(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(alpha=1.0 / n, adjust=False).mean()


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    pc = df["close"].shift(1)
    tr = pd.concat([df["high"] - df["low"], (df["high"] - pc).abs(), (df["low"] - pc).abs()], axis=1).max(axis=1)
    return rma(tr, n)


def resample(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    out = df.resample(rule, label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last"})
    return out.dropna()


def pivots(high: np.ndarray, low: np.ndarray, p: int):
    """Onaylanmış salınımlar: ph[i] = (i-p)'deki tepe, i'de onaylanır. Yoksa nan."""
    n = len(high)
    ph = np.full(n, np.nan)
    pl = np.full(n, np.nan)
    for i in range(2 * p, n):
        c = i - p
        wh = high[c - p:i + 1]
        if high[c] == wh.max() and (wh == high[c]).sum() == 1:
            ph[i] = high[c]
        wl = low[c - p:i + 1]
        if low[c] == wl.min() and (wl == low[c]).sum() == 1:
            pl[i] = low[c]
    return ph, pl


def fmt(x: float, digits: int) -> str:
    return f"{x:,.{digits}f}"


def tr_time(ts: pd.Timestamp) -> str:
    return ts.tz_convert(TR).strftime("%d.%m %H:%M")


def mirror(df: pd.DataFrame) -> pd.DataFrame:
    """Satış tarafını alış mantığıyla hesaplamak için fiyatı ters çevirir."""
    return pd.DataFrame({"open": -df["open"], "high": -df["low"], "low": -df["high"], "close": -df["close"]},
                        index=df.index)


# =====================================================================
# Strateji motoru
# =====================================================================
# Motor her zaman "alış" mantığıyla yazıldı. Satış tarafı, fiyat ters çevrilerek
# (mirror) aynı kodla hesaplanır; sonuçlar tekrar çevrilir. Böylece iki taraf
# birebir aynı kurallarla çalışır ve kod tekrarı olmaz.

@dataclass
class Zone:
    top: float
    bot: float
    born: pd.Timestamp
    tests: int = 0
    used: bool = False


PRICE_FIELDS = ("ztop", "zbot", "ref_h", "ref_l", "invalid", "entry", "stop", "tp1", "tp2", "opp", "lvl", "rlow")


def bias_by_bar(df15: pd.DataFrame, s: dict) -> np.ndarray:
    h4 = resample(df15, s["bias_tf"])
    H, L, C = h4["high"].values, h4["low"].values, h4["close"].values
    ph, pl = pivots(H, L, s["htf_piv_len"])
    st, last_h, last_l = 0, np.nan, np.nan
    out = np.zeros(len(h4), dtype=int)
    for i in range(len(h4)):
        if not np.isnan(ph[i]):
            last_h = ph[i]
        if not np.isnan(pl[i]):
            last_l = pl[i]
        if not np.isnan(last_h) and C[i] > last_h:
            st = 1
        if not np.isnan(last_l) and C[i] < last_l:
            st = -1
        out[i] = st
    # Sadece KAPANMIŞ 4s mumlar kullanılır
    ends = pd.DataFrame({"t": (h4.index + pd.Timedelta(s["bias_tf"])).as_unit("ns"), "bias": out})
    left = pd.DataFrame({"t": df15.index.as_unit("ns")})
    m = pd.merge_asof(left, ends, on="t", direction="backward")
    return m["bias"].fillna(0).astype(int).values


def zone_events(df15: pd.DataFrame, s: dict):
    """1s talep (alış tarafı) bölgeleri ve karşı taraf (arz) bölgeleri."""
    h1 = resample(df15, s["zone_tf"])
    a = atr(h1).values
    O, H, L, C = (h1[c].values for c in ("open", "high", "low", "close"))
    step = pd.Timedelta(s["zone_tf"])
    ev = []
    for i in range(1, len(h1)):
        if np.isnan(a[i - 1]) or np.isnan(a[i]):
            continue
        if abs(C[i - 1] - O[i - 1]) > s["base_mult"] * a[i - 1]:
            continue
        end = h1.index[i] + step
        if C[i] > O[i] and (C[i] - O[i]) >= s["disp_mult"] * a[i] and C[i] > H[i - 1]:
            ev.append((end, "own", max(O[i - 1], C[i - 1]), min(L[i - 1], L[i])))
        if C[i] < O[i] and (O[i] - C[i]) >= s["disp_mult"] * a[i] and C[i] < L[i - 1]:
            ev.append((end, "opp", max(H[i - 1], H[i]), min(O[i - 1], C[i - 1])))
    return ev


def in_killzone(ts: pd.Timestamp, s: dict) -> bool:
    t = ts.tz_convert(NY)
    hm = t.hour * 60 + t.minute
    for kz in s["killzones"]:
        ha, ma = map(int, kz[0].split(":"))
        hb, mb = map(int, kz[1].split(":"))
        if ha * 60 + ma <= hm < hb * 60 + mb:
            return True
    return False


def _eval_rr(H, L, i0, entry, stop, rr, max_bars):
    """Sabit hedefli sonuç (alış koordinatında). Aynı mumda stop+hedef = stop."""
    tp = entry + rr * (entry - stop)
    for j in range(i0, min(len(H), i0 + max_bars)):
        if L[j] <= stop:
            return -1.0
        if j > i0 and H[j] >= tp:
            return rr
    return None


SESSIONS = [("Asya", "20:00", "24:00"), ("Londra", "02:00", "05:00"), ("New York", "09:30", "11:00")]


def session_levels(df: pd.DataFrame):
    """Seans tepeleri (alış koordinatında) ve dünkü tepe. [(bitiş zamanı, seviye, ad)]"""
    ny = df.index.tz_convert(NY)
    hm = ny.hour * 60 + ny.minute
    H = df["high"].values
    names = np.array([None] * len(df), dtype=object)
    for nm, a, b in SESSIONS:
        ha, ma = map(int, a.split(":"))
        hb, mb = map(int, b.split(":"))
        mask = (hm >= ha * 60 + ma) & (hm < hb * 60 + mb)
        names[mask] = nm
    out = []
    idx = df.index
    k = 0
    while k < len(df):
        nm = names[k]
        j = k
        while (j + 1 < len(df) and names[j + 1] == nm
               and (idx[j + 1] - idx[j]) <= pd.Timedelta("1h")):
            j += 1
        if nm is not None:
            out.append((idx[j] + BAR, float(H[k:j + 1].max()), nm))
        k = j + 1
    # dünkü tepe (New York günü)
    day = pd.Series(H, index=df.index).groupby(ny.date).agg(["max"])
    last_idx = pd.Series(df.index, index=df.index).groupby(ny.date).max()
    for d0 in day.index[:-1]:
        out.append((last_idx[d0] + BAR, float(day.loc[d0, "max"]), "Dünkü"))
    out.sort(key=lambda r: r[0])
    return out


def run_side(df: pd.DataFrame, s: dict, use_kz: bool, bias: np.ndarray):
    """Tek taraf (alış koordinatında). Olaylar, işlemler ve son durumu döndürür."""
    n = len(df)
    t = df.index
    O, H, L, C = (df[c].values for c in ("open", "high", "low", "close"))
    A = atr(df).values
    ph, pl = pivots(H, L, s["piv_len"])
    p = s["piv_len"]
    zev = zone_events(df, s)
    lev = session_levels(df) if s.get("use_retest", True) else []
    zi = li = 0

    own, opp = [], []
    last_ph = last_pl = np.nan
    events, trades, open_trades, armed = [], [], [], []
    pl_list = []          # (dip değeri, dibin oluştuğu mum)
    levels = []           # seans seviyeleri: dict(lvl, name, born, state, ...)
    W = dict(state=0)

    def push(arr, z):
        arr.append(z)
        while len(arr) > s["max_zones"]:
            arr.pop(0)

    def nearest_opp(entry):
        tops = [z.bot for z in opp if z.bot > entry]
        return min(tops) if tops else None

    def make_setup(i, e, st, extra):
        """Ortak kurulum: karşı bölge kontrolü + kalite. None = yer yok."""
        r = e - st
        if r <= 0:
            return None, None
        o = nearest_opp(e)
        room = None if o is None else (o - e) / r
        if room is not None and room < s["min_rr"]:
            return None, room
        ev = dict(kind="SETUP", i=i, time=t[i], entry=e, stop=st, risk=r, tp1=e + 2 * r, tp2=e + 3 * r,
                  room=room, opp=o, **extra)
        return ev, room

    for i in range(2, n):
        ti = t[i]
        a_i = A[i] if not np.isnan(A[i]) else 0.0
        buf = s["buf_mult"] * a_i
        kz_ok = (not use_kz) or in_killzone(ti, s)
        bias_ok = (not s["use_bias"]) or bias[i] == 1
        not_against = (not s["use_bias"]) or bias[i] != -1

        # 1) Yeni 1s bölgeler ve seans seviyeleri (sadece kapanmış olanlar)
        while zi < len(zev) and zev[zi][0] <= ti:
            end, kind, top, bot = zev[zi]
            push(own if kind == "own" else opp, Zone(top, bot, end))
            zi += 1
        while li < len(lev) and lev[li][0] <= ti:
            end, lvl, name = lev[li]
            if C[i - 1] < lvl and name in s.get("retest_levels", ("Londra", "New York", "Dünkü")):
                levels.append(dict(lvl=lvl, name=name, born=end, state="fresh"))
            li += 1

        # 2) 15dk salınımlar
        if not np.isnan(ph[i]):
            last_ph = ph[i]
            if W["state"] == 1 and (i - p) >= W["start"]:
                W["ref_h"] = ph[i]      # bölgeden ilk tepkinin tepesi yeni kırılım seviyesi olur
        if not np.isnan(pl[i]):
            last_pl = pl[i]
            pl_list.append((pl[i], i - p))
            if len(pl_list) > 200:
                pl_list.pop(0)

        # 3) Bölgeler: geçersiz kılma + test sayımı
        for z in list(opp):
            if C[i] > z.top:
                opp.remove(z)
        for z in list(reversed(own)):
            if C[i] < z.bot:
                own.remove(z)
                continue
            if L[i] <= z.top and L[i - 1] > z.top:
                z.tests += 1
                if (W["state"] == 0 and not z.used and z.tests <= s["max_tests"] and bias_ok):
                    cands = [v for v, b in pl_list if b >= i - 192 and v < L[i]]
                    liq = max(cands) if cands else np.nan
                    W = dict(state=1, zone=z, start=i, sweep=L[i], ref_h=last_ph, ref_l=liq,
                             swept=False, swept_lvl=np.nan, test_no=z.tests, wt=ti)
                    events.append(dict(kind="WATCH", i=i, time=ti, ztop=z.top, zbot=z.bot, born=z.born,
                                       test_no=z.tests, ref_h=last_ph, ref_l=liq,
                                       invalid=z.bot - buf, kz_now=in_killzone(ti, s)))

        # 4) Bölge akışı: tepki + kırılım bekleniyor
        if W["state"] == 1:
            z = W["zone"]
            W["sweep"] = min(W["sweep"], L[i])
            if C[i] < z.bot - buf:
                events.append(dict(kind="UNWATCH", i=i, time=ti, ztop=z.top, zbot=z.bot, watch_time=W["wt"],
                                   reason="15dk'lık mum bölgenin dışında kapandı, bölge bozuldu."))
                W = dict(state=0)
            elif i - W["start"] > s["watch_bars"]:
                events.append(dict(kind="UNWATCH", i=i, time=ti, ztop=z.top, zbot=z.bot, watch_time=W["wt"],
                                   reason=f"{s['watch_bars'] * 15 // 60} saat içinde beklediğim kırılım gelmedi."))
                W = dict(state=0)
            else:
                ref_h, ref_l = W["ref_h"], W["ref_l"]
                lvl_sweep = (not np.isnan(ref_l)) and L[i] < ref_l and C[i] > ref_l
                prev_min = L[max(0, i - 96):i].min()
                day_sweep = L[i] < prev_min and C[i] > prev_min
                if lvl_sweep or day_sweep:
                    W["swept"] = True
                    W["swept_lvl"] = ref_l if lvl_sweep else prev_min
                swept = W["swept"]
                mss = (not np.isnan(ref_h)) and C[i] > ref_h and (C[i] - O[i]) >= s["mss_mult"] * a_i
                if mss and bias_ok and kz_ok and (swept or not s["require_sweep"]) and not armed:
                    fvg = L[i] > H[i - 2]
                    e = L[i] if fvg else (O[i] + C[i]) / 2
                    ev, room = make_setup(i, e, W["sweep"] - buf, dict(
                        setup="ZONE", ztop=z.top, zbot=z.bot, test_no=W["test_no"], swept=swept,
                        ref_h=ref_h, ref_l=W["swept_lvl"] if swept else ref_l, fvg=fvg))
                    z.used = True
                    if ev is None:
                        if room is not None:
                            events.append(dict(kind="UNWATCH", i=i, time=ti, ztop=z.top, zbot=z.bot,
                                               watch_time=W["wt"],
                                               reason=f"Kırılım geldi ama önündeki karşı bölge çok yakın "
                                                      f"({room:.1f}R). 1:2 kazanç yeri yok, işlem değmez."))
                    else:
                        score = int(swept) + int(W["test_no"] == 1) + int(fvg) + int(room is None or room >= 3)
                        ev["grade"] = "A" if score >= 3 else ("B" if score == 2 else "C")
                        ev["score"] = score
                        events.append(ev)
                        armed.append(dict(ev=ev, arm=i))
                    W = dict(state=0)

        # 5) Kırılım + geri test akışı (seans tepeleri)
        for lv in list(levels):
            if ti - lv["born"] > pd.Timedelta("30h") and lv["state"] == "fresh":
                levels.remove(lv)
                continue
            if lv["state"] == "fresh":
                if C[i] > lv["lvl"] and not ((C[i] - O[i]) >= s.get("break_mult", 0.8) * a_i and C[i] > lv["lvl"] + 0.2 * a_i):
                    lv["clean"] = False       # güçlü olmayan bir mumla üstünde kapandı
                if C[i] > lv["lvl"] and lv.get("clean", True) is False:
                    levels.remove(lv)          # zayıf kırılım: takip etme
                    continue
                if C[i] > lv["lvl"] + 0.2 * a_i and (C[i] - O[i]) >= s.get("break_mult", 0.8) * a_i:
                    # aynı yerdeki başka bir kırılmış seviyeyi tekrar takip etme
                    dup = any(o is not lv and o["state"] in ("broken", "retest") and abs(o["lvl"] - lv["lvl"]) < 0.5 * a_i
                              for o in levels)
                    if dup:
                        levels.remove(lv)
                        continue
                    lv.update(state="broken", bi=i, bt=ti, rlow=np.inf, away=False)
                    if bias_ok:
                        lv["announced"] = True
                        events.append(dict(kind="BREAK", i=i, time=ti, lvl=lv["lvl"], name=lv["name"],
                                           invalid=lv["lvl"] - s.get("fail_mult", 1.5) * a_i, kz_now=in_killzone(ti, s)))
                continue
            # kırılmış: önce seviyeden uzaklaşmalı, sonra geri test gelmeli
            if H[i] >= lv["lvl"] + s.get("away_mult", 1.0) * a_i:
                lv["away"] = True
            if lv["away"] and L[i] <= lv["lvl"] + 0.3 * a_i:
                lv["state"] = "retest"
            if lv["state"] == "retest":
                lv["rlow"] = min(lv["rlow"], L[i])
            if C[i] < lv["lvl"] - s.get("fail_mult", 1.5) * a_i or i - lv["bi"] > s.get("retest_bars", 32):
                if lv.get("announced"):
                    why = ("15dk'lık mum seviyenin belirgin şekilde altında kapandı, kırılım başarısız."
                           if C[i] < lv["lvl"] - s.get("fail_mult", 1.5) * a_i else
                           f"{s.get('retest_bars', 32) * 15 // 60} saat içinde beklediğim geri test gelmedi.")
                    events.append(dict(kind="RFAIL", i=i, time=ti, lvl=lv["lvl"], name=lv["name"],
                                       watch_time=lv["bt"], reason=why))
                levels.remove(lv)
                continue
            if (lv["state"] == "retest" and C[i] > lv["lvl"] and C[i] > O[i] and (C[i] - O[i]) >= 0.4 * a_i
                    and bias_ok and kz_ok and not armed):
                swept = lv["rlow"] < lv["lvl"] - 0.2 * a_i      # seviyenin altına sarkıp geri alındı
                e = max(lv["lvl"], (O[i] + C[i]) / 2)
                ev, room = make_setup(i, e, lv["rlow"] - buf, dict(
                    setup="RETEST", lvl=lv["lvl"], name=lv["name"], rlow=lv["rlow"], swept=swept,
                    fvg=False, ztop=None, zbot=None, trend=int(bias[i])))
                levels.remove(lv)
                if ev is None:
                    if room is not None and lv.get("announced"):
                        events.append(dict(kind="RFAIL", i=i, time=ti, lvl=lv["lvl"], name=lv["name"],
                                           watch_time=lv["bt"],
                                           reason=f"Geri testten döndü ama önündeki karşı bölge çok yakın ({room:.1f}R). "
                                                  f"1:2 kazanç yeri yok."))
                    continue
                score = (int(swept) + int(lv["name"] in ("Londra", "Dünkü", "New York"))
                         + int(bias[i] == 1 or not s["use_bias"]) + int(room is None or room >= 3))
                ev["grade"] = "A" if score >= 3 else ("B" if score == 2 else "C")
                ev["score"] = score
                events.append(ev)
                armed.append(dict(ev=ev, arm=i))

        # 6) Kurulum hazır: girişe gelmesi bekleniyor
        for a in list(armed):
            ev = a["ev"]
            if a["arm"] == i:
                continue
            if L[i] <= ev["entry"]:
                events.append(dict(kind="ENTRY", i=i, time=ti, entry=ev["entry"], stop=ev["stop"],
                                   tp1=ev["tp1"], tp2=ev["tp2"], risk=ev["risk"], grade=ev["grade"]))
                tr = dict(i=i, entry=ev["entry"], stop=ev["stop"], risk=ev["risk"], tp1=ev["tp1"],
                          tp2=ev["tp2"], grade=ev["grade"], setup=ev["setup"], tp1_hit=False, managed=None)
                trades.append(tr)
                open_trades.append(tr)
                armed.remove(a)
            elif H[i] >= ev["tp1"]:
                events.append(dict(kind="CANCEL", i=i, time=ti, entry=ev["entry"],
                                   reason="Fiyat girişe gelmeden TP1'e gitti, fırsat kaçtı."))
                armed.remove(a)
            elif i - a["arm"] > s["arm_bars"]:
                events.append(dict(kind="CANCEL", i=i, time=ti, entry=ev["entry"],
                                   reason=f"{s['arm_bars'] * 15 // 60} saat içinde girişe gelmedi."))
                armed.remove(a)

        # 7) Açık işlemlerin takibi (TP1'de yarısı + stop girişe, TP2'de kalanı)
        for tr in list(open_trades):
            if not tr["tp1_hit"]:
                if L[i] <= tr["stop"]:
                    tr["managed"] = -1.0
                    events.append(dict(kind="STOP", i=i, time=ti, entry=tr["entry"], stop=tr["stop"], result=-1.0))
                    open_trades.remove(tr)
                    continue
                if i > tr["i"] and H[i] >= tr["tp1"]:
                    tr["tp1_hit"] = True
                    events.append(dict(kind="TP1", i=i, time=ti, entry=tr["entry"], tp1=tr["tp1"], tp2=tr["tp2"]))
                    if H[i] >= tr["tp2"]:
                        tr["managed"] = 2.5
                        events.append(dict(kind="TP2", i=i, time=ti, tp2=tr["tp2"], result=2.5))
                        open_trades.remove(tr)
                        continue
            else:
                if L[i] <= tr["entry"]:
                    tr["managed"] = 1.0
                    events.append(dict(kind="BE", i=i, time=ti, entry=tr["entry"], result=1.0))
                    open_trades.remove(tr)
                    continue
                if H[i] >= tr["tp2"]:
                    tr["managed"] = 2.5
                    events.append(dict(kind="TP2", i=i, time=ti, tp2=tr["tp2"], result=2.5))
                    open_trades.remove(tr)
                    continue
            if i - tr["i"] > s["trade_bars"]:
                open_trades.remove(tr)

    for tr in trades:
        tr["r2"] = _eval_rr(H, L, tr["i"], tr["entry"], tr["stop"], 2.0, s["trade_bars"])
        tr["r3"] = _eval_rr(H, L, tr["i"], tr["entry"], tr["stop"], 3.0, s["trade_bars"])

    # son durumdaki seviyeler (saatlik özet için)
    lv_state = [dict(lvl=lv["lvl"], name=lv["name"], state=lv["state"]) for lv in levels]
    return events, trades, dict(own=own, opp=opp, watch=W, open_trades=open_trades,
                                armed=[a["ev"] for a in armed], levels=lv_state)


def _flip_event(ev):
    ev = dict(ev)
    for k in PRICE_FIELDS:
        if k in ev and ev[k] is not None and not (isinstance(ev[k], float) and np.isnan(ev[k])):
            ev[k] = -ev[k]
    if ev.get("ztop") is not None and ev.get("zbot") is not None:
        ev["ztop"], ev["zbot"] = ev["zbot"], ev["ztop"]
    return ev


def run_strategy(df: pd.DataFrame, s: dict, use_kz: bool = True):
    bias = bias_by_bar(df, s)
    ev_l, tr_l, st_l = run_side(df, s, use_kz, bias)
    ev_s, tr_s, st_s = run_side(mirror(df), s, use_kz, -bias)
    for e in ev_l:
        e["dir"] = "L"
    ev_s = [_flip_event(e) for e in ev_s]
    for e in ev_s:
        e["dir"] = "S"
    events = sorted(ev_l + ev_s, key=lambda e: (e["i"], e["dir"]))
    trades = [dict(tr, dir="L") for tr in tr_l] + [dict(tr, dir="S") for tr in tr_s]

    demand = [(z.bot, z.top, z.tests) for z in st_l["own"]]
    supply = [(z.bot, z.top, z.tests) for z in st_l["opp"]]

    def watch_info(W, flip):
        if W["state"] != 1:
            return None
        lvl = W["ref_h"]
        z = W["zone"]
        zb, zt = (-z.top, -z.bot) if flip else (z.bot, z.top)
        return dict(level=None if np.isnan(lvl) else (-lvl if flip else lvl), zbot=zb, ztop=zt)

    def levels_info(lvls, flip):
        return [dict(lvl=-x["lvl"] if flip else x["lvl"], name=x["name"], state=x["state"]) for x in lvls]

    C = df["close"].values
    snap = dict(
        last_time=df.index[-1], last_close=float(C[-1]),
        chg_1h=float(C[-1] / C[-5] - 1) if len(C) > 5 else 0.0,
        bias=int(bias[-1]), in_kz=in_killzone(df.index[-1] + BAR, s), demand=demand, supply=supply,
        long=watch_info(st_l["watch"], False), short=watch_info(st_s["watch"], True),
        armed=[dict(dir="L", entry=e["entry"]) for e in st_l["armed"]] +
              [dict(dir="S", entry=-e["entry"]) for e in st_s["armed"]],
        levels_long=levels_info(st_l["levels"], False), levels_short=levels_info(st_s["levels"], True),
        open_trades=len(st_l["open_trades"]) + len(st_s["open_trades"]))
    return events, trades, snap


# =====================================================================
# Grafik resmi (Telegram'a gönderilir)
# =====================================================================
def render_chart(name, cfg, df, ev, title):
    """Olayın olduğu yerin 15dk grafiğini çizer, PNG baytlarını döndürür."""
    import io
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle

    d = cfg["digits"]
    i = ev["i"]
    a = max(0, i - 72)
    b = min(len(df), i + 25)
    sub = df.iloc[a:b]
    n = len(sub)
    O, H, L, C = (sub[c].values for c in ("open", "high", "low", "close"))
    UP, DN, INK, MUTED = "#1a9e77", "#d6455d", "#1f2430", "#6b7280"
    BLUE, GOLD, PUR = "#3b5bdb", "#f2b705", "#7048e8"
    long = ev["dir"] == "L"

    fig, ax = plt.subplots(figsize=(12, 6.75), dpi=100)
    fig.patch.set_facecolor("white")
    x_ev = i - a
    right = n + 34
    labels = []

    def hline(y, x0, color, style, label, bold=False):
        if y is None or (isinstance(y, float) and np.isnan(y)):
            return
        ax.plot([x0, n - 0.5], [y, y], color=color, lw=1.5, ls=style, zorder=2)
        labels.append([y, y, f"{label} {fmt(y, d)}", color, bold])

    # Bölge kutusu
    if ev.get("ztop") is not None and ev.get("zbot") is not None:
        zcol = UP if long else DN
        ax.add_patch(Rectangle((-0.5, ev["zbot"]), n, ev["ztop"] - ev["zbot"], color=zcol, alpha=0.13, lw=0, zorder=1))
        ax.text(0.5, ev["ztop"] if long else ev["zbot"], " TALEP BÖLGESİ (1s)" if long else " ARZ BÖLGESİ (1s)",
                color=zcol, fontsize=10, fontweight="bold", va="bottom" if long else "top", zorder=6,
                bbox=dict(boxstyle="round,pad=0.2", fc="white", ec="none", alpha=0.85))

    # FVG
    if ev["kind"] == "SETUP" and ev.get("fvg") and i >= 2:
        if long:
            lo, hi = df["high"].values[i - 2], df["low"].values[i]
        else:
            lo, hi = df["high"].values[i], df["low"].values[i - 2]
        ax.add_patch(Rectangle((x_ev - 1.5, lo), n - x_ev + 1, hi - lo, color=GOLD, alpha=0.28, lw=0, zorder=1))

    # Mumlar
    for k in range(n):
        col = UP if C[k] >= O[k] else DN
        ax.plot([k, k], [L[k], H[k]], color=col, lw=1.1, zorder=3)
        ax.add_patch(Rectangle((k - 0.32, min(O[k], C[k])), 0.64, max(abs(C[k] - O[k]), 1e-9), color=col, zorder=4))

    # Olay mumu işareti
    ax.axvline(x_ev, color=MUTED, lw=0.8, ls=":", zorder=1)

    YEL = "#c99a00"
    if ev.get("lvl") is not None:
        nm = ev.get("name", "")
        lname = ("Dünkü tepe" if long else "Dünkü dip") if nm == "Dünkü" else f"{nm} {'tepesi' if long else 'dibi'}"
        hline(ev["lvl"], 0, YEL, "-", lname, bold=True)
    if ev["kind"] == "WATCH":
        hline(ev.get("ref_h"), max(0, x_ev - 12), BLUE, "--", "Kırılırsa ALIŞ" if long else "Kırılırsa SATIŞ", bold=True)
        hline(ev.get("ref_l"), max(0, x_ev - 12), PUR, ":", "Likidite")
        hline(ev.get("invalid"), x_ev, DN, "-", "Altında kapanırsa vazgeç" if long else "Üstünde kapanırsa vazgeç")
    elif ev["kind"] == "BREAK":
        hline(ev.get("invalid"), x_ev, DN, "-", "Altında kapanırsa vazgeç" if long else "Üstünde kapanırsa vazgeç")
        ax.annotate("Buraya geri gelip dönerse " + ("ALIŞ" if long else "SATIŞ"),
                    xy=(n - 2, ev["lvl"]), xytext=(n * 0.62, ev["lvl"] + (-1 if long else 1) * (H.max() - L.min()) * 0.18),
                    ha="center", fontsize=10.5, color=INK, fontweight="bold", zorder=7,
                    bbox=dict(boxstyle="round,pad=0.3", fc="white", ec="#d0d4dc"),
                    arrowprops=dict(arrowstyle="-|>", color=INK, lw=1.1))
    else:
        if ev.get("setup") != "RETEST":
            hline(ev.get("ref_h"), max(0, x_ev - 12), BLUE, "--", "Kırılan seviye")
            if ev.get("swept"):
                hline(ev.get("ref_l"), max(0, x_ev - 16), PUR, ":", "Alınan likidite")
        hline(ev["entry"], x_ev, INK, "-", "GİRİŞ", bold=True)
        hline(ev["stop"], x_ev, DN, "-", "STOP", bold=True)
        hline(ev["tp1"], x_ev, UP, "--", "TP1 1:2", bold=True)
        hline(ev["tp2"], x_ev, UP, "-", "TP2 1:3", bold=True)

    # Eksenler
    ys = [L.min(), H.max()]
    for k in ("ztop", "zbot", "lvl", "tp2", "stop", "ref_h", "ref_l", "invalid"):
        v = ev.get(k)
        if v is not None and not (isinstance(v, float) and np.isnan(v)):
            ys.append(v)
    pad = (max(ys) - min(ys)) * 0.06
    ax.set_ylim(min(ys) - pad, max(ys) + pad)
    # Etiketler üst üste binmesin
    gap = (max(ys) - min(ys) + 2 * pad) * 0.045
    labels.sort(key=lambda r: r[0])
    for k in range(1, len(labels)):
        if labels[k][1] - labels[k - 1][1] < gap:
            labels[k][1] = labels[k - 1][1] + gap
    for y, ly, txt, col, bold in labels:
        ax.text(n + 0.4, ly, txt, color=col, fontsize=10, va="center", ha="left",
                fontweight="bold" if bold else "normal")
        if abs(ly - y) > 1e-12:
            ax.plot([n - 0.5, n + 0.3], [y, ly], color=col, lw=0.8)
    ax.set_xlim(-1, right)
    ticks = list(range(0, n, 12))
    ax.set_xticks(ticks)
    ax.set_xticklabels([sub.index[k].tz_convert(TR).strftime("%d.%m\n%H:%M") for k in ticks], fontsize=8.5, color=MUTED)
    ax.tick_params(axis="y", labelsize=9, colors=MUTED)
    ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: fmt(v, d)))
    for sp in ("top", "right"):
        ax.spines[sp].set_visible(False)
    for sp in ("left", "bottom"):
        ax.spines[sp].set_color("#d0d4dc")
    ax.set_title(title, loc="left", fontsize=14, fontweight="bold", color=INK)
    ax.text(0, -0.13, "15 dakikalık grafik · saatler Türkiye saati · veri: Yahoo/Coinbase (broker fiyatından biraz farklı olabilir)",
            transform=ax.transAxes, fontsize=8.5, color=MUTED)
    fig.subplots_adjust(left=0.08, right=0.99, top=0.92, bottom=0.14)
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=100)
    plt.close(fig)
    return buf.getvalue()


def tg_send_photo(png: bytes, caption: str) -> bool:
    token = os.environ.get("TELEGRAM_TOKEN", "").strip()
    chat = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat:
        path = f"grafik_{int(time.time() * 1000)}.png"
        with open(path, "wb") as f:
            f.write(png)
        print(f"---- (Telegram ayarı yok, grafik {path} dosyasına yazıldı) ----")
        return False
    boundary = "----firsat" + str(int(time.time() * 1000))
    parts = []
    for k, v in (("chat_id", chat), ("caption", caption[:1000]), ("parse_mode", "HTML")):
        parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode())
    parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"photo\"; filename=\"grafik.png\"\r\n"
                 f"Content-Type: image/png\r\n\r\n".encode() + png + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    body = b"".join(parts)
    req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendPhoto", data=body,
                                 headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                if json.loads(r.read().decode()).get("ok", False):
                    return True
        except Exception as ex:  # noqa
            print("Telegram foto hatası:", ex)
            time.sleep(2)
    return False


def send_chart(name, cfg, df, ev):
    """Grafik gönderimi hiçbir zaman ana mesajı engellemez."""
    if not SEND_CHARTS or df is None:
        return
    try:
        side = "ALIŞ" if ev["dir"] == "L" else "SATIŞ"
        if ev["kind"] in ("WATCH", "BREAK"):
            title = f"{name} | Takipte: {side} fırsatı olabilir"
        else:
            kind = "kırılım + geri test" if ev.get("setup") == "RETEST" else "bölgeden dönüş"
            title = f"{name} | {side} FIRSATI ({kind}) — Kalite {ev.get('grade', '')}"
        png = render_chart(name, cfg, df, ev, title)
        tg_send_photo(png, f"<b>{title}</b>")
    except Exception:  # noqa
        traceback.print_exc()


# =====================================================================
# Veri
# =====================================================================
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")


def _http_json(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.loads(r.read().decode())


def _yahoo_direct(ticker: str) -> pd.DataFrame:
    """Yahoo grafik API'si (yfinance olmadan, doğrudan)."""
    last = None
    for host in ("query1", "query2"):
        try:
            url = (f"https://{host}.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(ticker)}"
                   f"?interval=15m&range=59d&includePrePost=false")
            js = _http_json(url)
            res = js["chart"]["result"][0]
            q = res["indicators"]["quote"][0]
            idx = pd.to_datetime(res["timestamp"], unit="s", utc=True)
            return pd.DataFrame({"open": q["open"], "high": q["high"], "low": q["low"], "close": q["close"]}, index=idx)
        except Exception as ex:  # noqa
            last = ex
    raise RuntimeError(f"yahoo: {last}")


def _yfinance(ticker: str) -> pd.DataFrame:
    import yfinance as yf
    df = yf.Ticker(ticker).history(period="59d", interval="15m", auto_adjust=False)
    if df is None or df.empty:
        raise RuntimeError("yfinance: boş veri")
    return df.rename(columns=str.lower)[["open", "high", "low", "close"]]


def _coinbase_btc() -> pd.DataFrame:
    """BTC için yedek kaynak: Coinbase 15dk mumlar (300'lük parçalar)."""
    frames = []
    end = pd.Timestamp.now(tz="UTC").floor("15min")
    for _ in range(20):  # 20 x 300 x 15dk ~ 62 gün
        start = end - pd.Timedelta(minutes=15 * 300)
        url = ("https://api.exchange.coinbase.com/products/BTC-USD/candles?granularity=900"
               f"&start={start.strftime('%Y-%m-%dT%H:%M:%SZ')}&end={end.strftime('%Y-%m-%dT%H:%M:%SZ')}")
        rows = _http_json(url)
        if not rows:
            break
        df = pd.DataFrame(rows, columns=["t", "low", "high", "open", "close", "vol"])
        df.index = pd.to_datetime(df["t"], unit="s", utc=True)
        frames.append(df[["open", "high", "low", "close"]])
        end = start
        time.sleep(0.35)
    if not frames:
        raise RuntimeError("coinbase: boş veri")
    return pd.concat(frames)


def _clean(df: pd.DataFrame) -> pd.DataFrame:
    df = df.astype(float).dropna()
    df.index = pd.to_datetime(df.index)
    df.index = df.index.tz_localize("UTC") if df.index.tz is None else df.index.tz_convert("UTC")
    df.index = df.index.as_unit("ns")
    df = df[~df.index.duplicated(keep="last")].sort_index()
    df = df[(df["high"] >= df["low"]) & (df["close"] > 0)]
    now = pd.Timestamp.now(tz="UTC")
    df = df[df.index + BAR <= now]   # sadece kapanmış mumlar
    if len(df) < 300:
        raise RuntimeError(f"çok az mum ({len(df)})")
    return df


def fetch(ticker: str) -> pd.DataFrame:
    sources = [("yahoo", lambda: _yahoo_direct(ticker)), ("yfinance", lambda: _yfinance(ticker))]
    if ticker == "BTC-USD":
        sources.insert(1, ("coinbase", _coinbase_btc))
    errors = []
    for attempt in range(2):
        for name, fn in sources:
            try:
                df = _clean(fn())
                print(f"  {ticker}: veri kaynağı = {name}")
                return df
            except Exception as ex:  # noqa
                errors.append(f"{name}: {type(ex).__name__}: {str(ex)[:120]}")
        time.sleep(4)
    raise RuntimeError(" | ".join(errors[-3:]))




# =====================================================================
# Telegram
# =====================================================================
def _tg_post(token, chat, text):
    data = urllib.parse.urlencode({"chat_id": chat, "text": text, "parse_mode": "HTML",
                                   "disable_web_page_preview": "true"}).encode()
    for attempt in range(3):
        try:
            with urllib.request.urlopen(f"https://api.telegram.org/bot{token}/sendMessage", data=data, timeout=20) as r:
                if json.loads(r.read().decode()).get("ok", False):
                    return True
        except Exception as ex:  # noqa
            print("Telegram hatası:", ex)
            time.sleep(2)
    return False


def tg_send(text: str) -> bool:
    token = os.environ.get("TELEGRAM_TOKEN", "").strip()
    chat = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat:
        print("---- (Telegram ayarı yok, mesaj ekrana yazıldı) ----\n" + text + "\n")
        return False
    # Telegram 4096 karakter sınırı: paragraflardan bölerek gönder
    chunks, cur = [], ""
    for para in text.split("\n\n"):
        if len(cur) + len(para) + 2 > 3800 and cur:
            chunks.append(cur)
            cur = para
        else:
            cur = f"{cur}\n\n{para}" if cur else para
    if cur:
        chunks.append(cur)
    ok = True
    for c in chunks:
        ok = _tg_post(token, chat, c) and ok
    return ok


# =====================================================================
# Mesajlar
# =====================================================================
def W_(d):
    """Yöne göre kelimeler."""
    if d == "L":
        return dict(side="ALIŞ", zone="talep", opp="arz", move="yükselişin", beyond="üstünde",
                    sweep="altındaki dip", fail="altına", mss_word="üstünde")
    return dict(side="SATIŞ", zone="arz", opp="talep", move="düşüşün", beyond="altında",
                sweep="üstündeki tepe", fail="üstüne", mss_word="altında")


def _ok(x):
    return x is not None and not (isinstance(x, float) and np.isnan(x))


def killzone_tr_text(s=None):
    s = s or SETTINGS
    today = dt.datetime.now(NY).date()
    parts = []
    for kz in s["killzones"]:
        ha, ma = map(int, kz[0].split(":"))
        hb, mb = map(int, kz[1].split(":"))
        sa = dt.datetime(today.year, today.month, today.day, ha, ma, tzinfo=NY).astimezone(TR)
        sb = dt.datetime(today.year, today.month, today.day, hb, mb, tzinfo=NY).astimezone(TR)
        name = kz[2] if len(kz) > 2 else "Seans"
        parts.append(f"{name} {sa:%H:%M}–{sb:%H:%M}")
    return " · ".join(parts) + " (TR)"


def msg_watch(name, cfg, ev, use_kz):
    d, w = cfg["digits"], W_(ev["dir"])
    lines = [
        f"<b>{name} | Takipte: {w['side']} fırsatı olabilir</b>",
        f"Fiyat 1 saatlik {w['zone']} bölgesine girdi: {fmt(ev['zbot'], d)} – {fmt(ev['ztop'], d)} "
        f"(bölgenin {ev['test_no']}. testi). Bu bölge {tr_time(ev['born'])} civarında güçlü bir "
        f"{w['move']} başladığı yer.",
        "",
    ]
    if _ok(ev["ref_h"]):
        lines.append(f"<b>Beklediğim:</b> 15dk'lık bir mumun {fmt(ev['ref_h'], d)} {w['mss_word']} güçlü kapanması. "
                     f"Olursa {w['side']} fırsatını giriş, stop ve hedefleriyle gönderirim.")
    else:
        lines.append(f"<b>Beklediğim:</b> fiyatın ilk tepkide yaptığı tepenin {w['mss_word']} güçlü kapanması. "
                     f"Olursa {w['side']} fırsatını gönderirim.")
    if _ok(ev["ref_l"]):
        lines.append(f"<b>Daha iyi olur:</b> fiyat önce {fmt(ev['ref_l'], d)} likiditesini iğneyle alıp geri "
                     f"dönerse. Bu şart değil, olursa fırsat daha güçlü.")
    else:
        lines.append(f"<b>Daha iyi olur:</b> fiyat son 24 saatin {'dibinin altına' if ev['dir'] == 'L' else 'tepesinin üstüne'} "
                     f"iğne atıp geri dönerse (likidite alınır). Şart değil.")
    lines.append(f"<b>Vazgeçerim:</b> 15dk'lık mum {fmt(ev['invalid'], d)} {w['fail']} kapanırsa ya da "
                 f"{SETTINGS['watch_bars'] * 15 // 60} saat içinde bir şey olmazsa.")
    if use_kz:
        lines.append(f"Sadece seans saatlerinde fırsat veririm: {killzone_tr_text()}.")
    lines.append("\nBu henüz işlem değil, sadece haber veriyorum.")
    return "\n".join(lines)


def approx(x, cfg):
    """Okuması kolay yuvarlanmış fiyat."""
    ax_ = abs(x)
    if ax_ < 10:
        v = f"{x:,.4f}"
    elif ax_ < 1000:
        v = f"{x:,.1f}"
    elif ax_ < 10000:
        v = f"{x:,.0f}"
    else:
        v = f"{round(x / 10) * 10:,.0f}"
    return "~" + v


def lvl_name(name, d):
    if name == "Dünkü":
        return "dünkü tepe" if d == "L" else "dünkü dip"
    return f"{name} tepesi" if d == "L" else f"{name} dibi"


def lvl_acc(name, d):
    """'Londra tepesini' / 'dünkü dibi' gibi belirtme hali."""
    if name == "Dünkü":
        return "dünkü tepeyi" if d == "L" else "dünkü dibi"
    return f"{name} tepesini" if d == "L" else f"{name} dibini"


def msg_break(name, cfg, ev):
    w = W_(ev["dir"])
    up = ev["dir"] == "L"
    ln = lvl_name(ev["name"], ev["dir"])
    lines = [
        f"<b>{name} | Takipte: {w['side']} fırsatı olabilir</b>",
        f"Fiyat {lvl_acc(ev['name'], ev['dir'])} ({approx(ev['lvl'], cfg)}) {'yukarı' if up else 'aşağı'} kırdı.",
        "",
        f"<b>Beklediğim:</b> fiyatın geri gelip bu seviyeyi test etmesi ve buradan "
        f"{'yukarı' if up else 'aşağı'} dönmesi. Olursa {w['side']} fırsatını giriş, stop ve hedefleriyle gönderirim.",
        f"<b>Daha iyi olur:</b> fiyat seviyenin biraz {'altına sarkıp geri üstüne' if up else 'üstüne çıkıp geri altına'} "
        f"dönerse (likidite alınmış olur).",
        f"<b>Vazgeçerim:</b> 15dk'lık mum {approx(ev['invalid'], cfg)} {'altında' if up else 'üstünde'} kapanırsa "
        f"ya da {SETTINGS.get('retest_bars', 32) * 15 // 60} saat içinde geri test gelmezse.",
        "",
        "Bu henüz işlem değil, sadece haber veriyorum.",
    ]
    return "\n".join(lines)


def msg_rfail(name, cfg, ev):
    w = W_(ev["dir"])
    return (f"<b>{name} | {w['side']} fikrinden vazgeçtim</b>\n"
            f"{(lambda x: x[0].upper() + x[1:])(lvl_name(ev['name'], ev['dir']))} ({approx(ev['lvl'], cfg)}): {ev['reason']}")


def msg_setup(name, cfg, ev):
    d, w, u = cfg["digits"], W_(ev["dir"]), cfg.get("unit", "pip")
    up = ev["dir"] == "L"
    dist = ev["risk"] / cfg["pip"]
    bias_word = "yukarı" if up else "aşağı"
    room_txt = "önü açık" if ev["room"] is None else f"karşı bölgeye {ev['room']:.1f}R"
    if ev.get("setup") == "RETEST":
        ln = lvl_name(ev["name"], ev["dir"])
        trend_line = (f"• Yön: 4 saatlik trend {bias_word}." if ev.get("trend") == 1 else
                      f"• Yön: 4 saatlik trend net değil ama {'aşağı' if up else 'yukarı'} da değil.")
        conf = f"seviyeden güçlü bir {'yeşil' if up else 'kırmızı'} mumla {'yukarı' if up else 'aşağı'} döndü."
        if ev["swept"]:
            conf = (f"önce seviyenin {'altına sarkıp' if up else 'üstüne çıkıp'} likiditeyi aldı, sonra güçlü bir "
                    f"{'yeşil' if up else 'kırmızı'} mumla geri {'üstüne' if up else 'altına'} döndü.")
        why = [trend_line,
               f"• Seviye: {ln} ({approx(ev['lvl'], cfg)}) {'yukarı' if up else 'aşağı'} kırıldı, sonra fiyat geri gelip test etti.",
               f"• Onay: {conf}"]
        entry_why = "seviye ile dönüş mumunun ortası"
        stop_why = f"geri testteki en {'düşük' if up else 'yüksek'} noktanın {'altı' if up else 'üstü'}"
        important = ev["name"] in ("Londra", "Dünkü", "New York")
        quality = (f"Kalite {ev['grade']} ({ev['score']}/4): {'likidite alındı' if ev['swept'] else 'likidite alınmadı'} · "
                   f"{'önemli seviye' if important else 'küçük seviye'} · "
                   f"trend {'uyumlu' if ev.get('trend') == 1 else 'nötr'} · {room_txt}")
        title = f"<b>{name} | {w['side']} FIRSATI (kırılım + geri test) — Kalite {ev['grade']}</b>"
    else:
        if ev["swept"] and _ok(ev["ref_l"]):
            conf = (f"önce {fmt(ev['ref_l'], d)} likiditesi iğneyle alındı, sonra fiyat {fmt(ev['ref_h'], d)} "
                    f"{w['mss_word']} güçlü kapandı.")
        else:
            conf = f"fiyat {fmt(ev['ref_h'], d)} {w['mss_word']} güçlü kapandı. Likidite alınmadı."
        why = [f"• Yön: 4 saatlik trend {bias_word}.",
               f"• Bölge: 1 saatlik {w['zone']} bölgesi {fmt(ev['zbot'], d)} – {fmt(ev['ztop'], d)} ({ev['test_no']}. test).",
               f"• Onay: {conf}"]
        entry_why = "güçlü mumun bıraktığı boşluk (FVG)" if ev["fvg"] else "güçlü mumun ortası"
        stop_why = "iğnenin altı" if up else "iğnenin üstü"
        if not ev["swept"]:
            stop_why = "bölgedeki en düşük noktanın altı" if up else "bölgedeki en yüksek noktanın üstü"
        quality = (f"Kalite {ev['grade']} ({ev['score']}/4): {'likidite alındı' if ev['swept'] else 'likidite alınmadı'} · "
                   f"{ev['test_no']}. test · FVG {'var' if ev['fvg'] else 'yok'} · {room_txt}")
        title = f"<b>{name} | {w['side']} FIRSATI (bölgeden dönüş) — Kalite {ev['grade']}</b>"
    lines = [title, f"Mum: {tr_time(ev['time'])} (TR, 15dk)", "", "<b>Neden:</b>"] + why + [
        "",
        "<b>Plan:</b>",
        f"• Giriş (limit): <b>{fmt(ev['entry'], d)}</b> — {entry_why}",
        f"• Stop: <b>{fmt(ev['stop'], d)}</b> ({dist:,.1f} {u}) — {stop_why}",
        f"• TP1 (1:2): {fmt(ev['tp1'], d)} → yarısını kapat, stopu girişe çek",
        f"• TP2 (1:3): {fmt(ev['tp2'], d)}",
    ]
    if ev["opp"] is not None:
        warn = " → TP2'den önce dikkat" if ev["room"] < 3 else ""
        lines.append(f"• Önündeki {w['opp']} bölgesi: {fmt(ev['opp'], d)} ({ev['room']:.1f}R){warn}")
    lines += [
        "",
        quality,
        f"Boşver: fiyat girişe gelmeden TP1'e giderse ya da {SETTINGS['arm_bars'] * 15 // 60} saat "
        f"içinde girişe gelmezse.",
        "",
        "Grafikte kontrol et, haber takvimine bak. Karar senin.",
    ]
    return "\n".join(lines)


def msg_entry(name, cfg, ev):
    d, w = cfg["digits"], W_(ev["dir"])
    return (f"<b>{name} | {w['side']} — GİRİŞ SEVİYESİNE GELDİ</b> (Kalite {ev['grade']})\n"
            f"Limit emrin varsa doldu. Giriş {fmt(ev['entry'], d)} · Stop {fmt(ev['stop'], d)}\n"
            f"TP1 (1:2) {fmt(ev['tp1'], d)} · TP2 (1:3) {fmt(ev['tp2'], d)}")


def msg_cancel(name, cfg, ev):
    w = W_(ev["dir"])
    return (f"<b>{name} | {w['side']} fırsatından vazgeçtim</b>\n{ev['reason']} "
            f"(giriş {fmt(ev['entry'], cfg['digits'])}). Bekleyen emrin varsa sil.")


def msg_unwatch(name, cfg, ev):
    d, w = cfg["digits"], W_(ev["dir"])
    return (f"<b>{name} | {w['side']} fikrinden vazgeçtim</b>\n"
            f"Bölge {fmt(ev['zbot'], d)} – {fmt(ev['ztop'], d)}: {ev['reason']}")


def msg_tp1(name, cfg, ev):
    d, w = cfg["digits"], W_(ev["dir"])
    return (f"<b>{name} | {w['side']} — TP1 geldi (+2R)</b>\n"
            f"Yarısını kapat, stopu girişe ({fmt(ev['entry'], d)}) çek. Kalan hedef TP2 {fmt(ev['tp2'], d)}.")


def msg_tp2(name, cfg, ev):
    w = W_(ev["dir"])
    return f"<b>{name} | {w['side']} — TP2 geldi</b>\nİşlem tamamlandı: toplam +2,5R (yarısı 2R, yarısı 3R)."


def msg_be(name, cfg, ev):
    w = W_(ev["dir"])
    return f"<b>{name} | {w['side']} — kalan yarı girişten kapandı</b>\nİşlem tamamlandı: toplam +1R."


def msg_stop(name, cfg, ev):
    w = W_(ev["dir"])
    return (f"<b>{name} | {w['side']} — stop oldu (−1R)</b>\n"
            f"Kurallara uyduysan bu normal bir kayıp. Sıradaki fırsata.")


MESSAGES = {"WATCH": msg_watch, "UNWATCH": msg_unwatch, "BREAK": msg_break, "RFAIL": msg_rfail,
            "SETUP": msg_setup, "ENTRY": msg_entry,
            "CANCEL": msg_cancel, "TP1": msg_tp1, "TP2": msg_tp2, "BE": msg_be, "STOP": msg_stop}


def symbol_summary(name, cfg, snap):
    """Kısa: bu paritede şu an ne bekleniyor."""
    head = f"<b>{name}</b> — "
    if pd.Timestamp.now(tz="UTC") - snap["last_time"] > pd.Timedelta("2h"):
        return head + "piyasa kapalı."
    for a in snap["armed"]:
        w = W_(a["dir"])
        return head + f"<b>fırsat açık:</b> {w['side']}, {approx(a['entry'], cfg)} girişine gelmesini bekliyorum."
    for d, lvls in (("L", snap["levels_long"]), ("S", snap["levels_short"])):
        w = W_(d)
        for lv in lvls:
            if lv["state"] == "retest":
                return head + (f"<b>beklediğim yerde:</b> {lvl_name(lv['name'], d)} ({approx(lv['lvl'], cfg)}) geri test ediliyor, "
                               f"{'yukarı' if d == 'L' else 'aşağı'} dönerse {w['side']}.")
    for d, info in (("L", snap["long"]), ("S", snap["short"])):
        if info:
            w = W_(d)
            return head + (f"<b>beklediğim yerde:</b> fiyat {w['zone']} bölgesinde ({approx(info['zbot'], cfg)} – "
                           f"{approx(info['ztop'], cfg)}), {'yukarı' if d == 'L' else 'aşağı'} kırılım gelirse {w['side']}.")
    for d, lvls in (("L", snap["levels_long"]), ("S", snap["levels_short"])):
        w = W_(d)
        for lv in lvls:
            if lv["state"] == "broken":
                return head + (f"<b>takipte:</b> {lvl_name(lv['name'], d)} ({approx(lv['lvl'], cfg)}) kırıldı, "
                               f"geri gelip test etmesini bekliyorum ({w['side']}).")
    return head + "taktiğimize uygun bir şey yok, bekliyorum."


# =====================================================================
# Durum dosyası
# =====================================================================
def load_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            st = json.load(f)
    except Exception:  # noqa
        st = {}
    st.setdefault("sent", {})
    st.setdefault("last_brief", "")
    st.setdefault("errors", {})
    return st


def save_state(st):
    cutoff = (pd.Timestamp.now(tz="UTC") - pd.Timedelta("3D")).isoformat()
    st["sent"] = {k: v for k, v in st["sent"].items() if v >= cutoff}
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=1, sort_keys=True)


# =====================================================================
# Ana akış
# =====================================================================
def use_kz_for(cfg, s):
    return s["use_killzone"] and cfg.get("use_killzone", True)


def analyze_all():
    out = {}
    for name, cfg in SYMBOLS.items():
        try:
            df = fetch(cfg["ticker"])
            events, trades, snap = run_strategy(df, SETTINGS, use_kz_for(cfg, SETTINGS))
            out[name] = dict(ok=True, df=df, events=events, trades=trades, snap=snap)
            print(f"{name}: {len(df)} mum, son mum {df.index[-1]}, {len(events)} olay")
        except Exception as ex:  # noqa
            traceback.print_exc()
            out[name] = dict(ok=False, error=str(ex))
    return out


def _enabled(kind):
    if kind in ("WATCH", "BREAK"):
        return SEND_WATCH_ALERTS
    if kind in ("UNWATCH", "RFAIL"):
        return SEND_GIVEUP_ALERTS
    if kind == "CANCEL":
        return SEND_CANCEL_ALERTS
    if kind in ("TP1", "TP2", "BE", "STOP"):
        return SEND_RESULT_ALERTS
    return True


def do_scan(state, results):
    now = pd.Timestamp.now(tz="UTC")
    since = now - pd.Timedelta(hours=ALERT_LOOKBACK_HOURS)
    for name, res in results.items():
        cfg = SYMBOLS[name]
        if not res["ok"]:
            day = now.strftime("%Y-%m-%d")
            if state["errors"].get(name) != day:
                tg_send(f"<b>Uyarı:</b> {name} verisi alınamadı. Bugün bu enstrüman taranamayabilir.\n"
                        f"{html.escape(res['error'][:300])}")
                state["errors"][name] = day
            continue
        for ev in res["events"]:
            if ev["time"] + BAR < since or not _enabled(ev["kind"]):
                continue
            key = f"{name}|{ev['kind']}|{ev['dir']}|{ev['time'].isoformat()}"
            if key in state["sent"]:
                continue
            if ev["kind"] in ("WATCH", "BREAK") and WATCH_ONLY_IN_KILLZONE and use_kz_for(cfg, SETTINGS) and not ev["kz_now"]:
                continue
            if ev["kind"] in ("UNWATCH", "RFAIL"):
                src = "WATCH" if ev["kind"] == "UNWATCH" else "BREAK"
                wkey = f"{name}|{src}|{ev['dir']}|{ev['watch_time'].isoformat()}"
                if wkey not in state["sent"]:
                    continue   # izleme mesajı gönderilmediyse bitiş mesajı da gönderilmez
            if ev["kind"] == "WATCH":
                text = msg_watch(name, cfg, ev, use_kz_for(cfg, SETTINGS))
            else:
                text = MESSAGES[ev["kind"]](name, cfg, ev)
            if ev["kind"] in ("WATCH", "BREAK", "SETUP"):
                send_chart(name, cfg, res["df"], ev)
            if tg_send(text) or not os.environ.get("TELEGRAM_TOKEN"):
                state["sent"][key] = now.isoformat()


def do_brief(results, title="Günlük özet"):
    now = dt.datetime.now(TR).strftime("%d.%m %H:%M")
    lines = [f"<b>{title}</b> ({now})"]
    for name, res in results.items():
        if res["ok"]:
            lines.append(symbol_summary(name, SYMBOLS[name], res["snap"]))
        else:
            lines.append(f"<b>{name}</b> — veri alınamadı.")
    tg_send("\n".join(lines))


def _stats(vals):
    v = [x for x in vals if x is not None]
    if not v:
        return "işlem yok"
    w = sum(1 for x in v if x > 0)
    return f"{len(v)} işlem, %{w / len(v) * 100:.0f} kazanç, {sum(v):+.1f}R, işlem başı {np.mean(v):+.2f}R"


def do_report(results):
    ok = {n: r for n, r in results.items() if r["ok"]}
    if not ok:
        tg_send("<b>Rapor:</b> veri alınamadı.")
        return
    weeks = max(1e-9, np.mean([(r["df"].index[-1] - r["df"].index[0]).days / 7 for r in ok.values()]))

    def line(trades, setups):
        v = [t["managed"] for t in trades if t["managed"] is not None]
        if not v:
            return f"{setups} fırsat (haftada ~{setups / weeks:.1f}), sonuçlanan işlem yok"
        w = sum(1 for x in v if x > 0)
        return (f"{setups} fırsat (haftada ~{setups / weeks:.1f}) · {len(v)} işlem · "
                f"%{w / len(v) * 100:.0f} kazanç · toplam {sum(v):+.1f}R")

    out = [f"<b>Geçmiş test</b> (son ~{weeks:.0f} hafta)"]
    for vname, over in VARIANTS.items():
        s = dict(SETTINGS, **over)
        setups, trades, per = 0, [], []
        for name, r in ok.items():
            ev, tr = (r["events"], r["trades"]) if not over else run_strategy(r["df"], s, use_kz_for(SYMBOLS[name], s))[:2]
            n_set = sum(1 for e in ev if e["kind"] == "SETUP")
            setups += n_set
            trades += tr
            per.append((name, n_set, tr))
        out.append(f"<b>{vname}:</b> {line(trades, setups)}")
        if not over:
            for name, n_set, tr in per:
                out.append(f"   {name}: {line(tr, n_set)}")
    out.append("R = risk ettiğin miktar. +1R = riski kadar kazanç. Az işlemle sonuç kesin değil.")
    tg_send("\n".join(out))


def main():
    mode = (sys.argv[1] if len(sys.argv) > 1 else os.environ.get("MODE", "scan")).strip().lower() or "scan"
    print("Mod:", mode)
    state = load_state()
    results = analyze_all()

    if mode == "test":
        tg_send("<b>Fırsat tarayıcı çalışıyor (sürüm 3).</b>\nBu bir test mesajıdır. Aşağıda şu anki durum var.")
        do_brief(results, title="Anlık durum")
        # Grafik özelliğini göstermek için geçmişten son kurulum örneği
        last = None
        for name, res in results.items():
            if res["ok"]:
                for ev in res["events"]:
                    if ev["kind"] == "SETUP" and (last is None or ev["time"] > last[2]["time"]):
                        last = (name, res, ev)
        if last:
            name, res, ev = last
            tg_send("<b>Örnek:</b> geçmişteki son fırsat aşağıda. Gerçek bir fırsat geldiğinde mesaj ve grafik böyle görünecek.")
            send_chart(name, SYMBOLS[name], res["df"], ev)
            tg_send(msg_setup(name, SYMBOLS[name], ev))
    elif mode == "brief":
        do_brief(results)
    elif mode == "rapor":
        do_report(results)
    else:
        do_scan(state, results)
        now_tr = dt.datetime.now(TR)
        today = now_tr.strftime("%Y-%m-%d")
        hour_key = now_tr.strftime("%Y-%m-%d %H")
        if HOURLY_STATUS and now_tr.hour in HOURLY_HOURS_TR:
            if state.get("last_hourly") != hour_key:
                first = state["last_brief"] != today
                do_brief(results, title="Günaydın, günün durumu" if first else "Saatlik durum")
                state["last_hourly"] = hour_key
                state["last_brief"] = today
        elif now_tr.hour >= DAILY_BRIEF_TR_HOUR and state["last_brief"] != today:
            do_brief(results)
            state["last_brief"] = today

    save_state(state)


if __name__ == "__main__":
    main()
