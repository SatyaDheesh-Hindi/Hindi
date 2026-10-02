"""Hindi quality test (READ-ONLY on both databases).

Writes Hindi for a fixed panel of real articles with the current prompt + model and
records gates / quality signals, next to what is currently shipped (translation DB).

    python quality/run_quality.py --shard 0/3 --out qout/0.json [--ids 1,2,3]
"""
import argparse, json, os, sys, time, zlib

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import hindi_core as core            # noqa: E402
import hindi_pipeline as pipe        # noqa: E402  (reuses its DB connection helpers)


def dec(b):
    if b is None:
        return ""
    if isinstance(b, (bytes, bytearray, memoryview)):
        try:
            return zlib.decompress(bytes(b)).decode("utf-8")
        except Exception:
            return bytes(b).decode("utf-8", "ignore")
    return str(b)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ids", default="")
    ap.add_argument("--shard", default="0/1")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    ids = [int(x) for x in a.ids.split(",") if x.strip()] or json.load(open(os.path.join(HERE, "panel.json")))
    k, n = map(int, a.shard.split("/"))
    ids = ids[k::n]

    ca = pipe.get_db_connection().cursor()
    ph = ",".join("?" * len(ids))
    ca.execute(f"SELECT id, COALESCE(NULLIF(rephrased_title,''), title), rephrased_article, category FROM articles WHERE id IN ({ph})", ids)
    arts = {r[0]: r for r in ca.fetchall()}
    current = {}
    try:
        cb = pipe.get_translation_db_connection().cursor()
        cb.execute(f"SELECT article_id, rephrased_title_hi, rephrased_article_hi FROM translations WHERE article_id IN ({ph})", ids)
        current = {r[0]: {"headline": r[1] or "", "body": dec(r[2])} for r in cb.fetchall()}
    except Exception as e:
        print("no current translations readable:", e)

    t = core.Translator()
    results = []
    for i in ids:
        if i not in arts:
            continue
        _, title, blob, cat = arts[i]
        en = dec(blob)
        t0 = time.time()
        try:
            out = t.write_article(title, en)
            err = None
            if out.get("headline") and core.script_gate(out["headline"])[0]:
                h, how = t.verified_headline(title, out["headline"], out["body"])  # same check as the service
                out["headline_check"] = how
                if how != "ok":
                    out["headline_before_check"], out["headline"] = out["headline"], h
        except Exception as e:
            out, err = {"headline": "", "body": "", "attempts": 0, "missing_numbers": []}, str(e)[:300]
        secs = round(time.time() - t0, 1)
        ok, reasons = core.verify(en, out["body"], is_gemma=True)
        results.append({
            "id": i, "category": cat, "en_title": title, "en_body": en,
            "new": out, "current": current.get(i), "seconds": secs, "error": err,
            "gates_ok": ok and not err, "gates": reasons,
            "signals": core.quality_signals(en, out["body"]),
            "current_signals": core.quality_signals(en, current[i]["body"]) if i in current else None,
        })
        print(f"[{len(results)}/{len(ids)}] {i} {secs}s gates={'ok' if ok else reasons} | {out['headline']}", flush=True)

    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    json.dump({"prompt_version": core.PROMPT_VERSION, "model": t.model_name, "results": results},
              open(a.out, "w"), ensure_ascii=False)


if __name__ == "__main__":
    main()
