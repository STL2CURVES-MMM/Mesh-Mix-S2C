import json, sys
a = {json.loads(l)["name"]: json.loads(l) for l in open(sys.argv[1])}
b = {json.loads(l)["name"]: json.loads(l) for l in open(sys.argv[2])}
print(f"{'model':30} {'faces':>11} {'valid':>11} {'ok':>11} {'p99/tol':>13} {'max/tol':>13} {'within2':>15} {'time s':>13}")
for n in a:
    if n not in b: continue
    x, y = a[n], b[n]
    def f(k, fmt="{}"):
        return fmt.format(x.get(k)) + "→" + fmt.format(y.get(k))
    r = lambda r, k: (r.get(k) or 0) / max(r.get("tol") or 1, 1e-12)
    same = all(x.get(k) == y.get(k) for k in ("faces", "valid", "ok")) and abs((x.get("max") or 0) - (y.get("max") or 0)) < 1e-6
    print(f"{n[:30]:30} {f('faces'):>11} {f('valid'):>11} {f('ok'):>11} {r(x,'p99'):6.2f}→{r(y,'p99'):<6.2f} {r(x,'max'):6.2f}→{r(y,'max'):<6.2f} {x.get('within2',0):.4f}→{y.get('within2',0):.4f} {x['seconds']:6.1f}→{y['seconds']:<6.1f}{'' if not same else '  (same)'}")
