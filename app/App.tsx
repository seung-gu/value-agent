import { useEffect, useState } from 'react';
import { Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';
import { PieChart } from 'react-native-gifted-charts';

// Backend API base URL. EXPO_PUBLIC_API_URL is inlined at build time; localhost fallback.
const API = process.env.EXPO_PUBLIC_API_URL ?? 'http://127.0.0.1:8000';

type Sector = { sector_code: string; sector_name: string };
type Group = {
  group_code: string;
  sector_code: string;
  sector_name: string;
  group_name: string;
};
type SubIndustry = {
  sub_code: string;
  group_code: string;
  name: string;
  definition: string;
};
type ProposalItem = { name: string; definition: string; rationale: string };
type Proposal = { subs: ProposalItem[]; sources: string[] };
type Share = {
  company_code: string;
  company_name: string;
  ticker: string | null;
  percentage: number;
  source: string;
};
type SubChild = { sub_code: string; name: string; definition: string };
type SubResult = {
  sub_code: string;
  name: string;
  kind?: 'shares' | 'split';
  as_of: string;
  shares: Share[]; // [] when kind==='split'
  children?: SubChild[]; // present when kind==='split'
};
type FinancialRow = {
  company_code: string;
  period: string;
  account: string;
  amount: number;
  source: string;
};
type StreamRow = {
  company_code: string;
  period: string;
  stream: string;
  amount: number;
  source: string;
};
type CompanyResult = {
  company_code: string;
  name: string;
  ticker: string | null;
  cik: string | null;
  financials: FinancialRow[];
  portfolio: StreamRow[];
};

const PIE_COLORS = [
  '#4e79a7', '#f28e2b', '#e15759', '#76b7b2', '#59a14f',
  '#edc948', '#b07aa1', '#ff9da7', '#9c755f', '#bab0ac',
];

function toPie(items: { label: string; value: number }[]) {
  return items.map((it, i) => ({
    value: it.value,
    text: `${Math.round(it.value)}%`,
    color: PIE_COLORS[i % PIE_COLORS.length],
  }));
}

const ACCOUNT_LABELS: Record<string, string> = {
  revenue: '매출',
  operating_income: '영업이익',
  net_income: '순이익',
  operating_cash_flow: '영업현금흐름',
};

// Portfolio streams are absolute amounts -> pie by share of their sum (text shows the %).
function toPieByAmount(items: { label: string; value: number }[]) {
  const sum = items.reduce((a, b) => a + b.value, 0) || 1;
  return items.map((it, i) => ({
    value: it.value,
    text: `${Math.round((it.value / sum) * 100)}%`,
    color: PIE_COLORS[i % PIE_COLORS.length],
  }));
}

// Compact money label (currency-agnostic; T/B/M units). Sign preserved for losses.
function fmtAmount(n: number) {
  const a = Math.abs(n);
  const sign = n < 0 ? '-' : '';
  if (a >= 1e12) return `${sign}${(a / 1e12).toFixed(1)}T`;
  if (a >= 1e9) return `${sign}${(a / 1e9).toFixed(1)}B`;
  if (a >= 1e6) return `${sign}${(a / 1e6).toFixed(1)}M`;
  return `${sign}${a.toLocaleString()}`;
}

// The rows of the most recent fiscal period (periods sort lexically: 'FY2025' > 'FY2024').
function latestPeriod<T extends { period: string }>(rows: T[]): T[] {
  if (rows.length === 0) return [];
  const p = [...rows].map((r) => r.period).sort().at(-1);
  return rows.filter((r) => r.period === p);
}

async function postJSON(path: string, body: unknown) {
  const r = await fetch(`${API}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

export default function App() {
  const [sectors, setSectors] = useState<Sector[]>([]);
  const [sel, setSel] = useState<Sector | null>(null);
  const [groups, setGroups] = useState<Group[]>([]);
  const [taxonomy, setTaxonomy] = useState<Record<string, SubIndustry[]>>({});
  const [proposals, setProposals] = useState<Record<string, Proposal>>({});
  // Analysis results keyed by sub_code -- each sub-industry is analyzed on its own.
  const [subResults, setSubResults] = useState<Record<string, SubResult>>({});
  const [companies, setCompanies] = useState<Record<string, CompanyResult>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetch(`${API}/sectors`)
      .then((r) => r.json())
      .then(setSectors)
      .catch(() => setError('백엔드 연결 실패 (/sectors) — 서버가 켜져 있나요?'));
  }, []);

  // Select a sector -> load its industry groups + any already-defined sub-industries.
  async function selectSector(s: Sector) {
    setSel(s);
    setError(null);
    setProposals({});
    setSubResults({});
    try {
      const gs: Group[] = await fetch(`${API}/groups?sector_code=${s.sector_code}`).then((r) =>
        r.json(),
      );
      setGroups(gs);
      const tx: Record<string, SubIndustry[]> = {};
      await Promise.all(
        gs.map(async (g) => {
          tx[g.group_code] = await fetch(`${API}/taxonomy?group_code=${g.group_code}`).then((r) =>
            r.json(),
          );
        }),
      );
      setTaxonomy(tx);
    } catch {
      setError('산업그룹 로드 실패');
    }
  }

  // HITL step 1: agent proposes the sub-industries for a group (draft, not saved).
  async function propose(g: Group) {
    setBusy(`propose:${g.group_code}`);
    setError(null);
    try {
      const p: Proposal = await postJSON('/taxonomy/propose', { group_code: g.group_code });
      setProposals((prev) => ({ ...prev, [g.group_code]: p }));
    } catch {
      setError('세부산업 제안 실패');
    } finally {
      setBusy(null);
    }
  }

  // HITL step 2: persist the approved proposal (surrogate codes assigned server-side).
  async function save(g: Group) {
    const p = proposals[g.group_code];
    if (!p) return;
    setBusy(`save:${g.group_code}`);
    try {
      const saved: SubIndustry[] = await postJSON('/taxonomy/save', {
        group_code: g.group_code,
        findings: p.subs,
      });
      setTaxonomy((prev) => ({ ...prev, [g.group_code]: saved }));
      setProposals((prev) => {
        const n = { ...prev };
        delete n[g.group_code];
        return n;
      });
    } catch {
      setError('저장 실패');
    } finally {
      setBusy(null);
    }
  }

  // Analyze ONE sub-industry's company market shares (no sector-wide fan-out).
  async function analyzeSub(sub: SubIndustry) {
    setBusy(`sub:${sub.sub_code}`);
    setError(null);
    try {
      const r: SubResult = await postJSON('/analyze/sub', { sub_code: sub.sub_code });
      setSubResults((prev) => ({ ...prev, [sub.sub_code]: r }));
      if (r.kind === 'split' && r.children) {
        // register the child segments right under their parent so each can be analyzed
        setTaxonomy((prev) => {
          const cur = prev[sub.group_code] ?? [];
          const fresh = r
            .children!.filter((c) => !cur.some((e) => e.sub_code === c.sub_code))
            .map((c) => ({
              sub_code: c.sub_code,
              group_code: sub.group_code,
              name: c.name,
              definition: c.definition,
            }));
          if (fresh.length === 0) return prev;
          const idx = cur.findIndex((e) => e.sub_code === sub.sub_code);
          const next = [...cur];
          next.splice(idx + 1, 0, ...fresh);
          return { ...prev, [sub.group_code]: next };
        });
      }
    } catch (e) {
      setError(`분석 실패: ${String(e)}`);
    } finally {
      setBusy(null);
    }
  }

  // Tap a company -> its financials + revenue portfolio (EDGAR for US-listed, else web).
  async function loadCompany(s: Share) {
    setBusy(`pf:${s.company_code}`);
    try {
      const co: CompanyResult = await postJSON('/company/analyze', {
        name: s.company_name,
        ticker: s.ticker,
      });
      setCompanies((prev) => ({ ...prev, [s.company_code]: co }));
    } catch {
      // ignore -- leave without company detail
    } finally {
      setBusy(null);
    }
  }

  return (
    <ScrollView contentContainerStyle={styles.container}>
      <Text style={styles.title}>Value Agent</Text>

      {/* sector chips */}
      <View style={styles.chips}>
        {sectors.map((s) => (
          <Pressable
            key={s.sector_code}
            style={[styles.chip, sel?.sector_code === s.sector_code && styles.chipSel]}
            onPress={() => selectSector(s)}
          >
            <Text style={styles.chipText}>{s.sector_name}</Text>
          </Pressable>
        ))}
      </View>

      {error && <Text style={styles.error}>{error}</Text>}

      {/* per-group: define sub-industries, then analyze each sub-industry on its own */}
      {sel && (
        <View style={styles.card}>
          <Text style={styles.h2}>{sel.sector_name}</Text>
          <Text style={styles.muted}>
            산업그룹 {groups.length}개 · 세부산업을 정의하고 하나씩 분석
          </Text>

          {groups.map((g) => {
            const defs = taxonomy[g.group_code] ?? [];
            const prop = proposals[g.group_code];
            return (
              <View key={g.group_code} style={styles.sub}>
                <Text style={styles.subName}>
                  {g.group_code} · {g.group_name}
                </Text>

                {defs.length > 0 ? (
                  defs.map((d) => {
                    const res = subResults[d.sub_code];
                    const loading = busy === `sub:${d.sub_code}`;
                    const isChild = /-S\d+$/.test(d.sub_code); // split child, e.g. '4530-06-S01'
                    return (
                      <View key={d.sub_code} style={[styles.subItem, isChild && styles.childItem]}>
                        <View style={styles.subItemRow}>
                          <Text style={styles.subItemName}>
                            {isChild ? '↳ ' : ''}
                            {d.name}
                          </Text>
                          <Pressable
                            style={styles.smallBtn}
                            onPress={() => analyzeSub(d)}
                            disabled={loading}
                          >
                            <Text style={styles.smallBtnText}>
                              {loading ? '분석 중…' : res ? '↻ 새로' : '📊 분석'}
                            </Text>
                          </Pressable>
                        </View>

                        {res && res.kind === 'split' && (
                          <Text style={styles.muted}>
                            세부 세그먼트로 나뉘었어 — 아래 ↳ 항목을 각각 분석해줘
                          </Text>
                        )}
                        {res && res.kind !== 'split' && res.shares.length > 0 && (
                          <>
                            {res.as_of ? <Text style={styles.muted}>기준 {res.as_of}</Text> : null}
                            <View style={styles.pieWrap}>
                              <PieChart
                                data={toPie(
                                  res.shares.map((s) => ({
                                    label: s.company_name,
                                    value: s.percentage,
                                  })),
                                )}
                                radius={68}
                                showText
                                textColor="#fff"
                                textSize={10}
                              />
                            </View>
                            {res.shares.map((s, j) => {
                              const isOthers = s.company_name.trim().toLowerCase() === 'others';
                              const co = companies[s.company_code];
                              const fins = co ? latestPeriod(co.financials) : [];
                              const streams = co ? latestPeriod(co.portfolio) : [];
                              return (
                                <View key={j}>
                                  <Pressable
                                    onPress={() => !isOthers && loadCompany(s)}
                                    disabled={isOthers || busy === `pf:${s.company_code}`}
                                  >
                                    <Text style={styles.companyRow}>
                                      <Text style={{ color: PIE_COLORS[j % PIE_COLORS.length] }}>● </Text>
                                      {s.company_name}
                                      {s.ticker ? ` (${s.ticker})` : ''} — {s.percentage}%
                                      {!isOthers &&
                                        (busy === `pf:${s.company_code}`
                                          ? '  ⏳'
                                          : co
                                            ? ''
                                            : '  · 상세')}
                                    </Text>
                                  </Pressable>
                                  {co && (
                                    <View style={styles.companyDetail}>
                                      {fins.length > 0 && (
                                        <View style={styles.finRow}>
                                          {fins.map((f, k) => (
                                            <Text key={k} style={styles.finItem}>
                                              {ACCOUNT_LABELS[f.account] ?? f.account} {fmtAmount(f.amount)}
                                            </Text>
                                          ))}
                                        </View>
                                      )}
                                      {streams.length > 0 && (
                                        <View style={styles.pieWrap}>
                                          <PieChart
                                            data={toPieByAmount(
                                              streams.map((x) => ({ label: x.stream, value: x.amount })),
                                            )}
                                            radius={50}
                                            showText
                                            textColor="#fff"
                                            textSize={9}
                                          />
                                        </View>
                                      )}
                                      {fins.length === 0 && streams.length === 0 && (
                                        <Text style={styles.muted}>재무·포트폴리오 데이터 없음</Text>
                                      )}
                                    </View>
                                  )}
                                </View>
                              );
                            })}
                          </>
                        )}
                        {res && res.kind !== 'split' && res.shares.length === 0 && (
                          <Text style={styles.muted}>점유율 데이터 없음</Text>
                        )}
                      </View>
                    );
                  })
                ) : prop ? (
                  <View>
                    {prop.subs.map((s, i) => (
                      <Text key={i} style={styles.row}>
                        • {s.name} — {s.definition}
                      </Text>
                    ))}
                    <Pressable
                      style={styles.btn}
                      onPress={() => save(g)}
                      disabled={busy === `save:${g.group_code}`}
                    >
                      <Text style={styles.btnText}>
                        {busy === `save:${g.group_code}` ? '저장 중…' : '✓ 저장'}
                      </Text>
                    </Pressable>
                  </View>
                ) : (
                  <Pressable
                    style={styles.btn}
                    onPress={() => propose(g)}
                    disabled={busy === `propose:${g.group_code}`}
                  >
                    <Text style={styles.btnText}>
                      {busy === `propose:${g.group_code}` ? '조사 중…' : '🔍 세부산업 제안받기'}
                    </Text>
                  </Pressable>
                )}
              </View>
            );
          })}
        </View>
      )}
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: { padding: 20, paddingTop: 60, backgroundColor: '#fff' },
  title: { fontSize: 22, fontWeight: '700', marginBottom: 16 },
  chips: { flexDirection: 'row', flexWrap: 'wrap', gap: 8 },
  chip: { backgroundColor: '#eef', paddingVertical: 8, paddingHorizontal: 12, borderRadius: 16 },
  chipSel: { backgroundColor: '#c9d8ff' },
  chipText: { fontSize: 13 },
  muted: { color: '#888', fontSize: 12, marginBottom: 6 },
  error: { color: '#c00', marginTop: 16 },
  card: { marginTop: 20, padding: 16, borderRadius: 12, backgroundColor: '#f7f7f9', gap: 4 },
  h2: { fontSize: 18, fontWeight: '700' },
  sub: { marginTop: 10, paddingTop: 10, borderTopWidth: 1, borderTopColor: '#e3e3ea' },
  subName: { fontSize: 14, fontWeight: '600' },
  row: { fontSize: 13, lineHeight: 19 },
  subItem: { marginTop: 8, paddingLeft: 8, borderLeftWidth: 2, borderLeftColor: '#e3e3ea' },
  subItemRow: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' },
  subItemName: { fontSize: 13, fontWeight: '600', flex: 1 },
  pieWrap: { alignItems: 'center', marginVertical: 10 },
  childItem: { marginLeft: 16, borderLeftColor: '#c9d8ff' },
  companyRow: { fontSize: 13, lineHeight: 20 },
  companyDetail: { marginTop: 4, marginBottom: 6, paddingLeft: 14 },
  finRow: { flexDirection: 'row', flexWrap: 'wrap', gap: 10, marginTop: 2 },
  finItem: { fontSize: 12, color: '#444' },
  btn: {
    marginTop: 6,
    backgroundColor: '#e7eefc',
    paddingVertical: 8,
    paddingHorizontal: 12,
    borderRadius: 8,
    alignSelf: 'flex-start',
  },
  btnText: { fontSize: 13, color: '#2456c4', fontWeight: '600' },
  smallBtn: { backgroundColor: '#e7eefc', paddingVertical: 5, paddingHorizontal: 10, borderRadius: 6 },
  smallBtnText: { fontSize: 12, color: '#2456c4', fontWeight: '600' },
});
