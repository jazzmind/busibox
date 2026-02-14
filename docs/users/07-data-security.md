---
title: "Data & Security"
category: "platform"
order: 7
description: "How Busibox protects your data and privacy"
published: true
---

# Data & Security

Busibox is designed so your data stays private and under your control. Here's what that means in practice.

## Your Data Stays on Your Infrastructure

Everything runs on your organization's infrastructure. Documents, conversations, search indexes, and embeddings are stored on your servers. Nothing is sent to external services unless your administrator explicitly configures cloud AI (e.g., for certain models). In that case, your admin controls what data, if any, is sent and to whom.

## Authentication

You sign in without passwords. Busibox supports:

- **Passkeys** — Use your device's biometrics or security key. Fast and secure.
- **TOTP** — One-time codes from an authenticator app. Good for multi-device use.
- **Magic links** — A sign-in link sent to your email. Simple and passwordless.

Your admin chooses which methods are available. All of them avoid the risks of reused or weak passwords.

## Personal vs Shared Documents

You control who sees your documents:

- **Personal** — Only you can search and view them. Use for drafts, notes, or sensitive content.
- **Shared with roles** — Visible to users with specific roles. You choose the role when you upload.

Agents respect these settings. If a document is personal, only you can use it in chat. If it's shared with "Project Team," only users in that role can search it.

## How Permissions Work

Access is controlled by roles. Your admin assigns you roles (e.g., "User," "Admin," "Project Team"). Each role has permissions:

- Which apps you can open
- Which documents you can see (your own, shared with your roles, or org-wide)
- Whether you can manage agents, users, or settings

Agents use your permissions. When you chat, the agent can only search documents you're allowed to see. It can't access data outside your scope.

## AI Agents Can Only See What You Can See

This is important: agents don't have their own access. They act on your behalf. If you can't see a document, the agent can't search it. If a document is shared with "Finance" and you're not in that role, the agent won't use it in your conversations.

## Encryption

Data is protected in two ways:

- **At rest** — Stored data is encrypted on disk. Even if someone gains access to the storage, they can't read it without the keys.
- **In transit** — When data moves between your browser and the servers, it uses HTTPS. No one on the network can read it in transit.

Your admin manages the encryption setup. You benefit from it automatically.

## Audit Trail

Actions are logged. Who uploaded what, who searched for what, when — these events are recorded. Admins can use this for compliance, troubleshooting, and security reviews. You don't need to do anything; logging happens in the background.

## What This Means in Practice

Simple scenarios:

- **You upload a personal document.** Only you can search it. Agents use it only when you're chatting.
- **You upload a document shared with "Project Team."** You and anyone in that role can search it. Agents use it for those users.
- **You're not in the "Finance" role.** You can't see finance documents. Agents can't search them for you either.
- **You chat with an agent.** The agent searches only documents you're allowed to see. Its answers are grounded in your authorized content.

Security is built in. You use the platform normally; the system enforces these rules automatically.
