#!/usr/bin/env python3
"""
S&D + ICT Fırsat Tarayıcı
=========================
Strateji (5 kapı):
  1) 4 saatlik yön (son kırılan salınım)
  2) 1 saatlik taze arz/talep bölgesi (küçük baz mum + güçlü çıkış)
  3) Killzone (Londra 02:00-05:00, NY AM 09:30-11:00, New York saati)
  4) 15 dakikalıkta likidite süpürmesi + yapı kırılımı (MSS)
  5) FVG'ye / gövdeye geri çekilmede giriş, karşı bölgeye en az 1:2 yer

Çalışma modları (MODE ortam değişkeni ya da ilk argüman):
  scan   : normal tarama (GitHub her 5 dakikada bunu çalıştırır)
  test   : Telegram'a test mesajı + şu anki durum özeti gönderir
  brief  : günlük özeti hemen gönderir
  rapor  : son ~59 günün geçmiş testini (backtest) hesaplayıp gönderir
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
    # ad       : Yahoo Finance kodu, fiyat basamağı, pip büyüklüğü
    "EURUSD": {"ticker": "EURUSD=X", "digits": 5, "pip": 0.0001, "unit": "pip"},
    "GBPUSD": {"ticker": "GBPUSD=X", "digits": 5, "pip": 0.0001, "unit": "pip"},
    "XAUUSD": {"ticker": "GC=F",     "digits": 2, "pip": 1.0, "unit": "$"},    # Altın vadeli (spot'a çok yakın)
    "US100":  {"ticker": "NQ=F",     "digits": 2, "pip": 1.0, "unit": "puan"},    # Nasdaq 100 vadeli
    "BTCUSD": {"ticker": "BTC-USD",  "digits": 2, "pip": 1.0, "unit": "$"},
}

SETTINGS = {
    "bias_tf": "4h",          # yön zaman dilimi
    "zone_tf": "1h",          # bölge zaman dilimi
    "use_bias": True,         # 4s yöne ters işlem arama
    "disp_mult": 1.2,         # bölge çıkış mumu gövdesi >= ATR x bu
    "base_mult": 0.5,         # baz mum gövdesi <= ATR x bu
    "max_zones": 6,           # taraf başına en fazla aktif bölge
    "use_killzone": True,     # sadece killzone içinde kurulum
    "killzones": [("02:00", "05:00"), ("09:30", "11:00")],  # New York saati
    "require_sweep": True,    # likidite süpürmesi şart
    "mss_mult": 0.8,          # MSS mumu gövdesi >= 15dk ATR x bu
    "piv_len": 3,             # 15dk salınım uzunluğu
    "htf_piv_len": 3,         # 4s salınım uzunluğu
    "watch_bars": 24,         # bölgeye dokunduktan sonra MSS bekleme (15dk mum)
    "arm_bars": 16,           # kurulumdan sonra girişe gelme bekleme (15dk mum)
    "buf_mult": 0.5,          # stop tamponu = 15dk ATR x bu
    "min_rr": 2.0,            # karşı bölgeye en az R:R
}

ALERT_LOOKBACK_HOURS = 3      # son kaç saatteki olaylar bildirilsin (gecikmelere karşı)
DAILY_BRIEF_TR_HOUR = 8       # günlük özet saati (Türkiye saati)
SEND_CANCEL_ALERTS = True     # iptal olan kurulumları da bildir
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
    """Onaylanmış salınımlar: ph[i] = i-p'deki tepe (i'de onaylandı), yoksa nan."""
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


# =====================================================================
# Strateji motoru
# =====================================================================
@dataclass
class Zone:
    kind: str       # "D" talep, "S" arz
    top: float
    bot: float
    born: pd.Timestamp
    tests: int = 0


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
    # Sadece KAPANMIŞ 4s mumlar: 4s mumun bitiş zamanı <= 15dk mumun başlangıcı
    ends = pd.DataFrame({"t": h4.index + pd.Timedelta(s["bias_tf"]), "bias": out})
    left = pd.DataFrame({"t": df15.index})
    m = pd.merge_asof(left, ends, on="t", direction="backward")
    return m["bias"].fillna(0).astype(int).values


def zone_events(df15: pd.DataFrame, s: dict):
    h1 = resample(df15, s["zone_tf"])
    a = atr(h1).values
    O, H, L, C = (h1[c].values for c in ("open", "high", "low", "close"))
    step = pd.Timedelta(s["zone_tf"])
    ev = []
    for i in range(1, len(h1)):
        if np.isnan(a[i - 1]) or np.isnan(a[i]):
            continue
        base_ok = abs(C[i - 1] - O[i - 1]) <= s["base_mult"] * a[i - 1]
        if not base_ok:
            continue
        end = h1.index[i] + step
        if C[i] > O[i] and (C[i] - O[i]) >= s["disp_mult"] * a[i] and C[i] > H[i - 1]:
            ev.append((end, "D", max(O[i - 1], C[i - 1]), min(L[i - 1], L[i])))
        if C[i] < O[i] and (O[i] - C[i]) >= s["disp_mult"] * a[i] and C[i] < L[i - 1]:
            ev.append((end, "S", max(H[i - 1], H[i]), min(O[i - 1], C[i - 1])))
    return ev


def in_killzone(ts: pd.Timestamp, s: dict) -> bool:
    t = ts.tz_convert(NY)
    hm = t.hour * 60 + t.minute
    for a, b in s["killzones"]:
        ha, ma = map(int, a.split(":"))
        hb, mb = map(int, b.split(":"))
        if ha * 60 + ma <= hm < hb * 60 + mb:
            return True
    return False


def run_strategy(df: pd.DataFrame, s: dict, use_kz: bool = True):
    """15dk veriyi baştan sona işler. Olay listesi + son durum döndürür."""
    n = len(df)
    t = df.index
    O, H, L, C = (df[c].values for c in ("open", "high", "low", "close"))
    A = atr(df).values
    bias = bias_by_bar(df, s)
    ph, pl = pivots(H, L, s["piv_len"])
    zev = zone_events(df, s)
    zi = 0

    demand, supply = [], []
    last_ph = last_pl = np.nan
    events, trades = [], []

    L_ = dict(state=0)   # alış durumu
    S_ = dict(state=0)   # satış durumu

    def add_zone(arr, z):
        arr.append(z)
        while len(arr) > s["max_zones"]:
            arr.pop(0)

    def opp_room(entry, risk, long):
        if long:
            tops = [z.bot for z in supply if z.bot > entry]
            if not tops:
                return None
            return (min(tops) - entry) / risk
        bots = [z.top for z in demand if z.top < entry]
        if not bots:
            return None
        return (entry - max(bots)) / risk

    for i in range(2, n):
        # 1) Yeni 1s bölgeler (sadece kapanmış 1s mumlardan)
        while zi < len(zev) and zev[zi][0] <= t[i]:
            end, kind, top, bot = zev[zi]
            add_zone(demand if kind == "D" else supply, Zone(kind, top, bot, end))
            zi += 1

        # 2) 15dk salınımlar
        if not np.isnan(ph[i]):
            last_ph = ph[i]
        if not np.isnan(pl[i]):
            last_pl = pl[i]

        atr_i = A[i] if not np.isnan(A[i]) else 0.0
        buf = s["buf_mult"] * atr_i
        kz_ok = (not use_kz) or in_killzone(t[i], s)

        # 3) Talep bölgeleri: geçersiz kılma + ilk test
        for z in list(reversed(demand)):
            if C[i] < z.bot:
                demand.remove(z)
            elif L[i] <= z.top and L[i - 1] > z.top:
                z.tests += 1
                if z.tests == 1 and L_["state"] == 0:
                    L_.update(state=1, sweep=L[i], ref_h=last_ph, ref_l=last_pl, zone=z, start=i)
        # Arz bölgeleri
        for z in list(reversed(supply)):
            if C[i] > z.top:
                supply.remove(z)
            elif H[i] >= z.bot and H[i - 1] < z.bot:
                z.tests += 1
                if z.tests == 1 and S_["state"] == 0:
                    S_.update(state=1, sweep=H[i], ref_h=last_ph, ref_l=last_pl, zone=z, start=i)

        # 4) ALIŞ akışı
        if L_["state"] == 1:
            L_["sweep"] = min(L_["sweep"], L[i])
            if i - L_["start"] > s["watch_bars"] or C[i] < L_["zone"].bot - buf:
                L_["state"] = 0
            else:
                swept = (not s["require_sweep"]) or (not np.isnan(L_["ref_l"]) and L_["sweep"] < L_["ref_l"])
                mss = (not np.isnan(L_["ref_h"])) and C[i] > L_["ref_h"] and (C[i] - O[i]) >= s["mss_mult"] * atr_i
                bias_ok = (not s["use_bias"]) or bias[i] == 1
                if swept and mss and kz_ok and bias_ok:
                    e = L[i] if L[i] > H[i - 2] else (O[i] + C[i]) / 2
                    st = L_["sweep"] - buf
                    r = e - st
                    room = opp_room(e, r, True) if r > 0 else None
                    if r > 0 and (room is None or room >= s["min_rr"]):
                        L_.update(state=2, entry=e, stop=st, risk=r, arm=i)
                        events.append(dict(kind="SETUP", dir="L", i=i, time=t[i], entry=e, stop=st, risk=r,
                                           room=room, ztop=L_["zone"].top, zbot=L_["zone"].bot, fvg=L[i] > H[i - 2]))
                    else:
                        L_["state"] = 0
        elif L_["state"] == 2:
            e, r = L_["entry"], L_["risk"]
            if L[i] <= e:
                events.append(dict(kind="ENTRY", dir="L", i=i, time=t[i], entry=e, stop=L_["stop"], risk=r))
                trades.append(dict(dir="L", i=i, entry=e, stop=L_["stop"], risk=r))
                L_["state"] = 0
            elif H[i] >= e + 2 * r:
                events.append(dict(kind="CANCEL", dir="L", i=i, time=t[i], entry=e, reason="Fiyat girişe gelmeden 1:2 hedefe gitti"))
                L_["state"] = 0
            elif i - L_["arm"] > s["arm_bars"]:
                events.append(dict(kind="CANCEL", dir="L", i=i, time=t[i], entry=e, reason="Süre doldu, giriş seviyesine gelmedi"))
                L_["state"] = 0

        # 5) SATIŞ akışı
        if S_["state"] == 1:
            S_["sweep"] = max(S_["sweep"], H[i])
            if i - S_["start"] > s["watch_bars"] or C[i] > S_["zone"].top + buf:
                S_["state"] = 0
            else:
                swept = (not s["require_sweep"]) or (not np.isnan(S_["ref_h"]) and S_["sweep"] > S_["ref_h"])
                mss = (not np.isnan(S_["ref_l"])) and C[i] < S_["ref_l"] and (O[i] - C[i]) >= s["mss_mult"] * atr_i
                bias_ok = (not s["use_bias"]) or bias[i] == -1
                if swept and mss and kz_ok and bias_ok:
                    e = H[i] if H[i] < L[i - 2] else (O[i] + C[i]) / 2
                    st = S_["sweep"] + buf
                    r = st - e
                    room = opp_room(e, r, False) if r > 0 else None
                    if r > 0 and (room is None or room >= s["min_rr"]):
                        S_.update(state=2, entry=e, stop=st, risk=r, arm=i)
                        events.append(dict(kind="SETUP", dir="S", i=i, time=t[i], entry=e, stop=st, risk=r,
                                           room=room, ztop=S_["zone"].top, zbot=S_["zone"].bot, fvg=H[i] < L[i - 2]))
                    else:
                        S_["state"] = 0
        elif S_["state"] == 2:
            e, r = S_["entry"], S_["risk"]
            if H[i] >= e:
                events.append(dict(kind="ENTRY", dir="S", i=i, time=t[i], entry=e, stop=S_["stop"], risk=r))
                trades.append(dict(dir="S", i=i, entry=e, stop=S_["stop"], risk=r))
                S_["state"] = 0
            elif L[i] <= e - 2 * r:
                events.append(dict(kind="CANCEL", dir="S", i=i, time=t[i], entry=e, reason="Fiyat girişe gelmeden 1:2 hedefe gitti"))
                S_["state"] = 0
            elif i - S_["arm"] > s["arm_bars"]:
                events.append(dict(kind="CANCEL", dir="S", i=i, time=t[i], entry=e, reason="Süre doldu, giriş seviyesine gelmedi"))
                S_["state"] = 0

    snapshot = dict(
        last_time=t[-1], last_close=C[-1], bias=int(bias[-1]),
        in_kz=in_killzone(t[-1] + BAR, s),
        demand=[z for z in demand], supply=[z for z in supply],
        long_state=L_["state"], short_state=S_["state"],
    )
    return events, trades, snapshot


def evaluate_trades(df: pd.DataFrame, trades: list, rr: float):
    """Dolan işlemlerin sonucu. Aynı mumda stop ve hedef varsa stop sayılır (temkinli)."""
    H, L = df["high"].values, df["low"].values
    results = []
    for tr in trades:
        e, st, r, i0 = tr["entry"], tr["stop"], tr["risk"], tr["i"]
        tp = e + rr * r if tr["dir"] == "L" else e - rr * r
        res = None
        for j in range(i0, len(df)):
            if tr["dir"] == "L":
                hit_stop = L[j] <= st
                hit_tp = H[j] >= tp and j > i0
            else:
                hit_stop = H[j] >= st
                hit_tp = L[j] <= tp and j > i0
            if hit_stop:
                res = -1.0
                break
            if hit_tp:
                res = rr
                break
        results.append(res)  # None = hâlâ açık
    return results


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
def tg_send(text: str) -> bool:
    token = os.environ.get("TELEGRAM_TOKEN", "").strip()
    chat = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat:
        print("---- (Telegram ayarı yok, mesaj ekrana yazıldı) ----\n" + text + "\n")
        return False
    data = urllib.parse.urlencode({"chat_id": chat, "text": text, "parse_mode": "HTML",
                                   "disable_web_page_preview": "true"}).encode()
    for attempt in range(3):
        try:
            with urllib.request.urlopen(f"https://api.telegram.org/bot{token}/sendMessage", data=data, timeout=20) as r:
                ok = json.loads(r.read().decode()).get("ok", False)
                if ok:
                    return True
        except Exception as ex:  # noqa
            print("Telegram hatası:", ex)
            time.sleep(2)
    return False


# =====================================================================
# Mesajlar
# =====================================================================
def dir_word(d):
    return "ALIŞ" if d == "L" else "SATIŞ"


def msg_setup(name, cfg, ev):
    d = cfg["digits"]
    e, st, r = ev["entry"], ev["stop"], ev["risk"]
    sign = 1 if ev["dir"] == "L" else -1
    tp1, tp2 = e + sign * 2 * r, e + sign * 3 * r
    pips = r / cfg["pip"]
    room = "karşı bölge yok (açık alan)" if ev["room"] is None else f"{ev['room']:.1f}R"
    zone = "talep" if ev["dir"] == "L" else "arz"
    fvg = "FVG üst kenarı" if ev["fvg"] and ev["dir"] == "L" else ("FVG alt kenarı" if ev["fvg"] else "MSS mumunun ortası")
    return (
        f"<b>{name} | {dir_word(ev['dir'])} KURULUMU</b>\n"
        f"Mum: {tr_time(ev['time'])} (TR, 15dk)\n\n"
        f"Giriş (limit): <b>{fmt(e, d)}</b>  ({fvg})\n"
        f"Stop: <b>{fmt(st, d)}</b>  ({pips:,.1f} {cfg.get('unit', 'pip')})\n"
        f"TP1 (1:2): {fmt(tp1, d)}\n"
        f"TP2 (1:3): {fmt(tp2, d)}\n"
        f"Karşı bölgeye yer: {room}\n\n"
        f"Kapılar: 4s yön {'yukarı' if ev['dir'] == 'L' else 'aşağı'} · taze 1s {zone} bölgesi "
        f"({fmt(ev['zbot'], d)} – {fmt(ev['ztop'], d)}) · killzone · süpürme + MSS\n"
        f"Geçerlilik: {SETTINGS['arm_bars']} mum (~{SETTINGS['arm_bars'] * 15 // 60} saat).\n\n"
        f"Önce grafiğe bak, haber takvimini kontrol et. Karar senin."
    )


def msg_entry(name, cfg, ev):
    d = cfg["digits"]
    e, st, r = ev["entry"], ev["stop"], ev["risk"]
    sign = 1 if ev["dir"] == "L" else -1
    return (
        f"<b>{name} | {dir_word(ev['dir'])} GİRİŞ SEVİYESİNE GELDİ</b>\n"
        f"Mum: {tr_time(ev['time'])} (TR)\n"
        f"Giriş: {fmt(e, d)} · Stop: {fmt(st, d)}\n"
        f"TP1 (1:2): {fmt(e + sign * 2 * r, d)} · TP2 (1:3): {fmt(e + sign * 3 * r, d)}"
    )


def msg_cancel(name, cfg, ev):
    return (f"<b>{name} | {dir_word(ev['dir'])} kurulumu iptal</b>\n"
            f"{ev['reason']} (giriş {fmt(ev['entry'], cfg['digits'])}, {tr_time(ev['time'])} TR)")


def killzone_tr_text():
    today = dt.datetime.now(NY).date()
    parts = []
    names = ["Londra", "NY AM"]
    for (a, b), nm in zip(SETTINGS["killzones"], names):
        ha, ma = map(int, a.split(":"))
        hb, mb = map(int, b.split(":"))
        sa = dt.datetime(today.year, today.month, today.day, ha, ma, tzinfo=NY).astimezone(TR)
        sb = dt.datetime(today.year, today.month, today.day, hb, mb, tzinfo=NY).astimezone(TR)
        parts.append(f"{nm} {sa:%H:%M}–{sb:%H:%M}")
    return " · ".join(parts)


def symbol_summary(name, cfg, snap):
    d = cfg["digits"]
    px = snap["last_close"]
    b = {1: "YUKARI", -1: "AŞAĞI", 0: "BELİRSİZ"}[snap["bias"]]
    below = [z for z in snap["demand"] if z.top < px]
    above = [z for z in snap["supply"] if z.bot > px]
    nd = max(below, key=lambda z: z.top) if below else None
    ns = min(above, key=lambda z: z.bot) if above else None
    lines = [f"<b>{name}</b> {fmt(px, d)} · 4s yön: {b}"]
    if nd:
        lines.append(f"  Talep: {fmt(nd.bot, d)}–{fmt(nd.top, d)} ({(px - nd.top) / px * 100:.2f}% aşağıda{', test edildi' if nd.tests else ', taze'})")
    else:
        lines.append("  Talep: yakın aktif bölge yok")
    if ns:
        lines.append(f"  Arz: {fmt(ns.bot, d)}–{fmt(ns.top, d)} ({(ns.bot - px) / px * 100:.2f}% yukarıda{', test edildi' if ns.tests else ', taze'})")
    else:
        lines.append("  Arz: yakın aktif bölge yok")
    st = []
    if snap["long_state"] == 1: st.append("talep bölgesinde, MSS bekleniyor")
    if snap["long_state"] == 2: st.append("ALIŞ kurulumu aktif, girişe gelmesi bekleniyor")
    if snap["short_state"] == 1: st.append("arz bölgesinde, MSS bekleniyor")
    if snap["short_state"] == 2: st.append("SATIŞ kurulumu aktif, girişe gelmesi bekleniyor")
    if st:
        lines.append("  Durum: " + "; ".join(st))
    return "\n".join(lines)


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
def analyze_all():
    out = {}
    for name, cfg in SYMBOLS.items():
        try:
            df = fetch(cfg["ticker"])
            use_kz = cfg.get("use_killzone", SETTINGS["use_killzone"])
            events, trades, snap = run_strategy(df, SETTINGS, use_kz)
            out[name] = dict(ok=True, df=df, events=events, trades=trades, snap=snap)
            print(f"{name}: {len(df)} mum, son mum {df.index[-1]}, {len(events)} olay")
        except Exception as ex:  # noqa
            traceback.print_exc()
            out[name] = dict(ok=False, error=str(ex))
    return out


def do_scan(state, results):
    now = pd.Timestamp.now(tz="UTC")
    since = now - pd.Timedelta(hours=ALERT_LOOKBACK_HOURS)
    sent_any = False
    for name, res in results.items():
        cfg = SYMBOLS[name]
        if not res["ok"]:
            day = now.strftime("%Y-%m-%d")
            if state["errors"].get(name) != day:
                tg_send(f"<b>Uyarı:</b> {name} verisi alınamadı. Bugün bu enstrüman taranamayabilir.\n{html.escape(res['error'][:300])}")
                state["errors"][name] = day
            continue
        for ev in res["events"]:
            if ev["time"] + BAR < since:
                continue
            if ev["kind"] == "CANCEL" and not SEND_CANCEL_ALERTS:
                continue
            key = f"{name}|{ev['kind']}|{ev['dir']}|{ev['time'].isoformat()}"
            if key in state["sent"]:
                continue
            text = {"SETUP": msg_setup, "ENTRY": msg_entry, "CANCEL": msg_cancel}[ev["kind"]](name, cfg, ev)
            if tg_send(text) or not os.environ.get("TELEGRAM_TOKEN"):
                state["sent"][key] = now.isoformat()
                sent_any = True
    return sent_any


def do_brief(results, title="Günlük özet"):
    today = dt.datetime.now(TR).strftime("%d.%m.%Y")
    parts = [f"<b>{title} — {today}</b>", f"Killzone (TR): {killzone_tr_text()}", ""]
    for name, res in results.items():
        if res["ok"]:
            parts.append(symbol_summary(name, SYMBOLS[name], res["snap"]))
        else:
            parts.append(f"<b>{name}</b>: veri alınamadı ({html.escape(res['error'][:200])})")
        parts.append("")
    parts.append("Bu bir yatırım tavsiyesi değil; kurulumları kendi analizinle teyit et.")
    tg_send("\n".join(parts))


def do_report(results):
    parts = ["<b>Geçmiş test raporu (son ~59 gün, 15dk)</b>",
             "Aynı mumda hem stop hem hedef olursa stop sayıldı. Spread/komisyon dahil değil.", ""]
    tot = {2.0: [], 3.0: []}
    for name, res in results.items():
        if not res["ok"]:
            parts.append(f"<b>{name}</b>: veri alınamadı ({html.escape(res['error'][:200])})\n")
            continue
        ev = res["events"]
        n_setup = sum(1 for e in ev if e["kind"] == "SETUP")
        n_fill = len(res["trades"])
        parts.append(f"<b>{name}</b>: {n_setup} kurulum, {n_fill} doldu")
        for rr in (2.0, 3.0):
            r = [x for x in evaluate_trades(res["df"], res["trades"], rr) if x is not None]
            tot[rr] += r
            if r:
                w = sum(1 for x in r if x > 0)
                parts.append(f"  1:{int(rr)} → {w}/{len(r)} kazanç (%{w / len(r) * 100:.0f}), toplam {sum(r):+.1f}R, işlem başı {np.mean(r):+.2f}R")
            else:
                parts.append(f"  1:{int(rr)} → kapanmış işlem yok")
        parts.append("")
    for rr in (2.0, 3.0):
        r = tot[rr]
        if r:
            w = sum(1 for x in r if x > 0)
            parts.append(f"<b>TOPLAM 1:{int(rr)}</b>: {len(r)} işlem, %{w / len(r) * 100:.0f} kazanç, {sum(r):+.1f}R, işlem başı {np.mean(r):+.2f}R")
    parts.append("\nNot: 59 gün küçük bir örneklem. Sonuçlar kesin değil, yön gösterir.")
    tg_send("\n".join(parts))


def main():
    mode = (sys.argv[1] if len(sys.argv) > 1 else os.environ.get("MODE", "scan")).strip().lower() or "scan"
    print("Mod:", mode)
    state = load_state()
    results = analyze_all()

    if mode == "test":
        tg_send("<b>Fırsat tarayıcı çalışıyor.</b>\nBu bir test mesajıdır. Aşağıda şu anki durum var.")
        do_brief(results, title="Anlık durum")
    elif mode == "brief":
        do_brief(results)
    elif mode == "rapor":
        do_report(results)
    else:
        do_scan(state, results)
        now_tr = dt.datetime.now(TR)
        today = now_tr.strftime("%Y-%m-%d")
        if now_tr.hour >= DAILY_BRIEF_TR_HOUR and state["last_brief"] != today:
            do_brief(results)
            state["last_brief"] = today

    save_state(state)


if __name__ == "__main__":
    main()
