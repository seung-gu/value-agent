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

type CompanyShare = { company: string; share: number; evidence: string };
type Segment = { label: string; percentage: number };
type SubIndustry = {
  name: string;
  market_size: string;
  companies: CompanyShare[];
  sources: string[];
};
type CompanyPortfolio = { name: string; portfolio: Segment[]; sources: string[] };
type SectorAnalysis = {
  sector: string;
  market_size: string;
  cagr: string;
  potential_score: number;
  sub_industries: SubIndustry[];
  company_portfolios: CompanyPortfolio[];
  key_drivers: string[];
  sources: string[];
  confidence: number;
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

export default function App() {
  const [sectors, setSectors] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<SectorAnalysis | null>(null);
  const [busy, setBusy] = useState<string | null>(null); // name currently being refined

  useEffect(() => {
    fetch(`${API}/sectors`)
      .then((r) => r.json())
      .then(setSectors)
      .catch(() => setError("Can't reach backend (/sectors) — is the server running?"));
  }, []);

  // Stage 1: big picture
  async function analyze(sector: string) {
    setLoading(true);
    setError(null);
    setResult(null);
    try {
      const r = await fetch(`${API}/analyze`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ sector }),
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      setResult((await r.json()) as SectorAnalysis);
    } catch (e) {
      setError(`Analysis failed: ${String(e)}`);
    } finally {
      setLoading(false);
    }
  }

  // Stage 2: fill an empty sub-industry's company shares
  async function refineSub(name: string) {
    setBusy(name);
    try {
      const r = await fetch(`${API}/refine/sub-industry`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name }),
      });
      const filled = (await r.json()) as SubIndustry;
      setResult((prev) =>
        prev
          ? {
              ...prev,
              sub_industries: prev.sub_industries.map((s) =>
                s.name === name
                  ? { ...s, companies: filled.companies, sources: filled.sources }
                  : s,
              ),
            }
          : prev,
      );
    } catch {
      // leave it empty on failure
    } finally {
      setBusy(null);
    }
  }

  // Stage 2: research one company's portfolio
  async function refineCompany(name: string) {
    setBusy(name);
    try {
      const r = await fetch(`${API}/refine/company`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name }),
      });
      const pf = (await r.json()) as CompanyPortfolio;
      setResult((prev) =>
        prev
          ? {
              ...prev,
              company_portfolios: [
                ...prev.company_portfolios.filter((c) => c.name !== name),
                pf,
              ],
            }
          : prev,
      );
    } catch {
      // ignore
    } finally {
      setBusy(null);
    }
  }

  const portfolios = result?.company_portfolios.filter((c) => c.portfolio.length > 0) ?? [];

  return (
    <ScrollView contentContainerStyle={styles.container}>
      <Text style={styles.title}>Value Agent — Sector Analysis</Text>

      <View style={styles.chips}>
        {sectors.map((s) => (
          <Pressable key={s} style={styles.chip} onPress={() => analyze(s)} disabled={loading}>
            <Text style={styles.chipText}>{s}</Text>
          </Pressable>
        ))}
      </View>

      {loading && (
        <View style={styles.center}>
          <ActivityIndicator size="large" />
          <Text style={styles.muted}>Analyzing… (~1-2 min)</Text>
        </View>
      )}

      {error && <Text style={styles.error}>{error}</Text>}

      {result && (
        <View style={styles.card}>
          <Text style={styles.h2}>{result.sector}</Text>
          <Text style={styles.score}>
            Potential {result.potential_score}/100 · Confidence {result.confidence}
          </Text>
          <Text style={styles.row}>📊 Market size: {result.market_size}</Text>
          <Text style={styles.row}>📈 CAGR: {result.cagr}</Text>

          <Text style={styles.h3}>Growth drivers</Text>
          {result.key_drivers.map((d, i) => (
            <Text key={i} style={styles.row}>• {d}</Text>
          ))}

          <Text style={styles.h3}>Sub-industries</Text>
          {result.sub_industries.map((s, i) => (
            <View key={i} style={styles.sub}>
              <Text style={styles.subName}>{s.name}</Text>
              {s.market_size ? <Text style={styles.subMeta}>{s.market_size}</Text> : null}

              {s.companies.length > 0 ? (
                <>
                  <View style={styles.pieWrap}>
                    <PieChart
                      data={toPie(s.companies.map((c) => ({ label: c.company, value: c.share })))}
                      radius={68}
                      showText
                      textColor="#fff"
                      textSize={10}
                    />
                  </View>
                  {s.companies.map((c, j) => {
                    const isOthers = c.company.trim().toLowerCase() === 'others';
                    return (
                      <Pressable
                        key={j}
                        onPress={() => !isOthers && refineCompany(c.company)}
                        disabled={isOthers || busy === c.company}
                      >
                        <Text style={styles.companyRow}>
                          <Text style={{ color: PIE_COLORS[j % PIE_COLORS.length] }}>● </Text>
                          {c.company} — {c.share}%
                          {!isOthers && (busy === c.company ? '  ⏳' : '  · tap for portfolio')}
                        </Text>
                      </Pressable>
                    );
                  })}
                </>
              ) : (
                <Pressable
                  style={styles.refineBtn}
                  onPress={() => refineSub(s.name)}
                  disabled={busy === s.name}
                >
                  <Text style={styles.refineBtnText}>
                    {busy === s.name ? 'Researching…' : '🔍 조사하기 (회사 점유율)'}
                  </Text>
                </Pressable>
              )}
            </View>
          ))}

          {portfolios.length > 0 && (
            <>
              <Text style={styles.h3}>Company portfolios</Text>
              {portfolios.map((cp, i) => (
                <View key={i} style={styles.sub}>
                  <Text style={styles.subName}>{cp.name}</Text>
                  <View style={styles.pieWrap}>
                    <PieChart
                      data={toPie(cp.portfolio.map((seg) => ({ label: seg.label, value: seg.percentage })))}
                      radius={68}
                      showText
                      textColor="#fff"
                      textSize={10}
                    />
                  </View>
                  {cp.portfolio.map((seg, j) => (
                    <Text key={j} style={styles.companyRow}>
                      <Text style={{ color: PIE_COLORS[j % PIE_COLORS.length] }}>● </Text>
                      {seg.label} — {seg.percentage}%
                    </Text>
                  ))}
                </View>
              ))}
            </>
          )}
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
  chipText: { fontSize: 13 },
  center: { alignItems: 'center', marginTop: 24, gap: 8 },
  muted: { color: '#888' },
  error: { color: '#c00', marginTop: 16 },
  card: { marginTop: 20, padding: 16, borderRadius: 12, backgroundColor: '#f7f7f9', gap: 4 },
  h2: { fontSize: 18, fontWeight: '700' },
  score: { fontSize: 14, color: '#225', marginBottom: 8 },
  h3: { fontSize: 15, fontWeight: '600', marginTop: 14 },
  row: { fontSize: 13, lineHeight: 19 },
  sub: { marginTop: 10, paddingTop: 10, borderTopWidth: 1, borderTopColor: '#e3e3ea' },
  subName: { fontSize: 14, fontWeight: '600' },
  subMeta: { fontSize: 12, color: '#558', marginBottom: 4 },
  pieWrap: { alignItems: 'center', marginVertical: 10 },
  companyRow: { fontSize: 13, lineHeight: 20 },
  refineBtn: {
    marginTop: 6,
    backgroundColor: '#e7eefc',
    paddingVertical: 8,
    paddingHorizontal: 12,
    borderRadius: 8,
    alignSelf: 'flex-start',
  },
  refineBtnText: { fontSize: 13, color: '#2456c4', fontWeight: '600' },
});
