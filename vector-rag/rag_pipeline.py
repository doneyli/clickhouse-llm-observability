"""RAG Pipeline with Vector Search using ChromaDB."""

import os
from typing import Optional, List
from dataclasses import dataclass

from langchain_anthropic import ChatAnthropic
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_chroma import Chroma
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langchain_text_splitters import RecursiveCharacterTextSplitter

from documents import get_documents, get_document_metadata


@dataclass
class RAGConfig:
    model_name: str = "claude-sonnet-4-6"
    embedding_model: str = "all-MiniLM-L6-v2"
    temperature: float = 0.7
    max_tokens: int = 1500
    chunk_size: int = 500
    chunk_overlap: int = 50
    top_k: int = 3


class VectorRAGPipeline:
    """RAG pipeline with proper vector retrieval."""

    def __init__(self, config: Optional[RAGConfig] = None):
        self.config = config or RAGConfig(
            model_name=os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6"),
            temperature=float(os.getenv("TEMPERATURE", "0.7")),
        )
        self._context = ""
        self._setup_embeddings()
        self._setup_vectorstore()
        self._setup_llm()
        self._setup_chains()

    def _setup_embeddings(self):
        """Initialize embedding model."""
        print(f"Loading embedding model: {self.config.embedding_model}")
        self.embeddings = HuggingFaceEmbeddings(
            model_name=self.config.embedding_model,
            model_kwargs={'device': 'cpu'},
            encode_kwargs={'normalize_embeddings': True}
        )

    def _setup_vectorstore(self):
        """Initialize ChromaDB and index documents."""
        print("Indexing documents into ChromaDB...")

        # Get and chunk documents
        documents = get_documents()
        metadata = get_document_metadata()

        text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=self.config.chunk_size,
            chunk_overlap=self.config.chunk_overlap,
        )

        # Split documents into chunks
        chunks = []
        chunk_metadata = []
        for doc, meta in zip(documents, metadata):
            doc_chunks = text_splitter.split_text(doc)
            chunks.extend(doc_chunks)
            chunk_metadata.extend([meta] * len(doc_chunks))

        # Create vector store
        self.vectorstore = Chroma.from_texts(
            texts=chunks,
            embedding=self.embeddings,
            metadatas=chunk_metadata,
            collection_name="rag_demo"
        )
        self.retriever = self.vectorstore.as_retriever(
            search_kwargs={"k": self.config.top_k}
        )
        print(f"Indexed {len(chunks)} chunks from {len(documents)} documents")

    def _setup_llm(self):
        """Initialize LLM."""
        self.llm = ChatAnthropic(
            model=self.config.model_name,
            temperature=self.config.temperature,
            max_tokens=self.config.max_tokens,
        )

    def _setup_chains(self):
        """Setup generation chain."""
        self.response_prompt = ChatPromptTemplate.from_template(
            """Answer the question based on the provided context.

Context:
{context}

Question: {question}

Instructions:
- Use only information from the context to answer
- If the context doesn't contain relevant information, say so
- Be concise and accurate

Answer:"""
        )

        self.response_chain = (
            self.response_prompt | self.llm | StrOutputParser()
        ).with_config({"metadata": {"purpose": "rag_generation"}})

    def retrieve(self, question: str) -> str:
        """Retrieve relevant documents via vector similarity search."""
        docs = self.retriever.invoke(question)
        self._context = "\n\n---\n\n".join([doc.page_content for doc in docs])
        return self._context

    def generate(self, question: str, context: str, callbacks: list = None) -> str:
        """Generate response from context.

        Args:
            question: The user's question
            context: Retrieved context
            callbacks: Optional list of LangChain callbacks (e.g., Langfuse handler)
        """
        config = {"callbacks": callbacks} if callbacks else {}
        return self.response_chain.invoke({
            "question": question,
            "context": context
        }, config=config)

    def query(self, question: str, callbacks: list = None) -> str:
        """Execute the full RAG pipeline.

        Args:
            question: The user's question
            callbacks: Optional list of LangChain callbacks (e.g., Langfuse handler)
        """
        context = self.retrieve(question)
        answer = self.generate(question, context, callbacks)
        return answer

    @property
    def context(self) -> str:
        """Expose context for groundedness evaluation."""
        return self._context


def create_pipeline(config: Optional[RAGConfig] = None) -> VectorRAGPipeline:
    return VectorRAGPipeline(config)
