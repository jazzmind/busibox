---
title: "Troubleshooting"
category: "platform"
order: 8
description: "Common issues and how to resolve them"
published: true
---

# Troubleshooting

Here are common issues you might run into and what to try. If something isn't covered, contact your administrator.

## Can't Log In

**What to try:**

- Use a different sign-in method if more than one is available (e.g., magic link instead of passkey).
- Check that you're using the correct URL. Your admin can confirm the AI Portal address.
- Clear your browser cache and cookies, then try again.
- If you use a magic link, make sure it hasn't expired. Request a new one if needed.

**If it still fails:** Your admin may need to check your account, roles, or authentication settings. Contact them for help.

## Documents Not Processing

**What to check:**

- **Status** — Look at the document's processing status. It may still be queued, parsing, or embedding. Large files can take several minutes.
- **Format** — Ensure the file format is supported (PDF, Word, Excel, PowerPoint, images, HTML, Markdown, text). Unusual or corrupted files may fail.
- **File size** — Very large files can timeout or take a long time. Your admin may have limits.

**What to try:** Wait a bit longer. If it stays stuck or shows an error, try re-uploading. If it keeps failing, contact your admin — they can check logs and processing settings.

## Search Not Finding Documents

**What to check:**

- **Processing** — The document must be fully processed (status: completed) before it's searchable.
- **Visibility** — If you shared the document with a role, make sure you're in that role. Personal documents are only searchable by you.
- **Query** — Try rephrasing. Semantic search understands meaning, but very vague or unrelated queries may not match.

**What to try:** Confirm the document is completed, check visibility settings, and try a more specific question. If you're sure it should match, contact your admin.

## Agent Giving Wrong Answers

**What to try:**

- **Be more specific** — Narrow your question. "What are the budget assumptions in the Q3 forecast?" is better than "Tell me about the budget."
- **Enable document search** — If you're asking about your documents, ensure document search is on. Otherwise the agent may answer from general knowledge.
- **Check your documents** — Make sure the relevant content is uploaded and fully processed. The agent can only use documents you have access to.
- **Rephrase** — Sometimes a different wording gets better results.

Agents are powerful but not perfect. Good questions and good documents lead to better answers.

## App Not Loading

**What to check:**

- **Permissions** — Your admin controls which apps you can access. If you don't see an app or get an access error, you may not have permission.
- **Browser** — Try a different browser or clear cache. Some apps work best in modern browsers (Chrome, Firefox, Safari, Edge).
- **URL** — Make sure you're opening the app from the AI Portal. Direct links may not work if they bypass authentication.

**What to try:** Refresh the page. If it still fails, contact your admin to verify your access and the app's status.

## Slow Responses

**Possible causes:**

- **Local models** — If your org uses local AI models, response speed depends on your hardware. Complex questions take longer.
- **Network** — Slow or unstable connections can delay loading and streaming.
- **Load** — If many people are using the platform, responses may slow down.

**What to try:** Wait a bit — streaming responses can feel slow at first but then speed up. If it's consistently slow, your admin can check model configuration and server load.

## Getting Help

When you need support:

1. **Try the steps above** for the issue you're seeing.
2. **Note what you did** — What you clicked, what you searched, any error messages.
3. **Contact your administrator** — They can check logs, permissions, and configuration. They're the best resource for platform-specific issues.

Your admin has access to logs and settings that can diagnose most problems. Don't hesitate to reach out.
