import { useEffect, useState } from 'react';
import {
  ActivityIndicator,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from 'react-native';

// Backend API base URL.
// - local dev: falls back to localhost
// - deployed (Railway/Vercel): set EXPO_PUBLIC_API_URL to the backend's public URL.
//   Expo inlines EXPO_PUBLIC_* env vars into the bundle at build time.
const API = process.env.EXPO_PUBLIC_API_URL ?? 'http://127.0.0.1:8000';

type Company = { name: string; reason: string };
type SectorAnalysis = {
  sector: string;
  market_size: string;
  cagr: string;
  potential_score: number;
  top_companies: Company[];
  key_drivers: string[];
  sources: string[];
  confidence: number;
};

export default function App() {
  const [sectors, setSectors] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<SectorAnalysis | null>(null);

  // Load the sector list on mount (GET /sectors)
  useEffect(() => {
    fetch(`${API}/sectors`)
      .then((r) => r.json())
      .then(setSectors)
      .catch(() => setError("Can't reach backend (/sectors) — is the server running?"));
  }, []);

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

  return (
    <ScrollView contentContainerStyle={styles.container}>
      <Text style={styles.title}>Value Agent — Sector Analysis</Text>

      <View style={styles.chips}>
        {sectors.map((s) => (
          <Pressable
            key={s}
            style={styles.chip}
            onPress={() => analyze(s)}
            disabled={loading}
          >
            <Text style={styles.chipText}>{s}</Text>
          </Pressable>
        ))}
      </View>

      {loading && (
        <View style={styles.center}>
          <ActivityIndicator size="large" />
          <Text style={styles.muted}>Analyzing… (30s–1min)</Text>
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

          <Text style={styles.h3}>Competitors</Text>
          {result.top_companies.map((c, i) => (
            <Text key={i} style={styles.row}>
              • {c.name} — {c.reason}
            </Text>
          ))}

          <Text style={styles.h3}>Growth drivers</Text>
          {result.key_drivers.map((d, i) => (
            <Text key={i} style={styles.row}>
              • {d}
            </Text>
          ))}

          <Text style={styles.h3}>Sources ({result.sources.length})</Text>
          {result.sources.slice(0, 5).map((u, i) => (
            <Text key={i} style={styles.src}>
              {u}
            </Text>
          ))}
        </View>
      )}
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: { padding: 20, paddingTop: 60, backgroundColor: '#fff' },
  title: { fontSize: 22, fontWeight: '700', marginBottom: 16 },
  chips: { flexDirection: 'row', flexWrap: 'wrap', gap: 8 },
  chip: {
    backgroundColor: '#eef',
    paddingVertical: 8,
    paddingHorizontal: 12,
    borderRadius: 16,
  },
  chipText: { fontSize: 13 },
  center: { alignItems: 'center', marginTop: 24, gap: 8 },
  muted: { color: '#888' },
  error: { color: '#c00', marginTop: 16 },
  card: {
    marginTop: 20,
    padding: 16,
    borderRadius: 12,
    backgroundColor: '#f7f7f9',
    gap: 4,
  },
  h2: { fontSize: 18, fontWeight: '700' },
  score: { fontSize: 14, color: '#225', marginBottom: 8 },
  h3: { fontSize: 15, fontWeight: '600', marginTop: 12 },
  row: { fontSize: 13, lineHeight: 19 },
  src: { fontSize: 11, color: '#558' },
});
