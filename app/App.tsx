import { useEffect, useState } from 'react';
import {
  ActivityIndicator,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from 'react-native';
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
type SubIndustry = { sub_code: string; group_code: string; name: string; definition: string };
type ProposalItem = { name: string; definition: string; rationale: string };
type Proposal = { subs: ProposalItem[]; sources: string[] };
type Share = { company_code: string; company_name: string; percentage: number; source: string };
type AnalyzedSub = { sub_code: string; name: string; shares: Share[] };
type AnalyzedGroup = { group_code: string; group_name: string; sub_industries: AnalyzedSub[] };
type SectorResult = { sector_code: string; period: string; groups: AnalyzedGroup[] };
type PortfolioRow = {
  company_code: string;
  period: string;
  segment: string;
  percentage: number;
  source: string;
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
  const [result, setResult] = useState<SectorResult | null>(null);
  const [portfolios, setPortfolios] = useState<Record<string, PortfolioRow[]>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetch(`${API}/sectors`)
      .then((r) => r.json())
      .then(setSectors)
      .catch(() => setError("백엔드 연결 실패 (/sectors) — 서버가 켜져 있나요?"));
  }, []);

  // Select a sector -> load its industry groups + any already-defined sub-industries.
  async function selectSector(s: Sector) {
    setSel(s);
    setResult(null);
    setError(null);
    setProposals({});
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

  // Analyze: fill the defined sub-industries' company market shares across the sector.
  async function analyze() {
    if (!sel) return;
    setBusy('analyze');
    setError(null);
    setResult(null);
    try {
      const r: SectorResult = await postJSON('/analyze', { sector_code: sel.sector_code });
      setResult(r);
    } catch (e) {
      setError(`분석 실패: ${String(e)}`);
    } finally {
      setBusy(null);
    }
  }

  // Tap a company -> its revenue portfolio (segment pie).
  async function loadPortfolio(s: Share) {
    setBusy(`pf:${s.company_code}`);
    try {
      const pf: PortfolioRow[] = await postJSON('/company/portfolio', {
        company_code: s.company_code,
      });
      setPortfolios((prev) => ({ ...prev, [s.company_code]: pf }));
    } catch {
      // ignore -- leave without a portfolio
    } finally {
      setBusy(null);
    }
  }

  const definedCount = groups.filter((g) => (taxonomy[g.group_code]?.length ?? 0) > 0).length;

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

      {/* taxonomy: define sub-industries per group */}
      {sel && (
        <View style={styles.card}>
          <Text style={styles.h2}>{sel.sector_name}</Text>
          <Text style={styles.muted}>
            산업그룹 {groups.length}개 · 정의됨 {definedCount}
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
                  <Text style={styles.row}>세부산업: {defs.map((d) => d.name).join(', ')}</Text>
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

          {definedCount > 0 && (
            <Pressable style={styles.analyzeBtn} onPress={analyze} disabled={busy === 'analyze'}>
              <Text style={styles.analyzeBtnText}>
                {busy === 'analyze' ? '분석 중… (~수 분)' : '📊 이 섹터 점유율 분석'}
              </Text>
            </Pressable>
          )}
        </View>
      )}

      {busy === 'analyze' && (
        <View style={styles.center}>
          <ActivityIndicator size="large" />
        </View>
      )}

      {/* analysis result: groups -> sub-industries -> share pies */}
      {result &&
        result.groups.map((g) => (
          <View key={g.group_code} style={styles.card}>
            <Text style={styles.h2}>{g.group_name}</Text>
            {g.sub_industries.map((sub) => (
              <View key={sub.sub_code} style={styles.sub}>
                <Text style={styles.subName}>{sub.name}</Text>
                {sub.shares.length > 0 ? (
                  <>
                    <View style={styles.pieWrap}>
                      <PieChart
                        data={toPie(
                          sub.shares.map((s) => ({ label: s.company_name, value: s.percentage })),
                        )}
                        radius={68}
                        showText
                        textColor="#fff"
                        textSize={10}
                      />
                    </View>
                    {sub.shares.map((s, j) => {
                      const isOthers = s.company_name.trim().toLowerCase() === 'others';
                      const pf = portfolios[s.company_code];
                      return (
                        <View key={j}>
                          <Pressable
                            onPress={() => !isOthers && loadPortfolio(s)}
                            disabled={isOthers || busy === `pf:${s.company_code}`}
                          >
                            <Text style={styles.companyRow}>
                              <Text style={{ color: PIE_COLORS[j % PIE_COLORS.length] }}>● </Text>
                              {s.company_name} — {s.percentage}%
                              {!isOthers &&
                                (busy === `pf:${s.company_code}`
                                  ? '  ⏳'
                                  : pf
                                    ? ''
                                    : '  · 포트폴리오')}
                            </Text>
                          </Pressable>
                          {pf && pf.length > 0 && (
                            <View style={styles.pieWrap}>
                              <PieChart
                                data={toPie(
                                  pf.map((x) => ({ label: x.segment, value: x.percentage })),
                                )}
                                radius={50}
                                showText
                                textColor="#fff"
                                textSize={9}
                              />
                            </View>
                          )}
                        </View>
                      );
                    })}
                  </>
                ) : (
                  <Text style={styles.muted}>점유율 데이터 없음</Text>
                )}
              </View>
            ))}
          </View>
        ))}
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
  center: { alignItems: 'center', marginTop: 24, gap: 8 },
  muted: { color: '#888', fontSize: 12, marginBottom: 6 },
  error: { color: '#c00', marginTop: 16 },
  card: { marginTop: 20, padding: 16, borderRadius: 12, backgroundColor: '#f7f7f9', gap: 4 },
  h2: { fontSize: 18, fontWeight: '700' },
  sub: { marginTop: 10, paddingTop: 10, borderTopWidth: 1, borderTopColor: '#e3e3ea' },
  subName: { fontSize: 14, fontWeight: '600' },
  row: { fontSize: 13, lineHeight: 19 },
  pieWrap: { alignItems: 'center', marginVertical: 10 },
  companyRow: { fontSize: 13, lineHeight: 20 },
  btn: {
    marginTop: 6,
    backgroundColor: '#e7eefc',
    paddingVertical: 8,
    paddingHorizontal: 12,
    borderRadius: 8,
    alignSelf: 'flex-start',
  },
  btnText: { fontSize: 13, color: '#2456c4', fontWeight: '600' },
  analyzeBtn: {
    marginTop: 14,
    backgroundColor: '#2456c4',
    paddingVertical: 11,
    paddingHorizontal: 14,
    borderRadius: 8,
    alignItems: 'center',
  },
  analyzeBtnText: { fontSize: 14, color: '#fff', fontWeight: '700' },
});
