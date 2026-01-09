"""Sample documents for RAG demo - ClickHouse & Observability knowledge base."""

DOCUMENTS = [
    {
        "title": "What is ClickHouse?",
        "content": """ClickHouse is an open-source column-oriented database management system
        designed for online analytical processing (OLAP). It was created by Yandex and is
        known for its exceptional query performance on large datasets. ClickHouse can process
        billions of rows per second and is commonly used for real-time analytics, log analysis,
        and business intelligence applications. Key features include columnar storage,
        data compression, vectorized query execution, and horizontal scalability."""
    },
    {
        "title": "ClickHouse vs Traditional Databases",
        "content": """Unlike row-oriented databases like PostgreSQL or MySQL, ClickHouse stores
        data in columns. This makes it extremely efficient for analytical queries that only
        need a subset of columns. For example, calculating the sum of a single column across
        millions of rows is much faster in ClickHouse because it only reads that specific
        column from disk. However, ClickHouse is not optimized for frequent updates or
        transactional workloads - it's designed for append-only data patterns."""
    },
    {
        "title": "OpenTelemetry Overview",
        "content": """OpenTelemetry (OTel) is an open-source observability framework for
        generating, collecting, and exporting telemetry data (traces, metrics, and logs).
        It provides vendor-neutral APIs and SDKs for instrumenting applications. Key components
        include: Traces (distributed request tracking), Metrics (numerical measurements),
        Logs (timestamped records), and the OpenTelemetry Collector (agent for processing
        and exporting data). OTel has become the industry standard for cloud-native observability."""
    },
    {
        "title": "What is OpenLLMetry?",
        "content": """OpenLLMetry is an extension of OpenTelemetry specifically designed for
        Large Language Model (LLM) applications. It automatically instruments LLM frameworks
        like LangChain, OpenAI, and Anthropic to capture: prompt content, completion responses,
        token usage (input/output/total), latency per operation, model information, and cost
        estimation. OpenLLMetry helps teams understand LLM behavior, optimize costs, and
        debug issues in production AI applications."""
    },
    {
        "title": "TruLens for LLM Evaluation",
        "content": """TruLens is an open-source framework for evaluating and tracking LLM
        applications. It provides feedback functions that score LLM outputs on dimensions like:
        Answer Relevance (does the answer address the question?), Groundedness (is the answer
        supported by the context?), Coherence (is the response well-structured?), and
        Harmfulness (does the response contain harmful content?). TruLens uses an
        LLM-as-a-judge approach where a separate model evaluates the outputs."""
    },
    {
        "title": "RAG Architecture",
        "content": """Retrieval-Augmented Generation (RAG) combines information retrieval with
        text generation. The architecture has three main stages: 1) Indexing - documents are
        chunked, embedded into vectors, and stored in a vector database. 2) Retrieval - when
        a query arrives, it's embedded and similar document chunks are retrieved via vector
        similarity search. 3) Generation - the retrieved context is combined with the query
        and sent to an LLM to generate the final answer. RAG reduces hallucinations by
        grounding responses in retrieved facts."""
    },
    {
        "title": "Vector Embeddings",
        "content": """Vector embeddings are numerical representations of text that capture
        semantic meaning. Similar texts have similar embeddings (close in vector space).
        Common embedding models include OpenAI's text-embedding-ada-002, sentence-transformers,
        and Cohere's embed models. Embeddings typically have 384-1536 dimensions. They enable
        semantic search where queries find conceptually similar documents even without
        exact keyword matches. This is the foundation of modern RAG systems."""
    },
    {
        "title": "Vector Databases",
        "content": """Vector databases are specialized storage systems optimized for
        high-dimensional vector similarity search. Popular options include: ChromaDB (simple,
        embedded), Pinecone (managed cloud), Weaviate (open-source, feature-rich), Milvus
        (scalable, distributed), and pgvector (PostgreSQL extension). They use algorithms
        like HNSW (Hierarchical Navigable Small World) for fast approximate nearest neighbor
        search. ClickHouse also supports vector search with its vector similarity functions."""
    },
    {
        "title": "LLM Observability Best Practices",
        "content": """Key practices for LLM observability: 1) Track token usage and costs per
        request. 2) Measure latency at each pipeline stage. 3) Log prompts and completions
        for debugging (with PII redaction). 4) Implement quality evaluations (relevance,
        groundedness). 5) Monitor error rates and failure modes. 6) Set up alerts for cost
        spikes or quality degradation. 7) Use distributed tracing for multi-step chains.
        8) Store telemetry in a queryable backend like ClickHouse for analytics."""
    },
    {
        "title": "ClickHouse for Observability Data",
        "content": """ClickHouse is excellent for storing observability data due to its:
        High ingestion rate (millions of events/second), efficient compression (10-20x),
        fast analytical queries on large datasets, SQL interface for familiar querying,
        and support for time-series patterns. HyperDX/ClickStack uses ClickHouse as its
        backend for storing OpenTelemetry traces, logs, and metrics. Common queries include
        percentile latencies, error rate trends, and token usage aggregations."""
    },
]


def get_documents():
    """Return documents as list of strings for indexing."""
    return [f"{doc['title']}\n\n{doc['content']}" for doc in DOCUMENTS]


def get_document_metadata():
    """Return metadata for each document."""
    return [{"title": doc["title"]} for doc in DOCUMENTS]
