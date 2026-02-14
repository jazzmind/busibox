---
title: "Documents"
category: "platform"
order: 4
description: "Uploading, processing, and searching your documents"
published: true
---

# Documents

Documents are the foundation of Busibox. You upload them, the system processes them, and then you can search and chat with them. Here's how it works from your perspective.

## Uploading Documents

You can upload a wide range of formats:

- **Office files** — Word (.docx), Excel (.xlsx), PowerPoint (.pptx)
- **PDFs** — Standard and scanned (with OCR when needed)
- **Images** — JPEG, PNG, and other common formats (text is extracted when possible)
- **Web content** — HTML and Markdown
- **Plain text** — .txt files

Go to the Documents section in the AI Portal, click upload, and select your files. You can upload multiple at once. Large files may take longer to process.

## What Happens After Upload

When you upload a document, it goes through a processing pipeline:

1. **Extract** — The system pulls text (and structure) from the file. For images and complex PDFs, AI may be used to improve extraction.
2. **Chunk** — The text is split into smaller pieces (chunks) that are easier to search and cite.
3. **Embed** — Each chunk is turned into an embedding — a representation of its meaning — so the system can find it by semantic similarity.
4. **Index** — The chunks and embeddings are stored in a search index. Once this is done, the document is searchable.

You don't need to do any of this manually. It happens automatically in the background.

## Processing Status

Each document goes through several stages. You can see the status in the Documents view:

- **Queued** — Waiting to be processed
- **Parsing** — Extracting text from the file
- **Chunking** — Splitting into searchable pieces
- **Embedding** — Creating semantic representations
- **Indexing** — Adding to the search index
- **Completed** — Ready to search and use in chat

If a document gets stuck or fails, check that the format is supported and the file isn't corrupted. Your admin can help with processing issues.

## Searching Documents

Once processing is complete, you can search in two ways:

- **Direct search** — Use the search bar in the Documents section. Type a question or keywords. The system uses both keyword matching and semantic similarity to find relevant passages.
- **Chat search** — When you chat with an agent and enable document search, the agent searches your documents automatically and uses them to answer your questions.

Search is natural language. Ask "What are the budget assumptions?" or "Summarize the executive summary" — you don't need exact phrases from the document.

## Document Visibility

Documents can be:

- **Personal** — Only you can see and search them. Use this for drafts, notes, or sensitive content.
- **Shared with roles** — Visible to users with specific roles (e.g., "Project Team" or "Admins"). Your admin defines these roles.

When you upload, you choose the visibility. Agents respect this: they can only search documents you're allowed to see.

## Chat with Your Documents

The most powerful way to use documents is through agents. When you enable document search in a chat:

1. You ask a question.
2. The agent searches your authorized documents.
3. It finds relevant passages.
4. It synthesizes an answer and cites the sources.

You get answers grounded in your actual content, not generic responses. The citations let you verify and dig deeper.

## Tips for Best Results

- **Clear documents** — Well-structured PDFs and Office files process more reliably than heavily scanned or handwritten content.
- **Good file names** — Descriptive names help you find documents later, even though search is semantic.
- **Appropriate visibility** — Share only with roles that need access. Personal documents stay private by default.
- **Wait for processing** — Large or complex documents can take a few minutes. Check the status before searching.
