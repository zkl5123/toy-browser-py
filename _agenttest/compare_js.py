"""Compare edge_js_results.json vs toy_js_results.json."""
import json, os
D = os.path.dirname(__file__)
edge = json.load(open(os.path.join(D, 'edge_js_results.json'), encoding='utf-8'))
toy = json.load(open(os.path.join(D, 'toy_js_results.json'), encoding='utf-8'))

def norm_logs(logs):
    return [l.strip() for l in logs if l.strip()]

def norm_status(s):
    return 'OK' if s == 'OK' else ('ERR' if s.startswith('ERROR') or s.startswith('EVAL_ERROR') else s)

matches = 0
mismatches = []
print(f"{'case':<24} {'edge':<10} {'toy':<10} {'logs-eq':<8}")
for cid in edge:
    if cid not in toy:
        mismatches.append((cid, 'missing in toy'))
        continue
    e_ok, t_ok = norm_status(edge[cid]['status']), norm_status(toy[cid]['status'])
    e_logs = norm_logs(edge[cid]['logs'])
    t_logs = norm_logs(toy[cid]['logs'])
    eq = e_logs == t_logs
    if e_ok == t_ok and eq:
        matches += 1
    else:
        mismatches.append((cid, f"edge={e_ok} toy={t_ok} logs_eq={eq}\n  edge_logs={e_logs}\n  toy_logs={t_logs}"))
    mark = '=' if eq else 'X'
    print(f"{cid:<24} {e_ok:<10} {t_ok:<10} {mark:<8}")

print(f"\n--- {matches}/{len(edge)} cases fully match (status + logs) ---")
for cid, d in mismatches:
    print(f"\n[{cid}]\n{d}")
