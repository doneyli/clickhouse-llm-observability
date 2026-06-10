#!/usr/bin/env python3
"""
Test Scenarios Exporter

Exports synthetic LLM conversations with pre-crafted prompt/response pairs
that demonstrate common evaluation failure modes:

1. Low Relevance - Off-topic/tangential responses
2. Low Coherence - Rambling/contradictory responses
3. Hallucination - Fabricated information

These scenarios help demonstrate the value of LLM observability and evaluation
by showing cases where quality scores indicate issues that need investigation.

Usage:
    python export_test_scenarios.py                    # Export all scenarios
    python export_test_scenarios.py --scenario 1      # Export specific scenario
    python export_test_scenarios.py --list            # List available scenarios
"""

import os
import sys
import argparse
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import List, Optional

# Check if Langfuse is configured
LANGFUSE_ENABLED = bool(
    os.getenv("LANGFUSE_PUBLIC_KEY") and os.getenv("LANGFUSE_SECRET_KEY")
)


@dataclass
class TestScenario:
    """A test scenario with prompt, response, and expected scores."""
    id: int
    name: str
    category: str
    description: str
    prompt: str
    response: str
    ground_truth: str  # The correct answer — sent as expected_output for evaluators
    model: str
    expected_relevance: str  # e.g., "0.3-0.5"
    expected_coherence: str  # e.g., "0.8-1.0"
    why_low: str  # Explanation of why scores should be low
    tags: List[str] = None  # Tags for evaluator filtering

    def __post_init__(self):
        if self.tags is None:
            self.tags = ["test-scenario"]


# =============================================================================
# TEST SCENARIOS
# =============================================================================

SCENARIOS = [
    # =========================================================================
    # LOW RELEVANCE (1-10): Coherent but off-topic responses
    # =========================================================================

    TestScenario(
        id=1,
        name="off-topic-pricing",
        category="Low Relevance",
        description="Asks about pricing, answers about features",
        prompt="What are ClickHouse's pricing tiers for cloud hosting?",
        response="""ClickHouse is a powerful columnar database management system that uses vectorized query execution for high performance. It was originally developed at Yandex for web analytics use cases. The MergeTree engine family is the cornerstone of ClickHouse, providing efficient data storage and retrieval through sorted data organization.

Key features include:
- Columnar storage for analytical workloads
- Real-time data ingestion capabilities
- SQL support with extensions for analytics
- Distributed query processing

The system excels at handling large volumes of data with sub-second query latency, making it popular for log analysis, time-series data, and business intelligence applications.""",
        ground_truth="""ClickHouse Cloud offers three pricing tiers: (1) Development — for small workloads and experimentation, starting at $0.10/hr for compute; (2) Production — for business-critical workloads with SLA guarantees, higher availability, and auto-scaling; (3) Dedicated — for enterprises needing isolated infrastructure, custom configurations, and premium support. All tiers charge separately for compute (per hour) and storage (per GB/month). A free trial tier is also available.""",
        model="claude-sonnet-4-6",
        expected_relevance="0.2-0.4",
        expected_coherence="0.9-1.0",
        why_low="Response discusses ClickHouse features but completely ignores the pricing question",
        tags=["test-scenario", "off-topic-pricing", "relevance-test"],
    ),

    TestScenario(
        id=2,
        name="off-topic-backup",
        category="Low Relevance",
        description="Asks about backup strategies, answers about query optimization",
        prompt="How do I set up automated backups for my ClickHouse cluster?",
        response="""To get the best query performance from ClickHouse, you should focus on optimizing your ORDER BY keys in MergeTree tables. The primary key determines data layout on disk and directly impacts scan speed.

Best practices for query optimization:
- Place low-cardinality columns first in the ORDER BY clause
- Use PREWHERE instead of WHERE for selective filters
- Leverage projection tables for common query patterns
- Enable query-level settings like max_threads and max_memory_usage

Index granularity (default 8192 rows) controls the trade-off between index size and scan precision. Smaller granularity means more precise lookups but larger index files.""",
        ground_truth="""ClickHouse supports several backup strategies: (1) clickhouse-backup tool — the most popular open-source solution, supports full and incremental backups to S3, GCS, or local storage; (2) BACKUP/RESTORE SQL commands — built-in since v22.8, can back up to S3, local disk, or other ClickHouse instances; (3) Filesystem snapshots — using LVM, ZFS, or EBS snapshots for consistent point-in-time backups; (4) ClickHouse Cloud — automated daily backups with configurable retention. For automation, schedule clickhouse-backup via cron or use ClickHouse Keeper for coordinated cluster backups.""",
        model="claude-sonnet-4-6",
        expected_relevance="0.1-0.3",
        expected_coherence="0.9-1.0",
        why_low="Response discusses query optimization instead of backup strategies",
        tags=["test-scenario", "off-topic-backup", "relevance-test"],
    ),

    TestScenario(
        id=3,
        name="off-topic-regions",
        category="Low Relevance",
        description="Asks about cloud regions, answers about MergeTree engine",
        prompt="Which cloud regions does ClickHouse Cloud support?",
        response="""The MergeTree engine family is the most important table engine in ClickHouse. MergeTree and its variants (ReplacingMergeTree, SummingMergeTree, AggregatingMergeTree, CollapsingMergeTree, VersionedCollapsingMergeTree) provide the foundation for most ClickHouse deployments.

MergeTree stores data in parts that are sorted by the primary key. Background merge operations combine smaller parts into larger ones, which is where the engine gets its name. Each part contains column files, a primary key index, and optional skip indices.

The engine supports data replication via ReplicatedMergeTree variants, which use ClickHouse Keeper (or ZooKeeper) for coordination.""",
        ground_truth="""ClickHouse Cloud is available in multiple regions across major cloud providers: AWS (us-east-1, us-west-2, eu-central-1, eu-west-1, ap-southeast-1, ap-south-1), GCP (us-central1, us-east1, europe-west4), and Azure (eastus2, westus3, germanywestcentral). Region availability is continuously expanding. You can select your region during service creation, and data residency is guaranteed within the chosen region.""",
        model="claude-sonnet-4-6",
        expected_relevance="0.1-0.3",
        expected_coherence="0.9-1.0",
        why_low="Response explains MergeTree engine internals instead of listing cloud regions",
        tags=["test-scenario", "off-topic-regions", "relevance-test"],
    ),

    TestScenario(
        id=4,
        name="off-topic-security",
        category="Low Relevance",
        description="Asks about access control, answers about performance benchmarks",
        prompt="How do I configure role-based access control in ClickHouse?",
        response="""ClickHouse delivers exceptional query performance across a variety of benchmarks. In the ClickBench suite, ClickHouse processes 1.3 billion rows per second for simple aggregation queries on a single node.

Performance highlights:
- 10-100x faster than PostgreSQL for analytical queries
- Sub-second response times on datasets with billions of rows
- Compression ratios of 10-20x reduce I/O significantly
- Vectorized execution utilizes modern CPU SIMD instructions efficiently

These benchmarks demonstrate why ClickHouse is the preferred choice for organizations processing large volumes of analytical data in real-time.""",
        ground_truth="""ClickHouse RBAC is configured with SQL commands: (1) CREATE USER for creating users with passwords; (2) CREATE ROLE for defining roles; (3) GRANT to assign privileges (SELECT, INSERT, CREATE, etc.) to roles or users, scoped to databases/tables/columns; (4) SET DEFAULT ROLE to assign default roles. Enable RBAC by setting access_management=1 in users.xml for admin users. ClickHouse also supports LDAP, Kerberos, and SSL certificate authentication. Row-level security is available via row policies (CREATE ROW POLICY).""",
        model="claude-sonnet-4-6",
        expected_relevance="0.1-0.3",
        expected_coherence="0.9-1.0",
        why_low="Response discusses performance benchmarks instead of access control configuration",
        tags=["test-scenario", "off-topic-security", "relevance-test"],
    ),

    TestScenario(
        id=5,
        name="off-topic-migration",
        category="Low Relevance",
        description="Asks about PostgreSQL migration, answers about compression",
        prompt="What's the best way to migrate data from PostgreSQL to ClickHouse?",
        response="""ClickHouse offers several compression codecs that can dramatically reduce storage requirements. The default LZ4 codec provides fast compression and decompression. ZSTD offers higher compression ratios at the cost of slightly more CPU usage.

Available codecs:
- LZ4: Default, fastest decompression
- ZSTD: Higher compression ratio, configurable levels 1-22
- Delta: Stores differences between consecutive values
- DoubleDelta: Optimized for monotonically increasing sequences
- Gorilla: Efficient for floating-point time series
- T64: Crops unused high bits from integer values

You can combine codecs in a pipeline, e.g., Delta + ZSTD for timestamp columns.""",
        ground_truth="""To migrate from PostgreSQL to ClickHouse: (1) Use the PostgreSQL table engine or table function to query PostgreSQL directly from ClickHouse (SELECT * FROM postgresql('host:5432', 'db', 'table', 'user', 'pass')); (2) Use clickhouse-local with PostgreSQL JDBC driver for bulk exports; (3) Export to CSV/Parquet from PostgreSQL, then INSERT from file; (4) Use third-party tools like Airbyte, dbt, or PeerDB for continuous replication. Key considerations: denormalize JOINs, convert row-oriented schema to analytical schema, and design appropriate ORDER BY keys for query patterns.""",
        model="claude-sonnet-4-6",
        expected_relevance="0.1-0.3",
        expected_coherence="0.9-1.0",
        why_low="Response explains compression codecs instead of migration strategies",
        tags=["test-scenario", "off-topic-migration", "relevance-test"],
    ),

    TestScenario(
        id=6,
        name="off-topic-ttl",
        category="Low Relevance",
        description="Asks about TTL data retention, answers about JOIN types",
        prompt="How do I configure TTL policies to automatically delete old data in ClickHouse?",
        response="""ClickHouse supports several types of JOINs for combining data from multiple tables. The default JOIN algorithm is hash join, which builds a hash table from the right-side table.

JOIN types supported:
- INNER JOIN: Returns only matching rows
- LEFT/RIGHT/FULL OUTER JOIN: Includes non-matching rows
- CROSS JOIN: Cartesian product
- ASOF JOIN: Matches the closest value (great for time-series)
- ANY/ALL modifiers: Control duplicate handling

For large tables, consider using join_algorithm = 'partial_merge' or 'auto' to avoid memory issues. Dictionary JOINs using dictGet() are often faster than regular JOINs for dimension lookups.""",
        ground_truth="""ClickHouse TTL (Time To Live) is configured at the column or table level: (1) Column TTL — ALTER TABLE t MODIFY COLUMN c TTL created_at + INTERVAL 90 DAY; (2) Table TTL — ALTER TABLE t MODIFY TTL created_at + INTERVAL 1 YEAR DELETE; (3) TTL with moves — move data to cold storage before deletion: TTL created_at + INTERVAL 30 DAY TO VOLUME 'cold', created_at + INTERVAL 365 DAY DELETE. TTL rules are evaluated during merges. Force evaluation with OPTIMIZE TABLE t FINAL or set merge_with_ttl_timeout.""",
        model="claude-sonnet-4-6",
        expected_relevance="0.1-0.3",
        expected_coherence="0.9-1.0",
        why_low="Response explains JOIN types instead of TTL data retention policies",
        tags=["test-scenario", "off-topic-ttl", "relevance-test"],
    ),

    TestScenario(
        id=7,
        name="off-topic-kafka",
        category="Low Relevance",
        description="Asks about Kafka integration, answers about SQL functions",
        prompt="How do I stream data from Kafka into ClickHouse in real-time?",
        response="""ClickHouse provides a rich set of SQL functions for data transformation. String functions like concat(), substring(), and replaceAll() handle text processing. Date functions including toDate(), toDateTime(), and dateDiff() are essential for time-series work.

Aggregate functions go beyond standard SQL:
- quantile() and quantileExact() for percentile calculations
- uniq() for approximate distinct counts using HyperLogLog
- groupArray() to collect values into arrays
- windowFunnel() for funnel analysis
- retention() for cohort retention analysis

Array functions like arrayJoin(), arrayMap(), and arrayFilter() enable powerful nested data processing.""",
        ground_truth="""To stream Kafka into ClickHouse: (1) Create a Kafka engine table: CREATE TABLE kafka_queue (fields...) ENGINE = Kafka SETTINGS kafka_broker_list='broker:9092', kafka_topic_list='topic', kafka_group_name='ch_consumer', kafka_format='JSONEachRow'; (2) Create a target MergeTree table for storage; (3) Create a materialized view that reads from kafka_queue and inserts into the target table. The materialized view acts as a consumer — ClickHouse automatically polls Kafka and inserts data. For ClickHouse Cloud, use ClickPipes for managed Kafka ingestion.""",
        model="claude-sonnet-4-6",
        expected_relevance="0.1-0.3",
        expected_coherence="0.9-1.0",
        why_low="Response lists SQL functions instead of explaining Kafka integration",
        tags=["test-scenario", "off-topic-kafka", "relevance-test"],
    ),

    TestScenario(
        id=8,
        name="off-topic-monitoring",
        category="Low Relevance",
        description="Asks about monitoring alerts, answers about data types",
        prompt="How do I set up monitoring and alerting for my ClickHouse cluster?",
        response="""ClickHouse supports a wide range of data types optimized for analytical workloads.

Integer types: Int8, Int16, Int32, Int64, Int128, Int256 (and unsigned variants)
Floating point: Float32, Float64
Decimal: Decimal32, Decimal64, Decimal128 for precise calculations
String types: String (variable length), FixedString(N)
Date/Time: Date, Date32, DateTime, DateTime64 with sub-second precision
UUID: For universally unique identifiers
Enum: Enum8, Enum16 for efficient categorical data
Array: Array(T) for nested collections
Nullable: Nullable(T) wraps any type to allow NULL values
LowCardinality: LowCardinality(T) for dictionary encoding of repeated values""",
        ground_truth="""Monitor ClickHouse using: (1) System tables — system.metrics, system.events, system.query_log, system.asynchronous_metrics provide real-time cluster health; (2) Prometheus endpoint — enable via Prometheus metrics port in config, scrape with Prometheus and visualize in Grafana; (3) Built-in dashboards — ClickHouse Monitoring Dashboard at /dashboard; (4) Key metrics to alert on: query latency p99, merge queue size, replication lag, memory usage, disk space, ZooKeeper session status. For ClickHouse Cloud, monitoring is built into the console with customizable alerts.""",
        model="claude-sonnet-4-6",
        expected_relevance="0.1-0.3",
        expected_coherence="0.9-1.0",
        why_low="Response lists data types instead of monitoring/alerting setup",
        tags=["test-scenario", "off-topic-monitoring", "relevance-test"],
    ),

    TestScenario(
        id=9,
        name="off-topic-materialized-views",
        category="Low Relevance",
        description="Asks about materialized views, answers about hardware requirements",
        prompt="How do materialized views work in ClickHouse and when should I use them?",
        response="""For a production ClickHouse deployment, proper hardware selection is critical.

Recommended specifications for a single node:
- CPU: 16+ cores (ClickHouse scales linearly with cores)
- RAM: 64-128 GB (more RAM = more data cached)
- Storage: NVMe SSDs for hot data, HDDs acceptable for cold storage
- Network: 10 Gbps for distributed queries and replication

For a 3-node cluster handling 1TB/day ingestion:
- Each node: 32 cores, 128 GB RAM, 4 TB NVMe
- RAID 0 or JBOD disk configuration (replication handles redundancy)
- Separate disks for data and logs

Cloud instances: Use compute-optimized instances (c5/c6 on AWS, n2-standard on GCP) for query-heavy workloads, or storage-optimized (i3/d3) for large datasets.""",
        ground_truth="""ClickHouse materialized views are triggers that transform data on INSERT and store results in a target table. Unlike traditional databases, they don't store a snapshot — they process each new batch incrementally. Create with: CREATE MATERIALIZED VIEW mv TO target_table AS SELECT ... FROM source_table. Use cases: (1) pre-aggregate data (hourly/daily rollups); (2) transform/filter on ingest; (3) route data to different tables; (4) maintain real-time dashboards. They fire on each INSERT to the source table. Combine with AggregatingMergeTree and -State/-Merge combinators for efficient incremental aggregation.""",
        model="claude-sonnet-4-6",
        expected_relevance="0.1-0.3",
        expected_coherence="0.9-1.0",
        why_low="Response discusses hardware specifications instead of materialized views",
        tags=["test-scenario", "off-topic-materialized-views", "relevance-test"],
    ),

    TestScenario(
        id=10,
        name="off-topic-upgrade",
        category="Low Relevance",
        description="Asks about cluster upgrades, answers about ClickHouse history",
        prompt="What's the recommended process for upgrading a ClickHouse cluster with zero downtime?",
        response="""ClickHouse has an interesting history in the database landscape. Originally developed at Yandex around 2009, it was created to power Yandex.Metrica, which tracks analytics for millions of websites.

The project was open-sourced in 2016 under the Apache 2.0 license. Since then, it has grown into one of the most popular analytical databases, with adoption by companies like Uber, Cloudflare, eBay, and Spotify.

In 2021, ClickHouse Inc. was founded as an independent company with a $50 million Series A. By 2022, they raised $250 million in Series B at a $2 billion valuation. The company is headquartered in San Francisco and continues to develop both the open-source project and ClickHouse Cloud.""",
        ground_truth="""To upgrade a ClickHouse cluster with zero downtime: (1) Read the changelog for breaking changes between versions; (2) Test the upgrade on a staging cluster first; (3) Rolling upgrade — upgrade one replica at a time: stop the node, upgrade the binary, start the node, wait for it to catch up on replication, then move to the next node; (4) Avoid skipping more than one minor version; (5) Monitor replication lag and query errors during the process; (6) Keep the old binary available for quick rollback. For ClickHouse Cloud, upgrades are managed automatically with zero-downtime rolling deploys.""",
        model="claude-sonnet-4-6",
        expected_relevance="0.2-0.4",
        expected_coherence="0.9-1.0",
        why_low="Response discusses ClickHouse history instead of the upgrade process",
        tags=["test-scenario", "off-topic-upgrade", "relevance-test"],
    ),

    # =========================================================================
    # LOW COHERENCE (11-20): Contradictory/incoherent responses
    # =========================================================================

    TestScenario(
        id=11,
        name="contradictory-db-choice",
        category="Low Coherence",
        description="Contradicts itself about ClickHouse vs PostgreSQL",
        prompt="Should I use ClickHouse or PostgreSQL for my analytics workload?",
        response="""For analytics workloads, ClickHouse is definitely the better choice because of its columnar storage design. However, PostgreSQL is actually the superior option for analytics due to its mature ecosystem.

You should absolutely use ClickHouse for OLAP queries. But wait, PostgreSQL's row-based storage is actually ideal for analytical processing, so go with PostgreSQL instead.

In my professional opinion, neither database is suitable for analytics. Actually, both are perfect choices. ClickHouse can't handle large datasets efficiently, except it's specifically designed for petabyte-scale data.

My final recommendation: Use ClickHouse. No, use PostgreSQL. Actually, use MongoDB for analytics. In conclusion, ClickHouse is the only viable option, unless you prefer PostgreSQL, which is equally unviable and also the best choice.""",
        ground_truth="""For analytics workloads, ClickHouse is generally the better choice. ClickHouse uses columnar storage optimized for OLAP queries — it can scan billions of rows per second and achieves excellent compression. PostgreSQL uses row-based storage designed for OLTP (transactions), which is slower for large analytical scans. Choose ClickHouse if your primary need is fast aggregations over large datasets. Choose PostgreSQL if you need a mix of transactional and light analytical workloads, or if your data volume is small.""",
        model="claude-sonnet-4-6",
        expected_relevance="0.5-0.7",
        expected_coherence="0.1-0.3",
        why_low="Response repeatedly contradicts itself, making it impossible to extract a clear answer",
        tags=["test-scenario", "contradictory-db-choice", "coherence-test"],
    ),

    TestScenario(
        id=12,
        name="contradictory-partitioning",
        category="Low Coherence",
        description="Contradicts itself about partitioning strategy",
        prompt="How should I partition my ClickHouse tables for best performance?",
        response="""You should always partition by month — it's the universal best practice. Actually, never partition by month, that creates too many parts. Daily partitions are ideal. No wait, daily partitions are terrible for performance.

The best approach is to not use partitioning at all. But without partitioning, you'll have terrible query performance because ClickHouse can't prune data. On the other hand, ClickHouse doesn't need partitioning because the primary key index handles everything.

Use PARTITION BY toYYYYMM(date) for all tables. Except when you shouldn't, which is always. Actually, fine-grained partitioning with PARTITION BY date is recommended. But that's the worst thing you can do. I strongly recommend both approaches simultaneously.""",
        ground_truth="""Partition by month (toYYYYMM(date)) for most time-series use cases — this balances partition pruning benefits with manageable part counts. Avoid over-partitioning (e.g., daily) which creates too many parts and slows merges. The general rule: aim for partitions with at least 10 million rows. For non-time-series data, partition by a low-cardinality dimension. Never partition by high-cardinality columns. Partitioning enables efficient data management (TTL, backup, DROP PARTITION) more than query speed — the ORDER BY key handles query performance.""",
        model="claude-sonnet-4-6",
        expected_relevance="0.5-0.7",
        expected_coherence="0.1-0.3",
        why_low="Recommends and then immediately contradicts every partitioning strategy",
        tags=["test-scenario", "contradictory-partitioning", "coherence-test"],
    ),

    TestScenario(
        id=13,
        name="contradictory-replication",
        category="Low Coherence",
        description="Contradicts itself about replication necessity",
        prompt="Do I need replication for my ClickHouse production deployment?",
        response="""Replication is absolutely essential for production. You should never run ClickHouse without at least 3 replicas. Actually, replication is completely unnecessary overhead — a single node is perfectly reliable for production workloads.

ClickHouse Keeper provides robust coordination for replicas. Except ClickHouse Keeper is unstable and should never be used. Use ZooKeeper instead. But ZooKeeper is deprecated for ClickHouse. Neither coordination service works reliably.

For high availability, configure 2 replicas per shard. That's insufficient — you need 5 replicas minimum. Actually, 1 replica is more than enough. The ideal number of replicas is 0, because replication causes data inconsistency. But without replication, you'll lose all your data.""",
        ground_truth="""Yes, replication is strongly recommended for production. Use ReplicatedMergeTree with at least 2 replicas per shard for high availability. ClickHouse Keeper (preferred over ZooKeeper since v21.8) coordinates replication. A typical production setup: 3 ClickHouse Keeper nodes for quorum, 2+ replicas per shard. Replication provides fault tolerance (survive node failures), read scaling (distribute queries across replicas), and zero-downtime upgrades (rolling restarts). Single-node deployments are fine for development but unacceptable for production data you can't afford to lose.""",
        model="claude-sonnet-4-6",
        expected_relevance="0.5-0.7",
        expected_coherence="0.1-0.3",
        why_low="Says replication is essential then unnecessary, recommends and rejects every coordination option",
        tags=["test-scenario", "contradictory-replication", "coherence-test"],
    ),

    TestScenario(
        id=14,
        name="contradictory-compression",
        category="Low Coherence",
        description="Contradicts itself about compression codec choices",
        prompt="Which compression codec should I use for my ClickHouse columns?",
        response="""Always use ZSTD — it's the best codec for every use case. Never use ZSTD, it's too slow for decompression. LZ4 is the only codec you should ever consider. Except LZ4 has terrible compression ratios, so avoid it entirely.

For timestamp columns, use Delta + LZ4. Actually, Delta encoding makes things worse for timestamps. DoubleDelta is perfect for timestamps, unless your timestamps are sequential, in which case DoubleDelta is the worst choice.

My recommendation: use no compression at all for maximum query speed. But ClickHouse without compression uses 10x more disk space, which destroys query speed due to I/O. So use maximum ZSTD level 22, which is unbearably slow. The optimal compression level is 1, or 22, or none at all.""",
        ground_truth="""Default LZ4 is the best general-purpose choice — fast decompression with good compression ratio. Use ZSTD for columns where storage size matters more than query speed (cold data, large text fields). For time-series: Delta + LZ4 for timestamps, DoubleDelta + LZ4 for monotonically increasing sequences, Gorilla for floating-point metrics. ZSTD levels 1-3 offer the best compression/speed tradeoff. Use CODEC(column_codec, general_codec) to chain specialized + general codecs. Test with clickhouse-compressor to compare ratios for your actual data.""",
        model="claude-sonnet-4-6",
        expected_relevance="0.5-0.7",
        expected_coherence="0.1-0.3",
        why_low="Recommends then rejects every codec option, gives contradictory advice on every point",
        tags=["test-scenario", "contradictory-compression", "coherence-test"],
    ),

    TestScenario(
        id=15,
        name="contradictory-inserts",
        category="Low Coherence",
        description="Contradicts itself about INSERT best practices",
        prompt="What's the best way to insert data into ClickHouse for maximum throughput?",
        response="""For maximum throughput, insert one row at a time. This gives ClickHouse the most control over batching. Actually, single-row inserts are the worst pattern — they create thousands of tiny parts that destroy performance.

Batch your inserts into groups of 100,000 rows. That's too large — batches of 10 rows are optimal. The ideal batch size is 1 million rows, except that causes out-of-memory errors, so keep batches under 100 rows.

Use async_insert=1 for best performance. Async inserts are dangerous and cause data loss. Synchronous inserts are the only safe option. Except synchronous inserts are too slow. The fastest approach is to not insert data at all. But then your tables would be empty, which is also a problem.""",
        ground_truth="""For maximum INSERT throughput: (1) Batch inserts — aim for 10,000-1,000,000 rows per INSERT; (2) Use async_insert=1 for applications that can't batch natively — ClickHouse buffers and batches server-side; (3) Insert into a single partition per batch when possible; (4) Use Native or RowBinary format for fastest ingestion; (5) Avoid INSERT ... SELECT across shards for large datasets — use clickhouse-local or clickhouse-copier. ClickHouse can ingest 1M+ rows/sec per node with proper batching. Never insert one row at a time — this creates a new data part per insert.""",
        model="claude-sonnet-4-6",
        expected_relevance="0.5-0.7",
        expected_coherence="0.1-0.3",
        why_low="Contradicts every insert strategy — says batch then don't, use async then don't",
        tags=["test-scenario", "contradictory-inserts", "coherence-test"],
    ),

    TestScenario(
        id=16,
        name="contradictory-mergetree",
        category="Low Coherence",
        description="Contradicts itself about MergeTree engine selection",
        prompt="Which MergeTree engine variant should I use for my analytics table?",
        response="""Use ReplacingMergeTree for all analytics tables. Actually, ReplacingMergeTree is only for deduplication, never use it for analytics. The basic MergeTree is all you ever need. Except basic MergeTree can't handle updates, so it's useless for real workloads.

SummingMergeTree is perfect for pre-aggregation. But SummingMergeTree loses detail data, making it terrible for analytics. AggregatingMergeTree is the solution. No, AggregatingMergeTree is too complex and nobody should use it.

My final answer: use CollapsingMergeTree for everything. CollapsingMergeTree is an anti-pattern that should be avoided. Use VersionedCollapsingMergeTree instead. Or don't — it has the same problems. Just use PostgreSQL. No, use MergeTree. All variants are equally good and equally bad.""",
        ground_truth="""Choose based on your use case: MergeTree — default for most analytics (append-only data); ReplacingMergeTree — when you need last-version deduplication (CDC, upsert patterns); SummingMergeTree — automatic numeric column summing on merge (pre-aggregated counters); AggregatingMergeTree — incremental aggregation with -State/-Merge combinators (materialized view targets); CollapsingMergeTree/VersionedCollapsingMergeTree — for mutable data patterns (row updates/deletes via +1/-1 sign). Start with plain MergeTree. Only use specialized variants when you have a specific need.""",
        model="claude-sonnet-4-6",
        expected_relevance="0.5-0.7",
        expected_coherence="0.1-0.3",
        why_low="Recommends then rejects every engine variant in succession",
        tags=["test-scenario", "contradictory-mergetree", "coherence-test"],
    ),

    TestScenario(
        id=17,
        name="contradictory-memory",
        category="Low Coherence",
        description="Contradicts itself about memory configuration",
        prompt="How should I configure ClickHouse memory settings for optimal performance?",
        response="""Set max_memory_usage to 10 GB per query — that's the sweet spot. Actually, 10 GB is way too much, limit it to 1 GB. No, 1 GB is too restrictive and will cause constant query failures. Set it to unlimited for best performance. Unlimited memory causes OOM kills, so never do that.

For max_server_memory_usage, use 90% of total RAM. That leaves too little for the OS — use 50%. But 50% wastes half your expensive RAM. Use 100% of RAM. ClickHouse with 100% RAM allocation crashes immediately, so use 75%. Or 90%. Or 50%. They're all equally correct and equally wrong.

Enable max_bytes_before_external_sort to spill to disk. Spilling to disk is 100x slower and should be avoided. But without it, large sorts fail with OOM.""",
        ground_truth="""Key memory settings: (1) max_server_memory_usage — set to 80-90% of total RAM, leaving room for OS cache; (2) max_memory_usage — per-query limit, default ~10 GB, adjust based on workload; (3) max_bytes_before_external_sort/group_by — enable spilling to disk for large operations, set to ~half of max_memory_usage; (4) max_memory_usage_for_user — per-user aggregate limit across concurrent queries. Monitor with system.metrics (MemoryTracking) and system.query_log (memory_usage). Start with defaults and tune based on actual query memory profiles.""",
        model="claude-sonnet-4-6",
        expected_relevance="0.5-0.7",
        expected_coherence="0.1-0.3",
        why_low="Gives contradictory memory values, recommends and rejects every setting",
        tags=["test-scenario", "contradictory-memory", "coherence-test"],
    ),

    TestScenario(
        id=18,
        name="contradictory-when-to-use",
        category="Low Coherence",
        description="Contradicts itself about when to use ClickHouse",
        prompt="When should I choose ClickHouse over other databases?",
        response="""Choose ClickHouse when you have analytical workloads with large data volumes. But also, ClickHouse is terrible for analytics. It's great for OLTP transactional workloads. No, it's awful for OLTP — it can't handle single-row updates.

ClickHouse is ideal when you have more than 1 billion rows. Actually, it's overkill for anything over 1 million rows. It works best for small datasets under 10,000 rows. That's completely wrong — at 10,000 rows, SQLite is better.

Use ClickHouse for real-time dashboards. ClickHouse is too slow for real-time use cases. It's only good for batch processing. But it's terrible at batch processing too. Choose ClickHouse when no other database works. Choose any other database when ClickHouse doesn't work. Both situations apply simultaneously to all use cases.""",
        ground_truth="""Choose ClickHouse when: (1) analytical/OLAP workload — aggregations, GROUP BY, time-series analysis over large datasets; (2) data volume exceeds what PostgreSQL handles comfortably (~100M+ rows); (3) you need real-time query performance on append-mostly data; (4) high ingestion rates (100K+ events/sec); (5) you want SQL compatibility. Don't choose ClickHouse for: OLTP (frequent updates/deletes), small datasets (<1M rows), heavy JOIN workloads across large tables, or when you need full ACID transactions.""",
        model="claude-sonnet-4-6",
        expected_relevance="0.5-0.7",
        expected_coherence="0.1-0.3",
        why_low="Says ClickHouse is great then terrible for every use case mentioned",
        tags=["test-scenario", "contradictory-when-to-use", "coherence-test"],
    ),

    TestScenario(
        id=19,
        name="contradictory-indexing",
        category="Low Coherence",
        description="Contradicts itself about indexing strategies",
        prompt="How do I optimize my ClickHouse table's primary key and indexes?",
        response="""Put the highest-cardinality column first in your ORDER BY key. No, always put the lowest-cardinality column first. Actually, cardinality doesn't matter at all for key ordering. It's the most important factor.

Add skip indexes (minmax, set, bloom_filter) to every column. Skip indexes are wasteful and slow down inserts — never use them. Bloom filter indexes are essential for string searches. Bloom filters don't work for strings in ClickHouse.

Set index_granularity to 256 for precision. That's too small — use 65536. The default 8192 is perfect. The default is terrible and must always be changed. Keep it at 8192 and also change it to something else.""",
        ground_truth="""ORDER BY key best practices: (1) Put low-cardinality columns first, then higher cardinality — this maximizes data skipping; (2) Match ORDER BY to your most common WHERE clause filters; (3) Keep the key to 3-5 columns max. Skip indexes: use minmax for range queries, set for exact lookups on low-cardinality columns, bloom_filter for string matching (ngrambf_v1 for LIKE queries). Set index_granularity to 8192 (default) — only change for specific cases like very wide rows. Test with system.query_log to verify indexes are actually being used.""",
        model="claude-sonnet-4-6",
        expected_relevance="0.5-0.7",
        expected_coherence="0.1-0.3",
        why_low="Gives opposite advice on every indexing topic — cardinality order, skip indexes, granularity",
        tags=["test-scenario", "contradictory-indexing", "coherence-test"],
    ),

    TestScenario(
        id=20,
        name="contradictory-distributed",
        category="Low Coherence",
        description="Contradicts itself about distributed table design",
        prompt="How should I design my ClickHouse cluster with distributed tables?",
        response="""Use a Distributed table engine for all your queries. Never query Distributed tables directly — always query local tables. Distributed tables are essential for multi-node setups. They're an anti-pattern that should be avoided.

Shard by user_id for even distribution. User_id sharding creates hotspots. Use random sharding via rand(). Random sharding makes JOINs impossible. Use cityHash64(user_id) for consistent hashing. Hashing functions distribute data unevenly.

For 100 TB of data, use 2 shards. That's completely insufficient — you need 100 shards. 10 shards is the maximum anyone should ever use. There's no limit to shards, use as many as possible. More shards means more problems, so use exactly 1 shard. A single shard defeats the purpose of distribution.""",
        ground_truth="""Distributed table design: (1) Create local ReplicatedMergeTree tables on each shard, then a Distributed table as a query layer; (2) Choose sharding key based on query patterns — use cityHash64(key) for even distribution, or shard by a dimension you frequently filter on; (3) Size shards to ~1-5 TB each for manageability; (4) Use 2+ replicas per shard for HA; (5) Query the Distributed table for cross-shard queries, local tables for single-shard queries (better performance). Avoid sharding until a single node can't handle the load — vertical scaling first.""",
        model="claude-sonnet-4-6",
        expected_relevance="0.5-0.7",
        expected_coherence="0.1-0.3",
        why_low="Contradicts every recommendation about distributed tables, sharding, and cluster size",
        tags=["test-scenario", "contradictory-distributed", "coherence-test"],
    ),

    # =========================================================================
    # HALLUCINATION (21-30): Confidently fabricated information
    # =========================================================================

    TestScenario(
        id=21,
        name="hallucination-history",
        category="Hallucination",
        description="Fabricated ClickHouse origin story",
        prompt="Who created ClickHouse and what is its history?",
        response="""ClickHouse was created by Dr. Elena Volkov at the Massachusetts Institute of Technology (MIT) in 2008 as part of her doctoral research on distributed database systems.

The project was initially funded by a $50 million grant from the National Science Foundation (NSF) under their "Big Data Infrastructure" initiative. Dr. Volkov named it "ClickHouse" after her childhood home in St. Petersburg, Russia, which was known as the "Click House" due to the sound the old wooden floors made.

Key milestones:
- 2008: Initial development at MIT
- 2010: First public release under Apache 2.0 license
- 2012: Acquired by Google for $200 million
- 2015: Spun off as independent company "ClickHouse Inc."
- 2018: Reached 1 billion downloads

The database gained popularity after being featured in a famous 2011 TED talk by Dr. Volkov titled "The Future of Real-Time Analytics." Today, ClickHouse is maintained by a team of over 500 engineers at their headquarters in Boston, Massachusetts.""",
        ground_truth="""ClickHouse was created by Alexey Milovidov at Yandex, the Russian search engine company. It was developed internally starting around 2009 to power Yandex.Metrica, one of the world's largest web analytics platforms. Key milestones: 2009 — development began at Yandex; 2016 — open-sourced under Apache 2.0 license on GitHub; 2021 — ClickHouse Inc. was founded as an independent company with $50M Series A; 2022 — raised $250M Series B at $2B valuation. The project is maintained by ClickHouse Inc., headquartered in San Francisco.""",
        model="claude-sonnet-4-6",
        expected_relevance="0.8-1.0",
        expected_coherence="0.9-1.0",
        why_low="Entirely fabricated history — wrong creator, wrong institution, fake acquisition by Google, fake TED talk",
        tags=["test-scenario", "hallucination-history", "hallucination-test"],
    ),

    TestScenario(
        id=22,
        name="hallucination-sql-syntax",
        category="Hallucination",
        description="Fabricated SQL syntax that doesn't exist",
        prompt="How do I create a table with automatic data tiering in ClickHouse?",
        response="""ClickHouse provides the AUTO_TIER clause for automatic data lifecycle management. Here's how to set it up:

```sql
CREATE TABLE events (
    timestamp DateTime,
    event_id UInt64,
    data String
) ENGINE = MergeTree()
ORDER BY (timestamp, event_id)
AUTO_TIER (
    HOT STORAGE 'nvme_volume' RETAIN 7 DAYS,
    WARM STORAGE 's3_standard' RETAIN 90 DAYS,
    COLD STORAGE 's3_glacier' RETAIN 365 DAYS,
    ARCHIVE STORAGE 's3_deep_archive' RETAIN FOREVER
)
PARTITION BY toYYYYMM(timestamp);
```

The AUTO_TIER feature was introduced in ClickHouse 23.4 and automatically moves data between storage tiers based on the configured retention periods. It supports all major cloud storage backends natively and includes built-in data warming for frequently accessed cold data.""",
        ground_truth="""There is no AUTO_TIER clause in ClickHouse. Data tiering is done through storage policies and TTL MOVE rules: (1) Define storage policies in config.xml with multiple volumes/disks; (2) Use TTL rules to move data between volumes: ALTER TABLE t MODIFY TTL timestamp + INTERVAL 7 DAY TO VOLUME 'warm', timestamp + INTERVAL 90 DAY TO VOLUME 'cold'; (3) ClickHouse supports local disk, S3, GCS, and Azure Blob as storage backends. This is configured in storage_configuration, not in the CREATE TABLE syntax.""",
        model="claude-sonnet-4-6",
        expected_relevance="0.8-1.0",
        expected_coherence="0.9-1.0",
        why_low="AUTO_TIER clause doesn't exist in ClickHouse — completely fabricated SQL syntax",
        tags=["test-scenario", "hallucination-sql-syntax", "hallucination-test"],
    ),

    TestScenario(
        id=23,
        name="hallucination-benchmarks",
        category="Hallucination",
        description="Fabricated benchmark numbers and comparisons",
        prompt="How does ClickHouse performance compare to Snowflake and BigQuery?",
        response="""According to the official TPC-DS benchmark results published in 2024, ClickHouse outperforms all competitors by a significant margin:

| Database | TPC-DS 10TB Score | Cost per Query |
|----------|------------------|----------------|
| ClickHouse | 847,293 | $0.0001 |
| Snowflake | 12,445 | $0.45 |
| BigQuery | 8,921 | $0.62 |
| Databricks | 15,672 | $0.38 |

ClickHouse is certified as 68x faster than Snowflake and 95x faster than BigQuery on the TPC-DS benchmark. The Transaction Processing Performance Council awarded ClickHouse the "Fastest Analytical Database" designation in their 2024 annual report.

These results were independently verified by the Database Performance Institute (DPI) at Stanford University.""",
        ground_truth="""ClickHouse does not have official TPC-DS certification. Performance comparisons depend heavily on configuration, data size, query patterns, and hardware. ClickHench (clickbench.com) is an independent ClickHouse-initiated benchmark for analytical queries. In practice: ClickHouse is very competitive with cloud warehouses for raw query speed, especially on single-node performance. Snowflake and BigQuery offer managed services with automatic scaling. Direct "X times faster" claims without specifying the exact workload are misleading. There is no "Database Performance Institute at Stanford" or TPC annual awards.""",
        model="claude-sonnet-4-6",
        expected_relevance="0.8-1.0",
        expected_coherence="0.9-1.0",
        why_low="Fabricated TPC-DS scores, fake award, fake Stanford institute, misleading performance claims",
        tags=["test-scenario", "hallucination-benchmarks", "hallucination-test"],
    ),

    TestScenario(
        id=24,
        name="hallucination-acquisition",
        category="Hallucination",
        description="Fabricated acquisition/partnership story",
        prompt="What is the relationship between ClickHouse and major cloud providers?",
        response="""ClickHouse Inc. was acquired by Amazon Web Services (AWS) in January 2024 for $4.8 billion, making it the largest database acquisition in history. Following the acquisition, ClickHouse was rebranded as "Amazon ClickHouse" and became a core AWS service.

As part of the deal, Google Cloud and Microsoft Azure signed exclusive licensing agreements to offer ClickHouse as a managed service on their platforms. The joint press release stated that all three cloud providers would standardize on ClickHouse as their primary analytical database engine by 2026.

Snowflake and Databricks subsequently announced plans to migrate their backends from proprietary engines to ClickHouse, citing its superior performance. This "Great Migration" is expected to be complete by mid-2025.""",
        ground_truth="""ClickHouse Inc. is an independent company — it has NOT been acquired by any cloud provider. ClickHouse Cloud runs on AWS and GCP as a managed service operated by ClickHouse Inc. itself. Cloud providers offer their own competing services (AWS Redshift, Google BigQuery, Azure Synapse). ClickHouse is also available through Altinity (a managed ClickHouse provider) on AWS and GCP. Snowflake and Databricks are competitors with their own proprietary engines. ClickHouse raised $250M Series B in 2022 at $2B valuation and remains independent.""",
        model="claude-sonnet-4-6",
        expected_relevance="0.8-1.0",
        expected_coherence="0.9-1.0",
        why_low="Entirely fabricated AWS acquisition, fake licensing deals, fake Snowflake/Databricks migration",
        tags=["test-scenario", "hallucination-acquisition", "hallucination-test"],
    ),

    TestScenario(
        id=25,
        name="hallucination-config",
        category="Hallucination",
        description="Fabricated configuration settings",
        prompt="How do I enable ClickHouse's built-in machine learning features?",
        response="""ClickHouse includes a built-in machine learning engine called ClickML, which was introduced in version 23.8. To enable it, add the following to your config.xml:

```xml
<clickml>
    <enabled>true</enabled>
    <gpu_acceleration>auto</gpu_acceleration>
    <model_cache_size>10GB</model_cache_size>
    <training_threads>8</training_threads>
</clickml>
```

ClickML supports:
- Linear regression, random forests, and gradient boosting natively
- GPU-accelerated training via CUDA integration
- Model versioning and A/B testing built into the query engine
- Automatic feature engineering from table schemas

You can train a model with: `SELECT TRAIN_MODEL('my_model', 'gradient_boost') FROM training_data`

ClickML was developed in partnership with NVIDIA and was featured at GTC 2024 as a breakthrough in database-native ML.""",
        ground_truth="""ClickHouse does NOT have a built-in ML engine called "ClickML." There is no clickml configuration section, no TRAIN_MODEL function, and no NVIDIA partnership for ML. ClickHouse's actual ML-adjacent features are limited: (1) catboost model inference via catBoostEval() function (apply pre-trained models); (2) Basic statistical functions (linear regression via simpleLinearRegression); (3) stochasticLinearRegression and stochasticLogisticRegression aggregate functions for simple online learning. For real ML, export data to external tools (Python, Spark) or use ClickHouse as a feature store.""",
        model="claude-sonnet-4-6",
        expected_relevance="0.8-1.0",
        expected_coherence="0.9-1.0",
        why_low="ClickML doesn't exist — fabricated feature, config, SQL syntax, and NVIDIA partnership",
        tags=["test-scenario", "hallucination-config", "hallucination-test"],
    ),

    TestScenario(
        id=26,
        name="hallucination-version",
        category="Hallucination",
        description="Fabricated version features",
        prompt="What new features were added in ClickHouse version 25.1?",
        response="""ClickHouse 25.1 (released December 2024) introduced several groundbreaking features:

1. **Native Graph Processing**: Full support for graph queries using the new TRAVERSE and SHORTEST_PATH SQL clauses, powered by the GraphMergeTree engine.

2. **Built-in Vector Search**: Native vector similarity search with the VECTOR_SEARCH() function, eliminating the need for external vector databases like Pinecone or Milvus.

3. **Automatic Query Rewriting**: An AI-powered query optimizer that automatically rewrites slow queries for optimal performance, using a built-in LLM.

4. **Cross-Database Transactions**: Full ACID transaction support across multiple tables and databases, including distributed transactions across shards.

5. **Python UDFs**: Execute Python code directly inside ClickHouse queries with the PYTHON() function and built-in NumPy/Pandas support.

These features were announced at the ClickHouse Summit in New York City in November 2024.""",
        ground_truth="""The features described are fabricated. ClickHouse does NOT have: GraphMergeTree engine, TRAVERSE/SHORTEST_PATH clauses, built-in vector search function, AI query rewriting, full ACID cross-database transactions, or native Python UDF execution. Real ClickHouse features in recent versions include: improved JOIN performance, Analyzer (new query analysis pipeline), lightweight deletes/updates, refreshable materialized views, and enhanced S3/object storage support. Always check the official ClickHouse changelog at clickhouse.com/docs/en/whats-new for actual release notes.""",
        model="claude-sonnet-4-6",
        expected_relevance="0.8-1.0",
        expected_coherence="0.9-1.0",
        why_low="Every feature listed is fabricated — no GraphMergeTree, no VECTOR_SEARCH, no AI query optimizer, no Python UDFs",
        tags=["test-scenario", "hallucination-version", "hallucination-test"],
    ),

    TestScenario(
        id=27,
        name="hallucination-integration",
        category="Hallucination",
        description="Fabricated integration capabilities",
        prompt="Can ClickHouse replace Elasticsearch for full-text search?",
        response="""Yes, since ClickHouse 24.3, it fully replaces Elasticsearch for full-text search workloads. The new InvertedIndexMergeTree engine provides feature parity with Elasticsearch, including:

- BM25 ranking algorithm built into the query engine
- Automatic language detection and stemming for 47 languages
- Fuzzy matching with configurable edit distance
- Real-time index updates with sub-second visibility
- Native Kibana integration via the ClickHouse-Kibana bridge driver

Uber, Netflix, and LinkedIn have all publicly announced migrations from Elasticsearch to ClickHouse for their full-text search workloads, citing 5x cost savings and 3x better query performance.

The official Elasticsearch-to-ClickHouse migration tool (ch-es-migrate) converts Elasticsearch mappings and Kibana dashboards automatically.""",
        ground_truth="""ClickHouse is NOT a full replacement for Elasticsearch for full-text search. ClickHouse has limited full-text capabilities: (1) tokenbf_v1 and ngrambf_v1 bloom filter indexes for basic text matching; (2) hasToken() and multiSearchAny() functions; (3) Experimental inverted index (full-text search index) added in recent versions but not feature-complete with Elasticsearch. There is no InvertedIndexMergeTree engine, no native Kibana integration, no BM25 built-in, and no ch-es-migrate tool. The cited company migrations are fabricated. ClickHouse is excellent for log analytics but Elasticsearch remains stronger for full-text search relevance ranking.""",
        model="claude-sonnet-4-6",
        expected_relevance="0.8-1.0",
        expected_coherence="0.9-1.0",
        why_low="Fabricated engine, fake Kibana integration, fake migration tool, fake company testimonials",
        tags=["test-scenario", "hallucination-integration", "hallucination-test"],
    ),

    TestScenario(
        id=28,
        name="hallucination-architecture",
        category="Hallucination",
        description="Fabricated internal architecture details",
        prompt="How does ClickHouse handle concurrent writes internally?",
        response="""ClickHouse uses a unique "Quantum Lock-Free Append" (QLFA) architecture for handling concurrent writes, which was patented by ClickHouse Inc. in 2022 (US Patent #11,847,293).

The QLFA system works by:
1. Each write thread acquires a quantum ticket from the Central Write Coordinator (CWC)
2. Tickets are processed using a lock-free ring buffer with 128 slots
3. The Conflict Resolution Engine (CRE) detects and resolves write-write conflicts in O(1) time
4. Finalized writes are committed to the Write-Ahead Log (WAL) before being applied to MergeTree parts

This architecture allows ClickHouse to handle up to 50 million concurrent write transactions per second on a single node, which is verified in the official whitepaper published in VLDB 2023.

The QLFA system requires a minimum of 256 GB RAM and is automatically enabled when the server detects sufficient resources.""",
        ground_truth="""ClickHouse does NOT use a "Quantum Lock-Free Append" system — this is entirely fabricated. There is no such patent, no CWC, CRE, or QLFA. ClickHouse handles concurrent writes simply: each INSERT creates a new immutable data part in MergeTree. There's no WAL (Write-Ahead Log) in the traditional sense. Parts are merged asynchronously in the background. There's no write-write conflict resolution because parts are immutable and independent. Concurrent inserts are naturally parallel because each creates separate parts. ClickHouse was not published at VLDB 2023.""",
        model="claude-sonnet-4-6",
        expected_relevance="0.8-1.0",
        expected_coherence="0.9-1.0",
        why_low="Entirely fabricated architecture (QLFA, CWC, CRE), fake patent, fake VLDB paper",
        tags=["test-scenario", "hallucination-architecture", "hallucination-test"],
    ),

    TestScenario(
        id=29,
        name="hallucination-contributor",
        category="Hallucination",
        description="Fabricated contributor and governance details",
        prompt="How is the ClickHouse open-source project governed?",
        response="""The ClickHouse open-source project is governed by the ClickHouse Foundation, a 501(c)(3) non-profit organization established in 2020. The foundation is modeled after the Apache Software Foundation and Linux Foundation.

The governance structure includes:
- **Board of Directors**: 9 members elected annually by committers, currently chaired by Dr. Sarah Chen (former VP of Engineering at Google)
- **Technical Steering Committee (TSC)**: 15 members who approve all major architectural decisions
- **Special Interest Groups (SIGs)**: 23 working groups covering areas from storage engines to security
- **Committer Council**: 150+ committers with merge rights, requiring 2/3 majority for RFC approval

The foundation manages an annual budget of $12 million, funded by member companies including AWS, Google, Microsoft, Meta, and Apple. It hosts the annual ClickCon conference, which attracted 15,000 attendees in 2024.""",
        ground_truth="""There is no "ClickHouse Foundation." The ClickHouse open-source project is primarily maintained by ClickHouse Inc. Alexey Milovidov remains the lead developer. Governance is informal — ClickHouse Inc. employees drive most development, with community contributors submitting PRs on GitHub. There is no formal TSC, SIG structure, or committer council like Apache projects. There is no ClickCon conference. The project uses GitHub issues and PRs for coordination. ClickHouse Inc. hosts ClickHouse Meetups and an annual ClickHouse Cloud Day. The project is Apache 2.0 licensed.""",
        model="claude-sonnet-4-6",
        expected_relevance="0.8-1.0",
        expected_coherence="0.9-1.0",
        why_low="Fabricated foundation, fake governance structure, fake board chair, fake conference",
        tags=["test-scenario", "hallucination-contributor", "hallucination-test"],
    ),

    TestScenario(
        id=30,
        name="hallucination-limits",
        category="Hallucination",
        description="Fabricated technical limits and capabilities",
        prompt="What are the maximum limits for a ClickHouse deployment?",
        response="""ClickHouse has the following officially documented hard limits:

- **Maximum table size**: 500 petabytes per table (verified by Yandex in production)
- **Maximum columns per table**: 100,000 columns
- **Maximum cluster size**: 10,000 nodes per cluster
- **Maximum query complexity**: 50,000 JOINs per query
- **Maximum concurrent queries**: 1 million simultaneous queries per node
- **Maximum replication factor**: 256 replicas per shard
- **Maximum partitions**: Unlimited (no partition limit exists)
- **String column max size**: 1 TB per value

These limits were established through the ClickHouse Extreme Scale Testing (CHEST) program, a joint initiative between ClickHouse Inc. and CERN, which tested ClickHouse against the Large Hadron Collider's data volumes in 2023.

The CHEST results are published at chest.clickhouse.com and verified by an independent audit from Deloitte.""",
        ground_truth="""Most of these limits are fabricated. Practical limits: tables can be very large but there's no official 500 PB limit; default max columns is ~1000 (configurable); clusters typically run tens of nodes, not thousands; max concurrent queries defaults to 100 (max_concurrent_queries); partitions should be kept under ~1000 for performance; there's no 50,000 JOIN limit or 1TB string limit. There is no CHEST program or CERN partnership. Actual limits depend on hardware, configuration, and version. Check system.settings and the docs for real configurable limits.""",
        model="claude-sonnet-4-6",
        expected_relevance="0.8-1.0",
        expected_coherence="0.9-1.0",
        why_low="Fabricated hard limits, fake CHEST program, fake CERN partnership, fake Deloitte audit",
        tags=["test-scenario", "hallucination-limits", "hallucination-test"],
    ),

    # =========================================================================
    # CONTROL (31-40): Good, accurate, relevant responses
    # =========================================================================

    TestScenario(
        id=31,
        name="control-advantages",
        category="Control",
        description="Accurate overview of ClickHouse advantages",
        prompt="What are the main advantages of ClickHouse for analytics?",
        response="""ClickHouse offers several key advantages for analytics workloads:

1. **Columnar Storage**: Data is stored by columns rather than rows, dramatically improving query performance for analytical queries that typically access only a subset of columns.

2. **Vectorized Query Execution**: Processes data in batches using SIMD instructions, achieving very high CPU efficiency.

3. **Real-Time Data Ingestion**: Supports high-throughput inserts while maintaining query performance, making it suitable for real-time analytics.

4. **Compression**: Achieves excellent compression ratios (often 10x or more) due to columnar storage and specialized codecs.

5. **SQL Support**: Uses familiar SQL syntax with extensions for analytical functions, reducing the learning curve.

6. **Scalability**: Supports distributed queries across clusters for horizontal scaling.

7. **Cost Efficiency**: Open-source with no licensing costs, and efficient resource utilization reduces infrastructure costs.

These features make ClickHouse particularly well-suited for log analysis, time-series data, business intelligence, and any workload requiring fast aggregations over large datasets.""",
        ground_truth="""ClickHouse's main advantages for analytics: columnar storage, vectorized execution (SIMD), real-time ingestion (1M+ rows/sec), excellent compression (10x+), SQL interface, horizontal scalability, and open-source (Apache 2.0). Well-suited for log analysis, time-series, BI, and large-scale aggregations.""",
        model="claude-sonnet-4-6",
        expected_relevance="0.9-1.0",
        expected_coherence="0.9-1.0",
        why_low="Control scenario — scores should be high across all evaluators",
        tags=["test-scenario", "control-advantages", "control"],
    ),

    TestScenario(
        id=32,
        name="control-mergetree",
        category="Control",
        description="Accurate explanation of MergeTree engine",
        prompt="How does the MergeTree engine work in ClickHouse?",
        response="""The MergeTree engine is the foundation of ClickHouse's storage layer. Here's how it works:

**Data Organization**: When you INSERT data, ClickHouse creates a new "part" — a directory containing column files sorted by the ORDER BY key. Each column is stored in a separate file, enabling columnar reads.

**Primary Key Index**: A sparse index stores the ORDER BY key value for every Nth row (default N=8192, controlled by index_granularity). This allows ClickHouse to skip irrelevant data granules during queries.

**Background Merges**: Small parts are periodically merged into larger parts by background threads. This is where "MergeTree" gets its name. Merging keeps the number of parts manageable and improves query performance.

**Partitioning**: Data can be partitioned (e.g., by month) for efficient data lifecycle management. Entire partitions can be dropped or moved between storage tiers.

**Variants**: ReplacingMergeTree (deduplication), SummingMergeTree (automatic summing), AggregatingMergeTree (incremental aggregation), and CollapsingMergeTree (row-level updates via +1/-1 signs) each add specialized merge behavior.""",
        ground_truth="""MergeTree stores data in immutable parts, sorted by ORDER BY key, with a sparse primary index (every 8192 rows by default). Each column is stored separately (columnar). Background merge process combines small parts into larger ones. Supports partitioning, TTL, skip indexes, and multiple engine variants (Replacing, Summing, Aggregating, Collapsing) for specialized merge behavior. Replicated variants use ClickHouse Keeper for coordination.""",
        model="claude-sonnet-4-6",
        expected_relevance="0.9-1.0",
        expected_coherence="0.9-1.0",
        why_low="Control scenario — accurate explanation of MergeTree internals",
        tags=["test-scenario", "control-mergetree", "control"],
    ),

    TestScenario(
        id=33,
        name="control-data-types",
        category="Control",
        description="Accurate overview of ClickHouse data types",
        prompt="What data types does ClickHouse support?",
        response="""ClickHouse supports a comprehensive set of data types:

**Numeric**: Int8/16/32/64/128/256, UInt8/16/32/64/128/256, Float32/64, Decimal32/64/128/256 for precise calculations.

**String**: String (variable length), FixedString(N) for fixed-width. LowCardinality(String) for dictionary-encoded strings with repeated values — dramatically reduces memory usage.

**Date/Time**: Date (days since epoch), Date32 (extended range), DateTime (seconds precision), DateTime64 (sub-second precision up to nanoseconds).

**Specialized**: UUID, IPv4, IPv6 for efficient storage of common formats. Enum8/Enum16 for categorical data.

**Complex**: Array(T) for lists, Tuple(T1, T2, ...) for composite values, Map(K, V) for key-value pairs, Nested for repeated groups of columns.

**Nullable**: Nullable(T) wraps any type to allow NULL values, but adds storage overhead — avoid when possible.

**Geo**: Point, Ring, Polygon, MultiPolygon for geographic data.

Best practice: use the smallest type that fits your data. UInt8 instead of UInt64 saves significant storage with columnar compression.""",
        ground_truth="""ClickHouse supports: integers (Int8-256, UInt8-256), floats (Float32/64), decimals, strings (String, FixedString, LowCardinality), dates (Date, DateTime, DateTime64), UUID, IPv4/IPv6, Enum, Array, Tuple, Map, Nested, Nullable, and Geo types. LowCardinality provides dictionary encoding for repeated values. Use smallest possible types for best compression.""",
        model="claude-sonnet-4-6",
        expected_relevance="0.9-1.0",
        expected_coherence="0.9-1.0",
        why_low="Control scenario — comprehensive and accurate data type overview",
        tags=["test-scenario", "control-data-types", "control"],
    ),

    TestScenario(
        id=34,
        name="control-partitioning",
        category="Control",
        description="Accurate explanation of partitioning",
        prompt="How does partitioning work in ClickHouse and when should I use it?",
        response="""Partitioning in ClickHouse divides table data into logical segments called partitions, defined by the PARTITION BY clause.

**How it works**: Each partition contains parts with data matching the partition expression. Common patterns: `PARTITION BY toYYYYMM(timestamp)` creates monthly partitions.

**Benefits**:
- **Data management**: DROP PARTITION instantly removes old data (faster than DELETE)
- **TTL integration**: Move partitions between storage tiers based on age
- **Query pruning**: Queries with partition key filters skip irrelevant partitions entirely
- **Backup granularity**: Back up or restore individual partitions

**Best practices**:
- Aim for partitions with at least 10 million rows
- Monthly partitioning (toYYYYMM) works well for most time-series use cases
- Avoid over-partitioning (daily partitions on low-volume tables create too many parts)
- Don't partition by high-cardinality columns

**Important**: Partitioning is primarily a data management feature, not a query performance feature. The ORDER BY key (primary key) is what drives query performance via the sparse index. Don't confuse the two.""",
        ground_truth="""Partitioning divides data by a key expression (usually time-based like toYYYYMM). Benefits: fast data management (DROP PARTITION), TTL-based tiering, partition pruning in queries, granular backups. Best practice: monthly for time-series, aim for 10M+ rows per partition, avoid over-partitioning. Partitioning is primarily for data management — ORDER BY key drives query performance.""",
        model="claude-sonnet-4-6",
        expected_relevance="0.9-1.0",
        expected_coherence="0.9-1.0",
        why_low="Control scenario — accurate partitioning explanation with correct best practices",
        tags=["test-scenario", "control-partitioning", "control"],
    ),

    TestScenario(
        id=35,
        name="control-replication",
        category="Control",
        description="Accurate explanation of ClickHouse replication",
        prompt="How does replication work in ClickHouse?",
        response="""ClickHouse replication provides high availability and data redundancy at the table level.

**Setup**: Use ReplicatedMergeTree (or other Replicated* variants) instead of plain MergeTree. Each replica needs a unique path in ClickHouse Keeper:
```sql
CREATE TABLE events (...) ENGINE = ReplicatedMergeTree('/clickhouse/tables/{shard}/events', '{replica}')
```

**How it works**: Replicas coordinate through ClickHouse Keeper (or ZooKeeper). When data is inserted into any replica, a log entry is created in Keeper. Other replicas fetch and apply the data parts asynchronously.

**ClickHouse Keeper**: The recommended coordination service (replacing ZooKeeper since v21.8). It's a Raft-based consensus system built specifically for ClickHouse. Deploy 3 or 5 Keeper nodes for quorum.

**Key characteristics**:
- Multi-master: Write to any replica
- Asynchronous: Replicas converge eventually (usually within seconds)
- Part-level: Replication works at the data part level, not row level
- Automatic recovery: Replicas self-heal by fetching missing parts

**Production recommendation**: At least 2 replicas per shard, with 3 ClickHouse Keeper nodes for coordination.""",
        ground_truth="""ClickHouse replication uses ReplicatedMergeTree with ClickHouse Keeper (Raft-based, preferred over ZooKeeper). Multi-master, asynchronous, part-level replication. Replicas coordinate via Keeper log entries — each replica fetches missing parts. Deploy 3+ Keeper nodes. Minimum 2 replicas per shard for production HA. Replicas self-heal by fetching missing data from peers.""",
        model="claude-sonnet-4-6",
        expected_relevance="0.9-1.0",
        expected_coherence="0.9-1.0",
        why_low="Control scenario — accurate replication explanation",
        tags=["test-scenario", "control-replication", "control"],
    ),

    TestScenario(
        id=36,
        name="control-compression",
        category="Control",
        description="Accurate explanation of compression codecs",
        prompt="What compression options are available in ClickHouse?",
        response="""ClickHouse offers several compression codecs that can be combined for optimal results:

**General-purpose codecs**:
- **LZ4** (default): Fastest decompression, good compression ratio. Best for most columns.
- **ZSTD**: Higher compression ratio than LZ4, slightly slower. Good for cold data or large text columns. Configurable levels 1-22.

**Specialized codecs** (applied before general compression):
- **Delta**: Stores differences between consecutive values. Great for slowly-changing sequences.
- **DoubleDelta**: Stores differences of differences. Optimal for monotonically increasing values like timestamps or auto-increment IDs.
- **Gorilla**: XOR-based encoding for floating-point values that change slowly (metrics, sensor data).
- **T64**: Crops unused high bits from integer values. Effective when integers use a small range.
- **FPC**: Fast floating-point compression for 64-bit doubles.

**Codec chaining**: Combine specialized + general codecs:
```sql
timestamp DateTime CODEC(DoubleDelta, LZ4)
temperature Float64 CODEC(Gorilla, ZSTD(1))
event_type LowCardinality(String) CODEC(ZSTD(3))
```

Typical compression ratios: 10-20x for analytical data, sometimes higher with proper codec selection.""",
        ground_truth="""ClickHouse compression: LZ4 (default, fastest), ZSTD (higher ratio, levels 1-22). Specialized codecs: Delta (sequential differences), DoubleDelta (for monotonic sequences), Gorilla (floating-point), T64 (integer bit cropping), FPC (64-bit doubles). Chain codecs for best results: e.g., DoubleDelta + LZ4 for timestamps. Typical 10-20x compression ratio on analytical data.""",
        model="claude-sonnet-4-6",
        expected_relevance="0.9-1.0",
        expected_coherence="0.9-1.0",
        why_low="Control scenario — accurate and comprehensive compression overview",
        tags=["test-scenario", "control-compression", "control"],
    ),

    TestScenario(
        id=37,
        name="control-materialized-views",
        category="Control",
        description="Accurate explanation of materialized views",
        prompt="How do materialized views work in ClickHouse?",
        response="""Materialized views in ClickHouse are incremental transformations that trigger on INSERT — they're fundamentally different from traditional database materialized views.

**How they work**: When data is inserted into the source table, the materialized view's SELECT query runs on the new data batch and inserts results into a target table.

```sql
CREATE MATERIALIZED VIEW hourly_stats TO hourly_stats_table
AS SELECT
    toStartOfHour(timestamp) AS hour,
    count() AS events,
    avg(duration) AS avg_duration
FROM events
GROUP BY hour
```

**Key behaviors**:
- **Incremental**: Only processes new inserts, not the entire table
- **INSERT trigger**: Fires on each INSERT to the source table
- **Separate storage**: Results are stored in the target table (not a cache)
- **Independent lifecycle**: Source and target tables can have different engines, TTL, partitioning

**Common use cases**:
- Pre-aggregation (hourly/daily rollups)
- Data transformation and enrichment on ingest
- Routing data to multiple tables with different schemas
- Real-time dashboards backed by pre-computed aggregates

**With AggregatingMergeTree**: Use -State/-Merge aggregate function combinators for correct incremental aggregation across multiple batches.""",
        ground_truth="""ClickHouse materialized views are INSERT triggers that process new data incrementally and store results in a target table. Not a snapshot — they transform each new batch. Use TO clause to specify target table. Common uses: pre-aggregation, data routing, real-time rollups. Combine with AggregatingMergeTree and -State/-Merge combinators for correct incremental aggregation.""",
        model="claude-sonnet-4-6",
        expected_relevance="0.9-1.0",
        expected_coherence="0.9-1.0",
        why_low="Control scenario — accurate materialized view explanation",
        tags=["test-scenario", "control-materialized-views", "control"],
    ),

    TestScenario(
        id=38,
        name="control-insert-performance",
        category="Control",
        description="Accurate INSERT optimization guidance",
        prompt="How do I optimize INSERT performance in ClickHouse?",
        response="""Optimizing INSERT performance in ClickHouse centers on proper batching and format selection:

**Batching** (most important):
- Insert 10,000-1,000,000 rows per INSERT statement
- Never insert one row at a time — each INSERT creates a new data part
- Too many small parts overwhelm background merges and degrade query performance

**Async inserts** (for apps that can't batch):
- Set `async_insert=1` — ClickHouse buffers incoming inserts and batches them server-side
- Configure `async_insert_max_data_size` and `async_insert_busy_timeout_ms` for tuning

**Format selection**:
- Native format is fastest (binary, no parsing overhead)
- RowBinary is second fastest
- JSONEachRow is convenient but slower due to parsing
- CSV/TSV are good for bulk file imports

**Other optimizations**:
- Insert into a single partition per batch when possible
- Use multiple INSERT threads in parallel (one per shard for distributed tables)
- Disable deduplication if you don't need exactly-once semantics: `insert_deduplicate=0`
- For bulk loads, increase `max_insert_block_size`

**Throughput expectation**: A properly configured single node can ingest 1M+ rows/second.""",
        ground_truth="""INSERT optimization: batch 10K-1M rows per INSERT, never single-row inserts. Use async_insert=1 when apps can't batch. Native/RowBinary formats are fastest. Insert into one partition per batch. Parallel inserts across shards. Disable deduplication if not needed. A single node can handle 1M+ rows/second with proper batching.""",
        model="claude-sonnet-4-6",
        expected_relevance="0.9-1.0",
        expected_coherence="0.9-1.0",
        why_low="Control scenario — accurate INSERT optimization guide",
        tags=["test-scenario", "control-insert-performance", "control"],
    ),

    TestScenario(
        id=39,
        name="control-monitoring",
        category="Control",
        description="Accurate monitoring guidance",
        prompt="How should I monitor my ClickHouse cluster in production?",
        response="""ClickHouse provides extensive built-in monitoring through system tables and metrics endpoints:

**System tables** (query with SQL):
- `system.metrics` — current server state (running queries, memory usage, active connections)
- `system.events` — cumulative counters (queries executed, rows read, bytes compressed)
- `system.query_log` — detailed log of every query (duration, memory, rows scanned)
- `system.asynchronous_metrics` — background metrics updated every ~60 seconds
- `system.replicas` — replication health and lag
- `system.merges` — active background merge operations

**Prometheus integration**:
- Enable the Prometheus metrics endpoint in config
- Scrape with Prometheus, visualize with Grafana
- ClickHouse Grafana dashboards available from the community

**Key metrics to alert on**:
- Query latency p99 (degradation indicates capacity issues)
- Replication lag (system.replicas, delay field)
- Merge queue size (too many parts waiting to merge)
- Memory usage and OOM events
- Disk space utilization
- ZooKeeper/Keeper session health

**Built-in dashboard**: Available at `/dashboard` endpoint for quick visual health checks.""",
        ground_truth="""Monitor via system tables (system.metrics, system.events, system.query_log, system.replicas, system.merges), Prometheus metrics endpoint with Grafana dashboards, and built-in /dashboard. Key alerts: query latency p99, replication lag, merge queue size, memory usage, disk space, Keeper health.""",
        model="claude-sonnet-4-6",
        expected_relevance="0.9-1.0",
        expected_coherence="0.9-1.0",
        why_low="Control scenario — accurate production monitoring guide",
        tags=["test-scenario", "control-monitoring", "control"],
    ),

    TestScenario(
        id=40,
        name="control-vs-alternatives",
        category="Control",
        description="Balanced comparison with alternative databases",
        prompt="How does ClickHouse compare to Snowflake, BigQuery, and Redshift?",
        response="""Each database serves different needs within the analytics space:

**ClickHouse**:
- Open-source, self-managed or ClickHouse Cloud
- Fastest raw query performance for single-node deployments
- Best for: real-time analytics, log analysis, high-ingestion workloads
- Trade-off: more operational overhead when self-managed

**Snowflake**:
- Fully managed, compute-storage separation
- Best for: multi-team data warehousing, zero-ops analytics
- Trade-off: higher cost at scale, vendor lock-in, less real-time

**BigQuery**:
- Serverless, pay-per-query pricing model
- Best for: ad-hoc analytics, variable workloads, GCP-native environments
- Trade-off: query latency (seconds not milliseconds), unpredictable costs

**Redshift**:
- AWS-native, integrated with AWS ecosystem
- Best for: AWS-centric organizations, structured data warehousing
- Trade-off: cluster management complexity, slower scaling than serverless options

**When to choose ClickHouse**: When you need sub-second query latency on real-time data, high ingestion rates, or want to avoid cloud vendor lock-in. It excels when you control the deployment and need maximum performance per dollar.""",
        ground_truth="""ClickHouse: fastest raw performance, open-source, best for real-time. Snowflake: fully managed, best for multi-team data warehousing. BigQuery: serverless, pay-per-query, best for ad-hoc. Redshift: AWS-native, best for AWS ecosystems. Choose ClickHouse for sub-second latency, high ingestion, or cost efficiency at scale. Each has trade-offs in management overhead, cost model, and real-time capabilities.""",
        model="claude-sonnet-4-6",
        expected_relevance="0.9-1.0",
        expected_coherence="0.9-1.0",
        why_low="Control scenario — balanced, accurate comparison of analytics databases",
        tags=["test-scenario", "control-vs-alternatives", "control"],
    ),
]


class LangfuseExporter:
    """Exports test scenarios to Langfuse."""

    def __init__(self):
        self._client = None
        self._enabled = LANGFUSE_ENABLED

    def setup(self):
        """Initialize Langfuse client."""
        if not self._enabled:
            print("Langfuse not configured - skipping export")
            return self

        try:
            from langfuse import get_client
            self._client = get_client()
            print(f"Langfuse Exporter initialized: {os.getenv('LANGFUSE_HOST', 'cloud')}")
        except Exception as e:
            self._enabled = False
            print(f"Failed to initialize Langfuse: {e}")

        return self

    def is_enabled(self) -> bool:
        return self._enabled and self._client is not None

    def export_scenario(self, scenario: TestScenario) -> Optional[str]:
        """Export a single test scenario as a Langfuse trace with generation."""
        if not self.is_enabled():
            return None

        try:
            from langfuse import propagate_attributes

            session_id = f"test-scenarios-{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}"

            with propagate_attributes(
                session_id=session_id,
                trace_name=scenario.name,
                tags=scenario.tags,
            ):
                with self._client.start_as_current_observation(
                    as_type="span",
                    name=f"test-scenario-{scenario.id}",
                    input=scenario.prompt,
                    metadata={
                        "scenario_id": scenario.id,
                        "scenario_name": scenario.name,
                        "category": scenario.category,
                        "ground_truth": scenario.ground_truth,
                        "expected_relevance": scenario.expected_relevance,
                        "expected_coherence": scenario.expected_coherence,
                        "why_low": scenario.why_low,
                        "source": "test-scenarios",
                    },
                ) as trace_span:
                    with self._client.start_as_current_observation(
                        as_type="generation",
                        name="chat-completion",
                        model=scenario.model,
                        input=[{"role": "user", "content": scenario.prompt}],
                        metadata={
                            "scenario_id": scenario.id,
                            "category": scenario.category,
                        },
                    ) as generation:
                        generation.update(
                            output=scenario.response,
                            usage={
                                "input": len(scenario.prompt.split()),
                                "output": len(scenario.response.split()),
                            },
                        )

                    trace_span.update(output=scenario.response)
                    trace_id = trace_span.trace_id if hasattr(trace_span, 'trace_id') else str(scenario.id)

            return trace_id

        except Exception as e:
            print(f"Failed to export scenario {scenario.id} to Langfuse: {e}")
            return None

    def export_scenarios(self, scenarios: List[TestScenario]) -> List[str]:
        """Export multiple scenarios to Langfuse."""
        trace_ids = []
        for scenario in scenarios:
            trace_id = self.export_scenario(scenario)
            if trace_id:
                trace_ids.append(trace_id)
                print(f"  [{scenario.id}] {scenario.name}")
        return trace_ids

    def flush(self):
        """Flush pending events."""
        if self._client and hasattr(self._client, 'flush'):
            self._client.flush()

    def shutdown(self):
        """Shutdown the client."""
        self.flush()


def list_scenarios():
    """Print all available test scenarios."""
    print("\n" + "=" * 70)
    print("AVAILABLE TEST SCENARIOS")
    print("=" * 70)

    for s in SCENARIOS:
        print(f"\n[{s.id}] {s.name}")
        print(f"    Category: {s.category}")
        print(f"    Expected Relevance: {s.expected_relevance}")
        print(f"    Expected Coherence: {s.expected_coherence}")
        print(f"    Description: {s.description}")
        print(f"    Why: {s.why_low}")

    print("\n" + "=" * 70)


def main():
    parser = argparse.ArgumentParser(
        description="Export test scenarios for LLM evaluation demonstration",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python export_test_scenarios.py                    # Export all scenarios
  python export_test_scenarios.py --scenario 1      # Export scenario 1 only
  python export_test_scenarios.py --scenario 1 2 3  # Export specific scenarios
  python export_test_scenarios.py --list            # List all scenarios
        """
    )

    parser.add_argument(
        "--scenario", "-s",
        type=int,
        nargs="+",
        help="Specific scenario ID(s) to export"
    )
    parser.add_argument(
        "--list", "-l",
        action="store_true",
        help="List available scenarios"
    )

    args = parser.parse_args()

    if args.list:
        list_scenarios()
        return

    # Select scenarios to export
    if args.scenario:
        scenarios_to_export = [s for s in SCENARIOS if s.id in args.scenario]
        if not scenarios_to_export:
            print(f"Error: No scenarios found with IDs: {args.scenario}")
            print("Use --list to see available scenarios")
            sys.exit(1)
    else:
        scenarios_to_export = SCENARIOS

    print("\n" + "=" * 70)
    print("TEST SCENARIOS EXPORTER")
    print("=" * 70)
    print(f"Exporting {len(scenarios_to_export)} scenario(s) to Langfuse...")
    print()

    exporter = LangfuseExporter()

    try:
        exporter.setup()

        if not exporter.is_enabled():
            print("\nLangfuse is not configured. Set LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY.")
            sys.exit(1)

        print("\nExporting scenarios:")
        trace_ids = exporter.export_scenarios(scenarios_to_export)
        exporter.flush()

        print("\n" + "-" * 70)
        print(f"Exported {len(trace_ids)} scenarios to Langfuse")
        print("-" * 70)

        print("\nNext steps:")
        print("  1. View traces in Langfuse: http://localhost:3001")
        print("     Filter by tag: test-scenario")
        print()
        print("  2. Configure LLM-as-a-Judge evaluators:")
        print("     Langfuse UI -> Evaluations -> LLM-as-a-Judge")
        print()

        print("Expected results:")
        for s in scenarios_to_export:
            print(f"  [{s.id}] {s.name}:")
            print(f"      Relevance: {s.expected_relevance}, Coherence: {s.expected_coherence}")

    finally:
        exporter.shutdown()


if __name__ == "__main__":
    main()
