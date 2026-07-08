// Usage: npx tsx scripts/seed-traces.ts
import { readFileSync, existsSync } from "fs";
import { resolve, dirname } from "path";
import { fileURLToPath } from "url";
import { Langfuse } from "langfuse";

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

const envPath = resolve(__dirname, "../.env");
if (existsSync(envPath)) {
  readFileSync(envPath, "utf-8").split("\n").forEach((line) => {
    const m = line.match(/^([^#=\s]+)\s*=\s*(.*)$/);
    if (m) process.env[m[1]] = m[2].replace(/^["']|["']$/g, "");
  });
}

interface TraceFixture {
  name: string;
  userId: string;
  tags: string[];
  metadata: {
    classification: string;
    team: string;
    topic: string;
  };
  input: Record<string, unknown>;
  output: Record<string, unknown>;
}

const host =
  process.env.LANGFUSE_BASE_URL ??
  process.env.LANGFUSE_HOST ??
  "http://localhost:3001";

const publicKey = process.env.LANGFUSE_PUBLIC_KEY;
const secretKey = process.env.LANGFUSE_SECRET_KEY;

if (!publicKey || !secretKey) {
  console.error(
    "Error: LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY must be set.\n" +
      "Copy .env.example to .env and fill in your project API keys."
  );
  process.exit(1);
}

const langfuse = new Langfuse({
  publicKey,
  secretKey,
  baseUrl: host,
});

const fixturesPath = resolve(__dirname, "fixtures/traces.json");
const fixtures: TraceFixture[] = JSON.parse(readFileSync(fixturesPath, "utf-8"));

async function main() {
  console.log(`Seeding ${fixtures.length} traces to ${host}...`);
  let success = 0;

  for (let i = 0; i < fixtures.length; i++) {
    const f = fixtures[i];
    console.log(`Seeding trace ${i + 1}/${fixtures.length}: ${f.name}`);
    try {
      langfuse.trace({
        name: f.name,
        userId: f.userId,
        tags: f.tags,
        metadata: f.metadata,
        input: f.input,
        output: f.output,
      });
      success++;
    } catch (err) {
      console.error(`  Failed: ${(err as Error).message}`);
    }
  }

  await langfuse.flushAsync();
  console.log(`\nDone. ${success}/${fixtures.length} traces enqueued and flushed.`);
  console.log(`Check your Langfuse project at ${host}`);
}

main().catch((err) => {
  console.error("Seed failed:", err);
  process.exit(1);
});
